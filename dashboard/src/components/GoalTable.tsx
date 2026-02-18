import { useState } from 'react';
import { formatBRL, formatPercent } from '../utils/dataParser';
import { Settings2 } from 'lucide-react';
import styles from './GoalTable.module.css';

interface CompanyGoalData {
  empresa: string;
  grupo: string;
  realizado: number;
  metaMensal: number;
  metaProporcional: number;
  percentualMeta: number;
  gap: number;
}

interface GoalTableProps {
  data: CompanyGoalData[];
  onEditGoals: () => void;
}

export function GoalTable({ data, onEditGoals }: GoalTableProps) {
  const [sortBy, setSortBy] = useState<'empresa' | 'percentual' | 'gap'>('percentual');
  const [sortDesc, setSortDesc] = useState(true);

  const sortedData = [...data].sort((a, b) => {
    let comparison = 0;
    switch (sortBy) {
      case 'empresa':
        comparison = a.empresa.localeCompare(b.empresa);
        break;
      case 'percentual':
        comparison = a.percentualMeta - b.percentualMeta;
        break;
      case 'gap':
        comparison = a.gap - b.gap;
        break;
    }
    return sortDesc ? -comparison : comparison;
  });

  const handleSort = (column: 'empresa' | 'percentual' | 'gap') => {
    if (sortBy === column) {
      setSortDesc(!sortDesc);
    } else {
      setSortBy(column);
      setSortDesc(true);
    }
  };

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>Metas por Linha</span>
        <button className={styles.editButton} onClick={onEditGoals}>
          <Settings2 size={14} />
          Editar Metas
        </button>
      </div>

      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th onClick={() => handleSort('empresa')} className={styles.sortable}>
                Linha {sortBy === 'empresa' && (sortDesc ? '↓' : '↑')}
              </th>
              <th>Realizado</th>
              <th>Meta</th>
              <th onClick={() => handleSort('percentual')} className={styles.sortable}>
                % Meta {sortBy === 'percentual' && (sortDesc ? '↓' : '↑')}
              </th>
              <th onClick={() => handleSort('gap')} className={styles.sortable}>
                Gap {sortBy === 'gap' && (sortDesc ? '↓' : '↑')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedData.map((item) => {
              const isAhead = item.gap >= 0;
              const progressWidth = Math.min(item.percentualMeta, 100);
              const expectedPercent = item.metaMensal > 0
                ? (item.metaProporcional / item.metaMensal) * 100
                : 0;

              return (
                <tr key={item.empresa}>
                  <td>
                    <div className={styles.empresaCell}>
                      <span className={styles.empresaNome}>{item.empresa}</span>
                      <span className={styles.empresaGrupo}>{item.grupo}</span>
                    </div>
                  </td>
                  <td className={styles.number}>{formatBRL(item.realizado)}</td>
                  <td className={styles.number}>{formatBRL(item.metaMensal)}</td>
                  <td>
                    <div className={styles.progressCell}>
                      <div className={styles.progressBar}>
                        <div
                          className={styles.progressFill}
                          style={{ width: `${progressWidth}%` }}
                        />
                        <div
                          className={styles.expectedMarker}
                          style={{ left: `${expectedPercent}%` }}
                        />
                      </div>
                      <span className={styles.progressText}>
                        {formatPercent(item.percentualMeta)}
                      </span>
                    </div>
                  </td>
                  <td className={`${styles.number} ${isAhead ? styles.positive : styles.negative}`}>
                    {isAhead ? '+' : ''}{formatBRL(item.gap)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
