"""Scheduled Discord *server* banner rotation using approved Hanayo fanart.

Separate from banner_manager.py (the official YouTube notification images).
Images must be explicitly approved for public banner use and placed in:
    $BOT_DATA_DIR/hanayo_banner_approved/
The bot neither scrapes nor silently licenses fan artwork.
"""
import base64
import hashlib
import io
import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError

KST = ZoneInfo("Asia/Seoul")
SLOTS = {(8, 0), (18, 0)}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
SIZE = (960, 540)


def base_dir():
    return Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))


def fanart_dir():
    return Path(os.getenv("HANAYO_BANNER_DIR", str(base_dir() / "hanayo_banner_approved")))


def state_path():
    return base_dir() / "server_banner_state.json"


def ratio_type(width, height):
    """Accept near-16:9 and near-16:10 landscapes; ignore square/vertical."""
    if min(width, height) <= 0 or width < 960 or height < 540:
        return None
    ratio = width / height
    if abs(ratio - 16 / 9) <= 0.035:
        return "16:9"
    if abs(ratio - 16 / 10) <= 0.03:
        return "16:10"
    return None


def candidates(folder=None):
    root = folder or fanart_dir()
    if not root.exists():
        return []
    result = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        try:
            with Image.open(path) as im:
                # Avoid decompression bombs and animated files.
                if getattr(im, "is_animated", False):
                    continue
                if ratio_type(*ImageOps.exif_transpose(im).size):
                    result.append(path)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            logging.warning("배너 후보 검사 실패 %s: %s", path.name, exc)
    return result


def render(path):
    """Never crop 16:10 art: extend with a blurred backdrop instead."""
    with Image.open(path) as source:
        source = ImageOps.exif_transpose(source)
        kind = ratio_type(*source.size)
        if kind is None:
            raise ValueError("Not an eligible 16:9 or 16:10 image")
        art = source.convert("RGB")
        if kind == "16:9":
            result = ImageOps.fit(art, SIZE, method=Image.Resampling.LANCZOS,
                                  centering=(0.5, 0.5))
        else:
            backdrop = ImageOps.fit(art, SIZE, method=Image.Resampling.LANCZOS)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=24))
            foreground = ImageOps.contain(art, SIZE, method=Image.Resampling.LANCZOS)
            left = (SIZE[0] - foreground.width) // 2
            top = (SIZE[1] - foreground.height) // 2
            backdrop.paste(foreground, (left, top))
            result = backdrop
        output = io.BytesIO()
        result.save(output, format="JPEG", quality=89, optimize=True)
        payload = output.getvalue()
        if len(payload) > MAX_OUTPUT_BYTES:
            raise ValueError("Processed banner is too large")
        return payload


def load_state():
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def slot_key(now=None):
    local = (now or datetime.now(KST)).astimezone(KST)
    if (local.hour, local.minute) not in SLOTS:
        return None
    return local.strftime("%Y-%m-%d %H:%M")


def choose_image(paths, previous_sha=None):
    # Hash content rather than filename to prevent duplicate crops/renames.
    hashes = [(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths]
    pool = [(path, digest) for path, digest in hashes if digest != previous_sha]
    if not pool:
        return None
    return random.choice(pool)


def update_server_banner(image_bytes, token, guild_id):
    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = requests.patch(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json={"banner": "data:image/jpeg;base64," + encoded},
        timeout=25,
    )
    response.raise_for_status()


def run_once(now=None):
    """Run only in the two KST minute windows, once per successful slot."""
    key = slot_key(now)
    if not key:
        return False
    state = load_state()
    if state.get("last_slot") == key:
        return False
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not token or not guild_id.isdigit():
        logging.error("서버 배너: DISCORD_BOT_TOKEN / DISCORD_GUILD_ID 설정 필요")
        return False
    selection = choose_image(candidates(), state.get("last_image_sha"))
    if selection is None:
        logging.warning("서버 배너: 사용 허가된 새 히요리 팬아트가 없습니다. 기존 배너 유지")
        return False
    path, digest = selection
    image_bytes = render(path)
    update_server_banner(image_bytes, token, guild_id)
    save_state({"last_slot": key, "last_image_sha": digest, "last_image": path.name})
    logging.info("서버 배너 교체 성공: %s / %s", key, path.name)
    return True


def run_server_banner_scheduler():
    """Opt-in: leave existing bot/notifications untouched by default."""
    if os.getenv("SERVER_BANNER_ENABLED", "0").strip() != "1":
        logging.info("서버 배너 변경 기능 비활성화 (SERVER_BANNER_ENABLED=1 설정)")
        return
    last_attempt = None
    while True:
        try:
            key = slot_key()
            if key and key != last_attempt:
                last_attempt = key  # No API retry storms within a slot.
                run_once()
        except Exception:
            logging.exception("서버 배너 변경 실패: 기존 배너 유지")
        time.sleep(10)
