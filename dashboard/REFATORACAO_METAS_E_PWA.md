# Refatoração de Metas + PWA (Fonte Única de Verdade)

Este documento descreve **todas** as mudanças da refatoração das metas e o setup de PWA, com foco em manter cálculos **coerentes** e evitar duplicações.
Ele deve ser lido **antes** de qualquer alteração futura. Se você for uma LLM ou dev novo no projeto, **siga exatamente estas regras**.

---

## Objetivo da Refatoração

O dashboard acumulou cálculos redundantes em diferentes componentes (metas diárias, semanais, proporcionais, ajuste de fim de semana etc.).
Isso gerava divergências entre:
- `Geral` vs `Metas`
- `KPIs` vs `Gráficos`
- `Entrada` vs `Resumo`

**Meta da refatoração:**
Criar **uma única fonte de verdade** para metas e esperado, garantindo consistência em todas as telas.

---

## Fonte Única de Verdade

### Arquivo principal
`src/utils/goalCalculator.ts`

Todos os cálculos de metas devem passar por este utilitário.

### O que ele resolve
- Meta mensal/anual por empresa/grupo/segmento
- Meta diária **ajustada** por dia da semana
- Soma de metas diárias por período
- Filtro por **segmento via empresas** (não por proporção histórica)

### Funções principais
- `buildCompanyMetaInfo(yearlyGoals)`
  - Constrói lista de empresas com grupo + segmento.
- `filterCompaniesByFilters(companies, filters)`
  - Aplica filtros de empresa/grupo/segmento de forma consistente.
- `getCompanyDailyBaseGoal(company, date)`
  - Meta diária base (metaMensal / diasDoMes).
- `getCompanyAdjustedDailyGoal(company, date)`
  - Aplica ajuste por dia útil/fds **apenas para AR CONDICIONADO**.
  - Regra:
    - dia útil: `120%`
    - fim de semana: `50%`
- `getTotalAdjustedDailyGoal(companies, date)`
  - Soma **todas** as metas diárias ajustadas das empresas filtradas.
- `sumAdjustedDailyGoalsForRange(companies, start, end)`
  - Soma metas ajustadas dia-a-dia para um período.
- `getTotalMonthlyGoal(companies, month)`
  - Soma meta mensal das empresas.
- `getTotalYearlyGoal(companies)`
  - Soma meta anual das empresas.

---

## Regras Fixas (NÃO MUDAR)

Estas regras foram acordadas e **não devem ser reescritas em outros pontos do sistema**.

### 1) Referência D-1 (ontem)
- Todos os indicadores de "esperado" usam **D‑1** (ontem).
- Isso garante que “onde deveríamos estar” respeita o fechamento de faturamento no dia seguinte.

### 2) Segmento = soma das empresas
- Ao filtrar por **segmento**, as metas são **soma direta das empresas do segmento**, usando `COMPANIES`.
- Não usar proporção histórica do faturamento.

### 3) AR CONDICIONADO ajusta por empresa
- O ajuste de fim de semana vem da **soma das metas diárias ajustadas das empresas**.
- **Não** usar ponderação por participação do faturamento.

### 4) Esperado semanal
- `esperadoSemanal` = soma das metas ajustadas de **segunda até D‑1**.
- `metaSemana` = soma das metas ajustadas da semana completa (Seg–Dom).

### 5) Meta anual proporcional
- Usa **mês de D‑1**.

---

## Onde os cálculos são usados

### `src/hooks/useFilters.ts`
Responsável por:
- Filtrar dados de faturamento.
- Gerar `goalMetrics` com metas/esperados oficiais.
- Gerar `dailyData` com `goal` já calculado por dia.

**Importante:**
`dailyData` agora contém `goal` pronto para gráficos.
Componentes não devem recalcular meta diária.

---

### `RevenueChart` (linha diária)
**Fonte única:** `dailyData.goal`
O gráfico **não** calcula meta diária.
Eixo X usa **índice sequencial** para garantir uso total da largura no mobile.

---

### `PaceChart` (ritmo)
- Em `datePreset === 'all'` (Ritmo Mensal do Ano), a **meta mensal varia por mês**.
- A meta mensal de cada mês é a **soma das metas das empresas filtradas** (base mensal), não uma média fixa (`metaAno/12`).
- Não reintroduzir meta mensal fixa no gráfico anual.

---

### `GroupStackedBars` (barras empilhadas)
**Fonte única:** `dailyData.goal`
O gráfico **não** calcula meta diária.

---

### `GoalsDashboard` / `PeriodCards`
Usam os valores já entregues por `goalMetrics`:
- `metaHoje` = meta ajustada de D‑1
- `esperadoSemanal`
- `metaMensal`, `metaAno`

---

### `DataEntry`
Agora exibe:
- AR CONDICIONADO: `meta útil` e `meta fds`
- Outros: meta diária base

Comparação de preenchimento usa **meta ajustada** por data.

---

## Instruções para outras LLMs / devs

**NUNCA**:
- Criar novos cálculos de meta em componentes.
- Proporcionalizar meta de segmento por share de faturamento.
- Usar “hoje” em vez de D‑1 nos esperados.
- Ajustar meta diária com base na participação do faturamento.

**SEMPRE**:
- Usar funções do `goalCalculator.ts`.
- Passar metas já prontas do `useFilters`.
- Consultar este documento antes de alterar qualquer KPI ou gráfico.

---

## Linhas de Receita (antigo “Empresa”)

### Conceito
No sistema, **“Empresa” agora é tratado como “Linha de Receita”** (nome de linha).
Os campos técnicos continuam usando `empresa` por compatibilidade com Supabase, mas **toda a UI** deve falar “Linha”.

### Fonte de verdade
- `useRevenueLines` mantém a lista de linhas (nome, grupo, segmento) em **localStorage**.
- A lista é a base para **filtros**, **metas**, **segmentos** e **mapeamento** de dados.

### Regras
- Ao **criar** uma linha:
  - ela entra imediatamente nos filtros.
  - um registro de metas anual é criado automaticamente (com metas 0).
- Ao **remover** uma linha:
  - ela sai dos filtros e telas.
  - a meta anual correspondente é removida.
  - dados históricos continuam no banco, mas não entram nos cálculos.

### Arquivos principais
- `src/hooks/useRevenueLines.ts`
- `src/hooks/useSupabaseFaturamento.ts` (mapeia grupo/segmento)
- `src/hooks/useFilters.ts` (usa lista de linhas como base dos filtros)
- `src/components/RevenueLinesManager.tsx` (tela de cadastro)

---

## PWA (Mobile)

### Arquivos adicionados
- `public/manifest.webmanifest`
- `public/sw.js`
- `public/pwa-192.png`, `public/pwa-512.png`
- `public/pwa-192-maskable.png`, `public/pwa-512-maskable.png`
- `public/apple-touch-icon.png`

### Registro do SW
`src/main.tsx`
- Em produção: registra `sw.js`.
- Em desenvolvimento: desativa SW e limpa cache para evitar “versão travada”.

### Comportamento offline
`sw.js` implementa:
- Cache-first para assets.
- Network-first para HTML.
- Cache simples para Google Fonts.

---

## Checklist rápido antes de mexer em metas

- A mudança usa **goalCalculator.ts**?
- O valor esperado é **D‑1**?
- Segmento é **soma de empresas**?
- Gráficos usam `dailyData.goal` sem recalcular?
- Entrada usa meta diária **ajustada** por dia útil/fds?

Se alguma resposta for **não**, pare e revise.
