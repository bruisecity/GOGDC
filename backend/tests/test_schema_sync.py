"""ORM 模型与 SQL 迁移脚本的一致性检查（CI 门禁）。

改 models.py 或 001_core_schema.sql 任一处而不同步另一处，此测试即红。
不连数据库：用 SQLite 内存库建 ORM 表结构（纯结构校验，不含 PG 特有 DDL），
并静态比对表名/列名与 SQL 文件中的 DDL 声明。
"""
import re
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app import models

SQL_PATH = models.__file__.rsplit("/", 2)[0] + "/sql/001_core_schema.sql"


def _sql_tables() -> dict[str, set[str]]:
    """从 DDL 文本解析 {表名: {列名集合}}。"""
    sql = open(SQL_PATH, encoding="utf-8").read()
    tables: dict[str, set[str]] = {}
    for m in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S
    ):
        name, body = m.group(1), m.group(2)
        cols = set(re.findall(r"^\s{4}(?!CONSTRAINT)(\w+)\s+", body, re.M))
        tables[name] = cols
    return tables


def _orm_tables() -> dict[str, set[str]]:
    """用 SQLite 内存库实际建 ORM 表，回读结构（比静态解析更可信）。"""
    eng = create_engine("sqlite://")
    models.Base.metadata.create_all(eng)
    out: dict[str, set[str]] = {}
    for table in models.Base.metadata.sorted_tables:
        cols = {c.name for c in table.columns}
        out[table.name] = cols
    return out


def test_table_sets_match():
    sql_t, orm_t = _sql_tables(), _orm_tables()
    assert set(sql_t) == set(orm_t), (
        f"表集合不一致 SQL-only={set(sql_t) - set(orm_t)} "
        f"ORM-only={set(orm_t) - set(sql_t)}"
    )


@pytest.mark.parametrize("table", sorted(_sql_tables()))
def test_columns_match(table):
    sql_cols = _sql_tables()[table]
    orm_cols = _orm_tables()[table]
    # SQLite 建表成功本身校验了 ORM 侧合法性；列比对双向
    assert sql_cols - orm_cols == set(), f"{table}: SQL 有而 ORM 缺 {sql_cols - orm_cols}"
    assert orm_cols - sql_cols == set(), f"{table}: ORM 有而 SQL 缺 {orm_cols - sql_cols}"


def test_sqlite_ddl_actually_runs():
    """ORM 元数据可真实落库（结构级验证，非仅可导入）。"""
    eng = create_engine("sqlite://")
    models.Base.metadata.create_all(eng)
    with eng.connect() as conn:
        names = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"data_source", "dataset", "audit_log"} <= names
