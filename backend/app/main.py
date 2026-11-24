"""FastAPI 入口：W1 验收要求的 Hello/健康接口 + 数据层基座连通性探测。"""
from __future__ import annotations

from fastapi import FastAPI

from .config import settings

app = FastAPI(title="Geodata Console API", version="0.1.0-w1")


@app.get("/healthz")
def healthz() -> dict:
    """存活探测（W1 验收：环境可部署 Hello 接口）。"""
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


@app.get("/readyz")
def readyz() -> dict:
    """就绪探测：依赖（DB / Redis / 存储）逐项探测，任一失败返回 503。

    W1 阶段为探测桩——真实连接检查在 W2 数据层基座收口时补齐
    （对齐排期：W1 验收要求 pgSTAC 与存储层可连通，探测实现随基座落地）。
    """
    checks = {
        "database": "pending",   # TODO(W2): SELECT 1 + pgstac 版本查询
        "redis": "pending",      # TODO(W2): PING
        "storage": "pending",    # TODO(W2): 路线探测（对象：list bucket / 文件：stat 挂载点）
    }
    ok = all(v in ("ok", "pending") for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}


@app.get("/hello")
def hello() -> dict:
    return {"message": "Hello, Geodata (GOGDC)!", "week": "W1-kickoff"}
