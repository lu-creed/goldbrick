#!/bin/bash
# GoldBrick 一键启动脚本
# 兼容：macOS / Windows（Git Bash）/ Linux
# 用法：bash start.sh

# 获取脚本所在目录（无论从哪里执行都能找到项目路径）
DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 检测操作系统 ──────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin*)              echo "macos"   ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        Linux*)               echo "linux"   ;;
        *)                    echo "unknown" ;;
    esac
}
OS=$(detect_os)

# 兼容 Windows 上 python3 可能不存在（只有 python）
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "❌ 未找到 Python，请先安装 Python 3.10+"
    exit 1
fi

echo ""
echo "==================================="
echo "  GoldBrick 启动中... [$OS]"
echo "==================================="

# ── 检查并安装前端依赖 ────────────────────────────────────────
if [ ! -d "$DIR/frontend/node_modules" ]; then
    echo ""
    echo "📦 首次运行，正在安装前端依赖（约需 1-2 分钟）..."
    cd "$DIR/frontend" && npm install --registry https://registry.npmmirror.com
    if [ $? -ne 0 ]; then
        echo "❌ 前端依赖安装失败，请检查 npm 是否已安装"
        exit 1
    fi
    echo "✅ 前端依赖安装完成"
fi

# ── 检查并安装后端依赖 ────────────────────────────────────────
if ! $PYTHON -c "import uvicorn" 2>/dev/null; then
    echo ""
    echo "📦 首次运行，正在安装后端依赖（约需 2-3 分钟）..."

    echo "  → 升级 pip..."
    $PYTHON -m pip install --upgrade pip \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn -q

    echo "  → 安装后端依赖包..."
    $PYTHON -m pip install -r "$DIR/backend/requirements.txt" \
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

# ── macOS：用 osascript 打开 Terminal 窗口 ────────────────────
if [ "$OS" = "macos" ]; then
    osascript <<EOF
tell application "Terminal"
    activate
    do script "echo '=== GoldBrick 后端 ===' && cd '$DIR/backend' && $PYTHON -m uvicorn app.main:app --reload --port 8000"
end tell
EOF
    osascript <<EOF
tell application "Terminal"
    do script "echo '=== GoldBrick 前端 ===' && cd '$DIR/frontend' && npm run dev"
end tell
EOF
    sleep 3
    open "http://localhost:5173"

# ── Windows Git Bash：用 start 打开 cmd 窗口 ─────────────────
elif [ "$OS" = "windows" ]; then
    # cygpath 把 Unix 路径转成 Windows 路径，cmd 才能识别
    WIN_DIR=$(cygpath -w "$DIR")
    start "GoldBrick 后端" cmd //k "echo === GoldBrick 后端 === && cd /d \"$WIN_DIR\\backend\" && python -m uvicorn app.main:app --reload --port 8000"
    start "GoldBrick 前端" cmd //k "echo === GoldBrick 前端 === && cd /d \"$WIN_DIR\\frontend\" && npm run dev"
    sleep 5
    # Git Bash 下用 start 打开浏览器
    start "http://localhost:5173"

# ── Linux：优先 gnome-terminal，备用 xterm ────────────────────
elif [ "$OS" = "linux" ]; then
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal --title="GoldBrick 后端" -- bash -c \
            "cd '$DIR/backend' && $PYTHON -m uvicorn app.main:app --reload --port 8000; exec bash"
        gnome-terminal --title="GoldBrick 前端" -- bash -c \
            "cd '$DIR/frontend' && npm run dev; exec bash"
    elif command -v xterm &>/dev/null; then
        xterm -title "GoldBrick 后端" -e \
            "cd '$DIR/backend' && $PYTHON -m uvicorn app.main:app --reload --port 8000" &
        xterm -title "GoldBrick 前端" -e \
            "cd '$DIR/frontend' && npm run dev" &
    else
        echo "⚠️  无法检测终端模拟器，请手动启动："
        echo "   后端: cd $DIR/backend && $PYTHON -m uvicorn app.main:app --reload --port 8000"
        echo "   前端: cd $DIR/frontend && npm run dev"
        exit 0
    fi
    sleep 3
    xdg-open "http://localhost:5173" 2>/dev/null || true

else
    echo "❌ 不支持的操作系统：$OS"
    echo "请手动启动："
    echo "   后端: cd $DIR/backend && $PYTHON -m uvicorn app.main:app --reload --port 8000"
    echo "   前端: cd $DIR/frontend && npm run dev"
    exit 1
fi

echo ""
echo "✅ 已启动！浏览器正在打开 http://localhost:5173"
echo ""
echo "💡 提示："
echo "   - 关闭「GoldBrick 后端」终端 = 停止后端"
echo "   - 关闭「GoldBrick 前端」终端 = 停止前端"
echo "   - 下次直接运行 bash start.sh 即可（无需重新安装依赖）"
echo ""
