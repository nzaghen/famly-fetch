"""
famly-fetch - A tool to fetch your kid's images from famly.co
"""

__all__ = ["ApiClient", "FamlyDownloader"]


def __getattr__(name):
    """Load the network downloader only when one of its public classes is used."""

    if name == "ApiClient":
        from .api_client import ApiClient

        return ApiClient
    if name == "FamlyDownloader":
        from .downloader import FamlyDownloader

        return FamlyDownloader
    raise AttributeError(name)
