# PRD: Extrato Obrigatorio no Onboarding (dashboard_ca)

## 1. Introducao/Overview

Atualmente, o onboarding de sellers no modo `dashboard_ca` faz backfill historico exclusivamente via API do Mercado Pago (`search_payments` por `money_release_date`). Isso cobre vendas (order payments) e non-orders classificados pelo `expense_classifier`, mas **nao garante cobertura de 100% das linhas do extrato** — o `extrato_ingester` roda apenas no nightly pipeline (D-1 a D-3), deixando todo o periodo historico (`ca_start_date` ate ontem) potencialmente com lacunas em `mp_expenses`.

Esta feature exige o upload de um CSV de extrato de conta (`account_statement`) durante a ativacao/upgrade para `dashboard_ca`. O CSV e processado imediatamente para popular `mp_expenses` com todas as linhas non-order (lacunas) do periodo historico, garantindo cobertura completa desde o primeiro dia.

**Problema:** Lacunas historicas em `mp_expenses` que so seriam cobertas manualmente ou pelo nightly pipeline (que so olha D-1..D-3).

**Solucao:** Exigir extrato CSV no onboarding → processar com logica do `extrato_ingester` → armazenar original no Google Drive.

---

## 2. Goals

- Garantir 100% de cobertura do extrato em `mp_expenses` desde `ca_start_date` ate D-1 no momento da ativacao
- Eliminar a necessidade de rodar `extrato_ingester` manualmente para o periodo historico
- Armazenar o CSV original no Google Drive (pasta por seller) para auditoria
- Rejeitar ativacoes `dashboard_ca` que nao incluam extrato com periodo completo
- Manter fluxo `dashboard_only` inalterado (extrato nao exigido)

---

## 3. User Stories

### US-001: Upload de extrato no activate (dashboard_ca)

**Description:** As an admin, I want to upload an extrato CSV when activating a seller in `dashboard_ca` mode so that mp_expenses is fully populated from day one.

**Acceptance Criteria:**
- [ ] Endpoint `POST /admin/sellers/{slug}/activate` aceita multipart form data com campo `extrato_csv` (arquivo CSV)
- [ ] Campo `extrato_csv` e obrigatorio quando `integration_mode == "dashboard_ca"`
- [ ] Campo `extrato_csv` e ignorado/opcional quando `integration_mode == "dashboard_only"`
- [ ] Resposta inclui `extrato_processed: {lines_total, lines_inserted, lines_skipped}` alem dos campos existentes
- [ ] Typecheck/lint passa

### US-002: Upload de extrato no upgrade-to-ca

**Description:** As an admin, I want to upload an extrato CSV when upgrading a seller from `dashboard_only` to `dashboard_ca` so that the historical period is fully covered.

**Acceptance Criteria:**
- [ ] Endpoint `POST /admin/sellers/{slug}/upgrade-to-ca` aceita multipart form data com campo `extrato_csv` (arquivo CSV obrigatorio)
- [ ] Validacao de periodo identica ao activate
- [ ] Resposta inclui `extrato_processed` com contadores
- [ ] Typecheck/lint passa

### US-003: Validacao de periodo do CSV

**Description:** As the system, I want to validate that the extrato CSV covers exactly `ca_start_date` to D-1 so that no gaps exist in the historical data.

**Acceptance Criteria:**
- [ ] Parser le CSV com estrutura: header `INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE`, linha de saldos, linha vazia, header `RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE`, linhas de transacoes
- [ ] Separador e `;` (ponto-e-virgula), valores monetarios usam `,` como decimal e `.` como milhar
- [ ] Extrai `min(RELEASE_DATE)` e `max(RELEASE_DATE)` do CSV
- [ ] Rejeita com 400 se `min(RELEASE_DATE) > ca_start_date` (falta cobertura no inicio)
- [ ] Rejeita com 400 se `max(RELEASE_DATE) < ontem` (falta cobertura no final)
- [ ] Aceita se `min(RELEASE_DATE) <= ca_start_date` E `max(RELEASE_DATE) >= ontem`
- [ ] Mensagem de erro clara indicando qual periodo esta faltando
- [ ] Aceita datas no formato `DD-MM-YYYY` (formato padrao do extrato MP)

### US-004: Processamento do extrato → mp_expenses

**Description:** As the system, I want to process the extrato CSV and insert gap lines into mp_expenses so that the seller's historical non-order activity is fully represented.

**Acceptance Criteria:**
- [ ] Reutiliza a logica existente do `extrato_ingester.py` para classificar linhas
- [ ] Cada linha do extrato e verificada contra `payments` (order payments) e `mp_expenses` (non-orders ja classificados pelo backfill da API)
- [ ] Linhas nao cobertas sao inseridas em `mp_expenses` com `source="extrato"`
- [ ] `payment_id` composto segue padrao existente do extrato_ingester (ex: `"reference_id:tipo"`)
- [ ] Linhas de `Liberacao de dinheiro` sao ignoradas (cobertas pelo backfill de payments)
- [ ] Processamento e sincrono (bloqueia resposta do endpoint ate concluir)
- [ ] Retorna contadores: `{lines_total, lines_inserted, lines_skipped, lines_already_covered}`

### US-005: Armazenamento do CSV no Google Drive

**Description:** As an admin, I want the original extrato CSV stored in Google Drive so that I can audit and reference it later.

**Acceptance Criteria:**
- [ ] CSV e salvo no Google Drive usando a service account ja configurada (`LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_JSON` ou `_FILE`)
- [ ] Estrutura de pastas: `{GOOGLE_DRIVE_ROOT}/onboarding/{seller_slug}/extrato_{ca_start_date}_to_{end_date}.csv`
- [ ] Se pasta do seller nao existe, cria automaticamente
- [ ] Se Google Drive nao estiver configurado, processamento continua normalmente mas log warning sobre storage nao disponivel
- [ ] URL do arquivo no Drive e salva na tabela `sellers` em campo novo `extrato_onboarding_drive_url`

### US-006: Retry de backfill preserva extrato

**Description:** As an admin, I want backfill retries to not require re-upload of the extrato so that re-runs are seamless.

**Acceptance Criteria:**
- [ ] `POST /admin/sellers/{slug}/backfill-retry` nao exige extrato (ja foi processado no activate/upgrade)
- [ ] Linhas inseridas pelo extrato no primeiro run sao idempotentes (nao duplicam no retry)
- [ ] `_load_already_done()` do `onboarding_backfill.py` ja cobre linhas com `source="extrato"` em `mp_expenses`

---

## 4. Functional Requirements

**FR-1:** O sistema deve aceitar upload de arquivo CSV via multipart form data nos endpoints `/admin/sellers/{slug}/activate` e `/admin/sellers/{slug}/upgrade-to-ca`.

**FR-2:** O sistema deve rejeitar (HTTP 400) a ativacao `dashboard_ca` se o campo `extrato_csv` nao for enviado.

**FR-3:** O sistema deve parsear o CSV com separador `;`, formato de data `DD-MM-YYYY`, valores monetarios com `,` decimal.

**FR-4:** O sistema deve validar que o periodo do CSV cobre `ca_start_date` ate `D-1` (ontem). Caso contrario, retorna HTTP 400 com mensagem indicando o gap.

**FR-5:** O sistema deve processar cada linha do CSV usando a logica de classificacao do `extrato_ingester.py`, inserindo lacunas em `mp_expenses` com `source="extrato"`.

**FR-6:** O sistema deve fazer upload do CSV original para o Google Drive na pasta `onboarding/{seller_slug}/`.

**FR-7:** O sistema deve salvar a URL do Drive na coluna `extrato_onboarding_drive_url` da tabela `sellers`.

**FR-8:** O processamento do extrato deve ocorrer **antes** do lancamento do backfill task, para que o backfill possa usar as linhas inseridas como "already done" e nao duplicar.

**FR-9:** O endpoint `dashboard_only` deve ignorar o campo `extrato_csv` se enviado (nao bloquear, nao processar).

**FR-10:** Se Google Drive nao estiver configurado (envs vazias), o processamento do CSV em `mp_expenses` deve prosseguir normalmente, apenas o upload para Drive e pulado com log warning.

---

## 5. Non-Goals (Out of Scope)

- **UI de upload no dashboard React** — por enquanto, upload e via admin API (Postman/curl). UI pode ser adicionada futuramente.
- **Multiplos CSVs** — o endpoint aceita um unico CSV cobrindo todo o periodo. Se o seller tem extratos separados por mes, deve concatenar antes de enviar.
- **Validacao de saldo** — nao validaremos se `INITIAL_BALANCE + CREDITS + DEBITS = FINAL_BALANCE`.
- **Processamento assincrono do CSV** — o CSV e processado sincronamente no request. Para sellers com 6+ meses de historico, isso pode levar alguns segundos, mas e aceitavel dado que e uma operacao one-time feita pelo admin.
- **Re-upload de extrato** — nao havera endpoint dedicado para re-upload. Se necessario, o admin faz manualmente via `POST /admin/extrato/ingest/{seller}`.
- **Suporte a outros formatos** (XLSX, OFX) — apenas CSV do account_statement do MP.

---

## 6. Technical Considerations

### 6.1 Mudanca de endpoint de JSON para Multipart

Os endpoints `activate` e `upgrade-to-ca` atualmente recebem JSON body (`ActivateSellerRequest` / `UpgradeToCaRequest`). Com o upload de arquivo, precisam migrar para **multipart form data**.

**Opcoes:**
- **A) FastAPI `Form` + `UploadFile`:** Campos do request como `Form(...)` + arquivo como `UploadFile`. Mais limpo, mas muda a assinatura dos endpoints.
- **B) Endpoint separado para upload:** Manter endpoints existentes inalterados, criar `POST /admin/sellers/{slug}/upload-extrato` que deve ser chamado antes do activate. Mais retrocompativel.

**Recomendacao:** Opcao A — mudar para multipart. O admin panel (dashboard React) pode ser ajustado depois. Por enquanto, quem usa e Postman/curl do admin.

### 6.2 Estrutura do CSV

```
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
4.476,23;207.185,69;-210.571,52;1.090,40

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-01-2026;Transferencia Pix enviada LILLIAN DA ROCHA;139632176183;-350,00;4.126,23
01-01-2026;Liberacao de dinheiro ;138199281600;3.994,84;5.771,27
...
```

- Separador: `;`
- Datas: `DD-MM-YYYY`
- Valores: `-1.234,56` (negativo com hifen, milhar com ponto, decimal com virgula)
- Linhas vazias separam header de saldos do header de transacoes

### 6.3 Reutilizacao do extrato_ingester

O `extrato_ingester.py` ja tem toda a logica de:
- Classificar linhas do extrato por `TRANSACTION_TYPE`
- Verificar cobertura contra `payments` e `mp_expenses`
- Inserir lacunas com `source="extrato"` e `payment_id` composto

A nova feature deve extrair/reutilizar essa logica, nao duplicar. Idealmente, criar uma funcao `process_extrato_csv(seller_slug, csv_bytes, date_from, date_to)` que pode ser chamada tanto pelo onboarding quanto pelo ingester nightly.

### 6.4 Google Drive Integration

Ja existe integracao com Google Drive em `legacy_daily_export.py`. Reutilizar:
- `LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_JSON` / `_FILE` para autenticacao
- `LEGACY_DAILY_GOOGLE_DRIVE_ROOT_FOLDER_ID` como pasta raiz
- Criar subpasta `onboarding/{seller_slug}/` se nao existir

### 6.5 Ordem de Processamento

```
1. Validar request (campos obrigatorios, formato CSV, periodo)
2. Parsear CSV → lista de linhas
3. Processar linhas → inserir lacunas em mp_expenses (source="extrato")
4. Upload CSV original para Google Drive (best-effort)
5. Atualizar seller (integration_mode, ca_start_date, etc.)
6. Lancar backfill task em background
7. Retornar resposta com contadores
```

O passo 3 **deve** ocorrer antes do passo 6 para que o backfill ja encontre as linhas do extrato como "already done" e nao tente re-classificar.

### 6.6 Nova coluna na tabela sellers

```sql
ALTER TABLE sellers ADD COLUMN extrato_onboarding_drive_url text;
```

---

## 7. Success Metrics

- **Cobertura 100%:** Apos ativacao com extrato, `check_extrato_coverage()` deve retornar `coverage_pct = 100%` para o periodo `ca_start_date` ate D-1
- **Zero lacunas manuais:** Admin nao precisa rodar `POST /admin/extrato/ingest/{seller}` manualmente apos onboarding
- **Idempotencia:** Re-upload ou retry de backfill nao duplica linhas em `mp_expenses`
- **Tempo de processamento:** CSV de 1 mes (~500 linhas) processa em < 5 segundos

---

## 8. Open Questions

1. **Limite de tamanho do CSV:** Sellers com 12+ meses de historico podem ter CSVs de 10k+ linhas. Precisamos de um limite de tamanho no upload? (Sugestao: 10MB max)
2. **Encoding do CSV:** O extrato do MP vem em UTF-8 ou Latin-1? Precisamos detectar encoding automaticamente?
3. **Concatenacao de extratos mensais:** Se o seller so consegue baixar extrato mes a mes no MP, devemos aceitar multiplos arquivos ou exigir que concatene? (Decisao atual: exigir unico CSV, mas pode ser revisado)

---

## Appendix: Ralph Loop Implementation Order

Para implementacao via Ralph (autonomous agent loop), as user stories devem ser executadas nesta ordem:

```
Loop 1: US-003 (Parser + validacao de periodo)
  → Criar funcao standalone parse_extrato_csv() e validate_extrato_period()
  → Testes com CSV de referencia (testes/extratos/extrato janeiro 141Air.csv)

Loop 2: US-004 (Processamento → mp_expenses)
  → Extrair logica reutilizavel do extrato_ingester.py
  → Criar process_extrato_csv(seller_slug, csv_bytes, date_from, date_to)
  → Testar com CSV de referencia + verificar mp_expenses populado

Loop 3: US-005 (Google Drive storage)
  → Reutilizar integracao existente do legacy_daily_export.py
  → Criar funcao upload_extrato_to_drive(seller_slug, csv_bytes, date_range)
  → Migration: nova coluna extrato_onboarding_drive_url

Loop 4: US-001 + US-002 (Endpoints activate + upgrade-to-ca)
  → Migrar endpoints para multipart form data
  → Integrar parse + process + upload na sequencia correta
  → Validar que backfill respeita linhas ja inseridas

Loop 5: US-006 (Retry idempotente)
  → Verificar que backfill-retry nao exige re-upload
  → Verificar que _load_already_done() cobre source="extrato"
  → Teste end-to-end: activate → backfill → retry
```
