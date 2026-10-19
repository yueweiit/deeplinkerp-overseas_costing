"""
中文用途：编辑与重算服务。

这是后续整个后端最核心的服务之一，
后面会承接：
1. 单字段修改
2. 批量字段修改
3. 汇率更新
4. 分摊重算
5. 版本创建与切换
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from overseas_costing.services import (
    allocation_service,
    audit_service,
    material_input_service,
    source_priority_service,
    version_service,
)
from overseas_costing.services.material_value_semantics import is_effectively_missing
from overseas_costing.services.supplier_resolution_service import (
    load_active_suppliers,
    validate_canonical_supplier,
)


# --- First usable implementation for the Excel -> recalculate MVP. ---

import json as _json
from copy import deepcopy as _deepcopy
from datetime import datetime as _datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

try:
    import frappe as _frappe
except Exception:  # pragma: no cover - local tests can import without Frappe
    _frappe = None

from overseas_costing.utils.currency import round_money as _round_money

DEFAULT_FX_RMB_TO_MXN = 2.6
PURCHASE_CORRECTION_FIELDS = frozenset(
    {"goods_value", "unit_price", "purchase_currency", "purchase_uom", "unit_price_uom"}
)
REASON_REQUIRED_ITEM_FIELDS = PURCHASE_CORRECTION_FIELDS | frozenset({
    "net_weight_kg",
    "gross_weight_kg",
    "volume_m3",
    "chargeable_weight_kg",
    "project_collection",
    "supplier",
})
# 「参考字段」＝ERP 路由与采购的基础资料，唯一 owner。
#
# 这两项是采购/业务在成本确认**之后**才补齐的输入（站点默认供应商与 ERP 项目路由都靠它们），
# 分摊计算却从不读它们：``cost_preview_service`` 里 ``project_collection`` 只在物料输出里
# 透传（见 ``build_saved_cost_data`` 的 preview_items），``supplier`` 只出现在
# ``COST_INPUT_FIELDS`` 的契约声明里。所以它们单开一档写入门槛：已确认 / 已锁定 / 已回写的
# **当前版本**仍可维护，历史与归档版本照旧只读；数量金额与结构操作一字不放开。
REFERENCE_ITEM_FIELDS = frozenset({"supplier", "project_collection"})
SHIPMENT_VALUE_EDIT_FIELDS = frozenset({"shipment_value_rmb", "goods_value"})
SHIPMENT_VALUE_INPUT_FIELDS = frozenset({
    "unit_price", "purchase_currency", "unit_price_uom", "purchase_uom",
    "quantity", "unit", "actual_shipped_qty", "shipped_uom", "source_doc_no",
})
SERVER_ITEM_METADATA_FIELDS = frozenset({
    "shipment_valuation",
    "manual_shipment_valuation",
    "logistics_row",
    "autofill_review",
    "supplier_raw_value",
    "supplier_field_present",
    "supplier_match_status",
    "supplier_canonical_name",
    "supplier_candidates",
})
EDITABLE_ITEM_FIELDS = frozenset(
    {
        "material_code",
        "product_name",
        "product_name_es",
        "spec_model",
        "unit",
        "purchase_uom",
        "recipient",
        "unit_price",
        "unit_price_uom",
        "purchase_currency",
        "quantity",
        "actual_shipped_qty",
        "shipped_uom",
        "goods_value",
        "import_name",
        "hs_code",
        "category",
        "customs_no",
        "waybill_no",
        "container_no",
        "sea_bill_no",
        "commercial_invoice_no",
        "purchase_order_no",
        "china_misc_rmb",
        "china_misc_mxn",
        "china_ocean_usd",
        "cc_rate",
        "cc_anti_dumping",
        "igi_rate",
        "igi_amount",
        "iva_rate",
        "iva_amount",
        "dta",
        "prv_duty",
        "prv_iva",
        "import_tax_total",
        "revalidacion",
        "maniobras",
        "muellaje",
        "entrega_mercancia",
        "previo",
        "service_aa",
        "almacenajes",
        "reconocimiento_aduanero",
        "honorarios",
        "complemento_maniobras",
        "desconsolidacion",
        "maniobra_falso",
        "arrastre",
        "patio_regulador",
        "entrega_vacio",
        "limpieza_contenedor",
        "mexico_customs_mxn",
        "mexico_customs_rmb",
        "mexico_customs_usd",
        "mexico_inland_mxn",
        "mexico_misc_mxn",
        "mexico_inland_misc_rmb",
        "china_to_mexico_freight_rmb",
        "net_weight_kg",
        "gross_weight_kg",
        "volume_m3",
        "volume_weight_kg",
        "chargeable_weight_kg",
        "project_collection",
        "supplier",
        "transport_mode",
        "source_type",
        "source_doc_no",
        "source_file_name",
        "source_attachment_id",
        "parse_status",
        "manual_override_reason",
        "dingtalk_instance_id",
        "dingtalk_official_url",
        "source_remark",
        "raw_excel_json",
        "extra_json",
    }
)
SPECIAL_OVERRIDE_ITEM_FIELDS = frozenset({"weight_ratio", "alloc_price_mxn", "total_cost_rmb", "total_unit_rmb"})
READONLY_CALC_ITEM_FIELDS = frozenset(
    {
        "goods_value_ratio",
        "freight_alloc_rmb",
        "freight_alloc_mxn",
        "total_logistics_mxn",
        "derived_json",
    }
)
NUMERIC_ITEM_FIELDS = frozenset(
    {
        "unit_price",
        "quantity",
        "actual_shipped_qty",
        "goods_value",
        "china_misc_rmb",
        "china_misc_mxn",
        "china_ocean_usd",
        "cc_rate",
        "cc_anti_dumping",
        "igi_rate",
        "igi_amount",
        "iva_rate",
        "iva_amount",
        "goods_value_ratio",
        "dta",
        "prv_duty",
        "prv_iva",
        "import_tax_total",
        "revalidacion",
        "maniobras",
        "muellaje",
        "entrega_mercancia",
        "previo",
        "service_aa",
        "almacenajes",
        "reconocimiento_aduanero",
        "honorarios",
        "complemento_maniobras",
        "desconsolidacion",
        "maniobra_falso",
        "arrastre",
        "patio_regulador",
        "entrega_vacio",
        "limpieza_contenedor",
        "mexico_customs_mxn",
        "mexico_customs_rmb",
        "mexico_customs_usd",
        "mexico_inland_mxn",
        "mexico_misc_mxn",
        "mexico_inland_misc_rmb",
        "china_to_mexico_freight_rmb",
        "net_weight_kg",
        "gross_weight_kg",
        "volume_m3",
        "volume_weight_kg",
        "chargeable_weight_kg",
        "weight_ratio",
        "freight_alloc_rmb",
        "freight_alloc_mxn",
        "total_logistics_mxn",
        "alloc_price_mxn",
        "total_cost_rmb",
        "total_unit_rmb",
    }
)
CHECK_ITEM_FIELDS = frozenset({"manual_override_flag"})
SELECT_ITEM_OPTIONS = {
    "transport_mode": {"SEA", "AIR", "EXPRESS"},
    "purchase_currency": {"RMB", "USD", "MXN"},
    "parse_status": {"PENDING", "SUCCESS", "PARTIAL", "FAILED", "MANUAL"},
}
SELECT_ITEM_ALIASES = {
    "transport_mode": {
        "海运": "SEA",
        "空运": "AIR",
        "快递": "EXPRESS",
    },
    "purchase_currency": {
        "人民币": "RMB",
        "人民币RMB": "RMB",
        "CNY": "RMB",
        "美元": "USD",
        "美金": "USD",
        "美元Dólar": "USD",
        "美元Dolar": "USD",
        "比索": "MXN",
        "墨西哥比索": "MXN",
    },
}
DEFAULT_CALC_FIELDS = [
    "goods_value",
    "goods_value_ratio",
    "weight_ratio",
    "freight_alloc_rmb",
    "freight_alloc_mxn",
    "total_logistics_mxn",
    "alloc_price_mxn",
    "total_cost_rmb",
    "total_unit_rmb",
    "derived_json",
]
ITEM_QUERY_FIELDS = [
    "name",
    "batch",
    "version",
    "row_no",
    "material_code",
    "product_name",
    "spec_model",
    "transport_mode",
    "unit_price",
    "quantity",
    "goods_value",
    "gross_weight_kg",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "mexico_customs_mxn",
    "mexico_customs_rmb",
    "mexico_customs_usd",
    "china_misc_rmb",
    "china_misc_mxn",
    "china_ocean_usd",
    "china_to_mexico_freight_rmb",
    "mexico_inland_mxn",
    "mexico_misc_mxn",
    "mexico_inland_misc_rmb",
    "igi_amount",
    "iva_amount",
    "dta",
    "prv_duty",
    "prv_iva",
    "import_tax_total",
    "revalidacion",
    "maniobras",
    "muellaje",
    "entrega_mercancia",
    "previo",
    "service_aa",
    "almacenajes",
    "reconocimiento_aduanero",
    "honorarios",
    "complemento_maniobras",
    "desconsolidacion",
    "maniobra_falso",
    "arrastre",
    "patio_regulador",
    "entrega_vacio",
    "limpieza_contenedor",
]


def _now() -> str:
    if _frappe is not None:
        try:
            return _frappe.utils.now()
        except Exception:
            pass
    return _datetime.now().isoformat(timespec="seconds")


def _to_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _json_dumps(value) -> str:
    return _json.dumps(value, ensure_ascii=False, default=str)


def _coerce_check(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "y", "on", "是"} else 0


def _coerce_edit_value(fieldname: str, value, *, supplier_catalog=None):
    if fieldname in SHIPMENT_VALUE_EDIT_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(_("本次发货货值必须是有限非负数字。")) from None
        if not amount.is_finite() or amount < 0:
            raise ValueError(_("本次发货货值必须是有限非负数字。"))
        storage_value = float(amount)
        if not Decimal(str(storage_value)).is_finite():
            raise ValueError(_("本次发货货值必须是有限非负数字。"))
        return storage_value
    if fieldname in NUMERIC_ITEM_FIELDS:
        return _to_float(value)
    if fieldname in CHECK_ITEM_FIELDS:
        return _coerce_check(value)
    if fieldname in SELECT_ITEM_OPTIONS:
        text = str(value or "").strip()
        text = SELECT_ITEM_ALIASES.get(fieldname, {}).get(text, text.upper())
        if text and text not in SELECT_ITEM_OPTIONS[fieldname]:
            raise ValueError(_("字段 {0} 的值 {1} 不在允许范围内。").format(fieldname, text))
        return text
    if fieldname == "supplier":
        return validate_canonical_supplier(value, suppliers=supplier_catalog)
    if isinstance(value, (dict, list)):
        return _json_dumps(value)
    return "" if value is None else str(value).strip()


def _normalize_edit_remark(remark: str | None = None, manual_override_reason: str | None = None) -> str:
    return str(manual_override_reason or remark or "").strip()


def _server_metadata_fields(value, fields=SERVER_ITEM_METADATA_FIELDS) -> dict:
    """Extract reserved JSON keys while keeping unrelated legacy extensions editable."""
    if isinstance(value, str):
        try:
            value = _json.loads(value or "{}")
        except (TypeError, ValueError):
            value = {}
    if not isinstance(value, dict):
        return {}
    protected = set(fields) | {key for key in value if (str(key).startswith('settlement_') or key == 'effective_logistics_source')}
    return {key: value[key] for key in protected if key in value}


def assert_server_metadata_unchanged(previous, proposed, *, fields=SERVER_ITEM_METADATA_FIELDS) -> None:
    """Public writes may retain source facts, but cannot create, replace, or remove them."""
    before = _json.dumps(_server_metadata_fields(previous, fields), sort_keys=True, default=str)
    after = _json.dumps(_server_metadata_fields(proposed, fields), sort_keys=True, default=str)
    if before != after:
        raise ValueError(_("服务器来源元数据不能通过普通编辑修改，请重新分析资料并确认。"))


def _validate_edit_field(fieldname: str, remark: str = "") -> tuple[bool, str, str]:
    if not fieldname:
        return False, "字段名不能为空。", "missing"
    if fieldname in SHIPMENT_VALUE_EDIT_FIELDS:
        return True, "", "manual_shipment_valuation"
    if fieldname in EDITABLE_ITEM_FIELDS:
        return True, "", "editable"
    if fieldname in SPECIAL_OVERRIDE_ITEM_FIELDS:
        if remark:
            return True, "", "special_override"
        return False, f"字段 {fieldname} 是计算结果字段，人工覆盖需要填写修改原因。", "reason_required"
    if fieldname in READONLY_CALC_ITEM_FIELDS:
        return False, f"字段 {fieldname} 由重算服务生成，不能直接手工编辑。", "readonly_calc"
    return False, f"字段 {fieldname} 不在可编辑白名单内。", "not_allowed"


def _edit_values_equal(fieldname: str, old_value, new_value) -> bool:
    if fieldname in NUMERIC_ITEM_FIELDS:
        return _to_float(old_value) == _to_float(new_value)
    if fieldname in CHECK_ITEM_FIELDS:
        return _coerce_check(old_value) == _coerce_check(new_value)
    return ("" if old_value is None else str(old_value).strip()) == ("" if new_value is None else str(new_value).strip())


def _load_updates_payload(updates) -> list[dict]:
    if updates in (None, ""):
        return []
    loaded_updates = _json.loads(updates) if isinstance(updates, str) else updates
    if isinstance(loaded_updates, dict):
        return [loaded_updates]
    if isinstance(loaded_updates, list):
        return loaded_updates
    raise ValueError(_("批量更新参数必须是 JSON 对象或对象数组。"))


def _preview_update_result(update: dict, default_remark: str = "") -> dict:
    item_name = update.get("item_name") or update.get("name")
    fieldname = update.get("fieldname") or update.get("field_name")
    value = update.get("value") if "value" in update else update.get("field_value")
    remark = _normalize_edit_remark(update.get("remark") or default_remark, update.get("manual_override_reason"))
    if not item_name or not fieldname:
        return {
            "ok": False,
            "changed": False,
            "item_name": item_name,
            "fieldname": fieldname,
            "message": "批量更新行缺少 item_name/name 或 fieldname/field_name。",
        }
    is_allowed, message, edit_mode = _validate_edit_field(fieldname, remark)
    if not is_allowed:
        return {
            "ok": False,
            "changed": False,
            "item_name": item_name,
            "fieldname": fieldname,
            "message": message,
            "edit_mode": edit_mode,
        }
    try:
        coerced_value = _coerce_edit_value(fieldname, value)
        if fieldname == "extra_json":
            assert_server_metadata_unchanged(None, coerced_value)
    except ValueError as exc:
        return {
            "ok": False,
            "changed": False,
            "item_name": item_name,
            "fieldname": fieldname,
            "message": str(exc),
            "edit_mode": edit_mode,
        }
    return {
        "ok": True,
        "changed": True,
        "item_name": item_name,
        "fieldname": fieldname,
        "value": coerced_value,
        "manual_override_reason": remark,
        "edit_mode": edit_mode,
    }


def _load_payload(payload) -> dict:
    if payload in (None, ""):
        return {}
    if isinstance(payload, str):
        loaded = _json.loads(payload)
    else:
        loaded = payload
    if not isinstance(loaded, dict):
        raise ValueError(_("参数必须是 JSON 对象。"))
    return loaded


def _build_new_item_values(batch_doc_name: str, version_name: str, payload: dict, row_no: int | None = None) -> dict:
    values = {
        "doctype": "Overseas Cost Item",
        "batch": batch_doc_name,
        "version": version_name,
        "stable_line_key": material_input_service.ensure_stable_line_key({}),
    }
    if row_no is not None:
        values["row_no"] = row_no

    for fieldname, value in payload.items():
        if fieldname not in EDITABLE_ITEM_FIELDS and fieldname not in SPECIAL_OVERRIDE_ITEM_FIELDS:
            continue
        values[fieldname] = _coerce_edit_value(fieldname, value)

    quantity = _to_float(values.get("quantity"), default=0.0)
    unit_price = _to_float(values.get("unit_price"), default=0.0)
    purchase_uom = str(values.get("purchase_uom") or values.get("unit") or "").strip()
    shipped_uom = str(values.get("shipped_uom") or purchase_uom).strip()
    values["purchase_uom"] = purchase_uom
    values["shipped_uom"] = shipped_uom
    values["cost_output_uom"] = shipped_uom
    if _to_float(values.get("actual_shipped_qty"), default=0.0) > 0:
        values["actual_shipped_qty_mode"] = "MANUAL_CONFIRMED"
    else:
        values["actual_shipped_qty_mode"] = "DEFAULT_PURCHASE"
    if values.get("goods_value") in (None, "") and quantity and unit_price:
        values["goods_value"] = quantity * unit_price
    from overseas_costing.services.transport_service import prepare_item_transport
    mode = _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "transport_mode") if _frappe is not None else ""
    values = prepare_item_transport(values, mode)
    values.setdefault("manual_override_flag", 1)
    values.setdefault("manual_override_reason", "手工新增物料")
    return values


def _is_rule_enabled(rule: dict) -> bool:
    return all(_coerce_check(rule[field]) for field in ("is_enabled", "is_active") if rule.get(field) is not None)


class CalculationValidationError(ValueError):
    """The input cannot safely produce a formal monetary calculation."""


MONEY_QUANTUM = Decimal("0.000001")
FINAL_SCOPES = frozenset({"freight", "customs", "tax", "mexico_inland"})
_UNSET_FX = object()


def _decimal(value, label: str = "金额") -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CalculationValidationError(_("{0}缺失或无效，不能计算。").format(label)) from None
    if not result.is_finite():
        raise CalculationValidationError(_("{0}必须是有限数值，不能计算。").format(label))
    return result


def _positive_fx(value, label: str) -> Decimal:
    result = _decimal(value, f"{label}汇率")
    if result <= 0:
        raise CalculationValidationError(_("{0}汇率必须大于 0，不能计算。").format(label))
    return result


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _is_final_rule(rule: dict) -> bool:
    return bool(_coerce_check(rule.get("is_final")))


def _normalize_currency_code(value: str | None) -> str:
    text = str(value or "").strip().replace(" ", "").upper()
    aliases = {
        "RMB": "RMB", "CNY": "RMB", "人民币": "RMB", "人民币RMB": "RMB",
        "USD": "USD", "美元": "USD", "美金": "USD", "DÓLAR": "USD", "DOLAR": "USD",
        "美元DÓLAR": "USD", "美元DOLAR": "USD",
        "MXN": "MXN", "PESO": "MXN", "PESOS": "MXN", "比索": "MXN", "墨西哥比索": "MXN",
    }
    return aliases.get(text, text)


def _amount_to_rmb(amount, currency: str | None, fx_rmb_to_mxn, fx_usd_to_rmb) -> Decimal:
    amount = _decimal(amount)
    currency_code = _normalize_currency_code(currency)
    if currency_code == "MXN":
        return amount / _positive_fx(fx_rmb_to_mxn, "RMB/MXN")
    if currency_code == "USD":
        return amount * _positive_fx(fx_usd_to_rmb, "USD/RMB")
    if currency_code == "RMB":
        return amount
    raise CalculationValidationError(_("费用币种缺失或不支持：{0}。").format(currency or '未提供'))


def _rule_scopes(rule: dict) -> set[str]:
    if rule.get("covered_scopes"):
        return {scope.strip() for scope in str(rule["covered_scopes"]).split(",") if scope.strip()}
    code = str(rule.get("rule_code") or rule.get("fee_key") or "").lower()
    if code.startswith("mexico_inland"):
        return {"mexico_inland"}
    if "freight" in code or "ocean" in code:
        return {"freight"}
    if "customs" in code or "clearance" in code or code in CUSTOMS_SERVICE_FIELDS:
        return {"customs"}
    if "tax" in code or code in TAX_COMPONENT_FIELDS:
        return {"tax"}
    return set()


def _select_calculation_rules(items: list[dict], rules: list[dict]) -> tuple[list[dict], set[str]]:
    final_rules = [rule for rule in rules if _is_final_rule(rule)]
    covered = set()
    scope_sources = {}
    for rule in final_rules:
        if not _is_rule_enabled(rule):
            raise CalculationValidationError(_("最终结算规则已停用或来源失效，不能恢复旧估值；请核对结算来源。"))
        scopes = _rule_scopes(rule)
        if not rule.get("covered_scopes") or not scopes or scopes - FINAL_SCOPES:
            raise CalculationValidationError(_("最终结算费用覆盖范围缺失或无效。"))
        source = (str(rule.get("source_binding_id") or "").strip(), str(rule.get("source_snapshot") or "").strip())
        if not all(source):
            raise CalculationValidationError(_("最终结算来源绑定或来源快照缺失。"))
        for scope in scopes:
            if scope in scope_sources and scope_sources[scope] != source:
                raise CalculationValidationError(_("同一费用范围存在多个最终结算来源或快照，不能重复计费。"))
            scope_sources[scope] = source
        _decimal(rule.get("amount"))
        covered.update(scopes)

    legacy_rules = [rule for rule in rules if not _is_final_rule(rule) and _is_rule_enabled(rule)
                    and _to_float(rule.get("amount"))]
    selected = []
    for rule in legacy_rules:
        scopes = _rule_scopes(rule)
        if scopes & covered:
            if scopes - covered:
                raise CalculationValidationError(_("旧费用池与最终结算覆盖范围部分交叉，无法拆分整笔费用。"))
            continue
        selected.append(rule)
    if final_rules or not selected:
        # Covered estimates cannot decide whether independent costs get a fallback.
        # Keep every explicit code here, including disabled/zero rules, so those
        # pools cannot be recreated from stale item fields.
        explicit_codes = {rule.get("rule_code") or rule.get("fee_key") for rule in rules}
        explicit_scopes = covered.union(*(_rule_scopes(rule) for rule in rules))
        selected.extend(rule for rule in _fallback_rules_from_items(items)
                        if rule.get("rule_code") not in explicit_codes
                        and not (_rule_scopes(rule) & explicit_scopes))
    return selected + final_rules, covered


def _allocate_pool(amount: Decimal, weights: list[Decimal], rows: list[dict]) -> list[Decimal]:
    """Allocate one currency pool at six places; equal remainders use stable item identity."""
    amount = _money(amount)
    total = sum(weights, Decimal(0))
    if total <= 0:
        return [Decimal(0)] * len(rows)
    if any(weight < 0 for weight in weights):
        raise CalculationValidationError(_("分摊依据不能为负数。"))
    exact = [abs(amount) * weight / total for weight in weights]
    allocated = [value.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN) for value in exact]
    remainder_units = int((abs(amount) - sum(allocated, Decimal(0))) / MONEY_QUANTUM)
    order = sorted(range(len(rows)), key=lambda index: (
        -(exact[index] - allocated[index]),
        str(rows[index].get("name") or ""),
        _to_float(rows[index].get("row_no")),
        str(rows[index].get("material_code") or ""),
        _json.dumps(rows[index], sort_keys=True, ensure_ascii=False, default=str),
    ))
    for index in order[:remainder_units]:
        allocated[index] += MONEY_QUANTUM
    return [-value for value in allocated] if amount < 0 else allocated


def _basis_value(item: dict, basis: str) -> float:
    if basis == "gross_weight":
        return _to_float(item.get("gross_weight_kg"))
    if basis == "volume":
        return _to_float(item.get("volume_m3"))
    if basis in {"chargeable_weight", "chargeable_weight_kg"}:
        return _chargeable_weight_value(item)
    return _to_float(item.get("goods_value"))


def _chargeable_weight_value(item: dict) -> float:
    explicit = _to_float(item.get("chargeable_weight_kg"))
    if explicit:
        return explicit
    gross_weight = _to_float(item.get("gross_weight_kg"))
    volume_weight = _to_float(item.get("volume_weight_kg"))
    return max(gross_weight, volume_weight)


def _first_nonzero(items: list[dict], fieldname: str) -> float:
    for item in items:
        value = _to_float(item.get(fieldname))
        if value:
            return value
    return 0.0


def _has_any_positive(items: list[dict], fieldname: str) -> bool:
    return any(_to_float(item.get(fieldname)) for item in items)


def _total_value(items: list[dict], fieldname: str) -> float:
    return sum(_to_float(item.get(fieldname)) for item in items)


def _first_transport_mode(items: list[dict]) -> str:
    for item in items:
        value = str(item.get("transport_mode") or "").strip().upper()
        if value:
            return value
    return ""


def _default_freight_basis(items: list[dict]) -> str:
    if _total_value(items, "gross_weight_kg"):
        return "gross_weight"
    if sum(_chargeable_weight_value(item) for item in items):
        return "chargeable_weight"
    if _total_value(items, "volume_m3"):
        return "volume"
    return "goods_value"


def _add_basic_rule(
    specs: list[dict],
    *,
    items: list[dict],
    fieldname: str,
    rule_code: str,
    expense_category: str,
    allocation_basis: str,
    currency: str,
    remark: str,
    priority_no: int,
) -> None:
    amount = _first_nonzero(items, fieldname)
    if not amount:
        return
    specs.append(
        {
            "rule_code": rule_code,
            "expense_category": expense_category,
            "allocation_basis": allocation_basis,
            "basis_field": allocation_basis,
            "currency": currency,
            "amount": amount,
            "remark": remark,
            "priority_no": priority_no,
            "is_enabled": 1,
            "is_system_suggestion": 1,
        }
    )


def _fallback_rules_from_items(items: list[dict]) -> list[dict]:
    freight_basis = _default_freight_basis(items)
    misc_basis = "gross_weight"
    specs: list[dict] = []
    _add_basic_rule(
        specs,
        items=items,
        fieldname="china_misc_rmb",
        rule_code="china_misc_rmb",
        expense_category="中国段杂费",
        allocation_basis=misc_basis,
        currency="RMB",
        remark="系统基础分摊：来自明细字段“中国运输及相关杂费 RMB”，默认先按毛重分摊并填入每行金额；如属于抛货或特殊费用，人工可改为体积/计费重或其他口径后重算。",
        priority_no=10,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="china_misc_mxn",
        rule_code="china_misc_mxn",
        expense_category="中国段杂费",
        allocation_basis=misc_basis,
        currency="MXN",
        remark="系统基础分摊：来自明细字段“中国运输及相关杂费 MXN”，默认先按毛重分摊并填入每行金额；如属于抛货或特殊费用，人工可改为体积/计费重或其他口径后重算。",
        priority_no=11,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="china_ocean_usd",
        rule_code="china_ocean_usd",
        expense_category="中国海运费",
        allocation_basis=freight_basis,
        currency="USD",
        remark="系统基础分摊：来自明细字段“中国海运 USD”，运输费用默认先按毛重分摊；如确认属于抛货，可人工改为体积/计费重后重算，体积小重量大仍按重量。",
        priority_no=20,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="china_to_mexico_freight_rmb",
        rule_code="china_to_mexico_freight_rmb",
        expense_category="中国到墨西哥运费",
        allocation_basis=freight_basis,
        currency="RMB",
        remark="系统基础分摊：来自国际物流 OA、货代账单或明细字段“中国到墨西哥运费 RMB”，运输费用默认先按毛重分摊；如确认属于抛货，可人工改为体积/计费重后重算，体积小重量大仍按重量。",
        priority_no=21,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="mexico_inland_mxn",
        rule_code="mexico_inland_mxn",
        expense_category="墨西哥内陆运输费",
        allocation_basis=misc_basis,
        currency="MXN",
        remark="系统基础分摊：来自明细字段“墨西哥内陆运输费用 MXN”，按重量分摊；缺少重量时暂停该费用分摊并提示补充数据。",
        priority_no=30,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="mexico_misc_mxn",
        rule_code="mexico_misc_mxn",
        expense_category="墨西哥杂费",
        allocation_basis=misc_basis,
        currency="MXN",
        remark="系统基础分摊：来自明细字段“墨西哥杂费 MXN”，按重量分摊；缺少重量时暂停该费用分摊并提示补充数据。",
        priority_no=31,
    )
    _add_basic_rule(
        specs,
        items=items,
        fieldname="mexico_inland_misc_rmb",
        rule_code="mexico_inland_misc_rmb",
        expense_category="墨西哥内陆/杂费",
        allocation_basis=misc_basis,
        currency="RMB",
        remark="系统基础分摊：来自清关资料、墨西哥本地费用资料或明细字段“墨西哥内陆运输+杂费 RMB”，默认按重量分摊并填入每行金额，人工可复核调整。",
        priority_no=32,
    )
    return specs


TAX_COMPONENT_FIELDS = ("igi_amount", "iva_amount", "dta", "prv_duty", "prv_iva")
CUSTOMS_SERVICE_FIELDS = (
    "revalidacion",
    "maniobras",
    "muellaje",
    "entrega_mercancia",
    "previo",
    "service_aa",
    "almacenajes",
    "reconocimiento_aduanero",
    "honorarios",
    "complemento_maniobras",
    "desconsolidacion",
    "maniobra_falso",
    "arrastre",
    "patio_regulador",
    "entrega_vacio",
    "limpieza_contenedor",
)


def _direct_customs_amounts(
    row: dict, fx_rmb_to_mxn, fx_usd_to_rmb, covered_scopes: set[str] | None = None,
) -> tuple[Decimal, Decimal, dict]:
    covered = (covered_scopes or set()) & {"customs", "tax"}
    source = "墨西哥清关费用字段"
    source_type = "customs_total"
    policy = "采用清关费用总额，不再叠加关税、增值税和清关服务明细"
    tax_mxn = service_mxn = Decimal(0)
    if covered == {"customs", "tax"}:
        customs_rmb = customs_mxn = Decimal(0)
        source = "最终结算费用池"
        source_type = "final_settlement"
        policy = "最终结算已覆盖清关和税费，原明细总额及组成项不再重复计入"
    else:
        customs_mxn = _decimal(row.get("mexico_customs_mxn") or 0)
        customs_rmb = _decimal(row.get("mexico_customs_rmb") or 0)
        customs_usd = _decimal(row.get("mexico_customs_usd") or 0)
        if covered and any((customs_mxn, customs_rmb, customs_usd)):
            raise CalculationValidationError(_("清关总额与最终结算覆盖范围部分交叉，需明确税费与服务费拆分。"))
        if not customs_mxn and not customs_rmb and customs_usd:
            customs_rmb = _amount_to_rmb(customs_usd, "USD", fx_rmb_to_mxn, fx_usd_to_rmb)
            source = "墨西哥清关费用 USD"
        if not customs_mxn and not customs_rmb:
            source_type = "customs_components"
            if "tax" not in covered:
                tax_mxn = _decimal(row.get("import_tax_total") or 0)
                if not tax_mxn:
                    tax_mxn = sum((_decimal(row.get(field) or 0) for field in TAX_COMPONENT_FIELDS), Decimal(0))
            if "customs" not in covered:
                service_mxn = sum((_decimal(row.get(field) or 0) for field in CUSTOMS_SERVICE_FIELDS), Decimal(0))
            customs_mxn = tax_mxn + service_mxn
            if customs_mxn:
                source = "税费/清关明细字段"
                policy = "未提供清关费用总额，采用物料实际税费与清关服务明细合计"
            else:
                source = "未提供清关费用"
                policy = "未识别到清关费用总额或组成明细"
        if not customs_rmb and customs_mxn:
            customs_rmb = _amount_to_rmb(customs_mxn, "MXN", fx_rmb_to_mxn, fx_usd_to_rmb)
        if not customs_mxn and customs_rmb:
            customs_mxn = customs_rmb * _positive_fx(fx_rmb_to_mxn, "RMB/MXN")

    return _money(customs_rmb), _money(customs_mxn), {
        "source": source,
        "source_type": source_type,
        "policy": policy,
        "suppressed_scopes": sorted(covered),
        "tax_policy": "关税按物料品类/海关编码实际税率；IVA 增值税按 CIF 价值加关税后乘 16%，最终以完税凭证或实际付款为准。",
        "tax_mxn": _round_money(tax_mxn, 6),
        "service_mxn": _round_money(service_mxn, 6),
    }


def _basis_decimal(item: dict, basis: str) -> Decimal:
    if basis in {"chargeable_weight", "chargeable_weight_kg"}:
        explicit = _decimal(item.get("chargeable_weight_kg") or 0, "计费重")
        return explicit or max(_decimal(item.get("gross_weight_kg") or 0, "毛重"),
                               _decimal(item.get("volume_weight_kg") or 0, "体积重"))
    fields = {"goods_value": "goods_value", "gross_weight": "gross_weight_kg", "volume": "volume_m3"}
    if basis not in fields:
        raise CalculationValidationError(_("不支持的分摊依据：{0}。").format(basis))
    return _decimal(item.get(fields[basis]) or 0, "分摊依据")


def calculate_item_rows(
    items: list[dict],
    rules: list[dict] | None = None,
    *,
    fx_rmb_to_mxn=_UNSET_FX,
    fx_usd_to_rmb: float | None = None,
) -> tuple[list[dict], dict]:
    """Pure calculation; invalid final source/currency/FX raises before results can be persisted."""
    selected_rules, covered = _select_calculation_rules(items, rules or [])
    return _calculate_selected_item_rows(
        items, selected_rules, covered,
        fx_rmb_to_mxn=fx_rmb_to_mxn, fx_usd_to_rmb=fx_usd_to_rmb,
    )


def _calculate_selected_item_rows(
    items: list[dict],
    enabled_rules: list[dict],
    covered: set[str],
    *,
    fx_rmb_to_mxn=_UNSET_FX,
    fx_usd_to_rmb: float | None = None,
) -> tuple[list[dict], dict]:
    """Calculate exactly the selected pools without recreating any fallback rules."""
    rows = [_deepcopy(item) for item in items]
    for row in rows:
        quantity = _decimal(row.get("quantity") or 0, "数量")
        unit_price = _decimal(row.get("unit_price") or 0, "采购单价")
        if row.get("goods_value") in (None, "") and quantity and unit_price:
            row["goods_value"] = float(_money(quantity * unit_price))

    has_final = any(_is_final_rule(rule) for rule in enabled_rules)
    if fx_rmb_to_mxn is _UNSET_FX:
        fx_rmb_to_mxn = None if has_final else DEFAULT_FX_RMB_TO_MXN
    mxn_rate = _positive_fx(fx_rmb_to_mxn, "RMB/MXN")
    basis_totals_decimal = {
        basis: sum((_basis_decimal(row, basis) for row in rows), Decimal(0))
        for basis in ("goods_value", "gross_weight", "volume", "chargeable_weight", "chargeable_weight_kg")
    }
    basis_totals = {basis: float(value) for basis, value in basis_totals_decimal.items()}
    total_goods_value = basis_totals_decimal["goods_value"]

    pools = []
    for rule in enabled_rules:
        amount_rmb_exact = _amount_to_rmb(rule.get("amount"), rule.get("currency"), mxn_rate, fx_usd_to_rmb)
        amount_rmb = _money(amount_rmb_exact)
        amount_mxn = _money(amount_rmb_exact * mxn_rate)
        basis = rule.get("allocation_basis") or rule.get("basis_field") or "goods_value"
        weights = [_basis_decimal(row, basis) for row in rows]
        pools.append({
            "rule": rule, "basis": basis, "weights": weights,
            "basis_total": sum(weights, Decimal(0)),
            "amount_rmb": amount_rmb, "amount_mxn": amount_mxn,
            "rmb": _allocate_pool(amount_rmb, weights, rows),
            "mxn": _allocate_pool(amount_mxn, weights, rows),
        })

    total_cost_rmb = total_logistics_mxn = Decimal(0)
    calculated_rows = []
    for index, row in enumerate(rows):
        goods_value = _money(_decimal(row.get("goods_value") or 0))
        quantity = _decimal(row.get("quantity") or 0, "数量")
        mexico_customs_rmb, mexico_customs_mxn, customs_detail = _direct_customs_amounts(
            row, mxn_rate, fx_usd_to_rmb, covered,
        )
        allocated_other_rmb = allocated_other_mxn = Decimal(0)
        freight_alloc_rmb = freight_alloc_mxn = Decimal(0)
        allocated_rules = []
        for pool in pools:
            rule = pool["rule"]
            allocated_rmb = pool["rmb"][index]
            allocated_mxn = pool["mxn"][index]
            rule_code = rule.get("rule_code") or rule.get("fee_key") or ""
            if "freight" in _rule_scopes(rule):
                freight_alloc_rmb += allocated_rmb
                freight_alloc_mxn += allocated_mxn
            else:
                allocated_other_rmb += allocated_rmb
                allocated_other_mxn += allocated_mxn
            allocated_rules.append({
                "rule_code": rule_code,
                "expense_category": rule.get("expense_category") or "",
                "amount": float(_money(_decimal(rule.get("amount")))),
                "currency": _normalize_currency_code(rule.get("currency")),
                "amount_rmb": float(pool["amount_rmb"]),
                "amount_mxn": float(pool["amount_mxn"]),
                "basis": pool["basis"],
                "basis_label": pool["basis"],
                "ratio": float(_safe_div(pool["weights"][index], pool["basis_total"])),
                "allocated_rmb": float(allocated_rmb),
                "allocated_mxn": float(allocated_mxn),
                "remark": rule.get("remark") or "",
                "is_final": int(_is_final_rule(rule)),
                "source_binding_id": rule.get("source_binding_id") or "",
                "source_snapshot": rule.get("source_snapshot") or "",
                "covered_scopes": rule.get("covered_scopes") or "",
            })

        row_total_logistics_mxn = mexico_customs_mxn + freight_alloc_mxn + allocated_other_mxn
        row_total_cost_rmb = goods_value + mexico_customs_rmb + freight_alloc_rmb + allocated_other_rmb
        row.update({
            "goods_value": float(goods_value),
            "goods_value_ratio": _round_money(_safe_div(goods_value, total_goods_value) * 100, 6),
            "weight_ratio": _round_money(_safe_div(_basis_decimal(row, "gross_weight"), basis_totals_decimal["gross_weight"]) * 100, 6),
            "freight_alloc_rmb": float(freight_alloc_rmb),
            "freight_alloc_mxn": float(freight_alloc_mxn),
            "total_logistics_mxn": float(row_total_logistics_mxn),
            "alloc_price_mxn": _round_money(_safe_div(row_total_logistics_mxn, quantity), 6),
            "total_cost_rmb": float(row_total_cost_rmb),
            "total_unit_rmb": _round_money(_safe_div(row_total_cost_rmb, quantity), 6),
            "derived_json": _json_dumps({
                "basis_totals": basis_totals,
                "chargeable_weight_kg": _round_money(_basis_decimal(row, "chargeable_weight"), 6),
                "fx_rmb_to_mxn": float(mxn_rate),
                "fx_usd_to_rmb": fx_usd_to_rmb,
                "allocated_rules": allocated_rules,
                "allocated_other_rmb": float(allocated_other_rmb),
                "mexico_customs_rmb": float(mexico_customs_rmb),
                "mexico_customs_mxn": float(mexico_customs_mxn),
                "direct_customs": {**customs_detail, "amount_rmb": float(mexico_customs_rmb),
                                   "amount_mxn": float(mexico_customs_mxn)},
            }),
        })
        total_cost_rmb += row_total_cost_rmb
        total_logistics_mxn += row_total_logistics_mxn
        calculated_rows.append(row)

    summary = {
        "total_goods_value": _round_money(total_goods_value, 6),
        "total_gross_weight_kg": _round_money(basis_totals_decimal["gross_weight"], 6),
        "total_volume_m3": _round_money(basis_totals_decimal["volume"], 6),
        "total_chargeable_weight_kg": _round_money(basis_totals_decimal["chargeable_weight"], 6),
        "total_logistics_mxn": float(total_logistics_mxn),
        "total_cost_rmb": float(total_cost_rmb),
        "fee_pool_rmb": float(sum((pool["amount_rmb"] for pool in pools), Decimal(0))),
        "fee_pool_mxn": float(sum((pool["amount_mxn"] for pool in pools), Decimal(0))),
        "final_covered_scopes": sorted(covered),
        "item_count": len(rows),
        "rule_count": len(enabled_rules),
        "source_priority_policy": source_priority_service.get_source_priority_policy(),
    }
    summary["calculation_review"] = _build_calculation_review(calculated_rows, summary, enabled_rules)
    return calculated_rows, summary


def _build_calculation_review(
    calculated_rows: list[dict],
    summary_snapshot: dict,
    rules_for_calculation: list[dict] | None = None,
    ai_allocation: dict | None = None,
) -> dict:
    rows = calculated_rows or []
    rules = rules_for_calculation or []
    positive_rules = [
        rule
        for rule in rules
        if _is_rule_enabled(rule) and (_to_float(rule.get("amount")) or _to_float(rule.get("amount_rmb")))
    ]
    item_count = len(rows) or int(_to_float(summary_snapshot.get("item_count")))
    total_goods_value = _to_float(summary_snapshot.get("total_goods_value"))
    total_cost_rmb = _to_float(summary_snapshot.get("total_cost_rmb"))
    fee_pool_rmb = _to_float(summary_snapshot.get("fee_pool_rmb"))
    basis_values = {str(rule.get("allocation_basis") or rule.get("basis_field") or "") for rule in positive_rules}
    needs_weight = "gross_weight" in basis_values
    needs_volume = "volume" in basis_values or "volume_m3" in basis_values

    counts = {
        "missing_quantity": sum(1 for row in rows if not _to_float(row.get("quantity"))),
        "missing_unit_price": sum(1 for row in rows if not _to_float(row.get("unit_price"))),
        "missing_goods_value": sum(1 for row in rows if not _to_float(row.get("goods_value"))),
        "missing_gross_weight": sum(1 for row in rows if not _to_float(row.get("gross_weight_kg"))),
        "missing_volume": sum(1 for row in rows if not _to_float(row.get("volume_m3"))),
        "missing_total_unit_cost": sum(1 for row in rows if not _to_float(row.get("total_unit_rmb"))),
    }
    allocated_fee_rmb = 0.0
    for row in rows:
        allocated_fee_rmb += _to_float(row.get("freight_alloc_rmb"))
        derived = _load_json_dict(row.get("derived_json"))
        allocated_fee_rmb += _to_float(derived.get("allocated_other_rmb"))

    reasons: list[str] = []
    blocking = False
    if item_count <= 0:
        blocking = True
        reasons.append("当前没有物料明细，不能试算综合成本")
    if item_count > 0 and total_goods_value <= 0:
        blocking = True
        reasons.append("采购货值为空，综合成本没有计算基准")
    basis_totals = {
        "goods_value": sum(_to_float(row.get("goods_value")) for row in rows),
        "gross_weight": sum(_to_float(row.get("gross_weight_kg")) for row in rows),
        "volume": sum(_to_float(row.get("volume_m3")) for row in rows),
        "chargeable_weight": sum(_chargeable_weight_value(row) for row in rows),
        "chargeable_weight_kg": sum(_chargeable_weight_value(row) for row in rows),
    }
    basis_labels = {
        "goods_value": "货值",
        "gross_weight": "毛重",
        "volume": "体积",
        "chargeable_weight": "计费重",
        "chargeable_weight_kg": "计费重",
    }
    unavailable_bases = [basis for basis in basis_values if basis in basis_totals and basis_totals[basis] <= 0]
    if unavailable_bases:
        blocking = True
        missing_labels = "、".join(basis_labels.get(basis, basis) for basis in sorted(unavailable_bases))
        reasons.append(f"当前费用规则需要按{missing_labels}分摊，但整批缺少对应数据，相关费用未分摊")
    if counts["missing_quantity"]:
        reasons.append(
            f"数量缺失或为 0 的物料 {counts['missing_quantity']} 行"
            f"{_problem_row_examples(rows, lambda row: not _to_float(row.get('quantity')))}"
        )
    if counts["missing_unit_price"]:
        reasons.append(
            f"采购单价缺失或为 0 的物料 {counts['missing_unit_price']} 行"
            f"{_problem_row_examples(rows, lambda row: not _to_float(row.get('unit_price')))}"
        )
    if counts["missing_goods_value"]:
        reasons.append(
            f"货值缺失或为 0 的物料 {counts['missing_goods_value']} 行"
            f"{_problem_row_examples(rows, lambda row: not _to_float(row.get('goods_value')))}"
        )
    if needs_weight and counts["missing_gross_weight"]:
        reasons.append(
            f"当前按重量分摊，毛重缺失或为 0 的物料 {counts['missing_gross_weight']} 行"
            f"{_problem_row_examples(rows, lambda row: not _to_float(row.get('gross_weight_kg')))}"
        )
    if basis_values.intersection({"chargeable_weight", "chargeable_weight_kg"}):
        missing_chargeable = sum(1 for row in rows if not _chargeable_weight_value(row))
        if missing_chargeable:
            reasons.append(
                f"当前按计费重分摊，计费重/毛重/体积重均缺失或为 0 的物料 {missing_chargeable} 行"
                f"{_problem_row_examples(rows, lambda row: not _chargeable_weight_value(row))}"
            )
    if needs_volume and counts["missing_volume"]:
        reasons.append(
            f"当前按体积分摊，体积缺失或为 0 的物料 {counts['missing_volume']} 行"
            f"{_problem_row_examples(rows, lambda row: not _to_float(row.get('volume_m3')))}"
        )
    has_final = any(_is_final_rule(rule) for rule in rules)
    if item_count > 0 and not positive_rules and not has_final:
        reasons.append("当前没有费用池，费用分摊金额为 0")
    elif positive_rules and not allocated_fee_rmb and fee_pool_rmb:
        reasons.append("费用池已识别，但分摊结果为 0，请检查分摊依据字段")
    unallocated_fee_rmb = abs(fee_pool_rmb - allocated_fee_rmb)
    if positive_rules and unallocated_fee_rmb > 0.01:
        reasons.append(f"费用池仍有 {_round_money(unallocated_fee_rmb, 2):g} RMB 未分摊")
    if total_cost_rmb <= 0 and item_count > 0:
        reasons.append("综合成本未生成或为 0")

    ai_message = ""
    ai_notes: list[str] = []
    if ai_allocation is not None:
        ai_message = ai_allocation.get("message") or ai_allocation.get("reason") or ""
        if ai_allocation.get("ok"):
            ai_notes.append("AI已选择分摊依据，金额由系统按规则计算")
        elif ai_message:
            reasons.append(f"AI未返回可用分摊口径，已使用系统基础规则：{ai_message}")

    if blocking:
        status = "blocked"
        label = "待补数据"
    elif reasons:
        status = "review"
        label = "需人工复核"
    else:
        status = "usable"
        label = "可先采用"
        reasons.append("核心采购金额、费用池和分摊结果已生成，可作为演示试算结果")
    if ai_notes:
        reasons.extend(ai_notes)

    return {
        "status": status,
        "label": label,
        "reason": "；".join(reasons[:4]),
        "reasons": reasons,
        "counts": counts,
        "fee_rule_count": len(positive_rules),
        "fee_pool_rmb": _round_money(fee_pool_rmb, 6),
        "allocated_fee_rmb": _round_money(allocated_fee_rmb, 6),
        "unallocated_fee_rmb": _round_money(unallocated_fee_rmb, 6),
        "total_goods_value": _round_money(total_goods_value, 6),
        "total_cost_rmb": _round_money(total_cost_rmb, 6),
        "ai_used": bool((ai_allocation or {}).get("ok")),
        "ai_message": ai_message,
    }


def _load_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _problem_row_examples(rows: list[dict], predicate, limit: int = 3) -> str:
    examples = []
    for row in rows:
        if not predicate(row):
            continue
        row_no = row.get("row_no") or row.get("idx") or ""
        material = str(row.get("material_code") or "").strip()
        product = str(row.get("product_name") or row.get("spec_model") or "").strip()
        parts = []
        if row_no:
            parts.append(f"第{row_no}行")
        if material:
            parts.append(material)
        if product:
            parts.append(product)
        examples.append(" ".join(parts) or str(row.get("name") or "未命名物料"))
        if len(examples) >= limit:
            break
    if not examples:
        return ""
    suffix = "等" if sum(1 for row in rows if predicate(row)) > len(examples) else ""
    return f"（{', '.join(examples)}{suffix}）"


def _resolve_batch_name(batch_name: str) -> str | None:
    if _frappe is None:
        return None

    batch = _frappe.db.get_value("Overseas Cost Batch", batch_name, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    batch = _frappe.db.get_value("Overseas Cost Batch", {"batch_no": batch_name}, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    return None


def _resolve_version_name(batch_doc_name: str, version_name: str | None = None) -> str | None:
    if _frappe is None:
        return version_name
    if version_name:
        return version_name
    current_version = _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "current_version")
    if current_version:
        return current_version
    latest_rows = _frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=["name"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return latest_rows[0]["name"] if latest_rows else None


def _get_version_context(version_name: str) -> dict:
    if _frappe is None or not version_name:
        return {}
    row = _frappe.db.get_value(
        "Overseas Cost Version",
        version_name,
        ["name", "fx_usd_to_rmb", "fx_rmb_to_mxn", "version_type"],
        as_dict=True,
    )
    return dict(row or {})


def _get_items(batch_doc_name: str, version_name: str) -> list[dict]:
    return _frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": version_name, "is_excluded": 0},
        fields=ITEM_QUERY_FIELDS,
        order_by="row_no asc",
        limit_page_length=10000,
    )


def _get_rules(batch_doc_name: str, version_name: str) -> list[dict]:
    return _frappe.get_all(
        "Overseas Cost Allocation Rule",
        filters={"batch": batch_doc_name, "version": version_name},
        fields=[
            "name",
            "rule_code",
            "expense_category",
            "allocation_basis",
            "basis_field",
            "currency",
            "amount",
            "remark",
            "is_active",
            "is_enabled",
            "priority_no",
            "source_binding_id",
            "source_snapshot",
            "covered_scopes",
            "is_final",
        ],
        order_by="priority_no asc, modified asc",
        limit_page_length=1000,
    )


def _insert_audit_log(
    *,
    batch_doc_name: str,
    version_name: str | None,
    action_type: str,
    field_name: str = "",
    row_no: int | None = None,
    old_value=None,
    new_value=None,
    action_remark: str = "",
) -> None:
    if _frappe is None:
        return

    operator_name = ""
    session_user = getattr(getattr(_frappe, "session", None), "user", None)
    if session_user and session_user != "Guest":
        operator_name = session_user

    _frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_doc_name,
            "version": version_name,
            "action_type": action_type,
            "field_name": field_name,
            "row_no": row_no,
            "old_value": "" if old_value is None else str(old_value),
            "new_value": "" if new_value is None else str(new_value),
            "operator_name": operator_name,
            "action_remark": action_remark,
        }
    ).insert(ignore_permissions=True)


def _item_version_context(batch_name, item_version, requested_version=None):
    """锁定并返回「批次 + 版本」的写入上下文，供下面两档门槛共用（只查一次库）。"""

    if requested_version and requested_version != item_version:
        raise ValueError(_('物料不属于所选版本，请刷新当前调整草稿。'))
    rows = _frappe.db.sql(
        "SELECT b.current_version, b.confirm_status, b.writeback_status, v.status AS version_status "
        "FROM `tabOverseas Cost Batch` b JOIN `tabOverseas Cost Version` v ON v.batch=b.name "
        "WHERE b.name=%s AND v.name=%s FOR UPDATE", (batch_name, item_version), as_dict=True)
    return rows[0] if rows else {}


def _version_allows_cost_edits(context, item_version) -> bool:
    """成本字段（数量、金额、装箱组结构）的门槛：必须是当前未确认、未回写的活动版本。"""

    return bool(
        context.get('current_version') == item_version
        and context.get('version_status') == 'Active'
        and context.get('confirm_status') != 'Confirmed'
        and context.get('writeback_status') != 'Success'
    )


def _assert_current_item_version(batch_name, item_version, requested_version=None, *, reference_fields=False):
    """物料写入门槛，两档。

    ``reference_fields=True``：只写 :data:`REFERENCE_ITEM_FIELDS`（供应商 / 项目归属）时放宽为
    「必须是本批次的当前版本、且版本未归档」。这两项是采购在成本确认后补的 ERP 基础资料、
    不参与金额计算，所以已确认 / 已锁定 / 已回写的当前版本仍要能维护；同时
    :func:`_refresh_saved_input_hash_for_reference_change` 会把已存结果的输入指纹重新对齐，
    批次不会被钉在 RESULT_STALE 上。

    默认档（历史版本、数量金额、结构操作）行为与放宽前逐字一致。
    """

    context = _item_version_context(batch_name, item_version, requested_version)
    if reference_fields:
        if (context.get('current_version') != item_version
                or str(context.get('version_status') or '') in {'', 'Archived'}):
            raise ValueError(_('只能维护当前版本的供应商与项目归属，历史或归档版本请创建调整草稿。'))
        return context
    if not _version_allows_cost_edits(context, item_version):
        raise ValueError(_('只能编辑当前未确认的活动版本，历史版本请创建调整草稿。'))
    return context


def _refresh_saved_input_hash_for_reference_change(batch_doc_name: str, version_name: str) -> None:
    """把已存结果重新对齐到当前的参考字段（只用于 :data:`REFERENCE_ITEM_FIELDS` 的写入）。

    两件事，都只碰参考字段，金额与分摊一个字节都不动：

    1. 快照里的物料列表带了一份 ``project_collection`` 的透传副本（见
       ``cost_preview_service.build_saved_cost_data`` 的 preview_items），
       ``cost_review_service._saved_result_matches`` 会逐条比对它 —— 只刷指纹不刷它，
       批次会从 ``RESULT_STALE`` 换成 ``SAVED_RESULT_INVALID``，还是「待重新计算」。
       快照里没有的参考字段（``supplier``）自然不同步。
    2. 重写 ``input_hash``。这两项按 ``COST_INPUT_FIELDS`` 契约在指纹里，但分摊计算从不读它们
       （见常量注释）；而已确认版本不允许重算 ——
       ``cost_preview_service.calculate_comprehensive_cost`` 对 Confirmed/Archived/is_locked
       直接 ``PermissionError``，解锁只有物流结算链。所以不对齐，批次会被永久判成
       ``RESULT_STALE`` 且无路可清。

    现算走 ``cost_review_service.project_review_inputs`` —— 与判 RESULT_STALE 的是同一份实现，
    不在这里另写一遍投影。
    """

    if _frappe is None:
        return
    from overseas_costing.services import batch_service, cost_review_service

    try:
        context = batch_service._load_erp_push_context(batch_doc_name, version_name)
    except Exception:  # noqa: BLE001 - 读上下文失败不该把已经写成功的字段回报成失败
        return
    if not context or not context.get("ok"):
        return
    version = context.get("version") or {}
    resolved_version = str(version.get("name") or "")
    if not resolved_version:
        return
    snapshot = _load_json_dict(version.get("summary_snapshot_json"))
    if not snapshot:
        return
    live_reference = {
        str(row.get("name") or ""): {field: row.get(field) for field in REFERENCE_ITEM_FIELDS}
        for row in (context.get("items") or [])
    }
    changed = False
    for row in ((snapshot.get("comprehensive_cost") or {}).get("items") or []):
        current = live_reference.get(str(row.get("name") or ""))
        if not current:
            continue
        for field, value in current.items():
            # 快照里没有的参考字段（supplier）不凭空加进去；有的就跟着当前值走。
            if field in row and row.get(field) != value:
                row[field] = value
                changed = True
    projection = cost_review_service.project_review_inputs(
        batch=context.get("batch") or {},
        version=version,
        items=context.get("items") or [],
        fees=context.get("rules") or [],
        fee_components=context.get("fee_components") or [],
        source_context=context.get("source_context") or {},
    )
    refreshed = str(projection.get("current_hash") or "")
    if not refreshed or (not changed and snapshot.get("input_hash") == refreshed):
        return
    snapshot["input_hash"] = refreshed
    _frappe.db.set_value(
        "Overseas Cost Version", resolved_version, "summary_snapshot_json",
        _json.dumps(snapshot, ensure_ascii=False, default=str), update_modified=False,
    )
    _insert_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version,
        action_type="EDIT",
        action_remark="已确认版本维护供应商 / 项目归属：已存结果的输入指纹已重新对齐（金额未变）",
    )


def update_item_field(
    item_name: str,
    fieldname: str,
    value: str,
    version_name: str | None = None,
    remark: str | None = None,
    manual_override_reason: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
    _skip_edit_check: bool = False,
    _skip_commit: bool = False,
    _supplier_catalog=None,
    _defer_reference_rebaseline: bool = False,
) -> dict:
    """更新单行明细中的一个字段。

    ``_defer_reference_rebaseline``：批量入口逐行调用本函数时置位，把「刷新已存结果的输入
    指纹」推迟到整批写入之后做一次 —— 否则 N 行会各重算一遍全批哈希。
    """
    is_shipment_value = fieldname in SHIPMENT_VALUE_EDIT_FIELDS
    edit_remark = _normalize_edit_remark(remark, manual_override_reason)
    is_allowed, validation_message, edit_mode = _validate_edit_field(fieldname, edit_remark)
    if not is_allowed:
        return {
            "ok": False,
            "changed": False,
            "dry_run": _frappe is None,
            "item_name": item_name,
            "fieldname": fieldname,
            "version_name": version_name,
            "edit_mode": edit_mode,
            "message": validation_message,
        }

    try:
        coerced_value = _coerce_edit_value(fieldname, value, supplier_catalog=_supplier_catalog)
        if fieldname == "extra_json" and _frappe is None:
            assert_server_metadata_unchanged(None, coerced_value)
    except ValueError as exc:
        return {
            "ok": False,
            "changed": False,
            "dry_run": _frappe is None,
            "item_name": item_name,
            "fieldname": fieldname,
            "version_name": version_name,
            "edit_mode": edit_mode,
            "message": str(exc),
        }

    companion_updates = {}
    if fieldname == "actual_shipped_qty":
        if _to_float(coerced_value) <= 0:
            return {
                "ok": False,
                "changed": False,
                "dry_run": _frappe is None,
                "item_name": item_name,
                "fieldname": fieldname,
                "version_name": version_name,
                "edit_mode": edit_mode,
                "message": "实际发货数量必须大于 0。",
            }
        companion_updates = {
            "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
            "actual_shipped_qty_source_revision": _now(),
        }

    if _frappe is None:
        audit_service.build_audit_stub("EDIT", {"item_name": item_name, "fieldname": fieldname})
        return {
            "ok": True,
            "changed": True,
            "dry_run": True,
            "item_name": item_name,
            "fieldname": fieldname,
            "value": coerced_value,
            "version_name": version_name,
            "manual_override_reason": edit_remark,
            "companion_updates": companion_updates,
            "edit_mode": edit_mode,
            "message": "当前未连接 Frappe，已返回编辑预览。",
        }

    item_doc = _frappe.get_doc("Overseas Cost Item", item_name)
    if int(getattr(item_doc, "is_excluded", 0) or 0):
        return {"ok": False, "changed": False, "item_name": item_name,
                "message": "该物料已被软排除，请先恢复后再编辑。"}
    from overseas_costing.services.effective_source_values import PHYSICAL_FIELDS, batch_source_context, project_source_values, physical_overlay_update
    source_context = batch_source_context(item_doc.batch,item_doc.version,lock=True)
    expense_physical = (source_context.get('root_kind') == 'expense' or (source_context.get('packing') or {}).get('selected_source')) and fieldname in PHYSICAL_FIELDS
    if not _skip_edit_check:
        from overseas_costing.services import edit_session_service

        edit_session_service.assert_batch_write(
            item_doc.batch,
            edit_token=edit_token,
            expected_modified=expected_modified,
        )
    from overseas_costing.services.shipment_cost_service import number as shipment_number
    from overseas_costing.services.shipment_cost_service import (
        legacy_manual_shipment_valuation, object_json, shipment_input_fingerprint, shipment_value,
    )
    existing_metadata = object_json(getattr(item_doc, "extra_json", None))
    existing_manual = existing_metadata.get("manual_shipment_valuation")
    existing_legacy_manual = legacy_manual_shipment_valuation(existing_metadata)
    active_manual = existing_manual if isinstance(existing_manual, dict) else existing_legacy_manual
    old_value = active_manual.get("amount_rmb") if is_shipment_value and isinstance(active_manual, dict) else getattr(item_doc, fieldname, None)
    if expense_physical:
        old_value = project_source_values(item_doc.as_dict(),source_context).get(fieldname)
    if (fieldname in {'material_code', 'product_name', 'spec_model'}
            and 'settlement_cargo' in _server_metadata_fields(getattr(item_doc, 'extra_json', None))
            and not _edit_values_equal(fieldname, old_value, coerced_value)):
        return {'ok': False, 'changed': False, 'item_name': item_name, 'fieldname': fieldname,
                'message': '物料身份已采用物流结算采购支出，请在关联来源中更正后重新应用。'}
    if fieldname == "extra_json":
        try:
            # This API saves with ignore_permissions=True after validating its payload.
            # Do not let that trusted persistence flag authorize client source facts.
            assert_server_metadata_unchanged(old_value, coerced_value)
        except ValueError as exc:
            return {
                "ok": False, "changed": False, "item_name": item_name,
                "fieldname": fieldname, "version_name": version_name or item_doc.version,
                "edit_mode": "server_metadata", "message": str(exc),
            }
    if (
        fieldname in REASON_REQUIRED_ITEM_FIELDS
        and not _edit_values_equal(fieldname, old_value, coerced_value)
        and not is_effectively_missing(fieldname, old_value, item_doc.as_dict() if hasattr(item_doc, "as_dict") else vars(item_doc))
        and not edit_remark
    ):
        return {
            "ok": False,
            "changed": False,
            "item_name": item_name,
            "fieldname": fieldname,
            "version_name": version_name or item_doc.version,
            "edit_mode": "reason_required",
            "message": f"字段 {fieldname} 已有有效值，修改时必须填写修改原因。",
        }
    same_value = _edit_values_equal(fieldname, old_value, coerced_value)
    if is_shipment_value:
        current_row = project_source_values(item_doc.as_dict(), source_context)
        if coerced_value == "":
            same_value = not isinstance(active_manual, dict)
        else:
            same_value = (shipment_number(old_value) == shipment_number(coerced_value)
                          and isinstance(existing_manual, dict)
                          and existing_manual.get("input_fingerprint") == shipment_input_fingerprint(current_row))
    if same_value:
        current_valuation = shipment_value(project_source_values(item_doc.as_dict(), source_context))
        return {
            "ok": True,
            "changed": False,
            "item_name": item_name,
            "fieldname": fieldname,
            "old_value": old_value,
            "value": coerced_value,
            "version_name": version_name or item_doc.version,
            "edit_mode": edit_mode,
            "valuation": current_valuation,
            "goods_value": current_valuation.get("amount_rmb") if current_valuation.get("amount_rmb") is not None else 0,
            "message": "字段值未变化，已跳过保存。",
        }

    reference_fields = fieldname in REFERENCE_ITEM_FIELDS
    version_context = {}
    try:
        version_context = _assert_current_item_version(
            item_doc.batch, item_doc.version, version_name, reference_fields=reference_fields)
    except ValueError as exc:
        return {'ok': False, 'changed': False, 'item_name': item_name, 'fieldname': fieldname,
                'version_name': version_name or item_doc.version, 'message': str(exc)}
    # 「已确认 / 已锁定 / 已回写的当前版本上补基础资料」这一档：写入本身服务端放行，但它既不能
    # 把批次标成 Dirty（那会让 cost_review_service 判 RESULT_STALE、列表显示成未确认），也不能
    # 让已存快照的输入指纹就此对不上（已确认版本不允许重算，对不上就再也清不掉）。
    reference_relaxed = reference_fields and not _version_allows_cost_edits(version_context, item_doc.version)
    valuation_result = None
    if is_shipment_value:
        from overseas_costing.services.shipment_cost_service import (
            build_legacy_shipment_valuation, build_manual_shipment_valuation,
            restore_legacy_manual_prior,
        )
        metadata = object_json(item_doc.extra_json)
        had_legacy_manual = isinstance(existing_legacy_manual, dict)
        if had_legacy_manual:
            legacy_prior = restore_legacy_manual_prior(
                project_source_values(item_doc.as_dict(), source_context), existing_legacy_manual,
            )
            if (legacy_prior is None
                    and shipment_number(item_doc.goods_value) != shipment_number(existing_legacy_manual.get("amount_rmb"))):
                legacy_prior = build_legacy_shipment_valuation(
                    project_source_values(item_doc.as_dict(), source_context)
                )
            if legacy_prior is None:
                metadata.pop("shipment_valuation", None)
            else:
                metadata["shipment_valuation"] = legacy_prior
        if coerced_value == "":
            if (not any(key in metadata for key in ("shipment_valuation", "settlement_cargo"))
                    and isinstance(existing_manual, dict)
                    and shipment_number(item_doc.goods_value) != shipment_number(existing_manual.get("amount_rmb"))):
                legacy_prior = build_legacy_shipment_valuation(
                    project_source_values(item_doc.as_dict(), source_context)
                )
                if legacy_prior is not None:
                    metadata["shipment_valuation"] = legacy_prior
            metadata.pop("manual_shipment_valuation", None)
            item_doc.goods_value = 0
        else:
            current_row = project_source_values(item_doc.as_dict(), source_context)
            if (not had_legacy_manual
                    and not any(key in metadata for key in ("shipment_valuation", "settlement_cargo"))):
                legacy_prior = build_legacy_shipment_valuation(current_row)
                if legacy_prior is not None:
                    metadata["shipment_valuation"] = legacy_prior
            metadata["manual_shipment_valuation"] = build_manual_shipment_valuation(
                current_row,
                coerced_value,
                actor=getattr(getattr(_frappe, "session", None), "user", ""),
                reason=edit_remark,
                confirmed_at=_now(),
            )
        item_doc.extra_json = _json.dumps(metadata, ensure_ascii=False, default=str)
        valuation_result = shipment_value(project_source_values(item_doc.as_dict(), source_context))
        amount = valuation_result.get("amount_rmb")
        item_doc.goods_value = _to_float(amount) if amount is not None else 0
    elif expense_physical:
        from overseas_costing.services.effective_logistics_source import resolve_source_context
        current_context = resolve_source_context(item_doc.batch,item_doc.version,lock=True)
        if current_context != source_context:
            return {'ok':False,'changed':False,'message':'当前采购支出资料已变化，请重新读取资料'}
        adopted_values = {fieldname:coerced_value, **companion_updates}
        if fieldname == 'actual_shipped_qty':
            cargo = object_json(item_doc.extra_json).get('settlement_cargo') or {}
            adopted_values['shipped_uom'] = cargo.get('unit') or ''
        try:
            overlay = physical_overlay_update(item_doc.as_dict(),source_context,adopted_values,
                evidence={'kind':'manual_override', 'actor':_frappe.session.user,
                          'remark':edit_remark,'source_context':source_context,'at':_now()},
                adopt_current_context=True)
        except ValueError as exc:
            return {'ok':False,'changed':False,'message':str(exc)}
        cargo = overlay.get('settlement_cargo')
        if isinstance(cargo, dict) and cargo:
            cargo = dict(cargo)
            if fieldname == 'actual_shipped_qty':
                cargo['quantity'] = coerced_value
            elif fieldname == 'shipped_uom':
                cargo['unit'] = coerced_value
            overlay['settlement_cargo'] = cargo
        item_doc.extra_json = _json.dumps(overlay,ensure_ascii=False,default=str)
    else:
        setattr(item_doc, fieldname, coerced_value)
        if fieldname == "supplier":
            supplier_metadata = object_json(getattr(item_doc, "extra_json", None))
            supplier_metadata["supplier_match_status"] = "MANUAL_CONFIRMED" if coerced_value else "EMPTY"
            supplier_metadata["supplier_canonical_name"] = coerced_value or ""
            item_doc.extra_json = _json.dumps(supplier_metadata, ensure_ascii=False, default=str)
    if fieldname == "actual_shipped_qty" and not expense_physical:
        shipping_uom = str(
            getattr(item_doc, "shipped_uom", "")
            or getattr(item_doc, "purchase_uom", "")
            or getattr(item_doc, "unit", "")
            or ""
        ).strip()
        if shipping_uom:
            companion_updates.update({"shipped_uom": shipping_uom, "cost_output_uom": shipping_uom})
        for companion_field, companion_value in companion_updates.items():
            setattr(item_doc, companion_field, companion_value)
    elif fieldname == "shipped_uom" and str(coerced_value or "").strip() and not expense_physical:
        companion_updates["cost_output_uom"] = str(coerced_value).strip()
        setattr(item_doc, "cost_output_uom", companion_updates["cost_output_uom"])
    if not is_shipment_value and fieldname in SHIPMENT_VALUE_INPUT_FIELDS:
        valuation_result = shipment_value(project_source_values(item_doc.as_dict(), source_context))
        amount = valuation_result.get("amount_rmb")
        item_doc.goods_value = _to_float(amount) if amount is not None else 0
    if fieldname != "manual_override_flag":
        item_doc.manual_override_flag = 1
    if edit_remark and fieldname != "manual_override_reason":
        item_doc.manual_override_reason = edit_remark
    item_doc.save(ignore_permissions=True)
    if reference_relaxed:
        # 已确认 / 锁定版本补基础资料：只推进并发版本号（乐观锁仍要前进），
        # 绝不把 status 写成 Dirty —— 那等于把一个已确认批次说成未确认。
        _frappe.db.set_value("Overseas Cost Batch", item_doc.batch, "modified", _now(), update_modified=False)
    else:
        _frappe.db.set_value("Overseas Cost Batch", item_doc.batch, "status", "Dirty", update_modified=True)
    _insert_audit_log(
        batch_doc_name=item_doc.batch,
        version_name=version_name or item_doc.version,
        action_type="EDIT",
        field_name=fieldname,
        row_no=getattr(item_doc, "row_no", None),
        old_value=old_value,
        new_value=coerced_value,
        action_remark=(
            f"已确认版本维护基础资料：{edit_remark}" if reference_relaxed and edit_remark
            else "已确认版本维护基础资料" if reference_relaxed
            else f"单字段编辑：{edit_remark}" if edit_remark
            else "单字段编辑"
        ),
    )
    if reference_relaxed and not _defer_reference_rebaseline:
        _refresh_saved_input_hash_for_reference_change(item_doc.batch, version_name or item_doc.version)
    if not _skip_commit:
        _frappe.db.commit()
    batch_modified = _frappe.db.get_value("Overseas Cost Batch", item_doc.batch, "modified")
    return {
        "ok": True,
        "changed": True,
        "item_name": item_name,
        "fieldname": fieldname,
        "old_value": old_value,
        "value": coerced_value,
        "version_name": version_name or item_doc.version,
        "manual_override_reason": edit_remark,
        "companion_updates": companion_updates,
        "edit_mode": edit_mode,
        "batch_modified": batch_modified,
        "valuation": valuation_result,
        "goods_value": getattr(item_doc, "goods_value", None),
        # 走「已确认/锁定版本补基础资料」这一档时为真：批量入口据此在整批写完后刷新一次指纹。
        "reference_relaxed": reference_relaxed,
        "message": (
            "字段已更新（该版本已确认，仅维护供应商 / 项目归属，成本结果保持不变）。"
            if reference_relaxed else "字段已更新，批次已标记为 Dirty。"
        ),
    }


def _batch_rollback_message(results: list[dict]) -> str:
    """整体回滚时的对外文案：必须带出逐行的真实原因。

    「要么全成功要么全回滚」只说明结果，不说明为什么；原因全在 ``results`` 里。
    只回一句「已整体回滚」会让用户和操作记录都拿到一句无法定位的话（哪一行、为什么
    都没有），于是只能反复点保存。这里取第一行失败做主线，其余只报数量。
    """

    failures = [row for row in results or [] if not row.get("ok")]
    first = failures[0] if failures else {}
    item_name = str(first.get("item_name") or "").strip()
    detail = str(first.get("message") or "").strip()
    cause = "：".join(part for part in (f"物料 {item_name}" if item_name else "", detail) if part)
    message = "批量字段更新存在错误，已整体回滚。"
    if cause:
        message = f"{message}原因：{cause}"
    if len(failures) > 1:
        message = f"{message}（共 {len(failures)} 行失败）"
    return message


def batch_update_items(
    batch_name: str,
    updates: str,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    try:
        loaded_updates = _load_updates_payload(updates)
    except (TypeError, ValueError, _json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "dry_run": _frappe is None,
            "batch_name": batch_name,
            "version_name": version_name,
            "changed_count": 0,
            "skipped_count": 0,
            "error_count": 1,
            "results": [],
            "message": f"批量更新参数解析失败：{exc}",
        }

    if _frappe is None:
        audit_service.build_audit_stub("BATCH_EDIT", {"batch_name": batch_name})
        results = [_preview_update_result(update, default_remark=remark or "") for update in loaded_updates]
        changed_count = sum(1 for result in results if result.get("ok") and result.get("changed"))
        error_count = sum(1 for result in results if not result.get("ok"))
        return {
            "ok": error_count == 0,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "changed_count": changed_count,
            "skipped_count": 0,
            "error_count": error_count,
            "results": results,
            "message": "当前未连接 Frappe，已返回批量编辑预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    from overseas_costing.services import edit_session_service

    edit_session_service.assert_batch_write(
        batch_doc_name,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )
    supplier_catalog = (
        load_active_suppliers()
        if any((update.get("fieldname") or update.get("field_name")) == "supplier" for update in loaded_updates)
        else None
    )

    changed_count = 0
    skipped_count = 0
    error_count = 0
    results = []
    for update in loaded_updates:
        item_name = update.get("item_name") or update.get("name")
        fieldname = update.get("fieldname") or update.get("field_name")
        value = update.get("value") if "value" in update else update.get("field_value")
        edit_remark = update.get("remark") or update.get("manual_override_reason") or remark
        if not item_name or not fieldname:
            error_count += 1
            results.append(
                {
                    "ok": False,
                    "changed": False,
                    "item_name": item_name,
                    "fieldname": fieldname,
                    "message": "批量更新行缺少 item_name/name 或 fieldname/field_name。",
                }
            )
            continue

        item_batch = _frappe.db.get_value("Overseas Cost Item", item_name, "batch")
        if item_batch != batch_doc_name:
            error_count += 1
            results.append(
                {
                    "ok": False,
                    "changed": False,
                    "item_name": item_name,
                    "fieldname": fieldname,
                    "message": f"明细 {item_name} 不属于批次 {batch_doc_name}。",
                }
            )
            continue

        result = update_item_field(
            item_name,
            fieldname,
            value,
            version_name=version_name,
            remark=edit_remark,
            _skip_edit_check=True,
            _skip_commit=True,
            _supplier_catalog=supplier_catalog,
            _defer_reference_rebaseline=True,
        )
        results.append(result)
        if not result.get("ok"):
            error_count += 1
        elif result.get("changed"):
            changed_count += 1
        else:
            skipped_count += 1

    if error_count:
        _frappe.db.rollback()
        return {
            "ok": False,
            "batch_name": batch_doc_name,
            "version_name": version_name,
            "changed_count": 0,
            "rolled_back_count": changed_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "results": results,
            "message": _batch_rollback_message(results),
        }

    if changed_count or skipped_count:
        if any(row.get("reference_relaxed") for row in results):
            # 已确认 / 锁定版本上补基础资料：整批写完再刷新一次已存结果的输入指纹，
            # 逐行做会把同一份全批哈希重算 N 遍。
            _refresh_saved_input_hash_for_reference_change(
                batch_doc_name, version_name or _resolve_version_name(batch_doc_name, version_name))
        _insert_audit_log(
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            action_type="BATCH_EDIT",
            action_remark=f"批量字段更新：成功 {changed_count}，跳过 {skipped_count}，失败 {error_count}",
        )
        _frappe.db.commit()

    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": version_name,
        "changed_count": changed_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "results": results,
        "batch_modified": _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified"),
        "message": "批量字段更新完成。",
    }


def confirm_actual_shipped_qty_from_quantity(
    batch_name: str,
    version_name: str | None = None,
    remark: str | None = None,
    preview_items: str | list[dict] | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    edit_remark = remark or "人工确认采购数量等于实际发货数量"

    if _frappe is None:
        try:
            items = _load_updates_payload(preview_items) if preview_items not in (None, "") else []
        except (TypeError, ValueError, _json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "dry_run": True,
                "batch_name": batch_name,
                "version_name": version_name,
                "changed_count": 0,
                "skipped_count": 0,
                "missing_quantity_count": 0,
                "results": [],
                "message": f"预览明细参数解析失败：{exc}",
            }
        return _build_confirm_actual_qty_preview(batch_name, version_name, items, edit_remark)

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有可更新的版本。"}

    items = _frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": resolved_version_name, "is_excluded": 0},
        fields=["name", "row_no", "material_code", "product_name", "quantity", "actual_shipped_qty"],
        order_by="row_no asc",
        limit_page_length=10000,
    )
    preview = _build_confirm_actual_qty_preview(batch_doc_name, resolved_version_name, items, edit_remark)
    updates = [
        {
            "item_name": result["item_name"],
            "fieldname": "actual_shipped_qty",
            "value": result["value"],
            "remark": edit_remark,
        }
        for result in preview["results"]
        if result.get("ok") and result.get("changed")
    ]

    if not updates:
        return {
            **preview,
            "dry_run": False,
            "message": preview.get("message") or "没有可按采购数量确认的实际发货数量。",
        }

    result = batch_update_items(
        batch_name=batch_doc_name,
        version_name=resolved_version_name,
        updates=_json_dumps(updates),
        remark=edit_remark,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )
    response = {
        **result,
        "missing_quantity_count": preview["missing_quantity_count"],
        "message": f"已按采购数量确认实际发货数量 {result.get('changed_count', 0)} 行；请重新试算后再校验结果。",
    }
    response["batch_modified"] = _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified")
    return response


def _build_confirm_actual_qty_preview(
    batch_name: str,
    version_name: str | None,
    items: list[dict],
    remark: str,
) -> dict:
    results = []
    changed_count = 0
    skipped_count = 0
    missing_quantity_count = 0
    for item in items:
        item_name = item.get("name") or item.get("item_name")
        quantity = _to_float(item.get("quantity"))
        actual_qty = _to_float(item.get("actual_shipped_qty"))
        base = {
            "ok": True,
            "item_name": item_name,
            "row_no": item.get("row_no") or item.get("excel_row_no"),
            "material_code": item.get("material_code"),
            "product_name": item.get("product_name"),
            "fieldname": "actual_shipped_qty",
            "manual_override_reason": remark,
        }
        if actual_qty > 0:
            skipped_count += 1
            results.append({**base, "changed": False, "value": actual_qty, "skip_reason": "actual_qty_exists"})
            continue
        if quantity <= 0:
            skipped_count += 1
            missing_quantity_count += 1
            results.append({**base, "changed": False, "value": actual_qty, "skip_reason": "quantity_missing"})
            continue
        changed_count += 1
        results.append({**base, "changed": True, "value": quantity, "source_fieldname": "quantity"})

    return {
        "ok": True,
        "dry_run": _frappe is None,
        "batch_name": batch_name,
        "version_name": version_name,
        "changed_count": changed_count,
        "skipped_count": skipped_count,
        "missing_quantity_count": missing_quantity_count,
        "results": results,
        "message": f"可按采购数量确认实际发货数量 {changed_count} 行，采购数量也缺失 {missing_quantity_count} 行。",
    }


def create_item(
    batch_name: str,
    item_payload: str | dict | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    try:
        payload = _load_payload(item_payload)
        assert_server_metadata_unchanged(None, payload.get("extra_json"))
    except (TypeError, ValueError, _json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "dry_run": _frappe is None,
            "batch_name": batch_name,
            "version_name": version_name,
            "message": f"新增物料参数解析失败：{exc}",
        }

    if _frappe is None:
        values = _build_new_item_values(batch_name, version_name or "", payload)
        audit_service.build_audit_stub("BATCH_EDIT", {"batch_name": batch_name, "action": "CREATE_ITEM"})
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "item": values,
            "message": "当前未连接 Frappe，已返回新增物料预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    from overseas_costing.services import edit_session_service

    edit_session_service.assert_batch_write(
        batch_doc_name,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有可新增明细的版本。"}

    try:
        _assert_current_item_version(batch_doc_name, resolved_version_name, version_name)
    except ValueError as exc:
        return {'ok': False, 'batch_name': batch_doc_name, 'message': str(exc)}

    latest = _frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": resolved_version_name},
        fields=["row_no"],
        order_by="row_no desc",
        limit_page_length=1,
    )
    next_row_no = int((latest[0].get("row_no") if latest else 0) or 0) + 1
    values = _build_new_item_values(batch_doc_name, resolved_version_name, payload, row_no=next_row_no)
    item_doc = _frappe.get_doc(values).insert(ignore_permissions=True)
    _frappe.db.set_value("Overseas Cost Batch", batch_doc_name, "status", "Dirty", update_modified=True)
    _insert_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        action_type="BATCH_EDIT",
        field_name="item",
        row_no=next_row_no,
        new_value=_json_dumps({k: v for k, v in values.items() if k != "doctype"}),
        action_remark=remark or "新增物料",
    )
    _frappe.db.commit()

    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "item_name": item_doc.name,
        "row_no": next_row_no,
        "batch_modified": _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified"),
        "message": "物料已新增，批次已标记为 Dirty。",
    }


def delete_item(
    item_name: str,
    batch_name: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    if not item_name:
        return {"ok": False, "dry_run": _frappe is None, "message": "缺少要删除的物料明细。"}

    if _frappe is None:
        audit_service.build_audit_stub("BATCH_EDIT", {"item_name": item_name, "action": "EXCLUDE_ITEM"})
        return {
            "ok": True,
            "dry_run": True,
            "item_name": item_name,
            "batch_name": batch_name,
            "version_name": version_name,
            "soft_excluded": True,
            "message": "当前未连接 Frappe，已返回软排除物料预览。",
        }

    item_doc = _frappe.get_doc("Overseas Cost Item", item_name)
    from overseas_costing.services import edit_session_service

    edit_session_service.assert_batch_write(
        item_doc.batch,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )
    if batch_name:
        batch_doc_name = _resolve_batch_name(batch_name)
        if batch_doc_name and item_doc.batch != batch_doc_name:
            return {"ok": False, "message": f"物料 {item_name} 不属于批次 {batch_doc_name}。"}

    try:
        _assert_current_item_version(item_doc.batch, item_doc.version, version_name)
    except ValueError as exc:
        return {'ok': False, 'item_name': item_name, 'message': str(exc)}

    if int(getattr(item_doc, "is_excluded", 0) or 0):
        return {"ok": True, "changed": False, "soft_excluded": True, "item_name": item_name,
                "batch_name": item_doc.batch, "version_name": version_name or item_doc.version,
                "batch_modified": _frappe.db.get_value("Overseas Cost Batch", item_doc.batch, "modified"),
                "message": "该物料已在已排除列表中。"}

    old_snapshot = {
        "name": item_doc.name,
        "row_no": getattr(item_doc, "row_no", None),
        "material_code": getattr(item_doc, "material_code", ""),
        "product_name": getattr(item_doc, "product_name", ""),
        "quantity": getattr(item_doc, "quantity", None),
        "goods_value": getattr(item_doc, "goods_value", None),
    }
    batch_doc_name = item_doc.batch
    resolved_version_name = version_name or item_doc.version
    row_no = getattr(item_doc, "row_no", None)

    item_doc.is_excluded = 1
    item_doc.excluded_at = _now()
    item_doc.excluded_by = str(getattr(getattr(_frappe, "session", None), "user", "") or "")
    item_doc.exclusion_reason = str(remark or "手工排除物料").strip()
    item_doc.save(ignore_permissions=True)
    from overseas_costing.services.material_packing_group_service import mark_member_changed
    mark_member_changed(_frappe, item_doc.version, getattr(item_doc, 'stable_line_key', ''), 'excluded')
    _frappe.db.set_value("Overseas Cost Batch", batch_doc_name, "status", "Dirty", update_modified=True)
    _insert_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        action_type="BATCH_EDIT",
        field_name="item",
        row_no=row_no,
        old_value=_json_dumps(old_snapshot),
        new_value=_json_dumps({"is_excluded": 1, "excluded_at": item_doc.excluded_at,
                               "excluded_by": item_doc.excluded_by,
                               "exclusion_reason": item_doc.exclusion_reason}),
        action_remark=remark or "软排除物料",
    )
    _frappe.db.commit()

    return {
        "ok": True,
        "item_name": item_name,
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "soft_excluded": True,
        "batch_modified": _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified"),
        "message": "物料已软排除，可在“已排除物料”中恢复。",
    }


def restore_item(
    item_name: str,
    batch_name: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    if not item_name:
        return {"ok": False, "dry_run": _frappe is None, "message": "缺少要恢复的物料明细。"}
    if _frappe is None:
        audit_service.build_audit_stub("BATCH_EDIT", {"item_name": item_name, "action": "RESTORE_ITEM"})
        return {"ok": True, "dry_run": True, "restored": True, "item_name": item_name,
                "batch_name": batch_name, "version_name": version_name,
                "message": "当前未连接 Frappe，已返回恢复物料预览。"}

    item_doc = _frappe.get_doc("Overseas Cost Item", item_name)
    from overseas_costing.services import edit_session_service
    edit_session_service.assert_batch_write(
        item_doc.batch, edit_token=edit_token, expected_modified=expected_modified,
    )
    if batch_name:
        resolved_batch = _resolve_batch_name(batch_name)
        if resolved_batch and item_doc.batch != resolved_batch:
            return {"ok": False, "message": f"物料 {item_name} 不属于批次 {resolved_batch}。"}
    try:
        _assert_current_item_version(item_doc.batch, item_doc.version, version_name)
    except ValueError as exc:
        return {"ok": False, "item_name": item_name, "message": str(exc)}
    if not int(getattr(item_doc, "is_excluded", 0) or 0):
        return {"ok": False, "item_name": item_name, "message": "该物料当前未被排除。"}

    old_value = _json_dumps({"is_excluded": 1,
                             "excluded_at": getattr(item_doc, "excluded_at", None),
                             "excluded_by": getattr(item_doc, "excluded_by", ""),
                             "exclusion_reason": getattr(item_doc, "exclusion_reason", "")})
    item_doc.is_excluded = 0
    item_doc.excluded_at = None
    item_doc.excluded_by = ""
    item_doc.exclusion_reason = ""
    item_doc.save(ignore_permissions=True)
    from overseas_costing.services.material_packing_group_service import mark_member_changed
    mark_member_changed(_frappe, item_doc.version, getattr(item_doc, 'stable_line_key', ''), 'restored')
    _frappe.db.set_value("Overseas Cost Batch", item_doc.batch, "status", "Dirty", update_modified=True)
    _insert_audit_log(
        batch_doc_name=item_doc.batch,
        version_name=version_name or item_doc.version,
        action_type="BATCH_EDIT",
        field_name="item",
        row_no=getattr(item_doc, "row_no", None),
        old_value=old_value,
        new_value=_json_dumps({"is_excluded": 0}),
        action_remark=remark or "恢复软排除物料",
    )
    _frappe.db.commit()
    return {"ok": True, "restored": True, "item_name": item_name,
            "batch_name": item_doc.batch, "version_name": version_name or item_doc.version,
            "batch_modified": _frappe.db.get_value("Overseas Cost Batch", item_doc.batch, "modified"),
            "message": "物料已恢复到当前表。"}


def delete_batch(batch_name: str, remark: str | None = None) -> dict:
    if not batch_name:
        return {"ok": False, "dry_run": _frappe is None, "message": "缺少要删除的批次。"}

    if _frappe is None:
        audit_service.build_audit_stub("BATCH_DELETE", {"batch_name": batch_name})
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "deleted_counts": {},
            "message": "当前未连接 Frappe，已返回删除批次预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}。"}

    # Comparison saves lock the same parent before persisting their source link.
    # Current reads must follow that lock so a newly committed reference cannot
    # be hidden by an older REPEATABLE READ snapshot.
    if not _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "name", for_update=True):
        return {"ok": False, "message": f"批次不存在或已被删除：{batch_name}。"}
    if _frappe.db.table_exists("Overseas Air Sea Comparison") and _frappe.db.get_value(
        "Overseas Air Sea Comparison", {"source_batch": batch_doc_name}, "name", for_update=True
    ):
        return {"ok": False, "batch_name": batch_doc_name,
            "message": "该批次仍被空运海运测算记录引用，请先处理相关测算记录后再删除批次。"}

    delete_plan = [
        ("Overseas Cost Audit Log", "audit_log_count"),
        ("Overseas Cost Fee Evidence", "fee_evidence_count"),
        ("Overseas Cost Attachment", "attachment_count"),
        ("Overseas Cost Allocation Rule", "rule_count"),
        ("Overseas Cost Item", "item_count"),
        ("Overseas Cost Version", "version_count"),
    ]
    names_by_doctype: dict[str, list[str]] = {}
    deleted_counts: dict[str, int] = {}
    for doctype, count_key in delete_plan:
        rows = _frappe.get_all(
            doctype,
            filters={"batch": batch_doc_name},
            fields=["name"],
            limit_page_length=10000,
        )
        names = [row["name"] for row in rows]
        names_by_doctype[doctype] = names
        deleted_counts[count_key] = len(names)

    for doctype, _count_key in delete_plan:
        for name in names_by_doctype.get(doctype, []):
            _frappe.db.delete(doctype, {"name": name})

    _frappe.db.delete("Overseas Cost Batch", {"name": batch_doc_name})
    deleted_counts["batch_count"] = 1
    _frappe.db.commit()

    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "deleted_counts": deleted_counts,
        "message": remark or "批次及关联数据已删除。",
    }


def recalculate_batch(
    batch_name: str,
    version_name: str | None = None,
    commit_after_recalculate: bool = True,
) -> dict:
    if _frappe is None:
        summary_snapshot = version_service.build_empty_summary_snapshot()
        audit_service.build_audit_stub("RECALCULATE", {"batch_name": batch_name})
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "summary_snapshot": summary_snapshot,
            "message": "当前未连接 Frappe，已返回重算预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有可重算版本。"}

    from overseas_costing.services.logistics_settlement.runtime import calculation_blockers
    settlement_issues = calculation_blockers(batch_doc_name, resolved_version_name, for_calculation=True, lock=True)
    if settlement_issues:
        return {"ok": False, "batch_name": batch_doc_name, "version_name": resolved_version_name,
                "message": "；".join(settlement_issues), "calculation_review": {"status": "blocked", "issues": settlement_issues}}

    batch = _frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        ["name", "source_approval_status", "extra_json"],
        as_dict=True,
    ) or {}
    items = _get_items(batch_doc_name, resolved_version_name)
    from overseas_costing.services.batch_service import _build_invalid_business_state

    invalid_business = _build_invalid_business_state(batch, items)
    if invalid_business.get("invalid"):
        return {
            "ok": False,
            "batch_name": batch_doc_name,
            "version_name": resolved_version_name,
            "invalid_business": True,
            "invalid_business_scope": invalid_business.get("scope") or "",
            "message": invalid_business.get("message") or "当前批次存在已排除审批，不能重新计算。",
        }

    from overseas_costing.services import cost_preview_service

    return cost_preview_service.calculate_comprehensive_cost(
        batch_doc_name, resolved_version_name, trusted=True,
        commit_after_calculate=commit_after_recalculate,
    )


def update_allocation_rule(batch_name: str, version_name: str, rule_payload: str) -> dict:
    if _frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "rule_payload": rule_payload,
            "message": "当前未连接 Frappe，已返回规则更新预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    version_batch = _frappe.db.get_value("Overseas Cost Version", version_name, "batch")
    if version_batch != batch_doc_name:
        raise ValueError(_("分摊规则的版本不存在或不属于当前批次。"))

    payload = _json.loads(rule_payload or "{}")
    rule_name = payload.get("rule_id") or payload.get("name")
    rule_code = payload.get("rule_code")
    values = {
        key: payload[key]
        for key in (
            "rule_code",
            "expense_category",
            "allocation_basis",
            "basis_field",
            "currency",
            "amount",
            "priority_no",
            "is_active",
            "is_enabled",
            "remark",
        )
        if key in payload
    }
    if not rule_name and rule_code:
        rule_name = _frappe.db.get_value(
            "Overseas Cost Allocation Rule",
            {"batch": batch_doc_name, "version": version_name, "rule_code": rule_code},
            "name",
        )

    previous = (_frappe.db.get_value(
        "Overseas Cost Allocation Rule", rule_name,
        ["batch", "version", "scope_value_json", "scope_type", "allocation_basis", "basis_field"], as_dict=True,
    ) or {}) if rule_name else {}
    if rule_name and not previous:
        raise ValueError(_("分摊规则不存在，请刷新后重试。"))
    if rule_name and (previous.get("batch") != batch_doc_name or previous.get("version") != version_name):
        raise ValueError(_("分摊规则不属于当前批次和版本，不能迁移已有规则。"))
    previous_scope = previous.get("scope_value_json")
    assert_server_metadata_unchanged(
        previous_scope, payload.get("scope_value_json", previous_scope), fields=("project_allocation",)
    )
    if _server_metadata_fields(previous_scope, ("project_allocation",)) and any(
        field in payload and payload[field] != expected
        for field, expected in (("scope_type", "ALL_ITEMS"), ("allocation_basis", "gross_weight"),
                                ("basis_field", "gross_weight"))
    ):
        raise ValueError(_("已确认的项目毛重规则不能变更为其他范围或依据。"))

    from overseas_costing.services import fee_service
    from overseas_costing.services.logistics_settlement.fee_policy import assert_fee_edit_allowed
    existing = fee_service._query_rules(batch_doc_name, version_name)
    current_fee = next((fee for fee in existing if fee.get('name') == rule_name), {})
    assert_fee_edit_allowed(existing, {**current_fee, **payload, 'name': rule_name})
    _assert_current_item_version(batch_doc_name, version_name)

    if rule_name:
        _frappe.db.set_value("Overseas Cost Allocation Rule", rule_name, values, update_modified=True)
    else:
        values.update({"doctype": "Overseas Cost Allocation Rule", "batch": batch_doc_name, "version": version_name})
        rule_name = _frappe.get_doc(values).insert(ignore_permissions=True).name

    _frappe.db.set_value("Overseas Cost Batch", batch_doc_name, "status", "Dirty", update_modified=True)
    _insert_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=version_name,
        action_type="BATCH_EDIT",
        field_name="allocation_rule",
        new_value=rule_payload,
        action_remark="更新分摊规则",
    )
    _frappe.db.commit()

    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": version_name,
        "rule_name": rule_name,
        "message": "分摊规则已更新，批次已标记为 Dirty。",
    }


def _lock_version_lifecycle_batch(batch_doc_name: str) -> None:
    # Saved calculation obtains these locks in the same batch -> version order.
    _frappe.db.sql(
        "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE",
        (batch_doc_name,),
    )


def create_version(batch_name: str, source_version_name: str, version_type: str) -> dict:
    if _frappe is None:
        audit_service.build_audit_stub("CREATE_VERSION", {"batch_name": batch_name, "version_type": version_type})
        return {
            "ok": True, "dry_run": True, "batch_name": batch_name,
            "source_version_name": source_version_name, "version_type": version_type,
            "message": "当前未连接 Frappe，已返回版本创建预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    try:
        _lock_version_lifecycle_batch(batch_doc_name)
        source_version = source_version_name or _resolve_version_name(batch_doc_name)
        if not source_version:
            _frappe.db.rollback()
            return {"ok": False, "message": "没有可复制的源版本。"}
        _frappe.db.sql(
            "SELECT name FROM `tabOverseas Cost Version` WHERE name=%s AND batch=%s FOR UPDATE",
            (source_version, batch_doc_name),
        )
        if _frappe.db.get_value("Overseas Cost Version", source_version, "batch") != batch_doc_name:
            _frappe.db.rollback()
            return {"ok": False, "message": "源版本不属于当前批次，无法复制。"}

        child_doctypes = {'item': 'Overseas Cost Item', 'rule': 'Overseas Cost Allocation Rule',
                          'evidence': 'Overseas Cost Fee Evidence', 'component': 'Overseas Cost Fee SKU Component'}
        for doctype in child_doctypes.values():
            _frappe.db.sql(
                f"SELECT name FROM `tab{doctype}` WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE",
                (batch_doc_name, source_version),
            )
        version_code = f"{version_type}-{_now().replace(':', '').replace('-', '').replace(' ', '-')}"
        source_doc = _frappe.get_doc("Overseas Cost Version", source_version)
        new_version = _frappe.get_doc({
            "doctype": "Overseas Cost Version", "batch": batch_doc_name,
            "version_code": version_code, "version_type": version_type,
            "status": "Active", "is_current": 0, "source_type": "Clone",
            "fx_usd_to_rmb": getattr(source_doc, "fx_usd_to_rmb", None),
            "fx_rmb_to_mxn": getattr(source_doc, "fx_rmb_to_mxn", None),
            "rule_snapshot_json": None, "summary_snapshot_json": None, "calculated_at": None,
            "extra_json": getattr(source_doc, "extra_json", None),
            "remark": f"Cloned from {source_version}",
        }).insert(ignore_permissions=True)

        from overseas_costing.services.logistics_settlement.writer import clone_version_children
        rows_by_kind = {kind: [dict(row) for row in _frappe.get_all(
            doctype, filters={'batch': batch_doc_name, 'version': source_version},
            fields=['*'], limit_page_length=0)] for kind, doctype in child_doctypes.items()}
        def create_child(kind, values):
            return _frappe.get_doc({'doctype': child_doctypes[kind], **values}).insert(ignore_permissions=True).as_dict()
        def update_child(kind, name, values):
            _frappe.db.set_value(child_doctypes[kind], name, values, update_modified=True)
        clone_version_children(rows_by_kind, new_version.name, create_child, update_child)
        _frappe.db.set_value("Overseas Cost Batch", batch_doc_name, "version_count",
            _frappe.db.count("Overseas Cost Version", {"batch": batch_doc_name}), update_modified=True)
        _insert_audit_log(
            batch_doc_name=batch_doc_name, version_name=new_version.name, action_type="CREATE_VERSION",
            action_remark=f"从 {source_version} 复制生成 {version_type} 版本，须重新试算",
        )
        _frappe.db.commit()
    except Exception:
        _frappe.db.rollback()
        raise

    return {
        "ok": True, "batch_name": batch_doc_name, "source_version_name": source_version,
        "version_name": new_version.name, "version_type": version_type, "message": "版本已创建。",
    }


def _batch_values_for_current_version(batch_doc_name: str, version: dict) -> dict:
    historical = version.get("status") in {"Confirmed", "Archived"}
    summary = _load_json_dict(version.get("summary_snapshot_json")) if historical else {}
    total_cost = _to_float(summary.get("total_cost_rmb"))
    return {
        "current_version": version["name"],
        # Active versions may have been edited since their snapshot; always
        # require a fresh calculation after switching back to one.
        "status": "Confirmed" if historical else "Dirty",
        "confirm_status": "Confirmed" if historical else "Pending",
        "is_locked": 1 if historical else 0,
        "item_count": int(summary.get("item_count") or _frappe.db.count(
            "Overseas Cost Item", {"batch": batch_doc_name, "version": version["name"]})),
        "total_goods_value": _to_float(summary.get("total_goods_value")),
        "total_gross_weight_kg": _to_float(summary.get("total_gross_weight_kg")),
        "estimated_total_cost_rmb": total_cost,
        "actual_total_cost_rmb": total_cost if version.get("version_type") in {"Actual", "Adjustment"} else 0,
        "writeback_status": "Not Started", "writeback_time": None,
        "writeback_message": "", "erp_target_doc": "",
    }


def switch_version(batch_name: str, target_version_name: str) -> dict:
    if _frappe is None:
        audit_service.build_audit_stub("SWITCH_VERSION", {"batch_name": batch_name, "target_version_name": target_version_name})
        return {
            "ok": True, "dry_run": True, "batch_name": batch_name,
            "target_version_name": target_version_name, "message": "当前未连接 Frappe，已返回版本切换预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    try:
        _lock_version_lifecycle_batch(batch_doc_name)
        _frappe.db.sql(
            "SELECT name FROM `tabOverseas Cost Version` WHERE batch=%s ORDER BY name FOR UPDATE",
            (batch_doc_name,),
        )
        versions = _frappe.get_all(
            "Overseas Cost Version", filters={"batch": batch_doc_name},
            fields=["name", "status", "version_type", "summary_snapshot_json"], limit_page_length=1000,
        )
        target = next((row for row in versions if row["name"] == target_version_name), None)
        if target is None:
            _frappe.db.rollback()
            return {"ok": False, "batch_name": batch_doc_name, "target_version_name": target_version_name,
                    "message": "目标版本不属于当前批次，无法切换。"}
        if _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "current_version") == target_version_name:
            _frappe.db.commit()
            return {"ok": True, "batch_name": batch_doc_name, "target_version_name": target_version_name,
                    "unchanged": True, "message": "所选版本已是当前版本。"}

        batch_values = _batch_values_for_current_version(batch_doc_name, target)
        old_values = _frappe.db.get_value("Overseas Cost Batch", batch_doc_name, list(batch_values), as_dict=True)
        for row in versions:
            _frappe.db.set_value("Overseas Cost Version", row["name"], "is_current",
                1 if row["name"] == target_version_name else 0, update_modified=False)
        _frappe.db.set_value("Overseas Cost Batch", batch_doc_name, batch_values, update_modified=True)
        _insert_audit_log(
            batch_doc_name=batch_doc_name, version_name=target_version_name, action_type="SWITCH_VERSION",
            field_name="current_version", old_value=_json_dumps(old_values), new_value=_json_dumps(batch_values),
            action_remark=f"切换当前版本为 {target_version_name}",
        )
        _frappe.db.commit()
    except Exception:
        _frappe.db.rollback()
        raise

    return {"ok": True, "batch_name": batch_doc_name, "target_version_name": target_version_name,
            "message": "当前版本已切换。"}
