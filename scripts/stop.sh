#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT_DIR/.run/backend.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "未找到后端 PID 文件，无需停止。"
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true
  echo "已停止后端进程: $PID"
else
  echo "后端进程已不存在。"
fi

rm -f "$PID_FILE"
