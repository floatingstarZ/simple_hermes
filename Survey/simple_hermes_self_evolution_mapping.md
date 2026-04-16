# Simple Hermes 自进化能力映射

更新时间：2026-04-16

## 当前已有功能

Simple Hermes 已经具备自进化系统的底座，并加入了一个最小本地闭环：失败信号会保存成 experience card，experience card 可以沉淀为候选 skill，候选 skill 需要显式 promote 才能进入稳定 skill。

| 能力块 | 当前实现 | 文件/工具 | 自进化意义 |
| --- | --- | --- | --- |
| 长期记忆 | general memory / user memory | `remember`, `remember_user`, `MemoryStore` | 可保存用户偏好和稳定事实 |
| 会话历史 | SQLite session、lineage、recall、recall_all | `SessionStore`, `history`, `recall` | 可跨 session 回看经验 |
| 上下文压缩 | continuation session + handoff summary | `SimpleAgent` compression | 长任务可延续 |
| 任务 ledger | session-scoped todo list | `todo` | 复杂任务状态可恢复 |
| skill 雏形 | Markdown skill create/view/use/list/delete/propose/promote | `skills` | procedural memory 的本地载体，候选区和稳定区隔离 |
| experience card | 项目内 JSONL 经验池 | `experience`, `.simple_hermes/evolution/` | 保存失败类型、证据、lesson 和 artifact 引用 |
| artifact manifest | 扫描 raw/artifacts/output/logs | `artifact` | 将工具输出变成可引用证据 |
| deliverable validation | Markdown/JSON 结构校验和恢复建议 | `validate_deliverable` | 给自我修复提供明确失败信号 |
| code benchmark | fixture + runner + HTML report | `benchmarks/`, `scripts/run_code_agent_benchmark.py` | 可以做演化前后对比 |
| 后台任务 | start/list/status/tail/wait/stop | `background` | 支持长时间采集和实验 |
| delegation | child sessions / parallel delegation | `delegate`, `parallel_delegate` | 可将探索、验证、综合分工 |

## 和自进化框架的差距

| 目标机制 | AgentEvolver / SkillClaw 等做法 | Simple Hermes 当前状态 | 缺口 |
| --- | --- | --- | --- |
| 自动任务生成 | self-questioning / task manager | 只有手写 benchmark | 缺少从历史失败生成测试任务 |
| 经验池 | experience manager / vector store / shared storage | project-local JSONL experience cards | 还缺少跨项目检索和相似失败聚合 |
| 失败归因 | self-attributing / Failure Analyzer | 有 test output 和 validation failure | 缺少失败类型分类和责任定位 |
| skill induction | trajectory -> skill KB | `skills propose` 候选区 | 还缺少从 trace 自动生成候选文本 |
| skill refinement | execution feedback -> skill rewrite | 手动 patch | 缺少 outcome log 和 patch proposal |
| skill promotion | validated publish mode | 候选区 + placeholder gate + 显式 promote | 还缺少 benchmark gate 和 rollback |
| 长期评测 | sequential stream, token/step trend | 单次 benchmark 为主 | 缺少跨轮次 evolution metrics |
| 安全治理 | contracts / verified fallback / memory isolation | credential audit + path protection | 缺少 memory/skill 写入审计 |

## 最小可实现闭环

建议不要直接做“大 Evolver”。Simple Hermes 的下一步应是一个小而硬的闭环：

```text
benchmark/task run
  -> trace + test/log/artifact
  -> failure card
  -> skill candidate or skill patch proposal
  -> validation task
  -> promote to stable skill if passed
```

这个闭环对应 SkillForge/SkillClaw 的最小本地版本，不需要 RL，不需要远程 shared storage，也不需要向量数据库。

## 建议新增模块

### 1. Experience Card

新增一个工具或内部 helper，把一次任务结果保存成结构化卡片：

```json
{
  "task_id": "dailytrack-20260416",
  "goal": "完成 daily track",
  "status": "failed",
  "files_touched": ["track.md", "papers.json"],
  "tools_used": ["fetch_url", "artifact", "write_file", "validate_deliverable"],
  "failure_type": "placeholder_deliverable",
  "evidence": ["validate_deliverable failed: placeholder arXiv id"],
  "lesson": "不要在 papers.json 中写 2604.xxxxx，占位符必须从 raw artifacts 中重建"
}
```

落地位置：

- `.simple_hermes/experience/<session-id>/<timestamp>.json`
- 或 project-local `.simple_hermes_experience/`

### 2. Skill Candidate

新增 `skills propose <name> ::: <reason/evidence/body>`，写入候选区而非稳定区：

```text
~/.simple_hermes_codex/skill_candidates/<name>/<timestamp>/SKILL.md
~/.simple_hermes_codex/skill_candidates/<name>/<timestamp>/evidence.json
```

候选 skill 必须包含：

- trigger conditions
- exact workflow
- pitfalls
- validation steps
- evidence links to traces/tests/artifacts

### 3. Skill Outcome Log

每次 `skills use <name>` 后，记录 outcome：

```json
{
  "skill": "daily-track",
  "session_id": "...",
  "task": "...",
  "outcome": "passed|failed|unknown",
  "validation": "...",
  "timestamp": 1770000000
}
```

用途：

- 找出 stale skill。
- 当同一 skill 连续失败时触发 patch proposal。
- 为 benchmark 统计 skill 是否真的降低 step count。

### 4. Local Promotion Gate

新增 `skills promote <candidate-id>` 或脚本化 promotion：

1. 运行指定 benchmark/task。
2. 检查 deliverable validation。
3. 检查没有 placeholder、没有敏感信息。
4. 生成 diff。
5. 通过后写入 stable skill。

不建议第一版自动覆盖 stable skill。先保留人工确认或显式命令。

### 5. Evolution Benchmark

基于现有 `benchmarks/code_agent_tasks.json`，新增长期指标：

- success rate
- average steps
- repeated tool-call blocks
- validation failures
- number of generated experience cards
- skill candidate accepted/rejected

更接近 SEA-Eval：同一类任务按顺序流执行，看 agent 是否越跑越省步骤、越少重复失败。

## 功能优先级

### P0：先做可观测性

- 保存 `experience card`。
- benchmark 报告里展示 failure type。
- 将 validation failure、test failure、background refusal、backend retry 写入结构化事件。

原因：没有结构化经验，就谈不上自进化。

### P1：做 skill candidate，不直接自动改 stable skill

- 从经验卡生成候选 skill。
- 候选区独立存储。
- 支持人工查看、diff 和 promote。

原因：避免 Zombie Agents 式长期污染，也避免坏 skill 覆盖好 skill。

### P2：做 outcome-driven skill patch

- 记录 `skills use` 的 outcome。
- 同一 skill 多次失败后生成 patch proposal。
- patch proposal 必须引用失败证据。

原因：这对应 SkillForge 的 Failure Analyzer -> Skill Diagnostician -> Skill Optimizer。

### P3：再考虑自动任务生成

- 从历史 failure card 生成 regression benchmark。
- 从成功 experience card 生成 transfer task。

原因：这才对应 AgentEvolver 的 self-questioning，但应放在可观测性和验证之后。

## 不建议现在做

- 不建议接 RL/GRPO。Simple Hermes 当前目标是 code-agent harness，不是训练平台。
- 不建议直接引入向量数据库。SQLite FTS 或简单 JSONL 已足够。
- 不建议自动写入长期 memory/skill 而不经 validation。
- 不建议把每次失败都生成 skill；先聚合相似失败，避免 skill 垃圾化。

## 已落地的第一步

1. 增加 `experience` 工具：`experience record/list/view/summarize`。
2. `validate_deliverable` 失败自动记录 `deliverable_validation_failed` 或 `placeholder_deliverable`。
3. `run_tests` 失败自动记录 `test_failure` 或 `zero_tests`。
4. 增加 `skills propose/list-candidates/view-candidate/promote`。
5. promotion 前检查 TODO、占位符、空 arXiv 链接等明显未完成内容。

## 推荐下一步实现顺序

1. 让 benchmark runner 读取 trace，自动生成 experience cards。
2. 增加 skill outcome log，记录 `skills use` 后的成功/失败。
3. 对更多工具失败建立 failure taxonomy，例如 background refusal、backend retry、artifact 缺失。
4. 加 benchmark promotion gate，通过指定回归任务后再允许 promote。
5. 在 HTML test/benchmark report 中展示 evolution timeline。

这一条线做完后，Simple Hermes 才能从“能记忆/能写 skill”升级为“能从失败和成功中积累可验证经验”。
