/**
 * Trade Card form, isolated from the heavy TradeDetail render tree.
 *
 * Why this lives in its own component:
 *   Every keystroke in a textarea triggers a React re-render of the component
 *   that owns the state. When the state lived in TradeDetail, each character
 *   re-rendered the whole 800-line tree — including two Recharts canvases
 *   (the trade P&L curve + the day-summary sparkline) which both
 *   re-construct SVG on every render. That's what made typing feel laggy.
 *
 *   By owning the text-field state HERE, only this small component re-renders
 *   when you type. The parent's Recharts/lightweight-charts stay frozen.
 */
import { useState, memo } from 'react';
import { Save } from 'lucide-react';
import { updateTradeCard } from '../lib/api';
import type { SetupRow } from '../lib/api';
import { cn } from '../lib/utils';

interface Props {
  tradeId: string;
  initial: {
    setup_name?: string;
    trade_idea?: string;
    what_good?: string;
    what_bad?: string;
    notes?: string;
    rating?: number | null;
  };
  setupLibrary: SetupRow[];
}

function TradeCardFormImpl({ tradeId, initial, setupLibrary }: Props) {
  const [setupName, setSetupName] = useState(initial.setup_name || '');
  const [tradeIdea, setTradeIdea] = useState(initial.trade_idea || '');
  const [whatGood, setWhatGood] = useState(initial.what_good || '');
  const [whatBad, setWhatBad] = useState(initial.what_bad || '');
  const [rating, setRating] = useState<number | null>(initial.rating ?? null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      await updateTradeCard(tradeId, {
        setup_name: setupName,
        trade_idea: tradeIdea,
        what_good: whatGood,
        what_bad: whatBad,
        notes: initial.notes,         // preserve existing notes value
        rating: rating ?? undefined,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-text-2">Trade Card</h3>
        <div className="flex items-center gap-2">
          {saved && <span className="text-xs text-green-400">Saved ✓</span>}
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent-2 disabled:opacity-50 text-white text-sm transition-colors">
            <Save className="w-3.5 h-3.5" /> {saving ? 'Saving…' : 'Save Card'}
          </button>
        </div>
      </div>

      {/* Setup name + quality rating */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        <div>
          <label className="text-xs text-text-3 block mb-1">Setup name</label>
          <input type="text" value={setupName} onChange={e => setSetupName(e.target.value)}
            placeholder="e.g. pullback long, VWAP fade, breakout retest"
            list="setup-suggestions"
            className="w-full bg-surface-3 border border-border rounded-lg px-3 py-2 text-sm text-text
                       placeholder:text-text-3 focus:outline-none focus:border-accent/50" />
          <datalist id="setup-suggestions">
            {setupLibrary.map(s => (
              <option key={s.name} value={s.name}>
                {`${s.used}× · avg ${s.avg_net != null ? (s.avg_net >= 0 ? '+$' : '-$') + Math.abs(s.avg_net).toFixed(0) : '—'} · ${s.wins}W/${s.losses}L`}
              </option>
            ))}
            {setupLibrary.length === 0 && (
              <>
                <option value="pullback long" />
                <option value="pullback short" />
                <option value="breakout long" />
                <option value="breakout short" />
                <option value="VWAP fade" />
                <option value="reversal long" />
                <option value="reversal short" />
                <option value="opening drive" />
                <option value="POC reject" />
                <option value="VAH/VAL break" />
                <option value="single-print fill" />
              </>
            )}
          </datalist>
          {setupLibrary.length > 0 && (
            <p className="text-xs text-text-3 mt-1">
              {setupLibrary.length} setup{setupLibrary.length === 1 ? '' : 's'} in your library
            </p>
          )}
        </div>
        <div>
          <label className="text-xs text-text-3 block mb-1">
            Quality <span className="text-text-3/70">(1 = worst, 5 = best)</span>
          </label>
          <div className="flex gap-2">
            {[
              { v: 1, grade: 'D',  hint: 'avoid',  color: 'text-red' },
              { v: 2, grade: 'C',  hint: 'B−',     color: 'text-orange-400' },
              { v: 3, grade: 'B',  hint: 'OK',     color: 'text-yellow-300' },
              { v: 4, grade: 'A',  hint: 'good',   color: 'text-green-400' },
              { v: 5, grade: 'A+', hint: 'great',  color: 'text-emerald-300' },
            ].map(({ v, grade, hint, color }) => (
              <button key={v}
                onClick={() => setRating(v === rating ? null : v)}
                title={`${v}  ·  ${grade}  ·  ${hint}`}
                className={cn(
                  'flex flex-col items-center justify-center w-12 h-12 rounded-lg text-sm font-medium transition-colors',
                  v === rating ? 'bg-accent text-white' : 'bg-surface-3 hover:bg-surface-4'
                )}>
                <span className={v === rating ? 'text-white' : color}>{v}</span>
                <span className={cn(
                  'text-[10px] leading-none mt-0.5',
                  v === rating ? 'text-white/80' : 'text-text-3'
                )}>{grade}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Trade idea / thesis */}
      <div className="mb-3">
        <label className="text-xs text-text-3 block mb-1">Trade idea / thesis</label>
        <textarea value={tradeIdea} onChange={e => setTradeIdea(e.target.value)}
          placeholder="What was the setup? HTF trend, level being tested, expected reaction…"
          rows={2}
          className="w-full bg-surface-3 border border-border rounded-lg px-3 py-2 text-sm text-text
                     placeholder:text-text-3 resize-y focus:outline-none focus:border-accent/50" />
      </div>

      {/* What was good / bad */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-green-300 block mb-1">What was good ✓</label>
          <textarea value={whatGood} onChange={e => setWhatGood(e.target.value)}
            placeholder="Execution, timing, structure read, discipline…"
            rows={3}
            className="w-full bg-surface-3 border border-green-900/30 rounded-lg px-3 py-2 text-sm text-text
                       placeholder:text-text-3 resize-y focus:outline-none focus:border-green-500/50" />
        </div>
        <div>
          <label className="text-xs text-red-300 block mb-1">What was bad ✗</label>
          <textarea value={whatBad} onChange={e => setWhatBad(e.target.value)}
            placeholder="Late entry, oversize, tilt, ignored rule, bad stop…"
            rows={3}
            className="w-full bg-surface-3 border border-red-900/30 rounded-lg px-3 py-2 text-sm text-text
                       placeholder:text-text-3 resize-y focus:outline-none focus:border-red-500/50" />
        </div>
      </div>
    </div>
  );
}

// React.memo: when the parent (TradeDetail) re-renders for ANY reason,
// this form will skip its render unless its props actually changed.
const TradeCardForm = memo(TradeCardFormImpl);
export default TradeCardForm;
