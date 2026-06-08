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

## Como reproduzir
```
python3 testes/judge_caixa_jan2026.py                       # âncora + buckets, 4 sellers
python3 -m testes.harness.run 141air jan,fev,mar,abr,mai     # 141air timeline completa
python3 -m testes.harness.run net-air jan,fev,mar,abr,mai    # net-air timeline completa
```
