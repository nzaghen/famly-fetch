import json
import tempfile
import unittest
from pathlib import Path

from famly_fetch.archive import ArchiveExporter
from famly_fetch.downloader import FamlyDownloader


class _ParentPostApi:
    def __init__(self):
        self.calls = 0

    def feed(self, cursor=None, older_than=None, limit=None):
        self.calls += 1
        if self.calls > 1:
            return {"feedItems": []}
        return {
            "feedItems": [
                {
                    "feedItemId": "unmatched-feed-item",
                    "originatorId": "Post:unmatched",
                    "createdDate": "2024-01-01T09:00:00Z",
                    "body": "Another child's post",
                    "images": [{"imageId": "other-photo"}],
                },
                {
                    "feedItemId": "matched-feed-item",
                    "originatorId": "Post:matched",
                    "createdDate": "2024-01-02T09:00:00Z",
                    "body": "We painted winter trees.",
                    "author": {"title": "Rainbow Nursery"},
                    "images": [
                        {"imageId": "tagged-photo"},
                        {"imageId": "other-child-photo"},
                    ],
                },
            ]
        }


class DownloaderArchiveTests(unittest.TestCase):
    def test_parent_post_text_mode_matches_existing_tagged_photo_without_downloads(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo_path = root / "tagged.jpg"
            photo_path.write_bytes(b"existing photo")
            archive = ArchiveExporter(root)
            tagged_photo = archive.media("tagged-photo", "photo", photo_path)
            archive.add_entry(
                entry_id="tagged_photo:tagged-photo",
                source="tagged_photo",
                kind="photo",
                date="2024-01-02T10:00:00Z",
                author=None,
                text=None,
                media=[tagged_photo],
            )

            downloader = FamlyDownloader.__new__(FamlyDownloader)
            downloader.archive = archive
            downloader._apiClient = _ParentPostApi()
            downloader.archive_parent_posts_for_tagged_photos()
            downloader.save_archive()

            payload = json.loads((root / "archive.json").read_text())
            feed_entries = [
                entry for entry in payload["entries"] if entry["source"] == "feed"
            ]
            self.assertEqual(len(feed_entries), 1)
            self.assertEqual(feed_entries[0]["text"], "We painted winter trees.")
            self.assertEqual(
                [item["media_id"] for item in feed_entries[0]["media"]],
                ["tagged-photo"],
            )
            self.assertNotIn(
                "Another child's post", (root / "archive.json").read_text()
            )
