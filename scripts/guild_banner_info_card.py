"""One persistent Components V2 card for the current Discord guild banner."""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

CHANNEL_ID = "1567561255764500551"
KST = ZoneInfo("Asia/Seoul")
API = "https://discord.com/api/v10"
HEADERS = {"User-Agent": "abyss-guild-banner-card/1.0"}


def card_payload(state):
    artist = str(state.get("artist") or "작가 정보 없음")[:90]
    if not re.fullmatch(r"@[A-Za-z0-9_]{1,15}", artist):
        artist = "작가 정보 확인 중"
    post_url = str(state.get("post_url") or "")
    if not re.fullmatch(r"https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]+", post_url):
        post_url = None
    stamp = state.get("updated_at")
    try:
        updated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(KST)
        updated_label = updated.strftime("%Y.%m.%d %H:%M KST")
    except (ValueError, TypeError):
        updated_label = "기록 없음"
    parts = [
        {"type": 10, "content": "## 🌸 ABYSS · SERVER BANNER\n-# 하나요 히요리 · 현재 적용 중인 서버 배너"},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 12, "items": [{"media": {"url": "attachment://current_banner.jpg"},
                               "description": "현재 디스코드 서버 배너 · 하나요 히요리"}]},
        {"type": 10, "content": (
            "**🎨 현재 팬아트**\n"
            f"작가 · {artist}\n"
            f"적용 · {updated_label}\n"
            "자동 변경 · 매일 08:00 / 18:00 KST\n"
            "규격 · 960 × 540"
        )},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "-# 작가님께 감사드립니다. 원본 링크에서 작품을 확인하실 수 있습니다."},
    ]
    if post_url:
        parts.append({"type": 1, "components": [
            {"type": 2, "style": 5, "label": "🎨 팬아트 원본 보기", "url": post_url}
        ]})
    return {"flags": 32768, "allowed_mentions": {"parse": []},
            "components": [{"type": 17, "accent_color": 12234221, "components": parts}],
            "attachments": [{"id": 0, "filename": "current_banner.jpg",
                             "description": "현재 디스코드 서버 배너"}]}


def check_channel(token, guild_id):
    """Read only; never change permissions or create a message here."""
    headers = {**HEADERS, "Authorization": "Bot " + token}
    response = requests.get(API + "/channels/" + CHANNEL_ID, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    if str(data.get("guild_id")) != str(guild_id) or data.get("type") not in (0, 5):
        raise RuntimeError("Configured banner info channel is not a text/announcement channel in the target guild")
    return {"channel_id": CHANNEL_ID, "channel_type": data["type"]}


def upsert_card(token, guild_id, image_bytes, state, existing_id=None):
    """Update the bot's original card; create only when missing or deleted.

    Sending needs VIEW_CHANNEL + SEND_MESSAGES + ATTACH_FILES in the target
    channel. Never use Manage Channels or edit member permissions here.
    """
    if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
        raise ValueError("Invalid banner bytes")
    check_channel(token, guild_id)
    headers = {**HEADERS, "Authorization": "Bot " + token}
    payload = card_payload(state)
    filename = ("current_banner.jpg", image_bytes, "image/jpeg")
    if existing_id and str(existing_id).isdigit():
        resp = requests.patch(API + "/channels/" + CHANNEL_ID + "/messages/" + str(existing_id),
                              headers=headers, data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                              files={"files[0]": filename}, timeout=35)
        if resp.status_code != 404:
            resp.raise_for_status()
            return str(resp.json()["id"]), "edited"
    resp = requests.post(API + "/channels/" + CHANNEL_ID + "/messages",
                         headers=headers, data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                         files={"files[0]": filename}, timeout=35)
    resp.raise_for_status()
    return str(resp.json()["id"]), "created"
