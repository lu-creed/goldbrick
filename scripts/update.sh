#!/bin/bash
# GoldBrick 轻量自动更新脚本
# 由 backend 的 APScheduler 检测到有新 commit 时通过 subprocess.Popen 调用
# 设计为：无需 sudo，独立进程（不受 pm2 restart 影响），完整记录日志
# 流程：git pull → 按需装依赖 → npm run build → pm2 restart goldbrick-backend
set -e

APP_DIR="/opt/goldbrick"
LOG_DIR="$APP_DIR/backend/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/auto-update-$(date +%Y%m%d-%H%M%S).log"

# pm2、node 等通常装在 /usr/local/bin 或 ~/.nvm/…，保证 PATH 找得到
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.nvm/versions/node/current/bin:$PATH"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始自动更新 ==="

    cd "$APP_DIR"

    BEFORE_PKG_HASH=$(md5sum frontend/package.json 2>/dev/null | awk '{print $1}')
    BEFORE_REQ_HASH=$(md5sum backend/requirements.txt 2>/dev/null | awk '{print $1}')

    echo "> git pull origin main"
    GIT_TERMINAL_PROMPT=0 timeout 120 git pull origin main

    AFTER_PKG_HASH=$(md5sum frontend/package.json 2>/dev/null | awk '{print $1}')
    AFTER_REQ_HASH=$(md5sum backend/requirements.txt 2>/dev/null | awk '{print $1}')

    cd "$APP_DIR/frontend"
    if [ "$BEFORE_PKG_HASH" != "$AFTER_PKG_HASH" ]; then
        echo "> package.json 有变更，重装前端依赖"
        npm install --registry https://registry.npmmirror.com
    else
        echo "> package.json 无变更，跳过 npm install"
    fi

    echo "> npm run build"
    npm run build

    cd "$APP_DIR/backend"
    if [ "$BEFORE_REQ_HASH" != "$AFTER_REQ_HASH" ] && [ -d ".venv" ]; then
        echo "> requirements.txt 有变更，重装后端依赖"
        # shellcheck disable=SC1091
        source .venv/bin/activate
        pip install -r requirements.txt \
            -i https://pypi.tuna.tsinghua.edu.cn/simple \
            --trusted-host pypi.tuna.tsinghua.edu.cn
        deactivate
    else
        echo "> requirements.txt 无变更，跳过 pip install"
    fi

    echo "> pm2 restart goldbrick-backend"
    pm2 restart goldbrick-backend

    echo "=== $(date '+%Y-%m-%d %H:%M:%S') 自动更新完成 ==="
} >> "$LOG_FILE" 2>&1
