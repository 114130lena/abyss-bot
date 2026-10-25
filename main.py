import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

CHANNEL_ID = os.getenv("CHZZK_CHANNEL_ID", "").strip()
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

API_URL = (
    "https://api.chzzk.naver.com/service/v3.3/"
    f"channels/{CHANNEL_ID}/live-detail"
)

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


def get_live_info():
    response = session.get(API_URL, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 200 or not data.get("content"):
        raise ValueError("치지직 API 응답에 방송 정보가 없습니다.")

    content = data["content"]
    return {
        "status": content.get("status", "UNKNOWN"),
        "title": content.get("liveTitle") or "제목 없음",
        "url": f"https://chzzk.naver.com/live/{content.get('liveId', '')}",
    }


def send_discord_notification(live):
    payload = {
        "username": "치지직 방송 알림",
        "embeds": [{
            "title": "🔴 방송이 시작되었습니다!",
            "description": f"**{live['title']}**",
            "url": live["url"],
            "color": 0x00FFA3,
            "fields": [{
                "name": "채널",
                "value": "하나요 히요리",
                "inline": True,
            }],
            "footer": {"text": "CHZZK Live Notification"},
        }],
        "allowed_mentions": {"parse": []},
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    logging.info("디스코드 알림 전송 성공")


def main():
    if not CHANNEL_ID:
        raise SystemExit("CHZZK_CHANNEL_ID가 .env에 설정되지 않았습니다.")
    if not WEBHOOK_URL.startswith("https://"):
        raise SystemExit("DISCORD_WEBHOOK_URL을 .env에 설정해 주세요.")

    previous_status = None
    logging.info("치지직 방송 알림 봇 시작")

    while True:
        try:
            live = get_live_info()
            status = live["status"]

            logging.info(
                "방송 상태: %s | 제목: %s",
                status,
                live["title"],
            )

            # 첫 확인은 기준 상태로만 사용해 재시작 중 중복 알림을 방지
            if previous_status is not None:
                if status == "OPEN" and previous_status != "OPEN":
                    send_discord_notification(live)

            previous_status = status

        except Exception as error:
            logging.warning("확인 중 오류 발생: %s", error)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
