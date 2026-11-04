"""Independent Oracle systemd runner for 08:00 and 18:00 KST guild banners.

No gateway connection, no CHZZK notification service restart, and no Discord
secret in logs. Shared rotation state makes later GitHub Actions a safe backup.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from guild_server_banner import (
    change_guild_banner, convert_banner, read_state, rotation_selection,
    write_state, approved_hanayo_art, collected_hanayo_art,
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
KST = ZoneInfo("Asia/Seoul")
CHANNEL = "1567561255764500551"


def active_slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    minutes = now.hour * 60 + now.minute
    for hour in (8, 18):
        if hour * 60 <= minutes < hour * 60 + 90:
            return now.strftime("%Y-%m-%d") + f" {hour:02d}:00"
    return None


def sync_existing_card(state, token, guild):
    from guild_banner_info_card import upsert_card
    target = state.get("last_sha256")
    pool = approved_hanayo_art() + collected_hanayo_art()
    selected = next(((path,meta) for path,meta in pool
                     if meta.get("sha256") == target), None)
    if selected is None:
        raise RuntimeError("Previously applied banner art is no longer in the cache")
    image = convert_banner(selected[0])
    message, status = upsert_card(token, guild, image, state, state.get("info_message_id"))
    state["info_message_id"] = message
    state["info_channel_id"] = CHANNEL
    state["info_card_pending"] = False
    write_state(state)
    return status


def main():
    slot = active_slot()
    if slot is None:
        print("GUILD_BANNER_TIMER outside_window")
        return
    token = os.environ.get("DISCORD_BOT_TOKEN","").strip()
    guild = os.environ.get("DISCORD_GUILD_ID","").strip()
    if not token or not guild.isdigit():
        raise RuntimeError("Missing restricted guild banner credentials")
    old = read_state()
    if old.get("last_slot") == slot:
        if old.get("info_card_pending"):
            print("GUILD_BANNER_TIMER repairing_card")
            status = sync_existing_card(old, token, guild)
            print("GUILD_BANNER_TIMER repaired_card="+status)
        else:
            print("GUILD_BANNER_TIMER already_rotated "+slot)
        return
    chosen, used, total, remaining = rotation_selection(
        old.get("last_sha256"), old.get("rotation_used", []))
    if not chosen:
        print("GUILD_BANNER_TIMER no_eligible_fanart")
        raise SystemExit(2)
    image_path, meta = chosen
    payload = convert_banner(image_path)
    # Change remote guild once. Only record as complete after Discord accepts.
    change_guild_banner(payload, token, guild)
    new = {
        **old,
        "last_slot": slot,
        "last_sha256": meta["sha256"],
        "artist": meta.get("artist"),
        "post_url": meta.get("post_url"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "rotation_used": used,
        "candidate_total": total,
        "remaining_after": remaining,
        "info_card_pending": True,
    }
    write_state(new)
    print(json.dumps({"status":"banner_changed","slot":slot,
                      "artist":new["artist"],"candidate_total":total},ensure_ascii=False),flush=True)
    try:
        status = sync_existing_card(new,token,guild)
        print("GUILD_BANNER_TIMER card="+status)
    except Exception as exc:
        # Keep the applied banner and preserve an automatic repair marker.
        print("GUILD_BANNER_TIMER card_pending="+type(exc).__name__,flush=True)
        raise


if __name__=="__main__":
    main()
