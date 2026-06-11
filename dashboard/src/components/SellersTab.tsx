import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { formatBRL } from '../utils/dataParser';
import { RefreshCw, Check, X, Settings, Copy, ArrowUpCircle, RotateCcw } from 'lucide-react';
import type { CaAccount, CaCostCenter, ActivateSellerConfig, UpgradeToCAConfig, UpgradeToCAResult, BackfillStatus } from '../hooks/useAdmin';
import type { RevenueLine } from '../types';
import styles from './AdminPanel.module.css';

export interface Seller {
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

export interface SyncResult {
  empresa: string;
  date: string;
  valor?: number;
  orders?: number;
  status: string;
}

export interface SellersTabProps {
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
  // V3 onboarding
  getInstallLink: () => Promise<{ url: string }>;
  activateSeller: (slug: string, config: ActivateSellerConfig) => Promise<{ status: string; backfill_triggered: boolean }>;
  upgradeToCA: (slug: string, config: UpgradeToCAConfig, files: File[]) => Promise<UpgradeToCAResult>;
  getBackfillStatus: (slug: string) => Promise<BackfillStatus>;
  retryBackfill: (slug: string) => Promise<{ status: string }>;
  loadSellers: () => Promise<void>;
}

const NEW_LINE_VALUE = '__new__';

const STATUS_LABELS: Record<string, string> = {
  active: 'Ativo',
  pending_approval: 'Pendente',
  pending: 'Pendente',
  rejected: 'Rejeitado',
  disconnected: 'Desconectado',
};

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
}

// V3 Upgrade form (for active dashboard_only sellers)
interface UpgradeForm {
  sellerSlug: string;
  sellerName: string;
  ca_start_year: number;
  ca_start_month: number; // 1-12
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
  files: File[];
  uploadError: string | null;
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

// ── Shared form fields ────────────────────────────────────────

function LineSelectorField({
  value,
  revenueLines,
  onSelect,
}: {
  value: string;
  revenueLines: RevenueLine[];
  onSelect: (value: string) => void;
}) {
  return (
    <label className={styles.formLabel}>
      Linha de Receita
      <select
        className={styles.formSelect}
        value={value}
        onChange={(e) => onSelect(e.target.value)}
      >
        <option value={NEW_LINE_VALUE}>+ Criar nova linha</option>
        {revenueLines.map((l) => (
          <option key={l.empresa} value={l.empresa}>
            {l.empresa} ({l.grupo} / {l.segmento})
          </option>
        ))}
      </select>
    </label>
  );
}

function ComboField({
  value,
  options,
  placeholder,
  onChange,
}: {
  value: string;
  options: string[];
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const isCustom = !options.includes(value);
  return (
    <div className={styles.comboRow}>
      <select
        className={styles.formSelect}
        value={isCustom ? '__custom__' : value}
        onChange={(e) => {
          onChange(e.target.value === '__custom__' ? '' : e.target.value);
        }}
      >
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
        <option value="__custom__">Outro...</option>
      </select>
      {isCustom && (
        <input
          className={styles.formInput}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
        />
      )}
    </div>
  );
}

function CaFields({
  conta,
  centro,
  caAccounts,
  caCostCenters,
  onConta,
  onCentro,
}: {
  conta: string;
  centro: string;
  caAccounts: CaAccount[];
  caCostCenters: CaCostCenter[];
  onConta: (value: string) => void;
  onCentro: (value: string) => void;
}) {
  return (
    <>
      <label className={styles.formLabel}>
        Conta Bancaria CA
        <select
          className={styles.formSelect}
          value={conta}
          onChange={(e) => onConta(e.target.value)}
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
          value={centro}
          onChange={(e) => onCentro(e.target.value)}
        >
          <option value="">Selecione...</option>
          {caCostCenters.map((cc) => (
            <option key={cc.id} value={cc.id}>
              {cc.descricao}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function MonthYearField({
  month,
  year,
  currentYear,
  onMonth,
  onYear,
}: {
  month: number;
  year: number;
  currentYear: number;
  onMonth: (value: number) => void;
  onYear: (value: number) => void;
}) {
  return (
    <label className={styles.formLabel}>
      Mes de Inicio (CA)
      <div className={styles.monthPickerRow}>
        <select
          className={styles.formSelect}
          value={month}
          onChange={(e) => onMonth(Number(e.target.value))}
        >
          {MONTH_NAMES.map((name, i) => (
            <option key={i + 1} value={i + 1}>{name}</option>
          ))}
        </select>
        <select
          className={styles.formSelectYear}
          value={year}
          onChange={(e) => onYear(Number(e.target.value))}
        >
          {generateYearOptions(currentYear).map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>
      <span className={styles.startDatePreview}>
        Data de inicio: {buildStartDate(year, month)}
      </span>
    </label>
  );
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
    return <span className={styles.badgeCA}>Conta Azul</span>;
  }
  return <span className={styles.badgeDashboard}>Dashboard</span>;
}

export function SellersTab({
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
  getInstallLink,
  activateSeller,
  upgradeToCA,
  getBackfillStatus,
  retryBackfill,
  loadSellers,
}: SellersTabProps) {
  const [syncing, setSyncing] = useState(false);
  const [configForm, setConfigForm] = useState<ConfigForm | null>(null);

  // V3 state
  const [installLink, setInstallLink] = useState<string>('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [activationForm, setActivationForm] = useState<ActivationForm | null>(null);
  const [activating, setActivating] = useState(false);
  const [upgradeForm, setUpgradeForm] = useState<UpgradeForm | null>(null);
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

  // Unified list: pending first (action required), then active, then the rest
  const orderedSellers = useMemo(() => {
    const rank = (s: Seller) =>
      s.onboarding_status === 'pending_approval' ? 0
        : s.onboarding_status === 'active' ? 1
          : 2;
    return [...sellers].sort((a, b) => {
      const r = rank(a) - rank(b);
      if (r !== 0) return r;
      return (a.dashboard_empresa || a.name).localeCompare(b.dashboard_empresa || b.name);
    });
  }, [sellers]);

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
      }
      await activateSeller(activationForm.sellerSlug, config);
      setActivationForm(null);
    } finally {
      setActivating(false);
    }
  };

  const isActivationNewLine = activationForm?.selectedLine === NEW_LINE_VALUE;
  const isCAMode = activationForm?.integration_mode === 'dashboard_ca';
  const isCAModeValid = !isCAMode || (
    activationForm.ca_conta_bancaria !== '' && activationForm.ca_centro_custo_variavel !== ''
  );

  // ── V3 Upgrade form ───────────────────────────────────────

  const openUpgradeForm = (s: Seller) => {
    setUpgradeForm({
      sellerSlug: s.slug,
      sellerName: s.dashboard_empresa || s.name,
      ca_start_year: currentYear,
      ca_start_month: currentMonth,
      ca_conta_bancaria: s.ca_conta_bancaria || '',
      ca_centro_custo_variavel: s.ca_centro_custo_variavel || '',
      files: [],
      uploadError: null,
    });
  };

  const upgradeFileInputRef = useRef<HTMLInputElement>(null);

  const handleUpgradeSubmit = async () => {
    if (!upgradeForm) return;
    if (!upgradeForm.ca_conta_bancaria || !upgradeForm.ca_centro_custo_variavel) return;
    if (upgradeForm.files.length === 0) return;
    setUpgrading(true);
    setUpgradeForm(prev => prev ? { ...prev, uploadError: null } : null);
    try {
      const config: UpgradeToCAConfig = {
        ca_conta_bancaria: upgradeForm.ca_conta_bancaria,
        ca_centro_custo_variavel: upgradeForm.ca_centro_custo_variavel,
        ca_start_date: buildStartDate(upgradeForm.ca_start_year, upgradeForm.ca_start_month),
      };
      const result = await upgradeToCA(upgradeForm.sellerSlug, config, upgradeForm.files);
      if (result.status === 'error') {
        setUpgradeForm(prev => prev ? { ...prev, uploadError: result.error || 'Erro desconhecido' } : null);
        return;
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
    <>
      {/* Utilities: install link + sync, side by side */}
      <div className={styles.utilityGrid}>
        <section className={styles.section}>
          <h3>Conectar novo seller</h3>
          <p className={styles.sectionHint}>
            Envie este link para o seller autorizar o Mercado Livre.
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
        </section>

        <section className={styles.section}>
          <div className={styles.sectionHeader}>
            <h3>Sync de faturamento</h3>
            <button
              type="button"
              className={styles.syncBtn}
              onClick={handleSync}
              disabled={syncing}
            >
              <RefreshCw size={14} className={syncing ? styles.spinning : ''} />
              {syncing ? 'Sincronizando...' : 'Sincronizar'}
            </button>
          </div>
          {syncStatus.last_sync ? (
            <p className={styles.lastSync}>
              Ultimo sync: {new Date(syncStatus.last_sync).toLocaleString('pt-BR')}
            </p>
          ) : (
            <p className={styles.lastSync}>Nenhum sync registrado.</p>
          )}
          {syncStatus.results.length > 0 && (
            <div className={styles.syncResults}>
              {syncStatus.results.map((r, i) => (
                <div key={i} className={`${styles.syncRow} ${styles[`sync_${r.status}`] || ''}`}>
                  <span className={styles.syncEmpresa}>{r.empresa}</span>
                  <span className={styles.syncVal}>{r.valor ? formatBRL(r.valor) : '-'}</span>
                  <span className={styles.syncOrders}>{r.orders ?? 0} pedidos</span>
                  <span className={`${styles.syncStatus} ${r.status === 'synced' ? styles.statusOk : ''}`}>
                    {r.status === 'synced' ? 'ok' : r.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Unified sellers list: pending first, then active, then the rest */}
      <section className={styles.section}>
        <h3>Sellers</h3>
        <p className={styles.sectionHint}>
          {activeSellers.length} ativos
          {pendingSellers.length > 0 && ` · ${pendingSellers.length} aguardando ativacao`}
        </p>
        <div className={styles.sellerList}>
          {orderedSellers.map(rawSeller => {
            const s = getSellerBackfillStatus(rawSeller);
            const isPending = s.onboarding_status === 'pending_approval';
            const isActive = s.onboarding_status === 'active';
            const isDashboardOnly = !s.integration_mode || s.integration_mode === 'dashboard_only';

            return (
              <div
                key={s.id}
                className={`${styles.sellerCard} ${styles[`status_${s.onboarding_status}`] || ''}`}
              >
                <div className={styles.sellerInfo}>
                  <div className={styles.sellerNameRow}>
                    <strong>{s.dashboard_empresa || s.name}</strong>
                    {isActive ? (
                      <IntegrationBadge seller={s} />
                    ) : (
                      <span className={styles.statusBadge}>
                        {STATUS_LABELS[s.onboarding_status] || s.onboarding_status}
                      </span>
                    )}
                  </div>
                  <span className={styles.sellerSlug}>{s.slug}</span>
                  {isPending && s.email && <span className={styles.sellerEmail}>{s.email}</span>}
                  {isActive && (s.dashboard_grupo || s.ml_user_id) && (
                    <span className={styles.sellerMeta}>
                      {s.dashboard_grupo && `${s.dashboard_grupo} / ${s.dashboard_segmento}`}
                      {s.ml_user_id ? `${s.dashboard_grupo ? ' · ' : ''}ML ${s.ml_user_id}` : ''}
                    </span>
                  )}
                  {isActive && <BackfillIndicator seller={s} onRetry={handleRetryBackfill} />}
                </div>
                <div className={styles.sellerActions}>
                  {isPending && (
                    <>
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
                    </>
                  )}
                  {isActive && (
                    <>
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
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* V3 Activation Form Modal */}
      {activationForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Ativar seller: {activationForm.sellerName}</h3>

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

            <LineSelectorField
              value={activationForm.selectedLine}
              revenueLines={revenueLines}
              onSelect={handleActivationLineSelect}
            />

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
                <ComboField
                  value={activationForm.grupo}
                  options={existingGrupos}
                  placeholder="Nome do grupo"
                  onChange={(grupo) => setActivationForm({ ...activationForm, grupo })}
                />
              ) : (
                <input className={styles.formInput} value={activationForm.grupo} disabled />
              )}
            </label>

            {/* Segmento */}
            <label className={styles.formLabel}>
              Segmento
              {isActivationNewLine ? (
                <ComboField
                  value={activationForm.segmento}
                  options={existingSegmentos}
                  placeholder="Nome do segmento"
                  onChange={(segmento) => setActivationForm({ ...activationForm, segmento })}
                />
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
                  onClick={() => setActivationForm({ ...activationForm, integration_mode: 'dashboard_only' })}
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
                <MonthYearField
                  month={activationForm.ca_start_month}
                  year={activationForm.ca_start_year}
                  currentYear={currentYear}
                  onMonth={(ca_start_month) => setActivationForm({ ...activationForm, ca_start_month })}
                  onYear={(ca_start_year) => setActivationForm({ ...activationForm, ca_start_year })}
                />
                <CaFields
                  conta={activationForm.ca_conta_bancaria}
                  centro={activationForm.ca_centro_custo_variavel}
                  caAccounts={caAccounts}
                  caCostCenters={caCostCenters}
                  onConta={(ca_conta_bancaria) => setActivationForm({ ...activationForm, ca_conta_bancaria })}
                  onCentro={(ca_centro_custo_variavel) => setActivationForm({ ...activationForm, ca_centro_custo_variavel })}
                />
              </>
            )}

            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setActivationForm(null)}
                disabled={activating}
              >
                Cancelar
              </button>
              <button
                type="button"
                className={styles.approveBtn}
                onClick={handleActivationSubmit}
                disabled={activating || !isCAModeValid}
              >
                {activating ? 'Ativando...' : 'Ativar seller'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Legacy Edit Form Modal (for active sellers config) */}
      {configForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Configurar seller</h3>

            <LineSelectorField
              value={configForm.selectedLine}
              revenueLines={revenueLines}
              onSelect={handleLineSelect}
            />

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
                <ComboField
                  value={configForm.grupo}
                  options={existingGrupos}
                  placeholder="Nome do grupo"
                  onChange={(grupo) => setConfigForm({ ...configForm, grupo })}
                />
              ) : (
                <input className={styles.formInput} value={configForm.grupo} disabled />
              )}
            </label>

            <label className={styles.formLabel}>
              Segmento
              {isNewLine ? (
                <ComboField
                  value={configForm.segmento}
                  options={existingSegmentos}
                  placeholder="Nome do segmento"
                  onChange={(segmento) => setConfigForm({ ...configForm, segmento })}
                />
              ) : (
                <input className={styles.formInput} value={configForm.segmento} disabled />
              )}
            </label>

            <CaFields
              conta={configForm.ca_conta_bancaria}
              centro={configForm.ca_centro_custo_variavel}
              caAccounts={caAccounts}
              caCostCenters={caCostCenters}
              onConta={(ca_conta_bancaria) => setConfigForm({ ...configForm, ca_conta_bancaria })}
              onCentro={(ca_centro_custo_variavel) => setConfigForm({ ...configForm, ca_centro_custo_variavel })}
            />

            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setConfigForm(null)}>
                Cancelar
              </button>
              <button type="button" className={styles.approveBtn} onClick={handleSubmit}>
                Salvar
              </button>
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
              O backfill sera iniciado automaticamente apos salvar. Os extratos CSV devem cobrir todo o periodo desde a data de inicio ate ontem.
            </p>

            <MonthYearField
              month={upgradeForm.ca_start_month}
              year={upgradeForm.ca_start_year}
              currentYear={currentYear}
              onMonth={(ca_start_month) => setUpgradeForm({ ...upgradeForm, ca_start_month, uploadError: null })}
              onYear={(ca_start_year) => setUpgradeForm({ ...upgradeForm, ca_start_year, uploadError: null })}
            />

            <CaFields
              conta={upgradeForm.ca_conta_bancaria}
              centro={upgradeForm.ca_centro_custo_variavel}
              caAccounts={caAccounts}
              caCostCenters={caCostCenters}
              onConta={(ca_conta_bancaria) => setUpgradeForm({ ...upgradeForm, ca_conta_bancaria })}
              onCentro={(ca_centro_custo_variavel) => setUpgradeForm({ ...upgradeForm, ca_centro_custo_variavel })}
            />

            <div className={styles.formLabel}>
              Extratos CSV (Dinheiro em Conta - MP)
              <input
                ref={upgradeFileInputRef}
                type="file"
                accept=".csv"
                multiple
                className={styles.hiddenInput}
                onChange={e => {
                  const selected = e.target.files ? Array.from(e.target.files) : [];
                  setUpgradeForm(prev => prev ? { ...prev, files: [...prev.files, ...selected], uploadError: null } : null);
                  e.target.value = '';
                }}
              />
              <button
                type="button"
                className={`${styles.btnSecondary} ${styles.fileSelectBtn}`}
                onClick={() => upgradeFileInputRef.current?.click()}
              >
                Selecionar arquivos CSV...
              </button>
              {upgradeForm.files.length > 0 && (
                <div className={styles.fileList}>
                  {upgradeForm.files.map((f, i) => (
                    <div key={i} className={styles.fileRow}>
                      <span>{f.name} ({(f.size / 1024).toFixed(0)} KB)</span>
                      <button
                        type="button"
                        className={styles.fileRemoveBtn}
                        onClick={() => setUpgradeForm(prev => prev ? { ...prev, files: prev.files.filter((_, j) => j !== i), uploadError: null } : null)}
                        title="Remover"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {upgradeForm.files.length === 0 && (
                <span className={styles.fileRequiredHint}>
                  Obrigatorio: selecione os extratos que cobrem de {buildStartDate(upgradeForm.ca_start_year, upgradeForm.ca_start_month)} ate ontem.
                </span>
              )}
            </div>

            {upgradeForm.uploadError && (
              <div className={styles.uploadErrorBox}>
                {upgradeForm.uploadError}
              </div>
            )}

            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setUpgradeForm(null)}
                disabled={upgrading}
              >
                Cancelar
              </button>
              <button
                type="button"
                className={styles.approveBtn}
                onClick={handleUpgradeSubmit}
                disabled={upgrading || !upgradeForm.ca_conta_bancaria || !upgradeForm.ca_centro_custo_variavel || upgradeForm.files.length === 0}
              >
                {upgrading ? 'Processando extrato...' : 'Upload e iniciar backfill'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
