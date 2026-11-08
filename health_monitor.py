"""Periodic offline health snapshots. No extra network calls and no Discord spam."""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
MONITOR_INTERVAL = max(300, int(os.getenv("ABYSS_HEALTH_INTERVAL_SECONDS", "1800")))


def _read(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (ValueError, OSError):
        return default


def snapshot():
    """Report only counts and staleness, never keys, Discord tokens or user content."""
    now = time.time()
    files = {
        "youtube_seen": ROOT / "youtube_seen_videos.json",
        "x_hanayo_seen": ROOT / "x_seen_posts.json",
        "x_huya_seen": ROOT / "x_huya_seen_posts.json",
        "cafe_hanayo_seen": ROOT / "cafe_seen_articles.json",
        "cafe_huya_seen": ROOT / "cafe_seen_articles_382.json",
        "chzzk_live": ROOT / "live_state.json",
        "chzzk_replay": ROOT / "chzzk_replay_seen.json",
    }
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "states": {}, "fanart": {}, "banner": {}}
    for name, path in files.items():
        if path.is_file():
            result["states"][name] = {"age_hours": round((now - path.stat().st_mtime) / 3600, 1)}
        else:
            result["states"][name] = {"missing": True}
    queue_path = ROOT / "creator_media" / "fanart_discovery.json"
    queue = _read(queue_path, {})
    for group in ("hanayo", "sakihane"):
        items = [v for v in queue.values() if isinstance(v, dict) and v.get("group") == group]
        result["fanart"][group] = {
            "posts": len(items),
            "cached_images": sum(len(v.get("cached_files") or []) for v in items),
        }
    if queue_path.is_file():
        result["fanart"]["queue_age_hours"] = round((now - queue_path.stat().st_mtime) / 3600, 1)
    banner_path = ROOT / "creator_media" / "guild_banner_rotation_state.json"
    banner = _read(banner_path, {})
    result["banner"] = {"last_slot": banner.get("last_slot"),
                        "used_count": len(banner.get("rotation_used") or [])}
    from delivery_review import count_pending
    result["discord_uncertain_deliveries"] = count_pending()
    try:
        from guild_server_banner import collected_hanayo_art
        result["banner"]["collected_candidates"] = len(collected_hanayo_art())
    except (OSError, ValueError, ImportError) as exc:
        logging.warning("ABYSS_BANNER_CANDIDATE_COUNT_UNAVAILABLE reason=%s", type(exc).__name__)
    from fanart_vision import enabled as vision_enabled, ready as vision_ready
    result["banner"]["vision_enabled"] = vision_enabled()
    result["banner"]["vision_ready"] = vision_ready()
    return result


def run():
    while True:
        try:
            logging.info("ABYSS_HEALTH_SNAPSHOT %s", json.dumps(snapshot(), ensure_ascii=False, separators=(",", ":")))
        except Exception:
            logging.exception("ABYSS_HEALTH_SNAPSHOT_FAILED")
        time.sleep(MONITOR_INTERVAL)


def start():
    threading.Thread(target=run, name="abyss-health", daemon=True).start()
