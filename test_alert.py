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
    """Isolated premium preview: never used by production notification workers."""
    from discord_v2 import FLAGS, COLORS, text, gallery, separator
    display = "하나요 히요리" if group == HANAYO else "사키하네 후야"
    banner = get_banner(group)
    if platform == "chzzk":
        # Test-only design. No actual broadcast is announced and mentions are disabled.
        creator = display
        title = "바츠가게 령? 방송"
        test_url = "https://chzzk.naver.com/"
        parts = []
        if banner and banner.startswith("https://"):
            parts.append(gallery(banner, "스트리머 공식 배너"))
        parts.append(text("🟢 **CHZZK** · " + creator + " · **LIVE**"))
        parts.append(separator())
        parts.append(text("## [" + title + "](" + test_url + ")"))
        parts.append(gallery("https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg",
                             "테스트용 방송 썸네일"))
        parts.append(separator())
        parts.append(text("Just Chatting · 2026.10.29 21:30 (테스트 날짜)"))
        return {"username": "정덕삼", "flags": FLAGS,
                "components": [{"type": 17, "accent_color": COLORS["chzzk"],
                                "components": parts}],
                "allowed_mentions": {"parse": []}}
    if platform == "x":
        # Preview only: sample text/images, never a real X post.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        parts = []
        if banner and banner.startswith("https://"):
            parts.append(gallery(banner, "스트리머 공식 배너"))
        parts.append(text("𝕏 **X** · " + display + " · **NEW POST**"))
        parts.append(separator())
        parts.append(text("오늘 새로운 일러스트가 나왔어요! 💕\\n"
                          "너무 예쁘게 그려주셔서 감사합니다!\\n"
                          "여러분도 꼭 봐주세요. (테스트 본문)"))
        examples = [
            "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg",
            "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
            "https://i.ytimg.com/vi/aqz-KE-bpKQ/mqdefault.jpg",
            "https://i.ytimg.com/vi/jNQXAC9IVRw/mqdefault.jpg",
        ]
        if image_count:
            parts.append({"type": 12, "items": [
                {"media": {"url": url}, "description": "테스트용 첨부 이미지"}
                for url in examples[:image_count]
            ]})
        parts.append(separator())
        stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d %H:%M")
        parts.append(text("게시글 작성 · " + stamp + " (테스트 시간)\\n"
                          "[원문 보기 (테스트)](https://x.com/HanayoHiyori)"))
        return {"username": "정덕삼", "flags": FLAGS,
                "components": [{"type": 17, "accent_color": COLORS["x"],
                                "components": parts}],
                "allowed_mentions": {"parse": []}}
    name = display + " · " + ("CHZZK REPLAY" if platform == "replay" else platform.upper())
    configs = {
        "chzzk": ("🔴 LIVE  ·  CHZZK", "방송이 시작됐어요!", "오늘도 함께 즐기는 방송 👻",
                  "Just Chatting", "방송 시청하기"),
        "youtube": ("▶ NEW VIDEO  ·  YOUTUBE", "새 영상이 공개됐어요!", "하이라이트 영상이 올라왔습니다.",
                    "", "영상 보러 가기"),
        "x": ("✦ NEW POST  ·  X", "새로운 소식이 도착했어요.",
              "오늘도 즐거운 하루 보내세요! 👻\n이따 방송에서 만나요!", "",
              "게시글 확인하기"),
        "cafe": ("✎ NEW ARTICLE  ·  NAVER CAFE", "방송 일정 안내",
                 "오늘 방송은 저녁에 진행됩니다.\n자세한 내용은 카페에서 확인해 주세요.", display,
                 "게시글 보기"),
        "replay": ("▸ NEW VOD  ·  CHZZK", "놓친 방송을 다시 만나보세요!",
                   "오늘의 방송 다시보기", "", "다시보기 시청하기"),
    }
    label, heading, description, extra, action = configs[platform]
    color_platform = "chzzk" if platform == "replay" else platform
    parts = []
    if banner and banner.startswith("https://"):
        parts.append(gallery(banner, "스트리머 공식 배너"))
    parts.append(text("**" + label + "**\n" + display))
    parts.append(separator())
    parts.append(text("**" + heading + "**"))
    if platform in ("chzzk", "youtube", "replay"):
        parts.append(gallery("https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "테스트용 썸네일"))
        parts.append(text("**" + description + "**" + ("\n" + extra if extra else "")))
    else:
        parts.append(text("\n".join("> " + line for line in description.splitlines())))
        if extra:
            parts.append(text("작성자 · " + extra))
    parts.append(separator())
    parts.append(text("🔗 " + action + "  ·  테스트 알림"))
    return {"username": "정덕삼", "flags": FLAGS,
            "components": [{"type": 17, "accent_color": COLORS[color_platform], "components": parts}],
            "allowed_mentions": {"parse": []}}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=[HANAYO, SAKIHANE], default=HANAYO)
    parser.add_argument("--platform", choices=["chzzk", "youtube", "x", "cafe", "replay"], default="chzzk")
    parser.add_argument("--send", action="store_true", help="Post one clearly labeled test message")
    parser.add_argument("--premium", action="store_true", help="Preview the premium design in test messages only")
    parser.add_argument("--images", type=int, choices=[0, 1, 2, 4], default=2,\n                        help="Number of sample X attachments in premium preview")
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
