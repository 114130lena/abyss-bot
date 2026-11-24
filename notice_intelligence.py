"""Conservative schedule correlation and source health state, with no extra Discord messages."""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
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
    """Resolve an explicit event date relative to the *post*, not current time.

    Supports yyyy-mm-dd, mm/dd, mm월dd일, today/tomorrow.
    Refuse conflicting dates and references to a past/cancelled schedule.
    """
    source = str(title or "") + "\n" + str(body or "")
    from discord_v2 import _parse_korean_datetime
    posted = _parse_korean_datetime(published)
    if posted is None:
        return None
    posted_day = posted.date()
    full = set(re.findall(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)", source))
    korean = set(re.findall(r"(?<!\d)(1[0-2]|[1-9])월\s*(3[01]|[12]\d|[1-9])일", source))
    # Full dates must not also be read as short month/day dates.
    without_full = re.sub(r"(?<!\d)20\d{2}[./-]\d{1,2}[./-]\d{1,2}(?!\d)", " ", source)
    numeric = set(re.findall(r"(?<![\d/])(1[0-2]|[1-9])[./-](3[01]|[12]\d|[1-9])(?!\d)", without_full))
    short = korean | numeric
    dates = set()
    try:
        for y, m, d in full:
            dates.add(datetime(int(y), int(m), int(d)).date())
        for m, d in short:
            candidates = [datetime(year, int(m), int(d)).date()
                          for year in (posted.year, posted.year + 1)]
            dates.update(day for day in candidates if 0 <= (day - posted_day).days <= 30)
    except (ValueError, TypeError):
        return None
    if not dates:
        # Only infer relative dates when the reference is explicit and there
        # are no contradictory absolute calendar dates in the text.
        if re.search(r"모레", source):
            dates.add(posted_day + timedelta(days=2))
        elif re.search(r"내일", source):
            dates.add(posted_day + timedelta(days=1))
        elif re.search(r"오늘|금일|이따(?:가|가요)?", source):
            dates.add(posted_day)
        else:
            # A named weekday is safe only when it matches the publication day.
            weekdays = ("월", "화", "수", "목", "금", "토", "일")
            mentioned = set(re.findall(r"(?<![가-힣])([월화수목금토일])요일", source))
            if mentioned == {weekdays[posted_day.weekday()]}:
                dates.add(posted_day)
    valid = {day for day in dates if 0 <= (day - posted_day).days <= 30}
    return next(iter(valid)).isoformat() if len(valid) == 1 else None


def _clock_result(source):
    """Pick an explicit KST clock. A bare '9시' has unknown AM/PM."""
    source = str(source or "")
    # Do not let old/cancelled values in change announcements override new time.
    lines = [line.strip() for line in source.splitlines() if line.strip()
             and not any(term in line for term in
                         ("변경 전", "기존 시간", "원래 시간", "이전 시간", "예정이었"))]
    clocks = set()
    uncertain = set()
    for line in lines:
        for h, minute in re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", line):
            clocks.add(f"{int(h):02d}:{int(minute):02d}")
        for period, hour, minute in re.findall(
                r"(오전|오후|아침|저녁|밤)\s*(1[0-2]|[1-9])시\s*(반|[0-5]?\d분)?", line):
            h = int(hour) % 12 + (12 if period in ("오후", "저녁", "밤") else 0)
            m = 30 if minute == "반" else int(minute[:-1]) if minute else 0
            clocks.add(f"{h:02d}:{m:02d}")
        # Explicit 24-hour notation such as '21시' is unambiguous.
        for hour, minute in re.findall(
                r"(?<!\d)([01]?\d|2[0-3])시\s*(반|[0-5]?\d분)?", line):
            if 13 <= int(hour) <= 23:
                m = 30 if minute == "반" else int(minute[:-1]) if minute else 0
                clocks.add(f"{int(hour):02d}:{m:02d}")
        # Bare Korean clock notation should never silently become 09:00.
        for hour, minute in re.findall(
                r"(?<!\d)(1[0-2]|[1-9])시\s*(반|[0-5]?\d분)?", line):
            m = 30 if minute == "반" else int(minute[:-1]) if minute else 0
            uncertain.add(f"{int(hour)}시" + (f" {m:02d}분" if m else ""))
    if len(clocks) == 1:
        exact = next(iter(clocks))
        # "오후 8시 혹은 9시" is a choice, not a confirmed 20:00.
        numeric_hour = int(exact[:2]) % 12 or 12
        for bare in uncertain:
            matched = re.match(r"(1[0-2]|[1-9])시", bare)
            if matched and int(matched.group(1)) != numeric_hour:
                return None, ""
        return exact, ""
    if len(clocks) > 1:
        return None, ""
    if len(uncertain) == 1:
        return "", next(iter(uncertain)) + " (오전/오후 미확인)"
    return None, ""


def _clock_key(source):
    """Legacy helper: exact normalized clock or None (never a tuple)."""
    clock, _ = _clock_result(source)
    return clock or None


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
    # Separate explicitly dated secondary breaks from the primary notice.
    primary_key = _event_key(title, "", published)
    if primary_key and re.search(r"방송\s*(?:일정|공지|예정)", str(title or "")):
        primary_lines, secondary = [], []
        for line in str(body or "").splitlines():
            other = _event_key("", line, published)
            if other and other != primary_key and re.match(r"\s*(?:\d{1,2}월\s*\d{1,2}일|내일|모레)\s*(?:은|는|에)?\s*휴방", line):
                secondary.append((other, line))
            else:
                primary_lines.append(line)
        if secondary:
            body = "\n".join(primary_lines)
            for other, line in secondary:
                with _STATE_LOCK:
                    _record_schedule_state(group, other, "🌙 휴방 안내", "break", "", "unspecified", url, published=published)
    kind = _notice_type(title, body)
    # "휴방이 아닌 9시 방송" is a broadcast announcement, not a break.
    # Classification can be conservative but must not suppress this pattern.
    source = str(title or "") + "\n" + str(body or "")
    if re.search(r"휴방(?:은|이|은요)?\s*(?:아닌|아니고|아닙니다|아니에요)", source) and re.search(r"방송|라이브", source):
        kind = "📺 방송 예고"
    if not kind and re.search(r"방송|생방|라이브|합방", source):
        if not re.search(r"방송\s*(?:종료|없음)|방송\s*안\s*(?:해요|합니다)|지난\s*방송", source):
            kind = "📺 방송 예고"
    if not kind:
        return None
    key = _event_key(title, body, published)
    if key is None:
        return None
    status = "cancelled" if kind == "🚫 방송 취소 안내" else "break" if kind == "🌙 휴방 안내" else "scheduled"
    clock, time_note = _clock_result(source)
    # For an explicit same-day announcement posted after noon, "9시" cannot
    # mean 09:00 if that hour already passed. Do not infer otherwise.
    from discord_v2 import _parse_korean_datetime
    posted = _parse_korean_datetime(published)
    if (status == "scheduled" and clock == "" and time_note
            and posted and key == posted.date().isoformat()
            and (re.search(r"오늘|금일|이따", source)
                 or re.search(r"(?<![가-힣])" + ("월화수목금토일"[posted.weekday()]) + r"요일", source))):
        h = re.match(r"(1[0-2]|[1-9])시", time_note)
        if h and int(h.group(1)) < 12 and 12 <= posted.hour < int(h.group(1)) + 12:
            minute_match = re.search(r"\s(\d{2})분", time_note)
            minute = int(minute_match.group(1)) if minute_match else 0
            clock = f"{int(h.group(1)) + 12:02d}:{minute:02d}"
            time_note = ""
    # A bare 9:00 in a same-day evening notice is not a confirmed morning time.
    # Infer PM only if the morning clock is already past and no AM/PM qualifier exists.
    if (status == "scheduled" and posted and key == posted.date().isoformat()
            and clock and int(clock[:2]) in range(1, 12)
            and 12 <= posted.hour < int(clock[:2]) + 12
            and not re.search(r"오전|아침|새벽|AM|a\\.m\\.", source, re.I)
            and re.search(r"(?<!\\d)" + re.escape(str(int(clock[:2]))) + r":" + re.escape(clock[3:]) + r"(?!\\d)", source)):
        clock = f"{int(clock[:2]) + 12:02d}:{clock[3:]}"
    if clock is None and status == "scheduled":
        # Only explicit dated broadcast notices with no clock at all may be time-TBD.
        if not (primary_key and re.search(r"방송\s*(?:일정|공지|예정)", str(title or ""))
                and not re.search(r"\d{1,2}\s*시|\d{1,2}:\d{2}", source)):
            return None
        clock, time_note = "", "시간 미정"
    topic = _event_topic(title, body)
    with _STATE_LOCK:
        return _record_schedule_state(group, key, kind, status, clock, topic, url, time_note=time_note, published=posted.isoformat() if posted else "")


def _record_schedule_state(group, key, kind, status, clock, topic, url, time_note="", published=""):
    state = _read(SCHEDULE_FILE)
    identifier = str(group) + ":" + key + ":" + topic
    previous = state.get(identifier) or {}
    from discord_v2 import _parse_korean_datetime
    incoming = _parse_korean_datetime(published)
    previous_post = _parse_korean_datetime(previous.get("published_at"))
    if incoming and previous_post and incoming < previous_post:
        return None
    # An unspecified event cannot safely be associated with an already named event.
    if topic == "unspecified" and any(
            k.startswith(str(group) + ":" + key + ":") and k != identifier
            for k in state):
        return None
    new = {"status": status, "time": clock or "", "time_note": time_note,
           "source": url, "kind": kind,
           "topic": topic, "published_at": incoming.isoformat() if incoming else ""}
    if previous and not (incoming and (not previous_post or incoming > previous_post)) and previous.get("time") != new["time"] and kind not in (
            "🔄 일정 변경 안내", "🚫 방송 취소 안내", "🌙 휴방 안내"):
        return None
    if previous and (previous.get("status"), previous.get("time")) != (status, new["time"]):
        logging.warning("일정 변경 감지: %s 이전=%s 현재=%s 원문=%s",
                        identifier, previous.get("time") or previous.get("status"),
                        new["time"] or status, url)
    if previous == new:
        return previous
    state[identifier] = new
    if len(state) > 300:
        state = dict(sorted(state.items())[-300:])
    _save(SCHEDULE_FILE, state)
    return new


def reconcile_recent_cafe_schedules(now=None):
    """Read recent public Cafe notices only; never send Discord alerts.

    Existing entries can be repaired even when the notifier already marked an
    article as seen. Limit detail requests to avoid affecting live polling.
    """
    from cafe_notifier import BOARDS, article_detail, get_articles
    from discord_v2 import _parse_korean_datetime
    now = (now or datetime.now(KST)).astimezone(KST)
    inserted = 0
    for board in BOARDS:
        try:
            articles = get_articles(board["menu_id"])
        except Exception as exc:
            logging.warning("COMMUNITY_CAFE_SCHEDULE_LIST_FAILURE board=%s error=%s",
                            board["menu_id"], type(exc).__name__)
            continue
        examined = 0
        for article in sorted(articles[:12], key=lambda a: str(a.get("date") or "")):
            published = _parse_korean_datetime(article.get("date"))
            if (published is None or published > now + timedelta(minutes=5)
                    or now - published > timedelta(days=7)):
                continue
            # Public details are fetched for no more than six recent posts.
            if examined >= 6:
                break
            examined += 1
            try:
                summary, _ = article_detail(article)
                result = record_schedule(board["group"], article.get("title", ""),
                                         summary, article.get("date"), article.get("url", ""))
                if result is not None:
                    inserted += 1
            except Exception as exc:
                logging.warning("COMMUNITY_CAFE_SCHEDULE_PARSE_FAILURE board=%s error=%s",
                                board["menu_id"], type(exc).__name__)
    logging.info("COMMUNITY_CAFE_SCHEDULE_RECONCILED records=%d", inserted)
    return inserted


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
