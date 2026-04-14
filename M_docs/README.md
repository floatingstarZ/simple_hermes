# Simple Hermes Codex 图集

这个目录集中保存 Simple Hermes Codex 的架构图、流程图和实现细节图。根目录不再放图，后续新增或更新图都放在这里。

## 可直接查看的 SVG

- [总体架构](simple-hermes-architecture.svg)
- [请求时序](simple-hermes-request-sequence.svg)
- [长期会话、上下文压缩与后台任务](simple-hermes-session-compression-background.svg)
- [连续性与 delegation](simple-hermes-continuity-and-delegation.svg)
- [并行 delegation](simple-hermes-parallel-delegation.svg)
- [Backend 模式](simple-hermes-backend-modes.svg)
- [Simple Hermes vs Full Hermes](simple-hermes-vs-full-hermes.svg)

## 可编辑源文件

- [总体架构 Excalidraw](simple-hermes-architecture.excalidraw)
- [请求时序 Excalidraw](simple-hermes-request-sequence.excalidraw)
- [长期会话、上下文压缩与后台任务 Excalidraw](simple-hermes-session-compression-background.excalidraw)
- [连续性与 delegation Excalidraw](simple-hermes-continuity-and-delegation.excalidraw)
- [并行 delegation Excalidraw](simple-hermes-parallel-delegation.excalidraw)
- [Backend 模式 Excalidraw](simple-hermes-backend-modes.excalidraw)
- [Simple Hermes vs Full Hermes Excalidraw](simple-hermes-vs-full-hermes.excalidraw)

## 实现细节图

- [IMPLEMENTATION_DETAILS.md](IMPLEMENTATION_DETAILS.md)

该文件包含更细粒度的 Mermaid 图，覆盖：

- CLI 到 agent loop 的实时 step 输出链路
- backend planner 和 tool registry 的决策路径
- 代码修改任务中的 guard / recovery / diff / test 闭环
- session、memory、active task 和 continuation 的持久化关系
- background task 生命周期
- delegation / parallel delegation 的 session 拓扑
- benchmark harness 的测试路径

## 重新生成 SVG

```bash
python3 render_diagrams.py
```

脚本会读取本目录下的 `*.excalidraw`，并在同目录生成同名 `*.svg`。
