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
    """Prevent source titles from breaking clickable Markdown links."""
    import re
    return re.sub(r"([\\\\[\\]()*_~`>])", r"\\\\\\1", str(value or ""))


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


def alert(platform, creator, banner="", role_id="", title="", body="",
          url="", media_url="", images=None, category="", date="",
          author="", board="", replay=False, ping=True, username="정덕삼"):
    """Unified Discord V2 presentation across all notification workers."""
    creator = str(creator or "알 수 없음")
    tag, status = {
        "chzzk": ("🟢 CHZZK", "REPLAY" if replay else "LIVE"),
        "youtube": ("🔴 YOUTUBE", "NEW VIDEO"),
        "x": ("𝕏 X", "NEW POST"),
        "cafe": ("🟢 NAVER CAFE", "NEW POST"),
    }[platform]
    pieces = []
    if _valid_url(banner):
        pieces.append(gallery(banner, "스트리머 공식 배너"))
    pieces.append(text("**" + tag + " · " + creator + " · " + status + "**"))
    pieces.append(separator())
    if title:
        display = _safe_markdown(str(title).replace("\\n", " ").strip()[:500])
        if _valid_url(url):
            pieces.append(text("## [" + display + "](" + url + ")"))
        else:
            pieces.append(text("## " + display))
    if body:
        import re
        content = str(body).replace("\\r\\n", "\\n").replace("\\r", "\\n")
        content = re.sub(r"\\n{4,}", "\\n\\n\\n", content).strip()
        if content:
            pieces.append(text(content[:2500]))
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
            {"media": {"url": value}, "description": "첨부 이미지" if platform in ("x", "cafe") else "썸네일"}
            for value in media[:4]
        ]})
    footer = []
    if category:
        footer.append(str(category)[:100])
    elif board:
        footer.append(str(board)[:100])
    elif replay:
        footer.append("다시보기")
    elif platform == "youtube":
        footer.append("영상 업로드")
    elif platform == "x":
        footer.append("게시글 작성")
    elif platform == "cafe":
        footer.append("카페 게시글")
    if date:
        footer.append(_korean_datetime(date))
    if author and platform == "cafe":
        footer.append("작성자 " + str(author)[:80])
    if footer or (platform == "x" and _valid_url(url)):
        pieces.append(separator())
        if footer:
            pieces.append(text(" · ".join(footer)))
        if platform == "x" and _valid_url(url):
            pieces.append(text("[원문 보기](" + url + ")"))
    outer = []
    if role_id:
        outer.append(text("<@&" + role_id + ">" if ping else "[테스트] 멘션 비활성화"))
    outer.append({"type": 17, "accent_color": COLORS[platform], "components": pieces})
    return {"username": username, "flags": FLAGS, "components": outer,
            "allowed_mentions": {"parse": [], "roles": [role_id] if role_id and ping else []}}
