"""
Parse Quantower trade history from local SQLite databases.

Quantower stores fills in `user-trades.db` per connection at:
  C:/Quantower/UserTradesCache/<connection>/user-trades.db
  C:/QuantowerBy/UserTradesCache/<connection>/user-trades.db

Each row in USER_TRADES is a fill (entry or exit), grouped by position_id.
We convert to the same `Fill` dataclass used for SC, then reuse
`reconstruct_trades()` for FIFO matching.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from sc_parser import Fill, reconstruct_trades, Trade

# .NET DateTime ticks → Unix epoch offset (ticks from 0001-01-01 to 1970-01-01)
DOTNET_EPOCH_TICKS = 621_355_968_000_000_000  # 100ns ticks

try:
    from config import QUANTOWER_ROOTS as _CFG_QUANTOWER_ROOTS
    QUANTOWER_ROOTS = _CFG_QUANTOWER_ROOTS or []
except ImportError:
    QUANTOWER_ROOTS = ["C:/Quantower"]


def _dotnet_ticks_to_unix_ms(ticks: int) -> int:
    """Convert .NET DateTime ticks (100ns since 0001-01-01) to Unix milliseconds."""
    return (ticks - DOTNET_EPOCH_TICKS) // 10_000


def _ticks_to_datetime(ticks: int) -> datetime:
    return datetime.fromtimestamp(_dotnet_ticks_to_unix_ms(ticks) / 1000, tz=timezone.utc)


def _parse_symbol(symbol_id: str) -> str:
    """Convert Quantower symbol_id to root symbol.

    Examples:
      'ESH6@CME'        -> 'ES'
      '/NQM26:XCME'     -> 'NQ'
      '/MESM26:XCME'    -> 'MES'
      'ESM6.CME'        -> 'ES'
    """
    if not symbol_id:
        return ""
    # Strip leading slash
    s = symbol_id.lstrip("/")
    # Split on @ or :
    s = re.split(r"[@:.]", s)[0]
    # Remove month+year suffix: e.g., ESH6 -> ES, MESM26 -> MES, NQM26 -> NQ
    m = re.match(r"^([A-Z]+?)([HMUZFGJKNQVX])(\d{1,2})$", s)
    if m:
        return m.group(1)
    return s


def discover_quantower_dbs() -> list[dict]:
    """Find all Quantower trade DBs across both install paths."""
    dbs = []
    for root in QUANTOWER_ROOTS:
        cache_dir = os.path.join(root, "UserTradesCache")
        if not os.path.isdir(cache_dir):
            continue
        for conn_name in os.listdir(cache_dir):
            db_path = os.path.join(cache_dir, conn_name, "user-trades.db")
            if os.path.isfile(db_path):
                size = os.path.getsize(db_path)
                dbs.append({
                    "path": db_path,
                    "connection": conn_name,
                    "instance": os.path.basename(root),
                    "size": size,
                })
    return dbs


def read_quantower_fills(db_path: str) -> list[Fill]:
    """Read all fills from a Quantower user-trades.db."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT trade_id, symbol_id, account_id, time, price, quantity, side,
               position_impact_type, fee_value, order_id, order_type_id, position_id
        FROM USER_TRADES
        ORDER BY time
    """).fetchall()
    conn.close()

    fills = []
    for r in rows:
        if r["price"] is None or r["quantity"] is None or not r["time"]:
            continue
        ts_ms = _dotnet_ticks_to_unix_ms(r["time"])
        if ts_ms <= 0:
            continue

        # Quantower side: 0=Buy, 1=Sell
        side = "BUY" if r["side"] == 0 else "SELL"

        # Use account_id as-is — that's the broker account
        account = r["account_id"] or "unknown"

        # Symbol normalisation: keep raw for trade_id, root for routing
        symbol_raw = r["symbol_id"]
        # Convert "ESH6@CME" or "/NQM26:XCME" to a clean symbol like "ESH6.CME"
        # for downstream FIFO matching grouping (which groups by exact symbol)
        clean_sym = symbol_raw.lstrip("/").replace("@", ".").replace(":", ".")

        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        fills.append(Fill(
            timestamp=ts,
            timestamp_ms=ts_ms,
            symbol=clean_sym,
            side=side,
            price=float(r["price"]),
            quantity=float(r["quantity"]),
            order_id=r["order_id"] or "",
            fill_id=r["trade_id"] or "",
            account=account,
            order_type=r["order_type_id"] or "",
            description=f"Quantower fill (impact={r['position_impact_type']})",
        ))
    return fills


def import_all_quantower() -> dict:
    """Discover and parse all Quantower DBs.

    Returns dict with: dbs, total_fills, trades_by_account
    """
    dbs = discover_quantower_dbs()
    all_fills: list[Fill] = []
    summary = []

    for db in dbs:
        try:
            fills = read_quantower_fills(db["path"])
        except Exception as e:
            summary.append({**db, "error": str(e), "fills": 0})
            continue
        all_fills.extend(fills)
        summary.append({**db, "fills": len(fills)})

    trades = reconstruct_trades(all_fills)
    return {
        "dbs": summary,
        "total_fills": len(all_fills),
        "total_trades": len(trades),
        "trades": trades,
    }


if __name__ == "__main__":
    res = import_all_quantower()
    print(f"=== Quantower Discovery ===")
    for db in res["dbs"]:
        err = f" ERROR: {db.get('error')}" if "error" in db else ""
        print(f"  {db['connection']}: {db.get('fills', 0)} fills{err}")
    print(f"\nTotal fills: {res['total_fills']}")
    print(f"Total trades reconstructed: {res['total_trades']}")

    if res["trades"]:
        print(f"\nFirst 5 trades:")
        for t in res["trades"][:5]:
            print(f"  {t.entry_time} {t.side} {t.entry_qty}x {t.symbol} "
                  f"@ {t.entry_price:.2f} -> {t.exit_price} | net P&L tbd")

        accounts = {}
        for t in res["trades"]:
            accounts.setdefault(t.account, []).append(t)
        print(f"\nBy account:")
        for acct, trs in accounts.items():
            closed = [t for t in trs if not t.is_open]
            print(f"  {acct}: {len(closed)} closed trades, gross=${sum(t.pnl_dollars for t in closed):.2f}")
