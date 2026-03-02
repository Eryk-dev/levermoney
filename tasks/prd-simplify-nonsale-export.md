# PRD: Simplificar Exportacao de Non-Sale (Despesas)

## 1. Introduction/Overview

A tela atual de exportacao de despesas (non-sale) possui filtros de mes e ano que adicionam complexidade desnecessaria. O usuario precisa apenas ver um resumo dos itens pendentes e exporta-los com um clique. Esta PRD descreve a simplificacao dessa tela: remover filtros de periodo, mostrar apenas o resumo de pendentes, e oferecer um botao direto de exportacao.

## 2. Goals

- Reduzir friccao na exportacao de despesas pendentes
- Eliminar filtros de mes/ano que nao agregam valor ao fluxo
- Manter o resumo estatistico ja implementado
- Adicionar opcao "Todos" no filtro de seller
- Preservar o historico de batches e fluxo de export+backup existente

## 3. User Stories

### US-001: Ver resumo de despesas pendentes sem selecionar periodo

**Description:** As an admin, I want to see a summary of all pending expenses (pending_review + auto_categorized) without having to select a month/year, so that I can quickly understand what needs to be exported.

**Acceptance Criteria:**
- [ ] Os dropdowns de "Mes" e "Ano" sao removidos da UI
- [ ] O stats card exibe dados de TODAS as despesas com status `pending_review` ou `auto_categorized` (sem filtro de data)
- [ ] Os 4 campos do resumo permanecem: total de despesas, valor total, pendentes de revisao, auto-categorizadas
- [ ] O resumo carrega automaticamente ao selecionar um seller
- [ ] Typecheck/lint passes

### US-002: Filtrar por seller com opcao "Todos"

**Description:** As an admin, I want to select a specific seller or "Todos" to see pending expenses across all sellers, so that I can export for one or all sellers at once.

**Acceptance Criteria:**
- [ ] O dropdown de seller inclui opcao "Todos" como primeiro item
- [ ] Ao selecionar "Todos", o resumo agrega pendentes de todos os sellers
- [ ] Ao selecionar "Todos", a exportacao gera arquivo com despesas de todos os sellers
- [ ] O comportamento atual de selecionar um seller especifico permanece inalterado
- [ ] Typecheck/lint passes

### US-003: Exportar pendentes com um clique

**Description:** As an admin, I want to click a single "Exportar Pendentes" button to export all pending expenses, so that the export flow is fast and simple.

**Acceptance Criteria:**
- [ ] O botao de exportacao tem label "Exportar Pendentes"
- [ ] Ao clicar, executa o fluxo completo: gera ZIP, faz backup GDrive, marca como `exported`
- [ ] O filtro de exportacao usa status IN (`pending_review`, `auto_categorized`) em vez de filtro por data
- [ ] O botao fica desabilitado quando nao ha pendentes (total = 0)
- [ ] O botao mostra estado de loading ("Exportando...") durante a exportacao
- [ ] Resultado exibe batch ID e status do backup como ja implementado
- [ ] Typecheck/lint passes

### US-004: Manter historico de batches

**Description:** As an admin, I want to continue seeing the batch history table after the simplification, so that I can track past exports and re-download files.

**Acceptance Criteria:**
- [ ] A tabela de historico de batches permanece visivel abaixo do resumo/botao
- [ ] Todas as colunas atuais sao mantidas (Data, Linhas, Valor, Status, Backup, Batch ID, Acoes)
- [ ] Funcionalidade de re-download continua funcionando
- [ ] Polling de status GDrive continua funcionando
- [ ] Typecheck/lint passes

## 4. Functional Requirements

- **FR-1:** O sistema deve remover os dropdowns de "Mes" e "Ano" do componente `ExpensesExportTab`
- **FR-2:** O sistema deve buscar stats filtrando por status (`pending_review`, `auto_categorized`) sem filtro de data
- **FR-3:** O endpoint `/expenses/{seller}/stats` deve aceitar um parametro opcional `status_filter` para filtrar por status especificos
- **FR-4:** O endpoint `/expenses/{seller}/export` deve aceitar um parametro opcional `status_filter` para exportar apenas itens com status especificos, sem exigir `date_from`/`date_to`
- **FR-5:** O dropdown de seller deve incluir opcao "Todos" que agrega dados de todos os sellers
- **FR-6:** Quando "Todos" estiver selecionado, o sistema deve chamar stats/export para todos os sellers ativos
- **FR-7:** O botao "Exportar Pendentes" deve executar o mesmo fluxo de export+backup ja existente
- **FR-8:** A tabela de historico de batches deve continuar funcionando sem alteracoes

## 5. Non-Goals (Out of Scope)

- Nao alterar a logica de classificacao non-sale no `processor.py`
- Nao alterar o formato do ZIP exportado
- Nao alterar a logica de backup GDrive
- Nao adicionar filtros novos (ex: por tipo de despesa, por direcao)
- Nao alterar a tela de review/categorizacao de despesas

## 6. Technical Considerations

- **Backend (stats endpoint):** Adicionar parametro `status_filter` ao GET `/expenses/{seller}/stats` para filtrar por status sem exigir datas. Quando `status_filter` fornecido, `date_from`/`date_to` tornam-se opcionais.
- **Backend (export endpoint):** Mesma alteracao: `status_filter` torna datas opcionais. Valor padrao atual (excluir exported/imported) pode ser mantido como fallback.
- **Frontend:** Remover estado de `month`/`year`, `buildDateRange()`, e dropdowns de periodo. Substituir por chamada direta com `status_filter=pending_review,auto_categorized`.
- **Seller "Todos":** Pode ser implementado como chamada agregada no frontend (loop por sellers) ou endpoint dedicado no backend. Avaliar qual abordagem e mais simples.

## 7. Success Metrics

- Exportacao de pendentes requer apenas 2 interacoes: selecionar seller (ou "Todos") + clicar "Exportar Pendentes"
- Zero regressao nas funcionalidades existentes (batch history, re-download, GDrive backup)
- Tempo de carregamento do resumo <= 2 segundos

## 8. Open Questions

- Para seller "Todos": gerar um unico ZIP com subpastas por seller ou um ZIP por seller?
- O endpoint de batches precisa suportar listagem cross-seller quando "Todos" esta selecionado?
