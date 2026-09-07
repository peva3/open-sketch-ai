# Supex Chat Agent

`supex-chat` is a provider-agnostic agent that talks to almost any AI model
endpoint and uses the result to drive SketchUp through the same MCP server
Claude Code uses. Unlike Claude Code, it is not tied to one model vendor: any
OpenAI-compatible or Anthropic-compatible API works, including local servers
such as Unsloth Desktop.

There are two ways to interact with it:

- **Windowed chat app** (`supex-chat serve`) — a local web page on
  `127.0.0.1` that streams the agent's work as it happens.
- **Terminal session** (`supex-chat`, interactive or `--prompt`) — the same
  agent loop in your terminal.

Both share one `Agent` loop (see `driver/src/supex_driver/agent/agent.py`), so
behavior is identical across windows and terminals.

## How it works

```
you (chat window / terminal)
        |
        v
supex-chat agent ---------- model endpoint (OpenAI / Anthropic dialect)
        |                          base_url + api_key + model
        |   authors .rb / .cmp.oo sources in the workspace
        v
Supex MCP server (python -m supex_driver)      <-- same server Claude Code uses
        |
        v
SketchUp Ruby runtime  -- edits land live in the running SketchUp model
```

The agent is given the tools Claude Code gets (the 27 MCP tools) plus five
workspace file tools (`read_file`, `write_file`, `edit_file`, `list_dir`,
`delete_file`). It authors Ruby (`.rb`) or VCAD (`.cmp.oo`) sources in your
workspace and runs them via `eval_ruby_file`, so geometry appears in SketchUp
with no import/export round-trips. After making a change it takes a screenshot;
when vision is enabled it reads the PNG and attaches it to the next model turn
so the model can visually verify and self-correct.

## Requirements

- SketchUp with the Supex Runtime extension running (see the project
  [README](../README.md)).
- The repo is checked out (`supex-chat` runs the MCP backend from source via
  `uv run`), or a self-contained build (see `driver/packaging/`).

## Quick start (windowed)

```bash
cd /path/to/your/sketchup-project
/path/to/supex/sketch serve
```

`sketch serve` starts the agent server and opens
`http://127.0.0.1:8765` in your default browser. Ask for something — "add a
2m x 1m table with four legs at these coordinates" — and watch the model write
and run Ruby against the live SketchUp model.

If no provider configuration is set, the agent needs a model endpoint. The
simplest path is environment variables or a provider profile (next section).

## Configuration

Resolution precedence (highest first):

1. CLI flags (`--model`, `--base-url`, `--api-key`, ...)
2. `SUPEX_AI_*` environment variables
3. Provider-standard variables (`OPENAI_*` / `ANTHROPIC_*`)
4. A named provider profile (`--provider-profile`, or `SUPEX_AI_PROFILE`)
5. Built-in defaults

### Endpoint flags

All entry points accept the same endpoint options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--base-url` | dialect default | API base URL, e.g. `https://api.openai.com/v1`, `https://api.anthropic.com`, or `http://localhost:8000` for Unsloth Desktop |
| `--api-key` | none | API key (never logged) |
| `--model` | **required** | Model identifier, e.g. `gpt-5`, `claude-sonnet-4-5`, `qwen3-local` |
| `--dialect` | `auto` | `auto`, `openai`, or `anthropic`. `auto` sniffs the URL (`anthropic` / `/v1/messages`), then the key shape (`sk-ant-...`) |

### Dialect table

| Dialect | Endpoint | Streaming body | Auth header |
|---------|----------|----------------|-------------|
| `openai` | `{base}/chat/completions` | `data:` SSE lines | `Authorization: Bearer <key>` |
| `anthropic` | `{base}/v1/messages` | `data:` SSE events | `x-api-key` for `api.anthropic.com`, otherwise Bearer |

Because the dialect is selected per endpoint, the same key and `base_url`
behave correctly for both vendor clouds and local servers that speak one of
the two dialects.

### Behavior flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--timeout` | `60` | Request timeout in seconds |
| `--temperature` | unset | Sampling temperature (provider default when unset) |
| `--max-tokens` | unset | Max output tokens (Anthropic default `4096`) |
| `--max-iterations` | `10` | Max tool-call iterations per prompt |
| `--vision` / `--no-vision` | off | When on, the agent reads screenshot PNGs and attaches them to the next model turn |
| `--allow-delete` | off | Permit the `delete_file` workspace tool (off by default) |

### Environment variables

See [Configuration](configuration.md) for the full `SUPEX_AI_*` table. The
most common are:

```bash
export SUPEX_AI_BASE_URL=https://api.openai.com/v1
export SUPEX_AI_API_KEY=sk-...
export SUPEX_AI_MODEL=gpt-5
export SUPEX_AI_VISION=1
```

Provider-standard variables are also honored when the matching dialect group
is selected: `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL` and
`ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` (Anthropic also
accepts `ANTHROPIC_AUTH_TOKEN`).

### Provider profiles

Instead of typing flags or exporting variables each time, define named
profiles in `profiles.toml` under the per-OS user config directory:

| OS | Path |
|----|------|
| Linux | `$XDG_CONFIG_HOME/supex-chat/profiles.toml` (default `~/.config/supex-chat/profiles.toml`) |
| macOS | `~/Library/Application Support/supex-chat/profiles.toml` |
| Windows | `%APPDATA%\supex-chat\profiles.toml` |

Override the directory with `SUPEX_AI_CONFIG_DIR`.

```toml
# Optional: profile used when none is requested.
default = "openai"

[profiles.openai]
base_url = "https://api.openai.com/v1"
api_key = "sk-..."
model = "gpt-5"
dialect = "openai"

[profiles.unsloth]
base_url = "http://localhost:8000"
api_key = "sk-unsloth-..."
model = "qwen3-local"
```

Select a profile with `--provider-profile openai` or `SUPEX_AI_PROFILE=openai`.
Profiles are the weakest source: any flag or environment variable wins.

## Unsloth Desktop (local models)

[Unsloth Desktop](https://unsloth.ai) runs local GGUF models and exposes them
over a local API on one port (commonly `http://localhost:8000`), speaking both
the Anthropic and OpenAI dialects on that single endpoint.

1. In Unsloth Desktop, open **Settings -> API** and create an API key
   (usually shown as `sk-unsloth-...`). Unsloth acts as an Anthropic-flavored
   endpoint, but the OpenAI dialect also works.
2. Load a model in Unsloth Desktop.
3. Point `supex-chat` at it:

```bash
/path/to/supex/sketch --base-url http://localhost:8000 \
    --api-key sk-unsloth-... \
    --model qwen3-local \
    --list-models        # optional: print the model IDs Unsloth exposes
```

or use a profile (above). `--dialect auto` detects the flavor from the URL;
pass `--dialect anthropic` or `--dialect openai` if your local server needs a
specific one.

Then start a session:

```bash
/path/to/supex/sketch serve
# or terminal:
/path/to/supex/sketch --base-url http://localhost:8000 --api-key sk-unsloth-... --model qwen3-local
```

Small local models are less reliable at tool calling than frontier models. If
the agent stops acting, try `--temperature 0` for more deterministic tool
selection, keep prompts concrete, and expect to retry phrasing.

## Other OpenAI-compatible servers

Any server that implements the OpenAI chat-completions API works with the same
flags — only `base_url` (and model id) changes. Common examples:

| Provider | `--base-url` | `--model` example |
|----------|--------------|-------------------|
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/v1` | your deployment name |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4.5` |
| Ollama | `http://localhost:11434/v1` | `llama3.3` |
| LM Studio | `http://localhost:1234/v1` | `qwen2.5-7b-instruct` |

When both `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` are exported the OpenAI
group wins; set `SUPEX_AI_DIALECT` (or a profile) to disambiguate.

## Terminal usage

```bash
# Interactive session
./sketch

# One-shot prompt
./sketch --prompt "create a staircase with 15 steps"

# One-shot with an explicit endpoint
./sketch --base-url https://api.openai.com/v1 \
    --api-key sk-... --model gpt-5 \
    --prompt "add a door on the north wall"

# Health check of the model endpoint and the SketchUp backend
./sketch --check
```

### Slash commands

| Command | Meaning |
|---------|---------|
| `/help` | Show help text |
| `/model` | Show the configured endpoint |
| `/tools` | List the tools available to the agent |
| `/status` | Backend tool count, `check_status` output, session token usage, message count |
| `/reset` | Clear the conversation (frees the context window) |
| `/exit` | End the session |

## Security

- The API key is never logged, printed, or surfaced in any health endpoint;
  `ProviderConfig` excludes it from its repr.
- Model output is treated as untrusted data. The only thing executed from a
  model response is a tool-call request in the visible loop; model text is
  never evaluated. Terminal banners escape rich-markup control sequences.
- Workspace file tools are contained to the workspace plus any
  `SUPEX_ALLOWED_ROOTS`, resolve symlinks to prevent escape, and
  `delete_file` is disabled unless you pass `--allow-delete`.
- The SketchUp Ruby runtime executes arbitrary Ruby (as it does for Claude
  Code); path containment is a guardrail, not a sandbox. See
  [Security](security.md). Only run the agent against SketchUp models you
  trust.

## In-SketchUp embedding (roadmap)

The chat window is a plain web page served on `127.0.0.1`, so the same URL can
later be embedded inside SketchUp itself via `UI::HtmlDialog`. That is a
staged additive milestone; tracked in `TODO.md` (T-4.7).
