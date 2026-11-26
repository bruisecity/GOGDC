"""W2 基线迁移：将 backend/sql 的 DDL 纳入 Alembic 版本管理。

此前（W1）schema 由 docker-entrypoint-initdb.d 与 CI psql 直灌；
W2 起统一收敛到 Alembic——sql/*.sql 仍是唯一事实来源（由
tests/test_schema_sync.py 一致性门禁与 ORM 对齐），本迁移只负责
按顺序执行。后续结构变更一律新增 revision，不再改 001 文件历史。

Revision ID: 0001_w2_baseline
Revises:
Create Date: 2026-11-24
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "0001_w2_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SQL_FILES = ("000_extensions.sql", "001_core_schema.sql")


def upgrade() -> None:
    # 显式锁定 search_path：pgstac 镜像会把数据库级 search_path 设为
    # "pgstac, public"，不加这句时所有无限定 CREATE TABLE 会静默落进 pgstac
    # schema（W7 备份演练在 CI 实测暴露的真实缺陷）。
    op.execute("SET search_path TO public")
    for name in SQL_FILES:
        sql = (SQL_DIR / name).read_text(encoding="utf-8")
        op.execute(sql)


def downgrade() -> None:
    # 基线回滚：按 ORM 元数据逆序删表（扩展与 pgstac schema 保留）
    op.execute("SET search_path TO public")
    for table in reversed(Base.metadata.sorted_tables):
        op.execute(f'DROP TABLE IF EXISTS "{table.name}" CASCADE')
