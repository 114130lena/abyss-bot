"""Opt-in YouTube WebSub receiver. Requires a public HTTPS reverse proxy.

Set YOUTUBE_WEBSUB_CALLBACK_URL=https://your-domain.example/youtube/websub
and YOUTUBE_WEBSUB_SECRET=<random-long-string>. Proxy that path to 127.0.0.1:8765.
RSS polling remains active independently.
"""
import hashlib
import hmac
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from youtube_notifier import load_channel_ids, receive_websub_feed

CALLBACK = os.getenv("YOUTUBE_WEBSUB_CALLBACK_URL", "").strip()
SECRET = os.getenv("YOUTUBE_WEBSUB_SECRET", "").encode()
PORT = int(os.getenv("YOUTUBE_WEBSUB_PORT", "8765"))
HUB = "https://pubsubhubbub.appspot.com/subscribe"
LEASE = 432000

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.info("WebSub HTTP: " + format, *args)

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        mode = params.get("hub.mode", [""])[0]
        topic = params.get("hub.topic", [""])[0]
        challenge = params.get("hub.challenge", [""])[0]
        topic_id = parse_qs(urlparse(topic).query).get("channel_id", [""])[0]
        expected_topic = "https://www.youtube.com/xml/feeds/videos.xml?channel_id=" + topic_id
        if (urlparse(self.path).path != urlparse(CALLBACK).path
            or mode not in ("subscribe", "unsubscribe")
            or topic != expected_topic
            or topic_id not in load_channel_ids()
            or not challenge):
            self.send_error(400)
            return
        data = challenge.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlparse(self.path).path != urlparse(CALLBACK).path:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 1 or length > 1024 * 1024:
            self.send_error(413)
            return
        data = self.rfile.read(length)
        # YouTube's hub may use either SHA-1 or SHA-256 signing.
        valid = False
        for algorithm, digest in (("sha1", hashlib.sha1), ("sha256", hashlib.sha256)):
            signature = self.headers.get("X-Hub-Signature" if algorithm == "sha1" else "X-Hub-Signature-256", "")
            if hmac.compare_digest(signature, algorithm + "=" + hmac.new(SECRET, data, digest).hexdigest()):
                valid = True
        if not valid:
            self.send_error(403)
            return
        try:
            receive_websub_feed(data)
        except Exception:
            logging.exception("WebSub update processing failed")
            self.send_error(500)
            return
        self.send_response(204)
        self.end_headers()


def renew_subscriptions():
    """Renew at 24-hour intervals; each subscription requests a 5-day lease."""
    while True:
        for channel in load_channel_ids():
            topic = "https://www.youtube.com/xml/feeds/videos.xml?channel_id=" + channel
            try:
                response = requests.post(HUB, data={
                    "hub.callback": CALLBACK, "hub.topic": topic,
                    "hub.mode": "subscribe",
                    "hub.verify": "async",
                    "hub.secret": SECRET.decode(),
                    "hub.lease_seconds": str(LEASE),
                }, timeout=20)
                response.raise_for_status()
                logging.info("YouTube WebSub subscription requested: %s", channel)
            except Exception as exc:
                logging.warning("YouTube WebSub subscription failed for %s: %s", channel, exc)
        time.sleep(86400)


def start_websub():
    if not CALLBACK or not SECRET:
        logging.info("YouTube WebSub disabled: HTTPS callback and secret not configured; RSS remains active")
        return
    parsed = urlparse(CALLBACK)
    if parsed.scheme != "https" or not parsed.netloc or len(SECRET) < 24:
        logging.error("YouTube WebSub disabled: invalid public HTTPS URL or weak secret")
        return
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, name="youtube-websub-http", daemon=True).start()
    threading.Thread(target=renew_subscriptions, name="youtube-websub-renew", daemon=True).start()
    logging.info("YouTube WebSub listening on localhost:%s", PORT)
