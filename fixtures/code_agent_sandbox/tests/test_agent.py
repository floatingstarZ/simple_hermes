import os
import tempfile
import unittest
from pathlib import Path

from simple_hermes.agent import AgentTraceStep, PlannerDecision, SimpleAgent, ToolCall
from simple_hermes.backend import OpenAICompatibleBackend, _detect_hermes_repo_root
from simple_hermes.cli import _detect_project_root


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
            '```json\n{"kind": "tool_call", "tool": "read", "argument": "README.md", "text": "Use read"}\n```'
        )
        self.assertEqual(decision.kind, "tool_call")
        self.assertIsNotNone(decision.tool_call)
        self.assertEqual(decision.tool_call.name, "read")
        self.assertEqual(decision.tool_call.argument, "README.md")

    def test_parse_response_text_uses_first_object_when_trailing_text_would_be_extra_data(self) -> None:
        decision = OpenAICompatibleBackend._parse_response_text(
            '{"kind": "text", "text": "Done."}\nI will now stop.'
        )
        self.assertEqual(decision.kind, "text")
        self.assertEqual(decision.text, "Done.")


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
        self.assertIn("[assistant]<history>(tool_result)", call["history_text"])

    def test_backend_history_keeps_recent_tool_results_so_backend_can_use_multiple_reads(self) -> None:
        class HistoryAwareBackend:
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
                    return PlannerDecision(
                        kind="tool_call",
                        text="read the test first",
                        tool_call=ToolCall(name="read", argument="tests/test_text_tools.py"),
                    )
                if len(self.calls) == 2:
                    return PlannerDecision(
                        kind="tool_call",
                        text="now read the implementation",
                        tool_call=ToolCall(name="read", argument="text_tools.py"),
                    )
                if "Hello World" not in history_text or "return text.title()" not in history_text:
                    return PlannerDecision(
                        kind="text",
                        text="missing combined read context",
                        tool_call=None,
                    )
                return PlannerDecision(kind="text", text="I saw both files.", tool_call=None)

        backend = HistoryAwareBackend()
        tests_dir = self.project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_text_tools.py").write_text(
            "import unittest\n\n"
            "from text_tools import normalize_title\n\n\n"
            "class NormalizeTitleTests(unittest.TestCase):\n"
            "    def test_trim(self):\n"
            "        self.assertEqual(normalize_title('  hello world  '), 'Hello World')\n",
            encoding="utf-8",
        )
        (self.project_root / "text_tools.py").write_text(
            "def normalize_title(text: str) -> str:\n"
            "    return text.title()\n",
            encoding="utf-8",
        )
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state3_history_tool_results",
            backend=backend,
            max_steps=3,
        )

        result = agent.run("inspect both files and tell me what you learned")
        self.assertEqual(result.final_response, "I saw both files.")

    def test_detect_hermes_repo_root_from_env(self) -> None:
        os.environ["SIMPLE_HERMES_HERMES_ROOT"] = "/tmp/hermes-root"
        try:
            self.assertEqual(_detect_hermes_repo_root(), "/tmp/hermes-root")
        finally:
            os.environ.pop("SIMPLE_HERMES_HERMES_ROOT", None)

    def test_planner_detects_absolute_path_inside_natural_language_request(self) -> None:
        path = self.project_root / "README.md"
        decision = self.agent.plan(f"{path}这个code干啥的")
        self.assertEqual(decision.kind, "tool_call")
        self.assertIsNotNone(decision.tool_call)
        self.assertEqual(decision.tool_call.name, "read")
        self.assertEqual(decision.tool_call.argument, str(path))

    def test_rule_mode_can_read_and_explain_file_from_natural_language_path_request(self) -> None:
        code_path = self.project_root / "sample_module.py"
        code_path.write_text(
            "import os\n\n"
            "class Demo:\n"
            "    pass\n\n"
            "def run_task():\n"
            "    return os.getcwd()\n",
            encoding="utf-8",
        )
        result = self.agent.run(f"请看看{code_path}这个code干啥的")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("I read sample_module.py.", result.final_response)
        self.assertIn("Classes: Demo.", result.final_response)
        self.assertIn("Functions: run_task.", result.final_response)

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
        self.assertLessEqual(result.steps, 1)

    def test_backend_followup_message_with_absolute_like_path_does_not_bypass_backend(self) -> None:
        backend = FakeBackend([
            PlannerDecision(kind="tool_call", text="use read", tool_call=ToolCall(name="read", argument="/test_text_tools.py")),
            PlannerDecision(kind="text", text="I saw the missing-file result and stopped.", tool_call=None),
        ])
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state_backend_followup",
            backend=backend,
        )
        result = agent.run("please inspect a missing file")
        self.assertEqual(result.tool_used, "read")
        self.assertIn("I saw the missing-file result and stopped.", result.final_response)
        self.assertEqual(len(backend.calls), 2)
        self.assertIn("Tool read returned:\nFile not found: /test_text_tools.py", backend.calls[-1]["message"])

    def test_cli_detects_repo_root_when_cwd_is_outside_repo(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        (self.project_root / "pyproject.toml").write_text("[project]\nname = 'tmp'\n", encoding="utf-8")
        (self.project_root / "simple_hermes").mkdir(exist_ok=True)
        detected = _detect_project_root(cwd=outside, module_file=self.project_root / "simple_hermes" / "cli.py")
        self.assertEqual(detected, self.project_root.resolve())

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
        self.assertEqual(result.trace[1].kind, "tool_result")

    def test_backend_failure_becomes_agent_error_response(self) -> None:
        agent = self._make_agent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state6",
            backend=ErrorBackend(),
        )
        result = agent.run("hi there")
        self.assertIn("backend exploded", result.final_response)
        self.assertIn("error", result.trace[-1].kind)

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
