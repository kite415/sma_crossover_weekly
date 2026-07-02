# Weekly SMA Crossover Alerter → ntfy

Every weekday after the US market close, this checks a watchlist and pushes an
[ntfy](https://ntfy.sh) notification whenever a ticker's price **crosses its
10-week or 60-week simple moving average** — in **either direction**:

- **▲ crossed above** → bullish signal
- **▼ crossed below** → bearish signal

It runs for free on GitHub Actions cron. No API keys needed (prices come from
Yahoo Finance via `yfinance`).

---

## How it works

For each ticker in `watchlist.txt`:

1. Download ~5 years of daily prices (`yfinance`).
2. Resample to **weekly bars** (`W-FRI`, the last close of each week).
3. Compute the **10-week and 60-week SMAs** (rolling means over weekly closes).
4. Detect a crossover of the latest weekly close vs. **either** SMA.
5. Fire **one** ntfy notification per SMA on the *transition only*.

Because it runs **daily**, it persists the last-known side (`above` / `below`)
per ticker **per SMA** in **`state.json`** and only alerts when a side flips.
The first run (and any newly added ticker) records the side **silently** — no
alert blast for everything's current position.

- Each SMA needs its length + 1 weekly bars (11 for the 10-week, 61 for the
  60-week). An SMA without enough history is skipped; the ticker's other SMAs
  are still evaluated.
- Each ticker is wrapped in try/except, so one bad symbol can't kill the run.
- Everything checked/found is logged to the Actions console.

### `CONFIRM_MODE` toggle

Set via the `CONFIRM_MODE` env var (default **`close`**):

| Mode    | Behavior                                                                                  |
|---------|-------------------------------------------------------------------------------------------|
| `close` | **Default.** Only evaluates *completed* weekly bars. The in-progress week is dropped, so you only get confirmed Friday crosses — cleaner, less noise. A Friday-close cross alerts on the next run after that Friday. |
| `live`  | Also evaluates the *current in-progress* week against the SMA, so an intraweek cross alerts same-day. Those alerts are tagged **`(tentative)`** because the week can still reverse before Friday. Noisier. |

Change the default in `alerter.py`, or override per manual run (see below).

---

## Setup

### 1. Pick & subscribe to an ntfy topic

ntfy topics are public-by-default and act like a password — **pick something
random and unguessable**, e.g. `sma-alerts-7Kq2vXp9`.

- **Phone:** install the ntfy app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
  [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)),
  tap **+**, enter your topic name. Done.
- **Browser:** open `https://ntfy.sh/<your-topic>` and allow notifications.

Test it:

```bash
curl -d "hello from ntfy" https://ntfy.sh/<your-topic>
```

You should get a push within a second or two.

### 2. Push this repo to GitHub

Create a repo and push these files (`alerter.py`, `requirements.txt`,
`watchlist.txt`, `.github/workflows/alerter.yml`, this README).

### 3. Add `NTFY_TOPIC` as a GitHub secret

In your repo: **Settings → Secrets and variables → Actions → New repository
secret**.

- **Name:** `NTFY_TOPIC`
- **Value:** your topic name, e.g. `sma-alerts-7Kq2vXp9` (just the topic, not
  the full URL).

(Optional) Add `NTFY_SERVER` the same way if you self-host ntfy; otherwise it
defaults to `https://ntfy.sh`.

### 4. Confirm Actions can write state

The workflow declares `permissions: contents: write` so it can commit
`state.json` back to the repo. If your org disables this by default, enable
**Settings → Actions → General → Workflow permissions → Read and write
permissions**.

---

## Testing (do this before trusting the cron)

1. Go to the **Actions** tab → **SMA crossover alerter** → **Run workflow**.
2. (Optional) set the **confirm_mode** input to `live` to force same-day
   evaluation while testing.
3. Click **Run workflow** and watch the logs.

- **First manual run:** records each ticker's current side into `state.json`
  and sends **no alerts** (by design). You'll see `first sighting; recording
  side` lines and a `state.json` commit.
- **To prove a notification actually fires:** edit `state.json` to flip a
  ticker's `"side"` (e.g. set NVDA to the opposite of what the log reported),
  commit, then **Run workflow** again. The mismatch will trigger a real ntfy
  push for that ticker. Reset `state.json` afterward if you like (the next run
  will re-record it anyway).

### Run locally

```bash
pip install -r requirements.txt
export NTFY_TOPIC=<your-topic>
export CONFIRM_MODE=close        # or live
python alerter.py
```

---

## Schedule

```
cron: "30 21 * * 1-5"   # 21:30 UTC, Mon–Fri
```

That's **4:30pm EST / 5:30pm EDT**, always after the 4:00pm ET close. GitHub's
scheduled runs are UTC and can fire late under load — that's fine here.

## Watchlist

`watchlist.txt`, one ticker per line. Blank lines and `#` comments are ignored.
Seeded with NVDA, AAPL, MSFT, AMZN, GOOGL — edit freely.
