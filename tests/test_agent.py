import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from simple_hermes.agent import AgentTraceStep, PlannerDecision, SimpleAgent, ToolCall
from simple_hermes.backend import OpenAICompatibleBackend, _detect_hermes_repo_root
from simple_hermes.cli import (
    _background,
    _compress,
    _default_session_id,
    _detect_max_steps,
    _detect_project_root,
    _detect_session_id,
    _model,
    _new_session,
    _rename_session,
    _reset_session,
    _resume_session,
    _status,
    _tool_results,
    _usage,
)
from simple_hermes.agent.prompting import PLANNER_SYSTEM_MESSAGE, PromptContext, build_planner_prompt


class FakeBackend:
    def __init__(self, decisions) -> None:
        self.decisions = list(decisions)
        self.calls = []

    def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
        self.calls.append(
            {
                "message": message,
                "memory_block": memory_block,
                "history_text": history_text,
                "tools_text": tools_text,
            }
        )
        if not self.decisions:
            return PlannerDecision(kind="text", text="backend default", tool_call=None)
        return self.decisions.pop(0)


class ErrorBackend:
    def plan(self, **kwargs):
        raise RuntimeError("backend exploded")


class ErrorAfterFirstToolBackend:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return PlannerDecision(kind="tool_call", text="read file", tool_call=ToolCall(name="read", argument="README.md"))
        raise RuntimeError("backend exploded")


class CompactRetryBackend:
    def __init__(self) -> None:
        self.calls = []

    def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
        self.calls.append(
            {
                "message": message,
                "memory_block": memory_block,
                "history_text": history_text,
                "tools_text": tools_text,
            }
        )
        if len(self.calls) == 1:
            raise RuntimeError("peer closed connection without sending complete message body")
        return PlannerDecision(kind="text", text="compact retry succeeded", tool_call=None)


class BackendResponseParsingTests(unittest.TestCase):
    def test_parse_response_text_accepts_brief_explanation_around_json(self) -> None:
        decision = OpenAICompatibleBackend._parse_response_text(
            'Planner result:\n{"kind": "text", "text": "Hello from the backend."}\nDone.'
        )
        self.assertEqual(decision.kind, "text")
        self.assertEqual(decision.text, "Hello from the backend.")
        self.assertIsNone(decision.tool_call)

    def test_parse_response_text_accepts_json_fenced_block(self) -> None:
        decision = OpenAICompatibleBackend._parse_response_text(
            '```json\n{"kind": "tool_call", "tool": "read", "argument": "README.md", "text": "Use read", "requires_edit": true, "requires_test": true}\n```'
        )
        self.assertEqual(decision.kind, "tool_call")
        self.assertIsNotNone(decision.tool_call)
        self.assertEqual(decision.tool_call.name, "read")
        self.assertEqual(decision.tool_call.argument, "README.md")
        self.assertTrue(decision.requires_edit)
        self.assertTrue(decision.requires_test)

    def test_parse_response_text_uses_first_object_when_trailing_text_would_be_extra_data(self) -> None:
        decision = OpenAICompatibleBackend._parse_response_text(
            '{"kind": "text", "text": "Done."}\nI will now stop.'
        )
        self.assertEqual(decision.kind, "text")
        self.assertEqual(decision.text, "Done.")

    def test_backend_retry_helper_retries_transient_connection_errors(self) -> None:
        calls = {"count": 0}

        def flaky_operation():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("peer closed connection without sending complete message body")
            return "ok"

        os.environ["SIMPLE_HERMES_BACKEND_RETRIES"] = "1"
        try:
            self.assertEqual(OpenAICompatibleBackend._with_retries(flaky_operation), "ok")
        finally:
            os.environ.pop("SIMPLE_HERMES_BACKEND_RETRIES", None)
        self.assertEqual(calls["count"], 2)

    def test_backend_retry_helper_does_not_retry_parse_errors(self) -> None:
        calls = {"count": 0}

        def bad_operation():
            calls["count"] += 1
            raise ValueError("Planner response did not contain JSON")

        os.environ["SIMPLE_HERMES_BACKEND_RETRIES"] = "3"
        try:
            with self.assertRaises(ValueError):
                OpenAICompatibleBackend._with_retries(bad_operation)
        finally:
            os.environ.pop("SIMPLE_HERMES_BACKEND_RETRIES", None)
        self.assertEqual(calls["count"], 1)

    def test_planner_prompt_prefers_single_line_patch_file_format(self) -> None:
        prompt = build_planner_prompt(
            PromptContext(
                message="change a file",
                memory_block="",
                history_text="",
                tools_text="Available tools:\n- patch_file",
            )
        )
        self.assertIn("path ::: exact target text ::: replacement text", prompt)
        self.assertIn("read_lines", prompt)
        self.assertIn("project_overview", prompt)
        self.assertIn("diff", prompt)
        self.assertIn("active task state", prompt)
        self.assertIn("status-only text", prompt)
        self.assertIn("do not ask for clarification", prompt)
        self.assertIn("autonomous coding assistant", prompt)
        self.assertIn("requires_edit", prompt)
        self.assertIn("requires_test", prompt)
        self.assertIn("project-level context", prompt)
        self.assertIn("relevant SKILL.md", prompt)
        self.assertIn("Do not hardcode repository-specific tracking preferences", prompt)
        self.assertIn("project-local skills under the current repository's skills/", prompt)
        self.assertIn("placeholder/TODO/incomplete deliverables", prompt)
        self.assertIn("todo ledger", prompt)
        self.assertIn("one phase in_progress", prompt)
        self.assertIn("mark it completed", prompt)
        self.assertIn("raw artifacts and a target draft", prompt)
        self.assertIn("Do not use shell redirection", prompt)
        self.assertIn("documented --output arguments", prompt)

    def test_planner_system_message_pushes_autonomous_tool_use(self) -> None:
        self.assertIn("autonomous planning layer", PLANNER_SYSTEM_MESSAGE)
        self.assertIn("safe tool use", PLANNER_SYSTEM_MESSAGE)
        self.assertIn("strict JSON", PLANNER_SYSTEM_MESSAGE)


class AgentPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "README.md").write_text("Agent test file", encoding="utf-8")
        self._agents = []
        self.agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state",
        )

    def _make_agent(self, **kwargs) -> SimpleAgent:
        agent = SimpleAgent(**kwargs)
        self._agents.append(agent)
        return agent

    def tearDown(self) -> None:
        for agent in self._agents:
            agent.sessions.conn.close()
        self.temp_dir.cleanup()

    def test_agent_runs_tool_command(self) -> None:
        result = self.agent.run("read README.md")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("Agent test file", result.final_response)

    def test_agent_runs_code_tool_commands(self) -> None:
        write_result = self.agent.run("write_file notes.txt original")
        self.assertEqual(write_result.tool_used, "write_file")
        self.assertIn("Wrote file notes.txt", write_result.final_response)

        patch_result = self.agent.run("patch_file notes.txt\noriginal\n---\nupdated")
        self.assertEqual(patch_result.tool_used, "patch_file")
        self.assertIn("Patched file notes.txt", patch_result.final_response)
        self.assertEqual((self.project_root / "notes.txt").read_text(encoding="utf-8"), "updated")

        terminal_result = self.agent.run("terminal pwd")
        self.assertEqual(terminal_result.tool_used, "terminal")
        self.assertIn(str(self.project_root), terminal_result.final_response)

    def test_agent_runs_tree_and_run_tests_commands(self) -> None:
        tree_result = self.agent.run("tree .")
        self.assertEqual(tree_result.tool_used, "tree")
        self.assertIn("Tree for .", tree_result.final_response)

        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleAgentTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        run_tests_result = self.agent.run("run_tests")
        self.assertEqual(run_tests_result.tool_used, "run_tests")
        self.assertIn("exit code: 0", run_tests_result.final_response)

    def test_agent_supports_user_memory_command(self) -> None:
        result = self.agent.run("remember_user user likes diagrams")
        self.assertEqual(result.tool_used, "remember_user")
        self.assertIn("Saved user memory", result.final_response)

    def test_agent_fallback_message_for_unknown_intent(self) -> None:
        result = self.agent.run("write me a poem")
        self.assertIsNone(result.tool_used)
        self.assertIn("I do not have a real LLM backend", result.final_response)

    def test_planner_returns_explicit_tool_call(self) -> None:
        decision = self.agent.plan("read README.md")
        self.assertEqual(decision.kind, "tool_call")
        self.assertIsNotNone(decision.tool_call)
        self.assertEqual(decision.tool_call.name, "read")
        self.assertEqual(decision.tool_call.argument, "README.md")

    def test_planner_returns_text_when_no_tool_matches(self) -> None:
        decision = self.agent.plan("tell me a joke")
        self.assertEqual(decision.kind, "text")
        self.assertIsNone(decision.tool_call)
        self.assertIn("I do not have a real LLM backend", decision.text)

    def test_planner_delegates_project_inspection_intent_to_backend(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="backend decides", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_project_inspection",
            backend=backend,
        )

        decision = agent.plan("先看这个 todo 项目，告诉我 active_items 当前逻辑。只做诊断结论。")

        self.assertEqual(decision.kind, "text")
        self.assertIsNone(decision.tool_call)
        self.assertEqual(decision.text, "backend decides")
        self.assertEqual(len(backend.calls), 1)

    def test_agent_can_use_injected_backend_for_tool_choice(self) -> None:
        backend = FakeBackend([
            PlannerDecision(
                kind="tool_call",
                text="Use tool read",
                tool_call=ToolCall(name="read", argument="README.md"),
            ),
            PlannerDecision(kind="text", text="Done reading the file.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state2",
            backend=backend,
        )
        result = agent.run("please inspect the readme")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("Done reading the file.", result.final_response)
        self.assertEqual(len(backend.calls), 2)
        self.assertIn("Original user request:\nplease inspect the readme", backend.calls[-1]["message"])

    def test_backend_receives_memory_history_and_tools_context(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="backend answer", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state3",
            backend=backend,
        )
        agent.memory.add("project uses sqlite")
        agent.run("history")
        agent.run("what do you know?")
        call = backend.calls[-1]
        self.assertIn("project uses sqlite", call["memory_block"])
        self.assertIn("Available tools:", call["tools_text"])
        self.assertIn("[user](user_message) history", call["history_text"])
        self.assertNotIn("tool_result", call["history_text"])

    def test_backend_history_excludes_current_user_message_during_run(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="backend answer", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_current_history",
            backend=backend,
        )
        agent.sessions.append("user", "previous context", session_id=agent.session_id, kind="user_message")

        agent.run("current daily track request")

        self.assertEqual(agent.sessions.last_user_message(agent.session_id), "current daily track request")
        call = backend.calls[-1]
        self.assertIn("[user](user_message) previous context", call["history_text"])
        self.assertNotIn("current daily track request", call["history_text"])

    def test_backend_receives_project_instructions_and_skill_summaries_without_secrets(self) -> None:
        (self.project_root / "AGENTS.md").write_text(
            "@CLAUDE.md\nTreat daily track as the full repository workflow.\napi_key = sk-testsecret1234567890\n",
            encoding="utf-8",
        )
        (self.project_root / "CLAUDE.md").write_text(
            "Claude workflow detail from referenced instruction file.\n",
            encoding="utf-8",
        )
        (self.project_root / "MEMORY.md").write_text(
            "Track date defaults to previous Beijing day. token=ghp_secretvalue1234567890\n",
            encoding="utf-8",
        )
        skill_dir = self.project_root / "skills" / "huggingface-papers"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: huggingface-papers\n"
            "description: Fetch Hugging Face Daily Papers for a date.\n"
            "---\n"
            "# Hugging Face Daily Papers\n",
            encoding="utf-8",
        )
        backend = FakeBackend([PlannerDecision(kind="text", text="ready for daily track", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_project_context",
            backend=backend,
        )

        agent.run("开始 daily track")

        memory_block = backend.calls[-1]["memory_block"]
        self.assertIn("Project-level context:", memory_block)
        self.assertIn("AGENTS.md", memory_block)
        self.assertIn("Treat daily track as the full repository workflow", memory_block)
        self.assertIn("CLAUDE.md", memory_block)
        self.assertIn("Claude workflow detail from referenced instruction file", memory_block)
        self.assertIn("MEMORY.md", memory_block)
        self.assertIn("Track date defaults to previous Beijing day", memory_block)
        self.assertIn("huggingface-papers (skills/huggingface-papers/SKILL.md)", memory_block)
        self.assertIn("Fetch Hugging Face Daily Papers", memory_block)
        self.assertIn("[REDACTED]", memory_block)
        self.assertNotIn("sk-testsecret1234567890", memory_block)
        self.assertNotIn("ghp_secretvalue1234567890", memory_block)

    def test_backend_receives_workflow_todo_ledger(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="continue from ledger", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_workflow_todo",
            backend=backend,
        )
        agent.tools.run(
            "todo",
            'write [{"id":"collect","content":"Collect source artifacts","status":"completed"},'
            '{"id":"synth","content":"Synthesize final deliverable","status":"in_progress"},'
            '{"id":"verify","content":"Run verification","status":"pending"}]',
        )

        agent.run("继续这个复杂任务")

        memory_block = backend.calls[-1]["memory_block"]
        self.assertIn("Current workflow todo ledger:", memory_block)
        self.assertNotIn("Collect source artifacts", memory_block)
        self.assertIn("[in_progress] synth: Synthesize final deliverable", memory_block)
        self.assertIn("[pending] verify: Run verification", memory_block)
        self.assertIn("Use the todo tool to mark completed phases", memory_block)

    def test_backend_plan_retries_with_compact_context_after_transient_error(self) -> None:
        (self.project_root / "AGENTS.md").write_text("Project instruction.\n" + ("long context\n" * 2000), encoding="utf-8")
        (self.project_root / "CLAUDE.md").write_text("Claude instruction.\n" + ("long claude context\n" * 2000), encoding="utf-8")
        (self.project_root / "MEMORY.md").write_text("Memory instruction.\n" + ("long memory context\n" * 2000), encoding="utf-8")
        backend = CompactRetryBackend()
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_compact_retry",
            backend=backend,
        )

        result = agent.run("continue a workflow")

        self.assertIn("compact retry succeeded", result.final_response)
        self.assertEqual(len(backend.calls), 2)
        self.assertIn("compact retry context", backend.calls[1]["message"])
        self.assertLess(len(backend.calls[1]["memory_block"]), len(backend.calls[0]["memory_block"]))
        self.assertLessEqual(len(backend.calls[1]["history_text"]), len(backend.calls[0]["history_text"]))

    def test_detect_hermes_repo_root_from_env(self) -> None:
        os.environ["SIMPLE_HERMES_HERMES_ROOT"] = "/tmp/hermes-root"
        try:
            self.assertEqual(_detect_hermes_repo_root(), "/tmp/hermes-root")
        finally:
            os.environ.pop("SIMPLE_HERMES_HERMES_ROOT", None)

    def test_rule_mode_does_not_infer_tools_from_natural_language_path(self) -> None:
        path = self.project_root / "README.md"
        decision = self.agent.plan(f"{path}这个code干啥的")
        self.assertEqual(decision.kind, "text")
        self.assertIsNone(decision.tool_call)
        self.assertIn("I do not have a real LLM backend", decision.text)

    def test_rule_mode_requires_explicit_read_command_for_file_access(self) -> None:
        code_path = self.project_root / "sample_module.py"
        code_path.write_text(
            "import os\n\n"
            "class Demo:\n"
            "    pass\n\n"
            "def run_task():\n"
            "    return os.getcwd()\n",
            encoding="utf-8",
        )
        result = self.agent.run(f"read {code_path}")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("# sample_module.py", result.final_response)
        self.assertIn("class Demo", result.final_response)
        self.assertIn("def run_task", result.final_response)

    def test_backend_mode_explains_file_after_read_instead_of_timing_out(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use read", tool_call=ToolCall(name="read", argument="sample_backend.py")),
            PlannerDecision(kind="text", text="This file defines backend adapters.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_backend_explain",
            backend=backend,
        )
        target = self.project_root / "sample_backend.py"
        target.write_text(
            "from dataclasses import dataclass\n\n"
            "class OpenAICompatibleBackend:\n"
            "    pass\n\n"
            "def backend_from_env():\n"
            "    return None\n",
            encoding="utf-8",
        )
        result = agent.run(f"{target}这个code干啥的")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("backend adapters", result.final_response)
        self.assertLessEqual(result.steps, 2)

    def test_cli_detects_repo_root_when_cwd_is_inside_repo(self) -> None:
        inside = self.project_root / "subdir" / "nested"
        inside.mkdir(parents=True, exist_ok=True)
        (self.project_root / "pyproject.toml").write_text("[project]\nname = 'tmp'\n", encoding="utf-8")
        (self.project_root / "simple_hermes").mkdir(exist_ok=True)
        detected = _detect_project_root(cwd=inside, module_file=self.project_root / "simple_hermes" / "cli.py")
        self.assertEqual(detected, self.project_root.resolve())

    def test_cli_uses_cwd_when_cwd_is_outside_source_repo(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        (self.project_root / "pyproject.toml").write_text("[project]\nname = 'tmp'\n", encoding="utf-8")
        (self.project_root / "simple_hermes").mkdir(exist_ok=True)
        detected = _detect_project_root(cwd=outside, module_file=self.project_root / "simple_hermes" / "cli.py")
        self.assertEqual(detected, outside.resolve())

    def test_cli_uses_project_scoped_session_id_with_env_override(self) -> None:
        default_id = _default_session_id(self.project_root)
        self.assertTrue(default_id.startswith("project-"))
        self.assertEqual(default_id, _detect_session_id(self.project_root))
        os.environ["SIMPLE_HERMES_SESSION_ID"] = "manual-session"
        try:
            self.assertEqual(_detect_session_id(self.project_root), "manual-session")
        finally:
            os.environ.pop("SIMPLE_HERMES_SESSION_ID", None)

    def test_cli_reads_max_steps_from_env(self) -> None:
        self.assertEqual(_detect_max_steps(default=90), 90)
        os.environ["SIMPLE_HERMES_MAX_STEPS"] = "300"
        try:
            self.assertEqual(_detect_max_steps(default=90), 300)
        finally:
            os.environ.pop("SIMPLE_HERMES_MAX_STEPS", None)

    def test_backend_followup_after_failed_tests_requests_repair(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="write file", tool_call=ToolCall(name="write_file", argument="notes.txt ::: done")),
            PlannerDecision(kind="tool_call", text="run tests", tool_call=ToolCall(name="run_tests", argument="")),
            PlannerDecision(kind="tool_call", text="patch again", tool_call=ToolCall(name="patch_file", argument="notes.txt ::: done ::: fixed")),
            PlannerDecision(kind="tool_call", text="run tests again", tool_call=ToolCall(name="run_tests", argument="")),
            PlannerDecision(kind="text", text="Fixed and tests passed.", tool_call=None),
        ])
        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n"
            "    def test_note_fixed(self):\n"
            "        self.assertEqual(open('notes.txt', encoding='utf-8').read(), 'fixed')\n",
            encoding="utf-8",
        )
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_failed_test_followup",
            backend=backend,
        )
        result = agent.run("please modify notes.txt and run tests")
        self.assertIn("tests passed", result.final_response)
        self.assertIn("The last verification command failed", backend.calls[2]["message"])
        self.assertEqual((self.project_root / "notes.txt").read_text(encoding="utf-8"), "fixed")

    def test_multi_turn_followup_can_use_previous_diagnosis_without_forced_edit(self) -> None:
        core_file = self.project_root / "todo_app" / "core.py"
        core_file.parent.mkdir(exist_ok=True)
        core_file.write_text(
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class TodoItem:\n"
            "    title: str\n"
            "    completed: bool = False\n\n"
            "class TodoList:\n"
            "    def __init__(self):\n"
            "        self.items = []\n\n"
            "    def active_items(self):\n"
            "        return list(self.items)\n",
            encoding="utf-8",
        )

        class HistoryAwareBackend:
            def __init__(self) -> None:
                self.calls = []
                self.saw_prior_diagnosis = False

            def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
                self.calls.append({"message": message, "history_text": history_text})
                if len(self.calls) == 1:
                    return PlannerDecision(
                        kind="tool_call",
                        text="read active_items",
                        tool_call=ToolCall(name="read", argument="todo_app/core.py"),
                    )
                if len(self.calls) == 2:
                    return PlannerDecision(
                        kind="text",
                        text="诊断结论：当前实现直接返回 `list(self.items)`，应基于 `not item.completed` 过滤。我没有修改任何文件。",
                        tool_call=None,
                    )
                if len(self.calls) == 3:
                    self.saw_prior_diagnosis = "list(self.items)" in history_text and "not item.completed" in history_text
                    return PlannerDecision(
                        kind="tool_call",
                        text="patch active_items",
                        tool_call=ToolCall(
                            name="patch_file",
                            argument=(
                                "todo_app/core.py :::     def active_items(self):\n"
                                "        return list(self.items)\n"
                                " :::     def active_items(self):\n"
                                "        return [item for item in self.items if not item.completed]\n"
                            ),
                        ),
                        requires_edit=True,
                    )
                return PlannerDecision(kind="text", text="已根据上一轮诊断修复 active_items。", tool_call=None)

        backend = HistoryAwareBackend()
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_multi_turn",
            backend=backend,
        )

        first = agent.run("先看这个 todo 项目，告诉我 active_items 当前逻辑。只做诊断，不要修改文件。")
        self.assertIn("当前实现直接返回 `list(self.items)`", first.final_response)
        self.assertIn("not item.completed", first.final_response)
        self.assertIn("return list(self.items)", core_file.read_text(encoding="utf-8"))

        second = agent.run("那就修复 active_items。")

        self.assertIn("已根据上一轮诊断修复", second.final_response)
        self.assertTrue(backend.saw_prior_diagnosis)
        self.assertIn("if not item.completed", core_file.read_text(encoding="utf-8"))

    def test_multi_step_backend_loop_can_use_two_tools_then_answer(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use read", tool_call=ToolCall(name="read", argument="README.md")),
            PlannerDecision(kind="tool_call", text="use remember", tool_call=ToolCall(name="remember", argument="README mentioned")),
            PlannerDecision(kind="text", text="I inspected the README and saved the result.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state4",
            backend=backend,
        )
        result = agent.run("inspect then save")
        self.assertEqual(result.tool_used, "remember")
        self.assertIn("I inspected the README and saved the result.", result.final_response)
        self.assertEqual(len(backend.calls), 3)
        self.assertIn("README mentioned", agent.memory.list_text())

    def test_agent_response_contains_trace(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use read", tool_call=ToolCall(name="read", argument="README.md")),
            PlannerDecision(kind="text", text="Done.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state5",
            backend=backend,
        )
        result = agent.run("inspect readme")
        self.assertGreaterEqual(len(result.trace), 2)
        self.assertIsInstance(result.trace[0], AgentTraceStep)
        self.assertEqual(result.trace[0].kind, "tool_call")
        self.assertIn("argument=README.md", result.trace[0].content)
        self.assertEqual(result.trace[1].kind, "tool_result")

    def test_agent_run_streams_trace_steps(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use read", tool_call=ToolCall(name="read", argument="README.md")),
            PlannerDecision(kind="text", text="Done.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_stream_steps",
            backend=backend,
        )
        streamed: list[AgentTraceStep] = []

        result = agent.run("inspect readme", on_step=streamed.append)

        self.assertEqual([step.kind for step in streamed], [step.kind for step in result.trace])
        self.assertEqual([step.tool_name for step in streamed], [step.tool_name for step in result.trace])
        self.assertTrue(any(step.kind == "tool_result" for step in streamed))

    def test_backend_loop_blocks_repeated_failed_tool_call(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use missing read", tool_call=ToolCall(name="read", argument="/missing.py")),
            PlannerDecision(kind="tool_call", text="repeat missing read", tool_call=ToolCall(name="read", argument="/missing.py")),
            PlannerDecision(kind="text", text="I will recover instead of repeating.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_repeat_block",
            backend=backend,
        )
        result = agent.run("inspect and recover")
        self.assertIn("I will recover", result.final_response)
        self.assertTrue(any(step.kind == "repeated_tool_blocked" for step in result.trace))
        self.assertIn("Do not repeat that exact tool call", backend.calls[-1]["message"])

    def test_backend_loop_blocks_repeated_successful_read(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="read once", tool_call=ToolCall(name="read", argument="README.md")),
            PlannerDecision(kind="tool_call", text="read again", tool_call=ToolCall(name="read", argument="README.md")),
            PlannerDecision(kind="text", text="I will patch or answer now.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_repeat_read",
            backend=backend,
        )
        result = agent.run("inspect without looping")
        self.assertIn("I will patch", result.final_response)
        self.assertTrue(any(step.kind == "repeated_tool_blocked" for step in result.trace))
        self.assertIn("Do not read the same file again", backend.calls[-1]["message"])

    def test_backend_loop_blocks_repeated_successful_project_inspection(self) -> None:
        target = self.project_root / "notes.txt"
        target.write_text("old value", encoding="utf-8")
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="tree once", tool_call=ToolCall(name="tree", argument=".")),
            PlannerDecision(kind="tool_call", text="tree again", tool_call=ToolCall(name="tree", argument="")),
            PlannerDecision(kind="tool_call", text="patch file", tool_call=ToolCall(name="patch_file", argument="notes.txt ::: old ::: new")),
            PlannerDecision(kind="text", text="Changed notes.txt.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_repeat_inspection",
            backend=backend,
        )
        result = agent.run("please modify notes.txt")
        self.assertIn("Changed notes.txt", result.final_response)
        self.assertEqual(target.read_text(encoding="utf-8"), "new value")
        self.assertTrue(any(step.kind == "repeated_tool_blocked" for step in result.trace))
        self.assertTrue(any("Do not restart project inspection" in call["message"] for call in backend.calls))

    def test_backend_loop_blocks_excessive_inspection_before_required_edit(self) -> None:
        for index in range(20):
            (self.project_root / f"notes{index}.txt").write_text(f"value {index}", encoding="utf-8")
        decisions = [
            PlannerDecision(kind="tool_call", text=f"read {index}", tool_call=ToolCall(name="read", argument=f"notes{index}.txt"), requires_edit=True)
            for index in range(14)
        ]
        decisions.extend([
            PlannerDecision(kind="tool_call", text="write summary", tool_call=ToolCall(name="write_file", argument="summary.txt ::: done")),
            PlannerDecision(kind="text", text="Wrote summary.", tool_call=None),
        ])
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_inspection_budget",
            backend=backend,
        )

        result = agent.run("complete a repository workflow and write summary")

        self.assertIn("Wrote summary", result.final_response)
        self.assertTrue((self.project_root / "summary.txt").exists())
        self.assertTrue(any(step.kind == "inspection_budget_blocked" for step in result.trace))
        self.assertTrue(any("next step must be productive" in call["message"] for call in backend.calls))

    def test_backend_loop_counts_skills_and_read_only_terminal_as_inspection(self) -> None:
        skill_dir = self.project_root / "skills" / "daily-source"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: daily-source\n---\n# Daily Source\n", encoding="utf-8")
        for index in range(11):
            (self.project_root / f"context{index}.txt").write_text(f"value {index}", encoding="utf-8")
        decisions = [
            PlannerDecision(kind="tool_call", text="list skills", tool_call=ToolCall(name="skills", argument="list"), requires_edit=True),
            PlannerDecision(kind="tool_call", text="view skill", tool_call=ToolCall(name="skills", argument="view daily-source")),
            PlannerDecision(kind="tool_call", text="cat context", tool_call=ToolCall(name="terminal", argument="cat context0.txt")),
        ]
        decisions.extend(
            PlannerDecision(kind="tool_call", text=f"read {index}", tool_call=ToolCall(name="read", argument=f"context{index}.txt"))
            for index in range(1, 11)
        )
        decisions.extend([
            PlannerDecision(kind="tool_call", text="write summary", tool_call=ToolCall(name="write_file", argument="summary.txt ::: done")),
            PlannerDecision(kind="text", text="Wrote summary.", tool_call=None),
        ])
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_inspection_budget_skills_terminal",
            backend=backend,
        )

        result = agent.run("complete workflow and write summary")

        self.assertIn("Wrote summary", result.final_response)
        self.assertTrue((self.project_root / "summary.txt").exists())
        self.assertTrue(any(step.kind == "inspection_budget_blocked" for step in result.trace))

    def test_backend_loop_stops_after_repeated_inspection_budget_blocks(self) -> None:
        for index in range(20):
            (self.project_root / f"context{index}.txt").write_text(f"value {index}", encoding="utf-8")
        decisions = [
            PlannerDecision(kind="tool_call", text=f"read {index}", tool_call=ToolCall(name="read", argument=f"context{index}.txt"), requires_edit=True)
            for index in range(13)
        ]
        read_only_python = "python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('context0.txt').read_text())\nPY"
        decisions.extend(
            PlannerDecision(kind="tool_call", text="inspect again", tool_call=ToolCall(name="terminal", argument=read_only_python))
            for _ in range(6)
        )
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_inspection_budget_stop",
            backend=backend,
        )

        result = agent.run("complete workflow and write summary")

        self.assertIn("Stopped because the backend kept proposing inspection-only tool calls", result.final_response)
        self.assertGreaterEqual(sum(1 for step in result.trace if step.kind == "inspection_budget_blocked"), 4)

    def test_backend_loop_blocks_long_workflow_without_any_edit(self) -> None:
        decisions = [
            PlannerDecision(kind="tool_call", text="check background", tool_call=ToolCall(name="background", argument="list"), requires_edit=(index == 0))
            for index in range(45)
        ]
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_no_edit_budget",
            backend=backend,
        )

        result = agent.run("complete workflow and write summary")

        self.assertIn("Stopped because the backend kept proposing non-writing tool calls", result.final_response)
        self.assertGreaterEqual(sum(1 for step in result.trace if step.kind == "no_edit_budget_blocked"), 4)

    def test_backend_loop_blocks_final_text_after_placeholder_write(self) -> None:
        backend = FakeBackend([
            PlannerDecision(
                kind="tool_call",
                text="write placeholder",
                tool_call=ToolCall(name="write_file", argument="report.md ::: # Report\n\nTODO: fill later"),
                requires_edit=True,
            ),
            PlannerDecision(kind="text", text="Done.", tool_call=None),
            PlannerDecision(
                kind="tool_call",
                text="complete report",
                tool_call=ToolCall(name="write_file", argument="report.md ::: # Report\n\nComplete summary from collected evidence."),
            ),
            PlannerDecision(kind="text", text="Completed report.md.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_placeholder_write",
            backend=backend,
        )

        result = agent.run("complete the report and write report.md")

        self.assertIn("Completed report.md", result.final_response)
        self.assertEqual((self.project_root / "report.md").read_text(encoding="utf-8"), "# Report\n\nComplete summary from collected evidence.")
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))
        self.assertTrue(any("placeholder, TODO, or incomplete" in call["message"] for call in backend.calls))

    def test_placeholder_write_keeps_inspection_budget_active(self) -> None:
        for index in range(14):
            (self.project_root / f"raw{index}.json").write_text(f'{{"item": {index}}}', encoding="utf-8")
        decisions = [
            PlannerDecision(
                kind="tool_call",
                text="write placeholder",
                tool_call=ToolCall(name="write_file", argument="track.md ::: # Track\n\n状态：待补全正文"),
                requires_edit=True,
            )
        ]
        decisions.extend(
            PlannerDecision(kind="tool_call", text=f"inspect raw {index}", tool_call=ToolCall(name="read", argument=f"raw{index}.json"))
            for index in range(14)
        )
        decisions.extend([
            PlannerDecision(
                kind="tool_call",
                text="write final track",
                tool_call=ToolCall(name="write_file", argument="track.md ::: # Track\n\nFinal grounded summary."),
            ),
            PlannerDecision(kind="text", text="Completed track.md.", tool_call=None),
        ])
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_placeholder_budget",
            backend=backend,
        )

        result = agent.run("complete the tracking workflow and write track.md")

        self.assertIn("Completed track.md", result.final_response)
        self.assertEqual((self.project_root / "track.md").read_text(encoding="utf-8"), "# Track\n\nFinal grounded summary.")
        self.assertTrue(any(step.kind == "inspection_budget_blocked" for step in result.trace))

    def test_backend_loop_blocks_post_edit_inspection_loop(self) -> None:
        self.project_root.joinpath("report.md").write_text("# Report\n\nComplete summary.", encoding="utf-8")
        decisions = [
            PlannerDecision(
                kind="tool_call",
                text="write complete report",
                tool_call=ToolCall(name="write_file", argument="report.md ::: # Report\n\nComplete summary."),
                requires_edit=True,
            )
        ]
        decisions.extend(
            PlannerDecision(kind="tool_call", text=f"inspect after edit {index}", tool_call=ToolCall(name="read", argument="report.md"))
            for index in range(10)
        )
        decisions.extend([
            PlannerDecision(kind="tool_call", text="inspect diff", tool_call=ToolCall(name="diff", argument="")),
            PlannerDecision(kind="text", text="Report is complete.", tool_call=None),
        ])
        backend = FakeBackend(decisions)
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_post_edit_inspection",
            backend=backend,
        )

        result = agent.run("complete the report")

        self.assertIn("Report is complete", result.final_response)
        self.assertTrue(any(step.kind == "post_edit_inspection_blocked" for step in result.trace))
        self.assertTrue(any("after an edit" in call["message"] for call in backend.calls))

    def test_terminal_python_read_text_counts_as_inspection_but_subprocess_does_not(self) -> None:
        read_only = "python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('README.md').read_text())\nPY"
        productive = "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3', 'script.py'])\nPY"

        self.assertTrue(self.agent._terminal_is_read_only_inspection(read_only))
        self.assertFalse(self.agent._terminal_is_read_only_inspection(productive))
        self.assertTrue(self.agent._terminal_command_has_write_hint("python3 script.py --output result.json"))
        self.assertTrue(self.agent._mentions_incomplete_deliverable("TODO: fill later"))
        self.assertTrue(self.agent._mentions_incomplete_deliverable("状态：待补全正文"))
        self.assertFalse(self.agent._mentions_incomplete_deliverable("状态：初稿"))
        self.assertFalse(self.agent._mentions_incomplete_deliverable("todo_app/core.py"))

    def test_normalizes_compound_tool_names_from_backend(self) -> None:
        call = self.agent._normalize_tool_call(ToolCall(name="background wait", argument="bg1 20"))
        self.assertEqual(call.name, "background")
        self.assertEqual(call.argument, "wait bg1 20")
        todo_call = self.agent._normalize_tool_call(ToolCall(name="todo update", argument="read completed"))
        self.assertEqual(todo_call.name, "todo")
        self.assertEqual(todo_call.argument, "update read completed")

    def test_background_wait_loop_helpers_detect_running_tasks(self) -> None:
        self.assertEqual(self.agent._background_task_id_from_argument("wait bg3 120"), "bg3")
        self.assertEqual(self.agent._background_task_id_from_argument("status bg7"), "bg7")
        self.assertIsNone(self.agent._background_task_id_from_argument("tail bg3"))
        self.assertEqual(self.agent._background_wait_task_id_from_argument("wait bg3 120"), "bg3")
        self.assertIsNone(self.agent._background_wait_task_id_from_argument("status bg7"))
        self.assertEqual(
            self.agent._background_running_task_id_from_result("Background task bg3 is still running after 120.0s."),
            "bg3",
        )
        self.assertEqual(
            self.agent._background_running_task_id_from_result("bg3: running, 252.3s\n$ command"),
            "bg3",
        )
        self.assertEqual(
            self.agent._background_running_task_id_from_result("bg3 tail (running):\npartial output"),
            "bg3",
        )
        self.assertEqual(
            self.agent._background_finished_task_id_from_result("Background task bg3 completed with exit code 0.\n$ command"),
            "bg3",
        )
        self.assertEqual(
            self.agent._background_finished_task_id_from_result("Stopped background task bg3 with exit code -15."),
            "bg3",
        )
        self.assertTrue(
            self.agent._background_task_known_finished(
                "bg3",
                ["- background('stop bg3') -> Background task bg3 is already complete."],
            )
        )
        self.assertFalse(
            self.agent._background_task_known_finished(
                "bg3",
                ["- background('wait bg3 20') -> Background task bg3 is still running after 20.0s."],
            )
        )

    def test_backend_loop_blocks_wait_after_tail_status_cycle(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c 'import time; print(\"partial\", flush=True); time.sleep(5)'"
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="start slow job", tool_call=ToolCall(name="background", argument=f"start {command}")),
            PlannerDecision(kind="tool_call", text="wait slow job", tool_call=ToolCall(name="background", argument="wait bg1 0.1")),
            PlannerDecision(kind="tool_call", text="tail slow job", tool_call=ToolCall(name="background", argument="tail bg1")),
            PlannerDecision(kind="tool_call", text="status slow job", tool_call=ToolCall(name="background", argument="status bg1")),
            PlannerDecision(kind="tool_call", text="wait again", tool_call=ToolCall(name="background", argument="wait bg1 0.1")),
            PlannerDecision(kind="tool_call", text="stop slow job", tool_call=ToolCall(name="background", argument="stop bg1")),
            PlannerDecision(kind="text", text="Stopped the stuck background task and continued.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_background_wait_loop",
            backend=backend,
        )

        result = agent.run("run a workflow with a slow background source")

        self.assertIn("Stopped the stuck", result.final_response)
        self.assertTrue(any(step.kind == "background_wait_loop_blocked" for step in result.trace))
        self.assertTrue(any("Do not wait again" in call["message"] for call in backend.calls))

    def test_backend_followup_carries_run_state_across_tool_calls(self) -> None:
        target = self.project_root / "notes.txt"
        target.write_text("old value", encoding="utf-8")
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="overview", tool_call=ToolCall(name="project_overview", argument="")),
            PlannerDecision(kind="tool_call", text="read file", tool_call=ToolCall(name="read", argument="notes.txt")),
            PlannerDecision(kind="tool_call", text="patch file", tool_call=ToolCall(name="patch_file", argument="notes.txt ::: old ::: new")),
            PlannerDecision(kind="text", text="Changed notes.txt.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_run_summary",
            backend=backend,
        )
        result = agent.run("please modify notes.txt")
        self.assertIn("Changed notes.txt", result.final_response)
        last_message = backend.calls[-1]["message"]
        self.assertIn("Run state so far:", last_message)
        self.assertIn("- project_overview('') ->", last_message)
        self.assertIn("- read('notes.txt') ->", last_message)
        self.assertIn("- patch_file('notes.txt ::: old ::: new') -> Patched file notes.txt.", last_message)

    def test_backend_loop_blocks_premature_text_before_code_edit(self) -> None:
        target = self.project_root / "notes.txt"
        target.write_text("old value", encoding="utf-8")
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="read file", tool_call=ToolCall(name="read", argument="notes.txt"), requires_edit=True),
            PlannerDecision(kind="text", text="I have not changed the file yet.", tool_call=None),
            PlannerDecision(kind="tool_call", text="patch file", tool_call=ToolCall(name="patch_file", argument="notes.txt ::: old ::: new")),
            PlannerDecision(kind="tool_call", text="inspect diff", tool_call=ToolCall(name="diff", argument="notes.txt")),
            PlannerDecision(kind="text", text="Changed and ready to test.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_premature_text",
            backend=backend,
        )
        result = agent.run("please modify notes.txt")
        self.assertIn("Changed and ready", result.final_response)
        self.assertEqual(target.read_text(encoding="utf-8"), "new value")
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))

    def test_backend_loop_accepts_terminal_edit_but_still_requires_requested_tests(self) -> None:
        target = self.project_root / "notes.txt"
        target.write_text("old value", encoding="utf-8")
        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        backend = FakeBackend([
            PlannerDecision(
                kind="tool_call",
                text="edit with terminal",
                tool_call=ToolCall(
                    name="terminal",
                    argument="python3 -c 'from pathlib import Path; Path(\"notes.txt\").write_text(\"new value\", encoding=\"utf-8\")'",
                ),
                requires_edit=True,
                requires_test=True,
            ),
            PlannerDecision(kind="text", text="Edited but not tested.", tool_call=None),
            PlannerDecision(kind="tool_call", text="run tests", tool_call=ToolCall(name="run_tests", argument="")),
            PlannerDecision(kind="text", text="Edited and tests passed.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_terminal_edit_test",
            backend=backend,
        )
        result = agent.run("please modify notes.txt and run tests")
        self.assertIn("Edited and tests passed", result.final_response)
        self.assertEqual(target.read_text(encoding="utf-8"), "new value")
        self.assertTrue(any("Run run_tests now" in step.content for step in result.trace))

    def test_backend_loop_does_not_count_zero_tests_as_success(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="write file", tool_call=ToolCall(name="write_file", argument="notes.txt ::: done"), requires_edit=True, requires_test=True),
            PlannerDecision(
                kind="tool_call",
                text="bad test target",
                tool_call=ToolCall(
                    name="terminal",
                    argument="python3 -c 'print(\"exit code: 0\\\\nRan 0 tests in 0.000s\\\\nOK\")'",
                ),
            ),
            PlannerDecision(kind="text", text="Edited and tests passed.", tool_call=None),
            PlannerDecision(kind="tool_call", text="run real tests", tool_call=ToolCall(name="run_tests", argument="")),
            PlannerDecision(kind="text", text="Edited and real tests passed.", tool_call=None),
        ])
        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_zero_tests",
            backend=backend,
        )
        result = agent.run("please modify notes.txt and run tests")
        self.assertIn("real tests passed", result.final_response)
        self.assertTrue(any("Run run_tests now" in step.content for step in result.trace))

    def test_backend_loop_treats_create_fix_and_add_as_code_changes(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="text", text="I can create that file.", tool_call=None, requires_edit=True),
            PlannerDecision(kind="tool_call", text="write file", tool_call=ToolCall(name="write_file", argument="notes.txt ::: created")),
            PlannerDecision(kind="text", text="Created notes.txt.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_create_task",
            backend=backend,
        )
        result = agent.run("please create notes.txt")
        self.assertIn("Created notes.txt", result.final_response)
        self.assertEqual((self.project_root / "notes.txt").read_text(encoding="utf-8"), "created")
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))

    def test_backend_loop_handles_web_game_code_agent_scenario(self) -> None:
        (self.project_root / "pyproject.toml").write_text(
            "[project]\nname = 'coin-catcher-fixture'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (self.project_root / "index.html").write_text(
            "<!doctype html>\n"
            "<html><body><p id=\"score\">Score: 0</p><button id=\"coin\">Collect coin</button>"
            "<script src=\"src/game.js\"></script></body></html>\n",
            encoding="utf-8",
        )
        src_dir = self.project_root / "src"
        src_dir.mkdir(exist_ok=True)
        game_file = src_dir / "game.js"
        game_file.write_text(
            "let score = 0;\n\n"
            "function renderScore() {\n"
            "  const scoreEl = document.getElementById(\"score\");\n"
            "  if (scoreEl) {\n"
            "    scoreEl.textContent = `Score: ${score}`;\n"
            "  }\n"
            "}\n\n"
            "function collectCoin() {\n"
            "  score += 1;\n"
            "  renderScore();\n"
            "  return score;\n"
            "}\n",
            encoding="utf-8",
        )
        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_game_rules.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n\n"
            "class CoinCatcherRulesTest(unittest.TestCase):\n"
            "    def test_collect_coin_awards_ten_points(self):\n"
            "        source = Path('src/game.js').read_text(encoding='utf-8')\n"
            "        self.assertIn('score += 10;', source)\n"
            "        self.assertNotIn('score += 1;', source)\n",
            encoding="utf-8",
        )
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="inspect project", tool_call=ToolCall(name="project_overview", argument=""), requires_edit=True, requires_test=True),
            PlannerDecision(kind="tool_call", text="list code", tool_call=ToolCall(name="glob", argument="**/*")),
            PlannerDecision(kind="tool_call", text="read game", tool_call=ToolCall(name="read", argument="src/game.js")),
            PlannerDecision(kind="text", text="I found the scoring rule but have not changed it yet.", tool_call=None),
            PlannerDecision(kind="tool_call", text="patch score", tool_call=ToolCall(name="patch_file", argument="src/game.js ::: score += 1; ::: score += 10;")),
            PlannerDecision(kind="tool_call", text="inspect diff", tool_call=ToolCall(name="diff", argument="src/game.js")),
            PlannerDecision(kind="text", text="Edited and ready.", tool_call=None),
            PlannerDecision(kind="tool_call", text="run tests", tool_call=ToolCall(name="run_tests", argument="")),
            PlannerDecision(kind="text", text="Updated the web game scoring and tests passed.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_web_game",
            backend=backend,
        )
        result = agent.run("请完整完成这个小游戏任务：解析项目，把收集金币的得分从 1 改为 10，运行测试验证。")
        self.assertIn("tests passed", result.final_response)
        self.assertIn("score += 10;", game_file.read_text(encoding="utf-8"))
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))
        tool_names = [step.tool_name for step in result.trace if step.kind == "tool_result"]
        self.assertEqual(tool_names, ["project_overview", "glob", "read", "patch_file", "diff", "run_tests"])

    def test_coding_request_blocks_clarification_and_pushes_backend_to_write(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="text", text="请先说明你想要哪种版本。", tool_call=None, requires_edit=True),
            PlannerDecision(
                kind="tool_call",
                text="write snake game",
                tool_call=ToolCall(name="write_file", argument="snake.html ::: <!doctype html><title>Snake</title>"),
            ),
            PlannerDecision(kind="tool_call", text="inspect diff", tool_call=ToolCall(name="diff", argument="snake.html")),
            PlannerDecision(kind="text", text="已创建 HTML 贪吃蛇。", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_snake_first_turn",
            backend=backend,
        )

        result = agent.run("写个贪吃蛇小程序")

        self.assertIn("已创建", result.final_response)
        self.assertTrue((self.project_root / "snake.html").exists())
        self.assertTrue(any(step.kind == "task_frame_started" for step in result.trace))
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))
        active_task = agent._load_active_task()
        self.assertIsNotNone(active_task)
        self.assertEqual(active_task["status"], "completed")

    def test_short_variant_followup_continues_previous_coding_request(self) -> None:
        self.agent.sessions.append("user", "写个贪吃蛇小程序", session_id=self.agent.session_id, kind="user_message")
        self.agent.sessions.append("assistant", "请先说明你想要哪种版本。", session_id=self.agent.session_id, kind="assistant_text")
        self.agent.sessions.set_state(
            self.agent.session_id,
            "active_task",
            '{"id": "task-html", "category": "coding", "goal": "写个贪吃蛇小程序", "status": "in_progress"}',
        )
        backend = FakeBackend([
            PlannerDecision(kind="text", text="下面是源码；如果你要我写入项目，请继续确认。", tool_call=None),
            PlannerDecision(
                kind="tool_call",
                text="write html version",
                tool_call=ToolCall(name="write_file", argument="snake.html ::: <!doctype html><title>Snake HTML</title>"),
            ),
            PlannerDecision(kind="text", text="已写入 HTML 版本。", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_snake_variant",
            backend=backend,
            session_store=self.agent.sessions,
            memory_store=self.agent.memory,
            session_id=self.agent.session_id,
        )

        result = agent.run("HTML版本")

        self.assertIn("已写入", result.final_response)
        self.assertTrue((self.project_root / "snake.html").exists())
        self.assertIn("Active task goal: 写个贪吃蛇小程序", backend.calls[0]["message"])
        self.assertTrue(any(step.kind == "task_frame_resolved" for step in result.trace))
        self.assertTrue(any(step.kind == "premature_text_blocked" for step in result.trace))

    def test_permission_followup_continues_previous_coding_request(self) -> None:
        self.agent.sessions.append("user", "写个贪吃蛇小程序", session_id=self.agent.session_id, kind="user_message")
        self.agent.sessions.append("assistant", "如果要我写进当前项目，请继续确认。", session_id=self.agent.session_id, kind="assistant_text")
        self.agent.sessions.set_state(
            self.agent.session_id,
            "active_task",
            '{"id": "task-permission", "category": "coding", "goal": "写个贪吃蛇小程序", "status": "in_progress"}',
        )
        backend = FakeBackend([
            PlannerDecision(kind="text", text="我可以直接开始。", tool_call=None),
            PlannerDecision(
                kind="tool_call",
                text="write snake game after permission",
                tool_call=ToolCall(name="write_file", argument="snake.html ::: <!doctype html><title>Snake Allowed</title>"),
            ),
            PlannerDecision(kind="text", text="已根据许可写入。", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_snake_permission",
            backend=backend,
            session_store=self.agent.sessions,
            memory_store=self.agent.memory,
            session_id=self.agent.session_id,
        )

        result = agent.run("你做的任何操作都是允许的")

        self.assertIn("已根据许可写入", result.final_response)
        self.assertTrue((self.project_root / "snake.html").exists())
        self.assertIn("Current user follow-up: 你做的任何操作都是允许的", backend.calls[0]["message"])
        self.assertTrue(any(step.kind == "task_frame_resolved" for step in result.trace))

    def test_active_task_frame_continues_generic_followup_without_keyword_list(self) -> None:
        self.agent.sessions.set_state(
            self.agent.session_id,
            "active_task",
            '{"id": "task1", "category": "coding", "goal": "写个贪吃蛇小程序", "status": "in_progress"}',
        )
        backend = FakeBackend([
            PlannerDecision(kind="text", text="我需要更多信息。", tool_call=None),
            PlannerDecision(
                kind="tool_call",
                text="write from generic followup",
                tool_call=ToolCall(name="write_file", argument="snake.html ::: <!doctype html><title>Snake Generic</title>"),
            ),
            PlannerDecision(kind="text", text="已按你的补充继续写入。", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_snake_generic_followup",
            backend=backend,
            session_store=self.agent.sessions,
            memory_store=self.agent.memory,
            session_id=self.agent.session_id,
        )

        result = agent.run("照你认为合适的方式继续")

        self.assertIn("已按你的补充", result.final_response)
        self.assertTrue((self.project_root / "snake.html").exists())
        self.assertIn("Active task id: task1", backend.calls[0]["message"])
        self.assertTrue(any(step.kind == "task_frame_resolved" for step in result.trace))

    def test_backend_failure_falls_back_to_local_context(self) -> None:
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state6",
            backend=ErrorBackend(),
        )
        agent.memory.add_user("user prefers concise Chinese answers")

        result = agent.run("你记得我的历史消息吗？我的个人偏好")

        self.assertIn("Backend planning failed before any tool ran", result.final_response)
        self.assertIn("backend exploded", result.final_response)
        self.assertIn("Known user preferences", result.final_response)
        self.assertIn("user prefers concise Chinese answers", result.final_response)
        self.assertIn("你记得我的历史消息吗？我的个人偏好", result.final_response)
        self.assertEqual(result.trace[-1].kind, "backend_error_fallback")

    def test_backend_failure_after_tool_returns_tool_result(self) -> None:
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_backend_error_after_tool",
            backend=ErrorAfterFirstToolBackend(),
        )
        result = agent.run("inspect README.md")
        self.assertIn("Agent test file", result.final_response)
        self.assertIn("Backend planning failed after the tool result", result.final_response)
        self.assertEqual(result.trace[-1].kind, "backend_error_fallback")

    def test_delegate_creates_child_session_and_returns_summary(self) -> None:
        result = self.agent.run("delegate read README.md")
        self.assertEqual(result.tool_used, "delegate")
        self.assertIn("Child agent summary", result.final_response)
        children = self.agent.sessions.child_sessions(self.agent.session_id)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["parent_session_id"], self.agent.session_id)

    def test_delegate_respects_depth_limit(self) -> None:
        child = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state7",
            delegation_depth=1,
            max_delegation_depth=1,
        )
        result = child.run("delegate read README.md")
        self.assertIn("depth limit", result.final_response)

    def test_parallel_delegate_runs_multiple_child_tasks(self) -> None:
        result = self.agent.run("parallel_delegate read README.md ; summarize project")
        self.assertEqual(result.tool_used, "parallel_delegate")
        self.assertIn("Parallel child summaries", result.final_response)
        children = self.agent.sessions.child_sessions(self.agent.session_id)
        self.assertGreaterEqual(len(children), 2)

    def test_continuation_session_split_after_long_history(self) -> None:
        initial_session = self.agent.session_id
        for i in range(9):
            self.agent.run(f"remember fact {i}")
        self.assertNotEqual(self.agent.session_id, initial_session)
        children = self.agent.sessions.child_sessions(initial_session)
        self.assertTrue(any(child["id"] == self.agent.session_id for child in children))
        summary_rows = [
            row for row in self.agent.sessions.history(session_id=self.agent.session_id, limit=20)
            if row["role"] == "summary"
        ]
        self.assertTrue(summary_rows)
        self.assertIn("Conversation handoff summary:", summary_rows[0]["content"])
        self.assertIn("Goal:", summary_rows[0]["content"])

    def test_agent_can_resume_latest_continuation_session(self) -> None:
        base_dir = Path(self.temp_dir.name) / "state_resume"
        first = self._make_agent(
            project_root=self.project_root,
            base_dir=base_dir,
            session_id="project-demo",
        )
        for i in range(9):
            first.run(f"remember continuation fact {i}")
        latest = first.session_id

        resumed = self._make_agent(
            project_root=self.project_root,
            base_dir=base_dir,
            session_id="project-demo",
            resume_latest_continuation=True,
        )

        self.assertEqual(resumed.session_id, latest)

    def test_cli_resume_and_rename_session_helpers(self) -> None:
        first_child = self.agent.sessions.create_child_session(self.agent.session_id, title="first child")
        second_child = self.agent.sessions.create_child_session(self.agent.session_id, title="second child")

        rename_text = _rename_session(self.agent, "primary work")
        self.assertIn("Renamed current session", rename_text)
        self.assertEqual(self.agent.sessions.session_info(self.agent.session_id)["title"], "primary work")

        list_text = _resume_session(self.agent, "")
        self.assertIn("Usage: /resume", list_text)
        self.assertIn(second_child, list_text)

        resume_text = _resume_session(self.agent, second_child)
        self.assertIn("Resumed session", resume_text)
        self.assertEqual(self.agent.session_id, second_child)

        numbered_text = _resume_session(self.agent, "2")
        self.assertIn("Resumed session", numbered_text)
        self.assertEqual(self.agent.session_id, first_child)

        latest_text = _resume_session(self.agent, "latest")
        self.assertIn("Resumed session", latest_text)
        self.assertEqual(self.agent.session_id, second_child)

    def test_cli_session_management_helpers(self) -> None:
        self.agent.run("remember session fact")
        usage_text = _usage(self.agent)
        self.assertIn("Estimated tokens", usage_text)
        tool_results_text = _tool_results(self.agent, "")
        self.assertIn("remember", tool_results_text)

        new_text = _new_session(self.agent, "fresh work")
        self.assertIn("Started session", new_text)
        self.assertIn("fresh work", new_text)
        new_session_id = self.agent.session_id

        self.agent.run("remember new fact")
        reset_text = _reset_session(self.agent)
        self.assertIn("Cleared messages", reset_text)
        self.assertEqual(self.agent.sessions.message_count(new_session_id), 0)

    def test_cli_status_shows_runtime_project_and_task_state(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="ok", tool_call=None)])
        backend.model = "fake-model"
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_status",
            backend=backend,
        )
        agent.sessions.set_state(
            agent.session_id,
            "active_task",
            '{"id": "task-status", "category": "coding", "goal": "继续完善 status 面板", "status": "in_progress"}',
        )

        text = _status(agent, "real-llm")

        self.assertIn("Runtime:", text)
        self.assertIn("FakeBackend", text)
        self.assertIn("fake-model", text)
        self.assertIn(str(self.project_root), text)
        self.assertIn("Git: not a git repository", text)
        self.assertIn("Default project session:", text)
        self.assertIn("Active task: task-status [in_progress]", text)
        self.assertIn("Background agents: 0", text)
        self.assertIn("Compression threshold:", text)

    def test_cli_model_helper_switches_backend_model(self) -> None:
        backend = FakeBackend([PlannerDecision(kind="text", text="ok", tool_call=None)])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_model",
            backend=backend,
        )
        backend.model = "old-model"
        self.assertIn("old-model", _model(agent, ""))

        text = _model(agent, "new-model")

        self.assertIn("new-model", text)
        self.assertEqual(backend.model, "new-model")

    def test_manual_compress_creates_continuation(self) -> None:
        initial_session = self.agent.session_id
        self.agent.run("remember compress me")

        text = _compress(self.agent)

        self.assertIn("Compressed session", text)
        self.assertNotEqual(self.agent.session_id, initial_session)
        self.assertTrue(self.agent.sessions.history(session_id=self.agent.session_id, limit=10))

    def test_background_agent_runs_in_child_session(self) -> None:
        text = _background(self.agent, "read README.md")
        self.assertIn("Started background agent agent-bg1", text)

        waited = _background(self.agent, "wait agent-bg1 5")

        self.assertIn("done", waited)
        self.assertIn("Agent test file", waited)
        children = self.agent.sessions.child_sessions(self.agent.session_id)
        self.assertTrue(any("background:" in (child.get("title") or "") for child in children))

    def test_child_agent_tool_restriction_blocks_parallel_delegate(self) -> None:
        child = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state8",
            allowed_tools={"help", "summarize", "read", "search", "recall", "history", "memories", "user_memories", "remember", "remember_user"},
        )
        result = child.run("parallel_delegate read README.md ; summarize project")
        self.assertIn("not allowed", result.final_response)

    def test_agent_loads_allowed_tools_from_env(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOWED_TOOLS"] = "help,read"
        try:
            agent = self._make_agent(
                project_root=self.project_root,
                base_dir=Path(self.temp_dir.name) / "state9",
            )
            result = agent.run("terminal pwd")
            self.assertIn("not allowed", result.final_response)
            self.assertEqual(agent.run("read README.md").tool_used, "read")
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOWED_TOOLS", None)

    def test_child_agent_tools_default_to_parent_allowlist(self) -> None:
        parent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state10",
            allowed_tools={"help", "read", "delegate", "parallel_delegate"},
        )
        self.assertEqual(parent._child_allowed_tools(), {"help", "read", "delegate", "parallel_delegate"})

    def test_child_agent_tools_can_be_narrowed_by_env(self) -> None:
        os.environ["SIMPLE_HERMES_CHILD_ALLOWED_TOOLS"] = "help,read"
        try:
            parent = self._make_agent(
                project_root=self.project_root,
                base_dir=Path(self.temp_dir.name) / "state11",
                allowed_tools={"help", "read", "delegate", "parallel_delegate"},
            )
            self.assertEqual(parent._child_allowed_tools(), {"help", "read"})
        finally:
            os.environ.pop("SIMPLE_HERMES_CHILD_ALLOWED_TOOLS", None)


if __name__ == "__main__":
    unittest.main()
