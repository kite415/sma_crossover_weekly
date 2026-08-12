"""
Crash-recovery experiments: pure state machine, engine.py style. No I/O --
(previous case, snapshot) -> (new case, events); fully covered in
tests/test_experiments.py.

Design (user, 2026-08-11)
-------------------------
A CASE opens when a ticker's daily close is >= CRASH_PCT (30%) below its
highest close of the trailing 1M/3M/6M window, tagged by the FASTEST window
satisfied (1M within 3M within 6M -> exclusive tags: violent / medium /
slow-grind). One active case per ticker; the trough is tracked; the tag may
upgrade to a faster window until any entry fires.

Five ENTRY RULES are stamped per case, each at most once (this is the repeat
control -- whipsaws around the MA become data, not alerts):
  daily-cross   first daily close > 10wk MA          <- the ONLY alerting rule
  weekly-cross  first completed Friday close > 10wk MA
  confirm-2     2nd consecutive Friday close > 10wk MA
  confirm-3     3rd consecutive Friday close > 10wk MA (a close below resets)
  dual-ma       first daily close > both 10wk AND 20wk MAs
The case archives (state -> None) once every rule has fired; the rolling
crash reference decays with time, so a genuinely new crash re-opens later.

Forward-only by design: no backtesting (user rationale: strategies are
regime-dependent; they want evidence for what works now). The scoreboard
lives in bot/dashboard.py; signals in the experiment_signals table.
"""

from bot.data import CRASH_PCT

WINDOWS = ("1M", "3M", "6M")  # fastest first; tag = first satisfied
ENTRY_RULES = ("daily-cross", "weekly-cross", "confirm-2", "confirm-3", "dual-ma")
STREAK_RULES = {"weekly-cross": 1, "confirm-2": 2, "confirm-3": 3}


def fastest_window(crash, pct=CRASH_PCT):
    """The fastest window whose off-high percent meets the crash threshold.
    `crash` is snapshot["crash"]: {"1M": pct_below_rolling_high, ...}."""
    for name in WINDOWS:
        if (crash or {}).get(name, 0.0) >= pct:
            return name
    return None


def open_case(snap, today):
    return {
        "opened": today,
        "window": fastest_window(snap.get("crash")),
        "trough": snap.get("daily_close"),
        "streak": 0,
        "last_conf_week": snap["bar_dates"].get("weekly_confirmed"),
        "fired": {},
    }


def exp_step(prev, snap, today):
    """
    Advance one ticker's experiment machine by one scan.

    prev: the active case dict, or None (idle -- no case).
    Returns (new_case_or_None, events); events are
      {"type": "OPEN", "window", "off_pct"} or
      {"type": "ENTRY", "rule", "window", "alert": bool, "price",
       "case_opened", "trough"}.
    Only daily-cross carries alert=True; everything else is scoreboard-only.
    """
    events = []
    if prev is None:
        window = fastest_window(snap.get("crash"))
        if window is None:
            return None, []
        prev = open_case(snap, today)
        events.append(
            {"type": "OPEN", "window": window,
             "off_pct": snap["crash"][window]}
        )
        # fall through: a same-scan daily-cross (violent-crash edge) may fire.

    case = dict(prev)
    case["fired"] = dict(prev.get("fired") or {})
    px = snap.get("daily_close")
    if px is not None and (case.get("trough") is None or px < case["trough"]):
        case["trough"] = px

    # Tag upgrade: a slow-grind case that turns violent is re-tagged until
    # the first entry fires (after that the tag is part of recorded signals).
    if not case["fired"]:
        faster = fastest_window(snap.get("crash"))
        if faster and WINDOWS.index(faster) < WINDOWS.index(case["window"]):
            case["window"] = faster

    def fire(rule, alert=False):
        case["fired"][rule] = {"date": today, "price": px}
        events.append(
            {"type": "ENTRY", "rule": rule, "window": case["window"],
             "alert": alert, "price": px, "case_opened": case["opened"],
             "trough": case["trough"]}
        )

    # Daily rules: the live weekly flags ARE "today's close vs the MA line"
    # (the in-progress weekly bar closes at today's price).
    d10 = (snap.get("weekly_above") or {}).get("10") is True
    d20 = (snap.get("weekly_above") or {}).get("20") is True
    if d10 and "daily-cross" not in case["fired"]:
        fire("daily-cross", alert=True)
    if d10 and d20 and "dual-ma" not in case["fired"]:
        fire("dual-ma")

    # Weekly rules advance only when a NEW completed week appears -- mid-week
    # scans are weekly no-ops, so alerts stay stable whatever price does
    # intraday. (After downtime a multi-week gap advances the streak by one:
    # conservative, never fabricates consecutive closes.)
    conf_week = snap["bar_dates"].get("weekly_confirmed")
    if conf_week and conf_week != case.get("last_conf_week"):
        case["last_conf_week"] = conf_week
        above = (snap.get("weekly_above_confirmed") or {}).get("10") is True
        case["streak"] = case.get("streak", 0) + 1 if above else 0
        for rule, need in STREAK_RULES.items():
            if case["streak"] >= need and rule not in case["fired"]:
                fire(rule)

    if all(rule in case["fired"] for rule in ENTRY_RULES):
        return None, events  # every entry stamped: case archived
    return case, events
