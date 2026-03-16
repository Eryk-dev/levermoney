import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useExtrato } from '../hooks/useExtrato';
import type { ExtratoSellerStatus, ExtratoUploadResult, ExtratoUploadRecord } from '../hooks/useExtrato';
import styles from './ExtratoTab.module.css';

// ── Types ────────────────────────────────────────────────────────

interface Seller {
  slug: string;
  name: string;
  dashboard_empresa?: string;
  active: boolean;
}

interface ExtratoTabProps {
  sellers: Seller[];
  onLogout: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────

function formatMonth(month: string): string {
  const [y, m] = month.split('-');
  const names = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${names[parseInt(m, 10) - 1]}/${y}`;
}

// ── Component ───────────────────────────────────────────────────

export function ExtratoTab({ sellers, onLogout }: ExtratoTabProps) {
  const { loadSellersStatus, uploadExtratos, loadUploadHistory } = useExtrato({ onUnauthorized: onLogout });

  // Coverage status for all sellers
  const [sellersStatus, setSellersStatus] = useState<ExtratoSellerStatus[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);

  // Per-seller upload state
  const [uploadingSlug, setUploadingSlug] = useState<string | null>(null);
  const [uploadResults, setUploadResults] = useState<Map<string, ExtratoUploadResult>>(new Map());
  const [uploadErrors, setUploadErrors] = useState<Map<string, string>>(new Map());

  // Per-seller history (collapsible)
  const [expandedSlugs, setExpandedSlugs] = useState<Set<string>>(new Set());
  const [sellerHistory, setSellerHistory] = useState<Map<string, ExtratoUploadRecord[]>>(new Map());
  const [loadingHistory, setLoadingHistory] = useState<Set<string>>(new Set());

  // File input refs (one per seller card)
  const fileInputRefs = useRef<Map<string, HTMLInputElement>>(new Map());

  // Sorted sellers from status endpoint (already filtered to dashboard_ca)
  const sortedSellers = useMemo(
    () =>
      [...sellersStatus].sort((a, b) =>
        (a.dashboard_empresa || a.name || a.slug).localeCompare(
          b.dashboard_empresa || b.name || b.slug,
        ),
      ),
    [sellersStatus],
  );

  // Load coverage status on mount
  useEffect(() => {
    let cancelled = false;

    const fetchStatus = async () => {
      setLoadingStatus(true);
      const data = await loadSellersStatus();
      if (cancelled) return;
      if (data) setSellersStatus(data);
      setLoadingStatus(false);
    };

    void fetchStatus();
    return () => { cancelled = true; };
  }, [loadSellersStatus]);

  // Reload status for all sellers
  const reloadStatus = useCallback(async () => {
    const data = await loadSellersStatus();
    if (data) setSellersStatus(data);
  }, [loadSellersStatus]);

  // Upload handler for a single seller
  const handleUpload = useCallback(
    async (slug: string, files: FileList) => {
      setUploadingSlug(slug);
      setUploadErrors((prev) => { const n = new Map(prev); n.delete(slug); return n; });

      try {
        const result = await uploadExtratos(slug, Array.from(files));
        if (result) {
          setUploadResults((prev) => {
            const n = new Map(prev);
            n.set(slug, result);
            return n;
          });
          // Reload coverage status
          await reloadStatus();
          // Reload history if expanded
          if (expandedSlugs.has(slug)) {
            const hist = await loadUploadHistory(slug);
            if (hist) {
              setSellerHistory((prev) => { const n = new Map(prev); n.set(slug, hist); return n; });
            }
          }
        }
      } catch (e) {
        setUploadErrors((prev) => {
          const n = new Map(prev);
          n.set(slug, e instanceof Error ? e.message : 'Erro de conexao');
          return n;
        });
      } finally {
        setUploadingSlug(null);
        // Reset file input
        const input = fileInputRefs.current.get(slug);
        if (input) input.value = '';
      }
    },
    [uploadExtratos, reloadStatus, expandedSlugs, loadUploadHistory],
  );

  // Toggle history section for a seller
  const toggleHistory = useCallback(
    (slug: string) => {
      setExpandedSlugs((prev) => {
        const next = new Set(prev);
        if (next.has(slug)) {
          next.delete(slug);
        } else {
          next.add(slug);
          // Lazy load history on first expand
          setLoadingHistory((lb) => { const n = new Set(lb); n.add(slug); return n; });
          void loadUploadHistory(slug).then((hist) => {
            if (hist) {
              setSellerHistory((p) => { const n = new Map(p); n.set(slug, hist); return n; });
            }
            setLoadingHistory((lb) => { const n = new Set(lb); n.delete(slug); return n; });
          });
        }
        return next;
      });
    },
    [loadUploadHistory],
  );

  return (
    <div className={styles.wrapper}>
      {/* Loading state */}
      {loadingStatus && (
        <div className={styles.loading}>Carregando status de extratos...</div>
      )}

      {/* Empty state */}
      {!loadingStatus && sortedSellers.length === 0 && (
        <div className={styles.loading}>Nenhum seller com integracao Conta Azul encontrado.</div>
      )}

      {/* Seller cards grid */}
      {!loadingStatus && sortedSellers.length > 0 && (
        <div className={styles.sellerGrid}>
          {sortedSellers.map((seller) => {
            const isUploading = uploadingSlug === seller.slug;
            const result = uploadResults.get(seller.slug);
            const error = uploadErrors.get(seller.slug);

            return (
              <div
                key={seller.slug}
                className={`${styles.sellerCard} ${seller.coverage_status === 'complete' ? styles.sellerCardComplete : ''}`}
              >
                {/* Header: name + badges */}
                <div className={styles.sellerHeader}>
                  <h3 className={styles.sellerName}>
                    {seller.dashboard_empresa || seller.name || seller.slug}
                  </h3>
                  <div className={styles.badgeRow}>
                    <span
                      className={`${styles.badge} ${
                        seller.coverage_status === 'complete'
                          ? styles.badgeComplete
                          : seller.coverage_status === 'partial'
                            ? styles.badgePartial
                            : styles.badgeMissing
                      }`}
                    >
                      {seller.coverage_status === 'complete'
                        ? 'completo'
                        : seller.coverage_status === 'partial'
                          ? 'parcial'
                          : 'faltante'}
                    </span>
                    {seller.extrato_missing && (
                      <span className={`${styles.badge} ${styles.badgeWarning}`}>
                        sem extrato
                      </span>
                    )}
                  </div>
                </div>

                {/* ca_start_date */}
                {seller.ca_start_date && (
                  <div className={styles.statItem}>
                    <span className={styles.statLabel}>CA desde</span>
                    <span className={styles.statValue}>{seller.ca_start_date}</span>
                  </div>
                )}

                {/* Months grid */}
                <div className={styles.monthsGrid}>
                  {seller.months_needed.map((month) => {
                    const isUploaded = seller.months_uploaded.includes(month);
                    return (
                      <span
                        key={month}
                        className={`${styles.monthChip} ${isUploaded ? styles.monthOk : styles.monthMissing}`}
                      >
                        {formatMonth(month)}
                      </span>
                    );
                  })}
                </div>

                {/* Upload button */}
                <div className={styles.uploadRow}>
                  <input
                    ref={(el) => {
                      if (el) fileInputRefs.current.set(seller.slug, el);
                    }}
                    type="file"
                    accept=".csv,text/csv"
                    multiple
                    className={styles.fileInput}
                    disabled={isUploading || uploadingSlug !== null}
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length > 0) {
                        void handleUpload(seller.slug, e.target.files);
                      }
                    }}
                  />
                  <button
                    className={styles.uploadBtn}
                    disabled={isUploading || uploadingSlug !== null}
                    onClick={() => {
                      const input = fileInputRefs.current.get(seller.slug);
                      if (input) input.click();
                    }}
                  >
                    {isUploading ? 'Enviando...' : 'Upload CSVs'}
                  </button>
                </div>

                {/* Upload result */}
                {result && (
                  <div className={styles.resultBox}>
                    <span className={`${styles.badge} ${styles.badgeComplete}`}>
                      {result.total_files} arquivo(s)
                    </span>
                    <span className={styles.resultDetail}>
                      {result.total_ingested} linhas ingeridas, {result.months_processed.length} mes(es)
                    </span>
                    {result.gdrive_status && result.gdrive_status !== 'skipped' && (
                      <span className={`${styles.badge} ${styles.badgeGdrive}`}>
                        gdrive: {result.gdrive_status}
                      </span>
                    )}
                  </div>
                )}

                {/* Upload error */}
                {error && (
                  <div className={styles.errorBox}>{error}</div>
                )}

                {/* History (collapsible) */}
                <div className={styles.historySection}>
                  <button
                    className={styles.historyToggle}
                    onClick={() => toggleHistory(seller.slug)}
                  >
                    <span
                      className={`${styles.chevron} ${expandedSlugs.has(seller.slug) ? styles.chevronOpen : ''}`}
                    >
                      &#9656;
                    </span>
                    Historico
                  </button>

                  {expandedSlugs.has(seller.slug) && (
                    <div className={styles.historyContent}>
                      {loadingHistory.has(seller.slug) ? (
                        <div className={styles.loading}>Carregando...</div>
                      ) : (() => {
                        const records = sellerHistory.get(seller.slug);
                        if (!records || records.length === 0) {
                          return <div className={styles.loading}>Nenhum upload encontrado.</div>;
                        }
                        return (
                          <table className={styles.historyTable}>
                            <thead>
                              <tr>
                                <th>Mes</th>
                                <th>Arquivo</th>
                                <th>Linhas</th>
                                <th>Status</th>
                                <th>Data</th>
                              </tr>
                            </thead>
                            <tbody>
                              {records.map((rec) => (
                                <tr key={rec.id}>
                                  <td>{formatMonth(rec.month)}</td>
                                  <td className={styles.filenameCell} title={rec.filename || ''}>
                                    {rec.filename
                                      ? rec.filename.length > 20
                                        ? rec.filename.slice(0, 17) + '...'
                                        : rec.filename
                                      : '\u2014'}
                                  </td>
                                  <td>{rec.lines_ingested ?? '\u2014'}</td>
                                  <td>
                                    <span className={styles.statusBadge} data-status={rec.status}>
                                      {rec.status}
                                    </span>
                                  </td>
                                  <td>
                                    {new Date(rec.uploaded_at).toLocaleString('pt-BR', {
                                      day: '2-digit',
                                      month: '2-digit',
                                      year: 'numeric',
                                      hour: '2-digit',
                                      minute: '2-digit',
                                    })}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        );
                      })()}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
