# PRD: Extrato CSV Upload no Admin Panel (Frontend)

## 1. Introdução / Visão Geral

O backend dos endpoints `POST /admin/sellers/{slug}/activate` e `POST /admin/sellers/{slug}/upgrade-to-ca` foi refatorado para aceitar `multipart/form-data` com upload obrigatório de um CSV do account_statement (extrato MP). O frontend do Admin Panel ainda envia `JSON.stringify`, o que vai quebrar esses endpoints imediatamente.

Este PRD cobre as mudanças necessárias no dashboard React para:
1. Adaptar as chamadas de API para `FormData` (multipart)
2. Adicionar campo de upload de arquivo CSV nos dois formulários modais
3. Tornar o CSV obrigatório no modo `dashboard_ca` (ativação) e sempre obrigatório no upgrade
4. Exibir feedback claro ao admin: erros de validação do período coberto pelo extrato e resumo do processamento após sucesso

---

## 2. Goals

- Alinha o frontend com o contrato multipart do backend sem regressão
- Torna impossível submeter ativação CA ou upgrade sem anexar o extrato
- Exibe ao admin o resultado do processamento (quantas linhas ingeridas no `mp_expenses`)
- Exibe mensagens de erro legíveis quando o extrato não cobre o período necessário

---

## 3. User Stories

### US-F01: Adaptar chamadas de API para FormData

**Descrição:** Como desenvolvedor, quero que `activateSeller` e `upgradeToCA` em `useAdmin.ts` enviem `FormData` em vez de `JSON`, para que o backend FastAPI receba os campos como multipart form data.

**Acceptance Criteria:**
- [ ] Em `useAdmin.ts`, a função `activateSeller` constrói um `FormData` com todos os campos de `ActivateSellerConfig` (cada campo string via `fd.append(key, value ?? '')`) e o arquivo `extrato_csv` se presente (`fd.append('extrato_csv', file, file.name)`)
- [ ] A chamada `fetch` de `activateSeller` NÃO define `Content-Type` manualmente (deixar o browser setar `multipart/form-data; boundary=...` automaticamente) — remover o `headers()` do Content-Type para esta chamada; manter apenas o `X-Admin-Token`
- [ ] Mesma mudança para `upgradeToCA`: FormData com os campos + arquivo `extrato_csv`
- [ ] A interface `ActivateSellerConfig` ganha campo `extrato_csv?: File`
- [ ] A interface `UpgradeToCAConfig` ganha campo `extrato_csv: File` (obrigatório)
- [ ] O tipo de retorno de ambas as funções inclui `extrato_processed?: ExtratoProcessedStats | null`, onde `ExtratoProcessedStats` tem ao menos `newly_ingested: number` e `total_lines: number`
- [ ] Em caso de erro HTTP 4xx/5xx, a função lê `res.json()` e retorna `{ status: 'error', error_detail: string, ... }` com o campo `detail` da resposta FastAPI, para que o componente possa exibir a mensagem ao usuário
- [ ] Typecheck passa (`npm run build` sem erros de tipo)

---

### US-F02: Adicionar upload de extrato no formulário de Ativação

**Descrição:** Como admin, quero ver um campo de upload de arquivo CSV dentro do modal de ativação (quando modo `dashboard_ca` estiver selecionado), para poder anexar o extrato antes de confirmar.

**Acceptance Criteria:**
- [ ] Em `AdminPanel.tsx`, o bloco `{isCAMode && (...)}` (após o campo Centro de Custo CA) inclui um novo campo `<label>` com texto "Extrato MP (account_statement CSV)" e um `<input type="file" accept=".csv">`
- [ ] O estado local `activationForm` recebe campo `extrato_csv: File | null` (inicializado como `null`)
- [ ] Ao selecionar arquivo, `activationForm.extrato_csv` é atualizado via `onChange`
- [ ] Quando um arquivo está selecionado, exibe o nome do arquivo abaixo do input (ex: `✓ extrato_jan2026.csv`)
- [ ] A variável `isCAModeValid` também exige `activationForm.extrato_csv !== null` quando `isCAMode === true`
- [ ] O botão "Ativar" fica `disabled` enquanto `!isCAModeValid` (já existente) — portanto fica desabilitado sem o arquivo
- [ ] `handleActivationSubmit` passa `extrato_csv: activationForm.extrato_csv` na config quando `dashboard_ca`
- [ ] Quando `integration_mode` muda de `dashboard_ca` para `dashboard_only`, o `extrato_csv` no form é resetado para `null`
- [ ] Typecheck passa

---

### US-F03: Adicionar upload de extrato no formulário de Upgrade para CA

**Descrição:** Como admin, quero ver um campo de upload de CSV no modal de Upgrade para CA, sempre obrigatório, para anexar o extrato histórico antes de iniciar o backfill.

**Acceptance Criteria:**
- [ ] Em `AdminPanel.tsx`, o modal `{upgradeForm && (...)}` inclui um campo `<label>` com texto "Extrato MP (account_statement CSV) *" e `<input type="file" accept=".csv">` (antes dos botões de ação)
- [ ] O estado local `upgradeForm` recebe campo `extrato_csv: File | null` (inicializado como `null`)
- [ ] Ao selecionar arquivo, `upgradeForm.extrato_csv` é atualizado
- [ ] Quando arquivo selecionado, exibe nome abaixo do input
- [ ] O botão "Salvar e Iniciar Backfill" fica `disabled` se `!upgradeForm.extrato_csv` (além das validações já existentes de conta e centro de custo)
- [ ] `handleUpgradeSubmit` passa `extrato_csv: upgradeForm.extrato_csv` na config
- [ ] Typecheck passa

---

### US-F04: Exibir feedback de erro e sucesso do extrato

**Descrição:** Como admin, quero ver mensagens claras após submeter o formulário: um erro descritivo se o extrato não cobrir o período necessário, e um resumo de quantas linhas foram processadas em caso de sucesso.

**Acceptance Criteria:**
- [ ] Em `AdminPanel.tsx`, adicionar estado local `activationError: string | null` (e equivalente `upgradeError: string | null`)
- [ ] Se `activateSeller` retornar `status: 'error'`, exibir `activationError` como parágrafo de erro em vermelho dentro do modal (ex: `<p className={styles.formError}>{activationError}</p>`) — o modal NÃO fecha
- [ ] Se `upgradeToCA` retornar `status: 'error'`, mesma lógica com `upgradeError`
- [ ] Quando `status: 'ok'` e `extrato_processed` presente na resposta, mostrar um `alert()` ou linha de texto no modal antes de fechar: ex. `"Extrato processado: X linhas novas ingeridas no mp_expenses."` (pode ser um `window.alert` simples para não complicar)
- [ ] Ao reabrir o modal (novo `activationForm`/`upgradeForm`), os estados de erro são resetados
- [ ] Adicionar classe CSS `formError` no arquivo `AdminPanel.module.css` com estilo básico: `color: var(--danger); font-size: var(--text-sm); margin-top: var(--space-1);`
- [ ] Typecheck passa

---

## 4. Functional Requirements

- **FR-1:** O frontend NÃO deve enviar `Content-Type: application/json` nos endpoints de ativação e upgrade (quebraria o parsing multipart do FastAPI).
- **FR-2:** O campo `extrato_csv` deve ser enviado com o método `FormData.append('extrato_csv', file, file.name)` — não como base64, não como string.
- **FR-3:** Para `dashboard_only`, o campo `extrato_csv` não é enviado e o input de arquivo não é exibido.
- **FR-4:** Para `dashboard_ca` (ativação) e upgrade-to-ca, o arquivo CSV é obrigatório para habilitar o botão de submit.
- **FR-5:** Erros HTTP 400 do backend com `detail` (período não coberto) devem ser exibidos em texto legível ao admin dentro do modal.
- **FR-6:** O modal de ativação / upgrade fecha apenas em caso de sucesso (`status: 'ok'`), não em erros de validação do extrato.

---

## 5. Non-Goals (Fora de Escopo)

- Validação local do CSV no frontend (período, formato de colunas) — o backend já faz isso
- Preview do conteúdo do CSV antes do upload
- Progress bar de upload
- Endpoint de backfill-retry não precisa de extrato CSV (confirmado no backend — sem mudanças)
- Mudanças no endpoint de aprovação de sellers pendentes (`/admin/sellers/{id}/approve`)

---

## 6. Technical Considerations

### Arquivos a modificar

| Arquivo | Mudança |
|---------|---------|
| `dashboard/src/hooks/useAdmin.ts` | FormData, interfaces, retorno de erro |
| `dashboard/src/components/AdminPanel.tsx` | File input nos 2 modais, validação, feedback |
| `dashboard/src/components/AdminPanel.module.css` | Classe `.formError` |

### Padrão de FormData com token de auth

```typescript
// Não usar headers() diretamente — ele define Content-Type: application/json
// Para multipart, construir headers manualmente apenas com X-Admin-Token:
const fd = new FormData();
fd.append('integration_mode', config.integration_mode);
// ... outros campos
if (config.extrato_csv) fd.append('extrato_csv', config.extrato_csv, config.extrato_csv.name);

const res = await fetch(url, {
  method: 'POST',
  headers: { 'X-Admin-Token': token },  // SEM Content-Type
  body: fd,
});
```

### Estado do form de ativação (tipo atual + extensão)

```typescript
// Campo a adicionar em ActivationForm (estado local em AdminPanel):
extrato_csv: File | null;
```

### Estado de erro

```typescript
const [activationError, setActivationError] = useState<string | null>(null);
const [upgradeError, setUpgradeError] = useState<string | null>(null);
// Limpar ao abrir modal: setActivationError(null) em openActivationForm
// Limpar ao abrir modal: setUpgradeError(null) em openUpgradeForm
```

---

## 7. Success Metrics

- Admin consegue ativar um seller `dashboard_ca` enviando o CSV sem erros de rede/CORS
- Admin vê mensagem de erro descritiva se o CSV não cobrir o período correto
- Admin vê confirmação do número de linhas processadas após sucesso
- Nenhuma regressão para ativação `dashboard_only` (sem arquivo)

---

## 8. Open Questions

- (Resolvido) O campo `extrato_csv` é opcional para `dashboard_only` e obrigatório para `dashboard_ca` e upgrade ✓
- Após exibir `extrato_processed`, fechar o modal automaticamente ou aguardar o admin clicar? → Usar `alert()` simples e fechar automaticamente (menor complexidade)
