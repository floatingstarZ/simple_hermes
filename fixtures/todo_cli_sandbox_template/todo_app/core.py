from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TodoItem:
    title: str
    completed: bool = False


class TodoList:
    def __init__(self) -> None:
        self.items: list[TodoItem] = []

    def add(self, title: str) -> TodoItem:
        item = TodoItem(title=title)
        self.items.append(item)
        return item

    def complete(self, title: str) -> None:
        for item in self.items:
            if item.title == title:
                item.completed = True
                return
        raise ValueError(f"Unknown todo: {title}")

    def active_items(self) -> list[TodoItem]:
        return list(self.items)
