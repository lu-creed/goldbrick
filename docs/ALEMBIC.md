# 数据库 Schema 迁移（0.0.4-dev 起）

从 0.0.4 起，schema 变更走 **Alembic**。现有的 `app/database.py::ensure_*_columns()` 幂等兜底函数保留
作为启动期"缺列补齐"的最后一道保险，但**正式的 schema 变更应通过 Alembic 提交**。

## 现状

- **Alembic 根目录**：`backend/alembic/`
- **配置文件**：`backend/alembic.ini`（`sqlalchemy.url` 被 `env.py` 覆盖，实际走 `app.database.engine`）
- **迁移目录**：`backend/alembic/versions/`
- **当前 head**：`c2fa9427bcf2 baseline 0.0.4 schema`（空迁移；只是声明"现状 = 起点"）

## 常用命令

所有命令都在 `backend/` 目录下运行，使用 venv 里的 alembic：

```bash
cd backend

# 看当前库停在哪个 revision
.venv/bin/alembic current

# 看所有历史
.venv/bin/alembic history

# 改了 models.py 后自动对比库和模型、生成迁移脚本（检查后再 apply）
.venv/bin/alembic revision --autogenerate -m "add foo column to bar"

# 手写一份空迁移（复杂变更 autogenerate 搞不定时）
.venv/bin/alembic revision -m "custom migration"

# 应用所有未跑的迁移到 head
.venv/bin/alembic upgrade head

# 回退一步
.venv/bin/alembic downgrade -1

# 离线生成 SQL（用于生产库 DBA 审阅）
.venv/bin/alembic upgrade head --sql > migration.sql
```

## 首次引入（已完成）

现有部署的 SQLite 库已经 `stamp head`，Alembic 视其为"已在 head 版本"。之后只增量跑新的 revision。

## 新环境首次启动的处理

两种路径，**任选其一**：

### 路径 A — 保持现状：`create_all` + `ensure_*_columns()` + `stamp head`

```bash
# 启动 app（会自动 create_all 建表 + 运行 ensure_* 函数补齐列）
./start.sh

# 启动后单独 stamp Alembic 版本，让后续 upgrade 命令对齐
cd backend && .venv/bin/alembic stamp head
```

这条路径适合本地开发 / 快速搭建。缺点是 `create_all` 和 Alembic 的真相源分叉。

### 路径 B — 纯 Alembic 管理

```bash
# 不跑 app，先从零迁移到 head
cd backend && .venv/bin/alembic upgrade head

# 再启动 app（create_all 对已存在的表会跳过）
./start.sh
```

推荐在 PostgreSQL 切换（见 `docs/POSTGRES_MIGRATION.md`）后改走路径 B，届时所有 schema
变更来源统一为 Alembic revision 文件。

## 与 ensure_*_columns() 的分工

短期共存：

- `ensure_*_columns()`：启动期兜底，防止升级 app 代码后老库缺列启动失败
- Alembic：正式的 schema 变更流程；review-first

长期目标（PG 切换后）：废除 `ensure_*_columns()`，全部迁移走 Alembic。过渡期新增列先在
`ensure_*_columns()` 写一次兜底，同时用 `alembic revision --autogenerate` 生成正式迁移。

## 已识别的历史 schema 漂移

`alembic revision --autogenerate` 首次跑出来检测到：

- 3 个废弃表需要 drop：`adj_factor_daily`、`bars_adj_factor`、`adj_factors`（已被 `adj_factors_daily` 取代）
- `adj_factors_daily.adj_factor` 列类型从 `NUMERIC(20,8)` 对齐到 `NUMERIC(18,8)`
- `sync_runs.pause_requested / cancel_requested` 的 server_default 与模型声明不完全一致

这些都是老库遗留，**没放进 baseline**（避免首次引入误 drop）。下次动手清理时：

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "cleanup legacy adj tables"
# 人工审阅生成的脚本，确认 drop 的表是真的废弃，再 apply
.venv/bin/alembic upgrade head
```
