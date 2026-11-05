"""Shared X RSS provider circuit breaker across Hanayo and Huya pollers.

On a provider-wide rate limit or outage, do not pound a second account feed
on the same host every 30 seconds. Other RSS providers continue independently.
"""
import logging
import threading
import time
from urllib.parse import urlsplit

import requests

_lock = threading.Lock()
_until = {}
_failures = {}
_host_until = {}


def _host(url):
    return (urlsplit(str(url)).hostname or "").lower()


def active(url):
    now = time.monotonic()
    with _lock:
        return now >= max(_until.get(url, 0), _host_until.get(_host(url), 0))


def note_failure(url, exc):
    code = getattr(getattr(exc, "response", None), "status_code", None)
    host = _host(url)
    with _lock:
        failures = _failures.get(url, 0) + 1
        _failures[url] = failures
        # A 404 from public RSS endpoints may be an outage rather than a
        # deleted account; retry eventually, not on every loop.
        if code == 429:
            seconds = 1800
        elif code in (404, 410):
            seconds = 3600
        else:
            # Timeouts and transient 5xx responses also deserve cooldown.
            seconds = min(1800, 180 * (2 ** min(failures - 1, 4)))
        until = time.monotonic() + seconds
        _until[url] = max(_until.get(url, 0), until)
        # Shared provider status applies to both creator endpoints.
        # Nitter hosts stay independent from each other and from FxTwitter.
        _host_until[host] = max(_host_until.get(host, 0), until)
    logging.warning("X_SOURCE_BACKOFF host=%s status=%s cooldown_s=%d", host, code, seconds)


def timed_fetch(fetch, url):
    start = time.monotonic()
    try:
        result = fetch(url)
    except Exception as exc:
        note_failure(url, exc)
        raise
    with _lock:
        _failures.pop(url, None)
        _until.pop(url, None)
    logging.info("X_RSS_FETCH host=%s seconds=%.2f", _host(url), time.monotonic() - start)
    return result
