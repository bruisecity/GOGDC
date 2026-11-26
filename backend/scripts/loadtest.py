#!/usr/bin/env python3
"""W7 压测工具：目录检索延迟（P95）与单节点并发下载。

设计口径（§7 非功能）：
- 目录检索 P95 < 1s（pgSTAC 空间索引）；
- 单汇聚节点并发下载不低于 10。

只依赖标准库（urllib + threading + statistics），可在 CI / 本地直跑：
  # 检索压测（对启用了 pgSTAC 的部署）
  python3 scripts/loadtest.py --base-url http://127.0.0.1:8000 \
      --search-requests 200 --search-concurrency 8 --p95-target-ms 1000

  # 并发下载压测
  python3 scripts/loadtest.py --base-url http://127.0.0.1:8000 \
      --api-key gk_xxx --dataset-id 1 \
      --download-workers 16 --download-seconds 10 --download-min-concurrency 10

退出码：达标 0；任一目标未达标 1（供 CI 门禁使用）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request


def http_get(url: str, headers: dict | None = None, timeout: float = 30.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def http_post_json(url: str, body: dict, timeout: float = 30.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def percentile(sorted_vals: list[float], pct: float) -> float:
    """最近邻插值百分位（样本量小时与常见定义一致）。"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def run_search(base_url: str, requests_n: int, concurrency: int,
               body: dict) -> dict:
    """检索压测：N 次并发 POST /stac/v1/search，收集延迟分布。"""
    latencies: list[float] = []
    lock = threading.Lock()
    errors = 0
    idx = list(range(requests_n))
    cursor = [0]

    def worker():
        nonlocal errors
        while True:
            with lock:
                if cursor[0] >= len(idx):
                    return
                cursor[0] += 1
            t0 = time.perf_counter()
            try:
                http_post_json(f"{base_url}/stac/v1/search", body)
                with lock:
                    latencies.append(time.perf_counter() - t0)
            except Exception:
                with lock:
                    errors += 1

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = sorted(latencies)
    return {
        "requests": requests_n,
        "concurrency": concurrency,
        "ok": len(s),
        "errors": errors,
        "p50_ms": round(percentile(s, 0.50) * 1000, 2),
        "p95_ms": round(percentile(s, 0.95) * 1000, 2),
        "p99_ms": round(percentile(s, 0.99) * 1000, 2),
        "avg_ms": round(statistics.mean(s) * 1000, 2) if s else 0.0,
    }


def run_download(base_url: str, api_key: str, dataset_id: int,
                 workers: int, seconds: float) -> dict:
    """并发下载压测：workers 线程持续下载 seconds 秒。

    统计「最大同时在飞」作为单节点并发能力观测值——
    只有请求持续成功时在飞数才会堆上去，失败立刻拉低。
    """
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{base_url}/api/v1/datasets/{dataset_id}/download"
    stop_at = time.perf_counter() + seconds
    successes = 0
    errors = 0
    total_bytes = 0
    inflight = 0
    max_inflight = 0
    lock = threading.Lock()

    def worker():
        nonlocal successes, errors, total_bytes, inflight, max_inflight
        while time.perf_counter() < stop_at:
            with lock:
                inflight += 1
                max_inflight = max(max_inflight, inflight)
            try:
                status, payload = http_get(url, headers=headers)
                if status == 200:
                    with lock:
                        successes += 1
                        total_bytes += len(payload)
                else:
                    with lock:
                        errors += 1
            except Exception:
                with lock:
                    errors += 1
            finally:
                with lock:
                    inflight -= 1

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return {
        "workers": workers,
        "duration_s": seconds,
        "successes": successes,
        "errors": errors,
        "max_concurrent": max_inflight,
        "throughput_mbps": round(total_bytes / seconds / 1_000_000, 2),
        "downloaded_mb": round(total_bytes / 1_000_000, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="W7 压测：检索 P95 + 并发下载")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--dataset-id", type=int, default=1)
    # 检索阶段
    parser.add_argument("--search-requests", type=int, default=0,
                        help="0 = 跳过检索压测")
    parser.add_argument("--search-concurrency", type=int, default=8)
    parser.add_argument("--p95-target-ms", type=float, default=1000.0,
                        help="检索 P95 目标（ms），默认 1000（设计口径 <1s）")
    parser.add_argument("--search-bbox", default="-10,-10,10,10")
    # 下载阶段
    parser.add_argument("--download-workers", type=int, default=0,
                        help="0 = 跳过下载压测")
    parser.add_argument("--download-seconds", type=float, default=10.0)
    parser.add_argument("--download-min-concurrency", type=int, default=10,
                        help="并发下载下限（设计口径 >=10）")
    parser.add_argument("--json-out", default="",
                        help="把结果 JSON 落盘到该路径")
    args = parser.parse_args()

    result: dict = {"base_url": args.base_url, "search": None, "download": None}
    passed = True

    if args.search_requests > 0:
        w, s, e, n = [float(v) for v in args.search_bbox.split(",")]
        body = {"bbox": [w, s, e, n], "limit": 20}
        result["search"] = run_search(
            args.base_url, args.search_requests, args.search_concurrency, body
        )
        ok95 = result["search"]["p95_ms"] < args.p95_target_ms
        ok_err = result["search"]["errors"] == 0
        passed = passed and ok95 and ok_err
        print(
            f"[search] requests={args.search_requests} c={args.search_concurrency} "
            f"p50={result['search']['p50_ms']}ms p95={result['search']['p95_ms']}ms "
            f"p99={result['search']['p99_ms']}ms errors={result['search']['errors']} "
            f"-> P95<{'PASS' if ok95 else 'FAIL'} (<{args.p95_target_ms}ms)"
        )

    if args.download_workers > 0:
        result["download"] = run_download(
            args.base_url, args.api_key, args.dataset_id,
            args.download_workers, args.download_seconds,
        )
        d = result["download"]
        ok_conc = d["max_concurrent"] >= args.download_min_concurrency
        ok_err = d["errors"] == 0 and d["successes"] > 0
        passed = passed and ok_conc and ok_err
        print(
            f"[download] workers={d['workers']} duration={d['duration_s']}s "
            f"successes={d['successes']} errors={d['errors']} "
            f"max_concurrent={d['max_concurrent']} "
            f"throughput={d['throughput_mbps']}MB/s "
            f"-> concurrency>={'PASS' if ok_conc else 'FAIL'} "
            f"(>={args.download_min_concurrency})"
        )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
