# 03 — Forense dos subsistemas + bugs

Resultado do workflow forense (9 agentes lendo cada subsistema + histórico git).

## Maturidade por subsistema (0-100, honesto)

| Subsistema | Mat. | É gargalo do 100%? |
|---|---|---|
| `processor.py` (núcleo payment→CA) | 72 | parcial — receita/comissão certos; líquido não fecha sozinho |
| `financial_closing.py` | 35 | **SIM** — mede completude de job, NÃO reconcilia valor |
| `extrato_ingester` + `coverage_checker` | 55 | **SIM** — bug de formato, fontes diferentes, cobertura por contagem |
| `release_report_*` (validação fee) | 58 | **SIM** — unidirecional, mismatch de frete, congela payment |
| `expense_classifier.py` | 55 | corretude/escala — string-matching frágil, teto auto baixo |
| `ca_api` + `ca_queue` + `baixas` | 72 | parcial — POST maduro; baixa pela promessa, dead trava |
| `legacy/engine.py` (~1500 linhas) | 55 | aposentado (legacy_daily_enabled=False) |

## O achado central (a "ilusão do 100%")

Existem dois "100%" e o sistema mede o errado:
- **COBERTURA** (toda linha do extrato tem balde) — medido por `extrato_coverage_checker`, por
  CONTAGEM de linhas. ~98,9%.
- **VALOR** (Σ CA == Σ extrato/dia) — **NINGUÉM mede.** `financial_closing.py:156-188` soma os
  valores assinados e **joga fora**. `closed=true` só significa "jobs em estado final".

A regra de ouro `extrato_total_dia = baixa_api_dia + ajustes_legado_dia` (REGRAS_NEGOCIO.md:35)
**nunca foi implementada como check.** Por isso o sistema "reportava 100%" e não fechava.

## Bugs com dinheiro colado (smoking guns), ordem de impacto

1. **🔴 ingester pode processar ZERO linhas.** `_parse_account_statement` só entende
   `RELEASE_DATE;TRANSACTION_TYPE` mas recebe release_report em `DATE;SOURCE_ID;RECORD_TYPE`
   (extrato_ingester.py:565,234 vs ml_api.py:449-467). Não chama o conversor de
   `legacy/daily_export.py:403-433`. Alimentado com formato da API → 0 transações, silencioso.
   **Provável raiz da "tentativa frustrada".** (Fase 1 — pendente.)
2. **🔴 coverage mede fonte DIFERENTE do ingester** + `int(payment_id)` (coverage_checker:61-71)
   quebra chave composta `"123456:df"`. (Fase 2 — int corrigido; unificar fonte pendente.)
3. **🔴 reconciliação de valor ausente** (o juiz — Fase 0, construído nesta rodada).
4. **🟠 release_report_validator unidirecional:** só ajusta quando ML cobra MAIS (:324-333);
   mismatch de base no frete (líquido vendedor vs bruto report → infla despesa); `fee_adjusted`
   nunca reseta → congela payment. (Fase 4 — guard de frete feito; resto pendente.)
5. **🟠 net_diff<0 (taxa oculta) só warning** (processor.py:322-329). (Fase 1 — **corrigido**.)
6. **🟠 bugs de classificação do extrato:** "Pix recebido" sem regra (→OTHER); "Reembolso de
   pagamento de conta" casa skip antes de reembolso (R$2.168 perdido); "Dinheiro recebido
   cancelado" sinal trocado (income); "Compra Mercado Livre" PT → OTHER; double-count de
   disputa (retido + débito ambos). (Fase 7 — **corrigidos** os 4 primeiros + sinal.)
7. **🟠 baixa morta = parcela eterna.** Baixa que toma 4xx vai pra dead sem grupo, sem sinal →
   parcela fica EM_ABERTO pra sempre → conta financeira do CA ≠ extrato (ca_queue.py:150-160,
   310-321). (Fase 3-full — pendente.)
8. **🟠 estorno usava `datetime.now()`** (processor.py:471) → estorno no dia errado. (Fase 3 —
   **corrigido**, usa data real BRT.)
9. **🟠 refund parcial não reverte comissão/frete proporcionalmente** → líquido diverge.
   (Fase 4 — pendente.)

## Divergências ACEITAS como corretas (definem o teto)

- Devoluções diferidas (DRE por data do estorno vs painel ML por mês da venda).
- by_admin/kit-split pulado quando não-synced.
- Subsídio ML 1.3.7; financing_fee net-neutral.
- Resíduo < R$200/seller (by_admin parciais + arredondamento).
