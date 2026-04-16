from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from urllib import request
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable, List


from simple_hermes.agent.backend import LLMBackend
from simple_hermes.config import CRON_PATH, SKILLS_DIR, PermissionConfig, permission_config_from_env
from simple_hermes.state.checkpoints import CheckpointStore
from simple_hermes.state.memory import MemoryStore
from simple_hermes.state.session import SessionStore
from simple_hermes.state.continuity import ContinuityView
from simple_hermes.tools.registry import ToolRegistry

SENSITIVE_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "secrets.txt"}
SENSITIVE_PARTS = {".git", ".ssh"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".token"}
DANGEROUS_TERMINAL_COMMANDS = {"rm", "sudo", "su", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", "kill", "pkill", "killall"}
BACKGROUND_CHAIN_OPERATORS = {"&&", "||", ";", "&"}
SHELL_WRAPPER_NAMES = {"sh", "bash", "zsh"}
IGNORED_TREE_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
SENSITIVE_AUDIT_NAMES = {".env", ".env.local", ".npmrc", ".pypirc", "credentials.json", "auth-profiles.json"}
SENSITIVE_AUDIT_PARTS = {".ssh", ".aws", ".config/gcloud"}
MAX_TOOL_OUTPUT_CHARS = 3000
MAX_READ_FILE_CHARS = 12000
MAX_FETCH_URL_CHARS = 12000
TERMINAL_TIMEOUT_SECONDS = 10
RUN_TESTS_TIMEOUT_SECONDS = 30
DEFAULT_TREE_DEPTH = 2
DEFAULT_TEST_ARGS = ["discover", "-s", "tests", "-v"]
EXTERNAL_TEST_COMMANDS = {"npm", "pnpm", "yarn", "cargo", "go", "pytest"}
TODO_STATE_KEY = "todo_list"
VALID_TODO_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
ARTIFACT_STATE_KEY = "artifact_manifest"
ARTIFACT_FILE_SUFFIXES = {".json", ".jsonl", ".csv", ".tsv", ".md", ".txt", ".log", ".html", ".xml"}
ARTIFACT_DIR_NAMES = {"raw", "artifacts", "generated", "outputs", "output", "logs", "reports"}
TOOL_OUTPUT_ARTIFACT_MIN_CHARS = 200
TOOL_OUTPUT_ARTIFACT_MAX_CHARS = 200_000
DELIVERABLE_PLACEHOLDER_MARKERS = (
    "待补",
    "待整理",
    "待完善",
    "待完成",
    "待刷新",
    "未完成",
    "占位",
    "补全",
    "后续步骤",
    "todo",
    "tbd",
    "placeholder",
    "not final",
)
DELIVERABLE_PLACEHOLDER_PATTERNS = (
    (re.compile(r"\b\d{4}\.x{3,}\b", re.IGNORECASE), "placeholder arXiv id"),
    (re.compile(r"https?://arxiv\.org/?(?:[\s)\]]|$)", re.IGNORECASE), "empty arXiv link"),
)


@dataclass
class BackgroundTask:
    task_id: str
    command: str
    process: subprocess.Popen
    started_at: float
    session_id: str
    output: list[str] = field(default_factory=list)
    completed_at: float | None = None
    returncode: int | None = None
    completion_recorded: bool = False
    completion_recording: bool = False
    completion_event_delivered: bool = False
    monitor_thread: threading.Thread | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class BuiltInTools:
    """暴露给 planner 和显式 CLI 命令的内置工具实现。

    工具层应比 agent planner 更确定：这里处理文件系统边界、命令安全保护、
    回滚快照以及会话/记忆写入等副作用。自然语言意图判断应放在 planner，
    这个类只执行已经确定的具体工具请求。
    """

    def __init__(
        self,
        memory: MemoryStore,
        sessions: SessionStore,
        project_root: Path,
        delegate_runner: Callable[[str], str] | None = None,
        allowed_tools: set[str] | None = None,
        permissions: PermissionConfig | None = None,
        backend: LLMBackend | None = None,
        session_id: str = "default",
        session_id_getter: Callable[[], str] | None = None,
    ) -> None:
        self.memory = memory
        self.sessions = sessions
        self.project_root = project_root
        self.delegate_runner = delegate_runner
        self.allowed_tools = allowed_tools
        self.permissions = permissions if permissions is not None else permission_config_from_env()
        self.backend = backend
        self.session_id = session_id
        self.session_id_getter = session_id_getter
        self.continuity = ContinuityView(sessions)
        self._file_snapshots: dict[str, str] = {}
        self.checkpoints = CheckpointStore(project_root)
        self.skills_dir = Path(os.getenv("SIMPLE_HERMES_SKILLS_DIR", str(SKILLS_DIR))).expanduser()
        self.skill_candidates_dir = Path(
            os.getenv("SIMPLE_HERMES_SKILL_CANDIDATES_DIR", str(self.skills_dir.parent / "skill_candidates"))
        ).expanduser()
        self.cron_path = Path(os.getenv("SIMPLE_HERMES_CRON_PATH", str(CRON_PATH))).expanduser()
        self._background_tasks: dict[str, BackgroundTask] = {}
        self._background_counter = 0
        self.registry = ToolRegistry(allowed_tools=allowed_tools)
        self._register_tools()

    def _is_sensitive_path(self, path: Path) -> bool:
        """识别常见可能包含密钥的文件名、后缀和目录。"""
        parts = {part.lower() for part in path.parts}
        if parts & SENSITIVE_PARTS:
            return True
        name = path.name.lower()
        if name in SENSITIVE_NAMES:
            return True
        return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)

    def _register_tools(self) -> None:
        """集中注册工具名和面向 planner 的可读说明。"""
        self.registry.register("remember", "Save a durable fact to general memory", self.remember)
        self.registry.register("remember_user", "Save a durable fact about the user", self.remember_user)
        self.registry.register("memories", "List saved general memories", self.memories)
        self.registry.register("user_memories", "List saved user memories", self.user_memories)
        self.registry.register("history", "Show recent session history", self.history)
        self.registry.register("lineage", "Show lineage for the current session", self.lineage)
        self.registry.register("sessions", "List recent sessions", self.sessions_view)
        self.registry.register("descendants", "List descendant sessions for the current session", self.descendants)
        self.registry.register("recall", "Search session history", self.recall)
        self.registry.register("recall_all", "Search history across all sessions", self.recall_all)
        self.registry.register(
            "todo",
            "Manage a session task ledger for complex workflows. Usage: todo list | todo write <json-array> | todo add <id> <status> <content> | todo update <id> <status> [content] | todo clear",
            self.todo,
        )
        self.registry.register(
            "skills",
            "Manage durable/project-local Markdown skills and evolution candidates. Usage: skills list|view <name>|create <name> ::: <body>|use <name>|propose <name> ::: <body>|candidates|promote <id>",
            self.skills,
        )
        self.registry.register("cron", "Manage scheduled tasks. Usage: cron add <name> every <seconds> ::: <text> | list | run-due | run <id> | delete <id>", self.cron)
        self.registry.register("mcp", "Export session data through a small MCP-like JSON interface. Usage: mcp resources|sessions|session <id>|search <query>", self.mcp)
        self.registry.register(
            "artifact",
            "Track and summarize workflow artifacts. Usage: artifact scan [path] | list | clear. Scans raw/artifacts/output/logs files and writes a session manifest.",
            self.artifact,
        )
        self.registry.register(
            "experience",
            "Record and inspect structured evolution experience cards. Usage: experience record [key=value ...] ::: evidence | list | view <id> | summarize",
            self.experience,
        )
        self.registry.register(
            "validate_deliverable",
            "Validate a generated deliverable. Usage: validate_deliverable <path> [min_items=N] [required_fields=a,b,c] [required_headings=A,B]. Reports schema/content gaps and recovery hints.",
            self.validate_deliverable,
        )
        self.registry.register("read", "Read a file relative to the project root", self.read_file)
        self.registry.register("read_lines", "Read a numbered line range. Usage: read_lines <path> <start> <end>", self.read_lines)
        self.registry.register("tree", "Show a compact project tree for a path inside the project", self.tree)
        self.registry.register("glob", "Find project files by glob pattern, for example glob **/*.py", self.glob_files)
        self.registry.register("project_overview", "Summarize project type, key files, and likely verification commands", self.project_overview)
        self.registry.register("diff", "Show git diff for the project or a project-relative path", self.diff)
        self.registry.register("checkpoint", "Create or list project checkpoints. Usage: checkpoint [list|create <reason>]", self.checkpoint)
        self.registry.register("rollback", "Restore a project checkpoint. Usage: rollback [checkpoint-id|latest]", self.rollback)
        self.registry.register("terminal", "Run a guarded shell command in the project root", self.terminal)
        self.registry.register("run_tests", "Run Python unittest targets with a safer default command", self.run_tests)
        self.registry.register(
            "background",
            "Manage background shell tasks. Usage: background start <cmd> | list | status [id] | tail <id> | wait <id> [seconds] | stop <id>",
            self.background,
        )
        self.registry.register(
            "write_file",
            "Write or overwrite a project-relative file. Formats: path ::: content, path<space>content, or path\\ncontent.",
            self.write_file,
        )
        self.registry.register(
            "patch_file",
            "Replace exact text in a project-relative file. Prefer: path ::: exact target ::: replacement. Multiline: path\\ntarget\\n---\\nreplacement.",
            self.patch_file,
        )
        self.registry.register("search", "Search files by filename or contents", self.search_files)
        self.registry.register("summarize", "Summarize what Simple Hermes is", self.summarize)
        self.registry.register("delegate", "Run a tiny child agent on a subtask and return a summary", self.delegate)
        self.registry.register("parallel_delegate", "Run a few independent child tasks concurrently and return summaries", self.parallel_delegate)
        self.registry.register("fetch_url", "Fetch a public http(s) URL without auth headers and return a text preview", self.fetch_url)
        self.registry.register("dependency_scan", "Inventory local dependency manifests without contacting vulnerability services", self.dependency_scan)
        self.registry.register("credential_audit", "List likely credential files by path only; never reads or prints their contents", self.credential_audit)
        self.registry.register("help", "Show available tools", self.help)

    def _approval_required(self, operation: str) -> bool:
        raw = os.getenv("SIMPLE_HERMES_REQUIRE_APPROVAL", "").strip().lower()
        if raw in {"1", "true", "yes", "all"}:
            return True
        requested = {part.strip() for part in raw.split(",") if part.strip()}
        return operation in requested

    def _current_session_id(self) -> str:
        if self.session_id_getter is not None:
            return self.session_id_getter()
        return self.session_id

    def _resolve_project_path(self, text: str) -> tuple[Path | None, str, str | None]:
        raw = text.strip()
        if not raw:
            return None, "", "Usage: read <path>"
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        path = candidate.resolve()
        project_root = self.project_root.resolve()
        try:
            rel = str(path.relative_to(project_root))
        except ValueError:
            if not self.permissions.allow_outside_project_reads:
                return None, raw, "Refusing to read outside the project root."
            return path, str(path), None
        return path, rel, None

    def _resolve_project_write_path(self, text: str, usage: str) -> tuple[Path | None, str, str | None]:
        raw = text.strip()
        if not raw:
            return None, "", usage
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve()
        project_root = self.project_root.resolve()
        try:
            rel = str(path.relative_to(project_root))
        except ValueError:
            if not self.permissions.allow_outside_project_writes:
                return None, raw, "Refusing to write outside the project root."
            rel = str(path)
        if self._is_sensitive_path(path) and not self.permissions.allow_sensitive_writes:
            return None, rel, f"Refusing to write sensitive file: {rel}"
        return path, rel, None

    def _parse_tree_args(self, text: str) -> tuple[str, int, str | None]:
        raw = text.strip()
        if not raw:
            return ".", DEFAULT_TREE_DEPTH, None
        parts = raw.split()
        depth = DEFAULT_TREE_DEPTH
        if parts and parts[-1].isdigit():
            depth = max(1, min(int(parts[-1]), 6))
            parts = parts[:-1]
        path_text = " ".join(parts).strip() or "."
        return path_text, depth, None

    def _format_tree(self, path: Path, rel: str, depth: int) -> str:
        lines = [f"Tree for {rel} (depth={depth})"]

        def walk(current: Path, prefix: str, remaining_depth: int) -> None:
            if remaining_depth <= 0 or not current.is_dir():
                return
            try:
                children = list(current.iterdir())
            except OSError as e:
                lines.append(f"{prefix}[skipped: {current.name}: {e.strerror or e.__class__.__name__}]")
                return

            def sort_key(child: Path) -> tuple[bool, str]:
                try:
                    is_dir = child.is_dir()
                except OSError:
                    is_dir = False
                return (not is_dir, child.name.lower())

            entries = [child for child in sorted(children, key=sort_key) if child.name not in IGNORED_TREE_PARTS]
            for index, child in enumerate(entries):
                connector = "└── " if index == len(entries) - 1 else "├── "
                try:
                    is_dir = child.is_dir()
                except OSError as e:
                    lines.append(f"{prefix}{connector}{child.name} [skipped: {e.strerror or e.__class__.__name__}]")
                    continue
                suffix = "/" if is_dir else ""
                lines.append(f"{prefix}{connector}{child.name}{suffix}")
                next_prefix = prefix + ("    " if index == len(entries) - 1 else "│   ")
                if is_dir:
                    walk(child, next_prefix, remaining_depth - 1)

        if path.is_file():
            return f"Tree for {rel}\n└── {path.name}"
        walk(path, "", depth)
        return "\n".join(lines)

    def _run_subprocess(self, args: list[str], *, timeout: int, label: str, env: dict[str, str] | None = None) -> str:
        try:
            completed = subprocess.run(
                args,
                cwd=self.project_root,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            rendered = " ".join(shlex.quote(part) for part in args)
            return f"{label} timed out after {timeout}s: {rendered}"
        rendered = " ".join(shlex.quote(part) for part in args)
        output = [
            f"$ {rendered}",
            f"exit code: {completed.returncode}",
        ]
        if completed.stdout:
            output.extend(["", "stdout:", completed.stdout.rstrip()])
        if completed.stderr:
            output.extend(["", "stderr:", completed.stderr.rstrip()])
        if not completed.stdout and not completed.stderr:
            output.extend(["", "(no output)"])
        artifact_path = self._save_tool_output_artifact(label, rendered, "\n".join(output), completed.returncode)
        if artifact_path:
            output.extend(["", f"artifact: {artifact_path}"])
        return self._truncate("\n".join(output))

    def _test_env(self) -> dict[str, str]:
        """运行项目测试前移除 agent 自身后端环境变量。

        测试命令应该验证用户项目，而不是误用 agent 自己的 LLM/backend 配置。
        """
        env = os.environ.copy()
        for key in (
            "SIMPLE_HERMES_BACKEND",
            "SIMPLE_HERMES_PROVIDER",
            "SIMPLE_HERMES_BASE_URL",
            "SIMPLE_HERMES_API_KEY",
            "SIMPLE_HERMES_MODEL",
            "SIMPLE_HERMES_API_MODE",
            "SIMPLE_HERMES_HERMES_ROOT",
            "SIMPLE_HERMES_PROJECT_ROOT",
        ):
            env.pop(key, None)
        return env

    def _truncate(self, text: str) -> str:
        """限制工具输出长度，避免撑爆下一轮 planner prompt。"""
        if len(text) <= MAX_TOOL_OUTPUT_CHARS:
            return text
        return text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]..."

    def _missing_path_message(self, display_path: str, path: Path) -> str:
        """请求路径不存在时，按同名文件给出项目内候选路径。"""
        basename = path.name
        suggestions: List[str] = []
        if basename:
            for candidate in self.project_root.rglob(basename):
                if candidate.is_dir():
                    continue
                if any(part in IGNORED_TREE_PARTS for part in candidate.parts):
                    continue
                try:
                    suggestions.append(str(candidate.relative_to(self.project_root)))
                except ValueError:
                    suggestions.append(str(candidate))
                if len(suggestions) >= 5:
                    break
        if suggestions:
            return f"File not found: {display_path}\nDid you mean:\n" + "\n".join(f"- {item}" for item in suggestions)
        return f"File not found: {display_path}"

    def _terminal_refusal_reason(self, command: str) -> str | None:
        """执行 shell 命令前应用本地安全策略。"""
        if not command:
            return "Usage: terminal <command>"
        if ">" in command and not self.permissions.allow_dangerous_terminal:
            return "Refusing terminal command with shell redirection. Use write_file for file writes."
        if re.search(r"\bgit\s+(?:reset|clean|checkout|restore)\b", command):
            return "Refusing destructive git command."
        if self.permissions.allow_dangerous_terminal:
            return None
        for token in re.findall(r"[A-Za-z0-9_./-]+", command):
            if Path(token).name in DANGEROUS_TERMINAL_COMMANDS:
                return f"Refusing dangerous terminal command: {Path(token).name}"
        return None

    def _shell_chain_operators(self, command: str, *, _depth: int = 0) -> list[str]:
        """找出命令中的顶层 shell 串联操作符。

        `background` 的语义是“启动一个可跟踪的长任务”。如果把多个采集命令用
        `&&` 或 `;` 串成一个任务，agent 后续只能等待/截断整个链，难以对每个
        来源独立 tail、stop、重试或 artifact 化。这里用 shlex 保留引号语义，
        避免把 `python -c 'a;b'` 这类语言内部语句误判为 shell 串联。
        """
        operators: list[str] = []
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = []
        operators.extend(token for token in tokens if token in BACKGROUND_CHAIN_OPERATORS)

        if _depth >= 1:
            return sorted(set(operators))
        try:
            split_tokens = shlex.split(command)
        except ValueError:
            split_tokens = []
        if split_tokens and Path(split_tokens[0]).name in SHELL_WRAPPER_NAMES:
            for index, token in enumerate(split_tokens[:-1]):
                if token.startswith("-") and "c" in token:
                    operators.extend(self._shell_chain_operators(split_tokens[index + 1], _depth=_depth + 1))
                    break
        return sorted(set(operators))

    def _background_refusal_reason(self, command: str) -> str | None:
        """后台任务的额外约束：一个 background task 只承载一个 shell job。"""
        refusal = self._terminal_refusal_reason(command)
        if refusal:
            return refusal.replace("terminal", "background")
        operators = self._shell_chain_operators(command)
        if operators:
            rendered = ", ".join(operators)
            return (
                "Refusing chained background command: shell operator(s) "
                f"{rendered} detected. Start independent commands as separate background tasks so each has its own id, tail, wait, stop, and artifact log. "
                "If the chain must be atomic, write a project-local script with write_file and run that script as one background task."
            )
        return None

    def _remember_file_snapshot(self, display_path: str, path: Path) -> None:
        """记录编辑前内容，供非 git 项目的 diff 回退逻辑使用。"""
        if display_path not in self._file_snapshots and path.exists() and path.is_file():
            self._file_snapshots[display_path] = path.read_text(encoding="utf-8", errors="ignore")

    def _snapshot_diff(self, display_path: str, path: Path) -> str | None:
        """为非 git 项目中已编辑的文件生成内存 diff。"""
        if display_path not in self._file_snapshots:
            return None
        before = self._file_snapshots[display_path].splitlines(keepends=True)
        after = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(before, after, fromfile=f"a/{display_path}", tofile=f"b/{display_path}"))
        return diff or f"No changes since first edit snapshot for {display_path}."

    def _snapshot_diffs(self, raw: str) -> list[str]:
        """返回单个路径或全部已编辑快照的 diff 回退结果。"""
        if raw:
            path, display_path, error = self._resolve_project_path(raw)
            if error:
                return [error]
            assert path is not None
            snapshot = self._snapshot_diff(display_path, path)
            return [] if snapshot is None else [snapshot]

        snapshots: list[str] = []
        for display_path in sorted(self._file_snapshots):
            path = (self.project_root / display_path).resolve()
            snapshot = self._snapshot_diff(display_path, path)
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def _local_git_root(self) -> Path | None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self.project_root,
                text=True,
                capture_output=True,
                timeout=TERMINAL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return None
        if completed.returncode != 0:
            return None
        return Path(completed.stdout.strip()).resolve()

    def _nearest_patch_candidates(self, target: str, content: str) -> list[str]:
        target_lines = target.splitlines()
        content_lines = content.splitlines()
        candidates: list[tuple[float, str]] = []
        if target_lines and content_lines:
            window = max(1, len(target_lines))
            for start in range(0, max(1, len(content_lines) - window + 1)):
                snippet = "\n".join(content_lines[start : start + window])
                score = difflib.SequenceMatcher(None, target.strip(), snippet.strip()).ratio()
                candidates.append((score, snippet))
        if not candidates:
            for line in content_lines:
                score = difflib.SequenceMatcher(None, target.strip(), line.strip()).ratio()
                candidates.append((score, line))
        return [snippet for score, snippet in sorted(candidates, key=lambda item: item[0], reverse=True)[:3] if score >= 0.45]

    def remember(self, text: str) -> str:
        return self.memory.add(text)

    def remember_user(self, text: str) -> str:
        return self.memory.add_user(text)

    def memories(self, _: str) -> str:
        return self.memory.list_text()

    def user_memories(self, _: str) -> str:
        return self.memory.list_user_text()

    def history(self, _: str) -> str:
        return self.sessions.history_text(session_id=self._current_session_id())

    def lineage(self, _: str) -> str:
        return self.continuity.lineage_text(self._current_session_id())

    def sessions_view(self, _: str) -> str:
        return self.continuity.recent_sessions_text()

    def descendants(self, _: str) -> str:
        return self.continuity.descendants_text(self._current_session_id())

    def recall(self, text: str) -> str:
        query = text.strip()
        if not query:
            return "Usage: recall <query>"

        def _summarizer(q: str, rows):
            if self.backend is None:
                return "; ".join(row['content'][:80] for row in rows[:3])
            snippets = "\n".join(f"- {row['content'][:120]}" for row in rows[:5])
            try:
                decision = self.backend.plan(
                    message=f"Summarize these recall hits for query: {q}",
                    memory_block=self.memory.as_prompt_block(),
                    history_text=snippets,
                    tools_text="No tool call allowed. Return text only.",
                )
                if decision.text:
                    return decision.text
            except Exception:
                pass
            return "; ".join(row['content'][:80] for row in rows[:3])

        return self.sessions.search_text(query, session_id=self._current_session_id(), summarizer=_summarizer)

    def recall_all(self, text: str) -> str:
        query = text.strip()
        if not query:
            return "Usage: recall_all <query>"
        return self.sessions.search_all_text(query, limit=20)

    def _load_todos(self) -> list[dict[str, str]]:
        """读取当前会话的任务 ledger；损坏时清空，避免污染 planner。"""
        raw = self.sessions.get_state(self._current_session_id(), TODO_STATE_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.sessions.delete_state(self._current_session_id(), TODO_STATE_KEY)
            return []
        if not isinstance(data, list):
            return []
        return [self._normalize_todo(item) for item in data if isinstance(item, dict)]

    def _save_todos(self, items: list[dict[str, str]]) -> None:
        self.sessions.set_state(
            self._current_session_id(),
            TODO_STATE_KEY,
            json.dumps([self._normalize_todo(item) for item in items], ensure_ascii=False),
        )

    def _normalize_todo(self, item: dict[str, Any]) -> dict[str, str]:
        item_id = str(item.get("id") or item.get("name") or "?").strip() or "?"
        content = str(item.get("content") or item.get("task") or item.get("description") or "(no description)").strip()
        status = str(item.get("status") or "pending").strip().lower()
        if status not in VALID_TODO_STATUSES:
            status = "pending"
        return {"id": item_id[:80], "content": content, "status": status}

    def _format_todos(self, items: list[dict[str, str]]) -> str:
        summary = {
            "total": len(items),
            "pending": sum(1 for item in items if item["status"] == "pending"),
            "in_progress": sum(1 for item in items if item["status"] == "in_progress"),
            "completed": sum(1 for item in items if item["status"] == "completed"),
            "cancelled": sum(1 for item in items if item["status"] == "cancelled"),
        }
        return json.dumps({"todos": items, "summary": summary}, ensure_ascii=False, indent=2)

    def todo(self, text: str) -> str:
        """会话级任务 ledger，借鉴 Hermes/Codex 的显式 progress 机制。

        这是通用工具，不包含 DailyTrack 偏好。复杂任务由 planner 自己写入
        阶段列表，并在完成每个阶段后更新状态；主循环只负责把 ledger 注入
        后续 prompt，减少反复读上下文和无目的扩展搜索。
        """
        raw = text.strip()
        if raw.lower().startswith("todo "):
            raw = raw.split(maxsplit=1)[1].strip()
        if not raw or raw.lower() in {"list", "read", "show"}:
            return self._format_todos(self._load_todos())
        if raw.lower() == "clear":
            self._save_todos([])
            return self._format_todos([])

        action, _, rest = raw.partition(" ")
        action = action.lower()
        if action in {"write", "set", "replace"}:
            payload = rest.strip()
        elif raw.startswith("["):
            payload = raw
            action = "write"
        else:
            payload = ""

        if action in {"write", "set", "replace"}:
            if not payload:
                return "Usage: todo write <json-array>"
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                return f"Invalid todo JSON: {exc}"
            if not isinstance(data, list):
                return "Todo JSON must be a list of {id, content, status} objects."
            items = [self._normalize_todo(item) for item in data if isinstance(item, dict)]
            in_progress = [item for item in items if item["status"] == "in_progress"]
            if len(in_progress) > 1:
                return "Only one todo item may be in_progress at a time."
            self._save_todos(items)
            return self._format_todos(items)

        if action == "add":
            parts = rest.split(maxsplit=2)
            if len(parts) < 3:
                return "Usage: todo add <id> <status> <content>"
            item_id, status, content = parts
            if status not in VALID_TODO_STATUSES:
                return f"Invalid status: {status}. Use one of {sorted(VALID_TODO_STATUSES)}."
            items = self._load_todos()
            items.append(self._normalize_todo({"id": item_id, "status": status, "content": content}))
            if sum(1 for item in items if item["status"] == "in_progress") > 1:
                return "Only one todo item may be in_progress at a time."
            self._save_todos(items)
            return self._format_todos(items)

        if action == "update":
            parts = rest.split(maxsplit=2)
            if len(parts) < 2:
                return "Usage: todo update <id> <status> [content]"
            item_id, status = parts[0], parts[1].lower()
            content = parts[2] if len(parts) > 2 else None
            if status not in VALID_TODO_STATUSES:
                return f"Invalid status: {status}. Use one of {sorted(VALID_TODO_STATUSES)}."
            items = self._load_todos()
            found = False
            for item in items:
                if item["id"] == item_id:
                    item["status"] = status
                    if content:
                        item["content"] = content
                    found = True
                    break
            if not found:
                items.append(self._normalize_todo({"id": item_id, "status": status, "content": content or "(no description)"}))
            if sum(1 for item in items if item["status"] == "in_progress") > 1:
                return "Only one todo item may be in_progress at a time."
            self._save_todos(items)
            return self._format_todos(items)

        return "Usage: todo list | todo write <json-array> | todo add <id> <status> <content> | todo update <id> <status> [content] | todo clear"

    def _safe_skill_name(self, raw: str) -> str | None:
        name = raw.strip().replace(" ", "-")
        if not name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            return None
        if name in {".", ".."}:
            return None
        return name

    def _skill_path(self, name: str) -> Path:
        return self.skills_dir / f"{name}.md"

    def _project_skill_path(self, name: str) -> Path | None:
        """Resolve project-local skills stored as skills/<name>/SKILL.md or skills/<name>.md."""
        candidates = [
            self.project_root / "skills" / name / "SKILL.md",
            self.project_root / "skills" / f"{name}.md",
        ]
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _list_durable_skill_names(self) -> list[str]:
        return sorted(path.stem for path in self.skills_dir.glob("*.md") if path.is_file())

    def _list_project_skill_names(self) -> list[str]:
        project_skills = self.project_root / "skills"
        if not project_skills.is_dir():
            return []
        names: list[str] = []
        for path in project_skills.iterdir():
            if path.is_dir() and (path / "SKILL.md").is_file():
                names.append(path.name)
            elif path.is_file() and path.suffix.lower() == ".md":
                names.append(path.stem)
        return sorted(set(names))

    def _skill_candidate_root(self) -> Path:
        self.skill_candidates_dir.mkdir(parents=True, exist_ok=True)
        return self.skill_candidates_dir

    def _candidate_metadata_path(self, candidate_id: str) -> Path:
        return self._skill_candidate_root() / candidate_id / "metadata.json"

    def _candidate_skill_path(self, candidate_id: str) -> Path:
        return self._skill_candidate_root() / candidate_id / "SKILL.md"

    def _load_skill_candidate_metadata(self, candidate_id: str) -> dict[str, Any] | None:
        path = self._candidate_metadata_path(candidate_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _list_skill_candidates(self) -> list[dict[str, Any]]:
        root = self._skill_candidate_root()
        candidates: list[dict[str, Any]] = []
        for metadata_path in root.glob("*/metadata.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                candidates.append(data)
        return sorted(candidates, key=lambda item: str(item.get("created_at", "")), reverse=True)

    def _create_skill_candidate(self, name: str, body: str, *, source_experience: str = "", reason: str = "") -> str:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        candidate_id = f"cand-{timestamp}-{uuid.uuid4().hex[:6]}-{name}"
        candidate_dir = self._skill_candidate_root() / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=False)
        content = body.strip()
        if not content.startswith("#"):
            content = f"# {name}\n\n{content}"
        (candidate_dir / "SKILL.md").write_text(content + "\n", encoding="utf-8")
        metadata = {
            "id": candidate_id,
            "name": name,
            "status": "candidate",
            "session_id": self._current_session_id(),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_experience": source_experience,
            "reason": reason,
            "path": str(candidate_dir / "SKILL.md"),
        }
        (candidate_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return candidate_id

    def _evolution_session_dir(self) -> Path:
        """当前项目、当前会话的进化状态目录。

        经验卡保存在项目内 `.simple_hermes/evolution/`，这样它能跟随测试项目
        一起被检查、复制和归档；稳定 skill 仍放在用户级 skills 目录，二者隔离。
        """
        session_hash = hashlib.sha1(self._current_session_id().encode("utf-8")).hexdigest()[:12]
        path = self.project_root / ".simple_hermes" / "evolution" / session_hash
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _experience_log_path(self) -> Path:
        return self._evolution_session_dir() / "experience.jsonl"

    def _parse_key_value_tokens(self, tokens: list[str]) -> dict[str, str]:
        """解析 `key=value` 参数；保持简单确定，不做自然语言意图判断。"""
        options: dict[str, str] = {}
        for token in tokens:
            key, sep, value = token.partition("=")
            if sep:
                options[key.strip().lower()] = value.strip()
        return options

    def _load_experience_cards(self) -> list[dict[str, Any]]:
        path = self._experience_log_path()
        if not path.is_file():
            return []
        cards: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                cards.append(data)
        return cards

    def _record_experience_card(
        self,
        *,
        status: str = "observed",
        failure_type: str = "manual",
        goal: str = "",
        target: str = "",
        lesson: str = "",
        evidence: str | list[str] | None = None,
        source: str = "manual",
    ) -> dict[str, Any]:
        """写入一张结构化经验卡，作为后续 skill candidate 的证据来源。"""
        if isinstance(evidence, str):
            evidence_lines = [line for line in evidence.splitlines() if line.strip()]
        elif isinstance(evidence, list):
            evidence_lines = [str(line) for line in evidence if str(line).strip()]
        else:
            evidence_lines = []
        manifest = self._load_artifact_manifest()
        artifacts = [
            str(item.get("path"))
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        ][:20]
        timestamp = dt.datetime.now(dt.timezone.utc)
        card: dict[str, Any] = {
            "id": f"exp-{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "created_at": timestamp.isoformat(),
            "session_id": self._current_session_id(),
            "project_root": str(self.project_root),
            "source": source,
            "status": status,
            "failure_type": failure_type,
            "goal": goal,
            "target": target,
            "lesson": lesson,
            "evidence": [self._redact_secret_text(line) for line in evidence_lines[:80]],
            "artifacts": artifacts,
        }
        path = self._experience_log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")
        return card

    def experience(self, text: str) -> str:
        """记录和查看本地进化经验卡。"""
        raw = text.strip()
        action, _, rest = raw.partition(" ")
        action = action.lower() or "list"

        if action in {"list", "ls"}:
            cards = self._load_experience_cards()
            if not cards:
                return f"No experience cards recorded at {self._experience_log_path()}."
            lines = [f"Experience log: {self._experience_log_path()}", f"Cards: {len(cards)}"]
            for card in reversed(cards[-50:]):
                lines.append(
                    f"- {card.get('id')}: status={card.get('status')} "
                    f"type={card.get('failure_type')} target={card.get('target') or '-'}"
                )
            return self._truncate("\n".join(lines))

        if action == "view":
            card_id = rest.strip()
            if not card_id:
                return "Usage: experience view <id>"
            for card in self._load_experience_cards():
                if card.get("id") == card_id:
                    return self._truncate(json.dumps(card, ensure_ascii=False, indent=2))
            return f"Experience card not found: {card_id}"

        if action == "summarize":
            cards = self._load_experience_cards()
            if not cards:
                return f"No experience cards recorded at {self._experience_log_path()}."
            by_type: dict[str, int] = {}
            by_status: dict[str, int] = {}
            for card in cards:
                by_type[str(card.get("failure_type") or "unknown")] = by_type.get(str(card.get("failure_type") or "unknown"), 0) + 1
                by_status[str(card.get("status") or "unknown")] = by_status.get(str(card.get("status") or "unknown"), 0) + 1
            payload = {
                "experience_log": str(self._experience_log_path()),
                "total": len(cards),
                "by_status": dict(sorted(by_status.items())),
                "by_failure_type": dict(sorted(by_type.items())),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if action == "record":
            meta_text, evidence_text = (rest.split(" ::: ", 1) + [""])[:2] if " ::: " in rest else (rest, "")
            fields: dict[str, str] = {}
            json_evidence: Any = None
            if meta_text.strip().startswith("{"):
                try:
                    data = json.loads(meta_text.strip())
                except json.JSONDecodeError as exc:
                    return f"Invalid experience JSON: {exc}"
                if not isinstance(data, dict):
                    return "Experience JSON must be an object."
                fields = {str(key): str(value) for key, value in data.items() if key != "evidence"}
                json_evidence = data.get("evidence")
            else:
                try:
                    fields = self._parse_key_value_tokens(shlex.split(meta_text))
                except ValueError as exc:
                    return f"Invalid experience arguments: {exc}"
            evidence: str | list[str] | None
            if evidence_text.strip():
                evidence = evidence_text
            elif isinstance(json_evidence, list):
                evidence = [str(item) for item in json_evidence]
            elif json_evidence is not None:
                evidence = str(json_evidence)
            else:
                evidence = ""
            card = self._record_experience_card(
                status=fields.get("status", "observed"),
                failure_type=fields.get("failure_type", fields.get("type", "manual")),
                goal=fields.get("goal", ""),
                target=fields.get("target", ""),
                lesson=fields.get("lesson", ""),
                evidence=evidence,
                source=fields.get("source", "manual"),
            )
            return f"Recorded experience {card['id']}: {self._experience_log_path()}"

        return "Usage: experience record [key=value ...] ::: evidence | list | view <id> | summarize"

    def skills(self, text: str) -> str:
        """本地 Markdown skill 管理入口，先实现最小可用的 procedural memory。"""
        raw = text.strip()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if not raw or raw in {"list", "ls"}:
            durable_skills = self._list_durable_skill_names()
            project_skills = self._list_project_skill_names()
            if not durable_skills and not project_skills:
                return f"No local skills found in {self.skills_dir} or {self.project_root / 'skills'}."
            lines = ["Local skills:"]
            if durable_skills:
                lines.append(f"Durable skills in {self.skills_dir}:")
                lines.extend(f"- {name}" for name in durable_skills)
            if project_skills:
                lines.append(f"Project-local skills in {self.project_root / 'skills'}:")
                lines.extend(f"- {name}" for name in project_skills)
            return "\n".join(lines)

        action, _, rest = raw.partition(" ")
        action = action.lower()
        if action in {"candidates", "list-candidates"}:
            candidates = self._list_skill_candidates()
            if not candidates:
                return f"No skill candidates found in {self._skill_candidate_root()}."
            lines = [f"Skill candidates in {self._skill_candidate_root()}:"]
            for item in candidates[:50]:
                lines.append(
                    f"- {item.get('id')}: {item.get('name')} status={item.get('status')} "
                    f"source={item.get('source_experience') or 'manual'}"
                )
            return "\n".join(lines)

        if action == "create":
            if " ::: " not in rest:
                return "Usage: skills create <name> ::: <markdown body>"
            name_text, body = rest.split(" ::: ", 1)
            name = self._safe_skill_name(name_text)
            if name is None:
                return "Invalid skill name. Use letters, numbers, dot, dash, or underscore."
            path = self._skill_path(name)
            content = body.strip()
            if not content.startswith("#"):
                content = f"# {name}\n\n{content}"
            path.write_text(content + "\n", encoding="utf-8")
            return f"Created skill {name}: {path}"

        if action == "propose":
            if " ::: " not in rest:
                return "Usage: skills propose <name> [from=<experience-id>] [reason=<text>] ::: <markdown body>"
            meta_text, body = rest.split(" ::: ", 1)
            parts = shlex.split(meta_text)
            if not parts:
                return "Usage: skills propose <name> [from=<experience-id>] [reason=<text>] ::: <markdown body>"
            name = self._safe_skill_name(parts[0])
            if name is None:
                return "Invalid skill name. Use letters, numbers, dot, dash, or underscore."
            options = self._parse_key_value_tokens(parts[1:])
            candidate_id = self._create_skill_candidate(
                name,
                body,
                source_experience=options.get("from", ""),
                reason=options.get("reason", ""),
            )
            return f"Created skill candidate {candidate_id}: {self._candidate_skill_path(candidate_id)}"

        if action == "view-candidate":
            candidate_id = rest.strip()
            if not candidate_id:
                return "Usage: skills view-candidate <candidate-id>"
            metadata = self._load_skill_candidate_metadata(candidate_id)
            path = self._candidate_skill_path(candidate_id)
            if metadata is None or not path.is_file():
                return f"Skill candidate not found: {candidate_id}"
            body = path.read_text(encoding="utf-8", errors="replace")
            return self._truncate(
                f"# skill-candidate:{candidate_id}\n"
                f"Metadata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
                f"{body}"
            )

        if action == "promote":
            candidate_id = rest.strip()
            if not candidate_id:
                return "Usage: skills promote <candidate-id>"
            metadata = self._load_skill_candidate_metadata(candidate_id)
            candidate_path = self._candidate_skill_path(candidate_id)
            if metadata is None or not candidate_path.is_file():
                return f"Skill candidate not found: {candidate_id}"
            name = self._safe_skill_name(str(metadata.get("name") or ""))
            if name is None:
                return f"Skill candidate has invalid name: {candidate_id}"
            content = candidate_path.read_text(encoding="utf-8", errors="replace")
            marker_hits, pattern_hits = self._placeholder_hits(content)
            if marker_hits or pattern_hits:
                return (
                    f"Refusing to promote candidate {candidate_id}: placeholder markers remain. "
                    f"markers={marker_hits} patterns={pattern_hits}"
                )
            target = self._skill_path(name)
            target.write_text(content.rstrip() + "\n", encoding="utf-8")
            metadata["status"] = "promoted"
            metadata["promoted_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            metadata["promoted_to"] = str(target)
            self._candidate_metadata_path(candidate_id).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"Promoted skill candidate {candidate_id} to stable skill {name}: {target}"

        if action in {"view", "use"}:
            name = self._safe_skill_name(rest)
            if name is None:
                return f"Usage: skills {action} <name>"
            path = self._skill_path(name)
            source = "durable"
            if not path.exists():
                project_path = self._project_skill_path(name)
                if project_path is None:
                    return f"Skill not found: {name}"
                path = project_path
                source = "project"
            if not path.exists():
                return f"Skill not found: {name}"
            body = path.read_text(encoding="utf-8", errors="replace")
            if action == "use":
                self.sessions.append(
                    "summary",
                    f"[skill:{name} source={source} path={path}]\n{body}",
                    session_id=self._current_session_id(),
                    kind="skill_context",
                    tool_name="skills",
                )
                return f"Loaded skill into session context ({source}): {name}\nPath: {path}\n\n{self._truncate(body)}"
            return self._truncate(f"# skill:{name} ({source})\nPath: {path}\n\n{body}")

        if action in {"delete", "rm"}:
            name = self._safe_skill_name(rest)
            if name is None:
                return "Usage: skills delete <name>"
            path = self._skill_path(name)
            if not path.exists():
                return f"Skill not found: {name}"
            path.unlink()
            return f"Deleted skill {name}."

        return (
            "Usage: skills list|view <name>|use <name>|create <name> ::: <markdown body>|delete <name>|"
            "propose <name> [from=<experience-id>] [reason=<text>] ::: <markdown body>|"
            "candidates|view-candidate <id>|promote <id>"
        )

    def _load_cron_jobs(self) -> list[dict]:
        if not self.cron_path.exists():
            return []
        try:
            data = json.loads(self.cron_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _save_cron_jobs(self, jobs: list[dict]) -> None:
        self.cron_path.parent.mkdir(parents=True, exist_ok=True)
        self.cron_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    def _format_cron_job(self, job: dict) -> str:
        next_run = dt.datetime.fromtimestamp(float(job.get("next_run_at") or 0)).isoformat(timespec="seconds")
        return (
            f"- {job.get('id')}: {job.get('name')} every {job.get('interval_seconds')}s "
            f"next={next_run} enabled={job.get('enabled', True)} text={job.get('text')}"
        )

    def _run_cron_job(self, job: dict) -> str:
        text = str(job.get("text") or "").strip()
        if not text:
            return f"Cron job {job.get('id')} has no text."
        if text.startswith("tool:"):
            tool_text = text[len("tool:"):].strip()
            tool_name, _, arg = tool_text.partition(" ")
            result = self.registry.run(tool_name.strip(), arg.strip())
        else:
            result = f"Scheduled prompt ready for agent processing: {text}"
        self.sessions.append(
            "assistant",
            f"[cron:{job.get('id')}] {result}",
            session_id=self._current_session_id(),
            kind="cron_result",
            tool_name="cron",
        )
        return result

    def cron(self, text: str) -> str:
        """轻量 cron 注册表；真正的常驻调度可由外部循环定期调用 run-due。"""
        raw = text.strip()
        jobs = self._load_cron_jobs()
        if not raw or raw in {"list", "ls"}:
            if not jobs:
                return f"No cron jobs configured at {self.cron_path}."
            return "Cron jobs:\n" + "\n".join(self._format_cron_job(job) for job in jobs)

        action, _, rest = raw.partition(" ")
        action = action.lower()
        now = time.time()
        if action == "add":
            match = re.match(r"(?P<name>.+?)\s+every\s+(?P<seconds>\d+)\s+:::\s+(?P<text>.+)", rest, flags=re.DOTALL)
            if not match:
                return "Usage: cron add <name> every <seconds> ::: <text>"
            interval = max(1, int(match.group("seconds")))
            job = {
                "id": f"cron-{uuid.uuid4().hex[:10]}",
                "name": match.group("name").strip(),
                "interval_seconds": interval,
                "text": match.group("text").strip(),
                "created_at": now,
                "next_run_at": now + interval,
                "last_run_at": None,
                "enabled": True,
                "session_id": self._current_session_id(),
            }
            jobs.append(job)
            self._save_cron_jobs(jobs)
            return f"Added cron job {job['id']}: {job['name']}"

        if action == "delete":
            job_id = rest.strip()
            kept = [job for job in jobs if job.get("id") != job_id]
            if len(kept) == len(jobs):
                return f"Cron job not found: {job_id}"
            self._save_cron_jobs(kept)
            return f"Deleted cron job {job_id}."

        if action == "run":
            job_id = rest.strip()
            for job in jobs:
                if job.get("id") == job_id:
                    result = self._run_cron_job(job)
                    job["last_run_at"] = now
                    job["next_run_at"] = now + int(job.get("interval_seconds") or 1)
                    self._save_cron_jobs(jobs)
                    return f"Ran cron job {job_id}:\n{result}"
            return f"Cron job not found: {job_id}"

        if action == "run-due":
            due = [job for job in jobs if job.get("enabled", True) and float(job.get("next_run_at") or 0) <= now]
            if not due:
                return "No cron jobs are due."
            lines = []
            for job in due:
                result = self._run_cron_job(job)
                job["last_run_at"] = now
                job["next_run_at"] = now + int(job.get("interval_seconds") or 1)
                lines.append(f"{job.get('id')}: {result}")
            self._save_cron_jobs(jobs)
            return "Ran due cron jobs:\n" + "\n".join(lines)

        return "Usage: cron add <name> every <seconds> ::: <text> | list | run-due | run <id> | delete <id>"

    def _redact_secret_text(self, text: str) -> str:
        patterns = [
            r"(?i)(api[_-]?key|token|secret|password)(\s*[:=]\s*)[^\s,;]+",
            r"sk-[A-Za-z0-9_-]{12,}",
        ]
        redacted = text
        for pattern in patterns:
            redacted = re.sub(pattern, lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]" if len(m.groups()) >= 2 else "[REDACTED]", redacted)
        return redacted

    def mcp(self, text: str) -> str:
        """提供一个小型 MCP-like JSON 接口，便于后续接真实 MCP server。"""
        raw = text.strip()
        action, _, rest = raw.partition(" ")
        action = action or "resources"
        if action == "resources":
            payload = {
                "resources": [
                    {"uri": "simple-hermes://sessions", "description": "Recent Simple Hermes sessions"},
                    {"uri": "simple-hermes://session/<id>", "description": "Messages for one session"},
                    {"uri": "simple-hermes://search/<query>", "description": "Cross-session recall search"},
                ]
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if action == "sessions":
            return json.dumps({"sessions": self.sessions.recent_sessions(limit=20)}, ensure_ascii=False, indent=2)
        if action == "session":
            session_id = rest.strip() or self._current_session_id()
            payload = {
                "session": self.sessions.session_info(session_id),
                "messages": self.sessions.messages_for_session(session_id, limit=100),
            }
            return self._redact_secret_text(json.dumps(payload, ensure_ascii=False, indent=2))
        if action == "search":
            query = rest.strip()
            if not query:
                return "Usage: mcp search <query>"
            return self._redact_secret_text(json.dumps({"results": self.sessions.search_all(query, limit=20)}, ensure_ascii=False, indent=2))
        return "Usage: mcp resources|sessions|session <id>|search <query>"

    def _artifact_manifest_path(self) -> Path:
        """返回当前会话的 artifact manifest 文件路径。

        manifest 写在项目内隐藏目录，目的是让长工作流的原始数据、中间结果和日志
        不再只存在于 LLM 上下文或临时 stdout 里。它是通用运行时状态，不包含
        DailyTrack 专用规则。
        """
        session_hash = hashlib.sha1(self._current_session_id().encode("utf-8")).hexdigest()[:12]
        return self.project_root / ".simple_hermes" / "artifacts" / session_hash / "manifest.json"

    def _artifact_entry_for_path(self, path: Path) -> dict[str, Any] | None:
        """为一个候选文件生成轻量 artifact 摘要。"""
        if not path.is_file() or path.suffix.lower() not in ARTIFACT_FILE_SUFFIXES:
            return None
        if self._is_sensitive_path(path):
            return None
        try:
            rel = str(path.relative_to(self.project_root))
        except ValueError:
            rel = str(path)
        try:
            stat = path.stat()
        except OSError:
            return None
        entry: dict[str, Any] = {
            "path": rel,
            "suffix": path.suffix.lower(),
            "bytes": stat.st_size,
            "modified_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, list):
                    entry["kind"] = "json-list"
                    entry["items"] = len(data)
                elif isinstance(data, dict):
                    entry["kind"] = "json-object"
                    for key in ("papers", "items", "results", "links", "entries", "models", "datasets"):
                        value = data.get(key)
                        if isinstance(value, list):
                            entry["items"] = len(value)
                            entry["primary_key"] = key
                            break
                    entry.setdefault("keys", list(data.keys())[:12])
                else:
                    entry["kind"] = type(data).__name__
            except Exception as exc:
                entry["kind"] = "json-invalid"
                entry["error"] = str(exc)
        elif path.suffix.lower() in {".md", ".txt", ".log"}:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            entry["kind"] = "text"
            entry["lines"] = text.count("\n") + (1 if text else 0)
            if path.suffix.lower() == ".md":
                entry["headings"] = [line.strip() for line in text.splitlines() if line.startswith("#")][:12]
        else:
            entry["kind"] = path.suffix.lower().lstrip(".") or "file"
        return entry

    def _load_artifact_manifest(self) -> dict[str, Any]:
        raw = self.sessions.get_state(self._current_session_id(), ARTIFACT_STATE_KEY)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        manifest_path = self._artifact_manifest_path()
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"session_id": self._current_session_id(), "artifacts": []}

    def _save_artifact_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["session_id"] = self._current_session_id()
        manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest_path = self._artifact_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.sessions.set_state(self._current_session_id(), ARTIFACT_STATE_KEY, json.dumps(manifest, ensure_ascii=False))

    def _upsert_artifact_entry(self, entry: dict[str, Any]) -> None:
        """把单个 artifact 摘要写入 manifest。"""
        manifest = self._load_artifact_manifest()
        existing = {
            item.get("path"): item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        existing[entry["path"]] = entry
        manifest["artifacts"] = [existing[key] for key in sorted(existing)]
        self._save_artifact_manifest(manifest)

    def _tool_output_artifact_slug(self, command: str) -> str:
        """从命令生成稳定但不泄露长参数的 artifact 文件名片段。"""
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", command.strip())[:48].strip("-")
        digest = hashlib.sha1(command.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned or 'command'}-{digest}"

    def _save_tool_output_artifact(self, tool_name: str, command: str, output_text: str, exit_code: int | None) -> str | None:
        """把成功工具输出落盘为 raw artifact。

        这补上 Hermes 风格的 artifact store 缩影：长信息任务不能只把采集结果放在
        stdout 或 LLM 上下文里，否则 synthesis 阶段很容易找不到原始证据并重新采集。
        """
        if exit_code != 0:
            return None
        redacted = self._redact_secret_text(output_text).strip()
        if len(redacted) < TOOL_OUTPUT_ARTIFACT_MIN_CHARS:
            return None
        if len(redacted) > TOOL_OUTPUT_ARTIFACT_MAX_CHARS:
            redacted = redacted[:TOOL_OUTPUT_ARTIFACT_MAX_CHARS] + "\n...[tool output artifact truncated]"
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = self._tool_output_artifact_slug(command)
        output_dir = self._artifact_manifest_path().parent / "tool_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{timestamp}_{tool_name}_{slug}.log"
        header = (
            f"tool: {tool_name}\n"
            f"exit_code: {exit_code}\n"
            f"command: {self._redact_secret_text(command)}\n"
            f"captured_at: {dt.datetime.now(dt.timezone.utc).isoformat()}\n\n"
        )
        path.write_text(header + redacted + "\n", encoding="utf-8")
        entry = self._artifact_entry_for_path(path)
        if entry is None:
            return None
        entry["source_tool"] = tool_name
        self._upsert_artifact_entry(entry)
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    def _iter_artifact_paths(self, root: Path) -> list[Path]:
        """以容错方式遍历文件，避免无权限目录让整个 workflow 中断。"""
        if root.is_file():
            return [root]
        paths: list[Path] = []
        for current, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda _error: None):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in IGNORED_TREE_PARTS and dirname != ".simple_hermes"
            ]
            current_path = Path(current)
            for filename in filenames:
                paths.append(current_path / filename)
                if len(paths) >= 1200:
                    return paths
        return paths

    def _artifact_candidates(self, root: Path) -> list[Path]:
        """扫描可能的 workflow artifacts，避免把普通源码文件都放进 manifest。"""
        candidates: list[Path] = []
        for path in self._iter_artifact_paths(root):
            if len(candidates) >= 300:
                break
            if any(part in IGNORED_TREE_PARTS or part == ".simple_hermes" for part in path.parts):
                continue
            if not path.is_file() or path.suffix.lower() not in ARTIFACT_FILE_SUFFIXES:
                continue
            try:
                rel_parts = path.relative_to(self.project_root).parts
            except ValueError:
                rel_parts = path.parts
            if any(part in ARTIFACT_DIR_NAMES for part in rel_parts) or path.name in {"track.md", "papers.json"}:
                candidates.append(path)
        return candidates

    def artifact(self, text: str) -> str:
        """扫描、列出和清理当前会话 artifact manifest。"""
        raw = text.strip()
        action, _, rest = raw.partition(" ")
        action = action or "list"
        if action == "clear":
            manifest = {"session_id": self._current_session_id(), "artifacts": []}
            self._save_artifact_manifest(manifest)
            return "Cleared artifact manifest."

        if action == "list":
            manifest = self._load_artifact_manifest()
            artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
            if not artifacts:
                return "No artifacts recorded yet. Use `artifact scan [path]` after source collection or generation."
            lines = [f"Artifact manifest: {self._artifact_manifest_path()}", f"Artifacts: {len(artifacts)}"]
            for item in artifacts[:80]:
                bits = [item.get("path", "?"), str(item.get("kind", "file"))]
                if "items" in item:
                    bits.append(f"items={item['items']}")
                bits.append(f"bytes={item.get('bytes', 0)}")
                lines.append("- " + " | ".join(bits))
            if len(artifacts) > 80:
                lines.append(f"... {len(artifacts) - 80} more artifacts omitted")
            return self._truncate("\n".join(lines))

        if action == "scan":
            path_text = rest.strip() or "."
            path, display_path, error = self._resolve_project_path(path_text)
            if error:
                return error.replace("read", "scan")
            assert path is not None
            if not path.exists():
                return f"Path not found: {display_path}"
            paths = [path] if path.is_file() else self._artifact_candidates(path)
            entries = [entry for candidate in paths if (entry := self._artifact_entry_for_path(candidate)) is not None]
            manifest = self._load_artifact_manifest()
            existing = {
                item.get("path"): item
                for item in manifest.get("artifacts", [])
                if isinstance(item, dict) and item.get("path")
            }
            for entry in entries:
                existing[entry["path"]] = entry
            manifest["artifacts"] = [existing[key] for key in sorted(existing)]
            self._save_artifact_manifest(manifest)
            lines = [
                f"Scanned artifacts under {display_path}: {len(entries)} found, {len(manifest['artifacts'])} recorded total.",
                f"Manifest: {self._artifact_manifest_path()}",
            ]
            for entry in entries[:30]:
                summary = f"- {entry['path']} | {entry.get('kind')} | bytes={entry.get('bytes')}"
                if "items" in entry:
                    summary += f" | items={entry['items']}"
                lines.append(summary)
            if len(entries) > 30:
                lines.append(f"... {len(entries) - 30} more newly scanned artifacts omitted")
            return self._truncate("\n".join(lines))

        return "Usage: artifact scan [path] | list | clear"

    def _parse_validation_options(self, tokens: list[str]) -> dict[str, str]:
        options: dict[str, str] = {}
        for token in tokens:
            key, sep, value = token.partition("=")
            if sep:
                options[key.strip().lower()] = value.strip()
        return options

    def _split_csv_option(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def _nearby_raw_artifacts(self, path: Path) -> list[str]:
        """寻找可用于重建交付物的 raw/artifact 文件。"""
        found: list[str] = []
        seen: set[str] = set()

        def add_candidate(candidate_text: str) -> bool:
            candidate_text = candidate_text.strip()
            if not candidate_text or candidate_text in seen:
                return len(found) >= 12
            candidate = (self.project_root / candidate_text).resolve() if not Path(candidate_text).is_absolute() else Path(candidate_text)
            if candidate == path:
                return len(found) >= 12
            seen.add(candidate_text)
            found.append(candidate_text)
            return len(found) >= 12

        # validation failure often happens after terminal/background collectors have
        # produced useful stdout. Those logs live in the session manifest rather than
        # raw/, so list them first as durable rebuild sources.
        manifest = self._load_artifact_manifest()
        for item in manifest.get("artifacts", []):
            if not isinstance(item, dict):
                continue
            item_path = item.get("path")
            if isinstance(item_path, str) and add_candidate(item_path):
                return found

        roots = []
        if (path.parent / "raw").is_dir():
            roots.append(path.parent / "raw")
        if (path.parent / "artifacts").is_dir():
            roots.append(path.parent / "artifacts")
        roots.append(self.project_root)
        for root in roots:
            for candidate in self._artifact_candidates(root):
                try:
                    rel = str(candidate.relative_to(self.project_root))
                except ValueError:
                    rel = str(candidate)
                if add_candidate(rel):
                    return found
        return found

    def _validate_json_deliverable(self, path: Path, display_path: str, options: dict[str, str]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        notes: list[str] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            return [f"invalid JSON: {exc}"], notes

        if isinstance(data, list):
            rows = data
            notes.append(f"json list items={len(rows)}")
        elif isinstance(data, dict):
            rows = None
            for key in ("papers", "items", "results", "entries", "links"):
                value = data.get(key)
                if isinstance(value, list):
                    rows = value
                    notes.append(f"json object key={key} items={len(rows)}")
                    break
            if rows is None:
                rows = []
                notes.append(f"json object keys={list(data.keys())[:12]}")
        else:
            rows = []
            errors.append(f"expected JSON list/object, got {type(data).__name__}")

        min_items_raw = options.get("min_items") or options.get("min-items")
        if min_items_raw:
            try:
                min_items = int(min_items_raw)
                if len(rows) < min_items:
                    errors.append(f"item count {len(rows)} < min_items {min_items}")
            except ValueError:
                errors.append(f"invalid min_items value: {min_items_raw}")

        required_fields = self._split_csv_option(options.get("required_fields", "") or options.get("required-fields", ""))
        if required_fields and rows:
            missing: dict[str, int] = {field: 0 for field in required_fields}
            for row in rows:
                if not isinstance(row, dict):
                    errors.append("one or more list items are not objects")
                    break
                for field in required_fields:
                    if row.get(field) in (None, "", [], {}):
                        missing[field] += 1
            for field, count in missing.items():
                if count:
                    errors.append(f"field {field!r} missing/empty in {count}/{len(rows)} items")
        elif required_fields and not rows:
            errors.append(f"required_fields requested but no list items found in {display_path}")

        marker_hits, pattern_hits = self._placeholder_hits(json.dumps(data, ensure_ascii=False))
        if marker_hits:
            errors.append("placeholder/incomplete markers found in JSON strings: " + ", ".join(marker_hits))
        if pattern_hits:
            errors.append("placeholder/incomplete patterns found in JSON strings: " + ", ".join(pattern_hits))
        return errors, notes

    def _placeholder_hits(self, text: str) -> tuple[list[str], list[str]]:
        lowered = text.lower()
        markers = sorted({marker for marker in DELIVERABLE_PLACEHOLDER_MARKERS if marker in lowered})
        patterns = sorted({label for pattern, label in DELIVERABLE_PLACEHOLDER_PATTERNS if pattern.search(text)})
        return markers, patterns

    def _validate_markdown_deliverable(self, path: Path, options: dict[str, str]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        notes: list[str] = []
        text = path.read_text(encoding="utf-8", errors="replace")
        notes.append(f"markdown bytes={len(text.encode('utf-8'))} lines={len(text.splitlines())}")
        min_bytes_raw = options.get("min_bytes") or options.get("min-bytes")
        if min_bytes_raw:
            try:
                min_bytes = int(min_bytes_raw)
                if len(text.encode("utf-8")) < min_bytes:
                    errors.append(f"size {len(text.encode('utf-8'))} bytes < min_bytes {min_bytes}")
            except ValueError:
                errors.append(f"invalid min_bytes value: {min_bytes_raw}")
        headings = self._split_csv_option(options.get("required_headings", "") or options.get("required-headings", ""))
        for heading in headings:
            if heading not in text:
                errors.append(f"missing required heading/content: {heading!r}")
        placeholder_hits, pattern_hits = self._placeholder_hits(text)
        if placeholder_hits:
            errors.append("placeholder/incomplete markers found: " + ", ".join(sorted(set(placeholder_hits))))
        if pattern_hits:
            errors.append("placeholder/incomplete patterns found: " + ", ".join(pattern_hits))
        empty_table_rows = []
        for index, line in enumerate(text.splitlines(), 1):
            if set(line.strip()) <= {"|", "-", ":", " "}:
                continue
            if "|" in line and re.search(r"\|\s*(?:N/A|-)?\s*\|", line):
                empty_table_rows.append(index)
        if empty_table_rows:
            errors.append(f"possible empty/N/A markdown table cells on lines: {empty_table_rows[:12]}")
        return errors, notes

    def validate_deliverable(self, text: str) -> str:
        """验证已生成交付物，并给出从 artifact 重建的恢复建议。"""
        parts = shlex.split(text.strip()) if text.strip() else []
        if not parts:
            return "Usage: validate_deliverable <path> [min_items=N] [required_fields=a,b,c] [required_headings=A,B]"
        path_text, option_tokens = parts[0], parts[1:]
        path, display_path, error = self._resolve_project_path(path_text)
        if error:
            return error.replace("read", "validate")
        assert path is not None
        if not path.exists() or not path.is_file():
            return self._missing_path_message(display_path, path)
        if self._is_sensitive_path(path) and not self.permissions.allow_sensitive_reads:
            return f"Refusing to validate sensitive file: {display_path}"

        options = self._parse_validation_options(option_tokens)
        suffix = path.suffix.lower()
        if suffix == ".json":
            errors, notes = self._validate_json_deliverable(path, display_path, options)
        elif suffix in {".md", ".txt"}:
            errors, notes = self._validate_markdown_deliverable(path, options)
        else:
            errors, notes = [], [f"file bytes={path.stat().st_size}"]
            if path.stat().st_size == 0:
                errors.append("file is empty")

        entry = self._artifact_entry_for_path(path)
        if entry is not None:
            manifest = self._load_artifact_manifest()
            existing = {
                item.get("path"): item
                for item in manifest.get("artifacts", [])
                if isinstance(item, dict) and item.get("path")
            }
            existing[entry["path"]] = entry
            manifest["artifacts"] = [existing[key] for key in sorted(existing)]
            self._save_artifact_manifest(manifest)

        status = "failed" if errors else "ok"
        lines = [f"DELIVERABLE_VALIDATION {status}: {display_path}", *[f"- note: {note}" for note in notes]]
        lines.extend(f"- error: {error}" for error in errors)
        if errors:
            raw_artifacts = self._nearby_raw_artifacts(path)
            if raw_artifacts:
                lines.append("Recommended recovery: rebuild this deliverable from raw/artifact files instead of patching individual markdown rows.")
                lines.append("Candidate artifacts:")
                lines.extend(f"- {item}" for item in raw_artifacts)
            else:
                lines.append("Recommended recovery: regenerate the deliverable from source data, then rerun validate_deliverable.")
            failure_type = "placeholder_deliverable" if any("placeholder" in item or "incomplete" in item for item in errors) else "deliverable_validation_failed"
            self._record_experience_card(
                status="failed",
                failure_type=failure_type,
                target=display_path,
                lesson="Repair the deliverable from grounded source artifacts before re-validating.",
                evidence=lines,
                source="validate_deliverable",
            )
        return self._truncate("\n".join(lines))

    def read_file(self, text: str) -> str:
        path, display_path, error = self._resolve_project_path(text)
        if error:
            return error
        assert path is not None
        if not path.exists() or not path.is_file():
            return self._missing_path_message(display_path, path)
        if self._is_sensitive_path(path) and not self.permissions.allow_sensitive_reads:
            return f"Refusing to read sensitive file: {display_path}"
        content = path.read_text(encoding="utf-8", errors="ignore")
        if len(content) > MAX_READ_FILE_CHARS:
            content = content[:MAX_READ_FILE_CHARS] + "\n...[truncated]..."
        return f"# {display_path}\n\n{content}"

    def read_lines(self, text: str) -> str:
        usage = "Usage: read_lines <path> <start> <end>"
        parts = text.strip().rsplit(maxsplit=2)
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            return usage
        path_text, start_text, end_text = parts
        start = max(1, int(start_text))
        end = max(start, int(end_text))
        path, display_path, error = self._resolve_project_path(path_text)
        if error:
            return error
        assert path is not None
        if not path.exists() or not path.is_file():
            return self._missing_path_message(display_path, path)
        if self._is_sensitive_path(path) and not self.permissions.allow_sensitive_reads:
            return f"Refusing to read sensitive file: {display_path}"
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if start > len(lines):
            return f"Line range starts after end of file: {display_path} has {len(lines)} lines."
        selected = lines[start - 1 : min(end, len(lines))]
        numbered = [f"{lineno:>5}: {line}" for lineno, line in enumerate(selected, start=start)]
        return f"# {display_path}:{start}-{min(end, len(lines))}\n\n" + "\n".join(numbered)

    def tree(self, text: str) -> str:
        path_text, depth, _ = self._parse_tree_args(text)
        path, display_path, error = self._resolve_project_path(path_text)
        if error:
            return error.replace("read", "inspect")
        assert path is not None
        if not path.exists():
            return f"Path not found: {display_path}"
        return self._truncate(self._format_tree(path, display_path, depth))

    def glob_files(self, text: str) -> str:
        pattern = text.strip() or "**/*"
        matches: List[str] = []
        for path in self.project_root.glob(pattern):
            if path.is_dir():
                continue
            if any(part in IGNORED_TREE_PARTS for part in path.parts):
                continue
            try:
                matches.append(str(path.relative_to(self.project_root)))
            except ValueError:
                matches.append(str(path))
            if len(matches) >= 100:
                break
        if not matches:
            return f"No files matched glob: {pattern}"
        return "Files:\n" + "\n".join(f"- {item}" for item in sorted(matches))

    def _project_instruction_file_lines(self) -> list[str]:
        """列出项目内常见 agent 指令文件；这里只报路径，不强行解释具体项目语义。"""
        names = ("AGENTS.md", "CLAUDE.md", "MEMORY.md")
        lines = [f"- {name}" for name in names if (self.project_root / name).is_file()]
        return lines or ["- none"]

    def _project_local_skill_lines(self) -> list[str]:
        """列出项目内 skills/*/SKILL.md 的简短元信息，方便 planner 下一步 read。"""
        skills_root = self.project_root / "skills"
        if not skills_root.is_dir():
            return ["- none"]
        lines: list[str] = []
        for skill_path in sorted(skills_root.glob("*/SKILL.md"))[:40]:
            try:
                rel = str(skill_path.relative_to(self.project_root))
            except ValueError:
                rel = str(skill_path)
            name = skill_path.parent.name
            description = ""
            try:
                text = self._redact_secret_text(skill_path.read_text(encoding="utf-8", errors="replace")[:2500])
            except OSError:
                text = ""
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    for line in text[3:end].splitlines():
                        key, sep, value = line.partition(":")
                        if not sep:
                            continue
                        cleaned_key = key.strip().lower()
                        cleaned_value = value.strip().strip("\"'")
                        if cleaned_key == "name" and cleaned_value:
                            name = cleaned_value
                        elif cleaned_key == "description" and cleaned_value:
                            description = cleaned_value
            if not description:
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        description = stripped.lstrip("#").strip()
                        break
            suffix = f": {description}" if description else ""
            lines.append(f"- {name} ({rel}){suffix}")
        return lines or ["- none"]

    def project_overview(self, _: str) -> str:
        markers = {
            "pyproject.toml": "Python project",
            "setup.py": "Python project",
            "requirements.txt": "Python dependencies",
            "package.json": "Node/JavaScript project",
            "pnpm-lock.yaml": "pnpm lockfile",
            "package-lock.json": "npm lockfile",
            "yarn.lock": "Yarn lockfile",
            "Cargo.toml": "Rust project",
            "go.mod": "Go project",
            "Makefile": "Makefile tasks",
        }
        found_markers = [f"- {name}: {label}" for name, label in markers.items() if (self.project_root / name).exists()]
        py_files = list(self.project_root.glob("**/*.py"))[:200]
        js_files = list(self.project_root.glob("**/*.js"))[:200]
        ts_files = list(self.project_root.glob("**/*.ts"))[:200]
        test_dirs = [str(path.relative_to(self.project_root)) for path in self.project_root.glob("**/tests") if path.is_dir()]

        likely_commands: List[str] = []
        if (self.project_root / "pyproject.toml").exists() or (self.project_root / "tests").is_dir():
            likely_commands.append(f"{shlex.quote(sys.executable)} -m unittest discover -s tests -v")
        package_script_lines: list[str] = []
        if (self.project_root / "package.json").exists():
            try:
                package = json.loads((self.project_root / "package.json").read_text(encoding="utf-8"))
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            except Exception:
                scripts = {}
            likely_commands.append("npm test")
            if isinstance(scripts, dict) and scripts:
                package_script_lines = ["Package scripts:"]
                package_script_lines.extend(f"- {name}: {command}" for name, command in sorted(scripts.items())[:8])
        if (self.project_root / "Cargo.toml").exists():
            likely_commands.append("cargo test")
        if (self.project_root / "go.mod").exists():
            likely_commands.append("go test ./...")

        overview_lines = [
            f"Project root: {self.project_root}",
            "Detected markers:",
            *(found_markers or ["- none"]),
            "File counts:",
            f"- Python: {len(py_files)}",
            f"- JavaScript: {len(js_files)}",
            f"- TypeScript: {len(ts_files)}",
            "Test directories:",
            *(f"- {item}" for item in (test_dirs[:10] or ["none"])),
            "Likely verification commands:",
            *(f"- {cmd}" for cmd in (likely_commands or ["inspect project files first"])),
            "Project instruction files:",
            *self._project_instruction_file_lines(),
            "Project-local skills:",
            *self._project_local_skill_lines(),
        ]
        overview_lines.extend(package_script_lines)
        return "\n".join(overview_lines)

    def diff(self, text: str) -> str:
        raw = text.strip()
        git_root = self._local_git_root()
        snapshots = self._snapshot_diffs(raw)
        if git_root is not None and git_root != self.project_root.resolve() and snapshots:
            return "Project root is nested under a parent git repository; showing in-memory edit snapshot diff:\n" + "\n".join(snapshots)

        args = ["git", "diff", "--no-ext-diff", "--"]
        if raw:
            args.append(raw)
        result = self._run_subprocess(args, timeout=TERMINAL_TIMEOUT_SECONDS, label="diff")
        if "exit code: 129" not in result and "Not a git repository" not in result:
            return result
        if snapshots:
            return "No git repository; showing in-memory edit snapshot diff:\n" + "\n".join(snapshots)
        if raw:
            return result + "\n\nNo in-memory edit snapshot exists for this file yet."
        return result

    def checkpoint(self, text: str) -> str:
        """手动创建或列出 checkpoint 的工具入口。"""
        raw = text.strip()
        if not raw or raw == "create":
            record = self.checkpoints.create(reason="manual")
            return f"Created checkpoint {record.checkpoint_id}: {record.file_count} files. Reason: {record.reason}"
        action, _, arg = raw.partition(" ")
        if action.lower() in {"list", "ls"}:
            records = self.checkpoints.list_records(limit=10)
            if not records:
                return "No checkpoints found."
            lines = ["Checkpoints:"]
            for record in records:
                created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created_at))
                lines.append(f"- {record.checkpoint_id}: {record.file_count} files, {created}, {record.reason}")
            return "\n".join(lines)
        if action.lower() == "create":
            record = self.checkpoints.create(reason=arg or "manual")
            return f"Created checkpoint {record.checkpoint_id}: {record.file_count} files. Reason: {record.reason}"
        return "Usage: checkpoint [list|create <reason>]"

    def rollback(self, text: str) -> str:
        """恢复 checkpoint；默认恢复最近一次快照。"""
        raw = text.strip()
        checkpoint_id = None if not raw or raw == "latest" else raw
        return self.checkpoints.restore(checkpoint_id=checkpoint_id)

    def run_tests(self, text: str) -> str:
        """用保守默认值运行有超时限制的项目测试命令。"""
        raw = text.strip()
        if not raw:
            if (self.project_root / "package.json").exists() and not (self.project_root / "tests").is_dir():
                args = ["npm", "test"]
            elif (self.project_root / "Cargo.toml").exists():
                args = ["cargo", "test"]
            elif (self.project_root / "go.mod").exists():
                args = ["go", "test", "./..."]
            else:
                args = [sys.executable, "-m", "unittest", *DEFAULT_TEST_ARGS]
        else:
            parsed = shlex.split(raw)
            if not parsed:
                args = [sys.executable, "-m", "unittest", *DEFAULT_TEST_ARGS]
            elif parsed[0] in {"python", "python3"}:
                args = parsed
            elif parsed[0] == "unittest":
                args = [sys.executable, "-m", *parsed]
            elif parsed[0] in EXTERNAL_TEST_COMMANDS:
                args = parsed
            elif (self.project_root / parsed[0]).is_dir():
                args = [sys.executable, "-m", "unittest", "discover", "-s", parsed[0], "-v", *parsed[1:]]
            else:
                args = [sys.executable, "-m", "unittest", *parsed]
        result = self._run_subprocess(args, timeout=RUN_TESTS_TIMEOUT_SECONDS, label="run_tests", env=self._test_env())
        if "exit code: 0" not in result or "Ran 0 tests" in result:
            failure_type = "zero_tests" if "Ran 0 tests" in result else "test_failure"
            self._record_experience_card(
                status="failed",
                failure_type=failure_type,
                target="run_tests",
                lesson="Inspect failing tests and patch source before rerunning.",
                evidence=result.splitlines()[:80],
                source="run_tests",
            )
        return result

    def terminal(self, text: str) -> str:
        command = text.strip()
        refusal = self._terminal_refusal_reason(command)
        if refusal:
            return refusal
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.project_root,
                text=True,
                capture_output=True,
                timeout=TERMINAL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return f"Terminal command timed out after {TERMINAL_TIMEOUT_SECONDS}s: {command}"
        output = [
            f"$ {command}",
            f"exit code: {completed.returncode}",
        ]
        if completed.stdout:
            output.extend(["", "stdout:", completed.stdout.rstrip()])
        if completed.stderr:
            output.extend(["", "stderr:", completed.stderr.rstrip()])
        if not completed.stdout and not completed.stderr:
            output.extend(["", "(no output)"])
        artifact_path = self._save_tool_output_artifact("terminal", command, "\n".join(output), completed.returncode)
        if artifact_path:
            output.extend(["", f"artifact: {artifact_path}"])
        return self._truncate("\n".join(output))

    def _next_background_id(self) -> str:
        self._background_counter += 1
        return f"bg{self._background_counter}"

    def _background_status_label(self, task: BackgroundTask) -> str:
        returncode = task.process.poll()
        with task.lock:
            if returncode is not None:
                task.returncode = returncode
                if task.completed_at is None:
                    task.completed_at = time.time()
            if task.returncode is None:
                return "running"
            return f"done exit={task.returncode}"

    def _format_background_tail(self, task: BackgroundTask, line_limit: int = 20) -> str:
        with task.lock:
            lines = list(task.output[-line_limit:])
        if not lines:
            return "(no output yet)"
        return "\n".join(lines)

    def _record_background_completion(self, task: BackgroundTask, *, use_fresh_store: bool = False) -> None:
        with task.lock:
            if task.returncode is None or task.completion_recorded or task.completion_recording:
                return
            task.completion_recording = True
            tail = "\n".join(task.output[-12:]) if task.output else "(no output yet)"
            content = (
                f"[background:{task.task_id}] completed with exit code {task.returncode}\n"
                f"$ {task.command}\n"
                f"{tail}"
            )
            session_id = task.session_id
        try:
            if use_fresh_store:
                store = SessionStore(path=self.sessions.path)
                try:
                    store.append("assistant", content, session_id=session_id, kind="background_result", tool_name="background")
                finally:
                    store.conn.close()
            else:
                self.sessions.append("assistant", content, session_id=session_id, kind="background_result", tool_name="background")
        except Exception:
            with task.lock:
                task.completion_recording = False
            return
        with task.lock:
            task.completion_recorded = True
            task.completion_recording = False

    def drain_background_events(self) -> str:
        """返回尚未交付给 agent loop 的后台完成事件。

        这是 Hermes `notify_on_complete` 的精简版：后台命令由工具层监控，
        完成事件被 agent 主循环主动 drain 并注入下一轮 planner，而不是要求
        模型持续调用 `background wait`。同一个任务的完成事件只交付一次。
        """
        events: list[str] = []
        for task_id, task in sorted(self._background_tasks.items()):
            status = self._background_status_label(task)
            if not status.startswith("done exit="):
                continue
            self._record_background_completion(task)
            with task.lock:
                if task.completion_event_delivered:
                    continue
                task.completion_event_delivered = True
                exit_code = task.returncode
                command = task.command
                tail = "\n".join(task.output[-12:]) if task.output else "(no output yet)"
            events.append(
                f"[background:{task_id}] completed with exit code {exit_code}\n"
                f"$ {command}\n"
                f"{tail}"
            )
            artifact_path = self._save_tool_output_artifact("background", command, events[-1], exit_code)
            if artifact_path:
                events[-1] += f"\nartifact: {artifact_path}"
        return "\n\n".join(events)

    def _drain_background_task(self, task: BackgroundTask) -> None:
        try:
            if task.process.stdout is not None:
                for line in task.process.stdout:
                    with task.lock:
                        task.output.append(line.rstrip("\n"))
                        if len(task.output) > 500:
                            task.output = task.output[-500:]
                task.process.stdout.close()
            returncode = task.process.wait()
            with task.lock:
                task.returncode = returncode
                task.completed_at = time.time()
            self._record_background_completion(task, use_fresh_store=True)
        except Exception as exc:
            with task.lock:
                task.output.append(f"[background monitor failed: {exc}]")
                task.returncode = task.process.poll()
                task.completed_at = time.time()

    def _background_start(self, command: str) -> str:
        command = command.strip()
        refusal = self._background_refusal_reason(command)
        if refusal:
            return refusal
        task_id = self._next_background_id()
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=self.project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=self._test_env(),
            )
        except Exception as exc:
            return f"Background task failed to start: {exc}"
        task = BackgroundTask(
            task_id=task_id,
            command=command,
            process=process,
            started_at=time.time(),
            session_id=self._current_session_id(),
        )
        self._background_tasks[task_id] = task
        thread = threading.Thread(target=self._drain_background_task, args=(task,), daemon=True)
        task.monitor_thread = thread
        thread.start()
        return f"Started background task {task_id}: {command}\nUse `background status {task_id}`, `background tail {task_id}`, or `background wait {task_id}`."

    def _background_list(self) -> str:
        if not self._background_tasks:
            return "No background tasks in this agent process."
        lines = ["Background tasks:"]
        for task_id, task in sorted(self._background_tasks.items()):
            status = self._background_status_label(task)
            elapsed = max(0.0, time.time() - task.started_at)
            self._record_background_completion(task)
            lines.append(f"- {task_id}: {status}, {elapsed:.1f}s, {task.command}")
        return "\n".join(lines)

    def _background_get(self, task_id: str) -> BackgroundTask | None:
        return self._background_tasks.get(task_id.strip())

    def _background_status(self, arg: str) -> str:
        task_id = arg.strip()
        if not task_id:
            return self._background_list()
        task = self._background_get(task_id)
        if task is None:
            return f"Unknown background task: {task_id}"
        status = self._background_status_label(task)
        self._record_background_completion(task)
        elapsed = max(0.0, time.time() - task.started_at)
        return f"{task.task_id}: {status}, {elapsed:.1f}s\n$ {task.command}"

    def _background_tail(self, arg: str) -> str:
        parts = arg.split()
        if not parts:
            return "Usage: background tail <id> [lines]"
        task = self._background_get(parts[0])
        if task is None:
            return f"Unknown background task: {parts[0]}"
        line_limit = 20
        if len(parts) > 1 and parts[1].isdigit():
            line_limit = max(1, min(int(parts[1]), 100))
        status = self._background_status_label(task)
        self._record_background_completion(task)
        return f"{task.task_id} tail ({status}):\n" + self._format_background_tail(task, line_limit=line_limit)

    def _background_wait(self, arg: str) -> str:
        parts = arg.split()
        if not parts:
            return "Usage: background wait <id> [seconds]"
        task = self._background_get(parts[0])
        if task is None:
            return f"Unknown background task: {parts[0]}"
        timeout = None
        if len(parts) > 1:
            try:
                timeout = max(0.1, min(float(parts[1]), 3600.0))
            except ValueError:
                return "Usage: background wait <id> [seconds]"
        try:
            returncode = task.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            tail = self._format_background_tail(task, line_limit=8)
            return (
                f"Background task {task.task_id} is still running after {timeout}s.\n"
                "Use `background tail` to harvest partial output, `background stop` if enough output exists, or proceed with another step; do not wait indefinitely.\n"
                f"Recent output:\n{tail}"
            )
        if task.monitor_thread is not None:
            task.monitor_thread.join(timeout=1)
        with task.lock:
            task.returncode = returncode
            task.completed_at = task.completed_at or time.time()
        self._record_background_completion(task)
        result = (
            f"Background task {task.task_id} completed with exit code {returncode}.\n"
            f"$ {task.command}\n"
            + self._format_background_tail(task, line_limit=20)
        )
        artifact_path = self._save_tool_output_artifact("background", task.command, result, returncode)
        if artifact_path:
            result += f"\nartifact: {artifact_path}"
        return result

    def _background_stop(self, arg: str) -> str:
        task_id = arg.strip()
        if not task_id:
            return "Usage: background stop <id>"
        task = self._background_get(task_id)
        if task is None:
            return f"Unknown background task: {task_id}"
        if task.process.poll() is not None:
            self._background_status_label(task)
            self._record_background_completion(task)
            return f"Background task {task.task_id} is already complete."
        task.process.terminate()
        try:
            returncode = task.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            task.process.kill()
            returncode = task.process.wait(timeout=5)
        if task.monitor_thread is not None:
            task.monitor_thread.join(timeout=1)
        with task.lock:
            task.returncode = returncode
            task.completed_at = time.time()
            task.output.append("[terminated by background stop]")
        self._record_background_completion(task)
        return f"Stopped background task {task.task_id} with exit code {returncode}."

    def background(self, text: str) -> str:
        """分发后台 shell 任务管理子命令。"""
        raw = text.strip()
        if not raw:
            return "Usage: background start <cmd> | list | status [id] | tail <id> | wait <id> [seconds] | stop <id>"
        action, _, arg = raw.partition(" ")
        action = action.lower()
        if action == "start":
            return self._background_start(arg)
        if action in {"list", "ls"}:
            return self._background_list()
        if action == "status":
            return self._background_status(arg)
        if action == "tail":
            return self._background_tail(arg)
        if action == "wait":
            return self._background_wait(arg)
        if action == "stop":
            return self._background_stop(arg)
        return "Unknown background action. Use: start, list, status, tail, wait, or stop."

    def write_file(self, text: str) -> str:
        """创建或覆盖文本文件；写入前先创建 checkpoint。"""
        usage = "Usage: write_file <path> <content> or write_file <path>\\n<content>"
        if " ::: " in text:
            path_text, content = text.split(" ::: ", 1)
        elif "\n" in text:
            path_text, content = text.split("\n", 1)
        else:
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return usage
            path_text, content = parts
        path, display_path, error = self._resolve_project_write_path(path_text, usage)
        if error:
            return error
        assert path is not None
        self.checkpoints.create(reason=f"before write_file {display_path}")
        self._remember_file_snapshot(display_path, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote file {display_path} ({len(content)} chars)."

    def patch_file(self, text: str) -> str:
        """精确替换文件文本；替换前先创建 checkpoint。"""
        usage = (
            "Usage: patch_file <path> ::: <exact target> ::: <replacement> "
            "or patch_file <path>\\n<target>\\n---\\n<replacement>"
        )
        if " ::: " in text:
            parts = text.split(" ::: ", 2)
            if len(parts) != 3:
                return usage
            path_text, target, replacement = parts
        elif "\n" not in text:
            return usage
        else:
            path_text, patch_text = text.split("\n", 1)
            separator = "\n---\n"
            if separator not in patch_text:
                return usage
            target, replacement = patch_text.split(separator, 1)
        if not target:
            return "Patch target cannot be empty."
        path, display_path, error = self._resolve_project_write_path(path_text, usage)
        if error:
            return error
        assert path is not None
        if not path.exists() or not path.is_file():
            return f"File not found: {display_path}"
        content = path.read_text(encoding="utf-8", errors="ignore")
        if target not in content:
            candidates = self._nearest_patch_candidates(target, content)
            if candidates:
                rendered = "\n---\n".join(candidates)
                return f"Target string not found in {display_path}.\nNearest candidate snippets:\n{rendered}"
            return f"Target string not found in {display_path}."
        self.checkpoints.create(reason=f"before patch_file {display_path}")
        self._remember_file_snapshot(display_path, path)
        updated = content.replace(target, replacement, 1)
        path.write_text(updated, encoding="utf-8")
        return f"Patched file {display_path}."

    def write_file_tool(self, text: str) -> str:
        return self.write_file(text)

    def patch_file_tool(self, text: str) -> str:
        return self.patch_file(text)

    def search_files(self, text: str) -> str:
        query = text.strip()
        if not query:
            return "Usage: search <text>"
        matches: List[str] = []
        for path in self.project_root.rglob("*"):
            if path.is_dir():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(self.project_root))
            if query.lower() in rel.lower():
                matches.append(rel)
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if re.search(re.escape(query), content, re.IGNORECASE):
                matches.append(rel)
            if len(matches) >= 20:
                break
        if not matches:
            return f"No matches for: {query}"
        return "Matches:\n" + "\n".join(f"- {m}" for m in matches)

    def summarize(self, text: str) -> str:
        subject = text.strip() or "this project"
        memory_block = self.memory.as_prompt_block()
        lines = [
            f"Simple Hermes summary for: {subject}",
            "",
            "This is a tiny educational clone of Hermes concepts.",
            "It has a small agent loop, a tool registry, split durable memory, session history, and a CLI.",
            "It is intentionally rule-based so the architecture is easy to understand.",
        ]
        if memory_block:
            lines.extend(["", memory_block])
        return "\n".join(lines)

    def delegate(self, text: str) -> str:
        if self.delegate_runner is None:
            return "Delegation is not available in this agent instance."
        if self._approval_required("delegate"):
            return "Approval required for delegate. Set SIMPLE_HERMES_REQUIRE_APPROVAL differently or disable the guard to proceed."
        task = text.strip()
        if not task:
            return "Usage: delegate <subtask>"
        return self.delegate_runner(task)

    def parallel_delegate(self, text: str) -> str:
        if self.delegate_runner is None:
            return "Parallel delegation is not available in this agent instance."
        if self._approval_required("parallel_delegate"):
            return "Approval required for parallel_delegate. Set SIMPLE_HERMES_REQUIRE_APPROVAL differently or disable the guard to proceed."
        task = text.strip()
        if not task:
            return "Usage: parallel_delegate <task1 ; task2 ; ...>"
        return self.delegate_runner(f"parallel::{task}")

    def fetch_url(self, text: str) -> str:
        """无认证头的轻量网页抓取工具，避免误带本机 API key。"""
        url = text.strip()
        if not url:
            return "Usage: fetch_url <https-url>"
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "fetch_url only supports public http(s) URLs."
        req = request.Request(
            url,
            headers={
                "User-Agent": "simple-hermes-codex/0.1",
                "Accept": "text/plain,text/html,application/json;q=0.9,*/*;q=0.1",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                content_type = resp.headers.get("content-type", "")
                raw = resp.read(MAX_FETCH_URL_CHARS + 1)
                status = getattr(resp, "status", "unknown")
        except Exception as exc:
            return f"fetch_url failed: {exc.__class__.__name__}: {exc}"
        text_preview = raw[:MAX_FETCH_URL_CHARS].decode("utf-8", errors="replace")
        if len(raw) > MAX_FETCH_URL_CHARS:
            text_preview += "\n...[truncated]..."
        return self._redact_secret_text(f"Fetched {url}\nstatus: {status}\ncontent-type: {content_type}\n\n{text_preview}")

    def dependency_scan(self, _: str) -> str:
        """本地依赖清单扫描；不访问 OSV 或其他外部漏洞服务。"""
        lines = ["Dependency scan (local manifests only; no vulnerability service contacted):"]
        package_json = self.project_root / "package.json"
        if package_json.exists():
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
            except Exception as exc:
                lines.append(f"- package.json: failed to parse ({exc.__class__.__name__})")
            else:
                deps = {}
                for section in ("dependencies", "devDependencies", "optionalDependencies"):
                    value = package.get(section, {}) if isinstance(package, dict) else {}
                    if isinstance(value, dict):
                        deps.update({f"{name} ({section})": version for name, version in value.items()})
                lines.append(f"- package.json dependencies: {len(deps)}")
                for name, version in sorted(deps.items())[:40]:
                    lines.append(f"  - {name}: {version}")
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            deps = re.findall(r"['\"]([A-Za-z0-9_.-]+[A-Za-z0-9_.<>=!~ -]*)['\"]", content)
            lines.append(f"- pyproject.toml quoted dependency-like entries: {len(deps)}")
            for item in deps[:40]:
                lines.append(f"  - {item}")
        requirements = sorted(self.project_root.glob("requirements*.txt"))
        for path in requirements[:8]:
            rel = path.relative_to(self.project_root)
            deps = [
                line.strip()
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            lines.append(f"- {rel}: {len(deps)} entries")
            for item in deps[:20]:
                lines.append(f"  - {item}")
        if len(lines) == 1:
            lines.append("- no supported dependency manifests found.")
        return "\n".join(lines)

    def credential_audit(self, _: str) -> str:
        """只列出疑似凭据文件路径，不读取文件内容。"""
        matches: list[str] = []
        for path in self.project_root.rglob("*"):
            if path.is_dir():
                continue
            try:
                rel = str(path.relative_to(self.project_root))
            except ValueError:
                rel = str(path)
            rel_lower = rel.lower()
            if path.name.lower() in SENSITIVE_AUDIT_NAMES:
                matches.append(rel)
            elif any(part in rel_lower for part in SENSITIVE_AUDIT_PARTS):
                matches.append(rel)
            elif path.suffix.lower() in SENSITIVE_SUFFIXES:
                matches.append(rel)
            if len(matches) >= 100:
                break
        if not matches:
            return "No likely credential files found under the project root. Contents were not read."
        return (
            "Likely credential-bearing files under project root (contents not read, values redacted by design):\n"
            + "\n".join(f"- {item}" for item in sorted(matches))
        )

    def help(self, _: str) -> str:
        return self.registry.help_text()
