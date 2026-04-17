#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_REPO="${HERMES_REPO:-/Users/hzy/Desktop/work/hermes-agent}"
DAILYTRACK_REPO="${DAILYTRACK_REPO:-/Users/hzy/Desktop/work/DailyTrack_NewTech}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
TRACE_DIR="${TRACE_DIR:-$ROOT_DIR/runs/$RUN_ID}"
MODEL="${MODEL:-gpt-5.4}"
PROVIDER="${PROVIDER:-openai-codex}"
MAX_TURNS="${MAX_TURNS:-45}"

mkdir -p "$TRACE_DIR"

PROMPT_FILE="$TRACE_DIR/prompt.txt"
cat > "$PROMPT_FILE" <<'PROMPT'
开始 daily track。请按本仓库 AGENTS.md / CLAUDE.md / MEMORY.md 的约定完整执行今天的 DailyTrack 工作流：确定目标日期、读取既有 tracking state、采集 HuggingFace Papers / arXiv / GitHub watchlist / HuggingFace Hub / RSS 或 Anthropic 页面等相关来源，去重筛选 LLM RL、RLVR、GRPO、embodied RL、RL training framework、agent infrastructure 相关内容，写入目标日期目录下的 track.md 和 papers.json，并更新必要的 global 索引/seen 文件。请自己决定需要调用哪些本地 skill、脚本和网络源；完成后汇报写入了哪些文件、主要收录哪些条目、哪些源失败或为空。
PROMPT

(
  cd "$DAILYTRACK_REPO"
  PYTHONPATH="$ROOT_DIR:$HERMES_REPO${PYTHONPATH:+:$PYTHONPATH}" \
  HERMES_OPENAI_CAPTURE_DIR="$TRACE_DIR" \
  HERMES_YOLO_MODE=1 \
  https_proxy="${https_proxy:-http://127.0.0.1:7890}" \
  http_proxy="${http_proxy:-http://127.0.0.1:7890}" \
  HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7890}" \
  HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7890}" \
  hermes --yolo chat \
    -q "$(cat "$PROMPT_FILE")" \
    -Q \
    --provider "$PROVIDER" \
    --model "$MODEL" \
    --max-turns "$MAX_TURNS"
) > "$TRACE_DIR/hermes_stdout.txt" 2>&1

echo "0" > "$TRACE_DIR/exit_code.txt"
echo "$TRACE_DIR"
