"""Weekly momentum indicators: RSI, KDJ (K line), MACD.

Pure pandas helpers. Each returns the latest value as a float, or None when
there isn't enough history to compute a meaningful number -- callers treat
None as "condition fails" (an unmeasurable young ticker can't confirm
momentum).
"""

import pandas as pd


def rsi(closes, n=14):
    """Wilder RSI. Needs at least n+1 bars."""
    if len(closes) < n + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0.0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def kdj_k(high, low, close, n=9, smooth=3):
    """K line of KDJ(n,3,3): RSV smoothed with the classic 1/3-2/3 rule
    (equivalent to an EMA with alpha=1/smooth). Needs at least n+2 bars."""
    if len(close) < n + 2:
        return None
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rng = hh - ll
    rsv = (close - ll) / rng * 100.0
    rsv = rsv.mask(rng == 0, 50.0)  # flat range: neutral
    k = rsv.dropna().ewm(alpha=1.0 / smooth, adjust=False).mean()
    if k.empty or pd.isna(k.iloc[-1]):
        return None
    return float(k.iloc[-1])


def macd_line(closes, fast=12, slow=26):
    """MACD line (EMA_fast - EMA_slow). Needs slow+9 bars for stability."""
    if len(closes) < slow + 9:
        return None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    val = float((ema_fast - ema_slow).iloc[-1])
    return None if pd.isna(val) else val
