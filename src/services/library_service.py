"""Static media previews and recoverable removal of generated hero outputs."""
import datetime
import hashlib
import json
import re
import shutil
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


def static_thumbnail(root: Path, source: str, full_size: bool = False) -> Path:
    root = root.resolve()
    gif = resolve_gif(root, source)
    stat = gif.stat()
    key = hashlib.sha256(f"{gif}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    cache_dir = _contained(root, root / "temp" / ("library_images" if full_size else "library_thumbnails"))
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
            if not full_size:
                frame.thumbnail((256, 256), Image.Resampling.LANCZOS)
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_dir / f"{key}-{uuid.uuid4().hex}.tmp"
        try:
            frame.save(temporary, format="PNG")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def trash_hero(root: Path, hero_id: str, library_root: Path | None = None) -> dict:
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
    saved = None
    if library_root is not None:
        library_root = library_root.resolve()
        saved = _contained(library_root, library_root / "heroes" / hero_id)
        if saved.is_dir():
            sources.insert(0, saved)
    if not sources:
        raise FileNotFoundError("Hero not found")
    trash = _contained(root, root / "trash")
    batch = trash / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{hero_id}_{uuid.uuid4().hex[:8]}"
    batch.mkdir(parents=True)
    moved = []
    try:
        for source in sources:
            relative = Path("library") / source.relative_to(library_root) if source == saved else source.relative_to(root)
            destination = batch / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved.append((source, destination))
        (batch / "manifest.json").write_text(json.dumps({
            "hero_id": hero_id,
            "original_paths": [str(Path("library") / source.relative_to(library_root)) if source == saved else str(source.relative_to(root)) for source, _ in moved],
            "output_root": str(root),
            "library_root": str(library_root) if library_root else None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Restore completed moves if a later file is locked or a write fails.
        for source, destination in reversed(moved):
            destination.rename(source)
        raise
    return {"status": "ok", "deleted": hero_id, "moved_count": len(moved), "trash_path": str(batch)}


def library_root_for(config) -> Path:
    return Path(getattr(config, "library_root", Path(config.output_root).resolve().parent / "library")).resolve()


def validate_tree(root: Path, directory: Path) -> Path:
    """Validate every descendant before recursive copying, moving or deleting."""
    directory = _contained(root.resolve(), directory.absolute())
    for path in directory.rglob("*"):
        _contained(root.resolve(), path)
    return directory


def migrate_library(output: Path, library: Path) -> dict:
    output, library = output.resolve(), library.resolve()
    if library == output or library.is_relative_to(output) or output.is_relative_to(library):
        raise ValueError("Library and output roots must be separate directories")
    legacy = _contained(output, output / "heroes")
    destination = _contained(library, library / "heroes")
    destination.mkdir(parents=True, exist_ok=True)
    moved, conflicts = [], []
    candidates = sorted(legacy.iterdir()) if legacy.is_dir() else []
    for hero in candidates:
        if not hero.is_dir():
            continue
        validate_tree(output, hero)
        target = _contained(library, destination / hero.name)
        if target.exists():
            conflicts.append(hero.name)  # Keep both copies; never overwrite a saved hero.
            continue
        hero.rename(target)
        moved.append(hero.name)
    # Old saved entries sometimes only have matched assets and display a run's GIF.
    # Make those GIFs durable before the user can clean the runs.
    runs = _contained(output, output / "runs")
    for hero in destination.iterdir():
        if not hero.is_dir() or any((hero / "gif").rglob("*.gif")):
            continue
        for run in sorted(runs.iterdir(), reverse=True) if runs.is_dir() else []:
            source = run / "heroes" / hero.name / "gif"
            if source.is_dir() and any(source.rglob("*.gif")):
                validate_tree(output, source)
                target = _contained(library, hero / "gif")
                validate_tree(library, target)
                staging = _contained(library, hero / f".gif_migration_{uuid.uuid4().hex}")
                previous = _contained(library, hero / f".gif_previous_{uuid.uuid4().hex}")
                try:
                    shutil.copytree(source, staging)
                    if target.exists():
                        target.rename(previous)
                    try:
                        staging.rename(target)
                    except OSError:
                        if previous.exists():
                            previous.rename(target)
                        raise
                finally:
                    if staging.exists():
                        shutil.rmtree(validate_tree(library, staging))
                break
    return {"moved": moved, "conflicts": conflicts}


def clean_generated(output: Path, library: Path) -> dict:
    """Delete runs/temp only, after validating both trees. Saved heroes are protected."""
    output, library = output.resolve(), library.resolve()
    migration = migrate_library(output, library)
    sources = [validate_tree(output, output / name) for name in ("runs", "temp") if (output / name).exists()]
    removed = []
    for source in sources:
        shutil.rmtree(source)
        removed.append(source.name)
    return {"status": "ok", "removed": removed, "migration": migration}


def clean_intermediates(output: Path, hero_id: str | None = None) -> list[str]:
    if hero_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", hero_id):
        raise ValueError("Invalid hero ID")
    output = output.resolve()
    runs = _contained(output, output / "runs")
    sources = []
    for run in runs.iterdir() if runs.is_dir() else []:
        heroes = _contained(output, run / "heroes")
        candidates = [heroes / hero_id] if hero_id else (list(heroes.iterdir()) if heroes.is_dir() else [])
        for hero in candidates:
            for name in ("raw_export", "diagnostics"):
                source = hero / name
                if source.exists():
                    sources.append(validate_tree(output, source))
    for source in sources:
        shutil.rmtree(source)
    return [source.relative_to(output).as_posix() for source in sources]
