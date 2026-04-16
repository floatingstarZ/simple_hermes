# Simple Hermes DailyTrack 失败分析报告

日期：2026-04-15  
分支：`20260415.6-hermes-runtime-core`  
任务：评估 `simple_hermes_codex` 是否能独立完成 `/Users/hzy/Desktop/work/DailyTrack_NewTech` 的 DailyTrack 工作流，并解释它为什么没有产出有效结果。

## 1. 结论摘要

本次失败不是因为 `simple_hermes_codex` 完全不会搜索、不会运行脚本，或者模型本身无法理解 DailyTrack。trace 显示，它已经能完成一部分真正的 agent 行为：

- 能读取项目结构、最近 track 文件和项目约定。
- 能启动 background 任务并处理部分 completion event。
- 能调用项目内 skills 脚本采集 HuggingFace Papers、arXiv、RSS、GitHub、Anthropic 页面。
- 能在 pip 系统环境安装失败后，改用局部 `.venv` 继续 RSS 采集。
- 能生成 `2026-04-14/track.md`、`2026-04-14/papers.json`，并尝试更新全局索引。

但是，它没有把这些动作组织成稳定的任务级 pipeline。最终表现为：

- `track.md` 只有约 4.2 KB，内容明显不完整。
- `papers.json` 只有 1 条，而不是一个合理的 DailyTrack 精选列表。
- `track.md` 中多篇论文的 arXiv 链接为空或为 `N/A`。
- GitHub、HuggingFace Hub、RSS/博客等来源大多变成占位式描述。
- 验证阶段发现字段缺失后，agent 陷入 read/search/patch/verify 循环。
- 最后被本地 `no-edit workflow budget` 拦截，未能完成可靠修复。

因此，问题的根因不是“搜索能力差”，而是 **缺少成熟 code/research agent runtime 所需的 artifact 管理、产物契约、结构化合成、失败恢复和质量门控**。

## 2. 对比结果

### 2.1 simple_hermes 结果

保存位置：

- trace：`test_results/dailytrack-agent-runs/hermes-runtime-core-after-output-budget/dailytrack_2026-04-14_20260415-130524_trace.txt`
- 输出目录：`test_results/dailytrack-agent-runs/hermes-runtime-core-after-output-budget/output_2026-04-14/`
- `track.md`：约 4252 bytes
- `papers.json`：约 512 bytes，只有 1 条

`papers.json` 中唯一条目：

```text
Teaching LLMs Human-Like Editing of Inappropriate Argumentation via Reinforcement Learning
```

`track.md` 中能看到一些候选论文，但很多链接和字段丢失：

```text
PromptEcho ... |  | cs.CV, cs.AI
KnowRL ... |  | cs.AI
PubSwap ... |  | cs.LG
Rethinking On-Policy Distillation ... |  | cs.LG, cs.AI
Lightning OPD ... |  | cs.LG, cs.AI
```

这说明模型知道有这些候选，但没有把结构化来源字段带到最终产物。

### 2.2 full Hermes 结果

保存位置：

- trace HTML：`test_results/dailytrack-agent-runs/full-hermes-retry/hermes_dailytrack_2026-04-14_20260415-133918_trace.html`
- 输出目录：`test_results/dailytrack-agent-runs/full-hermes-retry/output_2026-04-14/`
- `track.md`：约 16 KB
- `papers.json`：约 35 KB，21 篇
- `raw/`：63 个 JSON 原始/中间结果
- `repo_updates/`：隔离仓库内被 Hermes 更新的 global/MEMORY 文件副本

full Hermes 的关键差异：

- 自动加载相关 skills：`arxiv`、`huggingface-hub`、`blogwatcher`、`github-repo-management`、`hermes-agent`。
- 生成 `2026-04-14/raw/collect_sources.sh`，把多源采集固化成可复用脚本。
- 生成 `2026-04-14/raw/build_dailytrack.py`，从 raw JSON 结构化合成 `track.md` 和 `papers.json`。
- 发现 GitHub skill 输出不完整后，主动改用 `gh api` 补采 watchlist 仓库和搜索结果。
- 发现 OpenAI 页面 scraper 失败后，尝试 browser fallback。
- 最终同步更新 `global/index.md`、`global/watchlist.md`、`global/papers.json`、`global/seen_papers.json`、`global/anthropic_news.json` 和 `MEMORY.md`。

full Hermes 的成功关键不是更会写总结，而是它把任务变成了 **采集脚本 + 原始数据目录 + 合成脚本 + 质量检查 + 全局状态更新** 的执行链。

## 3. Trace 层面的失败路径

simple_hermes 的 trace 大致分为五段。

### 3.1 前期规划和采集是有效的

它先创建 todo：

```text
plan -> collect -> synthesize -> verify
```

然后读取项目结构和最近 track 文件：

```text
tree .
read 2026-04-13/track.md
```

随后启动多组 background 任务：

```text
python3 skills/huggingface-papers/scripts/fetch_hf_papers.py ...
python3 skills/rss-reader/scripts/read_rss.py ...
python3 skills/arxiv-fetch/scripts/fetch_arxiv.py ...
python3 skills/github-fetch/scripts/github_fetch.py ...
python3 skills/web-scraper/scripts/scrape.py ...
```

这些动作说明 agent 的初始方向不是错的。

### 3.2 它采到了多源候选，但最终没有完整使用

本地检查 raw artifacts 显示：

```text
/tmp/arxiv_llm_rl.json          30 条
/tmp/arxiv_grpo.json            20 条
/tmp/arxiv_online_distill.json  15 条
/tmp/arxiv_embodied.json        20 条
```

这和最终 `papers.json` 只有 1 条形成直接矛盾。  
因此，失败发生在 **raw artifacts 到 deliverables 的转换阶段**。

### 3.3 合成阶段没有形成稳定 pipeline

simple_hermes 在 step 25/26 使用临时 Python 脚本尝试写文件，但这个脚本没有作为项目内 artifact 保存下来，也没有形成清晰 schema：

- 没有保留完整候选列表。
- 没有统一 `arxiv_id` / `arxiv_url` / `source` / `source_date`。
- 没有对 `papers.json` 进行条目数和字段完整度校验。
- 没有从 raw JSON 重新构建 markdown 表格。
- 对 GitHub/RSS/HF Hub 的处理偏占位。

最终 `track.md` 中出现“候选标题存在，但链接为空”的典型结构化合成错误。

### 3.4 验证发现问题，但修复方向错误

后续 trace 中，agent 已经发现了问题：

```text
Inspect track.md and papers.json for empty link/arxiv fields
Locate source artifacts or generated data containing the missing arXiv IDs/URLs
```

但是它采用的是局部 patch 思路：

```text
patch_file track.md ...
```

并遇到：

```text
Target string not found in 2026-04-14/track.md
```

这类失败是必然的。长 markdown 表格不适合靠 exact string patch 修复，尤其当模型看到的是截断/压缩后的内容时，空格、摘要截断、行格式都可能不一致。

正确做法应该是：重新读取 raw artifacts，重建整个 `track.md` section 和 `papers.json`，而不是修补坏表格中的某几行。

### 3.5 最终被 no-edit guard 拦截

最终停止信息：

```text
Stopped because the backend kept proposing non-writing tool calls after the no-edit workflow budget was exhausted.
```

相关控制参数在 `simple_hermes/agent/core.py`：

```python
WORKFLOW_INSPECTION_BUDGET = 12
WORKFLOW_NO_EDIT_STEP_BUDGET = 40
WORKFLOW_POST_EDIT_INSPECTION_BUDGET = 8
```

相关拦截逻辑：

```python
if requires_edit and not edit_is_complete and step >= WORKFLOW_NO_EDIT_STEP_BUDGET:
    ...
```

这个 guard 本来是为了防止 agent 无限阅读、不写文件。  
但在这个案例里，它暴露出更深的问题：agent 需要的是 **从错误交付物恢复到结构化重建** 的机制，而不是继续提醒模型“下一步要写文件”。

## 4. 根因分析

### 4.1 缺少 artifact store

DailyTrack 不是一次普通搜索，而是多源采集任务。每个来源都应落盘为可追踪 artifact：

```text
raw/hf_daily.json
raw/arxiv_llm_rl.json
raw/arxiv_agent.json
raw/github_watchlist.json
raw/rss_blogs.json
raw/anthropic_engineering.json
...
```

full Hermes 成功的核心之一就是把 raw data 留在 `raw/`。  
simple_hermes 虽然运行了脚本，但很多结果落在 `/tmp` 或工具输出中，没有被统一登记、验证和引用。

后果：

- 后续合成脚本不知道完整数据在哪里。
- 模型上下文压缩后丢掉 URL/ID。
- 验证阶段只能在坏 markdown 里找信息，而不是回到 raw JSON。

### 4.2 缺少 deliverable schema

DailyTrack 至少需要两个明确 schema：

`papers.json` 条目应包含：

```json
{
  "item_type": "paper",
  "arxiv_id": "...",
  "title": "...",
  "authors": [],
  "published": "...",
  "updated": "...",
  "source": "...",
  "source_date": "...",
  "arxiv_url": "...",
  "hf_url": "...",
  "category": "...",
  "tags": [],
  "summary": "...",
  "reason": "...",
  "confidence": "high|medium-high|medium|low",
  "date_tracked": "2026-04-14"
}
```

`track.md` 应包含：

- 日期和覆盖范围。
- HF Papers section。
- ArXiv section，按主题分组。
- GitHub watchlist。
- GitHub Top/Trending 新发现。
- HuggingFace Hub。
- RSS/官方博客。
- Anthropic / OpenAI 等产业信号。
- 综合总结。
- 数据文件说明。

simple_hermes 当前没有在 runtime 层强制这个 contract，只是让 planner 自己写。模型会写得像报告，但不会稳定保证字段完整。

### 4.3 工具输出截断导致结构化信息丢失

`simple_hermes/agent/core.py` 里有工具结果压缩逻辑，避免 prompt 过长。这对普通任务合理，但对 DailyTrack 这类信息抽取任务会造成副作用：

- 工具输出里有 title 但后续上下文里没有完整 arXiv ID。
- 有摘要但没有 URL。
- 有 GitHub repo 名但没有 stars/pushed_at/latest commit。

full Hermes 的处理方式是：大输出不要靠模型记，应该落盘为 artifact，然后用脚本读取。

### 4.4 patch 工具不适合修复结构化长文档

当前 `patch_file` 偏 exact replacement。DailyTrack 失败后要修的是：

- markdown 表格多行；
- JSON 列表；
- global index；
- seen_papers；
- anthropic_news。

这些都不适合靠 exact text patch。更好的通用方式是：

- JSON 文件用 parser 读写。
- Markdown section 用 heading 边界重写。
- 整个 DailyTrack 用 build script 重建。
- 验证失败时回到 source artifacts 重跑 build，而不是 patch 输出文件。

### 4.5 no-edit guard 的语义不够细

当前 guard 只能区分：

- 是否需要 edit；
- 是否已经有成功 edit；
- 是否继续 inspection；
- 是否超过 budget。

但 DailyTrack 需要更细的任务状态：

```text
collecting -> raw_artifacts_ready -> draft_written -> schema_invalid -> rebuild_required -> validated
```

现在的状态机只知道“还没完成编辑”，不知道“已经写了坏产物，需要重建产物”。因此它只能把模型往 patch/write 推，而不能自动触发正确恢复动作。

### 4.6 background 任务有了，但缺少任务级 orchestration

simple_hermes 已经支持 background，并且能收到 completion event。但它只是工具层能力，不是任务级调度器。

full Hermes 更接近：

```text
生成采集计划
生成采集脚本
运行采集脚本
检查哪些源成功/失败
补采失败源
生成合成脚本
验证结果
更新全局状态
```

simple_hermes 目前是：

```text
模型每步选择一个 tool call
本地 guard 防止太离谱
```

这两者的稳定性差距很大。

### 4.7 项目内 skills 没有变成“可执行 workflow”

simple_hermes 能读 skill，也能运行 skill scripts，但没有把多个 skill 合成一个工作流。  
DailyTrack 项目的 skills 本质是组件：

- `huggingface-papers`
- `arxiv-fetch`
- `github-fetch`
- `huggingface-fetch`
- `rss-reader`
- `web-scraper`
- `semantic-scholar-fetch`

成熟 agent 需要把这些组件组织成 pipeline。  
simple_hermes 当前只是“知道有这些工具，然后逐个尝试”，缺少 workflow compiler。

## 5. 为什么 full Hermes 能成功

full Hermes 成功不是因为它没有错误。trace 中它也遇到了错误：

- 默认模型和 provider 不匹配时第一次 400。
- `github_fetch.py` 输出不完整。
- OpenAI 页面 scraper 失败。
- 部分命令需要 fallback。

但它能恢复，原因是机制不同。

### 5.1 它把任务外化成脚本

full Hermes 写了：

```text
2026-04-14/raw/collect_sources.sh
2026-04-14/raw/build_dailytrack.py
2026-04-14/raw/update_globals.py
```

这带来几个好处：

- 每一步可复查。
- raw 数据不依赖模型上下文。
- 失败源可以单独重跑。
- 最终产物可重建。
- 后续 review 可以看脚本逻辑，而不是只看 agent 对话。

### 5.2 它主动补齐工具缺口

当 GitHub skill 不够时，它没有停在“结果为空”，而是改用：

```text
gh api repos/<repo>
gh api repos/<repo>/commits
gh api search/repositories
```

这说明它有“工具不够就换底层工具”的能力。

### 5.3 它维护全局状态

full Hermes 不只写当日文件，还更新：

- `global/index.md`
- `global/watchlist.md`
- `global/papers.json`
- `global/seen_papers.json`
- `global/anthropic_news.json`
- `MEMORY.md`

这符合 DailyTrack 项目的长期工作流。simple_hermes 虽然尝试更新全局文件，但因为前面的合成不可靠，更新也不可信。

### 5.4 它的输出更符合项目口味

full Hermes 的 `track.md` 有清晰判断：

```text
OPD 与 reward/state engineering 开始接棒 04-13 的 credit-assignment 小周期
agent 的 state/traceability/eval/governance 继续深化
Anthropic 同日给出 scalable oversight 与 agentic eval infra noise 两个高信号官方增量
```

这不是简单列论文，而是延续了项目已有的长期主题线。

## 6. simple_hermes 当前能力评估

### 6.1 已经具备的能力

目前 simple_hermes 已经具备：

- 基础 LLM planner。
- Hermes runtime backend 对接。
- 记忆和 session。
- `/resume`、`/rename`、压缩、background 等基础长期会话能力。
- 项目指令文件注入。
- 项目 local skills 发现。
- background process。
- 部分 no-edit / inspection loop guard。
- 基础文件读写、patch、terminal、diff、test。

这已经足够完成中小型代码任务和简单多轮任务。

### 6.2 不足以完成的任务类型

当前还不稳定的任务：

- 多源研究采集。
- 长链路数据清洗。
- 多个结构化产物同步更新。
- 需要全局状态维护的任务。
- 需要从失败 artifact 中恢复的任务。
- 需要长期主题延续和去重的 track 任务。

DailyTrack 正好覆盖了这些弱项。

## 7. 改进路线

下面的改进应尽量保持通用，不要把 DailyTrack 关键词硬编码进 agent。

### 7.1 引入 artifact store

目标：任何长任务中的大输出和中间结果，都应可追踪、可复用、可验证。

建议新增：

```text
.simple_hermes/artifacts/<session_id>/
  manifest.json
  raw/
  generated/
  logs/
```

manifest 记录：

```json
{
  "artifacts": [
    {
      "id": "art_001",
      "path": "...",
      "kind": "json",
      "source_tool": "background",
      "command": "...",
      "created_at": "...",
      "summary": "...",
      "row_count": 47
    }
  ]
}
```

这样 planner 后续不必靠上下文记忆，可以通过 artifact manifest 找到数据。

### 7.2 引入 deliverable contract

当用户要求“完成一个项目工作流”时，agent 应先从项目文件推断交付物，再建立 contract：

```json
{
  "deliverables": [
    {
      "path": "2026-04-14/track.md",
      "type": "markdown",
      "required": true,
      "validators": ["non_empty", "contains_sections", "no_placeholder"]
    },
    {
      "path": "2026-04-14/papers.json",
      "type": "json",
      "required": true,
      "validators": ["valid_json", "min_items:5", "required_fields:title,source,reason,confidence"]
    }
  ]
}
```

contract 可以由模型提出，但本地 runtime 应负责执行验证。

### 7.3 增加结构化 validator

新增通用 validation tool：

```text
validate_artifact <path> --schema <schema-file-or-inline>
validate_markdown <path> --required-headings ...
validate_json <path> --min-items ... --required-fields ...
```

DailyTrack 这类任务中，validator 应能直接报：

```text
papers.json has 1 items, expected >= 10
missing field arxiv_url in 0/21? OK
track.md has empty table cells in 5 rows
section "GitHub 框架动态" contains placeholder text
```

关键是：validator 的输出应引导重建，而不是只让模型继续读文件。

### 7.4 支持 section-level rewrite

新增或增强 patch 工具：

```text
replace_section path ::: heading ::: new_content
rewrite_json path ::: python/json transform
rewrite_markdown_table path ::: table_marker ::: rows_json
```

这样 agent 不需要 exact string patch 长文档。

### 7.5 失败时从 raw artifacts 重建，而不是 patch 坏结果

当 validator 发现结构性问题时，runtime 应给 planner 一个明确恢复消息：

```text
The deliverable is structurally invalid.
Do not patch individual markdown rows.
Rebuild track.md and papers.json from the raw artifacts listed below:
- raw/hf_daily.json
- raw/arxiv_llm_rl.json
- raw/github_watchlist.json
```

这比现在的“下一步必须写文件”更有效。

### 7.6 任务级 workflow runner

增加一个通用 workflow runner，不绑定 DailyTrack，但支持模型生成并执行阶段化计划：

```text
workflow start
workflow add collect "run source collection scripts"
workflow add synthesize "build deliverables from artifacts"
workflow add validate "validate schema and content"
workflow add repair "repair from validation errors"
workflow status
workflow complete
```

每个阶段有输入、输出、检查条件。这样比 todo 更可执行。

### 7.7 强化 background 任务和长命令管理

当前 background 可以跑命令，但需要更强：

- 支持 command group。
- 支持每个 background task 自动登记 artifact。
- 支持 timeout、retry、partial success。
- 支持 task log HTML。
- 支持失败源列表汇总。

full Hermes 的 `collect_sources.sh` 本质就是手工实现了这些。

### 7.8 引入 project workflow skill

不要在 agent core 中写 DailyTrack 偏好，但可以在 DailyTrack 项目里建立 workflow skill：

```text
.agents/skills/dailytrack-workflow/SKILL.md
.agents/skills/dailytrack-workflow/schema/papers.schema.json
.agents/skills/dailytrack-workflow/scripts/collect_sources.py
.agents/skills/dailytrack-workflow/scripts/build_track.py
.agents/skills/dailytrack-workflow/scripts/validate_track.py
```

simple_hermes 要做的是能正确发现并执行这个 workflow skill，而不是把关键词写死。

### 7.9 引入 stronger recovery policy

当前 repeated failure 主要阻止重复调用。还需要根据失败类型给恢复策略：

```text
patch target not found -> read exact section or rewrite whole section
json too few items -> rebuild from raw artifacts
empty URL field -> join against source JSON by title/arxiv_id
source script failed -> retry with venv or fallback CLI/API
diff unavailable -> use file snapshot diff
not git repo -> use internal checkpoint diff
```

### 7.10 对比测试加入 regression benchmark

把 DailyTrack 变成固定 benchmark，不要求实时内容完全一致，但要求结构达标：

```text
tests/benchmarks/dailytrack/
  input_prompt.txt
  expected_contract.json
  evaluator.py
```

最低验收：

- `track.md` 存在且大于指定大小。
- `papers.json` 存在且条目数 >= N。
- 每条 paper 有 title/source/reason/confidence/url 或 arxiv_url。
- 无明显 `待复核` / `N/A` / 空表格字段。
- raw artifacts 数量 >= N。
- global index 有日期记录。

## 8. 优先级建议

### P0：先修“产物能不能可靠完成”

1. Artifact store。
2. Deliverable contract。
3. JSON/Markdown validator。
4. 失败时重建而不是 patch。

这四项是 DailyTrack 能否稳定成功的核心。

### P1：再修“长任务执行体验”

1. Workflow runner。
2. Background command group。
3. artifact 自动登记。
4. HTML trace 增加 artifact/validator 面板。

### P2：最后修“智能度和生态”

1. Project workflow skill。
2. fallback tool policy。
3. session memory 与 workflow state 的联动。
4. 多 agent 并行采集。

## 9. 最终判断

`simple_hermes_codex` 当前处在一个中间阶段：

- 对普通代码任务，它已经从 toy agent 走向可用 agent。
- 对 DailyTrack 这种长链路研究任务，它还缺少成熟 runtime 的关键层。

full Hermes 的成功证明：同一个模型、同一个网络环境、同一个 DailyTrack 项目，任务本身是可以完成的。差距主要来自 agent runtime，而不是模型能力。

因此，下一步不应该继续堆 DailyTrack 专用提示词，而应该把 full Hermes 成功路径抽象成通用机制：

```text
project instructions
  -> workflow plan
  -> artifact collection
  -> schema-bound synthesis
  -> validation
  -> repair from artifacts
  -> final report and persisted state
```

只要这条链路做扎实，DailyTrack 会自然变好，其他复杂 code/research agent 任务也会一起变好。
