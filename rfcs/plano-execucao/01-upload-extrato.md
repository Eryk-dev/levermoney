# 01 — Upload de Extrato via Admin Panel

> **Track A do Plano de Execucao.** Feature pratica e independente.
> Pode (e deve) ser implementada ANTES do Track B (Unified Event Ledger).
>
> **Pre-requisito lido:** `app/services/extrato_ingester.py` (logica de ingestao existente),
> `app/routers/admin/extrato.py` (endpoints existentes de coverage/ingest),
> `migrations/006_payment_events.sql` (tabela payment_events).

---

## 1. Problema

O extrato CSV do Mercado Pago (account_statement) precisa ser baixado manualmente
pelo usuario no painel web do MP. Hoje o arquivo termina sendo passado por scripts
CLI (`testes/ingest_extrato_gaps.py`), o que quebra o fluxo operacional.

A logica de ingestao **ja existe** e **ja funciona** em `extrato_ingester.py`:
parse, classificacao por 30+ regras, deduplicacao contra `payment_events` e
`mp_expenses`, upsert na tabela `mp_expenses`. O que falta e apenas:

1. Uma nova funcao publica `ingest_extrato_from_csv()` que aceite o texto CSV
   diretamente (sem baixar da ML API).
2. Um endpoint de upload no admin panel.
3. Uma tabela de historico dos uploads (`extrato_uploads`).
4. Uma aba "Extratos" no Admin Panel React.

---

## 2. Fluxo Completo

```
Usuario baixa extrato CSV no painel web do Mercado Pago
    (Minha conta → Extrato → Exportar → formato CSV)
    |
    v
Admin Panel → aba "Extratos"
    → seleciona seller + mes
    → clica "Upload"
    → seleciona arquivo .csv do computador
    |
    v
POST /admin/extrato/upload
    → valida autenticacao (X-Admin-Token)
    → valida tamanho (max 5 MB)
    → valida formato CSV (header INITIAL_BALANCE obrigatorio)
    → chama ingest_extrato_from_csv(seller_slug, csv_text, month)
    |
    v
ingest_extrato_from_csv() — nova funcao em extrato_ingester.py
    → _parse_account_statement(csv_text)          (ja existe)
    → _classify_extrato_line() por linha           (ja existe)
    → batch lookups: payment_events + mp_expenses  (ja existe)
    → resolve _CHECK_PAYMENTS                      (ja existe)
    → upsert linhas novas em mp_expenses           (ja existe)
    → retorna stats dict
    |
    v
Backend persiste resultado em extrato_uploads
    (upsert por seller_slug + month: re-upload do mesmo mes substitui o registro)
    |
    v
Resposta: { lines_total, lines_ingested, lines_skipped,
            lines_already_covered, initial_balance, final_balance, gaps_found }
    |
    v
Novas linhas aparecem em mp_expenses com status = auto_categorized ou pending_review
    |
    v
Usuario vai para aba "Despesas"
    → exporta ZIP → revisa no Excel → importa no Conta Azul
```

---

## 3. Migration: `migrations/007_extrato_uploads.sql`

Arquivo **novo** a criar. Segue o padrao das migrations existentes (`006_payment_events.sql`):
comeca com comentario descritivo, usa `CREATE TABLE IF NOT EXISTS`, cria indices.

### Schema

```sql
-- Migration 007: extrato_uploads — historico de uploads de extratos CSV
--
-- Armazena o resultado de cada ingestao de extrato (account_statement)
-- enviada via Admin Panel. Permite rastrear quais meses foram carregados
-- e quantas linhas foram ingeridas por seller.
--
-- Idempotencia: upsert por (seller_slug, month).
-- Re-upload do mesmo mes atualiza o registro (nao duplica mp_expenses
-- pois o ingester usa composite keys para deduplicacao).

CREATE TABLE IF NOT EXISTS extrato_uploads (
    id                   BIGSERIAL PRIMARY KEY,
    seller_slug          TEXT NOT NULL REFERENCES sellers(slug),
    month                TEXT NOT NULL,              -- "2026-01" (YYYY-MM)
    filename             TEXT,                        -- nome original do arquivo
    uploaded_at          TIMESTAMPTZ DEFAULT NOW(),
    lines_total          INT,
    lines_ingested       INT,
    lines_skipped        INT,
    lines_already_covered INT,
    initial_balance      NUMERIC(12,2),
    final_balance        NUMERIC(12,2),
    status               TEXT DEFAULT 'processing',  -- processing | completed | failed | error
    error_message        TEXT,
    summary              JSONB,                       -- breakdown completo por tipo (by_type dict)
    UNIQUE (seller_slug, month)
);

-- Lookup por seller (aba Extratos do admin)
CREATE INDEX IF NOT EXISTS idx_extrato_uploads_seller
    ON extrato_uploads (seller_slug, month DESC);

-- Lookup por status (para alertas de falha)
CREATE INDEX IF NOT EXISTS idx_extrato_uploads_status
    ON extrato_uploads (status);
```

### Observacoes

- `UNIQUE(seller_slug, month)` garante idempotencia de re-uploads.
- `status = 'processing'` e gravado ANTES da ingestao comecar (permite detectar
  uploads que travaram por timeout).
- `summary` armazena o campo `by_type` do resultado de `ingest_extrato_for_seller`
  (dict com contagem por expense_type). Util para debug futuro.
- Nao ha FK para `mp_expenses`: o registro rastreia o evento de upload, nao
  cada linha individualmente (linhas vivem em `mp_expenses` com `source='extrato'`).

---

## 4. Nova funcao: `ingest_extrato_from_csv()`

**Arquivo:** `app/services/extrato_ingester.py`

Adicionar ABAIXO de `ingest_extrato_for_seller()` e ANTES de `ingest_extrato_all_sellers()`.

### Assinatura

```python
async def ingest_extrato_from_csv(
    seller_slug: str,
    csv_text: str,
    month: str,
) -> dict:
    """Ingest account_statement lines from a pre-downloaded CSV string.

    Variant of ingest_extrato_for_seller() that accepts raw CSV text
    instead of downloading the report from the ML API. Called by the
    admin upload endpoint when the user supplies the file manually.

    The month parameter is used for date filtering (keeps only lines
    within the calendar month) and for logging. It does NOT restrict
    what the CSV may contain — the CSV may include a few days of the
    previous or next month at the margins (MP reports overlap); those
    lines are filtered out before ingestion.

    Args:
        seller_slug: Seller identifier (must exist in sellers table).
        csv_text:    Raw text content of the account_statement CSV.
                     BOM (UTF-8-sig) is handled internally.
        month:       Calendar month to ingest, format "YYYY-MM".
                     Lines outside this month are skipped.

    Returns:
        Stats dict matching ingest_extrato_for_seller() return format:
        {
            seller, total_lines, skipped_internal, already_covered,
            amount_updated, newly_ingested, errors, by_type, summary
        }

    Raises:
        ValueError: If the CSV does not contain an INITIAL_BALANCE header
                    (i.e. it is not a valid account_statement CSV).
    """
```

### Logica de implementacao

A funcao segue exatamente os passos 2–7 de `ingest_extrato_for_seller()`,
pulando apenas o passo 1 (download via `_get_or_create_report()`).

Diferencas em relacao a `ingest_extrato_for_seller()`:

1. **Sem download ML API** — recebe `csv_text` diretamente como argumento.
2. **Filtro de data por mes** — deriva `begin_date` e `end_date` do parametro
   `month` (primeiro e ultimo dia do mes), em vez de receber datas explicitas.
3. **Validacao do CSV** — verifica se `"INITIAL_BALANCE"` esta presente no
   texto antes de chamar `_parse_account_statement()`. Se ausente, levanta
   `ValueError("CSV invalido: header INITIAL_BALANCE nao encontrado")`.
4. **Log do origem** — usa `logger.info("extrato_ingester %s: upload path, month=%s, ...")`.

```python
# Derivar begin_date / end_date a partir do mes
import calendar
year, mo = int(month[:4]), int(month[5:7])
begin_date = f"{year:04d}-{mo:02d}-01"
last_day = calendar.monthrange(year, mo)[1]
end_date = f"{year:04d}-{mo:02d}-{last_day:02d}"
```

O restante do corpo e identico ao bloco de `ingest_extrato_for_seller()` apos
o download, reaproveitando todas as funcoes internas existentes sem modificacao:
`_parse_account_statement`, `_classify_extrato_line`, `_resolve_check_payments`,
`_build_expense_from_extrato`, `_batch_lookup_payment_ids`,
`_batch_lookup_expense_payment_ids`, `_batch_lookup_expense_details`,
`_batch_lookup_composite_expense_ids`, `_batch_lookup_refunded_payment_ids`,
`_update_expense_amount_from_extrato`, `_fuzzy_match_expense`.

**Nao reimplementar nenhuma dessas funcoes.** Chamar diretamente.

---

## 5. Endpoints

### 5.1 `POST /admin/extrato/upload`

**Arquivo:** `app/routers/admin/extrato.py` — adicionar ao router existente.

#### Descricao

Recebe um arquivo CSV via multipart/form-data, valida, ingere e persiste o
resultado em `extrato_uploads`.

#### Parametros (form-data)

| Campo | Tipo | Obrigatorio | Descricao |
|-------|------|-------------|-----------|
| `file` | UploadFile | sim | Arquivo CSV do extrato MP |
| `seller_slug` | str | sim | Slug do seller (ex: `netair`) |
| `month` | str | sim | Mes no formato `YYYY-MM` (ex: `2026-01`) |

#### Validacoes

1. **Auth:** `require_admin` dependency (X-Admin-Token).
2. **Tamanho:** rejeitar se `file.size > 5 * 1024 * 1024` (5 MB).
   Extratos reais sao tipicamente 50–200 KB. Limite protege contra uploads
   acidentais de arquivos errados.
3. **Content-type:** aceitar `text/csv`, `application/csv`, `text/plain` e
   `application/octet-stream` (navegadores variam no MIME type de CSV).
4. **Formato CSV:** apos decodificacao, verificar se o texto contem
   `"INITIAL_BALANCE"` (case-insensitive). Se nao, retornar HTTP 422.
5. **Seller existe:** verificar no banco antes de processar.
6. **Mes valido:** validar formato YYYY-MM com regex `r"^\d{4}-(0[1-9]|1[0-2])$"`.

#### Logica

```
1. Ler bytes do arquivo (await file.read())
2. Tentar decodificar: utf-8-sig → latin-1 (mesmo padrao do ingester existente)
3. Validar presenca de INITIAL_BALANCE
4. Gravar registro em extrato_uploads com status='processing'
   (upsert por seller_slug+month)
5. Chamar ingest_extrato_from_csv(seller_slug, csv_text, month)
6. Atualizar extrato_uploads com resultado (status='completed' ou 'failed')
7. Retornar resultado
```

#### Resposta de sucesso (HTTP 200)

```json
{
  "seller_slug": "netair",
  "month": "2026-01",
  "filename": "extrato janeiro netair.csv",
  "lines_total": 690,
  "lines_ingested": 12,
  "lines_skipped": 480,
  "lines_already_covered": 198,
  "amount_updated": 0,
  "initial_balance": 4476.23,
  "final_balance": 1090.40,
  "gaps_found": {
    "difal": 3,
    "reembolso_disputa": 5,
    "liberacao_cancelada": 2,
    "faturas_ml": 2
  },
  "upload_id": 42
}
```

O campo `gaps_found` e o `by_type` dict retornado pela funcao de ingestao.
O campo `upload_id` e o `id` do registro inserido em `extrato_uploads`.

#### Respostas de erro

| HTTP | Condicao | Exemplo de detail |
|------|----------|-------------------|
| 401 | Token invalido | `"Unauthorized"` |
| 413 | Arquivo maior que 5 MB | `"File too large: 6.2MB exceeds 5MB limit"` |
| 422 | CSV invalido (sem INITIAL_BALANCE) | `"Invalid CSV: INITIAL_BALANCE header not found. Make sure you downloaded the account statement (extrato) from Mercado Pago."` |
| 422 | Mes invalido | `"Invalid month format. Expected YYYY-MM, got '2026-13'"` |
| 404 | Seller nao encontrado | `"Seller 'xxx' not found"` |
| 500 | Erro interno | `"Ingestion failed: <detalhe>"` |

#### Idempotencia

Re-upload do mesmo `(seller_slug, month)` e seguro:
- O registro em `extrato_uploads` e substituido (ON CONFLICT DO UPDATE).
- As linhas em `mp_expenses` nao sao duplicadas (dedup via composite keys
  ja implementada no ingester).
- Linhas com `status='exported'` no `mp_expenses` nunca sao sobrescritas.

---

### 5.2 `GET /admin/extrato/status`

**Arquivo:** `app/routers/admin/extrato.py`

#### Descricao

Retorna, para cada seller ativo, quais meses tem extrato uploadado e quais
estao com gaps (seller tem `payment_events` no mes mas nenhum upload registrado).

#### Parametros (query string)

| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `seller_slug` | str | None | Filtrar por seller (opcional) |
| `months_back` | int | 3 | Quantos meses verificar (contando o mes atual) |

#### Logica

```
1. Listar todos os sellers ativos (get_all_active_sellers)
2. Para cada seller (ou o seller filtrado):
   a. Determinar os meses a verificar (mes atual + months_back anteriores)
   b. Consultar extrato_uploads para esses meses
   c. Consultar payment_events para checar se ha eventos no mes
      (COUNT por (seller_slug, competencia_date) agrupado por mes)
   d. Classificar cada mes:
      - "uploaded"  → tem registro em extrato_uploads com status='completed'
      - "missing"   → tem payment_events mas nao tem upload
      - "no_data"   → nao tem payment_events nem upload (mes sem movimentacao)
      - "failed"    → registro em extrato_uploads com status='failed' ou 'error'
3. Retornar lista de sellers com breakdown por mes
```

#### Resposta (HTTP 200)

```json
{
  "generated_at": "2026-03-13T10:30:00Z",
  "months_checked": ["2026-01", "2026-02", "2026-03"],
  "sellers": [
    {
      "seller_slug": "netair",
      "months": {
        "2026-01": {
          "status": "uploaded",
          "upload_id": 12,
          "uploaded_at": "2026-02-05T14:23:00Z",
          "lines_ingested": 12,
          "lines_total": 690,
          "initial_balance": 4476.23,
          "final_balance": 1090.40
        },
        "2026-02": {
          "status": "missing",
          "upload_id": null,
          "payment_events_count": 143
        },
        "2026-03": {
          "status": "missing",
          "upload_id": null,
          "payment_events_count": 37
        }
      }
    },
    {
      "seller_slug": "141air",
      "months": {
        "2026-01": {
          "status": "uploaded",
          "upload_id": 8,
          "uploaded_at": "2026-02-03T09:11:00Z",
          "lines_ingested": 8,
          "lines_total": 521,
          "initial_balance": 1200.00,
          "final_balance": 890.50
        },
        "2026-02": { "status": "no_data" },
        "2026-03": { "status": "missing", "payment_events_count": 12 }
      }
    }
  ]
}
```

---

### 5.3 `GET /admin/extrato/status/{seller_slug}`

**Arquivo:** `app/routers/admin/extrato.py`

#### Descricao

Detalhamento de um seller especifico: todos os meses com upload registrado,
ordenados do mais recente para o mais antigo.

#### Parametros

| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `seller_slug` | path | — | Slug do seller |
| `months_back` | int query | 12 | Quantos meses verificar |

#### Resposta (HTTP 200)

```json
{
  "seller_slug": "netair",
  "generated_at": "2026-03-13T10:30:00Z",
  "months": [
    {
      "month": "2026-03",
      "status": "missing",
      "upload_id": null,
      "payment_events_count": 37
    },
    {
      "month": "2026-02",
      "status": "failed",
      "upload_id": 31,
      "uploaded_at": "2026-03-01T08:00:00Z",
      "error_message": "CSV invalido: header INITIAL_BALANCE nao encontrado",
      "lines_total": null,
      "lines_ingested": null
    },
    {
      "month": "2026-01",
      "status": "uploaded",
      "upload_id": 12,
      "uploaded_at": "2026-02-05T14:23:00Z",
      "filename": "extrato janeiro netair.csv",
      "lines_total": 690,
      "lines_ingested": 12,
      "lines_skipped": 480,
      "lines_already_covered": 198,
      "initial_balance": 4476.23,
      "final_balance": 1090.40,
      "gaps_found": {
        "difal": 3,
        "reembolso_disputa": 5,
        "liberacao_cancelada": 2,
        "faturas_ml": 2
      }
    }
  ]
}
```

---

## 6. Admin Panel UI: `ExtratoTab.tsx`

**Arquivo:** `dashboard/src/components/AdminPanel/ExtratoTab.tsx` (novo)

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Extratos                              [Atualizar]            │
│ Meses verificados: Mar/2026  Fev/2026  Jan/2026             │
├──────────────┬────────────┬────────────┬────────────────────┤
│ Seller       │ Mar/2026   │ Fev/2026   │ Jan/2026           │
├──────────────┼────────────┼────────────┼────────────────────┤
│ 141air       │ [upload]   │ [upload]   │ Uploaded 03/02 ✓   │
│              │ (missing)  │ (missing)  │ 521 linhas / 8 gap │
├──────────────┼────────────┼────────────┼────────────────────┤
│ netair       │ [upload]   │ ✗ falhou   │ Uploaded 05/02 ✓   │
│              │ (missing)  │ [retentar] │ 690 linhas / 12 gap│
├──────────────┼────────────┼────────────┼────────────────────┤
│ easypeasy    │ — sem dados│ — sem dados│ — sem dados        │
└──────────────┴────────────┴────────────┴────────────────────┘
```

### Indicadores de status por celula

| Status | Cor | Icone | Texto |
|--------|-----|-------|-------|
| `uploaded` | Verde | ✓ | "Uploaded DD/MM — N linhas / G gap" |
| `missing` | Amarelo | ⚠ | "(faltando)" + botao Upload |
| `failed` / `error` | Vermelho | ✗ | "Falhou" + botao Retentar |
| `no_data` | Cinza | — | "Sem movimentacao" |
| `processing` | Azul | ... | "Processando..." |

### Comportamento do botao Upload

1. Abre `<input type="file" accept=".csv">` via `useRef`.
2. Apos selecao do arquivo, exibe modal de confirmacao:
   ```
   Fazer upload do extrato de [MES] para [SELLER]?
   Arquivo: extrato janeiro netair.csv (127 KB)
   [Cancelar]  [Upload]
   ```
3. Durante o upload: spinner no lugar do botao, celula em estado `processing`.
4. Apos sucesso: celula muda para `uploaded`, exibe resumo inline:
   ```
   Uploaded agora ✓
   690 linhas • 12 novos gaps
   difal: 3 | reembolso: 5 | outros: 4
   ```
5. Apos erro: celula muda para `failed`, exibe mensagem de erro truncada
   com tooltip para ver o erro completo.

### Hook sugerido: `useExtratoStatus`

```typescript
interface ExtratoMonthStatus {
  month: string;               // "YYYY-MM"
  status: "uploaded" | "missing" | "no_data" | "failed" | "processing";
  upload_id: number | null;
  uploaded_at: string | null;
  lines_total: number | null;
  lines_ingested: number | null;
  initial_balance: number | null;
  final_balance: number | null;
  gaps_found: Record<string, number> | null;
  error_message: string | null;
}

interface ExtratoSellerStatus {
  seller_slug: string;
  months: Record<string, ExtratoMonthStatus>;
}

// Funcoes expostas pelo hook
loadStatus(months_back?: number): Promise<void>
uploadExtrato(sellerSlug: string, month: string, file: File): Promise<ExtratoMonthStatus>
```

A funcao `uploadExtrato` envia `FormData` com `file`, `seller_slug` e `month`
para `POST /admin/extrato/upload`. Atualiza o estado local imediatamente apos
a resposta (sem precisar recarregar tudo).

### Integracao no AdminPanel

Adicionar `ExtratoTab` como nova aba no componente `AdminPanel`, entre as abas
existentes de "Sellers" e "Despesas". A aba so e exibida quando o admin
esta autenticado (ja garantido pelo `AdminPanel` pai).

---

## 7. Arquivos a Criar ou Modificar

### Novos

| Arquivo | O que fazer |
|---------|-------------|
| `migrations/007_extrato_uploads.sql` | Schema da tabela `extrato_uploads` (secao 3) |
| `dashboard/src/components/AdminPanel/ExtratoTab.tsx` | Aba de upload no Admin Panel (secao 6) |

### Modificados

| Arquivo | O que adicionar |
|---------|-----------------|
| `app/services/extrato_ingester.py` | Nova funcao `ingest_extrato_from_csv()` (secao 4) |
| `app/routers/admin/extrato.py` | 3 novos endpoints: POST upload, GET status, GET status/{seller} (secao 5) |

**Nao modificar** nenhuma outra funcao existente em `extrato_ingester.py`.
A nova funcao e um wrapper que chama as funcoes privadas existentes — nao
duplica logica.

---

## 8. Funcoes Reutilizadas (NAO reimplementar)

Todas as funcoes abaixo de `app/services/extrato_ingester.py` devem ser
chamadas diretamente pela nova `ingest_extrato_from_csv()`. Nao copiar,
nao mover, nao alterar:

| Funcao | Responsabilidade |
|--------|-----------------|
| `_parse_account_statement(csv_text)` | Parseia CSV em (summary, transactions) |
| `_classify_extrato_line(transaction_type)` | Classifica por 30+ regras → (expense_type, direction, ca_category_uuid) |
| `_resolve_check_payments(transaction_type)` | Resolve _CHECK_PAYMENTS → fallback type quando ref_id nao esta em payment_events |
| `_build_expense_from_extrato(tx, seller_slug, ...)` | Monta row para upsert em mp_expenses |
| `_batch_lookup_payment_ids(db, seller_slug, ref_ids)` | Busca ref_ids em payment_events |
| `_batch_lookup_expense_payment_ids(db, seller_slug, ref_ids)` | Busca ref_ids em mp_expenses (plain integer) |
| `_batch_lookup_expense_details(db, seller_slug, ref_ids)` | Busca detalhes (amount, status) para correcao de IOF |
| `_batch_lookup_composite_expense_ids(db, seller_slug, composite_keys)` | Busca chaves compositas ja ingeridas |
| `_batch_lookup_refunded_payment_ids(db, seller_slug, ref_ids)` | Busca payments com refund_created (evita double-count disputas) |
| `_update_expense_amount_from_extrato(db, seller_slug, detail, real_amount, ref_id)` | Corrige amount de mp_expense existente (IOF diff) |
| `_fuzzy_match_expense(db, seller_slug, amount, date, expense_types)` | Dedup por amount+date+type para faturas_ml com IDs internos ML |

---

## 9. Guard Rails e Restricoes

### Idempotencia de uploads

Re-upload do mesmo `(seller_slug, month)` e totalmente seguro:

- **`extrato_uploads`:** usa `ON CONFLICT (seller_slug, month) DO UPDATE` — o
  registro anterior e substituido com os novos stats.
- **`mp_expenses`:** o ingester usa composite keys `"{ref_id}:{abbrev}"` com
  logica de deduplicacao em varios niveis (payment_events, composite IDs,
  fuzzy match). Linhas ja exportadas (`status='exported'`) nunca sao
  sobrescritas.

Consequencia: o usuario pode fazer upload varias vezes no mesmo mes sem risco
de duplicar lancamentos no Conta Azul.

### Limite de tamanho

5 MB hardcoded no endpoint. Justificativa: extratos reais observados em
jan/2026 tem entre 50 KB (easypeasy) e 200 KB (netair com 690 linhas).
5 MB e ~25x o maior extrato conhecido — proteção contra arquivos errados.

### Validacao do CSV

O header `INITIAL_BALANCE` e obrigatorio no arquivo. E a primeira linha da
primeira secao do formato account_statement do Mercado Pago. Arquivos que nao
tem esse header (planilhas Excel exportadas como CSV, outros relatorios MP, etc.)
sao rejeitados com HTTP 422 e mensagem explicativa.

### Autenticacao

Todos os 3 novos endpoints usam `require_admin` (dependency de `admin/_deps.py`),
identico aos endpoints existentes no mesmo router. Nenhum endpoint e publico.

### Sem processamento background

O upload e sincrono: o endpoint aguarda a conclusao da ingestao antes de
responder. Justificativa: extratos de 690 linhas processam em ~1-2 segundos
(principalmente IO Supabase). Nao ha necessidade de task background como
o GDrive backup das despesas. O timeout default de 120s do uvicorn e mais
que suficiente.

### Encoding

O endpoint tenta decodificar em `utf-8-sig` (para BOM) e faz fallback para
`latin-1` — o mesmo padrao de `ingest_extrato_for_seller()`. O usuario nao
precisa se preocupar com encoding ao exportar do MP.

---

## 10. Ordem de Implementacao Recomendada

Execute nesta ordem para poder testar cada etapa isoladamente:

1. **Migration** — rodar `007_extrato_uploads.sql` no Supabase.
2. **`ingest_extrato_from_csv()`** — adicionar a funcao em `extrato_ingester.py`.
   Testar via pytest com o fixture CSV de 141air jan/2026 existente em
   `testes/data/` (se disponivel) ou direto no REPL.
3. **Endpoint `POST /admin/extrato/upload`** — adicionar em `extrato.py`.
   Testar com `curl -F "file=@extrato.csv" -F "seller_slug=141air" -F "month=2026-01"`.
4. **Endpoints `GET /admin/extrato/status`** — adicionar em `extrato.py`.
5. **`ExtratoTab.tsx`** — implementar UI React.
6. **Testes pytest** — adicionar testes para `ingest_extrato_from_csv()` em
   `testes/test_extrato_classification.py` ou arquivo novo.

---

## 11. Testes a Escrever

### Unitarios (pytest, offline)

| Caso | Input | Esperado |
|------|-------|----------|
| CSV valido com INITIAL_BALANCE | texto completo do extrato 141air jan/2026 | parse correto: summary + transactions |
| CSV sem INITIAL_BALANCE | string CSV qualquer sem esse header | `ValueError` levantado |
| CSV vazio | string vazia | `ValueError` ou result com total_lines=0 |
| Re-ingestao do mesmo mes | chamar 2x com mesmo CSV | 2a chamada: `newly_ingested=0`, `already_covered=N` |
| Filtro de mes | CSV com linhas de dez/2025 e jan/2026 | so linhas de jan/2026 ingeridas |

### De integracao

Para testar com Supabase real, usar `seller_slug='141air'` e `month='2026-01'`
com o CSV de referencia. Validar contra os valores conhecidos da auditoria
jan/2026 (saldo inicial R$ 4.476,23, saldo final R$ 1.090,40, 690 linhas).

---

## 12. Impacto em Documentos Existentes

Apos implementar, atualizar os seguintes arquivos:

| Documento | O que atualizar |
|-----------|-----------------|
| `docs/TABELAS.md` | Adicionar schema de `extrato_uploads` |
| `docs/ROTAS.md` | Adicionar os 3 novos endpoints |
| `docs/CODE_MAP.md` | Adicionar assinatura de `ingest_extrato_from_csv()` |
| `app/routers/CLAUDE.md` | Atualizar descricao do modulo `extrato.py` |
| `app/services/CLAUDE.md` | Atualizar descricao do `extrato_ingester.py` |
| `CLAUDE.md` (raiz) | Sem necessidade: a estrutura de arquivos nao muda |

---

*Criado: 2026-03-13*
*Status: Aguardando implementacao*
