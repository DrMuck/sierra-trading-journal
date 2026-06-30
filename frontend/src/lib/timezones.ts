// Symbol → exchange-timezone mapping. Each charted symbol shows its own
// local exchange time (e.g. ES on CME → America/Chicago → "08:30" at cash open
// instead of UTC "13:30").

const CME_SYMBOLS = new Set([
  // Equity index
  'ES', 'MES', 'NQ', 'MNQ', 'RTY', 'M2K', 'YM', 'MYM',
  // Metals
  'GC', 'MGC', 'SI', 'SIL', 'HG',
  // Energy
  'CL', 'MCL', 'NG', 'RB', 'HO',
  // Rates
  'ZB', 'ZN', 'ZF', 'ZT',
  // Agriculture
  'ZS', 'ZC', 'ZW', 'ZL', 'ZM',
]);

const EUREX_SYMBOLS = new Set([
  'FDAX', 'FESX', 'FGBL', 'FGBM', 'FGBS', 'FGBX',
]);

const CRYPTO_SYMBOLS = new Set([
  'BTC', 'ETH', 'SOL', 'DOGE',
]);

export function getExchangeTimezone(rootSymbol: string | undefined | null): string {
  if (!rootSymbol) return 'UTC';
  const s = rootSymbol.toUpperCase();
  if (CME_SYMBOLS.has(s)) return 'America/Chicago';
  if (EUREX_SYMBOLS.has(s)) return 'Europe/Berlin';
  if (CRYPTO_SYMBOLS.has(s)) return 'UTC';
  return 'UTC';  // safe default
}

/** Short user-facing timezone label that follows DST automatically. */
export function getExchangeTzLabel(rootSymbol: string | undefined | null): string {
  const tz = getExchangeTimezone(rootSymbol);
  // Get the short timezone name (CDT/CST/CET/CEST/UTC) for the *current* date.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    timeZoneName: 'short',
  }).formatToParts(new Date());
  return parts.find(p => p.type === 'timeZoneName')?.value ?? tz;
}

/** HH:MM in the symbol's exchange timezone. Input: Unix seconds (UTC). */
export function formatClockInTz(unixSec: number, tz: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(unixSec * 1000));
}

/** HH:MM:SS in the symbol's exchange timezone. */
export function formatClockSecInTz(unixSec: number, tz: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(unixSec * 1000));
}

/** Build a lightweight-charts `localization` object for the symbol's TZ. */
export function makeChartLocalization(rootSymbol: string | undefined | null) {
  const tz = getExchangeTimezone(rootSymbol);
  return {
    locale: 'en-GB',
    // Crosshair / legend / OHLC tooltip
    timeFormatter: (time: number) => formatClockSecInTz(time, tz),
    dateFormat: 'yyyy-MM-dd',
  };
}

/** Build a tickMarkFormatter for the time axis. */
export function makeTickMarkFormatter(rootSymbol: string | undefined | null) {
  const tz = getExchangeTimezone(rootSymbol);
  return (time: number) => formatClockInTz(time, tz);
}
