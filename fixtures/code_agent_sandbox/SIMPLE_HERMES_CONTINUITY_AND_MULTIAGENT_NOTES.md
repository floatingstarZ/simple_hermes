# Simple Hermes 持续运行与多 Agent 升级说明

这次升级主要补的是三件你最看重的能力：
- 持续运行能力
- 多 Agent 能力
- 基础鲁棒性

一、持续运行能力：这次增强了什么

1. Session lineage
- `SessionStore` 现在不只保存消息，还保存 session 之间的父子关系
- `sessions` 表新增：
  - `parent_session_id`
  - `title`
- 现在 child session 可以明确挂到 parent session 下面

2. Child session creation
- 新增：
  - `create_child_session(parent_session_id, title)`
  - `child_sessions(parent_session_id)`
- 这让系统第一次拥有了真正的 session 谱系概念

3. 更结构化的消息元数据
- 消息已经带有：
  - `kind`
  - `tool_name`
- 这样 history 不只是文本堆积，而是更接近“可解释执行日志”

4. tiny continuity 仍然保留
- 旧的 summary/compression 机制依然存在
- 会在消息积累到一定程度后插入 summary

这一层虽然仍然远弱于 Full Hermes 的 Continuity Plane，
但已经不再只是“SQLite 历史记录”，而是开始有：
- session structure
- parent/child lineage
- structured message types

二、多 Agent：这次增强了什么

1. 新增 `delegate` 工具
- 现在 `BuiltInTools` 里已经注册：
  - `delegate`
- 用法：
  - `delegate <subtask>`

2. Child agent 真正存在了
- `SimpleAgent` 内新增 `_delegate_task()`
- 它会：
  1. 创建 child session
  2. 用相同 project root 创建 child agent
  3. 共享 memory/store，但使用不同 session_id
  4. 运行 child task
  5. 返回 summary

3. Summary-only return
- child agent 结果不会把全部轨迹直接灌回 parent
- parent 拿到的是：
  - `Child agent summary (...)`

这点非常接近 Full Hermes 的教学核心：
- child agent 是隔离出来的执行单元
- 返回 summary，而不是直接污染父上下文

虽然这仍然不是完整的 `delegate_task` 系统，但它已经把最关键的概念骨架补上了。

三、鲁棒性：这次增强了什么

1. backend 错误不再直接崩
- 如果 backend planning 抛异常
- 现在会返回结构化错误文本：
  - `Backend planning failed: ...`
- 同时 trace 里会记录 `backend_error`

2. tool 错误不再直接崩
- 如果工具执行抛异常
- 现在会返回：
  - `Tool <name> failed: ...`
- trace 里会记录 `tool_error`

3. step limit 依然保留
- backend-driven 多步循环仍然有 `max_steps`
- 超出后会干净地停止，而不是无限循环

所以现在系统在“planning 出错”或“tool 出错”时，
已经开始有一点 Full Hermes 那种“不要立刻崩掉”的味道了。

四、这次改动对应的关键文件

1. Session / continuity
- `simple_hermes/session.py`

2. Agent loop / delegation / robustness
- `simple_hermes/agent.py`

3. Tool registration
- `simple_hermes/tools.py`

4. Tests
- `tests/test_agent.py`
- `tests/test_session.py`

五、这次升级后，simple_hermes 的层级变化

升级前：
- 最小单 agent
- tiny continuity
- real backend bridge

升级后：
- 单 agent + tiny multi-agent
- tiny continuity + session lineage
- 基础 backend/tool error robustness

换句话说：
现在它已经不只是“一个能调用工具的小 agent”，
而是开始具备：
- 任务拆分
- session 谱系
- 错误承受力

六、仍然还没做到的地方

相比 Full Hermes，仍然明显还没有：
- 真正的 FTS5 + focused summary recall
- continuation session / session split
- delegate_task 的 budget/toolset/depth control
- 并发 child agents
- richer fallback chain
- production-grade approval/safety

所以现在最准确的表述是：

`simple_hermes` 已经开始拥有持久运行、多 Agent、鲁棒性的骨架；
但还没有 Full Hermes 那种平台级完整实现。

七、一句话总结

这轮优化的意义是：
把 `simple_hermes` 从“真实最小 agent”推进到了“开始拥有平台能力雏形的最小 agent”。