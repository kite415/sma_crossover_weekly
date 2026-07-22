"""Alert message formatting. Pure strings -- no Discord objects here, so the
scan and its dry-run mode never need discord.py installed."""

DISCORD_LIMIT = 1990  # a hair under the 2000-char message cap


def _tent(event):
    pending = event.get("pending")
    if pending:
        return " *(tentative — " + "; ".join(pending) + ")*"
    return " *(tentative)*" if event.get("tentative") else ""


def buy_waits(snap, event):
    """Inline tokens for what a BUY still awaits (unfinished bars only --
    the 60m is context, not a wait). Empty = firm signal."""
    waits = []
    for p in event.get("pending") or []:
        if p.startswith("monthly gate"):
            waits.append("pending month close (gate)")
        elif p.startswith("pending "):
            waits.append(p)  # "pending Fri Jul 24 close"
    return waits


def _m60(snap):
    """60-month SMA context ('nice to have' -- shown, never required)."""
    v = (snap.get("monthly_above") or {}).get("60")
    if v is None:
        return None  # young ticker: no 60m SMA to speak of
    return "60m ✓" if v else "60m ✗"


def _line(ticker, snap, legs, waits=None, with_m60=False):
    parts = [", ".join(legs) if legs else "setup live"]
    if with_m60 and _m60(snap):
        parts.append(_m60(snap))
    if waits:
        parts.extend(waits)
    return f"**{ticker}** ${snap['daily_close']:.2f} — " + " · ".join(parts)


def scan_report(buys, watching):
    """One message per scan, two mutually exclusive sections. buys entries
    are (ticker, snap, legs, waits); watching entries (ticker, snap, legs).
    Empty sections are omitted; both empty -> None (nothing to post)."""
    sections = []
    if buys:
        sections.append(
            "✅ **BUY:**\n"
            + "\n".join(
                f"• {_line(t, s, legs, waits, with_m60=True)}"
                for t, s, legs, waits in buys
            )
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
