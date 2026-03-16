# PRD: Extrato Obrigatorio no Onboarding/Upgrade Conta Azul

**Status:** Draft
**Criado:** 2026-03-16
**Autor:** Eryk + Claude

---

## 1. Introducao / Overview

O extrato "Dinheiro em conta" do Mercado Pago e a unica fonte que garante 100% de cobertura financeira. Ele contem movimentacoes invisiveis tanto para a Payments API quanto para o Release Report: DIFAL, faturas ML, disputas, dinheiro retido, debitos retroativos, e payments que a API do ML silenciosamente dropa.

Esse CSV **nao esta disponivel via API** — so pode ser baixado manualmente no painel do MP. Sem ele, o backfill de um seller novo fica com gaps permanentes no event ledger.

Esta feature torna o upload do extrato CSV parte do fluxo de ativacao de sellers para Conta Azul (`integration_mode=dashboard_ca`), garantindo que desde o dia 1 o ledger tenha cobertura completa. Caso o admin nao tenha o extrato disponivel, pode marcar explicitamente "sem extrato" e prosseguir — mas o seller fica com flag `extrato_missing=true`.

---

## 2. Goals

- Garantir 100% de cobertura do event ledger desde `ca_start_date` para sellers novos
- Capturar movimentacoes invisiveis a Payments API e Release Report (DIFAL, faturas, disputas, ML API gaps)
- Permitir ativacao sem extrato quando necessario, com rastreabilidade
- Armazenar CSV original no Google Drive como evidencia auditavel
- Permitir upload tardio de extrato para sellers ja ativados sem extrato

---

## 3. User Stories

### US-001: Upload de extrato na ativacao

**Descricao:** Como admin, quero fazer upload do extrato CSV do MP ao ativar um seller para CA, para que o backfill tenha cobertura completa desde o inicio.

**Acceptance Criteria:**
- [ ] Tela de ativacao exibe campo de upload de CSV (um ou mais arquivos)
- [ ] Sistema aceita multiplos CSVs (ex: jan + fev + mar) e valida que juntos cobrem `ca_start_date` ate ontem
- [ ] Upload dispara validacao de formato (`INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE` + `RELEASE_DATE;TRANSACTION_TYPE;...`)
- [ ] CSV invalido mostra mensagem de erro clara sem iniciar backfill
- [ ] CSV valido e processado pelo extrato ingester antes do backfill iniciar
- [ ] Backfill so inicia apos extrato ingerido com sucesso (ou flag "sem extrato" marcada)

### US-002: Ativacao sem extrato

**Descricao:** Como admin, quero poder ativar um seller sem extrato quando nao tenho o CSV disponivel, aceitando que havera gaps.

**Acceptance Criteria:**
- [ ] Tela de ativacao tem opcao "Ativar sem extrato" (checkbox ou botao alternativo)
- [ ] Ao marcar, sistema exige confirmacao explicita ("Backfill tera gaps permanentes ate upload do extrato")
- [ ] Seller e ativado com campo `extrato_missing=true` na tabela `sellers`
- [ ] Backfill roda normalmente (Payments API + Release Report + Baixas) sem extrato ingestion
- [ ] Flag `extrato_missing` fica visivel no painel admin para o seller

### US-003: Validacao de cobertura de datas

**Descricao:** Como sistema, devo validar que o(s) extrato(s) enviado(s) cobrem 100% do periodo `ca_start_date` ate ontem.

**Acceptance Criteria:**
- [ ] Parser extrai as datas de todas as linhas de transacao de cada CSV
- [ ] Sistema calcula range coberto: `min(RELEASE_DATE)` ate `max(RELEASE_DATE)` de todos os CSVs combinados
- [ ] Se range coberto nao inclui `ca_start_date` → rejeitar com mensagem "Extrato comeca em {data}, mas ca_start_date e {data}"
- [ ] Se range coberto nao inclui ontem → rejeitar com mensagem "Extrato termina em {data}, mas precisa ir ate {ontem}"
- [ ] Gaps internos entre CSVs sao detectados (ex: jan + mar sem fev) → rejeitar com mensagem "Gap de {data_inicio} a {data_fim}"
- [ ] CSVs com datas sobrepostas sao aceitos (dedup por reference_id e idempotente)

### US-004: Armazenamento do CSV no Google Drive

**Descricao:** Como sistema, devo armazenar o CSV original no Google Drive como evidencia auditavel.

**Acceptance Criteria:**
- [ ] CSV e salvo em `ROOT/EXTRATOS/{SELLER_NAME}/{YYYY-MM}.csv`
- [ ] Multiplos CSVs sao salvos individualmente (um arquivo por mes)
- [ ] Se seller nao tem Google Drive configurado, upload e skipped (sem bloquear ativacao)
- [ ] Link do GDrive e registrado na tabela `sellers` ou tabela auxiliar
- [ ] Upload roda em background, sem bloquear o fluxo de ativacao

### US-005: Upload tardio de extrato

**Descricao:** Como admin, quero poder enviar o extrato de um seller ja ativado sem extrato, para fechar os gaps retroativamente.

**Acceptance Criteria:**
- [ ] Endpoint dedicado: `POST /admin/sellers/{slug}/extrato/upload`
- [ ] Aceita multiplos CSVs, mesma validacao de formato e cobertura de datas
- [ ] Roda `ingest_extrato_from_csv()` sobre cada CSV (sem re-backfill)
- [ ] Atualiza `extrato_missing=false` apos processamento com sucesso
- [ ] CSV e salvo no Google Drive (mesma estrutura da US-004)
- [ ] Retorna stats: `newly_ingested`, `already_covered`, `errors`, `by_type`

### US-007: Aba de extratos no admin (cards por seller)

**Descricao:** Como admin, quero ver na aba de extratos um grid de cards por seller (igual a aba de despesas), mostrando quais periodos de extrato estao faltando para cada um.

**Acceptance Criteria:**
- [ ] Aba "Extratos" no AdminPanel com layout de cards por seller (mesma estrutura visual da aba Despesas)
- [ ] Cada card mostra: nome do seller, `ca_start_date`, status do extrato (`completo` / `parcial` / `faltante`)
- [ ] Card indica quais meses estao cobertos e quais estao faltando (ex: "Jan OK, Fev OK, Mar FALTANDO")
- [ ] Card com `extrato_missing=true` tem badge visual destacado (ex: vermelho/amarelo)
- [ ] Botao de upload de extrato por seller card (aceita multiplos CSVs)
- [ ] Apos upload com sucesso, card atualiza automaticamente mostrando novos periodos cobertos
- [ ] Sellers sem `integration_mode=dashboard_ca` nao aparecem na aba

### US-006: Integracao com onboarding backfill

**Descricao:** Como sistema, o backfill deve incluir extrato ingestion como step antes das baixas.

**Acceptance Criteria:**
- [ ] `onboarding_backfill.py` recebe parametro opcional `extrato_csvs: list[str]`
- [ ] Se CSVs fornecidos: roda `ingest_extrato_from_csv()` para cada um apos Release Report e antes de Baixas
- [ ] Se nao fornecidos (ativacao sem extrato): skip step com log "extrato ingestion skipped (extrato_missing=true)"
- [ ] Progress dict inclui `extrato_ingested: int` e `extrato_errors: int`
- [ ] Falha no extrato ingester nao aborta o backfill (non-fatal, como release report)

---

## 4. Functional Requirements

**FR-1:** O sistema deve aceitar upload de um ou mais arquivos CSV no formato "Dinheiro em conta" do MP (`;` como separador, seções `INITIAL_BALANCE` e `RELEASE_DATE`).

**FR-2:** O sistema deve validar que cada CSV tem o formato correto antes de processar. CSVs sem header `INITIAL_BALANCE` ou `RELEASE_DATE` devem ser rejeitados com erro descritivo.

**FR-3:** O sistema deve validar que a uniao dos CSVs cobre o periodo completo `ca_start_date` ate `ontem` (D-1), sem gaps internos maiores que 1 dia util.

**FR-4:** O sistema deve processar os CSVs via `ingest_extrato_from_csv()` (parser existente `_parse_account_statement`), gravando eventos no event ledger.

**FR-5:** O sistema deve permitir ativacao sem extrato mediante confirmacao explicita, setando `extrato_missing=true` na tabela `sellers`.

**FR-6:** O sistema deve armazenar CSVs originais no Google Drive em `ROOT/EXTRATOS/{SELLER_NAME}/{YYYY-MM}.csv`, em background.

**FR-7:** O sistema deve expor endpoint para upload tardio de extrato que roda ingestion sem re-backfill e atualiza `extrato_missing=false`.

**FR-8:** O `onboarding_backfill.py` deve incluir step de extrato ingestion (entre release report e baixas) quando CSVs foram fornecidos.

**FR-9:** A aba "Extratos" no admin deve exibir grid de cards por seller (igual aba Despesas), mostrando periodos cobertos vs faltantes, com upload direto por card.

**FR-11:** Aceitar apenas arquivos `.csv` com a estrutura determinada (`INITIAL_BALANCE`/`RELEASE_DATE`). Rejeitar qualquer outro formato sem processar.

**FR-10:** O fluxo de upgrade (`dashboard_only` → `dashboard_ca`) deve seguir as mesmas regras de exigencia de extrato.

---

## 5. Non-Goals (Out of Scope)

- **Nao** tentaremos baixar o extrato automaticamente via API (confirmado que nao existe endpoint)
- **Nao** bloquearemos o backfill por falta de extrato — o admin pode optar por prosseguir sem
- **Nao** faremos re-backfill no upload tardio — o ingester roda sobre o CSV e complementa o ledger
- **Nao** criaremos painel self-service para sellers fazerem upload (apenas admin)
- **Nao** modificaremos o nightly pipeline (extrato ingester ja foi desativado — continua desativado)
- **Nao** validaremos se o extrato pertence ao seller correto (confiamos no admin)

---

## 6. Technical Considerations

### Infraestrutura existente que sera reutilizada
- `_parse_account_statement()` em `extrato_ingester.py` — parser do formato CSV ja funcional
- `ingest_extrato_from_csv()` em `extrato_ingester.py` — ingestion ja funcional
- `gdrive_client.py` — upload para Google Drive ja funcional
- `onboarding_backfill.py` — orquestracao do backfill ja funcional

### Mudancas necessarias na tabela `sellers`
- Novo campo: `extrato_missing: boolean default false`
- Novo campo: `extrato_uploaded_at: timestamptz null`
- Novo campo: `extrato_gdrive_links: jsonb null` (array de links por mes)

### Validacao de cobertura de datas
- Extrair `min(date)` e `max(date)` de cada CSV apos parse
- Unir ranges de todos CSVs e verificar continuidade
- Tolerancia: gaps de ate 1 dia sao aceitaveis (fins de semana sem movimentacao)
- Rejeitar se `min(date) > ca_start_date` ou `max(date) < ontem`

### Formato esperado do CSV
```
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
1.090,40;125.752,95;-123.027,63;3.815,72

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-02-2026;Transferencia Pix enviada LILLIAN DA ROCHA;144355753052;-350,00;740,40
02-02-2026;Dinheiro retido Reclamacoes e devolucoes;142181590693;-230,87;509,53
```

### Fluxo revisado do onboarding backfill
```
1. Fetch payments (ML API)
2. Process orders + non-orders
3. Backfill release report
4. [NOVO] Ingest extrato CSVs (se fornecidos)
5. Trigger baixas
6. Mark completed
```

### Endpoint de upload tardio
```
POST /admin/sellers/{slug}/extrato/upload
Content-Type: multipart/form-data
Files: extrato_files[] (um ou mais CSVs)
Response: { stats: {...}, gdrive_links: [...] }
```

---

## 7. Success Metrics

- 100% dos sellers ativados com extrato tem zero gaps no coverage checker
- Sellers ativados sem extrato sao rastreados via `extrato_missing=true`
- Upload tardio reduz `extrato_missing` count ao longo do tempo
- Zero regressoes no fluxo de backfill existente (Payments API + Release Report + Baixas)

---

## 8. Open Questions

Todas as questoes foram resolvidas:

- **Q1 (resolvido):** Sem notificacao periodica. A aba de extratos no admin mostra todos os sellers em cards (igual aba de despesas), indicando quais periodos de extrato estao faltando.
- **Q2 (resolvido):** Sem limite de tamanho. Validacao e apenas por formato: deve ser CSV com estrutura `INITIAL_BALANCE`/`RELEASE_DATE` valida.
- **Q3 (resolvido):** Sem limite de periodo. Cobertura total exigida independente de quanto tempo atras e o `ca_start_date`.
