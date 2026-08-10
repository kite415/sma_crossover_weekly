"""Configuration from environment variables (.env supported via python-dotenv)."""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional; env vars alone work fine
    pass


def _require(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"ERROR: required env var {name} is not set (see .env.example)")
    return val


class Config:
    def __init__(self):
        self.token = _require("DISCORD_TOKEN")
        self.guild_id = int(_require("GUILD_ID"))
        self.alert_channel_id = int(_require("ALERT_CHANNEL_ID"))
        self.confirm_mode = (os.environ.get("CONFIRM_MODE") or "live").strip().lower()
        if self.confirm_mode not in ("live", "close"):
            raise SystemExit("ERROR: CONFIRM_MODE must be 'live' or 'close'")
        self.db_path = os.environ.get("DB_PATH", "data/bot.db")
        # Below-60m signals are deferred until price is within this percent
        # of the 60-month SMA (see README "proximity rule").
        self.m60_prox_pct = float(os.environ.get("M60_PROXIMITY_PCT", "10"))
        # Drawdown-episode arm: a ticker is only eligible to trigger while it
        # is recovering from a decline of at least this percent off its high
        # (see README "drawdown arm").
        self.dd_arm_pct = float(os.environ.get("DD_ARM_PCT", "30"))
        # Daily scan time (America/New_York). 16:10 ET is right after the
        # 4pm close; official closing prices settle within a few minutes.
        self.scan_hour = int(os.environ.get("SCAN_HOUR", "16"))
        self.scan_minute = int(os.environ.get("SCAN_MINUTE", "10"))
