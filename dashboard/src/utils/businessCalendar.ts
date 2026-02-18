import { addDays, subDays } from 'date-fns';

export type HolidayBucket = 'normal' | 'holiday' | 'pre_holiday' | 'post_holiday';
export type PaydayBucket = 'regular' | 'salary_window' | 'advance_window' | 'month_end';

function dateKey(date: Date): string {
  return date.toISOString().split('T')[0];
}

function normalizeDate(date: Date): Date {
  const normalized = new Date(date);
  normalized.setHours(12, 0, 0, 0);
  return normalized;
}

function easterSunday(year: number): Date {
  // Meeus/Jones/Butcher algorithm (Gregorian calendar)
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31); // 3=Mar, 4=Apr
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(year, month - 1, day, 12, 0, 0);
}

function fixedNationalHolidays(year: number): Date[] {
  return [
    new Date(year, 0, 1, 12, 0, 0), // Confraternizacao Universal
    new Date(year, 3, 21, 12, 0, 0), // Tiradentes
    new Date(year, 4, 1, 12, 0, 0), // Dia do Trabalho
    new Date(year, 8, 7, 12, 0, 0), // Independencia
    new Date(year, 9, 12, 12, 0, 0), // Nossa Senhora Aparecida
    new Date(year, 10, 2, 12, 0, 0), // Finados
    new Date(year, 10, 15, 12, 0, 0), // Proclamacao da Republica
    new Date(year, 10, 20, 12, 0, 0), // Dia da Consciencia Negra
    new Date(year, 11, 25, 12, 0, 0), // Natal
  ];
}

function movableNationalHolidays(year: number): Date[] {
  const easter = easterSunday(year);
  return [
    subDays(easter, 48), // Carnaval (segunda)
    subDays(easter, 47), // Carnaval (terca)
    subDays(easter, 2), // Sexta-feira Santa
    addDays(easter, 60), // Corpus Christi
  ];
}

function buildHolidaySet(year: number): Set<string> {
  const keys = new Set<string>();
  const all = [...fixedNationalHolidays(year), ...movableNationalHolidays(year)];
  all.forEach((holiday) => keys.add(dateKey(holiday)));
  return keys;
}

const holidayCache = new Map<number, Set<string>>();

function getHolidaySet(year: number): Set<string> {
  const cached = holidayCache.get(year);
  if (cached) return cached;
  const next = buildHolidaySet(year);
  holidayCache.set(year, next);
  return next;
}

export function isNationalHoliday(date: Date): boolean {
  const normalized = normalizeDate(date);
  return getHolidaySet(normalized.getFullYear()).has(dateKey(normalized));
}

export function getHolidayBucket(date: Date): HolidayBucket {
  const normalized = normalizeDate(date);
  if (isNationalHoliday(normalized)) return 'holiday';

  const prev = subDays(normalized, 1);
  const next = addDays(normalized, 1);
  if (isNationalHoliday(next)) return 'pre_holiday';
  if (isNationalHoliday(prev)) return 'post_holiday';

  return 'normal';
}

export function getPaydayBucket(date: Date): PaydayBucket {
  const normalized = normalizeDate(date);
  const day = normalized.getDate();
  const daysInMonth = new Date(normalized.getFullYear(), normalized.getMonth() + 1, 0).getDate();

  if (day >= 4 && day <= 7) return 'salary_window';
  if (day >= 18 && day <= 22) return 'advance_window';
  if (day >= daysInMonth - 2) return 'month_end';
  return 'regular';
}
