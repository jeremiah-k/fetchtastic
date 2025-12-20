"""
Configuration Utilities for Fetchtastic Download Subsystem

This module provides configuration-related utilities that were previously
in the monolithic downloader but are needed by the new modular architecture.
"""

from typing import Any, Dict, List

from fetchtastic.log_utils import logger


def _get_string_list_from_config(config: Dict[str, Any], key: str) -> List[str]:
    """
    Extract a list of strings from the given configuration key.

    Parameters:
        config (Dict[str, Any]): Configuration mapping to read the value from.
        key (str): The configuration key whose value should be extracted.

    Returns:
        List[str]: A list of strings derived from the configuration value:
            - empty list if the key is missing or the value is falsy,
            - if the value is a list, each item converted to a string,
            - otherwise a single-element list containing the stringified value.
    """
    value = config.get(key)
    if not value:
        return []

    if isinstance(value, list):
        return [str(item) for item in value]

    return [str(value)]


def get_prerelease_patterns(config: Dict[str, Any]) -> List[str]:
    """
    Get file-selection patterns used to identify prerelease assets.

    Prefers `SELECTED_PRERELEASE_ASSETS` key in `config`; if absent, falls back to legacy
    `EXTRACT_PATTERNS` key and emits a deprecation warning. Always returns a list (empty if no
    patterns are configured).

    Parameters:
        config (dict): Configuration mapping that may contain `SELECTED_PRERELEASE_ASSETS` or
            legacy `EXTRACT_PATTERNS` key.

    Returns:
        list[str]: The list of prerelease asset selection patterns.
    """
    # Check for new dedicated configuration key first
    if "SELECTED_PRERELEASE_ASSETS" in config:
        return _get_string_list_from_config(config, "SELECTED_PRERELEASE_ASSETS")

    # Fall back to EXTRACT_PATTERNS for backward compatibility
    extract_patterns = _get_string_list_from_config(config, "EXTRACT_PATTERNS")
    if extract_patterns:
        logger.warning(
            "Using EXTRACT_PATTERNS for prerelease file selection is deprecated. "
            "Please re-run 'fetchtastic setup' to update your configuration."
        )

    return extract_patterns
