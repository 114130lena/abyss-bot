"""Poll a public Nitter RSS feed for new original X posts."""
import x_feed_health as feed_health
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure
from banner_manager import get_banner
from creator_profiles import handle as creator_handle, avatar as creator_avatar
from discord_v2 import alert as unified_alert
from creator_groups import x_group, role_for

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
USERNAME = os.getenv("X_USERNAME", "HanayoHiyori").lstrip("@")
FEED_URL = os.getenv("X_RSS_URL", f"https://nitter.meowing.monster/{USERNAME}/rss")
BACKUP_FEEDS = [v.strip() for v in os.getenv("X_BACKUP_RSS_URLS", f"https://nitter.mie.foundation/{USERNAME}/rss,https://nitter.mizuki.st/{USERNAME}/rss").split(",") if v.strip()]
ALL_FEEDS = list(dict.fromkeys([FEED_URL] + BACKUP_FEEDS))
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



class _PostTextParser(HTMLParser):
    """Extract post text from Nitter RSS HTML while preserving paragraphs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in ("p", "div", "blockquote") and self.parts:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "blockquote"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _normalize_post_text(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return re.sub(r"\n{4,}", "\n\n\n", value).strip()


def _post_body(item):
    """Prefer RSS description; title fallback if HTML is empty."""
    description = item.findtext("description") or ""
    if description.strip():
        parser = _PostTextParser()
        parser.feed(description)
        body = _normalize_post_text("".join(parser.parts))
        if body:
            return body
    return _normalize_post_text(unescape(item.findtext("title") or ""))



def _post_images(item):
    """Parse X image URLs from HTML and standard RSS media tags."""
    from urllib.parse import urlparse
    urls = []
    def add(value):
        value = unescape(str(value or "")).strip()
        if value.startswith("//"):
            value = "https:" + value
        p = urlparse(value)
        if p.scheme == "https" and p.hostname in ("pbs.twimg.com", "abs.twimg.com") and "/media/" in p.path and value not in urls:
            urls.append(value)
    class Finder(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in ("img", "source", "a"):
                return
            values = dict(attrs)
            for key in ("src", "data-src", "data-original", "data-original-src", "data-image", "href"):
                add(values.get(key))
            for key in ("srcset", "data-srcset"):
                for candidate in (values.get(key) or "").split(","):
                    add(candidate.strip().split(" ")[0])
    Finder().feed(item.findtext("description") or "")
    for node in item.iter():
        name = node.tag.rsplit("}", 1)[-1].lower()
        if name in ("enclosure", "content", "thumbnail"):
            add(node.attrib.get("url"))
    return urls[:4]


def _fetch_posts(feed_url=None):
    source = feed_url or FEED_URL
    response = requests.get(source, headers={"User-Agent": "abyss-bot/1.0 (RSS polling)"}, timeout=(2.5, 4))
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
        if not post_id.isdigit() or (not _original_post(item) and not __import__("x_repost_notifier").is_repost(item.findtext("title") or "", item.findtext("description") or "")):
            continue
        text = _post_body(item)
        posts.append({"id": post_id, "text": text, "url": f"https://x.com/{USERNAME}/status/{post_id}",
                      "source": source, "pub_date": item.findtext("pubDate") or "",
                      "images": _post_images(item), "rss_title": item.findtext("title") or "", "source_link": item.findtext("link") or "", "rss_description": item.findtext("description") or "", "repost": __import__("x_repost_notifier").is_repost(item.findtext("title") or "", item.findtext("description") or "")})
    return sorted(posts, key=lambda p: int(p["id"]))


def _notify(post):
    from x_post_media import resolve_media
    photo_urls, video_preview, has_video, media_known = resolve_media(post, USERNAME)
    if photo_urls:
        post = dict(post, images=photo_urls)
    # Preserve safety if metadata is unavailable: never mislabel a potentially
    # illustrated X post as confirmed text-only and display unrelated fanart.
    uncertain_photo = not media_known
    banner = get_banner(X_GROUP) if X_GROUP else ""
    payload = unified_alert(
        "x", "하나요 히요리", banner, X_ROLE_ID,
        body=(post["text"] or "새 게시글")[:2500],
        url=post["url"], date=post.get("pub_date", ""),
        images=post.get("images", []), media_url=video_preview,
        handle=creator_handle(X_GROUP), avatar=creator_avatar(X_GROUP))
    if has_video:
        payload["_abyss_has_video"] = True
    if has_video:
        # A stable clickable fallback even when the public thumbnail fails to render.
        from urllib.parse import urlsplit
        for c in payload.get("components",[]):
            if c.get("type")==17:
                for part in c.get("components",[]):
                    if part.get("type")==10 and "𝕏 X" in str(part.get("content","")):
                        part["content"] += "\n▶️ [원본 X 영상 보기]("+post["url"]+")"
                        break
    if uncertain_photo:
        payload["_abyss_photo_unknown"] = True
    if media_known and not photo_urls and not has_video and not video_preview:
        payload["_abyss_verified_text_only"] = True
    logging.info("X_SEND_MEDIA_GUARD creator=%s post=%s photos=%d video=%s known=%s text_only=%s",
                 USERNAME,post.get("id"),len(photo_urls),has_video,media_known,
                 bool(payload.get("_abyss_verified_text_only")))
    send_webhook(WEBHOOK_URL, payload)
    try:
        from notice_intelligence import record_schedule
        record_schedule(X_GROUP, "", post.get("text", ""), post.get("pub_date", ""), post["url"])
    except Exception:
        logging.exception("히요리 X 일정 추적 오류(알림은 정상 전송)")


def _fx_image_map(username):
    """Fallback account RSS media, matched by exact post ID."""
    feed = "https://fxtwitter.com/" + username + "/feed.xml"
    response = requests.get(feed, timeout=12, params={"count": 100},
                            headers={"User-Agent": "abyss-bot/1.0 (RSS)"})
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("Oversize RSS")
    root = ET.fromstring(response.content)
    found = {}
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        match = re.search(r"/status/([0-9]+)", link)
        if match:
            images = _post_images(item)
            if images:
                found[match.group(1)] = images
    return found


def _fetch_merged_posts(include_backups=True):
    """Poll all configured RSS sources concurrently and deduplicate post IDs."""
    posts_by_id = {}
    successes = 0
    feeds = list(ALL_FEEDS)
    if not feeds:
        raise ValueError("X RSS 서버가 설정되지 않았습니다")
    with ThreadPoolExecutor(max_workers=min(len(feeds), 5)) as pool:
        futures = {pool.submit(feed_health.timed_fetch, _fetch_posts, source): source for source in feeds if feed_health.active(source)}
        from x_fx_feed import fetch_creator_originals
        fx_url = "https://fxtwitter.com/" + USERNAME + "/feed.xml"
        if feed_health.active(fx_url):
            futures[pool.submit(feed_health.timed_fetch, lambda _url: fetch_creator_originals(USERNAME, __import__("sys").modules[__name__]), fx_url)] = fx_url
        for future in as_completed(futures):
            source = futures[future]
            try:
                items = future.result()
                successes += 1
                for post in items:
                    previous = posts_by_id.get(post["id"])
                    if previous is None or (post.get("repost"),len(post.get("images", []))) > (previous.get("repost"),len(previous.get("images", []))):
                        posts_by_id[post["id"]] = post
            except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
                logging.warning("X RSS 서버 조회 실패: %s (%s)", source, exc)
    if not successes:
        raise ValueError("모든 X RSS 서버 조회 실패")
    # Do not block delivery on optional FxEmbed image lookups.
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
    """Poll all configured RSS servers every interval; deduplicate by post ID."""
    global next_check, failures
    now = time.monotonic()
    if now < next_check:
        return
    next_check = now + INTERVAL
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("X 알림 건너뜀: DISCORD_WEBHOOK_URL 설정 필요")
        return
    try:
        posts = _fetch_merged_posts(True)
        if not posts:
            logging.info("X RSS: 원본 게시글 없음")
            failures = 0
            try:
                from notice_intelligence import report_source
                report_source("히요리 X", True)
            except Exception:
                logging.exception("X 수집 진단 기록 실패")
            return
        seen = _load_seen()
        if seen is None:
            _save_seen({post["id"] for post in posts})
            logging.info("X RSS 첫 실행: 기존 글 %d개 읽음 처리", len(posts))
            failures = 0
            return
        from x_repost_notifier import is_repost_post, notify as notify_repost
        repost_file=BASE_DIR / "x_repost_seen_posts.json"
        try:
            repost_seen=set(json.loads(repost_file.read_text(encoding="utf-8")))
        except (OSError,ValueError,TypeError):
            repost_seen={p["id"] for p in posts if is_repost_post(p, USERNAME)}
            repost_file.write_text(json.dumps(sorted(repost_seen)),encoding="utf-8")
        for post in posts:
            if is_repost_post(post, USERNAME):
                if post["id"] not in repost_seen:
                    logging.info("X_REPOST_DISCOVERED creator=hanayo id=%s source=%s",post["id"],post.get("source","?"))
                    notify_repost(X_GROUP,post,WEBHOOK_URL)
                    repost_seen.add(post["id"])
                    repost_file.write_text(json.dumps(sorted(repost_seen,key=int)[-500:]),encoding="utf-8")
                continue
            if post["id"] in seen:
                continue
            _notify(post)
            seen.add(post["id"])
            _save_seen(seen)
            logging.info("X 새 게시글 알림 성공: %s | RSS=%s | 작성 후 지연=%s초",
                         post["url"], post.get("source", "?"), _post_latency(post))
        failures = 0
        try:
            from notice_intelligence import report_source
            report_source("히요리 X", True)
        except Exception:
            logging.exception("히요리 X 진단 기록 실패")
    except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
        failures += 1
        try:
            from notice_intelligence import report_source
            report_source("히요리 X", False, str(exc))
        except Exception:
            logging.exception("X 진단 기록 실패")
        delay = min(300, INTERVAL * (2 ** min(failures, 4)))
        next_check = time.monotonic() + delay
        warn_failure("X RSS", failures, exc)
        logging.warning("X RSS %d초 후 재시도", delay)
