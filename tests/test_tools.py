import os
import tempfile
import unittest
from pathlib import Path

from simple_hermes.backend import PlannerDecision
from simple_hermes.memory import MemoryStore
from simple_hermes.session import SessionStore
from simple_hermes.tools import BuiltInTools


class FakeRecallBackend:
    def plan(self, **kwargs):
        return PlannerDecision(kind="text", text="MODEL RECALL SUMMARY", tool_call=None)


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "README.md").write_text("Simple Hermes tools test", encoding="utf-8")
        (self.project_root / ".env").write_text("SECRET=***", encoding="utf-8")
        (self.project_root / "tests").mkdir(exist_ok=True)
        (self.project_root / "tests" / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.memory = MemoryStore(
            memory_path=Path(self.temp_dir.name) / "memory.txt",
            user_path=Path(self.temp_dir.name) / "user.txt",
        )
        self.sessions = SessionStore(path=Path(self.temp_dir.name) / "sessions.db")
        self.tools = BuiltInTools(self.memory, self.sessions, self.project_root)

    def tearDown(self) -> None:
        self.sessions.conn.close()
        self.temp_dir.cleanup()

    def test_help_lists_tools(self) -> None:
        text = self.tools.registry.help_text()
        self.assertIn("remember", text)
        self.assertIn("terminal", text)
        self.assertIn("tree", text)
        self.assertIn("run_tests", text)
        self.assertIn("write_file", text)
        self.assertIn("patch_file", text)
        self.assertIn("read_lines", text)
        self.assertIn("glob", text)
        self.assertIn("diff", text)
        self.assertIn("path ::: exact target", text)

    def test_read_file(self) -> None:
        text = self.tools.read_file("README.md")
        self.assertIn("Simple Hermes tools test", text)

    def test_read_file_keeps_medium_sized_code_context(self) -> None:
        target = self.project_root / "medium.py"
        content = "x = 1\n" + ("# filler\n" * 500) + "def status_marker():\n    return True\n"
        target.write_text(content, encoding="utf-8")
        text = self.tools.read_file("medium.py")
        self.assertIn("def status_marker", text)
        self.assertNotIn("...[truncated]...", text)

    def test_read_lines_returns_numbered_range(self) -> None:
        target = self.project_root / "module.py"
        target.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
        text = self.tools.read_lines("module.py 2 3")
        self.assertIn("# module.py:2-3", text)
        self.assertIn("    2: beta", text)
        self.assertIn("    3: gamma", text)
        self.assertNotIn("alpha", text)

    def test_read_file_suggests_same_named_project_file_when_missing(self) -> None:
        module_dir = self.project_root / "simple_hermes"
        module_dir.mkdir()
        (module_dir / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
        text = self.tools.read_file("/cli.py")
        self.assertIn("File not found: /cli.py", text)
        self.assertIn("Did you mean:", text)
        self.assertIn("simple_hermes/cli.py", text)

    def test_read_file_accepts_absolute_in_project_path(self) -> None:
        abs_path = self.project_root / "README.md"
        text = self.tools.read_file(str(abs_path))
        self.assertIn("# README.md", text)
        self.assertIn("Simple Hermes tools test", text)

    def test_read_file_refuses_sensitive_files_when_disabled(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOW_SENSITIVE_READS"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.read_file(".env")
            self.assertIn("Refusing to read sensitive file", text)
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_SENSITIVE_READS", None)

    def test_read_file_allows_sensitive_files_by_default(self) -> None:
        text = self.tools.read_file(".env")
        self.assertIn("SECRET=***", text)

    def test_read_file_can_access_outside_project_by_default(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("outside content", encoding="utf-8")
        text = self.tools.read_file(str(outside))
        self.assertIn("outside content", text)

    def test_read_file_refuses_outside_project_when_disabled(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("outside content", encoding="utf-8")
        os.environ["SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_READS"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.read_file(str(outside))
            self.assertIn("Refusing to read outside the project root", text)
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_READS", None)

    def test_user_memory_tool(self) -> None:
        text = self.tools.remember_user("user likes concise replies")
        self.assertIn("Saved user memory", text)
        listed = self.tools.user_memories("")
        self.assertIn("user likes concise replies", listed)

    def test_restricted_tool_registry_blocks_disallowed_tools(self) -> None:
        limited_tools = BuiltInTools(self.memory, self.sessions, self.project_root, allowed_tools={"help", "summarize"})
        text = limited_tools.registry.run("read", "README.md")
        self.assertIn("not allowed", text)

    def test_tree_shows_project_structure(self) -> None:
        (self.project_root / "src").mkdir(exist_ok=True)
        (self.project_root / "src" / "module.py").write_text("print('ok')\n", encoding="utf-8")
        text = self.tools.tree(".")
        self.assertIn("Tree for .", text)
        self.assertIn("README.md", text)
        self.assertIn("src/", text)

    def test_glob_finds_project_files(self) -> None:
        (self.project_root / "src").mkdir(exist_ok=True)
        (self.project_root / "src" / "module.py").write_text("print('ok')\n", encoding="utf-8")
        text = self.tools.glob_files("**/*.py")
        self.assertIn("src/module.py", text)

    def test_diff_shows_git_diff_when_project_is_git_repo(self) -> None:
        subprocess_env = os.environ.copy()
        self.tools._run_subprocess(["git", "init"], timeout=10, label="git", env=subprocess_env)
        self.tools._run_subprocess(["git", "add", "README.md"], timeout=10, label="git", env=subprocess_env)
        self.tools._run_subprocess(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "baseline"],
            timeout=10,
            label="git",
            env=subprocess_env,
        )
        (self.project_root / "README.md").write_text("Simple Hermes tools test\nchanged\n", encoding="utf-8")
        text = self.tools.diff("README.md")
        self.assertIn("exit code: 0", text)
        self.assertIn("+changed", text)

    def test_run_tests_runs_default_unittest_discovery(self) -> None:
        text = self.tools.run_tests("")
        self.assertIn("exit code: 0", text)
        self.assertIn("test_ok", text)

    def test_run_tests_does_not_leak_agent_backend_env(self) -> None:
        (self.project_root / "tests" / "test_backend_env.py").write_text(
            "import unittest\n"
            "import os\n\n\n"
            "class BackendEnvTest(unittest.TestCase):\n"
            "    def test_backend_env_is_clean(self):\n"
            "        self.assertNotIn('SIMPLE_HERMES_BACKEND', os.environ)\n",
            encoding="utf-8",
        )
        os.environ["SIMPLE_HERMES_BACKEND"] = "openai"
        try:
            text = self.tools.run_tests("discover -s tests -v")
        finally:
            os.environ.pop("SIMPLE_HERMES_BACKEND", None)
        self.assertIn("exit code: 0", text)
        self.assertIn("test_backend_env_is_clean", text)

    def test_terminal_runs_in_project_root(self) -> None:
        text = self.tools.terminal("pwd")
        self.assertIn("exit code: 0", text)
        self.assertIn(str(self.project_root), text)

    def test_terminal_refuses_dangerous_command_when_disabled(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.terminal("rm -rf README.md")
            self.assertIn("Refusing dangerous terminal command", text)
            self.assertTrue((self.project_root / "README.md").exists())
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL", None)

    def test_terminal_allows_dangerous_command_by_default(self) -> None:
        path = self.project_root / "delete_me.txt"
        path.write_text("bye", encoding="utf-8")
        text = self.tools.terminal("rm delete_me.txt")
        self.assertIn("exit code: 0", text)
        self.assertFalse(path.exists())

    def test_terminal_truncates_long_output(self) -> None:
        text = self.tools.terminal("python3 -c 'print(\"x\" * 4000)'")
        self.assertIn("...[truncated]...", text)
        self.assertLess(len(text), 3200)

    def test_write_file_writes_project_relative_file(self) -> None:
        text = self.tools.write_file("notes/todo.txt first task")
        self.assertIn("Wrote file notes/todo.txt", text)
        self.assertEqual((self.project_root / "notes" / "todo.txt").read_text(encoding="utf-8"), "first task")

    def test_write_file_allows_absolute_path_by_default(self) -> None:
        outside = Path(self.temp_dir.name) / "external.txt"
        text = self.tools.write_file(f"{outside}\nhello")
        self.assertIn(str(outside), text)
        self.assertEqual(outside.read_text(encoding="utf-8"), "hello")

    def test_write_file_refuses_outside_project_root_when_disabled(self) -> None:
        outside = Path(self.temp_dir.name) / "external.txt"
        os.environ["SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_WRITES"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.write_file(f"{outside}\nhello")
            self.assertIn("Refusing to write outside the project root", text)
            self.assertFalse(outside.exists())
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_WRITES", None)

    def test_write_file_refuses_sensitive_path_when_disabled(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOW_SENSITIVE_WRITES"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.write_file(".env\nSECRET=updated")
            self.assertIn("Refusing to write sensitive file", text)
            self.assertEqual((self.project_root / ".env").read_text(encoding="utf-8"), "SECRET=***")
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_SENSITIVE_WRITES", None)

    def test_write_file_allows_sensitive_path_by_default(self) -> None:
        text = self.tools.write_file(".env\nSECRET=updated")
        self.assertIn("Wrote file .env", text)
        self.assertEqual((self.project_root / ".env").read_text(encoding="utf-8"), "SECRET=updated")

    def test_patch_file_replaces_target_string(self) -> None:
        target = self.project_root / "patch_me.txt"
        target.write_text("hello old world", encoding="utf-8")
        text = self.tools.patch_file("patch_me.txt\nold\n---\nnew")
        self.assertIn("Patched file patch_me.txt", text)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello new world")

    def test_patch_file_accepts_single_line_delimiter_format(self) -> None:
        target = self.project_root / "patch_me.txt"
        target.write_text("hello old world", encoding="utf-8")
        text = self.tools.patch_file("patch_me.txt ::: old ::: new")
        self.assertIn("Patched file patch_me.txt", text)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello new world")

    def test_patch_file_delimiter_format_allows_multiline_target(self) -> None:
        target = self.project_root / "patch_me.txt"
        target.write_text("alpha\nold\nbeta\n", encoding="utf-8")
        text = self.tools.patch_file("patch_me.txt ::: alpha\nold\nbeta\n ::: alpha\nnew\nbeta\n")
        self.assertIn("Patched file patch_me.txt", text)
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nnew\nbeta\n")

    def test_patch_file_reports_missing_target(self) -> None:
        target = self.project_root / "patch_me.txt"
        target.write_text("hello world", encoding="utf-8")
        text = self.tools.patch_file("patch_me.txt\nabsent\n---\nnew")
        self.assertIn("Target string not found", text)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

    def test_patch_file_replaces_unique_text(self) -> None:
        path = self.project_root / "sample.txt"
        path.write_text("alpha beta gamma", encoding="utf-8")
        text = self.tools.patch_file("sample.txt\nbeta\n---\nBETA")
        self.assertIn("Patched", text)
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha BETA gamma")

    def test_patch_file_reports_missing_old_string(self) -> None:
        path = self.project_root / "sample.txt"
        path.write_text("alpha beta gamma", encoding="utf-8")
        text = self.tools.patch_file("sample.txt\nmissing\n---\nBETA")
        self.assertIn("Target string not found", text)

    def test_approval_guard_blocks_delegate(self) -> None:
        os.environ["SIMPLE_HERMES_REQUIRE_APPROVAL"] = "delegate"
        try:
            guarded_tools = BuiltInTools(self.memory, self.sessions, self.project_root, delegate_runner=lambda task: task)
            text = guarded_tools.delegate("read README.md")
            self.assertIn("Approval required", text)
        finally:
            os.environ.pop("SIMPLE_HERMES_REQUIRE_APPROVAL", None)

    def test_recall_can_use_model_assisted_summarizer(self) -> None:
        self.sessions.append("user", "sqlite was chosen for continuity")
        self.sessions.append("assistant", "confirmed sqlite")
        tools = BuiltInTools(self.memory, self.sessions, self.project_root, backend=FakeRecallBackend())
        text = tools.recall("sqlite")
        self.assertIn("MODEL RECALL SUMMARY", text)

    def test_lineage_tool_shows_current_session_chain(self) -> None:
        child_id = self.sessions.create_child_session("default", title="child task")
        tools = BuiltInTools(self.memory, self.sessions, self.project_root, session_id=child_id)
        text = tools.lineage("")
        self.assertIn("Session lineage", text)
        self.assertIn("[root]", text)
        self.assertIn("[child]", text)

    def test_sessions_and_descendants_tools_show_session_views(self) -> None:
        child_id = self.sessions.create_child_session("default", title="child task")
        cont_id = self.sessions.create_continuation_session(child_id)
        tools = BuiltInTools(self.memory, self.sessions, self.project_root, session_id="default")
        sessions_text = tools.sessions_view("")
        descendants_text = tools.descendants("")
        self.assertIn("Recent sessions:", sessions_text)
        self.assertIn(child_id, sessions_text)
        self.assertIn("Descendant sessions for default", descendants_text)
        self.assertIn(cont_id, descendants_text)


if __name__ == "__main__":
    unittest.main()
