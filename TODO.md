# TODO — Broadening Supex to Any AI Backend ("Open Sketch AI")

Tracking document for the project work. Task states: `[ ]` open, `[x]` done, `[~]` in progress.
The user tests on Windows + SketchUp 2026; this Linux box has no SketchUp, no Ruby — so most *code* work happens here and most *runtime verification* happens on the user's machine or in CI.

> **Git decision (user, 2026-09-07):** commit work to `main` (this fork is solo; `open-sketch-ai/AGENTS.md`'s dev-branch guidance is waived by explicit user choice). TODO.md was committed as `dbcdf46` on `main`. Phases 1-3 committed as `0219c82`.
>
> **Integrity fix (2026-09-07):** two Python 2-style `except A, B:` clauses (invalid Python 3) were found in committed code — `agent/agent.py` (`_image_path_tokens`) and `agent/file_tools.py` (`FileTools.call`) — plus a missing `BackendError` import surfaced while adding `Agent.backend_status`. All fixed and verified (`ruff`/`mypy` clean; agent suite genuinely green). This underscored that earlier "tests pass" notes must be re-verified against the real tree before each commit.

---

## Goal

Today Supex's AI brain is hard-wired to one client: **Claude Code** (an MCP host). Nothing in the repo calls a model API; Claude Code supplies reasoning, tool selection, file authoring, and the chat loop. We want Supex to work with **almost any AI model endpoint**, configured by the standard `base_url` + `api_key` + `model` scheme, including local servers such as **Unsloth Desktop** — delivered as a **self-contained agent that runs on Windows, Linux, and macOS**.

**The workflow we are building:** the user types a prompt into a **chat window**; whatever they ask for is applied **live inside the running SketchUp application** (no import/export round-trips); the model then **screenshots its own output and sees the image**, so it can verify and self-correct before reporting done.

## Decisions locked (confirmed with user)

| Question | Decision |
|---|---|
| Product interface | **Windowed chat app first** (a persistent local agent-server process + a chat front-end shown in its own window/browser tab, separate from SketchUp). The **terminal CLI** (`supex-chat`) is retained as a second interface. Both share one `Agent` loop + event stream. |
| UI form factor / SketchUp embedding | **Separate window app first (user-recommended choice).** The chat UI is a plain local web page served by the agent server — a "just a URL" front-end — so the *same* page can later be embedded **inside SketchUp via `UI::HtmlDialog`** as a staged, additive milestone (the agent loop itself always runs in Python; SketchUp's single-threaded Ruby never hosts it). |
| Live in-place updates | Changes **populate inside the SketchUp application** the moment the model's code runs, through the existing in-process `eval_ruby`/`eval_ruby_file` bridge — **no export/import** anywhere in the flow. `.rb`/`.cmp.oo` sources and `.tmp` screenshots are internal working files only. |
| Visual feedback | The model **verifies its own work**: when it calls a screenshot tool, the agent (vision on) reads the PNG and attaches it as an image on the next model turn so the model sees the actual result; the window also shows thumbnails of captured screenshots. Vision off degrades to returning the file path. |
| API dialects | **OpenAI-compatible AND Anthropic-compatible** (Unsloth Desktop speaks both on one port: `/v1/chat/completions` + `/v1/messages`, plus `GET /v1/models`; auth `Bearer sk-unsloth-…`) |
| Agent capability scope | **27 SketchUp MCP tools + workspace file tools** (author `.rb`/`.cmp.oo` per the guide, then `eval_ruby_file`); no shell; screenshot/vision support where the model allows |
| Integration | **Additive** new Python component in `driver/` that talks to the **existing Supex MCP server** as its SketchUp backend. Claude Code + current MCP flow stays 100% intact |
| **Distribution** | **Self-contained per-OS standalone binaries** (PyInstaller) for **Windows, Linux, and macOS** — no Python/uv/repo clone needed on the target; `uv tool`/pipx install remains a fallback. A GitHub Actions build matrix (ubuntu/macos/windows) is **drafted but only created after explicit user approval** (root `AGENTS.md` §36.4a forbids workflow files otherwise) |

## Key facts the plan relies on (verified)

- SketchUp side: Ruby extension → JSON-RPC TCP bridge on `127.0.0.1:9876` (hello handshake, `tools/call`, optional `SUPEX_AUTH_TOKEN`). Repo code never calls any LLM today.
- Driver (`driver/`, package `supex-driver`, Python ≥3.14, deps: `mcp[cli]>=2.1.1`, `typer`, `rich`, `websockets`, `httpx2`):
  - `mcp_server.py` builds `MCPServer("Supex")`, **27 tools** via `@mcp.tool()` (18 core + 7 VCAD in `vcad_tools.py` + 2 diagnostics in `vcad_diagnostics.py`). `ctx` is an injected first param; schemas derive from signatures. Single shared `mcp` singleton.
  - Root `./mcp` wrapper = stdio server (`uv run --project driver supex-mcp`), tees protocol to `mcp-protocol.jsonl`. It is **Unix/bash + repo + uv dependent**, so the new agent spawns the server differently (see T-2.1/T-8.2).
  - CLI (`cli/main.py`) uses `get_sketchup_connection(agent=…)` → `send_command(method, params, request_id)`; 15 Typer commands.
- Config is env-driven; centralized in `docs/configuration.md` (`SUPEX_*`, defaults: host `localhost`, port `9876`, etc.). Agent guide lives in `docs/agents/guide/*.md` (`README.md` router, `ruby.md`, `vcad.md`, `mcp.md` canonical tool inventory, `workflow.md`, `troubleshooting.md`).
- File tools in the runtime resolve relative paths against the workspace passed in the `hello` handshake; path allowlist `SUPEX_ALLOWED_ROOTS`. `eval_ruby`/`eval_ruby_file` execute arbitrary Ruby in-process (dev guardrail, not a sandbox).
- Screenshots (`take_screenshot`, `take_batch_screenshots`, `vcad_viewer_screenshot`) return **file paths only** (token economy); making the model *see* them is the agent layer's job (Phase 4).
- Local test env: `gh` authenticated as `peva3`; no uv/ruby/rust initially. Driver tests need uv + Python 3.14 (now installed locally). Ruby suites need the `su-mock` Ruby harness. CI image exists (`devtools/ci/Dockerfile`).

> **Cross-platform requirement (user, 2026-09-07):** every component we add must run on **Windows, Linux, and macOS**. Consequences: no reliance on bash wrappers as a primary entry, no `uv` on the target machine, pathlib everywhere, no `:`-separated path lists in new code, and prompts/guide data must travel inside the package (no repo-checkout assumption). See **Phase 8** for packaging.

---

## Phase 0 — Project setup & naming

- [x] **T-0.1** Branch strategy decided: **commit to `main`** (user choice for this solo fork; overrides the repo AGENTS dev-branch guidance). TODO.md committed/pushed.
- [x] **T-0.2** **Naming decision (locked):** wrapper `./sketch`, console script `supex-chat`, package `supex_driver/agent/`. Uses the supex driver namespace with a distinct command name.
- [x] **T-0.3** **Env namespace locked:** `SUPEX_AI_BASE_URL` / `SUPEX_AI_API_KEY` / `SUPEX_AI_MODEL` / `SUPEX_AI_DIALECT` (`auto`|`openai`|`anthropic`) / `SUPEX_AI_TIMEOUT` / `SUPEX_AI_TEMPERATURE` / `SUPEX_AI_MAX_TOKENS` / `SUPEX_AI_VISION` / `SUPEX_AI_MAX_ITERATIONS`. Fallbacks per resolved dialect: `OPENAI_BASE_URL`+`OPENAI_API_KEY`+`OPENAI_MODEL` or `ANTHROPIC_BASE_URL`+`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`+`ANTHROPIC_MODEL`. Precedence: CLI flags > env > profiles file (Phase 6). Env table entry added to `docs/configuration.md` in T-7.2.
- [x] **T-0.4** Installed locally: `uv` 0.12.3 + Python 3.14.7; `uv sync --project driver` done. Regression baseline: 736 driver tests pass (only `test_vcad_e2e_mock.py` excluded — needs Ruby, absent here).

## Phase 1 — Provider layer (OpenAI + Anthropic dialects)

Goal: a small, dependency-lean HTTP client that speaks both dialects with streaming + tool calling, fully unit-testable against recorded fixtures (no live network). Pure Python, cross-platform by construction. **DONE (commit `0219c82`).**

- [x] **T-1.1** `driver/src/supex_driver/agent/config.py` — provider config model: `base_url`, `api_key`, `model`, `dialect` (`auto` | `openai` | `anthropic`), request/stream timeouts, temperature, `max_iterations`, vision capability flag, workspace path. `auto` sniffs dialect: Anthropic path present (`/v1/messages`) → anthropic, else openai. Precedence + env parsing + validation (clear errors on missing key for remote vs. local endpoints where `sk-`/`ollama`/`none` keys are allowed). 194 lines; mypy + ruff clean; tests in `tests/agent/test_config.py`.
- [x] **T-1.2** HTTP client decision made during review: **declare `httpx2` as a direct dependency** — it is *already* required transitively by `mcp[cli]` (v2.1.1) and is present in `driver/uv.lock`, so no new transitive packages are introduced (decision-ladder rung 4). `httpx2` mirrors the classic `httpx` API (async `client.stream`, `Timeout`) **and ships SSE built-in** (`EventSource`, `ServerSentEvent`), removing the need for the separate `httpx-sse` package. Plain `httpx` is NOT installed in this tree. `httpx2>=2.12` declared, uv.lock updated.
- [x] **T-1.3** `agent/providers/base.py` — `ChatProvider` protocol: `stream(messages, tools) -> AsyncIterator[ProviderEvent]` where events = `text_delta`, `tool_call(id, name, args_json)`, `done(stop_reason, usage)`. Shared schema translation helpers: our internal tool schema (`{name, description, input_schema}`) → OpenAI `tools[]`/`tool_choice` AND → Anthropic `tools[]` (with `input_schema`). 307 lines; `Message`/`ToolCall`/`ToolSchema`/`ImagePart`/`Usage`/`Done` dataclasses + `normalize_stop_reason`/`url_join`/`parse_tool_arguments`/`extract_model_ids` helpers.
- [x] **T-1.4** `agent/providers/openai.py` — POST `{base}/chat/completions`, `messages` + `tools`, SSE stream; assemble `tool_calls` deltas; parse usage/stop_reason. Handle `/v1/models`-style listing when `base_url` points at a local server. 226 lines; cumulative-resend argument merging; `data:`/`[DONE]` handling.
- [x] **T-1.5** `agent/providers/anthropic.py` — POST `{base}/messages` with `system`, `messages`, `tools`, `tool_choice`; SSE content-block events (`content_block_start`/`delta`/`stop`, `tool_use`); map Anthropic stop_reason; **auth header strategy**: real Anthropic = `x-api-key` + `anthropic-version`; Unsloth/llama local = `Authorization: Bearer <token>`. Config field to select. 293 lines; `tool_use` input seeded from `content_block_start` then `input_json_delta` merged.
- [x] **T-1.6** `agent/providers/__init__.py` factory + model discovery helper (`GET {base}/v1/models` → model ids; degrade gracefully when absent). `build_provider(config)` + async `list_models(config)`.
- [x] **T-1.7** Unit tests `driver/tests/agent/test_config.py` + `test_providers.py` — recorded/fixture SSE bodies (OpenAI + Anthropic) for: config resolution matrix, text streaming, single/parallel tool calls, tool_use→tool_result continuation, auth header selection (`Bearer` vs `x-api-key`), `/v1/models` parse, HTTP error (401/429/500) + connection-error surfacing, malformed-JSON → `ProviderProtocolError`. No network.

## Phase 2 — SketchUp backend via the existing MCP server + file tools

Goal: agent gets SketchUp's 27 tools and workspace file access, additive to the MCP server. **DONE (commit `0219c82`).**

- [x] **T-2.1** `agent/sketchup_mcp.py` — thin **MCP client** (SDK `mcp.client.stdio`) that spawns the supex server the same way Claude Code does. Spawn command is **distribution-aware, never the repo `./mcp` bash wrapper**: source/dev → `[sys.executable, '-m', 'supex_driver']` (runs `supex_driver/__main__.py` → stdio MCP server; same interpreter guarantees importability, works with no uv/bash); installed → `supex-mcp` console script on PATH; frozen → sibling `supex-mcp` exe (see T-8.2). Env passthrough: `SUPEX_WORKSPACE`, `SUPEX_AUTH_TOKEN`, `SUPEX_ALLOWED_ROOTS` (inherits parent `SUPEX_*` env, filters non-SUPEX). On session init: `initialize`, then `tools/list` → internal `ToolSchema` list. Executes `tools/call` (client_info name `supex-chat`), returns flattened `result.content` text.
- [x] **T-2.2** `connect()` idempotent + `_teardown()` on failure; `list_tools()`, `call_tool()` reconnect-once-and-retry semantics (server survives SketchUp closed); `async with` (adds `__aenter__/__aexit__`) + clean `aclose()`.
- [x] **T-2.3** `agent/file_tools.py` — workspace file tools: `read_file` (UTF-8 text or JSON binary/oversize notice, 1 MiB cap), `write_file` (mkdir parents), `edit_file` (replace-once/replace_all), `list_dir` (JSON entries), `delete_file` (opt-in `allow_delete`). Python-side containment: resolve + symlink-aware prefix check against workspace + `SUPEX_ALLOWED_ROOTS`, `os.pathsep` split, `*` disables (mirrors `path_policy.rb`). Violations raise `PathNotAllowedError`; tool problems return compact JSON.
- [x] **T-2.4** Tests `driver/tests/agent/test_sketchup_mcp.py` (14 tests: command resolution incl frozen/console-script/module branches; real subprocess spawn `[python,-m,supex_driver]` asserts 27 tools + `check_status` text; missing-binary → `BackendConnectionError`; `BackendToolError` on unknown tool; env passthrough incl auth token + allowed roots) and `test_file_tools.py` (23 tests: path policy absolute/relative/`..`/symlink-escape denied, ALLOWED_ROOTS extension + `*` + os.pathsep, write-parent-dirs, edit replace counts, binary flag, delete gating, schemas). **832 driver tests pass** (was 795); mypy clean on all 9 `agent/` source files.
- [ ] **T-2.5** Manual verification script/procedure for the user on Windows (no SketchUp needed): `supex-chat --check` lists tools from the MCP server and pings `:9876`.

## Phase 3 — Agent loop, system prompt, history handling

Goal: provider-neutral agentic loop + guide-derived system prompt + public `Agent` facade. **Core DONE (commit `0219c82`; tests `fakes.py` + `test_loop.py` + `test_agent.py` + `test_prompts.py`). T-3.4 image plumbing done; the *autonomous screenshot attach* portion is implemented in Phase 4 (T-4.5) where it belongs.**

- [x] **T-3.1** `agent/prompts.py` — assemble the **system prompt** from `docs/agents/guide/*.md` (README router + mcp.md + ruby.md + workflow.md + troubleshooting.md; resolve the `stdlib` symlink target; skip git-ignored `api/` generated docs by default). **Source resolution is dual:** in a repo checkout, load from `SUPEX_ROOT` (any cwd); outside a checkout (installed/frozen binary), load the same guide set from **bundled package data** shipped with the wheel/binary (see T-8.3). Add an agent-specific preamble explaining the file-based workflow, relative-path convention, and screenshot-read workflow. 151 lines.
- [x] **T-3.2** `agent/loop.py` — core agentic loop: history in provider-neutral form → per-dialect message/tool-result encoding (OpenAI `role:"tool"` + `tool_call_id`; Anthropic `tool_use`/`tool_result` blocks) → stream model → execute tool calls via Phase 2 backends → append results → repeat until no tool call. Guards: `max_iterations`, dangling-tool-call cleanup. `TurnResult(text, iterations, stopped, tool_calls, usage)`; `AgentLoop(provider, system, tools, execute, …)`; `on_event` hook streams `ProviderEvent`s to any consumer (terminal, server, UI).
- [x] **T-3.3** Message/result size hygiene: tool results compact JSON; cap huge returns (`truncate_result`, 20 KiB default); screenshots stay as paths at the tool layer (converted to images by the agent when vision is on — T-4.5).
- [x] **T-3.4** **Image plumbing (per-dialect):** OpenAI content blocks `image_url` data-URL; Anthropic `image` base64 blocks. `Message.images: list[ImagePart]`; `run_turn(..., images=...)` attaches user-supplied images; `prompts.py` preamble reflects vision on/off. **Remaining:** the loop does not yet *autonomously* load screenshot-tool PNG results into the next model turn — that is T-4.5.
- [x] **T-3.5** Unit tests `driver/tests/agent/test_loop.py` — fake provider scripted to (a) answer directly, (b) request one tool then finish, (c) loop tools, (d) hit `max_iterations`; message-history encoding verified byte-correct for **both** dialects; parallel tool calls; usage propagation; truncation; dangling cleanup.
- [x] **T-3.6** `agent/agent.py` — public `Agent` API tying config + providers + backends + loop + prompts; exposes `run_turn(user_text, images=…)` for embedding in the CLI, the server, and future UIs. `Agent` is the single shared core used by **both** the terminal and the windowed app.

## Phase 4 — Windowed chat app (separate window first) + live visual feedback

Goal: the user types a prompt into a **window**, the model's edits appear **live inside SketchUp** (in-process eval; nothing imported/exported), and the model **sees screenshots of its own output** to verify. Architecture: a persistent **local agent-server** process hosts the `Agent` loop and serves a plain **chat UI page** on `127.0.0.1`; the UI is "just a URL", so the identical page can later be embedded inside SketchUp via `UI::HtmlDialog` (staged, T-4.7). No new heavy frameworks: stdlib HTTP for the server, dependency-free static UI (decision ladder).

- [x] **T-4.1** `agent/server.py` — run the shared `Agent` facade behind a **local HTTP server** (stdlib `http.server`, asyncio-friendly; loopback bind by default). Streaming protocol (SSE or NDJSON) carries the same `ProviderEvent` stream the CLI uses: text deltas, tool-call start/result, **image attachments**, done + usage. Endpoints: `GET /` (UI), `GET /api/health`, `GET /api/models`, `POST /api/chat` (stream one user turn incl. optional image), `POST /api/reset`. `SUPEX_AI_HOST`/`SUPEX_AI_PORT` + `--host`/`--port`. Loopback-only by default (mirror runtime security posture); nothing sensitive on the wire beyond what env already holds. **Done:** `AgentServer` owns the `Agent` on a dedicated background asyncio loop; NDJSON wire contract documented in module docstring (`delta`/`tool_call`/`model_end`/`tool_result`/terminal `done`|`error`); event callbacks bridge onto the active request queue; one turn at a time (409 on concurrent `/api/chat`); static UI served from `agent/chat_ui/` with extension allowlist.
- [x] **T-4.2** `agent/chat_ui/` — small **static single-page UI** (HTML/CSS/JS, no build step, no CDN): multiline prompt box, streaming assistant text, tool-call activity lines, **inline thumbnails of screenshots the model captured**, model + SketchUp connection status, `/reset`. Dependency-free so PyInstaller can ship it as `datas` (T-8.3) and an in-SketchUp `HtmlDialog` can load it unchanged. **Done:** single `index.html` (dark theme, badges for model/dialect/vision/workspace from `/api/health`, streaming NDJSON reader, per-tool cards w/ running state + click-to-fullscreen image thumbnails, Esc aborts turn, Ctrl+L / button `/api/reset`).
- [ ] **T-4.3** Launch plumbing: `supex-chat serve` (or `supex-chat --ui`) starts the server and opens the default browser to `http://127.0.0.1:<port>`; Ctrl-C / window close shuts down cleanly; Windows-native (no bash). Optional later: a native webview window (e.g. `pywebview`) so it feels like a real app window — deferred to keep deps lean.
- [x] **T-4.4** Tests `driver/tests/agent/test_server.py` — start the server on an ephemeral port with `ScriptedProvider`/`FakeBackend`; assert `/api/health`, `/api/models`, and one `/api/chat` stream emits text-delta + done; image attachments surface as base64 payloads; reset clears history. **Done:** 12 tests (health, UI index, model listing via `_ModelsProvider`, 404, text stream delta+done, tool_call+tool_result pairing with images, screenshot image base64 surfacing parametrized openai/anthropic dialect, empty/malformed payload 400, reset clears history, sequential chats). Plus `test_loop.py::test_on_tool_result_reports_each_execution`. **896 driver tests pass** (was 883).
- [x] **T-4.5** **Autonomous visual feedback (the "model sees its output" core):** when a screenshot tool (`take_screenshot`, `take_batch_screenshots`, `vcad_viewer_screenshot`) returns a workspace PNG **and** `config.vision` is on, the loop reads the file (within the workspace containment rules) and attaches it as an image message on the **next model turn** so the model sees the actual result and can self-correct. **Done:** `AgentLoop.collect_visual` hook + dialect-aware attachment (Anthropic: image blocks ride the `tool_result` content array; OpenAI: tool content is string-only, so a synthetic trailing user message carries the image with a `_VISUAL_NOTE`); `Agent._visual_images` resolves the returned workspace PNG path under containment, base64-encodes it (max 8 MiB, max 8 images, png/jpg/webp). Vision off → keep returning the path text only. Prompt preamble (prompts.py) updated. **883 driver tests pass** (was 871).
- [x] **T-4.6** Wiring consistency: image feedback hook lives in `Agent`/loop so the **terminal CLI and the windowed app behave identically**; both render a tool line "screenshot saved: <path> (vision off)" vs auto-attach when on. **Done:** `AgentLoop.on_tool_result` callback fires per executed tool with `(tool_id, name, arguments, truncated_result, images)`; `Agent` accepts `on_tool_result` and `AgentServer` routes it onto the NDJSON `tool_result` line — the server exposes exactly what the CLI would print.
- [ ] **T-4.7** **Staged (additive, later): in-SketchUp window.** A small Ruby extension opens a `UI::HtmlDialog` pointing at the running agent server URL (`http://127.0.0.1:<port>`), embedding the *same* chat page inside SketchUp. Optional; only after Phase 4 core + Phase 8 binaries exist; version-sensitive (WebView2/WKWebView) and tested on the user's machine. The agent loop still runs in Python — the dialog is a pure front-end.

## Phase 5 — Chat CLI + console entry + UX

Goal: terminal interface to the same `Agent` core, canonical cross-platform console entry. (Windowed app is Phase 4; terminal is a peer interface, not the primary distribution path.)

- [x] **T-5.1** `agent/cli.py` (Typer, consistent with `cli/main.py` style) — entry `supex-chat`. Subcommands/flags:
  - `supex-chat` → interactive session (slash commands `/help`, `/model`, `/tools`, `/status`, `/reset`, `/exit`).
  - `supex-chat serve` → launch the Phase 4 windowed app (server + browser; `--no-open` to skip).
  - `--prompt "…"` / `-p` one-shot non-interactive run.
  - `--base-url`, `--api-key`, `--model`, `--dialect`, `--list-models`, `--check`, `--version`, plus `--vision/--no-vision`, `--allow-delete`, `--timeout`, `--temperature`, `--max-tokens`, `--max-iterations`. (`--provider-profile` deferred to T-6.1, which owns the profiles file.)
  - Respect existing output env (`SUPEX_PLAIN`/`SUPEX_COLOR`/TTY via plain text vs rich Console) and logging (`SUPEX_LOG_DIR` → `agent-chat.log`, mirrors `cli/main.py` lazy file logger). **Done:** Typer app `invoke_without_command=True`; `_ensure_logging`; `_config_kwargs` (guards unknown dialect → `typer.BadParameter`); `_load_config_from` (ConfigError → exit 2); `EventPrinter` streams `TextDelta`/tool banners/usage; `_slash_command` router; `_interactive_async`, `_run_one_turn`, `_one_shot`, `_print_models`, `_run_check`, `serve`. Requires `Agent.config` + `Agent.backend_status()` helpers added to `agent.py` (`backend_status` runs the `check_status` MCP tool; never raises for a disconnected SketchUp).
- [x] **T-5.2** Rich rendering of model stream (live `TextDelta` write-out via rich Console with `markup=False`/plain stdout) + tool-call notices + error surfacing; Ctrl-C interrupts a turn in the REPL (banner) and exits cleanly at the prompt; NO_COLOR/plain honored by `EventPrinter`; works in `cmd`/`pwsh`/Windows Terminal (plain text) as well as POSIX TTYs. **Done.**
- [x] **T-5.3** Console entry `supex-chat = "supex_driver.agent.cli:main"` added in `driver/pyproject.toml` — canonical, cross-platform. Root wrapper `sketch` (bash, portable shebang) added as a Unix convenience only, mirroring the `supex` wrapper's symlink resolution + `FORCE_COLOR` + log-tee; documented in its header comment.
- [x] **T-5.4** Unit tests `driver/tests/agent/test_cli.py` — 21 tests, no live I/O: config-knob collection + invalid-dialect rejection; env-vs-flag precedence and missing-model exit 2 via `_load_config_from`; `EventPrinter` streaming/plain/banners/usage; slash-command routing (`/help`, `/model`, `/tools`, `/status`, `/reset`, `/exit`, unknown, non-slash passthrough) against `Agent`+`FakeBackend`; one-shot final-text and `AgentError` surfacing. **917 driver tests pass** (was 896). Verified live: `uv run python -m supex_driver.agent.cli --version` → `supex-chat 0.3.0`, `--help` shows the full option set.

## Phase 6 — Config profiles, robustness, real-endpoint hardening

- [ ] **T-6.1** Optional per-project config file (git-ignored) to store named **provider profiles** (OpenAI, Azure, OpenRouter, Groq, Ollama, LM Studio, vLLM, Unsloth) with `base_url`/`api_key`/`model`/`dialect`; secrets never committed (`docs/security.md` note + `.gitignore`). Profile file location is **per-OS user config dir** (see T-8.4), overridable by env; precedence: flags > env > profiles file.
- [ ] **T-6.2** Retries/backoff for provider HTTP + SSE reconnect on mid-stream drop (mirror `docs/configuration.md` `SUPEX_RETRIES`); distinguish "model unreachable" vs "SketchUp unreachable" in errors and in `--check`.
- [ ] **T-6.3** Unsloth-Desktop-specific UX polish: `--list-models` via `GET /v1/models`, accept both `sk-unsloth-…` (Bearer) and Anthropic headers, sane defaults for `http://localhost:8000`/`:8888`. Document exact user steps (create key in Settings→API).
- [ ] **T-6.4** Token/context management: basic token accounting from provider usage, auto-compact or guided `/reset` when nearing the model context window.
- [ ] **T-6.5** Security pass: never log api_key; treat model/tool output as untrusted data (per root `AGENTS.md` §36.9/§55 mindset — SketchUp can execute arbitrary Ruby, so provider output must never be auto-executed beyond the tool loop the user is watching); confirm-before-destructive conventions.

## Phase 7 — Docs, polish, release prep

- [ ] **T-7.1** `docs/agent.md` (or extend `docs/cli.md`): what the chat agent is, provider config reference, dialect table, Unsloth quickstart, OpenAI/Azure/OpenRouter/Ollama/LM Studio examples, `supex-chat` usage, **the windowed-app workflow** (prompt in window → live changes in SketchUp → model screenshots its own output), security notes.
- [ ] **T-7.2** Update `docs/configuration.md` env table with all new `SUPEX_AI_*`/agent vars + log files. Update `driver/README.md` tool counts/commands + supported-OSes note. Update root `README.md` feature/install text to mention the provider-agnostic chat agent (do NOT overclaim; keep Claude Code as a supported client).
- [ ] **T-7.3** Update project-structure notes in `AGENTS.md` (new `driver/src/supex_driver/agent/`, console entry) only if the repo AGENTS structure section stays accurate.
- [ ] **T-7.4** Test inventory parity: extend `driver/tests/test_tool_inventory.py`-style check so agent-visible tool docs stay in sync; add `agent` suite slug to `scripts/launch-test.sh` and CI if CI is driven by it.
- [ ] **T-7.5** Full lint + typecheck sweep (`ruff`, `mypy`, per `scripts/lint.sh`) and full driver test suite in CI image; manual E2E on the user's Windows machine (see Verification).
- [ ] **T-7.6** Version bump + changelog ONLY when the user explicitly asks (repo `AGENTS.md`: never bump without being asked). Prep a `v0.4.0`-style release flow per `scripts/release.sh` when user greenlights, **with per-OS binaries attached from Phase 8**.

## Phase 8 — Cross-platform packaging & distribution (Windows + Linux + macOS)

Goal: deliver `supex-chat` as **self-contained per-OS binaries** so any user can run the agent on Windows/Linux/macOS with **no Python, no uv, no repo clone**. Also the point where every "works here" assumption of the dev box is audited for the other two OSes.

- [ ] **T-8.1** Add a `build` extra to `driver/pyproject.toml` (`pyinstaller`) and a `driver/packaging/` recipe (spec file or `pyinstaller` CLI args) that produces **three executables in one build**: `supex-chat`, `supex-mcp` (stdio MCP server), and `supex` (existing CLI) — so a frozen agent can spawn a frozen MCP backend as a sibling executable. Validate hidden imports (typer, rich, mcp SDK, httpx2, websockets).
- [ ] **T-8.2** `agent/_command.py` (or inside `sketchup_mcp.py`) — backend command resolution across distribution modes: **frozen** (`getattr(sys, 'frozen', False)`): sibling `supex-mcp[.exe]` next to `sys.executable`; **installed**: `shutil.which('supex-mcp')`; **source/dev**: `[sys.executable, '-m', 'supex_driver.mcp']`. Never references the repo `./mcp` bash wrapper.
- [ ] **T-8.3** Guide prompt data **and** the chat UI (Phase 4) must be self-contained: include `docs/agents/guide/*.md` (minus generated/symlinked dirs) and `agent/chat_ui/` as package data in the wheel (`[tool.hatch.build]` include) **and** as PyInstaller `datas` so `prompts.py` (T-3.1) and the server (T-4.1) resolve with no repo present.
- [ ] **T-8.4** Cross-platform user paths: new code resolves the per-OS user config dir for provider profiles (T-6.1) — Windows `%APPDATA%`, macOS `~/Library/Application Support`, Linux `$XDG_CONFIG_HOME`/`~/.config`; and parses `SUPEX_ALLOWED_ROOTS` with an `os.pathsep`-aware splitter (Windows `;` vs POSIX `:`) rather than hard-coding `:`. No new `:`-joined path lists.
- [ ] **T-8.5** Local Linux build smoke test (this box): `uv run --extra build pyinstaller …` → run the produced `dist/supex-chat --version`, `--check`, `serve` (windowed app), and exercise the frozen spawn-backend path headless. (Windows/macOS binaries can only be smoke-tested by the user or CI.)
- [ ] **T-8.6** Draft a GitHub Actions build-matrix job (ubuntu-latest + macos-latest + windows-latest) → artifacts/Release. **DO NOT create the workflow file without explicit user approval** (root `AGENTS.md` §36.4a). Present the draft to the user for sign-off.
- [ ] **T-8.7** Docs: `docs/agent.md` gains an **Install** section (per-OS binary download + `uv tool`/pipx fallback; `sketch` marked Unix-only) and a **Build from source** section (per-OS via `pyinstaller` or CI); `driver/README.md` notes supported OSes and the dev-vs-frozen mode difference.

---

## Verification (how each chunk is proven)

| Chunk | Local (Linux box) | User machine (Windows + SketchUp 2026) |
|---|---|---|
| Phase 1 providers | Unit tests vs recorded fixtures (`pytest`) | `supex-chat --list-models` against Unsloth/OpenAI |
| Phase 2 backend+files | Unit tests vs fake MCP server | `supex-chat --check` shows 27 tools + bridge connected |
| Phase 3 loop | Unit tests (scripted provider) | `supex-chat --prompt "create a cube and screenshot it"` |
| **Phase 4 windowed app** | `test_server.py` + serve smoke test | Open the chat window beside SketchUp; prompt; watch edits land live + model screenshot feedback |
| Phase 5 CLI | Arg/slash-command tests | Interactive session; `/tools`, `/status`, `/model` |
| Phase 6 hardening | Security/backoff unit tests | Real Unsloth + a cloud OpenAI-compatible endpoint |
| Phase 7 release | `./scripts/lint.sh`, full driver suite in CI image | E2E: full Ruby and VCAD workflow via a local model |
| **Phase 8 packaging** | PyInstaller Linux build + `dist/supex-chat --check`/`serve` smoke test | Run the **Windows binary** (no Python/uv installed) against SketchUp |

## Risks / open questions

- **Tool-calling reliability on small local models** (Unsloth GGUF) is the biggest unknown; document fallbacks (verbatim JSON mode prompt, `tool_choice`, smaller tool set) — Phase 1/3 should keep the loop resilient to malformed tool calls (retry once, then surface).
- **Vision** support varies wildly across local models; keep it strictly opt-in and degrade to "path only" (T-4.5).
- **SketchUp live-refresh**: model edits land via in-process eval and SketchUp repaints natively; no refresh plumbing needed — confirm visually on the user's machine (screen recording during an agent run).
- **PyInstaller + Python 3.14** compatibility must be verified early (T-8.1/T-8.5); if PyInstaller lags, pin an older Python for builds or use the CI image's interpreter.
- **Frozen subprocess spawn** (agent → MCP backend) is the trickiest packaging detail; the sibling-executable design (T-8.2) must be smoke-tested per OS. Onefile re-extraction cost is acceptable.
- **Guide content as system prompt** may be large; Phase 3 must budget/curate which guide files are always loaded vs. on-demand (respect `AGENTS.md` context-economy rules). Bundling into the binary grows it slightly; acceptable.
- **In-SketchUp `HtmlDialog`** (T-4.7) is version-sensitive (WebView2/WKWebView) and only testable by the user; kept optional and staged behind the separate-window app.
- **Repo governance**: root `AGENTS.md` rules (no version bumps, no GitHub automation incl. Actions workflows without approval, identity = `peva3`, dev branch waived on main) apply throughout. Windows/macOS binary builds are only verifiable by the user or via an approved CI matrix.
- **Windows path-list separator**: `SUPEX_ALLOWED_ROOTS`/`VCAD_LOON_PATH` are documented `:`-joined in the Ruby/Rust side; our Python parsing must not assume that (T-8.4).

## Out of scope (for now)

- Packaging the **Ruby runtime extension or Rust VCAD sidecar** as per-OS binaries — only the **Python agent** is the self-contained deliverable; the SketchUp-side extension keeps its existing dev install/launch flow (unchanged by this project).
- A **native desktop shell** for the chat window (Electron/Tauri/pywebview) — the Phase 4 app uses the system browser first; a native shell is a possible later polish.
- Converting the MCP server to HTTP/streamable transport so Unsloth *itself* could be the MCP host — possible later if users want Unsloth's built-in MCP UI to attach to SketchUp.
- Shell/terminal tool for the agent, or agentic web search.
- Modifying the SketchUp Ruby runtime or VCAD sidecar behavior.
