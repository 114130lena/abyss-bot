"""Per-process RSS source cooldown and latency instrumentation."""
import logging,time,threading
import requests
_lock=threading.Lock()
_until={}
def active(url):
    with _lock:return time.monotonic()>=_until.get(url,0)
def note_failure(url,exc):
    code=getattr(getattr(exc,"response",None),"status_code",None)
    seconds=600 if code==429 else 1800 if code in (404,410) else 90
    with _lock:_until[url]=time.monotonic()+seconds
    logging.warning("X_SOURCE_BACKOFF host=%s status=%s cooldown_s=%s",requests.utils.urlparse(url).netloc,code,seconds)
def timed_fetch(fetch,url):
    start=time.monotonic()
    try:
        result=fetch(url)
        logging.info("X_RSS_FETCH host=%s seconds=%.2f",requests.utils.urlparse(url).netloc,time.monotonic()-start)
        return result
    except Exception as exc:
        note_failure(url,exc)
        raise
