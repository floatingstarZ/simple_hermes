from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import os
import re
import threading
import time
import uuid
from typing import Callable, Optional, Tuple, List
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
ACTIVE_TASK_STATE_KEY = "active_task"
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "MEMORY.md")
PROJECT_CONTEXT_FILE_CHAR_LIMIT = 3500
PROJECT_CONTEXT_TOTAL_CHAR_LIMIT = 10000
PROJECT_SKILL_SUMMARY_LIMIT = 40
PROJECT_SKILL_READ_CHAR_LIMIT = 2500
SECRET_REDACTION_PATTERNS = (
    r"(?i)(api[_-]?key|token|secret|password)(\s*[:=]\s*)[^\s,;]+",
    r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+",
    r"sk-[A-Za-z0-9_-]{8,}",
    r"gh[pousr]_[A-Za-z0-9_]{8,}",
)


@dataclass
class BackgroundAgentTask:
    task_id: str
    prompt: str
    session_id: str
    started_at: float
    status: str = "running"
    result: str = ""
    error: str = ""
    completed_at: float | None = None
    thread: threading.Thread | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


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
    """带持久状态和受控工具调用的小型 code agent 主循环。

    这里刻意拆成两层：
    - 本地确定性控制流负责会话、工具、回滚、重复失败恢复；
    - 后端 planner 负责自然语言意图，例如这轮是否需要编辑、是否需要验证。

    这个边界很重要：本地代码不要重新引入“包含 fix 就认为要编辑”这类宽泛的
    关键词意图路由；是否需要编辑/测试应由 planner 通过
    `PlannerDecision.requires_edit` 和 `PlannerDecision.requires_test` 表达。
    """

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
        self._background_agent_counter = 0
        self._background_agent_tasks: dict[str, BackgroundAgentTask] = {}
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

    def _load_active_task(self) -> dict | None:
        """读取持久化的当前编码任务；遇到损坏状态时直接丢弃。"""
        raw = self.sessions.get_state(self.session_id, ACTIVE_TASK_STATE_KEY)
        if not raw:
            return None
        try:
            task = json.loads(raw)
        except json.JSONDecodeError:
            self.sessions.delete_state(self.session_id, ACTIVE_TASK_STATE_KEY)
            return None
        return task if isinstance(task, dict) else None

    def _save_active_task(self, task: dict) -> None:
        """把当前任务以 JSON 形式写入会话局部状态表。"""
        task["updated_at"] = time.time()
        self.sessions.set_state(self.session_id, ACTIVE_TASK_STATE_KEY, json.dumps(task, ensure_ascii=False))

    def _start_active_task(self, message: str, category: str = "coding") -> dict:
        """在 planner 声明需要编辑之后，创建一个可跨轮次延续的任务框架。"""
        task = {
            "id": uuid.uuid4().hex[:12],
            "category": category,
            "goal": message.strip(),
            "status": "in_progress",
            "created_at": time.time(),
            "updated_at": time.time(),
            "last_user_message": message.strip(),
        }
        self._save_active_task(task)
        return task

    def _set_active_task_status(self, status: str) -> None:
        """只更新当前任务状态，不隐式创建新任务。"""
        task = self._load_active_task()
        if task is None:
            return
        task["status"] = status
        self._save_active_task(task)

    def _is_explicit_separate_message(self, message: str) -> bool:
        """只识别硬性的 UI/工具边界，不判断自然语言意图。

        类似 “HTML version” 或 “continue” 的短追问应继续挂在当前任务上。
        显式工具命令则保持独立，因为这时用户是在直接控制工具层。
        """
        stripped = message.strip()
        if not stripped:
            return True
        if self._plan_tool(stripped)[0] is not None:
            return True
        return False

    def _resolve_task_frame(self, message: str) -> tuple[str, dict | None, str | None]:
        """在合适的时候把短追问接到已有编码任务上。

        新任务不再通过这里的关键词启发式识别。只有后端 planner 设置
        `requires_edit=True` 后，才会创建新的任务框架。
        """
        task = self._load_active_task()
        if task is None or task.get("category") != "coding":
            return message, task, None
        if task.get("status") not in {"in_progress", "awaiting_user"}:
            return message, task, None
        if self._is_explicit_separate_message(message):
            return message, task, None

        task["last_user_message"] = message.strip()
        self._save_active_task(task)
        expanded = (
            "Continue the active coding task from session state.\n"
            f"Active task id: {task.get('id')}\n"
            f"Active task goal: {task.get('goal')}\n"
            f"Current user follow-up: {message}\n"
            "Treat the follow-up as additional constraints or permission for the active task. "
            "Do not ask for more confirmation unless the task is truly impossible; inspect the project if needed, then create or edit the appropriate project file."
        )
        return expanded, task, "continued_task"

    def _format_run_state(self, observations: List[str]) -> str:
        """把本轮工具观察结果压缩成下一步 planner 可读的状态摘要。"""
        if not observations:
            return "Run state so far: no tool calls have completed in this run."
        return "Run state so far:\n" + "\n".join(observations[-8:])

    def _compact_observation(self, tool_name: str, argument: str, result: str) -> str:
        """压缩单个工具结果，避免下一轮 planner prompt 过长。"""
        preview = result.replace("\n", "\\n")
        if len(preview) > 700:
            preview = preview[:700] + "...[truncated]"
        rendered_arg = argument.strip()
        if len(rendered_arg) > 160:
            rendered_arg = rendered_arg[:160] + "...[truncated]"
        return f"- {tool_name}({rendered_arg!r}) -> {preview}"

    def _tool_followup_message(self, original_message: str, tool_name: str, result: str, observations: List[str]) -> str:
        """工具调用之后构造下一轮 planner 消息。

        后端会同时看到原始请求、简短运行状态和当前工具的完整结果。这样模型能基于
        真实观察继续推进，而不是依赖 Python 侧隐藏的意图推断。
        """
        failure_hint = ""
        if tool_name in {"run_tests", "terminal"} and "exit code: 0" not in result:
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
        """只解析显式工具命令。

        这里刻意不从自然语言路径推断 `read`，也不从“分析项目”这类表达推断
        `project_overview`。真实 LLM 模式下，这些选择应交给后端 planner。
        """
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
            ("recall_all ", "recall_all"),
            ("recall ", "recall"),
            ("skills ", "skills"),
            ("skills", "skills"),
            ("cron ", "cron"),
            ("cron", "cron"),
            ("mcp ", "mcp"),
            ("mcp", "mcp"),
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
            ("fetch_url ", "fetch_url"),
            ("dependency_scan", "dependency_scan"),
            ("credential_audit", "credential_audit"),
            ("summarize", "summarize"),
            ("help", "help"),
        ]
        for prefix, tool_name in prefixes:
            if lower == prefix.strip() or lower.startswith(prefix):
                arg = message[len(prefix):] if message.lower().startswith(prefix) else ""
                if tool_name in {"write_file", "patch_file"}:
                    return tool_name, arg
                return tool_name, arg.strip()
        return None, message

    def _fallback_text(self) -> str:
        return (
            "I do not have a real LLM backend. Try one of the explicit commands: "
            "help, remember <text>, remember_user <text>, memories, user_memories, history, recall <query>, read <file>, tree [path] [depth], "
            "terminal <command>, run_tests [unittest args], write_file <path> <content>, "
            "patch_file <path> ::: <target> ::: <replacement>, read_lines <path> <start> <end>, "
            "glob <pattern>, project_overview, diff [path], background <start|list|status|tail|wait|stop>, "
            "skills <list|view|use|create>, cron <add|list|run-due|run|delete>, recall_all <query>, mcp <resources|sessions|session|search>, "
            "fetch_url <url>, dependency_scan, credential_audit, search <query>, summarize"
        )

    def _backend_history_text(self, limit: int = 12) -> str:
        """为后端 planner 准备最近对话上下文。"""
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

    def _redact_project_context(self, text: str) -> str:
        """清理项目级上下文里的常见密钥形态，避免 planner prompt 泄露凭证。"""
        redacted = text
        for pattern in SECRET_REDACTION_PATTERNS:
            redacted = re.sub(
                pattern,
                lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]" if len(m.groups()) >= 2 else "[REDACTED]",
                redacted,
            )
        return redacted

    def _bounded_project_file_excerpt(self, path: Path, limit: int = PROJECT_CONTEXT_FILE_CHAR_LIMIT) -> str | None:
        """读取项目指令文件的有界片段；失败时跳过而不是阻断规划。"""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        text = self._redact_project_context(text.strip())
        if len(text) > limit:
            text = text[:limit] + "\n...[truncated; inspect the full file with read when relevant]"
        return text

    def _project_instruction_excerpts(self) -> list[tuple[str, str]]:
        """收集常见项目级 agent 指令文件的短摘录。"""
        excerpts: list[tuple[str, str]] = []
        for filename in PROJECT_INSTRUCTION_FILES:
            path = self.project_root / filename
            if not path.is_file():
                continue
            excerpt = self._bounded_project_file_excerpt(path)
            if excerpt:
                excerpts.append((filename, excerpt))
        return excerpts

    def _parse_project_skill_summary(self, skill_path: Path) -> tuple[str, str]:
        """从本地 skill 的 SKILL.md 抽取名称和描述。

        这里只读元信息，不展开脚本内容；真正使用技能前仍应 read 对应 SKILL.md。
        """
        name = skill_path.parent.name
        description = ""
        try:
            text = skill_path.read_text(encoding="utf-8", errors="replace")[:PROJECT_SKILL_READ_CHAR_LIMIT]
        except OSError:
            return name, description
        text = self._redact_project_context(text)
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
        return name, description

    def _project_skill_summaries(self) -> list[str]:
        """列出项目内 skills/*/SKILL.md，帮助 planner 知道应该先读哪个技能。"""
        skills_root = self.project_root / "skills"
        if not skills_root.is_dir():
            return []
        lines: list[str] = []
        for skill_path in sorted(skills_root.glob("*/SKILL.md"))[:PROJECT_SKILL_SUMMARY_LIMIT]:
            try:
                rel = str(skill_path.relative_to(self.project_root))
            except ValueError:
                rel = str(skill_path)
            name, description = self._parse_project_skill_summary(skill_path)
            suffix = f": {description}" if description else ""
            lines.append(f"- {name} ({rel}){suffix}")
        return lines

    def _project_context_text(self) -> str:
        """为 planner 构造通用项目上下文，不把任何具体项目偏好写死到代码里。"""
        instruction_excerpts = self._project_instruction_excerpts()
        skill_summaries = self._project_skill_summaries()
        if not instruction_excerpts and not skill_summaries:
            return ""

        lines = [
            "Project-level context:",
            "- Treat repository files as the source of truth; do not hardcode project-specific preferences in Simple Hermes.",
            "- Follow instruction files such as AGENTS.md, CLAUDE.md, and MEMORY.md when they are present.",
            "- When project-local skills are listed under skills/, read the relevant SKILL.md before running its scripts or terminal commands.",
        ]
        if instruction_excerpts:
            lines.append("Instruction file excerpts:")
            for rel, excerpt in instruction_excerpts:
                lines.append(f"## {rel}")
                lines.append(excerpt)
        if skill_summaries:
            lines.append("Project-local skills:")
            lines.extend(skill_summaries)
        text = "\n".join(lines)
        if len(text) > PROJECT_CONTEXT_TOTAL_CHAR_LIMIT:
            text = text[:PROJECT_CONTEXT_TOTAL_CHAR_LIMIT] + "\n...[project context truncated]"
        return text

    def _backend_memory_block(self) -> str:
        """合并长期记忆和项目级指令摘要，供后端 planner 使用。"""
        blocks = [self.memory.as_prompt_block().strip()]
        project_context = self._project_context_text().strip()
        if project_context:
            blocks.append(project_context)
        return "\n\n".join(block for block in blocks if block)

    def plan(self, message: str, allow_explicit_tools: bool = True) -> PlannerDecision:
        """从显式命令或后端 planner 获得一个规划决策。"""
        if allow_explicit_tools:
            tool_name, arg = self._plan_tool(message)
            if tool_name:
                return PlannerDecision(
                    kind="tool_call",
                    text=f"Use tool {tool_name}",
                    tool_call=ToolCall(name=tool_name, argument=arg),
                )
        if self.backend is not None:
            return self.backend.plan(
                message=message,
                memory_block=self._backend_memory_block(),
                history_text=self._backend_history_text(limit=12),
                tools_text=self.tools.help_text(),
            )

        return PlannerDecision(kind="text", text=self._fallback_text(), tool_call=None)

    def _compression_threshold(self) -> int:
        """读取历史压缩阈值，并在配置异常时使用有界默认值。"""
        raw = os.getenv("SIMPLE_HERMES_COMPRESSION_THRESHOLD", "").strip()
        if not raw:
            return 10
        try:
            value = int(raw)
        except ValueError:
            return 10
        return max(4, min(value, 200))

    def _preview_message(self, content: str, limit: int = 180) -> str:
        """生成交接摘要里使用的单行预览。"""
        preview = re.sub(r"\s+", " ", content).strip()
        if len(preview) > limit:
            return preview[:limit] + "..."
        return preview

    def _truncate_block(self, content: str, limit: int = 1200) -> str:
        """后端失败时压缩记忆/历史块，保证回退文本可读。"""
        content = content.strip()
        if len(content) > limit:
            return content[:limit] + "\n...[truncated]"
        return content

    def _backend_unavailable_fallback_text(self, error: Exception) -> str:
        """首个后端调用断开时，返回仍然有用的本地记忆和会话上下文。"""
        memory_block = self.memory.as_prompt_block().strip() or "No durable/user memories saved."
        history_text = self._backend_history_text(limit=8).strip() or "No recent session history."
        return (
            "Backend planning failed before any tool ran, so I fell back to local session context.\n\n"
            f"Error: {error}\n\n"
            "Local memories:\n"
            f"{self._truncate_block(memory_block)}\n\n"
            "Recent session context:\n"
            f"{self._truncate_block(history_text)}"
        )

    def _unique_nonempty(self, items: List[str], limit: int) -> List[str]:
        """对摘要候选项去重，同时保持原始顺序。"""
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
        """为长会话生成可延续的压缩摘要。

        这只是对已存历史的轻量摘要器，不是 planner。它抽取明显的产物、约束和进展，
        让 continuation session 在原始历史很长时也能保留足够上下文。
        """
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
        """压缩后仍原样保留最近几条非工具消息。"""
        tail = [
            row for row in history
            if row.get("role") != "summary" and row.get("kind") != "tool_result"
        ]
        return tail[-limit:]

    def _maybe_compress_history(self) -> None:
        """当新消息累计到阈值后，创建 continuation session。"""
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

    def compress_now(self) -> str:
        """手动 `/compress` 入口，用于强制创建 continuation session。"""
        history = self.sessions.history(session_id=self.session_id, limit=200)
        if not history:
            return "No session history to compress."
        summary_text = self._build_handoff_summary(history)
        tail = self._continuation_tail(history)
        self.sessions.append("summary", summary_text, session_id=self.session_id, kind="summary")
        continuation_id = self.sessions.create_continuation_session(self.session_id, title="manual compression")
        self.sessions.append("summary", summary_text, session_id=continuation_id, kind="summary")
        for row in tail:
            self.sessions.append(
                row.get("role", "assistant"),
                row.get("content", ""),
                session_id=continuation_id,
                kind=row.get("kind"),
                tool_name=row.get("tool_name"),
            )
        previous_session = self.session_id
        self.session_id = continuation_id
        return (
            f"Compressed session {previous_session} into continuation {continuation_id}.\n"
            f"Summary chars: {len(summary_text)}. Preserved tail messages: {len(tail)}."
        )

    def _child_allowed_tools(self) -> set[str] | None:
        """计算父 agent 和子 agent 工具白名单的交集。"""
        child_tools = allowed_tools_from_env("SIMPLE_HERMES_CHILD_ALLOWED_TOOLS")
        if child_tools is None:
            return None if self.allowed_tools is None else set(self.allowed_tools)
        if self.allowed_tools is None:
            return child_tools
        return set(self.allowed_tools) & child_tools

    def _delegate_task(self, task: str) -> str:
        """运行一个有步数限制的子 agent，并返回面向父 agent 的简短摘要。"""
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
        """使用独立 SQLite 连接并行运行多个子 agent 子任务。"""
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

    def _next_background_agent_id(self) -> str:
        """生成当前进程内唯一的后台 agent id。"""
        self._background_agent_counter += 1
        return f"agent-bg{self._background_agent_counter}"

    def start_background_agent(self, prompt: str) -> str:
        """在 daemon 线程中启动子 agent，并把最终结果写回父会话。"""
        prompt = prompt.strip()
        if not prompt:
            return "Usage: /background <prompt> or /background list|status <id>|wait <id>"
        task_id = self._next_background_agent_id()
        parent_session_id = self.session_id
        child_session_id = self.sessions.create_child_session(parent_session_id, title=f"background: {prompt[:60]}")
        task = BackgroundAgentTask(
            task_id=task_id,
            prompt=prompt,
            session_id=child_session_id,
            started_at=time.time(),
        )
        self._background_agent_tasks[task_id] = task

        def _run() -> None:
            child_sessions = SessionStore(path=self.sessions.path)
            child_memory = MemoryStore(memory_path=self.memory.memory_path, user_path=self.memory.user_path)
            child_agent = SimpleAgent(
                project_root=self.project_root,
                backend=self.backend,
                max_steps=self.max_steps,
                session_id=child_session_id,
                memory_store=child_memory,
                session_store=child_sessions,
                delegation_depth=self.delegation_depth + 1,
                max_delegation_depth=self.max_delegation_depth,
                child_step_budget=self.child_step_budget,
                allowed_tools=self.allowed_tools,
            )
            try:
                result = child_agent.run(prompt)
                text = result.final_response
                child_sessions.append(
                    "assistant",
                    f"[background-agent:{task_id}] completed\n{text}",
                    session_id=parent_session_id,
                    kind="background_agent_result",
                    tool_name="background_agent",
                )
                with task.lock:
                    task.status = "done"
                    task.result = text
                    task.completed_at = time.time()
            except Exception as exc:
                child_sessions.append(
                    "assistant",
                    f"[background-agent:{task_id}] failed\n{exc}",
                    session_id=parent_session_id,
                    kind="background_agent_result",
                    tool_name="background_agent",
                )
                with task.lock:
                    task.status = "failed"
                    task.error = str(exc)
                    task.completed_at = time.time()
            finally:
                child_sessions.conn.close()

        thread = threading.Thread(target=_run, daemon=True)
        task.thread = thread
        thread.start()
        return f"Started background agent {task_id} in child session {child_session_id}."

    def background_agents_text(self) -> str:
        """渲染当前 CLI 进程知道的所有后台 agent。"""
        if not self._background_agent_tasks:
            return "No background agents in this process."
        lines = ["Background agents:"]
        for task_id, task in sorted(self._background_agent_tasks.items()):
            with task.lock:
                elapsed = max(0.0, time.time() - task.started_at)
                lines.append(f"- {task_id}: {task.status}, {elapsed:.1f}s, session={task.session_id}, prompt={task.prompt}")
        return "\n".join(lines)

    def background_agent_status(self, task_id: str) -> str:
        """渲染单个后台 agent 的状态、结果或错误。"""
        task = self._background_agent_tasks.get(task_id.strip())
        if task is None:
            return f"Unknown background agent: {task_id}"
        with task.lock:
            elapsed = max(0.0, time.time() - task.started_at)
            text = f"{task.task_id}: {task.status}, {elapsed:.1f}s\nsession={task.session_id}\nprompt={task.prompt}"
            if task.result:
                text += "\n\nResult:\n" + task.result
            if task.error:
                text += "\n\nError:\n" + task.error
            return text

    def wait_background_agent(self, task_id: str, timeout: float | None = None) -> str:
        """等待一个后台线程，然后渲染它的当前状态。"""
        task = self._background_agent_tasks.get(task_id.strip())
        if task is None:
            return f"Unknown background agent: {task_id}"
        if task.thread is not None:
            task.thread.join(timeout=timeout)
        return self.background_agent_status(task_id)

    def _tool_result_message(self, tool_name: str, result: str) -> str:
        """工具结果写入会话历史时使用的规范格式。"""
        return f"[tool:{tool_name}] {result}"

    def _trace_decision_content(self, decision: PlannerDecision) -> str:
        """把 planner 决策压缩成适合 CLI progress 显示的文本。"""
        if decision.tool_call is None:
            return decision.text
        return f"{decision.text} | argument={decision.tool_call.argument}"

    def _inspection_call_key(self, tool_name: str, argument: str) -> tuple[str, str]:
        """规范化检查类工具调用，便于阻断重复的大范围读取。"""
        normalized = argument.strip()
        if tool_name == "project_overview":
            normalized = ""
        elif tool_name == "tree" and not normalized:
            normalized = "."
        return tool_name, normalized

    def _looks_like_failed_tool_result(self, result: str) -> bool:
        """识别已知工具失败文本，用于重复调用恢复逻辑。"""
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
        """要求后端从失败中恢复，而不是重复同一个失败工具调用。"""
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"The tool call {tool_name}({argument!r}) already failed with:\n{result}\n\n"
            "Do not repeat that exact tool call. Recover by using another general tool such as tree, glob, search, read_lines, terminal, "
            "or by using a suggested project-relative path. If no more tool is needed, return a text answer."
        )

    def _repeated_read_message(self, original_message: str, argument: str, result: str, observations: List[str]) -> str:
        """要求后端使用已读文件结果，避免读取同一文件形成循环。"""
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"You already read {argument!r}. Do not read the same file again in this run.\n"
            "Use the existing file content to decide the next step. If you need line-specific context, use read_lines. "
            "If the user requested a code change, proceed with patch_file or write_file before running tests.\n\n"
            f"Previous read result:\n{result}"
        )

    def _repeated_inspection_message(self, original_message: str, tool_name: str, argument: str, result: str, observations: List[str]) -> str:
        """重复检查后要求后端缩小范围或进入编辑步骤。"""
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"You already ran {tool_name}({argument!r}) successfully in this run. Do not restart project inspection with the same call.\n"
            "Use the existing tool result to decide the next concrete step. If the user requested a code change, proceed with "
            "patch_file or write_file when the target is known; otherwise use a different, narrower inspection tool.\n\n"
            f"Previous {tool_name} result:\n{result}"
        )

    def _premature_text_message(self, original_message: str, text: str, observations: List[str]) -> str:
        """planner 在必需编辑完成前试图结束时，构造恢复提示。"""
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
        """planner 在必需验证完成前试图结束时，构造恢复提示。"""
        return (
            f"Original user request:\n{original_message}\n\n"
            f"{self._format_run_state(observations)}\n\n"
            f"Your proposed text answer was:\n{text}\n\n"
            "The code edit appears to be done, but the user also requested testing or verification and no test command has succeeded yet. "
            "Run run_tests now, or use terminal to run a project-appropriate test command. Do not patch again unless a test failure shows that another edit is needed."
        )

    def _record_tool_result(self, tool_name: str, result: str) -> None:
        """带元数据持久化工具结果，支撑 recall 和 `/tool-results`。"""
        self.sessions.append(
            "assistant",
            self._tool_result_message(tool_name, result),
            session_id=self.session_id,
            kind="tool_result",
            tool_name=tool_name,
        )

    def _terminal_may_have_edited(self, argument: str, result: str) -> bool:
        """尽力判断成功的 terminal 命令是否修改了文件。"""
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
        """识别成功验证，并排除 “Ran 0 tests” 这类空跑。"""
        if "exit code: 0" not in result:
            return False
        if "Ran 0 tests" in result:
            return False
        if tool_name == "run_tests":
            return True
        lower_arg = argument.lower()
        return tool_name == "terminal" and ("pytest" in lower_arg or "unittest" in lower_arg)

    def _tool_result_is_diff(self, tool_name: str, result: str) -> bool:
        """跟踪成功编辑后是否已经检查过 diff。"""
        return tool_name == "diff" and not self._looks_like_failed_tool_result(result)

    def _record_assistant_text(self, text: str) -> None:
        """把最终 assistant 文本写入当前会话。"""
        self.sessions.append("assistant", text, session_id=self.session_id, kind="assistant_text")

    def run(self, message: str, on_step: Callable[[AgentTraceStep], None] | None = None) -> AgentResponse:
        """让一轮用户输入经过 planner/tool 主循环。

        这里最关键的状态变量是：
        - `requires_edit` 和 `requires_test`：来自 planner 决策或已有 active task；
        - `successful_edit`、`successful_test`、`inspected_diff`：来自工具结果；
        - `failed_calls` 和 `completed_inspections`：用于把 planner 从循环中拉出来。

        这个函数刻意不做自然语言意图分类，只在 planner/工具状态已经显式给出需求后
        执行进度约束。
        """
        trace: List[AgentTraceStep] = []

        def emit(item: AgentTraceStep) -> None:
            trace.append(item)
            if on_step is not None:
                on_step(item)

        original_message, active_task, _ = self._resolve_task_frame(message)
        self.sessions.append("user", message, session_id=self.session_id, kind="user_message")
        if original_message != message:
            emit(AgentTraceStep(step=0, kind="task_frame_resolved", content=original_message))

        if self.backend is None:
            decision = self.plan(original_message)
            emit(AgentTraceStep(step=1, kind=decision.kind, content=self._trace_decision_content(decision), tool_name=decision.tool_call.name if decision.tool_call else None))
            if decision.tool_call is not None:
                try:
                    result = self.tools.run(decision.tool_call.name, decision.tool_call.argument)
                except Exception as e:
                    error_text = f"Tool {decision.tool_call.name} failed: {e}"
                    self._record_assistant_text(error_text)
                    emit(AgentTraceStep(step=1, kind="error", content=error_text, tool_name=decision.tool_call.name))
                    self._maybe_compress_history()
                    return AgentResponse(final_response=error_text, tool_used=decision.tool_call.name, steps=1, trace=trace)
                self._record_tool_result(decision.tool_call.name, result)
                emit(AgentTraceStep(step=1, kind="tool_result", content=result, tool_name=decision.tool_call.name))
                self._maybe_compress_history()
                return AgentResponse(final_response=result, tool_used=decision.tool_call.name, steps=1, trace=trace)

            self._record_assistant_text(decision.text)
            self._maybe_compress_history()
            return AgentResponse(final_response=decision.text, steps=1, trace=trace)

        current_message = original_message
        last_tool_used: Optional[str] = None
        last_text = ""
        failed_calls: dict[tuple[str, str], str] = {}
        completed_inspections: dict[tuple[str, str], str] = {}
        run_observations: List[str] = []
        blocked_repeats = 0
        requires_edit = bool(
            active_task is not None
            and active_task.get("category") == "coding"
            and active_task.get("status") in {"in_progress", "awaiting_user"}
        )
        requires_test = False
        successful_edit = False
        inspected_diff = False
        successful_test = False
        for step in range(1, self.max_steps + 1):
            try:
                decision = self.plan(current_message, allow_explicit_tools=(step == 1))
            except Exception as e:
                if last_tool_used is not None and last_text:
                    fallback_text = f"{last_text}\n\nBackend planning failed after the tool result: {e}"
                    self._record_assistant_text(fallback_text)
                    emit(AgentTraceStep(step=step, kind="backend_error_fallback", content=fallback_text, tool_name=last_tool_used))
                    self._maybe_compress_history()
                    return AgentResponse(final_response=fallback_text, tool_used=last_tool_used, steps=step, trace=trace)
                error_text = f"Backend planning failed: {e}"
                fallback_text = self._backend_unavailable_fallback_text(e)
                self._record_assistant_text(fallback_text)
                emit(AgentTraceStep(step=step, kind="backend_error_fallback", content=error_text))
                self._maybe_compress_history()
                return AgentResponse(final_response=fallback_text, tool_used=last_tool_used, steps=step, trace=trace)
            requires_edit = requires_edit or decision.requires_edit
            requires_test = requires_test or decision.requires_test
            if requires_edit and active_task is None:
                active_task = self._start_active_task(message)
                emit(AgentTraceStep(step=step, kind="task_frame_started", content=f"{active_task.get('id')}: {active_task.get('goal')}"))
            emit(AgentTraceStep(step=step, kind=decision.kind, content=self._trace_decision_content(decision), tool_name=decision.tool_call.name if decision.tool_call else None))
            if decision.tool_call is None:
                if requires_edit and not successful_edit:
                    self._set_active_task_status("in_progress")
                    recovery_message = self._premature_text_message(original_message, decision.text, run_observations)
                    emit(AgentTraceStep(step=step, kind="premature_text_blocked", content=recovery_message))
                    current_message = recovery_message
                    continue
                if requires_edit and successful_edit and not inspected_diff:
                    emit(AgentTraceStep(step=step, kind="tool_call", content="Auto-inspect diff before final text.", tool_name="diff"))
                    result = self.tools.run("diff", "")
                    last_tool_used = "diff"
                    last_text = result
                    self._record_tool_result("diff", result)
                    emit(AgentTraceStep(step=step, kind="tool_result", content=result, tool_name="diff"))
                    run_observations.append(self._compact_observation("diff", "", result))
                    inspected_diff = self._tool_result_is_diff("diff", result)
                if requires_test and not successful_test:
                    recovery_message = self._premature_test_message(original_message, decision.text, run_observations)
                    emit(AgentTraceStep(step=step, kind="premature_text_blocked", content=recovery_message))
                    current_message = recovery_message
                    continue
                if successful_edit:
                    self._set_active_task_status("completed")
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
                emit(AgentTraceStep(step=step, kind="repeated_tool_blocked", content=recovery_message, tool_name=decision.tool_call.name))
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
                emit(AgentTraceStep(step=step, kind="repeated_tool_blocked", content=recovery_message, tool_name=decision.tool_call.name))
                current_message = recovery_message
                continue

            try:
                result = self.tools.run(decision.tool_call.name, decision.tool_call.argument)
            except Exception as e:
                error_text = f"Tool {decision.tool_call.name} failed: {e}"
                self._record_assistant_text(error_text)
                emit(AgentTraceStep(step=step, kind="tool_error", content=error_text, tool_name=decision.tool_call.name))
                self._maybe_compress_history()
                return AgentResponse(final_response=error_text, tool_used=decision.tool_call.name, steps=step, trace=trace)
            last_tool_used = decision.tool_call.name
            last_text = result
            self._record_tool_result(decision.tool_call.name, result)
            emit(AgentTraceStep(step=step, kind="tool_result", content=result, tool_name=decision.tool_call.name))
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
            if self._tool_result_is_diff(decision.tool_call.name, result):
                inspected_diff = True
            current_message = self._tool_followup_message(original_message, decision.tool_call.name, result, run_observations)

        timeout_text = (
            "Stopped after reaching the tiny agent step limit. "
            f"Last tool result was:\n{last_text}"
        )
        self._record_assistant_text(timeout_text)
        emit(AgentTraceStep(step=self.max_steps, kind="text", content=timeout_text, tool_name=last_tool_used))
        self._maybe_compress_history()
        return AgentResponse(final_response=timeout_text, tool_used=last_tool_used, steps=self.max_steps, trace=trace)
