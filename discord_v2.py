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
            for label in ("**방송 제목**\\n", "**영상 제목**\\n", "**다시보기 제목**\\n"):
                details = details.replace(label, "")
            if "**카테고리**\\n" in details:
                title, category = details.split("**카테고리**\\n", 1)
                details = "**" + title.strip() + "**\\n" + category.strip()
            elif not details.startswith("**"):
                details = "**" + details.strip() + "**"
        elif platform == "x":
            details = "\\n".join("> " + line if line.strip() else ">" for line in details.splitlines())
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
