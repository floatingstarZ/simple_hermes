#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DATE="2026-04-14"
SOURCE_ROOT="/Users/hzy/Desktop/work/DailyTrack_NewTech"
WORK_ROOT=""
LIVE=0
STEPS="${SIMPLE_HERMES_MAX_STEPS:-300}"
TIMEOUT_SECONDS="${DAILYTRACK_TIMEOUT_SECONDS:-3600}"
SESSION_ID=""
TRACE_DIR="$REPO_ROOT/test_results/dailytrack-agent-runs/manual"
COMMAND="${SIMPLE_HERMES_COMMAND:-simple_hermes_codex}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_dailytrack_case.sh [options]

Options:
  --date YYYY-MM-DD       Target DailyTrack date. Default: 2026-04-14
  --source PATH           Source DailyTrack repo. Default: /Users/hzy/Desktop/work/DailyTrack_NewTech
  --workdir PATH          Existing or desired isolated workdir. Default: /tmp/dailytrack_sh_case_<date>_<timestamp>
  --live                  Run directly in --source instead of making an isolated copy.
  --steps N               SIMPLE_HERMES_MAX_STEPS. Default: 300
  --timeout SECONDS       Kill the whole CLI run after this many seconds. Default: 3600
  --session ID            SIMPLE_HERMES_SESSION_ID. Default: dailytrack-case-<date>-<timestamp>
  --trace-dir PATH        Directory for trace/input/html logs.
  -h, --help              Show this help.

Environment:
  SIMPLE_HERMES_COMMAND   Command to run. Default: simple_hermes_codex
  SIMPLE_HERMES_BACKEND   Defaulted to hermes-runtime if unset.
  Proxy env vars          Defaulted to 127.0.0.1:7890 if unset.

The default mode is isolated: the script copies DailyTrack to /tmp and edits that
copy. Use --live only when you intentionally want to modify the real project.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE="$2"
      shift 2
      ;;
    --source)
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --workdir)
      WORK_ROOT="$2"
      shift 2
      ;;
    --live)
      LIVE=1
      shift
      ;;
    --steps)
      STEPS="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --session)
      SESSION_ID="$2"
      shift 2
      ;;
    --trace-dir)
      TRACE_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

timestamp="$(date +%Y%m%d-%H%M%S)"
safe_date="${DATE//[^0-9A-Za-z_-]/-}"
if [[ -z "$SESSION_ID" ]]; then
  SESSION_ID="dailytrack-case-${safe_date}-${timestamp}"
fi

if [[ ! -d "$SOURCE_ROOT" ]]; then
  echo "Source DailyTrack repo not found: $SOURCE_ROOT" >&2
  exit 1
fi

if [[ "$LIVE" -eq 1 ]]; then
  PROJECT_ROOT="$SOURCE_ROOT"
else
  if [[ -z "$WORK_ROOT" ]]; then
    WORK_ROOT="/tmp/dailytrack_sh_case_${safe_date}_${timestamp}"
  fi
  if [[ -e "$WORK_ROOT" ]]; then
    echo "Workdir already exists: $WORK_ROOT" >&2
    echo "Choose another --workdir or remove it manually." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$WORK_ROOT")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude .git \
      --exclude .venv \
      --exclude __pycache__ \
      --exclude .mypy_cache \
      --exclude .pytest_cache \
      "$SOURCE_ROOT/" "$WORK_ROOT/"
  else
    cp -R "$SOURCE_ROOT" "$WORK_ROOT"
  fi
  PROJECT_ROOT="$WORK_ROOT"
fi

mkdir -p "$TRACE_DIR"
input_path="$TRACE_DIR/dailytrack_${safe_date}_${timestamp}_input.txt"
trace_path="$TRACE_DIR/dailytrack_${safe_date}_${timestamp}_trace.txt"
html_path="$TRACE_DIR/dailytrack_${safe_date}_${timestamp}_trace.html"

cat > "$input_path" <<EOF
开始 daily track，目标日期 ${DATE}。

请你先解析本仓库的 AGENTS.md / CLAUDE.md / MEMORY.md 和 skills 工作流，然后按项目约定完成这一天的 DailyTrack。你需要自己决定该使用哪些本地 skill、脚本、网络源和中间文件。

要求：
1. 不要硬编码关键词偏好，优先遵循仓库说明和 skills。
2. 采集 HuggingFace Daily Papers、arXiv、GitHub、HuggingFace Hub、RSS/blog/source pages 中和 LLM RL、RLVR、GRPO、agent、embodied RL、training framework 相关的内容。
3. 对每条候选内容保留来源、日期、标题、链接、简短理由和可信度判断。
4. 最终写入目标日期目录下的 track.md 和 papers.json；如果项目已有命名规范，请遵循已有规范。
5. 长命令和网络采集优先使用 background，不要因为单个源超时就停止整个任务。
6. 如果缺依赖，可以在项目局部环境里安装或创建 .venv。
7. 完成后运行必要的检查，最后汇报写入了哪些文件、主要收录了哪些条目、哪些源失败或为空。

/status
/trace
exit
EOF

export https_proxy="${https_proxy:-http://127.0.0.1:7890}"
export http_proxy="${http_proxy:-http://127.0.0.1:7890}"
export all_proxy="${all_proxy:-socks5://127.0.0.1:7890}"
export HTTPS_PROXY="${HTTPS_PROXY:-$https_proxy}"
export HTTP_PROXY="${HTTP_PROXY:-$http_proxy}"
export ALL_PROXY="${ALL_PROXY:-$all_proxy}"
export SIMPLE_HERMES_BACKEND="${SIMPLE_HERMES_BACKEND:-hermes-runtime}"
export SIMPLE_HERMES_MAX_STEPS="$STEPS"
export SIMPLE_HERMES_SESSION_ID="$SESSION_ID"
export SIMPLE_HERMES_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONUNBUFFERED=1

echo "DailyTrack case"
echo "  date:       $DATE"
echo "  project:    $PROJECT_ROOT"
echo "  live:       $LIVE"
echo "  session:    $SESSION_ID"
echo "  steps:      $SIMPLE_HERMES_MAX_STEPS"
echo "  trace:      $trace_path"
echo "  input:      $input_path"
echo

set +e
if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT_SECONDS" "$COMMAND" < "$input_path" > "$trace_path" 2>&1
else
  echo "[WARN] timeout command not found; running without a hard timeout." > "$trace_path"
  "$COMMAND" < "$input_path" >> "$trace_path" 2>&1
fi
status=$?
set -e

if [[ "$status" -eq 124 ]]; then
  echo "[TIMEOUT] $COMMAND exceeded ${TIMEOUT_SECONDS}s." >> "$trace_path"
fi

if [[ -f "$REPO_ROOT/scripts/run_tests_with_results.py" ]]; then
  python3 "$REPO_ROOT/scripts/run_tests_with_results.py" \
    --render-log "$trace_path" \
    --title "DailyTrack ${DATE} case" \
    --html "$html_path" >/dev/null || true
fi

target_dir="$PROJECT_ROOT/$DATE"
echo
echo "Result"
echo "  exit code:  $status"
echo "  project:    $PROJECT_ROOT"
echo "  trace:      $trace_path"
if [[ -f "$html_path" ]]; then
  echo "  html:       $html_path"
fi
if [[ -d "$target_dir" ]]; then
  echo "  target dir: $target_dir"
  find "$target_dir" -maxdepth 1 -type f -print | sed 's/^/    - /'
else
  echo "  target dir: not created ($target_dir)"
fi

exit "$status"
