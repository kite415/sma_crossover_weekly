# Setup

One-time setup takes about 15 minutes: create the Discord bot, fill `.env`,
start the container.

## 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application** → name it (e.g. `SMA Scanner`).
2. Left sidebar → **Bot**:
   - **Reset Token** → copy it → this is `DISCORD_TOKEN`. (Shown once —
     if you lose it, reset again.)
   - No privileged intents are needed (slash commands don't read messages).
3. Left sidebar → **OAuth2** → **URL Generator**:
   - Scopes: `bot` + `applications.commands`
   - Bot permissions: **Send Messages**, **Embed Links**
   - Open the generated URL and invite the bot to your server.

## 2. Get the IDs

1. Discord → **User Settings → Advanced → Developer Mode: on**.
2. Right-click your **server icon** → *Copy Server ID* → `GUILD_ID`.
3. Create/pick an alerts channel (e.g. `#alerts`), right-click it →
   *Copy Channel ID* → `ALERT_CHANNEL_ID`. Make sure the bot can post there.

## 3. Configure

```bash
cp .env.example .env
# fill in DISCORD_TOKEN, GUILD_ID, ALERT_CHANNEL_ID
```

## 4. Run — Docker (Mac or Raspberry Pi)

```bash
docker compose up -d --build
docker compose logs -f     # watch it come up
```

The SQLite database lands in `./data/bot.db` (a bind mount), so it survives
rebuilds and restarts. `restart: unless-stopped` brings the bot back after
reboots — enable Docker Desktop's *Start at login* on a Mac, or
`sudo systemctl enable docker` on a Pi.

First start: the bot syncs the slash commands to your server (they appear
within a minute) and imports `watchlist.txt` into the database once. Then run
`/scan` — the first scan seeds ~900 tickers silently (a few minutes) and
reports what it did. Alerts begin with the first real transition after that.

### Without Docker (bare Python)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m bot.main
```

To keep it alive as a service:

- **Raspberry Pi (systemd)** — `/etc/systemd/system/sma-bot.service`:

  ```ini
  [Unit]
  Description=SMA scanner Discord bot
  After=network-online.target

  [Service]
  WorkingDirectory=/home/pi/sma_crossover_weekly
  ExecStart=/home/pi/sma_crossover_weekly/.venv/bin/python -m bot.main
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

  then `sudo systemctl enable --now sma-bot`.

- **Mac (launchd)** — `~/Library/LaunchAgents/com.sma.bot.plist` with
  `KeepAlive` + `RunAtLoad` pointing at the same command, then
  `launchctl load ~/Library/LaunchAgents/com.sma.bot.plist`.
  Note: the Mac must not sleep for the 17:30 scan to fire
  (System Settings → Energy → prevent automatic sleeping, or use a Pi).

## 5. Sanity checks

- `/status NVDA` — should answer in a few seconds with all three timeframes.
- `/buy AAPL 200` then `/positions` — the position shows; BUY signals for
  AAPL are now muted. `/sell AAPL 210` prints the P&L and unmutes.
- `python -m bot.scan --dry-run --tickers NVDA,HIMS` — engine output without
  touching Discord (uses/creates `data/bot.db` unless `--db` says otherwise).
