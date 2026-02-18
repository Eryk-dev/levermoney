import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import type { DatePreset } from '../hooks/useFilters';
import type { CoverageMetrics } from '../types';
import styles from './PeriodCards.module.css';

interface PeriodData {
  realizado: number;
  meta: number;
  metaProporcional: number;
  coverage?: CoverageMetrics;
}

interface PeriodCardsProps {
  hoje: PeriodData;
  semana: PeriodData;
  mes: PeriodData;
  ano?: PeriodData;
  activePreset?: DatePreset;
}

function formatCompact(value: number): string {
  if (Math.abs(value) >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString('pt-BR', { maximumFractionDigits: 0 });
}

function getGapStatus(gap: number, meta: number): 'positive' | 'negative' | 'neutral' {
  const tolerance = meta * 0.05;
  if (gap > tolerance) return 'positive';
  if (gap < -tolerance) return 'negative';
  return 'neutral';
}

interface CardProps {
  label: string;
  realizado: number;
  meta: number;
  metaProporcional: number;
  coverage?: CoverageMetrics;
  showPercent?: boolean;
  isActive?: boolean;
}

function Card({ label, realizado, meta, metaProporcional, coverage, showPercent, isActive }: CardProps) {
  const gap = realizado - metaProporcional;
  const status = getGapStatus(gap, metaProporcional);
  const percent = meta > 0 ? Math.round((realizado / meta) * 100) : 0;

  const Icon = status === 'positive' ? TrendingUp : status === 'negative' ? TrendingDown : Minus;

  return (
    <div className={`${styles.card} ${styles[status]} ${isActive ? styles.active : ''}`}>
      <div className={styles.cardHeader}>
        <span className={styles.cardLabel}>{label}</span>
        <Icon size={14} className={styles.cardIcon} />
      </div>
      <div className={styles.cardValues}>
        <span className={styles.cardRealizado}>{formatCompact(realizado)}</span>
        <span className={styles.cardMeta}>/ {formatCompact(meta)}</span>
      </div>
      <div className={styles.cardProgress}>
        <div
          className={styles.cardProgressFill}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
        <div
          className={styles.cardProgressExpected}
          style={{ left: `${Math.min((metaProporcional / meta) * 100, 100)}%` }}
        />
      </div>
      <div className={styles.cardFooter}>
        <span className={`${styles.cardGap} ${styles[status]}`}>
          {gap >= 0 ? '+' : ''}{formatCompact(gap)}
        </span>
        {showPercent && (
          <span className={styles.cardPercent}>{percent}%</span>
        )}
      </div>
      {coverage && coverage.expected > 0 && (
        <div className={styles.cardCoverage}>
          <span className={styles.cardCoverageLabel}>Cobertura</span>
          <span className={styles.cardCoverageValue}>
            {coverage.observed}/{coverage.expected} ({Math.round(coverage.percent * 100)}%)
          </span>
        </div>
      )}
    </div>
  );
}

export function PeriodCards({ hoje, semana, mes, ano, activePreset }: PeriodCardsProps) {
  const dailyLabel = activePreset === 'yesterday' ? 'Ontem' : 'Hoje';

  return (
    <div className={styles.container}>
      <Card label={dailyLabel} {...hoje} isActive={activePreset === 'today' || activePreset === 'yesterday'} />
      <Card label="Semana" {...semana} isActive={activePreset === 'wtd'} />
      <Card label="Mês" {...mes} showPercent isActive={activePreset === 'mtd' || activePreset === 'all'} />
      {ano && <Card label="Ano" {...ano} showPercent />}
    </div>
  );
}
