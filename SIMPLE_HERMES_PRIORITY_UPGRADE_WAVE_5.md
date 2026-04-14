# Simple Hermes 优先级升级第 5 轮

这轮继续对标 Hermes 的“持续运行 + 可治理执行”思路，重点补了：
- focused recall summary
- child tool restriction
- tiny approval guard

一、focused recall summary

之前：
- recall 只是把匹配到的历史列出来

现在：
- `search_text()` 返回的是 focused recall summary 结构
- 形式上包含：
  - 标题：`Focused recall summary for '<query>'`
  - headline：把命中的前几条内容压缩成一句简要总结
  - supporting snippets：附带支撑片段

这一步很重要，因为它让 continuity 不再只是“检索”，
而开始接近“把历史重新组织成当前可用上下文”。

二、child tool restriction

现在 child agent 不再默认拥有 parent 的全部工具面。

新增机制：
- `allowed_tools`
- `ToolRegistry` 在 run/help 两个层面都会限制可见/可执行工具

当前 child 的默认工具集合更窄，保留的是：
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

因此 child 不能再继续发起：
- `parallel_delegate`
- 等更强 orchestration 行为

这一步的意义是：
让 child agent 从“能跑”变成“受控 worker”。

三、tiny approval guard

新增环境变量：
- `SIMPLE_HERMES_REQUIRE_APPROVAL`

支持：
- `delegate`
- `parallel_delegate`
- 或 `all`

如果命中审批规则，会返回：
- `Approval required for ...`

这不是完整的 approval UI/workflow，
但它已经把“某些动作需要许可”的边界引入系统了。

四、这轮升级的架构意义

如果说前几轮补的是：
- agent loop
- state
- continuity skeleton
- child sessions
- parallel workers

那么这一轮补的是：
- recall 质量
- child 治理
- delegation 风险边界

也就是说，系统现在不只是更能跑，
而且更开始像“一个可治理的 agent 系统”。

五、当前仍然还缺的关键 Hermes 特性

仍然没有：
- model-assisted recall summarizer
- continuation session split
- richer approval interaction
- provider fallback chain
- gateway / ACP / cron / MCP / skills / plugins

六、一句话总结

这轮升级的意义是：
把 `simple_hermes` 从“有持续运行骨架和多 Agent 雏形”，进一步推进成“开始有 recall 质量、child 权限边界、和 delegation 审批意识的教学型 agent 原型”。