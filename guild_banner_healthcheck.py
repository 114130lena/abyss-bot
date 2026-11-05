"""Read-only health check for the 08:00/18:00 KST banner rotation."""
import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from guild_server_banner import read_state

KST=ZoneInfo("Asia/Seoul")

def expected_slot(now):
    if now.hour < 8 or (now.hour == 8 and now.minute < 30):
        return None
    if now.hour < 18 or (now.hour == 18 and now.minute < 30):
        return now.strftime("%Y-%m-%d") + " 08:00"
    return now.strftime("%Y-%m-%d") + " 18:00"

def check(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    target=expected_slot(now)
    state=read_state()
    if not target:
        return {"status":"outside_window","current_slot":state.get("last_slot")}
    good=state.get("last_slot")==target and not state.get("pending_banner")
    return {"status":"ok" if good else "missing_or_unconfirmed",
            "expected_slot":target,"current_slot":state.get("last_slot"),
            "info_card_pending":bool(state.get("info_card_pending")),
            "pending_transaction":bool(state.get("pending_banner"))}

if __name__=="__main__":
    result=check()
    print("BANNER_HEALTH "+json.dumps(result,ensure_ascii=False))
    if result["status"] not in ("ok","outside_window"):
        raise SystemExit(1)
