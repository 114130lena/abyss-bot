"""Persistent, deduplicated server-banner bookmarks (no public write endpoint)."""
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LOCK = threading.RLock()
KST = ZoneInfo("Asia/Seoul")
ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
FILE = ROOT / "creator_media" / "banner_bookmarks.json"


def list_bookmarks():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else []


def keep_current(state, actor_id, now=None):
    """Only call after Discord authenticated interaction + admin authorization."""
    sha = state.get("last_sha256")
    group = state.get("group") or "hanayo"
    source = state.get("post_url") or ""
    artist = state.get("artist") or ""
    if (not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha)
            or group not in ("hanayo", "sakihane")
            or not isinstance(actor_id, str) or not actor_id.isdigit()
            or not isinstance(source, str) or not source.startswith("https://x.com/")
            or not isinstance(artist, str) or not re.fullmatch(r"@[A-Za-z0-9_]{1,15}", artist)):
        return {"status": "unverified_source"}
    with LOCK:
        items = list_bookmarks()
        if any(item.get("sha256") == sha for item in items if isinstance(item, dict)):
            return {"status": "already_saved"}
        item = {"sha256": sha, "group": group, "artist": artist,
                "post_url": source, "saved_at": (now or datetime.now(KST)).astimezone(KST).isoformat(),
                "saved_by": actor_id}
        items.append(item)
        FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(FILE)
        return {"status": "saved", "item": item}


def public_catalog():
    """Safe metadata for a read-only gallery; no Discord user identifiers."""
    return [{k: item[k] for k in ("sha256", "group", "artist", "post_url", "saved_at")
             if k in item} for item in list_bookmarks() if isinstance(item, dict)]
