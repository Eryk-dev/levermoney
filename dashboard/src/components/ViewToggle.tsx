import { Lock } from 'lucide-react';
import styles from './ViewToggle.module.css';

export type ViewType = 'geral' | 'metas' | 'entrada' | 'linhas' | 'admin';

interface ViewToggleProps {
  value: ViewType;
  onChange: (view: ViewType) => void;
  showAdmin?: boolean;
}

export function ViewToggle({ value, onChange, showAdmin }: ViewToggleProps) {
  return (
    <div className={styles.container}>
      <button
        className={`${styles.button} ${value === 'geral' ? styles.active : ''}`}
        onClick={() => onChange('geral')}
      >
        Visão Geral
      </button>
      <button
        className={`${styles.button} ${value === 'metas' ? styles.active : ''}`}
        onClick={() => onChange('metas')}
      >
        Metas
      </button>
      <button
        className={`${styles.button} ${value === 'entrada' ? styles.active : ''}`}
        onClick={() => onChange('entrada')}
      >
        Entrada
      </button>
      <button
        className={`${styles.button} ${value === 'linhas' ? styles.active : ''}`}
        onClick={() => onChange('linhas')}
      >
        Linhas
      </button>
      {showAdmin && (
        <button
          className={`${styles.button} ${styles.adminButton} ${value === 'admin' ? styles.active : ''}`}
          onClick={() => onChange('admin')}
        >
          <Lock size={12} /> Admin
        </button>
      )}
    </div>
  );
}
