"""Public, read-only fanart inventory for the existing guild-banner info card.

Counts use the SAME selectors as production banner rotation and notification
fanart galleries. A fanart 'post' is one attributed original X status (which
may contain up to four photos); a guild banner 'image' is one eligible SHA256.
Never treat a successful source *check* as proof of newly collected artwork.
"""
import hashlib
import json
from datetime import datetime, timezone

from fanart_discovery import BASE, QUEUE, read
from schedule_display import format_saved_time


def _utc(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _latest(*values):
    actual = [t for t in (_utc(value) for value in values) if t]
    return max(actual).isoformat() if actual else ""


def snapshot():
    """Return no tokens, no unpublished titles and no original creator names."""
    queue_exists = QUEUE.is_file()
    queue = read(QUEUE)
    result = {"available": queue_exists, "groups": {}, "checked_at": "",
              "last_new_post_at": "", "server_banner_images": None, "server_banner_total": None, "server_banner_used": None}
    for group in ("hanayo", "sakihane"):
        items = [item for item in queue.values()
                 if isinstance(item, dict) and item.get("group") == group]
        # Only images physically present in their group-specific cache count.
        cache_root = (BASE / "fanart_candidates" / group).resolve()
        valid_images = 0
        for item in items:
            files = item.get("cached_files")
            for relative in files[:4] if isinstance(files, list) else []:
                if not isinstance(relative, str):
                    continue
                candidate = (BASE / relative).resolve()
                if (cache_root in candidate.parents and candidate.is_file()):
                    valid_images += 1
        result["groups"][group] = {"posts": len(items) if queue_exists else None,
                                    "cached_images": valid_images if queue_exists else None,
                                    "notification_posts": None, "notification_total": None, "notification_reusable": None}
    if not queue_exists:
        return result

    # Reuse actual eligibility and exclusion logic; do not count blocked
    # artworks, reused server banners or incorrectly tagged content.
    from fanart_url_media import candidates as notification_candidates, availability
    for group in ("hanayo", "sakihane"):
        actual = availability(group, pool=notification_candidates(group))
        result["groups"][group]["notification_posts"] = actual["unused"]
        result["groups"][group]["notification_total"] = actual["total"]
        result["groups"][group]["notification_reusable"] = actual["reusable"]

    from guild_server_banner import banner_inventory
    guild_pool = banner_inventory()
    result["server_banner_images"] = guild_pool["unused"]
    result["server_banner_total"] = guild_pool["total"]
    result["server_banner_used"] = guild_pool["used"]
    profile = read(BASE / "fanart_profile_state.json")
    search = read(BASE / "fanart_fx_search_state.json")
    artists = read(BASE / "fanart_artist_watch_state.json")
    api = read(BASE / "fanart_discovery_state.json")
    api_checked = [v.get("checked_at", "") for v in api.values()
                   if isinstance(v, dict)]
    result["checked_at"] = _latest(profile.get("checked_at"), search.get("checked_at"),
                                    artists.get("checked_at"), *api_checked)
    result["last_new_post_at"] = _latest(*(
        item.get("discovered_at") for item in queue.values()
        if isinstance(item, dict) and item.get("discovered_at")
    ))
    return result


def checksum(report):
    """Stable fingerprint so a once-a-minute check patches only on changes."""
    serialized = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _value(value, unit):
    return (str(value) + unit) if isinstance(value, int) else "집계 불가"


def card_sections(report):
    """Separate collection, remaining candidates and timestamps into clear blocks."""
    if not report.get("available"):
        return ("**🎨 팬아트 수집 현황**\n수집 데이터 확인 불가 · 서버 기록을 확인할 수 없습니다.",)
    h = report.get("groups", {}).get("hanayo", {})
    f = report.get("groups", {}).get("sakihane", {})
    stored = (
        "**🎨 팬아트 수집 현황**\n"
        f"**수집 게시글**　히요리 {_value(h.get('posts'), '건')} · 후야 {_value(f.get('posts'), '건')}\n"
        f"**저장된 이미지**　히요리 {_value(h.get('cached_images'), '장')} · 후야 {_value(f.get('cached_images'), '장')}"
    )
    available = (
        "**📌 사용 가능한 후보**\n"
        f"**서버 배너용 후보**　히요리 {_value(report.get('server_banner_images'), '장')}"
        f" / 전체 {_value(report.get('server_banner_total'), '장')}\n"
        f"**알림 배너·팬아트 후보**　히요리 {_value(h.get('notification_posts'), '건')}"
        f" · 후야 {_value(f.get('notification_posts'), '건')}\n"
        "-# 서버 배너는 사용 후 제외 · 알림 팬아트는 소진 시에만 재사용"
    )
    checked = (
        "**🕒 수집 기록**\n"
        f"**마지막 수집 점검**　{format_saved_time(report.get('checked_at'))}\n"
        f"**마지막 신규 게시글 발견**　{format_saved_time(report.get('last_new_post_at'))}"
    )
    return stored, available, checked


def card_text(report):
    """Text-only view and test compatibility."""
    return "\n\n".join(card_sections(report))
