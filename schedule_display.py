"""Shared Korea-time 08:00 / 18:00 update schedule for banner and clip cards.

Discord's relative timestamp (<t:unix:R>) counts down in the *client* without
editing a live message every minute. Both cards share this single definition.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
UPDATE_HOURS = (8, 18)



def format_saved_time(value):
    """Format verified persisted timestamps for Discord, without guessing dates."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return "기록 없음"
    else:
        return "기록 없음"
    if moment.tzinfo is None:
        return "기록 없음"
    try:
        return f"<t:{int(moment.timestamp())}:f>"
    except (ValueError, OverflowError, OSError):
        return "기록 없음"


def next_update(now=None):
    """Strictly future scheduled slot (exactly 08:00 advances to 18:00)."""
    now = (now or datetime.now(KST)).astimezone(KST)
    for hour in UPDATE_HOURS:
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate
    return (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


def schedule_label(now=None):
    """Human-readable schedule and dynamic Discord relative-time countdown."""
    due = next_update(now)
    unix = int(due.timestamp())
    return (
        "**정기 업데이트** · 매일 08:00 / 18:00 KST\n"
        f"**다음 업데이트** · <t:{unix}:F>\n"
        f"**업데이트까지** · <t:{unix}:R>"
    )
