"""Independent Huya X RSS watcher; reuses the existing validated RSS parsing helpers."""
import x_feed_health as feed_health
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
    response = requests.get(feed, timeout=(2.5, 4), headers={"User-Agent": "abyss-bot/1.0 (RSS polling)"})
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
        if not post_id.isdigit() or (not common._original_post(item) and not __import__("x_repost_notifier").is_repost(item.findtext("title") or "", item.findtext("description") or "")):
            continue
        posts.append({
            "id": post_id, "text": common._post_body(item),
            "url": f"https://x.com/{USERNAME}/status/{post_id}",
            "images": common._post_images(item),
            "pub_date": item.findtext("pubDate") or "",
            "source": feed, "rss_title": item.findtext("title") or "", "source_link": item.findtext("link") or "", "rss_description": item.findtext("description") or "", "repost": __import__("x_repost_notifier").is_repost(item.findtext("title") or "", item.findtext("description") or ""),
        })
    return posts


def _fetch_merged_posts():
    posts = {}
    successes = 0
    with ThreadPoolExecutor(max_workers=min(3, len(FEEDS))) as pool:
        jobs = {pool.submit(feed_health.timed_fetch, _fetch_posts, url): url for url in FEEDS if feed_health.active(url)}
        from x_fx_feed import fetch_creator_originals
        fx_url = "https://fxtwitter.com/" + USERNAME + "/feed.xml"
        if feed_health.active(fx_url):
            jobs[pool.submit(feed_health.timed_fetch, lambda _url: fetch_creator_originals(USERNAME, common), fx_url)] = fx_url
        if not jobs:
            logging.info("X_ALL_SOURCES_COOLING_DOWN creator=%s retry_in_seconds=%d",
                         USERNAME, max(1, int(feed_health.retry_after(FEEDS + [fx_url]))))
            return None
        for job in as_completed(jobs):
            try:
                items = job.result()
                successes += 1
                for item in items:
                    previous = posts.get(item["id"])
                    if previous is None or (item.get("repost"),len(item.get("images", []))) > (previous.get("repost"),len(previous.get("images", []))):
                        posts[item["id"]] = item
            except (requests.RequestException, ValueError, ET.ParseError, OSError) as exc:
                logging.warning("후야 X RSS 소스 실패 %s: %s", jobs[job], exc)
    if successes == 0:
        raise ValueError("All Huya X RSS sources failed")
    # Do not block alert delivery on optional FxEmbed media enrichment.
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
    # Final safety gate: reposts must NEVER enter the normal broadcast webhook,
    # even if a future poller inadvertently invokes this function directly.
    from x_repost_notifier import is_repost_post
    if is_repost_post(post, USERNAME):
        raise ValueError("Repost cannot be sent via ordinary X notification")
    from x_post_media import resolve_media
    photo_urls, video_preview, has_video, media_known = resolve_media(post, USERNAME)
    if photo_urls:
        post = dict(post, images=photo_urls)
    # Preserve safety if metadata is unavailable: never mislabel a potentially
    # illustrated X post as confirmed text-only and display unrelated fanart.
    uncertain_photo = not media_known
    payload = unified_alert(
        "x", "사키하네 후야", get_banner(GROUP), "",
        body=(post["text"] or "새 게시글")[:2500],
        url=post["url"], date=post.get("pub_date", ""),
        images=post.get("images", []), media_url=video_preview,
        handle=creator_handle(GROUP), avatar=creator_avatar(GROUP))
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
        record_schedule(GROUP, "", post.get("text", ""), post.get("pub_date", ""), post["url"])
    except Exception:
        logging.exception("후야 X 일정 추적 오류(알림은 정상 전송)")


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
        if posts is None:
            # Every provider is in its circuit-breaker period: neither a success nor a new failure.
            sources = FEEDS + ["https://fxtwitter.com/" + USERNAME + "/feed.xml"]
            next_check = time.monotonic() + max(INTERVAL, min(300, int(feed_health.retry_after(sources)) + 1))
            return
        if not posts:
            failures = 0
            try:
                from notice_intelligence import report_source
                report_source("후야 X", True)
            except Exception:
                logging.exception("X 수집 진단 기록 실패")
            return
        seen = _load_seen()
        if seen is None:
            _save_seen({post["id"] for post in posts})
            logging.info("후야 X 감시 초기화: 기존 %d개 읽음 처리", len(posts))
            failures = 0
            return
        from x_repost_notifier import is_repost_post, notify as notify_repost
        repost_file=BASE_DIR / "x_huya_repost_seen_posts.json"
        try:
            repost_seen=set(json.loads(repost_file.read_text(encoding="utf-8")))
        except (OSError,ValueError,TypeError):
            repost_seen={p["id"] for p in posts if is_repost_post(p, USERNAME)}
            repost_file.write_text(json.dumps(sorted(repost_seen)),encoding="utf-8")
        for post in posts:
            if is_repost_post(post, USERNAME):
                if post["id"] not in repost_seen:
                    logging.info("X_REPOST_DISCOVERED creator=sakihane id=%s source=%s",post["id"],post.get("source","?"))
                    notify_repost(GROUP,post,WEBHOOK_URL)
                    repost_seen.add(post["id"])
                    repost_file.write_text(json.dumps(sorted(repost_seen,key=int)[-500:]),encoding="utf-8")
                continue
            if post["id"] in seen:
                continue
            _notify(post)
            seen.add(post["id"])
            _save_seen(seen)
            logging.info("후야 X 새 글 알림: %s", post["url"])
        failures = 0
        try:
            from notice_intelligence import report_source
            report_source("후야 X", True)
        except Exception:
            logging.exception("후야 X 진단 기록 실패")
    except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
        failures += 1
        try:
            from notice_intelligence import report_source
            report_source("후야 X", False, str(exc))
        except Exception:
            logging.exception("X 진단 기록 실패")
        next_check = time.monotonic() + min(300, INTERVAL * (2 ** min(failures, 4)))
        warn_failure("후야 X RSS", failures, exc)
