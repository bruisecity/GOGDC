"""安全检测服务（W3 切片 2：落盘数据 → 检测通过 / 已隔离）。

规则来自 detection_rule 表（params JSON 驱动），本轮实现两类：
- 元数据完整性：params={require_format: bool, min_size_bytes: int}
- 病毒载荷：params={suspicious_extensions: [str], max_bytes: int}

「敏感信息」「坐标基准」两类规则按 W4 详设实现，本轮命中即放行（占位）。
处置口径：
- 任一命中「隔离」动作的规则 → security_status=已隔离 + QuarantineSample（证据入 evidence）；
- 「标记」动作 → 记录违规但不阻断（样本不落隔离区，detail 留审计）；
- 全部通过 → 检测通过。
"""
from __future__ import annotations

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
    # 敏感信息 / 坐标基准：W4 详设，本轮放行
    return []


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
