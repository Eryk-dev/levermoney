# Conta Azul Pro — API Endpoints (capturados via network)

**Auth scheme:** header `x-authorization: <UUID token>` (exemplo da captura: `3c00ca5a-...`)
Token aparece em `localStorage` encriptado (CA ofusca) e em cada request. Rotaciona com sessão.

---

## 1. Categorias financeiras

**GET** `https://services.contaazul.com/search-engine-core/v1/financial/categories/view`

Query params:
- `page` (int, 1-indexed)
- `page_size` (int)
- `type` (`REVENUE` | `EXPENSE`)
- `searchTerm` (string, opcional)

Response envelope:
```json
{ "totalItems": N, "items": [ {...}, ... ] }
```

Item schema:
```json
{
  "id": 417418638,
  "uuid": "e1e6db54-66df-4c8e-a28a-fd3c74119a49",
  "name": "1.1 RECEITA DE VENDAS",
  "type": "REVENUE",
  "parentUuid": null,
  "version": 4,
  "level": 0,
  "children": [ { ... recursive ... } ]
}
```

**Observações:**
- Hierarquia máxima observada: nível 2 (mas API suporta mais)
- `level: 0` = raiz (grupos), `level: 1` = subcategorias
- Nomes seguem padrão numérico: `1.1`, `1.1.1`, `2.10`, `2.11.1`
- Há "Categorias financeiras" separadas de "Categorias DRE" — linkagem manual obrigatória

---

## 2. Centros de custo

**GET** `https://services.contaazul.com/finance-pro/v1/cost-centers`

Query params:
- `search` (string)
- `page_size` (int)
- `page` (int)
- `quick_filter` (`ACTIVE` | `INACTIVE` | `ALL`)

Response envelope:
```json
{ "totalItems": 25, "items": [ ... ], "totals": { ... } }
```

Item schema:
```json
{
  "id": "ea62b7c0-be2f-11f0-b53b-c7780e8df70d",
  "version": 2,
  "code": "CC001.1",
  "name": "NETAIR - VARIÁVEL",
  "parent": null,
  "active": true
}
```

**Observações:**
- `code` no formato `CCNNN.N` (pode ser null)
- `parent` suporta hierarquia (não usado na conta inspecionada — todos null)
- `version` = optimistic locking

---

## 3. Permissões do usuário

**GET** `https://services.contaazul.com/app/me/permissions`

---

## 4. Status de incidentes por path

**GET** `https://services.contaazul.com/service-status-issues/api/v1/incidents/search?path={path}`

---

## 5. Has any cost center (boot check)

**HEAD** `https://services.contaazul.com/finance-pro-reader/v1/cost-center/has-any`

Usado pra saber se deve renderizar onboarding ou lista.

---

## Padrões inferidos

1. **Microservice naming**: `services.contaazul.com/{service-name}/v{N}/{resource}`
   - `finance-pro` — core financeiro (write)
   - `finance-pro-reader` — read replica otimizada
   - `search-engine-core` — busca/listagem com paginação
2. **Versioning**: optimistic locking via campo `version` em todos os recursos
3. **UUIDs**: recursos usam UUIDs, não IDs numéricos (exceto categorias que têm ambos)
4. **Auth**: session token rotativo, não JWT — reautenticação transparente