#!/bin/bash
# GoldBrick 服务器部署脚本
# 首次运行：自动安装依赖、拉取代码、启动服务
# 再次运行：拉取最新代码、重新构建、重启服务
# 用法：bash deploy.sh

set -e  # 任何命令失败就停止

REPO="https://github.com/lu-creed/goldbrick.git"
APP_DIR="/opt/goldbrick"
BACKEND_PORT=8000
FRONTEND_PORT=3000

echo ""
echo "======================================="
echo "  GoldBrick 部署脚本"
echo "======================================="

# -----------------------------------------------
# 工具函数
# -----------------------------------------------
info()    { echo "[INFO] $*"; }
success() { echo "[OK]   $*"; }
error()   { echo "[ERR]  $*"; exit 1; }

# -----------------------------------------------
# 第一步：检测系统 & 安装系统依赖（首次）
# -----------------------------------------------
install_system_deps() {
    info "检测操作系统..."
    if [ -f /etc/debian_version ]; then
        info "Ubuntu/Debian 系统，安装依赖..."
        apt-get update -q
        apt-get install -y -q git python3 python3-pip python3-venv curl
    elif [ -f /etc/redhat-release ]; then
        info "CentOS/RHEL 系统，安装依赖..."
        yum install -y git python3 python3-pip curl
    else
        error "不支持的操作系统，请手动安装 git / python3 / pip"
    fi

    # 安装 Node.js（如果没有）
    if ! command -v node &>/dev/null; then
        info "安装 Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null || \
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - 2>/dev/null
        apt-get install -y nodejs 2>/dev/null || yum install -y nodejs 2>/dev/null
    fi

    # 安装 pm2（进程管理，保持服务后台运行）
    if ! command -v pm2 &>/dev/null; then
        info "安装 pm2..."
        npm install -g pm2 --registry https://registry.npmmirror.com -q
    fi

    success "系统依赖就绪"
}

# -----------------------------------------------
# 第二步：拉取代码（首次 clone，后续 pull）
# -----------------------------------------------
fetch_code() {
    if [ ! -d "$APP_DIR/.git" ]; then
        info "首次部署，拉取代码..."
        git clone "$REPO" "$APP_DIR"
    else
        info "更新代码..."
        cd "$APP_DIR"
        git pull origin main
    fi
    success "代码已是最新"
}

# -----------------------------------------------
# 第三步：后端依赖
# -----------------------------------------------
setup_backend() {
    info "配置后端依赖..."
    cd "$APP_DIR/backend"

    # 首次：创建虚拟环境
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi

    source .venv/bin/activate
    pip install -q --upgrade pip \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn

    pip install -q -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn

    deactivate
    success "后端依赖就绪"
}

# -----------------------------------------------
# 第四步：前端构建
# -----------------------------------------------
build_frontend() {
    info "构建前端..."
    cd "$APP_DIR/frontend"

    npm install --registry https://registry.npmmirror.com -q
    npm run build

    # 安装静态文件服务器
    if ! command -v serve &>/dev/null; then
        npm install -g serve --registry https://registry.npmmirror.com -q
    fi

    success "前端构建完成"
}

# -----------------------------------------------
# 第五步：启动 / 重启服务
# -----------------------------------------------
start_services() {
    info "启动服务..."

    # 后端
    pm2 delete goldbrick-backend 2>/dev/null || true
    pm2 start "$APP_DIR/backend/.venv/bin/uvicorn" \
        --name goldbrick-backend \
        --cwd "$APP_DIR/backend" \
        -- app.main:app --host 0.0.0.0 --port $BACKEND_PORT

    # 前端
    pm2 delete goldbrick-frontend 2>/dev/null || true
    pm2 start serve \
        --name goldbrick-frontend \
        -- -s "$APP_DIR/frontend/dist" -l $FRONTEND_PORT

    # 设置开机自启（仅首次有效，幂等）
    pm2 save
    pm2 startup 2>/dev/null | tail -1 | bash 2>/dev/null || true

    success "服务已启动"
}

# -----------------------------------------------
# 主流程
# -----------------------------------------------
FIRST_RUN=false
[ ! -d "$APP_DIR/.git" ] && FIRST_RUN=true

if $FIRST_RUN; then
    install_system_deps
fi

fetch_code
setup_backend
build_frontend
start_services

# -----------------------------------------------
# 完成
# -----------------------------------------------
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "你的服务器IP")
echo ""
echo "======================================="
echo "  部署完成！"
echo "  前端：http://$SERVER_IP:$FRONTEND_PORT"
echo "  后端：http://$SERVER_IP:$BACKEND_PORT"
echo ""
echo "  查看日志：pm2 logs"
echo "  查看状态：pm2 status"
echo "  重启服务：bash $APP_DIR/scripts/deploy.sh"
echo "======================================="
echo ""
