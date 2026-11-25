"""Pydantic 请求/响应模型（W2 切片）。

错误信封 {code, message, trace_id} 对应设计稿 §7.3；
harvest-task 与 auth 的载荷对应 §7.2 控制台契约。
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(v: str) -> str:
    if not EMAIL_RE.match(v):
        raise ValueError("邮箱格式不合法")
    return v


class ApiError(BaseModel):
    """统一错误信封。trace_id 由请求中间件生成、异常处理器回填。"""

    code: str
    message: str
    trace_id: Optional[str] = None


# ---------- 认证 ----------

class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="guest", pattern="^(admin|operator|auditor|researcher|guest)$")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _validate_email(v)


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _validate_email(v)


class AuthOut(BaseModel):
    email: str
    role: str
    api_key: str


class MeOut(BaseModel):
    email: str
    role: Optional[str]
    download_quota: int
    req_quota: int


# ---------- 采集任务控制台 ----------

class HarvestTaskCreate(BaseModel):
    data_source_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    schedule_cron: str = Field(default="", max_length=64)
    retry_max: int = Field(default=3, ge=0, le=99)


class HarvestTaskPatch(BaseModel):
    status: str = Field(pattern="^(idle|running|failed|paused)$")


class HarvestTaskOut(BaseModel):
    id: int
    data_source_id: int
    node_id: int
    schedule_cron: str
    retry_max: int
    retry_count: int
    last_failure_class: Optional[str]
    status: str
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class HarvestTaskListOut(BaseModel):
    items: list[HarvestTaskOut]
    total: int


# ---------- STAC 检索（W3） ----------

class StacSearchIn(BaseModel):
    """item-search 子集：collections / ids / bbox / datetime / limit。"""

    collections: Optional[list[str]] = None
    ids: Optional[list[str]] = None
    bbox: Optional[list[float]] = Field(default=None, min_length=4, max_length=6)
    datetime: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)

    def to_search_body(self) -> dict:
        body: dict = {"limit": self.limit}
        if self.collections:
            body["collections"] = self.collections
        if self.ids:
            body["ids"] = self.ids
        if self.bbox:
            body["bbox"] = self.bbox
        if self.datetime:
            body["datetime"] = self.datetime
        return body


# ---------- 编目批次控制台（W3） ----------

class CatalogBatchCreate(BaseModel):
    collection_id: str = Field(min_length=1, max_length=64)


class CatalogBatchAdvance(BaseModel):
    target: str = Field(pattern="^(草稿|字段校验|预览|发布|归档)$")


class CatalogBatchOut(BaseModel):
    id: int
    batch_no: str
    scope: dict
    publish_state: str
    version: int
    parent_batch_id: Optional[int]
    item_count: Optional[int]

    class Config:
        from_attributes = True


class CatalogBatchListOut(BaseModel):
    items: list[CatalogBatchOut]
    total: int


# ---------- 审计与检测规则控制台（W4） ----------

class AuditLogOut(BaseModel):
    id: int
    user_id: Optional[int]
    operation: str
    resource: Optional[str]
    result: Optional[str]
    detail: dict
    trace_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListOut(BaseModel):
    items: list[AuditLogOut]
    total: int


class DetectionRuleOut(BaseModel):
    id: int
    rule_type: str
    params: dict
    severity: str
    action: str
    version: int
    enabled: bool

    class Config:
        from_attributes = True


class DetectionRuleListOut(BaseModel):
    items: list[DetectionRuleOut]
    total: int


class DetectionRulePatch(BaseModel):
    enabled: bool



# ---------- 存储分层与生命周期控制台（W5） ----------

class TransitionIn(BaseModel):
    target_tier: str = Field(pattern="^(热|温|冷)$")
    bucket: str = Field(min_length=1, max_length=128)
    after_days: int = Field(ge=0, le=36500)


class LifecyclePolicyIn(BaseModel):
    transitions: list[TransitionIn] = Field(min_length=1)


class StorageBucketOut(BaseModel):
    id: int
    name: str
    tier: str
    lifecycle_policy: dict
    quota_bytes: int
    used_bytes: int
    versioning_enabled: bool
    object_lock_days: Optional[int]
    watermark_ratio: Optional[float]
    watermark_status: str

    class Config:
        from_attributes = True


class StorageBucketListOut(BaseModel):
    items: list[StorageBucketOut]
    total: int


class LifecycleRunOut(BaseModel):
    migrated: int
    transitions: dict
    policy_failures: list[str]


class WatermarkBucketOut(BaseModel):
    bucket: str
    tier: str
    used_bytes: int
    quota_bytes: int
    ratio: Optional[float]
    status: str


class WatermarkStatusOut(BaseModel):
    alert_ratio: float
    buckets: list[WatermarkBucketOut]


class WatermarkCheckOut(BaseModel):
    """巡检结果：当前告警中的桶 + 本次是否新落了审计记录。"""

    alerts: list[WatermarkBucketOut]
    audited: bool
