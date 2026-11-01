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

def card_image(group="hanayo"):
    from PIL import Image, ImageDraw, ImageFont
    style = {"hanayo": ((92, 60, 115), (195, 118, 160), "HANAYO HIYORI", "COLLAB STREAM"),
             "sakihane": ((53, 50, 102), (125, 95, 179), "SAKIHANE HUYA", "COLLAB STREAM")}
    a, b, name, headline = style[group]
    width, height = 960, 390
    im = Image.new("RGB", (width, height))
    pixels = im.load()
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(x*(1-t)+z*t) for x,z in zip(a,b))
        for x in range(width):
            pixels[x,y] = color
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((26, 26, width-26, height-26), radius=26, outline=(224, 200, 241), width=2)
    try:
        large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 71)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except OSError:
        large = ImageFont.load_default()
        small = ImageFont.load_default()
    d.text((76, 72), name, font=small, fill=(238, 218, 251))
    d.text((76, 148), headline, font=large, fill=(255,255,255))
    d.line((76, 279, 880, 279), fill=(232, 205, 240), width=2)
    d.text((76, 305), "ABYSS BOT  /  DESIGN PREVIEW", font=small, fill=(244, 228, 250))
    buffer = io.BytesIO()
    im.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

def make_preview(group="hanayo"):
    creator = "하나요 히요리" if group == "hanayo" else "사키하네 후야"
    handle = "@HanayoHiyori" if group == "hanayo" else "@Sakihanechannel"
    card = alert("x", creator, get_banner(group), "", title="[테스트] 이미지 카드 + V2",
                 body="메리와 합방 있어요!\n\n원문과 작은 부제목, 날짜, 버튼은 기존 V2 방식으로 남습니다.",
                 date=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                 url="https://x.com/HanayoHiyori" if group == "hanayo" else "https://x.com/SakihaneHuya",
                 handle=handle, ping=False)
    container = card["components"][0]["components"]
    container.insert(0, {"type": 12, "items": [{"media": {"url": "attachment://abyss_preview.png"},
                                                "description": "어비스봇 시험용 이미지 카드"}]})
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
        print(json.dumps({"payload": payload, "png_bytes": len(image)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
