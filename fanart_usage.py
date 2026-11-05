"""Shared fanart usage separation for notification vs Discord guild banners.

The guild banner scheduler records used sha256 values. Notification selection
resolves those hashes to original X posts and excludes their entire galleries.
Explicit server-banner-only entries are excluded even before the first rotation.
"""
from urllib.parse import urlsplit
from fanart_discovery import BASE, read


def canonical_post(url):
    """Treat x.com and twitter.com links to one post as the same artwork."""
    p = urlsplit(str(url or ""))
    if p.scheme != "https" or p.hostname not in (
        "x.com", "www.x.com", "twitter.com", "www.twitter.com"
    ):
        return ""
    import re
    match = re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/([0-9]+)", p.path)
    return match.group(1) if match else ""


def excluded_notification_art(group):
    """Return (reserved sha256s, original post IDs).

    A currently/previously used guild banner and an explicitly reserved entry
    must never appear in the notification fanart footer.
    """
    index = read(BASE / "fanart_index.json")
    entries = index.get(group, []) if isinstance(index, dict) else []
    state = read(BASE / "guild_banner_rotation_state.json") if group == "hanayo" else {}
    used = set()
    if isinstance(state, dict):
        used = {s for s in state.get("rotation_used", []) if isinstance(s, str)}
        last = state.get("last_sha256")
        if isinstance(last, str) and last:
            used.add(last)
    reserved_sha = set(used)
    reserved_posts = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha256")
        dedicated = entry.get("use_for_server_banner") is True or entry.get("server_banner_only") is True
        if isinstance(sha, str) and (sha in used or dedicated):
            reserved_sha.add(sha)
            pid = canonical_post(entry.get("post_url"))
            if pid:
                reserved_posts.add(pid)
    return reserved_sha, reserved_posts


def notification_allowed(group, entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("use_for_notification_banner") is False:
        return False
    if entry.get("use_for_server_banner") is True or entry.get("server_banner_only") is True:
        return False
    hashes, posts = excluded_notification_art(group)
    return (entry.get("sha256") not in hashes
            and canonical_post(entry.get("post_url")) not in posts)
