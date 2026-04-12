# PaperMoneyRadioMonitor

A small Python monitor that checks radio station web pages every 30 seconds and emails you when "Pretty Faces" by Paper Money is currently playing.

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` or set environment variables directly:

```text
EMAIL_USERNAME=your@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_TO=timsussman1@gmail.com
EMAIL_TO_LIST=timsussman1@gmail.com,other@example.com
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
POLL_INTERVAL_SECONDS=30
STATION_CONFIG_PATH=stations.json
LOG_DIR=logs
TARGET_TITLE=Pretty Faces
TARGET_ARTIST=Paper Money
TEST_SONGS=I Just Might|Bruno Mars;So Easy (To Fall In Love)|Olivia Dean;Man I Need|Olivia Dean;Yukon|Justin Bieber;Ordinary|Alex Warren
```

The monitor will automatically load these values from a local `.env` file if it exists.

3. Edit `stations.json` and add the radio station URLs you want monitored.

Each station entry may also include optional metadata like `city`, `state`, `network`, and `amperwave_id`.

If the station is Townsquare/AmperWave powered, adding `amperwave_id` makes detection more robust because the monitor can query the AmperWave API directly.

```json
[
  {
    "name": "KFFM-FM",
    "city": "Yakima",
    "state": "WA",
    "network": "Townsquare Media",
    "amperwave_id": "5123",
    "url": "https://kffm.com/listen-live/"
  }
]
```

## How the monitor detects the song

The monitor tries to extract the currently playing track from the page using:

- JSON-LD metadata embedded in the page
- Open Graph / meta descriptions
- HTML elements with classes or IDs related to now-playing, track, artist, or song
- Plain page text fallback

If the page is purely client-side JavaScript and does not embed track metadata in the initial HTML, the parser may not be able to see the currently playing artist/title without a station-specific API.

For Townsquare / AmperWave powered listen-live pages, the monitor now detects the embedded AmperWave player iframe and fetches the station's now-playing API directly.

## Logging

The monitor logs every detected track to a per-station daily file under the `logs/` folder by default.
Each station gets its own subfolder, and the file name is `YYYY-MM-DD.txt`.

## Run

```bash
python monitor.py
```

## Run As A Raspberry Pi Service

This repo now includes first-class `systemd` support so the monitor can run under service management on a Raspberry Pi without starting automatically at boot.

1. Clone the repo onto the Pi, create the virtual environment, install requirements, and create `.env`.

```bash
cd /home/pi/PaperMoneyRadioMonitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

2. Generate and install the unit file.

```bash
sudo .venv/bin/python scripts/install_systemd_service.py \
  --user pi \
  --install
```

3. Start it manually when you want it running.

```bash
sudo systemctl start papermoney-radio-monitor
```

4. Check service state and logs.

```bash
sudo systemctl status papermoney-radio-monitor
journalctl -u papermoney-radio-monitor -f
```

The generated service:

- waits until you start it manually with `systemctl start`
- restarts automatically if the monitor exits
- uses the repo directory as the working directory, so `.env`, `stations.json`, and `logs/` keep working
- shuts down cleanly on `systemctl stop` or reboot via `SIGTERM`

## Behavior

- Checks each station every 30 seconds by default.
- Sends an email when it detects the song "Pretty Faces" by Paper Money.
- Also watches a configurable set of test songs and sends one `[THIS IS A TEST!]` email on the first detected match each time the process starts, then disables test-song monitoring for the rest of that session.
- Sends an email if the script aborts due to a monitoring error.

> For Gmail, use an app password if your account has 2FA enabled.
