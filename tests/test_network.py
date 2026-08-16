import unittest
import urllib.request
from unittest.mock import Mock, patch

from famly_fetch.network import (
    NetworkPolicyError,
    _ValidatingRedirectHandler,
    open_famly_api,
    validate_api_base_url,
    validate_api_url,
    validate_media_url,
)


class NetworkPolicyTests(unittest.TestCase):
    def test_api_credentials_can_only_target_supported_origins(self):
        self.assertEqual(
            validate_api_base_url("https://app.famly.co/"),
            "https://app.famly.co",
        )
        self.assertEqual(
            validate_api_base_url("https://familyapp.brighthorizons.co.uk/"),
            "https://famlyapi.familyapp.brighthorizons.co.uk",
        )
        self.assertEqual(
            validate_api_base_url("https://famlyapi.familyapp.brighthorizons.co.uk/"),
            "https://famlyapi.familyapp.brighthorizons.co.uk",
        )
        validate_api_url("https://app.famly.co/graphql?Authenticate")
        validate_api_url(
            "https://famlyapi.familyapp.brighthorizons.co.uk/graphql?Authenticate"
        )

        blocked = [
            "http://app.famly.co",
            "https://evil.example",
            "https://app.famly.co.evil.example",
            "https://app.famly.co:444",
            "https://user:password@app.famly.co",
            "https://img.famly.co",
            "https://app.famly.co/unexpected-base-path",
            "https://other.brighthorizons.co.uk",
            "https://familyapp.brighthorizons.co.uk.evil.example",
        ]
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(NetworkPolicyError):
                validate_api_base_url(url)

    def test_media_allows_only_explicit_https_hosts(self):
        allowed = [
            "https://img.famly.co/image.jpg",
            "https://static.famly.co/file.pdf",
            "https://famly.co/file",
            "https://img.familyapp.brighthorizons.co.uk/image.jpg",
        ]
        for url in allowed:
            with self.subTest(url=url):
                self.assertEqual(validate_media_url(url), url)

        blocked = [
            "http://img.famly.co/image.jpg",
            "https://famly.co.evil.example/image.jpg",
            "https://examplefamly.co/image.jpg",
            "https://bucket.s3.amazonaws.com/image.jpg",
            "https://example.cloudfront.net/image.jpg",
            "https://img.famly.co:8443/image.jpg",
            "https://cdn.brighthorizons.co.uk/image.jpg",
            "https://img.familyapp.brighthorizons.co.uk.evil.example/image.jpg",
        ]
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(NetworkPolicyError):
                validate_media_url(url)

    def test_redirect_is_blocked_before_request_creation(self):
        handler = _ValidatingRedirectHandler(validate_media_url)
        original_request = urllib.request.Request("https://img.famly.co/image.jpg")

        with self.assertRaises(NetworkPolicyError):
            handler.redirect_request(
                original_request,
                None,
                302,
                "Found",
                {},
                "https://tracking.example/image.jpg",
            )

    @patch("famly_fetch.network.urllib.request.build_opener")
    def test_final_response_url_is_revalidated_and_closed(self, build_opener):
        response = Mock()
        response.geturl.return_value = "https://tracking.example/redirected"
        opener = Mock()
        opener.open.return_value = response
        build_opener.return_value = opener
        request = urllib.request.Request("https://app.famly.co/api/me/me/me")

        with self.assertRaises(NetworkPolicyError):
            open_famly_api(request)

        response.close.assert_called_once_with()

    @patch("famly_fetch.network.urllib.request.build_opener")
    def test_proxy_environment_is_disabled(self, build_opener):
        response = Mock()
        response.geturl.return_value = "https://app.famly.co/api/me/me/me"
        build_opener.return_value.open.return_value = response
        request = urllib.request.Request("https://app.famly.co/api/me/me/me")

        self.assertIs(open_famly_api(request), response)
        handlers = build_opener.call_args.args
        proxy_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        )
        self.assertEqual(proxy_handler.proxies, {})


if __name__ == "__main__":
    unittest.main()
