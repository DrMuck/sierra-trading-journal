"""
Correlation + (likely) causation analysis on a chosen account.

Methodology, inspired by López de Prado et al. "Can AI Learn Causal Structure?"
(ADIA Lab Causal Discovery Challenge, 2024 / SSRN-6125566):

  - Observational data alone CANNOT prove causation. But we can classify each
    feature into a causal category based on temporal precedence + subject-matter
    reasoning:

      EXOGENOUS_CAUSE  — happens outside your control, precedes entry
                         (time-of-day, day-of-week)
      PRE-ENTRY_CAUSE  — market state at entry, precedes outcome
                         (HTF trend, 1m vol, 15s delta)
      DECISION_PROXY   — describes your choice (side, qty, symbol)
                         can be confounded with tilt
      OUTCOME          — Y itself (pnl_ticks, win)
      CONFOUNDER       — affects both decision and outcome
                         (current loss streak, intraday drawdown)
      COLLIDER         — caused by BOTH your decision AND the outcome
                         (duration, exit_reason) — must NOT condition on these

  - We compute Pearson + Spearman correlations between each numeric feature and
    pnl_ticks; point-biserial for categorical; quintile-conditional P(win) to
    spot non-linearities.

  - For features that are EXOGENOUS (time/day) or PRE-ENTRY (vol/delta/trend at
    entry), strong correlation IS strong evidence of causation (no reverse
    causation possible since the feature was set before the trade).

  - For DECISION_PROXY features (side, symbol), correlations are confounded by
    your *choice* of when to take which trade — but we can still measure the
    conditional outcome.
"""
import argparse
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Callable

import numpy as np
from scipy import stats

from config import DB_PATH, TICK_SIZES
from tick_data import _load_ticks


# ---------------- Feature computation ----------------
def features_for(trade, ticks):
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
    sign = 1 if side == "LONG" else -1

    entry_dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc) - timedelta(hours=4)
    dow_num = entry_dt.weekday()  # 0=Mon..6=Sun
    mins_after_open = (entry_dt.hour - 9) * 60 + (entry_dt.minute - 30)

    def stat_window(win_s):
        m = (ts >= entry_ms - win_s * 1000) & (ts < entry_ms)
        p = px[m]; a = av[m]; b = bv[m]; v = vol[m]
        if len(p) < 2:
            return dict(trend=0.0, rng=0.0, delta=0, vol=0, cp=0.5)
        rng = float(p.max() - p.min())
        return dict(
            trend=float(p[-1] - p[0]) * sign,
            rng=rng,
            delta=int(a.sum() - b.sum()) * sign,
            vol=int(v.sum()),
            cp=float((entry - p.min()) / rng) if rng > 0 else 0.5,
        )

    f15s = stat_window(15)
    f1m = stat_window(60)
    f5m = stat_window(300)
    f15m = stat_window(900)

    # 15m pullback_pct
    m15m = (ts >= entry_ms - 900 * 1000) & (ts < entry_ms)
    p15m = px[m15m]
    pullback = 0.0
    if len(p15m) >= 5:
        hi = float(p15m.max()); lo = float(p15m.min()); rng = hi - lo
        if rng > 0:
            pullback = ((hi - entry) / rng) if side == "LONG" else ((entry - lo) / rng)

    # In-trade MFE/MAE
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
    pnl_ticks = pnl_pts / tick_size

    return {
        # OUTCOME
        "pnl_ticks": round(pnl_ticks, 2),
        "win": 1 if pnl_pts > 0 else 0,
        "mfe_ticks": round(mfe_pts / tick_size, 2),
        "mae_ticks": round(mae_pts / tick_size, 2),
        # EXOGENOUS (causal)
        "dow_num": dow_num,
        "is_monday": int(dow_num == 0),
        "mins_after_open": float(mins_after_open),
        # PRE-ENTRY market state (likely causal)
        "tr15s_dir": f15s["trend"],
        "tr1m_dir": f1m["trend"],
        "tr5m_dir": f5m["trend"],
        "tr15m_dir": f15m["trend"],
        "rng15s": f15s["rng"],
        "rng1m": f1m["rng"],
        "rng5m": f5m["rng"],
        "rng15m": f15m["rng"],
        "d15s_dir": f15s["delta"],
        "d1m_dir": f1m["delta"],
        "v15s": f15s["vol"],
        "v1m": f1m["vol"],
        "v5m": f5m["vol"],
        "cp1m": f1m["cp"],
        "cp15m": f15m["cp"],
        "pullback_pct": pullback,
        "compression": (f1m["rng"] / f5m["rng"]) if f5m["rng"] > 0.001 else 1.0,
        # DECISION_PROXY (confounded — your choice)
        "side": side,
        "qty": int(trade["entry_qty"] or 0),
        "symbol": symbol,
        # Metadata
        "trade_id": trade["id"],
        "trade_date": trade["trade_date"],
        "entry_time_ms": entry_ms,
    }


def annotate_intraday_state(feats):
    """For each trade compute pre-trade state: trade_idx_today, running_pnl,
    gap_since_last_trade, current_loss_streak, current_intraday_drawdown_dollars.
    These are CONFOUNDERS (tilt state)."""
    by_date = defaultdict(list)
    for f in feats:
        by_date[f["trade_date"]].append(f)
    for date, lst in by_date.items():
        lst.sort(key=lambda x: x["entry_time_ms"])
        running = 0.0
        peak = 0.0
        loss_streak = 0
        last_exit_ms = None
        for i, f in enumerate(lst):
            f["trade_idx_today"] = i
            f["running_pnl_pre"] = running
            f["intraday_dd_pre"] = peak - running  # how far below peak when entering
            f["loss_streak_pre"] = loss_streak
            f["gap_since_last_sec"] = (f["entry_time_ms"] - last_exit_ms) / 1000 if last_exit_ms else 99999
            # Update for next trade (using this trade's outcome)
            net_dollars = 0.0  # set from DB later if needed
            # Approximate dollars from pnl_ticks
            net_dollars = f["pnl_ticks"] * TICK_SIZES.get("ES", 0.25) * 50.0
            running += net_dollars
            if running > peak:
                peak = running
            if f["win"]:
                loss_streak = 0
            else:
                loss_streak += 1
            # No exit_time_ms in features — approximate
            last_exit_ms = f["entry_time_ms"] + 60_000  # rough
    return feats


def load_all_features(account, symbols=("ES", "MES", "NQ", "MNQ")):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(f"""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points, net_pnl
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account=? AND root_symbol IN ({placeholders})
         ORDER BY entry_time_ms
    """, (account, *symbols)).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    print(f"Loaded {len(trades)} closed trades for account={account}")

    cache = {}
    feats = []
    for t in trades:
        k = (t["root_symbol"], t["trade_date"])
        if k not in cache:
            cache[k] = _load_ticks(t["root_symbol"], t["trade_date"])
        f = features_for(t, cache[k])
        if f:
            feats.append(f)
    print(f"Computed features for {len(feats)} trades")
    annotate_intraday_state(feats)
    return feats


# ---------------- Stats helpers ----------------
NUMERIC_FEATS = [
    # EXOGENOUS
    ("mins_after_open",   "EXOGENOUS"),
    ("dow_num",           "EXOGENOUS"),
    ("is_monday",         "EXOGENOUS"),
    # PRE-ENTRY (likely causal)
    ("tr15s_dir",         "PRE_ENTRY"),
    ("tr1m_dir",          "PRE_ENTRY"),
    ("tr5m_dir",          "PRE_ENTRY"),
    ("tr15m_dir",         "PRE_ENTRY"),
    ("rng15s",            "PRE_ENTRY"),
    ("rng1m",             "PRE_ENTRY"),
    ("rng5m",             "PRE_ENTRY"),
    ("rng15m",            "PRE_ENTRY"),
    ("d15s_dir",          "PRE_ENTRY"),
    ("d1m_dir",           "PRE_ENTRY"),
    ("v15s",              "PRE_ENTRY"),
    ("v1m",               "PRE_ENTRY"),
    ("v5m",               "PRE_ENTRY"),
    ("cp1m",              "PRE_ENTRY"),
    ("cp15m",             "PRE_ENTRY"),
    ("pullback_pct",      "PRE_ENTRY"),
    ("compression",       "PRE_ENTRY"),
    # CONFOUNDER (tilt state — affects BOTH decision and outcome)
    ("trade_idx_today",   "CONFOUNDER"),
    ("running_pnl_pre",   "CONFOUNDER"),
    ("intraday_dd_pre",   "CONFOUNDER"),
    ("loss_streak_pre",   "CONFOUNDER"),
    ("gap_since_last_sec","CONFOUNDER"),
    # DECISION
    ("qty",               "DECISION"),
]

CATEG_FEATS = [
    ("side",   "DECISION"),
    ("symbol", "DECISION"),
]


def correlations(feats):
    y_pnl = np.array([f["pnl_ticks"] for f in feats], dtype=float)
    y_win = np.array([f["win"] for f in feats], dtype=float)
    n = len(feats)
    rows = []
    for name, role in NUMERIC_FEATS:
        x = np.array([f[name] for f in feats], dtype=float)
        if np.std(x) < 1e-9:
            continue
        pr_pnl, p_pnl = stats.pearsonr(x, y_pnl)
        sp_pnl, ps_pnl = stats.spearmanr(x, y_pnl)
        pr_win, p_win = stats.pointbiserialr(y_win, x)  # x continuous, y binary
        rows.append({
            "feature": name, "role": role, "n": n,
            "pearson_pnl": pr_pnl, "p_pearson": p_pnl,
            "spearman_pnl": sp_pnl, "p_spearman": ps_pnl,
            "biserial_win": pr_win, "p_biserial": p_win,
        })
    return rows


def quintile_conditional(feats, feature_name, y_name="pnl_ticks"):
    """For each quintile of `feature_name`, compute mean(pnl_ticks) and WR."""
    valid = [(f[feature_name], f[y_name], f["win"]) for f in feats
             if f[feature_name] is not None]
    if not valid:
        return []
    xs = np.array([v[0] for v in valid], dtype=float)
    ys = np.array([v[1] for v in valid], dtype=float)
    wins = np.array([v[2] for v in valid], dtype=float)
    if np.std(xs) < 1e-9:
        return []
    qs = np.quantile(xs, [0.2, 0.4, 0.6, 0.8])
    bins = np.digitize(xs, qs)
    out = []
    for q in range(5):
        m = bins == q
        if m.sum() == 0:
            continue
        out.append({
            "quintile": q + 1, "n": int(m.sum()),
            "feat_range": (float(xs[m].min()), float(xs[m].max())),
            "mean_pnl": float(ys[m].mean()),
            "wr": float(wins[m].mean()),
        })
    return out


def categorical_effect(feats, feature_name):
    """For categorical features: per-category WR + mean PnL + chi-squared p-value."""
    cats = defaultdict(list)
    for f in feats:
        cats[f[feature_name]].append(f)
    rows = []
    for cat, lst in cats.items():
        wr = sum(x["win"] for x in lst) / len(lst)
        mean_pnl = sum(x["pnl_ticks"] for x in lst) / len(lst)
        rows.append({"cat": cat, "n": len(lst), "wr": wr, "mean_pnl": mean_pnl})

    # Chi-squared on win/lose contingency
    contingency = []
    for cat, lst in cats.items():
        wins = sum(x["win"] for x in lst)
        contingency.append([wins, len(lst) - wins])
    chi2, p_chi, _, _ = stats.chi2_contingency(contingency)

    # Kruskal-Wallis on pnl_ticks (non-parametric ANOVA)
    groups = [[x["pnl_ticks"] for x in lst] for lst in cats.values()]
    if len(groups) >= 2 and all(len(g) >= 2 for g in groups):
        h, p_kw = stats.kruskal(*groups)
    else:
        h = 0.0; p_kw = 1.0
    return rows, {"chi2": chi2, "p_chi": p_chi, "kruskal_h": h, "p_kruskal": p_kw}


def interaction_effects(feats, top_n=10):
    """Pairwise feature interactions: which 2-feature combinations have the strongest
    differentiated outcomes. Uses sign-adjusted z-score from the mean WR / mean pnl.
    """
    # Discretize each numeric feature into 2 buckets (above/below median)
    medians = {}
    for name, _ in NUMERIC_FEATS:
        vals = np.array([f[name] for f in feats], dtype=float)
        if np.std(vals) > 1e-9:
            medians[name] = float(np.median(vals))
    base_wr = sum(f["win"] for f in feats) / len(feats)
    base_pnl = sum(f["pnl_ticks"] for f in feats) / len(feats)

    results = []
    fnames = list(medians.keys())
    for i in range(len(fnames)):
        for j in range(i + 1, len(fnames)):
            f1, f2 = fnames[i], fnames[j]
            for s1 in (False, True):
                for s2 in (False, True):
                    lst = [f for f in feats
                           if (f[f1] >= medians[f1]) == s1 and (f[f2] >= medians[f2]) == s2]
                    if len(lst) < 30:
                        continue
                    wr = sum(f["win"] for f in lst) / len(lst)
                    mean_pnl = sum(f["pnl_ticks"] for f in lst) / len(lst)
                    # delta from base
                    dwr = wr - base_wr
                    dpnl = mean_pnl - base_pnl
                    results.append({
                        "f1": f1, "high1": s1, "f2": f2, "high2": s2,
                        "n": len(lst), "wr": wr, "delta_wr": dwr,
                        "mean_pnl": mean_pnl, "delta_pnl": dpnl,
                    })
    results.sort(key=lambda x: x["delta_pnl"])
    return results, base_wr, base_pnl


# ---------------- Report ----------------
def report(feats):
    # Section 1: correlations
    print("\n========== CORRELATIONS vs pnl_ticks ==========")
    rows = correlations(feats)
    print(f"  {'feature':<22} {'role':<11}  pearson  p_p     spearman  p_s     bi-win   p_b")
    for r in sorted(rows, key=lambda x: -abs(x["spearman_pnl"])):
        print(f"  {r['feature']:<22} {r['role']:<11}  "
              f"{r['pearson_pnl']:+.3f}  {r['p_pearson']:.3f}   "
              f"{r['spearman_pnl']:+.3f}  {r['p_spearman']:.3f}   "
              f"{r['biserial_win']:+.3f}  {r['p_biserial']:.3f}")

    # Section 2: per-feature quintile breakdown for the top-7 strongest
    top_feats = sorted(rows, key=lambda x: -abs(x["spearman_pnl"]))[:7]
    print("\n========== QUINTILE BREAKDOWN (top 7 features by |spearman|) ==========")
    for r in top_feats:
        name = r["feature"]
        q = quintile_conditional(feats, name)
        print(f"\n  {name}  ({r['role']})  spearman={r['spearman_pnl']:+.3f}")
        for entry in q:
            print(f"    Q{entry['quintile']}  n={entry['n']:>3}  "
                  f"range=[{entry['feat_range'][0]:>7.2f}..{entry['feat_range'][1]:>7.2f}]  "
                  f"mean_pnl={entry['mean_pnl']:>+5.2f}t  WR={entry['wr']*100:>4.1f}%")

    # Section 3: categorical features
    print("\n========== CATEGORICAL FEATURES ==========")
    for name, role in CATEG_FEATS:
        cats, sig = categorical_effect(feats, name)
        print(f"\n  {name}  ({role})  chi2 p={sig['p_chi']:.4f}  "
              f"kruskal p={sig['p_kruskal']:.4f}")
        for c in sorted(cats, key=lambda x: -x["mean_pnl"]):
            print(f"    {str(c['cat']):<8}  n={c['n']:>3}  "
                  f"WR={c['wr']*100:>4.1f}%  mean_pnl={c['mean_pnl']:+.2f}t")

    # Section 4: top interactions
    print("\n========== TOP 10 BEST PAIR INTERACTIONS (highest mean_pnl) ==========")
    inter, base_wr, base_pnl = interaction_effects(feats)
    print(f"  Base: WR={base_wr*100:.1f}%  mean_pnl={base_pnl:+.2f}t")
    print(f"  {'feat1':<22} {'>=med':<7}  {'feat2':<22} {'>=med':<7}  "
          f"{'n':>4} {'WR':>6} {'mean_pnl':>9}")
    # Top by mean_pnl (sorted ascending, so reverse for top)
    for r in inter[-10:][::-1]:
        print(f"  {r['f1']:<22} {str(r['high1']):<7}  {r['f2']:<22} {str(r['high2']):<7}  "
              f"{r['n']:>4} {r['wr']*100:>5.1f}% {r['mean_pnl']:>+8.2f}t")

    print("\n========== TOP 10 WORST PAIR INTERACTIONS (lowest mean_pnl) ==========")
    for r in inter[:10]:
        print(f"  {r['f1']:<22} {str(r['high1']):<7}  {r['f2']:<22} {str(r['high2']):<7}  "
              f"{r['n']:>4} {r['wr']*100:>5.1f}% {r['mean_pnl']:>+8.2f}t")

    # Section 5: Causal classification (likely causal vs confounded)
    print("\n========== CAUSAL INTERPRETATION (per López de Prado framework) ==========")
    print("Features ordered by correlation strength + role:")
    print()
    print("  LIKELY-CAUSAL (exogenous or pre-entry market state; precedes outcome):")
    for r in sorted([x for x in rows if x["role"] in ("EXOGENOUS", "PRE_ENTRY")],
                    key=lambda x: -abs(x["spearman_pnl"]))[:10]:
        sig = "★" if abs(r["spearman_pnl"]) > 0.1 and r["p_spearman"] < 0.01 else " "
        direction = "+" if r["spearman_pnl"] > 0 else "−"
        print(f"    {sig} {r['feature']:<22} {direction} (rho={r['spearman_pnl']:+.3f}, p={r['p_spearman']:.4f})")
    print()
    print("  CONFOUNDED (tilt state — affects both your decision and outcome;")
    print("  correlation here reflects 'how you trade when you're in this state',")
    print("  not 'this state causes losses' in a clean sense):")
    for r in sorted([x for x in rows if x["role"] == "CONFOUNDER"],
                    key=lambda x: -abs(x["spearman_pnl"])):
        sig = "★" if abs(r["spearman_pnl"]) > 0.1 and r["p_spearman"] < 0.01 else " "
        direction = "+" if r["spearman_pnl"] > 0 else "−"
        print(f"    {sig} {r['feature']:<22} {direction} (rho={r['spearman_pnl']:+.3f}, p={r['p_spearman']:.4f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--symbols", default="ES,MES,NQ,MNQ")
    args = ap.parse_args()
    syms = tuple(s.strip().upper() for s in args.symbols.split(","))
    feats = load_all_features(args.account, syms)
    if not feats:
        print("No features computed.")
    else:
        report(feats)
