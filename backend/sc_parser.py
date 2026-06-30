"""
Sierra Chart Trade Activity Log parser.

Binary format: TLV (Tag-Length-Value) with u32 LE tag, u32 LE length, N bytes data.
Record delimiter: tag 199 with length 0.
"""
import struct
import os
import re
from datetime import datetime, timezone, date
from dataclasses import dataclass, field
from typing import Optional
from config import TRADE_LOG_DIRS, SC_EPOCH_OFFSET_US, PRICE_DIVISORS


# Tag IDs from DTC protocol / SC internal format
TAG_MSG_TYPE = 1
TAG_INTERNAL_ORDER_ID = 100
TAG_STATUS = 101
TAG_DATETIME = 102
TAG_SYMBOL = 103
TAG_DESCRIPTION = 104
TAG_ORDER_ID = 105       # Internal sequential order ID
TAG_EXT_ORDER_ID = 106   # Exchange order ID
TAG_ORDER_TYPE = 107     # "Limit", "Stop", "Market"
TAG_QUANTITY = 108       # Order quantity (f64)
TAG_BUY_SELL = 109       # 1=Buy, 2=Sell
TAG_PRICE = 110          # Order price (f64, raw SC encoding)
TAG_TIME_IN_FORCE = 112  # 1=user entry, 2=sent to exchange, 5=accepted, 8=filled
TAG_FILL_PRICE = 113     # Fill price (f64, raw SC encoding)
TAG_FILL_QTY = 114       # Fill quantity (f64)
TAG_AVG_FILL_PRICE = 115
TAG_FILLED_QTY = 116
TAG_ACCOUNT = 118
TAG_PARENT_ORDER_ID = 119
TAG_SIDE_CODE = 120      # 1=Buy side, 2=Sell side
TAG_FILL_ID = 124        # Exchange fill ID string
TAG_POSITION_QTY = 125   # Current position quantity (f64)
TAG_CHART_NUM = 126
TAG_CHARTBOOK = 127
TAG_LINKED = 128
TAG_UNKNOWN_133 = 133
TAG_EXT_ID_STR = 137     # Exchange order ID as string
TAG_PRICE_STR = 139      # Price as human-readable string
TAG_UNKNOWN_156 = 156
TAG_DATETIME2 = 160
TAG_DELIMITER = 199      # Record boundary (length always 0)

# Integer-valued tags (read as i64 or u32)
I64_TAGS = {TAG_MSG_TYPE, TAG_INTERNAL_ORDER_ID, TAG_ORDER_ID, TAG_DATETIME,
            TAG_DATETIME2, TAG_PARENT_ORDER_ID}
U32_TAGS = {TAG_STATUS, TAG_CHART_NUM, TAG_LINKED}

# Float64 tags
F64_TAGS = {TAG_PRICE, TAG_FILL_PRICE, TAG_FILL_QTY, TAG_AVG_FILL_PRICE,
            TAG_FILLED_QTY, TAG_QUANTITY, TAG_POSITION_QTY}

# String tags
STR_TAGS = {TAG_SYMBOL, TAG_DESCRIPTION, TAG_EXT_ORDER_ID, TAG_ORDER_TYPE,
            TAG_ACCOUNT, TAG_CHARTBOOK, TAG_FILL_ID, TAG_EXT_ID_STR, TAG_PRICE_STR}


def sc_datetime_to_utc(sc_dt: int) -> datetime:
    """Convert SC datetime (microseconds since 1899-12-30) to UTC datetime."""
    unix_us = sc_dt - SC_EPOCH_OFFSET_US
    return datetime.fromtimestamp(unix_us / 1_000_000, tz=timezone.utc)


def sc_datetime_to_unix_ms(sc_dt: int) -> int:
    """Convert SC datetime to Unix milliseconds."""
    unix_us = sc_dt - SC_EPOCH_OFFSET_US
    return unix_us // 1000


def _symbol_to_root(symbol: str) -> str:
    """Extract root symbol from any SC / QT contract identifier.

    Handles all observed formats:
       ESM6.CME              → ES   (legacy single-digit-year)
       ESM26.CME             → ES   (two-digit-year)
       ESM26-CME             → ES   (Sierra dash variant)
       ESM26_FUT_CME         → ES   (Sierra Fut suffix)
       MESH26_FUT_CME        → MES
       /NQM26:XCME           → NQ   (Quantower)
       GCG26-COMEX           → GC
    """
    if not symbol:
        return ""
    s = symbol.lstrip("/")
    # First dot-, dash-, underscore-, or colon-separated segment is the contract code
    s = re.split(r"[.:_\-]", s, maxsplit=1)[0]
    # Strip month-code + 1-2 digit year suffix
    match = re.match(r"^([A-Z]+?)([HMUZFGJKNQVX])(\d{1,2})$", s)
    if match:
        return match.group(1)
    return s


def _get_price_divisor(symbol: str) -> float:
    """Get price divisor for a symbol."""
    root = _symbol_to_root(symbol)
    return PRICE_DIVISORS.get(root, 100.0)


# Sane price ranges per root symbol. Used by `_normalize_price` to figure out
# whether the raw value is already in dollars (Sim files) or in cents-like
# units (most Live files). When both interpretations fall in range, prefer
# the configured divisor; when neither does, fall through to that too.
_SANE_PRICE_RANGES = {
    "ES":  (1000, 20000),  "MES": (1000, 20000),
    "NQ":  (2000, 50000),  "MNQ": (2000, 50000),
    "RTY": (500, 5000),    "M2K": (500, 5000),
    "YM":  (10000, 80000), "MYM": (10000, 80000),
    "GC":  (500, 10000),   "MGC": (500, 10000),
    "SI":  (5, 200),       "SIL": (5, 200),
    "CL":  (10, 300),      "MCL": (10, 300),
    "NG":  (1, 30),
    "FDAX": (5000, 30000),
}


def _normalize_price(symbol: str, raw_price: float, divisor: float) -> float:
    """Map raw fill price to dollars, robust to Sim vs Live scaling.

    Live SC files store raw / divisor (e.g. ES=742950, divisor=100 → 7429.50).
    Sim SC files store the dollar value directly (raw=7429.50 with NO divisor
    needed). We try both and pick whichever lands inside the configured sane
    range for the instrument.
    """
    root = _symbol_to_root(symbol)
    rng = _SANE_PRICE_RANGES.get(root)
    candidate_divided = raw_price / divisor if divisor else raw_price
    if rng is None:
        return candidate_divided
    lo, hi = rng
    if lo <= candidate_divided <= hi:
        return candidate_divided
    if lo <= raw_price <= hi:
        return raw_price
    return candidate_divided


def parse_trade_log(filepath: str) -> list[dict]:
    """Parse a Sierra Chart trade activity log binary file.

    Returns a list of record dicts with decoded fields.
    """
    with open(filepath, "rb") as f:
        data = f.read()

    records = []
    current = {}
    offset = 0
    size = len(data)

    while offset + 8 <= size:
        tag = struct.unpack_from("<I", data, offset)[0]
        length = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8

        if offset + length > size:
            break

        # Record delimiter
        if tag == TAG_DELIMITER:
            if current:
                records.append(current)
                current = {}
            continue

        raw = data[offset : offset + length]
        offset += length

        # Decode by tag type
        if tag in I64_TAGS and length == 8:
            current[tag] = struct.unpack_from("<q", raw, 0)[0]
        elif tag in U32_TAGS and length == 4:
            current[tag] = struct.unpack_from("<I", raw, 0)[0]
        elif tag in F64_TAGS and length == 8:
            current[tag] = struct.unpack_from("<d", raw, 0)[0]
        elif tag in STR_TAGS:
            current[tag] = raw.decode("utf-8", errors="replace").rstrip("\x00")
        elif tag == TAG_BUY_SELL and length >= 1:
            current[tag] = raw[0]
        elif tag == TAG_TIME_IN_FORCE and length >= 1:
            current[tag] = raw[0]
        elif tag == TAG_SIDE_CODE and length >= 1:
            current[tag] = raw[0]
        elif tag == TAG_UNKNOWN_133 and length >= 1:
            current[tag] = raw[0]
        else:
            current[tag] = raw  # Keep as bytes

    # Last record
    if current:
        records.append(current)

    return records


@dataclass
class Fill:
    """A single order fill."""
    timestamp: datetime
    timestamp_ms: int
    symbol: str
    side: str           # "BUY" or "SELL"
    price: float        # Actual price (after divisor)
    quantity: float
    order_id: str       # External order ID
    fill_id: str
    account: str
    order_type: str     # "Limit", "Stop", "Market"
    description: str
    is_sim: bool = False  # came from a *.simulated.data SC file or QT demo


@dataclass
class Trade:
    """A round-trip trade (entry + exit)."""
    id: str
    symbol: str
    root_symbol: str
    account: str
    side: str                # "LONG" or "SHORT"
    entry_time: datetime
    entry_time_ms: int
    entry_price: float
    entry_qty: float
    exit_time: Optional[datetime] = None
    exit_time_ms: Optional[int] = None
    exit_price: Optional[float] = None
    exit_qty: Optional[float] = None
    pnl_points: Optional[float] = None
    pnl_dollars: Optional[float] = None   # gross P&L (before commissions)
    commissions: Optional[float] = None   # total round-trip commissions
    net_pnl: Optional[float] = None       # pnl_dollars - commissions
    duration_seconds: Optional[float] = None
    entry_order_type: str = ""
    exit_order_type: str = ""
    fills: list[Fill] = field(default_factory=list)
    is_open: bool = True
    is_sim: bool = False     # Sierra '.simulated.data' or Quantower demo


def extract_fills(records: list[dict], source_path: str = "") -> list[Fill]:
    """Extract fill events from parsed records.

    A fill is identified by status==2 AND one of:
      - "Filled" in description  (Live exchange-confirmed fill)
      - non-zero FILL_PRICE      (Sim fills don't include "Filled" in the
                                  description but DO populate FILL_PRICE)
      - FILL_QTY > 0             (extra safety net)

    Using status==2 alone would catch duplicate "sent-to-exchange" rows for
    the same fill on Live accounts, so we still need at least one of the
    signal conditions above.

    `source_path` is optional but recommended: it sets the per-fill `is_sim`
    flag when the SC file is the `.simulated.data` variant.
    """
    src_is_sim = ".simulated" in (source_path or "").lower()
    fills = []

    for rec in records:
        desc = rec.get(TAG_DESCRIPTION, "")
        status = rec.get(TAG_STATUS)
        # tif not currently used — kept for future expansion
        # tif = rec.get(TAG_TIME_IN_FORCE)

        if status != 2:
            continue

        fp = rec.get(TAG_FILL_PRICE)
        fq = rec.get(TAG_FILL_QTY)
        is_fill = (
            ("Filled" in desc)
            or (isinstance(fp, float) and fp != 0)
            or (isinstance(fq, float) and fq > 0)
        )

        if not is_fill:
            continue

        symbol = rec.get(TAG_SYMBOL, "")
        if not symbol:
            continue

        divisor = _get_price_divisor(symbol)

        # Get raw fill price (prefer tag 113, fall back to tag 110)
        fill_price_raw = rec.get(TAG_FILL_PRICE)
        if fill_price_raw is not None and isinstance(fill_price_raw, float) and fill_price_raw != 0:
            raw_price = fill_price_raw
        else:
            price_raw = rec.get(TAG_PRICE, 0)
            if isinstance(price_raw, float):
                raw_price = price_raw
            else:
                continue

        # Auto-detect price scale. Live Sierra files store prices in cents-
        # like units (e.g. ES=742950 → /100 → 7429.50). Sim files store the
        # already-scaled dollar value (7429.50 with no divisor needed). Pick
        # whichever interpretation lands in the sane price range for this
        # instrument; fall back to the configured divisor if neither does.
        price = _normalize_price(symbol, raw_price, divisor)

        # Get fill quantity
        fill_qty = rec.get(TAG_FILL_QTY)
        if fill_qty is not None and isinstance(fill_qty, float) and fill_qty > 0:
            qty = fill_qty
        else:
            qty_raw = rec.get(TAG_QUANTITY)
            if qty_raw is not None and isinstance(qty_raw, float) and qty_raw > 0:
                qty = qty_raw
            else:
                qty = 1.0

        buy_sell = rec.get(TAG_BUY_SELL, 0)
        side = "BUY" if buy_sell == 1 else "SELL"

        dt_raw = rec.get(TAG_DATETIME, 0)
        if dt_raw <= 0:
            continue

        ts = sc_datetime_to_utc(dt_raw)
        ts_ms = sc_datetime_to_unix_ms(dt_raw)

        ext_id = rec.get(TAG_EXT_ORDER_ID, "")
        fill_id_raw = rec.get(TAG_FILL_ID, b"")
        if isinstance(fill_id_raw, bytes):
            fill_id = fill_id_raw.decode("utf-8", errors="replace").rstrip("\x00")
        else:
            fill_id = str(fill_id_raw)

        account = rec.get(TAG_ACCOUNT, "")
        order_type = rec.get(TAG_ORDER_TYPE, "")

        fills.append(Fill(
            timestamp=ts,
            timestamp_ms=ts_ms,
            symbol=symbol,
            side=side,
            price=price,
            quantity=qty,
            order_id=ext_id,
            fill_id=fill_id,
            account=account,
            order_type=order_type,
            description=desc,
            is_sim=src_is_sim,
        ))

    return fills


def _adjust_entry_fills(entry_fills: list[Fill], close_qty: float) -> list[Fill]:
    """Create fill records with quantities adjusted to match close_qty.

    For partial closes, the entry fills may have more total qty than was closed.
    This returns copies with quantities proportionally scaled to sum to close_qty.
    """
    total_entry = sum(f.quantity for f in entry_fills)
    if abs(total_entry - close_qty) < 0.001:
        return list(entry_fills)  # exact match, no adjustment needed

    adjusted = []
    remaining = close_qty
    for f in entry_fills:
        qty = min(f.quantity, remaining)
        if qty <= 0:
            break
        adjusted.append(Fill(
            timestamp=f.timestamp, timestamp_ms=f.timestamp_ms,
            symbol=f.symbol, side=f.side, price=f.price,
            quantity=qty, order_id=f.order_id, fill_id=f.fill_id,
            account=f.account, order_type=f.order_type,
            description=f.description,
        ))
        remaining -= qty
    return adjusted


def reconstruct_trades(fills: list[Fill]) -> list[Trade]:
    """Reconstruct grouped round-trip trades from fills.

    A trade = one position lifecycle: flat -> entry (possibly scaled in)
    -> exits (possibly scaled out) -> flat.
    Each trade contains ALL entry and exit fills.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for fill in fills:
        key = (fill.account, fill.symbol)
        groups[key].append(fill)

    trades = []

    for (account, symbol), group_fills in groups.items():
        group_fills.sort(key=lambda f: f.timestamp_ms)

        root = _symbol_to_root(symbol)
        from config import POINT_VALUES, get_commission_per_side
        point_value = POINT_VALUES.get(root, 50.0)
        comm_per_side = get_commission_per_side(account, root)

        position = 0.0
        entry_fills: list[Fill] = []
        exit_fills: list[Fill] = []

        def _finalize_trade():
            """Create a Trade from accumulated entry/exit fills."""
            if not entry_fills:
                return

            total_entry_qty = sum(f.quantity for f in entry_fills)
            avg_entry = sum(f.price * f.quantity for f in entry_fills) / total_entry_qty
            trade_side = "LONG" if entry_fills[0].side == "BUY" else "SHORT"

            total_exit_qty = sum(f.quantity for f in exit_fills)
            avg_exit = sum(f.price * f.quantity for f in exit_fills) / total_exit_qty if total_exit_qty > 0 else None

            # P&L from each exit fill against avg entry
            total_pnl_pts = 0.0
            for ef in exit_fills:
                if trade_side == "LONG":
                    total_pnl_pts += (ef.price - avg_entry) * ef.quantity
                else:
                    total_pnl_pts += (avg_entry - ef.price) * ef.quantity

            pnl_dollars = total_pnl_pts * point_value

            # Commission: every fill (entry + exit) * per_side
            total_fill_contracts = total_entry_qty + total_exit_qty
            commissions = round(total_fill_contracts * comm_per_side, 2)
            net_pnl = round(pnl_dollars - commissions, 2)

            is_open = total_exit_qty < total_entry_qty
            last_exit = exit_fills[-1] if exit_fills else None

            # is_sim is propagated from any fill that came from a *.simulated.data
            # file (set by extract_fills via the source-path inspection).
            is_sim = any(getattr(f, "is_sim", False) for f in entry_fills + exit_fills)

            trade = Trade(
                id=f"{account}_{symbol}_{entry_fills[0].timestamp_ms}",
                symbol=symbol,
                root_symbol=root,
                account=account,
                side=trade_side,
                entry_time=entry_fills[0].timestamp,
                entry_time_ms=entry_fills[0].timestamp_ms,
                entry_price=round(avg_entry, 6),
                entry_qty=total_entry_qty,
                exit_time=last_exit.timestamp if last_exit else None,
                exit_time_ms=last_exit.timestamp_ms if last_exit else None,
                exit_price=round(avg_exit, 6) if avg_exit else None,
                exit_qty=total_exit_qty,
                pnl_points=round(total_pnl_pts, 4),
                pnl_dollars=round(pnl_dollars, 2),
                commissions=commissions,
                net_pnl=net_pnl,
                duration_seconds=(last_exit.timestamp - entry_fills[0].timestamp).total_seconds() if last_exit else None,
                entry_order_type=entry_fills[0].order_type,
                exit_order_type=last_exit.order_type if last_exit else "",
                fills=entry_fills + exit_fills,
                is_open=is_open,
                is_sim=is_sim,
            )
            trades.append(trade)

        for fill in group_fills:
            signed_qty = fill.quantity if fill.side == "BUY" else -fill.quantity
            new_position = position + signed_qty

            if position == 0:
                # Starting a new position
                entry_fills = [fill]
                exit_fills = []
                position = new_position
            elif (position > 0 and signed_qty > 0) or (position < 0 and signed_qty < 0):
                # Adding to position (scaling in)
                entry_fills.append(fill)
                position = new_position
            else:
                # Closing (partial or full) or reversing
                close_qty = min(abs(signed_qty), abs(position))
                exit_fills.append(fill)
                position = new_position

                if new_position == 0:
                    # Fully closed — finalize trade
                    _finalize_trade()
                    entry_fills = []
                    exit_fills = []
                elif (position > 0 and new_position < 0) or (position < 0 and new_position > 0):
                    # This shouldn't happen (we capped close_qty), but handle reversal
                    _finalize_trade()
                    entry_fills = [fill]
                    exit_fills = []
                # else: partial close, continue accumulating exits

        # Handle open position at end
        if position != 0 and entry_fills:
            _finalize_trade()

    trades.sort(key=lambda t: t.entry_time_ms)
    return trades


def discover_log_files() -> list[dict]:
    """Discover all trade activity log files across SC instances.

    Returns list of {path, date, account, instance} dicts.
    """
    files = []
    pattern = re.compile(
        r"TradeActivityLog_(\d{4}-\d{2}-\d{2})_UTC\.(.+)\.data$"
    )

    for log_dir in TRADE_LOG_DIRS:
        if not os.path.isdir(log_dir):
            continue
        instance = os.path.basename(os.path.dirname(log_dir))
        for fname in os.listdir(log_dir):
            m = pattern.match(fname)
            if not m:
                continue
            dt_str, account = m.group(1), m.group(2)
            # Skip "None" account (demo/unassigned)
            if account == "None":
                continue
            # Skip simulated unless explicitly wanted
            is_sim = "simulated" in account.lower() or account.startswith("Sim")
            files.append({
                "path": os.path.join(log_dir, fname),
                "date": dt_str,
                "account": account.replace(".simulated", ""),
                "instance": instance,
                "is_sim": is_sim,
            })

    files.sort(key=lambda x: (x["date"], x["account"]))
    return files


def load_trades_for_date(dt: str, account: str = None) -> list[Trade]:
    """Load and reconstruct trades for a specific date.

    Args:
        dt: Date string "YYYY-MM-DD"
        account: Optional account filter
    """
    log_files = discover_log_files()
    all_fills = []

    for lf in log_files:
        if lf["date"] != dt:
            continue
        if account and lf["account"] != account:
            continue
        records = parse_trade_log(lf["path"])
        fills = extract_fills(records)
        all_fills.extend(fills)

    return reconstruct_trades(all_fills)


def get_available_dates() -> list[str]:
    """Get all dates that have trade activity logs."""
    log_files = discover_log_files()
    dates = sorted(set(lf["date"] for lf in log_files))
    return dates


def get_accounts() -> list[str]:
    """Get all unique account IDs."""
    log_files = discover_log_files()
    accounts = sorted(set(lf["account"] for lf in log_files))
    return accounts


if __name__ == "__main__":
    # Quick test
    files = discover_log_files()
    print(f"Found {len(files)} log files")
    for f in files[:5]:
        print(f"  {f['date']} | {f['account']} | {f['instance']}")

    if files:
        # Parse most recent file
        latest = files[-1]
        print(f"\nParsing {latest['path']}...")
        records = parse_trade_log(latest["path"])
        print(f"  {len(records)} records")

        fills = extract_fills(records)
        print(f"  {len(fills)} fills")
        for fill in fills[:5]:
            print(f"    {fill.timestamp} {fill.side} {fill.quantity}x {fill.symbol} @ {fill.price} ({fill.order_type})")

        trades = reconstruct_trades(fills)
        print(f"\n  {len(trades)} trades")
        for t in trades[:5]:
            status = "OPEN" if t.is_open else "CLOSED"
            pnl = f"P&L: {t.pnl_points:+.2f} pts (${t.pnl_dollars:+.2f})" if t.pnl_dollars is not None else ""
            print(f"    {t.entry_time} {t.side} {t.entry_qty}x {t.symbol} @ {t.entry_price:.2f} -> {t.exit_price or 'open'} {status} {pnl}")
