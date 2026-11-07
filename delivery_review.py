"""Private review queue for Discord deliveries with unknown outcome.

A dropped response is not proof that Discord rejected a notification. Never
blindly resend role pings; record a non-sensitive fingerprint for inspection.
"""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
JOURNAL = ROOT / "discord_delivery_uncertain.jsonl"
_LOCK = threading.Lock()


def record(payload, reason):
    """Append without saving webhook URLs, tokens or private message bodies."""
    import re
    pieces = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(pieces.encode("utf-8")).hexdigest()[:20]
    label = "x" if "𝕏 X" in pieces else "youtube" if "YOUTUBE" in pieces else "chzzk" if "CHZZK" in pieces else "cafe" if "CAFE" in pieces else "other"
    item = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fingerprint": digest, "platform": label,
            "reason": str(reason)[:50], "status": "needs_manual_review"}
    with _LOCK:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(JOURNAL, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    logging.warning("DISCORD_DELIVERY_REVIEW_PENDING platform=%s fingerprint=%s reason=%s",
                    label, digest, item["reason"])
    return digest


def count_pending():
    try:
        with JOURNAL.open("r", encoding="utf-8") as stream:
            return sum(1 for line in stream if line.strip())
    except OSError:
        return 0
