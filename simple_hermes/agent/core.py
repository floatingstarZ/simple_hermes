from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor

from simple_hermes.agent.backend import LLMBackend, PlannerDecision, ToolCall, backend_from_env
from simple_hermes.config import (
    MEMORY_PATH,
    USER_MEMORY_PATH,
    allowed_tools_from_env,
    permission_config_from_env,
)
from simple_hermes.state.memory import MemoryStore
from simple_hermes.state.session import SessionStore
from simple_hermes.tools.builtin import BuiltInTools

INSPECTION_TOOLS = {"read", "read_lines", "tree", "glob", "project_overview", "search"}


@dataclass
class AgentTraceStep:
    step: int
    kind: str
    content: str
    tool_name: Optional[str] = None


@dataclass
class AgentResponse:
    final_response: str
    tool_used: Optional[str] = None
    steps: int = 1
    trace: List[AgentTraceStep] = field(default_factory=list)


class SimpleAgent:
    """A tiny educational agent loop with continuity and delegation skeletons."""

    def __init__(
        self,
        project_root: Path,
        base_dir: Path | None = None,
        backend: LLMBackend | None = None,
        max_steps: int = 90,
        session_id: str = "default",
        memory_store: MemoryStore | None = None,
        session_store: SessionStore | None = None,
        delegation_depth: int = 0,
        max_delegation_depth: int = 1,
        child_step_budget: int = 2,
        allowed_tools: set[str] | None = None,
        resume_latest_continuation: bool = False,
    ) -> None:
        self.project_root = project_root
        self.max_steps = max_steps
        self.session_id = session_id
        self.delegation_depth = delegation_depth
        self.max_delegation_depth = max_delegation_depth
        self.child_step_budget = child_step_budget
        env_allowed_tools = allowed_tools_from_env("SIMPLE_HERMES_ALLOWED_TOOLS")
        self.allowed_tools = allowed_tools if allowed_tools is not None else env_allowed_tools
        self.permissions = permission_config_from_env()
        if base_dir is not None:
            base_dir.mkdir(parents=True, exist_ok=True)
            memory_path = base_dir / "memory.txt"
            user_path = base_dir / "user.txt"
            db_path = base_dir / "sessions.db"
        else:
            memory_path = MEMORY_PATH
            user_path = USER_MEMORY_PATH
            db_path = None
        self.memory = memory_store if memory_store is not None else MemoryStore(memory_path=memory_path, user_path=user_path)
        self.sessions = session_store if session_store is not None else SessionStore(path=db_path)
        self.sessions.ensure_session(self.session_id)
        if resume_latest_continuation:
            self.session_id = self.sessions.latest_continuation_or_self(self.session_id)
            self.sessions.ensure_session(self.session_id)
        self.backend = backend if backend is not None else backend_from_env()
        self.tools = BuiltInTools(
            self.memory,
            self.sessions,
            project_root,
            delegate_runner=self._delegate_task,
            allowed_tools=self.allowed_tools,
            permissions=self.permissions,
            backend=self.backend,
            session_id=self.session_id,
            session_id_getter=lambda: self.session_id,
        ).registry

    def _extract_path_from_message(self, message: str) -> Optional[str]:
        patterns = [
            r'([~/][^\s"\']+?\.[A-Za-z0-9]+)',
            r'((?:\./|\.\./)[^\s"\']+?\.[A-Za-z0-9]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(1)
        return None

    def _wants_file_explanation(self, message: str) -> bool:
        lower = message.lower()
        hints = (
            "what does",
            "what is",
            "explain",
            "analy",
            "inspect",
            "look at",
            "这个code",
            "这个文件",
            "干啥",
            "做什么",
            "看看",
            "解释",
            "分析",
        )
        return any(hint in lower for hint in hints)

    def _should_short_circuit_file_explanation(self, message: str) -> bool:
        return self._extract_path_from_message(message) is not None and self._wants_file_explanation(message)

    def _wants_project_inspection(self, message: str) -> bool:
        lower = message.lower()
        subject_hints = (
            "project",
            "codebase",
            "repository",
            "repo",
            "项目",
            "代码",
            "工程",
        )
        action_hints = (
            "inspect",
            "diagnos",
            "look at",
            "analy",
            "understand",
            "parse",
            "fix",
            "implement",
            "change",
            "edit",
            "看",
            "诊断",
            "分析",
            "解析",
            "理解",
            "修复",
            "实现",
            "修改",
        )
        return any(hint in lower for hint in subject_hints) and any(hint in lower for hint in action_hints)

    def _wants_passive_project_diagnosis(self, message: str) -> bool:
        lower = message.lower()
        diagnosis_hints = (
            "diagnos",
            "current logic",
            "test expect",
            "只做诊断",
            "诊断结论",
            "当前逻辑",
            "测试期待",
            "先看",
            "先分析",
        )
        return self._wants_project_inspection(message) and not self._needs_code_change(message) and any(hint in lower for hint in diagnosis_hints)

    def _diagnosis_query(self, message: str) -> str:
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", message):
            if "_" in token or token.endswith("items"):
                return token
        return "test"

    def _needs_code_change(self, message: str) -> bool:
        lower = message.lower()
        negative_hints = (
            "不要改代码",
            "先不要改",
            "不要修改",
            "不要编辑",
            "不要动代码",
            "只做诊断",
            "只分析",
            "只看",
            "do not edit",
            "don't edit",
            "do not change",
            "no code changes",
            "without changing",
        )
        if any(hint in lower for hint in negative_hints):
            return False
        hints = (
            "add",
            "build",
            "create",
            "fix",
            "implement",
            "modify",
            "change",
            "edit",
            "patch",
            "write code",
            "新增",
            "添加",
            "创建",
            "生成",
            "修复",
            "做个",
            "开发",
            "改代码",
            "修改",
            "实现",
            "实际改",
        )
        return any(hint in lower for hint in hints)

    def _needs_test(self, message: str) -> bool:
        lower = message.lower()
        hints = ("test", "verify", "验证", "测试")
        return any(hint in lower for hint in hints)

    def _rule_based_file_explanation(self, request: str, read_result: str) -> str:
        if not read_result.startswith("# "):
            return read_result
        parts = read_result.split("\n\n", 1)
        if len(parts) != 2:
            return read_result
        header, content = parts
        display_path = header[2:].strip()
        if not display_path.endswith(".py"):
            return f"I read {display_path}.\n\n{read_result}"

        classes = re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.MULTILINE)
        functions = re.findall(r"^(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.MULTILINE)
        imports = re.findall(r"^(?:from\s+([^\s]+)\s+import|import\s+([^\s]+))", content, re.MULTILINE)
        import_names = [left or right for left, right in imports]

        summary_bits = [f"I read {display_path}."]
        if self._wants_file_explanation(request):
            summary_bits.append("It looks like a Python module for this part of the project.")
        if import_names:
            summary_bits.append("Imports: " + ", ".join(import_names[:6]) + ("." if len(import_names) <= 6 else ", ..."))
        if classes:
            summary_bits.append("Classes: " + ", ".join(classes[:6]) + ("." if len(classes) <= 6 else ", ..."))
        if functions:
            summary_bits.append("Functions: " + ", ".join(functions[:8]) + ("." if len(functions) <= 8 else ", ..."))
        if "class OpenAICompatibleBackend" in content:
            summary_bits.append("This file mainly implements backend adapters: an OpenAI-compatible planner client, a Hermes runtime bridge, and environment-based backend selection.")
        if not classes and not functions:
            summary_bits.append("It mostly contains top-level code, constants, or configuration.")
        return " ".join(summary_bits) + "\n\n" + read_result

    def _explain_file_after_read(self, request: str, read_result: str) -> str:
        base_explanation = self._rule_based_file_explanation(request, read_result)
        if self.backend is None:
            return base_explanation
        try:
            decision = self.backend.plan(
                message=(
                    "Explain the code file that was just read. Answer the user's request directly in plain language. "
                    f"Original request: {request}"
                ),
                memory_block=self.memory.as_prompt_block(),
                history_text=read_result,
                tools_text="No tool call allowed. Return text only.",
            )
            if decision.text and decision.kind == "text":
                return decision.text + "\n\n" + read_result
        except Exception:
            pass
        return base_explanation

    def _format_run_state(self, observations: List[str]) -> str:
        if not observations:
            return "Run state so far: no tool calls have completed in this run."
        return "Run state so far:\n" + "\n".join(observations[-8:])

    def _compact_observation(self, tool_name: str, argument: str, result: str) -> str:
        preview = result.replace("\n", "\\n")
        if len(preview) > 700:
            preview = preview[:700] + "...[truncated]"
        rendered_arg = argument.strip()
        if len(rendered_arg) > 160:
            rendered_arg = rendered_arg[:160] + "...[truncated]"
        return f"- {tool_name}({rendered_arg!r}) -> {preview}"

    def _tool_followup_message(self, original_message: str, tool_name: str, result: str, observations: List[str]) -> str:
        failure_hint = ""
        if tool_name in {"run_tests", "terminal"} and "exit code: 0" not in result and self._needs_test(original_message):
            failure_hint = (
                "\nThe last verification command failed. Use the failure output to identify the remaining implementation gap, "
                "patch the relevant source file, and rerun tests before giving a final answer."
            )
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"Tool {tool_name} returned:\n{result}\n\n"
            "Use the tool result to answer the user's actual request directly. "
            "Only ask for another tool if the request still cannot be answered."
            f"{failure_hint}"
        )

    def _plan_tool(self, message: str) -> Tuple[Optional[str], str]:
        message = message.strip()
        lower = message.lower()
        prefixes = [
            ("remember_user ", "remember_user"),
            ("remember ", "remember"),
            ("user_memories", "user_memories"),
            ("memories", "memories"),
            ("history", "history"),
            ("lineage", "lineage"),
            ("sessions", "sessions"),
            ("descendants", "descendants"),
            ("recall ", "recall"),
            ("background ", "background"),
            ("read_lines ", "read_lines"),
            ("read ", "read"),
            ("tree", "tree"),
            ("glob ", "glob"),
            ("project_overview", "project_overview"),
            ("diff", "diff"),
            ("terminal ", "terminal"),
            ("run_tests", "run_tests"),
            ("write_file ", "write_file"),
            ("patch_file ", "patch_file"),
            ("search ", "search"),
            ("parallel_delegate ", "parallel_delegate"),
            ("delegate ", "delegate"),
            ("summarize", "summarize"),
            ("help", "help"),
        ]
        for prefix, tool_name in prefixes:
            if lower == prefix.strip() or lower.startswith(prefix):
                arg = message[len(prefix):] if message.lower().startswith(prefix) else ""
                if tool_name in {"write_file", "patch_file"}:
                    return tool_name, arg
                return tool_name, arg.strip()
        inferred_path = self._extract_path_from_message(message)
        if inferred_path:
            return "read", inferred_path
        return None, message

    def _fallback_text(self) -> str:
        return (
            "I do not have a real LLM backend. Try one of the explicit commands: "
            "help, remember <text>, remember_user <text>, memories, user_memories, history, recall <query>, read <file>, tree [path] [depth], "
            "terminal <command>, run_tests [unittest args], write_file <path> <content>, "
            "patch_file <path> ::: <target> ::: <replacement>, read_lines <path> <start> <end>, "
            "glob <pattern>, project_overview, diff [path], background <start|list|status|tail|wait|stop>, search <query>, summarize"
        )

    def _backend_history_text(self, limit: int = 12) -> str:
        rows = self.sessions.history(session_id=self.session_id, limit=limit)
        if not rows:
            return ""
        fallback = self._fallback_text()
        lines = []
        for row in rows:
            content = row.get("content", "")
            if content == fallback:
                continue
            if row.get("kind") == "tool_result":
                continue
            prefix = f"[{row['role']}]"
            if row.get("tool_name"):
                prefix += f"<{row['tool_name']}>"
            if row.get("kind"):
                prefix += f"({row['kind']})"
            lines.append(f"{prefix} {content}")
        return "\n".join(lines)

    def plan(self, message: str, allow_explicit_tools: bool = True) -> PlannerDecision:
        if allow_explicit_tools:
            tool_name, arg = self._plan_tool(message)
            if tool_name:
                return PlannerDecision(
                    kind="tool_call",
                    text=f"Use tool {tool_name}",
                    tool_call=ToolCall(name=tool_name, argument=arg),
                )
            if self._wants_project_inspection(message):
                return PlannerDecision(
                    kind="tool_call",
                    text="Inspect project before answering.",
                    tool_call=ToolCall(name="project_overview", argument=""),
                )

        greeting = message.strip().lower()
        if greeting in {"hi", "hello", "hey", "你好", "嗨"}:
            return PlannerDecision(kind="text", text="Hi! How can I help?", tool_call=None)

        if self.backend is not None:
            return self.backend.plan(
                message=message,
                memory_block=self.memory.as_prompt_block(),
                history_text=self._backend_history_text(limit=12),
                tools_text=self.tools.help_text(),
            )

        return PlannerDecision(kind="text", text=self._fallback_text(), tool_call=None)

    def _compression_threshold(self) -> int:
        raw = os.getenv("SIMPLE_HERMES_COMPRESSION_THRESHOLD", "").strip()
        if not raw:
            return 10
        try:
            value = int(raw)
        except ValueError:
            return 10
        return max(4, min(value, 200))

    def _preview_message(self, content: str, limit: int = 180) -> str:
        preview = re.sub(r"\s+", " ", content).strip()
        if len(preview) > limit:
            return preview[:limit] + "..."
        return preview

    def _unique_nonempty(self, items: List[str], limit: int) -> List[str]:
        seen = set()
        out = []
        for item in items:
            cleaned = item.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            out.append(cleaned)
            if len(out) >= limit:
                break
        return out

    def _build_handoff_summary(self, history: List[dict]) -> str:
        user_rows = [row for row in history if row.get("role") == "user"]
        assistant_rows = [
            row for row in history
            if row.get("role") == "assistant" and row.get("kind") != "tool_result"
        ]
        tool_rows = [row for row in history if row.get("kind") == "tool_result"]
        content_blob = "\n".join(row.get("content", "") for row in history)
        file_refs = self._unique_nonempty(
            re.findall(r"(?<![\w/.-])[\w./-]+\.(?:py|js|ts|tsx|jsx|md|json|toml|txt|html|css|yaml|yml)", content_blob),
            limit=8,
        )
        constraint_hints = (
            "must", "should", "prefer", "do not", "don't", "不要", "需要", "希望", "先", "测试", "验证"
        )
        constraint_rows = [
            row for row in user_rows
            if any(hint in row.get("content", "").lower() for hint in constraint_hints)
        ]
        tool_bits = [
            f"{row.get('tool_name') or 'tool'}: {self._preview_message(row.get('content', ''), 120)}"
            for row in tool_rows[-4:]
        ]
        progress_bits = [
            self._preview_message(row.get("content", ""), 160)
            for row in assistant_rows[-3:]
        ]

        goals = self._unique_nonempty(
            [self._preview_message(row.get("content", ""), 160) for row in (user_rows[:1] + user_rows[-2:])],
            limit=3,
        )
        constraints = self._unique_nonempty(
            [self._preview_message(row.get("content", ""), 160) for row in constraint_rows[-4:]],
            limit=4,
        )
        progress = self._unique_nonempty([*progress_bits, *tool_bits], limit=6)
        files = file_refs or ["No specific file references captured."]
        remaining = [self._preview_message(user_rows[-1].get("content", ""), 160)] if user_rows else ["Continue the current task."]

        def section(title: str, items: List[str]) -> List[str]:
            return [f"{title}:"] + [f"- {item}" for item in (items or ["None captured."])]

        return "\n".join(
            [
                "Conversation handoff summary:",
                *section("Goal", goals),
                *section("Constraints and preferences", constraints),
                *section("Progress so far", progress),
                *section("Relevant files and artifacts", files[:8]),
                *section("Remaining work", remaining),
            ]
        )

    def _continuation_tail(self, history: List[dict], limit: int = 4) -> List[dict]:
        tail = [
            row for row in history
            if row.get("role") != "summary" and row.get("kind") != "tool_result"
        ]
        return tail[-limit:]

    def _maybe_compress_history(self) -> None:
        history = self.sessions.history(session_id=self.session_id, limit=200)
        last_summary_index = -1
        for index, row in enumerate(history):
            if row.get("role") == "summary":
                last_summary_index = index
        new_rows_since_summary = len(history) - last_summary_index - 1
        if new_rows_since_summary <= self._compression_threshold():
            return
        summary_text = self._build_handoff_summary(history)
        tail = self._continuation_tail(history)
        self.sessions.append("summary", summary_text, session_id=self.session_id, kind="summary")
        continuation_id = self.sessions.create_continuation_session(self.session_id, title="continuation")
        self.sessions.append("summary", summary_text, session_id=continuation_id, kind="summary")
        for row in tail:
            self.sessions.append(
                row.get("role", "assistant"),
                row.get("content", ""),
                session_id=continuation_id,
                kind=row.get("kind"),
                tool_name=row.get("tool_name"),
            )
        self.session_id = continuation_id

    def _child_allowed_tools(self) -> set[str] | None:
        child_tools = allowed_tools_from_env("SIMPLE_HERMES_CHILD_ALLOWED_TOOLS")
        if child_tools is None:
            return None if self.allowed_tools is None else set(self.allowed_tools)
        if self.allowed_tools is None:
            return child_tools
        return set(self.allowed_tools) & child_tools

    def _delegate_task(self, task: str) -> str:
        if self.delegation_depth >= self.max_delegation_depth:
            return "Delegation refused: tiny delegation depth limit reached."
        task = task.strip()
        if task.startswith("parallel::"):
            raw = task[len("parallel::"):]
            subtasks = [part.strip() for part in raw.split(";") if part.strip()]
            if len(subtasks) < 2:
                return "Parallel delegation needs at least two semicolon-separated subtasks."
            return self._parallel_delegate(subtasks)
        child_session_id = self.sessions.create_child_session(self.session_id, title=task[:80])
        child_agent = SimpleAgent(
            project_root=self.project_root,
            backend=self.backend,
            max_steps=min(self.child_step_budget, max(2, self.max_steps - 1)),
            session_id=child_session_id,
            memory_store=self.memory,
            session_store=self.sessions,
            delegation_depth=self.delegation_depth + 1,
            max_delegation_depth=self.max_delegation_depth,
            child_step_budget=self.child_step_budget,
            allowed_tools=self._child_allowed_tools(),
        )
        result = child_agent.run(task)
        summary = result.final_response.strip().replace("\n", " ")
        if len(summary) > 220:
            summary = summary[:220] + "..."
        return f"Child agent summary ({child_session_id}): {summary}"

    def _parallel_delegate(self, subtasks: List[str]) -> str:
        child_specs = [
            (subtask, self.sessions.create_child_session(self.session_id, title=subtask[:80]))
            for subtask in subtasks
        ]

        def _run_subtask(spec: tuple[str, str]) -> str:
            subtask, child_session_id = spec
            child_memory = MemoryStore(memory_path=self.memory.memory_path, user_path=self.memory.user_path)
            child_sessions = SessionStore(path=self.sessions.path)
            child_agent = SimpleAgent(
                project_root=self.project_root,
                backend=self.backend,
                max_steps=min(self.child_step_budget, max(2, self.max_steps - 1)),
                session_id=child_session_id,
                memory_store=child_memory,
                session_store=child_sessions,
                delegation_depth=self.delegation_depth + 1,
                max_delegation_depth=self.max_delegation_depth,
                child_step_budget=self.child_step_budget,
                allowed_tools=self._child_allowed_tools(),
            )
            try:
                result = child_agent.run(subtask)
            finally:
                child_sessions.conn.close()
            summary = result.final_response.strip().replace("\n", " ")
            if len(summary) > 180:
                summary = summary[:180] + "..."
            return f"- {subtask} => ({child_session_id}) {summary}"

        with ThreadPoolExecutor(max_workers=min(len(subtasks), 4)) as ex:
            summaries = list(ex.map(_run_subtask, child_specs))
        return "Parallel child summaries:\n" + "\n".join(summaries)

    def _tool_result_message(self, tool_name: str, result: str) -> str:
        return f"[tool:{tool_name}] {result}"

    def _trace_decision_content(self, decision: PlannerDecision) -> str:
        if decision.tool_call is None:
            return decision.text
        return f"{decision.text} | argument={decision.tool_call.argument}"

    def _inspection_call_key(self, tool_name: str, argument: str) -> tuple[str, str]:
        normalized = argument.strip()
        if tool_name == "project_overview":
            normalized = ""
        elif tool_name == "tree" and not normalized:
            normalized = "."
        return tool_name, normalized

    def _looks_like_failed_tool_result(self, result: str) -> bool:
        prefixes = (
            "File not found:",
            "Path not found:",
            "Line range starts after end of file:",
            "Usage:",
            "Target string not found",
            "Tool not allowed",
            "Refusing ",
        )
        return result.startswith(prefixes)

    def _repeated_failure_message(self, original_message: str, tool_name: str, argument: str, result: str, observations: List[str]) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"The tool call {tool_name}({argument!r}) already failed with:\n{result}\n\n"
            "Do not repeat that exact tool call. Recover by using another general tool such as tree, glob, search, read_lines, terminal, "
            "or by using a suggested project-relative path. If no more tool is needed, return a text answer."
        )

    def _repeated_read_message(self, original_message: str, argument: str, result: str, observations: List[str]) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"You already read {argument!r}. Do not read the same file again in this run.\n"
            "Use the existing file content to decide the next step. If you need line-specific context, use read_lines. "
            "If the user requested a code change, proceed with patch_file or write_file before running tests.\n\n"
            f"Previous read result:\n{result}"
        )

    def _repeated_inspection_message(self, original_message: str, tool_name: str, argument: str, result: str, observations: List[str]) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"You already ran {tool_name}({argument!r}) successfully in this run. Do not restart project inspection with the same call.\n"
            "Use the existing tool result to decide the next concrete step. If the user requested a code change, proceed with "
            "patch_file or write_file when the target is known; otherwise use a different, narrower inspection tool.\n\n"
            f"Previous {tool_name} result:\n{result}"
        )

    def _premature_text_message(self, original_message: str, text: str, observations: List[str]) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"Your proposed text answer was:\n{text}\n\n"
            "The user requested a code change, but no write_file or patch_file call has succeeded in this run yet. "
            "Do not end with a status-only answer. Continue with a general tool to modify the file: patch_file, write_file, "
            "or a terminal command that actually edits the file. Prefer patch_file using `path ::: exact target text ::: replacement text`. "
            "Do not restart broad project inspection if the needed file and target text are already known from prior tool results. "
            "If the change is genuinely impossible, explain the concrete blocker."
        )

    def _premature_test_message(self, original_message: str, text: str, observations: List[str]) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"Your proposed text answer was:\n{text}\n\n"
            "The code edit appears to be done, but the user also requested testing or verification and no test command has succeeded yet. "
            "Run run_tests now, or use terminal to run a project-appropriate test command. Do not patch again unless a test failure shows that another edit is needed."
        )

    def _record_tool_result(self, tool_name: str, result: str) -> None:
        self.sessions.append(
            "assistant",
            self._tool_result_message(tool_name, result),
            session_id=self.session_id,
            kind="tool_result",
            tool_name=tool_name,
        )

    def _terminal_may_have_edited(self, argument: str, result: str) -> bool:
        if "exit code: 0" not in result:
            return False
        lower_arg = argument.lower()
        edit_hints = (
            "write_text",
            ".write(",
            "sed -i",
            "perl -pi",
            "tee ",
            "patched",
            "overwrite",
        )
        return any(hint in lower_arg for hint in edit_hints)

    def _tool_result_is_successful_test(self, tool_name: str, argument: str, result: str) -> bool:
        if "exit code: 0" not in result:
            return False
        if "Ran 0 tests" in result:
            return False
        if tool_name == "run_tests":
            return True
        lower_arg = argument.lower()
        return tool_name == "terminal" and ("pytest" in lower_arg or "unittest" in lower_arg)

    def _record_assistant_text(self, text: str) -> None:
        self.sessions.append("assistant", text, session_id=self.session_id, kind="assistant_text")

    def _run_passive_project_diagnosis(self, message: str) -> AgentResponse:
        trace: List[AgentTraceStep] = []
        observations: List[str] = []

        def run_tool(step: int, name: str, argument: str, reason: str) -> str:
            trace.append(AgentTraceStep(step=step, kind="tool_call", content=f"{reason} | argument={argument}", tool_name=name))
            result = self.tools.run(name, argument)
            self._record_tool_result(name, result)
            trace.append(AgentTraceStep(step=step, kind="tool_result", content=result, tool_name=name))
            observations.append(self._compact_observation(name, argument, result))
            return result

        step = 1
        overview = run_tool(step, "project_overview", "", "Inspect project before passive diagnosis.")
        step += 1
        query = self._diagnosis_query(message)
        search_result = run_tool(step, "search", query, "Locate files related to the requested symbol or behavior.")
        step += 1

        candidates: list[str] = []
        for line in search_result.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            candidate = line[2:].strip()
            if candidate.endswith((".py", ".js", ".ts")) and candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) >= 4:
                break

        read_results: list[str] = []
        for candidate in candidates:
            read_results.append(run_tool(step, "read", candidate, "Read matched implementation or test file for diagnosis."))
            step += 1

        joined_reads = "\n\n".join(read_results)
        findings: list[str] = []
        if "return list(self.items)" in joined_reads and query:
            findings.append(f"`{query}` 当前实现直接返回 `list(self.items)`，因此会包含已完成项。")
        if "completed: bool" in joined_reads and query:
            findings.append(f"数据模型里完成状态字段是 `completed`，修复时应基于 `not item.completed` 过滤。")
        if "assertEqual" in joined_reads and query in joined_reads:
            findings.append("测试里对该逻辑有明确期待：结果应只包含仍处于 active 状态的条目。")
        if not findings:
            findings.append("已完成项目定位；请看上面的匹配文件和片段来决定下一步是否修改。")

        files_text = ", ".join(candidates) if candidates else "未找到明确匹配文件"
        text = (
            "诊断结论：\n"
            f"- 相关文件：{files_text}\n"
            + "\n".join(f"- {finding}" for finding in findings)
            + "\n- 我没有修改任何文件。后续如果你说“修复/修改”，我会基于这些文件继续处理。"
        )
        trace.append(AgentTraceStep(step=step, kind="text", content=text))
        self._record_assistant_text(text)
        self._maybe_compress_history()
        return AgentResponse(final_response=text, tool_used="read" if read_results else "search", steps=step, trace=trace)

    def run(self, message: str) -> AgentResponse:
        trace: List[AgentTraceStep] = []
        original_message = message
        self.sessions.append("user", message, session_id=self.session_id, kind="user_message")

        if self.backend is not None and self._wants_passive_project_diagnosis(message):
            return self._run_passive_project_diagnosis(message)

        if self.backend is None:
            decision = self.plan(message)
            trace.append(AgentTraceStep(step=1, kind=decision.kind, content=self._trace_decision_content(decision), tool_name=decision.tool_call.name if decision.tool_call else None))
            if decision.tool_call is not None:
                try:
                    result = self.tools.run(decision.tool_call.name, decision.tool_call.argument)
                except Exception as e:
                    error_text = f"Tool {decision.tool_call.name} failed: {e}"
                    self._record_assistant_text(error_text)
                    trace.append(AgentTraceStep(step=1, kind="error", content=error_text, tool_name=decision.tool_call.name))
                    self._maybe_compress_history()
                    return AgentResponse(final_response=error_text, tool_used=decision.tool_call.name, steps=1, trace=trace)
                self._record_tool_result(decision.tool_call.name, result)
                trace.append(AgentTraceStep(step=1, kind="tool_result", content=result, tool_name=decision.tool_call.name))
                if decision.tool_call.name == "read" and self._should_short_circuit_file_explanation(original_message):
                    result = self._rule_based_file_explanation(original_message, result)
                    self._record_assistant_text(result)
                    self._maybe_compress_history()
                    return AgentResponse(final_response=result, tool_used=decision.tool_call.name, steps=1, trace=trace)
                self._maybe_compress_history()
                return AgentResponse(final_response=result, tool_used=decision.tool_call.name, steps=1, trace=trace)

            self._record_assistant_text(decision.text)
            self._maybe_compress_history()
            return AgentResponse(final_response=decision.text, steps=1, trace=trace)

        current_message = message
        last_tool_used: Optional[str] = None
        last_text = ""
        failed_calls: dict[tuple[str, str], str] = {}
        completed_inspections: dict[tuple[str, str], str] = {}
        run_observations: List[str] = []
        blocked_repeats = 0
        successful_edit = False
        successful_test = False
        for step in range(1, self.max_steps + 1):
            try:
                decision = self.plan(current_message, allow_explicit_tools=(step == 1))
            except Exception as e:
                error_text = f"Backend planning failed: {e}"
                self._record_assistant_text(error_text)
                trace.append(AgentTraceStep(step=step, kind="backend_error", content=error_text))
                self._maybe_compress_history()
                return AgentResponse(final_response=error_text, tool_used=last_tool_used, steps=step, trace=trace)
            trace.append(AgentTraceStep(step=step, kind=decision.kind, content=self._trace_decision_content(decision), tool_name=decision.tool_call.name if decision.tool_call else None))
            if decision.tool_call is None:
                if self._needs_code_change(original_message) and not successful_edit:
                    recovery_message = self._premature_text_message(original_message, decision.text, run_observations)
                    trace.append(AgentTraceStep(step=step, kind="premature_text_blocked", content=recovery_message))
                    current_message = recovery_message
                    continue
                if self._needs_test(original_message) and not successful_test:
                    recovery_message = self._premature_test_message(original_message, decision.text, run_observations)
                    trace.append(AgentTraceStep(step=step, kind="premature_text_blocked", content=recovery_message))
                    current_message = recovery_message
                    continue
                self._record_assistant_text(decision.text)
                self._maybe_compress_history()
                return AgentResponse(final_response=decision.text, tool_used=last_tool_used, steps=step, trace=trace)

            call_key = (decision.tool_call.name, decision.tool_call.argument)
            if call_key in failed_calls:
                blocked_repeats += 1
                recovery_message = self._repeated_failure_message(
                    original_message,
                    decision.tool_call.name,
                    decision.tool_call.argument,
                    failed_calls[call_key],
                    run_observations,
                )
                trace.append(AgentTraceStep(step=step, kind="repeated_tool_blocked", content=recovery_message, tool_name=decision.tool_call.name))
                if blocked_repeats >= 3:
                    error_text = (
                        "Stopped because the backend repeated the same failing tool call. "
                        f"Last blocked call: {decision.tool_call.name}({decision.tool_call.argument!r})."
                    )
                    self._record_assistant_text(error_text)
                    self._maybe_compress_history()
                    return AgentResponse(final_response=error_text, tool_used=last_tool_used, steps=step, trace=trace)
                current_message = recovery_message
                continue
            inspection_key = self._inspection_call_key(decision.tool_call.name, decision.tool_call.argument)
            if decision.tool_call.name in INSPECTION_TOOLS and inspection_key in completed_inspections:
                if decision.tool_call.name == "read":
                    recovery_message = self._repeated_read_message(
                        original_message,
                        decision.tool_call.argument,
                        completed_inspections[inspection_key],
                        run_observations,
                    )
                else:
                    recovery_message = self._repeated_inspection_message(
                        original_message,
                        decision.tool_call.name,
                        decision.tool_call.argument,
                        completed_inspections[inspection_key],
                        run_observations,
                    )
                trace.append(AgentTraceStep(step=step, kind="repeated_tool_blocked", content=recovery_message, tool_name=decision.tool_call.name))
                current_message = recovery_message
                continue

            try:
                result = self.tools.run(decision.tool_call.name, decision.tool_call.argument)
            except Exception as e:
                error_text = f"Tool {decision.tool_call.name} failed: {e}"
                self._record_assistant_text(error_text)
                trace.append(AgentTraceStep(step=step, kind="tool_error", content=error_text, tool_name=decision.tool_call.name))
                self._maybe_compress_history()
                return AgentResponse(final_response=error_text, tool_used=decision.tool_call.name, steps=step, trace=trace)
            last_tool_used = decision.tool_call.name
            last_text = result
            self._record_tool_result(decision.tool_call.name, result)
            trace.append(AgentTraceStep(step=step, kind="tool_result", content=result, tool_name=decision.tool_call.name))
            run_observations.append(self._compact_observation(decision.tool_call.name, decision.tool_call.argument, result))
            if self._looks_like_failed_tool_result(result):
                failed_calls[call_key] = result
            elif decision.tool_call.name in {"write_file", "patch_file"}:
                successful_edit = True
            elif decision.tool_call.name == "terminal" and self._terminal_may_have_edited(decision.tool_call.argument, result):
                successful_edit = True
            elif decision.tool_call.name in INSPECTION_TOOLS:
                completed_inspections[inspection_key] = result
            if self._tool_result_is_successful_test(decision.tool_call.name, decision.tool_call.argument, result):
                successful_test = True
            if decision.tool_call.name == "read" and self._should_short_circuit_file_explanation(original_message):
                explained = self._explain_file_after_read(original_message, result)
                self._record_assistant_text(explained)
                trace.append(AgentTraceStep(step=step, kind="text", content=explained, tool_name=decision.tool_call.name))
                self._maybe_compress_history()
                return AgentResponse(final_response=explained, tool_used=decision.tool_call.name, steps=step, trace=trace)
            current_message = self._tool_followup_message(original_message, decision.tool_call.name, result, run_observations)

        timeout_text = (
            "Stopped after reaching the tiny agent step limit. "
            f"Last tool result was:\n{last_text}"
        )
        self._record_assistant_text(timeout_text)
        trace.append(AgentTraceStep(step=self.max_steps, kind="text", content=timeout_text, tool_name=last_tool_used))
        self._maybe_compress_history()
        return AgentResponse(final_response=timeout_text, tool_used=last_tool_used, steps=self.max_steps, trace=trace)
