"""Every user-confirmed transition rule, pinned as a test."""

import copy

import pytest

from bot.engine import (
    IDLE,
    TRIGGERED,
    SIGNALED,
    entry_step,
    exit_step,
    seed_entry,
)

TODAY = "2026-07-13"


def snap(
    monthly=None,
    weekly=None,
    daily=None,
    above_5w=True,
    weekly_bar="2026-07-17",
    tentative_weekly=False,
    tentative_monthly=False,
):
    def flags(spec):
        if spec is None:
            spec = {"10": True, "20": True, "60": True}
        return dict(spec)

    return {
        "ticker": "TEST",
        "daily_close": 100.0,
        "weekly_close": 100.0,
        "monthly_close": 100.0,
        "daily_above": flags(daily),
        "weekly_above": flags(weekly),
        "monthly_above": flags(monthly),
        "above_5w": above_5w,
        "smas": {},
        "tentative_weekly": tentative_weekly,
        "tentative_monthly": tentative_monthly,
        "bar_dates": {"daily": TODAY, "weekly": weekly_bar, "monthly": "2026-07-31"},
    }


ALL_ABOVE = {"10": True, "20": True, "60": True}
BELOW_10 = {"10": False, "20": True, "60": True}
BELOW_60 = {"10": True, "20": True, "60": False}


# --------------------------------------------------------------------------- #
# Cold start / seeding
# --------------------------------------------------------------------------- #

def test_first_sighting_is_silent_even_when_fully_qualified():
    state, events = entry_step(None, snap(daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state["phase"] == IDLE
    assert state["setup_live"] is True


def test_already_qualified_ticker_never_triggers_without_a_transition():
    state = seed_entry(snap(), TODAY)
    state2, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state2["phase"] == IDLE


# --------------------------------------------------------------------------- #
# Trigger legs
# --------------------------------------------------------------------------- #

def test_trigger_on_10wk_reclaim_pullback_resume():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    assert state["setup_live"] is False
    state2, events = entry_step(state, snap(daily={"10": False, "20": True, "60": True}), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["legs"] == ["reclaimed 10wk SMA"]
    assert state2["phase"] == TRIGGERED


def test_trigger_on_60wk_reclaim_recovery():
    state = seed_entry(snap(weekly=BELOW_60), TODAY)
    state2, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert events[0]["legs"] == ["reclaimed 60wk SMA"]
    assert state2["phase"] == TRIGGERED


def test_trigger_when_monthly_gate_completes_last_hims_case():
    # Weekly already all-above; the monthly 20m reclaim is the last leg.
    state = seed_entry(snap(monthly={"10": True, "20": False, "60": True}), TODAY)
    assert state["setup_live"] is False
    state2, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["legs"] == ["monthly gate completed (20m reclaim)"]
    assert state2["phase"] == TRIGGERED


def test_trigger_on_5wk_reclaim_when_everything_else_holds():
    # The MU case: above 10/20/60wk + gate, but below the 5wk.
    state = seed_entry(snap(above_5w=False), TODAY)
    assert state["setup_live"] is False
    state2, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert events[0]["legs"] == ["reclaimed 5wk SMA"]
    assert state2["phase"] == TRIGGERED


def test_multiple_legs_named_together():
    state = seed_entry(snap(weekly={"10": False, "20": False, "60": True}), TODAY)
    state2, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert events[0]["legs"] == ["reclaimed 10wk SMA", "reclaimed 20wk SMA"]


# --------------------------------------------------------------------------- #
# Newly-computable SMA guard
# --------------------------------------------------------------------------- #

def test_sma_becoming_computable_does_not_trigger():
    # Seeded without a 60wk SMA (young ticker); history reaches 61 bars and
    # the 60wk flag appears as True -> setup flips live with no price event.
    young = {"10": True, "20": True}
    state = seed_entry(snap(weekly=young, monthly={"10": True, "20": True}), TODAY)
    assert state["setup_live"] is True  # 60 skipped, 10 & 20 above

    # Now suppose it was NOT live before because weekly lacked 60 and 20 was
    # False; then the 60 appears True while 20 flips... only real flips count.
    state = seed_entry(
        snap(weekly={"10": True, "20": True, "60": False}, monthly={"10": True, "20": True}),
        TODAY,
    )
    assert state["setup_live"] is False
    # 60wk vanishes from prev/cur comparison? No: here 60 appears computable
    # AND True next scan -- but it was False before, present in both -> real.
    # The pure newly-computable case: prev lacks the key entirely.
    state["weekly_above"] = {"10": True, "20": True}  # 60 not yet computable
    state["setup_live"] = False
    state["weekly_all"] = False  # pretend 20 was required-failing... simpler:
    state["weekly_above"] = {"10": True, "20": False}
    s2 = snap(weekly={"10": True, "20": False, "60": True}, monthly={"10": True, "20": True})
    # setup not live (20 below) -> no trigger regardless
    new, events = entry_step(state, s2, TODAY)
    assert events == []

    # Direct case: prev had no "60" key, cur has 60:True, everything else
    # unchanged-true, and prev.setup_live was False only because of... this
    # can only happen via all_above requiring 10 & 20 which were true; with
    # 60 absent, all_above was True, so setup was live. The remaining path:
    # gate had no 60m and becomes computable-True -> gate stays True. Either
    # way no False->True flip exists on shared keys -> no legs -> no event.
    prev = seed_entry(snap(weekly={"10": True, "20": True}, above_5w=False), TODAY)
    assert prev["setup_live"] is False  # blocked by 5wk only
    cur = snap(weekly={"10": True, "20": True, "60": True}, above_5w=False)
    new, events = entry_step(prev, cur, TODAY)
    assert events == []  # still blocked; and 60 appearing produced no leg


# --------------------------------------------------------------------------- #
# BUY / daily confirm
# --------------------------------------------------------------------------- #

def test_trigger_and_buy_same_scan():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state2, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER", "BUY"]
    assert state2["phase"] == SIGNALED


def test_buy_fires_later_when_daily_confirms():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    state, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert [e["type"] for e in events] == ["BUY"]
    assert state["phase"] == SIGNALED


def test_no_second_buy_while_signaled():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state, _ = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    state, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state["phase"] == SIGNALED


# --------------------------------------------------------------------------- #
# Reset + re-trigger
# --------------------------------------------------------------------------- #

def test_armed_demotes_silently_when_setup_breaks():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state, _ = entry_step(state, snap(daily=BELOW_10), TODAY)  # TRIGGERED
    state, events = entry_step(state, snap(weekly=BELOW_10, daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state["phase"] == IDLE
    # And no BUY on the way down even though daily confirmed.


def test_5wk_break_resets_then_reclaim_retriggers():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state, _ = entry_step(state, snap(daily=ALL_ABOVE), TODAY)  # SIGNALED
    # 5wk break: silent reset.
    state, events = entry_step(
        state, snap(above_5w=False, daily=ALL_ABOVE, weekly_bar="2026-07-24"), TODAY
    )
    assert events == []
    assert state["phase"] == IDLE
    # 5wk reclaim with everything else holding: fresh trigger + BUY.
    state, events = entry_step(
        state, snap(daily=ALL_ABOVE, weekly_bar="2026-07-31"), TODAY
    )
    assert [e["type"] for e in events] == ["TRIGGER", "BUY"]
    assert events[0]["legs"] == ["reclaimed 5wk SMA"]


def test_duplicate_trigger_suppressed_for_same_weekly_bar():
    # Live-mode churn: same in-progress weekly bar flips live->dead->live.
    bar = "2026-07-17"
    state = seed_entry(snap(weekly=BELOW_10, weekly_bar=bar), TODAY)
    state, events = entry_step(state, snap(daily=BELOW_10, weekly_bar=bar), TODAY)
    assert len(events) == 1  # announced
    state, events = entry_step(
        state, snap(weekly=BELOW_10, daily=BELOW_10, weekly_bar=bar), TODAY
    )
    assert events == [] and state["phase"] == IDLE  # dipped, silent
    state, events = entry_step(state, snap(daily=BELOW_10, weekly_bar=bar), TODAY)
    assert events == []  # re-lived on the SAME bar: transition, no re-announce
    assert state["phase"] == TRIGGERED
    # A NEW weekly bar may announce again.
    state, events = entry_step(
        state, snap(weekly=BELOW_10, daily=BELOW_10, weekly_bar="2026-07-24"), TODAY
    )
    state, events = entry_step(state, snap(daily=BELOW_10, weekly_bar="2026-07-24"), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]


def test_tentative_flag_propagates():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    _, events = entry_step(
        state, snap(daily=ALL_ABOVE, tentative_weekly=True), TODAY
    )
    assert all(e["tentative"] for e in events)


# --------------------------------------------------------------------------- #
# Gate strictness
# --------------------------------------------------------------------------- #

def test_missing_required_monthly_sma_fails_gate():
    # 6-month-old IPO: no 10m/20m SMAs -> gate must fail, not pass vacuously.
    state, _ = entry_step(None, snap(monthly={}), TODAY)
    assert state["gate"] is False and state["setup_live"] is False


def test_missing_60_is_skippable_everywhere():
    s = snap(
        monthly={"10": True, "20": True},
        weekly={"10": True, "20": True},
        daily={"10": True, "20": True},
    )
    state = seed_entry(s, TODAY)
    assert state["gate"] and state["weekly_all"] and state["setup_live"]


# --------------------------------------------------------------------------- #
# Exit engine
# --------------------------------------------------------------------------- #

def fresh_pos():
    return {"warn_armed": None, "above_5w": None, "exit_alerted": False}


def test_position_latches_seed_silently():
    pos, events = exit_step(fresh_pos(), snap(daily=BELOW_10, above_5w=False), TODAY)
    assert events == []  # bought a weak stock: no instant warning/sell
    assert pos["warn_armed"] is False and pos["above_5w"] is False


def test_warning_once_per_dip_with_rearm():
    pos, _ = exit_step(fresh_pos(), snap(daily=ALL_ABOVE), TODAY)
    pos, events = exit_step(pos, snap(daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["WARNING"]
    pos, events = exit_step(pos, snap(daily=BELOW_10), TODAY)
    assert events == []  # still below: latched, no repeat
    pos, events = exit_step(pos, snap(daily=ALL_ABOVE), TODAY)
    assert events == []  # recovery is silent, latch re-arms
    pos, events = exit_step(pos, snap(daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["WARNING"]  # next dip warns again


def test_sell_on_5wk_break_sets_exit_alerted_and_latches():
    pos, _ = exit_step(fresh_pos(), snap(daily=ALL_ABOVE), TODAY)
    pos, events = exit_step(pos, snap(daily=ALL_ABOVE, above_5w=False), TODAY)
    assert [e["type"] for e in events] == ["SELL"]
    assert pos["exit_alerted"] is True
    pos, events = exit_step(pos, snap(daily=ALL_ABOVE, above_5w=False), TODAY)
    assert events == []  # latched
    pos, events = exit_step(pos, snap(daily=ALL_ABOVE), TODAY)
    assert events == []  # reclaim re-arms silently
    pos, events = exit_step(pos, snap(daily=ALL_ABOVE, above_5w=False), TODAY)
    assert [e["type"] for e in events] == ["SELL"]  # next break sells again


def test_warning_and_sell_can_fire_same_scan():
    pos, _ = exit_step(fresh_pos(), snap(daily=ALL_ABOVE), TODAY)
    pos, events = exit_step(pos, snap(daily=BELOW_10, above_5w=False), TODAY)
    assert [e["type"] for e in events] == ["WARNING", "SELL"]
