import { useState, useCallback, useEffect } from 'react';
import { API_BASE } from '../lib/supabase';
import type { CompanyYearlyGoal } from '../data/goals';

interface Seller {
  id: string;
  slug: string;
  name: string;
  email?: string;
  active: boolean;
  onboarding_status: string;
  dashboard_empresa?: string;
  dashboard_grupo?: string;
  dashboard_segmento?: string;
  ca_conta_bancaria?: string;
  ca_centro_custo_variavel?: string;
  ml_user_id?: number;
  source?: string;
  created_at: string;
  // V3 onboarding fields
  integration_mode?: 'dashboard_only' | 'dashboard_ca';
  ca_start_date?: string | null;
  ca_backfill_status?: string | null;
  ca_backfill_started_at?: string | null;
  ca_backfill_completed_at?: string | null;
  ca_backfill_progress?: BackfillProgress | null;
}

interface SellerConfig {
  dashboard_empresa: string;
  dashboard_grupo: string;
  dashboard_segmento: string;
  ca_conta_bancaria?: string;
  ca_centro_custo_variavel?: string;
  ca_contato_ml?: string;
  ml_app_id?: string;
  ml_secret_key?: string;
}

interface SyncResult {
  empresa: string;
  date: string;
  valor?: number;
  orders?: number;
  fraud_skipped?: number;
  status: string;
  error?: string;
}

export interface CaAccount {
  id: string;
  nome: string;
  tipo?: string;
}

export interface CaCostCenter {
  id: string;
  descricao: string;
}

// V3 Onboarding types

export interface BackfillProgress {
  total: number;
  processed: number;
  orders_processed: number;
  expenses_classified: number;
  skipped: number;
  errors: number;
  baixas_created?: number;
}

export interface BackfillStatus {
  ca_backfill_status: string | null;
  ca_backfill_started_at: string | null;
  ca_backfill_completed_at: string | null;
  ca_backfill_progress: BackfillProgress | null;
}

export interface ActivateSellerConfig {
  integration_mode: 'dashboard_only' | 'dashboard_ca';
  name?: string;
  dashboard_empresa: string;
  dashboard_grupo: string;
  dashboard_segmento: string;
  ca_conta_bancaria?: string;
  ca_centro_custo_variavel?: string;
  ca_start_date?: string; // YYYY-MM-01
}

export interface UpgradeToCAConfig {
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
  ca_start_date: string; // YYYY-MM-01
}

const TOKEN_KEY = 'lever-admin-token';

export function useAdmin() {
  const [token, setToken] = useState<string | null>(() => {
    return sessionStorage.getItem(TOKEN_KEY);
  });
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; results: SyncResult[] }>({
    last_sync: null,
    results: [],
  });
  const [caAccounts, setCaAccounts] = useState<CaAccount[]>([]);
  const [caCostCenters, setCaCostCenters] = useState<CaCostCenter[]>([]);

  const isAuthenticated = !!token;

  const headers = useCallback(() => {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h['X-Admin-Token'] = token;
    return h;
  }, [token]);
  const noStore = useCallback((): RequestInit => ({
    headers: headers(),
    cache: 'no-store',
  }), [headers]);

  const login = useCallback(async (password: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      setToken(data.token);
      sessionStorage.setItem(TOKEN_KEY, data.token);
      return true;
    } catch {
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    sessionStorage.removeItem(TOKEN_KEY);
    setSellers([]);
  }, []);

  const loadSellers = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/admin/sellers`, noStore());
      if (res.status === 401) { logout(); return; }
      const data = await res.json();
      setSellers(data);
    } catch (e) {
      console.error('Failed to load sellers:', e);
    }
  }, [token, noStore, logout]);

  const approveSeller = useCallback(async (id: string, config: SellerConfig) => {
    if (!token) return;
    await fetch(`${API_BASE}/admin/sellers/${id}/approve`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(config),
    });
    await loadSellers();
  }, [token, headers, loadSellers]);

  const updateSellerConfig = useCallback(async (id: string, config: Partial<SellerConfig>) => {
    if (!token) return;
    await fetch(`${API_BASE}/admin/sellers/${id}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify(config),
    });
    await loadSellers();
  }, [token, headers, loadSellers]);

  const rejectSeller = useCallback(async (id: string) => {
    if (!token) return;
    await fetch(`${API_BASE}/admin/sellers/${id}/reject`, {
      method: 'POST',
      headers: headers(),
    });
    await loadSellers();
  }, [token, headers, loadSellers]);

  const triggerSync = useCallback(async () => {
    if (!token) return;
    const res = await fetch(`${API_BASE}/admin/sync/trigger`, {
      method: 'POST',
      headers: headers(),
    });
    if (res.ok) {
      const data = await res.json();
      setSyncStatus({ last_sync: data.last_sync ?? new Date().toISOString(), results: data.results });
    }
  }, [token, headers]);

  const loadSyncStatus = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/admin/sync/status`, noStore());
      if (res.ok) {
        const data = await res.json();
        setSyncStatus(data);
      }
    } catch (e) {
      console.error('Failed to load sync status:', e);
    }
  }, [token, noStore]);

  // ── CA Resources ──────────────────────────────────────────

  const loadCaAccounts = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/admin/ca/contas-financeiras`, noStore());
      if (res.ok) {
        const data = await res.json();
        setCaAccounts(data);
      }
    } catch (e) {
      console.error('Failed to load CA accounts:', e);
    }
  }, [token, noStore]);

  const loadCaCostCenters = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/admin/ca/centros-custo`, noStore());
      if (res.ok) {
        const data = await res.json();
        setCaCostCenters(data);
      }
    } catch (e) {
      console.error('Failed to load CA cost centers:', e);
    }
  }, [token, noStore]);

  // ── Persistence: Goals & Revenue Lines ────────────────────

  const saveGoalsBulk = useCallback(async (goals: CompanyYearlyGoal[]) => {
    if (!token) return;
    const year = new Date().getFullYear();
    const rows: { empresa: string; grupo: string; year: number; month: number; valor: number }[] = [];
    goals.forEach((g) => {
      Object.entries(g.metas).forEach(([month, valor]) => {
        rows.push({ empresa: g.empresa, grupo: g.grupo, year, month: Number(month), valor });
      });
    });
    try {
      await fetch(`${API_BASE}/admin/goals/bulk`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({ goals: rows }),
      });
    } catch (e) {
      console.error('Failed to save goals:', e);
    }
  }, [token, headers]);

  const createRevenueLine = useCallback(async (line: { empresa: string; grupo: string; segmento: string }) => {
    if (!token) return;
    try {
      await fetch(`${API_BASE}/admin/revenue-lines`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(line),
      });
    } catch (e) {
      console.error('Failed to create revenue line:', e);
    }
  }, [token, headers]);

  const updateRevenueLine = useCallback(async (empresa: string, updates: { grupo?: string; segmento?: string }) => {
    if (!token) return;
    try {
      await fetch(`${API_BASE}/admin/revenue-lines/${encodeURIComponent(empresa)}`, {
        method: 'PATCH',
        headers: headers(),
        body: JSON.stringify(updates),
      });
    } catch (e) {
      console.error('Failed to update revenue line:', e);
    }
  }, [token, headers]);

  const removeRevenueLine = useCallback(async (empresa: string) => {
    if (!token) return;
    try {
      await fetch(`${API_BASE}/admin/revenue-lines/${encodeURIComponent(empresa)}`, {
        method: 'DELETE',
        headers: headers(),
      });
    } catch (e) {
      console.error('Failed to remove revenue line:', e);
    }
  }, [token, headers]);

  // ── V3 Onboarding Methods ──────────────────────────────────

  const getInstallLink = useCallback(async (): Promise<{ url: string }> => {
    if (!token) return { url: '' };
    try {
      const res = await fetch(`${API_BASE}/admin/onboarding/install-link`, noStore());
      if (res.ok) return await res.json();
      // Fallback: construct the URL from the current origin
      const origin = window.location.origin;
      return { url: `${origin}/auth/ml/install` };
    } catch {
      const origin = window.location.origin;
      return { url: `${origin}/auth/ml/install` };
    }
  }, [token, noStore]);

  const activateSeller = useCallback(async (
    slug: string,
    config: ActivateSellerConfig,
  ): Promise<{ status: string; backfill_triggered: boolean }> => {
    if (!token) return { status: 'error', backfill_triggered: false };
    try {
      const res = await fetch(`${API_BASE}/admin/sellers/${slug}/activate`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(config),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        console.error('Failed to activate seller:', errData);
        return { status: 'error', backfill_triggered: false };
      }
      const data = await res.json();
      await loadSellers();
      return data;
    } catch (e) {
      console.error('Failed to activate seller:', e);
      return { status: 'error', backfill_triggered: false };
    }
  }, [token, headers, loadSellers]);

  const upgradeToCA = useCallback(async (
    slug: string,
    config: UpgradeToCAConfig,
  ): Promise<{ status: string; backfill_triggered: boolean }> => {
    if (!token) return { status: 'error', backfill_triggered: false };
    try {
      const res = await fetch(`${API_BASE}/admin/sellers/${slug}/upgrade-to-ca`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(config),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        console.error('Failed to upgrade seller to CA:', errData);
        return { status: 'error', backfill_triggered: false };
      }
      const data = await res.json();
      await loadSellers();
      return data;
    } catch (e) {
      console.error('Failed to upgrade seller to CA:', e);
      return { status: 'error', backfill_triggered: false };
    }
  }, [token, headers, loadSellers]);

  const getBackfillStatus = useCallback(async (slug: string): Promise<BackfillStatus> => {
    if (!token) {
      return {
        ca_backfill_status: null,
        ca_backfill_started_at: null,
        ca_backfill_completed_at: null,
        ca_backfill_progress: null,
      };
    }
    try {
      const res = await fetch(`${API_BASE}/admin/sellers/${slug}/backfill-status`, noStore());
      if (res.ok) return await res.json();
    } catch (e) {
      console.error('Failed to get backfill status:', e);
    }
    return {
      ca_backfill_status: null,
      ca_backfill_started_at: null,
      ca_backfill_completed_at: null,
      ca_backfill_progress: null,
    };
  }, [token, noStore]);

  const retryBackfill = useCallback(async (slug: string): Promise<{ status: string }> => {
    if (!token) return { status: 'error' };
    try {
      const res = await fetch(`${API_BASE}/admin/sellers/${slug}/backfill-retry`, {
        method: 'POST',
        headers: headers(),
      });
      if (res.ok) {
        const data = await res.json();
        await loadSellers();
        return data;
      }
    } catch (e) {
      console.error('Failed to retry backfill:', e);
    }
    return { status: 'error' };
  }, [token, headers, loadSellers]);

  // Load data when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadSellers();
      loadSyncStatus();
      loadCaAccounts();
      loadCaCostCenters();
    }
  }, [isAuthenticated, loadSellers, loadSyncStatus, loadCaAccounts, loadCaCostCenters]);

  // Keep admin panel fresh (sync status + sellers) without manual reload.
  useEffect(() => {
    if (!isAuthenticated) return;

    const refresh = () => {
      void loadSellers();
      void loadSyncStatus();
    };

    const poll = setInterval(refresh, 60_000);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') refresh();
    };

    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearInterval(poll);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [isAuthenticated, loadSellers, loadSyncStatus]);

  const pendingSellers = sellers.filter(s => s.onboarding_status === 'pending_approval');
  const activeSellers = sellers.filter(s => s.onboarding_status === 'active');

  return {
    isAuthenticated,
    login,
    logout,
    sellers,
    pendingSellers,
    activeSellers,
    approveSeller,
    updateSellerConfig,
    rejectSeller,
    syncStatus,
    triggerSync,
    loadSellers,
    caAccounts,
    caCostCenters,
    saveGoalsBulk,
    createRevenueLine,
    updateRevenueLine,
    removeRevenueLine,
    // V3 Onboarding
    getInstallLink,
    activateSeller,
    upgradeToCA,
    getBackfillStatus,
    retryBackfill,
  };
}
