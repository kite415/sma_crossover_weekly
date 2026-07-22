"""
Pure state machines for the multi-timeframe scanner. No I/O, no Discord,
no yfinance -- (previous state, snapshot) -> (new state, events). Everything
here is unit-tested with synthetic dicts in tests/test_engine.py.

Strategy recap
--------------
Entry engine (runs over the whole universe):
  * monthly gate  = monthly close above its 10- and 20-month SMAs. The
    60-month SMA is context only ("nice to have", user 2026-07-15) -- shown
    on alerts as 60m checkmark/cross but never required and never a trigger leg.
  * weekly setup  = weekly  close above its 10/20/60-week  SMAs
  * setup is LIVE when: gate AND weekly setup
  * TRIGGER  = setup goes not-live -> live via a real price flip; the alert
    names whichever leg(s) completed last (10wk/20wk/60wk reclaim or the
    monthly gate). The 5-week SMA plays NO role in the entry engine -- it
    hugs price so closely that its crossings are noise at universe scale
    (user feedback 2026-07-14); it is only the exit engine's SELL line.
  * BUY      = a triggered ticker's daily close is above its 10/20/60-day SMAs
    (may happen the same scan as the trigger).
  * Alerts are tagged tentative ONLY when the signal is waiting on an
    unfinished bar -- a condition that passes on the live (in-progress)
    weekly/monthly bar but would NOT pass on completed bars alone. The tag
    names what's pending ("pending Fri Jul 17 close", "monthly gate pending
    July close"). An open bar that the signal doesn't depend on never tags
    (user feedback 2026-07-15; earlier blanket rules made the tag noise).

Exit engine (runs ONLY over positions the user actually holds):
  * daily close below the 10-day SMA  -> WARNING, once per dip (re-arms when
    price recovers above the 10-day)
  * weekly close below the 5-week SMA -> SELL (latched; re-arms on reclaim)

An SMA with insufficient history is simply absent from the snapshot's flag
maps. The 10 and 20 flags must exist for a timeframe to pass; the 60 is
skippable (young tickers). A flag that flips only because the SMA *became
computable* (history finally long enough) must never fire a trigger -- only
flips on keys present in both the previous and current maps count.
"""

from datetime import date

# Phases of the entry machine. IDLE covers both "no setup" and "setup active
# but never announced" (cold start) -- a trigger needs a not-live -> live
# transition, so seeding a live setup as IDLE keeps it silent by design.
IDLE = "IDLE"
TRIGGERED = "TRIGGERED"  # setup announced, waiting for the daily confirm
SIGNALED = "SIGNALED"    # BUY sent (or consumed); quiet until the setup resets

REQUIRED_KEYS = ("10", "20")  # these SMAs must exist for a timeframe to pass
GATE_KEYS = ("10", "20")      # the monthly gate: 60m is context, not required


def all_above(flags):
    """True when every computable SMA is above, and 10 & 20 both exist."""
    if not flags:
        return False
    if any(k not in flags for k in REQUIRED_KEYS):
        return False
    return all(flags.values())


def gate_ok(monthly_flags):
    """Monthly gate: above the 10m and 20m SMAs. The 60m never gates."""
    return all((monthly_flags or {}).get(k) is True for k in GATE_KEYS)


def _flipped(prev_map, cur_map):
    """Keys that went False -> True, counting only keys present in BOTH maps
    (a newly computable SMA appearing in cur_map is not a price event)."""
    if not prev_map or not cur_map:
        return []
    return [k for k in cur_map if k in prev_map and cur_map[k] and not prev_map[k]]


def _weekly_label(iso):
    """'2026-07-17' -> 'Fri Jul 17' (day formatted portably)."""
    if not iso:
        return "week"
    d = date.fromisoformat(iso)
    return f"{d.strftime('%a %b')} {d.day}"


def _month_label(iso):
    if not iso:
        return "month"
    return date.fromisoformat(iso).strftime("%B")


def entry_pending(snap, gate, weekly_all):
    """What the current signal is still waiting on: conditions that pass on
    the live bar but not on completed bars alone. Empty when fully confirmed
    (always, in close mode -- confirmed maps equal the live maps there)."""
    pending = []
    weekly_conf = all_above(snap.get("weekly_above_confirmed", snap["weekly_above"]))
    gate_conf = gate_ok(snap.get("monthly_above_confirmed", snap["monthly_above"]))
    if weekly_all and not weekly_conf:
        pending.append(f"pending {_weekly_label(snap['bar_dates'].get('weekly'))} close")
    if gate and not gate_conf:
        pending.append(f"monthly gate pending {_month_label(snap['bar_dates'].get('monthly'))} close")
    return pending


def trigger_legs(prev, snap, gate):
    """Which leg(s) of the setup completed this scan. Empty list means the
    not-live -> live transition wasn't driven by a real price flip."""
    legs = []
    for k in sorted(_flipped(prev.get("weekly_above"), snap["weekly_above"]), key=int):
        legs.append(f"reclaimed {k}wk SMA")
    if gate and prev.get("gate") is False:
        monthly_flips = [
            k for k in _flipped(prev.get("monthly_above"), snap["monthly_above"])
            if k in GATE_KEYS  # a 60m flip is context, never a trigger leg
        ]
        if monthly_flips:
            legs.append(
                "monthly gate completed ("
                + "/".join(f"{k}m" for k in sorted(monthly_flips, key=int))
                + " reclaim)"
            )
    return legs


def seed_entry(snap, today):
    """First sighting of a ticker: record current truth, never alert."""
    gate = gate_ok(snap["monthly_above"])
    weekly_all = all_above(snap["weekly_above"])
    return {
        "phase": IDLE,
        "gate": gate,
        "weekly_all": weekly_all,
        "above_5w": bool(snap.get("above_5w")),  # informational (/status)
        "setup_live": gate and weekly_all,
        "monthly_above": dict(snap["monthly_above"]),
        "weekly_above": dict(snap["weekly_above"]),
        "daily_above": dict(snap["daily_above"]),
        "daily_close": snap.get("daily_close"),
        "smas": dict(snap.get("smas") or {}),
        "last_trigger_week": None,
        "last_trigger_legs": [],
        "weekly_bar": snap["bar_dates"].get("weekly"),
        "updated": today,
    }


def entry_step(prev, snap, today):
    """
    Advance one ticker's entry machine by one scan.

    prev: the stored entry state (dict from seed_entry/entry_step) or None.
    snap: snapshot dict from data.build_snapshot().
    Returns (new_state, events) where events is a list of dicts:
      {"type": "TRIGGER", "legs": [...], "tentative": bool}
      {"type": "BUY", "tentative": bool}
    """
    if prev is None:
        return seed_entry(snap, today), []

    gate = gate_ok(snap["monthly_above"])
    weekly_all = all_above(snap["weekly_above"])
    setup_live = gate and weekly_all
    daily_confirm = all_above(snap["daily_above"])
    weekly_bar = snap["bar_dates"].get("weekly")
    pending = entry_pending(snap, gate, weekly_all)

    events = []
    phase = prev.get("phase", IDLE)
    last_trigger_week = prev.get("last_trigger_week")
    # Legs persist so a BUY that confirms days after its trigger still names
    # what completed the setup.
    last_trigger_legs = prev.get("last_trigger_legs") or []

    if not setup_live:
        phase = IDLE  # silent reset (weekly break or gate break)
    else:
        if phase == IDLE and not prev.get("setup_live", False):
            legs = trigger_legs(prev, snap, gate)
            if legs:  # real price flip -- not just an SMA becoming computable
                phase = TRIGGERED
                last_trigger_legs = legs
                # Live-mode churn guard: the same in-progress weekly bar may
                # flip live->not-live->live across daily scans; transition,
                # but don't re-announce the same weekly bar twice.
                if last_trigger_week != weekly_bar:
                    events.append(
                        {"type": "TRIGGER", "legs": legs,
                         "pending": pending, "tentative": bool(pending)}
                    )
                last_trigger_week = weekly_bar
        if phase == TRIGGERED and daily_confirm:
            phase = SIGNALED
            events.append(
                {"type": "BUY", "legs": last_trigger_legs,
                 "pending": pending, "tentative": bool(pending)}
            )

    new = {
        "phase": phase,
        "gate": gate,
        "weekly_all": weekly_all,
        "above_5w": bool(snap.get("above_5w")),  # informational (/status)
        "setup_live": setup_live,
        "monthly_above": dict(snap["monthly_above"]),
        "weekly_above": dict(snap["weekly_above"]),
        "daily_above": dict(snap["daily_above"]),
        "daily_close": snap.get("daily_close"),
        "smas": dict(snap.get("smas") or {}),
        "last_trigger_week": last_trigger_week,
        "last_trigger_legs": last_trigger_legs,
        "weekly_bar": weekly_bar,
        "updated": today,
    }
    return new, events


def exit_step(pos, snap, today):
    """
    Advance one held position's exit machine by one scan.

    pos: {"warn_armed": bool|None, "above_5w": bool|None, "exit_alerted": bool}
         (None latches = first scan since the position was opened: seed
         silently from current truth so buying a weak stock doesn't insta-warn)
    Returns (new_pos_flags, events) with events like
      {"type": "WARNING", "tentative": bool} / {"type": "SELL", ...}.
    """
    events = []
    new = {
        "warn_armed": pos.get("warn_armed"),
        "above_5w": pos.get("above_5w"),
        "exit_alerted": bool(pos.get("exit_alerted")),
        "updated": today,
    }

    d10 = snap["daily_above"].get("10")
    if d10 is not None:
        if new["warn_armed"] is None:
            new["warn_armed"] = bool(d10)  # silent seed
        elif new["warn_armed"] and not d10:
            events.append({"type": "WARNING", "tentative": False})
            new["warn_armed"] = False
        elif not new["warn_armed"] and d10:
            new["warn_armed"] = True  # recovered above the 10d; re-arm

    above5 = snap.get("above_5w")
    if above5 is not None:
        if new["above_5w"] is None:
            new["above_5w"] = bool(above5)  # silent seed
        elif new["above_5w"] and not above5:
            # Waiting-on rule: tag only if the completed week wasn't already
            # below the 5wk -- i.e. the SELL rests on the open weekly bar.
            conf = snap.get("above_5w_confirmed", above5)
            pending = []
            if conf is not False:
                pending.append(
                    f"pending {_weekly_label(snap['bar_dates'].get('weekly'))} close"
                )
            events.append(
                {"type": "SELL", "pending": pending, "tentative": bool(pending)}
            )
            new["above_5w"] = False
            new["exit_alerted"] = True  # unmutes future BUY signals
        elif not new["above_5w"] and above5:
            new["above_5w"] = True  # re-arm the sell latch

    return new, events
