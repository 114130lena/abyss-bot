"""Discord-signed, opt-in banner bookmark interaction receiver.

Configure Discord Interactions Endpoint URL to /discord/banner-bookmark,
reverse-proxied to localhost:BANNER_BOOKMARK_PORT. No bot gateway needed.
"""
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

PATH = "/discord/banner-bookmark"
PORT = int(os.getenv("BANNER_BOOKMARK_PORT", "8767"))
MAX_BODY = 16384


def process(body, signature, timestamp):
    public_key = os.getenv("DISCORD_APPLICATION_PUBLIC_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", public_key):
        return 503, {"error": "interaction_not_configured"}
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            timestamp.encode("ascii") + body, bytes.fromhex(signature))
    except (ValueError, UnicodeError, BadSignatureError):
        return 401, {"error": "invalid_signature"}
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return 400, {"error": "invalid_payload"}
    if data.get("type") == 1:
        return 200, {"type": 1}
    if data.get("type") in (3, 5) and str((data.get("data") or {}).get("custom_id") or "").startswith("lp:"):
        if str(data.get("guild_id")) != os.getenv("DISCORD_GUILD_ID") or str(data.get("channel_id")) != "1575809466576932904":
            return 200, {"type": 4, "data": {"content": "전용 채널에서만 이용 가능합니다.", "flags": 64}}
        if abs(time.time() - int(timestamp)) > 60:
            return 401, {"error": "stale_signature"}
        req = urllib.request.Request("http://127.0.0.1:8768/interactions", data=body, headers={
            "Content-Type": "application/json", "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1) as response:
                if response.status == 202:
                    return 202, None
        except (OSError, urllib.error.URLError):
            pass
        return 200, {"type": 4, "data": {"content": "통나무 파워 서비스가 현재 연결되지 않았습니다.", "flags": 64}}
    if data.get("type") != 3:
        return 200, {"type": 4, "data": {"content": "지원하지 않는 요청입니다.", "flags": 64}}
    component = data.get("data") or {}
    identifier = component.get("custom_id") or ""
    guild = str(data.get("guild_id") or "")
    member = data.get("member") or {}
    actor = str((member.get("user") or {}).get("id") or "")
    permissions = member.get("permissions") or "0"
    configured_guild = os.getenv("DISCORD_GUILD_ID", "")
    if (guild != configured_guild or not guild.isdigit()
            or not actor.isdigit() or not str(permissions).isdigit()
            or not (int(permissions) & 0x20)):
        return 200, {"type": 4, "data": {"content": "서버 관리 권한이 필요합니다.", "flags": 64}}
    from guild_server_banner import read_state
    from banner_bookmarks import keep_current
    state = read_state()
    current_hash = str(state.get("guild_banner_hash") or "")
    if (not identifier.startswith("abyss:banner:keep:")
            or identifier[len("abyss:banner:keep:"):] != current_hash
            or not current_hash):
        message = "배너가 변경됐습니다. 최신 배너 정보에서 다시 눌러주세요."
    else:
        result = keep_current(state, actor)
        message = {"saved": "배너를 보관함에 저장했습니다.",
                   "already_saved": "이미 보관된 배너입니다.",
                   "unverified_source": "원본 출처가 확인되지 않아 저장하지 않았습니다."}.get(
                       result["status"], "저장할 수 없습니다.")
    return 200, {"type": 4, "data": {"content": message, "flags": 64}}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if urlparse(self.path).path != PATH:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 1 or length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        try:
            status, payload = process(
                body, self.headers.get("X-Signature-Ed25519", ""),
                self.headers.get("X-Signature-Timestamp", ""))
        except Exception:
            logging.exception("BANNER_BOOKMARK_INTERACTION_FAILED")
            status, payload = 500, {"error": "internal_error"}
        encoded = b"" if status == 202 else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        logging.info("Banner interaction: " + format, *args)


def start():
    if (os.getenv("BANNER_BOOKMARK_INTERACTIONS_ENABLED") != "1"
            or not re.fullmatch(r"[0-9a-fA-F]{64}",
                                os.getenv("DISCORD_APPLICATION_PUBLIC_KEY", ""))):
        logging.info("Banner bookmark interactions disabled or not configured")
        return
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    start()
