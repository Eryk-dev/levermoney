import { useState, useEffect, useRef } from 'react';
import { X, Copy } from 'lucide-react';
import { formatBRL } from '../utils/dataParser';
import { MONTH_NAMES, type CompanyYearlyGoal } from '../data/goals';
import styles from './GoalEditor.module.css';

interface GoalEditorProps {
  yearlyGoals: CompanyYearlyGoal[];
  onSave: (goals: CompanyYearlyGoal[]) => void;
  onClose: () => void;
}

export function GoalEditor({ yearlyGoals, onSave, onClose }: GoalEditorProps) {
  const [editedGoals, setEditedGoals] = useState<CompanyYearlyGoal[]>(yearlyGoals);
  const [filter, setFilter] = useState('');
  const [selectedMonth, setSelectedMonth] = useState<number | null>(null);
  const tableRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEditedGoals(yearlyGoals);
  }, [yearlyGoals]);

  const handleChange = (empresa: string, month: number, value: string) => {
    const numValue = parseFloat(value.replace(/\D/g, '')) || 0;
    setEditedGoals((prev) =>
      prev.map((g) =>
        g.empresa === empresa
          ? { ...g, metas: { ...g.metas, [month]: numValue } }
          : g
      )
    );
  };

  const handleSave = () => {
    onSave(editedGoals);
    onClose();
  };

  // Copy month values to all other months
  const copyToAllMonths = (sourceMonth: number) => {
    setEditedGoals((prev) =>
      prev.map((g) => {
        const sourceValue = g.metas[sourceMonth] || 0;
        const newMetas = { ...g.metas };
        for (let m = 1; m <= 12; m++) {
          newMetas[m] = sourceValue;
        }
        return { ...g, metas: newMetas };
      })
    );
  };

  const filteredGoals = editedGoals.filter(
    (g) =>
      g.empresa.toLowerCase().includes(filter.toLowerCase()) ||
      g.grupo.toLowerCase().includes(filter.toLowerCase())
  );

  // Group by grupo
  const grupos = [...new Set(filteredGoals.map(g => g.grupo))];

  // Calculate totals per month
  const monthTotals: Record<number, number> = {};
  for (let m = 1; m <= 12; m++) {
    monthTotals[m] = editedGoals.reduce((sum, g) => sum + (g.metas[m] || 0), 0);
  }

  const yearTotal = Object.values(monthTotals).reduce((a, b) => a + b, 0);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>Metas Anuais por Linha</h2>
          <button className={styles.closeButton} onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className={styles.toolbar}>
          <input
            type="text"
            placeholder="Buscar linha ou grupo..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className={styles.searchInput}
          />
          <div className={styles.copySection}>
            <span className={styles.copyLabel}>Copiar mês para todos:</span>
            <select
              value={selectedMonth || ''}
              onChange={(e) => setSelectedMonth(Number(e.target.value) || null)}
              className={styles.monthSelect}
            >
              <option value="">Selecione...</option>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(m => (
                <option key={m} value={m}>{MONTH_NAMES[m]}</option>
              ))}
            </select>
            <button
              className={styles.copyButton}
              onClick={() => selectedMonth && copyToAllMonths(selectedMonth)}
              disabled={!selectedMonth}
            >
              <Copy size={14} />
              Aplicar
            </button>
          </div>
        </div>

        <div className={styles.tableWrapper} ref={tableRef}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.stickyCol}>Linha</th>
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(m => (
                  <th key={m} className={styles.monthHeader}>{MONTH_NAMES[m]}</th>
                ))}
                <th className={styles.totalCol}>Total</th>
              </tr>
            </thead>
            {grupos.map(grupo => {
              const empresasDoGrupo = filteredGoals.filter(g => g.grupo === grupo);
              const grupoTotals: Record<number, number> = {};
              for (let m = 1; m <= 12; m++) {
                grupoTotals[m] = empresasDoGrupo.reduce((sum, g) => sum + (g.metas[m] || 0), 0);
              }
              const grupoYearTotal = Object.values(grupoTotals).reduce((a, b) => a + b, 0);

              return (
                <tbody key={grupo}>
                  {/* Group header row */}
                  <tr className={styles.groupRow}>
                    <td className={`${styles.stickyCol} ${styles.groupName}`}>{grupo}</td>
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(m => (
                      <td key={m} className={styles.groupTotal}>
                        {(grupoTotals[m] / 1000).toFixed(0)}k
                      </td>
                    ))}
                    <td className={styles.groupTotal}>
                      {(grupoYearTotal / 1000000).toFixed(1)}M
                    </td>
                  </tr>
                  {/* Company rows */}
                  {empresasDoGrupo.map(goal => {
                    const companyTotal = Object.values(goal.metas).reduce((a, b) => a + b, 0);
                    return (
                      <tr key={goal.empresa} className={styles.companyRow}>
                        <td className={`${styles.stickyCol} ${styles.companyName}`}>
                          {goal.empresa}
                        </td>
                        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(m => (
                          <td key={m} className={styles.inputCell}>
                            <input
                              type="text"
                              value={(goal.metas[m] || 0).toLocaleString('pt-BR')}
                              onChange={(e) => handleChange(goal.empresa, m, e.target.value)}
                              className={styles.cellInput}
                            />
                          </td>
                        ))}
                        <td className={styles.companyTotal}>
                          {(companyTotal / 1000).toFixed(0)}k
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              );
            })}
            <tfoot>
              <tr className={styles.totalRow}>
                <td className={`${styles.stickyCol} ${styles.totalLabel}`}>TOTAL</td>
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(m => (
                  <td key={m} className={styles.monthTotal}>
                    {(monthTotals[m] / 1000000).toFixed(1)}M
                  </td>
                ))}
                <td className={styles.yearTotal}>
                  {(yearTotal / 1000000).toFixed(1)}M
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        <div className={styles.footer}>
          <div className={styles.summary}>
            <span>Meta Anual Total:</span>
            <strong>{formatBRL(yearTotal)}</strong>
          </div>
          <div className={styles.actions}>
            <button className={styles.cancelButton} onClick={onClose}>
              Cancelar
            </button>
            <button className={styles.saveButton} onClick={handleSave}>
              Salvar Metas
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
