import { useState, useMemo, useRef, useCallback } from 'react';
import { Check, AlertCircle, Calendar, ChevronLeft, ChevronRight } from 'lucide-react';
import type { FaturamentoRecord, RevenueLine } from '../types';
import type { CompanyYearlyGoal } from '../data/goals';
import { formatBRL } from '../utils/dataParser';
import {
  buildCompanyMetaInfo,
  getCompanyAdjustedDailyGoal,
  getCompanyDailyBaseGoal,
} from '../utils/goalCalculator';
import styles from './DataEntry.module.css';

function toMonthInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}
interface DataEntryProps {
  data: FaturamentoRecord[];
  goals: CompanyYearlyGoal[];
  lines: RevenueLine[];
  onSave: (empresa: string, date: string, valor: number | null) => Promise<{ success: boolean; error?: string }>;
}

interface DayColumn {
  date: Date;
  label: string;
  shortLabel: string;
  isToday: boolean;
  isYesterday: boolean;
}

function formatCompact(value: number): string {
  if (value >= 1000000) return `${(value / 1000000).toFixed(2)}M`;
  return `${(value / 1000).toFixed(1)}k`;
}

function getDayLabel(date: Date, today: Date): string {
  const diffDays = Math.round((today.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return 'Hoje';
  if (diffDays === 1) return 'Ontem';
  return date.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '');
}

function getShortLabel(date: Date): string {
  return date.toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit' }).replace('.', '');
}

export function DataEntry({ data, goals, lines, onSave }: DataEntryProps) {
  const today = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }, []);

  const [selectedMonth, setSelectedMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));

  const monthLabel = useMemo(() => {
    return selectedMonth.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
  }, [selectedMonth]);

  const daysToShow = useMemo((): DayColumn[] => {
    const days: DayColumn[] = [];
    const start = new Date(selectedMonth.getFullYear(), selectedMonth.getMonth(), 1);
    const endOfMonth = new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + 1, 0);
    const isCurrentMonth = selectedMonth.getFullYear() === today.getFullYear()
      && selectedMonth.getMonth() === today.getMonth();
    const end = isCurrentMonth ? today : endOfMonth;

    const current = new Date(start);
    while (current <= end) {
      days.push({
        date: new Date(current),
        label: getDayLabel(current, today),
        shortLabel: getShortLabel(current),
        isToday: current.getTime() === today.getTime(),
        isYesterday: Math.round((today.getTime() - current.getTime()) / (1000 * 60 * 60 * 24)) === 1,
      });
      current.setDate(current.getDate() + 1);
    }

    return days;
  }, [selectedMonth, today]);

  const handleMonthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.value) return;
    const [year, month] = e.target.value.split('-').map(Number);
    if (!year || !month) return;
    setSelectedMonth(new Date(year, month - 1, 1));
  };

  const navigateMonth = (direction: 'prev' | 'next') => {
    const offset = direction === 'prev' ? -1 : 1;
    setSelectedMonth((prev) => new Date(prev.getFullYear(), prev.getMonth() + offset, 1));
  };

  // Values state: Map<"empresa:dateKey", number | null>
  const [values, setValues] = useState<Map<string, number | null>>(new Map());
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [savedFields, setSavedFields] = useState<Set<string>>(new Set());
  const inputRefs = useRef<Map<string, HTMLInputElement>>(new Map());

  // Use first day for month calculations
  const referenceDate = daysToShow[0]?.date || today;
  const companyMetaInfo = useMemo(() => buildCompanyMetaInfo(goals, lines), [goals, lines]);

  // Build company info from goals (with adjusted daily targets)
  const companies = useMemo(() => {
    return companyMetaInfo.map(company => {
      const baseDailyGoal = getCompanyDailyBaseGoal(company, referenceDate);
      const weekdayGoal = company.segmento === 'AR CONDICIONADO'
        ? baseDailyGoal * 1.2
        : baseDailyGoal;
      const weekendGoal = company.segmento === 'AR CONDICIONADO'
        ? baseDailyGoal * 0.5
        : baseDailyGoal;

      return {
        ...company,
        baseDailyGoal,
        weekdayGoal,
        weekendGoal,
      };
    });
  }, [companyMetaInfo, referenceDate]);

  // Get existing data for all days
  const existingData = useMemo(() => {
    const result = new Map<string, number>();

    daysToShow.forEach(day => {
      const dateKey = day.date.toISOString().split('T')[0];
      data.forEach(record => {
        const recordKey = record.data.toISOString().split('T')[0];
        if (recordKey === dateKey) {
          result.set(`${record.empresa}:${dateKey}`, record.faturamento);
        }
      });
    });

    return result;
  }, [data, daysToShow]);

  // Track days signature to detect changes
  const daysSignature = daysToShow.map(d => d.date.toISOString().split('T')[0]).join(',');
  const dataSignature = data.map(d => `${d.empresa}:${d.data.toISOString().split('T')[0]}:${d.faturamento}`).join(',');
  const [lastSignature, setLastSignature] = useState('');

  // Initialize/reset values when days or data change
  const currentSignature = `${daysSignature}|${dataSignature}`;
  if (currentSignature !== lastSignature && daysToShow.length > 0) {
    const newValues = new Map<string, number | null>();
    const newSavedFields = new Set<string>();

    daysToShow.forEach(day => {
      const dateKey = day.date.toISOString().split('T')[0];
      companies.forEach(c => {
        const key = `${c.empresa}:${dateKey}`;
        // Get value from Supabase data
        const existing = existingData.get(key);

        const hasExisting = existing !== undefined && existing !== null;
        if (hasExisting) {
          newValues.set(key, existing);
          newSavedFields.add(key);
        } else {
          newValues.set(key, null);
        }
      });
    });
    setValues(newValues);
    setSavedFields(newSavedFields);
    setLastSignature(currentSignature);
  }

  // Calculate stats per day
  const dayStats = useMemo(() => {
    return daysToShow.map(day => {
      const dateKey = day.date.toISOString().split('T')[0];
      let total = 0;
      let filledCount = 0;
      let goalTotal = 0;

      companies.forEach(c => {
        const key = `${c.empresa}:${dateKey}`;
        const value = values.get(key);
        if (value != null) {
          total += value;
          filledCount++;
        }
        goalTotal += getCompanyAdjustedDailyGoal(c, day.date);
      });

      return {
        date: day.date,
        dateKey,
        total,
        filledCount,
        totalCount: companies.length,
        goalTotal,
        percentFilled: Math.round((filledCount / companies.length) * 100),
        percentGoal: goalTotal > 0 ? Math.round((total / goalTotal) * 100) : 0,
      };
    });
  }, [daysToShow, companies, values]);

  // Group companies
  const groups = useMemo(() => {
    const grouped = new Map<string, typeof companies>();
    companies.forEach(c => {
      if (!grouped.has(c.grupo)) {
        grouped.set(c.grupo, []);
      }
      grouped.get(c.grupo)!.push(c);
    });
    return Array.from(grouped.entries());
  }, [companies]);

  // Local state for the input currently being edited to allow smooth typing with decimals
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState<string>('');
  const [isSaving, setIsSaving] = useState(false);
  const savingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleValueChange = useCallback(async (empresa: string, dateKey: string, rawValue: string) => {
    // Allow only digits and a single comma
    const sanitizedValue = rawValue.replace(/[^\d,]/g, '');

    // Check if there's more than one comma and keep only the first one
    const parts = sanitizedValue.split(',');
    const finalValue = parts.length > 2
      ? parts[0] + ',' + parts.slice(1).join('')
      : sanitizedValue;

    setEditingValue(finalValue);

    const isEmpty = finalValue.trim() === '';
    const cleanValue = finalValue.replace(',', '.');
    const numValue = cleanValue ? parseFloat(cleanValue) : 0;
    const key = `${empresa}:${dateKey}`;
    const shouldSaveValue = !isEmpty && numValue >= 0;

    setValues(prev => {
      const next = new Map(prev);
      next.set(key, shouldSaveValue ? numValue : null);
      return next;
    });

    // Auto-save to Supabase with debounce
    if (savingTimeoutRef.current) clearTimeout(savingTimeoutRef.current);

    savingTimeoutRef.current = setTimeout(async () => {
      setIsSaving(true);

      const result = await onSave(empresa, dateKey, shouldSaveValue ? numValue : null);

      if (result.success) {
        setSavedFields(prev => {
          const next = new Set(prev);
          if (shouldSaveValue) {
            next.add(key);
          } else {
            next.delete(key);
          }
          return next;
        });
      } else {
        console.error('Error saving:', result.error);
      }

      setTimeout(() => setIsSaving(false), 500);
    }, 500);
  }, [onSave]);

  const handleFocus = useCallback((key: string, value: number | null) => {
    setFocusedField(key);
    setEditingKey(key);
    setEditingValue(value !== null ? formatBRL(value).replace('R$', '').trim() : '');
  }, []);

  const handleBlur = useCallback(() => {
    setFocusedField(null);
    setEditingKey(null);
    setEditingValue('');
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent, empresa: string, dateKey: string) => {
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();

      // Build flat list of all inputs
      const allInputs: { empresa: string; dateKey: string }[] = [];
      groups.forEach(([, entries]) => {
        entries.forEach(entry => {
          daysToShow.forEach(day => {
            allInputs.push({ empresa: entry.empresa, dateKey: day.date.toISOString().split('T')[0] });
          });
        });
      });

      const currentKey = `${empresa}:${dateKey}`;
      const currentIndex = allInputs.findIndex(i => `${i.empresa}:${i.dateKey}` === currentKey);

      // Before moving, ensure we format the current value correctly if it's being edited
      if (editingKey === currentKey) {
        // The blur handler will clear editingKey, but we might want to force a save/format here
      }

      const nextIndex = e.shiftKey ? currentIndex - 1 : currentIndex + 1;

      if (nextIndex >= 0 && nextIndex < allInputs.length) {
        const next = allInputs[nextIndex];
        const nextKey = `${next.empresa}:${next.dateKey}`;
        const nextInput = inputRefs.current.get(nextKey);
        nextInput?.focus();
        nextInput?.select();
      }
    }
  }, [groups, daysToShow, editingKey]);

  // Calculate week total
  const weekTotal = dayStats.reduce((sum, d) => sum + d.total, 0);
  const weekGoal = dayStats.reduce((sum, d) => sum + d.goalTotal, 0);

  const dayCount = daysToShow.length;

  return (
    <div className={styles.container} style={{ '--day-count': dayCount } as React.CSSProperties}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerTop}>
          <div className={styles.headerTitle}>
            <h2 className={styles.title}>Mês de {monthLabel}</h2>
            <span className={styles.subtitle}>
              {daysToShow.length} {daysToShow.length === 1 ? 'dia' : 'dias'} para preencher
              {isSaving && <span className={styles.savingIndicator}> • Salvando...</span>}
            </span>
          </div>
          <div className={styles.weekSummary}>
            <span className={styles.weekTotal}>{formatBRL(weekTotal)}</span>
            <span className={styles.weekGoal}>
              de {formatCompact(weekGoal)} ({weekGoal > 0 ? Math.round((weekTotal / weekGoal) * 100) : 0}%)
            </span>
          </div>
        </div>

        {/* Date range filter */}
        <div className={styles.dateFilter}>
          <div className={styles.dateNavigation}>
            <button
              type="button"
              className={styles.navButton}
              onClick={() => navigateMonth('prev')}
              title="Mês anterior"
            >
              <ChevronLeft size={18} />
            </button>
            <div className={styles.monthPicker}>
              <Calendar size={16} className={styles.calendarIcon} />
              <input
                type="month"
                className={styles.monthInput}
                value={toMonthInputValue(selectedMonth)}
                onChange={handleMonthChange}
                max={toMonthInputValue(today)}
              />
            </div>
            <button
              type="button"
              className={styles.navButton}
              onClick={() => navigateMonth('next')}
              title="Próximo mês"
              disabled={selectedMonth.getFullYear() === today.getFullYear() && selectedMonth.getMonth() === today.getMonth()}
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>

      </header>

      {/* Scrollable content */}
      <div className={styles.scrollWrapper}>
        {/* Days header */}
        <div className={styles.daysHeader}>
          <div className={styles.daysHeaderLabel}>Linha</div>
          {daysToShow.map((day, i) => {
            const stats = dayStats[i];
            return (
              <div key={day.date.toISOString()} className={styles.dayHeader}>
                <span className={styles.dayName}>{day.label}</span>
                <span className={styles.dayDate}>
                  {day.date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}
                </span>
                <div className={styles.dayProgress}>
                  <div
                    className={styles.dayProgressBar}
                    style={{ width: `${stats.percentFilled}%` }}
                  />
                </div>
                <span className={styles.dayStats}>
                  {formatCompact(stats.total)}
                </span>
              </div>
            );
          })}
        </div>

        {/* Entry groups */}
        <div className={styles.groups}>
          {groups.map(([grupo, entries]) => (
            <div key={grupo} className={styles.group}>
              <div className={styles.groupHeader}>
                <span className={styles.groupName}>{grupo}</span>
              </div>

              <div className={styles.entriesTable}>
                {entries.map((entry) => (
                  <div key={entry.empresa} className={styles.entryRow}>
                    <div className={styles.entryInfo}>
                      <span className={styles.entryName} title={entry.empresa}>{entry.empresa}</span>
                      {entry.segmento === 'AR CONDICIONADO' ? (
                        <span className={styles.entryGoal}>
                          meta útil: {formatBRL(entry.weekdayGoal)} / meta fds: {formatBRL(entry.weekendGoal)}
                        </span>
                      ) : (
                        <span className={styles.entryGoal}>meta: {formatBRL(entry.baseDailyGoal)}</span>
                      )}
                    </div>

                    {daysToShow.map((day) => {
                      const dateKey = day.date.toISOString().split('T')[0];
                      const key = `${entry.empresa}:${dateKey}`;
                      const value = values.get(key) ?? null;
                      const isFocused = focusedField === key;
                      const isSaved = savedFields.has(key);
                      const hasValue = value !== null;
                      const adjustedGoal = getCompanyAdjustedDailyGoal(entry, day.date);

                      // Using a small epsilon or rounding for currency comparison to avoid float issues
                      const isAboveGoal = hasValue && (Math.round(value * 100) / 100) >= (Math.round(adjustedGoal * 100) / 100);
                      const isBelowGoal = hasValue && (Math.round(value * 100) / 100) < (Math.round(adjustedGoal * 100) / 100);

                      const displayValue = editingKey === key
                        ? editingValue
                        : value !== null
                          ? formatBRL(value).replace('R$', '').trim()
                          : '';

                      return (
                        <div
                          key={dateKey}
                          className={`${styles.entryCell} ${isFocused ? styles.focused : ''} ${hasValue ? styles.filled : ''} ${isBelowGoal ? styles.belowGoal : ''}`}
                        >
                          <input
                            ref={(el) => {
                              if (el) inputRefs.current.set(key, el);
                            }}
                            type="text"
                            inputMode="decimal"
                            className={styles.input}
                            value={displayValue}
                            placeholder="0"
                            onChange={(e) => handleValueChange(entry.empresa, dateKey, e.target.value)}
                            onFocus={() => handleFocus(key, value)}
                            onBlur={handleBlur}
                            onKeyDown={(e) => handleKeyDown(e, entry.empresa, dateKey)}
                          />
                          <div className={styles.cellStatus}>
                            {isSaved && hasValue && <Check size={12} className={styles.savedIcon} />}
                            {isAboveGoal && <span className={styles.aboveGoal}>+</span>}
                            {isBelowGoal && <AlertCircle size={12} className={styles.warningIcon} />}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <footer className={styles.footer}>
        <span className={styles.keyHint}>
          <kbd>Tab</kbd> / <kbd>Enter</kbd> próximo
        </span>
        <span className={styles.keyHint}>
          <kbd>Shift+Tab</kbd> anterior
        </span>
      </footer>
    </div>
  );
}
