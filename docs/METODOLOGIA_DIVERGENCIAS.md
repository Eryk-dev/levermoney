# Metodologia de Busca de Divergencias — Extrato ML vs Sistema

## 1. Objetivo

Identificar divergencias entre o **extrato bancario real do MercadoLivre** (CSV exportado)
e os **lancamentos capturados pelo sistema** (payments, mp_expenses, ca_jobs no Supabase).

O cruzamento e feito **dia a dia**, comparando cada movimentacao do extrato com os dados
do sistema para verificar se: (a) foi capturada, (b) foi categorizada e (c) gerou baixa no CA.

---

## 2. Fontes de Dados

### 2.1 Extrato CSV (fonte real)

Arquivo exportado do MercadoLivre. Formato:

```
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
4.476,23;207.185,69;-210.571,52;1.090,40

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-01-2026;Liberacao de dinheiro ;138199281600;3.994,84;5.771,27
```

Colunas relevantes:
- **RELEASE_DATE**: data da movimentacao (DD-MM-YYYY)
- **TRANSACTION_TYPE**: tipo da operacao (texto livre)
- **REFERENCE_ID**: ID do payment no ML (chave de cruzamento)
- **TRANSACTION_NET_AMOUNT**: valor liquido (positivo = credito, negativo = debito)

### 2.2 Tabela `payments` (vendas)

Vendas aprovadas, sincronizadas via API do ML.

| Coluna | Descricao |
|---|---|
| `ml_payment_id` | ID do payment (= REFERENCE_ID do extrato) |
| `ml_order_id` | ID do pedido ML |
| `amount` | Valor bruto da venda |
| `net_amount` | Valor liquido (= amount - processor_fee - processor_shipping) |
| `processor_fee` | Comissao ML |
| `processor_shipping` | Frete MercadoEnvios |
| `money_release_date` | Data de liberacao do dinheiro |
| `ml_status` | Status no ML (approved, refunded, etc) |

### 2.3 Tabela `mp_expenses` (movimentacoes nao-venda)

Despesas, saques, boletos, depositos, transferencias.

| Coluna | Descricao |
|---|---|
| `payment_id` | ID do payment (= REFERENCE_ID do extrato) |
| `amount` | Valor da movimentacao |
| `expense_type` | Tipo (bill_payment, transfer_pix, etc) |
| `expense_direction` | Direcao (expense, transfer, income) |
| `ca_category` | Categoria CA atribuida (NULL = nao classificado) |
| `date_approved` | Data de aprovacao |

### 2.4 Tabela `ca_jobs` (baixas no CA)

Lancamentos enviados ao ContaAzul.

| Coluna | Descricao |
|---|---|
| `group_id` | Formato `{seller_slug}:{payment_id}` |
| `job_type` | Tipo: receita, comissao, frete, estorno, estorno_taxa, baixa |
| `status` | completed, pending, failed |
| `ca_payload` | JSON com `valor`, `descricao`, etc |

---

## 3. Passo a Passo (por dia)

### Passo 1 — Extrair linhas do extrato

Filtrar o CSV pela data desejada. Exemplo para 01/01/2026:

```bash
grep "^01-01-2026" extrato.csv
```

### Passo 2 — Coletar REFERENCE_IDs unicos

De todas as linhas do dia, extrair a lista de REFERENCE_IDs unicos.
Note que um mesmo ID pode aparecer em varias linhas (ex: liberacao + estorno + reembolso).

### Passo 3 — Buscar cada ID no sistema

Para cada REFERENCE_ID, executar 3 queries:

**3a. Buscar em `payments`:**
```sql
SELECT ml_payment_id, ml_order_id, amount, net_amount,
       processor_fee, processor_shipping,
       money_release_date, status, ml_status
FROM payments
WHERE seller_slug = '{SELLER}'
  AND ml_payment_id IN ({IDs});
```

**3b. Buscar em `mp_expenses`:**
```sql
SELECT payment_id, description, amount, ca_category,
       expense_type, expense_direction, date_approved
FROM mp_expenses
WHERE seller_slug = '{SELLER}'
  AND payment_id IN ({IDs});
```

**3c. Buscar em `ca_jobs`:**
```sql
SELECT group_id, job_type, status,
       (ca_payload->>'valor')::numeric as valor,
       ca_payload->>'descricao' as descricao
FROM ca_jobs
WHERE seller_slug = '{SELLER}'
  AND group_id LIKE ANY(ARRAY[
    '{SELLER}:{ID1}%', '{SELLER}:{ID2}%', ...
  ])
ORDER BY group_id, job_type;
```

### Passo 4 — Classificar cada REFERENCE_ID

Para cada ID, determinar em qual situacao se encontra:

| Situacao | Onde esta? | Tem baixa? | Diverge? |
|---|---|---|---|
| Venda com baixa | payments + ca_jobs | Sim | Verificar valores |
| Expense categorizado | mp_expenses (ca_category != null) + ca_jobs | Sim | Verificar valores |
| Expense sem categoria | mp_expenses (ca_category = null) | Nao | **SIM** — invisivel ao CA |
| Movimentacao invisivel | Nenhuma tabela | Nao | **SIM** — nao capturado |

### Passo 5 — Calcular net por payment (refunds)

Para payments com `ml_status = 'refunded'`, o extrato pode ter multiplas linhas:

| Linha tipica do extrato | O que representa |
|---|---|
| Liberacao de dinheiro | +net_amount (venda liberada) |
| Debito por divida Reclamacoes | -amount (refund total ao comprador) |
| Reembolso Envio cancelado | +(processor_fee + processor_shipping) — taxas devolvidas |
| Entrada de dinheiro | +processor_fee (so a comissao devolvida, sem frete) |

**Verificar se o CA bate:**
```
Net extrato = soma das linhas do extrato para o ID
Net CA = receita - comissao - frete - estorno + estorno_taxa
```

Se `estorno_taxa` no CA inclui o frete mas o extrato nao devolveu o frete,
ha divergencia (estorno parcial de taxa).

### Passo 6 — Totalizar divergencias do dia

```
Divergencia = SUM(valores no extrato que nao tem baixa correspondente no CA)
```

---

## 4. Tipos de Divergencia Encontrados

### 4.1 Expense sem categoria (ca_category = NULL)

**O que e:** mp_expenses capturados pelo sistema mas sem classificacao CA.
Sem categoria, nenhum ca_job e criado — invisivel ao CA.

**Exemplos:**
- Boletos (Itau, BB, Cora, Celcoin)
- Transferencias PIX enviadas (saques para contas bancarias)
- Depositos PIX recebidos
- Transferencias Intra MP
- Pagamentos QR (release)

**Impacto:** Debitos reais que nao aparecem no CA. Acumulam divergencia todo dia.

**Solucao:** Classificar com ca_category apropriada para gerar baixas.

### 4.2 Movimentacao invisivel (nao capturada)

**O que e:** REFERENCE_ID do extrato que nao existe em `payments` nem em `mp_expenses`.
O sistema simplesmente nao capturou essa movimentacao.

**Exemplos:**
- "Dinheiro retido Reclamacoes e devolucoes" — hold temporario por disputa
- Pode ser revertido dias depois ("Reembolso Reclamacoes e devolucoes")

**Impacto:** Movimentacao real no saldo que o sistema desconhece.

**Solucao:** Ingerir via extrato (ingestion de gaps) ou criar tipo especifico no mp_expenses.

### 4.3 Estorno parcial de taxa

**O que e:** Em refunds, o CA estorna fee+frete (estorno_taxa), mas o ML pode
devolver so a fee e nao o frete. O CA fica com credito a mais.

**Como detectar:** Comparar `estorno_taxa` do ca_job com as linhas de reembolso do extrato:
- Se extrato tem "Reembolso Envio cancelado" com fee+frete: OK, estorno total
- Se extrato tem so "Entrada de dinheiro" com fee: estorno parcial, frete perdido

**Impacto:** Diferenca = valor do frete (processor_shipping) por ocorrencia.

**Solucao:** Verificar no raw_payment se houve reembolso parcial e ajustar estorno_taxa.

---

## 5. Checklist de Verificacao (por dia)

- [ ] Todos os REFERENCE_IDs do extrato foram encontrados em payments ou mp_expenses?
- [ ] Se nao: listar IDs invisiveis e seus tipos/valores
- [ ] mp_expenses sem ca_category: qual o valor total dos debitos nao baixados?
- [ ] Payments refunded: estorno_taxa no CA bate com reembolsos reais do extrato?
- [ ] Movimentacoes temporarias (holds): aparecem revertidas em outro dia?
- [ ] Saldo final do extrato = saldo inicial + creditos + debitos?
- [ ] Net do CA para o dia = net do extrato para o dia?

---

## 6. Exemplo Pratico — 141air, Janeiro 2026

### Dia 01/01/2026

| # | Tipo | Ref ID | Extrato | Sistema | Baixa CA |
|---|---|---|---|---|---|
| 1 | PIX enviado (LILLIAN) | 139632176183 | -350,00 | mp_expenses (null) | Nao |
| 2 | Boleto Itau | 139636302479 | -1.172,33 | mp_expenses (null) | Nao |
| 3 | Boleto Itau | 139636133731 | -1.120,47 | mp_expenses (null) | Nao |
| 4 | Boleto Itau | 140284411096 | -57,00 | mp_expenses (null) | Nao |
| 5 | Liberacao | 138199281600 | +3.994,84 | payments | Sim |
| 6 | Liberacao | 138157090675 | +59,85 | payments | Sim |
| 7 | Liberacao | 138651457556 | +413,86 | payments | Sim |
| 8 | QR Pix | 137262603377 | +42,49 | payments | Sim |
| 9 | PIX enviado (Laura) | 140291550258 | -1.288,44 | mp_expenses (null) | Nao |

**Divergencia:** R$ 3.988,24 (5 debitos sem categoria, sem baixa no CA)

### Dia 02/01/2026

| # | Tipo | Ref ID | Extrato | Sistema | Baixa CA |
|---|---|---|---|---|---|
| 1 | Dinheiro retido | 138913863776 | -88,57 | **Nao existe** | Nao |
| 2-4 | Refund completo | 139749344683 | 0,00 (net) | payments (refunded) | Sim (net=0) |
| 5,7,8 | Refund completo | 140422465618 | 0,00 (net) | payments (refunded) | Sim (net=0) |
| 6,9 | Refund completo | 140422485450 | 0,00 (net) | payments (refunded) | Sim (net=0) |
| 10-12 | Refund parcial | 139562683028 | -13,96 (net) | payments (refunded) | CA diz 0 |

**Divergencia:** R$ 102,53 (hold invisivel R$ 88,57 + frete nao reembolsado R$ 13,96)
