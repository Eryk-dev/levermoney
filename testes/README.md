# Testes — LeverMoney Conciliador v1.1.0

> **152 testes automatizados** (pytest) + scripts standalone de simulacao e validacao.
> Todos usam dados reais (cache JSON + extratos CSV). Nenhum grava no CA ou altera Supabase.

---

## Estrutura

```
testes/
├── conftest.py                     # Fixtures pytest + collect_ignore_glob
├── test_processor_unit.py          # 31 testes — matematica do processor
├── test_extrato_classification.py  # 73 testes — classificacao de linhas do extrato
├── test_dre_reconciliation.py      # 48 testes — DRE completo com dados reais
│
├── data/                           # Dados reais (read-only)
│   ├── cache_jan2026/              #   Cache de payments JSON (ML API)
│   ├── cache_fev2026/              #   Cache fevereiro
│   └── extratos/                   #   Extratos CSV reais do MP (4 sellers x 2 meses)
│
├── standalone/                     # Scripts de teste que NAO sao pytest
│   ├── test_extrato_ingester.py    #   9 grupos de testes offline do extrato_ingester
│   ├── test_onboarding_backfill.py #   12 testes do backfill (Supabase + ML API)
│   ├── test_admin_endpoints.py     #   Validacao de endpoints admin (API rodando)
│   └── test_reconciliation_141air.py # Reconciliacao por ref_id contra Supabase
│
├── simulacoes/                     # Simulacoes offline (nao gravam nada)
│   ├── simulate_fresh_backfill_141air.py      # Backfill do zero, 690/690 linhas
│   ├── simulate_onboarding_141air_jan2026.py  # Onboarding multi-seller
│   ├── simulate_dre_141air_jan2026.py         # DRE por competencia
│   ├── simulate_dre.py                        # DRE parametrico
│   ├── simulate_caixa_jan2026.py              # DRE por caixa
│   ├── dre_janeiro_141air.py                  # DRE especifico 141air
│   └── fluxo_caixa_netair_jan2026.py          # Fluxo de caixa netair
│
├── utils/                          # Utilitarios
│   └── rebuild_cache.py            # Reconstroi cache via ML API
│
├── reports/                        # Relatorios gerados
│   └── reconciliation_report_141air_jan2026.txt
│
└── README.md                       # Este arquivo
```

---

## Testes Automatizados (pytest)

Rodam offline, sem Supabase ou API, em ~1.3 segundos.

```bash
cd "/Volumes/SSD Eryk/LeverMoney"

# Todos os testes (152)
python3 -m pytest

# Com output detalhado
python3 -m pytest -v --tb=long

# Apenas um arquivo
python3 -m pytest testes/test_dre_reconciliation.py -v

# Apenas uma classe
python3 -m pytest testes/test_dre_reconciliation.py::TestReceita -v
```

### test_processor_unit.py (31 testes)

Valida a matematica core do `processor.py`:
- `_to_float()` — conversao robusta de tipos
- `_to_brt_date()` — UTC-4 → BRT (UTC-3), crossing de meia-noite
- `_extract_processor_charges()` — fees, shipping, coupons, financing_fee
- `_compute_effective_net_amount()` — net normal e partial refund
- Estorno granular (fee refunded, shipping retained)
- Payload builders: `_build_parcela`, `_build_evento`, `_build_despesa_payload`
- Valores CA end-to-end (competencia, vencimento, categorias)

### test_extrato_classification.py (73 testes)

Valida parsing e classificacao do extrato (account statement):
- `_parse_br_number()` — numeros BR (1.234,56)
- `_normalize_text()` — acentos, cedilha, case
- `_parse_account_statement()` — parsing CSV completo
- `_classify_extrato_line()` — 30+ tipos de transacao
- `_resolve_check_payments()` — smart skip com fallbacks
- Expense builder (status, direction)
- Completude: todo expense_type tem abbreviacao + descricao
- Cobertura real: zero linhas nao classificadas em 8 extratos reais

### test_dre_reconciliation.py (48 testes)

Valida o DRE completo contra dados reais (141air, Janeiro 2026):
- **TestDataLoading** — contagem de payments, filtros, grupos
- **TestSkipFilters** — collector.id, marketplace_shipment, by_admin, competencia
- **TestReceita** — R$179.512,35 ML + R$59,90 MP, CB/reimbursed, refunded
- **TestComissao** — R$23.085,97, financing_fee excluida, coupon logic
- **TestFrete** — R$8.946,37, nunca negativo
- **TestDevolucoes** — R$45.375,41, capped em transaction_amount
- **TestEstornoTaxa** — R$5.948,66, so em refund total
- **TestEstornoFrete** — R$1.442,21
- **TestPerPaymentBalance** — todo payment balanceia (amount - fee - ship = net)
- **TestDREConsistency** — receita liquida R$141.587,71, resultado R$109.555,37
- **TestExtratoLiberacaoMatch** — 289/289 liberacoes match (datas + valores)
- **TestExtratoCoverage** — 690 linhas, gap R$0,00
- **TestCompetenciaDate** — 0 crossings UTC-4→BRT, usa date_approved

---

## Scripts Standalone

NAO sao pytest. Rodar individualmente com `python3 testes/standalone/script.py`.

| Script | Dependencias | O que faz |
|--------|-------------|-----------|
| `test_extrato_ingester.py` | Nada (offline) | 9 grupos de testes do extrato_ingester |
| `test_onboarding_backfill.py` | Supabase + ML API | 12 testes do backfill (10 pass, 2 skip) |
| `test_admin_endpoints.py` | API rodando | Validacao de endpoints admin |
| `test_reconciliation_141air.py` | Supabase | Reconciliacao por ref_id, gera relatorio |

---

## Simulacoes

Simulacoes offline que usam cache JSON + extratos CSV. Nao gravam nada.

```bash
# Backfill do zero
python3 testes/simulacoes/simulate_fresh_backfill_141air.py

# Onboarding multi-seller
python3 testes/simulacoes/simulate_onboarding_141air_jan2026.py --seller 141air

# DRE por competencia
python3 testes/simulacoes/simulate_dre_141air_jan2026.py --seller 141air

# DRE por caixa
python3 testes/simulacoes/simulate_caixa_jan2026.py --seller 141air
```

---

## Utilitarios

```bash
# Rebuildar cache de payments via ML API (~30-40 min para todos)
python3 testes/utils/rebuild_cache.py --seller 141air
python3 testes/utils/rebuild_cache.py --all
```

---

## Dados reais (`data/`)

| Diretorio | Conteudo |
|-----------|----------|
| `cache_jan2026/` | 141air_payments.json (879 payments, ~4.8MB) |
| `cache_fev2026/` | 141air_payments.json (~3.6MB) |
| `extratos/` | 8 CSVs: 4 sellers x 2 meses (jan + fev 2026) |

Sellers disponiveis: `141air`, `net-air`, `netparts-sp`, `easy-utilidades`

---

## DRE de Referencia — 141air Janeiro 2026

Validado por `test_dre_reconciliation.py` (48 testes).

```
RECEITA BRUTA                              R$ 179.572,25
  1.1.1 Vendas ML (mercadolibre)           R$ 179.512,35
  1.1.2 Loja Propria (mercadopago)         R$      59,90
DEDUCOES
  1.2.1 Devolucoes (77)                   (R$  45.375,41)
OUTRAS RECEITAS
  1.3.4 Estornos de Taxas (75)             R$   5.948,66
  1.3.7 Estorno de Frete (54)              R$   1.442,21
RECEITA LIQUIDA                            R$ 141.587,71
DESPESAS VARIAVEIS
  2.8.2 Comissoes Marketplace (435)       (R$  23.085,97)
  2.9.4 Frete MercadoEnvios (362)         (R$   8.946,37)
RESULTADO OPERACIONAL                      R$ 109.555,37
```
