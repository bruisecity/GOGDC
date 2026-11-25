"""FastAPI 入口（W2）：路由挂载 + 统一错误信封 + trace_id 中间件 + 真实 readyz。"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .core.errors import install_error_handlers
from .core.probes import run_all_probes
from .db import engine
from .routes import auth as auth_routes, audit as audit_routes, detection as detection_routes
from .routes import catalog as catalog_routes
from .routes import datasets as dataset_routes
from .routes import harvest as harvest_routes
from .routes import stac as stac_routes
from .routes import storage as storage_routes

app = FastAPI(title="Geodata Console API", version="0.6.0-w6")

install_error_handlers(app)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """为每个请求生成 trace_id：写入 state（错误信封取用）并回写响应头。"""
    request.state.trace_id = uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


app.include_router(stac_routes.router)
app.include_router(auth_routes.router)
app.include_router(harvest_routes.router)
app.include_router(catalog_routes.router)
app.include_router(dataset_routes.router)
app.include_router(audit_routes.router)
app.include_router(detection_routes.router)
app.include_router(storage_routes.router)


@app.get("/healthz")
def healthz() -> dict:
    """存活探测：进程在线即 ok，不触碰外部依赖。"""
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


@app.get("/readyz")
def readyz():
    """就绪探测：DB(SELECT 1 + pgstac)/Redis(PING)/存储(端点可达) 逐项真实探测。

    全部 ok → 200；任一失败 → 503（k8s/网关据此摘流量）。
    """
    body = run_all_probes(engine)
    if body["status"] != "ok":
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/hello")
def hello() -> dict:
    return {"message": "Hello, Geodata (GOGDC)!", "week": "W4-portal-compliance"}
