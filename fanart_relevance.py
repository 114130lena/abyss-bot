"""Conservative metadata screen for automatically selected Hanayo guild artwork.

This is NOT visual character recognition. A multi-member tag or a group-only
caption is insufficient evidence that Hiyori is pictured. Only original posts
with Hiyori-specific wording may enter the automatic guild-banner pool.
Notification source-links remain independent.
"""
import re

_HIYORI = re.compile(r"(?<![\w#])(?:하나요\s*히요리|히요리|花陽\s*ひより|ひより|ヒヨリ|hanayo\s*hiyori|hanayohiyori|hiyori)(?![\w])", re.I)
_TAGS = re.compile(r"(?<![\w])#[a-zA-Z0-9_]+", re.I)
_OTHER_MEMBER_TAGS = {"#toart_", "#rangart_", "#maryart_", "#huyart"}
_GROUP_ONLY = re.compile(r"(?:어비스\s*(?:첫|오리지널|신곡|곡)|abyss\s*(?:original|song))", re.I)
_REPOST = re.compile(r"^\s*(?:RT by |RT @|repost by )", re.I)


def eligible_hanayo_banner(item):
    """Fail closed for ambiguous multi-character artwork; no manual approval required."""
    if not isinstance(item, dict):
        return False
    if item.get("server_banner_excluded") is True:
        return False
    text = str(item.get("text") or "")
    if _REPOST.search(text):
        return False
    tags = {x.lower() for x in _TAGS.findall(text)}
    if tags & _OTHER_MEMBER_TAGS:
        return False
    # Remove hashtag tokens before checking for an explicit character cue.
    caption = _TAGS.sub(" ", text)
    if _GROUP_ONLY.search(caption) and not _HIYORI.search(caption):
        return False
    if not _HIYORI.search(caption):
        return False
    return True
