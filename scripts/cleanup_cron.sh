#!/bin/bash
# GoldBrick 服务器定期磁盘清理脚本
#
# 用途：建议配成 crontab 每周跑一次；同步任务日志、pm2 日志、pip 缓存、
#       10 年外的历史数据一次性清掉，保持磁盘在稳定水位。
#
# 典型 crontab（每周日凌晨 4:30）：
#   30 4 * * 0 /bin/bash /opt/goldbrick/scripts/cleanup_cron.sh >> /var/log/goldbrick-cleanup.log 2>&1
#
# ⚠️ 本脚本不跑 VACUUM（那会锁数据库，需要停后端）。要回收磁盘体积请手动跑：
#    sudo bash /opt/goldbrick/scripts/vacuum_db.sh

set -e
APP_DIR="/opt/goldbrick"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "[$(ts)] $*"; }

say "=== GoldBrick cleanup start ==="
df -h / | sed 's/^/[before] /'

# ── 1) pm2 日志：有 pm2-logrotate 最好，没有就 flush 保底 ─────────
if command -v pm2 >/dev/null 2>&1; then
    pm2 flush 2>/dev/null || true
    say "pm2 flushed"
fi

# ── 2) 应用同步日志：保留 30 天 ───────────────────────────
find "$APP_DIR/backend/logs" -type f -mtime +30 -delete 2>/dev/null || true
say "old sync logs (>30d) removed"

# ── 3) 系统缓存：pip / npm / apt ─────────────────────────
rm -rf /root/.cache/pip /root/.cache/npm /root/.npm 2>/dev/null || true
apt-get clean -q 2>/dev/null || true
say "pip/npm/apt caches cleaned"

# ── 4) 10 年外历史数据 DELETE（不 VACUUM，运行期非阻塞）──────────
if [ -x "$APP_DIR/backend/.venv/bin/python" ]; then
    cd "$APP_DIR/backend"
    "$APP_DIR/backend/.venv/bin/python" - <<'PY' || say "WARN python prune failed"
from app.services.data_retention import prune_history_older_than
from app.database import SessionLocal
db = SessionLocal()
try:
    out = prune_history_older_than(db, years=10)
    print("prune:", out)
finally:
    db.close()
PY
    say "history prune (years=10) done"
else
    say "WARN backend .venv not found, skip db prune"
fi

df -h / | sed 's/^/[after] /'
say "=== GoldBrick cleanup done ==="
