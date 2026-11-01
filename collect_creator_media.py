"""Collect and preserve *official account* profile and banner image history.

Run: python collect_creator_media.py
Optional environment: X_BEARER_TOKEN, CHZZK_CLIENT_ID, CHZZK_CLIENT_SECRET.
No Discord messages are sent. The local uploaded hanayo_banner.jpg is NEVER read.
Only public platform media or verified account metadata is accepted.
"""
import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
from PIL import Image, UnidentifiedImageError
from dotenv import load_dotenv

from creator_profiles import CHANNELS as YT_CHANNELS, refresh as refresh_youtube_avatar
from banner_manager import refresh_one as refresh_youtube_banner
from creator_groups import CHZZK_GROUPS, chzzk_group

BASE = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE / ".env")
DEST = BASE / "creator_media"
INDEX = DEST / "index.json"
MAX_BYTES = 12 * 1024 * 1024
ACCOUNTS = {
    "hanayo": {"x": "HanayoHiyori"},
    "sakihane": {"x": "sakihanehuya"},
}

def read_index():
    try:
        obj = json.loads(INDEX.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}

def save_index(index):
    DEST.mkdir(parents=True, exist_ok=True)
    temp = INDEX.with_suffix(".tmp")
    temp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(INDEX)

def valid_url(url):
    if not isinstance(url, str) or len(url) > 3000:
        return False
    p = urlsplit(url)
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password

def download_image(group, kind, source, url, index, session=None):
    """Verify bytes and dimensions, retain old unique images by sha256."""
    if group not in ACCOUNTS or kind not in ("profile", "banner") or not valid_url(url):
        return False
    session = session or requests.Session()
    try:
        with session.get(url, timeout=18, stream=True, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            if not valid_url(response.url):
                raise ValueError("Invalid redirect target")
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                raise ValueError("Non-image response")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("Image exceeds size limit")
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = img.format
        if width < 64 or height < 64 or width * height > 40_000_000:
            raise ValueError("Invalid image dimensions")
        if kind == "banner" and width / height < 1.5:
            raise ValueError("Banner is not wide enough")
        if kind == "profile" and not 0.75 <= width / height <= 1.33:
            raise ValueError("Profile image is not square-ish")
        ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}.get(fmt)
        if not ext:
            raise ValueError("Unsupported image format")
        digest = hashlib.sha256(data).hexdigest()
        directory = DEST / group / kind
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (digest + ext)
        if not target.exists():
            target.write_bytes(data)
        items = index.setdefault(group, {}).setdefault(kind, [])
        if not any(item.get("sha256") == digest for item in items):
            items.append({"sha256": digest, "path": str(target.relative_to(DEST)),
                          "source": source, "original_url": url, "width": width,
                          "height": height, "collected_at": datetime.now(timezone.utc).isoformat()})
            return True
        return False
    except (requests.RequestException, OSError, UnidentifiedImageError, ValueError) as err:
        logging.warning("Media skipped %s %s %s: %s", group, source, kind, err)
        return False

def sources_youtube(group):
    # Reuse the existing validated public YouTube avatar/banner refreshers.
    result = []
    if group in YT_CHANNELS:
        profile = refresh_youtube_avatar(group, force=True)
        banner = refresh_youtube_banner(group, force=True)
        if valid_url(profile):
            result.append(("profile", "youtube", profile))
        if valid_url(banner):
            result.append(("banner", "youtube", banner))
    return result

def sources_chzzk(session):
    # Existing monitored IDs are authoritative; do not guess creator IDs.
    result = {"hanayo": [], "sakihane": []}
    channel_file = BASE / "channels.txt"
    try:
        ids = [x.strip() for x in channel_file.read_text(encoding="utf-8").splitlines()
               if x.strip() and not x.lstrip().startswith("#")]
    except OSError:
        ids = list(CHZZK_GROUPS)
    for cid in ids:
        if not cid.isalnum() or len(cid) > 80:
            continue
        group = chzzk_group(cid)
        try:
            response = session.get("https://api.chzzk.naver.com/service/v1/channels/" + cid,
                                   timeout=12, headers={"User-Agent": "Mozilla/5.0",
                                                       "Referer": "https://chzzk.naver.com/"})
            response.raise_for_status()
            body = response.json()
            info = body.get("content") or {}
            url = info.get("channelImageUrl")
            if valid_url(url):
                result[group].append(("profile", "chzzk", url))
        except (requests.RequestException, ValueError, KeyError, TypeError) as err:
            logging.warning("CHZZK channel metadata skipped %s: %s", cid, err)
    return result

def sources_x(group, session):
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        return []
    username = ACCOUNTS[group]["x"]
    try:
        response = session.get("https://api.x.com/2/users/by/username/" + username,
            params={"user.fields": "profile_image_url,profile_banner_url"},
            headers={"Authorization": "Bearer " + token}, timeout=12)
        response.raise_for_status()
        info = response.json().get("data") or {}
        out = []
        avatar = info.get("profile_image_url", "")
        if avatar:
            # Public X image endpoint offers the original rendition.
            avatar = avatar.replace("_normal.", ".")
            out.append(("profile", "x", avatar))
        if info.get("profile_banner_url"):
            out.append(("banner", "x", info["profile_banner_url"]))
        return out
    except (requests.RequestException, ValueError, TypeError) as err:
        logging.warning("X images unavailable for %s: %s", group, err)
        return []

def sources_x_free(group, session):
    """Public no-login profile/header discovery; fail closed if access is blocked."""
    try:
        from x_public_media import discover
        return discover(ACCOUNTS[group]["x"], session)
    except (ImportError, ValueError, TypeError) as err:
        logging.warning("Free X media collector unavailable: %s", err)
        return []

def sources_configured(group):
    # Other platforms can be added only from URLs explicitly verified/configured.
    out = []
    for platform in ("CHZZK", "CAFE", "X"):
        for kind in ("PROFILE", "BANNER"):
            url = os.getenv(group.upper() + "_" + platform + "_" + kind + "_URL", "")
            if valid_url(url):
                out.append((kind.lower(), platform.lower(), url))
    return out

def collect():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    index = read_index()
    # Remove mistakenly tagged widescreen X banners from profile metadata.
    for group_data in index.values():
        if isinstance(group_data, dict):
            group_data["profile"] = [entry for entry in group_data.get("profile", [])
                if isinstance(entry, dict) and 0.75 <= entry.get("width", 0) / max(entry.get("height", 1), 1) <= 1.33]
    added = 0
    session = requests.Session()
    chzzk = sources_chzzk(session)
    for group in ACCOUNTS:
        discovered = sources_youtube(group) + chzzk[group] + sources_x(group, session) + sources_x_free(group, session) + sources_configured(group)
        for kind, source, url in discovered:
            added += int(download_image(group, kind, source, url, index, session=session))
        save_index(index)
        logging.info("%s: %s profiles, %s banners",
                     group, len(index.get(group, {}).get("profile", [])),
                     len(index.get(group, {}).get("banner", [])))
    print(json.dumps({"added": added, "counts": {
        group: {kind: len(index.get(group, {}).get(kind, []))
                for kind in ("profile", "banner")} for group in ACCOUNTS
    }}, ensure_ascii=False))
    return index

if __name__ == "__main__":
    collect()
