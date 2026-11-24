"""Pure, conservative preprocessing for public Korean broadcast notices.

No I/O or external API. Keep date-specific breaks separate from primary
broadcast text; distinguish a Space-only day from a CHZZK broadcast.
The storage layer owns freshness, conflict resolution and source repair.
"""
import re
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class NoticeAnalysis:
    primary_body: str
    secondary_breaks: tuple
    space_only: bool
    evidence: tuple
    secondary_broadcasts: tuple = ()


def analyze_notice(title, body, posted, resolve_date):
    source = str(title or "") + "\n" + str(body or "")
    space_only = bool(
        re.search(r"방송을?\s*쉬어|방송을?\s*쉽니다", source)
        and re.search(r"오늘.{0,30}(?:스페이스|스페로)", source)
    )
    if space_only:
        return NoticeAnalysis("", (), True, tuple(source.splitlines()))
    primary = resolve_date(title, "", posted.isoformat())
    kept, breaks, evidence, broadcasts = [], [], [], []
    for line in str(body or "").splitlines():
        if re.search(r"(?:스페이스|스페로|스페를|스페는|스페\s)", line) and re.search(r"만나|진행|예정|시작|켜|합니다|해요", line):
            continue
        day = None
        match = re.match(r"\s*(\d{1,2})일\s*(?:은|는|에)?\s*휴방", line)
        if match:
            day = next((d.isoformat() for d in
                        (posted.date() + timedelta(days=i) for i in range(31))
                        if d.day == int(match.group(1))), None)
        elif re.match(r"\s*(?:\d{1,2}월\s*\d{1,2}일|내일|모레)\s*(?:은|는|에)?\s*휴방", line):
            day = resolve_date("", line, posted.isoformat())
        # Preserve negated breaks, do not invent a second date when unclear.
        if (day and day != (primary or posted.date().isoformat())
                and not re.search(r"휴방\s*(?:이|은|가)?\s*아니", line)):
            breaks.append(day)
            evidence.append(line)
        else:
            dated = re.match(r"\s*(?:오늘|내일|모레|\d{1,2}월\s*\d{1,2}일|\d{1,2}일)\s*(?:은|는|에)?", line)
            if dated and re.search(r"방송|라이브|합방", line) and not re.search(r"휴방|어제|지난", line):
                day = resolve_date("", line, posted.isoformat())
                if not day:
                    m = re.match(r"\s*(\d{1,2})일", line)
                    if m:
                        day = next((d.isoformat() for d in
                                    (posted.date() + timedelta(days=i) for i in range(31))
                                    if d.day == int(m.group(1))), None)
                if day and primary and day != primary:
                    # Replace relative date with the resolved date to keep parsing scoped.
                    broadcasts.append((day, line[dated.end():]))
                    evidence.append(line)
                    continue
            kept.append(line)
    return NoticeAnalysis("\n".join(kept), tuple(dict.fromkeys(breaks)),
                          False, tuple(evidence), tuple(broadcasts))


def content_details(body):
    """Extract explicitly marked content only, not speculative game names."""
    source = str(body or "")
    result = {}
    match = re.search(r"(?:좀비\s*게임|게임)\s+([가-힣A-Za-z0-9][가-힣A-Za-z0-9 \-]{0,35}?)\s+(?:해보려고|할\s*예정|합니다|할게)", source)
    if match:
        result["content"] = match.group(1).strip()
    if re.search(r"솔플|솔로\s*플레이", source):
        result["play_type"] = "솔로 플레이"
    return result


def space_events(title, body, posted, resolve_date, resolve_clock):
    """Extract explicit X Space plans independently of CHZZK events."""
    events = []
    source = str(title or "") + "\n" + str(body or "")
    for line in re.split(r"[\n!?。]+", source):
        if not re.search(r"스페이스|스페로|스페를|스페는|스페\s", line):
            continue
        if not re.search(r"만나|진행|예정|시작|켜|합니다|해요", line):
            continue
        if re.search(r"어제|지난|종료|취소|안\s*해|하지\s*않", line):
            continue
        day = resolve_date("", line, posted.isoformat())
        if not day:
            continue
        clock, note = resolve_clock(line)
        if clock is None:
            continue
        event = {"date": day, "kind": "🎙️ X 스페이스 안내",
                 "status": "space", "time": clock or "", "time_note": note,
                 "topic": "x_space", "platform": "x"}
        if event not in events:
            events.append(event)
    return events
