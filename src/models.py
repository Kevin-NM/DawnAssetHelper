from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class HeroBundleGroup:
    hero_id: str
    hero_number: int | None
    hero_type: str
    variant_ids: list[str]
    bundle_files: list[Path]
    file_count: int
    total_size: int
    status: str

@dataclass
class RunContext:
    run_id: str
    run_root: Path
    heroes_root: Path
    logs_root: Path

@dataclass
class HeroRunResult:
    hero_id: str
    hero_number: int | None
    hero_type: str
    bundle_files: list[str]
    status: str
    matched_skeleton: dict | None = None
    matched_atlas: dict | None = None
    matched_texture: dict | None = None
    manual_gif_ready_result: dict | None = None
    unpack_result: dict | None = None
    spine_import_result: dict | None = None
    auto_gif_result: dict | None = None
    assetstudio_attempts: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
