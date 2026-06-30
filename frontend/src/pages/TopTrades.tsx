/**
 * Side-by-side comparison of the best- or worst-net-P&L trades on an account.
 *
 * Each card shows:
 *   - rank, date · NY time, symbol/side/qty
 *   - mini price-bar chart with entry/exit markers
 *   - mini running-P&L curve
 *   - net P&L, MFE, MAE, setup name, quality grade
 * Clicking the card navigates to /trades/{id} for the full detail page.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine, ReferenceDot,
} from 'recharts';
import { getLeaderboard, getAccounts } from '../lib/api';
import type { LeaderboardCard, AccountEntry } from '../lib/api';
import { formatCurrency, cn } from '../lib/utils';
import { getExchangeTimezone, getExchangeTzLabel, formatClockInTz } from '../lib/timezones';
import { Trophy, Skull, TrendingUp, TrendingDown } from 'lucide-react';

const QUALITY_LABEL = ['', 'D', 'C', 'B', 'A', 'A+'];

export default function TopTrades() {
  // canonical "Acct:L" / "Acct:S" token
  const [accountValue, setAccountValue] = useState<string>('');  // populated after getAccounts loads
  const [accountEntries, setAccountEntries] = useState<AccountEntry[]>([]);
  const [direction, setDirection] = useState<'top' | 'bottom'>('top');
  const [n, setN] = useState<number>(20);
  const [symbol, setSymbol] = useState<string>('ES');
  const [cards, setCards] = useState<LeaderboardCard[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    getAccounts()
      .then(d => setAccountEntries(d.entries || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!accountValue) return;
    setLoading(true);
    setError('');
    getLeaderboard({ account_value: accountValue, direction, n, symbol: symbol || undefined })
      .then(r => setCards(r.trades || []))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, [accountValue, direction, n, symbol]);

  // P&L axis range pinned across all cards for visual comparison
  const pnlRange = useMemo(() => {
    if (!cards.length) return { min: 0, max: 0 };
    let lo = 0, hi = 0;
    for (const c of cards) {
      if (c.mae < lo) lo = c.mae;
      if (c.mfe > hi) hi = c.mfe;
    }
    return { min: lo, max: hi };
  }, [cards]);

  const tz = useMemo(() => getExchangeTimezone(symbol || 'ES'), [symbol]);
  const tzLabel = useMemo(() => getExchangeTzLabel(symbol || 'ES'), [symbol]);

  return (
    <div className="p-6 space-y-4 max-w-7xl">
      {/* Header + filters */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h2 className="text-2xl font-semibold flex items-center gap-2">
          {direction === 'top' ? <Trophy className="w-6 h-6 text-yellow-400" /> : <Skull className="w-6 h-6 text-red" />}
          {direction === 'top' ? 'Top' : 'Worst'} {n} Trades
        </h2>
        <span className="text-xs text-text-3">times in {tzLabel}</span>
      </div>

      <div className="bg-surface-2 border border-border rounded-xl p-3 flex flex-wrap items-center gap-2 text-sm">
        {/* Direction toggle */}
        <div className="flex items-center gap-1 mr-2">
          <button onClick={() => setDirection('top')}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium transition-colors',
              direction === 'top' ? 'bg-green/15 text-green' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
            )}>
            <TrendingUp className="w-3.5 h-3.5" /> Best
          </button>
          <button onClick={() => setDirection('bottom')}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium transition-colors',
              direction === 'bottom' ? 'bg-red/15 text-red' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
            )}>
            <TrendingDown className="w-3.5 h-3.5" /> Worst
          </button>
        </div>

        {/* N count */}
        <div className="flex items-center gap-1 mr-2">
          <span className="text-text-3 mr-1">Count:</span>
          {[10, 20, 30, 50].map(v => (
            <button key={v} onClick={() => setN(v)}
              className={cn(
                'px-2 py-1 rounded text-xs font-medium transition-colors',
                n === v ? 'bg-accent text-white' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
              )}>
              {v}
            </button>
          ))}
        </div>

        {/* Account */}
        <label className="flex items-center gap-2">
          <span className="text-text-3">Account:</span>
          <select value={accountValue} onChange={e => setAccountValue(e.target.value)}
            className="bg-surface-3 border border-border rounded-lg px-2 py-1 text-sm focus:outline-none focus:border-accent/50">
            {accountEntries.filter(e => !e.is_sim).map(e => (
              <option key={e.value} value={e.value}>{e.label} ({e.n})</option>
            ))}
            {accountEntries.some(e => e.is_sim) && accountEntries.some(e => !e.is_sim) && (
              <option disabled>──────────</option>
            )}
            {accountEntries.filter(e => e.is_sim).map(e => (
              <option key={e.value} value={e.value}>{e.label} ({e.n})</option>
            ))}
            {!accountEntries.some(e => e.value === accountValue) && (
              <option value={accountValue}>{accountValue}</option>
            )}
          </select>
        </label>

        {/* Symbol */}
        <label className="flex items-center gap-2">
          <span className="text-text-3">Symbol:</span>
          <select value={symbol} onChange={e => setSymbol(e.target.value)}
            className="bg-surface-3 border border-border rounded-lg px-2 py-1 text-sm focus:outline-none focus:border-accent/50">
            <option value="">All</option>
            <option value="ES">ES</option>
            <option value="MES">MES</option>
            <option value="NQ">NQ</option>
            <option value="MNQ">MNQ</option>
          </select>
        </label>
      </div>

      {/* Status */}
      {loading && <p className="text-text-2">Loading…</p>}
      {error && <p className="text-red">Error: {error}</p>}
      {!loading && !error && cards.length === 0 && (
        <p className="text-text-3">No trades found for this filter.</p>
      )}

      {/* Card grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {cards.map(c => <TradeCard key={c.trade.id} card={c} tz={tz} pnlRange={pnlRange} />)}
      </div>
    </div>
  );
}

interface TradeCardProps {
  card: LeaderboardCard;
  tz: string;
  pnlRange: { min: number; max: number };
}

function TradeCard({ card, tz, pnlRange }: TradeCardProps) {
  const { rank, trade, bars, pnl_curve, mfe, mae } = card;
  const isWin = (trade.net_pnl ?? 0) >= 0;
  const entryS = Math.floor(trade.entry_time_ms / 1000);
  const exitS = Math.floor(trade.exit_time_ms / 1000);

  // Add entry/exit price markers as overlay data
  const priceData = bars.map(b => ({ time: b.time, close: b.close }));
  const pnlData = pnl_curve;

  return (
    <Link to={`/trades/${encodeURIComponent(trade.id)}`}
      className="block bg-surface-2 border border-border rounded-xl p-3 hover:border-accent/40 transition-colors">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
        <div className="flex items-center gap-2">
          <span className={cn(
            'inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold',
            isWin ? 'bg-green/15 text-green' : 'bg-red/15 text-red'
          )}>#{rank}</span>
          <span className="font-mono text-xs text-text-3">{trade.trade_date}</span>
          <span className="text-xs text-text-3">{formatClockInTz(entryS, tz)} → {formatClockInTz(exitS, tz)}</span>
          <span className={cn(
            'text-[10px] font-medium px-1.5 py-0.5 rounded',
            trade.side === 'LONG' ? 'bg-green/15 text-green' : 'bg-red/15 text-red'
          )}>
            {trade.side} {trade.entry_qty}
          </span>
          <span className="text-xs font-mono text-text-3">{trade.root_symbol}</span>
        </div>
        <div className={cn('text-base font-bold tabular-nums', isWin ? 'text-green' : 'text-red')}>
          {formatCurrency(trade.net_pnl)}
        </div>
      </div>

      {/* Charts side-by-side */}
      <div className="grid grid-cols-2 gap-2">
        {/* Price chart */}
        <div className="h-24">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={priceData} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="2 4" stroke="#2a2d3e" vertical={false} />
              <XAxis dataKey="time" type="number" domain={['dataMin', 'dataMax']} hide />
              <YAxis domain={['dataMin', 'dataMax']} hide />
              <Tooltip
                contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 6, color: '#e4e6f0', fontSize: 11 }}
                labelFormatter={(v: any) => formatClockInTz(v as number, tz)}
                formatter={(v: any) => [(v as number).toFixed(2), 'Price']}
              />
              <Line type="monotone" dataKey="close" stroke="#cccccc" strokeWidth={1.2} dot={false} isAnimationActive={false} />
              {/* Entry / exit reference dots on the price line */}
              <ReferenceDot x={entryS} y={trade.entry_price} r={3.5} fill="#facc15" stroke="#facc15" />
              <ReferenceDot x={exitS} y={trade.exit_price} r={3.5} fill={isWin ? '#22c55e' : '#ef4444'} stroke={isWin ? '#22c55e' : '#ef4444'} />
            </LineChart>
          </ResponsiveContainer>
          <p className="text-[10px] text-text-3 text-center mt-0.5">Price · entry {trade.entry_price.toFixed(2)} → exit {trade.exit_price.toFixed(2)}</p>
        </div>

        {/* P&L curve */}
        <div className="h-24">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pnlData} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
              <defs>
                <linearGradient id={`g_${trade.id}_pos`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22c55e" stopOpacity={0.5} />
                  <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
                <linearGradient id={`g_${trade.id}_neg`} x1="0" y1="1" x2="0" y2="0">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.5} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 4" stroke="#2a2d3e" vertical={false} />
              <XAxis dataKey="time" type="number" domain={['dataMin', 'dataMax']} hide />
              <YAxis domain={[pnlRange.min, pnlRange.max]} hide />
              <Tooltip
                contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 6, color: '#e4e6f0', fontSize: 11 }}
                labelFormatter={(v: any) => formatClockInTz(v as number, tz)}
                formatter={(v: any) => [formatCurrency(v as number), 'Unrealized']}
              />
              <ReferenceLine y={0} stroke="#666" strokeWidth={0.7} />
              <Area type="monotone" dataKey="pnl"
                stroke={isWin ? '#22c55e' : '#ef4444'} strokeWidth={1.2}
                fill={isWin ? `url(#g_${trade.id}_pos)` : `url(#g_${trade.id}_neg)`}
                isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
          <p className="text-[10px] text-text-3 text-center mt-0.5">
            P&L · MFE <span className="text-green">{formatCurrency(mfe)}</span> · MAE <span className="text-red">{formatCurrency(mae)}</span>
          </p>
        </div>
      </div>

      {/* Footer: setup/rating/duration */}
      <div className="flex items-center justify-between mt-2 text-[11px] text-text-3 flex-wrap gap-1">
        <div className="flex items-center gap-2">
          {trade.setup_name && (
            <span className="text-accent font-medium">{trade.setup_name}</span>
          )}
          {trade.rating != null && (
            <span className="bg-surface-3 px-1.5 py-0.5 rounded text-text-2">
              {trade.rating}/{QUALITY_LABEL[trade.rating] || ''}
            </span>
          )}
        </div>
        <div>
          {trade.duration_seconds.toFixed(0)}s · {trade.pnl_points >= 0 ? '+' : ''}{trade.pnl_points.toFixed(2)} pts
        </div>
      </div>
    </Link>
  );
}
