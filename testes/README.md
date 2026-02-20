# Manual de Testes — Lever Money V3

> Todos os scripts são **read-only** — não gravam no Conta Azul nem alteram o Supabase.
> Usam cache local de payments e extratos CSV reais de janeiro/2026.

---

## Estrutura

```
testes/
├── README.md                              ← este arquivo
├── cache_jan2026/                         ← cache de payments do ML (JSON)
│   ├── 141air_payments.json
│   ├── easy-utilidades_payments.json
│   ├── net-air_payments.json
│   └── netparts-sp_payments.json
├── extratos/                              ← extratos reais do MP (CSV, jan/2026)
│   ├── extrato janeiro 141Air.csv
│   ├── extrato janeiro Easyutilidades.csv
│   ├── extrato janeiro netair.csv
│   └── extrato janeiro netparts.csv
├── rebuild_cache.py                       ← (re)constroi o cache local via ML API
├── test_onboarding_backfill.py            ← testes unitarios do backfill
├── simulate_onboarding_141air_jan2026.py  ← simulacao: cobertura do onboarding
├── simulate_dre_141air_jan2026.py         ← DRE por competencia (date_approved)
└── simulate_caixa_jan2026.py             ← DRE por vencimento (money_release_date)
```

---

## Pre-requisitos

```bash
# Ativar virtualenv e verificar .env
cd "/Volumes/SSD Eryk/financeiro v2/lever money claude v3"

# Garantir que o cache existe (ja gerado; so recriar se necessario)
ls testes/cache_jan2026/
# Deve listar: 141air_payments.json, easy-utilidades_payments.json, ...
```

---

## 0. rebuild_cache.py — Reconstruir o Cache Local

**O que faz:** Busca payments diretamente na ML API e salva em JSON local.
Usa 4 campos de data (`date_created`, `date_approved`, `date_last_updated`, `money_release_date`)
para garantir cobertura máxima. Necessário apenas quando o cache estiver desatualizado.

**Quando usar:** Antes de rodar os scripts de simulação pela primeira vez,
ou quando precisar de dados mais recentes do ML.

```bash
# Um seller especifico
python3 testes/rebuild_cache.py --seller 141air

# Com periodo customizado
python3 testes/rebuild_cache.py --seller net-air --begin 2026-01-01 --end 2026-01-31

# Todos os sellers (demora ~30-40 min por conta da paginacao da ML API)
python3 testes/rebuild_cache.py --all
```

**Sellers disponíveis:** `141air`, `net-air`, `netparts-sp`, `easy-utilidades`

**Output:** arquivos JSON em `testes/cache_jan2026/`:
```
141air_payments.json     — ex: 879 payments únicos
net-air_payments.json    — ex: 12.374 payments únicos
netparts-sp_payments.json
easy-utilidades_payments.json
```

> **Fuso horário:** Usa `-03:00` (BRT), alinhado com `onboarding_backfill.py` e `daily_sync.py`.

---

## 1. test_onboarding_backfill.py — Testes Unitários do Backfill

**O que testa:** Lógica do backfill de onboarding V2 — validação de config,
busca por `money_release_date`, filtros de skip, idempotência, janela dinâmica de datas.

**Dependências:** Acesso ao Supabase + ML API (seller 141air)

```bash
python3 testes/test_onboarding_backfill.py
```

**Resultado esperado:** 12 testes — **10 PASS, 2 SKIP**

Os 2 SKIP (`seller_config_from_db`, `backfill_status_read_live`) passam após
a migration `004_onboarding_v2.sql` estar aplicada no Supabase.

**Testes mais importantes:**
- `search_by_money_release_date` — confirma que a ML API retorna payments por `money_release_date`
- `payment_classification` — verifica a árvore de decisão: order vs non-order vs skip
- `test_future_release_window` — valida que o `end_date` do backfill é `today+90d` (não ontem),
  garantindo captura de vendas aprovadas em fim de mês com liberação futura

---

## 2. simulate_onboarding_141air_jan2026.py — Simulação de Cobertura

**O que faz:** Simulação end-to-end do onboarding de qualquer seller com `ca_start_date=2026-01-01`.
Compara o resultado do backfill contra o extrato real e prova cobertura 100%.

**Dependências:** Cache JSON em `testes/cache_jan2026/` + Extrato CSV em `testes/extratos/`

```bash
# Um seller
python3 testes/simulate_onboarding_141air_jan2026.py --seller 141air
python3 testes/simulate_onboarding_141air_jan2026.py --seller net-air
python3 testes/simulate_onboarding_141air_jan2026.py --seller netparts-sp
python3 testes/simulate_onboarding_141air_jan2026.py --seller easy-utilidades

# Todos os sellers
python3 testes/simulate_onboarding_141air_jan2026.py --all
```

**Resultado esperado:**
```
  Seller              Coverage      Diff       Status
  141air               100.0%    R$ 0,00     APROVADO
  net-air              100.0%    R$ 0,00     APROVADO
  netparts-sp          100.0%    R$ 0,00     APROVADO
  easy-utilidades      100.0%    R$ 0,00     APROVADO
  RESULTADO GERAL: TODOS APROVADOS
```

**O que o script faz (5 fases):**
1. Parseia o extrato real (mapeado automaticamente pelo seller slug)
2. Simula o backfill por `money_release_date` (cache local, read-only) com janela `ca_start_date → today+90d`
3. Simula o extrato ingester (classifica gaps)
4. Reconcilia diariamente (extrato vs sistema)
5. Imprime relatório com veredicto APROVADO/REPROVADO

---

## 3. simulate_dre_141air_jan2026.py — DRE por Competência

**O que faz:** Gera o DRE (Demonstração do Resultado do Exercício) usando
**regime de competência** — receita reconhecida em `date_approved` (BRT),
independente de quando o dinheiro é liberado.

**Dependências:** Cache JSON + Extrato CSV

```bash
# Um seller
python3 testes/simulate_dre_141air_jan2026.py --seller 141air
python3 testes/simulate_dre_141air_jan2026.py --seller net-air
python3 testes/simulate_dre_141air_jan2026.py --seller netparts-sp
python3 testes/simulate_dre_141air_jan2026.py --seller easy-utilidades

# Todos os sellers
python3 testes/simulate_dre_141air_jan2026.py --all
```

**Resultado de referência — Janeiro 2026 (todos os sellers):**
```
  Seller              Receitas          Despesas        Resultado    Margem
  141air          R$ 154.149,41    R$ -150.241,17      R$  3.908,24   2,5%
  net-air         R$ 867.784,42    R$ -382.251,42    R$ 485.533,00  56,0%
  netparts-sp     R$ 707.787,08    R$ -915.692,18   R$ -207.905,10 -29,4%
  easy-utilidades R$ 153.822,45     R$ -58.339,58     R$ 95.482,87  62,1%
```

**Output inclui:**
- DRE formatado por categoria CA (1.1.1, 1.2.1, 1.3.4, 2.8.2, 2.9.4, etc.)
- Sumário de payments (aprovados, devolvidos, pulados, non-orders)
- Análise cross-month (vendas DEZ→caixa JAN, vendas JAN→caixa FEV)
- Breakdown detalhado por categoria
- Reconciliação competência vs caixa

> **Atenção netparts-sp:** R$ 611.627,44 em 46 payments classificados como `other`
> (non-orders). Revisar antes de importar no Conta Azul.

---

## 4. simulate_caixa_jan2026.py — DRE por Vencimento (Caixa)

**O que faz:** Gera o DRE usando **regime de caixa** — receita reconhecida em
`money_release_date` (quando o dinheiro é efetivamente liberado pelo ML).

Diferença-chave vs competência:
- **Inclui** vendas aprovadas em DEZ/2025 liberadas em JAN/2026 (cruzam o mês)
- **Exclui** vendas aprovadas em JAN/2026 liberadas em FEV/2026+ (ainda não no caixa)

**Dependências:** Cache JSON + Extrato CSV

```bash
# Um seller
python3 testes/simulate_caixa_jan2026.py --seller 141air
python3 testes/simulate_caixa_jan2026.py --seller net-air
python3 testes/simulate_caixa_jan2026.py --seller netparts-sp
python3 testes/simulate_caixa_jan2026.py --seller easy-utilidades

# Todos os sellers
python3 testes/simulate_caixa_jan2026.py --all
```

**Resultado de referência — Janeiro 2026 (todos os sellers):**
```
  Seller              Receitas          Despesas        Resultado    Margem
  141air          R$ 141.209,98    R$ -149.081,89     R$ -7.871,91  -5,6%
  net-air         R$ 718.395,91    R$ -367.301,53    R$ 351.094,38  48,9%
  netparts-sp     R$ 484.404,76    R$ -870.476,94   R$ -386.072,18 -79,7%
  easy-utilidades R$ 136.072,10     R$ -54.500,30     R$ 81.571,80  59,9%
```

**Output inclui:**
- DRE formatado com composição da receita bruta por mês de aprovação
- Bloco `[EXCLUIDOS]` mostrando vendas de jan que só entram no caixa de fev
- Memo com extrato bancário (saldo inicial → final)

---

## Comparativo: Competência vs Caixa

| Critério | Competência | Caixa (Vencimento) |
|----------|------------|-------------------|
| Data base | `date_approved` (BRT) | `money_release_date` |
| Venda aprov. DEZ, lib. JAN | DRE de **dezembro** | DRE de **janeiro** |
| Venda aprov. JAN, lib. FEV | DRE de **janeiro** | DRE de **fevereiro** |
| Alinha com | Painel ML, Nota Fiscal | Extrato bancário |
| Usado para | DRE oficial (accrual) | Fechamento de caixa |

---

## Ordem Recomendada de Execução

```bash
# 1. (Opcional) Rebuildar cache se desatualizado — ~30-40 min
python3 testes/rebuild_cache.py --all

# 2. Testes unitários do backfill — ~2 min (usa ML API e Supabase reais)
python3 testes/test_onboarding_backfill.py

# 3. Validar cobertura 100% do onboarding — ~5s por seller
python3 testes/simulate_onboarding_141air_jan2026.py --all

# 4. DRE por competencia — ~5s por seller
python3 testes/simulate_dre_141air_jan2026.py --all

# 5. DRE por vencimento (caixa) — ~5s por seller
python3 testes/simulate_caixa_jan2026.py --all
```

Se todos passarem, o sistema está pronto para ativar sellers no modo CA.

---

## Notas Importantes

### Cache Local (`cache_jan2026/`)

- Gerado pelo `rebuild_cache.py` via ML API
- Cobre **janeiro e dezembro** de 2025/2026 por 4 critérios de data
- Timezone: `-03:00` (BRT) — alinhado com todo o sistema
- Não expira automaticamente; rebuildar quando necessário

### Por que dois DREs?

O Conta Azul opera em **regime de competência** (`date_approved`).
O `simulate_dre_141air_jan2026.py` replica exatamente o que será lançado no CA.

O `simulate_caixa_jan2026.py` é uma visão complementar que alinha com o
**extrato bancário do MP**, útil para fechamento de caixa diário e conferência
com o financeiro.

### Sellers Disponíveis

| Slug | Nome |
|------|------|
| `141air` | 141AIR |
| `net-air` | NET AIR |
| `netparts-sp` | NETPARTS SP |
| `easy-utilidades` | EASY UTILIDADES |
