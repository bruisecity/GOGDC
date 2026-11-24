"""RBAC 判定单元测试。矩阵本身是 W1 起步配置（待评审），测试只验证引擎行为。"""
import pytest

from app.domain import rbac
from app.domain.rbac import PermissionDenied, authorize, require


class TestAuthorize:
    def test_admin_full(self):
        for r in rbac.RESOURCES:
            for a in rbac.ACTIONS:
                assert authorize("admin", r, a)

    def test_operator_write_not_approve(self):
        assert authorize("operator", "harvest", "写")
        assert not authorize("operator", "harvest", "审批")

    def test_researcher_readonly(self):
        assert authorize("researcher", "catalog", "读")
        assert not authorize("researcher", "storage", "写")
        assert not authorize("researcher", "settings", "写")

    def test_guest_denied(self):
        assert not authorize("guest", "overview", "读")

    def test_unknown_rejected(self):
        assert not authorize("nobody", "overview", "读")
        assert not authorize("admin", "nonexistent", "读")
        assert not authorize("admin", "overview", "删除")

    def test_resources_count(self):
        # 与设计稿 8 资源口径一致
        assert len(rbac.RESOURCES) == 8


class TestRequire:
    def test_require_ok(self):
        require("auditor", "detection", "审批")  # 不抛即通过

    def test_require_raises(self):
        with pytest.raises(PermissionDenied):
            require("operator", "settings", "写")
