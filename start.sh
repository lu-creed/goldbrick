#!/bin/bash
# GoldBrick 一键启动脚本（macOS）
# 用法：bash start.sh
#
# 功能：
#   1. 自动检测并安装前端/后端依赖（首次运行约需 2-3 分钟，之后瞬间完成）
#   2. 打开两个 Terminal 窗口（后端 + 前端），输出各自日志
#   3. 3 秒后自动用浏览器打开 http://localhost:5173

# 获取脚本所在目录（无论从哪里执行都能找到项目路径）
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "==================================="
echo "  GoldBrick 启动中..."
echo "==================================="

# ---- 检查 node_modules（前端依赖） ----
if [ ! -d "$DIR/frontend/node_modules" ]; then
    echo ""
    echo "📦 首次运行，正在安装前端依赖（约需 1-2 分钟）..."
    # 使用淘宝镜像源（国内网络稳定）
    cd "$DIR/frontend" && npm install --registry https://registry.npmmirror.com
    if [ $? -ne 0 ]; then
        echo "❌ 前端依赖安装失败，请检查 npm 是否已安装"
        exit 1
    fi
    echo "✅ 前端依赖安装完成"
fi

# ---- 检查 uvicorn（后端依赖的核心包） ----
# 用 python3 -m pip 而非 pip3，确保装进 miniconda 环境而非系统 Python
# 使用清华镜像源（国内网络稳定，速度快，绕过代理问题）
if ! python3 -c "import uvicorn" 2>/dev/null; then
    echo ""
    echo "📦 首次运行，正在安装后端依赖（约需 2-3 分钟）..."

    # 第一步：升级 pip（当前版本过旧会导致依赖安装失败）
    echo "  → 升级 pip..."
    python3 -m pip install --upgrade pip \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn -q

    # 第二步：安装后端依赖
    # --no-build-isolation：跳过隔离编译环境，直接用 miniconda 已有的 setuptools
    # 解决 greenlet 等 C 扩展包编译时"哈希校验失败"的问题
    echo "  → 安装后端依赖包..."
    python3 -m pip install -r "$DIR/backend/requirements.txt" \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        --no-build-isolation
    if [ $? -ne 0 ]; then
        echo "❌ 后端依赖安装失败，请检查网络或联系开发者"
        exit 1
    fi
    echo "✅ 后端依赖安装完成"
fi

echo ""
echo "🚀 正在启动后端（端口 8000）和前端（端口 5173）..."

# ---- 打开后端 Terminal 窗口 ----
osascript <<EOF
tell application "Terminal"
    activate
    do script "echo '=== GoldBrick 后端 ===' && cd '$DIR/backend' && python3 -m uvicorn app.main:app --reload --port 8000"
end tell
EOF

# ---- 打开前端 Terminal 窗口 ----
osascript <<EOF
tell application "Terminal"
    do script "echo '=== GoldBrick 前端 ===' && cd '$DIR/frontend' && npm run dev"
end tell
EOF

# ---- 等待服务启动后打开浏览器 ----
echo "⏳ 等待服务就绪（约 3 秒）..."
sleep 3
open "http://localhost:5173"

echo ""
echo "✅ 已启动！浏览器正在打开 http://localhost:5173"
echo ""
echo "💡 提示："
echo "   - 关闭「GoldBrick 后端」终端窗口 = 停止后端"
echo "   - 关闭「GoldBrick 前端」终端窗口 = 停止前端"
echo "   - 下次直接运行 bash start.sh 即可（无需重新安装依赖）"
echo ""
