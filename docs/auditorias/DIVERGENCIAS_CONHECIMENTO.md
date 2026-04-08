# Base de Conhecimento — Divergencias Extrato ML vs Sistema

> Documento vivo. Atualizar conforme novas descobertas forem feitas durante correcoes.
> Ultima atualizacao: 2026-03-04 (v2)

---

## 1. Arquitetura do Pipeline (como os dados fluem)

```
Movimentacao no MercadoLivre/MercadoPago
│
├─── API Payments (/v1/payments/search) ──────────────────────┐
│    Retorna: vendas, refunds, chargebacks, assinaturas,      │
│    boletos, PIX, transferencias                              │
│    NAO retorna: DIFAL, faturas ML, cartao credito,           │
│    envio pos-refund, holds                                   │
│                                                              │
│    Processamento:                                            │
│    ├─ daily_sync.py (fetch por date_approved + date_updated) │
│    ├─ Tem order_id? → processor.py → payments + ca_jobs      │
│    └─ Sem order_id? → expense_classifier.py → mp_expenses    │
│                                                              │
├─── Release Report (CSV via /v1/account/release_report) ─────┤
│    Retorna: payouts, cashback, shipping adjustments          │
│    Processamento: release_report_sync.py → mp_expenses       │
│                                                              │
└─── Account Statement (extrato CSV manual) ───────────────────┤
     Retorna: TUDO (fonte real de verdade)                     │
     Processamento: extrato_ingester.py → mp_expenses (gaps)   │
     So ingere o que NAO existe em payments/mp_expenses         │
                                                               │
                         ┌─────────────────────────────────────┘
                         │
                         v
              ┌─── payments (vendas) ──→ ca_jobs (receita, comissao, frete, estorno)
              │                              └──→ baixas.py (busca parcelas no CA, baixa)
              │
              └─── mp_expenses (nao-venda) ──→ export XLSX (manual)
                                                ❌ NAO gera ca_jobs
                                                ❌ NAO gera baixas automaticas
```

**Conclusao arquitetural:** Existe um gap fundamental — `mp_expenses` categorizados nunca viram `ca_jobs`. O pipeline de baixas so processa `payments`.

### Esclarecimento importante: mp_expenses sem ca_category sao INTENCIONAIS

Os `mp_expenses` sem `ca_category` (boletos, PIX, transferencias) **nao sao um bug**.
Esses lancamentos precisam de verificacao manual (categoria, fornecedor, etc.) antes de
serem importados no ContaAzul. O fluxo correto e:

1. Sistema captura a movimentacao em `mp_expenses`
2. Usuario revisa no dashboard e atribui categoria, fornecedor, etc.
3. Exporta XLSX e importa manualmente no CA

**O problema real:** mesmo somando os lancamentos ja baixados automaticamente (via `ca_jobs`)
COM os lancamentos pendentes de importacao manual (`mp_expenses` sem categoria),
o total **ainda nao bate** com o saldo do extrato bancario.

Isso significa que existem divergencias ALEM dos expenses nao importados — ou seja,
ha movimentacoes que o sistema nao capturou de forma alguma, ou valores que estao
sendo registrados incorretamente. Essas sao as divergencias reais que precisam de correcao:

- Movimentacoes invisiveis (secao 2 — nao capturadas pela API nem pelo extrato_ingester)
- Estorno parcial de taxa (secao 4 — valores errados no CA)
- IOF em assinaturas (secao 5 — valores registrados sem IOF)
- Refund parcial nao detectado (secao 6 — lookback insuficiente)
- Vendas nao sincronizadas (secao 7 — 13 vendas dia 26)

---

## 2. O que a API de Payments NAO retorna

Aprendemos que a API `/v1/payments/search` do MercadoLivre tem pontos cegos. Estas movimentacoes SÓ aparecem no Account Statement (extrato):

| Tipo | ID no extrato | Descricao no extrato | Por que a API nao retorna |
|------|---------------|---------------------|--------------------------|
| **DIFAL** | Curto (27xxxxx) | "Debito por divida Diferenca da aliquota" | E cobranca estatal, nao e payment |
| **Faturas ML** | Numerico longo | "Debito por divida Faturas vencidas do ML" | E cobranca administrativa do ML |
| **Cartao credito** | Numerico longo | "Pagamento Cartao de credito" | E transferencia interna ML, filtrado como `money_transfer` |
| **Envio pos-refund** | Mesmo do payment original | "Debito por divida Envio do Mercado Livre" | Cobranca de frete de devolucao, separada do refund |
| **Holds** | Numerico longo | "Dinheiro retido Reclamacoes e devolucoes" | Bloqueio temporario por disputa |

### Como o extrato_ingester.py captura cada tipo

```
Arquivo: app/services/extrato_ingester.py

Padroes de classificacao (linhas ~73-118):
  "diferenca da aliquota"    → expense_type: "difal"              → ca_category: "2.2.3"
  "faturas vencidas"         → expense_type: "faturas_ml"         → ca_category: "2.8.2"
  "envio do mercado livre"   → expense_type: "debito_envio_ml"    → ca_category: "2.9.4"
  "dinheiro retido"          → expense_type: "dinheiro_retido"    → ca_category: NULL (pending_review)
  "pagamento cartao"         → expense_type: NULL                 → SKIPPED! (bug — tratado como interno)
```

**Acao necessaria:** Corrigir `extrato_ingester.py` para nao skippar "pagamento cartao de credito".

---

## 3. mp_expenses — fluxo de importacao manual (by design)

### Contexto
Os `mp_expenses` NAO sao importados automaticamente no ContaAzul por design.
Eles requerem verificacao humana (categoria correta, fornecedor, centro de custo, etc.)
antes de serem importados. O fluxo intencional e:

```
expense capturado pelo sistema
→ mp_expenses (com ou sem ca_category)
→ Usuario revisa no dashboard
→ Exporta XLSX
→ Importa manualmente no ContaAzul
```

Este fluxo manual e esperado e NAO e a causa da divergencia entre extrato e sistema.

### O que E problema: expenses com ca_category que PODERIAM ser automatizados
Para expenses que o `extrato_ingester.py` classifica automaticamente (DIFAL, faturas ML, etc.),
seria possivel criar ca_jobs automaticos. Mas isso e uma decisao de produto — hoje o design
e que TUDO em mp_expenses passa por revisao manual.

### Arquivos envolvidos
- `app/services/expense_classifier.py` — linhas 247-290 (classifica e insere em mp_expenses)
- `app/services/extrato_ingester.py` — classifica gaps do extrato com ca_category
- Export XLSX — destino atual dos mp_expenses para importacao manual no CA

---

## 4. Estorno parcial de taxa — bug no processor.py

### O bug
```python
# processor.py, linhas 499-511
net = payment.get("transaction_details", {}).get("net_received_amount", 0)
total_fees = round(amount - net, 2)  # ← ASSUME QUE TUDO FOI REEMBOLSADO
```

O codigo calcula `total_fees = amount - net` e estorna TUDO (comissao + frete).
Na realidade, o ML pode devolver so a comissao e reter o frete.

### Dados disponiveis (ignorados pelo codigo)
O `raw_payment` contem `charges_details` com informacao granular:
```json
{
  "type": "fee",
  "amounts": {
    "original": 10.50,
    "refunded": 10.50    // ← quanto FOI reembolsado
  }
},
{
  "type": "shipping",
  "amounts": {
    "original": 24.95,
    "refunded": 0.00     // ← ZERO = frete NAO reembolsado
  }
}
```

### Solucao
Iterar `charges_details[].amounts.refunded` e somar apenas o que foi efetivamente reembolsado.

### Evidencia do legacy
O codigo antigo (`legacy/engine.py`, linhas 1196-1216) ja separava `mp_fee` de `shipping_fee` corretamente.

---

## 5. IOF em assinaturas internacionais

### O problema
- API do MercadoPago retorna `transaction_amount` = valor SEM IOF
- Extrato bancario mostra valor COM IOF (6,38%)
- mp_expenses registra valor da API (sem IOF)
- Divergencia: ~6,38% em cada assinatura internacional

### Casos encontrados (Jan/2026)
| Servico | Valor API | Valor extrato | IOF |
|---------|-----------|--------------|-----|
| Supabase | 163,31 | 169,03 | 5,72 |
| Claude.ai | 550,00 | 569,25 | 19,25 |
| Notion | 127,48 | 131,94 | 4,46 |

### Fix parcial existente
`release_report_sync.py` tem funcao `_update_existing_expense_amount()` (linha 203) que corrige o valor usando o release report. Mas so funciona se a assinatura aparece no release report.

---

## 6. Refund parcial nao detectado (lookback limitado)

### O problema
`daily_sync.py` busca payments atualizados com lookback de **3 dias** (padrao).
Se um refund parcial ocorre 4+ dias apos a venda, o sistema nao re-fetcha o payment.

### Como funciona o re-sync
```python
# daily_sync.py, linhas 267-280
by_updated = await _fetch_payments_by_range(
    seller_slug, begin, end_dt, range_field="date_last_updated"
)
# begin = hoje - lookback_days (default=3)
```

Se `date_last_updated` do payment mudou para refund apos o lookback, nao e capturado.

### Deteccao de mudanca (funciona se re-fetchar)
```python
# daily_sync.py, linhas 344-352
if existing.get("ml_status") != status:
    should_reprocess = True  # Detecta approved → refunded
```

### Solucao
Aumentar `lookback_days` ou criar re-sync periodico (semanal) com lookback maior.

---

## 7. Vendas nao sincronizadas (13 vendas dia 26)

### Filtros em daily_sync.py que podem descartar vendas
```python
# daily_sync.py, linhas 358-367
if payment.get("description") == "marketplace_shipment":
    skipped += 1  # Ajuste de frete, nao venda real
if (payment.get("collector") or {}).get("id") is not None:
    skipped += 1  # Terceiro / comissario
if status not in ("approved", "refunded", "in_mediation", "charged_back"):
    skipped += 1  # Status nao processavel
```

### CAUSA RAIZ IDENTIFICADA (2026-03-04)

A API de busca do ML (`/v1/payments/search`) **silenciosamente dropou** 13 payments dos resultados.

**O que aconteceu no dia 26:**
- Foi um dia atipico: ~135 payments liberados de uma vez (batch release)
- 54 payments com `money_release_date` = `2026-01-26T16:06:42.000-04:00`
- 57 payments com `money_release_date` = `2026-01-26T16:06:43.000-04:00`
- Quando muitos payments compartilham o mesmo timestamp, a API silenciosamente omite alguns
- O `paging.total` retornado pela API NAO reflete a omissao

**Por que o extrato_ingester nao pegou:**
O `extrato_ingester.py` (linha 77) **skipa** "Liberacao de dinheiro" por design,
assumindo que a API de Payments ja cobriu. Nao ha safety net.

```python
("liberacao de dinheiro",  None, None, None),  # ← SKIP
("pagamento com",          None, None, None),  # ← SKIP
```

### Os 13 payments perdidos (R$ 3.824,82 total)

| # | REFERENCE_ID | Tipo | Valor |
|---|-------------|------|-------|
| 1 | 141043812466 | Liberacao | +734,76 |
| 2 | 141183074293 | Liberacao | +861,77 |
| 3 | 141251658525 | Liberacao | +63,68 |
| 4 | 141359034751 | Liberacao | +38,92 |
| 5 | 141385949804 | Liberacao | +405,92 |
| 6 | 141470360279 | Liberacao | +102,75 |
| 7 | 141587118535 | QR Pix | +63,68 |
| 8 | 141922182246 | Liberacao | +19,96 |
| 9 | 141996119325 | Liberacao | +383,81 |
| 10 | 142110483725 | QR Pix | +203,89 |
| 11 | 142292552528 | Liberacao | +88,74 |
| 12 | 142339588114 | QR Pix | +781,00 |
| 13 | 142406081170 | QR Pix | +75,94 |

### Solucao

**Curto prazo:** Re-ingerir os 13 payments via `GET /v1/payments/{id}` (endpoint individual,
sempre retorna o payment se ele existe) e processar via `process_payment_webhook()`.

**Medio prazo:** Modificar `extrato_ingester.py` para que, antes de skippar "Liberacao de dinheiro",
verifique se o REFERENCE_ID existe em `payments`. Se NAO existir:
1. Buscar via `GET /v1/payments/{id}`
2. Processar pelo pipeline normal
3. Logar warning sobre gap da API de busca

**Longo prazo:** Apos backfill, adicionar step de verificacao cruzada:
extrato vs payments, buscando individualmente qualquer ID faltante.

---

## 8. PRINCIPAL DIVERGENCIA: Fluxo de claims/devolucoes (73,9% do gap)

### Descoberta (2026-03-04)

A analise de gap de janeiro 2026 revelou que **73,9% da divergencia** (R$ 22.868,32 de R$ 30.941,55)
vem de uma diferenca de MODELAGEM entre o extrato e o sistema.

### Como o extrato registra um refund
Para cada pagamento com reclamacao/devolucao, o extrato mostra 3 linhas:
```
Debito por divida Reclamacoes no ML    REF_ID  -VALOR_TOTAL   (debito)
Liberacao de dinheiro                  REF_ID  +NET_AMOUNT    (credito)
Reembolso Envio cancelado              REF_ID  +REFUND        (credito)
```
Resultado no extrato: **net = 0** (ou proximo de zero).

### Como o sistema registra
A tabela `payments` registra apenas `net_amount` como valor positivo.
O payment existe com `ml_status = 'refunded'`, mas `net_amount` ainda
mostra o valor que SERIA liberado se nao tivesse sido refundado.

### Consequencia
O sistema **superestima a receita** em R$ 22.868 no mes porque inclui
o `net_amount` de payments refundados como se fosse receita efetivamente recebida.

### 64 payments afetados em janeiro
Os maiores:
| ID | Extrato Net | Sistema Net | Diferenca |
|---|---:|---:|---:|
| 139749344683 | 0,00 | 4.318,05 | -4.318,05 |
| 140422465618 | 0,00 | 2.318,05 | -2.318,05 |
| 140422485450 | 0,00 | 2.000,00 | -2.000,00 |
| 140563976561 | 0,00 | 1.133,78 | -1.133,78 |
| 140132250075 | 0,00 | 729,88 | -729,88 |

### 4 payments refundados sem correspondencia no extrato (25,8% do gap)
| ID | Valor Sistema | Observacao |
|---|---:|---|
| 143762867120 | 4.121,55 | Refundado, nunca liberado no extrato |
| 143815855230 | 4.121,55 | Refundado, nunca liberado no extrato |
| 140688038213 | 313,23 | Refundado |
| 143909170600 | 31,60 | Refundado |

### Solucao
1. **Payments refundados nao devem contar como receita liberada** — filtrar por `ml_status`
2. O `net_amount` de um payment refundado deveria ser zerado ou o calculo de receita
   do dia deveria excluir payments com `ml_status = 'refunded'`
3. O pipeline de baixas ja trata estornos, mas o calculo de saldo diario precisa ser ajustado

### Referencia
Analise completa em `docs/GAP_ANALYSIS_JAN2026.md`.

---

## 9. Transferencias PIX e Boletos — importacao manual (by design)

### Contexto
Transferencias PIX (saques), depositos e boletos sao movimentacoes que requerem
verificacao manual antes de importacao no CA (categoria, fornecedor, centro de custo, etc.).

### Status atual
- PIX enviados (transfer_pix): capturados em mp_expenses, sem ca_category → aguardam revisao
- PIX recebidos (deposit): capturados em mp_expenses, sem ca_category → aguardam revisao
- Boletos (bill_payment): capturados em mp_expenses, sem ca_category → aguardam revisao

### Fluxo esperado
1. Sistema captura em mp_expenses (OK — ja funciona)
2. Usuario revisa no dashboard e classifica
3. Exporta XLSX e importa manualmente no CA

### Nota
Esses lancamentos NAO sao a causa da divergencia investigada. Eles sao contabilizados
como "pendentes de importacao" e, quando somados as baixas automaticas, o total DEVERIA
bater com o extrato. O fato de NAO bater e o que motiva a investigacao das demais
divergencias (secoes 2, 4, 5, 6, 7).

---

## 10. Registro de correcoes aplicadas

| Data | Tipo corrigido | Descricao | Arquivos alterados | Resultado |
|------|---------------|-----------|-------------------|-----------|
| 2026-03-04 | Estorno parcial de taxa | processor.py agora usa charges_details[].amounts.refunded em vez de amount-net | processor.py, ca_queue.py | Separa estorno_taxa e estorno_frete corretamente |
| 2026-03-04 | Pagamento cartao credito | extrato_ingester.py nao mais skipa "pagamento cartao de credito" | extrato_ingester.py | Captura como pagamento_cartao_credito (pending_review) |
| 2026-03-05 | Smart skip (safety net) | Liberacoes/QR/Dinheiro recebido agora verificam se ref_id existe em payments antes de skippar. Se nao existe, ingere como liberacao_nao_sync / qr_pix_nao_sync / dinheiro_recebido | extrato_ingester.py | Cobre os 13 payments perdidos do dia 26 e qualquer futuro gap da API |
| 2026-03-05 | IOF subscriptions | Extrato ingester agora atualiza valor de mp_expenses existentes com o valor real do extrato (pos-IOF) | extrato_ingester.py | Fecha gap de IOF (Supabase, Claude, Notion) |
| 2026-03-05 | Faturas ML dedup | Fuzzy match por valor+data+tipo evita duplicatas quando extrato usa ID interno ML diferente do payment_id | extrato_ingester.py | Previne duplicacao de faturas ML |
| 2026-03-05 | Envio ML debt always ingest | debito_envio_ml, bonus_envio, debito_troca agora sao ingeridos MESMO quando ref_id ja existe em payments | extrato_ingester.py | Captura cobracas de envio separadas pos-refund |
| 2026-03-05 | Bonus envio pattern | Adicionado pattern "bonificacao" como alias de bonus_envio | extrato_ingester.py | Captura bonus de envio do ML |

---

## 11. Resumo do Gap (Janeiro 2026, 141air)

### V1 (payments.net_amount vs extrato) — SUPERADO
Gap: R$ 30.941,55 — era 90,5% diferenca de modelagem, nao divergencia real.

### V2 (ca_jobs + mp_expenses vs extrato por reference_id) — ATUAL
Gap real: R$ 2.949,49 antes dos fixes.

### Apos fixes (2026-03-05)
| Componente | Antes | Apos fix | Fix aplicado |
|---|---:|---:|---|
| Smart skip (13 payments dia 26 + outros) | +3.895,61 | 0,00 | _CHECK_PAYMENTS |
| Cartao de credito | -3.010,62 | 0,00 | pagamento_cartao_credito |
| Faturas ML | -612,97 | 0,00 | fuzzy match dedup |
| Holds/retencoes | -436,58 | 0,00 | dinheiro_retido (ja existia) |
| PIX recebida | +349,07 | 0,00 | dinheiro_recebido via smart skip |
| DIFAL | -77,64 | 0,00 | difal (ja existia, agora roda) |
| Envio ML debt | -70,35 | 0,00 | always ingest debito_envio_ml |
| IOF subscriptions | -29,43 | 0,00 | _update_expense_amount_from_extrato |
| Bonus envio | +10,90 | 0,00 | bonus_envio + bonificacao |
| Cashback/cupom ML | -42,99 | -42,99 | Fora do nosso controle |
| **TOTAL** | **2.949,49** | **-42,99** | **99,87% cobertura** |

Os R$ 42,99 restantes sao cupons/cashback que o ML da ao comprador, reduzindo o valor
liberado abaixo do calculado (receita - comissao - frete). Nao e perdida — e desconto do ML.

Analises: `docs/GAP_ANALYSIS_JAN2026.md` (V1), `docs/GAP_ANALYSIS_V2_CAJOBS.md` (V2)
Teste de reconciliacao: `testes/test_reconciliation_141air.py`

---

## 12. Perguntas em aberto

1. ~~**Dia 26**: Por que 13 vendas nao foram sincronizadas?~~ **RESOLVIDO** — Smart skip agora captura.
2. ~~**Cartao de credito**: Deve ser capturado?~~ **RESOLVIDO** — Capturado como pagamento_cartao_credito (pending_review).
3. ~~**Holds temporarios**: Precisam de lancamento?~~ **RESOLVIDO** — Ja capturados como dinheiro_retido.
4. **Boleto Bank of America R$ 93k**: E pagamento real ou erro? Valor muito alto.
5. ~~**IOF**: O release_report_sync cobre todas as assinaturas?~~ **RESOLVIDO** — extrato_ingester agora atualiza valores com IOF real.
6. ~~**Claims/devolucoes**: Como ajustar calculo de receita?~~ **RESOLVIDO** — Nao e bug, e modelagem. ca_jobs com estornos ja zeram corretamente.
7. **Payments refundados sem extrato**: 4 payments com money_release_date em janeiro nunca apareceram no extrato. Provavelmente refundados antes da liberacao.
8. **Cashback/cupom ML (R$ 42,99)**: 6 payments onde ML deu desconto ao comprador. Valor pequeno, fora do nosso controle.
