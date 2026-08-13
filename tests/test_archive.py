import json
import re
import unittest

from famly_fetch.archive import (
    ArchiveFileError,
    ArchiveExporter,
    ExclusionFileError,
    build_render_report,
    text_to_markdown,
    write_html,
    write_markdown,
    write_render_report,
)
from tests.support import TemporaryDirectoryTestCase


class ArchiveExporterTests(TemporaryDirectoryTestCase):
    def test_explicit_archive_child_controls_title_and_survives_reload(self):
        exporter = ArchiveExporter(self.root)
        exporter.set_archive_children([("child-riley", "Riley")])
        exporter.add_entry(
            entry_id="journey:shared-observation",
            source="journey",
            kind="observation",
            date="2024-01-01T10:00:00Z",
            author="Rachel",
            children=[
                {"id": "child-riley", "name": "Riley"},
                {"id": "child-rowan", "name": "Rowan"},
            ],
            text="A shared observation",
        )
        exporter.save()

        payload = self.read_json()
        self.assertEqual(
            payload["archive_children"],
            [{"id": "child-riley", "name": "Riley"}],
        )
        self.assertTrue(
            self.read_text("archive.md").startswith("# Riley's Famly Archive")
        )
        self.assertNotIn("Child:", self.read_text("archive.md"))
        self.assertNotIn("Child:", self.read_text("archive.html"))
        self.assertNotIn("Riley & Rowan", self.read_text("archive.html"))

        ArchiveExporter(self.root).save()
        self.assertTrue(
            self.read_text("archive.md").startswith("# Riley's Famly Archive")
        )
        self.assertNotIn("Child:", self.read_text("archive.md"))
        self.assertNotIn("Child:", self.read_text("archive.html"))

    def test_legacy_archive_title_prefers_child_specific_tagged_entries(self):
        exporter = ArchiveExporter(self.root)
        tagged_media = self.archived_photo(exporter, "tagged-photo", "tagged.jpg")
        exporter.add_entry(
            entry_id="tagged_photo:tagged-photo",
            source="tagged_photo",
            kind="photo",
            date="2024-01-01T10:00:00Z",
            author=None,
            children=[{"id": "child-riley", "name": "Riley"}],
            text=None,
            media=[tagged_media],
        )
        exporter.add_entry(
            entry_id="journey:shared-observation",
            source="journey",
            kind="observation",
            date="2024-01-02T10:00:00Z",
            author="Rachel",
            children=[
                {"id": "child-riley", "name": "Riley"},
                {"id": "child-rowan", "name": "Rowan"},
            ],
            text="A shared observation",
        )
        exporter.save()

        self.assertTrue(
            self.read_text("archive.md").startswith("# Riley's Famly Archive")
        )
        self.assertNotIn("Child:", self.read_text("archive.md"))
        self.assertNotIn("Child:", self.read_text("archive.html"))

    def test_legacy_archive_title_collapses_aliases_for_the_same_child_id(self):
        exporter = ArchiveExporter(self.root)
        exporter.add_entry(
            entry_id="journey:first-name",
            source="journey",
            kind="observation",
            date="2024-01-01T10:00:00Z",
            author="Rachel",
            children=[{"id": "child-riley", "name": "Riley"}],
            text="First name form",
        )
        exporter.add_entry(
            entry_id="journey:first-name-and-initial",
            source="journey",
            kind="observation",
            date="2024-01-02T10:00:00Z",
            author="Rachel",
            children=[{"id": "child-riley", "name": "Riley R."}],
            text="Initial form",
        )
        exporter.save()

        markdown = self.read_text("archive.md")
        self.assertTrue(markdown.startswith("# Riley's Famly Archive"))
        self.assertNotIn("Riley & Riley R.", markdown)
        self.assertNotIn("Child:", markdown)
        self.assertNotIn("Child:", self.read_text("archive.html"))

    def test_existing_archive_rejects_missing_and_duplicate_entry_ids(self):
        archive_path = self.root / "archive.json"
        for entries in (
            [{"source": "journey"}],
            [{"entry_id": "same"}, {"entry_id": "same"}],
        ):
            archive_path.write_text(
                json.dumps({"schema_version": 1, "entries": entries})
            )
            with self.assertRaises(ArchiveFileError):
                ArchiveExporter(self.root)

    def test_invalid_existing_archive_fails_closed_without_overwriting_it(self):
        archive_path = self.root / "archive.json"
        damaged_content = '{"entries": [DAMAGED'
        archive_path.write_text(damaged_content)

        with self.assertRaises(ArchiveFileError):
            ArchiveExporter(self.root)

        self.assertEqual(archive_path.read_text(), damaged_content)

    def test_entries_support_text_media_or_both_and_sort_oldest_first(self):
        exporter = ArchiveExporter(self.root)
        photo = self.archived_photo(exporter, "photo-1", "2024-01-02/photo one.jpg")
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

        payload = self.read_json()
        self.assertEqual(
            [entry["entry_id"] for entry in payload["entries"]],
            ["note:older", "journey:middle", "feed:newer"],
        )
        self.assertIsNone(payload["entries"][0]["media"] or None)
        self.assertIsNone(payload["entries"][2]["text"])

        markdown = self.read_text("archive.md")
        self.assertLess(markdown.index("Text only"), markdown.index("Text and photo"))
        self.assertIn("Written by: Rachel Rivers", markdown)
        self.assertNotIn("Child: Riley", markdown)
        self.assertIn("- Posted: 01 January 2024", markdown)
        self.assertNotIn("- Posted: 01 January 2024, 10:00", markdown)
        self.assertNotIn("- Observed:", markdown)
        self.assertNotIn("- Published:", markdown)
        self.assertTrue(markdown.startswith("# Riley's Famly Archive\n"))
        self.assertIn("![Photo 1](<2024-01-02/photo one.jpg>)", markdown)
        html = self.read_text("archive.html")
        self.assertIn("<title>Riley&#x27;s Famly Archive</title>", html)
        self.assertIn("<h1>Riley&#x27;s Famly Archive</h1>", html)
        self.assertIn("<span>Posted 01 January 2024</span>", html)
        self.assertNotIn("<span>Posted 01 January 2024, 10:00</span>", html)
        self.assertIn('data-filter="other"', html)
        self.assertEqual(html.count('data-entry-category="other"'), 2)

    def test_existing_json_is_merged_and_duplicate_tagged_photo_is_hidden(self):
        first = ArchiveExporter(self.root)
        photo = self.archived_photo(first, "same-photo", "photo.jpg")
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

        second = ArchiveExporter(self.root)
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

        payload = self.read_json()
        self.assertEqual(
            [entry["entry_id"] for entry in payload["entries"]],
            ["journey:observation"],
        )

    def test_same_tagged_photo_merges_children_in_combined_archive(self):
        exporter = ArchiveExporter(self.root)
        photo = self.archived_photo(exporter, "shared-photo", "shared.jpg")

        for child_id, name in (
            ("child-riley", "Riley"),
            ("child-rowan", "Rowan"),
        ):
            exporter.add_entry(
                entry_id="tagged_photo:shared-photo",
                source="tagged_photo",
                kind="photo",
                date="2024-01-01T00:00:00Z",
                author=None,
                children=[{"id": child_id, "name": name}],
                text=None,
                media=[photo],
            )
        exporter.save()

        payload = self.read_json()
        self.assertEqual(
            {child["name"] for child in payload["entries"][0]["children"]},
            {"Riley", "Rowan"},
        )
        self.assertTrue(
            (self.root / "archive.md")
            .read_text()
            .startswith("# Riley & Rowan's Famly Archive\n")
        )
        self.assertIn("Child: Riley, Rowan", self.read_text("archive.md"))

    def test_feed_duplicate_does_not_hide_tagged_photo_entry(self):
        exporter = ArchiveExporter(self.root)
        photo = self.archived_photo(exporter, "same-photo", "photo.jpg")
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

        payload = self.read_json()
        self.assertEqual(
            {entry["entry_id"] for entry in payload["entries"]},
            {"tagged_photo:same-photo", "feed:post"},
        )

    def test_standalone_markdown_conversion_and_html_text(self):
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
        json_path = self.root / "data.json"
        json_path.write_text(json.dumps(payload))
        output_path = self.root / "custom.md"
        write_markdown(json_path, output_path)

        markdown = output_path.read_text()
        self.assertTrue(markdown.startswith("# Famly Archive\n"))
        self.assertIn("Hello **world**", markdown)
        self.assertEqual(text_to_markdown("<p>One</p><p>Two</p>"), "One\n\nTwo")

    def test_exclusion_file_only_applies_to_readable_generation(self):
        excluded_entry_id = "journey:private-entry"
        (self.root / "archive.exclude.json").write_text(
            json.dumps({"excluded_entry_ids": [excluded_entry_id]})
        )

        exporter = ArchiveExporter(self.root)
        for entry_id, text in (
            (excluded_entry_id, "Excluded text"),
            ("journey:kept-entry", "Kept text"),
        ):
            exporter.add_entry(
                entry_id=entry_id,
                source="journey",
                kind="observation",
                date="2024-01-01T10:00:00Z",
                author="Rachel",
                text=text,
            )
        exporter.save()

        payload = self.read_json()
        self.assertEqual(
            {entry["entry_id"] for entry in payload["entries"]},
            {excluded_entry_id, "journey:kept-entry"},
        )
        self.assertNotIn("Excluded text", self.read_text("archive.md"))
        self.assertNotIn("Excluded text", self.read_text("archive.html"))

        write_markdown(self.root / "archive.json")
        write_html(self.root / "archive.json")
        self.assertNotIn("Excluded text", self.read_text("archive.md"))
        self.assertNotIn("Excluded text", self.read_text("archive.html"))

    def test_invalid_exclusion_file_fails_closed(self):
        (self.root / "archive.json").write_text(
            json.dumps({"schema_version": 1, "entries": []})
        )
        (self.root / "archive.exclude.json").write_text("not valid JSON")

        with self.assertRaises(ExclusionFileError):
            write_markdown(self.root / "archive.json")

    def test_empty_assessment_block_is_not_rendered(self):
        exporter = ArchiveExporter(self.root)
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

        markdown, html = self.archive_outputs()
        self.assertIn("Assessment summary", markdown)
        self.assertNotIn("### Assessment", markdown)
        self.assertIn("Assessment summary", html)
        self.assertNotIn('<section class="assessment">', html)

    def test_multiple_photos_render_as_clickable_editorial_grid(self):
        exporter = ArchiveExporter(self.root)
        photos = [
            self.archived_photo(exporter, f"photo-{index}", f"photos/photo {index}.jpg")
            for index in range(1, 4)
        ]

        exporter.add_entry(
            entry_id="journey:grid",
            source="journey",
            kind="observation",
            date="2024-01-01T00:00:00Z",
            author="Rachel",
            text="Three photos",
            media=photos,
        )
        exporter.save()

        markdown, html = self.archive_outputs()
        self.assertIn('<table role="presentation"', markdown)
        self.assertIn('<td colspan="2"', markdown)
        self.assertEqual(markdown.count('<td width="50%"'), 2)
        self.assertEqual(markdown.count("<img "), 3)
        self.assertEqual(markdown.count("<a href="), 3)
        self.assertIn('src="photos/photo 1.jpg"', markdown)
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn("max-width:860px", html)
        self.assertIn('<a class="photo hero"', html)
        self.assertIn('href="photos/photo 1.jpg"', html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn(
            ".photo img{display:block;height:100%;max-height:520px;object-fit:contain",
            html,
        )
        self.assertNotIn("object-fit:cover", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("data-lightbox", html)
        self.assertNotIn("data-print", html)
        self.assertNotIn("@media print", html)
        self.assertNotIn("window.print()", html)
        self.assertIn("<script>", html)
        self.assertNotIn("<script src=", html)
        self.assertIn(".filter-button{", html)
        self.assertIn("min-height:44px", html)
        self.assertIn("width:44px;z-index:2", html)

    def test_standalone_photos_are_grouped_by_week_separately_from_journey(self):
        exporter = ArchiveExporter(self.root)

        def photo(media_id):
            return self.archived_photo(exporter, media_id)

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

        payload = self.read_json()
        self.assertEqual(len(payload["entries"]), 4)

        markdown = self.read_text("archive.md")
        self.assertEqual(markdown.count("## Week of"), 2)
        self.assertIn("## Week of 01 January 2024", markdown)
        self.assertIn("- Photos: 2", markdown)
        self.assertIn("Photo 1: 01 January 2024, 10:00", markdown)
        self.assertIn("Monday caption", markdown)
        self.assertEqual(markdown.count("A separate observation"), 1)

        html = self.read_text("archive.html")
        self.assertEqual(html.count('class="post photo-week"'), 2)
        self.assertIn("Week of 01 January 2024", html)
        self.assertIn("2 photos", html)
        self.assertEqual(html.count("A separate observation"), 1)
        self.assertIn('class="weekly-photo"', html)
        self.assertIn(".weekly-photo .photo{min-height:180px}", html)
        self.assertNotIn(".weekly-photo .photo{aspect-ratio", html)
        gallery_ids = re.findall(
            r'<a class="photo(?: [^"]*)?"[^>]+data-gallery="([^"]+)"', html
        )
        self.assertEqual(len(gallery_ids), 4)
        self.assertEqual(gallery_ids[0], gallery_ids[1])
        self.assertNotEqual(gallery_ids[1], gallery_ids[2])
        self.assertNotEqual(gallery_ids[2], gallery_ids[3])
        self.assertIn('const entry = item.closest("[data-entry-category]");', html)
        self.assertIn("return !entry || !entry.hidden;", html)
        self.assertNotIn("item.dataset.gallery === link.dataset.gallery", html)
        self.assertIn('event.key === "ArrowRight"', html)
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

    def test_weekly_description_comes_from_matching_parent_feed_post(self):
        exporter = ArchiveExporter(self.root)
        shared_photo = self.archived_photo(exporter, "shared-photo")

        exporter.add_entry(
            entry_id="tagged_photo:shared-photo",
            source="tagged_photo",
            kind="photo",
            date="2024-01-02T10:00:00Z",
            author=None,
            children=[{"id": "child-1", "name": "Riley"}],
            text="Image-level text",
            media=[shared_photo],
        )
        exporter.add_entry(
            entry_id="feed:parent-post",
            source="feed",
            kind="post",
            date="2024-01-02T09:00:00Z",
            author="Rainbow Nursery",
            text="We painted winter trees this week.",
            media=[shared_photo],
        )
        exporter.save()

        markdown = self.read_text("archive.md")
        self.assertIn("### This week", markdown)
        self.assertIn("We painted winter trees this week.", markdown)
        self.assertLess(markdown.index("### This week"), markdown.index("### Photos"))

        html = self.read_text("archive.html")
        self.assertIn('<section class="weekly-summary"><h3>This week</h3>', html)
        self.assertIn("We painted winter trees this week.", html)
        self.assertIn("Feed post from 02 January 2024, by Rainbow Nursery", html)
        self.assertNotIn("Feed post from 02 January 2024, 09:00", html)
        self.assertNotIn('data-filter="other"', html)
        self.assertEqual(html.count("We painted winter trees this week."), 1)
        self.assertNotIn('data-entry-category="other"', html)

        report = build_render_report(self.root / "archive.json")
        self.assertEqual(report["weekly_photos"]["tagged_photo_count"], 1)
        self.assertEqual(report["weekly_photos"]["weekly_card_count"], 1)
        self.assertEqual(report["parent_feed_posts"]["matching_post_count"], 1)
        self.assertEqual(report["photo_matching"]["unmatched_photo_count"], 0)
        report_path = write_render_report(self.root / "archive.json")
        self.assertTrue(report_path.exists())
        self.assertIn("parent_feed_posts", report_path.read_text())


if __name__ == "__main__":
    unittest.main()
