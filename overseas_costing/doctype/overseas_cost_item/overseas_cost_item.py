"""
中文用途：海外成本明细行控制器。

明细行负责记录：
1. 物料编码、产品名称、单价、数量、总货值
2. 西语名称、规格型号、单位、收件人
3. 报关/运单维度字段
4. 税费、清关费、物流费、重量体积与分摊结果
5. OA / 附件来源留痕、人工覆盖标记与 Excel 映射快照
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from frappe.model.document import Document

from overseas_costing.constants import TRANSPORT_MODES
from overseas_costing.utils.validators import require_in, require_value


class OverseasCostItem(Document):
    """海外成本明细行。"""

    def on_trash(self) -> None:
        if not self.flags.get('ignore_permissions'):
            from overseas_costing.services.calculate_service import _server_metadata_fields
            if 'settlement_cargo' in _server_metadata_fields(getattr(self, 'extra_json', None)):
                raise ValueError(_('已采用物流结算采购支出的物料不能直接删除，请更正来源明细或关联。'))

    def validate(self) -> None:
        # Frappe reserves flags from client document payloads. Existing validated
        # backend services explicitly use save/insert(ignore_permissions=True).
        if not self.flags.get("ignore_permissions"):
            from overseas_costing.services.calculate_service import (
                _server_metadata_fields,
                assert_server_metadata_unchanged,
            )

            previous = self.get_doc_before_save()
            assert_server_metadata_unchanged(
                getattr(previous, "extra_json", None), getattr(self, "extra_json", None)
            )
            if ('settlement_cargo' in _server_metadata_fields(getattr(previous, 'extra_json', None))
                    and any(getattr(previous, field, None) != getattr(self, field, None)
                            for field in ('material_code', 'product_name', 'spec_model'))):
                raise ValueError(_('物料身份已采用物流结算采购支出，请更正来源后重新应用。'))
            if _server_metadata_fields(getattr(previous, "extra_json", None)) and any(
                getattr(previous, field, None) != getattr(self, field, None) for field in ("batch", "version")
            ):
                raise ValueError(_("服务器来源物料不能通过普通保存迁移到其他批次或版本。"))
        require_value(self.batch, "所属批次")
        require_value(self.version, "所属版本")
        if not self.material_code and not self.product_name:
            raise ValueError(_("物料编码和产品名称至少要有一个"))
        if self.transport_mode:
            require_in(self.transport_mode, TRANSPORT_MODES, "运输方式")
