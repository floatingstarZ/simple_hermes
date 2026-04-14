# Simple Hermes 优先级升级第 3 轮

这轮继续对标 Hermes 的核心精神，但仍然保持教学版的简单性。

重点推进的是：
- focused continuity 的前置能力
- child governance
- safety / approval 的最小边界
- 并发 child delegation

一、这轮新增了什么

1. safe parallel delegation
- 新增 `parallel_delegate`
- 允许把几个独立子任务并发交给 child agents 处理
- 返回 summary-only 聚合结果
- 每个 child 有独立 session_id
- 每个 child 使用自己的 SQLite 连接与 memory 实例，避免跨线程问题

2. FTS-style recall skeleton 继续稳定化
- session search 优先走 FTS，再回退到 LIKE
- 这使 continuity 不再只是简单字符串查找

3. delegation governance 更完整
- `delegation_depth`
- `max_delegation_depth`
- `child_step_budget`
- child lineage 继续保留

4. minimal safety guard
- 读取明显敏感文件时拒绝执行
- 这是 tiny approval/safety 思想的最小化版本

二、这些能力的意义

1. 并发 child delegation
意义：
- 把多 Agent 从“存在 child”推进到“能并发分工”
- 更接近真实 worker model
- 让 delegation 的价值不只体现在隔离，也体现在吞吐

2. FTS-style recall
意义：
- 让长期运行系统开始具备更靠谱的检索骨架
- 为 future focused summary recall 打地基

3. governance / restriction / limits
意义：
- 没有预算和深度限制的 delegation 很容易失控
- 这些约束是多 Agent 从“好玩”走向“可治理”的必要条件

4. safety guard
意义：
- 能力越强，越需要最小安全边界
- tiny safety 不是完整 approval system，但已经能避免一部分低级风险

三、这轮之后 still missing 的关键 Hermes 能力

仍然还没有：
- focused recall summary（现在只有 FTS skeleton）
- child toolset restriction（child 仍然没有真正的工具裁剪）
- richer approval model
- provider fallback chain
- continuation session split
- gateway / ACP / cron / MCP / skills / plugins

四、一句话总结

这轮升级的意义是：
把 `simple_hermes` 从“有 child agent 雏形”，继续推进成“开始拥有并发 worker、持续运行检索骨架、以及最小治理边界的教学型 agent 原型”。