"""Review discovered X fanart without confusing a hashtag with permission to reuse."""
import argparse
import json
import shutil
from pathlib import Path
from fanart_discovery import BASE, QUEUE, read, write
from fanart_library import INCOMING, ingest

def summary():
    queue=read(QUEUE)
    result={"total":len(queue),"pending":0,"blocked":0,"approved":0,"cached":0}
    for item in queue.values():
        status=item.get("review_status","pending")
        if status in result:result[status]+=1
        if item.get("cached_files"):result["cached"]+=1
    return result

def approve(key, permission_confirmed=False):
    if not permission_confirmed:
        raise ValueError("Explicit artist permission confirmation required")
    queue=read(QUEUE)
    item=queue.get(key)
    if not isinstance(item,dict) or item.get("review_status")=="blocked":
        raise ValueError("Candidate not found or blocked")
    paths=item.get("cached_files") or []
    if not paths:
        raise ValueError("Candidate image has not been cached")
    group=item.get("group")
    if group not in ("hanayo","sakihane"):raise ValueError("Invalid group")
    INCOMING.mkdir(parents=True,exist_ok=True)
    for i,rel in enumerate(paths[:4]):
        source=(BASE/rel).resolve()
        if BASE.resolve() not in source.parents or not source.is_file() or not source.is_relative_to(BASE/"fanart_candidates"):
            raise ValueError("Invalid candidate path")
        name=group+"_"+str(item["post_id"])+"_"+str(i)
        dest=INCOMING/(name+source.suffix)
        if not dest.is_file(): shutil.copyfile(source,dest)
        metadata={"group":group,"hashtag":item["hashtag"],"post_url":item["post_url"],
                  "artist":item["artist"],"permission_confirmed":True,"status":"approved"}
        (INCOMING/(name+".json")).write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    result=ingest()
    if result["imported"]==0 and result["rejected"]:
        raise ValueError("Image import rejected")
    item["review_status"]="approved"
    item["permission_confirmed"]=True
    write(QUEUE,queue)
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--approve",metavar="GROUP:POST_ID")
    p.add_argument("--permission-confirmed",action="store_true")
    args=p.parse_args()
    if args.approve:
        print(json.dumps(approve(args.approve,args.permission_confirmed),ensure_ascii=False))
    else:
        print(json.dumps(summary(),ensure_ascii=False))
