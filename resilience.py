"""Shared Discord delivery, persistent state and throttled operator alerts."""
import json
import logging
import os
import random
import time
from pathlib import Path
import requests

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
ADMIN_WEBHOOK = os.getenv("ADMIN_DISCORD_WEBHOOK_URL", "").strip()
_last_alert = {}

def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default

def _collect_x_image_attachments(prepared):
    """Resolve only X media photo URLs and attach verified image bytes.

    Original URL remains untouched if fetching the photo fails.
    """
    import io
    from urllib.parse import urlsplit
    from PIL import Image
    attachments = []
    for container in prepared.get("components", []):
        for block in container.get("components", []):
            if block.get("type") != 12:
                continue
            for item in block.get("items", []):
                media = item.get("media", {})
                source = media.get("url", "")
                parsed = urlsplit(source)
                if parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com" or not parsed.path.startswith("/media/"):
                    continue
                if len(attachments) >= 4:
                    return attachments
                try:
                    with requests.get(source, stream=True, timeout=12, allow_redirects=False) as res:
                        res.raise_for_status()
                        if not res.headers.get("Content-Type", "").lower().startswith("image/"):
                            continue
                        raw = bytearray()
                        for part in res.iter_content(65536):
                            raw.extend(part)
                            if len(raw) > 8_000_000:
                                raise ValueError("X image exceeds attachment limit")
                    with Image.open(io.BytesIO(raw)) as im:
                        im.verify()
                    with Image.open(io.BytesIO(raw)) as im:
                        if im.format not in ("JPEG", "PNG", "WEBP"):
                            continue
                        kind = im.format
                    ext = ".png" if kind == "PNG" else ".webp" if kind == "WEBP" else ".jpg"
                    name = "abyss_x_media_" + str(len(attachments)) + ext
                    media["url"] = "attachment://" + name
                    mime = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
                    attachments.append((name, bytes(raw), mime))
                except (requests.RequestException, ValueError, OSError, TypeError) as exc:
                    logging.warning("X post picture URL fetch failed; original URL retained: %s", type(exc).__name__)
    return attachments

def send_webhook(url, payload, attempts=3):
    """Retry transient HTTP/network failures, respecting Discord retry_after."""
    if not url.startswith("https://"):
        raise ValueError("Invalid Discord webhook URL")
    if payload.get("flags", 0) & 32768:
        separator = "&" if "?" in url else "?"
        url = url + separator + "with_components=true"
    for attempt in range(attempts):
        try:
            prepared = payload
            chosen = None
            selected_urls = None
            if os.getenv("ABYSS_RANDOM_MEDIA", "0").lower() in ("1", "true", "yes"):
                try:
                    from random_media import prepare_payload
                    prepared, chosen = prepare_payload(payload)
                except Exception as exc:
                    logging.warning("랜덤 이미지 준비 실패, 기존 이미지 사용: %s", exc)
            if os.getenv("ABYSS_FANART_URLS", "0") == "1":
                try:
                    from fanart_url_media import apply as apply_fanart_urls
                    linked, selected_urls = apply_fanart_urls(prepared)
                    if selected_urls:
                        prepared, chosen = linked, None
                except Exception as exc:
                    logging.warning("팬아트 링크 선택 실패: %s", exc)
            # Attach X post photos alongside the existing random header/footer images.
            # Work from a fresh payload copy so retries never use stale attachment URLs.
            if prepared is payload:
                import copy
                prepared = copy.deepcopy(payload)
            x_photos = _collect_x_image_attachments(prepared)
            if chosen or x_photos:
                from contextlib import ExitStack
                import io
                with ExitStack() as stack:
                    uploaded = {}
                    offset = 0
                    if chosen:
                        group, selected = chosen
                        for i, kind in enumerate(("profile", "banner")):
                            path = selected[kind][0]
                            mime = ("image/png" if path.suffix.lower() == ".png"
                                    else "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg")
                            uploaded[f"files[{i}]"] = ("abyss_" + kind + path.suffix,
                                                       stack.enter_context(path.open("rb")), mime)
                        offset = 2
                    for i,(filename,data,mime) in enumerate(x_photos):
                        uploaded[f"files[{i+offset}]"] = (filename,io.BytesIO(data),mime)
                    response = requests.post(url, data={"payload_json": json.dumps(prepared, ensure_ascii=False)},
                                             files=uploaded, timeout=35)
            else:
                response = requests.post(url, json=prepared, timeout=12)
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
                if selected_urls:
                    try:
                        from fanart_url_media import mark_delivered as mark_fanart_delivered
                        mark_fanart_delivered(selected_urls)
                    except Exception as exc:
                        logging.warning("팬아트 사용 기록 저장 실패: %s", exc)
                if chosen:
                    try:
                        from random_media import mark_delivered
                        mark_delivered(*chosen)
                    except Exception as exc:
                        logging.warning("최근 랜덤 이미지 기록 실패: %s", exc)
                return
            if attempt == attempts - 1:
                response.raise_for_status()
            if response.status_code == 429:
                try:
                    delay = float(response.json().get("retry_after", 1))
                except (ValueError, TypeError, AttributeError):
                    delay = 1.0
            else:
                delay = 2 ** attempt
            time.sleep(min(max(delay, 0.5), 30))
        except requests.ReadTimeout:
            # Discord may have accepted the post even when its response was lost.
            # Blind retry could ping members twice; leave it unread for operator review.
            logging.warning("Discord response timed out; delivery uncertain, suppressing immediate retry")
            raise
        except requests.ConnectTimeout:
            # No TCP connection was established, so a retry is safe.
            if attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt + random.random(), 15))
        except requests.ConnectionError:
            # The connection may have dropped after Discord accepted the post.
            # Do not blindly resend a role mention.
            logging.warning("Discord connection lost; delivery uncertain, suppressing retry")
            raise

def warn_failure(service, failures, error):
    """Operator alerts are opt-in, not sent to public notification channels."""
    logging.warning("%s failure #%s: %s", service, failures, error)
    if failures < 3 or not ADMIN_WEBHOOK.startswith("https://"):
        return
    now = time.monotonic()
    if now - _last_alert.get(service, -100000) < 3600:
        return
    _last_alert[service] = now
    try:
        send_webhook(ADMIN_WEBHOOK, {
            "username": "어비스봇 상태 알림",
            "content": "",
            "embeds": [{"title": service + " 확인 오류", "description": str(error)[:1000], "color": 0xE67E22}],
            "allowed_mentions": {"parse": []}
        }, attempts=2)
    except Exception as exc:
        logging.warning("관리자 경고 전송 실패: %s", exc)
