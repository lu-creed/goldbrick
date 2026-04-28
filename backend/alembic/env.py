"""Alembic 迁移环境：与 app.database 共享 engine 和 Base.metadata。

设计要点：
- 不读 alembic.ini 的 sqlalchemy.url，而是直接用 app.database.engine
  → 自动跟随 .env 的 DATABASE_URL（SQLite / PostgreSQL 通吃）
- target_metadata 指向 Base.metadata
  → autogenerate 可以对比当前库 schema vs 模型定义生成迁移
- 开机期仍走 database.ensure_*_columns() 幂等迁移
  → Alembic 只负责"有控制的 schema 变更"（新增/重命名等），不是启动硬依赖
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# 让本文件能 import 到 app.*（alembic 从 backend/ 目录运行，app 是同级包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, engine  # noqa: E402
# 导入 models 让所有 ORM 类都注册到 Base.metadata
from app import models  # noqa: F401,E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：不连库，只生成 SQL 脚本（alembic upgrade --sql）。"""
    url = str(engine.url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 对许多 ALTER TABLE 不友好，开启 batch 模式让 Alembic 自动走 copy-rename
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接用 app.database 的 engine 连接目标库。"""
    is_sqlite = str(engine.url).startswith("sqlite")
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite 用 batch 模式保证 ALTER COLUMN/DROP COLUMN 等也能正常执行
            render_as_batch=is_sqlite,
            compare_type=True,        # 检测列类型变更
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
