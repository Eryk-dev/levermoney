import { ChevronLeft, ChevronRight } from 'lucide-react';
import styles from './MonthSelector.module.css';

const MONTH_NAMES = [
  'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
];

interface MonthSelectorProps {
  month: number; // 1-12
  year: number;
  onChange: (month: number, year: number) => void;
}

export function MonthSelector({ month, year, onChange }: MonthSelectorProps) {
  const now = new Date();
  const isCurrentMonth = month === now.getMonth() + 1 && year === now.getFullYear();

  const goPrev = () => {
    if (month === 1) {
      onChange(12, year - 1);
    } else {
      onChange(month - 1, year);
    }
  };

  const goNext = () => {
    if (isCurrentMonth) return;
    if (month === 12) {
      onChange(1, year + 1);
    } else {
      onChange(month + 1, year);
    }
  };

  return (
    <div className={styles.container}>
      <span className={styles.label}>Mês</span>
      <div className={styles.selector}>
        <button type="button" className={styles.arrow} onClick={goPrev}>
          <ChevronLeft size={16} />
        </button>
        <span className={styles.monthLabel}>
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button
          type="button"
          className={`${styles.arrow} ${isCurrentMonth ? styles.disabled : ''}`}
          onClick={goNext}
          disabled={isCurrentMonth}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
