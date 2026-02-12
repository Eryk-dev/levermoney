import { useState } from 'react';
import { formatBRL } from '../utils/dataParser';
import { LogOut, RefreshCw, Check, X, Zap } from 'lucide-react';
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
  approveSeller: (id: string, config: { dashboard_empresa: string; dashboard_grupo: string; dashboard_segmento: string; ca_conta_bancaria?: string; ca_centro_custo_variavel?: string }) => Promise<void>;
  rejectSeller: (id: string) => Promise<void>;
  syncStatus: { last_sync: string | null; results: SyncResult[] };
  triggerSync: () => Promise<void>;
  onLogout: () => void;
}

export function AdminPanel({
  sellers,
  pendingSellers,
  activeSellers,
  approveSeller,
  rejectSeller,
  syncStatus,
  triggerSync,
  onLogout,
}: AdminPanelProps) {
  const [syncing, setSyncing] = useState(false);
  const [approveForm, setApproveForm] = useState<{ id: string; empresa: string; grupo: string; segmento: string; ca_conta_bancaria: string; ca_centro_custo_variavel: string } | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    await triggerSync();
    setSyncing(false);
  };

  const handleApprove = async () => {
    if (!approveForm) return;
    await approveSeller(approveForm.id, {
      dashboard_empresa: approveForm.empresa,
      dashboard_grupo: approveForm.grupo,
      dashboard_segmento: approveForm.segmento,
      ca_conta_bancaria: approveForm.ca_conta_bancaria || undefined,
      ca_centro_custo_variavel: approveForm.ca_centro_custo_variavel || undefined,
    });
    setApproveForm(null);
  };

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
                    onClick={() => setApproveForm({ id: s.id, empresa: s.name, grupo: 'OUTROS', segmento: 'OUTROS', ca_conta_bancaria: '', ca_centro_custo_variavel: '' })}
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

      {/* Approve Form Modal */}
      {approveForm && (
        <div className={styles.modal}>
          <div className={styles.modalContent}>
            <h3>Aprovar Seller</h3>
            <label className={styles.formLabel}>
              Empresa (dashboard)
              <input
                className={styles.formInput}
                value={approveForm.empresa}
                onChange={e => setApproveForm({ ...approveForm, empresa: e.target.value })}
              />
            </label>
            <label className={styles.formLabel}>
              Grupo
              <input
                className={styles.formInput}
                value={approveForm.grupo}
                onChange={e => setApproveForm({ ...approveForm, grupo: e.target.value })}
              />
            </label>
            <label className={styles.formLabel}>
              Segmento
              <input
                className={styles.formInput}
                value={approveForm.segmento}
                onChange={e => setApproveForm({ ...approveForm, segmento: e.target.value })}
              />
            </label>
            <label className={styles.formLabel}>
              Conta Bancária CA (UUID)
              <input
                className={styles.formInput}
                value={approveForm.ca_conta_bancaria}
                onChange={e => setApproveForm({ ...approveForm, ca_conta_bancaria: e.target.value })}
                placeholder="UUID da conta financeira no Conta Azul"
              />
            </label>
            <label className={styles.formLabel}>
              Centro de Custo CA (UUID)
              <input
                className={styles.formInput}
                value={approveForm.ca_centro_custo_variavel}
                onChange={e => setApproveForm({ ...approveForm, ca_centro_custo_variavel: e.target.value })}
                placeholder="UUID do centro de custo no Conta Azul"
              />
            </label>
            <div className={styles.modalActions}>
              <button type="button" className={styles.approveBtn} onClick={handleApprove}>Confirmar</button>
              <button type="button" className={styles.rejectBtn} onClick={() => setApproveForm(null)}>Cancelar</button>
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
              <div className={styles.sellerBadge}>
                <Zap size={12} /> {s.source || 'ml'}
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
