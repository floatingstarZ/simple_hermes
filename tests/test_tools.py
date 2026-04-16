import os
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
        self.assertIn("project_overview", text)
        self.assertIn("diff", text)
        self.assertIn("checkpoint", text)
        self.assertIn("rollback", text)
        self.assertIn("skills", text)
        self.assertIn("cron", text)
        self.assertIn("mcp", text)
        self.assertIn("todo", text)
        self.assertIn("artifact", text)
        self.assertIn("experience", text)
        self.assertIn("validate_deliverable", text)
        self.assertIn("fetch_url", text)
        self.assertIn("credential_audit", text)
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

    def test_todo_write_update_and_session_state(self) -> None:
        text = self.tools.todo(
            'write [{"id":"read","content":"Read project instructions","status":"in_progress"},'
            '{"id":"write","content":"Write the deliverable","status":"pending"}]'
        )
        self.assertIn('"total": 2', text)
        self.assertIn('"in_progress": 1', text)

        self.tools.todo("update read completed")
        updated = self.tools.todo("update write in_progress")
        self.assertIn('"completed": 1', updated)
        self.assertIn('"in_progress": 1', updated)
        self.assertIn("Write the deliverable", updated)

        persisted = BuiltInTools(self.memory, self.sessions, self.project_root)
        listed = persisted.todo("list")
        self.assertIn("Read project instructions", listed)
        self.assertIn("Write the deliverable", listed)
        self.assertIn("in_progress", listed)

        rejected = self.tools.todo(
            'write [{"id":"a","content":"A","status":"in_progress"},'
            '{"id":"b","content":"B","status":"in_progress"}]'
        )
        self.assertIn("Only one todo item may be in_progress", rejected)

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

    def test_tree_skips_unreadable_directories(self) -> None:
        original_iterdir = Path.iterdir

        def fake_iterdir(path):
            if path.name == "blocked":
                raise PermissionError(1, "Operation not permitted", str(path))
            return original_iterdir(path)

        (self.project_root / "blocked").mkdir(exist_ok=True)
        (self.project_root / "visible").mkdir(exist_ok=True)
        with mock.patch.object(Path, "iterdir", fake_iterdir):
            text = self.tools.tree(". 3")

        self.assertIn("visible/", text)
        self.assertIn("[skipped: blocked: Operation not permitted]", text)

    def test_glob_finds_project_files(self) -> None:
        (self.project_root / "src").mkdir(exist_ok=True)
        (self.project_root / "src" / "module.py").write_text("print('ok')\n", encoding="utf-8")
        text = self.tools.glob_files("**/*.py")
        self.assertIn("src/module.py", text)

    def test_project_overview_reports_markers_and_test_command(self) -> None:
        (self.project_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        text = self.tools.project_overview("")
        self.assertIn("Python project", text)
        self.assertIn("Likely verification commands:", text)
        self.assertIn("-m unittest discover -s tests -v", text)

    def test_project_overview_reports_package_scripts(self) -> None:
        (self.project_root / "package.json").write_text(
            '{"scripts": {"test": "node --test", "lint": "eslint ."}}\n',
            encoding="utf-8",
        )
        text = self.tools.project_overview("")
        self.assertIn("Node/JavaScript project", text)
        self.assertIn("npm test", text)
        self.assertIn("Package scripts:", text)
        self.assertIn("- test: node --test", text)

    def test_project_overview_reports_instruction_files_and_local_skills(self) -> None:
        (self.project_root / "AGENTS.md").write_text("Follow project workflow.", encoding="utf-8")
        skill_dir = self.project_root / "skills" / "rss-reader"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: rss-reader\n"
            "description: Fetch RSS feeds for tracking.\n"
            "---\n"
            "# RSS Reader\n",
            encoding="utf-8",
        )

        text = self.tools.project_overview("")

        self.assertIn("Project instruction files:", text)
        self.assertIn("- AGENTS.md", text)
        self.assertIn("Project-local skills:", text)
        self.assertIn("- rss-reader (skills/rss-reader/SKILL.md): Fetch RSS feeds for tracking.", text)

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

    def test_diff_falls_back_to_in_memory_snapshot_outside_git(self) -> None:
        target = self.project_root / "script.js"
        target.write_text("score += 1;\n", encoding="utf-8")
        patch_text = self.tools.patch_file("script.js ::: score += 1; ::: score += 10;")
        self.assertIn("Patched file script.js", patch_text)
        text = self.tools.diff("script.js")
        self.assertIn("No git repository", text)
        self.assertIn("-score += 1;", text)
        self.assertIn("+score += 10;", text)

    def test_diff_uses_snapshot_when_project_is_inside_parent_git_repo(self) -> None:
        parent_file = Path(self.temp_dir.name) / "parent.txt"
        parent_file.write_text("parent\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.temp_dir.name, check=True, capture_output=True)
        subprocess.run(["git", "add", "parent.txt"], cwd=self.temp_dir.name, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "parent"],
            cwd=self.temp_dir.name,
            check=True,
            capture_output=True,
        )
        parent_file.write_text("parent changed\n", encoding="utf-8")
        target = self.project_root / "script.js"
        target.write_text("score += 1;\n", encoding="utf-8")
        self.assertIn("Patched file", self.tools.patch_file("script.js ::: score += 1; ::: score += 10;"))

        text = self.tools.diff("")

        self.assertIn("Project root is nested under a parent git repository", text)
        self.assertIn("+score += 10;", text)
        self.assertNotIn("parent changed", text)

    def test_run_tests_runs_default_unittest_discovery(self) -> None:
        text = self.tools.run_tests("")
        self.assertIn("exit code: 0", text)
        self.assertIn("test_ok", text)

    def test_run_tests_directory_argument_uses_unittest_discovery(self) -> None:
        text = self.tools.run_tests("tests")
        self.assertIn("exit code: 0", text)
        self.assertIn("-m unittest discover -s tests -v", text)
        self.assertIn("test_ok", text)
        self.assertNotIn("Ran 0 tests", text)

    def test_run_tests_uses_npm_for_package_json_project(self) -> None:
        fake_bin = Path(self.temp_dir.name) / "bin"
        fake_bin.mkdir()
        fake_npm = fake_bin / "npm"
        fake_npm.write_text("#!/bin/sh\necho fake npm \"$@\"\n", encoding="utf-8")
        fake_npm.chmod(0o755)
        (self.project_root / "package.json").write_text('{"scripts": {"test": "node --test"}}\n', encoding="utf-8")
        tests_dir = self.project_root / "tests"
        for child in tests_dir.iterdir():
            child.unlink()
        tests_dir.rmdir()
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{fake_bin}{os.pathsep}{old_path}"
        try:
            text = self.tools.run_tests("")
        finally:
            os.environ["PATH"] = old_path
        self.assertIn("$ npm test", text)
        self.assertIn("fake npm test", text)

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

    def test_terminal_allows_shell_redirection_by_default(self) -> None:
        text = self.tools.terminal("printf redirected > redirected.txt")

        self.assertIn("exit code: 0", text)
        self.assertEqual((self.project_root / "redirected.txt").read_text(encoding="utf-8"), "redirected")

    def test_terminal_refuses_shell_redirection_when_dangerous_terminal_disabled(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.terminal("printf redirected > redirected.txt")
            self.assertIn("Refusing terminal command with shell redirection", text)
            self.assertFalse((self.project_root / "redirected.txt").exists())
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL", None)

    def test_background_task_can_start_wait_and_record_completion(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c 'print(\"background done\")'"
        start = self.tools.background(f"start {command}")
        self.assertIn("Started background task bg1", start)

        waited = self.tools.background("wait bg1 5")

        self.assertIn("completed with exit code 0", waited)
        self.assertIn("background done", waited)
        listed = self.tools.background("list")
        self.assertIn("bg1: done exit=0", listed)
        history = self.sessions.history(limit=10)
        self.assertTrue(any(row.get("kind") == "background_result" for row in history))

    def test_background_wait_running_task_suggests_tail_stop_or_proceed(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c 'import time; print(\"partial\", flush=True); time.sleep(2)'"
        start = self.tools.background(f"start {command}")
        self.assertIn("Started background task bg1", start)

        waited = self.tools.background("wait bg1 0.1")
        tailed = self.tools.background("tail bg1")

        self.assertIn("still running", waited)
        self.assertIn("background tail", waited)
        self.assertIn("background stop", waited)
        self.assertIn("partial", waited)
        self.assertIn("bg1 tail (running):", tailed)
        self.assertIn("partial", tailed)
        self.tools.background("stop bg1")

    def test_background_task_refuses_dangerous_terminal_commands(self) -> None:
        os.environ["SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL"] = "0"
        try:
            guarded = BuiltInTools(self.memory, self.sessions, self.project_root)
            text = guarded.background("start rm -rf README.md")
            self.assertIn("Refusing dangerous background command", text)
            self.assertTrue((self.project_root / "README.md").exists())
        finally:
            os.environ.pop("SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL", None)

    def test_background_start_refuses_chained_shell_commands(self) -> None:
        first = f"{shlex.quote(sys.executable)} -c 'print(\"one\")'"
        second = f"{shlex.quote(sys.executable)} -c 'print(\"two\")'"

        text = self.tools.background(f"start {first} && {second}")

        self.assertIn("Refusing chained background command", text)
        self.assertIn("separate background tasks", text)
        self.assertIn("write a project-local script", text)

    def test_background_chain_detection_allows_language_internal_semicolon(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c 'print(\"one\"); print(\"two\")'"

        start = self.tools.background(f"start {command}")
        waited = self.tools.background("wait bg1 5")

        self.assertIn("Started background task bg1", start)
        self.assertIn("completed with exit code 0", waited)
        self.assertIn("one", waited)
        self.assertIn("two", waited)

    def test_background_start_refuses_shell_wrapper_chains(self) -> None:
        text = self.tools.background("start bash -lc 'echo one && echo two'")

        self.assertIn("Refusing chained background command", text)
        self.assertIn("&&", text)

    def test_write_file_writes_project_relative_file(self) -> None:
        text = self.tools.write_file("notes/todo.txt first task")
        self.assertIn("Wrote file notes/todo.txt", text)
        self.assertEqual((self.project_root / "notes" / "todo.txt").read_text(encoding="utf-8"), "first task")

    def test_checkpoint_and_rollback_restore_project_files(self) -> None:
        target = self.project_root / "restore_me.txt"
        target.write_text("before\n", encoding="utf-8")
        checkpoint = self.tools.checkpoint("create baseline")
        self.assertIn("Created checkpoint", checkpoint)
        target.write_text("after\n", encoding="utf-8")
        (self.project_root / "created_later.txt").write_text("new\n", encoding="utf-8")

        rollback = self.tools.rollback("latest")

        self.assertIn("Restored checkpoint", rollback)
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertFalse((self.project_root / "created_later.txt").exists())

    def test_write_file_creates_automatic_checkpoint(self) -> None:
        target = self.project_root / "auto.txt"
        target.write_text("old", encoding="utf-8")
        self.assertIn("Wrote file auto.txt", self.tools.write_file("auto.txt ::: new"))
        listed = self.tools.checkpoint("list")
        self.assertIn("before write_file auto.txt", listed)

    def test_checkpoint_restore_preserves_test_logs(self) -> None:
        logs = self.project_root / "test_results"
        logs.mkdir()
        (logs / "trace.txt").write_text("trace before", encoding="utf-8")
        checkpoint = self.tools.checkpoint("create baseline")
        self.assertIn("Created checkpoint", checkpoint)
        (logs / "trace.txt").write_text("trace after", encoding="utf-8")

        rollback = self.tools.rollback("latest")

        self.assertIn("Restored checkpoint", rollback)
        self.assertEqual((logs / "trace.txt").read_text(encoding="utf-8"), "trace after")

    def test_latest_checkpoint_uses_created_at_not_filename_order(self) -> None:
        target = self.project_root / "same_second.txt"
        target.write_text("one", encoding="utf-8")
        with mock.patch("simple_hermes.state.checkpoints.time.strftime", return_value="cp-20260414-160000"):
            with mock.patch("simple_hermes.state.checkpoints.time.time", side_effect=[1.0, 2.0]):
                first = self.tools.checkpoints.create(reason="first")
                target.write_text("two", encoding="utf-8")
                second = self.tools.checkpoints.create(reason="second")

        self.assertEqual(first.checkpoint_id, "cp-20260414-160000")
        self.assertEqual(second.checkpoint_id, "cp-20260414-160000-1")
        self.assertEqual(self.tools.checkpoints.latest_id(), second.checkpoint_id)
        self.assertEqual(self.tools.checkpoints.list_records(limit=2)[0].reason, "second")

    def test_write_file_delimiter_format_accepts_multiline_content(self) -> None:
        text = self.tools.write_file("snake.py ::: import curses\n\nprint('snake')\n")
        self.assertIn("Wrote file snake.py", text)
        self.assertEqual((self.project_root / "snake.py").read_text(encoding="utf-8"), "import curses\n\nprint('snake')\n")
        self.assertFalse((self.project_root / "snake.py ::: import curses").exists())

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

    def test_patch_file_reports_nearest_candidate_when_target_missing(self) -> None:
        target = self.project_root / "patch_me.py"
        target.write_text("def active_items(self):\n    return list(self.items)\n", encoding="utf-8")
        text = self.tools.patch_file("patch_me.py ::: def active_item(self):\n    return list(self.items) ::: replacement")
        self.assertIn("Target string not found", text)
        self.assertIn("Nearest candidate snippets:", text)
        self.assertIn("def active_items", text)

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

    def test_recall_all_searches_across_sessions(self) -> None:
        child_id = self.sessions.create_child_session("default", title="child")
        self.sessions.append("user", "cross session marker", session_id=child_id)

        text = self.tools.recall_all("marker")

        self.assertIn("Cross-session recall summary", text)
        self.assertIn(child_id, text)
        self.assertIn("cross session marker", text)

    def test_skills_create_view_and_use_local_markdown(self) -> None:
        self.tools.skills_dir = Path(self.temp_dir.name) / "skills"

        created = self.tools.skills("create code-review ::: prefer concise findings")
        listed = self.tools.skills("list")
        viewed = self.tools.skills("view code-review")
        used = self.tools.skills("use code-review")

        self.assertIn("Created skill code-review", created)
        self.assertIn("code-review", listed)
        self.assertIn("prefer concise findings", viewed)
        self.assertIn("Loaded skill into session context", used)
        history = self.sessions.history(session_id="default", limit=10)
        self.assertTrue(any(row.get("kind") == "skill_context" for row in history))

    def test_experience_record_list_view_and_summarize(self) -> None:
        recorded = self.tools.experience(
            "record status=failed failure_type=test_failure goal='fix tests' ::: AssertionError on discount"
        )
        card_id = recorded.split()[2].rstrip(":")

        listed = self.tools.experience("list")
        viewed = self.tools.experience(f"view {card_id}")
        summary = self.tools.experience("summarize")

        self.assertIn("Recorded experience", recorded)
        self.assertIn(card_id, listed)
        self.assertIn("AssertionError on discount", viewed)
        self.assertIn('"test_failure": 1', summary)
        self.assertTrue((self.project_root / ".simple_hermes" / "evolution").exists())

    def test_skills_candidate_propose_view_and_promote(self) -> None:
        self.tools.skills_dir = Path(self.temp_dir.name) / "skills"
        self.tools.skill_candidates_dir = Path(self.temp_dir.name) / "skill_candidates"

        proposed = self.tools.skills(
            "propose code-review from=exp-1 reason='captured failure' ::: "
            "# code-review\n\nCheck tests first and cite failing output."
        )
        candidate_id = proposed.split()[3].rstrip(":")
        listed = self.tools.skills("candidates")
        viewed = self.tools.skills(f"view-candidate {candidate_id}")
        promoted = self.tools.skills(f"promote {candidate_id}")
        stable = self.tools.skills("view code-review")

        self.assertIn("Created skill candidate", proposed)
        self.assertIn(candidate_id, listed)
        self.assertIn("captured failure", viewed)
        self.assertIn("Promoted skill candidate", promoted)
        self.assertIn("Check tests first", stable)

    def test_skills_promote_refuses_placeholder_candidate(self) -> None:
        self.tools.skills_dir = Path(self.temp_dir.name) / "skills"
        self.tools.skill_candidates_dir = Path(self.temp_dir.name) / "skill_candidates"

        proposed = self.tools.skills("propose draft-skill ::: # draft-skill\n\nTODO: fill details later.")
        candidate_id = proposed.split()[3].rstrip(":")
        promoted = self.tools.skills(f"promote {candidate_id}")

        self.assertIn("Refusing to promote candidate", promoted)
        self.assertFalse((self.tools.skills_dir / "draft-skill.md").exists())

    def test_skills_list_view_and_use_project_local_skill(self) -> None:
        skill_dir = self.project_root / "skills" / "huggingface-papers"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: huggingface-papers\n"
            "description: Fetch HuggingFace Daily Papers.\n"
            "---\n"
            "# HuggingFace Papers\n",
            encoding="utf-8",
        )

        listed = self.tools.skills("list")
        viewed = self.tools.skills("view huggingface-papers")
        used = self.tools.skills("use huggingface-papers")

        self.assertIn("Project-local skills", listed)
        self.assertIn("- huggingface-papers", listed)
        self.assertIn("skill:huggingface-papers (project)", viewed)
        self.assertIn("Fetch HuggingFace Daily Papers", viewed)
        self.assertIn("Loaded skill into session context (project)", used)
        history = self.sessions.history(session_id="default", limit=10)
        self.assertTrue(any("source=project" in row.get("content", "") for row in history))

    def test_cron_add_run_due_and_delete(self) -> None:
        self.tools.cron_path = Path(self.temp_dir.name) / "cron.json"

        added = self.tools.cron("add smoke every 1 ::: tool:remember scheduled fact")
        jobs = self.tools._load_cron_jobs()
        jobs[0]["next_run_at"] = 0
        self.tools._save_cron_jobs(jobs)
        due = self.tools.cron("run-due")
        listed = self.tools.cron("list")
        deleted = self.tools.cron(f"delete {jobs[0]['id']}")

        self.assertIn("Added cron job", added)
        self.assertIn("Ran due cron jobs", due)
        self.assertIn("Saved memory", due)
        self.assertIn("smoke", listed)
        self.assertIn("Deleted cron job", deleted)

    def test_mcp_exports_sessions_and_redacts_secret_values(self) -> None:
        self.sessions.append("user", "api_key = TEST_API_KEY_VALUE")

        resources = self.tools.mcp("resources")
        session_json = self.tools.mcp("session default")
        search_json = self.tools.mcp("search api_key")

        self.assertIn("simple-hermes://sessions", resources)
        self.assertIn("[REDACTED]", session_json)
        self.assertNotIn("TEST_API_KEY_VALUE", session_json)
        self.assertIn("[REDACTED]", search_json)

    def test_artifact_scan_records_workflow_outputs(self) -> None:
        raw_dir = self.project_root / "raw"
        output_dir = self.project_root / "outputs"
        raw_dir.mkdir()
        output_dir.mkdir()
        (raw_dir / "papers.json").write_text(
            json.dumps([{"title": "A"}, {"title": "B"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (output_dir / "track.md").write_text("# Daily Track\n\ncontent\n", encoding="utf-8")

        scanned = self.tools.artifact("scan .")
        listed = self.tools.artifact("list")

        self.assertIn("Scanned artifacts under .", scanned)
        self.assertIn("raw/papers.json", scanned)
        self.assertIn("items=2", scanned)
        self.assertIn("outputs/track.md", listed)
        self.assertIn("Artifacts: 2", listed)
        self.assertTrue((self.project_root / ".simple_hermes" / "artifacts").exists())

    def test_terminal_success_output_is_saved_as_artifact(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c \"print('artifact evidence ' * 30)\""
        result = self.tools.terminal(command)
        listed = self.tools.artifact("list")

        self.assertIn("exit code: 0", result)
        self.assertIn("artifact:", result)
        self.assertIn("tool_outputs", listed)
        self.assertIn("source_tool", self.sessions.get_state("default", "artifact_manifest") or "")

    def test_validate_failure_lists_manifest_tool_outputs_as_rebuild_candidates(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c \"print('[{{\\\"title\\\": \\\"A\\\"}}, {{\\\"title\\\": \\\"B\\\"}}]' * 20)\""
        terminal_result = self.tools.terminal(command)
        (self.project_root / "papers.json").write_text(
            json.dumps([{"title": "Only one"}], ensure_ascii=False),
            encoding="utf-8",
        )

        failed = self.tools.validate_deliverable("papers.json min_items=2 required_fields=title")

        self.assertIn("artifact:", terminal_result)
        self.assertIn("DELIVERABLE_VALIDATION failed", failed)
        self.assertIn("Candidate artifacts:", failed)
        self.assertIn(".simple_hermes/artifacts", failed)
        self.assertIn("tool_outputs", failed)

    def test_todo_accepts_redundant_tool_prefix_in_argument(self) -> None:
        self.tools.todo('write [{"id":"collect","content":"Collect data","status":"in_progress"}]')

        result = self.tools.todo("todo update collect completed")

        self.assertIn('"status": "completed"', result)

    def test_validate_json_deliverable_reports_schema_gaps_and_raw_candidates(self) -> None:
        raw_dir = self.project_root / "raw"
        raw_dir.mkdir()
        (raw_dir / "collected_papers.json").write_text(
            json.dumps(
                [
                    {"title": "Paper A", "source": "arxiv", "reason": "relevant", "confidence": "high"},
                    {"title": "Paper B", "source": "github", "reason": "useful", "confidence": "medium"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.project_root / "papers.json").write_text(
            json.dumps([{"title": "Only one", "source": "arxiv"}], ensure_ascii=False),
            encoding="utf-8",
        )

        failed = self.tools.validate_deliverable(
            "papers.json min_items=2 required_fields=title,source,reason,confidence"
        )

        self.assertIn("DELIVERABLE_VALIDATION failed", failed)
        self.assertIn("item count 1 < min_items 2", failed)
        self.assertIn("field 'reason' missing/empty", failed)
        self.assertIn("Recommended recovery: rebuild this deliverable from raw/artifact files", failed)
        self.assertIn("raw/collected_papers.json", failed)

        (self.project_root / "papers.json").write_text(
            json.dumps(
                [
                    {"title": "Paper A", "source": "arxiv", "reason": "relevant", "confidence": "high"},
                    {"title": "Paper B", "source": "github", "reason": "useful", "confidence": "medium"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ok = self.tools.validate_deliverable(
            "papers.json min_items=2 required_fields=title,source,reason,confidence"
        )
        self.assertIn("DELIVERABLE_VALIDATION ok", ok)

    def test_validate_markdown_deliverable_flags_placeholders_and_empty_cells(self) -> None:
        (self.project_root / "track.md").write_text(
            "# Daily Track\n\n"
            "## GitHub\n\n"
            "| name | url | note |\n"
            "| --- | --- | --- |\n"
            "| project | N/A | 待补 |\n",
            encoding="utf-8",
        )

        text = self.tools.validate_deliverable(
            "track.md min_bytes=100 required_headings=Daily,GitHub"
        )

        self.assertIn("DELIVERABLE_VALIDATION failed", text)
        self.assertIn("placeholder/incomplete markers found", text)
        self.assertIn("possible empty/N/A markdown table cells", text)
        self.assertIn("placeholder_deliverable", self.tools.experience("summarize"))

    def test_run_tests_failure_records_experience(self) -> None:
        (self.project_root / "tests" / "test_sample.py").write_text(
            "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_fail(self):\n        self.assertTrue(False)\n",
            encoding="utf-8",
        )

        text = self.tools.run_tests("")
        summary = self.tools.experience("summarize")

        self.assertIn("exit code: 1", text)
        self.assertIn('"test_failure": 1', summary)

    def test_validate_markdown_deliverable_flags_tracking_placeholders(self) -> None:
        (self.project_root / "track.md").write_text(
            "# Daily Track\n\n"
            "## ArXiv\n\n"
            "| paper | arxiv |\n"
            "| --- | --- |\n"
            "| Candidate | [2604.xxxxx](https://arxiv.org/) |\n\n"
            "GitHub stars 更新待刷新，papers.json 需在后续步骤补全。\n",
            encoding="utf-8",
        )

        text = self.tools.validate_deliverable("track.md min_bytes=80 required_headings=Daily,ArXiv")

        self.assertIn("DELIVERABLE_VALIDATION failed", text)
        self.assertIn("placeholder/incomplete markers found", text)
        self.assertIn("待刷新", text)
        self.assertIn("补全", text)
        self.assertIn("placeholder/incomplete patterns found", text)
        self.assertIn("placeholder arXiv id", text)
        self.assertIn("empty arXiv link", text)

    def test_validate_json_deliverable_flags_placeholder_strings(self) -> None:
        (self.project_root / "papers.json").write_text(
            json.dumps(
                [
                    {
                        "title": "Candidate",
                        "source": "arxiv",
                        "reason": "待刷新",
                        "confidence": "medium",
                        "arxiv_id": "2604.xxxxx",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        text = self.tools.validate_deliverable(
            "papers.json min_items=1 required_fields=title,source,reason,confidence,arxiv_id"
        )

        self.assertIn("DELIVERABLE_VALIDATION failed", text)
        self.assertIn("placeholder/incomplete markers found in JSON strings", text)
        self.assertIn("placeholder/incomplete patterns found in JSON strings", text)
        self.assertIn("placeholder arXiv id", text)

    def test_dependency_scan_and_credential_audit_do_not_read_secret_values(self) -> None:
        (self.project_root / "package.json").write_text(
            '{"dependencies": {"left-pad": "1.3.0"}, "devDependencies": {"eslint": "9.0.0"}}',
            encoding="utf-8",
        )
        (self.project_root / ".env.local").write_text("API_KEY=TEST_API_KEY_VALUE", encoding="utf-8")

        deps = self.tools.dependency_scan("")
        audit = self.tools.credential_audit("")

        self.assertIn("left-pad", deps)
        self.assertIn(".env.local", audit)
        self.assertIn("contents not read", audit)
        self.assertNotIn("TEST_API_KEY_VALUE", audit)

    def test_fetch_url_rejects_non_http_urls_without_reading_local_files(self) -> None:
        text = self.tools.fetch_url(str(self.project_root / ".env"))

        self.assertIn("only supports public http(s) URLs", text)
        self.assertNotIn("SECRET=***", text)

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
