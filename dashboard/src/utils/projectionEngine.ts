export interface RawSeasonalityRecord {
  date: Date;
  value: number;
  empresa: string;
  grupo: string;
}

export interface SeasonalityFactors {
  weekday: number[]; // Monday-based, length 7
  month: number[]; // Jan=0, length 12
  monthPosition: number[]; // 0..5 bins within month
  sampleSize: number;
}

export interface SeasonalityHierarchy {
  total: SeasonalityFactors;
  groups: Record<string, SeasonalityFactors>;
  companies: Record<string, SeasonalityFactors>;
}

export interface DailyTotal {
  date: Date;
  total: number;
}

export interface ForecastQuantiles {
  p10: number;
  p50: number;
  p90: number;
}

export interface ForecastModel {
  baseline: number;
  alpha: number;
  quantiles: ForecastQuantiles;
  confidence: number;
  trendConfirmed: boolean;
  forecastForDate: (date: Date) => ForecastQuantiles;
}

const WEEKDAYS = 7;
const MONTHS = 12;
const MIN_FACTOR = 0.3;
const MAX_FACTOR = 3;
const OUTLIER_Z = 3.5;
const SHRINK_K = 60;
const MONTH_BINS = 6;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function toWeekdayIndex(date: Date): number {
  const jsDay = date.getDay(); // 0=Sun
  return jsDay === 0 ? 6 : jsDay - 1;
}

function dateKey(date: Date): string {
  return date.toISOString().split('T')[0];
}

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }
  return sorted[mid];
}

function mad(values: number[], med?: number): number {
  if (values.length === 0) return 0;
  const center = med ?? median(values);
  const deviations = values.map((v) => Math.abs(v - center));
  return median(deviations);
}

function weightedQuantile(values: number[], weights: number[], q: number): number {
  if (values.length === 0) return 1;
  const items = values.map((v, i) => ({ v, w: weights[i] ?? 1 }));
  items.sort((a, b) => a.v - b.v);
  const totalWeight = items.reduce((sum, i) => sum + i.w, 0);
  if (totalWeight === 0) return items[Math.floor(items.length / 2)].v;
  let cumulative = 0;
  for (const item of items) {
    cumulative += item.w;
    if (cumulative / totalWeight >= q) return item.v;
  }
  return items[items.length - 1].v;
}

function computeSeasonality(series: { date: Date; value: number }[]): SeasonalityFactors {
  const clean = series.filter((s) => s.value > 0);
  if (clean.length < 3) {
    return {
      weekday: Array(WEEKDAYS).fill(1),
      month: Array(MONTHS).fill(1),
      monthPosition: Array(MONTH_BINS).fill(1),
      sampleSize: clean.length,
    };
  }

  const values = clean.map((s) => s.value);
  const logValues = values.map((v) => Math.log(v));
  const medLog = median(logValues);
  const madLog = mad(logValues, medLog);
  const isOutlier = logValues.map((lv) => {
    if (madLog === 0) return false;
    const z = (0.6745 * (lv - medLog)) / madLog;
    return Math.abs(z) > OUTLIER_Z;
  });

  const nonOutliers = clean.filter((_, i) => !isOutlier[i]);
  const baselineMean = mean(nonOutliers.map((s) => s.value)) || mean(values);
  if (baselineMean <= 0) {
    return {
      weekday: Array(WEEKDAYS).fill(1),
      month: Array(MONTHS).fill(1),
      monthPosition: Array(MONTH_BINS).fill(1),
      sampleSize: nonOutliers.length,
    };
  }

  const weekdaySum = Array(WEEKDAYS).fill(0);
  const weekdayCount = Array(WEEKDAYS).fill(0);
  const monthSum = Array(MONTHS).fill(0);
  const monthCount = Array(MONTHS).fill(0);
  const monthPosSum = Array(MONTH_BINS).fill(0);
  const monthPosCount = Array(MONTH_BINS).fill(0);

  nonOutliers.forEach((s) => {
    const w = toWeekdayIndex(s.date);
    weekdaySum[w] += s.value;
    weekdayCount[w] += 1;
    const m = s.date.getMonth();
    monthSum[m] += s.value;
    monthCount[m] += 1;
    const daysInMonth = new Date(s.date.getFullYear(), s.date.getMonth() + 1, 0).getDate();
    const pos = Math.min(
      MONTH_BINS - 1,
      Math.floor(((s.date.getDate() - 1) / daysInMonth) * MONTH_BINS)
    );
    monthPosSum[pos] += s.value;
    monthPosCount[pos] += 1;
  });

  const weekday = weekdaySum.map((sum, i) => {
    if (weekdayCount[i] === 0) return 1;
    return clamp((sum / weekdayCount[i]) / baselineMean, MIN_FACTOR, MAX_FACTOR);
  });

  const month = monthSum.map((sum, i) => {
    if (monthCount[i] === 0) return 1;
    return clamp((sum / monthCount[i]) / baselineMean, MIN_FACTOR, MAX_FACTOR);
  });

  const monthPosition = monthPosSum.map((sum, i) => {
    if (monthPosCount[i] === 0) return 1;
    return clamp((sum / monthPosCount[i]) / baselineMean, MIN_FACTOR, MAX_FACTOR);
  });

  return {
    weekday,
    month,
    monthPosition,
    sampleSize: nonOutliers.length,
  };
}

function aggregateSeries(records: RawSeasonalityRecord[], keyFn: (r: RawSeasonalityRecord) => string) {
  const byEntity = new Map<string, Map<string, { date: Date; value: number }>>();
  records.forEach((r) => {
    if (!r.value || r.value <= 0) return;
    const entityKey = keyFn(r);
    const dKey = dateKey(r.date);
    if (!byEntity.has(entityKey)) byEntity.set(entityKey, new Map());
    const entityMap = byEntity.get(entityKey)!;
    const existing = entityMap.get(dKey);
    if (existing) {
      existing.value += r.value;
    } else {
      entityMap.set(dKey, { date: r.date, value: r.value });
    }
  });
  return byEntity;
}

export function calculateCompanySeasonality(records: RawSeasonalityRecord[]): SeasonalityHierarchy {
  const totalMap = aggregateSeries(records, () => 'total');
  const totalSeries = totalMap.get('total')
    ? Array.from(totalMap.get('total')!.values())
    : [];

  const groupMap = aggregateSeries(records, (r) => r.grupo || 'OUTROS');
  const companyMap = aggregateSeries(records, (r) => r.empresa || 'OUTROS');

  const groups: Record<string, SeasonalityFactors> = {};
  groupMap.forEach((series, group) => {
    groups[group] = computeSeasonality(Array.from(series.values()));
  });

  const companies: Record<string, SeasonalityFactors> = {};
  companyMap.forEach((series, company) => {
    companies[company] = computeSeasonality(Array.from(series.values()));
  });

  return {
    total: computeSeasonality(totalSeries),
    groups,
    companies,
  };
}

function blendFactors(primary: SeasonalityFactors, fallback: SeasonalityFactors): SeasonalityFactors {
  const weight = primary.sampleSize / (primary.sampleSize + SHRINK_K);
  const weekday = primary.weekday.map((v, i) =>
    clamp(v * weight + (fallback.weekday[i] ?? 1) * (1 - weight), MIN_FACTOR, MAX_FACTOR)
  );
  const month = primary.month.map((v, i) =>
    clamp(v * weight + (fallback.month[i] ?? 1) * (1 - weight), MIN_FACTOR, MAX_FACTOR)
  );
  const monthPosition = primary.monthPosition.map((v, i) =>
    clamp(v * weight + (fallback.monthPosition[i] ?? 1) * (1 - weight), MIN_FACTOR, MAX_FACTOR)
  );
  return {
    weekday,
    month,
    monthPosition,
    sampleSize: primary.sampleSize + fallback.sampleSize,
  };
}

export function getSeasonalityForEntity(
  hierarchy: SeasonalityHierarchy,
  empresa?: string,
  grupo?: string
): SeasonalityFactors {
  if (empresa && hierarchy.companies[empresa]) {
    const company = hierarchy.companies[empresa];
    const fallback = grupo && hierarchy.groups[grupo] ? hierarchy.groups[grupo] : hierarchy.total;
    return blendFactors(company, fallback);
  }

  if (grupo && hierarchy.groups[grupo]) {
    return blendFactors(hierarchy.groups[grupo], hierarchy.total);
  }

  return hierarchy.total;
}

function seasonalityFactor(date: Date, seasonality?: SeasonalityFactors): number {
  if (!seasonality) return 1;
  const weekday = seasonality.weekday[toWeekdayIndex(date)] ?? 1;
  const month = seasonality.month[date.getMonth()] ?? 1;
  const daysInMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const pos = Math.min(
    MONTH_BINS - 1,
    Math.floor(((date.getDate() - 1) / daysInMonth) * MONTH_BINS)
  );
  const monthPosition = seasonality.monthPosition[pos] ?? 1;
  const factor = weekday * month * monthPosition;
  return factor > 0 ? factor : 1;
}

function daysBetween(a: Date, b: Date): number {
  const ms = a.getTime() - b.getTime();
  return Math.round(ms / (1000 * 60 * 60 * 24));
}

function detectTrend(
  series: { date: Date; value: number }[],
  outliers: boolean[],
  refDate: Date
): boolean {
  const recentCutoff = new Date(refDate);
  recentCutoff.setDate(recentCutoff.getDate() - 20);
  const recentOutliers = series.reduce((sum, s, i) => {
    if (s.date >= recentCutoff && outliers[i]) return sum + 1;
    return sum;
  }, 0);
  if (recentOutliers >= 10) return true;

  const monthlyTotals = new Map<string, number>();
  series.forEach((s) => {
    const y = s.date.getFullYear();
    const m = s.date.getMonth() + 1;
    const key = `${y}-${String(m).padStart(2, '0')}`;
    monthlyTotals.set(key, (monthlyTotals.get(key) || 0) + s.value);
  });
  const keys = Array.from(monthlyTotals.keys()).sort();
  if (keys.length < 3) return false;

  const last = monthlyTotals.get(keys[keys.length - 1]) || 0;
  const prev = monthlyTotals.get(keys[keys.length - 2]) || 0;
  const prev2 = monthlyTotals.get(keys[keys.length - 3]) || 0;
  if (prev2 <= 0) return false;
  return last >= prev2 * 1.25 && prev >= prev2 * 1.25;
}

export function buildForecastModel(
  history: DailyTotal[],
  seasonality?: SeasonalityFactors,
  refDate: Date = new Date(),
  fallbackDaily = 0
): ForecastModel {
  const ordered = history
    .filter((h) => h.total > 0)
    .slice()
    .sort((a, b) => a.date.getTime() - b.date.getTime());

  if (ordered.length < 3) {
    const baseline = fallbackDaily || mean(ordered.map((o) => o.total)) || 0;
    return {
      baseline,
      alpha: 0.1,
      quantiles: { p10: 0.85, p50: 1, p90: 1.15 },
      confidence: 0.25,
      trendConfirmed: false,
      forecastForDate: (date: Date) => {
        const expected = baseline * seasonalityFactor(date, seasonality);
        return {
          p10: expected * 0.85,
          p50: expected,
          p90: expected * 1.15,
        };
      },
    };
  }

  const deseasonalized = ordered.map((o) => {
    const factor = seasonalityFactor(o.date, seasonality);
    return {
      date: o.date,
      value: factor > 0 ? o.total / factor : o.total,
    };
  });

  const logValues = deseasonalized.map((d) => Math.log(d.value));
  const medLog = median(logValues);
  const madLog = mad(logValues, medLog);
  const outliers = logValues.map((lv) => {
    if (madLog === 0) return false;
    const z = (0.6745 * (lv - medLog)) / madLog;
    return Math.abs(z) > OUTLIER_Z;
  });

  const trendConfirmed = detectTrend(deseasonalized, outliers, refDate);
  const alpha = trendConfirmed ? 0.25 : 0.1;

  let ewma = deseasonalized[0].value || fallbackDaily;
  const residuals: number[] = [];
  const residualWeights: number[] = [];
  const halfLife = 120;

  deseasonalized.forEach((d, i) => {
    let value = d.value;
    if (!trendConfirmed && outliers[i] && madLog > 0) {
      const sign = Math.sign(logValues[i] - medLog) || 1;
      const cappedLog = medLog + sign * OUTLIER_Z * madLog;
      value = Math.exp(cappedLog);
    }
    ewma = i === 0 ? value : alpha * value + (1 - alpha) * ewma;
    const factor = seasonalityFactor(d.date, seasonality);
    const predicted = ewma * factor;
    if (predicted > 0) {
      const residual = ordered[i].total / predicted;
      residuals.push(residual);
      const ageDays = Math.max(0, daysBetween(refDate, d.date));
      residualWeights.push(Math.exp(-ageDays / halfLife));
    }
  });

  let quantiles: ForecastQuantiles = { p10: 0.85, p50: 1, p90: 1.15 };
  if (residuals.length >= 10) {
    quantiles = {
      p10: weightedQuantile(residuals, residualWeights, 0.1),
      p50: weightedQuantile(residuals, residualWeights, 0.5),
      p90: weightedQuantile(residuals, residualWeights, 0.9),
    };
  }

  const recentCutoff = new Date(refDate);
  recentCutoff.setDate(recentCutoff.getDate() - 29);
  const recentCount = ordered.filter((o) => o.date >= recentCutoff).length;
  const coverage = clamp(recentCount / 30, 0, 1);
  const meanResidual = mean(residuals) || 1;
  const variance = mean(residuals.map((r) => Math.pow(r - meanResidual, 2)));
  const std = Math.sqrt(variance);
  const stability = clamp(1 / (1 + (std / meanResidual)), 0, 1);
  const confidence = clamp(0.2 + 0.6 * coverage + 0.2 * stability, 0.1, 0.95);

  const baseline = ewma || fallbackDaily;

  return {
    baseline,
    alpha,
    quantiles,
    confidence,
    trendConfirmed,
    forecastForDate: (date: Date) => {
      const expected = baseline * seasonalityFactor(date, seasonality);
      return {
        p10: expected * quantiles.p10,
        p50: expected * quantiles.p50,
        p90: expected * quantiles.p90,
      };
    },
  };
}
