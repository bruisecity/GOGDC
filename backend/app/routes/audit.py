"""审计日志查询（设计稿 W4 验收：审计可查）。

GET /api/console/audit-logs —— 多条件过滤 + 分页；
权限：audit:读（admin / operator / auditor）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.auth import PrincipalDep, require
from ..models import AuditLog
from ..schema import AuditLogListOut, AuditLogOut
from .deps import SessionDep

router = APIRouter(prefix="/api/console/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    principal: PrincipalDep,
    db: SessionDep,
    operation: str | None = Query(default=None, max_length=64),
    trace_id: str | None = Query(default=None, max_length=64),
    user_id: int | None = Query(default=None),
    result: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require("audit", "读")(principal)
    query = db.query(AuditLog)
    if operation:
        query = query.filter(AuditLog.operation == operation)
    if trace_id:
        query = query.filter(AuditLog.trace_id == trace_id)
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if result:
        query = query.filter(AuditLog.result == result)
    total = query.count()
    rows = (
        query.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    )
    return AuditLogListOut(
        items=[AuditLogOut.model_validate(r) for r in rows], total=total
    )
