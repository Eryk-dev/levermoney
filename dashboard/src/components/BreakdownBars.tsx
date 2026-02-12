import { useState } from 'react';
import { formatBRL } from '../utils/dataParser';
import { ChevronDown, ChevronUp } from 'lucide-react';
import styles from './BreakdownBars.module.css';

interface BreakdownItem {
  label: string;
  value: number;
}

interface BreakdownBarsProps {
  title: string;
  data: BreakdownItem[];
  limit?: number;
}

export function BreakdownBars({ title, data, limit = 8 }: BreakdownBarsProps) {
  const [expanded, setExpanded] = useState(false);
  const maxValue = Math.max(...data.map((d) => d.value), 1);

  if (data.length === 0) {
    return (
      <div className={styles.container}>
        <span className={styles.title}>{title}</span>
        <div className={styles.empty}>Nenhum dado</div>
      </div>
    );
  }

  const hasMore = data.length > limit;
  const displayData = expanded ? data : data.slice(0, limit);

  return (
    <div className={styles.container}>
      <span className={styles.title}>{title}</span>
      <div className={styles.bars}>
        {displayData.map((item) => (
          <div key={item.label} className={styles.row}>
            <span className={styles.label} title={item.label}>{item.label}</span>
            <div className={styles.barContainer}>
              <div
                className={styles.bar}
                style={{ width: `${(item.value / maxValue) * 100}%` }}
              />
            </div>
            <span className={`${styles.value} tabular`}>
              {formatBRL(item.value)}
            </span>
          </div>
        ))}
      </div>
      {hasMore && (
        <button
          type="button"
          className={styles.toggleButton}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <>
              <ChevronUp size={14} />
              Mostrar menos
            </>
          ) : (
            <>
              <ChevronDown size={14} />
              Ver todos ({data.length})
            </>
          )}
        </button>
      )}
    </div>
  );
}
