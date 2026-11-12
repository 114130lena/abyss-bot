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
            from fanart_global_sources import update as update_global
            update_global("fx_search", result)
            global_search_failed = result.get("status") != "ok"
        except (OSError, ValueError, TypeError, requests.RequestException):
            logging.exception("FANART_DISCOVERY_HASHTAG failed; existing queue preserved")
            global_search_failed = True
            try:
                from fanart_global_sources import update as update_global
                update_global("fx_search", {"status": "unavailable"})
            except (OSError, ValueError, TypeError):
                logging.warning("FANART_GLOBAL_HEALTH_WRITE_FAILED")

    # Free indexes are supplementary and bounded (once / 2 hours). They may
    # discover new artists, but only verified original X posts with photos
    # enter the same canonical, group:post_id-deduplicated queue.
    if os.getenv("FANART_FREE_INDEX_SEARCH_ENABLED", "1") == "1":
        try:
            from fanart_free_search import discover as search_free_indexes
            free_result = search_free_indexes()
            logging.info("FANART_FREE_INDEX result=%s", free_result)
        except (OSError, ValueError, TypeError, requests.RequestException):
            logging.exception("FANART_FREE_INDEX_UNAVAILABLE; existing queue preserved")

    # Even responding global indexes can miss newer posts. Always run the
    # bounded artist watch to backfill verified authors, including new ones.
    if os.getenv("FANART_ARTIST_WATCH_ENABLED", "1") == "1":
        try:
            from fanart_artist_watch import discover as watch_artists
            logging.info("FANART_DISCOVERY_ARTIST_FALLBACK result=%s", watch_artists())
        except (OSError, ValueError, TypeError, requests.RequestException):
            logging.exception("FANART_DISCOVERY_ARTIST_FALLBACK unavailable; queue preserved")

    # Hard stop on chargeable X-wide fanart search. Existing X notification
    # workers use their own independent settings; only fanart search is disabled.
    if os.getenv("FANART_USE_X_API", "0") == "1":
        logging.warning("FANART_OFFICIAL_X_SEARCH_DISABLED reason=zero_cost_policy")
    try:
        from fanart_global_sources import update as update_global
        update_global("official_x", {"status": "disabled"})
    except (OSError, ValueError, TypeError):
        logging.warning("FANART_GLOBAL_HEALTH_WRITE_FAILED")

    if os.getenv("FANART_CACHE_CANDIDATES", "0") == "1":
        from fanart_candidate_cache import run as cache_candidates
        try:
            result = cache_candidates()
            logging.info("FANART_REVIEW_CACHE result=%s", result)
        except Exception:
            logging.exception("FANART_REVIEW_CACHE failed; existing queue preserved")
