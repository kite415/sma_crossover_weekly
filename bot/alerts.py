"""Alert message formatting. Pure strings -- no Discord objects here, so the
scan and its dry-run mode never need discord.py installed."""

DISCORD_LIMIT = 1990  # a hair under the 2000-char message cap


def _tent(event):
    return " *(tentative)*" if event.get("tentative") else ""


def _sma_line(snap, prefix, keys=("10", "20", "60")):
    vals = snap.get("smas") or {}
    parts = [
        f"{k}{prefix[0]} ${vals[f'{prefix[0]}{k}']:.2f}"
        for k in keys
        if f"{prefix[0]}{k}" in vals
    ]
    return " / ".join(parts)


def digest_line(ticker, snap, event):
    legs = ", ".join(event["legs"])
    return f"**{ticker}** ${snap['daily_close']:.2f} — {legs}{_tent(event)}"


def digest_message(lines):
    return "📢 **New setups** — monthly gate + weekly 10/20/60 complete:\n" + "\n".join(
        f"• {line}" for line in lines
    )


def buy_message(ticker, snap, event):
    return (
        f"✅ **BUY — {ticker}** ${snap['daily_close']:.2f}{_tent(event)}\n"
        f"Daily close above all daily SMAs ({_sma_line(snap, 'daily')}).\n"
        f"Weekly: {_sma_line(snap, 'weekly')} · 5w ${snap['smas'].get('w5', 0):.2f}"
    )


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
