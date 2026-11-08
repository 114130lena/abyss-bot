"""Read-only operational checks and conservative cache housekeeping for Abyss."""
import argparse
import json
import os
import shutil
import subprocess
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
MEDIA = BASE / "creator_media"
CANDIDATES = MEDIA / "fanart_candidates"
MAX_AGE_DAYS = 45

def service_active(name):
    p = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=8)
    return p.returncode == 0 and p.stdout.strip() == "active"

def safe_clean(dry_run=True):
    """Only delete stale, unreferenced candidate cache images; never official or approved art."""
    protected = set()
    for name in ("index.json", "fanart_discovery_queue.json", "fanart_queue.json"):
        p = MEDIA / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        def walk(v):
            if isinstance(v, str) and ("fanart_candidates/" in v):
                protected.add(v.split("fanart_candidates/", 1)[1])
            elif isinstance(v, dict):
                for x in v.values(): walk(x)
            elif isinstance(v, list):
                for x in v: walk(x)
        walk(data)
    summary = {"dry_run": dry_run, "candidates": 0, "deleted": 0, "bytes": 0}
    if not CANDIDATES.exists():
        return summary
    now = time.time()
    for path in sorted(CANDIDATES.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        if path.is_symlink():
            continue
        relative = str(path.relative_to(CANDIDATES))
        if relative in protected or now - path.stat().st_mtime < MAX_AGE_DAYS * 86400:
            continue
        summary["candidates"] += 1
        if summary["deleted"] >= 100 or summary["bytes"] + path.stat().st_size > 500 * 1024 * 1024:
            break
        summary["bytes"] += path.stat().st_size
        if not dry_run:
            path.unlink()
        summary["deleted"] += 1
    return summary


def _read_dict(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _minutes_since(raw, now=None):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        current = now or datetime.now(timezone.utc)
        return round(max(0, (current - stamp.astimezone(timezone.utc)).total_seconds()) / 60, 1)
    except (OverflowError, ValueError, TypeError):
        return None


def source_check_summary(state=None, now=None):
    """Use actual poll timestamps; seen-post file ages are not poll health."""
    state = state if state is not None else _read_dict(BASE / "source_health_state.json")
    summary = {}
    for name in ("네이버 카페", "히요리 X", "후야 X"):
        value = state.get(name, {})
        value = value if isinstance(value, dict) else {}
        checked = _minutes_since(value.get("last_check"), now)
        successful = _minutes_since(value.get("last_success"), now)
        try:
            failures = max(0, min(9999, int(value.get("consecutive_failures") or 0)))
        except (ValueError, TypeError):
            failures = 0
        if checked is None:
            status = "unverified"
        elif checked > 30:
            status = "stale"
        elif failures >= 3:
            status = "repeated_failures"
        elif failures:
            status = "retrying"
        elif successful is None:
            status = "no_success_record"
        elif successful > 30:
            status = "stale_success"
        else:
            status = "ok"
        summary[name] = {
            "status": status, "last_check_age_minutes": checked,
            "last_success_age_minutes": successful,
            "consecutive_failures": failures
        }
    return summary


def component_check_summary():
    """Summarize live disk/cache/clip/banner state without exposing URLs or tokens."""
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from health_monitor import snapshot, health_warnings
    raw = snapshot()
    art = raw.get("fanart", {})
    banner = raw.get("banner", {})
    clip = raw.get("clip", {})
    source_groups = art.get("hashtag_search", {})
    artist_groups = art.get("known_artist_fallback", {})
    profile_groups = art.get("profile_feeds", {})
    groups = {}
    for name in ("hanayo", "sakihane"):
        item = art.get(name, {})
        src = source_groups.get(name, {}) if isinstance(source_groups, dict) else {}
        artist = artist_groups.get(name, {}) if isinstance(artist_groups, dict) else {}
        profile = profile_groups.get(name, {}) if isinstance(profile_groups, dict) else {}
        artist = artist if isinstance(artist, dict) else {}
        profile = profile if isinstance(profile, dict) else {}
        groups[name] = {
            "posts": int(item.get("posts") or 0),
            "verified_cached_images": int(item.get("verified_cached_images") or 0),
            "missing_cached_images": int(item.get("missing_cached_images") or 0),
            "hashtag_source_status": str(src.get("status") or "unverified")
                if isinstance(src, dict) else "unverified",
            "artist_fallback_status": str(artist.get("status") or "unverified"),
            "artist_fallback_checked": int(artist.get("checked") or 0),
            "artist_fallback_successful": int(artist.get("successful") or 0),
            "creator_profile_status": str(profile.get("status") or "unverified"),
        }
    return {
        "fanart": {
            "groups": groups, "search_age_hours": art.get("search_age_hours"),
            "queue_age_hours": art.get("queue_age_hours"),
        },
        "banner": {
            "enabled": bool(banner.get("bot_rotation_enabled")),
            "legacy_timer_active": banner.get("legacy_timer_active"),
            "total_candidates": banner.get("total_eligible_candidates"),
            "unused_candidates": banner.get("unused_candidates"),
            "vision_enabled": bool(banner.get("vision_enabled")),
            "vision_ready": bool(banner.get("vision_ready")),
        },
        "clip": {
            "configured": bool(clip.get("configured")),
            "state_exists": bool(clip.get("state_exists")),
            "cached_clips": int(clip.get("cached_clips") or 0),
            "collected_age_hours": clip.get("collected_age_hours"),
            "published_age_hours": clip.get("published_age_hours"),
        },
        "spaces": {"configured": bool(raw.get("spaces", {}).get("configured"))},
        "discord_uncertain_deliveries": int(raw.get("discord_uncertain_deliveries") or 0),
        "warning_codes": sorted(health_warnings(raw)),
    }

def inspect():
    report = {"checked_at": int(time.time()), "services": {}, "files": {}, "disk": {}, "findings": []}
    for service in ("chzzk-bot.service", "abyss-media-refresh.timer"):
        report["services"][service] = service_active(service)
        if not report["services"][service]:
            report["findings"].append("inactive " + service)
    for filename in ("channels.txt", "live_state.json", "x_seen_posts.json", "creator_media/index.json"):
        p = BASE / filename
        exists = p.is_file()
        age = int(time.time() - p.stat().st_mtime) if exists else None
        report["files"][filename] = {"exists": exists, "age_seconds": age}
        if not exists:
            report["findings"].append("missing " + filename)
    usage = shutil.disk_usage(BASE)
    report["disk"] = {"free_bytes": usage.free, "percent_used": round(100 * usage.used / usage.total, 1)}
    if usage.free < 1024 * 1024 * 1024:
        report["findings"].append("less than 1GiB free")
    report["cache"] = safe_clean(dry_run=True)
    report["sources"] = source_check_summary()
    # Degrade the diagnostic, not the live service, if a read-only module is
    # unavailable. Do not dump raw exceptions, posts or tokens into Actions.
    try:
        report["components"] = component_check_summary()
    except Exception as exc:
        report["components"] = {"status": "unavailable", "error_type": type(exc).__name__}
        report["findings"].append("component_health_unavailable")
    for name, state in report["sources"].items():
        if state["status"] in ("stale", "repeated_failures", "no_success_record", "stale_success"):
            report["findings"].append("source_health_" + name)
    for name in report.get("components", {}).get("warning_codes", []):
        report["findings"].append("component_" + name)
    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune", action="store_true", help="Remove only old unreferenced candidate files")
    args = parser.parse_args()
    report = inspect()
    if args.prune:
        report["cleanup"] = safe_clean(dry_run=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["services"].get("chzzk-bot.service"):
        raise SystemExit(2)
