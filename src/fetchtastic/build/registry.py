"""
Build module registry.
"""

from typing import Dict, List, Optional

from fetchtastic.build.base import GradleBuildModule
from fetchtastic.build.dfu.modules import DFUBuildModule

_MODULES: Dict[str, GradleBuildModule] = {
    "dfu": DFUBuildModule(),
}


def get_build_module(name: str) -> Optional[GradleBuildModule]:
    """
    Return a build module instance by name, or None if not found.
    """
    return _MODULES.get(name)


def list_build_modules() -> List[str]:
    """
    Return available build module names.
    """
    return sorted(_MODULES.keys())
