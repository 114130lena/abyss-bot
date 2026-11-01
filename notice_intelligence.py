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
    """Only an unambiguous future date may identify an event."""
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
        explicit = {datetime(int(y), int(m), int(d), tzinfo=KST).date() for y, m, d in full}
        relative = set()
        for m, d in short:
            relative.update(datetime(y, int(m), int(d), tzinfo=KST).date()
                            for y in (posted.year, posted.year + 1))
        future = lambda d: 0 <= (d - posted.date()).days <= 30
        if explicit and relative and not any(d in relative for d in explicit):
            return None
        valid = {d for d in (explicit or relative) if future(d)}
        return next(iter(valid)).isoformat() if len(valid) == 1 else None
    except (ValueError, TypeError):
        return None


def _clock_key(source):
    """Normalize 20:00 and 오후 8시 to the same stable value."""
    values = set()
    for h, m in re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", source):
        values.add(f"{int(h):02d}:{int(m):02d}")
    for period, hour, minute in re.findall(r"(오전|오후)\s*(1[0-2]|[1-9])시\s*(반|[0-5]?\d분)?", source):
        h = int(hour) % 12 + (12 if period == "오후" else 0)
        minute_value = 30 if minute == "반" else int(minute[:-1]) if minute else 0
        values.add(f"{h:02d}:{minute_value:02d}")
    return next(iter(values)) if len(values) == 1 else None


def _event_topic(title, body):
    """Never correlate two broadcasts solely because they share a calendar day."""
    source = str(title or "") + "\n" + str(body or "")
    game = re.findall(r"(?<![가-힣])(?:발헤임|아크\s*레이더스|마인크래프트|롤|리그\s*오브\s*레전드)(?![가-힣])", source, re.I)
    people = participants(title, body)
    topics = sorted(set(x.casefold().replace(" ", "") for x in game))
    if people:
        topics.extend("with:" + x for x in sorted(people))
    return "|".join(topics) or "unspecified"

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
    status = "cancelled" if kind == "🚫 방송 취소 안내" else "break" if kind == "🌙 휴방 안내" else "scheduled"
    clock = _clock_key(source)
    if clock is None and status == "scheduled":
        return None
    topic = _event_topic(title, body)
    with _STATE_LOCK:
        return _record_schedule_state(group, key, kind, status, clock, topic, url)


def _record_schedule_state(group, key, kind, status, clock, topic, url):
    state = _read(SCHEDULE_FILE)
    identifier = str(group) + ":" + key + ":" + topic
    previous = state.get(identifier) or {}
    # An unspecified event cannot safely be associated with an already named event.
    if topic == "unspecified" and any(
            k.startswith(str(group) + ":" + key + ":") and k != identifier
            for k in state):
        return None
    new = {"status": status, "time": clock or "", "source": url, "kind": kind,
           "topic": topic}
    if previous and previous.get("time") != new["time"] and kind not in (
            "🔄 일정 변경 안내", "🚫 방송 취소 안내", "🌙 휴방 안내"):
        return None
    if previous and (previous.get("status"), previous.get("time")) != (status, new["time"]):
        logging.warning("일정 변경 감지: %s 이전=%s 현재=%s 원문=%s",
                        identifier, previous.get("time") or previous.get("status"),
                        new["time"] or status, url)
    state[identifier] = new
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
    # One notification on sustained failure; one when the source recovers.
    previous_failures = int(previous.get("consecutive_failures", 0))
    announce = ok and previous_failures >= 3
    if announce:
        try:
            from resilience import ADMIN_WEBHOOK, send_webhook
            if ADMIN_WEBHOOK.startswith("https://"):
                label = "복구됨" if ok else "연속 수집 실패"
                send_webhook(ADMIN_WEBHOOK, {
                    "username": "어비스봇 상태 알림",
                    "embeds": [{"title": name + " · " + label,
                                "description": ("수집이 다시 정상화되었습니다." if ok else
                                                "3회 연속 수집 실패. RSS 및 네트워크 상태를 확인하세요."),
                                "color": 0x2ECC71 if ok else 0xE67E22}],
                    "allowed_mentions": {"parse": []},
                }, attempts=2)
        except Exception:
            logging.exception("운영자 수집 상태 알림 전송 실패")

if __name__ == "__main__":
    # Run on the bot host: python notice_intelligence.py
    # No webhook tokens or post contents are printed.
    state = _read(HEALTH_FILE)
    for name in ("네이버 카페", "히요리 X", "후야 X"):
        item = state.get(name) or {}
        print(f"{name}: last_success={item.get('last_success', '없음')} "
              f"last_check={item.get('last_check', '없음')} "
              f"failures={item.get('consecutive_failures', 0)}")
