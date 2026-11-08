"""Discord guild banner rotation independent from notification media selection.

Previously curated approved artworks and automatically screened hashtag artworks
are accepted. When a vision API is configured, scored image content supersedes text gating.
"""
import argparse
import base64
import hashlib
import io
import json
import logging
import os
import random
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
from schedule_display import schedule_label

KST = ZoneInfo("Asia/Seoul")
SLOTS = {(8, 0), (18, 0)}
SIZE = (960, 540)
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def media_base():
    return Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / "creator_media"


def state_path():
    return media_base() / "guild_banner_rotation_state.json"


def read_state():
    """Recover the latest intact backup if the primary JSON is damaged."""
    original = state_path()
    for path in (original, original.with_suffix(".json.bak"),
                 original.with_suffix(".json.bak2")):
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):
            continue
    return {}


def write_state(state):
    """Atomic state replacement with two retained, private local backups."""
    destination = state_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp")
    backup = destination.with_suffix(".json.bak")
    older = destination.with_suffix(".json.bak2")
    # Preserve a permanent list of posts used for guild banners even when
    # rotation_used resets at the end of a cycle. The notification footer
    # must never reuse one of those source posts.
    from fanart_usage import canonical_post
    historical = read_state()
    ids = [x for x in historical.get("all_used_post_ids", []) if isinstance(x, str) and x.isdigit()]
    ids.extend(x for x in state.get("all_used_post_ids", []) if isinstance(x, str) and x.isdigit())
    current_id = canonical_post(state.get("post_url"))
    if current_id:
        ids.append(current_id)
    # Permanently retain the SHA-256 of every successfully used banner.
    # Older rotation_used lists may have been reset after a complete cycle;
    # preserve other historic clues (hash attribution and last applied SHA).
    sha_used = [x for x in historical.get("all_used_sha256", []) if isinstance(x, str) and x]
    sha_used.extend(x for x in state.get("all_used_sha256", []) if isinstance(x, str) and x)
    for source_state in (historical, state):
        sha_used.extend(x for x in source_state.get("rotation_used", [])
                        if isinstance(x, str) and x)
        last = source_state.get("last_sha256")
        if isinstance(last, str) and last:
            sha_used.append(last)
        for history in (source_state.get("banner_hash_history") or {}).values():
            if isinstance(history, dict) and isinstance(history.get("sha256"), str):
                sha_used.append(history["sha256"])
    state = {**state,
             "all_used_post_ids": list(dict.fromkeys(ids)),
             "all_used_sha256": list(dict.fromkeys(sha_used))}
    serialized = json.dumps(state, ensure_ascii=False, indent=2)
    temp.write_text(serialized, encoding="utf-8")
    temp.chmod(0o600)
    if destination.exists():
        # A damaged state file must not overwrite our last valid backup.
        try:
            existing = json.loads(destination.read_text("utf-8"))
        except (ValueError, OSError):
            existing = None
        if isinstance(existing, dict):
            if backup.exists():
                shutil.copy2(backup, older)
            shutil.copy2(destination, backup)
            backup.chmod(0o600)
            if older.exists():
                older.chmod(0o600)
    temp.replace(destination)


def kind(width, height):
    if width <= 0 or height <= 0:
        return None
    aspect = width / height
    if width >= 960 and height >= 540 and abs(aspect - 16 / 9) <= 0.035:
        return "16:9"
    if width >= 960 and height >= 540 and abs(aspect - 16 / 10) <= 0.03:
        return "16:10"
    if os.getenv("SERVER_BANNER_ALLOW_FIT", "0") == "1" and width >= 600 and height >= 600 and 0.5 <= aspect <= 2.5:
        return "fit"
    return None


def approved_hanayo_art():
    """Reject every other creator, unapproved candidate, and path traversal."""
    base = media_base()
    try:
        index = json.loads((base / "fanart_index.json").read_text("utf-8"))
        entries = index.get("hanayo", []) if isinstance(index, dict) else []
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    approved_root = (base / "fanart" / "hanayo").resolve()
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("approved") is not True or entry.get("permission_confirmed") is not True:
            continue
        if entry.get("hashtag", "").casefold() != "#hiyoriart_":
            continue
        source = entry.get("original_path", "")
        if not isinstance(source, str) or not source or not isinstance(entry.get("sha256"), str):
            continue
        path = (base / source).resolve()
        if approved_root not in path.parents or path.suffix.lower() not in EXTS or not path.is_file():
            continue
        if entry["sha256"] in seen:
            continue
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                continue
            with Image.open(path) as img:
                if getattr(img, "is_animated", False):
                    continue
                if not kind(*ImageOps.exif_transpose(img).size):
                    continue
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            continue
        seen.add(entry["sha256"])
        result.append((path, entry))
    return result


def collected_hanayo_art():
    """Use cached hashtag art for the separately attributed guild-banner flow.

    This pool is opt-in and independent from notification footer art. Retain
    creator attribution in state / existing guild source-information card.
    """
    if os.getenv("SERVER_BANNER_USE_COLLECTED", "1") != "1":
        return []
    from fanart_discovery import QUEUE, read, classify
    base = media_base().resolve()
    root = (base / "fanart_candidates" / "hanayo").resolve()
    if not root.is_dir():
        return []
    result = []
    seen = set()
    for item in read(QUEUE).values():
        if not isinstance(item, dict) or item.get("group") != "hanayo":
            continue
        if str(item.get("hashtag", "")).casefold() != "#hiyoriart_":
            continue
        from fanart_relevance import eligible_hanayo_banner
        from fanart_vision import enabled as vision_enabled, classification as vision_classification
        if not vision_enabled() and not eligible_hanayo_banner(item):
            continue
        if (item.get("decision") == "blocked"
                or item.get("review_status") == "blocked"
                or classify(item.get("text", "")) == "blocked"
                or item.get("server_banner_excluded") is True):
            continue
        from fanart_usage import canonical_post
        source = item.get("post_url", "")
        artist = item.get("artist", "")
        if not canonical_post(source) or not isinstance(artist, str) or not artist.startswith("@"):
            continue
        for rel in item.get("cached_files", [])[:4]:
            if not isinstance(rel, str):
                continue
            path = (base / rel).resolve()
            if root not in path.parents or path.suffix.lower() not in EXTS or not path.is_file():
                continue
            if not 0 < path.stat().st_size <= 12 * 1024 * 1024:
                continue
            try:
                with Image.open(path) as img:
                    if getattr(img, "is_animated", False):
                        continue
                    dimensions = ImageOps.exif_transpose(img).size
                    if not kind(*dimensions):
                        continue
                if vision_enabled():
                    visual = vision_classification(path)
                    if not visual or not visual.get("banner_eligible"):
                        continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                continue
            if digest in seen:
                continue
            seen.add(digest)
            from fanart_usage import preferred_server_banner
            aspect_group = "wide" if preferred_server_banner(*dimensions) else "fit"
            result.append((path, {"sha256": digest, "artist": artist,
                                  "post_url": source, "source": "cached_hanayo_fanart",
                                  "server_aspect": aspect_group,
                                  "vision_score": visual.get("score") if vision_enabled() else None}))
    # The guild owns widescreen artworks first; other ratios remain for
    # notification cards. Fit-only artwork is a fallback when none are wide.
    widescreen = [entry for entry in result if entry[1].get("server_aspect") == "wide"]
    # One wide image alone must not hide valid 16:10/fit artworks: otherwise
    # minimum-two rotation is starved even with a populated trusted art cache.
    # Prefer all-wide rotation when at least two distinct widescreen images exist.
    return widescreen if len(widescreen) >= 2 else result


def convert_banner(path):
    """16:9: downsize; 16:10: keep full art and blur-fill the sides."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        category = kind(*image.size)
        if category is None:
            raise ValueError("Image is not an eligible 16:9 or 16:10 landscape")
        foreground = image.convert("RGB")
        if category == "16:9":
            result = ImageOps.fit(foreground, SIZE, method=Image.Resampling.LANCZOS)
        else:
            result = ImageOps.fit(foreground, SIZE, method=Image.Resampling.LANCZOS)
            result = result.filter(ImageFilter.GaussianBlur(24))
            fitted = ImageOps.contain(foreground, SIZE, method=Image.Resampling.LANCZOS)
            result.paste(fitted, ((SIZE[0] - fitted.width) // 2,
                                  (SIZE[1] - fitted.height) // 2))
        output = io.BytesIO()
        result.save(output, "JPEG", quality=89, optimize=True)
        return output.getvalue()


def banner_inventory(state=None):
    """Actual eligible guild-banner pool, excluding anything already displayed.

    Never silently reset historic usage after the last artwork was selected.
    New art becomes eligible automatically as soon as it enters the real pool.
    """
    from fanart_usage import canonical_post
    state = state if isinstance(state, dict) else read_state()
    unique = {}
    for path, info in approved_hanayo_art() + collected_hanayo_art():
        digest = info.get("sha256")
        if isinstance(digest, str) and digest:
            unique.setdefault(digest, (path, info))
    used = set(x for x in (state.get("rotation_used") or []) if isinstance(x, str))
    used.update(x for x in (state.get("all_used_sha256") or []) if isinstance(x, str))
    previous = state.get("last_sha256")
    if isinstance(previous, str) and previous:
        used.add(previous)
    for record in (state.get("banner_hash_history") or {}).values():
        if isinstance(record, dict) and isinstance(record.get("sha256"), str):
            used.add(record["sha256"])
    used_posts = set(x for x in (state.get("all_used_post_ids") or [])
                     if isinstance(x, str) and x.isdigit())
    remaining = {
        sha: value for sha, value in unique.items()
        if sha not in used
        and canonical_post(value[1].get("post_url")) not in used_posts
    }
    return {"total": len(unique), "unused": len(remaining),
            "used": len(unique) - len(remaining), "available": remaining}


def rotation_selection(previous=None, used=None, state=None):
    """Select a never-shown banner. Do not recycle when the pool is exhausted.

    The history is persisted only after Discord confirms a successful change.
    New images can join the pool at any point without resetting past usage.
    """
    state = dict(state if isinstance(state, dict) else read_state())
    history = list(dict.fromkeys(x for x in (used or state.get("rotation_used") or [])
                                 if isinstance(x, str) and x))
    state["rotation_used"] = history
    if previous and not state.get("last_sha256"):
        state["last_sha256"] = previous
    available = banner_inventory(state)
    unseen = available["available"]
    if not unseen:
        return None, history, available["total"], 0
    from fanart_vision import enabled as vision_enabled
    if vision_enabled():
        best = max(int(meta.get("vision_score") or 0) for _path, meta in unseen.values())
        unseen = {sha: value for sha, value in unseen.items()
                  if int(value[1].get("vision_score") or 0) >= best - 15}
    picked = random.choice(list(unseen))
    return (unseen[picked], history + [picked], available["total"],
            available["unused"] - 1)


def select_art(previous):
    return rotation_selection(previous, read_state().get("rotation_used", []))[0]


def change_guild_banner(image, token, guild_id):
    if not guild_id.isdigit() or not token:
        raise ValueError("Discord bot token and numeric guild ID required")
    response = requests.patch(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={"Authorization": "Bot " + token, "Content-Type": "application/json"},
        json={"banner": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")},
        timeout=25,
    )
    response.raise_for_status()
    # The successful PATCH response is authoritative for the new banner hash.
    # A GET immediately afterwards may still return the old CDN revision.
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None
    if (isinstance(data, dict) and str(data.get("id", guild_id)) == guild_id
            and isinstance(data.get("banner"), str) and data["banner"]):
        return data["banner"]
    return None


# Persistent source-information card. This is independent of the banner's
# twice-daily rotation: manual guild edits are detected by Discord banner hash.
INFO_CHANNEL_ID = os.getenv("SERVER_BANNER_INFO_CHANNEL_ID", "1567561255764500551").strip()


def _current_guild_banner(token, guild_id):
    response = requests.get(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={"Authorization": "Bot " + token},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or str(data.get("id", guild_id)) != guild_id:
        raise ValueError("Unexpected Discord guild response")
    value = data.get("banner")
    if value is not None and (not isinstance(value, str) or len(value) > 160):
        raise ValueError("Invalid guild banner hash")
    return value or ""


def _valid_banner_source(artist, source):
    """Only display attribution for a real X status and a plausible author."""
    import re
    from fanart_usage import canonical_post
    return (isinstance(artist, str) and bool(re.fullmatch(r"@[A-Za-z0-9_]{1,15}", artist))
            and isinstance(source, str) and bool(canonical_post(source)))


def _banner_history(state):
    """Versioned hash-to-source records from successful bot changes only."""
    raw = state.get("banner_hash_history")
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items()
            if isinstance(key, str) and 0 < len(key) <= 160
            and isinstance(value, dict)}


def _resolve_banner_attribution(banner_hash, previous):
    """Match the *exact* Discord banner hash, never guess by art title/hashtag.

    An external edit can select a formerly bot-applied banner. Recover its
    artist/source only when that exact hash was recorded after a successful PATCH.
    """
    record = _banner_history(previous).get(banner_hash)
    if not record:
        return None
    artist, source = record.get("artist"), record.get("post_url")
    if not _valid_banner_source(artist, source):
        return None
    return {"artist": artist, "post_url": source,
            "banner_source_recorded_at": str(record.get("changed_at") or "").strip(),
            "banner_change_method": "이전 자동 배너 재사용 감지"}



def _audit_banner_change(token, guild_id, actual, get=None):
    """Read Discord guild-update audit logs; match only this exact banner hash.

    A guild update is not necessarily a banner update. Require a change to the
    banner_hash key with new_value identical to the current banner. Fail closed
    on a permission error, incomplete entry, or an unrecognised response.
    """
    fetch = get or requests.get
    endpoint = f"https://discord.com/api/v10/guilds/{guild_id}/audit-logs"
    before = None
    for _ in range(3):
        params = {"action_type": 1, "limit": 100}
        if before:
            params["before"] = before
        try:
            response = fetch(endpoint, headers={"Authorization": "Bot " + token},
                             params=params, timeout=12)
            if response.status_code in (401, 403):
                return {"status": "permission_denied"}
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return {"status": "unavailable"}
        if not isinstance(data, dict) or not isinstance(data.get("audit_log_entries"), list):
            return {"status": "invalid_response"}
        entries = data["audit_log_entries"]
        users = {str(u.get("id")): u for u in data.get("users", [])
                 if isinstance(u, dict)} if isinstance(data.get("users"), list) else {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("action_type") != 1:
                continue
            changes = entry.get("changes")
            if not isinstance(changes, list):
                continue
            if not any(isinstance(change, dict)
                       and change.get("key") in ("banner_hash", "banner")
                       and change.get("new_value") == actual
                       for change in changes):
                continue
            entry_id = str(entry.get("id") or "")
            if not entry_id.isdigit():
                continue
            try:
                milliseconds = (int(entry_id) >> 22) + 1420070400000
                changed_at = datetime.fromtimestamp(milliseconds / 1000, KST)
            except (ValueError, OverflowError, OSError):
                continue
            actor_id = str(entry.get("user_id") or "")
            user = users.get(actor_id, {})
            name = str(user.get("username") or "").strip()
            # Names are purely descriptive. Never send a user or role mention.
            if not (1 <= len(name) <= 32 and all(c.isalnum() or c in "._" for c in name)):
                name = ""
            actor = ("(@" + name + ")") if name else "(변경 계정 상세 미확인)"
            kind = "봇/앱 변경" if user.get("bot") is True else "관리 권한 사용자 변경"
            return {"status": "matched",
                    "banner_change_method": kind + " " + actor,
                    "banner_changed_at": changed_at.strftime("%Y-%m-%d %H:%M KST"),
                    "banner_audit_entry_id": entry_id}
        if len(entries) < 100:
            break
        oldest = str(entries[-1].get("id") or "")
        if not oldest.isdigit() or oldest == before:
            break
        before = oldest
    return {"status": "not_found"}


def _recover_cached_image_source(guild_id, actual, get=None):
    """Recover a missing source only when the live guild image matches local art.

    This never searches the web by appearance or invents authors. Only source
    candidates already linked to a verified original X post are considered.
    Keep the threshold strict to avoid attributing unrelated images.
    """
    from PIL import ImageChops, ImageStat
    candidates = approved_hanayo_art() + collected_hanayo_art()
    if not candidates:
        return {"status": "no_candidates"}
    extension = "gif" if actual.startswith("a_") else "png"
    url = f"https://cdn.discordapp.com/banners/{guild_id}/{actual}.{extension}?size=1024"
    try:
        response = (get or requests.get)(url, timeout=(3, 12))
        response.raise_for_status()
        if len(response.content) > 12 * 1024 * 1024:
            return {"status": "oversized"}
        with Image.open(io.BytesIO(response.content)) as original:
            live = ImageOps.fit(ImageOps.exif_transpose(original).convert("RGB"),
                                SIZE, method=Image.Resampling.LANCZOS)
    except (requests.RequestException, OSError, ValueError,
            UnidentifiedImageError, Image.DecompressionBombError):
        return {"status": "unavailable"}
    scores = []
    for path, meta in candidates:
        if not _valid_banner_source(meta.get("artist"), meta.get("post_url")):
            continue
        try:
            candidate = Image.open(io.BytesIO(convert_banner(path))).convert("RGB")
            distance = max(ImageStat.Stat(ImageChops.difference(live, candidate)).mean)
            if distance < 7.0:
                scores.append((distance, meta))
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            continue
    scores.sort(key=lambda x: x[0])
    if not scores:
        return {"status": "not_found"}
    # Only accept an unambiguous match. A 16:9 crop comparison may not be
    # distinguishable for near-duplicate fanart from the same composition.
    if len(scores) > 1 and scores[1][0] - scores[0][0] < 3.0:
        return {"status": "ambiguous"}
    result = scores[0][1]
    return {"status": "matched", "artist": result["artist"],
            "post_url": result["post_url"]}



def _audit_retry_due(state, banner_hash, now=None):
    """Retry transient/missing-permission audit lookup periodically, not every poll.

    An operator can grant VIEW_AUDIT_LOG after initial failure. Do not freeze
    provenance recovery for the remaining lifetime of the same banner hash.
    """
    if state.get("banner_audit_checked_hash") != banner_hash:
        return True
    if state.get("banner_audit_status") not in (
            "permission_denied", "unavailable", "invalid_response"):
        return False
    last = str(state.get("banner_audit_checked_at") or "")
    try:
        previous = datetime.fromisoformat(last)
        if previous.tzinfo is None:
            return True
    except (ValueError, TypeError):
        return True
    now = now or datetime.now(KST)
    return now - previous.astimezone(KST) >= timedelta(minutes=5)


def _banner_card(guild_id, banner_hash, state, fanart_stats=None):
    """Compact Components V2 card with a clear preview, schedule and inventory."""
    source = str(state.get("post_url") or "").strip()
    artist = str(state.get("artist") or "").strip()
    known_source = bool(banner_hash and _valid_banner_source(artist, source))
    method = str(state.get("banner_change_method") or "").strip()
    if not method or method.startswith(("기존 기록", "이전 기록")):
        method = "이전 배너 · 변경 방식 미확인"
    changed_at = str(state.get("banner_changed_at") or "").strip()
    if not changed_at and state.get("banner_first_observed_at"):
        changed_at = ("정확한 변경 시각 미확인 (최초 확인 · "
                      + str(state["banner_first_observed_at"]) + ")")

    # One image, then short grouped labels. Avoid nested duplicated headings,
    # raw source URLs and a single overwhelming paragraph of metadata.
    parts = [{"type": 10, "content":
              "## 서버 배너 정보\n-# HANAYO HIYORI · STELLIVE · ABYSS"}]
    if banner_hash:
        ext = "gif" if banner_hash.startswith("a_") else "png"
        media_url = f"https://cdn.discordapp.com/banners/{guild_id}/{banner_hash}.{ext}?size=1024"
        parts.append({"type": 12, "items": [{
            "media": {"url": media_url},
            "description": "현재 적용된 서버 배너"}]})
    else:
        parts.append({"type": 10, "content": "-# 현재 서버에 배너 이미지가 없습니다."})

    parts.append({"type": 10, "content": (
        "**현재 적용된 서버 배너**\n"
        f"**변경 방식** · {method}\n"
        f"**변경 시각** · {changed_at or '기록 없음'}\n"
        f"**원본 작가** · {artist if known_source else '확인되지 않음'}\n"
        "**원본 게시물** · " +
        (f"[X에서 보기]({source})" if known_source else "확인되지 않음")
    )})
    if known_source:
        parts.append({"type": 1, "components": [{
            "type": 2, "style": 5, "label": "원본 게시물", "url": source}]})

    from fanart_public_stats import snapshot, card_sections
    if fanart_stats is None:
        fanart_stats = snapshot()
    parts.extend([
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "**⏰ 업데이트 일정**\n" + schedule_label()},
        {"type": 14, "divider": True, "spacing": 1},
    ])
    for section in card_sections(fanart_stats):
        parts.append({"type": 10, "content": section})
    parts.append({"type": 14, "divider": True, "spacing": 1})
    parts.append({"type": 10, "content":
                  "-# 새 이미지가 없으면 기존 배너를 유지합니다 · 현황은 수집 시 갱신"})
    return {"flags": 32768, "components": [{
        "type": 17, "accent_color": 0x8391BB, "components": parts
    }], "allowed_mentions": {"parse": []}}


def _find_existing_banner_info(headers, base):
    """Reuse a previous bot-authored banner card before creating a duplicate."""
    identity = requests.get("https://discord.com/api/v10/users/@me",
                            headers=headers, timeout=12)
    identity.raise_for_status()
    bot_id = str(identity.json().get("id", ""))
    if not bot_id.isdigit():
        return ""
    response = requests.get(base, headers=headers, params={"limit": 100}, timeout=12)
    response.raise_for_status()
    messages = response.json()
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str((message.get("author") or {}).get("id")) != bot_id:
            continue
        titles = [str(e.get("title", "")) for e in message.get("embeds", [])
                  if isinstance(e, dict)]
        if "서버 배너 정보" in titles:
            identifier = str(message.get("id", ""))
            if identifier.isdigit():
                return identifier
    return ""


def sync_banner_info(force=False):
    """Edit one bot-owned information card, reconciling externally changed banners.

    A failed message update never blocks banner rotation. Errors are retried at
    the next poll. No state is committed before Discord accepts the message.
    """
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    channel_id = INFO_CHANNEL_ID
    if not token or not guild_id.isdigit() or not channel_id.isdigit():
        return {"status": "missing_configuration"}
    actual = _current_guild_banner(token, guild_id)
    previous = read_state()
    expected = previous.get("guild_banner_hash")
    # If the current banner differs from the last bot-applied banner, any
    # recorded artist is stale, even after a restart or manual replacement.
    # Missing hash is an uninitialized record, not proof of an external change.
    changed = expected is not None and expected != actual
    uninitialized = expected is None
    state = dict(previous)
    observed = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    if changed:
        recovered = _resolve_banner_attribution(actual, previous)
        state["guild_banner_hash"] = actual
        state["banner_changed_at"] = ""   # previous hash's timestamp is not current
        state["banner_first_observed_at"] = observed
        state["banner_audit_checked_hash"] = ""
        state["banner_source_checked_hash"] = ""
        if recovered:
            state.update(recovered)
        else:
            state["artist"] = ""
            state["post_url"] = ""
            state["banner_change_method"] = "외부 변경 감지"
        state["banner_info_updated_hash"] = ""
    if uninitialized:
        state["guild_banner_hash"] = actual
        state["banner_info_updated_hash"] = ""
        if actual:
            state.setdefault("banner_first_observed_at", observed)
        # Retain legacy source metadata but do not claim to know its provenance.
        state.setdefault("banner_change_method", "기존 기록 · 변경 주체 미상")
    if actual and not state.get("banner_changed_at") and not state.get("banner_first_observed_at"):
        state["banner_first_observed_at"] = observed
        state["banner_info_updated_hash"] = ""

    # Audit data is independent of artist credit. It can establish the time
    # and updater even when no valid original X URL is in our history.
    audit_needed = bool(actual and (changed or uninitialized
        or not state.get("banner_changed_at")
        or state.get("banner_change_method", "").startswith(("기존 기록", "이전 기록"))))
    if (audit_needed and os.getenv("SERVER_BANNER_AUDIT_LOOKUP", "0") == "1"
            and _audit_retry_due(state, actual)):
        audit = _audit_banner_change(token, guild_id, actual)
        state["banner_audit_checked_hash"] = actual
        state["banner_audit_status"] = audit["status"]
        state["banner_audit_checked_at"] = datetime.now(KST).isoformat()
        if audit["status"] == "matched":
            state["banner_change_method"] = audit["banner_change_method"]
            state["banner_changed_at"] = audit["banner_changed_at"]
            state["banner_audit_entry_id"] = audit["banner_audit_entry_id"]
        state["banner_info_updated_hash"] = ""
        logging.info("BANNER_METADATA_AUDIT status=%s", audit["status"])

    # An exact image comparison can recover the source from locally retained
    # artwork; a hashtag or a similar-looking character alone cannot.
    if (actual and not _valid_banner_source(state.get("artist"), state.get("post_url"))
            and os.getenv("SERVER_BANNER_SOURCE_IMAGE_MATCH", "0") == "1"
            and state.get("banner_source_checked_hash") != actual):
        match = _recover_cached_image_source(guild_id, actual)
        state["banner_source_checked_hash"] = actual
        state["banner_source_match_status"] = match["status"]
        if match["status"] == "matched":
            state["artist"] = match["artist"]
            state["post_url"] = match["post_url"]
            state["banner_source_verified_via"] = "cached_image_match"
        state["banner_info_updated_hash"] = ""
        logging.info("BANNER_METADATA_IMAGE_MATCH status=%s", match["status"])
    # Always target the explicitly identified ORIGINAL information message.
    # Earlier versions created an unwanted second bot message when the
    # original was a webhook post or not found in the 100-message history.
    # Neither auto-discovery nor new POST is safe for an existing channel.
    message_id = os.getenv("SERVER_BANNER_INFO_MESSAGE_ID", "").strip()
    if not message_id.isdigit():
        return {"status": "original_message_id_required"}
    from fanart_public_stats import snapshot, checksum
    fanart_stats = snapshot()
    stats_checksum = checksum(fanart_stats)
    if (not force and not changed
            and str(state.get("banner_info_message_id")) == message_id
            and state.get("banner_info_updated_hash") == actual
            and state.get("banner_info_stats_checksum") == stats_checksum
            and state.get("banner_info_layout_version") == 10):
        return {"status": "up_to_date", "message_id": message_id}
    payload = _banner_card(guild_id, actual, state, fanart_stats=fanart_stats)
    from discord_message_sync import update_original
    status = update_original(channel_id, message_id, payload, token,
                             get=requests.get, patch=requests.patch)
    if status != "updated":
        logging.warning("BANNER_INFO_ORIGINAL_UNEDITABLE message=%s reason=%s; no duplicate created",
                        message_id, status)
        return {"status": "original_message_uneditable",
                "message_id": message_id, "reason": status}
    state["banner_info_message_id"] = message_id
    state["banner_info_updated_hash"] = actual
    state["banner_info_layout_version"] = 10
    state["banner_info_stats_checksum"] = stats_checksum
    write_state(state)
    logging.info("SERVER_BANNER_INFO_SYNC channel=%s banner=%s message=%s external=%s",
                 channel_id, actual[:12], message_id, changed)
    return {"status": "updated", "message_id": message_id, "external": changed}


def slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    return now.strftime("%Y-%m-%d %H:%M") if (now.hour, now.minute) in SLOTS else None


def rotate_once(*, manual=False, dry_run=False, now=None, preview_path=None):
    """Manual tests do NOT change scheduling state unless a real API call succeeds."""
    current_slot = slot(now)
    if not manual and not current_slot:
        return {"status": "outside_schedule"}
    previous = read_state()
    if current_slot and not manual and previous.get("last_slot") == current_slot:
        return {"status": "already_rotated"}
    selected, rotation_used, candidate_total, remaining = rotation_selection(
        previous.get("last_sha256"), previous.get("rotation_used", []), state=previous)
    if selected is None:
        if candidate_total:
            logging.info("SERVER_BANNER_ROTATION_EXHAUSTED total=%d; new fanart required",
                         candidate_total)
            return {"status": "exhausted_candidates", "candidate_total": candidate_total,
                    "remaining": 0}
        logging.warning("SERVER_BANNER_PIPELINE candidates=0 source=approved_and_cached")
        return {"status": "no_approved_hanayo_art"}
    # Do not overwrite an intentionally chosen manual banner with the only
    # available candidate at the next scheduled slot. Info sync keeps running.
    minimum = max(2, int(os.getenv("SERVER_BANNER_MIN_CANDIDATES", "2")))
    if not manual and candidate_total < minimum:
        logging.warning("SERVER_BANNER_ROTATION_SKIPPED candidates=%d minimum=%d",
                        candidate_total, minimum)
        return {"status": "insufficient_candidates",
                "candidate_total": candidate_total, "minimum": minimum}
    source, metadata = selected
    logging.info("SERVER_BANNER_PICK candidates=%d remaining=%d artist=%s source=%s",
                 candidate_total, remaining, metadata.get("artist", ""), metadata.get("post_url", ""))
    image = convert_banner(source)
    if preview_path is not None:
        destination = Path(preview_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image)
    info = {"sha256": metadata["sha256"], "artist": metadata.get("artist"),
            "post_url": metadata.get("post_url"), "size": list(SIZE),
            "rotation_used": rotation_used, "candidate_total": candidate_total,
            "remaining_after": remaining}
    if dry_run:
        return {"status": "preview_only", **info}
    if os.getenv("SERVER_BANNER_ENABLED", "0") != "1":
        return {"status": "disabled", **info}
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not token or not guild_id.isdigit():
        return {"status": "missing_discord_credentials"}
    changed_hash = change_guild_banner(image, token, guild_id)
    # The guild PATCH succeeded. Record its new banner hash when available;
    # never guess a CDN hash from the local JPEG.
    changed_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    recorded = {**previous, "last_slot": current_slot, "last_sha256": metadata["sha256"],
                "artist": metadata.get("artist"), "post_url": metadata.get("post_url"),
                "rotation_used": rotation_used,
                "all_used_sha256": list(dict.fromkeys(
                    [*(previous.get("all_used_sha256") or []), metadata["sha256"]])),
                "candidate_total": candidate_total,
                "remaining_after": remaining,
                "banner_change_method": "봇 자동 교체" if not manual else "봇 수동 명령 교체",
                "banner_changed_at": changed_at}
    try:
        recorded["guild_banner_hash"] = (changed_hash if isinstance(changed_hash, str) and changed_hash
                                          else _current_guild_banner(token, guild_id))
    except (requests.RequestException, ValueError):
        # Prevent stale attribution if a later reconciliation cannot verify it.
        recorded["guild_banner_hash"] = None
    # Keep exact banner-hash provenance across restarts and later manual
    # re-selection. Never infer attribution from image dimensions or captions.
    new_hash = recorded.get("guild_banner_hash")
    if (isinstance(new_hash, str) and new_hash
            and _valid_banner_source(recorded.get("artist"), recorded.get("post_url"))):
        history = _banner_history(previous)
        history[new_hash] = {
            "artist": recorded["artist"], "post_url": recorded["post_url"],
            "changed_at": changed_at, "method": recorded["banner_change_method"],
            "sha256": metadata["sha256"],
        }
        # Limit file growth; keep the latest 100 hashes, and retain a separate
        # permanent post-id exclusion list as before.
        recorded["banner_hash_history"] = dict(list(history.items())[-100:])
    recorded["banner_info_updated_hash"] = None
    write_state(recorded)
    try:
        sync_banner_info()
    except (requests.RequestException, ValueError, OSError):
        logging.exception("배너 변경 성공, 정보 메시지 갱신은 재시도 예정")
    logging.info("히요리 서버 배너 교체 완료: %s / %s", metadata.get("artist"), current_slot)
    return {"status": "changed", **info}


def run_server_banner_scheduler():
    """Must be explicitly enabled; existing alert workers remain independent."""
    if os.getenv("SERVER_BANNER_ENABLED", "0") != "1":
        return
    attempted = None
    last_info_check = 0.0
    last_info_slot = None
    while True:
        try:
            now_slot = slot()
            if now_slot and attempted != now_slot:
                result = rotate_once()
                logging.info("server-banner result: %s", result)
                # Retry transient failures within the scheduled minute instead of
                # permanently consuming the slot before a successful change.
                if result.get("status") in ("changed", "already_rotated", "exhausted_candidates", "insufficient_candidates", "no_approved_hanayo_art", "disabled"):
                    attempted = now_slot
            now = time.monotonic()
            due_info = bool(now_slot and last_info_slot != now_slot)
            if due_info or now - last_info_check >= max(30, int(os.getenv("SERVER_BANNER_INFO_POLL_SECONDS", "60"))):
                last_info_check = now
                try:
                    result = sync_banner_info(force=due_info)
                    logging.info("server-banner info: %s", result)
                    if due_info and result.get("status") in ("updated", "up_to_date"):
                        last_info_slot = now_slot
                except (requests.RequestException, ValueError, OSError):
                    logging.exception("서버 배너 정보 동기화 실패; 다음 주기 재시도")
        except Exception:
            logging.exception("서버 배너 자동 변경 실패; 기존 배너 유지")
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="승인된 히요리 팬아트로 디스코드 서버 배너 테스트")
    parser.add_argument("--apply-once", action="store_true",
                        help="즉시 서버에 적용 (환경변수 활성화 및 봇 인증 필수)")
    parser.add_argument("--output", default="",
                        help="실제 변경 없이 저장할 미리보기 JPG 경로")
    args = parser.parse_args()
    if not args.apply_once and not args.output:
        parser.error("--output 또는 --apply-once 중 하나를 지정해 주세요")
    print(json.dumps(rotate_once(manual=True, dry_run=not args.apply_once,
                                  preview_path=args.output or None),
                     ensure_ascii=False))
