"""Crash-safe Discord server banner transaction helper.

A pending intent is saved before a PATCH. On retries, a live guild read
reconciles the persisted state instead of applying a second random image.
Only a server-acknowledged banner mutation is committed as a completed slot.
"""
import json
import time
from datetime import datetime, timezone

import requests

BASE = "https://discord.com/api/v10"


def guild_banner(token, guild):
    resp = requests.get(BASE + "/guilds/" + str(guild),
                        headers={"Authorization": "Bot " + token},
                        timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "banner" not in data:
        raise RuntimeError("Discord guild response lacks banner field")
    return data["banner"]


def record_pending(state, *, slot, info, before_hash, write):
    if state.get("pending_banner"):
        raise RuntimeError("An unreconciled banner transaction already exists")
    state = dict(state)
    state["pending_banner"] = {
        "slot": slot,
        "before_hash": before_hash,
        "sha256": info["sha256"],
        "artist": info.get("artist"),
        "post_url": info.get("post_url"),
        "rotation_used": info.get("rotation_used", []),
        "candidate_total": info.get("candidate_total", 0),
        "remaining_after": info.get("remaining_after", 0),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    write(state)
    return state


def reconcile(state, token, guild, write, *, grace_seconds=90):
    """Return (state, outcome). Never apply a different artwork if uncertain."""
    pending = state.get("pending_banner")
    if not isinstance(pending, dict):
        return state, "none"
    current = guild_banner(token, guild)
    if current != pending.get("before_hash"):
        new = {**state, "last_slot": pending["slot"],
               "last_sha256": pending["sha256"],
               "artist": pending.get("artist"),
               "post_url": pending.get("post_url"),
               "updated_at": datetime.now(timezone.utc).isoformat(),
               "rotation_used": pending.get("rotation_used", []),
               "candidate_total": pending.get("candidate_total", 0),
               "remaining_after": pending.get("remaining_after", 0),
               "info_card_pending": True,
               "verified_banner_hash": current}
        new.pop("pending_banner", None)
        write(new)
        return new, "reconciled_changed"
    # The original banner is still live. Delay retry briefly if the HTTP
    # request might still be in flight (or Discord CDN metadata is delayed).
    try:
        started = datetime.fromisoformat(pending["started_at"])
        age = (datetime.now(timezone.utc) - started).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = 0
    if age < grace_seconds:
        return state, "pending_grace"
    new = dict(state)
    new.pop("pending_banner", None)
    write(new)
    return new, "not_applied"


def run_change(state, *, slot, info, payload, token, guild, write, change):
    """Persist intent, PATCH banner, verify Discord GET, then finalize."""
    before = guild_banner(token, guild)
    state = record_pending(state, slot=slot, info=info, before_hash=before, write=write)
    change(payload, token, guild)
    after = guild_banner(token, guild)
    if after == before:
        raise RuntimeError("Discord did not confirm banner hash change; leaving intent for recovery")
    state, result = reconcile(state, token, guild, write, grace_seconds=0)
    if result != "reconciled_changed":
        raise RuntimeError("Discord guild banner reconciliation failed")
    return state
