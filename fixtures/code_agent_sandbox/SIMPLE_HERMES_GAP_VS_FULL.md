# Simple Hermes 与 Full Hermes 的功能差距清单

这份文档专门回答一个问题：

相比完整的 Hermes，`simple_hermes` 现在还缺什么？

先说结论：

`simple_hermes` 现在已经是一个真正的最小 agent，具备：

- state
- planning
- action
- iteration
- continuity 的最小版本

但它还不是完整的 Hermes 平台。
它更准确的定位是：

- 教学版最小 agent
- 可运行、可扩展、可理解
- 但仍然故意省略了大量 production-grade 平台能力

一、先看：simple_hermes 已经有了什么

目前 `simple_hermes` 已经具备：

1. 执行核心

- `SimpleAgent`
- 多步 agent loop
- 显式 `PlannerDecision` / `ToolCall`
- 最终 `AgentResponse` + trace

1. 工具层

- `ToolRegistry`
- 一组内建工具
- 显式命令优先直达工具

1. 状态层

- split memory（general + user）
- SQLite session history
- 基础 metadata（`kind`, `tool_name`）

1. 连续性层

- history
- recall/search
- tiny compression / summary

1. 模型层

- rule-based planner
- OpenAI-compatible backend
- Hermes runtime bridge

1. 交互层
6. 交互层
- CLI
- prompt_toolkit 输入编辑
- `/status` `/trace` `/help` 等 UI 命令

7. 多 Agent 雏形
- `delegate` 工具
- child session lineage
- child agent summary-only return

所以它绝对不是空壳了。

二、最大的差距：它还不是“平台”，只是“单表面 agent”

Full Hermes 的一个核心特点是：
它不是一个单独的终端 agent，而是一个平台。

`simple_hermes` 现在只有：

- CLI surface

而 Full Hermes 还有：

- Gateway（Telegram / Discord / Slack / API / Email 等）
- ACP adapter（编辑器接入）
- Cron（定时 agent 作业）
- 多平台 delivery

所以第一大差距就是：

`simple_hermes` 只有一个入口壳；
Hermes 有完整的多表面系统。

三、工具平台能力还差很多

`simple_hermes` 有最小的 registry 思想，但和 Hermes 相比还缺：

1. toolsets

- Full Hermes 可以按场景裁剪工具暴露面
- `simple_hermes` 现在没有真正的 toolset gating

1. availability / approval / safety checks

- Full Hermes 在危险工具上有审批、安全校验、路径约束等
- `simple_hermes` 没有生产级安全前置层

1. 大量工具生态

- Full Hermes 有 terminal、file、browser、delegate、execute_code、memory、session_search、cronjob、MCP 等完整工具面
- `simple_hermes` 只有少量教学工具

1. 并发与执行策略

- Full Hermes 对哪些工具可以并发、哪些必须顺序执行有更复杂控制
- `simple_hermes` 目前没有这层

所以第二大差距是：

`simple_hermes` 有最小工具平台骨架；
Hermes 有真正的平台级能力平面。

四、continuity plane 仍然只是教学缩略版

这是两者最重要的差距之一。

Full Hermes 的 continuity plane 包括：

- memory
- SessionDB
- session_search
- compression
- session lineage
- continuation session

而 `simple_hermes` 现在只有：

- split memory
- SQLite 历史
- 简单 search
- tiny summary

还缺这些关键能力：

1. 真正的跨会话 recall 系统

- Full Hermes 有 FTS5 + focused summary
- `simple_hermes` 只是 LIKE 搜索级别

1. lineage / continuation session

- Full Hermes 能在压缩、delegation、续接后保留谱系
- `simple_hermes` 没有 session lineage

1. 更强的 compression 机制

- Full Hermes 的 compression 是“任务延续机制”
- `simple_hermes` 现在只是教学版 summary 插入

1. memory flush / rebuild prompt / continuation split

- 这些都还没有

所以第三大差距是：

`simple_hermes` 已经有 continuity 的“概念骨架”；
Hermes 才有真正的平台级 continuity infrastructure。

五、runtime/provider 层还差很多生产能力

`simple_hermes` 现在已经有：

- OpenAI-compatible backend
- Hermes runtime bridge

这是很重要的一步，但和 Full Hermes 相比仍然缺：

1. 完整 provider/runtime abstraction

- Full Hermes 对 provider/model/base_url/api_mode 的统一更成熟
- `simple_hermes` 只是借用 + 极简包装

1. fallback chain

- Full Hermes 有 transport recovery -> fallback -> restore primary
- `simple_hermes` 没有这整条生产容错链

1. 更细粒度模型能力差异处理

- Full Hermes 会处理不同 provider 的接口差异、reasoning 差异等
- `simple_hermes` 基本没有这层

1. usage/cost/pricing 跟踪

- Full Hermes 有 usage/pricing 相关能力
- `simple_hermes` 还没有

所以第四大差距是：

`simple_hermes` 已经能接真实 backend；
Hermes 才有完整运行时系统。

六、多 agent / delegation 还没有

六、多 agent / delegation 仍然只是很小的雏形

Full Hermes 很重要的一层是：
- `delegate_task`
- child agent
- fresh conversation
- restricted toolsets
- summary-only return

`simple_hermes` 现在已经有了一点雏形：
- `delegate` 工具
- child session lineage
- child agent summary-only return

但它仍然还没有：
- subagent budget / depth control
- restricted toolsets for child agents
- 并发 child agents
- richer parent/child coordination
- child heartbeat / progress reporting

这意味着：
它已经不是纯单 agent 系统，
但还远不是 Full Hermes 的多 agent 系统。

七、生态扩展层还没有

Full Hermes 不只是 agent 本体，它还有：

- skills
- plugins
- MCP

`simple_hermes` 现在都还没有：

1. Skills

- 没有 procedural memory / skill files

1. Plugins

- 没有运行时代码扩展系统

1. MCP

- 没有外部工具协议接入层

所以第五大差距是：

`simple_hermes` 现在还是“封闭的小系统”；
Hermes 是可扩展生态平台。

八、观测性与运维能力也还差很远

Full Hermes 还有很多更偏生产的能力：

- richer logs
- transport recovery
- background task/process management
- tool execution observability
- richer error classification
- durability across surfaces

`simple_hermes` 现在虽然有：

- trace
- session metadata

但离生产观测性仍然差很多。

九、UI 也仍然只是轻量 CLI，不是完整产品体验

现在 `simple_hermes` 的 CLI 已经好很多了：

- prompt_toolkit
- banner
- tips
- status
- trace

但和 Full Hermes CLI 比，仍然缺：

- 更复杂的 TUI 布局
- 工具进度流式展示
- richer slash command system
- 更强的模型/模式切换交互
- 更完整的状态面板

所以在体验层面它还是“简洁可用”，不是“完整产品级前端”。

十、用一句话概括还缺什么

如果把 Hermes 分成四层：

1. Execution Core
2. Capability Plane
3. Continuity Plane
4. Surface & Ecosystem Plane

那么 `simple_hermes` 的现状是：

- 第 1 层：已经有了最小可用版
- 第 2 层：有骨架，但还远不够丰富
- 第 3 层：有概念缩略版，但还不是完整基础设施
- 第 4 层：基本还没展开

十一、最值得继续补的功能优先级
七、最值得继续补的功能优先级

如果未来还要继续把 `simple_hermes` 往 Full Hermes 靠，我建议优先顺序是：

第一优先级：真正增强 continuity
1. model-assisted recall summarizer
2. better compression
3. continuation session / lineage refinement

第二优先级：增强工具平台与治理
4. richer child toolset restriction
5. richer approval/safety guard
6. more tools

第三优先级：增强 runtime
7. fallback/recovery
8. richer backend/provider abstraction

第四优先级：增强平台表面
9. mini cron
10. richer delegation orchestration
11. mini skills or MCP

十二、最后一句判断

现在的 `simple_hermes` 已经完成了“真正最小 agent”的任务。

它还没完成的，不再是“agent 基本成立”这件事，
而是：

如何从一个最小 agent，逐步长成像 Full Hermes 那样的 agent 平台。

所以你如果问：
“相比 Hermes，还有哪些功能没加？”

最准确的回答是：

它缺的主要不是单点 feature，而是完整的平台层：

- 多表面
- 大工具平面
- 完整 continuity infrastructure
- 多 agent
- 生态扩展层
- 生产级 runtime/recovery/observability
