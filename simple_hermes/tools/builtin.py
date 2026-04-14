from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List


from simple_hermes.agent.backend import LLMBackend
from simple_hermes.config import PermissionConfig, permission_config_from_env
from simple_hermes.state.checkpoints import CheckpointStore
from simple_hermes.state.memory import MemoryStore
from simple_hermes.state.session import SessionStore
from simple_hermes.state.continuity import ContinuityView
from simple_hermes.tools.registry import ToolRegistry

SENSITIVE_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "secrets.txt"}
SENSITIVE_PARTS = {".git", ".ssh"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".token"}
DANGEROUS_TERMINAL_COMMANDS = {"rm", "sudo", "su", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", "kill", "pkill", "killall"}
IGNORED_TREE_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_TOOL_OUTPUT_CHARS = 3000
MAX_READ_FILE_CHARS = 12000
TERMINAL_TIMEOUT_SECONDS = 10
RUN_TESTS_TIMEOUT_SECONDS = 30
DEFAULT_TREE_DEPTH = 2
DEFAULT_TEST_ARGS = ["discover", "-s", "tests", "-v"]
EXTERNAL_TEST_COMMANDS = {"npm", "pnpm", "yarn", "cargo", "go", "pytest"}


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
        if ">" in command:
            return "Refusing terminal command with shell redirection. Use write_file for file writes."
        if re.search(r"\bgit\s+(?:reset|clean|checkout|restore)\b", command):
            return "Refusing destructive git command."
        if self.permissions.allow_dangerous_terminal:
            return None
        for token in re.findall(r"[A-Za-z0-9_./-]+", command):
            if Path(token).name in DANGEROUS_TERMINAL_COMMANDS:
                return f"Refusing dangerous terminal command: {Path(token).name}"
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
        return self._run_subprocess(args, timeout=RUN_TESTS_TIMEOUT_SECONDS, label="run_tests", env=self._test_env())

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
        refusal = self._terminal_refusal_reason(command)
        if refusal:
            return refusal.replace("terminal", "background")
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
        self._background_status_label(task)
        self._record_background_completion(task)
        return f"{task.task_id} tail:\n" + self._format_background_tail(task, line_limit=line_limit)

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
            return f"Background task {task.task_id} is still running after {timeout}s."
        if task.monitor_thread is not None:
            task.monitor_thread.join(timeout=1)
        with task.lock:
            task.returncode = returncode
            task.completed_at = task.completed_at or time.time()
        self._record_background_completion(task)
        return (
            f"Background task {task.task_id} completed with exit code {returncode}.\n"
            f"$ {task.command}\n"
            + self._format_background_tail(task, line_limit=20)
        )

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

    def help(self, _: str) -> str:
        return self.registry.help_text()
