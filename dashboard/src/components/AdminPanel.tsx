import { useState, useMemo } from 'react';
import { formatBRL } from '../utils/dataParser';
import { LogOut, RefreshCw, Check, X, Zap, Settings } from 'lucide-react';
import type { CaAccount, CaCostCenter, CaContato } from '../hooks/useAdmin';
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
  ca_contato_ml?: string;
  ml_user_id?: number;
  source?: string;
  created_at: string;
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
    ca_contato_ml?: string;
  }) => Promise<void>;
  updateSellerConfig: (id: string, config: {
    dashboard_empresa?: string;
    dashboard_grupo?: string;
    dashboard_segmento?: string;
    ca_conta_bancaria?: string;
    ca_centro_custo_variavel?: string;
    ca_contato_ml?: string;
  }) => Promise<void>;
  rejectSeller: (id: string) => Promise<void>;
  syncStatus: { last_sync: string | null; results: SyncResult[] };
  triggerSync: () => Promise<void>;
  caAccounts: CaAccount[];
  caCostCenters: CaCostCenter[];
  caContatos: CaContato[];
  createContatoForSeller: (sellerId: string) => Promise<unknown>;
  revenueLines: RevenueLine[];
  onLogout: () => void;
}

const NEW_LINE_VALUE = '__new__';

interface ConfigForm {
  id: string;
  mode: 'approve' | 'edit';
  selectedLine: string; // empresa name or '__new__'
  empresa: string;
  grupo: string;
  segmento: string;
  ca_conta_bancaria: string;
  ca_centro_custo_variavel: string;
  ca_contato_ml: string;
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
  caContatos,
  createContatoForSeller,
  revenueLines,
  onLogout,
}: AdminPanelProps) {
  const [syncing, setSyncing] = useState(false);
  const [configForm, setConfigForm] = useState<ConfigForm | null>(null);
  const [creatingContato, setCreatingContato] = useState(false);

  const existingGrupos = useMemo(() => {
    const set = new Set(revenueLines.map((l) => l.grupo));
    return [...set].sort();
  }, [revenueLines]);

  const existingSegmentos = useMemo(() => {
    const set = new Set(revenueLines.map((l) => l.segmento));
    return [...set].sort();
  }, [revenueLines]);

  const handleSync = async () => {
    setSyncing(true);
    await triggerSync();
    setSyncing(false);
  };

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
      ca_contato_ml: configForm.ca_contato_ml || undefined,
    };
    if (configForm.mode === 'approve') {
      await approveSeller(configForm.id, config);
    } else {
      await updateSellerConfig(configForm.id, config);
    }
    setConfigForm(null);
  };

  const openApproveForm = (s: Seller) => {
    setConfigForm({
      id: s.id,
      mode: 'approve',
      selectedLine: NEW_LINE_VALUE,
      empresa: s.name,
      grupo: 'OUTROS',
      segmento: 'OUTROS',
      ca_conta_bancaria: '',
      ca_centro_custo_variavel: '',
      ca_contato_ml: '',
    });
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
      ca_contato_ml: s.ca_contato_ml || '',
    });
  };

  const isNewLine = configForm?.selectedLine === NEW_LINE_VALUE;

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.title}>Painel Admin</h2>
        <button type="button" className={styles.logoutBtn} onClick={onLogout}>
          <LogOut size={14} /> Sair
        </button>
      </div>

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
                    onClick={() => openApproveForm(s)}
                  >
                    <Check size={14} /> Aprovar
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

      {/* Config Form Modal (approve or edit) */}
      {configForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>{configForm.mode === 'approve' ? 'Aprovar Seller' : 'Configurar Seller'}</h3>

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

            {/* Empresa - editable only for new lines */}
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
                <input
                  className={styles.formInput}
                  value={configForm.empresa}
                  disabled
                />
              )}
            </label>

            {/* Grupo dropdown */}
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

            {/* Segmento dropdown */}
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

            {/* CA Conta Bancária dropdown */}
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

            {/* CA Centro de Custo dropdown */}
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

            {/* CA Contato dropdown */}
            <label className={styles.formLabel}>
              Contato CA
              <div className={styles.comboRow}>
                <select
                  className={styles.formSelect}
                  value={configForm.ca_contato_ml}
                  onChange={e => setConfigForm({ ...configForm, ca_contato_ml: e.target.value })}
                >
                  <option value="">(Auto-criar ao aprovar)</option>
                  {caContatos.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.nome}
                    </option>
                  ))}
                </select>
                {configForm.mode === 'edit' && !configForm.ca_contato_ml && (
                  <button
                    type="button"
                    className={styles.approveBtn}
                    disabled={creatingContato}
                    onClick={async () => {
                      setCreatingContato(true);
                      const result = await createContatoForSeller(configForm.id);
                      if (result && typeof result === 'object' && 'ca_contato_ml' in result) {
                        setConfigForm({ ...configForm, ca_contato_ml: (result as { ca_contato_ml: string }).ca_contato_ml });
                      }
                      setCreatingContato(false);
                    }}
                  >
                    {creatingContato ? 'Criando...' : 'Criar agora'}
                  </button>
                )}
              </div>
            </label>

            <div className={styles.modalActions}>
              <button type="button" className={styles.approveBtn} onClick={handleSubmit}>
                {configForm.mode === 'approve' ? 'Aprovar' : 'Salvar'}
              </button>
              <button type="button" className={styles.rejectBtn} onClick={() => setConfigForm(null)}>Cancelar</button>
            </div>
          </div>
        </div>
      )}

      {/* Active Sellers */}
      <section className={styles.section}>
        <h3>Sellers Ativos ({activeSellers.length})</h3>
        <div className={styles.sellerList}>
          {activeSellers.map(s => (
            <div key={s.id} className={styles.sellerCard}>
              <div className={styles.sellerInfo}>
                <strong>{s.dashboard_empresa || s.name}</strong>
                <span className={styles.sellerSlug}>{s.slug}</span>
                <span className={styles.sellerMeta}>
                  {s.dashboard_grupo && `${s.dashboard_grupo} / ${s.dashboard_segmento}`}
                  {s.ml_user_id && ` | ML: ${s.ml_user_id}`}
                </span>
              </div>
              <div className={styles.sellerActions}>
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
          ))}
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
