"""Read-only Bluebird source benchmark for Abyss Bot.

Run inside the upstream ghcr.io/ethanc/bluebird container. Never import its
Discord notifier or supply webhook credentials. No X cookies or paid APIs.
Only normalized IDs, timestamps and media counts are persisted.
"""
import argparse
import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from core.bettertwitfix import BetterTwitFix
from core.carryfeed import CarryFeed
from core.fxembed import FxEmbed
from core.service import ServiceCircuitBreaker

ACCOUNTS = ("HanayoHiyori", "SakihaneHuya")
PROVIDERS = (BetterTwitFix, CarryFeed, FxEmbed)
OUT = Path("/results/samples.jsonl")
STOP = False


def stop_handler(_signal, _frame):
    global STOP
    STOP = True


def summarize(feed, account):
    posts = tuple(getattr(feed, "posts", ()) or ())
    original = [
        p for p in posts
        if getattr(p, "username", "").casefold() == account.casefold()
        and not getattr(p, "is_repost", False)
    ]
    reposts = [p for p in posts if getattr(p, "is_repost", False)]
    media = [p for p in posts if getattr(p, "media", ())]
    # Media counts describe Bluebird-normalized attachments, not definitive
    # image/video completeness. Compare external original URLs separately.
    def info(post):
        return {
            "id": str(post.post_id),
            "created_at": int(post.created_at),
            "repost": bool(post.is_repost),
            "reply": bool(post.is_reply),
            "quote": bool(post.is_quote),
            "media_count": len(post.media),
            "video_url_present": any(".mp4" in m.url.lower() for m in post.media),
        }
    return {
        "complete": bool(getattr(feed, "complete", False)),
        "posts_count": len(posts),
        "originals_count": len(original),
        "reposts_count": len(reposts),
        "replies_count": sum(bool(p.is_reply) for p in posts),
        "media_posts_count": len(media),
        "video_url_posts_count": sum(
            any(".mp4" in m.url.lower() for m in p.media) for p in posts
        ),
        "items": [info(p) for p in posts[:100]],
    }


def inspect_source(provider, account):
    started = time.monotonic()
    outcome = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.service_name,
        "account": account,
        "ok": False,
    }
    try:
        feed = provider.fetch_user(account, None)
        outcome["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if feed is None:
            outcome["error"] = "source_failed_or_cooling_down"
        else:
            outcome["ok"] = True
            outcome.update(summarize(feed, account))
    except Exception as exc:
        outcome["elapsed_seconds"] = round(time.monotonic() - started, 3)
        outcome["error"] = type(exc).__name__
    return outcome


def save(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    if OUT.stat().st_size > 4_000_000:
        recent = OUT.read_text(encoding="utf-8").splitlines()[-1500:]
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text("\n".join(recent) + "\n", encoding="utf-8")
        tmp.replace(OUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--cycles", type=int, default=0)
    args = parser.parse_args()
    if not 30 <= args.interval <= 3600:
        raise SystemExit("interval outside safe range")
    os.environ["TWS_TELEMETRY"] = "0"
    # Use Bluebird's REAL adapters. Do not invoke its Discord pipeline.
    providers = []
    for source_class in PROVIDERS:
        breaker = ServiceCircuitBreaker(source_class.service_name, 3, 1800, 8)
        instance = source_class(breaker)
        instance.retries = 0
        instance.retry_delay = 0
        providers.append(instance)
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    print("BLUEBIRD_SHADOW_START accounts=2 paid_api=no login=no discord=no", flush=True)
    cycle = 0
    while not STOP and (args.cycles == 0 or cycle < args.cycles):
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(inspect_source, provider, account)
                for provider in providers for account in ACCOUNTS
            ]
            rows = [future.result() for future in as_completed(futures)]
        save(rows)
        cycle += 1
        for row in rows:
            print(
                "BLUEBIRD_SAMPLE cycle={cycle} provider={provider} account={account} "
                "ok={ok} elapsed_s={elapsed_seconds} posts={posts_count} "
                "originals={originals_count} reposts={reposts_count} "
                "with_media={media_posts_count} video_urls={video_url_posts_count} "
                "error={error}".format(
                    cycle=cycle,
                    provider=row["provider"], account=row["account"],
                    ok=row["ok"], elapsed_seconds=row["elapsed_seconds"],
                    posts_count=row.get("posts_count", 0),
                    originals_count=row.get("originals_count", 0),
                    reposts_count=row.get("reposts_count", 0),
                    media_posts_count=row.get("media_posts_count", 0),
                    video_url_posts_count=row.get("video_url_posts_count", 0),
                    error=row.get("error", "-"),
                ),
                flush=True,
            )
        print("BLUEBIRD_CYCLE_COMPLETE", cycle, flush=True)
        remaining = max(0.0, args.interval - (time.monotonic() - start))
        while remaining > 0 and not STOP:
            delay = min(1.0, remaining)
            time.sleep(delay)
            remaining -= delay
    print("BLUEBIRD_SHADOW_STOP cycles="+str(cycle), flush=True)


if __name__ == "__main__":
    main()
