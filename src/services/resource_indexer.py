import hashlib
import json
import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional

INDEX_DIR_NAME = "resource_index"


@dataclass
class ResourceEntry:
    filename: str
    size: int
    modified: float
    sha256: str
    file_type: str  # "hero", "activity_spine", "activity_pack", "other"


@dataclass
class ResourceIndex:
    folder: str
    created_at: str
    file_count: int
    total_size: int
    entries: list[ResourceEntry]


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def classify_file(filename: str) -> str:
    if re.match(r"^roles_showroles_spine_hero\d+", filename):
        return "hero"
    if re.match(r"^roles_showroles_spine_activity_", filename):
        return "activity_spine"
    if re.match(r"^activities_packs_", filename):
        return "activity_pack"
    if re.match(r"^activities_", filename):
        return "activity_pack"
    return "other"


def build_index(folder: str, output_root: str) -> ResourceIndex:
    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")

    entries = []
    for file_path in sorted(folder_path.iterdir()):
        if not file_path.is_file() or not file_path.name.endswith(".ab"):
            continue
        entry = ResourceEntry(
            filename=file_path.name,
            size=file_path.stat().st_size,
            modified=file_path.stat().st_mtime,
            sha256=compute_sha256(file_path),
            file_type=classify_file(file_path.name),
        )
        entries.append(entry)

    idx = ResourceIndex(
        folder=str(folder_path.resolve()),
        created_at=datetime.now().isoformat(),
        file_count=len(entries),
        total_size=sum(e.size for e in entries),
        entries=entries,
    )

    index_dir = Path(output_root) / INDEX_DIR_NAME
    index_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    index_file = index_dir / f"index_{ts}.json"
    index_file.write_text(json.dumps(asdict(idx), indent=2, ensure_ascii=False), encoding="utf-8")

    return idx


def load_index(index_path: str) -> ResourceIndex:
    data = json.loads(Path(index_path).read_text(encoding="utf-8"))
    entries = [ResourceEntry(**e) for e in data["entries"]]
    return ResourceIndex(
        folder=data["folder"],
        created_at=data["created_at"],
        file_count=data["file_count"],
        total_size=data["total_size"],
        entries=entries,
    )


def load_latest_index(output_root: str) -> Optional[ResourceIndex]:
    index_dir = Path(output_root) / INDEX_DIR_NAME
    if not index_dir.exists():
        return None
    files = sorted(index_dir.glob("index_*.json"), reverse=True)
    if not files:
        return None
    return load_index(str(files[0]))


def list_indexes(output_root: str) -> list[dict]:
    index_dir = Path(output_root) / INDEX_DIR_NAME
    if not index_dir.exists():
        return []
    results = []
    for f in sorted(index_dir.glob("index_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "filename": f.name,
                "created_at": data.get("created_at", ""),
                "folder": data.get("folder", ""),
                "file_count": data.get("file_count", 0),
                "total_size": data.get("total_size", 0),
            })
        except Exception:
            pass
    return results


def diff_indexes(old_index: ResourceIndex, new_index: ResourceIndex) -> dict:
    old_map = {e.filename: e for e in old_index.entries}
    new_map = {e.filename: e for e in new_index.entries}

    new_files = []
    modified_files = []
    removed_files = []
    unchanged_files = []

    for name, new_entry in new_map.items():
        if name not in old_map:
            new_files.append({
                "filename": name,
                "size": new_entry.size,
                "sha256": new_entry.sha256,
                "file_type": new_entry.file_type,
            })
        elif new_entry.sha256 != old_map[name].sha256:
            modified_files.append({
                "filename": name,
                "old_size": old_map[name].size,
                "new_size": new_entry.size,
                "old_sha256": old_map[name].sha256,
                "new_sha256": new_entry.sha256,
                "file_type": new_entry.file_type,
            })
        else:
            unchanged_files.append({
                "filename": name,
                "size": new_entry.size,
                "file_type": new_entry.file_type,
            })

    for name in old_map:
        if name not in new_map:
            removed_files.append({
                "filename": name,
                "size": old_map[name].size,
                "file_type": old_map[name].file_type,
            })

    def sort_key(f):
        return f["filename"]

    return {
        "old_folder": old_index.folder,
        "new_folder": new_index.folder,
        "old_total": len(old_index.entries),
        "new_total": len(new_index.entries),
        "new_files": sorted(new_files, key=sort_key),
        "modified_files": sorted(modified_files, key=sort_key),
        "removed_files": sorted(removed_files, key=sort_key),
        "unchanged_files": sorted(unchanged_files, key=sort_key),
        "summary": {
            "new_count": len(new_files),
            "modified_count": len(modified_files),
            "removed_count": len(removed_files),
            "unchanged_count": len(unchanged_files),
        },
    }