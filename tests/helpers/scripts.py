"""
tests/helpers/scripts.py

Import a module from ``scripts/`` by name. The scripts directory is not a package, so tests load the file directly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str) -> ModuleType:
    """Load ``scripts/<name>.py`` as a module called ``name`` (once per process)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look the module up here while the class body runs
    spec.loader.exec_module(module)
    return module
