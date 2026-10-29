"""Manual notification test. Dry-run by default; --send posts one safe test embed."""
import argparse
import json
import os
from dotenv import load_dotenv
from banner_manager import get_banner
from creator_groups import role_for, HANAYO, SAKIHANE
from resilience import send_webhook
from discord_v2 import payload as v2_payload

def make_payload(group, platform, ping=False):
    role = role_for(group)
    banner = get_banner(group)
    media = "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg" if platform in ("chzzk", "youtube") else ""
    return v2_payload(platform, role, banner,
                      "[테스트] " + platform.upper() + " 알림", group,
                      "알림 레이아웃·배너·역할 확인용입니다. 실제 방송이나 게시글이 아닙니다.",
                      media_url=media, media_description="테스트용 썸네일",
                      username="어비스봇 테스트", ping=ping)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=[HANAYO, SAKIHANE], default=HANAYO)
    parser.add_argument("--platform", choices=["chzzk", "youtube", "x", "cafe"], default="chzzk")
    parser.add_argument("--send", action="store_true", help="Post one clearly labeled test message")
    parser.add_argument("--ping", action="store_true", help="Actually mention the group role (requires --send)")
    args = parser.parse_args()
    if args.ping and not args.send:
        parser.error("--ping requires --send")
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    payload = make_payload(args.group, args.platform, args.ping)
    if args.send:
        target = os.getenv("TEST_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")
        send_webhook(target, payload)
        print("Test message posted. Mention enabled:", args.ping)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
