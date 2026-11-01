"""Fetch licensed, pinned nominal robot assets; no scene instances or answers."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def fetch(manifest, destination):
    manifest = json.loads(Path(manifest).read_text())
    destination = Path(destination)
    for item in manifest["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe asset path")
        target = destination/relative
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            continue
        url = item.get("url") or f'https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/{manifest["revision"]}/{relative.as_posix()}'
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"asset checksum mismatch: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (destination/"provenance.json").write_text(json.dumps(manifest, indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    fetch(args.manifest, args.destination)
