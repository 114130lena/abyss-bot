"""Bound X repost media waits without losing original links or media guards.

Runs against files present in Oracle bot working directory. Idempotent and
fails closed if the expected production code shape changes.
"""
from pathlib import Path
import ast
import sys

def replace_once(text,old,new,label):
    if text.count(old)!=1:
        raise RuntimeError(f"{label} expected one match, got {text.count(old)}")
    return text.replace(old,new,1)

def change_media(text):
    if "X_FAST_REPOST_MEDIA_V1" in text:
        return text
    text=replace_once(text,"for attempt in range(3):\n            response=requests.get(endpoint,timeout=(2.5,4),",
       "for attempt in range(1):\n            response=requests.get(endpoint,timeout=(2,3),","resolver request cap")
    text=replace_once(text,'            if attempt<2:\n                time.sleep(2)\n',
       '            # X_FAST_REPOST_MEDIA_V1: do not wait for missing metadata in notification path.\n',"resolver retry wait")
    return text

def change_resilience(text):
    if "X_SEND_PHASE_TIMING_V1" in text:
        return text
    text=replace_once(text,"with requests.get(source, stream=True, timeout=12, allow_redirects=False) as res:",
      "with requests.get(source, stream=True, timeout=(2,4), allow_redirects=False) as res:","X photo fetch timeout")
    text=replace_once(text,'    if not url.startswith("https://"):\n        raise ValueError("Invalid Discord webhook URL")',
      '    # X_SEND_PHASE_TIMING_V1: measure media preparation and Discord network.\n    started_total=time.monotonic()\n    if not url.startswith("https://"):\n        raise ValueError("Invalid Discord webhook URL")',"webhook start")
    text=replace_once(text,'            x_photos = _collect_x_image_attachments(prepared)',
      '            x_photos = _collect_x_image_attachments(prepared)\n            preparation_seconds=time.monotonic()-started_total\n            http_started=time.monotonic()',"media preparation duration")
    text=replace_once(text,'            if response.status_code not in (429, 500, 502, 503, 504):',
      '            http_seconds=time.monotonic()-http_started\n            if response.status_code not in (429, 500, 502, 503, 504):',"HTTP duration")
    text=replace_once(text,'                if selected_urls:\n                    try:',
      '                if url and "thread_id=" in url or any(" X" in str(c.get("content","")) for container in prepared.get("components",[]) for c in container.get("components",[]) if isinstance(c,dict)):\n                    logging.info("X_DELIVERY_PHASES preparation_seconds=%.2f http_seconds=%.2f total_seconds=%.2f photo_uploads=%d",preparation_seconds,http_seconds,time.monotonic()-started_total,len(x_photos))\n                if selected_urls:\n                    try:',"phase logging")
    return text

def change_repost(text):
    if "X_REPOST_E2E_LATENCY_V1" in text:
        return text
    text=replace_once(text,'def notify(group,post,webhook):\n    creator=',
      'def notify(group,post,webhook):\n    # X_REPOST_E2E_LATENCY_V1 includes media lookup, unlike existing delivery_seconds.\n    overall_started=time.monotonic()\n    creator=',"end to end start")
    text=replace_once(text,'    if match:\n        found,video_preview,has_video,photo_known=resolve_media(',
      '    if match:\n        media_started=time.monotonic()\n        found,video_preview,has_video,photo_known=resolve_media(',"media timer")
    text=replace_once(text,'        if found:resolved_images=found',
      '        media_seconds=time.monotonic()-media_started\n        if found:resolved_images=found',"media duration")
    text=replace_once(text,'    logging.info("X_REPOST_DELIVERED creator=%s id=%s source=%s delivery_seconds=%.2f",group,post.get("id",""),post.get("source","?"),time.monotonic()-started)',
      '    logging.info("X_REPOST_DELIVERED creator=%s id=%s source=%s delivery_seconds=%.2f",group,post.get("id",""),post.get("source","?"),time.monotonic()-started)\n    logging.info("X_REPOST_E2E creator=%s id=%s media_seconds=%.2f webhook_seconds=%.2f total_seconds=%.2f",group,post.get("id",""),locals().get("media_seconds",0.0),time.monotonic()-started,time.monotonic()-overall_started)',"end to end log")
    return text

TRANSFORMS={"x_post_media.py":change_media,"resilience.py":change_resilience,"x_repost_notifier.py":change_repost}
def run(root,write):
    for name,trans in TRANSFORMS.items():
        path=Path(root)/name
        original=path.read_text(encoding="utf-8")
        revised=trans(original)
        ast.parse(revised,filename=name)
        if write and revised!=original:
            path.write_text(revised,encoding="utf-8")
        print("X_OPTIMIZE",name,"changed",revised!=original,"valid",True,flush=True)

if __name__=="__main__":
    run(sys.argv[1] if len(sys.argv)>1 else ".", "--write" in sys.argv)
