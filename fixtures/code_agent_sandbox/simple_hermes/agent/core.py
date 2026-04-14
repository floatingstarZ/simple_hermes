from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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

    def _tool_followup_message(self, original_message: str, tool_name: str, result: str) -> str:
        return (
            f"Original user request:\n{original_message}\n\n"
            f"Tool {tool_name} returned:\n{result}\n\n"
            "Use the tool result to answer the user's actual request directly. "
            "Only ask for another tool if the request still cannot be answered."
        )

    def _is_internal_tool_followup_message(self, message: str) -> bool:
        return message.startswith("Original user request:\n") and "\n\nTool " in message

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
            ("read ", "read"),
            ("tree", "tree"),
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
        if inferred_path and not self._is_internal_tool_followup_message(message):
            return "read", inferred_path
        return None, message

    def _fallback_text(self) -> str:
        return (
            "I do not have a real LLM backend. Try one of the explicit commands: "
            "help, remember <text>, remember_user <text>, memories, user_memories, history, recall <query>, read <file>, tree [path] [depth], "
            "terminal <command>, run_tests [unittest args], write_file <path> <content>, patch_file <path>\\n<target>\\n---\\n<replacement>, search <query>, summarize"
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
            prefix = f"[{row['role']}]"
            if row.get("tool_name"):
                prefix += f"<{row['tool_name']}>"
            if row.get("kind"):
                prefix += f"({row['kind']})"
            if len(content) > 1200:
                content = content[:1200] + "..."
            lines.append(f"{prefix} {content}")
        return "\n".join(lines)

    def plan(self, message: str) -> PlannerDecision:
        tool_name, arg = self._plan_tool(message)
        if tool_name:
            return PlannerDecision(
                kind="tool_call",
                text=f"Use tool {tool_name}",
                tool_call=ToolCall(name=tool_name, argument=arg),
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

    def _maybe_compress_history(self) -> None:
        history = self.sessions.history(session_id=self.session_id, limit=50)
        summary_exists = any(row["role"] == "summary" for row in history)
        if summary_exists or len(history) <= 10:
            return
        early = history[:6]
        summary_bits = "; ".join(f"{row['role']}: {row['content'][:60]}" for row in early)
        summary_text = f"Earlier conversation summary: {summary_bits}"
        self.sessions.append("summary", summary_text, session_id=self.session_id, kind="summary")
        continuation_id = self.sessions.create_continuation_session(self.session_id, title="continuation")
        self.sessions.append("summary", summary_text, session_id=continuation_id, kind="summary")
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

    def _record_tool_result(self, tool_name: str, result: str) -> None:
        self.sessions.append(
            "assistant",
            self._tool_result_message(tool_name, result),
            session_id=self.session_id,
            kind="tool_result",
            tool_name=tool_name,
        )

    def _record_assistant_text(self, text: str) -> None:
        self.sessions.append("assistant", text, session_id=self.session_id, kind="assistant_text")

    def run(self, message: str) -> AgentResponse:
        trace: List[AgentTraceStep] = []
        original_message = message
        self.sessions.append("user", message, session_id=self.session_id, kind="user_message")

        if self.backend is None:
            decision = self.plan(message)
            trace.append(AgentTraceStep(step=1, kind=decision.kind, content=decision.text, tool_name=decision.tool_call.name if decision.tool_call else None))
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
        for step in range(1, self.max_steps + 1):
            try:
                decision = self.plan(current_message)
            except Exception as e:
                error_text = f"Backend planning failed: {e}"
                self._record_assistant_text(error_text)
                trace.append(AgentTraceStep(step=step, kind="backend_error", content=error_text))
                self._maybe_compress_history()
                return AgentResponse(final_response=error_text, tool_used=last_tool_used, steps=step, trace=trace)
            trace.append(AgentTraceStep(step=step, kind=decision.kind, content=decision.text, tool_name=decision.tool_call.name if decision.tool_call else None))
            if decision.tool_call is None:
                self._record_assistant_text(decision.text)
                self._maybe_compress_history()
                return AgentResponse(final_response=decision.text, tool_used=last_tool_used, steps=step, trace=trace)

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
            if decision.tool_call.name == "read" and self._should_short_circuit_file_explanation(original_message):
                explained = self._explain_file_after_read(original_message, result)
                self._record_assistant_text(explained)
                trace.append(AgentTraceStep(step=step, kind="text", content=explained, tool_name=decision.tool_call.name))
                self._maybe_compress_history()
                return AgentResponse(final_response=explained, tool_used=decision.tool_call.name, steps=step, trace=trace)
            current_message = self._tool_followup_message(original_message, decision.tool_call.name, result)

        timeout_text = (
            "Stopped after reaching the tiny agent step limit. "
            f"Last tool result was:\n{last_text}"
        )
        self._record_assistant_text(timeout_text)
        trace.append(AgentTraceStep(step=self.max_steps, kind="text", content=timeout_text, tool_name=last_tool_used))
        self._maybe_compress_history()
        return AgentResponse(final_response=timeout_text, tool_used=last_tool_used, steps=self.max_steps, trace=trace)
