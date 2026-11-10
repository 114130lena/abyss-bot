"""Isolated, opt-in Bluebird Twscraper benchmark (never sends Discord alerts).

No X credentials or raw posts are logged. A cookie file must be placed on the
Ubuntu host manually by its owner; never put it in Git, an issue, or a chat.
Authenticated source results are kept separate from the live bot and the
anonymous Bluebird sources.
"""
import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

ACCOUNTS = ("HanayoHiyori", "SakihaneHuya")
COOKIES_FILE = Path("/private/x.cookies")
DATABASE_FILE = Path("/private/twscraper.db")
SAMPLE_FILE = Path("/results/auth_samples.jsonl")
STOP = False


def stop_handler(_signum, _frame):
    global STOP
    STOP = True


def summarize(feed, account):
    posts = tuple(getattr(feed, "posts", ()) or ())
    filtered = tuple(p for p in posts if p.username.casefold() == account.casefold())
    entries = []
    for post in filtered[:150]:
        target = getattr(post, "repost_of", None)
        media = getattr(post, "media", ()) or ()
        entries.append({
            "id": str(post.post_id),
            "created_at": int(post.created_at),
            "repost": bool(post.is_repost),
            "repost_original_id": str(target.post_id) if target else None,
            "reply": bool(post.is_reply),
            "quote": bool(post.is_quote),
            "media_count": len(media),
            "video_count": sum(".mp4" in item.url.lower() for item in media),
        })
    return {
        "complete": bool(getattr(feed, "complete", False)),
        "posts_count": len(filtered),
        "originals_count": sum(not p.is_repost and not p.is_reply for p in filtered),
        "reposts_count": sum(bool(p.is_repost) for p in filtered),
        "replies_count": sum(bool(p.is_reply) for p in filtered),
        "media_posts_count": sum(bool(p.media) for p in filtered),
        "items": entries,
    }


def save_sample(record):
    SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Create with owner-only permissions; auth session data is separately stored.
    fd = os.open(SAMPLE_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    if SAMPLE_FILE.stat().st_size > 3_000_000:
        tail = SAMPLE_FILE.read_text(encoding="utf-8").splitlines()[-800:]
        temporary = SAMPLE_FILE.with_suffix(".tmp")
        temporary.write_text("\n".join(tail) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(SAMPLE_FILE)


def needs_next_cycle(cycle: int, maximum_cycles: int) -> bool:
    """Never sleep after the final cycle of a one-shot benchmark."""
    return maximum_cycles == 0 or cycle < maximum_cycles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--cycles", type=int, default=0)
    args = parser.parse_args()
    if not 180 <= args.interval <= 3600:
        raise SystemExit("Authenticated polling interval must be at least 180 seconds")
    os.umask(0o077)
    os.environ["TWS_TELEMETRY"] = "0"

    # Disable third-party log sinks BEFORE touching browser session credentials.
    from loguru import logger
    logger.remove()
    from core.twscraper import Twscraper, TwscraperRuntime, validate_cookies
    from core.service import ServiceCircuitBreaker

    if not COOKIES_FILE.is_file():
        print("AUTH_SHADOW_NOT_CONFIGURED: missing local cookie file", flush=True)
        return 2
    # Never echo this value or record it in command arguments / Docker metadata.
    cookies = COOKIES_FILE.read_text(encoding="utf-8").strip()
    try:
        validate_cookies(cookies)
    except Exception:
        print("AUTH_SHADOW_NOT_CONFIGURED: invalid cookie names", flush=True)
        return 2

    runtime = TwscraperRuntime(DATABASE_FILE)
    try:
        try:
            runtime.start(cookies)
        except Exception as exc:
            # Exception text can contain URLs or request data. Emit type only.
            print("AUTH_SHADOW_INIT_FAILURE", type(exc).__name__, flush=True)
            return 3
        finally:
            cookies = None

        source = Twscraper(ServiceCircuitBreaker("TwscraperAuthShadow", 2, 1800, 8), runtime)
        source.retries = 0
        source.retry_delay = 0
        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        print("AUTH_SHADOW_STARTED accounts=2 paid_api=no discord=no", flush=True)
        cycle = 0
        while not STOP and (args.cycles == 0 or cycle < args.cycles):
            cycle += 1
            start = time.monotonic()
            for account in ACCOUNTS:
                if STOP:
                    break
                before = time.monotonic()
                sample = {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "provider": "Twscraper",
                    "account": account,
                    "ok": False,
                }
                try:
                    feed = source.fetch_user(account, None)
                    if feed is None:
                        sample["error"] = "source_unavailable_or_rate_limited"
                    else:
                        sample["ok"] = True
                        sample.update(summarize(feed, account))
                except Exception as exc:
                    sample["error"] = type(exc).__name__
                sample["elapsed_seconds"] = round(time.monotonic() - before, 3)
                save_sample(sample)
                print(
                    "AUTH_SHADOW_SAMPLE cycle={} account={} success={} elapsed_s={} "
                    "posts={} originals={} reposts={} media={} error={}".format(
                        cycle, account, sample["ok"], sample["elapsed_seconds"],
                        sample.get("posts_count", 0),
                        sample.get("originals_count", 0),
                        sample.get("reposts_count", 0),
                        sample.get("media_posts_count", 0), sample.get("error", "-"),
                    ),
                    flush=True,
                )
            if not needs_next_cycle(cycle, args.cycles):
                break
            elapsed = time.monotonic() - start
            remaining = max(0, args.interval - elapsed)
            while remaining > 0 and not STOP:
                time.sleep(min(1.0, remaining))
                remaining -= 1
        print("AUTH_SHADOW_STOPPED cycles="+str(cycle), flush=True)
        return 0
    finally:
        # Ensure Bluebird's async X session closes without leaving a writer.
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
