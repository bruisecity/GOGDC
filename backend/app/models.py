"""SQLAlchemy ORM 模型：第三章「字段级设计（核心实体）」的落库实现。

口径（与文档一致）：
- 主键统一 BIGINT 自增；时间统一 TIMESTAMPTZ（UTC）
- 枚举以 VARCHAR + CHECK 起步；扩展结构用 JSONB
- collection / item 不建表——直接用 pgSTAC 原生结构，附加属性随 STAC JSON 存放

W1 基座范围：全部实体建表（迁移脚本见 backend/sql/001_core_schema.sql，
两者由 CI 一致性检查对齐——见 tests/test_schema_sync.py）。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(128))  # 不存明文凭证
    quota_limit: Mapped[int] = mapped_column(BigInteger, default=0)   # 字节口径
    quota_used: Mapped[int] = mapped_column(BigInteger, default=0)
    health_status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("health_status IN ('healthy','degraded','down')"),
        default="healthy",
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HarvestTask(Base):
    __tablename__ = "harvest_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_source.id"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("node.id"), nullable=False
    )
    schedule_cron: Mapped[str] = mapped_column(String(64), default="")
    retry_max: Mapped[int] = mapped_column(SmallInteger, default=3)
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    last_failure_class: Mapped[str | None] = mapped_column(
        String(32),
        CheckConstraint(
            "last_failure_class IS NULL OR last_failure_class IN "
            "('连接超时','源站限流','凭证失效','元数据校验')"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("status IN ('idle','running','failed','paused')"),
        default="idle",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Node(Base):
    __tablename__ = "node"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    org_name: Mapped[str] = mapped_column(String(128), nullable=False)
    public_egress: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disk_total: Mapped[int] = mapped_column(BigInteger, default=0)  # 字节
    disk_used: Mapped[int] = mapped_column(BigInteger, default=0)
    download_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)


class DatasetCategory(Base):
    """收储分类字典（15 类）。TODO(W1 评审): 类目清单以业务确认为准后补种子。"""

    __tablename__ = "dataset_category"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Dataset(Base):
    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("dataset_category.id"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(16), default="")  # GeoTIFF/CSV/NetCDF…
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    license: Mapped[str | None] = mapped_column(String(64))
    security_status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint(
            "security_status IN ('检测通过','检测中','待检测','已隔离')"
        ),
        default="待检测",
    )
    # bbox 四列，WGS84，[west, south, east, north]
    bbox_west: Mapped[float | None] = mapped_column(Numeric(9, 5))
    bbox_south: Mapped[float | None] = mapped_column(Numeric(9, 5))
    bbox_east: Mapped[float | None] = mapped_column(Numeric(9, 5))
    bbox_north: Mapped[float | None] = mapped_column(Numeric(9, 5))
    temporal_start: Mapped[date | None] = mapped_column(Date)
    temporal_end: Mapped[date | None] = mapped_column(Date)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)  # 与存储路线解耦
    collection_id: Mapped[str | None] = mapped_column(String(64))  # 编目后回填（pgSTAC）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DetectionRule(Base):
    __tablename__ = "detection_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(
        String(32),
        CheckConstraint(
            "rule_type IN ('敏感信息','坐标基准','元数据完整性','病毒载荷')"
        ),
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(
        String(16), CheckConstraint("severity IN ('low','medium','high')")
    )
    action: Mapped[str] = mapped_column(
        String(16), CheckConstraint("action IN ('隔离','放行','标记')")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QuarantineSample(Base):
    __tablename__ = "quarantine_sample"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dataset.id"), nullable=False
    )
    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("detection_rule.id"), nullable=False
    )
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    disposal_state: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint(
            "disposal_state IN ('待处置','人工复核','执行处置','二次确认','归档')"
        ),
        default="待处置",
    )
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CatalogBatch(Base):
    __tablename__ = "catalog_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(32), unique=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    publish_state: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint(
            "publish_state IN ('草稿','字段校验','预览','发布','归档')"
        ),
        default="草稿",
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_batch_id: Mapped[int | None] = mapped_column(BigInteger)
    item_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StorageBucket(Base):
    __tablename__ = "storage_bucket"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    tier: Mapped[str] = mapped_column(
        String(8), CheckConstraint("tier IN ('热','温','冷')")
    )
    lifecycle_policy: Mapped[dict] = mapped_column(
        JSON, default=dict
    )
    quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0)   # 80% 告警线
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    versioning_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    object_lock_days: Mapped[int | None] = mapped_column(Integer)


class StorageObject(Base):
    __tablename__ = "storage_object"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bucket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("storage_bucket.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(1024), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(64))
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("bucket_id", "key", name="uq_storage_object_key"),
    )


class Role(Base):
    """角色字典（5 角色）。TODO(W1 评审): 角色命名与设计稿对齐后定稿。"""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str | None] = mapped_column(String(255), unique=True)
    role_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("role.id"), nullable=False
    )
    download_quota: Mapped[int] = mapped_column(BigInteger, default=0)  # 字节
    req_quota: Mapped[int] = mapped_column(Integer, default=0)          # 频次
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Permission(Base):
    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("role.id"), nullable=False
    )
    resource: Mapped[str] = mapped_column(String(32), nullable=False)  # 8 资源
    action: Mapped[str] = mapped_column(String(8), nullable=False)     # 读/写/审批
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[str | None] = mapped_column(String(16))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
