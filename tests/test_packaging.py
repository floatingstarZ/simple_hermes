import stat
import tomllib
import unittest
from unittest import mock
from pathlib import Path

from scripts.run_code_agent_benchmark import agent_env


REPO_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_pyproject_exposes_console_script(self) -> None:
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            data["project"]["scripts"]["simple_hermes_codex"],
            "simple_hermes.cli:main",
        )
        include = data["tool"]["setuptools"]["packages"]["find"]["include"]
        self.assertIn("simple_hermes*", include)

    def test_setup_script_installs_current_checkout(self) -> None:
        setup_path = REPO_ROOT / "setup.sh"
        self.assertTrue(setup_path.exists())
        mode = setup_path.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "setup.sh should be executable")

        text = setup_path.read_text(encoding="utf-8")
        self.assertIn('pip install --no-build-isolation -e "$PROJECT_ROOT"', text)
        self.assertIn("simple_hermes_codex_local.pth", text)
        self.assertIn("simple_hermes_codex", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("hermes-agent", text)

    def test_benchmark_env_does_not_assume_local_hermes_checkout(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            env = agent_env(Path("/tmp/project"), "bench-session")

        self.assertEqual(env["SIMPLE_HERMES_PROJECT_ROOT"], "/tmp/project")
        self.assertEqual(env["SIMPLE_HERMES_SESSION_ID"], "bench-session")
        self.assertNotIn("SIMPLE_HERMES_BACKEND", env)
        self.assertNotIn("SIMPLE_HERMES_HERMES_ROOT", env)


if __name__ == "__main__":
    unittest.main()
