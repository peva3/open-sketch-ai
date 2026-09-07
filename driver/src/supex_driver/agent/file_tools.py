"""Workspace file tools for the agent, with Python-side path containment.

The Supex Ruby runtime lets an agent run Ruby inside SketchUp
(``eval_ruby_file``), and relative paths there resolve against the workspace
handed over in the ``hello`` handshake. So the agent's own file tools must
enforce the same boundary in Python: every path is confined to
``SUPEX_WORKSPACE`` plus ``SUPEX_ALLOWED_ROOTS``, with symlinks resolved so a
workspace link cannot be used to escape (mirrors ``runtime/path_policy.rb``).

``SUPEX_ALLOWED_ROOTS`` is split with :data:`os.pathsep` (``;`` on Windows,
``:`` on POSIX) so Windows drive letters survive; the value ``*`` disables
containment, exactly as in the runtime guardrail.

Tool *problems* (missing file, missing match, binary payload) are returned
as compact JSON so the model can read and react; containment violations
raise :class:`PathNotAllowedError` so the safety boundary is unmistakable.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path

from supex_driver.agent.errors import FileToolError, PathNotAllowedError
from supex_driver.agent.providers.base import ToolSchema

_READ_CAP_BYTES = 1_048_576  # 1 MiB, mirrors SUPEX_MAX_RESPONSE spirit


def default_workspace() -> Path:
    """Workspace root: ``SUPEX_WORKSPACE`` env or the current directory."""
    return Path(os.environ.get("SUPEX_WORKSPACE") or Path.cwd()).resolve()


def split_allowed_roots(value: str | None) -> list[str]:
    """Split ``SUPEX_ALLOWED_ROOTS`` on :data:`os.pathsep`."""
    if not value:
        return []
    return [part for part in value.split(os.pathsep) if part]


class FileTools:
    """File-tool backend confined to a workspace plus allowed roots."""

    TOOL_NAMES = ("read_file", "write_file", "edit_file", "list_dir", "delete_file")

    def __init__(
        self,
        workspace: str | Path | None = None,
        allowed_roots: str | None = None,
        *,
        allow_delete: bool = False,
        allow_all: bool = False,
    ) -> None:
        if workspace is None:
            workspace = default_workspace()
        self._workspace = Path(workspace).resolve()
        allowed = (
            os.environ.get("SUPEX_ALLOWED_ROOTS")
            if allowed_roots is None
            else allowed_roots
        )
        if allow_all or (allowed or "").strip() == "*":
            self._allow_all = True
            self._roots = [self._workspace]
        else:
            self._allow_all = False
            roots = [self._workspace]
            roots.extend(Path(r).resolve() for r in split_allowed_roots(allowed))
            self._roots = roots
        self._allow_delete = allow_delete

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def resolve(self, raw: str | Path) -> Path:
        """Resolve ``raw`` against the workspace and enforce containment."""
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self._workspace / path
        resolved = path.resolve()  # symlink-aware
        if self._allow_all:
            return resolved
        for root in self._roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        raise PathNotAllowedError(
            f"path {str(path)!r} resolves outside allowed roots "
            f"({', '.join(str(r) for r in self._roots)})"
        )

    def _rel(self, resolved: Path) -> str:
        try:
            return str(resolved.relative_to(self._workspace))
        except ValueError:
            return str(resolved)

    def _json(self, payload: dict) -> str:
        return json.dumps(payload, separators=(",", ":"))

    def schemas(self) -> list[ToolSchema]:
        """Internal schemas for the five file tools."""
        string_prop = {"type": "string"}
        return [
            ToolSchema(
                name="read_file",
                description=(
                    "Read a UTF-8 text file inside the workspace. Returns file "
                    "content, or a JSON notice when the file is binary."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"path": string_prop},
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="write_file",
                description="Create or overwrite a UTF-8 text file inside the workspace.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": string_prop,
                        "content": string_prop,
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolSchema(
                name="edit_file",
                description=(
                    "Replace one occurrence of old_text with new_text in a UTF-8 "
                    "file inside the workspace (set replace_all=true for every "
                    "occurrence)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": string_prop,
                        "old_text": string_prop,
                        "new_text": string_prop,
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            ),
            ToolSchema(
                name="list_dir",
                description="List entries of a directory inside the workspace as JSON.",
                input_schema={
                    "type": "object",
                    "properties": {"path": string_prop},
                    "required": [],
                },
            ),
            ToolSchema(
                name="delete_file",
                description="Delete a file inside the workspace. Disabled unless enabled.",
                input_schema={
                    "type": "object",
                    "properties": {"path": string_prop},
                    "required": ["path"],
                },
            ),
        ]

    async def call(self, name: str, arguments: dict) -> str:
        """Dispatch a file-tool call to its implementation."""
        if name not in self.TOOL_NAMES:
            raise FileToolError(f"unknown file tool {name!r}")
        impl = getattr(self, name)
        try:
            return await impl(arguments)
        except (PathNotAllowedError, FileToolError):
            raise
        except OSError as exc:
            return self._json({"ok": False, "error": f"{exc.strerror or exc}"})

    async def read_file(self, arguments: dict) -> str:
        resolved = self.resolve(arguments["path"])
        data = resolved.read_bytes()
        if len(data) > _READ_CAP_BYTES:
            return self._json(
                {
                    "ok": False,
                    "error": (
                        f"file {self._rel(resolved)!r} is {len(data)} bytes; "
                        f"only the first {_READ_CAP_BYTES} are readable"
                    ),
                }
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            media = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            return self._json(
                {
                    "binary": True,
                    "ok": True,
                    "path": self._rel(resolved),
                    "media_type": media,
                    "size": len(data),
                    "hint": "binary file; not readable as text",
                }
            )

    async def write_file(self, arguments: dict) -> str:
        resolved = self.resolve(arguments["path"])
        content = str(arguments["content"])
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content.encode("utf-8"))
        return self._json(
            {"ok": True, "path": self._rel(resolved), "bytes": len(content)}
        )

    async def edit_file(self, arguments: dict) -> str:
        resolved = self.resolve(arguments["path"])
        old_text = str(arguments["old_text"])
        new_text = str(arguments["new_text"])
        replace_all = bool(arguments.get("replace_all"))
        data = resolved.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return self._json({"ok": False, "error": "file is not UTF-8 text"})
        if old_text not in text:
            return self._json(
                {"ok": False, "error": f"old_text not found in {self._rel(resolved)!r}"}
            )
        replaced = text.count(old_text) if replace_all else 1
        text = (
            text.replace(old_text, new_text)
            if replace_all
            else text.replace(old_text, new_text, 1)
        )
        resolved.write_bytes(text.encode("utf-8"))
        return self._json(
            {"ok": True, "path": self._rel(resolved), "replaced": replaced}
        )

    async def list_dir(self, arguments: dict) -> str:
        raw = str(arguments.get("path") or ".")
        resolved = self.resolve(raw)
        if not resolved.is_dir():
            return self._json(
                {"ok": False, "error": f"{self._rel(resolved)!r} is not a directory"}
            )
        entries: list[dict] = []
        try:
            children = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            return self._json({"ok": False, "error": str(exc)})
        for child in children:
            try:
                stat = child.stat()
                kind = "dir" if child.is_dir() else "file"
                entries.append(
                    {
                        "name": self._rel(child),
                        "type": kind,
                        "size": stat.st_size if kind == "file" else None,
                    }
                )
            except OSError:
                continue
        return self._json({"ok": True, "path": self._rel(resolved), "entries": entries})

    async def delete_file(self, arguments: dict) -> str:
        if not self._allow_delete:
            return self._json(
                {"ok": False, "error": "delete_file is disabled (opt in via CLI)"}
            )
        resolved = self.resolve(arguments["path"])
        if resolved == self._workspace or resolved in self._roots:
            raise PathNotAllowedError(
                f"refusing to delete a configured root {str(resolved)!r}"
            )
        if resolved.is_dir():
            raise FileToolError(f"{self._rel(resolved)!r} is a directory; refusing")
        resolved.unlink(missing_ok=True)
        return self._json({"ok": True, "deleted": self._rel(resolved)})
