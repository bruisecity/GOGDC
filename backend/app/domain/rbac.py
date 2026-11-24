"""RBAC 引擎：角色—资源—动作三段权限判定。

依据技术方案第六章（5 角色 × 8 资源权限矩阵）与第三章 permission 实体：
- resource: 8 资源（与中台八面板/门户对齐）
- action: 读 / 写 / 审批
- 判定入口 authorize(role, resource, action) -> bool

角色与权限种子数据为 W1 起步配置（标注：待 W1 评审与设计稿权限矩阵逐格核对后定稿）。
矩阵以数据行（permission 表）为准，本模块只做判定，不含业务逻辑。
"""
from __future__ import annotations

RESOURCES: tuple[str, ...] = (
    "overview",        # 数据总览
    "harvest",         # 采集任务
    "node",            # 汇聚节点
    "detection",       # 安全检测
    "catalog",         # STAC 编目
    "storage",         # 存储管理
    "audit",           # 权限审计
    "settings",        # 系统设置
)
ACTIONS: tuple[str, ...] = ("读", "写", "审批")

# 起步矩阵：role_code -> {resource: 允许的动作集合}
# TODO(W1 评审): 与设计稿 5×8 矩阵逐格核对；此处为可运行的初始配置。
ROLE_MATRIX: dict[str, dict[str, set[str]]] = {
    "admin": {r: {"读", "写", "审批"} for r in RESOURCES},
    "operator": {
        "overview": {"读"},
        "harvest": {"读", "写"},
        "node": {"读", "写"},
        "detection": {"读", "写"},
        "catalog": {"读", "写"},
        "storage": {"读", "写"},
        "audit": {"读"},
        "settings": {"读"},
    },
    "auditor": {
        "overview": {"读"},
        "harvest": {"读"},
        "node": {"读"},
        "detection": {"读", "审批"},
        "audit": {"读", "写"},
        "catalog": {"读"},
        "storage": {"读"},
        "settings": {"读"},
    },
    "researcher": {
        "overview": {"读"},
        "catalog": {"读"},
        "audit": set(),
        "harvest": set(),
        "node": set(),
        "detection": set(),
        "storage": set(),
        "settings": set(),
    },
    "guest": {r: set() for r in RESOURCES},
}


class PermissionDenied(PermissionError):
    """权限不足。code=forbidden 写回统一错误信封。"""


def authorize(role: str, resource: str, action: str) -> bool:
    """判定角色对资源的动作权限；未知角色/资源/动作一律拒绝。"""
    if role not in ROLE_MATRIX:
        return False
    if resource not in RESOURCES or action not in ACTIONS:
        return False
    return action in ROLE_MATRIX[role].get(resource, set())


def require(role: str, resource: str, action: str) -> None:
    """断言式判定，不通过抛 PermissionDenied。"""
    if not authorize(role, resource, action):
        raise PermissionDenied(f"role={role} 无 {resource}:{action} 权限")
