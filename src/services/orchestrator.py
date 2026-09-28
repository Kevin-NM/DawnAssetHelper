import datetime
import traceback
import os
import shutil
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QThread
from src.models import HeroBundleGroup, RunContext, HeroRunResult
from src.app_config import AppConfig
from src.services.assetstudio_service import AssetStudioService
from src.services.spine_service import SpineService
from src.services.spine_exporter_service import SpineExporterService
from src.services.export_preset_manager import ExportPresetManager
from src.services.asset_matcher import AssetMatcher
from src.services.logger_service import logger
from src.utils.json_utils import save_json
from src.utils.path_utils import ensure_dir
from src.services.gif_post_processor import GIFPostProcessor
from src.utils.spine_utils import parse_spine_atlas

class OrchestratorWorker(QThread):
    progress_updated = Signal(int, int) # current, total
    step_updated = Signal(str)
    finished = Signal(dict)
    
    def __init__(self, config: AppConfig, heroes: list[HeroBundleGroup] = None, mode: str = "extract_only"):
        super().__init__()
        self.config = config
        self.heroes = heroes or []
        self.mode = mode
        self.is_cancelled = False
        self.asset_studio = None
        self.spine_service = None
        self.run_context = None
        
        self.run_summary = {}
        self.spine_exporter_service = None
        
    def run(self):
        try:
            self._do_work()
        except Exception as e:
            logger.error(f"Orchestrator error: {e}")
            logger.error(traceback.format_exc())
        finally:
            self.finished.emit(self.run_summary)
            
    def _find_latest_matched_dir(self, output_root: Path, hero_id: str) -> Path | None:
        from src.services.library_service import library_root_for
        saved = library_root_for(self.config) / "heroes" / hero_id / "matched"
        if saved.exists() and any(saved.rglob("*.atlas")) and any(saved.rglob("*.png")) and (any(saved.rglob("*.skel")) or any(saved.rglob("*.json"))):
            return saved
        runs_dir = output_root / "runs"
        if not runs_dir.exists(): return None
        
        runs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        for run in runs:
            matched = run / "heroes" / hero_id / "matched"
            if not matched.exists():
                continue
            has_atlas = (matched / f"{hero_id}.atlas").exists() or any(matched.glob("*.atlas"))
            has_skel = (matched / f"{hero_id}.skel").exists() or (matched / f"{hero_id}.json").exists() or any(matched.glob("*.skel")) or any(matched.glob("*.json"))
            has_png = any(matched.glob("*.png"))
            if has_atlas and has_skel and has_png:
                return matched
        return None

    def _find_latest_unpacked_dir(self, output_root: Path, hero_id: str) -> Path | None:
        runs_dir = output_root / "runs"
        if not runs_dir.exists(): return None

        runs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        for run in runs:
            unpacked = run / "heroes" / hero_id / "unpacked"
            if unpacked.exists() and any(unpacked.rglob("*.png")):
                return unpacked
        return None

    def _do_work(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_root = Path(self.config.output_root)
        if self.config.assetstudio_use_absolute_output_path:
            output_root = output_root.resolve()
            
        run_root = output_root / "runs" / timestamp
        
        self.run_context = RunContext(
            run_id=timestamp,
            run_root=run_root,
            heroes_root=run_root / "heroes",
            logs_root=run_root / "logs"
        )
        
        self.run_context.logs_root.mkdir(parents=True, exist_ok=True)
        logger.setup_run_logger(self.run_context.logs_root, timestamp)
        
        logger.info(f"Started run: {timestamp} (Mode: {self.mode})")
        
        if self.mode in ["extract_only", "extract_and_unpack", "extract_unpack_and_open", "extract_and_gif", "full", "full_open"]:
            self.asset_studio = AssetStudioService(self.config.assetstudio_path, self.config)
            if not self.asset_studio.validate():
                logger.error("AssetStudio path is invalid or does not exist.")
                return
                
        gif_engine = getattr(self.config, 'gif_engine', 'spine_cli')
        needs_unpack = self.mode in ["unpack_only", "extract_and_unpack", "extract_unpack_and_open", "full", "full_open"]
        needs_gif = self.mode in ["gif_only", "extract_and_gif", "full", "full_open", "test_gif_preset", "test_gif_preset_open"]

        if needs_unpack or (needs_gif and gif_engine == 'spine_cli'):
            self.spine_service = SpineService(self.config.spine_cli_path, self.config)
            if not self.spine_service.validate():
                logger.error("Spine CLI path is invalid or does not exist.")
                return

        if needs_gif and gif_engine == 'spine_exporter':
            exporter_path = getattr(self.config, 'spine_exporter_path', '')
            if exporter_path:
                self.spine_exporter_service = SpineExporterService(exporter_path)
                if not self.spine_exporter_service.validate():
                    logger.error("spine-export-cli path is invalid or does not exist.")
                    return
            else:
                logger.error("spine_exporter_path is not configured.")
                return

        total_heroes = len(self.heroes)
        success_count = 0
        failed_count = 0
        unpack_success_count = 0
        unpack_failed_count = 0
        spine_import_success_count = 0
        spine_import_failed_count = 0
        gif_success_count = 0
        gif_failed_count = 0
        hero_results = []
        
        for i, hero in enumerate(self.heroes):
            if self.is_cancelled:
                logger.warning("Run cancelled by user.")
                break
                
            self.step_updated.emit(f"Processing ({i+1}/{total_heroes}): {hero.hero_id}")
            self.progress_updated.emit(i, total_heroes)
            
            if self.mode in ["extract_only", "extract_and_unpack", "extract_unpack_and_open", "extract_and_gif", "full", "full_open"]:
                result = self._process_hero(hero)
            else:
                result = HeroRunResult(
                    hero_id=hero.hero_id,
                    hero_number=hero.hero_number,
                    hero_type=hero.hero_type,
                    bundle_files=[f.name for f in hero.bundle_files],
                    status="success"
                )
                
            if self.mode in ["unpack_only", "extract_and_unpack", "extract_unpack_and_open", "full", "full_open"]:
                if self.mode in ["extract_and_unpack", "extract_unpack_and_open", "full", "full_open"] and result.status != "success":
                    logger.warning(f"Skipping unpack for {hero.hero_id} because extract failed.")
                else:
                    unpack_res = self._unpack_hero(hero.hero_id, output_root)
                    result.unpack_result = unpack_res
                    if unpack_res["status"] in ["success", "success_with_dimension_mismatches"]:
                        unpack_success_count += 1
                    else:
                        unpack_failed_count += 1

            if self.mode in ["gif_only", "extract_and_gif", "full", "full_open", "test_gif_preset", "test_gif_preset_open"]:
                if self.mode in ["extract_and_gif", "full", "full_open"] and result.status != "success":
                    logger.warning(f"Skipping GIF export for {hero.hero_id} because extract failed.")
                else:
                    unpack_res = getattr(result, 'unpack_result', None)
                    gif_engine = getattr(self.config, 'gif_engine', 'spine_cli')
                    if gif_engine == 'spine_exporter':
                        import_res, auto_gif_res, render_workspace_res = self._gif_hero_with_exporter(hero.hero_id, output_root)
                    else:
                        import_res, auto_gif_res, render_workspace_res = self._gif_hero(hero.hero_id, output_root, unpack_res)
                    result.spine_import_result = import_res
                    result.auto_gif_result = auto_gif_res
                    if not hasattr(result, "render_workspace_result"):
                        setattr(result, "render_workspace_result", render_workspace_res)
                    
                    if import_res["status"] == "success":
                        spine_import_success_count += 1
                    elif import_res["status"] != "skipped":
                        spine_import_failed_count += 1
                        
                    if auto_gif_res and auto_gif_res.get("status") in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"]:
                        gif_success_count += 1
                        if auto_gif_res.get("status") == "success_with_warnings" and result.status == "success":
                            result.status = "success_with_warnings"
                            
                        # GIF Postprocess
                        if self.config.gif_postprocess_enabled:
                            self.step_updated.emit(f"Post-processing GIFs ({i+1}/{total_heroes}): {hero.hero_id}")
                            gif_dir = self.run_context.heroes_root / hero.hero_id / "gif"
                            post_processor = GIFPostProcessor(self.config)
                            post_res = post_processor.process_dir(gif_dir, hero.hero_id)
                            result.gif_postprocess_result = post_res
                    else:
                        gif_failed_count += 1
                        # if test_gif_preset, result status doesn't fail the whole hero run if we just want to see
                        if self.mode != "test_gif_preset":
                            pass
            
            self._save_hero_summary(self.run_context.heroes_root / hero.hero_id, result)
            hero_results.append(result)
            
            if result.status in ["success", "success_with_warnings"]:
                success_count += 1
            else:
                failed_count += 1
                
        self.progress_updated.emit(total_heroes, total_heroes)
        self.step_updated.emit("Generating summary...")
        
        run_summary = {
            "run_id": timestamp,
            "mode": self.mode,
            "status": "success" if not self.is_cancelled else "cancelled",
            "total_heroes": total_heroes,
            "success_count": success_count,
            "failed_count": failed_count,
            "unpack_success_count": unpack_success_count,
            "unpack_failed_count": unpack_failed_count,
            "spine_import_success_count": spine_import_success_count,
            "spine_import_failed_count": spine_import_failed_count,
            "gif_success_count": gif_success_count,
            "gif_failed_count": gif_failed_count,
            "heroes": [
                {
                    "hero_id": r.hero_id,
                    "hero_type": r.hero_type,
                    "status": r.status,
                    "summary_file": f"heroes/{r.hero_id}/summary.json"
                } for r in hero_results
            ]
        }
        
        self.run_summary = run_summary
        save_json(run_root / "summary.json", run_summary)
        logger.info("Run finished.")
        
        if self.mode in ["extract_unpack_and_open", "full_open", "test_gif_preset", "test_gif_preset_open"] and not self.is_cancelled:
            self._open_results(hero_results, run_root)
            
    def _open_results(self, hero_results, run_root: Path):
        if not hero_results: return
        try:
            if len(hero_results) == 1:
                hero_id = hero_results[0].hero_id
                gif_dir = run_root / "heroes" / hero_id / "gif"
                spine_ready = run_root / "heroes" / hero_id / "spine_ready"
                spine_project = run_root / "heroes" / hero_id / "spine_project"
                
                auto_gif_res = hero_results[0].auto_gif_result
                if auto_gif_res and auto_gif_res.get("status") in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"] and gif_dir.exists():
                    logger.info(f"[AutoGIF] Opening GIF folder: heroes/{hero_id}/gif")
                    os.startfile(str(gif_dir))
                elif auto_gif_res and auto_gif_res.get("status") not in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"]:
                    if spine_project.exists():
                        logger.info(f"You can open the .spine project manually to verify animations and export settings.")
                        logger.info(f"[ManualGIF] Opening result folder: heroes/{hero_id}/spine_project")
                        os.startfile(str(spine_project))
                    elif spine_ready.exists():
                        logger.info(f"spine_ready is also available for manual import.")
                        logger.info(f"[ManualGIF] Opening result folder: heroes/{hero_id}/spine_ready")
                        os.startfile(str(spine_ready))
                    else:
                        os.startfile(str(run_root / "heroes" / hero_id))
                elif spine_ready.exists():
                    logger.info(f"[ManualGIF] Opening result folder: heroes/{hero_id}/spine_ready")
                    os.startfile(str(spine_ready))
                else:
                    os.startfile(str(run_root / "heroes" / hero_id))
            else:
                os.startfile(str(run_root / "heroes"))
        except Exception as e:
            logger.error(f"Failed to open results folder: {e}")

    def _unpack_hero(self, hero_id: str, output_root: Path) -> dict:
        matched_dir = None
        if self.mode in ["extract_and_unpack", "extract_unpack_and_open", "full", "full_open"]:
            matched_dir = self.run_context.heroes_root / hero_id / "matched"
        else:
            matched_dir = self._find_latest_matched_dir(output_root, hero_id)
            
        unpacked_dir = self.run_context.heroes_root / hero_id / "unpacked"
        
        res = {
            "status": "failed",
            "input_folder": "matched",
            "atlas": f"matched/{hero_id}.atlas",
            "output_folder": "unpacked",
            "png_count": 0,
            "errors": []
        }
        
        if not matched_dir:
            msg = f"Cannot find matched directory for {hero_id}. Please extract first."
            logger.error(msg)
            res["errors"].append(msg)
            return res
            
        success, png_count, errors = self.spine_service.unpack_atlas(hero_id, matched_dir, unpacked_dir)
        
        if success:
            res["status"] = "success"
            res["png_count"] = png_count
            
            # Validation is already called inside unpack_atlas or we can call it here for clearer status
            valid_res = self.spine_service.validate_unpacked_images(hero_id, matched_dir, unpacked_dir)
            res["validation"] = valid_res
            if valid_res.get("status") == "failed_dimension_mismatch":
                res["status"] = "success_with_dimension_mismatches"
                res["errors"].append("Unpacked images do not match atlas original dimensions. GIF export may be broken.")
        else:
            res["errors"].extend(errors)
            
        return res
        
    def _gif_hero(self, hero_id: str, output_root: Path, unpack_res: dict = None) -> tuple[dict, dict, dict]:
        logger.info(f"[AutoGIF] Starting full auto GIF workflow for {hero_id}")
        
        hero_dir = self.run_context.heroes_root / hero_id
        matched_dir = hero_dir / "matched"
        unpacked_dir = hero_dir / "unpacked"
        render_workspace_dir = hero_dir / "render_workspace"
        spine_project_dir = hero_dir / "spine_project"
        gif_output_dir = hero_dir / "gif"
        
        import_res = {"status": "failed", "input_skeleton": None, "project_file": None, "errors": []}
        auto_gif_res = {"status": "failed", "gif_count": 0, "gif_files": [], "errors": [], "warnings": []}
        
        # Dimension mismatches are risky, but do not halt automatically because
        # some Spine atlas workflows intentionally unpack to the original rect.
        if unpack_res and unpack_res.get("status") == "success_with_dimension_mismatches":
            msg = "[AutoGIF] WARNING: Unpacked image dimensions differ from atlas metadata. Continuing export, but output may need inspection."
            logger.warning(msg)
            auto_gif_res["warnings"].append(msg)

        if not matched_dir.exists():
            latest_matched = self._find_latest_matched_dir(output_root, hero_id)
            if latest_matched:
                matched_dir = latest_matched
                logger.info(f"[AutoGIF] Using latest matched folder for {hero_id}: {matched_dir}")

        if not unpacked_dir.exists() or not any(unpacked_dir.rglob("*.png")):
            latest_unpacked = self._find_latest_unpacked_dir(output_root, hero_id)
            if latest_unpacked:
                unpacked_dir = latest_unpacked
                logger.info(f"[AutoGIF] Using latest unpacked folder for {hero_id}: {unpacked_dir}")

        manager = ExportPresetManager(self.config)
        preset_mode, preset_file = manager.get_preset()
        
        import_res = {
            "status": "failed",
            "input_skeleton": f"matched/{hero_id}.skel",
            "project_file": f"spine_project/{hero_id}.spine",
            "errors": []
        }
        
        auto_gif_res = {
            "status": "failed",
            "preset_mode": preset_mode,
            "preset_template": preset_file,
            "runtime_preset": "",
            "project_file": f"spine_project/{hero_id}.spine",
            "output_folder": "gif",
            "exit_code": -1,
            "gif_count": 0,
            "gif_files": [],
            "stdout_file": "gif/export_stdout.txt",
            "stderr_file": "gif/export_stderr.txt",
            "warnings": [],
            "errors": []
        }
        
        if preset_mode == "none" or not preset_file:
            auto_gif_res["status"] = "failed_missing_preset"
            msg = "GIF preset is not initialized."
            logger.error(msg)
            auto_gif_res["errors"].append(msg)
            auto_gif_res["errors"].append("Please click 'Import GIF Preset' and select a GIF export settings JSON generated by Spine Pro.")
            # default render workspace result when preset missing
            default_render_workspace_res = {
                "status": "skipped",
                "folder": "render_workspace",
                "skeleton": None,
                "root_image_count": 0,
                "images_folder_count": 0,
                "errors": []
            }
            return import_res, auto_gif_res, default_render_workspace_res
            
        if not matched_dir.exists():
            msg = f"Cannot find matched directory for {hero_id}. Please extract/unpack first."
            logger.error(msg)
            import_res["errors"].append(msg)
            auto_gif_res["errors"].append("Skipped because import failed")
            default_render_workspace_res = {
                "status": "skipped",
                "folder": "render_workspace",
                "skeleton": None,
                "root_image_count": 0,
                "images_folder_count": 0,
                "errors": []
            }
            return import_res, auto_gif_res, default_render_workspace_res
            
        matched_pngs = list(matched_dir.glob("*.png")) if matched_dir.exists() else []
        if len(matched_pngs) == 0:
            msg = "Matched atlas page texture is missing. AutoGIF must use original atlas page PNGs, not unpacked region PNGs."
            logger.error(msg)
            auto_gif_res["status"] = "failed_missing_images"
            auto_gif_res["errors"].append(msg)
            default_render_workspace_res = {
                "status": "skipped",
                "folder": "render_workspace",
                "skeleton": None,
                "root_image_count": 0,
                "images_folder_count": 0,
                "errors": []
            }
            return import_res, auto_gif_res, default_render_workspace_res
            
        logger.info(f"[AutoGIF] Verified matched atlas/page textures: {len(matched_pngs)} png files.")
            
        # Prepare render workspace
        r_root, r_imgs, r_errors = self.spine_service.prepare_render_workspace(hero_id, matched_dir, unpacked_dir, render_workspace_dir)
        render_workspace_res = {
            "status": "success" if not r_errors else "failed",
            "folder": "render_workspace",
            "skeleton": f"render_workspace/{hero_id}.skel",
            "root_image_count": r_root,
            "images_folder_count": r_imgs,
            "errors": r_errors
        }
        
        if r_errors:
            auto_gif_res["status"] = "failed_render_workspace"
            auto_gif_res["errors"].extend(r_errors)
            return import_res, auto_gif_res, render_workspace_res
        
        # Import the whole render workspace so Spine resolves the sibling atlas/page texture.
        # Importing only the .skel file can create a project with missing materials.
        success, project_file, errors, stdout, stderr = self.spine_service.import_project(hero_id, render_workspace_dir, spine_project_dir)
        if success:
            import_res["status"] = "success"
        else:
            import_res["errors"].extend(errors)
            auto_gif_res["errors"].append("Skipped because import failed")
            auto_gif_res["stdout"] = stdout
            auto_gif_res["stderr"] = stderr
            return import_res, auto_gif_res, render_workspace_res

        # Keep the same atlas/page textures beside the generated project for GIF export.
        copied_count, copy_errors = self.spine_service.prepare_spine_project_local_images(
            hero_id=hero_id,
            image_source_dir=render_workspace_dir,
            spine_project_dir=spine_project_dir
        )
        
        auto_gif_res["spine_project_images"] = {
            "mode": "local_copy",
            "source": "matched_atlas_pages",
            "target": "spine_project",
            "copied_count": copied_count,
            "status": "success" if not copy_errors else "failed",
            "errors": copy_errors
        }
        
        if copy_errors:
            auto_gif_res["status"] = "failed_local_images"
            auto_gif_res["errors"].extend(copy_errors)
            return import_res, auto_gif_res, render_workspace_res
            
        logger.info("[AutoGIF] Exporting GIF after copying local images to spine_project")
            
        runtime_preset_path = gif_output_dir / "runtime_gif.export.json"
        auto_gif_res["runtime_preset"] = f"gif/{runtime_preset_path.name}"
        auto_gif_res["preset_mode"] = "runtime_from_template"
        
        # Clean gif_output_dir before creating runtime preset
        if gif_output_dir.exists():
            for item in gif_output_dir.iterdir():
                if item.is_file():
                    try: item.unlink()
                    except Exception as e: logger.warning(f"Failed to delete {item}: {e}")
        else:
            gif_output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[AutoGIF] Template preset exists: {Path(preset_file).exists()}")
        logger.info(f"[AutoGIF] Runtime preset path: {runtime_preset_path}")
        
        success, error_msg = manager.create_runtime_preset(
            template_path=preset_file,
            hero_id=hero_id,
            project_file=project_file,
            output_dir=gif_output_dir,
            runtime_json_path=runtime_preset_path
        )
        if not success or not runtime_preset_path.exists():
            auto_gif_res["status"] = "failed_runtime_preset_missing"
            msg = "Runtime GIF preset was not created before calling Spine CLI."
            logger.error(f"[AutoGIF] ERROR: {msg}")
            auto_gif_res["errors"].append(msg)
            if error_msg:
                auto_gif_res["errors"].append(error_msg)
            # Use the latest render_workspace_res which is defined earlier
            return import_res, auto_gif_res, render_workspace_res
                    
        settings_path = runtime_preset_path
        status, count, gif_errors, gif_warnings, mesh_warning_count, files, export_stdout, export_stderr, exit_code = self.spine_service.export_gif(hero_id, project_file, gif_output_dir, settings_path, hero_dir)
        auto_gif_res["exit_code"] = exit_code
        auto_gif_res["status"] = status
        auto_gif_res["warnings"] = gif_warnings
        auto_gif_res["mesh_dimension_warning_count"] = mesh_warning_count
        
        try:
            import json
            with open(settings_path, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)
            auto_gif_res["preset_maxBounds"] = preset_data.get("maxBounds")
            auto_gif_res["preset_scale"] = preset_data.get("scale")
            auto_gif_res["preset_outputType"] = preset_data.get("outputType")
            auto_gif_res["preset_animationType"] = preset_data.get("animationType")
        except:
            pass
        
        if status in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"]:
            auto_gif_res["gif_count"] = count
            auto_gif_res["gif_files"] = [f"gif/{f}" for f in files]
            if status == "success":
                logger.info(f"[AutoGIF] GIF export success for {hero_id}. GIF files: {count}")
            elif status == "output_with_mesh_dimension_warnings":
                logger.warning(f"[AutoGIF] GIF export produced files but with mesh dimension warnings ({mesh_warning_count}).")

            # Remove the Spine CLI cold-start fade-in (Esoteric forum 13542 / spine-editor #538).
            # The untouched original GIF is preserved as <name>.original.gif.
            try:
                from src.services.gif_post_processor import GIFPostProcessor
                fade_res = GIFPostProcessor(self.config).remove_fadein_in_dir(gif_output_dir, hero_id)
                auto_gif_res["fadein_removal"] = fade_res
                total_removed = sum(it.get("fadein_frames_removed", 0) for it in fade_res.get("items", []))
                logger.info(f"[AutoGIF] Fade-in removal done for {hero_id}. Total fade-in frames removed: {total_removed}")
            except Exception as e:
                logger.error(f"[AutoGIF] Fade-in removal failed for {hero_id}: {e}")
                auto_gif_res.setdefault("warnings", []).append(f"Fade-in removal failed: {e}")
        else:
            auto_gif_res["errors"].extend(gif_errors)
            auto_gif_res["stdout"] = export_stdout
            auto_gif_res["stderr"] = export_stderr
            
            if status == "failed_invalid_preset":
                auto_gif_res["errors"].append("GIF export preset is incompatible with your Spine version.")
                auto_gif_res["errors"].append("Please import a GIF preset generated by Spine Pro Export dialog.")
            
        return import_res, auto_gif_res, render_workspace_res

    def _gif_hero_with_exporter(self, hero_id: str, output_root: Path) -> tuple[dict, dict, dict]:
        logger.info(f"[SpineExporter] Starting GIF export for {hero_id} using spine-export-cli")

        hero_dir = self.run_context.heroes_root / hero_id
        matched_dir = hero_dir / "matched"
        gif_output_dir = hero_dir / "gif"

        import_res = {"status": "skipped", "input_skeleton": None, "project_file": None, "errors": []}
        render_workspace_res = {"status": "skipped", "folder": None, "skeleton": None, "root_image_count": 0, "images_folder_count": 0, "errors": []}

        auto_gif_res = {
            "status": "failed",
            "engine": "spine_exporter",
            "preset_mode": "spine_exporter_cli",
            "preset_template": "",
            "runtime_preset": "",
            "project_file": "",
            "output_folder": "gif",
            "exit_code": -1,
            "gif_count": 0,
            "gif_files": [],
            "stdout_file": "gif/export_stdout.txt",
            "stderr_file": "gif/export_stderr.txt",
            "warnings": [],
            "errors": []
        }

        if not matched_dir.exists():
            latest_matched = self._find_latest_matched_dir(output_root, hero_id)
            if latest_matched:
                matched_dir = latest_matched
                logger.info(f"[SpineExporter] Using latest matched folder: {matched_dir}")

        if not matched_dir.exists():
            msg = f"Cannot find matched directory for {hero_id}. Please extract first."
            logger.error(msg)
            auto_gif_res["errors"].append(msg)
            return import_res, auto_gif_res, render_workspace_res

        skel_file = matched_dir / f"{hero_id}.skel"
        if not skel_file.exists():
            skel_file = matched_dir / f"{hero_id}.json"
        if not skel_file.exists():
            skel_files = list(matched_dir.glob("*.skel")) + list(matched_dir.glob("*.json"))
            if skel_files:
                skel_file = skel_files[0]
                logger.info(f"[SpineExporter] Found skeleton by glob: {skel_file.name}")
        atlas_file = matched_dir / f"{hero_id}.atlas"
        if not atlas_file.exists():
            atlas_files = list(matched_dir.glob("*.atlas"))
            if atlas_files:
                atlas_file = atlas_files[0]
                logger.info(f"[SpineExporter] Found atlas by glob: {atlas_file.name}")
        if not skel_file.exists() or not atlas_file.exists():
            msg = f"Missing .skel or .atlas in {matched_dir}"
            logger.error(msg)
            auto_gif_res["errors"].append(msg)
            return import_res, auto_gif_res, render_workspace_res

        # Clean gif output dir
        if gif_output_dir.exists():
            for item in gif_output_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete {item}: {e}")
        else:
            gif_output_dir.mkdir(parents=True, exist_ok=True)

        fps = getattr(self.config, 'gif_export_fps', 30)
        scale = getattr(self.config, 'gif_export_scale', 1.0)
        pma = getattr(self.config, 'gif_export_pma', False)
        timeout_sec = getattr(self.config, 'timeout_minutes', 30) * 60

        status, count, gif_errors, gif_warnings, files, export_stdout, export_stderr, exit_code = self.spine_exporter_service.export_gif(
            hero_id=hero_id,
            matched_dir=matched_dir,
            gif_output_dir=gif_output_dir,
            fps=fps,
            scale=scale,
            pma=pma,
            timeout=timeout_sec,
        )

        auto_gif_res["exit_code"] = exit_code
        auto_gif_res["status"] = status
        auto_gif_res["warnings"] = gif_warnings

        if status in ["success", "success_with_warnings"]:
            auto_gif_res["gif_count"] = count
            auto_gif_res["gif_files"] = [f"gif/{f}" for f in files]
            logger.info(f"[SpineExporter] GIF export success for {hero_id}. GIF files: {count}")

            try:
                from src.services.gif_post_processor import GIFPostProcessor
                fade_res = GIFPostProcessor(self.config).remove_fadein_in_dir(gif_output_dir, hero_id)
                auto_gif_res["fadein_removal"] = fade_res
                total_removed = sum(it.get("fadein_frames_removed", 0) for it in fade_res.get("items", []))
                logger.info(f"[SpineExporter] Fade-in removal done for {hero_id}. Total removed: {total_removed}")
            except Exception as e:
                logger.error(f"[SpineExporter] Fade-in removal failed for {hero_id}: {e}")
                auto_gif_res.setdefault("warnings", []).append(f"Fade-in removal failed: {e}")

            if getattr(self.config, 'gif_spinepro_reencode_enabled', False):
                try:
                    from src.services.gif_reencoder import GifReencoder
                    inline_preset = getattr(self.config, 'gif_spinepro_preset', None)
                    preset_path = getattr(self.config, 'gif_spinepro_preset_path', '')
                    reencode_res = GifReencoder(
                        inline_preset=inline_preset,
                        preset_path=preset_path if not inline_preset else None,
                    ).reencode_dir(gif_output_dir, hero_id)
                    auto_gif_res["spinepro_reencode"] = reencode_res
                    logger.info(f"[SpineExporter] Spine Pro re-encode done for {hero_id}: {reencode_res.get('status')}")
                except Exception as e:
                    logger.error(f"[SpineExporter] Spine Pro re-encode failed for {hero_id}: {e}")
                    auto_gif_res.setdefault("warnings", []).append(f"Spine Pro re-encode failed: {e}")
        else:
            auto_gif_res["errors"].extend(gif_errors)
            auto_gif_res["stdout"] = export_stdout
            auto_gif_res["stderr"] = export_stderr

        return import_res, auto_gif_res, render_workspace_res

    def manual_baseline_export_test(self, manual_project_path: str, output_root: str = None) -> dict:
        """Diagnostic: Export a manually verified .spine project using the current GIF preset."""
        res = {
            "status": "failed",
            "project": manual_project_path,
            "output_dir": "",
            "gif_count": 0,
            "mesh_warning_count": 0,
            "errors": []
        }
        
        try:
            manual_project = Path(manual_project_path)
            if not manual_project.exists():
                res["errors"].append(f"Manual project file not found: {manual_project_path}")
                return res
                
            output_root_path = Path(output_root or self.config.output_root)
            debug_dir = output_root_path / "debug_manual_export"
            debug_dir.mkdir(parents=True, exist_ok=True)
            res["output_dir"] = str(debug_dir)
            
            from src.services.export_preset_manager import ExportPresetManager
            manager = ExportPresetManager(self.config)
            preset_type, preset_file = manager.get_preset()
            
            if preset_type == "none":
                res["errors"].append("No GIF export preset found in config.")
                return res
                
            runtime_preset = debug_dir / "runtime_manual_test.export.json"
            
            # Use strict mode for baseline test
            success, error_msg = manager.create_runtime_preset(
                template_path=preset_file,
                hero_id="manual_test",
                project_file=manual_project,
                output_dir=debug_dir,
                runtime_json_path=runtime_preset
            )
            
            if not success:
                res["errors"].append(f"Failed to create runtime preset: {error_msg}")
                return res

            if self.spine_service is None:
                self.spine_service = SpineService(self.config.spine_cli_path, self.config)
                if not self.spine_service.validate():
                    res["errors"].append("Spine CLI path is invalid or does not exist.")
                    return res
                 
            status, count, gif_errors, warnings, mesh_count, files, stdout, stderr, exit_code = self.spine_service.export_gif(
                hero_id="manual_test",
                project_file=manual_project,
                gif_output_dir=debug_dir,
                export_settings_json=runtime_preset,
                hero_dir=None
            )
            
            res["status"] = status
            res["gif_count"] = count
            res["mesh_warning_count"] = mesh_count
            res["errors"].extend(gif_errors)
            
            if status in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"]:
                logger.info(f"[ManualBaseline] Export completed. Status: {status}, GIFs: {count}, Mesh Warnings: {mesh_count}")
            else:
                logger.error(f"[ManualBaseline] Export failed. Status: {status}")
                
            return res
            
        except Exception as e:
            msg = f"Manual baseline test exception: {e}"
            logger.error(msg)
            res["errors"].append(msg)
            return res

    def compare_auto_vs_manual_project(self, hero_id: str, manual_project_path: str) -> dict:
        """Diagnostic: Compare auto-generated project with a manually verified one."""
        res = {
            "hero_id": hero_id,
            "auto_project": "",
            "manual_project": manual_project_path,
            "auto_mesh_warnings": 0,
            "manual_mesh_warnings": 0,
            "diagnosis": ""
        }
        
        output_root = Path(self.config.output_root)
        auto_project = output_root / "heroes" / hero_id / "spine_project" / f"{hero_id}.spine"
        res["auto_project"] = str(auto_project)
        
        # Get auto warning count from export_stdout.txt if exists
        auto_stdout = output_root / "heroes" / hero_id / "gif" / "export_stdout.txt"
        if auto_stdout.exists():
            content = auto_stdout.read_text(encoding='utf-8')
            res["auto_mesh_warnings"] = content.count("WARNING: Mesh image file dimensions changed")
            
        # Run manual baseline test to get manual warning count
        manual_res = self.manual_baseline_export_test(manual_project_path)
        res["manual_mesh_warnings"] = manual_res.get("mesh_warning_count", 0)
        
        if res["auto_mesh_warnings"] > 0 and res["manual_mesh_warnings"] == 0:
            diag = "[Diagnosis] Auto project uses image files with dimensions that differ from mesh data.\n"
            diag += "[Diagnosis] Manual project does not have this issue.\n"
            diag += "[Diagnosis] Problem is likely in auto import image source (unpacked PNGs), not GIF preset."
        elif res["auto_mesh_warnings"] > 0 and res["manual_mesh_warnings"] > 0:
            diag = "[Diagnosis] Both auto and manual projects have mesh dimension warnings.\n"
            diag += "[Diagnosis] Problem might be inherent to the skeleton data or the GIF preset's handling of these meshes."
        else:
            diag = "[Diagnosis] No significant mesh warning difference detected."
            
        res["diagnosis"] = diag
        logger.info(f"\n--- Project Comparison Diagnosis ---\n{diag}\n-----------------------------------")
        
        return res

    def _create_spine_ready(self, hero_id: str, matched_dir: Path, spine_ready_dir: Path) -> dict:
        res = {
            "status": "failed",
            "folder": "spine_ready",
            "files": [],
            "errors": []
        }
        
        skel = matched_dir / f"{hero_id}.skel"
        atlas = matched_dir / f"{hero_id}.atlas"
        
        missing = [f.name for f in (skel, atlas) if not f.exists()]
        if missing:
            res["errors"].append(f"Missing {', '.join(missing)}")
            return res

        texture_paths = []
        try:
            atlas_data = parse_spine_atlas(atlas)
            for page in atlas_data.get("pages", []):
                page_name = page.page_name
                texture = matched_dir / page_name
                if not texture.exists() and not page_name.lower().endswith(".png"):
                    texture = matched_dir / f"{page_name}.png"
                if texture.exists():
                    texture_paths.append((page_name if page_name.lower().endswith(".png") else f"{page_name}.png", texture))
                else:
                    res["errors"].append(f"Missing atlas page texture: {page_name}")
        except Exception as e:
            logger.warning(f"[ManualGIF] Failed to parse atlas pages, falling back to matched PNGs: {e}")

        if not texture_paths:
            fallback_png = matched_dir / f"{hero_id}.png"
            if fallback_png.exists():
                texture_paths.append((fallback_png.name, fallback_png))
            else:
                for png in sorted(matched_dir.rglob("*.png")):
                    texture_paths.append((png.relative_to(matched_dir).as_posix(), png))

        if not texture_paths:
            res["errors"].append("Missing texture PNG")
            return res

        if res["errors"]:
            return res
            
        if spine_ready_dir.exists():
            for item in spine_ready_dir.iterdir():
                if item.is_file():
                    try: item.unlink()
                    except Exception as e: logger.warning(f"Failed to delete {item}: {e}")
        else:
            spine_ready_dir.mkdir(parents=True, exist_ok=True)
            
        logger.info(f"[ManualGIF] Creating spine_ready folder for {hero_id}")
        
        try:
            shutil.copy2(skel, spine_ready_dir / skel.name)
            logger.info(f"[ManualGIF] Copied matched/{skel.name} -> spine_ready/{skel.name}")
            shutil.copy2(atlas, spine_ready_dir / atlas.name)
            logger.info(f"[ManualGIF] Copied matched/{atlas.name} -> spine_ready/{atlas.name}")

            copied_texture_files = []
            for page_name, texture in texture_paths:
                safe_parts = [p for p in page_name.replace("\\", "/").split("/") if p and p not in [".", ".."]]
                texture_dest = spine_ready_dir.joinpath(*safe_parts)
                texture_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(texture, texture_dest)
                copied_rel = texture_dest.relative_to(spine_ready_dir).as_posix()
                copied_texture_files.append(copied_rel)
                logger.info(f"[ManualGIF] Copied matched/{texture.relative_to(matched_dir).as_posix()} -> spine_ready/{copied_rel}")
            
            readme = spine_ready_dir / "README_MANUAL_GIF.txt"
            readme_content = f"""DawnAssetHelper manual Spine workflow

Files in this folder:
- {skel.name}
- {atlas.name}
{chr(10).join(f'- {name}' for name in copied_texture_files)}

Open Spine Pro manually and import/open the skeleton file.
Then export GIF from Spine Pro using your preferred settings.

If the atlas or texture is not found in Spine, make sure the .atlas and .png files are in the same folder as the .skel file."""
            readme.write_text(readme_content, encoding='utf-8')
            
            logger.info(f"[ManualGIF] Ready for manual Spine Pro workflow: heroes/{hero_id}/spine_ready")
            
            res["status"] = "success"
            res["files"] = [
                f"spine_ready/{skel.name}",
                f"spine_ready/{atlas.name}",
                *[f"spine_ready/{name}" for name in copied_texture_files]
            ]
        except Exception as e:
            res["errors"].append(str(e))
            logger.error(f"Failed to create spine_ready: {e}")
            
        return res

    def _process_hero(self, hero: HeroBundleGroup) -> HeroRunResult:
        hero_dir = self.run_context.heroes_root / hero.hero_id
        raw_export_dir = hero_dir / "raw_export"
        matched_dir = hero_dir / "matched"
        
        raw_export_dir.mkdir(parents=True, exist_ok=True)
        
        result = HeroRunResult(
            hero_id=hero.hero_id,
            hero_number=hero.hero_number,
            hero_type=hero.hero_type,
            bundle_files=[f.name for f in hero.bundle_files],
            status="failed",
            assetstudio_attempts=[]
        )
        
        if hero.status == "Partial":
            msg = f"Hero {hero.hero_id} has Partial status (only 1 AB found)"
            logger.warning(msg)
            result.warnings.append(msg)
            
        success = True
        has_any_export = False
        
        for bundle_file in hero.bundle_files:
            if self.is_cancelled:
                success = False
                break
                
            logger.info(f"Extracting bundle: {bundle_file.name}")
            bundle_stem = bundle_file.stem
            
            fallback_types = self.config.assetstudio_fallback_export_types
            attempts_for_bundle = []
            bundle_success = False
            
            for export_type in fallback_types:
                if self.is_cancelled:
                    break
                    
                attempt_name = f"{export_type}_with_types"
                if self._try_export_attempt(bundle_file, raw_export_dir, attempt_name, export_type, True, attempts_for_bundle, result):
                    bundle_success = True
                    has_any_export = True
                    break
                    
                if self.config.assetstudio_try_without_types:
                    attempt_name = f"{export_type}_without_types"
                    if self._try_export_attempt(bundle_file, raw_export_dir, attempt_name, export_type, False, attempts_for_bundle, result):
                        bundle_success = True
                        has_any_export = True
                        break

            if not bundle_success and not self.is_cancelled:
                logger.error(f"AssetStudio exported nothing for {bundle_file.name} after all attempts.")
                
        if not has_any_export and success:
            msg = "AssetStudio exported nothing for all related bundle files. Please check CLI profile, export types, or try loading the same bundles in AssetStudioGUI."
            logger.error(msg)
            result.errors.insert(0, msg)
            result.status = "failed"
            return result
            
        if not success:
            result.status = "failed"
            return result
            
        # Match assets
        matcher = AssetMatcher(raw_export_dir, matched_dir, hero.hero_id)
        skel, atlas, texture = matcher.match_all()
        
        result.matched_skeleton = skel
        result.matched_atlas = atlas
        result.matched_texture = texture
        
        if not skel: result.errors.append("Missing skeleton file")
        if not atlas: result.errors.append("Missing atlas file")
        if not texture: result.errors.append("Missing texture file")
        
        if skel and atlas and texture:
            result.status = "success"
            spine_ready_dir = hero_dir / "spine_ready"
            result.manual_gif_ready_result = self._create_spine_ready(hero.hero_id, matched_dir, spine_ready_dir)
        else:
            result.status = "failed"
            result.manual_gif_ready_result = {
                "status": "failed",
                "folder": "spine_ready",
                "files": [],
                "errors": result.errors.copy()
            }
            
        return result

    def _try_export_attempt(self, bundle_file: Path, raw_export_dir: Path, attempt_name: str, export_type: str, include_types: bool, attempts_for_bundle: list, result: HeroRunResult) -> bool:
        attempt_dir = raw_export_dir / bundle_file.stem / attempt_name
        attempt_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[AssetStudio] Attempt {attempt_name} for {bundle_file.name}")
        
        extracted, cmd_str, stdout, stderr = self.asset_studio.extract_bundle(
            bundle_path=bundle_file,
            output_dir=attempt_dir,
            timeout_sec=self.config.timeout_minutes * 60,
            export_type=export_type,
            include_types=include_types
        )
        
        logger.info(f"[AssetStudio] Command: {cmd_str}")
        
        output_lower = (stdout + "\n" + stderr).lower()
        nothing_exported = "nothing exported" in output_lower
        
        file_count = 0
        if attempt_dir.exists():
            file_count = sum(1 for _ in attempt_dir.rglob('*') if _.is_file())
            
        is_success = extracted and file_count > 0 and not nothing_exported
        
        attempt_status = "success" if is_success else "empty" if nothing_exported or file_count == 0 else "failed"
        
        if not extracted:
            logger.warning(f"[AssetStudio] Attempt {attempt_name} returned exit code non-zero.")
            logger.warning(f"Stderr:\n{stderr}")
            
        if is_success:
            logger.info(f"[AssetStudio] Attempt {attempt_name} exported {file_count} files.")
        else:
            logger.info(f"[AssetStudio] Attempt {attempt_name} exported nothing. Trying next...")

        attempt_info = {
            "bundle_name": bundle_file.name,
            "attempt": attempt_name,
            "exit_code": 0 if extracted else 1,
            "exported_file_count": file_count,
            "nothing_exported": nothing_exported or file_count == 0,
            "status": attempt_status
        }
        attempts_for_bundle.append(attempt_info)
        result.assetstudio_attempts.append(attempt_info)
        
        return is_success

    def _save_hero_summary(self, hero_dir: Path, result: HeroRunResult):
        data = {
            "hero_id": result.hero_id,
            "hero_number": result.hero_number,
            "hero_type": result.hero_type,
            "bundle_files": result.bundle_files,
            "status": result.status,
            "matched": {
                "skeleton": result.matched_skeleton,
                "atlas": result.matched_atlas,
                "texture": result.matched_texture
            },
            "manual_gif_ready": result.manual_gif_ready_result,
            "unpack": result.unpack_result,
            "spine_import": result.spine_import_result,
            "auto_gif": result.auto_gif_result,
            "gif_postprocess": getattr(result, "gif_postprocess_result", None),
            "warnings": result.warnings,
            "errors": result.errors,
            "assetstudio_attempts": result.assetstudio_attempts
        }
        hero_dir.mkdir(parents=True, exist_ok=True)
        save_json(hero_dir / "summary.json", data)

    def run_gif_postprocess(self, hero_ids: list[str]) -> dict:
        """Manually trigger GIF post-processing for specified heroes."""
        output_root = Path(self.config.output_root)
        if self.config.assetstudio_use_absolute_output_path:
            output_root = output_root.resolve()
            
        post_processor = GIFPostProcessor(self.config)
        results = []
        
        for hero_id in hero_ids:
            logger.info(f"[ManualGIFPost] Processing {hero_id}")
            gif_dir = self._find_latest_gif_dir(output_root, hero_id)
            
            if not gif_dir:
                logger.warning(f"[ManualGIFPost] No GIF directory found for {hero_id}")
                results.append({"hero_id": hero_id, "status": "not_found", "error": "GIF directory not found"})
                continue
                
            post_res = post_processor.process_dir(gif_dir, hero_id)
            results.append({"hero_id": hero_id, "status": "success", "result": post_res})
            
            # Also update summary.json if it exists
            summary_path = gif_dir.parent / "summary.json"
            if summary_path.exists():
                try:
                    import json
                    with open(summary_path, 'r', encoding='utf-8') as f:
                        summary_data = json.load(f)
                    summary_data["gif_postprocess"] = post_res
                    with open(summary_path, 'w', encoding='utf-8') as f:
                        json.dump(summary_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"[ManualGIFPost] Failed to update summary.json for {hero_id}: {e}")
                    
        return {"status": "success", "items": results}

    def force_trim_idle(self, hero_ids: list[str], trim_count: int = 5) -> dict:
        """Diagnostic: Force trim the idle.gif leading frames."""
        output_root = Path(self.config.output_root)
        if self.config.assetstudio_use_absolute_output_path:
            output_root = output_root.resolve()
            
        post_processor = GIFPostProcessor(self.config)
        results = []
        
        for hero_id in hero_ids:
            logger.info(f"[ForceIdleTrim] Processing {hero_id} (trim: {trim_count})")
            
            # 1. Try persistent heroes folder
            gif_dir = output_root / "heroes" / hero_id / "gif"
            
            # 2. If not found, try the latest run folder
            if not gif_dir.exists():
                logger.info(f"[ForceIdleTrim] Persistent gif dir not found, searching in runs...")
                runs_dir = output_root / "runs"
                if runs_dir.exists():
                    # Get all run folders sorted by name descending (latest first)
                    run_folders = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
                    for run in run_folders:
                        candidate = run / "heroes" / hero_id / "gif"
                        if candidate.exists():
                            gif_dir = candidate
                            logger.info(f"[ForceIdleTrim] Found gif dir in run: {run.name}")
                            break
            
            if not gif_dir.exists():
                logger.warning(f"[ForceIdleTrim] GIF directory not found for {hero_id} in base heroes or runs folders.")
                results.append({"hero_id": hero_id, "status": "not_found"})
                continue
                
            # Find all GIFs containing "idle"
            matching_gifs = []
            for f in gif_dir.glob("*.gif"):
                fname = f.name.lower()
                if "idle" in fname and not fname.endswith(".original.gif"):
                    matching_gifs.append(f)
                    
            if not matching_gifs:
                logger.warning(f"[ForceIdleTrim] No idle-related GIFs found for {hero_id} in {gif_dir}")
                # List files in dir for debugging
                files = [f.name for f in gif_dir.glob("*")]
                logger.info(f"[ForceIdleTrim] Files in dir: {', '.join(files)}")
                results.append({"hero_id": hero_id, "status": "no_idle_gif"})
                continue
                
            logger.info(f"[ForceIdleTrim] Found {len(matching_gifs)} idle-related GIFs: {[f.name for f in matching_gifs]}")
            
            hero_success = True
            hero_post_results = []
            for gif_path in matching_gifs:
                # Call trim directly
                post_res = post_processor.trim_leading_bad_frames(gif_path, hero_id, force_trim=trim_count)
                hero_post_results.append(post_res)
                if post_res.get("status") not in ["success", "preserved", "preserved_with_leading_frame_warning"]:
                    hero_success = False
            
            results.append({
                "hero_id": hero_id, 
                "status": "success" if hero_success else "partial_success", 
                "result": hero_post_results[0] if hero_post_results else None, # for backward compat
                "all_results": hero_post_results
            })
            
            # Update summary.json if it exists
            summary_path = output_root / "heroes" / hero_id / "summary.json"
            if summary_path.exists():
                try:
                    import json
                    with open(summary_path, 'r', encoding='utf-8') as f:
                        summary_data = json.load(f)
                    if "gif_postprocess" not in summary_data:
                        summary_data["gif_postprocess"] = {"status": "success", "items": [], "errors": []}
                    
                    for post_res in hero_post_results:
                        found = False
                        for item in summary_data["gif_postprocess"].get("items", []):
                            if item.get("file") == post_res.get("file"):
                                item.update(post_res)
                                found = True
                                break
                        if not found:
                            summary_data["gif_postprocess"]["items"].append(post_res)
                        
                    with open(summary_path, 'w', encoding='utf-8') as f:
                        json.dump(summary_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"[ForceIdleTrim] Failed to update summary.json for {hero_id}: {e}")
                    
        return {"status": "success", "items": results}

    def _find_latest_gif_dir(self, output_root: Path, hero_id: str) -> Path | None:
        from src.services.library_service import library_root_for
        saved = library_root_for(self.config) / "heroes" / hero_id / "gif"
        if saved.exists() and any(saved.rglob("*.gif")):
            return saved
        direct = output_root / "heroes" / hero_id / "gif"
        if direct.exists() and any(direct.glob("*.gif")):
            return direct

        runs_dir = output_root / "runs"
        if not runs_dir.exists():
            return None

        runs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        for run in runs:
            candidate = run / "heroes" / hero_id / "gif"
            if candidate.exists() and any(candidate.glob("*.gif")):
                return candidate
        return None

    def cancel(self):
        self.is_cancelled = True
        if self.asset_studio:
            self.asset_studio.cancel()
        if self.spine_service:
            self.spine_service.cancel()
        if self.spine_exporter_service:
            self.spine_exporter_service.cancel()

Orchestrator = OrchestratorWorker
