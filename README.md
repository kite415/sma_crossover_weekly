# SMA Multi-Timeframe Scanner — Discord Bot

A self-hosted Discord bot that scans the **S&P 500 + S&P 400 + your personal
watchlist** (~900 tickers) every trading day after the close, looking for
stocks completing a three-timeframe momentum setup, and tracks the positions
you actually hold for exit alerts.

## The strategy

Three timeframes, three roles:

| Timeframe | Role | Condition |
|---|---|---|
| **Monthly** | Regime gate | close above the 10/20/60-month SMAs |
| **Weekly** | Trigger | close above the 10/20/60-week SMAs **and** the 5-week SMA |
| **Daily** | Entry confirm | close above the 10/20/60-day SMAs |

A setup is **live** when the monthly gate holds and the weekly close is above
all four weekly SMAs. The scanner alerts on *transitions*, not conditions:

```
            trigger (setup completes)        daily confirm
  IDLE ────────────────────────▶ TRIGGERED ────────────────▶ SIGNALED
    ▲            📢 digest                       ✅ BUY          │
    └────────────────── weekly close < 5wk SMA (silent) ◀───────┘
```

- **Trigger** — the setup goes from not-live to live, whatever leg completed
  last: a 10wk reclaim (pullback resuming), a 60wk reclaim (recovery
  breakout), the monthly gate completing, or a 5wk reclaim (continuation
  after a shakeout). The alert names the leg.
- **BUY** — a triggered ticker's daily close is above all three daily SMAs
  (often the same evening as the trigger).
- **Reset** — a weekly close below the 5-week SMA silently ends the setup;
  a later reclaim (everything else still holding) is a fresh trigger.

An SMA with insufficient history is skipped (the 10/20 must exist; the 60 is
optional so young tickers still qualify). New tickers seed silently — no
alert blast for setups that completed long ago; only *new* events fire. A
persistently strong stock therefore stays quiet until its first real
5wk-or-deeper pullback resolves — that's by design.

## Positions & alert routing

Log what you actually buy with `/buy` — that ticker joins the **exit
engine**:

| Alert | Condition | Who gets it |
|---|---|---|
| 📢 Setup digest | new triggers, one message per scan | everything scanned |
| ✅ BUY | daily confirm after a trigger | everything scanned — **muted while you hold the ticker** (unmutes after a SELL alert or `/sell`) |
| ⚠️ WARNING | daily close below the 10-day SMA, once per dip | held positions only |
| 🔻 SELL | weekly close below the 5-week SMA | held positions only |

## Slash commands

- `/buy <ticker> <price> [qty]` — log a position (starts exit tracking)
- `/sell <ticker> [price]` — close it (prints P&L, unmutes BUY signals)
- `/positions` — open positions with last-scan price and P&L
- `/status <ticker>` — fresh three-timeframe check for any symbol
- `/watchlist add | remove | list` — personal tickers beyond the indices
- `/scan` — run a scan on demand

## Configuration (`.env`)

| Var | Meaning |
|---|---|
| `DISCORD_TOKEN` / `GUILD_ID` / `ALERT_CHANNEL_ID` | see [SETUP.md](SETUP.md) |
| `CONFIRM_MODE` | `live` (default): evaluate the in-progress weekly/monthly bar, tagging alerts *(tentative)*. `close`: completed bars only. The daily bar is always final on scheduled scans (they run after the close); a manual midday `/scan` evaluates the intraday price. |
| `SCAN_HOUR` / `SCAN_MINUTE` | scan time, America/New_York (default 17:30 Mon–Fri) |
| `DB_PATH` | SQLite location (the docker volume handles this) |

## Running it

See **[SETUP.md](SETUP.md)** for the full walkthrough (Discord app creation →
`.env` → `docker compose up -d`, plus Raspberry Pi / bare-Mac notes).

Dry-run a scan without Discord at all:

```bash
python -m bot.scan --dry-run --db data/bot.db            # full universe
python -m bot.scan --dry-run --tickers NVDA,HIMS         # quick subset
```

Tests (the state machine is pure and fully covered):

```bash
pytest tests/
```

## Notes

- Data comes from Yahoo via `yfinance` (no API key), dividend-adjusted.
- The universe refreshes from Wikipedia weekly and falls back to its cached
  copy if the scrape fails. A ticker leaving the index is dropped silently —
  unless you hold it, in which case it stays tracked until you `/sell`.
- The monthly gate is entry-only: once you're in a position, exits are the
  10-day warning and the 5-week SELL, never the gate.
- One data hiccup never ejects state: a ticker with no data this scan keeps
  yesterday's state untouched.
