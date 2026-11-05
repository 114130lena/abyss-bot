"""Discord guild banner rotation, isolated from notification-card banner_manager.py.

Source: only explicitly approved Hanayo entries in creator_media/fanart_index.json.
No scraping, image re-sharing, or Discord calls until explicitly enabled.
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
    if os.getenv("SERVER_BANNER_USE_COLLECTED", "0") != "1":
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
                    if not kind(*ImageOps.exif_transpose(img).size):
                        continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                continue
            if digest in seen:
                continue
            seen.add(digest)
            result.append((path, {"sha256": digest, "artist": artist,
                                  "post_url": source, "source": "cached_hanayo_fanart"}))
    return result


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
    write_state({**previous, "last_slot": current_slot, "last_sha256": metadata["sha256"],
                 "artist": metadata.get("artist"), "post_url": metadata.get("post_url"),
                 "rotation_used": rotation_used, "candidate_total": candidate_total,
                 "remaining_after": remaining})
    logging.info("히요리 서버 배너 교체 완료: %s / %s", metadata.get("artist"), current_slot)
    return {"status": "changed", **info}


def run_server_banner_scheduler():
    """Must be explicitly enabled; existing alert workers remain independent."""
    if os.getenv("SERVER_BANNER_ENABLED", "0") != "1":
        return
    attempted = None
    while True:
        try:
            now_slot = slot()
            if now_slot and attempted != now_slot:
                attempted = now_slot
                logging.info("server-banner result: %s", rotate_once())
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
