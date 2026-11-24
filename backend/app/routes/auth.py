"""认证路由：注册（签发 X-API-Key）、登录（轮换 Key）、查看本人。"""

from __future__ import annotations

from fastapi import APIRouter, status

from ..core.auth import (
    PrincipalDep,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)
from ..core.errors import ConflictError, NotFoundError
from ..domain.rbac import PermissionDenied
from ..models import AppUser, Role
from ..schema import AuthOut, LoginIn, MeOut, RegisterIn
from .deps import SessionDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_key(db, user: AppUser) -> AuthOut:
    """为用户轮换 X-API-Key 并返回明文（仅此一次可见）。"""
    raw = generate_api_key()
    user.api_key_hash = hash_api_key(raw)
    db.add(user)
    db.commit()
    role = db.get(Role, user.role_id)
    return AuthOut(email=user.email, role=role.code if role else "", api_key=raw)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, db: SessionDep, principal: PrincipalDep):
    # 非管理员只能自注册 guest；其余角色由 admin 分配
    is_admin_assign = (
        not principal.anonymous
        and principal.role_code == "admin"
        and payload.role != "guest"
    )
    if payload.role != "guest" and not is_admin_assign:
        raise PermissionDenied(
            f"角色[{principal.role_code or 'anonymous'}]无权注册 {payload.role} 用户"
        )
    if db.query(AppUser).filter(AppUser.email == payload.email).first():
        raise ConflictError("该邮箱已注册")
    role = db.query(Role).filter(Role.code == payload.role).first()
    if role is None:
        raise ConflictError(f"角色 {payload.role} 不存在（需先初始化角色种子数据）")
    user = AppUser(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    return _issue_key(db, user)


@router.post("/login")
def login(payload: LoginIn, db: SessionDep):
    user = db.query(AppUser).filter(AppUser.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 统一模糊提示，不区分邮箱不存在与口令错误
        raise NotFoundError("邮箱或口令错误")
    return _issue_key(db, user)


@router.get("/me")
def me(principal: PrincipalDep, db: SessionDep):
    if principal.anonymous or principal.user is None:
        raise NotFoundError("未登录")
    role = db.get(Role, principal.user.role_id)
    return MeOut(
        email=principal.user.email,
        role=role.code if role else None,
        download_quota=principal.user.download_quota,
        req_quota=principal.user.req_quota,
    )
