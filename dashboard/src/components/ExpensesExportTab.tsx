import { useState, useEffect, useCallback } from 'react';
import { useExpenses } from '../hooks/useExpenses';
import type { ExpenseStats, ExportResult } from '../hooks/useExpenses';
import styles from './ExpensesExportTab.module.css';

// ── Types ────────────────────────────────────────────────────────

interface Seller {
  slug: string;
  name: string;
  dashboard_empresa?: string;
  active: boolean;
}

interface ExpensesExportTabProps {
  sellers: Seller[];
  onLogout: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────

const MONTH_NAMES = [
  'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
];

function getDefaultPeriod(): { month: number; year: number } {
  const now = new Date();
  // Default = previous month
  let month = now.getMonth(); // 0-indexed, so getMonth() for current gives prev-month in 1-indexed
  let year = now.getFullYear();
  if (month === 0) {
    month = 12;
    year -= 1;
  }
  return { month, year };
}

function buildDateRange(month: number, year: number): { dateFrom: string; dateTo: string } {
  const dateFrom = `${year}-${String(month).padStart(2, '0')}-01`;
  // Last day of the month
  const lastDay = new Date(year, month, 0).getDate();
  const dateTo = `${year}-${String(month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;
  return { dateFrom, dateTo };
}

function formatBRL(value: number): string {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function generateYears(): number[] {
  const current = new Date().getFullYear();
  return [current - 2, current - 1, current, current + 1];
}

// ── Component ───────────────────────────────────────────────────

export function ExpensesExportTab({ sellers, onLogout }: ExpensesExportTabProps) {
  const { loadStats, exportAndBackup } = useExpenses({ onUnauthorized: onLogout });

  // Filters
  const [selectedSlug, setSelectedSlug] = useState('');
  const defaultPeriod = getDefaultPeriod();
  const [month, setMonth] = useState(defaultPeriod.month);
  const [year, setYear] = useState(defaultPeriod.year);

  // Stats
  const [stats, setStats] = useState<ExpenseStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);

  // Export
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);

  // Confirmation modal
  const [showConfirm, setShowConfirm] = useState(false);

  // Sorted active sellers
  const activeSellers = sellers
    .filter((s) => s.active)
    .sort((a, b) => a.name.localeCompare(b.name));

  // Load stats when seller or period changes
  const fetchStats = useCallback(async () => {
    if (!selectedSlug) {
      setStats(null);
      return;
    }
    setLoadingStats(true);
    setExportResult(null);
    const { dateFrom, dateTo } = buildDateRange(month, year);
    const result = await loadStats(selectedSlug, dateFrom, dateTo);
    setStats(result);
    setLoadingStats(false);
  }, [selectedSlug, month, year, loadStats]);

  useEffect(() => {
    void fetchStats();
  }, [fetchStats]);

  // Export handler
  const doExport = async () => {
    if (!selectedSlug) return;
    setExporting(true);
    setShowConfirm(false);
    const { dateFrom, dateTo } = buildDateRange(month, year);
    const result = await exportAndBackup(selectedSlug, dateFrom, dateTo);
    setExportResult(result);
    setExporting(false);
  };

  const handleExportClick = () => {
    if (stats && stats.pending_review_count > 0) {
      setShowConfirm(true);
    } else {
      void doExport();
    }
  };

  const exportDisabled = !selectedSlug || loadingStats || !stats || stats.total === 0 || exporting;

  return (
    <div className={styles.wrapper}>
      {/* Filters */}
      <div className={styles.filtersRow}>
        <div className={styles.filterGroup}>
          <label>Seller</label>
          <select
            value={selectedSlug}
            onChange={(e) => setSelectedSlug(e.target.value)}
          >
            <option value="">Selecione...</option>
            {activeSellers.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.dashboard_empresa || s.name}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.filterGroup}>
          <label>Mes</label>
          <select
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
          >
            {MONTH_NAMES.map((name, i) => (
              <option key={i + 1} value={i + 1}>{name}</option>
            ))}
          </select>
        </div>
        <div className={styles.filterGroup}>
          <label>Ano</label>
          <select
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
          >
            {generateYears().map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Stats */}
      {loadingStats && <div className={styles.loading}>Carregando...</div>}

      {!loadingStats && stats && (
        <div className={styles.statsCard}>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Total de despesas</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Valor total</span>
            <span className={styles.statValue}>{formatBRL(stats.total_amount)}</span>
          </div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Pendentes de revisao</span>
            <span className={`${styles.statValue} ${stats.pending_review_count > 0 ? styles.statValueWarn : ''}`}>
              {stats.pending_review_count}
            </span>
          </div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Auto-categorizadas</span>
            <span className={styles.statValue}>{stats.auto_categorized_count}</span>
          </div>
        </div>
      )}

      {/* Export */}
      <div className={styles.exportRow}>
        <button
          type="button"
          className={`${styles.exportBtn} ${exporting ? styles.exporting : ''}`}
          disabled={exportDisabled}
          onClick={handleExportClick}
        >
          {exporting ? 'Exportando...' : 'Exportar e baixar'}
        </button>

        {exportResult && (
          <div className={styles.resultInfo}>
            <span className={styles.batchIdLabel}>Batch: {exportResult.batchId}</span>
            {exportResult.gdriveStatus === 'queued' && (
              <span className={styles.badgeQueued}>Backup: queued</span>
            )}
            {exportResult.gdriveStatus === 'skipped_no_drive_root' && (
              <span className={styles.badgeSkipped}>Backup: skipped</span>
            )}
          </div>
        )}
      </div>

      {/* Confirmation modal */}
      {showConfirm && stats && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Confirmar exportacao</h3>
            <p>
              Existem <strong>{stats.pending_review_count}</strong> despesas pendentes de revisao.
              Ao exportar, todas serao marcadas como exported.
            </p>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.cancelBtn}
                onClick={() => setShowConfirm(false)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className={styles.confirmBtn}
                onClick={() => void doExport()}
              >
                Exportar mesmo assim
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
