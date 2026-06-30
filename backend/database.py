"""SQLite database for Trading Journal."""
import sqlite3
import json
import os
from datetime import datetime
from config import DB_PATH


def get_db() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            root_symbol TEXT NOT NULL,
            account TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_time_ms INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            entry_qty REAL NOT NULL,
            exit_time TEXT,
            exit_time_ms INTEGER,
            exit_price REAL,
            exit_qty REAL,
            pnl_points REAL,
            pnl_dollars REAL,
            commissions REAL DEFAULT 0,
            net_pnl REAL,
            duration_seconds REAL,
            entry_order_type TEXT,
            exit_order_type TEXT,
            is_open INTEGER DEFAULT 1,
            trade_date TEXT NOT NULL,
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            rating INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT REFERENCES trades(id),
            timestamp TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            order_id TEXT,
            fill_id TEXT,
            account TEXT NOT NULL,
            order_type TEXT,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT NOT NULL,
            account TEXT NOT NULL,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            total_pnl_dollars REAL DEFAULT 0,
            total_pnl_points REAL DEFAULT 0,
            max_win REAL DEFAULT 0,
            max_loss REAL DEFAULT 0,
            avg_winner REAL DEFAULT 0,
            avg_loser REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            PRIMARY KEY (date, account)
        );

        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_date TEXT NOT NULL,
            account TEXT NOT NULL,
            records_parsed INTEGER DEFAULT 0,
            fills_extracted INTEGER DEFAULT 0,
            trades_created INTEGER DEFAULT 0,
            imported_at TEXT DEFAULT (datetime('now')),
            UNIQUE(file_path)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
        CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(root_symbol);
        CREATE INDEX IF NOT EXISTS idx_fills_trade ON fills(trade_id);

        -- Per-symbol/date computed RTH volume profile zones (cached).
        -- One row per (symbol, date) holds VAH/VAL/POC and the singles list.
        CREATE TABLE IF NOT EXISTS business_zones (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            poc REAL NOT NULL,
            vah REAL NOT NULL,
            val REAL NOT NULL,
            rth_high REAL NOT NULL,
            rth_low REAL NOT NULL,
            total_volume INTEGER NOT NULL,
            value_area_pct REAL DEFAULT 0.70,
            tick_size REAL NOT NULL,
            singles_json TEXT DEFAULT '[]',  -- list of single-print price ranges
            computed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (symbol, date)
        );
    """)

    # Lightweight migration: add the trade-card columns if they don't exist.
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for col_def in [
        "setup_name TEXT DEFAULT ''",
        "trade_idea TEXT DEFAULT ''",
        "what_good TEXT DEFAULT ''",
        "what_bad TEXT DEFAULT ''",
        # is_sim: 1 if this came from a Sim trading account (SC '.simulated.data'
        # file or Quantower demo connection). Keeps Sim trades segregated from
        # real-money trades in the same account/symbol filters.
        "is_sim INTEGER DEFAULT 0",
    ]:
        col = col_def.split()[0]
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col_def}")

    conn.commit()
    conn.close()


def upsert_trade(conn: sqlite3.Connection, trade) -> None:
    """Insert or update a trade."""
    trade_date = trade.entry_time.strftime("%Y-%m-%d")
    conn.execute("""
        INSERT OR REPLACE INTO trades
        (id, symbol, root_symbol, account, side, entry_time, entry_time_ms,
         entry_price, entry_qty, exit_time, exit_time_ms, exit_price, exit_qty,
         pnl_points, pnl_dollars, commissions, net_pnl, duration_seconds,
         entry_order_type, exit_order_type, is_open, trade_date, is_sim)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.id, trade.symbol, trade.root_symbol, trade.account, trade.side,
        trade.entry_time.isoformat(), trade.entry_time_ms,
        trade.entry_price, trade.entry_qty,
        trade.exit_time.isoformat() if trade.exit_time else None,
        trade.exit_time_ms, trade.exit_price, trade.exit_qty,
        trade.pnl_points, trade.pnl_dollars, trade.commissions, trade.net_pnl,
        trade.duration_seconds,
        trade.entry_order_type, trade.exit_order_type,
        1 if trade.is_open else 0, trade_date,
        1 if getattr(trade, "is_sim", False) else 0,
    ))


def upsert_fill(conn: sqlite3.Connection, fill, trade_id: str) -> None:
    """Insert a fill linked to a trade."""
    conn.execute("""
        INSERT INTO fills (trade_id, timestamp, timestamp_ms, symbol, side,
                          price, quantity, order_id, fill_id, account,
                          order_type, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_id, fill.timestamp.isoformat(), fill.timestamp_ms,
        fill.symbol, fill.side, fill.price, fill.quantity,
        fill.order_id, fill.fill_id, fill.account,
        fill.order_type, fill.description,
    ))


def compute_daily_stats(conn: sqlite3.Connection, date: str, account: str) -> None:
    """Compute and store daily statistics."""
    rows = conn.execute("""
        SELECT pnl_dollars, pnl_points, net_pnl, commissions FROM trades
        WHERE trade_date = ? AND account = ? AND is_open = 0
    """, (date, account)).fetchall()

    if not rows:
        return

    total = len(rows)
    # Use net_pnl (after commissions) for all stats
    winners = [r["net_pnl"] for r in rows if r["net_pnl"] and r["net_pnl"] > 0]
    losers = [r["net_pnl"] for r in rows if r["net_pnl"] and r["net_pnl"] < 0]

    total_pnl = sum(r["net_pnl"] or 0 for r in rows)
    total_pts = sum(r["pnl_points"] or 0 for r in rows)
    win_rate = len(winners) / total if total > 0 else 0
    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    conn.execute("""
        INSERT OR REPLACE INTO daily_stats
        (date, account, total_trades, winning_trades, losing_trades,
         total_pnl_dollars, total_pnl_points, max_win, max_loss,
         avg_winner, avg_loser, win_rate, profit_factor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date, account, total, len(winners), len(losers),
        round(total_pnl, 2), round(total_pts, 4),
        max(winners) if winners else 0,
        min(losers) if losers else 0,
        sum(winners) / len(winners) if winners else 0,
        sum(losers) / len(losers) if losers else 0,
        round(win_rate, 4),
        round(profit_factor, 4) if profit_factor != float("inf") else 9999.0,
    ))


init_db()
