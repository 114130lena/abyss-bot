import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure
from banner_manager import get_banner
from creator_profiles import handle as creator_handle, avatar as creator_avatar
from discord_v2 import alert as unified_alert
from creator_groups import cafe_group, role_for

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
load_dotenv(BASE_DIR / ".env")

CAFE_ID = os.getenv("CAFE_ID", "29424353")
DEFAULT_MENU_ID = os.getenv("CAFE_MENU_ID", "431")
INTERVAL = max(30, int(os.getenv("CAFE_POLL_INTERVAL", "30")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

BOARDS = [
    {
        "menu_id": DEFAULT_MENU_ID,
        "group": cafe_group(DEFAULT_MENU_ID),
        "seen_file": BASE_DIR / "cafe_seen_articles.json",
    },
    {
        "menu_id": "382",
        "group": cafe_group("382"),
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
        # The board-list API may supply a preview image; no extra authenticated scraping.
        image_candidates = [
            item.get("thumbnailImageUrl"), item.get("representImageUrl"),
            item.get("imageUrl"), item.get("thumbnailUrl"),
        ]
        images = []
        for candidate in image_candidates:
            if isinstance(candidate, str) and candidate.startswith("https://") and candidate not in images:
                images.append(candidate)
        articles.append({
            "id": str(article_id),
            "title": item.get("subject") or "제목 없음",
            "author": (item.get("writerInfo") or {}).get("nickName") or "작성자 미상",
            "summary": (item.get("summary") or "").strip(),
            "date": item.get("writeDate") or item.get("createdAt") or "",
            "images": images[:4],
            "url": (
                f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}"
                f"/articles/{article_id}?menuid={menu_id}"
            ),
        })
    return articles



class _CafeHtmlParser(HTMLParser):
    """Extract readable paragraphs and image URLs from public Cafe article HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.images = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("br", "p", "div", "li"):
            self.lines.append("\\n")
        if tag == "img":
            for key in ("data-src", "data-original", "src"):
                value = attrs.get(key, "")
                if value:
                    url = urljoin("https://cafe.naver.com/", value)
                    if urlsplit(url).scheme == "https" and urlsplit(url).hostname:
                        if url not in self.images:
                            self.images.append(url)
                    break

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "li") and not self.skip:
            self.lines.append("\\n")

    def handle_data(self, value):
        if not self.skip:
            self.lines.append(value)

    def get_text(self):
        import re
        result = "".join(self.lines).replace("\\xa0", " ")
        result = re.sub(r" *\\n *", "\\n", result)
        return re.sub(r"\\n{3,}", "\\n\\n", result).strip()


def _article_html(data):
    """Find article content HTML in different Cafe API response envelopes."""
    if isinstance(data, dict):
        for key in ("contentHtml", "contentHTML", "articleContentHtml"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for key in ("article", "result", "message", "content", "data"):
            if key in data:
                found = _article_html(data[key])
                if found:
                    return found
    return ""


def article_detail(article):
    """Try the public article detail API; fall back to the board preview on failure."""
    fallback_text = article.get("summary", "").strip()
    fallback_images = list(article.get("images") or [])[:4]
    try:
        url = f"https://apis.naver.com/cafe-web/cafe-articleapi/v3/cafes/{CAFE_ID}/articles/{article['id']}"
        response = session.get(url, timeout=12)
        response.raise_for_status()
        html = _article_html(response.json())
        if not html:
            raise ValueError("Public article HTML not available")
        parser = _CafeHtmlParser()
        parser.feed(html)
        return parser.get_text() or fallback_text, (parser.images or fallback_images)[:4]
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logging.info("카페 %s 상세 본문 접근 불가; 목록 미리보기 사용: %s",
                     article.get("id"), type(exc).__name__)
        return fallback_text, fallback_images


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
    role_id = role_for(group)
    creator = "하나요 히요리" if group == "hanayo" else "사키하네 후야"
    title = " ".join(article["title"].split())[:300]
    summary, images = article_detail(article)
    payload = unified_alert(
        "cafe", creator, get_banner(group), role_id,
        title=title, body=summary,
        url=article["url"], author=article.get("author", ""),
        board="방송 공지" if str(menu_id) in ("431", "382") else "카페 게시글",
        date=article.get("date", ""), images=images,
        handle=creator_handle(group), avatar=creator_avatar(group))
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
            notify(article, None, menu_id, board["group"])
            seen.insert(0, article["id"])
            seen = seen[:100]
            save_seen(board["seen_file"], seen)

        if not new_articles:
            logging.info("카페 게시판 %s 새 게시글 없음", menu_id)

    last_check = time.monotonic()
