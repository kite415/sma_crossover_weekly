"""SQLite persistence: ticker state, positions ledger, watchlist, caches."""

import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticker_state (
    ticker  TEXT PRIMARY KEY,
    state   TEXT NOT NULL,          -- JSON blob from engine.entry_step
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    qty           REAL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    exit_price    REAL,
    exit_alerted  INTEGER NOT NULL DEFAULT 0,
    warn_armed    INTEGER,          -- NULL until seeded by the first scan
    above_5w      INTEGER           -- NULL until seeded by the first scan
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_position_per_ticker
    ON positions (ticker) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS watchlist (
    ticker   TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS universe_cache (
    source     TEXT PRIMARY KEY,    -- 'sp500' | 'sp400'
    tickers    TEXT NOT NULL,       -- JSON {symbol: sector} (legacy: list)
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticker_sectors (
    ticker     TEXT PRIMARY KEY,    -- non-index tickers (watchlist/positions)
    sector     TEXT,
    fetched_at TEXT NOT NULL
);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)  # sqlite won't create missing dirs
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Ticker state (entry engine)
# --------------------------------------------------------------------------- #

def get_ticker_state(conn, ticker):
    row = conn.execute(
        "SELECT state FROM ticker_state WHERE ticker = ?", (ticker,)
    ).fetchone()
    return json.loads(row["state"]) if row else None

def get_all_ticker_states(conn):
    return {
        row["ticker"]: json.loads(row["state"])
        for row in conn.execute("SELECT ticker, state FROM ticker_state")
    }

def put_ticker_state(conn, ticker, state):
    conn.execute(
        "INSERT INTO ticker_state (ticker, state, updated) VALUES (?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET state = excluded.state, "
        "updated = excluded.updated",
        (ticker, json.dumps(state), utcnow()),
    )

def delete_ticker_states(conn, tickers):
    conn.executemany(
        "DELETE FROM ticker_state WHERE ticker = ?", [(t,) for t in tickers]
    )


# --------------------------------------------------------------------------- #
# Positions ledger
# --------------------------------------------------------------------------- #

def open_position(conn, ticker, entry_price, qty=None):
    """Returns the new row id, or None if a position is already open."""
    try:
        cur = conn.execute(
            "INSERT INTO positions (ticker, entry_price, qty, opened_at) "
            "VALUES (?, ?, ?, ?)",
            (ticker, entry_price, qty, utcnow()),
        )
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid

def close_position(conn, ticker, exit_price=None):
    """Returns the closed row (as dict) or None if nothing was open."""
    row = get_open_position(conn, ticker)
    if row is None:
        return None
    conn.execute(
        "UPDATE positions SET closed_at = ?, exit_price = ? WHERE id = ?",
        (utcnow(), exit_price, row["id"]),
    )
    conn.commit()
    return row

def get_open_position(conn, ticker):
    row = conn.execute(
        "SELECT * FROM positions WHERE ticker = ? AND closed_at IS NULL",
        (ticker,),
    ).fetchone()
    return dict(row) if row else None

def get_open_positions(conn):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM positions WHERE closed_at IS NULL ORDER BY opened_at"
        )
    ]

def update_position_flags(conn, pos_id, flags):
    conn.execute(
        "UPDATE positions SET warn_armed = ?, above_5w = ?, exit_alerted = ? "
        "WHERE id = ?",
        (
            _b(flags.get("warn_armed")),
            _b(flags.get("above_5w")),
            1 if flags.get("exit_alerted") else 0,
            pos_id,
        ),
    )

def _b(v):
    return None if v is None else (1 if v else 0)

def position_flags(row):
    """DB row -> the flags dict engine.exit_step expects."""
    return {
        "warn_armed": None if row["warn_armed"] is None else bool(row["warn_armed"]),
        "above_5w": None if row["above_5w"] is None else bool(row["above_5w"]),
        "exit_alerted": bool(row["exit_alerted"]),
    }


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #

def watchlist_add(conn, ticker):
    cur = conn.execute(
        "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
        (ticker, utcnow()),
    )
    conn.commit()
    return cur.rowcount > 0

def watchlist_remove(conn, ticker):
    cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
    conn.commit()
    return cur.rowcount > 0

def watchlist_all(conn):
    return [row["ticker"] for row in conn.execute("SELECT ticker FROM watchlist ORDER BY ticker")]


# --------------------------------------------------------------------------- #
# Universe cache
# --------------------------------------------------------------------------- #

def cache_universe(conn, source, tickers):
    """tickers: {symbol: sector} (a plain iterable also works, stored as list)."""
    payload = (
        json.dumps(tickers, sort_keys=True)
        if isinstance(tickers, dict)
        else json.dumps(sorted(tickers))
    )
    conn.execute(
        "INSERT INTO universe_cache (source, tickers, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(source) DO UPDATE SET tickers = excluded.tickers, "
        "fetched_at = excluded.fetched_at",
        (source, payload, utcnow()),
    )
    conn.commit()

def cached_universe(conn, source):
    row = conn.execute(
        "SELECT tickers, fetched_at FROM universe_cache WHERE source = ?",
        (source,),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row["tickers"]), row["fetched_at"]


def get_ticker_sectors(conn, tickers):
    if not tickers:
        return {}
    qs = ",".join("?" * len(tickers))
    return {
        row["ticker"]: row["sector"]
        for row in conn.execute(
            f"SELECT ticker, sector FROM ticker_sectors WHERE ticker IN ({qs})",
            list(tickers),
        )
    }

def put_ticker_sector(conn, ticker, sector):
    conn.execute(
        "INSERT INTO ticker_sectors (ticker, sector, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET sector = excluded.sector, "
        "fetched_at = excluded.fetched_at",
        (ticker, sector, utcnow()),
    )
    conn.commit()
