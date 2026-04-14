# Simple Hermes：为什么下一个优先级是这些？

你问得非常对：
除了“继续加功能”，更重要的是搞清楚这些功能的 motivation 到底是什么。

这份文档就是专门解释：
为什么我把下一步优先级放在
- 并发 / 多 Agent
- recall/continuity
- safety / robustness

一、为什么并发要先加

因为一旦系统开始有 delegation，
下一个最自然的问题就会变成：

“如果有两个互不依赖的子任务，为什么还要串行跑？”

并发的 motivation 不是“炫技”，而是三件非常现实的事：

1. 降低总任务耗时
- 独立子任务可以同时推进
- 这会让 agent 更像真正的工作系统，而不是单线程脚本

2. 更贴近 Full Hermes 的多 Agent 价值
- Full Hermes 的多 agent 价值不只是 isolation
- 还包括把不同子问题分发出去并发推进

3. 让 delegation 不只是“语义上的 child agent”
- 现在 tiny delegation 已经成立
- 并发会让它更像真正的 worker model

所以并发是多 Agent 体系从“有骨架”变成“像一个系统”的关键一步。

二、为什么 focused recall / 更强 continuity 重要

因为 agent 一旦不是一次性对话，而是连续运行，
最大的敌人就不只是“不会做”，而是：

“做过，但想不起来”

所以 recall 的 motivation 是：

1. 避免历史浪费
- 已经做过的事，不能每轮都重新推理

2. 支撑长期任务
- 多轮任务会越来越长
- 不能只靠最近几条消息活着

3. 让 memory 和 history 真正形成协同
- memory 存 durable facts
- history 存过程
- recall 负责把历史重新变成当前有用的信息

为什么我说“focused recall”比普通 search 更重要？
因为：
- 普通 search 只是找到旧文本
- focused recall 才是把旧文本重新压缩成当前可用上下文

这是 Full Hermes continuity plane 的关键精神。

三、为什么 child toolset restriction 重要

当系统开始有 child agent 时，
下一个问题就不是“能不能分任务”，而是：

“child agent 拿到什么权限？”

这件事的 motivation 很强：

1. 防止 child 越权
- 如果 child 和 parent 拿一样的全部工具
- 多 agent 会很快变成失控扩散

2. 让 agent 结构更清晰
- parent 做 orchestrator
- child 做专门 worker
- 这时候权限边界必须体现角色差异

3. 更贴近 Full Hermes 的 delegation 哲学
- 真正的 delegate_task 不是复制 parent 全部能力
- 而是生成受控 worker

所以 child toolset restriction 是“多 Agent 从能跑到可治理”的关键步骤。

四、为什么最小 approval / safety layer 重要

因为 agent 一旦开始：
- 多步执行
- delegation
- 真实 backend planning

它就不再只是一个“解释器”，而是会越来越像一个能真实行动的系统。

这时没有 safety layer，就意味着：
- 可以更快地做错事
- 可以更稳定地做错事

safety 的 motivation 有三层：

1. 降低破坏性风险
- 文件操作、命令执行、路径访问，本来就有真实风险

2. 让系统可扩展
- 没有安全边界，能力越多越危险
- 有边界，能力越多才越可控

3. 让鲁棒性不是“遇错不崩”，而是“默认不乱来”
- 这是更高级的鲁棒性

所以安全前置不是附属 feature，
而是 Agent 从玩具走向系统的必要条件。

五、为什么这些优先级是这个顺序

我现在给 `simple_hermes` 的优先级排序大致是：

第一梯队：
1. 并发 child agents
2. 更强 recall / focused continuity
3. child toolset restriction
4. minimal approval / safety

原因是：
- 并发让多 Agent 体系真正开始像系统
- recall 让长期运行真正开始像系统
- toolset restriction + safety 让系统开始可治理

换句话说：
前两个解决“能不能长期高效工作”，
后两个解决“会不会失控”。

六、一句话总结

为什么接下来优先做这些？

因为现在 `simple_hermes` 已经不是“能不能做成 agent”的问题了，
而是：

如何让它开始具备 Full Hermes 最核心的三个平台气质：
- 长期持续运行
- 多 agent 协作
- 受控且不失控的执行
