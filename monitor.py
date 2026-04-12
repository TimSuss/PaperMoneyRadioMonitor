import json
import os
import re
import signal
import smtplib
import sys
import time
import traceback
from datetime import datetime, timezone
from email.message import EmailMessage
from threading import Event
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

CONFIG_PATH = os.environ.get("STATION_CONFIG_PATH", "stations.json")
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_SMTP_SERVER = os.environ.get("EMAIL_SMTP_SERVER", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_FROM = os.environ.get("EMAIL_FROM", EMAIL_USERNAME)
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_USERNAME)
EMAIL_TO_LIST = os.environ.get("EMAIL_TO_LIST")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
TARGET_TITLE = os.environ.get("TARGET_TITLE", "Pretty Faces")
TARGET_ARTIST = os.environ.get("TARGET_ARTIST", "Paper Money")
AMPERWAVE_MAX_ITEMS = int(os.environ.get("AMPERWAVE_MAX_ITEMS", "20"))
AMPERWAVE_API_HOST = "api-nowplaying.amperwave.net"
AMPERWAVE_API_PATH = "prtplus/nowplaying"
AMPERWAVE_API_VERSION = 1
LOG_DIR = os.environ.get("LOG_DIR", "logs")
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "3"))
STOP_EVENT = Event()
STOP_SIGNAL_NAME = None


class MonitorError(Exception):
    pass


def request_shutdown(signum, _frame):
    global STOP_SIGNAL_NAME
    STOP_SIGNAL_NAME = signal.Signals(signum).name
    if not STOP_EVENT.is_set():
        print(f"Received {STOP_SIGNAL_NAME}. Requesting graceful shutdown...")
    STOP_EVENT.set()


def install_signal_handlers():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_shutdown)


def load_station_config(path):
    if not os.path.exists(path):
        raise MonitorError(f"Station config file not found: {path}")

    with open(path, "r", encoding="utf-8") as handle:
        stations = json.load(handle)

    if not isinstance(stations, list) or not stations:
        raise MonitorError("stations.json must contain a non-empty JSON array of station objects.")

    for station in stations:
        if not isinstance(station, dict) or "name" not in station or "url" not in station:
            raise MonitorError("Each station entry must be an object with 'name' and 'url'.")
        if "city" in station and not isinstance(station["city"], str):
            raise MonitorError("If present, 'city' must be a string.")
        if "state" in station and not isinstance(station["state"], str):
            raise MonitorError("If present, 'state' must be a string.")
        if "network" in station and not isinstance(station["network"], str):
            raise MonitorError("If present, 'network' must be a string.")
        if "amperwave_id" in station and not isinstance(station["amperwave_id"], str):
            raise MonitorError("If present, 'amperwave_id' must be a string.")
        if "disabled" in station and not isinstance(station["disabled"], bool):
            raise MonitorError("If present, 'disabled' must be a boolean.")
        if "failure_count" in station and not isinstance(station["failure_count"], int):
            raise MonitorError("If present, 'failure_count' must be an integer.")
        if "disabled_reason" in station and not isinstance(station["disabled_reason"], str):
            raise MonitorError("If present, 'disabled_reason' must be a string.")

    return stations


def save_station_config(path, stations):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stations, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def increment_station_failure(station, reason):
    station["failure_count"] = station.get("failure_count", 0) + 1
    station["disabled_reason"] = reason
    if station["failure_count"] >= MAX_CONSECUTIVE_FAILURES:
        station["disabled"] = True
        station["disabled_time"] = datetime.now(timezone.utc).isoformat()
        print(
            f"Station '{station['name']}' disabled after {station['failure_count']} consecutive failures: {reason}"
        )
        return True

    print(
        f"Station '{station['name']}' failure count {station['failure_count']} / {MAX_CONSECUTIVE_FAILURES}: {reason}"
    )
    return False


def reset_station_failures(station):
    if station.get("failure_count", 0) > 0:
        station["failure_count"] = 0
        station.pop("disabled_reason", None)
        station.pop("disabled_time", None)
        print(f"Station '{station['name']}' failure counter reset after successful check.")


def is_station_url_valid(url):
    headers = {
        "User-Agent": "PaperMoneyRadioMonitor/1.0 (+https://github.com)"
    }
    try:
        response = requests.get(url, headers=headers, timeout=(10, 15), stream=True)
        response.close()
        response.raise_for_status()
        return True
    except Exception:
        return False


def validate_station_urls(path, stations):
    valid_stations = []
    disabled_count = 0
    for station in stations:
        if station.get("disabled"):
            print(f"Skipping disabled station: {station.get('name', 'Unnamed station')}")
            continue

        url = station.get("url")
        if not url:
            reason = "missing URL"
            station["disabled"] = True
            disabled_count += 1
            print(f"Disabling station '{station.get('name', 'Unnamed station')}' because URL validation failed: {url} ({reason})")
            continue

        print(f"Validating station URL for: {station['name']} -> {url}")
        if is_station_url_valid(url):
            valid_stations.append(station)
        else:
            station["disabled"] = True
            disabled_count += 1
            print(f"Disabling station '{station['name']}' because URL validation failed: {url} (unreachable or non-200 response)")

    if disabled_count:
        save_station_config(CONFIG_PATH, stations)
        print(f"Updated {CONFIG_PATH} with {disabled_count} disabled station(s).")

    if not valid_stations:
        raise MonitorError("No valid station URLs available after startup validation.")

    print(f"{len(valid_stations)} station(s) validated and enabled.")
    return valid_stations


def create_smtp_connection():
    if not EMAIL_USERNAME or not EMAIL_PASSWORD:
        raise MonitorError("EMAIL_USERNAME and EMAIL_PASSWORD must be set to send email.")

    try:
        if EMAIL_SMTP_PORT == 465:
            smtp = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30)
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()

        smtp.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        return smtp
    except smtplib.SMTPAuthenticationError as exc:
        raise MonitorError(
            f"SMTP authentication failed for {EMAIL_SMTP_SERVER}:{EMAIL_SMTP_PORT}. "
            "Check the username/password and, if using Gmail, use an app password or enable SMTP access."
        ) from exc


def send_single_email(smtp, subject, body, to_email):
    message = EmailMessage()
    message["From"] = EMAIL_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    smtp.send_message(message)


def parse_recipient_list(recipient_value):
    if not recipient_value:
        return []

    separators = [",", ";"]
    for sep in separators:
        if sep in recipient_value:
            items = [item.strip() for item in recipient_value.split(sep)]
            return [item for item in items if item]

    return [recipient_value.strip()] if recipient_value.strip() else []


def sanitize_station_name(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(name)).strip("_")


def log_track(station_name, title, artist, source):
    safe_station = sanitize_station_name(station_name)
    station_dir = os.path.join(LOG_DIR, safe_station)
    os.makedirs(station_dir, exist_ok=True)
    date_str = datetime.now(timezone.utc).date().isoformat()
    log_file = os.path.join(station_dir, f"{date_str}.txt")
    timestamp = datetime.now(timezone.utc).isoformat()
    title_text = title or "UNKNOWN"
    artist_text = artist or "UNKNOWN"
    source_text = source or "unknown"
    line = f"{timestamp} | {title_text} | {artist_text} | source:{source_text}\n"
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(line)


def send_email(subject, body, to_email=None):
    to_email = to_email or EMAIL_TO
    if not to_email:
        raise MonitorError("No EMAIL_TO configured for send_email.")

    with create_smtp_connection() as smtp:
        send_single_email(smtp, subject, body, to_email)


def send_email_blast(subject, body, to_emails=None):
    recipients = parse_recipient_list(to_emails or EMAIL_TO_LIST) or parse_recipient_list(EMAIL_TO)
    if not recipients:
        raise MonitorError("No email recipients configured for blast notifications.")

    with create_smtp_connection() as smtp:
        for to_email in recipients:
            send_single_email(smtp, subject, body, to_email)


def normalize_text(text):
    return " ".join(str(text).lower().split())


def clean_value(value):
    if not value:
        return ""
    return " ".join(str(value).strip().strip('"\'').split())


def extract_amperwave_station_id(soup):
    iframe = soup.find("iframe", src=re.compile(r"player\.amperwave\.net/\d+", re.I))
    if not iframe:
        return None

    src = iframe.get("src") or ""
    if src.startswith("//"):
        src = "https:" + src
    parsed = urlparse(src)
    path = parsed.path.lstrip("/")
    if not path:
        return None
    station_id = path.split("/")[0]
    return station_id if station_id.isdigit() else None


def build_amperwave_nowplaying_url(station_id, max_items=AMPERWAVE_MAX_ITEMS):
    return f"https://{AMPERWAVE_API_HOST}/api/v{AMPERWAVE_API_VERSION}/{AMPERWAVE_API_PATH}/{max_items}/{station_id}/nowplaying.json"


def fetch_amperwave_nowplaying(station_id, max_items=AMPERWAVE_MAX_ITEMS):
    url = build_amperwave_nowplaying_url(station_id, max_items)
    response = requests.get(url, headers={"User-Agent": "PaperMoneyRadioMonitor/1.0 (+https://github.com)"}, timeout=(10, 20))
    response.raise_for_status()
    return response.json()


def parse_amperwave_nowplaying(data):
    if not isinstance(data, dict):
        return None, None, None

    performances = data.get("performances") or []
    if not performances:
        return None, None, None

    now = datetime.now(timezone.utc)
    candidate = None
    candidate_time = None

    for performance in performances:
        performance_time = performance.get("time")
        if not performance_time:
            continue
        try:
            perf_dt = datetime.fromisoformat(performance_time.replace("Z", "+00:00"))
        except ValueError:
            continue

        if perf_dt <= now and (candidate_time is None or perf_dt > candidate_time):
            candidate = performance
            candidate_time = perf_dt

    if not candidate:
        candidate = performances[0]

    title = clean_value(candidate.get("title"))
    artist = clean_value(candidate.get("artist"))
    if title and artist:
        return title.title(), artist.title(), "amperwave-api"

    return None, None, "amperwave-api"


def parse_artist_title(text):
    if not text:
        return None, None

    text = re.sub(r"[\u2013\u2014–—]+", "-", str(text))
    text = normalize_text(text)

    patterns = [
        r"now playing[:\s-]*([^\n\-]+?)\s*[-|]\s*([^\n]+)",
        r"currently playing[:\s-]*([^\n\-]+?)\s*[-|]\s*([^\n]+)",
        r"now playing[:\s-]*([^\n]+?)\s+by\s+([^\n]+)",
        r"currently playing[:\s-]*([^\n]+?)\s+by\s+([^\n]+)",
        r"song[:\s]*([^\|\n]+?)\s*[|\-]\s*artist[:\s]*([^\n]+)",
        r"artist[:\s]*([^\|\n]+?)\s*[|\-]\s*song[:\s]*([^\n]+)",
        r"([^\n\-]+?)\s*[-|]\s*([^\n]+)",
        r"([^\n]+?)\s+by\s+([^\n]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        groups = match.groups()
        if len(groups) == 2:
            first, second = clean_value(groups[0]), clean_value(groups[1])
            if " by " in pattern or pattern.endswith(r"by\s+([^\\n]+)"):
                title, artist = first, second
            elif "artist" in pattern and pattern.startswith("artist"):
                artist, title = first, second
            elif "song" in pattern and pattern.startswith("song"):
                title, artist = first, second
            else:
                # Most common format is Artist - Title, but some pages use Title by Artist.
                if " by " in text or first == TARGET_TITLE.lower() or second == TARGET_TITLE.lower():
                    title, artist = first, second
                else:
                    artist, title = first, second

            if title and artist:
                return title.title(), artist.title()

    return None, None


def extract_track_info_from_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            title, artist = extract_artist_title_from_jsonld_object(obj)
            if title and artist:
                return title, artist
    return None, None


def extract_artist_title_from_jsonld_object(obj):
    if not isinstance(obj, dict):
        return None, None

    title = obj.get("name") or obj.get("headline") or obj.get("trackName") or obj.get("song")
    if isinstance(title, dict):
        title = title.get("name")

    artist = None
    by_artist = obj.get("byArtist") or obj.get("artist")
    if isinstance(by_artist, dict):
        artist = by_artist.get("name")
    elif isinstance(by_artist, str):
        artist = by_artist

    if title and artist:
        return clean_value(title).title(), clean_value(artist).title()

    description = obj.get("description")
    if description:
        title, artist = parse_artist_title(description)
        if title and artist:
            return title, artist

    return None, None


def extract_track_info_from_meta(soup):
    for tag in soup.find_all("meta"):
        content = tag.get("content") or ""
        if not content:
            continue

        lower = normalize_text(content)
        if any(key in lower for key in ["now playing", "currently playing", "artist", "song", "track"]):
            title, artist = parse_artist_title(content)
            if title and artist:
                return title, artist

    return None, None


def find_candidate_texts(soup):
    candidates = []
    keywords = ["now", "playing", "track", "artist", "song", "title", "current"]

    for element in soup.find_all(attrs=True):
        attrs = " ".join(
            [str(value) for value in element.attrs.values() if isinstance(value, (str, list))]
        ).lower()
        if any(key in attrs for key in keywords):
            text = element.get_text(separator=" ", strip=True)
            if text:
                candidates.append(text)

    for script in soup.find_all("script"):
        if script.string and any(key in script.string.lower() for key in keywords):
            candidates.append(script.string)

    return candidates


def extract_track_info(soup):
    title, artist = extract_track_info_from_jsonld(soup)
    if title and artist:
        return title, artist, "jsonld"

    title, artist = extract_track_info_from_meta(soup)
    if title and artist:
        return title, artist, "meta"

    for text in find_candidate_texts(soup):
        title, artist = parse_artist_title(text)
        if title and artist:
            return title, artist, "candidate"

    return None, None, None


def is_target_song_playing(title, artist, page_text):
    if title and artist:
        return normalize_text(title) == normalize_text(TARGET_TITLE) and normalize_text(artist) == normalize_text(TARGET_ARTIST)

    normalized = normalize_text(page_text)
    return normalize_text(TARGET_TITLE) in normalized and normalize_text(TARGET_ARTIST) in normalized


def fetch_station_page(url):
    headers = {
        "User-Agent": "PaperMoneyRadioMonitor/1.0 (+https://github.com)"
    }
    response = requests.get(url, headers=headers, timeout=(10, 20))
    response.raise_for_status()
    return response.text


def check_station(station):
    html = fetch_station_page(station["url"])
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator=" ", strip=True)

    title = artist = source = None
    station_id = station.get("amperwave_id")
    if station_id:
        station_id = str(station_id).strip()
    else:
        station_id = extract_amperwave_station_id(soup)

    if station_id:
        try:
            data = fetch_amperwave_nowplaying(station_id)
            title, artist, source = parse_amperwave_nowplaying(data)
        except Exception:
            title = artist = None
            source = None

    if not title or not artist:
        title, artist = extract_track_info(soup)
        source = source or "html"
    if not title or not artist:
        title, artist = parse_artist_title(page_text)
        source = source or "page-text"

    log_track(station["name"], title, artist, source)
    playing = is_target_song_playing(title, artist, page_text)
    return playing, title, artist, source


def build_error_body(error, station_name=None, station_url=None):
    lines = [
        f"PaperMoneyRadioMonitor detected an error at {datetime.utcnow().isoformat()} UTC.",
        "",
    ]
    if station_name and station_url:
        lines.append(f"Station: {station_name}")
        lines.append(f"URL: {station_url}")
        lines.append("")
    lines.append("Error:")
    lines.append(str(error))
    lines.append("")
    lines.append("Traceback:")
    lines.append(traceback.format_exc())
    return "\n".join(lines)


def main():
    stations = load_station_config(CONFIG_PATH)
    stations = validate_station_urls(CONFIG_PATH, stations)
    state = {station["name"]: False for station in stations}
    enabled_station_names = ", ".join([station["name"] for station in stations])

    start_body = (
        f"PaperMoneyRadioMonitor started at {datetime.utcnow().isoformat()} UTC.\n"
        f"Loaded {len(stations)} station(s) from {CONFIG_PATH}.\n"
        f"Enabled stations: {enabled_station_names}\n"
        f"Polling every {POLL_INTERVAL_SECONDS} seconds.\n"
        f"Target song: '{TARGET_TITLE}' by '{TARGET_ARTIST}'.\n"
    )
    try:
        send_email("PaperMoneyRadioMonitor started", start_body)
    except Exception:
        print("Failed to send startup notification:", traceback.format_exc())

    print(f"Loaded {len(stations)} station(s) from {CONFIG_PATH}.")
    print(f"Polling every {POLL_INTERVAL_SECONDS} seconds.")
    print("Press Ctrl+C to stop manually.")

    while not STOP_EVENT.is_set():
        for station in stations:
            if STOP_EVENT.is_set():
                break

            name = station["name"]
            try:
                playing, title, artist, source = check_station(station)
                if station.get("failure_count", 0) > 0:
                    reset_station_failures(station)
                    save_station_config(CONFIG_PATH, stations)
            except Exception as exc:
                reason = str(exc)
                disabled = increment_station_failure(station, reason)
                save_station_config(CONFIG_PATH, stations)
                if disabled:
                    print(f"Station {name} is now disabled permanently until re-enabled in {CONFIG_PATH}.")
                    continue
                print(f"Error checking station {name} ({station['url']}): {exc}")
                print(traceback.format_exc())
                continue

            if playing and not state[name]:
                track_text = f"{title} by {artist}" if title and artist else f"{TARGET_TITLE} by {TARGET_ARTIST}"
                body = (
                    f"The song '{track_text}' is now playing on {name}.\n"
                    f"Station URL: {station['url']}\n"
                    f"Discovered via: {source}\n"
                    f"Checked at {datetime.utcnow().isoformat()} UTC."
                )
                send_email_blast(f"Now Playing: {track_text}", body)
                print(f"Alert sent for station: {name} ({track_text}) via {source}")
                state[name] = True

            if not playing and state[name]:
                state[name] = False

        if STOP_EVENT.wait(POLL_INTERVAL_SECONDS):
            break

    return STOP_SIGNAL_NAME


if __name__ == "__main__":
    install_signal_handlers()
    try:
        shutdown_signal = main()
        if shutdown_signal:
            try:
                stop_body = (
                    f"PaperMoneyRadioMonitor stopped gracefully at {datetime.utcnow().isoformat()} UTC.\n"
                    f"Shutdown signal: {shutdown_signal}."
                )
                send_email("PaperMoneyRadioMonitor stopped", stop_body)
            except Exception:
                print("Failed to send shutdown notification:", traceback.format_exc())
            print(f"PaperMoneyRadioMonitor stopped after {shutdown_signal}.")
            sys.exit(0)
    except KeyboardInterrupt:
        try:
            stop_body = (
                f"PaperMoneyRadioMonitor stopped by user at {datetime.utcnow().isoformat()} UTC.\n"
                f"This was a graceful shutdown after user interrupt."
            )
            send_email("PaperMoneyRadioMonitor stopped", stop_body)
        except Exception:
            print("Failed to send shutdown notification:", traceback.format_exc())
        print("PaperMoneyRadioMonitor stopped by user.")
        sys.exit(0)
    except Exception as exc:
        try:
            error_body = (
                f"PaperMoneyRadioMonitor encountered a fatal error at {datetime.utcnow().isoformat()} UTC.\n"
                f"Error: {exc}\n\n"
                f"See console output for details."
            )
            send_email("PaperMoneyRadioMonitor fatal error", error_body)
        except Exception:
            print("Failed to send fatal error notification:", traceback.format_exc())
        print("Fatal error, see email notification or console output.")
        sys.exit(1)
