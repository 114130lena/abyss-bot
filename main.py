import os
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from cafe_notifier import check_cafe
from twitter_notifier import check_twitter
from youtube_notifier import check_youtube
from resilience import load_json, save_json, send_webhook, warn_failure

load_dotenv()

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
CHANNELS_FILE = BASE_DIR / "channels.txt"
LIVE_STATE_FILE = BASE_DIR / "live_state.json"
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
POLL_INTERVAL = max(10, int(os.getenv("POLL_INTERVAL", "10")))
ROLE_BY_CHANNEL = {
    "36ddb9bb4f17593b60f1b63cec86611d": "1564279305171640361",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://chzzk.naver.com/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

session = requests.Session()
session.headers.update(HEADERS)


def load_channels():
    """Read one CHZZK channel ID per line; changes apply without restarting."""
    try:
        lines = CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logging.warning("%s 파일이 없습니다.", CHANNELS_FILE)
        return []

    channel_ids = []
    for line in lines:
        channel_id = line.strip()
        if channel_id and not channel_id.startswith("#") and channel_id not in channel_ids:
            channel_ids.append(channel_id)
    return channel_ids


def get_live_info(channel_id):
    url = f"https://api.chzzk.naver.com/service/v3.3/channels/{channel_id}/live-detail"
    response = session.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 200 or not data.get("content"):
        raise ValueError("치지직 API 응답에 방송 정보가 없습니다.")

    content = data["content"]
    channel = content.get("channel") or {}
    live_id = content.get("liveId")
    thumbnail = content.get("liveImageUrl") or content.get("defaultThumbnailImageUrl") or ""
    if thumbnail:
        thumbnail = thumbnail.replace("{type}", "720")

    return {
        "channel_id": channel_id,
        "name": channel.get("channelName") or content.get("channelName") or channel_id,
        "avatar": channel.get("channelImageUrl") or "",
        "status": content.get("status", "UNKNOWN"),
        "title": content.get("liveTitle") or "제목 없음",
        "category": content.get("liveCategoryValue") or "카테고리 정보 없음",
        "thumbnail": thumbnail,
        "open_date": content.get("openDate") or "",
        "url": f"https://chzzk.naver.com/live/{live_id}" if live_id else f"https://chzzk.naver.com/{channel_id}",
    }


def send_discord_notification(live):
    embed = {
        "author": {
            "name": live["name"],
            "url": f"https://chzzk.naver.com/{live['channel_id']}",
        },
        "title": live["title"],
        "url": live["url"],
        "description": f"{live['name']} 님이 방송을 시작했습니다.",
        "color": 0x00FFA3,
        "fields": [
            {
                "name": "카테고리",
                "value": live["category"],
                "inline": False,
            }
        ],
        "footer": {"text": f"chzzk.naver.com • {live['open_date']}".strip(" •")},
    }

    if live["avatar"]:
        embed["author"]["icon_url"] = live["avatar"]
    if live["thumbnail"]:
        embed["image"] = {"url": live["thumbnail"]}

    role_id = ROLE_BY_CHANNEL.get(live["channel_id"], os.getenv("DISCORD_ROLE_ID", "").strip())
    payload = {
        "username": "치지직 방송 알림",
        "content": f"<@&{role_id}>" if role_id else "",
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [role_id] if role_id else []},
    }

    send_webhook(WEBHOOK_URL, payload)
    logging.info("%s 디스코드 알림 전송 성공", live["name"])


def check_chzzk_once(state):
    """Record each observed live session so restarts do not repeat or lose alerts."""
    for channel_id in load_channels():
        try:
            live = get_live_info(channel_id)
            status = live["status"]
            previous = state.get(channel_id, {})
            if not isinstance(previous, dict):
                previous = {}
            # A completed stream may keep an old liveId. Only use it for OPEN sessions.
            live_id = live["url"].rsplit("/", 1)[-1] if "/live/" in live["url"] else ""
            previously_open = previous.get("status") == "OPEN"
            previous_id = previous.get("live_id", "")
            should_notify = (
                status == "OPEN"
                and bool(previous)
                and (not previously_open or (bool(live_id) and bool(previous_id) and live_id != previous_id))
            )
            if should_notify:
                send_discord_notification(live)
            state[channel_id] = {"status": status, "live_id": live_id if status == "OPEN" else ""}
            save_json(LIVE_STATE_FILE, state)
            logging.info("%s | 상태: %s | 제목: %s", live["name"], status, live["title"])
        except Exception as exc:
            logging.warning("치지직 %s 확인 중 오류: %s", channel_id, exc)


def run_worker(name, checker, interval):
    failures = 0
    while True:
        started = time.monotonic()
        try:
            checker()
            failures = 0
        except Exception as exc:
            failures += 1
            warn_failure(name, failures, exc)
        time.sleep(max(1, interval - (time.monotonic() - started)))


def main():
    if not WEBHOOK_URL.startswith("https://"):
        raise SystemExit("DISCORD_WEBHOOK_URL을 .env에 설정해 주세요.")
    state = load_json(LIVE_STATE_FILE, {})
    if not isinstance(state, dict):
        raise SystemExit("live_state.json 형식이 올바르지 않습니다.")
    # Each checker retains its own internal polling interval.
    workers = [
        ("네이버 카페", check_cafe),
        ("X RSS", check_twitter),
        ("유튜브", check_youtube),
    ]
    for name, checker in workers:
        thread = threading.Thread(
            target=run_worker, args=(name, checker, 5), name=name, daemon=True
        )
        thread.start()
    logging.info("어비스봇 시작: 독립 알림 작업 3개 실행")
    failures = 0
    while True:
        started = time.monotonic()
        try:
            check_chzzk_once(state)
            failures = 0
        except Exception as exc:
            failures += 1
            warn_failure("치지직", failures, exc)
        time.sleep(max(1, POLL_INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
