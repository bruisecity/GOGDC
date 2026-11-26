"""W7 压测种子：SQLite 文件库 + 检测通过数据集 + 本地 blob。

本地沙箱无 Postgres，用 SQLite 启动服务做 API 层压测；
启动服务前先运行本脚本：
  python3 scripts/dev_seed.py
产物：var/loadtest.env（API_KEY / DATASET_ID），供压测脚本读取。
"""
from __future__ import annotations

import io
import os
import secrets

# 必须在导入 app 之前覆盖：本地无 psycopg，引擎按 SQLite 文件库创建
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


from app.models import AppUser, Base, DataSource, DatasetCategory, Node, Role
from app.core.auth import generate_api_key, hash_api_key, hash_password
from app.services.storage import LocalBlobStore

DB_PATH = "sqlite:///./var/loadtest.db"
if os.path.exists("./var/loadtest.db"):
    os.remove("./var/loadtest.db")

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, future=True)
db = Session()

# 角色种子
role_map = {}
for code in ["admin", "operator", "auditor", "researcher", "guest"]:
    role = Role(code=code, name=code)
    db.add(role)
    role_map[code] = role
db.commit()

# 数据源 / 节点 / 分类
db.add(DataSource(code="usgs", base_url="https://apps.nationalmap.gov"))
db.add(Node(code="node-1", org_name="压测节点", public_egress=True))
cat = DatasetCategory(code="elevation", name="高程")
db.add(cat)
db.commit()

# 压测用户（operator，额度 0 = 不限）
raw_key = generate_api_key()
user = AppUser(
    email="loadtest@example.com",
    password_hash=hash_password(secrets.token_urlsafe(16)),
    api_key_hash=hash_api_key(raw_key),
    role_id=role_map["operator"].id,
    download_quota=0,
)
db.add(user)
db.commit()

# 检测通过数据集：4 MiB blob
payload = os.urandom(4 * 1024 * 1024)
store = LocalBlobStore("./var/storage")
key = "w7-loadtest/dataset-1.bin"
store.put(key, payload)

ds = Dataset = None  # noqa
from app.models import Dataset as DatasetModel  # 延迟导入避免遮蔽

dataset = DatasetModel(
    category_id=cat.id,
    format="BIN",
    size_bytes=len(payload),
    license="CC0",
    security_status="检测通过",
    storage_key=key,
)
db.add(dataset)
db.commit()

with io.open("./var/loadtest.env", "w", encoding="utf-8") as f:
    f.write(f"API_KEY={raw_key}\nDATASET_ID={dataset.id}\n")

print(f"seeded: dataset_id={dataset.id} size={len(payload)} bytes")
print(f"api_key={raw_key[:8]}... (full key in var/loadtest.env)")
db.close()
