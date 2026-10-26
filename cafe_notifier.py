import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
load_dotenv(BASE_DIR / ".env")

CAFE_ID = os.getenv("CAFE_ID", "29424353")
MENU_ID = os.getenv("CAFE_MENU_ID", "431")
INTERVAL = max(30, int(os.getenv("CAFE_POLL_INTERVAL", "60")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
ROLE_ID = os.getenv("DISCORD_ROLE_ID", "").strip()
SEEN_FILE = BASE_DIR / "cafe_seen_articles.json"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://cafe.naver.com/",
})

last_check = 0.0


def get_articles():
    url = (
        "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/"
        f"cafes/{CAFE_ID}/menus/{MENU_ID}/articles"
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
                f"/articles/{article_id}?menuid={MENU_ID}"
            ),
        })
    return articles


def load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return [str(value) for value in data]
    except FileNotFoundError:
        return None


def save_seen(ids):
    SEEN_FILE.write_text(
        json.dumps(ids[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def notify(article):
    role_mention = f"<@&{ROLE_ID}>" if ROLE_ID else ""
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
        "footer": {"text": "네이버 카페 새 게시글"},
    }
    payload = {
        "username": "네이버 카페 알림",
        "content": role_mention,
        "embeds": [embed],
        "allowed_mentions": {
            "parse": [],
            "roles": [ROLE_ID] if ROLE_ID else [],
        },
    }
    response = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()
    logging.info("카페 새 글 알림 전송: %s", article["title"])


def check_cafe():
    global last_check
    if time.monotonic() - last_check < INTERVAL:
        return

    articles = get_articles()
    if not articles:
        last_check = time.monotonic()
        logging.info("카페 게시글 목록이 비어 있습니다.")
        return

    seen = load_seen()
    current_ids = [article["id"] for article in articles]
    if seen is None:
        save_seen(current_ids)
        last_check = time.monotonic()
        logging.info("카페 알림 초기 설정 완료: 기존 글 %d개", len(current_ids))
        return

    seen_set = set(seen)
    new_articles = [article for article in articles if article["id"] not in seen_set]
    new_articles.reverse()

    for article in new_articles:
        notify(article)
        seen.insert(0, article["id"])
        seen = seen[:100]
        save_seen(seen)

    last_check = time.monotonic()
    if not new_articles:
        logging.info("카페 새 게시글 없음")
