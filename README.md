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

## Behavior

- Checks each station every 30 seconds by default.
- Sends an email when it detects the song "Pretty Faces" by Paper Money.
- Sends an email if the script aborts due to a monitoring error.

> For Gmail, use an app password if your account has 2FA enabled.
