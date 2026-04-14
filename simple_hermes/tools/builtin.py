from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, List


from simple_hermes.agent.backend import LLMBackend
from simple_hermes.config import PermissionConfig, permission_config_from_env
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


class BuiltInTools:
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
        self.registry = ToolRegistry(allowed_tools=allowed_tools)
        self._register_tools()

    def _is_sensitive_path(self, path: Path) -> bool:
        parts = {part.lower() for part in path.parts}
        if parts & SENSITIVE_PARTS:
            return True
        name = path.name.lower()
        if name in SENSITIVE_NAMES:
            return True
        return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)

    def _register_tools(self) -> None:
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
        self.registry.register("diff", "Show git diff for the project or a project-relative path", self.diff)
        self.registry.register("terminal", "Run a guarded shell command in the project root", self.terminal)
        self.registry.register("run_tests", "Run Python unittest targets with a safer default command", self.run_tests)
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
            entries = [
                child for child in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                if child.name not in IGNORED_TREE_PARTS
            ]
            for index, child in enumerate(entries):
                connector = "└── " if index == len(entries) - 1 else "├── "
                suffix = "/" if child.is_dir() else ""
                lines.append(f"{prefix}{connector}{child.name}{suffix}")
                next_prefix = prefix + ("    " if index == len(entries) - 1 else "│   ")
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

    def _truncate(self, text: str) -> str:
        if len(text) <= MAX_TOOL_OUTPUT_CHARS:
            return text
        return text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]..."

    def _missing_path_message(self, display_path: str, path: Path) -> str:
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

    def diff(self, text: str) -> str:
        raw = text.strip()
        args = ["git", "diff", "--no-ext-diff", "--"]
        if raw:
            args.append(raw)
        return self._run_subprocess(args, timeout=TERMINAL_TIMEOUT_SECONDS, label="diff")

    def run_tests(self, text: str) -> str:
        raw = text.strip()
        if not raw:
            args = [sys.executable, "-m", "unittest", *DEFAULT_TEST_ARGS]
        else:
            parsed = shlex.split(raw)
            if not parsed:
                args = [sys.executable, "-m", "unittest", *DEFAULT_TEST_ARGS]
            elif parsed[0] in {"python", "python3"}:
                args = parsed
            elif parsed[0] == "unittest":
                args = [sys.executable, "-m", *parsed]
            else:
                args = [sys.executable, "-m", "unittest", *parsed]
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
        return self._run_subprocess(args, timeout=RUN_TESTS_TIMEOUT_SECONDS, label="run_tests", env=env)

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

    def write_file(self, text: str) -> str:
        usage = "Usage: write_file <path> <content> or write_file <path>\\n<content>"
        if " ::: " in text and "\n" not in text:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote file {display_path} ({len(content)} chars)."

    def patch_file(self, text: str) -> str:
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
            return f"Target string not found in {display_path}."
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
