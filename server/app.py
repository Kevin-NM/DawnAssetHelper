import os
import sys
import json
import asyncio
import queue
import shutil
import subprocess
import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel

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

    heroes_dir = root / "heroes"
    if heroes_dir.exists():
        for hero_dir in sorted(heroes_dir.iterdir()):
            if not hero_dir.is_dir():
                continue
            entry = _build_library_entry(hero_dir.name, hero_dir)
            if entry:
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
    gif_dir = hero_dir / "gif"
    matched_dir = hero_dir / "matched"
    summary_file = hero_dir / "summary.json"

    # Determine URL base based on path
    # Library: output/heroes/hero_id  |  Runs: output/runs/<ts>/heroes/hero_id
    is_library = hero_dir.parent.name == "heroes" and "runs" not in hero_dir.parts
    if is_library:
        url_base = f"/output/heroes/{hero_id}/gif"
    else:
        # Find the timestamp directory between "runs" and "heroes"
        parts = hero_dir.parts
        runs_idx = next((i for i, p in enumerate(parts) if p == "runs"), None)
        ts = parts[runs_idx + 1] if runs_idx is not None and runs_idx + 1 < len(parts) else "unknown"
        url_base = f"/output/runs/{ts}/heroes/{hero_id}/gif"

    gifs = []
    thumbnail = None
    if gif_dir.exists():
        gifs = [f.name for f in sorted(gif_dir.glob("*.gif")) if not f.name.endswith(".original.gif")]
        if gifs:
            thumbnail = f"{url_base}/{gifs[0]}"

    if not thumbnail:
        for subdir in sorted(gif_dir.iterdir()) if gif_dir.exists() else []:
            if subdir.is_dir():
                sub_gifs = [f.name for f in sorted(subdir.glob("*.gif")) if not f.name.endswith(".original.gif")]
                if sub_gifs:
                    thumbnail = f"{url_base}/{subdir.name}/{sub_gifs[0]}"
                    gifs.extend(sub_gifs)
                    break

    has_matched = (matched_dir / f"{hero_id}.skel").exists() or (matched_dir / f"{hero_id}.json").exists()

    summary = {}
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        except:
            pass

    if not thumbnail:
        output_root_path = Path("output")
        # Check library first
        lib_gif = output_root_path / "heroes" / hero_id / "gif"
        if lib_gif.exists():
            lib_gifs = [f.name for f in sorted(lib_gif.glob("*.gif")) if not f.name.endswith(".original.gif")]
            if lib_gifs:
                thumbnail = f"/output/heroes/{hero_id}/gif/{lib_gifs[0]}"
            else:
                for subdir in sorted(lib_gif.iterdir()):
                    if subdir.is_dir():
                        sub_gifs = [f.name for f in sorted(subdir.glob("*.gif")) if not f.name.endswith(".original.gif")]
                        if sub_gifs:
                            thumbnail = f"/output/heroes/{hero_id}/gif/{subdir.name}/{sub_gifs[0]}"
                            break

        if not thumbnail:
            runs_dir = output_root_path / "runs"
            if runs_dir.exists():
                for run_dir in sorted(runs_dir.iterdir(), reverse=True):
                    candidate = run_dir / "heroes" / hero_id / "gif"
                    if candidate.exists():
                        run_gifs = [f.name for f in sorted(candidate.glob("*.gif")) if not f.name.endswith(".original.gif")]
                        if run_gifs:
                            thumbnail = f"/output/runs/{run_dir.name}/heroes/{hero_id}/gif/{run_gifs[0]}"
                            break
                    for subdir in sorted(candidate.iterdir()) if candidate.exists() else []:
                        if subdir.is_dir():
                            sub_gifs = [f.name for f in sorted(subdir.glob("*.gif")) if not f.name.endswith(".original.gif")]
                            if sub_gifs:
                                thumbnail = f"/output/runs/{run_dir.name}/heroes/{hero_id}/gif/{subdir.name}/{sub_gifs[0]}"
                                break

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
    }


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

    hero_dir = None
    section = "library"
    heroes_dir = root / "heroes"
    if heroes_dir.exists():
        candidate = heroes_dir / hero_id
        if candidate.exists():
            hero_dir = candidate

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

    gif_dir = hero_dir / "gif"
    variant_gifs = {}

    if section == "library":
        gif_base = f"/output/heroes/{hero_id}/gif"
    else:
        gif_base = f"/output/runs/{hero_dir.parent.parent.name}/heroes/{hero_id}/gif"

    if gif_dir.exists():
        for f in sorted(gif_dir.glob("*.gif")):
            if not f.name.endswith(".original.gif"):
                entry.setdefault("gif_files_detail", []).append({
                    "name": f.name,
                    "url": f"{gif_base}/{f.name}",
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

    dest_dir = root / "heroes" / hero_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    promoted = []

    matched_src = src_dir / "matched"
    if matched_src.exists():
        matched_dest = dest_dir / "matched"
        if matched_dest.exists():
            shutil.rmtree(matched_dest)
        shutil.copytree(matched_src, matched_dest)
        promoted.append("matched")

    gif_src = src_dir / "gif"
    if gif_src.exists():
        gif_dest = dest_dir / "gif"
        if gif_dest.exists():
            shutil.rmtree(gif_dest)
        shutil.copytree(gif_src, gif_dest)
        promoted.append("gif")

    summary_src = src_dir / "summary.json"
    if summary_src.exists():
        shutil.copy2(summary_src, dest_dir / "summary.json")
        promoted.append("summary.json")

    spine_src = src_dir / "spine_ready"
    if spine_src.exists():
        spine_dest = dest_dir / "spine_ready"
        if spine_dest.exists():
            shutil.rmtree(spine_dest)
        shutil.copytree(spine_src, spine_dest)
        promoted.append("spine_ready")

    return {"status": "ok", "promoted": promoted, "dest": str(dest_dir)}


@app.post("/api/library/{hero_id}/clean")
async def api_clean_hero(hero_id: str):
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    removed = []
    runs_dir = root / "runs"
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            hero_dir = run_dir / "heroes" / hero_id
            if hero_dir.exists():
                raw_export = hero_dir / "raw_export"
                if raw_export.exists():
                    shutil.rmtree(raw_export)
                    removed.append(f"{run_dir.name}/raw_export")
                diagnostics = hero_dir / "diagnostics"
                if diagnostics.exists():
                    shutil.rmtree(diagnostics)
                    removed.append(f"{run_dir.name}/diagnostics")

    return {"status": "ok", "removed": removed}


@app.post("/api/clean-temp")
async def api_clean_temp():
    config = get_config()
    root = Path(config.output_root)
    if config.assetstudio_use_absolute_output_path:
        root = root.resolve()

    removed = []
    runs_dir = root / "runs"
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            heroes_dir = run_dir / "heroes"
            if not heroes_dir.exists():
                continue
            for hero_dir in heroes_dir.iterdir():
                if not hero_dir.is_dir():
                    continue
                raw_export = hero_dir / "raw_export"
                if raw_export.exists():
                    shutil.rmtree(raw_export)
                diagnostics = hero_dir / "diagnostics"
                if diagnostics.exists():
                    shutil.rmtree(diagnostics)
            removed.append(run_dir.name)

    return {"status": "ok", "cleaned_runs": removed}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, ws="websockets")
