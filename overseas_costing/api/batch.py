"""
中文用途：批次查询相关 API。

对应一期最先要给前端使用的查询接口：
1. 获取批次列表
2. 获取批次详情
3. 获取批次明细
4. 获取版本列表
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import frappe

from overseas_costing.services import batch_service
from overseas_costing.services.access_control import (
    require_batch_permission,
    require_doctype_permission,
    require_overseas_cost_access,
)


@frappe.whitelist()
def get_batch_list(
    transport_mode: str = "SEA",
    business_type: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    recent_days: int | str | None = None,
    include_history: int | str | bool | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """返回批次列表。"""

    require_overseas_cost_access()
    return batch_service.get_batch_list(
        {
            "transport_mode": transport_mode,
            "business_type": business_type,
            "status": status,
            "keyword": keyword,
            "recent_days": recent_days,
            "include_history": include_history,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


@frappe.whitelist()
def get_batch_filter_options() -> dict:
    """返回工作台筛选框使用的固定选项。"""

    require_overseas_cost_access()
    return batch_service.get_batch_filter_options()


@frappe.whitelist()
def create_batch(batch_payload: str | None = None) -> dict:
    """新增一个空的报关/运单批次。"""

    require_doctype_permission("Overseas Cost Batch", "create")
    return batch_service.create_batch(batch_payload=batch_payload)


@frappe.whitelist()
def get_batch_detail(batch_name: str, version_name: str | None = None) -> dict:
    """返回单个批次的头部信息和摘要。"""

    batch_name = require_batch_permission(batch_name, "read")
    return batch_service.get_batch_detail(batch_name=batch_name, version_name=version_name)


@frappe.whitelist()
def get_batch_items(
    batch_name: str,
    version_name: str | None = None,
    customs_no: str | None = None,
    waybill_no: str | None = None,
    material_code: str | None = None,
    product_name: str | None = None,
    import_name: str | None = None,
    hs_code: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
) -> dict:
    """返回批次下按 Excel 列顺序展示的明细行。"""

    batch_name = require_batch_permission(batch_name, "read")
    return batch_service.get_batch_items(
        batch_name=batch_name,
        version_name=version_name,
        customs_no=customs_no,
        waybill_no=waybill_no,
        material_code=material_code,
        product_name=product_name,
        import_name=import_name,
        hs_code=hs_code,
        category=category,
        keyword=keyword,
    )


@frappe.whitelist()
def get_version_list(batch_name: str) -> dict:
    """返回批次下的版本列表。"""

    batch_name = require_batch_permission(batch_name, "read")
    return batch_service.get_version_list(batch_name=batch_name)


@frappe.whitelist()
def get_audit_logs(batch_name: str, version_name: str | None = None, limit: int | str = 80) -> dict:
    """返回批次修改记录，用于页面底部留痕展示。"""

    batch_name = require_batch_permission(batch_name, "read")
    return batch_service.get_audit_logs(batch_name=batch_name, version_name=version_name, limit=limit)


@frappe.whitelist()
def export_current_result_xlsx(batch_names_json: str | None = None, transport_label: str | None = None) -> dict:
    """导出当前筛选范围内的核算结果 xlsx。"""

    batch_names = batch_service._normalize_export_batch_names(batch_names_json)
    permitted_names = [require_batch_permission(name, "read") for name in batch_names]
    return batch_service.export_current_result_xlsx(
        batch_names_json=frappe.as_json(permitted_names),
        transport_label=transport_label,
    )


@frappe.whitelist()
def get_dingtalk_order_link(batch_name: str) -> dict:
    """返回钉钉订单按钮使用的跳转信息。"""

    batch_name = require_batch_permission(batch_name, "read")
    return batch_service.get_dingtalk_order_link(batch_name=batch_name)


@frappe.whitelist()
def open_dingtalk_order(batch_name: str) -> None:
    """直接重定向到钉钉审批页，供前端按钮 href 直接调用。"""

    batch_name = require_batch_permission(batch_name, "read")
    result = batch_service.get_dingtalk_order_link(batch_name=batch_name)
    open_url = result.get("dingtalk_order", {}).get("open_url", "")
    if not open_url:
        frappe.throw(_("当前批次缺少钉钉实例ID或官方链接，无法打开钉钉订单。"))

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = open_url
