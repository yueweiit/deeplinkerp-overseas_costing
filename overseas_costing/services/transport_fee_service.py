"""Pure transport fee identities shared by OA ingestion and fee consumers."""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from decimal import Decimal, InvalidOperation


PRIMARY_FREIGHT = {
    "SEA": ("international_sea_freight", "国际海运费", "volume"),
    "AIR": ("international_air_freight", "国际空运费", "chargeable_weight"),
    "EXPRESS": ("international_express_fee", "国际快递费", "chargeable_weight"),
}
HISTORICAL_SURCHARGES = (
    ("sea_port_forwarder_surcharge", "港杂/货代附加费", "volume", "expense_invoice"),
    ("air_forwarder_surcharge", "空运附加费", "chargeable_weight", "expense_invoice"),
    ("express_surcharge", "快递附加费", "chargeable_weight", "expense_invoice"),
)


def resolve_transport_mode(value: object) -> str:
    """Resolve supported transport families, leaving unknown values unspecified."""
    text = str(value or "").strip().upper()
    if text in PRIMARY_FREIGHT:
        return text
    for mode in ("AIR", "SEA"):
        if text in {f"{mode}_STANDARD", f"{mode}_DDP"}:
            return mode
    return ""


def primary_freight_definition(mode: object) -> dict:
    resolved = resolve_transport_mode(mode)
    if not resolved:
        raise ValueError(_("运输方式未明确，无法确定国际物流费用类型。"))
    key, label, basis = PRIMARY_FREIGHT[resolved]
    return {"logical_fee_key": key, "expense_category": label, "allocation_basis": basis}


def fee_is_active(rule: dict) -> bool:
    return all(rule.get(field) not in (0, False, "0") for field in ("is_enabled", "is_active"))


def legacy_oa_freight_updates(rule: dict, mode: object) -> dict:
    """Suggest safe legacy OA identity/status repairs, never source money edits.

    Explicit manual revisions and declared amount states are authoritative. Old
    zero values cannot distinguish absent Frappe Currency values from real zero.
    """
    if rule.get("rule_code") != "oa_logistics_freight" or not fee_is_active(rule):
        return {}
    if not resolve_transport_mode(mode):
        return {}
    definition = primary_freight_definition(mode)
    explicit = str(rule.get("logical_fee_key") or "").strip()
    if explicit and explicit != definition["logical_fee_key"]:
        return {}
    revisions = [str(rule.get(field) or "") for field in ("amount_revision", "scope_revision")]
    if any(revision and not revision.startswith("oa:") for revision in revisions):
        return {}
    updates = {**definition, "basis_field": definition["allocation_basis"]}
    state = str(rule.get("amount_status") or "").strip().upper()
    if state in {"", "MISSING"}:
        try:
            amount = Decimal(str(rule.get("amount")))
        except (InvalidOperation, ValueError, TypeError):
            amount = None
        if amount is not None and amount.is_finite() and amount > 0:
            updates["amount_status"] = "ESTIMATED"
    return {field: value for field, value in updates.items() if rule.get(field) != value}


def mark_duplicate_fees(fees: list[dict]) -> list[dict]:
    """Retain every active collision; callers must block all colliding inputs."""
    rows = [dict(row) for row in fees or []]
    by_key = {}
    for index, row in enumerate(rows):
        key = str(row.get("logical_fee_key") or "")
        if key and fee_is_active(row) and not row.get("virtual"):
            by_key.setdefault(key, []).append(index)
    for indexes in by_key.values():
        if len(indexes) < 2:
            continue
        names = [str(rows[index].get("name") or rows[index].get("rule_code") or f"记录{index + 1}") for index in indexes]
        for index in indexes:
            rows[index].update(duplicate_rule_names=names, requires_review=True,
                               conflict_code="DUPLICATE_LOGICAL_FEE")
    return rows


def assert_no_duplicate_fees(fees: list[dict]) -> None:
    for row in mark_duplicate_fees(fees):
        if row.get("conflict_code") == "DUPLICATE_LOGICAL_FEE" or row.get("duplicate_rule_names"):
            names = "、".join(str(name) for name in row.get("duplicate_rule_names", []))
            raise ValueError(_("费用重复（{0}）：{1}。请先核对并停用重复记录。").format(row.get('expense_category') or row.get('logical_fee_key'), names))
