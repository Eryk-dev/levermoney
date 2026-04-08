# Auditoria Janeiro 2026 — 141air — Extrato vs Sistema

## Resumo por Dia

| Dia | Mov. | OK | Diverg. | Valor Divergente (R$) |
|-----|------|-----|---------|----------------------|
| 01 | 9 | 4 | 5 | 3.988,24 |
| 02 | 12 | 8 | 4 | 102,53 |
| 03 | 6 | 6 | 0 | 0,00 |
| 04 | 6 | 5 | 1 | 1.000,00 |
| 05 | 9 | 5 | 4 | 1.881,95 |
| 06 | 15 | 11 | 4 | 1.649,97 |
| 07 | 6 | 5 | 1 | 10,90 |
| 08 | 8 | 4 | 4 | 1.025,19 |
| 09 | 4 | 0 | 4 | 711,71 |
| 10 | 14 | 10 | 4 | 4.098,14 |
| 11 | 11 | 10 | 1 | 8,99 |
| 12 | 9 | 6 | 3 | 872,88 |
| 13 | 10 | 9 | 1 | 1.435,56 |
| 14 | 10 | 7 | 3 | 667,11 |
| 15 | 18 | 14 | 4 | 290,02 |
| 16 | 24 | 21 | 3 | 941,16 |
| 17 | 25 | 25 | 0 | 0,00 |
| 18 | 17 | 17 | 0 | 0,00 |
| 19 | 16 | 10 | 6 | 8.422,58 |
| 20 | 25 | 22 | 3 | 1.085,75 |
| 21 | 17 | 16 | 1 | 11,04 |
| 22 | 19 | 16 | 3 | 687,61 |
| 23 | 28 | 24 | 4 | 4.470,65 |
| 24 | 18 | 16 | 2 | 87,80 |
| 25 | 22 | 19 | 3 | 3.842,35 |
| 26 | 152 | 125 | 19 | 98.005,10 |
| 27 | 14 | 12 | 2 | 88,94 |
| 28 | 2 | 2 | 0 | 0,00 |
| 29 | 7 | 2 | 5 | 0,00 |
| 30 | 11 | 6 | 5 | 0,00 |
| 31 | 0 | - | - | - |

---

## Catalogo de Falhas da API / Pipeline

### TIPO 1: Expenses sem ca_category (sem baixa no CA)

Movimentacoes capturadas em `mp_expenses` mas com `ca_category = NULL`.
Sem categoria, o pipeline nao gera ca_jobs. Invisivel ao ContaAzul.

| Dia | Ref ID | Descricao | Valor (R$) | expense_type |
|-----|--------|-----------|------------|--------------|
| 01 | 139632176183 | PIX enviado (LILLIAN) | 350,00 | transfer_pix |
| 01 | 139636302479 | Boleto Itau | 1.172,33 | bill_payment |
| 01 | 139636133731 | Boleto Itau | 1.120,47 | bill_payment |
| 01 | 140284411096 | Boleto Itau | 57,00 | bill_payment |
| 01 | 140291550258 | PIX enviado (Laura) | 1.288,44 | transfer_pix |
| 04 | 140655181652 | PIX enviado (Eryk) | 1.000,00 | transfer_pix |
| 05 | 140051770645 | Boleto Itau | 816,28 | bill_payment |
| 05 | 140701705736 | Boleto Itau | 757,87 | bill_payment |
| 05 | 140078200469 | PIX enviado (Marcelo) | 40,00 | transfer_pix |
| 06 | 140207271759 | Boleto Itau | 709,99 | bill_payment |
| 06 | 140207380399 | Boleto Itau | 321,00 | bill_payment |
| 06 | 140918245036 | PIX enviado (141 Air) | 100,00 | transfer_pix |
| 06 | 140961637762 | Boleto Itau | 518,98 | bill_payment |
| 09 | 140686543155 | Pagamento QR YAPAY | 200,00 | bill_payment |
| 09 | 140704272795 | Boleto Cora | 119,90 | bill_payment |
| 09 | 140705030609 | PIX enviado (Igor) | 31,90 | transfer_pix |
| 10 | 140779626787 | PIX enviado (Jonathan) | 70,00 | transfer_pix |
| 10 | 140878309513 | PIX enviado (Eryk) | 4.000,00 | transfer_pix |
| 12 | 141741288274 | Boleto Itau | 865,11 | bill_payment |
| 13 | 141836496376 | Boleto Itau | 1.435,56 | bill_payment |
| 14 | 141299911863 | Boleto Itau | 632,79 | bill_payment |
| 15 | 141448841633 | PIX enviado (Rivelino) | 50,00 | transfer_pix |
| 16 | 141638563857 | PIX enviado (Netparts) | 6.874,64 | transfer_pix |
| 19 | 142631359778 | Boleto Itau | 1.264,68 | bill_payment |
| 19 | 142631356098 | Boleto Celcoin | 349,00 | bill_payment |
| 19 | 141963473267 | Boleto Itau | 1.419,17 | bill_payment |
| 19 | 141963576589 | Boleto Itau | 624,55 | bill_payment |
| 19 | 142631650030 | Boleto Itau | 1.754,56 | bill_payment |
| 20 | 142160416821 | Deposito PIX (Eryk) | 936,34 | deposit |
| 23 | 143189222686 | Pagamento QR INPI | 140,00 | bill_payment |
| 23 | 142520240771 | Boleto Itau | 4.296,19 | bill_payment |
| 23 | 142530127795 | PIX enviado (Marcelo) | 30,00 | transfer_pix |
| 25 | 142804447735 | Boleto Itau | 818,38 | bill_payment |
| 25 | 142804560955 | Boleto Itau | 1.191,60 | bill_payment |
| 25 | 142804506011 | Boleto Itau | 1.832,37 | bill_payment |
| 26 | 142882588563 | Pagamento QR YAPAY | 95,72 | bill_payment |
| 26 | 143625557386 | Boleto Bank of America | 93.739,19 | bill_payment |

**Total: 37 ocorrencias, ~R$ 130.853,61**

Subtipos:
- **Boletos** (Itau, BB, Cora, Celcoin, INPI, Bank of America): 22 ocorrencias
- **Transferencias PIX** (saques para contas): 12 ocorrencias
- **Depositos PIX** (recebidos de volta): 1 ocorrencia
- **Pagamentos QR** (YAPAY, INPI): 2 ocorrencias

---

### TIPO 2: Expenses COM ca_category mas SEM baixa no CA

Movimentacoes em `mp_expenses` que tem categoria atribuida, mas que mesmo assim nao geraram ca_jobs. O pipeline de baixas nao processa mp_expenses, apenas payments.

| Dia | Ref ID | Descricao | Valor (R$) | ca_category |
|-----|--------|-----------|------------|-------------|
| 07 | 139026984141 | Bonus envio ME | 10,90 | 1.3.4 Descontos e Estornos de Taxas |
| 08 | 140496724089 | Pagamento Supabase | 163,31 | 2.6.4 Banco de Dados (Supabase) |
| 08 | 141215405790 | Pagamento Claude.ai | 550,00 | 2.6.5 APIs e Integracoes |
| 09 | 141355673172 | Ressarcimento Full ML | 359,91 | 1.4.2 Outras Receitas Eventuais |
| 11 | 138913692605 | Cashback envio | 8,99 | 1.3.4 Descontos e Estornos de Taxas |
| 15 | 142118838162 | DARF Simples Nacional | 220,93 | 2.2.7 Simples Nacional |
| 15 | 140775069472 | Bonus Flex ML | 1,09 | 1.3.4 Descontos e Estornos de Taxas |
| 16 | 141623517505 | DARF Simples Nacional | 941,16 | 2.2.7 Simples Nacional |
| 20 | 142123236115 | Ressarcimento Full ML | 149,41 | 1.4.2 Outras Receitas Eventuais |
| 22 | 140843722249 | Cashback envio | 9,99 | 1.3.4 Descontos e Estornos de Taxas |
| 26 | 141596403673 | Cashback envio | 18,99 | 1.3.4 Descontos e Estornos de Taxas |
| 26 | 142052290099 | Cashback envio | 30,99 | 1.3.4 Descontos e Estornos de Taxas |
| 26 | 142176328322 | Cashback envio | 19,99 | 1.3.4 Descontos e Estornos de Taxas |
| 26 | 143104571692 | Estorno disputa ML | 203,89 | 1.3.4 Descontos e Estornos de Taxas |
| 26 | 143581089158 | Ressarcimento Full ML | 263,85 | 1.4.2 Outras Receitas Eventuais |
| 27 | 140706688211 | Cashback envio | 1,09 | 1.3.4 Descontos e Estornos de Taxas |
| 29 | 143315503969 | Ressarcimento Full ML | 220,63 | 1.4.2 Outras Receitas Eventuais |
| 29 | 143315621045 | Ressarcimento Full ML | 626,46 | 1.4.2 Outras Receitas Eventuais |
| 29 | 143993074150 | Ressarcimento Full ML | 470,25 | 1.4.2 Outras Receitas Eventuais |

**Total: 19 ocorrencias, ~R$ 4.271,83**

**Causa raiz:** O pipeline de baixas (`_run_baixas_all_sellers`) so cria ca_jobs para vendas (payments). Expenses categorizados ficam em mp_expenses mas nunca viram lancamento no CA.

---

### TIPO 3: Movimentacoes invisiveis (nao existem em nenhuma tabela)

REFERENCE_IDs que aparecem no extrato real mas nao existem nem em `payments`, nem em `mp_expenses`, nem em `ca_jobs`. A API do ML nao retornou esses registros.

| Dia | Ref ID | Descricao extrato | Valor (R$) | Subtipo |
|-----|--------|-------------------|------------|---------|
| 02 | 138913863776 | Dinheiro retido Reclamacoes | -88,57 | Hold temporario (devolvido dia 16) |
| 05 | 138151129880 | Debito+Reembolso (refund net=0) | 0,00 (net) | Refund invisivel |
| 08 | 137820920749 | Reembolso Reclamacoes | +209,04 | Reembolso de claim |
| 08 | 138209751237 | Dinheiro retido Reclamacoes | -77,91 | Hold temporario |
| 10 | 137617703230 | Debito+Entrada+Reembolso (net=-28,14) | -28,14 | Refund parcial |
| 14 | 138209751237 | Debito+Entrada+Reembolso (net=-13,96) | -13,96 | Refund parcial (resolucao do hold dia 08) |
| 14 | 2728587235 | DIFAL | -20,36 | Imposto DIFAL |
| 16 | 138913863776 | Debito+Reembolso (resolucao) | 0,00 (net) | Resolucao do hold dia 02 |
| 19 | 141963223933 | Pagamento Cartao de credito | -3.010,62 | Cartao de credito |
| 21 | 2775052514 | DIFAL | -11,04 | Imposto DIFAL |
| 22 | 2775723042 | Faturas vencidas do ML | -612,97 | Fatura ML |
| 22 | 2778152634 | DIFAL | -46,24 | Imposto DIFAL |
| 22 | 142651949400 | Envio ML (pos-refund) | -28,40 | Cobranca frete devolucao |
| 24 | 142038148246 | Envio ML (pos-refund) | -42,90 | Cobranca frete devolucao |
| 24 | 140778735616 | Envio ML (pos-refund) | -44,90 | Cobranca frete devolucao |
| 26 | 141043812466 | Liberacao de dinheiro | +734,76 | Venda nao sincronizada |
| 26 | 141183074293 | Liberacao de dinheiro | +861,77 | Venda nao sincronizada |
| 26 | 141251658525 | Liberacao de dinheiro | +63,68 | Venda nao sincronizada |
| 26 | 141359034751 | Liberacao de dinheiro | +38,92 | Venda nao sincronizada |
| 26 | 141385949804 | Liberacao de dinheiro | +405,92 | Venda nao sincronizada |
| 26 | 141470360279 | Liberacao de dinheiro | +102,75 | Venda nao sincronizada |
| 26 | 141587118535 | Liberacao de dinheiro | +63,68 | Venda nao sincronizada |
| 26 | 141922182246 | Liberacao de dinheiro | +19,96 | Venda nao sincronizada |
| 26 | 141996119325 | Liberacao de dinheiro | +383,81 | Venda nao sincronizada |
| 26 | 142110483725 | Pagamento QR Pix | +203,89 | Venda nao sincronizada |
| 26 | 142292552528 | Liberacao de dinheiro | +88,74 | Venda nao sincronizada |
| 26 | 142339588114 | Liberacao de dinheiro | +781,00 | Venda nao sincronizada |
| 26 | 142406081170 | Pagamento QR Pix | +75,94 | Venda nao sincronizada |
| 26 | 142933941713 | Debito por divida | -70,79 | Retencao |
| 26 | 142935080179 | Debito por divida | -101,04 | Retencao |
| 26 | 143608784484 | Reembolso/Entrada | +70,79 | Resolucao retencao |
| 26 | 143610282146 | Debito/Retido | -102,75 | Retencao |

**Total: 32 ocorrencias**

**Subtipos identificados:**

| Subtipo | Qtd | Descricao | Impacto |
|---------|-----|-----------|---------|
| **DIFAL** | 3 | Imposto estadual, IDs curtos (27xxxxx) | Debito real, impacta saldo |
| **Fatura ML** | 1 | Cobranca de faturas do ML | Debito real |
| **Cartao de credito** | 1 | Pagamento de fatura de cartao | Debito real |
| **Holds temporarios** | 4 | "Dinheiro retido" por disputa | Temporario, resolvido depois |
| **Envio ML pos-refund** | 3 | Cobranca de frete de devolucao | Debito real, cobrado apos refund |
| **Refunds invisiveis** | 4 | Refunds de payments que nao existem no sistema | Net geralmente zero |
| **Vendas nao sincronizadas** | 13 | Liberacoes que nao existem em payments (dia 26) | Receita nao registrada |
| **Retencoes** | 3 | Debitos/retidos sem contrapartida | Impacta saldo |

---

### TIPO 4: Estorno parcial de taxa

Em refunds, o CA registra estorno_taxa = fee + frete, mas o ML pode devolver so a fee.

| Dia | Ref ID | Extrato debita | Payment amount | Diff (R$) | Causa |
|-----|--------|---------------|----------------|-----------|-------|
| 02 | 139562683028 | 109,90 | 109,90 | 13,96 | Frete nao reembolsado |
| 12 | 140915607218 | 119,37 | 111,60 | 7,77 | Extrato debita mais que amount |
| 15 | 141555524687 | 162,92 | 144,90 | 18,02 | Extrato debita mais que amount |

**Total: 3 ocorrencias, R$ 39,75**

**Nota:** Nos casos 12 e 15, o extrato debita um valor MAIOR que o `amount` do payment. Isso indica que o ML cobra adicionais no momento do refund que nao estao refletidos no raw_payment.

---

### TIPO 5: Diferenca de IOF em pagamentos internacionais

Extrato mostra valor com IOF (6,38%), mp_expenses registra valor sem IOF.

| Dia | Ref ID | Descricao | Extrato (R$) | mp_expenses (R$) | Diff IOF (R$) |
|-----|--------|-----------|-------------|------------------|---------------|
| 08 | 140496724089 | Supabase | 169,03 | 163,31 | 5,72 |
| 08 | 141215405790 | Claude.ai | 569,25 | 550,00 | 19,25 |
| 23 | 143199074090 | Notion | 131,94 | 127,48 | 4,46 |

**Total: 3 ocorrencias, R$ 29,43**

---

### TIPO 6: Reembolso parcial nao registrado

Payment com `ml_status=approved` no sistema, mas extrato mostra refund parcial.

| Dia | Ref ID | Net sistema (R$) | Net extrato (R$) | Diff (R$) |
|-----|--------|------------------|------------------|-----------|
| 27 | 141612723343 | 180,86 | 91,92 | 88,94 |

**Total: 1 ocorrencia, R$ 88,94**

**Causa:** O ML processou um refund parcial, mas a API ainda retorna `status=approved`. O sistema nao detectou a mudanca de status.

---

## Consolidado por Tipo de Falha

| Tipo | Ocorrencias | Valor Total (R$) | Severidade |
|------|-------------|-------------------|------------|
| 1. Expense sem ca_category | 37 | ~130.853 | ALTA (acumula diariamente) |
| 2. Expense COM categoria sem baixa | 19 | ~4.272 | MEDIA (pipeline incompleto) |
| 3. Movimentacao invisivel | 32 | variavel | ALTA (dados perdidos) |
| 4. Estorno parcial de taxa | 3 | ~40 | BAIXA (valores pequenos) |
| 5. Diferenca IOF | 3 | ~29 | BAIXA (informativa) |
| 6. Refund parcial nao detectado | 1 | ~89 | MEDIA (status desatualizado) |

---

## Acoes Recomendadas

### Prioridade 1 — Criar baixas para mp_expenses categorizados (Tipo 2)
O pipeline de baixas precisa processar mp_expenses que ja tem ca_category, nao so payments.
Impacto: R$ 4.272 ficam "no limbo" — categorizados mas sem lancamento no CA.

### Prioridade 2 — Capturar movimentacoes invisiveis (Tipo 3)
Subtipos que a API de Payments nao retorna:
- **DIFAL**: IDs curtos (27xxxxx), cobrados como "Debito por divida"
- **Faturas ML**: cobracas de faturas vencidas do marketplace
- **Pagamento Cartao**: pagamento de fatura de cartao via conta ML
- **Envio ML pos-refund**: cobranca de frete de devolucao separada do refund
- **Vendas nao sincronizadas** (dia 26): 13 vendas que a API nao retornou

### Prioridade 3 — Classificar expenses pendentes (Tipo 1)
Boletos e PIX precisam de regras de categorizacao automatica ou workflow manual.
Maior impacto financeiro (R$ 130k) mas ja estao no sistema.

### Prioridade 4 — Corrigir estorno_taxa para refunds parciais (Tipo 4)
Verificar no raw_payment se frete foi reembolsado antes de incluir no estorno_taxa.

### Prioridade 5 — Registrar IOF separadamente (Tipo 5)
Para assinaturas internacionais, capturar valor com IOF do extrato vs valor base da API.

### Prioridade 6 — Detectar refunds parciais (Tipo 6)
Re-verificar status de payments `approved` que tiveram movimentacoes de debito no extrato.
