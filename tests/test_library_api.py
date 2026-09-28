import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from server import app as server


class LibraryApiTests(unittest.TestCase):
    def test_listing_and_detail_use_static_previews_and_delete_does_not_resurface(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            hero = "test_library_disposable"
            for relative in (f"heroes/{hero}", f"runs/test_run/heroes/{hero}"):
                gif = root / relative / "gif/idle.gif"
                gif.parent.mkdir(parents=True)
                Image.new("RGBA", (10, 20), "red").save(gif)
            config = SimpleNamespace(output_root=str(root), assetstudio_use_absolute_output_path=False)
            with patch.object(server, "get_config", return_value=config), \
                 patch.object(server, "active_worker", None), \
                 patch.dict(server.worker_status, running=False):
                library = asyncio.run(server.api_library())
                self.assertEqual(len(library["library"]), 1)
                self.assertTrue(library["library"][0]["static_thumbnail"].startswith("/api/library-thumbnail?"))
                detail = asyncio.run(server.api_library_detail(hero))
                self.assertTrue(detail["gif_files_detail"][0]["thumbnail"].startswith("/api/library-thumbnail?"))
                preview = server.api_library_thumbnail(f"heroes/{hero}/gif/idle.gif")
                self.assertEqual(preview.media_type, "image/png")
                result = asyncio.run(server.api_delete_library_hero(hero))
                self.assertEqual(result["moved_count"], 2)
                self.assertEqual(asyncio.run(server.api_library()), {"library": [], "runs": []})

    def test_running_job_refuses_delete(self):
        with patch.dict(server.worker_status, running=True):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(server.api_delete_library_hero("test_library_disposable"))
            self.assertEqual(error.exception.status_code, 409)

    def test_thumbnail_refuses_traversal_and_missing_source(self):
        for source, status in (("../secret.gif", 400), ("heroes/test_missing/gif/idle.gif", 404)):
            with self.assertRaises(HTTPException) as error:
                server.api_library_thumbnail(source)
            self.assertEqual(error.exception.status_code, status)


if __name__ == "__main__":
    unittest.main()
