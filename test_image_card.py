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

def _font(size, bold=False):
    """Prefer installed Korean fonts; keep the renderer functional without them."""
    from PIL import ImageFont
    variants = (
        ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
        if bold else
        ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    )
    for font in variants + ["/usr/share/fonts/truetype/dejavu/DejaVuSans" +
                            ("-Bold" if bold else "") + ".ttf"]:
        try:
            return ImageFont.truetype(font, size)
        except OSError:
            continue
    return ImageFont.load_default()


def card_image(group="hanayo"):
    """Render a reusable profile-only header. All post content stays in V2."""
    from PIL import Image, ImageDraw, ImageOps
    from creator_profiles import avatar as profile_avatar
    from urllib.parse import urlsplit
    style = {
        "hanayo": ((77, 51, 98), (142, 87, 129), "하나요 히요리",
                   "@HanayoHiyori", "🌸 H I Y O R I"),
        "sakihane": ((47, 46, 92), (101, 77, 147), "사키하네 후야",
                     "@Sakihanechannel", "💜 H U Y A"),
    }
    a, b, name, handle, theme = style[group]
    width, height = 960, 248
    im = Image.new("RGB", (width, height))
    background = ImageDraw.Draw(im)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(x * (1 - t) + z * t) for x, z in zip(a, b))
        background.line((0, y, width, y), fill=color)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((18, 18, width - 18, height - 18), radius=28,
                        outline=(201, 178, 216), width=2)
    d.text((57, 42), name, font=_font(48, True), fill=(255, 255, 255))
    d.text((58, 114), handle, font=_font(28), fill=(225, 210, 237))
    # A compact romanized theme on the final line: no announcement text.
    d.text((58, 168), theme, font=_font(27, True), fill=(246, 207, 233))
    # Official avatar comes from the existing profile cache. No unofficial art.
    avatar_url = profile_avatar(group)
    portrait = None
    if avatar_url:
        parsed = urlsplit(avatar_url)
        if parsed.scheme == "https" and parsed.hostname in (
                "yt3.googleusercontent.com", "yt3.ggpht.com"):
            try:
                response = requests.get(avatar_url, timeout=6,
                                        headers={"User-Agent": "abyss-bot/1.0"})
                response.raise_for_status()
                if len(response.content) <= 4_000_000:
                    portrait = Image.open(io.BytesIO(response.content))
                    portrait.load()
            except (requests.RequestException, OSError, ValueError):
                portrait = None
    size, x, y = 172, 750, 38
    d.ellipse((x - 4, y - 4, x + size + 4, y + size + 4),
              outline=(244, 215, 247), width=5)
    if portrait is not None:
        portrait = ImageOps.fit(portrait.convert("RGB"), (size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size-1, size-1), fill=255)
        im.paste(portrait, (x, y), mask)
    else:
        # Avatar not cached or temporarily unavailable: use a neutral symbol.
        d.ellipse((x, y, x + size, y + size), fill=(104, 89, 133))
        d.ellipse((x + 57, y + 37, x + 115, y + 95), fill=(213, 193, 227))
        d.rounded_rectangle((x + 38, y + 106, x + 134, y + 156),
                            radius=30, fill=(213, 193, 227))
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
    container.insert(0, {"type": 12, "items": [{
        "media": {"url": "attachment://abyss_preview.png"},
        "description": "스트리머 공식 프로필 헤더"}]})
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("hanayo","sakihane"), default="hanayo")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--both", action="store_true", help="Send two distinct layouts to the same fixed test thread")
    parser.add_argument("--use-existing-webhook", action="store_true", help="Explicit manual preview with existing webhook, only to the fixed test thread")
    args = parser.parse_args()
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    payload, image = make_preview(args.group)
    if args.send:
        target = os.getenv("DISCORD_WEBHOOK_URL", "") if args.use_existing_webhook else os.getenv("TEST_DISCORD_WEBHOOK_URL","")
        if not target:
            raise SystemExit("Selected webhook is not configured")
        send_preview(target, payload, image)
        print("Preview sent to designated test thread with all role mentions disabled.")
    else:
        print(json.dumps({"image_header": payload, "integrated": integrated, "png_bytes": len(image)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
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


