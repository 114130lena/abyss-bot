"""Persistent exact-event Discord notification idempotency across all workers.

An event key is a PLATFORM:OWNER:EXTERNAL_ID, independent from the
image selection and content. Claims are written BEFORE the webhook request.
If Discord may have accepted a send whose acknowledgement was lost, block
automatic retries and expose the pending state for operator reconciliation.
"""
import fcntl
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
LEDGER = BASE / "discord_event_delivery_ledger.json"
LOCK = BASE / "discord_event_delivery_ledger.lock"
PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,25}:[A-Za-z0-9_-]{1,90}:[A-Za-z0-9_.:-]{1,160}$")
RETENTION_DAYS = 365


def _read():
    try:
        state = json.loads(LEDGER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Event delivery journal unreadable; notifications blocked to prevent replay") from exc
    if not isinstance(state, dict) or any(not isinstance(v, dict) for v in state.values()):
        raise ValueError("Invalid event delivery journal; delivery blocked")
    return state


def _write(state):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".discord_event_", dir=str(LEDGER.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, LEDGER)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _locked(operation):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            return operation()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def claim(key, now=None):
    if not isinstance(key, str) or not PATTERN.fullmatch(key):
        raise ValueError("Invalid notification event identity")
    now = now or datetime.now(timezone.utc)
    def action():
        history = _read()
        if key in history:
            logging.warning("DISCORD_EVENT_DUPLICATE_SUPPRESSED key=%s status=%s",
                            key, history[key].get("status", "unknown"))
            return False
        cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
        # Pending/uncertain requests are never silently forgotten.
        history = {k:v for k,v in history.items()
                   if v.get("status") != "sent" or v.get("at", "") >= cutoff}
        history[key] = {"at": now.isoformat(), "status": "pending"}
        _write(history)
        return True
    return _locked(action)


def release_preconnect(key):
    """Only release a claim when transport proves no connection was established."""
    if not isinstance(key, str) or not PATTERN.fullmatch(key):
        raise ValueError("Invalid notification event identity")
    def action():
        history = _read()
        if history.get(key, {}).get("status") != "pending":
            return False
        del history[key]
        _write(history)
        logging.info("DISCORD_PRECONNECT_CLAIM_RELEASED key=%s", key)
        return True
    return _locked(action)


def update(key, status, now=None):
    if status not in ("sent", "uncertain"):
        raise ValueError("Unsupported event delivery status")
    now = now or datetime.now(timezone.utc)
    def action():
        history = _read()
        if key not in history:
            raise ValueError("Event delivery claim missing")
        history[key] = {"at": now.isoformat(), "status": status}
        _write(history)
    _locked(action)


def get_status(key):
    """Read one event's durable receipt without exposing its message body."""
    if not isinstance(key, str) or not PATTERN.fullmatch(key):
        raise ValueError("Invalid notification event identity")
    return _locked(lambda: _read().get(key, {}).get("status"))
