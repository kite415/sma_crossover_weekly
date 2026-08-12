"""Crash-recovery experiment rules, pinned as tests (design of 2026-08-11)."""

import pandas as pd

from bot import db
from bot.data import build_snapshot, reconstruct_case
from bot.experiments import ENTRY_RULES, exp_step

TODAY = "2026-08-12"

CALM = {"1M": 5.0, "3M": 8.0, "6M": 12.0}          # no window at -30%
CRASH_1M = {"1M": 33.0, "3M": 38.0, "6M": 41.0}    # violent: all satisfied
CRASH_3M = {"1M": 12.0, "3M": 31.0, "6M": 35.0}    # medium
CRASH_6M = {"1M": 9.0, "3M": 22.0, "6M": 30.0}     # slow grind


def snap(
    crash=CRASH_3M,
    price=70.0,
    w10=False,               # today's close vs the live 10wk MA line
    w20=False,
    conf_week="2026-08-07",  # last completed weekly bar label
    conf_10=False,           # that completed week's close vs its 10wk SMA
):
    return {
        "ticker": "TEST",
        "daily_close": price,
        "crash": dict(crash),
        "weekly_above": {"10": w10, "20": w20},
        "weekly_above_confirmed": {"10": conf_10, "20": False},
        "bar_dates": {"daily": TODAY, "weekly": "2026-08-14",
                      "weekly_confirmed": conf_week},
    }


# --------------------------------------------------------------------------- #
# Case lifecycle
# --------------------------------------------------------------------------- #

def test_no_case_while_calm():
    state, events = exp_step(None, snap(crash=CALM), TODAY)
    assert state is None and events == []


def test_case_opens_on_crash_with_fastest_window_tag():
    state, events = exp_step(None, snap(crash=CRASH_1M), TODAY)
    assert state is not None and state["window"] == "1M"
    assert [e["type"] for e in events] == ["OPEN"]
    assert events[0]["window"] == "1M" and events[0]["off_pct"] == 33.0

    state, _ = exp_step(None, snap(crash=CRASH_6M), TODAY)
    assert state["window"] == "6M"


def test_tag_upgrades_until_first_entry_fires():
    state, _ = exp_step(None, snap(crash=CRASH_6M), TODAY)
    state, _ = exp_step(state, snap(crash=CRASH_3M), TODAY)
    assert state["window"] == "3M"  # decline accelerated: re-tagged
    # First entry fires -> tag frozen.
    state, events = exp_step(state, snap(crash=CRASH_3M, w10=True), TODAY)
    assert events and state["window"] == "3M"
    state, _ = exp_step(state, snap(crash=CRASH_1M), TODAY)
    assert state["window"] == "3M"  # no upgrade after a recorded signal


def test_trough_tracks_the_low():
    state, _ = exp_step(None, snap(price=70.0), TODAY)
    state, _ = exp_step(state, snap(price=61.5), TODAY)
    state, _ = exp_step(state, snap(price=66.0), TODAY)
    assert state["trough"] == 61.5


# --------------------------------------------------------------------------- #
# Daily rules
# --------------------------------------------------------------------------- #

def test_daily_cross_alerts_once():
    state, _ = exp_step(None, snap(), TODAY)
    state, events = exp_step(state, snap(w10=True, price=75.0), TODAY)
    assert [(e["type"], e["rule"], e["alert"]) for e in events] == [
        ("ENTRY", "daily-cross", True)
    ]
    assert events[0]["price"] == 75.0
    # Dip back under and cross again: silence (one fire per case).
    state, events = exp_step(state, snap(w10=False), TODAY)
    assert events == []
    state, events = exp_step(state, snap(w10=True), TODAY)
    assert events == []


def test_dual_ma_requires_both_and_is_silent():
    state, _ = exp_step(None, snap(), TODAY)
    state, events = exp_step(state, snap(w10=True), TODAY)
    assert [e["rule"] for e in events] == ["daily-cross"]  # 20wk still below
    state, events = exp_step(state, snap(w10=True, w20=True), TODAY)
    assert [(e["rule"], e["alert"]) for e in events] == [("dual-ma", False)]


def test_same_scan_open_and_daily_cross():
    # Violent-crash edge: 30% below the 1M high yet above the 10wk MA.
    state, events = exp_step(None, snap(crash=CRASH_1M, w10=True), TODAY)
    assert [e["type"] for e in events] == ["OPEN", "ENTRY"]
    assert events[1]["rule"] == "daily-cross" and events[1]["alert"]


# --------------------------------------------------------------------------- #
# Weekly rules (completed Fridays)
# --------------------------------------------------------------------------- #

def test_weekly_streak_fires_cross_confirm2_confirm3_on_successive_fridays():
    state, _ = exp_step(None, snap(), TODAY)  # AMD shape
    fired = []
    for week, above in [("2026-08-14", True), ("2026-08-21", True),
                        ("2026-08-28", True)]:
        state, events = exp_step(
            state, snap(conf_week=week, conf_10=above), TODAY
        )
        fired.append([e["rule"] for e in events])
    assert fired == [["weekly-cross"], ["confirm-2"], ["confirm-3"]]
    assert state["streak"] == 3


def test_streak_resets_on_a_friday_close_below():
    state, _ = exp_step(None, snap(), TODAY)
    state, _ = exp_step(state, snap(conf_week="2026-08-14", conf_10=True), TODAY)
    state, events = exp_step(
        state, snap(conf_week="2026-08-21", conf_10=False), TODAY
    )
    assert events == [] and state["streak"] == 0
    # Rebuild: confirm-2 needs two FRESH consecutive weeks.
    state, events = exp_step(
        state, snap(conf_week="2026-08-28", conf_10=True), TODAY
    )
    assert events == []  # weekly-cross already fired; streak back to 1
    state, events = exp_step(
        state, snap(conf_week="2026-09-04", conf_10=True), TODAY
    )
    assert [e["rule"] for e in events] == ["confirm-2"]


def test_midweek_scans_never_advance_weekly_rules():
    state, _ = exp_step(None, snap(), TODAY)
    state, _ = exp_step(state, snap(conf_week="2026-08-14", conf_10=True), TODAY)
    for _ in range(4):  # Mon-Thu rescans of the same completed week
        state, events = exp_step(
            state, snap(conf_week="2026-08-14", conf_10=True), TODAY
        )
        assert events == []
    assert state["streak"] == 1


# --------------------------------------------------------------------------- #
# Archive & re-crash
# --------------------------------------------------------------------------- #

def test_archive_after_all_rules_then_fresh_crash_reopens():
    state, _ = exp_step(None, snap(), TODAY)
    state, _ = exp_step(state, snap(w10=True, w20=True), TODAY)  # daily + dual
    for week in ("2026-08-14", "2026-08-21", "2026-08-28"):
        state, events = exp_step(
            state, snap(w10=True, w20=True, conf_week=week, conf_10=True), TODAY
        )
    assert state is None  # all five fired -> archived
    assert [e["rule"] for e in events] == ["confirm-3"]
    # Calm days stay idle; a new crash opens a fresh case with empty stamps.
    state, events = exp_step(None, snap(crash=CALM), TODAY)
    assert state is None
    state, events = exp_step(None, snap(crash=CRASH_1M), TODAY)
    assert state is not None and state["fired"] == {}
    assert [e["type"] for e in events] == ["OPEN"]


# --------------------------------------------------------------------------- #
# Snapshot crash facts + cold-start reconstruction
# --------------------------------------------------------------------------- #

def _series(values, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series([float(v) for v in values], index=idx)


def test_snapshot_crash_pcts_reflect_decline_speed():
    # Flat at 100, then a slow 100-day slide to 60: only the 6M window sees
    # a 30%+ drop; the 1M high has slid down with the price.
    vals = [100.0] * 200 + [100.0 - 0.4 * i for i in range(1, 101)]
    snap_ = build_snapshot("TEST", _series(vals))
    assert snap_["crash"]["6M"] >= 30.0
    assert snap_["crash"]["1M"] < 15.0


def test_reconstruct_case_finds_uncrossed_crash():
    # Crash from 100 to 62 in a few days, then hover in the low 60s: far
    # below the 10wk MA -> an open case with the crash-day trough.
    vals = [100.0] * 200 + [88.0, 76.0, 62.0] + [64.0] * 15
    case = reconstruct_case(_series(vals))
    assert case is not None
    assert case["window"] == "1M" and case["fired"] == {}
    assert case["trough"] == 62.0


def test_reconstruct_case_skips_crossed_recovery():
    # Same crash but price then recovers above the 10wk MA: the entries were
    # missed; never fabricate them.
    vals = [100.0] * 200 + [88.0, 76.0, 62.0] + [64.0] * 5 + [98.0] * 40
    assert reconstruct_case(_series(vals)) is None


def test_reconstruct_case_none_when_calm():
    assert reconstruct_case(_series([100.0] * 300)) is None


# --------------------------------------------------------------------------- #
# Dashboard smoke test
# --------------------------------------------------------------------------- #

def test_dashboard_renders_scoreboard_rows():
    from bot import dashboard

    conn = db.connect(":memory:")
    db.put_ticker_state(conn, "AAA", {"daily_close": 110.0})
    db.add_experiment_signal(conn, "1M", "daily-cross", "AAA", "2026-08-01",
                             100.0, "2026-07-20", 80.0)
    db.put_experiment_state(conn, "BBB", {
        "opened": "2026-08-05", "window": "3M", "trough": 55.0,
        "streak": 0, "last_conf_week": "2026-08-07", "fired": {},
    })
    html_out = dashboard.render(conn)
    assert "AAA" in html_out and "BBB" in html_out
    assert "+10.0%" in html_out          # 100 -> 110 marked to market
    assert "daily-cross" in html_out
    assert "Watching (1 open cases)" in html_out
