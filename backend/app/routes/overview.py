"""数据总览面板路由（W7 切片：总览面板真实 API）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core.auth import PrincipalDep, require
from ..schema import OverviewOut
from ..services.overview_service import build_overview
from .deps import SessionDep

router = APIRouter(prefix="/api/console/overview", tags=["overview"])


@router.get("")
def get_overview(principal: PrincipalDep, db: SessionDep):
    """总览面板一次拉齐：数据集 / 节点在线率 / 源健康度 / 任务分布 /
    四类失败计数 / 容量水位。要求 overview:读（admin / operator / auditor / researcher）。"""
    require("overview", "读")(principal)
    return OverviewOut.model_validate(build_overview(db))
