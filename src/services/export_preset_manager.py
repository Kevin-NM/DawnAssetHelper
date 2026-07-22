import os
import json
from pathlib import Path
from src.services.logger_service import logger

class ExportPresetManager:
    def __init__(self, config):
        self.config = config
        
    def get_preset(self) -> tuple[str, str]:
        custom_path = self.config.spine_gif_export_settings_path
        if custom_path and os.path.exists(custom_path) and os.path.isfile(custom_path):
            logger.info(f"[GIFPreset] Using user custom GIF export preset:\n{custom_path}")
            return "user_custom", custom_path
            
        if getattr(self.config, 'use_internal_default_gif_preset', True):
            internal_path = Path(getattr(self.config, 'internal_default_gif_preset_path', "config/export_presets/default_gif.export.json"))
            
            if internal_path.exists():
                logger.info(f"[GIFPreset] Using internal default GIF export preset:\n{internal_path}")
                return "internal_default", str(internal_path)
            
        return "none", ""

    @staticmethod
    def normalize_gif_preset(data: dict) -> dict:
        bg = data.get("background")
        if bg in ["00000000", "#00000000", "0x00000000", "transparent", ""]:
            logger.info(f"[AutoGIF] Normalized GIF preset background from {bg} to null for Spine 3.8.75 compatibility.")
            data["background"] = None
        return data

    @staticmethod
    def apply_safe_no_crop_gif_settings(data: dict) -> dict:
        """Force Spine GIF export settings that keep all frames and avoid crop/alpha loss."""
        safe_settings = {
            "renderImages": True,
            "renderBones": False,
            "renderOthers": False,
            "maxBounds": True,
            "fitWidth": 0,
            "fitHeight": 0,
            "enlarge": False,
            "cropX": 0,
            "cropY": 0,
            "cropWidth": 0,
            "cropHeight": 0,
            "rangeStart": -1,
            "rangeEnd": -1,
            "lastFrame": False,
            "alphaThreshold": 0,
        }
        for key, value in safe_settings.items():
            if data.get(key) != value:
                logger.info(f"[GIFPreset] Safe no-crop setting: {key}: {data.get(key)} -> {value}")
            data[key] = value
        return data

    def create_runtime_preset(self, template_path: str, hero_id: str, project_file: Path, output_dir: Path, runtime_json_path: Path) -> tuple[bool, str]:
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template_data = json.load(f)
            
            # Deep copy template to track changes
            data = json.loads(json.dumps(template_data))
            
            data = self.normalize_gif_preset(data)
            data = self.apply_safe_no_crop_gif_settings(data)
            
            # Always override these core fields
            data['project'] = str(project_file.resolve())
            data['output'] = str(output_dir.resolve())
            data['skeleton'] = hero_id
            data['open'] = False
            
            strict_mode = getattr(self.config, 'strict_preserve_export_preset', True)
            
            if not strict_mode:
                # Flexible mode overrides
                if "renderImages" not in data:
                    data["renderImages"] = True
                if "renderBones" not in data:
                    data["renderBones"] = False
                if "renderOthers" not in data:
                    data["renderOthers"] = False
                if "skinType" not in data:
                    data["skinType"] = "default"
                if "skinNone" not in data:
                    data["skinNone"] = False
                
                if getattr(self.config, 'override_gif_bounds', False):
                    data['maxBounds'] = True
                    data['cropX'] = 0
                    data['cropY'] = 0
                    data['cropWidth'] = 0
                    data['cropHeight'] = 0
                    data['fitWidth'] = 0
                    data['fitHeight'] = 0
                    data['enlarge'] = False
                    
                if getattr(self.config, 'override_gif_scale', False):
                    scale_val = getattr(self.config, 'gif_export_scale', 100.0)
                    if scale_val > 0:
                        data['scale'] = scale_val
            
            # Perform Diff and Validation
            # background might be normalized to null, and safe no-crop export fields are intentionally forced.
            allowed_overrides = [
                'project', 'output', 'skeleton', 'open', 'background',
                'renderImages', 'renderBones', 'renderOthers',
                'maxBounds', 'fitWidth', 'fitHeight', 'enlarge',
                'cropX', 'cropY', 'cropWidth', 'cropHeight',
                'rangeStart', 'rangeEnd', 'lastFrame', 'alphaThreshold'
            ]
            changed_keys = []
            unexpected_changes = []
            
            # Use template_data as base for comparison
            # Note: normalize_gif_preset might change 'background' in template_data if we called it on template_data too,
            # but we only called it on 'data'.
            
            for key in data:
                old_val = template_data.get(key)
                new_val = data[key]
                if old_val != new_val:
                    changed_keys.append(key)
                    if key not in allowed_overrides:
                        unexpected_changes.append(key)
                        
            logger.info(f"[PresetDiff] Changed keys: {', '.join(changed_keys)}")
            for key in changed_keys:
                logger.info(f"[PresetDiff]   {key}: {template_data.get(key)} -> {data[key]}")
                
            if strict_mode and unexpected_changes:
                for key in unexpected_changes:
                    logger.error(f"[PresetDiff] ERROR: Unexpected preset field changed in strict mode: {key}")
                return False, f"Strict mode violation: Unexpectedly modified fields {unexpected_changes}"

            runtime_json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(runtime_json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
                
            logger.info(f"[AutoGIF] Runtime GIF preset created: {runtime_json_path}")
            return True, ""
        except Exception as e:
            msg = f"Failed to create runtime preset: {e}"
            logger.error(msg)
            return False, msg

