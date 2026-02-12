import { useState, useCallback, useEffect } from 'react';
import { API_BASE } from '../lib/supabase';

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
  ml_user_id?: number;
  source?: string;
  created_at: string;
}

interface SellerConfig {
  dashboard_empresa: string;
  dashboard_grupo: string;
  dashboard_segmento: string;
  ca_conta_mp_retido?: string;
  ca_conta_mp_disponivel?: string;
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

  const isAuthenticated = !!token;

  const headers = useCallback(() => {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h['X-Admin-Token'] = token;
    return h;
  }, [token]);

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
      const res = await fetch(`${API_BASE}/admin/sellers`, { headers: headers() });
      if (res.status === 401) { logout(); return; }
      const data = await res.json();
      setSellers(data);
    } catch (e) {
      console.error('Failed to load sellers:', e);
    }
  }, [token, headers, logout]);

  const approveSeller = useCallback(async (id: string, config: SellerConfig) => {
    if (!token) return;
    await fetch(`${API_BASE}/admin/sellers/${id}/approve`, {
      method: 'POST',
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
      setSyncStatus({ last_sync: new Date().toISOString(), results: data.results });
    }
  }, [token, headers]);

  const loadSyncStatus = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/admin/sync/status`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setSyncStatus(data);
      }
    } catch (e) {
      console.error('Failed to load sync status:', e);
    }
  }, [token, headers]);

  // Load data when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadSellers();
      loadSyncStatus();
    }
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
    rejectSeller,
    syncStatus,
    triggerSync,
    loadSellers,
  };
}
