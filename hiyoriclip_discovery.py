"""Safe account discovery from public #hiyoriclip_ RSS references.

This is intentionally NOT a global hashtag crawler. Finds public X status
links embedded in known accounts' repost/quote RSS entries. Candidates need
a live original-post verification before their author is enrolled.
"""
import re
from html import unescape
from urllib.parse import urlsplit

URL=re.compile(r'https?://(?:www\.)?(?:x\.com|twitter\.com|fxtwitter\.com|fixupx\.com)/([a-zA-Z0-9_]{1,15})/status/([0-9]{10,25})')
MAX_REFERENCES=60

def status_links(value):
    text=unescape(str(value or ""))
    seen=set()
    for match in URL.finditer(text):
        handle,post_id=match.groups()
        key=(handle.casefold(),post_id)
        if key not in seen:
            seen.add(key)
            yield handle,post_id

def candidates_from_rss(xml, owner, tag):
    """Discover repost authors without treating their text as our own clip."""
    import xml.etree.ElementTree as ET
    root=ET.fromstring(xml)
    if root.tag!="rss": raise ValueError("Invalid RSS")
    chan=root.find("channel")
    if chan is None: raise ValueError("Missing channel")
    results=[]
    for item in chan.findall("item"):
        parts=[item.findtext("link") or "",item.findtext("description") or "",
               item.findtext("title") or ""]
        content=" ".join(parts)
        # Repost references are useful even when RSS title omits the hashtag.
        for author,post_id in status_links(content):
            if author.casefold()==owner.casefold():
                continue
            key=(author,post_id)
            if key not in results:
                results.append(key)
            if len(results)>=MAX_REFERENCES:
                return results
    return results

def new_accounts(catalog, existing, *, min_clips=1, limit=30):
    """Only authors with a verified, stored original video are proposed."""
    known={name.casefold() for name in existing}
    counts={}
    for row in catalog.values():
        name=str(row.get("author") or "").lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}",name) or name.casefold() in known:
            continue
        if not row.get("video_ids") and not row.get("video_urls"):
            continue
        counts[name]=counts.get(name,0)+1
    return [k for k,_ in sorted(
        ((k,v) for k,v in counts.items() if v>=min_clips),
        key=lambda x:(-x[1],x[0].casefold()))][:limit]
