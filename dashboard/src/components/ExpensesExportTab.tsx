import { useState, useEffect, useMemo, useCallback } from 'react';
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

function formatBRL(value: number): string {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

const PENDING_STATUS_FILTER = 'pending_review,auto_categorized';

function gdriveLabel(status: string | null): string {
  if (!status) return '';
  switch (status) {
    case 'queued': return 'queued';
    case 'uploaded': return 'uploaded';
    case 'skipped_no_drive_root': return 'skipped';
    default: return status;
  }
}

// ── Component ───────────────────────────────────────────────────

export function ExpensesExportTab({ sellers, onLogout }: ExpensesExportTabProps) {
  const { loadStats, exportAndBackup } = useExpenses({ onUnauthorized: onLogout });

  // Per-seller stats
  const [sellerStats, setSellerStats] = useState<Map<string, ExpenseStats>>(new Map());
  const [loadingStats, setLoadingStats] = useState(true);

  // Per-seller exporting state
  const [exportingSlug, setExportingSlug] = useState<string | null>(null);

  // Per-seller export results
  const [exportResults, setExportResults] = useState<Map<string, ExportResult>>(new Map());

  // Confirmation modal
  const [confirmSlug, setConfirmSlug] = useState<string | null>(null);

  // Global export state
  const [globalExportProgress, setGlobalExportProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [globalExportSummary, setGlobalExportSummary] = useState<string[] | null>(null);

  // Sorted active sellers (alphabetical by display name)
  const activeSellers = useMemo(
    () =>
      sellers
        .filter((s) => s.active)
        .sort((a, b) =>
          (a.dashboard_empresa || a.name).localeCompare(
            b.dashboard_empresa || b.name,
          ),
        ),
    [sellers],
  );

  // Reload stats for a single seller
  const reloadSellerStats = useCallback(
    async (slug: string) => {
      const stats = await loadStats(slug, undefined, undefined, PENDING_STATUS_FILTER);
      if (stats) {
        setSellerStats((prev) => {
          const next = new Map(prev);
          next.set(slug, stats);
          return next;
        });
      }
    },
    [loadStats],
  );

  // Export handler for a single seller
  const handleExport = useCallback(
    async (slug: string) => {
      setExportingSlug(slug);
      try {
        const result = await exportAndBackup(slug, undefined, undefined, PENDING_STATUS_FILTER);
        if (result) {
          setExportResults((prev) => {
            const next = new Map(prev);
            next.set(slug, result);
            return next;
          });
          // Reload stats for this seller after successful export
          await reloadSellerStats(slug);
        }
      } finally {
        setExportingSlug(null);
      }
    },
    [exportAndBackup, reloadSellerStats],
  );

  // Handle export button click (with confirmation if pending_review > 0)
  const onExportClick = useCallback(
    (slug: string) => {
      const stats = sellerStats.get(slug);
      if (stats && stats.pending_review_count > 0) {
        setConfirmSlug(slug);
      } else {
        void handleExport(slug);
      }
    },
    [sellerStats, handleExport],
  );

  // Derived: whether any seller has pending expenses
  const anySellerHasPending = useMemo(
    () => activeSellers.some((s) => (sellerStats.get(s.slug)?.total ?? 0) > 0),
    [activeSellers, sellerStats],
  );

  // Global export: export all sellers with pending expenses sequentially
  const handleGlobalExport = useCallback(async () => {
    const sellersWithPending = activeSellers.filter(
      (s) => (sellerStats.get(s.slug)?.total ?? 0) > 0,
    );
    if (sellersWithPending.length === 0) return;

    setGlobalExportSummary(null);
    const batchIds: string[] = [];

    try {
      for (let i = 0; i < sellersWithPending.length; i++) {
        const seller = sellersWithPending[i];
        setGlobalExportProgress({ current: i + 1, total: sellersWithPending.length });
        setExportingSlug(seller.slug);

        const result = await exportAndBackup(
          seller.slug,
          undefined,
          undefined,
          PENDING_STATUS_FILTER,
        );

        if (result) {
          batchIds.push(result.batchId);
          setExportResults((prev) => {
            const next = new Map(prev);
            next.set(seller.slug, result);
            return next;
          });
        }
      }
    } finally {
      setExportingSlug(null);
      setGlobalExportProgress(null);
    }

    if (batchIds.length > 0) {
      setGlobalExportSummary(batchIds);
    }

    // Reload stats for all sellers
    await Promise.all(activeSellers.map((s) => reloadSellerStats(s.slug)));
  }, [activeSellers, sellerStats, exportAndBackup, reloadSellerStats]);

  // Load stats for all active sellers on mount
  useEffect(() => {
    if (activeSellers.length === 0) {
      setLoadingStats(false);
      return;
    }

    let cancelled = false;

    const fetchAll = async () => {
      setLoadingStats(true);
      const results = await Promise.all(
        activeSellers.map(async (s) => {
          const stats = await loadStats(
            s.slug,
            undefined,
            undefined,
            PENDING_STATUS_FILTER,
          );
          return { slug: s.slug, stats };
        }),
      );

      if (cancelled) return;

      const map = new Map<string, ExpenseStats>();
      for (const { slug, stats } of results) {
        if (stats) map.set(slug, stats);
      }
      setSellerStats(map);
      setLoadingStats(false);
    };

    void fetchAll();
    return () => {
      cancelled = true;
    };
  }, [activeSellers, loadStats]);

  return (
    <div className={styles.wrapper}>
      {/* Confirmation modal */}
      {confirmSlug && (
        <div className={styles.modalOverlay}>
          <div className={styles.modal}>
            <p className={styles.modalText}>
              Este seller possui despesas com status <strong>pending_review</strong>.
              Deseja exportar mesmo assim?
            </p>
            <div className={styles.modalActions}>
              <button
                className={styles.modalBtnCancel}
                onClick={() => setConfirmSlug(null)}
              >
                Cancelar
              </button>
              <button
                className={styles.modalBtnConfirm}
                onClick={() => {
                  const slug = confirmSlug;
                  setConfirmSlug(null);
                  void handleExport(slug);
                }}
              >
                Exportar mesmo assim
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loadingStats && (
        <div className={styles.loading}>Carregando stats de todos os sellers...</div>
      )}

      {/* Global export button + summary */}
      {!loadingStats && (
        <div className={styles.globalExportRow}>
          <button
            className={styles.globalExportBtn}
            disabled={
              !anySellerHasPending ||
              exportingSlug !== null ||
              globalExportProgress !== null
            }
            style={globalExportProgress ? { cursor: 'wait' } : undefined}
            onClick={() => void handleGlobalExport()}
          >
            {globalExportProgress
              ? `Exportando ${globalExportProgress.current}/${globalExportProgress.total}...`
              : 'Exportar Todos os Pendentes'}
          </button>

          {globalExportSummary && (
            <div className={styles.globalSummary}>
              <span className={styles.statLabel}>
                {globalExportSummary.length} batch(es) gerados:
              </span>
              {globalExportSummary.map((id) => (
                <span key={id} className={styles.batchIdChip}>
                  {id.slice(0, 12)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Seller cards grid */}
      {!loadingStats && (
        <div className={styles.sellerGrid}>
          {activeSellers.map((seller) => {
            const stats = sellerStats.get(seller.slug);
            const isEmpty = !stats || stats.total === 0;
            const isExporting = exportingSlug === seller.slug;
            const exportResult = exportResults.get(seller.slug);

            return (
              <div
                key={seller.slug}
                className={`${styles.sellerCard} ${isEmpty ? styles.sellerCardEmpty : ''}`}
              >
                <h3 className={styles.sellerName}>
                  {seller.dashboard_empresa || seller.name}
                </h3>

                {stats ? (
                  <div className={styles.sellerStats}>
                    <div className={styles.statItem}>
                      <span className={styles.statLabel}>Total de despesas</span>
                      <span className={styles.statValue}>{stats.total}</span>
                    </div>
                    <div className={styles.statItem}>
                      <span className={styles.statLabel}>Valor total</span>
                      <span className={styles.statValue}>
                        {formatBRL(stats.total_amount)}
                      </span>
                    </div>
                    <div className={styles.statItem}>
                      <span className={styles.statLabel}>Pendentes de revisao</span>
                      <span
                        className={`${styles.statValue} ${stats.pending_review_count > 0 ? styles.statValueWarn : ''}`}
                      >
                        {stats.pending_review_count}
                      </span>
                    </div>
                    <div className={styles.statItem}>
                      <span className={styles.statLabel}>Auto-categorizadas</span>
                      <span className={styles.statValue}>
                        {stats.auto_categorized_count}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className={styles.sellerStats}>
                    <span className={styles.statLabel}>Sem dados</span>
                  </div>
                )}

                {/* Export button */}
                <button
                  className={styles.exportBtn}
                  disabled={isEmpty || isExporting || exportingSlug !== null || globalExportProgress !== null}
                  style={isExporting ? { cursor: 'wait' } : undefined}
                  onClick={() => onExportClick(seller.slug)}
                >
                  {isExporting ? 'Exportando...' : 'Exportar Pendentes'}
                </button>

                {/* Export result */}
                {exportResult && (
                  <div className={styles.exportResult}>
                    <span className={styles.statLabel}>
                      Batch: {exportResult.batchId.slice(0, 12)}
                    </span>
                    {exportResult.gdriveStatus && (
                      <span
                        className={`${styles.badge} ${
                          exportResult.gdriveStatus === 'queued'
                            ? styles.badgeQueued
                            : exportResult.gdriveStatus === 'uploaded'
                              ? styles.badgeUploaded
                              : styles.badgeSkipped
                        }`}
                      >
                        {gdriveLabel(exportResult.gdriveStatus)}
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
