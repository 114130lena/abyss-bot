"""Discord Components V2 layout: single accent-colored container per alert."""
FLAGS = 1 << 15
COLORS = {"chzzk": 0x00FFA3, "youtube": 0xFF4747,
          "x": 0x1DA1F2, "cafe": 0x03C75A}

def text(value):
    return {"type": 10, "content": str(value)[:4000]}

def separator():
    return {"type": 14, "divider": True, "spacing": 1}

def gallery(url, description=""):
    return {"type": 12, "items": [{"media": {"url": url}, "description": description[:1024]}]}

def payload(platform, role_id, banner, headline, creator, details, media_url="",
            media_description="", link_url="", link_label="원본 바로가기",
            username="어비스봇", ping=True):
    """Order: banner → notification/creator → media → details/link."""
    parts = []
    if banner and banner.startswith("https://"):
        parts.append(gallery(banner, "스트리머 공식 채널 배너"))
    parts.append(text("**" + headline + "**\n" + creator))
    if media_url and media_url.startswith("https://"):
        parts.append(gallery(media_url, media_description))
    if details:
        if platform in ("chzzk", "youtube"):
            for label in ("**방송 제목**\n", "**영상 제목**\n", "**다시보기 제목**\n"):
                details = details.replace(label, "")
            if "**카테고리**\n" in details:
                title, category = details.split("**카테고리**\n", 1)
                details = "**" + title.strip() + "**\n" + category.strip()
            elif not details.startswith("**"):
                details = "**" + details.strip() + "**"
        elif platform == "x":
            details = "\n".join("> " + line if line.strip() else ">" for line in details.splitlines())
        parts.append(text(details[:2800]))
    if link_url and link_url.startswith("https://"):
        parts.append(separator())
        parts.append(text("[🔗 " + link_label + "](" + link_url + ")"))
    # Discord v2 does not allow legacy content or embeds.
    # Place the mention as an independent Text Display above the container.
    # Discord Components V2 Container has a limit of 10 direct children.
    # Preserve the official banner, role mention, and original link on long posts.
    if len(pieces) > 10:
        raise ValueError("Discord card contains too many components")
    outer = []
    if role_id:
        outer.append(text("<@&" + role_id + ">" if ping else "[테스트] 역할 " + role_id + " (멘션 비활성화)"))
    outer.append({"type": 17, "accent_color": COLORS[platform], "components": parts})
    return {"username": username, "flags": FLAGS, "components": outer,
            "allowed_mentions": {"parse": [], "roles": [role_id] if role_id and ping else []}}


def _valid_url(value):
    return isinstance(value, str) and value.startswith("https://")


def _safe_markdown(value):
    """Escape Markdown-sensitive title characters without changing normal text."""
    result = str(value or "").replace("\\", "\\\\")
    for char in ("[", "]", "(", ")", "*", "_", "~", "`"):
        result = result.replace(char, "\\" + char)
    return result


def _korean_datetime(value):
    """Render provided timestamps in Asia/Seoul; preserve unknown input."""
    if not value:
        return ""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    try:
        raw = str(value).strip()
        if raw.isdigit() and len(raw) in (10, 13):
            epoch = int(raw) / (1000 if len(raw) == 13 else 1)
            return datetime.fromtimestamp(epoch, tz=ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d %H:%M")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d %H:%M")
    except (ValueError, TypeError, OverflowError):
        return str(value)[:40]


def _x_headline(body):
    """Turn the first nonempty post line into a direct link title."""
    import re
    lines = str(body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            headline = line.strip()[:220]
            remaining = "\n".join(lines[i + 1:]).strip()
            return headline, remaining
    return "X 게시글", ""


def _notice_type(title, body):
    """Classify explicitly announced streaming events, not casual greetings."""
    import re
    source = (str(title or "") + "\n" + str(body or ""))[:3500]
    # Only an explicit stream-related sentence is enough to declare a status.
    negated = re.search(
        r"(?:휴방|방송\s*취소|방송\s*중단)\s*(?:은|이|가)?\s*"
        r"(?:아닙니다|아니에요|아님|없습니다|없어요)", source)
    if negated:
        return None
    rules = (
        (r"방송(?:을|이)?\s*취소|방송\s*중단|라이브\s*취소|"
         r"(?:방송|합방)\s*일정\s*취소", "🚫 방송 취소 안내"),
        (r"(?:방송|합방|라이브)?\s*(?:일정|시간)\s*(?:변경|수정|조정)|"
         r"(?:방송|합방)\s*(?:연기|지연|변경)|변경된\s*(?:방송|합방)?\s*일정", "🔄 일정 변경 안내"),
        (r"휴방|오늘(?:은|도)?\s*(?:방송을?)?\s*쉬(?:어요|겠습니다|는\s*날|기로)|"
         r"오늘(?:은|도)?\s*쉽니다", "🌙 휴방 안내"),
        (r"합방(?:이|은|을|은요|은욤)?\s*(?:있|합니|해요|해욥|예정|시작)|"
         r"(?:오늘|내일|이따|저녁|밤에|오후|오전).{0,24}합방|"
         r"합방.{0,24}(?:오늘|내일|저녁|오후|오전)", "🎮 합방 예고"),
        (r"방송\s*(?:일정|예정|공지|시작)|오늘\s*방송|라이브\s*예정|"
         r"방송할\s*예정|방송합니다|방송\s*(?:켤게|킬게|켜요|켜겠)|"
         r"(?:오늘|이따|저녁).{0,20}방송에서\s*만나", "📺 방송 예고"),
    )
    for pattern, label in rules:
        if re.search(pattern, source, re.IGNORECASE):
            return label
    return None


def _schedule_facts(title, body):
    """Extract only unambiguous, explicitly written calendar and clock values."""
    import re
    source = str(title or "") + "\n" + str(body or "")
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not any(word in source for word in ("방송", "일정", "라이브", "LIVE", "대전",
                                           "이벤트", "휴방", "합방")):
        return []
    blocked = ("변경 전", "기존 시간", "원래", "취소", "예정이었", "지난", "어제", "아닐", "미정")
    date_pattern = re.compile(r"(?<!\d)(1[0-2]|[1-9])월\s*(3[01]|[12]\d|[1-9])일")
    time_pattern = re.compile(
        r"(오전|오후)\s*(1[0-2]|[1-9])시(?:\s*(?:[0-5]?\d분?|반))?"
        r"|(?<!\d)([01]?\d|2[0-3]):([0-5]\d)")
    dates, times = [], []
    for line in lines:
        if any(word in line for word in blocked):
            continue
        dates.extend(m.group(0) for m in date_pattern.finditer(line))
        times.extend(m.group(0) for m in time_pattern.finditer(line))
    dates = list(dict.fromkeys(dates))
    times = list(dict.fromkeys(times))
    if len(dates) > 1 or len(times) > 1:
        return []
    result = []
    if dates:
        result.append("📅 날짜 · " + dates[0])
    if times:
        result.append("🕒 언급된 시간 · " + times[0])
    return result


def _today_summary(title, body, published, now=None):
    """Show TODAY only for a confirmed same-day event in Korean local time.

    Relative 'today' references are valid only when the post itself is from today.
    Never use the current date as a replacement for a missing publication timestamp.
    """
    import re
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("Asia/Seoul")
    today = (now or datetime.now(zone)).astimezone(zone).date()
    category = _notice_type(title, body)
    if not category:
        return ""
    post_day = _parse_korean_datetime(published)
    if post_day is None or post_day.date() > today or post_day.year != today.year:
        return ""
    source = str(title or "") + "\n" + str(body or "")
    if any(x in source for x in ("변경 전", "기존 일정", "원래", "어제", "취소되었습니다", "내일", "모레")):
        return ""
    dates = set((int(m.group(1)), int(m.group(2))) for m in re.finditer(
        r"(?<!\d)(1[0-2]|[1-9])월\s*(3[01]|[12]\d|[1-9])일", source))
    if dates:
        if len(dates) != 1 or (today.month, today.day) not in dates:
            return ""
        # A post from a prior year cannot authorize today's same month/day.
        if published:
            post_day = _parse_korean_datetime(published)
            if post_day is None or post_day.date().year != today.year:
                return ""
    elif re.search(r"오늘|금일", source):
        post_day = _parse_korean_datetime(published)
        if post_day is None or post_day.date() != today:
            return ""
    else:
        return ""
    return "📍 **TODAY · " + category.split(" ", 1)[1] + "**"


def _parse_korean_datetime(value):
    from datetime import datetime
    from email.utils import parsedate_to_datetime
    from zoneinfo import ZoneInfo
    if not value:
        return None
    zone = ZoneInfo("Asia/Seoul")
    try:
        raw = str(value).strip()
        if raw.isdigit() and len(raw) in (10, 13):
            return datetime.fromtimestamp(int(raw) / (1000 if len(raw) == 13 else 1), zone)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(raw)
        return (dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone))
    except (ValueError, TypeError, OverflowError):
        return None


def _creator_theme(creator, handle):
    """Cosmetic only: do not change role IDs or platform color accents."""
    identity = (str(creator) + " " + str(handle)).casefold()
    if "히요리" in identity or "hanayo" in identity:
        return "🌸 H I Y O R I"
    if "후야" in identity or "sakihane" in identity:
        return "💜🐲 H U Y A"
    return ""


def _link_button(url, platform, replay=False):
    label = ("다시보기 열기" if replay else "방송 보러가기" if platform == "chzzk"
             else "영상 시청하기" if platform == "youtube"
             else "게시글 전체 보기" if platform == "cafe" else "X 원문 보기")
    return {"type": 1, "components": [{"type": 2, "style": 5, "label": label[:80], "url": url}]}


def alert(platform, creator, banner="", role_id="", title="", body="",
          url="", media_url="", images=None, category="", date="",
          author="", board="", replay=False, ping=True, username="정덕삼",
          handle="", avatar=""):
    """Unified V2 card: creator, platform, title/media, date, final banner."""
    creator = str(creator or "알 수 없음")
    tag = {
        "chzzk": "🟢 CHZZK · 다시보기" if replay else "🟢 CHZZK",
        "youtube": "🔴 YOUTUBE",
        "x": "𝕏 X",
        "cafe": "🟢 NAVER CAFE",
    }[platform]
    pieces = []
    # Native Section profile header: name/handle left, creator portrait right.
    identity = "## " + _safe_markdown(creator)
    if "히요리" in creator:
        identity += " HANAYO HIYORI"
    elif "후야" in creator:
        identity += " SAKIHANE HUYA"
    if "후야" in creator:
        identity += "\n-# @SakihaneHuya"
    elif handle:
        identity += "\n-# " + _safe_markdown(handle)
    if "히요리" in creator:
        identity += "\n## STELLIVE · ABYSS"
    elif "후야" in creator:
        identity += "\n## STELLIVE · EVERYS"
    if _valid_url(avatar) or str(avatar).startswith("attachment://"):
        pieces.append({"type": 9, "components": [text(identity)],
                       "accessory": {"type": 11, "media": {"url": avatar},
                                     "description": creator + " 프로필 사진"}})
    else:
        pieces.append(text(identity))
    pieces.append(separator())
    pieces.append(text("**🟢 CHZZK · LIVE**" if platform == "chzzk" and not replay else "**" + tag + "**"))
    if platform == "x" and not title:
        title, body = _x_headline(body)
    if title:
        display = _safe_markdown(str(title).replace("\n", " ").strip()[:500])
        # Title is clickable; category follows outside the hyperlink on the same line.
        title_line = "[" + display + "](" + url + ")" if _valid_url(url) else display
        if platform == "chzzk" and category:
            pieces.append(text("## " + title_line + "\n-# · " + _safe_markdown(str(category)[:80])))
        else:
            pieces.append(text("## " + title_line))
    analysis_lines = []
    if platform in ("cafe", "x"):
        notice_type = _notice_type(title, body)
        if notice_type:
            from notice_intelligence import participants
            companions = participants(title, body, "히요리" if "히요리" in creator else "후야" if "후야" in creator else "") if notice_type == "🎮 합방 예고" else []
            subtitle = notice_type + (" · " + ", ".join(companions) + "와 함께" if len(companions) == 1 else
                                      " · " + ", ".join(companions) + " 참여" if companions else "")
            analysis_lines.append("-# " + subtitle)

    if body:
        import re
        content = str(body).replace("\r\n", "\n").replace("\r", "\n")
        content = re.sub(r"\n{4,}", "\n\n\n", content).strip()
        if content:
            if platform == "cafe" and len(content) > 3000:
                content = content[:2980].rstrip() + "\n\n… [전체 글 보기](" + url + ")" if _valid_url(url) else content[:3000]
            if analysis_lines:
                content = "\n\n".join(analysis_lines) + "\n\n" + content
                analysis_lines = []
            pieces.append(text(content[:3900] if platform == "cafe" else content[:2900]))
    if analysis_lines:
        pieces.append(text("\n\n".join(analysis_lines)))
    media = []
    if _valid_url(media_url):
        media.append(media_url)
    for image_url in (images or []):
        if _valid_url(image_url) and image_url not in media:
            media.append(image_url)
        if len(media) >= 4:
            break
    if media:
        pieces.append({"type": 12, "items": [
            {"media": {"url": value}, "description":
             "첨부 이미지" if platform in ("x", "cafe") else "썸네일"}
            for value in media[:4]
        ]})
    footer = []
    if category and platform != "chzzk":
        footer.append(str(category)[:100])
    if date and platform != "chzzk":
        label = ("방송 시작" if platform == "chzzk" and not replay else
                 "등록" if replay else "업로드" if platform == "youtube" else "작성")
        footer.append(label + " · " + _korean_datetime(date) + " KST")
    if platform == "cafe" and not date:
        footer.append("작성 시간 정보 없음")
    if footer:
        pieces.append(separator())
        pieces.append(text(" · ".join(footer)))
    # Titles are clickable; retain a source button for platforms without linked titles.
    if _valid_url(url) and not title:
        pieces.append(_link_button(url, platform, replay))
    if _valid_url(banner):
        pieces.append(separator())
        # Small signature above the full-width official YouTube banner.
        signature = _creator_theme(creator, handle)
        label = "-# 공식 채널 · OFFICIAL LINKS"
        if signature:
            label += " · " + signature
        pieces.append(text(label))
        pieces.append(gallery(banner, "공식 유튜브 채널 배너"))
    import os
    identity_key = (creator + " " + handle).casefold()
    group = ("hanayo" if "히요리" in identity_key or "hanayo" in identity_key
             else "sakihane" if "후야" in identity_key or "sakihane" in identity_key
             else "")
    # Never substitute another creator's links if a channel ID is not configured.
    if group:
        from creator_groups import CHZZK_GROUPS
        chzzk_id = next((cid for cid, g in CHZZK_GROUPS.items() if g == group), "")
        youtube = ("https://www.youtube.com/@HanayoHiyori" if group == "hanayo"
                   else "https://www.youtube.com/@Sakihanechannel")
        twitter = ("https://x.com/HanayoHiyori" if group == "hanayo"
                   else "https://x.com/SakihaneHuya")
        chzzk = os.getenv(group.upper() + "_CHZZK_URL", "").strip()
        if not _valid_url(chzzk) and chzzk_id:
            chzzk = "https://chzzk.naver.com/" + chzzk_id
        cafe = os.getenv("STELLIVE_CAFE_URL", "https://cafe.naver.com/f-e/cafes/29424353").strip()
        links = [("CHZZK", chzzk), ("CAFE", cafe),
                 ("X", twitter), ("YouTube", youtube)]
        buttons = [{"type": 2, "style": 5, "label": name, "url": address}
                   for name, address in links if _valid_url(address)]
        if buttons:
            pieces.append({"type": 1, "components": buttons})
    # Discord containers are limited to 10 direct components; avoid API rejection.
    # Prefer dropping optional separators if posts contain many independent fields.
    while len(pieces) > 10:
        for index in range(len(pieces) - 1, -1, -1):
            if pieces[index]["type"] == 14:
                pieces.pop(index)
                break
        else:
            # When a long post exceeds the limit, merge neighboring text displays.
            for index in range(len(pieces) - 2):
                if pieces[index]["type"] == 10 and pieces[index+1]["type"] == 10:
                    pieces[index]["content"] += "\n" + pieces.pop(index+1)["content"]
                    break
            else:
                raise ValueError("Too many Discord V2 components")
    outer = []
    if role_id:
        outer.append(text("<@&" + role_id + ">" if ping else "[테스트] 멘션 비활성화"))
    outer.append({"type": 17, "accent_color": COLORS[platform], "components": pieces})
    return {"username": username, "flags": FLAGS, "components": outer,
            "allowed_mentions": {"parse": [], "roles": [role_id] if role_id and ping else []}}
