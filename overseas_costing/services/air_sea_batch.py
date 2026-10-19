"""Read-only batch projection for the independent air/sea calculator."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import hmac
import json

from overseas_costing.services.air_sea_calculation import DEFAULTS, CURRENCY_DEFAULTS, number, text
from overseas_costing.services.material_input_service import resolve_effective_quantity

try:
    import frappe
except ImportError:
    frappe = None

ITEM_FIELDS = (
    "name", "modified", "row_no", "stable_line_key", "material_code", "product_name", "product_name_es",
    "spec_model", "unit", "purchase_uom", "shipped_uom", "quantity", "actual_shipped_qty",
    "actual_shipped_qty_mode", "actual_shipped_qty_source_revision", "purchase_order_no", "import_name",
    "hs_code", "supplier", "gross_weight_kg", "volume_m3", "net_weight_kg", "project_collection",
    "customs_declared_value_mxn", "extra_json",
    "source_doc_no", "dingtalk_instance_id",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def source_revision(header, items, snapshot, context=None):
    return hashlib.sha256(_json({"batch": header.get("name"), "modified": header.get("modified"),
        "version": header.get("current_version"), "items": items, "snapshot": snapshot,
        "context": context}).encode()).hexdigest()


def build_payload(header, items, snapshot, context=None):
    from overseas_costing.services.effective_source_values import project_source_values
    rows, warnings = [], []
    for index, item in enumerate(items, 1):
        item = project_source_values(item, context or {})
        row = [""] * 30
        fields = {0: "purchase_order_no", 1: "material_code", 3: "import_name", 5: "supplier",
                  6: "product_name", 8: "hs_code", 10: "product_name_es", 11: "spec_model", 29: "project_collection"}
        for column, field in fields.items():
            row[column] = str(item.get(field) or "")
        row[0] = str(item.get("source_doc_no") or item.get("purchase_order_no") or "")
        shipping = resolve_effective_quantity(item)
        row[20], row[4] = shipping.get("quantity", ""), shipping.get("uom", "")
        if shipping.get("blocking"):
            warnings.append(f"第 {index} 行发货数量或单位待补充")
        if not row[4]:
            # Do not manufacture a per-piece denominator for source data with unknown units.
            row[20] = ""
        if shipping.get("is_default"):
            warnings.append(f"第 {index} 行发货数量沿用工作台的采购数量默认值，请核对")
        if shipping.get("mode") == "LEGACY_UNVERIFIED":
            warnings.append(f"第 {index} 行发货数量来源尚未确认，请核对")
        for column, field in ((22, "net_weight_kg"), (23, "gross_weight_kg"), (24, "volume_m3")):
            parsed = number(item.get(field))
            if parsed is not None and parsed > 0:
                row[column] = text(parsed)
        declared = number(item.get("customs_declared_value_mxn"))
        # Currency DocType defaults are zero even when no declaration was supplied.
        if declared is not None and declared > 0:
            row[26] = text(declared)
        else:
            warnings.append(f"第 {index} 行缺少明确的申报货值，请补填（采购金额未作为申报金额带入）")
        rows.append(row)
    overrides = {"grossWeight": None, "volume": None}
    if snapshot:
        for key, field in (("grossWeight", "total_gross_weight_kg"), ("volume", "total_volume_m3")):
            parsed = number(snapshot.get(field))
            if parsed is not None and parsed > 0:
                overrides[key] = text(parsed)
        warnings.append("采用已确认装箱快照的整票毛重和体积；整票汇总单独计入，不重复累加共享箱数据。")
    elif any(not row[23] for row in rows):
        warnings.append("未找到当前有效的装箱快照，缺少的毛重请补填或填写整票汇总。")
    return {"ok": True, "payload": {"rows": rows, "parameters": dict(DEFAULTS),
        "currencies": {**CURRENCY_DEFAULTS, "declaredCurrency": "MXN"},
        "totals_override": overrides, "source": None}, "warnings": warnings}


def load_batch(batch_name):
    from overseas_costing.services.access_control import require_batch_permission
    from overseas_costing.services import batch_service, packing_snapshot_service, effective_logistics_source

    batch_name = require_batch_permission(batch_name, "read")
    header = frappe.get_doc("Overseas Cost Batch", batch_name).as_dict()
    version = header.get("current_version")
    if not version or frappe.db.get_value("Overseas Cost Version", version, "batch") != batch_name:
        raise ValueError(_("当前批次没有有效的成本版本。"))
    available_fields = {field.fieldname for field in frappe.get_meta("Overseas Cost Item").fields} | {"name", "modified"}
    items = frappe.get_all("Overseas Cost Item", filters={"batch": batch_name, "version": version, "is_excluded": 0},
        fields=[field for field in ITEM_FIELDS if field in available_fields],
        order_by="row_no asc, name asc", limit_page_length=5001)
    if len(items) > 5000:
        raise ValueError(_("当前批次超过 5000 行，请使用分批手动测算；未带入截断的数据。"))
    if not items:
        raise ValueError(_("当前批次没有可带入的物料。"))
    invalid = batch_service._build_invalid_business_state(header, items)
    if invalid.get("invalid"):
        raise ValueError(invalid.get("message") or "当前批次来源已失效，不能带入。")
    bundle = effective_logistics_source.current_source_bundle(batch_name, version)
    context = (bundle or {}).get("context")
    if context:
        effective_logistics_source.require_available(context)
    snapshot = packing_snapshot_service.get_current_packing_snapshot(batch_name, version)
    if snapshot and snapshot.get("cost_version") not in (None, "", version):
        snapshot = None
    return header, items, snapshot, context


def _signature(source):
    from frappe.utils.password import get_encryption_key
    key = get_encryption_key().encode()
    return hmac.new(key, _json({key: value for key, value in source.items() if key != "token"}).encode(), hashlib.sha256).hexdigest()


def validate_source(source, *, for_update=False):
    if not source:
        return None
    if not isinstance(source, dict) or not hmac.compare_digest(str(source.get("token") or ""), _signature(source)):
        raise ValueError(_("批次来源信息无效，请重新从工作台带入。"))
    from overseas_costing.services.access_control import require_batch_permission
    batch_name = require_batch_permission(source["batch"], "read")
    if for_update and not frappe.db.get_value("Overseas Cost Batch", batch_name, "name", for_update=True):
        # Share the parent lock with delete_batch: either this reference saves first,
        # or a committed deletion is observed even under REPEATABLE READ.
        raise ValueError(_("来源批次已被删除，不能保存此测算；请重新选择来源。"))
    return source


def preview_batch(batch_name):
    header, items, snapshot, context = load_batch(batch_name)
    result = build_payload(header, items, snapshot, context)
    source = {"batch": header["name"], "batch_no": header.get("batch_no") or header["name"],
        "version": header["current_version"], "packing_snapshot": (snapshot or {}).get("name"),
        "revision": source_revision(header, items, snapshot, context), "imported_at": str(frappe.utils.now_datetime())}
    source["token"] = _signature(source)
    result["payload"]["source"] = source
    return result


def source_changed(source):
    if not source:
        return False
    validate_source(source)
    try:
        header, items, snapshot, context = load_batch(source["batch"])
    except (ValueError, frappe.DoesNotExistError):
        return True
    return source.get("revision") != source_revision(header, items, snapshot, context)


def search_batches(keyword="", page=1, page_length=20):
    from overseas_costing.services.access_control import require_overseas_cost_access
    from overseas_costing.services.workbench_service import _classified_batches, normalize_page
    require_overseas_cost_access()
    page, page_length = normalize_page(page, page_length, default_length=20)
    candidates = _classified_batches({"keyword": str(keyword or "")[:200]})
    items = []
    for row in candidates:
        if (row.get("source_status") or {}).get("invalid_business"):
            continue
        if frappe.has_permission("Overseas Cost Batch", ptype="read", doc=row["name"]):
            items.append({key: row.get(key) for key in ("name", "batch_no", "source_approval_no", "transport_mode")})
    offset = (page - 1) * page_length
    return {"ok": True, "items": items[offset:offset + page_length], "total": len(items), "page": page, "page_length": page_length}
