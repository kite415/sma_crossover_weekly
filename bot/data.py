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

# Crash-recovery experiments: a "crash" is a daily close at least CRASH_PCT
# percent below the highest close of the trailing window (trading days).
CRASH_WINDOWS = {"1M": 21, "3M": 63, "6M": 126}
CRASH_PCT = 30.0

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

    # Crash-window facts: percent below the highest close of each trailing
    # window. The experiment layer applies the CRASH_PCT threshold; alerts
    # reuse the same numbers for display.
    crash = {}
    for wname, wdays in CRASH_WINDOWS.items():
        rmax = float(closes.rolling(wdays, min_periods=1).max().iloc[-1])
        crash[wname] = round((rmax - float(closes.iloc[-1])) / rmax * 100.0, 2)

    # Drawdown-episode facts ("high" = highest close inside the fetch window,
    # so ~10y, on adjusted prices). A close at a new high makes the episode
    # low equal the peak -> episode_dd_pct ~ 0, which is how the engine's
    # arm naturally resets on full recovery.
    peak = float(closes.max())
    peak_date = closes.idxmax()
    episode_low = float(closes[closes.index >= peak_date].min())
    drawdown = {
        "peak": round(peak, 4),
        "peak_date": peak_date.date().isoformat(),
        "episode_dd_pct": round((peak - episode_low) / peak * 100.0, 2),
        "off_high_pct": round((peak - float(closes.iloc[-1])) / peak * 100.0, 2),
    }

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
        "drawdown": drawdown,
        "crash": crash,
        "smas": smas,
        "tentative_weekly": tent_w,
        "tentative_monthly": tent_m,
        "bar_dates": {
            "daily": closes.index[-1].date().isoformat(),
            "weekly": weekly.index[-1].date().isoformat(),
            "monthly": monthly.index[-1].date().isoformat(),
            # last COMPLETED weekly bar (None if only the open bar exists) --
            # the engine's confirmed-bar reset compares against this.
            "weekly_confirmed": (
                w_conf_series.index[-1].date().isoformat()
                if len(w_conf_series) else None
            ),
        },
    }


def reconstruct_case(closes, lookback=252):
    """Cold-start for the crash-recovery experiments: rebuild an open case
    from history, or None. A case exists when some day in the trailing
    `lookback` trading days closed >= CRASH_PCT below its rolling-window high
    AND no daily close has beaten the 10-week MA since (entries are genuinely
    still ahead of us; a crossed case is never partially reconstructed).

    The historical daily 10wk MA is approximated from completed weekly closes
    forward-filled to days (the live engine uses the in-progress week too;
    for reconstruction the completed-week line is close enough and strictly
    conservative). Runs once per ticker: its result seeds experiment_state.
    """
    closes = closes.dropna()
    if len(closes) < 30:
        return None
    frac = 1.0 - CRASH_PCT / 100.0
    flags = {
        name: closes <= frac * closes.rolling(w, min_periods=1).max()
        for name, w in CRASH_WINDOWS.items()
    }
    any_crash = flags["1M"] | flags["3M"] | flags["6M"]
    recent = any_crash.iloc[-lookback:]
    crash_days = recent[recent].index
    if len(crash_days) == 0:
        return None

    wk = closes.resample("W-FRI").last().dropna()
    wk = wk[wk.index.date <= closes.index[-1].date()]  # completed weeks only
    ma_daily = wk.rolling(10).mean().reindex(closes.index, method="ffill")
    above = closes > ma_daily  # NaN MA compares False: young history blocks
    above_days = above[above].index
    last_above = above_days.max() if len(above_days) else None

    valid = [d for d in crash_days if last_above is None or d > last_above]
    if not valid:
        return None
    opened = min(valid)
    window = next(
        n for n in ("1M", "3M", "6M") if flags[n][flags[n].index >= opened].any()
    )
    return {
        "opened": opened.date().isoformat(),
        "window": window,
        "trough": round(float(closes[closes.index >= opened].min()), 4),
        "streak": 0,
        "last_conf_week": wk.index[-1].date().isoformat() if len(wk) else None,
        "fired": {},
    }
