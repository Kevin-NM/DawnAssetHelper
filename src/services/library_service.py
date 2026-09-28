"""Static media previews and recoverable removal of generated hero outputs."""
import datetime
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

from PIL import Image

_thumbnail_lock = threading.Lock()


def _contained(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved != candidate.absolute():
        raise ValueError("Output paths must stay inside the output directory without links")
    return resolved


def resolve_gif(root: Path, source: str) -> Path:
    root = root.resolve()
    relative = Path(source)
    parts = relative.parts
    if relative.is_absolute() or ".." in parts or relative.suffix.lower() != ".gif":
        raise ValueError("Invalid GIF path")
    library = len(parts) >= 4 and parts[0] == "heroes" and parts[2] == "gif"
    run = len(parts) >= 6 and parts[0] == "runs" and parts[2] == "heroes" and parts[4] == "gif"
    if not (library or run):
        raise ValueError("Only Library and Runs GIFs can be previewed")
    source_path = _contained(root, root / relative)
    if not source_path.is_file():
        raise FileNotFoundError("GIF not found")
    return source_path


def static_thumbnail(root: Path, source: str) -> Path:
    root = root.resolve()
    gif = resolve_gif(root, source)
    stat = gif.stat()
    key = hashlib.sha256(f"{gif}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    cache_dir = _contained(root, root / "temp" / "library_thumbnails")
    target = cache_dir / f"{key}.png"
    # Bound decoder memory and avoid duplicate rendering of the same source.
    with _thumbnail_lock:
        if target.is_file():
            return target
        with Image.open(gif) as image:
            try:
                image.seek(10)  # Skip common startup fade-in frames.
            except EOFError:
                image.seek(0)
            frame = image.convert("RGBA")
            frame.thumbnail((256, 256), Image.Resampling.LANCZOS)
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_dir / f"{key}-{uuid.uuid4().hex}.tmp"
        try:
            frame.save(temporary, format="PNG")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def trash_hero(root: Path, hero_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", hero_id):
        raise ValueError("Invalid hero ID")
    root = root.resolve()
    candidates = [root / "heroes" / hero_id]
    runs = _contained(root, root / "runs")
    if runs.is_dir():
        candidates.extend(run / "heroes" / hero_id for run in sorted(runs.iterdir()) if run.is_dir())
    # Validate every source before moving any of them, including Windows junctions.
    sources = [_contained(root, candidate) for candidate in candidates if candidate.exists()]
    sources = [source for source in sources if source.is_dir()]
    if not sources:
        raise FileNotFoundError("Hero not found")
    trash = _contained(root, root / "trash")
    batch = trash / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{hero_id}_{uuid.uuid4().hex[:8]}"
    batch.mkdir(parents=True)
    moved = []
    try:
        for source in sources:
            relative = source.relative_to(root)
            destination = batch / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved.append((source, destination))
        (batch / "manifest.json").write_text(json.dumps({
            "hero_id": hero_id,
            "original_paths": [str(source.relative_to(root)) for source, _ in moved],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Restore completed moves if a later file is locked or a write fails.
        for source, destination in reversed(moved):
            destination.rename(source)
        raise
    return {"status": "ok", "deleted": hero_id, "moved_count": len(moved), "trash_path": str(batch)}
