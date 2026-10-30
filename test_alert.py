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
                      username="정덕삼", ping=False)


def make_premium_payload(group, platform, image_count=2):
    """Preview the production card design with fake content and no mentions."""
    from discord_v2 import alert as unified_alert
    from creator_profiles import handle, avatar
    from datetime import datetime
    from zoneinfo import ZoneInfo
    display = "하나요 히요리" if group == HANAYO else "사키하네 후야"
    kind = "chzzk" if platform == "replay" else platform
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    sample_image = "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg"
    images = [
        "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg",
        "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
        "https://i.ytimg.com/vi/aqz-KE-bpKQ/mqdefault.jpg",
        "https://i.ytimg.com/vi/jNQXAC9IVRw/mqdefault.jpg",
    ][:image_count] if kind == "x" else []
    urls = {
        "chzzk": "https://chzzk.naver.com/",
        "youtube": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        "x": "https://x.com/HanayoHiyori",
        "cafe": "https://cafe.naver.com/",
    }
    return unified_alert(
        kind, display, get_banner(group),
        title="" if kind == "x" else "[테스트] " + ("다시보기" if platform == "replay" else "방송 일정 안내"),
        body="오늘 새로운 일러스트가 나왔어요! 💕\n\n너무 예쁘게 그려주셔서 감사합니다!" if kind == "x" else
             "테스트 공지입니다." if kind == "cafe" else "",
        url=urls[kind], media_url=sample_image if kind in ("chzzk", "youtube") else "",
        images=images, category="Just Chatting" if kind == "chzzk" and platform != "replay" else "",
        date=stamp, replay=platform == "replay", ping=False,
        handle=handle(group), avatar=avatar(group))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=[HANAYO, SAKIHANE], default=HANAYO)
    parser.add_argument("--platform", choices=["chzzk", "youtube", "x", "cafe", "replay"], default="chzzk")
    parser.add_argument("--send", action="store_true", help="Post one clearly labeled test message")
    parser.add_argument("--premium", action="store_true", help="Preview the premium design in test messages only")
    parser.add_argument("--images", type=int, choices=[0, 1, 2, 4], default=2,
                        help="Number of sample X attachments in premium preview")
    args = parser.parse_args()
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    payload = make_premium_payload(args.group, args.platform, image_count=args.images) if args.premium else make_payload(args.group, args.platform)
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
