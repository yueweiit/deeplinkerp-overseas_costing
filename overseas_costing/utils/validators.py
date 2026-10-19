"""
中文用途：基础参数校验工具。

当前先放轻量级校验函数，
后续前后端接口统一从这里复用。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message



def require_value(value, field_label: str) -> None:
    if value in (None, ""):
        raise ValueError(_("{0} 不能为空").format(field_label))


def require_in(value: str, allowed: tuple[str, ...], field_label: str) -> None:
    if value not in allowed:
        raise ValueError(_("{0} 不合法，当前值：{1}").format(field_label, value))
