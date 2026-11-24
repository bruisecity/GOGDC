"""安全检测服务测试（W3）。"""
from app.models import AuditLog, Dataset, QuarantineSample
from app.services.detection_service import run_detection


def _dataset(db_session, category, *, fmt="IMG", size=2048, key="usgs/1/a.img"):
    ds = Dataset(
        category_id=category.id, format=fmt, size_bytes=size,
        security_status="待检测", storage_key=key,
    )
    db_session.add(ds)
    db_session.commit()
    return ds


def test_pass_yields_approved(db_session, blob_store, category, detection_rules):
    ds = _dataset(db_session, category)
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store, trace_id="t-1")
    assert result.status == "检测通过"
    assert result.violations == []
    ds = db_session.get(Dataset, ds.id)
    assert ds.security_status == "检测通过"
    assert db_session.query(QuarantineSample).count() == 0
    audit = db_session.query(AuditLog).filter(AuditLog.operation == "安全检测").one()
    assert audit.result == "检测通过"
    assert audit.trace_id == "t-1"


def test_missing_format_quarantines(db_session, blob_store, category, detection_rules):
    ds = _dataset(db_session, category, fmt="", size=2048)
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "已隔离"
    ds = db_session.get(Dataset, ds.id)
    assert ds.security_status == "已隔离"
    sample = db_session.query(QuarantineSample).one()
    assert sample.dataset_id == ds.id
    assert sample.rule_id == detection_rules["meta"].id
    assert sample.disposal_state == "待处置"
    assert sample.evidence["rule_type"] == "元数据完整性"


def test_suspicious_extension_quarantines(db_session, blob_store, category, detection_rules):
    ds = _dataset(db_session, category, key="usgs/2/payload.exe")
    blob_store.put(ds.storage_key, b"MZ...")
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "已隔离"
    assert any(v["rule_type"] == "病毒载荷" for v in result.violations)


def test_missing_blob_quarantines(db_session, blob_store, category, detection_rules):
    ds = _dataset(db_session, category)
    result = run_detection(db_session, ds.id, blob_store)  # 未 put blob
    assert result.status == "已隔离"


def test_disabled_rules_are_skipped(db_session, blob_store, category, detection_rules):
    detection_rules["meta"].enabled = False
    detection_rules["virus"].enabled = False
    db_session.commit()
    ds = _dataset(db_session, category, fmt="", size=0)  # 无规则命中
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "检测通过"
    audit = db_session.query(AuditLog).filter(AuditLog.operation == "安全检测").one()
    assert audit.detail["rules_checked"] == 0
