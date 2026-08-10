import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from famly_fetch.cli import main


class _Downloader:
    latest = None

    def __init__(self, **kwargs):
        self.calls = []
        _Downloader.latest = self

    def get_all_children(self):
        return [("child-riley", "Riley"), ("child-rowan", "Rowan")]

    def get_parents_ids(self, child_id):
        self.calls.append(("parents", child_id))
        return set()

    def download_tagged_images(self, child_id, first_name):
        self.calls.append(("tagged", child_id, first_name))

    def download_images_from_learning_journey(self, child_id, first_name):
        self.calls.append(("journey", child_id, first_name))

    def archive_parent_posts_for_tagged_photos(self, selected_children=None):
        self.calls.append(("parent-text", selected_children))

    def save_state(self):
        self.calls.append(("state",))

    def save_archive(self):
        self.calls.append(("save",))

    def set_archive_children(self, children):
        self.calls.append(("archive-children", children))


class ChildSelectionTests(unittest.TestCase):
    def test_no_child_selector_persists_the_combined_archive_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                result = CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--no-tagged",
                        "--pictures-folder",
                        str(Path(directory) / "pictures-combined"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            (
                "archive-children",
                [("child-riley", "Riley"), ("child-rowan", "Rowan")],
            ),
            _Downloader.latest.calls,
        )

    def test_child_name_selects_only_that_child_case_insensitively(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                result = CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--child",
                        "rowan",
                        "--journey",
                        "--export-text",
                        "--pictures-folder",
                        str(Path(directory) / "pictures-rowan"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            _Downloader.latest.calls,
            [
                ("archive-children", [("child-rowan", "Rowan")]),
                ("parents", "child-rowan"),
                ("tagged", "child-rowan", "Rowan"),
                ("journey", "child-rowan", "Rowan"),
                ("save",),
            ],
        )

    def test_child_downloads_tagged_photos_before_parent_post_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                result = CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--child",
                        "Riley",
                        "--export-text",
                        "--tagged-post-text",
                        "--pictures-folder",
                        str(Path(directory) / "pictures-riley"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            _Downloader.latest.calls,
            [
                ("archive-children", [("child-riley", "Riley")]),
                ("parents", "child-riley"),
                ("tagged", "child-riley", "Riley"),
                ("parent-text", [("child-riley", "Riley")]),
                ("save",),
            ],
        )

    def test_no_tagged_keeps_parent_post_enrichment_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                result = CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--child",
                        "Riley",
                        "--no-tagged",
                        "--export-text",
                        "--tagged-post-text",
                        "--pictures-folder",
                        str(Path(directory) / "pictures-riley"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn(
            ("tagged", "child-riley", "Riley"), _Downloader.latest.calls
        )
        self.assertIn(
            ("parent-text", [("child-riley", "Riley")]),
            _Downloader.latest.calls,
        )

    def test_child_id_selects_only_that_child(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                result = CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--child",
                        "child-riley",
                        "--pictures-folder",
                        str(Path(directory) / "pictures-riley"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(("tagged", "child-riley", "Riley"), _Downloader.latest.calls)
        self.assertNotIn(("tagged", "child-rowan", "Rowan"), _Downloader.latest.calls)

    def test_child_rejects_sources_that_are_not_child_specific(self):
        result = CliRunner().invoke(
            main,
            ["--access-token", "test-token", "--child", "Riley", "--feed"],
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn(
            "cannot be combined with --messages, --liked, or --feed", result.output
        )

    def test_unknown_child_lists_available_children(self):
        with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
            result = CliRunner().invoke(
                main,
                ["--access-token", "test-token", "--child", "Remy"],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn('No child matched "Remy"', result.output)
        self.assertIn("Riley (child-riley)", result.output)
        self.assertIn("Rowan (child-rowan)", result.output)


if __name__ == "__main__":
    unittest.main()
