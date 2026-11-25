#!/usr/bin/env python3
"""API 压测脚本（W5 交付：容量水位监控的配套压力工具）。

对指定端点做固定并发 × 固定时长的压测，输出 RPS / 延迟分位数 / 错误数，
用于验证容量水位告警与限流行为。依赖 backend requirements 里的 httpx。

用法：
    python3 scripts/loadtest_api.py --base-url http://localhost:8000 \
        --paths /healthz,/api/datasets --concurrency 8 --duration 30

认证端点可带 --api-key（X-API-Key）。结果同时打印表格并可选写 JSON
（--json-out loadtest.json）供容量规划留档。
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time

try:
    import httpx
except ImportError:  # pragma: no cover
    raise SystemExit("缺少 httpx：pip install httpx<0.28（backend requirements 已含）")


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def worker(
    base_url: str,
    paths: list[str],
    headers: dict,
    duration: float,
    stop_at: float,
    latencies: list[float],
    errors: list[str],
    lock: threading.Lock,
) -> None:
    with httpx.Client(base_url=base_url, headers=headers, timeout=15.0) as client:
        while time.monotonic() < stop_at:
            for path in paths:
                if time.monotonic() >= stop_at:
                    break
                start = time.monotonic()
                try:
                    resp = client.get(path)
                    elapsed = time.perf_counter() - start
                    with lock:
                        latencies.append(elapsed)
                        if resp.status_code >= 400:
                            errors.append(f"{path}->{resp.status_code}")
                except httpx.HTTPError as exc:
                    with lock:
                        errors.append(f"{path}->{type(exc).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description="GOGDC API 压测")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--paths", default="/healthz,/api/datasets")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--api-key", default=None, help="受保护端点的 X-API-Key")
    parser.add_argument("--json-out", default=None, help="结果写 JSON 文件")
    args = parser.parse_args()

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    latencies: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    stop_at = time.monotonic() + args.duration

    threads = [
        threading.Thread(
            target=worker,
            args=(args.base_url, paths, headers, args.duration, stop_at,
                  latencies, errors, lock),
            daemon=True,
        )
        for _ in range(args.concurrency)
    ]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=args.duration + 30)
    wall = time.monotonic() - started

    total = len(latencies)
    s = sorted(latencies)
    report = {
        "base_url": args.base_url,
        "paths": paths,
        "concurrency": args.concurrency,
        "duration_s": round(wall, 2),
        "total_requests": total,
        "rps": round(total / wall, 2) if wall > 0 else 0,
        "p50_ms": round(percentile(s, 50) * 1000, 1),
        "p95_ms": round(percentile(s, 95) * 1000, 1),
        "p99_ms": round(percentile(s, 99) * 1000, 1),
        "errors": len(errors),
        "error_sample": sorted(set(errors))[:5],
    }
    if s:
        report["mean_ms"] = round(statistics.fmean(s) * 1000, 1)

    print("=== GOGDC API 压测报告 ===")
    for k, v in report.items():
        if k != "error_sample" or v:
            print(f"{k:>16}: {v}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.json_out}")


if __name__ == "__main__":
    main()
