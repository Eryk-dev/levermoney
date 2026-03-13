import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { API_BASE } from '../lib/supabase';
import styles from './ExtratoTab.module.css';

// ── Types ────────────────────────────────────────────────────────

interface Seller {
  slug: string;
  name: string;
  dashboard_empresa?: string;
  active: boolean;
}

interface ExtratoTabProps {
  sellers: Seller[];
  onLogout: () => void;
}

interface UploadResult {
  upload_id: number;
  seller_slug: string;
  month: string;
  filename: string;
  status: string;
  lines_total: number;
  lines_ingested: number;
  lines_skipped: number;
  lines_already_covered: number;
  amount_updated: number;
  initial_balance: number | null;
  final_balance: number | null;
  gaps_found: Record<string, number>;
}

interface UploadRecord {
  id: number;
  filename: string | null;
  month: string;
  status: string;
  lines_total: number | null;
  lines_ingested: number | null;
  lines_skipped: number | null;
  lines_already_covered: number | null;
  initial_balance: number | null;
  final_balance: number | null;
  error_message: string | null;
  uploaded_at: string;
}

// ── Helpers ──────────────────────────────────────────────────────

function formatBRL(value: number): string {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function getCurrentMonth(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

function formatMonth(month: string): string {
  const [y, m] = month.split('-');
  const names = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${names[parseInt(m, 10) - 1]}/${y}`;
}

// ── Component ───────────────────────────────────────────────────

export function ExtratoTab({ sellers, onLogout }: ExtratoTabProps) {
  const token = sessionStorage.getItem('lever-admin-token');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Upload form state
  const [selectedSeller, setSelectedSeller] = useState('');
  const [selectedMonth, setSelectedMonth] = useState(getCurrentMonth);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // History state
  const [historySeller, setHistorySeller] = useState('');
  const [history, setHistory] = useState<UploadRecord[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const activeSellers = useMemo(
    () =>
      sellers
        .filter((s) => s.active)
        .sort((a, b) =>
          (a.dashboard_empresa || a.name).localeCompare(b.dashboard_empresa || b.name),
        ),
    [sellers],
  );

  // Set default seller when sellers load
  useEffect(() => {
    if (activeSellers.length > 0 && !selectedSeller) {
      setSelectedSeller(activeSellers[0].slug);
    }
  }, [activeSellers, selectedSeller]);

  // Load history when seller changes
  const loadHistory = useCallback(
    async (slug: string) => {
      if (!slug || !token) return;
      setLoadingHistory(true);
      try {
        const res = await fetch(
          `${API_BASE}/admin/extrato/uploads/${encodeURIComponent(slug)}?limit=20`,
          {
            headers: { 'X-Admin-Token': token },
            cache: 'no-store',
          },
        );
        if (res.status === 401) {
          onLogout();
          return;
        }
        if (res.ok) {
          const payload = await res.json();
          setHistory(payload.data || []);
        }
      } catch (e) {
        console.error('Failed to load extrato history:', e);
      } finally {
        setLoadingHistory(false);
      }
    },
    [token, onLogout],
  );

  useEffect(() => {
    if (historySeller) {
      void loadHistory(historySeller);
    } else {
      setHistory([]);
    }
  }, [historySeller, loadHistory]);

  // Set default history seller
  useEffect(() => {
    if (activeSellers.length > 0 && !historySeller) {
      setHistorySeller(activeSellers[0].slug);
    }
  }, [activeSellers, historySeller]);

  // Upload handler
  const handleUpload = useCallback(async () => {
    if (!selectedFile || !selectedSeller || !selectedMonth || !token) return;

    setUploading(true);
    setUploadResult(null);
    setUploadError(null);

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('seller_slug', selectedSeller);
    formData.append('month', selectedMonth);

    try {
      const res = await fetch(`${API_BASE}/admin/extrato/upload`, {
        method: 'POST',
        headers: { 'X-Admin-Token': token },
        body: formData,
      });

      if (res.status === 401) {
        onLogout();
        return;
      }

      const data = await res.json();

      if (!res.ok) {
        setUploadError(data.detail || `Erro ${res.status}`);
        return;
      }

      setUploadResult(data);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';

      // Reload history if viewing the same seller
      if (historySeller === selectedSeller) {
        void loadHistory(historySeller);
      }
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Erro de conexao');
    } finally {
      setUploading(false);
    }
  }, [selectedFile, selectedSeller, selectedMonth, token, onLogout, historySeller, loadHistory]);

  return (
    <div className={styles.wrapper}>
      {/* Upload section */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Upload de Extrato</h3>

        <div className={styles.formRow}>
          <label className={styles.formLabel}>
            Seller
            <select
              className={styles.formSelect}
              value={selectedSeller}
              onChange={(e) => setSelectedSeller(e.target.value)}
              disabled={uploading}
            >
              {activeSellers.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.dashboard_empresa || s.name}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formLabel}>
            Mes (YYYY-MM)
            <input
              type="month"
              className={styles.formInput}
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(e.target.value)}
              disabled={uploading}
            />
          </label>
        </div>

        <div className={styles.formRow}>
          <label className={styles.formLabel}>
            Arquivo CSV
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              className={styles.formInput}
              onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
              disabled={uploading}
            />
          </label>
        </div>

        {selectedFile && (
          <div className={styles.fileInfo}>
            {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
          </div>
        )}

        <button
          className={styles.uploadBtn}
          disabled={!selectedFile || !selectedSeller || !selectedMonth || uploading}
          onClick={() => void handleUpload()}
        >
          {uploading ? 'Enviando...' : 'Upload'}
        </button>

        {/* Upload result */}
        {uploadResult && (
          <div className={styles.resultBox}>
            <div className={styles.resultHeader}>
              <span className={styles.statusBadge} data-status="completed">
                completed
              </span>
              <span className={styles.resultFilename}>{uploadResult.filename}</span>
              <span className={styles.resultMonth}>{formatMonth(uploadResult.month)}</span>
            </div>
            <div className={styles.resultStats}>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Total linhas</span>
                <span className={styles.statValue}>{uploadResult.lines_total}</span>
              </div>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Novos gaps</span>
                <span className={`${styles.statValue} ${uploadResult.lines_ingested > 0 ? styles.statValueHighlight : ''}`}>
                  {uploadResult.lines_ingested}
                </span>
              </div>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Ja cobertos</span>
                <span className={styles.statValue}>{uploadResult.lines_already_covered}</span>
              </div>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Internos (skip)</span>
                <span className={styles.statValue}>{uploadResult.lines_skipped}</span>
              </div>
            </div>
            {uploadResult.initial_balance != null && (
              <div className={styles.balanceRow}>
                Saldo: {formatBRL(uploadResult.initial_balance)} → {formatBRL(uploadResult.final_balance ?? 0)}
              </div>
            )}
            {Object.keys(uploadResult.gaps_found).length > 0 && (
              <div className={styles.gapsRow}>
                {Object.entries(uploadResult.gaps_found).map(([type, count]) => (
                  <span key={type} className={styles.gapChip}>
                    {type}: {count}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Upload error */}
        {uploadError && (
          <div className={styles.errorBox}>{uploadError}</div>
        )}
      </section>

      {/* History section */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Historico de Uploads</h3>

        <div className={styles.formRow}>
          <label className={styles.formLabel}>
            Seller
            <select
              className={styles.formSelect}
              value={historySeller}
              onChange={(e) => setHistorySeller(e.target.value)}
            >
              {activeSellers.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.dashboard_empresa || s.name}
                </option>
              ))}
            </select>
          </label>
          <button
            className={styles.refreshBtn}
            onClick={() => void loadHistory(historySeller)}
            disabled={loadingHistory || !historySeller}
          >
            Atualizar
          </button>
        </div>

        {loadingHistory && (
          <div className={styles.loading}>Carregando historico...</div>
        )}

        {!loadingHistory && history.length === 0 && historySeller && (
          <div className={styles.loading}>Nenhum upload encontrado.</div>
        )}

        {!loadingHistory && history.length > 0 && (
          <div className={styles.tableWrap}>
            <table className={styles.historyTable}>
              <thead>
                <tr>
                  <th>Mes</th>
                  <th>Arquivo</th>
                  <th>Status</th>
                  <th>Total</th>
                  <th>Novos</th>
                  <th>Cobertos</th>
                  <th>Saldo Inicial</th>
                  <th>Data Upload</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td>{formatMonth(row.month)}</td>
                    <td className={styles.filenameCell} title={row.filename || ''}>
                      {row.filename ? (row.filename.length > 25 ? row.filename.slice(0, 22) + '...' : row.filename) : '—'}
                    </td>
                    <td>
                      <span className={styles.statusBadge} data-status={row.status}>
                        {row.status}
                      </span>
                    </td>
                    <td>{row.lines_total ?? '—'}</td>
                    <td>{row.lines_ingested ?? '—'}</td>
                    <td>{row.lines_already_covered ?? '—'}</td>
                    <td>{row.initial_balance != null ? formatBRL(row.initial_balance) : '—'}</td>
                    <td>
                      {new Date(row.uploaded_at).toLocaleString('pt-BR', {
                        day: '2-digit',
                        month: '2-digit',
                        year: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
