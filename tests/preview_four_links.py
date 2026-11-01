"""Safe Discord Components V2 four-link footer payload preview.

No Discord webhook calls. Run: python3 tests/preview_four_links.py
"""
import json

def footer(thumb, youtube, x, chzzk, cafe):
    links = [
        ("YouTube", youtube),
        ("𝕏 Twitter", x),
        ("CHZZK", chzzk),
        ("스텔라이브 카페", cafe),
    ]
    assert all(u.startswith("https://") for _, u in links)
    components = []
    if thumb and thumb.startswith("https://"):
        components.append({
            "type": 9,
            "components": [{"type": 10, "content": "**OFFICIAL CHANNELS**\n-# 공식 채널 바로가기"}],
            "accessory": {"type": 11, "media": {"url": thumb},
                          "description": "공식 채널 이미지"}
        })
    else:
        components.append({"type": 10, "content": "**OFFICIAL CHANNELS**"})
    components.append({"type": 1, "components": [
        {"type": 2, "style": 5, "label": name, "url": url} for name, url in links
    ]})
    return components

def demo():
    components = [
        {"type": 10, "content": "## 사키하네 후야\n💜 H U Y A"},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "**🟢 CHZZK · LIVE**"},
        *footer(
            "https://www.youtube.com/favicon.ico",
            "https://www.youtube.com/@Sakihanechannel",
            "https://x.com/SakihaneHuya",
            "https://chzzk.naver.com/36ddb9bb4f17593b60f1b63cec86611d",
            "https://cafe.naver.com/f-e/cafes/29424353",
        ),
    ]
    payload = {
        "username": "어비스봇 · 테스트",
        "flags": 1 << 15,
        "components": [{
            "type": 17, "accent_color": 0x00FFA3, "components": components
        }],
        "allowed_mentions": {"parse": []},
    }
    assert len(components) <= 10
    assert len(components[-1]["components"]) == 4
    assert all(b["style"] == 5 for b in components[-1]["components"])
    assert all(b["type"] == 2 for b in components[-1]["components"])
    assert len({b["label"] for b in components[-1]["components"]}) == 4
    assert payload["flags"] == 32768
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("PASS: V2 structure, four equal-height link buttons, no mentions")

if __name__ == "__main__":
    demo()
