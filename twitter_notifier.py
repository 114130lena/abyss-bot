"""Poll a public Nitter RSS feed for new original X posts."""
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
USERNAME = os.getenv("X_USERNAME", "HanayoHiyori").lstrip("@")
FEED_URL = os.getenv("X_RSS_URL", f"https://nitter.meowing.monster/{USERNAME}/rss")
BACKUP_FEEDS = [v.strip() for v in os.getenv("X_BACKUP_RSS_URLS", "").split(",") if v.strip()]
active_feed = FEED_URL
INTERVAL = max(30, int(os.getenv("X_POLL_INTERVAL", "30")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SEEN_FILE = BASE_DIR / "x_seen_posts.json"
X_ROLE_ID = os.getenv("X_DISCORD_ROLE_ID", "1563499860135772211").strip()
X_BANNER_URL = os.getenv("HANAYO_BANNER_URL", "").strip()
session = requests.Session()
session.headers.update({"User-Agent": "abyss-bot/1.0 (RSS polling)"})
next_check = 0.0
failures = 0


def _load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Seen posts state must be a list")
        return set(map(str, data))
    except FileNotFoundError:
        return None


def _save_seen(ids):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(ids, key=int)[-200:], ensure_ascii=False), encoding="utf-8")
    tmp.replace(SEEN_FILE)


def _original_post(item):
    title = (item.findtext("title") or "").strip()
    description = item.findtext("description") or ""
    # Nitter may label reposts/replies in the title. Quotes contain a quoted block.
    if re.match(r"^(?:RT by|R to|Retweeted by|Replying to)\b", title, re.I):
        return False
    if "<blockquote" in description.lower():
        return False
    return True


def _fetch_posts():
    response = session.get(active_feed, timeout=15)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    if root.tag != "rss":
        raise ValueError("X RSS response is not RSS XML")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("X RSS channel missing")
    title = channel.findtext("title") or ""
    if f"@{USERNAME}".casefold() not in title.casefold():
        raise ValueError("X RSS feed account does not match configured account")
    posts = []
    for item in channel.findall("item"):
        post_id = (item.findtext("guid") or "").strip()
        if not post_id.isdigit() or not _original_post(item):
            continue
        text = unescape((item.findtext("title") or "").strip())
        posts.append({"id": post_id, "text": text, "url": f"https://x.com/{USERNAME}/status/{post_id}"})
    return sorted(posts, key=lambda p: int(p["id"]))


def _notify(post):
    embed = {
        "title": f"@{USERNAME} 새 게시글",
        "description": (post["text"] or "새 게시글")[:4000],
        "url": post["url"],
        "color": 0x1DA1F2,
        "footer": {"text": "X · 공개 RSS 피드"},
    }
    if X_BANNER_URL.startswith("https://"):
        embed["image"] = {"url": X_BANNER_URL}
    payload = {
        "username": "X 새 게시글 알림",
        "content": f"<@&{X_ROLE_ID}>" if X_ROLE_ID else "",
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [X_ROLE_ID] if X_ROLE_ID else []},
    }
    send_webhook(WEBHOOK_URL, payload)


def check_twitter():
    """Nonblocking interval/backoff guard; call once per existing bot loop."""
    global next_check, failures, active_feed
    now = time.monotonic()
    if now < next_check:
        return
    next_check = now + INTERVAL
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("X 알림 건너뜀: DISCORD_WEBHOOK_URL 설정 필요")
        return
    try:
        posts = _fetch_posts()
        if not posts:
            logging.info("X RSS: 원본 게시글 없음")
            return
        seen = _load_seen()
        if seen is None:
            _save_seen({p["id"] for p in posts})
            logging.info("X RSS 첫 실행: 기존 글 %d개를 읽음 처리", len(posts))
            failures = 0
            return
        for post in posts:
            if post["id"] in seen:
                continue
            _notify(post)
            seen.add(post["id"])
            _save_seen(seen)
            logging.info("X 새 게시글 알림 성공: %s", post["url"])
        failures = 0
        if active_feed != FEED_URL:
            logging.info("X RSS backup feed operational: %s", active_feed)
    except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
        failures += 1
        delay = min(1800, INTERVAL * (2 ** min(failures, 6)))
        next_check = time.monotonic() + delay
        feeds = [FEED_URL] + BACKUP_FEEDS
        if failures >= 2 and len(feeds) > 1:
            active_feed = feeds[(feeds.index(active_feed) + 1) % len(feeds)] if active_feed in feeds else FEED_URL
            logging.warning("X RSS 예비 서버 전환: %s", active_feed)
        warn_failure("X RSS", failures, exc)
        logging.warning("X RSS %d초 후 재시도", delay)
