"""X-API-Key 认证与 RBAC 执行点（设计稿 §7.3）。

匿名可读公开接口；X-API-Key 命中注册用户后按角色鉴权。
认证失败抛 AuthenticationError(401)，权限不足抛 PermissionDenied(403)，
统一走全局错误信封 {code, message, trace_id}。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.rbac import ACTIONS, RESOURCES, PermissionDenied, authorize
from ..models import AppUser, Role


@dataclass
class Principal:
    """请求主体：匿名（anonymous=True）或注册用户。"""

    user: Optional[AppUser]
    role_code: Optional[str]
    anonymous: bool

    @property
    def display_name(self) -> str:
        return "anonymous" if self.anonymous else (self.user.email if self.user else "anonymous")


ANONYMOUS = Principal(user=None, role_code=None, anonymous=True)


class AuthenticationError(Exception):
    """X-API-Key 无效导致的认证失败。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def hash_api_key(raw_key: str) -> str:
    """X-API-Key 的存储摘要（AppUser.api_key_hash 同口径）。"""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """常数时间比较，避免时序侧信道。"""
    return hmac.compare_digest(hash_api_key(raw_key), stored_hash)


def generate_api_key() -> str:
    """签发新 X-API-Key（仅在注册/登录时明文返回一次）。"""
    return "gk_" + secrets.token_urlsafe(32)


def authenticate(request: Request, db: Session) -> Principal:
    """解析 X-API-Key → AppUser(+Role)。

    - 未携带：匿名主体（公开读接口可用）
    - 携带但库中无此摘要：AuthenticationError(401)
    """
    raw_key = request.headers.get("X-API-Key")
    if not raw_key:
        return ANONYMOUS
    api_key_hash = hash_api_key(raw_key)
    user = db.query(AppUser).filter(AppUser.api_key_hash == api_key_hash).first()
    if user is None:
        raise AuthenticationError("无效的 X-API-Key")
    role = db.get(Role, user.role_id)
    return Principal(user=user, role_code=role.code if role else None, anonymous=False)


def get_principal(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> Principal:
    """FastAPI 依赖入口。"""
    return authenticate(request, db)


PrincipalDep = Annotated[Principal, Depends(get_principal)]
SessionDep = Annotated[Session, Depends(get_db)]


def require(resource: str, action: str):
    """路由级 RBAC 守卫工厂。

    用法::

        user = require("harvest", "写")(principal)
    """

    if resource not in RESOURCES or action not in ACTIONS:
        raise ValueError(f"非法资源/动作: {resource}/{action}")

    def checker(principal: Principal) -> AppUser:
        if principal.anonymous or principal.user is None:
            raise PermissionDenied(f"anonymous 无 {resource}:{action} 权限")
        assert principal.role_code is not None
        if not authorize(principal.role_code, resource, action):
            raise PermissionDenied(
                f"角色[{principal.role_code}]无 {resource}:{action} 权限"
            )
        return principal.user

    return checker



# ---------- 口令散列（pbkdf2，W2 起登录/注册使用） ----------

def hash_password(password: str, *, iterations: int = 60_000) -> str:
    """生成 pbkdf2 口令散列，格式 pbkdf2$<iter>$<salt_hex>$<hash_hex>。"""
    import hashlib
    import secrets

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令；格式不合法一律返回 False。"""
    import hashlib
    import hmac as _hmac

    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return _hmac.compare_digest(digest.hex(), hash_hex)
