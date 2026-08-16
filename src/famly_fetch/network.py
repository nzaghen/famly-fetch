"""Strict outbound-network policy for famly-fetch."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse


OFFICIAL_FAMLY_API_BASE = "https://app.famly.co"
FAMLY_DOMAIN = "famly.co"
BRIGHT_HORIZONS_APP_HOST = "familyapp.brighthorizons.co.uk"
BRIGHT_HORIZONS_API_HOST = "famlyapi.familyapp.brighthorizons.co.uk"
BRIGHT_HORIZONS_API_BASE = f"https://{BRIGHT_HORIZONS_API_HOST}"
BRIGHT_HORIZONS_MEDIA_HOST = "img.familyapp.brighthorizons.co.uk"
BRIGHT_HORIZONS_VIDEO_HOST = (
    "brighthorizons-video-storage.s3.eu-central-1.amazonaws.com"
)
BRIGHT_HORIZONS_MEDIA_HOSTS = {
    BRIGHT_HORIZONS_MEDIA_HOST,
    BRIGHT_HORIZONS_VIDEO_HOST,
}

API_BASES_BY_HOST = {
    "app.famly.co": OFFICIAL_FAMLY_API_BASE,
    BRIGHT_HORIZONS_APP_HOST: BRIGHT_HORIZONS_API_BASE,
    BRIGHT_HORIZONS_API_HOST: BRIGHT_HORIZONS_API_BASE,
}
API_HOSTS = {"app.famly.co", BRIGHT_HORIZONS_API_HOST}


class NetworkPolicyError(RuntimeError):
    """Raised before a request can leave the process for an unapproved host."""


def _url_parts(url: str, purpose: str):
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError as error:
        raise NetworkPolicyError(f"Blocked malformed {purpose} URL") from error

    hostname = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https":
        raise NetworkPolicyError(f"Blocked non-HTTPS {purpose} URL")
    if not hostname:
        raise NetworkPolicyError(f"Blocked {purpose} URL with no hostname")
    if parts.username is not None or parts.password is not None:
        raise NetworkPolicyError(f"Blocked {purpose} URL containing user information")
    if port not in (None, 443):
        raise NetworkPolicyError(f"Blocked {purpose} URL using port {port}")
    return parts, hostname


def validate_api_base_url(url: str) -> str:
    """Accept and normalize supported Famly application and API origins."""

    parts, hostname = _url_parts(url, "Famly API")
    normalized_base = API_BASES_BY_HOST.get(hostname)
    if normalized_base is None:
        raise NetworkPolicyError(f"Blocked unsupported API host: {hostname}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise NetworkPolicyError("Famly API base URL must not contain a path or query")
    return normalized_base


def validate_api_url(url: str) -> str:
    parts, hostname = _url_parts(url, "Famly API")
    if hostname not in API_HOSTS:
        raise NetworkPolicyError(f"Blocked unsupported API host: {hostname}")
    if parts.fragment:
        raise NetworkPolicyError("Blocked Famly API URL containing a fragment")
    return url


def validate_media_url(url: str) -> str:
    """Allow HTTPS media only on Famly-owned domains."""

    parts, hostname = _url_parts(url, "Famly media")
    is_famly_host = hostname == FAMLY_DOMAIN or hostname.endswith(f".{FAMLY_DOMAIN}")
    if not is_famly_host and hostname not in BRIGHT_HORIZONS_MEDIA_HOSTS:
        raise NetworkPolicyError(f"Blocked unsupported media host: {hostname}")
    if parts.fragment:
        raise NetworkPolicyError("Blocked Famly media URL containing a fragment")
    return url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, validator: Callable[[str], str]):
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_validated(
    request: urllib.request.Request,
    validator: Callable[[str], str],
    timeout: float = 60,
):
    validator(request.full_url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _ValidatingRedirectHandler(validator),
    )
    response = opener.open(request, timeout=timeout)
    try:
        validator(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def open_famly_api(request: urllib.request.Request, timeout: float = 60):
    return _open_validated(request, validate_api_url, timeout)


def open_famly_media(request: urllib.request.Request, timeout: float = 120):
    return _open_validated(request, validate_media_url, timeout)
