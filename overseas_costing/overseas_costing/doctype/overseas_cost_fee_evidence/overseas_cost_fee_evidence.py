"""中文用途：校验费用与最终凭证的明确关联。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import frappe
from frappe.model.document import Document


VALIDATION_STATUSES = {"PENDING", "VALID", "INVALID", "UNLINKED"}


class OverseasCostFeeEvidence(Document):
    def validate(self) -> None:
        if not self.batch or not self.version or not self.fee_rule:
            frappe.throw(_("费用凭证必须关联批次、版本和费用规则。"))
        if self.validation_status not in VALIDATION_STATUSES:
            frappe.throw(_("费用凭证校验状态不合法。"))
        if self.validation_status != "UNLINKED" and not self.attachment:
            frappe.throw(_("未解除关联的费用凭证必须指定附件。"))
