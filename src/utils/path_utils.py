import os
import shutil
from pathlib import Path

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

def copy_file(src: str | Path, dest: str | Path) -> bool:
    try:
        src_path = Path(src)
        dest_path = Path(dest)
        if not src_path.exists():
            return False
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return True
    except Exception:
        return False
