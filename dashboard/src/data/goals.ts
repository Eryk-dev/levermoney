// Metas anuais por empresa - cada empresa tem metas para cada mês

export interface MonthlyGoals {
  [month: number]: number; // 1-12 para Jan-Dez
}

export interface CompanyYearlyGoal {
  empresa: string;
  grupo: string;
  metas: MonthlyGoals;
}

// Helper para criar metas uniformes (mesmo valor todos os meses)
function uniformGoals(value: number): MonthlyGoals {
  return { 1: value, 2: value, 3: value, 4: value, 5: value, 6: value, 7: value, 8: value, 9: value, 10: value, 11: value, 12: value };
}

// Metas 2026 extraídas da planilha 'metas 2026 lever (3).xlsx' - aba ANO
export const DEFAULT_YEARLY_GOALS: CompanyYearlyGoal[] = [
  // NETAIR (grupo)
  { empresa: 'NETAIR', grupo: 'NETAIR', metas: {
    1: 1000000, 2: 1000000, 3: 1000000, 4: 800000, 5: 700000, 6: 700000,
    7: 700000, 8: 700000, 9: 700000, 10: 800000, 11: 1100000, 12: 1100000
  }},
  { empresa: 'NETPARTS', grupo: 'NETAIR', metas: {
    1: 800000, 2: 800000, 3: 800000, 4: 700000, 5: 600000, 6: 600000,
    7: 600000, 8: 600000, 9: 800000, 10: 800000, 11: 1000000, 12: 1000000
  }},
  { empresa: 'SHOPEE NETAIR', grupo: 'NETAIR', metas: {
    1: 70000, 2: 80000, 3: 90000, 4: 80000, 5: 70000, 6: 70000,
    7: 70000, 8: 70000, 9: 70000, 10: 90000, 11: 100000, 12: 110000
  }},
  { empresa: '141AIR', grupo: 'NETAIR', metas: {
    1: 140000, 2: 140000, 3: 120000, 4: 120000, 5: 100000, 6: 100000,
    7: 120000, 8: 130000, 9: 140000, 10: 150000, 11: 160000, 12: 170000
  }},
  { empresa: 'VITAO', grupo: 'NETAIR', metas: {
    1: 20000, 2: 30000, 3: 40000, 4: 50000, 5: 60000, 6: 70000,
    7: 80000, 8: 90000, 9: 100000, 10: 110000, 11: 120000, 12: 130000
  }},
  { empresa: 'VINICIUS', grupo: 'NETAIR', metas: {
    1: 80000, 2: 80000, 3: 70000, 4: 70000, 5: 70000, 6: 70000,
    7: 70000, 8: 70000, 9: 80000, 10: 90000, 11: 100000, 12: 110000
  }},
  { empresa: 'ARTHUR', grupo: 'NETAIR', metas: {
    1: 20000, 2: 25000, 3: 30000, 4: 35000, 5: 40000, 6: 45000,
    7: 50000, 8: 55000, 9: 60000, 10: 65000, 11: 70000, 12: 75000
  }},
  { empresa: 'JONATHAN', grupo: 'NETAIR', metas: {
    1: 0, 2: 0, 3: 20000, 4: 25000, 5: 30000, 6: 35000,
    7: 40000, 8: 45000, 9: 50000, 10: 55000, 11: 60000, 12: 65000
  }},

  // ACA (meta total do grupo distribuída: 80% AUTOFY, 20% AUTOMY)
  { empresa: 'AUTOFY (CONDENSADORES )', grupo: 'ACA', metas: {
    1: 80000, 2: 200000, 3: 280000, 4: 360000, 5: 440000, 6: 520000,
    7: 600000, 8: 680000, 9: 760000, 10: 840000, 11: 920000, 12: 1000000
  }},
  { empresa: 'AUTOMY', grupo: 'ACA', metas: {
    1: 20000, 2: 50000, 3: 70000, 4: 90000, 5: 110000, 6: 130000,
    7: 150000, 8: 170000, 9: 190000, 10: 210000, 11: 230000, 12: 250000
  }},
  { empresa: 'SHOPEE ACA', grupo: 'ACA', metas: uniformGoals(0) },

  // EASY - META total varia: 300k, 250k, 300k, 250k, 200k, 200k, 300k, 250k, 200k, 300k, 250k, 300k
  // Distribuição proporcional: SP 10%, CWB 83.3%, Shopee 6.7%
  { empresa: 'EASYPEASY SP', grupo: 'EASY', metas: {
    1: 30000, 2: 25000, 3: 30000, 4: 25000, 5: 20000, 6: 20000,
    7: 30000, 8: 25000, 9: 20000, 10: 30000, 11: 25000, 12: 30000
  }},
  { empresa: 'EASYPEASY CWB', grupo: 'EASY', metas: {
    1: 250000, 2: 208000, 3: 250000, 4: 208000, 5: 167000, 6: 167000,
    7: 250000, 8: 208000, 9: 167000, 10: 250000, 11: 208000, 12: 250000
  }},
  { empresa: 'SHOPEE EASY', grupo: 'EASY', metas: {
    1: 20000, 2: 17000, 3: 20000, 4: 17000, 5: 13000, 6: 13000,
    7: 20000, 8: 17000, 9: 13000, 10: 20000, 11: 17000, 12: 20000
  }},

  // BELLATOR
  { empresa: 'BELLATOR CWB', grupo: 'BELLATOR', metas: {
    1: 80000, 2: 70000, 3: 130000, 4: 120000, 5: 80000, 6: 140000,
    7: 120000, 8: 100000, 9: 100000, 10: 180000, 11: 180000, 12: 200000
  }},
  { empresa: 'BELLATOR - JUNIOR', grupo: 'BELLATOR', metas: uniformGoals(0) },
  { empresa: 'BELLATOR - SITE', grupo: 'BELLATOR', metas: uniformGoals(0) },

  // UNIQUE
  { empresa: 'ML 1 - UNIQUE', grupo: 'UNIQUE', metas: {
    1: 25000, 2: 35000, 3: 45000, 4: 55000, 5: 65000, 6: 100000,
    7: 85000, 8: 95000, 9: 105000, 10: 115000, 11: 125000, 12: 135000
  }},
  { empresa: 'ML 2 - UNIQUE', grupo: 'UNIQUE', metas: {
    1: 50000, 2: 60000, 3: 70000, 4: 80000, 5: 90000, 6: 100000,
    7: 110000, 8: 120000, 9: 130000, 10: 140000, 11: 150000, 12: 160000
  }},
  { empresa: 'UNIQUEKIDS', grupo: 'UNIQUE', metas: {
    1: 100000, 2: 110000, 3: 120000, 4: 130000, 5: 140000, 6: 150000,
    7: 160000, 8: 170000, 9: 250000, 10: 250000, 11: 200000, 12: 210000
  }},
  { empresa: 'UNIQUEBOX', grupo: 'UNIQUE', metas: {
    1: 30000, 2: 30000, 3: 30000, 4: 50000, 5: 300000, 6: 500000,
    7: 30000, 8: 30000, 9: 30000, 10: 30000, 11: 50000, 12: 70000
  }},
  { empresa: 'MANU', grupo: 'UNIQUE', metas: {
    1: 70000, 2: 75000, 3: 80000, 4: 85000, 5: 90000, 6: 95000,
    7: 100000, 8: 105000, 9: 110000, 10: 115000, 11: 120000, 12: 125000
  }},
  { empresa: 'REPRESENTANTES', grupo: 'UNIQUE', metas: {
    1: 0, 2: 15000, 3: 20000, 4: 25000, 5: 30000, 6: 35000,
    7: 40000, 8: 45000, 9: 50000, 10: 55000, 11: 60000, 12: 65000
  }},
  { empresa: 'SITE TERCEIROS', grupo: 'UNIQUE', metas: {
    1: 0, 2: 0, 3: 15000, 4: 20000, 5: 25000, 6: 30000,
    7: 35000, 8: 40000, 9: 45000, 10: 50000, 11: 55000, 12: 60000
  }},
];

// Nomes dos meses em português
export const MONTH_NAMES = [
  '', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'
];

export const MONTH_FULL_NAMES = [
  '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'
];

// Interface para meta de um mês específico
export interface CompanyGoal {
  empresa: string;
  grupo: string;
  metaMensal: number;
}

// Converter para formato de meta por período (para um mês específico)
export function toMonthlyGoals(goals: CompanyYearlyGoal[], month: number): CompanyGoal[] {
  return goals.map(g => ({
    empresa: g.empresa,
    grupo: g.grupo,
    metaMensal: g.metas[month] || 0,
  }));
}
