"""Idempotent scheduled backup for the SAME banner state as the running bot.

Only the main bot rotates at 08:00/18:00. GitHub provides two early
08:04/08:07 and 18:04/18:07 recovery opportunities. A delayed GitHub job
must never rotate a banner at night or after the first 10 minutes.
No legacy banner database or separate eligibility rules are used.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")


def target_slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    # GitHub scheduled jobs can start hours after their intended cron time.
    # Never replay yesterday's/missed slot outside its own KST hour.
    # The main bot owns 08:00/18:00; a delayed backup may recover only
    # within 00:00-09:59 of the same KST scheduled hour.
    if now.hour not in (8, 18) or now.minute >= 10:
        return None
    return now.replace(minute=0, second=0, microsecond=0)


def run(now=None):
    load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")
    slot = target_slot(now)
    if slot is None:
        # A delayed GitHub job is not an error and must never write to Discord.
        return {"status": "outside_schedule", "candidate_total": 0,
                "target_slot": None}
    from guild_server_banner import rotate_once
    # The second guard is checked immediately before the Discord API PATCH,
    # preventing a slow image conversion from crossing the allowed deadline.
    deadline = slot + timedelta(minutes=10)
    result = rotate_once(now=slot, apply_deadline=deadline)
    return {"status": result.get("status", "unknown"),
            "candidate_total": result.get("candidate_total", 0),
            "target_slot": slot.strftime("%Y-%m-%d %H:%M")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = run()
    print("GUILD_BANNER_BACKUP " + json.dumps(data, ensure_ascii=False))
    raise SystemExit(0 if data["status"] in ("changed", "already_rotated", "exhausted_candidates", "outside_schedule") else 1)
