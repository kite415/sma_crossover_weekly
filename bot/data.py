"""
Market data layer: batch yfinance downloads, weekly/monthly resampling,
in-progress-bar handling, and SMA snapshots consumed by engine.py.
"""

import time
from datetime import datetime

import pandas as pd
import yfinance as yf

from bot import indicators

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


OHLC_COLS = ["High", "Low", "Close"]


def fetch_ohlc(tickers, period=FETCH_PERIOD, pause=1.0):
    """Batch-download daily High/Low/Close. Returns {ticker: pd.DataFrame}
    (missing / empty tickers are simply absent). Never raises for
    individual tickers. Highs/lows feed the weekly KDJ."""
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
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                ohlc = sub[OHLC_COLS].dropna(subset=["Close"])
                if not ohlc.empty:
                    out[t] = ohlc
            except (KeyError, TypeError):
                continue
        if i + BATCH_SIZE < len(tickers):
            time.sleep(pause)  # be polite to Yahoo between chunks
    return out


def fetch_closes(tickers, period=FETCH_PERIOD, pause=1.0):
    """Close-only view of fetch_ohlc (kept for ticker validation)."""
    return {t: df["Close"] for t, df in fetch_ohlc(tickers, period, pause).items()}


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


def _momentum(weekly_frame):
    """Weekly momentum flags + display values from an H/L/C frame."""
    closes = weekly_frame["Close"]
    r = indicators.rsi(closes)
    k = indicators.kdj_k(weekly_frame["High"], weekly_frame["Low"], closes)
    m = indicators.macd_line(closes)
    flags = {
        "rsi": None if r is None else r > 50.0,
        "kdj": None if k is None else k > 50.0,
        "macd": None if m is None else m > 0.0,
    }
    values = {
        "rsi": None if r is None else round(r, 1),
        "kdj_k": None if k is None else round(k, 1),
        "macd": None if m is None else round(m, 3),
    }
    return flags, values


def build_snapshot(ticker, ohlc, mode="live", today=None):
    """Snapshot dict for engine.py, or None if the ticker can't be evaluated.
    `ohlc` is a daily DataFrame with High/Low/Close (a bare close Series is
    also accepted and upgraded with High=Low=Close).

    The daily bar is never trimmed: scheduled scans run after the 4pm ET
    close, so the last daily bar is final. (A manual midday /scan evaluates
    the intraday price on the daily timeframe -- documented, not coded away.)
    """
    if today is None:
        today = today_et()
    if isinstance(ohlc, pd.Series):
        ohlc = pd.DataFrame({"High": ohlc, "Low": ohlc, "Close": ohlc})
    ohlc = ohlc.dropna(subset=["Close"])
    if ohlc.empty:
        return None
    closes = ohlc["Close"]

    weekly_hlc, tent_w = _trim_in_progress(
        ohlc.resample("W-FRI")
        .agg({"High": "max", "Low": "min", "Close": "last"})
        .dropna(subset=["Close"]),
        mode,
        today,
    )
    weekly = weekly_hlc["Close"]
    monthly, tent_m = _trim_in_progress(
        closes.resample("ME").last().dropna(), mode, today
    )
    if weekly.empty or monthly.empty:
        return None

    d_flags, d_vals = _sma_flags(closes, DAILY_SMAS)
    w_flags, w_vals = _sma_flags(weekly, WEEKLY_SMAS)
    m_flags, m_vals = _sma_flags(monthly, MONTHLY_SMAS)

    # Confirmed-bars-only variants: what the flags would be if the open
    # weekly/monthly bar didn't exist. The engine tags an alert tentative
    # only when a condition passes live but NOT confirmed -- i.e. the signal
    # is genuinely waiting on the bar to close. In close mode the open bar
    # was already dropped, so confirmed == live and nothing is ever pending.
    w_conf_frame = weekly_hlc.iloc[:-1] if tent_w else weekly_hlc
    w_conf_series = w_conf_frame["Close"]
    m_conf_series = monthly.iloc[:-1] if tent_m else monthly
    w_flags_conf, _ = _sma_flags(w_conf_series, WEEKLY_SMAS)
    m_flags_conf, _ = _sma_flags(m_conf_series, MONTHLY_SMAS)

    momentum, momentum_values = _momentum(weekly_hlc)
    momentum_conf, _ = _momentum(w_conf_frame)

    def _above_5w(series):
        if len(series) < WEEKLY_EXIT_SMA + 1:
            return None, None
        sma5 = float(series.rolling(WEEKLY_EXIT_SMA).mean().iloc[-1])
        return float(series.iloc[-1]) > sma5, sma5

    above_5w, sma5 = _above_5w(weekly)
    above_5w_conf, _ = _above_5w(w_conf_series)
    if sma5 is not None:
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
        "weekly_above_confirmed": w_flags_conf,
        "monthly_above_confirmed": m_flags_conf,
        "momentum": momentum,
        "momentum_confirmed": momentum_conf,
        "momentum_values": momentum_values,
        "above_5w": above_5w,
        "above_5w_confirmed": above_5w_conf,
        "smas": smas,
        "tentative_weekly": tent_w,
        "tentative_monthly": tent_m,
        "bar_dates": {
            "daily": closes.index[-1].date().isoformat(),
            "weekly": weekly.index[-1].date().isoformat(),
            "monthly": monthly.index[-1].date().isoformat(),
        },
    }
