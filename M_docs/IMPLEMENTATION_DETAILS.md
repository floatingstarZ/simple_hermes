# Simple Hermes Codex 实现细节图

这份图集用于快速理解当前实现，而不是画产品宣传图。图里的模块名尽量对应真实文件、函数和状态字段，方便从图跳回代码。

## 1. 总体分层

```mermaid
flowchart TB
    User["用户输入\nsimple_hermes_codex >"] --> CLI["simple_hermes/cli.py\nREPL + UI panels + streaming progress"]

    CLI --> RootDetect["_detect_project_root()\nSIMPLE_HERMES_PROJECT_ROOT\n或当前工作目录"]
    CLI --> SessionDetect["_detect_session_id()\nproject-<sha1(root)>"]
    CLI --> Agent["SimpleAgent\nsimple_hermes/agent/core.py"]

    Agent --> Backend{"backend_from_env()"}
    Backend --> Rule["规则 fallback\nbackend=None"]
    Backend --> OpenAI["OpenAICompatibleBackend\n/chat/completions 或 /responses"]
    Backend --> Hermes["HermesRuntimeBackend\n复用 hermes-agent runtime/provider/auth"]

    Agent --> Memory["MemoryStore\n~/.simple_hermes_codex/memory.txt\n~/.simple_hermes_codex/user.txt"]
    Agent --> Sessions["SessionStore\n~/.simple_hermes_codex/sessions.db\nmessages + session_state + FTS"]
    Agent --> Tools["BuiltInTools + ToolRegistry"]

    Tools --> ReadTools["读项目\nread / read_lines / tree / glob / search / project_overview"]
    Tools --> EditTools["改项目\nwrite_file / patch_file / terminal"]
    Tools --> VerifyTools["验证\ndiff / run_tests / terminal"]
    Tools --> ContinuityTools["连续性\nhistory / recall / lineage / sessions / descendants / todo"]
    Tools --> BackgroundTools["后台任务\nbackground start/list/status/tail/wait/stop"]
    Tools --> DelegateTools["子任务\ndelegate / parallel_delegate"]

    ReadTools --> Project["project_root"]
    EditTools --> Project
    VerifyTools --> Project
    ContinuityTools --> Sessions
    BackgroundTools --> Sessions
    DelegateTools --> Sessions
```

## 2. CLI 实时 step 输出链路

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as cli.py main()
    participant Agent as SimpleAgent.run()
    participant Emit as emit(AgentTraceStep)
    participant Tool as ToolRegistry.run()
    participant UI as _stream_step()

    U->>CLI: 输入任务
    CLI->>CLI: _stream_start(mode)
    CLI->>Agent: run(message, on_step=_stream_step)
    Agent->>Emit: emit(task_frame_started 或 decision)
    Emit-->>UI: AgentTraceStep(step, kind, content, tool_name)
    UI-->>U: 立即打印 step 行
    Agent->>Tool: tools.run(name, argument)
    Tool-->>Agent: tool result
    Agent->>Emit: emit(tool_result)
    Emit-->>UI: AgentTraceStep
    UI-->>U: 立即打印 tool_result 预览
    Agent-->>CLI: AgentResponse(final_response, trace)
    CLI->>CLI: _stream_end()
    CLI-->>U: 最终 answer panel + tool/steps meta
```

关键实现点：

- `SimpleAgent.run(message, on_step=...)` 内部用 `emit()` 同时写入 `trace` 和触发回调。
- CLI 只负责渲染，不知道 agent 的内部状态机。
- `_preview_step_content()` 截断长 tool result，避免把终端刷乱。

## 3. Agent 主循环

```mermaid
flowchart TD
    Start["run(message)"] --> Resolve["_resolve_task_frame()\n只处理已有 active_task 连续性"]
    Resolve --> AppendUser["sessions.append(user_message)"]
    AppendUser --> BackendMode{"backend is None?"}
    BackendMode -->|yes| RulePlan["plan(...)\n显式命令/规则 fallback"]
    RulePlan --> RuleTool{"decision.tool_call?"}
    RuleTool -->|yes| RuleRun["tools.run()\nrecord tool_result\nemit tool_result"]
    RuleTool -->|no| RuleText["record assistant text"]
    RuleRun --> ReturnRule["AgentResponse"]
    RuleText --> ReturnRule

    BackendMode -->|no| Loop["for step in 1..max_steps"]
    Loop --> Plan["plan(current_message)\n显式工具只允许 step 1"]
    Plan --> PlanErr{"backend error?"}
    PlanErr -->|yes, 有 last tool| Fallback["返回 last tool result\n附 backend_error_fallback"]
    PlanErr -->|yes, 无 last tool| BackendErr["backend_error"]
    PlanErr -->|no| UpdateReq["合并模型声明\nrequires_edit / requires_test"]
    UpdateReq --> Decision{"tool_call?"}

    Decision -->|no text| NeedEdit{"requires_edit\n但未成功编辑?"}
    NeedEdit -->|yes| PrematureEdit["premature_text_blocked\n要求继续 write_file/patch_file/terminal edit"]
    PrematureEdit --> Loop
    NeedEdit -->|no| NeedDiff{"已编辑\n但未 inspect diff?"}
    NeedDiff -->|yes| AutoDiff["自动 diff\nrecord tool_result"]
    AutoDiff --> NeedTest
    NeedDiff -->|no| NeedTest{"requires_test\n但无成功测试?"}
    NeedTest -->|yes| PrematureTest["premature_text_blocked\n要求 run_tests 或 terminal test"]
    PrematureTest --> Loop
    NeedTest -->|no| FinalText["record assistant text\nmaybe mark active_task completed"]
    FinalText --> Compress["_maybe_compress_history()"]
    Compress --> ReturnText["AgentResponse"]

    Decision -->|yes tool_call| RepeatFail{"同一 tool+arg\n曾失败?"}
    RepeatFail -->|yes| RecoverFail["repeated_tool_blocked\n换路径/换工具/换策略"]
    RecoverFail --> Loop
    RepeatFail -->|no| RepeatInspect{"inspection tool\n同一目标已成功?"}
    RepeatInspect -->|yes| RecoverInspect["repeated_tool_blocked\n不要重复读/重复 broad inspection"]
    RecoverInspect --> Loop
    RepeatInspect -->|no| RunTool["tools.run(name, arg)"]
    RunTool --> ToolErr{"tool exception?"}
    ToolErr -->|yes| ToolError["tool_error\n结束"]
    ToolErr -->|no| Record["record tool_result\nemit tool_result\nappend run_observations"]
    Record --> UpdateFlags["更新 successful_edit\nsuccessful_test\ninspected_diff\ncompleted_inspections\nfailed_calls"]
    UpdateFlags --> Followup["_tool_followup_message()\n把原始请求+run state+tool result 喂给下一轮"]
    Followup --> Loop
```

## 4. 通用工作流 ledger

```mermaid
flowchart TD
    UserTask["复杂任务\n多阶段收集/编辑/验证"] --> PromptRule["planner prompt\n要求使用 todo ledger"]
    PromptRule --> TodoWrite["todo write JSON phases\n只允许一个 in_progress"]
    TodoWrite --> State["SessionStore.session_state\nkey=todo_list"]
    State --> MemoryBlock["_backend_memory_block()"]
    MemoryBlock --> Inject["Current workflow todo ledger\n只注入 pending/in_progress"]
    Inject --> Planner["下一轮 planner"]
    Planner --> Work["执行真实工具\nread / terminal / write_file / run_tests"]
    Work --> TodoUpdate["todo update\n完成当前阶段并推进下一阶段"]
    TodoUpdate --> State
```

这个机制来自对 Hermes/Codex trace 的对比：稳定的 code agent 不只靠 prompt 热情，而是需要一个显式、可持久、会被下一轮 planner 看见的进度结构。

- `todo` 是通用 session 工具，不包含任何 DailyTrack 专用关键词或偏好。
- `todo_list` 存在 `SessionStore` 的 state 表里，随当前 session 持久化。
- planner 负责决定何时创建阶段、何时推进阶段；Python 主循环只负责保存、校验并注入上下文。
- 已完成阶段不会反复注入，减少模型在长任务里重新做 broad inspection。
- `todo update`、`todo add` 这类复合工具名会被 `_normalize_tool_call()` 归一化，避免模型把动作写进 tool name 后工具层无法执行。

## 5. 代码任务 guard 和恢复路径

```mermaid
stateDiagram-v2
    [*] --> NeedGrounding: PlannerDecision.requires_edit=true
    NeedGrounding --> ToolPlanning: project_overview/tree/glob/search/read/read_lines
    ToolPlanning --> Editing: write_file 或 patch_file 或 terminal 编辑成功
    ToolPlanning --> BlockPrematureText: backend 提前给状态性 text
    BlockPrematureText --> ToolPlanning: 注入 recovery prompt

    Editing --> NeedDiff: successful_edit=True
    NeedDiff --> DiffDone: diff 成功
    DiffDone --> NeedTest: PlannerDecision.requires_test=true
    DiffDone --> FinalAnswer: requires_test=false

    NeedTest --> TestPassed: run_tests 或 terminal pytest/unittest exit code 0
    NeedTest --> BlockPrematureTest: backend 提前总结
    BlockPrematureTest --> NeedTest: 注入 run_tests 提醒
    NeedTest --> RepairAfterFail: 测试失败
    RepairAfterFail --> Editing: 根据失败输出 patch

    TestPassed --> FinalAnswer
    FinalAnswer --> [*]: record assistant_text + maybe compress

    ToolPlanning --> RepeatedInspectionBlocked: 同一 read/tree/project_overview 已成功
    RepeatedInspectionBlocked --> ToolPlanning: 改用更窄工具或直接编辑

    ToolPlanning --> RepeatedFailureBlocked: 同一 tool+arg 已失败
    RepeatedFailureBlocked --> ToolPlanning: 换路径/换工具/换策略
```

## 6. Planner backend 模式

```mermaid
flowchart LR
    Env["环境变量"] --> Selector["backend_from_env()"]
    Selector -->|SIMPLE_HERMES_BACKEND=rule/none/other| NoBackend["None\n规则 fallback"]
    Selector -->|openai/openai-compatible/llm| OAI["OpenAICompatibleBackend"]
    Selector -->|hermes/hermes-runtime| HR["HermesRuntimeBackend"]

    OAI --> Prompt["build_planner_prompt(PromptContext)"]
    HR --> Prompt
    Prompt --> Sys["PLANNER_SYSTEM_MESSAGE\nReturn strict JSON only"]
    Prompt --> Payload["JSON prompt\nrules + memory + recent_history + available_tools + user_message"]

    OAI --> Chat["/chat/completions\napi_mode=chat_completions"]
    OAI --> Responses["/responses\napi_mode=codex_responses"]
    HR --> HermesRuntime["hermes-agent runtime provider\nresolve_provider_client()"]

    Chat --> Parse["_parse_response_text()"]
    Responses --> Parse
    HermesRuntime --> Parse
    Parse --> Decision["PlannerDecision\nkind=text 或 tool_call"]
```

## 7. ToolRegistry 和权限路径

```mermaid
flowchart TB
    Agent["SimpleAgent"] --> Registry["ToolRegistry.run(name, arg)"]
    Registry --> Allowed{"allowed_tools is None\n或 name in allowed_tools?"}
    Allowed -->|no| ToolDenied["Tool not allowed in this agent"]
    Allowed -->|yes| Handler["handler(arg)"]

    Handler --> ReadPath["_resolve_project_path()"]
    Handler --> WritePath["_resolve_project_write_path()"]
    Handler --> TerminalGuard["_terminal_refusal_reason()"]
    Handler --> BackgroundGuard["_background_start()\n复用 terminal guard"]

    ReadPath --> ReadPerm{"outside/sensitive read allowed?"}
    ReadPerm -->|no| RefuseRead["Refusing to read..."]
    ReadPerm -->|yes| ReadFile["read_file/read_lines/tree/glob/search"]

    WritePath --> WritePerm{"outside/sensitive write allowed?"}
    WritePerm -->|no| RefuseWrite["Refusing to write..."]
    WritePerm -->|yes| Snapshot["_remember_file_snapshot()\n用于非 git diff fallback"]
    Snapshot --> WriteOps["write_file / patch_file"]

    TerminalGuard --> Dangerous{"redirection/destructive git/dangerous cmd?"}
    Dangerous -->|yes| RefuseTerm["Refusing terminal command"]
    Dangerous -->|no| RunShell["subprocess.run(shell=True)\ntimeout=10s"]

    BackgroundGuard --> Spawn["subprocess.Popen(shell=True)\nstdout/stderr 合流"]
```

## 8. 状态持久化关系

```mermaid
erDiagram
    sessions ||--o{ messages : contains
    sessions ||--o{ session_state : owns
    sessions ||--o{ sessions : parent_child
    messages_fts }o--|| messages : indexes_content

    sessions {
        text id PK
        real created_at
        text parent_session_id
        text title
        text session_type
    }

    messages {
        integer id PK
        text session_id
        text role
        text content
        text kind
        text tool_name
        real created_at
    }

    session_state {
        text session_id PK
        text key PK
        text value
        real updated_at
    }

    messages_fts {
        text session_id
        text content
    }
```

## 9. Active task 多轮连续性

```mermaid
flowchart TD
    Msg["用户消息"] --> Load["_load_active_task()"]
    Load --> HasTask{"存在 coding task\n且 status in_progress/awaiting_user?"}
    HasTask -->|no| Original2["当成普通新消息"]
    HasTask -->|yes| Separate{"显式工具命令或空消息?"}
    Separate -->|yes| Original2
    Separate -->|no| Continue["更新 last_user_message\n保存 active_task"]
    Continue --> Expanded["构造 expanded message:\nActive task id\nActive task goal\nCurrent user follow-up\n继续执行不要重新确认"]
    Expanded --> AgentLoop["进入 agent loop"]
    Original2 --> AgentLoop

    AgentLoop --> ModelReq{"PlannerDecision.requires_edit?"}
    ModelReq -->|yes, 且无 active_task| StartTask["_start_active_task()\nsession_state.active_task = JSON"]
    ModelReq -->|no| Done
    StartTask --> Done{"successful_edit\n且最终 text?"}
    Done -->|yes| Complete["_set_active_task_status('completed')"]
    Done -->|no| Keep["保持 in_progress"]
```

## 10. 上下文压缩和 continuation session

```mermaid
sequenceDiagram
    participant Agent as SimpleAgent
    participant Store as SessionStore
    participant DB as SQLite
    participant Next as Continuation Session

    Agent->>Store: history(session_id, limit=200)
    Store->>DB: SELECT messages
    DB-->>Store: history rows
    Store-->>Agent: rows
    Agent->>Agent: 找 last summary index
    Agent->>Agent: new_rows_since_summary > threshold?
    alt 需要压缩
        Agent->>Agent: _build_handoff_summary(history)
        Agent->>Agent: _continuation_tail(history)
        Agent->>Store: append(role=summary, kind=summary)
        Agent->>Store: create_continuation_session(parent)
        Store-->>Agent: continuation_id
        Agent->>Store: append(summary 到 continuation)
        Agent->>Store: append 最近非 tool_result tail
        Agent->>Agent: self.session_id = continuation_id
    else 不需要压缩
        Agent-->>Agent: return
    end
```

## 11. Background task 生命周期

```mermaid
flowchart TD
    Cmd["background start <cmd>"] --> Guard["_terminal_refusal_reason(cmd)"]
    Guard --> Refuse{"拒绝?"}
    Refuse -->|yes| ReturnRefuse["返回拒绝原因"]
    Refuse -->|no| Popen["subprocess.Popen\ncwd=project_root\nstdout=PIPE\nstderr=STDOUT\nstdin=DEVNULL"]
    Popen --> Task["BackgroundTask\nid/command/process/started_at/session_id/output/returncode"]
    Task --> Thread["daemon monitor thread\n_drain_background_task()"]
    Thread --> Drain["逐行读取 stdout\n保留最近 500 行"]
    Drain --> Wait["process.wait()"]
    Wait --> Completed["returncode + completed_at"]
    Completed --> Record["_record_background_completion()\n写入 kind=background_result"]
    Record --> History["history/recall 可检索后台结果"]

    Task --> Status["background status/list"]
    Task --> Tail["background tail <id> [lines]"]
    Task --> WaitCmd["background wait <id> [seconds]"]
    Task --> Stop["background stop <id>"]
```

## 12. Delegation 和 parallel delegation

```mermaid
flowchart TB
    Parent["Parent SimpleAgent\nsession_id=root/cont"] --> DelegateTool["delegate 或 parallel_delegate"]
    DelegateTool --> Depth{"delegation_depth < max?"}
    Depth -->|no| Refuse["Delegation refused"]
    Depth -->|yes| Mode{"parallel::?"}

    Mode -->|single| ChildSession["create_child_session(parent)"]
    ChildSession --> ChildAgent["Child SimpleAgent\nshared MemoryStore + SessionStore\nchild_step_budget\nchild allowed tools"]
    ChildAgent --> ChildRun["child_agent.run(task)"]
    ChildRun --> Summary["Child agent summary(session_id): ..."]

    Mode -->|parallel| Specs["为每个 subtask 创建 child session"]
    Specs --> Pool["ThreadPoolExecutor\nmax_workers <= 4"]
    Pool --> Worker1["worker child 1\nfresh SessionStore connection"]
    Pool --> Worker2["worker child 2\nfresh SessionStore connection"]
    Pool --> WorkerN["worker child N\nfresh SessionStore connection"]
    Worker1 --> Joined["Parallel child summaries"]
    Worker2 --> Joined
    WorkerN --> Joined
```

## 13. Benchmark harness

```mermaid
flowchart TD
    TaskFile["benchmarks/code_agent_tasks.json"] --> Load["load_tasks()"]
    Load --> Select["--task-id 过滤"]
    Select --> Copy["copy_template()\nfixtures/* -> benchmark_runs/<run>/workspaces/<task_id>"]
    Copy --> InitialTest["run_command(test_command)\n确认 expected_initial_failure"]
    InitialTest --> RunAgent{"--run-agent?"}
    RunAgent -->|no| FinalTest
    RunAgent -->|yes| AgentEnv["agent_env()\nSIMPLE_HERMES_PROJECT_ROOT=task_dir\nSIMPLE_HERMES_SESSION_ID=bench-...\nSIMPLE_HERMES_MAX_STEPS=300\nSIMPLE_HERMES_BACKEND 默认 hermes-runtime"]
    AgentEnv --> CLI["subprocess.run(simple_hermes_codex)\n输入 turns + /trace + exit"]
    CLI --> Trace["traces/<task_id>.txt"]
    Trace --> FinalTest["run_command(test_command)"]
    FinalTest --> Summary["summary.json\nready / passed / agent result / test result"]
```

## 14. 一次复杂代码修改任务的完整闭环

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as CLI
    participant A as SimpleAgent
    participant B as Backend Planner
    participant T as Tools
    participant S as SessionStore

    U->>CLI: 请解析项目、修改代码、运行测试
    CLI->>A: run(message, on_step)
    A->>S: append(user_message)
    A->>A: _start_active_task()
    A->>B: plan(original_message + memory/history/tools)
    B-->>A: tool_call project_overview
    A->>T: project_overview
    T-->>A: markers/file counts/test commands
    A->>S: append(tool_result)
    A->>B: plan(original + run state + tool result)
    B-->>A: tool_call read/search/glob
    A->>T: inspect target files
    T-->>A: code context
    A->>B: plan(with code context)
    B-->>A: tool_call patch_file/write_file
    A->>T: edit file
    T-->>A: patched/wrote file
    A->>A: successful_edit=True
    A->>B: plan(after edit)
    B-->>A: text early
    A->>A: auto diff before final
    A->>T: diff
    T-->>A: git diff 或 snapshot diff
    A->>B: plan(with diff)
    B-->>A: run_tests
    A->>T: run_tests
    T-->>A: exit code 0
    A->>A: successful_test=True
    A->>B: plan(with test result)
    B-->>A: final text
    A->>S: append(assistant_text)
    A->>A: _maybe_compress_history()
    A-->>CLI: AgentResponse
    CLI-->>U: final panel
```

## 15. 文件到职责映射

```mermaid
flowchart LR
    Core["simple_hermes/agent/core.py"] --> C1["task frame"]
    Core --> C2["agent loop"]
    Core --> C3["guard/recovery"]
    Core --> C4["compression"]
    Core --> C5["delegation"]

    Backend["simple_hermes/agent/backend.py"] --> B1["OpenAI compatible HTTP"]
    Backend --> B2["Hermes runtime bridge"]
    Backend --> B3["JSON planner parsing"]

    Prompt["simple_hermes/agent/prompting.py"] --> P1["planner prompt JSON"]
    Prompt --> P2["autonomy rules"]

    Tools["simple_hermes/tools/builtin.py"] --> T1["file tools"]
    Tools --> T2["terminal/tests"]
    Tools --> T3["background tasks"]
    Tools --> T4["session/memory tools"]
    Tools --> T5["delegation tools"]

    Registry["simple_hermes/tools/registry.py"] --> R1["tool registration"]
    Registry --> R2["allowlist gate"]

    Session["simple_hermes/state/session.py"] --> S1["SQLite schema"]
    Session --> S2["lineage/children/continuation"]
    Session --> S3["FTS recall"]
    Session --> S4["session_state active_task"]

    Memory["simple_hermes/state/memory.py"] --> M1["general memory"]
    Memory --> M2["user memory"]
    Memory --> M3["prompt block"]

    CLI["simple_hermes/cli.py"] --> UI1["REPL"]
    CLI --> UI2["project/session detection"]
    CLI --> UI3["streamed progress"]
    CLI --> UI4["status/trace panels"]
    CLI --> UI5["session commands\n/resume /rename /new /reset"]
    CLI --> UI6["runtime commands\n/compress /usage /tool-results /model /background"]
    CLI --> UI7["checkpoint commands\n/checkpoint /rollback /undo"]
```

## 16. Slash session commands

```mermaid
flowchart TD
    Input["用户输入 slash command"] --> Handler["_handle_ui_command()"]
    Handler --> Kind{"命令类型"}

    Kind -->|/resume 无参数| List["_format_recent_sessions()\n展示最近 session 和序号"]
    Kind -->|/resume number| Pick["recent_sessions()[index-1]"]
    Kind -->|/resume latest| Latest["recent_sessions()[0]\n最近会话"]
    Kind -->|/resume project| Project["latest_continuation_or_self(default project session)"]
    Kind -->|/resume session-id| Exact["session_info(session-id)"]
    Pick --> Switch["agent.session_id = target\nagent.last_trace = []"]
    Latest --> Switch
    Project --> Switch
    Exact --> Switch
    Switch --> ResumePanel["Resume panel\nsession id/title/type/message count"]

    Kind -->|/rename title| Rename["SessionStore.rename_session(current, title)"]
    Rename --> RenamePanel["Rename panel\n当前 session + title"]

    Kind -->|/new title| New["ensure_session(project/chat-id)\n切换 agent.session_id"]
    Kind -->|/reset| Reset["clear_session_messages()\n清空 messages + session_state"]
    Kind -->|/compress| Compress["SimpleAgent.compress_now()\nsummary + continuation"]
    Kind -->|/usage| Usage["SessionStore.usage_summary()"]
    Kind -->|/tool-results| ToolResults["SessionStore.recent_tool_results()\n查看存储的 tool output 引用"]
    Kind -->|/model| Model["显示或设置 backend.model"]
    Kind -->|/background prompt| Background["start_background_agent()\nchild session + daemon thread"]
    Kind -->|/retry| Retry["last_user_message()\n重新进入 run()"]
    Kind -->|/checkpoint| Checkpoint["CheckpointStore.create/list"]
    Kind -->|/rollback 或 /undo| Rollback["CheckpointStore.restore()"]
```

## 17. Checkpoint / rollback

```mermaid
flowchart TD
    Edit["write_file / patch_file"] --> Auto["自动 checkpoint\nreason=before edit"]
    Manual["/checkpoint 或 checkpoint create"] --> Create["CheckpointStore.create()"]
    Auto --> Create
    Create --> Scan["扫描 project_root 文本文件\n跳过 .git/node_modules/venv/binary"]
    Scan --> Store["~/.simple_hermes_codex/checkpoints/<project-hash>/cp-*.json"]
    User["/rollback latest 或 /undo"] --> Restore["CheckpointStore.restore()"]
    Restore --> Files["恢复 checkpoint 内文本文件"]
    Restore --> Remove["删除 checkpoint 后新增的可扫描文本文件"]
    Files --> Report["返回 restored / removed 计数"]
    Remove --> Report
```

## 18. HTML test report

```mermaid
flowchart LR
    Runner["scripts/run_tests_with_results.py"] --> Run["python -m unittest discover -s tests -v"]
    Run --> Log["unittest-<stamp>.log"]
    Run --> Parse["parse_unittest_output()"]
    Parse --> JSON["unittest-<stamp>.json\nper-test status"]
    Parse --> HTML["unittest-<stamp>.html\nper-test table + raw log"]
    Runner --> RenderLog["--render-log existing-trace.txt"]
    RenderLog --> ParseStep["parse_trace_steps()"]
    ParseStep --> TraceHTML["existing-trace.html\nstreamed steps + raw log"]
```
