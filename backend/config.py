"""Configuration for Trading Journal backend.

Configuration sources, in order of precedence:
  1. Environment variables (set in the shell)
  2. `.env` file in the backend folder (loaded if python-dotenv is installed)
  3. Built-in defaults below

Commissions (broker fees) are read from `commissions.json` in the backend
folder if it exists; otherwise from the hard-coded `_COMMISSIONS_DEFAULT`
dict below.  See `commissions.example.json` for the schema.
"""
from __future__ import annotations
import json
import os
from typing import Any


BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


# ────────────────────────── .env loading ────────────────────────────
# python-dotenv is optional; if it's not installed we just rely on real env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
except ImportError:
    pass


def _env_csv(name: str, default: str) -> list[str]:
    """Read a comma-separated env var into a stripped, non-empty list."""
    raw = os.environ.get(name, default) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name, default)
    return val.strip() if val else default


# ────────────────────────── Paths ───────────────────────────────────
SIERRA_CHART_PATHS = _env_csv("SIERRA_CHART_PATHS", "C:/SierraChart")
TRADE_LOG_DIRS = [os.path.join(p, "TradeActivityLogs") for p in SIERRA_CHART_PATHS]

QUANTOWER_ROOTS = _env_csv("QUANTOWER_ROOTS", "")

TICKER_LIBRARY = _env("TICKER_LIBRARY", "")
PARQUET_DIRS = (
    {sym: os.path.join(TICKER_LIBRARY, f"{sym}_PARQUET")
     for sym in ("ES", "NQ", "GC", "FDAX")}
    if TICKER_LIBRARY else {}
)

DB_PATH = _env("DB_PATH", "") or os.path.join(BACKEND_DIR, "journal.db")


# ────────────────────────── Server ──────────────────────────────────
BACKEND_HOST = _env("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(_env("BACKEND_PORT", "8001"))

FRONTEND_ORIGINS = _env_csv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)


# ────────────────────────── Instrument metadata ─────────────────────
# Tick + point values are intrinsic to the instrument — kept in code, not env.
PRICE_DIVISORS = {
    "ES": 100.0,  "MES": 100.0,
    "NQ": 100.0,  "MNQ": 100.0,
    "GC": 10.0,   "MGC": 10.0,
    "FDAX": 1.0,
}

TICK_SIZES = {
    "ES": 0.25,  "MES": 0.25,
    "NQ": 0.25,  "MNQ": 0.25,
    "GC": 0.10,  "MGC": 0.10,
    "FDAX": 0.50,
    "CL": 0.01,  "MCL": 0.01,
    "RTY": 0.10, "M2K": 0.10,
    "YM": 1.0,   "MYM": 1.0,
}

POINT_VALUES = {
    "ES": 50.0,   "MES": 5.0,
    "NQ": 20.0,   "MNQ": 2.0,
    "GC": 100.0,  "MGC": 10.0,
    "FDAX": 25.0,
    "CL": 1000.0, "MCL": 100.0,
    "RTY": 50.0,  "M2K": 5.0,
    "YM": 5.0,    "MYM": 0.50,
}


# ────────────────────────── Commissions ─────────────────────────────
# Hard-coded fallback if neither commissions.json nor an env override exists.
_COMMISSIONS_DEFAULT: dict[str, dict[str, float]] = {
    "default": {
        "ES": 1.99,  "MES": 0.51,
        "NQ": 1.99,  "MNQ": 0.51,
        "GC": 1.99,  "MGC": 0.51,
        "FDAX": 1.50,
    },
    "per_account": {},
}


def _load_commissions() -> dict[str, Any]:
    """Read commissions.json from the backend folder; fall back to defaults."""
    path = os.path.join(BACKEND_DIR, "commissions.json")
    if not os.path.isfile(path):
        return _COMMISSIONS_DEFAULT
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Strip comment keys so they don't get treated as symbols
        data.pop("_comment_", None)
        if isinstance(data.get("default"), dict):
            data["default"].pop("_comment_", None)
        if isinstance(data.get("per_account"), dict):
            data["per_account"].pop("_comment_", None)
            for acct, rates in data["per_account"].items():
                if isinstance(rates, dict):
                    rates.pop("_comment_", None)
        # Validate shape
        if "default" not in data:
            return _COMMISSIONS_DEFAULT
        if "per_account" not in data:
            data["per_account"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return _COMMISSIONS_DEFAULT


_COMMISSIONS = _load_commissions()
COMMISSIONS_PER_SIDE = dict(_COMMISSIONS["default"])
COMMISSIONS_PER_SIDE_BY_ACCOUNT = {
    acct: dict(rates) for acct, rates in _COMMISSIONS["per_account"].items()
}


def get_commission_per_side(account: str, root_symbol: str) -> float:
    """Look up commission per side: per-account first, then default symbol rate."""
    acct = COMMISSIONS_PER_SIDE_BY_ACCOUNT.get(account)
    if acct and root_symbol in acct:
        return acct[root_symbol]
    return COMMISSIONS_PER_SIDE.get(root_symbol, 0.50)


def reload_commissions() -> None:
    """Re-read commissions.json — useful from the UI after editing the file."""
    global _COMMISSIONS, COMMISSIONS_PER_SIDE, COMMISSIONS_PER_SIDE_BY_ACCOUNT
    _COMMISSIONS = _load_commissions()
    COMMISSIONS_PER_SIDE = dict(_COMMISSIONS["default"])
    COMMISSIONS_PER_SIDE_BY_ACCOUNT = {
        acct: dict(rates) for acct, rates in _COMMISSIONS["per_account"].items()
    }


# ────────────────────────── Constants ───────────────────────────────
# Microseconds between SC's 1899-12-30 epoch and the Unix epoch.
SC_EPOCH_OFFSET_US = 25569 * 86400 * 1_000_000  # 2,209,161,600,000,000
