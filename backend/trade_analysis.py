"""
Trade analysis for pullback/breakout scalping.

For each closed trade in the last N days (filtered to a specific account),
compute:
  - MAE / MFE in ticks (max adverse / favorable excursion from entry)
  - Time to MAE, time to MFE
  - R-multiple touched (1R, 1.5R, 2R, 3R, 4R) where R = configurable risk
  - Did MAE happen BEFORE MFE? (signals "scalp now" vs "hold")
  - Pre-entry tape context: trend/range/delta/volume/speed across 5/15/60/300s
  - Position in 15-min swing at entry (pullback quality proxy)
  - 5-min post-exit MFE/MAE (continuation vs "left on the table")

Outputs CSV + console summary slicing winners vs losers by setup features.
"""
import argparse
import csv
import sqlite3
from datetime import datetime, timezone, timedelta

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


def _r_unit(symbol: str, risk_ticks: float) -> float:
    return risk_ticks * TICK_SIZES.get(symbol, 0.25)


def analyze_trade(trade: dict, ticks: dict, *, risk_ticks: float = 6) -> dict | None:
    if ticks is None or len(ticks["ts_ns"]) == 0:
        return None

    side = trade["side"]
    entry = trade["entry_price"]
    entry_ms = trade["entry_time_ms"]
    exit_ms = trade["exit_time_ms"]
    exit_price = trade["exit_price"]
    if exit_ms is None or exit_price is None:
        return None

    ts_ms = ticks["ts_ns"] // 1_000_000
    px = ticks["price"]
    av = ticks.get("ask_volume", np.zeros_like(ticks["volume"]))
    bv = ticks.get("bid_volume", np.zeros_like(ticks["volume"]))
    vol = ticks["volume"]

    symbol = trade["root_symbol"]
    tick_size = TICK_SIZES.get(symbol, 0.25)

    in_mask = (ts_ms >= entry_ms) & (ts_ms <= exit_ms)
    in_ts = ts_ms[in_mask]
    in_px = px[in_mask]
    if len(in_ts) == 0:
        return None

    if side == "LONG":
        mfe_idx = int(np.argmax(in_px))
        mae_idx = int(np.argmin(in_px))
        mfe_pts = float(in_px[mfe_idx] - entry)
        mae_pts = float(in_px[mae_idx] - entry)
    else:
        mfe_idx = int(np.argmin(in_px))
        mae_idx = int(np.argmax(in_px))
        mfe_pts = float(entry - in_px[mfe_idx])
        mae_pts = float(entry - in_px[mae_idx])

    mfe_ticks = mfe_pts / tick_size
    mae_ticks = mae_pts / tick_size
    t_to_mfe_s = (int(in_ts[mfe_idx]) - entry_ms) / 1000.0
    t_to_mae_s = (int(in_ts[mae_idx]) - entry_ms) / 1000.0
    mae_before_mfe = t_to_mae_s < t_to_mfe_s

    r_unit_px = _r_unit(symbol, risk_ticks)
    r_targets = {}
    for r in (1.0, 1.5, 2.0, 3.0, 4.0):
        target = entry + r * r_unit_px if side == "LONG" else entry - r * r_unit_px
        hit_mask = (in_px >= target) if side == "LONG" else (in_px <= target)
        if hit_mask.any():
            hit_idx = int(np.argmax(hit_mask))
            r_targets[f"hit_{r}R"] = 1
            r_targets[f"t_to_{r}R_s"] = (int(in_ts[hit_idx]) - entry_ms) / 1000.0
        else:
            r_targets[f"hit_{r}R"] = 0
            r_targets[f"t_to_{r}R_s"] = None

    # Post-exit 5 min
    post_end_ms = exit_ms + 5 * 60 * 1000
    post_mask = (ts_ms > exit_ms) & (ts_ms <= post_end_ms)
    post_px = px[post_mask]
    if len(post_px) > 0:
        if side == "LONG":
            post_mfe_pts = float(post_px.max() - entry)
            post_mae_pts = float(post_px.min() - entry)
        else:
            post_mfe_pts = float(entry - post_px.min())
            post_mae_pts = float(entry - post_px.max())
    else:
        post_mfe_pts = post_mae_pts = 0.0

    ctx = {}
    for win_s in (5, 15, 60, 300):
        m = (ts_ms >= entry_ms - win_s * 1000) & (ts_ms < entry_ms)
        p_px = px[m]; p_av = av[m]; p_bv = bv[m]; p_vol = vol[m]
        if len(p_px) < 2:
            ctx[f"trend_{win_s}s_pts"] = 0.0
            ctx[f"range_{win_s}s_pts"] = 0.0
            ctx[f"delta_{win_s}s"] = 0
            ctx[f"volume_{win_s}s"] = 0
            ctx[f"speed_{win_s}s_tps"] = 0.0
            continue
        ctx[f"trend_{win_s}s_pts"] = float(p_px[-1] - p_px[0])
        ctx[f"range_{win_s}s_pts"] = float(p_px.max() - p_px.min())
        ctx[f"delta_{win_s}s"] = int(p_av.sum() - p_bv.sum())
        ctx[f"volume_{win_s}s"] = int(p_vol.sum())
        ctx[f"speed_{win_s}s_tps"] = len(p_px) / win_s

    # 15-min swing position
    win_s = 900
    m = (ts_ms >= entry_ms - win_s * 1000) & (ts_ms < entry_ms)
    p_px = px[m]
    if len(p_px) >= 5:
        hi = float(p_px.max()); lo = float(p_px.min()); rng = hi - lo
        ctx[f"swing_range_{win_s}s_pts"] = rng
        if rng > 0:
            if side == "LONG":
                ctx["pullback_pct"] = (hi - entry) / rng
            else:
                ctx["pullback_pct"] = (entry - lo) / rng
        else:
            ctx["pullback_pct"] = 0.0
    else:
        ctx[f"swing_range_{win_s}s_pts"] = 0.0
        ctx["pullback_pct"] = 0.0

    pnl_pts_actual = float(trade["pnl_points"] or 0) / float(trade["entry_qty"] or 1)
    pnl_ticks = pnl_pts_actual / tick_size
    win = 1 if pnl_pts_actual > 0 else 0
    fake_pullback = 1 if (mae_before_mfe and not r_targets["hit_1.0R"]) else 0

    # Time of day (NY)
    entry_dt_utc = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc)
    entry_ny = entry_dt_utc - timedelta(hours=4)  # EDT (May)
    hour_ny = entry_ny.hour + entry_ny.minute / 60.0

    return {
        "trade_id": trade["id"],
        "date": trade["trade_date"],
        "symbol": symbol,
        "side": side,
        "entry_ny": entry_ny.strftime("%Y-%m-%d %H:%M:%S"),
        "hour_ny": round(hour_ny, 2),
        "entry_price": entry,
        "exit_price": exit_price,
        "duration_s": (exit_ms - entry_ms) / 1000.0,
        "pnl_ticks": round(pnl_ticks, 2),
        "win": win,
        "mfe_ticks": round(mfe_ticks, 2),
        "mae_ticks": round(mae_ticks, 2),
        "t_to_mfe_s": round(t_to_mfe_s, 1),
        "t_to_mae_s": round(t_to_mae_s, 1),
        "mae_before_mfe": int(mae_before_mfe),
        "fake_pullback": fake_pullback,
        "post_mfe_pts": round(post_mfe_pts, 2),
        "post_mae_pts": round(post_mae_pts, 2),
        **r_targets,
        **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in ctx.items()},
    }


def run_analysis(*, account: str, days: int, out_csv: str, risk_ticks: float):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
                  entry_time_ms, exit_time_ms, trade_date, pnl_points, duration_seconds
             FROM trades
            WHERE is_open=0 AND exit_time_ms IS NOT NULL
              AND account = ? AND trade_date >= ?
            ORDER BY entry_time_ms""", (account, cutoff)
    ).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Loaded {len(trades)} trades for account={account} since {cutoff}")

    tick_cache = {}
    results = []
    for t in trades:
        key = (t["root_symbol"], t["trade_date"])
        if key not in tick_cache:
            tick_cache[key] = _load_ticks(t["root_symbol"], t["trade_date"])
        r = analyze_trade(t, tick_cache[key], risk_ticks=risk_ticks)
        if r:
            results.append(r)
    print(f"Analyzed {len(results)} trades (others lacked tick data)")

    if results:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"Wrote {out_csv}")
    return results


def _fmt_grp(label, lst, tick_size=0.25):
    import statistics as st
    if not lst:
        print(f"  {label:<40}: n=0")
        return
    wins = [r for r in lst if r["win"]]
    wr = len(wins) / len(lst)
    avg_pnl = st.mean(r["pnl_ticks"] for r in lst)
    avg_mfe = st.mean(r["mfe_ticks"] for r in lst)
    avg_mae = st.mean(r["mae_ticks"] for r in lst)
    avg_t_mfe = st.mean(r["t_to_mfe_s"] for r in lst)
    avg_dur = st.mean(r["duration_s"] for r in lst)
    # Expectancy in ticks (per trade)
    print(f"  {label:<40}: n={len(lst):>3} wr={wr*100:>4.1f}% "
          f"E={avg_pnl:>+6.2f}t mfe={avg_mfe:>5.1f}t mae={avg_mae:>+5.1f}t "
          f"tMFE={avg_t_mfe:>5.1f}s dur={avg_dur:>5.1f}s")


def print_summary(results: list[dict], risk_ticks: float = 6):
    import statistics as st
    n = len(results)
    if n == 0:
        return

    winners = [r for r in results if r["win"]]
    losers = [r for r in results if not r["win"]]
    longs = [r for r in results if r["side"] == "LONG"]
    shorts = [r for r in results if r["side"] == "SHORT"]

    print("\n========== OVERALL ==========")
    _fmt_grp("ALL", results)
    _fmt_grp("WINNERS", winners)
    _fmt_grp("LOSERS", losers)

    print("\n========== BY SIDE ==========")
    _fmt_grp("LONG", longs)
    _fmt_grp("SHORT", shorts)

    print("\n========== BY SYMBOL ==========")
    for s in sorted(set(r["symbol"] for r in results)):
        _fmt_grp(s, [r for r in results if r["symbol"] == s])

    # Time of day (NY)
    print("\n========== BY HOUR (NY local) ==========")
    by_hour = {}
    for r in results:
        h = int(r["hour_ny"])
        by_hour.setdefault(h, []).append(r)
    for h in sorted(by_hour):
        _fmt_grp(f"{h:02d}:00", by_hour[h])

    # MFE distribution for winners
    print("\n========== MFE DISTRIBUTION (Winners only, ticks) ==========")
    if winners:
        mfes = sorted(r["mfe_ticks"] for r in winners)
        for p in (10, 25, 50, 75, 90):
            idx = min(int(len(mfes) * p / 100), len(mfes) - 1)
            print(f"  P{p:>2}: {mfes[idx]:>6.1f}t")
        print(f"  max={mfes[-1]:.1f}t   mean={sum(mfes)/len(mfes):.1f}t")
        for tp in (8, 12, 16, 20, 25, 30, 40):
            past = sum(1 for m in mfes if m >= tp)
            print(f"   >= {tp:>2}t: {past:>3}/{len(mfes)} ({past/len(mfes)*100:.1f}%)")

    # R-multiple touch rates
    print(f"\n========== R-MULTIPLE TOUCH RATES (R = {risk_ticks}t) ==========")
    for r_lev in (1.0, 1.5, 2.0, 3.0, 4.0):
        k = f"hit_{r_lev}R"
        h_all = sum(1 for r in results if r.get(k))
        h_l = sum(1 for r in longs if r.get(k))
        h_s = sum(1 for r in shorts if r.get(k))
        print(f"  {r_lev}R touched: ALL {h_all:>3}/{n} ({h_all/n*100:>5.1f}%)  "
              f"LONG {h_l}/{len(longs)}  SHORT {h_s}/{len(shorts) if shorts else 0}")

    # MAE-first analysis (THIS is your "fake pullback" diagnostic)
    print("\n========== PULLBACK QUALITY ==========")
    fake = [r for r in results if r["fake_pullback"]]
    clean = [r for r in results if not r["fake_pullback"]]
    print(f"  Fake-pullback rate (MAE-first AND never hit 1R): {len(fake)}/{n} ({len(fake)/n*100:.1f}%)")
    _fmt_grp("FAKE pullbacks", fake)
    _fmt_grp("CLEAN entries", clean)

    mae_first = [r for r in results if r["mae_before_mfe"]]
    mfe_first = [r for r in results if not r["mae_before_mfe"]]
    print()
    _fmt_grp("MAE-first (drilled against me first)", mae_first)
    _fmt_grp("MFE-first (worked immediately)", mfe_first)

    # Post-exit behavior — answers trail/hold question
    print("\n========== POST-EXIT (5 min after exit) ==========")
    if winners:
        avg_extra = st.mean(r["post_mfe_pts"] for r in winners)
        cont = sum(1 for r in winners if r["post_mfe_pts"] > r["mfe_ticks"] * 0.25)
        print(f"  WINNERS: post-exit further-MFE avg {avg_extra/0.25:>5.1f}t  "
              f"({cont}/{len(winners)} continued further)")
    if losers:
        avg_recov = st.mean(r["post_mfe_pts"] for r in losers)
        recov = sum(1 for r in losers if r["post_mfe_pts"] >= risk_ticks * 0.25)
        print(f"  LOSERS:  post-exit further-MFE avg {avg_recov/0.25:>5.1f}t  "
              f"({recov}/{len(losers)} would have recovered 1R+ after stop)")

    # Position in 15-min swing
    print("\n========== ENTRY POSITION IN 15-MIN SWING ==========")
    print("  (LONG: 0=at swing high/late, 1=at swing low/great pullback)")
    bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in bins:
        grp = [r for r in results if lo <= r.get("pullback_pct", 0) < hi]
        _fmt_grp(f"pullback_pct {lo:.1f}-{hi:.1f}", grp)

    # Pre-entry trend (last 60s) — was momentum WITH or AGAINST trade?
    print("\n========== PRE-ENTRY 60S MOMENTUM (pts, sign-corrected to trade dir) ==========")
    def fav_trend(r, win="60s"):
        t = r.get(f"trend_{win}_pts", 0)
        return t if r["side"] == "LONG" else -t
    bins_t = [(-99, -1), (-1, -0.25), (-0.25, 0), (0, 0.25), (0.25, 1), (1, 99)]
    for lo, hi in bins_t:
        grp = [r for r in results if lo <= fav_trend(r) < hi]
        _fmt_grp(f"60s trend {lo:>+5.2f}..{hi:>+5.2f}pts", grp)

    # Pre-entry trend last 5 min — HTF context
    print("\n========== PRE-ENTRY 5MIN MOMENTUM (pts, sign-corrected to trade dir) ==========")
    bins_t = [(-99, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 99)]
    for lo, hi in bins_t:
        grp = [r for r in results if lo <= fav_trend(r, "300s") < hi]
        _fmt_grp(f"5min trend {lo:>+5.2f}..{hi:>+5.2f}pts", grp)

    # Tape speed at entry
    print("\n========== PRE-ENTRY TAPE SPEED (last 15s, ticks/sec) ==========")
    bins_s = [(0, 1), (1, 3), (3, 6), (6, 12), (12, 999)]
    for lo, hi in bins_s:
        grp = [r for r in results if lo <= r.get("speed_15s_tps", 0) < hi]
        _fmt_grp(f"{lo}-{hi} tps", grp)

    # Delta at entry (sign-corrected to direction)
    print("\n========== PRE-ENTRY DELTA (last 15s, signed to trade direction) ==========")
    def fav_delta(r):
        d = r.get("delta_15s", 0)
        return d if r["side"] == "LONG" else -d
    bins_d = [(-99999, -200), (-200, -50), (-50, 0), (0, 50), (50, 200), (200, 99999)]
    for lo, hi in bins_d:
        grp = [r for r in results if lo <= fav_delta(r) < hi]
        _fmt_grp(f"fav_delta {lo}..{hi}", grp)

    # Range compression (last 60s range relative to last 300s range)
    print("\n========== RANGE COMPRESSION (60s range / 300s range, lower = tighter) ==========")
    def compr(r):
        r60 = r.get("range_60s_pts", 0)
        r300 = r.get("range_300s_pts", 0)
        return (r60 / r300) if r300 > 0.001 else 1.0
    bins_c = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.5)]
    for lo, hi in bins_c:
        grp = [r for r in results if lo <= compr(r) < hi]
        _fmt_grp(f"compression {lo:.1f}-{hi:.1f}", grp)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--risk", type=float, default=6, help="risk ticks (R unit)")
    ap.add_argument("--out", default="trade_analysis.csv")
    args = ap.parse_args()
    res = run_analysis(account=args.account, days=args.days,
                       out_csv=args.out, risk_ticks=args.risk)
    if res:
        print_summary(res, risk_ticks=args.risk)
