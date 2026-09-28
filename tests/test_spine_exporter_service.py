import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.spine_exporter_service import SpineExporterService


class SpineExporterCommandTests(unittest.TestCase):
    def test_adjacent_npm_package_uses_loader_even_with_cmd_shim(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cli = root / "spine-export-cli.cmd"
            cli.touch()
            entry = root / "node_modules/spine-exporter/dist/cli/index.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            (root / "node.exe").touch()
            cmd = SpineExporterService(str(cli))._build_cmd(["--help"])
            self.assertEqual(cmd[0], str(root / "node.exe"))
            self.assertEqual(cmd[1], "--experimental-loader")
            self.assertTrue(cmd[2].endswith("/patches/spine-exporter-loader.mjs"))
            self.assertEqual(cmd[3:], [str(entry), "--help"])

    def test_nvm_shim_resolves_package_from_npm_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cli = root / "shim/spine-export-cli.exe"
            cli.parent.mkdir()
            cli.touch()
            (cli.parent / "npm.exe").touch()
            entry = root / "global/spine-exporter/dist/cli/index.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            with patch("src.services.spine_exporter_service.ProcessRunner") as runner:
                runner.return_value.run.return_value = (0, str(root / "global"), "")
                self.assertEqual(SpineExporterService(str(cli))._find_js_entry(cli), entry)
                self.assertEqual(runner.call_args.args[0], [str(cli.parent / "npm.exe"), "root", "-g"])

    def test_missing_package_fails_validation_instead_of_using_unsafe_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = Path(folder) / "spine-export-cli.exe"
            cli.touch()
            service = SpineExporterService(str(cli))
            with patch.object(service, "_find_js_entry", return_value=None):
                self.assertFalse(service.validate())

    def test_input_precedes_selected_animation_array(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cli = root / "spine-export-cli.exe"
            cli.touch()
            service = SpineExporterService(str(cli))
            with patch.object(service, "_build_cmd", side_effect=lambda args: args), \
                 patch("src.services.spine_exporter_service.ProcessRunner") as runner:
                runner.return_value.run.return_value = (0, "", "")
                service._export_from_dir(root, root / "gif", selected_animations=["idle"])
                args = runner.call_args.args[0]
                self.assertEqual(args[0], str(root.resolve()))
                self.assertEqual(args[-2:], ["--selected-animation", "idle"])

    def test_unsupported_loader_does_not_report_stale_gif_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cli = root / "spine-export-cli.exe"
            cli.touch()
            gif = root / "gif"
            gif.mkdir()
            (gif / "idle.gif").write_bytes(b"old output")
            service = SpineExporterService(str(cli))
            with patch.object(service, "_build_cmd", return_value=["node"]), \
                 patch("src.services.spine_exporter_service.ProcessRunner") as runner:
                runner.return_value.run.return_value = (1, "", "Error: Unsupported spine-exporter renderer")
                result = service._export_from_dir(root, gif)
                self.assertEqual(result[0], "failed_spine_exporter")
                self.assertEqual(result[1], 0)
                self.assertIn("earlier export", result[2][0])


if __name__ == "__main__":
    unittest.main()
