"""Deterministic OA fee proposals and missing-row repair without upstream reads."""

from decimal import Decimal

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from overseas_costing.scripts import import_oa_logistics as importer
from overseas_costing.services import effective_logistics_source
from overseas_costing.services.logistics_settlement.model import digest


def backfill_missing_oa_fees(batch_name: str, version_name: str) -> dict:
    """Fill absent fee rows from the locked current logistics source.

    The caller owns the transaction. Existing rows, including zero, missing,
    manual and retired decisions, are never changed. No calculation or commit
    is performed here.
    """
    bundle = effective_logistics_source.current_source_bundle(batch_name, version_name, lock=True)
    context = (bundle or {}).get("context") or {}
    source = (bundle or {}).get("source") or {}
    if (not bundle or bundle.get("binding") or context.get("root_kind") != "logistics"
            or not context.get("available") or context.get("invalid")
            or (context.get("freight") or {}).get("selected")
            or not source.get("snapshot")
            or context.get("root_source_id") != source.get("id")
            or context.get("source_snapshot") != source.get("snapshot")):
        return {"ok": True, "action": "skipped", "created_count": 0, "updated_count": 0,
                "reason": _("当前费用或装箱来源已采用、失效或不可核对，保留现有费用。")}

    return importer._sync_oa_logistics_allocation_rules(
        batch_name=batch_name,
        version_name=version_name,
        approval_item={"form_fields": source.get("fields") or {},
                       "transport_mode": bundle["batch"].get("transport_mode")},
        only_missing=True,
        include_zero=True,
    )


def build_explicit_approval_fee_proposals(source: dict, *, transport_mode: str) -> list[dict]:
    """Keep explicit amounts, including zero, separate from absent fee fields."""
    if source.get("approval_role") != "international_logistics":
        return []
    descriptors = importer.extract_logistics_fees_from_approval(
        {"form_fields": source.get("form_fields") or {}, "transport_mode": transport_mode},
        include_zero=True,
    )
    proposals = []
    for fee in descriptors:
        key = fee["logical_fee_key"]
        proposals.append({
            "proposal_id": "approval-explicit-fee:" + digest(source.get("source_id"), key),
            "proposal_type": "fee_update",
            "result_origin": "SYSTEM",
            "_explicit_approval_fee": True,
            "confidence": 1.0,
            "default_selected": True,
            "conflict_group": "fee:" + key,
            "reason": _("钉钉国际物流审批费用字段由系统直接读取，待补最终凭证。"),
            "source_refs": [{"source": "approval_form", "file": source.get("source_label") or "钉钉审批正文",
                             "field": fee["source_field"]}],
            "payload": {
                "logical_fee_key": key,
                "expense_category": fee["expense_category"],
                "amount_status": "ESTIMATED",
                "amount": format(Decimal(str(fee["amount"])).normalize(), "f"),
                "currency": fee["currency"],
                "scope_type": "ALL_ITEMS",
                "allocation_basis": fee["allocation_basis"],
                "remark": _("来自钉钉审批正文明确费用，待补凭证。"),
            },
        })
    return proposals
