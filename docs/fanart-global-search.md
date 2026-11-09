# X-wide fanart discovery: provider health and recovery

## Current limitation
The FxEmbed `/2/search` endpoint has an upstream outage (FxEmbed #2600, reported since 2026-10-09). Creator/profile RSS and rotating known-artist media feeds remain useful but **cannot discover every new artist**. No code-only change can guarantee global X coverage while the upstream source remains unavailable.

## Sources and cost gate
- `FANART_FX_SEARCH_ENABLED=1` (default): attempt the existing no-key whole-X search. The response must contain a valid search result structure; provider outage is recorded as unavailable, not silently regarded as success.
- `FANART_ARTIST_WATCH_ENABLED=1` (default): fallback monitoring of already-known artists only; it never counts as global hashtag search health.
- `FANART_USE_X_API=1` **only after deliberate operator approval of X API access, quota and billing**, plus a configured `X_BEARER_TOKEN`: existing official-X recent search for both hashtags. Do not enable automatically because it can incur cost.
- `FANART_USE_X_API=0` (default): no official-X search calls.
- These paths never authorize replaying Discord messages, clearing usage history or overriding fanart exclusions.

## Monitoring
`creator_media/fanart_global_sources.json` contains per-provider status and per-group `source_responding` vs `global_search_unavailable`. `coverage_verified=false` remains false until real production verification confirms a previously unseen author and expected hashtag/media provenance, even when an API returns 200. The usual `fanart_fx_search_state.json` and known-artist health records remain separate.

## Acceptance for actual search restoration
1. Observe a working, authorized X-wide query on both `#hiyoriart_` and `#huyart`.
2. Confirm a post by an author absent from the existing candidate queue, with original post URL and photo URLs.
3. Confirm no duplicate insertion, blocked-art restriction changes, notification history resets or unauthorized paid calls.
4. Confirm a repeat successful production poll and report it independently of artist fallback.

Until these checks pass, consider **global discovery not yet restored**.
