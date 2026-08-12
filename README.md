# SMA Multi-Timeframe Scanner — Discord Bot

A self-hosted Discord bot that scans the **S&P 500 + S&P 400 + your personal
watchlist** (~900 tickers) every trading day after the close, looking for
stocks completing a three-timeframe momentum setup, and tracks the positions
you actually hold for exit alerts.

## The strategy

Eligibility plus two timeframes:

| Layer | Role | Condition |
|---|---|---|
| **Drawdown arm** | Eligibility | the stock is recovering from a drawdown **episode** that reached ≥ `DD_ARM_PCT` (default 30%) below its high (highest close in the ~10y data window), **and** price still sits ≥ `DD_MIN_OFF_PCT` (default 15%) below that high. Arming latches through the meat of the recovery; it stands down once the recovery is nearly complete or a new high ends the episode. Stocks grinding along near their highs never alert. |
| **Weekly** | Trigger | close above the 10- and 20-week SMAs (60wk is context: `60w ✓/✗`; monthly SMAs are context too, incl. `60m ✓/✗`) **and momentum confirms**: RSI(14) > 50, KDJ(9,3,3) K-line > 50, MACD(12,26) line > 0 |
| **Daily** | Entry confirm | close above the 10/20/60-day SMAs |

A setup is **live** when the ticker is armed, the weekly close is above the
10/20-week SMAs, and all three momentum indicators confirm (an incomputable
indicator on a young ticker fails the setup). The scanner alerts on
*transitions*, not conditions:

```
            trigger (setup completes)        daily confirm
  IDLE ────────────────────────▶ TRIGGERED ────────────────▶ SIGNALED
    ▲            📢 digest                       ✅ BUY          │
    └── CONFIRMED weekly close below a 10/20wk SMA / momentum ◀─┘
        cross-down, or the episode ends at a new high (silent)
```

- **Trigger** — the setup goes from not-live to live via a real price flip,
  whatever leg completed last: a 10/20wk SMA reclaim or a momentum
  indicator confirming ("RSI crossed 50", "KDJ crossed 50", "MACD turned
  positive"). The alert names the leg. Becoming *armed* is never a leg —
  eligibility engaging alone can't fire an alert.
- **BUY** — a triggered ticker's daily close is above all three daily SMAs
  (often the same evening as the trigger).
- **Reset** — silent, and only on *confirmed* weakness: a **completed**
  weekly close back below the 10/20-week SMAs / momentum thresholds (from a
  week ending on or after the week the setup went live, with the live bar
  not already back above), or the arm standing down (recovery inside the
  `DD_MIN_OFF_PCT` cap or a new high).
  Mid-week wobbles on the in-progress bar hold state instead of resetting,
  so a Tuesday trigger that sags into Friday and bounces Monday does **not**
  re-announce. After a confirmed reset, reclaiming is a fresh trigger.
- The **5-week SMA** plays no role in entries — it hugs price too closely
  and its crossings are noise at universe scale. It has exactly one job:
  the SELL line for positions you hold.
- **60m proximity rule**: a signal whose price sits *below* the 60-month SMA
  is deferred — no alert — until price comes within `M60_PROXIMITY_PCT`
  (default 10%) of the line. The engine keeps tracking silently and fires
  the held alert (original trigger legs intact) the day the gap closes or
  price crosses the 60m. Below-60m alerts show the gap: `60m ✗ (5.9% below)`.
- **(tentative — …)** appears only when the signal is *waiting on* an
  unfinished bar — a condition that passes on the in-progress weekly bar
  but wouldn't pass on completed bars alone. The tag names what's pending:
  `(tentative — pending Fri Jul 17 close)` for a midweek weekly reclaim. An
  open bar the signal doesn't depend on never tags; in `close` mode nothing
  is ever tentative.

An SMA with insufficient history is skipped (the 10/20 must exist; the 60 is
optional so young tickers still qualify). New tickers seed silently — no
alert blast for setups that completed long ago; only *new* events fire. A
persistently strong stock therefore stays quiet until its first real
5wk-or-deeper pullback resolves — that's by design.

## Crash-recovery experiments (forward testing)

Parallel test variants alongside the live strategy — tracked, never traded
by the bot. Deliberately **forward-only** (no backtest): strategies are
regime-dependent; the goal is evidence for what works *now*.

- **A case opens** when a daily close is ≥30% below the highest close of the
  trailing **1M / 3M / 6M** window, tagged by the *fastest* window satisfied
  (violent / medium / slow-grind crashes — exclusive buckets). The report
  shows `🔍 Now watching: CHRW — 31% off its 1-month high`. One active case
  per ticker; the trough is tracked; at first deploy, in-progress crashes
  still below the 10-week MA are reconstructed from history (silently).
- **One Discord alert per case** — the evening of the first *daily* close
  back above the 10-week MA ("the day it happens"). Whipsaws never re-alert.
- **Five entry rules** are silently stamped per case (date + price):
  `daily-cross`, `weekly-cross` (first completed Friday close above the 10wk
  MA), `confirm-2` / `confirm-3` (2nd/3rd consecutive Friday close; a close
  below resets), and `dual-ma` (daily close above both the 10wk and 20wk
  MAs). Live-strategy BUYs are logged too, as the `live` baseline. The case
  archives once all five have fired.
- **Scoreboard**: `http://<mac>.local:8321/dashboard.html` on the home
  network (port via `DASHBOARD_PORT`, 0 disables), regenerated after every
  scan — per-bucket win rates and returns, open cases, recent signals.
  `/experiments` in Discord shows the text version anywhere.

## Positions & alert routing

Log what you actually buy with `/buy` — that ticker joins the **exit
engine**:

Each scan posts **one report** with two mutually exclusive sections (a ticker
appears in exactly one; empty sections are omitted):

| Section | Meaning |
|---|---|
| ✅ **BUY** | armed + weekly + daily aligned. Each line carries its context inline: the trigger leg, the drawdown episode (`−52% max · 32% off high`), `60w`/`60m ✓/✗` (nice-to-haves), and any pending bar (`pending Fri Jul 24 close`). No pending tag = firm signal. |
| 👀 **Setup complete — watching daily confirm** | triggered on the weekly, daily SMAs not yet all above; moves to BUY the day it confirms |

Within each section, tickers are grouped under emoji sector headers with
**💻 Tech always first** (a broad bucket: GICS Information Technology +
Communication Services + hand-picked extras like AMZN/TSLA — edit
`TECH_EXTRAS` in `bot/sectors.py`), then other GICS sectors alphabetically.
Sector data rides along with the weekly Wikipedia constituent scrape;
watchlist tickers outside the indices resolve via yfinance once and are
cached.

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
- `/experiments` — crash-recovery experiment scoreboard (text version)
- `/watchlist add | remove | list` — personal tickers beyond the indices
- `/scan` — run a scan on demand

## Configuration (`.env`)

| Var | Meaning |
|---|---|
| `DISCORD_TOKEN` / `GUILD_ID` / `ALERT_CHANNEL_ID` | see [SETUP.md](SETUP.md) |
| `CONFIRM_MODE` | `live` (default): evaluate the in-progress weekly/monthly bar, tagging alerts *(tentative)*. `close`: completed bars only. The daily bar is always final on scheduled scans (they run after the close); a manual midday `/scan` evaluates the intraday price. |
| `SCAN_HOUR` / `SCAN_MINUTE` | scan time, America/New_York (default 16:10 Mon–Fri, right after the close) |
| `M60_PROXIMITY_PCT` | below-60m signals stay silent until price is within this percent of the 60-month SMA (default 10) |
| `DD_ARM_PCT` | drawdown-episode arm threshold: a ticker is only eligible to trigger while recovering from a decline of at least this percent off its high (default 30) |
| `DD_MIN_OFF_PCT` | the arm's cap: price must still be at least this percent below the high — recoveries inside it stand down (default 15) |
| `DASHBOARD_PORT` | LAN port for the experiment scoreboard page (default 8321; 0 disables) |
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
- The drawdown arm is entry-only: once you're in a position, exits are the
  10-day warning and the 5-week SELL — a new high never triggers a sell.
- One data hiccup never ejects state: a ticker with no data this scan keeps
  yesterday's state untouched.
