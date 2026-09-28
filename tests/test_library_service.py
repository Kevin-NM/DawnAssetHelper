import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from src.services.library_service import resolve_gif, static_thumbnail, trash_hero


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
