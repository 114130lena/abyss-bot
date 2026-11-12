"""Privacy-safe Discord HTTP delivery timing; no post text or webhook URLs."""
import json
import os
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

SOURCE_STATE = ROOT / "discord_source_delivery_latency.json"
_SOURCE_LOCK = threading.Lock()
SOURCE_LIMIT = 100


def parse_source_time(raw):
    """Accept ISO8601 or RFC2822 only; never guess local timezone."""
    # Naver Cafe may supply epoch milliseconds as an integer or digit text.
    # Accept only explicit seconds/milliseconds epochs, never guess local time.
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) or (
            isinstance(raw, str) and raw.strip().isdigit()):
        try:
            value = float(raw)
            if not value.is_integer():
                return None
            epoch = value / 1000 if value >= 10**12 else value
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            if not 2000 <= dt.year <= 2100:
                return None
            return dt
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw.strip())
        except (TypeError, ValueError, IndexError):
            return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def record_source_lag(platform, source_time, now=None):
    """Record source publication to successful Discord HTTP response, no post IDs."""
    if platform not in ("x-original", "x-repost", "cafe", "youtube",
                        "chzzk-live", "chzzk-replay"):
        return None
    published = parse_source_time(source_time)
    if published is None:
        return None
    current = now or datetime.now(timezone.utc)
    lag = (current - published).total_seconds()
    if lag < -300 or lag > 7 * 86400:
        return None
    lag = round(max(0, lag), 2)
    with _SOURCE_LOCK:
        try:
            state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError, TypeError):
            state = {}
        samples = state.get(platform, [])
        if not isinstance(samples, list):
            samples = []
        samples = [s for s in samples if isinstance(s, (int, float))
                   and not isinstance(s, bool) and 0 <= s <= 7 * 86400]
        state[platform] = (samples + [lag])[-SOURCE_LIMIT:]
        SOURCE_STATE.parent.mkdir(parents=True, exist_ok=True)
        temp = SOURCE_STATE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        temp.replace(SOURCE_STATE)
    return lag


def source_lag_summary():
    """Observational p50/p95; absent events are unknown, not zero latency."""
    try:
        state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(state, dict):
        return {}
    result = {}
    for platform, values in state.items():
        if not isinstance(values, list):
            continue
        samples = sorted(float(x) for x in values if isinstance(x, (int, float))
                         and not isinstance(x, bool) and 0 <= x <= 7 * 86400)
        if samples:
            result[platform] = {
                "samples": len(samples),
                "p50_seconds": round(samples[(len(samples)-1)//2], 2),
                "p95_seconds": round(samples[max(0, (95*len(samples)+99)//100-1)], 2),
                "max_seconds": round(samples[-1], 2),
            }
    return result
