"""Privacy-safe Discord HTTP delivery timing; no post text or webhook URLs."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
STATE = ROOT / "discord_delivery_latency.json"
_LOCK = threading.Lock()
MAX_SAMPLES = 100


def record_transport(seconds, attachments=0):
    """Record successful transport durations only; never mark ambiguous sends OK."""
    elapsed = round(max(0.0, min(float(seconds), 3600.0)), 3)
    count = max(0, min(int(attachments), 100))
    with _LOCK:
        try:
            data = json.loads(STATE.read_text(encoding="utf-8"))
            samples = data.get("recent_seconds", []) if isinstance(data, dict) else []
        except (OSError, ValueError, TypeError):
            samples = []
        if not isinstance(samples, list):
            samples = []
        samples = [x for x in samples if isinstance(x, (int, float))
                   and not isinstance(x, bool) and 0 <= x <= 3600][-MAX_SAMPLES + 1:]
        samples.append(elapsed)
        result = {
            "recent_seconds": samples,
            "recent_success_count": len(samples),
            "average_seconds": round(sum(samples) / len(samples), 2),
            "max_seconds": round(max(samples), 2),
            "last_seconds": elapsed,
            "last_attachment_count": count,
            "slow_count": sum(x > 15 for x in samples),
        }
        STATE.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as out:
            json.dump(result, out)
        temp.replace(STATE)
        return result


def summary():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"status": "unverified"}
    if not isinstance(data, dict) or not isinstance(data.get("recent_seconds"), list):
        return {"status": "unverified"}
    # Whitelist aggregate fields. Never reflect untrusted raw state values.
    return {
        "status": "ok", "recent_success_count": len(data["recent_seconds"]),
        "average_seconds": data.get("average_seconds"),
        "max_seconds": data.get("max_seconds"),
        "last_seconds": data.get("last_seconds"),
        "slow_count": data.get("slow_count"),
    }
