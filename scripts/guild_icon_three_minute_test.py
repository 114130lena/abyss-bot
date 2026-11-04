"""One controlled three-minute Discord guild icon test with automatic rollback.

No fanart is posted or republished in this smoke test. A purple outline is added
to the CURRENT server icon, making a visible temporary difference while keeping
its original design. The original icon is backed up on the Ubuntu host and
restored in a finally block, including if a later operation fails.
"""
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageOps

API = "https://discord.com/api/v10"
BACKUP = "/home/ubuntu/chzzk-bot/creator_media/guild_icon_original_backup.png"

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def main():
    token=os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild=os.getenv("DISCORD_GUILD_ID", "").strip()
    host=os.getenv("SSH_HOST", "").strip()
    user=os.getenv("SSH_USER", "").strip()
    require(bool(token) and guild.isdigit(), "Missing Discord bot credentials")
    require(bool(re.fullmatch(r"[a-zA-Z0-9_.:-]+",host)), "Invalid SSH host")
    require(bool(re.fullmatch(r"[a-z_][a-z0-9_-]*",user)), "Invalid SSH user")
    key=str(Path.home()/".ssh"/"oracle_key")
    session=requests.Session()
    session.headers.update({"Authorization":"Bot "+token, "User-Agent":"abyss-guild-icon-restore-test/1.0"})
    def get_guild():
        response=session.get(API+"/guilds/"+guild,timeout=18)
        response.raise_for_status()
        return response.json()
    def change_icon(raw,mime):
        body={"icon":"data:"+mime+";base64,"+base64.b64encode(raw).decode("ascii")}
        for attempt in range(5):
            response=session.patch(API+"/guilds/"+guild,json=body,timeout=35)
            if response.status_code==429:
                try:
                    seconds=float(response.json().get("retry_after",response.headers.get("Retry-After",1)))
                except (ValueError,TypeError):
                    seconds=1.0
                print("GUILD_ICON_RATE_LIMIT retry_after_seconds="+str(round(seconds,1)),flush=True)
                time.sleep(max(1,min(seconds,600))+1)
                continue
            response.raise_for_status()
            return
        raise RuntimeError("Guild icon update was rate-limited repeatedly")
    original=get_guild()
    icon_hash=original.get("icon")
    require(bool(icon_hash), "Server has no original icon; canceling test")
    require(not str(icon_hash).startswith("a_"), "Original is animated; canceling test to preserve exact animation")
    url=f"https://cdn.discordapp.com/icons/{guild}/{icon_hash}.png?size=1024"
    download=requests.get(url,timeout=30)
    download.raise_for_status()
    with Image.open(io.BytesIO(download.content)) as im:
        original_image=im.convert("RGBA")
        if original_image.size!=(512,512):
            original_image=ImageOps.fit(original_image,(512,512),method=Image.Resampling.LANCZOS)
        original_io=io.BytesIO()
        original_image.save(original_io,format="PNG")
        original_bytes=original_io.getvalue()
        test_icon=original_image.copy()
    # Add a prominent but reversible colored ring without replacing the art.
    draw=ImageDraw.Draw(test_icon)
    draw.ellipse((12,12,500,500),outline=(176,99,255,255),width=24)
    temporary_io=io.BytesIO()
    test_icon.save(temporary_io,format="PNG")
    temporary_bytes=temporary_io.getvalue()
    require(temporary_bytes!=original_bytes,"Temporary icon did not change")
    print("GUILD_ICON_BACKUP_CAPTURED original_hash="+icon_hash[:8]+"...",flush=True)
    # Keep a private restoration file on the existing server for manual recovery.
    remote=f"{user}@{host}"
    local=Path("/tmp/abyss-guild-original-icon.png")
    local.write_bytes(original_bytes)
    subprocess.run(["ssh","-i",key,"-o","IdentitiesOnly=yes",remote,
        "umask 077; mkdir -p /home/ubuntu/chzzk-bot/creator_media; "
        "test -f "+BACKUP+" || touch "+BACKUP],check=True,timeout=30)
    subprocess.run(["scp","-i",key,"-o","IdentitiesOnly=yes",str(local),
        f"{remote}:/tmp/abyss-guild-icon-restore-candidate.png"],check=True,timeout=35)
    subprocess.run(["ssh","-i",key,"-o","IdentitiesOnly=yes",remote,
        "if [ ! -s "+BACKUP+" ]; then "
        "mv /tmp/abyss-guild-icon-restore-candidate.png "+BACKUP+"; "
        "chmod 600 "+BACKUP+"; else "
        "rm -f /tmp/abyss-guild-icon-restore-candidate.png; fi"],check=True,timeout=30)
    print("GUILD_ICON_BACKUP_SAVED",flush=True)
    changed=False
    new_icon=None
    try:
        changed=True  # Restore even if the first PATCH times out after being applied.
        change_icon(temporary_bytes,"image/png")
        new_icon=get_guild().get("icon")
        require(new_icon and new_icon != icon_hash,"Temporary guild icon not observed")
        print("GUILD_ICON_TEMPORARY_APPLIED waiting_seconds=180",flush=True)
        time.sleep(180)
    finally:
        if changed:
            for attempt in range(3):
                try:
                    change_icon(original_bytes,"image/png")
                    restored=get_guild().get("icon")
                    require(restored and restored!=new_icon,"Original guild icon restoration not observed")
                    print("GUILD_ICON_ORIGINAL_RESTORED confirmed=true",flush=True)
                    break
                except Exception as exc:
                    print("GUILD_ICON_RESTORE_RETRY attempt="+str(attempt+1)+" error="+type(exc).__name__,flush=True)
                    if attempt==2:
                        print("GUILD_ICON_RESTORE_FAILED backup_file="+BACKUP,flush=True)
                        raise
                    time.sleep(12)

if __name__=="__main__":
    main()
