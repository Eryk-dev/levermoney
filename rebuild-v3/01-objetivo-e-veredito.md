# 01 — Objetivo real e veredito de viabilidade

## O objetivo do dono (evoluiu na conversa)

1. Primeiro: "software que lê todos os relatórios extraíveis (API + extrato CSV) e faz a
   conciliação + lançamento automático no Conta Azul, pelo menos das vendas ML + comissão".
2. Depois: "fechar o **DRE 1 dia depois do fechamento do mês**, com vendas/devoluções corretas".
3. E crucialmente: "**o caixa precisa bater 100% também** — não posso acumular 1% de diferença
   por dia, no acumulado diverge muito".
4. E: "ver tudo dentro dos relatórios do **Conta Azul** — competência, vencimento e baixa
   corretamente registradas".
5. Dor central: "o duro é apresentar um número **diferente do painel do Mercado Livre** —
   no mínimo preciso saber explicar a divergência".

## O escopo completo (o sistema-alvo)

Ler **todos** os relatórios (Payments API + release report + VENDAS + extrato CSV), **conciliar
o caixa 100% ao centavo** contra o extrato, **lançar automaticamente no CA** vendas+comissão+
frete+estorno **e** os movimentos non-venda, fechar o **DRE por competência em D+1**, com
**fila de exceção** pequena/explícita que **trava** o fechamento em vez de acumular erro.

## Veredito: o que é factível vs utopia

### Factível (não é utopia)
- **Caixa 100% ao centavo:** o extrato é a âncora (traz `PARTIAL_BALANCE` = saldo absoluto
  corrido + `INITIAL/FINAL_BALANCE`). Identidade diária `saldo_inicial + Σlinhas = saldo_final`
  fecha por construção. **Drift é impossível de esconder** porque você ancora no saldo
  absoluto, não em deltas. Provado: âncora bate ao centavo nos 10 extratos.
- **Vendas → CA automático:** tem chave de join (`REFERENCE_ID` da liberação == `payment.id`),
  casos finitos. Defasado D+N (o fee final só trava quando o release report liquida).
- **DRE D+1 por competência:** consequência do caixa fechado dia a dia.

### Utopia (limite do dado, aceitar)
- **Bater bit-a-bit por competência contra o painel ML em tempo real.** Impedido por:
  - **Competência ≠ caixa:** painel ML conta devolução no mês da VENDA; o DRE conta no mês do
    ESTORNO. Venda jan devolvida fev → ML põe em jan, contabilidade em fev. Documentado e
    intencional (REGRAS_NEGOCIO 11.13; ex NET-AIR R$62k diferidos).
  - **Granularidade:** 1 payment em disputa vira N linhas no extrato (retido→débito→reversão);
    a Payments API expõe 1 transição de status. Sem chave 1↔N determinística.
  - **Taxonomia instável:** `TRANSACTION_TYPE` é texto livre pt-BR ("Pix recebido João"), sem
    código. Cauda longa sempre exige humano → 100% AUTOMÁTICO de categorização é utopia.
  - **Tolerância R$0,10:** os próprios relatórios ML não amarram ao centavo entre si.

## As 3 definições de "100%" (sem ambiguidade)

| Definição | Atingível? |
|---|---|
| **100% de CAIXA** (saldo CA == extrato ao centavo) | ✅ automático, ancorado no extrato |
| **100% de LANÇAMENTO DE VENDAS** no CA | ✅ automático, defasado D+N |
| **100% de CATEGORIZAÇÃO sem nenhum humano jamais** | ⚠️ utopia só pela cauda non-venda nova; valor nunca erra (só o rótulo), e a cauda encolhe (cada caso novo vira regra) |

**O medo do "1%/dia acumulando" é infundado SE ancorar no extrato:** o ~1% não é erro de
valor, é cauda de CATEGORIA (qual conta) — o dinheiro entra cheio no caixa. Ou o dia fecha em
zero, ou a exceção aparece e trava. Não acumula.

## Por que o projeto travou (forense de git)

~3 semanas, último commit 4/mar. Morte por long tail + indecisão de arquitetura, NÃO por muro
teórico. Sintomas: flip-flops (competência mudou 3x na mesma linha; cashback 1.3.1↔1.3.4;
sinal de transfer corrigido no último commit) = regras decididas por tentativa-erro sem modelo
causal. Duas arquiteturas concorrentes (legacy engine XLSX que ancorava no extrato — conceito
CERTO — vs classifier V3 → mp_expenses) nunca convergiram; o legacy foi desligado por último
(commit 4a74335) sem o novo cobrir tudo. **E o juiz de caixa (reconciliação de valor) nunca
foi construído** — o sistema reportava "100%" que era COBERTURA (linha tem balde), não VALOR
(Σ CA == Σ extrato). Por isso parecia fechar e não fechava.
