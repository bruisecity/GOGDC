"""应用配置：全部经环境变量注入，本地默认值只用于开发环境。"""
from __future__ import annotations

import os


class Settings:
    """W1 起步配置项。生产值由部署环境注入，不写入代码库。"""

    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "geodata-console")
        self.env = os.getenv("APP_ENV", "dev")  # dev / test / prod
        # 数据层：pgSTAC（业务库与 STAC 目录同库，W1 基座连通性验证目标）
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:geodata@localhost:5432/geodata",
        )
        # 消息队列（Celery broker，异步链路：采集→检测→编目→存储）
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        # 存储路线：object（Ceph RGW / 兼容 S3）或 file（CephFS/NFS 挂载），
        # 依据技术方案第四章：存储层与路线解耦，storage_key 始终存相对键。
        self.storage_route = os.getenv("STORAGE_ROUTE", "object")
        self.s3_endpoint = os.getenv("S3_ENDPOINT", "http://localhost:9000")
        self.s3_bucket = os.getenv("S3_BUCKET", "geodata-hot")
        # W3：本地落盘目录（file 路线 / 开发环境）；storage_key 始终存相对键
        self.storage_dir = os.getenv("STORAGE_DIR", "./var/storage")
        # W3：pgSTAC 直连 DSN（业务库同库；空 = 未启用编目，STAC 路由回空响应）
        self.pgstac_dsn = os.getenv("PGSTAC_DSN", "")
        # W3：STAC item 资产里的下载入口前缀（门户公网地址）
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "")
        # W3：采集落盘默认分类编码（字典行缺失时 worker 跳过落盘并告警）
        self.default_category_code = os.getenv("DEFAULT_CATEGORY_CODE", "elevation")


settings = Settings()
