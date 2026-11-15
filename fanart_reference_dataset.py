"""Build a local, reviewable reference-image manifest without guessing identities.

Usage:
  python fanart_reference_dataset.py build
  python fanart_reference_dataset.py label --post-id 123 --image 0 --label hanayo
  python fanart_reference_dataset.py label --post-id 123 --image 0 --label other
  python fanart_reference_dataset.py summary

No networking, Discord edits, bot configuration changes, or image inference.
Each attachment gets its own record. Hashes prevent duplicate references.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ALLOWED = {"hanayo", "sakihane", "other", "uncertain"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _read(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def build(base=None):
    from fanart_discovery import BASE
    root = Path(base or BASE).resolve()
    queue = _read(root / "fanart_discovery.json")
    destination = root / "fanart_reference_dataset.json"
    previous = _read(destination)
    records = dict(previous.get("records", {})) if isinstance(previous.get("records"), dict) else {}
    count = 0
    for key, post in queue.items():
        if not isinstance(post, dict) or post.get("group") not in ("hanayo", "sakihane"):
            continue
        post_id = str(post.get("post_id") or key.rsplit(":", 1)[-1])
        if not post_id.isdigit():
            continue
        for index, relative in enumerate(post.get("cached_files", [])[:4]):
            if not isinstance(relative, str):
                continue
            path = (root / relative).resolve()
            if (root not in path.parents or not path.is_file()
                    or path.suffix.lower() not in EXTENSIONS):
                continue
            record_id = f"{post_id}:{index}"
            digest = _digest(path)
            old = records.get(record_id)
            # Any replaced media becomes unverified, even at the same index.
            retained_label = (old.get("label") if isinstance(old, dict)
                              and old.get("sha256") == digest
                              and old.get("label") in ALLOWED else "uncertain")
            records[record_id] = {
                "post_id": post_id, "image_index": index,
                "source_group": post["group"], "post_url": str(post.get("post_url") or ""),
                "relative_path": relative, "sha256": digest, "label": retained_label,
                "label_source": "human" if retained_label != "uncertain" else "unverified",
            }
            count += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    _save(destination, {"schema_version": 1, "records": records})
    return {"path": str(destination), "scanned": count,
            "total": len(records), "labeled": sum(
                v.get("label") in ("hanayo", "sakihane", "other") for v in records.values())}


def _save(destination, value):
    temp = destination.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(destination)


def label(post_id, index, value, base=None):
    if value not in ALLOWED or not str(post_id).isdigit() or index < 0 or index > 3:
        raise ValueError("Invalid label, post ID, or image index")
    from fanart_discovery import BASE
    path = Path(base or BASE) / "fanart_reference_dataset.json"
    dataset = _read(path)
    records = dataset.get("records", {})
    key = f"{post_id}:{index}"
    if not isinstance(records, dict) or key not in records:
        raise ValueError("Run build first; reference image not found")
    record = records[key]
    image = (Path(base or BASE).resolve() / record["relative_path"]).resolve()
    if not image.is_file() or _digest(image) != record.get("sha256"):
        raise ValueError("Source image changed; rebuild and inspect before labeling")
    record["label"] = value
    record["label_source"] = "human" if value != "uncertain" else "unverified"
    record["labeled_at"] = datetime.now(timezone.utc).isoformat()
    _save(path, dataset)
    return record


def summary(base=None):
    from fanart_discovery import BASE
    records = _read(Path(base or BASE) / "fanart_reference_dataset.json").get("records", {})
    counts = {name: 0 for name in sorted(ALLOWED)}
    if isinstance(records, dict):
        for record in records.values():
            if isinstance(record, dict):
                counts[record.get("label", "uncertain")] = counts.get(record.get("label", "uncertain"), 0) + 1
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "label", "summary"))
    parser.add_argument("--post-id", default="")
    parser.add_argument("--image", type=int, default=0)
    parser.add_argument("--label", choices=sorted(ALLOWED))
    args = parser.parse_args()
    if args.command == "build":
        result = build()
    elif args.command == "label":
        if not args.label:
            parser.error("--label is required for label")
        result = label(args.post_id, args.image, args.label)
    else:
        result = summary()
    print(json.dumps(result, ensure_ascii=False))
