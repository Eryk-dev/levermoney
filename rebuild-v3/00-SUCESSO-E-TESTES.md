# 00 — SUCESSO E TESTES (LEIA ANTES DE MEXER EM QUALQUER CÓDIGO)

> Este é o documento mais importante. Define **o que é sucesso**, **a régua** que mede, e
> **as diretrizes de teste** pra você não mexer no código e regredir sem perceber.
> Regra de ouro: **nenhuma mudança vale se a régua piorar.**

---

## 1. O objetivo, em uma frase

**Os valores lançados no Conta Azul devem bater com o extrato do Mercado Pago com diferença mínima.**
Nada mais importa. Tecnicismo é meio, não fim.

---

## 2. A RÉGUA OFICIAL (o único número que decide)

```bash
python3 -m testes.harness.gabarito            # 141air + net-air
python3 -m testes.harness.gabarito 141air     # só um
```

Saída (o que olhar):

```
# 141air   (N refs de venda c/ caixa in-window)
  >> ERRO REAL (valores errados, ambas pernas) = R$ X   ← O NÚMERO QUE IMPORTA
     Σ|diff| total (inclui timing cross-window)  = R$ Y
```

- **ERRO REAL** = soma dos erros de valor em vendas onde **as duas pernas** (liberação + devolução)
  estão na janela. É o "os valores estão errados de verdade". **É ESTE que você minimiza.**
- **Σ|diff| total** = inclui também o timing cross-window (venda na borda jan/mai). Sobe/desce
  com o recorte, não só com bug. Use como detector sensível de regressão.

**Menor = melhor. 0 = perfeito.**

---

## 3. NÚMEROS ATUAIS (baseline — jun/2026, após fix1/2/3b)

| Seller | ERRO REAL | Σ\|diff\| total | refs com erro |
|---|--:|--:|--:|
| 141air | **R$ 3.990** | R$ 6.748 | 30 / 1565 |
| net-air | **R$ 65.859** | R$ 90.696 | 495 / 22830 |

Contexto: 141air fatura ~R$511k bruto em 5 meses → erro real ~0,8%. net-air proporcional.
Antes dos 3 fixes era ~3-4x pior (141air ~R$20k full-ledger). **Os fixes funcionaram.**

---

## 4. METAS (o que é SUCESSO)

| Nível | ERRO REAL (141air) | Como chegar |
|---|--:|---|
| 🟡 hoje | R$ 3.990 (0,8%) | fix1/2/3b feitos. Resto = cauda de disputa (chargeback onde a contabilidade do ML não fecha) |
| 🟢 meta cirúrgica | < R$ 2.000 | só se achar bug REAL novo que o teste prove (gabarito CAI). Não force. |
| 🟢🟢 meta final (ao centavo) | ~R$ 0 | **baixa extrato-dirigida** (`baixas_extrato_runner` — já codada). O valor da baixa vira o do extrato → o resíduo vira ajuste explícito. É REDESIGN de execução, não mais um fix de regra. |

**Tradução:** o erro real de hoje (R$3.990) é **cauda de disputa irredutível cirurgicamente** —
o próprio outro chat provou que dedup/remoção dela REGRIDE. **O zero ao centavo vem da baixa
extrato-dirigida, não de mais mexidas nas regras.** Parar de caçar a cauda manualmente.

---

## 5. DIRETRIZES DE TESTE (rode SEMPRE, antes E depois de qualquer mudança)

### A bateria verde (tem que passar 100% sempre)
```bash
python3 -m testes.harness.test_rules           # regras de classificação do extrato
python3 -m testes.harness.test_baixas_extrato  # baixa extrato-dirigida
python3 -m pytest testes/finalizacao/ -q       # fee bidirecional, DRE, pontes, config
python3 -c "import app.main"                    # app sobe sem erro
```
**Qualquer um vermelho = você quebrou algo. Reverta antes de continuar.**

### A régua (o número que decide se a mudança AJUDA)
```bash
python3 -m testes.harness.gabarito > /tmp/antes.txt   # ANTES de mexer
# ... faz a mudança ...
python3 -m testes.harness.gabarito > /tmp/depois.txt  # DEPOIS
diff /tmp/antes.txt /tmp/depois.txt
```
- ERRO REAL **caiu** → a mudança ajudou. ✅ mantém.
- ERRO REAL **subiu** → a mudança regrediu. ❌ **REVERTE** (`git checkout <arquivo>`).
- ERRO REAL **igual** mas testes verdes → mudança neutra (ok se necessária por outro motivo).

### Diagnóstico (pra entender ONDE está o erro)
```bash
python3 -m testes.harness.run 141air dre       # DRE por competência
python3 -m testes.harness.run 141air ponte      # pontes caixa↔DRE / DRE↔painel ML
python3 -m testes.harness.saldo 141air          # saldo dia a dia (extrato vs CA)
python3 testes/judge_caixa_jan2026.py           # âncora do extrato + cobertura (OTHER=0)
```

---

## 6. REGRA DE OURO (pra não mexer erroneamente)

1. **Sempre rode a régua ANTES de mexer.** Anote o número.
2. **Mudança só vale se a régua CAI ou fica igual.** Subiu? Reverte. Sem exceção.
3. **Cobertura tem que continuar 100%** (`judge` mostra `OTHER=0`). Se uma regra nova fizer
   linha cair em OTHER, você criou um buraco.
4. **Não caçar a cauda de disputa com dedup/remoção** — o outro chat já provou que regride
   (fix3 +32k, noet pior). A cauda fecha com baixa extrato-dirigida, não com regra.
5. **Não tocar `processor._extract_processor_charges` / `charges_details`** sem rodar a régua —
   é o coração do cálculo de fee. Bug aqui propaga pra tudo.

---

## 7. O QUE CADA MUDANÇA "BOA" PARECE (exemplos dos 3 fixes que funcionaram)

| Fix | O que era | Por que AJUDOU (régua caiu) |
|---|---|---|
| fix1 (processor) | estorno_taxa revertia o frete que o ML reteve | reverter só `amounts.refunded` (o que o ML devolveu de fato) |
| fix2 (ingester) | "Débito por dívida Envio" não-lançado = gap | ingerir essa linha (clawback de frete) |
| fix3b (ingester) | processor estorno_taxa + ingester contavam o refund 2x | dedup direcionado, preservando "Reembolso Reclamações" |

Padrão das boas: **eliminam double-count ou gap específico, sem mexer no que já bate.** Cirúrgico.

---

## 8. SUCESSO vs FALHA — checklist final

✅ **SUCESSO** (pode parar, está bom):
- bateria verde 100%
- `judge` OTHER=0 (cobertura 100%)
- gabarito ERRO REAL não subiu vs baseline
- (ao centavo) baixa extrato-dirigida ligada em prod e o caixa do CA = extrato

❌ **FALHA** (reverte):
- qualquer teste vermelho
- OTHER > 0 (criou buraco de cobertura)
- gabarito ERRO REAL subiu
- mexeu em charges_details/estorno sem rodar a régua

---

## 9. Onde está cada coisa

- Régua: `testes/harness/gabarito.py`
- Bateria: `testes/harness/test_rules.py`, `test_baixas_extrato.py`, `testes/finalizacao/`
- Diagnóstico: `testes/harness/{run,saldo}.py`, `testes/judge_caixa_jan2026.py`
- O fechamento ao centavo: `app/services/baixas_extrato_runner.py` (falta ligar ao CA — ver doc 08)
- Contexto completo: os outros docs em `rebuild-v3/`
