import { useMemo } from 'react';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { formatDate, formatBRL } from '../utils/dataParser';
import type { DailyDataPoint } from '../hooks/useFilters';
import { useIsMobile } from '../hooks/useIsMobile';
import styles from './GroupStackedBars.module.css';

interface GroupStackedBarsProps {
  data: DailyDataPoint[];
  groups: string[];
  title?: string;
  comparisonData?: DailyDataPoint[] | null;
  comparisonLabel?: string | null;
}

// Muted, cohesive palette
const GROUP_COLORS: Record<string, string> = {
  'NETAIR': '#1a1a1a',
  'ACA': '#525252',
  'EASY': '#737373',
  'BELLATOR': '#a3a3a3',
  'UNIQUE': '#d4d4d4',
};

function getGroupColor(grupo: string, index: number): string {
  if (GROUP_COLORS[grupo]) return GROUP_COLORS[grupo];
  const grays = ['#1a1a1a', '#404040', '#666666', '#8c8c8c', '#b3b3b3', '#d9d9d9'];
  return grays[index % grays.length];
}

function formatCompact(value: number): string {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(0)}k`;
  return value.toString();
}

export function GroupStackedBars({
  data,
  groups,
  title = 'Contribuição por Grupo',
}: GroupStackedBarsProps) {
  const isMobile = useIsMobile();
  const formatAxisDate = (value: number | string | Date) => {
    if (value instanceof Date) return formatDate(value);
    const asNumber = typeof value === 'string' ? Number(value) : value;
    return formatDate(new Date(asNumber));
  };

  const chartData = useMemo(() => {
    return data.map((d) => {
      const point: Record<string, string | number | null> = {
        dateKey: d.date.getTime(),
      };

      // Add group values
      groups.forEach((grupo) => {
        point[grupo] = (d[`group_${grupo}`] as number) || 0;
      });

      if (typeof d.goal === 'number') {
        point.goal = d.goal;
      }

      return point;
    });
  }, [data, groups]);

  const maxValue = useMemo(() => {
    let max = 0;
    data.forEach((d) => {
      let total = 0;
      groups.forEach((grupo) => {
        total += (d[`group_${grupo}`] as number) || 0;
      });
      if (total > max) max = total;
    });
    const maxGoal = data.length > 0 ? Math.max(...data.map((d) => d.goal || 0)) : 0;
    return Math.max(max, maxGoal);
  }, [data, groups]);

  if (data.length === 0 || groups.length === 0) {
    return (
      <div className={styles.container}>
        <div className={styles.header}>
          <span className={styles.title}>{title}</span>
        </div>
        <div className={styles.empty}>Nenhum dado para exibir</div>
      </div>
    );
  }

  const hasGoalLine = data.some((d) => (d.goal || 0) > 0);
  const yAxisMax = maxValue > 0 ? maxValue * 1.1 : undefined;

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>{title}</span>
      </div>
      <div className={styles.chartWrapper}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="dateKey"
              type="number"
              domain={['auto', 'auto']}
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 9, fill: 'var(--ink-faint)' }}
              tickMargin={8}
              interval="preserveStartEnd"
              scale="time"
              tickFormatter={formatAxisDate}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 11, fill: 'var(--ink-faint)' }}
              tickFormatter={(v) => formatCompact(v)}
              tickMargin={8}
              width={48}
              domain={[0, yAxisMax || 'auto']}
            />
            {!isMobile && (
              <Tooltip
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length) {
                    const total = payload
                      .filter((p) => p.dataKey !== 'goal')
                      .reduce((sum, p) => sum + (Number(p.value) || 0), 0);
                    const goalEntry = payload.find((p) => p.dataKey === 'goal');
                    const goalValue = goalEntry ? Number(goalEntry.value) : null;
                    const gap = goalValue !== null ? total - goalValue : null;

                    return (
                      <div className={styles.tooltip}>
                        <span className={styles.tooltipDate}>
                          {typeof label === 'string' ? label : formatAxisDate(label as number)}
                        </span>
                        <div className={styles.tooltipItems}>
                          {payload
                            .filter((entry) => entry.dataKey !== 'goal' && Number(entry.value) > 0)
                            .map((entry, index) => {
                              const percent = total > 0
                                ? ((Number(entry.value) / total) * 100).toFixed(0)
                                : 0;
                              return (
                                <div key={index} className={styles.tooltipItem}>
                                  <span
                                    className={styles.tooltipDot}
                                    style={{ background: entry.color }}
                                  />
                                  <span className={styles.tooltipLabel}>{entry.name}</span>
                                  <span className={styles.tooltipPercent}>{percent}%</span>
                                  <span className={styles.tooltipValue}>
                                    {formatBRL(Number(entry.value))}
                                  </span>
                                </div>
                              );
                            })}
                        </div>
                        <div className={styles.tooltipTotal}>
                          <span>Total</span>
                          <span>{formatBRL(total)}</span>
                        </div>
                        {goalValue !== null && (
                          <>
                            <div className={styles.tooltipTotal} style={{ borderTop: 'none', paddingTop: 0, marginTop: 0 }}>
                              <span style={{ color: '#23D8D3' }}>Meta</span>
                              <span style={{ color: '#23D8D3' }}>{formatBRL(goalValue)}</span>
                            </div>
                            <div className={styles.tooltipTotal} style={{ borderTop: 'none', paddingTop: 0, marginTop: 0 }}>
                              <span>Gap</span>
                              <span style={{ color: gap && gap >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                                {gap && gap >= 0 ? '+' : ''}{formatBRL(gap || 0)}
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    );
                  }
                  return null;
                }}
              />
            )}
            <Legend
              verticalAlign="top"
              align="right"
              height={36}
              formatter={(value) => (
                <span style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
                  {value}
                </span>
              )}
            />

            {/* Stacked bars for each group */}
            {groups.map((grupo, index) => (
              <Bar
                key={grupo}
                dataKey={grupo}
                stackId="stack"
                fill={getGroupColor(grupo, index)}
                maxBarSize={24}
              />
            ))}

            {/* Goal line - same as RevenueChart */}
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
                legendType="none"
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
