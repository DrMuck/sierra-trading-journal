"""
A+ setup detector for a chosen account (pass --account on the CLI).

Two-axis classification of each historical trade:

  AXIS 1: Setup TYPE
    - PULLBACK: HTF trend in direction, price retraced from 15m extreme
                (pullback_pct between 0.25 and 0.70), 1m has cooled
                (close_pos NOT at extreme, 1m trend not chasing)
    - BREAKOUT: range compression (60s_range / 300s_range low) then
                expansion at entry, price near recent range top/bottom
                in trade direction
    - NEITHER:  doesn't fit either cleanly

  AXIS 2: Setup GRADE (matches the "10 best trades" signature)
    A+: All 5 quality checks pass (HTF, time, weekday, vol, no-chase)
    A : 4/5 checks pass
    B : 2-3/5 pass
    C : 0-1/5 (avoid)

The 5 quality checks (derived from your 10 best vs 10 worst):
  Q1 HTF_TREND_WITH:   15m trend in trade direction >= +3 pts
  Q2 NO_CHASE:         1m trend in trade dir <= +1 pt   AND   1m close_pos <= 0.80
  Q3 GOOD_TIME:        bucket in {open_30, morning_1, mid}  (not pre_open/open_15/morning_2 chop)
  Q4 GOOD_DAY:         Tuesday/Wednesday/Thursday/Friday  (no Monday)
  Q5 VOL_DELTA_CONF:   1m volume >= 3500  AND  15s delta in direction >= +50

For ES: tick_size = 0.25
"""
import argparse
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


# Tunable thresholds (derived from 10-best vs 10-worst analysis)
Q1_HTF_TREND_MIN = 3.0      # pts in 15m, in trade direction
Q2_1M_TREND_MAX = 1.0       # pts in 1m, in trade direction (chase if exceeded)
Q2_1M_CLOSE_POS_MAX = 0.80  # 1m close position (0=lo,1=hi); chase if exceeded
Q3_GOOD_BUCKETS = {"open_30", "morning_1", "mid"}
Q4_BAD_DAYS = {"Mon"}
Q5_1M_VOL_MIN = 3500
Q5_15S_DELTA_MIN = 50

# Pullback / breakout classification thresholds
PB_PULLBACK_PCT_MIN = 0.25  # entry is at least 25% retraced from 15m extreme
PB_PULLBACK_PCT_MAX = 0.70  # but not more than 70%
PB_1M_CLOSE_POS_MAX = 0.80  # entry not at 1m extreme

BR_COMPRESSION_MAX = 0.40   # 60s_range / 300s_range — tight pre-entry
BR_1M_CLOSE_POS_MIN_LONG = 0.70  # for LONG breakouts, must be near 1m high
BR_1M_CLOSE_POS_MAX_SHORT = 0.30 # for SHORT breakouts, near 1m low


def bucket_of(min_after_open: int) -> str:
    if min_after_open < 0: return "pre_open"
    if min_after_open < 15: return "open_15"
    if min_after_open < 30: return "open_30"
    if min_after_open < 60: return "morning_1"
    if min_after_open < 120: return "morning_2"
    if min_after_open < 210: return "mid"
    return "afternoon"


def features_for(trade: dict, ticks: dict) -> dict | None:
    if ticks is None or len(ticks["ts_ns"]) == 0:
        return None
    side = trade["side"]
    entry = trade["entry_price"]
    entry_ms = trade["entry_time_ms"]
    exit_ms = trade["exit_time_ms"]
    if not exit_ms: return None

    ts = ticks["ts_ns"] // 1_000_000
    px = ticks["price"]
    av = ticks.get("ask_volume", np.zeros_like(ticks["volume"]))
    bv = ticks.get("bid_volume", np.zeros_like(ticks["volume"]))
    vol = ticks["volume"]
    symbol = trade["root_symbol"]
    tick_size = TICK_SIZES.get(symbol, 0.25)

    sign = 1 if side == "LONG" else -1
    entry_dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc) - timedelta(hours=4)
    dow = entry_dt.strftime("%a")
    minutes_after_open = (entry_dt.hour - 9) * 60 + (entry_dt.minute - 30)
    bucket = bucket_of(minutes_after_open)

    def window(win_s):
        m = (ts >= entry_ms - win_s * 1000) & (ts < entry_ms)
        return px[m], av[m], bv[m], vol[m]

    # Windows
    p15s, av15s, bv15s, vol15s = window(15)
    p1m, av1m, bv1m, vol1m = window(60)
    p5m, _, _, _ = window(300)
    p15m, _, _, _ = window(900)

    def stat_block(p_px, p_av, p_bv, p_vol, win_s):
        if len(p_px) < 2:
            return 0.0, 0.0, 0, 0, 0.5
        rng = float(p_px.max() - p_px.min())
        trend_sign = float(p_px[-1] - p_px[0]) * sign
        delta_sign = int(p_av.sum() - p_bv.sum()) * sign
        volume = int(p_vol.sum())
        close_pos = float((entry - p_px.min()) / rng) if rng > 0 else 0.5
        return trend_sign, rng, delta_sign, volume, close_pos

    tr15s, rng15s, d15s, v15s, cp15s = stat_block(p15s, av15s, bv15s, vol15s, 15)
    tr1m, rng1m, d1m, v1m, cp1m = stat_block(p1m, av1m, bv1m, vol1m, 60)
    rng5m = float(p5m.max() - p5m.min()) if len(p5m) >= 2 else 0.0
    if len(p15m) >= 5:
        hi = float(p15m.max()); lo = float(p15m.min()); rng15m = hi - lo
        tr15m = float(p15m[-1] - p15m[0]) * sign
        if rng15m > 0:
            if side == "LONG":
                pullback_pct = (hi - entry) / rng15m
                cp15m = (entry - lo) / rng15m
            else:
                pullback_pct = (entry - lo) / rng15m
                cp15m = (hi - entry) / rng15m
        else:
            pullback_pct = 0.0
            cp15m = 0.5
    else:
        rng15m = 0; tr15m = 0; pullback_pct = 0; cp15m = 0.5

    compression = (rng1m / rng5m) if rng5m > 0.001 else 1.0

    # In-trade MFE
    in_mask = (ts >= entry_ms) & (ts <= exit_ms)
    in_px = px[in_mask]
    if len(in_px) > 0:
        if side == "LONG":
            mfe_pts = float(in_px.max() - entry)
            mae_pts = float(in_px.min() - entry)
        else:
            mfe_pts = float(entry - in_px.min())
            mae_pts = float(entry - in_px.max())
    else:
        mfe_pts = mae_pts = 0.0

    pnl_pts = float(trade["pnl_points"] or 0) / float(trade["entry_qty"] or 1)

    return {
        "id": trade["id"],
        "date": trade["trade_date"],
        "entry_ny": entry_dt.strftime("%H:%M:%S"),
        "dow": dow,
        "symbol": symbol,
        "side": side,
        "bucket": bucket,
        "net_pnl": trade["net_pnl"],
        "pnl_ticks": round(pnl_pts / tick_size, 1),
        "mfe_ticks": round(mfe_pts / tick_size, 1),
        "mae_ticks": round(mae_pts / tick_size, 1),
        # Feature vector
        "tr15s_pts": round(tr15s, 3),
        "d15s": d15s,
        "v15s": v15s,
        "cp15s": round(cp15s, 3),
        "tr1m_pts": round(tr1m, 3),
        "v1m": v1m,
        "cp1m": round(cp1m, 3),
        "tr15m_pts": round(tr15m, 3),
        "pullback_pct": round(pullback_pct, 3),
        "cp15m": round(cp15m, 3),
        "compression": round(compression, 3),
    }


def grade(f: dict) -> tuple[str, dict]:
    """Return (grade, checks dict) — 5 boolean quality gates."""
    checks = {
        "Q1_HTF_TREND_WITH":  f["tr15m_pts"] >= Q1_HTF_TREND_MIN,
        "Q2_NO_CHASE":        f["tr1m_pts"] <= Q2_1M_TREND_MAX and f["cp1m"] <= Q2_1M_CLOSE_POS_MAX,
        "Q3_GOOD_TIME":       f["bucket"] in Q3_GOOD_BUCKETS,
        "Q4_GOOD_DAY":        f["dow"] not in Q4_BAD_DAYS,
        "Q5_VOL_DELTA_CONF":  f["v1m"] >= Q5_1M_VOL_MIN and f["d15s"] >= Q5_15S_DELTA_MIN,
    }
    score = sum(checks.values())
    if score == 5: g = "A+"
    elif score == 4: g = "A"
    elif score >= 2: g = "B"
    else: g = "C"
    return g, checks


def classify_setup(f: dict) -> str:
    """PULLBACK / BREAKOUT / NEITHER."""
    pb_ok = (PB_PULLBACK_PCT_MIN <= f["pullback_pct"] <= PB_PULLBACK_PCT_MAX and
             f["cp1m"] <= PB_1M_CLOSE_POS_MAX)
    if f["side"] == "LONG":
        br_ok = (f["compression"] <= BR_COMPRESSION_MAX and
                 f["cp1m"] >= BR_1M_CLOSE_POS_MIN_LONG)
    else:
        br_ok = (f["compression"] <= BR_COMPRESSION_MAX and
                 f["cp1m"] <= BR_1M_CLOSE_POS_MAX_SHORT)
    if pb_ok and not br_ok: return "PULLBACK"
    if br_ok and not pb_ok: return "BREAKOUT"
    if pb_ok and br_ok:     return "PULLBACK"   # tie goes to pullback (closer to your style)
    return "NEITHER"


def stat_grp(label, lst):
    if not lst:
        return f"  {label:<32}: n=0"
    wins = sum(1 for r in lst if r["pnl_ticks"] > 0)
    wr = wins / len(lst) * 100
    e_pnl = sum(r["pnl_ticks"] for r in lst) / len(lst)
    e_net = sum((r["net_pnl"] or 0) for r in lst) / len(lst)
    total = sum(r["net_pnl"] or 0 for r in lst)
    return (f"  {label:<32}: n={len(lst):>3} wr={wr:>4.1f}% E={e_pnl:>+5.2f}t "
            f"avg_net={e_net:>+6.2f}$ TOTAL={total:>+7.0f}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--days", type=int, default=999)
    ap.add_argument("--symbols", default="ES",
                    help="Comma-separated root symbols to include (default ES). Use ALL for everything.")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    if args.symbols.upper() == "ALL":
        sym_clause = ""; sym_params = ()
    else:
        syms = [s.strip().upper() for s in args.symbols.split(",")]
        sym_clause = f" AND root_symbol IN ({','.join('?' for _ in syms)})"
        sym_params = tuple(syms)
    rows = conn.execute(f"""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points, net_pnl
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account = ? AND trade_date >= ?{sym_clause}
         ORDER BY entry_time_ms
    """, (args.account, cutoff) + sym_params).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Loaded {len(trades)} closed trades for {args.account} since {cutoff}\n")

    cache = {}
    feats = []
    for t in trades:
        k = (t["root_symbol"], t["trade_date"])
        if k not in cache:
            cache[k] = _load_ticks(t["root_symbol"], t["trade_date"])
        f = features_for(t, cache[k])
        if f:
            g, checks = grade(f)
            f["grade"] = g
            f["setup_type"] = classify_setup(f)
            f["q_checks"] = checks
            feats.append(f)
    print(f"Analyzed {len(feats)} trades")

    # ============ GRADE PERFORMANCE ============
    print("\n========== PERFORMANCE BY GRADE ==========")
    for g in ("A+", "A", "B", "C"):
        lst = [f for f in feats if f["grade"] == g]
        print(stat_grp(g, lst))

    # ============ SETUP TYPE PERFORMANCE ============
    print("\n========== PERFORMANCE BY SETUP TYPE ==========")
    for st in ("PULLBACK", "BREAKOUT", "NEITHER"):
        lst = [f for f in feats if f["setup_type"] == st]
        print(stat_grp(st, lst))

    # ============ GRADE × TYPE MATRIX ============
    print("\n========== GRADE x TYPE MATRIX ==========")
    for g in ("A+", "A", "B", "C"):
        for st in ("PULLBACK", "BREAKOUT", "NEITHER"):
            lst = [f for f in feats if f["grade"] == g and f["setup_type"] == st]
            if not lst: continue
            print(stat_grp(f"{g:<3} x {st}", lst))

    # ============ WHICH CHECK FAILS MOST ON LOSERS? ============
    print("\n========== INDIVIDUAL QUALITY CHECKS (each independently) ==========")
    print(f"{'check':<22} {'pass':>8} {'fail':>8}")
    for k in ("Q1_HTF_TREND_WITH", "Q2_NO_CHASE", "Q3_GOOD_TIME", "Q4_GOOD_DAY", "Q5_VOL_DELTA_CONF"):
        passers = [f for f in feats if f["q_checks"][k]]
        failers = [f for f in feats if not f["q_checks"][k]]
        wr_p = sum(1 for r in passers if r["pnl_ticks"] > 0) / len(passers) * 100 if passers else 0
        wr_f = sum(1 for r in failers if r["pnl_ticks"] > 0) / len(failers) * 100 if failers else 0
        e_p = sum(r["pnl_ticks"] for r in passers) / len(passers) if passers else 0
        e_f = sum(r["pnl_ticks"] for r in failers) / len(failers) if failers else 0
        net_p = sum(r["net_pnl"] or 0 for r in passers)
        net_f = sum(r["net_pnl"] or 0 for r in failers)
        print(f"  {k:<22} pass: n={len(passers):>3} wr={wr_p:>4.1f}% E={e_p:>+5.2f}t  total=${net_p:>+6.0f}")
        print(f"  {'':<22} fail: n={len(failers):>3} wr={wr_f:>4.1f}% E={e_f:>+5.2f}t  total=${net_f:>+6.0f}")

    # ============ DELTA & VOLUME ENTRY ANALYSIS ============
    print("\n========== DELTA SIGNATURE (15s pre-entry, sign-corrected) ==========")
    bins = [(-99999, 0), (0, 50), (50, 100), (100, 200), (200, 99999)]
    for lo, hi in bins:
        lst = [f for f in feats if lo <= f["d15s"] < hi]
        print(stat_grp(f"d15s {lo}..{hi}", lst))

    print("\n========== VOLUME SIGNATURE (1m pre-entry) ==========")
    bins = [(0, 1500), (1500, 3000), (3000, 5000), (5000, 8000), (8000, 999999)]
    for lo, hi in bins:
        lst = [f for f in feats if lo <= f["v1m"] < hi]
        print(stat_grp(f"v1m {lo}..{hi}", lst))

    print("\n========== DELTA x VOLUME (4 quadrants) ==========")
    quads = [
        ("delta>=50 & vol>=3500  (CONFIRMED)", lambda f: f["d15s"] >= 50 and f["v1m"] >= 3500),
        ("delta>=50 & vol<3500    (thin)",      lambda f: f["d15s"] >= 50 and f["v1m"] <  3500),
        ("delta<50  & vol>=3500   (mixed vol)", lambda f: f["d15s"] <  50 and f["v1m"] >= 3500),
        ("delta<50  & vol<3500    (NEITHER)",   lambda f: f["d15s"] <  50 and f["v1m"] <  3500),
    ]
    for label, pred in quads:
        lst = [f for f in feats if pred(f)]
        print(stat_grp(label, lst))

    # ============ SUMMARY TABLE: HOW WERE THE BEST 10 GRADED? ============
    print("\n========== TOP 10 BEST: WHAT GRADE WERE THEY? ==========")
    top10 = sorted(feats, key=lambda f: -(f["net_pnl"] or 0))[:10]
    print(f"{'date':<11} {'time':>9} {'side':<5} ${'net':>6} {'grade':<4} {'type':<10}  checks-failed")
    for f in top10:
        failed = [k.replace('Q', '').split('_')[0] for k, v in f["q_checks"].items() if not v]
        failed_str = "all-pass" if not failed else ",".join(failed)
        print(f"{f['date']:<11} {f['entry_ny']:>9} {f['side']:<5} "
              f"${(f['net_pnl'] or 0):>+6.0f} {f['grade']:<4} {f['setup_type']:<10}  {failed_str}")

    print("\n========== TOP 10 WORST: WHAT GRADE WERE THEY? ==========")
    bot10 = sorted(feats, key=lambda f: (f["net_pnl"] or 0))[:10]
    for f in bot10:
        failed = [k.replace('Q', '').split('_')[0] for k, v in f["q_checks"].items() if not v]
        failed_str = "all-pass" if not failed else ",".join(failed)
        print(f"{f['date']:<11} {f['entry_ny']:>9} {f['side']:<5} "
              f"${(f['net_pnl'] or 0):>+6.0f} {f['grade']:<4} {f['setup_type']:<10}  {failed_str}")

    # ============ CONCRETE NUMBERS ============
    print("\n========== BOTTOM LINE ==========")
    total_net = sum((f["net_pnl"] or 0) for f in feats)
    print(f"  Account net (all {len(feats)} trades):  ${total_net:+.2f}")
    for g in ("A+", "A", "B", "C"):
        lst = [f for f in feats if f["grade"] == g]
        net = sum(f["net_pnl"] or 0 for f in lst)
        print(f"  {g:<3}  trades={len(lst):>3}  net=${net:+>8.2f}")
    # If you'd only traded A+ and A
    keep = [f for f in feats if f["grade"] in ("A+", "A")]
    net_keep = sum(f["net_pnl"] or 0 for f in keep)
    drop = [f for f in feats if f["grade"] in ("B", "C")]
    net_drop = sum(f["net_pnl"] or 0 for f in drop)
    print(f"  IF you'd only taken A+/A: kept {len(keep)}/{len(feats)} trades, "
          f"net=${net_keep:+.2f}, would have avoided ${net_drop:+.2f} of B/C trades")


if __name__ == "__main__":
    main()
