"""Status of independent whole-X hashtag search providers.

Known-artist watch feeds are NOT full search.  This file stores only aggregate
health, never credentials, usernames, post text or URLs.  It does not invoke a
paid source or decide whether one may be enabled.
"""
from datetime import datetime, timezone
from fanart_discovery import BASE, read, write

STATE = BASE / "fanart_global_sources.json"
PROVIDERS = ("fx_search", "official_x")


def update(provider, result):
    if provider not in PROVIDERS:
        raise ValueError("Unrecognized global search provider")
    result = result if isinstance(result, dict) else {}
    state = read(STATE)
    providers = state.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    raw_status = str(result.get("status") or "unavailable")
    # FX can return partial for one or both groups; official X may be
    # explicitly disabled due to a credential or billing decision.
    groups = result.get("groups")
    groups = groups if isinstance(groups, dict) else {}
    per_group = {
        name: {"status": str(groups.get(name, {}).get("status") or "unverified")
               if isinstance(groups.get(name), dict) else "unverified",
               "added": max(0, int(groups.get(name, {}).get("added") or 0))
               if isinstance(groups.get(name), dict) else 0}
        for name in ("hanayo", "sakihane")
    }
    providers[provider] = {
        "status": raw_status if raw_status in (
            "ok", "partial", "unavailable", "disabled",
            "api_unavailable", "error") else "unavailable",
        "added": max(0, int(result.get("added") or 0)),
        "groups": per_group,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    state = {"providers": {p: providers[p] for p in PROVIDERS if p in providers},
             "checked_at": datetime.now(timezone.utc).isoformat()}
    # Distinguish a responding endpoint with valid photos from an endpoint
    # returning an empty page. Neither proves complete hashtag coverage.
    state["groups"] = {}
    for group in ("hanayo", "sakihane"):
        available = [p for p in PROVIDERS if p in providers
                     and providers[p]["groups"][group]["status"] in ("ok", "empty")]
        state["groups"][group] = {
            "status": "source_responding" if available else "global_search_unavailable",
            "responding_sources": available,
            "verified_photo_results": any(providers[p]["groups"][group]["status"] == "ok" for p in available),
            "coverage_verified": False,
        }
    write(STATE, state)
    return state
