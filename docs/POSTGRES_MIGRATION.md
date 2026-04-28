# PostgreSQL 切换指南（0.0.4-dev 预案）

当前默认仍使用 SQLite（`backend/data/app.db`）。代码已支持 PostgreSQL，但**尚未切换**，等监控出现以下任一情况再考虑切：

- 日志反复出现 `database is locked`（SQLite WAL + 30s timeout 也扛不住）
- 多进程 worker（uvicorn --workers >1）
- 并发用户数 >10

## 切换步骤

### 1. 起一个 PostgreSQL（推荐 Docker）

```bash
docker run -d --name goldbrick-pg \
  -e POSTGRES_USER=goldbrick \
  -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=goldbrick \
  -p 5432:5432 \
  -v goldbrick-pgdata:/var/lib/postgresql/data \
  postgres:16
```

### 2. 安装驱动

```bash
cd backend
.venv/bin/pip install 'psycopg[binary]>=3.1'
# 或经典驱动：.venv/bin/pip install psycopg2-binary
```

连接串格式（二选一）：
- `postgresql+psycopg://goldbrick:change-me@localhost:5432/goldbrick`（psycopg3）
- `postgresql+psycopg2://goldbrick:change-me@localhost:5432/goldbrick`（psycopg2）

### 3. 复制数据

```bash
export SQLITE_URL="sqlite:////$(pwd)/backend/data/app.db"
export POSTGRES_URL="postgresql+psycopg://goldbrick:change-me@localhost:5432/goldbrick"

# 先跑 dry-run 看行数
cd backend
.venv/bin/python ../scripts/migrate_sqlite_to_postgres.py --dry-run

# 确认无误后真实迁移
.venv/bin/python ../scripts/migrate_sqlite_to_postgres.py
```

### 4. 切换 .env

在 `backend/.env` 加一行：

```
DATABASE_URL=postgresql+psycopg://goldbrick:change-me@localhost:5432/goldbrick
```

重启 backend。`app/database.py` 会自动：
- 跳过 SQLite 特定的 PRAGMA（WAL）与 `check_same_thread` 参数
- 所有 `ensure_*` 迁移函数通过 `_add_column_if_missing` / `_table_exists` 的 PG 分支执行
- `migrate_for_user_system` 用 `information_schema.tables` 而非 `sqlite_master`

### 5. 回滚

若 PG 出问题，把 `.env` 里的 `DATABASE_URL` 注释掉（回退到默认 SQLite）重启即可。旧的 `backend/data/app.db` 在整个迁移过程中没被写入，仍是一致状态。

## 已知限制

- **`scripts/migrate_sqlite_to_postgres.py` 只做一次性冷迁移**。迁移进行时不要让 backend 对 SQLite 写入，否则两边会不一致。
- 大表（`bars_daily` 几百万行）用默认 `--batch-size=1000` 约 5~15 分钟。可调大到 `5000`。
- 迁移后序列（主键自增）已 reset 到 `MAX(id)+1`；如果手工写过 id，脚本仍然安全。
- 暂未引入 Alembic；后续 schema 变更仍靠 `app/database.py` 里的 `ensure_*_columns()` 幂等函数。要上 Alembic 时这些函数可留作兜底。
