# Conta Azul Pro — Reverse Engineering Findings

**Data da captura:** 2026-04-17
**Empresa logada:** unique comercial (141AIR)
**Escopo:** módulos Financeiro + Relatórios

---

## 0. Sidebar — Módulo Financeiro (submenus)

| Item | URL / Nota |
|------|-----------|
| Extrato da Conta PJ (Novo) | Feature nova |
| Outras contas | Investimento, cooperativa, outros |
| Visão de competência | Regime de competência |
| Contas a pagar | — |
| DDA (Novo) | Débito Direto Autorizado |
| Contas a receber | — |
| Inadimplentes (Beta) | — |
| Extrato de movimentações | — |
| Fluxo de caixa | — |
| Histórico | — |
| Cadastros | Categorias, centros de custo, formas de pagamento, contas |

## 0.1 Contas financeiras (dashboard inicial)

Tipos de conta observados:
- **Conta corrente** (141AIR - MP, 141AIR - SD)
- **Cartão de crédito** (141AIR - MP - CC, 141AIR - SD - CC) — sempre **vinculado** a uma conta corrente
- **Investimento** (141 - MP LEVER TALENTS) — vinculado a conta corrente
- **Outro tipo de conta** (141 SD - COTAS DE COOPERATIVA)

Cada card mostra: tipo, nome, valor, vínculo, status (conciliações pendentes).

---

## 1. Conciliação Bancária (tela principal)

**URL pattern:** `#/ca/financeiro/contas/corrente/{account_uuid}?tab=PENDING_RECONCILIATIONS|RECONCILIATION`

### 1.1 Header

- Seletor de conta (dropdown com todas as contas)
- Botão **Ações da conta** (dropdown) — inclui OFX import, config
- Botão **Análise por categorias**
- Navegador de mês/ano (← Abril de 2026 →)
- **Data da última atualização**: timestamp do sync
- **Data do último lançamento importado**: ex. 10/04/2026
- **Saldo atual na ContaAzul**: R$ -56.321,02
- **Valor pendente de conciliação**: R$ 67.014,49

### 1.2 Tabs

- **Conciliações pendentes** — lançamentos do banco ainda não conciliados (contador na tab)
- **Movimentações** — lançamentos já conciliados / movimentos internos

### 1.3 Filtros / ações globais

- Pesquisa por **descrição ou valor** (search box)
- Botão **Limpar filtros**
- Botão **Ver lançamentos arquivados**
- Aba interna: Todos / Recebimentos / Pagamentos (com contadores)
- Ações bulk: **Selecionar lançamentos · Conciliar · Editar · Desvincular · Arquivar · Ordenar**

### 1.4 Vista de matching (two-column)

**Layout side-by-side:**

| Lançamentos do banco (OFX) | Lançamentos da Conta Azul |
|---|---|
| Data + dia da semana (ex: 04/03/2026 Quarta-Feira) | Tabs: **Novo lançamento** / **Nova transferência** / Buscar lançamento |
| Valor (verde = entrada, vermelho = saída) | — |
| Descrição bruta do OFX (ex: `RECEBIMENTO PIX-PIX_CRED 56959528000147 141 AIR COMERCIO DE PECAS LTDA`) | Campos editáveis (pré-preenchidos por heurística): |
| Badge `Integração manual` (vs auto) | — **Descrição*** (pré-preenchida com descrição do banco) |
| Botão **Arquivar** (ignora sem conciliar) | — **Categoria*** (dropdown com plano de contas, ex: `1.1.5 Vendas Diretas/Balcão`) |
| — | — **Cliente** (dropdown, opcional) |
| — | — **Centro de custo** (dropdown, opcional) |
| — | Link **Repetir lançamento** (aprende padrão) |
| — | Botão **Completar informações** (form completo modal) |
| — | Botão **Conciliar** (ação principal) |

### 1.5 Sinais importantes

- Plano de contas é **numerado hierarquicamente** (`1.1.5` = receita › venda › direta)
- Categoria tem **auto-sugestão** (ícone de raio ⚡ ao lado do campo)
- "Nova transferência" = movimento entre contas (não gera receita/despesa — apenas saldo)
- "Buscar lançamento" = casar com conta a pagar/receber **já existente** em aberto
- Sistema mantém `Data do último lançamento importado` (sync incremental OFX)

### 1.6 Campos capturados no matching

- `bank_date` (data do movimento bancário)
- `bank_amount` (sinal: + entrada, - saída)
- `bank_description` (texto bruto OFX)
- `integration_source` (manual | automático)
- `ca_description` (editável, default = bank_description)
- `ca_category_id` (referencia plano de contas)
- `ca_client_id` (opcional)
- `ca_cost_center_id` (opcional)
- `ca_completar_informacoes` → abre modal com rateio, parcelas, anexos

---

---

## 2. Menu "Ações da conta" (dropdown por conta)

Capturado em `screenshots/03-acoes-da-conta-dropdown.png`. Itens:

1. **Editar conta** — metadados (nome, tipo, banco, agência, número)
2. **Editar saldo inicial da conta** — opening balance correction
3. **Importar extrato (.OFX)** — upload OFX manual
4. **Configurar conciliação automática** — regras de auto-categorização (keyword → categoria)
5. **Ver lançamentos arquivados** — ignorados
6. **Configurar boletos** — emissão de boletos vinculados à conta
7. **Ativar integração** — open-banking / ebanking sync automático

## 3. Modal "Completar informações" (Nova receita / Nova despesa)

Screenshot: `04-completar-informacoes-modal.png`. URL contém `rollover=FinancialEventEditRollover`.

### 3.1 Seção "Informações do lançamento"

| Campo | Tipo | Obrigatório | Notas |
|-------|------|-------------|-------|
| Cliente | combobox | não | Tem CTA "Consultar cliente no Serasa" |
| Data de competência | date | sim | Regime de competência |
| Descrição | textarea | sim | Default = bank description |
| Valor | currency | sim | R$ |
| **Habilitar rateio** | switch | — | Ativa seção de rateio (seção 3.2) |
| Categoria | combobox hierárquico | sim | Ex: `1.1.5 Vendas Diretas/Balcão` |
| Centro de custo | combobox | não | Desaparece quando rateio ON |
| Código de referência | text | não | Custom ID livre |

### 3.2 Seção "Informar categoria e centro de custo" (aparece com rateio ON)

Screenshot: `05-rateio-enabled.png`.

Tabela multi-linha — cada linha representa uma **categoria** dentro da transação:

| Coluna | Tipo | Notas |
|--------|------|-------|
| Categoria* | combobox | Plano de contas |
| Valor total* | currency | Parte do total nesta categoria |
| Porcentagem* | % | Calculado / editável (valor sincroniza com %) |
| Centro de custo | combobox | CC único para essa linha |
| Ação | botão "Rateio de centro de custo" | Sub-rateio (ver 3.3) |

Botão **"+ Adicionar categoria"** adiciona nova linha.

### 3.3 Modal "Rateio de centro de custos" (sub-modal)

Screenshot: `06-rateio-cc-submodal.png`.

**Header:** Categoria + Valor da categoria (readonly, herdado da linha de rateio pai).

Tabela:

| Coluna | Tipo |
|--------|------|
| Centro de custos* | combobox |
| Valor* | R$ |
| Percentual* | % |

Rodapé:
- **Restante de rateio** (live counter: 100% / R$ 10.000,00 disponível)
- **Total rateado** (live counter: soma das linhas)
- Botão **+ Adicionar linha**
- **Cancelar** / **Concluir**

### 3.4 Estrutura de rateio (resumo)

**Hierarquia 2-níveis:**

```
transaction (valor total)
  └─ categoria_split[]  ← rateio por categoria
       ├─ categoria_id
       ├─ valor, percentual
       ├─ centro_de_custo_id (opcional — CC único)
       └─ cost_center_split[] ← rateio por CC (opcional)
             ├─ centro_de_custo_id
             └─ valor, percentual
```

### 3.5 Seção "Repetir lançamento?" (switch)

Presumido: gera recorrência (mensal, semanal, etc.) — a detalhar.

### 3.6 Seção "Condição de pagamento"

| Campo | Tipo | Obrigatório |
|-------|------|-------------|
| Parcelamento | dropdown (1x, 2x, ... 12x+) | sim |
| Vencimento | date | sim |
| Forma de pagamento | combobox | — |
| Conta de recebimento | combobox (lista de contas) | sim |
| Recebido? | checkbox | — |

### 3.7 Seção "Informar NSU?" (switch)

Para recebimentos de cartão — captura NSU da maquininha.

### 3.8 Seção "Informações do recebimento" (aparece quando Recebido=on)

Screenshot: `08-completar-info-bottom.png`.

| Campo | Tipo | Obrigatório |
|-------|------|-------------|
| Data do recebimento | date | sim |
| Valor recebido | currency | sim |
| Juros | currency | sim (default 0) |
| Multa | currency | sim (default 0) |
| Desconto | currency | sim (default 0) |
| Tarifa | currency | sim (default 0) |

Somatório: **Total a receber** (live) = valor recebido - desconto + juros + multa - tarifa (a confirmar sinais exatos).

### 3.9 Seção "Resumo da baixa" (expansível)

Collapsed por padrão. Provavelmente mostra: data + valor + diferença de competência, parcelas geradas, etc.

### 3.10 Tabs finais: Observações / Anexo

- **Observações**: textarea livre
- **Anexo**: upload de arquivos (boleto, nota, comprovante)

### 3.11 Footer

- **Voltar** / **Salvar** (com caret → opções tipo "Salvar e novo", a confirmar)

---

---

## 4. Cadastros → Categorias Financeiras

Screenshots: `12-categorias-financeiras.png`, `13-categorias-1-1-expanded.png`.

### 4.1 Observação crítica — DUAS hierarquias de categorias

**Banner de aviso na tela:**
> As configurações desta tela se aplicam ao DRE Gerencial padrão. Para que as categorias financeiras criadas nesta tela sejam refletidas nos novos relatórios de DRE, você precisará configurá-las em outra tela. Acesse **Categorias do DRE**, encontre a categoria do DRE desejada e na coluna **Vincule às categorias financeiras**, abra a seleção e escolha a categoria financeira criada para vinculá-la ao DRE.

**Implicação para o clone:**
- **Categorias financeiras** (usadas em lançamentos / conciliação)
- **Categorias DRE** (usadas em relatório DRE)
- **Linkagem manual** entre as duas — trade-off: flexibilidade vs consistência
- **Nosso clone deve unificar** OU replicar o mesmo padrão intencionalmente

### 4.2 Dados extraídos via API

187 categorias totais (ver `data/categories.json` — a salvar):
- **4 raízes REVENUE (level 0)**: 1.1 (Vendas), 1.3 (Outras Operacionais), 1.4 (Não Operacionais), 3.2 (Estorno L&D)
- **19 raízes EXPENSE (level 0)**: 1.2 (Deduções), 2.1 (Custos), 2.2 (Impostos Vendas), 2.3 (Impostos Diversos), 2.4 (Pessoal), 2.5 (Administrativas), 2.6 (Tecnologia), 2.7 (Marketing), 2.8 (Comerciais), 2.9 (Logística), 2.10 (Legais), 2.11 (Financeiras), 2.12 (Veículos), 2.13 (Impostos Lucro), 2.14 (Outras Operacionais), 2.15 (Investimentos), 2.16 (Intercompany), 2.17 (Não Operacionais), 3.1 (Distribuição L&D)

Schema de categoria:
```typescript
{
  id: number,        // numeric ID legacy
  uuid: string,      // primary FK
  name: string,      // "1.1.5 Vendas Diretas/Balcão" (numeric prefix embutido)
  type: "REVENUE" | "EXPENSE",
  parentUuid: string | null,  // tree parent
  version: number,   // optimistic locking
  level: number      // 0 = root group, 1+ = children
}
```

---

## 5. Cadastros → Centros de Custo

Screenshot: `14-centros-de-custo.png`. Dados em `data/cost-centers.json`.

**Total:** 25 centros de custo ativos.

**Padrão descoberto:** cada empresa do grupo tem 2 CCs:
- `CCnnn.1` — VARIÁVEL (custos que escalam com volume)
- `CCnnn.2` — FIXO (custos recorrentes)

Empresas: NETAIR, NETPARTS, 141AIR, EASYPEASY, UNIQUE, BELLATOR, LEVER TALENTS, GRUPO, VICTOR, EASYPEASY SP.

Schema:
```typescript
{
  id: string,      // UUID
  version: number,
  code: string | null,  // "CC001.1"
  name: string,         // "NETAIR - VARIÁVEL"
  parent: string | null, // supports hierarchy (não usado aqui)
  active: boolean
}
```

**Tabs:** Ativos (25) / Inativos (0) / Todos (25). Botão: "Novo centro de custo".

---

## 6. Contas a Pagar (lista)

Screenshots: `15-contas-a-pagar.png` (promo modal), `16-contas-a-pagar-clean.png`.

### 6.1 Header (actions bar)
- **Nova despesa** (botão com dropdown — provavelmente "despesa única" vs "despesa recorrente" vs "via CA AI Captura")
- **Relatórios** (dropdown)
- **Exportar**
- **Imprimir**
- **Importar planilha** (bulk import via XLSX/CSV)

### 6.2 Filtros
- **Vencimento** — navegador de mês/ano
- **Pesquisar no período selecionado** — search box
- **Conta** — dropdown (filtra por conta bancária)
- **Mais filtros** — dropdown expandido (categoria, centro custo, fornecedor, status, etc.)

### 6.3 KPI tabs (também funcionam como filtros)
- Vencidos (R$)
- Vencem hoje (R$)
- A vencer (R$)
- Pagos (R$)
- Total do período (R$)

### 6.4 Bulk actions
- "N registro(s) selecionado(s)"
- **Pagar pelo CA de Bolso** (pagamento centralizado via ContaAzul)
- **Ações em lote** (dropdown — pagar, cancelar, editar categoria, etc.)

### 6.5 Table columns
| Coluna | Descrição |
|--------|-----------|
| checkbox | bulk select |
| Vencimento (sortable) | due date |
| Pagamento (sortable, tooltip) | payment date (quando pago) |
| Resumo do lançamento | multi-line: `parcela X/Y - DESCRIÇÃO` + `categoria · fornecedor` |
| Total (R$) | valor original |
| A pagar (R$) | saldo restante |
| Situação | badge Pago ✓ (verde) / Em Aberto (laranja) |
| Ações | dropdown por linha |

### 6.6 Insight — parcelamento nativo

Cada parcela é uma linha na tabela com:
- Indicador "1↑ 5/52" (parcela 5 de 52 — apontador visual pra parcela-pai)
- Mesma descrição base + sufixo de parcela
- Data de vencimento própria
- Status independente

---

---

## 7. Cartão de Crédito

Screenshots: `17-cartao-credito-main.png`, `18-fatura-paga-details.png`.

**URL:** `/ca/financeiro/contas/cartao-de-credito/{uuid}`

### 7.1 Header
- Seletor de conta (dropdown)
- **Editar conta** (primary button — não tem "Ações da conta" como conta corrente)
- Status da fatura: **Fatura paga** (badge clicável — toggle entre status)
- Month navigator

### 7.2 Summary card (3 colunas)

| Campo | Descrição |
|-------|-----------|
| Saldo anterior | Saldo que passou do mês anterior (rotativo ou parcial) |
| Fechamento | Data do fechamento da fatura (cutoff) |
| Vencimento | Data limite para pagamento |
| Valor da fatura | Total da fatura atual (negativo = a pagar) |

### 7.3 Ciclo da fatura (inferido)

```
Saldo anterior → Fechamento → Vencimento → Pagamento
     (rollover)    (cutoff)      (due)       (pay)
```

### 7.4 Transaction table (por dia)

Colunas: Data (header), Descrição, Categoria, Parcela, Valor.

**Linha de totalização diária:** "Saldo final do dia" (running balance).

**Parcelamento nativo:** formato "N/M" (ex: 1/3) — cada parcela é uma linha própria na fatura quando vence.

### 7.5 Diferenças vs conta corrente

- **Não tem OFX import direto na tela** — lançamentos manuais ou via captura AI
- **Conta CC vinculada** a conta corrente (para pagamento de fatura)
- **Parcelas futuras** ficam "pendentes" e aparecem nas próximas faturas
- **Rotativo** via campo "Saldo anterior" quando pagamento parcial

---

## 8. Relatórios — Módulo completo

Screenshots: `19-relatorios-home.png`, `20-relatorios-padrao.png`, `21-fluxo-caixa-diario.png`, `23-relatorios-all-expanded.png`.

**URL base:** `/ca/relatorios`

### 8.1 Estrutura do módulo

**Tabs:**
- **Favoritos** — starred reports
- **Padrão** — built-in (ver abaixo)
- **Personalizados** — user-defined
- **Antigos** — legacy

**Header actions:**
- **Novo relatório personalizado** (report builder)
- **Agendador de relatórios** (scheduled delivery)
- **Exportar** (dropdown — PDF/Excel)

### 8.2 Relatórios Padrão — Financeiro

**Grupo DRE** (3 relatórios — regime competência):
1. **DRE com análise vertical e horizontal** — comparação período a período, tendências
2. **DRE Gerencial** — resultado econômico (receitas - custos - despesas = L/P)
3. **DRE por centros de custo** — DRE segmentado por CC

> **Nota crítica:** DRE **Caixa** (regime de caixa) está no grupo Fluxo de caixa, marcado com "(visão caixa)".

**Grupo Fluxo de caixa** (9 relatórios — mix de produção e beta):
1. Fluxo de caixa diário
2. Fluxo de caixa mensal
3. Fluxo de caixa mensal (Personalizado) — beta
4. Gráfico do Fluxo de caixa mensal — beta
5. Fluxo de caixa diário (v2) — beta
6. Gráfico de fluxo de caixa diário — beta
7. **Análise de resultados por mês: visão caixa** (= DRE Caixa)
8. Análise de resultados com análise vertical e horizontal: visão caixa
9. Análise de resultados por centro de custo: visão caixa

**Grupo Análise financeira** (15 relatórios):
1. Análise de pagamentos — valores pagos (data baixa, fornecedor, categoria, CC, conta origem, tarifas, descontos)
2. Análise de recebimentos — valores recebidos (data baixa, cliente, categoria, CC, conta origem, encargos)
3. Análise de inadimplentes — clientes em atraso
4. Análise por categorias — lançamentos agrupados por categoria+CC (aberto/vencido/baixado)
5. Análise por centros de custo — agrupado por CC+categoria
6. Gráfico de despesas por categoria
7. Gráfico de receitas e despesas por vencimento
8. Gráfico de receitas por categoria
9. Gráfico de saldo mensal por centro de custo
10. Posição de contas por cliente/fornecedor
11. Relação de contas a receber/pagar
12. Relação de lançamentos no caixa
13. Relação de lançamentos por categorias e centros de custo (detalhamento de rateio)
14. Relação detalhada de recebimentos e pagamentos (multas, juros, descontos, tarifas)
15. Situação financeira por vendedores

### 8.3 Relatório exemplar — Fluxo de Caixa Diário

Screenshot: `21-fluxo-caixa-diario.png`. URL: `/ca/relatorios/fluxo-de-caixa-diario?periodo=2026-04`.

**Layout:**
- Filtros: mês/ano, Diário/Mensal toggle, Filtrar por conta
- **Gráfico**: barras (recebimentos verde + pagamentos vermelho + transferências) + linha (saldo)
- **Tabela**: Data, Recebimentos, Pagamentos, Transferências entrada, Transferências saída, Saldo Final
- Botão: **Acessar relatório avançado** (upsell)
- Botão: **Exportar** (dropdown)

### 8.4 Grupos fora do escopo financeiro (não detalhado)

- **Vendas** (~15 relatórios) — CMV, margem, clientes, produtos
- **Compras** (~4 relatórios) — compras por categoria, produtos comprados
- **Estoque** (~7 relatórios) — giro, posição, curva ABC

---

## 9. Screens não detalhados (padrões já capturados)

Estas telas seguem os mesmos padrões de **Conciliação bancária / Contas a pagar**. Captura detalhada pulada por similaridade:

- **Extrato da Conta PJ** (Novo) — variação da conta corrente oficial CA
- **Outras contas** — lista de contas não-corrente (investimento, cooperativa)
- **Visão de competência** — alternativa de lista com regime competência
- **Contas a receber** — espelho de Contas a pagar (Nova receita form idêntico)
- **DDA** — inbox de boletos DDA → match com Contas a pagar
- **Extrato de movimentações** — view consolidada entre contas
- **Fluxo de caixa** (tela Financeiro) — vista operacional (≠ relatório Fluxo de caixa)
- **Histórico** — audit log de lançamentos

---

## 10. Padrões arquiteturais observados (resumo pra clone)

1. **Event-driven por natureza** — cada lançamento tem data de competência + data de caixa separadas
2. **Hierarquia numerada em categorias** — `X.Y.Z` embedded no name, UUID como FK
3. **Rateio 2-níveis** — transação → categorias[] → centros_de_custo[]
4. **Multi-account**: conta corrente (com OFX) ↔ cartão de crédito (sem OFX, com fatura) ↔ investimento ↔ outros, com vínculos entre eles
5. **Parcelamento é entidade** — cada parcela é uma linha independente
6. **Optimistic locking** — todos recursos têm campo `version`
7. **Paginação padrão** — `page`, `page_size`, `totalItems`, `items[]`
8. **Duas vistas do DRE** — competência vs caixa (unificar no clone é mais simples)
9. **Auto-recon configurable** — "Configurar conciliação automática" por conta (keyword → categoria)
10. **Não há baixa reversível** — confirmado pelo user (principal dor do CA atual)

---

- [ ] Scrollar modal pra ver "Informações do recebimento" completo
- [ ] Expandir Parcelamento (ver como gera parcelas)
- [ ] Expandir Repetir lançamento (recorrência)
- [ ] Expandir Forma de pagamento (ver lista)
- [ ] Fechar modal → ir pra **Cadastros** (plano de contas hierárquico completo)
- [ ] Cadastros → Centros de custo (lista)
- [ ] Cadastros → Formas de pagamento
- [ ] Testar **Nova transferência** (diferente de receita/despesa)
- [ ] Testar **Buscar lançamento** (matching com AP/AR existentes)
- [ ] Configurar conciliação automática (regras auto-categorização)
- [ ] Navegar para **Cartão de crédito** (141AIR - SD - CC) — fatura, parcelas, fechamento
- [ ] Contas a pagar / Contas a receber (list + create)
- [ ] DDA, Extrato movimentações, Fluxo de caixa, Histórico
- [ ] Relatórios (DRE, fluxo de caixa, etc.)
