import json
import tempfile
import unittest
from pathlib import Path


class TemporaryDirectoryTestCase(unittest.TestCase):
    """Give each test an automatically cleaned, isolated filesystem root."""

    def setUp(self):
        super().setUp()
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)

    def read_json(self, name: str = "archive.json"):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def read_text(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def archive_outputs(self) -> tuple[str, str]:
        return self.read_text("archive.md"), self.read_text("archive.html")

    def write_file(self, name: str, content: bytes = b"photo") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def archived_photo(self, exporter, media_id: str, name: str | None = None) -> dict:
        path = self.write_file(name or f"photos/{media_id}.jpg")
        return exporter.media(media_id, "photo", path)
