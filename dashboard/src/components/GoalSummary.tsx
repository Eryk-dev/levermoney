import { formatBRL, formatPercent } from '../utils/dataParser';
import type { DatePreset } from '../hooks/useFilters';
import styles from './GoalSummary.module.css';

interface GoalSummaryProps {
  realizado: number;
  realizadoMes: number;
  meta: number;
  metaProporcional: number;
  diaAtual: number;
  datePreset: DatePreset;
  // Week metrics
  metaSemana: number;
  realizadoSemana: number;
  diasNaSemana: number;
  esperadoSemanal: number;
  // Day metrics
  metaDia: number;
  metaDiaAjustada: number;
  realizadoDia: number;
  // Year metrics
  metaAno: number;
  realizadoAno: number;
  mesAtual: number;
  // AR CONDICIONADO specific
  isArCondicionado: boolean;
}

interface ProgressBarProps {
  label: string;
  realizado: number;
  meta: number;
  expected?: number;
  expectedLabel?: string;
}

function ProgressBar({ label, realizado, meta, expected, expectedLabel }: ProgressBarProps) {
  if (meta === 0) return null;

  const percentual = (realizado / meta) * 100;
  const expectedPercent = expected !== undefined ? (expected / meta) * 100 : null;
  const gap = expected !== undefined ? realizado - expected : realizado - meta;
  const isAhead = gap >= 0;

  return (
    <div className={styles.progressBlock}>
      <div className={styles.progressHeader}>
        <span className={styles.label}>{label}</span>
        <span className={styles.percentage}>{formatPercent(percentual)}</span>
      </div>
      <div className={styles.progressBar}>
        <div
          className={styles.progressFill}
          style={{ width: `${Math.min(percentual, 100)}%` }}
        />
        {expectedPercent !== null && (
          <div
            className={styles.expectedMarker}
            style={{ left: `${Math.min(expectedPercent, 100)}%` }}
          />
        )}
      </div>
      <div className={styles.progressFooter}>
        <span>{formatBRL(realizado)}</span>
        <span className={styles.metaValue}>de {formatBRL(meta)}</span>
      </div>
      {expectedLabel && (
        <div className={styles.gapRow}>
          <span className={styles.gapLabel}>{expectedLabel}</span>
          <span className={`${styles.gapValue} ${isAhead ? styles.positive : styles.negative}`}>
            {isAhead ? '+' : ''}{formatBRL(gap)}
          </span>
        </div>
      )}
    </div>
  );
}

export function GoalSummary({
  realizado,
  realizadoMes,
  meta,
  metaProporcional,
  diaAtual,
  datePreset,
  metaSemana,
  realizadoSemana,
  diasNaSemana,
  esperadoSemanal,
  metaDiaAjustada,
  realizadoDia,
  metaAno,
  realizadoAno,
  mesAtual,
}: GoalSummaryProps) {
  if (meta === 0) return null;

  // Determine which levels to show based on datePreset
  // Hoje/Ontem: Diária → Semana
  // Semana: Semana → Mês
  // Mês/Tudo: Mês → Ano
  const isDaily = datePreset === 'today' || datePreset === 'yesterday';
  const isWeek = datePreset === 'wtd';
  const isMonth = datePreset === 'mtd';
  const isMonthOrAll = datePreset === 'mtd' || datePreset === 'all';

  // Expected value for year (proportional to current month)
  const metaAnoProporcional = (metaAno / 12) * mesAtual;

  return (
    <div className={styles.container}>
      {/* Hoje/Ontem: Diária → Semanal */}
      {isDaily && (
        <>
          <ProgressBar
            label="Meta Diária"
            realizado={realizadoDia}
            meta={metaDiaAjustada}
          />
          <div className={styles.divider} />
          <ProgressBar
            label="Meta Semanal"
            realizado={realizadoSemana}
            meta={metaSemana}
            expected={esperadoSemanal}
            expectedLabel={`vs esperado (${diasNaSemana} dias)`}
          />
        </>
      )}

      {/* Semana: Semanal → Mensal */}
      {isWeek && (
        <>
          <ProgressBar
            label="Meta Semanal"
            realizado={realizadoSemana}
            meta={metaSemana}
            expected={esperadoSemanal}
            expectedLabel={`vs esperado (${diasNaSemana} dias)`}
          />
          <div className={styles.divider} />
          <ProgressBar
            label="Meta Mensal"
            realizado={realizadoMes}
            meta={meta}
            expected={metaProporcional}
            expectedLabel={`vs esperado (dia ${diaAtual})`}
          />
        </>
      )}

      {/* Mensal/Tudo: Mensal/Período → Anual */}
      {isMonthOrAll && (
        <>
          {isMonth && (
            <>
              <ProgressBar
                label="Meta Diária"
                realizado={realizadoDia}
                meta={metaDiaAjustada}
              />
              <div className={styles.divider} />
            </>
          )}
          <ProgressBar
            label={datePreset === 'all' ? "Meta do Período" : "Meta Mensal"}
            realizado={datePreset === 'all' ? realizado : realizadoMes}
            meta={meta}
            expected={datePreset === 'all' ? undefined : metaProporcional}
            expectedLabel={datePreset === 'all' ? undefined : `vs esperado (dia ${diaAtual})`}
          />
          <div className={styles.divider} />
          <ProgressBar
            label="Meta Anual"
            realizado={realizadoAno}
            meta={metaAno}
            expected={metaAnoProporcional}
            expectedLabel={`vs esperado (mês ${mesAtual})`}
          />
        </>
      )}
    </div>
  );
}
