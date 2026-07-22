import json
import shutil
import subprocess
from pathlib import Path
from src.services.logger_service import logger


SPINE_PRO_DEFAULTS = {
    "colors": 238,
    "colorDither": 50,
    "quality": 8,
    "alphaThresh": 8,
    "alphaDither": 40,
    "fps": 20,
    "scale": 50,
    "loop": True,
}


def _spine_preset_to_ffmpeg(sp: dict) -> dict:
    colors = sp.get("colors", 238)
    color_dither = sp.get("colorDither", 50)
    alpha_thresh = sp.get("alphaThresh", 8)
    loop = sp.get("loop", True)

    bayer_scale = max(0, min(5, round(5 * (100 - color_dither) / 100)))

    return {
        "palettegen": {
            "max_colors": colors,
            "reserve_transparent": True,
            "transparency_color": "0xFFFFFF",
            "stats_mode": "diff",
        },
        "paletteuse": {
            "dither": "bayer",
            "bayer_scale": bayer_scale,
            "diff_mode": "rectangle",
            "alpha_threshold": alpha_thresh,
        },
        "gif_output": {
            "loop": 0 if loop else 1,
        },
    }


class GifReencoder:
    def __init__(self, inline_preset: dict | None = None, preset_path: str | None = None):
        if inline_preset:
            self.preset = _spine_preset_to_ffmpeg(inline_preset)
        elif preset_path:
            self.preset = self._load_file_preset(preset_path)
        else:
            self.preset = _spine_preset_to_ffmpeg(SPINE_PRO_DEFAULTS)

    def _load_file_preset(self, preset_path: str) -> dict:
        p = Path(preset_path)
        if not p.exists():
            logger.warning(f"[GifReencoder] Preset not found: {p}, using Spine Pro defaults")
            return _spine_preset_to_ffmpeg(SPINE_PRO_DEFAULTS)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = _spine_preset_to_ffmpeg(SPINE_PRO_DEFAULTS)
            for k, v in data.items():
                if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                    base[k] = {**base[k], **v}
                else:
                    base[k] = v
            return base
        except Exception as e:
            logger.warning(f"[GifReencoder] Failed to load preset: {e}, using defaults")
            return _spine_preset_to_ffmpeg(SPINE_PRO_DEFAULTS)

    def reencode_dir(self, gif_dir: Path, hero_id: str) -> dict:
        if not gif_dir.exists():
            return {"status": "failed", "errors": [f"GIF directory not found: {gif_dir}"]}

        gif_files = sorted(
            f for f in gif_dir.glob("*.gif")
            if not f.name.endswith(".original.gif")
            and not f.name.endswith(".prepost.gif")
            and not f.name.endswith(".spinepro.gif")
        )

        results = []
        for gif_path in gif_files:
            res = self._reencode_one(gif_path)
            results.append(res)

        errors = [r["error"] for r in results if r.get("status") == "failed"]
        return {
            "status": "success" if not errors else "partial",
            "items": results,
            "errors": errors,
        }

    def _reencode_one(self, gif_path: Path) -> dict:
        res = {"file": gif_path.name, "status": "skipped", "error": ""}
        backup_path = gif_path.with_suffix(".original.gif")
        tmp_path = gif_path.with_suffix(".spinepro.gif")

        try:
            if not backup_path.exists():
                shutil.copy2(gif_path, backup_path)

            cmd = self._build_ffmpeg_cmd(backup_path, tmp_path)
            logger.info(f"[GifReencoder] Re-encoding {gif_path.name}")

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if proc.returncode != 0:
                logger.error(f"[GifReencoder] ffmpeg failed for {gif_path.name}: {proc.stderr[-300:]}")
                res["status"] = "failed"
                res["error"] = f"ffmpeg exit {proc.returncode}"
                if tmp_path.exists():
                    tmp_path.unlink()
                return res

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                res["status"] = "failed"
                res["error"] = "ffmpeg produced empty output"
                return res

            shutil.move(str(tmp_path), str(gif_path))
            res["status"] = "success"
            logger.info(f"[GifReencoder] Done: {gif_path.name}")

        except subprocess.TimeoutExpired:
            logger.error(f"[GifReencoder] Timeout re-encoding {gif_path.name}")
            res["status"] = "failed"
            res["error"] = "timeout"
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception as e:
            logger.error(f"[GifReencoder] Error re-encoding {gif_path.name}: {e}")
            res["status"] = "failed"
            res["error"] = str(e)
            if tmp_path.exists():
                tmp_path.unlink()

        return res

    def _build_ffmpeg_cmd(self, input_path: Path, output_path: Path) -> list[str]:
        pg = self.preset.get("palettegen", {})
        pu = self.preset.get("paletteuse", {})
        go = self.preset.get("gif_output", {})

        palette_filter = (
            f"palettegen=max_colors={pg.get('max_colors', 238)}"
            f":reserve_transparent={'on' if pg.get('reserve_transparent', True) else 'off'}"
            f":transparency_color={pg.get('transparency_color', '0xFFFFFF')}"
            f":stats_mode={pg.get('stats_mode', 'diff')}"
        )
        use_filter = (
            f"paletteuse=dither={pu.get('dither', 'bayer')}"
            f":bayer_scale={pu.get('bayer_scale', 3)}"
            f":diff_mode={pu.get('diff_mode', 'rectangle')}"
            f":alpha_threshold={pu.get('alpha_threshold', 8)}"
        )

        complex_filter = (
            f"[0:v]split[a][b];"
            f"[a]{palette_filter}[p];"
            f"[b][p]{use_filter}[out]"
        )

        return [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-filter_complex", complex_filter,
            "-map", "[out]",
            "-loop", str(go.get("loop", 0)),
            str(output_path),
        ]
