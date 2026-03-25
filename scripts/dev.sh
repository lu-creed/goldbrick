#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUN_DIR="$ROOT_DIR/.run"
PID_FILE="$RUN_DIR/backend.pid"

mkdir -p "$RUN_DIR"

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    BPID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${BPID:-}" ]] && kill -0 "$BPID" 2>/dev/null; then
      kill "$BPID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "检测到后端已在运行（PID=${EXISTING_PID}），请先执行 ./scripts/stop.sh"
    exit 1
  else
    rm -f "$PID_FILE"
  fi
fi

# 1) backend env + deps
if [[ ! -d "$BACKEND_DIR/.venv" ]]; then
  echo "[backend] 创建 Python 虚拟环境..."
  /usr/bin/python3 -m venv "$BACKEND_DIR/.venv"
fi

# shellcheck disable=SC1091
source "$BACKEND_DIR/.venv/bin/activate"

echo "[backend] 安装依赖..."
pip install -q -r "$BACKEND_DIR/requirements.txt"
pip install -q -r "$BACKEND_DIR/requirements-sync.txt"

if [[ ! -f "$BACKEND_DIR/.env" ]]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  echo "[backend] 已自动生成 backend/.env（请按需填写）"
fi

# 2) frontend deps
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "[frontend] 安装依赖..."
  (cd "$FRONTEND_DIR" && npm install)
fi

# 3) 启动 backend（后台）
echo "[backend] 启动中..."
(cd "$BACKEND_DIR" && nohup "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --reload --host 127.0.0.1 --port 8000 > "$RUN_DIR/backend.log" 2>&1 & echo $! > "$PID_FILE")

# 简单健康检查
for _ in {1..40}; do
  if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
  echo "[backend] 启动失败，请查看 $RUN_DIR/backend.log"
  cleanup
  exit 1
fi

echo "[ok] 后端已启动: http://127.0.0.1:8000"
echo "[frontend] 启动开发服务（Ctrl+C 会同时结束后端）..."

trap cleanup EXIT INT TERM
cd "$FRONTEND_DIR"
npm run dev
