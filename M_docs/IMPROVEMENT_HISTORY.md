# Simple Hermes Codex 改进历史

本文记录 `simple_hermes_codex` 从一个轻量 Hermes 学习项目，逐步演进成通用 code agent 实验环境的过程。它不是发布日志，而是偏工程复盘：每一阶段记录当时暴露的问题、采取的改进、验证方式和剩余风险，方便后续继续迭代时判断哪些设计是历史包袱，哪些能力已经被测试覆盖。

## 0. 项目定位

最初目标不是复刻完整 Hermes，而是保留一个足够小、足够可读的 agent loop，用来实验以下问题：

- LLM planner 如何稳定地产生工具调用，而不是只给建议。
- 代码 agent 如何读项目、改代码、跑测试、保留历史。
- 长任务如何通过 session、todo ledger、后台任务和上下文压缩持续推进。
- 复杂工作流，例如 DailyTrack，能否不靠硬编码关键词，而是靠项目说明、skills 和工具链完成。

当前项目已经从单步工具 demo 发展为可运行的本地 code agent：支持真实 LLM backend、Hermes runtime bridge、长期会话、上下文压缩、后台任务、workflow todo ledger、局部 skills、benchmark harness、HTML 测试报告和 DailyTrack 级别的工作流测试。

## 1. 基础 Agent Loop 和 CLI 体验

早期版本主要解决“能不能像 agent 一样工作”的问题。初始实现包含 `SimpleAgent`、工具注册表、显式命令解析和 REPL CLI。规则模式下，用户输入 `read README.md`、`tree .`、`write_file`、`patch_file`、`terminal`、`run_tests` 等命令，可以直接进入工具层。

这一阶段的问题很明显：它更像一个工具壳，而不是 code agent。用户让它“解析项目”“写一个小游戏”时，它经常停在提问或建议阶段，没有足够强的自主性，也不会持续推进多步任务。

主要改进包括：

- 增加真实 planner backend 支持，让模型选择下一步工具调用。
- 增加 Hermes runtime bridge，复用主 Hermes 的 provider/auth/runtime 配置。
- 给 CLI 增加实时 progress 输出，每个 step 显示 `tool_call`、`tool_result`、`text`、`blocked` 或 `error`。
- 增加 `/status`、`/trace` 等基础可观测能力，让用户知道当前项目根、session、backend、active task 和最大步数。

对应里程碑：

- `b66574a stream agent progress in cli`
- `06fe7a3 strengthen autonomous planner prompt`
- `e7c1999 improve cli status diagnostics`

## 2. 自主性与多轮任务

真实测试中，一个典型失败是用户要求“写 HTML 贪吃蛇”，agent 先问版本；用户回答“HTML版本”后，agent 仍然只给代码片段或建议保存，而不是直接在项目里创建文件。另一个失败是用户明确说“你做的任何操作都是允许的”，agent 仍然继续解释自己下一步会做什么。

这暴露出两个问题：

- system/planner prompt 不够强调“默认执行而不是询问”。
- 多轮 follow-up 没有被绑定到之前的 active coding task。

改进方向：

- 调整 planner prompt，明确它是 autonomous coding assistant 的规划层，除非任务确实不可能，否则应选择具体工具推进。
- 引入 active task state。模型声明 `requires_edit=True` 后，agent 会创建当前 coding task；后续短回复、补充约束或授权会挂到同一个任务上。
- 对过早 final text 加 guard。如果任务需要编辑但尚未成功编辑，planner 不能直接用文字结束，而会被要求继续读写文件。
- 对“写了占位/TODO/未完成产物就结束”的情况加检查，避免生成空壳 deliverable。

对应里程碑：

- `1511de1 use active task state for followups`
- `595c88e improve coding follow-up execution`
- `edf12a6 improve deliverable completion guards`

## 3. 去掉自然语言意图启发式

一度存在 `_wants_file_explanation` 这类本地启发式，用关键词判断用户是不是想解释文件、是不是要编辑、是不是要测试。这类设计短期能让 demo 变好，但长期会破坏通用性：不同任务、不同语言、不同用户表达都会让关键词规则误判。

这阶段的原则是：

- 意图判断交给 backend planner。
- 本地 Python 只做确定性控制流、工具安全、重复调用保护、测试结果识别、后台任务状态识别。
- 判断“工具结果是否成功”“terminal 是否可能修改文件”“测试是否真的跑了”这类状态启发式可以保留，因为它们不是自然语言意图分类，而是运行时事实判断。

最终移除了自然语言意图路由，把是否需要编辑/验证沉到 `PlannerDecision.requires_edit` 和 `PlannerDecision.requires_test`。

对应里程碑：

- `cbf286f remove natural language intent heuristics`

保留下来的判断型启发式主要包括：

- 识别测试是否成功，且排除 `Ran 0 tests`。
- 识别 terminal 命令是否可能写文件。
- 识别 `background wait/status/tail` 的循环和完成状态。
- 识别 placeholder/TODO/incomplete deliverable，防止过早结束。

## 4. 长期会话、上下文压缩和基础会话命令

随着任务变长，单轮对话不够用。项目开始支持 SQLite session store：每条消息记录 `role`、`kind`、`tool_name` 和时间戳，支持 session lineage、descendants、focused recall 和 cross-session recall。

新增能力：

- 默认 session id 由 project root 派生，做到同一项目长期恢复。
- `/resume`：列出或切换 session，支持编号、`latest` 和 `project`。
- `/rename`：给当前 session 命名。
- `/new`、`/reset`、`/compress`、`/usage`、`/sessions`、`/tool-results`。
- 自动上下文压缩：当消息数超过阈值，生成 handoff summary，创建 continuation session，并在下次启动时恢复最新 continuation。

压缩摘要保留：

- 当前目标。
- 用户约束和偏好。
- 已完成进度。
- 相关文件和产物。
- 剩余任务。

对应里程碑：

- `9c3d12a add session slash commands and docs diagrams`
- `448ab0d update readme and session diagrams`

## 5. 后台任务与长命令

DailyTrack、benchmark、测试套件和网络采集都可能超过同步 terminal 的舒适范围。为此加入 `background` 工具。

后台任务支持：

- `background start <cmd>`：启动长命令。
- `background list/status/tail/wait/stop`：查看、采样、等待或停止任务。
- 任务完成后，结果会写回 session history，供后续 planner 读取。
- 对 wait/status/tail 循环加保护，避免 planner 一直等待同一任务。

后续 DailyTrack 测试暴露了一个细节 bug：任务已经完成后，planner 再次 `wait` 以收集输出时，会被错误地当作等待循环阻断。修复后，agent 会根据最近 observations 判断任务是否已知完成，允许收割最终输出。

对应里程碑：

- `b66574a stream agent progress in cli`
- `752e4d0 improve backend and background reliability`

## 6. 本地 Skills、Cron、MCP-like 导出和安全工具

为了向 Hermes 靠近，但不把实现变得过重，项目增加了一批轻量能力：

- `skills create/list/view/use/delete`：本地 Markdown skill。
- 项目级 `skills/` 扫描：planner prompt 可以看到项目自带 skill 摘要。
- `cron add/list/run-due/run/delete`：轻量 scheduled task 存储和触发。
- `mcp resources/sessions/session/search`：以 JSON 资源形式导出 session 数据，为后续真实 MCP server 预留接口。
- `dependency_scan`、`credential_audit`：只做轻量扫描，`credential_audit` 默认只列路径，不打印密钥内容。

重要约束：

- 不把 DailyTrack 关键词或用户个人 tracking 偏好硬编码进 agent。
- 让 agent 通过项目 `AGENTS.md`、`CLAUDE.md`、`MEMORY.md` 和项目 skills 理解任务。
- 对输出到 planner 或 MCP-like export 的内容做密钥脱敏。

对应里程碑：

- `be121f2 add local skills cron mcp and safety tools`
- `aee20c8 add project instruction skill context`

## 7. Benchmark 和 HTML 测试报告

为了避免“看起来能跑，但回归无法确认”，项目加入两类测试体系。

第一类是单元测试：

- `python3 scripts/run_tests_with_results.py`
- 输出 `.log`、`.json`、`.html`
- HTML 报告按 test case 展示通过、失败和原始日志。

第二类是 code-agent benchmark：

- `benchmarks/code_agent_tasks.json`
- `fixtures/` 下提供多种小项目。
- 覆盖单轮、多轮、Python、JavaScript、多文件改动、TODO active items、小网页游戏等场景。
- `scripts/run_code_agent_benchmark.py --run-agent --timeout 480` 可调用真实 agent 执行。

交互式 trace 也可以渲染成 HTML：

- `scripts/run_tests_with_results.py --render-log <trace>`
- 生成每个 streamed step 的可视化表格，方便检查 planner 是否卡在读文件、是否真正写文件、是否正确使用后台任务。

对应里程碑：

- `a188370 add runtime controls and html test reports`
- `aa54a68 add chinese code comments and game fixture checks`

## 8. DailyTrack 能力改进

DailyTrack 是当前最重要的综合测试，因为它要求 agent：

- 读取目标项目说明和历史记忆。
- 识别目标日期。
- 使用项目本地 skills。
- 联网采集 HuggingFace Papers、arXiv、GitHub、RSS 等来源。
- 在长流程中维护阶段进度。
- 最终写入日期目录下的 track 文件和结构化数据。

早期问题：

- agent 会长时间检查文件和 skills，不进入产物写入。
- 缺少显式 workflow 状态，长任务中容易忘记自己处于哪个阶段。
- terminal 同步命令超时后，planner 不知道应改用后台任务。
- 后端 chunked-read 或连接抖动会中断整个工作流。

改进：

- 加入通用 `todo` workflow ledger，不针对 DailyTrack 硬编码。
- planner prompt 强调复杂任务先建立或更新 todo ledger。
- 对长 workflow 中“过度检查但没有写入”的情况加 progress guard。
- 对 backend 增加 retry 和 compact-context retry。
- 对 background wait-loop 做完成状态识别。
- 对当前轮用户消息从 backend history 中排除，避免首轮 prompt 重复当前 request。

有效 trace 说明：

- `test_results/dailytrack-agent-runs/20260415-workflow-ledger/simple_hermes_dailytrack_trace_net.html`：早期完整网络测试到达 step 30，成功使用 todo ledger、后台采集、HF papers、arXiv fallback、GitHub search 和 RSS venv recovery，但后端 chunked-read 在 synthesis 前中断。
- `test_results/dailytrack-agent-runs/20260415-workflow-ledger/simple_hermes_dailytrack_smoke_historyfix_net_trace.html`：history fix 后的有效联网 smoke，6 步内完成 active task 创建、todo 初始化、读取 `MEMORY.md`、planning 完成、collection 进入、HF/arXiv 后台任务启动。停止原因是显式设置 `SIMPLE_HERMES_MAX_STEPS=6`。

对应里程碑：

- `e4daacd improve workflow progress controls`
- `edf12a6 improve deliverable completion guards`
- `b41fdc2 add workflow todo ledger`
- `752e4d0 improve backend and background reliability`
- `e1e986d fix planner history for backend reliability`

## 9. 后端可靠性修复

真实 LLM backend 的失败形态有几类：

- HTTP 连接断开。
- incomplete chunked read。
- timeout。
- 代理或本地网络不通。
- Codex sandbox 禁止联网，但 SDK 只暴露为 `Connection error`。

当前修复：

- `SIMPLE_HERMES_BACKEND_RETRIES`，默认 2，最大 5。
- 对连接类错误做指数退避重试。
- JSON 解析、schema 错误不重试，避免掩盖 planner 输出问题。
- `SimpleAgent.plan()` 在全上下文 planner call 失败后，用压缩 memory、较短 history 再试一次。
- 新增 `scripts/diagnose_backend.py`，可分别诊断 backend、`SimpleAgent.plan()` 和 `SimpleAgent.run()`，并打印 prompt size 相关信息，但不打印密钥。
- 修复 `run()` 首轮 planning 时 current user message 重复进入 history 的问题。

重要测试结论：

- 在 Codex sandbox 内直接联网会出现 `[Errno 1] Operation not permitted`，上层常表现为 `Connection error`，这不是 simple_hermes 的真实 backend 失败。
- 有效 DailyTrack 联网测试需要在获准的外部执行路径下运行。
- 最新完整单元测试：`test_results/unittest-20260415-105508.html`，`157 tests OK`。

## 10. 当前能力边界

已经比较稳定的部分：

- 多步 agent loop。
- 真实 backend planner。
- CLI 流式 step 输出。
- 长期 session、continuation、recall。
- active task 多轮跟进。
- todo workflow ledger。
- background task 生命周期。
- 项目 instruction/skill context。
- 单元测试和 HTML trace 报告。

仍然不应夸大的部分：

- 不是生产级 sandbox。
- `terminal` 默认权限仍偏宽松，适合本机受信项目实验。
- Cron 只是轻量任务存储和手动触发，没有常驻 daemon。
- MCP-like 导出还不是完整 MCP server。
- DailyTrack 能力已经能进入正确 workflow，但完整端到端产物仍受后端稳定性、网络源可用性和长任务调度影响，需要继续保留 trace 做回归。

## 11. 后续建议

优先级较高的改进：

- 给 DailyTrack 增加更长步数的端到端验证，并保存成功写入 `track.md` / `papers.json` 的 HTML trace。
- 给 backend 诊断脚本增加 `--save-json`，保存错误类型、prompt size、session id 和 trace kinds，便于跨天对比。
- 将 background task 的结果摘要做得更结构化，减少长 stdout 对后续 planner prompt 的污染。
- 将 benchmark 结果做成趋势表，记录每个分支对不同任务的通过率。
- 在 restricted profile 下跑一次完整 benchmark，确认安全配置不会破坏基本 code-agent 能力。

维护原则：

- 不硬编码用户个人 tracking 偏好。
- 不把 API key、token、cookie 或 auth profile 内容写入文档、日志或测试产物。
- 对自然语言意图保持 planner-driven，本地只做确定性状态约束和工具安全。
- 每次复杂能力改进都保留：分支、commit、单元测试 HTML、关键交互 trace HTML。
