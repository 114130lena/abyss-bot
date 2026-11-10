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


def snapshot(*, quick=False):
    """Report redacted health data; a quick deployment probe skips image hashing.

    The periodic monitor still uses the complete inventory. Deploy verification
    must not repeat multi-minute image scans or block a production rollout.
    """
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
              "states": {}, "fanart": {}, "banner": {}, "clip": {}, "spaces": {}}
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
    # cached_images above counts references in the persisted queue. Confirm
    # actual disk files separately: a fresh queue timestamp alone does not
    # mean a successful new-art download.
    art_base = (ROOT / "creator_media").resolve()
    for group in ("hanayo", "sakihane"):
        safe_root = (art_base / "fanart_candidates" / group).resolve()
        valid, missing = 0, 0
        for item in queue.values():
            if not isinstance(item, dict) or item.get("group") != group:
                continue
            files = item.get("cached_files")
            for value in files if isinstance(files, list) else []:
                if not isinstance(value, str):
                    missing += 1
                    continue
                candidate = (art_base / value).resolve()
                if safe_root in candidate.parents and candidate.is_file():
                    valid += 1
                else:
                    missing += 1
        result["fanart"][group]["verified_cached_images"] = valid
        result["fanart"][group]["missing_cached_images"] = missing
    if queue_path.is_file():
        result["fanart"]["queue_age_hours"] = round((now - queue_path.stat().st_mtime) / 3600, 1)
    fx_path = ROOT / "creator_media" / "fanart_fx_search_state.json"
    fx_state = _read(fx_path, {})
    result["fanart"]["hashtag_search"] = fx_state.get("groups", {})
    global_state = _read(ROOT / "creator_media" / "fanart_global_sources.json", {})
    # Source health is separate from artist-only fallback and cannot imply full
    # hashtag coverage without a new-artist production verification.
    result["fanart"]["global_sources"] = global_state.get("groups", {})
    result["fanart"]["global_providers"] = {
        name: {"status": str(data.get("status") or "unverified")}
        for name, data in (global_state.get("providers") or {}).items()
        if name in ("fx_search", "official_x") and isinstance(data, dict)
    }
    if fx_path.is_file():
        result["fanart"]["search_age_hours"] = round((now - fx_path.stat().st_mtime) / 3600, 1)
    # The clip worker refreshes its offline state every two hours but only
    # edits the existing Discord message at 08:00/18:00 KST. Expose both times,
    # and never mistake an old publication for successful recent collection.
    clip_path = ROOT / "daily_clip_state.json"
    clip = _read(clip_path, {})
    clip_list = clip.get("clips")
    def _age_from_iso(raw):
        if not isinstance(raw, str) or not raw:
            return None
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return None
            return round(max(0, (datetime.now(timezone.utc) -
                                  moment.astimezone(timezone.utc)).total_seconds()) / 3600, 2)
        except (ValueError, OverflowError, OSError):
            return None
    result["clip"] = {
        "configured": bool(os.getenv("DISCORD_BOT_TOKEN", "").strip()
                           and os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip().isdigit()
                           and os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip().isdigit()),
        "state_exists": clip_path.is_file(),
        "cached_clips": len(clip_list) if isinstance(clip_list, list) else 0,
        "collected_age_hours": _age_from_iso(clip.get("collected_at")),
        "published_age_hours": _age_from_iso(clip.get("last_updated")),
        "last_published_slot": str(clip.get("last_published_slot") or ""),
    }
    # Merely having the source module in the repository does not enable Spaces.
    result["spaces"] = {
        "configured": bool(os.getenv("X_BEARER_TOKEN", "").strip()
            and (os.getenv("HANAYO_X_USER_ID", "").strip().isdigit()
                 or os.getenv("SAKIHANE_X_USER_ID", "").strip().isdigit()))
    }
    profile_path = ROOT / "creator_media" / "fanart_profile_state.json"
    profile = _read(profile_path, {})
    result["fanart"]["profile_feeds"] = profile.get("groups", {})
    if profile_path.is_file():
        result["fanart"]["profile_age_hours"] = round(
            max(0, now - profile_path.stat().st_mtime) / 3600, 2)
    artist_watch = _read(ROOT / "creator_media" / "fanart_artist_watch_state.json", {})
    result["fanart"]["known_artist_fallback"] = artist_watch.get("groups", {})
    free_state = _read(ROOT / "creator_media" / "fanart_free_sources_state.json", {})
    free_groups = free_state.get("groups", {}) if isinstance(free_state, dict) else {}
    result["fanart"]["free_index_sources"] = {
        group: {source: {"status": str(stats.get("status") or "unverified"),
                         "added": int(stats.get("added") or 0),
                         "duplicates": int(stats.get("duplicates") or 0)}
                for source, stats in (free_groups.get(group) or {}).items()
                if source in ("bing_index", "google_news_index")
                or source.startswith("nitter_")
                if isinstance(stats, dict)}
        for group in ("hanayo", "sakihane")
        if isinstance(free_groups, dict) and isinstance(free_groups.get(group), dict)
    }
    result["fanart"]["free_index_last_checked"] = free_state.get("checked_at")

    banner_path = ROOT / "creator_media" / "guild_banner_rotation_state.json"
    banner = _read(banner_path, {})
    # The previous systemd timer and the in-process scheduler must never both
    # own guild banner writes. Report the actual legacy timer state safely.
    try:
        import subprocess
        legacy = subprocess.run(["systemctl", "is-active", "--quiet",
                                 "abyss-guild-banner.timer"], timeout=3,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        legacy = None
    result["banner"] = {"legacy_timer_active": legacy,
                        "bot_rotation_enabled": os.getenv("SERVER_BANNER_ENABLED", "0") == "1",
                        "last_slot": banner.get("last_slot"),
                        "used_count": len(banner.get("rotation_used") or []),
                        "change_time_known": bool(banner.get("banner_changed_at")),
                        "source_known": bool(banner.get("artist") and banner.get("post_url")),
                        "audit_status": banner.get("banner_audit_status", "not_checked"),
                        "source_match_status": banner.get("banner_source_match_status", "not_checked")}
    # Explain why a populated art queue may still yield only one valid
    # server-banner candidate when visual recognition is not configured.
    from fanart_relevance import eligible_hanayo_banner, eligible_huya_banner
    hanayo_posts = [item for item in queue.values()
                    if isinstance(item, dict) and item.get("group") == "hanayo"]
    text_eligible = [item for item in hanayo_posts if eligible_hanayo_banner(item)]
    result["banner"]["text_eligible_posts"] = len(text_eligible)
    result["banner"]["text_eligible_cached_images"] = sum(
        len(item.get("cached_files") or []) for item in text_eligible)
    huya_posts = [item for item in queue.values()
                  if isinstance(item, dict) and item.get("group") == "sakihane"]
    result["banner"]["huya_text_eligible_posts"] = sum(
        bool(eligible_huya_banner(item)) for item in huya_posts)
    from delivery_review import count_pending
    result["discord_uncertain_deliveries"] = count_pending()
    if quick:
        # This is the only expensive stage: inventory reads and hashes every
        # local cached image repeatedly. Keep it in periodic full snapshots,
        # never in a time-bounded SSH deploy probe. Missing counts are NOT zero.
        result["banner"]["inventory_scanned"] = False
    else:
        try:
            from guild_server_banner import (collected_hanayo_art, collected_huya_art,
                                             approved_hanayo_art, banner_inventory)
            result["banner"]["collected_candidates"] = (
                len(collected_hanayo_art()) + len(collected_huya_art()))
            result["banner"]["approved_candidates"] = len(approved_hanayo_art())
            available = banner_inventory(banner)
            result["banner"]["unused_candidates"] = available["unused"]
            result["banner"]["total_eligible_candidates"] = available["total"]
            result["banner"]["unused_by_group"] = available.get("unused_by_group", {})
            result["banner"]["inventory_scanned"] = True
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
    if any((report.get("fanart", {}).get(group, {}).get("missing_cached_images") or 0) > 0
           for group in ("hanayo", "sakihane")):
        warnings["fanart_cache_integrity"] = "팬아트 캐시 참조 중 파일이 없는 이미지가 있습니다."
    if os.getenv("SERVER_BANNER_ENABLED", "0") == "1":
        if ("collected_candidates" in banner and "approved_candidates" in banner
                and (banner["collected_candidates"] + banner["approved_candidates"])
                < max(2, int(os.getenv("SERVER_BANNER_MIN_CANDIDATES", "2")))):
            warnings["server_banner_pool"] = "서버 배너 후보가 최소 개수보다 부족합니다."
    if report.get("clip", {}).get("configured"):
        clip = report["clip"]
        if not clip.get("state_exists") or clip.get("collected_age_hours") is None:
            warnings["clip_collection"] = "클립 수집 상태 파일이나 성공 기록이 없습니다."
        elif clip["collected_age_hours"] > 3:
            warnings["clip_collection"] = "클립 최근 수집 성공 기록이 3시간 이상 오래됐습니다."
    # The watchlist augments but cannot replace the global source: a failure
    # remains visible, alongside successful known-artist profile checks.
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
