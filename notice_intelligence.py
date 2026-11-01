"""Conservative schedule correlation and source health state, with no extra Discord messages."""
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
SCHEDULE_FILE = ROOT / "notice_schedule_state.json"
HEALTH_FILE = ROOT / "source_health_state.json"
ALIASES = {
    "메리": ("메리",), "히요리": ("히요리", "하나요"), "후야": ("후야", "후유", "HUYA"),
    "나나": ("나나",), "시부키": ("시부키", "부키"), "시로": ("시로",),
    "리코": ("리코",), "린": ("린",),
}
COLLAB = re.compile(r"합방|콜라보|컬래버|함께\s*방송|같이\s*방송|[×Xx]\s*합방", re.I)
KST = ZoneInfo("Asia/Seoul")
_STATE_LOCK = threading.RLock()


def participants(title, body, creator=""):
    """Recognize explicit collaborators, not unrelated names in chatter."""
    source = str(title or "") + "\n" + str(body or "")
    result = []
    for line in source.splitlines():
        if not COLLAB.search(line):
            continue
        for canonical, names in ALIASES.items():
            if canonical in result or (creator and canonical == creator):
                continue
            for alias in names:
                pattern = (r"(?<![가-힣A-Za-z])" + re.escape(alias) +
                           r"(?:님)?(?=랑|하고|와|과|님|[\s,·×Xx]|$)")
                if re.search(pattern, line, re.I):
                    result.append(canonical)
                    break
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
    """Match a single explicit event date to the post date, never 'today' alone."""
    source = str(title or "") + "\n" + str(body or "")
    from discord_v2 import _parse_korean_datetime
    posted = _parse_korean_datetime(published)
    if posted is None:
        return None
    full = set(re.findall(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)", source))
    short = set(re.findall(r"(?<!\d)(1[0-2]|[1-9])월\s*(3[01]|[12]\d|[1-9])일", source))
    if len(full) > 1 or len(short) > 1:
        return None
    try:
        candidates = []
        for year, month, day in full:
            candidates.append(datetime(int(year), int(month), int(day), tzinfo=KST).date())
        if short:
            month, day = next(iter(short))
            for year in (posted.year, posted.year + 1):
                candidates.append(datetime(year, int(month), int(day), tzinfo=KST).date())
        valid = {date for date in candidates if 0 <= (date - posted.date()).days <= 30}
        # Mixed date expressions must not disagree.
        if full and short and len(valid) != 1:
            return None
        return next(iter(valid)).isoformat() if len(valid) == 1 else None
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
    if len(clocks) != 1 and kind not in ("🚫 방송 취소 안내", "🌙 휴방 안내") :
        return None
    status = "cancelled" if kind == "🚫 방송 취소 안내" else "break" if kind == "🌙 휴방 안내" else "scheduled"
    with _STATE_LOCK:
        return _record_schedule_state(group, key, kind, status, clocks, url)


def _record_schedule_state(group, key, kind, status, clocks, url):
    state = _read(SCHEDULE_FILE)
    identifier = str(group) + ":" + key
    previous = state.get(identifier) or {}
    new = {"status": status, "time": next(iter(clocks)) if len(clocks) == 1 else "",
           "source": url, "kind": kind}
    # Two ordinary announcements on one date may be different streams.
    # Do not treat them as a revision without an explicit change/cancel label.
    if previous and previous.get("time") != new["time"] and kind not in (
            "🔄 일정 변경 안내", "🚫 방송 취소 안내", "🌙 휴방 안내"):
        return None
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
    with _STATE_LOCK:
        _report_source_state(name, ok, reason, now)


def _report_source_state(name, ok, reason, now):
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

if __name__ == "__main__":
    # Run on the bot host: python notice_intelligence.py
    # No webhook tokens or post contents are printed.
    state = _read(HEALTH_FILE)
    for name in ("네이버 카페", "히요리 X", "후야 X"):
        item = state.get(name) or {}
        print(f"{name}: last_success={item.get('last_success', '없음')} "
              f"last_check={item.get('last_check', '없음')} "
              f"failures={item.get('consecutive_failures', 0)}")
