"""One-shot Discord test-thread preview of a real three-photo X fanart post.

Uses the PR's actual fanart_url_media.apply() footer formatter.
Never changes the production bot, fanart cache, or live notification channel.
"""
import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from dotenv import load_dotenv

TEST_THREAD_ID = "1566335914500816988"
POST_ID = "2116843682917961768"
AUTHOR = "Haayung00"
POST_URL = f"https://x.com/{AUTHOR}/status/{POST_ID}"
SOURCE = f"https://api.fxtwitter.com/{AUTHOR}/status/{POST_ID}"


def source_images():
    """Retrieve this *specific* post. Fail closed if original photos cannot be confirmed."""
    response = requests.get(SOURCE, timeout=18, headers={"User-Agent": "abyss-bot/1.0"})
    response.raise_for_status()
    data = response.json()
    tweet = data.get("tweet") if isinstance(data, dict) else None
    photos = (tweet.get("media") or {}).get("photos") if isinstance(tweet, dict) else None
    if not isinstance(photos, list):
        raise RuntimeError("X 원본 게시글의 사진 목록을 확인할 수 없습니다")
    found = []
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        url = photo.get("url") or photo.get("media_url_https")
        p = urlsplit(str(url or ""))
        if p.scheme == "https" and p.hostname == "pbs.twimg.com" and p.path.startswith("/media/") and url not in found:
            found.append(url)
    if len(found) != 3:
        raise RuntimeError(f"대상 게시글 사진 3장이 검증되지 않았습니다 (확인: {len(found)}장)")
    return found


def preview(images):
    # Test production fanart_url_media.apply() with a controlled footer signature.
    from unittest.mock import patch
    import fanart_url_media
    sample = {"flags": 32768, "username": "어비스봇 · 팬아트 3장 테스트",
              "allowed_mentions": {"parse": []},
              "components": [{"type": 17, "accent_color": 8791748, "components": [
                  {"type": 9, "components": [{"type": 10,
                   "content": "## 사키하네 후야 SAKIHANE HUYA\\n-# @SakihaneHuya"}],
                   "accessory": {"type": 11,
                                 "media": {"url": "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg"}}},
                  {"type": 14, "divider": True, "spacing": 1},
                  {"type": 10, "content": "**[테스트] 팬아트 게시글 3장 갤러리 · 멘션 비활성화**"},
                  {"type": 10, "content": "-# 실제 X 게시글의 사진 3장 전부를 하나의 세트로 출력합니다."},
                  {"type": 10, "content": "-# 공식 채널 · OFFICIAL LINKS"},
                  {"type": 12, "items": [{"media": {
                      "url": "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg"}}]},
                  {"type": 1, "components": [
                      {"type": 2, "style": 5, "label": label, "url": url}
                      for label, url in (
                          ("CHZZK", "https://chzzk.naver.com/"),
                          ("CAFE", "https://cafe.naver.com/f-e/cafes/29424353"),
                          ("X", "https://x.com/SakihaneHuya"),
                          ("YouTube", "https://www.youtube.com/@Sakihanechannel"),
                      )
                  ]},
              ]}]}
    chosen_post = {"artist": "@Haayung00", "post_url": POST_URL, "images": images}
    with patch.object(fanart_url_media, "candidates", return_value=[chosen_post]), \
         patch.object(fanart_url_media, "read", return_value={}):
        changed, chosen = fanart_url_media.apply(sample)
    if not chosen:
        raise RuntimeError("fanart_url_media.apply returned no gallery")
    parts = changed["components"][0]["components"]
    gallery = next((part for part in parts if part.get("type") == 12), None)
    if not gallery or [item["media"]["url"] for item in gallery["items"]] != images:
        raise RuntimeError("3장의 팬아트 갤러리 검증 실패")
    row = next((part for part in parts if part.get("type") == 1), None)
    if not row or [button["label"] for button in row["components"]] != [
            "원본 게시물", "CHZZK", "CAFE", "X", "YouTube"]:
        raise RuntimeError("5개 원본/플랫폼 버튼 검증 실패")
    return changed


def main():
    load_dotenv("/home/ubuntu/chzzk-bot/.env")
    target = os.getenv("TEST_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
    if not target:
        raise RuntimeError("No existing webhook configured on the server")
    images = source_images()
    payload = preview(images)
    parsed = urlsplit(target)
    if parsed.scheme != "https" or parsed.hostname not in ("discord.com", "discordapp.com"):
        raise RuntimeError("Invalid Discord webhook host")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"thread_id": TEST_THREAD_ID, "with_components": "true", "wait": "true"})
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    response = requests.post(endpoint, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    message_id = str(result.get("id", ""))
    if not message_id.isdigit():
        raise RuntimeError("Discord did not return a message ID")
    print(json.dumps({"status": "sent", "thread_id": TEST_THREAD_ID,
                      "message_id": message_id, "image_count": len(images),
                      "url": f"https://discord.com/channels/@me/{TEST_THREAD_ID}/{message_id}"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
