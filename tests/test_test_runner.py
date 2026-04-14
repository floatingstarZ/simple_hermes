import tempfile
import unittest
from pathlib import Path

from scripts.run_tests_with_results import parse_trace_steps, parse_unittest_output, write_html_report, write_trace_html_report


class TestRunnerReportTests(unittest.TestCase):
    def test_parse_unittest_output_and_write_html_report(self) -> None:
        output = (
            "test_ok (tests.test_demo.DemoTests.test_ok) ... ok\n"
            "test_bad (tests.test_demo.DemoTests.test_bad) ... FAIL\n"
        )
        tests = parse_unittest_output(output)
        self.assertEqual(len(tests), 2)
        self.assertEqual(tests[0]["status"], "ok")
        self.assertEqual(tests[1]["status"], "FAIL")

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            write_html_report(
                html_path,
                command=["python3", "-m", "unittest"],
                cwd=Path(temp_dir),
                returncode=1,
                tests=tests,
                output=output,
            )
            html = html_path.read_text(encoding="utf-8")

        self.assertIn("Per-test view", html)
        self.assertIn("test_ok", html)
        self.assertIn("test_bad", html)
        self.assertIn("Needs check", html)

    def test_parse_trace_steps_and_write_trace_html_report(self) -> None:
        output = "\x1b[36mstep 01 · tool_call [write_file]\x1b[0m Use tool write_file\nstep 01 · tool_result [write_file] Wrote file\n"
        steps = parse_trace_steps(output)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["kind"], "tool_call")
        self.assertEqual(steps[1]["tool"], "write_file")

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "trace.txt"
            html_path = Path(temp_dir) / "trace.html"
            log_path.write_text(output, encoding="utf-8")
            write_trace_html_report(html_path, title="Trace", log_path=log_path, output=output)
            html = html_path.read_text(encoding="utf-8")

        self.assertIn("Streamed step view", html)
        self.assertIn("tool_call", html)
        self.assertIn("Wrote file", html)


if __name__ == "__main__":
    unittest.main()
