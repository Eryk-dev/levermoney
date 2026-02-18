import type { GoalMetrics } from '../types';
import { formatBRL, formatPercent } from '../utils/dataParser';
import styles from './GoalProgress.module.css';

interface GoalProgressProps {
  metrics: GoalMetrics;
}

export function GoalProgress({ metrics }: GoalProgressProps) {
  const {
    metaMensal,
    metaProporcional,
    realizado,
    gapProporcional,
    percentualMeta,
    diasNoMes,
    diaAtual,
  } = metrics;

  const progressPercent = Math.min(percentualMeta, 100);
  const expectedPercent = metaMensal > 0 ? (metaProporcional / metaMensal) * 100 : 0;
  const isAhead = gapProporcional >= 0;

  if (metaMensal === 0) {
    return null;
  }

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>Progresso da Meta</span>
        <span className={styles.period}>
          Dia {diaAtual} de {diasNoMes}
        </span>
      </div>

      <div className={styles.progressContainer}>
        <div className={styles.progressBar}>
          <div
            className={styles.progressFill}
            style={{ width: `${progressPercent}%` }}
          />
          <div
            className={styles.expectedMarker}
            style={{ left: `${expectedPercent}%` }}
          />
        </div>
        <div className={styles.progressLabels}>
          <span>{formatPercent(percentualMeta)}</span>
          <span className={styles.expectedLabel}>
            Esperado: {formatPercent(expectedPercent)}
          </span>
        </div>
      </div>

      <div className={styles.metrics}>
        <div className={styles.metric}>
          <span className={styles.metricLabel}>Realizado</span>
          <span className={styles.metricValue}>{formatBRL(realizado)}</span>
        </div>
        <div className={styles.metric}>
          <span className={styles.metricLabel}>Meta Proporcional</span>
          <span className={styles.metricValue}>{formatBRL(metaProporcional)}</span>
        </div>
        <div className={styles.metric}>
          <span className={styles.metricLabel}>Meta Total</span>
          <span className={styles.metricValue}>{formatBRL(metaMensal)}</span>
        </div>
        <div className={styles.metric}>
          <span className={styles.metricLabel}>Gap</span>
          <span className={`${styles.metricValue} ${isAhead ? styles.positive : styles.negative}`}>
            {isAhead ? '+' : ''}{formatBRL(gapProporcional)}
          </span>
        </div>
      </div>
    </div>
  );
}
