import statistics
from pathlib import Path
from PIL import Image, ImageSequence
from src.services.logger_service import logger


class GIFPostProcessor:
    def __init__(self, config):
        self.config = config

    def process_dir(self, gif_dir: Path, hero_id: str) -> dict:
        res = {
            "status": "success",
            "enabled": self.config.gif_postprocess_enabled,
            "mode": "diagnostic_preserve_frames",
            "items": [],
            "errors": []
        }

        if not gif_dir.exists():
            res["errors"].append(f"GIF directory not found: {gif_dir}")
            res["status"] = "failed"
            return res

        gif_files = sorted(gif_dir.glob("*.gif"))
        gif_files = [f for f in gif_files if not f.name.endswith(".original.gif") and not f.name.endswith(".prepost.gif")]

        for gif_path in gif_files:
            try:
                item_res = self.trim_leading_bad_frames(gif_path, hero_id)
                res["items"].append(item_res)
            except Exception as e:
                msg = f"Failed to process {gif_path.name}: {e}"
                logger.error(f"[GIFPost] {msg}")
                res["errors"].append(msg)

        if res["errors"]:
            res["status"] = "failed" if not res["items"] else "success_with_warnings"

        return res

    def detect_leading_fadein_frames(self, frames: list, max_scan: int = 30) -> int:
        """Count leading frames affected by the Spine CLI cold-start fade-in.

        The CLI fade-in makes the character blend in from transparent, so the
        leading frames have both (a) a global alpha ramp (max alpha < 255) and
        (b) a visible-pixel count that climbs toward the animation's stable
        coverage. Detecting only the alpha ramp misses frames that GIF palette
        quantization leaves with a few opaque pixels, so we also treat leading
        frames as fade-in while their visible coverage is still ramping up to a
        stable plateau.
        """
        limit = min(len(frames), max_scan)
        if limit <= 1:
            return 0

        visible_counts = []
        max_alphas = []
        for i in range(limit):
            rgba = frames[i].convert("RGBA")
            alpha = rgba.getchannel("A")
            hist = alpha.histogram()
            visible_counts.append(rgba.size[0] * rgba.size[1] - hist[0])
            max_alphas.append(max((idx for idx, c in enumerate(hist) if c > 0), default=0))

        # Stable coverage estimated from the upper half of the scanned window,
        # which is past the cold-start fade-in.
        stable_ref = sorted(visible_counts)[len(visible_counts) // 2:]
        stable = statistics.median(stable_ref) if stable_ref else max(visible_counts)
        if stable <= 0:
            return 0

        tolerance = 0.95  # a clean frame should cover >= 95% of stable coverage
        fade_count = 0
        for i in range(limit):
            faded_alpha = max_alphas[i] < 255
            faded_coverage = visible_counts[i] < stable * tolerance
            if faded_alpha or faded_coverage:
                fade_count += 1
            else:
                break

        if fade_count >= len(frames):
            fade_count = len(frames) - 1
        return max(0, fade_count)

    def trim_leading_fadein_frames(self, gif_path: Path, hero_id: str) -> dict:
        """Remove Spine CLI cold-start fade-in frames, preserving the original.

        Confirmed root cause (Esoteric forum 13542 / spine-editor #538): the
        Spine CLI applies the editor's startup fade-in while exporting, so the
        first frames blend in from transparent. Those frames are genuinely wrong
        for a looping animation, so this produces a clean GIF with the fade-in
        frames removed. The untouched original is always kept as
        ``<name>.original.gif`` so nothing is overwritten or lost.
        """
        animation_name = self._parse_animation_name(gif_path.name, hero_id) or "unknown"
        res = {
            "file": self._safe_relative(gif_path),
            "animation": animation_name,
            "mode": "remove_cli_fadein",
            "original_frame_count": 0,
            "fadein_frames_removed": 0,
            "final_frame_count": 0,
            "duration_preserved": False,
            "repair_strategy": "remove_leading_fadein_frames",
            "original_backup": "",
            "status": "skipped",
        }

        try:
            with Image.open(gif_path) as im:
                frames = [f.convert("RGBA").copy() for f in ImageSequence.Iterator(im)]
                durations = []
                loop = im.info.get("loop", 0)
                for f in ImageSequence.Iterator(im):
                    durations.append(f.info.get("duration", 50))

            original_count = len(frames)
            res["original_frame_count"] = original_count
            res["final_frame_count"] = original_count

            if original_count <= 1:
                res["status"] = "preserved"
                logger.info(f"[GIFFade] {gif_path.name} has {original_count} frame, nothing to trim.")
                return res

            fade_count = self.detect_leading_fadein_frames(frames, max_scan=getattr(self.config, "gif_trim_max_scan_frames", 30))
            res["fadein_frames_removed"] = fade_count
            logger.info(f"[GIFFade] {gif_path.name}: detected {fade_count} leading fade-in frames (of {original_count}).")

            if fade_count <= 0:
                res["status"] = "no_fadein_detected"
                logger.info(f"[GIFFade] {gif_path.name}: no fade-in detected, GIF left as-is.")
                return res

            # Preserve the untouched original alongside the trimmed result.
            backup_path = gif_path.with_suffix(".original.gif")
            if not backup_path.exists():
                import shutil
                shutil.copy2(gif_path, backup_path)
            res["original_backup"] = self._safe_relative(backup_path)
            logger.info(f"[GIFFade] original preserved: {backup_path.name}")

            new_frames = frames[fade_count:]
            new_durations = durations[fade_count:] if len(durations) >= len(frames) else [durations[0] if durations else 50] * len(new_frames)
            res["final_frame_count"] = len(new_frames)
            res["duration_preserved"] = len(new_frames) == original_count

            new_frames[0].save(
                gif_path,
                save_all=True,
                append_images=new_frames[1:],
                duration=new_durations,
                loop=loop,
                disposal=2,
                optimize=False,
            )

            with Image.open(gif_path) as verify:
                saved = sum(1 for _ in ImageSequence.Iterator(verify))
            if saved != len(new_frames):
                res["status"] = "failed_verification"
                logger.error(f"[GIFFade] verification failed: expected {len(new_frames)} frames, got {saved}.")
            else:
                res["status"] = "fadein_removed"
                logger.info(f"[GIFFade] {gif_path.name}: removed {fade_count} fade-in frames, {saved} frames remain.")
            return res
        except Exception as e:
            res["status"] = "failed"
            res["error"] = str(e)
            logger.error(f"[GIFFade] ERROR processing {gif_path.name}: {e}")
            return res

    def remove_fadein_in_dir(self, gif_dir: Path, hero_id: str) -> dict:
        res = {"status": "success", "mode": "remove_cli_fadein", "items": [], "errors": []}
        if not gif_dir.exists():
            res["status"] = "failed"
            res["errors"].append(f"GIF directory not found: {gif_dir}")
            return res
        gif_files = sorted(
            f for f in gif_dir.glob("*.gif")
            if not f.name.endswith(".original.gif") and not f.name.endswith(".prepost.gif")
        )
        for gif_path in gif_files:
            res["items"].append(self.trim_leading_fadein_frames(gif_path, hero_id))
        return res

    def trim_leading_bad_frames(self, gif_path: Path, hero_id: str, force_trim: int = None) -> dict:
        """Inspect leading GIF frames without deleting or rewriting them.

        Older builds tried to trim the first few frames in-place. Those frames can
        be required by Spine/GIF playback even when they look visually wrong, so
        this method is intentionally non-destructive and keeps the legacy name for
        callers that already use it.
        """
        animation_name = self._parse_animation_name(gif_path.name, hero_id)
        logger.info(f"[GIFPost] Inspecting {gif_path.name} (preserve all frames)")

        if not animation_name:
            logger.error(f"[GIFPost] ERROR: Failed to parse animation name from {gif_path.name}")
            animation_name = "unknown"
        else:
            logger.info(f"[GIFPost] parsed animation: {animation_name}")

        res = {
            "file": self._safe_relative(gif_path),
            "animation": animation_name,
            "auto_detected_bad_leading_frames": 0,
            "fallback_trim_frames": 0,
            "final_trim_frames": 0,
            "trimmed_frames": 0,
            "requested_force_trim_frames": int(force_trim or 0),
            "original_frame_count": 0,
            "final_frame_count": 0,
            "backup": "",
            "verification": "preserved_without_rewrite",
            "status": "skipped"
        }

        try:
            with Image.open(gif_path) as im:
                original_frames = [frame.convert("RGBA").copy() for frame in ImageSequence.Iterator(im)]
                original_durations = []
                for frame in ImageSequence.Iterator(im):
                    original_durations.append(frame.info.get("duration", 50))
                original_loop = im.info.get("loop", 0)

            original_count = len(original_frames)
            res["original_frame_count"] = original_count

            debug_dir = gif_path.parent / "debug_frames"
            debug_dir.mkdir(parents=True, exist_ok=True)

            if original_count <= 1:
                logger.info(f"[GIFPost] {gif_path.name} has only {original_count} frame, preserving as-is.")
                res["final_frame_count"] = original_count
                res["status"] = "preserved"
                return res

            metrics = []
            for idx, frame in enumerate(original_frames[:self.config.gif_trim_max_scan_frames]):
                metric = self._frame_metric(frame)
                metric["idx"] = idx
                metrics.append(metric)
                logger.info(f"[GIFPost] frame {idx}: visible={metric['visible_pixels']} bbox={metric['bbox']} area={metric['bbox_area']} alpha_avg={metric['alpha_avg']:.2f}")

            auto_detected_count = self._detect_bad_leading_frames(metrics)
            res["auto_detected_bad_leading_frames"] = auto_detected_count
            logger.info(f"[GIFPost] auto_detected_bad_leading_frames: {auto_detected_count}")

            fallback_frames = int(getattr(self.config, "gif_trim_fallback_by_animation", {}).get(animation_name, 0))
            res["fallback_trim_frames"] = fallback_frames
            if fallback_frames:
                logger.info(f"[GIFPost] fallback_trim_frames for {animation_name}: {fallback_frames}")

            if force_trim is not None:
                final_trim_count = int(force_trim)
                logger.warning(f"[GIFPost] Forced trim was requested ({final_trim_count}), but frame deletion is disabled. GIF will be preserved as-is.")
            else:
                final_trim_count = max(auto_detected_count, fallback_frames)

            final_trim_count = max(0, min(final_trim_count, original_count - 1))
            res["final_trim_frames"] = final_trim_count
            res["trimmed_frames"] = 0
            res["final_frame_count"] = original_count

            original_frames[0].save(debug_dir / f"{gif_path.stem}_frame_000.png")
            if final_trim_count > 0:
                original_frames[final_trim_count].save(debug_dir / f"{gif_path.stem}_detected_frame_{final_trim_count:03d}.png")

            logger.info(f"[GIFPost] frame_count: {original_count}")
            logger.info(f"[GIFPost] detected/requested leading frames: {final_trim_count}")
            logger.info("[GIFPost] preservation policy: no GIF frames were deleted or rewritten.")

            res["status"] = "preserved_with_leading_frame_warning" if final_trim_count > 0 else "preserved"

            return res
        except Exception as e:
            logger.error(f"[GIFPost] ERROR processing {gif_path.name}: {e}")
            res["status"] = "failed"
            res["error"] = str(e)
            return res

    def _parse_animation_name(self, filename: str, hero_id: str) -> str | None:
        name = Path(filename).stem
        prefix = f"{hero_id}-"
        if name.startswith(prefix):
            return name[len(prefix):]
        return None

    def _frame_metric(self, frame: Image.Image) -> dict:
        alpha = frame.getchannel("A")
        bbox = alpha.getbbox()
        hist = alpha.histogram()
        visible_pixels = frame.size[0] * frame.size[1] - hist[0]
        alpha_values = []
        for i, count in enumerate(hist):
            if count:
                alpha_values.extend([i] * min(count, 1000))
        alpha_avg = statistics.mean(alpha_values) if alpha_values else 0
        bbox_area = 0
        if bbox:
            bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        return {
            "visible_pixels": visible_pixels,
            "bbox": bbox,
            "bbox_area": bbox_area,
            "alpha_avg": alpha_avg
        }

    def _detect_bad_leading_frames(self, metrics: list[dict]) -> int:
        if len(metrics) < 3:
            return 0
        visible = [m["visible_pixels"] for m in metrics]
        areas = [m["bbox_area"] for m in metrics]
        stable_visible = statistics.median(visible[min(5, len(visible)//2):]) if len(visible) > 5 else max(visible)
        stable_area = statistics.median(areas[min(5, len(areas)//2):]) if len(areas) > 5 else max(areas)

        bad = 0
        for idx, metric in enumerate(metrics):
            if idx == 0 and metric["visible_pixels"] == 0:
                bad += 1
                continue
            too_few_pixels = stable_visible and metric["visible_pixels"] < stable_visible * 0.35
            too_small_bbox = stable_area and metric["bbox_area"] < stable_area * 0.35
            if too_few_pixels or too_small_bbox:
                bad += 1
            else:
                break
        return bad

    def _safe_relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(Path.cwd()))
        except Exception:
            return str(path)
