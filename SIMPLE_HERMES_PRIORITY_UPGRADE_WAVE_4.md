# Simple Hermes 优先级升级第 4 轮

这轮升级继续向 Hermes 对标，但仍然坚持“教学优先、复杂度可控”。

核心推进：
- focused recall summary
- child tool restriction
- tiny approval guard

一、focused recall summary

之前的 recall 已经有：
- FTS-style search skeleton
- LIKE fallback

这轮进一步升级成：
- `search_text()` 不再只是原始搜索结果列表
- 现在会返回：
  - `Focused recall summary for '<query>'`
  - 一个简短 headline
  - supporting snippets

这意味着：
`simple_hermes` 的 recall 已经开始从“查文本”转向“把历史整理成当前有用信息”。

虽然这还不是 Full Hermes 的完整 focused summary model，
但方向已经对了。

二、child tool restriction

之前 child agent 仍然默认拿到和 parent 近似的工具面。
这轮新增了：
- `allowed_tools` 机制
- `ToolRegistry` 可以限制允许执行的工具
- child agent 现在只拿一个更窄的工具集合

这让多 Agent 更接近 Hermes 的“受控 child worker”思想。

当前 child 默认保留的工具大致是：
- help
- history
- recall
- read
- search
- summarize
- memories
- user_memories
- remember
- remember_user

而像 `parallel_delegate` 这样的更强 orchestration 能力，child 会被限制。

三、tiny approval guard

这轮新增了一个非常轻量的 approval 入口：
- `SIMPLE_HERMES_REQUIRE_APPROVAL`

目前它可以用于：
- `delegate`
- `parallel_delegate`
- 或者全部敏感操作（通过更激进配置）

例如：
- `SIMPLE_HERMES_REQUIRE_APPROVAL=delegate`

此时 delegation 会返回：
- `Approval required for delegate ...`

这不是 Full Hermes 那种完整 approval workflow，
但它已经把“风险操作需要额外许可”的思想引进来了。

四、这一轮的意义

如果说上一轮主要解决的是：
- 持续运行骨架
- 多 Agent 雏形
- 并发 worker

那么这一轮主要解决的是：
- recall 从 raw search 走向 focused recall
- child agent 从能跑走向可治理
- risky actions 从裸奔走向最小审批边界

这几步正好补的是 Hermes 平台味里很重要的部分：
- continuity quality
- governance
- safety

五、仍然还没有的部分

对比 Full Hermes，仍然还缺：
- 更强 recall summarizer / topic-aware synthesis
- continuation session split
- richer child toolsets / availability checks
- full approval UI / interactive confirmation
- provider fallback chain
- gateway / ACP / cron / MCP / skills / plugins

六、一句话总结

这轮升级的意义是：
把 `simple_hermes` 从“有检索、有 child agent、有并发”进一步推进成“开始有 recall 质量、child 治理、以及最小审批意识的教学型 agent 平台原型”。