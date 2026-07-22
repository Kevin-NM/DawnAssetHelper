import shutil
import os
from pathlib import Path
from src.services.process_runner import ProcessRunner
from src.services.logger_service import logger


class SpineExporterService:
    def __init__(self, cli_path: str):
        self.cli_path = self._resolve_cli_path(cli_path)
        self.process_runner = None

    def _resolve_cli_path(self, cli_path: str) -> Path:
        p = Path(cli_path)

        if p.exists() and p.is_file():
            if p.suffix.lower() == '.ps1':
                cmd_path = p.with_suffix('.cmd')
                if cmd_path.exists():
                    logger.info(f"[SpineExporter] Switching from .ps1 to .cmd: {cmd_path}")
                    return cmd_path
                js_path = self._find_js_entry(p)
                if js_path:
                    logger.info(f"[SpineExporter] Will use node to run: {js_path}")
                    return p
            return p

        npm_global = Path(os.environ.get("APPDATA", "")) / "npm"
        for candidate in [
            npm_global / "spine-export-cli.cmd",
            npm_global / "spine-export-cli",
        ]:
            if candidate.exists():
                logger.info(f"[SpineExporter] Auto-detected CLI at: {candidate}")
                return candidate

        return p

    def _find_js_entry(self, ps1_path: Path) -> Path | None:
        npm_modules = ps1_path.parent / "node_modules" / "spine-exporter"
        js_entry = npm_modules / "dist" / "cli" / "index.js"
        if js_entry.exists():
            return js_entry
        return None

    def _build_cmd(self, args: list[str]) -> list[str]:
        cli = self.cli_path
        if cli.suffix.lower() == '.ps1':
            js_entry = self._find_js_entry(cli)
            if js_entry:
                return ["node", str(js_entry)] + args
        return [str(cli)] + args

    def validate(self) -> bool:
        if not self.cli_path.exists() or not self.cli_path.is_file():
            logger.error(f"[SpineExporter] CLI path does not exist: {self.cli_path}")
            return False

        cmd = self._build_cmd(["--help"])
        runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = runner.run(timeout=15)

        if exit_code != 0:
            logger.error(f"[SpineExporter] Validation failed. Exit code: {exit_code}")
            logger.error(f"[SpineExporter] Stdout: {stdout}\nStderr: {stderr}")
            return False

        logger.info(f"[SpineExporter] CLI validated: {self.cli_path}")
        return True

    def export_gif(
        self,
        hero_id: str,
        matched_dir: Path,
        gif_output_dir: Path,
        fps: int = 30,
        scale: float = 1.0,
        pma: bool = False,
        selected_animations: list[str] = None,
        timeout: int = 600,
    ) -> tuple[str, int, list[str], list[str], list[str], str, str, int]:
        if not matched_dir.exists():
            return "failed_input_missing", 0, [f"Matched directory not found: {matched_dir}"], [], [], "", "", -1

        if not gif_output_dir.exists():
            gif_output_dir.mkdir(parents=True, exist_ok=True)

        variant_dirs = sorted([
            d for d in matched_dir.iterdir()
            if d.is_dir() and d.name.startswith(f"{hero_id}_")
        ])

        if variant_dirs:
            all_files, all_stdout, all_stderr, errors, warnings = [], [], [], [], []
            overall_status = "success"

            for vdir in variant_dirs:
                vid = vdir.name
                status, count, errs, warns, files, stdout, stderr, code = self._export_from_dir(
                    vdir, gif_output_dir / vid, fps, scale, pma, selected_animations, timeout,
                )
                errors.extend(errs)
                warnings.extend(warns)
                all_files.extend(files)
                all_stdout.append(stdout)
                all_stderr.append(stderr)
                if status not in ["success", "success_with_warnings"]:
                    overall_status = status
            return overall_status, len(all_files), errors, warnings, all_files, "\n".join(all_stdout), "\n".join(all_stderr), 0

        return self._export_from_dir(matched_dir, gif_output_dir, fps, scale, pma, selected_animations, timeout)

    def _export_from_dir(
        self,
        input_dir: Path,
        gif_output_dir: Path,
        fps: int = 30,
        scale: float = 1.0,
        pma: bool = False,
        selected_animations: list[str] = None,
        timeout: int = 600,
    ) -> tuple[str, int, list[str], list[str], list[str], str, str, int]:
        errors, warnings = [], []

        if not gif_output_dir.exists():
            gif_output_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(gif_output_dir.resolve()).replace("\\", "/") + "/{animationName}"
        args = ["--export-type", "gif", "--fps", str(fps), "--scale", str(scale), "-o", output_template]
        if pma:
            args.append("--pma")
        if selected_animations:
            args.append("--selected-animation")
            args.extend(selected_animations)
        args.append(str(input_dir.resolve()))

        cmd = self._build_cmd(args)
        logger.info(f"[SpineExporter] Exporting GIF from {input_dir.name}")

        self.process_runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = self.process_runner.run(
            timeout=timeout,
            stdout_callback=lambda line: logger.info(f"[SpineExporter] {line}"),
            stderr_callback=lambda line: logger.warning(f"[SpineExporter] {line}"),
        )

        gif_files_paths = list(gif_output_dir.rglob("*.gif"))
        gif_count = len(gif_files_paths)
        gif_files = [f.name for f in gif_files_paths]

        logger.info(f"[SpineExporter] exit code: {exit_code}, gif count: {gif_count}")
        if gif_files:
            logger.info(f"[SpineExporter] gif files: {', '.join(gif_files)}")

        status = "success"
        if exit_code != 0:
            status = "success_with_warnings" if gif_count > 0 else "failed_spine_exporter"
            if gif_count == 0:
                errors.append(f"spine-export-cli failed with exit code {exit_code}.")
        elif gif_count == 0:
            status = "failed_no_gif_generated"
            errors.append("spine-export-cli completed but produced no GIF files.")

        return status, gif_count, errors, warnings, gif_files, stdout, stderr, exit_code
