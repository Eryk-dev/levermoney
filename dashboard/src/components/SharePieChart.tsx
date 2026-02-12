import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';
import { formatBRL, formatPercent } from '../utils/dataParser';
import { useIsMobile } from '../hooks/useIsMobile';
import styles from './SharePieChart.module.css';

interface DataItem {
  name: string;
  value: number;
}

interface SharePieChartProps {
  title: string;
  data: DataItem[];
  showLegend?: boolean;
  size?: number;
}

const COLORS = ['var(--ink)', 'var(--line)'];
const SEGMENT_COLORS = [
  '#1a1a1a',
  '#404040',
  '#666666',
  '#8c8c8c',
  '#b3b3b3',
];

export function SharePieChart({ title, data, showLegend = false, size }: SharePieChartProps) {
  const isMobile = useIsMobile();
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const chartHeight = size || 160;
  const outerRadius = size ? size * 0.4 : 70;
  const innerRadius = size ? size * 0.25 : 45;

  const isCompact = !!size;

  if (total === 0) {
    return (
      <div className={isCompact ? styles.containerCompact : styles.container}>
        <span className={styles.title}>{title}</span>
        <div className={styles.empty}>Nenhum dado</div>
      </div>
    );
  }

  const colors = showLegend ? SEGMENT_COLORS : COLORS;

  return (
    <div className={isCompact ? styles.containerCompact : styles.container}>
      <span className={styles.title}>{title}</span>
      <div className={styles.chartWrapper}>
        <div className={styles.chart}>
          <ResponsiveContainer width="100%" height={chartHeight}>
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={innerRadius}
                outerRadius={outerRadius}
                paddingAngle={2}
                dataKey="value"
                strokeWidth={0}
              >
                {data.map((_, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={colors[index % colors.length]}
                  />
                ))}
              </Pie>
              {!isMobile && (
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const item = payload[0].payload as DataItem;
                      const percent = (item.value / total) * 100;
                      return (
                        <div className={styles.tooltip}>
                          <span className={styles.tooltipName}>{item.name}</span>
                          <span className={styles.tooltipValue}>
                            {formatBRL(item.value)} ({formatPercent(percent)})
                          </span>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
              )}
            </PieChart>
          </ResponsiveContainer>
        </div>
        {showLegend && (
          <div className={styles.legend}>
            {data.map((item, index) => {
              const percent = (item.value / total) * 100;
              return (
                <div key={item.name} className={styles.legendItem}>
                  <span
                    className={styles.legendDot}
                    style={{ background: colors[index % colors.length] }}
                  />
                  <span className={styles.legendLabel}>{item.name}</span>
                  <span className={`${styles.legendValue} tabular`}>
                    {formatPercent(percent)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
        {!showLegend && data.length === 2 && (
          <div className={styles.centerLabel}>
            <span className={`${size ? styles.centerPercentSmall : styles.centerPercent} tabular`}>
              {formatPercent((data[0].value / total) * 100)}
            </span>
            <span className={size ? styles.centerTextSmall : styles.centerText}>do total</span>
          </div>
        )}
      </div>
    </div>
  );
}
