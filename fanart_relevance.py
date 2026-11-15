"""Conservative metadata screen for automatically selected Hanayo guild artwork.

This is NOT visual character recognition. The Hiyori fanart hashtag is a
sufficient *metadata* cue for otherwise unambiguous original posts, including
caption-free artwork. Reject explicit background-only posts and reposts;
other members' tags alone do not disqualify collaboration fanart.
"""
import re

_HIYORI = re.compile(r"(?<![\w#])(?:하나요\s*히요리|히요리|花陽\s*ひより|ひより|ヒヨリ|hanayo\s*hiyori|hanayohiyori|hiyori)(?![\w])", re.I)
_TAGS = re.compile(r"(?<![\w])#[a-zA-Z0-9_]+", re.I)
_GROUP_ONLY = re.compile(r"(?:어비스\s*(?:첫|오리지널|신곡|곡)|abyss\s*(?:original|song))", re.I)
_BACKGROUND_ONLY = re.compile(
    r"(?:풍경|배경\s*(?:그림|사진|이미지)?|캐릭터\s*(?:없음|없는)|"
    r"background\s*(?:only|art|image)?|scenery\b|landscape\b)", re.I)
_REPOST = re.compile(r"^\s*(?:RT by |RT @|repost by )", re.I)
_HUYA = re.compile(
    r"(?<![\w#])(?:사키하네\s*후야|후야|さきはね\s*ふや|ふや|フヤ|"
    r"sakihane\s*huya|sakihanehuya|huya)(?![\w])", re.I)


# Shared, free text/hashtag triage. This is a metadata screen, not an image
# recognition claim; caption-free posts remain unverified by visual content.
_OTHER_CHARACTER_NAMES = re.compile(
    r"(?<![\\w#])(?:타비|시라유키\\s*히나|히나|유니|아야츠노\\s*유니|"
    r"칸나|아이리\\s*칸나|시부키|리코|아카네\\s*리제|"
    r"tavi|hina|yuni|kanna|shibuki|riko|rize)(?![\\w])", re.I)


def assess_post_metadata(group, item):
    """Return (eligible, reason), without deleting or mutating source records.

    Other members' tags are allowed for collaboration artwork. Explicit
    background-only captions or another named character without the intended
    character are still insufficient for output.
    A hashtag-only match is permitted as metadata, never visually verified.
    """
    if group not in ("hanayo", "sakihane") or not isinstance(item, dict):
        return False, "invalid"
    if item.get("server_banner_excluded") is True:
        return False, "excluded"
    text = str(item.get("text") or "")
    if _REPOST.search(text):
        return False, "repost"
    tags = {x.casefold() for x in _TAGS.findall(text)}
    tag = "#hiyoriart_" if group == "hanayo" else "#huyart"
    if tag not in tags:
        return False, "missing_official_tag"
    caption = _TAGS.sub(" ", text)
    own = _HIYORI if group == "hanayo" else _HUYA
    if (_GROUP_ONLY.search(caption) or _BACKGROUND_ONLY.search(caption)) and not own.search(caption):
        return False, "background_or_group_only"
    other_creator = _HUYA if group == "hanayo" else _HIYORI
    if other_creator.search(caption) and not own.search(caption):
        return False, "other_creator_named"
    if _OTHER_CHARACTER_NAMES.search(caption) and not own.search(caption):
        return False, "other_character_named"
    return True, "metadata_only"



def eligible_hanayo_banner(item):
    return assess_post_metadata("hanayo", item)[0]


def eligible_huya_banner(item):
    return assess_post_metadata("sakihane", item)[0]
