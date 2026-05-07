#!/bin/bash
# GoldBrick 手动 VACUUM 脚本
#
# VACUUM 需要独占锁数据库，因此会短暂停 pm2 后端（通常 <30 秒）。
# 执行前请确保：(1) 磁盘至少有 1 GB 空余；(2) 用户已知悉服务会短暂不可用。
#
# 用法：sudo bash /opt/goldbrick/scripts/vacuum_db.sh

set -e
APP_DIR="/opt/goldbrick"
DB_PATH="$APP_DIR/backend/data/app.db"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "[$(ts)] $*"; }

[ -f "$DB_PATH" ] || { echo "数据库不存在: $DB_PATH"; exit 1; }

FREE_MB=$(df -Pm / | awk 'NR==2{print $4}')
say "磁盘空余 ${FREE_MB} MB"
if [ "${FREE_MB:-0}" -lt 1024 ]; then
    echo "⚠️ 磁盘空余 < 1GB，VACUUM 可能失败。请先清其他地方再跑。"
    exit 2
fi

say "数据库当前大小:"
ls -lh "$DB_PATH"

say "暂停后端..."
pm2 stop goldbrick-backend 2>/dev/null || say "pm2 stop 跳过（可能未启动）"
sleep 2

say "开始 VACUUM（可能需要几十秒到几分钟）..."
sqlite3 "$DB_PATH" "VACUUM;"
say "VACUUM 完成"

ls -lh "$DB_PATH"

say "重启后端..."
pm2 start goldbrick-backend 2>/dev/null || pm2 restart goldbrick-backend 2>/dev/null || \
    say "⚠️ pm2 未自动启动，请手动 pm2 start"

df -h /
say "=== 完成 ==="
