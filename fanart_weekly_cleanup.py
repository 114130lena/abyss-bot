"""Weekly disk cleanup for unneeded fanart candidate JPEG copies.
Keeps original post metadata and permanent delivered-URL history unchanged.
"""
import json,os,time
from pathlib import Path
from fanart_discovery import BASE,QUEUE,read,write
CACHE=(BASE/"fanart_candidates").resolve()
WEEK=7*86400

def cleanup(now=None):
    now=time.time() if now is None else now
    removed=0;bytes_freed=0
    queue=read(QUEUE)
    for item in queue.values():
        if not isinstance(item,dict):continue
        kept=[]
        expired=False
        for rel in item.get("cached_files",[]):
            if not isinstance(rel,str):continue
            p=(BASE/rel).resolve()
            if CACHE not in p.parents or not p.is_file():continue
            if now-p.stat().st_mtime>=WEEK:
                bytes_freed+=p.stat().st_size
                p.unlink()
                removed+=1
                expired=True
            else:kept.append(rel)
        item["cached_files"]=kept
        if expired and not kept:item["cache_retired"]=True
    write(QUEUE,queue)
    print(json.dumps({"deleted_cache_files":removed,"freed_bytes":bytes_freed,
                      "metadata_kept":len(queue)},ensure_ascii=False))
    return removed

if __name__=="__main__":
    cleanup()
