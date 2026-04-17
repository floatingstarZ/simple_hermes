# Agent Handoff

更新时间：2026-04-17

这份文件给下一个接手 `simple_hermes_codex` / Simple Hermes 工作的 Agent 使用。当前可读工作目录是：

```text
/Users/hzy/simple_hermes_selfevo_work
```

原先用户提到的目录是：

```text
/Users/hzy/Desktop/work/simple_hermes_codex
```

但在本轮会话中该目录内容读取出现 `Operation not permitted`，所以我从远端克隆到了 `/Users/hzy/simple_hermes_selfevo_work` 继续工作。这个克隆已推送到同一个远端分支。

## Git 状态

- Remote: `https://github.com/floatingstarZ/simple_hermes.git`
- Branch: `20260416.1-self-evolution-survey`
- 写入本 handoff 前的功能基线 commit: `19dcdde design self evolution experiments`
- 已推送到 origin。
- 上游分支状态在最后一次检查时是干净同步：

```text
20260416.1-self-evolution-survey...origin/20260416.1-self-evolution-survey
```

最近关键功能 commits：

```text
19dcdde design self evolution experiments
9ac459d add local self evolution experiment loop
8a62d46 add self evolution survey workflow
```

## 用户目标

用户希望 Simple Hermes 具备“自我进化”能力，并要求先设计实验。这里的自我进化不是 RL 训练，也不是让 agent 自动改自己代码；当前定义为一个可审计的本地工程闭环：

```text
任务执行
  -> 失败/成功证据
  -> experience card
  -> self_evolve 归纳候选 skill
  -> 验证门
  -> 显式 promote
  -> 后续同类任务表现对比
```

重要边界：

- 不自动把经验写入稳定长期 skill。
- `self_evolve run` 只生成候选并记录验证，不直接覆盖 stable skill。
- `skills promote <candidate-id>` 仍然是显式动作。
- 不写入或上传 API key、token、cookie、auth profile。
- 实验产物目录已加入 `.gitignore`，不要把运行产物误提交。

## 已实现能力

### 1. Experience Card

文件：`simple_hermes/tools/builtin.py`

已有工具：

```text
experience record/list/view/summarize
```

经验卡保存在：

```text
.simple_hermes/evolution/<session-hash>/experience.jsonl
```

`run_tests` 失败会自动记录：

- `test_failure`
- `zero_tests`

`validate_deliverable` 失败会自动记录：

- `deliverable_validation_failed`
- `placeholder_deliverable`

### 2. Skill Candidate

已有工具：

```text
skills propose/candidates/view-candidate/promote
```

候选 skill 默认写到用户级 candidate 目录，稳定 skill 和候选区隔离。`skills promote` 会拒绝明显未完成内容，例如 TODO、占位符、空 arXiv 链接等。

### 3. self_evolve 工具

文件：`simple_hermes/tools/builtin.py`

核心入口：

```text
self_evolve status
self_evolve propose [name=N] [failure_type=T] [min_count=N]
self_evolve validate <candidate-id> [command='discover -s tests -v']
self_evolve run [name=N] [failure_type=T] [min_count=N] [command='discover -s tests -v']
```

实现要点：

- 从当前 session 的 experience cards 读取同类经验。
- 如果没有指定 `failure_type`，默认选择数量最多的 failure type。
- 生成结构化 Markdown skill candidate，包含：
  - `## When to Use`
  - `## Recovery Workflow`
  - `## Evidence`
  - `## Validation`
- quality gate 会拒绝：
  - 占位符/未完成 marker
  - 缺少核心 heading
  - 内容过短
- `validate` 可执行 `run_tests` 命令并把 validation 记录写回 candidate metadata。

Agent 显式命令解析已经支持 `self_evolve`：

- `simple_hermes/agent/core.py`
- `tests/test_agent.py`

### 4. 离线确定性实验脚本

文件：

```text
scripts/run_self_evolution_experiment.py
```

运行：

```bash
python3 scripts/run_self_evolution_experiment.py
```

行为：

1. 创建隔离 demo workspace。
2. 写入两张 `test_failure` experience card。
3. 调用 `self_evolve run` 生成 `test-failure-recovery` candidate。
4. 用 demo 项目的 unittest 做 validation gate。
5. 输出 `summary.json`。

一次已跑通的结果：

```json
{
  "candidate_id": "cand-20260417-013426-604b32-test-failure-recovery",
  "experience_cards": 2,
  "validated_candidates": 1,
  "promoted": false
}
```

该实验输出在 ignored 目录：

```text
self_evolution_runs/20260417-093426/summary.json
```

## 实验设计

用户后续要求“先设计实验”，已经新增：

```text
Survey/self_evolution_experiment_design.md
```

并更新索引：

```text
README.md
Survey/README.md
```

实验设计分三层：

### E0：离线确定性闭环

不依赖 LLM、不依赖网络、不依赖 API key。验证工具链本身：

```text
experience -> self_evolve -> candidate -> validate
```

### E1：真实 agent benchmark A/B 对照

用现有 `benchmarks/code_agent_tasks.json` 中的任务对比：

- A0 baseline：无自进化 skill。
- B0 candidate-only：只生成候选，不 promote。
- C0 promoted：生成、验证并 promote。
- D0 negative-gate：坏 candidate 验证门拒绝测试。

主要指标：

- `pass_rate`
- `step_count`
- `repeated_inspection_blocks`
- `experience_cards_created`
- `skill_used`
- `duration_seconds`

### E2：顺序任务流自进化

按任务流观察是否“越做越会做”：

```text
todo-active-items
  -> invoice-discount
  -> coin-catcher-score
```

如果 Python 任务收益明显但 JavaScript 任务无收益，结论必须限定，不要夸大为通用自进化。

## 验证结果

最近一次完整单元测试：

```bash
python3 -m unittest discover -s tests -v
```

结果：

```text
Ran 189 tests in 3.496s
OK
```

离线自进化实验：

```bash
python3 scripts/run_self_evolution_experiment.py
```

结果已通过，见上面的 `candidate_id` 示例。

敏感 token 形态扫描没有命中。扫描命令：

```bash
rg -n -S '(sk-[A-Za-z0-9_-]{12,}|sk-proj-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})' . \
  -g '!self_evolution_runs/**' \
  -g '!benchmark_runs/**' \
  -g '!test_results/**' \
  -g '!traces/**' \
  -g '!.git/**'
```

## 当前建议下一步

不要急着扩大 agent 能力。先按实验设计把实验工具化：

1. 给 `scripts/run_self_evolution_experiment.py` 增加：

```text
--group baseline|candidate|promoted|negative-gate
```

2. 增加分析脚本：

```text
scripts/analyze_evolution_runs.py
```

用于读取 `self_evolution_runs/` 和 `benchmark_runs/`，输出对照表。

3. 让 benchmark runner 支持显式注入隔离 skill/memory 目录，方便 A/B 实验：

```text
SIMPLE_HERMES_SKILLS_DIR
SIMPLE_HERMES_SKILL_CANDIDATES_DIR
```

4. 先跑 E0 + D0，确认正向闭环和负向验证门。
5. 再跑 E1 的 A0/C0，各 3 轮。
6. 只有 E1 有稳定正向信号后，再做 E2 长期顺序流。

## 注意事项

- 用户关心“自我进化详细流程”和实验可信度，不要只做 demo。
- 不要把自进化描述成模型训练或 RL，目前只是本地经验/skill 工程闭环。
- 不要上传实验产物、trace、benchmark run 目录。
- 提交前再次运行 token 扫描。
- 如果需要真实 agent benchmark，可能需要本地代理和 Hermes runtime backend；先固定 backend/model，否则 A/B 结果不可比。
- 当前分支已经包含先前 PPT/文档相关成果，但这次 handoff 重点是 Simple Hermes 自进化实验。
