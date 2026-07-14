"""
Bot entrypoint: Discord client + the daily scan scheduler.

Run with: python -m bot.main  (or via docker compose up -d)
"""

import asyncio
import logging

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import alerts, commands as bot_commands, db
from bot.config import Config
from bot.scan import run_scan

log = logging.getLogger("sma-bot")


class ScannerBot(discord.Client):
    def __init__(self, cfg):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.conn = db.connect(cfg.db_path)
        self.tree = discord.app_commands.CommandTree(self)
        self.scheduler = AsyncIOScheduler()
        self._scan_lock = asyncio.Lock()
        bot_commands.register(self.tree, self.conn, cfg, self.run_scan_and_post)

    async def setup_hook(self):
        await self.tree.sync(guild=discord.Object(id=self.cfg.guild_id))
        self.scheduler.add_job(
            self.run_scan_and_post,
            CronTrigger(
                day_of_week="mon-fri",
                hour=self.cfg.scan_hour,
                minute=self.cfg.scan_minute,
                timezone="America/New_York",
            ),
            name="daily-scan",
        )
        self.scheduler.start()
        log.info(
            "scheduled daily scan %02d:%02d America/New_York Mon-Fri",
            self.cfg.scan_hour, self.cfg.scan_minute,
        )

    async def on_ready(self):
        log.info("logged in as %s; alert channel %s", self.user, self.cfg.alert_channel_id)

    async def run_scan_and_post(self, manual=False):
        if self._scan_lock.locked():
            log.info("scan already running; skipping")
            return
        async with self._scan_lock:
            channel = self.get_channel(self.cfg.alert_channel_id) or await self.fetch_channel(
                self.cfg.alert_channel_id
            )
            def scan_with_own_conn():
                # sqlite3 connections are single-thread; the scan runs in a
                # worker thread, so it gets its own connection (WAL mode lets
                # it coexist with the command handlers on the main thread).
                conn = db.connect(self.cfg.db_path)
                try:
                    return run_scan(conn, self.cfg.confirm_mode)
                finally:
                    conn.close()

            try:
                result = await asyncio.to_thread(scan_with_own_conn)
            except Exception:
                log.exception("scan failed")
                await channel.send("💥 Scan failed — check the bot logs.")
                return

            for line in result.log:
                log.info(line)

            sent = 0
            if result.digest:
                for part in alerts.chunk(result.digest):
                    await channel.send(part)
                    sent += 1
            for msg in result.messages:
                for part in alerts.chunk(msg):
                    await channel.send(part)
                    sent += 1
            if manual and sent == 0:
                s = result.stats
                await channel.send(
                    f"Scan done — no new signals. "
                    f"({s['with_data']}/{s['universe']} tickers, "
                    f"{s['positions']} position(s) tracked, {s['seeded']} newly seeded)"
                )


def seed_watchlist_from_file(conn, path="watchlist.txt"):
    """One-time migration: import the legacy watchlist.txt into the DB."""
    import os

    if db.watchlist_all(conn) or not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                db.watchlist_add(conn, line.upper())
    log.info("seeded watchlist from %s: %s", path, db.watchlist_all(conn))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cfg = Config()
    bot = ScannerBot(cfg)
    seed_watchlist_from_file(bot.conn)
    bot.run(cfg.token, log_handler=None)


if __name__ == "__main__":
    main()
