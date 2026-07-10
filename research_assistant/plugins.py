from __future__ import annotations

import importlib
from typing import Iterable

from .registry import ToolRegistry


class PluginError(RuntimeError):
    pass


def load_plugins(registry: ToolRegistry, module_names: Iterable[str]) -> None:
    """Load only developer-configured plugins; the model cannot install plugins."""
    for module_name in module_names:
        module = importlib.import_module(module_name)
        register = getattr(module, "register_tools", None)
        if not callable(register):
            raise PluginError(f"Plugin {module_name!r} must expose register_tools(registry).")
        register(registry)
