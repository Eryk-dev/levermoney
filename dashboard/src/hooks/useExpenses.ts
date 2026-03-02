import { useCallback } from 'react';
import { API_BASE } from '../lib/supabase';

// ── Types ────────────────────────────────────────────────────────

export interface ExpenseStats {
  seller: string;
  total: number;
  total_amount: number;
  by_type: Record<string, number>;
  by_direction: Record<string, number>;
  by_status: Record<string, number>;
  pending_review_count: number;
  auto_categorized_count: number;
}

export interface ExportResult {
  batchId: string;
  gdriveStatus: string | null;
}

export interface BatchRecord {
  id: string;
  batch_id: string;
  seller_slug: string;
  company: string;
  date_from: string | null;
  date_to: string | null;
  rows_count: number;
  amount_total_signed: number;
  status: string;
  created_at: string;
  gdrive_status: string | null;
  gdrive_folder_link: string | null;
  gdrive_file_link: string | null;
}

// ── Constants ────────────────────────────────────────────────────

const TOKEN_KEY = 'lever-admin-token';

// ── Hook ─────────────────────────────────────────────────────────

interface UseExpensesOptions {
  onUnauthorized?: () => void;
}

export function useExpenses({ onUnauthorized }: UseExpensesOptions = {}) {
  const getToken = useCallback((): string | null => {
    return sessionStorage.getItem(TOKEN_KEY);
  }, []);

  const authHeaders = useCallback((): Record<string, string> => {
    const h: Record<string, string> = {};
    const t = getToken();
    if (t) h['X-Admin-Token'] = t;
    return h;
  }, [getToken]);

  const handleUnauthorized = useCallback(() => {
    if (onUnauthorized) onUnauthorized();
  }, [onUnauthorized]);

  // ── loadStats ────────────────────────────────────────────────

  const loadStats = useCallback(
    async (
      sellerSlug: string,
      dateFrom: string,
      dateTo: string,
    ): Promise<ExpenseStats | null> => {
      try {
        const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
        const res = await fetch(
          `${API_BASE}/expenses/${sellerSlug}/stats?${params}`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as ExpenseStats;
      } catch (e) {
        console.error('useExpenses.loadStats failed:', e);
        return null;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  // ── exportAndBackup ──────────────────────────────────────────

  const exportAndBackup = useCallback(
    async (
      sellerSlug: string,
      dateFrom: string,
      dateTo: string,
    ): Promise<ExportResult | null> => {
      try {
        const params = new URLSearchParams({
          mark_exported: 'true',
          gdrive_backup: 'true',
          date_from: dateFrom,
          date_to: dateTo,
        });
        const res = await fetch(
          `${API_BASE}/expenses/${sellerSlug}/export?${params}`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        // Read custom headers
        const batchId = res.headers.get('X-Export-Batch-Id') || '';
        const gdriveStatus = res.headers.get('X-GDrive-Status');

        // Download the blob
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `expenses-${sellerSlug}-${dateFrom}-${dateTo}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        return { batchId, gdriveStatus };
      } catch (e) {
        console.error('useExpenses.exportAndBackup failed:', e);
        return null;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  // ── loadBatches ──────────────────────────────────────────────

  const loadBatches = useCallback(
    async (sellerSlug: string): Promise<BatchRecord[] | null> => {
      try {
        const params = new URLSearchParams({ limit: '20' });
        const res = await fetch(
          `${API_BASE}/expenses/${sellerSlug}/batches?${params}`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as BatchRecord[];
      } catch (e) {
        console.error('useExpenses.loadBatches failed:', e);
        return null;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  // ── redownloadBatchById ──────────────────────────────────────

  const redownloadBatchById = useCallback(
    async (sellerSlug: string, batchId: string): Promise<boolean> => {
      try {
        const res = await fetch(
          `${API_BASE}/expenses/${sellerSlug}/batches/${batchId}/download`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return false; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // Extract filename from Content-Disposition or use fallback
        const disposition = res.headers.get('Content-Disposition') || '';
        const filenameMatch = disposition.match(/filename="?([^";\n]+)"?/);
        a.download = filenameMatch ? filenameMatch[1] : `batch-${batchId}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        return true;
      } catch (e) {
        console.error('useExpenses.redownloadBatchById failed:', e);
        return false;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  return { loadStats, exportAndBackup, loadBatches, redownloadBatchById };
}
