# SMA Multi-Timeframe Scanner — Discord Bot

A self-hosted Discord bot that scans the **S&P 500 + S&P 400 + your personal
watchlist** (~900 tickers) every trading day after the close, looking for
stocks completing a three-timeframe momentum setup, and tracks the positions
you actually hold for exit alerts.

## The strategy

Three timeframes, three roles:

| Timeframe | Role | Condition |
|---|---|---|
| **Monthly** | Regime gate | close above the 10- and 20-month SMAs (the 60-month is context only — shown on alerts as `60m ✓/✗`, never required) |
| **Weekly** | Trigger | close above the 10/20/60-week SMAs |
| **Daily** | Entry confirm | close above the 10/20/60-day SMAs |

A setup is **live** when the monthly gate holds and the weekly close is above
the three weekly SMAs. The scanner alerts on *transitions*, not conditions:

```
            trigger (setup completes)        daily confirm
  IDLE ────────────────────────▶ TRIGGERED ────────────────▶ SIGNALED
    ▲            📢 digest                       ✅ BUY          │
    └──────── weekly close < a 10/20/60wk SMA, or gate breaks ◀─┘
                              (silent)
```

- **Trigger** — the setup goes from not-live to live, whatever leg completed
  last: a 10wk reclaim (pullback resuming), a 60wk reclaim (recovery
  breakout), or the monthly gate completing. The alert names the leg.
- **BUY** — a triggered ticker's daily close is above all three daily SMAs
  (often the same evening as the trigger).
- **Reset** — a weekly close back below any of the 10/20/60-week SMAs (or
  the gate breaking) silently ends the setup; reclaiming is a fresh trigger.
- The **5-week SMA** plays no role in entries — it hugs price too closely
  and its crossings are noise at universe scale. It has exactly one job:
  the SELL line for positions you hold.
- **(tentative — …)** appears only when the signal is *waiting on* an
  unfinished bar — a condition that passes on the in-progress weekly or
  monthly bar but wouldn't pass on completed bars alone. The tag names
  what's pending: `(tentative — pending Fri Jul 17 close)` for a midweek
  weekly reclaim, `(tentative — monthly gate pending July close)` when the
  gate rests on the partial month. An open bar the signal doesn't depend on
  never tags; in `close` mode nothing is ever tentative.

An SMA with insufficient history is skipped (the 10/20 must exist; the 60 is
optional so young tickers still qualify). New tickers seed silently — no
alert blast for setups that completed long ago; only *new* events fire. A
persistently strong stock therefore stays quiet until its first real
5wk-or-deeper pullback resolves — that's by design.

## Positions & alert routing

Log what you actually buy with `/buy` — that ticker joins the **exit
engine**:

Each scan posts **one report** with two mutually exclusive sections (a ticker
appears in exactly one; empty sections are omitted):

| Section | Meaning |
|---|---|
| ✅ **BUY** | all three timeframes aligned. Each line carries its context inline: the trigger leg, `60m ✓/✗` (the nice-to-have), and any pending bar (`pending Fri Jul 24 close`, `pending month close (gate)`). No pending tag = firm signal. |
| 👀 **Setup complete — watching daily confirm** | triggered on the weekly/monthly, daily SMAs not yet all above; moves to BUY the day it confirms |

BUY entries are **muted while you hold the ticker** (unmute after a SELL
alert or `/sell`). Position alerts stay individual messages:

| Alert | Condition | Who gets it |
|---|---|---|
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
