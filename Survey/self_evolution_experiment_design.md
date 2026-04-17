# Simple Hermes 自进化实验设计

更新时间：2026-04-17

## 目标

验证 Simple Hermes 的“本地自进化”是否真的带来可观测改进，而不是只把失败日志写成更多文件。

本实验不验证训练式 RL，也不验证模型能力本身。实验对象是工程闭环：

```text
任务执行
  -> 失败/成功证据
  -> experience card
  -> self_evolve 归纳候选 skill
  -> 验证门
  -> 显式 promote
  -> 后续同类任务表现对比
```

核心问题：

1. experience card 是否能稳定捕获可复用失败模式。
2. `self_evolve` 生成的候选 skill 是否结构完整、可验证、不会污染稳定 skill。
3. promote 后，agent 在同类任务上是否减少重复失败、减少检查步数、提高通过率。
4. 安全边界是否成立：实验不写入 API key，不自动覆盖稳定 skill，不把无验证经验注入长期能力。

## 实验假设

| 编号 | 假设 | 可观测判定 |
| --- | --- | --- |
| H1 | 自进化闭环能从重复失败中生成合格候选 skill | 候选 skill 包含触发条件、恢复流程、证据、验证步骤；quality gate 通过 |
| H2 | 验证门能挡住无效候选 | 占位符、过短内容、缺少核心 heading、失败测试会导致 validation failed |
| H3 | 使用已验证 skill 后，同类 benchmark 的重复失败下降 | 同一 failure_type 的 experience card 数下降，最终测试通过率不下降 |
| H4 | 使用已验证 skill 后，执行效率提升 | 平均 step 数、重复 read/search/tool-call 次数下降 |
| H5 | 自进化不会引入敏感信息或长期污染 | token 扫描无命中；未显式 promote 时 stable skills 不变 |

## 实验分层

### E0：离线确定性闭环

目的：验证工具链本身，不依赖 LLM、不依赖网络、不依赖 API key。

已有脚本：

```bash
python3 scripts/run_self_evolution_experiment.py
```

流程：

1. 创建隔离 workspace。
2. 写入 demo Python 项目和可通过的 unittest。
3. 写入两张 `test_failure` experience card。
4. 执行：

```text
self_evolve run name=test-failure-recovery failure_type=test_failure min_count=2 command='discover -s tests -v'
```

5. 检查 `summary.json`：

```json
{
  "experience_cards": 2,
  "validated_candidates": 1,
  "promoted": false
}
```

成功标准：

- 生成 1 个 candidate skill。
- candidate metadata 中 `status=validated`。
- stable skill 目录未被写入，除非显式传 `--promote`。
- `summary.json` 可复现实验路径、候选 id、经验日志路径。

失败标准：

- 没有生成 candidate。
- validation 未记录。
- 未显式 promote 却写入 stable skill。
- 输出包含疑似密钥/token。

### E1：真实 agent 单轮 benchmark 对照

目的：检查自进化 skill 是否能改善现有 code-agent benchmark。

任务集：

| 任务 | 类型 | 目标 failure_type |
| --- | --- | --- |
| `todo-active-items` | Python 单文件 bugfix | `test_failure` |
| `invoice-discount` | Python 多文件 feature | `test_failure` |
| `coin-catcher-score` | JavaScript/game 规则修改 | `test_failure` |

对照组 A：无自进化 skill

```bash
python3 scripts/run_code_agent_benchmark.py --run-agent --timeout 480
```

实验组 B：先执行 E0 或从历史失败生成并 promote `test-failure-recovery`，再跑同一 benchmark。

建议固定条件：

- 同一 backend/provider/model。
- 同一 timeout。
- 同一网络代理配置。
- 每组至少跑 3 轮，避免单次 LLM 抖动误导结论。
- 每轮使用新的 benchmark workspace，但共享同一个 stable skill 目录或明确指定 `SIMPLE_HERMES_SKILLS_DIR`。

采集指标：

| 指标 | 来源 | 解释 |
| --- | --- | --- |
| pass_rate | `benchmark_runs/<run>/summary.json` | 最终测试通过率 |
| ready_rate | `summary.json` | fixture 初始失败是否正常 |
| duration_seconds | `summary.json` | 端到端耗时 |
| step_count | trace 中 streamed step 数 | agent 执行长度 |
| repeated_inspection_blocks | trace 中重复 read/search/project_overview 次数 | 是否还在循环检查 |
| experience_cards_created | `.simple_hermes/evolution/**/experience.jsonl` | 是否仍在产生同类失败 |
| skill_used | session history 中 `skill_context` | 是否实际加载了 skill |

成功标准：

- 实验组 pass_rate 不低于对照组。
- 同类任务平均 step_count 下降，或 repeated_inspection_blocks 下降。
- 没有新增敏感信息泄漏。
- 失败时能产生结构化 experience card，而不是只留下散乱 trace。

不算成功的情况：

- 只因为模型随机性单轮通过。
- skill 未被加载，但结果变好。
- 通过率变高但修改了测试或跳过验证。
- 产生大量泛化很差的候选 skill。

### E2：顺序任务流自进化

目的：验证“越做越会做”，接近 SEA-Eval 的长期评测方式。

任务流设计：

```text
round 1: todo-active-items
  -> 若失败，记录 experience
  -> self_evolve propose/validate
  -> 人工或脚本显式 promote

round 2: invoice-discount
  -> 观察是否复用 test-failure-recovery
  -> 若失败，记录新 experience
  -> 更新候选或生成 patch proposal

round 3: coin-catcher-score
  -> 观察跨语言任务是否仍有帮助，还是误导
```

关键点：

- 每个 round 的 workspace 必须重建，避免代码修改泄漏。
- skill/memory 目录可以延续，这是实验变量。
- 每轮都保存 trace、summary、experience log、candidate metadata。
- 若 skill 导致错误方向，应记录 negative outcome，不能直接覆盖原 skill。

判定：

- 如果 Python 任务收益明显但 JavaScript 任务无收益，不应强行宣称通用自进化；结论应限定为 `test_failure` 工作流 skill 对 Python unittest 类任务有效。
- 如果跨语言任务被误导，应把 skill 的 `When to Use` 缩窄。

## 实验变量

| 类型 | 变量 | 取值 |
| --- | --- | --- |
| 自变量 | skill 状态 | none / candidate only / validated promoted |
| 自变量 | experience 数量 | 1 / 2 / 5+ |
| 自变量 | validation gate | no command / unittest command / benchmark task |
| 控制变量 | backend | 固定同一 provider/model |
| 控制变量 | timeout | 固定，例如 480s |
| 控制变量 | workspace | 每轮从 fixture 复制 |
| 因变量 | pass_rate | 0-1 |
| 因变量 | step_count | trace 统计 |
| 因变量 | failure_card_count | JSONL 统计 |
| 因变量 | unsafe_write_count | stable skill 未授权写入次数 |

## 推荐实验矩阵

第一阶段只跑小矩阵：

| 组别 | experience | self_evolve | promote | benchmark |
| --- | --- | --- | --- | --- |
| A0 baseline | none | no | no | all tasks x3 |
| B0 candidate-only | 2 test_failure cards | run | no | all tasks x3 |
| C0 promoted | 2 test_failure cards | run + validate | yes | all tasks x3 |
| D0 negative gate | malformed candidate | validate | no | no benchmark，只测 gate |

解释：

- A0 给基线。
- B0 检查“只生成候选但不加载”不会污染行为。
- C0 检查 promote 后是否有改进。
- D0 检查验证门能否拒绝坏 skill。

## 数据产物

每次实验生成一个 run 目录：

```text
self_evolution_runs/<run-id>/
  summary.json
  workspace/
  skill_candidates/
  stable_skills/
  memory.txt
  sessions.db

benchmark_runs/<run-id>/
  summary.json
  traces/*.txt
  workspaces/*/
```

建议后续新增统一 `evolution_experiment_summary.json`：

```json
{
  "run_id": "20260417-e2-001",
  "group": "C0-promoted",
  "backend": "hermes-runtime",
  "model": "fixed-model-name",
  "tasks": [
    {
      "task_id": "todo-active-items",
      "passed": true,
      "step_count": 18,
      "duration_seconds": 52.4,
      "skill_used": ["test-failure-recovery"],
      "experience_cards_created": 0
    }
  ],
  "safety": {
    "secret_scan_hits": 0,
    "unauthorized_stable_skill_writes": 0
  }
}
```

## 分析方法

先不用复杂统计，先看工程信号：

1. 每组 pass_rate。
2. 每组平均 step_count。
3. 每组 repeated_inspection_blocks。
4. 每组新产生的 `test_failure` experience 数。
5. 每组 candidate/promote 数量。
6. 每组敏感扫描结果。

三轮样本太少时，只能给工程判断，不做显著性结论。真正要比较，需要每组 10+ 轮，或者更多同类 fixture。

## 安全和污染控制

必须满足：

- 默认不读取 `.env`、auth profile、API key 文件。
- `self_evolve run` 不直接写 stable skill。
- `skills promote` 前必须通过 placeholder/quality gate。
- 实验产物目录加入 `.gitignore`。
- push 前执行 token 形态扫描：

```bash
rg -n -S '(sk-[A-Za-z0-9_-]{12,}|sk-proj-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})' . \
  -g '!self_evolution_runs/**' \
  -g '!benchmark_runs/**' \
  -g '!test_results/**' \
  -g '!traces/**' \
  -g '!.git/**'
```

## 当前最小下一步

先不要扩大功能。下一步只做实验工具化：

1. 给 `scripts/run_self_evolution_experiment.py` 增加 `--group baseline|candidate|promoted|negative-gate`。
2. 增加 `scripts/analyze_evolution_runs.py`，读取 `self_evolution_runs/` 和 `benchmark_runs/` 输出对照表。
3. 让 benchmark runner 可选注入 `SIMPLE_HERMES_SKILLS_DIR`，方便 A/B 组隔离。
4. 先跑 E0 + D0，确认闭环和验证门。
5. 再跑 E1 的 A0/C0，各 3 轮。

只有 E1 出现稳定正向信号后，再做 E2 长期顺序流。
