import { useState, useMemo, useCallback, useEffect } from 'react';
import type { FaturamentoRecord, Filters, KPIs, GoalMetrics, RevenueLine } from '../types';
import {
  startOfWeek,
  endOfWeek,
  startOfMonth,
  endOfMonth,
  startOfYear,
  getDaysInMonth,
  differenceInCalendarDays,
  addDays,
} from 'date-fns';
import type { CompanyYearlyGoal } from '../data/goals';
import {
  buildCompanyMetaInfo,
  buildAdaptiveDailyGoalPlanner,
  filterCompaniesByFilters,
  getTotalBaseDailyGoal,
  getTotalMonthlyGoal,
  getTotalYearlyGoal,
} from '../utils/goalCalculator';

export type DatePreset = 'today' | 'yesterday' | 'wtd' | 'mtd' | 'all';

export interface CompanyDailyPerformanceItem {
  empresa: string;
  grupo: string;
  segmento: string;
  realizado: number;
  meta: number;
  gap: number;
  percentualMeta: number;
}

const GAP_CATCHUP_STORAGE_KEY = 'faturamento-dashboard-gap-catchup-enabled';
const DATE_PRESET_KEY = 'dashboard-date-preset';
const FILTERS_KEY = 'dashboard-filters';
const COMPARISON_ENABLED_KEY = 'dashboard-comparison-enabled';
const COMPARISON_START_KEY = 'dashboard-comparison-start';
const COMPARISON_END_KEY = 'dashboard-comparison-end';

function getReferenceDateForPreset(preset: DatePreset): Date {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  if (preset === 'yesterday') {
    date.setDate(date.getDate() - 1);
  }
  return date;
}

export interface DailyDataPoint {
  date: Date;
  total: number | null;
  goal?: number;
  [key: string]: number | Date | null | undefined; // For dynamic company keys
}

interface GoalHelpers {
  yearlyGoals: CompanyYearlyGoal[];
  setSelectedMonth: (month: number) => void;
  lines: RevenueLine[];
}

export function useFilters(data: FaturamentoRecord[], goalHelpers: GoalHelpers) {
  const { yearlyGoals, setSelectedMonth, lines } = goalHelpers;
  const [filters, setFilters] = useState<Filters>(() => {
    try {
      const raw = localStorage.getItem(FILTERS_KEY);
      if (!raw) return { empresas: [], grupos: [], segmentos: [], dataInicio: null, dataFim: null };
      const parsed = JSON.parse(raw);
      return {
        empresas: parsed.empresas ?? [],
        grupos: parsed.grupos ?? [],
        segmentos: parsed.segmentos ?? [],
        dataInicio: parsed.dataInicio ? new Date(parsed.dataInicio) : null,
        dataFim: parsed.dataFim ? new Date(parsed.dataFim) : null,
      };
    } catch {
      return { empresas: [], grupos: [], segmentos: [], dataInicio: null, dataFim: null };
    }
  });

  const [datePreset, setDatePreset] = useState<DatePreset>(() => {
    try {
      const raw = localStorage.getItem(DATE_PRESET_KEY);
      if (raw === 'today' || raw === 'yesterday' || raw === 'wtd' || raw === 'mtd' || raw === 'all') return raw;
    } catch { /* ignore */ }
    return 'today';
  });
  const [gapCatchUpEnabled, setGapCatchUpEnabled] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(GAP_CATCHUP_STORAGE_KEY);
      if (raw === null) return true;
      return raw === 'true';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(GAP_CATCHUP_STORAGE_KEY, String(gapCatchUpEnabled));
    } catch {
      // ignore storage failures
    }
  }, [gapCatchUpEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem(DATE_PRESET_KEY, datePreset);
    } catch { /* ignore */ }
  }, [datePreset]);

  useEffect(() => {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({
        empresas: filters.empresas,
        grupos: filters.grupos,
        segmentos: filters.segmentos,
        dataInicio: filters.dataInicio ? filters.dataInicio.toISOString() : null,
        dataFim: filters.dataFim ? filters.dataFim.toISOString() : null,
      }));
    } catch { /* ignore */ }
  }, [filters]);

  const lineSets = useMemo(() => {
    return {
      empresas: new Set(lines.map((l) => l.empresa)),
      grupos: new Set(lines.map((l) => l.grupo)),
      segmentos: new Set(lines.map((l) => l.segmento)),
    };
  }, [lines]);

  useEffect(() => {
    setFilters((prev) => ({
      ...prev,
      empresas: prev.empresas.filter((e) => lineSets.empresas.has(e)),
      grupos: prev.grupos.filter((g) => lineSets.grupos.has(g)),
      segmentos: prev.segmentos.filter((s) => lineSets.segmentos.has(s)),
    }));
  }, [lineSets]);

  const companyMetaInfo = useMemo(
    () => buildCompanyMetaInfo(yearlyGoals, lines),
    [yearlyGoals, lines]
  );
  const selectedCompaniesForGoals = useMemo(
    () => filterCompaniesByFilters(companyMetaInfo, filters),
    [companyMetaInfo, filters]
  );

  // Extract unique values for dropdowns
  const options = useMemo(() => {
    const isSingleDayPreset = datePreset === 'today' || datePreset === 'yesterday';
    const empresasComResultadoNoDia = new Set<string>();

    if (isSingleDayPreset) {
      const referenceDate = getReferenceDateForPreset(datePreset);
      const dayStart = new Date(referenceDate);
      dayStart.setHours(0, 0, 0, 0);
      const dayEnd = new Date(referenceDate);
      dayEnd.setHours(23, 59, 59, 999);

      data.forEach((record) => {
        if (record.faturamento <= 0) return;
        if (filters.grupos.length > 0 && !filters.grupos.includes(record.grupo)) return;
        if (filters.segmentos.length > 0 && !filters.segmentos.includes(record.segmento)) return;
        if (record.data < dayStart || record.data > dayEnd) return;
        empresasComResultadoNoDia.add(record.empresa);
      });
    }

    const empresas = (isSingleDayPreset
      ? Array.from(empresasComResultadoNoDia)
      : [...new Set(lines.map((c) => c.empresa))]
    ).sort();
    const grupos = [...new Set(lines.map((c) => c.grupo))].sort();
    const segmentos = [...new Set(lines.map((c) => c.segmento))].sort();

    const dates = data.map((d) => d.data.getTime());
    const minDate = dates.length > 0 ? new Date(Math.min(...dates)) : null;
    const maxDate = dates.length > 0 ? new Date(Math.max(...dates)) : null;

    return { empresas, grupos, segmentos, minDate, maxDate };
  }, [
    data,
    lines,
    datePreset,
    filters.grupos,
    filters.segmentos,
  ]);

  // Calculate effective date range based on preset or custom dates
  const effectiveDateRange = useMemo(() => {
    const today = new Date();
    today.setHours(23, 59, 59, 999);

    switch (datePreset) {
      case 'today': {
        const start = new Date(today);
        start.setHours(0, 0, 0, 0);
        const end = new Date(today);
        end.setHours(23, 59, 59, 999);
        return { start, end };
      }
      case 'yesterday': {
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const start = new Date(yesterday);
        start.setHours(0, 0, 0, 0);
        const end = new Date(yesterday);
        end.setHours(23, 59, 59, 999);
        return { start, end };
      }
      case 'wtd': {
        const start = startOfWeek(today, { weekStartsOn: 1 });
        const end = endOfWeek(today, { weekStartsOn: 1 });
        return { start, end };
      }
      case 'mtd': {
        const start = startOfMonth(today);
        const end = endOfMonth(today);
        return { start, end };
      }
      case 'all':
      default:
        return {
          start: filters.dataInicio,
          end: filters.dataFim,
        };
    }
  }, [datePreset, filters.dataInicio, filters.dataFim]);

  // Apply filters to data
  const filteredData = useMemo(() => {
    return data.filter((record) => {
      if (filters.empresas.length > 0 && !filters.empresas.includes(record.empresa)) return false;
      if (filters.grupos.length > 0 && !filters.grupos.includes(record.grupo)) return false;
      if (filters.segmentos.length > 0 && !filters.segmentos.includes(record.segmento)) return false;
      if (effectiveDateRange.start && record.data < effectiveDateRange.start) return false;
      if (effectiveDateRange.end && record.data > effectiveDateRange.end) return false;
      return true;
    });
  }, [data, filters, effectiveDateRange]);

  // Data filtered only by entities (all dates), used by goal models and month/week/day realized
  const entityFilteredData = useMemo(() => {
    return data.filter((record) => {
      if (filters.empresas.length > 0 && !filters.empresas.includes(record.empresa)) return false;
      if (filters.grupos.length > 0 && !filters.grupos.includes(record.grupo)) return false;
      if (filters.segmentos.length > 0 && !filters.segmentos.includes(record.segmento)) return false;
      return true;
    });
  }, [data, filters.empresas, filters.grupos, filters.segmentos]);

  const adaptiveGoalPlanner = useMemo(() => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    return buildAdaptiveDailyGoalPlanner(
      selectedCompaniesForGoals,
      entityFilteredData,
      referenceDate,
      { catchUpEnabled: gapCatchUpEnabled }
    );
  }, [selectedCompaniesForGoals, entityFilteredData, datePreset, gapCatchUpEnabled]);

  // Data filtered only by date (for total calculations)
  const dateFilteredData = useMemo(() => {
    return data.filter((record) => {
      if (effectiveDateRange.start && record.data < effectiveDateRange.start) return false;
      if (effectiveDateRange.end && record.data > effectiveDateRange.end) return false;
      return true;
    });
  }, [data, effectiveDateRange]);

  // Calculate KPIs
  const kpis = useMemo((): KPIs => {
    const faturamentoTotal = dateFilteredData.reduce((sum, r) => sum + r.faturamento, 0);
    const faturamentoFiltrado = filteredData.reduce((sum, r) => sum + r.faturamento, 0);

    const percentualDoTotal =
      faturamentoTotal > 0 ? (faturamentoFiltrado / faturamentoTotal) * 100 : 0;

    return {
      faturamentoFiltrado,
      faturamentoTotal,
      percentualDoTotal,
    };
  }, [filteredData, dateFilteredData]);

  // Use o mês da referência ativa (Hoje/Ontem) para metas
  useEffect(() => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    setSelectedMonth(referenceDate.getMonth() + 1);
  }, [setSelectedMonth, datePreset]);

  // Calculate goal metrics (metas)
  const goalMetrics = useMemo((): GoalMetrics => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    const dayStart = new Date(referenceDate);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(referenceDate);
    dayEnd.setHours(23, 59, 59, 999);

    const isAllPreset = datePreset === 'all';
    const hasCustomRange = isAllPreset && !!effectiveDateRange.start && !!effectiveDateRange.end;
    const refMonth = referenceDate.getMonth() + 1;
    const refYear = referenceDate.getFullYear();
    const diaAtual = referenceDate.getDate();
    const diasNoMes = getDaysInMonth(referenceDate);

    const selectedCompanies = selectedCompaniesForGoals;
    const isArCondicionado = selectedCompanies.length > 0 && selectedCompanies.every(
      (c) => c.segmento === 'AR CONDICIONADO'
    );

    // Get realized amount from filtered data
    const realizado = filteredData.reduce((sum, r) => sum + r.faturamento, 0);

    let metaMensal = 0;
    let metaProporcional = 0;
    const metaDia = getTotalBaseDailyGoal(selectedCompanies, referenceDate);
    const metaDiaAjustada = adaptiveGoalPlanner.getGoalForDate(referenceDate);
    const metaAno = getTotalYearlyGoal(selectedCompanies);
    const metasMensais = Array.from({ length: 12 }, (_, index) =>
      getTotalMonthlyGoal(selectedCompanies, index + 1)
    );

    if (hasCustomRange && effectiveDateRange.start && effectiveDateRange.end) {
      metaMensal = adaptiveGoalPlanner.sumGoalsForRange(
        effectiveDateRange.start,
        effectiveDateRange.end
      );
      metaProporcional = metaMensal;
    } else if (isAllPreset && filteredData.length > 0) {
      const uniqueDates = Array.from(
        new Set(filteredData.map((r) => r.data.toISOString().split('T')[0]))
      ).map((dateStr) => new Date(dateStr + 'T12:00:00'));

      const goalMap = adaptiveGoalPlanner.buildGoalMapForDates(uniqueDates);
      metaMensal = uniqueDates.reduce((sum, date) => {
        const key = date.toISOString().split('T')[0];
        return sum + (goalMap.get(key) || 0);
      }, 0);
      metaProporcional = metaMensal;
    } else {
      metaMensal = getTotalMonthlyGoal(selectedCompanies, refMonth);
      metaProporcional = adaptiveGoalPlanner.sumGoalsForRange(
        startOfMonth(referenceDate),
        referenceDate
      );
    }

    const gapProporcional = realizado - metaProporcional;
    const gapTotal = realizado - metaMensal;
    const percentualMeta = metaMensal > 0 ? (realizado / metaMensal) * 100 : 0;
    const percentualProporcional = metaProporcional > 0 ? (realizado / metaProporcional) * 100 : 0;

    // Week metrics
    const weekStart = startOfWeek(referenceDate, { weekStartsOn: 1 });
    const weekEnd = addDays(weekStart, 6);
    const diasNaSemana = Math.max(
      Math.min(
        Math.floor((dayEnd.getTime() - weekStart.getTime()) / (1000 * 60 * 60 * 24)) + 1,
        7
      ),
      0
    );

    const metaSemana = adaptiveGoalPlanner.sumGoalsForRange(weekStart, weekEnd);
    const esperadoSemanal = adaptiveGoalPlanner.sumGoalsForRange(weekStart, referenceDate);

    // Realizado semanal ate a referencia (Hoje/Ontem)
    const realizadoSemana = entityFilteredData
      .filter((record) => record.data >= weekStart && record.data <= dayEnd)
      .reduce((sum, r) => sum + r.faturamento, 0);

    const realizadoDia = entityFilteredData
      .filter((record) => record.data >= dayStart && record.data <= dayEnd)
      .reduce((sum, r) => sum + r.faturamento, 0);

    // Month metrics - always full month ate a referencia, independente do filtro de data
    const monthStart = startOfMonth(referenceDate);
    const realizadoMes = entityFilteredData
      .filter((record) => record.data >= monthStart && record.data <= dayEnd)
      .reduce((sum, r) => sum + r.faturamento, 0);

    // Year metrics
    const mesAtual = refMonth;
    const mesesNoAno = 12;

    // Get all year data for the reference year until reference day
    const realizadoAno = entityFilteredData
      .filter((record) => record.data.getFullYear() === refYear && record.data <= dayEnd)
      .reduce((sum, r) => sum + r.faturamento, 0);

    const coverage = (() => {
      const normalize = (d: Date) => {
        const nd = new Date(d);
        nd.setHours(0, 0, 0, 0);
        return nd;
      };
      const startDay = normalize(referenceDate);
      const endDay = normalize(referenceDate);
      const weekStartDate = normalize(weekStart);
      const monthStartDate = normalize(startOfMonth(referenceDate));
      const yearStartDate = normalize(startOfYear(referenceDate));

      const countDays = (records: FaturamentoRecord[], start: Date, end: Date) => {
        const unique = new Set<string>();
        records.forEach((r) => {
          const rd = normalize(r.data);
          if (rd >= start && rd <= end) {
            unique.add(rd.toISOString().split('T')[0]);
          }
        });
        return unique.size;
      };

      const build = (records: FaturamentoRecord[], start: Date, end: Date) => {
        const expected = Math.max(differenceInCalendarDays(end, start) + 1, 0);
        const observed = expected > 0 ? countDays(records, start, end) : 0;
        return {
          observed,
          expected,
          percent: expected > 0 ? observed / expected : 0,
        };
      };

      return {
        dia: build(entityFilteredData, startDay, endDay),
        semana: build(entityFilteredData, weekStartDate, endDay),
        mes: build(entityFilteredData, monthStartDate, endDay),
        ano: build(entityFilteredData, yearStartDate, endDay),
      };
    })();

    return {
      metaMensal,
      metaProporcional,
      realizado,
      realizadoMes,
      gapProporcional,
      gapTotal,
      percentualMeta,
      percentualProporcional,
      diasNoMes,
      diaAtual,
      metaSemana,
      realizadoSemana,
      diasNaSemana,
      esperadoSemanal,
      metaDia,
      metaDiaAjustada,
      realizadoDia,
      metaAno,
      metasMensais,
      realizadoAno,
      mesesNoAno,
      mesAtual,
      coverage,
      isArCondicionado,
    };
  }, [
    filteredData,
    datePreset,
    effectiveDateRange,
    selectedCompaniesForGoals,
    entityFilteredData,
    adaptiveGoalPlanner,
  ]);

  // Company goal breakdown for table - uses current reference month (Hoje/Ontem)
  const companyGoalData = useMemo(() => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    const refMonth = referenceDate.getMonth() + 1;
    const refYear = referenceDate.getFullYear();
    const monthStart = startOfMonth(referenceDate);
    const referenceEnd = new Date(referenceDate);
    referenceEnd.setHours(23, 59, 59, 999);

    const allMonthData = data.filter((record) => {
      const recordMonth = record.data.getMonth() + 1;
      const recordYear = record.data.getFullYear();
      return recordMonth === refMonth && recordYear === refYear && record.data <= referenceEnd;
    });

    const byEmpresa = new Map<string, number>();
    allMonthData.forEach((record) => {
      byEmpresa.set(record.empresa, (byEmpresa.get(record.empresa) || 0) + record.faturamento);
    });

    const historyByEmpresa = new Map<string, FaturamentoRecord[]>();
    data.forEach((record) => {
      const current = historyByEmpresa.get(record.empresa);
      if (current) {
        current.push(record);
      } else {
        historyByEmpresa.set(record.empresa, [record]);
      }
    });

    return companyMetaInfo.map((company) => {
      const realizado = byEmpresa.get(company.empresa) || 0;
      const metaMensal = company.metas[refMonth] || 0;
      const companyPlanner = buildAdaptiveDailyGoalPlanner(
        [company],
        historyByEmpresa.get(company.empresa) || [],
        referenceDate,
        { catchUpEnabled: gapCatchUpEnabled }
      );
      const metaProporcional = companyPlanner.sumGoalsForRange(monthStart, referenceDate);
      const percentualMeta = metaMensal > 0 ? (realizado / metaMensal) * 100 : 0;
      const gap = realizado - metaProporcional;

      return {
        empresa: company.empresa,
        grupo: company.grupo,
        segmento: company.segmento,
        realizado,
        metaMensal,
        metaProporcional,
        percentualMeta,
        gap,
      };
    }).filter((item) => item.metaMensal > 0 || item.realizado > 0);
  }, [data, companyMetaInfo, datePreset, gapCatchUpEnabled]);

  // Daily totals for chart - with breakdown by company and group
  const dailyData = useMemo(() => {
    const grouped = new Map<string, { total: number; byCompany: Map<string, number>; byGroup: Map<string, number> }>();
    const referenceDate = getReferenceDateForPreset(datePreset);

    const ensureDateBucket = (date: Date) => {
      const key = date.toISOString().split('T')[0];
      if (!grouped.has(key)) {
        grouped.set(key, { total: 0, byCompany: new Map(), byGroup: new Map() });
      }
      return key;
    };

    if ((datePreset === 'mtd' || datePreset === 'wtd') && effectiveDateRange.start && effectiveDateRange.end) {
      const rangeStart = new Date(effectiveDateRange.start);
      const rangeEnd = new Date(effectiveDateRange.end);
      let cursor = new Date(rangeStart);
      cursor.setHours(12, 0, 0, 0);
      while (cursor <= rangeEnd) {
        ensureDateBucket(cursor);
        cursor = addDays(cursor, 1);
      }
    }

    filteredData.forEach((record) => {
      const dateKey = ensureDateBucket(record.data);
      const dayData = grouped.get(dateKey)!;
      dayData.total += record.faturamento;

      // Track by company
      const currentCompanyTotal = dayData.byCompany.get(record.empresa) || 0;
      dayData.byCompany.set(record.empresa, currentCompanyTotal + record.faturamento);

      // Track by group
      const currentGroupTotal = dayData.byGroup.get(record.grupo) || 0;
      dayData.byGroup.set(record.grupo, currentGroupTotal + record.faturamento);
    });

    const referenceKey = referenceDate.toISOString().split('T')[0];
    const inRange = (!effectiveDateRange.start || referenceDate >= effectiveDateRange.start) &&
      (!effectiveDateRange.end || referenceDate <= effectiveDateRange.end);
    if (datePreset !== 'all' && inRange && !grouped.has(referenceKey)) {
      grouped.set(referenceKey, { total: 0, byCompany: new Map(), byGroup: new Map() });
    }

    const dates = Array.from(grouped.keys()).map((date) => new Date(date + 'T12:00:00'));
    const goalMap = adaptiveGoalPlanner.buildGoalMapForDates(dates);

    return Array.from(grouped.entries())
      .map(([date, { total, byCompany, byGroup }]) => {
        const pointDate = new Date(date + 'T12:00:00');
        const hasRecords = byCompany.size > 0 || byGroup.size > 0;
        const isFuture = pointDate > referenceDate;
        const point: DailyDataPoint = {
          date: pointDate, // Use noon to avoid timezone issues
          total: !hasRecords && isFuture ? null : total,
        };

        const goalValue = goalMap.get(date) || 0;
        if (goalValue > 0) {
          point.goal = goalValue;
        }

        // Add company-specific values
        byCompany.forEach((value, empresa) => {
          point[empresa] = value;
        });

        // Add group-specific values (prefixed to avoid collision)
        byGroup.forEach((value, grupo) => {
          point[`group_${grupo}`] = value;
        });

        return point;
      })
      .sort((a, b) => a.date.getTime() - b.date.getTime());
  }, [filteredData, adaptiveGoalPlanner, datePreset, effectiveDateRange]);

  // Comparison state
  const [comparisonEnabled, setComparisonEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COMPARISON_ENABLED_KEY) === 'true';
    } catch { return false; }
  });
  const [customComparisonStart, setCustomComparisonStart] = useState<Date | null>(() => {
    try {
      const raw = localStorage.getItem(COMPARISON_START_KEY);
      return raw ? new Date(raw) : null;
    } catch { return null; }
  });
  const [customComparisonEnd, setCustomComparisonEnd] = useState<Date | null>(() => {
    try {
      const raw = localStorage.getItem(COMPARISON_END_KEY);
      return raw ? new Date(raw) : null;
    } catch { return null; }
  });

  useEffect(() => {
    try { localStorage.setItem(COMPARISON_ENABLED_KEY, String(comparisonEnabled)); } catch { /* ignore */ }
  }, [comparisonEnabled]);

  useEffect(() => {
    try {
      if (customComparisonStart) localStorage.setItem(COMPARISON_START_KEY, customComparisonStart.toISOString());
      else localStorage.removeItem(COMPARISON_START_KEY);
    } catch { /* ignore */ }
  }, [customComparisonStart]);

  useEffect(() => {
    try {
      if (customComparisonEnd) localStorage.setItem(COMPARISON_END_KEY, customComparisonEnd.toISOString());
      else localStorage.removeItem(COMPARISON_END_KEY);
    } catch { /* ignore */ }
  }, [customComparisonEnd]);

  // Calculate comparison date range based on current period duration
  const comparisonDateRange = useMemo(() => {
    if (!comparisonEnabled) return null;

    // If custom comparison dates are set, use them
    if (customComparisonStart && customComparisonEnd) {
      return {
        start: customComparisonStart,
        end: customComparisonEnd,
        label: 'Período Personalizado',
      };
    }

    // Calculate based on effective date range
    const { start: currentStart, end: currentEnd } = effectiveDateRange;

    // If no date range is set (all data), skip comparison
    if (!currentStart || !currentEnd) return null;

    // Calculate duration in days
    const durationMs = currentEnd.getTime() - currentStart.getTime();
    const durationDays = Math.ceil(durationMs / (1000 * 60 * 60 * 24)) + 1;

    // Comparison period is immediately before current period
    const compEnd = new Date(currentStart);
    compEnd.setDate(compEnd.getDate() - 1);
    compEnd.setHours(23, 59, 59, 999);

    const compStart = new Date(compEnd);
    compStart.setDate(compStart.getDate() - durationDays + 1);
    compStart.setHours(0, 0, 0, 0);

    // Generate label based on duration
    let label: string;
    if (durationDays === 1) {
      label = 'Dia Anterior';
    } else if (durationDays <= 7) {
      label = `${durationDays} dias anteriores`;
    } else {
      label = 'Período Anterior';
    }

    return { start: compStart, end: compEnd, label };
  }, [comparisonEnabled, customComparisonStart, customComparisonEnd, effectiveDateRange]);

  // Filter data for comparison period
  const comparisonFilteredData = useMemo(() => {
    if (!comparisonDateRange) return null;

    return data.filter((record) => {
      if (filters.empresas.length > 0 && !filters.empresas.includes(record.empresa)) return false;
      if (filters.grupos.length > 0 && !filters.grupos.includes(record.grupo)) return false;
      if (filters.segmentos.length > 0 && !filters.segmentos.includes(record.segmento)) return false;
      if (record.data < comparisonDateRange.start) return false;
      if (record.data > comparisonDateRange.end) return false;
      return true;
    });
  }, [data, filters, comparisonDateRange]);

  // Daily totals for comparison data
  const comparisonDailyData = useMemo(() => {
    if (!comparisonFilteredData) return null;

    const grouped = new Map<string, { total: number; byCompany: Map<string, number>; byGroup: Map<string, number> }>();
    const ensureDateBucket = (date: Date) => {
      const key = date.toISOString().split('T')[0];
      if (!grouped.has(key)) {
        grouped.set(key, { total: 0, byCompany: new Map(), byGroup: new Map() });
      }
      return key;
    };

    if ((datePreset === 'mtd' || datePreset === 'wtd') && comparisonDateRange) {
      const monthStart = new Date(comparisonDateRange.start);
      monthStart.setHours(12, 0, 0, 0);
      const monthEnd = new Date(comparisonDateRange.end);
      monthEnd.setHours(12, 0, 0, 0);
      let cursor = new Date(monthStart);
      while (cursor <= monthEnd) {
        ensureDateBucket(cursor);
        cursor = addDays(cursor, 1);
      }
    }

    comparisonFilteredData.forEach((record) => {
      const dateKey = ensureDateBucket(record.data);
      const dayData = grouped.get(dateKey)!;
      dayData.total += record.faturamento;

      // Track by company
      const currentCompanyTotal = dayData.byCompany.get(record.empresa) || 0;
      dayData.byCompany.set(record.empresa, currentCompanyTotal + record.faturamento);

      // Track by group
      const currentGroupTotal = dayData.byGroup.get(record.grupo) || 0;
      dayData.byGroup.set(record.grupo, currentGroupTotal + record.faturamento);
    });

    return Array.from(grouped.entries())
      .map(([date, { total, byCompany, byGroup }]) => {
        const hasRecords = byCompany.size > 0 || byGroup.size > 0;
        const point: DailyDataPoint = {
          date: new Date(date + 'T12:00:00'), // Use noon to avoid timezone issues
          total: hasRecords ? total : null,
        };

        // Add company-specific values
        byCompany.forEach((value, empresa) => {
          point[empresa] = value;
        });

        // Add group-specific values (prefixed to avoid collision)
        byGroup.forEach((value, grupo) => {
          point[`group_${grupo}`] = value;
        });

        return point;
      })
      .sort((a, b) => a.date.getTime() - b.date.getTime());
  }, [comparisonFilteredData, datePreset, comparisonDateRange]);

  const comparisonLabel = comparisonDateRange?.label ?? null;

  const getGoalForDate = useCallback(
    (date: Date) => adaptiveGoalPlanner.getGoalForDate(date),
    [adaptiveGoalPlanner]
  );

  // Get list of companies in the chart data (for line chart)
  const companiesWithDailyResult = useMemo(() => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    const dayStart = new Date(referenceDate);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(referenceDate);
    dayEnd.setHours(23, 59, 59, 999);

    const totals = new Map<string, number>();
    entityFilteredData
      .filter((record) => record.data >= dayStart && record.data <= dayEnd)
      .forEach((record) => {
        totals.set(record.empresa, (totals.get(record.empresa) || 0) + record.faturamento);
      });

    return Array.from(totals.entries())
      .filter(([, total]) => total > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([empresa]) => empresa);
  }, [entityFilteredData, datePreset]);

  const chartCompanies = useMemo(() => {
    if (filters.empresas.length > 1) return filters.empresas;
    if (datePreset === 'today' && filters.empresas.length === 0) {
      return companiesWithDailyResult;
    }
    return [];
  }, [filters.empresas, datePreset, companiesWithDailyResult]);

  const companyDailyPerformance = useMemo((): CompanyDailyPerformanceItem[] => {
    const referenceDate = getReferenceDateForPreset(datePreset);
    const dayStart = new Date(referenceDate);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(referenceDate);
    dayEnd.setHours(23, 59, 59, 999);

    const realizedByCompany = new Map<string, number>();
    entityFilteredData
      .filter((record) => record.data >= dayStart && record.data <= dayEnd)
      .forEach((record) => {
        realizedByCompany.set(
          record.empresa,
          (realizedByCompany.get(record.empresa) || 0) + record.faturamento
        );
      });

    const historyByEmpresa = new Map<string, FaturamentoRecord[]>();
    entityFilteredData.forEach((record) => {
      const current = historyByEmpresa.get(record.empresa);
      if (current) {
        current.push(record);
      } else {
        historyByEmpresa.set(record.empresa, [record]);
      }
    });

    return selectedCompaniesForGoals
      .map((company) => {
        const companyPlanner = buildAdaptiveDailyGoalPlanner(
          [company],
          historyByEmpresa.get(company.empresa) || [],
          referenceDate,
          { catchUpEnabled: gapCatchUpEnabled }
        );
        const meta = companyPlanner.getGoalForDate(referenceDate);
        const realizado = realizedByCompany.get(company.empresa) || 0;
        const gap = realizado - meta;
        const percentualMeta = meta > 0 ? (realizado / meta) * 100 : 0;

        return {
          empresa: company.empresa,
          grupo: company.grupo,
          segmento: company.segmento,
          realizado,
          meta,
          gap,
          percentualMeta,
        };
      })
      .filter((item) => item.realizado > 0)
      .sort((a, b) => b.realizado - a.realizado);
  }, [selectedCompaniesForGoals, entityFilteredData, datePreset, gapCatchUpEnabled]);

  // Get all unique companies from filtered data (for stacked bar chart)
  const allCompaniesInData = useMemo(() => {
    const companyTotals = new Map<string, number>();
    filteredData.forEach((record) => {
      companyTotals.set(record.empresa, (companyTotals.get(record.empresa) || 0) + record.faturamento);
    });
    return Array.from(companyTotals.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([empresa]) => empresa);
  }, [filteredData]);

  // Get all unique groups from filtered data (sorted by total revenue)
  const allGroupsInData = useMemo(() => {
    const groupTotals = new Map<string, number>();
    filteredData.forEach((record) => {
      groupTotals.set(record.grupo, (groupTotals.get(record.grupo) || 0) + record.faturamento);
    });
    return Array.from(groupTotals.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([grupo]) => grupo);
  }, [filteredData]);

  // Group breakdown
  const groupBreakdown = useMemo(() => {
    const grouped = new Map<string, number>();

    filteredData.forEach((record) => {
      grouped.set(record.grupo, (grouped.get(record.grupo) || 0) + record.faturamento);
    });

    return Array.from(grouped.entries())
      .map(([grupo, total]) => ({ grupo, total }))
      .sort((a, b) => b.total - a.total);
  }, [filteredData]);

  // Segment breakdown
  const segmentBreakdown = useMemo(() => {
    const grouped = new Map<string, number>();

    filteredData.forEach((record) => {
      grouped.set(record.segmento, (grouped.get(record.segmento) || 0) + record.faturamento);
    });

    return Array.from(grouped.entries())
      .map(([segmento, total]) => ({ segmento, total }))
      .sort((a, b) => b.total - a.total);
  }, [filteredData]);

  // Empresa breakdown
  const empresaBreakdown = useMemo(() => {
    const grouped = new Map<string, number>();

    filteredData.forEach((record) => {
      grouped.set(record.empresa, (grouped.get(record.empresa) || 0) + record.faturamento);
    });

    return Array.from(grouped.entries())
      .map(([empresa, total]) => ({ empresa, total }))
      .sort((a, b) => b.total - a.total);
  }, [filteredData]);

  // Pie chart data - filtered vs rest
  const pieData = useMemo(() => {
    const filtrado = kpis.faturamentoFiltrado;
    const resto = kpis.faturamentoTotal - filtrado;

    return [
      { name: 'Selecionado', value: filtrado },
      { name: 'Outros', value: resto },
    ];
  }, [kpis]);

  // Segment pie chart data
  const segmentPieData = useMemo(() => {
    const grouped = new Map<string, number>();

    dateFilteredData.forEach((record) => {
      grouped.set(record.segmento, (grouped.get(record.segmento) || 0) + record.faturamento);
    });

    return Array.from(grouped.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [dateFilteredData]);

  const updateFilter = useCallback(
    <K extends keyof Filters>(key: K, value: Filters[K]) => {
      setFilters((prev) => ({ ...prev, [key]: value }));
      if (key === 'dataInicio' || key === 'dataFim') {
        setDatePreset('all');
      }
    },
    []
  );

  const toggleFilterValue = useCallback(
    (key: 'empresas' | 'grupos' | 'segmentos', value: string) => {
      setFilters((prev) => {
        const current = prev[key];
        const updated = current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value];
        return { ...prev, [key]: updated };
      });
    },
    []
  );

  const setDatePresetHandler = useCallback((preset: DatePreset) => {
    setDatePreset(preset);
    if (preset !== 'all') {
      setFilters((prev) => ({ ...prev, dataInicio: null, dataFim: null }));
    }
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({
      empresas: [],
      grupos: [],
      segmentos: [],
      dataInicio: null,
      dataFim: null,
    });
    setDatePreset('mtd');
    setComparisonEnabled(false);
    setCustomComparisonStart(null);
    setCustomComparisonEnd(null);
  }, []);

  const toggleComparison = useCallback(() => {
    setComparisonEnabled((prev) => !prev);
  }, []);

  const setCustomComparisonRange = useCallback((start: Date | null, end: Date | null) => {
    setCustomComparisonStart(start);
    setCustomComparisonEnd(end);
  }, []);

  const clearCustomComparison = useCallback(() => {
    setCustomComparisonStart(null);
    setCustomComparisonEnd(null);
  }, []);

  const toggleGapCatchUp = useCallback(() => {
    setGapCatchUpEnabled((prev) => !prev);
  }, []);

  const hasActiveFilters =
    filters.empresas.length > 0 ||
    filters.grupos.length > 0 ||
    filters.segmentos.length > 0 ||
    filters.dataInicio !== null ||
    filters.dataFim !== null ||
    datePreset !== 'all' ||
    comparisonEnabled;

  return {
    filters,
    options,
    filteredData,
    kpis,
    goalMetrics,
    companyGoalData,
    dailyData,
    comparisonDailyData,
    comparisonLabel,
    comparisonEnabled,
    comparisonDateRange,
    customComparisonStart,
    customComparisonEnd,
    gapCatchUpEnabled,
    getGoalForDate,
    chartCompanies,
    companyDailyPerformance,
    allCompaniesInData,
    allGroupsInData,
    groupBreakdown,
    segmentBreakdown,
    empresaBreakdown,
    pieData,
    segmentPieData,
    datePreset,
    effectiveDateRange,
    updateFilter,
    toggleFilterValue,
    setDatePreset: setDatePresetHandler,
    clearFilters,
    hasActiveFilters,
    toggleComparison,
    setCustomComparisonRange,
    clearCustomComparison,
    toggleGapCatchUp,
  };
}
