# Daily Track - 2026-04-16（北京时间）

> 主题：LLM RL 算法 / 具身模型 RL / RL 训练框架 / Agent / Harness  
> 今日重心：**self-evolve 继续增强**，但形态更具体：spatial self-evolution、coding-agent memory transfer、agent exploration/exploitation diagnosis、skills compilation、GUI agent policy optimization，以及 Claude Opus 4.7 / OpenAI Agents SDK 把 long-running agent harness 推到产品层。

---

## 零、今日最重要判断

| 方向 | 代表信号 | 判断 |
|------|----------|------|
| **Self-evolve 从文本反思转向环境闭环** | SpatialEvo / Memory Transfer Learning / TREX / Self-Distillation Zero | 自我进化不再只是写总结，而是在 deterministic environment、memory transfer、tree exploration、self-revision 里形成可训练闭环 |
| **Agent benchmark 继续真实化** | GameWorld / OccuBench / MERRIN / InfiniteScienceGym | 评测对象从静态问答转向游戏、职业任务、noisy web evidence、程序生成科学分析 |
| **GUI agent 进入 RL + grounding 双线** | UI-Zoomer / UI-Copilot / Mobile GUI Threats | GUI 方向一边补定位框和 zoom/refine，一边把 long-horizon automation 推向 tool-integrated policy optimization |
| **Harness 产品化继续加速** | Anthropic Claude Opus 4.7 / OpenAI Agents SDK / SemaClaw / Sema Code | 模型、SDK、文件系统 memory、sandbox、task budgets、auto mode、review command 正在合并成 agent 操作系统雏形 |
| **LLM RL 小周期仍在 OPD / token-level signal / policy objective** | RationalRewards / From P(y\|x) to P(y) / TPO / TIP / Self-Distillation Zero | RL 主线没有停，近期重点是 reward signal、pre-train space、token importance、binary reward densification |

---

## 一、HuggingFace Daily Papers

> HF Daily Papers 2026-04-16 共抓到 33 篇，本次精选 25 篇进入 `papers.json`。

### Self-Evolve / Agent Memory / Harness

| 论文 | Upvotes | 关键贡献 | 链接 |
|------|---------|----------|------|
| **SpatialEvo: Self-Evolving Spatial Intelligence via Deterministic Geometric Environments** | **60↑** | 今天最贴近 self-evolve 主线：用 deterministic geometric environments 构造可验证、可反复迭代的 spatial intelligence 训练场。重点不是“模型反思”，而是环境本身提供可复现的进化压力。 | [2604.14144](https://arxiv.org/abs/2604.14144) |
| **Memory Transfer Learning: How Memories are Transferred Across Domains in Coding Agents** | **24↑** | 直接研究 coding agent 的 memory 如何跨 domain 迁移。它补的是 self-evolving coding agent 的关键缺口：经验是否能从一个 repo / task 泛化到另一个 repo / task。 | [2604.14004](https://arxiv.org/abs/2604.14004) |
| **Exploration and Exploitation Errors Are Measurable for Language Model Agents** | **22↑** | 把 LM agent 的 exploration / exploitation error 显式量化。对 agentic RL 很关键，因为自我进化失败经常不是能力不够，而是该探索时没探索、该复用时乱探索。 | [2604.13151](https://arxiv.org/abs/2604.13151) |
| **Sema Code: Decoupling AI Coding Agents into Programmable, Embeddable Infrastructure** | **18↑** | 把 coding agent 拆成可编程、可嵌入基础设施，继续说明 coding agent 的竞争点正在从模型本身转向 harness / runtime / embeddable interface。 | [2604.11045](https://arxiv.org/abs/2604.11045) |
| **SemaClaw: A Step Towards General-Purpose Personal AI Agents through Harness Engineering** | **15↑** | 直接把 personal agent 和 harness engineering 绑定，是 OpenClaw / Claude Code / Codex / DeerFlow 这条线的学术化信号。 | [2604.11548](https://arxiv.org/abs/2604.11548) |
| **TREX: Automating LLM Fine-tuning via Agent-Driven Tree-based Exploration** | **9↑** | 用 agent-driven tree exploration 自动化 fine-tuning。它和 autoresearch / AiScientist 的区别是更聚焦训练流程搜索，适合作为 autonomous post-training engineering 方向观察。 | [2604.14116](https://arxiv.org/abs/2604.14116) |
| **SkVM: Compiling Skills for Efficient Execution Everywhere** | **4↑** | 把 skills 编译成更高效的可执行形态。对 self-evolving agent 来说，skill 不应该永远是 prompt 文本，而应该能被压缩、编译、部署、复用。 | [2604.03088](https://arxiv.org/abs/2604.03088) |
| **Self-Sovereign Agent** | **1↑** | 偏治理/身份视角：持久 agent 如果要自主演化，必须有身份、边界、权限和所有权模型。不是算法核心，但对长期 agent 产品化重要。 | [2604.08551](https://arxiv.org/abs/2604.08551) |

### Agent / GUI / Benchmark

| 论文 | Upvotes | 关键贡献 | 链接 |
|------|---------|----------|------|
| **GameWorld: Towards Standardized and Verifiable Evaluation of Multimodal Game Agents** | **105↑** | 视频游戏作为标准化、可验证的多模态 agent 评测环境。游戏天然有闭环状态、稀疏反馈、可验证目标和不可逆错误，很适合作为 embodied/generalist agent 中间层。 | [2604.07429](https://arxiv.org/abs/2604.07429) |
| **OccuBench: Evaluating AI Agents on Real-World Professional Tasks via Language World Models** | **46↑** | 用 language world models 评估真实职业任务，把 agent benchmark 从“能不能点按钮/答题”推向 occupation-level professional workflows。 | [2604.10866](https://arxiv.org/abs/2604.10866) |
| **UI-Zoomer: Uncertainty-Driven Adaptive Zoom-In for GUI Grounding** | **9↑** | 和前期 GUI grounding 专题结论一致：最强 pipeline 不是直接吐 bbox，而是 uncertainty -> zoom/refine -> grounded action。 | [2604.14113](https://arxiv.org/abs/2604.14113) |
| **UI-Copilot: Advancing Long-Horizon GUI Automation via Tool-Integrated Policy Optimization** | **4↑** | GUI agent 从 grounding/benchmark 继续走向 policy optimization。重点是 long-horizon GUI automation，不只是单步点击定位。 | [2604.13822](https://arxiv.org/abs/2604.13822) |
| **MERRIN: A Benchmark for Multimodal Evidence Retrieval and Reasoning in Noisy Web Environments** | **5↑** | 面向 noisy web 的多模态证据检索和推理，适合 deep research / browser agent / evidence chain 方向。 | [2604.13418](https://arxiv.org/abs/2604.13418) |
| **Mobile GUI Agents under Real-world Threats: Are We There Yet?** | **2↑** | 移动 GUI agent 安全评测，补齐真实威胁下的 computer-use agent 风险。和 Blind Spot of Agent Safety / instruction hierarchy 同线。 | [2507.04227](https://arxiv.org/abs/2507.04227) |
| **Do AI Coding Agents Log Like Humans? An Empirical Study** | **2↑** | 从日志行为看 coding agents。它和 CodeTracer 形成邻近线：agent observability 不只看结果，还要看中间记录是否支持 debug 和责任归因。 | [2604.09409](https://arxiv.org/abs/2604.09409) |
| **InfiniteScienceGym: An Unbounded, Procedurally-Generated Benchmark for Scientific Analysis** | **1↑** | 程序生成科学分析 benchmark，为 autonomous science agent 提供近似无限任务源。和 Physics Simulator RL / AiScientist 同向。 | [2604.13201](https://arxiv.org/abs/2604.13201) |

### LLM RL / OPD / Reward

| 论文 | Upvotes | 关键贡献 | 链接 |
|------|---------|----------|------|
| **RationalRewards: Reasoning Rewards Scale Visual Generation Both Training and Test Time** | **95↑** | 用 reasoning rewards 同时提升视觉生成训练和 test-time scaling，是 RL/reward 从文本推理向视觉生成迁移的强信号。 | [2604.11626](https://arxiv.org/abs/2604.11626) |
| **From P(y\|x) to P(y): Investigating Reinforcement Learning in Pre-train Space** | **23↑** | 研究 RL 在 pre-train space 中的作用，属于近期“RL 到底改了什么”的机制分析。 | [2604.14142](https://arxiv.org/abs/2604.14142) |
| **Target Policy Optimization** | **19↑** | 新 policy optimization 候选。需要后续看是否和 GRPO/PPO/OPD 形成可复用训练优势。 | [2604.06159](https://arxiv.org/abs/2604.06159) |
| **TIP: Token Importance in On-Policy Distillation** | **10↑** | OPD 进入 token-level signal 分析阶段。核心问题是 teacher/student 信号不是每个 token 都等价，应该按重要性使用。 | [2604.14084](https://arxiv.org/abs/2604.14084) |
| **Self-Distillation Zero: Self-Revision Turns Binary Rewards into Dense Supervision** | **5↑** | 把 binary reward 经 self-revision 变成 dense supervision，连接 RLVR 稀疏奖励和自修正训练。 | [2604.12002](https://arxiv.org/abs/2604.12002) |
| **What do Language Models Learn and When? The Implicit Curriculum Hypothesis** | **1↑** | 训练机制分析。对 RL/data curriculum 有背景价值：模型不是均匀学习所有能力，而存在隐式课程。 | [2604.08510](https://arxiv.org/abs/2604.08510) |

### World Model / 3D / Diffusion

| 论文 | Upvotes | 关键贡献 | 链接 |
|------|---------|----------|------|
| **Seedance 2.0: Advancing Video Generation for World Complexity** | **112↑** | 今日 HF 最高热度。原生多模态音视频生成，支持 text/image/audio/video 多输入和 480p/720p 输出，是生成式 world complexity 的产业级信号。 | [2604.14148](https://arxiv.org/abs/2604.14148) |
| **Free Geometry: Refining 3D Reconstruction from Longer Versions of Itself** | **14↑** | 用更长版本自身 refine 3D reconstruction，属于 3D/world model 的 self-refinement 思路。 | [2604.14048](https://arxiv.org/abs/2604.14048) |
| **LangFlow: Continuous Diffusion Rivals Discrete in Language Modeling** | **12↑** | 连续扩散语言模型对标离散 LM，延续 dLLM / diffusion LLM 方向。 | [2604.11748](https://arxiv.org/abs/2604.11748) |

---

## 二、GitHub Watchlist

> 说明：GitHub 状态按 2026-04-17 16:5x 左右采集。部分 latest commit 已进入 04-17 白天，仅作为采集时状态，用来刷新 watchlist 工程脉搏。

| 框架 | Stars | 今日动态 |
|------|-------|----------|
| **karpathy/autoresearch** | **73,612** | 主仓代码仍停在 03-26，但社区热度继续上升；和 TREX / InfiniteScienceGym / Memory Transfer Learning 共同维持 autonomous research engineering 叙事 |
| **verl-project/verl** | **20,754** | `[rollout] fix: RM sleep/wake teacher replicas`，rollout / RM 基础设施仍在高频迭代 |
| **huggingface/trl** | **18,076** | 04-17 移除 experimental trainers 的 dead token attributes，agent/tool 训练细节继续修正 |
| **OpenRLHF/OpenRLHF** | **9,365** | 04-17 `update`，保持稳定快速迭代 |
| **THUDM/slime** | **5,347** | 04-16 修复 eval sample logging(list sample)；stars 持续上涨 |
| **inclusionAI/AReaL** | **5,051** | 04-17 新增 MoE LoRA 支持（single node / cross node），框架继续向更复杂模型后训练扩展 |
| **hiyouga/EasyR1** | **4,860** | stars 继续涨，但近几日主线提交不强；仍作为多模态 RL watchlist 保留 |
| **RLinf/RLinf** | **3,126** | `restore FSDP actor offload state`，具身 / agent RL infra 继续修训练稳定性 |
| **NVIDIA-NeMo/Gym** | **832** | `remove mini-swe dummy resources server`，说明 LLM RL environment 仍在补基准与环境工程 |
| **bytedance/deer-flow** | **62,154** | `Memory update system has cache corruption, data loss, and thread-safety bugs` 修复，长时程 harness 的 memory correctness 开始进入基础设施级攻坚 |
| **modelscope/AgentEvolver** | **1,396** | 自进化 agent watchlist；近期仍以文档/整理为主 |
| **JudgmentLabs/judgeval** | **1,020** | 04-09 release merge；继续代表 agent post-building / RL-SFT post-training 层 |

### GitHub Top / Trending 额外关注

| 项目 | Stars | 为什么关注 |
|------|-------|------------|
| **deer-flow** | **62,154** | 仍是最重要的开源 long-horizon SuperAgent harness；memory / subagents / sandbox / skills / gateway 一体化 |
| **aiming-lab/MetaClaw** | **热度上升中** | GitHub 搜索里最贴近今天 self-evolve / harness 主线：强调“Just talk to your agent — it learns and EVOLVES” |
| **JudgmentLabs/judgeval** | **1,020** | 把环境数据、评测和 agent post-training 绑定，和今天 GameWorld / OccuBench / MERRIN 的 benchmark 叙事一致 |
| **facebookresearch/meta-agents-research-environments** | **新进入搜索结果** | 强调 dynamic, realistic scenarios 的 agent environments，说明真实环境 benchmark 继续受关注 |
| **ragflow / OpenHands / deer-flow** | **头部稳定** | trending agent 生态继续由 context layer、software agents、long-horizon harness 三类系统主导 |

---

## 三、HuggingFace Hub

| 模型/发布 | Likes | 亮点 |
|----------|-------|------|
| **MiniMaxAI/MiniMax-M2.7** | **884** | text-generation，仍居 trending 前列 |
| **tencent/HY-Embodied-0.5** | **772** | embodied VLM，和具身/VLA 方向强相关 |
| **Qwen/Qwen3.6-35B-A3B** | **454** | image-text-to-text，继续作为多模态模型供给侧信号 |
| **zai-org/GLM-5.1** | **1,290** | text-generation，04-16 仍更新 |
| **baidu/ERNIE-Image / Turbo** | **378 / 262** | text-to-image，04-16 更新，图像生成基础模型热度继续 |
| **tencent/HY-World-2.0** | **163** | image-to-3d，和 Seedance / Free Geometry / world model 方向一致 |
| **openbmb/VoxCPM2** | **941** | TTS/audio agent 邻近方向，04-16 更新 |

---

## 四、产业前沿 / 官方博客

| 来源 | 日期 | 标题 | 意义 |
|------|------|------|------|
| **Anthropic News** | 2026-04-16 | **Introducing Claude Opus 4.7** | 今天最重要的产业信号。官方把 coding、agents、vision、long-running tasks、self-verification 放到同一代模型叙事里，并强调 cyber safeguards / verification program，说明 frontier coding agent 正在进入“强能力 + 强防护”并推的阶段。 |
| **Anthropic Engineering** | 2026-04-14 左右 | **Quantifying infrastructure noise in agentic coding evals** | 直接指出 sandbox / resource headroom / enforcement policy 能让 Terminal-Bench 2.0 分数波动 6 个点，甚至超过模型间 leaderboard gap；对你关心的 harness / eval 工程尤其重要。 |
| **Anthropic Engineering** | 2026-04-14 左右 | **Scaling Managed Agents: Decoupling the brain from the hands** | 把 Managed Agents 明确抽象为 session / harness / sandbox 三层可替换接口，和 DeerFlow、OpenAI Agents SDK 一起说明托管 harness 正在产品化。 |
| **Anthropic Research** | 2026-04-14 | **Automated Alignment Researchers** | 让多份 Claude Opus 4.6 副本在 sandbox / shared forum / code store / remote evaluator 中自发提出并测试 scalable oversight 想法，是 autonomous research × alignment 的高信号样本。 |
| **OpenAI News** | 2026-04-16 | **Codex for (almost) everything** | 继续把 Codex 从 coding assistant 推向更广的 agentic execution surface；和 Anthropic Opus 4.7 一起说明“长时程软件代理”已成模型厂商主战场。 |
| **OpenAI News** | 2026-04-16 | **Accelerating the cyber defense ecosystem that protects us all** | agentic coding / cyber capability 继续被纳入可信访问与防护叙事，和 Anthropic 的 cyber safeguards 形成对应。 |
| **Hugging Face Blog** | 2026-04-16 | **The PR you would have opened yourself** | 直接命中 coding agent 工作流，把自动改动提炼成更接近人类 reviewer / author 期望的 PR 形态，属于 agent ergonomics / workflow infra 信号。 |

官方链接：

- [Anthropic: Introducing Claude Opus 4.7](https://www.anthropic.com/news/claude-opus-4-7)
- [Anthropic Engineering: Quantifying infrastructure noise in agentic coding evals](https://www.anthropic.com/engineering/infrastructure-noise)
- [Anthropic Engineering: Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents)
- [Anthropic Research: Automated Alignment Researchers](https://www.anthropic.com/research/automated-alignment-researchers)
- [OpenAI: Codex for (almost) everything](https://openai.com/)

---

## 综合总结

- **#1 self-evolve 今天最强信号是 SpatialEvo + Memory Transfer Learning**：一个把自我进化放进 deterministic spatial environments，一个研究 coding agent memory 跨域迁移，说明“进化”正在变成可测、可迁移、可复用的系统问题。
- **#2 GUI agent 继续沿两条线推进**：UI-Zoomer 解决 grounding 中的不确定性定位，UI-Copilot 把长时程 GUI automation 接到 policy optimization；这和之前 proposal + zoom/refine + verifier 的判断一致。
- **#3 Agent benchmark 明显转向高真实性和可验证性**：GameWorld、OccuBench、MERRIN、InfiniteScienceGym 都不满足于小任务静态成功率，而是在模拟真实环境、职业工作、noisy web 和科学分析。
- **#4 Anthropic Opus 4.7 是今天产业侧最大事件**：特别是文件系统 memory、task budgets、long-running coding workflows、auto mode 和 `/ultrareview`，这些都和你关注的主动式 agent / harness / self-evolve 直接相关。
- **#5 对你的研究方向启发**：`Budgeted Runtime Adaptation` 可以更明确地定义为“在长任务中预算 context、memory transfer、zoom/refine、tool policy optimization、verification、skill compilation 和 task budgets”，并用 RL 学习什么时候触发这些操作。

## 附：数据文件

- `papers.json` - 本日精选 25 篇
