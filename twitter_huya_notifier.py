"""Independent Huya X RSS watcher; reuses the existing validated RSS parsing helpers."""
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv
import twitter_notifier as common
from creator_groups import role_for, x_group
from creator_profiles import handle as creator_handle, avatar as creator_avatar
from banner_manager import get_banner
from discord_v2 import alert as unified_alert
from resilience import send_webhook, warn_failure

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
USERNAME = "SakihaneHuya"
GROUP = x_group(USERNAME)
assert GROUP == "sakihane"
ROLE = role_for(GROUP)
SEEN_FILE = BASE_DIR / "x_huya_seen_posts.json"
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
INTERVAL = max(30, int(os.getenv("X_POLL_INTERVAL", "30")))
FEEDS = list(dict.fromkeys([
    f"https://nitter.meowing.monster/{USERNAME}/rss",
    f"https://nitter.mie.foundation/{USERNAME}/rss",
    f"https://nitter.mizuki.st/{USERNAME}/rss",
]))
next_check = 0.0
failures = 0


def _fetch_posts(feed):
    response = requests.get(feed, timeout=8, headers={"User-Agent": "abyss-bot/1.0 (RSS polling)"})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    if root.tag != "rss":
        raise ValueError("Invalid RSS")
    channel = root.find("channel")
    if channel is None or ("@" + USERNAME).casefold() not in (channel.findtext("title") or "").casefold():
        raise ValueError("X RSS account mismatch")
    posts = []
    for item in channel.findall("item"):
        post_id = (item.findtext("guid") or "").strip()
        if not post_id.isdigit() or not common._original_post(item):
            continue
        posts.append({
            "id": post_id, "text": common._post_body(item),
            "url": f"https://x.com/{USERNAME}/status/{post_id}",
            "images": common._post_images(item),
            "pub_date": item.findtext("pubDate") or "",
            "source": feed,
        })
    return posts


def _fetch_merged_posts():
    posts = {}
    successes = 0
    with ThreadPoolExecutor(max_workers=min(3, len(FEEDS))) as pool:
        jobs = {pool.submit(_fetch_posts, url): url for url in FEEDS}
        for job in as_completed(jobs):
            try:
                items = job.result()
                successes += 1
                for item in items:
                    posts.setdefault(item["id"], item)
            except (requests.RequestException, ValueError, ET.ParseError, OSError) as exc:
                logging.warning("후야 X RSS 소스 실패 %s: %s", jobs[job], exc)
    if successes == 0:
        raise ValueError("All Huya X RSS sources failed")
    return sorted(posts.values(), key=lambda item: int(item["id"]))


def _load_seen():
    try:
        state = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, list):
            raise ValueError("Huya X seen state must be a list")
        return set(str(x) for x in state)
    except FileNotFoundError:
        return None


def _save_seen(ids):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SEEN_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(sorted(ids, key=int)[-200:]), encoding="utf-8")
    temp.replace(SEEN_FILE)


def _notify(post):
    payload = unified_alert(
        "x", "사키하네 후야", get_banner(GROUP), ROLE,
        body=(post["text"] or "새 게시글")[:2500],
        url=post["url"], date=post.get("pub_date", ""),
        images=post.get("images", []),
        handle=creator_handle(GROUP), avatar=creator_avatar(GROUP))
    send_webhook(WEBHOOK_URL, payload)


def check_huya_x():
    global next_check, failures
    now = time.monotonic()
    if now < next_check:
        return
    next_check = now + INTERVAL
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("후야 X 알림 건너뜀: 웹훅 미설정")
        return
    try:
        posts = _fetch_merged_posts()
        if not posts:
            failures = 0
            return
        seen = _load_seen()
        if seen is None:
            _save_seen({post["id"] for post in posts})
            logging.info("후야 X 감시 초기화: 기존 %d개 읽음 처리", len(posts))
            failures = 0
            return
        for post in posts:
            if post["id"] in seen:
                continue
            _notify(post)
            seen.add(post["id"])
            _save_seen(seen)
            logging.info("후야 X 새 글 알림: %s", post["url"])
        failures = 0
    except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
        failures += 1
        next_check = time.monotonic() + min(300, INTERVAL * (2 ** min(failures, 4)))
        warn_failure("후야 X RSS", failures, exc)
