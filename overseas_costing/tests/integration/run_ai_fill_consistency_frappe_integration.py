"""Exercise AI material preview, confirmation and reread on isolated MariaDB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import os
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import frappe

from overseas_costing.api import calculate, materials
from overseas_costing.services import edit_session_service
from overseas_costing.services import material_ai_fill_service as ai
from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services import material_ai_selection_service as review
from overseas_costing.services.logistics_settlement.ledger import FrappeLedger
from overseas_costing.services.logistics_settlement.model import dumps
from overseas_costing.services.material_input_service import present_material_row
from overseas_costing.services.source_review_extract_service import build_system_approval_proposals
from overseas_costing.tests.integration.run_settlement_frappe_integration import connect, report


class FixtureRepository(ai.FrappeMaterialAIFillRepository):
    """Replace external source resolution only; the rest uses real Frappe."""

    def __init__(self, source):
        self.source = source

    def list_sources(self, batch_name, version_name):
        return [deepcopy(self.source)]

    def capture_row_dependencies(self, *args, **kwargs):
        return []


def make_preview(tag, amount):
    ledger = FrappeLedger()
    batch = ledger.create("batch", {
        "batch_no": tag, "status": "Draft", "transport_mode": "EXPRESS", "confirm_status": "Pending",
    })["name"]
    version = ledger.create("version", {
        "batch": batch, "version_code": "LOCAL-INITIAL", "status": "Active", "is_current": 1,
        "fx_usd_to_rmb": 7, "fx_rmb_to_mxn": 2.5,
    })["name"]
    ledger.put("batch", batch, {"current_version": version})
    cargo = []
    for idx in (1, 2):
        code = f"{tag}-{idx}"
        cargo.append({
            "物料编码 Código de material": code, "物料名称": "本地模具",
            "规格型号": f"LOCAL-SPEC-{idx}", "数量Cantidad": "1", "单位Unidad": "套",
            "货值Valor de mercancía": str(amount), "币种Moneda": "人民币RMB",
        })
        ledger.create("item", {
            "batch": batch, "version": version, "row_no": idx, "stable_line_key": code,
            "material_code": code, "unit": "套", "shipped_uom": "套",
            "quantity": 2, "actual_shipped_qty": 0, "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
            "goods_value": float(amount),
            "extra_json": dumps({"adopted_purchase_value": {
                "material_code": code, "spec_model": f"LOCAL-SPEC-{idx}", "amount_rmb": str(amount),
                "quantity": "1", "uom": "套", "currency": "RMB",
                "source_refs": [{"source_id": tag, "document_id": "LOCAL-DOC", "row": idx}],
            }}),
        })
    source = {
        "source_id": tag, "source_kind": "approval_form", "available": True, "source_hash": tag,
        "approval_role": "international_logistics", "approval_no": tag,
        "form_fields": {"货物信息Bienes": cargo},
    }
    repo = FixtureRepository(source)
    items = repo.get_items(batch, version)
    before = ledger.rows("item", batch=batch, version=version)
    assert all(present_material_row(r)["shipment_valuation"]["error"] ==
               "PURCHASE_VALUE_IDENTITY_MISMATCH" for r in items)
    context = repo.get_context(batch, version)
    document = {"document_id": "LOCAL-DOC", "form_fields": source["form_fields"], "source_ref": source}
    proposals = build_system_approval_proposals(items, source)
    for proposal in proposals:
        for ref in proposal["source_refs"]:
            ref["document_id"] = "LOCAL-DOC"
    normalized = ai.normalize_source_review_proposals(
        proposals, items, [document], trusted_system_proposal_ids={p["proposal_id"] for p in proposals},
    )
    run = repo.create_run({
        "batch": batch, "version": version, "status": "READY", "proposal_version": 1,
        "source_manifest_json": "[]", "clarification_text": "",
        "input_fingerprint": ai._source_review_fingerprint(batch, version, items, [source], "", context=context),
        "candidates_json": dumps(normalized),
        "draft_json": dumps({
            "processing_version": ai.SOURCE_REVIEW_PROCESSING_VERSION, "row_review_policy": rows.POLICY,
            "material_input_fingerprint": review.material_fingerprint(items, [source], context),
        }),
    })
    catalog = review.review_catalog(repo, batch, run)
    choices = {
        f"{c['item_name']}:{c['fieldname']}": c["candidate_id"] for c in catalog["field_candidates"]
        if c["can_apply"] and c["fieldname"] in {"goods_value", "actual_shipped_qty", "shipped_uom"}
    }
    assert len([key for key in choices if key.endswith(":goods_value")]) == 2, catalog["field_candidates"]
    preview = review.prepare(batch, run.name, [], [], "update_selected", version,
                             field_choices=choices, repository=repo)["preview"]
    assert preview["can_apply"], preview.get("unresolved")
    amounts = [r["shipment_value_rmb"] for r in preview["rows"]]
    assert all(amount is not None for amount in amounts), amounts
    assert [Decimal(x) for x in amounts] == [amount] * 2, amounts
    assert ledger.rows("item", batch=batch, version=version) == before
    lease = edit_session_service.acquire_edit_session(batch)
    assert lease["ok"]
    assert preview["batch_modified"] == str(lease["modified"])
    # Reproduce an old detail tab sending yesterday's revision alongside a fresh preview.
    args = (batch, run.name, preview["id"], preview["revision"], lease["edit_token"], "2000-01-01 00:00:00")
    frappe.db.commit()
    return args, source, version, before


def concurrent_confirm(args, source):
    barrier = Barrier(2, timeout=20)

    def worker():
        connect()
        try:
            barrier.wait()
            result = materials.confirm_source_ai_selection(*args)
            frappe.db.commit()
            return result
        finally:
            frappe.db.rollback()
            frappe.destroy()

    confirm = review.confirm

    def confirm_with_fixture(*request):
        return confirm(*request, repository=FixtureRepository(source))

    # Retain the API's real permission checks, locks and fresh-transaction retry.
    with patch.object(review, "confirm", confirm_with_fixture):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(worker), pool.submit(worker)]
            return [result.result(timeout=40) for result in results]


def verify_oa_document_guard(tag):
    data = {"doctype": "Overseas Cost Batch", "batch_no": tag, "source_type": "oa_logistics",
            "source_instance_id": tag, "source_approval_status": "COMPLETED"}
    try:
        frappe.get_doc(data).insert()
    except frappe.ValidationError as exc:
        assert str(exc) == frappe._("钉钉国际物流来源只能通过审批导入创建。")
    else:
        raise AssertionError("Normal document insertion accepted an OA source")
    doc = frappe.get_doc(data).insert(ignore_permissions=True)
    doc = frappe.get_doc(doc.doctype, doc.name)
    doc.source_instance_id = f"{tag}-MODIFIED"
    try:
        doc.save()
    except frappe.ValidationError as exc:
        assert str(exc) == frappe._("钉钉国际物流审批来源不可手工修改，请通过审批同步更新。")
    else:
        raise AssertionError("Normal document save changed OA source provenance")
    frappe.db.rollback()
    report("native_oa_source_controller", ok=True)


def main():
    connect()
    try:
        tag = "LOCAL-AI-CONSISTENCY-" + uuid4().hex[:10]
        amount = Decimal(os.environ.get("LOCAL_AI_FILL_LINE_VALUE", "82.37"))
        assert amount.is_finite() and 0 < amount <= 1000000
        args, source, version, _ = make_preview(tag + "-OK", amount)
        results = concurrent_confirm(args, source)
        assert sum(bool(r.get("idempotent")) for r in results) == 1, results
        assert all(r["version_name"] == version and r["ok"] for r in results)
        ledger = FrappeLedger()
        saved = ledger.rows("item", batch=args[0], version=version)
        amounts = [Decimal(present_material_row(r)["shipment_value_rmb"]) for r in saved]
        assert amounts == [amount] * 2
        assert all(r["quantity"] == 2 and r["actual_shipped_qty"] == 1 for r in saved)
        assert all(not r["purchase_currency"] and not r["unit_price"] for r in saved)
        batch = ledger.get("batch", args[0])
        assert batch["confirm_status"] == "Pending" and batch["writeback_status"] == "Not Started"
        report("ai_fill_consistency_real_database", ok=True, rows=2, shipment_total=sum(amounts),
               concurrent_confirmations=2, idempotent_replays=1)

        lease = edit_session_service.acquire_edit_session(args[0])
        assert lease["ok"]
        corrected = amount + Decimal("1.25")
        edited = calculate.update_item_field(
            saved[0]["name"], "shipment_value_rmb", str(corrected), version,
            remark="本地测试人工核对", edit_token=lease["edit_token"],
            expected_modified=str(lease["modified"]),
        )
        assert edited["ok"] and edited["changed"], edited
        updated_rows = ledger.rows("item", batch=args[0], version=version)
        assert [Decimal(present_material_row(row)["shipment_value_rmb"]) for row in updated_rows] == [corrected, amount]
        report("ai_fill_manual_edit_after_confirmation", ok=True, edited_rows=1)

        args, source, version, before = make_preview(tag + "-ROLLBACK", amount)
        original_put = FrappeLedger.put
        written = []

        def fail_after_first_item_write(self, kind, name, values):
            result = original_put(self, kind, name, values)
            if kind == "item" and name in {row["name"] for row in before}:
                written.append(name)
                raise RuntimeError("LOCAL-INJECTED-FAILURE")
            return result

        with patch.object(FrappeLedger, "put", fail_after_first_item_write):
            try:
                review.confirm(*args, repository=FixtureRepository(source))
            except RuntimeError as exc:
                assert str(exc) == "LOCAL-INJECTED-FAILURE", str(exc)
            else:
                raise AssertionError("The failure was not raised after the real item write")
        assert len(written) == 1
        assert ledger.rows("item", batch=args[0], version=version) == before
        assert FixtureRepository(source).get_run(args[1]).status == "READY"
        report("ai_fill_consistency_rollback", ok=True, persisted_changes=0)
        args, source, version, before = make_preview(tag + "-STALE", amount)
        changed = "2099-01-01 00:00:00"
        frappe.db.set_value("Overseas Cost Batch", args[0], "modified", changed, update_modified=False)
        frappe.db.commit()
        original_confirm = review.confirm
        with patch.object(review, "confirm", lambda *request: original_confirm(
            *request, repository=FixtureRepository(source)
        )):
            denied = materials.confirm_source_ai_selection(*args[:-1], changed)
        assert not denied["ok"] and denied["code"] == "BATCH_REVISION_CHANGED", denied
        assert ledger.rows("item", batch=args[0], version=version) == before
        assert FixtureRepository(source).get_run(args[1]).status == "READY"
        report("ai_fill_rejects_changes_after_preview", ok=True, persisted_changes=0)
        verify_oa_document_guard(tag + "-GUARD")
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
