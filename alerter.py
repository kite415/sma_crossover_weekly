#!/usr/bin/env python3
"""
Daily weekly-SMA crossover alerter -> ntfy.

For each ticker in watchlist.txt:
  * download ~2y of daily prices (yfinance, no API key),
  * resample to weekly bars (W-FRI, last close of each week),
  * compute the 10-week simple moving average,
  * detect when the weekly close crosses its 10-week SMA in EITHER direction,
  * fire one ntfy notification on the transition (de-duped via state.json).

Because this runs daily, we persist the last-known side (above/below) per
ticker in state.json and only alert when that side actually flips. First run
(or a newly added ticker) records the side silently -- no alert blast.
"""

import json
import os
import sys
import traceback
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _NY = None

import requests
import yfinance as yf

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

# "close" -> only evaluate completed weekly bars (confirmed Friday cross).
# "live"  -> also evaluate the current in-progress week; such alerts are
#            tagged "(tentative)".  Env var CONFIRM_MODE overrides this default.
# An unset OR empty env var (scheduled runs pass "") falls back to the default.
CONFIRM_MODE = (os.environ.get("CONFIRM_MODE") or "close").strip().lower()

SMA_WEEKS = 10
MIN_WEEKLY_BARS = SMA_WEEKS + 1  # need a current + prior bar that both have an SMA

WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "watchlist.txt")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def today_et():
    """Today's date in US/Eastern (falls back to local if zoneinfo missing)."""
    now = datetime.now(_NY) if _NY else datetime.now()
    return now.date()


# --------------------------------------------------------------------------- #
# Watchlist / state I/O
# --------------------------------------------------------------------------- #

def load_watchlist(path):
    tickers = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line.upper())
    # de-dupe, preserve order
    seen, out = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARN: could not read state file {path}: {exc}; starting fresh")
        return {}


def save_state(path, state):
    with open(path, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# ntfy
# --------------------------------------------------------------------------- #

def send_ntfy(title, body, tags):
    if not NTFY_TOPIC:
        print("ERROR: NTFY_TOPIC is not set; cannot send notification")
        return False
    # Use ntfy's JSON publishing endpoint so UTF-8 (arrows/emoji) survives --
    # HTTP headers can't carry non-latin-1 characters.
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": body,
        "tags": tags,
    }
    try:
        resp = requests.post(f"{NTFY_SERVER}/", json=payload, timeout=20)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"ERROR: ntfy publish failed: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Core analysis for one ticker
# --------------------------------------------------------------------------- #

def analyse(ticker):
    """
    Returns a dict describing the current state of the ticker, or None if it
    can't be evaluated (no data / too few bars).
    """
    hist = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist:
        print(f"  {ticker}: no price data returned; skipping")
        return None

    closes = hist["Close"].dropna()
    weekly = closes.resample("W-FRI").last().dropna()
    if weekly.empty:
        print(f"  {ticker}: no weekly bars; skipping")
        return None

    # Is the most recent weekly bucket the current, still-in-progress week?
    last_label = weekly.index[-1].date()
    incomplete = last_label > today_et()

    tentative = False
    if CONFIRM_MODE == "live":
        tentative = incomplete  # keep the in-progress week, flag it
    else:  # "close" (default): drop the in-progress week, evaluate confirmed bars
        if incomplete:
            weekly = weekly.iloc[:-1]

    if len(weekly) < MIN_WEEKLY_BARS:
        print(
            f"  {ticker}: only {len(weekly)} weekly bars "
            f"(need {MIN_WEEKLY_BARS}); skipping"
        )
        return None

    sma = weekly.rolling(SMA_WEEKS).mean()

    cur_close = float(weekly.iloc[-1])
    cur_sma = float(sma.iloc[-1])
    prior_close = float(weekly.iloc[-2])
    prior_sma = float(sma.iloc[-2])

    side = "above" if cur_close > cur_sma else "below"

    # Textbook bar-level crossover flags (for logging; firing is state-driven).
    cross_up = cur_close > cur_sma and prior_close <= prior_sma
    cross_down = cur_close < cur_sma and prior_close >= prior_sma

    return {
        "ticker": ticker,
        "side": side,
        "close": cur_close,
        "sma": cur_sma,
        "tentative": tentative,
        "cross_up": cross_up,
        "cross_down": cross_down,
        "bar_date": weekly.index[-1].date().isoformat(),
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    if CONFIRM_MODE not in ("close", "live"):
        print(f"ERROR: CONFIRM_MODE must be 'close' or 'live', got '{CONFIRM_MODE}'")
        return 2

    print(f"=== SMA crossover alerter ===")
    print(f"mode={CONFIRM_MODE}  date(ET)={today_et()}  server={NTFY_SERVER}")
    if not NTFY_TOPIC:
        print("WARN: NTFY_TOPIC not set -- analysis will run but no alerts sent")

    tickers = load_watchlist(WATCHLIST_FILE)
    print(f"watchlist ({len(tickers)}): {', '.join(tickers)}")

    state = load_state(STATE_FILE)
    alerts = 0
    checked = 0

    for ticker in tickers:
        try:
            info = analyse(ticker)
            if info is None:
                continue
            checked += 1

            side = info["side"]
            prev = state.get(ticker, {})
            prev_side = prev.get("side")

            tag = " (tentative)" if info["tentative"] else ""
            print(
                f"  {ticker}: close ${info['close']:.2f} vs "
                f"10wk SMA ${info['sma']:.2f} -> {side}{tag} "
                f"(prev={prev_side}, up={info['cross_up']}, down={info['cross_down']})"
            )

            fire = prev_side is not None and side != prev_side

            if fire:
                if side == "above":
                    arrow, direction, tags = "▲", "crossed above", ["green_circle", "chart_with_upwards_trend"]
                else:
                    arrow, direction, tags = "▼", "crossed below", ["red_circle", "chart_with_downwards_trend"]

                title = f"{ticker} {arrow} {direction}"
                body = (
                    f"price ${info['close']:.2f} vs 10-week SMA "
                    f"${info['sma']:.2f}{tag}"
                )
                if send_ntfy(title, body, tags):
                    alerts += 1
                    print(f"    -> ALERT sent: {title} | {body}")
                else:
                    print(f"    -> alert NOT sent (ntfy failure): {title}")
            elif prev_side is None:
                print(f"    -> first sighting; recording side, no alert")

            # Persist latest side (always), so we only fire on real flips.
            state[ticker] = {
                "side": side,
                "close": round(info["close"], 4),
                "sma": round(info["sma"], 4),
                "tentative": info["tentative"],
                "bar_date": info["bar_date"],
                "updated": today_et().isoformat(),
            }

        except Exception as exc:  # one bad ticker must not kill the run
            print(f"  {ticker}: ERROR {exc}")
            traceback.print_exc()
            continue

    save_state(STATE_FILE, state)
    print(f"=== done: checked {checked}/{len(tickers)} tickers, {alerts} alert(s) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
