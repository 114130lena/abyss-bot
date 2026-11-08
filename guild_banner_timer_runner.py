"""Idempotent scheduled backup for the SAME banner state as the running bot.

Only the main bot rotates at 08:00/18:00. This runner is the GitHub 08:27
and 18:27 backup if the bot missed its slot. No legacy banner database or
separate eligibility rules are used. Existing file flock protects backups.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")


def target_slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    if now.hour not in (8, 18):
        return None
    return now.replace(minute=0, second=0, microsecond=0)


def run(now=None):
    load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")
    slot = target_slot(now)
    if slot is None:
        return {"status": "outside_window"}
    from guild_server_banner import rotate_once
    result = rotate_once(now=slot)
    return {"status": result.get("status", "unknown"),
            "candidate_total": result.get("candidate_total", 0),
            "target_slot": slot.strftime("%Y-%m-%d %H:%M")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = run()
    print("GUILD_BANNER_BACKUP " + json.dumps(data, ensure_ascii=False))
    raise SystemExit(0 if data["status"] in ("changed", "already_rotated", "outside_window") else 1)
