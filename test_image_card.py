"""Optional image-card + Discord Components V2 preview. Production notifications stay unchanged."""
import argparse
import io
import json
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TEST_THREAD_ID = "1566335914500816988"

from dotenv import load_dotenv
from discord_v2 import alert
from banner_manager import get_banner

def _font(size, bold=False, require_korean=False):
    """Use a real Korean font; never render Hangul through DejaVu tofu glyphs."""
    from PIL import ImageFont
    from pathlib import Path
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold
        else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for font in candidates:
        if Path(font).is_file():
            return ImageFont.truetype(font, size)
    if require_korean:
        return None
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans" +
        ("-Bold" if bold else "") + ".ttf", size)


def _fitted_font(draw, value, max_width, start=48, minimum=22, bold=False,
                 require_korean=False):
    """Fit all text before drawing, rather than cutting a name at a line break."""
    for size in range(start, minimum - 1, -1):
        font = _font(size, bold=bold, require_korean=require_korean)
        if font and draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return font
    return _font(minimum, bold=bold, require_korean=require_korean)


def card_image(group="hanayo"):
    """Discord-like compact creator header; posts remain native copyable V2 text."""
    from PIL import Image, ImageDraw, ImageOps
    from creator_profiles import avatar as profile_avatar
    from urllib.parse import urlsplit
    style = {
        "hanayo": ((51, 43, 64), (83, 57, 87), "하나요 히요리",
                   "HANAYO HIYORI", "@HanayoHiyori", "H I Y O R I", (242, 172, 204)),
        "sakihane": ((45, 43, 67), (74, 60, 105), "사키하네 후야",
                     "SAKIHANE HUYA", "@SakihaneHuya", "H U Y A", (192, 173, 246)),
    }
    a, b, name_ko, name_en, handle, romanized, accent = style[group]
    width, height = 960, 252
    im = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(im)
    for y in range(height):
        t = y / (height - 1)
        d.line((0, y, width, y),
               fill=tuple(round(x * (1 - t) + z * t) for x, z in zip(a, b)))
    d.rounded_rectangle((14, 14, 946, 238), radius=22,
                        outline=(113, 107, 134), width=2)
    d.rounded_rectangle((40, 48, 47, 205), radius=3, fill=accent)

    text_left = 78
    max_text_width = 595
    korean_font = _fitted_font(d, name_ko, max_text_width, 47, 28,
                               bold=True, require_korean=True)
    # If the server has no Korean fonts, display readable romanization,
    # rather than missing-glyph rectangles; CI/server should install fonts-nanum.
    name = name_ko if korean_font else name_en
    font_name = korean_font or _fitted_font(d, name_en, max_text_width, 42, 27, True)
    d.text((text_left, 45), name, font=font_name, fill=(253, 252, 255))

    handle_font = _fitted_font(d, handle, max_text_width, 31, 23)
    d.text((text_left, 117), handle, font=handle_font, fill=(209, 205, 223))
    d.text((text_left, 178), romanized,
           font=_fitted_font(d, romanized, max_text_width, 26, 20, True),
           fill=accent)

    # Profile ring uses consistent sizing; official cached avatar only.
    avatar_url = profile_avatar(group)
    portrait = None
    if avatar_url:
        parsed = urlsplit(avatar_url)
        if parsed.scheme == "https" and parsed.hostname in (
                "yt3.googleusercontent.com", "yt3.ggpht.com"):
            try:
                res = requests.get(avatar_url, timeout=6,
                                   headers={"User-Agent": "abyss-bot/1.0"})
                res.raise_for_status()
                if len(res.content) <= 4_000_000:
                    portrait = Image.open(io.BytesIO(res.content))
                    portrait.load()
            except (requests.RequestException, OSError, ValueError):
                pass
    size, x, y = 178, 736, 37
    d.ellipse((x - 5, y - 5, x + size + 5, y + size + 5),
              outline=accent, width=5)
    if portrait is not None:
        portrait = ImageOps.fit(portrait.convert("RGB"), (size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        im.paste(portrait, (x, y), mask)
    else:
        d.ellipse((x, y, x + size, y + size), fill=(103, 95, 122))
        d.ellipse((x + 59, y + 38, x + 119, y + 98), fill=(213, 204, 226))
        d.rounded_rectangle((x + 40, y + 109, x + 138, y + 156),
                            radius=24, fill=(213, 204, 226))
    buffer = io.BytesIO()
    im.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

def make_integrated_preview(group="hanayo"):
    """Single V2 container, using native profile Section rather than image overlay."""
    from creator_profiles import handle, avatar
    creator = "하나요 히요리" if group == "hanayo" else "사키하네 후야"
    card = alert("x", creator, get_banner(group), "",
                 title="[통합형 테스트] 프로필 + V2 본문",
                 body="메리와 합방 있어요!\n\n같은 컨테이너 안의 프로필, 게시글 원문, 부제목, 날짜와 버튼을 비교합니다.",
                 date=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                 url="https://x.com/HanayoHiyori" if group == "hanayo" else "https://x.com/SakihaneHuya",
                 handle=handle(group), avatar=avatar(group), ping=False)
    card["username"] = "어비스봇 · 1안 통합형"
    card["allowed_mentions"] = {"parse": []}
    return card


def make_preview(group="hanayo"):
    creator = "하나요 히요리" if group == "hanayo" else "사키하네 후야"
    card = alert("x", creator, get_banner(group), "",
                 title="[프로필 헤더형 테스트] 이미지 + V2",
                 body="메리와 합방 있어요!\n\n게시글 원문과 부제목, 날짜, 버튼은 V2 본문에 표시됩니다.",
                 date=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                 url="https://x.com/HanayoHiyori" if group == "hanayo" else "https://x.com/SakihaneHuya",
                 ping=False)
    container = card["components"][0]["components"]
    # Remove the redundant V2 profile display and its following separator.
    if container and container[0].get("type") in (9, 10):
        container.pop(0)
    if container and container[0].get("type") == 14:
        container.pop(0)
    # Only one X brand mark, no duplicate "𝕏 X".
    if container and container[0].get("type") == 10:
        container[0]["content"] = container[0]["content"].replace("𝕏 X", "𝕏")
    # A native divider keeps the header and post content aligned across screens.
    container.insert(0, {"type": 12, "items": [{
        "media": {"url": "attachment://abyss_preview.png"},
        "description": "스트리머 공식 프로필 헤더"}]})
    container.insert(1, {"type": 14, "divider": True, "spacing": 1})
    if len(container) > 10:
        raise ValueError("Discord V2 container component limit exceeded")
    card["username"] = "어비스봇 이미지 미리보기"
    card["allowed_mentions"] = {"parse": []}
    return card, card_image(group)

def send_preview(webhook, payload, image, thread_id=TEST_THREAD_ID):
    if not webhook.startswith("https://"):
        raise ValueError("TEST_DISCORD_WEBHOOK_URL must be HTTPS")
    parsed = urlsplit(webhook)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["with_components"] = "true"
    query["thread_id"] = str(thread_id)
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    response = requests.post(endpoint,
                             data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                             files={"files[0]": ("abyss_preview.png", image, "image/png")},
                             timeout=20)
    response.raise_for_status()

def send_integrated_preview(webhook, payload, thread_id=TEST_THREAD_ID):
    """Native V2-only comparison; same pinned thread, no image attachment."""
    if not webhook.startswith("https://"):
        raise ValueError("Existing webhook must be HTTPS")
    parsed = urlsplit(webhook)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["with_components"] = "true"
    query["thread_id"] = str(thread_id)
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    response = requests.post(endpoint, json=payload, timeout=20)
    response.raise_for_status()



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("hanayo","sakihane"), default="hanayo")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--both", action="store_true", help="Send two distinct layouts to the same fixed test thread")
    parser.add_argument("--use-existing-webhook", action="store_true", help="Explicit manual preview with existing webhook, only to the fixed test thread")
    args = parser.parse_args()
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    payload, image = make_preview(args.group)
    integrated = make_integrated_preview(args.group) if args.both else None
    if args.send:
        target = os.getenv("DISCORD_WEBHOOK_URL", "") if args.use_existing_webhook else os.getenv("TEST_DISCORD_WEBHOOK_URL","")
        if not target:
            raise SystemExit("Selected webhook is not configured")
        if integrated is not None:
            send_integrated_preview(target, integrated)
        send_preview(target, payload, image)
        print("Two designs sent." if args.both else "One design sent.")
        print("All role mentions disabled.")
    else:
        print(json.dumps({"image_header": payload, "integrated": integrated, "png_bytes": len(image)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
