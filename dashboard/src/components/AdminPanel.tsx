import { useState, useMemo, useEffect } from 'react';
import { LogOut } from 'lucide-react';
import { ExpensesExportTab } from './ExpensesExportTab';
import { ExtratoTab } from './ExtratoTab';
import { SellersTab } from './SellersTab';
import type { SellersTabProps } from './SellersTab';
import { useExtrato } from '../hooks/useExtrato';
import styles from './AdminPanel.module.css';

export type { Seller, SyncResult } from './SellersTab';

interface AdminPanelProps extends SellersTabProps {
  onLogout: () => void;
}

type AdminTab = 'sellers' | 'expenses' | 'extratos';

export function AdminPanel({ onLogout, ...sellersTabProps }: AdminPanelProps) {
  const [adminTab, setAdminTab] = useState<AdminTab>('sellers');
  const { loadSellersStatus } = useExtrato({ onUnauthorized: onLogout });

  // Memoize filtered sellers to avoid creating new array references on every render
  const activeSellersForTabs = useMemo(
    () => sellersTabProps.sellers.filter(s => s.active),
    [sellersTabProps.sellers],
  );

  // Extrato coverage badge: sellers with incomplete coverage.
  // Refetched on tab change so the count refreshes after uploads.
  const [extratoIncomplete, setExtratoIncomplete] = useState(0);
  useEffect(() => {
    let cancelled = false;
    void loadSellersStatus().then((data) => {
      if (cancelled || !data) return;
      setExtratoIncomplete(data.filter((s) => s.coverage_status !== 'complete').length);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminTab]);

  const tabs: Array<{ key: AdminTab; label: string; count: number }> = [
    { key: 'sellers', label: 'Sellers', count: sellersTabProps.pendingSellers.length },
    { key: 'extratos', label: 'Extratos', count: extratoIncomplete },
    { key: 'expenses', label: 'Despesas', count: 0 },
  ];

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.headerText}>
          <h2 className={styles.title}>Painel Admin</h2>
          <span className={styles.subtitle}>Sellers, extratos e exportação de despesas</span>
        </div>
        <button type="button" className={styles.logoutBtn} onClick={onLogout}>
          <LogOut size={14} /> Sair
        </button>
      </div>

      <div className={styles.modeToggle}>
        {tabs.map(({ key, label, count }) => (
          <button
            key={key}
            type="button"
            className={`${styles.modeBtn} ${adminTab === key ? styles.modeBtnActive : ''}`}
            onClick={() => setAdminTab(key)}
          >
            {label}
            {count > 0 && <span className={styles.tabCount}>{count}</span>}
          </button>
        ))}
      </div>

      {adminTab === 'sellers' && <SellersTab {...sellersTabProps} />}

      {adminTab === 'expenses' && (
        <ExpensesExportTab
          sellers={activeSellersForTabs}
          onLogout={onLogout}
        />
      )}

      {adminTab === 'extratos' && <ExtratoTab onLogout={onLogout} />}
    </div>
  );
}
