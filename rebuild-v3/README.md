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

**Veredito:** não é utopia, e **o núcleo do conciliador calcula CERTO (~0,1% de erro).** A
"tentativa frustrada" não era inviabilidade — faltava o **juiz de caixa** (que nunca existiu),
a **cobertura** (gaps de classificação), o **alinhamento de data** caixa↔CA, e as **pontes**.
Tudo construído/corrigido nesta rodada. A utopia continua sendo bater bit-a-bit por *competência*
contra o *painel ML* em tempo real (devolução diferida, granularidade, taxonomia instável) — mas
isso não é o objetivo: caixa fecha contra o **extrato** (factível).

**Provado nesta rodada (via API ao vivo, read-only, 2 sellers reais, jan-mai):**
- **Âncora** do extrato: ✓ ao centavo em **10 extratos** (jan-mai × 141air + net-air), com
  continuidade de saldo (FINAL→INITIAL).
- **Cobertura: 100%** (0 linhas OTHER) nos 10 meses — toda linha classificada.
- **Erro de valor real do núcleo: ~0,05-0,1%** (141air R$324; net-air R$274 / R$2,2M). O "1%"
  aparente era 96% **boundary** (borda da janela jan-mai), não erro de cálculo.
- **Ponte caixa↔DRE fecha:** Δ recebíveis a liberar soma +R$377 em 5 meses (0,1%).

**As 7 fases — todas com artefato implementado + verificado (dry-run):**
0 Juiz ✅ · 1 Taxa oculta ✅ · 2 Chave composta ✅ · 3 Data estorno + baixa extrato-dirigida
(core) ✅ · 4 Refund parcial ✅ · 5 Pontes ✅ · 6 DRE competência ✅ · 7 Cobertura 100% ✅.

**Falta (camada AO VIVO, depende do usuário):** wiring de produção (baixa→CA real, produtizar
DRE/pontes como endpoints), Fase 4 fee bidirecional (precisa fixture release report), ingester
3 layouts, 4 decisões de negócio, e o cutover (deploy + escrita CA habilitada). Ver 08.

**15 commits** em `fix/conciliador-reconciliation`.

## Branch

Todo o código está em `fix/conciliador-reconciliation`. Artefatos de teste em `testes/harness/`
e `testes/judge_caixa_jan2026.py`. Spec em `docs/superpowers/specs/`.
