"""Read-only operational checks and conservative cache housekeeping for Abyss."""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
MEDIA = BASE / "creator_media"
CANDIDATES = MEDIA / "fanart_candidates"
MAX_AGE_DAYS = 45

def service_active(name):
    p = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=8)
    return p.returncode == 0 and p.stdout.strip() == "active"

def safe_clean(dry_run=True):
    """Only delete stale, unreferenced candidate cache images; never official or approved art."""
    protected = set()
    for name in ("index.json", "fanart_discovery_queue.json", "fanart_queue.json"):
        p = MEDIA / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        def walk(v):
            if isinstance(v, str) and ("fanart_candidates/" in v):
                protected.add(v.split("fanart_candidates/", 1)[1])
            elif isinstance(v, dict):
                for x in v.values(): walk(x)
            elif isinstance(v, list):
                for x in v: walk(x)
        walk(data)
    summary = {"dry_run": dry_run, "candidates": 0, "deleted": 0, "bytes": 0}
    if not CANDIDATES.exists():
        return summary
    now = time.time()
    for path in sorted(CANDIDATES.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        if path.is_symlink():
            continue
        relative = str(path.relative_to(CANDIDATES))
        if relative in protected or now - path.stat().st_mtime < MAX_AGE_DAYS * 86400:
            continue
        summary["candidates"] += 1
        if summary["deleted"] >= 100 or summary["bytes"] + path.stat().st_size > 500 * 1024 * 1024:
            break
        summary["bytes"] += path.stat().st_size
        if not dry_run:
            path.unlink()
        summary["deleted"] += 1
    return summary

def inspect():
    report = {"checked_at": int(time.time()), "services": {}, "files": {}, "disk": {}, "findings": []}
    for service in ("chzzk-bot.service", "abyss-media-refresh.timer"):
        report["services"][service] = service_active(service)
        if not report["services"][service]:
            report["findings"].append("inactive " + service)
    for filename in ("channels.txt", "live_state.json", "x_seen_posts.json", "creator_media/index.json"):
        p = BASE / filename
        exists = p.is_file()
        age = int(time.time() - p.stat().st_mtime) if exists else None
        report["files"][filename] = {"exists": exists, "age_seconds": age}
        if not exists:
            report["findings"].append("missing " + filename)
    usage = shutil.disk_usage(BASE)
    report["disk"] = {"free_bytes": usage.free, "percent_used": round(100 * usage.used / usage.total, 1)}
    if usage.free < 1024 * 1024 * 1024:
        report["findings"].append("less than 1GiB free")
    report["cache"] = safe_clean(dry_run=True)
    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune", action="store_true", help="Remove only old unreferenced candidate files")
    args = parser.parse_args()
    report = inspect()
    if args.prune:
        report["cleanup"] = safe_clean(dry_run=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["services"].get("chzzk-bot.service"):
        raise SystemExit(2)
