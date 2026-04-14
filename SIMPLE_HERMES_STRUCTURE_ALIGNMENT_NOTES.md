# Simple Hermes 结构对齐说明

这次我开始把 `simple_hermes` 的代码组织方式往 Full Hermes 的结构风格上对齐。

目标不是机械复制目录数量，
而是把“概念边界”对齐：
- agent logic 放到 agent/
- tools 放到 tools/
- state persistence 放到 state/
- 顶层模块保留兼容导出，避免已有调用全部断掉

一、现在的新结构

`simple_hermes/` 下面现在有：

- `agent/`
  - `core.py`
  - `backend.py`
  - `prompting.py`
  - `__init__.py`

- `tools/`
  - `registry.py`
  - `builtin.py`
  - `__init__.py`

- `state/`
  - `memory.py`
  - `session.py`
  - `continuity.py`
  - `__init__.py`

同时保留了顶层兼容 shim：
- `agent.py`
- `backend.py`
- `prompting.py`
- `tools.py`
- `memory.py`
- `session.py`

这些顶层文件现在主要是：
- 从新子包 re-export
- 不打断已有导入路径

二、为什么这样对齐

Full Hermes 里最重要的结构感不是“文件多”，
而是“能力分层清楚”：

1. `agent/`
- prompt
- backend/runtime
- execution logic

2. `tools/`
- tool registry
- tool implementations

3. state /
- persistence 相关存储与 continuity helpers
- memory
- session/history
- continuity browsing helpers

现在 `simple_hermes` 也开始按这个方式组织，
所以以后继续增强：
- recall summarizer
- continuation logic
- approval/safety
- richer tools
时，不容易又长回“一坨文件”。

三、兼容性策略

为了不把你现在已经能运行的工程打坏，
我没有粗暴删除旧顶层文件。
而是采用：
- 新结构承载真实实现
- 旧顶层路径做兼容转发

例如：
- `simple_hermes.agent` 仍然能 import
- 但真实实现已经在 `simple_hermes/agent/core.py`

这使得：
- 现有 tests 不需要大规模重写
- CLI 不会突然断掉
- 后面可以逐步把调用迁移到新结构

四、这一步的意义

这次对齐的意义不是“好看一点”，而是：

1. 后续继续对标 Hermes 时，有地方可放
- agent enhancements 放 `agent/`
- tool governance 放 `tools/`
- continuity/state enhancements 放 `state/`

2. 结构更适合教学
- 新手一眼能看出每块职责

3. 后续演进不会越来越乱
- 这是为了让 `simple_hermes` 能持续长成一个小而真的系统，而不是 demo 堆砌

五、一句话总结

这次结构调整的核心思想是：

不是复制 Full Hermes 的复杂度，
而是复制它“分层组织代码”的方式。