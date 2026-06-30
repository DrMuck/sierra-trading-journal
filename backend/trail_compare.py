"""
Compare trail-stop rule variants stratified by time-of-day.

Rules implemented (all bars built from tick data inside the trade window):
  - "fixed":        keep initial SL, no trail (baseline)
  - "be_at_1R":     move to break-even when 1R is touched, no further trail
  - "wick_1m":      move stop to prior 1-min bar low (LONG) / high (SHORT)
                    after each completed bar, but only in favorable direction
  - "wick_30s":     same as above but on 30s bars
  - "hl_pivot":     after a confirmed swing low (LONG) / high (SHORT) with lookback,
                    trail to that pivot ± offset_ticks. (Original sim.)
  - "atr_mult":     trail at MFE - k * ATR(N seconds)

Time-of-day buckets (NY local):
  pre_open (<09:30), open_15 (09:30-09:45), open_30 (09:45-10:00),
  morning_1 (10:00-10:30), morning_2 (10:30-11:30), mid (11:30-13:00),
  afternoon (13:00-15:00), late (>=15:00)

Also flags: is_first_trade_of_day, minutes_since_open.
"""
import argparse
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


# ---------- helpers ----------

def _bucket_for_minute_after_open(minutes_after_open: float) -> str:
    """Map minutes past 09:30 NY to a bucket name."""
    if minutes_after_open < 0:
        return "pre_open"
    if minutes_after_open < 15:
        return "open_15"
    if minutes_after_open < 30:
        return "open_30"
    if minutes_after_open < 60:
        return "morning_1"
    if minutes_after_open < 120:
        return "morning_2"
    if minutes_after_open < 210:
        return "mid"
    if minutes_after_open < 330:
        return "afternoon"
    return "late"


_BUCKET_ORDER = ["pre_open", "open_15", "open_30", "morning_1",
                 "morning_2", "mid", "afternoon", "late"]


def _build_bars(ts_ms: np.ndarray, px: np.ndarray, bar_seconds: int, start_ms: int):
    if len(ts_ms) == 0:
        return []
    interval_ms = bar_seconds * 1000
    bar_ids = (ts_ms - start_ms) // interval_ms
    unique = np.unique(bar_ids)
    out = []
    for bid in unique:
        m = bar_ids == bid
        bp = px[m]
        bt = ts_ms[m]
        out.append({
            "start_ms": start_ms + int(bid) * interval_ms,
            "end_ms": start_ms + int(bid + 1) * interval_ms,
            "high": float(bp.max()),
            "low": float(bp.min()),
            "close": float(bp[-1]),
            "last_ms": int(bt[-1]),
        })
    return out


# ---------- core sim for one rule ----------

@dataclass
class SimOut:
    exit_price: float
    exit_ms: int
    exit_reason: str
    n_stop_updates: int = 0


def simulate(trade: dict, ticks: dict, *, rule: str,
             initial_stop_ticks: float, tick_size: float,
             max_duration_minutes: int = 60,
             # rule-specific:
             bar_seconds: int = 60,
             lookback: int = 3, offset_ticks: float = 1,
             atr_n_seconds: int = 60, atr_mult: float = 1.5,
             risk_ticks: float = 6) -> SimOut | None:
    if ticks is None or len(ticks["ts_ns"]) == 0:
        return None
    side = trade["side"]
    entry = trade["entry_price"]
    entry_ms = trade["entry_time_ms"]
    max_end_ms = entry_ms + max_duration_minutes * 60 * 1000

    ts_ms = ticks["ts_ns"] // 1_000_000
    px = ticks["price"]
    mask = (ts_ms >= entry_ms) & (ts_ms <= max_end_ms)
    w_ts = ts_ms[mask]
    w_px = px[mask]
    if len(w_ts) == 0:
        return None

    # Initial stop
    if side == "LONG":
        stop = entry - initial_stop_ticks * tick_size
    else:
        stop = entry + initial_stop_ticks * tick_size
    n_updates = 0

    # Build bars (used by wick_*, hl_pivot)
    bar_s = {
        "wick_1m": 60, "wick_30s": 30, "hl_pivot": bar_seconds,
    }.get(rule, 60)
    bars = _build_bars(w_ts, w_px, bar_s, entry_ms)

    # Track running MFE/MAE for be_at_1R, atr_mult
    if side == "LONG":
        running_extreme = entry  # high so far
    else:
        running_extreme = entry  # low so far
    hit_1R = False
    r_unit_px = risk_ticks * tick_size

    # Iterate tick-by-tick (cheap on numpy)
    # For wick_1m / hl_pivot, we update stop only at bar boundary.
    # For be_at_1R and atr_mult, we update continuously.

    bar_idx = 0
    last_processed_bar_end = entry_ms

    exit_price = None
    exit_ms = None
    exit_reason = None

    for i in range(len(w_ts)):
        t = int(w_ts[i])
        p = float(w_px[i])

        # 1) Stop-out check first
        if side == "LONG" and p <= stop:
            exit_price = stop
            exit_ms = t
            exit_reason = "stop" if n_updates == 0 else "trail"
            break
        if side == "SHORT" and p >= stop:
            exit_price = stop
            exit_ms = t
            exit_reason = "stop" if n_updates == 0 else "trail"
            break

        # 2) Update running extreme + 1R flag
        if side == "LONG":
            if p > running_extreme:
                running_extreme = p
            if (p - entry) >= r_unit_px:
                hit_1R = True
        else:
            if p < running_extreme:
                running_extreme = p
            if (entry - p) >= r_unit_px:
                hit_1R = True

        # 3) Rule-based stop updates
        if rule == "fixed":
            pass

        elif rule == "be_at_1R":
            if hit_1R:
                be = entry
                if side == "LONG" and be > stop:
                    stop = be
                    n_updates += 1
                elif side == "SHORT" and be < stop:
                    stop = be
                    n_updates += 1

        elif rule in ("wick_1m", "wick_30s"):
            # Move bar pointer forward; on bar close, update stop to prev bar's wick
            while bar_idx < len(bars) and bars[bar_idx]["end_ms"] <= t:
                # bar_idx just closed; trail to its wick
                cb = bars[bar_idx]
                if side == "LONG":
                    new_stop = cb["low"] - offset_ticks * tick_size
                    if new_stop > stop:
                        stop = new_stop
                        n_updates += 1
                else:
                    new_stop = cb["high"] + offset_ticks * tick_size
                    if new_stop < stop:
                        stop = new_stop
                        n_updates += 1
                bar_idx += 1

        elif rule == "hl_pivot":
            # On bar close, attempt pivot confirmation
            while bar_idx < len(bars) and bars[bar_idx]["end_ms"] <= t:
                center = bar_idx - lookback
                if center >= lookback:
                    cb = bars[center]
                    left = range(center - lookback, center)
                    right = range(center + 1, center + lookback + 1)
                    if side == "LONG":
                        if all(bars[j]["low"] >= cb["low"] for j in list(left) + list(right)):
                            new_stop = cb["low"] - offset_ticks * tick_size
                            if new_stop > stop:
                                stop = new_stop
                                n_updates += 1
                    else:
                        if all(bars[j]["high"] <= cb["high"] for j in list(left) + list(right)):
                            new_stop = cb["high"] + offset_ticks * tick_size
                            if new_stop < stop:
                                stop = new_stop
                                n_updates += 1
                bar_idx += 1

        elif rule == "atr_mult":
            # ATR estimated as rolling realized range of last atr_n_seconds, scaled
            # Cheap proxy: (high - low) of last N seconds.
            cutoff = t - atr_n_seconds * 1000
            recent = w_px[(w_ts >= cutoff) & (w_ts <= t)]
            if len(recent) >= 2:
                atr_px = float(recent.max() - recent.min())
                if side == "LONG":
                    new_stop = running_extreme - atr_mult * atr_px
                    if new_stop > stop:
                        stop = new_stop
                        n_updates += 1
                else:
                    new_stop = running_extreme + atr_mult * atr_px
                    if new_stop < stop:
                        stop = new_stop
                        n_updates += 1

    if exit_reason is None:
        exit_price = float(w_px[-1])
        exit_ms = int(w_ts[-1])
        exit_reason = "timeout"

    return SimOut(exit_price=exit_price, exit_ms=exit_ms,
                  exit_reason=exit_reason, n_stop_updates=n_updates)


# ---------- runner ----------

def get_trades(account: str, days: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account = ? AND trade_date >= ?
         ORDER BY entry_time_ms
    """, (account, cutoff)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _annotate(trades: list[dict]):
    """Add is_first_trade_of_day + bucket + minutes_after_open columns."""
    seen_dates = set()
    for t in trades:
        entry_dt_utc = datetime.fromtimestamp(t["entry_time_ms"] / 1000, tz=timezone.utc)
        entry_ny = entry_dt_utc - timedelta(hours=4)  # EDT
        open_dt = entry_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        mins_after = (entry_ny - open_dt).total_seconds() / 60.0
        t["mins_after_open"] = round(mins_after, 1)
        t["bucket"] = _bucket_for_minute_after_open(mins_after)
        t["entry_ny"] = entry_ny.strftime("%H:%M")
        t["is_first_trade"] = 0
        key = (t["trade_date"], "first")
        if key not in seen_dates:
            seen_dates.add(key)
            t["is_first_trade"] = 1
    return trades


def run_one_rule(trades: list[dict], tick_cache: dict, *, rule: str,
                 initial_stop_ticks: float = 6, risk_ticks: float = 6,
                 **kwargs):
    """Run a single rule across all trades, return per-trade outcomes."""
    out_rows = []
    for t in trades:
        ticks = tick_cache.get((t["root_symbol"], t["trade_date"]))
        ts = TICK_SIZES.get(t["root_symbol"], 0.25)
        res = simulate(t, ticks, rule=rule,
                       initial_stop_ticks=initial_stop_ticks,
                       tick_size=ts, risk_ticks=risk_ticks, **kwargs)
        if res is None:
            continue
        side = t["side"]
        entry = t["entry_price"]
        if side == "LONG":
            pnl_pts = res.exit_price - entry
        else:
            pnl_pts = entry - res.exit_price
        pnl_ticks = pnl_pts / ts
        orig_pts = float(t["pnl_points"] or 0) / float(t["entry_qty"] or 1)
        orig_ticks = orig_pts / ts
        out_rows.append({
            **t,
            "rule": rule,
            "sim_exit_price": res.exit_price,
            "sim_pnl_ticks": round(pnl_ticks, 2),
            "orig_pnl_ticks": round(orig_ticks, 2),
            "duration_s": (res.exit_ms - t["entry_time_ms"]) / 1000.0,
            "exit_reason": res.exit_reason,
            "n_stop_updates": res.n_stop_updates,
        })
    return out_rows


def summarize_by_bucket(rows: list[dict], rule_label: str):
    """Print per-bucket performance for one rule."""
    print(f"\n--- Rule: {rule_label} ---")
    print(f"  {'bucket':<11} {'n':>4} {'WR':>6} {'E_ticks':>8} {'avg_dur':>8} "
          f"{'stops':>6} {'trails':>6} {'timeout':>8}")
    buckets = {}
    for r in rows:
        buckets.setdefault(r["bucket"], []).append(r)
    # Add "ALL"
    buckets["__ALL__"] = rows
    for b in _BUCKET_ORDER + ["__ALL__"]:
        if b not in buckets:
            continue
        lst = buckets[b]
        if not lst:
            continue
        wins = [r for r in lst if r["sim_pnl_ticks"] > 0]
        wr = len(wins) / len(lst) * 100
        e = sum(r["sim_pnl_ticks"] for r in lst) / len(lst)
        dur = sum(r["duration_s"] for r in lst) / len(lst)
        nstop = sum(1 for r in lst if r["exit_reason"] == "stop")
        ntrail = sum(1 for r in lst if r["exit_reason"] == "trail")
        ntime = sum(1 for r in lst if r["exit_reason"] == "timeout")
        label = "ALL" if b == "__ALL__" else b
        print(f"  {label:<11} {len(lst):>4} {wr:>5.1f}% {e:>+7.2f}t "
              f"{dur:>7.1f}s {nstop:>6} {ntrail:>6} {ntime:>7}")


def summarize_by_first_trade(rows: list[dict], rule_label: str):
    """Compare first-trade-of-day vs not."""
    first = [r for r in rows if r.get("is_first_trade")]
    rest = [r for r in rows if not r.get("is_first_trade")]
    if not first:
        return
    print(f"\n  [{rule_label}]  first-trade-of-day  vs  rest")
    for lab, lst in (("first", first), ("rest", rest)):
        wins = [r for r in lst if r["sim_pnl_ticks"] > 0]
        wr = len(wins) / len(lst) * 100 if lst else 0
        e = sum(r["sim_pnl_ticks"] for r in lst) / len(lst) if lst else 0
        nstop = sum(1 for r in lst if r["exit_reason"] == "stop")
        ntrail = sum(1 for r in lst if r["exit_reason"] == "trail")
        ntime = sum(1 for r in lst if r["exit_reason"] == "timeout")
        # MFE-style: best run achieved (proxy = sim_pnl when timeout means held to end)
        print(f"    {lab:<6}: n={len(lst):>3}  WR={wr:>4.1f}%  E={e:>+6.2f}t  "
              f"stops={nstop}  trails={ntrail}  timeouts={ntime}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--initial_sl", type=float, default=6)
    ap.add_argument("--risk", type=float, default=6)
    ap.add_argument("--max_min", type=int, default=60,
                    help="Max trade duration (minutes)")
    args = ap.parse_args()

    trades = get_trades(args.account, args.days)
    trades = _annotate(trades)
    print(f"Account={args.account}  trades={len(trades)}  "
          f"window=last {args.days} days  initial_SL={args.initial_sl}t")

    # Pre-cache ticks
    tick_cache = {}
    keys = set((t["root_symbol"], t["trade_date"]) for t in trades)
    for sym, date in keys:
        tick_cache[(sym, date)] = _load_ticks(sym, date)
    print(f"Loaded tick data for {len(keys)} (symbol,date) pairs")

    rule_specs = [
        ("fixed",         {"max_duration_minutes": args.max_min}),
        ("be_at_1R",      {"max_duration_minutes": args.max_min}),
        ("wick_1m",       {"max_duration_minutes": args.max_min, "offset_ticks": 1}),
        ("wick_30s",      {"max_duration_minutes": args.max_min, "offset_ticks": 1}),
        ("hl_pivot",      {"max_duration_minutes": args.max_min, "bar_seconds": 60,
                           "lookback": 2, "offset_ticks": 1}),
        ("atr_mult",      {"max_duration_minutes": args.max_min,
                           "atr_n_seconds": 60, "atr_mult": 1.5}),
    ]

    all_results = {}
    for rule, params in rule_specs:
        rows = run_one_rule(trades, tick_cache, rule=rule,
                            initial_stop_ticks=args.initial_sl,
                            risk_ticks=args.risk, **params)
        all_results[rule] = rows

    # Print baseline: actual exits (from journal)
    print("\n========== BASELINE: YOUR ACTUAL EXITS ==========")
    actual_rows = []
    for t in trades:
        ts = TICK_SIZES.get(t["root_symbol"], 0.25)
        pnl_pts = float(t["pnl_points"] or 0) / float(t["entry_qty"] or 1)
        pnl_ticks = pnl_pts / ts
        actual_rows.append({**t, "sim_pnl_ticks": round(pnl_ticks, 2),
                            "duration_s": (t["exit_time_ms"] - t["entry_time_ms"]) / 1000,
                            "exit_reason": "actual", "n_stop_updates": 0})
    summarize_by_bucket(actual_rows, "ACTUAL")
    summarize_by_first_trade(actual_rows, "ACTUAL")

    print("\n========== SIM RESULTS BY RULE ==========")
    rule_totals = []
    for rule, _ in rule_specs:
        rows = all_results[rule]
        summarize_by_bucket(rows, rule)
        summarize_by_first_trade(rows, rule)
        total_e = sum(r["sim_pnl_ticks"] for r in rows) / len(rows) if rows else 0
        total_n = len(rows)
        wr = sum(1 for r in rows if r["sim_pnl_ticks"] > 0) / total_n * 100 if total_n else 0
        rule_totals.append((rule, total_n, wr, total_e))

    print("\n========== RULE LEADERBOARD (overall expectancy) ==========")
    rule_totals.sort(key=lambda x: -x[3])
    actual_e = sum(r["sim_pnl_ticks"] for r in actual_rows) / len(actual_rows) if actual_rows else 0
    actual_wr = sum(1 for r in actual_rows if r["sim_pnl_ticks"] > 0) / len(actual_rows) * 100
    print(f"  {'rule':<12} {'n':>4} {'WR':>6} {'E':>10}  vs actual")
    print(f"  {'actual':<12} {len(actual_rows):>4} {actual_wr:>5.1f}% "
          f"{actual_e:>+8.2f}t   (baseline)")
    for rule, n, wr, e in rule_totals:
        delta = e - actual_e
        print(f"  {rule:<12} {n:>4} {wr:>5.1f}% {e:>+8.2f}t   "
              f"{'+' if delta >= 0 else ''}{delta:+.2f}t")

    # Best rule per bucket
    print("\n========== BEST RULE PER TIME BUCKET (expectancy) ==========")
    print(f"  {'bucket':<11} {'best_rule':<12} {'n':>4} {'WR':>6} {'E':>10}   "
          f"{'2nd':<12} {'E':>8}")
    for b in _BUCKET_ORDER:
        rule_perf = []
        for rule, _ in rule_specs:
            lst = [r for r in all_results[rule] if r["bucket"] == b]
            if not lst:
                continue
            e = sum(r["sim_pnl_ticks"] for r in lst) / len(lst)
            wr = sum(1 for r in lst if r["sim_pnl_ticks"] > 0) / len(lst) * 100
            rule_perf.append((rule, len(lst), wr, e))
        if not rule_perf:
            continue
        rule_perf.sort(key=lambda x: -x[3])
        best = rule_perf[0]
        second = rule_perf[1] if len(rule_perf) > 1 else (None, 0, 0, 0)
        print(f"  {b:<11} {best[0]:<12} {best[1]:>4} {best[2]:>5.1f}% {best[3]:>+8.2f}t   "
              f"{(second[0] or '-'):<12} {second[3]:>+7.2f}t")


if __name__ == "__main__":
    main()
