import { useState, useMemo, useCallback, useEffect } from 'react';
import { useSupabaseFaturamento } from './hooks/useSupabaseFaturamento';
import { useFilters } from './hooks/useFilters';
import { useGoals } from './hooks/useGoals';
import { useRevenueLines } from './hooks/useRevenueLines';
import { formatBRL, formatPercent } from './utils/dataParser';
import { ViewToggle, type ViewType } from './components/ViewToggle';
import { MultiSelect } from './components/MultiSelect';
import { DatePicker } from './components/DatePicker';
import { MonthSelector } from './components/MonthSelector';
import { PeriodNavigator } from './components/PeriodNavigator';
import { KPICard } from './components/KPICard';
import { GoalsDashboard } from './components/GoalsDashboard';
import { GoalSummary } from './components/GoalSummary';
import { GoalEditor } from './components/GoalEditor';
import { RevenueChart } from './components/RevenueChart';
import { GroupStackedBars } from './components/GroupStackedBars';
import { ComparisonToggle } from './components/ComparisonToggle';
import { DataEntry } from './components/DataEntry';
import { SharePieChart } from './components/SharePieChart';
import { RevenueLinesManager } from './components/RevenueLinesManager';
import { TodayCompanyPerformance } from './components/TodayCompanyPerformance';
import { AdminPanel } from './components/AdminPanel';
import { AdminLogin } from './components/AdminLogin';
import { useIsMobile } from './hooks/useIsMobile';
import { useAdmin } from './hooks/useAdmin';
import { RotateCcw, Settings2, Lock, SlidersHorizontal, X, ChevronDown, ChevronUp } from 'lucide-react';
import logo from './assets/logo.svg';
import styles from './App.module.css';

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

interface ActiveFilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

function App() {
  const [currentView, setCurrentView] = useState<ViewType>(() => {
    try {
      const raw = localStorage.getItem('dashboard-current-view');
      if (raw === 'geral' || raw === 'metas' || raw === 'entrada' || raw === 'linhas') return raw;
    } catch { /* ignore */ }
    return 'geral';
  });
  const [showGoalEditor, setShowGoalEditor] = useState(false);
  const [pieMode, setPieMode] = useState<'segmento' | 'grupo' | 'empresa'>(() => {
    try {
      const raw = localStorage.getItem('dashboard-pie-mode');
      if (raw === 'segmento' || raw === 'grupo' || raw === 'empresa') return raw;
    } catch { /* ignore */ }
    return 'segmento';
  });
  const isMobile = useIsMobile();
  const [installPromptEvent, setInstallPromptEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [showIosHint, setShowIosHint] = useState(false);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [showGeneralAdvancedFilters, setShowGeneralAdvancedFilters] = useState(false);
  const [showMetasAdvancedFilters, setShowMetasAdvancedFilters] = useState(false);

  const admin = useAdmin();

  const [metasMonth, setMetasMonth] = useState(() => new Date().getMonth() + 1);
  const [metasYear, setMetasYear] = useState(() => new Date().getFullYear());

  const { yearlyGoals, updateYearlyGoals, setSelectedMonth } = useGoals();
  const { lines, addLine, updateLine, removeLine } = useRevenueLines(yearlyGoals);

  // All data comes from Supabase
  const { data, upsertEntry, deleteEntry } = useSupabaseFaturamento({ includeZero: true, lines });

  const {
    filters,
    options,
    kpis,
    goalMetrics,
    companyGoalData,
    dailyData,
    comparisonDailyData,
    comparisonLabel,
    comparisonEnabled,
    customComparisonStart,
    customComparisonEnd,
    gapCatchUpEnabled,
    getGoalForDate,
    chartCompanies,
    companyDailyPerformance,
    allGroupsInData,
    groupBreakdown,
    empresaBreakdown,
    segmentPieData,
    datePreset,
    granularity,
    periodLabel,
    canNavigateForward,
    navigatePeriod,
    setGranularity,
    updateFilter,
    toggleFilterValue,
    clearFilters,
    toggleComparison,
    setCustomComparisonRange,
    clearCustomComparison,
    toggleGapCatchUp,
  } = useFilters(data, {
    yearlyGoals,
    setSelectedMonth,
    lines,
    metasMonth: currentView === 'metas' ? metasMonth : undefined,
    metasYear: currentView === 'metas' ? metasYear : undefined,
  });

  useEffect(() => {
    try { localStorage.setItem('dashboard-current-view', currentView); } catch { /* ignore */ }
  }, [currentView]);

  // Reset month override when leaving metas tab
  useEffect(() => {
    if (currentView !== 'metas') {
      setMetasMonth(new Date().getMonth() + 1);
      setMetasYear(new Date().getFullYear());
    }
  }, [currentView]);

  useEffect(() => {
    try { localStorage.setItem('dashboard-pie-mode', pieMode); } catch { /* ignore */ }
  }, [pieMode]);

  const hasEntityFilter =
    filters.empresas.length > 0 ||
    filters.grupos.length > 0 ||
    filters.segmentos.length > 0;
  const isDailyPreset = datePreset === 'today' || datePreset === 'yesterday';
  const dailyLabel = datePreset === 'today' ? 'Hoje' : 'Ontem';

  // Aggregate all data by date for historical projections
  const allHistoricalDailyData = useMemo(() => {
    const filtered = data.filter((record) => {
      if (filters.empresas.length > 0 && !filters.empresas.includes(record.empresa)) return false;
      if (filters.grupos.length > 0 && !filters.grupos.includes(record.grupo)) return false;
      if (filters.segmentos.length > 0 && !filters.segmentos.includes(record.segmento)) return false;
      return true;
    });

    const byDate = new Map<string, { date: Date; total: number }>();
    filtered.forEach((record) => {
      const key = record.data.toISOString().split('T')[0];
      const existing = byDate.get(key);
      if (existing) {
        existing.total += record.faturamento;
      } else {
        byDate.set(key, { date: record.data, total: record.faturamento });
      }
    });
    return Array.from(byDate.values()).sort((a, b) => a.date.getTime() - b.date.getTime());
  }, [data, filters.empresas, filters.grupos, filters.segmentos]);

  // Raw data with empresa/grupo for seasonality calculations
  const rawDataForSeasonality = useMemo(() => {
    return data.map(record => ({
      date: record.data,
      value: record.faturamento,
      empresa: record.empresa,
      grupo: record.grupo,
    }));
  }, [data]);

  const groupPieData = useMemo(() => {
    return groupBreakdown.map((item) => ({
      name: item.grupo,
      value: item.total,
    }));
  }, [groupBreakdown]);

  const empresaPieData = useMemo(() => {
    const limit = 6;
    const top = empresaBreakdown.slice(0, limit);
    const rest = empresaBreakdown.slice(limit);
    const restTotal = rest.reduce((sum, item) => sum + item.total, 0);
    const base = top.map((item) => ({ name: item.empresa, value: item.total }));
    if (restTotal > 0) {
      base.push({ name: 'Outros', value: restTotal });
    }
    return base;
  }, [empresaBreakdown]);

  const pieConfig = useMemo(() => {
    switch (pieMode) {
      case 'grupo':
        return { title: 'Por Grupo', data: groupPieData };
      case 'empresa':
        return { title: 'Por Linha', data: empresaPieData };
      case 'segmento':
      default:
        return { title: 'Por Segmento', data: segmentPieData };
    }
  }, [pieMode, groupPieData, empresaPieData, segmentPieData]);

  const companyRankingData = useMemo(() => {
    if (isDailyPreset) {
      return [...companyDailyPerformance].sort((a, b) => b.realizado - a.realizado);
    }

    // Apply entity filters to companyGoalData
    const entityFiltered = companyGoalData.filter((item) => {
      if (filters.empresas.length > 0 && !filters.empresas.includes(item.empresa)) return false;
      if (filters.grupos.length > 0 && !filters.grupos.includes(item.grupo)) return false;
      if (filters.segmentos.length > 0 && !filters.segmentos.includes(item.segmento)) return false;
      return true;
    });

    return entityFiltered
      .map((item) => {
        const meta = item.metaProporcional > 0 ? item.metaProporcional : item.metaMensal;
        const gap = item.realizado - meta;
        const percentualMeta = meta > 0 ? (item.realizado / meta) * 100 : 0;
        return {
          empresa: item.empresa,
          grupo: item.grupo,
          segmento: item.segmento,
          realizado: item.realizado,
          meta,
          gap,
          percentualMeta,
        };
      })
      .filter((item) => item.realizado > 0 || item.meta > 0)
      .sort((a, b) => b.realizado - a.realizado);
  }, [isDailyPreset, companyDailyPerformance, companyGoalData, filters]);

  const groupRankingData = useMemo(() => {
    const grouped = new Map<string, { realizado: number; meta: number }>();
    companyRankingData.forEach((item) => {
      const current = grouped.get(item.grupo) || { realizado: 0, meta: 0 };
      current.realizado += item.realizado;
      current.meta += item.meta;
      grouped.set(item.grupo, current);
    });

    return Array.from(grouped.entries())
      .map(([grupo, totals]) => {
        const percentualMeta = totals.meta > 0 ? (totals.realizado / totals.meta) * 100 : 0;
        return {
          empresa: grupo,
          grupo,
          segmento: '',
          realizado: totals.realizado,
          meta: totals.meta,
          gap: totals.realizado - totals.meta,
          percentualMeta,
        };
      })
      .filter((item) => item.realizado > 0 || item.meta > 0)
      .sort((a, b) => b.realizado - a.realizado);
  }, [companyRankingData]);

  const segmentRankingData = useMemo(() => {
    const grouped = new Map<string, { realizado: number; meta: number }>();
    companyRankingData.forEach((item) => {
      const current = grouped.get(item.segmento) || { realizado: 0, meta: 0 };
      current.realizado += item.realizado;
      current.meta += item.meta;
      grouped.set(item.segmento, current);
    });

    return Array.from(grouped.entries())
      .map(([segmento, totals]) => {
        const percentualMeta = totals.meta > 0 ? (totals.realizado / totals.meta) * 100 : 0;
        return {
          empresa: segmento,
          grupo: '',
          segmento,
          realizado: totals.realizado,
          meta: totals.meta,
          gap: totals.realizado - totals.meta,
          percentualMeta,
        };
      })
      .filter((item) => item.realizado > 0 || item.meta > 0)
      .sort((a, b) => b.realizado - a.realizado);
  }, [companyRankingData]);

  const handleAddLine = useCallback((line: { empresa: string; grupo: string; segmento: string }) => {
    addLine(line);
    const metas: Record<number, number> = {};
    for (let m = 1; m <= 12; m += 1) {
      metas[m] = 0;
    }
    const newGoals = !yearlyGoals.some((g) => g.empresa === line.empresa)
      ? [...yearlyGoals, { empresa: line.empresa, grupo: line.grupo, metas }]
      : yearlyGoals;
    updateYearlyGoals(newGoals);
    if (admin.isAuthenticated) {
      admin.createRevenueLine(line);
      admin.saveGoalsBulk(newGoals);
    }
  }, [addLine, updateYearlyGoals, yearlyGoals, admin]);

  const handleUpdateLine = useCallback((empresa: string, updates: { grupo?: string; segmento?: string }) => {
    updateLine(empresa, updates);
    if (updates.grupo) {
      updateYearlyGoals(yearlyGoals.map((g) =>
        g.empresa === empresa ? { ...g, grupo: updates.grupo! } : g
      ));
    }
    if (admin.isAuthenticated) {
      admin.updateRevenueLine(empresa, updates);
    }
  }, [updateLine, updateYearlyGoals, yearlyGoals, admin]);

  const handleRemoveLine = useCallback((empresa: string) => {
    removeLine(empresa);
    updateYearlyGoals(yearlyGoals.filter((g) => g.empresa !== empresa));
    if (admin.isAuthenticated) {
      admin.removeRevenueLine(empresa);
    }
  }, [removeLine, updateYearlyGoals, yearlyGoals, admin]);

  const handleSaveEntry = useCallback(async (empresa: string, date: string, valor: number | null) => {
    if (valor === null) {
      return deleteEntry(empresa, date);
    }
    return upsertEntry(empresa, date, valor);
  }, [deleteEntry, upsertEntry]);

  useEffect(() => {
    const handler = (event: Event) => {
      event.preventDefault();
      setInstallPromptEvent(event as BeforeInstallPromptEvent);
    };
    window.addEventListener('beforeinstallprompt', handler);
    return () => window.removeEventListener('beforeinstallprompt', handler);
  }, []);

  useEffect(() => {
    const onInstalled = () => setInstallPromptEvent(null);
    window.addEventListener('appinstalled', onInstalled);
    return () => window.removeEventListener('appinstalled', onInstalled);
  }, []);

  useEffect(() => {
    const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
      || (navigator as Navigator & { standalone?: boolean }).standalone;
    setShowIosHint(isIos && !isStandalone);
  }, []);

  useEffect(() => {
    if (!isMobile) {
      setMobileFiltersOpen(false);
    }
  }, [isMobile]);

  useEffect(() => {
    setMobileFiltersOpen(false);
  }, [currentView]);

  const handleInstallClick = async () => {
    if (!installPromptEvent) return;
    await installPromptEvent.prompt();
    await installPromptEvent.userChoice;
    setInstallPromptEvent(null);
  };

  const now = new Date();
  const currentMonth = now.getMonth() + 1;
  const currentYear = now.getFullYear();
  const isDefaultMetasReference = metasMonth === currentMonth && metasYear === currentYear;
  const metasReferenceLabel = useMemo(() => (
    new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric' })
      .format(new Date(metasYear, metasMonth - 1, 1))
  ), [metasMonth, metasYear]);

  const clearMetasFilters = useCallback(() => {
    clearFilters();
    setMetasMonth(currentMonth);
    setMetasYear(currentYear);
    if (!gapCatchUpEnabled) {
      toggleGapCatchUp();
    }
  }, [clearFilters, currentMonth, currentYear, gapCatchUpEnabled, toggleGapCatchUp]);

  const clearGeneralFilters = useCallback(() => {
    clearFilters();
    if (!gapCatchUpEnabled) {
      toggleGapCatchUp();
    }
  }, [clearFilters, gapCatchUpEnabled, toggleGapCatchUp]);

  const generalFilterChips = useMemo<ActiveFilterChip[]>(() => {
    const chips: ActiveFilterChip[] = [];
    if (filters.grupos.length > 0) {
      chips.push({
        key: 'grupos',
        label: `Grupo (${filters.grupos.length})`,
        onRemove: () => updateFilter('grupos', []),
      });
    }
    if (filters.segmentos.length > 0) {
      chips.push({
        key: 'segmentos',
        label: `Segmento (${filters.segmentos.length})`,
        onRemove: () => updateFilter('segmentos', []),
      });
    }
    if (filters.empresas.length > 0) {
      chips.push({
        key: 'linhas',
        label: `Linha (${filters.empresas.length})`,
        onRemove: () => updateFilter('empresas', []),
      });
    }
    if (filters.dataInicio || filters.dataFim) {
      chips.push({
        key: 'intervalo',
        label: 'Intervalo personalizado',
        onRemove: () => {
          updateFilter('dataInicio', null);
          updateFilter('dataFim', null);
        },
      });
    }
    if (comparisonEnabled) {
      chips.push({
        key: 'comparacao',
        label: 'Comparação ativa',
        onRemove: toggleComparison,
      });
    }
    if (!gapCatchUpEnabled) {
      chips.push({
        key: 'gap',
        label: 'Meta sem ajuste automático',
        onRemove: toggleGapCatchUp,
      });
    }
    return chips;
  }, [filters, updateFilter, comparisonEnabled, toggleComparison, gapCatchUpEnabled, toggleGapCatchUp]);

  const metasFilterChips = useMemo<ActiveFilterChip[]>(() => {
    const chips: ActiveFilterChip[] = [];
    if (filters.grupos.length > 0) {
      chips.push({
        key: 'grupos',
        label: `Grupo (${filters.grupos.length})`,
        onRemove: () => updateFilter('grupos', []),
      });
    }
    if (filters.segmentos.length > 0) {
      chips.push({
        key: 'segmentos',
        label: `Segmento (${filters.segmentos.length})`,
        onRemove: () => updateFilter('segmentos', []),
      });
    }
    if (filters.empresas.length > 0) {
      chips.push({
        key: 'linhas',
        label: `Linha (${filters.empresas.length})`,
        onRemove: () => updateFilter('empresas', []),
      });
    }
    if (!isDefaultMetasReference) {
      chips.push({
        key: 'mes',
        label: metasReferenceLabel,
        onRemove: () => {
          setMetasMonth(currentMonth);
          setMetasYear(currentYear);
        },
      });
    }
    if (!gapCatchUpEnabled) {
      chips.push({
        key: 'gap',
        label: 'Meta sem ajuste automático',
        onRemove: toggleGapCatchUp,
      });
    }
    return chips;
  }, [
    filters,
    updateFilter,
    isDefaultMetasReference,
    metasReferenceLabel,
    currentMonth,
    currentYear,
    gapCatchUpEnabled,
    toggleGapCatchUp,
  ]);

  const activeFilterChips = currentView === 'metas' ? metasFilterChips : generalFilterChips;
  const activeFilterCount = activeFilterChips.length;
  const hasGeneralFiltersApplied = generalFilterChips.length > 0;
  const hasMetasFiltersApplied = metasFilterChips.length > 0;

  const shouldShowContext = currentView === 'geral' || currentView === 'metas';
  const currentContextTitle = currentView === 'metas' ? 'Painel de Metas' : 'Visão Geral';
  const currentContextSubtitle = currentView === 'metas'
    ? `Referência: ${metasReferenceLabel}`
    : `Período: ${periodLabel}`;

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <img src={logo} alt="Lever Money" className={styles.logo} />
        </div>
        <div className={styles.headerRight}>
          <div className={styles.headerActions}>
            {currentView === 'metas' && (
              <button
                type="button"
                className={styles.editMetasButton}
                onClick={() => setShowGoalEditor(true)}
              >
                <Settings2 size={16} />
                Editar Metas
              </button>
            )}
            {installPromptEvent && (
              <button
                type="button"
                className={styles.installButton}
                onClick={handleInstallClick}
              >
                Instalar
              </button>
            )}
            {showIosHint && !installPromptEvent && (
              <span className={styles.installHint}>No iOS: Compartilhar → Adicionar à Tela</span>
            )}
            {!admin.isAuthenticated && (
              <button
                type="button"
                className={styles.adminLoginBtn}
                onClick={() => setCurrentView('admin')}
                title="Admin"
              >
                <Lock size={14} />
              </button>
            )}
          </div>
          <ViewToggle value={currentView} onChange={setCurrentView} showAdmin={admin.isAuthenticated} />
        </div>
      </header>

      {shouldShowContext && (
        <section className={styles.contextBar}>
          <div className={styles.contextText}>
            <span className={styles.contextTitle}>{currentContextTitle}</span>
            <span className={styles.contextSubtitle}>{currentContextSubtitle}</span>
          </div>
          <div className={styles.contextActions}>
            {currentView === 'geral' && comparisonEnabled && (
              <span className={styles.contextPill}>
                Comparando com {comparisonLabel || 'período anterior'}
              </span>
            )}
            {isMobile && (
              <button
                type="button"
                className={styles.openFiltersButton}
                onClick={() => setMobileFiltersOpen(true)}
              >
                <SlidersHorizontal size={14} />
                Filtros {activeFilterCount > 0 ? `(${activeFilterCount})` : ''}
              </button>
            )}
          </div>
        </section>
      )}

      {activeFilterChips.length > 0 && (
        <section className={styles.activeFiltersBar}>
          {activeFilterChips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              className={styles.activeFilterChip}
              onClick={chip.onRemove}
              title={`Remover filtro: ${chip.label}`}
            >
              <span>{chip.label}</span>
              <X size={12} />
            </button>
          ))}
        </section>
      )}

      {currentView === 'geral' && (
        <>
          {!isMobile && (
            <section className={styles.filtersPanel}>
              <div className={styles.filtersHeader}>
                <div className={styles.filtersHeading}>
                  <span className={styles.filtersTitle}>Filtros</span>
                  <span className={styles.filtersHint}>Use os essenciais para leitura rápida e abra os avançados quando precisar de investigação.</span>
                </div>
                <div className={styles.filtersActionsRow}>
                  <button
                    type="button"
                    className={styles.advancedToggle}
                    onClick={() => setShowGeneralAdvancedFilters((prev) => !prev)}
                  >
                    {showGeneralAdvancedFilters ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    {showGeneralAdvancedFilters ? 'Ocultar avançados' : 'Mostrar avançados'}
                  </button>
                  <button
                    type="button"
                    className={`${styles.clearButton} ${styles.clearButtonText}`}
                    onClick={clearGeneralFilters}
                    title="Limpar todos os filtros"
                    disabled={!hasGeneralFiltersApplied}
                  >
                    <RotateCcw size={14} />
                    Limpar filtros
                  </button>
                </div>
              </div>

              <div className={styles.filtersMainRow}>
                <PeriodNavigator
                  granularity={granularity}
                  periodLabel={periodLabel}
                  canNavigateForward={canNavigateForward}
                  onGranularityChange={setGranularity}
                  onNavigate={navigatePeriod}
                />
                <MultiSelect
                  label="Grupo"
                  values={filters.grupos}
                  options={options.grupos}
                  onChange={(v) => toggleFilterValue('grupos', v)}
                  onClear={() => updateFilter('grupos', [])}
                />
                <MultiSelect
                  label="Segmento"
                  values={filters.segmentos}
                  options={options.segmentos}
                  onChange={(v) => toggleFilterValue('segmentos', v)}
                  onClear={() => updateFilter('segmentos', [])}
                />
                <MultiSelect
                  label="Linha"
                  values={filters.empresas}
                  options={options.empresas}
                  onChange={(v) => toggleFilterValue('empresas', v)}
                  onClear={() => updateFilter('empresas', [])}
                />
              </div>

              {showGeneralAdvancedFilters && (
                <div className={styles.filtersAdvancedRow}>
                  <button
                    type="button"
                    className={`${styles.catchUpToggle} ${gapCatchUpEnabled ? styles.catchUpOn : styles.catchUpOff}`}
                    onClick={toggleGapCatchUp}
                    title="Recalcular metas futuras para suprir (ou não) o gap acumulado"
                  >
                    Meta {gapCatchUpEnabled ? 'com ajuste automático' : 'sem ajuste automático'}
                  </button>
                  <DatePicker
                    label="De"
                    value={filters.dataInicio}
                    onChange={(v) => updateFilter('dataInicio', v)}
                    min={options.minDate}
                    max={filters.dataFim || options.maxDate}
                  />
                  <DatePicker
                    label="Até"
                    value={filters.dataFim}
                    onChange={(v) => updateFilter('dataFim', v)}
                    min={filters.dataInicio || options.minDate}
                    max={options.maxDate}
                  />
                  <ComparisonToggle
                    enabled={comparisonEnabled}
                    onToggle={toggleComparison}
                    customStart={customComparisonStart}
                    customEnd={customComparisonEnd}
                    onCustomRangeChange={setCustomComparisonRange}
                    onClearCustom={clearCustomComparison}
                    comparisonLabel={comparisonLabel}
                    minDate={options.minDate}
                    maxDate={options.maxDate}
                  />
                </div>
              )}
            </section>
          )}

          <section className={styles.kpiRow}>
            <div className={styles.kpiCards}>
              <KPICard
                label="Faturamento"
                value={formatBRL(kpis.faturamentoFiltrado)}
              />
              <div className={styles.kpiDivider} />
              <KPICard
                label="% do Total"
                value={formatPercent(hasEntityFilter ? kpis.percentualDoTotal : 100)}
                sublabel={hasEntityFilter ? `de ${formatBRL(kpis.faturamentoTotal)}` : undefined}
              />
            </div>
            <GoalSummary
              realizado={goalMetrics.realizado}
              realizadoMes={goalMetrics.realizadoMes}
              meta={goalMetrics.metaMensal}
              metaProporcional={goalMetrics.metaProporcional}
              diaAtual={goalMetrics.diaAtual}
              datePreset={datePreset}
              metaSemana={goalMetrics.metaSemana}
              realizadoSemana={goalMetrics.realizadoSemana}
              diasNaSemana={goalMetrics.diasNaSemana}
              esperadoSemanal={goalMetrics.esperadoSemanal}
              metaDia={goalMetrics.metaDia}
              metaDiaAjustada={goalMetrics.metaDiaAjustada}
              realizadoDia={goalMetrics.realizadoDia}
              metaAno={goalMetrics.metaAno}
              realizadoAno={goalMetrics.realizadoAno}
              mesAtual={goalMetrics.mesAtual}
              isArCondicionado={goalMetrics.isArCondicionado}
            />
          </section>

          <section className={`${styles.chartRow} ${isDailyPreset ? styles.chartRowToday : ''}`}>
            {isDailyPreset ? (
              <div className={styles.dailyRankingsGrid}>
                <TodayCompanyPerformance
                  data={companyRankingData}
                  title={`Por Linha (${dailyLabel})`}
                  limit={8}
                  countLabel="linhas"
                  emptyMessage={`Sem dados para ${dailyLabel.toLowerCase()}.`}
                />
                <TodayCompanyPerformance
                  data={groupRankingData}
                  title={`Por Grupo (${dailyLabel})`}
                  limit={8}
                  countLabel="grupos"
                  emptyMessage={`Sem dados para ${dailyLabel.toLowerCase()}.`}
                />
                <TodayCompanyPerformance
                  data={segmentRankingData}
                  title={`Por Segmento (${dailyLabel})`}
                  limit={8}
                  countLabel="segmentos"
                  emptyMessage={`Sem dados para ${dailyLabel.toLowerCase()}.`}
                />
              </div>
            ) : (
              <div className={styles.mainChart}>
                <RevenueChart
                  data={dailyData}
                  companies={chartCompanies}
                  comparisonData={comparisonDailyData}
                  comparisonLabel={comparisonLabel}
                />
              </div>
            )}
            <div className={styles.sideChart}>
              <div className={styles.pieSwitcher}>
                <div className={styles.pieTabs}>
                  <button
                    type="button"
                    className={`${styles.pieTab} ${pieMode === 'segmento' ? styles.pieTabActive : ''}`}
                    onClick={() => setPieMode('segmento')}
                  >
                    Segmento
                  </button>
                  <button
                    type="button"
                    className={`${styles.pieTab} ${pieMode === 'grupo' ? styles.pieTabActive : ''}`}
                    onClick={() => setPieMode('grupo')}
                  >
                    Grupo
                  </button>
                  <button
                    type="button"
                    className={`${styles.pieTab} ${pieMode === 'empresa' ? styles.pieTabActive : ''}`}
                    onClick={() => setPieMode('empresa')}
                  >
                    Linha
                  </button>
                </div>
                <SharePieChart
                  title={pieConfig.title}
                  data={pieConfig.data}
                  showLegend
                />
              </div>
            </div>
          </section>

          {!isDailyPreset && (
            <section className={styles.fullWidthChart}>
              <GroupStackedBars
                data={dailyData}
                groups={allGroupsInData}
                title="Contribuição por Grupo"
                comparisonData={comparisonDailyData}
                comparisonLabel={comparisonLabel}
              />
            </section>
          )}

          {!isDailyPreset && (
            <section className={styles.grid}>
              <div className={styles.gridItem}>
                <TodayCompanyPerformance
                  data={companyRankingData}
                  title="Por Linha"
                  limit={8}
                  countLabel="linhas"
                  emptyMessage="Sem dados no período."
                />
              </div>
              <div className={styles.gridItem}>
                <TodayCompanyPerformance
                  data={groupRankingData}
                  title="Por Grupo"
                  limit={8}
                  countLabel="grupos"
                  emptyMessage="Sem dados no período."
                />
              </div>
              <div className={styles.gridItem}>
                <TodayCompanyPerformance
                  data={segmentRankingData}
                  title="Por Segmento"
                  limit={8}
                  countLabel="segmentos"
                  emptyMessage="Sem dados no período."
                />
              </div>
            </section>
          )}
        </>
      )}

      {currentView === 'metas' && (
        <>
          {!isMobile && (
            <section className={styles.filtersPanel}>
              <div className={styles.filtersHeader}>
                <div className={styles.filtersHeading}>
                  <span className={styles.filtersTitle}>Filtros de metas</span>
                  <span className={styles.filtersHint}>Selecione o mês de referência e recortes de análise antes de entrar no detalhamento.</span>
                </div>
                <div className={styles.filtersActionsRow}>
                  <button
                    type="button"
                    className={styles.advancedToggle}
                    onClick={() => setShowMetasAdvancedFilters((prev) => !prev)}
                  >
                    {showMetasAdvancedFilters ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    {showMetasAdvancedFilters ? 'Ocultar avançados' : 'Mostrar avançados'}
                  </button>
                  <button
                    type="button"
                    className={`${styles.clearButton} ${styles.clearButtonText}`}
                    onClick={clearMetasFilters}
                    title="Limpar todos os filtros"
                    disabled={!hasMetasFiltersApplied}
                  >
                    <RotateCcw size={14} />
                    Limpar filtros
                  </button>
                </div>
              </div>

              <div className={styles.filtersMainRow}>
                <MonthSelector
                  month={metasMonth}
                  year={metasYear}
                  onChange={(m, y) => { setMetasMonth(m); setMetasYear(y); }}
                />
                <MultiSelect
                  label="Grupo"
                  values={filters.grupos}
                  options={options.grupos}
                  onChange={(v) => toggleFilterValue('grupos', v)}
                  onClear={() => updateFilter('grupos', [])}
                />
                <MultiSelect
                  label="Segmento"
                  values={filters.segmentos}
                  options={options.segmentos}
                  onChange={(v) => toggleFilterValue('segmentos', v)}
                  onClear={() => updateFilter('segmentos', [])}
                />
                <MultiSelect
                  label="Linha"
                  values={filters.empresas}
                  options={options.empresas}
                  onChange={(v) => toggleFilterValue('empresas', v)}
                  onClear={() => updateFilter('empresas', [])}
                />
              </div>

              {showMetasAdvancedFilters && (
                <div className={styles.filtersAdvancedRow}>
                  <button
                    type="button"
                    className={`${styles.catchUpToggle} ${gapCatchUpEnabled ? styles.catchUpOn : styles.catchUpOff}`}
                    onClick={toggleGapCatchUp}
                    title="Recalcular metas futuras para suprir (ou não) o gap acumulado"
                  >
                    Meta {gapCatchUpEnabled ? 'com ajuste automático' : 'sem ajuste automático'}
                  </button>
                </div>
              )}
            </section>
          )}

          <GoalsDashboard
            data={companyGoalData}
            totalRealizado={goalMetrics.realizadoMes}
            totalMeta={goalMetrics.metaMensal}
            metaProporcional={goalMetrics.metaProporcional}
            diaAtual={goalMetrics.diaAtual}
            diasNoMes={goalMetrics.diasNoMes}
            coverage={goalMetrics.coverage}
            filters={filters}
            datePreset={datePreset}
            dailyData={dailyData}
            allHistoricalData={allHistoricalDailyData}
            rawDataForSeasonality={rawDataForSeasonality}
            getGoalForDate={getGoalForDate}
            realizadoHoje={goalMetrics.realizadoDia}
            metaHoje={goalMetrics.metaDiaAjustada}
            realizadoSemana={goalMetrics.realizadoSemana}
            metaSemana={goalMetrics.metaSemana}
            esperadoSemanal={goalMetrics.esperadoSemanal}
            realizadoAno={goalMetrics.realizadoAno}
            metaAno={goalMetrics.metaAno}
            metasMensais={goalMetrics.metasMensais}
            mesAtual={goalMetrics.mesAtual}
          />
        </>
      )}

      {isMobile && mobileFiltersOpen && (currentView === 'geral' || currentView === 'metas') && (
        <div className={styles.mobileFiltersOverlay} onClick={() => setMobileFiltersOpen(false)}>
          <section className={styles.mobileFiltersSheet} onClick={(e) => e.stopPropagation()}>
            <header className={styles.mobileFiltersHeader}>
              <div className={styles.mobileFiltersTitleBlock}>
                <span className={styles.mobileFiltersTitle}>
                  {currentView === 'metas' ? 'Filtros de Metas' : 'Filtros da Visão Geral'}
                </span>
                <span className={styles.mobileFiltersSubtitle}>
                  {activeFilterCount > 0 ? `${activeFilterCount} filtro(s) ativo(s)` : 'Sem filtros ativos'}
                </span>
              </div>
              <button
                type="button"
                className={styles.mobileCloseButton}
                onClick={() => setMobileFiltersOpen(false)}
              >
                <X size={16} />
              </button>
            </header>

            <div className={styles.mobileFiltersContent}>
              {currentView === 'geral' ? (
                <>
                  <div className={styles.mobileFilterGroup}>
                    <span className={styles.mobileFilterGroupTitle}>Essenciais</span>
                    <PeriodNavigator
                      granularity={granularity}
                      periodLabel={periodLabel}
                      canNavigateForward={canNavigateForward}
                      onGranularityChange={setGranularity}
                      onNavigate={navigatePeriod}
                    />
                    <MultiSelect
                      label="Grupo"
                      values={filters.grupos}
                      options={options.grupos}
                      onChange={(v) => toggleFilterValue('grupos', v)}
                      onClear={() => updateFilter('grupos', [])}
                      native
                      nativeMode="sheet"
                    />
                    <MultiSelect
                      label="Segmento"
                      values={filters.segmentos}
                      options={options.segmentos}
                      onChange={(v) => toggleFilterValue('segmentos', v)}
                      onClear={() => updateFilter('segmentos', [])}
                      native
                      nativeMode="sheet"
                    />
                    <MultiSelect
                      label="Linha"
                      values={filters.empresas}
                      options={options.empresas}
                      onChange={(v) => toggleFilterValue('empresas', v)}
                      onClear={() => updateFilter('empresas', [])}
                      native
                      nativeMode="sheet"
                    />
                  </div>

                  <div className={styles.mobileFilterGroup}>
                    <button
                      type="button"
                      className={styles.mobileAdvancedToggle}
                      onClick={() => setShowGeneralAdvancedFilters((prev) => !prev)}
                    >
                      {showGeneralAdvancedFilters ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      {showGeneralAdvancedFilters ? 'Ocultar avançados' : 'Mostrar avançados'}
                    </button>

                    {showGeneralAdvancedFilters && (
                      <div className={styles.mobileAdvancedContent}>
                        <button
                          type="button"
                          className={`${styles.catchUpToggle} ${gapCatchUpEnabled ? styles.catchUpOn : styles.catchUpOff}`}
                          onClick={toggleGapCatchUp}
                          title="Recalcular metas futuras para suprir (ou não) o gap acumulado"
                        >
                          Meta {gapCatchUpEnabled ? 'com ajuste automático' : 'sem ajuste automático'}
                        </button>
                        <DatePicker
                          label="De"
                          value={filters.dataInicio}
                          onChange={(v) => updateFilter('dataInicio', v)}
                          min={options.minDate}
                          max={filters.dataFim || options.maxDate}
                        />
                        <DatePicker
                          label="Até"
                          value={filters.dataFim}
                          onChange={(v) => updateFilter('dataFim', v)}
                          min={filters.dataInicio || options.minDate}
                          max={options.maxDate}
                        />
                        <ComparisonToggle
                          enabled={comparisonEnabled}
                          onToggle={toggleComparison}
                          customStart={customComparisonStart}
                          customEnd={customComparisonEnd}
                          onCustomRangeChange={setCustomComparisonRange}
                          onClearCustom={clearCustomComparison}
                          comparisonLabel={comparisonLabel}
                          minDate={options.minDate}
                          maxDate={options.maxDate}
                        />
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <div className={styles.mobileFilterGroup}>
                    <span className={styles.mobileFilterGroupTitle}>Essenciais</span>
                    <MonthSelector
                      month={metasMonth}
                      year={metasYear}
                      onChange={(m, y) => { setMetasMonth(m); setMetasYear(y); }}
                    />
                    <MultiSelect
                      label="Grupo"
                      values={filters.grupos}
                      options={options.grupos}
                      onChange={(v) => toggleFilterValue('grupos', v)}
                      onClear={() => updateFilter('grupos', [])}
                      native
                      nativeMode="sheet"
                    />
                    <MultiSelect
                      label="Segmento"
                      values={filters.segmentos}
                      options={options.segmentos}
                      onChange={(v) => toggleFilterValue('segmentos', v)}
                      onClear={() => updateFilter('segmentos', [])}
                      native
                      nativeMode="sheet"
                    />
                    <MultiSelect
                      label="Linha"
                      values={filters.empresas}
                      options={options.empresas}
                      onChange={(v) => toggleFilterValue('empresas', v)}
                      onClear={() => updateFilter('empresas', [])}
                      native
                      nativeMode="sheet"
                    />
                  </div>

                  <div className={styles.mobileFilterGroup}>
                    <button
                      type="button"
                      className={styles.mobileAdvancedToggle}
                      onClick={() => setShowMetasAdvancedFilters((prev) => !prev)}
                    >
                      {showMetasAdvancedFilters ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      {showMetasAdvancedFilters ? 'Ocultar avançados' : 'Mostrar avançados'}
                    </button>
                    {showMetasAdvancedFilters && (
                      <div className={styles.mobileAdvancedContent}>
                        <button
                          type="button"
                          className={`${styles.catchUpToggle} ${gapCatchUpEnabled ? styles.catchUpOn : styles.catchUpOff}`}
                          onClick={toggleGapCatchUp}
                          title="Recalcular metas futuras para suprir (ou não) o gap acumulado"
                        >
                          Meta {gapCatchUpEnabled ? 'com ajuste automático' : 'sem ajuste automático'}
                        </button>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            <footer className={styles.mobileFiltersFooter}>
              <button
                type="button"
                className={`${styles.clearButton} ${styles.clearButtonText}`}
                onClick={currentView === 'metas' ? clearMetasFilters : clearGeneralFilters}
                disabled={currentView === 'metas' ? !hasMetasFiltersApplied : !hasGeneralFiltersApplied}
              >
                <RotateCcw size={14} />
                Limpar
              </button>
              <button
                type="button"
                className={styles.mobileApplyButton}
                onClick={() => setMobileFiltersOpen(false)}
              >
                Ver resultados
              </button>
            </footer>
          </section>
        </div>
      )}

      {currentView === 'entrada' && (
        <section className={styles.dataEntry}>
          <DataEntry
            data={data}
            goals={yearlyGoals}
            lines={lines}
            onSave={handleSaveEntry}
          />
        </section>
      )}

      {currentView === 'linhas' && (
        <section className={styles.dataEntry}>
          <RevenueLinesManager
            lines={lines}
            onAdd={handleAddLine}
            onUpdate={handleUpdateLine}
            onRemove={handleRemoveLine}
          />
        </section>
      )}

      {currentView === 'admin' && (
        admin.isAuthenticated ? (
          <AdminPanel
            sellers={admin.sellers}
            pendingSellers={admin.pendingSellers}
            activeSellers={admin.activeSellers}
            approveSeller={admin.approveSeller}
            updateSellerConfig={admin.updateSellerConfig}
            rejectSeller={admin.rejectSeller}
            syncStatus={admin.syncStatus}
            triggerSync={admin.triggerSync}
            caAccounts={admin.caAccounts}
            caCostCenters={admin.caCostCenters}
            revenueLines={lines}
            onLogout={() => {
              admin.logout();
              setCurrentView('geral');
            }}
            getInstallLink={admin.getInstallLink}
            activateSeller={admin.activateSeller}
            upgradeToCA={admin.upgradeToCA}
            getBackfillStatus={admin.getBackfillStatus}
            retryBackfill={admin.retryBackfill}
            loadSellers={admin.loadSellers}
          />
        ) : (
          <AdminLogin onLogin={admin.login} />
        )
      )}

      {showGoalEditor && (
        <GoalEditor
          yearlyGoals={yearlyGoals}
          onSave={(goals) => {
            updateYearlyGoals(goals);
            if (admin.isAuthenticated) {
              admin.saveGoalsBulk(goals);
            }
          }}
          onClose={() => setShowGoalEditor(false)}
        />
      )}
    </div>
  );
}

export default App;
