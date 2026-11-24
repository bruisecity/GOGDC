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
