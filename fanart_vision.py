"""Cache-first, opt-in visual scoring for Hiyori guild-banner candidate images.

The 1 GB Oracle instance cannot host a useful vision model locally. Once an
operator configures OPENAI_API_KEY and SERVER_BANNER_VISION_ENABLED=1, a bounded
background worker scores cached fanart against the official Hiyori avatar.
The banner scheduler only READS scored results; API failures never produce a
positive classification or interrupt alerts.
"""
import base64
import hashlib
import io
import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps
from dotenv import load_dotenv

from fanart_discovery import BASE, QUEUE, read
from fanart_usage import canonical_post

load_dotenv(BASE.parent / ".env")
CACHE = BASE / "fanart_vision_scores.json"
MODEL = os.getenv("SERVER_BANNER_VISION_MODEL", "gpt-4.1-mini")
SCHEMA_VERSION = 1
_LOCK = threading.Lock()
_MAX_IMAGE_BYTES = 900_000
_MAX_API_CALLS = 5


def enabled():
    return os.getenv("SERVER_BANNER_VISION_ENABLED", "0") == "1"


def ready():
    return enabled() and bool(os.getenv("OPENAI_API_KEY", "").strip())


def _reference():
    """Only a verified official YouTube avatar; not arbitrary user text."""
    from creator_profiles import avatar
    url = avatar("hanayo")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname in ("yt3.googleusercontent.com", "yt3.ggpht.com"):
        return url
    return ""


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _cached_result(path, reference):
    digest = _digest(path)
    record = read(CACHE).get(digest)
    if not isinstance(record, dict) or record.get("version") != SCHEMA_VERSION:
        return digest, None
    if record.get("model") != MODEL or record.get("reference_sha") != hashlib.sha256(reference.encode()).hexdigest():
        return digest, None
    return digest, record


def classification(path):
    """Read-only; never calls an API from a Discord message or banner rotation."""
    if not ready():
        return None
    reference = _reference()
    if not reference:
        return None
    try:
        _, record = _cached_result(Path(path), reference)
        return record
    except (OSError, ValueError):
        return None


def _jpeg_data(path):
    with Image.open(path) as img:
        if getattr(img, "is_animated", False):
            raise ValueError("Animated candidate")
        image = ImageOps.exif_transpose(img).convert("RGB")
        image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
        data = io.BytesIO()
        image.save(data, "JPEG", quality=80, optimize=True)
        if not 0 < data.tell() <= _MAX_IMAGE_BYTES:
            raise ValueError("Candidate JPEG too large")
        return base64.b64encode(data.getvalue()).decode("ascii")


def _bounded_score(value):
    if isinstance(value, bool):
        raise ValueError("Boolean score")
    score = float(value)
    if not 0 <= score <= 1:
        raise ValueError("Score out of range")
    return score


def _analyze(path, reference, session=None):
    """Send only the candidate image and an official avatar to a vision API."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or not reference:
        raise ValueError("Vision key or reference missing")
    prompt = (
        "Compare IMAGE 1 (a fan illustration) against IMAGE 2 (official avatar of "
        "the fictional VTuber character Hanayo Hiyori). Base your verdict on "
        "VISIBLE CONTENT, not hashtags, caption or social context. "
        "Assess whether a drawn anime character is depicted, whether Hiyori "
        "is recognizably depicted based on IMAGE 2, whether she is visually "
        "prominent even in a multi-character group illustration, and whether "
        "the scene works as a wide Discord server banner with the character "
        "clearly visible after a 16:9 crop. If ambiguous, mark lower confidence. "
        "Return ONLY a JSON object with: has_character (boolean), "
        "hiyori_present (boolean), hiyori_prominence (0..1), "
        "banner_composition (0..1), confidence (0..1). "
        "Do not assume a character is present merely because a drawing is colorful."
    )
    body = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 210,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _jpeg_data(path), "detail": "high"}},
            {"type": "image_url", "image_url": {"url": reference, "detail": "high"}},
        ]}]
    }
    response = (session or requests).post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json=body, timeout=(5, 24))
    response.raise_for_status()
    data = json.loads(response.json()["choices"][0]["message"]["content"])
    if not isinstance(data.get("has_character"), bool) or not isinstance(data.get("hiyori_present"), bool):
        raise ValueError("Invalid vision boolean fields")
    prominence = _bounded_score(data["hiyori_prominence"])
    composition = _bounded_score(data["banner_composition"])
    confidence = _bounded_score(data["confidence"])
    score = round(100 * (0.45 * prominence + 0.35 * composition + 0.2 * confidence))
    # Avoid interpreting vague images as confidently depicting Hiyori.
    qualified = (data["has_character"] and data["hiyori_present"]
                 and prominence >= 0.45 and composition >= 0.55 and confidence >= 0.75
                 and score >= 65)
    return {"has_character": data["has_character"],
            "hiyori_present": data["hiyori_present"],
            "hiyori_prominence": prominence, "banner_composition": composition,
            "confidence": confidence, "score": score, "banner_eligible": bool(qualified)}


def scan(max_new=_MAX_API_CALLS):
    """Bounded periodic backfill; skip unknown or missing credentials.

    No public messages are posted, no profile media is downloaded by this task.
    """
    if not enabled():
        return {"status": "off", "analyzed": 0}
    if not ready():
        logging.warning("BANNER_VISION_UNAVAILABLE reason=missing_api_key")
        return {"status": "missing_api_key", "analyzed": 0}
    reference = _reference()
    if not reference:
        logging.warning("BANNER_VISION_UNAVAILABLE reason=missing_official_avatar")
        return {"status": "missing_official_avatar", "analyzed": 0}
    from fanart_discovery import classify
    root = (BASE / "fanart_candidates" / "hanayo").resolve()
    if not root.is_dir():
        return {"status": "no_cached_images", "analyzed": 0}
    analyzed = 0
    checked = 0
    seen = set()
    with _LOCK:
        cache = read(CACHE)
        for item in read(QUEUE).values():
            if analyzed >= min(max_new, _MAX_API_CALLS):
                break
            if not isinstance(item, dict) or item.get("group") != "hanayo":
                continue
            if item.get("hashtag", "").lower() != "#hiyoriart_":
                continue
            if item.get("server_banner_excluded") or item.get("review_status") == "blocked" or item.get("decision") == "blocked" or classify(item.get("text", "")) == "blocked":
                continue
            if not canonical_post(item.get("post_url")):
                continue
            for rel in item.get("cached_files", [])[:4]:
                if analyzed >= min(max_new, _MAX_API_CALLS):
                    break
                path = (BASE / rel).resolve() if isinstance(rel, str) else BASE
                if root not in path.parents or not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    continue
                try:
                    with Image.open(path) as im:
                        w, h = ImageOps.exif_transpose(im).size
                    if w < 960 or h < 540 or w / h < 1.4 or w / h > 2.0:
                        continue
                    digest = _digest(path)
                    if digest in seen:
                        continue
                    seen.add(digest)
                    previous = cache.get(digest)
                    refhash = hashlib.sha256(reference.encode()).hexdigest()
                    if isinstance(previous, dict) and previous.get("model") == MODEL and previous.get("version") == SCHEMA_VERSION and previous.get("reference_sha") == refhash:
                        continue
                    result = _analyze(path, reference)
                    cache[digest] = {**result, "model": MODEL, "version": SCHEMA_VERSION, "reference_sha": refhash}
                    from resilience import save_json
                    CACHE.parent.mkdir(parents=True, exist_ok=True)
                    save_json(CACHE, cache)
                    analyzed += 1
                    checked += 1
                    logging.info("BANNER_VISION_ANALYZED eligible=%s score=%d", result["banner_eligible"], result["score"])
                except (OSError, ValueError, KeyError, requests.RequestException) as exc:
                    logging.warning("BANNER_VISION_SCAN_FAILURE reason=%s", type(exc).__name__)
                    return {"status": "api_error", "analyzed": analyzed}
    return {"status": "ok", "analyzed": analyzed, "checked": checked}
