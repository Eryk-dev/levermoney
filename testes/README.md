# Testes e Simulacoes — Lever Money V3

> Guia de uso dos scripts de teste. Todos sao **read-only** — nao gravam no Conta Azul nem alteram o Supabase.

---

## Pre-requisitos

```bash
# 1. Ativar virtualenv
cd "/Volumes/SSD Eryk/financeiro v2/lever money claude v3"
source venv/bin/activate

# 2. Verificar .env carregado (SUPABASE_URL, SUPABASE_KEY, ML_APP_ID, ML_SECRET_KEY)
cat .env | head -5

# 3. Garantir que a migration foi aplicada
# (ja foi aplicada via MCP Supabase em 2026-02-19)
```

---

## 1. test_extrato_ingester.py

**O que testa:** Parsing do extrato CSV, classificacao dos 35 tipos de transacao, idempotencia de chaves compostas, cobertura 100% em todos os 4 sellers.

**Quando usar:** Apos qualquer alteracao em `app/services/extrato_ingester.py` ou nas regras de classificacao.

```bash
python3 testes/test_extrato_ingester.py
```

**Resultado esperado:** 15 testes, 15 PASS, 0 FAIL.

**Testes mais importantes:**
- `coverage_after_classification_141air` — verifica 0 linhas desconhecidas no extrato da 141AIR
- `coverage_all_sellers` — verifica 0 linhas desconhecidas nos 4 sellers
- `dispute_group` — verifica que disputas com mesmo REFERENCE_ID geram entradas separadas
- `brazilian_number_parsing` — verifica conversao "1.234,56" → 1234.56

**Dependencias:** Extratos CSV em `testes/extratos/`

---

## 2. test_onboarding_backfill.py

**O que testa:** Logica do backfill de onboarding V2 — validacao de config, busca por money_release_date, filtros de skip, idempotencia, progresso.

**Quando usar:** Apos qualquer alteracao em `app/services/onboarding_backfill.py` ou no processor.

```bash
python3 testes/test_onboarding_backfill.py
```

**Resultado esperado:** 11 testes — 9 PASS, 2 SKIP (requerem migration aplicada).

**Testes mais importantes:**
- `search_by_money_release_date` — confirma que a API do ML retorna payments por money_release_date (usa API real)
- `already_done_filtering_live` — carrega payments ja processados do Supabase (usa DB real)
- `payment_classification` — verifica arvore de decisao: order vs non-order vs skip
- `date_validation` — verifica que ca_start_date deve ser dia 1 do mes

**Dependencias:** Acesso ao Supabase + ML API (seller 141air)

**Nota:** Os 2 testes "SKIP" (`seller_config_from_db`, `backfill_status_read_live`) passam apos a migration `004_onboarding_v2.sql` ser aplicada.

---

## 3. test_admin_endpoints.py

**O que testa:** Validacao dos novos endpoints admin (onboarding + extrato), formatos de request/response, tratamento de erros.

**Quando usar:** Apos alterar endpoints em `app/routers/admin.py`.

```bash
# Modo basico (sem API rodando — testa validacao offline):
python3 testes/test_admin_endpoints.py

# Modo completo (com API rodando + admin autenticado):
ADMIN_PASSWORD=suasenha python3 testes/test_admin_endpoints.py
```

**Resultado esperado:**
- Sem API: 7 PASS, 11 SKIP
- Com API: 18 PASS, 0 SKIP

**Testes mais importantes:**
- `ca_start_date_validation_logic` — verifica parsing e validacao de datas
- `activate_required_fields_for_dashboard_ca` — verifica campos obrigatorios do modo CA
- `integration_mode_enum_validation` — verifica valores validos/invalidos
- `unauthorized_access` — verifica que endpoint rejeita token invalido

**Para rodar com API:**
```bash
# Terminal 1: subir API
uvicorn app.main:app --reload --port 8000

# Terminal 2: rodar testes
ADMIN_PASSWORD=suasenha python3 testes/test_admin_endpoints.py
```

---

## 4. simulate_onboarding_141air_jan2026.py

**O que faz:** Simulacao end-to-end do onboarding da 141AIR com ca_start_date=2026-01-01. Compara o resultado contra o extrato real e prova cobertura 100%.

**Quando usar:** Antes de ativar qualquer seller no modo CA, para validar que o sistema cobre 100% do extrato.

```bash
python3 testes/simulate_onboarding_141air_jan2026.py
```

**Resultado esperado:**
```
COVERAGE: 100.0% (690/690 linhas)
Diferenca: R$ 0,00
VEREDICTO: APROVADO
```

**O que o script faz (5 fases):**
1. Parseia o extrato real (`extratos/extrato janeiro 141Air.csv`)
2. Simula o backfill por money_release_date (API ML real, read-only)
3. Simula o extrato ingester (classifica gaps)
4. Reconcilia diariamente (extrato vs sistema)
5. Imprime relatorio com veredicto APROVADO/REPROVADO

**Dependencias:** Extrato CSV + Supabase + ML API

**Tempo de execucao:** ~2-3 minutos (pagina todos os payments da API)

---

## 5. simulate_dre_141air_jan2026.py

**O que faz:** Gera o DRE (Demonstracao do Resultado do Exercicio) como ficaria no Conta Azul, usando regime de competencia (date_approved) em vez de caixa (money_release_date).

**Quando usar:** Para visualizar o impacto financeiro antes de ativar um seller, ou para validar que categorias CA estao corretas.

```bash
python3 testes/simulate_dre_141air_jan2026.py
```

**Resultado esperado:** DRE formatado com:
- Receitas por categoria (1.1.1, 1.2.1, 1.3.4)
- Despesas por categoria (2.8.2, 2.9.4, 2.2.7, 2.6.x)
- Resultado do periodo
- Analise cross-month (vendas DEZ→caixa JAN, vendas JAN→caixa FEV)

**Dependencias:** Cache em `cache_jan2026/` + Extrato CSV + Supabase + ML API

**Tempo de execucao:** ~1-2 minutos (usa cache quando disponivel)

---

## Estrutura do diretorio

```
testes/
├── README.md                              ← este arquivo
├── cache_jan2026/                         ← cache de payments (JSON, gerado automaticamente)
│   ├── 141air_payments.json
│   ├── easy-utilidades_payments.json
│   ├── net-air_payments.json
│   └── netparts-sp_payments.json
├── extratos/                              ← extratos reais do MP (CSV, dados de janeiro 2026)
│   ├── extrato janeiro 141Air.csv
│   ├── extrato janeiro Easyutilidades.csv
│   ├── extrato janeiro netair.csv
│   └── extrato janeiro netparts.csv
├── test_extrato_ingester.py               ← testes unitarios: classificacao de extrato
├── test_onboarding_backfill.py            ← testes unitarios: backfill de onboarding
├── test_admin_endpoints.py                ← testes de API: endpoints admin
├── simulate_onboarding_141air_jan2026.py  ← simulacao: onboarding + cobertura 100%
└── simulate_dre_141air_jan2026.py         ← simulacao: DRE por competencia
```

---

## Ordem recomendada para validacao completa

```
1. python3 testes/test_extrato_ingester.py          # Classificacao OK?
2. python3 testes/test_onboarding_backfill.py        # Backfill OK?
3. python3 testes/simulate_onboarding_141air_jan2026.py  # 100% cobertura?
4. python3 testes/simulate_dre_141air_jan2026.py     # DRE faz sentido?
5. ADMIN_PASSWORD=xxx python3 testes/test_admin_endpoints.py  # Endpoints OK?
```

Se todos passarem, o sistema esta pronto para ativar sellers no modo CA.
