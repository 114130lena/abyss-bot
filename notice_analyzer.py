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


def analyze_notice(title, body, posted, resolve_date):
    source = str(title or "") + "\n" + str(body or "")
    space_only = bool(
        re.search(r"방송을?\s*쉬어|방송을?\s*쉽니다", source)
        and re.search(r"오늘.{0,30}(?:스페이스|스페로)", source)
    )
    if space_only:
        return NoticeAnalysis("", (), True, tuple(source.splitlines()))
    primary = resolve_date(title, "", posted.isoformat())
    kept, breaks, evidence = [], [], []
    for line in str(body or "").splitlines():
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
            kept.append(line)
    return NoticeAnalysis("\n".join(kept), tuple(dict.fromkeys(breaks)),
                          False, tuple(evidence))
