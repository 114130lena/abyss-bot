"""Manual notification test. Never send role mentions, including with --send."""
import argparse
import json
import os
from dotenv import load_dotenv
from banner_manager import get_banner
from creator_groups import role_for, HANAYO, SAKIHANE
from resilience import send_webhook
from discord_v2 import payload as v2_payload
from chzzk_replay_notifier import thread_webhook_url
from youtube_notifier import youtube_thread_webhook_url

def make_payload(group, platform):
    role = "" if platform == "replay" else role_for(group)
    banner = get_banner(group)
    media = "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg" if platform in ("chzzk", "youtube") else ""
    return v2_payload("chzzk" if platform == "replay" else platform, role, banner,
                      "[테스트] " + platform.upper() + " 알림", group,
                      "알림 레이아웃·배너·역할 확인용입니다. 실제 방송이나 게시글이 아닙니다.",
                      media_url=media, media_description="테스트용 썸네일",
                      username="어비스봇 테스트", ping=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=[HANAYO, SAKIHANE], default=HANAYO)
    parser.add_argument("--platform", choices=["chzzk", "youtube", "x", "cafe", "replay"], default="chzzk")
    parser.add_argument("--send", action="store_true", help="Post one clearly labeled test message")
    args = parser.parse_args()
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    payload = make_payload(args.group, args.platform)
    if args.send:
        target = os.getenv("TEST_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")
        if args.platform == "replay":
            target = thread_webhook_url()
        elif args.platform == "youtube":
            target = youtube_thread_webhook_url()
        send_webhook(target, payload)
        print("Test message posted. Role mentions are disabled.")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
