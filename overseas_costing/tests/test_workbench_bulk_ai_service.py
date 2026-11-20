"""Bulk staging uses the real catalog, fill-only projection and native receipts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from overseas_costing.services import workbench_bulk_ai_service as service
from overseas_costing.tests.test_ai_selection_service import Repo


@pytest.fixture
def runtime(monkeypatch):
    repo = Repo()
    repo.supports_row_selection = True
    repo.list_sources_with_payment_references = lambda *args: deepcopy(repo.sources)
    repo.get_locked_fees = lambda *args: deepcopy(repo.fees)
    repo.commit = lambda: None
    calls = []
    monkeypatch.setattr(service.ai, "FrappeMaterialAIFillRepository", lambda: repo)
    monkeypatch.setattr(service.linked_sources, "hydrate_linked_sources", lambda *args, **kwargs: {
        "status": "COMPLETE", "linked_count": 0, "hydrated_count": 0, "blockers": []})
    monkeypatch.setattr(service.ai, "payment_source_preflight", lambda *args: {
        "payment_preflight": {"status": "NOT_REQUIRED", "selected_refs": []}})
    monkeypatch.setattr(service, "require_batch_permission", lambda batch, right: batch)
    monkeypatch.setattr(service.leases, "acquire_edit_session", lambda batch: (
        calls.append(("acquire", batch)) or {"ok": True, "edit_token": "TOKEN"}))
    monkeypatch.setattr(service.leases, "release_edit_session", lambda batch, token: calls.append(("release", batch, token)))
    monkeypatch.setattr(service.leases, "_lock_row", lambda batch: {})
    repo.assert_write = lambda batch, token, revision: calls.append(("write_guard", batch, token, revision))
    native_writer = repo.apply_row_selection

    def write(run, preview, draft, context):
        result = native_writer(run, preview, draft, context)
        result["changed_count"] = len(preview["changes"]) + len(preview["fees"])
        return result

    repo.apply_row_selection = write
    return repo, calls


def test_preview_then_explicit_apply_uses_real_receipt_and_is_idempotent(runtime):
    repo, calls = runtime
    before = deepcopy(repo.items)
    staged = service.preview("B1", repo.run["name"])
    assert staged["status"] == "PREVIEW_READY" and staged["can_apply"]
    assert staged["field_names"] == ["gross_weight_kg"]
    assert repo.items == before and not repo.writes and not calls
    receipt = repo.run["draft_json"]["row_previews"][staged["preview_id"]]
    assert receipt["mode"] == "fill_missing"
    assert receipt["selected_row_ids"] == receipt["selected_fee_ids"] == []
    assert not receipt["selected_packing_assignments"]

    args = ("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert service.apply(*args)["status"] == "APPLIED"
    assert len(repo.writes) == 1 and repo.writes[0]["rows"][0]["gross_weight_kg"] == 2
    assert calls == [("acquire", "B1"), ("write_guard", "B1", "TOKEN", "M1"), ("release", "B1", "TOKEN")]
    assert service.apply(*args)["idempotent"]
    assert len(repo.writes) == 1 and len(calls) == 3


def test_existing_values_are_never_overwritten(runtime):
    repo, calls = runtime
    repo.items[0]["gross_weight_kg"] = 7
    repo.run["input_fingerprint"] = service.ai._source_review_fingerprint(
        "B1", "V1", repo.items, repo.sources, "", context=repo.context)
    repo.run["draft_json"]["material_input_fingerprint"] = service.selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    staged = service.preview("B1", repo.run["name"])
    if staged.get("preview_id"):
        result = service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
        assert not result["can_apply"]
    assert not staged["can_apply"] and repo.items[0]["gross_weight_kg"] == 7
    assert not repo.writes and not calls


def test_unverified_value_and_conflicting_same_stage_evidence_are_not_selected(runtime):
    repo, calls = runtime
    repo.sources.append({**repo.sources[0], "source_id": "OTHER"})
    repo.run["source_manifest_json"] = deepcopy(repo.sources)
    repo.run["input_fingerprint"] = service.ai._source_review_fingerprint(
        "B1", "V1", repo.items, repo.sources, "", context=repo.context)
    repo.run["draft_json"]["material_input_fingerprint"] = service.selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    repo.run["candidates_json"].extend([
        {**deepcopy(repo.run["candidates_json"][0]), "proposal_id": "PRICE",
         "payload": {"fields": {"goods_value": 85}}},
        {**deepcopy(repo.run["candidates_json"][0]), "proposal_id": "CONFLICT",
         "source_refs": [{"source_id": "OTHER"}], "payload": {"fields": {"gross_weight_kg": 3}}},
    ])
    result = service.preview("B1", repo.run["name"])
    assert not result["can_apply"]
    assert not repo.writes and not calls


def test_changed_source_never_reaches_native_writer(runtime):
    repo, calls = runtime
    staged = service.preview("B1", repo.run["name"])
    repo.sources[0]["source_hash"] = "CHANGED"
    result = service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert result["status"] == "STALE"
    assert not repo.writes and not calls


def test_older_or_manual_selection_cannot_be_applied_as_bulk_defaults(runtime):
    repo, calls = runtime
    result = service.apply("B1", repo.run["name"], "OTHER-PREVIEW", "OTHER-REVISION")
    assert result["status"] == "PREVIEW_CHANGED" and not result["can_apply"]
    assert not repo.writes and not calls


def test_other_user_lease_is_respected(runtime, monkeypatch):
    repo, calls = runtime
    staged = service.preview("B1", repo.run["name"])
    monkeypatch.setattr(service.leases, "acquire_edit_session", lambda batch: {"ok": False, "locked_by": "OTHER"})
    result = service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert result["status"] == "EDIT_LOCKED"
    assert "locked_by" not in result and not repo.writes and not calls


def test_native_failure_rolls_back_and_releases_lease(runtime):
    repo, calls = runtime
    staged = service.preview("B1", repo.run["name"])
    repo.apply_row_selection = lambda *args: (_ for _ in ()).throw(ValueError("source changed"))
    with pytest.raises(ValueError, match="source changed"):
        service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert repo.rolled_back and not repo.writes
    assert calls[-1] == ("release", "B1", "TOKEN")


def test_payment_selection_is_not_bypassed_with_empty_references(runtime, monkeypatch):
    repo, calls = runtime
    repo.run["status"] = "FAILED"
    preflights = []
    monkeypatch.setattr(service.ai, "payment_source_preflight", lambda *args: (
        preflights.append(args) or {"payment_preflight": {"status": "NEEDS_SELECTION", "selected_refs": []}}))
    result = service.analyze("B1", "request-123")
    assert result["status"] == "PAYMENT_SELECTION" and result["payment_selection_required"]
    assert preflights == [("B1", "V1")]
    assert len(repo.created) == 1 and not repo.writes and not calls


def test_running_run_is_returned_without_restarting_worker(runtime, monkeypatch):
    repo, calls = runtime
    repo.run["status"] = "FAILED"
    monkeypatch.setattr(service.ai, "_default_enqueue", lambda *args: None)
    first = service.analyze("B1", "request-123")
    repo.run["status"] = "RUNNING"
    monkeypatch.setattr(service.ai, "_default_enqueue", lambda *args: pytest.fail("must not requeue"))
    result = service.analyze("B1", "request-123")
    assert result["run_id"] == first["run_id"] and result["status"] == "RUNNING"
    assert not repo.writes and not calls


def test_frozen_or_written_back_context_stays_blocked(runtime):
    repo, calls = runtime
    repo.get_context = lambda *args: (_ for _ in ()).throw(ValueError("frozen version"))
    with pytest.raises(ValueError, match="frozen version"):
        service.analyze("B1", "request-123")
    assert service.preview("B1", repo.run["name"])["status"] == "STALE"
    assert not repo.writes and not calls


def test_summary_does_not_return_raw_documents_urls_or_credentials(runtime):
    repo, _ = runtime
    repo.run.update(status="FAILED", error_message="https://secret.invalid/?token=PRIVATE",
                    source_progress_json=[{"text": "PRIVATE", "file_url": "PRIVATE"}])
    result = service.preview("B1", repo.run["name"])
    assert result["status"] == "FAILED" and "PRIVATE" not in str(result)


def test_ambiguous_default_flags_are_not_guessed():
    candidate = {"item_name": "I1", "fieldname": "gross_weight_kg", "candidate_id": "ONE",
                 "can_apply": True, "default_selected": True}
    assert service._recommended_fields({"field_candidates": [candidate, {**candidate, "candidate_id": "TWO"}]}) == {}


@pytest.mark.parametrize("structural", ["added_count", "removed_count", "scope_excluded_item_names"])
def test_structural_native_projection_never_becomes_automatic_fill(runtime, monkeypatch, structural):
    repo, calls = runtime
    native_prepare = service.selections.prepare

    def proposed_structure(*args, **kwargs):
        result = native_prepare(*args, **kwargs)
        result["preview"][structural] = ["OTHER"] if structural == "scope_excluded_item_names" else 1
        return result

    monkeypatch.setattr(service.selections, "prepare", proposed_structure)
    staged = service.preview("B1", repo.run["name"])
    assert staged["status"] == "REVIEW_REQUIRED" and not staged["can_apply"]
    result = service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert not result["can_apply"] and not repo.writes and not calls


def test_source_change_after_lease_is_caught_by_native_confirm(runtime, monkeypatch):
    repo, calls = runtime
    staged = service.preview("B1", repo.run["name"])

    def acquire(batch):
        repo.sources[0]["source_hash"] = "CHANGED-AFTER-PREVIEW"
        return {"ok": True, "edit_token": "TOKEN"}

    monkeypatch.setattr(service.leases, "acquire_edit_session", acquire)
    with pytest.raises(ValueError, match="来源|资料"):
        service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert repo.rolled_back and not repo.writes
    assert calls[-1] == ("release", "B1", "TOKEN")


def test_native_start_queues_once_and_leaves_business_rows_untouched(runtime, monkeypatch):
    repo, calls = runtime
    repo.run["status"] = "FAILED"
    repo.list_sources_with_payment_references = lambda *args: deepcopy(repo.sources)
    queued = []
    monkeypatch.setattr(service.ai, "payment_source_preflight", lambda *args: {
        "payment_preflight": {"status": "NOT_REQUIRED", "selected_refs": []}})
    monkeypatch.setattr(service.ai, "_default_enqueue", queued.append)
    before = deepcopy(repo.items)
    first = service.analyze("B1", "request-123")
    second = service.analyze("B1", "request-123")
    assert first["status"] == second["status"] == "QUEUED"
    assert first["run_id"] == second["run_id"]
    assert queued == [first["run_id"]] and repo.items == before
    assert not repo.writes and not calls


def test_confirmed_zero_shipment_stays_zero_while_other_missing_fields_fill(runtime):
    repo, calls = runtime
    repo.items[0].update(actual_shipped_qty=0, actual_shipped_qty_mode="MANUAL_CONFIRMED")
    repo.run["candidates_json"][0]["payload"]["fields"]["actual_shipped_qty"] = 20
    repo.run["input_fingerprint"] = service.ai._source_review_fingerprint(
        "B1", "V1", repo.items, repo.sources, "", context=repo.context)
    repo.run["draft_json"]["material_input_fingerprint"] = service.selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    staged = service.preview("B1", repo.run["name"])
    assert staged["field_names"] == ["gross_weight_kg"]
    service.apply("B1", repo.run["name"], staged["preview_id"], staged["preview_revision"])
    assert repo.writes[0]["rows"][0]["actual_shipped_qty"] == 0
    assert calls[-1] == ("release", "B1", "TOKEN")


@pytest.mark.parametrize(("current_amount", "recommended", "expected_count"), [
    (None, True, 1), (0, True, 0), (17, True, 0), (None, False, 0),
])
def test_fee_only_preview_uses_central_missing_fee_policy(runtime, current_amount, recommended, expected_count):
    repo, _ = runtime
    repo.run["trigger_mode"] = "BULK_GAP_FILL"
    repo.run["candidates_json"] = [{
        "proposal_id": "APPROVED-FEE", "proposal_type": "fee_update", "result_origin": "SYSTEM",
        "default_selected": recommended, "confidence": .99, "source_refs": [{"source_id": "DOC"}],
        "payload": {"logical_fee_key": "international_sea_freight", "expense_category": "国际海运费",
                    "amount": "100", "currency": "RMB", "amount_status": "ESTIMATED",
                    "allocation_basis": "volume", "scope_type": "ALL_ITEMS"},
    }]
    if current_amount is not None:
        repo.fees = [{"name": "EXISTING", "logical_fee_key": "international_sea_freight",
                      "amount": current_amount, "amount_status": "ESTIMATED", "is_active": 1, "is_enabled": 1}]
    result = service.preview("B1", repo.run["name"])
    assert result.get("fee_count", 0) == expected_count
    if expected_count:
        assert result["field_names"] == [] and result["can_apply"]
        adopted = service.apply("B1", repo.run["name"], result["preview_id"], result["preview_revision"])
        assert adopted["changed_count"] == 1 and len(repo.writes[0]["fees"]) == 1
    else:
        assert not result["can_apply"] and not repo.writes


def test_native_fee_confirmation_repository_uses_current_locking_reads(monkeypatch):
    calls = []

    def sql(query, params, **kwargs):
        calls.append((query, params))
        assert "FOR UPDATE" in query and kwargs["as_dict"]
        if "tabOverseas Cost Batch" in query:
            return [{"transport_mode": "SEA"}]
        return [{"name": "FEE", "logical_fee_key": "international_sea_freight", "amount": 200,
                 "amount_status": "ESTIMATED", "is_active": 1, "is_enabled": 1}]

    monkeypatch.setattr(service.ai, "frappe", SimpleNamespace(db=SimpleNamespace(sql=sql)))
    result = service.ai.FrappeMaterialAIFillRepository().get_locked_fees("B1", "V1")
    assert result[0]["amount"] == 200
    assert calls[0][1] == ("B1",) and calls[1][1] == ("B1", "V1")
    assert "`amount_revision`" in calls[1][0] and "ORDER BY name FOR UPDATE" in calls[1][0]


