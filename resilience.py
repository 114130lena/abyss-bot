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
    """Fetch up to four X photos concurrently; preserve original URLs on errors."""
    import io
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.parse import urlsplit
    from PIL import Image

    candidates = []
    for container in prepared.get("components", []):
        for block in container.get("components", []):
            if block.get("type") != 12:
                continue
            for item in block.get("items", []):
                media = item.get("media", {})
                source = media.get("url", "")
                parsed = urlsplit(source)
                if parsed.scheme == "https" and parsed.hostname == "pbs.twimg.com" and parsed.path.startswith("/media/"):
                    candidates.append((media, source))
                if len(candidates) >= 4:
                    break
            if len(candidates) >= 4:
                break
        if len(candidates) >= 4:
            break

    if not candidates:
        return []

    def fetch(index, source):
        try:
            with requests.get(source, stream=True, timeout=(2, 5), allow_redirects=False) as res:
                res.raise_for_status()
                if not res.headers.get("Content-Type", "").lower().startswith("image/"):
                    return None
                raw = bytearray()
                for part in res.iter_content(65536):
                    raw.extend(part)
                    if len(raw) > 8_000_000:
                        raise ValueError("X image exceeds attachment limit")
            with Image.open(io.BytesIO(raw)) as im:
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ("JPEG", "PNG", "WEBP"):
                    return None
                kind = im.format
            ext = ".png" if kind == "PNG" else ".webp" if kind == "WEBP" else ".jpg"
            mime = "image/png" if kind == "PNG" else "image/webp" if kind == "WEBP" else "image/jpeg"
            return (index, "abyss_x_media_" + str(index) + ext, bytes(raw), mime)
        except (requests.RequestException, ValueError, OSError, TypeError) as exc:
            logging.warning("X photo fetch failed, retaining original URL: %s", type(exc).__name__)
            return None

    results = {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
        futures = [pool.submit(fetch, index, source) for index, (_, source) in enumerate(candidates)]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results[result[0]] = result
    attachments = []
    for index in sorted(results):
        _, name, raw, mime = results[index]
        candidates[index][0]["url"] = "attachment://" + name
        attachments.append((name, raw, mime))
    logging.info("X_MEDIA_DOWNLOAD count=%d success=%d elapsed_seconds=%.2f",
                 len(candidates), len(attachments), time.monotonic() - started)
    return attachments

def _collect_x_video_attachments(prepared):
    """Attach a small X MP4 to Discord for reliable in-app playback.

    Large, rejected, or unavailable videos retain their original remote URL.
    No redirects; no untrusted hosts; bounded downloads and no extra video API.
    """
    import io
    from urllib.parse import urlsplit
    maximum = 5_500_000
    for container in prepared.get("components", []):
        for block in container.get("components", []):
            if block.get("type") != 12:
                continue
            for item in block.get("items", []):
                media = item.get("media") or {}
                source = media.get("url", "")
                parsed = urlsplit(str(source))
                if not (parsed.scheme == "https"
                        and parsed.hostname == "video.twimg.com"
                        and parsed.path.lower().endswith(".mp4")):
                    continue
                started = time.monotonic()
                try:
                    with requests.get(source, stream=True, timeout=(2, 4),
                                      allow_redirects=False) as response:
                        response.raise_for_status()
                        if response.status_code != 200:
                            continue
                        mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                        if mime != "video/mp4":
                            continue
                        length = response.headers.get("Content-Length", "")
                        if length.isdigit() and int(length) > maximum:
                            logging.info("X_VIDEO_ATTACHMENT_SKIP reason=too_large bytes=%s", length)
                            continue
                        raw = bytearray()
                        for chunk in response.iter_content(65536):
                            raw.extend(chunk)
                            if len(raw) > maximum:
                                raise ValueError("video size exceeds Discord small-clip limit")
                    if not raw:
                        continue
                    name = "abyss_x_video.mp4"
                    media["url"] = "attachment://" + name
                    logging.info("X_VIDEO_ATTACHMENT_READY bytes=%d elapsed_seconds=%.2f",
                                 len(raw), time.monotonic() - started)
                    return [(name, bytes(raw), "video/mp4")]
                except (requests.RequestException, OSError, ValueError, TypeError) as exc:
                    logging.info("X_VIDEO_ATTACHMENT_FALLBACK reason=%s", type(exc).__name__)
    return []


def _role_mention_expectations(payload):
    """Only roles explicitly allowed and rendered in Text Display can be pinged."""
    allowed = payload.get("allowed_mentions") or {}
    roles = [str(r) for r in allowed.get("roles", []) if str(r).isdigit()]
    texts = []
    def visit(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == 10:
                texts.append(str(item.get("content", "")))
            visit(item.get("components") or [])
    visit(payload.get("components") or [])
    content = "\\n".join(texts)
    return [role for role in roles if "<@&" + role + ">" in content]


def _audit_role_mention(response, payload):
    """Verify whether Discord parsed the configured role; never resend to test."""
    expected = _role_mention_expectations(payload)
    allowed = (payload.get("allowed_mentions") or {}).get("roles", [])
    if allowed and not expected:
        logging.warning("DISCORD_ROLE_MENTION_PAYLOAD_INVALID roles_allowed=%d", len(allowed))
        return
    if not expected:
        return  # Reposts and test previews intentionally have no role ping.
    if response.status_code != 200:
        logging.warning("DISCORD_ROLE_MENTION_UNVERIFIED reason=no_message_response http=%s",
                        response.status_code)
        return
    try:
        data = response.json()
    except (ValueError, TypeError):
        logging.warning("DISCORD_ROLE_MENTION_UNVERIFIED reason=invalid_response")
        return
    if not isinstance(data, dict):
        logging.warning("DISCORD_ROLE_MENTION_UNVERIFIED reason=missing_message")
        return
    actual = [str(role) for role in data.get("mention_roles", [])]
    missing = [role for role in expected if role not in actual]
    if missing:
        logging.warning("DISCORD_ROLE_MENTION_MISSING expected=%s actual=%s message_id=%s",
                        ",".join(expected), ",".join(actual), str(data.get("id", "")))
    else:
        logging.info("DISCORD_ROLE_MENTION_RECOGNIZED count=%d message_id=%s",
                     len(expected), str(data.get("id", "")))
    if data.get("flags", 0) & 4096:
        logging.warning("DISCORD_ROLE_MENTION_SUPPRESSED flag=SUPPRESS_NOTIFICATIONS")


def send_webhook(url, payload, attempts=3):
    """Send once per explicit source event across restarts and concurrent workers.

    The identity is internal-only. In an ambiguous failure the claim is left
    pending for review, never blindly re-sent with duplicate role mentions.
    """
    key = payload.get("_abyss_event_key")
    if key is None:
        return _send_webhook_transport(url, payload, attempts)
    from discord_event_dedup import claim, update
    if not claim(key):
        return None
    cleaned = {k: v for k, v in payload.items() if k != "_abyss_event_key"}
    try:
        outcome = _send_webhook_transport(url, cleaned, attempts)
        update(key, "sent")
        return outcome
    except Exception:
        # Could have been accepted by Discord before failure or process death.
        try:
            update(key, "uncertain")
        except (OSError, ValueError, TypeError):
            logging.exception("DISCORD_EVENT_UNCERTAIN_LEDGER_UPDATE_FAILED key=%s", key)
        raise


def _send_webhook_transport(url, payload, attempts=3):
    """Retry transient HTTP/network failures, respecting Discord retry_after."""
    if not url.startswith("https://"):
        raise ValueError("Invalid Discord webhook URL")
    if payload.get("flags", 0) & 32768:
        separator = "&" if "?" in url else "?"
        url = url + separator + "with_components=true"
    # Discord normally returns 204. wait=true returns the message object, letting
    # us distinguish a visible @role from a role that Discord actually parsed.
    if "wait=" not in url:
        url += ("&" if "?" in url else "?") + "wait=true"
    for attempt in range(attempts):
        delivery_started = time.monotonic()
        try:
            # Internal metadata is stripped before the Discord API request.
            photo_unknown = bool(payload.get('_abyss_photo_unknown'))
            has_video = bool(payload.get('_abyss_has_video'))
            verified_text_only = bool(payload.get('_abyss_verified_text_only'))
            prepared = dict(payload)
            prepared.pop('_abyss_photo_unknown', None)
            prepared.pop('_abyss_has_video', None)
            prepared.pop('_abyss_verified_text_only', None)
            chosen = None
            selected_urls = None
            # X/Cafe posts with their own photos keep the original official banner.
            # Skip randomized local attachments entirely for those notifications.
            native_post_photo = photo_unknown or has_video
            try:
                from fanart_url_media import has_post_photos
                native_post_photo = native_post_photo or has_post_photos(payload)
                tag = " ".join(c.get("content","") for box in payload.get("components",[]) if box.get("type")==17 for c in box.get("components",[]) if c.get("type")==10)
                native_post_photo = native_post_photo or "YOUTUBE" in tag or ("CHZZK" in tag and "다시보기" in tag)
                # X may list a photo-free item before its photo metadata arrives.
                # Only confirmed original-media absence can authorize fanart.
                if "𝕏 X" in tag and not verified_text_only:
                    native_post_photo = True
                    logging.info("X_FANART_BLOCKED reason=unverified_media")
            except Exception as exc:
                logging.warning("게시글 이미지 확인 실패: %s", exc)
                native_post_photo = True
            if not native_post_photo and os.getenv("ABYSS_RANDOM_MEDIA", "0").lower() in ("1", "true", "yes"):
                try:
                    from random_media import prepare_payload
                    prepared, chosen = prepare_payload(payload)
                except Exception as exc:
                    logging.warning("랜덤 이미지 준비 실패, 기존 이미지 사용: %s", exc)
            if not native_post_photo and os.getenv("ABYSS_NOTIFICATION_FANART", "1").lower() not in ("0", "false", "no"):
                try:
                    from fanart_url_media import apply as apply_fanart_urls
                    linked, selected_urls = apply_fanart_urls(prepared)
                    if selected_urls:
                        # Keep 'chosen' so the randomized official profile file is attached.
                        # Fanart replaces only the footer URL, not the profile attachment.
                        prepared = linked
                        logging.info('FANART_SELECTED group=%s source=%s', selected_urls.get('group'), selected_urls.get('banner', ('','',''))[2])
                except Exception as exc:
                    logging.warning("팬아트 링크 선택 실패: %s", exc)
            # Sanitize after all media decorators: they may return the original
            # payload, which still contains internal classification markers.
            import copy
            prepared = copy.deepcopy(prepared)
            for private_key in ("_abyss_photo_unknown","_abyss_has_video","_abyss_verified_text_only"):
                prepared.pop(private_key,None)
            # A curated local fanart image requires a real Discord attachment;
            # using attachment:// without uploading the file leaves a blank card.
            local_fanart = None
            if selected_urls and selected_urls.get("local_path"):
                from fanart_discovery import BASE as fanart_base
                candidate = Path(selected_urls["local_path"]).resolve()
                root = fanart_base.resolve()
                if (root in candidate.parents and candidate.is_file()
                        and candidate.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                        and 0 < candidate.stat().st_size <= 8_000_000):
                    local_fanart = candidate
                else:
                    # Do not send an invalid dangling attachment reference.
                    logging.warning("FANART_LOCAL_INVALID; restoring original footer")
                    prepared = dict(payload)
                    selected_urls = None
                    chosen = None
            x_photos = _collect_x_image_attachments(prepared)
            x_videos = _collect_x_video_attachments(prepared)
            if chosen or x_photos or x_videos or local_fanart:
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
                    for i,(filename,data,mime) in enumerate(x_videos):
                        uploaded[f"files[{i+offset+len(x_photos)}]"] = (filename,io.BytesIO(data),mime)
                    if local_fanart:
                        extension = local_fanart.suffix.lower()
                        mime = ("image/png" if extension == ".png" else
                                "image/webp" if extension == ".webp" else "image/jpeg")
                        filename = "abyss_notification_fanart" + extension
                        uploaded[f"files[{offset + len(x_photos) + len(x_videos)}]"] = (
                            filename, stack.enter_context(local_fanart.open("rb")), mime)
                    response = requests.post(url, data={"payload_json": json.dumps(prepared, ensure_ascii=False)},
                                             files=uploaded, timeout=35)
            else:
                response = requests.post(url, json=prepared, timeout=12)
            # A 5xx response does not prove the Discord write failed. Retrying
            # could create a second message or ping users twice. Fail closed:
            # the caller retains its pre-send claim as uncertain for review.
            if response.status_code in (500, 502, 503, 504):
                logging.warning("DISCORD_DELIVERY_UNCERTAIN_HTTP status=%d; automatic_retry_blocked",
                                response.status_code)
                try:
                    from delivery_review import record
                    record(payload, "HTTP_" + str(response.status_code))
                except (OSError, ValueError, TypeError) as exc:
                    logging.warning("Delivery review journal unavailable: %s", type(exc).__name__)
                response.raise_for_status()
                raise requests.HTTPError("Ambiguous Discord 5xx delivery")
            if response.status_code != 429:
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
                        group, local_selection = chosen
                        # An attributed X fanart gallery replaces the random
                        # footer banner. Record only images actually shown,
                        # not a hidden local banner attachment.
                        if selected_urls:
                            local_selection = {key: value for key, value in local_selection.items()
                                               if key != "banner"}
                        if local_selection:
                            mark_delivered(group, local_selection)
                    except Exception as exc:
                        logging.warning("최근 랜덤 이미지 기록 실패: %s", exc)
                _audit_role_mention(response, prepared)
                logging.info("DISCORD_WEBHOOK_DELIVERED attempt=%d elapsed_seconds=%.2f files=%d",
                             attempt + 1, time.monotonic() - delivery_started,
                             len(x_photos) + len(x_videos) + (2 if chosen else 0) + (1 if local_fanart else 0))
                if time.monotonic() - delivery_started > 15:
                    logging.warning("DISCORD_DELIVERY_SLOW elapsed_seconds=%.2f x_photos=%d x_videos=%d",
                                    time.monotonic() - delivery_started, len(x_photos), len(x_videos))
                # Best-effort, privacy-safe timing; telemetry must never block alerts.
                try:
                    from delivery_latency import record_transport
                    record_transport(time.monotonic() - delivery_started,
                                     len(x_photos) + len(x_videos)
                                     + (2 if chosen else 0) + (1 if local_fanart else 0))
                except (ImportError, OSError, ValueError, TypeError) as exc:
                    logging.warning("DISCORD_LATENCY_METRICS_UNAVAILABLE reason=%s",
                                    type(exc).__name__)
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
            try:
                from delivery_review import record
                record(payload, "ReadTimeout")
            except (OSError, ValueError, TypeError) as exc:
                logging.warning("Delivery review journal unavailable: %s", type(exc).__name__)
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
            try:
                from delivery_review import record
                record(payload, "ConnectionError")
            except (OSError, ValueError, TypeError) as exc:
                logging.warning("Delivery review journal unavailable: %s", type(exc).__name__)
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
