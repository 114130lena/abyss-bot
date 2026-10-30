"""Monitor public CHZZK replay videos for Hanayo and post to a Discord thread."""
import logging
import os
import time
from pathlib import Path
import requests
from dotenv import load_dotenv
from banner_manager import get_banner
from creator_profiles import handle as creator_handle, avatar as creator_avatar
from creator_groups import chzzk_group
from discord_v2 import alert as unified_alert
from resilience import load_json, save_json, send_webhook, warn_failure

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
CHANNELS_FILE = BASE_DIR / "channels.txt"
STATE_FILE = BASE_DIR / "chzzk_replay_seen.json"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
THREAD_ID = os.getenv("CHZZK_REPLAY_THREAD_ID", "1565293200225206342").strip()
INTERVAL = max(60, int(os.getenv("CHZZK_REPLAY_POLL_INTERVAL", "180")))
_LAST = 0
_FAILURES = 0
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://chzzk.naver.com/"})

def _channel_ids():
    try:
        ids = [line.split("#", 1)[0].strip()
               for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        return []
    return [cid for cid in dict.fromkeys(ids) if cid and chzzk_group(cid) == "hanayo"]

def fetch_replays(channel_id):
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/videos"
    response = _SESSION.get(url, params={"sortType": "LATEST", "pagingType": "PAGE",
                                          "page": 0, "size": 30}, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200 or not isinstance(data.get("content"), dict):
        raise ValueError("CHZZK replay API did not return a valid content object")
    items = data["content"].get("data")
    if not isinstance(items, list):
        raise ValueError("CHZZK replay API has no video list")
    replays = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("videoType", "")).upper() != "REPLAY":
            continue
        video_no = item.get("videoNo")
        if not str(video_no).isdigit():
            continue
        thumb = (item.get("thumbnailImageUrl") or "").replace("{type}", "720")
        replays.append({"id": str(video_no), "title": item.get("videoTitle") or "제목 없음",
                        "thumbnail": thumb, "url": "https://chzzk.naver.com/video/" + str(video_no),
                        "date": item.get("publishDate") or item.get("createdDate") or ""})
    return list(reversed(replays))

def thread_webhook_url():
    if not THREAD_ID.isdigit() or not WEBHOOK.startswith("https://"):
        raise ValueError("Valid Discord webhook and replay thread ID required")
    if "thread_id=" in WEBHOOK:
        raise ValueError("Discord webhook URL already has thread_id; use parent channel webhook")
    sep = "&" if "?" in WEBHOOK else "?"
    return WEBHOOK + sep + "thread_id=" + THREAD_ID

def notify(replay):
    message = unified_alert(
        "chzzk", "하나요 히요리", get_banner("hanayo"),
        title=replay["title"], url=replay["url"],
        media_url=replay.get("thumbnail", ""), date=replay.get("date", ""),
        replay=True, ping=False,
        handle=creator_handle("hanayo"), avatar=creator_avatar("hanayo"))
    message["allowed_mentions"] = {"parse": []}
    send_webhook(thread_webhook_url(), message)

def check_replays():
    global _LAST, _FAILURES
    if time.monotonic() - _LAST < INTERVAL:
        return
    _LAST = time.monotonic()
    try:
        channels = _channel_ids()
        if not channels:
            raise ValueError("Hanayo CHZZK channel is not available in channels.txt")
        state = load_json(STATE_FILE, {})
        if not isinstance(state, dict):
            raise ValueError("Replay state file must contain a JSON object")
        for channel_id in channels:
            videos = fetch_replays(channel_id)
            old = state.get(channel_id)
            if old is None:
                state[channel_id] = [v["id"] for v in videos][-300:]
                save_json(STATE_FILE, state)
                logging.info("CHZZK replay initialized for %s: %d previous videos", channel_id, len(videos))
                continue
            known = set(old)
            for video in videos:
                if video["id"] in known:
                    continue
                notify(video)
                old = (old + [video["id"]])[-300:]
                known.add(video["id"])
                state[channel_id] = old
                save_json(STATE_FILE, state)
                logging.info("CHZZK replay posted to thread %s: %s", THREAD_ID, video["url"])
        _FAILURES = 0
    except Exception as exc:
        _FAILURES += 1
        warn_failure("CHZZK replay", _FAILURES, exc)
