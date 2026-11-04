"""One shot for the systemd timer: only rotate at 08:00 or 18:00 KST."""
import json
from guild_server_banner import rotate_once

def main():
    result = rotate_once(manual=False)
    print("GUILD_BANNER_SLOT " + json.dumps(result, ensure_ascii=False))
    if result["status"] in {"missing_discord_credentials"}:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
