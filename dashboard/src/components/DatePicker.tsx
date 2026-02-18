import { Calendar } from 'lucide-react';
import { formatDateInput } from '../utils/dataParser';
import styles from './DatePicker.module.css';

interface DatePickerProps {
  label: string;
  value: Date | null;
  onChange: (date: Date | null) => void;
  min?: Date | null;
  max?: Date | null;
}

export function DatePicker({ label, value, onChange, min, max }: DatePickerProps) {
  return (
    <div className={styles.container}>
      <span className={styles.label}>{label}</span>
      <div className={styles.inputWrapper}>
        <input
          type="date"
          className={styles.input}
          value={value ? formatDateInput(value) : ''}
          min={min ? formatDateInput(min) : undefined}
          max={max ? formatDateInput(max) : undefined}
          onChange={(e) => {
            const val = e.target.value;
            onChange(val ? new Date(val + 'T00:00:00') : null);
          }}
        />
        <Calendar size={14} className={styles.icon} />
      </div>
    </div>
  );
}
