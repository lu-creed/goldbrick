#!/bin/bash
# 在服务器上运行：安装依赖、构建、重启服务（不需要 git pull）
# 用法：sudo bash /opt/goldbrick/scripts/server-deploy.sh

set -e

APP_DIR="/opt/goldbrick"
BACKEND_PORT=8000

echo ""
echo "======================================="
echo "  GoldBrick 服务器构建 & 启动"
echo "======================================="

info()    { echo "[INFO] $*"; }
success() { echo "[OK]   $*"; }

# 确保 nginx 已安装
if ! command -v nginx &>/dev/null; then
    info "安装 Nginx..."
    apt-get update -q && apt-get install -y -q nginx
fi

# 确保 pm2 已安装
if ! command -v pm2 &>/dev/null; then
    info "安装 pm2..."
    npm install -g pm2 --registry https://registry.npmmirror.com -q
fi

# 后端依赖
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

# 构建前端
info "构建前端..."
cd "$APP_DIR/frontend"
npm install --registry https://registry.npmmirror.com -q
npm run build
success "前端构建完成"

# 配置 Nginx
info "配置 Nginx..."
cat > /etc/nginx/sites-available/goldbrick <<EOF
server {
    listen 80;
    server_name _;

    root $APP_DIR/frontend/dist;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/goldbrick /etc/nginx/sites-enabled/goldbrick
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && systemctl reload nginx 2>/dev/null || systemctl restart nginx
systemctl enable nginx 2>/dev/null || true
success "Nginx 配置完成"

# 启动后端
info "启动后端..."
pm2 delete goldbrick-backend 2>/dev/null || true
pm2 start "$APP_DIR/backend/.venv/bin/uvicorn" \
    --name goldbrick-backend \
    --cwd "$APP_DIR/backend" \
    -- app.main:app --host 127.0.0.1 --port $BACKEND_PORT
pm2 save
pm2 startup 2>/dev/null | tail -1 | bash 2>/dev/null || true
success "后端已启动"

SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "你的服务器IP")
echo ""
echo "======================================="
echo "  部署完成！访问：http://$SERVER_IP"
echo "  查看日志：pm2 logs goldbrick-backend"
echo "======================================="
