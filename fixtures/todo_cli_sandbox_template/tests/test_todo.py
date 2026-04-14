import unittest

from todo_app import TodoList


class TodoListTest(unittest.TestCase):
    def test_completed_items_are_not_active(self) -> None:
        todos = TodoList()
        todos.add("draft review")
        todos.add("run tests")

        todos.complete("draft review")

        self.assertEqual([item.title for item in todos.active_items()], ["run tests"])
        self.assertEqual([item.title for item in todos.items], ["draft review", "run tests"])

    def test_unknown_todo_raises_value_error(self) -> None:
        todos = TodoList()

        with self.assertRaises(ValueError):
            todos.complete("missing")


if __name__ == "__main__":
    unittest.main()
