import { useCallback } from 'react';
import { API_BASE } from '../lib/supabase';

// ── Types ────────────────────────────────────────────────────────

export interface ExtratoSellerStatus {
  slug: string;
  name: string | null;
  dashboard_empresa: string | null;
  ca_start_date: string | null;
  extrato_missing: boolean;
  extrato_uploaded_at: string | null;
  months_needed: string[];
  months_uploaded: string[];
  months_missing: string[];
  coverage_status: 'complete' | 'partial' | 'missing';
}

export interface ExtratoUploadResult {
  seller_slug: string;
  total_files: number;
  total_lines: number;
  total_ingested: number;
  total_errors: number;
  months_processed: string[];
  gdrive_status: string;
  results: Array<{
    month: string;
    filename: string;
    status: string;
    lines_total?: number;
    lines_ingested?: number;
    lines_skipped?: number;
    lines_already_covered?: number;
    error?: string;
  }>;
}

export interface ExtratoUploadRecord {
  id: number;
  seller_slug: string;
  month: string;
  filename: string;
  status: string;
  lines_total: number | null;
  lines_ingested: number | null;
  lines_skipped: number | null;
  lines_already_covered: number | null;
  initial_balance: number | null;
  final_balance: number | null;
  uploaded_at: string;
}

interface UploadsResponse {
  seller: string;
  count: number;
  data: ExtratoUploadRecord[];
}

// ── Constants ────────────────────────────────────────────────────

const TOKEN_KEY = 'lever-admin-token';

// ── Hook ─────────────────────────────────────────────────────────

interface UseExtratoOptions {
  onUnauthorized?: () => void;
}

export function useExtrato({ onUnauthorized }: UseExtratoOptions = {}) {
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

  // ── loadSellersStatus ──────────────────────────────────────────

  const loadSellersStatus = useCallback(
    async (): Promise<ExtratoSellerStatus[] | null> => {
      try {
        const res = await fetch(
          `${API_BASE}/admin/extrato/sellers-status`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as ExtratoSellerStatus[];
      } catch (e) {
        console.error('useExtrato.loadSellersStatus failed:', e);
        return null;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  // ── uploadExtratos ─────────────────────────────────────────────

  const uploadExtratos = useCallback(
    async (
      sellerSlug: string,
      files: File[],
    ): Promise<ExtratoUploadResult | null> => {
      try {
        const formData = new FormData();
        for (const file of files) {
          formData.append('files', file);
        }
        const res = await fetch(
          `${API_BASE}/admin/sellers/${sellerSlug}/extrato/upload`,
          {
            method: 'POST',
            headers: authHeaders(),
            body: formData,
          },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(
            detail?.detail?.message || detail?.detail || `HTTP ${res.status}`,
          );
        }
        return (await res.json()) as ExtratoUploadResult;
      } catch (e) {
        console.error('useExtrato.uploadExtratos failed:', e);
        throw e;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  // ── loadUploadHistory ──────────────────────────────────────────

  const loadUploadHistory = useCallback(
    async (sellerSlug: string): Promise<ExtratoUploadRecord[] | null> => {
      try {
        const res = await fetch(
          `${API_BASE}/admin/extrato/uploads/${sellerSlug}`,
          { headers: authHeaders(), cache: 'no-store' },
        );
        if (res.status === 401) { handleUnauthorized(); return null; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = (await res.json()) as UploadsResponse;
        return payload.data ?? [];
      } catch (e) {
        console.error('useExtrato.loadUploadHistory failed:', e);
        return null;
      }
    },
    [authHeaders, handleUnauthorized],
  );

  return { loadSellersStatus, uploadExtratos, loadUploadHistory };
}
