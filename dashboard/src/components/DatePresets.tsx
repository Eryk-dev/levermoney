import type { DatePreset } from '../hooks/useFilters';
import styles from './DatePresets.module.css';

interface DatePresetsProps {
  value: DatePreset;
  onChange: (preset: DatePreset) => void;
}

const presets: { value: DatePreset; label: string }[] = [
  { value: 'today', label: 'Hoje' },
  { value: 'yesterday', label: 'Ontem' },
  { value: 'wtd', label: 'Semana' },
  { value: 'mtd', label: 'Mês' },
  { value: 'all', label: 'Tudo' },
];

export function DatePresets({ value, onChange }: DatePresetsProps) {
  return (
    <div className={styles.container}>
      <span className={styles.label}>Período</span>
      <div className={styles.buttons}>
        {presets.map((preset) => (
          <button
            key={preset.value}
            type="button"
            className={`${styles.button} ${value === preset.value ? styles.active : ''}`}
            onClick={() => onChange(preset.value)}
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  );
}
