"""
中文用途：海外成本批次主单控制器。

主单负责记录：
1. 批次号、报关单号、运单号、运输方式
2. OA / Excel 导入来源追溯信息
3. 当前版本、确认状态、回写状态
4. 汇总金额、重量、明细数量
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model.document import Document

from overseas_costing.constants import (
    BATCH_CONFIRM_STATUSES,
    BATCH_SOURCE_TYPES,
    BATCH_STATUSES,
    BATCH_WRITEBACK_STATUSES,
    BUSINESS_TYPES,
    TRANSPORT_MODES,
)
from overseas_costing.utils.validators import require_in, require_value


class OverseasCostBatch(Document):
    """海外成本批次主单。"""

    def validate(self) -> None:
        require_value(self.batch_no, "批次号")
        if self.transport_mode:
            require_in(self.transport_mode, TRANSPORT_MODES, "运输方式")
        if self.business_type:
            require_in(self.business_type, BUSINESS_TYPES, "业务类型")
        if self.source_type:
            require_in(self.source_type, BATCH_SOURCE_TYPES, "来源类型")
        if self.status:
            require_in(self.status, BATCH_STATUSES, "批次状态")
        if self.confirm_status:
            require_in(self.confirm_status, BATCH_CONFIRM_STATUSES, "确认状态")
        if self.writeback_status:
            require_in(self.writeback_status, BATCH_WRITEBACK_STATUSES, "回写状态")
        self._validate_oa_source_provenance()

    def _validate_oa_source_provenance(self) -> None:
        """OA 来源字段只由服务端导入流程维护，普通单据保存不能改写。"""

        if self.is_new():
            if self.source_type == "oa_logistics" and not self.flags.ignore_permissions:
                frappe.throw(_("钉钉国际物流来源只能通过审批导入创建。"))
            return

        before = self.get_doc_before_save()
        if before is None:
            before = frappe.db.get_value(
                self.doctype, self.name,
                [
                    "source_type", "source_data_id", "source_approval_no", "source_instance_id",
                    "source_dingtalk_url", "source_approval_status", "source_created_at",
                    "source_finished_at", "extra_json",
                ],
                as_dict=True,
            )
        if not before:
            frappe.throw(_("无法核验批次原始来源，请刷新后重试。"))
        if "oa_logistics" not in {before.get("source_type"), self.source_type}:
            return
        protected_fields = (
            "source_type", "source_data_id", "source_approval_no", "source_instance_id",
            "source_dingtalk_url", "source_approval_status", "source_created_at", "source_finished_at",
        )
        for fieldname in protected_fields:
            if str(before.get(fieldname) or "") != str(self.get(fieldname) or ""):
                frappe.throw(_("钉钉国际物流审批来源不可手工修改，请通过审批同步更新。"))

        def approval_fields(value):
            try:
                extra = json.loads(value) if isinstance(value, str) else (value or {})
            except (TypeError, ValueError):
                extra = {}
            if not isinstance(extra, dict):
                extra = {}
            return {key: extra.get(key) for key in ("approval_result", "excluded")}

        if approval_fields(before.get("extra_json")) != approval_fields(self.get("extra_json")):
            frappe.throw(_("钉钉国际物流审批来源不可手工修改，请通过审批同步更新。"))
