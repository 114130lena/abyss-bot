"""Read-only health check for canonical 08:00/18:00 Discord guild banner.

The bot, its GitHub backup runner and this monitor share *one*
creator_media/guild_banner_rotation_state.json file. A successful old timer
slot in a different database cannot mask a missed current-bot rotation.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")


def expected_slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    if now.hour >= 18:
        hour, day = 18, now
    elif now.hour >= 8:
        hour, day = 8, now
    else:
        hour, day = 18, now - timedelta(days=1)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def check(now=None, current_hash=None):
    load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")
    from guild_server_banner import read_state, _current_guild_banner
    expected = expected_slot(now).strftime("%Y-%m-%d %H:%M")
    state = read_state()
    last_slot = state.get("last_slot")
    recorded_hash = state.get("guild_banner_hash")
    if current_hash is None:
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        guild = os.getenv("DISCORD_GUILD_ID", "").strip()
        if not token or not guild.isdigit():
            return {"status": "missing_credentials", "expected_slot": expected}
        current_hash = _current_guild_banner(token, guild)
    if last_slot != expected:
        status = "missing_slot"
    elif not recorded_hash or recorded_hash != current_hash:
        status = "banner_hash_mismatch"
    elif state.get("banner_info_updated_hash") != current_hash:
        status = "info_card_pending"
    else:
        status = "ok"
    return {"status": status, "expected_slot": expected,
            "last_slot": str(last_slot or ""),
            "candidate_total": int(state.get("candidate_total") or 0)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = check()
    except Exception as exc:
        logging.warning("BANNER_HEALTH_FAILED reason=%s", type(exc).__name__)
        result = {"status": "unavailable"}
    print("BANNER_HEALTH " + json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "ok" else 1)
