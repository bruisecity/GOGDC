"""测试公共夹具：SQLite 内存库 + 会话覆盖 + 种子数据。"""
from __future__ import annotations

import os

# 必须在导入 app 之前覆盖：本地无 psycopg，引擎按 SQLite 创建；
# 生产/CI 由 requirements 安装 psycopg，真实 URL 不受影响。
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, SmallInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    """测试垫片：SQLite 下 BIGINT 主键映射为 INTEGER 才能自增。"""
    return "INTEGER"


@compiles(SmallInteger, "sqlite")
def _smallint_as_integer(type_, compiler, **kw):
    """测试垫片：SMALLINT 主键（role.id）同样需要 INTEGER 别名。"""
    return "INTEGER"

from app.db import get_db
from app.main import app
from app.models import AppUser, Base, DataSource, DatasetCategory, DetectionRule, Node, Role
from app.core.auth import generate_api_key, hash_api_key, hash_password
from app.routes.deps import get_blob_store
from app.services.storage import LocalBlobStore

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

ROLES = ["admin", "operator", "auditor", "researcher", "guest"]


@pytest.fixture()
def db_session():
    """每测试独立的内存库会话（含角色/数据源/节点种子）。"""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = TestingSessionLocal()
    try:
        for code in ROLES:
            session.add(Role(code=code, name=code))
        session.add(DataSource(code="usgs", base_url="https://apps.nationalmap.gov"))
        session.add(Node(code="node-1", org_name="承担单位A", public_egress=True))
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    """TestClient：get_db 覆盖为测试会话。"""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_user(db, email: str, role: str, *, download_quota: int = 0) -> str:
    """直接建用户并返回明文 X-API-Key（不走注册接口，供权限测试用）。"""
    role_row = db.query(Role).filter(Role.code == role).one()
    raw = generate_api_key()
    db.add(
        AppUser(
            email=email,
            password_hash=hash_password("secret-pass-123"),
            role_id=role_row.id,
            api_key_hash=hash_api_key(raw),
            download_quota=download_quota,
        )
    )
    db.commit()
    return raw


@pytest.fixture()
def blob_store(tmp_path):
    """临时目录的本地存储实现。"""
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture()
def category(db_session):
    """测试用数据分类字典行。"""
    row = DatasetCategory(code="elevation", name="高程")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def detection_rules(db_session):
    """默认两条启用规则：元数据完整性 + 病毒载荷（均「隔离」动作）。"""
    meta = DetectionRule(
        rule_type="元数据完整性",
        params={"require_format": True, "min_size_bytes": 1},
        severity="high",
        action="隔离",
    )
    virus = DetectionRule(
        rule_type="病毒载荷",
        params={"suspicious_extensions": [".exe", ".bat", ".sh"], "max_bytes": 100_000_000},
        severity="high",
        action="隔离",
    )
    db_session.add_all([meta, virus])
    db_session.commit()
    return {"meta": meta, "virus": virus}
