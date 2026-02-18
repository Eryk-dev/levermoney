import { formatBRL } from '../utils/dataParser';
import styles from './GroupRanking.module.css';

interface GroupData {
  grupo: string;
  realizado: number;
  meta: number;
  metaProporcional: number;
}

interface GroupRankingProps {
  groups: GroupData[];
}

function getStatus(realizado: number, metaProporcional: number): 'ahead' | 'on-track' | 'behind' {
  const gap = realizado - metaProporcional;
  const tolerance = metaProporcional * 0.05;
  if (gap > tolerance) return 'ahead';
  if (gap < -tolerance) return 'behind';
  return 'on-track';
}

export function GroupRanking({ groups }: GroupRankingProps) {
  // Sort by percentage of goal achieved
  const sorted = [...groups]
    .filter(g => g.meta > 0)
    .sort((a, b) => (b.realizado / b.meta) - (a.realizado / a.meta));

  const maxPercent = Math.max(...sorted.map(g => (g.realizado / g.meta) * 100), 100);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>Ranking por Grupo</span>
        <span className={styles.subtitle}>{sorted.length} grupos</span>
      </div>

      <div className={styles.list}>
        {sorted.map((group, index) => {
          const percent = (group.realizado / group.meta) * 100;
          const barWidth = (percent / maxPercent) * 100;
          const status = getStatus(group.realizado, group.metaProporcional);
          const gap = group.realizado - group.metaProporcional;

          return (
            <div key={group.grupo} className={styles.row}>
              <div className={styles.rank}>
                <span className={styles.rankNumber}>{index + 1}</span>
              </div>
              <div className={styles.info}>
                <div className={styles.nameRow}>
                  <span className={styles.name}>{group.grupo}</span>
                  <span className={`${styles.gap} ${styles[status]}`}>
                    {gap >= 0 ? '+' : ''}{formatBRL(gap)}
                  </span>
                </div>
                <div className={styles.barContainer}>
                  <div
                    className={`${styles.bar} ${styles[status]}`}
                    style={{ width: `${barWidth}%` }}
                  />
                  <div
                    className={styles.expectedMarker}
                    style={{ left: `${Math.min((group.metaProporcional / group.meta) * 100 / maxPercent * 100, 100)}%` }}
                  />
                </div>
                <div className={styles.values}>
                  <span className={styles.realizado}>{formatBRL(group.realizado)}</span>
                  <span className={styles.meta}>de {formatBRL(group.meta)}</span>
                  <span className={styles.percent}>{Math.round(percent)}%</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
