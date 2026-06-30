"""
Volume-profile Business Zones generator — Python equivalent of
SierraChart's BusinessZones.cpp study.

For each (symbol, date) we compute, from RTH tick data:

  - POC  Point of Control: the price level with the highest total volume
  - VAH  Value Area High:  upper boundary of the value area
  - VAL  Value Area Low:   lower boundary of the value area
  - RTH  high / low / total volume
  - Single-print zones: contiguous price ranges where only a SINGLE 30-min
    TPO period traded (gaps in time-price distribution).

Algorithm:
  1. Bin all RTH ticks into price levels of `tick_size`.
  2. POC = price level with highest cumulative volume.
  3. Value Area: starting from POC, expand symmetrically (1 step at a time,
     taking the heavier neighbor each iteration) until cumulative volume
     covers `value_area_pct` (default 70%) of total RTH volume.
  4. Single prints: divide RTH session into 30-min "TPO" buckets. For each
     price bin, count how many TPO buckets had ANY trade there. A price bin
     with count == 1 is a "single print." Contiguous singles become a zone.

RTH definition (ES, NQ, etc.): 09:30-16:00 NY local. Configurable.
"""
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

import numpy as np

from config import TICK_SIZES


# RTH window in NY local
RTH_START_HOUR = 9
RTH_START_MIN = 30
RTH_END_HOUR = 16
RTH_END_MIN = 0
# TPO period in minutes for single-print detection
TPO_MINUTES = 30
# Default value area fraction
VALUE_AREA_PCT = 0.70


@dataclass
class BusinessZones:
    symbol: str
    date: str
    poc: float
    vah: float
    val: float
    rth_high: float
    rth_low: float
    total_volume: int
    tick_size: float
    value_area_pct: float
    singles: list  # list of {low: float, high: float, n_letters: int}

    def to_dict(self):
        d = asdict(self)
        return d


def _rth_window_utc(date_str: str) -> tuple[int, int]:
    """Return (start_ms, end_ms) for RTH session on the given date in UTC ms.

    NY local 09:30-16:00 is UTC -4 in EDT (Apr-Nov) or -5 in EST.
    For simplicity assume EDT for May-Oct, EST otherwise.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # EDT covers second Sunday Mar -> first Sunday Nov; rough heuristic:
    is_dst = (dt.month, dt.day) > (3, 14) and (dt.month, dt.day) < (11, 7)
    offset_hr = -4 if is_dst else -5
    ny_start = dt.replace(hour=RTH_START_HOUR, minute=RTH_START_MIN)
    ny_end = dt.replace(hour=RTH_END_HOUR, minute=RTH_END_MIN)
    utc_start = ny_start - timedelta(hours=offset_hr)  # NY → UTC: add abs(offset)
    utc_end = ny_end - timedelta(hours=offset_hr)
    return (int(utc_start.replace(tzinfo=timezone.utc).timestamp() * 1000),
            int(utc_end.replace(tzinfo=timezone.utc).timestamp() * 1000))


def compute_zones(symbol: str, date_str: str, ticks: dict,
                  value_area_pct: float = VALUE_AREA_PCT) -> BusinessZones | None:
    """Build BusinessZones from a tick-data dict (ts_ns, price, volume)."""
    if ticks is None or len(ticks["ts_ns"]) == 0:
        return None

    tick_size = TICK_SIZES.get(symbol, 0.25)
    start_ms, end_ms = _rth_window_utc(date_str)
    ts_ms = ticks["ts_ns"] // 1_000_000
    in_rth = (ts_ms >= start_ms) & (ts_ms <= end_ms)
    if not in_rth.any():
        return None

    px = ticks["price"][in_rth].astype(float)
    vol = ticks["volume"][in_rth].astype(np.int64)
    ts_in = ts_ms[in_rth]

    if len(px) == 0:
        return None

    # Build volume histogram in price-tick bins
    lo = float(px.min())
    hi = float(px.max())
    # Snap to tick grid
    lo_t = round(lo / tick_size) * tick_size
    hi_t = round(hi / tick_size) * tick_size
    n_bins = int(round((hi_t - lo_t) / tick_size)) + 1
    if n_bins <= 1:
        return None
    bin_idx = np.round((px - lo_t) / tick_size).astype(np.int64)
    bin_idx = np.clip(bin_idx, 0, n_bins - 1)
    vol_by_bin = np.bincount(bin_idx, weights=vol, minlength=n_bins).astype(np.int64)
    total_vol = int(vol_by_bin.sum())
    if total_vol == 0:
        return None

    # POC: bin with highest volume
    poc_bin = int(np.argmax(vol_by_bin))
    poc_price = lo_t + poc_bin * tick_size

    # Value area: expand symmetrically from POC
    target_vol = int(total_vol * value_area_pct)
    acc_vol = int(vol_by_bin[poc_bin])
    up_idx = poc_bin
    dn_idx = poc_bin
    while acc_vol < target_vol and (up_idx < n_bins - 1 or dn_idx > 0):
        # Look at next-up and next-down 1-bin (some systems look at 2-bin sums)
        up_v = int(vol_by_bin[up_idx + 1]) if up_idx + 1 < n_bins else -1
        dn_v = int(vol_by_bin[dn_idx - 1]) if dn_idx - 1 >= 0 else -1
        if up_v == -1 and dn_v == -1:
            break
        if up_v >= dn_v:
            up_idx += 1
            acc_vol += up_v
        else:
            dn_idx -= 1
            acc_vol += dn_v
    vah_price = lo_t + up_idx * tick_size
    val_price = lo_t + dn_idx * tick_size

    # Single prints: divide RTH into 30-min TPO buckets, count TPOs per bin
    tpo_ms = TPO_MINUTES * 60_000
    tpo_id = (ts_in - start_ms) // tpo_ms
    tpo_count = np.zeros(n_bins, dtype=np.int32)
    unique_tpo = np.unique(tpo_id)
    for tid in unique_tpo:
        m = tpo_id == tid
        b_in_tpo = bin_idx[m]
        if len(b_in_tpo) == 0:
            continue
        # which bins did this TPO touch?
        unique_bins = np.unique(b_in_tpo)
        tpo_count[unique_bins] += 1

    # Singles = bins with exactly 1 TPO and which are inside the RTH range
    # but NOT inside the value area (singles in VA aren't useful)
    singles_mask = (tpo_count == 1)
    # group contiguous singles into zones
    singles = []
    i = 0
    while i < n_bins:
        if not singles_mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n_bins and singles_mask[j + 1]:
            j += 1
        # bins i..j (inclusive) are contiguous singles
        zone_lo = lo_t + i * tick_size
        zone_hi = lo_t + j * tick_size
        # skip 1-tick singles unless explicitly wanted; we keep all
        singles.append({"low": zone_lo, "high": zone_hi, "n_letters": int(j - i + 1)})
        i = j + 1

    return BusinessZones(
        symbol=symbol, date=date_str,
        poc=float(poc_price), vah=float(vah_price), val=float(val_price),
        rth_high=float(px.max()), rth_low=float(px.min()),
        total_volume=total_vol, tick_size=tick_size,
        value_area_pct=value_area_pct,
        singles=singles,
    )


# ------------------- DB caching -------------------

def get_or_compute_zones(conn, symbol: str, date_str: str,
                         tick_loader,
                         contract: str | None = None) -> BusinessZones | None:
    """Read cached zones from DB; if missing, compute and store.

    If `contract` is given (e.g. 'ESU26'), the cache key uses the contract
    code instead of the bare root, so ESM26 and ESU26 don't share zones
    during roll weeks. Tick data is also loaded for that specific contract.
    """
    cache_key = (contract or symbol).upper()
    row = conn.execute(
        "SELECT * FROM business_zones WHERE symbol=? AND date=?",
        (cache_key, date_str)
    ).fetchone()
    if row:
        return BusinessZones(
            symbol=row["symbol"], date=row["date"],
            poc=row["poc"], vah=row["vah"], val=row["val"],
            rth_high=row["rth_high"], rth_low=row["rth_low"],
            total_volume=row["total_volume"], tick_size=row["tick_size"],
            value_area_pct=row["value_area_pct"],
            singles=json.loads(row["singles_json"] or "[]"),
        )
    # tick_loader signature: (root_symbol, date_str, contract=None)
    try:
        ticks = tick_loader(symbol, date_str, contract=contract)
    except TypeError:
        # back-compat for callers passing a tick_loader without `contract`
        ticks = tick_loader(symbol, date_str)
    z = compute_zones(symbol, date_str, ticks)
    if z is None:
        return None
    z.symbol = cache_key  # store with contract-specific key
    conn.execute("""
        INSERT OR REPLACE INTO business_zones
        (symbol, date, poc, vah, val, rth_high, rth_low, total_volume,
         tick_size, value_area_pct, singles_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        z.symbol, z.date, z.poc, z.vah, z.val, z.rth_high, z.rth_low,
        z.total_volume, z.tick_size, z.value_area_pct,
        json.dumps(z.singles),
    ))
    conn.commit()
    return z


def get_zones_window(conn, symbol: str, end_date: str, days_back: int,
                     tick_loader,
                     contract: str | None = None) -> list[BusinessZones]:
    """Return BusinessZones for the last `days_back` trading days (or fewer if
    tick data is missing) up to and including `end_date`.
    Older days first; previous-day at the end."""
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    out = []
    days_added = 0
    delta = 1
    while days_added < days_back and delta < days_back * 3 + 10:
        d = end - timedelta(days=delta)
        delta += 1
        if d.weekday() >= 5:
            continue
        z = get_or_compute_zones(conn, symbol, d.strftime("%Y-%m-%d"),
                                 tick_loader, contract=contract)
        if z is not None:
            out.append(z)
            days_added += 1
    out.reverse()
    return out


if __name__ == "__main__":
    # Quick smoke test
    import sys
    sys.path.insert(0, ".")
    import database as db
    from tick_data import _load_ticks
    conn = db.get_db()
    z = get_or_compute_zones(conn, "ES", "2026-06-17", _load_ticks)
    if z is None:
        print("no data")
    else:
        print(f"ES 2026-06-17 zones:")
        print(f"  RTH range: {z.rth_low:.2f} .. {z.rth_high:.2f}")
        print(f"  POC: {z.poc:.2f}")
        print(f"  VAH: {z.vah:.2f}  VAL: {z.val:.2f}")
        print(f"  Total RTH volume: {z.total_volume:,}")
        print(f"  Single-print zones: {len(z.singles)}")
        for s in z.singles[:5]:
            print(f"    {s['low']:.2f} .. {s['high']:.2f}  ({s['n_letters']} letters)")
    conn.close()
