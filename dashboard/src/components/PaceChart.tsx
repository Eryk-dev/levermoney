import { useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import { formatBRL } from '../utils/dataParser';
import type { DatePreset } from '../hooks/useFilters';
import { buildForecastModel, type SeasonalityFactors } from '../utils/projectionEngine';
import { useIsMobile } from '../hooks/useIsMobile';
import styles from './PaceChart.module.css';

interface DailyDataPoint {
  date: Date;
  total: number | null;
}

interface PaceChartProps {
  dailyData: DailyDataPoint[];
  allHistoricalData?: DailyDataPoint[];
  metaMensal: number;
  metaAno?: number;
  diasNoMes: number;
  diaAtual: number;
  mesReferencia?: number;
  anoReferencia?: number;
  datePreset?: DatePreset;
  seasonalityFactors?: SeasonalityFactors;
  getGoalForDate?: (date: Date) => number;
  monthlyGoals?: number[];
}

function formatCompact(value: number): string {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(0)}k`;
  return value.toString();
}

function formatK(value: number): string {
  if (value === 0) return '0';
  if (Math.abs(value) < 1000) return value.toFixed(0);
  return `${(value / 1000).toFixed(0)}k`;
}

const WEEKDAY_NAMES = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
const MONTH_NAMES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];

interface PaceChartTooltipProps {
  active?: boolean;
  payload?: Array<{
    value?: number;
    dataKey?: string | number;
    color?: string;
  }>;
  label?: string | number;
}

function PaceChartTooltip({ active, payload, label }: PaceChartTooltipProps) {
  if (!active || !payload?.length) return null;

  const labels: Record<string, string> = {
    realizado: 'Realizado',
    meta: 'Meta',
    projecao: 'Projeção',
  };

  return (
    <div className={styles.tooltip}>
      <div className={styles.tooltipHeader}>{label}</div>
      {payload.map((entry) => {
        const value = typeof entry.value === 'number' ? entry.value : undefined;
        if (value === undefined) return null;
        const key = typeof entry.dataKey === 'string' ? entry.dataKey : String(entry.dataKey);
        return (
          <div key={key} className={styles.tooltipRow}>
            <span
              className={styles.tooltipDot}
              style={{ background: entry.color }}
            />
            <span>{labels[key] || key}:</span>
            <span className={styles.tooltipValue}>{formatBRL(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function PaceChart({
  dailyData,
  allHistoricalData,
  metaMensal,
  metaAno = 0,
  diasNoMes,
  diaAtual,
  mesReferencia,
  anoReferencia,
  datePreset = 'mtd',
  seasonalityFactors,
  getGoalForDate,
  monthlyGoals,
}: PaceChartProps) {
  const isMobile = useIsMobile();
  const realizedDailyData = useMemo(
    () => dailyData.filter((d): d is { date: Date; total: number } => typeof d.total === 'number'),
    [dailyData]
  );
  const referenceDate = useMemo(() => {
    if (realizedDailyData.length > 0) {
      const latestDate = realizedDailyData.reduce((latest, d) =>
        d.date > latest ? d.date : latest, realizedDailyData[0].date);
      return new Date(latestDate.getFullYear(), latestDate.getMonth(), latestDate.getDate(), 12, 0, 0);
    }
    if (anoReferencia && mesReferencia) {
      return new Date(anoReferencia, mesReferencia - 1, Math.max(diaAtual || 1, 1), 12, 0, 0);
    }
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12, 0, 0);
  }, [realizedDailyData, anoReferencia, mesReferencia, diaAtual]);

  const refMonth = referenceDate.getMonth() + 1;
  const refYear = referenceDate.getFullYear();

  // Calculate average daily revenue from filtered data only
  // Note: dailyData is already filtered by empresa/grupo when filters are applied
  const avgDaily = useMemo(() => {
    const values = realizedDailyData.filter((d) => d.total > 0).map((d) => d.total);

    // If we have data, use it
    if (values.length > 0) {
      return values.reduce((a, b) => a + b, 0) / values.length;
    }

    // Fallback to meta-based estimate
    return metaMensal / diasNoMes;
  }, [realizedDailyData, metaMensal, diasNoMes]);

  const forecastModel = useMemo(() => {
    const source = allHistoricalData && allHistoricalData.length > 0
      ? allHistoricalData.filter((d): d is { date: Date; total: number } => typeof d.total === 'number')
      : realizedDailyData;
    return buildForecastModel(source, seasonalityFactors, referenceDate, avgDaily);
  }, [allHistoricalData, realizedDailyData, seasonalityFactors, referenceDate, avgDaily]);

  const chartState = useMemo(() => {
    const dataSource = allHistoricalData && allHistoricalData.length > 0
      ? allHistoricalData.filter((d): d is { date: Date; total: number } => typeof d.total === 'number')
      : realizedDailyData;
    const toKey = (date: Date) => date.toISOString().split('T')[0];
    const metaDiaria = metaMensal / diasNoMes;
    const goalForDate = (date: Date) => {
      if (getGoalForDate) {
        return getGoalForDate(new Date(date.getFullYear(), date.getMonth(), date.getDate(), 12, 0, 0));
      }
      return metaDiaria;
    };

    const dailyTotals = new Map<string, number>();
    realizedDailyData.forEach((d) => {
      const key = toKey(d.date);
      dailyTotals.set(key, (dailyTotals.get(key) || 0) + d.total);
    });

    if (datePreset === 'today' || datePreset === 'yesterday') {
      const targetDate = referenceDate;
      const dayTotal = realizedDailyData
        .filter(d => d.date.toDateString() === targetDate.toDateString())
        .reduce((sum, d) => sum + d.total, 0);

      return {
        mode: 'single-day' as const,
        meta: goalForDate(targetDate),
        realizado: dayTotal,
        projecao: dayTotal,
        title: datePreset === 'today' ? 'Hoje' : 'Ontem',
      };
    }

    if (datePreset === 'wtd') {
      const startOfWeek = new Date(referenceDate);
      const dayOfWeek = referenceDate.getDay();
      const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
      startOfWeek.setDate(referenceDate.getDate() - mondayOffset);

      let metaSemana = 0;
      for (let i = 0; i < 7; i++) {
        const date = new Date(startOfWeek);
        date.setDate(startOfWeek.getDate() + i);
        metaSemana += goalForDate(date);
      }

      const dailyDataPoints: { label: string; realizado?: number; meta: number; projecao?: number }[] = [];
      for (let i = 0; i < 7; i++) {
        const date = new Date(startOfWeek);
        date.setDate(startOfWeek.getDate() + i);
        const key = toKey(date);
        const actual = dailyTotals.get(key);
        const isPast = date <= referenceDate;
        dailyDataPoints.push({
          label: WEEKDAY_NAMES[i],
          realizado: isPast && actual !== undefined ? actual : undefined,
          meta: goalForDate(date),
          projecao: !isPast ? forecastModel.forecastForDate(date).p50 : undefined,
        });
      }

      const cumulativeData: { label: string; realizado?: number; meta: number; projecao?: number }[] = [];
      let cumRealizado = 0;
      let cumMeta = 0;
      for (let i = 0; i < 7; i++) {
        const date = new Date(startOfWeek);
        date.setDate(startOfWeek.getDate() + i);
        const key = toKey(date);
        const dayTotal = dailyTotals.get(key) || 0;
        cumRealizado += dayTotal;
        cumMeta += goalForDate(date);
        const isPast = date <= referenceDate;
        cumulativeData.push({
          label: WEEKDAY_NAMES[i],
          realizado: isPast ? cumRealizado : undefined,
          meta: cumMeta,
        });
      }

      const rawDay = referenceDate.getDay();
      const currentDayOfWeek = rawDay === 0 ? 6 : rawDay - 1;
      let cumulativeProjection = cumulativeData[currentDayOfWeek]?.realizado || 0;
      for (let i = currentDayOfWeek; i < 7; i++) {
        const projectionDate = new Date(startOfWeek);
        projectionDate.setDate(startOfWeek.getDate() + i);
        if (i > currentDayOfWeek) {
          cumulativeProjection += forecastModel.forecastForDate(projectionDate).p50;
        }
        cumulativeData[i].projecao = cumulativeProjection;
      }

      const totalRealizado = cumulativeData[currentDayOfWeek]?.realizado || 0;
      const totalProjecao = cumulativeData[6]?.projecao || totalRealizado;

      return {
        mode: 'dual' as const,
        daily: {
          data: dailyDataPoints,
          meta: metaDiaria,
          title: 'Ritmo Diário da Semana',
          xAxisKey: 'label',
          xAxisFormatter: (v: string) => v,
        },
        cumulative: {
          data: cumulativeData,
          meta: metaSemana,
          realizado: totalRealizado,
          projecao: totalProjecao,
          title: 'Ritmo Acumulado da Semana',
          xAxisKey: 'label',
          xAxisFormatter: (v: string) => v,
        },
      };
    }

    if (datePreset === 'all') {
      const fallbackMonthly = metaAno > 0 ? metaAno / 12 : metaMensal;
      const resolvedMonthlyGoals = monthlyGoals && monthlyGoals.length === 12
        ? monthlyGoals
        : Array.from({ length: 12 }, () => fallbackMonthly);
      const metaAnual = metaAno > 0
        ? metaAno
        : resolvedMonthlyGoals.reduce((sum, value) => sum + value, 0);
      const currentMonth = referenceDate.getMonth();

      const monthlyTotals = new Map<number, number>();
      dataSource.forEach(d => {
        if (d.date.getFullYear() === refYear) {
          const month = d.date.getMonth();
          monthlyTotals.set(month, (monthlyTotals.get(month) || 0) + d.total);
        }
      });

      const monthlyForecast = Array.from({ length: 12 }, () => 0);
      for (let m = currentMonth; m < 12; m++) {
        const daysInMonth = new Date(refYear, m + 1, 0).getDate();
        for (let day = 1; day <= daysInMonth; day++) {
          if (m === currentMonth && day <= referenceDate.getDate()) continue;
          const forecastDate = new Date(refYear, m, day, 12, 0, 0);
          monthlyForecast[m] += forecastModel.forecastForDate(forecastDate).p50;
        }
      }

      const dailyChartData: { label: string; realizado?: number; meta: number; projecao?: number }[] = [];
      for (let m = 0; m < 12; m++) {
        const actual = monthlyTotals.get(m) || 0;
        const isPast = m < currentMonth;
        const isCurrent = m === currentMonth;
        dailyChartData.push({
          label: MONTH_NAMES[m],
          realizado: isPast || isCurrent ? actual : undefined,
          meta: resolvedMonthlyGoals[m] || 0,
          projecao: m >= currentMonth ? actual + monthlyForecast[m] : undefined,
        });
      }

      const cumulativeData: { label: string; realizado?: number; meta: number; projecao?: number }[] = [];
      let cumRealizado = 0;
      let cumMeta = 0;
      for (let m = 0; m < 12; m++) {
        const monthTotal = monthlyTotals.get(m) || 0;
        cumRealizado += monthTotal;
        cumMeta += resolvedMonthlyGoals[m] || 0;
        cumulativeData.push({
          label: MONTH_NAMES[m],
          realizado: m <= currentMonth ? cumRealizado : undefined,
          meta: cumMeta,
        });
      }

      let projCum = cumulativeData[currentMonth]?.realizado || 0;
      for (let m = currentMonth; m < 12; m++) {
        projCum += monthlyForecast[m];
        cumulativeData[m].projecao = projCum;
      }

      const totalRealizado = cumulativeData[currentMonth]?.realizado || 0;
      const totalProjecao = cumulativeData[11]?.projecao || totalRealizado;

      return {
        mode: 'dual' as const,
        daily: {
          data: dailyChartData,
          meta: resolvedMonthlyGoals[currentMonth] || 0,
          title: 'Ritmo Mensal do Ano',
          xAxisKey: 'label',
          xAxisFormatter: (v: string) => v,
        },
        cumulative: {
          data: cumulativeData,
          meta: metaAnual,
          realizado: totalRealizado,
          projecao: totalProjecao,
          title: 'Ritmo Acumulado do Ano',
          xAxisKey: 'label',
          xAxisFormatter: (v: string) => v,
        },
      };
    }

    const dailyChartData: { dia: number; realizado?: number; meta: number; projecao?: number }[] = [];
    for (let dia = 1; dia <= diasNoMes; dia++) {
      const date = new Date(refYear, refMonth - 1, dia, 12, 0, 0);
      const key = toKey(date);
      const actual = dailyTotals.get(key);
      const isPast = dia <= diaAtual;
      dailyChartData.push({
        dia,
        realizado: isPast && actual !== undefined ? actual : undefined,
        meta: goalForDate(date),
        projecao: !isPast ? forecastModel.forecastForDate(date).p50 : undefined,
      });
    }

    const cumulativeData: { dia: number; realizado?: number; meta: number; projecao?: number }[] = [];
    let cumulative = 0;
    let cumulativeMeta = 0;
    for (let dia = 1; dia <= diasNoMes; dia++) {
      const date = new Date(refYear, refMonth - 1, dia, 12, 0, 0);
      const key = toKey(date);
      const dayValue = dailyTotals.get(key) || 0;
      cumulative += dayValue;
      cumulativeMeta += goalForDate(date);
      const metaCum = cumulativeMeta;
      if (dia <= diaAtual) {
        cumulativeData.push({ dia, realizado: cumulative, meta: metaCum });
      } else {
        cumulativeData.push({ dia, meta: metaCum });
      }
    }

    let cumulativeProjection = cumulativeData[diaAtual - 1]?.realizado || 0;
    for (let dia = diaAtual; dia <= diasNoMes; dia++) {
      const projectionDate = new Date(refYear, refMonth - 1, dia, 12, 0, 0);
      if (dia > diaAtual) {
        cumulativeProjection += forecastModel.forecastForDate(projectionDate).p50;
      }
      cumulativeData[dia - 1].projecao = cumulativeProjection;
    }

    const totalRealizado = cumulativeData[diaAtual - 1]?.realizado || 0;
    const totalProjecao = cumulativeData[diasNoMes - 1]?.projecao || totalRealizado;

    return {
      mode: 'dual' as const,
      daily: {
        data: dailyChartData,
        meta: metaDiaria,
        title: 'Ritmo Diário do Mês',
        xAxisKey: 'dia',
        xAxisFormatter: (v: number) => {
          if (diasNoMes <= 7) return v.toString();
          return (v % 5 === 0 || v === 1 || v === diasNoMes) ? v.toString() : '';
        },
      },
      cumulative: {
        data: cumulativeData,
        meta: metaMensal,
        realizado: totalRealizado,
        projecao: totalProjecao,
        title: 'Ritmo Acumulado do Mês',
        xAxisKey: 'dia',
        xAxisFormatter: (v: number) => {
          if (diasNoMes <= 7) return v.toString();
          return (v % 5 === 0 || v === 1 || v === diasNoMes) ? v.toString() : '';
        },
      },
    };
  }, [
    realizedDailyData,
    allHistoricalData,
    datePreset,
    metaMensal,
    metaAno,
    monthlyGoals,
    diasNoMes,
    diaAtual,
    refMonth,
    refYear,
    referenceDate,
    forecastModel,
    getGoalForDate,
  ]);

  const summary = chartState.mode === 'single-day'
    ? { meta: chartState.meta, realizado: chartState.realizado, projecao: chartState.projecao, title: chartState.title }
    : { meta: chartState.cumulative.meta, realizado: chartState.cumulative.realizado, projecao: chartState.cumulative.projecao, title: chartState.cumulative.title };

  const percentMeta = summary.meta > 0 ? Math.round((summary.projecao / summary.meta) * 100) : 0;
  const statusClass = percentMeta >= 100 ? styles.positive : percentMeta >= 80 ? styles.warning : styles.negative;

  if (chartState.mode === 'single-day') {
    const metaDia = chartState.meta;
    const percent = metaDia > 0 ? Math.round((chartState.realizado / metaDia) * 100) : 0;
    const dailyGap = chartState.realizado - metaDia;

    return (
      <div className={styles.container}>
        <div className={styles.header}>
          <div className={styles.title}>{chartState.title}</div>
          <div className={styles.summary}>
            <div className={styles.summaryItem}>
              <span className={styles.summaryLabel}>Realizado</span>
              <span className={styles.summaryValue}>{formatBRL(chartState.realizado)}</span>
            </div>
            <div className={styles.summaryItem}>
              <span className={styles.summaryLabel}>Meta</span>
              <span className={styles.summaryValue}>{formatBRL(metaDia)}</span>
            </div>
          </div>
        </div>
        <div className={styles.simpleProgress}>
          <div className={styles.progressBar}>
            <div
              className={`${styles.progressFill} ${percent >= 100 ? styles.success : percent >= 80 ? styles.warning : styles.danger}`}
              style={{ width: `${Math.min(percent, 100)}%` }}
            />
            <div className={styles.progressMarker} style={{ left: '100%' }} />
          </div>
          <div className={styles.progressLabels}>
            <span>{percent}% da meta</span>
            <span className={percent >= 100 ? styles.positive : styles.negative}>
              {dailyGap >= 0 ? '+' : ''}{formatBRL(dailyGap)}
            </span>
          </div>
        </div>
      </div>
    );
  }

  const { daily, cumulative } = chartState;
  const dailyMetaLabel = datePreset === 'all' ? 'Meta mensal' : 'Meta diária';
  const dailyMetaLabelValue = datePreset === 'all'
    ? daily.meta
    : (metaMensal / diasNoMes);

  return (
    <div className={styles.container}>
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <div className={styles.sectionTitle}>{daily.title}</div>
          <div className={styles.sectionMeta}>
            {dailyMetaLabel}: {formatCompact(dailyMetaLabelValue)}
          </div>
        </div>
        <div className={styles.chartWrapperDaily}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={daily.data} margin={{ top: 8, right: 48, left: 0, bottom: 0 }}>
              <XAxis
                dataKey={daily.xAxisKey}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
                tickFormatter={daily.xAxisFormatter}
                tickMargin={8}
                interval="preserveStartEnd"
                scale="point"
                padding={{ left: 20, right: 20 }}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
                tickFormatter={formatK}
                tickMargin={8}
                width={48}
              />
              {!isMobile && <Tooltip content={<PaceChartTooltip />} />}
              <Line
                type="monotone"
                dataKey="meta"
                stroke="#23D8D3"
                strokeDasharray="4 4"
                strokeWidth={1.5}
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="projecao"
                stroke={percentMeta >= 100 ? 'var(--success)' : percentMeta >= 80 ? 'var(--warning)' : 'var(--danger)'}
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="realizado"
                stroke="var(--ink)"
                strokeWidth={1.5}
                dot={{
                  r: 3,
                  fill: 'var(--paper)',
                  stroke: 'var(--ink)',
                  strokeWidth: 1.5,
                }}
                activeDot={{
                  r: 5,
                  fill: 'var(--ink)',
                  stroke: 'var(--paper)',
                  strokeWidth: 2,
                }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className={styles.sectionDivider} />

      <div className={styles.section}>
        <div className={styles.header}>
          <div className={styles.title}>{summary.title}</div>
          <div className={styles.summary}>
            <div className={styles.summaryItem}>
              <span className={styles.summaryLabel}>Atual</span>
              <span className={styles.summaryValue}>{formatCompact(summary.realizado)}</span>
            </div>
            <div className={styles.summaryItem}>
              <span className={styles.summaryLabel}>Projeção</span>
              <span className={`${styles.summaryValue} ${statusClass}`}>
                {formatCompact(summary.projecao)}
              </span>
            </div>
            <div className={styles.summaryItem}>
              <span className={styles.summaryLabel}>Meta</span>
              <span className={styles.summaryValue}>{formatCompact(summary.meta)}</span>
            </div>
          </div>
        </div>

        <div className={styles.projectionSummary}>
          <div className={`${styles.projectionBadge} ${statusClass}`}>
            Projeção: {percentMeta}% da meta
          </div>
          <span className={styles.projectionGap}>
            {summary.projecao >= summary.meta ? '+' : ''}{formatBRL(summary.projecao - summary.meta)}
          </span>
        </div>

        <div className={styles.chartWrapperCumulative}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={cumulative.data} margin={{ top: 8, right: 48, left: 0, bottom: 0 }}>
              <XAxis
                dataKey={cumulative.xAxisKey}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
                tickFormatter={cumulative.xAxisFormatter}
                tickMargin={8}
                interval="preserveStartEnd"
                scale="point"
                padding={{ left: 20, right: 20 }}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
                tickFormatter={formatK}
                tickMargin={8}
                width={48}
              />
              {!isMobile && <Tooltip content={<PaceChartTooltip />} />}
              <Line
                type="monotone"
                dataKey="meta"
                stroke="var(--ink-faint)"
                strokeWidth={1.2}
                strokeDasharray="4 4"
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="projecao"
                stroke={percentMeta >= 100 ? 'var(--success)' : percentMeta >= 80 ? 'var(--warning)' : 'var(--danger)'}
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="realizado"
                stroke="var(--ink)"
                strokeWidth={1.5}
                dot={{
                  r: 3,
                  fill: 'var(--paper)',
                  stroke: 'var(--ink)',
                  strokeWidth: 1.5,
                }}
                activeDot={{
                  r: 5,
                  fill: 'var(--ink)',
                  stroke: 'var(--paper)',
                  strokeWidth: 2,
                }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
