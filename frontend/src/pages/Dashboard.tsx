import { useEffect, useState } from 'react';
import { getSummary, getDailyStats, getStatsByHour, getAccounts } from '../lib/api';
import { formatCurrency, formatPnl, formatDuration, pnlColor, cn } from '../lib/utils';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell, ReferenceLine,
} from 'recharts';
import { TrendingUp, TrendingDown, Target, Clock, Activity, Award } from 'lucide-react';

function StatCard({ label, value, icon: Icon, color }: {
  label: string; value: string; icon: any; color?: string;
}) {
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-text-3 uppercase tracking-wider">{label}</span>
        <Icon className={cn('w-4 h-4', color || 'text-text-3')} />
      </div>
      <p className={cn('text-xl font-semibold', color)}>{value}</p>
    </div>
  );
}

export default function Dashboard() {
  const [summary, setSummary] = useState<any>(null);
  const [dailyStats, setDailyStats] = useState<any[]>([]);
  const [hourlyStats, setHourlyStats] = useState<any[]>([]);
  type AcctEntry = { account: string; is_sim: number; n: number; value: string; label: string };
  const [accountEntries, setAccountEntries] = useState<AcctEntry[]>([]);
  // canonical "Acct:L"/"Acct:S" token; backend resolves it
  const [selectedAccount, setSelectedAccount] = useState<string>('');

  useEffect(() => {
    getAccounts().then(d => {
      setAccountEntries(d.entries || []);
    });
  }, []);

  useEffect(() => {
    const params: Record<string, string | undefined> = selectedAccount ? { account_value: selectedAccount } : {};
    getSummary(params).then(setSummary);
    getDailyStats(params).then(d => setDailyStats(d.stats || []));
    getStatsByHour(params).then(d => setHourlyStats(d.stats || []));
  }, [selectedAccount]);

  if (!summary) {
    return (
      <div className="p-8 text-text-2">
        <p>Loading dashboard... If no data appears, go to <strong>Import</strong> first.</p>
      </div>
    );
  }

  const cumPnl = summary.cumulative_pnl || [];
  const totalPnl = summary.total_pnl || 0;
  const pnlColorClass = totalPnl >= 0 ? 'text-green' : 'text-red';

  // Prepare daily P&L bars
  const dailyBars = [...dailyStats].reverse().map(d => ({
    date: d.date,
    pnl: d.total_pnl_dollars,
    trades: d.total_trades,
    winRate: (d.win_rate * 100).toFixed(0),
  }));

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Dashboard</h2>
        {accountEntries.length > 0 && (
          <select
            value={selectedAccount}
            onChange={e => setSelectedAccount(e.target.value)}
            className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text"
          >
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
        )}
      </div>

      {/* Quick stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-3">
        <StatCard label="Net P&L" value={formatPnl(totalPnl)} icon={TrendingUp} color={pnlColorClass} />
        <StatCard label="Commissions" value={`-${formatCurrency(summary.total_commissions || 0)}`} icon={TrendingDown} color="text-yellow" />
        <StatCard label="Total Trades" value={String(summary.total_trades || 0)} icon={Activity} />
        <StatCard label="Win Rate" value={`${(summary.win_rate * 100).toFixed(1)}%`} icon={Target}
          color={summary.win_rate >= 0.5 ? 'text-green' : 'text-red'} />
        <StatCard label="Profit Factor" value={String(summary.profit_factor || '-')} icon={Award}
          color={summary.profit_factor >= 1 ? 'text-green' : 'text-red'} />
        <StatCard label="Avg Trade" value={formatPnl(summary.avg_pnl)} icon={Activity} color={pnlColor(summary.avg_pnl)} />
      </div>

      {/* Cumulative P&L chart */}
      <div className="bg-surface-2 border border-border rounded-xl p-5">
        <h3 className="text-sm font-medium text-text-2 mb-4">Cumulative P&L</h3>
        {cumPnl.length > 0 ? (
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={cumPnl}>
              <defs>
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={totalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={totalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis dataKey="date" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} />
              <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip
                contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0' }}
                formatter={(v: number) => [formatCurrency(v), 'Cumulative P&L']}
              />
              <ReferenceLine y={0} stroke="#363952" />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke={totalPnl >= 0 ? '#22c55e' : '#ef4444'}
                fill="url(#pnlGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-text-3 text-sm">No data yet. Import trade logs first.</p>
        )}
      </div>

      {/* Daily P&L bar chart + Hourly breakdown side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-surface-2 border border-border rounded-xl p-5">
          <h3 className="text-sm font-medium text-text-2 mb-4">Daily P&L</h3>
          {dailyBars.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={dailyBars}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis dataKey="date" tick={{ fill: '#6b6f8a', fontSize: 10 }} tickLine={false} />
                <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                <Tooltip
                  contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0' }}
                  formatter={(v: number, name: string) => [formatCurrency(v), 'P&L']}
                  labelFormatter={(label) => label}
                />
                <ReferenceLine y={0} stroke="#363952" />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {dailyBars.map((d, i) => (
                    <Cell key={i} fill={d.pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-text-3 text-sm">No daily data</p>
          )}
        </div>

        <div className="bg-surface-2 border border-border rounded-xl p-5">
          <h3 className="text-sm font-medium text-text-2 mb-4">P&L by Hour (CT)</h3>
          {hourlyStats.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={hourlyStats}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis dataKey="hour" tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false}
                  tickFormatter={h => `${h}:00`} />
                <YAxis tick={{ fill: '#6b6f8a', fontSize: 11 }} tickLine={false} tickFormatter={v => `$${v}`} />
                <Tooltip
                  contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0' }}
                  formatter={(v: number) => [formatCurrency(v), 'P&L']}
                  labelFormatter={h => `${h}:00 CT`}
                />
                <ReferenceLine y={0} stroke="#363952" />
                <Bar dataKey="total_pnl" radius={[3, 3, 0, 0]}>
                  {hourlyStats.map((d, i) => (
                    <Cell key={i} fill={d.total_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-text-3 text-sm">No hourly data</p>
          )}
        </div>
      </div>

      {/* Daily stats table */}
      <div className="bg-surface-2 border border-border rounded-xl overflow-hidden">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-medium text-text-2">Daily Breakdown</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-text-3 text-xs uppercase">
                <th className="text-left px-4 py-2.5">Date</th>
                <th className="text-left px-4 py-2.5">Account</th>
                <th className="text-right px-4 py-2.5">Trades</th>
                <th className="text-right px-4 py-2.5">W/L</th>
                <th className="text-right px-4 py-2.5">Win Rate</th>
                <th className="text-right px-4 py-2.5">P&L</th>
                <th className="text-right px-4 py-2.5">PF</th>
              </tr>
            </thead>
            <tbody>
              {dailyStats.map((d, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-surface-3/50 transition-colors">
                  <td className="px-4 py-2.5 font-medium">{d.date}</td>
                  <td className="px-4 py-2.5 text-text-2 text-xs">{d.account}</td>
                  <td className="px-4 py-2.5 text-right">{d.total_trades}</td>
                  <td className="px-4 py-2.5 text-right">
                    <span className="text-green">{d.winning_trades}</span>
                    /
                    <span className="text-red">{d.losing_trades}</span>
                  </td>
                  <td className={cn('px-4 py-2.5 text-right', d.win_rate >= 0.5 ? 'text-green' : 'text-red')}>
                    {(d.win_rate * 100).toFixed(0)}%
                  </td>
                  <td className={cn('px-4 py-2.5 text-right font-medium', pnlColor(d.total_pnl_dollars))}>
                    {formatPnl(d.total_pnl_dollars)}
                  </td>
                  <td className={cn('px-4 py-2.5 text-right', d.profit_factor >= 1 ? 'text-green' : 'text-red')}>
                    {d.profit_factor?.toFixed(1) || '-'}
                  </td>
                </tr>
              ))}
              {dailyStats.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-text-3">
                    No data. Import trade logs first.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
