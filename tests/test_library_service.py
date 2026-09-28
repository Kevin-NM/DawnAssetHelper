import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from src.services.library_service import resolve_gif, static_thumbnail, trash_hero, migrate_library, clean_generated, clean_intermediates


class LibraryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def make_gif(self, relative="heroes/hero1/gif/idle.gif", color="red"):
        gif = self.root / relative
        gif.parent.mkdir(parents=True, exist_ok=True)
        first = Image.new("RGBA", (400, 800), color)
        second = Image.new("RGBA", (400, 800), "blue")
        first.save(gif, save_all=True, append_images=[second], duration=100, loop=0)
        return gif

    def test_thumbnail_is_small_static_png_and_cached(self):
        gif = self.make_gif()
        thumbnail = static_thumbnail(self.root, str(gif.relative_to(self.root)))
        with Image.open(thumbnail) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.n_frames, 1)
            self.assertEqual(image.size, (128, 256))
        with patch("src.services.library_service.Image.open", side_effect=AssertionError("cache miss")):
            self.assertEqual(static_thumbnail(self.root, str(gif.relative_to(self.root))), thumbnail)

    def test_changed_source_invalidates_cache(self):
        gif = self.make_gif()
        source = str(gif.relative_to(self.root))
        before = static_thumbnail(self.root, source)
        self.make_gif(color="green")
        stat = gif.stat()
        os.utime(gif, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        self.assertNotEqual(before, static_thumbnail(self.root, source))

    def test_full_image_preserves_dimensions_transparency_and_edges(self):
        gif = self.root / "heroes/hero1/gif/idle.gif"
        gif.parent.mkdir(parents=True)
        image = Image.new("RGBA", (400, 800), (0, 0, 0, 0))
        image.putpixel((200, 0), (255, 0, 0, 255))
        image.putpixel((200, 799), (255, 0, 0, 255))
        image.save(gif, transparency=0)
        full = static_thumbnail(self.root, "heroes/hero1/gif/idle.gif", full_size=True)
        with Image.open(full) as png:
            self.assertEqual(png.size, (400, 800))
            self.assertEqual(png.getpixel((0, 0))[3], 0)
            self.assertEqual(png.getpixel((200, 0))[3], 255)
            self.assertEqual(png.getpixel((200, 799))[3], 255)

    def test_migration_cleanup_idempotence_conflicts_and_run_fallback(self):
        output = self.root / "output"
        saved = self.root / "library"
        legacy = output / "heroes/hero1"
        legacy.mkdir(parents=True)
        (legacy / "summary.json").write_text('{"status":"saved"}')
        gif = output / "runs/run1/heroes/hero1/gif/idle.gif"
        gif.parent.mkdir(parents=True)
        Image.new("RGBA", (10, 20), "red").save(gif)
        (output / "temp").mkdir()
        (output / "resource_index").mkdir()
        self.assertEqual(migrate_library(output, saved)["moved"], ["hero1"])
        self.assertEqual(migrate_library(output, saved)["moved"], [])
        permanent = saved / "heroes/hero1/gif/idle.gif"
        self.assertEqual(permanent.read_bytes(), gif.read_bytes())
        legacy.mkdir()
        (legacy / "keep.txt").write_text("conflict")
        self.assertEqual(migrate_library(output, saved)["conflicts"], ["hero1"])
        clean_generated(output, saved)
        self.assertTrue(permanent.is_file())
        self.assertTrue((legacy / "keep.txt").is_file())
        self.assertTrue((output / "resource_index").is_dir())
        self.assertFalse((output / "runs").exists())
        self.assertFalse((output / "temp").exists())
        self.assertEqual(clean_generated(output, saved)["removed"], [])

    def test_cleanup_rejects_overlapping_roots(self):
        self.make_gif()
        with self.assertRaises(ValueError):
            clean_generated(self.root, self.root / "library")
        self.assertTrue((self.root / "heroes/hero1/gif/idle.gif").is_file())

    def test_failed_fallback_copy_blocks_cleanup_and_can_retry(self):
        output, saved = self.root / "output", self.root / "library"
        hero = saved / "heroes/hero1"
        hero.mkdir(parents=True)
        gif = output / "runs/run1/heroes/hero1/gif/idle.gif"
        gif.parent.mkdir(parents=True)
        Image.new("RGBA", (10, 20), "red").save(gif)
        def incomplete_copy(source, destination):
            destination.mkdir()
            (destination / "idle.gif").write_bytes(b"partial")
            raise OSError("disk full")
        with patch("src.services.library_service.shutil.copytree", incomplete_copy):
            with self.assertRaises(OSError):
                clean_generated(output, saved)
        self.assertTrue(gif.is_file())
        self.assertFalse((hero / "gif/idle.gif").exists())
        self.assertEqual(list(hero.glob(".gif_migration_*")), [])
        clean_generated(output, saved)
        with Image.open(hero / "gif/idle.gif") as image:
            self.assertEqual(image.size, (10, 20))

    def test_intermediate_cleanup_preserves_saved_and_run_gifs(self):
        self.make_gif()
        run_gif = self.make_gif("runs/run1/heroes/hero1/gif/idle.gif")
        for name in ("raw_export", "diagnostics"):
            directory = run_gif.parent.parent / name
            directory.mkdir()
            (directory / "scratch.txt").write_text("temporary")
        removed = clean_intermediates(self.root, "hero1")
        self.assertEqual(len(removed), 2)
        self.assertTrue(run_gif.is_file())
        self.assertTrue((self.root / "heroes/hero1/gif/idle.gif").is_file())
        with self.assertRaises(ValueError):
            clean_intermediates(self.root, "../hero1")

    def test_invalid_paths_cannot_read_or_move_outside_output(self):
        for source in ("../secret.gif", str(self.root / "secret.gif"), "temp/secret.gif", "heroes/hero1/gif/atlas.png"):
            with self.assertRaises(ValueError):
                resolve_gif(self.root, source)
        for hero in ("..", "../hero1", "hero1/child", "C:\\private", ""):
            with self.assertRaises(ValueError):
                trash_hero(self.root, hero)

    def test_delete_moves_library_and_all_runs_preserving_other_heroes(self):
        paths = ["heroes/hero1/gif/idle.gif", "runs/run1/heroes/hero1/gif/idle.gif", "runs/run2/heroes/hero1/gif/idle.gif"]
        for path in paths:
            self.make_gif(path)
        other = self.make_gif("heroes/hero2/gif/idle.gif")
        result = trash_hero(self.root, "hero1")
        self.assertEqual(result["moved_count"], 3)
        trash = Path(result["trash_path"])
        for path in paths:
            self.assertFalse((self.root / path).exists())
            self.assertTrue((trash / path).is_file())
        self.assertTrue((trash / "manifest.json").is_file())
        self.assertTrue(other.is_file())
        with self.assertRaises(FileNotFoundError):
            trash_hero(self.root, "hero1")

    def test_failed_move_restores_previous_moves(self):
        self.make_gif()
        self.make_gif("runs/run1/heroes/hero1/gif/idle.gif")
        original = Path.rename

        def rename(path, destination):
            if path == self.root / "runs/run1/heroes/hero1":
                raise PermissionError("file in use")
            return original(path, destination)

        with patch.object(Path, "rename", rename):
            with self.assertRaises(PermissionError):
                trash_hero(self.root, "hero1")
        self.assertTrue((self.root / "heroes/hero1/gif/idle.gif").is_file())
        self.assertTrue((self.root / "runs/run1/heroes/hero1/gif/idle.gif").is_file())

    def test_linked_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            self.root.joinpath("heroes").mkdir()
            link = self.root / "heroes/hero1"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink privilege unavailable")
            with self.assertRaises(ValueError):
                trash_hero(self.root, "hero1")


if __name__ == "__main__":
    unittest.main()
