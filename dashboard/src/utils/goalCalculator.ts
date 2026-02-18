import { addDays, getDaysInMonth } from 'date-fns';
import type { FaturamentoRecord, Filters, RevenueLine } from '../types';
import type { CompanyYearlyGoal } from '../data/goals';
import { COMPANIES } from '../data/fallbackData';
import {
  buildForecastModel,
  calculateCompanySeasonality,
  type DailyTotal,
  type RawSeasonalityRecord,
} from './projectionEngine';
import {
  getHolidayBucket,
  getPaydayBucket,
  type HolidayBucket,
  type PaydayBucket,
} from './businessCalendar';

export interface CompanyMetaInfo extends CompanyYearlyGoal {
  segmento: string;
}

export interface AdaptiveGoalPlanner {
  monthTarget: number;
  referenceDate: Date;
  getGoalForDate: (date: Date) => number;
  sumGoalsForRange: (start: Date, end: Date) => number;
  buildGoalMapForDates: (dates: Date[]) => Map<string, number>;
}

export interface AdaptiveGoalPlannerOptions {
  catchUpEnabled?: boolean;
}

const HOLIDAY_BUCKETS: HolidayBucket[] = ['normal', 'holiday', 'pre_holiday', 'post_holiday'];
const PAYDAY_BUCKETS: PaydayBucket[] = ['regular', 'salary_window', 'advance_window', 'month_end'];

export function buildCompanyMetaInfo(
  yearlyGoals: CompanyYearlyGoal[],
  lines: RevenueLine[] = COMPANIES
): CompanyMetaInfo[] {
  return yearlyGoals.map((goal) => {
    const companyInfo = lines.find((c) => c.empresa === goal.empresa);
    return {
      ...goal,
      segmento: companyInfo?.segmento || 'OUTROS',
    };
  });
}

export function filterCompaniesByFilters(companies: CompanyMetaInfo[], filters: Filters): CompanyMetaInfo[] {
  return companies.filter((company) => {
    if (filters.empresas.length > 0 && !filters.empresas.includes(company.empresa)) return false;
    if (filters.grupos.length > 0 && !filters.grupos.includes(company.grupo)) return false;
    if (filters.segmentos.length > 0 && !filters.segmentos.includes(company.segmento)) return false;
    return true;
  });
}

function normalizeDate(date: Date): Date {
  const normalized = new Date(date);
  normalized.setHours(12, 0, 0, 0);
  return normalized;
}

function dateKey(date: Date): string {
  return date.toISOString().split('T')[0];
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function isWeekend(date: Date): boolean {
  const day = date.getDay();
  return day === 0 || day === 6;
}

export function getAdjustmentFactor(segmento: string, date: Date): number {
  if (segmento !== 'AR CONDICIONADO') return 1;
  return isWeekend(date) ? 0.5 : 1.2;
}

export function getCompanyDailyBaseGoal(company: CompanyMetaInfo, date: Date): number {
  const month = date.getMonth() + 1;
  const metaMensal = company.metas[month] || 0;
  const daysInMonth = getDaysInMonth(date);
  return daysInMonth > 0 ? metaMensal / daysInMonth : 0;
}

export function getCompanyAdjustedDailyGoal(company: CompanyMetaInfo, date: Date): number {
  const base = getCompanyDailyBaseGoal(company, date);
  return base * getAdjustmentFactor(company.segmento, date);
}

export function getTotalAdjustedDailyGoal(companies: CompanyMetaInfo[], date: Date): number {
  if (companies.length === 0) return 0;
  const normalized = normalizeDate(date);
  return companies.reduce((sum, company) => sum + getCompanyAdjustedDailyGoal(company, normalized), 0);
}

export function getTotalBaseDailyGoal(companies: CompanyMetaInfo[], date: Date): number {
  if (companies.length === 0) return 0;
  const normalized = normalizeDate(date);
  return companies.reduce((sum, company) => sum + getCompanyDailyBaseGoal(company, normalized), 0);
}

export function getTotalMonthlyGoal(companies: CompanyMetaInfo[], month: number): number {
  if (companies.length === 0) return 0;
  return companies.reduce((sum, company) => sum + (company.metas[month] || 0), 0);
}

export function getTotalYearlyGoal(companies: CompanyMetaInfo[]): number {
  if (companies.length === 0) return 0;
  return companies.reduce((sum, company) => {
    const yearly = Object.values(company.metas).reduce((acc, value) => acc + value, 0);
    return sum + yearly;
  }, 0);
}

function aggregateDailyTotals(records: FaturamentoRecord[]): DailyTotal[] {
  const grouped = new Map<string, { date: Date; total: number }>();

  records.forEach((record) => {
    const normalized = normalizeDate(record.data);
    const key = dateKey(normalized);
    const existing = grouped.get(key);
    if (existing) {
      existing.total += record.faturamento;
      return;
    }
    grouped.set(key, { date: normalized, total: record.faturamento });
  });

  return Array.from(grouped.values()).sort((a, b) => a.date.getTime() - b.date.getTime());
}

function daysBetween(a: Date, b: Date): number {
  const ms = a.getTime() - b.getTime();
  return Math.round(ms / (1000 * 60 * 60 * 24));
}

function buildDefaultMultiplierMap<T extends string>(buckets: readonly T[]): Record<T, number> {
  return buckets.reduce((acc, bucket) => {
    acc[bucket] = 1;
    return acc;
  }, {} as Record<T, number>);
}

function learnCategoryMultipliers<T extends string>(
  history: DailyTotal[],
  refDate: Date,
  buckets: readonly T[],
  categoryForDate: (date: Date) => T,
  baselineForDate: (date: Date) => number,
  options: { halfLifeDays: number; priorWeight: number; min: number; max: number }
): Record<T, number> {
  const stats = new Map<T, { weightedResidualSum: number; totalWeight: number }>();
  buckets.forEach((bucket) => {
    stats.set(bucket, { weightedResidualSum: 0, totalWeight: 0 });
  });

  history.forEach((point) => {
    const baseline = baselineForDate(point.date);
    if (baseline <= 0) return;

    const residual = point.total / baseline;
    if (!Number.isFinite(residual) || residual <= 0) return;

    const ageDays = Math.max(0, daysBetween(refDate, point.date));
    const recencyWeight = Math.exp(-ageDays / options.halfLifeDays);
    const category = categoryForDate(point.date);
    const stat = stats.get(category);
    if (!stat) return;
    stat.weightedResidualSum += residual * recencyWeight;
    stat.totalWeight += recencyWeight;
  });

  return buckets.reduce((acc, bucket) => {
    const stat = stats.get(bucket);
    const meanResidual = stat && stat.totalWeight > 0
      ? stat.weightedResidualSum / stat.totalWeight
      : 1;
    const blended = (meanResidual * (stat?.totalWeight || 0) + options.priorWeight) /
      ((stat?.totalWeight || 0) + options.priorWeight);
    acc[bucket] = clamp(blended, options.min, options.max);
    return acc;
  }, buildDefaultMultiplierMap(buckets));
}

export function buildAdaptiveDailyGoalPlanner(
  companies: CompanyMetaInfo[],
  historyRecords: FaturamentoRecord[],
  referenceDate: Date,
  options: AdaptiveGoalPlannerOptions = {}
): AdaptiveGoalPlanner {
  const normalizedRef = normalizeDate(referenceDate);
  const refMonthIndex = normalizedRef.getMonth();
  const refMonth = refMonthIndex + 1;
  const refYear = normalizedRef.getFullYear();
  const catchUpEnabled = options.catchUpEnabled ?? true;

  if (companies.length === 0) {
    return {
      monthTarget: 0,
      referenceDate: normalizedRef,
      getGoalForDate: () => 0,
      sumGoalsForRange: () => 0,
      buildGoalMapForDates: (dates: Date[]) => {
        const map = new Map<string, number>();
        dates.forEach((date) => {
          map.set(dateKey(normalizeDate(date)), 0);
        });
        return map;
      },
    };
  }

  const companySet = new Set(companies.map((company) => company.empresa));
  const filteredHistory = historyRecords.filter((record) => companySet.has(record.empresa));
  const dailyHistory = aggregateDailyTotals(filteredHistory).filter((point) => point.date <= normalizedRef);
  const monthTarget = getTotalMonthlyGoal(companies, refMonth);
  const daysInRefMonth = getDaysInMonth(normalizedRef);
  const fallbackDaily = daysInRefMonth > 0 ? monthTarget / daysInRefMonth : 0;

  const rawSeasonality: RawSeasonalityRecord[] = filteredHistory.map((record) => ({
    date: normalizeDate(record.data),
    value: record.faturamento,
    empresa: record.empresa,
    grupo: record.grupo,
  }));
  const seasonality = calculateCompanySeasonality(rawSeasonality).total;
  const forecastModel = buildForecastModel(dailyHistory, seasonality, normalizedRef, fallbackDaily);

  const baselineForDate = (date: Date) => forecastModel.forecastForDate(date).p50;
  const holidayMultipliers = learnCategoryMultipliers(
    dailyHistory,
    normalizedRef,
    HOLIDAY_BUCKETS,
    getHolidayBucket,
    baselineForDate,
    { halfLifeDays: 180, priorWeight: 18, min: 0.65, max: 1.4 }
  );
  const paydayMultipliers = learnCategoryMultipliers(
    dailyHistory,
    normalizedRef,
    PAYDAY_BUCKETS,
    getPaydayBucket,
    baselineForDate,
    { halfLifeDays: 160, priorWeight: 24, min: 0.7, max: 1.35 }
  );

  const monthDates: Date[] = [];
  for (let day = 1; day <= daysInRefMonth; day += 1) {
    monthDates.push(new Date(refYear, refMonthIndex, day, 12, 0, 0));
  }

  const baseStrengthByKey = new Map<string, number>();
  monthDates.forEach((date) => {
    const key = dateKey(date);
    const forecast = baselineForDate(date);
    const holidayFactor = holidayMultipliers[getHolidayBucket(date)] || 1;
    const paydayFactor = paydayMultipliers[getPaydayBucket(date)] || 1;
    const modelStrength = forecast * holidayFactor * paydayFactor;
    const configuredGoal = getTotalAdjustedDailyGoal(companies, date);
    const blendedStrength = modelStrength > 0
      ? (modelStrength * 0.75) + (configuredGoal * 0.25)
      : configuredGoal;
    const floor = configuredGoal > 0
      ? configuredGoal * 0.2
      : Math.max(fallbackDaily * 0.2, 1);
    baseStrengthByKey.set(key, Math.max(blendedStrength, floor));
  });

  const totalBaseStrength = monthDates.reduce((sum, date) => {
    return sum + (baseStrengthByKey.get(dateKey(date)) || 0);
  }, 0);

  const baselineGoalByKey = new Map<string, number>();
  if (monthTarget > 0 && totalBaseStrength > 0) {
    monthDates.forEach((date) => {
      const key = dateKey(date);
      const score = baseStrengthByKey.get(key) || 0;
      baselineGoalByKey.set(key, (monthTarget * score) / totalBaseStrength);
    });
  } else {
    monthDates.forEach((date) => baselineGoalByKey.set(dateKey(date), 0));
  }

  const monthStart = new Date(refYear, refMonthIndex, 1, 12, 0, 0);
  const realizedToDate = filteredHistory.reduce((sum, record) => {
    const date = normalizeDate(record.data);
    if (date < monthStart || date > normalizedRef) return sum;
    return sum + record.faturamento;
  }, 0);

  const adaptiveGoalByKey = new Map<string, number>();
  if (monthTarget <= 0) {
    monthDates.forEach((date) => adaptiveGoalByKey.set(dateKey(date), 0));
  } else if (!catchUpEnabled) {
    monthDates.forEach((date) => {
      const key = dateKey(date);
      adaptiveGoalByKey.set(key, baselineGoalByKey.get(key) || 0);
    });
  } else {
    const catchUpDates = monthDates.filter((date) => date >= normalizedRef);
    const pastDates = monthDates.filter((date) => date < normalizedRef);
    const remainingTarget = Math.max(monthTarget - realizedToDate, 0);

    pastDates.forEach((date) => {
      const key = dateKey(date);
      adaptiveGoalByKey.set(key, baselineGoalByKey.get(key) || 0);
    });

    if (catchUpDates.length === 0 || remainingTarget === 0) {
      catchUpDates.forEach((date) => adaptiveGoalByKey.set(dateKey(date), 0));
    } else {
      const catchUpStrengthTotal = catchUpDates.reduce((sum, date) => {
        return sum + (baseStrengthByKey.get(dateKey(date)) || 0);
      }, 0);

      if (catchUpStrengthTotal > 0) {
        catchUpDates.forEach((date) => {
          const key = dateKey(date);
          const strength = baseStrengthByKey.get(key) || 0;
          adaptiveGoalByKey.set(key, (remainingTarget * strength) / catchUpStrengthTotal);
        });
      } else {
        const uniform = remainingTarget / catchUpDates.length;
        catchUpDates.forEach((date) => adaptiveGoalByKey.set(dateKey(date), uniform));
      }
    }
  }

  const isReferenceMonth = (date: Date): boolean => {
    return date.getFullYear() === refYear && date.getMonth() === refMonthIndex;
  };

  const getGoalForDate = (date: Date): number => {
    const normalized = normalizeDate(date);
    if (isReferenceMonth(normalized)) {
      return adaptiveGoalByKey.get(dateKey(normalized)) || 0;
    }
    return getTotalAdjustedDailyGoal(companies, normalized);
  };

  const sumGoalsForRange = (start: Date, end: Date): number => {
    const startDate = normalizeDate(start);
    const endDate = normalizeDate(end);
    if (startDate > endDate) return 0;

    let total = 0;
    let current = startDate;
    while (current <= endDate) {
      total += getGoalForDate(current);
      current = addDays(current, 1);
    }
    return total;
  };

  const buildGoalMapForDates = (dates: Date[]): Map<string, number> => {
    const map = new Map<string, number>();
    dates.forEach((date) => {
      const normalized = normalizeDate(date);
      map.set(dateKey(normalized), getGoalForDate(normalized));
    });
    return map;
  };

  return {
    monthTarget,
    referenceDate: normalizedRef,
    getGoalForDate,
    sumGoalsForRange,
    buildGoalMapForDates,
  };
}

export function sumAdjustedDailyGoalsForRange(
  companies: CompanyMetaInfo[],
  start: Date,
  end: Date
): number {
  if (companies.length === 0) return 0;
  const startDate = normalizeDate(start);
  const endDate = normalizeDate(end);
  if (startDate > endDate) return 0;

  let total = 0;
  let current = startDate;
  while (current <= endDate) {
    total += getTotalAdjustedDailyGoal(companies, current);
    current = addDays(current, 1);
  }
  return total;
}

export function buildDailyGoalMap(companies: CompanyMetaInfo[], dates: Date[]): Map<string, number> {
  const map = new Map<string, number>();
  dates.forEach((date) => {
    const normalized = normalizeDate(date);
    const key = normalized.toISOString().split('T')[0];
    map.set(key, getTotalAdjustedDailyGoal(companies, normalized));
  });
  return map;
}

export function getCompanyAdjustedDailyGoalForDate(
  companies: CompanyMetaInfo[],
  empresa: string,
  date: Date
): number {
  const company = companies.find((c) => c.empresa === empresa);
  if (!company) return 0;
  return getCompanyAdjustedDailyGoal(company, date);
}
