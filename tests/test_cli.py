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
        return [("child-riley", "Riley")]

    def get_parents_ids(self, child_id):
        return set()

    def download_tagged_images(self, child_id, first_name):
        self.calls.append(("tagged", child_id, first_name))

    def archive_parent_posts_for_tagged_photos(self):
        self.calls.append(("parent-text",))

    def save_archive(self):
        self.calls.append(("save",))


class TaggedPostTextTests(unittest.TestCase):
    def _invoke(self, *extra_args):
        with tempfile.TemporaryDirectory() as directory:
            with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
                return CliRunner().invoke(
                    main,
                    [
                        "--access-token",
                        "test-token",
                        "--export-text",
                        "--tagged-post-text",
                        "--pictures-folder",
                        str(Path(directory) / "pictures"),
                        *extra_args,
                    ],
                )

    def test_downloads_tagged_photos_before_matching_parent_post_text(self):
        result = self._invoke()

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            _Downloader.latest.calls,
            [
                ("tagged", "child-riley", "Riley"),
                ("parent-text",),
                ("save",),
            ],
        )

    def test_no_tagged_keeps_metadata_only_mode(self):
        result = self._invoke("--no-tagged")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            _Downloader.latest.calls,
            [("parent-text",), ("save",)],
        )


if __name__ == "__main__":
    unittest.main()
