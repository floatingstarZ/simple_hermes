import tempfile
import unittest
from pathlib import Path

from simple_hermes.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.temp_dir.name) / "memory.txt"
        self.user_path = Path(self.temp_dir.name) / "user.txt"
        self.store = MemoryStore(memory_path=self.memory_path, user_path=self.user_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_general_memory_persists(self) -> None:
        result = self.store.add("project uses sqlite")
        self.assertIn("Saved memory", result)

        reloaded = MemoryStore(memory_path=self.memory_path, user_path=self.user_path)
        self.assertIn("project uses sqlite", reloaded.list_text())

    def test_add_user_memory_persists_separately(self) -> None:
        result = self.store.add_user("user prefers diagrams")
        self.assertIn("Saved user memory", result)

        reloaded = MemoryStore(memory_path=self.memory_path, user_path=self.user_path)
        self.assertIn("user prefers diagrams", reloaded.list_user_text())
        self.assertNotIn("user prefers diagrams", reloaded.list_text())

    def test_prompt_block_contains_both_sections(self) -> None:
        self.store.add("project uses sqlite")
        self.store.add_user("user prefers diagrams")

        prompt = self.store.as_prompt_block()
        self.assertIn("Known durable facts", prompt)
        self.assertIn("Known user preferences", prompt)
        self.assertIn("project uses sqlite", prompt)
        self.assertIn("user prefers diagrams", prompt)


if __name__ == "__main__":
    unittest.main()
