# Deploying Bot_sterior on a Linux server

You need a Linux box that's always online. After you have SSH access to one, do this once:

## 1. Create a dedicated user and folder

```bash
sudo useradd --system --create-home --home-dir /opt/botsterior --shell /usr/sbin/nologin botsterior
sudo mkdir -p /opt/botsterior/attendees
sudo chown -R botsterior:botsterior /opt/botsterior
```

## 2. Copy the files onto the server

From your laptop:

```bash
scp verify_bot.py user@server:/tmp/
scp botsterior.service user@server:/tmp/
scp attendees/*.csv user@server:/tmp/    # or use rsync
```

Then on the server:

```bash
sudo mv /tmp/verify_bot.py /opt/botsterior/
sudo mv /tmp/*.csv /opt/botsterior/attendees/
sudo chown -R botsterior:botsterior /opt/botsterior
```

## 3. Make a Python virtualenv + install deps

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip   # if missing
sudo -u botsterior python3 -m venv /opt/botsterior/venv
sudo -u botsterior /opt/botsterior/venv/bin/pip install -U \
    discord.py python-dotenv pandas openpyxl
```

## 4. Create the .env file with the bot token

```bash
sudo -u botsterior tee /opt/botsterior/.env > /dev/null <<'EOF'
DISCORD_TOKEN=paste_your_token_here
EOF
sudo chmod 600 /opt/botsterior/.env
```

## 5. Install the systemd service

```bash
sudo cp /tmp/botsterior.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now botsterior      # start + run at every boot
```

## 6. Verify it's alive

```bash
sudo systemctl status botsterior            # should say "active (running)"
sudo journalctl -u botsterior -f            # tail the logs in real time
```

You should see `Loaded N attendees ...` then `Logged in as Bot_sterior#1234`.

## Day-to-day operations

| What you want to do | Command |
|---|---|
| Check status | `sudo systemctl status botsterior` |
| Tail logs | `sudo journalctl -u botsterior -f` |
| Restart (after editing code) | `sudo systemctl restart botsterior` |
| Stop temporarily | `sudo systemctl stop botsterior` |
| Disable autostart at boot | `sudo systemctl disable botsterior` |

## Updating the attendee list

You don't need to touch systemd at all. Just `scp` a new CSV into
`/opt/botsterior/attendees/` (or edit one in place), then in Discord type
`!reload`. The bot picks up the changes instantly without a restart.

If you change `verify_bot.py` itself, then yes — restart:

```bash
sudo systemctl restart botsterior
```

## What "Restart=always" buys you

The bot will come back automatically after:
- A crash (e.g. discord.py raises something unhandled)
- Discord API hiccups that kill the connection
- A server reboot (because `enable` was used)
- A network blip (it'll retry every 10 seconds until it reconnects)

The only times it stays down are if you `systemctl stop` it explicitly or
if the script fails to start 5 times in 10 seconds (rare; usually means a
config error you need to fix).
