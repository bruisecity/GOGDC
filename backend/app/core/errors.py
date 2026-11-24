"""全局异常 → 统一错误信封 {code, message, trace_id}（设计稿 §7.3）。

trace_id 由请求中间件写入 request.state，异常处理器回填到响应体。
注册顺序即优先级：认证 401 → 权限 403 → 状态机/请求 422 → 兜底 500。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..domain.rbac import PermissionDenied
from ..domain.states import InvalidTransition
from ..schema import ApiError
from .auth import AuthenticationError


class ConflictError(Exception):
    """资源冲突（如邮箱已注册）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(Exception):
    """目标资源不存在。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _envelope(request: Request, code: str, message: str) -> dict:
    return ApiError(
        code=code,
        message=message,
        trace_id=getattr(request.state, "trace_id", None),
    ).model_dump()


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content=_envelope(request, "CONFLICT", exc.message))

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content=_envelope(request, "NOT_FOUND", exc.message))

    @app.exception_handler(AuthenticationError)
    async def _auth_error(request: Request, exc: AuthenticationError):
        return JSONResponse(status_code=401, content=_envelope(request, "UNAUTHENTICATED", exc.reason))

    @app.exception_handler(PermissionDenied)
    async def _forbidden(request: Request, exc: PermissionDenied):
        return JSONResponse(
            status_code=403,
            content=_envelope(request, "PERMISSION_DENIED", str(exc)),
        )

    @app.exception_handler(InvalidTransition)
    async def _invalid_transition(request: Request, exc: InvalidTransition):
        return JSONResponse(
            status_code=422,
            content=_envelope(
                request,
                "INVALID_STATE_TRANSITION",
                f"{exc.entity} 不允许 {exc.current} → {exc.target}",
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        return JSONResponse(
            status_code=422,
            content=_envelope(
                request,
                "VALIDATION_ERROR",
                f"请求参数校验失败: {first.get('msg', 'invalid')}",
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_envelope(request, "INTERNAL_ERROR", "服务内部错误，请稍后重试"),
        )
