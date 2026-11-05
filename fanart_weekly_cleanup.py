"""Weekly bounded cleanup of only unreferenced fanart candidate-cache copies.

Active artwork, source URLs, post metadata, delivery/rotation histories and
server banner candidates are never removed merely because they are seven days
old. Old backup scripts retired entire posts; this one does not.
"""
import argparse
import json
import os
import time
from pathlib import Path
from fanart_discovery import BASE, QUEUE, read

CACHE = (BASE / "fanart_candidates").resolve()
WEEK = 7 * 86400
MAX_FILES = 200
MAX_BYTES = 512 * 1024 * 1024


def referenced_files():
    refs = set()
    for item in read(QUEUE).values():
        if not isinstance(item, dict):
            continue
        if item.get("review_status") == "blocked" or item.get("decision") == "blocked":
            continue
        for value in item.get("cached_files", []):
            if not isinstance(value, str):
                continue
            path = (BASE / value).resolve()
            if CACHE in path.parents:
                refs.add(path)
    return refs


def cleanup(now=None, dry_run=False):
    now = time.time() if now is None else now
    refs = referenced_files()
    removed = 0
    freed = 0
    examined = 0
    if CACHE.is_dir():
        for path in sorted(CACHE.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            actual = path.resolve()
            if CACHE not in actual.parents:
                continue
            examined += 1
            if actual in refs or now - path.stat().st_mtime < WEEK:
                continue
            size = path.stat().st_size
            if removed >= MAX_FILES or freed + size > MAX_BYTES:
                break
            if not dry_run:
                path.unlink()
            removed += 1
            freed += size
    result = {"dry_run": dry_run, "examined": examined,
              "protected_references": len(refs),
              "deleted_or_would_delete": removed, "bytes": freed,
              "metadata_preserved": len(read(QUEUE))}
    print("FANART_WEEKLY_CLEANUP " + json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
