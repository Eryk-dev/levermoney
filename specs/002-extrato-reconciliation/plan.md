# Plan 002 — Reconciliação Extrato

**Spec:** `specs/002-extrato-reconciliation/spec.md`
**Contract:** `specs/002-extrato-reconciliation/contracts/reconciliation.yml`
**Criado:** 2026-04-16

---

## Estratégia: SDD + TDD-no-unit

- **SDD no nível do sistema**: spec.md + contract.yml definem o QUE tem que ser verdade.
- **TDD no nível de unidade**: cada task começa com um teste falhando que verifica um ponto específico do spec.
- **Eval harness como gate**: `scripts/run_reconciliation.py` retorna um único número (% de cobertura). Toda PR deve mover esse número pra cima, nunca pra baixo.
- **Sistema de aprendizado**: `docs/reconciliation/ERRORS.md` guarda todo bug encontrado. Antes de tentar consertar qualquer coisa, consulta se já vimos esse sintoma.

## Ordem de execução

### Fase 0 — Fundação (este commit)
- [x] Apagar speckit 001 e testes defasados
- [x] Criar estrutura specs/002 + docs/reconciliation + scripts
- [x] Popular ERRORS.md com os 4 bugs já descobertos
- [x] Popular RUNS.md com baseline (56% créd / 86% déb — 141air jan)

### Fase 1 — Eval harness + gate (próximo)
- [ ] Implementar `scripts/run_reconciliation.py` lendo contract.yml
- [ ] Teste `testes/e2e/test_reconciliation_141air.py` que FALHA no baseline
- [ ] Wrapper `scripts/run_reconciliation.sh` + `scripts/run_tests.sh` com logging automático
- [ ] Primeiro `RUNS.md` entry gerado automaticamente

### Fase 2 — Derrubar bugs conhecidos (um por um, TDD)
Cada bug = 1 task = 1 teste novo (red) + 1 fix (green) + entry em ERRORS.md:

- [ ] ERR-0001: sinal invertido em `transfer_intra` (expense_classifier)
- [ ] ERR-0002: mp_expenses stale (`liberacao_nao_sync`, `qr_pix_nao_sync`) quando payment_id já está em payment_events
- [ ] ERR-0003: NET por payment vs extrato por data — arquitetura de CashMovement precisa emitir 1 movimento por evento (não 1 por payment)
- [ ] ERR-0004: naming mismatch `bill_payment` (mp_expenses) vs `pagamento_conta` (extrato classifier)

### Fase 3 — Classificador 100% coberto
- [ ] Teste property-based: todo tipo real dos 8 CSVs de extrato mapeia pra algo não-"other"
- [ ] Se retornar "other": quebra. Adicionar regra explícita ao `EXTRATO_CLASSIFICATION_RULES`.

### Fase 4 — Golden snapshots
- [ ] Curar ~30 payments reais cobrindo todos os edge cases (approved/accredited, approved/partially_bpp_refunded, charged_back/reimbursed, refunded/bpp_refunded, refunded/refunded, refunded/by_admin, cancelled)
- [ ] Para cada: snapshot fixo do output do processor (eventos gerados + payloads CA)
- [ ] Qualquer regressão quebra os testes

### Fase 5 — Invariantes ambientais
- [ ] Assertivas rodando no fim de daily_sync e backfill (flag `ASSERT_INVARIANTS=true`)
- [ ] I-1, I-3, I-4, I-5 do spec — se falhar, aborta e alerta

### Fase 6 — Promover gate e replicar pra outros sellers
- [ ] Quando 141air jan bate 99,5%: tornar teste bloqueante de CI
- [ ] Replicar pra net-air, netparts-sp, easy-utilidades, easypeasy
- [ ] Fevereiro também

## Princípios operacionais

1. **Log tudo.** Todo run de reconciliação → RUNS.md. Todo pytest → TEST_LOG.md. Toda decisão → DECISIONS.md. Todo bug → ERRORS.md. Isso não é opcional; é como escapamos do loop.
2. **Um teste por bug.** Nunca corrigir sem teste que falha antes. Se não consegue escrever o teste, não entendeu o bug.
3. **Barra só sobe.** Coverage % em RUNS.md só anda pra cima. Se cair: para tudo e investiga.
4. **Antes de consertar, consulta.** ERRORS.md é a memória permanente. Busca o sintoma antes.
5. **Spec é contrato.** Mudou o que é "correto"? Primeiro atualiza spec + contract.yml, depois o código.
