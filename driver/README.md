# Supex Driver

Python MCP server and CLI for SketchUp automation. Enables AI agents and command-line tools to execute Ruby code in SketchUp through the Model Context Protocol.

## Requirements

- Python 3.14 or later
- SketchUp with Supex Runtime extension installed

## Overview

Supex Driver is part of the Supex platform - a bridge between AI agents and SketchUp. It provides:

- **MCP Server**: 27 tools for AI agents via Model Context Protocol
- **CLI**: 14 commands for direct terminal interaction
- **Connection Layer**: TCP/JSON-RPC client for SketchUp runtime

## Supex Chat Agent

`driver/src/supex_driver/agent/` adds `supex-chat` (console script), a provider-agnostic agent that drives SketchUp through the same MCP server Claude Code uses. It speaks OpenAI- and Anthropic-compatible APIs (including local servers such as Unsloth Desktop) configured via `base_url` + `api_key` + `model`. See `docs/agent.md` for the full reference.

### Intended workflow

You type a prompt into a chat window; whatever you ask for is applied **live inside the running SketchUp application** — the agent authors Ruby (`.rb`) or VCAD (`.cmp.oo`) sources in the workspace and runs them in-process via `eval_ruby_file`, so geometry appears in SketchUp with no import/export round-trips. The model then **screenshots its own output**; when vision is enabled, the agent reads the PNG and attaches it to the next model turn so the model can verify and self-correct.

### Interface roadmap

- **Windowed chat app (first):** the agent runs as a persistent local server (`supex-chat serve`) serving a dependency-free chat UI on `127.0.0.1` — open it in the default browser, or later embed the same URL inside SketchUp via `UI::HtmlDialog`.
- **Terminal CLI (peer interface):** interactive `supex-chat` session or `--prompt` one-shot.
- Both share a single `Agent` loop + event stream (`driver/src/supex_driver/agent/agent.py`), so behavior is identical across windows and terminals.
- Tracked in `TODO.md` Phases 4-5 (windowed app + CLI); in-SketchUp embedding is a staged additive milestone (`TODO.md` T-4.7).

### Supported platforms

The agent runs on **Windows, Linux, and macOS**. It is delivered as **self-contained per-OS binaries** (PyInstaller) so no Python, `uv`, or repository clone is required on the target machine. Distribution implications:

- The canonical entry point is the `supex-chat` console script / binary; the repo-root `sketch` wrapper is a Unix convenience only.
- When running the agent from source, the SketchUp MCP backend is spawned as `python -m supex_driver.mcp` (same interpreter). When running an installed/frozen binary, a sibling `supex-mcp` executable is used. The repo `./mcp` bash wrapper is never assumed.
- Agent guide content (`docs/agents/guide/*.md`) is bundled as package data so prompts resolve without a repo checkout.
- See `driver/packaging/` (recipe: `uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/supex.spec`, run per target OS) and `docs/agent.md` **Install** / **Build from source** for full instructions.

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPEX_HOST` | `localhost` | SketchUp runtime host |
| `SUPEX_PORT` | `9876` | SketchUp runtime port |
| `SUPEX_TIMEOUT` | `15.0` | Socket timeout in seconds |
| `SUPEX_RETRIES` | `2` | Max retry attempts |
| `SUPEX_AUTH_TOKEN` | not set | Token sent in the `hello` handshake when the runtime requires one |
| `SUPEX_WORKSPACE` | see below | Project directory; relative paths in file tools resolve against it |
| `SUPEX_LOG_DIR` | `$SUPEX_WORKSPACE/.tmp/logs` | Log file directory |
| `SUPEX_AGENT` | `user`/`mcp` | Agent identifier |

The `./supex` wrapper defaults `SUPEX_WORKSPACE` to `~/.supex/tmp-workspace`, the `./mcp` wrapper to the current directory.

## MCP Tools

### Execution

| Tool | Description |
|------|-------------|
| `eval_ruby(code)` | Execute Ruby code directly |
| `eval_ruby_file(file_path)` | Execute Ruby script from file (preferred); relative paths resolve against the workspace |

### Introspection

| Tool | Description |
|------|-------------|
| `get_model_info()` | Entity counts, units, modified state |
| `list_entities(type)` | List geometry (all/faces/edges/groups/components) |
| `get_entity(entity_id)` | Full state of one entity: bounds, dimensions (inches), layer, material, hidden/visible/locked; transformation, origin and definition for groups and component instances |
| `get_selection()` | Currently selected entities |
| `get_layers()` | All layers/tags |
| `get_materials()` | All materials with colors |
| `get_camera_info()` | Camera position and settings |
| `take_screenshot(width?, height?, transparent?, output_path?)` | Save view to PNG (default 1920x1080, `.tmp/screenshots/`); returns the file path |
| `take_batch_screenshots(shots)` | Multiple screenshots with camera control |

### Model Management

| Tool | Description |
|------|-------------|
| `open_model(path)` | Open .skp file (relative paths resolve against the workspace) |
| `save_model(path?)` | Save model, optionally to a new path (relative paths resolve against the workspace) |
| `export_scene(format)` | Export: skp, obj, stl, png, jpg, jpeg |

### Status

| Tool | Description |
|------|-------------|
| `check_status()` | Unified health check (SketchUp, console capture, VCAD sidecar, viewer) |

### VCAD Authoring

| Tool | Description |
|------|-------------|
| `vcad_place(node_id, source_file, position?, component_name?)` | Evaluate a `.cmp.oo` file and place the resulting mesh in SketchUp |
| `vcad_update(node_id, source_file?, cascade?)` | Re-evaluate a VCAD node and update its SketchUp geometry; `cascade` also updates dependents |
| `vcad_inspect(source)` | Inspect VCAD geometry: volume, surface area, bounding box (file path or inline Loon) |
| `vcad_eval(code)` | Evaluate Loon code in the VCAD sidecar (REPL mode) |
| `vcad_list_nodes()` | List all VCAD nodes in the current model |
| `vcad_watch_pause()` | Pause reactive watching; changes accumulate without re-evaluation |
| `vcad_watch_resume()` | Resume watching and re-evaluate nodes affected by accumulated changes |

### VCAD Viewer

| Tool | Description |
|------|-------------|
| `vcad_viewer_state()` | Get viewer state snapshot |
| `vcad_viewer_screenshot()` | Save viewer screenshot to `.tmp/vcad-viewer/` |
| `vcad_viewer_focus(node_id)` | Focus viewer camera on a node |

### VCAD Diagnostics

| Tool | Description |
|------|-------------|
| `vcad_metrics()` | Current telemetry snapshot |
| `vcad_reconcile_status()` | Last reconciliation run: timestamp, drift summary, pending actions |

## CLI Commands

```bash
./supex <command> [options]
```

| Command | Description |
|---------|-------------|
| `status` | Check connection and docs status |
| `reload` | Reload extension |
| `eval <code>` | Execute Ruby code |
| `eval-file <path>` | Execute Ruby script |
| `info` | Model information |
| `entities [type]` | List entities |
| `entity <id>` | Full state of one entity |
| `selection` | Selected entities |
| `layers` | List layers |
| `materials` | List materials |
| `camera` | Camera info |
| `screenshot` | Capture view |
| `open <path>` | Open model |
| `save [path]` | Save model |
| `export <format>` | Export scene |

**Common Options:**
- `--host/-H` - SketchUp host (default: localhost)
- `--port/-p` - SketchUp port (default: 9876)
- `--raw/-r` - Output raw JSON (`eval`, `eval-file`, `info`, `entities`, `entity`, `selection`, `layers`, `materials`, `camera`)

## Example Usage

```ruby
# Create a simple box (execute via eval_ruby_file)
model = Sketchup.active_model
model.start_operation('Create Box', true)

group = model.entities.add_group
face = group.entities.add_face(
  [0, 0, 0], [1.m, 0, 0], [1.m, 1.m, 0], [0, 1.m, 0]
)
face.pushpull(50.cm)
group.name = 'Box'

model.commit_operation
```

For complete examples, see the [example-simple-table](https://github.com/darwin/supex/tree/example-simple-table) branch.

## Architecture

```
AI Agent / CLI
      |
      | MCP Protocol (stdio) / Direct calls
      v
+----------------------------------+
|  Python Driver (driver/)         |
|  +-- mcp/            (MCPServer) |
|  +-- cli/main.py     (Typer)     |
|  +-- connection/     (Socket)    |
+----------------------------------+
      |
      | TCP Socket (localhost:9876)
      | JSON-RPC 2.0
      v
+----------------------------------+
|  Ruby Runtime (runtime/)         |
|  +-- SketchUp Process            |
+----------------------------------+
```

## Project Structure

```
driver/
+-- src/supex_driver/
|   +-- __init__.py                  # Package exports
|   +-- __main__.py                  # python -m supex_driver
|   +-- mcp/
|   |   +-- mcp_server.py            # MCPServer, core and viewer tools
|   |   +-- vcad_tools.py            # VCAD authoring tools
|   |   +-- vcad_diagnostics.py      # VCAD metrics and reconcile status tools
|   +-- cli/
|   |   +-- main.py                  # Typer CLI commands
|   |   +-- output.py                # Rich/plain output formatting
|   +-- agent/                       # Provider-agnostic chat agent (supex-chat)
|   |   +-- __init__.py              # Public Agent API + config re-exports
|   |   +-- config.py                # Provider/env config resolution
|   |   +-- errors.py                # Agent error hierarchy
|   |   +-- providers/               # OpenAI + Anthropic dialect clients
|   |   |   +-- base.py              # ChatProvider protocol + event dataclasses
|   |   |   +-- openai.py            # OpenAI /v1/chat/completions dialect
|   |   |   +-- anthropic.py         # Anthropic /v1/messages dialect
|   |   +-- sketchup_mcp.py          # MCP client backend (SketchUp tools)
|   |   +-- file_tools.py            # Workspace file tools
|   |   +-- prompts.py               # Guide-derived system prompt
|   |   +-- loop.py                  # Agentic loop
|   |   +-- agent.py                 # Public Agent facade (single shared core)
|   |   +-- server.py                # Local agent server (windowed app; Phase 4)
|   |   +-- chat_ui/                 # Dependency-free chat web UI (Phase 4)
|   |   +-- cli.py                   # supex-chat CLI + serve entry
|   +-- connection/
|       +-- sketchup_connection.py   # TCP socket client for the runtime
|       +-- sketchup_exceptions.py   # SketchUp error hierarchy
|       +-- vcad_connection.py       # TCP client for the VCAD sidecar
|       +-- vcad_exceptions.py       # VCAD error hierarchy
|       +-- vcad_sidecar.py          # Sidecar process launcher
|       +-- vcad_viewer_relay.py     # Viewer relay client
|       +-- vcad_state.py            # Node state and dependency tracking
|       +-- vcad_dag.py              # Dependency graph
|       +-- vcad_file_watcher.py     # Reactive file watching
|       +-- vcad_observer.py         # SketchUp model change polling
|       +-- vcad_reconcile_state.py  # Reconciliation state
|       +-- vcad_metrics.py          # Telemetry counters
|       +-- vcad_logging.py          # VCAD event logging
|       +-- vcad_schema.py           # Sidecar protocol schemas
|       +-- vcad_artifact_manifest.py # Artifact manifest handling
+-- tests/                   # pytest suite
+-- pyproject.toml           # Package config
+-- README.md
```

## Development

### Setup

```bash
cd driver
uv sync --dev
```

### Commands

```bash
# Linting
uv run ruff check src/ tests/

# Formatting
uv run ruff format src/ tests/

# Type checking
uv run mypy src/

# Tests
uv run pytest tests/ -v
```

### Entry Points

| Script | Purpose |
|--------|---------|
| `./mcp` | MCP server (for Claude Code; Unix wrapper) |
| `./supex` | CLI interface (Unix wrapper) |
| `./sketch` | Chat agent (Unix convenience wrapper) |
| `supex-mcp` | Direct MCP entry point |
| `supex` | Direct CLI entry point |
| `supex-chat` | Chat agent entry point (canonical, cross-platform) |

### Testing Connection

```bash
# Quick connection test
./supex status

# Or programmatically
uv run python -c "
from supex_driver.connection import get_sketchup_connection
conn = get_sketchup_connection()
print(conn.send_command('ping'))
"
```

## Error Handling

Exception hierarchy:
- `SketchUpError` - Base exception
- `SketchUpConnectionError` - Connection failures
- `SketchUpTimeoutError` - Timeout errors
- `SketchUpProtocolError` - JSON/protocol errors

### Logging

The MCP server logs to stderr only. Log files live in `$SUPEX_WORKSPACE/.tmp/logs/` (configurable via `SUPEX_LOG_DIR`):
- `mcp-protocol.jsonl` - MCP stdin/stdout traffic (written by the `./mcp` wrapper)
- `mcp-stderr.log` - MCP server errors and warnings (`./mcp`)
- `cli-driver.log` - CLI driver log (written by the CLI itself)
- `cli-stdout.log` - CLI standard output (`./supex`)
- `cli-stderr.log` - CLI errors and warnings (`./supex`)
