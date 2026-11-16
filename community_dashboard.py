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
# Checked against official STELLIVE profiles and debut announcement.
# Huya debut additionally cross-checked against independent member archives.
VERIFIED_MILESTONES = (
    {"group": "hanayo", "title": "생일", "month_day": "03-31",
     "source": "https://stellive.me/hiyori/"},
    {"group": "hanayo", "title": "데뷔", "date": "2026-10-18",
     "source": "https://stellive.me/news/14633"},
    {"group": "sakihane", "title": "생일", "month_day": "07-07",
     "source": "https://stellive.me/huya"},
    {"group": "sakihane", "title": "데뷔", "date": "2025-09-20",
     "source": "https://recal.kr/ko/e/vt/stellive/UC0YQnenKBCu5sGb7H61n6HA"},
)



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


def _api_start(value, now):
    """Parse CHZZK live openDate (naive times are KST), never publication time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        dt = dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)
        return dt if now - timedelta(days=2) <= dt <= now else None
    except (ValueError, OverflowError):
        return None


def record_transition(channel_id, previous, current, now=None):
    """Track real API start time and bounded end observation, not fake exact end.

    End time is between last confirmed-live poll and first confirmed-offline
    poll. Suppress sessions after prolonged blind spots; never infer downtime.
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    if not isinstance(current, dict):
        return
    group = chzzk_group(channel_id)
    current_open = current.get("status") == "OPEN"
    if current.get("status") not in ("OPEN", "CLOSE"):
        # Unknown status must not end a confirmed broadcast.
        return
    with LOCK:
        state = _read(SESSIONS)
        sessions = state.get("sessions")
        sessions = sessions if isinstance(sessions, list) else []
        active = state.get("active")
        active = active if isinstance(active, dict) else {}
        key = str(channel_id)
        existing = active.get(key) if isinstance(active.get(key), dict) else None
        current_id = str(current.get("live_id") or current.get("open_date") or "")
        start_at = _api_start(current.get("open_date"), now) if current_open else None
        if current_open:
            if not current_id:
                return
            if existing and str(existing.get("id")) == current_id:
                existing["last_live_seen"] = now.isoformat()
                if start_at and existing.get("source") != "api_open_date":
                    existing["started"] = start_at.isoformat()
                    existing["source"] = "api_open_date"
            else:
                # On restart, use CHZZK's original live opening timestamp.
                # If unavailable, mark first observation as approximate.
                if existing:
                    logging.warning("COMMUNITY_LIVE_SESSION_SWITCH_UNCERTAIN channel=%s", key)
                active[key] = {
                    "id": current_id,
                    "started": (start_at or now).isoformat(),
                    "source": "api_open_date" if start_at else "first_live_poll",
                    "last_live_seen": now.isoformat(),
                }
        else:
            if existing:
                active.pop(key, None)
                started = _when(existing.get("started"))
                last_live = _when(existing.get("last_live_seen"))
                # Don't turn a long outage into fictitious streaming time.
                gap = (now - last_live).total_seconds() if last_live else float("inf")
                if (started and last_live and started <= last_live <= now
                        and 0 <= gap <= 300 and (now - started).total_seconds() <= 172800):
                    lower = max(0, int((last_live - started).total_seconds()))
                    upper = max(lower, int((now - started).total_seconds()))
                    if upper > 0 and not any(
                        row.get("id") == existing["id"] and row.get("channel") == key
                        for row in sessions if isinstance(row, dict)
                    ):
                        sessions.append({
                            "id": existing["id"], "channel": key, "group": group,
                            "started": started.isoformat(),
                            "start_source": existing.get("source", "first_live_poll"),
                            "ended_after": last_live.isoformat(),
                            "ended_before": now.isoformat(),
                            "seconds_min": lower,
                            "seconds_max": upper,
                            "seconds": (lower + upper) // 2,
                            "uncertainty_seconds": (upper - lower + 1) // 2,
                        })
                else:
                    logging.warning("COMMUNITY_LIVE_DURATION_UNVERIFIED channel=%s", key)
        cutoff = now - timedelta(days=400)
        state["sessions"] = [
            row for row in sessions if isinstance(row, dict)
            and (_when(row.get("ended_before") or row.get("ended")) or cutoff) >= cutoff
        ][-2500:]
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
        note = str(data.get("time_note") or "").strip()[:60]
        rows.append((dt, pieces[0], status, url, pieces[2], bool(clock), note))
    return sorted(rows, key=lambda x: x[0])[:12]


def _milestones(now):
    """Combine verified built-ins with optional manually validated records."""
    entries = list(VERIFIED_MILESTONES)
    custom = _read(MILESTONES).get("events")
    if isinstance(custom, list):
        entries.extend(item for item in custom if isinstance(item, dict))
    result, seen = [], set()
    for item in entries:
        group = item.get("group")
        title = str(item.get("title") or "").strip()
        if group not in NAMES or not title or (group, title) in seen:
            continue
        source = str(item.get("source") or "")
        if source and not source.startswith("https://"):
            source = ""
        try:
            if item.get("month_day"):
                month, day_value = map(int, str(item["month_day"]).split("-"))
                first_year = None
            else:
                born = datetime.strptime(str(item.get("date") or ""), "%Y-%m-%d").date()
                month, day_value, first_year = born.month, born.day, born.year
            for year in (now.year, now.year + 1, now.year + 2):
                try:
                    next_day = datetime(year, month, day_value).date()
                except ValueError:
                    if (month, day_value) != (2, 29):
                        raise
                    next_day = datetime(year, 2, 28).date()
                if next_day >= now.date():
                    break
            if first_year is not None and next_day.year < first_year:
                continue
        except (ValueError, TypeError):
            continue
        seen.add((group, title))
        years = next_day.year - first_year if first_year else None
        result.append(((next_day - now.date()).days, group, title[:90],
                       next_day.strftime("%m/%d"), years, source))
    return sorted(result)[:10]


def _stats(now):
    rows = _read(SESSIONS).get("sessions")
    rows = rows if isinstance(rows, list) else []
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = {key: {"count": 0, "seconds": 0, "seconds_min": 0,
                    "seconds_max": 0, "recent": []} for key in NAMES}
    for row in rows:
        if not isinstance(row, dict) or row.get("group") not in result:
            continue
        started = _when(row.get("started"))
        if started is None or not (start_month <= started <= now):
            continue
        bucket = result[row["group"]]
        bucket["count"] += 1
        seconds = max(0, min(int(row.get("seconds") or 0), 172800))
        bucket["seconds"] += seconds
        bucket["seconds_min"] += max(0, int(row.get("seconds_min", seconds)))
        bucket["seconds_max"] += max(0, int(row.get("seconds_max", seconds)))
        bucket["recent"].append(row)
    for bucket in result.values():
        bucket["recent"].sort(key=lambda x: str(x.get("started") or ""), reverse=True)
        bucket["recent"] = bucket["recent"][:3]
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
        for dt, _, status, url, topic, precise, note in events:
            status_label = {"scheduled": "📺 방송", "break": "🌙 휴방", "cancelled": "🚫 취소"}[status]
            day = dt.strftime("%m/%d")
            hour = dt.strftime("%H:%M") if precise else (note or "시간 미정")
            label = status_label + " · " + day + " " + hour + " KST"
            if topic != "unspecified":
                label += " · " + _clean(topic).replace("|", ", ")
            if url:
                label += " · [공지](" + url + ")"
            lines.append(label)
        parts.append({"type": 10, "content": (
            "### " + name + " · 방송 일정\n" +
            ("\n".join(lines) if lines else "-# 확인된 향후 일정이 없습니다. (공지 기반)"))})
    anniversary_lines = []
    for days, group, title, month_day, years, source in milestones:
        label = "**" + NAMES[group] + "** · " + _clean(title)
        if years is not None:
            label += " " + str(years) + "주년"
        label += " (" + month_day + ") · " + ("오늘" if days == 0 else "D-" + str(days))
        if source:
            label += " · [출처](" + source + ")"
        anniversary_lines.append(label)
    parts.append({"type": 14, "divider": True, "spacing": 1})
    parts.append({"type": 10, "content": (
        "### 🎉 기념일 · 주요 기록\n" +
        ("\n".join(anniversary_lines) if anniversary_lines else
         "-# 확인된 기념일이 없습니다.") +
        "\n-# 공식 프로필·데뷔 공지 및 검증된 자료 기준")})
    parts.append({"type": 14, "divider": True, "spacing": 1})
    month_label = now.strftime("%Y년 %m월")
    statlines = []
    for group, name in NAMES.items():
        data = stats[group]
        mins = data["seconds"] // 60
        statlines.append("**" + name + "** · " + str(data["count"]) +
                         "회 · 약 " + str(mins // 60) + "시간 " + str(mins % 60) + "분")
        if data["count"] and data["seconds_max"] != data["seconds_min"]:
            uncertainty = data["seconds_max"] - data["seconds_min"]
            statlines.append("-# 종료 감지 오차 범위 · 최대 " +
                             str((uncertainty + 59) // 60) + "분")
        for session in data["recent"]:
            started = _when(session.get("started"))
            last_live = _when(session.get("ended_after"))
            off = _when(session.get("ended_before") or session.get("ended"))
            if not started or not off:
                continue
            start_note = "치지직 시작시각" if session.get("start_source") == "api_open_date" else "최초 관측"
            end = (last_live.strftime("%H:%M:%S") + " ~ " if last_live else "") + off.strftime("%H:%M:%S")
            statlines.append("-# " + started.strftime("%m/%d %H:%M:%S") +
                             " 시작(" + start_note + ") · 종료 " + end +
                             " KST · 약 " + str((int(session.get("seconds") or 0) + 30) // 60) + "분")
    parts.append({"type": 10, "content": (
        "### 📊 방송 활동 통계 · " + month_label + "\n" +
        "\n".join(statlines) +
        "\n-# 기능 활성화 후 시작·종료가 모두 관측된 방송만 집계합니다."
        "\n-# 수집 중단·재시작 구간의 누락 가능성이 있어 전체 방송 기록은 아닙니다.")})
    parts.append({"type": 14, "divider": True, "spacing": 1})
    parts.append({"type": 10, "content": (
        "-# 방송 예정 시간은 카페 공지 기준이며 실제 방송 시작 시간과 다를 수 있습니다."
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
