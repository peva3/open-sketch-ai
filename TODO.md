# TODO — Broadening Supex to Any AI Backend ("Open Sketch AI")

Tracking document for the project work. Task states: `[ ]` open, `[x]` done, `[~]` in progress.
All development happens on the `dev` branch (see `AGENTS.md`). The user tests on Windows + SketchUp 2026; this Linux box has no SketchUp, no Ruby, no Python 3.14/uv — so most *code* work happens here and most *runtime verification* happens on the user's machine or in CI.

---

## Goal

Today Supex's AI brain is hard-wired to one client: **Claude Code** (an MCP host). Nothing in the repo calls a model API; Claude Code supplies reasoning, tool selection, file authoring, and the chat loop. We want Supex to work with **almost any AI model endpoint**, configured by the standard `base_url` + `api_key` + `model` scheme, including local servers such as **Unsloth Desktop**.

## Decisions locked (confirmed with user)

| Question | Decision |
|---|---|
| Product shape | **Terminal agent CLI** (Claude-Code-style interactive session that drives SketchUp) |
| API dialects | **OpenAI-compatible AND Anthropic-compatible** (Unsloth Desktop speaks both on one port: `/v1/chat/completions` + `/v1/messages`, plus `GET /v1/models`; auth `Bearer sk-unsloth-…`) |
| Agent capability scope | **27 SketchUp MCP tools + workspace file tools** (author `.rb`/`.cmp.oo` per the guide, then `eval_ruby_file`); no shell; screenshot/vision support where the model allows |
| Integration | **Additive** new Python component in `driver/` that talks to the **existing Supex MCP server** as its SketchUp backend (Option A). Claude Code + current MCP flow stays 100% intact |

## Key facts the plan relies on (verified)

- SketchUp side: Ruby extension → JSON-RPC TCP bridge on `127.0.0.1:9876` (hello handshake, `tools/call`, optional `SUPEX_AUTH_TOKEN`). Repo code never calls any LLM today.
- Driver (`driver/`, package `supex-driver`, Python ≥3.14, deps: `mcp[cli]>=2.1.1`, `typer`, `rich`, `websockets`):
  - `mcp_server.py` builds `MCPServer("Supex")`, **27 tools** via `@mcp.tool()` (18 core + 7 VCAD in `vcad_tools.py` + 2 diagnostics in `vcad_diagnostics.py`). `ctx` is an injected first param; schemas derive from signatures. Single shared `mcp` singleton.
  - Root `./mcp` wrapper = stdio server (`uv run --project driver supex-mcp`), tees protocol to `mcp-protocol.jsonl`. This is what the new agent will spawn as an MCP client.
  - CLI (`cli/main.py`) uses `get_sketchup_connection(agent=…)` → `send_command(method, params, request_id)`; 15 Typer commands.
- Config is env-driven; centralized in `docs/configuration.md` (`SUPEX_*`, defaults: host `localhost`, port `9876`, etc.). Agent guide lives in `docs/agents/guide/*.md` (`README.md` router, `ruby.md`, `vcad.md`, `mcp.md` canonical tool inventory, `workflow.md`, `troubleshooting.md`).
- File tools in the runtime resolve relative paths against the workspace passed in the `hello` handshake; path allowlist `SUPEX_ALLOWED_ROOTS`. `eval_ruby`/`eval_ruby_file` execute arbitrary Ruby in-process (dev guardrail, not a sandbox).
- Screenshots (`take_screenshot`, `take_batch_screenshots`, `vcad_viewer_screenshot`) return **file paths only** (token economy); a vision-capable agent must read the PNG itself.
- Local test env: `gh` authenticated as `peva3`; no uv/ruby/rust. Driver tests need uv + Python 3.14 (installable), Ruby suites need the `su-mock` Ruby harness. CI image exists (`devtools/ci/Dockerfile`).

---

## Phase 0 — Project setup & naming

- [ ] **T-0.1** Create `dev` branch (from `main`); push it. Commit TODO.md there. (Per `AGENTS.md`: all work on `dev`, never direct to `main`.)
- [ ] **T-0.2** **Naming decision (do first):** pick the product/command name. Proposed working name: wrapper `./sketch` + console script `supex-chat`, package `supex_driver/agent/` (keeps the supex driver namespace). Alternative candidates: `open-sketch`, `supex-ai`, `sketch-ai`. Decide once and use consistently in code/docs/wrapper.
- [ ] **T-0.3** Decide AI-provider **env var namespace** (proposal: `SUPEX_AI_BASE_URL` / `SUPEX_AI_API_KEY` / `SUPEX_AI_MODEL` / `SUPEX_AI_DIALECT` + standard `OPENAI_*`/`ANTHROPIC_*` fallbacks). Document precedence: CLI flags > env > optional project config file. Env reference must be added to `docs/configuration.md`.
- [ ] **T-0.4** Optionally install `uv` + Python 3.14 in this environment so driver lint/unit tests can run locally during implementation (else rely on user/CI).

## Phase 1 — Provider layer (OpenAI + Anthropic dialects)

Goal: a small, dependency-lean HTTP client that speaks both dialects with streaming + tool calling, fully unit-testable against recorded fixtures (no live network).

- [ ] **T-1.1** `driver/src/supex_driver/agent/config.py` — provider config model: `base_url`, `api_key`, `model`, `dialect` (`auto` | `openai` | `anthropic`), request/stream timeouts, temperature, `max_iterations`, vision capability flag, workspace path. `auto` sniffs dialect: Anthropic path present (`/v1/messages`) → anthropic, else openai. Precedence + env parsing + validation (clear errors on missing key for remote vs. local endpoints where `sk-`/`ollama`/`none` keys are allowed).
- [ ] **T-1.2** Add HTTP dependency to `driver/pyproject.toml` (decision: **`httpx`** for HTTP/1.1 + SSE; or stdlib `urllib`+hand-rolled SSE if we want zero new deps — pick one in review of `AGENTS.md` §17/§50 ladder). Update `driver/uv.lock`.
- [ ] **T-1.3** `agent/providers/base.py` — `ChatProvider` protocol: `stream(messages, tools) -> AsyncIterator[ProviderEvent]` where events = `text_delta`, `tool_call(id, name, args_json)`, `done(stop_reason, usage)`. Shared schema translation helpers: our internal tool schema (`{name, description, input_schema}`) → OpenAI `tools[]`/`tool_choice` AND → Anthropic `tools[]` (with `input_schema`).
- [ ] **T-1.4** `agent/providers/openai.py` — POST `{base}/chat/completions`, `messages` + `tools`, SSE stream; assemble `tool_calls` deltas; parse usage/stop_reason. Handle `/v1/models`-style listing when `base_url` points at a local server.
- [ ] **T-1.5** `agent/providers/anthropic.py` — POST `{base}/messages` with `system`, `messages`, `tools`, `tool_choice`; SSE content-block events (`content_block_start`/`delta`/`stop`, `tool_use`); map Anthropic stop_reason; **auth header strategy**: real Anthropic = `x-api-key` + `anthropic-version`; Unsloth/llama local = `Authorization: Bearer <token>`. Config field to select.
- [ ] **T-1.6** `agent/providers/__init__.py` factory + model discovery helper (`GET {base}/v1/models` → model ids; degrade gracefully when absent).
- [ ] **T-1.7** Unit tests `driver/tests/agent/test_providers.py` — recorded/fixture SSE bodies (OpenAI + Anthropic) for: text streaming, single tool call, parallel tool calls, tool_use→tool_result continuation, auth header selection, `/v1/models` parse, error/timeout surfacing. No network.

## Phase 2 — SketchUp backend via the existing MCP server + file tools

Goal: agent gets SketchUp's 27 tools and workspace file access, additive to the MCP server.

- [ ] **T-2.1** `agent/sketchup_mcp.py` — thin **MCP client** (SDK `mcp.client.stdio`) that spawns the supex server the same way Claude Code does: command = repo `mcp` wrapper (resolved via `SUPEX_ROOT`/`SUPEX_WORKSPACE`, `SUPEX_AUTH_TOKEN` passthrough). On session init: `initialize`, then `tools/list` → our internal tool schema list (name/description/input_schema). Executes `tools/call` and returns `result.content` text.
- [ ] **T-2.2** Lazy singleton + reconnect semantics (server survives SketchUp closed); expose `list_tools()`, `call_tool(name, arguments)`; timeout + clean teardown on exit. Mirror `get_agent_name` so handshake reports e.g. `"supex-chat"` for logs.
- [ ] **T-2.3** `agent/file_tools.py` — workspace file tools registered into the same internal schema list: `read_file`, `write_file`, `edit_file` (find/replace), `list_dir`, `delete_file`. **Enforce workspace containment in Python** (resolve + symlink-aware prefix check against `SUPEX_WORKSPACE` + `SUPEX_ALLOWED_ROOTS`, mirroring runtime `path_policy.rb` semantics) so eval_ruby_file-relative paths stay in-bounds. Binary/image files flagged (see T-3.4). `delete_file`/destructive ops gated behind CLI confirm.
- [ ] **T-2.4** Unit tests `driver/tests/agent/test_sketchup_mcp.py` (against a **fake MCP server** — instantiate a stub `tools/list`/`tools/call` stdio server, or record against the real `su-mock` runtime) and `test_file_tools.py` (path policy: absolute-in-workspace ok, `..` escape denied, symlink escape denied, ALLOWED_ROOTS extension).
- [ ] **T-2.5** Manual verification script/procedure for the user on Windows (no SketchUp needed): `./sketch --check` lists tools from the MCP server and pings `:9876`.

## Phase 3 — Agent loop, system prompt, result handling

- [ ] **T-3.1** `agent/prompts.py` — assemble the **system prompt** from the repo's `docs/agents/guide/*.md` (README router + mcp.md + ruby.md + workflow.md + troubleshooting.md; resolve the `stdlib` symlink target; skip git-ignored `api/` generated docs by default). Resolve guide dir from `SUPEX_ROOT` so it works from any cwd. Add an agent-specific preamble explaining the file-based workflow, relative-path convention, and screenshot-read workflow.
- [ ] **T-3.2** `agent/loop.py` — core agentic loop: history in provider-neutral form → per-dialect message/tool-result encoding (OpenAI `role:"tool"` + `tool_call_id`; Anthropic `tool_use`/`tool_result` blocks) → stream model → execute tool calls via Phase 2 backends → append results → repeat until no tool call. Guards: `max_iterations`, context-length overflow handling, user interrupt.
- [ ] **T-3.3** Message/result size hygiene: tool results already JSON/compact; cap huge returns (mirror `SUPEX_MAX_RESPONSE` spirit); keep screenshots as paths, not pixels (matches tool design).
- [ ] **T-3.4** **Vision path:** when a tool result points at an image (screenshot png) under the workspace and the configured model is vision-capable, load the image and attach as a multimodal content block on the next model turn; otherwise tell the model the path exists but can't be viewed. Needs per-dialect content-block support for images (OpenAI `image_url` data URL; Anthropic `image` base64). Config flag `vision`.
- [ ] **T-3.5** Unit tests `driver/tests/agent/test_loop.py` — fake provider scripted to (a) answer directly, (b) request one tool then finish, (c) loop 3 tools, (d) hit `max_iterations`; verify message history encoding for both dialects is byte-correct; verify path/image result handling.
- [ ] **T-3.6** `agent/agent.py` — public `Agent` API tying config + providers + backends + loop + prompts; exposes `run_turn(user_text)` for embedding in CLI and future UIs.

## Phase 4 — Chat CLI + root wrapper + UX

- [ ] **T-4.1** `agent/cli.py` (Typer, consistent with `cli/main.py` style) — entry `supex-chat`. Subcommands/flags:
  - `supex-chat` → interactive session (multiline input; slash commands `/help`, `/model`, `/tools`, `/status`, `/reset`, `/exit`).
  - `--prompt "…"` / `-p` one-shot non-interactive run.
  - `--base-url`, `--api-key`, `--model`, `--dialect`, `--provider`-profile, `--list-models`.
  - `--check` diagnostics (SketchUp bridge via MCP server, model endpoint, tool count).
  - Respect existing output env (`SUPEX_PLAIN`/`SUPEX_COLOR`/TTY) and logging (`SUPEX_LOG_DIR` → e.g. `agent-chat.log`).
- [ ] **T-4.2** Rich rendering of model stream (markdown-ish streaming, minimal) + tool-call notices + error surfacing; Ctrl-C cancels current turn (keeps session).
- [ ] **T-4.3** Root wrapper `sketch` (bash, portable shebang, mirrors `mcp` wrapper: resolve `SUPEX_ROOT`, default `SUPEX_WORKSPACE=$(pwd)`, log dirs, `uv run --project "$SUPEX_ROOT/driver" supex-chat "$@"`). Add console entry `supex-chat = "supex_driver.agent.cli:main"` in `driver/pyproject.toml`.
- [ ] **T-4.4** Unit tests for CLI arg parsing/config resolution + slash-command routing (`driver/tests/agent/test_cli.py`); no live I/O.

## Phase 5 — Config profiles, robustness, real-endpoint hardening

- [ ] **T-5.1** Optional per-project config file (git-ignored, e.g. `.open-sketch/settings.json` or reuse `.mcp.json`-adjacent convention) to store named **provider profiles** (OpenAI, Azure, OpenRouter, Groq, Ollama, LM Studio, vLLM, Unsloth) with `base_url`/`api_key`/`model`/`dialect`; secrets never committed (`docs/security.md` note + `.gitignore`). Precedence: flags > env > profiles file.
- [ ] **T-5.2** Retries/backoff for provider HTTP + SSE reconnect on mid-stream drop (mirror `docs/configuration.md` `SUPEX_RETRIES`); distinguish "model unreachable" vs "SketchUp unreachable" in errors and in `--check`.
- [ ] **T-5.3** Unsloth-Desktop-specific UX polish: `--list-models` via `GET /v1/models`, accept both `sk-unsloth-…` (Bearer) and Anthropic headers, sane defaults for `http://localhost:8000`/`:8888`. Document exact user steps (create key in Settings→API).
- [ ] **T-5.4** Token/context management: basic token accounting from provider usage, auto-compact or guided `/reset` when nearing the model context window.
- [ ] **T-5.5** Security pass: never log api_key; treat model/tool output as untrusted data (per root `AGENTS.md` §36.9/§55 mindset — SketchUp can execute arbitrary Ruby, so provider output must never be auto-executed beyond the tool loop the user is watching); confirm-before-destructive conventions.

## Phase 6 — Docs, polish, release prep

- [ ] **T-6.1** `docs/agent.md` (or extend `docs/cli.md`): what the chat agent is, provider config reference, dialect table, Unsloth quickstart, OpenAI/Azure/OpenRouter/Ollama/LM Studio examples, `./sketch` usage, security notes.
- [ ] **T-6.2** Update `docs/configuration.md` env table with all new `SUPEX_AI_*`/agent vars + log files. Update `driver/README.md` tool counts/commands. Update root `README.md` feature/install text to mention the provider-agnostic chat agent (do NOT overclaim; keep Claude Code as a supported client).
- [ ] **T-6.3** Update project-structure notes in `AGENTS.md` (new `driver/src/supex_driver/agent/`, wrapper) only if the repo AGENTS structure section stays accurate.
- [ ] **T-6.4** Test inventory parity: extend `driver/tests/test_tool_inventory.py`-style check so agent-visible tool docs stay in sync; add `agent` suite slug to `scripts/launch-test.sh` and CI if CI is driven by it.
- [ ] **T-6.5** Full lint + typecheck sweep (`ruff`, `mypy`, per `scripts/lint.sh`) and full driver test suite in CI image; manual E2E on the user's Windows machine (see Verification).
- [ ] **T-6.6** Version bump + changelog ONLY when the user explicitly asks (repo `AGENTS.md`: never bump without being asked). Prep a `v0.4.0`-style release branch/tag flow per `scripts/release.sh` when user greenlights.

---

## Verification (how each chunk is proven)

| Chunk | Local (this box) | User machine (Windows + SketchUp 2026) |
|---|---|---|
| Phase 1 providers | Unit tests vs recorded fixtures (`pytest`) | `./sketch --list-models` against Unsloth/OpenAI |
| Phase 2 backend+files | Unit tests vs fake MCP server + `su-mock` runtime | `./sketch --check` shows 27 tools + bridge connected |
| Phase 3 loop | Unit tests (scripted provider) | Run `./sketch`, prompt `create a cube and screenshot it` |
| Phase 4 CLI | Arg/slash-command tests | Interactive session; `/tools`, `/status`, `/model` |
| Phase 5 hardening | Security/backoff unit tests | Real Unsloth + a cloud OpenAI-compatible endpoint |
| Phase 6 release | `./scripts/lint.sh`, full driver suite in CI image | E2E: full Ruby and VCAD workflow via a local model |

## Risks / open questions

- **Tool-calling reliability on small local models** (Unsloth GGUF) is the biggest unknown; document fallbacks (verbatim JSON mode prompt, `tool_choice`, smaller tool set) — Phase 1/3 should keep the loop resilient to malformed tool calls (retry once, then surface).
- **New dependency** (`httpx` vs stdlib SSE) touches `driver/uv.lock` — decide early (T-1.2) per the decision ladder.
- **Guide content as system prompt** may be large; Phase 3 must budget/curate which guide files are always loaded vs. on-demand (respect `AGENTS.md` context-economy rules).
- **Vision** support varies wildly across local models; keep it strictly opt-in and degrade to "path only".
- **Repo governance**: root `AGENTS.md` rules (no version bumps, no GitHub automation without approval, identity = `peva3`, dev branch) apply throughout.

## Out of scope (for now)

- Converting the MCP server to HTTP/streamable transport so Unsloth *itself* could be the MCP host — possible later if users want Unsloth's built-in MCP UI to attach to SketchUp.
- Shell/terminal tool for the agent, agentic web search, or a GUI.
- Modifying the SketchUp Ruby runtime or VCAD sidecar behavior.
