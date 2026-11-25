#!/usr/bin/env python3
"""S3 兼容集群建桶引导（W5 部署联调：MinIO / Ceph RGW 通用）。

部署新环境（或联调环境）起好对象存储后、应用接入前，先创建三档桶：
热 S3_BUCKET / 温 S3_WARM_BUCKET / 冷 S3_COLD_BUCKET（均可用环境变量覆盖）。
幂等：桶已存在视为成功。

用法：
    # 联调 MinIO
    S3_ENDPOINT=http://127.0.0.1:9000 S3_ACCESS_KEY=... S3_SECRET_KEY=... \
        python3 scripts/s3_bootstrap.py
    # 生产 Ceph RGW
    S3_ENDPOINT=http://rgw.ceph:8080 S3_ACCESS_KEY=... S3_SECRET_KEY=... \
        S3_REGION=us-east-1 python3 scripts/s3_bootstrap.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import settings  # noqa: E402
from app.services.s3_store import S3CompatBlobStore  # noqa: E402
from app.services.storage import BlobStoreError  # noqa: E402


def main() -> int:
    if not settings.s3_access_key or not settings.s3_secret_key:
        print("缺少 S3_ACCESS_KEY / S3_SECRET_KEY 环境变量", file=sys.stderr)
        return 2
    tiers = [
        ("热", settings.s3_bucket),
        ("温", settings.s3_warm_bucket),
        ("冷", settings.s3_cold_bucket),
    ]
    failed = False
    for tier, bucket in tiers:
        store = S3CompatBlobStore(
            settings.s3_endpoint, bucket,
            settings.s3_access_key, settings.s3_secret_key,
            region=settings.s3_region,
        )
        try:
            if store.bucket_exists():
                print(f"[=] {tier} 桶已存在: {settings.s3_endpoint}/{bucket}")
                continue
            store.create_bucket()
            print(f"[+] {tier} 桶已创建: {settings.s3_endpoint}/{bucket}")
        except BlobStoreError as exc:
            print(f"[x] {tier} 桶 {bucket} 建桶失败: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
