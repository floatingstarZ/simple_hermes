# Simple Hermes Codex

Simple Hermes Codex 是一个面向代码 Agent 实验的轻量项目。它参考 Hermes 的核心思路，但不是完整 Hermes 的复刻。这个项目的目标是把 Agent loop、工具层、会话存储、上下文压缩、后台任务和 benchmark harness 放在一个足够小、足够可读的代码库里，方便逐步改成一个更通用的 code agent。

当前版本已经不只是一次性的工具调用 demo。它可以解析项目、读写代码、运行测试、保留项目级长期会话、把长上下文压缩成 continuation session，并支持后台运行较长的 shell 任务。

## 当前能力

- 多步 `SimpleAgent` loop：支持 backend planning 和显式 tool call。
- 规则模式 fallback：没有 LLM backend 时仍可用于学习和显式命令调试。
- 可选 OpenAI-compatible backend。
- 可选 Hermes runtime bridge：复用 Hermes 的 provider/auth 解析。
- 代码工具：`read`、`read_lines`、`tree`、`glob`、`project_overview`、`search`、`write_file`、`patch_file`、`diff`、`terminal`、`run_tests`。
- 后台任务工具：`background start/list/status/tail/wait/stop`。
- 持久化 memory：general memory 和 user profile memory 分开存。
- SQLite 会话历史：记录 `kind`、`tool_name`、session lineage、descendants 和 focused recall。
- 长期会话：默认按 project root 生成稳定 session id，并自动恢复最新 continuation。
- 上下文压缩：结构化 handoff summary，包含 goal、constraints、progress、files、remaining work。
- delegation 骨架：child sessions、child-agent summary、parallel delegation、depth limits、child tool restrictions。
- 可配置权限与工具 allowlist。
- Code-agent benchmark fixtures：覆盖单轮、多轮、JavaScript、Python、多文件修改任务。
- 测试 runner：把本地 unittest 日志保存到 `test_results/`。

## 非目标

- 不是生产级 sandbox。
- 没有实现 MCP、browser automation、gateway adapters、cron、完整 provider fallback orchestration。
- 默认权限偏宽松，适合本机实验；在不可信项目上使用前应先打开 restrictive 配置。

## 图

图的源文件是根目录下的 Excalidraw JSON，SVG companion 由 `python3 render_diagrams.py` 批量生成。

- [总体架构](simple-hermes-architecture.svg)
- [请求时序](simple-hermes-request-sequence.svg)
- [长期会话、上下文压缩与后台任务](simple-hermes-session-compression-background.svg)
- [连续性与 delegation](simple-hermes-continuity-and-delegation.svg)
- [并行 delegation](simple-hermes-parallel-delegation.svg)
- [Backend 模式](simple-hermes-backend-modes.svg)
- [Simple Hermes vs Full Hermes](simple-hermes-vs-full-hermes.svg)

## 项目结构

- `simple_hermes/agent/core.py`：核心 Agent loop、历史使用、上下文压缩、delegation。
- `simple_hermes/agent/backend.py`：OpenAI-compatible planner backend 和 Hermes runtime bridge。
- `simple_hermes/agent/prompting.py`：planner prompt builder。
- `simple_hermes/tools/registry.py`：最小 tool registry。
- `simple_hermes/tools/builtin.py`：内置工具、代码工具、测试/终端工具、后台任务、治理钩子。
- `simple_hermes/state/memory.py`：general memory 与 user memory。
- `simple_hermes/state/session.py`：SQLite session history、lineage、continuation、recall。
- `simple_hermes/state/continuity.py`：只读 continuity browser helper。
- `simple_hermes/cli.py`：REPL CLI、project-root 检测、session 选择、status/trace 展示。
- `benchmarks/code_agent_tasks.json`：benchmark task 定义。
- `fixtures/`：benchmark 项目模板。
- `scripts/run_code_agent_benchmark.py`：benchmark harness。
- `scripts/run_tests_with_results.py`：保存日志和 JSON 摘要的 unittest runner。
- `tests/`：单元测试和回归测试。

为了降低 import 心智负担，项目保留了一些兼容 shim：

- `simple_hermes/agent.py`
- `simple_hermes/backend.py`
- `simple_hermes/prompting.py`
- `simple_hermes/tools.py`
- `simple_hermes/memory.py`
- `simple_hermes/session.py`

## 安装与运行

当前本机使用 wrapper 方式安装命令：

```bash
cd /Users/hzy/Desktop/work/simple_hermes_codex
simple_hermes_codex
```

本机 wrapper 位于 `/Users/hzy/.local/bin/simple_hermes_codex`，指向这个 checkout，并复用主 Hermes runtime 环境。`pyproject.toml` 里也暴露了同名 console script，便于之后走 venv 或 pipx 风格安装。

常用环境变量：

```bash
export SIMPLE_HERMES_PROJECT_ROOT=/path/to/project
export SIMPLE_HERMES_MAX_STEPS=300
```

## Planner 模式

规则 fallback 模式：

- 不需要 API key。
- 适合读控制流、测显式命令、做最小功能验证。

OpenAI-compatible 模式：

```bash
export SIMPLE_HERMES_BACKEND=openai
export SIMPLE_HERMES_BASE_URL=<openai-compatible-base-url>
export SIMPLE_HERMES_API_KEY=<api-key>
export SIMPLE_HERMES_MODEL=<model-name>
export SIMPLE_HERMES_API_MODE=chat_completions
```

Hermes runtime bridge 模式：

```bash
export SIMPLE_HERMES_BACKEND=hermes-runtime
export SIMPLE_HERMES_HERMES_ROOT=/Users/hzy/Desktop/work/hermes-agent
export SIMPLE_HERMES_PROVIDER=<provider>
export SIMPLE_HERMES_MODEL=<model>
```

runtime bridge 会尝试复用原 Hermes 的 provider resolution 和 auth setup。若不显式设置 `SIMPLE_HERMES_SESSION_ID`，CLI 会使用项目级稳定 session id，并自动恢复该项目最新 continuation。

## 长期会话与上下文压缩

默认 CLI session id 来自 project root：

```text
project-<sha1(project_root)[:12]>
```

当当前 session 的消息数量超过压缩阈值后，`SimpleAgent` 会：

1. 写入结构化 handoff summary。
2. 创建 continuation session。
3. 复制最近几条非 tool-result 的上下文尾部。
4. 将当前 `session_id` 切换到新的 continuation。
5. 下次 CLI 启动时默认恢复最新 continuation。

相关环境变量：

```bash
export SIMPLE_HERMES_SESSION_ID=manual-session
export SIMPLE_HERMES_COMPRESSION_THRESHOLD=40
```

常用 session 工具：

```text
history
recall active_items
sessions
lineage
descendants
```

## 后台任务

后台任务会在 project root 下运行受保护的 shell command，并捕获 stdout/stderr。任务完成后，结果会作为 `background_result` 写回当前 session history，之后可以通过 `history` 或 `recall` 看到。

例子：

```text
background start python3 -m unittest discover -s tests -v
background list
background status bg1
background tail bg1 40
background wait bg1 300
background stop bg1
```

这个功能主要用于较长的测试、构建或项目命令，避免每次都把整个对话阻塞在一个同步命令上。

## 常用工具命令

显式命令形态的输入会直接进入工具层，即使启用了真实 backend：

```text
read README.md
search SimpleAgent
project_overview
terminal pwd
run_tests
write_file scratch.txt hello
patch_file scratch.txt ::: hello ::: hello again
diff scratch.txt
remember project uses sqlite
remember_user I prefer concise review drafts
delegate read README.md
parallel_delegate read README.md ; summarize this project
```

## CLI 快捷命令

```text
/help      展示 UI-level help
/tips      展示示例 prompt
/status    展示 memory/session/backend 状态
/trace     展示上一轮 agent trace
/clear     清屏并重画 banner
exit       退出
```

如果环境里有 `prompt_toolkit`，CLI 会使用它支持光标移动、编辑和持久 prompt history；否则 fallback 到普通 `input()`。

## 权限配置

默认权限偏宽松，便于本机开发实验。要收紧权限：

```bash
export SIMPLE_HERMES_PERMISSION_PROFILE=restricted
export SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_READS=0
export SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_WRITES=0
export SIMPLE_HERMES_ALLOW_SENSITIVE_READS=0
export SIMPLE_HERMES_ALLOW_SENSITIVE_WRITES=0
export SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL=0
```

工具 allowlist：

```bash
export SIMPLE_HERMES_ALLOWED_TOOLS=help,read,search,run_tests
export SIMPLE_HERMES_CHILD_ALLOWED_TOOLS=help,read
```

需要 approval guard 时：

```bash
export SIMPLE_HERMES_REQUIRE_APPROVAL=delegate
export SIMPLE_HERMES_REQUIRE_APPROVAL=parallel_delegate
export SIMPLE_HERMES_REQUIRE_APPROVAL=all
```

## 测试

运行完整单元测试并保存 trace：

```bash
python3 scripts/run_tests_with_results.py
```

输出文件：

```text
test_results/unittest-<timestamp>.log
test_results/unittest-<timestamp>.json
```

只检查 benchmark harness 和 fixture 初始状态：

```bash
python3 scripts/run_code_agent_benchmark.py
```

运行真实 code-agent benchmark：

```bash
python3 scripts/run_code_agent_benchmark.py --run-agent --timeout 480
```

如果 backend 需要本地代理：

```bash
https_proxy=http://127.0.0.1:7890 \
http_proxy=http://127.0.0.1:7890 \
all_proxy=socks5://127.0.0.1:7890 \
HTTPS_PROXY=http://127.0.0.1:7890 \
HTTP_PROXY=http://127.0.0.1:7890 \
ALL_PROXY=socks5://127.0.0.1:7890 \
python3 scripts/run_code_agent_benchmark.py --run-agent --timeout 480
```

benchmark trace 会保存在：

```text
benchmark_runs/<run-id>/traces/
```

## 和 Full Hermes 的对应关系

- `AIAgent` -> `simple_hermes.agent.SimpleAgent`
- `tools/registry.py` -> `simple_hermes.tools.ToolRegistry`
- `memory_tool.py` -> `simple_hermes.memory.MemoryStore`
- `hermes_state.py` -> `simple_hermes.session.SessionStore`
- `prompt_builder.py` -> `simple_hermes.prompting.build_planner_prompt`
- provider client layer -> `simple_hermes.backend.OpenAICompatibleBackend`
- runtime provider bridge -> `simple_hermes.backend` Hermes runtime bridge
- CLI -> `simple_hermes.cli`

## 建议 smoke test

```text
/status
project_overview
read README.md
background start python3 -m unittest discover -s tests -v
background status bg1
background wait bg1 300
/trace
```

这组命令会覆盖 project detection、工具执行、后台任务 capture、session history 和 trace rendering。
