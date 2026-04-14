# Simple Hermes 优先级升级第 2 轮

这轮升级继续围绕三件你最看重的核心：
- 持续运行能力
- 多 Agent 能力
- 鲁棒性 / 安全性

一、持续运行能力：这轮增强了什么

1. FTS-style recall 骨架
- `SessionStore` 现在创建了 `messages_fts` 虚拟表（SQLite FTS5）
- message append 时会同步写入 FTS 索引
- search 时会优先尝试 FTS 查询
- 如果 FTS 查询失败或无结果，再回退到 LIKE 搜索

这意味着：
`simple_hermes` 现在不再只是“朴素 LIKE 搜索历史”，
而是开始具备更接近 Full Hermes session_search 的检索骨架。

2. 更好的 recall 教学意义
- Full Hermes 用的是 FTS5 + focused summary
- 现在 `simple_hermes` 至少已经把最关键的“FTS 思想”补进来了
- 虽然还没有 focused summary model，但 continuity plane 已经继续向前了一步

二、多 Agent：这轮增强了什么

1. delegation 深度控制
- `SimpleAgent` 现在新增：
  - `delegation_depth`
  - `max_delegation_depth`
  - `child_step_budget`

2. child step budget
- child agent 不再只是继承 parent 的粗略步数
- 现在可以通过 `child_step_budget` 限制 child 任务预算

3. depth limit 拒绝机制
- 如果当前 agent 已经达到 tiny delegation depth limit
- 再次 `delegate` 时会返回：
  - `Delegation refused: tiny delegation depth limit reached.`

这虽然远不等于 Full Hermes 的完整 delegation budget/depth 系统，
但已经开始具备：
- 递归失控保护
- 子任务预算意识

三、鲁棒性与安全性：这轮增强了什么

1. 最小敏感文件保护
- `BuiltInTools.read_file()` 现在会拒绝读取一些明显敏感文件
- 包括：
  - `.env`
  - `.env.local`
  - `id_rsa`
  - `id_ed25519`
  - `*.pem`
  - `*.key`
  - `*.p12`
  - `*.pfx`
  - `*.token`

2. 这意味着 simple_hermes 已经开始有一个非常轻量的 safety guard
- 还远不是 Full Hermes 的 approval/safety system
- 但至少不是完全裸奔的本地文件读取器了

3. 之前已有的 robustness 仍然保留
- backend_error
- tool_error
- max_steps stop
- summary/compression
- session migration

四、这轮改动涉及的关键文件

1. 持续运行 / recall
- `simple_hermes/session.py`

2. delegation 预算 / 深度
- `simple_hermes/agent.py`

3. safety guard
- `simple_hermes/tools.py`

4. tests
- `tests/test_session.py`
- `tests/test_agent.py`
- `tests/test_tools.py`

五、测试覆盖了什么

这轮新增验证包括：
- FTS-style search 可以命中 token
- child session lineage 仍然正常
- delegation depth limit 生效
- sensitive file read 被拒绝
- 之前所有 agent/backend/trace/compression 功能不回归

最终状态：
- 26 个测试全部通过

六、这一轮之后，simple_hermes 到了什么层级

如果说上一轮让它拥有了：
- tiny continuity
- tiny delegation
- basic robustness

那么这一轮继续把骨架往前推进成：
- FTS-style recall idea
- delegation budget/depth controls
- minimal safety guard

所以现在它已经更像一个“带平台味的教学 agent 原型”了。

七、仍然还没有的关键东西

和 Full Hermes 相比，这一轮之后仍然还没有：
- focused summary recall
- continuation session split
- parallel child agents
- restricted child toolsets
- full approval model
- provider fallback chain
- skills/plugins/MCP
- gateway/acp/cron surfaces

八、一句话总结

这轮升级的意义是：
把 `simple_hermes` 从“有多 Agent 雏形和 continuity 骨架”，继续推进成“开始具备 recall、预算控制和最小安全边界的教学型平台原型”。