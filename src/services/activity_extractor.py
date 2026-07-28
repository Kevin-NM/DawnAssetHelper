import re
import json
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