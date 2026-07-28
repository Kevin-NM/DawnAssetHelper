import re
import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

from src.app_config import AppConfig
from src.services.hero_scanner import HeroScanner, parse_hero_number, classify_hero_type
from src.services.activity_scanner import detect_activities, find_hero_ab_files
from src.services.logger_service import logger


@dataclass
class ActivityExtractPlan:
    activity_id: str
    activity_name: str
    pack_file: str
    hero_ab_files: dict[str, list[str]]  # hero_id -> [ab filenames]
    activity_spine_files: list[str]
    total_heroes: int
    total_files: int


def build_activity_extract_plan(
    ab_folder: str,
    assetstudio_path: str,
    config: AppConfig,
    output_root: str,
) -> list[ActivityExtractPlan]:
    activities = detect_activities(ab_folder, assetstudio_path, config, output_root)

    plans = []
    for activity in activities:
        hero_abs = find_hero_ab_files(ab_folder, activity.hero_ids)

        plans.append(ActivityExtractPlan(
            activity_id=activity.activity_id,
            activity_name=activity.activity_name,
            pack_file=activity.pack_file,
            hero_ab_files=hero_abs,
            activity_spine_files=activity.spine_files,
            total_heroes=len(hero_abs),
            total_files=sum(len(v) for v in hero_abs.values()),
        ))

    return plans


def extract_texture_previews(
    ab_folder: str,
    activity_id: str,
    assetstudio_path: str,
    config: AppConfig,
    output_root: str,
    timeout_sec: int = 300,
) -> dict:
    from src.services.assetstudio_service import AssetStudioService
    from src.services.activity_scanner import detect_activities, find_hero_ab_files
    import shutil
    import os

    activities = detect_activities(ab_folder, assetstudio_path, config, output_root, timeout_sec=60)
    activity = next((a for a in activities if a.activity_id == activity_id), None)
    if not activity:
        return {"error": f"Activity {activity_id} not found", "image_count": 0, "images": []}

    hero_abs = find_hero_ab_files(ab_folder, activity.hero_ids)

    svc = AssetStudioService(assetstudio_path, config)
    if not svc.validate():
        return {"error": "AssetStudio CLI not valid", "image_count": 0, "images": []}

    preview_dir = Path(output_root) / "temp" / "texture_preview" / activity_id
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)

    all_ab_files = []
    for hero_id, files in hero_abs.items():
        for f in files:
            all_ab_files.append(Path(ab_folder) / f)

    for spine_file in activity.spine_files:
        sf = Path(ab_folder) / spine_file
        if sf.exists() and sf not in all_ab_files:
            all_ab_files.append(sf)

    images = []
    for ab_file in all_ab_files:
        hero_output = preview_dir / ab_file.stem
        hero_output.mkdir(parents=True, exist_ok=True)

        success, cmd_str, stdout, stderr = svc.extract_bundle(
            ab_file, hero_output, timeout_sec // max(1, len(all_ab_files)),
            export_type="Convert", include_types=True
        )

        if not success:
            continue

        for root, dirs, files in os.walk(hero_output):
            for f in files:
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tga")):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, preview_dir)
                    images.append({
                        "filename": f,
                        "path": rel_path,
                        "source_ab": ab_file.name,
                        "size": os.path.getsize(full_path),
                    })

    return {
        "activity_id": activity_id,
        "image_count": len(images),
        "images": images,
        "preview_dir": str(preview_dir),
    }


@dataclass
class ActivityRunResult:
    activity_id: str
    activity_name: str
    run_id: str
    status: str
    total_heroes: int
    success_count: int
    failed_count: int
    gif_success_count: int
    gif_failed_count: int
    hero_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def list_activity_pack_files(activity_id: str, output_root: str) -> dict:
    pack_dir = Path(output_root) / "temp" / "activity_extract" / "activity_pack_extract"
    tex_dir = pack_dir / "Texture2D"
    asset_dir = pack_dir / "TextAsset"

    if not tex_dir.exists():
        return {"activity_id": activity_id, "items": [], "error": "Activity pack not extracted yet"}

    textures = {}
    for f in tex_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".png":
            textures[f.stem] = {
                "name": f.name,
                "stem": f.stem,
                "path": str(f.relative_to(pack_dir)),
                "size": f.stat().st_size,
            }

    text_assets = {}
    if asset_dir.exists():
        for f in asset_dir.iterdir():
            if f.is_file():
                text_assets[f.name] = {
                    "name": f.name,
                    "path": str(f.relative_to(pack_dir)),
                    "size": f.stat().st_size,
                }

    items = []
    for stem, tex in sorted(textures.items()):
        item = {
            "stem": stem,
            "texture": tex,
            "skeleton": None,
            "atlas": None,
            "has_all": False,
        }
        skel_name = f"{stem}.skel.prefab"
        atlas_name = f"{stem}.atlas.prefab"
        if skel_name in text_assets:
            item["skeleton"] = text_assets[skel_name]
        if atlas_name in text_assets:
            item["atlas"] = text_assets[atlas_name]
        item["has_all"] = item["skeleton"] is not None and item["atlas"] is not None
        items.append(item)

    complete = [i for i in items if i["has_all"]]
    texture_only = [i for i in items if not i["has_all"]]

    return {
        "activity_id": activity_id,
        "total_textures": len(textures),
        "complete_items": len(complete),
        "texture_only": len(texture_only),
        "items": items,
        "complete": complete,
        "texture_only_items": texture_only,
    }


def process_activity_selected(
    activity_id: str,
    selected_stems: list[str],
    config: AppConfig,
    output_root: str,
) -> dict:
    pack_dir = Path(output_root) / "temp" / "activity_extract" / "activity_pack_extract"
    tex_dir = pack_dir / "Texture2D"
    asset_dir = pack_dir / "TextAsset"

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = Path(output_root) / "runs" / run_ts
    activity_heroes_root = run_root / "heroes"

    processed = []
    errors = []

    for stem in selected_stems:
        hero_id = f"activity_{stem}"
        hero_dir = activity_heroes_root / hero_id
        matched_dir = hero_dir / "matched"
        matched_dir.mkdir(parents=True, exist_ok=True)

        tex_src = tex_dir / f"{stem}.png"
        skel_src = asset_dir / f"{stem}.skel.prefab"
        atlas_src = asset_dir / f"{stem}.atlas.prefab"

        if not tex_src.exists():
            errors.append(f"{stem}: texture not found")
            continue

        shutil.copy2(tex_src, matched_dir / f"{hero_id}.png")
        logger.info(f"[Activity] {stem}: copied texture")

        if skel_src.exists():
            dest = matched_dir / f"{hero_id}.skel"
            raw = skel_src.read_bytes()

            from src.services.asset_matcher import strip_unity_skel_header
            stripped = strip_unity_skel_header(raw)
            dest.write_bytes(stripped)
            logger.info(f"[Activity] {stem}: copied skeleton ({len(stripped)} bytes)")
        else:
            logger.warning(f"[Activity] {stem}: no skeleton file")

        if atlas_src.exists():
            dest = matched_dir / f"{hero_id}.atlas"
            shutil.copy2(atlas_src, dest)
            logger.info(f"[Activity] {stem}: copied atlas")
        else:
            logger.warning(f"[Activity] {stem}: no atlas file")

        processed.append({
            "stem": stem,
            "hero_id": hero_id,
            "has_skeleton": skel_src.exists(),
            "has_atlas": atlas_src.exists(),
            "has_texture": True,
        })

    result = {
        "activity_id": activity_id,
        "run_id": run_ts,
        "processed": len(processed),
        "errors": len(errors),
        "items": processed,
        "error_details": errors,
    }

    if processed:
        summary = {
            "run_id": run_ts,
            "mode": "activity_selected",
            "status": "success",
            "total_heroes": len(processed),
            "heroes": [
                {"hero_id": p["hero_id"], "hero_type": "Activity", "status": "success"}
                for p in processed
            ],
        }
        ensure_dir(run_root)
        from src.utils.json_utils import save_json
        save_json(run_root / "summary.json", summary)

        for p in processed:
            hero_summary = {
                "hero_id": p["hero_id"],
                "hero_type": "Activity",
                "status": "success",
                "matched_skeleton": f"matched/{p['hero_id']}.skel",
                "matched_atlas": f"matched/{p['hero_id']}.atlas",
                "matched_texture": f"matched/{p['hero_id']}.png",
            }
            save_json(activity_heroes_root / p["hero_id"] / "summary.json", hero_summary)

    return result


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)