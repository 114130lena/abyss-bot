import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure
from banner_manager import get_banner

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
load_dotenv(BASE_DIR / ".env")

CAFE_ID = os.getenv("CAFE_ID", "29424353")
DEFAULT_MENU_ID = os.getenv("CAFE_MENU_ID", "431")
INTERVAL = max(30, int(os.getenv("CAFE_POLL_INTERVAL", "30")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

BOARDS = [
    {
        "menu_id": DEFAULT_MENU_ID,
        "role_id": "1563499860135772211",
        "group": "hanayo",
        "seen_file": BASE_DIR / "cafe_seen_articles.json",
    },
    {
        "menu_id": "382",
        "role_id": "1564279305171640361",
        "group": "sakihane",
        "seen_file": BASE_DIR / "cafe_seen_articles_382.json",
    },
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://cafe.naver.com/",
})

last_check = 0.0


def get_articles(menu_id):
    url = (
        "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/"
        f"cafes/{CAFE_ID}/menus/{menu_id}/articles"
    )
    response = session.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()
    entries = (data.get("result") or {}).get("articleList") or []
    articles = []

    for entry in entries:
        item = entry.get("item") or {}
        article_id = item.get("articleId")
        if article_id is None or item.get("blindArticle"):
            continue
        articles.append({
            "id": str(article_id),
            "title": item.get("subject") or "제목 없음",
            "author": (item.get("writerInfo") or {}).get("nickName") or "작성자 미상",
            "summary": (item.get("summary") or "").strip(),
            "url": (
                f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}"
                f"/articles/{article_id}?menuid={menu_id}"
            ),
        })
    return articles


def load_seen(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(value) for value in data]
    except FileNotFoundError:
        return None


def save_seen(path, ids):
    path.write_text(
        json.dumps(ids[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def notify(article, role_id, menu_id, group):
    role_mention = f"<@&{role_id}>" if role_id else ""
    summary = article["summary"]
    if len(summary) > 1000:
        summary = summary[:997] + "..."

    embed = {
        "title": article["title"][:256],
        "url": article["url"],
        "description": summary or "게시글 미리보기가 없습니다.",
        "color": 0x03C75A,
        "fields": [{
            "name": "작성자",
            "value": article["author"][:256],
            "inline": True,
        }],
        "footer": {"text": f"네이버 카페 새 게시글 • 게시판 {menu_id}"},
    }
    banner = get_banner(group)
    embeds = ([{"color": 0x03C75A, "image": {"url": banner}}] if banner.startswith("https://") else [])
    embeds.append(embed)
    payload = {
        "username": "네이버 카페 알림",
        "content": role_mention,
        "embeds": embeds,
        "allowed_mentions": {
            "parse": [],
            "roles": [role_id] if role_id else [],
        },
    }
    send_webhook(WEBHOOK_URL, payload)
    logging.info("카페 게시판 %s 새 글 알림 전송: %s", menu_id, article["title"])


def check_cafe():
    global last_check
    if time.monotonic() - last_check < INTERVAL:
        return

    if not WEBHOOK_URL.startswith("https://"):
        raise ValueError("DISCORD_WEBHOOK_URL이 설정되지 않았습니다.")

    for board in BOARDS:
        menu_id = board["menu_id"]
        articles = get_articles(menu_id)
        if not articles:
            logging.info("카페 게시판 %s 게시글 목록이 비어 있습니다.", menu_id)
            continue

        seen = load_seen(board["seen_file"])
        current_ids = [article["id"] for article in articles]
        if seen is None:
            save_seen(board["seen_file"], current_ids)
            logging.info("카페 게시판 %s 초기 설정 완료: 기존 글 %d개", menu_id, len(current_ids))
            continue

        seen_set = set(seen)
        new_articles = [article for article in articles if article["id"] not in seen_set]
        new_articles.reverse()

        for article in new_articles:
            notify(article, board["role_id"], menu_id, board["group"])
            seen.insert(0, article["id"])
            seen = seen[:100]
            save_seen(board["seen_file"], seen)

        if not new_articles:
            logging.info("카페 게시판 %s 새 게시글 없음", menu_id)

    last_check = time.monotonic()
