"""Alembic 环境：连接串来自 app.config.settings（即 DATABASE_URL 环境变量）。"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 脚本（alembic upgrade head --sql）。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连数据库执行迁移。

    Postgres 连接经 connect_args 显式锁 search_path=public：pgstac 镜像会把
    数据库级 search_path 设为 "pgstac, public"，不锁定时无限定 DDL 与
    alembic_version 都会静默落进 pgstac schema（W7 备份演练实测暴露）。
    不把 options 塞进 URL——% 编码会触发 psycopg conninfo 的百分号插值错误。
    """
    from sqlalchemy import create_engine

    connect_args: dict = {}
    if settings.database_url.startswith("postgresql"):
        connect_args["options"] = "-csearch_path=public"
    connectable = create_engine(
        settings.database_url, poolclass=pool.NullPool, connect_args=connect_args
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
