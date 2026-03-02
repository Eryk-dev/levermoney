# PRD: Export de Despesas com Backup Google Drive (Revisado)

## 1. Introduction/Overview

O sistema Lever Money ja processa e classifica pagamentos sem order na tabela `mp_expenses`. Hoje, o export e operacionalmente fraco: depende de chamada manual e nao garante rastreabilidade de backup por lote.

Esta feature cria uma aba **Despesas** no AdminPanel para selecionar seller e periodo, visualizar resumo, exportar em 1 clique e ter backup em Google Drive com rastreabilidade por `batch_id`.

**Problema:** processo manual, sujeito a atraso e erro operacional (download/local, upload manual no Drive e baixa rastreabilidade).

**Solucao revisada:**
- Download do ZIP continua imediato no navegador (sem bloquear UX).
- Backup no Drive roda em segundo plano (best-effort, nao bloqueante).
- Historico mostra status do backup por lote.
- Re-download passa a ser deterministico por `batch_id`.

---

## 2. Goals

- Permitir export de despesas (`mp_expenses`) por seller e periodo via UI.
- Garantir download local imediato do ZIP.
- Executar backup no Google Drive sem bloquear a resposta de export.
- Exibir status do backup no historico por `batch_id`.
- Permitir re-download fiel por lote (nao por periodo generico).
- Manter fluxo simples para operacao financeira mensal.

---

## 3. User Stories

### US-001: Acessar aba Despesas no AdminPanel
**Description:** Como admin, quero alternar entre Sellers e Despesas sem perder o comportamento atual da area administrativa.

**Acceptance Criteria:**
- [ ] AdminPanel exibe tab bar com "Sellers" e "Despesas"
- [ ] Estado default e "Sellers"
- [ ] O conteudo atual de sellers fica encapsulado em `adminTab === 'sellers'`
- [ ] A aba "Despesas" exibe novo componente dedicado

### US-002: Selecionar seller e periodo
**Description:** Como admin, quero selecionar seller ativo e mes/ano para carregar stats e historico.

**Acceptance Criteria:**
- [ ] Dropdown lista apenas sellers ativos
- [ ] Seletor de periodo com mes e ano (default: mes anterior)
- [ ] Ao alterar seller/periodo, stats e historico sao recarregados

### US-003: Validar risco antes de export irreversivel
**Description:** Como admin, quero ser alertado quando existirem despesas `pending_review` antes de marcar linhas como exportadas.

**Acceptance Criteria:**
- [ ] Se `by_status.pending_review > 0`, UI exibe confirmacao antes do export
- [ ] Confirmacao informa quantidade pendente e impacto (marcacao como exported)
- [ ] Sem confirmacao explicita, export nao e executado

### US-004: Exportar com download imediato
**Description:** Como admin, quero clicar em exportar e receber o arquivo imediatamente, sem esperar upload no Drive.

**Acceptance Criteria:**
- [ ] Botao "Exportar e baixar" inicia download do ZIP no navegador
- [ ] Endpoint continua aceitando `mark_exported=true`
- [ ] `X-Export-Batch-Id` retorna no response
- [ ] UX nao fica bloqueada por upload no Drive

### US-005: Backup GDrive assincrono e rastreavel
**Description:** Como admin, quero que o backup no Drive rode em background e que o status fique visivel no historico do lote.

**Acceptance Criteria:**
- [ ] `gdrive_backup=true` nao bloqueia retorno do ZIP
- [ ] Status inicial do backup: `queued` (ou `skipped_no_drive_root` se nao configurado)
- [ ] Resultado final persistido no lote: `uploaded` ou `failed`
- [ ] Lote pode exibir `gdrive_folder_link` quando upload concluido

### US-006: Re-download deterministico por batch
**Description:** Como admin, quero baixar novamente o mesmo lote exportado, sem ambiguidades de filtro por data.

**Acceptance Criteria:**
- [ ] Novo endpoint de re-download por `batch_id`
- [ ] Re-download usa `expense_batch_items` do lote para reconstruir ZIP
- [ ] Re-download nao altera status das rows
- [ ] Historico traz botao "Baixar" por linha de batch

### US-007: Historico operacional completo
**Description:** Como admin, quero acompanhar cada lote com status de export e de backup.

**Acceptance Criteria:**
- [ ] Tabela exibe Data, Linhas, Valor Total, Status, Batch ID, Backup
- [ ] Backup mostra badge (`queued`, `uploaded`, `failed`, `skipped_no_drive_root`)
- [ ] Quando `uploaded`, exibe link da pasta no Drive
- [ ] Historico recarrega apos novo export

---

## 4. Functional Requirements

**Backend:**
- FR-1: `GET /expenses/{seller_slug}/export` aceita `gdrive_backup` (bool, default false)
- FR-2: Export retorna ZIP imediatamente; backup nao pode bloquear resposta HTTP
- FR-3: Response de export inclui sempre `X-Export-Batch-Id`
- FR-4: Quando `gdrive_backup=true`, response inclui `X-GDrive-Status` inicial:
  - `queued` (backup agendado)
  - `skipped_no_drive_root` (Drive nao configurado)
- FR-5: Upload GDrive usa hierarquia `ROOT/DESPESAS/{EMPRESA}/{YYYY-MM}/`
- FR-6: Resultado final do backup deve ser persistido em `expense_batches`:
  - `gdrive_status`, `gdrive_folder_link`, `gdrive_file_id`, `gdrive_file_link`, `gdrive_error`, `gdrive_updated_at`
- FR-7: Falha no backup nao impede download nem export
- FR-8: CORS deve expor `X-Export-Batch-Id` e `X-GDrive-Status`
- FR-9: Novo endpoint `GET /expenses/{seller_slug}/batches/{batch_id}/download`
- FR-10: Re-download por batch nao pode alterar `mp_expenses.status`

**Frontend:**
- FR-11: Nova aba Despesas em AdminPanel usando tab toggle existente
- FR-12: Hook `useExpenses` centraliza chamadas de stats/export/batches/redownload
- FR-13: Antes do export, se houver `pending_review`, exigir confirmacao via modal
- FR-14: Pos-export exibir `batch_id` e status inicial do backup
- FR-15: Historico exibe status de backup por lote e link de Drive quando disponivel
- FR-16: Re-download deve usar `batch_id`, nao combinacao de `date_from/date_to`
- FR-17: Polling do historico a cada 5s enquanto houver `gdrive_status=queued`, max 60s (12 tentativas)
- FR-18: Stats response inclui `pending_review_count` e `auto_categorized_count` explicitos

---

## 5. Non-Goals (Out of Scope)

- Interface de revisao detalhada de cada `pending_review`.
- Export em massa para varios sellers ao mesmo tempo.
- Configuracao de pasta GDrive custom por seller.
- Notificacoes por email/Slack.
- Persistir o ZIP binario no banco (re-download sera reconstruido por IDs do batch).

---

## 6. Technical Considerations

### Reuso de infraestrutura
- `app/services/legacy/daily_export.py` para cliente e operacoes de Google Drive.
- `app/routers/expenses/export.py` para export e batches.
- `expense_batches` + `expense_batch_items` para rastreabilidade por lote.

### Decisoes tecnicas-chave
- Backup em background para preservar UX responsiva no clique de export.
- Re-download por `batch_id` para evitar inconsistencias de periodo.
- Contrato de status padronizado:
  - `queued`
  - `uploaded`
  - `failed`
  - `skipped_no_drive_root`

### Arquivos impactados
| Arquivo | Acao |
|---------|------|
| `app/services/gdrive_client.py` | NOVO |
| `app/routers/expenses/export.py` | MODIFICAR |
| `app/routers/expenses/_deps.py` | MODIFICAR |
| `app/routers/expenses/crud.py` | MODIFICAR (stats complementares) |
| `app/main.py` | MODIFICAR (CORS expose_headers) |
| `dashboard/src/hooks/useExpenses.ts` | NOVO |
| `dashboard/src/components/ExpensesExportTab.tsx` | NOVO |
| `dashboard/src/components/ExpensesExportTab.module.css` | NOVO |
| `dashboard/src/components/AdminPanel.tsx` | MODIFICAR |

---

## 7. Success Metrics

- Export concluido em ate 1 clique apos selecao de seller/periodo.
- Download inicia em poucos segundos sem aguardar Drive.
- Historico mostra status de backup por lote com rastreabilidade por `batch_id`.
- Re-download de lote retorna conjunto deterministico de rows.
- Reducao de erros operacionais no processo mensal de financeiro.

---

## 8. Decisoes Tomadas

- Backup no Drive continua best-effort, mas agora com status auditavel por lote.
- Fluxo principal prioriza velocidade do operador (download imediato).
- Re-download deixa de ser por filtro de periodo e passa a ser por lote.
- Export que marca `exported` exige confirmacao quando houver pendencias de revisao.
- US-005 original (stats adjustment) absorvido no US-003 do prd.json.
- US-007 original (componente completo) dividido em US-006 (core: seletor+stats+export+modal) e US-007 (historico+polling+redownload).
