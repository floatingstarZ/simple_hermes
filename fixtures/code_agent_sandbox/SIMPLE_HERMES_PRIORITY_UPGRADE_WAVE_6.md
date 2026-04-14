# Simple Hermes 优先级升级第 6 轮

这轮继续往 Hermes 的 continuity plane 和结构分层方式靠。

重点补了：
- session type metadata
- lineage view
- status 面板里的 continuity 信息
- 结构继续向 `agent/ tools/ state/` 子包收拢

一、session type metadata

`SessionStore.sessions` 现在不只是：
- id
- parent_session_id
- title

还新增了：
- `session_type`

目前主要类型包括：
- `root`
- `child`
- `continuation`

这意味着系统现在不仅知道“谁是谁的 parent”，
也开始知道“这个 session 在 continuity 系统里扮演什么角色”。

二、lineage view

`SessionStore` 现在新增：
- `session_info(session_id)`
- `lineage(session_id)`
- `lineage_text(session_id)`

而工具层新增了：
- `lineage`

因此现在可以直接在 CLI 里输入：
- `lineage`

查看当前 session 的 lineage chain。

这一步很重要，因为它让 continuity 不再只是数据库内部结构，
而开始变成 agent 可见、用户可见的运行视图。

三、UI status 也更能体现 continuity 了

`/status` 现在会显示：
- current session id
- session type
- parent session
- message count

这样一来，当系统发生：
- child delegation
- continuation split
时，你能直接在 UI 里看出来当前自己到底处在哪个 session 上下文里。

四、结构继续对齐 Hermes

这轮没有再把逻辑堆回顶层，
而是继续把真实实现收进：
- `simple_hermes/agent/`
- `simple_hermes/tools/`
- `simple_hermes/state/`

顶层兼容文件仍然保留，但已经越来越薄。

这代表：
`simple_hermes` 现在不仅在能力上对标 Hermes，
也开始在代码组织方式上对标 Hermes。

五、一句话总结

这轮升级的意义是：
把 continuity 从“内部机制”推进到了“带类型、带 lineage、带 UI 可见状态的运行结构”，并继续让代码结构向 Hermes 的实现风格收拢。