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
    identity = "## " + _safe_markdown(creator)
    if handle:
        identity += "\n" + _safe_markdown(handle)
    if _valid_url(avatar):
        pieces.append({"type": 9, "components": [text(identity)],
                       "accessory": {"type": 11, "media": {"url": avatar},
                                     "description": creator + " 프로필 사진"}})
    else:
        pieces.append(text(identity))
    pieces.append(separator())
    pieces.append(text("**" + tag + "**"))
    if platform == "x" and not title:
        title, body = _x_headline(body)
    if title:
        display = _safe_markdown(str(title).replace("\n", " ").strip()[:500])
        if _valid_url(url):
            pieces.append(text("## [" + display + "](" + url + ")"))
        else:
            pieces.append(text("## " + display))
    if body:
        import re
        content = str(body).replace("\r\n", "\n").replace("\r", "\n")
        content = re.sub(r"\n{4,}", "\n\n\n", content).strip()
        if content:
            if platform == "cafe" and len(content) > 3000:
                content = content[:2980].rstrip() + "\n\n… [전체 글 보기](" + url + ")" if _valid_url(url) else content[:3000]
            pieces.append(text(content[:3200] if platform == "cafe" else content[:2500]))
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
    if category:
        footer.append(str(category)[:100])
    if date:
        label = ("방송 시작" if platform == "chzzk" and not replay else
                 "등록" if replay else "업로드" if platform == "youtube" else "작성")
        footer.append(label + " · " + _korean_datetime(date) + " KST")
    if footer:
        pieces.append(separator())
        pieces.append(text(" · ".join(footer)))
    if _valid_url(banner):
        pieces.append(gallery(banner, "스트리머 공식 배너"))
    outer = []
    if role_id:
        outer.append(text("<@&" + role_id + ">" if ping else "[테스트] 멘션 비활성화"))
    outer.append({"type": 17, "accent_color": COLORS[platform], "components": pieces})
    return {"username": username, "flags": FLAGS, "components": outer,
            "allowed_mentions": {"parse": [], "roles": [role_id] if role_id and ping else []}}
