from dataclasses import dataclass, field

@dataclass
class AppConfig:
    asset_bundle_folder: str = ""
    assetstudio_path: str = ""
    spine_cli_path: str = ""
    spine_exporter_path: str = ""
    gif_engine: str = "spine_cli"
    gif_export_fps: int = 30
    gif_export_scale: float = 1.0
    gif_export_pma: bool = False
    spine_gif_export_settings_path: str = ""
    use_internal_default_gif_preset: bool = True
    internal_default_gif_preset_path: str = "config/export_presets/default_gif.export.json"
    gif_output_open_after_complete: bool = True
    override_gif_scale: bool = False
    strict_preserve_export_preset: bool = True
    override_gif_bounds: bool = False
    gif_postprocess_enabled: bool = False
    gif_auto_trim_bad_leading_frames: bool = False
    gif_trim_max_scan_frames: int = 15
    gif_trim_fallback_by_animation: dict = field(default_factory=dict)
    gif_spinepro_reencode_enabled: bool = False
    gif_spinepro_preset_path: str = "config/spine_pro_gif_export.json"
    gif_spinepro_preset: dict | None = None
    output_root: str = "./output"
    library_root: str = "./library"
    timeout_minutes: int = 30
    assetstudio_export_types: list[str] = field(default_factory=lambda: [
        "tex2d",
        "textasset"
    ])
    assetstudio_cli_profile: str = "Auto"
    assetstudio_game: str = "Normal"
    assetstudio_types: list[str] = field(default_factory=lambda: [
        "Texture2D",
        "TextAsset"
    ])
    assetstudio_group_assets: str = "ByType"
    assetstudio_export_type: str = "Convert"
    assetstudio_fallback_export_types: list[str] = field(default_factory=lambda: [
        "Convert",
        "Raw",
        "Dump",
        "JSON"
    ])
    assetstudio_try_without_types: bool = True
    assetstudio_use_absolute_output_path: bool = True
    assetstudio_export_whole_hero_group_folder: bool = False
