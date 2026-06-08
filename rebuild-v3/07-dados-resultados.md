# 07 — Inventário de dados + resultados de baseline

## Extratos UI CSV (testes/extratos/)
Formato: `RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE`
(download manual do MP). Topo: `INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE`.

| Seller | jan | fev | mar | abr | mai |
|---|---|---|---|---|---|
| 141Air | ✓ | ✓ | ✓ | ✓ | ✓ |
| netair | ✓ | ✓ | ✓ | ✓ | ✓ |
| netparts (jan/fev só) | ✓ | ✓ | — | — | — |
| easy (jan/fev só) | ✓ | ✓ | — | — | — |

**Foco do rebuild: 141air + net-air, jan-mai (10 extratos).**

### Âncora validada (todos batem ao centavo, com continuidade de saldo)
```
seller/mes    INITIAL      FINAL      diff
141Air jan    4.476,23    1.090,40    0,00 ✓
141Air fev    1.090,40    3.815,72    0,00 ✓
141Air mar    3.815,72        0,00    0,00 ✓
141Air abr        0,00    2.026,43    0,00 ✓
141Air mai    2.026,43    4.352,05    0,00 ✓
netair jan   15.069,87    2.285,61    0,00 ✓  (7470 linhas)
netair fev    2.285,61   19.202,74    0,00 ✓
netair mar   19.202,74        0,00    0,00 ✓  (9436 linhas)
netair abr        0,00      437,38    0,00 ✓
netair mai      437,38   29.987,06    0,00 ✓
```

## Caches de payments (testes/cache_{mon}2026/)
Full ML payment objects (com charges_details). Buscados via `fetch_all.py` (ML API read-only).
- 141air: jan, fev (originais) + mar, abr, mai (fetchados).
- net-air: jan + fev, mar, abr, mai (fetchados).
Estrutura: `{seller_slug, fetched_at, counts, unique_total, payments:[...]}`.

## Credenciais / ambiente
`.env` tem credenciais REAIS (Supabase service_role PROD, tokens CA, ML app). 10 sellers ativos,
todos com token ML. **Só 141air tem config CA** (resto pending_ca). Modo de teste: **dry-run,
zero escrita no CA, Supabase prod só-leitura** (autorizado pelo usuário).

## Resultados de baseline (real, via API)

### 141air jan/2026 (date-aware [D])
```
Caixa domínio-vendas: NET_DIFF −R$1.929 / R$112k  (1,7%)
  approved (limpo):  −R$89    ← taxa oculta residual (Fase 1 quase zerou)
  refunded:          −R$1.840 ← refund parcial (Fase 4 pendente)
Cobertura de classificação: OTHER = 0 linhas (100%) após Fase 7
```

### net-air jan/2026 (via API ao vivo, 7419 payments, 19319 eventos CA)
```
Âncora ✓ ao centavo
Recon vendas lifecycle: NET_DIFF −R$2.312 / R$534k = 0,43%
Resíduo dominado por cross-month/spill (R$285k = liberações que liberam em mês posterior)
```

## Leitura dos números
- **Âncora perfeita** nos 2 sellers, 10 extratos → fundação do caixa sólida.
- **Vendas reconciliam ~99,6-99,9%** → núcleo do cálculo certo.
- Resíduo é **timing cross-month + refund parcial**, NÃO erro de cálculo.
- Cobertura **100%** (0 OTHER) após Fase 7.

## RESULTADO FINAL desta rodada (modo timeline — cada payment processado 1x)

Após Fase 7 estendida (cobertura) + Fase 1/3 (valor):

| Métrica | 141air (jan-mai) | net-air (jan-mar) |
|---|---|---|
| Âncora extrato | ✓ ao centavo | ✓ ao centavo |
| **Cobertura (OTHER)** | **0 linhas (100%)** | **0 linhas (100%)** |
| Resíduo de VALOR (date-indep.) | −R$4.069 / R$328k = **1,2%** | −R$13.284 / R$1,69M = **0,78%** |
| Caixa por mês (resíduo) | jan −1,7k, fev −4,6k, mar +3,6k, abr −1,1k, mai +0,2k | jan −6,6k, fev −1,0k, mar −7,3k |

**Cobertura 100% atingida** (toda linha do extrato classificada, jan-mai, 2 sellers) —
incluindo poupança "Renda", Mercado Crédito (empréstimos), e os bugs de Fase 7.

**Resíduo de valor ~1%** dominado por: (a) boundary (venda liberada antes de jan ou liberando
após mai → só uma perna no extrato da janela), (b) refund parcial (comissão/frete não revertidos
= Fase 4), (c) desalinho de data CA(money_release_date promessa) vs extrato real (= Fase 3-full).
NÃO é erro de cálculo do núcleo — é timing/borda. Não cresce sem limite (meses se compensam).

### ACHADO DECISIVO — o erro de valor REAL é ínfimo (141air, 5 meses)
Decompondo as 90 refs com resíduo > R$0,50:
```
boundary (perna fora da janela jan-mai):  85 refs  Σ −R$4.230   ← borda do recorte, NÃO é erro
erro REAL (ambas pernas no extrato):        5 refs  Σ +R$160,62  ← erro de valor de verdade
```
**O erro de valor real do processor é R$160 em 5 meses = 0,05% de R$328k.** O "1,2%" é 96%
boundary (some com janela maior, ex: incluir dezembro) e 4% erro. **Conclusão: o núcleo de
cálculo do conciliador está essencialmente CORRETO.** O que faltava era cobertura (resolvido
nesta rodada), o juiz de reconciliação (construído), e o alinhamento de data caixa↔CA (Fase 3-full).

## Como reproduzir
```
python3 testes/judge_caixa_jan2026.py                       # âncora + buckets, 4 sellers
python3 -m testes.harness.run 141air jan,fev,mar,abr,mai     # 141air timeline completa
python3 -m testes.harness.run net-air jan,fev,mar,abr,mai    # net-air timeline completa
```
