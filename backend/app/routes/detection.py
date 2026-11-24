"""安全检测规则控制台（设计稿 W4：四类规则可查可启停）。

GET /api/console/detection-rules；PATCH /{id}（启停）——
权限：detection:读 / detection:写。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from ..core.auth import PrincipalDep, require
from ..core.errors import NotFoundError
from ..models import DetectionRule
from ..schema import DetectionRuleListOut, DetectionRuleOut, DetectionRulePatch
from .deps import SessionDep

router = APIRouter(prefix="/api/console/detection-rules", tags=["detection"])


@router.get("")
def list_rules(
    principal: PrincipalDep,
    db: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require("detection", "读")(principal)
    total = db.query(DetectionRule).count()
    rows = (
        db.query(DetectionRule)
        .order_by(DetectionRule.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return DetectionRuleListOut(
        items=[DetectionRuleOut.model_validate(r) for r in rows], total=total
    )


@router.patch("/{rule_id}")
def patch_rule(
    rule_id: int,
    payload: DetectionRulePatch,
    principal: PrincipalDep,
    db: SessionDep,
):
    require("detection", "写")(principal)
    rule = db.get(DetectionRule, rule_id)
    if rule is None:
        raise NotFoundError(f"检测规则 {rule_id} 不存在")
    rule.enabled = payload.enabled
    db.add(rule)
    db.commit()
    return DetectionRuleOut.model_validate(rule)
