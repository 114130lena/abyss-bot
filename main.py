import os
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from cafe_notifier import check_cafe
from chzzk_replay_notifier import check_replays
from twitter_notifier import check_twitter
from youtube_notifier import check_youtube
from youtube_websub import start_websub
from banner_manager import get_banner, run_banner_refresh
from discord_v2 import alert as unified_alert
from creator_groups import chzzk_group, role_for
from resilience import load_json, save_json, send_webhook, warn_failure

load_dotenv()

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
CHANNELS_FILE = BASE_DIR / "channels.txt"
LIVE_STATE_FILE = BASE_DIR / "live_state.json"
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
POLL_INTERVAL = max(10, int(os.getenv("POLL_INTERVAL", "10")))

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
    group = chzzk_group(live["channel_id"])
    role_id = role_for(group)
    payload = unified_alert(
        "chzzk", live["name"], get_banner(group), role_id,
        title=live["title"], url=live["url"],
        media_url=live.get("thumbnail", ""),
        category=live.get("category", ""), date=live.get("open_date", ""))
    send_webhook(WEBHOOK_URL, payload)
    logging.info("%s 디스코드 알림 전송 성공", live["name"])


_chzzk_failures = {}


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
            opened_at = live.get("open_date") or ""
            previous_opened_at = previous.get("open_date", "")
            # A new live session can be detected even across a restart.
            changed_session = ((bool(live_id) and bool(previous_id) and live_id != previous_id)
                               or (bool(opened_at) and bool(previous_opened_at) and opened_at != previous_opened_at))
            should_notify = (status == "OPEN" and bool(previous)
                             and (not previously_open or changed_session))
            if should_notify:
                send_discord_notification(live)
            state[channel_id] = {"status": status, "live_id": live_id if status == "OPEN" else "",
                                 "open_date": opened_at if status == "OPEN" else ""}
            save_json(LIVE_STATE_FILE, state)
            _chzzk_failures[channel_id] = 0
            logging.info("%s | 상태: %s | 제목: %s", live["name"], status, live["title"])
        except Exception as exc:
            _chzzk_failures[channel_id] = _chzzk_failures.get(channel_id, 0) + 1
            warn_failure("치지직 " + channel_id, _chzzk_failures[channel_id], exc)


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
    threading.Thread(target=run_banner_refresh, name="youtube-banners", daemon=True).start()
    start_websub()
    state = load_json(LIVE_STATE_FILE, {})
    if not isinstance(state, dict):
        raise SystemExit("live_state.json 형식이 올바르지 않습니다.")
    # Each checker retains its own internal polling interval.
    workers = [
        ("네이버 카페", check_cafe),
        ("X RSS", check_twitter),
        ("유튜브", check_youtube),
        ("치지직 다시보기", check_replays),
    ]
    for name, checker in workers:
        thread = threading.Thread(
            target=run_worker, args=(name, checker, 5), name=name, daemon=True
        )
        thread.start()
    logging.info("어비스봇 시작: 독립 알림 작업 4개 실행")
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
