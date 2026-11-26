"""汇聚节点控制台路由（W7 切片：节点面板真实 API）。

口径：
- 列表按 RBAC node:读 鉴权，逐节点给出在线判定（心跳阈值见 overview_service）；
- 心跳上报按 node:写 鉴权，写库并落 audit_log（中台写操作全审计）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from sqlalchemy import func

from ..core.auth import PrincipalDep, require
from ..core.errors import NotFoundError
from ..models import AuditLog, HarvestTask, Node
from ..schema import NodeHeartbeatIn, NodeHeartbeatOut, NodeListOut, NodeOut
from .deps import SessionDep
from ..services.overview_service import node_online

router = APIRouter(prefix="/api/console/nodes", tags=["nodes"])


def _out(node: Node, task_count: int, *, now: datetime) -> NodeOut:
    return NodeOut(
        id=node.id,
        code=node.code,
        org_name=node.org_name,
        public_egress=bool(node.public_egress),
        heartbeat_at=node.heartbeat_at,
        disk_total=node.disk_total or 0,
        disk_used=node.disk_used or 0,
        download_bytes_total=node.download_bytes_total or 0,
        task_count=task_count,
        online=node_online(node.heartbeat_at, now),
    )


@router.get("")
def list_nodes(
    principal: PrincipalDep,
    db: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require("node", "读")(principal)
    now = datetime.now(timezone.utc)
    total = db.query(Node).count()
    rows = (
        db.query(Node)
        .order_by(Node.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    counts: dict[int, int] = dict(
        db.query(HarvestTask.node_id, func.count(HarvestTask.id))
        .group_by(HarvestTask.node_id)
        .all()
    )
    items = [_out(n, counts.get(n.id, 0), now=now) for n in rows]
    online = sum(1 for n in rows if node_online(n.heartbeat_at, now))
    return NodeListOut(items=items, total=total, online=online)


@router.post("/{node_id}/heartbeat")
def heartbeat(
    node_id: int,
    payload: NodeHeartbeatIn,
    principal: PrincipalDep,
    db: SessionDep,
    request: Request,
):
    require("node", "写")(principal)
    node = db.get(Node, node_id)
    if node is None:
        raise NotFoundError(f"汇聚节点 {node_id} 不存在")
    now = datetime.now(timezone.utc)
    node.heartbeat_at = now
    node.disk_total = payload.disk_total
    node.disk_used = payload.disk_used
    node.download_bytes_total = payload.download_bytes_total
    db.add(node)
    db.add(
        AuditLog(
            user_id=principal.user.id if principal.user else None,
            operation="节点心跳",
            resource="node",
            result="success",
            detail={
                "node_id": node.id,
                "node_code": node.code,
                "disk_total": payload.disk_total,
                "disk_used": payload.disk_used,
                "download_bytes_total": payload.download_bytes_total,
            },
            trace_id=getattr(request.state, "trace_id", None),
        )
    )
    db.commit()
    return NodeHeartbeatOut(
        id=node.id, heartbeat_at=now, online=node_online(node.heartbeat_at, now)
    )
