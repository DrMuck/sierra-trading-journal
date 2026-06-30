"""Trading Journal FastAPI backend."""
import os
import sys
from datetime import datetime, date
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

import database as db
from sc_parser import (
    discover_log_files, parse_trade_log, extract_fills,
    reconstruct_trades, get_available_dates, get_accounts,
)
from tick_data import get_ohlc_bars, get_tick_data_around_trade, _load_ticks
from config import POINT_VALUES

app = FastAPI(title="Trading Journal API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Import endpoints ──────────────────────────────────────────────

@app.post("/api/import/scan")
def scan_log_files():
    """Scan for available trade activity log files."""
    files = discover_log_files()
    conn = db.get_db()
    imported = {row["file_path"] for row in conn.execute("SELECT file_path FROM import_log").fetchall()}
    conn.close()

    return {
        "files": [
            {**f, "imported": f["path"] in imported}
            for f in files
        ],
        "total": len(files),
        "imported_count": sum(1 for f in files if f["path"] in imported),
    }


@app.post("/api/import/file")
def import_log_file(path: str, force: bool = False):
    """Import a single trade activity log file."""
    if not os.path.isfile(path):
        raise HTTPException(404, f"File not found: {path}")

    conn = db.get_db()

    # Check if already imported
    if not force:
        existing = conn.execute("SELECT id FROM import_log WHERE file_path = ?", (path,)).fetchone()
        if existing:
            return {"status": "skipped", "message": "Already imported. Use force=true to re-import."}

    # Parse — pass `path` so extract_fills can detect `.simulated.data` and
    # tag each fill (and downstream trade) as is_sim=True.
    records = parse_trade_log(path)
    fills = extract_fills(records, source_path=path)
    trades = reconstruct_trades(fills)

    # Delete existing data for this file if re-importing
    if force:
        conn.execute("DELETE FROM import_log WHERE file_path = ?", (path,))
        for trade in trades:
            conn.execute("DELETE FROM fills WHERE trade_id = ?", (trade.id,))
            conn.execute("DELETE FROM trades WHERE id = ?", (trade.id,))

    # Store trades and fills
    for trade in trades:
        db.upsert_trade(conn, trade)
        for fill in trade.fills:
            db.upsert_fill(conn, fill, trade.id)

    # Extract date and account from filename
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})_UTC\.(.+)\.data$", path)
    file_date = m.group(1) if m else ""
    file_account = m.group(2).replace(".simulated", "") if m else ""

    # Compute daily stats
    if file_date and file_account:
        db.compute_daily_stats(conn, file_date, file_account)

    # Log import
    conn.execute("""
        INSERT OR REPLACE INTO import_log (file_path, file_date, account,
            records_parsed, fills_extracted, trades_created)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (path, file_date, file_account, len(records), len(fills), len(trades)))

    conn.commit()
    conn.close()

    return {
        "status": "imported",
        "records": len(records),
        "fills": len(fills),
        "trades": len(trades),
    }


@app.post("/api/import/all")
def import_all_logs(force: bool = False):
    """Import all unimported trade activity log files."""
    files = discover_log_files()
    conn = db.get_db()
    imported = {row["file_path"] for row in conn.execute("SELECT file_path FROM import_log").fetchall()}
    conn.close()

    results = []
    for f in files:
        if not force and f["path"] in imported:
            continue
        result = import_log_file(f["path"], force=force)
        results.append({"file": f["path"], **result})

    # Also import Quantower
    qt_result = import_quantower(force=force)
    return {
        "imported": len(results), "results": results,
        "quantower": qt_result,
    }


@app.post("/api/import/quantower")
def import_quantower(force: bool = False):
    """Import all Quantower trade history from local SQLite DBs."""
    from quantower_parser import import_all_quantower
    res = import_all_quantower()
    if not res["trades"]:
        return {"status": "no_data", "dbs": res["dbs"]}

    conn = db.get_db()

    # Clear old quantower trades when force=True
    if force:
        conn.execute("DELETE FROM fills WHERE trade_id LIKE 'qt_%'")
        conn.execute("DELETE FROM trades WHERE id LIKE 'qt_%'")

    # Tag trade IDs with 'qt_' prefix to distinguish from SC trades
    new_count = 0
    for trade in res["trades"]:
        if not trade.id.startswith("qt_"):
            trade.id = f"qt_{trade.id}"
        # Skip if already imported (unless force)
        existing = conn.execute("SELECT 1 FROM trades WHERE id = ?", (trade.id,)).fetchone()
        if existing and not force:
            continue
        if existing:
            conn.execute("DELETE FROM fills WHERE trade_id = ?", (trade.id,))
        db.upsert_trade(conn, trade)
        for fill in trade.fills:
            db.upsert_fill(conn, fill, trade.id)
        new_count += 1

    # Update daily stats per account/date
    accounts_dates = set((t.account, t.entry_time.strftime("%Y-%m-%d")) for t in res["trades"])
    for acct, date in accounts_dates:
        db.compute_daily_stats(conn, date, acct)

    # Log the import
    for db_info in res["dbs"]:
        if "error" in db_info:
            continue
        conn.execute("""
            INSERT OR REPLACE INTO import_log
            (file_path, file_date, account, records_parsed, fills_extracted, trades_created)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (db_info["path"], datetime.now().strftime("%Y-%m-%d"),
              f"quantower:{db_info['connection']}",
              db_info["fills"], db_info["fills"], new_count))

    conn.commit()
    conn.close()

    return {
        "status": "imported",
        "dbs": res["dbs"],
        "total_fills": res["total_fills"],
        "trades_created": new_count,
    }


# ── Trade endpoints ───────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(
    date: Optional[str] = None,
    account: Optional[str] = None,
    account_value: Optional[str] = None,  # canonical "Acct:L" / "Acct:S" token
    is_sim: Optional[int] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Get trades with optional filters.

    `account_value` is the preferred way to filter by account: a token like
    `7502:L` (live trades on 7502) or `Sim1:S` (sim trades on Sim1).
    Legacy `account` + `is_sim` separately still work.
    """
    # Resolve account_value into (account, is_sim) overrides
    if account_value:
        if account_value.endswith(":S"):
            account = account_value[:-2]; is_sim = 1
        elif account_value.endswith(":L"):
            account = account_value[:-2]; is_sim = 0
        else:
            account = account_value
    conn = db.get_db()
    where = ["1=1"]
    params = []

    if date:
        where.append("trade_date = ?")
        params.append(date)
    if account:
        where.append("account = ?")
        params.append(account)
    if is_sim is not None:
        where.append("COALESCE(is_sim, 0) = ?")
        params.append(int(is_sim))
    if symbol:
        where.append("(root_symbol = ? OR symbol = ?)")
        params.extend([symbol, symbol])
    if side:
        where.append("side = ?")
        params.append(side.upper())

    where_sql = " AND ".join(where)

    total = conn.execute(f"SELECT COUNT(*) as cnt FROM trades WHERE {where_sql}", params).fetchone()["cnt"]

    rows = conn.execute(
        f"SELECT * FROM trades WHERE {where_sql} ORDER BY entry_time_ms DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()

    conn.close()
    return {
        "trades": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/trades/{trade_id}")
def get_trade(trade_id: str):
    """Get a single trade with its fills."""
    conn = db.get_db()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        raise HTTPException(404, "Trade not found")

    fills = conn.execute(
        "SELECT * FROM fills WHERE trade_id = ? ORDER BY timestamp_ms",
        (trade_id,)
    ).fetchall()

    conn.close()
    return {
        "trade": dict(trade),
        "fills": [dict(f) for f in fills],
    }


@app.put("/api/trades/{trade_id}/notes")
def update_trade_notes(trade_id: str, notes: str = "", tags: str = "[]", rating: int = None):
    """Update trade notes, tags, and rating."""
    conn = db.get_db()
    conn.execute(
        "UPDATE trades SET notes = ?, tags = ?, rating = ?, updated_at = datetime('now') WHERE id = ?",
        (notes, tags, rating, trade_id)
    )
    conn.commit()
    conn.close()
    return {"status": "updated"}


from pydantic import BaseModel


class TradeCardBody(BaseModel):
    setup_name: Optional[str] = None
    trade_idea: Optional[str] = None
    what_good: Optional[str] = None
    what_bad: Optional[str] = None
    notes: Optional[str] = None
    rating: Optional[int] = None
    tags: Optional[str] = None


@app.get("/api/trade-cards/setups")
def list_setup_library(account: Optional[str] = None):
    """Return the user's own setup-name library: distinct setup_name values
    from prior trade cards, with usage count + last-used date.

    Use this to populate the autocomplete dropdown with REAL setups the user
    has tagged before, instead of a hardcoded list.
    """
    conn = db.get_db()
    where = "WHERE setup_name IS NOT NULL AND setup_name != ''"
    params: list = []
    if account:
        where += " AND account = ?"
        params.append(account)
    rows = conn.execute(f"""
        SELECT setup_name AS name,
               COUNT(*) AS used,
               MAX(trade_date) AS last_used,
               ROUND(AVG(net_pnl), 2) AS avg_net,
               SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losses
          FROM trades
         {where}
         GROUP BY setup_name
         ORDER BY used DESC, last_used DESC
    """, params).fetchall()
    conn.close()
    return {"setups": [dict(r) for r in rows]}


@app.put("/api/trades/{trade_id}/card")
def update_trade_card(trade_id: str, body: TradeCardBody):
    """Update the structured trade-card fields (setup_name, idea, good/bad,
    plus existing notes/rating/tags). Only fields explicitly set in the body
    are updated — None means leave unchanged."""
    conn = db.get_db()
    fields = []
    values = []
    for key in ("setup_name", "trade_idea", "what_good", "what_bad",
                "notes", "tags", "rating"):
        val = getattr(body, key)
        if val is not None:
            fields.append(f"{key} = ?")
            values.append(val)
    if fields:
        values.append(trade_id)
        sql = f"UPDATE trades SET {', '.join(fields)}, updated_at=datetime('now') WHERE id = ?"
        conn.execute(sql, values)
        conn.commit()
    conn.close()
    return {"status": "updated"}


# ── Dashboard / Stats endpoints ──────────────────────────────────

@app.get("/api/stats/daily")
def get_daily_stats(
    account: Optional[str] = None,
    account_value: Optional[str] = None,
    is_sim: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """Get daily P&L stats."""
    # daily_stats is aggregated per (date, account) — it doesn't carry is_sim,
    # so the is_sim filter can only narrow on account. We resolve account_value
    # to extract its account portion; the is_sim portion is informational.
    a_from_val, _ = _split_account_value(account_value)
    if a_from_val:
        account = a_from_val
    conn = db.get_db()
    where = ["1=1"]
    params = []

    if account:
        where.append("account = ?")
        params.append(account)
    if from_date:
        where.append("date >= ?")
        params.append(from_date)
    if to_date:
        where.append("date <= ?")
        params.append(to_date)

    where_sql = " AND ".join(where)
    rows = conn.execute(
        f"SELECT * FROM daily_stats WHERE {where_sql} ORDER BY date DESC",
        params
    ).fetchall()
    conn.close()
    return {"stats": [dict(r) for r in rows]}


@app.get("/api/stats/summary")
def get_summary_stats(
    account: Optional[str] = None,
    account_value: Optional[str] = None,
    is_sim: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    gross: bool = False,
):
    """Get comprehensive summary statistics (Tradervue-style)."""
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)

    rows = conn.execute(f"""
        SELECT {pcol} as pnl, pnl_dollars, commissions, duration_seconds, trade_date, side
        FROM trades WHERE {where_sql}
        ORDER BY entry_time_ms
    """, params).fetchall()
    conn.close()

    if not rows:
        return {"total_trades": 0}

    import numpy as np
    pnls = [r["pnl"] for r in rows]
    gross_pnls = [r["pnl_dollars"] for r in rows]
    comms = [r["commissions"] for r in rows]
    durations = [r["duration_seconds"] for r in rows if r["duration_seconds"] is not None]
    n = len(pnls)
    pnl_arr = np.array(pnls)

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    scratches = [p for p in pnls if p == 0]

    win_durations = [r["duration_seconds"] for r in rows if r["pnl"] > 0 and r["duration_seconds"]]
    loss_durations = [r["duration_seconds"] for r in rows if r["pnl"] < 0 and r["duration_seconds"]]
    scratch_durations = [r["duration_seconds"] for r in rows if r["pnl"] == 0 and r["duration_seconds"]]

    # Consecutive wins/losses
    max_consec_wins = 0; max_consec_losses = 0
    cur_wins = 0; cur_losses = 0
    for p in pnls:
        if p > 0:
            cur_wins += 1; cur_losses = 0
            max_consec_wins = max(max_consec_wins, cur_wins)
        elif p < 0:
            cur_losses += 1; cur_wins = 0
            max_consec_losses = max(max_consec_losses, cur_losses)
        else:
            cur_wins = 0; cur_losses = 0

    # Profit factor
    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

    # Win rate
    win_rate = len(winners) / n if n > 0 else 0

    # Standard deviation
    std_dev = float(np.std(pnl_arr)) if n > 1 else 0

    # Kelly Criterion: K% = W - (1-W)/R where W=win_rate, R=avg_win/avg_loss
    avg_w = np.mean(winners) if winners else 0
    avg_l = abs(np.mean(losers)) if losers else 0
    if avg_l > 0 and win_rate > 0:
        r_ratio = avg_w / avg_l
        kelly = win_rate - (1 - win_rate) / r_ratio
    else:
        kelly = 0

    # SQN (System Quality Number) = sqrt(N) * mean(pnl) / std(pnl)
    if std_dev > 0 and n > 1:
        sqn = round(np.sqrt(min(n, 100)) * np.mean(pnl_arr) / std_dev, 2)
    else:
        sqn = None

    # K-Ratio (slope of equity curve / std error of slope)
    if n > 2:
        x = np.arange(n)
        cum = np.cumsum(pnl_arr)
        # Linear regression
        slope, intercept = np.polyfit(x, cum, 1)
        residuals = cum - (slope * x + intercept)
        std_err = np.sqrt(np.sum(residuals**2) / (n - 2)) / np.sqrt(np.sum((x - x.mean())**2))
        k_ratio = round(slope / std_err, 2) if std_err > 0 else 0
    else:
        k_ratio = 0

    # Average daily P&L
    from collections import defaultdict
    daily = defaultdict(float)
    daily_vol = defaultdict(int)
    for r in rows:
        daily[r["trade_date"]] += r["pnl"]
        daily_vol[r["trade_date"]] += 1
    daily_pnls = list(daily.values())
    trading_days = len(daily_pnls)
    avg_daily = np.mean(daily_pnls) if daily_pnls else 0
    avg_daily_volume = np.mean(list(daily_vol.values())) if daily_vol else 0

    # Probability of random chance (t-test: is mean P&L significantly != 0?)
    if n > 1 and std_dev > 0:
        t_stat = np.mean(pnl_arr) / (std_dev / np.sqrt(n))
        # Approximate p-value using normal distribution for large n
        from math import erfc, sqrt
        p_value = erfc(abs(t_stat) / sqrt(2))
        prob_random = round(p_value * 100, 1)
    else:
        prob_random = None

    # Cumulative P&L for chart
    cumulative = []
    running = 0
    for d in sorted(daily.keys()):
        running += daily[d]
        cumulative.append({"date": d, "pnl": round(running, 2)})

    result = {
        # Core
        "total_pnl": round(sum(pnls), 2),
        "gross_pnl": round(sum(gross_pnls), 2),
        "total_commissions": round(sum(comms), 2),
        "total_trades": n,
        "trading_days": trading_days,
        # Win/Loss
        "winners": len(winners),
        "losers": len(losers),
        "scratches": len(scratches),
        "win_rate": round(win_rate, 4),
        "loss_rate": round(len(losers) / n, 4) if n > 0 else 0,
        # Averages
        "avg_pnl": round(np.mean(pnls), 2),
        "avg_winner": round(np.mean(winners), 2) if winners else 0,
        "avg_loser": round(np.mean(losers), 2) if losers else 0,
        "avg_daily_pnl": round(avg_daily, 2),
        "avg_daily_volume": round(avg_daily_volume, 1),
        # Extremes
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        # Duration
        "avg_duration_s": round(np.mean(durations), 1) if durations else 0,
        "avg_win_duration_s": round(np.mean(win_durations), 1) if win_durations else 0,
        "avg_loss_duration_s": round(np.mean(loss_durations), 1) if loss_durations else 0,
        "avg_scratch_duration_s": round(np.mean(scratch_durations), 1) if scratch_durations else 0,
        # Streaks
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        # Advanced
        "profit_factor": profit_factor,
        "std_dev": round(std_dev, 2),
        "kelly_pct": round(kelly * 100, 1),
        "sqn": sqn,
        "k_ratio": k_ratio,
        "prob_random_pct": prob_random,
        # Chart
        "cumulative_pnl": cumulative,
    }
    return result


def _split_account_value(account_value: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Resolve the canonical "Acct:L" / "Acct:S" token into (account, is_sim).

    Bare strings (no :L/:S suffix) are returned as account-only.
    """
    if not account_value:
        return None, None
    if account_value.endswith(":S"):
        return account_value[:-2], 1
    if account_value.endswith(":L"):
        return account_value[:-2], 0
    return account_value, None


def _stat_filters(account: Optional[str], from_date: Optional[str],
                   to_date: Optional[str], symbol: Optional[str],
                   side: Optional[str], extra: str = "",
                   account_value: Optional[str] = None,
                   is_sim: Optional[int] = None) -> tuple[str, list]:
    """Build shared WHERE clause for stat endpoints.

    Both legacy `account` and new `account_value` ("Acct:L"/"Acct:S") accepted.
    `is_sim` may be passed separately too. Effective precedence:
        account_value > (account + is_sim)
    """
    a_from_val, is_sim_from_val = _split_account_value(account_value)
    if a_from_val:
        account = a_from_val
    if is_sim_from_val is not None:
        is_sim = is_sim_from_val
    where = ["is_open = 0"]
    params = []
    if extra:
        where.append(extra)
    if account:
        where.append("account = ?")
        params.append(account)
    if is_sim is not None:
        where.append("COALESCE(is_sim, 0) = ?")
        params.append(int(is_sim))
    if from_date:
        where.append("trade_date >= ?")
        params.append(from_date)
    if to_date:
        where.append("trade_date <= ?")
        params.append(to_date)
    if symbol:
        where.append("(root_symbol = ? OR symbol = ?)")
        params.extend([symbol, symbol])
    if side:
        where.append("side = ?")
        params.append(side.upper())
    return " AND ".join(where), params


def _pnl_col(gross: bool = False) -> str:
    """Return the P&L column name based on gross/net toggle."""
    return "pnl_dollars" if gross else "net_pnl"


@app.get("/api/stats/cumulative")
def get_cumulative_pnl(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                        to_date: Optional[str] = None, symbol: Optional[str] = None,
                        side: Optional[str] = None, gross: bool = False):
    """Cumulative P&L over time, respecting all filters."""
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)

    rows = conn.execute(f"""
        SELECT trade_date, {pcol} as pnl
        FROM trades WHERE {where_sql}
        ORDER BY entry_time_ms
    """, params).fetchall()
    conn.close()

    trade_cum = []
    running = 0
    for r in rows:
        running += r["pnl"]
        trade_cum.append({"date": r["trade_date"], "pnl": round(running, 2)})

    from collections import OrderedDict
    daily = OrderedDict()
    running = 0
    for r in rows:
        d = r["trade_date"]
        if d not in daily:
            daily[d] = {"date": d, "day_pnl": 0, "cum_pnl": 0, "trades": 0}
        daily[d]["day_pnl"] = round(daily[d]["day_pnl"] + r["pnl"], 2)
        daily[d]["trades"] += 1
        running += r["pnl"]
        daily[d]["cum_pnl"] = round(running, 2)

    return {
        "daily": list(daily.values()),
        "per_trade": trade_cum,
        "total": round(running, 2),
    }


@app.get("/api/stats/intraday")
def get_intraday_pnl(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                      to_date: Optional[str] = None, symbol: Optional[str] = None,
                      side: Optional[str] = None, gross: bool = False):
    """Per-trade intraday P&L curve averaged across all days."""
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)

    rows = conn.execute(f"""
        SELECT trade_date, exit_time, exit_time_ms, {pcol} as pnl, side,
               entry_time, entry_time_ms
        FROM trades WHERE {where_sql}
        ORDER BY entry_time_ms
    """, params).fetchall()
    conn.close()

    from collections import OrderedDict
    from datetime import datetime, timezone, timedelta

    # Group by date, build intraday cumulative per day
    days: dict[str, list] = {}
    for r in rows:
        d = r["trade_date"]
        if d not in days:
            days[d] = []
        days[d].append(dict(r))

    # Per-day intraday curves
    day_curves = []
    for date, day_trades in sorted(days.items()):
        cum = 0
        points = []
        for i, t in enumerate(day_trades):
            cum += t["pnl"]
            # Convert exit time to CT hour:minute
            if t["exit_time_ms"]:
                # UTC ms -> CT (subtract 5 hours for CDT)
                ct_ms = t["exit_time_ms"] - 5 * 3600 * 1000
                ct_dt = datetime.fromtimestamp(ct_ms / 1000, tz=timezone.utc)
                time_str = ct_dt.strftime("%H:%M")
                # Minutes since midnight CT for x-axis
                minutes = ct_dt.hour * 60 + ct_dt.minute
            else:
                ct_ms = t["entry_time_ms"] - 5 * 3600 * 1000
                ct_dt = datetime.fromtimestamp(ct_ms / 1000, tz=timezone.utc)
                time_str = ct_dt.strftime("%H:%M")
                minutes = ct_dt.hour * 60 + ct_dt.minute

            points.append({
                "trade_num": i + 1,
                "time": time_str,
                "minutes": minutes,
                "pnl": round(cum, 2),
                "trade_pnl": round(t["pnl"], 2),
            })
        day_curves.append({
            "date": date,
            "points": points,
            "final_pnl": round(cum, 2),
            "trades": len(day_trades),
        })

    # Average intraday curve (by trade number)
    max_trades = max((len(dc["points"]) for dc in day_curves), default=0)
    avg_curve = []
    for i in range(max_trades):
        pnls_at_i = [dc["points"][i]["pnl"] for dc in day_curves if i < len(dc["points"])]
        if pnls_at_i:
            avg_curve.append({
                "trade_num": i + 1,
                "avg_pnl": round(sum(pnls_at_i) / len(pnls_at_i), 2),
                "min_pnl": round(min(pnls_at_i), 2),
                "max_pnl": round(max(pnls_at_i), 2),
                "days": len(pnls_at_i),
            })

    return {
        "day_curves": day_curves,
        "avg_curve": avg_curve,
    }


@app.get("/api/stats/by-hour")
def get_stats_by_hour(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                      to_date: Optional[str] = None, symbol: Optional[str] = None,
                      side: Optional[str] = None, gross: bool = False):
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)
    ct_offset = '-5 hours'

    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%H', datetime(entry_time, '{ct_offset}')) AS INTEGER) as hour,
            COUNT(*) as trades,
            SUM(CASE WHEN {pcol} > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN {pcol} <= 0 THEN 1 ELSE 0 END) as losers,
            ROUND(SUM({pcol}), 2) as total_pnl,
            ROUND(AVG({pcol}), 2) as avg_pnl
        FROM trades WHERE {where_sql}
        GROUP BY hour ORDER BY hour
    """, params).fetchall()

    conn.close()
    return {"stats": [dict(r) for r in rows]}


@app.get("/api/stats/by-day")
def get_stats_by_day_of_week(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                              to_date: Optional[str] = None, symbol: Optional[str] = None,
                              side: Optional[str] = None, gross: bool = False):
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)

    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%w', trade_date) AS INTEGER) as dow,
            COUNT(*) as trades,
            SUM(CASE WHEN {pcol} > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN {pcol} <= 0 THEN 1 ELSE 0 END) as losers,
            ROUND(SUM({pcol}), 2) as total_pnl,
            ROUND(AVG({pcol}), 2) as avg_pnl,
            ROUND(CAST(SUM(CASE WHEN {pcol} > 0 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) as win_rate
        FROM trades WHERE {where_sql}
        GROUP BY dow ORDER BY dow
    """, params).fetchall()

    day_names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    conn.close()
    return {"stats": [
        {**dict(r), "day_name": day_names[r["dow"]]} for r in rows
    ]}


@app.get("/api/stats/by-duration")
def get_stats_by_duration(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                           to_date: Optional[str] = None, symbol: Optional[str] = None,
                           side: Optional[str] = None, gross: bool = False):
    conn = db.get_db()
    pcol = _pnl_col(gross)
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side,
                                       extra="duration_seconds IS NOT NULL",
                                       account_value=account_value, is_sim=is_sim)

    rows = conn.execute(f"""
        SELECT
            CASE
                WHEN duration_seconds < 10 THEN '0-10s'
                WHEN duration_seconds < 30 THEN '10-30s'
                WHEN duration_seconds < 60 THEN '30s-1m'
                WHEN duration_seconds < 120 THEN '1-2m'
                WHEN duration_seconds < 300 THEN '2-5m'
                WHEN duration_seconds < 600 THEN '5-10m'
                ELSE '10m+'
            END as bucket,
            CASE
                WHEN duration_seconds < 10 THEN 1
                WHEN duration_seconds < 30 THEN 2
                WHEN duration_seconds < 60 THEN 3
                WHEN duration_seconds < 120 THEN 4
                WHEN duration_seconds < 300 THEN 5
                WHEN duration_seconds < 600 THEN 6
                ELSE 7
            END as sort_order,
            COUNT(*) as trades,
            SUM(CASE WHEN {pcol} > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN {pcol} <= 0 THEN 1 ELSE 0 END) as losers,
            ROUND(SUM({pcol}), 2) as total_pnl,
            ROUND(AVG({pcol}), 2) as avg_pnl,
            ROUND(CAST(SUM(CASE WHEN {pcol} > 0 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) as win_rate
        FROM trades WHERE {where_sql}
        GROUP BY bucket ORDER BY sort_order
    """, params).fetchall()

    conn.close()
    return {"stats": [dict(r) for r in rows]}


@app.get("/api/stats/excursion")
def get_excursion_distribution(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                                to_date: Optional[str] = None, symbol: Optional[str] = None,
                                side: Optional[str] = None, gross: bool = False):
    """Compute MAE/MFE distribution per-contract in ticks, using tick data."""
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)

    trades = conn.execute(f"""
        SELECT id, root_symbol, trade_date, side, entry_price, entry_qty,
               entry_time_ms, exit_time_ms, pnl_points, net_pnl
        FROM trades WHERE {where_sql}
        ORDER BY entry_time_ms
    """, params).fetchall()
    conn.close()

    import numpy as np
    from config import TICK_SIZES

    tick_cache: dict[tuple, dict] = {}
    excursions = []  # per-contract excursion in ticks

    for t in trades:
        key = (t["root_symbol"], t["trade_date"])
        if key not in tick_cache:
            tick_cache[key] = _load_ticks(t["root_symbol"], t["trade_date"])
        ticks = tick_cache[key]
        if ticks is None or len(ticks["ts_ns"]) == 0:
            continue

        ts_ms = ticks["ts_ns"] // 1_000_000
        prices = ticks["price"]
        mask = (ts_ms >= t["entry_time_ms"]) & (ts_ms <= (t["exit_time_ms"] or t["entry_time_ms"]))
        window = prices[mask]
        if len(window) == 0:
            continue

        tick_size = TICK_SIZES.get(t["root_symbol"].replace("M", "") if t["root_symbol"].startswith("M") else t["root_symbol"], 0.25)
        if tick_size <= 0:
            tick_size = 0.25

        entry = t["entry_price"]
        if t["side"] == "LONG":
            excursion_pts = window - entry
        else:
            excursion_pts = entry - window

        mae_ticks = float(excursion_pts.min()) / tick_size
        mfe_ticks = float(excursion_pts.max()) / tick_size
        final_ticks = (t["pnl_points"] / t["entry_qty"]) / tick_size

        excursions.append({
            "mae_ticks": round(mae_ticks, 1),
            "mfe_ticks": round(mfe_ticks, 1),
            "final_ticks": round(final_ticks, 1),
        })

    if not excursions:
        return {"mae_histogram": [], "mfe_histogram": [], "count": 0}

    # Build histogram of MAE (in ticks)
    mae_values = [e["mae_ticks"] for e in excursions]
    mfe_values = [e["mfe_ticks"] for e in excursions]

    def _histogram(values: list, bucket_size: int = 2) -> list:
        if not values:
            return []
        lo = int(np.floor(min(values) / bucket_size)) * bucket_size
        hi = int(np.ceil(max(values) / bucket_size)) * bucket_size
        buckets = {}
        for v in values:
            b = int(np.floor(v / bucket_size)) * bucket_size
            buckets[b] = buckets.get(b, 0) + 1
        result = []
        for b in range(lo, hi + bucket_size, bucket_size):
            result.append({
                "bucket": b,
                "label": f"{b} to {b + bucket_size}",
                "count": buckets.get(b, 0),
            })
        return result

    return {
        "mae_histogram": _histogram(mae_values, bucket_size=2),
        "mfe_histogram": _histogram(mfe_values, bucket_size=2),
        "mae_stats": {
            "worst": round(min(mae_values), 2),
            "avg": round(float(np.mean(mae_values)), 2),
            "median": round(float(np.median(mae_values)), 2),
            "p95": round(float(np.percentile(mae_values, 5)), 2),
        },
        "mfe_stats": {
            "best": round(max(mfe_values), 2),
            "avg": round(float(np.mean(mfe_values)), 2),
            "median": round(float(np.median(mfe_values)), 2),
        },
        "count": len(excursions),
        "scatter": excursions,
    }


@app.get("/api/stats/by-atr")
def get_stats_by_atr(account: Optional[str] = None, account_value: Optional[str] = None, is_sim: Optional[int] = None, from_date: Optional[str] = None,
                      to_date: Optional[str] = None, symbol: Optional[str] = None,
                      side: Optional[str] = None, gross: bool = False):
    """Get P&L breakdown by 5-min ATR at trade entry time."""
    conn = db.get_db()
    where_sql, params = _stat_filters(account, from_date, to_date, symbol, side, account_value=account_value, is_sim=is_sim)
    pcol = _pnl_col(gross)

    trades = conn.execute(f"""
        SELECT id, root_symbol, trade_date, entry_time_ms, {pcol} as pnl
        FROM trades WHERE {where_sql}
        ORDER BY entry_time_ms
    """, params).fetchall()
    conn.close()

    import numpy as np

    # Cache tick data per (symbol, date)
    from tick_data import _extract_contract
    tick_cache: dict[tuple, dict] = {}
    results = []

    for t in trades:
        contract = _extract_contract(t["symbol"])
        key = (t["root_symbol"], t["trade_date"], contract)
        if key not in tick_cache:
            ticks = _load_ticks(t["root_symbol"], t["trade_date"], contract=contract)
            tick_cache[key] = ticks

        ticks = tick_cache[key]
        atr = None
        if ticks is not None and len(ticks["ts_ns"]) > 0:
            ts_ms = ticks["ts_ns"] // 1_000_000
            prices = ticks["price"]
            entry_ms = t["entry_time_ms"]

            # Build 5min OHLC bars for ~30 min before entry (6 bars)
            bar_interval_ms = 5 * 60 * 1000
            n_bars = 6
            window_start = entry_ms - n_bars * bar_interval_ms
            mask = (ts_ms >= window_start) & (ts_ms < entry_ms)
            w_ts = ts_ms[mask]
            w_prices = prices[mask]

            if len(w_prices) > 10:
                # Assign each tick to a bar
                bar_ids = (w_ts - window_start) // bar_interval_ms
                unique_bars = np.unique(bar_ids)

                # Build OHLC per bar
                bars_ohlc = []
                for bid in unique_bars:
                    bp = w_prices[bar_ids == bid]
                    if len(bp) > 0:
                        bars_ohlc.append({
                            "high": float(bp.max()),
                            "low": float(bp.min()),
                            "close": float(bp[-1]),
                        })

                # Compute True Range: max(H-L, |H-prevC|, |L-prevC|)
                if len(bars_ohlc) >= 3:
                    trs = []
                    for i in range(1, len(bars_ohlc)):
                        h = bars_ohlc[i]["high"]
                        l = bars_ohlc[i]["low"]
                        pc = bars_ohlc[i - 1]["close"]
                        tr = max(h - l, abs(h - pc), abs(l - pc))
                        trs.append(tr)
                    # Average of last 5 TRs (or however many we have)
                    atr = round(sum(trs[-5:]) / len(trs[-5:]), 2)

        results.append({
            "trade_id": t["id"],
            "atr": atr,
            "net_pnl": t["pnl"],
        })

    # Bucket ATR values
    atr_trades = [r for r in results if r["atr"] is not None]
    if not atr_trades:
        return {"stats": [], "scatter": results}

    atrs = [r["atr"] for r in atr_trades]
    min_atr, max_atr = min(atrs), max(atrs)
    step = max((max_atr - min_atr) / 6, 0.25)

    buckets: dict[float, tuple[str, list]] = {}
    for r in atr_trades:
        bucket_idx = int((r["atr"] - min_atr) / step) if step > 0 else 0
        lo = round(min_atr + bucket_idx * step, 1)
        hi = round(lo + step, 1)
        label = f"{lo}-{hi}"
        buckets.setdefault(lo, (label, []))
        buckets[lo][1].append(r)

    stats = []
    for lo_key in sorted(buckets.keys()):
        label, trades_in_bucket = buckets[lo_key]
        pnls = [t["net_pnl"] for t in trades_in_bucket]
        winners = sum(1 for p in pnls if p > 0)
        stats.append({
            "bucket": label,
            "trades": len(pnls),
            "winners": winners,
            "losers": len(pnls) - winners,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "win_rate": round(winners / len(pnls), 4),
            "avg_atr": round(sum(t["atr"] for t in trades_in_bucket) / len(trades_in_bucket), 2),
        })

    return {"stats": stats, "scatter": results}


# ── Chart data endpoints ─────────────────────────────────────────

@app.get("/api/chart/ohlc")
def get_chart_ohlc(
    symbol: str,
    date: str,
    interval: int = 60,
):
    """Get OHLC bars for a symbol and date."""
    bars = get_ohlc_bars(symbol, date, interval)
    return {"bars": bars, "count": len(bars)}


@app.get("/api/leaderboard")
def get_leaderboard(
    account: Optional[str] = None,
    account_value: Optional[str] = None,
    is_sim: Optional[int] = None,
    direction: str = "top",          # "top" | "bottom"
    n: int = 20,
    symbol: Optional[str] = None,
    interval: int = 15,              # bar interval for mini chart
    padding_seconds: int = 60,       # bars context before entry + after exit
):
    """Return the N best- or worst-net-P&L trades for the given account, each
    augmented with a tiny price-bar series (around the trade window) and a
    running unrealized-P&L curve so the frontend can render compact compare
    cards without per-card extra API calls.
    """
    import numpy as np
    from config import POINT_VALUES
    n = max(1, min(50, n))
    direction = direction.lower()
    if direction not in ("top", "bottom"):
        raise HTTPException(400, "direction must be 'top' or 'bottom'")
    order = "DESC" if direction == "top" else "ASC"

    # Resolve canonical "Acct:L"/"Acct:S" token if present.
    a_from_val, is_sim_from_val = _split_account_value(account_value)
    if a_from_val:
        account = a_from_val
    if is_sim_from_val is not None:
        is_sim = is_sim_from_val
    if not account:
        raise HTTPException(400, "account or account_value required")

    conn = db.get_db()
    where = ["account = ?", "is_open = 0", "net_pnl IS NOT NULL"]
    params: list = [account]
    if is_sim is not None:
        where.append("COALESCE(is_sim, 0) = ?")
        params.append(int(is_sim))
    if symbol:
        where.append("root_symbol = ?")
        params.append(symbol.upper())
    sql = (
        "SELECT * FROM trades WHERE " + " AND ".join(where)
        + f" ORDER BY net_pnl {order} LIMIT ?"
    )
    params.append(n)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()

    # Per-trade mini chart + P&L curve. Reuses the cached tick loader, so
    # multiple trades on the same date share one tick read.
    from tick_data import _extract_contract
    out = []
    for rank, t in enumerate(rows, start=1):
        root = t["root_symbol"]
        contract = _extract_contract(t["symbol"])
        entry_ms = t["entry_time_ms"]
        exit_ms = t["exit_time_ms"] or entry_ms
        win_ms = padding_seconds * 1000
        bars = get_ohlc_bars(
            root, t["trade_date"], interval,
            start_ms=entry_ms - win_ms,
            end_ms=exit_ms + win_ms,
            contract=contract,
        )

        # Unrealized P&L curve from ticks (sampled every ~5 ticks for compactness).
        ticks = _load_ticks(root, t["trade_date"], contract=contract)
        pnl_curve = []
        if ticks is not None and len(ticks["ts_ns"]) > 0:
            ts_ms = ticks["ts_ns"] // 1_000_000
            in_mask = (ts_ms >= entry_ms) & (ts_ms <= exit_ms)
            in_ts = ts_ms[in_mask]
            in_px = ticks["price"][in_mask].astype(float)
            if len(in_ts) > 0:
                point_value = POINT_VALUES.get(root, 50.0)
                qty = t["entry_qty"] or 1
                entry_px = t["entry_price"]
                sign = 1.0 if t["side"] == "LONG" else -1.0
                pnl_arr = (in_px - entry_px) * sign * qty * point_value
                # Sample to ~80 points max
                step = max(1, len(in_ts) // 80)
                for i in range(0, len(in_ts), step):
                    pnl_curve.append({
                        "time": int(in_ts[i]) // 1000,
                        "pnl": round(float(pnl_arr[i]), 2),
                    })
                # Always include the final exit point
                pnl_curve.append({
                    "time": int(in_ts[-1]) // 1000,
                    "pnl": round(float(pnl_arr[-1]), 2),
                })

        # MFE / MAE for the small card stats
        mfe = max((p["pnl"] for p in pnl_curve), default=0.0)
        mae = min((p["pnl"] for p in pnl_curve), default=0.0)

        out.append({
            "rank": rank,
            "trade": {
                "id": t["id"],
                "symbol": t["symbol"],
                "root_symbol": root,
                "side": t["side"],
                "entry_time_ms": entry_ms,
                "exit_time_ms": exit_ms,
                "entry_price": t["entry_price"],
                "exit_price": t["exit_price"],
                "entry_qty": t["entry_qty"],
                "trade_date": t["trade_date"],
                "duration_seconds": t["duration_seconds"],
                "pnl_dollars": t["pnl_dollars"],
                "net_pnl": t["net_pnl"],
                "pnl_points": t["pnl_points"],
                "setup_name": t.get("setup_name") or "",
                "rating": t.get("rating"),
            },
            "bars": bars,
            "pnl_curve": pnl_curve,
            "mfe": round(mfe, 2),
            "mae": round(mae, 2),
        })

    return {
        "account": account,
        "direction": direction,
        "n": len(out),
        "symbol": symbol,
        "trades": out,
    }


@app.get("/api/chart/daily/{trade_id}")
def get_daily_chart(trade_id: str, interval: int = 300, zone_days_back: int = 5):
    """Whole-day 5-minute chart for the trade's date, plus business zones for
    the date and N prior trading days. Markers for all trades taken that day on
    the same root symbol.

    interval: bar interval in seconds (default 300 = 5min)
    zone_days_back: how many days of historical RTH profiles to include
                    (e.g. 5 returns today + 5 prior days of POC/VAH/VAL)
    """
    from business_zones import get_or_compute_zones, get_zones_window
    conn = db.get_db()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        raise HTTPException(404, "Trade not found")
    trade = dict(trade)
    root = trade["root_symbol"]
    date_str = trade["trade_date"]
    from tick_data import _extract_contract
    contract = _extract_contract(trade["symbol"])

    bars = get_ohlc_bars(root, date_str, interval, contract=contract)

    # All trades for that root_symbol on that day (for markers).
    # During roll week, filter to ONLY trades on the same contract — markers
    # for ESM26 on the ESU26 chart would be plotted at the wrong price level.
    day_trades = conn.execute(
        """SELECT id, symbol, side, entry_price, exit_price, entry_time_ms,
                  exit_time_ms, net_pnl
             FROM trades
            WHERE root_symbol=? AND trade_date=? AND is_open=0
            ORDER BY entry_time_ms""",
        (root, date_str)).fetchall()
    markers = []
    for t in day_trades:
        t = dict(t)
        if _extract_contract(t["symbol"]) != contract:
            continue  # different contract → wrong price scale, skip
        markers.append({
            "trade_id": t["id"],
            "side": t["side"],
            "entry_time": t["entry_time_ms"] // 1000,
            "exit_time": (t["exit_time_ms"] or t["entry_time_ms"]) // 1000,
            "entry_price": t["entry_price"],
            "exit_price": t["exit_price"],
            "net_pnl": t["net_pnl"],
            "is_current": t["id"] == trade_id,
        })

    # Zones — today's session + N prior days, contract-specific so we don't
    # mix ESM26 with ESU26 zones during roll week.
    today_zones = get_or_compute_zones(conn, root, date_str, _load_ticks, contract=contract)
    prior_zones = get_zones_window(conn, root, date_str, zone_days_back, _load_ticks, contract=contract)
    conn.close()

    def zone_dict(z, is_today):
        if z is None: return None
        return {
            "date": z.date, "is_today": is_today,
            "poc": z.poc, "vah": z.vah, "val": z.val,
            "rth_high": z.rth_high, "rth_low": z.rth_low,
            "total_volume": z.total_volume,
            "singles": z.singles,
        }

    return {
        "trade_id": trade_id,
        "symbol": root, "date": date_str,
        "bars": bars,
        "markers": markers,
        "today_zones": zone_dict(today_zones, True),
        "prior_zones": [zone_dict(z, False) for z in prior_zones],
    }


@app.get("/api/zones/{symbol}/{date}")
def get_business_zones(symbol: str, date: str, days_back: int = 0):
    """Return business zones for the given (symbol, date).

    If days_back > 0, also include zones for prior trading days.
    """
    from business_zones import get_or_compute_zones, get_zones_window
    conn = db.get_db()
    today = get_or_compute_zones(conn, symbol, date, _load_ticks)
    prior = get_zones_window(conn, symbol, date, days_back, _load_ticks) if days_back > 0 else []
    conn.close()
    def to_dict(z): return None if z is None else {
        "date": z.date, "poc": z.poc, "vah": z.vah, "val": z.val,
        "rth_high": z.rth_high, "rth_low": z.rth_low,
        "total_volume": z.total_volume, "singles": z.singles,
    }
    return {"today": to_dict(today), "prior": [to_dict(z) for z in prior]}


@app.get("/api/chart/trade/{trade_id}")
def get_trade_chart_data(trade_id: str, interval: int = 60, lookahead: int = 60,
                         window_minutes: int = 0):
    """Get chart data with trade overlay and unrealized P&L curve.

    Args:
        lookahead:      seconds after exit to project P&L (default 60s)
        window_minutes: if > 0, only return bars within ±window_minutes of the
                        trade (entry-window .. exit+window+lookahead). Default
                        is auto-sized from the interval: 30 min for 15-30s
                        bars, 240 min for 60-120s bars, 360 min for >=300s.
                        Saves 100x payload size on small intervals.
    """
    conn = db.get_db()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        raise HTTPException(404, "Trade not found")

    fills = conn.execute(
        "SELECT * FROM fills WHERE trade_id = ? ORDER BY timestamp_ms",
        (trade_id,)
    ).fetchall()
    conn.close()

    trade = dict(trade)
    root = trade["root_symbol"]
    # Extract the exact contract the user traded (e.g. ESU26 from "ESU26.XCME")
    # so we load tick data from the right contract during roll weeks.
    from tick_data import _extract_contract
    contract = _extract_contract(trade["symbol"])

    # Auto-size the bar window if the caller didn't specify one.
    if window_minutes <= 0:
        if interval <= 30:
            window_minutes = 30
        elif interval <= 120:
            window_minutes = 240
        else:
            window_minutes = 360

    # Push the window down into get_ohlc_bars so it only aggregates the ticks
    # we actually need (100x speedup vs aggregating the whole day then slicing).
    entry_ms = trade["entry_time_ms"]
    exit_ms = trade["exit_time_ms"] or trade["entry_time_ms"]
    win_ms = window_minutes * 60 * 1000
    lo_ms = entry_ms - win_ms
    hi_ms = exit_ms + win_ms + lookahead * 1000
    bars = get_ohlc_bars(root, trade["trade_date"], interval,
                         start_ms=lo_ms, end_ms=hi_ms, contract=contract)

    # Trade markers (only fills for THIS trade)
    markers = []
    for f in fills:
        f = dict(f)
        markers.append({
            "time": f["timestamp_ms"] // 1000,
            "price": f["price"],
            "side": f["side"],
            "type": f["order_type"],
            "quantity": f["quantity"],
        })

    # ── Unrealized P&L curve using tick data ──────────────────────
    pnl_curve = []
    entry_ms = trade["entry_time_ms"]
    exit_ms = trade["exit_time_ms"] or entry_ms
    entry_price = trade["entry_price"]
    entry_qty = trade["entry_qty"]
    side = trade["side"]
    point_value = POINT_VALUES.get(root, 5.0)

    import numpy as np
    ticks = _load_ticks(root, trade["trade_date"], contract=contract)
    if ticks is not None and len(ticks["ts_ns"]) > 0:
        ts_ms = ticks["ts_ns"] // 1_000_000
        prices = ticks["price"]

        # Window: entry to exit + lookahead
        pre_pad_ms = 30_000  # 30s before entry
        lookahead_ms = lookahead * 1000
        mask = (ts_ms >= entry_ms - pre_pad_ms) & (ts_ms <= exit_ms + lookahead_ms)
        window_ts = ts_ms[mask]
        window_prices = prices[mask]

        # Subsample for performance (max ~2000 points)
        n = len(window_ts)
        step = max(1, n // 2000)

        for i in range(0, n, step):
            t = int(window_ts[i])
            p = float(window_prices[i])
            if t < entry_ms:
                unrealized = 0.0
            else:
                if side == "LONG":
                    unrealized = (p - entry_price) * entry_qty * point_value
                else:
                    unrealized = (entry_price - p) * entry_qty * point_value
            pnl_curve.append({
                "time": t // 1000,
                "time_ms": t,
                "price": p,
                "pnl": round(unrealized, 2),
                "projected": t > exit_ms,
            })

    return {
        "bars": bars,
        "trade": trade,
        "markers": markers,
        "pnl_curve": pnl_curve,
    }


# ── Account/meta endpoints ───────────────────────────────────────

@app.get("/api/accounts")
def list_accounts():
    """Get list of accounts from imported data.

    Returns one entry per (account, is_sim) combination so the same account
    name can appear twice if it has both live and sim trades:

      {
        "accounts": ["7502", "Sim1", ...],            # legacy flat list
        "imported": [...],                            # legacy flat list
        "entries": [
          { "account": "7502",  "is_sim": 0, "n": 742,  "value": "7502:L",  "label": "7502" },
          { "account": "Sim1",  "is_sim": 1, "n": 410,  "value": "Sim1:S",  "label": "Sim1 · Sim" },
          ...
        ]
      }

    The `value` is the canonical filter token for the trades endpoint
    (e.g. `?account_value=Sim1:S` resolves to account=Sim1 AND is_sim=1).
    """
    conn = db.get_db()
    rows = conn.execute(
        "SELECT account, COALESCE(is_sim, 0) AS is_sim, COUNT(*) AS n "
        "FROM trades GROUP BY account, is_sim "
        "ORDER BY is_sim ASC, account ASC"
    ).fetchall()
    discovered = get_accounts()
    conn.close()
    entries = []
    seen_accts = set()
    for r in rows:
        acct = r["account"]
        is_sim = int(r["is_sim"] or 0)
        suffix = "S" if is_sim else "L"
        label = f"{acct} · Sim" if is_sim else acct
        entries.append({
            "account": acct,
            "is_sim": is_sim,
            "n": r["n"],
            "value": f"{acct}:{suffix}",
            "label": label,
        })
        seen_accts.add(acct)
    imported = [r["account"] for r in rows]
    all_accounts = sorted(set(imported + discovered))
    return {
        "accounts": all_accounts,
        "imported": imported,
        "entries": entries,
    }


@app.get("/api/symbols")
def list_symbols():
    """Get unique symbols traded."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT DISTINCT root_symbol FROM trades ORDER BY root_symbol"
    ).fetchall()
    conn.close()
    return {"symbols": [r["root_symbol"] for r in rows]}


@app.get("/api/dates")
def list_dates():
    """Get available dates."""
    conn = db.get_db()
    imported = [r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM trades ORDER BY trade_date DESC"
    ).fetchall()]
    conn.close()
    discovered = get_available_dates()
    return {"imported_dates": imported, "available_dates": discovered}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
