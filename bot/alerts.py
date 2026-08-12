"""Alert message formatting. Pure strings -- no Discord objects here, so the
scan and its dry-run mode never need discord.py installed."""

from bot import sectors

DISCORD_LIMIT = 1990  # a hair under the 2000-char message cap


def _tent(event):
    pending = event.get("pending")
    if pending:
        return " *(tentative — " + "; ".join(pending) + ")*"
    return " *(tentative)*" if event.get("tentative") else ""


def buy_waits(snap, event):
    """Inline tokens for what a BUY still awaits (unfinished bars only --
    the 60m is context, not a wait). Empty = firm signal."""
    return [p for p in event.get("pending") or [] if p.startswith("pending ")]


def _m60(snap):
    """60-month SMA context ('nice to have' -- shown, never required). When
    below, include the gap so you can see how close the reclaim is."""
    v = (snap.get("monthly_above") or {}).get("60")
    if v is None:
        return None  # young ticker: no 60m SMA to speak of
    if v:
        return "60m ✓"
    m60 = (snap.get("smas") or {}).get("m60")
    px = snap.get("daily_close")
    if m60 and px:
        gap = (m60 - px) / px * 100.0
        return f"60m ✗ ({gap:.1f}% below)"
    return "60m ✗"


def _w60(snap):
    """60-week SMA context (like the 60m: shown, never required)."""
    v = (snap.get("weekly_above") or {}).get("60")
    if v is None:
        return None
    return "60w ✓" if v else "60w ✗"


def _dd(snap):
    """Drawdown-episode context: how deep the crash went and where the
    recovery stands (e.g. '−52% max · 32% off high')."""
    dd = snap.get("drawdown") or {}
    ep, off = dd.get("episode_dd_pct"), dd.get("off_high_pct")
    if ep is None or off is None:
        return None
    return f"−{ep:.0f}% max · {off:.0f}% off high"


def _line(ticker, snap, legs, waits=None, with_m60=False):
    parts = [", ".join(legs) if legs else "setup live"]
    if with_m60:
        for ctx in (_dd(snap), _w60(snap), _m60(snap)):
            if ctx:
                parts.append(ctx)
    if waits:
        parts.extend(waits)
    return f"**{ticker}** ${snap['daily_close']:.2f} — " + " · ".join(parts)


def _grouped_lines(entries, line_fn, cats):
    """Render entries grouped under emoji sector headers, Tech first. If no
    entry has a known category, skip the headers (flat list)."""
    groups = {}
    for entry in entries:
        cat = (cats or {}).get(entry[0], "Unknown")
        groups.setdefault(cat, []).append(f"• {line_fn(entry)}")
    if set(groups) == {"Unknown"}:
        return [line for lines in groups.values() for line in lines]
    out = []
    for cat in sorted(groups, key=sectors.sort_key):
        out.append(f"{sectors.emoji(cat)} {cat}")
        out.extend(groups[cat])
    return out


WINDOW_LABELS = {"1M": "1-month", "3M": "3-month", "6M": "6-month"}


def _exp_watch_line(ticker, snap, event):
    off = event.get("off_pct")
    label = WINDOW_LABELS.get(event["window"], event["window"])
    return (
        f"• **{ticker}** ${snap['daily_close']:.2f} — "
        f"{off:.0f}% off its {label} high"
    )


def _exp_alert_line(ticker, snap, event):
    trough = event.get("trough")
    px = snap["daily_close"]
    parts = [f"crashed {event.get('case_opened', '?')}"]
    if trough:
        parts.append(f"trough ${trough:.2f}")
        parts.append(f"{(px - trough) / trough * 100:+.1f}% off trough")
    return f"• **{ticker}** ${px:.2f} — " + " · ".join(parts)


def scan_report(buys, watching, cats=None, exp_watch=None, exp_alerts=None):
    """One message per scan. Live-strategy sections first (mutually
    exclusive, sector-grouped, Tech first), then the crash-recovery
    experiment sections, each test under its own header (user 2026-08-11:
    no generic "Experiments" umbrella). Empty sections are omitted; nothing
    at all -> None."""
    sections = []
    if buys:
        lines = _grouped_lines(
            buys, lambda e: _line(e[0], e[1], e[2], e[3], with_m60=True), cats
        )
        sections.append("✅ **BUY:**\n" + "\n".join(lines))
    if watching:
        lines = _grouped_lines(watching, lambda e: _line(e[0], e[1], e[2]), cats)
        sections.append("👀 **Setup complete — watching daily confirm:**\n" + "\n".join(lines))
    for window in ("1M", "3M", "6M"):
        entries = (exp_alerts or {}).get(window)
        if not entries:
            continue
        label = WINDOW_LABELS.get(window, window)
        sections.append(
            f"🧪 **Crashed 30% off {label} high — closed above 10wk MA today:**\n"
            + "\n".join(_exp_alert_line(t, s, e) for t, s, e in entries)
        )
    if exp_watch:
        sections.append(
            "🔍 **Now watching (fresh 30% crash):**\n"
            + "\n".join(_exp_watch_line(t, s, e) for t, s, e in exp_watch)
        )
    return "\n\n".join(sections) if sections else None


def warning_message(ticker, snap, pos):
    entry = f" (entry ${pos['entry_price']:.2f})" if pos.get("entry_price") else ""
    return (
        f"⚠️ **WARNING — {ticker}** ${snap['daily_close']:.2f}{entry}\n"
        f"Daily close below the 10-day SMA (${snap['smas'].get('d10', 0):.2f}). "
        f"SELL line is a weekly close below the 5-week SMA "
        f"(${snap['smas'].get('w5', 0):.2f})."
    )


def sell_message(ticker, snap, pos, event):
    entry = pos.get("entry_price")
    pnl = ""
    if entry:
        pct = (snap["daily_close"] - entry) / entry * 100
        pnl = f" · entry ${entry:.2f} → {pct:+.1f}%"
    return (
        f"🔻 **SELL — {ticker}** ${snap['daily_close']:.2f}{_tent(event)}\n"
        f"Weekly close below the 5-week SMA (${snap['smas'].get('w5', 0):.2f})"
        f"{pnl}"
    )


def chunk(text, limit=DISCORD_LIMIT):
    """Split a message on line boundaries to fit Discord's length cap."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks
