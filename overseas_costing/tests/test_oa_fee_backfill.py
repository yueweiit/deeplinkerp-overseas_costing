"""Explicit OA fees use one parser and the existing guarded fee writer."""

from copy import deepcopy

import pytest

from overseas_costing.services import material_ai_fill_service as ai
from overseas_costing.services import oa_fee_backfill_service as backfill
from overseas_costing.services.logistics_settlement import runtime
from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository, _items
from overseas_costing.tests.test_unified_oa_fee_sync import install


def approval_source(**fields):
    return {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "process_instance_id": "LOG-1", "approval_role": "international_logistics",
        "source_label": "国际物流审批正文", "source_hash": "FIXTURE-HASH",
        "form_fields": {"物流方式": "空运", "物流费用": "4288.37 RMB",
                        "当地配送费": "900 MXN", **fields},
        "workflow_stage": "international_logistics",
    }


def normalize(source, existing=None):
    proposals = ai.build_approval_fee_proposals(source, transport_mode="AIR")
    for proposal in proposals:
        for ref in proposal["source_refs"]:
            ref["document_id"] = "DOC-1"
    document = {"document_id": "DOC-1", "form_fields": source["form_fields"],
                "source_ref": {"source": "approval_form", "source_id": source["source_id"],
                               "process_instance_id": "LOG-1", "workflow_stage": "international_logistics"}}
    return ai.normalize_source_review_proposals(
        proposals, [], [document], transport_mode="AIR", existing_fees=existing or [],
        trusted_system_proposal_ids={proposal["proposal_id"] for proposal in proposals},
    )


def install_backfill(monkeypatch, rows=None):
    db, audits = install(monkeypatch, rows)
    bundle = {
        "batch": db.batch, "version": db.version, "binding": None,
        "source": {"id": "LOG-1", "snapshot": "SNAP-1", "fields": approval_source()["form_fields"]},
        "context": {"root_kind": "logistics", "root_source_id": "LOG-1", "source_snapshot": "SNAP-1",
                    "available": True, "freight": {"selected": False}},
    }
    calls = []

    def current_bundle(batch, version, *, lock=False):
        calls.append((batch, version, lock))
        return bundle

    monkeypatch.setattr(backfill.effective_logistics_source, "current_source_bundle", current_bundle)
    return db, audits, bundle, calls


def test_explicit_air_and_delivery_fees_have_canonical_values_and_verified_fields():
    proposals = normalize(approval_source())
    assert [(row["payload"]["logical_fee_key"], row["payload"]["amount"], row["payload"]["currency"])
            for row in proposals] == [
        ("international_air_freight", "4288.37", "RMB"), ("destination_delivery", "900", "MXN"),
    ]
    assert [row["payload"]["allocation_basis"] for row in proposals] == ["chargeable_weight", "gross_weight"]
    assert all(row["result_origin"] == "SYSTEM" and row["default_selected"] for row in proposals)
    assert [row["source_refs"][0]["field"] for row in proposals] == ["物流费用", "当地配送费"]


def test_explicit_primary_fee_does_not_also_import_a_quote():
    proposals = ai.build_approval_fee_proposals(
        approval_source(**{"物流报价": "DHL报价，5000元"}), transport_mode="AIR"
    )
    assert len(proposals) == 2
    assert proposals[0]["payload"]["amount"] == "4288.37"


@pytest.mark.parametrize("value,count", [("0 RMB", 1), (0, 1), ("", 0), (None, 0)])
def test_explicit_zero_is_a_known_amount_not_an_absent_field(value, count):
    proposals = normalize(approval_source(**{"物流费用": value, "当地配送费": ""}))
    assert len(proposals) == count
    if count:
        assert proposals[0]["payload"]["amount"] == "0"
        assert proposals[0]["payload"]["amount_status"] == "ESTIMATED"


@pytest.mark.parametrize("status", ["ESTIMATED", "ACTUAL", "NOT_INCURRED", "INCLUDED", "MISSING"])
def test_verified_workflow_arbitration_does_not_reselect_existing_manual_or_zero_fee(status):
    existing = [{"name": "MANUAL", "logical_fee_key": "international_air_freight", "amount": "0",
                 "currency": "USD", "amount_status": status, "amount_revision": "manual:keep"}]
    original = deepcopy(existing)
    proposals = normalize(approval_source(), existing)
    assert proposals[0]["conflict"]
    assert not proposals[0]["default_selected"]
    assert proposals[1]["default_selected"]
    assert existing == original


def test_explicit_fee_parser_is_scoped_to_international_logistics():
    source = {**approval_source(), "approval_role": "purchase"}
    assert backfill.build_explicit_approval_fee_proposals(source, transport_mode="AIR") == []


def test_missing_row_repair_creates_both_fees_once_and_uses_locked_current_source(monkeypatch):
    db, audits, _, calls = install_backfill(monkeypatch)
    first = backfill.backfill_missing_oa_fees("B", "V")
    assert calls[0] == ("B", "V", True)
    writes = deepcopy(db.writes)
    audit_count = len(audits)
    calls.clear()
    second = backfill.backfill_missing_oa_fees("B", "V")
    assert first["created_count"] == 2
    assert second["action"] == "unchanged" and second["created_count"] == second["updated_count"] == 0
    assert len(db.rows) == 2 and db.writes == writes and len(audits) == audit_count
    assert calls[0] == ("B", "V", True)
    assert [(row["logical_fee_key"], row["amount"], row["currency"]) for row in db.rows] == [
        ("international_air_freight", 4288.37, "RMB"), ("destination_delivery", 900, "MXN"),
    ]
    assert all(row["amount_revision"].startswith("oa:") for row in db.rows)


def test_repair_persists_explicit_zero_clearance_and_tax_but_not_absent_fields(monkeypatch):
    db, _, bundle, _ = install_backfill(monkeypatch)
    bundle["source"]["fields"].update({"清关费": "0", "进口税费": 0})
    result = backfill.backfill_missing_oa_fees("B", "V")
    assert result["created_count"] == 4
    fees = {row["logical_fee_key"]: row for row in db.rows}
    for key in ("customs_clearance_fee", "import_tax"):
        assert fees[key]["amount"] == 0
        assert fees[key]["amount_status"] == "ESTIMATED"
        assert fees[key]["currency"] == "MXN"
    assert backfill.backfill_missing_oa_fees("B", "V")["action"] == "unchanged"


@pytest.mark.parametrize("values", [
    {"amount": 0, "amount_status": "ESTIMATED", "amount_revision": "oa:old"},
    {"amount": 123, "amount_status": "ACTUAL"},
    {"amount": 0, "amount_status": "NOT_INCURRED"},
    {"amount": 0, "amount_status": "INCLUDED"},
    {"amount": 0, "amount_status": "MISSING", "amount_revision": "manual:keep"},
    {"amount": 123, "is_enabled": 0},
    {"amount": 123, "is_active": 0},
    {"amount": 123, "scope_revision": "manual:keep"},
])
def test_missing_row_repair_preserves_every_existing_row_and_only_fills_other_fee(monkeypatch, values):
    original = {"name": "SAVED", "logical_fee_key": "international_air_freight",
                "rule_code": "oa_logistics_freight", "currency": "USD", **values}
    db, _, _, _ = install_backfill(monkeypatch, [original])
    result = backfill.backfill_missing_oa_fees("B", "V")
    assert result["created_count"] == 1 and result["updated_count"] == 0
    assert db.rows[0] == original
    assert db.rows[1]["logical_fee_key"] == "destination_delivery"


@pytest.mark.parametrize("change", ["binding", "freight", "invalid", "unavailable", "expense", "snapshot", "source"])
def test_repair_cannot_restore_fees_under_another_or_unavailable_source(monkeypatch, change):
    db, _, bundle, _ = install_backfill(monkeypatch)
    if change == "binding":
        bundle["binding"] = {"id": "FINAL"}
    elif change == "freight":
        bundle["context"]["freight"]["selected"] = True
    elif change == "invalid":
        bundle["context"]["invalid"] = True
    elif change == "unavailable":
        bundle["context"]["available"] = False
    elif change == "expense":
        bundle["context"]["root_kind"] = "expense"
    elif change == "snapshot":
        bundle["context"]["source_snapshot"] = "OLD"
    else:
        bundle["context"]["root_source_id"] = "OTHER"
    assert backfill.backfill_missing_oa_fees("B", "V")["action"] == "skipped"
    assert not db.writes


def test_repair_retains_canonical_final_binding_guard(monkeypatch):
    db, _, _, _ = install_backfill(monkeypatch)
    monkeypatch.setattr(runtime, "has_final_binding", lambda _batch: True)
    result = backfill.backfill_missing_oa_fees("B", "V")
    assert result["created_count"] == result["updated_count"] == 0
    assert not db.writes


@pytest.mark.parametrize("target,change", [
    ("batch", {"current_version": "OTHER"}), ("batch", {"is_locked": 1}),
    ("batch", {"confirm_status": "Confirmed"}), ("version", {"status": "Archived"}),
])
def test_repair_uses_existing_current_version_and_frozen_state_guards(monkeypatch, target, change):
    db, _, _, _ = install_backfill(monkeypatch)
    getattr(db, target).update(change)
    assert not backfill.backfill_missing_oa_fees("B", "V")["ok"]
    assert not db.writes


def test_worker_emits_both_explicit_fees_when_model_returns_no_proposals(monkeypatch):
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest

    repo = _LifecycleRepository(status="QUEUED")
    repo.sources = [approval_source()]
    context = {**repo.get_context("B1", "V1"), "transport_mode": "AIR"}
    repo.get_context = lambda *_args: deepcopy(context)
    manifest = prepare_source_manifest(repo.sources)
    repo.run.update(proposal_version=1, source_manifest_json=manifest,
                    input_fingerprint=ai._source_review_fingerprint("B1", "V1", _items(), manifest, "", context=context))
    monkeypatch.setattr(ai, "_call_source_review_ai", lambda *_args, **_kwargs: {"proposals": []})
    result = ai.execute_material_ai_fill("RUN-1", repository=repo)
    assert result["status"] in {"READY", "READY_WITH_WARNINGS"}, repo.run.get("error_message")
    assert repo.run["draft_json"]["summary"]["fee_update"] == 2
