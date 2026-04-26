#!/bin/bash
# GoldBrick 服务器部署脚本
# 首次运行：自动安装依赖、拉取代码、配置 Nginx、启动服务
# 再次运行：拉取最新代码、重新构建、重启服务
# 用法：sudo bash deploy.sh

set -e

REPO="https://mirror.ghproxy.com/https://github.com/lu-creed/goldbrick.git"
APP_DIR="/opt/goldbrick"
BACKEND_PORT=8000
NGINX_PORT=80

echo ""
echo "======================================="
echo "  GoldBrick 部署脚本"
echo "======================================="

info()    { echo "[INFO] $*"; }
success() { echo "[OK]   $*"; }
error()   { echo "[ERR]  $*"; exit 1; }

# -----------------------------------------------
# 第一步：安装系统依赖（首次）
# -----------------------------------------------
install_system_deps() {
    info "检测操作系统..."
    if [ -f /etc/debian_version ]; then
        info "Ubuntu/Debian，安装依赖..."
        apt-get update -q
        apt-get install -y -q git python3 python3-pip python3-venv curl nginx
    elif [ -f /etc/redhat-release ]; then
        info "CentOS/RHEL，安装依赖..."
        yum install -y git python3 python3-pip curl nginx
    else
        error "不支持的操作系统，请手动安装 git / python3 / pip / nginx"
    fi

    # 安装 Node.js（如果没有）
    if ! command -v node &>/dev/null; then
        info "安装 Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null || \
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - 2>/dev/null
        apt-get install -y nodejs 2>/dev/null || yum install -y nodejs 2>/dev/null
    fi

    # 安装 pm2
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
# 第四步：构建前端
# -----------------------------------------------
build_frontend() {
    info "构建前端..."
    cd "$APP_DIR/frontend"
    npm install --registry https://registry.npmmirror.com -q
    npm run build
    success "前端构建完成"
}

# -----------------------------------------------
# 第五步：配置 Nginx
# 作用：80 端口统一入口，/ 访问前端，/api 转发后端
# -----------------------------------------------
setup_nginx() {
    info "配置 Nginx..."

    cat > /etc/nginx/sites-available/goldbrick <<EOF
server {
    listen $NGINX_PORT;
    server_name _;

    # 前端静态文件
    root $APP_DIR/frontend/dist;
    index index.html;

    # 前端路由（React Router）
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # /api 请求转发到后端
    location /api {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 120s;
    }
}
EOF

    # 启用配置（Ubuntu/Debian 方式）
    ln -sf /etc/nginx/sites-available/goldbrick /etc/nginx/sites-enabled/goldbrick
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

    nginx -t && systemctl reload nginx || systemctl restart nginx
    success "Nginx 配置完成"
}

# -----------------------------------------------
# 第六步：启动 / 重启后端
# -----------------------------------------------
start_services() {
    info "启动后端服务..."

    pm2 delete goldbrick-backend 2>/dev/null || true
    pm2 start "$APP_DIR/backend/.venv/bin/uvicorn" \
        --name goldbrick-backend \
        --cwd "$APP_DIR/backend" \
        -- app.main:app --host 127.0.0.1 --port $BACKEND_PORT

    pm2 save
    pm2 startup 2>/dev/null | tail -1 | bash 2>/dev/null || true

    # 确保 Nginx 开机自启
    systemctl enable nginx 2>/dev/null || true

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

# 无论首次还是更新，确保 nginx 已安装
if ! command -v nginx &>/dev/null; then
    info "未检测到 Nginx，正在安装..."
    if [ -f /etc/debian_version ]; then
        apt-get install -y -q nginx
    elif [ -f /etc/redhat-release ]; then
        yum install -y nginx
    fi
fi

fetch_code
setup_backend
build_frontend
setup_nginx
start_services

# -----------------------------------------------
# 完成
# -----------------------------------------------
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "你的服务器IP")
echo ""
echo "======================================="
echo "  部署完成！"
echo "  访问地址：http://$SERVER_IP"
echo ""
echo "  查看后端日志：pm2 logs goldbrick-backend"
echo "  查看后端状态：pm2 status"
echo "  再次更新部署：sudo bash $APP_DIR/scripts/deploy.sh"
echo "======================================="
echo ""
