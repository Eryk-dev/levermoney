import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { PeriodGranularity } from '../hooks/useFilters';
import styles from './PeriodNavigator.module.css';

interface PeriodNavigatorProps {
  granularity: PeriodGranularity;
  periodLabel: string;
  canNavigateForward: boolean;
  onGranularityChange: (g: PeriodGranularity) => void;
  onNavigate: (direction: -1 | 1) => void;
}

const granularities: { value: PeriodGranularity; label: string }[] = [
  { value: 'day', label: 'Dia' },
  { value: 'week', label: 'Semana' },
  { value: 'month', label: 'Mês' },
  { value: 'all', label: 'Tudo' },
];

export function PeriodNavigator({
  granularity,
  periodLabel,
  canNavigateForward,
  onGranularityChange,
  onNavigate,
}: PeriodNavigatorProps) {
  return (
    <div className={styles.container}>
      <span className={styles.label}>Período</span>
      <div className={styles.row}>
        <div className={styles.buttons}>
          {granularities.map((g) => (
            <button
              key={g.value}
              type="button"
              className={`${styles.button} ${granularity === g.value ? styles.active : ''}`}
              onClick={() => onGranularityChange(g.value)}
            >
              {g.label}
            </button>
          ))}
        </div>
        {granularity !== 'all' && (
          <div className={styles.navigator}>
            <button
              type="button"
              className={styles.arrow}
              onClick={() => onNavigate(-1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span className={styles.periodLabel}>{periodLabel}</span>
            <button
              type="button"
              className={`${styles.arrow} ${!canNavigateForward ? styles.disabled : ''}`}
              onClick={() => onNavigate(1)}
              disabled={!canNavigateForward}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
