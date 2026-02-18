export interface FaturamentoRecord {
  data: Date;
  empresa: string;
  grupo: string;
  segmento: string;
  faturamento: number;
}

export interface RevenueLine {
  empresa: string;
  grupo: string;
  segmento: string;
}

export interface Filters {
  empresas: string[];
  grupos: string[];
  segmentos: string[];
  dataInicio: Date | null;
  dataFim: Date | null;
}

export interface KPIs {
  faturamentoFiltrado: number;
  faturamentoTotal: number;
  percentualDoTotal: number;
}

export interface CoverageMetrics {
  observed: number;
  expected: number;
  percent: number;
}

export interface GoalMetrics {
  metaMensal: number;          // Meta do período selecionado
  metaProporcional: number;     // Meta proporcional até o dia atual
  realizado: number;            // Faturamento realizado no período filtrado
  realizadoMes: number;         // Faturamento realizado no mês até D-1 (independente do filtro)
  gapProporcional: number;      // Diferença entre realizado e meta proporcional
  gapTotal: number;             // Diferença entre realizado e meta total
  percentualMeta: number;       // % da meta total atingido
  percentualProporcional: number; // % em relação ao esperado até hoje
  diasNoMes: number;
  diaAtual: number;             // Dia de referencia (Hoje/Ontem) para calculo de esperado
  // Week metrics
  metaSemana: number;           // Meta da semana (soma das metas diarias, com planner dinamico no mes atual)
  realizadoSemana: number;      // Faturamento realizado na semana ate a referencia
  diasNaSemana: number;         // Dias na semana ate a referencia (calendario)
  esperadoSemanal: number;      // Meta esperada ate a referencia (planner dinamico no mes atual)
  // Day metrics
  metaDia: number;              // Meta do dia base (metaMensal / diasNoMes)
  metaDiaAjustada: number;      // Meta do dia (planner dinamico no mes atual; fallback para regra classica)
  realizadoDia: number;         // Faturamento realizado no dia de referencia (Hoje/Ontem)
  // Year metrics
  metaAno: number;              // Meta do ano
  realizadoAno: number;         // Faturamento realizado no ano ate a referencia
  mesesNoAno: number;           // Meses no ano (12)
  mesAtual: number;             // Mes de referencia (1-12)
  metasMensais: number[];       // Metas mensais (Jan-Dez) para o contexto filtrado
  // AR CONDICIONADO specific
  isArCondicionado: boolean;    // Se o contexto é 100% AR CONDICIONADO
  coverage: {
    dia: CoverageMetrics;
    semana: CoverageMetrics;
    mes: CoverageMetrics;
    ano: CoverageMetrics;
  };
}
