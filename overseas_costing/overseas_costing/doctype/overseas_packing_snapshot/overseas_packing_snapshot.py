"""中文用途：阻止已确认装箱快照被普通表单或接口静默修改。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import frappe
from frappe.model.document import Document


class OverseasPackingSnapshot(Document):
    def validate(self) -> None:
        if not self.batch:
            frappe.throw(_("所属批次不能为空。"))
        if self.status not in {"Draft", "Confirmed", "Superseded"}:
            frappe.throw(_("装箱快照状态不合法。"))

    def before_save(self) -> None:
        previous = self.get_doc_before_save()
        if not previous or previous.status not in {"Confirmed", "Superseded"}:
            return

        allow_packing_supersede = bool(getattr(self.flags, "allow_packing_supersede", False))
        allowed_changes = {"status", "is_current", "modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by"}
        changed = {
            fieldname
            for fieldname in self.as_dict()
            if getattr(previous, fieldname, None) != getattr(self, fieldname, None)
        }
        if allow_packing_supersede and changed <= allowed_changes and self.status == "Superseded" and not self.is_current:
            return
        frappe.throw(_("不能修改已确认的装箱快照；请确认新来源并生成新版本。"))
