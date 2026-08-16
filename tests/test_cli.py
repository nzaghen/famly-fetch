import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from famly_fetch.cli import _authentication_challenge_resolver, main


class AuthenticationChallengeTests(unittest.TestCase):
    challenge = {
        "deviceId": "device-1",
        "loginId": "login-1",
        "expiresAt": 123456,
        "choices": [
            {
                "context": {
                    "id": "context-riley",
                    "target": {
                        "__typename": "PersonContextTarget",
                        "children": [
                            {"name": {"firstName": "Riley", "fullName": "Riley R."}}
                        ],
                    },
                },
                "hmac": "signed-riley",
                "requiresTwoFactor": True,
            },
            {
                "context": {
                    "id": "context-nursery",
                    "target": {
                        "__typename": "InstitutionSet",
                        "title": "Riverside Nursery",
                    },
                },
                "hmac": "signed-nursery",
                "requiresTwoFactor": False,
            },
        ],
    }

    def test_selects_context_and_submits_authenticator_code(self):
        resolver = _authentication_challenge_resolver("Riley R.", "012345", None)

        self.assertEqual(
            resolver(self.challenge),
            {
                "deviceId": "device-1",
                "loginId": "login-1",
                "expiresAt": 123456,
                "userContextId": "context-riley",
                "hmac": "signed-riley",
                "twoFactorCode": 12345,
                "recoveryCode": None,
            },
        )

    def test_recovery_code_is_used_instead_of_authenticator_code(self):
        resolver = _authentication_challenge_resolver(
            "context-riley", None, "recovery-code"
        )

        answer = resolver(self.challenge)

        self.assertIsNone(answer["twoFactorCode"])
        self.assertEqual(answer["recoveryCode"], "recovery-code")

    def test_supplied_authenticator_code_is_not_reused_after_reauthentication(self):
        resolver = _authentication_challenge_resolver("context-riley", "012345", None)

        first_answer = resolver(self.challenge)
        with patch("famly_fetch.cli.click.prompt", return_value="654321") as prompt:
            second_answer = resolver(self.challenge)

        self.assertEqual(first_answer["twoFactorCode"], 12345)
        self.assertEqual(second_answer["twoFactorCode"], 654321)
        prompt.assert_called_once()


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
    def test_two_factor_and_recovery_codes_are_mutually_exclusive(self):
        result = CliRunner().invoke(
            main,
            [
                "--two-factor-code",
                "123456",
                "--recovery-code",
                "recovery-code",
            ],
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("cannot be used together", result.output)

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
                ("tagged", "child-rowan", "Rowan"),
                ("journey", "child-rowan", "Rowan"),
                ("state",),
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
                ("tagged", "child-riley", "Riley"),
                ("parent-text", [("child-riley", "Riley")]),
                ("state",),
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
        self.assertNotIn(("tagged", "child-riley", "Riley"), _Downloader.latest.calls)
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

    def test_relations_are_not_requested_without_liked_feed(self):
        with patch("famly_fetch.cli.FamlyDownloader", _Downloader):
            result = CliRunner().invoke(
                main,
                ["--access-token", "test-token", "--child", "Riley"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(any(call[0] == "parents" for call in _Downloader.latest.calls))

    def test_runtime_failure_returns_nonzero_exit_status(self):
        class _RejectedDownloader:
            def __init__(self, **kwargs):
                raise RuntimeError("Rejected synthetic request")

        with patch("famly_fetch.cli.FamlyDownloader", _RejectedDownloader):
            result = CliRunner().invoke(
                main,
                ["--access-token", "test-token"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Rejected synthetic request", result.output)

    def test_download_failure_still_saves_partial_state_and_archive(self):
        class _DownloadRejectedDownloader(_Downloader):
            def download_tagged_images(self, child_id, first_name):
                self.calls.append(("tagged", child_id, first_name))
                raise RuntimeError("Rejected synthetic download")

        with patch("famly_fetch.cli.FamlyDownloader", _DownloadRejectedDownloader):
            result = CliRunner().invoke(
                main,
                ["--access-token", "test-token", "--child", "Riley"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Rejected synthetic download", result.output)
        self.assertEqual(
            _Downloader.latest.calls,
            [
                ("archive-children", [("child-riley", "Riley")]),
                ("tagged", "child-riley", "Riley"),
                ("state",),
                ("save",),
            ],
        )

    def test_archive_save_failure_returns_nonzero_exit_status(self):
        class _SaveRejectedDownloader(_Downloader):
            def save_archive(self):
                raise RuntimeError("Rejected synthetic archive save")

        with patch("famly_fetch.cli.FamlyDownloader", _SaveRejectedDownloader):
            result = CliRunner().invoke(
                main,
                ["--access-token", "test-token", "--child", "Riley"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Rejected synthetic archive save", result.output)


if __name__ == "__main__":
    unittest.main()
