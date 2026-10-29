"""Manual notification test. Dry-run by default; --send posts one safe test embed."""
import argparse
import json
import os
from dotenv import load_dotenv
from banner_manager import get_banner
from creator_groups import role_for, HANAYO, SAKIHANE
from resilience import send_webhook

def make_payload(group, platform, ping=False):
    role = role_for(group)
    color = {"chzzk": 0x00FFA3, "youtube": 0xFF0000,
             "x": 0x1DA1F2, "cafe": 0x03C75A}[platform]
    embeds = []
    banner = get_banner(group)
    if banner.startswith("https://"):
        embeds.append({"color": color, "image": {"url": banner}})
    body = {"title": "[테스트] " + platform.upper() + " 알림",
            "description": "어비스봇 알림 디자인·그룹·배너 확인용 테스트입니다. 실제 방송/게시글이 아닙니다.",
            "color": color, "footer": {"text": "ABYSS BOT · TEST ONLY"}}
    if platform in ("chzzk", "youtube"):
        body["image"] = {"url": "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg"}
    embeds.append(body)
    return {"username": "어비스봇 테스트",
            "content": f"<@&{role}>" if ping else f"[테스트] {group} / 역할 ID: {role} (멘션 비활성화)",
            "embeds": embeds,
            "allowed_mentions": {"parse": [], "roles": [role] if ping else []}}

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
