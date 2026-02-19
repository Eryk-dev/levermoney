import { useState, useEffect, useCallback, useMemo } from 'react';
import { supabase } from '../lib/supabase';
import type { CompanyYearlyGoal } from '../data/goals';
import type { RevenueLine } from '../types';
import { COMPANIES } from '../data/fallbackData';

function normalizeLine(line: RevenueLine): RevenueLine {
  return {
    empresa: line.empresa.trim(),
    grupo: line.grupo.trim(),
    segmento: line.segmento.trim(),
  };
}

function dedupeLines(lines: RevenueLine[]): RevenueLine[] {
  const map = new Map<string, RevenueLine>();
  lines.forEach((line) => {
    if (!line.empresa) return;
    map.set(line.empresa, line);
  });
  return Array.from(map.values());
}

export function useRevenueLines(yearlyGoals: CompanyYearlyGoal[]) {
  const [lines, setLines] = useState<RevenueLine[]>(
    COMPANIES.map((line) => ({ ...line }))
  );

  // Load revenue lines from Supabase on mount
  useEffect(() => {
    const loadLines = async () => {
      try {
        const { data, error } = await supabase
          .from('revenue_lines')
          .select('empresa, grupo, segmento')
          .eq('active', true);

        if (error) throw error;

        if (data && data.length > 0) {
          setLines(dedupeLines(data.map(normalizeLine)));
        }
        // If no DB data, keep COMPANIES fallback
      } catch (e) {
        console.error('Failed to load revenue lines from Supabase:', e);
      }
    };

    loadLines();

    // Subscribe to realtime changes
    const channel = supabase
      .channel('revenue-lines-changes')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'revenue_lines' }, () => {
        loadLines();
      })
      .subscribe();

    // Refetch when tab becomes visible
    const onVisibility = () => {
      if (document.visibilityState === 'visible') loadLines();
    };
    document.addEventListener('visibilitychange', onVisibility);

    // Polling fallback every 60s
    const poll = setInterval(loadLines, 60_000);

    return () => {
      supabase.removeChannel(channel);
      document.removeEventListener('visibilitychange', onVisibility);
      clearInterval(poll);
    };
  }, []);

  // Ensure any goal entries exist in the line list
  useEffect(() => {
    if (yearlyGoals.length === 0) return;
    setLines((prev) => {
      const map = new Map(prev.map((line) => [line.empresa, line]));
      yearlyGoals.forEach((goal) => {
        if (!map.has(goal.empresa)) {
          map.set(goal.empresa, {
            empresa: goal.empresa,
            grupo: goal.grupo || 'OUTROS',
            segmento: 'OUTROS',
          });
        }
      });
      return Array.from(map.values());
    });
  }, [yearlyGoals]);

  const addLine = useCallback((line: RevenueLine) => {
    const normalized = normalizeLine(line);
    if (!normalized.empresa) return;
    setLines((prev) => {
      if (prev.some((l) => l.empresa === normalized.empresa)) return prev;
      return [...prev, normalized].sort((a, b) => a.empresa.localeCompare(b.empresa));
    });
  }, []);

  const updateLine = useCallback((empresa: string, updates: Partial<RevenueLine>) => {
    setLines((prev) =>
      prev.map((line) =>
        line.empresa === empresa
          ? normalizeLine({ ...line, ...updates } as RevenueLine)
          : line
      )
    );
  }, []);

  const removeLine = useCallback((empresa: string) => {
    setLines((prev) => prev.filter((line) => line.empresa !== empresa));
  }, []);

  const lineMap = useMemo(() => new Map(lines.map((line) => [line.empresa, line])), [lines]);

  return {
    lines,
    lineMap,
    addLine,
    updateLine,
    removeLine,
    setLines,
  };
}
