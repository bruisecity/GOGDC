"""安全检测服务（W3 切片 2：落盘数据 → 检测通过 / 已隔离）。

规则来自 detection_rule 表（params JSON 驱动），本轮实现两类：
- 元数据完整性：params={require_format: bool, min_size_bytes: int}
- 病毒载荷：params={suspicious_extensions: [str], max_bytes: int}

「敏感信息」「坐标基准」两类规则自 W4 起实装：
- 敏感信息：params={patterns: [{name, regex}], max_scan_bytes}，
  对载荷内容（前 max_scan_bytes 字节）做正则扫描（规则精调列入试运行期）；
- 坐标基准：params={datum, lon_range, lat_range}，校验 bbox 与声明的
  WGS84（EPSG:4326）经纬度范围一致（缺 bbox / 越界 / 方向非法均为违规）。
处置口径：
- 任一命中「隔离」动作的规则 → security_status=已隔离 + QuarantineSample（证据入 evidence）；
- 「标记」动作 → 记录违规但不阻断（样本不落隔离区，detail 留审计）；
- 全部通过 → 检测通过。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import AuditLog, Dataset, DetectionRule, QuarantineSample
from .storage import BlobStore


@dataclass
class DetectionResult:
    dataset_id: int
    status: str  # 检测通过 / 已隔离
    violations: list[dict] = field(default_factory=list)


def _apply_rule(rule: DetectionRule, dataset: Dataset, blob_store: BlobStore) -> list[str]:
    """返回该规则命中的问题描述列表（空 = 通过）。"""
    params = rule.params or {}
    if rule.rule_type == "元数据完整性":
        problems: list[str] = []
        if params.get("require_format", True) and not (dataset.format or "").strip():
            problems.append("缺少 format 字段")
        min_size = int(params.get("min_size_bytes", 1))
        if (dataset.size_bytes or 0) < min_size:
            problems.append(f"size_bytes={dataset.size_bytes} 低于下限 {min_size}")
        return problems
    if rule.rule_type == "病毒载荷":
        problems = []
        exts = [str(e).lower() for e in params.get("suspicious_extensions", [])]
        if exts and dataset.storage_key.lower().endswith(tuple(exts)):
            problems.append(f"存储键命中可疑扩展名: {dataset.storage_key}")
        max_bytes = params.get("max_bytes")
        if max_bytes is not None and (dataset.size_bytes or 0) > int(max_bytes):
            problems.append(f"size_bytes={dataset.size_bytes} 超过 {max_bytes}")
        if not blob_store.exists(dataset.storage_key):
            problems.append(f"存储对象缺失: {dataset.storage_key}")
        return problems
    if rule.rule_type == "敏感信息":
        patterns = [p for p in params.get("patterns", []) if isinstance(p, dict) and p.get("regex")]
        if not patterns:
            return []  # 未配置模式 = 规则不生效（不误伤）
        max_scan = int(params.get("max_scan_bytes", 1_000_000))
        try:
            data = blob_store.get(dataset.storage_key)
        except Exception as exc:  # 对象缺失/不可读按违规处理
            return [f"载荷不可读，无法做敏感信息扫描: {exc}"]
        text = data[:max_scan].decode("utf-8", errors="ignore")
        problems = []
        for pat in patterns:
            try:
                hits = re.findall(pat["regex"], text)
            except re.error as exc:
                problems.append(f"规则模式非法（不参与判定）: {pat.get('name')} {exc}")
                continue
            if hits:
                problems.append(f"命中敏感模式[{pat.get('name', '?')}] ×{len(hits)}")
        return problems
    if rule.rule_type == "坐标基准":
        datum = str(params.get("datum", "WGS84"))
        lon_lo, lon_hi = params.get("lon_range", [-180.0, 180.0])
        lat_lo, lat_hi = params.get("lat_range", [-90.0, 90.0])
        bbox = (dataset.bbox_west, dataset.bbox_south, dataset.bbox_east, dataset.bbox_north)
        if any(v is None for v in bbox):
            return [f"缺少空间范围，无法核对 {datum} 坐标基准"]
        w, s_, e, n = (float(v) for v in bbox)
        problems = []
        if w > e or s_ > n:
            problems.append("bbox 方向非法（west>east 或 south>north）")
        if not (float(lon_lo) <= w <= float(lon_hi) and float(lon_lo) <= e <= float(lon_hi)):
            problems.append(f"经度超出 {datum} 范围 [{lon_lo}, {lon_hi}]: west={w}, east={e}")
        if not (float(lat_lo) <= s_ <= float(lat_hi) and float(lat_lo) <= n <= float(lat_hi)):
            problems.append(f"纬度超出 {datum} 范围 [{lat_lo}, {lat_hi}]: south={s_}, north={n}")
        return problems
    return []  # 未知规则类型：不判定（新规则接入前不误伤）


def run_detection(
    db: Session,
    dataset_id: int,
    blob_store: BlobStore,
    *,
    trace_id: str | None = None,
    user_id: int | None = None,
) -> DetectionResult:
    """对单个数据集执行一次检测（启用规则全量跑）。"""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"数据集不存在: {dataset_id}")

    dataset.security_status = "检测中"
    db.add(dataset)
    db.commit()

    rules = db.query(DetectionRule).filter(DetectionRule.enabled.is_(True)).all()
    violations: list[dict] = []
    quarantined = False

    for rule in rules:
        problems = _apply_rule(rule, dataset, blob_store)
        if not problems:
            continue
        violation = {
            "rule_id": rule.id,
            "rule_type": rule.rule_type,
            "severity": rule.severity,
            "action": rule.action,
            "problems": problems,
        }
        violations.append(violation)
        if rule.action == "隔离":
            quarantined = True
            db.add(
                QuarantineSample(
                    dataset_id=dataset.id,
                    rule_id=rule.id,
                    evidence=violation,
                    disposal_state="待处置",
                )
            )

    dataset.security_status = "已隔离" if quarantined else "检测通过"
    db.add(dataset)
    db.add(
        AuditLog(
            user_id=user_id,
            operation="安全检测",
            resource="dataset",
            result="已隔离" if quarantined else "检测通过",
            detail={
                "dataset_id": dataset.id,
                "violations": violations,
                "rules_checked": len(rules),
            },
            trace_id=trace_id,
        )
    )
    db.commit()
    return DetectionResult(
        dataset_id=dataset.id,
        status=dataset.security_status,
        violations=violations,
    )
