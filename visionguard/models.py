"""Offline integrity checks before loading model files."""
import hashlib
import json
from pathlib import Path


def verify_models(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if len(manifest) != 3:
        raise RuntimeError("Model manifest must contain all three required models")
    for item in manifest:
        path = directory / item["file"]
        if path.parent != directory or not path.is_file():
            raise RuntimeError(f"Required model is missing: {item['file']}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != item["sha256"]:
            raise RuntimeError(f"Model integrity failed: {item['file']}")
