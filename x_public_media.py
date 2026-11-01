"""Best-effort free public X profile metadata collector; no login or paid API.

Only account profile/header metadata is accepted, never timeline images.
Sources are attempted in order; sites may refuse anonymous access.
"""
import html
import logging
import re
from urllib.parse import urlparse
import requests

ALLOWED_IMAGES = ("pbs.twimg.com", "abs.twimg.com")
PUBLIC_PAGES = ("https://x.com/{username}", "https://www.sotwe.com/{username}", "https://w.twstalker.com/{username}")

def valid_image(value):
    try:
        p = urlparse(value)
        return p.scheme == "https" and p.hostname in ALLOWED_IMAGES and bool(p.path)
    except (ValueError, TypeError):
        return False

def meta_tags(page):
    return re.findall(r'<meta\b[^>]*>', page, flags=re.I)

def attributes(tag):
    return {k.lower(): html.unescape(v) for k,v in re.findall(
        r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)}

def extract(page, username, domain):
    """Return only canonical profile/banner URLs, with conservative matching."""
    low = page.lower()
    account = username.lower()
    # Third-party mirrors must indicate the requested account in the page title/heading.
    if domain != "x.com" and not (("@"+account) in low or ("/"+account) in low):
        return []
    records = []
    for tag in meta_tags(page):
        a = attributes(tag)
        prop = (a.get("property") or a.get("name") or "").lower()
        value = a.get("content", "")
        if not valid_image(value):
            continue
        # X OG image usually refers to the avatar but cannot identify reliably.
        if prop in ("twitter:image", "twitter:image:src", "og:image") and domain == "x.com":
            records.append(("profile", "x", value))
    # Mirrors often identify profile and cover image using semantic alt text.
    for m in re.finditer(r'<img\b[^>]*>', page, flags=re.I):
        a = attributes(m.group())
        label = (a.get("alt", "") + " " + a.get("class", "") + " " + a.get("id", "")).lower()
        value = a.get("src") or a.get("data-src", "")
        if not valid_image(value):
            continue
        if "profile" in label and ("image" in label or "avatar" in label or "photo" in label):
            records.append(("profile", "x", value))
        elif any(x in label for x in ("banner", "header", "cover")):
            records.append(("banner", "x", value))
    # X's own profile header is commonly present as explicit profile_banner_url.
    for match in re.finditer(r'https:\\/\\/pbs\\.twimg\\.com\\/profile_banners\\/[^"\s<>\\]+',page):
        url = match.group().replace("\\/","/")
        if valid_image(url):
            records.append(("banner", "x", url))
    return list(dict.fromkeys(records))

def discover(username, session=None):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        return []
    session = session or requests.Session()
    for template in PUBLIC_PAGES:
        url = template.format(username=username)
        try:
            response = session.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "text/html"}, allow_redirects=True)
            response.raise_for_status()
            domain = urlparse(response.url).hostname or ""
            if domain not in ("x.com", "www.sotwe.com", "w.twstalker.com"):
                continue
            found = extract(response.text[:1_500_000],username,domain)
            if found:
                logging.info("Public X metadata for @%s via %s: %d",username,domain,len(found))
                return found
        except (requests.RequestException, ValueError) as exc:
            logging.warning("Public X profile unavailable for @%s (%s): %s",username,url,exc)
    logging.warning("No verified public X profile images for @%s",username)
    return []
