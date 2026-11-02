"""Independent six-hour official profile refresh; no fanart and no banner writes."""
import json,logging
from pathlib import Path
import requests
from collect_creator_media import (ACCOUNTS,read_index,save_index,download_image,
    sources_youtube,sources_chzzk,sources_x,sources_x_free,sources_configured)
import os

def run():
    logging.basicConfig(level=logging.INFO)
    index=read_index()
    session=requests.Session()
    chzzk=sources_chzzk(session)
    added=0
    counts={}
    for group in ACCOUNTS:
        all_sources=(sources_youtube(group)+chzzk[group]+sources_x(group,session)
             +(sources_x_free(group,session) if os.getenv("ABYSS_PUBLIC_X_DISCOVERY","0")=="1" else [])
             +sources_configured(group))
        for kind,source,url in all_sources:
            if kind!="profile":continue
            added+=int(download_image(group,"profile",source,url,index,session))
        # Eliminate non-official candidates from profile index, retaining old verified platform history.
        entries=index.setdefault(group,{}).get("profile",[])
        allowed={"youtube","chzzk","x","cafe"}
        entries=[x for x in entries if isinstance(x,dict) and x.get("source") in allowed
                 and 0.75<=float(x.get("width",0))/max(float(x.get("height",1)),1)<=1.33]
        index[group]["profile"]=entries
        counts[group]={"profiles":len(entries),"sources":sorted({x.get("source") for x in entries})}
    save_index(index)
    print(json.dumps({"new_official_profiles":added,"counts":counts},ensure_ascii=False))
    return counts

if __name__=="__main__":
    run()
