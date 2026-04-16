# 自进化 Agent 框架综述

更新时间：2026-04-16

## 核心判断

“自进化 Agent”不是单一技术，而是一组闭环机制。当前工作大致分成五类：

1. 训练型自进化：自动生成任务、复用经验、进行 RL 或偏好优化。
2. 技能库自进化：从轨迹、失败、工单、跨用户经验中沉淀和修订 reusable skills。
3. 轨迹失败学习：把失败执行轨迹转成负反馈、拒绝样本或经验记忆。
4. 工作流/程序自进化：搜索和改写 agentic workflow、agent program 或工具调用策略。
5. 治理与评测：评估长期演化增益、结构稳定性、安全边界和形式化约束。

对 Simple Hermes 最相关的是第 2、3、5 类：skill 生成/修订、失败 trace 归因、长期 benchmark。AgentEvolver 这类训练框架可以作为远期参考，但不适合直接搬进轻量 code agent。

## 框架速览

| 框架/论文 | 类型 | 核心闭环 | 适合借鉴到 Simple Hermes 的部分 |
| --- | --- | --- | --- |
| AgentEvolver | 训练型自进化 agent | self-questioning -> self-navigating -> self-attributing -> RL 优化 | 任务生成、经验池、轨迹归因的概念模型 |
| Memento-Skills | skill/memory 自进化 | read -> write reflective learning；skill router 选择技能，执行后更新 skill library | Markdown skill 作为可演化 procedural memory |
| SkillClaw | 群体 skill 演化 | client proxy 记录 session artifact；evolve server 聚合并写回共享 skill store | skill 版本、shared skill store、验证发布流 |
| SkillX | 可插拔 skill KB | 多层技能抽取 -> 迭代修订 -> 探索扩展 | 从轨迹抽取 strategic / functional / atomic 三层技能 |
| SkillForge | 领域 skill 自演化 | Domain skill creator -> Failure Analyzer -> Skill Diagnostician -> Skill Optimizer | 失败归因到具体 skill 缺陷，再重写 skill |
| UI-Voyager | GUI agent 失败学习 | failed experience -> rejection fine-tuning / self-distillation | 把失败 trace 当作可训练/可总结资产，而不是只作为日志 |
| HyEvo | workflow 自进化 | heterogeneous node synthesis + multi-island evolution + reflect-then-generate | 在轻量层面可借鉴“workflow spec + feedback refinement” |
| SEVerA | verified self-evolving agents | Search -> Verification -> Learning；FGGM 用形式化 contract 守住输出 | 自进化前必须有 contract / validator / fallback |
| SEA-Eval | 评测框架 | sequential task streams；看 success rate 和 token consumption 随时间变化 | 给 Simple Hermes 做长期演化 benchmark |
| Zombie Agents | 安全反例 | 间接注入进入长期记忆，跨 session 触发恶意行为 | 自进化 memory/skill 写入必须有隔离、审计和验证 |

## 1. AgentEvolver：训练级自进化

AgentEvolver 是目前最接近“引擎 Evolver”的框架。它不是简单 prompt 反思，而是把 agent 训练拆成三种自进化机制：

- Self-Questioning：探索环境并自动生成多样任务，降低人工构造任务集成本。
- Self-Navigating：总结和复用跨任务经验，提高 rollout 和探索效率。
- Self-Attributing：处理长轨迹，估计中间状态/动作对结果的贡献，用于更细粒度的 policy optimization。

它的定位是 end-to-end training framework，包含 environment service、task manager、experience manager、advantage processor，最后服务于 GRPO/RL 训练。对 Simple Hermes 来说，直接实现 RL 不现实，但可以抽出三个工程抽象：

- task generator：从项目/历史失败生成回归任务。
- experience pool：把成功/失败轨迹压缩成可检索经验。
- attribution note：对一次失败写出“哪个步骤/工具/skill 导致失败”。

参考：

- https://github.com/modelscope/AgentEvolver
- https://modelscope.github.io/AgentEvolver/
- https://huggingface.co/papers/2511.10395

## 2. Memento-Skills：skill 作为长期可演化记忆

Memento-Skills 把 agent 看成 “agent-designing agent”。核心不是只保存聊天记忆，而是把可复用行为写成 structured markdown skills。系统通过 read-write reflective learning 在执行中选择、更新、扩展技能库。

关键启发：

- skill 不是静态提示词，而是 procedural memory。
- skill router 需要根据当前 stateful prompt 选择相关技能。
- skill 写入必须来自经验，而不是泛泛总结。
- 一个 agent 可以通过 skill library 变成不同任务专用 agent 的生成器。

Simple Hermes 已有 `skills create/view/use/list/delete`，但还缺少：

- 自动从成功/失败 trace 生成 skill draft。
- 使用 skill 后记录 outcome。
- 发现 skill stale/wrong 后自动提出 patch。

参考：

- https://www.emergentmind.com/papers/2603.18743
- https://papers.cool/arxiv/2603.18743

## 3. SkillClaw：跨用户/群体 skill 演化

SkillClaw 是和 Hermes/OpenClaw skill 生态最贴近的工作。它把 skills 从本地静态文件变成可群体演化资产：

- client 侧 proxy 记录 agent session artifacts。
- evolve server 读取共享存储中的 session data。
- server 用 workflow engine 或 agent engine 生成/改写 skills。
- 写回 shared skill store，客户端可 pull/push/sync。
- 支持 validated publish mode，要求验证结果、审批数、平均分等条件。

这比 Hermes 当前 “agent 可以创建/更新本地 skill” 更系统。对 Simple Hermes 的可借鉴点：

- skill outcome log：每次 skill use 后保存任务、结果、失败原因。
- skill candidate directory：生成候选 skill，不直接覆盖稳定 skill。
- validated promotion：候选 skill 通过 benchmark 后再发布。
- remote/shared backend 暂时不用做，可先用本地目录模拟。

参考：

- https://github.com/AMAP-ML/SkillClaw
- https://www.alphaxiv.org/abs/2604.08377

## 4. SkillX / SkillForge：从轨迹和失败中修订技能

SkillX 关注从 raw trajectories 自动构造 plug-and-play skill KB，强调三层技能结构：

- strategic plans：高层规划技能。
- functional skills：可复用工具子流程。
- atomic skills：具体工具调用模式。

SkillForge 更偏企业场景，核心是 failure trace -> skill deficiency -> skill rewrite：

- Domain-Contextualized Skill Creator：用知识库和历史工单生成初始技能。
- Failure Analyzer：批量分析执行失败。
- Skill Diagnostician：定位 skill 缺陷。
- Skill Optimizer：改写 skill。

Simple Hermes 当前的 `validate_deliverable`、artifact manifest、test trace 已经有失败证据链雏形。下一步应把它们接到 skill repair：

- 当某类任务连续失败，自动生成 `skill_candidate.md`。
- 当使用某个 skill 后测试失败，创建 `skill_patch_proposal.md`。
- skill patch 必须引用 trace/test/artifact 证据。

参考：

- https://huggingface.co/papers/2604.04804
- https://papers.cool/arxiv/2604.04804
- https://papers.cool/arxiv/2604.08618

## 5. UI-Voyager / HyEvo：从失败轨迹到 workflow 演化

UI-Voyager 代表“失败经验驱动”的路线：不是丢弃失败轨迹，而是把它转成可学习信号。GUI 场景中的失败步骤、错误动作、状态偏移都可以成为下一轮策略改进材料。

HyEvo 代表 workflow 搜索路线：不是只优化 prompt，而是搜索混合 agentic workflow。它把 LLM 节点和 deterministic code 节点混合，用 execution feedback 迭代 topology 和 node logic。

对 Simple Hermes 的现实借鉴：

- 保存失败 trace，并生成 failure card。
- 将复杂任务分解成可复用 workflow spec。
- 对 workflow spec 做轻量 A/B：同一 benchmark 用两个策略跑，比较通过率和工具步数。

参考：

- https://gist.science/nl/paper/2603.24533
- https://juriscreators.com/article/articles/hyevo-self-evolving-hybrid-agentic-workflows-for-efficient-reasoning/

## 6. SEVerA / SEA-Eval / Zombie Agents：治理、评测和安全

自进化最容易变成“越改越坏”。近期工作已经从能力提升转向治理：

- SEVerA：把形式化 contract 加进 self-evolving agent，要求生成结果满足硬约束。
- SEA-Eval：不只看单任务成功率，而是看顺序任务流中的长期演化增益、结构稳定性和 token consumption。
- Zombie Agents：指出长期 memory/skill 会把一次性间接 prompt injection 变成跨 session 持久攻击。

对 Simple Hermes 的直接结论：

- 任何自动 skill/memory 写入都必须有 validator。
- skill 更新不应直接覆盖稳定版本。
- 长期 benchmark 要记录成功率、工具步数、token/step 消耗、失败类型分布。
- 来自 web/search/raw artifact 的内容不能直接进入长期 memory/skill；必须带来源、风险标记和人工/测试验证。

参考：

- https://huggingface.co/papers/2603.25111
- https://papers.cool/arxiv/2603.25111
- https://papers.cool/arxiv/2604.08988

## 对 Hermes “self-improving” 叙事的判断

Hermes 当前宣传中的 “built-in learning loop / self-improving / skills self-improve” 更接近 Memento-Skills / SkillClaw 这类 skill-memory 演化叙事，而不是 AgentEvolver 那种完整训练引擎。

从代码功能看，Hermes/Simple Hermes 已有：

- 持久 memory。
- session recall。
- skill 文件创建和更新。
- trajectory/test/artifact 记录。

但距离严格意义的 Evolver 还缺：

- 自动任务生成。
- 经验池和检索式 skill induction。
- 失败归因。
- skill 候选验证和发布。
- 顺序任务流评测。
- 防记忆污染/skill 污染的安全机制。

因此更准确的表述应是：

> Hermes/Simple Hermes 具备 self-improving agent 的若干工程基座，但还不是完整自进化引擎。下一步应优先实现“trace -> failure card -> skill candidate -> validation -> promotion”这条最小闭环。
