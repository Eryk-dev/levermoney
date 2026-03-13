# PRD: Etapa 1 — Upload de Extrato via Admin Panel

## 1. Introduction/Overview

O extrato CSV do Mercado Pago (Account Statement) precisa ser baixado manualmente pelo usuario no painel do MP. Hoje a ingestao e feita por scripts CLI, quebrando o fluxo operacional. A logica de ingestao ja existe em `extrato_ingester.py` — falta apenas expor via API e admin panel.

**Spec tecnica completa:** `rfcs/plano-execucao/01-upload-extrato.md`

## 2. Goals

- Permitir upload do extrato CSV pelo admin panel
- Ingerir lacunas automaticamente em `mp_expenses` (mesma logica existente)
- Manter historico de uploads com status e metricas
- Export ZIP existente funciona sem mudancas

## 3. User Stories

### US-001: Migration da tabela de uploads

**Description:** Como desenvolvedor, preciso da tabela `extrato_uploads` no Supabase para armazenar historico de uploads de extratos.

**Acceptance Criteria:**
- [ ] Arquivo `migrations/007_extrato_uploads.sql` criado com schema conforme spec
- [ ] Tabela contem: id, seller_slug, filename, month_ref, status, total_lines, new_inserted, duplicates_skipped, errors, summary_json, created_at, completed_at
- [ ] Migration aplicada no Supabase via `mcp__supabase__apply_migration`
- [ ] Tabela verificavel via `mcp__supabase__execute_sql`: `SELECT * FROM extrato_uploads LIMIT 1` retorna sem erro

### US-002: Funcao publica de ingestao a partir de CSV text

**Description:** Como sistema, preciso de uma funcao `ingest_extrato_from_csv(seller_slug, csv_text)` em `extrato_ingester.py` que aceite o conteudo CSV diretamente (sem baixar da ML API) e execute a pipeline de ingestao existente.

**Acceptance Criteria:**
- [ ] Funcao `ingest_extrato_from_csv(seller_slug: str, csv_text: str) -> dict` criada em `extrato_ingester.py`
- [ ] Reutiliza `_parse_account_statement()`, `_classify_extrato_line()`, `_resolve_check_payments()` e toda a logica de dedup/upsert existente
- [ ] Retorna dict com: `total_lines`, `new_inserted`, `duplicates_skipped`, `errors`, `summary` (por expense_type)
- [ ] Teste: chamar com extrato janeiro 141Air.csv e verificar que retorna metricas corretas
- [ ] Nao altera comportamento de `ingest_extrato_for_seller()` existente
- [ ] `python3 -m pytest` passa (152+ testes)

### US-003: Endpoints de upload e historico

**Description:** Como admin, quero fazer upload do extrato CSV via API e consultar historico de uploads por seller.

**Acceptance Criteria:**
- [ ] `POST /admin/extrato/upload` criado em `app/routers/admin/extrato.py`
  - Aceita multipart form: `file` (CSV) + `seller_slug` (string)
  - Valida: seller existe, arquivo e CSV valido, parse sem erros fatais
  - Chama `ingest_extrato_from_csv()` e persiste resultado em `extrato_uploads`
  - Retorna: `upload_id`, `status`, `total_lines`, `new_inserted`, `duplicates_skipped`
- [ ] `GET /admin/extrato/uploads/{seller_slug}` criado
  - Retorna lista de uploads ordenada por `created_at desc`
  - Aceita `limit` e `offset` query params
- [ ] Ambos endpoints protegidos por `require_admin`
- [ ] Upload do extrato janeiro 141Air.csv via curl retorna `new_inserted > 0` na primeira vez e `new_inserted = 0` na segunda (idempotencia)
- [ ] `python3 -m pytest` passa

### US-004: Aba Extratos no Admin Panel React

**Description:** Como admin, quero uma aba "Extratos" no painel admin para fazer upload de CSVs e ver historico de uploads por seller.

**Acceptance Criteria:**
- [ ] Nova aba "Extratos" no AdminPanel com navegacao funcional
- [ ] Componente de upload: selecao de seller + file input + botao "Enviar"
- [ ] Apos upload: mostra resultado (linhas novas, duplicatas, erros)
- [ ] Tabela de historico: lista uploads anteriores por seller com status e metricas
- [ ] Loading state durante upload
- [ ] Tratamento de erros (arquivo invalido, seller inexistente)
- [ ] `cd dashboard && npm run build` compila sem erros

## 4. Functional Requirements

- FR-1: O sistema deve aceitar upload de arquivo CSV no formato Account Statement do Mercado Pago (5 colunas: DATE, DESCRIPTION, REFERENCE_ID, TRANSACTION_NET_AMOUNT, BALANCE)
- FR-2: O sistema deve rejeitar arquivos que nao sao CSV ou que nao passam no parse do Account Statement
- FR-3: O sistema deve reutilizar 100% da logica de classificacao existente (`_classify_extrato_line`, 30+ regras)
- FR-4: O sistema deve deduplicar contra `payment_events` e `mp_expenses` existentes (mesma logica de `ingest_extrato_for_seller`)
- FR-5: O sistema deve persistir o resultado do upload em `extrato_uploads` com metricas
- FR-6: O sistema deve ser idempotente — upload do mesmo CSV duas vezes nao cria duplicatas

## 5. Non-Goals

- Nao alterar a logica de classificacao existente
- Nao criar novos tipos de despesa
- Nao integrar com event ledger (isso e Etapa 2+)
- Nao automatizar download do extrato da ML API
- Nao validar reconciliacao de caixa (isso e Etapa 2)

## 6. Technical Considerations

- A funcao `ingest_extrato_for_seller()` existente (1100+ linhas) baixa o extrato da ML API. A nova funcao `ingest_extrato_from_csv()` e um wrapper que pula o download e entra direto na pipeline de parse/classify/dedup/upsert.
- O parser `_parse_account_statement()` retorna `summary` (initial/final balance, credits, debits) + `transactions` (lista de dicts).
- O upload deve detectar encoding do CSV (UTF-8 ou Latin-1) automaticamente.

## 7. Success Metrics

- Upload do extrato janeiro 141Air.csv cria as mesmas linhas em `mp_expenses` que o script CLI
- Historico de uploads mostra status correto para cada upload
- 152+ testes pytest passando
- Dashboard compila sem erros

## 8. Open Questions

- Limite de tamanho do arquivo CSV? (sugestao: 10MB max)
- Permitir upload de multiplos meses no mesmo request? (sugestao: nao, um por vez)
