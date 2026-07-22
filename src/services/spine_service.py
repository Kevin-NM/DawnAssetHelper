import os
import shutil
from pathlib import Path
from src.services.process_runner import ProcessRunner
from src.services.logger_service import logger
from src.app_config import AppConfig
from src.utils.spine_utils import parse_spine_atlas

class SpineService:
    def __init__(self, cli_path: str, config: AppConfig = None):
        self.cli_path = Path(cli_path)
        self.config = config
        self.process_runner = None

    def validate(self) -> bool:
        if not self.cli_path.exists() or not self.cli_path.is_file():
            return False
            
        cmd = [str(self.cli_path), "--version"]
        runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = runner.run(timeout=10)
        
        if exit_code != 0:
            cmd = [str(self.cli_path), "-h"]
            runner = ProcessRunner(cmd)
            exit_code, stdout, stderr = runner.run(timeout=10)
            
        if exit_code != 0:
            logger.error(f"Spine CLI validation failed. Exit code: {exit_code}")
            logger.error(f"Stdout: {stdout}\nStderr: {stderr}")
            return False
            
        return True

    def unpack_atlas(self, hero_id: str, matched_dir: Path, unpacked_dir: Path) -> tuple[bool, int, list[str]]:
        errors = []
        
        if not self.cli_path.exists() or not self.cli_path.is_file():
            errors.append(f"Spine CLI path is invalid: {self.cli_path}")
            return False, 0, errors
            
        if not matched_dir.exists():
            errors.append(f"Matched directory does not exist: {matched_dir}")
            return False, 0, errors
            
        atlas_file = matched_dir / f"{hero_id}.atlas"
        
        if not atlas_file.exists():
            errors.append(f"Missing atlas file: {atlas_file}")
            
        if errors:
            return False, 0, errors
            
        if unpacked_dir.exists():
            for item in unpacked_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete {item}: {e}")
        else:
            unpacked_dir.mkdir(parents=True, exist_ok=True)
            
        cmd = [
            str(self.cli_path),
            "-i", str(matched_dir),
            "-o", str(unpacked_dir),
            "-c", str(atlas_file)
        ]
        
        cmd_str = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
        
        rel_matched = "matched"
        rel_atlas = f"matched/{atlas_file.name}"
        rel_unpacked = "unpacked"
        
        logger.info(f"[Spine] Unpacking atlas for {hero_id}")
        logger.info(f"[Spine] input folder: heroes/{hero_id}/{rel_matched}")
        logger.info(f"[Spine] atlas file: heroes/{hero_id}/{rel_atlas}")
        logger.info(f"[Spine] output folder: heroes/{hero_id}/{rel_unpacked}")
        logger.info(f"[Spine] command: {cmd_str}")
        
        self.process_runner = ProcessRunner(cmd)
        
        def on_stdout(line):
            logger.info(f"[Spine] {line}")
            
        def on_stderr(line):
            logger.warning(f"[Spine] {line}")
            
        exit_code, stdout, stderr = self.process_runner.run(
            timeout=60,
            stdout_callback=on_stdout,
            stderr_callback=on_stderr
        )
        
        if exit_code != 0:
            logger.error(f"[Spine] Unpack failed for {hero_id}.")
            logger.error(f"[Spine] stderr:\n{stderr}")
            errors.append(f"Spine process failed with exit code {exit_code}.")
            errors.append(f"Stderr: {stderr}")
            return False, 0, errors
            
        png_count = sum(1 for _ in unpacked_dir.rglob('*.png'))
        
        if png_count > 0:
            logger.info(f"[Spine] Unpack success for {hero_id}. PNG files: {png_count}")
            
            # Perform validation
            valid_res = self.validate_unpacked_images(hero_id, matched_dir, unpacked_dir)
            
            return True, png_count, []
        else:
            msg = "Spine unpack completed but no PNG files were generated."
            logger.error(f"[Spine] Unpack failed for {hero_id}. {msg}")
            errors.append(msg)
            return False, 0, errors

    def validate_unpacked_images(self, hero_id: str, matched_dir: Path, unpacked_dir: Path) -> dict:
        logger.info(f"[Validation] Starting unpacked image validation for {hero_id}")
        atlas_file = matched_dir / f"{hero_id}.atlas"
        if not atlas_file.exists():
            return {"status": "error", "error": "Atlas not found"}
            
        atlas_data = parse_spine_atlas(atlas_file)
        regions = atlas_data["regions"]
        
        from PIL import Image
        
        diag = {
            "total_regions": len(regions),
            "checked_png": 0,
            "dimension_mismatch_count": 0,
            "missing_png_count": 0,
            "samples": [],
            "status": "success"
        }
        
        for region in regions:
            png_path = unpacked_dir / f"{region.name}.png"
            if not png_path.exists():
                diag["missing_png_count"] += 1
                if len(diag["samples"]) < 20:
                    diag["samples"].append({"region": region.name, "error": "missing"})
                continue
                
            diag["checked_png"] += 1
            try:
                with Image.open(png_path) as img:
                    # Check if size matches atlas orig (or size if no orig)
                    # Note: Spine unpack might trim if whitespace stripping is enabled in atlas
                    # But if we are validating against the atlas definition, the PNG should match 'size'
                    # unless it was rotated or something.
                    # Actually, unpacked PNGs should be 'orig' size if we want them to match what Spine expects in its mesh.
                    expected_w, expected_h = region.orig
                    
                    if img.size != (expected_w, expected_h):
                        diag["dimension_mismatch_count"] += 1
                        if len(diag["samples"]) < 20:
                            diag["samples"].append({
                                "region": region.name, 
                                "atlas_orig": list(region.orig), 
                                "png_size": list(img.size),
                                "error": "dimension_mismatch"
                            })
            except Exception as e:
                diag["samples"].append({"region": region.name, "error": f"read_error: {e}"})
                
        if diag["dimension_mismatch_count"] > 0 or diag["missing_png_count"] > 0:
            diag["status"] = "failed_dimension_mismatch"
            logger.warning(f"[Validation] Mismatch detected: {diag['dimension_mismatch_count']} dimension mismatches, {diag['missing_png_count']} missing.")
            
        # Save diagnostics
        diag_dir = matched_dir.parent / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        import json
        with open(diag_dir / "unpack_validation.json", 'w', encoding='utf-8') as f:
            json.dump(diag, f, indent=2, ensure_ascii=False)
            
        return diag

    def cancel(self):
        if self.process_runner:
            self.process_runner.cancel()

    def import_project(self, hero_id: str, input_path: Path, spine_project_dir: Path) -> tuple[bool, Path|None, list[str], str, str]:
        errors = []
        if not self.cli_path.exists() or not self.cli_path.is_file():
            errors.append(f"Spine CLI path is invalid: {self.cli_path}")
            return False, None, errors, "", ""
            
        if not input_path.exists():
            errors.append(f"Spine import input does not exist: {input_path}")
            return False, None, errors, "", ""
            
        if spine_project_dir.exists():
            for item in spine_project_dir.iterdir():
                if item.is_file():
                    try: item.unlink()
                    except Exception as e: logger.warning(f"Failed to delete {item}: {e}")
                elif item.is_dir():
                    try: shutil.rmtree(item)
                    except Exception as e: logger.warning(f"Failed to delete {item}: {e}")
        else:
            spine_project_dir.mkdir(parents=True, exist_ok=True)
            
        project_file = spine_project_dir / f"{hero_id}.spine"
        
        cmd = [
            str(self.cli_path),
            "-i", str(input_path),
            "-o", str(project_file),
            "-r", hero_id
        ]
        
        cmd_str = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
        logger.info(f"[AutoGIF] Step 1/2 Import Spine project")
        logger.info(f"[Spine] Importing project from render workspace:\ninput: {input_path}\noutput: {project_file}")
        logger.info(f"[AutoGIF] command: {cmd_str}")
        
        self.process_runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = self.process_runner.run(
            timeout=120,
            stdout_callback=lambda line: logger.info(f"[Spine] {line}"),
            stderr_callback=lambda line: logger.warning(f"[Spine] {line}")
        )
        
        logger.info(f"[AutoGIF] import exit code: {exit_code}")
        logger.info(f"[AutoGIF] project exists: {str(project_file.exists()).lower()}")
        logger.info(f"[AutoGIF] project file: {project_file}")
        
        if exit_code != 0:
            logger.error(f"[Spine] Import failed for {hero_id}.")
            errors.append(f"Spine import process failed with exit code {exit_code}.")
            return False, None, errors, stdout, stderr
            
        if not project_file.exists():
            msg = "Spine import completed but project file was not generated."
            logger.error(f"[Spine] Import failed for {hero_id}. {msg}")
            errors.append(msg)
            return False, None, errors, stdout, stderr
            
        return True, project_file, [], stdout, stderr

    def inspect_spine_project(self, project_file: Path):
        try:
            import json
            with open(project_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"[SpineProject] project file is JSON: true")
            keys = list(data.keys())
            logger.info(f"[SpineProject] top-level keys: {', '.join(keys)}")
            possible_fields = []
            if 'skeleton' in data and isinstance(data['skeleton'], dict):
                skel = data['skeleton']
                for k in skel.keys():
                    if 'image' in k.lower() or 'path' in k.lower():
                        possible_fields.append(f"skeleton.{k}={skel[k]}")
            for k in keys:
                if 'image' in k.lower() or 'path' in k.lower():
                    possible_fields.append(f"{k}={data[k]}")
            logger.info(f"[SpineProject] possible image path fields: {', '.join(possible_fields) if possible_fields else 'none'}")
            return True, data
        except json.JSONDecodeError:
            logger.error("[SpineProject] Project file is not plain JSON. Cannot patch images path directly.")
            return False, None
        except Exception as e:
            logger.error(f"[SpineProject] Failed to read project file: {e}")
            return False, None

    def prepare_spine_project_local_images(self, hero_id: str, image_source_dir: Path, spine_project_dir: Path, unpacked_dir: Path = None) -> tuple[int, list[str]]:
        try:
            import shutil
            if not image_source_dir.exists():
                logger.error("[Images] ERROR: image source dir does not exist.")
                return 0, ["image source dir does not exist"]
            
            png_files = []
            png_files.extend((png.name, png) for png in image_source_dir.glob("*.png"))

            images_source_dir = image_source_dir / "images"
            if images_source_dir.exists():
                png_files.extend(
                    (f"images/{png.relative_to(images_source_dir).as_posix()}", png)
                    for png in images_source_dir.rglob("*.png")
                )

            if not png_files:
                logger.error("[Images] ERROR: image source dir has no PNG files.")
                return 0, ["image source dir has no PNG files"]
                
            if not spine_project_dir.exists():
                spine_project_dir.mkdir(parents=True, exist_ok=True)
                
            for item in spine_project_dir.iterdir():
                if item.is_file() and item.suffix.lower() == '.png':
                    try:
                        item.unlink()
                    except Exception:
                        pass
                elif item.is_dir() and item.name == "images":
                    try:
                        shutil.rmtree(item)
                    except Exception:
                        pass

            logger.info("[Images] Preparing local images for Spine project")
            logger.info("[Images] copying root atlas page and images/ region textures used during folder import")
            logger.info(f"[Images] source image dir: {image_source_dir}")
            logger.info(f"[Images] target spine_project dir: {spine_project_dir}")
            
            copied_count = 0
            sample_files = []
            copied_targets = set()
            for rel_name, png in png_files:
                rel_path = Path(rel_name)
                root_target = spine_project_dir / rel_path

                if root_target.resolve() in copied_targets:
                    continue
                root_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(png, root_target)
                copied_targets.add(root_target.resolve())
                
                copied_count += 1
                if len(sample_files) < 3:
                    sample_files.append(str(rel_path))

            atlas_file = image_source_dir / f"{hero_id}.atlas"
            if atlas_file.exists():
                shutil.copy2(atlas_file, spine_project_dir / atlas_file.name)
                    
            logger.info(f"[Images] copied png count: {copied_count}")
            if sample_files:
                logger.info(f"[Images] sample files: {', '.join(sample_files)}")
                
            return copied_count, []
        except Exception as e:
            msg = f"Failed to prepare local images: {e}"
            logger.error(f"[Images] ERROR: {msg}")
            return 0, [msg]

    def prepare_render_workspace(self, hero_id: str, matched_dir: Path, unpacked_dir: Path, render_workspace_dir: Path) -> tuple[int, int, list[str]]:
        try:
            import shutil
            if render_workspace_dir.exists():
                for item in render_workspace_dir.iterdir():
                    if item.is_file():
                        try: item.unlink()
                        except: pass
                    elif item.is_dir():
                        try: shutil.rmtree(item)
                        except: pass
            
            render_workspace_dir.mkdir(parents=True, exist_ok=True)
            images_dir = render_workspace_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            
            skel_file = matched_dir / f"{hero_id}.skel"
            atlas_file = matched_dir / f"{hero_id}.atlas"
            if not skel_file.exists():
                return 0, 0, [f"Skeleton file not found: {skel_file}"]
            if not atlas_file.exists():
                return 0, 0, [f"Atlas file not found: {atlas_file}"]
            
            shutil.copy2(skel_file, render_workspace_dir / f"{hero_id}.skel")
            shutil.copy2(atlas_file, render_workspace_dir / f"{hero_id}.atlas")
            
            logger.info(f"[RenderWorkspace] Preparing workspace for {hero_id}")
            logger.info(f"[RenderWorkspace] copied skel: matched/{hero_id}.skel -> render_workspace/{hero_id}.skel")
            logger.info(f"[RenderWorkspace] copied atlas: matched/{hero_id}.atlas -> render_workspace/{hero_id}.atlas")
            
            atlas_data = parse_spine_atlas(atlas_file)
            page_names = [p.page_name for p in atlas_data.get("pages", [])]
            copied_root = 0
            copied_images = 0
            copied_sources = set()

            for page_name in page_names:
                src_png = matched_dir / page_name
                if not src_png.exists() and not page_name.lower().endswith(".png"):
                    src_png = matched_dir / f"{page_name}.png"
                if not src_png.exists():
                    return copied_root, copied_images, [f"Atlas page texture not found: {page_name}"]

                target_name = page_name if page_name.lower().endswith(".png") else f"{page_name}.png"
                target_root = render_workspace_dir / target_name
                target_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_png, target_root)
                copied_sources.add(src_png.resolve())
                copied_root += 1
                logger.info(f"[RenderWorkspace] copied atlas page texture: matched/{src_png.name} -> render_workspace/{target_name}")

            unpacked_count = 0
            if unpacked_dir and unpacked_dir.exists():
                for png in unpacked_dir.rglob("*.png"):
                    rel_path = png.relative_to(unpacked_dir)
                    target_imgs = images_dir / rel_path
                    target_imgs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(png, target_imgs)
                    unpacked_count += 1
                copied_images += unpacked_count
                logger.info(f"[RenderWorkspace] copied unpacked region images to images/: {unpacked_count}")

            for png in matched_dir.glob("*.png"):
                if png.resolve() in copied_sources:
                    continue
                shutil.copy2(png, render_workspace_dir / png.name)
                copied_root += 1
                logger.info(f"[RenderWorkspace] copied extra matched texture: matched/{png.name} -> render_workspace/{png.name}")
                    
            logger.info(f"[RenderWorkspace] copied atlas/page textures to root: {copied_root}")
            logger.info(f"[RenderWorkspace] copied images folder textures: {copied_images}")
            logger.info(f"[RenderWorkspace] copied unpacked region images to images/: {unpacked_count}")
            
            return copied_root, copied_images, []
        except Exception as e:
            msg = f"Failed to prepare render workspace: {e}"
            logger.error(msg)
            return 0, 0, [msg]

    def export_gif(self, hero_id: str, project_file: Path, gif_output_dir: Path, export_settings_json: Path, hero_dir: Path) -> tuple[str, int, list[str], list[str], list[str], str, str, int]:
        errors = []
        warnings = []
        if not project_file.exists():
            errors.append(f"Project file does not exist: {project_file}")
            return "failed_spine_exit_code", 0, errors, warnings, [], "", "", -1
        if not export_settings_json.exists():
            errors.append(f"Export settings JSON does not exist: {export_settings_json}")
            return "failed_missing_preset", 0, errors, warnings, [], "", "", -1
            
        if not gif_output_dir.exists():
            gif_output_dir.mkdir(parents=True, exist_ok=True)
            
        project_file_abs = project_file.resolve()
        gif_output_dir_abs = gif_output_dir.resolve()
        export_settings_json_abs = export_settings_json.resolve()
        cli_path_abs = self.cli_path.resolve()
        
        cmd = [
            str(cli_path_abs),
            "-i", str(project_file_abs),
            "-o", str(gif_output_dir_abs),
            "-e", str(export_settings_json_abs)
        ]
        
        cmd_str = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
        logger.info(f"[AutoGIF] Step 2/2 Export GIF")
        logger.info(f"[AutoGIF] project file: {project_file_abs}")
        logger.info(f"[AutoGIF] gif output dir: {gif_output_dir_abs}")
        logger.info(f"[AutoGIF] preset file: {export_settings_json_abs}")
        logger.info(f"[AutoGIF] command: {cmd_str}")
        
        self.process_runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = self.process_runner.run(
            timeout=300,
            stdout_callback=lambda line: logger.info(f"[Spine] {line}"),
            stderr_callback=lambda line: logger.warning(f"[Spine] {line}")
        )
        
        gif_files_paths = list(gif_output_dir.rglob('*.gif'))
        gif_count = len(gif_files_paths)
        
        # Output checking
        if gif_count == 0 and hero_dir:
            # check root dir
            all_gifs = list(hero_dir.rglob('*.gif'))
            if all_gifs:
                logger.info(f"[AutoGIF] GIF files were generated outside expected gif folder. Moving them to gif folder.")
                for g in all_gifs:
                    if g.parent != gif_output_dir:
                        try:
                            import shutil
                            shutil.move(str(g), str(gif_output_dir / g.name))
                        except Exception as e:
                            logger.error(f"Failed to move {g} to {gif_output_dir}: {e}")
                gif_files_paths = list(gif_output_dir.rglob('*.gif'))
                gif_count = len(gif_files_paths)
                
        # Write stdout/stderr to file
        try:
            (gif_output_dir / "export_stdout.txt").write_text(stdout, encoding="utf-8")
            (gif_output_dir / "export_stderr.txt").write_text(stderr, encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write stdout/stderr to files: {e}")
        
        gif_files = [f.name for f in gif_files_paths]
        
        logger.info(f"[AutoGIF] export exit code: {exit_code}")
        logger.info(f"[AutoGIF] gif count: {gif_count}")
        if gif_count > 0:
            logger.info(f"[AutoGIF] gif files: {', '.join(gif_files)}")
            
        # Parse warnings
        mesh_warning_count = stdout.count("WARNING: Mesh image file dimensions changed") + stderr.count("WARNING: Mesh image file dimensions changed")
        if mesh_warning_count > 0:
            warnings.append(f"Mesh image dimensions changed ({mesh_warning_count} warnings). Output may be visually incorrect.")
            logger.info(f"[AutoGIF] Mesh image file dimensions changed warnings: {mesh_warning_count}")
        
        status = "success"
        has_real_error = "ERROR:" in stdout or "ERROR:" in stderr or "Error reading export settings file" in stdout or "Error reading export settings file" in stderr or "Unable to convert value" in stdout or "Unable to convert value" in stderr
        
        if exit_code != 0:
            if "Error reading export settings file" in stdout or "Unable to convert value" in stdout or "Error reading export settings file" in stderr or "Unable to convert value" in stderr:
                status = "failed_invalid_preset"
                msg = "GIF export preset is incompatible with this Spine version."
                logger.error(f"[Spine] GIF export failed for {hero_id}. {msg}")
                errors.append(msg)
            elif gif_count > 0 and not has_real_error:
                if mesh_warning_count > 0:
                    status = "output_with_mesh_dimension_warnings"
                else:
                    status = "success_with_warnings"
                logger.warning(f"[AutoGIF] GIF export completed with warnings/non-zero exit code.")
            else:
                status = "failed_spine_exit_code"
                msg = f"Spine GIF export process failed with exit code {exit_code}."
                logger.error(f"[Spine] GIF export failed for {hero_id}. {msg}")
                errors.append(msg)
        elif gif_count == 0:
            status = "failed_no_gif_generated"
            msg = "Spine project was generated successfully, but GIF export produced no GIF files. This usually means the GIF export preset is invalid, incompatible, or not configured to output GIF."
            logger.error(f"[Spine] GIF export failed for {hero_id}. {msg}")
            errors.append(msg)
        elif mesh_warning_count > 0:
            status = "output_with_mesh_dimension_warnings"
            logger.warning(f"[AutoGIF] GIF export completed with mesh dimension warnings ({mesh_warning_count}).")
        elif warnings:
            status = "success_with_warnings"
            logger.warning(f"[AutoGIF] GIF export completed with warnings.")
            
        return status, gif_count, errors, warnings, mesh_warning_count, gif_files, stdout, stderr, exit_code

    def import_and_export_gif(self, hero_id: str, matched_dir: Path, spine_project_dir: Path, gif_output_dir: Path, export_settings_json: Path) -> tuple[dict, dict]:
        # Left for backward compatibility, unused by Orchestrator's AutoGIF which calls them manually
        return {}, {}
