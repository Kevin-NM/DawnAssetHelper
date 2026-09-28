import os
import sys
import json
import asyncio
import queue
import shutil
import subprocess
import datetime
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Inject PySide6 stubs so orchestrator can load without PySide6
import server.pyside_stubs as _stubs
import sys as _sys
_sys.modules['PySide6'] = type(_sys)('PySide6')
_sys.modules['PySide6.QtCore'] = type(_sys)('PySide6.QtCore')
_sys.modules['PySide6.QtCore'].QObject = _stubs.QObject
_sys.modules['PySide6.QtCore'].Signal = _stubs.Signal
_sys.modules['PySide6.QtCore'].QThread = _stubs.QThread

from src.app_config import AppConfig
from src.services.hero_scanner import HeroScanner
from src.services.orchestrator import OrchestratorWorker
from src.services.logger_service import logger
from src.utils.json_utils import load_json, save_json
from src.services.library_service import (static_thumbnail, trash_hero, library_root_for,
    migrate_library, clean_generated, clean_intermediates, validate_tree, _contained)

app = FastAPI(title="DawnAssetHelper")

# Static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# Output directory (serve GIFs, PNGs etc.)
output_root = Path("./output")
if output_root.exists():
    app.mount("/output", StaticFiles(directory=output_root), name="output")

CONFIG_PATH = Path("config/appsettings.json")

# Global state
active_worker: Optional[OrchestratorWorker] = None
log_clients: list[WebSocket] = []
log_queue: queue.Queue = queue.Queue()
worker_status = {"running": False, "mode": "", "hero_count": 0, "finished": False}
_log_connected = False


def _on_log(text):
    """Thread-safe log handler: puts message into queue for WebSocket broadcast."""
    log_queue.put(text)


def get_config() -> AppConfig:
    config = AppConfig()
    if CONFIG_PATH.exists():
        data = load_json(CONFIG_PATH, {})
        for k, v in data.items():
            if hasattr(config, k):
                setattr(config, k, v)
    return config


def _prepare_library(config):
    library = library_root_for(config)
    if not worker_status["running"] and not (active_worker and active_worker.isRunning()):
        try:
            migrate_library(Path(config.output_root), library)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, f"Library migration failed; original data preserved: {exc}") from exc
    return library


def _media_base(hero_dir):
    config = get_config()
    library = library_root_for(config)
    path = hero_dir.resolve()
    if path.is_relative_to(library):
        return "/library/" + quote(path.relative_to(library).as_posix(), safe="/")
    return "/output/" + quote(path.relative_to(Path(config.output_root).resolve()).as_posix(), safe="/")


@app.get("/library/{source:path}")
def api_saved_media(source: str):
    root = library_root_for(get_config())
    try:
        path = _contained(root, root / source)
        if path.suffix.lower() not in (".gif", ".png") or not path.is_file():
            raise FileNotFoundError()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


# Background task: broadcast log_queue to all WebSocket clients
async def log_broadcaster():
    while True:
        try:
            # Non-blocking check for messages
            try:
                msg = log_queue.get_nowait()
                for ws in log_clients[:]:
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        if ws in log_clients:
                            log_clients.remove(ws)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break


@app.on_event("startup")
async def startup():
    asyncio.create_task(log_broadcaster())
    temp_preview_root = Path("./output/temp/texture_preview")
    temp_preview_root.mkdir(parents=True, exist_ok=True)
    app.mount("/output/temp/texture_preview", StaticFiles(directory=temp_preview_root), name="texture_preview")
    activity_extract_root = Path("./output/temp/activity_extract")
    activity_extract_root.mkdir(parents=True, exist_ok=True)
    app.mount("/output/temp/activity_extract", StaticFiles(directory=activity_extract_root), name="activity_extract")


class RunRequest(BaseModel):
    mode: str = "extract_only"
    hero_ids: list[str] = []


class ConfigUpdate(BaseModel):
    asset_bundle_folder: str = ""
    assetstudio_path: str = ""
    spine_cli_path: str = ""
    spine_exporter_path: str = ""
    gif_engine: str = "spine_cli"
    gif_export_fps: int = 30
    gif_export_scale: float = 1.0
    gif_export_pma: bool = False
    gif_spinepro_reencode_enabled: bool = False
    gif_spinepro_preset: dict | None = None


@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = Path(__file__).parent / "templates" / "index.html"
    return template_path.read_text(encoding="utf-8")


@app.get("/api/config")
async def api_get_config():
    config = get_config()
    return {
        "asset_bundle_folder": config.asset_bundle_folder,
        "assetstudio_path": config.assetstudio_path,
        "spine_cli_path": config.spine_cli_path,
        "spine_exporter_path": config.spine_exporter_path,
        "gif_engine": getattr(config, 'gif_engine', 'spine_cli'),
        "gif_export_fps": getattr(config, 'gif_export_fps', 30),
        "gif_export_scale": getattr(config, 'gif_export_scale', 1.0),
        "gif_export_pma": getattr(config, 'gif_export_pma', False),
        "gif_spinepro_reencode_enabled": getattr(config, 'gif_spinepro_reencode_enabled', False),
        "gif_spinepro_preset": getattr(config, 'gif_spinepro_preset', None),
    }



@app.post("/api/config")
async def api_save_config(req: ConfigUpdate):
    config = get_config()
    for k, v in req.dict().items():
        if hasattr(config, k):
            setattr(config, k, v)
    data = {
        "asset_bundle_folder": config.asset_bundle_folder,
        "assetstudio_path": config.assetstudio_path,
        "spine_cli_path": config.spine_cli_path,
        "spine_exporter_path": config.spine_exporter_path,
        "gif_engine": getattr(config, 'gif_engine', 'spine_cli'),
        "spine_gif_export_settings_path": config.spine_gif_export_settings_path,
        "use_internal_default_gif_preset": config.use_internal_default_gif_preset,
        "internal_default_gif_preset_path": config.internal_default_gif_preset_path,
        "gif_postprocess_enabled": config.gif_postprocess_enabled,
        "gif_auto_trim_bad_leading_frames": config.gif_auto_trim_bad_leading_frames,
        "gif_trim_max_scan_frames": config.gif_trim_max_scan_frames,
        "output_root": config.output_root,
        "library_root": config.library_root,
        "timeout_minutes": config.timeout_minutes,
        "assetstudio_export_types": config.assetstudio_export_types,
        "assetstudio_cli_profile": config.assetstudio_cli_profile,
        "assetstudio_game": config.assetstudio_game,
        "assetstudio_types": config.assetstudio_types,
        "assetstudio_group_assets": config.assetstudio_group_assets,
        "assetstudio_export_type": config.assetstudio_export_type,
        "assetstudio_fallback_export_types": config.assetstudio_fallback_export_types,
        "assetstudio_try_without_types": config.assetstudio_try_without_types,
        "assetstudio_use_absolute_output_path": config.assetstudio_use_absolute_output_path,
        "assetstudio_export_whole_hero_group_folder": config.assetstudio_export_whole_hero_group_folder,
        "gif_trim_fallback_by_animation": config.gif_trim_fallback_by_animation,
        "gif_export_fps": getattr(config, 'gif_export_fps', 30),
        "gif_export_scale": getattr(config, 'gif_export_scale', 1.0),
        "gif_export_pma": getattr(config, 'gif_export_pma', False),
        "gif_spinepro_reencode_enabled": getattr(config, 'gif_spinepro_reencode_enabled', False),
        "gif_spinepro_preset": getattr(config, 'gif_spinepro_preset', None),
    }
    save_json(CONFIG_PATH, data)
    return {"status": "ok"}


@app.get("/api/heroes")
async def api_scan_heroes():
    config = get_config()
    if not config.asset_bundle_folder or not Path(config.asset_bundle_folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")
    scanner = HeroScanner(config.asset_bundle_folder)
    heroes = scanner.scan()
    return [
        {
            "hero_id": h.hero_id,
            "hero_number": h.hero_number,
            "hero_type": h.hero_type,
            "variant_ids": h.variant_ids,
            "file_count": h.file_count,
            "total_size": h.total_size,
            "status": h.status,
        }
        for h in heroes
    ]


@app.get("/api/library")
async def api_library():
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    library = []
    runs = []

    saved_root = _prepare_library(config)
    seen = set()
    for heroes_dir in (saved_root / "heroes", root / "heroes"):
        if not heroes_dir.exists():
            continue
        for hero_dir in sorted(heroes_dir.iterdir()):
            if not hero_dir.is_dir() or hero_dir.name in seen:
                continue
            entry = _build_library_entry(hero_dir.name, hero_dir)
            if entry:
                seen.add(hero_dir.name)
                entry["section"] = "library"
                library.append(entry)

    runs_dir = root / "runs"
    if runs_dir.exists():
        seen = {e["hero_id"] for e in library}
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            run_heroes = run_dir / "heroes"
            if not run_heroes.exists():
                continue
            for hero_dir in sorted(run_heroes.iterdir()):
                if not hero_dir.is_dir():
                    continue
                hero_id = hero_dir.name
                if hero_id in seen:
                    continue
                seen.add(hero_id)
                entry = _build_library_entry(hero_id, hero_dir)
                if entry:
                    entry["section"] = "runs"
                    runs.append(entry)

    return {"library": library, "runs": runs}


def _thumbnail_url(gif_url: str | None, full_size=False) -> str | None:
    if not gif_url:
        return None
    area = "library" if gif_url.startswith("/library/") else "output"
    prefix = f"/{area}/"
    if not gif_url.startswith(prefix):
        return None
    source = unquote(gif_url[len(prefix):])
    config = get_config()
    root = library_root_for(config) if area == "library" else Path(config.output_root)
    try:
        version = (root / source).stat().st_mtime_ns
    except OSError:
        return None
    route = "library-image" if full_size else "library-thumbnail"
    return f"/api/{route}?source={quote(source, safe='')}&area={area}&v={version}"


def _png_response(source, area, full_size):
    config = get_config()
    if area not in ("output", "library"):
        raise HTTPException(400, "Invalid media area")
    root = library_root_for(config) if area == "library" else Path(config.output_root)
    try:
        image = static_thumbnail(root, source, full_size=full_size)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Cannot decode GIF preview") from exc
    return FileResponse(image, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/library-thumbnail")
def api_library_thumbnail(source: str, area: str = "output"):
    return _png_response(source, area, False)


@app.get("/api/library-image")
def api_library_image(source: str, area: str = "output"):
    return _png_response(source, area, True)


@app.delete("/api/library/{hero_id}")
async def api_delete_library_hero(hero_id: str):
    if worker_status["running"] or (active_worker and active_worker.isRunning()):
        raise HTTPException(409, "Cannot delete heroes while a job is running")
    try:
        config = get_config()
        return trash_hero(Path(config.output_root), hero_id, library_root_for(config))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except OSError as exc:
        raise HTTPException(409, "Files are in use or cannot be moved; deletion was cancelled") from exc


def _parse_number(hero_id: str) -> int | None:
    import re
    m = re.search(r'hero(\d+)', hero_id)
    return int(m.group(1)) if m else None


def _classify_type(hero_id: str) -> str:
    num = _parse_number(hero_id)
    if num is None:
        return "Unknown"
    if num > 10000:
        return "Skin"
    if num > 1200:
        return "Collab"
    return "Original"


def _build_library_entry(hero_id: str, hero_dir: Path) -> dict | None:
    gif_dir = _hero_gif_dir(hero_id, hero_dir)
    matched_dir = hero_dir / "matched"
    summary_file = hero_dir / "summary.json"

    url_base = _media_base(gif_dir.parent) + "/gif"

    gifs = []
    thumbnail = None
    if gif_dir.exists():
        gifs = [f.name for f in sorted(gif_dir.glob("*.gif")) if not f.name.endswith(".original.gif")]
        if gifs:
            thumbnail = f"{url_base}/{gifs[0]}"

    for subdir in sorted(gif_dir.iterdir()) if gif_dir.exists() else []:
        if subdir.is_dir():
            sub_gifs = [f.name for f in sorted(subdir.glob("*.gif")) if not f.name.endswith(".original.gif")]
            if sub_gifs:
                if not thumbnail:
                    thumbnail = f"{url_base}/{subdir.name}/{sub_gifs[0]}"
                gifs.extend(sub_gifs)

    has_matched = (matched_dir / f"{hero_id}.skel").exists() or (matched_dir / f"{hero_id}.json").exists()

    summary = {}
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        except:
            pass

    meta = _load_hero_metadata().get(hero_id, {})

    return {
        "hero_id": hero_id,
        "hero_name": meta.get("hero_name", ""),
        "hero_type": meta.get("hero_type") or summary.get("hero_type") or _classify_type(hero_id),
        "hero_number": summary.get("hero_number") or _parse_number(hero_id),
        "status": summary.get("status", "unknown"),
        "gif_count": len(gifs),
        "gif_files": gifs,
        "has_matched": has_matched,
        "thumbnail": thumbnail,
        "static_thumbnail": _thumbnail_url(thumbnail),
    }


def _hero_gif_dir(hero_id: str, hero_dir: Path) -> Path:
    """Keep earlier successful exports visible after a later matched-only run."""
    config = get_config()
    output = Path(config.output_root)
    candidates = [hero_dir / "gif", library_root_for(config) / "heroes" / hero_id / "gif", output / "heroes" / hero_id / "gif"]
    runs = output / "runs"
    if runs.is_dir():
        candidates.extend(run / "heroes" / hero_id / "gif" for run in sorted(runs.iterdir(), reverse=True) if run.is_dir())
    for candidate in candidates:
        if candidate.is_dir() and any(not f.name.endswith(".original.gif") for f in candidate.rglob("*.gif")):
            return candidate
    return hero_dir / "gif"


class HeroMetaUpdate(BaseModel):
    hero_name: str | None = None
    hero_type: str | None = None


METADATA_PATH = Path("config/hero_metadata.json")


def _load_hero_metadata() -> dict:
    if METADATA_PATH.exists():
        try:
            return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        except:
            pass
    return {}


def _save_hero_metadata(data: dict):
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(METADATA_PATH, data)


@app.get("/api/library/{hero_id}")
async def api_library_detail(hero_id: str):
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", hero_id):
        raise HTTPException(400, "Invalid hero ID")
    saved_root = _prepare_library(config)
    hero_dir = None
    section = "library"
    for heroes_dir in (saved_root / "heroes", root / "heroes"):
        candidate = heroes_dir / hero_id
        if candidate.is_dir():
            hero_dir = candidate
            break

    if not hero_dir:
        section = "runs"
        runs_dir = root / "runs"
        if runs_dir.exists():
            for run_dir in sorted(runs_dir.iterdir(), reverse=True):
                candidate = run_dir / "heroes" / hero_id
                if candidate.exists():
                    hero_dir = candidate
                    break

    if not hero_dir:
        raise HTTPException(404, "Hero not found")

    entry = _build_library_entry(hero_id, hero_dir)
    entry["section"] = section
    meta = _load_hero_metadata().get(hero_id, {})
    entry["hero_name"] = meta.get("hero_name", "")
    entry["hero_type"] = meta.get("hero_type", entry.get("hero_type", "Unknown"))

    gif_dir = _hero_gif_dir(hero_id, hero_dir)
    variant_gifs = {}

    gif_base = _media_base(gif_dir.parent) + "/gif"
    entry["folder_path"] = str(hero_dir.resolve())
    entry["main_image"] = _thumbnail_url(entry.get("thumbnail"), full_size=True)

    if gif_dir.exists():
        for f in sorted(gif_dir.glob("*.gif")):
            if not f.name.endswith(".original.gif"):
                entry.setdefault("gif_files_detail", []).append({
                    "name": f.name,
                    "url": f"{gif_base}/{f.name}",
                    "thumbnail": _thumbnail_url(f"{gif_base}/{f.name}"),
                    "image": _thumbnail_url(f"{gif_base}/{f.name}", full_size=True),
                    "size": f.stat().st_size,
                })
        for subdir in sorted(gif_dir.iterdir()):
            if subdir.is_dir():
                files = []
                for f in sorted(subdir.glob("*.gif")):
                    if not f.name.endswith(".original.gif"):
                        files.append({
                            "name": f.name,
                            "url": f"{gif_base}/{subdir.name}/{f.name}",
                            "thumbnail": _thumbnail_url(f"{gif_base}/{subdir.name}/{f.name}"),
                            "image": _thumbnail_url(f"{gif_base}/{subdir.name}/{f.name}", full_size=True),
                            "size": f.stat().st_size,
                        })
                if files:
                    variant_gifs[subdir.name] = files
    entry["variant_gifs"] = variant_gifs

    matched_dir = hero_dir / "matched"
    if matched_dir.exists():
        entry["matched_files"] = [f.name for f in sorted(matched_dir.iterdir()) if f.is_file()]
        for subdir in sorted(matched_dir.iterdir()):
            if subdir.is_dir():
                entry.setdefault("matched_variants", {})[subdir.name] = [f.name for f in sorted(subdir.iterdir()) if f.is_file()]

    return entry


@app.patch("/api/library/{hero_id}")
async def api_update_hero_meta(hero_id: str, req: HeroMetaUpdate):
    all_meta = _load_hero_metadata()
    if hero_id not in all_meta:
        all_meta[hero_id] = {}
    if req.hero_name is not None:
        all_meta[hero_id]["hero_name"] = req.hero_name
    if req.hero_type is not None:
        all_meta[hero_id]["hero_type"] = req.hero_type
    _save_hero_metadata(all_meta)
    return {"status": "ok", "metadata": all_meta[hero_id]}


@app.post("/api/run")
async def api_run(req: RunRequest):
    global active_worker
    if active_worker and active_worker.isRunning():
        raise HTTPException(409, "A job is already running")

    config = get_config()

    if req.hero_ids:
        scanner = HeroScanner(config.asset_bundle_folder)
        all_heroes = scanner.scan()
        heroes = [h for h in all_heroes if h.hero_id in req.hero_ids]
        found_ids = {h.hero_id for h in heroes}
        for hid in req.hero_ids:
            if hid not in found_ids:
                from src.models import HeroBundleGroup
                from src.services.hero_scanner import parse_hero_number, classify_hero_type
                heroes.append(HeroBundleGroup(
                    hero_id=hid,
                    hero_number=parse_hero_number(hid),
                    hero_type=classify_hero_type(hid),
                    variant_ids=[],
                    bundle_files=[],
                    file_count=0,
                    total_size=0,
                    status="Ready",
                ))
    else:
        raise HTTPException(400, "No heroes selected")

    active_worker = OrchestratorWorker(config, heroes, req.mode)

    worker_status["running"] = True
    worker_status["mode"] = req.mode
    worker_status["hero_count"] = len(heroes)
    worker_status["finished"] = False

    # Connect logger signal to log_queue (only once)
    global _log_connected
    if not _log_connected:
        logger.log_signal.connect(_on_log)
        _log_connected = True

    def on_finished(summary):
        global active_worker
        worker_status["running"] = False
        worker_status["finished"] = True
        log_queue.put("--- 任務完成 ---")
        active_worker = None

    active_worker.finished.connect(on_finished)
    active_worker.start()

    log_queue.put(f"已啟動：{req.mode}（{len(heroes)} 個英雄）")
    return {"status": "started", "mode": req.mode, "hero_count": len(heroes)}


@app.get("/api/status")
async def api_status():
    return {
        "running": worker_status["running"],
        "mode": worker_status["mode"],
        "hero_count": worker_status["hero_count"],
        "finished": worker_status["finished"],
    }


@app.post("/api/stop")
async def api_stop():
    if active_worker:
        active_worker.cancel()
        log_queue.put("--- 已送出停止請求 ---")
        return {"status": "stopping"}
    return {"status": "idle"}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    log_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in log_clients:
            log_clients.remove(websocket)


@app.get("/api/runs")
async def api_runs():
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    runs_dir = root / "runs"
    if not runs_dir.exists():
        return []

    runs = []
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        summary_file = run_dir / "summary.json"
        if summary_file.exists():
            try:
                summary = json.loads(summary_file.read_text(encoding="utf-8"))
                runs.append(summary)
            except:
                pass
    return runs


@app.post("/api/open-folder")
async def api_open_folder(req: dict):
    folder = req.get("path", "")
    if not folder or not Path(folder).exists():
        raise HTTPException(404, "Folder not found")
    subprocess.Popen(["explorer", str(Path(folder).resolve())])
    return {"status": "ok"}


@app.post("/api/library/{hero_id}/promote")
async def api_promote_hero(hero_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", hero_id):
        raise HTTPException(400, "Invalid hero ID")
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    src_dir = None
    runs_dir = root / "runs"
    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            candidate = run_dir / "heroes" / hero_id
            if candidate.exists():
                src_dir = candidate
                break

    if not src_dir:
        raise HTTPException(404, "Hero not found in runs")

    if worker_status["running"] or (active_worker and active_worker.isRunning()):
        raise HTTPException(409, "Cannot save heroes while a job is running")
    library = _prepare_library(config)
    try:
        validate_tree(root.resolve(), src_dir)
        dest_dir = validate_tree(library, library / "heroes" / hero_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # Copy the complete saved payload first; a failed copy must not publish a partial hero.
    import uuid
    staging = _contained(library, library / "temp" / f".saving_{hero_id}_{uuid.uuid4().hex}")
    previous = _contained(library, library / "versions" / f"{hero_id}_{uuid.uuid4().hex}")
    promoted = []
    try:
        staging.mkdir(parents=True)
        for name in ("matched", "gif", "spine_ready", "images", "summary.json"):
            source = src_dir / name
            if source.is_dir():
                shutil.copytree(source, staging / name)
                promoted.append(name)
            elif source.is_file():
                shutil.copy2(source, staging / name)
                promoted.append(name)
        if dest_dir.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            dest_dir.rename(previous)
        try:
            staging.rename(dest_dir)
        except OSError:
            if previous.exists():
                previous.rename(dest_dir)
            raise
    except OSError as exc:
        raise HTTPException(409, "Cannot save hero; existing Library data preserved") from exc
    finally:
        if staging.exists():
            shutil.rmtree(validate_tree(library, staging))

    return {"status": "ok", "promoted": promoted, "dest": str(dest_dir)}


def _clean_intermediates(hero_id=None):
    if worker_status["running"] or (active_worker and active_worker.isRunning()):
        raise HTTPException(409, "Cannot clean output while a job is running")
    try:
        return clean_intermediates(Path(get_config().output_root), hero_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(409, "Some temporary files are in use; saved Library is preserved") from exc


@app.post("/api/library/{hero_id}/clean")
async def api_clean_hero(hero_id: str):
    return {"status": "ok", "removed": _clean_intermediates(hero_id)}


@app.post("/api/clean-temp")
async def api_clean_temp():
    removed = _clean_intermediates()
    return {"status": "ok", "cleaned_runs": sorted({path.split("/")[1] for path in removed})}


@app.post("/api/clean-generated")
async def api_clean_generated():
    if worker_status["running"] or (active_worker and active_worker.isRunning()):
        raise HTTPException(409, "Cannot clean output while a job is running")
    config = get_config()
    try:
        return clean_generated(Path(config.output_root), library_root_for(config))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(409, "Some generated files could not be cleaned; saved Library is preserved") from exc


@app.delete("/api/runs/{run_id}")
async def api_delete_run(run_id: str):
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    run_dir = root / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(404, "Run not found")

    shutil.rmtree(run_dir)
    return {"status": "ok", "deleted": run_id}


# ============================================================
# Resource Index (本地表) Routes
# ============================================================

class ResourceIndexRequest(BaseModel):
    folder: str = ""

class ResourceCompareRequest(BaseModel):
    old_folder: str = ""
    new_folder: str = ""

@app.post("/api/resource-index/build")
async def api_build_resource_index(req: ResourceIndexRequest):
    from src.services.resource_indexer import build_index
    config = get_config()
    folder = req.folder or config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "Folder not set or does not exist")
    idx = build_index(folder, config.output_root)
    return {
        "status": "ok",
        "folder": idx.folder,
        "created_at": idx.created_at,
        "file_count": idx.file_count,
        "total_size": idx.total_size,
        "summary": {
            "hero": sum(1 for e in idx.entries if e.file_type == "hero"),
            "activity_spine": sum(1 for e in idx.entries if e.file_type == "activity_spine"),
            "activity_pack": sum(1 for e in idx.entries if e.file_type == "activity_pack"),
            "other": sum(1 for e in idx.entries if e.file_type == "other"),
        },
    }

@app.get("/api/resource-index")
async def api_list_indexes():
    from src.services.resource_indexer import list_indexes
    config = get_config()
    return list_indexes(config.output_root)

@app.get("/api/resource-index/latest")
async def api_latest_index():
    from src.services.resource_indexer import load_latest_index
    config = get_config()
    idx = load_latest_index(config.output_root)
    if not idx:
        raise HTTPException(404, "No index found")
    return {
        "folder": idx.folder,
        "created_at": idx.created_at,
        "file_count": idx.file_count,
        "total_size": idx.total_size,
        "entries": [
            {"filename": e.filename, "size": e.size, "sha256": e.sha256, "file_type": e.file_type}
            for e in idx.entries
        ],
    }

@app.post("/api/resource-index/compare")
async def api_compare_indexes(req: ResourceCompareRequest):
    from src.services.resource_indexer import build_index, load_latest_index, load_index, list_indexes, diff_indexes
    config = get_config()
    new_folder = req.new_folder or config.asset_bundle_folder
    if not new_folder or not Path(new_folder).exists():
        raise HTTPException(400, "New folder not valid")

    indexes = list_indexes(config.output_root)
    if len(indexes) >= 2:
        idx_path = Path(config.output_root) / "resource_index" / indexes[0]["filename"]
        prev_path = Path(config.output_root) / "resource_index" / indexes[1]["filename"]
        old_idx = load_index(str(prev_path))
        new_idx = load_index(str(idx_path))
    else:
        old_idx = load_latest_index(config.output_root)
        if not old_idx:
            old_idx = build_index(new_folder, config.output_root)
        new_idx = build_index(new_folder, config.output_root)

    diff = diff_indexes(old_idx, new_idx)
    diff["old_index"] = {"folder": old_idx.folder, "created_at": old_idx.created_at}
    diff["new_index"] = {"folder": new_idx.folder, "created_at": new_idx.created_at}
    return diff

# ============================================================
# Activity Routes
# ============================================================

class ActivityExtractRequest(BaseModel):
    activity_id: str = ""
    mode: str = "extract_and_gif"

@app.get("/api/activities")
async def api_list_activities():
    from src.services.activity_scanner import detect_activity_packs
    config = get_config()
    folder = config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")

    activities = detect_activity_packs(folder)
    return [
        {
            "activity_id": a.activity_id,
            "activity_name": a.activity_name,
            "pack_file": a.pack_file,
            "pack_size": a.pack_size,
            "hero_count": len(a.hero_ids),
            "hero_ids": a.hero_ids,
            "spine_file_count": len(a.spine_files),
        }
        for a in activities
    ]

@app.get("/api/activities/{activity_id}")
async def api_activity_detail(activity_id: str):
    from src.services.activity_scanner import detect_activities, find_hero_ab_files
    config = get_config()
    folder = config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")

    activities = detect_activities(folder, config.assetstudio_path, config, config.output_root)
    activity = next((a for a in activities if a.activity_id == activity_id), None)
    if not activity:
        raise HTTPException(404, f"Activity {activity_id} not found")

    hero_abs = find_hero_ab_files(folder, activity.hero_ids)

    return {
        "activity_id": activity.activity_id,
        "activity_name": activity.activity_name,
        "pack_file": activity.pack_file,
        "pack_size": activity.pack_size,
        "hero_ids": activity.hero_ids,
        "hero_ab_files": hero_abs,
        "spine_files": activity.spine_files,
        "total_heroes_with_ab": len(hero_abs),
    }

@app.get("/api/activities/{activity_id}/plan")
async def api_activity_plan(activity_id: str):
    from src.services.activity_extractor import build_activity_extract_plan
    config = get_config()
    folder = config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")

    plans = build_activity_extract_plan(folder, config.assetstudio_path, config, config.output_root)
    plan = next((p for p in plans if p.activity_id == activity_id), None)
    if not plan:
        raise HTTPException(404, f"Activity {activity_id} not found")

    return {
        "activity_id": plan.activity_id,
        "activity_name": plan.activity_name,
        "pack_file": plan.pack_file,
        "total_heroes": plan.total_heroes,
        "total_files": plan.total_files,
        "hero_ab_files": plan.hero_ab_files,
        "activity_spine_files": plan.activity_spine_files,
    }

@app.post("/api/activities/{activity_id}/extract")
async def api_activity_extract(activity_id: str, req: ActivityExtractRequest):
    global active_worker
    if active_worker and active_worker.isRunning():
        raise HTTPException(409, "A job is already running")

    from src.services.activity_scanner import detect_activities, find_hero_ab_files
    from src.services.hero_scanner import HeroScanner, parse_hero_number, classify_hero_type
    from src.models import HeroBundleGroup

    config = get_config()
    folder = config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")

    activities = detect_activities(folder, config.assetstudio_path, config, config.output_root)
    activity = next((a for a in activities if a.activity_id == activity_id), None)
    if not activity:
        raise HTTPException(404, f"Activity {activity_id} not found")

    hero_abs = find_hero_ab_files(folder, activity.hero_ids)

    scanner = HeroScanner(folder)
    all_heroes = scanner.scan()
    all_hero_map = {h.hero_id: h for h in all_heroes}

    heroes = []
    for hero_id in hero_abs:
        if hero_id in all_hero_map:
            heroes.append(all_hero_map[hero_id])
        else:
            ab_files = [Path(folder) / f for f in hero_abs[hero_id]]
            group = HeroBundleGroup(
                hero_id=hero_id,
                hero_number=parse_hero_number(hero_id),
                hero_type=classify_hero_type(hero_id),
                variant_ids=[],
                bundle_files=ab_files,
                file_count=len(ab_files),
                total_size=sum(f.stat().st_size for f in ab_files),
                status="Ready" if len(ab_files) >= 2 else "Partial",
            )
            heroes.append(group)

    mode = req.mode or "extract_and_gif"

    active_worker = OrchestratorWorker(config, heroes, mode)

    worker_status["running"] = True
    worker_status["mode"] = f"activity:{activity_id}:{mode}"
    worker_status["hero_count"] = len(heroes)
    worker_status["finished"] = False

    global _log_connected
    if not _log_connected:
        logger.log_signal.connect(_on_log)
        _log_connected = True

    def on_finished(summary):
        global active_worker
        worker_status["running"] = False
        worker_status["finished"] = True
        log_queue.put(f"--- 活動 {activity_id} 任務完成 ---")
        active_worker = None

    active_worker.finished.connect(on_finished)
    active_worker.start()

    log_queue.put(f"已啟動活動 {activity_id}：{mode}（{len(heroes)} 個英雄）")
    return {
        "status": "started",
        "activity_id": activity_id,
        "mode": mode,
        "hero_count": len(heroes),
        "hero_ids": [h.hero_id for h in heroes],
    }

@app.post("/api/activities/{activity_id}/preview")
async def api_activity_texture_preview(activity_id: str):
    from src.services.activity_extractor import extract_texture_previews
    config = get_config()
    folder = config.asset_bundle_folder
    if not folder or not Path(folder).exists():
        raise HTTPException(400, "asset_bundle_folder not set or does not exist")

    result = extract_texture_previews(
        folder, activity_id, config.assetstudio_path, config, config.output_root
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    return result

@app.get("/api/texture-preview/{activity_id}")
async def api_texture_preview_list(activity_id: str):
    config = get_config()
    preview_dir = Path(config.output_root) / "temp" / "texture_preview" / activity_id
    if not preview_dir.exists():
        return {"activity_id": activity_id, "image_count": 0, "images": []}

    import os
    images = []
    for root, dirs, files in os.walk(preview_dir):
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tga")):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, preview_dir)
                images.append({
                    "filename": f,
                    "path": rel_path,
                    "url": f"/output/temp/texture_preview/{activity_id}/{rel_path.replace(os.sep, '/')}",
                    "size": os.path.getsize(full_path),
                })

    return {
        "activity_id": activity_id,
        "image_count": len(images),
        "images": images,
    }

class ActivityProcessRequest(BaseModel):
    selected_stems: list[str] = []

@app.get("/api/activities/{activity_id}/pack-files")
async def api_activity_pack_files(activity_id: str):
    from src.services.activity_extractor import list_activity_pack_files
    config = get_config()
    result = list_activity_pack_files(activity_id, config.output_root)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result

@app.post("/api/activities/{activity_id}/process-selected")
async def api_activity_process_selected(activity_id: str, req: ActivityProcessRequest):
    from src.services.activity_extractor import process_activity_selected
    config = get_config()
    result = process_activity_selected(activity_id, req.selected_stems, config, config.output_root)
    return result

@app.post("/api/activities/{activity_id}/clean-pack")
async def api_clean_activity_pack(activity_id: str):
    import shutil
    config = get_config()
    pack_dir = Path(config.output_root) / "temp" / "activity_extract" / "activity_pack_extract"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
        return {"status": "ok", "cleaned": str(pack_dir)}
    return {"status": "ok", "cleaned": None}

class ExtractFileRequest(BaseModel):
    filename: str = ""

@app.post("/api/extract-file")
async def api_extract_file(req: ExtractFileRequest):
    import os as _os
    from src.services.assetstudio_service import AssetStudioService
    config = get_config()
    ab_path = Path(config.asset_bundle_folder) / req.filename
    if not ab_path.exists():
        raise HTTPException(404, f"File not found: {req.filename}")

    out_dir = Path(config.output_root) / "temp" / "diff_extract" / ab_path.stem
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    svc = AssetStudioService(config.assetstudio_path, config)
    success, cmd, stdout, stderr = svc.extract_bundle(ab_path, out_dir, 120, include_types=True)

    if not success:
        raise HTTPException(500, f"Extraction failed: {stderr[:200]}")

    img_count = 0
    txt_count = 0
    for root, dirs, files in _os.walk(out_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.tga')):
                img_count += 1
            elif f.lower().endswith(('.txt', '.json', '.bytes', '.prefab')):
                txt_count += 1

    return {
        "status": "ok",
        "filename": req.filename,
        "output_dir": str(out_dir),
        "file_count": img_count + txt_count,
        "image_count": img_count,
        "text_count": txt_count,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="websockets")
