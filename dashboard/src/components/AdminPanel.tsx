import { useState, useMemo } from 'react';
import { LogOut } from 'lucide-react';
import { ExpensesExportTab } from './ExpensesExportTab';
import { ExtratoTab } from './ExtratoTab';
import { SellersTab } from './SellersTab';
import type { SellersTabProps } from './SellersTab';
import styles from './AdminPanel.module.css';

export type { Seller, SyncResult } from './SellersTab';

interface AdminPanelProps extends SellersTabProps {
  onLogout: () => void;
}

type AdminTab = 'sellers' | 'expenses' | 'extratos';

const TABS: Array<{ key: AdminTab; label: string }> = [
  { key: 'sellers', label: 'Sellers' },
  { key: 'expenses', label: 'Despesas' },
  { key: 'extratos', label: 'Extratos' },
];

export function AdminPanel({ onLogout, ...sellersTabProps }: AdminPanelProps) {
  const [adminTab, setAdminTab] = useState<AdminTab>('sellers');

  // Memoize filtered sellers to avoid creating new array references on every render
  const activeSellersForTabs = useMemo(
    () => sellersTabProps.sellers.filter(s => s.active),
    [sellersTabProps.sellers],
  );

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.title}>Painel Admin</h2>
        <button type="button" className={styles.logoutBtn} onClick={onLogout}>
          <LogOut size={14} /> Sair
        </button>
      </div>

      <div className={styles.modeToggle}>
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`${styles.modeBtn} ${adminTab === key ? styles.modeBtnActive : ''}`}
            onClick={() => setAdminTab(key)}
          >
            {label}
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
