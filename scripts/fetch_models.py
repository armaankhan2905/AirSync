"""Explicit one-time HTTPS model setup. Runtime never performs downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(destination: Path, verify_only=False):
    manifest = json.loads((ROOT / "models/manifest.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for item in manifest:
        path = destination / item["file"]
        if path.exists() and sha256(path) == item["sha256"]:
            print(f"Verified: {item['file']}")
            continue
        if verify_only:
            raise RuntimeError(f"Missing or invalid model: {path}")
        print(f"Downloading: {item['file']}")
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            request = urllib.request.Request(item["url"], headers={"User-Agent": "VisionGuardAI-Setup/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                if not response.url.startswith("https://"):
                    raise RuntimeError("Model redirect must use HTTPS")
                while block := response.read(1024 * 1024):
                    output.write(block)
            if sha256(temporary) != item["sha256"]:
                raise RuntimeError(f"Model checksum mismatch: {item['file']}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print("Models ready for offline CPU inference.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=ROOT / "models")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    fetch(args.destination, args.verify_only)
