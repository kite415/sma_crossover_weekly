"""Slash commands: /buy /sell /positions /status /watchlist /scan."""

import asyncio
from typing import Optional

import discord
from discord import app_commands

from bot import db, sectors, universe
from bot.data import build_snapshot, fetch_closes
from bot.engine import all_above, gate_ok


def _norm(ticker):
    return universe.normalize(ticker)


def _yes(flag):
    return "✅" if flag else "❌"


def _fmt_timeframe(name, flags, above5=None):
    parts = [f"{k}: {_yes(v)}" for k, v in sorted(flags.items(), key=lambda kv: int(kv[0]))]
    if above5 is not None:
        parts.insert(0, f"5: {_yes(above5)}")
    return f"**{name}** — " + ("  ".join(parts) if parts else "insufficient history")


async def _validate_ticker(ticker):
    """True if yfinance returns any price data (runs in a thread)."""
    def check():
        closes = fetch_closes([ticker])
        return ticker in closes
    return await asyncio.to_thread(check)


def register(tree, conn, cfg, run_scan_and_post):
    """Attach all commands to the bot's CommandTree."""
    guild = discord.Object(id=cfg.guild_id)

    # ------------------------------------------------------------------ buy
    @tree.command(name="buy", description="Log a position you bought (starts exit tracking, mutes BUY signals)", guild=guild)
    @app_commands.describe(ticker="Ticker symbol", price="Your entry price", qty="Share count (optional)")
    async def buy(interaction: discord.Interaction, ticker: str, price: float, qty: Optional[float] = None):
        ticker = _norm(ticker)
        if db.get_open_position(conn, ticker):
            await interaction.response.send_message(
                f"You already hold **{ticker}** — `/sell {ticker}` first if you closed it.", ephemeral=True)
            return
        await interaction.response.defer()
        if not await _validate_ticker(ticker):
            await interaction.followup.send(f"❌ **{ticker}** returned no price data — is the symbol right?")
            return
        db.open_position(conn, ticker, price, qty)
        qty_txt = f" × {qty:g}" if qty else ""
        await interaction.followup.send(
            f"📒 Logged **{ticker}**{qty_txt} @ ${price:.2f}. Exit tracking starts next scan "
            f"(10-day warnings + 5-week SELL). BUY signals for {ticker} are muted while held.")

    # ----------------------------------------------------------------- sell
    @tree.command(name="sell", description="Close a logged position (stops exit tracking, unmutes BUY signals)", guild=guild)
    @app_commands.describe(ticker="Ticker symbol", price="Your exit price (optional)")
    async def sell(interaction: discord.Interaction, ticker: str, price: Optional[float] = None):
        ticker = _norm(ticker)
        row = db.close_position(conn, ticker, price)
        if row is None:
            await interaction.response.send_message(f"No open position in **{ticker}**.", ephemeral=True)
            return
        pnl = ""
        if price and row["entry_price"]:
            pct = (price - row["entry_price"]) / row["entry_price"] * 100
            pnl = f" · ${row['entry_price']:.2f} → ${price:.2f} = **{pct:+.1f}%**"
        await interaction.response.send_message(
            f"📕 Closed **{ticker}**{pnl}. BUY signals for {ticker} are live again.")

    # ------------------------------------------------------------ positions
    @tree.command(name="positions", description="Show open positions with last-scan prices", guild=guild)
    async def positions(interaction: discord.Interaction):
        rows = db.get_open_positions(conn)
        if not rows:
            await interaction.response.send_message("No open positions. Log one with `/buy`.")
            return
        lines = []
        for row in rows:
            state = db.get_ticker_state(conn, row["ticker"]) or {}
            last = state.get("daily_close")
            cur = f"${last:.2f}" if last else "n/a"
            pnl = ""
            if last and row["entry_price"]:
                pct = (last - row["entry_price"]) / row["entry_price"] * 100
                pnl = f" ({pct:+.1f}%)"
            warn = "" if row["warn_armed"] in (1, None) else " · ⚠️ below 10d"
            qty_txt = f" × {row['qty']:g}" if row["qty"] else ""
            lines.append(
                f"**{row['ticker']}** — entry ${row['entry_price']:.2f}{qty_txt}"
                f" · last {cur}{pnl}{warn} · since {row['opened_at'][:10]}"
            )
        await interaction.response.send_message("\n".join(lines))

    # --------------------------------------------------------------- status
    @tree.command(name="status", description="All three timeframes + phase for a ticker (fresh data)", guild=guild)
    @app_commands.describe(ticker="Ticker symbol")
    async def status(interaction: discord.Interaction, ticker: str):
        ticker = _norm(ticker)
        await interaction.response.defer()

        def fetch_snap():
            closes = fetch_closes([ticker])
            if ticker not in closes:
                return None
            return build_snapshot(ticker, closes[ticker], mode=cfg.confirm_mode)

        snap = await asyncio.to_thread(fetch_snap)
        if snap is None:
            await interaction.followup.send(f"❌ No data for **{ticker}**.")
            return
        state = db.get_ticker_state(conn, ticker)
        phase = state["phase"] if state else "(not scanned yet)"
        gate = gate_ok(snap["monthly_above"])
        weekly_all = all_above(snap["weekly_above"])
        live = gate and weekly_all
        held = db.get_open_position(conn, ticker) is not None
        # DB-only sector lookup (fetch_missing=False keeps it off-network and
        # safe on the event loop); populated for extras by the first scan.
        cat = sectors.category(ticker, universe.sector_of(conn, ticker, fetch_missing=False))
        cat_txt = f" · {sectors.emoji(cat)} {cat}" if cat != "Unknown" else ""
        lines = [
            f"**{ticker}** ${snap['daily_close']:.2f} · phase **{phase}**"
            + cat_txt + (" · 📒 held" if held else ""),
            _fmt_timeframe("Monthly", snap["monthly_above"]),
            _fmt_timeframe("Weekly", snap["weekly_above"], above5=snap["above_5w"]),
            _fmt_timeframe("Daily", snap["daily_above"]),
            f"Setup live: {_yes(live)} (gate {_yes(gate)} · weekly {_yes(weekly_all)}) · "
            f"5wk exit line: {_yes(bool(snap['above_5w']))}",
        ]
        await interaction.followup.send("\n".join(lines))

    # ------------------------------------------------------------ watchlist
    wl = app_commands.Group(name="watchlist", description="Personal tickers beyond the S&P 500/400", guild_ids=[cfg.guild_id])

    @wl.command(name="add", description="Add a ticker to the personal watchlist")
    @app_commands.describe(ticker="Ticker symbol")
    async def wl_add(interaction: discord.Interaction, ticker: str):
        ticker = _norm(ticker)
        await interaction.response.defer()
        if not await _validate_ticker(ticker):
            await interaction.followup.send(f"❌ **{ticker}** returned no price data — is the symbol right?")
            return
        if db.watchlist_add(conn, ticker):
            await interaction.followup.send(
                f"➕ **{ticker}** added. It seeds silently on the next scan, then alerts like any other name.")
        else:
            await interaction.followup.send(f"**{ticker}** is already on the watchlist.")

    @wl.command(name="remove", description="Remove a ticker from the personal watchlist")
    @app_commands.describe(ticker="Ticker symbol")
    async def wl_remove(interaction: discord.Interaction, ticker: str):
        ticker = _norm(ticker)
        if not db.watchlist_remove(conn, ticker):
            await interaction.response.send_message(
                f"**{ticker}** isn't on the personal watchlist. (S&P 500/400 members are in by index rule "
                f"and can't be removed.)", ephemeral=True)
            return
        note = ""
        if db.get_open_position(conn, ticker):
            note = f" You still hold {ticker}, so exit tracking continues until you `/sell`."
        await interaction.response.send_message(f"➖ **{ticker}** removed from the watchlist.{note}")

    @wl.command(name="list", description="Show the personal watchlist")
    async def wl_list(interaction: discord.Interaction):
        tickers = db.watchlist_all(conn)
        msg = ", ".join(f"**{t}**" for t in tickers) if tickers else "(empty — S&P 500/400 are always scanned)"
        await interaction.response.send_message(f"Personal watchlist: {msg}")

    tree.add_command(wl)

    # ----------------------------------------------------------------- scan
    @tree.command(name="scan", description="Run a scan right now (a few minutes)", guild=guild)
    async def scan_cmd(interaction: discord.Interaction):
        await interaction.response.send_message("🔍 Scanning ~900 tickers — results post here in a few minutes…")
        await run_scan_and_post(manual=True)
