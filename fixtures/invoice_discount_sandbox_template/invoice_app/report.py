from __future__ import annotations

from .operations import total


def invoice_summary(items: list[float]) -> str:
    return f"Total: ${total(items):.2f}"
