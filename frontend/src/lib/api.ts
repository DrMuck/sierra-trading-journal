const BASE = '/api';

async function fetchJson<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// Import
export const scanFiles = () => fetchJson<any>('/import/scan', { method: 'POST' });
export const importFile = (path: string, force = false) =>
  fetchJson<any>(`/import/file?path=${encodeURIComponent(path)}&force=${force}`, { method: 'POST' });
export const importAll = (force = false) =>
  fetchJson<any>(`/import/all?force=${force}`, { method: 'POST' });

// Trades
export const getTrades = (params: Record<string, string>) => {
  const qs = new URLSearchParams(params).toString();
  return fetchJson<any>(`/trades?${qs}`);
};
export const getTrade = (id: string) => fetchJson<any>(`/trades/${encodeURIComponent(id)}`);
export const updateTradeNotes = (id: string, notes: string, tags: string, rating?: number) =>
  fetchJson<any>(`/trades/${encodeURIComponent(id)}/notes?notes=${encodeURIComponent(notes)}&tags=${encodeURIComponent(tags)}${rating != null ? `&rating=${rating}` : ''}`, { method: 'PUT' });

export type TradeCard = {
  setup_name?: string;
  trade_idea?: string;
  what_good?: string;
  what_bad?: string;
  notes?: string;
  tags?: string;
  rating?: number;
};
export const updateTradeCard = (id: string, card: TradeCard) =>
  fetchJson<any>(`/trades/${encodeURIComponent(id)}/card`, {
    method: 'PUT',
    body: JSON.stringify(card),
  });

export type SetupRow = {
  name: string;
  used: number;
  last_used: string;
  avg_net: number | null;
  wins: number;
  losses: number;
};
export const getSetupLibrary = (account?: string) =>
  fetchJson<{ setups: SetupRow[] }>(
    `/trade-cards/setups${account ? `?account=${encodeURIComponent(account)}` : ''}`
  );

export type LeaderboardCard = {
  rank: number;
  trade: {
    id: string;
    symbol: string;
    root_symbol: string;
    side: 'LONG' | 'SHORT';
    entry_time_ms: number;
    exit_time_ms: number;
    entry_price: number;
    exit_price: number;
    entry_qty: number;
    trade_date: string;
    duration_seconds: number;
    pnl_dollars: number;
    net_pnl: number;
    pnl_points: number;
    setup_name?: string;
    rating?: number | null;
  };
  bars: { time: number; open: number; high: number; low: number; close: number; volume: number }[];
  pnl_curve: { time: number; pnl: number }[];
  mfe: number;
  mae: number;
};
export const getLeaderboard = (params: {
  /** canonical "Acct:L" / "Acct:S" token preferred; falls back to plain account */
  account_value?: string;
  account?: string;
  direction: 'top' | 'bottom';
  n?: number;
  symbol?: string;
  interval?: number;
}) => {
  const qs = new URLSearchParams({
    direction: params.direction,
    n: String(params.n ?? 20),
    interval: String(params.interval ?? 15),
  });
  if (params.account_value) qs.set('account_value', params.account_value);
  if (params.account) qs.set('account', params.account);
  if (params.symbol) qs.set('symbol', params.symbol);
  return fetchJson<{ trades: LeaderboardCard[]; n: number; account: string; direction: string; symbol: string | null }>(
    `/leaderboard?${qs.toString()}`
  );
};

// Stats
export const getDailyStats = (params?: Record<string, string>) => {
  const qs = params ? new URLSearchParams(params).toString() : '';
  return fetchJson<any>(`/stats/daily?${qs}`);
};
export const getSummary = (params?: Record<string, string | undefined>) => {
  return fetchJson<any>(`/stats/summary${_statQs(params)}`);
};
function _statQs(params?: Record<string, string | undefined>): string {
  if (!params) return '';
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v) clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return qs ? `?${qs}` : '';
}
export const getCumulativePnl = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/cumulative${_statQs(p)}`);
export const getIntradayPnl = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/intraday${_statQs(p)}`);
export const getStatsByHour = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/by-hour${_statQs(p)}`);
export const getStatsByDay = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/by-day${_statQs(p)}`);
export const getStatsByDuration = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/by-duration${_statQs(p)}`);
export const getStatsByATR = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/by-atr${_statQs(p)}`);
export const getExcursion = (p?: Record<string, string | undefined>) =>
  fetchJson<any>(`/stats/excursion${_statQs(p)}`);
export const getSymbols = () => fetchJson<any>('/symbols');

// Chart
export const getChartOHLC = (symbol: string, date: string, interval = 60) =>
  fetchJson<any>(`/chart/ohlc?symbol=${symbol}&date=${date}&interval=${interval}`);
export const getTradeChart = (tradeId: string, interval = 60, lookahead = 60) =>
  fetchJson<any>(`/chart/trade/${encodeURIComponent(tradeId)}?interval=${interval}&lookahead=${lookahead}`);
export const getDailyChart = (tradeId: string, interval = 300, zoneDaysBack = 5) =>
  fetchJson<any>(`/chart/daily/${encodeURIComponent(tradeId)}?interval=${interval}&zone_days_back=${zoneDaysBack}`);
export const getBusinessZones = (symbol: string, date: string, daysBack = 0) =>
  fetchJson<any>(`/zones/${encodeURIComponent(symbol)}/${encodeURIComponent(date)}?days_back=${daysBack}`);

// Meta
export type AccountEntry = {
  account: string;
  is_sim: number;          // 0 = live, 1 = sim
  n: number;               // trade count
  value: string;           // canonical "Acct:L" or "Acct:S" filter token
  label: string;           // display string e.g. "Sim1 · Sim"
};
export const getAccounts = () =>
  fetchJson<{ accounts: string[]; imported: string[]; entries: AccountEntry[] }>('/accounts');
export const getDates = () => fetchJson<any>('/dates');
