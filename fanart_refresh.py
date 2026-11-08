"""Scheduled fanart candidate collection. Private review queue only, never auto-post."""
import logging
import os
import time


def check_fanart():
    started = time.monotonic()
    from fanart_fx_rss import run as fetch_profile_feeds
    results = fetch_profile_feeds()
    logging.info("FANART_DISCOVERY_PROFILE elapsed_seconds=%.2f result=%s",
                 time.monotonic() - started, results)

    # Search all public authors, not just creator profile feeds.
    # Keep the profile-RSS and optional official-X-API sources as fallbacks.
    if os.getenv("FANART_FX_SEARCH_ENABLED", "1") == "1":
        try:
            from fanart_fx_search import discover as search_hashtag
            result = search_hashtag()
            logging.info("FANART_DISCOVERY_HASHTAG result=%s", result)
        except (OSError, ValueError, TypeError):
            logging.exception("FANART_DISCOVERY_HASHTAG failed; existing queue preserved")

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
