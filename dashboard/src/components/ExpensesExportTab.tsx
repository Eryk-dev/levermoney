import { useState, useEffect, useMemo } from 'react';
import { useExpenses } from '../hooks/useExpenses';
import type { ExpenseStats } from '../hooks/useExpenses';
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

// ── Component ───────────────────────────────────────────────────

export function ExpensesExportTab({ sellers, onLogout }: ExpensesExportTabProps) {
  const { loadStats } = useExpenses({ onUnauthorized: onLogout });

  // Per-seller stats
  const [sellerStats, setSellerStats] = useState<Map<string, ExpenseStats>>(new Map());
  const [loadingStats, setLoadingStats] = useState(true);

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
      {/* Loading state */}
      {loadingStats && (
        <div className={styles.loading}>Carregando stats de todos os sellers...</div>
      )}

      {/* Seller cards grid */}
      {!loadingStats && (
        <div className={styles.sellerGrid}>
          {activeSellers.map((seller) => {
            const stats = sellerStats.get(seller.slug);
            const isEmpty = !stats || stats.total === 0;

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
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
