"""Gate 3B bootstrap-fix regression tests (offline, source-level).

Verifies the six robustness changes that let the API start in environments
with partial optional dependencies, and that degradations are explicit:
1. app.py MCP addon guard (optional addon cannot brick startup)
2. routes/tool.py MCP discovery degrade-to-[] guard
3. mcp_tool_session.py pywin32 preload block present before the mcp import
4. pipecat_engine.py McpToolSession optional guard
5. service_factory.py optional Sarvam/Speechmatics SDK guards (503 at call time)
6. utils/win32_deps.py exists and is a no-op when the DLL dir is absent
"""
import inspect


def _source(module):
    return inspect.getsource(module)


def test_app_mcp_addon_is_guarded():
    from api import app

    src = _source(app)
    assert "mcp_app = None" in src
    assert "nullcontext()" in src
    assert "if mcp_app is not None:\n    app.mount" in src or "if mcp_app is not None:" in src


def test_tool_mcp_discovery_degrades_to_empty_list():
    from api.routes import tool

    src = _source(tool)
    assert "async def discover_mcp_tools(**kwargs) -> list:" in src
    assert "return []" in src


def test_mcp_tool_session_preloads_pywin32_before_mcp_import():
    # Source-structure check (environment-independent): the pywin32 preload
    # block must exist and run BEFORE the mcp import so optional dependency
    # gaps degrade instead of bricking startup.
    import pathlib

    p = (
        pathlib.Path(__file__).resolve().parents[1]
        / "services"
        / "workflow"
        / "mcp_tool_session.py"
    )
    src = p.read_text(encoding="utf-8")
    assert "pywin32_system32" in src
    assert "ExtensionFileLoader" in src
    assert src.index("pywin32_system32") < src.index(
        "from mcp.client.session_group"
    )


def test_pipecat_engine_mcp_session_guarded():
    from api.services.workflow import pipecat_engine

    src = _source(pipecat_engine)
    assert "McpToolSession = None" in src
    assert "MCP tool session unavailable" in src


def test_service_factory_optional_sdk_guards():
    from api.services.pipecat import service_factory

    src = _source(service_factory)
    assert "SarvamSTTService = SarvamSTTSettings = None" in src
    assert "SpeechmaticsSTTService = SpeechmaticsSTTSettings = None" in src
    assert 'detail="Sarvam provider SDK is not installed"' in src


def test_win32_deps_helper_is_safe_noop_without_dll_dir(monkeypatch):
    from types import SimpleNamespace

    from api.utils import win32_deps

    fake_path = SimpleNamespace(
        isdir=lambda p: False,
        abspath=lambda p: p,
        dirname=lambda p: "C:/fake",
        join=lambda *a: "C:/fake/x",
        exists=lambda p: False,
    )
    fake_os = SimpleNamespace(
        name="nt",
        path=fake_path,
        add_dll_directory=lambda d: None,
    )
    monkeypatch.setattr(win32_deps, "os", fake_os)
    win32_deps.ensure_pywin32()  # must not raise and must not register anything