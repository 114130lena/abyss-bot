"""Cross-provider X repost delivery idempotency.

FxTwitter may show an old original as an off-author repost while Nitter
shows a recent creator repost wrapper with a different status ID.
The claimed identity must cover BOTH forms, not just an X status ID.
"""
import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
LEDGER = ROOT / "x_repost_delivery_ledger.json"
LOCK = ROOT / "x_repost_delivery_ledger.lock"
KEEP_DAYS = 90
MAX_RECORDS = 4000
USER = re.compile(r"^\s*RT\s+@([A-Za-z0-9_]{1,15})\s*:\s*", re.I)
STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/)?$")
UNAVAILABLE = re.compile(r"(?im)^\s*(?:This post is unavailable|This Tweet is unavailable)\s*$")


def _original_status_from_url(group, post):
    """Identify a trusted off-creator ORIGINAL status, including Nitter links.

    The RSS repost wrapper can have a creator-owned ID whereas FxTwitter lists
    the original author's ID. Both should claim one stable original identity.
    """
    owner = {"hanayo": "hanayohiyori", "sakihane": "sakihanehuya"}.get(group)
    if not owner:
        return None
    for field in ("source_link", "url"):
        url = urlsplit(str(post.get(field) or ""))
        host = (url.hostname or "").lower()
        if url.scheme not in ("https", "http") or not (
                host in ("x.com", "www.x.com", "twitter.com", "www.twitter.com",
                         "fxtwitter.com", "www.fxtwitter.com")
                or host.startswith("nitter.")):
            continue
        match = STATUS.fullmatch(url.path)
        if match and match.group(1).lower() != owner:
            return match.group(1).lower(), match.group(2)
    return None

def fingerprint(group, post):
    """Return a stable content identity for known original artist reposts.

    Reject short/anonymous text fingerprints; exact post ID remains a fallback
    rather than collapsing unrelated status updates.
    """
    body = str(post.get("text") or "").replace("\r", "\n")
    title = str(post.get("rss_title") or "")
    match = USER.match(body) or USER.match(title)
    # The RSS repost wrapper includes RT @artist:. Fx timeline instead points
    # directly to artist/status/original_id without a wrapper.
    origin = _original_status_from_url(group, post)
    artist = match.group(1).lower() if match else (origin[0] if origin else "")
    creator = "hanayohiyori" if group == "hanayo" else "sakihanehuya"
    if artist == creator:
        artist = ""
    body = USER.sub("", body, count=1)
    body = UNAVAILABLE.sub("", body)
    body = re.sub(r"\s+", " ", body).strip()
    # Public RSS feeds can add an invisible unsupported-content line.
    # A substantive body and an identified original artist are both mandatory.
    if not artist or len(body) < 45:
        return None
    digest = hashlib.sha256((group + "\0" + artist + "\0" + body).encode("utf-8")).hexdigest()
    return "body:" + digest


def _post_key(group, post):
    sid = str(post.get("id") or "")
    return "status:" + group + ":" + sid if sid.isdigit() else None


def _keys(group, post):
    result = [_post_key(group, post)]
    # Cross-provider identity must not depend on exact RSS text or length.
    origin = _original_status_from_url(group, post)
    if origin:
        result.append("original:" + group + ":" + origin[0] + ":" + origin[1])
    # Preserve existing body keys for historical ledgers and wrappers that
    # expose only the creator's repost ID and the original artist's handle.
    result.append(fingerprint(group, post))
    return tuple(x for x in result if x)


def _load():
    try:
        state = json.loads(LEDGER.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Invalid delivery ledger shape")
        return state
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        # Invalid/unreadable dedup history is NOT evidence nothing was sent.
        # Never reset to empty: that would blast already-delivered reposts.
        logging.error("X_REPOST_LEDGER_UNREADABLE reason=%s; delivery blocked",
                      type(exc).__name__)
        raise ValueError("X repost delivery ledger unreadable") from exc


def _store(state):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    # On failure, never release a claim that might already have reached Discord.
    fd, name = tempfile.mkstemp(prefix=".x_repost_ledger_", dir=str(LEDGER.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, LEDGER)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _with_lock(func):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return func()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def reserve(group, post, now=None):
    """Atomic pre-send reservation across multiple workers and restarts.

    A pending record is intentionally NOT retried without reconciliation:
    a crashed sender might already have posted the message.
    """
    keys = _keys(group, post)
    if not keys:
        logging.warning("X_REPOST_DEDUP_UNKEYED; suppressing unsafe delivery")
        return False
    now = now or datetime.now(timezone.utc)
    def action():
        state = _load()
        for key in keys:
            if key in state:
                logging.warning("X_REPOST_DUPLICATE_SUPPRESSED group=%s id=%s key_type=%s previous=%s",
                                group, str(post.get("id") or "")[:22],
                                key.split(":", 1)[0], state[key].get("status", "unknown"))
                return False
        # Retain pending records indefinitely until manually reconciled; only
        # completed deliveries can be aged out.
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        state = {k: v for k, v in state.items()
                 if isinstance(v, dict) and (v.get("status") != "sent"
                                             or v.get("time", "") >= cutoff)}
        record = {"time": now.isoformat(), "status": "pending",
                  "post_id": str(post.get("id") or "")[:24]}
        for key in keys:
            state[key] = record
        if len(state) > MAX_RECORDS:
            # Keep all pending claims plus the most recently completed records.
            pending = [(k,v) for k,v in state.items() if v.get("status") != "sent"]
            sent = sorted(((k,v) for k,v in state.items() if v.get("status") == "sent"),
                          key=lambda kv: kv[1].get("time", ""), reverse=True)
            state = dict(pending + sent[:max(0, MAX_RECORDS-len(pending))])
        _store(state)
        return True
    return _with_lock(action)


def mark_sent(group, post, now=None):
    keys = _keys(group, post)
    now = now or datetime.now(timezone.utc)
    def action():
        state = _load()
        for key in keys:
            if key in state:
                state[key] = {**state[key], "status": "sent",
                              "time": now.isoformat()}
        _store(state)
    _with_lock(action)


def mark_uncertain(group, post, reason):
    keys = _keys(group, post)
    def action():
        state = _load()
        for key in keys:
            if key in state:
                state[key] = {**state[key], "status": "needs_review",
                              "reason": str(reason)[:48]}
        _store(state)
    _with_lock(action)
