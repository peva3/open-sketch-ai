# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds the three supex binaries.

Produces ``supex-chat`` (agent), ``supex-mcp`` (frozen MCP server) and
``supex`` (classic CLI) as standalone per-OS executables. The frozen agent
spawns the frozen ``supex-mcp`` sibling as its SketchUp backend (see
``supex_driver.agent.sketchup_mcp.resolve_backend_command``).

Build (from driver/):
    uv run --project . pyinstaller --noconfirm --clean packaging/supex.spec

Guide markdown and the chat UI are bundled as data so the binaries work with
no repo checkout. The guide copies must match docs/agents/guide (parity test
``tests/agent/test_guide_bundle.py`` enforces this; refresh with
``python -m supex_driver.agent.guide.sync``).
"""

import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Project layout: this spec lives in driver/packaging/, source under driver/src.
SRC = os.path.join(os.path.dirname(os.path.abspath(SPEC)), "..", "src")
ENTRIES = os.path.join(os.path.dirname(os.path.abspath(SPEC)), "entries")

DATAS = [
    # Guide markdown -> importlib.resources package dir (prompts.py BUNDLED_PACKAGE)
    (os.path.join(SRC, "supex_driver", "agent", "guide", "*.md"), "supex_driver/agent/guide"),
    # Chat UI -> next to server.py so Path(__file__).parent / "chat_ui" resolves
    (os.path.join(SRC, "supex_driver", "agent", "chat_ui", "index.html"), "supex_driver/agent/chat_ui"),
]

# Pull every supex_driver submodule (lazy imports, string-imported guide pkg)
HIDDEN = collect_submodules("supex_driver")


def _analysis(entry: str, name: str):
    return Analysis(
        [os.path.join(ENTRIES, entry)],
        pathex=[SRC],
        binaries=[],
        datas=DATAS,
        hiddenimports=HIDDEN,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )


# --- supex-chat (provider-agnostic agent + chat server) ---
a_chat = _analysis("supex_chat.py", "supex-chat")
pyz_chat = PYZ(a_chat.pure)
exe_chat = EXE(
    pyz_chat,
    a_chat.scripts,
    a_chat.binaries,
    a_chat.datas,
    [],
    name="supex-chat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
)

# --- supex-mcp (stdio MCP server; frozen sibling the agent spawns) ---
a_mcp = _analysis("supex_mcp.py", "supex-mcp")
pyz_mcp = PYZ(a_mcp.pure)
exe_mcp = EXE(
    pyz_mcp,
    a_mcp.scripts,
    a_mcp.binaries,
    a_mcp.datas,
    [],
    name="supex-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
)

# --- supex (classic CLI) ---
a_cli = _analysis("supex.py", "supex")
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.datas,
    [],
    name="supex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
)
