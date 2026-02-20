import { useState, useEffect, useCallback, useMemo } from 'react';
import { supabase } from '../lib/supabase';
import { DEFAULT_YEARLY_GOALS, toMonthlyGoals, type CompanyYearlyGoal, type CompanyGoal } from '../data/goals';

export function useGoals() {
  const [yearlyGoals, setYearlyGoals] = useState<CompanyYearlyGoal[]>(DEFAULT_YEARLY_GOALS);

  // Load goals from Supabase on mount
  useEffect(() => {
    const year = new Date().getFullYear();
    const loadGoals = async () => {
      try {
        const { data, error } = await supabase
          .from('goals')
          .select('*')
          .eq('year', year);

        if (error) throw error;

        if (data && data.length > 0) {
          // Convert flat DB rows to CompanyYearlyGoal format
          const goalMap = new Map<string, CompanyYearlyGoal>();
          data.forEach((row: { empresa: string; grupo: string; month: number; valor: number }) => {
            if (!goalMap.has(row.empresa)) {
              goalMap.set(row.empresa, {
                empresa: row.empresa,
                grupo: row.grupo,
                metas: {},
              });
            }
            goalMap.get(row.empresa)!.metas[row.month] = Number(row.valor);
          });

          // Merge with defaults (keep default entries not in DB)
          const dbGoals = Array.from(goalMap.values());
          const dbEmpresas = new Set(dbGoals.map(g => g.empresa));
          const merged = [
            ...dbGoals,
            ...DEFAULT_YEARLY_GOALS.filter(g => !dbEmpresas.has(g.empresa)),
          ];
          setYearlyGoals(merged);
        }
        // If no DB data, keep DEFAULT_YEARLY_GOALS
      } catch (e) {
        console.error('Failed to load goals from Supabase:', e);
        // Fallback to defaults already set
      }
      // loaded
    };

    loadGoals();

    // Subscribe to realtime changes + visibility refetch + polling fallback
    const channel = supabase
      .channel('goals-changes')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'goals' }, () => {
        loadGoals();
      })
      .subscribe();

    const onVisibility = () => {
      if (document.visibilityState === 'visible') loadGoals();
    };
    document.addEventListener('visibilitychange', onVisibility);

    const poll = setInterval(loadGoals, 60_000);

    return () => {
      supabase.removeChannel(channel);
      document.removeEventListener('visibilitychange', onVisibility);
      clearInterval(poll);
    };
  }, []);

  // Default to current month, but can be overridden
  const [selectedMonth, setSelectedMonth] = useState<number>(() => new Date().getMonth() + 1);

  const goals = useMemo((): CompanyGoal[] => {
    return toMonthlyGoals(yearlyGoals, selectedMonth);
  }, [yearlyGoals, selectedMonth]);

  const updateYearlyGoals = useCallback((newGoals: CompanyYearlyGoal[]) => {
    setYearlyGoals(newGoals);
  }, []);

  const updateGoalForMonth = useCallback((empresa: string, month: number, value: number) => {
    setYearlyGoals(prev => prev.map(g => {
      if (g.empresa === empresa) {
        return {
          ...g,
          metas: { ...g.metas, [month]: value }
        };
      }
      return g;
    }));
  }, []);

  const resetGoals = useCallback(() => {
    setYearlyGoals(DEFAULT_YEARLY_GOALS);
  }, []);

  const getCompanyGoal = useCallback((empresa: string, month?: number): number => {
    const m = month ?? selectedMonth;
    const goal = yearlyGoals.find((g) => g.empresa === empresa);
    return goal?.metas[m] ?? 0;
  }, [yearlyGoals, selectedMonth]);

  const getGroupGoal = useCallback((grupo: string, month?: number): number => {
    const m = month ?? selectedMonth;
    return yearlyGoals
      .filter((g) => g.grupo === grupo)
      .reduce((sum, g) => sum + (g.metas[m] ?? 0), 0);
  }, [yearlyGoals, selectedMonth]);

  const totalGoal = useMemo(() => {
    return yearlyGoals.reduce((sum, g) => sum + (g.metas[selectedMonth] ?? 0), 0);
  }, [yearlyGoals, selectedMonth]);

  const totalYearGoal = useMemo(() => {
    return yearlyGoals.reduce((sum, g) => {
      const yearTotal = Object.values(g.metas).reduce((s, v) => s + v, 0);
      return sum + yearTotal;
    }, 0);
  }, [yearlyGoals]);

  const updateGoals = useCallback((newGoals: CompanyGoal[]) => {
    setYearlyGoals(prev => prev.map(g => {
      const updated = newGoals.find(ng => ng.empresa === g.empresa);
      if (updated) {
        return {
          ...g,
          metas: { ...g.metas, [selectedMonth]: updated.metaMensal }
        };
      }
      return g;
    }));
  }, [selectedMonth]);

  return {
    goals,
    yearlyGoals,
    totalGoal,
    totalYearGoal,
    selectedMonth,
    setSelectedMonth,
    updateGoals,
    updateYearlyGoals,
    updateGoalForMonth,
    resetGoals,
    getCompanyGoal,
    getGroupGoal,
  };
}
