"""Read-only operator summary of existing Abyss health data.

This script does not fetch private posts, publish Discord messages, mutate bot
state, or change the banner/clip 08:00 and 18:00 KST schedule.
Run: python -m scripts.ops_status_card [--json]
"""
import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.operational_audit import source_check_summary, component_check_summary

KST = ZoneInfo("Asia/Seoul")
GROUPS = (("hanayo", "히요리"), ("sakihane", "후야"))
STATUS = {
    "ok": "정상", "partial": "일부 성공", "unavailable": "이용 불가",
    "stale": "수집 지연", "retrying": "재시도 중",
    "repeated_failures": "연속 실패", "stale_success": "성공 기록 지연",
    "unverified": "미확인", "no_success_record": "성공 기록 없음",
}


def _text(value, fallback="미확인"):
    if value is None or value == "":
        return fallback
    return str(value)


def render(sources, components, now=None):
    """Only render whitelisted aggregated metrics, never raw error/post data."""
    moment = (now or datetime.now(KST)).astimezone(KST)
    fanart = components.get("fanart", {})
    groups = fanart.get("groups", {})
    banner = components.get("banner", {})
    clip = components.get("clip", {})
    lines = [
        "**ABYSS BOT · 운영 상태**",
        "확인: " + moment.strftime("%Y-%m-%d %H:%M KST"),
        "",
        "**플랫폼 수집 상태**",
    ]
    for name in ("네이버 카페", "히요리 X", "후야 X"):
        item = sources.get(name, {})
        lines.append("- " + name + ": " + STATUS.get(item.get("status"), "미확인")
                     + " · 마지막 성공 " + _text(item.get("last_success_age_minutes")) + "분 전")
    lines += ["", "**팬아트 수집**"]
    for key, label in GROUPS:
        item = groups.get(key, {})
        lines.append(
            "- " + label + ": 게시물 " + _text(item.get("posts"), "0")
            + " · 캐시 " + _text(item.get("verified_cached_images"), "0")
            + " · 누락 " + _text(item.get("missing_cached_images"), "0")
            + " · 전체 검색 " + STATUS.get(item.get("hashtag_source_status"), "미확인")
            + " · 작가별 대체 " + STATUS.get(item.get("artist_fallback_status"), "미확인")
        )
    lines += [
        "", "**서버 배너**",
        "- 사용 가능한 미사용 후보: " + _text(banner.get("unused_candidates"))
        + " / 총 " + _text(banner.get("total_candidates")),
        "- 비전 평가: " + ("준비됨" if banner.get("vision_ready") else
                         "미구성/사용 불가"),
        "- 갱신: 매일 08:00 / 18:00 KST",
        "", "**오늘의 히요리 클립**",
        "- 수집된 클립: " + _text(clip.get("cached_clips"), "0")
        + " · 마지막 수집 " + _text(clip.get("collected_age_hours")) + "시간 전",
        "- 게시: 매일 08:00 / 18:00 KST · 수집: 2시간 간격",
        "", "**전송 검토**",
        "- 전송 결과 확인 필요: " + _text(components.get("discord_uncertain_deliveries"), "0")
        + "건 (자동 재발송하지 않음)",
        "- HTTP 전송 시간: 평균 " + _text(components.get("delivery_latency", {}).get("average_seconds")) + "초"
        + " · 최대 " + _text(components.get("delivery_latency", {}).get("max_seconds")) + "초",
        "- 상태 경고: " + ", ".join(components.get("warning_codes") or ["없음"]),
    ]
    return "\n".join(lines)


def snapshot():
    from delivery_latency import summary as latency_summary
    components = component_check_summary()
    components["delivery_latency"] = latency_summary()
    return {"sources": source_check_summary(), "components": components}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit sanitized aggregate JSON")
    args = parser.parse_args()
    report = snapshot()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report["sources"], report["components"]))


if __name__ == "__main__":
    main()
