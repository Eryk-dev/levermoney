# PRD: Simplificar Exportacao de Non-Sale (Despesas)

## 1. Introduction/Overview

A tela atual de exportacao de despesas (non-sale) possui um dropdown de seller e filtros de mes/ano que adicionam complexidade desnecessaria. O usuario precisa ver de uma so vez o resumo de pendentes de TODOS os sellers e exporta-los rapidamente. Esta PRD descreve a substituicao da tela atual por uma visao de cards por seller, cada um com resumo de pendentes e botao de export individual, alem de um botao global para exportar todos.

## 2. Goals

- Visao panoramica: ver pendentes de todos os sellers de uma vez
- Eliminar dropdown de seller e filtros de mes/ano
- Cards por seller em ordem alfabetica com resumo de stats
- Botao de export individual por card + botao global "Exportar Todos os Pendentes"
- Historico de batches colapsavel dentro de cada card
- Preservar fluxo de export+backup existente (ZIP + GDrive)

## 3. User Stories

### US-001: Cards de sellers com resumo de pendentes

**Description:** As an admin, I want to see all active sellers as cards sorted alphabetically, each showing a summary of pending expenses, so that I have a panoramic view without needing to select from a dropdown.

**Acceptance Criteria:**
- [ ] Dropdown de seller e removido
- [ ] Dropdowns de "Mes" e "Ano" sao removidos
- [ ] Cada seller ativo e exibido como um card individual
- [ ] Cards ordenados em ordem alfabetica pelo nome do seller
- [ ] Cada card mostra: nome do seller, total de despesas, valor total (R$), pendentes de revisao, auto-categorizadas
- [ ] Cards de sellers sem pendentes aparecem com stats zerados e visual diferenciado (opaco/cinza)
- [ ] Stats carregam automaticamente ao abrir a tela (chama loadStats com status_filter para cada seller)
- [ ] Typecheck/lint passes

### US-002: Botao de export individual por card

**Description:** As an admin, I want each seller card to have an "Exportar Pendentes" button so that I can export one seller at a time.

**Acceptance Criteria:**
- [ ] Cada card tem botao "Exportar Pendentes"
- [ ] Ao clicar, executa fluxo completo: gera ZIP, backup GDrive, marca como `exported`
- [ ] Exportacao usa status_filter=pending_review,auto_categorized (sem filtro de data)
- [ ] Botao desabilitado quando seller nao tem pendentes (total = 0)
- [ ] Botao mostra estado de loading ("Exportando...") durante a exportacao
- [ ] Resultado exibe batch_id e status do backup no card
- [ ] Modal de confirmacao quando pending_review_count > 0 permanece funcionando
- [ ] Typecheck/lint passes

### US-003: Botao global "Exportar Todos os Pendentes"

**Description:** As an admin, I want a global button above the cards to export pending expenses from all sellers at once, so that I can do a bulk export in one click.

**Acceptance Criteria:**
- [ ] Botao "Exportar Todos os Pendentes" posicionado acima dos cards
- [ ] Ao clicar, executa exportAndBackup sequencialmente para cada seller que tenha pendentes (total > 0)
- [ ] Exibe progresso durante a exportacao (ex: "Exportando 2/5...")
- [ ] Ao terminar, exibe lista de batch_ids gerados (um por seller)
- [ ] Botao desabilitado quando nenhum seller tem pendentes
- [ ] Botao desabilitado durante exportacao
- [ ] Typecheck/lint passes

### US-004: Historico de batches colapsavel por card

**Description:** As an admin, I want to see the batch history for each seller inside its card, collapsible to save space, so that I can track past exports and re-download files.

**Acceptance Criteria:**
- [ ] Cada card tem secao colapsavel "Historico" (fechada por padrao)
- [ ] Ao expandir, mostra tabela de batches do seller (mesmas colunas atuais: Data, Linhas, Valor, Status, Backup, Batch ID, Acoes)
- [ ] Funcionalidade de re-download continua funcionando
- [ ] Polling de status GDrive continua funcionando por card
- [ ] Cleanup do polling ao colapsar ou desmontar
- [ ] Typecheck/lint passes

## 4. Functional Requirements

- **FR-1:** O componente `ExpensesExportTab` deve ser reestruturado para exibir cards em vez de dropdown + stats unicos
- **FR-2:** O sistema deve buscar stats de todos os sellers ativos em paralelo ao montar o componente
- **FR-3:** Cada card deve usar `loadStats` com `status_filter=pending_review,auto_categorized` (sem datas)
- **FR-4:** O endpoint `/expenses/{seller}/stats` deve aceitar `status_filter` e tornar datas opcionais (ja implementado no backend via US-001 anterior)
- **FR-5:** O endpoint `/expenses/{seller}/export` deve aceitar `status_filter` e tornar datas opcionais (ja implementado)
- **FR-6:** O botao global deve iterar sobre sellers com pendentes e chamar export sequencialmente
- **FR-7:** O historico de batches deve ser carregado sob demanda ao expandir a secao no card
- **FR-8:** Cards sem pendentes devem ter visual diferenciado (opacidade reduzida)

## 5. Non-Goals (Out of Scope)

- Nao alterar endpoints backend (status_filter ja implementado)
- Nao alterar a logica de classificacao non-sale no `processor.py`
- Nao alterar o formato do ZIP exportado
- Nao alterar a logica de backup GDrive
- Nao adicionar filtros novos (ex: por tipo de despesa, por direcao)
- Nao implementar paginacao de cards (assume numero pequeno de sellers)

## 6. Technical Considerations

- **Backend:** Nenhuma alteracao necessaria. Os endpoints de stats e export ja aceitam `status_filter` (implementado na iteracao anterior).
- **Frontend:** Reestruturar `ExpensesExportTab` de layout single-seller (dropdown) para multi-seller (cards grid). Reutilizar `useExpenses` hook para cada seller individualmente.
- **Performance:** Chamadas de stats em paralelo (`Promise.all`) para todos os sellers ativos. Batches carregados sob demanda (lazy load ao expandir historico).
- **Layout:** CSS Grid ou flex-wrap para os cards. Responsivo para diferentes quantidades de sellers.

## 7. Success Metrics

- Ao abrir a tela, admin ve resumo de TODOS os sellers sem interacao
- Exportacao individual requer 1 clique (botao no card)
- Exportacao global requer 1 clique (botao acima dos cards)
- Zero regressao nas funcionalidades existentes (export, batch history, re-download, GDrive backup)

## 8. Open Questions

- Nenhuma (escopo definido com respostas do usuario)
