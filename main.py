import os
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from cafe_notifier import check_cafe

load_dotenv()

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
CHANNELS_FILE = BASE_DIR / "channels.txt"
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

    role_id = os.getenv("DISCORD_ROLE_ID", "").strip()
    payload = {
        "username": "치지직 방송 알림",
        "content": f"<@&{role_id}>" if role_id else "",
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [role_id] if role_id else []},
    }

    response = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()
    logging.info("%s 디스코드 알림 전송 성공", live["name"])


def main():
    if not WEBHOOK_URL.startswith("https://"):
        raise SystemExit("DISCORD_WEBHOOK_URL을 .env에 설정해 주세요.")

    previous_status = {}
    logging.info("치지직 다중 채널 알림 봇 시작")

    while True:
        channel_ids = load_channels()
        for channel_id in channel_ids:
            try:
                live = get_live_info(channel_id)
                status = live["status"]
                logging.info("%s | 상태: %s | 제목: %s", live["name"], status, live["title"])

                # First observation establishes a baseline and avoids a false alert after restart.
                if channel_id in previous_status and status == "OPEN" and previous_status[channel_id] != "OPEN":
                    send_discord_notification(live)

                previous_status[channel_id] = status
            except Exception as error:
                logging.warning("%s 확인 중 오류 발생: %s", channel_id, error)

        try:
            check_cafe()
        except Exception as error:
            logging.warning("네이버 카페 확인 중 오류 발생: %s", error)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
