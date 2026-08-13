import json
import tempfile
import unittest
from pathlib import Path

from famly_fetch.archive import ArchiveExporter


class ArchiveExporterTests(unittest.TestCase):
    def test_feed_duplicate_does_not_hide_tagged_photo_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo_path = root / "photo.jpg"
            photo_path.write_bytes(b"photo")
            exporter = ArchiveExporter(root)
            photo = exporter.media("same-photo", "photo", photo_path)
            exporter.add_entry(
                entry_id="tagged_photo:same-photo",
                source="tagged_photo",
                kind="photo",
                date="2024-01-01T00:00:00Z",
                author=None,
                text=None,
                media=[photo],
            )
            exporter.add_entry(
                entry_id="feed:post",
                source="feed",
                kind="post",
                date="2024-01-01T00:00:00Z",
                author="Rainbow Nursery",
                text="A feed post",
                media=[photo],
            )
            exporter.save()

            payload = json.loads((root / "archive.json").read_text())
            self.assertEqual(
                {entry["entry_id"] for entry in payload["entries"]},
                {"tagged_photo:same-photo", "feed:post"},
            )
