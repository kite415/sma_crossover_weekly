"""
Scan universe: S&P 500 + S&P 400 constituents (Wikipedia) + personal watchlist.

Constituents are cached in the DB and refreshed at most weekly; a failed
scrape falls back to the cache so a Wikipedia hiccup never kills a scan.
Index membership is the liquidity filter -- a stable universe keeps the
state machine's transitions meaningful (a rotating top-volume list would
seed/evict tickers constantly and miss the very events that put them there).
"""

from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests

from bot import db

SOURCES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}
REFRESH_DAYS = 7
_UA = {"User-Agent": "Mozilla/5.0 (sma-scanner-bot; personal use)"}


def normalize(symbol):
    """Wikipedia uses BRK.B; yfinance wants BRK-B."""
    return symbol.strip().upper().replace(".", "-")


def _scrape(url):
    resp = requests.get(url, headers=_UA, timeout=30)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        for col in ("Symbol", "Ticker symbol", "Ticker"):
            if col in table.columns:
                syms = [normalize(s) for s in table[col].astype(str) if s.strip()]
                if len(syms) >= 100:  # sanity: constituent tables are big
                    return syms
    raise ValueError(f"no constituent table found at {url}")


def constituents(conn, source, force=False):
    """Cached-with-refresh constituent list for 'sp500' / 'sp400'."""
    cached, fetched_at = db.cached_universe(conn, source)
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


def full_universe(conn):
    """S&P 500 + 400 + watchlist + any ticker with an open position (a held
    position stays tracked even if it leaves the index or the watchlist)."""
    tickers = set()
    for source in SOURCES:
        tickers.update(constituents(conn, source))
    tickers.update(db.watchlist_all(conn))
    tickers.update(p["ticker"] for p in db.get_open_positions(conn))
    return sorted(tickers)
