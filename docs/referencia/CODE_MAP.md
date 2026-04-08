> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

## 8. Code Map — Assinaturas de Todas as Funcoes

### app/services/processor.py — CORE

```python
def _to_brt_date(iso_str: str) -> str:
    """Converte ISO datetime ML (UTC-4) → BRT date (YYYY-MM-DD).
    Late-night sales cross midnight: 23:45 UTC-4 = 00:45 BRT → dia seguinte."""

def _extract_processor_charges(payment: dict) -> tuple[float, float, str | None, float, float]:
    """Extrai fee/frete de charges_details e reconcilia net calculado vs net real."""

def _build_parcela(descricao, data_vencimento, conta_financeira, valor, nota="") -> dict:
    """Monta parcela CA v2. Inclui detalhe_valor com valor_bruto e valor_liquido."""

def _build_evento(data_competencia, valor, descricao, observacao, contato,
                  conta_financeira, categoria, centro_custo, parcela,
                  rateio_centro_custo=True) -> dict:
    """Monta evento financeiro CA v2 com rateio e condicao_pagamento."""

def _build_despesa_payload(seller, data_competencia, data_vencimento,
                           valor, descricao, observacao, categoria, nota_parcela="") -> dict:
    """Build conta-a-pagar payload completo. Baixa feita separadamente pelo job /baixas."""

async def process_payment_webhook(seller_slug: str, payment_id: int, payment_data: dict = None):
    """Entry point principal. Classifica payment e despacha para handler correto.
    payment_data: if provided, skips API fetch (used by daily_sync)."""

async def _process_approved(db, seller, payment, existing):
    """EVENTO 1: Venda aprovada. Cria receita + comissao + frete + subsidio (se houver)."""

async def _process_partial_refund(db, seller, payment):
    """Refund parcial (status_detail='partially_refunded'). Estornos proporcionais."""

async def _process_refunded(db, seller, payment, existing):
    """EVENTO 4: Devolucao total. Cria receita original se necessario + estornos."""

def _upsert_payment(db, seller_slug, payment, status, error=None, ca_evento_id=None,
                    processor_fee=None, processor_shipping=None):
    """Insere ou atualiza payment no Supabase (idempotencia)."""
```

### app/services/ca_api.py — Cliente Conta Azul

```python
async def _get_ca_token() -> str:
    """Token CA com cache em memoria + refresh via OAuth2 com rotation.
    Lock asyncio previne refresh concorrente."""

async def _refresh_access_token(refresh_token) -> tuple[str, int, str | None]:
    """POST auth.contaazul.com/oauth2/token. Retorna (access, expires_in, new_refresh)."""

async def _request_with_retry(method, url, max_retries=3, **kwargs) -> Response:
    """HTTP com retry em 401 (re-auth), 429, 5xx. Respeita rate limiter global."""

async def criar_conta_receber(payload) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-receber"""

async def criar_conta_pagar(payload) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-pagar"""

async def listar_parcelas_evento(evento_id) -> list:
    """GET /v1/financeiro/eventos-financeiros/{id}/parcelas"""

async def buscar_parcelas_pagar(descricao, data_venc_de, data_venc_ate) -> list:
    """GET .../contas-a-pagar/buscar (filtro por descricao + datas)"""

async def buscar_parcelas_abertas_pagar(conta_id, data_de, data_ate, pagina, tamanho) -> tuple[list, int]:
    """GET .../contas-a-pagar/buscar (filtro por conta financeira + status aberto)"""

async def buscar_parcelas_abertas_receber(conta_id, data_de, data_ate, pagina, tamanho) -> tuple[list, int]:
    """GET .../contas-a-receber/buscar (filtro por conta financeira + status aberto)"""

async def listar_contas_financeiras() -> list:
    """GET /v1/conta-financeira — todas as contas (paginado)"""

async def listar_centros_custo() -> list:
    """GET /v1/centro-de-custo — todos os centros (paginado)"""

async def criar_baixa(parcela_id, data_pagamento, valor, conta_financeira) -> dict:
    """POST /v1/.../parcelas/{id}/baixa"""
```

### app/services/ml_api.py — Cliente ML/MP

```python
class MLAuthError(Exception):
    """Raised when ML tokens are invalid/revoked. Caller should prompt re-authentication."""

async def _get_token(seller_slug) -> str:
    """Token ML do seller. Auto-refresh se expirado.
    Raises MLAuthError if tokens missing or revoked (auto-clears invalid tokens from DB)."""

async def get_payment(seller_slug, payment_id) -> dict:
    """GET /v1/payments/{id}"""

async def get_order(seller_slug, order_id) -> dict:
    """GET /orders/{id}"""

async def get_shipment_costs(seller_slug, shipment_id) -> dict:
    """GET /shipments/{id}/costs"""

async def search_payments(seller_slug, begin_date, end_date, offset=0, limit=50, range_field="date_approved") -> dict:
    """GET /v1/payments/search — busca por periodo (date_approved/date_last_updated/money_release_date)."""

async def fetch_user_info(access_token) -> dict:
    """GET /users/me — perfil ML"""

async def exchange_code(code) -> dict:
    """POST /oauth/token — troca authorization_code por tokens"""

async def fetch_paid_orders(seller_slug, date_str) -> dict:
    """Busca orders pagos no dia → {valor, order_count, fraud_skipped}"""

async def get_release_report_config(seller_slug) -> dict:
    """GET /v1/account/release_report/config"""

async def configure_release_report(seller_slug) -> dict:
    """PUT /v1/account/release_report/config - configura colunas com fee breakdown."""
```

### app/services/ca_queue.py — Fila Persistente + Worker

```python
async def enqueue(seller_slug, job_type, ca_endpoint, ca_payload,
                  idempotency_key, group_id, priority, ca_method, scheduled_for) -> dict:
    """Insert job em ca_jobs. Retorna existente em conflito de idempotencia."""

# Wrappers (1:1 com call sites do processor):
async def enqueue_receita(seller_slug, payment_id, payload) -> dict      # priority=10
async def enqueue_comissao(seller_slug, payment_id, payload) -> dict     # priority=20
async def enqueue_frete(seller_slug, payment_id, payload) -> dict        # priority=20
async def enqueue_partial_refund(seller_slug, payment_id, index, payload) -> dict
async def enqueue_estorno(seller_slug, payment_id, payload) -> dict
async def enqueue_estorno_taxa(seller_slug, payment_id, payload) -> dict
async def enqueue_baixa(seller_slug, parcela_id, payload, scheduled_for) -> dict  # priority=30

class CaWorker:
    """Background loop: poll → claim atomico → execute → retry/dead."""
    async def start()               # Inicia loop + recover stuck jobs
    async def stop()                # Para gracefully
    async def _poll_next_job()      # Busca + claim atomico do proximo job
    async def _execute_job(job)     # POST/GET no CA, trata response
    def _mark_retryable(db, job, error, now)  # Backoff: 30s, 120s, 480s → dead
    async def _check_group_completion(group_id)  # Marca payment synced quando grupo completa
```

### app/services/daily_sync.py — Daily Sync

```python
async def _daily_sync_scheduler():
    """Async loop, roda as 00:01 BRT. Covers D-1 to D-3."""

async def sync_all_sellers(lookback_days=3) -> list[dict]:
    """Sync todos os sellers ativos. Retorna lista de resultados."""

async def sync_seller_payments(seller_slug, begin_date, end_date) -> dict:
    """Sync payments de um seller. Busca por date_approved + date_last_updated.
    Orders → processor, non-orders → classifier.
    Retorna {orders_processed, expenses_classified, skipped, errors}."""

def _compute_sync_window(cursor, lookback_days, seller_slug) -> tuple:
    """Calcula janela de sync baseada no cursor persistido."""

def _load_sync_cursor(db, seller_slug) -> dict | None:
    """Carrega cursor de sync do sync_state."""

def _persist_sync_cursor(db, seller_slug, cursor_data):
    """Persiste cursor de sync no sync_state."""
```

### app/services/expense_classifier.py — Classificador Non-Order

```python
AUTO_RULES = [...]  # Lista extensivel de regras de auto-categorizacao

def _extract_branch(payment) -> str:
    """Extrai point_of_interaction.business_info.branch."""

def _extract_unit(payment) -> str:
    """Extrai point_of_interaction.business_info.unit."""

def _extract_febraban(payment) -> str | None:
    """Extrai codigo Febraban dos references."""

def _match_rule(rule, payment, branch) -> bool:
    """Testa se uma auto-rule faz match."""

def _classify(payment) -> tuple[expense_type, direction, category, auto, description]:
    """Arvore de decisao: partition→skip, cashback→income, bill→expense, etc."""

async def classify_non_order_payment(db, seller_slug, payment) -> dict | None:
    """Classifica e salva em mp_expenses. Retorna None se skip (partition/addition)."""
```

### app/services/financial_closing.py — Fechamento Financeiro

```python
async def compute_seller_financial_closing(seller_slug, date_from, date_to) -> dict:
    """Computa fechamento financeiro de um seller (auto + manual lanes)."""

async def run_financial_closing_for_all(date_from, date_to) -> list[dict]:
    """Roda fechamento para todos os sellers ativos."""

def get_last_financial_closing() -> dict:
    """Retorna resultado do ultimo fechamento."""

def _compute_auto_lane(db, seller_slug, date_from, date_to) -> dict:
    """Lane automatica: payments + ca_jobs status."""

def _compute_manual_lane(db, seller_slug, date_from, date_to) -> dict:
    """Lane manual: mp_expenses + expense_batches status."""
```

### app/services/legacy_daily_export.py — Export Legado

```python
async def run_legacy_daily_for_seller(seller_slug, target_day, upload) -> dict:
    """Baixa account_statement, roda reconciliacao legada, gera ZIP, faz upload."""

async def run_legacy_daily_for_all(target_day, upload) -> list[dict]:
    """Roda export legado para todos os sellers ativos."""

def get_legacy_daily_status(seller_slug=None) -> dict:
    """Retorna status dos ultimos exports legados."""

async def _legacy_daily_scheduler():
    """Scheduler do export legado. Hora configuravel via env."""
```

### app/services/legacy_bridge.py — Bridge Legado

```python
async def run_legacy_reconciliation(extrato, dinheiro, vendas, pos_venda, liberacoes, centro_custo) -> dict:
    """Roda reconciliacao usando motor legado. Retorna resultado + erros."""

def build_legacy_expenses_zip(resultado) -> tuple[io.BytesIO, dict]:
    """Monta ZIP com PAGAMENTO_CONTAS.xlsx + TRANSFERENCIAS.xlsx a partir do resultado."""
```

### app/services/legacy_engine.py — Motor Legado

```python
def processar_conciliacao(arquivos, centro_custo="NETAIR") -> dict:
    """Motor de reconciliacao legado completo. ~1500 linhas. Processa CSVs do ML/MP."""

def gerar_xlsx_completo(rows, output_path) -> bool:
    """Gera XLSX com formatacao para importar no CA."""

def gerar_xlsx_resumo(rows, output_path) -> bool:
    """Gera XLSX de resumo."""
```

### app/services/release_report_sync.py — Sync Release Report

```python
async def sync_release_report(seller_slug, begin_date, end_date) -> dict:
    """Baixa release_report do ML e synca linhas para mp_expenses."""

async def sync_release_report_all_sellers(lookback_days=3) -> list[dict]:
    """Sync release report para todos sellers ativos (nightly pipeline)."""

async def backfill_release_report(seller_slug, begin_date, end_date) -> dict:
    """Backfill release report: baixa TODOS os reports que cobrem o periodo,
    seleciona cobertura minima e processa (usado pelo onboarding_backfill)."""

def _classify_payout(row, same_day_payouts) -> tuple[str, str, str]:
    """Classifica linha de payout do release report."""

def _classify_credit(row) -> tuple[str, str, str, str | None]:
    """Classifica linha de credito do release report."""
```

### app/services/release_report_validator.py — Validacao de Fees

```python
async def validate_release_fees_for_seller(seller_slug, begin_date, end_date) -> dict:
    """Compara processor_fee/shipping com MP_FEE/SHIPPING do release report.
    Cria ajustes CA (contas-a-pagar) para diferencas."""

async def validate_release_fees_all_sellers(lookback_days=3) -> list[dict]:
    """Valida fees para todos os sellers ativos (D-1 a D-{lookback_days})."""

def get_last_validation_result() -> dict:
    """Retorna resultado da ultima validacao."""

def _parse_release_report_with_fees(csv_bytes) -> list[dict]:
    """Parse CSV do release report com colunas de fee breakdown."""
```

### app/services/extrato_ingester.py — Ingestao de Lacunas do Extrato

```python
async def ingest_extrato_for_seller(seller_slug, begin_date, end_date) -> dict:
    """Ingere linhas do account_statement nao cobertas por payments/mp_expenses.
    Upsert em mp_expenses com source="extrato" e payment_id composto."""

async def ingest_extrato_all_sellers(lookback_days=3) -> list[dict]:
    """Ingestao para todos os sellers ativos (D-1 a D-{lookback_days})."""

def get_last_ingestion_result() -> dict:
    """Retorna resultado da ultima ingestao."""
```

### app/services/extrato_coverage_checker.py — Cobertura do Extrato

```python
async def check_extrato_coverage(seller_slug, begin_date, end_date) -> dict:
    """Verifica que TODAS as linhas do extrato sao cobertas por payments, mp_expenses ou legacy.
    Retorna {total_lines, covered_by_api, covered_by_expenses, uncovered, coverage_pct}."""

async def check_extrato_coverage_all_sellers(lookback_days=3) -> list[dict]:
    """Coverage check para todos os sellers ativos."""

def get_last_coverage_result() -> dict:
    """Retorna resultado do ultimo coverage check."""
```

### app/services/onboarding_backfill.py — Backfill de Ativacao (Onboarding V2)

```python
async def run_onboarding_backfill(seller_slug: str) -> None:
    """Backfill historico por money_release_date (ca_start_date -> ontem).
    Inclui: payments API + release report (payouts, cashback, shipping) + baixas."""

async def retry_backfill(seller_slug: str) -> None:
    """Re-dispara backfill com retomada idempotente."""

def get_backfill_status(seller_slug: str) -> dict:
    """Retorna ca_backfill_status/started/completed/progress."""
```

### app/services/faturamento_sync.py — Sync Periodico

```python
class FaturamentoSyncer:
    """Polls ML paid orders a cada N minutos e upsert em faturamento."""
    async def start()                        # Inicia scheduler
    async def stop()                         # Para
    async def sync_all() -> list[dict]       # Sync todos os sellers ativos
    def _get_syncable_sellers() -> list      # Sellers com dashboard_empresa + ML tokens
    def _upsert_faturamento(empresa, date, valor) -> bool  # Upsert Supabase
```

### app/services/release_checker.py — Verificacao de Liberacao

```python
class ReleaseChecker:
    """Verifica money_release_status do ML antes de baixas."""
    async def check_parcelas_batch(parcelas) -> dict[str, str]:
        """Retorna {parcela_id: "released"|"pending"|"unknown"|"bypass"}"""
    async def _preload(payment_ids, order_ids):
        """Bulk-load do Supabase (raw_payment cache)"""
    async def _recheck_ml_api(payment_ids) -> dict[int, str]:
        """Re-fetch do ML API para payments com release_date passada"""
```

### app/services/rate_limiter.py

```python
class TokenBucket:
    """9 req/s burst, 540 req/min guard. Singleton: rate_limiter."""
    async def acquire()  # Aguarda token disponivel
```

### app/services/onboarding.py

```python
async def create_signup(slug, name, email=None) -> dict:
    """Cria seller com pending_approval."""
async def approve_seller(seller_id, config) -> dict:
    """Aprova seller + cria revenue_line + 12 goals vazias."""
async def reject_seller(seller_id) -> dict:
    """Rejeita seller (suspended)."""
async def activate_seller(slug):
    """Marca seller como active (pos-OAuth ML)."""
```

### app/models/sellers.py

```python
CA_CATEGORIES = {
    "venda_ml":           "78f42170-...",  # 1.1.1 MercadoLibre
    "comissao_ml":        "699d6072-...",  # 2.8.2 Comissoes Marketplace
    "frete_mercadoenvios":"6ccbf8ed-...",  # 2.9.4 MercadoEnvios
    "frete_full":         "27c8de66-...",  # 2.9.10 Frete Full
    "devolucao":          "713ee216-...",  # 1.2.1 Devolucoes
    "estorno_taxa":       "c4cc890c-...",  # 1.3.4 Estornos de Taxas
    "estorno_frete":      "2c0ef767-...",  # 1.3.7 Estorno de Frete
    "antecipacao":        "7e9efb50-...",  # 2.11.9 Antecipacao
    "tarifa_pagamento":   "d77aa9d6-...",  # 2.11.8 Tarifas
}
CA_CONTATO_ML = "b247cccb-38a2-4851-bf0e-700c53036c2c"  # Contato "MERCADO LIVRE"

def get_seller_config(db, seller_slug) -> dict | None
def get_seller_by_ml_user_id(db, ml_user_id) -> dict | None
def get_all_active_sellers(db) -> list[dict]
```
