"""
Historical tape scanner — for every 15s bar across the trading days in the
history, evaluate the A+ quality grade (same 5 checks as aplus_detector.py) and
identify ALL moments where the setup was A+ or A, regardless of whether a trade
was taken.

Outputs:
  1) Count of A+/A bars per day vs how many trades you took that day
  2) For each A+ bar where NO trade was taken within ±60s: a "missed setup"
  3) Simulated P&L if you had taken every A+ entry (with 6t SL, 30min max)
     vs your actual P&L
  4) Time-of-day distribution of A+ bars (where do they cluster?)

Focuses on:
  - Chosen account + symbol (CLI args), LONG bias
  - Trading window: 09:30-13:00 NY (your active window)
  - 15-second bars built from tick parquet/SCID data
"""
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


# --- A+ thresholds (same as aplus_detector.py — LONG bias) ---
Q1_HTF_TREND_MIN = 3.0       # 15m trend in points (sign = LONG = positive)
Q2_1M_TREND_MAX = 1.0        # 1m trend in dir (LONG => <= +1.0 pts)
Q2_1M_CLOSE_POS_MAX = 0.80   # 1m close at most 80% of bar range
Q5_1M_VOL_MIN = 3500
Q5_15S_DELTA_MIN = 100

GOOD_TIME_START_HR = 10
GOOD_TIME_END_HR = 13
BAD_DAYS = {"Mon"}

# --- Sim params ---
SL_TICKS = 6
TICK_SIZE = 0.25
POINT_VALUE = 50.0
SIM_MAX_DURATION_MIN = 30
SIM_TRADE_LIFETIME_MIN = 10  # how far ahead to look for MFE
NEAR_TAKEN_WINDOW_SEC = 120  # if a real trade entered within ±this of an A+ bar, count as "taken"


@dataclass
class Bar:
    ts_ms: int           # bar START
    high: float
    low: float
    close: float
    volume: int
    bid_vol: int
    ask_vol: int


def build_15s_bars(ts_ms_arr, px, vol, av, bv, start_ms, end_ms):
    """Aggregate ticks into 15-second bars between start_ms and end_ms (inclusive)."""
    if len(ts_ms_arr) == 0:
        return []
    bar_ms = 15_000
    m = (ts_ms_arr >= start_ms) & (ts_ms_arr <= end_ms)
    ts = ts_ms_arr[m]
    p = px[m]
    v = vol[m]
    a = av[m]
    b = bv[m]
    if len(ts) == 0:
        return []
    bar_id = (ts - start_ms) // bar_ms
    bars = []
    for bid in np.unique(bar_id):
        sel = bar_id == bid
        bp = p[sel]
        bars.append(Bar(
            ts_ms=int(start_ms + int(bid) * bar_ms),
            high=float(bp.max()), low=float(bp.min()),
            close=float(bp[-1]),
            volume=int(v[sel].sum()),
            bid_vol=int(b[sel].sum()),
            ask_vol=int(a[sel].sum()),
        ))
    return bars


def evaluate_bars_for_aplus(bars):
    """For each bar index, return (grade_score, q_checks_tuple). LONG bias.

    Uses look-back of:
        15m = 60 bars
        1m  = 4 bars (current + 3 prior)
        15s = current bar (delta)
    """
    out = []
    n_15m = 60
    n_1m = 4
    for i, bar in enumerate(bars):
        # Q1: HTF trend
        if i >= n_15m:
            htf_trend = bars[i].close - bars[i - n_15m].close
        else:
            htf_trend = 0.0
        q1 = htf_trend >= Q1_HTF_TREND_MIN

        # Q2: chase check
        start_1m = max(0, i - n_1m + 1)
        slice_1m = bars[start_1m:i + 1]
        if len(slice_1m) < 2:
            q2 = True
            one_min_trend = 0
            cp1m = 0.5
        else:
            one_min_trend = slice_1m[-1].close - slice_1m[0].close
            hi1m = max(b.high for b in slice_1m)
            lo1m = min(b.low for b in slice_1m)
            rng1m = hi1m - lo1m
            cp1m = (slice_1m[-1].close - lo1m) / rng1m if rng1m > 0 else 0.5
            q2 = (one_min_trend <= Q2_1M_TREND_MAX) and (cp1m <= Q2_1M_CLOSE_POS_MAX)

        # Q5: vol + delta
        vol_1m = sum(b.volume for b in slice_1m)
        delta_15s = bar.ask_vol - bar.bid_vol
        q5 = (vol_1m >= Q5_1M_VOL_MIN) and (delta_15s >= Q5_15S_DELTA_MIN)

        # Q3 + Q4 done at day level outside this fn
        q1234_partial = (q1, q2, vol_1m, delta_15s)
        out.append({
            "i": i, "q1": q1, "q2": q2, "q5": q5,
            "htf_trend": htf_trend, "one_min_trend": one_min_trend,
            "cp1m": cp1m, "vol_1m": vol_1m, "delta_15s": delta_15s,
        })
    return out


def simulate_long_trade(bars, entry_idx, sl_ticks=SL_TICKS,
                        max_minutes=SIM_TRADE_LIFETIME_MIN):
    """Enter LONG at the close of bar[entry_idx], simulate to SL hit or max time.

    Returns (exit_idx, exit_price, pnl_ticks, exit_reason).
    """
    if entry_idx >= len(bars):
        return None
    entry = bars[entry_idx].close
    stop = entry - sl_ticks * TICK_SIZE
    end_ms = bars[entry_idx].ts_ms + max_minutes * 60_000
    mfe = 0.0
    for j in range(entry_idx + 1, len(bars)):
        bar = bars[j]
        if bar.ts_ms > end_ms:
            return (j, bar.close, (bar.close - entry) / TICK_SIZE, "timeout", mfe)
        if bar.low <= stop:
            pnl = (stop - entry) / TICK_SIZE
            return (j, stop, pnl, "stop", mfe)
        mfe = max(mfe, (bar.high - entry) / TICK_SIZE)
    if entry_idx + 1 >= len(bars):
        return None
    last = bars[-1]
    return (len(bars) - 1, last.close, (last.close - entry) / TICK_SIZE, "end_of_data", mfe)


def get_trading_days(account="", symbol="ES"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT trade_date FROM trades
        WHERE account=? AND root_symbol=? AND is_open=0
        ORDER BY trade_date
    """, (account, symbol)).fetchall()
    conn.close()
    return [r["trade_date"] for r in rows]


def get_trades_on(date_str, account="", symbol="ES"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, side, entry_price, exit_price, entry_qty, entry_time_ms,
               exit_time_ms, net_pnl, pnl_points
          FROM trades
         WHERE account=? AND root_symbol=? AND trade_date=? AND is_open=0
         ORDER BY entry_time_ms
    """, (account, symbol, date_str)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--symbol", default="ES")
    ap.add_argument("--days_back", type=int, default=999)
    ap.add_argument("--print_missed", type=int, default=25,
                    help="Print top N missed A+ moments per day (0 = skip)")
    args = ap.parse_args()

    dates = get_trading_days(args.account, args.symbol)
    cutoff = (datetime.now() - timedelta(days=args.days_back)).strftime("%Y-%m-%d")
    dates = [d for d in dates if d >= cutoff]
    print(f"Scanning {len(dates)} trading days for {args.account} {args.symbol} since {cutoff}")

    total_aplus_bars = 0
    total_a_bars = 0
    total_taken_aplus = 0
    total_missed_aplus = 0
    sim_pnl_ticks = 0.0
    sim_n_trades = 0
    sim_n_wins = 0
    sim_mfe_total = 0.0
    bucket_aplus = defaultdict(int)  # hour_ny -> count
    per_day = []

    for date_str in dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dow = dt.strftime("%a")
        if dow in BAD_DAYS:
            # Skip Mondays for LONG bias
            continue

        ticks = _load_ticks(args.symbol, date_str)
        if ticks is None or len(ticks["ts_ns"]) == 0:
            continue
        # Active window: 09:30-13:00 NY = 13:30-17:00 UTC (EDT) / 14:30-18:00 UTC (EST)
        # We use the calendar of the actual entry times. The tape itself is UTC.
        # Determine the day's session_start_ms: 09:30 NY -> UTC depending on DST.
        # Simpler: use 13:30-17:00 UTC for May/Jun (EDT = UTC-4).
        # For correctness, use the day's date in NY and convert.
        ny_open = datetime(dt.year, dt.month, dt.day, 9, 30)
        ny_close = datetime(dt.year, dt.month, dt.day, 13, 0)
        # EDT in May-June: UTC = NY + 4
        utc_open = ny_open + timedelta(hours=4)
        utc_close = ny_close + timedelta(hours=4)
        start_ms = int(utc_open.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(utc_close.replace(tzinfo=timezone.utc).timestamp() * 1000)

        ts_ms = ticks["ts_ns"] // 1_000_000
        px = ticks["price"].astype(np.float64)
        vol = ticks["volume"]
        av = ticks["ask_volume"]
        bv = ticks["bid_volume"]
        bars = build_15s_bars(ts_ms, px, vol, av, bv, start_ms, end_ms)
        if len(bars) < 60:
            continue

        evals = evaluate_bars_for_aplus(bars)
        actual_trades = get_trades_on(date_str, args.account, args.symbol)
        actual_entry_ms = [t["entry_time_ms"] for t in actual_trades if t["side"] == "LONG"]

        # Find A+ and A bars
        aplus_bars = []
        a_bars = []
        for ev in evals:
            bar = bars[ev["i"]]
            score = (1 if ev["q1"] else 0) + (1 if ev["q2"] else 0) + (1 if ev["q5"] else 0)
            # +Q3 (time) +Q4 (day) — we already filtered Mon at top; time check below
            hr_ny = ((bar.ts_ms // 1000 // 3600) - 4) % 24  # crude NY hour (EDT)
            q3 = GOOD_TIME_START_HR <= hr_ny < GOOD_TIME_END_HR
            q4 = True  # already skipped Monday
            total_score = score + (1 if q3 else 0) + (1 if q4 else 0)
            if total_score == 5:
                aplus_bars.append(ev["i"])
                bucket_aplus[hr_ny] += 1
            elif total_score == 4:
                a_bars.append(ev["i"])

        # Cross-reference with actual trades: "taken" if any real entry within window
        taken_aplus = 0
        missed_aplus_idx = []
        for bi in aplus_bars:
            bar_ts = bars[bi].ts_ms
            taken = any(abs(et - bar_ts) <= NEAR_TAKEN_WINDOW_SEC * 1000
                        for et in actual_entry_ms)
            if taken:
                taken_aplus += 1
            else:
                missed_aplus_idx.append(bi)

        # Simulate LONG entries on every A+ bar (one trade per bar, no overlap policy:
        # don't enter if a sim trade is still open). For honesty, also avoid stacking
        # within same minute.
        sim_results = []
        next_allowed_idx = 0
        for bi in aplus_bars:
            if bi < next_allowed_idx:
                continue
            res = simulate_long_trade(bars, bi)
            if res is None:
                continue
            exit_idx, _, pnl_ticks, reason, mfe = res
            sim_results.append((bi, exit_idx, pnl_ticks, reason, mfe))
            next_allowed_idx = exit_idx + 1
            sim_n_trades += 1
            sim_pnl_ticks += pnl_ticks
            sim_mfe_total += mfe
            if pnl_ticks > 0:
                sim_n_wins += 1

        total_aplus_bars += len(aplus_bars)
        total_a_bars += len(a_bars)
        total_taken_aplus += taken_aplus
        total_missed_aplus += len(missed_aplus_idx)

        per_day.append({
            "date": date_str,
            "dow": dow,
            "n_bars": len(bars),
            "aplus": len(aplus_bars),
            "a_bars": len(a_bars),
            "taken": taken_aplus,
            "missed": len(missed_aplus_idx),
            "n_actual_trades": len(actual_trades),
            "actual_long_trades": sum(1 for t in actual_trades if t["side"] == "LONG"),
            "sim_pnl_ticks": sum(r[2] for r in sim_results),
            "sim_n_trades": len(sim_results),
            "missed_idx": missed_aplus_idx,
            "bars": bars,
        })

    # ============ REPORT ============
    print()
    print("========== PER-DAY SUMMARY (Mondays skipped, LONG bias only) ==========")
    print(f"{'date':<11} {'dow':<4} {'n_bars':>6} {'aplus':>5} {'taken':>5} "
          f"{'missed':>6} {'actL':>5} {'all':>4}  {'sim_pnl_t':>10}")
    for d in per_day:
        print(f"{d['date']:<11} {d['dow']:<4} {d['n_bars']:>6} {d['aplus']:>5} "
              f"{d['taken']:>5} {d['missed']:>6} {d['actual_long_trades']:>5} "
              f"{d['n_actual_trades']:>4}  {d['sim_pnl_ticks']:>+9.1f}t")

    print()
    print("========== AGGREGATE ==========")
    print(f"  A+ bars found:    {total_aplus_bars}")
    print(f"  A bars found:     {total_a_bars}")
    print(f"  A+ bars TAKEN (real trade within ±{NEAR_TAKEN_WINDOW_SEC}s): {total_taken_aplus}")
    print(f"  A+ bars MISSED:   {total_missed_aplus}")
    if total_aplus_bars:
        print(f"  Take rate:        {total_taken_aplus / total_aplus_bars * 100:.1f}%")

    print()
    print("========== SIM: LONG @ every A+ bar, 6t SL, 10min max ==========")
    if sim_n_trades:
        wr = sim_n_wins / sim_n_trades * 100
        avg = sim_pnl_ticks / sim_n_trades
        avg_mfe = sim_mfe_total / sim_n_trades
        usd = sim_pnl_ticks * TICK_SIZE * POINT_VALUE
        print(f"  sim trades:       {sim_n_trades}")
        print(f"  win rate:         {wr:.1f}%")
        print(f"  total ticks:      {sim_pnl_ticks:+.1f}t")
        print(f"  total $$$:        ${usd:+.2f}")
        print(f"  avg ticks/trade:  {avg:+.2f}t")
        print(f"  avg MFE/trade:    {avg_mfe:+.2f}t  (your TP if structure-trailed)")

    print()
    print("========== A+ BAR DISTRIBUTION BY NY HOUR ==========")
    for h in sorted(bucket_aplus):
        print(f"  {h:>2}:00 - {h:>2}:59  count={bucket_aplus[h]}")

    # ============ MISSED OPPORTUNITIES (top N) ============
    if args.print_missed > 0:
        print()
        print(f"========== MISSED A+ BARS (top {args.print_missed} per day) ==========")
        for d in per_day:
            if not d["missed_idx"]:
                continue
            print(f"\n  {d['date']} ({d['dow']}) — {len(d['missed_idx'])} missed A+ bars")
            for bi in d["missed_idx"][:args.print_missed]:
                bar = d["bars"][bi]
                bar_ny = datetime.fromtimestamp(bar.ts_ms / 1000, tz=timezone.utc) - timedelta(hours=4)
                # Simulate the missed entry
                res = simulate_long_trade(d["bars"], bi)
                if res is None:
                    continue
                _, _, pnl, reason, mfe = res
                print(f"    {bar_ny.strftime('%H:%M:%S')}  close={bar.close:.2f}  "
                      f"sim_pnl={pnl:+.1f}t  MFE={mfe:+.1f}t  ({reason})")


if __name__ == "__main__":
    main()
