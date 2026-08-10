import json
import tempfile
import unittest
from pathlib import Path

from famly_fetch.archive import ArchiveExporter
from famly_fetch.downloader import FamlyDownloader


class _JourneyApi:
    def __init__(self):
        self.called = False

    def learning_journey_query(self, child_id, cursor=None, first=100):
        if self.called:
            raise AssertionError("Unexpected extra page")
        self.called = True
        return {
            "results": [
                {
                    "id": "text-only",
                    "children": [{"name": "Riley"}],
                    "createdBy": {"name": {"fullName": "Rachel"}},
                    "status": {"createdAt": "2024-01-01T09:00:00+00:00"},
                    "variant": "REGULAR_OBSERVATION",
                    "remark": {"body": "A text-only observation"},
                    "images": [],
                    "files": [],
                    "videos": [],
                },
                {
                    "id": "text-and-photo",
                    "children": [{"name": "Riley"}],
                    "createdBy": {"name": {"fullName": "Robert"}},
                    "status": {"createdAt": "2024-01-02T09:00:00+00:00"},
                    "variant": "PARENT_OBSERVATION",
                    "remark": {"body": "An observation with a photo"},
                    "images": [
                        {
                            "id": "photo-1",
                            "width": 100,
                            "height": 100,
                            "secret": {
                                "prefix": "https://cdn.example",
                                "key": "key",
                                "path": "photo.jpg",
                                "expires": "123",
                                "crop": None,
                            },
                        }
                    ],
                    "files": [],
                    "videos": [],
                },
                {
                    "id": "assessment",
                    "children": [{"id": "child-1", "name": "Riley"}],
                    "createdBy": {"name": {"fullName": "Rose"}},
                    "status": {"createdAt": "2024-01-03T09:00:00+00:00"},
                    "variant": "ASSESSMENT",
                    "version": "V2",
                    "settings": {
                        "assessmentSetting": {
                            "assessmentSettingsId": "setting-1",
                            "title": "Development Matters",
                        }
                    },
                    "remark": {
                        "body": "A summative assessment",
                        "date": "2024-01-02T15:30:00+00:00",
                        "areas": [
                            {
                                "area": {
                                    "id": "area-1",
                                    "frameworkId": "framework-1",
                                    "parentId": None,
                                    "title": "Communication and language",
                                    "description": None,
                                    "abbr": "CL",
                                    "framework": {
                                        "id": "framework-1",
                                        "title": "EYFS",
                                        "owner": "Famly",
                                    },
                                },
                                "refinement": None,
                                "note": "Confident progress",
                                "areaRefinementSettings": {
                                    "ageBandSetting": {
                                        "ageBandSettingId": "band-1",
                                        "from": 36,
                                        "to": 48,
                                        "label": "36–48 months",
                                    },
                                    "assessmentOptionSetting": {
                                        "assessmentOptionSettingId": "option-1",
                                        "label": "Progressing well",
                                        "backgroundColor": "#ffffff",
                                        "fontColor": "#000000",
                                    },
                                },
                            }
                        ],
                        "customFieldValues": [
                            {
                                "customFieldSetting": {
                                    "assessmentSettingsId": "setting-1",
                                    "customFieldId": "field-1",
                                    "label": "Key person summary",
                                    "order": 1,
                                },
                                "value": "Ready for the next challenge",
                            }
                        ],
                    },
                    "nextStep": {"body": "Practise longer conversations"},
                    "images": [],
                    "files": [],
                    "videos": [],
                },
            ],
            "next": None,
        }


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
    def test_output_folder_gets_a_private_gitignore_without_overwriting_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloader = FamlyDownloader.__new__(FamlyDownloader)
            downloader._pictures_folder = root
            downloader._protect_output_folder()
            self.assertEqual((root / ".gitignore").read_text(), "*\n!.gitignore\n")

            (root / ".gitignore").write_text("custom\n")
            downloader._protect_output_folder()
            self.assertEqual((root / ".gitignore").read_text(), "custom\n")

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
            self.assertIn(
                "We painted winter trees.", (root / "archive.html").read_text()
            )

    def test_parent_post_text_can_be_scoped_to_one_child(self):
        class _TwoChildParentApi:
            def feed(self, **kwargs):
                return {
                    "feedItems": [
                        {
                            "feedItemId": "riley-post",
                            "originatorId": "Post:riley",
                            "createdDate": "2024-01-01T10:00:00Z",
                            "body": "Riley's week",
                            "images": [{"imageId": "riley-photo"}],
                        },
                        {
                            "feedItemId": "rowan-post",
                            "originatorId": "Post:rowan",
                            "createdDate": "2024-01-01T11:00:00Z",
                            "body": "Rowan's week",
                            "images": [{"imageId": "rowan-photo"}],
                        },
                    ]
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ArchiveExporter(root)
            for child_id, name, photo_id in (
                ("child-riley", "Riley", "riley-photo"),
                ("child-rowan", "Rowan", "rowan-photo"),
            ):
                photo_path = root / f"{photo_id}.jpg"
                photo_path.write_bytes(b"photo")
                media = archive.media(photo_id, "photo", photo_path)
                archive.add_entry(
                    entry_id=f"tagged_photo:{photo_id}",
                    source="tagged_photo",
                    kind="photo",
                    date="2024-01-01T09:00:00Z",
                    author=None,
                    children=[{"id": child_id, "name": name}],
                    text=None,
                    media=[media],
                )

            downloader = FamlyDownloader.__new__(FamlyDownloader)
            downloader.archive = archive
            downloader._apiClient = _TwoChildParentApi()
            downloader.archive_parent_posts_for_tagged_photos(
                selected_children=[("child-riley", "Riley")]
            )
            downloader.save_archive()

            payload = json.loads((root / "archive.json").read_text())
            feed_entries = [
                entry for entry in payload["entries"] if entry["source"] == "feed"
            ]
            self.assertEqual(
                [entry["text"] for entry in feed_entries], ["Riley's week"]
            )

    def test_journey_keeps_text_only_entry_and_links_photo_to_its_post(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloader = FamlyDownloader.__new__(FamlyDownloader)
            downloader._pictures_folder = root
            downloader.stop_on_existing = False
            downloader.latitude = None
            downloader.longitude = None
            downloader.text_comments = True
            downloader.filename_pattern = "%FP-%Y-%m-%d_%H-%M-%S-%ID"
            downloader.state_file = root / "state.json"
            downloader.include_files = False
            downloader.include_videos = False
            downloader.downloaded_images = {}
            downloader.archive = ArchiveExporter(root)
            downloader._apiClient = _JourneyApi()

            def fake_fetch_image(_image, file_path):
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(b"photo")

            downloader.fetch_image = fake_fetch_image

            downloader.download_images_from_learning_journey("child-1", "Riley")
            downloader.save_archive()

            payload = json.loads((root / "archive.json").read_text())
            self.assertEqual(len(payload["entries"]), 3)

            text_only, with_photo, assessment = payload["entries"]
            self.assertEqual(text_only["text"], "A text-only observation")
            self.assertEqual(text_only["media"], [])
            self.assertEqual(with_photo["text"], "An observation with a photo")
            self.assertEqual(with_photo["media"][0]["media_id"], "photo-1")
            self.assertEqual(with_photo["media"][0]["kind"], "photo")
            self.assertTrue((root / with_photo["media"][0]["local_path"]).exists())
            self.assertEqual(assessment["kind"], "ASSESSMENT")
            self.assertEqual(
                assessment["assessment"]["setting"]["title"],
                "Development Matters",
            )
            self.assertEqual(
                assessment["assessment"]["areas"][0]["assessment_option"]["label"],
                "Progressing well",
            )
            self.assertEqual(
                assessment["assessment"]["custom_fields"][0]["value"],
                "Ready for the next challenge",
            )
            self.assertEqual(assessment["next_step"], "Practise longer conversations")

            markdown = (root / "archive.md").read_text()
            self.assertLess(
                markdown.index("A text-only observation"),
                markdown.index("An observation with a photo"),
            )
            self.assertIn("Written by: Rachel", markdown)
            self.assertNotIn("Child: Riley", markdown)
            self.assertIn("![Photo 1]", markdown)
            self.assertIn("### Assessment", markdown)
            self.assertIn("Configuration: Development Matters", markdown)
            self.assertIn("Communication and language", markdown)
            self.assertIn("Progressing well", markdown)
            self.assertIn("Confident progress", markdown)
            self.assertIn("Key person summary", markdown)
            self.assertIn("### What's next", markdown)
            self.assertNotIn("— Journey", markdown)

            html = (root / "archive.html").read_text()
            self.assertIn('<meta charset="utf-8">', html)
            self.assertIn("max-width:860px", html)
            self.assertEqual(html.count('data-entry-category="observation"'), 2)
            self.assertEqual(html.count('data-entry-category="assessment-review"'), 1)
            self.assertIn("Assessments &amp; reviews", html)
            self.assertIn("A summative assessment", html)
            self.assertIn("Configuration", html)
            self.assertIn('<div class="photos">', html)


if __name__ == "__main__":
    unittest.main()
