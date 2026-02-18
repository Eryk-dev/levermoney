import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { formatBRL } from '../utils/dataParser';
import type { CompanyDailyPerformanceItem } from '../hooks/useFilters';
import styles from './TodayCompanyPerformance.module.css';

interface TodayCompanyPerformanceProps {
  data: CompanyDailyPerformanceItem[];
  title?: string;
  limit?: number;
  countLabel?: string;
  emptyMessage?: string;
}

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return '0%';
  return `${Math.round(value)}%`;
}

export function TodayCompanyPerformance({
  data,
  title = 'Desempenho Diário por Linha',
  limit = 12,
  countLabel = 'linhas',
  emptyMessage = 'Sem dados para hoje.',
}: TodayCompanyPerformanceProps) {
  const [expanded, setExpanded] = useState(false);

  if (data.length === 0) {
    return (
      <div className={styles.container}>
        <div className={styles.header}>
          <span className={styles.title}>{title}</span>
        </div>
        <div className={styles.empty}>{emptyMessage}</div>
      </div>
    );
  }

  const hasMore = data.length > limit;
  const displayData = expanded ? data : data.slice(0, limit);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>{title}</span>
        <span className={styles.count}>{data.length} {countLabel}</span>
      </div>

      <div className={styles.list}>
        {displayData.map((item) => {
          const progress = item.meta > 0 ? Math.min((item.realizado / item.meta) * 100, 100) : 0;
          const gapClass = item.gap >= 0 ? styles.positive : styles.negative;

          return (
            <div key={item.empresa} className={styles.row}>
              <div className={styles.rowTop}>
                <div className={styles.identity}>
                  <span className={styles.empresa}>{item.empresa}</span>
                  <span className={styles.metaInfo}>
                    {formatBRL(item.realizado)} / {formatBRL(item.meta)}
                  </span>
                </div>
                <div className={styles.metrics}>
                  <span className={`${styles.gap} ${gapClass}`}>
                    {item.gap >= 0 ? '+' : ''}{formatBRL(item.gap)}
                  </span>
                  <span className={styles.percent}>{formatPercent(item.percentualMeta)}</span>
                </div>
              </div>
              <div className={styles.progressTrack}>
                <div
                  className={styles.progressFill}
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {hasMore && (
        <button
          type="button"
          className={styles.toggleButton}
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded ? (
            <>
              <ChevronUp size={14} />
              Mostrar menos
            </>
          ) : (
            <>
              <ChevronDown size={14} />
              Ver todas ({data.length})
            </>
          )}
        </button>
      )}
    </div>
  );
}
