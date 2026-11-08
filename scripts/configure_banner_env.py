"""Atomically apply banner credentials delivered over SSH stdin by GitHub Actions.

Never print credentials; never alter unrelated notification settings.
This file is a deploy-time helper and is not run by the bot itself.
"""
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

ENV_FILE = Path("/home/ubuntu/chzzk-bot/.env")
ALLOWED = {
    "DISCORD_BOT_TOKEN",
    "DISCORD_GUILD_ID",
    "SERVER_BANNER_ENABLED",
    "SERVER_BANNER_USE_COLLECTED",
    "SERVER_BANNER_INFO_CHANNEL_ID",
    "SERVER_BANNER_INFO_MESSAGE_ID",
    "SERVER_BANNER_INFO_POLL_SECONDS",
}
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def update_env(values, path=ENV_FILE):
    if not isinstance(values, dict) or set(values) != ALLOWED:
        raise ValueError("Unexpected banner setting keys")
    for name, value in values.items():
        if not KEY_PATTERN.fullmatch(name) or not isinstance(value, str):
            raise ValueError("Invalid banner setting")
        if not value or any(char in value for char in "\r\n\0"):
            raise ValueError("Empty or multiline banner setting")
    if not values["DISCORD_GUILD_ID"].isdigit():
        raise ValueError("Guild ID must contain digits")
    if not values["SERVER_BANNER_INFO_MESSAGE_ID"].isdigit():
        raise ValueError("Banner info message ID must contain digits")
    if values["SERVER_BANNER_ENABLED"] != "1":
        raise ValueError("Banner feature must be explicitly enabled")
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing .env must be a regular file")
    lines = path.read_text(encoding="utf-8").splitlines()
    output = []
    handled = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in values:
            if key not in handled:
                output.append(key + "=" + values[key])
                handled.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in handled:
            output.append(key + "=" + value)
    fd, temp = tempfile.mkstemp(prefix=".env.banner.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        update_env(data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print("Banner configuration failed without displaying credentials", file=sys.stderr)
        raise SystemExit(1)
    print("Banner settings configured successfully (values hidden)")
