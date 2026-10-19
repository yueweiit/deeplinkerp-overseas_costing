"""
中文用途：编辑、重算、版本切换相关 API。

当前一期重点接口：
1. 单字段编辑
2. 批量编辑
3. 重算
4. 更新分摊规则
5. 创建版本
6. 切换版本
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import frappe

from overseas_costing.services import calculate_service, cost_preview_service, cost_trial_ai_service
from overseas_costing.services.access_control import require_batch_permission, require_doctype_permission


MAX_BATCH_UPDATES_BYTES = 2_000_000


@frappe.whitelist()
def update_item_field(
    item_name: str,
    fieldname: str,
    value: str,
    version_name: str | None = None,
    remark: str | None = None,
    manual_override_reason: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """更新单行明细中的某个字段。"""

    require_doctype_permission("Overseas Cost Item", "write", doc=item_name)
    return calculate_service.update_item_field(
        item_name=item_name,
        fieldname=fieldname,
        value=value,
        version_name=version_name,
        remark=remark,
        manual_override_reason=manual_override_reason,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def batch_update_items(
    batch_name: str,
    updates: str,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """批量更新批次明细字段。"""

    batch_name = require_batch_permission(batch_name, "write")
    if len(str(updates or "").encode("utf-8")) > MAX_BATCH_UPDATES_BYTES:
        raise ValueError(_("批量单元格更新参数过大。"))
    return calculate_service.batch_update_items(
        batch_name=batch_name,
        updates=updates,
        version_name=version_name,
        remark=remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def confirm_actual_shipped_qty_from_quantity(
    batch_name: str,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """按采购数量批量确认缺失的实际发货数量。"""

    require_doctype_permission("Overseas Cost Batch", "write", doc=batch_name)
    return calculate_service.confirm_actual_shipped_qty_from_quantity(
        batch_name=batch_name,
        version_name=version_name,
        remark=remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def create_item(
    batch_name: str,
    item_payload: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """在当前批次版本下新增一条物料明细。"""

    require_doctype_permission("Overseas Cost Item", "create")
    return calculate_service.create_item(
        batch_name=batch_name,
        item_payload=item_payload,
        version_name=version_name,
        remark=remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def delete_item(
    item_name: str,
    batch_name: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """软排除一条物料明细，并写审计日志。"""

    require_doctype_permission("Overseas Cost Item", "write", doc=item_name)
    return calculate_service.delete_item(
        item_name=item_name,
        batch_name=batch_name,
        version_name=version_name,
        remark=remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def restore_item(
    item_name: str,
    batch_name: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """恢复一条被软排除的物料明细。"""

    require_doctype_permission("Overseas Cost Item", "write", doc=item_name)
    return calculate_service.restore_item(
        item_name=item_name,
        batch_name=batch_name,
        version_name=version_name,
        remark=remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )


@frappe.whitelist()
def delete_batch(batch_name: str, remark: str | None = None) -> dict:
    """删除一个批次及其关联明细、版本、规则、附件和修改记录。"""

    require_doctype_permission("Overseas Cost Batch", "delete", doc=batch_name)
    return calculate_service.delete_batch(batch_name=batch_name, remark=remark)


@frappe.whitelist()
def recalculate_batch(batch_name: str, version_name: str | None = None,
                      edit_token: str | None = None, expected_modified: str | None = None) -> dict:
    """触发整票重算。"""

    batch_name = require_batch_permission(batch_name, "write")
    return cost_preview_service.calculate_comprehensive_cost(batch_name, version_name,
        edit_token=edit_token, expected_modified=expected_modified)


@frappe.whitelist()
def update_allocation_rule(batch_name: str, version_name: str, rule_payload: str) -> dict:
    """更新分摊规则。"""

    require_doctype_permission("Overseas Cost Batch", "write", doc=batch_name)
    return calculate_service.update_allocation_rule(
        batch_name=batch_name,
        version_name=version_name,
        rule_payload=rule_payload,
    )


@frappe.whitelist()
def create_version(batch_name: str, source_version_name: str, version_type: str) -> dict:
    """基于现有版本创建新版本。"""

    require_doctype_permission("Overseas Cost Batch", "write", doc=batch_name)
    return calculate_service.create_version(
        batch_name=batch_name,
        source_version_name=source_version_name,
        version_type=version_type,
    )


@frappe.whitelist()
def switch_version(batch_name: str, target_version_name: str) -> dict:
    """切换当前版本。"""

    require_doctype_permission("Overseas Cost Batch", "write", doc=batch_name)
    return calculate_service.switch_version(
        batch_name=batch_name,
        target_version_name=target_version_name,
    )


@frappe.whitelist()
def preview_comprehensive_cost(batch_name: str, version_name: str | None = None) -> dict:
    """只读试算，不创建成本版本、不改写正式结果或 ERP 状态。"""

    batch_name = require_batch_permission(batch_name, "read")
    return cost_preview_service.preview_comprehensive_cost(
        batch_name,
        str(version_name or "") or None,
    )


@frappe.whitelist()
def calculate_comprehensive_cost(batch_name: str, version_name: str | None = None,
                                 edit_token: str | None = None, expected_modified: str | None = None) -> dict:
    """保存当前未确认版本的统一试算，确认和 ERP 推送保持独立。"""
    batch_name = require_batch_permission(batch_name, "write")
    return cost_preview_service.calculate_comprehensive_cost(
        batch_name=batch_name, version_name=str(version_name or "") or None,
        edit_token=edit_token, expected_modified=expected_modified,
    )


@frappe.whitelist()
def start_cost_trial_ai_review(batch_name: str, version_name: str | None = None,
                               edit_token: str | None = None, expected_modified: str | None = None,
                               force: str | int | bool = False,
                               reuse_only: str | int | bool = False) -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return cost_trial_ai_service.start_cost_trial_ai_review(
        batch_name=batch_name, version_name=str(version_name or "") or None,
        edit_token=str(edit_token or ""), expected_modified=str(expected_modified or ""),
        force=str(force).strip().lower() in {"1", "true", "yes"},
        reuse_only=str(reuse_only).strip().lower() in {"1", "true", "yes"},
    )


@frappe.whitelist()
def get_cost_trial_ai_review_status(batch_name: str, run_id: str, after_revision: str | int | None = None) -> dict:
    batch_name = require_batch_permission(batch_name, "read")
    return cost_trial_ai_service.get_cost_trial_ai_review_status(
        batch_name=batch_name, run_id=str(run_id or ""),
        after_revision=int(after_revision) if after_revision not in (None, "") else None,
    )


@frappe.whitelist()
def preview_cost_trial(batch_name: str, run_id: str, selections: str = "[]") -> dict:
    batch_name = require_batch_permission(batch_name, "read")
    return cost_trial_ai_service.preview_cost_trial(
        batch_name=batch_name, run_id=str(run_id or ""), selections=selections,
    )


@frappe.whitelist()
def confirm_cost_trial(batch_name: str, run_id: str, preview_token: str, selections: str = "[]",
                       edit_token: str | None = None, expected_modified: str | None = None) -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return cost_trial_ai_service.confirm_cost_trial(
        batch_name=batch_name, run_id=str(run_id or ""), preview_token=str(preview_token or ""),
        selections=selections, edit_token=str(edit_token or ""), expected_modified=str(expected_modified or ""),
    )


@frappe.whitelist()
def discard_cost_trial_ai_review(batch_name: str, run_id: str) -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return cost_trial_ai_service.discard_cost_trial_ai_review(batch_name=batch_name, run_id=str(run_id or ""))
