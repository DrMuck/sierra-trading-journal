"""
Plot today's P&L curve against ES price action for a chosen account.

Output: PNG with two stacked panels:
  - Top: ES 15s OHLC (line) over today's session, with trade markers
         (green ^ for winner entry, red v for loser entry; X for exit)
  - Bottom: running net P&L curve over the same time axis
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-17")
    ap.add_argument("--account", default="")
    ap.add_argument("--out", default="./today_chart.png")
    ap.add_argument("--bar_seconds", type=int, default=15)
    args = ap.parse_args()

    # Load trades
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, root_symbol, side, entry_time_ms, exit_time_ms,
               entry_price, exit_price, entry_qty, net_pnl, pnl_points
          FROM trades
         WHERE account=? AND trade_date=? AND is_open=0
         ORDER BY entry_time_ms
    """, (args.account, args.date)).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if not trades:
        print(f"No trades for {args.account} on {args.date}")
        return
    print(f"Loaded {len(trades)} trades for {args.date}")

    # Load ES tick data
    ticks = _load_ticks("ES", args.date)
    if ticks is None:
        print("No ES tick data for that date")
        return
    ts_ns = ticks["ts_ns"]
    px = ticks["price"].astype(float)
    vol = ticks["volume"]

    # Restrict to active window: 9:00 NY to last trade exit + 10 min
    first_entry_ms = trades[0]["entry_time_ms"]
    last_exit_ms = max(t["exit_time_ms"] or t["entry_time_ms"] for t in trades)
    window_start_ms = first_entry_ms - 30 * 60 * 1000  # 30 min before first trade
    window_end_ms = last_exit_ms + 15 * 60 * 1000     # 15 min after last trade

    ts_ms = ts_ns // 1_000_000
    mask = (ts_ms >= window_start_ms) & (ts_ms <= window_end_ms)
    ts_ms = ts_ms[mask]
    px_in = px[mask]
    vol_in = vol[mask]
    if len(ts_ms) == 0:
        print("No tick data in window")
        return

    # Build 15-second OHLC bars
    bar_ms = args.bar_seconds * 1000
    bar_id = (ts_ms - window_start_ms) // bar_ms
    bar_ids_unique = np.unique(bar_id)
    bars = []
    for bid in bar_ids_unique:
        sel = bar_id == bid
        bp = px_in[sel]
        bv = vol_in[sel]
        bars.append({
            "ts_ms": int(window_start_ms + int(bid) * bar_ms),
            "open": float(bp[0]),
            "high": float(bp.max()),
            "low": float(bp.min()),
            "close": float(bp[-1]),
            "volume": int(bv.sum()),
        })

    # Convert to plotting arrays (use NY local time)
    def ny_dt(ms):
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) - timedelta(hours=4)

    bar_times = [ny_dt(b["ts_ms"]) for b in bars]
    bar_close = [b["close"] for b in bars]
    bar_high = [b["high"] for b in bars]
    bar_low = [b["low"] for b in bars]

    # Build running P&L sequence keyed to trade exit times
    running = 0.0
    pnl_times = [ny_dt(first_entry_ms - 60_000)]
    pnl_vals = [0.0]
    for t in trades:
        running += t["net_pnl"] or 0
        pnl_times.append(ny_dt(t["exit_time_ms"]))
        pnl_vals.append(running)

    # Compute peak/trough for annotation
    peak_pnl = max(pnl_vals)
    peak_idx = pnl_vals.index(peak_pnl)
    trough_pnl = min(pnl_vals)
    trough_idx = pnl_vals.index(trough_pnl)

    # ── Plot ────────────────────────────────────────────────
    fig, (ax_p, ax_pnl) = plt.subplots(
        2, 1, sharex=True, figsize=(18, 10),
        gridspec_kw={"height_ratios": [3, 1.5]},
        facecolor="#0f1117",
    )
    for ax in (ax_p, ax_pnl):
        ax.set_facecolor("#161922")
        for spine in ax.spines.values():
            spine.set_color("#363952")
        ax.tick_params(colors="#cccccc", labelsize=9)
        ax.grid(True, color="#2a2e40", linewidth=0.5, alpha=0.6)

    # Top: ES price line
    ax_p.plot(bar_times, bar_close, color="#e0e0e0", linewidth=0.9, label="ES close (15s)")
    # Add range as a translucent band
    ax_p.fill_between(bar_times, bar_low, bar_high, color="#e0e0e0", alpha=0.08)
    ax_p.set_ylabel("ES price", color="#cccccc")
    ax_p.set_title(f"Account {args.account} — {args.date}  ($+{sum(t['net_pnl'] or 0 for t in trades):.0f} net, "
                   f"{len(trades)} trades)", color="white", fontsize=13)

    # Trade entry markers on the price chart (ES only — MES entries shown too but smaller)
    long_entries_x, long_entries_y = [], []
    short_entries_x, short_entries_y = [], []
    exits_x, exits_y = [], []
    win_entries_x, win_entries_y = [], []
    loss_entries_x, loss_entries_y = [], []
    for i, t in enumerate(trades, 1):
        entry_dt = ny_dt(t["entry_time_ms"])
        exit_dt = ny_dt(t["exit_time_ms"]) if t["exit_time_ms"] else entry_dt
        ep = t["entry_price"]
        xp = t["exit_price"] or ep
        net = t["net_pnl"] or 0
        # For MES, prices align with ES (same point scale); keep them
        if t["side"] == "LONG":
            long_entries_x.append(entry_dt); long_entries_y.append(ep)
        else:
            short_entries_x.append(entry_dt); short_entries_y.append(ep)
        exits_x.append(exit_dt); exits_y.append(xp)
        if net > 0:
            win_entries_x.append(entry_dt); win_entries_y.append(ep)
        else:
            loss_entries_x.append(entry_dt); loss_entries_y.append(ep)
        # Trade number label
        ax_p.annotate(str(i), (entry_dt, ep), textcoords="offset points",
                      xytext=(0, 7 if t["side"] == "LONG" else -10),
                      fontsize=7, color="#888899", ha="center")

    # Plot entries by win/loss
    if win_entries_x:
        ax_p.scatter(win_entries_x, win_entries_y, marker="o", s=55,
                     facecolors="none", edgecolors="#22c55e", linewidths=1.8,
                     label="Winner entry", zorder=5)
    if loss_entries_x:
        ax_p.scatter(loss_entries_x, loss_entries_y, marker="o", s=55,
                     facecolors="none", edgecolors="#ef4444", linewidths=1.8,
                     label="Loser entry", zorder=5)
    # Side direction arrows over the entry
    if long_entries_x:
        ax_p.scatter(long_entries_x, long_entries_y, marker="^", s=28,
                     color="#3b82f6", alpha=0.6, label="LONG", zorder=4)
    if short_entries_x:
        ax_p.scatter(short_entries_x, short_entries_y, marker="v", s=28,
                     color="#f59e0b", alpha=0.7, label="SHORT", zorder=4)
    # Exits
    ax_p.scatter(exits_x, exits_y, marker="x", s=35, color="#9ca3af",
                 alpha=0.5, label="Exit", zorder=3)

    # Cash open vertical line (9:30 NY)
    open_dt = ny_dt(first_entry_ms).replace(hour=9, minute=30, second=0, microsecond=0)
    ax_p.axvline(open_dt, color="#ffd60a", alpha=0.4, linewidth=1.0, linestyle="--",
                 label="9:30 NY cash open")

    ax_p.legend(loc="lower right", fontsize=8, facecolor="#1c1f2e", edgecolor="#363952",
                labelcolor="#cccccc")

    # Bottom: running P&L
    pnl_arr = np.array(pnl_vals)
    pnl_times_arr = pnl_times
    ax_pnl.plot(pnl_times_arr, pnl_arr, color="#60a5fa", linewidth=1.4)
    # Fill above/below zero
    ax_pnl.fill_between(pnl_times_arr, pnl_arr, 0,
                        where=(pnl_arr >= 0), color="#22c55e", alpha=0.20, interpolate=True)
    ax_pnl.fill_between(pnl_times_arr, pnl_arr, 0,
                        where=(pnl_arr < 0), color="#ef4444", alpha=0.20, interpolate=True)
    ax_pnl.axhline(0, color="#444", linewidth=0.7)
    ax_pnl.set_ylabel("Running P&L ($)", color="#cccccc")
    ax_pnl.set_xlabel("NY time", color="#cccccc")

    # Annotate peak / trough
    ax_pnl.annotate(f"Peak ${peak_pnl:.0f}",
                    xy=(pnl_times_arr[peak_idx], peak_pnl),
                    xytext=(10, 10), textcoords="offset points",
                    color="#22c55e", fontsize=9,
                    arrowprops=dict(arrowstyle="->", color="#22c55e", lw=0.8))
    ax_pnl.annotate(f"Trough ${trough_pnl:.0f}",
                    xy=(pnl_times_arr[trough_idx], trough_pnl),
                    xytext=(10, -15), textcoords="offset points",
                    color="#ef4444", fontsize=9,
                    arrowprops=dict(arrowstyle="->", color="#ef4444", lw=0.8))

    # Daily loss limit line (-$300 from TradingAssistent config default)
    ax_pnl.axhline(-300, color="#ef4444", linewidth=0.6, linestyle=":",
                   alpha=0.6, label="daily soft-stop -$300")
    ax_pnl.legend(loc="lower right", fontsize=8, facecolor="#1c1f2e",
                  edgecolor="#363952", labelcolor="#cccccc")

    # Format time axis
    for ax in (ax_p, ax_pnl):
        ax.xaxis.set_major_formatter(DateFormatter("%H:%M"))

    plt.tight_layout()
    plt.savefig(args.out, dpi=110, facecolor="#0f1117")
    plt.close()
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
