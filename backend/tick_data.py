"""Read tick/OHLC data from parquet files or Sierra Chart SCID files."""
import os
import sys
import glob
import struct
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from config import PARQUET_DIRS, SIERRA_CHART_PATHS, PRICE_DIVISORS

import numpy as np

try:
    import pyarrow.parquet as pq
    HAS_ARROW = True
except ImportError:
    HAS_ARROW = False

# ── SCID reader (from TickerManagement) ──────────────────────────

SCID_HEADER_SIZE = 56
SCID_RECORD_SIZE = 40
_SC_TO_UNIX_US = int((datetime(1970, 1, 1) - datetime(1899, 12, 30)).total_seconds() * 1_000_000)

# Map micro symbols to their full-size equivalents for tick data
MICRO_TO_FULL = {
    "MES": "ES",
    "MNQ": "NQ",
    "MGC": "GC",
}


def _sc_us_to_ns(sc_us: int) -> int:
    return (sc_us - _SC_TO_UNIX_US) * 1000


def _bisect_scid(data: bytes, n: int, target_us: int) -> int:
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        off = SCID_HEADER_SIZE + mid * SCID_RECORD_SIZE
        val = struct.unpack_from("<q", data, off)[0]
        if val < target_us:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _read_scid_for_date(scid_path: str, target_date: date) -> dict | None:
    """Read SCID file, extract ticks for a single date.

    Returns dict with ts_ns, price, volume, bid_volume, ask_volume numpy arrays.
    """
    data = Path(scid_path).read_bytes()
    if len(data) < SCID_HEADER_SIZE or data[:4] != b"SCID":
        return None

    rec_size = struct.unpack_from("<I", data, 8)[0]
    if rec_size != SCID_RECORD_SIZE:
        return None

    n_records = (len(data) - SCID_HEADER_SIZE) // SCID_RECORD_SIZE
    if n_records == 0:
        return None

    sc_epoch = datetime(1899, 12, 30)
    # Date bounds in SC microseconds
    dt_start = datetime.combine(target_date, datetime.min.time())
    dt_end = dt_start + timedelta(days=1)
    start_us = int((dt_start - sc_epoch).total_seconds() * 1_000_000)
    end_us = int((dt_end - sc_epoch).total_seconds() * 1_000_000)

    first_idx = _bisect_scid(data, n_records, start_us)
    last_idx = _bisect_scid(data, n_records, end_us)

    count = last_idx - first_idx
    if count <= 0:
        return None

    rec_data = data[SCID_HEADER_SIZE + first_idx * SCID_RECORD_SIZE:
                    SCID_HEADER_SIZE + last_idx * SCID_RECORD_SIZE]

    dt_raw = np.ndarray(count, dtype="<i8", buffer=rec_data, offset=0, strides=(SCID_RECORD_SIZE,))
    close = np.ndarray(count, dtype="<f4", buffer=rec_data, offset=20, strides=(SCID_RECORD_SIZE,))
    total_vol = np.ndarray(count, dtype="<u4", buffer=rec_data, offset=28, strides=(SCID_RECORD_SIZE,))
    bid_vol = np.ndarray(count, dtype="<u4", buffer=rec_data, offset=32, strides=(SCID_RECORD_SIZE,))
    ask_vol = np.ndarray(count, dtype="<u4", buffer=rec_data, offset=36, strides=(SCID_RECORD_SIZE,))

    ts_ns = ((dt_raw.astype(np.int64) - _SC_TO_UNIX_US) * 1000).copy()
    price = close.astype(np.float32).copy()
    volume = total_vol.astype(np.int32).copy()
    bv = bid_vol.astype(np.int32).copy()
    av = ask_vol.astype(np.int32).copy()

    return {"ts_ns": ts_ns, "price": price, "volume": volume, "bid_volume": bv, "ask_volume": av}


def _extract_contract(symbol: str | None) -> str | None:
    """Pull the contract code (e.g. ESU26, MESH26) out of a stored symbol.

    Quantower stores symbols like 'ESU26.XCME' or '/ESH7'. Sierra stores like
    'ESM26' directly. We just want the first dot-segment uppercased.
    """
    if not symbol:
        return None
    return symbol.split(".")[0].lstrip("/").upper() or None


def _find_scid_files(root_symbol: str, date_str: str,
                     contract: str | None = None) -> list[str]:
    """Find all SCID files for the given symbol across all SC instances.

    If `contract` is given (e.g. 'ESU26') the result is filtered to files for
    that specific contract — critical during roll periods when the front month
    has a much larger file than the contract actually traded.

    Returns list sorted by file size descending (largest/most complete first).
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    year_2digit = dt.strftime("%y")[1]  # single digit for SC naming: 2026 -> "6"
    year_2full = dt.strftime("%y")  # "26"

    # Determine likely contract month codes
    # Quarterly: H=Mar, M=Jun, U=Sep, Z=Dec
    month = dt.month
    if month <= 3:
        codes = ["H"]
    elif month <= 6:
        codes = ["M"]
    elif month <= 9:
        codes = ["U"]
    else:
        codes = ["Z"]
    # Also check adjacent quarter
    all_codes = ["H", "M", "U", "Z"]
    idx = all_codes.index(codes[0])
    if idx + 1 < len(all_codes):
        codes.append(all_codes[idx + 1])

    patterns = []
    for code in codes:
        patterns.extend([
            f"{root_symbol}{code}{year_2digit}.CME.scid",
            f"{root_symbol}{code}{year_2full}_FUT_CME.scid",
            f"{root_symbol}{code}{year_2full}-CME.scid",
            f"{root_symbol}{code}{year_2digit}_FUT_CME.scid",
        ])

    found = []
    for sc_path in SIERRA_CHART_PATHS:
        data_dir = os.path.join(sc_path, "Data")
        if not os.path.isdir(data_dir):
            continue
        for pattern in patterns:
            full = os.path.join(data_dir, pattern)
            if os.path.isfile(full):
                found.append(full)

    # If we know the exact contract the user traded, filter to ONLY files for
    # that contract. Roll-week bug fix: trader was on ESU26 (3 GB ESM file
    # existed and got picked) — without this filter we'd pick the wrong stream.
    if contract:
        c = contract.upper()
        narrowed = [p for p in found if os.path.basename(p).upper().startswith(c)]
        if narrowed:
            found = narrowed

    # Sort by file size descending — largest file has most complete data
    found.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return found


def _find_scid_file(root_symbol: str, date_str: str,
                    contract: str | None = None) -> str | None:
    """Find best SCID file for the given symbol (largest first)."""
    files = _find_scid_files(root_symbol, date_str, contract=contract)
    return files[0] if files else None


# ── Parquet reader ───────────────────────────────────────────────

def _find_parquet_file(root_symbol: str, date_str: str,
                       contract: str | None = None) -> str | None:
    """Find the parquet tick file for a given date. If `contract` is given,
    we filter to subfolders whose name starts with that contract code
    (e.g. ESU26_202606/) to avoid picking up the front-month data during roll.
    """
    base_dir = PARQUET_DIRS.get(root_symbol)
    if not base_dir or not os.path.isdir(base_dir):
        return None

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    yyyymmdd = dt.strftime("%Y%m%d")
    yyyymm = dt.strftime("%Y%m")

    def _filter(paths: list[str]) -> list[str]:
        if not contract:
            return paths
        c = contract.upper()
        # Subfolder names are like "ESM26_202606" or "MESM26_202606"
        narrowed = [p for p in paths
                    if os.path.basename(os.path.dirname(p)).upper().startswith(c + "_")]
        return narrowed or paths

    pattern = os.path.join(base_dir, f"*_{yyyymm}", f"{yyyymmdd}.parquet")
    matches = _filter(glob.glob(pattern))
    if matches:
        return matches[0]

    for delta in [-1, 1]:
        alt_month = (dt.month + delta - 1) % 12 + 1
        alt_year = dt.year + (1 if dt.month + delta > 12 else (-1 if dt.month + delta < 1 else 0))
        alt_yyyymm = f"{alt_year}{alt_month:02d}"
        pattern = os.path.join(base_dir, f"*_{alt_yyyymm}", f"{yyyymmdd}.parquet")
        matches = _filter(glob.glob(pattern))
        if matches:
            return matches[0]

    return None


def _read_parquet_trades(filepath: str) -> dict | None:
    """Read trade ticks from parquet file, return same format as SCID reader."""
    if not HAS_ARROW:
        return None
    table = pq.read_table(filepath, columns=["ts_ns", "level", "type", "price", "volume"])
    df = table.to_pandas()
    trades = df[(df["level"] == 1) & (df["type"].isin([2, 4, 5]))]
    if trades.empty:
        return None
    return {
        "ts_ns": trades["ts_ns"].values.astype(np.int64),
        "price": trades["price"].values.astype(np.float32),
        "volume": trades["volume"].values.astype(np.int32),
        "bid_volume": np.where(trades["type"].values.astype(int) == 4, trades["volume"].values, 0).astype(np.int32),
        "ask_volume": np.where(trades["type"].values.astype(int) == 2, trades["volume"].values, 0).astype(np.int32),
    }


# ── Unified data access ─────────────────────────────────────────

def _resolve_symbol(root_symbol: str) -> str:
    """Resolve micro symbols to full-size for tick data lookup."""
    return MICRO_TO_FULL.get(root_symbol, root_symbol)


from functools import lru_cache


@lru_cache(maxsize=16)
def _load_ticks_cached(root_symbol: str, date_str: str,
                       contract: str | None = None) -> dict | None:
    """LRU-cached tick loader — TradeDetail fires 3 chart requests per page
    view, each previously triggered a fresh ~1.5s parquet/SCID read of the
    whole day. Caching makes calls 2+ instant. Returns None if no data.

    The cache key includes `contract` so the same root symbol on the same
    date can serve different contracts (ESM26 vs ESU26 during roll week)
    from separate cache slots.
    """
    return _load_ticks_impl(root_symbol, date_str, contract=contract)


def _load_ticks(root_symbol: str, date_str: str,
                contract: str | None = None) -> dict | None:
    return _load_ticks_cached(root_symbol, date_str, contract=contract)


def _load_ticks_impl(root_symbol: str, date_str: str,
                     contract: str | None = None) -> dict | None:
    """Load tick data from best available source.

    Priority: parquet > SCID. Maps micro symbols (MES) to full (ES).
    Parquet prices are already scaled; SCID prices need divisor applied.

    `contract` (e.g. 'ESU26') narrows the file search — essential during
    contract roll periods when multiple files exist for the same root.
    """
    lookup_symbol = _resolve_symbol(root_symbol)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    # Try parquet first (prices already in correct scale)
    pq_path = _find_parquet_file(lookup_symbol, date_str, contract=contract)
    if pq_path:
        result = _read_parquet_trades(pq_path)
        if result and len(result["ts_ns"]) > 0:
            return result

    # Fall back to SCID — auto-detect scale (some files store raw dollars,
    # others store cents). Use a sensible price range for each instrument.
    sane_max = _SANE_PRICE_MAX.get(lookup_symbol, _SANE_PRICE_MAX.get(root_symbol, 100_000))

    for scid_path in _find_scid_files(lookup_symbol, date_str, contract=contract):
        result = _read_scid_for_date(scid_path, target_date)
        if result and len(result["ts_ns"]) > 0:
            result["price"] = _scale_scid_prices(result["price"], sane_max)
            return result

    if root_symbol != lookup_symbol:
        for scid_path in _find_scid_files(root_symbol, date_str, contract=contract):
            result = _read_scid_for_date(scid_path, target_date)
            if result and len(result["ts_ns"]) > 0:
                result["price"] = _scale_scid_prices(result["price"], sane_max)
                return result

    return None


# Maximum sensible price per instrument (dollars). If raw SCID price exceeds
# this, we assume cent-scaling and divide by 100.
_SANE_PRICE_MAX = {
    "ES": 50_000, "MES": 50_000,
    "NQ": 100_000, "MNQ": 100_000,
    "GC": 20_000, "MGC": 20_000,
    "FDAX": 50_000,
    "RTY": 10_000, "MYM": 100_000,
}


def _scale_scid_prices(prices: np.ndarray, sane_max: float) -> np.ndarray:
    """Auto-detect SCID price scale (dollars vs cents) and normalize."""
    if len(prices) == 0:
        return prices
    median = float(np.median(prices))
    if median > sane_max:
        # Cents-scaled — divide by 100
        return prices / 100.0
    return prices


def get_ohlc_bars(root_symbol: str, date_str: str, interval_seconds: int = 60,
                  start_ms: int | None = None, end_ms: int | None = None,
                  contract: str | None = None) -> list[dict]:
    """Build OHLC bars from tick data.

    Args:
        root_symbol: e.g. "ES", "NQ", "MES"
        date_str: "YYYY-MM-DD"
        interval_seconds: Bar interval in seconds (default 60 = 1min)
        start_ms / end_ms: optional window — only aggregate ticks in this range.
                          Huge speedup for the close-up trade chart endpoint.
        contract:         exact contract code (e.g. 'ESU26') to disambiguate
                          during roll periods. Without this, the loader picks
                          the largest matching file, which is usually the
                          front month — wrong when the trader is on the back.

    Returns list of {time, open, high, low, close, volume, bid_vol, ask_vol, delta} dicts.
    Uses np.reduceat for an O(N) single-pass aggregation instead of the O(N*B)
    boolean-mask loop the old version did. 100x faster on 24-hour 15s charts.
    """
    ticks = _load_ticks(root_symbol, date_str, contract=contract)
    if ticks is None:
        return []

    ts_ns = ticks["ts_ns"]
    prices = ticks["price"]
    volumes = ticks["volume"]
    bid_vols = ticks.get("bid_volume", np.zeros_like(volumes))
    ask_vols = ticks.get("ask_volume", np.zeros_like(volumes))

    # Optional time-window slice — done BEFORE bar aggregation so we don't
    # touch ticks outside the requested range.
    if start_ms is not None or end_ms is not None:
        ts_ms = ts_ns // 1_000_000
        lo = start_ms if start_ms is not None else int(ts_ms.min())
        hi = end_ms if end_ms is not None else int(ts_ms.max())
        m = (ts_ms >= lo) & (ts_ms <= hi)
        if not m.any():
            return []
        ts_ns = ts_ns[m]
        prices = prices[m]
        volumes = volumes[m]
        bid_vols = bid_vols[m]
        ask_vols = ask_vols[m]

    if len(ts_ns) == 0:
        return []

    epoch_s = ts_ns // 1_000_000_000
    bar_epochs = (epoch_s // interval_seconds) * interval_seconds

    # Single-pass groupby: ticks are time-sorted, so bar_epochs is non-decreasing.
    # Find boundary indices where bar_epoch changes — these mark each bar's start.
    n = len(bar_epochs)
    changes = np.empty(n, dtype=bool)
    changes[0] = True
    np.not_equal(bar_epochs[1:], bar_epochs[:-1], out=changes[1:])
    start_idx = np.flatnonzero(changes)
    if start_idx.size == 0:
        return []

    # Per-bar aggregations using np.reduceat (single-pass, vectorized).
    bar_times = bar_epochs[start_idx]
    highs = np.maximum.reduceat(prices, start_idx)
    lows = np.minimum.reduceat(prices, start_idx)
    opens = prices[start_idx]
    # close = price at the END of each bar = price just before the next bar starts
    end_idx = np.concatenate([start_idx[1:] - 1, [n - 1]])
    closes = prices[end_idx]
    vol_sums = np.add.reduceat(volumes, start_idx)
    bid_sums = np.add.reduceat(bid_vols, start_idx)
    ask_sums = np.add.reduceat(ask_vols, start_idx)
    deltas = ask_sums - bid_sums

    return [
        {
            "time": int(bar_times[i]),
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": int(vol_sums[i]),
            "bid_vol": int(bid_sums[i]),
            "ask_vol": int(ask_sums[i]),
            "delta": int(deltas[i]),
        }
        for i in range(len(bar_times))
    ]


def get_tick_data_around_trade(root_symbol: str, date_str: str,
                                entry_ms: int, exit_ms: int,
                                padding_minutes: int = 5) -> list[dict]:
    """Get tick-level data around a trade for detailed chart."""
    ticks = _load_ticks(root_symbol, date_str)
    if ticks is None:
        return []

    ts_ms = ticks["ts_ns"] // 1_000_000
    padding_ms = padding_minutes * 60 * 1000
    start_ms = entry_ms - padding_ms
    end_ms = (exit_ms or entry_ms) + padding_ms

    mask = (ts_ms >= start_ms) & (ts_ms <= end_ms)

    result = []
    for i in np.where(mask)[0]:
        av = int(ticks["ask_volume"][i])
        result.append({
            "time_ms": int(ts_ms[i]),
            "price": float(ticks["price"][i]),
            "volume": int(ticks["volume"][i]),
            "side": "ask" if av > 0 else "bid",
        })

    return result
