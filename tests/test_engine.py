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
    weekly_confirmed=None,   # defaults to the live map = nothing pending
    monthly_confirmed=None,  # defaults to the live map = nothing pending
    above_5w_confirmed="same",
    smas=None,
    daily_close=100.0,
):
    def flags(spec):
        if spec is None:
            spec = {"10": True, "20": True, "60": True}
        return dict(spec)

    live_weekly = flags(weekly)
    live_monthly = flags(monthly)
    return {
        "ticker": "TEST",
        "daily_close": daily_close,
        "weekly_close": 100.0,
        "monthly_close": 100.0,
        "daily_above": flags(daily),
        "weekly_above": live_weekly,
        "monthly_above": live_monthly,
        "weekly_above_confirmed": dict(live_weekly) if weekly_confirmed is None else dict(weekly_confirmed),
        "monthly_above_confirmed": dict(live_monthly) if monthly_confirmed is None else dict(monthly_confirmed),
        "above_5w": above_5w,
        "above_5w_confirmed": above_5w if above_5w_confirmed == "same" else above_5w_confirmed,
        "smas": dict(smas or {}),
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


def test_5wk_reclaim_does_not_trigger():
    # User feedback 2026-07-14: 5wk crossings are noise. A ticker above
    # 10/20/60wk + gate is live regardless of the 5wk; reclaiming the 5wk
    # is a non-event for the entry engine.
    state = seed_entry(snap(above_5w=False), TODAY)
    assert state["setup_live"] is True  # 5wk plays no role in liveness
    state2, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state2["phase"] == IDLE


def test_multiple_legs_named_together():
    state = seed_entry(snap(weekly={"10": False, "20": False, "60": True}), TODAY)
    state2, events = entry_step(state, snap(daily=BELOW_10), TODAY)
    assert events[0]["legs"] == ["reclaimed 10wk SMA", "reclaimed 20wk SMA"]


# --------------------------------------------------------------------------- #
# Newly-computable SMA guard
# --------------------------------------------------------------------------- #

def test_sma_becoming_computable_does_not_trigger():
    # A young ticker without a 60wk SMA is judged on 10/20 alone.
    young = {"10": True, "20": True}
    state = seed_entry(snap(weekly=young, monthly={"10": True, "20": True}), TODAY)
    assert state["setup_live"] is True  # 60 skipped, 10 & 20 above

    # Newly-computable flag on a real flip elsewhere: prev lacked the 60wk
    # key and was blocked only by the monthly gate; the scan where the gate
    # completes ALSO sees the 60wk appear (history hit 61 bars). The trigger
    # must name only the real price flip (the gate), never the new SMA.
    prev = seed_entry(
        snap(weekly={"10": True, "20": True}, monthly={"10": True, "20": False}),
        TODAY,
    )
    assert prev["setup_live"] is False  # blocked by the gate only
    cur = snap(weekly={"10": True, "20": True, "60": True}, monthly={"10": True, "20": True},
               daily=BELOW_10)
    new, events = entry_step(prev, cur, TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["legs"] == ["monthly gate completed (20m reclaim)"]

    # Pure newly-computable case: nothing else flips, the appearing 60wk flag
    # alone flips liveness (60:False was blocking, key present in both maps is
    # required for a leg -- here prev HAS no 60 key, so seed with a real block
    # that never resolves): prev blocked by 20wk=False, cur 20wk still False
    # but 60wk appears True -> still not live, and no leg either way.
    prev = seed_entry(snap(weekly={"10": True, "20": False}), TODAY)
    cur = snap(weekly={"10": True, "20": False, "60": True})
    new, events = entry_step(prev, cur, TODAY)
    assert events == []


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
    # The late BUY still names what completed the setup days earlier.
    assert events[0]["legs"] == ["reclaimed 10wk SMA"]


def test_same_scan_buy_carries_trigger_legs():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    _, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER", "BUY"]
    assert events[1]["legs"] == events[0]["legs"] == ["reclaimed 10wk SMA"]


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


def test_5wk_break_does_not_reset_but_10wk_break_does():
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    state, _ = entry_step(state, snap(daily=ALL_ABOVE), TODAY)  # SIGNALED
    # 5wk break: setup stays intact (5wk is exit-engine-only).
    state, events = entry_step(
        state, snap(above_5w=False, daily=ALL_ABOVE, weekly_bar="2026-07-24"), TODAY
    )
    assert events == []
    assert state["phase"] == SIGNALED
    # Losing the 10wk is the real reset...
    state, events = entry_step(
        state, snap(weekly=BELOW_10, daily=ALL_ABOVE, weekly_bar="2026-07-31"), TODAY
    )
    assert events == []
    assert state["phase"] == IDLE
    # ...and reclaiming it is a fresh trigger + BUY.
    state, events = entry_step(
        state, snap(daily=ALL_ABOVE, weekly_bar="2026-08-07"), TODAY
    )
    assert [e["type"] for e in events] == ["TRIGGER", "BUY"]
    assert events[0]["legs"] == ["reclaimed 10wk SMA"]


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


def test_tentative_only_when_waiting_on_a_bar():
    # (a) Weekly reclaim exists only on the open weekly bar -> pending Friday.
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    _, events = entry_step(
        state, snap(daily=ALL_ABOVE, weekly_confirmed=BELOW_10), TODAY
    )
    assert [e["type"] for e in events] == ["TRIGGER", "BUY"]
    assert all(e["pending"] == ["pending Fri Jul 17 close"] for e in events)
    assert all(e["tentative"] for e in events)

    # (b) Monthly gate rests on the partial month -> pending named month.
    gate_off = {"10": True, "20": False, "60": True}
    state = seed_entry(snap(monthly=gate_off, daily=BELOW_10), TODAY)
    _, events = entry_step(
        state, snap(daily=BELOW_10, monthly_confirmed=gate_off), TODAY
    )
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["pending"] == ["monthly gate pending July close"]

    # (c) Open bars the signal doesn't depend on never tag: confirmed maps
    # equal the live maps -> no pending, no tentative.
    state = seed_entry(snap(weekly=BELOW_10), TODAY)
    _, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert events and not any(e["tentative"] for e in events)
    assert all(e["pending"] == [] for e in events)

    # (d) Both legs waiting -> both named.
    state = seed_entry(snap(weekly=BELOW_10, monthly=gate_off, daily=BELOW_10), TODAY)
    _, events = entry_step(
        state,
        snap(daily=BELOW_10, weekly_confirmed=BELOW_10, monthly_confirmed=gate_off),
        TODAY,
    )
    assert events[0]["pending"] == [
        "pending Fri Jul 17 close",
        "monthly gate pending July close",
    ]


def test_sell_tentative_only_when_resting_on_open_bar():
    pos, _ = exit_step(fresh_pos(), snap(daily=ALL_ABOVE), TODAY)
    # Live weekly close below 5wk but the completed week wasn't -> pending.
    pos, events = exit_step(
        pos, snap(daily=ALL_ABOVE, above_5w=False, above_5w_confirmed=True), TODAY
    )
    assert [e["type"] for e in events] == ["SELL"]
    assert events[0]["pending"] == ["pending Fri Jul 17 close"]

    # Completed week already below (e.g. Friday-evening scan) -> firm SELL.
    pos, _ = exit_step(fresh_pos(), snap(daily=ALL_ABOVE), TODAY)
    pos, events = exit_step(
        pos, snap(daily=ALL_ABOVE, above_5w=False, above_5w_confirmed=False), TODAY
    )
    assert [e["type"] for e in events] == ["SELL"]
    assert events[0]["pending"] == [] and not events[0]["tentative"]


# --------------------------------------------------------------------------- #
# Gate strictness
# --------------------------------------------------------------------------- #

def test_missing_required_monthly_sma_fails_gate():
    # 6-month-old IPO: no 10m/20m SMAs -> gate must fail, not pass vacuously.
    state, _ = entry_step(None, snap(monthly={}), TODAY)
    assert state["gate"] is False and state["setup_live"] is False


def test_60m_never_required_and_never_a_trigger_leg():
    # Below the 60m SMA: gate passes anyway (60m is context, not a gate).
    below_60m = {"10": True, "20": True, "60": False}
    state = seed_entry(snap(monthly=below_60m), TODAY)
    assert state["gate"] is True and state["setup_live"] is True

    # A 60m reclaim on its own is a non-event (the TGT case, post-change).
    state2, events = entry_step(state, snap(daily=ALL_ABOVE), TODAY)
    assert events == []

    # And when the gate DOES complete via the 20m, the 60m still below
    # doesn't block, and the leg names only the 20m.
    state = seed_entry(
        snap(monthly={"10": True, "20": False, "60": False}, daily=BELOW_10), TODAY
    )
    assert state["gate"] is False
    _, events = entry_step(state, snap(monthly=below_60m, daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["legs"] == ["monthly gate completed (20m reclaim)"]


def test_missing_60_is_skippable_everywhere():
    s = snap(
        monthly={"10": True, "20": True},
        weekly={"10": True, "20": True},
        daily={"10": True, "20": True},
    )
    state = seed_entry(s, TODAY)
    assert state["gate"] and state["weekly_all"] and state["setup_live"]


# --------------------------------------------------------------------------- #
# 60m proximity deferral (HRL rule)
# --------------------------------------------------------------------------- #

# Price $100, 60m SMA $127 -> 27% below (HRL-like, outside the 10% default).
FAR = {"m60": 127.0}
# Price $100, 60m SMA $106 -> 6% below (inside the 10% default).
NEAR = {"m60": 106.0}
BELOW_60M = {"10": True, "20": True, "60": False}


def test_far_below_60m_trigger_is_deferred_silently():
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR), TODAY)
    state, events = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    assert events == []
    assert state["phase"] == TRIGGERED and state["deferred"] is True
    assert state["last_trigger_week"] is None  # never announced
    assert state["last_trigger_legs"] == ["reclaimed 10wk SMA"]


def test_deferred_advances_to_signaled_silently_then_buy_fires_when_gap_closes():
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR), TODAY)
    state, _ = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    # Daily confirms while still far below: silent advance to SIGNALED.
    state, events = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=ALL_ABOVE), TODAY)
    assert events == []
    assert state["phase"] == SIGNALED and state["deferred"] is True
    # Gap closes inside the threshold: the held BUY fires with original legs.
    state, events = entry_step(state, snap(monthly=BELOW_60M, smas=NEAR, daily=ALL_ABOVE), TODAY)
    assert [e["type"] for e in events] == ["BUY"]
    assert events[0]["legs"] == ["reclaimed 10wk SMA"]
    assert state["deferred"] is False


def test_deferred_trigger_fires_into_watching_when_gap_closes():
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR), TODAY)
    state, _ = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    state, events = entry_step(state, snap(monthly=BELOW_60M, smas=NEAR, daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    assert events[0]["legs"] == ["reclaimed 10wk SMA"]
    assert state["phase"] == TRIGGERED and state["deferred"] is False


def test_crossing_the_60m_also_releases_the_deferral():
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR), TODAY)
    state, _ = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    state, events = entry_step(state, snap(smas=FAR, daily=BELOW_10), TODAY)  # 60m now True
    assert [e["type"] for e in events] == ["TRIGGER"]


def test_setup_break_clears_deferral():
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR), TODAY)
    state, _ = entry_step(state, snap(monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    state, events = entry_step(state, snap(weekly=BELOW_10, monthly=BELOW_60M, smas=FAR, daily=BELOW_10), TODAY)
    assert events == []
    assert state["phase"] == IDLE and state["deferred"] is False
    # Later gap-close with no live setup fires nothing.
    state, events = entry_step(state, snap(weekly=BELOW_10, monthly=BELOW_60M, smas=NEAR, daily=BELOW_10), TODAY)
    assert events == []


def test_threshold_arg_respected_and_above_or_missing_60m_unaffected():
    # 6% below fires under the default 10% but defers under a strict 3%.
    state = seed_entry(snap(weekly=BELOW_10, monthly=BELOW_60M, smas=NEAR), TODAY)
    s2, events = entry_step(state, snap(monthly=BELOW_60M, smas=NEAR, daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]
    s3, events = entry_step(state, snap(monthly=BELOW_60M, smas=NEAR, daily=BELOW_10), TODAY, m60_prox_pct=3.0)
    assert events == [] and s3["deferred"] is True
    # No 60m SMA at all (young ticker): never deferred.
    young = {"10": True, "20": True}
    state = seed_entry(snap(weekly=BELOW_10, monthly=young), TODAY)
    _, events = entry_step(state, snap(monthly=young, daily=BELOW_10), TODAY)
    assert [e["type"] for e in events] == ["TRIGGER"]


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
