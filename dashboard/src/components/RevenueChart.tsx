import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
  LabelList,
} from 'recharts';
import { formatDate } from '../utils/dataParser';
import type { DailyDataPoint } from '../hooks/useFilters';
import { useIsMobile } from '../hooks/useIsMobile';
import styles from './RevenueChart.module.css';

interface RevenueChartProps {
  data: DailyDataPoint[];
  companies?: string[];
  title?: string;
  comparisonData?: DailyDataPoint[] | null;
  comparisonLabel?: string | null;
}

// Colors for company lines
const COMPANY_COLORS = [
  '#6366f1', // indigo
  '#f59e0b', // amber
  '#10b981', // emerald
  '#ef4444', // red
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#f97316', // orange
  '#84cc16', // lime
  '#ec4899', // pink
  '#14b8a6', // teal
];

function formatCompact(value: number): string {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(0)}k`;
  return value.toString();
}

function formatBRL(value: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export function RevenueChart({ data, companies = [], title = 'Faturamento Diário', comparisonData, comparisonLabel }: RevenueChartProps) {
  const isMobile = useIsMobile();
  const latestDailyGoalPoint = [...data].reverse().find(
    (point) => typeof point.total === 'number' && typeof point.goal === 'number'
  );

  const dailyGoalIndicators = latestDailyGoalPoint
    ? (() => {
        const realizado = latestDailyGoalPoint.total as number;
        const meta = latestDailyGoalPoint.goal as number;
        const gap = realizado - meta;
        const falta = Math.max(meta - realizado, 0);
        const percentual = meta > 0 ? (realizado / meta) * 100 : 0;
        return {
          dateLabel: formatDate(latestDailyGoalPoint.date),
          realizado,
          meta,
          gap,
          falta,
          percentual,
        };
      })()
    : null;

  const chartData = data.map((d, index) => {
    const point: Record<string, string | number | null> = {
      x: index,
      dateLabel: formatDate(d.date),
      total: d.total,
      // Align comparison by index (day 0 current = day 0 comparison)
      comparisonTotal: comparisonData && comparisonData[index] ? comparisonData[index].total : null,
      goal: typeof d.goal === 'number' ? d.goal : null,
    };

    // Add company values
    companies.forEach((company) => {
      point[company] = typeof d[company] === 'number' ? (d[company] as number) : null;
    });

    return point;
  });

  const hasComparison = comparisonData && comparisonData.length > 0;

  const showMultipleLines = companies.length > 1;

  // Calculate Y-axis domain
  const realizedValues = data
    .map((d) => d.total)
    .filter((value): value is number => typeof value === 'number');
  const maxDataValue = realizedValues.length > 0 ? Math.max(...realizedValues) : 0;

  // Y-axis always starts at zero
  const yAxisMin = 0;
  // Y-axis max = max between data and max goal, with 10% margin
  const maxGoalValue = data.length > 0 ? Math.max(...data.map((d) => d.goal || 0)) : 0;
  const maxWithGoal = Math.max(maxDataValue, maxGoalValue);
  const yAxisMax = maxWithGoal > 0 ? maxWithGoal * 1.1 : undefined;

  const hasGoalLine = data.some((d) => (d.goal || 0) > 0);
  const showTooltip = !isMobile;

  const chartHeight = isMobile ? 220 : 280;
  const maxIndex = Math.max(chartData.length - 1, 0);
  const getLabelFromIndex = (value: number | string): string => {
    const idx = typeof value === 'string' ? Number(value) : value;
    const rawLabel = chartData[idx]?.dateLabel;
    if (typeof rawLabel === 'string') return rawLabel;
    if (rawLabel != null) return String(rawLabel);
    return '';
  };
  const formatAxisLabel = (value: number | string) => getLabelFromIndex(value);
  const formatTooltipLabel = (label: unknown): string => {
    if (typeof label === 'string' || typeof label === 'number') {
      return getLabelFromIndex(label);
    }
    return '';
  };

  if (data.length === 0) {
    return (
      <div className={styles.container}>
        <div className={styles.header}>
          <span className={styles.title}>{title}</span>
        </div>
        <div className={styles.empty}>Nenhum dado para exibir</div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>{title}</span>
        {dailyGoalIndicators && (
          <div className={styles.indicators}>
            <div className={styles.indicatorItem}>
              <span className={styles.indicatorLabel}>
                Meta dia {dailyGoalIndicators.dateLabel}
              </span>
              <span className={styles.indicatorValue}>
                {formatBRL(dailyGoalIndicators.meta)}
              </span>
            </div>
            <div className={styles.indicatorItem}>
              <span className={styles.indicatorLabel}>Realizado</span>
              <span className={styles.indicatorValue}>
                {formatBRL(dailyGoalIndicators.realizado)}
              </span>
            </div>
            <div className={styles.indicatorItem}>
              <span className={styles.indicatorLabel}>Quanto falta</span>
              <span className={`${styles.indicatorValue} ${dailyGoalIndicators.falta > 0 ? styles.indicatorNegative : styles.indicatorPositive}`}>
                {dailyGoalIndicators.falta > 0
                  ? formatBRL(dailyGoalIndicators.falta)
                  : `Meta batida (+${formatBRL(dailyGoalIndicators.gap)})`}
              </span>
            </div>
            <div className={styles.indicatorItem}>
              <span className={styles.indicatorLabel}>Atingimento</span>
              <span className={`${styles.indicatorValue} ${dailyGoalIndicators.percentual >= 100 ? styles.indicatorPositive : styles.indicatorNegative}`}>
                {Math.round(dailyGoalIndicators.percentual)}%
              </span>
            </div>
          </div>
        )}
      </div>
      <div className={styles.chart}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <LineChart data={chartData} margin={{ top: 8, right: 48, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="x"
              type="number"
              domain={[0, maxIndex]}
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: isMobile ? 10 : 11, fill: 'var(--ink-faint)' }}
              tickMargin={8}
              interval="preserveStartEnd"
              tickFormatter={formatAxisLabel}
              padding={{ left: 20, right: 20 }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
              tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
              tickMargin={8}
              width={48}
              domain={[yAxisMin, yAxisMax || 'auto']}
            />
            {showTooltip && (
              <Tooltip
                labelFormatter={formatTooltipLabel}
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length) {
                    const currentTotalRaw = payload.find((p) => p.dataKey === 'total')?.value;
                    const currentTotal = typeof currentTotalRaw === 'number' ? currentTotalRaw : null;
                    const compTotalRaw = payload.find((p) => p.dataKey === 'comparisonTotal')?.value;
                    const compTotal = typeof compTotalRaw === 'number' ? compTotalRaw : null;
                    const goalEntry = payload.find((p) => p.dataKey === 'goal');
                    const goalValue = goalEntry && typeof goalEntry.value === 'number'
                      ? Number(goalEntry.value)
                      : null;
                    const delta = currentTotal !== null && compTotal !== null && compTotal !== 0
                      ? ((currentTotal - compTotal) / compTotal) * 100
                      : null;
                    const gap = currentTotal !== null && goalValue !== null ? currentTotal - goalValue : null;

                    const displayLabel = formatTooltipLabel(label);
                    return (
                      <div className={styles.tooltip}>
                        <span className={styles.tooltipDate}>{displayLabel}</span>
                        <div className={styles.tooltipItems}>
                          {payload
                            .filter((entry) =>
                              entry.dataKey !== 'comparisonTotal' &&
                              entry.dataKey !== 'goal' &&
                              typeof entry.value === 'number'
                            )
                            .map((entry, index) => (
                            <div key={index} className={styles.tooltipItem}>
                              <span
                                className={styles.tooltipDot}
                                style={{ background: entry.color }}
                              />
                              <span className={styles.tooltipLabel}>
                                {entry.name === 'total' ? 'Total' : entry.name}
                              </span>
                                  <span className={styles.tooltipValue}>
                                {formatBRL(Number(entry.value))}
                              </span>
                            </div>
                          ))}
                          {goalValue !== null && (
                            <div className={styles.tooltipItem}>
                              <span
                                className={styles.tooltipDot}
                                style={{ background: '#23D8D3' }}
                              />
                              <span className={styles.tooltipLabel}>Meta</span>
                              <span className={styles.tooltipValue}>
                                {formatBRL(goalValue)}
                              </span>
                            </div>
                          )}
                          {gap !== null && (
                            <div className={styles.tooltipItem}>
                              <span
                                className={styles.tooltipDot}
                                style={{ background: gap >= 0 ? 'var(--success)' : 'var(--danger)' }}
                              />
                              <span className={styles.tooltipLabel}>Gap</span>
                              <span className={styles.tooltipValue} style={{ color: gap >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                                {gap >= 0 ? '+' : ''}{formatBRL(gap)}
                              </span>
                            </div>
                          )}
                          {compTotal != null && (
                            <div className={styles.tooltipItem}>
                              <span
                                className={styles.tooltipDot}
                                style={{ background: 'var(--ink-faint)' }}
                              />
                              <span className={styles.tooltipLabel}>
                                {comparisonLabel || 'Anterior'}
                              </span>
                              <span className={styles.tooltipValue}>
                                {formatBRL(compTotal)}
                              </span>
                            </div>
                          )}
                          {delta !== null && (
                            <div className={styles.tooltipDelta} style={{ color: delta >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                              {delta >= 0 ? '+' : ''}{delta.toFixed(1)}%
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  }
                  return null;
                }}
              />
            )}
            {showMultipleLines && (
              <Legend
                verticalAlign="top"
                height={36}
                formatter={(value) => (
                  <span style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
                    {value === 'total' ? 'Total' : value}
                  </span>
                )}
              />
            )}

            {/* Use linear type for single/few points to avoid curve artifacts */}
            {hasGoalLine && (
              <Line
                type={data.length <= 2 ? 'linear' : 'monotone'}
                dataKey="goal"
                name="Meta"
                stroke="#23D8D3"
                strokeDasharray="4 4"
                strokeWidth={1.5}
                dot={false}
                activeDot={false}
                connectNulls
              />
            )}

            {/* Company lines */}
            {showMultipleLines &&
              companies.map((company, index) => (
                <Line
                  key={company}
                  type="monotone"
                  dataKey={company}
                  name={company}
                  stroke={COMPANY_COLORS[index % COMPANY_COLORS.length]}
                  strokeWidth={1.5}
                  dot={{
                    r: 3,
                    fill: 'var(--paper)',
                    stroke: COMPANY_COLORS[index % COMPANY_COLORS.length],
                    strokeWidth: 1.5,
                  }}
                  activeDot={{
                    r: 5,
                    fill: COMPANY_COLORS[index % COMPANY_COLORS.length],
                    stroke: 'var(--paper)',
                    strokeWidth: 2,
                  }}
                />
              ))}

            {/* Comparison line */}
            {hasComparison && (
              <Line
                type="monotone"
                dataKey="comparisonTotal"
                name={comparisonLabel || 'Anterior'}
                stroke="var(--ink-faint)"
                strokeWidth={1}
                strokeDasharray="4 4"
                dot={false}
                activeDot={{
                  r: 4,
                  fill: 'var(--ink-faint)',
                  stroke: 'var(--paper)',
                  strokeWidth: 2,
                }}
                connectNulls
              />
            )}

            {/* Total line */}
            <Line
              type="monotone"
              dataKey="total"
              name="total"
              stroke="var(--ink)"
              strokeWidth={showMultipleLines ? 2 : 1.5}
              strokeDasharray={showMultipleLines ? '4 4' : undefined}
              dot={
                showMultipleLines
                  ? false
                  : {
                      r: 3,
                      fill: 'var(--paper)',
                      stroke: 'var(--ink)',
                      strokeWidth: 1.5,
                    }
              }
              activeDot={{
                r: 5,
                fill: 'var(--ink)',
                stroke: 'var(--paper)',
                strokeWidth: 2,
              }}
            >
              {!showMultipleLines && !isMobile && data.length <= 31 && (
                <LabelList
                  dataKey="total"
                  position="top"
                  offset={8}
                  formatter={(value) => typeof value === 'number' ? formatCompact(value) : ''}
                  style={{
                    fontSize: 9,
                    fill: '#9a9a9a',
                    fontWeight: 500,
                  }}
                />
              )}
            </Line>
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
