"""
Market data layer: batch yfinance downloads, weekly/monthly resampling,
in-progress-bar handling, and SMA snapshots consumed by engine.py.
"""

import time
from datetime import datetime

import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _NY = None

MONTHLY_SMAS = (10, 20, 60)
WEEKLY_SMAS = (10, 20, 60)
DAILY_SMAS = (10, 20, 60)
WEEKLY_EXIT_SMA = 5

# 60 monthly bars for the longest SMA (+1 headroom so a value survives the
# close-mode trim of an in-progress bar) ~= 5.1 years.
FETCH_PERIOD = "10y"
BATCH_SIZE = 100


def today_et():
    now = datetime.now(_NY) if _NY else datetime.now()
    return now.date()


def fetch_closes(tickers, period=FETCH_PERIOD, pause=1.0):
    """Batch-download daily closes. Returns {ticker: pd.Series} (missing /
    empty tickers are simply absent). Never raises for individual tickers."""
    out = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), BATCH_SIZE):
        chunk = tickers[i : i + BATCH_SIZE]
        try:
            df = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue  # a whole failed chunk just means those tickers skip
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                closes = (
                    df[t]["Close"] if isinstance(df.columns, pd.MultiIndex)
                    else df["Close"]
                )
                closes = closes.dropna()
                if not closes.empty:
                    out[t] = closes
            except (KeyError, TypeError):
                continue
        if i + BATCH_SIZE < len(tickers):
            time.sleep(pause)  # be polite to Yahoo between chunks
    return out


def _trim_in_progress(series, mode, today):
    """The last weekly/monthly bucket is in progress when its label is a
    future date. live: keep it (tentative); close: drop it."""
    if series.empty:
        return series, False
    in_progress = series.index[-1].date() > today
    if in_progress and mode == "close":
        return series.iloc[:-1], False
    return series, in_progress


def _sma_flags(closes, periods):
    """{"10": bool, ...} for each period with enough history, plus the SMA
    values keyed for display. Periods without len >= period+1 are absent."""
    flags, values = {}, {}
    if closes.empty:
        return flags, values
    cur = float(closes.iloc[-1])
    for p in periods:
        if len(closes) >= p + 1:
            sma = float(closes.rolling(p).mean().iloc[-1])
            flags[str(p)] = cur > sma
            values[str(p)] = round(sma, 4)
    return flags, values


def build_snapshot(ticker, daily_closes, mode="live", today=None):
    """Snapshot dict for engine.py, or None if the ticker can't be evaluated.

    The daily bar is never trimmed: scheduled scans run after the 4pm ET
    close, so the last daily bar is final. (A manual midday /scan evaluates
    the intraday price on the daily timeframe -- documented, not coded away.)
    """
    if today is None:
        today = today_et()
    closes = daily_closes.dropna()
    if closes.empty:
        return None

    weekly, tent_w = _trim_in_progress(
        closes.resample("W-FRI").last().dropna(), mode, today
    )
    monthly, tent_m = _trim_in_progress(
        closes.resample("ME").last().dropna(), mode, today
    )
    if weekly.empty or monthly.empty:
        return None

    d_flags, d_vals = _sma_flags(closes, DAILY_SMAS)
    w_flags, w_vals = _sma_flags(weekly, WEEKLY_SMAS)
    m_flags, m_vals = _sma_flags(monthly, MONTHLY_SMAS)

    above_5w = None
    if len(weekly) >= WEEKLY_EXIT_SMA + 1:
        sma5 = float(weekly.rolling(WEEKLY_EXIT_SMA).mean().iloc[-1])
        above_5w = float(weekly.iloc[-1]) > sma5
        w_vals[str(WEEKLY_EXIT_SMA)] = round(sma5, 4)

    smas = {f"d{k}": v for k, v in d_vals.items()}
    smas.update({f"w{k}": v for k, v in w_vals.items()})
    smas.update({f"m{k}": v for k, v in m_vals.items()})

    return {
        "ticker": ticker,
        "daily_close": round(float(closes.iloc[-1]), 4),
        "weekly_close": round(float(weekly.iloc[-1]), 4),
        "monthly_close": round(float(monthly.iloc[-1]), 4),
        "daily_above": d_flags,
        "weekly_above": w_flags,
        "monthly_above": m_flags,
        "above_5w": above_5w,
        "smas": smas,
        "tentative_weekly": tent_w,
        "tentative_monthly": tent_m,
        "bar_dates": {
            "daily": closes.index[-1].date().isoformat(),
            "weekly": weekly.index[-1].date().isoformat(),
            "monthly": monthly.index[-1].date().isoformat(),
        },
    }
