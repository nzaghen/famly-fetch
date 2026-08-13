import json
import tempfile
import unittest
from pathlib import Path

from famly_fetch.archive import ArchiveExporter, text_to_markdown, write_markdown


class ArchiveExporterTests(unittest.TestCase):
    def test_entries_support_text_media_or_both_and_sort_oldest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo_path = root / "2024-01-02" / "photo one.jpg"
            photo_path.parent.mkdir()
            photo_path.write_bytes(b"photo")

            exporter = ArchiveExporter(root)
            photo = exporter.media("photo-1", "photo", photo_path)
            exporter.add_entry(
                entry_id="feed:newer",
                source="feed",
                kind="post",
                date="2024-01-03T10:00:00Z",
                author=None,
                text=None,
                media=[photo],
            )
            exporter.add_entry(
                entry_id="note:older",
                source="note",
                kind="Classic",
                date="2024-01-01T10:00:00Z",
                published_at="2024-01-01T11:00:00Z",
                author="Rachel Rivers",
                children=[{"id": "child-1", "name": "Riley"}],
                text="Text only",
            )
            exporter.add_entry(
                entry_id="journey:middle",
                source="journey",
                kind="REGULAR_OBSERVATION",
                date="2024-01-02T10:00:00Z",
                author="Robert Rivers",
                text="Text and photo",
                media=[photo],
            )
            exporter.save()

            payload = json.loads((root / "archive.json").read_text())
            self.assertEqual(
                [entry["entry_id"] for entry in payload["entries"]],
                ["note:older", "journey:middle", "feed:newer"],
            )
            self.assertIsNone(payload["entries"][0]["media"] or None)
            self.assertIsNone(payload["entries"][2]["text"])

            markdown = (root / "archive.md").read_text()
            self.assertLess(
                markdown.index("Text only"), markdown.index("Text and photo")
            )
            self.assertIn("Written by: Rachel Rivers", markdown)
            self.assertIn("Child: Riley", markdown)
            self.assertTrue(markdown.startswith("# Riley's Famly Archive\n"))
            self.assertIn("![Photo 1](<2024-01-02/photo one.jpg>)", markdown)
            html = (root / "archive.html").read_text()
            self.assertIn("<title>Riley&#x27;s Famly Archive</title>", html)
            self.assertIn("<h1>Riley&#x27;s Famly Archive</h1>", html)
            self.assertIn('data-filter="other"', html)
            self.assertEqual(html.count('data-entry-category="other"'), 2)

    def test_existing_json_is_merged_and_duplicate_tagged_photo_is_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo_path = root / "photo.jpg"
            photo_path.write_bytes(b"photo")

            first = ArchiveExporter(root)
            photo = first.media("same-photo", "photo", photo_path)
            first.add_entry(
                entry_id="tagged_photo:same-photo",
                source="tagged_photo",
                kind="photo",
                date="2024-01-01T00:00:00Z",
                author=None,
                text=None,
                media=[photo],
            )
            first.save()

            second = ArchiveExporter(root)
            second.add_entry(
                entry_id="journey:observation",
                source="journey",
                kind="observation",
                date="2024-01-01T00:00:00Z",
                author="Rachel",
                text="The richer entry",
                media=[photo],
            )
            second.save()

            payload = json.loads((root / "archive.json").read_text())
            self.assertEqual(
                [entry["entry_id"] for entry in payload["entries"]],
                ["journey:observation"],
            )

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

    def test_standalone_markdown_conversion_and_html_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "schema_version": 1,
                "generated_at": "2024-01-01T00:00:00Z",
                "entries": [
                    {
                        "entry_id": "note:1",
                        "source": "note",
                        "kind": "note",
                        "date": "2024-01-01T00:00:00Z",
                        "author": None,
                        "children": [],
                        "text": "<p>Hello <strong>world</strong></p>",
                        "media": [],
                    }
                ],
            }
            json_path = root / "data.json"
            json_path.write_text(json.dumps(payload))
            output_path = root / "custom.md"
            write_markdown(json_path, output_path)

            markdown = output_path.read_text()
            self.assertTrue(markdown.startswith("# Famly Archive\n"))
            self.assertIn("Hello **world**", markdown)
            self.assertEqual(text_to_markdown("<p>One</p><p>Two</p>"), "One\n\nTwo")

    def test_empty_assessment_block_is_not_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = ArchiveExporter(root)
            exporter.add_entry(
                entry_id="journey:empty-assessment",
                source="journey",
                kind="ASSESSMENT",
                date="2024-01-01T10:00:00Z",
                author="Rachel",
                text="Assessment summary",
                assessment={
                    "setting": {},
                    "areas": [],
                    "custom_fields": [{"label": "Empty", "value": ""}],
                },
            )
            exporter.save()

            markdown = (root / "archive.md").read_text()
            html = (root / "archive.html").read_text()
            self.assertIn("Assessment summary", markdown)
            self.assertNotIn("### Assessment", markdown)
            self.assertIn("Assessment summary", html)
            self.assertNotIn('<section class="assessment">', html)

    def test_standalone_photos_are_grouped_by_week_separately_from_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = ArchiveExporter(root)

            def photo(media_id):
                path = root / "photos" / f"{media_id}.jpg"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"photo")
                return exporter.media(media_id, "photo", path)

            exporter.add_entry(
                entry_id="tagged_photo:monday",
                source="tagged_photo",
                kind="photo",
                date="2024-01-01T10:00:00Z",
                author=None,
                children=[{"id": "child-1", "name": "Riley"}],
                text="Monday caption",
                media=[photo("monday")],
            )
            exporter.add_entry(
                entry_id="journey:tuesday",
                source="journey",
                kind="observation",
                date="2024-01-02T10:00:00Z",
                author="Rachel",
                text="A separate observation",
                media=[photo("journey")],
            )
            exporter.add_entry(
                entry_id="tagged_photo:wednesday",
                source="tagged_photo",
                kind="photo",
                date="2024-01-03T11:00:00Z",
                author=None,
                children=[{"id": "child-1", "name": "Riley"}],
                text=None,
                media=[photo("wednesday")],
            )
            exporter.add_entry(
                entry_id="tagged_photo:next-week",
                source="tagged_photo",
                kind="photo",
                date="2024-01-08T12:00:00Z",
                author=None,
                children=[{"id": "child-1", "name": "Riley"}],
                text=None,
                media=[photo("next-week")],
            )
            exporter.save()

            payload = json.loads((root / "archive.json").read_text())
            self.assertEqual(len(payload["entries"]), 4)

            markdown = (root / "archive.md").read_text()
            self.assertEqual(markdown.count("## Week of"), 2)
            self.assertIn("## Week of 01 January 2024", markdown)
            self.assertIn("- Photos: 2", markdown)
            self.assertIn("Photo 1: 01 January 2024, 10:00", markdown)
            self.assertIn("Monday caption", markdown)
            self.assertEqual(markdown.count("A separate observation"), 1)

            html = (root / "archive.html").read_text()
            self.assertEqual(html.count('class="post photo-week"'), 2)
            self.assertIn("Week of 01 January 2024", html)
            self.assertIn("2 photos", html)
            self.assertEqual(html.count("A separate observation"), 1)
            self.assertIn('class="weekly-photo"', html)
            self.assertIn(".weekly-photo .photo{min-height:180px}", html)
            self.assertNotIn(".weekly-photo .photo{aspect-ratio", html)
            self.assertIn("01 January 2024, 10:00 | Monday caption", html)
            self.assertNotRegex(html, r'(?:src|href)="https?://')
            self.assertNotIn("fetch(", html)
            self.assertNotIn("XMLHttpRequest", html)
            self.assertIn('data-filter="weekly-photos"', html)
            self.assertIn('data-filter="observation"', html)
            self.assertIn('data-filter="assessment-review"', html)
            self.assertNotIn('data-filter="other"', html)
            self.assertEqual(html.count('data-entry-category="weekly-photos"'), 2)
            self.assertEqual(html.count('data-entry-category="observation"'), 1)
            self.assertEqual(html.count('data-entry-category="other"'), 0)
            self.assertIn("function applyFilter(filter)", html)


if __name__ == "__main__":
    unittest.main()
