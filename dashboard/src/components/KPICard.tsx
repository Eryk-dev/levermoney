import styles from './KPICard.module.css';

interface KPICardProps {
  label: string;
  value: string;
  sublabel?: string;
  size?: 'large' | 'medium';
}

export function KPICard({ label, value, sublabel, size = 'medium' }: KPICardProps) {
  return (
    <div className={`${styles.card} ${styles[size]}`}>
      <span className={styles.label}>{label}</span>
      <span className={`${styles.value} tabular`}>{value}</span>
      <span className={`${styles.sublabel} ${!sublabel ? styles.hidden : ''}`}>
        {sublabel || '\u00A0'}
      </span>
    </div>
  );
}
