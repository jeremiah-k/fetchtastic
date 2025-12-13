"""
Legacy compatibility layer for `fetchtastic.downloaders.core`.

These symbols are implemented in the legacy monolithic module and are retained
here to avoid breaking older import paths during the refactor.
"""

from fetchtastic.downloader import (  # noqa: F401
    _atomic_write,
    _cleanup_apk_prereleases,
    _process_apk_downloads,
    cleanup_superseded_prereleases,
    compare_file_hashes,
)

__all__ = [
    "_atomic_write",
    "_cleanup_apk_prereleases",
    "_process_apk_downloads",
    "cleanup_superseded_prereleases",
    "compare_file_hashes",
]
