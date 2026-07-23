"""Sanity checks for the weekly momentum indicators."""

import numpy as np
import pandas as pd

from bot.indicators import kdj_k, macd_line, rsi


def series(values):
    idx = pd.date_range("2024-01-05", periods=len(values), freq="W-FRI")
    return pd.Series(values, index=idx, dtype=float)


RISING = series(np.linspace(50, 150, 60))
FALLING = series(np.linspace(150, 50, 60))
FLAT = series([100.0] * 60)


def test_rsi_direction():
    assert rsi(RISING) > 50
    assert rsi(FALLING) < 50


def test_rsi_pure_uptrend_is_100():
    assert rsi(RISING) == 100.0  # zero losses -> RSI pegs at 100


def test_rsi_known_value():
    # Alternate +2/-1 moves: avg gain ~2x avg loss -> RSI ~ 66.7 asymptotically.
    vals, px = [], 100.0
    for i in range(200):
        px += 2.0 if i % 2 == 0 else -1.0
        vals.append(px)
    assert abs(rsi(series(vals)) - 66.67) < 2.0


def test_kdj_direction():
    up = kdj_k(RISING * 1.01, RISING * 0.99, RISING)
    down = kdj_k(FALLING * 1.01, FALLING * 0.99, FALLING)
    assert up > 80      # riding the top of its 9-week range
    assert down < 20    # pinned to the bottom of its range


def test_kdj_flat_range_is_neutral():
    assert abs(kdj_k(FLAT, FLAT, FLAT) - 50.0) < 1e-9


def test_macd_direction():
    assert macd_line(RISING) > 0
    assert macd_line(FALLING) < 0
    assert abs(macd_line(FLAT)) < 1e-9


def test_insufficient_history_returns_none():
    short = series([100, 101, 102])
    assert rsi(short) is None
    assert kdj_k(short, short, short) is None
    assert macd_line(short) is None
    # MACD needs the longest runway: 30 bars is enough for RSI/KDJ, not MACD.
    med = series(np.linspace(50, 80, 30))
    assert rsi(med) is not None
    assert macd_line(med) is None
