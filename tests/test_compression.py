import tempfile
import unittest
from pathlib import Path

from simple_hermes.agent import SimpleAgent


class CompressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.agent = SimpleAgent(
            project_root=self.project_root,
            base_dir=Path(self.temp_dir.name) / "state",
        )

    def tearDown(self) -> None:
        self.agent.sessions.conn.close()
        self.temp_dir.cleanup()

    def test_history_compression_creates_summary_message(self) -> None:
        for i in range(8):
            self.agent.run(f"remember fact {i}")
        history_text = self.agent.sessions.history_text(limit=50)
        self.assertIn("[summary]", history_text)
        self.assertIn("Earlier conversation summary", history_text)


if __name__ == "__main__":
    unittest.main()
