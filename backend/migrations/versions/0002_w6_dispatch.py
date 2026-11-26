"""W6 调度策略完善：harvest_task 增加 time_window / last_run_at。

001 已同步为完整快照（新建库直接带这两列）；本 revision 只对已存在的旧库
执行 002 的幂等 ALTER，保证 0001 之后升级的库与 ORM 结构一致。

Revision ID: 0002_w6_dispatch
Revises: 0001_w2_baseline
Create Date: 2026-11-25
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0002_w6_dispatch"
down_revision: Union[str, None] = "0001_w2_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def upgrade() -> None:
    # 与 0001 同理：锁定 search_path，防止无限定 ALTER 落进错误 schema
    op.execute("SET search_path TO public")
    op.execute((SQL_DIR / "002_w6_dispatch.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("SET search_path TO public")
    op.execute("ALTER TABLE harvest_task DROP COLUMN IF EXISTS last_run_at")
    op.execute("ALTER TABLE harvest_task DROP COLUMN IF EXISTS time_window")
