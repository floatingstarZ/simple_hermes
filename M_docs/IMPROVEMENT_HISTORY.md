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
- `20260415.6-hermes-runtime-core` 分支增加后台 completion event 注入机制，避免长任务结束后继续依赖模型轮询。

重要测试结论：

- 在 Codex sandbox 内直接联网会出现 `[Errno 1] Operation not permitted`，上层常表现为 `Connection error`，这不是 simple_hermes 的真实 backend 失败。
- 有效 DailyTrack 联网测试需要在获准的外部执行路径下运行。
- 最新完整单元测试：`test_results/unittest-20260415-105508.html`，`157 tests OK`。

## 9.5 重新对齐 Hermes runtime 设计

DailyTrack 端到端测试暴露了一个更根本的问题：继续给主循环叠加“别等太久”“别写空文件”这类后置提示，只是在修补症状。重新阅读 Hermes 之后，确认它稳定的关键不是更多任务关键词，而是运行时分层：

- Agent loop 自己管理需要状态的工具，例如 todo、memory、session_search、delegate。
- 后台命令注册成 process session，由 runtime 监控并在完成时通知 agent，而不是靠模型反复 wait/poll。
- 大工具输出和长 stdout 先进入可引用的结果存储，再把摘要放进上下文。
- session、memory、context compression 属于 continuity plane，不是附属功能。

本阶段先落地其中最小但关键的一步：把后台任务完成从“模型轮询”改为“runtime event”。`BuiltInTools` 现在保留后台任务完成事件，`SimpleAgent` 在每次 planner call 之前 drain 这些事件，并把它们作为 `background_event` 注入 run state。这样长命令结束后，planner 会直接看到完成事件和 stdout tail，可以进入综合、写入或验证阶段，而不必继续调用 `background wait`。

同一轮还加入了 planner 侧工具结果预览：完整工具结果仍写入 session history，但下一轮 planner prompt 只接收有界 head/tail 预览。这个改动对应 Hermes 的 tool result storage 思路，避免 pip、测试、爬虫或长 stdout 把每一轮上下文撑大，也减少后端在长失败输出后断开的概率。

这不是 DailyTrack 专用能力，也没有写入任何 tracking 偏好。它对应 Hermes 的 `notify_on_complete` 思路，是通用 long-running task 机制的第一步。

验证：

- `test_background_completion_event_is_injected_into_next_planner_call` 覆盖后台完成事件进入下一轮 planner message。
- `test_backend_followup_shortens_large_tool_result_for_planner` 覆盖大工具输出进入 planner 前被压缩。
- 最新完整单元测试：`test_results/unittest-20260415-130513.html`，`161 tests OK`。
- DailyTrack 端到端 trace：`test_results/dailytrack-agent-runs/hermes-runtime-core-after-output-budget/dailytrack_2026-04-14_20260415-130524_trace.html`。这次成功创建 `/tmp/dailytrack_sh_case_2026-04-14_20260415-130524/2026-04-14/track.md` 和 `papers.json`，并可看到多个 `background_event` 自动注入。

## 9.6 Artifact Contract 与交付物验证

重新分析 DailyTrack 失败 trace 后确认：问题不只是“模型不够聪明”或“搜索不够多”，而是 agent runtime 缺少从原始采集到最终交付物的硬约束。旧流程能收集多个 raw source，但 raw stdout 被压缩进上下文后，最终 `track.md` / `papers.json` 仍可能只保留少量条目，或者出现空链接、空字段、占位表格。

本阶段加入通用的 artifact contract：

- 新增 `artifact` 工具，支持 `artifact scan [path]`、`artifact list`、`artifact clear`。
- artifact manifest 写入项目内 `.simple_hermes/artifacts/<session>/manifest.json`，并同步进入 session state。
- 扫描时只记录 raw/artifacts/output/logs/reports 等 workflow 产物，以及 `track.md`、`papers.json` 这类典型交付物文件；不会把普通源码全部塞进 manifest。
- artifact 扫描使用容错目录遍历，遇到无权限目录时跳过，避免复现 `.Trash` 这类 `Operation not permitted` 问题。

同时加入通用 `validate_deliverable` 工具：

- JSON 交付物支持 `min_items=N` 和 `required_fields=a,b,c`。
- Markdown 交付物支持 `min_bytes=N`、`required_headings=A,B`，并检测 TODO/TBD/占位/待补等未完成标记和明显空表格单元。
- 验证失败时会列出附近 raw/artifact 候选文件，并明确要求从 raw artifacts 重建，而不是继续修补 Markdown 某一行。
- `validate_deliverable ok` 现在会清除交付物未完成状态，并被视为成功验证，避免已经通过结构校验后仍被 `requires_test` 逻辑反复拦截。

对应状态机修复：

- `no-edit` guard 只在尚未发生任何成功编辑前生效。编辑完成后，即使后续检查读到无关 TODO，也不再阻止验证、todo 更新或最终检查。
- `validate_deliverable` 失败会触发“从 raw/artifact 重建”的 follow-up message，但不会被当作普通失败工具去重拦截，从而允许重写后再次验证。
- artifact scan 被视为长工作流的有效进展，不会被 no-edit guard 当作空转检查。

验证：

- 单元测试新增 artifact scan、JSON schema gap、Markdown placeholder/empty cell、validation failure recovery、validation ok 清除 incomplete state 等覆盖。
- 最新完整单元测试：`test_results/unittest-20260415-163548.html`，`166 tests OK`。
- DailyTrack 真实隔离回归 trace：`test_results/dailytrack-agent-runs/artifact-contracts-after-guardfix/dailytrack_2026-04-14_20260415-162457_trace.html`。该 trace 中可看到 `artifact scan raw/2026-04-14` 记录 5 个 raw artifact，随后生成 `2026-04-14/track.md` 和 `2026-04-14/papers.json`，并且 `validate_deliverable` 对二者均返回 ok；`papers.json` 为 14 条。
- 仍观察到 synthesis 前有较多 inspection 步骤，说明后续还应把“从 artifact manifest 直接生成 build script / deliverable”的能力做得更硬，而不是继续依赖模型自行决定何时停止检查。

## 9.7 Artifact Manifest 驱动的合成阶段约束

上一阶段解决了“有没有 raw artifact”和“最终交付物是否结构合格”的问题，但真实 trace 里仍能看到一个明显问题：artifact manifest 已经存在后，agent 仍可能继续读取 raw/log/source 文件，迟迟不进入 synthesis/write/validate 阶段。这不是 DailyTrack 专用问题，而是长信息任务通病：采集阶段没有被显式关闭，模型会继续用检查动作降低不确定性。

本阶段加入 artifact manifest 驱动的合成约束：

- 当前会话的 artifact manifest 会被注入 planner memory block，形成 `Current artifact manifest` 摘要。
- 摘要只包含路径、类型、条目数、字节数等轻量元信息，不读取或泄露文件全文。
- planner prompt 明确要求：当 memory block 已有 current artifact manifest 时，应把它当成 durable source inventory，进入 synthesis、write 或 validation，而不是继续 reread broad raw/log directories。
- agent loop 增加 `artifact_synthesis_blocked` 状态约束：在已有 artifact manifest 且尚未写入交付物时，如果继续连续宽泛 inspection，会被拦截并收到“从 recorded artifacts 合成/写入/验证”的恢复提示。
- 该约束由工具状态触发，不依赖 DailyTrack 关键词，也不写入任何用户 tracking 偏好。

验证：

- `test_backend_receives_artifact_manifest_summary` 覆盖 artifact manifest 进入 planner memory block。
- `test_artifact_manifest_pushes_agent_to_synthesis_instead_of_more_inspection` 覆盖已有 artifact 后继续检查会触发 `artifact_synthesis_blocked`，随后 agent 可以写入并验证交付物。
- 已先运行针对性单测：3 个相关测试均通过。

## 9.8 Tool Output Artifact Store

真实 DailyTrack 回归继续暴露出更底层的问题：source collection 命令已经成功返回大量 stdout，但这些结果没有稳定落盘。到了 synthesis 阶段，agent 会搜索 `artifacts/` 或目标日期 raw 目录，却找不到对应文件，于是重新采集或只凭压缩后的上下文写出带“待补”的交付物。

本阶段加入工具输出自动 artifact 化：

- `terminal` 成功执行且输出有实质内容时，会把 stdout/stderr 的规范化结果保存为 `.simple_hermes/artifacts/<session>/tool_outputs/*.log`。
- `background wait` 和 background completion event 成功完成时，也会把完成输出保存为 tool output artifact。
- tool output artifact 会自动写入 artifact manifest，并同步到 session state，因此后续 planner memory block 能看到 `Current artifact manifest`。
- 保存前会经过 secret redaction，避免 API key/token/password 等常见敏感值进入 artifact 文件或 manifest。
- 这不是要求模型主动使用 shell 重定向，而是 runtime 对成功工具输出做 durable capture，接近 Hermes artifact store 的基本职责。

同时修复一个实际 trace 中出现的兼容问题：

- `todo` 工具现在接受参数里重复带 `todo` 前缀的形式，例如 `todo update phase2 completed`。这类模型生成的小格式错误不应浪费两个 step。

验证：

- `test_terminal_success_output_is_saved_as_artifact` 覆盖 terminal 输出自动进入 artifact manifest。
- `test_todo_accepts_redundant_tool_prefix_in_argument` 覆盖冗余 `todo` 前缀兼容。
- `test_auto_tool_output_artifact_enables_synthesis_guard` 覆盖自动 tool output artifact 写入后，主循环能同步 `artifact_manifest_available`，并用 synthesis guard 阻止继续宽泛检查。
- 最新完整单元测试：`test_results/unittest-20260415-170709.html`，`171 tests OK`。
- DailyTrack 中断诊断 trace：`test_results/dailytrack-agent-runs/synthesis-contract/dailytrack_2026-04-14_20260415-165329_trace.html`。这次 trace 证明：agent 能完成采集、写入 `track.md`/`papers.json`，并由 `validate_deliverable` 拦截带“待补”的 Markdown；同时也证明了“stdout 未落盘”是 phase3 反复采集的关键原因。
- DailyTrack tool-output-artifacts trace：`test_results/dailytrack-agent-runs/tool-output-artifacts/dailytrack_2026-04-14_20260415-170318_trace.html`。这次 trace 可见 `.simple_hermes/artifacts/<session>/tool_outputs/*.log` 已自动生成，agent 也开始读取这些 artifact logs 进行 synthesis。该 trace 同时暴露出状态同步缺口：自动写入 manifest 后，主循环变量没有立刻同步，导致 synthesis guard 未触发；该问题已由 `test_auto_tool_output_artifact_enables_synthesis_guard` 修复。

## 9.9 Validation Recovery Cache Invalidation

进一步 DailyTrack 回归暴露出一个更细的状态一致性问题：`validate_deliverable 2026-04-14/papers.json ...` 先因文件不存在失败，agent 随后用 `write_file` 创建了 `papers.json`，但主循环仍把同一个 validation 调用视作“重复失败工具调用”，导致新文件无法再次验证。

本阶段补齐两个通用机制：

- `validate_deliverable` 失败时，候选重建来源现在会优先包含当前 session artifact manifest 中的路径，包括 `.simple_hermes/artifacts/<session>/tool_outputs/*.log`。这使结构化 JSON 不足条目或缺字段时，planner 能直接看到可用于重建的 durable tool outputs，而不是只扫 `raw/`。
- `write_file` / `patch_file` 成功写入某路径后，会清理同一路径上旧的 `validate_deliverable` / `read` / `read_lines` / `tree` 失败缓存。文件系统状态已经改变后，旧失败不应继续阻止相同工具调用。

验证：

- `test_validate_failure_lists_manifest_tool_outputs_as_rebuild_candidates` 覆盖 validation 失败时列出 manifest tool output 作为候选重建来源。
- `test_write_file_clears_stale_missing_validation_failure` 覆盖“先 validate 缺文件，write_file 创建，再 validate 同一路径”不会被重复失败缓存误拦截。
- 最新完整单元测试：`test_results/unittest-20260415-172532.html`，`173 tests OK`。
- DailyTrack trace：`test_results/dailytrack-agent-runs/manifest-rebuild-candidates/dailytrack_2026-04-14_20260415-171705_trace.html`。该 trace 中 agent 写出了 `2026-04-14/track.md` 和 `2026-04-14/papers.json`；`track.md` 经两轮修复后 `validate_deliverable ok`，`papers.json` 生成了 5 条且字段完整。trace 同时暴露出 stale validation failure cache 问题，已由本阶段缓存失效修复覆盖。

## 9.10 Background / Validation / Backend Recovery Contracts

继续 DailyTrack 真实回归时，出现了三个更通用的问题：

- 模型会把多个 source collection 命令拼成一个 `background start cmd && cmd && cmd`，导致一个后台任务承担多个逻辑任务，后续只能反复 wait/tail/status，难以精确恢复。
- `validate_deliverable` 能拦住占位交付物后，模型有时会反复运行同类“重建脚本”，但目标文件内容没有变化，随后继续重复同一个验证失败。
- 后台 source collection 失败后，后端偶发返回非法 planner JSON。旧逻辑会直接把最后一个失败工具结果当最终答案，导致长任务在采集阶段提前结束。

本阶段补齐了三个通用恢复契约：

- `background start` 拒绝顶层 shell 串联操作符 `&&`、`||`、`;`、`&`，并递归检测 `bash -lc` / `zsh -lc` / `sh -c` 包裹的串联命令。Python `-c` 这类语言内部分号不受影响。
- planner prompt 与 tool follow-up 明确要求：一个 background task 只做一个逻辑任务；需要原子串联时应先写项目本地脚本，再把脚本作为一个后台任务运行。
- `validate_deliverable` 增强占位检测：Markdown/JSON 中的 `待刷新`、`补全`、`后续步骤`、`2604.xxxxx`、空 arXiv 链接等会被标记为失败。
- 主循环记录每个交付物的内容 hash。若同一路径连续验证失败且文件 hash 未变，第三次验证会触发 `validation_no_progress_blocked`，要求先用 `write_file` / `patch_file` 或真正改变文件内容的 terminal 命令修复，而不是继续验证或重复同一类脚本。
- 工具结果之后的后端 planner 错误不再立刻终止。主循环会最多重试两次，并把上一工具结果、run state 和“必须返回 strict JSON”的恢复上下文重新交给后端。

验证：

- 新增单测覆盖：
  - chained background command refusal；
  - shell wrapper chain detection；
  - Python 内部分号不误杀；
  - chained background 被拒后的 agent recovery；
  - tracking placeholder Markdown/JSON validation；
  - validation failed but file unchanged 的 no-progress guard；
  - tool result 之后 backend JSON/parse error 的 retry。
- 最新完整单元测试：`test_results/unittest-20260415-180151.html`，`181 tests OK`。
- DailyTrack 失败 trace：`test_results/dailytrack-agent-runs/strict-deliverable-validation/dailytrack_2026-04-14_20260415-173931_trace.html`。该 trace 证明占位/低质量交付物会被验证器拦下，但也暴露出“文件未变仍重复验证”的循环。
- DailyTrack 后端错误 trace：`test_results/dailytrack-agent-runs/validation-no-progress-guard/dailytrack_2026-04-14_20260415-175255_trace.html`。该 trace 证明 source collection 已更完整，但后端非法 JSON 会提前结束任务。
- DailyTrack 成功 trace：`test_results/dailytrack-agent-runs/backend-retry-and-validation-guard/dailytrack_2026-04-14_20260415-175610_trace.html`。该 trace 中 agent 拆分了被拒绝的 `venv && pip install` 后台链，创建本地 venv，补装 `feedparser` 和 `requests`，完成 RSS/HF Hub 采集，写出 `2026-04-14/track.md` 与 `2026-04-14/papers.json`，并让二者通过 `validate_deliverable`。

残留问题：

- 成功 trace 的 DailyTrack 内容仍偏保守，`track.md` 对 arXiv/GitHub 的细粒度摘要不够强，`papers.json` 只有 3 条，质量还达不到人工专家最终版。
- 验证器目前能拦截占位、结构缺失和明显空内容，但不能判断论文 ID 与标题是否完全一致，也不能评估信息筛选质量。
- terminal heredoc 写入仍会让 no-edit guard 偶发误判，需要后续把 terminal 写文件识别从字符串 hint 升级为文件快照 diff/hash。

## 10. 当前能力边界

已经比较稳定的部分：

- 多步 agent loop。
- 真实 backend planner。
- CLI 流式 step 输出。
- 长期 session、continuation、recall。
- active task 多轮跟进。
- todo workflow ledger。
- artifact manifest 与交付物结构验证。
- artifact manifest 驱动的 synthesis/write/validate 阶段约束。
- terminal/background tool output artifact store。
- background task 生命周期。
- 项目 instruction/skill context。
- 单元测试和 HTML trace 报告。

仍然不应夸大的部分：

- 不是生产级 sandbox。
- `terminal` 默认权限仍偏宽松，适合本机受信项目实验。
- Cron 只是轻量任务存储和手动触发，没有常驻 daemon。
- MCP-like 导出还不是完整 MCP server。
- DailyTrack 能力已经能进入正确 workflow，但完整端到端产物仍受后端稳定性、网络源可用性和长任务调度影响，需要继续保留 trace 做回归。

## 11. 自进化试验补丁

2026-04-17 增加了一个不依赖 API key 的本地自进化实验闭环：

- `self_evolve status/propose/validate/run`：从 `.simple_hermes/evolution/<session>/experience.jsonl` 中读取同类经验卡，归纳候选 Markdown skill，并把验证记录写回 candidate metadata。
- `self_evolve run` 默认仍只写候选区，不覆盖稳定 skill；进入 `~/.simple_hermes_codex/skills/` 仍需要显式 `skills promote <candidate-id>`。
- `scripts/run_self_evolution_experiment.py`：创建隔离 demo workspace，写入两张 `test_failure` 经验卡，生成 `test-failure-recovery` 候选 skill，并用 unittest 作为验证门。
- 这一步验证的是“经验卡 -> 候选 skill -> 验证记录”的工程闭环，不是 RL 训练，也不是自动修改自身代码。

## 12. 后续建议

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
