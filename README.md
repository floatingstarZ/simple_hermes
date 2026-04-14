# Simple Hermes Codex

Simple Hermes Codex is a tiny educational project that recreates the core ideas behind Hermes in a form a beginner can read in one sitting.

It is intentionally not feature-complete.
It focuses on the architecture, not production power.

What it includes
- A minimal but real iterative agent loop
- A tiny tool registry
- A few built-in tools
- Split persistent memory (general memory + user profile memory)
- Persistent session history with lightweight metadata
- Tiny child-session lineage for delegation
- FTS-style recall/search skeleton with focused recall summary output
- Configurable permissions with permissive defaults (all permissions enabled unless you restrict them)
- Safe terminal command execution in the project root
- Basic project-relative file writing and patching tools
- A local CLI with a cleaner interactive UI
- A small automated test suite
- Optional real LLM planning via an OpenAI-compatible backend
- Optional Hermes runtime bridge to reuse existing Hermes auth/provider resolution
- A tiny educational compression step
- A tiny educational delegation mechanism with child-agent summary return
- Tiny delegation depth/budget control, safe parallel delegation, child tool restriction, and a minimal sensitive-file / approval guard

What it does not include
- Browser automation
- Gateway/platform adapters
- MCP
- Subagents
- Full Cron system
- Production-grade context compression
- Provider fallback orchestration in the full Hermes sense

Why this project exists
- To show the shape of Hermes without 1000+ files
- To make the core abstractions understandable
- To give you a sandbox for experimentation

Project layout
- simple_hermes/agent/core.py       Minimal iterative agent loop + continuity/delegation skeleton
- simple_hermes/agent/backend.py    Optional OpenAI-compatible planner backend + Hermes runtime bridge
- simple_hermes/agent/prompting.py  Tiny planner prompt builder
- simple_hermes/tools/registry.py   Tiny tool registry
- simple_hermes/tools/builtin.py    Built-in tools + tiny governance hooks
- simple_hermes/state/memory.py     Split persistent memory store
- simple_hermes/state/session.py    SQLite-backed session history and raw persistence
- simple_hermes/state/continuity.py Continuity/browser helpers (lineage, recent sessions, descendants)
- simple_hermes/cli.py              REPL CLI with panels, tips, trace view, status view
- simple_hermes/config.py           Paths and app settings
- tests/                            Verification suite

Compatibility shims kept for simplicity
- simple_hermes/agent.py
- simple_hermes/backend.py
- simple_hermes/prompting.py
- simple_hermes/tools.py
- simple_hermes/memory.py
- simple_hermes/session.py

How it maps to full Hermes
- AIAgent                -> simple_hermes.agent.SimpleAgent
- tools/registry.py      -> simple_hermes.tools.ToolRegistry
- memory_tool.py         -> simple_hermes.memory.MemoryStore
- hermes_state.py        -> simple_hermes.session.SessionStore
- prompt_builder.py      -> simple_hermes.prompting.build_planner_prompt
- provider client layer  -> simple_hermes.backend.OpenAICompatibleBackend
- runtime_provider.py    -> simple_hermes.backend hermes-runtime bridge
- cli.py                 -> simple_hermes.cli

How to install and run
1. cd /Users/hzy/Desktop/work/simple_hermes_codex
2. Install a small wrapper at `/Users/hzy/.local/bin/simple_hermes_codex` that points to this checkout and reuses the main Hermes venv/runtime
3. simple_hermes_codex

Packaging note
- The Python package metadata also exposes the `simple_hermes_codex` console script for venv/pipx-style installs.
- The local machine uses the wrapper approach to avoid Homebrew Python's externally-managed-environment restriction.

How to test
1. cd /Users/hzy/Desktop/work/simple_hermes_codex
2. python3 -m unittest discover -s tests -v

UI shortcuts
- `/help`   show UI-level help
- `/tips`   show example prompts
- `/status` show memory/session/backend status
- `/trace`  show the last agent trace
- `/clear`  clear the screen and redraw the banner
- `lineage` show session lineage
- `sessions` show recent sessions
- `descendants` show descendant sessions for the current session
- `help`    ask the tool layer for tool help
- `exit`    quit

Input UX note
- If `prompt_toolkit` is available (as it is in the main Hermes environment), Simple Hermes now uses it for input editing
- That gives proper backspace behavior, cursor movement, and persistent input history
- If `prompt_toolkit` is unavailable, it falls back to plain `input()`

Planner modes
1. Rule-based mode (default fallback)
- No API key required
- Best for learning the control flow

Approval note
- You can require approval for delegation-like operations with:
  - `SIMPLE_HERMES_REQUIRE_APPROVAL=delegate`
  - `SIMPLE_HERMES_REQUIRE_APPROVAL=parallel_delegate`
  - or `SIMPLE_HERMES_REQUIRE_APPROVAL=all`

Permission note
- Defaults are permissive: if you set nothing, Simple Hermes allows outside-project reads/writes, sensitive reads/writes, and dangerous terminal commands.
- You can switch to a restrictive preset with `SIMPLE_HERMES_PERMISSION_PROFILE=restricted`.
- Or override individual flags with:
  - `SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_READS=0|1`
  - `SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_WRITES=0|1`
  - `SIMPLE_HERMES_ALLOW_SENSITIVE_READS=0|1`
  - `SIMPLE_HERMES_ALLOW_SENSITIVE_WRITES=0|1`
  - `SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL=0|1`
- Tool allowlists are also configurable:
  - `SIMPLE_HERMES_ALLOWED_TOOLS=help,read,terminal`
  - `SIMPLE_HERMES_CHILD_ALLOWED_TOOLS=help,read` or `safe`

2. Real LLM mode (manual OpenAI-compatible config)
Set:
- `SIMPLE_HERMES_BACKEND=openai`
- `SIMPLE_HERMES_BASE_URL=<your openai-compatible base url>`
- `SIMPLE_HERMES_API_KEY=<your api key>`
- `SIMPLE_HERMES_MODEL=<model name>`
- Optional: `SIMPLE_HERMES_API_MODE=chat_completions` or `codex_responses`

3. Hermes runtime bridge mode
Set:
- `SIMPLE_HERMES_BACKEND=hermes-runtime`
- Optional: `SIMPLE_HERMES_PROVIDER=<provider>`
- Optional: `SIMPLE_HERMES_MODEL=<model>`
- Optional: `SIMPLE_HERMES_HERMES_ROOT=/path/to/hermes-agent`

This mode tries to reuse the original Hermes runtime provider resolution, so existing Hermes auth flows and provider setup can be borrowed.

Behavior note
Even in real backend mode, explicit command-shaped inputs such as:
- `read README.md`
- `search something`
- `remember ...`
- `terminal python3 -m unittest discover -s tests -v`
- `write_file notes.txt hello`
- `patch_file notes.txt\nhello\n---\nhello again`

still go directly to tools.
This keeps the teaching project predictable.

Why it now counts as a real minimal agent
Because it has:
- state (memory + session store)
- planning (PlannerDecision)
- action (tool registry)
- iteration (multi-step backend loop)
- continuity (history + tiny compression)

Suggested things to try
- hi
- /help
- /status
- remember project uses sqlite
- remember_user I prefer concise answers
- memories
- user_memories
- read pyproject.toml
- search SimpleAgent
- terminal python3 -m unittest discover -s tests -v
- write_file scratch.txt hello
- patch_file scratch.txt\nhello\n---\nhello again
- history
- sessions
- lineage
- descendants
- recall lineage
- delegate read README.md
- parallel_delegate read README.md ; summarize project
- /trace
- summarize this project

Example session
> hi
> /status
> remember project uses sqlite
> remember_user I prefer diagrams
> memories
> /trace

Design note
The project supports three teaching modes:
- rule-based planning
- direct OpenAI-compatible backend mode
- Hermes runtime bridge mode

That split is deliberate. It lets you learn the architecture first, then turn on a true backend later.
