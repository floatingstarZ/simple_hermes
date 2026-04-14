# Todo Core

A tiny Python business-logic fixture for code-agent scenario tests.

Task target: make completed todo items disappear from `active_items()` while keeping them in the full item list.

Expected agent task:

```text
请把这当成一个完整任务一次性完成：先解析这个 Python todo 项目；然后修复 active_items()，让已经完成的 todo 不再出现在 active_items 结果里，但仍保留在全部 items 里；修改后运行测试验证；最后总结改了什么和测试是否通过。
```

Initial state: `python -m unittest discover -s tests -v` fails until `TodoList.active_items()` filters completed items.
