import { useState } from 'react';
import { GitCompareArrows, ChevronDown, X } from 'lucide-react';
import { DatePicker } from './DatePicker';
import styles from './ComparisonToggle.module.css';

interface ComparisonToggleProps {
  enabled: boolean;
  onToggle: () => void;
  customStart: Date | null;
  customEnd: Date | null;
  onCustomRangeChange: (start: Date | null, end: Date | null) => void;
  onClearCustom: () => void;
  comparisonLabel: string | null;
  minDate?: Date | null;
  maxDate?: Date | null;
}

function formatDateShort(date: Date): string {
  return date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

export function ComparisonToggle({
  enabled,
  onToggle,
  customStart,
  customEnd,
  onCustomRangeChange,
  onClearCustom,
  comparisonLabel,
  minDate,
  maxDate,
}: ComparisonToggleProps) {
  const [showCustom, setShowCustom] = useState(false);

  const hasCustomRange = customStart && customEnd;

  return (
    <div className={styles.container}>
      <button
        type="button"
        className={`${styles.toggle} ${enabled ? styles.active : ''}`}
        onClick={onToggle}
        title={enabled ? 'Desativar comparação' : 'Ativar comparação'}
      >
        <GitCompareArrows size={14} />
        <span className={styles.label}>Comparar</span>
      </button>

      {enabled && (
        <>
          <button
            type="button"
            className={`${styles.customButton} ${showCustom ? styles.expanded : ''}`}
            onClick={() => setShowCustom(!showCustom)}
          >
            <span className={styles.periodLabel}>
              {hasCustomRange
                ? `${formatDateShort(customStart)} - ${formatDateShort(customEnd)}`
                : comparisonLabel || 'Período anterior'}
            </span>
            <ChevronDown size={12} className={styles.chevron} />
          </button>

          {showCustom && (
            <div className={styles.customPanel}>
              <div className={styles.customHeader}>
                <span>Período de comparação</span>
                {hasCustomRange && (
                  <button
                    type="button"
                    className={styles.clearCustom}
                    onClick={() => {
                      onClearCustom();
                      setShowCustom(false);
                    }}
                    title="Usar período automático"
                  >
                    <X size={12} />
                    Auto
                  </button>
                )}
              </div>
              <div className={styles.customDates}>
                <DatePicker
                  label="De"
                  value={customStart}
                  onChange={(v) => onCustomRangeChange(v, customEnd)}
                  min={minDate}
                  max={customEnd || maxDate}
                />
                <DatePicker
                  label="Até"
                  value={customEnd}
                  onChange={(v) => onCustomRangeChange(customStart, v)}
                  min={customStart || minDate}
                  max={maxDate}
                />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
