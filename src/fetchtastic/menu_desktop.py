"""Legacy imports for the shared client app asset selector."""

from .menu_app import fetch_app_assets as fetch_desktop_assets
from .menu_app import run_menu, select_assets

__all__ = ["fetch_desktop_assets", "run_menu", "select_assets"]
