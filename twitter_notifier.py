"""Poll a public Nitter RSS feed for new original X posts."""
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure
from banner_manager import get_banner
from discord_v2 import payload as v2_payload
from creator_groups import x_group, role_for

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
USERNAME = os.getenv("X_USERNAME", "HanayoHiyori").lstrip("@")
FEED_URL = os.getenv("X_RSS_URL", f"https://nitter.meowing.monster/{USERNAME}/rss")
BACKUP_FEEDS = [v.strip() for v in os.getenv("X_BACKUP_RSS_URLS", f"https://nitter.mie.foundation/{USERNAME}/rss,https://nitter.mizuki.st/{USERNAME}/rss").split(",") if v.strip()]
ALL_FEEDS = list(dict.fromkeys([FEED_URL] + BACKUP_FEEDS))
BACKUP_INTERVAL = max(60, int(os.getenv("X_BACKUP_CHECK_INTERVAL", "120")))
next_backup_check = 0.0
active_feed = FEED_URL
INTERVAL = max(30, int(os.getenv("X_POLL_INTERVAL", "30")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SEEN_FILE = BASE_DIR / "x_seen_posts.json"
X_GROUP = x_group(USERNAME)
X_ROLE_ID = role_for(X_GROUP) if X_GROUP else ""
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


def _fetch_posts(feed_url=None):
    source = feed_url or active_feed
    response = session.get(source, timeout=10)
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
        posts.append({"id": post_id, "text": text, "url": f"https://x.com/{USERNAME}/status/{post_id}",
                      "source": source, "pub_date": item.findtext("pubDate") or ""})
    return sorted(posts, key=lambda p: int(p["id"]))


def _notify(post):
    banner = get_banner(X_GROUP) if X_GROUP else ""
    payload = v2_payload("x", X_ROLE_ID, banner,
                         "X에 새로운 게시글이 올라왔습니다!", "@" + USERNAME,
                         (post["text"] or "새 게시글")[:2500],
                         link_url=post["url"], link_label="원본 게시글 보기",
                         username="X 새 게시글 알림")
    send_webhook(WEBHOOK_URL, payload)


def _fetch_merged_posts(include_backups):
    feeds = ALL_FEEDS if include_backups else [FEED_URL]
    posts_by_id = {}
    errors = []
    for source in feeds:
        try:
            for post in _fetch_posts(source):
                posts_by_id.setdefault(post["id"], post)
        except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
            errors.append((source, exc))
            logging.warning("X RSS 서버 조회 실패: %s (%s)", source, exc)
    if errors and not include_backups:
        for source in BACKUP_FEEDS:
            if source == FEED_URL:
                continue
            try:
                for post in _fetch_posts(source):
                    posts_by_id.setdefault(post["id"], post)
            except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
                errors.append((source, exc))
                logging.warning("X RSS 예비 서버 실패: %s (%s)", source, exc)
    if errors and not posts_by_id:
        raise ValueError("X RSS 서버에서 유효한 게시글을 가져오지 못했습니다")
    return sorted(posts_by_id.values(), key=lambda post: int(post["id"]))


def _post_latency(post):
    try:
        published = parsedate_to_datetime(post["pub_date"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - published).total_seconds()))
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


def check_twitter():
    """Poll primary RSS every interval and compare independent feeds periodically."""
    global next_check, next_backup_check, failures
    now = time.monotonic()
    if now < next_check:
        return
    next_check = now + INTERVAL
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("X 알림 건너뜀: DISCORD_WEBHOOK_URL 설정 필요")
        return
    compare_feeds = now >= next_backup_check and len(ALL_FEEDS) > 1
    if compare_feeds:
        next_backup_check = now + BACKUP_INTERVAL
    try:
        posts = _fetch_merged_posts(compare_feeds)
        if not posts:
            logging.info("X RSS: 원본 게시글 없음")
            failures = 0
            return
        seen = _load_seen()
        if seen is None:
            _save_seen({post["id"] for post in posts})
            logging.info("X RSS 첫 실행: 기존 글 %d개 읽음 처리", len(posts))
            failures = 0
            return
        for post in posts:
            if post["id"] in seen:
                continue
            _notify(post)
            seen.add(post["id"])
            _save_seen(seen)
            logging.info("X 새 게시글 알림 성공: %s | RSS=%s | 작성 후 지연=%s초",
                         post["url"], post.get("source", "?"), _post_latency(post))
        failures = 0
    except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
        failures += 1
        delay = min(300, INTERVAL * (2 ** min(failures, 4)))
        next_check = time.monotonic() + delay
        warn_failure("X RSS", failures, exc)
        logging.warning("X RSS %d초 후 재시도", delay)
