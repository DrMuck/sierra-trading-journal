import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getTrades, getAccounts, getDates } from '../lib/api';
import { formatPnl, formatPoints, formatDuration, formatTime, pnlColor, cn } from '../lib/utils';
import { ChevronLeft, ChevronRight, Filter } from 'lucide-react';

export default function Trades() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [trades, setTrades] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  // accountEntries: each (account, is_sim) combo with its own dropdown entry.
  // value = "<account>:L" for live, "<account>:S" for sim — backend resolves
  // the suffix into the proper SQL filter.
  type AcctEntry = { account: string; is_sim: number; n: number; value: string; label: string };
  const [accountEntries, setAccountEntries] = useState<AcctEntry[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const page = parseInt(searchParams.get('page') || '1');
  const limit = 50;
  const filterDate = searchParams.get('date') || '';
  const filterAccount = searchParams.get('account') || '';  // canonical "Acct:L|S"
  const filterSide = searchParams.get('side') || '';

  useEffect(() => {
    getAccounts().then(d => setAccountEntries(d.entries || []));
    getDates().then(d => setDates(d.imported_dates || []));
  }, []);

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {
      limit: String(limit),
      offset: String((page - 1) * limit),
    };
    if (filterDate) params.date = filterDate;
    if (filterAccount) params.account_value = filterAccount;
    if (filterSide) params.side = filterSide;

    getTrades(params).then(d => {
      setTrades(d.trades || []);
      setTotal(d.total || 0);
      setLoading(false);
    });
  }, [page, filterDate, filterAccount, filterSide]);

  const totalPages = Math.ceil(total / limit);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set('page', '1');
    setSearchParams(next);
  }

  return (
    <div className="p-6 space-y-4 max-w-7xl">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Trades</h2>
        <span className="text-sm text-text-2">{total} trades</span>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Filter className="w-4 h-4 text-text-3" />
        <select value={filterDate} onChange={e => setFilter('date', e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">All Dates</option>
          {dates.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <select value={filterAccount} onChange={e => setFilter('account', e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">All Accounts</option>
          {/* Live accounts first, then Sim */}
          {accountEntries.filter(e => !e.is_sim).map(e => (
            <option key={e.value} value={e.value}>{e.label} ({e.n})</option>
          ))}
          {accountEntries.some(e => e.is_sim) && accountEntries.some(e => !e.is_sim) && (
            <option disabled>──────────</option>
          )}
          {accountEntries.filter(e => e.is_sim).map(e => (
            <option key={e.value} value={e.value}>{e.label} ({e.n})</option>
          ))}
        </select>
        <select value={filterSide} onChange={e => setFilter('side', e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">All Sides</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>
      </div>

      {/* Trade table */}
      <div className="bg-surface-2 border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-text-3 text-xs uppercase">
                <th className="text-left px-4 py-2.5">Time</th>
                <th className="text-left px-4 py-2.5">Symbol</th>
                <th className="text-left px-4 py-2.5">Side</th>
                <th className="text-right px-4 py-2.5">Qty</th>
                <th className="text-right px-4 py-2.5">Entry</th>
                <th className="text-right px-4 py-2.5">Exit</th>
                <th className="text-right px-4 py-2.5">Gross</th>
                <th className="text-right px-4 py-2.5">Fees</th>
                <th className="text-right px-4 py-2.5">Net P&L</th>
                <th className="text-right px-4 py-2.5">Duration</th>
                <th className="text-left px-4 py-2.5">Account</th>
                <th className="text-center px-4 py-2.5">Exit Type</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => navigate(`/trades/${encodeURIComponent(t.id)}`)}
                  className="border-b border-border/50 hover:bg-surface-3/50 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-2.5 font-mono text-xs">
                    <div>{t.trade_date}</div>
                    <div className="text-text-3">{formatTime(t.entry_time)}</div>
                  </td>
                  <td className="px-4 py-2.5 font-medium">{t.symbol}</td>
                  <td className="px-4 py-2.5">
                    <span className={cn(
                      'px-2 py-0.5 rounded text-xs font-medium',
                      t.side === 'LONG' ? 'bg-green/15 text-green' : 'bg-red/15 text-red'
                    )}>
                      {t.side}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right">{t.entry_qty}</td>
                  <td className="px-4 py-2.5 text-right font-mono">{t.entry_price?.toFixed(2)}</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {t.is_open ? <span className="text-yellow text-xs">OPEN</span> : t.exit_price?.toFixed(2)}
                  </td>
                  <td className={cn('px-4 py-2.5 text-right', pnlColor(t.pnl_dollars))}>
                    {formatPnl(t.pnl_dollars)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-text-3 text-xs">
                    {t.commissions ? `-$${t.commissions.toFixed(2)}` : '-'}
                  </td>
                  <td className={cn('px-4 py-2.5 text-right font-semibold', pnlColor(t.net_pnl))}>
                    {formatPnl(t.net_pnl)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-text-2">
                    {formatDuration(t.duration_seconds)}
                  </td>
                  <td className="px-4 py-2.5 text-text-3 text-xs">{t.account}</td>
                  <td className="px-4 py-2.5 text-center">
                    <span className={cn('text-xs px-1.5 py-0.5 rounded', {
                      'bg-blue/15 text-blue': t.exit_order_type === 'Limit',
                      'bg-red/15 text-red': t.exit_order_type === 'Stop',
                      'bg-yellow/15 text-yellow': t.exit_order_type === 'Market',
                    })}>
                      {t.exit_order_type || '-'}
                    </span>
                  </td>
                </tr>
              ))}
              {!loading && trades.length === 0 && (
                <tr>
                  <td colSpan={12} className="px-4 py-12 text-center text-text-3">
                    No trades found. Import some data first.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border">
            <span className="text-xs text-text-3">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-1">
              <button
                disabled={page <= 1}
                onClick={() => setSearchParams(p => { p.set('page', String(page - 1)); return p; })}
                className="p-1.5 rounded hover:bg-surface-3 disabled:opacity-30"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setSearchParams(p => { p.set('page', String(page + 1)); return p; })}
                className="p-1.5 rounded hover:bg-surface-3 disabled:opacity-30"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
