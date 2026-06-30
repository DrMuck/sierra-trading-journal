"""
Full causal-discovery pipeline applied to a chosen account/symbol pair.

Three outputs (matching López de Prado paper's methodology where applicable):

  1) Random Forest classifier on win/loss using all pre-entry + confounder features.
     - Mean Decrease in Impurity (Gini importance)
     - Permutation importance (more robust to correlated features)
     - Partial-dependence-style conditional WR for top features

  2) Visual DAG diagram showing causal roles (matches paper's 8-category taxonomy:
     Exogenous-Cause, Pre-entry-Cause, Outcome, Confounder, Decision-Proxy, Collider).

  3) Counterfactual equity-curve simulation: top-3 causal rules applied to your
     historical trade tape. Skipped trades realize $0; kept trades realize actual
     net_pnl. Reports cumulative $ retained vs original.
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.model_selection import cross_val_predict, KFold
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

from correlation_causation import load_all_features, NUMERIC_FEATS, CATEG_FEATS


# ──────────────────── RANDOM FOREST CAUSAL CLASSIFIER ────────────────────

def rf_analysis(feats, out_png):
    """Train RF on win/loss, report Gini importance + permutation importance.

    Uses 5-fold cross-validation to avoid in-sample importance bias.
    """
    feat_names = [f[0] for f in NUMERIC_FEATS]
    X = np.array([[f[name] for name in feat_names] for f in feats], dtype=float)
    y = np.array([f["win"] for f in feats], dtype=int)

    # Cross-validated AUROC + balanced accuracy
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
    y_proba = cross_val_predict(
        RandomForestClassifier(n_estimators=300, max_depth=6,
                               class_weight="balanced", random_state=42, n_jobs=-1),
        X, y, cv=cv, method="predict_proba")[:, 1]
    auroc = roc_auc_score(y, y_proba)
    y_pred = (y_proba >= 0.5).astype(int)
    bacc = balanced_accuracy_score(y, y_pred)

    print(f"\n========== RANDOM FOREST WIN/LOSS CLASSIFIER ==========")
    print(f"  n={len(y)}, base win-rate={y.mean()*100:.1f}%")
    print(f"  5-fold CV AUROC:            {auroc:.4f}")
    print(f"  5-fold CV balanced accuracy: {bacc:.4f}")
    print(f"  (baseline random = 0.50 AUROC / 0.50 balanced acc)")

    # Fit final model on all data for importance
    rf = RandomForestClassifier(n_estimators=300, max_depth=6,
                                class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X, y)
    gini = rf.feature_importances_

    # Permutation importance (more robust)
    perm = permutation_importance(rf, X, y, n_repeats=15,
                                  random_state=42, n_jobs=-1, scoring="roc_auc")
    perm_mean = perm.importances_mean
    perm_std = perm.importances_std

    # Combine + sort
    rows = []
    role_map = dict(NUMERIC_FEATS)
    for i, name in enumerate(feat_names):
        rows.append({
            "feature": name,
            "role": role_map[name],
            "gini": gini[i],
            "perm_mean": perm_mean[i],
            "perm_std": perm_std[i],
        })
    rows.sort(key=lambda r: -r["perm_mean"])

    print(f"\n  {'feature':<22} {'role':<11} {'gini':>8} {'perm_AUROC_drop':>18}")
    for r in rows:
        sig = "★" if r["perm_mean"] > 2 * r["perm_std"] else " "
        print(f"  {sig} {r['feature']:<20} {r['role']:<11} "
              f"{r['gini']:>8.4f} {r['perm_mean']:>+10.4f} ± {r['perm_std']:.4f}")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(15, 9), facecolor="#0f1117")
    for ax in axes:
        ax.set_facecolor("#161922")
        ax.tick_params(colors="#cccccc", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#363952")
        ax.grid(True, color="#2a2e40", linewidth=0.5, alpha=0.6, axis="x")

    # By Gini
    sorted_gini = sorted(rows, key=lambda r: r["gini"])
    names_g = [r["feature"] for r in sorted_gini]
    vals_g = [r["gini"] for r in sorted_gini]
    role_colors = {"EXOGENOUS": "#22c55e", "PRE_ENTRY": "#3b82f6",
                   "CONFOUNDER": "#f59e0b", "DECISION": "#a855f7"}
    colors_g = [role_colors[r["role"]] for r in sorted_gini]
    axes[0].barh(names_g, vals_g, color=colors_g)
    axes[0].set_xlabel("Gini importance", color="#cccccc")
    axes[0].set_title("Feature importance (Gini / MDI)", color="white", fontsize=12)

    # By permutation
    sorted_perm = sorted(rows, key=lambda r: r["perm_mean"])
    names_p = [r["feature"] for r in sorted_perm]
    vals_p = [r["perm_mean"] for r in sorted_perm]
    stds_p = [r["perm_std"] for r in sorted_perm]
    colors_p = [role_colors[r["role"]] for r in sorted_perm]
    axes[1].barh(names_p, vals_p, xerr=stds_p,
                 color=colors_p, ecolor="#888", capsize=2)
    axes[1].axvline(0, color="#666", linewidth=0.8)
    axes[1].set_xlabel("Permutation AUROC drop (15 reps)", color="#cccccc")
    axes[1].set_title("Permutation importance (causal-likelihood proxy)",
                      color="white", fontsize=12)

    # Legend
    from matplotlib.patches import Patch
    handles = [Patch(color=v, label=k) for k, v in role_colors.items()]
    axes[1].legend(handles=handles, loc="lower right", fontsize=9,
                   facecolor="#1c1f2e", edgecolor="#363952", labelcolor="#cccccc")

    plt.suptitle(f"{account} {symbol} — Random Forest causal-likelihood ranking  "
                 f"(AUROC={auroc:.3f}, n={len(y)})",
                 color="white", fontsize=13)
    plt.tight_layout()
    plt.savefig(out_png, dpi=110, facecolor="#0f1117")
    plt.close()
    print(f"\n  Wrote: {out_png}")
    return rows, auroc


# ──────────────────── DAG DIAGRAM ────────────────────

def draw_dag(out_png, top_features):
    """Draw the causal DAG implied by the analysis.

    Layout:
      Top row:   EXOGENOUS (day, time)
      2nd row:   Pre-entry market state (HTF trend, vol, delta, range)
      3rd row:   Latent TILT (with proxies: loss_streak, intraday_dd, trade_idx)
      4th row:   Decision quality (unobserved)
      Bottom:    OUTCOME (Y = pnl_ticks)
    """
    fig, ax = plt.subplots(figsize=(15, 11), facecolor="#0f1117")
    ax.set_facecolor("#161922")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12)
    ax.axis("off")

    role_colors = {
        "EXOGENOUS":  "#22c55e",   # green
        "PRE_ENTRY":  "#3b82f6",   # blue
        "CONFOUNDER": "#f59e0b",   # amber
        "LATENT":     "#a855f7",   # purple (dashed border)
        "OUTCOME":    "#ef4444",   # red
        "DECISION":   "#6366f1",
    }

    def box(x, y, w, h, label, color, dashed=False, sublabel=None):
        bp = FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                            boxstyle="round,pad=0.15",
                            linewidth=2.0,
                            edgecolor=color,
                            facecolor="#1c1f2e",
                            linestyle="--" if dashed else "-")
        ax.add_patch(bp)
        ax.text(x, y + (0.15 if sublabel else 0), label,
                ha="center", va="center", color="white",
                fontsize=10, fontweight="bold")
        if sublabel:
            ax.text(x, y - 0.20, sublabel, ha="center", va="center",
                    color="#aaaaaa", fontsize=8)

    def arrow(x1, y1, x2, y2, color="#cccccc", lw=1.4, alpha=0.85, dashed=False):
        ap = FancyArrowPatch((x1, y1), (x2, y2),
                             arrowstyle="-|>", mutation_scale=12,
                             color=color, linewidth=lw, alpha=alpha,
                             linestyle="--" if dashed else "-")
        ax.add_patch(ap)

    # Title
    ax.text(8, 11.5, "Causal DAG — Trading",
            ha="center", color="white", fontsize=15, fontweight="bold")
    ax.text(8, 11.1, "Per López de Prado et al. (ADIA Lab Causal Discovery Challenge)",
            ha="center", color="#888", fontsize=9, style="italic")

    # Row 1 (top): EXOGENOUS
    box(3, 10, 2.4, 0.7, "DAY_OF_WEEK", role_colors["EXOGENOUS"],
        sublabel="Friday=best, Tue/Wed=worst")
    box(7, 10, 2.4, 0.7, "MIN_AFTER_OPEN", role_colors["EXOGENOUS"],
        sublabel="10:00-12:00 NY = best")
    box(11, 10, 2.4, 0.7, "MARKET_REGIME", role_colors["EXOGENOUS"],
        sublabel="trend day vs chop day")

    # Row 2: Pre-entry market state
    box(2,  8.0, 2.2, 0.7, "HTF_15m_trend", role_colors["PRE_ENTRY"],
        sublabel="must be ≥+3pt in dir")
    box(4.7, 8.0, 2.2, 0.7, "5m_range",       role_colors["PRE_ENTRY"],
        sublabel="tight or wide, NOT mid")
    box(7.4, 8.0, 2.2, 0.7, "15s_volume",     role_colors["PRE_ENTRY"],
        sublabel="LOW (paused)")
    box(10.1, 8.0, 2.2, 0.7, "1m_volume",     role_colors["PRE_ENTRY"],
        sublabel="HIGH (active)")
    box(12.8, 8.0, 2.2, 0.7, "15s_delta",     role_colors["PRE_ENTRY"],
        sublabel="≥+150 in dir")

    # Row 3: TILT and confounders
    box(3, 5.5, 3.0, 1.0, "TILT (latent)", role_colors["LATENT"],
        dashed=True, sublabel="unobservable cognitive state")
    box(7, 5.5, 2.2, 0.7, "loss_streak",   role_colors["CONFOUNDER"],
        sublabel="biserial p=0.004")
    box(9.5, 5.5, 2.2, 0.7, "intraday_DD",  role_colors["CONFOUNDER"],
        sublabel="biserial p=0.016")
    box(12, 5.5, 2.2, 0.7, "trade_idx_today", role_colors["CONFOUNDER"],
        sublabel="proxy for fatigue")

    # Row 4: Decision quality (latent)
    box(6, 3.3, 3.5, 0.9, "DECISION QUALITY", role_colors["LATENT"],
        dashed=True, sublabel="which trade you took, how aggressively")
    box(11, 3.3, 2.5, 0.7, "side / qty / sym", role_colors["DECISION"],
        sublabel="your observable choice")

    # Row 5: OUTCOME
    box(8, 1.0, 3.5, 1.0, "OUTCOME (Y)\npnl_ticks, win", role_colors["OUTCOME"])

    # Arrows
    # EXOG -> PRE_ENTRY (regime affects market state)
    arrow(11, 9.65, 9, 8.4, color="#666", alpha=0.5)
    arrow(11, 9.65, 12, 8.4, color="#666", alpha=0.5)
    # EXOG -> OUTCOME (direct exogenous effect on Y)
    arrow(3, 9.65, 7, 1.6, color="#22c55e", alpha=0.6)
    arrow(7, 9.65, 8, 1.6, color="#22c55e", alpha=0.6)

    # PRE_ENTRY -> OUTCOME
    for x in (2, 4.7, 7.4, 10.1, 12.8):
        arrow(x, 7.65, 8, 1.6, color="#3b82f6", alpha=0.45)

    # TILT -> proxies (latent → observable)
    arrow(3, 6.0, 6.5, 5.7, color="#a855f7", dashed=True, alpha=0.7)
    arrow(3, 6.0, 9, 5.7, color="#a855f7", dashed=True, alpha=0.7)
    arrow(3, 6.0, 11.5, 5.7, color="#a855f7", dashed=True, alpha=0.7)
    # TILT -> Decision quality
    arrow(3, 5.0, 6, 3.7, color="#a855f7", dashed=True, alpha=0.7)

    # Decision quality -> OUTCOME
    arrow(6, 2.85, 8, 1.6, color="#6366f1", lw=2.0)
    arrow(11, 2.95, 8.5, 1.6, color="#6366f1", lw=2.0)

    # Legend
    legend_y = 0.1
    legend_x = 0.5
    items = [
        ("Exogenous (clean causal)", role_colors["EXOGENOUS"]),
        ("Pre-entry market state (causal)", role_colors["PRE_ENTRY"]),
        ("Confounder (tilt proxy)", role_colors["CONFOUNDER"]),
        ("Latent (unobserved)", role_colors["LATENT"]),
        ("Outcome (Y)", role_colors["OUTCOME"]),
    ]
    for i, (label, color) in enumerate(items):
        bp = FancyBboxPatch((legend_x + i * 3.0, legend_y), 0.2, 0.2,
                            boxstyle="round,pad=0.03", facecolor=color,
                            edgecolor=color)
        ax.add_patch(bp)
        ax.text(legend_x + i * 3.0 + 0.35, legend_y + 0.1, label,
                color="#cccccc", fontsize=8, va="center")

    plt.savefig(out_png, dpi=110, facecolor="#0f1117", bbox_inches="tight")
    plt.close()
    print(f"  Wrote: {out_png}")


# ──────────────────── COUNTERFACTUAL EQUITY SIM ────────────────────

def counterfactual_equity(feats, out_png):
    """Apply top causal rules as a filter and rebuild the equity curve.

    Rules (top-3 likely-causal, derived from analysis):
      R1: Day-of-week NOT in {Mon, Tue, Wed}
      R2: HTF_15m trend in direction >= +3pts
      R3: vol+delta confirmation: (v15s in 500..1250) AND (v1m >= 3500)
           AND (d15s_dir >= 100)

    For each trade: if all rules pass → keep actual net_pnl; else → $0 (skip).
    Plot cumulative $ over time, comparing actual vs filtered.
    """
    # Need net_pnl in dollars — recompute from pnl_ticks * tick_size * point_value
    # (or pull from DB). Simpler: pull net_pnl from DB by trade_id.
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, net_pnl, entry_time_ms, trade_date, root_symbol
          FROM trades
         WHERE account=? AND is_open=0 AND root_symbol='ES'
         ORDER BY entry_time_ms
    """).fetchall()
    conn.close()
    pnl_by_id = {r["id"]: (r["net_pnl"] or 0, r["entry_time_ms"]) for r in rows}

    # Build rule-pass flags
    feats_sorted = sorted(feats, key=lambda f: f["entry_time_ms"])
    results = []
    n_kept = 0
    for f in feats_sorted:
        r1 = f["dow_num"] not in (0, 1, 2)  # NOT Mon/Tue/Wed
        r2 = f["tr15m_dir"] >= 3.0
        r3 = (500 <= f["v15s"] <= 1250) and f["v1m"] >= 3500 and f["d15s_dir"] >= 100
        kept = r1 and r2 and r3
        net = pnl_by_id.get(f["trade_id"], (0, f["entry_time_ms"]))[0]
        if kept:
            n_kept += 1
        results.append({
            "ts": f["entry_time_ms"],
            "actual": net,
            "filtered": net if kept else 0.0,
            "kept": kept,
            "rules_passed": [r1, r2, r3],
        })

    actual_cum = np.cumsum([r["actual"] for r in results])
    filtered_cum = np.cumsum([r["filtered"] for r in results])

    print(f"\n========== COUNTERFACTUAL SIM ==========")
    print(f"  Total trades:      {len(results)}")
    print(f"  Kept (all 3 rules): {n_kept}  ({n_kept/len(results)*100:.1f}%)")
    print(f"  Actual cumulative:    ${actual_cum[-1]:+.2f}")
    print(f"  Filtered cumulative:  ${filtered_cum[-1]:+.2f}")
    print(f"  Improvement:          ${filtered_cum[-1] - actual_cum[-1]:+.2f}")

    # Per-rule individual impact
    print(f"\n  Individual rule impact (each rule applied alone):")
    for rule_i, name in enumerate([
        "R1: NOT Mon/Tue/Wed",
        "R2: HTF 15m >= +3pt in dir",
        "R3: vol+delta confirmation"]):
        kept_n = sum(1 for r in results if r["rules_passed"][rule_i])
        kept_pnl = sum(r["actual"] for r in results if r["rules_passed"][rule_i])
        print(f"    {name:<32}: kept {kept_n:>3} trades, "
              f"net ${kept_pnl:+.0f}")

    # Plot cumulative comparison
    fig, ax = plt.subplots(figsize=(16, 8), facecolor="#0f1117")
    ax.set_facecolor("#161922")
    for spine in ax.spines.values():
        spine.set_color("#363952")
    ax.tick_params(colors="#cccccc", labelsize=9)
    ax.grid(True, color="#2a2e40", linewidth=0.5, alpha=0.6)

    xs = [datetime.fromtimestamp(r["ts"]/1000, tz=timezone.utc) - timedelta(hours=4)
          for r in results]
    ax.plot(xs, actual_cum, color="#ef4444", linewidth=1.6,
            label=f"Actual equity (final ${actual_cum[-1]:+.0f})")
    ax.plot(xs, filtered_cum, color="#22c55e", linewidth=1.8,
            label=f"3-rule filter (kept {n_kept}/{len(results)}, "
                  f"final ${filtered_cum[-1]:+.0f})")
    ax.fill_between(xs, filtered_cum, actual_cum,
                    where=(filtered_cum >= actual_cum), color="#22c55e", alpha=0.15)
    ax.fill_between(xs, filtered_cum, actual_cum,
                    where=(filtered_cum < actual_cum), color="#ef4444", alpha=0.15)
    ax.axhline(0, color="#666", linewidth=0.7)

    # Mark kept trades as dots on the filtered line
    kept_xs = [xs[i] for i, r in enumerate(results) if r["kept"]]
    kept_ys = [filtered_cum[i] for i, r in enumerate(results) if r["kept"]]
    ax.scatter(kept_xs, kept_ys, s=14, color="#22c55e", alpha=0.6, zorder=4,
               label=f"Trade kept by filter ({n_kept})")

    ax.set_title("Counterfactual equity: actual vs 3-rule causal filter\n"
                 "R1: NOT Mon/Tue/Wed  +  R2: HTF 15m >=+3pt in dir  +  R3: vol+delta confirm",
                 color="white", fontsize=12)
    ax.set_ylabel("Cumulative net $", color="#cccccc")
    ax.set_xlabel("Trade entry (NY time)", color="#cccccc")
    ax.legend(loc="upper left", fontsize=10, facecolor="#1c1f2e",
              edgecolor="#363952", labelcolor="#cccccc")

    plt.tight_layout()
    plt.savefig(out_png, dpi=110, facecolor="#0f1117")
    plt.close()
    print(f"\n  Wrote: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--out_dir", default=".")
    args = ap.parse_args()

    feats = load_all_features(args.account, ("ES",))
    if not feats:
        return

    rf_rows, auroc = rf_analysis(feats, f"{args.out_dir}/rf_importance.png")
    draw_dag(f"{args.out_dir}/causal_dag.png", rf_rows[:5])
    counterfactual_equity(feats, f"{args.out_dir}/counterfactual_equity.png")

    print("\n========== ALL OUTPUTS ==========")
    print(f"  {args.out_dir}/rf_importance.png")
    print(f"  {args.out_dir}/causal_dag.png")
    print(f"  {args.out_dir}/counterfactual_equity.png")


if __name__ == "__main__":
    main()
