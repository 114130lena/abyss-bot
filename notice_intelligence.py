"""Conservative schedule correlation and source health state, with no extra Discord messages."""
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
SCHEDULE_FILE = ROOT / "notice_schedule_state.json"
HEALTH_FILE = ROOT / "source_health_state.json"
KNOWN = ("메리", "히요리", "후야", "나나", "시부키", "시로", "리코", "린")
KST = ZoneInfo("Asia/Seoul")


def participants(title, body):
    """Only named collaborators adjacent to a collab statement are included."""
    source = str(title or "") + "\n" + str(body or "")
    result = []
    for line in source.splitlines():
        if "합방" not in line:
            continue
        for name in KNOWN:
            if name in result:
                continue
            if re.search(r"(?<![가-힣])" + re.escape(name) + r"(?:랑|하고|와|과|님과|님이|님도|,|·|\s|$)", line):
                result.append(name)
    return result[:4]


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _event_key(title, body, published):
    """Require an explicit full event date; never infer a date from 'today'."""
    source = str(title or "") + "\n" + str(body or "")
    days = set(re.findall(r"(?<!\d)(20\d{2})[.\/-](\d{1,2})[.\/-](\d{1,2})(?!\d)", source))
    if len(days) != 1:
        return None
    year, month, day = next(iter(days))
    try:
        event = datetime(int(year), int(month), int(day), tzinfo=KST).date()
        from discord_v2 import _parse_korean_datetime
        posted = _parse_korean_datetime(published)
        if posted is None or not (0 <= (event - posted.date()).days <= 30):
            return None
        return event.isoformat()
    except (ValueError, TypeError):
        return None


def record_schedule(group, title, body, published, url):
    """Correlate only same creator + explicitly dated single-clock events.

    Changes are logged for operators; do not mutate sent messages or send extra
    alerts. Original posts always continue through their usual notifiers.
    """
    from discord_v2 import _notice_type
    kind = _notice_type(title, body)
    if not kind:
        return None
    key = _event_key(title, body, published)
    if key is None:
        return None
    source = str(title or "") + "\n" + str(body or "")
    clocks = set(re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", source))
    clocks.update(re.findall(r"(?:오전|오후)\s*(?:1[0-2]|[1-9])시(?:\s*(?:반|[0-5]?\d분))?", source))
    # Explicitly conflicting times are not suitable for automatic comparisons.
    if len(clocks) != 1 and kind not in ("🚫 방송 취소 안내", "🌙 휴방 안내"):
        return None
    status = "cancelled" if kind == "🚫 방송 취소 안내" else "break" if kind == "🌙 휴방 안내" else "scheduled"
    state = _read(SCHEDULE_FILE)
    identifier = str(group) + ":" + key
    previous = state.get(identifier) or {}
    new = {"status": status, "time": next(iter(clocks)) if len(clocks) == 1 else "",
           "source": url, "kind": kind}
    if previous and previous != new and (previous.get("status"), previous.get("time")) != (status, new["time"]):
        logging.warning("일정 변경 감지: %s 이전=%s 현재=%s 원문=%s",
                        identifier, previous.get("time") or previous.get("status"),
                        new["time"] or status, url)
    state[identifier] = new
    # Prevent indefinite growth without guessing which upcoming plans remain valid.
    if len(state) > 300:
        state = dict(sorted(state.items())[-300:])
    _save(SCHEDULE_FILE, state)
    return new


def report_source(name, ok, reason=""):
    """Operational diagnostics only; no user-facing Discord notification spam."""
    now = datetime.now(KST).isoformat(timespec="seconds")
    state = _read(HEALTH_FILE)
    previous = state.get(name, {})
    entry = dict(previous)
    entry["last_check"] = now
    if ok:
        entry["last_success"] = now
        entry["consecutive_failures"] = 0
    else:
        entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
        if entry["consecutive_failures"] in (3, 10, 30):
            logging.warning("수집 상태 경고: %s 실패 %s회 (%s)",
                            name, entry["consecutive_failures"], str(reason)[:120])
    state[name] = entry
    _save(HEALTH_FILE, state)
