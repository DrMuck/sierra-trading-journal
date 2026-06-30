"""Backend logging — file rotation + console + uncaught-exception capture.

Why this exists: when a user runs into a problem ("Loading trade..." stuck,
"no tick data", import returns 0 trades) you need to see what the backend
actually did. The console window the user spawned with start.bat is fine
for live debugging, but logs/ keeps a persistent record so they can paste
the relevant lines into an issue.

Log files live at:
   backend/logs/journal-YYYY-MM-DD.log

One file per UTC day, rotated automatically. Old files are kept indefinitely
(text files are tiny — usually a few KB per day even with INFO level).

Levels honoured from `LOG_LEVEL` in .env (default INFO). Set to DEBUG to see
SQL queries, tick-file probe results, etc.
"""
from __future__ import annotations
import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime

# Resolved here so import-order doesn't matter — log_setup is the first
# non-stdlib import in main.py.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.environ.get("LOG_DIR") or os.path.join(_BACKEND_DIR, "logs")
_LOG_LEVEL = (os.environ.get("LOG_LEVEL") or "INFO").upper()

# Module-level singleton so re-imports don't double-attach handlers.
_INSTALLED = False


def get_log_dir() -> str:
    return _LOG_DIR


def install(app=None) -> logging.Logger:
    """Configure the root logger. Call once at app startup.

    If `app` is a FastAPI instance, also install an exception handler that
    logs the traceback before re-raising — without this, FastAPI swallows
    uncaught errors into a generic 500 with no stack on disk.
    """
    global _INSTALLED
    logger = logging.getLogger("journal")

    if not _INSTALLED:
        os.makedirs(_LOG_DIR, exist_ok=True)
        level = getattr(logging, _LOG_LEVEL, logging.INFO)
        logger.setLevel(level)

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [%(filename)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # File: one file per day, kept forever (text is tiny)
        log_path = os.path.join(
            _LOG_DIR, f"journal-{datetime.utcnow().strftime('%Y-%m-%d')}.log"
        )
        fh = logging.handlers.TimedRotatingFileHandler(
            log_path, when="midnight", utc=True, backupCount=0, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # Console (stderr) — matches what the user sees in the start.bat window
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # Don't double-emit via the root logger
        logger.propagate = False

        # Also route Python's default warnings module
        logging.captureWarnings(True)

        _INSTALLED = True
        logger.info("Logging installed — level=%s dir=%s", _LOG_LEVEL, _LOG_DIR)

    if app is not None:
        _install_fastapi_handlers(app, logger)

    return logger


def _install_fastapi_handlers(app, logger: logging.Logger) -> None:
    """Wire FastAPI's exception path so uncaught errors land in the log."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(Exception)
    async def _log_unhandled(request: Request, exc: Exception):
        tb = traceback.format_exc()
        logger.error("Unhandled exception on %s %s\n%s",
                     request.method, request.url.path, tb)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "internal server error",
                "type": exc.__class__.__name__,
                "where": f"{request.method} {request.url.path}",
                "hint": "see backend/logs/journal-<date>.log for the full traceback",
            },
        )


def tail_log(n_lines: int = 200, level_filter: str | None = None) -> list[str]:
    """Return the last N lines from today's log. Used by /api/diagnostics."""
    today = os.path.join(
        _LOG_DIR, f"journal-{datetime.utcnow().strftime('%Y-%m-%d')}.log"
    )
    if not os.path.isfile(today):
        return []
    try:
        with open(today, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    if level_filter:
        token = f" {level_filter.upper()} "
        lines = [l for l in lines if token in l]
    return [l.rstrip("\n") for l in lines[-n_lines:]]
