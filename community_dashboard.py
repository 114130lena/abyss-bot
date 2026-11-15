"""One persistent Discord Components V2 community hub.

Calendar: only validated cafe schedule events. Milestones: explicitly
configured verified dates. Broadcast analytics: sessions observed after this
feature was enabled, not all-time historical totals.

Never edit the banner card or clip card; publish one separate message.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from creator_groups import chzzk_group
from resilience import load_json, save_json
from discord_message_sync import update_original

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
STATE = ROOT / "community_dashboard_state.json"
SESSIONS = ROOT / "community_live_sessions.json"
MILESTONES = ROOT / "community_milestones.json"
SCHEDULE = ROOT / "notice_schedule_state.json"
LOCK = threading.RLock()
MARKER = "ABYSS · COMMUNITY HUB"
NAMES = {"hanayo": "하나요 히요리", "sakihane": "사키하네 후야"}


def _read(path):
    try:
        value = load_json(path, {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _when(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(KST) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def record_transition(channel_id, previous, current, now=None):
    """Record only observed live transitions; do not infer missing history."""
    if not isinstance(previous, dict) or not previous:
        return
    now = (now or datetime.now(KST)).astimezone(KST)
    group = chzzk_group(channel_id)
    previous_open = previous.get("status") == "OPEN"
    current_open = current.get("status") == "OPEN"
    prev_id = str(previous.get("live_id") or previous.get("open_date") or "")
    curr_id = str(current.get("live_id") or current.get("open_date") or "")
    if previous_open == current_open and (not current_open or prev_id == curr_id):
        return
    with LOCK:
        state = _read(SESSIONS)
        sessions = state.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        active = state.get("active")
        if not isinstance(active, dict):
            active = {}
        key = str(channel_id)
        # Close an observed session when it ends or a new session begins.
        existing = active.pop(key, None) if previous_open else None
        if isinstance(existing, dict) and existing.get("id"):
            started = _when(existing.get("started"))
            if started and now >= started:
                elapsed = min(int((now - started).total_seconds()), 86400)
                if elapsed > 0 and not any(row.get("id") == existing["id"] and row.get("channel") == key
                                            for row in sessions if isinstance(row, dict)):
                    sessions.append({"id": existing["id"], "channel": key,
                                     "group": group, "started": started.isoformat(),
                                     "ended": now.isoformat(), "seconds": elapsed})
        if current_open:
            # Use the first observed timestamp, not an unverified start time.
            # Restart can lose active state; do not invent prior duration.
            active[key] = {"id": curr_id or now.isoformat(), "started": now.isoformat()}
        cutoff = now - timedelta(days=400)
        state["sessions"] = [row for row in sessions if isinstance(row, dict)
                             and (_when(row.get("ended")) or cutoff) >= cutoff][-2500:]
        state["active"] = active
        save_json(SESSIONS, state)


def _events(now):
    rows = []
    for key, data in _read(SCHEDULE).items():
        if not isinstance(data, dict):
            continue
        pieces = str(key).split(":", 2)
        if len(pieces) != 3 or pieces[0] not in NAMES:
            continue
        day = pieces[1]
        clock = str(data.get("time") or "")
        try:
            dt = datetime.fromisoformat(day + "T" + (clock if len(clock) == 5 else "00:00")).replace(tzinfo=KST)
        except ValueError:
            continue
        if not (now - timedelta(days=1) <= dt <= now + timedelta(days=30)):
            continue
        status = data.get("status")
        if status not in ("scheduled", "break", "cancelled"):
            continue
        url = str(data.get("source") or "")
        if not url.startswith("https://"):
            url = ""
        rows.append((dt, pieces[0], status, url, pieces[2], bool(clock)))
    return sorted(rows, key=lambda x: x[0])[:12]


def _milestones(now):
    result = []
    entries = _read(MILESTONES).get("events")
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict) or item.get("group") not in NAMES:
            continue
        title = str(item.get("title") or "").strip()
        raw = str(item.get("date") or "")
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not title:
            continue
        # Compute a yearly recurrence without inventing the original date.
        for year in (now.year, now.year + 1, now.year + 2):
            try:
                next_day = day.replace(year=year)
            except ValueError:
                next_day = datetime(year, 2, 28).date()
            if next_day >= now.date():
                break
        result.append(((next_day - now.date()).days, item["group"], title[:90],
                       day.year))
    return sorted(result)[:8]


def _stats(now):
    rows = _read(SESSIONS).get("sessions")
    rows = rows if isinstance(rows, list) else []
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = {key: {"count": 0, "seconds": 0} for key in NAMES}
    for row in rows:
        if not isinstance(row, dict) or row.get("group") not in result:
            continue
        started = _when(row.get("started"))
        if started is None or not (start_month <= started <= now):
            continue
        result[row["group"]]["count"] += 1
        result[row["group"]]["seconds"] += max(0, min(int(row.get("seconds") or 0), 86400))
    return result


def _clean(value):
    return str(value).replace("\n", " ").replace("]", " ").replace("[", " ")[:90]


def render(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    calendar = _events(now)
    milestones = _milestones(now)
    stats = _stats(now)
    parts = [{"type": 10, "content": (
        "**" + MARKER + "**\n## 방송 일정 · 기념일 · 활동 통계\n"
        "-# 히요리 / 후야 · 한눈에 보는 통합 정보\n"
        "-# 마지막 갱신 <t:" + str(int(now.timestamp())) + ":f> · 1시간 간격 갱신")}]
    for group, name in NAMES.items():
        events = [e for e in calendar if e[1] == group][:6]
        lines = []
        for dt, _, status, url, topic, precise in events:
            status_label = {"scheduled": "📺 방송", "break": "🌙 휴방", "cancelled": "🚫 취소"}[status]
            day = dt.strftime("%m/%d")
            hour = dt.strftime("%H:%M") if precise else "시간 미정"
            label = status_label + " · " + day + " " + hour
            if topic != "unspecified":
                label += " · " + _clean(topic).replace("|", ", ")
            if url:
                label += " · [공지](" + url + ")"
            lines.append(label)
        parts.append({"type": 10, "content": (
            "### " + name + " · 방송 일정\n" +
            ("\n".join(lines) if lines else "-# 확인된 향후 일정이 없습니다. (공지 기반)"))})
    anniversary_lines = []
    for days, group, title, year in milestones:
        anniversary_lines.append("**" + NAMES[group] + "** · " + _clean(title) +
                                 " · " + ("오늘" if days == 0 else "D-" + str(days)))
    parts.append({"type": 14, "divider": True, "spacing": 1})
    parts.append({"type": 10, "content": (
        "### 🎉 기념일 · 주요 기록\n" +
        ("\n".join(anniversary_lines) if anniversary_lines else
         "-# 검증된 기념일을 등록하면 이곳에 표시됩니다.") +
        "\n-# 기념일 데이터는 확인된 날짜만 수동 등록")})
    parts.append({"type": 14, "divider": True, "spacing": 1})
    month_label = now.strftime("%Y년 %m월")
    statlines = []
    for group, name in NAMES.items():
        data = stats[group]
        mins = data["seconds"] // 60
        statlines.append("**" + name + "** · " + str(data["count"]) +
                         "회 · " + str(mins // 60) + "시간 " + str(mins % 60) + "분")
    parts.append({"type": 10, "content": (
        "### 📊 방송 활동 통계 · " + month_label + "\n" +
        "\n".join(statlines) +
        "\n-# 기능 활성화 후 시작·종료가 모두 관측된 방송만 집계합니다."
        "\n-# 수집 중단·재시작 구간의 누락 가능성이 있어 전체 방송 기록은 아닙니다.")})
    parts.append({"type": 14, "divider": True, "spacing": 1})
    parts.append({"type": 10, "content": (
        "-# 일정 출처: 카페 공지의 명확한 날짜·시간"
        "\n-# 기념일: 수동 검증 자료 · 방송: 치지직 상태 변화 기록"
        "\n-# 클립 기능은 비활성 상태로 유지")})
    return {"flags": 32768, "components": [
        {"type": 17, "accent_color": 0x8391BB, "components": parts}],
        "allowed_mentions": {"parse": []}}


def sync_once(now=None, get=None, post=None, patch=None):
    """Edit only the already-existing clip card, never create a new post."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    channel = os.getenv("COMMUNITY_DASHBOARD_CHANNEL_ID", "").strip()
    message_id = os.getenv("COMMUNITY_DASHBOARD_MESSAGE_ID", "").strip()
    clip_channel = os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip()
    clip_message = os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip()
    # Hard guard: the hub must target the exact paused clip message.
    if (not token or not channel.isdigit() or not message_id.isdigit()
            or channel != clip_channel or message_id != clip_message
            or os.getenv("CHZZK_CLIP_ENABLED", "0") != "0"):
        return {"status": "not_configured_or_clip_active"}
    with LOCK:
        status = update_original(channel, message_id, render(now), token,
                                 get=get or requests.get,
                                 patch=patch or requests.patch)
        if status != "updated":
            logging.warning("COMMUNITY_DASHBOARD_CLIP_MESSAGE_UNEDITABLE id=%s reason=%s",
                            message_id, status)
            return {"status": "update_failed", "reason": status}
        save_json(STATE, {"message_id": message_id,
                          "updated_at": (now or datetime.now(KST)).isoformat()})
        return {"status": "updated", "message_id": message_id}


def run_scheduler():
    """Hourly refresh, separate from the disabled clip system."""
    while True:
        try:
            result = sync_once()
            logging.info("COMMUNITY_DASHBOARD %s", result)
        except (OSError, ValueError, TypeError, requests.RequestException):
            logging.exception("COMMUNITY_DASHBOARD_FAILED")
        time.sleep(3600)
