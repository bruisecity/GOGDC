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


# ---- W4：敏感信息 / 坐标基准 实装 ----

from app.models import DetectionRule


def _add_rule(db_session, rule_type, params, *, action="隔离", enabled=True):
    rule = DetectionRule(
        rule_type=rule_type, params=params, severity="high",
        action=action, enabled=enabled,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def test_sensitive_pattern_hit_quarantines(db_session, blob_store, category, detection_rules):
    rule = _add_rule(db_session, "敏感信息", {
        "patterns": [
            {"name": "身份证号", "regex": r"\b\d{17}[\dXx]\b"},
            {"name": "手机号", "regex": r"\b1[3-9]\d{9}\b"},
        ],
        "max_scan_bytes": 4096,
    })
    ds = _dataset(db_session, category, key="usgs/2/a.txt")
    blob_store.put(ds.storage_key, b"contact: 13800138000, id: 11010119900307777X")
    result = run_detection(db_session, ds.id, blob_store, trace_id="sens-1")
    assert result.status == "已隔离"
    assert len(result.violations) == 1
    problems = result.violations[0]["problems"]
    assert any("身份证号" in p for p in problems)
    assert any("手机号" in p for p in problems)
    assert result.violations[0]["rule_id"] == rule.id
    assert db_session.query(QuarantineSample).count() == 1


def test_sensitive_clean_payload_passes(db_session, blob_store, category, detection_rules):
    _add_rule(db_session, "敏感信息", {
        "patterns": [{"name": "身份证号", "regex": r"\b\d{17}[\dXx]\b"}],
    })
    ds = _dataset(db_session, category, key="usgs/3/a.txt")
    blob_store.put(ds.storage_key, b"plain elevation raster payload, no pii")
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "检测通过"


def test_sensitive_no_patterns_is_noop(db_session, blob_store, category, detection_rules):
    _add_rule(db_session, "敏感信息", {"patterns": []})
    ds = _dataset(db_session, category, key="usgs/4/a.txt")
    blob_store.put(ds.storage_key, b"whatever")
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "检测通过"


def test_crs_out_of_range_quarantines(db_session, blob_store, category, detection_rules):
    rule = _add_rule(db_session, "坐标基准", {"datum": "WGS84"})
    ds = _dataset(db_session, category, key="usgs/5/a.img")
    ds.bbox_west, ds.bbox_south, ds.bbox_east, ds.bbox_north = (
        -200.0, 39.0, -104.0, 40.0,
    )
    db_session.add(ds)
    db_session.commit()
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "已隔离"
    assert result.violations[0]["rule_id"] == rule.id
    assert any("经度超出" in p for p in result.violations[0]["problems"])


def test_crs_missing_bbox_quarantines(db_session, blob_store, category, detection_rules):
    _add_rule(db_session, "坐标基准", {"datum": "WGS84"})
    ds = _dataset(db_session, category, key="usgs/6/a.img")
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "已隔离"
    assert any("坐标基准" in p for p in result.violations[0]["problems"])


def test_crs_valid_bbox_passes(db_session, blob_store, category, detection_rules):
    _add_rule(db_session, "坐标基准", {"datum": "WGS84"})
    ds = _dataset(db_session, category, key="usgs/7/a.img")
    ds.bbox_west, ds.bbox_south, ds.bbox_east, ds.bbox_north = (
        -105.0, 39.0, -104.0, 40.0,
    )
    db_session.add(ds)
    db_session.commit()
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "检测通过"


def test_crs_bad_orientation_flagged(db_session, blob_store, category, detection_rules):
    _add_rule(db_session, "坐标基准", {"datum": "WGS84"})
    ds = _dataset(db_session, category, key="usgs/8/a.img")
    ds.bbox_west, ds.bbox_south, ds.bbox_east, ds.bbox_north = (
        -104.0, 39.0, -105.0, 40.0,  # west > east
    )
    db_session.add(ds)
    db_session.commit()
    blob_store.put(ds.storage_key, b"x" * 2048)
    result = run_detection(db_session, ds.id, blob_store)
    assert result.status == "已隔离"
    assert any("方向非法" in p for p in result.violations[0]["problems"])
