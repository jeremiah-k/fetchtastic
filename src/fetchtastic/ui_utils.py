# src/fetchtastic/ui_utils.py

"""
UI utilities for consistent questionary-based user interfaces.
Provides common styling and helper functions for prompts.
"""

from typing import Any, Dict, List, Optional

import questionary
from questionary import Style

# Define consistent styling for all prompts using clean color scheme
# Primary: #2C2D3C (dark blue-gray), Secondary: #67EA94 (mint green)
FETCHTASTIC_STYLE = Style(
    [
        (
            "qmark",
            "fg:#67ea94 bold",
        ),  # token in front of the question - mint green
        ("question", "fg:#2c2d3c bold"),  # question text - dark blue-gray
        ("answer", "fg:#67ea94 bold"),  # submitted answer text - mint green
        (
            "pointer",
            "fg:#67ea94 bold",
        ),  # pointer used in select and checkbox prompts - mint green
        (
            "highlighted",
            "fg:#67ea94 bold",
        ),  # pointed-at choice in select and checkbox prompts - mint green
        (
            "selected",
            "fg:#67ea94",
        ),  # style for a selected item of a checkbox - mint green
        ("separator", "fg:#888888"),  # separator in lists - gray
        ("instruction", "fg:#888888 italic"),  # user instructions - gray italic
        ("text", ""),  # plain text
        (
            "disabled",
            "fg:#858585 italic",
        ),  # disabled choices for select and checkbox prompts
    ]
)


def multi_select_with_preselection(
    message: str,
    choices: List[str],
    preselected: Optional[List[str]] = None,
    min_selection: int = 0,
) -> Optional[List[str]]:
    """
    Create a multi-select checkbox prompt with preselection support.

    Args:
        message: The prompt message to display
        choices: List of available choices
        preselected: List of choices that should be preselected
        min_selection: Minimum number of selections required (0 for optional)

    Returns:
        List of selected choices, or None if cancelled/no selection
    """
    if not choices:
        return None

    # Handle preselection - use Choice objects with checked property
    try:
        from questionary import Choice

        choice_objects = []
        for choice in choices:
            is_checked = choice in (preselected or [])
            choice_objects.append(Choice(choice, checked=is_checked))

        selected = questionary.checkbox(
            message,
            choices=choice_objects,
            style=FETCHTASTIC_STYLE,
            instruction="(Use arrow keys to move, space to select)",
        ).ask()

        # Handle cancellation (Ctrl+C returns None)
        if selected is None:
            return None

        # Check minimum selection requirement
        if min_selection > 0 and len(selected) < min_selection:
            print(f"Please select at least {min_selection} item(s).")
            return None

        return selected

    except KeyboardInterrupt:
        return None


def single_select(
    message: str, choices: List[str], default: Optional[str] = None
) -> Optional[str]:
    """
    Create a single-select prompt.

    Args:
        message: The prompt message to display
        choices: List of available choices
        default: Default choice to highlight

    Returns:
        Selected choice, or None if cancelled
    """
    if not choices:
        return None

    try:
        selected = questionary.select(
            message,
            choices=choices,
            default=default,
            style=FETCHTASTIC_STYLE,
            instruction="(Use arrow keys to move, enter to select)",
        ).ask()

        return selected

    except KeyboardInterrupt:
        return None


def confirm_prompt(message: str, default: bool = True) -> Optional[bool]:
    """
    Create a confirmation prompt.

    Args:
        message: The prompt message to display
        default: Default value (True/False)

    Returns:
        Boolean response, or None if cancelled
    """
    try:
        result = questionary.confirm(
            message, default=default, style=FETCHTASTIC_STYLE
        ).ask()

        return result

    except KeyboardInterrupt:
        return None


def text_input(
    message: str, default: str = "", validate: Optional[Any] = None
) -> Optional[str]:
    """
    Create a text input prompt.

    Args:
        message: The prompt message to display
        default: Default text value
        validate: Validation function or validator class

    Returns:
        Input text, or None if cancelled
    """
    try:
        result = questionary.text(
            message, default=default, validate=validate, style=FETCHTASTIC_STYLE
        ).ask()

        return result

    except KeyboardInterrupt:
        return None


def show_preselection_info(preselected: List[str]) -> None:
    """
    Display information about preselected items.

    Args:
        preselected: List of preselected items to display
    """
    if preselected:
        print(
            f"Previously selected items will be preselected: {', '.join(preselected)}"
        )
        print()
