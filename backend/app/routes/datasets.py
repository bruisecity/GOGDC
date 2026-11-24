"""数据下载路由（W3 切片 6：门户下载入口，设计稿 §7 门户侧）。

口径：
- 匿名可下载（公开数据）；已隔离数据一律 403；
- 未完成检测（待检测/检测中）→ 409，不允许进入下载通道；
- 已登录用户按 download_quota（字节额度）校验并扣减；额度 0 = 不限
  （注册用户默认 0，额度由运营侧配置，语义为「剩余可下载字节」）；
- 成功下载回写 StorageObject.last_access_at 并落审计日志。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ..core.auth import PrincipalDep
from ..core.errors import ConflictError, NotFoundError, PermissionDenied
from ..models import AppUser, AuditLog, Dataset, StorageObject
from ..services.catalog import asset_mime
from ..services.storage import BlobStore, BlobStoreError
from .deps import SessionDep, get_blob_store

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.get("/{dataset_id}/download")
def download_dataset(
    dataset_id: int,
    request: Request,
    principal: PrincipalDep,
    db: SessionDep,
    blob_store: BlobStore = Depends(get_blob_store),
):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"数据集 {dataset_id} 不存在")
    if dataset.security_status == "已隔离":
        raise PermissionDenied("数据已隔离，禁止下载")
    if dataset.security_status != "检测通过":
        raise ConflictError(f"数据未完成安全检测（当前: {dataset.security_status}），暂不可下载")

    user = None
    if not principal.anonymous and principal.user is not None:
        user = principal.user
        if user is not None and user.download_quota and user.download_quota > 0:
            if dataset.size_bytes and dataset.size_bytes > user.download_quota:
                raise PermissionDenied(
                    f"下载额度不足: 需要 {dataset.size_bytes} 字节，剩余 {user.download_quota}"
                )

    try:
        payload = blob_store.get(dataset.storage_key)
    except BlobStoreError as exc:
        raise NotFoundError(f"存储对象不可用: {exc}") from exc

    if user is not None and user.download_quota and user.download_quota > 0:
        user.download_quota = max(0, user.download_quota - (dataset.size_bytes or len(payload)))
        db.add(user)

    obj = (
        db.query(StorageObject)
        .filter(StorageObject.key == dataset.storage_key)
        .one_or_none()
    )
    if obj is not None:
        obj.last_access_at = datetime.now(timezone.utc)
        db.add(obj)

    db.add(
        AuditLog(
            user_id=user.id if user is not None else None,
            operation="数据下载",
            resource="dataset",
            result="success",
            detail={
                "dataset_id": dataset.id,
                "storage_key": dataset.storage_key,
                "size_bytes": dataset.size_bytes or len(payload),
                "security_status": dataset.security_status,
            },
            trace_id=getattr(request.state, "trace_id", None),
        )
    )
    db.commit()

    filename = dataset.storage_key.rsplit("/", 1)[-1]
    return Response(
        content=payload,
        media_type=asset_mime(dataset.storage_key),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
