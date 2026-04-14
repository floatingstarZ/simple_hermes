# Coin Catcher

A tiny browser game fixture for code-agent scenario tests.

Task target: update `src/game.js` so each collected coin awards 10 points.

Expected agent task:

```text
请把这当成一个完整任务一次性完成：先解析这个小游戏项目；然后把收集金币的得分规则从每次 +1 改为每次 +10；修改后运行测试验证；最后总结改了什么和测试是否通过。
```

Initial state: `python -m unittest discover -s tests -v` fails until the scoring rule is fixed.
