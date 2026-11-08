"""Scheduled fanart candidate collection. Private review queue only, never auto-post."""
import logging
import os
import time
import requests
from datetime import datetime, timezone


def check_fanart():
    started = time.monotonic()
    from fanart_fx_rss import run as fetch_profile_feeds
    results = fetch_profile_feeds()
    logging.info("FANART_DISCOVERY_PROFILE elapsed_seconds=%.2f result=%s",
                 time.monotonic() - started, results)
    # Preserve source-specific success/failure for read-only operational audits.
    # Creator-profile RSS failures must never block global search or caching.
    try:
        from fanart_discovery import BASE, write
        groups = {name: {"status": ("unavailable" if value.get("error") else "ok"),
                         "added": int(value.get("added") or 0),
                         "error": str(value.get("error") or "")}
                  for name, value in results.items() if isinstance(value, dict)}
        write(BASE / "fanart_profile_state.json", {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "groups": groups})
    except (OSError, ValueError, TypeError):
        logging.warning("FANART_PROFILE_HEALTH_WRITE_FAILED")


    global_search_failed = os.getenv("FANART_FX_SEARCH_ENABLED", "1") != "1"
    # Search all public authors, not just creator profile feeds.
    # Keep the profile-RSS and optional official-X-API sources as fallbacks.
    if os.getenv("FANART_FX_SEARCH_ENABLED", "1") == "1":
        try:
            from fanart_fx_search import discover as search_hashtag
            result = search_hashtag()
            logging.info("FANART_DISCOVERY_HASHTAG result=%s", result)
            global_search_failed = result.get("status") != "ok"
        except (OSError, ValueError, TypeError):
            logging.exception("FANART_DISCOVERY_HASHTAG failed; existing queue preserved")
            global_search_failed = True

    # Global X hashtag search may be unavailable while media feeds of artists
    # already recorded in the queue remain accessible. Rotating accounts
    # preserve the original post and attached images; not global coverage.
    if global_search_failed and os.getenv("FANART_ARTIST_WATCH_ENABLED", "1") == "1":
        try:
            from fanart_artist_watch import discover as watch_artists
            logging.info("FANART_DISCOVERY_ARTIST_FALLBACK result=%s", watch_artists())
        except (OSError, ValueError, TypeError, requests.RequestException):
            logging.exception("FANART_DISCOVERY_ARTIST_FALLBACK unavailable; queue preserved")

    if os.getenv("FANART_USE_X_API", "0") == "1":
        from fanart_discovery import discover
        try:
            result = discover()
            logging.info("FANART_DISCOVERY_API result=%s", result)
        except Exception:
            logging.exception("FANART_DISCOVERY_API failed; existing queue preserved")

    if os.getenv("FANART_CACHE_CANDIDATES", "0") == "1":
        from fanart_candidate_cache import run as cache_candidates
        try:
            result = cache_candidates()
            logging.info("FANART_REVIEW_CACHE result=%s", result)
        except Exception:
            logging.exception("FANART_REVIEW_CACHE failed; existing queue preserved")
