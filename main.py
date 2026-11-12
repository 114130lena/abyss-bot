import os
import time
import logging
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load production configuration BEFORE importing modules that cache environment
# variables at import time (webhooks, API clients, WebSub receiver, alert routing).
load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")

from cafe_notifier import check_cafe
from chzzk_replay_notifier import check_replays
from twitter_notifier import check_twitter
from twitter_huya_notifier import check_huya_x
from youtube_notifier import check_youtube
from youtube_websub import start_websub
from health_monitor import start as start_health_monitor
from banner_manager import get_banner, run_banner_refresh
from creator_profiles import handle as creator_handle, avatar as creator_avatar
from discord_v2 import alert as unified_alert
from creator_groups import chzzk_group, role_for
from resilience import load_json, save_json, send_webhook, warn_failure

BASE_DIR = Path("/home/ubuntu/chzzk-bot")
CHANNELS_FILE = BASE_DIR / "channels.txt"
LIVE_STATE_FILE = BASE_DIR / "live_state.json"
LIVE_OUTBOX_FILE = BASE_DIR / "chzzk_live_outbox.json"
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
        "live_id": str(live_id) if live_id is not None else "",
        "name": channel.get("channelName") or content.get("channelName") or channel_id,
        "avatar": channel.get("channelImageUrl") or "",
        "status": content.get("status", "UNKNOWN"),
        "title": content.get("liveTitle") or "제목 없음",
        "category": content.get("liveCategoryValue") or "카테고리 정보 없음",
        "thumbnail": thumbnail,
        "open_date": content.get("openDate") or "",
        "url": f"https://chzzk.naver.com/live/{channel_id}",
    }


def live_event_key(live):
    """Stable per-stream identity; never deduplicate solely by channel."""
    session_id = str(live.get("live_id") or live.get("open_date") or "").strip()
    if not session_id:
        raise ValueError("CHZZK_OPEN_WITHOUT_SESSION_ID: cannot safely deduplicate live alert")
    stable = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return "chzzk-live:" + str(live["channel_id"]) + ":" + stable


def send_discord_notification(live):
    group = chzzk_group(live["channel_id"])
    role_id = role_for(group)
    payload = unified_alert(
        "chzzk", live["name"], get_banner(group), role_id,
        title=live["title"], url=live["url"],
        media_url=live.get("thumbnail", ""),
        category=live.get("category", ""), date=live.get("open_date", ""),
        handle=creator_handle(group), avatar=live.get("avatar") or creator_avatar(group),
        chzzk_channel_id=live["channel_id"])
    key = live_event_key(live)
    payload["_abyss_event_key"] = key
    payload["_abyss_source_platform"] = "chzzk-live"
    payload["_abyss_source_time"] = live.get("open_date", "")
    send_webhook(WEBHOOK_URL, payload)
    from discord_event_dedup import get_status
    if get_status(key) != "sent":
        raise RuntimeError("CHZZK_DELIVERY_UNVERIFIED: Discord event is not marked sent")
    logging.info("%s 디스코드 알림 전송 성공", live["name"])
    return True


_live_outbox_lock = threading.RLock()
_live_delivery_inflight = set()
_chzzk_failures = {}


def _read_live_outbox():
    outbox = load_json(LIVE_OUTBOX_FILE, {})
    if not isinstance(outbox, dict):
        raise ValueError("CHZZK_LIVE_OUTBOX_INVALID: manual recovery required")
    return outbox


def queue_live_notification(live):
    """Persist alert intent before advancing live_state; delivery runs off-poll."""
    key = live_event_key(live)
    from discord_event_dedup import get_status
    with _live_outbox_lock:
        outbox = _read_live_outbox()
        if key in outbox:
            return key
        previous = get_status(key)
        if previous is not None:
            # Never retry an already-sent or potentially accepted Discord event.
            logging.warning("CHZZK_EVENT_ALREADY_CLAIMED key=%s status=%s", key, previous)
            return key
        fields = ("channel_id", "live_id", "name", "avatar", "status",
                  "title", "category", "thumbnail", "open_date", "url")
        outbox[key] = {
            "live": {name: live.get(name, "") for name in fields},
            "status": "queued", "retries": 0, "retry_at": 0,
        }
        save_json(LIVE_OUTBOX_FILE, outbox)
    logging.info("CHZZK_LIVE_QUEUED key=%s source_open=%s", key, live.get("open_date", ""))
    return key


def _deliver_live_notification(key):
    """A failed/slow Discord webhook must never block status polling."""
    try:
        with _live_outbox_lock:
            item = _read_live_outbox().get(key)
            if not isinstance(item, dict) or item.get("status") != "queued":
                return
            live = dict(item["live"])
        try:
            send_discord_notification(live)
        except Exception as exc:
            logging.warning("CHZZK_DELIVERY_ERROR key=%s reason=%s", key, type(exc).__name__)
        from discord_event_dedup import get_status
        status = get_status(key)
        with _live_outbox_lock:
            outbox = _read_live_outbox()
            item = outbox.get(key)
            if not isinstance(item, dict):
                return
            if status == "sent":
                outbox.pop(key)
                logging.info("CHZZK_OUTBOX_DELIVERED key=%s", key)
            elif status in ("pending", "uncertain"):
                # A crash/read timeout/5xx may already have pinged Discord.
                # Freeze for human reconciliation instead of duplicate sending.
                item["status"] = "needs_review"
                logging.error("CHZZK_OUTBOX_NEEDS_REVIEW key=%s ledger=%s", key, status)
            else:
                retries = max(0, min(int(item.get("retries", 0)), 30)) + 1
                item["retries"] = retries
                item["retry_at"] = time.time() + min(60, 2 ** min(retries + 1, 6))
                logging.warning("CHZZK_OUTBOX_SAFE_RETRY key=%s attempt=%s", key, retries)
            save_json(LIVE_OUTBOX_FILE, outbox)
    except Exception:
        logging.exception("CHZZK_OUTBOX_DELIVERY_WORKER_FAILURE key=%s", key)
    finally:
        with _live_outbox_lock:
            _live_delivery_inflight.discard(key)


def run_chzzk_delivery():
    """Bounded independent delivery workers, with durable restart recovery."""
    while True:
        try:
            with _live_outbox_lock:
                outbox = _read_live_outbox()
                for key, item in outbox.items():
                    if len(_live_delivery_inflight) >= 4:
                        break
                    if (key in _live_delivery_inflight
                            or not isinstance(item, dict)
                            or item.get("status") != "queued"
                            or float(item.get("retry_at", 0)) > time.time()):
                        continue
                    _live_delivery_inflight.add(key)
                    threading.Thread(target=_deliver_live_notification, args=(key,),
                                     name="chzzk-discord-delivery", daemon=True).start()
        except Exception:
            logging.exception("CHZZK_OUTBOX_DISPATCH_FAILURE")
        time.sleep(1)



def check_chzzk_once(state):
    """Poll channels concurrently; never wait for Discord notification delivery."""
    channels = load_channels()
    if not channels:
        return
    with ThreadPoolExecutor(max_workers=min(4, len(channels))) as pool:
        pending = {pool.submit(get_live_info, channel_id): channel_id
                   for channel_id in channels}
        for future in as_completed(pending):
            channel_id = pending[future]
            try:
                live = future.result()
                status = live["status"]
                previous = state.get(channel_id, {})
                if not isinstance(previous, dict):
                    previous = {}
                live_id = live.get("live_id", "")
                previously_open = previous.get("status") == "OPEN"
                previous_id = previous.get("live_id", "")
                opened_at = live.get("open_date") or ""
                previous_opened_at = previous.get("open_date", "")
                changed_session = ((bool(live_id) and bool(previous_id) and live_id != previous_id)
                                   or (bool(opened_at) and bool(previous_opened_at)
                                       and opened_at != previous_opened_at))
                should_notify = (status == "OPEN" and bool(previous)
                                 and (not previously_open or changed_session))
                if should_notify:
                    queue_live_notification(live)
                state[channel_id] = {
                    "status": status,
                    "live_id": live_id if status == "OPEN" else "",
                    "open_date": opened_at if status == "OPEN" else "",
                }
                save_json(LIVE_STATE_FILE, state)
                _chzzk_failures[channel_id] = 0
                logging.info("%s | 상태: %s | 제목: %s", live["name"], status, live["title"])
            except Exception as exc:
                _chzzk_failures[channel_id] = _chzzk_failures.get(channel_id, 0) + 1
                warn_failure("치지직 " + channel_id, _chzzk_failures[channel_id], exc)


def run_worker(name, checker, interval):
    failures = 0
    while True:
        started = time.monotonic()
        try:
            checker()
            failures = 0
        except Exception as exc:
            failures += 1
            if name == "네이버 카페":
                try:
                    from notice_intelligence import report_source
                    report_source("네이버 카페", False, str(exc))
                except Exception:
                    logging.exception("카페 수집 장애 기록 실패")
            warn_failure(name, failures, exc)
        time.sleep(max(1, interval - (time.monotonic() - started)))


def main():
    if not WEBHOOK_URL.startswith("https://"):
        raise SystemExit("DISCORD_WEBHOOK_URL을 .env에 설정해 주세요.")
    threading.Thread(target=run_banner_refresh, name="youtube-banners", daemon=True).start()
    # Guild banner rotation is independent and explicitly opt-in.
    if os.getenv("SERVER_BANNER_ENABLED", "0") == "1":
        from guild_server_banner import run_server_banner_scheduler
        threading.Thread(target=run_server_banner_scheduler,
                         name="guild-banner-rotation", daemon=True).start()
    start_websub()
    start_health_monitor()
    # Existing clip card only; never creates a new Discord message.
    if (os.getenv("CHZZK_CLIP_CHANNEL_ID", "").isdigit()
            and os.getenv("CHZZK_CLIP_MESSAGE_ID", "").isdigit()):
        from chzzk_clip_digest import run_clip_scheduler
        threading.Thread(target=run_clip_scheduler,
                         name="hanayo-clip-digest", daemon=True).start()
    # Vision analysis runs independently of notification delivery and banner rotation.
    from fanart_vision import enabled as banner_vision_enabled, scan as scan_banner_vision
    if banner_vision_enabled():
        threading.Thread(target=run_worker,
                         args=("히요리 이미지 분석", scan_banner_vision, 1800),
                         name="banner-vision", daemon=True).start()
    # Check public profile feeds every 2 hours. Entries enter a private review
    # queue and can never become public fanart without explicit permission.
    if os.getenv("FANART_DISCOVERY_ENABLED", "1") == "1":
        from fanart_refresh import check_fanart
        threading.Thread(target=run_worker,
                         args=("팬아트 후보 수집", check_fanart, max(1800, int(os.getenv("FANART_DISCOVERY_INTERVAL", "3600")))),
                         name="fanart-discovery", daemon=True).start()
    # Prune only unreferenced, 7-day-old cached image copies, never source
    # records, approved art, notification history or guild banner candidates.
    if os.getenv("FANART_WEEKLY_CLEANUP_ENABLED", "1") == "1":
        from fanart_weekly_cleanup import cleanup as cleanup_fanart_cache
        threading.Thread(target=run_worker,
                         args=("팬아트 캐시 정리", cleanup_fanart_cache, 86400),
                         name="fanart-weekly-cleanup", daemon=True).start()
    state = load_json(LIVE_STATE_FILE, {})
    if not isinstance(state, dict):
        raise SystemExit("live_state.json 형식이 올바르지 않습니다.")
    threading.Thread(target=run_chzzk_delivery, name="chzzk-delivery-outbox",
                     daemon=True).start()
    # Each checker retains its own internal polling interval.
    workers = [
        ("네이버 카페", check_cafe),
        ("X RSS", check_twitter),
        ("후야 X RSS", check_huya_x),
        ("유튜브", check_youtube),
        ("치지직 다시보기", check_replays),
    ]
    # Spaces are independent of RSS reposts. Start only when the official X
    # API credentials and at least one numeric creator ID are configured.
    if (os.getenv("X_BEARER_TOKEN", "").strip()
            and (os.getenv("HANAYO_X_USER_ID", "").strip().isdigit()
                 or os.getenv("SAKIHANE_X_USER_ID", "").strip().isdigit())):
        from x_spaces_notifier import check_spaces
        workers.append(("X 스페이스", check_spaces))
    else:
        logging.info("X_SPACES_WORKER_DISABLED missing_api_credentials_or_creator_ids")
    for name, checker in workers:
        thread = threading.Thread(
            target=run_worker, args=(name, checker, 5), name=name, daemon=True
        )
        thread.start()
    logging.info("어비스봇 시작: 독립 알림 작업 5개 실행")
    failures = 0
    while True:
        started = time.monotonic()
        try:
            check_chzzk_once(state)
            failures = 0
        except Exception as exc:
            failures += 1
            warn_failure("치지직", failures, exc)
        time.sleep(max(1, POLL_INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
