"""Small, no-clobber evidence and checkpoint primitives."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return sha256(path)


def save_checkpoint(path, value):
    """Publish complete bytes once; never replace a previous checkpoint."""
    import torch
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            torch.save(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
    finally:
        os.unlink(tmp)
    digest = sha256(path)
    save_json(str(path) + ".identity.json", {"path": path.name, "sha256": digest,
              "bytes": path.stat().st_size, "created_utc": utc_now()})
    return digest


def load_checkpoint(path):
    """Load only locally produced, sidecar-hash-verified research state."""
    import torch
    path = Path(path)
    identity = read_json(str(path) + ".identity.json")
    if identity["sha256"] != sha256(path):
        raise ValueError("checkpoint SHA mismatch")
    return torch.load(path, map_location="cpu", weights_only=False)


def manifest_tree(root, exclude=()):
    root = Path(root)
    return [{"path": str(p.relative_to(root)), "sha256": sha256(p),
             "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file()
            and p.name not in exclude and "__pycache__" not in p.parts]
