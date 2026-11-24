"""数据层基座真实探测（W2 收口：替代 W1 的 pending 桩）。

每个探针返回 (ok, detail)；readyz 汇总，任一失败 → 503。
测试环境不部署真实依赖，测试通过 monkeypatch 替换探针。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import settings


def probe_database(engine: Engine) -> tuple[bool, str]:
    """SELECT 1 连通性 + pgstac schema 存在性（pgstac 由镜像 initdb 装载）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            row = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name='pgstac'"
                )
            ).scalar()
            pgstac = "yes" if row else "no"
            return True, f"select1=ok pgstac={pgstac}"
    except Exception as exc:  # noqa: BLE001 —— 探测失败本身就是结果
        return False, f"connect-failed: {type(exc).__name__}"


def probe_redis(url: str) -> tuple[bool, str]:
    """redis PING；客户端未安装时明确报告（而非崩溃）。"""
    try:
        import redis  # 延迟导入：无 redis 依赖的环境仅本探针降级
    except ImportError:
        return False, "redis-client-not-installed"
    try:
        client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        pong = client.ping()
        return bool(pong), "ping=pong" if pong else "ping=unexpected"
    except Exception as exc:  # noqa: BLE001
        return False, f"ping-failed: {type(exc).__name__}"


def probe_storage(route: str, endpoint: str) -> tuple[bool, str]:
    """存储路线探测：对象路线探测 S3 端点可达性（任意 HTTP 应答即视为端点在线）。"""
    import httpx

    try:
        resp = httpx.get(endpoint, timeout=3.0, follow_redirects=False)
        # MinIO/S3 根路径通常回 403（未带签名）——有应答即端点在线
        return resp.status_code < 500, f"route={route} status={resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"route={route} unreachable: {type(exc).__name__}"


def run_all_probes(engine: Engine) -> dict[str, Any]:
    """执行全部探针，返回 readyz 响应体。"""
    checks: dict[str, dict[str, Any]] = {}
    db_ok, db_detail = probe_database(engine)
    checks["database"] = {"status": "ok" if db_ok else "fail", "detail": db_detail}
    rd_ok, rd_detail = probe_redis(settings.redis_url)
    checks["redis"] = {"status": "ok" if rd_ok else "fail", "detail": rd_detail}
    st_ok, st_detail = probe_storage(settings.storage_route, settings.s3_endpoint)
    checks["storage"] = {"status": "ok" if st_ok else "fail", "detail": st_detail}
    all_ok = db_ok and rd_ok and st_ok
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
