"""Combine validated source purchase facts without averaging purchase prices."""

from copy import deepcopy
from decimal import Decimal

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from .purchase_value_evidence import (
    META_KEY, bind_purchase_value, identity_matches, number, retire_purchase_value,
    same_uom, shipment_valuation, text,
)
from .shipment_cost_service import (
    legacy_manual_shipment_valuation, object_json, shipment_input, shipment_value,
)


def aggregate_cargo_valuation(members: list[dict], aggregate_row: dict, *, fx_rates=None) -> dict:
    """Bind the selected member facts to the aggregate's final identity.

    Members are original source rows/snapshots, not an already modified anchor.
    System-excluded DEFAULT_PURCHASE rows use their fact's source quantity.
    Fact snapshots own their FX conversion; fx_rates never reprices them.
    No input, purchase price, or database record is changed.
    """
    if not members:
        return _blocked("CARGO_VALUE_MEMBERS_REQUIRED")
    metadata = deepcopy(object_json(aggregate_row.get("extra_json")))
    if _manual(metadata):
        return _blocked("CARGO_VALUE_MANUAL_PROTECTED")
    inputs = shipment_input(aggregate_row)
    aggregate_quantity = number(inputs.get("quantity"))
    if aggregate_quantity is None or aggregate_quantity <= 0:
        return _blocked("CARGO_VALUE_QUANTITY_REQUIRED")

    components, refs, seen = [], [], set()
    total_quantity, total_amount = Decimal(0), Decimal(0)
    for member in members:
        meta = object_json(member.get("extra_json"))
        fact = meta.get(META_KEY)
        if _manual(meta):
            return _blocked("CARGO_VALUE_MANUAL_PROTECTED")
        if not isinstance(fact, dict) or not fact.get("source_refs"):
            return _blocked("CARGO_VALUE_FACT_REQUIRED")
        row = deepcopy(member)
        if (row.get("is_excluded") and row.get("excluded_by") == "system"
                and row.get("actual_shipped_qty_mode") == "DEFAULT_PURCHASE"):
            row.update(quantity=fact.get("quantity"), unit=fact.get("uom"))
        value = shipment_value(row)
        quantity = number(value.get("quantity"))
        amount = number(value.get("amount_rmb"))
        if value.get("error") or amount is None or amount < 0:
            return _blocked(value.get("error") or "CARGO_VALUE_AMOUNT_REQUIRED")
        if quantity is None or quantity <= 0:
            return _blocked("CARGO_VALUE_QUANTITY_REQUIRED")
        if not identity_matches(aggregate_row, member, check_uom=False):
            return _blocked("PURCHASE_VALUE_IDENTITY_MISMATCH")
        if not same_uom(inputs.get("uom"), value.get("uom")):
            return _blocked("PURCHASE_VALUE_UOM_MISMATCH")
        locations = {_ref_identity(ref) for ref in fact["source_refs"] if _ref_identity(ref)}
        if not locations or locations & seen:
            return _blocked("CARGO_VALUE_SOURCE_OVERLAP")
        seen.update(locations)
        refs.extend(deepcopy(fact["source_refs"]))
        components.append({"fact": deepcopy(fact), "quantity": text(quantity),
                           "amount_rmb": text(amount)})
        total_quantity += quantity
        total_amount += amount
    if total_quantity != aggregate_quantity:
        return _blocked("CARGO_VALUE_QUANTITY_MISMATCH")

    fact = deepcopy(components[0]["fact"]) if len(components) == 1 else {
        "material_code": aggregate_row.get("material_code"),
        "spec_model": aggregate_row.get("spec_model"),
        "quantity": text(total_quantity), "uom": inputs["uom"],
        "amount_rmb": text(total_amount), "original_amount": text(total_amount),
        "currency": "RMB", "rate_to_rmb": "1", "explicit_zero": total_amount == 0,
        "source_type": "logistics_cargo_group", "source_refs": refs,
        "components": components,
    }
    bound = bind_purchase_value(aggregate_row, fact)
    if bound is None:
        return _blocked("PURCHASE_VALUE_IDENTITY_MISMATCH")
    valuation = shipment_valuation(aggregate_row, bound)
    if valuation.get("error") or number(valuation.get("amount_rmb")) != total_amount:
        return _blocked(valuation.get("error") or "CARGO_VALUE_AMOUNT_MISMATCH")
    if metadata.get(META_KEY) != bound:
        retire_purchase_value(metadata)
        metadata[META_KEY] = bound
    effective = shipment_value({**aggregate_row, "extra_json": metadata})
    if effective.get("error") or number(effective.get("amount_rmb")) != total_amount:
        return _blocked(effective.get("error") or "CARGO_VALUE_AMOUNT_MISMATCH")
    return {"ok": True, "metadata": metadata, "goods_value": effective["amount_rmb"],
            "valuation": effective, "fact": bound}


def _manual(metadata):
    return bool(metadata.get("manual_shipment_valuation") or legacy_manual_shipment_valuation(metadata))


def _ref_identity(ref):
    source = ref.get("source_id") or ref.get("document_id")
    row = ref.get("source_row_id") or ref.get("row")
    if not source or not ref.get("row"):
        return None
    return str(source), str(ref.get("sheet") or ref.get("field") or ""), str(row)


def _blocked(reason):
    return {"ok": False, "reason": reason, "message": _("归并货值缺少一致、有效的来源依据，请保留原行核对。")}
