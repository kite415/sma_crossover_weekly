"""
Scan universe: S&P 500 + S&P 400 constituents (Wikipedia) + personal watchlist.

Constituents are cached in the DB and refreshed at most weekly; a failed
scrape falls back to the cache so a Wikipedia hiccup never kills a scan.
Index membership is the liquidity filter -- a stable universe keeps the
state machine's transitions meaningful (a rotating top-volume list would
seed/evict tickers constantly and miss the very events that put them there).

The Wikipedia tables also carry each ticker's GICS sector, so the cache
stores {symbol: sector}. Watchlist/position tickers outside the indices get
their sector from yfinance once and keep it in the ticker_sectors table.
"""

from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

from bot import db

SOURCES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}
REFRESH_DAYS = 7
_UA = {"User-Agent": "Mozilla/5.0 (sma-scanner-bot; personal use)"}

_SYMBOL_COLS = ("Symbol", "Ticker symbol", "Ticker")
_SECTOR_COL = "GICS Sector"


def normalize(symbol):
    """Wikipedia uses BRK.B; yfinance wants BRK-B."""
    return symbol.strip().upper().replace(".", "-")


def _scrape(url):
    """-> {symbol: sector_or_None} from the constituents table."""
    resp = requests.get(url, headers=_UA, timeout=30)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        sym_col = next((c for c in _SYMBOL_COLS if c in table.columns), None)
        if sym_col is None or len(table) < 100:  # constituent tables are big
            continue
        out = {}
        for _, row in table.iterrows():
            sym = str(row[sym_col]).strip()
            if not sym or sym == "nan":
                continue
            sector = row.get(_SECTOR_COL)
            out[normalize(sym)] = None if pd.isna(sector) else str(sector).strip()
        return out
    raise ValueError(f"no constituent table found at {url}")


def _constituent_map(conn, source, force=False):
    """Cached-with-refresh {symbol: sector} for 'sp500' / 'sp400'."""
    cached, fetched_at = db.cached_universe(conn, source)
    if isinstance(cached, list):  # legacy symbols-only cache: treat as stale
        cached = {s: None for s in cached}
        fetched_at = None
    fresh_enough = False
    if fetched_at:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
        fresh_enough = age < timedelta(days=REFRESH_DAYS)
    if cached and fresh_enough and not force:
        return cached
    try:
        syms = _scrape(SOURCES[source])
        db.cache_universe(conn, source, syms)
        return syms
    except Exception as exc:
        if cached:
            print(f"WARN: {source} scrape failed ({exc}); using cached list")
            return cached
        raise


def constituents(conn, source, force=False):
    return sorted(_constituent_map(conn, source, force))


def _yahoo_sector(ticker):
    try:
        return yf.Ticker(ticker).info.get("sector") or None
    except Exception:
        return None


def sector_map(conn):
    """{ticker: sector} for the whole scan universe. Index tickers come from
    the Wikipedia cache; watchlist/position extras resolve via yfinance once
    and persist in ticker_sectors (failures aren't cached, so they retry)."""
    m = {}
    for source in SOURCES:
        try:
            m.update(_constituent_map(conn, source))
        except Exception:
            pass
    extras = set(db.watchlist_all(conn))
    extras.update(p["ticker"] for p in db.get_open_positions(conn))
    missing = [t for t in sorted(extras) if t not in m]
    known = db.get_ticker_sectors(conn, missing)
    for t in missing:
        if t in known:
            m[t] = known[t]
        else:
            sec = _yahoo_sector(t)
            if sec:
                db.put_ticker_sector(conn, t, sec)
            m[t] = sec
    return m


def sector_of(conn, ticker, fetch_missing=True):
    """Sector for one ticker: index cache -> ticker_sectors -> (optionally)
    a live yfinance lookup. fetch_missing=False keeps it DB-only (fast, safe
    on the event loop)."""
    for source in SOURCES:
        cached, _ = db.cached_universe(conn, source)
        if isinstance(cached, dict) and ticker in cached:
            return cached[ticker]
    sec = db.get_ticker_sectors(conn, [ticker]).get(ticker)
    if sec:
        return sec
    if fetch_missing:
        sec = _yahoo_sector(ticker)
        if sec:
            db.put_ticker_sector(conn, ticker, sec)
    return sec


def full_universe(conn):
    """S&P 500 + 400 + watchlist + any ticker with an open position (a held
    position stays tracked even if it leaves the index or the watchlist)."""
    tickers = set()
    for source in SOURCES:
        tickers.update(_constituent_map(conn, source))
    tickers.update(db.watchlist_all(conn))
    tickers.update(p["ticker"] for p in db.get_open_positions(conn))
    return sorted(tickers)
