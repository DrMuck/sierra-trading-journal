"""
Simulate a structure-based trailing stop strategy on historical trades.

Strategy:
- Use actual trade entry (price, time, side)
- Initial stop N ticks away (bootstraps the position)
- Build time bars and detect swing highs/lows using lookback pivots
- Once a swing low (LONG) or swing high (SHORT) confirms, move stop to
  structure_level ± offset_ticks, but only in the favorable direction
- Never loosen the stop beyond the initial stop
- Exit at trailing stop hit OR at max_duration_minutes (whichever first)
- The simulation is NOT bounded by the original exit — trades can run longer.
"""
import numpy as np
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor
from functools import partial


@dataclass
class SimResult:
    trade_id: str
    side: str
    entry_ms: int
    entry_price: float
    sim_exit_ms: int
    sim_exit_price: float
    sim_pnl_pts: float
    orig_exit_ms: int
    orig_exit_price: float
    orig_pnl_pts: float
    exit_reason: str
    n_stop_updates: int
    # Trajectory data for plotting
    stop_timeline: list  # list of (ts_ms, stop_price) updates
    pnl_curve: list       # list of (ts_ms, unrealized_pts) sampled


def _build_bars(ts_ms: np.ndarray, px: np.ndarray, bar_seconds: int, start_ms: int):
    if len(ts_ms) == 0:
        return []
    interval_ms = bar_seconds * 1000
    bar_ids = (ts_ms - start_ms) // interval_ms
    unique = np.unique(bar_ids)
    bars = []
    for bid in unique:
        mask = bar_ids == bid
        bp = px[mask]
        bt = ts_ms[mask]
        bars.append({
            "id": int(bid),
            "start_ms": start_ms + int(bid) * interval_ms,
            "end_ms": start_ms + int(bid + 1) * interval_ms,
            "high": float(bp.max()),
            "low": float(bp.min()),
            "close": float(bp[-1]),
            "last_ms": int(bt[-1]),
        })
    return bars


def simulate_trade(trade, ticks, *, bar_seconds=30, lookback=3, offset_ticks=2,
                   initial_stop_ticks=8, tick_size=0.25,
                   max_duration_minutes=60, collect_trajectory=False):
    """Simulate trailing-stop strategy for one trade.

    Trades can run up to max_duration_minutes past entry (not capped by original exit).
    """
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

    bars = _build_bars(w_ts, w_px, bar_seconds, entry_ms)
    if not bars:
        return None

    if side == "LONG":
        stop = entry - initial_stop_ticks * tick_size
    else:
        stop = entry + initial_stop_ticks * tick_size

    stop_timeline = [(entry_ms, stop)]
    n_stop_updates = 0
    exit_reason = None
    sim_exit_price = None
    sim_exit_ms = None

    for i in range(len(bars)):
        bar = bars[i]

        # Intrabar stop check
        if side == "LONG" and bar["low"] <= stop:
            sim_exit_price = stop
            sim_exit_ms = bar["start_ms"]
            exit_reason = "trailing_stop" if n_stop_updates > 0 else "initial_stop"
            break
        if side == "SHORT" and bar["high"] >= stop:
            sim_exit_price = stop
            sim_exit_ms = bar["start_ms"]
            exit_reason = "trailing_stop" if n_stop_updates > 0 else "initial_stop"
            break

        # Confirm pivot at bar (i - lookback)
        center_idx = i - lookback
        if center_idx >= lookback:
            center_bar = bars[center_idx]
            left = range(center_idx - lookback, center_idx)
            right = range(center_idx + 1, center_idx + lookback + 1)

            if side == "LONG":
                is_pivot = all(bars[j]["low"] >= center_bar["low"]
                               for j in list(left) + list(right))
                if is_pivot:
                    new_stop = center_bar["low"] - offset_ticks * tick_size
                    if new_stop > stop:
                        stop = new_stop
                        n_stop_updates += 1
                        stop_timeline.append((bar["start_ms"], stop))
            else:
                is_pivot = all(bars[j]["high"] <= center_bar["high"]
                               for j in list(left) + list(right))
                if is_pivot:
                    new_stop = center_bar["high"] + offset_ticks * tick_size
                    if new_stop < stop:
                        stop = new_stop
                        n_stop_updates += 1
                        stop_timeline.append((bar["start_ms"], stop))

    if exit_reason is None:
        # Max duration reached
        sim_exit_price = float(w_px[-1])
        sim_exit_ms = int(w_ts[-1])
        exit_reason = "max_duration"

    # Extend stop timeline to exit
    stop_timeline.append((sim_exit_ms, stop))

    if side == "LONG":
        sim_pnl_pts = sim_exit_price - entry
    else:
        sim_pnl_pts = entry - sim_exit_price

    orig_pnl_pts = (trade["pnl_points"] or 0) / (trade["entry_qty"] or 1)

    # Build P&L curve (subsampled)
    pnl_curve = []
    if collect_trajectory:
        step = max(1, len(w_ts) // 500)
        for i in range(0, len(w_ts), step):
            p = float(w_px[i])
            pnl = (p - entry) if side == "LONG" else (entry - p)
            pnl_curve.append((int(w_ts[i]), pnl))
        # Ensure exit point is included
        pnl_exit = sim_pnl_pts
        pnl_curve.append((sim_exit_ms, pnl_exit))

    return SimResult(
        trade_id=trade["id"],
        side=side,
        entry_ms=entry_ms,
        entry_price=entry,
        sim_exit_ms=sim_exit_ms,
        sim_exit_price=sim_exit_price,
        sim_pnl_pts=sim_pnl_pts,
        orig_exit_ms=trade["exit_time_ms"] or entry_ms,
        orig_exit_price=trade["exit_price"] or entry,
        orig_pnl_pts=orig_pnl_pts,
        exit_reason=exit_reason,
        n_stop_updates=n_stop_updates,
        stop_timeline=stop_timeline,
        pnl_curve=pnl_curve,
    )


def _worker(args):
    """Worker for parallel execution."""
    trade, ticks_dict, params = args
    # Reconstruct ticks dict from numpy arrays
    ticks = {"ts_ns": ticks_dict["ts_ns"], "price": ticks_dict["price"]}
    return simulate_trade(trade, ticks, **params)


def run_simulation_parallel(trades: list, tick_loader, *, bar_seconds=30, lookback=3,
                             offset_ticks=2, initial_stop_ticks=8, tick_size=0.25,
                             max_duration_minutes=60, workers=4,
                             collect_trajectory=False):
    """Run sim across all trades with multiprocessing."""
    # Pre-cache tick data per (symbol, date)
    tick_cache: dict[tuple, dict] = {}
    tasks = []
    params = {
        "bar_seconds": bar_seconds, "lookback": lookback,
        "offset_ticks": offset_ticks, "initial_stop_ticks": initial_stop_ticks,
        "tick_size": tick_size, "max_duration_minutes": max_duration_minutes,
        "collect_trajectory": collect_trajectory,
    }
    for t in trades:
        key = (t["root_symbol"], t["trade_date"])
        if key not in tick_cache:
            tick_cache[key] = tick_loader(t["root_symbol"], t["trade_date"])
        ticks = tick_cache[key]
        if ticks is not None:
            tasks.append((t, {"ts_ns": ticks["ts_ns"], "price": ticks["price"]}, params))

    results: list[SimResult] = []
    if workers > 1 and len(tasks) > 10:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_worker, tasks):
                if r is not None:
                    results.append(r)
    else:
        for task in tasks:
            r = _worker(task)
            if r is not None:
                results.append(r)

    if not results:
        return {"count": 0}

    orig = np.array([r.orig_pnl_pts for r in results])
    sim = np.array([r.sim_pnl_pts for r in results])

    exit_counts = {}
    for r in results:
        exit_counts[r.exit_reason] = exit_counts.get(r.exit_reason, 0) + 1

    return {
        "count": len(results),
        "orig_total_pts": round(float(orig.sum()), 2),
        "sim_total_pts": round(float(sim.sum()), 2),
        "delta_pts": round(float((sim - orig).sum()), 2),
        "orig_avg_pts": round(float(orig.mean()), 3),
        "sim_avg_pts": round(float(sim.mean()), 3),
        "orig_winrate": round(float((orig > 0).mean()), 4),
        "sim_winrate": round(float((sim > 0).mean()), 4),
        "orig_best": round(float(orig.max()), 2),
        "sim_best": round(float(sim.max()), 2),
        "orig_worst": round(float(orig.min()), 2),
        "sim_worst": round(float(sim.min()), 2),
        "exit_counts": exit_counts,
        "results": results,
    }


def plot_sample_trades(results: list, n: int = 9, outpath: str = "sim_samples.png",
                       tick_size: float = 0.25):
    """Create a grid plot showing P&L curve + trailing stop for sample trades."""
    picks = results[:n]
    _plot_trades_grid(picks, outpath, tick_size)


def plot_all_trades_paged(results: list, outdir: str, tick_size: float = 0.25,
                           per_page: int = 9, prefix: str = "sim"):
    """Paginate all trades into multi-page grid plots (9 per page by default)."""
    import os
    os.makedirs(outdir, exist_ok=True)
    # Sort chronologically for consistent pages
    sorted_res = sorted(results, key=lambda r: r.entry_ms)
    n_pages = (len(sorted_res) + per_page - 1) // per_page
    for p in range(n_pages):
        batch = sorted_res[p * per_page:(p + 1) * per_page]
        path = os.path.join(outdir, f"{prefix}_page_{p + 1:02d}.png")
        _plot_trades_grid(batch, path, tick_size, page_label=f"Page {p + 1}/{n_pages}")
    print(f"Wrote {n_pages} pages to {outdir}")


def _plot_trades_grid(picks: list, outpath: str, tick_size: float = 0.25,
                       page_label: str = ""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter
    from datetime import datetime, timezone, timedelta

    rows = (len(picks) + 2) // 3
    fig, axes = plt.subplots(rows, 3, figsize=(16, 4 * rows), facecolor="#0f1117")
    axes = axes.flatten() if rows > 1 else list(axes)
    # Style every axis for dark theme
    for ax in axes:
        ax.set_facecolor("#161922")
        ax.tick_params(colors="#e4e6f0", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#363952")
        ax.yaxis.label.set_color("#e4e6f0")
        ax.xaxis.label.set_color("#e4e6f0")
        ax.title.set_color("#e4e6f0")

    for ax, r in zip(axes, picks):
        if not r.pnl_curve:
            ax.axis("off")
            continue

        times = [datetime.fromtimestamp(t / 1000, tz=timezone.utc) - timedelta(hours=5)
                 for t, _ in r.pnl_curve]
        pnls_pts = [p for _, p in r.pnl_curve]
        # Convert to ticks for display
        pnls_ticks = [p / tick_size for p in pnls_pts]

        # Stop timeline as excursion (stop - entry for LONG; entry - stop for SHORT)
        stop_times = [datetime.fromtimestamp(t / 1000, tz=timezone.utc) - timedelta(hours=5)
                      for t, _ in r.stop_timeline]
        if r.side == "LONG":
            stop_excursion = [(s - r.entry_price) / tick_size for _, s in r.stop_timeline]
        else:
            stop_excursion = [(r.entry_price - s) / tick_size for _, s in r.stop_timeline]

        # Plot
        color = "#22c55e" if r.sim_pnl_pts >= 0 else "#ef4444"
        ax.plot(times, pnls_ticks, color=color, linewidth=1.2, label="Unrealized P&L")
        ax.fill_between(times, 0, pnls_ticks, color=color, alpha=0.15)

        # Stop line (step plot)
        ax.step(stop_times, stop_excursion, where="post",
                color="#f59e0b", linewidth=1.2, linestyle="--", label="Trailing Stop")

        ax.axhline(0, color="#666", linewidth=0.5)

        # Mark original exit
        if r.orig_exit_ms:
            orig_t = datetime.fromtimestamp(r.orig_exit_ms / 1000, tz=timezone.utc) - timedelta(hours=5)
            orig_y = r.orig_pnl_pts / tick_size
            ax.scatter([orig_t], [orig_y], color="#3b82f6", s=40, zorder=5, label="Original Exit")

        # Mark sim exit
        sim_t = datetime.fromtimestamp(r.sim_exit_ms / 1000, tz=timezone.utc) - timedelta(hours=5)
        sim_y = r.sim_pnl_pts / tick_size
        ax.scatter([sim_t], [sim_y], color=color, marker="X", s=80, zorder=5,
                   edgecolors="white", linewidth=1, label=f"Sim Exit ({r.exit_reason})")

        ax.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        for lbl in ax.get_xticklabels():
            lbl.set_color("#e4e6f0")
        for lbl in ax.get_yticklabels():
            lbl.set_color("#e4e6f0")
        title = (f"{r.side} @ {r.entry_price:.2f} | "
                 f"Orig: {r.orig_pnl_pts / tick_size:+.0f}t  Sim: {r.sim_pnl_pts / tick_size:+.0f}t "
                 f"({'+' if r.sim_pnl_pts > r.orig_pnl_pts else ''}"
                 f"{(r.sim_pnl_pts - r.orig_pnl_pts) / tick_size:+.0f}t)")
        ax.set_title(title, fontsize=10, color="#e4e6f0")
        ax.set_ylabel("Ticks", color="#e4e6f0")
        ax.grid(True, alpha=0.25, color="#363952")
        leg = ax.legend(fontsize=7, loc="best", facecolor="#1c1f2e", edgecolor="#363952")
        for text in leg.get_texts():
            text.set_color("#e4e6f0")

    for i in range(len(picks), len(axes)):
        axes[i].axis("off")

    if page_label:
        fig.suptitle(page_label, color="#9ca0b8", fontsize=11)
    plt.tight_layout()
    plt.savefig(outpath, dpi=100, facecolor="#0f1117")
    plt.close()


if __name__ == "__main__":
    import database as db
    from tick_data import _load_ticks

    conn = db.get_db()
    rows = conn.execute("""
        SELECT id, root_symbol, trade_date, side, entry_price, entry_qty,
               entry_time_ms, exit_time_ms, exit_price, pnl_points
        FROM trades
        WHERE account = ?  -- pass via argv or env AND side = 'LONG' AND is_open = 0
        ORDER BY entry_time_ms
    """).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Testing {len(trades)} LONG trades for APEX-05\n")

    # Narrow param sweep (extended trade duration now)
    print(f"{'bar_s':>5} {'lb':>3} {'off':>4} {'iSL':>4} {'maxMin':>7} "
          f"{'orig':>9} {'sim':>9} {'delta':>9} {'owr':>5} {'swr':>5} {'exits'}")
    print("-" * 100)

    best_params = None
    best_delta = -999999
    for bar_s in [15, 30, 60]:
        for lb in [2, 3]:
            for off in [2, 3]:
                for isl in [6, 8, 12]:
                    res = run_simulation_parallel(
                        trades, _load_ticks,
                        bar_seconds=bar_s, lookback=lb,
                        offset_ticks=off, initial_stop_ticks=isl,
                        max_duration_minutes=60, workers=8,
                    )
                    exits_str = ' '.join(f"{k[:4]}={v}" for k, v in res['exit_counts'].items())
                    print(f"{bar_s:>5} {lb:>3} {off:>4} {isl:>4} {60:>7} "
                          f"{res['orig_total_pts']:>9.2f} {res['sim_total_pts']:>9.2f} "
                          f"{res['delta_pts']:>+9.2f} "
                          f"{res['orig_winrate']*100:>4.1f}% {res['sim_winrate']*100:>4.1f}% "
                          f"{exits_str}")
                    if res['delta_pts'] > best_delta:
                        best_delta = res['delta_pts']
                        best_params = {'bar_seconds': bar_s, 'lookback': lb,
                                       'offset_ticks': off, 'initial_stop_ticks': isl}

    # Now generate plots using best params
    print(f"\nBest params: {best_params}")
    print("Generating sample trade visualizations...")
    res = run_simulation_parallel(trades, _load_ticks,
                                   **best_params, max_duration_minutes=60,
                                   workers=8, collect_trajectory=True)
    plot_sample_trades(res['results'], n=9,
                       outpath="./sim_samples.png")
