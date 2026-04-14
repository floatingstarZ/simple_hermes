# Simple Hermes 优先级升级第 8 轮

这轮继续往 Hermes 的结构风格和 continuity plane 对齐。

重点是：
- 把 continuity 浏览能力从 raw session storage 里抽出来
- 让结构更清楚：storage 负责存，continuity helper 负责看

一、为什么要做这一步

前几轮已经给 `SessionStore` 加了很多 continuity 能力：
- lineage
- recent sessions
- descendants
- session type inference

虽然能用，但如果这些浏览/解释逻辑都继续塞在 `SessionStore` 里，
后面代码会越来越像“数据库类什么都做”。

而 Hermes 的风格更像是：
- 持久化层负责存储
- 上层负责 continuity / recall / browsing / orchestration

所以这轮的目标就是：
把 continuity 浏览能力从 raw persistence 里抽成单独一层。

二、新增 continuity helper 模块

新增文件：
- `simple_hermes/state/continuity.py`

新增类：
- `ContinuityView`

它负责：
- `lineage()` / `lineage_text()`
- `recent_sessions()` / `recent_sessions_text()`
- `descendants()` / `descendants_text()`
- `session_info()`

这样一来：
- `SessionStore` 更像 raw persistence layer
- `ContinuityView` 更像 browsing / continuity helper layer

三、tools 已经改成走 continuity helper

`BuiltInTools` 现在不再直接把 lineage / sessions / descendants 全部压在 `SessionStore` 上调用，
而是持有：
- `self.continuity = ContinuityView(sessions)`

然后通过它暴露：
- `lineage`
- `sessions`
- `descendants`

这让结构更清楚，也更接近 Hermes 那种：
- storage
- higher-level continuity helpers
分层的味道。

四、这一步的意义

这轮并没有疯狂增加功能数量，
但它的意义很重要：

1. 代码结构更清楚
- storage 不再承担所有解释逻辑

2. 后面更容易继续扩展 continuity
- 例如 session browser
- better recall views
- continuity dashboards
- continuation inspectors

3. 更接近 Hermes 的实现风格
- 对齐的不是“文件越多越好”
- 而是“边界越来越清晰”

五、一句话总结

这轮升级的意义是：
把 `simple_hermes` 的 continuity 浏览层从 persistence 层里抽出来，形成一个更像 Hermes 风格的独立 helper 层，让后续持续运行能力的扩展更容易保持结构清晰。