import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import piexif

from famly_fetch.archive import ArchiveExporter
from famly_fetch.downloader import FamlyDownloader
from famly_fetch.image import Image
from famly_fetch.media_persistence import MediaPersistence
from tests.support import TemporaryDirectoryTestCase


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


class _SinglePageFeedApi:
    def __init__(self):
        self.calls = 0

    def feed(self, **kwargs):
        self.calls += 1
        return {"feedItems": self.feed_items if self.calls == 1 else []}


class _ParentPostApi(_SinglePageFeedApi):
    feed_items = [
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


class _DuplicateMediaApi:
    def make_api_request(self, method, path, params=None):
        return [
            {
                "imageId": "shared-photo",
                "prefix": "https://img.famly.co",
                "width": 100,
                "height": 100,
                "key": "shared.jpg",
                "createdAt": "2024-01-01T10:00:00+00:00",
                "text": None,
            }
        ]

    def learning_journey_query(self, child_id, cursor=None, first=100):
        return {
            "results": [
                {
                    "id": "shared-observation",
                    "children": [{"id": child_id, "name": "Riley"}],
                    "createdBy": {"name": {"fullName": "Rachel"}},
                    "status": {"createdAt": "2024-01-01T10:00:00+00:00"},
                    "variant": "REGULAR_OBSERVATION",
                    "remark": {"body": "Reused photo observation"},
                    "images": [
                        {
                            "id": "shared-photo",
                            "width": 100,
                            "height": 100,
                            "secret": {
                                "prefix": "https://img.famly.co",
                                "key": "key",
                                "path": "shared.jpg",
                                "expires": "1",
                            },
                        }
                    ],
                    "files": [],
                    "videos": [],
                }
            ],
            "next": None,
        }


class _RichTextApi:
    def learning_journey_query(self, child_id, cursor=None, first=100):
        return {
            "results": [
                {
                    "id": "rich-only",
                    "children": [{"id": child_id, "name": "Riley"}],
                    "createdBy": {"name": {"fullName": "Rachel"}},
                    "status": {"createdAt": "2024-01-01T10:00:00+00:00"},
                    "variant": "REGULAR_OBSERVATION",
                    "remark": {
                        "body": "",
                        "richTextBody": "Rich-only observation text",
                    },
                    "images": [],
                    "files": [],
                    "videos": [],
                }
            ],
            "next": None,
        }


class _FeedFileApi(_SinglePageFeedApi):
    feed_items = [
        {
            "feedItemId": "feed-file",
            "originatorId": "Post:file",
            "createdDate": "2024-01-01T10:00:00+00:00",
            "body": "Report attachment",
            "images": [],
            "files": [
                {
                    "fileId": "report-file",
                    "url": "https://files.famly.co/report.pdf",
                    "filename": "report.pdf",
                }
            ],
            "videos": [],
        }
    ]


class DownloaderArchiveTests(TemporaryDirectoryTestCase):
    def _configured_downloader(self, api, *, archive=True):
        downloader = FamlyDownloader.__new__(FamlyDownloader)
        downloader._pictures_folder = self.root
        downloader.stop_on_existing = False
        downloader.latitude = None
        downloader.longitude = None
        downloader.text_comments = True
        downloader.filename_pattern = "%FP-%Y-%m-%d_%H-%M-%S-%ID"
        downloader.state_file = self.root / "state.json"
        downloader.include_files = False
        downloader.include_videos = False
        downloader.downloaded_images = {}
        downloader.archive = ArchiveExporter(self.root) if archive else None
        downloader._apiClient = api
        return downloader

    def test_tagged_photo_reused_by_journey_keeps_existing_local_path(self):
        downloader = self._configured_downloader(_DuplicateMediaApi())
        downloader.fetch_image = lambda _image, file_path: file_path.write_bytes(
            b"photo"
        )

        with patch("famly_fetch.downloader.time.sleep", return_value=None):
            downloader.download_tagged_images("child-riley", "Riley")
        downloaded_path = next(self.root.glob("2024-01-01/Riley-*.jpg"))
        downloader.download_images_from_learning_journey("child-riley", "Riley")
        downloader.save_archive()

        payload = self.read_json()
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(payload["entries"][0]["source"], "journey")
        archived_path = self.root / payload["entries"][0]["media"][0]["local_path"]
        self.assertEqual(archived_path, downloaded_path)
        self.assertTrue(archived_path.is_file())

    def test_rich_text_body_is_used_when_plain_body_is_empty(self):
        downloader = self._configured_downloader(_RichTextApi())

        downloader.download_images_from_learning_journey("child-riley", "Riley")
        downloader.save_archive()

        payload = self.read_json()
        self.assertEqual(payload["entries"][0]["text"], "Rich-only observation text")
        self.assertIn("Rich-only observation text", self.read_text("archive.html"))

    def test_full_feed_saves_state_for_attachment_only_posts(self):
        downloader = self._configured_downloader(_FeedFileApi(), archive=False)
        downloader.include_files = True
        downloader.fetch_binary = lambda _url, file_path: file_path.write_bytes(
            b"report"
        )

        downloader.download_all_images_from_feed(batch_pause=0)

        state = self.read_json("state.json")
        self.assertIn("report-file", state)

    def test_filename_pattern_cannot_leave_pictures_folder(self):
        downloader = self._configured_downloader(None, archive=False)
        downloader.filename_pattern = "../../escaped-%ID"
        image = Image(
            img_id="photo-1",
            prefix="https://img.famly.co",
            width=1,
            height=1,
            key="photo.jpg",
            date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            text=None,
        )

        with self.assertRaises(ValueError):
            downloader.download_file_path(image, "Riley")

    def test_filename_pattern_without_id_is_made_collision_safe(self):
        downloader = self._configured_downloader(None, archive=False)
        downloader.filename_pattern = "%FP-%Y-%m-%d"
        date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        paths = [
            downloader.download_file_path(
                Image(
                    img_id=image_id,
                    prefix="https://img.famly.co",
                    width=1,
                    height=1,
                    key="photo.jpg",
                    date=date,
                    text=None,
                ),
                "Riley",
            )
            for image_id in ("photo-1", "photo-2")
        ]

        self.assertNotEqual(paths[0], paths[1])
        self.assertTrue(paths[0].name.endswith("-photo-1.jpg"))
        self.assertTrue(paths[1].name.endswith("-photo-2.jpg"))

    def test_trailing_z_timestamp_is_supported(self):
        image = Image.from_dict(
            {
                "imageId": "photo-z",
                "prefix": "https://img.famly.co",
                "width": 1,
                "height": 1,
                "key": "photo.jpg",
                "createdAt": "2024-01-01T10:00:00Z",
            }
        )

        self.assertEqual(image.date.utcoffset(), timedelta(0))

    def test_incomplete_download_preserves_existing_destination(self):
        class _Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": "10"}

        downloader = self._configured_downloader(None, archive=False)
        destination = self.root / "existing.pdf"
        destination.write_bytes(b"valid original")

        with patch(
            "famly_fetch.media_persistence.open_famly_media",
            return_value=_Response(b"short"),
        ):
            with self.assertRaisesRegex(IOError, "Incomplete Famly media"):
                downloader.fetch_binary(
                    "https://files.famly.co/report.pdf", destination
                )

        self.assertEqual(destination.read_bytes(), b"valid original")
        self.assertEqual(list(self.root.glob(".*.part*")), [])

    def test_image_exif_contains_all_standard_famly_date_fields(self):
        downloader = self._configured_downloader(None, archive=False)
        temporary_path = self.root / "temporary.jpg"
        temporary_path.write_bytes(b"synthetic jpeg")
        destination = self.root / "final.jpg"
        image = Image(
            img_id="photo-exif",
            prefix="https://img.famly.co",
            width=1,
            height=1,
            key="photo.jpg",
            date=datetime(2024, 1, 2, 10, 30, tzinfo=timezone(timedelta(hours=1))),
            text="Synthetic observation",
        )
        captured = {}

        def capture_exif(payload):
            captured.update(payload)
            return b"synthetic exif"

        with (
            patch.object(
                MediaPersistence,
                "_fetch_to_temporary_file",
                return_value=temporary_path,
            ),
            patch("famly_fetch.media_persistence.piexif.load", return_value={}),
            patch(
                "famly_fetch.media_persistence.piexif.dump",
                side_effect=capture_exif,
            ),
            patch("famly_fetch.media_persistence.piexif.insert"),
        ):
            downloader.fetch_image(image, destination)

        expected = b"2024:01:02 10:30:00"
        self.assertEqual(captured["0th"][piexif.ImageIFD.DateTime], expected)
        self.assertEqual(captured["Exif"][piexif.ExifIFD.DateTimeOriginal], expected)
        self.assertEqual(captured["Exif"][piexif.ExifIFD.DateTimeDigitized], expected)
        self.assertEqual(captured["Exif"][piexif.ExifIFD.OffsetTimeOriginal], b"+01:00")
        self.assertTrue(destination.is_file())

    def test_output_folder_gets_a_private_gitignore_without_overwriting_one(self):
        downloader = FamlyDownloader.__new__(FamlyDownloader)
        downloader._pictures_folder = self.root
        downloader._protect_output_folder()
        self.assertEqual((self.root / ".gitignore").read_text(), "*\n!.gitignore\n")

        (self.root / ".gitignore").write_text("custom\n")
        downloader._protect_output_folder()
        self.assertEqual((self.root / ".gitignore").read_text(), "custom\n")

    def test_parent_post_text_mode_matches_existing_tagged_photo_without_downloads(
        self,
    ):
        photo_path = self.root / "tagged.jpg"
        photo_path.write_bytes(b"existing photo")
        archive = ArchiveExporter(self.root)
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

        payload = self.read_json()
        feed_entries = [
            entry for entry in payload["entries"] if entry["source"] == "feed"
        ]
        self.assertEqual(len(feed_entries), 1)
        self.assertEqual(feed_entries[0]["text"], "We painted winter trees.")
        self.assertEqual(
            [item["media_id"] for item in feed_entries[0]["media"]],
            ["tagged-photo"],
        )
        self.assertNotIn("Another child's post", self.read_text("archive.json"))
        self.assertIn("We painted winter trees.", self.read_text("archive.html"))

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

        archive = ArchiveExporter(self.root)
        for child_id, name, photo_id in (
            ("child-riley", "Riley", "riley-photo"),
            ("child-rowan", "Rowan", "rowan-photo"),
        ):
            photo_path = self.root / f"{photo_id}.jpg"
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

        downloader = self._configured_downloader(_TwoChildParentApi(), archive=False)
        downloader.archive = archive
        downloader.archive_parent_posts_for_tagged_photos(
            selected_children=[("child-riley", "Riley")]
        )
        downloader.save_archive()

        payload = self.read_json()
        feed_entries = [
            entry for entry in payload["entries"] if entry["source"] == "feed"
        ]
        self.assertEqual([entry["text"] for entry in feed_entries], ["Riley's week"])

    def test_tagged_stop_on_existing_saves_newer_download_state(self):
        class _TaggedApi:
            def make_api_request(self, method, path, params=None):
                return [
                    {
                        "imageId": "new-photo",
                        "prefix": "https://img.famly.co",
                        "width": 1,
                        "height": 1,
                        "key": "new.jpg",
                        "createdAt": "2024-01-02T10:00:00Z",
                    },
                    {
                        "imageId": "old-photo",
                        "prefix": "https://img.famly.co",
                        "width": 1,
                        "height": 1,
                        "key": "old.jpg",
                        "createdAt": "2024-01-01T10:00:00Z",
                    },
                ]

        downloader = self._configured_downloader(_TaggedApi(), archive=False)
        downloader.stop_on_existing = True
        old_image = Image.from_dict(_TaggedApi().make_api_request(None, None)[1])
        old_path = downloader.download_file_path(old_image, "Riley")
        old_path.write_bytes(b"old")
        downloader.downloaded_images = {"old-photo": "already downloaded"}
        downloader.fetch_image = lambda _image, path: path.write_bytes(b"new")

        with patch("famly_fetch.downloader.time.sleep", return_value=None):
            downloader.download_tagged_images("child-riley", "Riley")

        state = self.read_json("state.json")
        self.assertIn("new-photo", state)
        self.assertIn("old-photo", state)

    def test_journey_media_uses_observation_date_but_entry_keeps_posted_date(self):
        class _ObservedDateApi:
            def learning_journey_query(self, child_id, cursor=None, first=100):
                return {
                    "results": [
                        {
                            "id": "observed-date",
                            "children": [{"id": child_id, "name": "Riley"}],
                            "createdBy": {"name": {"fullName": "Rachel"}},
                            "status": {"createdAt": "2024-01-03T10:00:00Z"},
                            "variant": "REGULAR_OBSERVATION",
                            "remark": {
                                "body": "Observed earlier",
                                "date": "2024-01-02T08:30:00Z",
                            },
                            "images": [
                                {
                                    "id": "observed-photo",
                                    "width": 1,
                                    "height": 1,
                                    "secret": {
                                        "prefix": "https://img.famly.co",
                                        "key": "key",
                                        "path": "photo.jpg",
                                        "expires": "1",
                                    },
                                }
                            ],
                            "files": [],
                            "videos": [],
                        }
                    ],
                    "next": None,
                }

        downloader = self._configured_downloader(_ObservedDateApi())
        captured_dates = []

        def fake_fetch(image, path):
            captured_dates.append(image.date.isoformat())
            path.write_bytes(b"photo")

        downloader.fetch_image = fake_fetch
        downloader.download_images_from_learning_journey("child-riley", "Riley")
        downloader.save_archive()

        payload = self.read_json()
        entry = payload["entries"][0]
        self.assertEqual(captured_dates, ["2024-01-02T08:30:00+00:00"])
        self.assertEqual(entry["date"], "2024-01-03T10:00:00Z")
        self.assertEqual(entry["observed_at"], "2024-01-02T08:30:00Z")
        self.assertTrue(entry["media"][0]["local_path"].startswith("2024-01-02/"))

    def test_journey_keeps_text_only_entry_and_links_photo_to_its_post(self):
        downloader = self._configured_downloader(_JourneyApi())

        def fake_fetch_image(_image, file_path):
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"photo")

        downloader.fetch_image = fake_fetch_image

        downloader.download_images_from_learning_journey("child-1", "Riley")
        downloader.save_archive()

        payload = self.read_json()
        self.assertEqual(len(payload["entries"]), 3)

        text_only, with_photo, assessment = payload["entries"]
        self.assertEqual(text_only["text"], "A text-only observation")
        self.assertEqual(text_only["media"], [])
        self.assertEqual(with_photo["text"], "An observation with a photo")
        self.assertEqual(with_photo["media"][0]["media_id"], "photo-1")
        self.assertEqual(with_photo["media"][0]["kind"], "photo")
        self.assertTrue((self.root / with_photo["media"][0]["local_path"]).exists())
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

        markdown = self.read_text("archive.md")
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

        html = self.read_text("archive.html")
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
