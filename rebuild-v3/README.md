# Rebuild V3 — Conciliador ML/MP ↔ Conta Azul

> Base de conhecimento da rodada de junho/2026. Captura TODO o entendimento, decisões,
> arquitetura-alvo, forense de bugs, harness de teste e progresso de implementação.
> **Não é o doc padrão** (`docs/` é a doc da V2/V3 antiga). Esta pasta é o plano do rebuild.

## O que é este sistema

App de **conciliação automática** entre Mercado Livre / Mercado Pago e o ERP Conta Azul.
Para cada venda no ML deveria lançar no CA: receita (bruto), despesa-comissão, despesa-frete,
e dar baixa quando o ML libera o dinheiro. Pagamentos sem order (boleto, PIX, DARF, SaaS,
cashback) são classificados em `mp_expenses`. Há também um dashboard de vendas/metas que
**já funciona** e está FORA do escopo deste rebuild.

O conciliador foi uma "tentativa frustrada" — o dono queria caixa batendo com o extrato e
DRE por competência, e nunca fechou. Esta rodada diagnosticou POR QUÊ e começou a consertar.

## Índice

| Doc | Conteúdo |
|---|---|
| [01-objetivo-e-veredito.md](01-objetivo-e-veredito.md) | O objetivo real, e o veredito: o que é factível vs utopia |
| [02-arquitetura-alvo.md](02-arquitetura-alvo.md) | Os 2 livros, baixa extrato-dirigida, 3 datas, 4 portões, pontes, fonte-da-verdade por relatório |
| [03-forense-e-bugs.md](03-forense-e-bugs.md) | Forense dos subsistemas + lista de bugs com dinheiro colado |
| [04-harness.md](04-harness.md) | O juiz + harness real-code dry-run: como funciona, como rodar, segurança |
| [05-fases-progresso.md](05-fases-progresso.md) | As 7 fases, o que foi feito/verificado, o que falta |
| [06-reconciliacao-contabil.md](06-reconciliacao-contabil.md) | Modelo: caixa vs DRE, as identidades, double-count, cross-month, limites de dado |
| [07-dados-resultados.md](07-dados-resultados.md) | Inventário de dados (extratos, caches) + números de baseline reais |
| [08-decisoes-proximos-passos.md](08-decisoes-proximos-passos.md) | Decisões de negócio pendentes + próximos passos |

## TL;DR (resumo executivo)

**Veredito:** não é utopia. **Caixa fecha 100% ao centavo** (ancorado no extrato) e **vendas
lançam automático** (defasado D+N). A utopia era bater bit-a-bit por *competência* contra o
*painel ML* em tempo real — isso o dado não permite (devolução diferida, granularidade,
taxonomia instável). O que faltou nunca foi viabilidade: foi **o juiz de caixa que nunca
existiu**, ancorar valor no extrato (não na API), e terminar a long tail.

**Provado nesta rodada (via API ao vivo, read-only, 2 sellers reais):**
- Âncora do extrato: ✓ perfeita ao centavo em **10 extratos** (jan-mai × 141air + net-air).
- Vendas reconciliam: 141air jan **0,08%**; net-air jan **0,43%**/R$534k.
- Cobertura de classificação: **100%** (0 linhas OTHER) após Fase 7.

**Feito + verificado:** harness real-code dry-run (roda o código REAL, zero escrita no CA),
juiz de reconciliação, e 6 fases de correção (ver 05).

**Falta:** Fase 3-full (baixa extrato-dirigida + cross-month stateful), Fase 4-full (fee
bidirecional, precisa fixture), Fase 5 (pontes), Fase 6 (DRE D+1 produção). + cutover ao vivo.

## Branch

Todo o código está em `fix/conciliador-reconciliation`. Artefatos de teste em `testes/harness/`
e `testes/judge_caixa_jan2026.py`. Spec em `docs/superpowers/specs/`.
