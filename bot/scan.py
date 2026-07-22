"""
Scan orchestrator: universe -> data -> engine -> alert strings.

Returns plain data so callers decide delivery: the Discord bot posts the
messages; `python -m bot.scan --dry-run` prints them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from bot import alerts, db, sectors, universe
from bot.data import build_snapshot, fetch_closes, today_et
from bot.engine import entry_step, exit_step


@dataclass
class ScanResult:
    digest: str | None = None            # one message for all new triggers
    messages: list = field(default_factory=list)  # individual BUY/WARN/SELL
    muted_buys: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


def run_scan(conn, mode="live", tickers=None, m60_prox_pct=None):
    if m60_prox_pct is None:
        import os
        m60_prox_pct = float(os.environ.get("M60_PROXIMITY_PCT", "10"))
    today = today_et().isoformat()
    scan_set = tickers if tickers is not None else universe.full_universe(conn)
    result = ScanResult()
    result.log.append(f"scan mode={mode} date={today} universe={len(scan_set)}")

    closes = fetch_closes(scan_set)
    result.log.append(f"data for {len(closes)}/{len(scan_set)} tickers")

    prev_states = db.get_all_ticker_states(conn)
    open_positions = {p["ticker"]: p for p in db.get_open_positions(conn)}

    snapshots = {}
    buy_entries = []
    triggered_this_scan = {}  # ticker -> (snap, legs) awaiting same-scan BUY
    seeded = triggers = buys = 0

    # ---- entry engine over the whole universe ----
    for ticker in scan_set:
        series = closes.get(ticker)
        prev = prev_states.get(ticker)
        if series is None:
            # Data outage: keep prior state untouched, never demote/eject.
            if prev is not None:
                lvl = "ERROR" if ticker in open_positions else "WARN"
                result.log.append(f"{lvl}: no data for {ticker}; state kept")
            continue
        snap = build_snapshot(ticker, series, mode=mode)
        if snap is None:
            continue
        snapshots[ticker] = snap

        was_seeded = prev is not None
        new_state, events = entry_step(prev, snap, today, m60_prox_pct=m60_prox_pct)
        db.put_ticker_state(conn, ticker, new_state)
        if not was_seeded:
            seeded += 1

        for event in events:
            if event["type"] == "TRIGGER":
                triggers += 1
                triggered_this_scan[ticker] = (snap, event["legs"])
            elif event["type"] == "BUY":
                buys += 1
                # A same-scan trigger+BUY shows only in a BUY section.
                triggered_this_scan.pop(ticker, None)
                pos = open_positions.get(ticker)
                if pos and not pos["exit_alerted"]:
                    # Held and no exit alert yet: mute per user rule.
                    result.muted_buys.append(ticker)
                    result.log.append(f"BUY for {ticker} muted (held)")
                    continue
                buy_entries.append(
                    (ticker, snap, event.get("legs") or [],
                     alerts.buy_waits(snap, event))
                )

    watch_entries = [
        (t, snap, legs) for t, (snap, legs) in triggered_this_scan.items()
    ]
    cats = {}
    if buy_entries or watch_entries:
        try:
            smap = universe.sector_map(conn)
        except Exception as exc:
            smap = {}
            result.log.append(f"WARN: sector lookup failed ({exc}); flat report")
        cats = {
            e[0]: sectors.category(e[0], smap.get(e[0]))
            for e in buy_entries + watch_entries
        }
    result.digest = alerts.scan_report(buy_entries, watch_entries, cats)

    # ---- exit engine over held positions only ----
    for ticker, pos in open_positions.items():
        snap = snapshots.get(ticker)
        if snap is None:
            result.log.append(f"ERROR: no data for held position {ticker}")
            continue
        flags, events = exit_step(db.position_flags(pos), snap, today)
        db.update_position_flags(conn, pos["id"], flags)
        for event in events:
            if event["type"] == "WARNING":
                result.messages.append(alerts.warning_message(ticker, snap, pos))
            elif event["type"] == "SELL":
                result.messages.append(alerts.sell_message(ticker, snap, pos, event))

    # ---- drop state for tickers that left the universe (no open position;
    # held tickers are always part of scan_set via full_universe) ----
    if tickers is None:  # only on full scans, never on ad-hoc subsets
        gone = set(prev_states) - set(scan_set)
        if gone:
            db.delete_ticker_states(conn, gone)
            result.log.append(f"dropped {len(gone)} departed tickers: {sorted(gone)[:10]}")

    conn.commit()
    result.stats = {
        "universe": len(scan_set),
        "with_data": len(closes),
        "seeded": seeded,
        "triggers": triggers,
        "buys": buys,
        "positions": len(open_positions),
        "alerts": len(result.messages) + (1 if result.digest else 0),
    }
    result.log.append(f"done: {result.stats}")
    return result


def main():
    ap = argparse.ArgumentParser(description="Run one scan (no Discord needed)")
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--mode", choices=["live", "close"], default="live")
    ap.add_argument("--m60-prox", type=float, default=None,
                    help="percent-of-price proximity to the 60m SMA below which "
                         "below-60m signals announce (default: env M60_PROXIMITY_PCT or 10)")
    ap.add_argument("--tickers", help="comma-separated subset (skips universe)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print alerts instead of anything else (scan state IS persisted to --db)")
    args = ap.parse_args()

    conn = db.connect(args.db)
    subset = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    result = run_scan(conn, mode=args.mode, tickers=subset, m60_prox_pct=args.m60_prox)

    for line in result.log:
        print(line)
    print("---")
    if result.digest:
        print(result.digest, "\n")
    for msg in result.messages:
        print(msg, "\n")
    if not result.digest and not result.messages:
        print("(no alerts)")


if __name__ == "__main__":
    main()
