"""GitHub-hosted guild banner scheduler.

Discord credentials stay on the GitHub runner; only selected JPEGs and non-secret
metadata cross SSH. This does not modify the existing webhook alert service.
"""
import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def slot_label():
    now = datetime.now(KST)
    event = os.getenv("GITHUB_EVENT_NAME", "")
    schedule = os.getenv("GITHUB_SCHEDULE", "")
    if event == "schedule":
        target_hour = {
            "25,45 23 * * *": 8,
            "25,45 9 * * *": 18,
        }.get(schedule)
        if target_hour is None:
            raise RuntimeError("Unknown scheduled rotation slot")
        # GitHub scheduling can be delayed. Skip stale events instead of
        # unexpectedly changing the server's banner much later in the day.
        elapsed = (now.hour * 60 + now.minute) - target_hour * 60
        if not 0 <= elapsed <= 90:
            return None
        return now.strftime("%Y-%m-%d") + f" {target_hour:02d}:00"
    return "manual:" + now.strftime("%Y-%m-%dT%H:%M:%S")


def run_cmd(args, *, timeout=90):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def main():
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    host = os.getenv("SSH_HOST", "").strip()
    user = os.getenv("SSH_USER", "").strip()
    key = str(Path.home() / ".ssh" / "oracle_key")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if not token or not guild.isdigit():
        raise RuntimeError("Missing Discord secrets on GitHub Actions")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        raise RuntimeError("Invalid SSH host")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise RuntimeError("Invalid SSH user")
    if not run_id.isdigit():
        raise RuntimeError("Invalid GitHub run ID")
    slot = slot_label()
    if slot is None:
        print("GUILD_BANNER_ROTATION skipped_stale_schedule")
        return
    remote = f"{user}@{host}"
    prefix = ["ssh", "-i", key, "-o", "IdentitiesOnly=yes", remote]
    root = "/home/ubuntu/chzzk-bot"
    boot = f"cd {root} && if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv-test/bin/python; fi; "
    state_code = "import json;from guild_server_banner import read_state; print(json.dumps(read_state()))"
    prior = json.loads(run_cmd(prefix + [boot + '"$PY" -c ' + shlex.quote(state_code)] ) or "{}")
    if prior.get("last_slot") == slot:
        if prior.get("info_card_pending"):
            from guild_banner_sync_card import main as repair_card
            repair_card()
            print("GUILD_BANNER_ROTATION info_card_repaired")
        else:
            print("GUILD_BANNER_ROTATION already_rotated")
        return
    image_file = f"/tmp/abyss-guild-rotation-{run_id}.jpg"
    try:
        cmd = boot + f'SERVER_BANNER_USE_COLLECTED=1 SERVER_BANNER_ALLOW_FIT=1 "$PY" guild_server_banner.py --output {image_file}'
        output = run_cmd(prefix + [cmd])
        result = json.loads(output.splitlines()[-1])
        if result.get("status") != "preview_only":
            raise RuntimeError("No verified local banner candidate: " + str(result.get("status")))
        with tempfile.TemporaryDirectory(prefix="abyss-guild-banner-") as work:
            local = Path(work) / "banner.jpg"
            run_cmd(["scp", "-i", key, "-o", "IdentitiesOnly=yes",
                     f"{remote}:{image_file}", str(local)])
            image = local.read_bytes()
            if not 0 < len(image) <= 8 * 1024 * 1024:
                raise RuntimeError("Invalid banner image byte size")
            from PIL import Image
            with Image.open(local) as img:
                if img.size != (960, 540) or img.format != "JPEG":
                    raise RuntimeError("Invalid banner image type or resolution")
            encoded = base64.b64encode(image).decode("ascii")
            data = json.dumps({"banner": "data:image/jpeg;base64," + encoded}).encode("utf-8")
            request = urllib.request.Request(
                f"https://discord.com/api/v10/guilds/{guild}",
                data=data,
                headers={"Authorization": "Bot " + token,
                         "Content-Type": "application/json",
                         "User-Agent": "abyss-guild-banner/1.0"},
                method="PATCH")
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status not in (200, 204):
                        raise RuntimeError("Discord rejected banner update")
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"Discord banner update HTTP {exc.code}") from None
        new_state = {
            **prior,
            "last_slot": slot,
            "last_sha256": result["sha256"],
            "artist": result.get("artist"),
            "post_url": result.get("post_url"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "rotation_used": result.get("rotation_used", []),
            "candidate_total": result.get("candidate_total", 0),
            "remaining_after": result.get("remaining_after", 0),
            "info_card_pending": True,
        }
        state = base64.urlsafe_b64encode(json.dumps(new_state).encode("utf-8")).decode("ascii")
        update_code = ("import base64,json,sys;from guild_server_banner import write_state;"
                       "write_state(json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode('utf-8')))")
        run_cmd(prefix + [boot + '"$PY" -c ' + shlex.quote(update_code) + ' ' + shlex.quote(state)])
        # The banner has already changed. Keep the rotation state even if
        # the informational card is not writable in its designated channel.
        card_status = "not_configured"
        try:
            from guild_banner_info_card import upsert_card
            new_id, card_status = upsert_card(token, guild, image, new_state,
                                              prior.get("info_message_id"))
            new_state["info_message_id"] = new_id
            new_state["info_card_pending"] = False
            new_state["info_channel_id"] = "1567561255764500551"
            encoded_state = base64.urlsafe_b64encode(
                json.dumps(new_state).encode("utf-8")).decode("ascii")
            run_cmd(prefix + [boot + '"$PY" -c ' + shlex.quote(update_code) + ' ' + shlex.quote(encoded_state)])
        except Exception as exc:
            card_status = "failed:" + type(exc).__name__
            print("GUILD_BANNER_CARD_WARNING " + card_status)
        print(json.dumps({"status": "changed", "slot": slot,
                          "artist": result.get("artist"), "post_url": result.get("post_url"),
                          "info_card": card_status}, ensure_ascii=False))
    finally:
        try:
            run_cmd(prefix + ["rm -f " + shlex.quote(image_file)], timeout=15)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass


if __name__ == "__main__":
    main()
