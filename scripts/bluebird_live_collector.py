"""Bluebird/Twscraper -> Abyss X bridge, without any Discord credentials.

Runs only in an isolated Docker container. Local session cookies never leave
/private and are never added to event snapshots or process arguments.
The first successful read of each creator *baselines* the timeline; it is NOT
a notification event. All later events are guarded by X event timestamps.
"""
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

USERS = ("HanayoHiyori", "SakihaneHuya")
COOKIE_FILE = Path("/private/x.cookies")
DB_FILE = Path("/private/twscraper_live.db")
STATE_FILE = Path("/private/bluebird_live_state.json")
SNAPSHOT_FILE = Path("/results/bluebird_bridge_events.json")
STOP = False
EVENT_MAX_AGE = 7200
MAX_SEEN = 5000
MAX_EVENTS = 250


def _stop(_signum, _frame):
    global STOP
    STOP = True


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "users": {}}
    # An unreadable checkpoint may represent previously delivered events.
    # Fail closed, never replay historical reposts after state loss/corruption.
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("users"), dict):
        raise ValueError("Bridge checkpoint invalid: refusing to reset baseline")
    return state


def _media(post):
    photos, video = [], ""
    for item in tuple(post.media or ()):
        url = str(getattr(item, "url", "") or "")
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            continue
        if parsed.hostname == "pbs.twimg.com" and parsed.path.startswith("/media/") and len(photos) < 4:
            if url not in photos:
                photos.append(url)
        elif parsed.hostname == "video.twimg.com" and parsed.path.lower().endswith((".mp4", ".webm")) and not video:
            video = url
    return photos, video


def _event(post, complete):
    username = str(post.username or "")
    post_id = str(post.post_id)
    if username.casefold() not in {name.casefold() for name in USERS} or not post_id.isdigit():
        return None
    if post.is_reply:
        return None
    repost = bool(post.is_repost)
    target = post.repost_of if repost else None
    if repost and (target is None or not str(target.post_id).isdigit()):
        return None
    owner_url = "https://x.com/{}/status/{}".format(username, post_id)
    target_url = (
        "https://x.com/{}/status/{}".format(target.username, target.post_id)
        if target is not None else owner_url
    )
    photos, video = _media(post)
    text = str(post.text or "")[:2800]
    title = "RT @{}: ".format(target.username) if target is not None else ""
    if title and text.casefold().startswith("rt @"):
        title = ""
    return {
        "id": post_id,
        "text": text,
        "url": owner_url,
        "source_link": target_url,
        "source": "bluebird-twscraper",
        "pub_date": datetime.fromtimestamp(int(post.created_at), timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "created_at": int(post.created_at),
        "images": photos,
        "bluebird_video_url": video,
        "bluebird_media_verified": bool(complete),
        "rss_title": (title + text[:140]) if repost else text[:140],
        "rss_description": "",
        "repost": repost,
        "reply": False,
    }


def _update(state, username, feed, now):
    key = username.casefold()
    account = state["users"].get(key)
    if account is not None and (
        not isinstance(account, dict)
        or not isinstance(account.get("seen"), dict)
        or not isinstance(account.get("events"), list)
    ):
        raise ValueError("Corrupt account checkpoint; refusing historical replay")
    first = account is None
    if first:
        account = {"seen": {}, "events": [], "cursor": None}
    seen, events = account["seen"], account["events"]
    posts = tuple(feed.posts or ())
    new_events = 0
    for post in sorted(posts, key=lambda p: (int(p.created_at), int(p.post_id))):
        if str(post.username).casefold() != key:
            continue
        event_id = str(post.post_id)
        if event_id in seen:
            continue
        seen[event_id] = int(post.created_at)
        age = now - int(post.created_at)
        # Never publish historical/too-new events. First scan *only* seeds.
        if first or age < -300 or age > EVENT_MAX_AGE:
            continue
        item = _event(post, bool(feed.complete))
        if item is not None:
            events.append(item)
            new_events += 1
    if len(seen) > MAX_SEEN:
        ids = sorted(seen, key=lambda key_id: (seen[key_id], int(key_id)))
        for old_id in ids[:-MAX_SEEN]:
            del seen[old_id]
    if posts and feed.complete:
        latest = max(
            (p for p in posts if str(p.username).casefold() == key),
            key=lambda p: (int(p.created_at), int(p.post_id)),
            default=None,
        )
        if latest is not None:
            account["cursor"] = {"created_at": int(latest.created_at), "post_id": str(latest.post_id)}
    events[:] = sorted(
        (x for x in events if 0 <= now - int(x["created_at"]) <= EVENT_MAX_AGE),
        key=lambda x: (x["created_at"], int(x["id"])),
    )[-MAX_EVENTS:]
    state["users"][key] = account
    return first, new_events


def _snapshot(state, now):
    users = {}
    for username in USERS:
        item = state["users"].get(username.casefold())
        if not item:
            return None
        users[username.casefold()] = list(item["events"])
    return {"version": 1, "updated_at": now, "users": users}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=90)
    args = parser.parse_args()
    if args.interval < 60 or args.interval > 1800:
        raise SystemExit("Safe interval range: 60-1800s")
    os.umask(0o077)
    os.environ["TWS_TELEMETRY"] = "0"
    from loguru import logger
    logger.remove()
    from core.twscraper import Twscraper, TwscraperRuntime, validate_cookies
    from core.service import ServiceCircuitBreaker
    from core.state import XCursor
    if not COOKIE_FILE.is_file():
        print("X_BRIDGE_DISABLED cookie_file_missing", flush=True)
        return 2
    cookies = COOKIE_FILE.read_text(encoding="utf-8").strip()
    try:
        validate_cookies(cookies)
    except Exception:
        print("X_BRIDGE_DISABLED cookie_names_invalid", flush=True)
        return 2
    try:
        state = _read_state()
    except (ValueError, OSError, json.JSONDecodeError):
        print("X_BRIDGE_DISABLED unreadable_checkpoint", flush=True)
        return 3
    runtime = TwscraperRuntime(DB_FILE)
    try:
        try:
            runtime.start(cookies)
        except Exception as exc:
            print("X_BRIDGE_DISABLED auth_init_error_type="+type(exc).__name__, flush=True)
            return 4
        finally:
            cookies = None
        source = Twscraper(ServiceCircuitBreaker("TwscraperBridge", 3, 1800, 10), runtime)
        source.retries = 0
        source.retry_delay = 0
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        print("X_BRIDGE_STARTED accounts=2 interval={} discord=false paid_api=false".format(args.interval), flush=True)
        while not STOP:
            cycle_started = time.monotonic()
            for username in USERS:
                if STOP:
                    break
                account = state["users"].get(username.casefold(), {})
                stored = account.get("cursor") if isinstance(account, dict) else None
                cursor = None
                if isinstance(stored, dict):
                    try:
                        cursor = XCursor(int(stored["created_at"]), str(stored["post_id"]))
                    except (KeyError, ValueError, TypeError):
                        print("X_BRIDGE_CHECKPOINT_INVALID creator="+username, flush=True)
                        return 5
                started = time.monotonic()
                try:
                    feed = source.fetch_user(username, cursor)
                    if feed is None:
                        print("X_BRIDGE_FETCH_UNAVAILABLE creator="+username, flush=True)
                        continue
                    first, new_count = _update(state, username, feed, int(time.time()))
                    _atomic_json(STATE_FILE, state)
                    print(
                        "X_BRIDGE_FETCH creator={} seconds={:.2f} posts={} new={} baseline={} complete={}".format(
                            username, time.monotonic()-started, len(feed.posts), new_count, first, feed.complete
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    print("X_BRIDGE_FETCH_ERROR creator={} type={}".format(username, type(exc).__name__), flush=True)
            now = int(time.time())
            output = _snapshot(state, now)
            if output is not None:
                try:
                    _atomic_json(SNAPSHOT_FILE, output)
                    print("X_BRIDGE_SNAPSHOT updated={} hiyori={} huya={}".format(
                        now, len(output["users"]["hanayohiyori"]), len(output["users"]["sakihanehuya"])
                    ), flush=True)
                except OSError as exc:
                    print("X_BRIDGE_SNAPSHOT_ERROR type="+type(exc).__name__, flush=True)
            remaining = max(0, args.interval - (time.monotonic()-cycle_started))
            while not STOP and remaining > 0:
                delay = min(1.0, remaining)
                time.sleep(delay)
                remaining -= delay
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
