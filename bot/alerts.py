"""Alert message formatting. Pure strings -- no Discord objects here, so the
scan and its dry-run mode never need discord.py installed."""

DISCORD_LIMIT = 1990  # a hair under the 2000-char message cap


def _tent(event):
    pending = event.get("pending")
    if pending:
        return " *(tentative — " + "; ".join(pending) + ")*"
    return " *(tentative)*" if event.get("tentative") else ""


def buy_waits(snap, event):
    """Short tokens naming everything this BUY is still waiting on. Empty =
    firm. The 60m ('nice to have') counts as a wait only when it exists and
    is below -- a young ticker with no 60m SMA has nothing to wait for."""
    waits = []
    for p in event.get("pending") or []:
        if p.startswith("monthly gate"):
            waits.append("month close (gate)")
        elif p.startswith("pending ") and p.endswith(" close"):
            waits.append(p[len("pending "):])  # "Fri Jul 24 close"
    if (snap.get("monthly_above") or {}).get("60") is False:
        waits.append("60m ✗")
    return waits


def _line(ticker, snap, legs, waits=None):
    parts = [", ".join(legs) if legs else "setup live"]
    if waits:
        parts.append(" · ".join(waits))
    return f"**{ticker}** ${snap['daily_close']:.2f} — " + " · ".join(parts)


def scan_report(firm, waiting, watching):
    """One message per scan, three mutually exclusive sections (each entry is
    (ticker, snap, legs[, waits])). Empty sections are omitted; all empty ->
    None (nothing to post)."""
    sections = []
    if firm:
        sections.append(
            "✅ **BUY — fully confirmed (incl. 60m):**\n"
            + "\n".join(f"• {_line(t, s, legs)}" for t, s, legs in firm)
        )
    if waiting:
        sections.append(
            "🕒 **BUY — waiting on:**\n"
            + "\n".join(f"• {_line(t, s, legs, waits)}" for t, s, legs, waits in waiting)
        )
    if watching:
        sections.append(
            "👀 **Setup complete — watching daily confirm:**\n"
            + "\n".join(f"• {_line(t, s, legs)}" for t, s, legs in watching)
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
