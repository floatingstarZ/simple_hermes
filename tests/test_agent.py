import os
import tempfile
import unittest
from pathlib import Path

from simple_hermes.agent import AgentTraceStep, PlannerDecision, SimpleAgent, ToolCall
from simple_hermes.backend import OpenAICompatibleBackend, _detect_hermes_repo_root
from simple_hermes.cli import _default_session_id, _detect_max_steps, _detect_project_root, _detect_session_id
from simple_hermes.agent.prompting import PromptContext, build_planner_prompt


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
        self.assertNotIn("tool_result", call["history_text"])

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

    def test_cli_detects_repo_root_when_cwd_is_outside_repo(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        (self.project_root / "pyproject.toml").write_text("[project]\nname = 'tmp'\n", encoding="utf-8")
        (self.project_root / "simple_hermes").mkdir(exist_ok=True)
        detected = _detect_project_root(cwd=outside, module_file=self.project_root / "simple_hermes" / "cli.py")
        self.assertEqual(detected, self.project_root.resolve())

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
            PlannerDecision(kind="tool_call", text="read file", tool_call=ToolCall(name="read", argument="notes.txt")),
            PlannerDecision(kind="text", text="I have not changed the file yet.", tool_call=None),
            PlannerDecision(kind="tool_call", text="patch file", tool_call=ToolCall(name="patch_file", argument="notes.txt ::: old ::: new")),
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
            PlannerDecision(kind="tool_call", text="write file", tool_call=ToolCall(name="write_file", argument="notes.txt ::: done")),
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
            PlannerDecision(kind="text", text="I can create that file.", tool_call=None),
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
            PlannerDecision(kind="tool_call", text="inspect project", tool_call=ToolCall(name="project_overview", argument="")),
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
