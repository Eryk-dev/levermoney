# Errors — Catálogo de bugs de reconciliação

**Regra:** antes de tentar consertar QUALQUER coisa, buscar aqui se o sintoma já foi visto. Se foi, ler a lição antes.

Formato: `ERR-NNNN` numerado sequencial. Nunca reusar número. Corrigido ≠ apagado — fica aqui como lição.

---

## ERR-0001 — R$ 53.000 classificado com sinal invertido (transfer_intra)
**Descoberto:** 2026-04-16, durante reconciliação 141air jan/2026
**Status:** Resolvido em 2026-04-16 (T-010) — ver `testes/unit/test_expense_classifier_sign.py`. Causa raiz: `_is_incoming_transfer` lia `payment.collector.id` (aninhado) mas a API MP retorna `collector_id` top-level, então o lookup sempre devolvia vazio e o sign default ia para `transfer_out`. Fix: aceita ambos formatos, expande lista incoming pra incluir `transferencia_pix_in`/`entrada_dinheiro`, e defaulta incoming quando ambos IDs ausentes.

**Sintoma:**
- Extrato: linha "Transferência Pix recebida", ref=X, **+R$ 53.000,00**
- Sistema: mp_expense com `expense_type='transfer_intra'`, `expense_direction='transfer'`, `amount=53000` **classificado como saída**

Reconciliador mostra:
- Orphan extrato: `transferencia_pix_in` +R$ 53.000
- Orphan sistema: `transfer_intra` -R$ 53.000
- Soma zero (é a mesma grana), mas sinal invertido quebra I-5.

**Causa raiz (suspeita):**
`app/services/expense_classifier.py` — regra de classificação de `transfer_intra` / `deposit` define `direction='transfer'` mas a derivação do signed_amount trata como saída por default quando a memória `expense_classifier_bugs.md` já registrava esse mesmo bug em 2026-04-07. **Regressão possivelmente reintroduzida.**

**Fix (a fazer em T-010):**
1. Escrever teste property-based que gera payments com cada `expense_type` em `{deposit, transfer_intra, transferencia_pix_in, transfer_pix, pix_enviado}` e valida que `signed_amount` sai com sinal correto baseado na semântica real (income/transfer_in → positivo, expense/transfer_out → negativo).
2. Ajustar `expense_classifier.py` até o teste passar.
3. Re-rodar reconciliação: deve ganhar +R$ 53k em cada lado (passa de orphan pra match).
4. Anexar RUNS.md entry mostrando o bump.

**Lição:**
Todo `expense_type` com `direction='transfer'` precisa definição EXPLÍCITA de sinal. Usar `direction='transfer'` como categoria neutra permite ambiguidade. Melhor: substituir por `transfer_in`/`transfer_out` direto, com sinais fixos.

**Arquivos envolvidos:** `app/services/expense_classifier.py`, `app/services/money.py`

---

## ERR-0002 — mp_expenses stale (liberacao_nao_sync / qr_pix_nao_sync)
**Descoberto:** 2026-04-16, durante reconciliação 141air jan/2026
**Status:** Resolvido em 2026-04-16 (T-011). Implementado helper puro `extrato_ingester.find_stale_expense_events()` (12 testes em `testes/integration/test_stale_mp_expenses.py`) e wired em `reconciliation.filter_stale_mp_expenses()` para excluir rows stale antes do match. Cleanup retroativo no DB ainda pendente (este fix evita que a reconciliação conte stale; ele NÃO deleta rows do mp_expenses).

**Sintoma:**
Reconciliador reporta:
- 14 rows `liberacao_nao_sync` em mp_expenses totalizando R$ 4.800,91 — todas com `payment_id` que AGORA existe em `payment_events` com `sale_approved`.
- 9 rows `qr_pix_nao_sync` em mp_expenses totalizando R$ 2.556,25 — idem.

Duplicam lançamentos: a informação correta está em payment_events (via `processor.py`), mas a row antiga em mp_expenses continua viva.

**Causa raiz:**
Sequência temporal:
- T1: `extrato_ingester` rodou, viu linha "Liberação de dinheiro" para payment X, buscou em payment_events, não encontrou (ainda), criou mp_expense com fallback `liberacao_nao_sync`.
- T2: backfill (hoje, 2026-04-16) foi executado, payment X passou a ter `sale_approved`.
- Row T1 nunca foi limpa.

**Fix (a fazer em T-011):**
1. Migration script: `UPDATE mp_expenses SET status='superseded' WHERE expense_type IN (...) AND payment_id IN (SELECT ml_payment_id FROM payment_events WHERE event_type='sale_approved')`.
2. Regra futura em `daily_sync` ou `extrato_ingester`: antes de ingerir como nao_sync, se o payment **agora** existe em events, skip.
3. Ou: job noturno que limpa stale.

**Lição:**
`_check_payments`-style classifiers precisam reconciliar dos dois lados quando o estado do mundo muda. Se a classificação depende de uma tabela B que pode receber dados depois, o registro em A fica stale. Solução estrutural: referential integrity via FK ou cleanup job.

**Arquivos envolvidos:** `app/services/extrato_ingester.py`, `app/services/daily_sync.py`, nova migration

---

## ERR-0003 — NET por payment diverge do extrato por timing de refund
**Descoberto:** 2026-04-16, durante reconciliação 141air jan/2026
**Status:** Resolvido em 2026-04-16 (T-012). `events_to_payment_movements()` refatorada: agora emite 2 grupos por payment — release group (sale + fee + shipping + subsidy na money_release_date) e refund group (refund + refund_fee + refund_shipping na refund event_date). 7 testes em `testes/integration/test_cash_movement_per_event.py`.

**Sintoma:**
46 linhas com `status=amount_diff`. Exemplo: payment `139749344683`, 2026-01-02, extrato=R$ 4.318,05 (Liberação), sistema NET=R$ 531,95. Diff: R$ 3.786,10.

**Causa raiz:**
Esse payment foi refundado em fevereiro. Nosso NET = sale_approved + fee + shipping - refund_created = R$ 531,95 (valor final pós-refund). Mas o extrato de **janeiro** mostra só a liberação inicial (sem o refund, que aparece como débito em fevereiro).

**Fix (a fazer em T-012):**
Reconciliador emite **1 CashMovement por evento individual** com sua `event_date`, não 1 por payment com NET consolidado. Cada evento casa com a linha correspondente do extrato (liberação → sale_approved release; débito dispute → refund_created).

**Lição:**
Contabilidade financeira é **por data**, não por entidade. NET consolidado por payment perde a dimensão temporal. Pensando em invariantes: I-3 (daily totals) exige granularidade de data; I-4 (per-payment) exige consistência do ciclo completo. Os dois coexistem mas precisam ser testados separadamente com estruturas diferentes.

**Arquivos envolvidos:** Reconciliador (a ser reescrito); função `events_to_payment_movements` precisa virar `events_to_event_movements`.

---

## ERR-0004 — Naming mismatch: extrato_type "pagamento_conta" vs mp_expense.expense_type "bill_payment"
**Descoberto:** 2026-04-16
**Status:** Resolvido em 2026-04-16 (T-013). `_expense_type_to_category` em `reconciliation.py` agora colapsa `bill_payment` → `pagamento_conta` (e `transfer_intra` → `transferencia_pix_in`) para a primeira passada do matcher. Contrato lockado em `testes/unit/test_category_mapping.py` (4 testes, incluindo idempotência).

**Sintoma:**
22 linhas `Pagamento de conta Itaú` no extrato ficaram órfãs no matcher.
25 rows `bill_payment` em mp_expenses ficaram órfãs "do outro lado".
Totais batem (R$ 116k), mas o matcher não casou porque o nome da categoria difere.

**Causa raiz:**
- `extrato_ingester._classify_extrato_line` retorna `expense_type='pagamento_conta'` para "pagamento de conta".
- `expense_classifier` (não-order payments) retorna `expense_type='bill_payment'` para o mesmo conceito.
- Ambos sistemas podem ingerir a mesma linha (pela API ou pelo CSV) com nomes diferentes.

**Fix (a fazer em T-013):**
1. Unificar em uma única constante canônica (sugestão: `bill_payment`).
2. Tabela de mapeamento em `contract.yml` traduz `extrato_type` → `canonical_expense_type`.
3. Matcher usa o canonical em todo lado.

**Lição:**
Quando dois subsistemas produzem dados para a mesma tabela, eles precisam partilhar o vocabulário. Taxonomia divergente é erro de integração — surge em testes cross-system, não em testes unitários de cada lado isolado.

**Arquivos envolvidos:** `app/services/extrato_ingester.py` (`EXTRATO_CLASSIFICATION_RULES`), `app/services/expense_classifier.py`, contract.yml

---

## ERR-0005 — Sign inversion para transfer_intra no matcher (duplicação do ERR-0001)
**Descoberto:** 2026-04-16, ao investigar órfão remanescente de R$ 53.000 após ERR-0001 corrigido.
**Status:** Aberto — fix em andamento via teste red em `testes/integration/test_reconciliation_sign_transfer_intra.py`.

**Sintoma:**
Par órfão remanescente em 141air jan/2026:
- Extrato: "Transferência Pix recebida EASY COMERCIO", ref=`143624212572`, **+R$ 53.000,00**
- mp_expenses: `payment_id=143624212572`, `expense_type='transfer_intra'`, `expense_direction='transfer'`, `amount=53000`

Mesmo `ref_id`, mesma categoria canônica após mapping (`transfer_intra → transferencia_pix_in`). Mas `reconciliation.expenses_to_movements` aplica `signed = -amount` para `transfer_intra`, gerando -R$ 53k no sistema vs +R$ 53k no extrato.
Matcher 3-pass falha em todos:
- Pass 1: (ref_id, category) + |+53k − (−53k)| = 106k > tolerância.
- Pass 2: (ref_id, amount) + mesma distância.
- Pass 3: sinais opostos.

Resultado: 2 órfãos (1 extrato + 1 sistema). Coverage créditos trava em 69,29%.

**Causa raiz:**
ERR-0001 (2026-04-16) corrigiu `_is_incoming_transfer` em `expense_classifier.py`, mas a camada de reconciliação tem sua **própria** lógica de sinal em `reconciliation.expenses_to_movements()` (linhas 406-416). Essa lógica não foi atualizada; continua tratando `transfer_intra` como `signed = -amount` independentemente do contexto.

Duas pistas de que é o mesmo bug:
- `signed_amount = -53000` no `payment_events` (gravado antes do fix do ERR-0001) ainda está lá.
- `raw_payment` em mp_expenses é null para essa row (dados históricos sem collector/payer para disambiguar).

**Fix (T-ERR-0005):**
1. Red test: pair (extrato +53k pix_in, mp_expense transfer_intra 53k) deve produzir `status='match'`.
2. Refatorar `expenses_to_movements` para usar **categoria canônica** (`_expense_type_to_category(expense_type)`) como base do sign, não o `expense_type` bruto. Regra:
   - canonical in {deposit, deposito_avulso, transferencia_pix_in, entrada_dinheiro} → positivo
   - canonical in {transfer_pix, pix_enviado, transferencia_pix_out} → negativo
   - outros transfer → default positivo (ERR-0001 lesson)
3. Re-rodar gate, anexar RUNS.md.

**Lição:**
Sign logic replicada em duas camadas (classifier + reconciliação) é bug esperando para acontecer. Sempre que a camada downstream não consegue ler `signed_amount` da fonte de verdade, precisa compartilhar a MESMA função de sinal. Regra futura: `reconciliation` deve ler `signed_amount` direto de `payment_events` quando possível; quando só tiver `mp_expenses`, usar helper canônico baseado em categoria canônica (mesmo `_is_incoming_transfer` ou wrapper).

**Arquivos envolvidos:** `app/services/reconciliation.py`, `testes/integration/test_reconciliation_sign_transfer_intra.py` (novo)

---

## ERR-0006 — load_payment_events perde sale_approved histórico
**Descoberto:** 2026-04-16, investigando liberacao + pagamento_qr órfãos restantes.
**Status:** Aberto — fix em andamento.

**Sintoma:**
Payments aprovados em dezembro (sale_approved com event_date=2025-12-XX) e liberados em janeiro (money_released com event_date=2026-01-YY) aparecem no extrato de janeiro como liberação, mas `events_to_payment_movements` não consegue montar o release group porque só `money_released` foi carregado (sale_approved + fee_charged + shipping_charged ficaram fora da query).

Resultado: 30 órfãos no extrato (21 liberacao R$ 4.2k + 9 pagamento_qr R$ 4.9k ≈ R$ 9.1k).

Exemplo: payment `138580747200`
- DB tem: sale_approved (+52.15, event_date=2025-12-19, release_date=2026-01-13), fee_charged (-12.75, event_date=2025-12-19), money_released (event_date=2026-01-13)
- Query load_payment_events(período=2026-01): pega money_released; sale_approved/fee ficam fora.
- Fallback load_events_for_pids só roda para pids que NÃO estão em current_pids. Como 138580747200 tem money_released dentro do período, já está em current_pids, então fallback pula ele.
- events_to_payment_movements: sem sale_approved, não emite release group.
- Extrato tem "Liberação de dinheiro" R$ 39.40 na 2026-01-13 → órfão.

**Causa raiz:**
`app/services/reconciliation.py::load_payment_events` filtra por `event_date ∈ [período]` OU `competencia_date ∈ [período]`. Um payment pode ter eventos fora do período (sale_approved em Dec) E eventos dentro (money_released em Jan). A query carrega só os eventos dentro. Depois, o fallback só compensa para payments AUSENTES do período.

**Fix:**
Após primeira passada, expandir para buscar TODOS eventos de qualquer pid já encontrado. Assim:
1. Load events in-period (existing).
2. Extract pid set from those events.
3. Load ALL events for those pids (new pass), dedupe by id.
4. Continue with existing logic.

Mantém o comportamento para extra_pids (pids do extrato sem nenhum evento in-period).

**Lição:**
Queries por "eventos em X período" não capturam lifecycle completo quando sale_approved e money_released estão em meses diferentes. Para reconciliação de caixa (que usa release_date), sempre busque o conjunto completo de eventos por pid, não só eventos com event_date no período.

**Arquivos envolvidos:** `app/services/reconciliation.py::load_payment_events`, `testes/integration/test_reconciliation_history_events.py` (novo)

---

## ERR-0007 — Refund group agregado perde granularidade do extrato MP
**Descoberto:** 2026-04-16, investigando amount_diff dominando débitos após ERR-0006.
**Status:** Aberto — fix em andamento.

**Sintoma:**
No extrato MP, um refund de dispute aparece como DUAS linhas separadas:
- "Débito por dívida Reclamações no Mercado Livre" com **-sale_amount** (ex: -4850,00)
- "Reembolso Envío cancelado" com **+fees_value** (ex: +531,95)

Net cash = -sale + fees = -(sale - fees) = -net.

Nosso `events_to_payment_movements` agrega o refund group em **1 CashMovement** com amount = refund_created + refund_fee + refund_shipping = -(sale - fees) = -4318,05. Quando o matcher tenta parear com a linha "-4850,00" do extrato, diff = 531,95 > tolerância → amount_diff. E a linha "+531,95" do extrato vira órfão (ou faz match errado com outra coisa).

~20 linhas com esse padrão em 141air jan/2026, somando ~R$ 1k+ em diffs + órfãos.

**Causa raiz:**
ERR-0003 motivou emitir 1 movimento por evento-natural (release vs refund). Mas o "evento natural" de refund no MP é dividido: MP separa o débito bruto (-sale) do crédito de taxas (+fees). Nossa representação colapsou ambos em 1 net.

**Fix:**
Emitir 2 movimentos no refund group:
1. `refund_created` sozinho, amount = signed_amount do evento (≈ -sale). Categoria `debito_divida_disputa`.
2. `refund_fee + refund_shipping` somados, amount = positivo (≈ +fees). Categoria `reembolso_disputa`.

Se qualquer componente faltar, emite o que tiver. Zero-NET é skipado.

**Lição:**
Granularidade do extrato dita granularidade do movimento interno. Mesmo que "refund" seja conceitualmente 1 transação de negócio, o MP o registra em 2 linhas — nossa reconciliação tem que espelhar. Futuros bugs: qualquer vez que o extrato tiver N linhas para 1 payment-event, emitir N movimentos.

**Arquivos envolvidos:** `app/services/reconciliation.py::events_to_payment_movements`, `testes/integration/test_refund_split_movements.py` (novo)

---

## ERR-0008 — Same-day release+refund net zero gera órfãos
**Descoberto:** 2026-04-16 após ERR-0007 revelar órfãos ligados a cancelamentos same-day.
**Status:** Aberto.

**Sintoma:**
Payments com sale+refund no mesmo dia (kit split, cancelamento imediato):
- Extrato: nenhuma linha (MP faz o netting interno).
- Sistema: 3 CashMovements — release (+net), refund_debit (-sale), refund_fee (+fees). Soma = 0.

Ex: 141air jan/2026, payments 143762867120 e 143815855230, ambos com sale +R$5000 e refund -R$5000 em 2026-01-28. 6 órfãos totalizando net = R$ 0.

**Causa raiz:**
`events_to_payment_movements` emite release e refund como entidades independentes. Quando ambos ocorrem no mesmo dia E somam zero, é um wash contábil que o extrato não exibe — mas nossa reconciliação mostra 3 orphans.

**Fix:**
Se release_group + refund_debit + refund_fee do MESMO pid, MESMO dia, somam ≈ 0 (<= tolerance), skipar todos os 3 movimentos.

**Lição:**
MP extrato é o reflexo real de caixa. Quando algo não aparece no extrato MAS existe em eventos, checar se é um wash contábil antes de criar órfão.

**Arquivos envolvidos:** `app/services/reconciliation.py::events_to_payment_movements`, `testes/integration/test_same_day_wash.py` (novo)

---

## ERR-0009 — load_mp_expenses exclui o primeiro dia do período
**Descoberto:** 2026-04-16, investigando órfãos `pagamento_conta` com mp_expense existente.
**Status:** Aberto.

**Sintoma:**
Linhas mp_expenses com `date_approved='2026-01-01'` (texto date-only) não eram carregadas quando se chama `load_mp_expenses(period_start='2026-01-01')`. 141air jan/2026 perde 5 rows, incluindo 3 `bill_payment` de R$ 2.3k.

**Causa raiz:**
`app/services/reconciliation.py::load_mp_expenses` usa `.gte("date_approved", f"{period_start}T00:00:00")`. A coluna `date_approved` é `text` com valores tipo `"2026-01-01"` (length 10, sem time). Comparação lexicográfica: `'2026-01-01' < '2026-01-01T00:00:00'` (prefix match + string mais curto vence). Resultado: rows do primeiro dia do período ficam fora.

**Fix:**
Remover sufixo `T00:00:00`/`T23:59:59` — usar `period_start` e `period_end` direto como strings `YYYY-MM-DD`. Comparação text funciona corretamente nesse formato.

**Lição:**
Ao comparar texto date-only vs date-time, o sufixo de time quebra silenciosamente. Em queries PostgREST com colunas text, sempre comparar com o MESMO formato que a coluna armazena.

**Arquivos envolvidos:** `app/services/reconciliation.py::load_mp_expenses`, `testes/unit/test_load_mp_expenses_boundary.py` (novo)

---

## ERR-0010 — Refund_created amount diverges from extrato debito (dispute interest/fees)
**Descoberto:** 2026-04-16, payment-level inspection do gap residual de débitos.
**Status:** Resolvido em 2026-04-16. `align_refund_created_with_extrato()` em `reconciliation.py` substitui o sistema refund_debit por uma movement por linha extrato `debito_divida_disputa` (mesma data + amount), tratando o extrato como source of truth do caixa MP. Coberto por `testes/integration/test_dispute_extrato_alignment.py` (5 testes).

**Sintoma:**
9 amount_diffs em 141air jan/2026 com pattern `Débito por dívida Reclamações no Mercado Livre`. Sistema emite `refund_created.signed_amount = -transaction_amount`, mas extrato mostra debito MAIOR (ou MENOR) por R$ 5–190.

Exemplos:
- pid=140915607218: ext -119.37, sys -111.60 (diff +7.77)
- pid=141555524687: ext -162.92, sys -144.90 (diff +18.02)
- pid=140240998217: ext -458.65, sys -647.88 (diff -189.23 — sys mais negativo!)
- pid=137614895655: ext -437.45, sys -369.55 (diff +67.90)

**Causa raiz:**
`processor._process_refunded` calcula `estorno_receita = min(total_refunded, transaction_amount)`. Mas o débito real que MP cobra inclui:
- Interest/admin fee de disputa (~5-15% adicional sobre o sale_amount)
- Ou um valor parcial (quando MP "pre-refunda" parte como reembolso)

O delta sempre tem contraparte: `mp_expense reembolso_disputa[:rd]` reflete o valor real do credito MP, e `extrato debito` reflete o valor real do débito MP. Os dois balanceiam o net da disputa, mas line-by-line divergem do que o processor computou via `transaction_amount`.

Verificação per-pid (jan/2026, 14 pids):
- 13/14 pids têm `ext_per_pid_sum != sys_per_pid_sum` (diff R$ 5–315)
- Apenas 1 pid (141609371619) per-pid balanceado

**Fix:**
Em `reconciliation.events_to_payment_movements`, quando um pid tem:
- `refund_created` event AND
- extrato tem `debito_divida_disputa` line para o mesmo pid

Substituir `refund_created.signed_amount` pelo valor REAL do debito_divida_disputa do extrato (treating extrato como source of truth para movimento de caixa).

Quando o pid também tem `mp_expense reembolso_*`, o credit side já é dedup'd via `_FEE_REFUND_DEDUP_EXPENSE_TYPES`. Após a substituição, os dois lados batem com o extrato.

**Lição:**
Para movimentos financeiros derivados de `transaction_amount`, há cenários (disputa, IOF, FX) onde MP aplica ajustes que mudam o valor real do débito/crédito. Sempre que o extrato existe e diverge de `transaction_amount`, o extrato vence — ele é o ground truth do caixa.

**Arquivos envolvidos:** `app/services/reconciliation.py::events_to_payment_movements`, `testes/integration/test_dispute_extrato_alignment.py` (novo)

---

## ERR-0011 — Subscription FX/IOF drift (~3.5%)
**Descoberto:** 2026-04-16.
**Status:** Resolvido em 2026-04-16. Match aceita até 5% de drift relativo para `category="subscription"` via `PCT_TOLERANCE_BY_CATEGORY` em `reconciliation.match_movements`. Coberto por `testes/integration/test_subscription_iof_tolerance.py` (5 testes).

**Sintoma:**
3 amount_diffs em pagamentos de subscription estrangeira:
- Supabase 2026-01-08: ext -169.03 vs sys -163.31 (diff R$ 5.72, +3.5%)
- Claude.ai 2026-01-08: ext -569.25 vs sys -550.00 (diff R$ 19.25, +3.5%)
- Notion 2026-01-23: ext -131.94 vs sys -127.48 (diff R$ 4.46, +3.5%)

Total drift: ~R$ 29.43.

**Causa raiz:**
Subscriptions cobradas em USD/EUR são convertidas pelo MP para BRL no momento da liquidação. O extrato mostra o valor BRL pós-IOF (Imposto sobre Operações Financeiras, 3.5% atualmente para cartões internacionais). Nosso `mp_expense.amount` usa `transaction_amount` em BRL pré-IOF.

Diff é sistemático: 3.5% acima do valor sistema.

**Fix:**
Em `match_movements`, aceitar amount_diff como match quando:
- Ambas as movs tem `category == "subscription"`
- `abs(diff) / abs(min(ext.amount, sys.amount)) <= 0.05` (5% tolerance)

Alternativa: ingester poderia ajustar mp_expense.amount para o valor pós-IOF lendo do extrato. Mais correto mas exige duplo passo.

**Lição:**
Cobranças em moeda estrangeira têm gap entre `transaction_amount` (pre-conversion) e o débito final no extrato (post-FX + post-IOF). Para reconciliação, tolerância % por categoria > tolerância absoluta por linha.

**Arquivos envolvidos:** `app/services/reconciliation.py::match_movements`, `testes/integration/test_subscription_iof_tolerance.py` (novo)

---

## ERR-0012 — by_admin/refunded pids sem extrato geram orphan_system fantasma
**Descoberto:** 2026-04-16.
**Status:** Resolvido em 2026-04-16. `align_refund_created_with_extrato()` suprime release+refund movements para qualquer pid com `refund_created` em events mas ZERO presença em extrato (caso phantom). Para refund movements sem `debito_divida_disputa` correspondente no extrato, suprime apenas o refund (release group preservado). Coberto pelos mesmos testes de ERR-0010 (`test_no_extrato_debit_means_suppress_refund_movements`, `test_pid_without_extrato_dispute_keeps_refund_when_other_extrato_exists`).

**Sintoma:**
Pids 140688038213 (by_admin) e 143909170600 (refunded) tem refund_created/refund_fee/refund_shipping em payment_events mas ZERO linhas no extrato. Reconciliação mostra orphans:
- 140688038213: -355.94 debito + 42.71 reembolso (sys orphans)
- 143909170600: 31.60 liberacao + -45.90 debito + 61.29 reembolso (sys orphans)

Para 143909170600, há também `refund_shipping +46.99` mas `shipping_charged` é 0 — ou seja, refunded MORE shipping que foi cobrado.

**Causa raiz:**
Para `status_detail = by_admin` (refund administrativo) e alguns `status_detail = refunded`, MP processa o refund internamente sem materializar no extrato do seller (a conta nem foi creditada nem debitada). Mas o ML API retorna o payment como refunded, então nosso processor cria refund_created baseado em `transaction_amount`.

Resultado: sistema tem movimentos de caixa para algo que MP nunca executou.

Para 143909170600 (status_detail=refunded same-day), há também o problema de `refund_shipping > shipping_charged`, que viola a invariante de "refund nunca excede o cobrado".

**Fix:**
Em `events_to_payment_movements`, quando o pid tem `refund_created` mas o extrato NÃO tem nenhuma linha `debito_divida_disputa` para o mesmo pid, suprimir release+refund movements (não emitir nenhum). Isso requer passar a lista de pids com extrato dispute.

Adicionalmente, validar `refund_shipping <= |shipping_charged|` no processor (warning + cap).

**Lição:**
Status do payment ML não garante materialização no caixa MP. Eventos sem contraparte no extrato são fantasmas; reconciliação deve tratar extrato como ground truth.

**Arquivos envolvidos:** `app/services/reconciliation.py::events_to_payment_movements`, `testes/integration/test_phantom_dispute_movements.py` (novo)

---

## ERR-0013 — bonus_envio no extrato sem evento sistema
**Descoberto:** 2026-04-16.
**Status:** Resolvido em 2026-04-16. Causa raiz: ML credita o `Bônus por envio` no extrato em janeiro, mas o `expense_classifier` capturou o evento como `cashback` (Bonificação Flex) datado de dezembro. Dois fixes: (1) `_expense_type_to_category` mapeia `cashback → bonus_envio`; (2) `load_mp_expenses_for_pids` busca mp_expenses fora do período pelo `payment_id`, e `expenses_to_movements` aceita `extrato_date_overrides` para remapear a data do mp_expense para a data do extrato. Resultado: bate exato (R$ 10.90), daily_diff_max = R$ 0.00.

**Sintoma:**
1 orphan_extrato em 141air jan/2026:
- pid=139026984141, 2026-01-07, +R$ 10.90, "Bônus por envio Mercado Envios"

Sistema não tem nenhum evento ou mp_expense para este pid.

**Causa raiz:**
ML paga bônus de envio para algumas vendas (subsídio para Mercado Envios). O extrato_ingester tem regra para `bonus_envio` (linha 167 em `_EXTRATO_RULES`), mas para pids que JÁ existem em `payment_ids_in_db` E que não estão na lista de tipos "distinct cash events" (linhas 1135-1139), o bonus é skipado como "already_covered".

`bonus_envio` ESTÁ na lista de distinct cash events (linha 1138), então deveria ser ingerido. Pode ser que o pid 139026984141 não esteja em `payment_ids_in_db` (era apenas um ref do bônus, sem venda real), e portanto a regra não aplicou.

**Fix:**
Investigar por que extrato_ingester não capturou. Provável: ref_id 139026984141 sem payment, sem composite key, mas extrato_ingester só ingere se `_should_ingest_uncovered` retornar True. Verificar regra para `bonus_envio` standalone.

**Impacto:** R$ 10.90 sozinho. Baixo; mas pattern pode aparecer em outros sellers.

**Lição:**
Bônus/incentivos da ML chegam no extrato sem necessariamente ter um payment associado. extrato_ingester deve sempre ingerir bonus_envio, independente de payment exists.

**Arquivos envolvidos:** `app/services/extrato_ingester.py`

---

## ERR-0014 — Release bpp_refunded mascarado por `Entrada de dinheiro`
**Descoberto:** 2026-04-16, durante reconciliação 141air fev/2026
**Status:** Resolvido em 2026-04-16. Fix em `align_refund_created_with_extrato` (case 4). Detecta pids com `group='release'` + `status_detail ∈ {bpp_refunded, refunded}` cujo footprint no extrato é unicamente `entrada_dinheiro` e suprime todos os movs de evento (release + refund_debit + refund_fee).

**Sintoma:**
Orphan_sistema `liberacao +R$ 1.251,70` para pid 144531559071 com `status_detail='bpp_refunded'`. Extrato tem apenas a linha `Entrada de dinheiro +1.251,70` para o mesmo ref (o mp_expense `entrada_dinheiro` já carrega o caixa real).

**Causa raiz:**
Para pagamentos `bpp_refunded` (Programa de Proteção do Mercado Envios), MP compensa o seller via "Entrada de dinheiro" em vez de "Liberação de dinheiro". O event ledger ainda contém sale_approved + fee_charged + shipping_charged (criados pelo processor quando o payment ainda estava aprovado), que geram um release group movement no reconciliador. Isso duplica o caixa já capturado pelo mp_expense.

**Fix:**
Novo case 4 em `align_refund_created_with_extrato`: quando a única categoria extrato do pid é `entrada_dinheiro` e o sistema tem um release group com status_detail bpp_refunded/refunded, suprimir release + refund movements.

**Lição:**
Nem todo release event vira cash. Para bpp_refunded, o extrato usa entrada_dinheiro como forma de compensação — o event ledger precisa ser alinhado com o ground truth do extrato.

**Arquivos envolvidos:** `app/services/reconciliation.py::align_refund_created_with_extrato`

---

## ERR-0015 — Duplicate/orphan de `cashback` vs extrato
**Descoberto:** 2026-04-16, durante reconciliação 141air fev/2026
**Status:** Resolvido em 2026-04-16. Fix em `filter_stale_mp_expenses` — drop cashback quando há um mp_expense não-cashback pro mesmo ref+valor (duplicata) OU quando o ref não aparece no extrato (MP-internal).

**Sintoma:**
Vários orphans cruzados envolvendo `cashback`:
- Duplicate: ref tem `cashback` do expense_classifier AND `bonus_envio` do extrato_ingester → 1 orphan_system.
- MP-internal: ref tem `cashback` mas não aparece no extrato → orphan_system.
- Extrato com outra categoria: extrato tem "Dinheiro recebido Proteção Mercado Envios Full" ou "Liberação de dinheiro" mas classifier capturou como `cashback` → sem match na pass 1 (categorias divergem).

**Causa raiz:**
`expense_classifier.py` emite `cashback` para qualquer money_transfer na branch=Cashback, mas o extrato lista o mesmo evento com tx_type variado: "Bônus por envio" (→ bonus_envio), "Dinheiro recebido Full" (→ dinheiro_recebido), "Liberação de dinheiro" (→ liberacao). O tradutor estático `_expense_type_to_category["cashback"] = "bonus_envio"` só cobre um desses casos.

**Fix:**
`filter_stale_mp_expenses` agora aceita `extrato_pids` e drop cashback em 2 casos; caso 3 (match via ref+amount na pass 2 do matcher) é preservado.

**Lição:**
Categoria do classifier não é 1:1 com categoria do extrato. Quando API e extrato divergem no naming, confiar no matcher pass 2 (ref+amount) em vez de forçar translation estática.

**Arquivos envolvidos:** `app/services/reconciliation.py::filter_stale_mp_expenses`

---

## ERR-0016 — Step (c) do extrato_ingester pulava tipos complementares
**Descoberto:** 2026-04-16, durante reconciliação 141air fev/2026
**Status:** Resolvido em 2026-04-16. Fix em `extrato_ingester.py` — step (c) agora só pula quando o expense_type NÃO está em `_COMPLEMENTARY_EXPENSE_TYPES`.

**Sintoma:**
Para refs que já tinham mp_expense de algum tipo em mês anterior (ex: `dinheiro_retido` em jan), linhas NOVAS do extrato em fev (ex: `reembolso_disputa` no mesmo ref) eram puladas como "already_covered". Resultado: 13 linhas de `reembolso_disputa` / `dinheiro_retido` / `debito_envio_ml` orfanadas em fev/2026.

**Causa raiz:**
Step (c) checava `if ref_id in expense_ids_in_db: skip`. `expense_ids_in_db` contém TODOS os refs com expense_captured events (de qualquer mês, qualquer tipo). A intenção original era evitar duplicata API↔extrato, mas o efeito colateral era pular eventos complementares legítimos (múltiplas linhas do mesmo dispute spread em meses diferentes).

**Fix:**
Extrai lista `_COMPLEMENTARY_EXPENSE_TYPES` (dispute groups, cancelamentos, estornos) e isenta desses tipos do skip de step (c). O check da step (a) (composite key) continua evitando duplicata real.

**Lição:**
Skip por ref plain é grosseiro quando um mesmo ref legitimamente recebe múltiplos tipos de evento ao longo do tempo. Skip deve ser por (ref, expense_type) = composite key.

**Arquivos envolvidos:** `app/services/extrato_ingester.py`

---

## ERR-0017 — Classifier `pix_enviado` capturava `Reembolso de Pix enviado`
**Descoberto:** 2026-04-16, durante reconciliação 141air fev/2026
**Status:** Resolvido em 2026-04-16. Fix em `extrato_ingester.EXTRATO_CLASSIFICATION_RULES`: nova regra `reembolso_pix_enviado` (income) antes de `pix_enviado` (expense).

**Sintoma:**
Linha do extrato "Reembolso de Pix enviado" (+R$ 18.574,41) era classificada como `pix_enviado` (expense, mesmo sinal negativo da linha original do pix), gerando duplo debit em mp_expenses e amount_diff de R$ 37.148,82.

**Causa raiz:**
Regra `("pix enviado", "pix_enviado", "expense", None)` matchava substring — tanto "Pix enviado" quanto "Reembolso de Pix enviado" disparavam a mesma classificação.

**Fix:**
Regra mais específica antes: `("reembolso de pix enviado", "reembolso_pix_enviado", "income", None)`. Primeira match wins → refund corretamente classificado como income.

**Lição:**
Regras baseadas em substring precisam ser ordenadas da mais específica para a menos. Adicionar pattern especializado SEMPRE que um novo tx_type aparecer no extrato.

**Arquivos envolvidos:** `app/services/extrato_ingester.py::EXTRATO_CLASSIFICATION_RULES`

---

## ERR-0018 — Direção hardcoded em categorias reversíveis
**Descoberto:** 2026-04-16, durante reconciliação 141air fev/2026
**Status:** Resolvido em 2026-04-16. Fix: `_SIGN_DRIVEN_EXPENSE_TYPES` (liberacao_cancelada, dinheiro_recebido_cancelado) lê direction do sinal do CSV no momento da classificação.

**Sintoma:**
Linha extrato "Liberação de dinheiro cancelada" com amount **+46,90** (positivo — seller recebeu de volta) era gravada com `signed_amount=-46,90` porque a regra tinha `direction="expense"` hardcoded.

**Causa raiz:**
Categorias de cancelamento/reversão podem ter qualquer sinal — depende do que é cancelado. Hardcodar direction assume um dos casos e quebra o outro.

**Fix:**
Nova constante `_SIGN_DRIVEN_EXPENSE_TYPES`. Durante a primeira-passagem de classificação, direction é sobrescrita a partir do sinal do `tx["amount"]`.

**Lição:**
`direction` deve ser intrínseco ao tipo OU explícito do sinal CSV. Nunca "hardcoded porque geralmente vem assim".

**Arquivos envolvidos:** `app/services/extrato_ingester.py`

---

## ERR-0019 — Release group com fees extras do MP (amount divergente do extrato)
**Descoberto:** 2026-04-16, durante reconciliação 141air mar/2026
**Status:** Resolvido em 2026-04-16. Novo case 5 em `align_refund_created_with_extrato`: quando extrato tem exatamente 1 linha `liberacao` para o pid e o release group do sistema tem amount diferente, override para o amount do extrato.

**Sintoma:**
Amount_diff em 141air mar/2026:
- pid 148949991586, 2026-03-18, extrato liberacao +R$ 10,24 vs sistema release group +R$ 16,70, diff R$ 6,46.

Só 1 linha no extrato para o ref (apenas "Liberação de dinheiro"). Mas o event ledger (sale_approved + fee_charged + shipping_charged) calculou net +R$ 16,70. MP deduziu ~R$ 6,46 em taxa/antecipação fora do event ledger.

**Causa raiz:**
Event ledger computa liberacao como sale - fees - shipping. Mas MP ocasionalmente deduz taxas adicionais (antecipação, IOF, ajuste tributário) que não aparecem como `fee_charged`/`shipping_charged` na ML API. Resultado: sistema superestima o release.

**Fix:**
Case 5 em `align_refund_created_with_extrato`:
```python
if group == "release" and len(ext_liberacao_by_pid.get(pid, [])) == 1:
    ext_line = ext_liberacao_by_pid[pid][0]
    if mv.amount != ext_line.amount:
        # override sys amount with extrato amount
```

Mantém 1-pra-1: só age quando há exatamente 1 linha `liberacao` positiva no extrato para o pid (evita conflitar com disputas multi-linha).

**Lição:**
Event ledger é fonte canônica do que ML reportou via API, mas MP pode aplicar deduções silenciosas. Extrato é o ground truth final de caixa — align 1-pra-1 quando diverge.

**Arquivos envolvidos:** `app/services/reconciliation.py::align_refund_created_with_extrato`

---

## ERR-0020 — "Pagamento com código QR Pix cancelado" mal classificado
**Descoberto:** 2026-04-16, durante reconciliação 141air mar/2026
**Status:** Resolvido em 2026-04-16. Nova regra classifier + sign-driven.

**Sintoma:**
Linhas "Pagamento com código QR Pix cancelado" eram classificadas como `_CHECK_PAYMENTS` (regra `("pagamento com", _CHECK_PAYMENTS, ...)`). Como o ref_id é de um payment existente, o check-payments pulava a linha (`skipped_internal`) — o cancelamento nunca entrava em mp_expenses.

Em 141air mar: 2 linhas cancelada (+R$ 24,70 + R$ 49,30) órfãs em extrato.

**Causa raiz:**
Regra `("pagamento com", _CHECK_PAYMENTS, "income", None)` em `EXTRATO_CLASSIFICATION_RULES` matcha substring. "Pagamento com código QR Pix cancelado" cai nessa regra antes que "cancelado" seja considerado.

**Fix:**
Regra mais específica antes:
```python
("pagamento com codigo qr pix cancelado", "pagamento_qr_cancelado", "income", None),
```
+ adiciona `pagamento_qr_cancelado` em `_SIGN_DRIVEN_EXPENSE_TYPES` (pode reverter crédito ou débito) e em `_COMPLEMENTARY_EXPENSE_TYPES`.

**Lição:**
Toda nova variação de TRANSACTION_TYPE no extrato precisa de inspeção: se é reversão/cancelamento, provavelmente precisa de pattern específico + sign-driven. ERR-0017, ERR-0018 e ERR-0020 seguem o mesmo pattern de correção.

**Arquivos envolvidos:** `app/services/extrato_ingester.py::EXTRATO_CLASSIFICATION_RULES`, `_SIGN_DRIVEN_EXPENSE_TYPES`, `_COMPLEMENTARY_EXPENSE_TYPES`

---

## Template (copiar pra novo erro)
```
## ERR-NNNN — Título curto
**Descoberto:** YYYY-MM-DD, contexto breve
**Status:** Aberto | Fix em PR #X | Resolvido no commit Y

**Sintoma:** o que o teste/log/reconciliação mostra
**Causa raiz:** por que está acontecendo
**Fix:** como vai ser corrigido (ou foi)
**Lição:** padrão genérico a lembrar
**Arquivos envolvidos:** paths
```
