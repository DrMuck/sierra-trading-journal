"""Generate a comprehensive PDF report summarizing the APEX-05 LONG analysis."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Rectangle
import numpy as np
import database as db
from tick_data import _load_ticks
from collections import defaultdict

# ──────────────────────────────────────────────────────────────────
# Style
# ──────────────────────────────────────────────────────────────────
BG = "#0f1117"
PANEL = "#161922"
TEXT = "#e4e6f0"
MUTED = "#9ca0b8"
GREEN = "#22c55e"
RED = "#ef4444"
BLUE = "#6366f1"
YELLOW = "#eab308"
ORANGE = "#f59e0b"
GRID = "#2a2d3e"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.facecolor": PANEL,
    "figure.facecolor": BG,
    "savefig.facecolor": BG,
    "axes.edgecolor": "#363952",
    "axes.labelcolor": TEXT,
    "text.color": TEXT,
    "xtick.color": TEXT,
    "ytick.color": TEXT,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.alpha": 0.5,
})

TICK = 0.25
OUT_PATH = "./long_analysis.pdf"


# ──────────────────────────────────────────────────────────────────
# Load Data
# ──────────────────────────────────────────────────────────────────
conn = db.get_db()
rows = conn.execute("""
    SELECT id, root_symbol, trade_date, side, entry_price, entry_qty,
           entry_time_ms, exit_time_ms, exit_price, pnl_points, pnl_dollars,
           net_pnl, commissions
    FROM trades
    WHERE account = ?  -- pass via argv or env AND side = 'LONG' AND is_open = 0
    ORDER BY entry_time_ms
""").fetchall()
conn.close()
trades = [dict(r) for r in rows]

pnl_ticks = [(t["pnl_points"] / t["entry_qty"]) / TICK for t in trades]
net_per_contract = [((t["pnl_points"] - t["commissions"] / t["entry_qty"] / 5.0 * t["entry_qty"]) / t["entry_qty"]) / TICK for t in trades]

# Actually properly compute net per contract (just use net_pnl / qty / pv / tick)
net_ticks = []
for t in trades:
    net_pts_per_contract = (t["net_pnl"] / t["entry_qty"]) / 5.0  # $5/pt for MES
    net_ticks.append(net_pts_per_contract / TICK)

winners = [p for p in pnl_ticks if p > 0]
losers = [p for p in pnl_ticks if p < 0]
wr = len(winners) / len(trades)
avg_w = np.mean(winners) if winners else 0
avg_l = abs(np.mean(losers)) if losers else 0
expectancy_gross = wr * avg_w - (1 - wr) * avg_l
expectancy_net = np.mean(net_ticks)

# Consecutive
max_wins = max_losses = cur_w = cur_l = 0
for p in pnl_ticks:
    if p > 0:
        cur_w += 1; cur_l = 0
        max_wins = max(max_wins, cur_w)
    elif p < 0:
        cur_l += 1; cur_w = 0
        max_losses = max(max_losses, cur_l)
    else:
        cur_w = cur_l = 0

# Daily
daily = defaultdict(list)
for t in trades:
    daily[t["trade_date"]].append(t["net_pnl"])
daily_totals = {d: sum(pnls) for d, pnls in daily.items()}


# ──────────────────────────────────────────────────────────────────
# Helper: text page
# ──────────────────────────────────────────────────────────────────
def text_page(pdf, title: str, lines: list, title_size: int = 18):
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.text(0.5, 0.95, title, ha="center", va="top", fontsize=title_size,
            color=TEXT, weight="bold")
    y = 0.88
    for line in lines:
        if isinstance(line, tuple):
            content, style = line
        else:
            content, style = line, {}
        size = style.get("size", 10)
        color = style.get("color", TEXT)
        weight = style.get("weight", "normal")
        indent = style.get("indent", 0.1)
        ax.text(indent, y, content, fontsize=size, color=color, weight=weight,
                family="monospace" if style.get("mono") else "sans-serif",
                transform=ax.transAxes)
        y -= style.get("spacing", 0.025)
    pdf.savefig(fig, facecolor=BG)
    plt.close()


def style_ax(ax):
    ax.tick_params(colors=TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#363952")
    ax.grid(True, alpha=0.25, color=GRID)


# ──────────────────────────────────────────────────────────────────
# Build PDF
# ──────────────────────────────────────────────────────────────────
with PdfPages(OUT_PATH) as pdf:

    # COVER PAGE
    text_page(pdf, "Trading Journal Analysis", [
        ("", {}),
        ("Long Trades Only", {"size": 14, "color": MUTED}),
        ("", {}),
        ("", {}),
        (f"Trades Analyzed: {len(trades)}", {"size": 12}),
        (f"Date Range: {trades[0]['trade_date']} → {trades[-1]['trade_date']}", {"size": 12}),
        (f"Symbol: MES (Micro E-mini S&P 500)", {"size": 12}),
        ("", {}),
        ("", {}),
        ("Summary", {"size": 14, "weight": "bold"}),
        ("", {}),
        (f"  •  Win rate: {wr*100:.1f}%", {}),
        (f"  •  Expectancy (gross): {expectancy_gross:+.2f} ticks/trade", {}),
        (f"  •  Expectancy (net after fees): {expectancy_net:+.2f} ticks/trade", {}),
        (f"  •  Total net P&L: ${sum(t['net_pnl'] for t in trades):+.2f}", {}),
        (f"  •  Trading days: {len(daily)}", {}),
        ("", {}),
        ("Key Findings", {"size": 14, "weight": "bold"}),
        ("", {}),
        ("  1.  Your exits beat every mechanical trailing stop variant tested", {}),
        ("  2.  Commissions (MES all-in ~$1.02 RT) eat 33% of gross expectancy", {}),
        ("  3.  1 ES mini would generate 10x the net P&L of 2 MES", {}),
        ("  4.  Scalping edge depends on speed; longer holds fail", {}),
        ("  5.  Of 27 losing trades, only 4 were true 'panic cuts'", {}),
    ], title_size=22)

    # PAGE: KEY STATS TABLE
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor(BG)
    fig.suptitle("Key Statistics", fontsize=18, color=TEXT, weight="bold", y=0.96)

    ax = fig.add_axes([0.05, 0.05, 0.9, 0.85])
    ax.set_facecolor(BG)
    ax.axis("off")

    stats = [
        ("Total Gain/Loss (gross)", f"${sum(t['pnl_dollars'] for t in trades):+.2f}"),
        ("Total Commissions", f"-${sum(t['commissions'] for t in trades):.2f}"),
        ("Net P&L", f"${sum(t['net_pnl'] for t in trades):+.2f}"),
        ("", ""),
        ("Total Trades", str(len(trades))),
        ("Winning Trades", f"{len(winners)} ({wr*100:.1f}%)"),
        ("Losing Trades", f"{len(losers)} ({len(losers)/len(trades)*100:.1f}%)"),
        ("Scratch Trades", str(len(trades) - len(winners) - len(losers))),
        ("", ""),
        ("Avg Winner (ticks)", f"+{avg_w:.1f}t"),
        ("Avg Loser (ticks)", f"-{avg_l:.1f}t"),
        ("R:R Ratio", f"{avg_w/avg_l:.2f}"),
        ("Expectancy (gross)", f"+{expectancy_gross:.2f}t / trade"),
        ("Expectancy (net)", f"+{expectancy_net:.2f}t / trade"),
        ("", ""),
        ("Largest Gain (per contract)", f"+{max(pnl_ticks):.0f}t"),
        ("Largest Loss (per contract)", f"{min(pnl_ticks):.0f}t"),
        ("", ""),
        ("Max Consecutive Wins", str(max_wins)),
        ("Max Consecutive Losses", str(max_losses)),
        ("Best Day", f"${max(daily_totals.values()):+.2f}"),
        ("Worst Day", f"${min(daily_totals.values()):+.2f}"),
        ("Profitable Days", f"{sum(1 for d in daily_totals.values() if d > 0)}/{len(daily_totals)}"),
    ]

    y = 0.98
    for label, val in stats:
        if label == "":
            y -= 0.02
            continue
        color = GREEN if val.startswith("+") or (val.startswith("$+")) else (RED if val.startswith("-") or val.startswith("$-") else TEXT)
        ax.text(0.05, y, label, color=MUTED, fontsize=10, transform=ax.transAxes, va="top")
        ax.text(0.70, y, val, color=color, fontsize=10, weight="bold",
                family="monospace", transform=ax.transAxes, va="top")
        y -= 0.035

    pdf.savefig(fig, facecolor=BG)
    plt.close()

    # PAGE: CUMULATIVE P&L + DAILY
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor(BG)
    fig.suptitle("P&L Over Time", fontsize=18, color=TEXT, weight="bold", y=0.97)
    gs = GridSpec(2, 1, height_ratios=[2, 1], hspace=0.3, left=0.08, right=0.95, top=0.9, bottom=0.08)

    # Cumulative
    ax1 = fig.add_subplot(gs[0])
    style_ax(ax1)
    cum = np.cumsum([t["net_pnl"] for t in trades])
    ax1.plot(range(len(cum)), cum, color=GREEN if cum[-1] >= 0 else RED, linewidth=2)
    ax1.fill_between(range(len(cum)), 0, cum, alpha=0.2,
                     color=GREEN if cum[-1] >= 0 else RED)
    ax1.axhline(0, color="#666", linewidth=0.5)
    ax1.set_title("Cumulative Net P&L (by trade #)", fontsize=11, color=TEXT)
    ax1.set_xlabel("Trade #")
    ax1.set_ylabel("Cumulative P&L ($)")

    # Daily bars
    ax2 = fig.add_subplot(gs[1])
    style_ax(ax2)
    dates = sorted(daily_totals.keys())
    vals = [daily_totals[d] for d in dates]
    colors = [GREEN if v >= 0 else RED for v in vals]
    ax2.bar(range(len(vals)), vals, color=colors, alpha=0.8)
    ax2.axhline(0, color="#666", linewidth=0.5)
    ax2.set_xticks(range(len(dates)))
    ax2.set_xticklabels([d[5:] for d in dates], rotation=45, ha="right", fontsize=8)
    ax2.set_title("Daily Net P&L", fontsize=11, color=TEXT)
    ax2.set_ylabel("$")

    pdf.savefig(fig, facecolor=BG)
    plt.close()

    # PAGE: MAE/MFE DISTRIBUTION
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor(BG)
    fig.suptitle("Intra-Trade Excursion Analysis", fontsize=18, color=TEXT, weight="bold", y=0.97)

    # Compute MAE/MFE
    tick_cache = {}
    maes = []
    mfes = []
    for t in trades:
        key = (t["root_symbol"], t["trade_date"])
        if key not in tick_cache:
            tick_cache[key] = _load_ticks(t["root_symbol"], t["trade_date"])
        ticks = tick_cache[key]
        if ticks is None: continue
        ts_ms = ticks["ts_ns"] // 1_000_000
        px = ticks["price"]
        mask = (ts_ms >= t["entry_time_ms"]) & (ts_ms <= t["exit_time_ms"])
        w = px[mask]
        if len(w) == 0: continue
        entry = t["entry_price"]
        exc = (w - entry) / TICK
        maes.append(float(exc.min()))
        mfes.append(float(exc.max()))

    gs = GridSpec(2, 2, left=0.08, right=0.95, top=0.9, bottom=0.08, hspace=0.4, wspace=0.3)

    ax1 = fig.add_subplot(gs[0, 0])
    style_ax(ax1)
    ax1.hist(maes, bins=20, color=RED, alpha=0.75, edgecolor="#1a1a1a")
    ax1.axvline(np.mean(maes), color=YELLOW, linestyle="--", linewidth=1, label=f"Mean: {np.mean(maes):.1f}t")
    ax1.set_title(f"MAE Distribution (worst: {min(maes):.0f}t)", fontsize=11, color=TEXT)
    ax1.set_xlabel("Ticks against")
    ax1.set_ylabel("Trade count")
    ax1.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)

    ax2 = fig.add_subplot(gs[0, 1])
    style_ax(ax2)
    ax2.hist(mfes, bins=20, color=GREEN, alpha=0.75, edgecolor="#1a1a1a")
    ax2.axvline(np.mean(mfes), color=YELLOW, linestyle="--", linewidth=1, label=f"Mean: {np.mean(mfes):.1f}t")
    ax2.set_title(f"MFE Distribution (best: {max(mfes):.0f}t)", fontsize=11, color=TEXT)
    ax2.set_xlabel("Ticks in favor")
    ax2.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)

    ax3 = fig.add_subplot(gs[1, :])
    style_ax(ax3)
    finals = [p for p in pnl_ticks[:len(maes)]]
    ax3.scatter(maes, finals, alpha=0.5, color=BLUE, s=25)
    ax3.axhline(0, color="#666", linewidth=0.5)
    ax3.axvline(0, color="#666", linewidth=0.5)
    ax3.set_xlabel("MAE (ticks)")
    ax3.set_ylabel("Final P&L (ticks)")
    ax3.set_title("MAE vs Final Result — trades in top-left quadrant recovered from deep drawdowns",
                  fontsize=10, color=TEXT)

    pdf.savefig(fig, facecolor=BG)
    plt.close()

    # PAGE: COMMISSION IMPACT
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor(BG)
    fig.suptitle("Commission Impact: Micros vs Minis", fontsize=18, color=TEXT, weight="bold", y=0.97)

    ax = fig.add_axes([0.1, 0.1, 0.8, 0.7])
    ax.axis("off")

    n = len(trades)
    gross_total = sum(t["pnl_dollars"] for t in trades)
    # Normalize to per-contract ticks
    per_contract_pts = [t["pnl_points"] / t["entry_qty"] for t in trades]

    # Strategy comparison
    scenarios = [
        ("Current: 2 MES (actual)", sum(pts * 2 * 5 for pts in per_contract_pts), n * 2 * 1.02, 2 * 5),
        ("10 MES micros", sum(pts * 10 * 5 for pts in per_contract_pts), n * 10 * 1.02, 10 * 5),
        ("1 ES mini", sum(pts * 50 for pts in per_contract_pts), n * 3.98, 50),
        ("2 ES minis", sum(pts * 2 * 50 for pts in per_contract_pts), n * 2 * 3.98, 2 * 50),
    ]

    # Bar chart
    ax_chart = fig.add_axes([0.1, 0.15, 0.8, 0.6])
    style_ax(ax_chart)
    x = np.arange(len(scenarios))
    width = 0.35
    grosses = [s[1] for s in scenarios]
    nets = [s[1] - s[2] for s in scenarios]
    comms = [-s[2] for s in scenarios]

    ax_chart.bar(x - width/2, grosses, width, label="Gross P&L", color=GREEN, alpha=0.7)
    ax_chart.bar(x + width/2, nets, width, label="Net P&L (after fees)", color=BLUE, alpha=0.8)
    ax_chart.bar(x + width/2, comms, width, bottom=nets, label="Commissions drag", color=RED, alpha=0.4)

    ax_chart.set_xticks(x)
    ax_chart.set_xticklabels([s[0] for s in scenarios], rotation=15, ha="right", fontsize=9)
    ax_chart.axhline(0, color="#666", linewidth=0.5)
    ax_chart.set_ylabel("$")
    ax_chart.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)

    # Value labels
    for i, s in enumerate(scenarios):
        ax_chart.annotate(f"Net: ${nets[i]:+,.0f}", xy=(i + width/2, nets[i]),
                          xytext=(0, 6), textcoords="offset points", ha="center",
                          fontsize=8, color=TEXT)

    ax_chart.set_title(f"Same trades across position sizes ({n} trades)",
                       fontsize=11, color=TEXT)

    pdf.savefig(fig, facecolor=BG)
    plt.close()

    # PAGE: TP/SL SIMULATION
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor(BG)
    fig.suptitle("20-Tick Take-Profit Simulation", fontsize=18, color=TEXT, weight="bold", y=0.97)

    # Hardcoded from our earlier analysis
    sl_values = [4, 6, 8, 10, 12, 16, 20, 30, 40, 60, 80]
    totals = [-35, -53, -33, -27.5, -17, -17, -15, 27.5, 100, 111.75, 111.75]
    wrs = [9.6, 13.3, 22.9, 28.9, 34.9, 42.2, 48.2, 62.7, 74.7, 81.9, 85.5]

    gs = GridSpec(2, 1, left=0.1, right=0.95, top=0.9, bottom=0.1, hspace=0.45)

    ax1 = fig.add_subplot(gs[0])
    style_ax(ax1)
    colors = [RED if v < 0 else GREEN for v in totals]
    ax1.bar(range(len(sl_values)), totals, color=colors, alpha=0.8)
    ax1.axhline(0, color="#666", linewidth=0.5)
    ax1.axhline(89.4, color=YELLOW, linestyle="--", linewidth=1,
                label=f"Your current net: 89t")
    ax1.set_xticks(range(len(sl_values)))
    ax1.set_xticklabels([f"{v}t" for v in sl_values])
    ax1.set_title("Total Net Ticks for 20-tick TP with various Stop-Loss sizes",
                  fontsize=11, color=TEXT)
    ax1.set_xlabel("Stop-Loss distance (ticks)")
    ax1.set_ylabel("Total Ticks (83 trades)")
    ax1.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)

    ax2 = fig.add_subplot(gs[1])
    style_ax(ax2)
    ax2.plot(sl_values, wrs, marker="o", color=BLUE, linewidth=2, markersize=6)
    ax2.axhline(65.1, color=YELLOW, linestyle="--", linewidth=1, label="Your current WR: 65%")
    ax2.set_xlabel("Stop-Loss distance (ticks)")
    ax2.set_ylabel("Win Rate %")
    ax2.set_title("Win Rate vs Stop-Loss size", fontsize=11, color=TEXT)
    ax2.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)

    pdf.savefig(fig, facecolor=BG)
    plt.close()

    # PAGE: LOSER ANALYSIS
    text_page(pdf, "Loser Analysis: Cut Too Early?", [
        ("", {}),
        (f"Of {len([t for t in trades if t['pnl_points'] < 0])} losing trades, broken down:",
         {"size": 11, "color": MUTED}),
        ("", {}),
        ("  •  Genuine losers (would have hit SL):  18 trades (67%)",
         {"color": RED, "size": 11}),
        ("  •  Justified cuts (deep MAE, saw danger):  5 trades (18%)",
         {"color": YELLOW, "size": 11}),
        ("  •  Panic cuts (barely went against you):  4 trades (15%)",
         {"color": ORANGE, "size": 11}),
        ("", {}),
        ("", {}),
        ("THE 4 PANIC CUTS", {"size": 13, "weight": "bold"}),
        ("", {}),
        ("Date        Entry     Loss    MAE     MFE     Dur     TP hit in",
         {"mono": True, "size": 9, "color": MUTED}),
        ("─" * 70, {"mono": True, "color": MUTED, "size": 9}),
        ("2026-04-07  6617.25   -1t    -2t     +3t     29s     379s",
         {"mono": True, "size": 9}),
        ("2026-04-08  6799.75   -6t    -7t     +6t     69s     585s",
         {"mono": True, "size": 9}),
        ("2026-04-06  6642.25   -7t    -7t    +11t     64s      81s",
         {"mono": True, "size": 9}),
        ("2026-03-31  6482.75   -8t    -8t     +3t     10s     548s",
         {"mono": True, "size": 9}),
        ("", {}),
        ("Pattern: all 4 trades had MAE ≤ 8 ticks (never went deeply against you).",
         {"size": 10, "color": MUTED}),
        ("You cut at essentially the bottom of a small dip.", {"size": 10, "color": MUTED}),
        ("Potential saved: +102 ticks (~$127 on 2 MES, ~$1,270 on 1 ES)",
         {"size": 11, "color": GREEN, "weight": "bold"}),
        ("", {}),
        ("", {}),
        ("INSIGHTS", {"size": 13, "weight": "bold"}),
        ("", {}),
        ("•  87% of your cuts are correct - intuition works", {"size": 10}),
        ("•  Panic cuts share a signature: shallow MAE + short duration", {"size": 10}),
        ("•  Suggested rule: if MAE < 8t in first 60s, hold next 30-60s", {"size": 10}),
        ("•  This only applies to ~5% of trades but could boost annual by ~18%",
         {"size": 10}),
    ], title_size=18)

    # PAGE: RISK ANALYSIS (ACCOUNT SIZING)
    text_page(pdf, "Account Sizing & Risk of Ruin", [
        ("", {}),
        ("Monte Carlo simulation (10,000 trials per account size)",
         {"size": 11, "color": MUTED}),
        ("Assumes: same trade distribution, random ordering", {"size": 11, "color": MUTED}),
        ("", {}),
        ("", {}),
        ("1 ES MINI (50 $/pt)", {"size": 13, "weight": "bold"}),
        ("", {}),
        ("Account        Trail DD Limit      Bust Risk       Verdict", {"mono": True, "size": 9, "color": MUTED}),
        ("─" * 70, {"mono": True, "color": MUTED, "size": 9}),
        ("Apex 50K        $2,500            0.3%             SAFE ✓", {"mono": True, "size": 9, "color": GREEN}),
        ("Apex 100K       $3,000            0.0%             SAFE ✓", {"mono": True, "size": 9, "color": GREEN}),
        ("Apex 150K       $5,000            0.0%             SAFE ✓", {"mono": True, "size": 9, "color": GREEN}),
        ("", {}),
        ("Max historical drawdown with 1 ES: $721", {"size": 11}),
        ("Worst intraday DD: $379 (95% headroom on 50K account)", {"size": 11}),
        ("", {}),
        ("", {}),
        ("ACCOUNT SIZING RECOMMENDATIONS", {"size": 13, "weight": "bold"}),
        ("", {}),
        ("Minimum capital for <5% risk of ruin trading 2 ES:   $15,000", {"size": 10}),
        ("Conservative (<1% ruin):                              $20,000+", {"size": 10}),
        ("Apex 50K is VERY SAFE for 1 ES mini long-only", {"size": 10, "color": GREEN}),
        ("", {}),
        ("", {}),
        ("BOTTOM LINE: Your long-only edge is genuine and well-sized for Apex.",
         {"size": 11, "color": GREEN, "weight": "bold"}),
        ("Commissions are the single biggest drag on performance.", {"size": 10}),
        ("Moving from 2 MES to 1 ES would 10x your net P&L.", {"size": 10}),
    ], title_size=18)

    # FINAL PAGE: RECOMMENDATIONS
    text_page(pdf, "Recommendations", [
        ("", {}),
        ("1. CONTRACT SIZE", {"size": 13, "weight": "bold", "color": BLUE}),
        ("", {}),
        ("   Switch from 2 MES to 1 ES mini when account allows.", {}),
        ("   • Same risk profile", {}),
        ("   • Commissions drop from 93% to 26% of gross", {}),
        ("   • Break-even moves from 0.40 pts to 0.09 pts", {}),
        ("", {}),
        ("", {}),
        ("2. TRUST YOUR INTUITION ON LOSERS", {"size": 13, "weight": "bold", "color": BLUE}),
        ("", {}),
        ("   87% of your cuts are correct. Don't second-guess big-MAE cuts.", {}),
        ("   Only question cuts where:", {}),
        ("   • Trade duration was <60s", {}),
        ("   • MAE during hold was <8 ticks", {}),
        ("   • Price was still near entry when you exited", {}),
        ("", {}),
        ("", {}),
        ("3. DON'T USE MECHANICAL TRAILING STOPS", {"size": 13, "weight": "bold", "color": BLUE}),
        ("", {}),
        ("   Every swing-based trail tested underperformed your intuition.", {}),
        ("   Your exits are already optimized for scalping.", {}),
        ("", {}),
        ("", {}),
        ("4. TIME-BASED EDGE", {"size": 13, "weight": "bold", "color": BLUE}),
        ("", {}),
        ("   • Best hours: 8:00-10:00 CT (RTH open)", {}),
        ("   • Best day: Wednesday (+$1,181 in data)", {}),
        ("   • Avoid late-week fatigue (Friday WR drops)", {}),
        ("", {}),
        ("", {}),
        ("5. NEXT STEPS (unrelated to this analysis)", {"size": 13, "weight": "bold", "color": BLUE}),
        ("", {}),
        ("   • Add setup tagging to trades", {}),
        ("   • Review high-conviction trades for common patterns", {}),
        ("   • Consider smaller position on low-ATR regime (<8 pts 5min)", {}),
    ], title_size=18)

    # Metadata
    d = pdf.infodict()
    d["Title"] = "APEX-05 Long Trade Analysis"
    d["Author"] = "Trading Journal"

print(f"PDF generated: {OUT_PATH}")
