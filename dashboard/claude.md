# Faturamento Dashboard - Documentacao Completa

> **Leia este documento ANTES de qualquer alteracao.** Se voce for uma LLM ou dev novo, siga exatamente estas regras.

---

## 1. Visao Geral

Dashboard de acompanhamento de faturamento para multiplas empresas (chamadas "Linhas de Receita" na UI) organizadas em **Grupos** e **Segmentos**. Permite:

- Visualizar receitas diarias, semanais, mensais e anuais
- Comparar desempenho real vs metas
- Analisar tendencias com projecao sazonal
- Entrada manual de dados via grid
- Gerenciar linhas de receita e metas
- Funcionar como PWA instalavel (offline read-only)

---

## 2. Stack Tecnologico

| Camada | Tecnologia | Versao |
|--------|------------|--------|
| Framework | React | 19.2.0 |
| Linguagem | TypeScript | 5.9.3 |
| Build | Vite | 7.2.4 |
| Graficos | Recharts | 3.7.0 |
| Datas | date-fns | 4.1.0 |
| Icones | lucide-react | 0.563.0 |
| Backend | Supabase | 2.93.3 |
| Servidor | Nginx Alpine | - |
| Container | Docker multi-stage | node:20 + nginx |

**Sem Redux/Zustand** - gerenciamento de estado via React Hooks + localStorage.
**Sem backend proprio** - SPA pura conectada ao Supabase.
**Sem autenticacao** - acesso publico via Supabase Anon Key + RLS.

---

## 3. Estrutura do Projeto

```
dash/
├── public/
│   ├── sw.js                         # Service Worker (offline)
│   ├── manifest.webmanifest          # PWA manifest
│   ├── pwa-*.png                     # Icones PWA (192, 512, maskable)
│   ├── apple-touch-icon*.png         # Icones iOS
│   └── favicon.svg
├── src/
│   ├── main.tsx                      # Entry point (React root + SW register)
│   ├── App.tsx                       # Componente principal (4 views)
│   ├── App.module.css                # Estilos do App
│   ├── App.css                       # Estilos globais
│   ├── index.css                     # CSS variables + reset
│   ├── types.ts                      # Interfaces TypeScript globais
│   ├── lib/
│   │   └── supabase.ts              # Cliente Supabase
│   ├── hooks/
│   │   ├── useSupabaseFaturamento.ts # Fetch + realtime do Supabase
│   │   ├── useFilters.ts            # Motor central de calculos
│   │   ├── useGoals.ts              # Gestao de metas (localStorage)
│   │   ├── useRevenueLines.ts       # Gestao de linhas de receita
│   │   └── useIsMobile.ts           # Deteccao mobile (768px)
│   ├── data/
│   │   ├── fallbackData.ts          # 24 empresas (COMPANIES)
│   │   └── goals.ts                 # Metas anuais 2026 por empresa/mes
│   ├── utils/
│   │   ├── goalCalculator.ts        # FONTE UNICA de calculos de metas
│   │   ├── projectionEngine.ts      # Sazonalidade + forecast
│   │   └── dataParser.ts            # Formatacao (BRL, %, datas)
│   ├── components/                   # 21 componentes React
│   │   ├── ViewToggle.tsx            # Seletor de view (Geral/Metas/Entrada/Linhas)
│   │   ├── MultiSelect.tsx           # Dropdown multi-select filtros
│   │   ├── DatePicker.tsx            # Seletor de data
│   │   ├── DatePresets.tsx           # Presets (Ontem/WTD/MTD/All)
│   │   ├── ComparisonToggle.tsx      # Toggle comparacao de periodos
│   │   ├── KPICard.tsx               # Card KPI simples
│   │   ├── GoalSummary.tsx           # Resumo de meta (barra de progresso)
│   │   ├── GoalProgress.tsx          # Barra de progresso de meta
│   │   ├── GoalEditor.tsx            # Modal de edicao de metas
│   │   ├── GoalsDashboard.tsx        # View Metas principal
│   │   ├── GoalTable.tsx             # Tabela expansivel de metas
│   │   ├── PeriodCards.tsx           # Cards Hoje/Semana/Mes/Ano
│   │   ├── GroupRanking.tsx          # Ranking de grupos
│   │   ├── RevenueChart.tsx          # Grafico de linha diario
│   │   ├── GroupStackedBars.tsx      # Barras empilhadas por grupo
│   │   ├── PaceChart.tsx             # Grafico de ritmo vs meta
│   │   ├── SharePieChart.tsx         # Pizza (segmento/grupo/empresa)
│   │   ├── BreakdownBars.tsx         # Barras horizontais rankeadas
│   │   ├── DataEntry.tsx             # Grid de entrada de dados
│   │   ├── RevenueLinesManager.tsx   # Gestao de linhas de receita
│   │   └── Select.tsx                # Dropdown simples
│   └── assets/
│       └── logo.svg
├── index.html                        # HTML entry (SPA)
├── vite.config.ts                    # Config Vite (React plugin)
├── tsconfig.json                     # TypeScript config
├── package.json                      # Dependencias e scripts
├── Dockerfile                        # Build multi-stage
├── nginx.conf                        # Config Nginx (SPA + gzip + cache)
├── CLAUDE.md                         # ESTE DOCUMENTO
└── REFATORACAO_METAS_E_PWA.md       # Doc da refatoracao de metas
```

---

## 4. Supabase

### Projeto
- **Nome:** 141air
- **ID:** `iezxmhrjndzuckjcxihd`
- **Regiao:** us-east-2
- **Dashboard:** https://supabase.com/dashboard/project/iezxmhrjndzuckjcxihd

### Tabela `faturamento`

```sql
CREATE TABLE faturamento (
  id BIGSERIAL PRIMARY KEY,
  empresa TEXT NOT NULL,
  data DATE NOT NULL,
  valor NUMERIC DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(empresa, data)
);
-- Indices: idx_faturamento_data, idx_faturamento_empresa
-- RLS: Habilitado
```

### Variaveis de Ambiente

```
VITE_SUPABASE_URL=https://iezxmhrjndzuckjcxihd.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key embedado em src/lib/supabase.ts>
```

### Operacoes

| Operacao | Metodo | Descricao |
|----------|--------|-----------|
| Fetch | `SELECT *` | Busca todos os registros no mount |
| Upsert | `UPSERT ON CONFLICT(empresa,data)` | Salva/atualiza entrada |
| Delete | `DELETE WHERE empresa+data` | Remove entrada |
| Realtime | `postgres_changes` channel | Refetch automatico em qualquer mudanca |

---

## 5. Fluxo de Dados

```
┌──────────────────────────────────────────────────────────┐
│                    FONTES DE DADOS                        │
├──────────────────────────────────────────────────────────┤
│  Supabase (remoto)           localStorage (local)         │
│  └─ tabela faturamento       ├─ yearly-goals              │
│  └─ realtime subscription    └─ revenue-lines             │
└──────────┬───────────────────────────┬───────────────────┘
           │                           │
           ▼                           ▼
    useSupabaseFaturamento         useGoals + useRevenueLines
    ├─ data: FaturamentoRecord[]   ├─ yearlyGoals
    ├─ upsertEntry()               ├─ lines: RevenueLine[]
    ├─ deleteEntry()               └─ updateYearlyGoals()
    └─ realtime refetch
           │
           │  + yearlyGoals + lines
           ▼
       useFilters (MOTOR CENTRAL DE CALCULOS)
       ├─ Aplica filtros (empresa/grupo/segmento/data)
       ├─ Calcula KPIs (faturamentoFiltrado, percentual)
       ├─ Calcula GoalMetrics (dia/semana/mes/ano)
       ├─ Gera dailyData (com goal por dia)
       ├─ Gera companyGoalData
       ├─ Gera breakdowns (empresa/grupo/segmento)
       └─ Suporta comparacao de periodos
           │
           ▼
       App.tsx (distribui via props)
       ├── View Geral  → KPIs, graficos, breakdowns
       ├── View Metas  → PeriodCards, PaceChart, GoalTable
       ├── View Entrada → DataEntry grid
       └── View Linhas → RevenueLinesManager
```

---

## 6. Tipos Principais (`src/types.ts`)

```typescript
interface FaturamentoRecord {
  data: Date;           // Data do registro (noon 12:00 para evitar timezone)
  empresa: string;      // Nome da linha de receita
  grupo: string;        // Grupo (NETAIR, ACA, EASY, BELLATOR, UNIQUE)
  segmento: string;     // Segmento (AR CONDICIONADO, UTILIDADES, BALESTRA, PRESENTES)
  faturamento: number;  // Valor em R$
}

interface RevenueLine {
  empresa: string;
  grupo: string;
  segmento: string;
}

interface Filters {
  empresas: string[];
  grupos: string[];
  segmentos: string[];
  dataInicio: Date | null;
  dataFim: Date | null;
}

interface KPIs {
  faturamentoFiltrado: number;
  faturamentoTotal: number;
  percentualDoTotal: number;
}

interface GoalMetrics {
  // Mes
  metaMensal: number;
  metaProporcional: number;
  realizado: number;
  realizadoMes: number;
  gapProporcional: number;
  gapTotal: number;
  percentualMeta: number;
  percentualProporcional: number;
  diasNoMes: number;
  diaAtual: number;               // D-1 (ontem)
  // Semana
  metaSemana: number;
  realizadoSemana: number;
  diasNaSemana: number;
  esperadoSemanal: number;        // Meta esperada ate D-1
  // Dia
  metaDia: number;                // Base (metaMensal / diasNoMes)
  metaDiaAjustada: number;        // Com regra AR CONDICIONADO
  realizadoDia: number;
  // Ano
  metaAno: number;
  realizadoAno: number;
  mesesNoAno: number;
  mesAtual: number;
  metasMensais: number[];         // 12 meses para graficos
  // AR CONDICIONADO
  isArCondicionado: boolean;
  coverage: { dia, semana, mes, ano: CoverageMetrics };
}
```

---

## 7. Hooks

### 7.1 `useSupabaseFaturamento` (`src/hooks/useSupabaseFaturamento.ts`)

Busca dados do Supabase e mantem sincronizado via realtime.

```typescript
// Entrada
{ includeZero?: boolean, lines?: RevenueLine[] }

// Saida
{
  data: FaturamentoRecord[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  upsertEntry: (empresa, date, valor) => Promise<Result>;
  deleteEntry: (empresa, date) => Promise<Result>;
  getValue: (empresa, date) => number | null;
}
```

**Comportamento:**
- Fetch inicial no mount
- Filtra registros por `activeLineSet` (apenas linhas ativas)
- Mapeia grupo/segmento via `lineMap`
- Datas normalizadas para noon (12:00) para evitar bugs de timezone
- Subscribe em `postgres_changes` → refetch completo em qualquer mudanca

### 7.2 `useFilters` (`src/hooks/useFilters.ts`)

Motor central de calculos. Recebe dados brutos e retorna tudo calculado.

```typescript
// Entrada
(data: FaturamentoRecord[], goalHelpers: { yearlyGoals, setSelectedMonth, lines })

// Saida
{
  filters, options, kpis, goalMetrics,
  companyGoalData, dailyData, comparisonDailyData, comparisonLabel,
  comparisonEnabled, customComparisonStart, customComparisonEnd,
  getGoalForDate, chartCompanies, allGroupsInData,
  groupBreakdown, segmentBreakdown, empresaBreakdown, segmentPieData,
  datePreset,
  updateFilter, toggleFilterValue, setDatePreset, clearFilters,
  hasActiveFilters, toggleComparison, setCustomComparisonRange, clearCustomComparison,
}
```

**Date Presets:**
- `yesterday` - dia anterior
- `wtd` - Week to Date (semana ate hoje)
- `mtd` - Month to Date (mes ate hoje) **[default]**
- `all` - periodo completo

**dailyData:** Cada ponto ja contem `goal` calculado. Componentes NAO devem recalcular.

### 7.3 `useGoals` (`src/hooks/useGoals.ts`)

Gerencia metas anuais persistidas em localStorage.

```typescript
// Storage: 'faturamento-dashboard-yearly-goals'
// Default: DEFAULT_YEARLY_GOALS (src/data/goals.ts)

{
  yearlyGoals: CompanyYearlyGoal[];
  goals: CompanyGoal[];              // Para o mes selecionado
  totalGoal: number;
  totalYearGoal: number;
  selectedMonth: number;             // 1-12
  updateYearlyGoals, updateGoalForMonth, resetGoals,
  getCompanyGoal, getGroupGoal, setSelectedMonth,
}
```

### 7.4 `useRevenueLines` (`src/hooks/useRevenueLines.ts`)

Gerencia lista de linhas de receita (empresas).

```typescript
// Storage: 'faturamento-dashboard-revenue-lines'
// Default: COMPANIES (src/data/fallbackData.ts)

{
  lines: RevenueLine[];
  lineMap: Map<string, RevenueLine>;
  addLine, updateLine, removeLine, setLines,
}
```

**Regras:**
- Adicionar linha → entra nos filtros imediatamente
- Remover linha → sai dos filtros, meta removida, dados historicos permanecem no banco
- Sincroniza com `yearlyGoals`: se existe meta sem linha, cria linha automaticamente

### 7.5 `useIsMobile` (`src/hooks/useIsMobile.ts`)

Retorna `boolean` para breakpoint de 768px.

---

## 8. Views (4 telas)

### 8.1 View "Geral" (default)

Visao principal com KPIs, graficos e breakdowns.

**Componentes:**
| Componente | Funcao |
|------------|--------|
| MultiSelect (x3) | Filtros: Grupo, Segmento, Linha |
| DatePresets | Yesterday, WTD, MTD, All |
| DatePicker (x2) | Data inicio/fim customizada |
| ComparisonToggle | Liga/desliga comparacao de periodos |
| KPICard (x2) | Faturamento filtrado, % do total |
| GoalSummary | Progresso da meta (barra + gap + %) |
| RevenueChart | Grafico de linha diario (+ comparacao) |
| SharePieChart | Pizza por segmento/grupo/empresa |
| GroupStackedBars | Barras empilhadas por grupo/dia |
| BreakdownBars (x3) | Rankings: Por Linha, Por Grupo, Por Segmento |

### 8.2 View "Metas"

Acompanhamento detalhado de metas.

**Componentes:**
| Componente | Funcao |
|------------|--------|
| Filtros | Grupo, Segmento, Empresa (sem date pickers) |
| PeriodCards | Cards: Hoje, Semana, Mes, Ano |
| PaceChart | Ritmo acumulado vs meta linear |
| GroupRanking | Ranking de grupos por performance |
| GoalTable | Tabela expansivel grupo → empresas |

**Status de meta:**
- `ahead` (verde): gap > +5% do esperado
- `on-track` (amarelo): gap entre -5% e +5%
- `behind` (vermelho): gap < -5% do esperado

### 8.3 View "Entrada"

Grid de entrada manual de dados.

**DataEntry:**
- Grid: Empresas (linhas) x Dias (colunas)
- Navegacao mensal com setas ← →
- Presets: Ontem, Semana Passada, Mes Passado
- Auto-save via upsert no Supabase
- Indicadores: ✓ (salvo), ! (abaixo da meta)
- Navegacao por Tab/Enter entre celulas

### 8.4 View "Linhas"

Gerenciamento de linhas de receita.

**RevenueLinesManager:**
- Adicionar nova linha (nome, grupo, segmento)
- Editar grupo/segmento de uma linha
- Remover linha (cascata para filtros e metas)
- Validacao de formulario

---

## 9. Fonte Unica de Verdade: `goalCalculator.ts`

**ARQUIVO MAIS IMPORTANTE DO SISTEMA.** Todos os calculos de metas passam por aqui.

### Funcoes

```typescript
// Constroi lista de empresas com segmento
buildCompanyMetaInfo(yearlyGoals, lines) → CompanyMetaInfo[]

// Filtra empresas pelos filtros ativos
filterCompaniesByFilters(companies, filters) → CompanyMetaInfo[]

// Fator de ajuste por segmento + dia da semana
getAdjustmentFactor(segmento, date) → number
// AR CONDICIONADO: dia util=1.2, fds=0.5
// Outros: sempre 1.0

// Meta diaria base = metaMensal / diasNoMes
getCompanyDailyBaseGoal(company, date) → number

// Meta diaria ajustada (aplica adjustment factor)
getCompanyAdjustedDailyGoal(company, date) → number

// Soma meta diaria ajustada de todas as empresas
getTotalAdjustedDailyGoal(companies, date) → number

// Soma meta diaria base de todas as empresas
getTotalBaseDailyGoal(companies, date) → number

// Soma meta mensal de todas as empresas
getTotalMonthlyGoal(companies, month) → number

// Soma meta anual de todas as empresas
getTotalYearlyGoal(companies) → number

// Soma metas diarias ajustadas num range de datas
sumAdjustedDailyGoalsForRange(companies, start, end) → number

// Mapa date→goal para graficos
buildDailyGoalMap(companies, dates) → Map<string, number>

// Meta ajustada de uma empresa especifica para uma data
getCompanyAdjustedDailyGoalForDate(companies, empresa, date) → number
```

---

## 10. Motor de Projecao: `projectionEngine.ts`

Analise de sazonalidade e previsao com quantis.

### Sazonalidade

```typescript
interface SeasonalityFactors {
  weekday: number[];       // 7 fatores (Seg=0, Dom=6)
  month: number[];         // 12 fatores (Jan=0, Dez=11)
  monthPosition: number[]; // 6 bins de posicao dentro do mes
  sampleSize: number;
}
```

**Calculo:**
1. Filtra valores > 0
2. Calcula log dos valores
3. Remove outliers via Z-score mediano (MAD) com threshold 3.5
4. Calcula media por dia-da-semana, mes e posicao-no-mes
5. Normaliza pela media geral
6. Clamp entre 0.3 e 3.0

### Forecast

```typescript
interface ForecastModel {
  baseline: number;           // EWMA (Exponentially Weighted Moving Average)
  alpha: number;              // 0.1 (normal) ou 0.25 (trend confirmado)
  quantiles: { p10, p50, p90 };
  confidence: number;         // 0.1 a 0.95
  trendConfirmed: boolean;
  forecastForDate: (date: Date) => { p10, p50, p90 };
}
```

**Processo:**
1. Dessazonaliza valores historicos
2. Remove outliers (Z-score > 3.5 no log)
3. Detecta tendencia (ultimos 3 meses crescentes 25%+)
4. Calcula EWMA com alpha adaptativo
5. Calcula residuos ponderados por recencia (half-life 120 dias)
6. Quantis p10/p50/p90 via weighted quantile
7. Confianca = f(cobertura_30d, estabilidade_residuos)

### Hierarquia

```typescript
// Blenda fatores company → group → total com shrinkage
calculateCompanySeasonality(records) → SeasonalityHierarchy
getSeasonalityForEntity(hierarchy, empresa?, grupo?) → SeasonalityFactors
```

---

## 11. Empresas e Metas

### 24 Linhas de Receita

| Grupo | Empresa | Segmento |
|-------|---------|----------|
| NETAIR | NETAIR | AR CONDICIONADO |
| NETAIR | NETPARTS | AR CONDICIONADO |
| NETAIR | 141AIR | AR CONDICIONADO |
| NETAIR | SHOPEE NETAIR | AR CONDICIONADO |
| NETAIR | VITAO | AR CONDICIONADO |
| NETAIR | VINICIUS | AR CONDICIONADO |
| NETAIR | ARTHUR | AR CONDICIONADO |
| NETAIR | JONATHAN | AR CONDICIONADO |
| ACA | AUTOFY (CONDENSADORES ) | AR CONDICIONADO |
| ACA | AUTOMY | AR CONDICIONADO |
| ACA | SHOPEE ACA | AR CONDICIONADO |
| EASY | EASYPEASY SP | UTILIDADES |
| EASY | EASYPEASY CWB | UTILIDADES |
| EASY | SHOPEE EASY | UTILIDADES |
| BELLATOR | BELLATOR CWB | BALESTRA |
| BELLATOR | BELLATOR - JUNIOR | BALESTRA |
| BELLATOR | BELLATOR - SITE | BALESTRA |
| UNIQUE | ML 1 - UNIQUE | PRESENTES |
| UNIQUE | ML 2 - UNIQUE | PRESENTES |
| UNIQUE | UNIQUEKIDS | PRESENTES |
| UNIQUE | UNIQUEBOX | PRESENTES |
| UNIQUE | MANU | PRESENTES |
| UNIQUE | REPRESENTANTES | PRESENTES |
| UNIQUE | SITE TERCEIROS | PRESENTES |

### Segmentos

| Segmento | Grupos | Regra Especial |
|----------|--------|----------------|
| AR CONDICIONADO | NETAIR, ACA | Meta ajustada: dia util=120%, fds=50% |
| UTILIDADES | EASY | Sem ajuste |
| BALESTRA | BELLATOR | Sem ajuste |
| PRESENTES | UNIQUE | Sem ajuste |

### Metas 2026

Definidas em `src/data/goals.ts` (DEFAULT_YEARLY_GOALS). Cada empresa tem metas mensais diferenciadas. Valores vem da planilha "metas 2026 lever (3).xlsx".

---

## 12. Regras de Negocio FIXAS (NAO MUDAR)

### 12.1 Referencia D-1 (ontem)

Todos os indicadores de "esperado" usam **D-1 (ontem)** como referencia.
Motivo: faturamento so pode ser fechado no dia seguinte (D+1).

| Indicador | Meta | Esperado | Realizado |
|-----------|------|----------|-----------|
| Diario | metaDiaAjustada de D-1 | - | faturamento de D-1 |
| Semanal | soma ajustada Seg-Dom | soma ajustada Seg ate D-1 | faturamento Seg ate D-1 |
| Mensal | soma ajustada do mes | soma ajustada dia 1 ate D-1 | faturamento dia 1 ate D-1 |
| Anual | soma 12 meses | proporcional ate mes D-1 | faturamento Jan ate D-1 |

### 12.2 Segmento = soma das empresas

Ao filtrar por segmento, metas sao **soma direta das empresas** do segmento.
**NAO** usar proporcao historica do faturamento.

### 12.3 AR CONDICIONADO ajusta por empresa

O ajuste de fds vem da soma individual das metas ajustadas.
**NAO** usar ponderacao por participacao do faturamento.

### 12.4 Esperado semanal

- `esperadoSemanal` = soma metas ajustadas de **segunda ate D-1**
- `metaSemana` = soma metas ajustadas da semana completa (Seg-Dom)

### 12.5 PaceChart (ritmo anual)

Em `datePreset === 'all'`, a meta mensal varia por mes (soma das metas das empresas filtradas).
**NAO** usar media fixa (metaAno/12).

---

## 13. Componentes de Visualizacao

### RevenueChart (grafico de linha)
- **Fonte:** `dailyData.goal` (NAO recalcula meta)
- Eixo X: indice sequencial (uso total da largura mobile)
- Eixo Y: `[0, max(faturamento, metaDiaria) * 1.1]`
- Suporta linhas de comparacao (periodo anterior, pontilhada)
- ReferenceLine horizontal para meta diaria

### GroupStackedBars (barras empilhadas)
- **Fonte:** `dailyData.goal` (NAO recalcula meta)
- Cores por grupo
- Tooltip com valores por grupo
- Linha de referencia para meta diaria

### SharePieChart (pizza)
- Switchable: Segmento / Grupo / Empresa
- Legenda lateral com percentuais

### PaceChart (ritmo)
- Area acumulada: realizado
- Linha pontilhada: meta linear acumulada
- Projecao futura via `projectionEngine`
- No preset "All": mostra ritmo do ano inteiro com meta variavel por mes

### BreakdownBars (ranking horizontal)
- 3 instancias: Por Linha, Por Grupo, Por Segmento
- Ordenado por valor decrescente
- Limite de itens exibidos

### PeriodCards
- 4 cards: Hoje, Semana, Mes, Ano
- Cada um mostra: realizado, meta, gap, %

---

## 14. Estilizacao

### CSS Architecture
- **CSS Modules** para escopo por componente (`.module.css`)
- **CSS Variables** globais em `src/index.css`
- **Sem Tailwind** - CSS manual com design tokens

### Design Tokens

```css
/* Cores */
--ink: #1a1a1a;           /* Texto */
--paper: #ffffff;          /* Fundo */
--positive: #23D8D3;       /* Teal (brand) */
--attention: #d97706;      /* Amber */
--success: #10b981;        /* Verde */
--danger: #ef4444;         /* Vermelho */
--warning: #f59e0b;        /* Laranja */

/* Espacamento: --space-1 a --space-16 (base 4px) */
/* Tipografia: --text-xs a --text-4xl */
/* Fonte: Inter (Google Fonts) */
```

### Responsividade
- Breakpoint mobile: **768px** (via `useIsMobile`)
- Mobile: filtros em overlays, layout vertical, tooltips desativados
- Desktop: layout em grid, dropdowns inline

---

## 15. PWA (Progressive Web App)

### Arquivos
- `public/manifest.webmanifest` - Configuracao da PWA
- `public/sw.js` - Service Worker

### Service Worker - Estrategias de Cache
- **HTML:** Network-first (fallback para cached shell)
- **Assets estaticos (JS/CSS/imagens):** Cache-first
- **Google Fonts:** Cache-first com runtime cache
- **Cache version:** `faturamento-dashboard-v1`

### Registro (src/main.tsx)
- **Producao:** Registra `sw.js`
- **Dev:** Desativa SW e limpa cache (evita "versao travada")

### Instalacao
- Botao "Instalar" aparece em `beforeinstallprompt`
- iOS: hint "Compartilhar → Adicionar a Tela"

---

## 16. Build e Deploy

### Scripts

```bash
npm run dev      # Vite dev server
npm run build    # tsc -b && vite build → dist/
npm run lint     # ESLint
npm run preview  # Vite preview de dist/
```

### Docker

```dockerfile
# Stage 1: Build
FROM node:20-alpine AS build
RUN npm ci && npm run build

# Stage 2: Serve
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

### Nginx
- Gzip: text, css, json, js, xml
- Cache estatico: 1 ano (immutable)
- SPA fallback: `try_files $uri $uri/ /index.html`
- Headers: X-Frame-Options SAMEORIGIN, X-Content-Type-Options nosniff
- Manifest: `application/manifest+json`

---

## 17. localStorage Keys

| Key | Conteudo | Default |
|-----|----------|---------|
| `faturamento-dashboard-yearly-goals` | Metas anuais editadas | `DEFAULT_YEARLY_GOALS` |
| `faturamento-dashboard-revenue-lines` | Lista de linhas de receita | `COMPANIES` |

---

## 18. Utilitarios (`src/utils/dataParser.ts`)

```typescript
formatBRL(20715)         → "R$ 20.715,00"
formatPercent(85.5)      → "85.5%"
formatDate(date)         → "02/09"
formatDateInput(date)    → "2026-02-09"
parseBRLCurrency("R$ 20.715,00") → 20715
parseBRDate("01/02/2026")        → Date
parseCSVData(csvText)             → FaturamentoRecord[]
```

---

## 19. Checklist Antes de Modificar

- [ ] A mudanca usa funcoes do `goalCalculator.ts`?
- [ ] O valor esperado referencia **D-1**?
- [ ] Segmento e calculado como **soma de empresas** (nao proporcao)?
- [ ] Graficos usam `dailyData.goal` sem recalcular?
- [ ] Entrada usa meta diaria **ajustada** por dia util/fds?
- [ ] Datas sao normalizadas para noon (12:00)?

Se alguma resposta for **NAO**, pare e revise.

---

## 20. Instrucoes para LLMs / Devs

**NUNCA:**
- Criar novos calculos de meta fora de `goalCalculator.ts`
- Proporcionalizar meta de segmento por share de faturamento
- Usar "hoje" em vez de D-1 nos esperados
- Ajustar meta diaria com base na participacao do faturamento
- Recalcular meta diaria nos componentes de grafico

**SEMPRE:**
- Usar funcoes do `goalCalculator.ts`
- Passar metas ja prontas do `useFilters`
- Normalizar datas para noon (12:00)
- Consultar este documento antes de alterar KPIs ou graficos
- Manter CSS Modules para novos componentes
