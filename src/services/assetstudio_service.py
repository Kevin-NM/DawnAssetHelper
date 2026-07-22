from pathlib import Path
from src.services.process_runner import ProcessRunner
from src.services.logger_service import logger
from src.app_config import AppConfig

class AssetStudioService:
    def __init__(self, cli_path: str, config: AppConfig):
        self.cli_path = Path(cli_path)
        self.is_dll = self.cli_path.suffix.lower() == '.dll'
        self.config = config
        self.process_runner = None

    def validate(self) -> bool:
        return self.cli_path.exists() and self.cli_path.is_file()

    def _get_base_cmd(self) -> list[str]:
        if self.is_dll:
            return ["dotnet", str(self.cli_path)]
        return [str(self.cli_path)]

    def detect_profile(self) -> str:
        if self.config.assetstudio_cli_profile != "Auto":
            return self.config.assetstudio_cli_profile

        cmd = self._get_base_cmd() + ["--help"]
        runner = ProcessRunner(cmd)
        exit_code, stdout, stderr = runner.run(timeout=10)
        output = stdout + "\n" + stderr

        if "--game" in output and "--types" in output and "--group_assets" in output:
            return "NewCLI"
        if "-o" in output and "-t" in output and "--log-output" in output:
            return "LegacyModCLI"

        return "Unknown"

    def get_command_args(self, bundle_path: str, output_dir: str, profile: str, export_type: str | None = None, include_types: bool = True) -> list[str]:
        cmd = self._get_base_cmd()
        
        target_export_type = export_type if export_type else self.config.assetstudio_export_type
        
        if profile == "NewCLI":
            cmd.extend([
                bundle_path,
                output_dir,
                "--game", self.config.assetstudio_game
            ])
            if include_types:
                cmd.extend(["--types", ",".join(self.config.assetstudio_types)])
            cmd.extend([
                "--group_assets", self.config.assetstudio_group_assets,
                "--export_type", target_export_type
            ])
        else: # Default fallback to LegacyModCLI
            cmd.extend([
                bundle_path,
                "-o", output_dir
            ])
            if include_types:
                cmd.extend(["-t", ",".join(self.config.assetstudio_export_types)])
            cmd.extend(["--log-output", "both"])
        return cmd

    def test_cli(self) -> tuple[bool, str]:
        if not self.validate():
            return False, "AssetStudio CLI path is invalid or does not exist."
        
        cmd_version = self._get_base_cmd() + ["--version"]
        runner = ProcessRunner(cmd_version)
        exit_code, stdout, stderr = runner.run(timeout=10)
        
        output = f"Running --version (exit code {exit_code}):\n{stdout}\n{stderr}\n"

        cmd_help = self._get_base_cmd() + ["--help"]
        runner = ProcessRunner(cmd_help)
        exit_code, stdout, stderr = runner.run(timeout=10)
        
        profile = self.detect_profile()
        output += f"\nDetected Profile: {profile}\n"

        if profile == "Unknown":
            output += "Cannot auto detect AssetStudio CLI profile. Please select NewCLI or LegacyModCLI manually.\n"
        else:
            dummy_cmd = self.get_command_args("<input_path>", "<output_path>", profile)
            output += f"Command format: {' '.join(dummy_cmd)}\n"

        return True, output

    def extract_bundle(self, bundle_path: Path, output_dir: Path, timeout_sec: int, export_type: str | None = None, include_types: bool = True) -> tuple[bool, str, str, str]:
        profile = self.detect_profile()
        if profile == "Unknown":
            msg = "Cannot auto detect AssetStudio CLI profile. Please select NewCLI or LegacyModCLI manually."
            logger.error(msg)
            return False, msg, "", ""

        args = self.get_command_args(str(bundle_path), str(output_dir), profile, export_type, include_types)
        cmd_str = " ".join(args)
        
        self.process_runner = ProcessRunner(args)
        
        exit_code, stdout, stderr = self.process_runner.run(
            timeout=timeout_sec
        )
        
        return exit_code == 0, cmd_str, stdout, stderr
        
    def cancel(self):
        if self.process_runner:
            self.process_runner.cancel()
