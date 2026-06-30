"""
Identify trades from the last N days where the user took profit too early.

For each closed trade compute:
  - pnl_ticks (actual)
  - mfe_ticks (max favorable excursion in-trade)
  - leftover_ticks = mfe - pnl  (how much was left on the table)
  - post_exit_mfe_5min_ticks  (continuation after exit, 5-min window)

Then:
  1) Sort winners by leftover_ticks DESC -> trades where you scalped a runner
  2) Sort by (pnl + post_exit_mfe_5min) DESC -> trades where exit timing missed a big move
  3) Group by time-of-day bucket to see WHEN early-exits happen most
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


def analyze(trade, ticks):
    if ticks is None or len(ticks["ts_ns"]) == 0:
        return None
    side = trade["side"]
    entry = trade["entry_price"]
    entry_ms = trade["entry_time_ms"]
    exit_ms = trade["exit_time_ms"]
    if not exit_ms:
        return None
    ts = ticks["ts_ns"] // 1_000_000
    px = ticks["price"]
    in_mask = (ts >= entry_ms) & (ts <= exit_ms)
    in_px = px[in_mask]
    in_ts = ts[in_mask]
    if len(in_px) == 0:
        return None
    sym = trade["root_symbol"]
    tick_size = TICK_SIZES.get(sym, 0.25)

    if side == "LONG":
        mfe_pts = float(in_px.max() - entry)
        mfe_idx = int(np.argmax(in_px))
    else:
        mfe_pts = float(entry - in_px.min())
        mfe_idx = int(np.argmin(in_px))
    t_to_mfe_s = (int(in_ts[mfe_idx]) - entry_ms) / 1000.0

    # 5 min after exit
    post_mask = (ts > exit_ms) & (ts <= exit_ms + 5 * 60 * 1000)
    post_px = px[post_mask]
    if len(post_px) > 0:
        if side == "LONG":
            post_mfe_pts = float(post_px.max() - entry)
        else:
            post_mfe_pts = float(entry - post_px.min())
    else:
        post_mfe_pts = 0.0

    # 10 min after entry total reach
    full_mask = (ts >= entry_ms) & (ts <= entry_ms + 10 * 60 * 1000)
    full_px = px[full_mask]
    if len(full_px) > 0:
        if side == "LONG":
            full_mfe_pts = float(full_px.max() - entry)
        else:
            full_mfe_pts = float(entry - full_px.min())
    else:
        full_mfe_pts = mfe_pts

    pnl_pts = float(trade["pnl_points"] or 0) / float(trade["entry_qty"] or 1)

    entry_dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc) - timedelta(hours=4)
    exit_dt = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc) - timedelta(hours=4)

    return {
        "id": trade["id"],
        "date": trade["trade_date"],
        "entry_ny": entry_dt.strftime("%H:%M:%S"),
        "exit_ny": exit_dt.strftime("%H:%M:%S"),
        "side": side,
        "symbol": sym,
        "entry": entry,
        "exit": trade["exit_price"],
        "pnl_ticks": round(pnl_pts / tick_size, 1),
        "mfe_ticks": round(mfe_pts / tick_size, 1),
        "leftover_ticks": round((mfe_pts - pnl_pts) / tick_size, 1),
        "t_to_mfe_s": round(t_to_mfe_s, 1),
        "post5_mfe_ticks": round(post_mfe_pts / tick_size, 1),
        "full10_mfe_ticks": round(full_mfe_pts / tick_size, 1),
        "duration_s": round((exit_ms - entry_ms) / 1000, 1),
        "win": 1 if pnl_pts > 0 else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account = ? AND trade_date >= ?
         ORDER BY entry_time_ms
    """, (args.account, cutoff)).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Loaded {len(trades)} trades for account={args.account} since {cutoff}\n")

    cache = {}
    results = []
    for t in trades:
        k = (t["root_symbol"], t["trade_date"])
        if k not in cache:
            cache[k] = _load_ticks(t["root_symbol"], t["trade_date"])
        r = analyze(t, cache[k])
        if r:
            results.append(r)

    # ============ 1. Winners ranked by leftover ============
    winners = [r for r in results if r["win"]]
    winners.sort(key=lambda x: -x["leftover_ticks"])
    print(f"========== TOP {args.top} WINNERS WITH LEFTOVER TICKS ==========")
    print(f"(took TP X ticks before the MFE; sorted by 'leftover_ticks' DESC)\n")
    print(f"{'date':<11} {'entry':>9} {'exit':>9} {'side':<6} "
          f"{'pnl':>5} {'mfe':>5} {'left':>5} {'tMFE':>6} {'dur':>6} {'post5':>6}")
    for r in winners[:args.top]:
        print(f"{r['date']:<11} {r['entry_ny']:>9} {r['exit_ny']:>9} {r['side']:<6} "
              f"{r['pnl_ticks']:>+5.1f} {r['mfe_ticks']:>5.1f} {r['leftover_ticks']:>5.1f} "
              f"{r['t_to_mfe_s']:>5.1f}s {r['duration_s']:>5.1f}s {r['post5_mfe_ticks']:>+6.1f}")

    # ============ 2. All trades ranked by potential we missed ============
    # potential = full10_mfe (regardless of win/loss). Trades where price reached
    # a big MFE within 10 min but we exited way before.
    print(f"\n========== TOP {args.top} TRADES BY 10-MIN POTENTIAL VS REALIZED ==========")
    print("(trades where price reached +N ticks in 10 min but we exited before)\n")
    by_pot = sorted(results, key=lambda x: -(x["full10_mfe_ticks"] - x["pnl_ticks"]))
    print(f"{'date':<11} {'entry':>9} {'exit':>9} {'side':<6} "
          f"{'pnl':>5} {'full10':>7} {'gap':>5} {'dur':>6}")
    for r in by_pot[:args.top]:
        gap = r["full10_mfe_ticks"] - r["pnl_ticks"]
        print(f"{r['date']:<11} {r['entry_ny']:>9} {r['exit_ny']:>9} {r['side']:<6} "
              f"{r['pnl_ticks']:>+5.1f} {r['full10_mfe_ticks']:>+7.1f} {gap:>+5.1f} "
              f"{r['duration_s']:>5.1f}s")

    # ============ 3. Summary: leftover by time-of-day bucket ============
    def bucket(t_str):
        h, m, s = map(int, t_str.split(":"))
        mins = (h * 60 + m) - (9 * 60 + 30)
        if mins < 0: return "pre_open"
        if mins < 15: return "open_15"
        if mins < 30: return "open_30"
        if mins < 60: return "morning_1"
        if mins < 120: return "morning_2"
        if mins < 210: return "mid"
        return "afternoon"

    print("\n========== LEFTOVER TICKS BY TIME-OF-DAY (winners only) ==========")
    by_bucket = {}
    for r in winners:
        by_bucket.setdefault(bucket(r["entry_ny"]), []).append(r)
    print(f"{'bucket':<11} {'n':>4} {'avg_pnl':>8} {'avg_mfe':>8} {'avg_left':>9} {'avg_post5':>10}")
    order = ["pre_open", "open_15", "open_30", "morning_1", "morning_2", "mid", "afternoon"]
    for b in order:
        if b not in by_bucket:
            continue
        lst = by_bucket[b]
        avg_pnl = sum(r["pnl_ticks"] for r in lst) / len(lst)
        avg_mfe = sum(r["mfe_ticks"] for r in lst) / len(lst)
        avg_left = sum(r["leftover_ticks"] for r in lst) / len(lst)
        avg_post = sum(r["post5_mfe_ticks"] for r in lst) / len(lst)
        print(f"{b:<11} {len(lst):>4} {avg_pnl:>+7.1f}t {avg_mfe:>+7.1f}t "
              f"{avg_left:>+8.1f}t {avg_post:>+9.1f}t")

    # ============ 4. Distribution of leftover ticks (winners) ============
    print("\n========== LEFTOVER DISTRIBUTION (winners only) ==========")
    if winners:
        lefts = sorted(r["leftover_ticks"] for r in winners)
        for p in (25, 50, 75, 90):
            idx = min(int(len(lefts) * p / 100), len(lefts) - 1)
            print(f"  P{p}: {lefts[idx]:>+5.1f}t")
        print(f"  max: {lefts[-1]:+.1f}t  mean: {sum(lefts)/len(lefts):+.1f}t")
        # Buckets
        for lo, hi in ((0, 4), (4, 8), (8, 12), (12, 20), (20, 999)):
            n = sum(1 for x in lefts if lo <= x < hi)
            print(f"  leftover {lo:>2}..{hi:<3}t: {n:>3}/{len(lefts)} "
                  f"({n/len(lefts)*100:.1f}%)")

    # ============ 5. Total opportunity cost ============
    total_pnl = sum(r["pnl_ticks"] for r in results)
    total_mfe = sum(r["mfe_ticks"] for r in results)
    total_full10 = sum(max(r["full10_mfe_ticks"], r["pnl_ticks"]) for r in results)
    print(f"\n========== OPPORTUNITY COST (all {len(results)} trades) ==========")
    print(f"  Realized total:           {total_pnl:>+7.1f}t  (${total_pnl * 12.50:>+.0f} ES, ${total_pnl * 1.25:>+.0f} MES)")
    print(f"  If exited at MFE always:  {total_mfe:>+7.1f}t  (impossible but ceiling)")
    print(f"  If held 10 min, took max: {total_full10:>+7.1f}t  (more realistic ceiling)")
    print(f"  Gap MFE - realized:       {total_mfe - total_pnl:>+7.1f}t  "
          f"(${(total_mfe - total_pnl) * 12.50:>+.0f} ES)")
    print(f"  Gap full10 - realized:    {total_full10 - total_pnl:>+7.1f}t  "
          f"(${(total_full10 - total_pnl) * 12.50:>+.0f} ES)")


if __name__ == "__main__":
    main()
