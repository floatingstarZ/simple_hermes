from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from .agent import AgentTraceStep, SimpleAgent
from .config import APP_NAME, BASE_DIR

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PTStyle
except Exception:  # pragma: no cover - fallback path when prompt_toolkit is unavailable
    PromptSession = None
    FileHistory = None
    PTStyle = None

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
BLUE = "\033[34m"


def _term_width(default: int = 88) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _rule(char: str = "─") -> str:
    return char * min(_term_width(), 88)


def _panel(title: str, body: str, color: str = CYAN) -> str:
    lines = body.splitlines() or [""]
    width = min(max(len(title) + 8, *(len(line) for line in lines)) + 4, 88)
    top = f"{color}┌{'─' * (width - 2)}┐{RESET}"
    header = f"{color}│ {BOLD}{title}{RESET}{color}{' ' * max(0, width - len(title) - 4)}│{RESET}"
    sep = f"{color}├{'─' * (width - 2)}┤{RESET}"
    body_lines = [f"{color}│{RESET} {line}{' ' * max(0, width - len(line) - 3)}{color}│{RESET}" for line in lines]
    bottom = f"{color}└{'─' * (width - 2)}┘{RESET}"
    return "\n".join([top, header, sep, *body_lines, bottom])


def _banner(mode: str) -> str:
    body = (
        f"{BOLD}Simple Hermes Codex{RESET}\n"
        f"A tiny Codex-focused educational version of Hermes.\n"
        f"Planner mode: {GREEN if mode == 'real-llm' else YELLOW}{mode}{RESET}\n"
        f"{DIM}Type /help for UI commands, help for tool help, exit to quit.{RESET}"
    )
    return _panel("Welcome", body, color=BLUE)


def _tips() -> str:
    return _panel(
        "Quick start",
        "Try these:\n"
        "- hi\n"
        "- help\n"
        "- remember project uses sqlite\n"
        "- remember_user I prefer diagrams\n"
        "- history\n"
        "- read README.md\n"
        "- /trace\n"
        "- /status\n"
        "- /resume\n"
        "- /rename active coding session\n"
        "- /checkpoint\n"
        "- /background run a quick check",
        color=MAGENTA,
    )


def _ui_help() -> str:
    return _panel(
        "UI commands",
        "/help      Show this UI help\n"
        "/status    Show runtime/project/git/session/task status\n"
        "/trace     Show the last agent trace\n"
        "/resume    List or switch sessions: /resume <id|number|latest|project>\n"
        "/rename    Rename the current session: /rename <title>\n"
        "/new       Start a fresh session: /new [title]\n"
        "/reset     Clear current session history and active state\n"
        "/compress  Manually compress current session into a continuation\n"
        "/usage     Show approximate session context usage\n"
        "/tool-results Show recent stored tool results\n"
        "/sessions  List recent sessions\n"
        "/model     Show or switch runtime model: /model [model]\n"
        "/checkpoint Create/list project checkpoints\n"
        "/rollback  Restore a checkpoint: /rollback [id|latest]\n"
        "/undo      Restore the latest checkpoint\n"
        "/retry     Retry the last user message\n"
        "/background Run or inspect a background agent task\n"
        "/tips      Show example prompts\n"
        "/clear     Clear the screen\n"
        "lineage    Show session lineage\n"
        "sessions   Show recent sessions\n"
        "descendants Show descendant sessions\n"
        "help       Show tool help\n"
        "exit       Quit Simple Hermes Codex",
        color=CYAN,
    )


def _backend_summary(agent: SimpleAgent, mode: str) -> str:
    if agent.backend is None:
        return f"{mode} / rule-based fallback"
    backend_name = agent.backend.__class__.__name__
    model = getattr(agent.backend, "model", None)
    if model:
        return f"{mode} / {backend_name} / model={model}"
    return f"{mode} / {backend_name}"


def _active_task_summary(agent: SimpleAgent) -> str:
    task = agent._load_active_task()
    if not task:
        return "-"
    task_id = task.get("id") or "unknown"
    status = task.get("status") or "unknown"
    goal = _middle_truncate(str(task.get("goal") or ""), 48) or "(no goal)"
    return f"{task_id} [{status}] {goal}"


def _git_summary(project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=3,
        )
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"
    if completed.returncode != 0:
        return "not a git repository"
    lines = completed.stdout.splitlines()
    branch = lines[0].removeprefix("## ").strip() if lines else "unknown"
    dirty = len(lines[1:])
    return f"{branch}; dirty files={dirty}"


def _status(agent: SimpleAgent, mode: str) -> str:
    info = agent.sessions.session_info(agent.session_id) or {}
    default_session = _default_session_id(agent.project_root)
    background_count = len(getattr(agent, "_background_agent_tasks", {}))
    body = (
        f"Runtime: {_backend_summary(agent, mode)}\n"
        f"Project root: {agent.project_root}\n"
        f"Git: {_git_summary(agent.project_root)}\n"
        f"Session id: {agent.session_id}\n"
        f"Default project session: {default_session}\n"
        f"Session title: {info.get('title') or '-'}\n"
        f"Session type: {info.get('session_type', 'unknown')}\n"
        f"Parent session: {info.get('parent_session_id') or '-'}\n"
        f"Active task: {_active_task_summary(agent)}\n"
        f"Background agents: {background_count}\n"
        f"General memories: {agent.memory.general_count()}\n"
        f"User memories: {agent.memory.user_count()}\n"
        f"Session messages: {agent.sessions.message_count(agent.session_id)}\n"
        f"Max agent steps: {agent.max_steps}\n"
        f"Compression threshold: {agent._compression_threshold()}"
    )
    return _panel("Status", body, color=GREEN)


def _format_trace(agent: SimpleAgent) -> str:
    trace = getattr(agent, "last_trace", [])
    if not trace:
        return _panel("Trace", "No trace yet. Ask something first.", color=YELLOW)
    lines = []
    for item in trace:
        tool_suffix = f" [{item.tool_name}]" if item.tool_name else ""
        preview = item.content.replace("\n", "\\n")
        if len(preview) > 220:
            preview = preview[:220] + "..."
        lines.append(f"step {item.step}: {item.kind}{tool_suffix} -> {preview}")
    return _panel("Last trace", "\n".join(lines), color=YELLOW)


def _middle_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    head = (limit - 3) // 2
    tail = limit - 3 - head
    return f"{text[:head]}...{text[-tail:]}"


def _format_recent_sessions(agent: SimpleAgent, limit: int = 12) -> str:
    rows = agent.sessions.recent_sessions(limit=limit)
    if not rows:
        return "No sessions yet."
    lines = ["Recent sessions:"]
    for index, row in enumerate(rows, start=1):
        marker = "*" if row["id"] == agent.session_id else " "
        session_id = _middle_truncate(row["id"], 34)
        title = _middle_truncate(row.get("title") or "(untitled)", 28)
        session_type = _middle_truncate(row.get("session_type") or "unknown", 12)
        parent = _middle_truncate(row.get("parent_session_id") or "-", 44)
        lines.append(f"{index:>2}. {marker} {session_id} [{session_type}] title={title}")
        lines.append(f"    parent={parent}")
    return "\n".join(lines)


def _resume_session(agent: SimpleAgent, arg: str) -> str:
    target = arg.strip()
    rows = agent.sessions.recent_sessions(limit=20)
    if not target:
        body = (
            "Usage: /resume <session-id|number|latest|project>\n"
            "- /resume 2 switches to the second recent session.\n"
            "- /resume latest switches to the most recently touched session.\n"
            "- /resume project switches to this project's latest continuation.\n\n"
            + _format_recent_sessions(agent, limit=12)
        )
        return _panel("Resume", body, color=CYAN)

    if target.isdigit():
        index = int(target)
        if index < 1 or index > len(rows):
            return _panel("Resume", f"No recent session at index {index}.", color=YELLOW)
        target = rows[index - 1]["id"]
    elif target.lower() == "latest":
        if not rows:
            return _panel("Resume", "No sessions to resume.", color=YELLOW)
        target = rows[0]["id"]
    elif target.lower() == "project":
        target = agent.sessions.latest_continuation_or_self(_default_session_id(agent.project_root))

    info = agent.sessions.session_info(target)
    if info is None:
        return _panel("Resume", f"No session found: {target}", color=YELLOW)

    agent.session_id = target
    agent.last_trace = []
    body = (
        f"Resumed session: {target}\n"
        f"Title: {info.get('title') or '(untitled)'}\n"
        f"Type: {info.get('session_type') or 'unknown'}\n"
        f"Messages: {agent.sessions.message_count(target)}"
    )
    return _panel("Resume", body, color=GREEN)


def _rename_session(agent: SimpleAgent, title: str) -> str:
    title = title.strip()
    if not title:
        return _panel("Rename", "Usage: /rename <title>", color=YELLOW)
    agent.sessions.rename_session(agent.session_id, title)
    return _panel("Rename", f"Renamed current session:\n{agent.session_id}\nTitle: {title}", color=GREEN)


def _new_session(agent: SimpleAgent, title: str) -> str:
    title = title.strip() or "new session"
    session_id = f"{_default_session_id(agent.project_root)}/chat-{uuid.uuid4().hex[:10]}"
    agent.sessions.ensure_session(session_id, title=title, session_type="root")
    agent.session_id = session_id
    agent.last_trace = []
    return _panel("New Session", f"Started session:\n{session_id}\nTitle: {title}", color=GREEN)


def _reset_session(agent: SimpleAgent) -> str:
    agent.sessions.clear_session_messages(agent.session_id)
    agent.last_trace = []
    return _panel("Reset", f"Cleared messages and active state for:\n{agent.session_id}", color=YELLOW)


def _usage(agent: SimpleAgent) -> str:
    usage = agent.sessions.usage_summary(agent.session_id)
    body = (
        f"Session id: {agent.session_id}\n"
        f"Messages: {usage['message_count']}\n"
        f"Characters: {usage['char_count']}\n"
        f"Estimated tokens: {usage['estimated_tokens']}\n"
        f"Tool-result chars: {usage['tool_result_chars']}\n"
        f"Summary chars: {usage['summary_chars']}"
    )
    return _panel("Usage", body, color=CYAN)


def _tool_results(agent: SimpleAgent, arg: str) -> str:
    raw = arg.strip()
    limit = 8
    if raw.isdigit():
        limit = max(1, min(int(raw), 50))
    rows = agent.sessions.recent_tool_results(agent.session_id, limit=limit)
    if not rows:
        return _panel("Tool Results", "No stored tool results in this session.", color=YELLOW)
    lines = ["Recent stored tool results:"]
    for row in rows:
        content = _middle_truncate(row.get("content") or "", 180)
        lines.append(f"- #{row['id']} {row.get('tool_name') or 'tool'}: {content}")
    return _panel("Tool Results", "\n".join(lines), color=CYAN)


def _model(agent: SimpleAgent, arg: str) -> str:
    model = getattr(agent.backend, "model", None) if agent.backend is not None else None
    requested = arg.strip()
    if not requested:
        if model:
            return _panel("Model", f"Current model: {model}", color=CYAN)
        return _panel("Model", "No live backend is configured; rule-based mode has no model.", color=YELLOW)
    if agent.backend is None or not hasattr(agent.backend, "model"):
        return _panel("Model", "Cannot switch model because no live backend is configured.", color=YELLOW)
    setattr(agent.backend, "model", requested)
    os.environ["SIMPLE_HERMES_MODEL"] = requested
    return _panel("Model", f"Switched runtime model to: {requested}", color=GREEN)


def _checkpoint(agent: SimpleAgent, arg: str) -> str:
    text = agent.tools.run("checkpoint", arg.strip() or "create")
    return _panel("Checkpoint", text, color=GREEN if "Created" in text else CYAN)


def _rollback(agent: SimpleAgent, arg: str) -> str:
    text = agent.tools.run("rollback", arg.strip() or "latest")
    return _panel("Rollback", text, color=YELLOW)


def _compress(agent: SimpleAgent) -> str:
    return _panel("Compress", agent.compress_now(), color=GREEN)


def _background(agent: SimpleAgent, arg: str) -> str:
    raw = arg.strip()
    if not raw or raw in {"list", "ls"}:
        return _panel("Background", agent.background_agents_text(), color=CYAN)
    action, _, rest = raw.partition(" ")
    if action == "status":
        return _panel("Background", agent.background_agent_status(rest.strip()), color=CYAN)
    if action == "wait":
        parts = rest.split()
        if not parts:
            return _panel("Background", "Usage: /background wait <id> [seconds]", color=YELLOW)
        timeout = None
        if len(parts) > 1:
            try:
                timeout = max(0.1, min(float(parts[1]), 3600.0))
            except ValueError:
                return _panel("Background", "Usage: /background wait <id> [seconds]", color=YELLOW)
        return _panel("Background", agent.wait_background_agent(parts[0], timeout=timeout), color=GREEN)
    return _panel("Background", agent.start_background_agent(raw), color=GREEN)


def _preview_step_content(content: str, limit: int = 180) -> str:
    preview = " ".join(content.replace("\n", " ").split())
    if len(preview) > limit:
        return preview[: max(0, limit - 3)] + "..."
    return preview


def _step_color(kind: str) -> str:
    if "error" in kind:
        return RED
    if "blocked" in kind:
        return YELLOW
    if kind == "tool_result":
        return GREEN
    if kind == "tool_call":
        return CYAN
    if kind == "text":
        return GREEN
    if kind.startswith("task_frame"):
        return MAGENTA
    return DIM


def _format_stream_step(item: AgentTraceStep) -> str:
    tool_suffix = f" [{item.tool_name}]" if item.tool_name else ""
    preview = _preview_step_content(item.content)
    color = _step_color(item.kind)
    label = f"step {item.step:02d} · {item.kind}{tool_suffix}"
    if preview:
        return f"{DIM}│{RESET} {color}{label}{RESET} {DIM}{preview}{RESET}"
    return f"{DIM}│{RESET} {color}{label}{RESET}"


def _stream_start(mode: str) -> None:
    print(f"{DIM}┌─ progress · {mode}{RESET}", flush=True)


def _stream_step(item: AgentTraceStep) -> None:
    print(_format_stream_step(item), flush=True)


def _stream_end() -> None:
    print(f"{DIM}└─ done{RESET}", flush=True)


def _run_agent_turn(agent: SimpleAgent, mode: str, message: str) -> None:
    _stream_start(mode)
    result = agent.run(message, on_step=_stream_step)
    _stream_end()
    agent.last_trace = result.trace
    title = f"Assistant · {mode}"
    print(_panel(title, result.final_response, color=GREEN if result.tool_used else CYAN))
    if result.tool_used:
        meta = f"tool used: {result.tool_used} | steps: {result.steps}"
    else:
        meta = f"steps: {result.steps}"
    print(f"{DIM}{meta}{RESET}\n")


def _retry_last_user(agent: SimpleAgent, mode: str) -> str | None:
    last = agent.sessions.last_user_message(agent.session_id)
    if not last:
        return _panel("Retry", "No previous user message in this session.", color=YELLOW)
    _run_agent_turn(agent, mode, last)
    return None


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _handle_ui_command(message: str, agent: SimpleAgent, mode: str) -> bool:
    stripped = message.strip()
    cmd = stripped.lower()
    if cmd == "/help":
        print(_ui_help())
        return True
    if cmd == "/tips":
        print(_tips())
        return True
    if cmd == "/status":
        print(_status(agent, mode))
        return True
    if cmd == "/trace":
        print(_format_trace(agent))
        return True
    if cmd == "/resume" or cmd.startswith("/resume "):
        print(_resume_session(agent, stripped[len("/resume"):]))
        return True
    if cmd == "/rename" or cmd.startswith("/rename "):
        print(_rename_session(agent, stripped[len("/rename"):]))
        return True
    if cmd == "/new" or cmd.startswith("/new "):
        print(_new_session(agent, stripped[len("/new"):]))
        return True
    if cmd == "/reset":
        print(_reset_session(agent))
        return True
    if cmd == "/compress":
        print(_compress(agent))
        return True
    if cmd == "/usage":
        print(_usage(agent))
        return True
    if cmd == "/tool-results" or cmd.startswith("/tool-results "):
        print(_tool_results(agent, stripped[len("/tool-results"):]))
        return True
    if cmd == "/sessions":
        print(_panel("Sessions", _format_recent_sessions(agent, limit=20), color=CYAN))
        return True
    if cmd == "/model" or cmd.startswith("/model "):
        print(_model(agent, stripped[len("/model"):]))
        return True
    if cmd == "/checkpoint" or cmd.startswith("/checkpoint "):
        print(_checkpoint(agent, stripped[len("/checkpoint"):]))
        return True
    if cmd == "/rollback" or cmd.startswith("/rollback "):
        print(_rollback(agent, stripped[len("/rollback"):]))
        return True
    if cmd == "/undo":
        print(_rollback(agent, "latest"))
        return True
    if cmd == "/retry":
        text = _retry_last_user(agent, mode)
        if text is not None:
            print(text)
        return True
    if cmd == "/background" or cmd.startswith("/background "):
        print(_background(agent, stripped[len("/background"):]))
        return True
    if cmd == "/clear":
        _clear_screen()
        print(_banner(mode))
        return True
    return False


def _create_prompt_session() -> "PromptSession | None":
    if PromptSession is None or FileHistory is None or PTStyle is None:
        return None
    # prompt_toolkit 需要真实 TTY；在管道/heredoc 冒烟测试里回退到 input()，
    # 避免测试日志里出现无意义的终端警告。
    if not os.isatty(0) or not os.isatty(1):
        return None
    history_path = BASE_DIR / "prompt_history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    style = PTStyle.from_dict(
        {
            "prompt": "ansicyan bold",
            "marker": "ansibrightblack",
        }
    )
    return PromptSession(
        history=FileHistory(str(history_path)),
        style=style,
    )


def _read_input(session: "PromptSession | None") -> str:
    if session is not None:
        # prompt_toolkit 能正确处理光标、退格、方向键和历史记录，比裸 input()
        # 更接近 Hermes 的交互式终端体验。
        return session.prompt([
            ("class:prompt", APP_NAME),
            ("class:marker", " > "),
        ]).strip()
    return input(f"{APP_NAME} > ").strip()


def _find_project_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "simple_hermes").is_dir():
            return candidate
    return None


def _detect_project_root(cwd: Path | None = None, module_file: Path | None = None) -> Path:
    explicit = os.getenv("SIMPLE_HERMES_PROJECT_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    cwd_root = (cwd or Path.cwd()).resolve()
    found = _find_project_root(cwd_root)
    if found is not None:
        return found.resolve()
    return cwd_root


def _default_session_id(project_root: Path) -> str:
    digest = hashlib.sha1(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"project-{digest}"


def _detect_session_id(project_root: Path) -> str:
    explicit = os.getenv("SIMPLE_HERMES_SESSION_ID", "").strip()
    if explicit:
        return explicit
    return _default_session_id(project_root)


def _has_explicit_session_id() -> bool:
    return bool(os.getenv("SIMPLE_HERMES_SESSION_ID", "").strip())


def _detect_max_steps(default: int = 90) -> int:
    raw = os.getenv("SIMPLE_HERMES_MAX_STEPS", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, 1000))


def main() -> None:
    project_root = _detect_project_root()
    agent = SimpleAgent(
        project_root=project_root,
        session_id=_detect_session_id(project_root),
        max_steps=_detect_max_steps(),
        resume_latest_continuation=not _has_explicit_session_id(),
    )
    mode = "real-llm" if agent.backend is not None else "rule-based"
    agent.last_trace = []
    session = _create_prompt_session()

    print(_banner(mode))
    print(_tips())

    while True:
        try:
            message = _read_input(session)
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}Bye.{RESET}")
            return

        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            print(f"{DIM}Bye.{RESET}")
            return
        if message.startswith("/") and _handle_ui_command(message, agent, mode):
            print()
            continue

        _run_agent_turn(agent, mode, message)


if __name__ == "__main__":
    main()
