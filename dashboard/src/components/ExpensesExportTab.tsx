import { useState, useEffect, useCallback, useRef } from 'react';
import { useExpenses } from '../hooks/useExpenses';
import type { ExpenseStats, ExportResult, BatchRecord } from '../hooks/useExpenses';
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

function formatBRL(value: number): string {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${dd}/${mm}/${yyyy} ${hh}:${min}`;
}

const MAX_POLL_ATTEMPTS = 12;
const POLL_INTERVAL_MS = 5000;
const ALL_SELLERS_VALUE = '__all__';
const PENDING_STATUS_FILTER = 'pending_review,auto_categorized';

// ── Component ───────────────────────────────────────────────────

export function ExpensesExportTab({ sellers, onLogout }: ExpensesExportTabProps) {
  const { loadStats, exportAndBackup, loadBatches, redownloadBatchById } = useExpenses({ onUnauthorized: onLogout });

  // Filters
  const [selectedSlug, setSelectedSlug] = useState('');

  // Stats
  const [stats, setStats] = useState<ExpenseStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);

  // Per-seller stats (used when __all__ is selected, to know which sellers to export)
  const perSellerStatsRef = useRef<Map<string, ExpenseStats>>(new Map());

  // Export
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [exportResults, setExportResults] = useState<ExportResult[]>([]);

  // Confirmation modal
  const [showConfirm, setShowConfirm] = useState(false);

  // Batches
  const [batches, setBatches] = useState<BatchRecord[]>([]);
  const [downloadingBatch, setDownloadingBatch] = useState<string | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);
  const pollLimitReachedRef = useRef(false);

  // Sorted active sellers
  const activeSellers = sellers
    .filter((s) => s.active)
    .sort((a, b) => a.name.localeCompare(b.name));

  // Load stats when seller changes
  const fetchStats = useCallback(async () => {
    if (!selectedSlug) {
      setStats(null);
      perSellerStatsRef.current = new Map();
      return;
    }
    setLoadingStats(true);
    setExportResult(null);
    setExportResults([]);

    if (selectedSlug === ALL_SELLERS_VALUE) {
      const active = sellers.filter((s) => s.active);
      const results = await Promise.all(
        active.map(async (s) => {
          const result = await loadStats(s.slug, undefined, undefined, PENDING_STATUS_FILTER);
          return { slug: s.slug, stats: result };
        })
      );

      const map = new Map<string, ExpenseStats>();
      const aggregated: ExpenseStats = {
        seller: ALL_SELLERS_VALUE,
        total: 0,
        total_amount: 0,
        by_type: {},
        by_direction: {},
        by_status: {},
        pending_review_count: 0,
        auto_categorized_count: 0,
      };

      for (const { slug, stats: sellerStats } of results) {
        if (sellerStats) {
          map.set(slug, sellerStats);
          aggregated.total += sellerStats.total;
          aggregated.total_amount += sellerStats.total_amount;
          aggregated.pending_review_count += sellerStats.pending_review_count;
          aggregated.auto_categorized_count += sellerStats.auto_categorized_count;
        }
      }

      perSellerStatsRef.current = map;
      setStats(aggregated);
    } else {
      perSellerStatsRef.current = new Map();
      const result = await loadStats(selectedSlug, undefined, undefined, PENDING_STATUS_FILTER);
      setStats(result);
    }

    setLoadingStats(false);
  }, [selectedSlug, loadStats, sellers]);

  useEffect(() => {
    void fetchStats();
  }, [fetchStats]);

  // ── Fetch batches ────────────────────────────────────────────
  const fetchBatches = useCallback(async () => {
    if (!selectedSlug) {
      setBatches([]);
      return null;
    }

    if (selectedSlug === ALL_SELLERS_VALUE) {
      const active = sellers.filter((s) => s.active);
      const results = await Promise.all(
        active.map((s) => loadBatches(s.slug))
      );
      const allBatches = results
        .filter((r): r is BatchRecord[] => r !== null)
        .flat()
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      setBatches(allBatches);
      return allBatches;
    }

    const result = await loadBatches(selectedSlug);
    if (result) setBatches(result);
    return result;
  }, [selectedSlug, loadBatches, sellers]);

  // Load batches when seller changes
  useEffect(() => {
    void fetchBatches();
  }, [fetchBatches]);

  // Export handler (with batch refresh)
  const doExport = async () => {
    if (!selectedSlug) return;
    setExporting(true);
    setShowConfirm(false);

    if (selectedSlug === ALL_SELLERS_VALUE) {
      const results: ExportResult[] = [];
      for (const [slug, sellerStats] of perSellerStatsRef.current) {
        if (sellerStats.total > 0) {
          const result = await exportAndBackup(slug, undefined, undefined, PENDING_STATUS_FILTER);
          if (result) results.push(result);
        }
      }
      setExportResults(results);
      setExportResult(null);
    } else {
      const result = await exportAndBackup(selectedSlug, undefined, undefined, PENDING_STATUS_FILTER);
      setExportResult(result);
      setExportResults([]);
    }

    setExporting(false);
    void fetchBatches();
  };

  const handleExportClick = () => {
    if (stats && stats.pending_review_count > 0) {
      setShowConfirm(true);
    } else {
      void doExport();
    }
  };

  const exportDisabled = !selectedSlug || loadingStats || !stats || stats.total === 0 || exporting;

  // ── Polling for queued batches ──────────────────────────────
  const clearPollingInterval = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  const stopPolling = useCallback((resetAttempts = true) => {
    clearPollingInterval();
    if (resetAttempts) {
      pollCountRef.current = 0;
      pollLimitReachedRef.current = false;
    }
  }, [clearPollingInterval]);

  const startPolling = useCallback(() => {
    if (pollIntervalRef.current) return;
    pollIntervalRef.current = setInterval(async () => {
      pollCountRef.current += 1;
      const result = await fetchBatches();
      const stillQueued = result?.some((b) => b.gdrive_status === 'queued');
      if (!stillQueued) {
        stopPolling(true);
        return;
      }
      if (pollCountRef.current >= MAX_POLL_ATTEMPTS) {
        pollLimitReachedRef.current = true;
        clearPollingInterval();
      }
    }, POLL_INTERVAL_MS);
  }, [clearPollingInterval, fetchBatches, stopPolling]);

  useEffect(() => {
    const hasQueued = batches.some((b) => b.gdrive_status === 'queued');
    if (hasQueued) {
      // Respect max attempts even across state refreshes.
      if (!pollLimitReachedRef.current && pollCountRef.current < MAX_POLL_ATTEMPTS) {
        startPolling();
      }
    } else {
      stopPolling(true);
    }
  }, [batches, startPolling, stopPolling]);

  useEffect(() => {
    return () => {
      stopPolling(true);
    };
  }, [stopPolling]);

  // ── Re-download handler ────────────────────────────────────
  const handleRedownload = async (batch: BatchRecord) => {
    setDownloadingBatch(batch.batch_id);
    await redownloadBatchById(batch.seller_slug, batch.batch_id);
    setDownloadingBatch(null);
  };

  const isAllSellers = selectedSlug === ALL_SELLERS_VALUE;

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
            <option value={ALL_SELLERS_VALUE}>Todos</option>
            {activeSellers.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.dashboard_empresa || s.name}
              </option>
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
          {exporting ? 'Exportando...' : 'Exportar Pendentes'}
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

        {exportResults.length > 0 && (
          <div className={styles.resultInfo}>
            {exportResults.map((r) => (
              <div key={r.batchId}>
                <span className={styles.batchIdLabel}>Batch: {r.batchId}</span>
                {r.gdriveStatus === 'queued' && (
                  <span className={styles.badgeQueued}> Backup: queued</span>
                )}
                {r.gdriveStatus === 'skipped_no_drive_root' && (
                  <span className={styles.badgeSkipped}> Backup: skipped</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Batch history */}
      {selectedSlug && batches.length > 0 && (
        <div className={styles.historySection}>
          <h3 className={styles.historyTitle}>Historico de exports</h3>
          <div className={styles.tableWrap}>
            <table className={styles.batchTable}>
              <thead>
                <tr>
                  <th>Data</th>
                  {isAllSellers && <th>Seller</th>}
                  <th>Linhas</th>
                  <th>Valor</th>
                  <th>Status</th>
                  <th>Backup</th>
                  <th>Batch ID</th>
                  <th>Acoes</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.batch_id}>
                    <td>{formatDateTime(b.created_at)}</td>
                    {isAllSellers && <td>{b.company || b.seller_slug}</td>}
                    <td>{b.rows_count}</td>
                    <td>{formatBRL(b.amount_total_signed)}</td>
                    <td>
                      <span className={
                        b.status === 'generated' ? styles.badgeGenerated :
                        b.status === 'exported' ? styles.badgeExported :
                        b.status === 'imported' ? styles.badgeImported :
                        styles.badgeGenerated
                      }>
                        {b.status}
                      </span>
                    </td>
                    <td>
                      {b.gdrive_status === 'uploaded' && b.gdrive_folder_link ? (
                        <a href={b.gdrive_folder_link} target="_blank" rel="noopener noreferrer" className={styles.badgeUploaded}>
                          uploaded
                        </a>
                      ) : b.gdrive_status === 'queued' ? (
                        <span className={styles.badgeQueued}>queued</span>
                      ) : b.gdrive_status === 'failed' ? (
                        <span className={styles.badgeFailed}>failed</span>
                      ) : b.gdrive_status === 'skipped_no_drive_root' ? (
                        <span className={styles.badgeSkipped}>skipped</span>
                      ) : b.gdrive_status ? (
                        <span className={styles.badgeSkipped}>{b.gdrive_status}</span>
                      ) : (
                        <span className={styles.badgeSkipped}>—</span>
                      )}
                    </td>
                    <td className={styles.batchIdCell}>{b.batch_id.slice(0, 12)}</td>
                    <td>
                      <button
                        type="button"
                        className={styles.downloadBtn}
                        disabled={downloadingBatch === b.batch_id}
                        onClick={() => void handleRedownload(b)}
                      >
                        {downloadingBatch === b.batch_id ? '...' : 'Baixar'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
