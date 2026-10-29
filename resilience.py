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

def send_webhook(url, payload, attempts=3):
    """Retry transient HTTP/network failures, respecting Discord retry_after."""
    if not url.startswith("https://"):
        raise ValueError("Invalid Discord webhook URL")
    if payload.get("flags", 0) & 32768:
        separator = "&" if "?" in url else "?"
        url = url + separator + "with_components=true"
    for attempt in range(attempts):
        try:
            response = requests.post(url, json=payload, timeout=12)
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
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
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt + random.random(), 15))

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
