"""Pre-load pywin32 DLL modules for environments without a system-wide install.

mcp's Windows helpers import ``pywintypes`` directly. pywin32 ships its DLLs
in a ``pywin32_system32`` directory that is only importable after its
post-install bootstrap. This helper performs that bootstrap in-process from
the candidate-local dependency directory (``.deps``), so the protected venv
and the system are never modified.
"""
import importlib.util
import os
import sys
from importlib.machinery import ExtensionFileLoader


def ensure_pywin32() -> None:
    if "pywintypes" in sys.modules:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    # <workspace>\.deps\pywin32_system32  (api\utils -> workspace root)
    dll_dir = os.path.abspath(
        os.path.join(here, "..", "..", ".deps", "pywin32_system32")
    )
    if not os.path.isdir(dll_dir):
        return
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(dll_dir)
    suffix = "3%d%d.dll" % sys.version_info[:2]
    for name in ("pywintypes", "pythoncom"):
        path = os.path.join(dll_dir, name + suffix)
        if not os.path.exists(path):
            continue
        try:
            loader = ExtensionFileLoader(name, path)
            spec = importlib.util.spec_from_loader(name, loader)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            loader.exec_module(mod)
        except Exception:
            pass


if os.name == "nt":
    ensure_pywin32()
