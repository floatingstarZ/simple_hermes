import tempfile
import unittest
from pathlib import Path

from simple_hermes.session import SessionStore
from simple_hermes.state.continuity import ContinuityView


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sessions.db"
        self.store = SessionStore(path=self.db_path)

    def tearDown(self) -> None:
        self.store.conn.close()
        self.temp_dir.cleanup()

    def test_append_and_history(self) -> None:
        self.store.append("user", "hello")
        self.store.append("assistant", "hi")
        text = self.store.history_text()
        self.assertIn("[user] hello", text)
        self.assertIn("[assistant] hi", text)

    def test_search(self) -> None:
        self.store.append("user", "hello sqlite")
        self.store.append("assistant", "hi there")
        text = self.store.search_text("sqlite")
        self.assertIn("hello sqlite", text)

    def test_append_can_store_kind_and_tool_name_metadata(self) -> None:
        self.store.append("assistant", "used read", kind="tool_result", tool_name="read")
        row = self.store.history(limit=1)[0]
        self.assertEqual(row["kind"], "tool_result")
        self.assertEqual(row["tool_name"], "read")
        results = self.store.recent_tool_results(limit=1)
        self.assertEqual(results[0]["tool_name"], "read")
        self.assertIn("used read", results[0]["content"])

    def test_session_state_round_trips_structured_agent_state(self) -> None:
        self.store.set_state("default", "active_task", '{"goal": "write snake"}')
        self.assertEqual(self.store.get_state("default", "active_task"), '{"goal": "write snake"}')
        self.store.set_state("default", "active_task", '{"goal": "write html snake"}')
        self.assertEqual(self.store.get_state("default", "active_task"), '{"goal": "write html snake"}')
        self.store.delete_state("default", "active_task")
        self.assertIsNone(self.store.get_state("default", "active_task"))

    def test_can_create_child_session_with_parent_lineage(self) -> None:
        child_id = self.store.create_child_session("default", title="child task")
        children = self.store.child_sessions("default")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["id"], child_id)
        self.assertEqual(children[0]["title"], "child task")

    def test_fts_search_matches_tokens(self) -> None:
        self.store.append("user", "continuity lineage budget")
        rows = self.store.search("lineage")
        self.assertTrue(any("continuity lineage budget" in row["content"] for row in rows))

    def test_focused_search_text_returns_summary_view(self) -> None:
        self.store.append("user", "we decided to use sqlite for continuity")
        self.store.append("assistant", "noted, sqlite chosen for persistence")
        text = self.store.search_text("sqlite")
        self.assertIn("Focused recall summary", text)
        self.assertIn("sqlite", text.lower())

    def test_search_text_can_use_external_summarizer(self) -> None:
        self.store.append("user", "we decided to use sqlite for continuity")
        self.store.append("assistant", "noted, sqlite chosen for persistence")
        text = self.store.search_text("sqlite", summarizer=lambda q, rows: f"MODEL SUMMARY: {q} / {len(rows)}")
        self.assertIn("MODEL SUMMARY", text)

    def test_session_info_and_lineage_text_show_session_types(self) -> None:
        child_id = self.store.create_child_session("default", title="child task")
        cont_id = self.store.create_continuation_session(child_id)
        info = self.store.session_info(cont_id)
        self.assertEqual(info["session_type"], "continuation")
        text = self.store.lineage_text(cont_id)
        self.assertIn("[root]", text)
        self.assertIn("[child]", text)
        self.assertIn("[continuation]", text)

    def test_rename_session_updates_title(self) -> None:
        self.store.rename_session("default", "main coding session")
        info = self.store.session_info("default")
        self.assertEqual(info["title"], "main coding session")

    def test_recent_and_descendant_session_views(self) -> None:
        child_id = self.store.create_child_session("default", title="child task")
        cont_id = self.store.create_continuation_session(child_id)
        recent = self.store.recent_sessions_text()
        descendants = self.store.descendants_text("default")
        self.assertIn("Recent sessions:", recent)
        self.assertIn(child_id, recent)
        self.assertIn("Descendant sessions for default", descendants)
        self.assertIn(child_id, descendants)
        self.assertIn(cont_id, descendants)

    def test_latest_continuation_resume_ignores_child_sessions(self) -> None:
        first_cont = self.store.create_continuation_session("default")
        child_id = self.store.create_child_session("default", title="side task")
        second_cont = self.store.create_continuation_session(first_cont)

        self.assertEqual(self.store.latest_continuation_or_self("default"), second_cont)
        continuation_ids = [row["id"] for row in self.store.continuation_descendants("default")]
        self.assertIn(first_cont, continuation_ids)
        self.assertIn(second_cont, continuation_ids)
        self.assertNotIn(child_id, continuation_ids)

    def test_continuity_view_wrapper_exposes_same_session_browser(self) -> None:
        child_id = self.store.create_child_session("default", title="child task")
        cont_id = self.store.create_continuation_session(child_id)
        view = ContinuityView(self.store)
        self.assertIn(child_id, view.recent_sessions_text())
        self.assertIn(cont_id, view.descendants_text("default"))
        self.assertIn("Session lineage:", view.lineage_text(cont_id))


if __name__ == "__main__":
    unittest.main()
