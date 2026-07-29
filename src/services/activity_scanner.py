import re
import json
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from src.services.assetstudio_service import AssetStudioService
from src.services.process_runner import ProcessRunner
from src.services.logger_service import logger
from src.app_config import AppConfig


@dataclass
class ActivityInfo:
    activity_id: str
    activity_name: str
    pack_file: str
    pack_size: int
    spine_files: list[str] = field(default_factory=list)
    hero_ids: list[str] = field(default_factory=list)
    variant_ids: list[str] = field(default_factory=list)


def parse_activity_name(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^roles_showroles_spine_activity_", "", text)
    parts = text.split("_")
    for i, part in enumerate(parts):
        if part == "depend":
            break
    else:
        i = len(parts)
    name = "_".join(parts[:i])
    name = re.sub(r"_\d+$", "", name)
    return name


def scan_activity_spine_files(ab_folder: str) -> dict[str, list[str]]:
    folder = Path(ab_folder)
    if not folder.exists():
        return {}

    activity_pattern = re.compile(
        r"^roles_showroles_spine_activity_([^_]+(?:_[^_]+)*?)_depend_\d+\.ab$"
    )

    activity_groups: dict[str, list[str]] = {}
    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file() or not file_path.name.endswith(".ab"):
            continue
        match = activity_pattern.match(file_path.name)
        if match:
            activity_name = parse_activity_name(file_path.name)
            if activity_name not in activity_groups:
                activity_groups[activity_name] = []
            activity_groups[activity_name].append(file_path.name)

    return activity_groups


def extract_hero_ids_from_activity_text(text: str) -> list[str]:
    hero_ids = set()
    for match in re.finditer(r"(?:^|[\\/])hero(\d+)", text, re.IGNORECASE):
        hero_ids.add(f"hero{match.group(1)}")
    return sorted(hero_ids)


def extract_activity_pack_metadata(
    pack_path: str,
    assetstudio_path: str,
    config: AppConfig,
    temp_dir: str,
    timeout_sec: int = 120,
) -> Optional[dict]:
    pack_path = Path(pack_path)
    if not pack_path.exists():
        logger.error(f"Activity pack not found: {pack_path}")
        return None

    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    svc = AssetStudioService(assetstudio_path, config)
    if not svc.validate():
        logger.error("AssetStudio CLI not valid for activity pack extraction")
        return None

    output_dir = temp_dir / "activity_pack_extract"
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    success, cmd_str, stdout, stderr = svc.extract_bundle(
        pack_path, output_dir, timeout_sec, export_type="Convert", include_types=False
    )

    if not success:
        logger.error(f"Activity pack extraction without types failed: {stderr[:500]}")
        success, cmd_str, stdout, stderr = svc.extract_bundle(
            pack_path, output_dir, timeout_sec, export_type="Convert", include_types=True
        )
        if not success:
            logger.error(f"Activity pack extraction with types also failed: {stderr[:500]}")
            return None

    result = {"hero_ids": [], "text_assets": [], "raw_files": []}

    for root, dirs, files in os.walk(output_dir):
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, output_dir)

            hero_ids_from_name = extract_hero_ids_from_activity_text(f)
            result["hero_ids"].extend(hero_ids_from_name)

            if f.endswith((".txt", ".json", ".bytes", ".csv")):
                try:
                    content = Path(full_path).read_text(encoding="utf-8", errors="ignore")
                    result["text_assets"].append({
                        "path": rel_path,
                        "name": f,
                        "content_preview": content[:5000],
                    })
                    hero_ids = extract_hero_ids_from_activity_text(content)
                    result["hero_ids"].extend(hero_ids)
                except Exception:
                    try:
                        raw = Path(full_path).read_bytes()
                        result["text_assets"].append({
                            "path": rel_path,
                            "name": f,
                            "content_preview": repr(raw[:500]),
                        })
                    except Exception:
                        pass
            else:
                result["raw_files"].append(rel_path)

    result["hero_ids"] = sorted(set(result["hero_ids"]))
    return result


def detect_activity_packs(ab_folder: str) -> list[ActivityInfo]:
    folder = Path(ab_folder)
    if not folder.exists():
        return []

    pack_pattern = re.compile(r"^activities_packs_(\d+)\.ab$")
    activity_spine_groups = scan_activity_spine_files(ab_folder)

    activities = []
    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue
        match = pack_pattern.match(file_path.name)
        if not match:
            continue

        activity_id = match.group(1)
        info = ActivityInfo(
            activity_id=activity_id,
            activity_name=f"activity_{activity_id}",
            pack_file=file_path.name,
            pack_size=file_path.stat().st_size,
        )
        for spine_files in activity_spine_groups.values():
            info.spine_files.extend(spine_files)
        info.spine_files = sorted(set(info.spine_files))
        activities.append(info)

    return activities


def detect_activities(
    ab_folder: str,
    assetstudio_path: str,
    config: AppConfig,
    output_root: str,
    timeout_sec: int = 120,
) -> list[ActivityInfo]:
    folder = Path(ab_folder)
    if not folder.exists():
        return []

    pack_pattern = re.compile(r"^activities_packs_(\d+)\.ab$")
    activity_spine_groups = scan_activity_spine_files(ab_folder)

    temp_dir = Path(output_root) / "temp" / "activity_extract"

    activities = []
    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue
        match = pack_pattern.match(file_path.name)
        if not match:
            continue

        activity_id = match.group(1)
        activity_name = f"activity_{activity_id}"

        metadata = extract_activity_pack_metadata(
            str(file_path), assetstudio_path, config, str(temp_dir), timeout_sec
        )

        info = ActivityInfo(
            activity_id=activity_id,
            activity_name=activity_name,
            pack_file=file_path.name,
            pack_size=file_path.stat().st_size,
        )

        if metadata and metadata.get("hero_ids"):
            info.hero_ids = metadata["hero_ids"]

        for spine_name, spine_files in activity_spine_groups.items():
            for spine_file in spine_files:
                for hero_id in info.hero_ids:
                    if hero_id in spine_file:
                        if hero_id not in info.spine_files:
                            info.spine_files.append(spine_file)

        for spine_file_list in activity_spine_groups.values():
            for sf in spine_file_list:
                if sf not in info.spine_files:
                    info.spine_files.append(sf)

        info.spine_files = sorted(set(info.spine_files))

        activities.append(info)

    return activities


def find_hero_ab_files(ab_folder: str, hero_ids: list[str]) -> dict[str, list[str]]:
    folder = Path(ab_folder)
    if not folder.exists():
        return {}

    hero_pattern = re.compile(r"^roles_showroles_spine_(hero\d+)(?:_(hero\d+[a-zA-Z]?))?_depend_\d+\.ab$")

    result: dict[str, list[str]] = {}
    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file() or not file_path.name.endswith(".ab"):
            continue
        match = hero_pattern.match(file_path.name)
        if not match:
            continue
        main_id = match.group(1)
        if main_id in hero_ids:
            if main_id not in result:
                result[main_id] = []
            result[main_id].append(file_path.name)

    return result