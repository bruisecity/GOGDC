"""W7 压测启动器：SQLite 文件库 + 单连接线程安全引擎 + uvicorn。

用法（后台启动）：
  PYTHONPATH=.ci_deps:. setsid nohup python3 scripts/dev_server.py \
    > var/uvicorn.log 2>&1 &
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite:///./var/loadtest.db"
os.environ["STORAGE_DIR"] = "./var/storage"

from sqlalchemy import BigInteger, SmallInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    return "INTEGER"


@compiles(SmallInteger, "sqlite")
def _smallint_as_integer(type_, compiler, **kw):
    return "INTEGER"


from app import db as db_module

engine = create_engine(
    "sqlite:///./var/loadtest.db",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=16,
    max_overflow=8,
)


from sqlalchemy import event


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, record):
    """WAL + busy_timeout：多线程并发读写不被共享锁卡死。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
db_module.engine = engine
db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
