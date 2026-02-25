import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { formatBRL } from '../utils/dataParser';
import { LogOut, RefreshCw, Check, X, Zap, Settings, Copy, ArrowUpCircle, RotateCcw } from 'lucide-react';
import type { CaAccount, CaCostCenter, ActivateSellerConfig, UpgradeToCAConfig, BackfillStatus, ExtratoProcessedStats } from '../hooks/useAdmin';
import type { RevenueLine } from '../types';
import styles from './AdminPanel.module.css';

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
  ca_backfill_progress?: {
    total: number;
    processed: number;
    orders_processed: number;
    expenses_classified: number;
    skipped: number;
    errors: number;
    baixas_created?: number;
  } | null;
}

interface SyncResult {
  empresa: string;
  date: string;
  valor?: number;
  orders?: number;
  status: string;
}

interface AdminPanelProps {
  sellers: Seller[];
  pendingSellers: Seller[];
  activeSellers: Seller[];
  approveSeller: (id: string, config: {
    dashboard_empresa: string;
    dashboard_grupo: string;
    dashboard_segmento: string;
    ca_conta_bancaria?: string;
    ca_centro_custo_variavel?: string;
  }) => Promise<void>;
  updateSellerConfig: (id: string, config: {
    dashboard_empresa?: string;
    dashboard_grupo?: string;
    dashboard_segmento?: string;
    ca_conta_bancaria?: string;
    ca_centro_custo_variavel?: string;
  }) => Promise<void>;
  rejectSeller: (id: string) => Promise<void>;
  syncStatus: { last_sync: string | null; results: SyncResult[] };
  triggerSync: () => Promise<void>;
  caAccounts: CaAccount[];
  caCostCenters: CaCostCenter[];
  revenueLines: RevenueLine[];
  onLogout: () => void;
  // V3 onboarding
  getInstallLink: () => Promise<{ url: string }>;
  activateSeller: (slug: string, config: ActivateSellerConfig) => Promise<{ status: string; backfill_triggered: boolean; extrato_processed?: ExtratoProcessedStats | null; error_detail?: string }>;
  upgradeToCA: (slug: string, config: UpgradeToCAConfig) => Promise<{ status: string; backfill_triggered: boolean; extrato_processed?: ExtratoProcessedStats | null; error_detail?: string }>;
  getBackfillStatus: (slug: string) => Promise<BackfillStatus>;
  retryBackfill: (slug: string) => Promise<{ status: string }>;
  loadSellers: () => Promise<void>;
}

const NEW_LINE_VALUE = '__new__';

interface ConfigForm {
  id: string;
  mode: 'approve' | 'edit';
  selectedLine: string;
  empresa: string;
  grupo: string;
  segmento: string;
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
}

// V3 Activation form (for pending sellers)
interface ActivationForm {
  sellerId: string;
  sellerSlug: string;
  sellerName: string;
  name: string;
  selectedLine: string;
  empresa: string;
  grupo: string;
  segmento: string;
  integration_mode: 'dashboard_only' | 'dashboard_ca';
  ca_start_year: number;
  ca_start_month: number; // 1-12
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
  extrato_csv: File | null;
}

// V3 Upgrade form (for active dashboard_only sellers)
interface UpgradeForm {
  sellerSlug: string;
  sellerName: string;
  ca_start_year: number;
  ca_start_month: number; // 1-12
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
  extrato_csv: File | null;
}

// ── Month picker helpers ──────────────────────────────────────

const MONTH_NAMES = [
  'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
];

function buildStartDate(year: number, month: number): string {
  // Always first day of month, zero-padded: YYYY-MM-01
  return `${year}-${String(month).padStart(2, '0')}-01`;
}

function generateYearOptions(current: number): number[] {
  return [current - 1, current, current + 1];
}

// ── Backfill status helpers ───────────────────────────────────

function BackfillIndicator({
  seller,
  onRetry,
}: {
  seller: Seller;
  onRetry: (slug: string) => void;
}) {
  const status = seller.ca_backfill_status;
  const progress = seller.ca_backfill_progress;

  if (!status) return null;

  if (status === 'running') {
    const pct = progress && progress.total > 0
      ? Math.round((progress.processed / progress.total) * 100)
      : 0;
    return (
      <div className={styles.backfillRunning}>
        <div className={styles.progressBarWrap}>
          <div className={styles.progressBarFill} style={{ width: `${pct}%` }} />
        </div>
        <span className={styles.backfillLabel}>
          Backfill: {progress ? `${progress.processed}/${progress.total}` : 'iniciando...'}{progress && progress.errors > 0 ? ` | ${progress.errors} erros` : ''}
        </span>
      </div>
    );
  }

  if (status === 'completed') {
    return <span className={styles.badgeCASync}>CA sincronizado</span>;
  }

  if (status === 'failed') {
    return (
      <div className={styles.backfillFailed}>
        <span className={styles.badgeBackfillFailed}>Backfill falhou</span>
        <button
          type="button"
          className={styles.retryBtn}
          onClick={() => onRetry(seller.slug)}
        >
          <RotateCcw size={12} /> Retry
        </button>
      </div>
    );
  }

  if (status === 'pending') {
    return <span className={styles.badgeBackfillPending}>Backfill aguardando...</span>;
  }

  return null;
}

function IntegrationBadge({ seller }: { seller: Seller }) {
  const mode = seller.integration_mode;
  if (mode === 'dashboard_ca') {
    return <span className={styles.badgeCA}>CA</span>;
  }
  return <span className={styles.badgeDashboard}>Dashboard</span>;
}

export function AdminPanel({
  sellers,
  pendingSellers,
  activeSellers,
  approveSeller,
  updateSellerConfig,
  rejectSeller,
  syncStatus,
  triggerSync,
  caAccounts,
  caCostCenters,
  revenueLines,
  onLogout,
  getInstallLink,
  activateSeller,
  upgradeToCA,
  getBackfillStatus,
  retryBackfill,
  loadSellers,
}: AdminPanelProps) {
  const [syncing, setSyncing] = useState(false);
  const [configForm, setConfigForm] = useState<ConfigForm | null>(null);

  // V3 state
  const [installLink, setInstallLink] = useState<string>('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [activationForm, setActivationForm] = useState<ActivationForm | null>(null);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  const [upgradeForm, setUpgradeForm] = useState<UpgradeForm | null>(null);
  const [upgradeError, setUpgradeError] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState(false);
  const [backfillStatuses, setBackfillStatuses] = useState<Record<string, BackfillStatus>>({});
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const currentYear = new Date().getFullYear();
  const currentMonth = new Date().getMonth() + 1; // 1-12

  const existingGrupos = useMemo(() => {
    const set = new Set(revenueLines.map((l) => l.grupo));
    return [...set].sort();
  }, [revenueLines]);

  const existingSegmentos = useMemo(() => {
    const set = new Set(revenueLines.map((l) => l.segmento));
    return [...set].sort();
  }, [revenueLines]);

  // Load install link on mount
  useEffect(() => {
    getInstallLink().then((res) => {
      if (res.url) setInstallLink(res.url);
    });
  }, [getInstallLink]);

  // Backfill polling: start when any active seller has status = 'running' or 'pending'
  const pollBackfillStatuses = useCallback(async () => {
    const runningSellers = activeSellers.filter(
      (s) => s.ca_backfill_status === 'running' || s.ca_backfill_status === 'pending',
    );
    if (runningSellers.length === 0) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      return;
    }
    const results = await Promise.all(
      runningSellers.map(async (s) => {
        const status = await getBackfillStatus(s.slug);
        return [s.slug, status] as [string, BackfillStatus];
      }),
    );
    setBackfillStatuses((prev) => {
      const next = { ...prev };
      results.forEach(([slug, status]) => {
        next[slug] = status;
      });
      return next;
    });
    // If any completed or failed, reload sellers list
    const anyDone = results.some(
      ([, status]) => status.ca_backfill_status === 'completed' || status.ca_backfill_status === 'failed',
    );
    if (anyDone) {
      await loadSellers();
    }
  }, [activeSellers, getBackfillStatus, loadSellers]);

  useEffect(() => {
    const hasRunning = activeSellers.some(
      (s) => s.ca_backfill_status === 'running' || s.ca_backfill_status === 'pending',
    );
    if (hasRunning && !pollingRef.current) {
      // Immediate poll
      void pollBackfillStatuses();
      // Then every 30s
      pollingRef.current = setInterval(() => {
        void pollBackfillStatuses();
      }, 30000);
    } else if (!hasRunning && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [activeSellers, pollBackfillStatuses]);

  // Merge local polling statuses with seller list data
  const getSellerBackfillStatus = useCallback((seller: Seller): Seller => {
    const polledStatus = backfillStatuses[seller.slug];
    if (!polledStatus) return seller;
    return {
      ...seller,
      ca_backfill_status: polledStatus.ca_backfill_status ?? seller.ca_backfill_status,
      ca_backfill_progress: polledStatus.ca_backfill_progress ?? seller.ca_backfill_progress,
    };
  }, [backfillStatuses]);

  // ── Sync ──────────────────────────────────────────────────

  const handleSync = async () => {
    setSyncing(true);
    await triggerSync();
    setSyncing(false);
  };

  // ── Install link ──────────────────────────────────────────

  const handleCopyLink = async () => {
    if (!installLink) return;
    try {
      await navigator.clipboard.writeText(installLink);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const el = document.createElement('input');
      el.value = installLink;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    }
  };

  // ── Legacy approve/edit form ──────────────────────────────

  const handleLineSelect = (value: string) => {
    if (!configForm) return;
    if (value === NEW_LINE_VALUE) {
      setConfigForm({
        ...configForm,
        selectedLine: NEW_LINE_VALUE,
        empresa: configForm.id ? sellers.find((s) => s.id === configForm.id)?.name || '' : '',
        grupo: 'OUTROS',
        segmento: 'OUTROS',
      });
    } else {
      const line = revenueLines.find((l) => l.empresa === value);
      if (line) {
        setConfigForm({
          ...configForm,
          selectedLine: value,
          empresa: line.empresa,
          grupo: line.grupo,
          segmento: line.segmento,
        });
      }
    }
  };

  const handleSubmit = async () => {
    if (!configForm) return;
    const config = {
      dashboard_empresa: configForm.empresa,
      dashboard_grupo: configForm.grupo,
      dashboard_segmento: configForm.segmento,
      ca_conta_bancaria: configForm.ca_conta_bancaria || undefined,
      ca_centro_custo_variavel: configForm.ca_centro_custo_variavel || undefined,
    };
    if (configForm.mode === 'approve') {
      await approveSeller(configForm.id, config);
    } else {
      await updateSellerConfig(configForm.id, config);
    }
    setConfigForm(null);
  };

  const openEditForm = (s: Seller) => {
    const matchedLine = s.dashboard_empresa
      ? revenueLines.find((l) => l.empresa === s.dashboard_empresa)
      : null;
    setConfigForm({
      id: s.id,
      mode: 'edit',
      selectedLine: matchedLine ? matchedLine.empresa : NEW_LINE_VALUE,
      empresa: s.dashboard_empresa || s.name,
      grupo: s.dashboard_grupo || 'OUTROS',
      segmento: s.dashboard_segmento || 'OUTROS',
      ca_conta_bancaria: s.ca_conta_bancaria || '',
      ca_centro_custo_variavel: s.ca_centro_custo_variavel || '',
    });
  };

  const isNewLine = configForm?.selectedLine === NEW_LINE_VALUE;

  // ── V3 Activation form ────────────────────────────────────

  const openActivationForm = (s: Seller) => {
    setActivationError(null);
    setActivationForm({
      sellerId: s.id,
      sellerSlug: s.slug,
      sellerName: s.name,
      name: s.name,
      selectedLine: NEW_LINE_VALUE,
      empresa: s.name,
      grupo: existingGrupos[0] || 'OUTROS',
      segmento: existingSegmentos[0] || 'OUTROS',
      integration_mode: 'dashboard_only',
      ca_start_year: currentYear,
      ca_start_month: currentMonth,
      ca_conta_bancaria: '',
      ca_centro_custo_variavel: '',
      extrato_csv: null,
    });
  };

  const handleActivationLineSelect = (value: string) => {
    if (!activationForm) return;
    if (value === NEW_LINE_VALUE) {
      setActivationForm({
        ...activationForm,
        selectedLine: NEW_LINE_VALUE,
        empresa: activationForm.name,
        grupo: existingGrupos[0] || 'OUTROS',
        segmento: existingSegmentos[0] || 'OUTROS',
      });
    } else {
      const line = revenueLines.find((l) => l.empresa === value);
      if (line) {
        setActivationForm({
          ...activationForm,
          selectedLine: value,
          empresa: line.empresa,
          grupo: line.grupo,
          segmento: line.segmento,
        });
      }
    }
  };

  const handleActivationSubmit = async () => {
    if (!activationForm) return;
    setActivating(true);
    try {
      const config: ActivateSellerConfig = {
        integration_mode: activationForm.integration_mode,
        name: activationForm.name,
        dashboard_empresa: activationForm.empresa,
        dashboard_grupo: activationForm.grupo,
        dashboard_segmento: activationForm.segmento,
      };
      if (activationForm.integration_mode === 'dashboard_ca') {
        config.ca_conta_bancaria = activationForm.ca_conta_bancaria;
        config.ca_centro_custo_variavel = activationForm.ca_centro_custo_variavel;
        config.ca_start_date = buildStartDate(activationForm.ca_start_year, activationForm.ca_start_month);
        config.extrato_csv = activationForm.extrato_csv ?? undefined;
      }
      const result = await activateSeller(activationForm.sellerSlug, config);
      if (result.status === 'error') {
        setActivationError(result.error_detail ?? 'Erro ao ativar seller');
        return;
      }
      if (result.extrato_processed) {
        window.alert('Extrato processado: ' + result.extrato_processed.newly_ingested + ' linhas novas ingeridas.');
      }
      setActivationForm(null);
    } finally {
      setActivating(false);
    }
  };

  const isActivationNewLine = activationForm?.selectedLine === NEW_LINE_VALUE;
  const isCAMode = activationForm?.integration_mode === 'dashboard_ca';
  const isCAModeValid = !isCAMode || (
    activationForm.ca_conta_bancaria !== '' && activationForm.ca_centro_custo_variavel !== '' && activationForm.extrato_csv !== null
  );

  // ── V3 Upgrade form ───────────────────────────────────────

  const openUpgradeForm = (s: Seller) => {
    setUpgradeError(null);
    setUpgradeForm({
      sellerSlug: s.slug,
      sellerName: s.dashboard_empresa || s.name,
      ca_start_year: currentYear,
      ca_start_month: currentMonth,
      ca_conta_bancaria: s.ca_conta_bancaria || '',
      ca_centro_custo_variavel: s.ca_centro_custo_variavel || '',
      extrato_csv: null,
    });
  };

  const handleUpgradeSubmit = async () => {
    if (!upgradeForm) return;
    if (!upgradeForm.ca_conta_bancaria || !upgradeForm.ca_centro_custo_variavel || !upgradeForm.extrato_csv) return;
    setUpgrading(true);
    try {
      const config: UpgradeToCAConfig = {
        ca_conta_bancaria: upgradeForm.ca_conta_bancaria,
        ca_centro_custo_variavel: upgradeForm.ca_centro_custo_variavel,
        ca_start_date: buildStartDate(upgradeForm.ca_start_year, upgradeForm.ca_start_month),
        extrato_csv: upgradeForm.extrato_csv!,
      };
      const result = await upgradeToCA(upgradeForm.sellerSlug, config);
      if (result.status === 'error') {
        setUpgradeError(result.error_detail ?? 'Erro ao fazer upgrade do seller');
        return;
      }
      if (result.extrato_processed) {
        window.alert('Extrato processado: ' + result.extrato_processed.newly_ingested + ' linhas novas ingeridas.');
      }
      setUpgradeForm(null);
    } finally {
      setUpgrading(false);
    }
  };

  // ── Retry backfill ────────────────────────────────────────

  const handleRetryBackfill = async (slug: string) => {
    await retryBackfill(slug);
  };

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.title}>Painel Admin</h2>
        <button type="button" className={styles.logoutBtn} onClick={onLogout}>
          <LogOut size={14} /> Sair
        </button>
      </div>

      {/* Install Link Section */}
      <section className={styles.section}>
        <h3>Link de Conexao ML</h3>
        <p className={styles.installLinkHint}>
          Copie este link e envie para o seller via WhatsApp para que ele autorize o Mercado Livre.
        </p>
        <div className={styles.installLinkRow}>
          <input
            type="text"
            className={styles.installLinkInput}
            value={installLink}
            readOnly
            placeholder="Carregando..."
          />
          <button
            type="button"
            className={styles.copyBtn}
            onClick={handleCopyLink}
            disabled={!installLink}
          >
            <Copy size={14} />
            {linkCopied ? 'Copiado!' : 'Copiar'}
          </button>
        </div>
        {linkCopied && <span className={styles.copiedMsg}>Link copiado!</span>}
      </section>

      {/* Sync Status */}
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h3>Sync Faturamento</h3>
          <button
            type="button"
            className={styles.syncBtn}
            onClick={handleSync}
            disabled={syncing}
          >
            <RefreshCw size={14} className={syncing ? styles.spinning : ''} />
            {syncing ? 'Sincronizando...' : 'Sync Agora'}
          </button>
        </div>
        {syncStatus.last_sync && (
          <p className={styles.lastSync}>
            Ultimo sync: {new Date(syncStatus.last_sync).toLocaleString('pt-BR')}
          </p>
        )}
        {syncStatus.results.length > 0 && (
          <div className={styles.syncResults}>
            {syncStatus.results.map((r, i) => (
              <div key={i} className={`${styles.syncRow} ${styles[`sync_${r.status}`] || ''}`}>
                <span className={styles.syncEmpresa}>{r.empresa}</span>
                <span className={styles.syncVal}>{r.valor ? formatBRL(r.valor) : '-'}</span>
                <span className={styles.syncOrders}>{r.orders ?? 0} pedidos</span>
                <span className={`${styles.syncStatus} ${r.status === 'synced' ? styles.statusOk : ''}`}>
                  {r.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Pending Sellers */}
      {pendingSellers.length > 0 && (
        <section className={styles.section}>
          <h3>Sellers Pendentes ({pendingSellers.length})</h3>
          <div className={styles.sellerList}>
            {pendingSellers.map(s => (
              <div key={s.id} className={styles.sellerCard}>
                <div className={styles.sellerInfo}>
                  <strong>{s.name}</strong>
                  <span className={styles.sellerSlug}>{s.slug}</span>
                  {s.email && <span className={styles.sellerEmail}>{s.email}</span>}
                </div>
                <div className={styles.sellerActions}>
                  <button
                    type="button"
                    className={styles.approveBtn}
                    onClick={() => openActivationForm(s)}
                  >
                    <Check size={14} /> Ativar
                  </button>
                  <button
                    type="button"
                    className={styles.rejectBtn}
                    onClick={() => rejectSeller(s.id)}
                  >
                    <X size={14} /> Rejeitar
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* V3 Activation Form Modal */}
      {activationForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Ativar Seller: {activationForm.sellerName}</h3>

            {/* Name field */}
            <label className={styles.formLabel}>
              Nome do Seller
              <input
                className={styles.formInput}
                value={activationForm.name}
                onChange={e => setActivationForm({ ...activationForm, name: e.target.value })}
                placeholder="Nome exibido no sistema"
              />
            </label>

            {/* Revenue Line selector */}
            <label className={styles.formLabel}>
              Linha de Receita
              <select
                className={styles.formSelect}
                value={activationForm.selectedLine}
                onChange={(e) => handleActivationLineSelect(e.target.value)}
              >
                <option value={NEW_LINE_VALUE}>+ Criar nova linha</option>
                {revenueLines.map((l) => (
                  <option key={l.empresa} value={l.empresa}>
                    {l.empresa} ({l.grupo} / {l.segmento})
                  </option>
                ))}
              </select>
            </label>

            {/* Empresa - editable only for new lines */}
            <label className={styles.formLabel}>
              Empresa (dashboard)
              {isActivationNewLine ? (
                <input
                  className={styles.formInput}
                  value={activationForm.empresa}
                  onChange={e => setActivationForm({ ...activationForm, empresa: e.target.value })}
                  placeholder="Nome da empresa no dashboard"
                />
              ) : (
                <input className={styles.formInput} value={activationForm.empresa} disabled />
              )}
            </label>

            {/* Grupo */}
            <label className={styles.formLabel}>
              Grupo
              {isActivationNewLine ? (
                <div className={styles.comboRow}>
                  <select
                    className={styles.formSelect}
                    value={existingGrupos.includes(activationForm.grupo) ? activationForm.grupo : '__custom__'}
                    onChange={(e) => {
                      if (e.target.value !== '__custom__') {
                        setActivationForm({ ...activationForm, grupo: e.target.value });
                      }
                    }}
                  >
                    {existingGrupos.map((g) => (
                      <option key={g} value={g}>{g}</option>
                    ))}
                    <option value="__custom__">Outro...</option>
                  </select>
                  {!existingGrupos.includes(activationForm.grupo) && (
                    <input
                      className={styles.formInput}
                      value={activationForm.grupo}
                      onChange={e => setActivationForm({ ...activationForm, grupo: e.target.value })}
                      placeholder="Nome do grupo"
                    />
                  )}
                </div>
              ) : (
                <input className={styles.formInput} value={activationForm.grupo} disabled />
              )}
            </label>

            {/* Segmento */}
            <label className={styles.formLabel}>
              Segmento
              {isActivationNewLine ? (
                <div className={styles.comboRow}>
                  <select
                    className={styles.formSelect}
                    value={existingSegmentos.includes(activationForm.segmento) ? activationForm.segmento : '__custom__'}
                    onChange={(e) => {
                      if (e.target.value !== '__custom__') {
                        setActivationForm({ ...activationForm, segmento: e.target.value });
                      }
                    }}
                  >
                    {existingSegmentos.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                    <option value="__custom__">Outro...</option>
                  </select>
                  {!existingSegmentos.includes(activationForm.segmento) && (
                    <input
                      className={styles.formInput}
                      value={activationForm.segmento}
                      onChange={e => setActivationForm({ ...activationForm, segmento: e.target.value })}
                      placeholder="Nome do segmento"
                    />
                  )}
                </div>
              ) : (
                <input className={styles.formInput} value={activationForm.segmento} disabled />
              )}
            </label>

            {/* Integration mode toggle */}
            <div className={styles.formLabel}>
              Modo de Integracao
              <div className={styles.modeToggle}>
                <button
                  type="button"
                  className={`${styles.modeBtn} ${activationForm.integration_mode === 'dashboard_only' ? styles.modeBtnActive : ''}`}
                  onClick={() => setActivationForm({ ...activationForm, integration_mode: 'dashboard_only', extrato_csv: null })}
                >
                  Dashboard only
                </button>
                <button
                  type="button"
                  className={`${styles.modeBtn} ${activationForm.integration_mode === 'dashboard_ca' ? styles.modeBtnActive : ''}`}
                  onClick={() => setActivationForm({ ...activationForm, integration_mode: 'dashboard_ca' })}
                >
                  Dashboard + Conta Azul
                </button>
              </div>
            </div>

            {/* CA fields - only when dashboard_ca mode */}
            {isCAMode && (
              <>
                <label className={styles.formLabel}>
                  Mes de Inicio (CA)
                  <div className={styles.monthPickerRow}>
                    <select
                      className={styles.formSelect}
                      value={activationForm.ca_start_month}
                      onChange={e => setActivationForm({ ...activationForm, ca_start_month: Number(e.target.value) })}
                    >
                      {MONTH_NAMES.map((name, i) => (
                        <option key={i + 1} value={i + 1}>{name}</option>
                      ))}
                    </select>
                    <select
                      className={styles.formSelectYear}
                      value={activationForm.ca_start_year}
                      onChange={e => setActivationForm({ ...activationForm, ca_start_year: Number(e.target.value) })}
                    >
                      {generateYearOptions(currentYear).map((y) => (
                        <option key={y} value={y}>{y}</option>
                      ))}
                    </select>
                  </div>
                  <span className={styles.startDatePreview}>
                    Data de inicio: {buildStartDate(activationForm.ca_start_year, activationForm.ca_start_month)}
                  </span>
                </label>

                <label className={styles.formLabel}>
                  Conta Bancaria CA
                  <select
                    className={styles.formSelect}
                    value={activationForm.ca_conta_bancaria}
                    onChange={e => setActivationForm({ ...activationForm, ca_conta_bancaria: e.target.value })}
                  >
                    <option value="">Selecione...</option>
                    {caAccounts.map((acc) => (
                      <option key={acc.id} value={acc.id}>
                        {acc.nome}{acc.tipo ? ` (${acc.tipo})` : ''}
                      </option>
                    ))}
                  </select>
                </label>

                <label className={styles.formLabel}>
                  Centro de Custo CA
                  <select
                    className={styles.formSelect}
                    value={activationForm.ca_centro_custo_variavel}
                    onChange={e => setActivationForm({ ...activationForm, ca_centro_custo_variavel: e.target.value })}
                  >
                    <option value="">Selecione...</option>
                    {caCostCenters.map((cc) => (
                      <option key={cc.id} value={cc.id}>
                        {cc.descricao}
                      </option>
                    ))}
                  </select>
                </label>

                <label className={styles.formLabel}>
                  Extrato MP (account_statement CSV)
                  <input
                    type="file"
                    accept=".csv"
                    onChange={e => setActivationForm({ ...activationForm!, extrato_csv: e.target.files?.[0] ?? null })}
                  />
                </label>
                {activationForm.extrato_csv && (
                  <span className={styles.startDatePreview}>✓ {activationForm.extrato_csv.name}</span>
                )}
              </>
            )}

            {activationError && <p className={styles.formError}>{activationError}</p>}

            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.approveBtn}
                onClick={handleActivationSubmit}
                disabled={activating || !isCAModeValid}
              >
                {activating ? 'Ativando...' : 'Ativar'}
              </button>
              <button
                type="button"
                className={styles.rejectBtn}
                onClick={() => setActivationForm(null)}
                disabled={activating}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Legacy Edit Form Modal (for active sellers config) */}
      {configForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Configurar Seller</h3>

            {/* Revenue Line selector */}
            <label className={styles.formLabel}>
              Linha de Receita
              <select
                className={styles.formSelect}
                value={configForm.selectedLine}
                onChange={(e) => handleLineSelect(e.target.value)}
              >
                <option value={NEW_LINE_VALUE}>+ Criar nova linha</option>
                {revenueLines.map((l) => (
                  <option key={l.empresa} value={l.empresa}>
                    {l.empresa} ({l.grupo} / {l.segmento})
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.formLabel}>
              Empresa (dashboard)
              {isNewLine ? (
                <input
                  className={styles.formInput}
                  value={configForm.empresa}
                  onChange={e => setConfigForm({ ...configForm, empresa: e.target.value })}
                  placeholder="Nome da empresa no dashboard"
                />
              ) : (
                <input className={styles.formInput} value={configForm.empresa} disabled />
              )}
            </label>

            <label className={styles.formLabel}>
              Grupo
              {isNewLine ? (
                <div className={styles.comboRow}>
                  <select
                    className={styles.formSelect}
                    value={existingGrupos.includes(configForm.grupo) ? configForm.grupo : '__custom__'}
                    onChange={(e) => {
                      if (e.target.value !== '__custom__') {
                        setConfigForm({ ...configForm, grupo: e.target.value });
                      }
                    }}
                  >
                    {existingGrupos.map((g) => (
                      <option key={g} value={g}>{g}</option>
                    ))}
                    <option value="__custom__">Outro...</option>
                  </select>
                  {!existingGrupos.includes(configForm.grupo) && (
                    <input
                      className={styles.formInput}
                      value={configForm.grupo}
                      onChange={e => setConfigForm({ ...configForm, grupo: e.target.value })}
                      placeholder="Nome do grupo"
                    />
                  )}
                </div>
              ) : (
                <input className={styles.formInput} value={configForm.grupo} disabled />
              )}
            </label>

            <label className={styles.formLabel}>
              Segmento
              {isNewLine ? (
                <div className={styles.comboRow}>
                  <select
                    className={styles.formSelect}
                    value={existingSegmentos.includes(configForm.segmento) ? configForm.segmento : '__custom__'}
                    onChange={(e) => {
                      if (e.target.value !== '__custom__') {
                        setConfigForm({ ...configForm, segmento: e.target.value });
                      }
                    }}
                  >
                    {existingSegmentos.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                    <option value="__custom__">Outro...</option>
                  </select>
                  {!existingSegmentos.includes(configForm.segmento) && (
                    <input
                      className={styles.formInput}
                      value={configForm.segmento}
                      onChange={e => setConfigForm({ ...configForm, segmento: e.target.value })}
                      placeholder="Nome do segmento"
                    />
                  )}
                </div>
              ) : (
                <input className={styles.formInput} value={configForm.segmento} disabled />
              )}
            </label>

            <label className={styles.formLabel}>
              Conta Bancaria CA
              <select
                className={styles.formSelect}
                value={configForm.ca_conta_bancaria}
                onChange={e => setConfigForm({ ...configForm, ca_conta_bancaria: e.target.value })}
              >
                <option value="">Selecione...</option>
                {caAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.nome}{acc.tipo ? ` (${acc.tipo})` : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.formLabel}>
              Centro de Custo CA
              <select
                className={styles.formSelect}
                value={configForm.ca_centro_custo_variavel}
                onChange={e => setConfigForm({ ...configForm, ca_centro_custo_variavel: e.target.value })}
              >
                <option value="">Selecione...</option>
                {caCostCenters.map((cc) => (
                  <option key={cc.id} value={cc.id}>
                    {cc.descricao}
                  </option>
                ))}
              </select>
            </label>

            <div className={styles.modalActions}>
              <button type="button" className={styles.approveBtn} onClick={handleSubmit}>
                Salvar
              </button>
              <button type="button" className={styles.rejectBtn} onClick={() => setConfigForm(null)}>Cancelar</button>
            </div>
          </div>
        </div>
      )}

      {/* Upgrade to CA Modal */}
      {upgradeForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Upgrade para CA: {upgradeForm.sellerName}</h3>
            <p className={styles.upgradeNote}>
              O backfill sera iniciado automaticamente apos salvar. Ele buscara todos os pagamentos com <em>money_release_date</em> a partir da data de inicio escolhida.
            </p>

            <label className={styles.formLabel}>
              Mes de Inicio (CA)
              <div className={styles.monthPickerRow}>
                <select
                  className={styles.formSelect}
                  value={upgradeForm.ca_start_month}
                  onChange={e => setUpgradeForm({ ...upgradeForm, ca_start_month: Number(e.target.value) })}
                >
                  {MONTH_NAMES.map((name, i) => (
                    <option key={i + 1} value={i + 1}>{name}</option>
                  ))}
                </select>
                <select
                  className={styles.formSelectYear}
                  value={upgradeForm.ca_start_year}
                  onChange={e => setUpgradeForm({ ...upgradeForm, ca_start_year: Number(e.target.value) })}
                >
                  {generateYearOptions(currentYear).map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              </div>
              <span className={styles.startDatePreview}>
                Data de inicio: {buildStartDate(upgradeForm.ca_start_year, upgradeForm.ca_start_month)}
              </span>
            </label>

            <label className={styles.formLabel}>
              Conta Bancaria CA
              <select
                className={styles.formSelect}
                value={upgradeForm.ca_conta_bancaria}
                onChange={e => setUpgradeForm({ ...upgradeForm, ca_conta_bancaria: e.target.value })}
              >
                <option value="">Selecione...</option>
                {caAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.nome}{acc.tipo ? ` (${acc.tipo})` : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.formLabel}>
              Centro de Custo CA
              <select
                className={styles.formSelect}
                value={upgradeForm.ca_centro_custo_variavel}
                onChange={e => setUpgradeForm({ ...upgradeForm, ca_centro_custo_variavel: e.target.value })}
              >
                <option value="">Selecione...</option>
                {caCostCenters.map((cc) => (
                  <option key={cc.id} value={cc.id}>
                    {cc.descricao}
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.formLabel}>
              Extrato MP (account_statement CSV) *
              <input
                type="file"
                accept=".csv"
                onChange={e => setUpgradeForm({ ...upgradeForm!, extrato_csv: e.target.files?.[0] ?? null })}
              />
            </label>
            {upgradeForm.extrato_csv && (
              <span className={styles.startDatePreview}>✓ {upgradeForm.extrato_csv.name}</span>
            )}

            {upgradeError && <p className={styles.formError}>{upgradeError}</p>}

            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.approveBtn}
                onClick={handleUpgradeSubmit}
                disabled={upgrading || !upgradeForm.ca_conta_bancaria || !upgradeForm.ca_centro_custo_variavel || !upgradeForm.extrato_csv}
              >
                {upgrading ? 'Salvando...' : 'Salvar e Iniciar Backfill'}
              </button>
              <button
                type="button"
                className={styles.rejectBtn}
                onClick={() => setUpgradeForm(null)}
                disabled={upgrading}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active Sellers */}
      <section className={styles.section}>
        <h3>Sellers Ativos ({activeSellers.length})</h3>
        <div className={styles.sellerList}>
          {activeSellers.map(rawSeller => {
            const s = getSellerBackfillStatus(rawSeller);
            const isDashboardOnly = !s.integration_mode || s.integration_mode === 'dashboard_only';
            return (
              <div key={s.id} className={styles.sellerCard}>
                <div className={styles.sellerInfo}>
                  <div className={styles.sellerNameRow}>
                    <strong>{s.dashboard_empresa || s.name}</strong>
                    <IntegrationBadge seller={s} />
                  </div>
                  <span className={styles.sellerSlug}>{s.slug}</span>
                  <span className={styles.sellerMeta}>
                    {s.dashboard_grupo && `${s.dashboard_grupo} / ${s.dashboard_segmento}`}
                    {s.ml_user_id && ` | ML: ${s.ml_user_id}`}
                  </span>
                  {/* Backfill status indicator */}
                  <BackfillIndicator seller={s} onRetry={handleRetryBackfill} />
                </div>
                <div className={styles.sellerActions}>
                  {isDashboardOnly && (
                    <button
                      type="button"
                      className={styles.upgradeBtn}
                      onClick={() => openUpgradeForm(s)}
                      title="Upgrade para Conta Azul"
                    >
                      <ArrowUpCircle size={14} /> Upgrade CA
                    </button>
                  )}
                  <button
                    type="button"
                    className={styles.editBtn}
                    onClick={() => openEditForm(s)}
                  >
                    <Settings size={14} /> Configurar
                  </button>
                  <div className={styles.sellerBadge}>
                    <Zap size={12} /> {s.source || 'ml'}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* All Sellers Summary */}
      <section className={styles.section}>
        <h3>Todos os Sellers ({sellers.length})</h3>
        <div className={styles.sellerList}>
          {sellers.map(s => (
            <div key={s.id} className={`${styles.sellerCard} ${styles[`status_${s.onboarding_status}`] || ''}`}>
              <div className={styles.sellerInfo}>
                <strong>{s.name}</strong>
                <span className={styles.sellerSlug}>{s.slug}</span>
              </div>
              <span className={styles.statusBadge}>{s.onboarding_status}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
