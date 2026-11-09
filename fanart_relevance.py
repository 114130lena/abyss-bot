"""Conservative metadata screen for automatically selected Hanayo guild artwork.

This is NOT visual character recognition. The Hiyori fanart hashtag is a
sufficient *metadata* cue for otherwise unambiguous original posts, including
caption-free artwork. Reject explicit background-only posts, reposts and mixed
member tags. Notification source-links remain independent.
"""
import re

_HIYORI = re.compile(r"(?<![\w#])(?:하나요\s*히요리|히요리|花陽\s*ひより|ひより|ヒヨリ|hanayo\s*hiyori|hanayohiyori|hiyori)(?![\w])", re.I)
_TAGS = re.compile(r"(?<![\w])#[a-zA-Z0-9_]+", re.I)
_OTHER_MEMBER_TAGS = {"#toart_", "#rangart_", "#maryart_", "#huyart"}
_GROUP_ONLY = re.compile(r"(?:어비스\s*(?:첫|오리지널|신곡|곡)|abyss\s*(?:original|song))", re.I)
_BACKGROUND_ONLY = re.compile(
    r"(?:풍경|배경\s*(?:그림|사진|이미지)?|캐릭터\s*(?:없음|없는)|"
    r"background\s*(?:only|art|image)?|scenery\b|landscape\b)", re.I)
_REPOST = re.compile(r"^\s*(?:RT by |RT @|repost by )", re.I)
_HUYA = re.compile(
    r"(?<![\w#])(?:사키하네\s*후야|후야|さきはね\s*ふや|ふや|フヤ|"
    r"sakihane\s*huya|sakihanehuya|huya)(?![\w])", re.I)
_OTHER_HUYA_TAGS = {"#hiyoriart_", "#toart_", "#rangart_", "#maryart_"}


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
    if "#hiyoriart_" not in tags or tags & _OTHER_MEMBER_TAGS:
        return False
    # Artists often use only the official hashtag and no text. That should
    # not prevent a suitable wide illustration from entering the pool.
    # Explicitly background-only or group-only captions still require
    # a separate positive Hiyori mention. When vision scoring is enabled,
    # it provides an independent character-in-image check.
    caption = _TAGS.sub(" ", text)
    if (_GROUP_ONLY.search(caption) or _BACKGROUND_ONLY.search(caption)) and not _HIYORI.search(caption):
        return False
    return True


def eligible_huya_banner(item):
    """Only unambiguous original #huyart posts; image must also be wide.

    The Huya rule never relies on the Hiyori-only vision reference image.
    """
    if not isinstance(item, dict) or item.get("server_banner_excluded") is True:
        return False
    text = str(item.get("text") or "")
    if _REPOST.search(text):
        return False
    tags = {x.lower() for x in _TAGS.findall(text)}
    if "#huyart" not in tags or tags & _OTHER_HUYA_TAGS:
        return False
    caption = _TAGS.sub(" ", text)
    if (_GROUP_ONLY.search(caption) or _BACKGROUND_ONLY.search(caption)) and not _HUYA.search(caption):
        return False
    return True
