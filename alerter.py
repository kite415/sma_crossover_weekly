#!/usr/bin/env python3
"""
Daily weekly-SMA crossover alerter -> ntfy.

For each ticker in watchlist.txt:
  * download several years of daily prices (yfinance, no API key),
  * resample to weekly bars (W-FRI, last close of each week),
  * compute the 10-week AND 60-week simple moving averages,
  * detect when the weekly close crosses EITHER SMA in EITHER direction,
  * fire one ntfy notification per crossing (de-duped via state.json).

Because this runs daily, we persist the last-known side (above/below) per
ticker *per SMA* in state.json and only alert when a side actually flips.
First run (or a newly added ticker/SMA) records the side silently -- no
alert blast.
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
CONFIRM_MODE = (os.environ.get("CONFIRM_MODE") or "live").strip().lower()

# Weekly SMAs to track. A crossover of ANY of these (in either direction)
# triggers its own alert. Each SMA is evaluated independently -- a ticker with
# enough history for the 10-week but not the 60-week still gets 10-week alerts.
SMA_WEEKS = [10, 60]

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
    can't be evaluated (no data / too few bars for even the shortest SMA).

    The returned dict carries a "smas" map keyed by SMA length (weeks); each
    entry has side/sma/cross flags for that SMA. Only SMAs with enough weekly
    history to have both a current and prior value are included.
    """
    # Longest SMA drives how much history we need; pull generously so even the
    # 60-week SMA has a solid runway. yfinance accepts 1y/2y/5y/10y/max.
    hist = yf.Ticker(ticker).history(period="5y", interval="1d", auto_adjust=False)
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

    cur_close = float(weekly.iloc[-1])

    smas = {}
    for period in SMA_WEEKS:
        # Need a current + prior bar that both have an SMA value.
        if len(weekly) < period + 1:
            continue
        sma = weekly.rolling(period).mean()
        cur_sma = float(sma.iloc[-1])
        prior_close = float(weekly.iloc[-2])
        prior_sma = float(sma.iloc[-2])

        side = "above" if cur_close > cur_sma else "below"

        # Textbook bar-level crossover flags (for logging; firing is state-driven).
        cross_up = cur_close > cur_sma and prior_close <= prior_sma
        cross_down = cur_close < cur_sma and prior_close >= prior_sma

        smas[period] = {
            "side": side,
            "sma": cur_sma,
            "cross_up": cross_up,
            "cross_down": cross_down,
        }

    if not smas:
        print(
            f"  {ticker}: only {len(weekly)} weekly bars "
            f"(need {min(SMA_WEEKS) + 1}); skipping"
        )
        return None

    return {
        "ticker": ticker,
        "close": cur_close,
        "tentative": tentative,
        "bar_date": weekly.index[-1].date().isoformat(),
        "smas": smas,
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

            prev = state.get(ticker, {})
            tag = " (tentative)" if info["tentative"] else ""

            # Rebuild the ticker's state entry from scratch each run.
            entry = {
                "close": round(info["close"], 4),
                "tentative": info["tentative"],
                "bar_date": info["bar_date"],
                "updated": today_et().isoformat(),
            }

            for period, res in info["smas"].items():
                key = f"sma{period}"
                side = res["side"]

                prev_side = prev.get(key, {}).get("side")
                # Legacy migration: old state stored a single flat "side" for
                # the 10-week SMA. Honour it so we don't re-fire on upgrade.
                if prev_side is None and period == 10:
                    prev_side = prev.get("side")

                print(
                    f"  {ticker}: close ${info['close']:.2f} vs "
                    f"{period}wk SMA ${res['sma']:.2f} -> {side}{tag} "
                    f"(prev={prev_side}, up={res['cross_up']}, down={res['cross_down']})"
                )

                fire = prev_side is not None and side != prev_side

                if fire:
                    if side == "above":
                        arrow, direction, tags = "▲", "crossed above", ["green_circle", "chart_with_upwards_trend"]
                    else:
                        arrow, direction, tags = "▼", "crossed below", ["red_circle", "chart_with_downwards_trend"]

                    title = f"{ticker} {arrow} {direction} {period}wk SMA"
                    body = (
                        f"price ${info['close']:.2f} vs {period}-week SMA "
                        f"${res['sma']:.2f}{tag}"
                    )
                    if send_ntfy(title, body, tags):
                        alerts += 1
                        print(f"    -> ALERT sent: {title} | {body}")
                    else:
                        print(f"    -> alert NOT sent (ntfy failure): {title}")
                elif prev_side is None:
                    print(f"    -> {period}wk first sighting; recording side, no alert")

                entry[key] = {"side": side, "sma": round(res["sma"], 4)}

            # Persist latest sides (always), so we only fire on real flips.
            state[ticker] = entry

        except Exception as exc:  # one bad ticker must not kill the run
            print(f"  {ticker}: ERROR {exc}")
            traceback.print_exc()
            continue

    save_state(STATE_FILE, state)
    print(f"=== done: checked {checked}/{len(tickers)} tickers, {alerts} alert(s) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
