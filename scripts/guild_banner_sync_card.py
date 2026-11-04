"""Create/update one info card for the guild banner already applied.

Reads the banner's persisted sha256 from Ubuntu and re-renders the same
image. Does NOT call the Discord guild banner modification endpoint.
"""
import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from guild_banner_info_card import upsert_card


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=100).stdout.strip()


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    guild = os.environ.get("DISCORD_GUILD_ID", "")
    host = os.environ.get("SSH_HOST", "")
    user = os.environ.get("SSH_USER", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not token or not guild.isdigit() or not re.fullmatch(r"[a-z_][a-z0-9_-]*",user):
        raise RuntimeError("Missing secrets or SSH user")
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+",host) or not run_id.isdigit():
        raise RuntimeError("Invalid host or run")
    remote = f"{user}@{host}"
    key = str(Path.home()/".ssh"/"oracle_key")
    ssh = ["ssh","-i",key,"-o","IdentitiesOnly=yes",remote]
    base = "/home/ubuntu/chzzk-bot"
    boot = f"cd {base}; if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv-test/bin/python; fi; "
    path = f"/tmp/abyss-banner-card-{run_id}.jpg"
    code = (
        "from pathlib import Path\n"
        "import json,sys\n"
        "from guild_server_banner import read_state,approved_hanayo_art,collected_hanayo_art,convert_banner\n"
        "state=read_state()\n"
        "target=state.get('last_sha256')\n"
        "selected=next(((p,m) for p,m in approved_hanayo_art()+collected_hanayo_art() if m['sha256']==target),None)\n"
        "if not selected: raise SystemExit('Last deployed fanart is missing from local cache')\n"
        "Path(sys.argv[1]).write_bytes(convert_banner(selected[0]))\n"
        "print(json.dumps(state))\n"
    )
    try:
        stdout=run(ssh+[boot+'SERVER_BANNER_USE_COLLECTED=1 SERVER_BANNER_ALLOW_FIT=1 "$PY" -c '+shlex.quote(code)+' '+shlex.quote(path)])
        state=json.loads(stdout.splitlines()[-1])
        with tempfile.TemporaryDirectory(prefix="abyss-info-") as temp:
            local=Path(temp)/"banner.jpg"
            run(["scp","-i",key,"-o","IdentitiesOnly=yes",f"{remote}:{path}",str(local)])
            from PIL import Image
            with Image.open(local) as im:
                if im.format!="JPEG" or im.size != (960,540):
                    raise RuntimeError("Invalid cached current banner")
            new_id,status=upsert_card(token,guild,local.read_bytes(),state,state.get("info_message_id"))
        state["info_message_id"]=new_id
        state["info_card_pending"]=False
        state["info_channel_id"]="1567561255764500551"
        write_code=("import base64,json,sys;from guild_server_banner import write_state;"
                    "write_state(json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode('utf-8')))")
        encoded=base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
        run(ssh+[boot+'"$PY" -c '+shlex.quote(write_code)+' '+shlex.quote(encoded)])
        print(json.dumps({"status":status,"channel_id":state["info_channel_id"],
                          "message_id":new_id,
                          "message_url":f"https://discord.com/channels/{guild}/1567561255764500551/{new_id}"},
                          ensure_ascii=False))
    finally:
        try: run(ssh+["rm -f "+shlex.quote(path)])
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired): pass


if __name__=="__main__":
    main()
