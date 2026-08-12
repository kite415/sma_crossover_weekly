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

### Without Docker (conda)

```bash
conda create -n sma-bot python=3.12 -y
conda activate sma-bot
pip install -r requirements.txt
python -m bot.main
```

To keep it alive as a service, point the service at the conda env's python
binary directly (services don't run `conda activate`; find yours with
`conda env list` — e.g. `~/miniconda3/envs/sma-bot/bin/python`):

- **Raspberry Pi (systemd)** — `/etc/systemd/system/sma-bot.service`:

  ```ini
  [Unit]
  Description=SMA scanner Discord bot
  After=network-online.target

  [Service]
  WorkingDirectory=/home/pi/sma_crossover_weekly
  ExecStart=/home/pi/miniconda3/envs/sma-bot/bin/python -m bot.main
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

  then `sudo systemctl enable --now sma-bot`.

- **Mac (launchd)** — `~/Library/LaunchAgents/com.sma.bot.plist` with
  `KeepAlive` + `RunAtLoad`. Wrap the command in `caffeinate` so the Mac
  can't idle-sleep past the post-close scan while the bot is running — launchd
  handles crashes/reboots, caffeinate handles sleep:

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0">
  <dict>
    <key>Label</key><string>com.sma.bot</string>
    <key>WorkingDirectory</key><string>/Users/YOU/sma_crossover_weekly</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/caffeinate</string>
      <string>-is</string>
      <string>/Users/YOU/miniconda3/envs/sma-bot/bin/python</string>
      <string>-m</string>
      <string>bot.main</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
  </dict>
  </plist>
  ```

  then `launchctl load ~/Library/LaunchAgents/com.sma.bot.plist`.

  Caffeinate caveats: it does **not** prevent sleep when a MacBook's lid is
  closed (clamshell sleep wins unless on power with an external display),
  and `-s` only holds on AC power — on battery the Mac can still sleep. If
  the machine is a laptop that gets closed or travels, host on a Raspberry
  Pi instead.

  **Deploying changes**: after editing code, restart the managed service —

  ```bash
  launchctl kickstart -k gui/$(id -u)/com.sma.bot
  ```

  Never run `python -m bot.main` in a terminal alongside (or instead of)
  the service: a second instance contends for the SQLite write lock and can
  kill the scheduled scan ("database is locked", 2026-08-11), and a
  terminal instance dies with its window and logs to the TTY instead of
  `data/bot.log`. To stop the bot temporarily use
  `launchctl bootout gui/$(id -u)/com.sma.bot` (KeepAlive restarts a merely
  killed process), then `launchctl bootstrap gui/$(id -u)
  ~/Library/LaunchAgents/com.sma.bot.plist` to bring it back. Avoid
  `launchctl disable`: a disabled service refuses to load later with the
  cryptic `Bootstrap failed: 5: Input/output error` (fix:
  `launchctl enable gui/$(id -u)/com.sma.bot`).

## 5. Sanity checks

- `/status NVDA` — should answer in a few seconds with all three timeframes.
- `/buy AAPL 200` then `/positions` — the position shows; BUY signals for
  AAPL are now muted. `/sell AAPL 210` prints the P&L and unmutes.
- `python -m bot.scan --dry-run --tickers NVDA,HIMS` — engine output without
  touching Discord (uses/creates `data/bot.db` unless `--db` says otherwise).
