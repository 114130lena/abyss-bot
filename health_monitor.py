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
    fx_path = ROOT / "creator_media" / "fanart_fx_search_state.json"
    fx_state = _read(fx_path, {})
    result["fanart"]["hashtag_search"] = fx_state.get("groups", {})
    if fx_path.is_file():
        result["fanart"]["search_age_hours"] = round((now - fx_path.stat().st_mtime) / 3600, 1)
    banner_path = ROOT / "creator_media" / "guild_banner_rotation_state.json"
    banner = _read(banner_path, {})
    result["banner"] = {"last_slot": banner.get("last_slot"),
                        "used_count": len(banner.get("rotation_used") or []),
                        "change_time_known": bool(banner.get("banner_changed_at")),
                        "source_known": bool(banner.get("artist") and banner.get("post_url")),
                        "audit_status": banner.get("banner_audit_status", "not_checked"),
                        "source_match_status": banner.get("banner_source_match_status", "not_checked")}
    from delivery_review import count_pending
    result["discord_uncertain_deliveries"] = count_pending()
    try:
        from guild_server_banner import collected_hanayo_art, approved_hanayo_art
        result["banner"]["collected_candidates"] = len(collected_hanayo_art())
        result["banner"]["approved_candidates"] = len(approved_hanayo_art())
    except (OSError, ValueError, ImportError) as exc:
        logging.warning("ABYSS_BANNER_CANDIDATE_COUNT_UNAVAILABLE reason=%s", type(exc).__name__)
    from fanart_vision import enabled as vision_enabled, ready as vision_ready
    result["banner"]["vision_enabled"] = vision_enabled()
    result["banner"]["vision_ready"] = vision_ready()
    return result


def health_warnings(report):
    """Only status and counts, not user text or secrets, enter admin alerts."""
    warnings = {}
    banner = report.get("banner", {})
    if os.getenv("SERVER_BANNER_ENABLED", "0") == "1":
        if ("collected_candidates" in banner and "approved_candidates" in banner
                and (banner["collected_candidates"] + banner["approved_candidates"])
                < max(2, int(os.getenv("SERVER_BANNER_MIN_CANDIDATES", "2")))):
            warnings["server_banner_pool"] = "서버 배너 후보가 최소 개수보다 부족합니다."
    if os.getenv("FANART_FX_SEARCH_ENABLED", "1") == "1":
        search = report.get("fanart", {}).get("hashtag_search", {})
        if not search or any(v.get("status") != "ok" for v in search.values()
                             if isinstance(v, dict)):
            warnings["fanart_hashtag_search"] = "팬아트 전체 해시태그 검색이 성공 기록을 남기지 못했습니다."
        if report.get("fanart", {}).get("search_age_hours", 0) > 3:
            warnings["fanart_hashtag_search"] = "팬아트 전체 해시태그 검색 상태 기록이 3시간 이상 오래됐습니다."
    return warnings


def run():
    failures = {}
    while True:
        try:
            report = snapshot()
            logging.info("ABYSS_HEALTH_SNAPSHOT %s", json.dumps(report, ensure_ascii=False, separators=(",", ":")))
            warnings = health_warnings(report)
            for name in set(failures) | set(warnings):
                if name not in warnings:
                    failures[name] = 0
                    continue
                failures[name] = failures.get(name, 0) + 1
                # Alerts are opt-in via ADMIN_DISCORD_WEBHOOK_URL, throttled in
                # warn_failure; never send them to public notification threads.
                if failures[name] >= 3:
                    from resilience import warn_failure
                    warn_failure(name, failures[name], RuntimeError(warnings[name]))
        except Exception:
            logging.exception("ABYSS_HEALTH_SNAPSHOT_FAILED")
        time.sleep(MONITOR_INTERVAL)


def start():
    threading.Thread(target=run, name="abyss-health", daemon=True).start()
