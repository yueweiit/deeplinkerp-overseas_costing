"""中文用途：独立比较整票按重量与按体积报价，不触碰正式成本、分摊或 ERP 状态。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

try:
    import frappe
except Exception:  # pragma: no cover
    frappe = None


REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
CURRENCY_PRECISION = {"JPY": 0, "KRW": 0}


def ceil_to_increment(quantity: Decimal, increment: Decimal | None) -> Decimal:
    if increment in (None, Decimal("0")):
        return quantity
    return (quantity / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def compare_freight(
    *,
    gross_weight_kg: Any,
    volume_m3: Any,
    currency: str,
    scope_confirmed: Any,
    weight: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    gross = _number(gross_weight_kg, "gross_weight_kg", required=True, positive=True)
    cubic = _number(volume_m3, "volume_m3", required=True, positive=True)
    normalized_currency = str(currency or "").strip().upper()
    if not CURRENCY_PATTERN.fullmatch(normalized_currency):
        raise ValueError(_("报价币种必须是三个字母的币种代码。"))
    for quote in (weight, volume):
        quote_currency = str(quote.get("currency") or normalized_currency).strip().upper()
        if quote_currency != normalized_currency:
            raise ValueError(_("重量与体积报价必须使用同一币种。"))

    weight_result = _calculate_basis(gross, weight, "weight")
    volume_result = _calculate_basis(cubic, volume, "volume")
    weight_total = Decimal(weight_result["total"])
    volume_total = Decimal(volume_result["total"])
    difference = abs(weight_total - volume_total)
    higher = max(weight_total, volume_total)
    savings = difference / higher * Decimal("100") if higher else Decimal("0")
    confirmed = _strict_bool(scope_confirmed)
    recommended = None
    if confirmed:
        if weight_total < volume_total:
            recommended = "weight"
        elif volume_total < weight_total:
            recommended = "volume"
        else:
            recommended = "equal"

    precision = CURRENCY_PRECISION.get(normalized_currency, 2)
    return {
        "currency": normalized_currency,
        "scope_confirmed": confirmed,
        "scope_status": "confirmed" if confirmed else "unconfirmed",
        "gross_weight_kg": _text(gross),
        "volume_m3": _text(cubic),
        "weight": weight_result,
        "volume": volume_result,
        "recommended_basis": recommended,
        "difference_amount": _text(difference),
        "savings_percent": _text(savings),
        "display": {
            "weight_total": _money(weight_total, precision),
            "volume_total": _money(volume_total, precision),
            "difference_amount": _money(difference, precision),
            "savings_percent": f"{savings.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}%",
            "currency": normalized_currency,
        },
    }


def _calculate_basis(actual: Decimal, quote: dict[str, Any], label: str) -> dict[str, str]:
    unit_price = _number(quote.get("unit_price"), f"{label}.unit_price", required=True)
    minimum = _number(quote.get("min_quantity"), f"{label}.min_quantity") or Decimal("0")
    increment = _number(quote.get("rounding_increment"), f"{label}.rounding_increment")
    minimum_base = _number(quote.get("min_base_freight"), f"{label}.min_base_freight") or Decimal("0")
    surcharge = _number(quote.get("surcharge"), f"{label}.surcharge") or Decimal("0")
    quantity_before_rounding = max(actual, minimum)
    chargeable = ceil_to_increment(quantity_before_rounding, increment)
    calculated_base = chargeable * unit_price
    base = max(calculated_base, minimum_base)
    total = base + surcharge
    return {
        "actual_quantity": _text(actual),
        "minimum_quantity": _text(minimum),
        "quantity_before_rounding": _text(quantity_before_rounding),
        "rounding_increment": _text(increment) if increment is not None else "0",
        "chargeable_quantity": _text(chargeable),
        "unit_price": _text(unit_price),
        "calculated_base_freight": _text(calculated_base),
        "minimum_base_freight": _text(minimum_base),
        "base_freight": _text(base),
        "surcharge": _text(surcharge),
        "total": _text(total),
    }


def save_freight_comparison(
    batch_name: str,
    snapshot_revision: str,
    request_id: str,
    quote: dict[str, Any],
    *,
    repository: Any | None = None,
) -> dict[str, Any]:
    request_id = str(request_id or "").strip().lower()
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError(_("运费试算请求 ID 必须是 64 位小写 SHA-256。"))
    repo = repository or FrappeFreightComparisonRepository()
    repo.lock_batch(str(batch_name))
    try:
        existing = repo.get_by_request_id(str(batch_name), request_id)
        if existing:
            current_name = repo.get_current_snapshot_name(str(batch_name))
            repo.rollback()
            return {
                "ok": True,
                "idempotent": True,
                "comparison": public_comparison(existing, current_snapshot_name=current_name),
            }
        snapshot = repo.get_snapshot(str(batch_name), str(snapshot_revision))
        if not snapshot or _record(snapshot, "status") not in {"Confirmed", "Superseded"}:
            repo.rollback()
            return {"ok": False, "message": "未找到对应的已确认装箱快照，请重新打开试算。"}
        calculation = compare_freight(
            gross_weight_kg=_record(snapshot, "total_gross_weight_kg"),
            volume_m3=_record(snapshot, "total_volume_m3"),
            currency=str(quote.get("currency") or ""),
            scope_confirmed=_strict_bool(quote.get("scope_confirmed")),
            weight=quote.get("weight") if isinstance(quote.get("weight"), dict) else {},
            volume=quote.get("volume") if isinstance(quote.get("volume"), dict) else {},
        )
        values = {
            "batch": str(batch_name),
            "packing_snapshot": _record(snapshot, "name"),
            "request_id": request_id,
            "snapshot_revision": str(snapshot_revision),
            "currency": calculation["currency"],
            "scope_confirmed": 1 if calculation["scope_confirmed"] else 0,
            "gross_weight_kg": calculation["gross_weight_kg"],
            "volume_m3": calculation["volume_m3"],
            "weight_unit_price": calculation["weight"]["unit_price"],
            "weight_min_quantity": calculation["weight"]["minimum_quantity"],
            "weight_rounding_increment": calculation["weight"]["rounding_increment"],
            "weight_min_base_freight": calculation["weight"]["minimum_base_freight"],
            "weight_surcharge": calculation["weight"]["surcharge"],
            "weight_total": calculation["weight"]["total"],
            "volume_unit_price": calculation["volume"]["unit_price"],
            "volume_min_quantity": calculation["volume"]["minimum_quantity"],
            "volume_rounding_increment": calculation["volume"]["rounding_increment"],
            "volume_min_base_freight": calculation["volume"]["minimum_base_freight"],
            "volume_surcharge": calculation["volume"]["surcharge"],
            "volume_total": calculation["volume"]["total"],
            "recommended_basis": calculation["recommended_basis"] or "unconfirmed",
            "difference_amount": calculation["difference_amount"],
            "savings_percent": calculation["savings_percent"],
            "input_json": _json(quote),
            "calculation_json": _json(calculation),
            "quote_remark": str(quote.get("quote_remark") or "")[:500],
        }
        comparison = repo.insert_comparison(values)
        repo.write_audit(
            {
                "batch": str(batch_name),
                "action_type": "FREIGHT_COMPARE",
                "field_name": "freight_comparison",
                "old_value": "",
                "new_value": _record(comparison, "name"),
                "operator_name": _session_user(),
                "action_remark": "保存整票按重量/体积独立运费试算",
            }
        )
        repo.commit()
        current_name = repo.get_current_snapshot_name(str(batch_name))
        return {
            "ok": True,
            "idempotent": False,
            "comparison": public_comparison(comparison, current_snapshot_name=current_name),
        }
    except Exception:
        repo.rollback()
        raise


def public_comparison(comparison: Any, *, current_snapshot_name: str | None) -> dict[str, Any]:
    allowed = (
        "name",
        "batch",
        "packing_snapshot",
        "request_id",
        "snapshot_revision",
        "currency",
        "scope_confirmed",
        "gross_weight_kg",
        "volume_m3",
        "weight_unit_price",
        "weight_min_quantity",
        "weight_rounding_increment",
        "weight_min_base_freight",
        "weight_surcharge",
        "weight_total",
        "volume_unit_price",
        "volume_min_quantity",
        "volume_rounding_increment",
        "volume_min_base_freight",
        "volume_surcharge",
        "volume_total",
        "recommended_basis",
        "difference_amount",
        "savings_percent",
        "quote_remark",
        "input_json",
        "calculation_json",
        "creation",
    )
    result = {field: _record(comparison, field) for field in allowed if _record(comparison, field) is not None}
    result["based_on_superseded_snapshot"] = bool(
        current_snapshot_name and str(result.get("packing_snapshot") or "") != str(current_snapshot_name)
    )
    return result


def preview_freight_comparison(
    batch_name: str,
    snapshot_revision: str,
    quote: dict[str, Any],
    *,
    repository: Any | None = None,
) -> dict[str, Any]:
    repo = repository or FrappeFreightComparisonRepository()
    snapshot = repo.get_snapshot(str(batch_name), str(snapshot_revision))
    if not snapshot or _record(snapshot, "status") not in {"Confirmed", "Superseded"}:
        return {"ok": False, "message": "未找到对应的已确认装箱快照。"}
    result = compare_freight(
        gross_weight_kg=_record(snapshot, "total_gross_weight_kg"),
        volume_m3=_record(snapshot, "total_volume_m3"),
        currency=str(quote.get("currency") or ""),
        scope_confirmed=_strict_bool(quote.get("scope_confirmed")),
        weight=quote.get("weight") if isinstance(quote.get("weight"), dict) else {},
        volume=quote.get("volume") if isinstance(quote.get("volume"), dict) else {},
    )
    return {"ok": True, "packing_snapshot": _record(snapshot, "name"), "calculation": result}


def list_freight_comparisons(batch_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
    if frappe is None:
        raise RuntimeError("当前环境未连接 Frappe。")
    current_name = frappe.db.get_value(
        "Overseas Packing Snapshot",
        {"batch": str(batch_name), "status": "Confirmed", "is_current": 1},
        "name",
    )
    rows = frappe.get_list(
        "Overseas Freight Comparison",
        filters={"batch": str(batch_name)},
        fields=[
            "name",
            "batch",
            "packing_snapshot",
            "request_id",
            "snapshot_revision",
            "currency",
            "scope_confirmed",
            "gross_weight_kg",
            "volume_m3",
            "weight_unit_price",
            "weight_min_quantity",
            "weight_rounding_increment",
            "weight_min_base_freight",
            "weight_surcharge",
            "weight_total",
            "volume_unit_price",
            "volume_min_quantity",
            "volume_rounding_increment",
            "volume_min_base_freight",
            "volume_surcharge",
            "volume_total",
            "recommended_basis",
            "difference_amount",
            "savings_percent",
            "quote_remark",
            "input_json",
            "calculation_json",
            "creation",
        ],
        order_by="creation desc",
        limit_page_length=max(1, min(int(limit), 200)),
    )
    return [public_comparison(row, current_snapshot_name=current_name) for row in rows]


def _number(
    value: Any,
    label: str,
    *,
    required: bool = False,
    positive: bool = False,
) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise ValueError(_("{0} 不能为空。").format(label))
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(_("{0} 必须是有效数字。").format(label)) from error
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ValueError(_("{0} 必须是有效的非负数字。").format(label))
    return result


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (1, "1"):
        return True
    if value in (0, None, "", "0"):
        return False
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "on"}:
        return True
    if normalized in {"false", "no", "off"}:
        return False
    raise ValueError(_("报价范围确认值无效。"))


def _text(value: Decimal) -> str:
    return format(value, "f")


def _money(value: Decimal, precision: int) -> str:
    quantum = Decimal(1).scaleb(-precision)
    return f"{value.quantize(quantum, rounding=ROUND_HALF_UP):,.{precision}f}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _record(record: Any, fieldname: str) -> Any:
    return record.get(fieldname) if isinstance(record, dict) else getattr(record, fieldname, None)


def _session_user() -> str:
    return str(getattr(getattr(frappe, "session", None), "user", "") or "") if frappe is not None else ""


class FrappeFreightComparisonRepository:
    def __init__(self) -> None:
        if frappe is None:
            raise RuntimeError("当前环境未连接 Frappe。")

    def lock_batch(self, batch_name: str) -> None:
        rows = frappe.db.sql(
            "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE",
            (batch_name,),
        )
        if not rows:
            raise ValueError(_("未找到当前批次。"))

    def get_by_request_id(self, batch_name: str, request_id: str) -> Any:
        name = frappe.db.get_value(
            "Overseas Freight Comparison", {"batch": batch_name, "request_id": request_id}, "name"
        )
        return frappe.get_doc("Overseas Freight Comparison", name) if name else None

    def get_snapshot(self, batch_name: str, revision: str) -> Any:
        name = frappe.db.get_value(
            "Overseas Packing Snapshot",
            {"batch": batch_name, "idempotency_key": revision},
            "name",
        )
        return frappe.get_doc("Overseas Packing Snapshot", name) if name else None

    def get_current_snapshot_name(self, batch_name: str) -> str | None:
        return frappe.db.get_value(
            "Overseas Packing Snapshot",
            {"batch": batch_name, "status": "Confirmed", "is_current": 1},
            "name",
        )

    def insert_comparison(self, values: dict[str, Any]) -> Any:
        return frappe.get_doc({"doctype": "Overseas Freight Comparison", **values}).insert(ignore_permissions=True)

    def write_audit(self, values: dict[str, Any]) -> None:
        frappe.get_doc({"doctype": "Overseas Cost Audit Log", **values}).insert(ignore_permissions=True)

    def commit(self) -> None:
        frappe.db.commit()

    def rollback(self) -> None:
        frappe.db.rollback()
