"""
Find the top N best trades on a chosen account and analyze their common patterns
across multiple timeframes (15s, 1min, 15min).

For each top trade, compute:
  - 15s pre-entry: trend, delta, volume, range, speed
  - 1min pre-entry: trend, range, where price was in last 1min (top/bottom of bar)
  - 15min pre-entry: trend, swing range, pullback %, time-since-swing-high/low
  - Daily context: minute-of-day, day-of-week, total daily volume estimate
  - Intra-trade: MFE, MAE, time-to-MFE, did MAE come before MFE
  - Trade size, side, duration

Output: per-trade detail table + aggregate patterns
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

import numpy as np

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


def analyze_trade(trade, ticks):
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
    av = ticks.get("ask_volume", np.zeros_like(ticks["volume"]))
    bv = ticks.get("bid_volume", np.zeros_like(ticks["volume"]))
    vol = ticks["volume"]
    symbol = trade["root_symbol"]
    tick_size = TICK_SIZES.get(symbol, 0.25)

    out = {
        "id": trade["id"],
        "date": trade["trade_date"],
        "symbol": symbol,
        "side": side,
        "qty": int(trade["entry_qty"]),
        "entry": entry,
        "exit": trade["exit_price"],
        "duration_s": round((exit_ms - entry_ms) / 1000, 1),
        "net_pnl": trade["net_pnl"],
        "pnl_ticks": round(float(trade["pnl_points"] or 0) / float(trade["entry_qty"] or 1) / tick_size, 1),
    }

    # NY-local entry time + day-of-week
    entry_dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc) - timedelta(hours=4)
    out["entry_ny"] = entry_dt.strftime("%H:%M:%S")
    out["dow"] = entry_dt.strftime("%a")
    minutes_after_open = (entry_dt.hour - 9) * 60 + (entry_dt.minute - 30)
    out["min_after_open"] = minutes_after_open

    # ---------- 15s timeframe ----------
    for win_s, prefix in [(15, "tf15s"), (60, "tf1m"), (900, "tf15m")]:
        m = (ts >= entry_ms - win_s * 1000) & (ts < entry_ms)
        p_px = px[m]; p_av = av[m]; p_bv = bv[m]; p_vol = vol[m]
        if len(p_px) < 2:
            out[f"{prefix}_trend_pts"] = 0.0
            out[f"{prefix}_range_pts"] = 0.0
            out[f"{prefix}_delta"] = 0
            out[f"{prefix}_volume"] = 0
            out[f"{prefix}_close_pos"] = 0.0
            continue
        out[f"{prefix}_trend_pts"] = round(float(p_px[-1] - p_px[0]), 3)
        out[f"{prefix}_range_pts"] = round(float(p_px.max() - p_px.min()), 3)
        out[f"{prefix}_delta"] = int(p_av.sum() - p_bv.sum())
        out[f"{prefix}_volume"] = int(p_vol.sum())
        # close_pos: where is entry relative to the window range?
        # 0 = at window low, 1 = at window high
        rng = float(p_px.max() - p_px.min())
        if rng > 0:
            out[f"{prefix}_close_pos"] = round(float((entry - p_px.min()) / rng), 3)
        else:
            out[f"{prefix}_close_pos"] = 0.5

    # 15m pullback_pct (where in swing entry was, sign-corrected to side)
    m = (ts >= entry_ms - 900 * 1000) & (ts < entry_ms)
    p_px = px[m]
    if len(p_px) >= 5:
        hi = float(p_px.max()); lo = float(p_px.min()); rng = hi - lo
        if rng > 0:
            if side == "LONG":
                out["tf15m_pullback_pct"] = round((hi - entry) / rng, 3)
            else:
                out["tf15m_pullback_pct"] = round((entry - lo) / rng, 3)
        else:
            out["tf15m_pullback_pct"] = 0.0
    else:
        out["tf15m_pullback_pct"] = 0.0

    # ---------- MFE / MAE ----------
    in_mask = (ts >= entry_ms) & (ts <= exit_ms)
    in_ts = ts[in_mask]; in_px = px[in_mask]
    if len(in_px) > 0:
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
        out["mfe_ticks"] = round(mfe_pts / tick_size, 1)
        out["mae_ticks"] = round(mae_pts / tick_size, 1)
        out["t_to_mfe_s"] = round((int(in_ts[mfe_idx]) - entry_ms) / 1000, 1)
        out["mae_before_mfe"] = int((int(in_ts[mae_idx]) - entry_ms) <
                                    (int(in_ts[mfe_idx]) - entry_ms))
    else:
        out["mfe_ticks"] = out["mae_ticks"] = 0.0
        out["t_to_mfe_s"] = 0.0
        out["mae_before_mfe"] = 0

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--metric", choices=["net_pnl", "pnl_ticks"], default="net_pnl")
    ap.add_argument("--also_worst", action="store_true", default=True)
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points, net_pnl
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account = ?
         ORDER BY entry_time_ms
    """, (args.account,)).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Loaded {len(trades)} closed trades for {args.account}")

    cache = {}
    analyzed = []
    for t in trades:
        k = (t["root_symbol"], t["trade_date"])
        if k not in cache:
            cache[k] = _load_ticks(t["root_symbol"], t["trade_date"])
        r = analyze_trade(t, cache[k])
        if r:
            analyzed.append(r)
    print(f"Analyzed {len(analyzed)} (others lacked tick data)")

    sort_key = (lambda x: -x[args.metric])
    top = sorted(analyzed, key=sort_key)[:args.top]
    bot = sorted(analyzed, key=lambda x: x[args.metric])[:args.top]

    def show_trades(label, lst):
        print(f"\n========== {label} ==========")
        print(f"{'date':<11} {'time':>9} {'sym':<4} {'side':<5} {'qty':>3} "
              f"{'$pnl':>8} {'tks':>5} {'dur':>6} {'mfe':>5} {'mae':>5} "
              f"{'t_mfe':>6} {'maeBM':>5}")
        for r in lst:
            print(f"{r['date']:<11} {r['entry_ny']:>9} {r['symbol']:<4} {r['side']:<5} "
                  f"{r['qty']:>3} ${r['net_pnl']:>+7.1f} {r['pnl_ticks']:>+5.1f} "
                  f"{r['duration_s']:>5.1f}s {r['mfe_ticks']:>+5.1f} {r['mae_ticks']:>+5.1f} "
                  f"{r['t_to_mfe_s']:>5.1f}s {r['mae_before_mfe']:>5}")

    def show_context(label, lst):
        print(f"\n--- {label}: Pre-entry context (sign-corrected to trade dir where noted) ---")
        print(f"{'date':<11} {'time':>9} {'side':<5} | "
              f"{'15s_tr':>7} {'15s_d':>6} {'15s_pos':>8} | "
              f"{'1m_tr':>7} {'1m_pos':>7} | "
              f"{'15m_tr':>8} {'pullb%':>7} {'15m_pos':>8}")
        for r in lst:
            # Sign-correct trends to trade direction
            sign = 1 if r["side"] == "LONG" else -1
            tr15 = r['tf15s_trend_pts'] * sign
            tr1m = r['tf1m_trend_pts'] * sign
            tr15m = r['tf15m_trend_pts'] * sign
            d15 = r['tf15s_delta'] * sign
            print(f"{r['date']:<11} {r['entry_ny']:>9} {r['side']:<5} | "
                  f"{tr15:>+6.2f} {d15:>+6} {r['tf15s_close_pos']:>7.2f} | "
                  f"{tr1m:>+6.2f} {r['tf1m_close_pos']:>6.2f} | "
                  f"{tr15m:>+7.2f} {r['tf15m_pullback_pct']:>6.2f} {r['tf15m_close_pos']:>7.2f}")

    show_trades(f"TOP {args.top} BEST (by {args.metric})", top)
    show_context(f"TOP {args.top} BEST", top)

    if args.also_worst:
        show_trades(f"TOP {args.top} WORST", bot)
        show_context(f"TOP {args.top} WORST", bot)

    # ---------- Aggregate patterns ----------
    def stats(lst, key, sign_correct=False):
        vals = []
        for r in lst:
            v = r.get(key, 0)
            if sign_correct and key.startswith("tf"):
                v = v * (1 if r["side"] == "LONG" else -1)
            vals.append(v)
        if not vals:
            return (0, 0, 0)
        return (sum(vals) / len(vals), min(vals), max(vals))

    def cmp(label, key, sign_correct=False, fmt="{:.2f}"):
        ta, tn, tx = stats(top, key, sign_correct)
        wa, wn, wx = stats(bot, key, sign_correct)
        f = fmt.format
        print(f"  {label:<28} top:{f(ta):>8} ({f(tn)}..{f(tx)})    "
              f"worst:{f(wa):>8} ({f(wn)}..{f(wx)})")

    print(f"\n========== AGGREGATE: TOP {args.top} BEST vs TOP {args.top} WORST ==========")
    print("(All trend/delta values sign-corrected to trade direction)\n")
    cmp("15s trend (pts to dir)", "tf15s_trend_pts", True)
    cmp("15s delta (signed)", "tf15s_delta", True, "{:+d}".format if False else "{:.0f}")
    cmp("15s volume", "tf15s_volume", False, "{:.0f}")
    cmp("15s close_pos (0=lo,1=hi)", "tf15s_close_pos", False)
    cmp("1m trend (pts to dir)", "tf1m_trend_pts", True)
    cmp("1m volume", "tf1m_volume", False, "{:.0f}")
    cmp("1m close_pos", "tf1m_close_pos", False)
    cmp("15m trend (pts to dir)", "tf15m_trend_pts", True)
    cmp("15m pullback_pct", "tf15m_pullback_pct", False)
    cmp("15m close_pos", "tf15m_close_pos", False)
    cmp("min_after_open", "min_after_open", False, "{:.1f}")
    cmp("MFE ticks", "mfe_ticks", False, "{:+.1f}")
    cmp("MAE ticks", "mae_ticks", False, "{:+.1f}")
    cmp("time-to-MFE seconds", "t_to_mfe_s", False, "{:.1f}")
    cmp("MAE before MFE flag", "mae_before_mfe", False, "{:.2f}")

    # ---------- Side distribution ----------
    print(f"\n--- Side / DOW / Symbol breakdown ---")
    from collections import Counter
    print(f"TOP {args.top} side:   {dict(Counter(r['side'] for r in top))}")
    print(f"WORST {args.top} side: {dict(Counter(r['side'] for r in bot))}")
    print(f"TOP {args.top} symbol: {dict(Counter(r['symbol'] for r in top))}")
    print(f"WORST {args.top} sym:  {dict(Counter(r['symbol'] for r in bot))}")
    print(f"TOP {args.top} DOW:    {dict(Counter(r['dow'] for r in top))}")
    print(f"WORST {args.top} DOW:  {dict(Counter(r['dow'] for r in bot))}")

    # ---------- Bucket counts ----------
    def bucket_min(m):
        if m < 0: return "pre_open"
        if m < 15: return "open_15"
        if m < 30: return "open_30"
        if m < 60: return "morning_1"
        if m < 120: return "morning_2"
        if m < 210: return "mid"
        return "afternoon"
    print(f"TOP {args.top} bucket: {dict(Counter(bucket_min(r['min_after_open']) for r in top))}")
    print(f"WORST {args.top} buck: {dict(Counter(bucket_min(r['min_after_open']) for r in bot))}")


if __name__ == "__main__":
    main()
