#!/bin/bash
# 在本地 Mac 上运行：把代码同步到服务器并触发部署
# 用法：bash scripts/push-to-server.sh
#
# 首次运行前，确保可以 SSH 免密登录服务器：
#   ssh-copy-id ubuntu@101.43.103.216

set -e

SERVER="ubuntu@101.43.103.216"
APP_DIR="/opt/goldbrick"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "======================================="
echo "  推送代码到服务器"
echo "======================================="

# 同步代码（排除不需要上传的大目录）
echo "[INFO] 同步代码..."
rsync -az --progress \
    --exclude 'node_modules' \
    --exclude '.venv' \
    --exclude 'frontend/dist' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'backend/data' \
    --exclude 'backend/logs' \
    "$LOCAL_DIR/" "$SERVER:$APP_DIR/"

echo "[OK]   代码同步完成"

# 在服务器上执行构建和重启
echo "[INFO] 触发服务器部署..."
ssh "$SERVER" "sudo bash $APP_DIR/scripts/server-deploy.sh"
