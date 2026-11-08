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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError

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
    state = {**state, "all_used_post_ids": list(dict.fromkeys(ids))}
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
    return widescreen if widescreen else result


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


def rotation_selection(previous=None, used=None):
    """Draw without replacement, automatically extending the pool with new art.

    Rotation history is committed by the caller only after Discord accepts the
    banner change, so an API failure cannot consume a picture.
    """
    pool = approved_hanayo_art() + collected_hanayo_art()
    unique = {}
    for path, info in pool:
        unique.setdefault(info["sha256"], (path, info))
    all_ids = set(unique)
    history = [sha for sha in (used or []) if sha in all_ids]
    unseen = [sha for sha in unique if sha not in history]
    if not unseen:
        history = []
        unseen = list(unique)
    if len(unseen) > 1 and previous in unseen:
        unseen.remove(previous)
    if not unseen:
        return None, [], len(unique), 0
    # Prefer high visual scores once the model-backed classifier is active;
    # retain variation among similarly scored artwork instead of always reusing one.
    from fanart_vision import enabled as vision_enabled
    if vision_enabled():
        best = max(int(unique[sha][1].get("vision_score") or 0) for sha in unseen)
        unseen = [sha for sha in unseen if int(unique[sha][1].get("vision_score") or 0) >= best - 15]
    picked = random.choice(unseen)
    return unique[picked], history + [picked], len(unique), max(len(unique) - len(history) - 1, 0)


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


def _banner_card(guild_id, banner_hash, state):
    from datetime import datetime
    if banner_hash:
        extension = "gif" if banner_hash.startswith("a_") else "png"
        image_url = (
            f"https://cdn.discordapp.com/banners/{guild_id}/{banner_hash}.{extension}?size=1024"
        )
        artist = str(state.get("artist") or "").strip()
        source = str(state.get("post_url") or "").strip()
        from fanart_usage import canonical_post
        if artist.startswith("@") and canonical_post(source):
            info = f"**작가:** {artist}\n**원본:** {source}"
        else:
            info = "**출처:** 확인되지 않은 수동 변경 배너"
        description = (
            "**현재 서버 배너**\n" + info +
            "\n-# Discord에 적용된 배너를 기준으로 자동 갱신됩니다."
        )
    else:
        image_url = ""
        description = "현재 서버에 설정된 배너가 없습니다."
    embed = {"title": "서버 배너 정보", "description": description,
             "color": 0x8391BB,
             "footer": {"text": "마지막 확인 · " + datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}}
    if image_url:
        embed["image"] = {"url": image_url}
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


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
    changed = expected != actual
    state = dict(previous)
    if changed:
        state["guild_banner_hash"] = actual
        state["artist"] = ""
        state["post_url"] = ""
        state["banner_info_updated_hash"] = ""
    message_id = str(state.get("banner_info_message_id") or
                     os.getenv("SERVER_BANNER_INFO_MESSAGE_ID", "")).strip()
    if (not force and not changed and message_id
            and state.get("banner_info_updated_hash") == actual):
        return {"status": "up_to_date", "message_id": message_id}
    payload = _banner_card(guild_id, actual, state)
    headers = {"Authorization": "Bot " + token, "Content-Type": "application/json"}
    base = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    response = None
    if not message_id.isdigit():
        try:
            message_id = _find_existing_banner_info(headers, base)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            logging.warning("기존 배너 정보 메시지 탐색 실패; 새 정보 메시지를 작성합니다")
    if message_id.isdigit():
        response = requests.patch(base + "/" + message_id, headers=headers,
                                  json=payload, timeout=12)
        if response.status_code not in (403, 404):
            response.raise_for_status()
    if not message_id.isdigit() or (response is not None and response.status_code in (403, 404)):
        response = requests.post(base, headers=headers, json=payload, timeout=12)
        response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not str(data.get("id", "")).isdigit():
        raise ValueError("Discord did not confirm banner information message")
    state["banner_info_message_id"] = str(data["id"])
    state["banner_info_updated_hash"] = actual
    write_state(state)
    logging.info("SERVER_BANNER_INFO_SYNC channel=%s banner=%s message=%s external=%s",
                 channel_id, actual[:12], data["id"], changed)
    return {"status": "updated", "message_id": str(data["id"]), "external": changed}


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
        previous.get("last_sha256"), previous.get("rotation_used", []))
    if selected is None:
        return {"status": "no_approved_hanayo_art"}
    source, metadata = selected
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
    change_guild_banner(image, token, guild_id)
    # The guild PATCH succeeded. Record its new banner hash when available;
    # never guess a CDN hash from the local JPEG.
    recorded = {**previous, "last_slot": current_slot, "last_sha256": metadata["sha256"],
                "artist": metadata.get("artist"), "post_url": metadata.get("post_url"),
                "rotation_used": rotation_used, "candidate_total": candidate_total,
                "remaining_after": remaining}
    try:
        recorded["guild_banner_hash"] = _current_guild_banner(token, guild_id)
    except (requests.RequestException, ValueError):
        # Prevent stale attribution if a later reconciliation cannot verify it.
        recorded["guild_banner_hash"] = None
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
    while True:
        try:
            now_slot = slot()
            if now_slot and attempted != now_slot:
                attempted = now_slot
                logging.info("server-banner result: %s", rotate_once())
            now = time.monotonic()
            if now - last_info_check >= max(30, int(os.getenv("SERVER_BANNER_INFO_POLL_SECONDS", "60"))):
                last_info_check = now
                try:
                    logging.info("server-banner info: %s", sync_banner_info())
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
