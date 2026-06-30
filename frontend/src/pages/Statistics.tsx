import { useEffect, useState } from 'react';
import { getStatsByHour, getStatsByDay, getStatsByDuration, getStatsByATR, getAccounts, getSymbols, getCumulativePnl, getIntradayPnl, getSummary, getExcursion } from '../lib/api';
import { formatCurrency, formatDuration, formatPnl, pnlColor, cn } from '../lib/utils';
import {
  AreaChart, Area, BarChart, Bar, LineChart, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Cell, ReferenceLine, ScatterChart, Scatter,
  ComposedChart, Line, Legend,
} from 'recharts';

function StatSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-5">
      <h3 className="text-sm font-medium text-text-2 mb-4">{title}</h3>
      {children}
    </div>
  );
}

function BreakdownTable({ data, labelKey, labelHeader }: {
  data: any[]; labelKey: string; labelHeader: string;
}) {
  return (
    <table className="w-full text-sm mt-3">
      <thead>
        <tr className="border-b border-border text-text-3 text-xs uppercase">
          <th className="text-left px-3 py-2">{labelHeader}</th>
          <th className="text-right px-3 py-2">Trades</th>
          <th className="text-right px-3 py-2">W/L</th>
          <th className="text-right px-3 py-2">Win%</th>
          <th className="text-right px-3 py-2">Net P&L</th>
          <th className="text-right px-3 py-2">Avg</th>
        </tr>
      </thead>
      <tbody>
        {data.map((d, i) => (
          <tr key={i} className="border-b border-border/30 hover:bg-surface-3/30">
            <td className="px-3 py-2 font-medium">{d[labelKey]}</td>
            <td className="px-3 py-2 text-right">{d.trades}</td>
            <td className="px-3 py-2 text-right">
              <span className="text-green">{d.winners}</span>/<span className="text-red">{d.losers}</span>
            </td>
            <td className={cn('px-3 py-2 text-right', (d.win_rate || d.winners / d.trades) >= 0.5 ? 'text-green' : 'text-red')}>
              {((d.win_rate || d.winners / d.trades) * 100).toFixed(0)}%
            </td>
            <td className={cn('px-3 py-2 text-right font-medium', pnlColor(d.total_pnl))}>
              {formatCurrency(d.total_pnl)}
            </td>
            <td className={cn('px-3 py-2 text-right', pnlColor(d.avg_pnl))}>
              {formatCurrency(d.avg_pnl)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const tooltipStyle = { background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0' };

export default function Statistics() {
  const [hourly, setHourly] = useState<any[]>([]);
  const [daily, setDaily] = useState<any[]>([]);
  const [duration, setDuration] = useState<any[]>([]);
  const [atrData, setAtrData] = useState<{ stats: any[]; scatter: any[] }>({ stats: [], scatter: [] });
  const [cumPnl, setCumPnl] = useState<{ daily: any[]; per_trade: any[]; total: number }>({ daily: [], per_trade: [], total: 0 });
  const [intraday, setIntraday] = useState<{ day_curves: any[]; avg_curve: any[] }>({ day_curves: [], avg_curve: [] });
  const [summary, setSummary] = useState<any>(null);
  const [excursion, setExcursion] = useState<any>(null);
  const [expandedChart, setExpandedChart] = useState<string | null>(null);
  type AcctEntry = { account: string; is_sim: number; n: number; value: string; label: string };
  const [accountEntries, setAccountEntries] = useState<AcctEntry[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  // selectedAccount holds the canonical "Acct:L"/"Acct:S" filter token now.
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [selectedSymbol, setSelectedSymbol] = useState<string>('');
  const [selectedSide, setSelectedSide] = useState<string>('');
  const [fromDate, setFromDate] = useState<string>('');
  const [toDate, setToDate] = useState<string>('');
  const [useGross, setUseGross] = useState<boolean>(false);

  useEffect(() => {
    getAccounts().then(d => setAccountEntries(d.entries || []));
    getSymbols().then(d => setSymbols(d.symbols || []));
  }, []);

  useEffect(() => {
    const p: Record<string, string | undefined> = {
      account_value: selectedAccount || undefined,
      symbol: selectedSymbol || undefined,
      side: selectedSide || undefined,
      from_date: fromDate || undefined,
      to_date: toDate || undefined,
      gross: useGross ? 'true' : undefined,
    };
    getSummary(p).then(setSummary);
    getCumulativePnl(p).then(setCumPnl);
    getIntradayPnl(p).then(setIntraday);
    getStatsByHour(p).then(d => setHourly(d.stats || []));
    getStatsByDay(p).then(d => setDaily(d.stats || []));
    getStatsByDuration(p).then(d => setDuration(d.stats || []));
    getStatsByATR(p).then(d => setAtrData(d));
    getExcursion(p).then(setExcursion);
  }, [selectedAccount, selectedSymbol, selectedSide, fromDate, toDate, useGross]);

  const hourlyWithWinRate = hourly.map(h => ({
    ...h,
    win_rate: h.trades > 0 ? h.winners / h.trades : 0,
    label: `${h.hour}:00`,
  }));

  return (
    <div className="p-6 space-y-5 max-w-7xl">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Statistics</h2>
      </div>

      {/* Filters */}
      <div className="bg-surface-2 border border-border rounded-xl p-4 flex items-center gap-3 flex-wrap">
        <span className="text-xs text-text-3 uppercase tracking-wider">Filters</span>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-text-3">From</label>
          <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
            className="bg-surface-3 border border-border rounded-lg px-2 py-1.5 text-sm text-text" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-text-3">To</label>
          <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
            className="bg-surface-3 border border-border rounded-lg px-2 py-1.5 text-sm text-text" />
        </div>
        <select value={selectedAccount} onChange={e => setSelectedAccount(e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">All Accounts</option>
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
        <select value={selectedSymbol} onChange={e => setSelectedSymbol(e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">All Symbols</option>
          {symbols.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={selectedSide} onChange={e => setSelectedSide(e.target.value)}
          className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text">
          <option value="">Long & Short</option>
          <option value="LONG">Long Only</option>
          <option value="SHORT">Short Only</option>
        </select>
        <div className="flex items-center gap-1 bg-surface-3 border border-border rounded-lg overflow-hidden">
          <button onClick={() => setUseGross(false)}
            className={cn('px-3 py-1.5 text-sm font-medium transition-colors',
              !useGross ? 'bg-accent text-white' : 'text-text-2 hover:text-text')}>
            Net P&L
          </button>
          <button onClick={() => setUseGross(true)}
            className={cn('px-3 py-1.5 text-sm font-medium transition-colors',
              useGross ? 'bg-accent text-white' : 'text-text-2 hover:text-text')}>
            Gross P&L
          </button>
        </div>
        {(fromDate || toDate || selectedAccount || selectedSymbol || selectedSide || useGross) && (
          <button onClick={() => { setFromDate(''); setToDate(''); setSelectedAccount(''); setSelectedSymbol(''); setSelectedSide(''); setUseGross(false); }}
            className="px-3 py-1.5 rounded-lg bg-surface-4 hover:bg-border text-xs font-medium transition-colors">
            Clear All
          </button>
        )}
      </div>

      {/* Key Stats Grid */}
      {summary && summary.total_trades > 0 && (
        <div className="bg-surface-2 border border-border rounded-xl overflow-hidden">
          <div className="p-4 border-b border-border">
            <h3 className="text-sm font-medium text-text-2">Stats</h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 text-sm">
            {[
              { label: 'Total Gain/Loss', value: formatPnl(summary.total_pnl), color: pnlColor(summary.total_pnl) },
              { label: 'Largest Gain', value: formatCurrency(summary.best_trade), color: 'text-green' },
              { label: 'Largest Loss', value: formatCurrency(summary.worst_trade), color: 'text-red' },
              { label: 'Avg Daily Gain/Loss', value: formatPnl(summary.avg_daily_pnl), color: pnlColor(summary.avg_daily_pnl) },
              { label: 'Avg Daily Volume', value: String(summary.avg_daily_volume || 0), color: '' },
              { label: 'Avg Trade Gain/Loss', value: formatPnl(summary.avg_pnl), color: pnlColor(summary.avg_pnl) },
              { label: 'Avg Winning Trade', value: formatCurrency(summary.avg_winner), color: 'text-green' },
              { label: 'Avg Losing Trade', value: formatCurrency(summary.avg_loser), color: 'text-red' },
              { label: 'Total Number of Trades', value: String(summary.total_trades), color: '' },
              { label: 'Number of Winning Trades', value: `${summary.winners} (${(summary.win_rate * 100).toFixed(1)}%)`, color: 'text-green' },
              { label: 'Number of Losing Trades', value: `${summary.losers} (${(summary.loss_rate * 100).toFixed(1)}%)`, color: 'text-red' },
              { label: 'Number of Scratch Trades', value: `${summary.scratches || 0} (${summary.total_trades ? ((summary.scratches||0)/summary.total_trades*100).toFixed(1) : 0}%)`, color: '' },
              { label: 'Avg Hold Time (winning)', value: formatDuration(summary.avg_win_duration_s), color: '' },
              { label: 'Avg Hold Time (losing)', value: formatDuration(summary.avg_loss_duration_s), color: '' },
              { label: 'Avg Hold Time (scratch)', value: formatDuration(summary.avg_scratch_duration_s), color: '' },
              { label: 'Max Consecutive Wins', value: String(summary.max_consec_wins || 0), color: 'text-green' },
              { label: 'Max Consecutive Losses', value: String(summary.max_consec_losses || 0), color: 'text-red' },
              { label: 'Trade P&L Std Deviation', value: formatCurrency(summary.std_dev), color: '' },
              { label: 'Kelly Percentage', value: summary.kelly_pct != null ? `${summary.kelly_pct}%` : 'n/a', color: summary.kelly_pct > 0 ? 'text-green' : 'text-red' },
              { label: 'System Quality Number (SQN)', value: summary.sqn != null ? String(summary.sqn) : 'n/a', color: '' },
              { label: 'K-Ratio', value: summary.k_ratio != null ? String(summary.k_ratio) : 'n/a', color: '' },
              { label: 'Probability of Random Chance', value: summary.prob_random_pct != null ? `${summary.prob_random_pct}%` : 'n/a', color: '' },
              { label: 'Profit Factor', value: String(summary.profit_factor || '-'), color: summary.profit_factor >= 1 ? 'text-green' : 'text-red' },
              { label: 'Total Commissions', value: formatCurrency(summary.total_commissions), color: 'text-yellow' },
              { label: 'Trading Days', value: String(summary.trading_days || 0), color: '' },
            ].map((s, i) => (
              <div key={i} className="px-4 py-2.5 border-b border-r border-border/40 flex justify-between items-center">
                <span className="text-text-3 text-xs">{s.label}</span>
                <span className={cn('font-medium', s.color)}>{s.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cumulative P&L */}
      {cumPnl.daily.length > 0 && (
        <StatSection title={`Cumulative P&L: ${formatCurrency(cumPnl.total)}`}>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <p className="text-xs text-text-3 mb-2">Daily Cumulative</p>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={cumPnl.daily}>
                  <defs>
                    <linearGradient id="cumPnlGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={cumPnl.total >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={cumPnl.total >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis dataKey="date" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number, name: string) => [
                      formatCurrency(v), name === 'cum_pnl' ? 'Cumulative' : 'Day P&L'
                    ]} />
                  <ReferenceLine y={0} stroke="#363952" />
                  <Area type="monotone" dataKey="cum_pnl"
                    stroke={cumPnl.total >= 0 ? '#22c55e' : '#ef4444'}
                    fill="url(#cumPnlGrad)" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div>
              <p className="text-xs text-text-3 mb-2">Daily Net P&L</p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={cumPnl.daily}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis dataKey="date" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number) => [formatCurrency(v), 'Day P&L']}
                    labelFormatter={l => `${l} (${cumPnl.daily.find(d => d.date === l)?.trades || 0} trades)`} />
                  <ReferenceLine y={0} stroke="#363952" />
                  <Bar dataKey="day_pnl" radius={[3, 3, 0, 0]}>
                    {cumPnl.daily.map((d, i) => (
                      <Cell key={i} fill={d.day_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </StatSection>
      )}

      {/* Intraday P&L Curves */}
      {intraday.day_curves.length > 0 && (
        <StatSection title="Intraday P&L Curve (per day overlay)">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs text-text-3">Each day's P&L over time (CT)</p>
                <button onClick={() => setExpandedChart('intraday-overlay')}
                  className="text-xs text-text-3 hover:text-accent-2 px-2 py-0.5 rounded border border-border hover:border-accent-2/50 transition-colors">
                  Expand ⛶
                </button>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis
                    dataKey="minutes" type="number"
                    domain={['dataMin', 'dataMax']}
                    tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false}
                    tickFormatter={m => {
                      const h = Math.floor(m / 60);
                      const min = m % 60;
                      return `${h}:${min.toString().padStart(2, '0')}`;
                    }}
                    label={{ value: 'Time (CT)', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }}
                  />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    tickFormatter={v => `$${v}`} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number, name: string) => [formatCurrency(v), name]}
                    labelFormatter={m => {
                      const h = Math.floor(Number(m) / 60);
                      const min = Number(m) % 60;
                      return `${h}:${min.toString().padStart(2, '0')} CT`;
                    }}
                  />
                  <ReferenceLine y={0} stroke="#9ca0b8" strokeWidth={1.2} strokeDasharray="4 2" />
                  {intraday.day_curves.map((dc, i) => {
                    const palette = ['#6366f1', '#f59e0b', '#06b6d4', '#ec4899', '#8b5cf6',
                                     '#10b981', '#f97316', '#14b8a6', '#e879f9', '#84cc16',
                                     '#3b82f6', '#ef4444', '#22c55e', '#a855f7'];
                    const color = palette[i % palette.length];
                    return (
                      <Line key={dc.date} data={dc.points} dataKey="pnl" name={dc.date}
                        stroke={color} strokeWidth={1.5}
                        dot={false} connectNulls />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div>
              <p className="text-xs text-text-3 mb-2">Average P&L by trade # (with min/max range)</p>
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={intraday.avg_curve}>
                  <defs>
                    <linearGradient id="avgIntraGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#6366f1" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis dataKey="trade_num" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    label={{ value: 'Trade #', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    tickFormatter={v => `$${v}`} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number, name: string) => [
                      formatCurrency(v),
                      name === 'avg_pnl' ? 'Avg P&L' : name === 'max_pnl' ? 'Best Day' : name === 'min_pnl' ? 'Worst Day' : name
                    ]}
                    labelFormatter={l => `Trade #${l}`}
                  />
                  <ReferenceLine y={0} stroke="#363952" />
                  <Area dataKey="max_pnl" stroke="none" fill="#22c55e" fillOpacity={0.1} />
                  <Area dataKey="min_pnl" stroke="none" fill="#ef4444" fillOpacity={0.1} />
                  <Line dataKey="avg_pnl" stroke="#6366f1" strokeWidth={2} dot={{ r: 2, fill: '#6366f1' }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-text-3 text-xs uppercase">
                  <th className="text-left px-3 py-2">Date</th>
                  <th className="text-right px-3 py-2">Trades</th>
                  <th className="text-right px-3 py-2">Final P&L</th>
                  <th className="text-left px-3 py-2">Intraday Path</th>
                </tr>
              </thead>
              <tbody>
                {intraday.day_curves.map((dc) => (
                  <tr key={dc.date} className="border-b border-border/30 hover:bg-surface-3/30">
                    <td className="px-3 py-2 font-medium">{dc.date}</td>
                    <td className="px-3 py-2 text-right">{dc.trades}</td>
                    <td className={cn('px-3 py-2 text-right font-medium', pnlColor(dc.final_pnl))}>
                      {formatCurrency(dc.final_pnl)}
                    </td>
                    <td className="px-3 py-1">
                      <ResponsiveContainer width="100%" height={30}>
                        <LineChart data={dc.points}>
                          <ReferenceLine y={0} stroke="#ffffff" strokeWidth={0.8} />
                          <Line dataKey="pnl" stroke={dc.final_pnl >= 0 ? '#22c55e' : '#ef4444'}
                            strokeWidth={1.5} dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </StatSection>
      )}

      {/* By Hour */}
      <StatSection title="P&L by Hour of Day (CT)">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-text-3 mb-2">Net P&L</p>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={hourlyWithWinRate}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis dataKey="label" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [formatCurrency(v), 'Net P&L']} />
                <ReferenceLine y={0} stroke="#363952" />
                <Bar dataKey="total_pnl" radius={[3, 3, 0, 0]}>
                  {hourlyWithWinRate.map((d, i) => (
                    <Cell key={i} fill={d.total_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div>
            <p className="text-xs text-text-3 mb-2">Win Rate & Trade Count</p>
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart data={hourlyWithWinRate}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis dataKey="label" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                <YAxis yAxisId="left" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
                <YAxis yAxisId="right" orientation="right" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                  tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
                <Tooltip contentStyle={tooltipStyle}
                  formatter={(v: number, name: string) => [
                    name === 'win_rate' ? `${(v * 100).toFixed(0)}%` : v, name === 'win_rate' ? 'Win Rate' : 'Trades'
                  ]} />
                <Bar yAxisId="left" dataKey="trades" name="Trades" fill="#363952" radius={[3, 3, 0, 0]} />
                <Line yAxisId="right" dataKey="win_rate" name="Win Rate" stroke="#6366f1" strokeWidth={2} dot={{ r: 3, fill: '#6366f1' }} />
                <ReferenceLine yAxisId="right" y={0.5} stroke="#363952" strokeDasharray="3 3" />
                <Legend wrapperStyle={{ fontSize: 11, color: '#9ca0b8' }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
        <BreakdownTable data={hourlyWithWinRate} labelKey="label" labelHeader="Hour" />
      </StatSection>

      {/* By Day of Week */}
      <StatSection title="P&L by Day of Week">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={daily}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis dataKey="day_name" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
              <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [formatCurrency(v), 'Net P&L']} />
              <ReferenceLine y={0} stroke="#363952" />
              <Bar dataKey="total_pnl" radius={[3, 3, 0, 0]}>
                {daily.map((d, i) => (
                  <Cell key={i} fill={d.total_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={daily}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis dataKey="day_name" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
              <YAxis yAxisId="left" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
              <Bar yAxisId="left" dataKey="trades" fill="#363952" radius={[3, 3, 0, 0]} />
              <Line yAxisId="right" dataKey="win_rate" stroke="#6366f1" strokeWidth={2} dot={{ r: 3, fill: '#6366f1' }} />
              <ReferenceLine yAxisId="right" y={0.5} stroke="#363952" strokeDasharray="3 3" />
              <Tooltip contentStyle={tooltipStyle}
                formatter={(v: number, name: string) => [
                  name === 'Win Rate' ? `${(v * 100).toFixed(0)}%` : v, name
                ]} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <BreakdownTable data={daily} labelKey="day_name" labelHeader="Day" />
      </StatSection>

      {/* By Duration */}
      <StatSection title="P&L by Trade Duration">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={duration}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis dataKey="bucket" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
              <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [formatCurrency(v), 'Net P&L']} />
              <ReferenceLine y={0} stroke="#363952" />
              <Bar dataKey="total_pnl" radius={[3, 3, 0, 0]}>
                {duration.map((d, i) => (
                  <Cell key={i} fill={d.total_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={duration}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis dataKey="bucket" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
              <YAxis yAxisId="left" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
              <Bar yAxisId="left" dataKey="trades" fill="#363952" radius={[3, 3, 0, 0]} />
              <Line yAxisId="right" dataKey="win_rate" stroke="#6366f1" strokeWidth={2} dot={{ r: 3, fill: '#6366f1' }} />
              <ReferenceLine yAxisId="right" y={0.5} stroke="#363952" strokeDasharray="3 3" />
              <Tooltip contentStyle={tooltipStyle}
                formatter={(v: number, name: string) => [
                  name === 'Win Rate' ? `${(v * 100).toFixed(0)}%` : v, name
                ]} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <BreakdownTable data={duration} labelKey="bucket" labelHeader="Duration" />
      </StatSection>

      {/* MAE / MFE Histogram */}
      {excursion && excursion.count > 0 && (
        <StatSection title={`Intra-Trade Excursion Distribution (${excursion.count} trades, per-contract, in ticks)`}>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <p className="text-xs text-text-3 mb-2">
                MAE (Max Adverse Excursion) — Worst: <span className="text-red">{excursion.mae_stats.worst} ticks</span>
                {' '}| Median: {excursion.mae_stats.median} | Avg: {excursion.mae_stats.avg}
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={excursion.mae_histogram}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis dataKey="bucket" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    label={{ value: 'Ticks', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    label={{ value: 'Trades', angle: -90, position: 'insideLeft', fill: '#6b6f8a', fontSize: 10 }} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number) => [v, 'Trades']}
                    labelFormatter={l => `${l} to ${Number(l) + 2} ticks`} />
                  <ReferenceLine x={0} stroke="#363952" />
                  <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                    {excursion.mae_histogram.map((d: any, i: number) => (
                      <Cell key={i} fill="#ef4444" fillOpacity={0.75} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div>
              <p className="text-xs text-text-3 mb-2">
                MFE (Max Favorable Excursion) — Best: <span className="text-green">{excursion.mfe_stats.best} ticks</span>
                {' '}| Median: {excursion.mfe_stats.median} | Avg: {excursion.mfe_stats.avg}
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={excursion.mfe_histogram}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis dataKey="bucket" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    label={{ value: 'Ticks', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                    label={{ value: 'Trades', angle: -90, position: 'insideLeft', fill: '#6b6f8a', fontSize: 10 }} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number) => [v, 'Trades']}
                    labelFormatter={l => `${l} to ${Number(l) + 2} ticks`} />
                  <ReferenceLine x={0} stroke="#363952" />
                  <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                    {excursion.mfe_histogram.map((d: any, i: number) => (
                      <Cell key={i} fill="#22c55e" fillOpacity={0.75} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          {/* Scatter: MAE vs Final */}
          <div className="mt-4">
            <p className="text-xs text-text-3 mb-2">MAE vs Final Result (ticks)</p>
            <ResponsiveContainer width="100%" height={240}>
              <ScatterChart>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis dataKey="mae_ticks" name="MAE" type="number"
                  tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                  label={{ value: 'MAE (ticks)', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }} />
                <YAxis dataKey="final_ticks" name="Final" type="number"
                  tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                  label={{ value: 'Final (ticks)', angle: -90, position: 'insideLeft', fill: '#6b6f8a', fontSize: 10 }} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3' }}
                  formatter={(v: number, name: string) => [`${v.toFixed(1)} ticks`, name]} />
                <ReferenceLine y={0} stroke="#363952" />
                <ReferenceLine x={0} stroke="#363952" />
                <Scatter data={excursion.scatter} fill="#6366f1" fillOpacity={0.5} r={3} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </StatSection>
      )}

      {/* By ATR */}
      <StatSection title="P&L vs 5-min ATR (5-bar average at entry)">
        {atrData.stats.length > 0 ? (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-text-3 mb-2">Net P&L by ATR Bucket</p>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={atrData.stats}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                    <XAxis dataKey="bucket" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                    <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                    <Tooltip contentStyle={tooltipStyle}
                      formatter={(v: number, name: string) => [
                        name === 'avg_atr' ? v.toFixed(2) + ' pts' : formatCurrency(v),
                        name === 'avg_atr' ? 'Avg ATR' : 'Net P&L'
                      ]} />
                    <ReferenceLine y={0} stroke="#363952" />
                    <Bar dataKey="total_pnl" radius={[3, 3, 0, 0]}>
                      {atrData.stats.map((d, i) => (
                        <Cell key={i} fill={d.total_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div>
                <p className="text-xs text-text-3 mb-2">ATR vs P&L Scatter</p>
                <ResponsiveContainer width="100%" height={200}>
                  <ScatterChart>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                    <XAxis dataKey="atr" name="ATR" type="number"
                      tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                      label={{ value: 'ATR (pts)', position: 'insideBottom', offset: -2, fill: '#6b6f8a', fontSize: 10 }} />
                    <YAxis dataKey="net_pnl" name="Net P&L" type="number"
                      tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                      tickFormatter={v => `$${v}`} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3' }}
                      formatter={(v: number, name: string) => [
                        name === 'ATR' ? `${v.toFixed(2)} pts` : formatCurrency(v), name
                      ]} />
                    <ReferenceLine y={0} stroke="#363952" />
                    <Scatter
                      data={atrData.scatter.filter((s: any) => s.atr != null)}
                      fill="#6366f1"
                      fillOpacity={0.6}
                      r={3}
                    />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            </div>
            <BreakdownTable data={atrData.stats} labelKey="bucket" labelHeader="ATR Range" />
          </>
        ) : (
          <p className="text-text-3 text-sm">Computing ATR requires tick data. No data available yet.</p>
        )}
      </StatSection>

      {/* Expanded chart modal */}
      {expandedChart === 'intraday-overlay' && (
        <div
          className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4"
          onClick={() => setExpandedChart(null)}
        >
          <div
            className="bg-surface-2 border border-border rounded-xl p-6 w-full max-w-[95vw] h-[90vh]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Intraday P&L Curve — Expanded View</h3>
              <button onClick={() => setExpandedChart(null)}
                className="text-text-2 hover:text-text px-3 py-1 rounded border border-border hover:bg-surface-3">
                Close ✕
              </button>
            </div>
            <div className="h-[calc(90vh-100px)]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                  <XAxis
                    dataKey="minutes" type="number"
                    domain={['dataMin', 'dataMax']}
                    tick={{ fill: '#6b6f8a', fontSize: 12 }} tickLine={false}
                    tickFormatter={m => {
                      const h = Math.floor(m / 60);
                      const min = m % 60;
                      return `${h}:${min.toString().padStart(2, '0')}`;
                    }}
                    label={{ value: 'Time (CT)', position: 'insideBottom', offset: -5, fill: '#6b6f8a', fontSize: 12 }}
                  />
                  <YAxis tick={{ fill: '#6b6f8a', fontSize: 12 }} tickLine={false}
                    tickFormatter={v => `$${v}`} />
                  <Tooltip contentStyle={tooltipStyle}
                    formatter={(v: number, name: string) => [formatCurrency(v), name]}
                    labelFormatter={m => {
                      const h = Math.floor(Number(m) / 60);
                      const min = Number(m) % 60;
                      return `${h}:${min.toString().padStart(2, '0')} CT`;
                    }}
                  />
                  <ReferenceLine y={0} stroke="#9ca0b8" strokeWidth={1.5} strokeDasharray="4 2" />
                  <Legend wrapperStyle={{ fontSize: 11, color: '#9ca0b8' }} />
                  {intraday.day_curves.map((dc, i) => {
                    const palette = ['#6366f1', '#f59e0b', '#06b6d4', '#ec4899', '#8b5cf6',
                                     '#10b981', '#f97316', '#14b8a6', '#e879f9', '#84cc16',
                                     '#3b82f6', '#ef4444', '#22c55e', '#a855f7'];
                    const color = palette[i % palette.length];
                    return (
                      <Line key={dc.date} data={dc.points} dataKey="pnl" name={dc.date}
                        stroke={color} strokeWidth={1.8}
                        dot={false} connectNulls />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
