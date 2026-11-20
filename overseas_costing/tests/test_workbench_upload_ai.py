"""Local upload scheduling, safe native adoption and saved-trial continuation."""

from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

from overseas_costing.services import cost_preview_service, import_service
from overseas_costing.services import workbench_bulk_ai_service as service
from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository
from overseas_costing.tests.test_workbench_bulk_ai_service import runtime


@pytest.mark.parametrize("trigger_mode", sorted(service.ai.AUTOMATIC_GAP_FILL_TRIGGER_MODES))
def test_upload_ready_continues_native_fill_before_calculation_and_records_status(runtime, monkeypatch, trigger_mode):
    repo, calls = runtime
    repo.run.update(trigger_mode=trigger_mode)
    repo.run["draft_json"]["automatic_processing"] = {"status": "PENDING"}
    committed = []
    repo.commit = lambda: committed.append(len(repo.writes))

    def calculate(batch, version, **kwargs):
        assert committed[-1] == 1
        assert repo.run["status"] == "APPLIED"
        assert kwargs["trusted"] is False and kwargs["edit_token"] == "TOKEN"
        return {"ok": True, "saved": True, "summary": {"is_complete": True}, "excluded_fees": []}

    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", calculate)
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["status"] == "CALCULATED" and result["saved"]
    assert result["changed_count"] == 1 and result["blockers"] == []
    assert repo.run["draft_json"]["automatic_processing"] == result
    assert service.ai.get_source_ai_review_status("B1", repo.run["name"], repository=repo)["automatic_processing"] == result
    assert len(repo.writes) == 1
    assert service.continue_automatic_review("B1", repo.run["name"]) == result
    assert len(repo.writes) == 1 and calls[-1] == ("release", "B1", "TOKEN")


def test_no_value_evidence_keeps_native_blocker_codes_without_guessing(runtime, monkeypatch):
    repo, _ = runtime
    repo.run.update(trigger_mode="LOCAL_UPLOAD", candidates_json=[])
    repo.commit = lambda: None
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: (
        _ for _ in ()).throw(ValueError("还有 1 行本次发货货值缺失或失效，请先补齐后再试算。")))
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["status"] == "NEEDS_MATERIALS" and result["missing_value_count"] == 1
    assert result["blockers"][0]["code"]
    assert "货值" in result["blockers"][0]["hint"]
    assert not repo.writes


@pytest.mark.parametrize("status", ["NO_APPLICABLE_GAPS", "REVIEW_REQUIRED"])
def test_unapplied_preview_changes_are_not_reported_as_saved(runtime, monkeypatch, status):
    repo, _ = runtime
    repo.run.update(trigger_mode="BULK_GAP_FILL")
    monkeypatch.setattr(service, "preview", lambda *args: {
        "status": status, "can_apply": False, "changed_count": 87})
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: {
        "ok": True, "saved": True, "summary": {"is_complete": False}, "excluded_fees": []})
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["changed_count"] == 0
    assert repo.run["status"] != "APPLIED" and not repo.writes
    assert result["status"] in {"NEEDS_MATERIALS", "REVIEW_REQUIRED"}


def test_calculation_failure_preserves_already_committed_gap_fill(runtime, monkeypatch):
    repo, _ = runtime
    repo.run.update(trigger_mode="LOCAL_UPLOAD")
    commits, attempts = [], []
    repo.commit = lambda: commits.append(deepcopy(repo.writes))
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: (
        attempts.append(args) or (_ for _ in ()).throw(ValueError("请补充汇率日期资料。"))))
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["status"] == "NEEDS_MATERIALS" and result["changed_count"] == 1
    assert repo.run["status"] == "APPLIED" and len(commits[1]) == 1
    assert len(repo.writes) == 1
    assert len(attempts) == 1


@pytest.mark.parametrize('deadlocks', [1, 2, 3])
def test_automatic_trial_retries_native_deadlocks_with_fresh_context_and_lease_only(runtime, monkeypatch, deadlocks):
    from overseas_costing.tests.test_cost_preview_service import AutoFxCalculationRepository

    class QueryDeadlockError(Exception):
        pass

    repo, calls = runtime
    repo.run.update(trigger_mode='BULK_GAP_FILL')
    committed, contexts, calculation_repos, attempts = [], [], [], []
    repo.commit = lambda: committed.append(deepcopy(repo.writes))
    repo.rollback = lambda: setattr(repo, 'writes', deepcopy(committed[-1]))
    monkeypatch.setitem(sys.modules, 'frappe', SimpleNamespace(db=SimpleNamespace(rollback=repo.rollback)))
    native_context = repo.get_context

    def get_context(*args):
        context = native_context(*args)
        if repo.run['status'] == 'APPLIED':
            contexts.append(context['version'])
        return context

    repo.get_context = get_context

    def acquire(batch):
        token = 'TOKEN-' + str(1 + sum(row[0] == 'acquire' for row in calls))
        calls.append(('acquire', batch, token))
        return {'ok': True, 'edit_token': token, 'modified': 'M1'}

    monkeypatch.setattr(service.leases, 'acquire_edit_session', acquire)

    class CalculationRepository(AutoFxCalculationRepository):
        prepare_fx_resolution = AutoFxCalculationRepository.prepare_fx_resolution

        def lock_and_load(self, batch, version, **kwargs):
            assert committed[-1] and repo.run['status'] == 'APPLIED'
            attempts.append(kwargs['edit_token'])
            if len(attempts) <= deadlocks:
                raise QueryDeadlockError('private SQL deadlock details')
            return super().lock_and_load(batch, version, **kwargs)

    def cost_repository(**kwargs):
        calculation = CalculationRepository()
        calculation_repos.append(calculation)
        return calculation

    monkeypatch.setattr(cost_preview_service, 'FrappeCostRepository', cost_repository)
    monkeypatch.setattr(service.ai, '_default_enqueue', lambda *args: pytest.fail('must not reanalyze'))
    result = service.continue_automatic_review('B1', repo.run['name'])
    expected = min(deadlocks + 1, 3)
    assert len(attempts) == len(calculation_repos) == len(contexts) == expected
    assert attempts == ['TOKEN-' + str(index + 2) for index in range(expected)]
    assert [row[2] for row in calls if row[0] == 'release'] == [
        'TOKEN-' + str(index + 1) for index in range(expected + 1)]
    assert len(repo.writes) == 1 and result['changed_count'] == 1
    assert repo.writes[0]['rows'][0]['gross_weight_kg'] == 2
    assert sum(calculation.rollbacks for calculation in calculation_repos) == min(deadlocks, 3)
    assert result['status'] == ('CALCULATED' if deadlocks < 3 else 'REVIEW_REQUIRED')
    assert not result['required_documents'] and 'private' not in str(result)
    assert bool(calculation_repos[-1].saved) is (deadlocks < 3)


def test_manual_run_never_auto_applies_or_calculates(runtime, monkeypatch):
    repo, _ = runtime
    repo.run["trigger_mode"] = "MANUAL"
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: pytest.fail("manual"))
    assert service.continue_automatic_review("B1", repo.run["name"])["status"] == "MANUAL_REVIEW_REQUIRED"
    assert not repo.writes


@pytest.mark.parametrize("payment_selection", [True, False])
def test_native_registration_routes_local_upload_through_production_preflight(runtime, monkeypatch, payment_selection):
    repo, _ = runtime
    repo.run["status"] = "FAILED"
    queued, registered, updated = [], [], []
    doc = SimpleNamespace(name="ATT12345")
    doc.insert = lambda **kwargs: doc

    def get_doc(values):
        registered.append(values)
        return doc

    monkeypatch.setattr(import_service, "frappe", SimpleNamespace(
        get_doc=get_doc, db=SimpleNamespace(commit=lambda: None, set_value=lambda *args, **kwargs: updated.append(args))))
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B1")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V1")
    monkeypatch.setattr(import_service, "_attach_existing_file_to_attachment", lambda *args: None)
    monkeypatch.setattr(service.ai, "_default_enqueue", queued.append)
    checked = []
    monkeypatch.setattr(service.ai, "payment_source_preflight", lambda *args: (
        checked.append(args) or {"payment_preflight": {
            "status": "NEEDS_SELECTION" if payment_selection else "NOT_REQUIRED", "selected_refs": []}}))
    result = import_service.register_manual_document_attachment(
        "B1", "SEA", "packing", "Packing", version_name="V1", file_name="purchase.png",
        file_url="/private/files/purchase.png")
    assert checked == [("B1", "V1")]
    assert len(registered) == 1 and not repo.writes
    processing = result["processing"]
    if payment_selection:
        assert processing["status"] == "PAYMENT_SELECTION" and not queued
        assert processing["blockers"][0]["code"] == "PAYMENT_SOURCE_SELECTION_REQUIRED"
        assert processing["required_documents"] == [] and processing["actions"] == processing["blockers"]
        assert json.loads(updated[0][3])["automatic_processing"] == processing
    else:
        assert processing["status"] == "QUEUED" and queued == [processing["run_id"]]
        assert repo.run["trigger_mode"] == "LOCAL_UPLOAD"
        assert not updated  # Do not stale queued evidence by changing its metadata.


@pytest.mark.parametrize("callback_failure", [False, True])
def test_worker_commits_pending_marker_before_auto_continuation(monkeypatch, callback_failure):
    repo = _LifecycleRepository(status="QUEUED")
    repo.run["trigger_mode"] = "LOCAL_UPLOAD"
    coverage = {"status": "COMPLETE", "linked_count": 0, "hydrated_count": 0, "blockers": []}
    draft = service.ai._load_json(repo.run.get('draft_json'), {})
    draft['review_input'] = {**draft.get('review_input', {}), 'source_coverage': coverage}
    repo.run['draft_json'] = draft
    context = repo.get_context("B1", "V1")
    repo.run["input_fingerprint"] = service.ai.build_input_fingerprint(
        "B1", "V1", repo.get_items("B1", "V1"), repo.sources, context=context)
    monkeypatch.setattr(service.ai, "FrappeMaterialAIFillRepository", lambda: repo)
    monkeypatch.setattr(service.ai, "_read_source", lambda *args, **kwargs: ([], {
        "text": "trusted source text", "source_ref": {"source_id": "A"}, "ai_eligible": True}))
    monkeypatch.setattr(service.ai, "_call_material_ai", lambda *args: {"ok": True, "candidates": []})
    seen = []

    def complete(batch, run_id):
        draft = service.ai._load_json(repo.run["draft_json"], {})
        seen.append(draft["automatic_processing"]["status"])
        assert draft['review_input']['source_coverage'] == coverage
        assert repo.run["status"] in service.ai.PREVIEW_READY_STATES
        if callback_failure:
            raise RuntimeError("do not relabel completed analysis FAILED")
        return {"status": "NEEDS_MATERIALS", "run_id": run_id, "changed_count": 0, "blockers": []}

    monkeypatch.setattr(service, "continue_automatic_review", complete)
    result = service.ai.execute_material_ai_fill("RUN-1")
    assert seen == ["PENDING"]
    assert result["status"] in service.ai.PREVIEW_READY_STATES
    assert result["automatic_processing"]["status"] == ("REVIEW_REQUIRED" if callback_failure else "NEEDS_MATERIALS")
    if callback_failure:
        assert service.ai._load_json(repo.run["draft_json"], {})["automatic_processing"]["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("status", ["FAILED", "STALE", "DISCARDED"])
def test_terminal_analysis_never_polls_forever_with_old_pending_marker(runtime, status):
    repo, _ = runtime
    repo.run.update(status=status, trigger_mode="LOCAL_UPLOAD")
    repo.run["draft_json"]["automatic_processing"] = {"status": "PENDING"}
    result = service.ai.get_source_ai_review_status("B1", repo.run["name"], repository=repo)
    assert result["automatic_processing"]["status"] == status


def test_saved_partial_trial_keeps_native_incomplete_reason(runtime, monkeypatch):
    repo, _ = runtime
    repo.run.update(trigger_mode="LOCAL_UPLOAD")
    repo.commit = lambda: None
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: {
        "ok": True, "saved": True, "summary": {"is_complete": False}, "excluded_fees": [],
        "incomplete_reasons": [{"reason_code": "SHIPPING_UNIT_REQUIRED", "message": "请补充发货单位。"}]})
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["saved"] and result["status"] == "NEEDS_MATERIALS"
    assert result["blockers"] == [{"code": "SHIPPING_UNIT_REQUIRED", "hint": "请补充发货单位。"}]
    assert result["required_documents"][0]["key"] == "packing" and not result["actions"]


@pytest.mark.parametrize("owner", ["current-user", "other-user"])
def test_automatic_fill_never_replaces_an_active_browser_lease(runtime, monkeypatch, owner):
    repo, calls = runtime
    repo.run.update(trigger_mode="LOCAL_UPLOAD")
    repo.commit = lambda: None
    monkeypatch.setattr(service.leases, "_lock_row", lambda batch: {
        "edit_lock_owner": owner, "edit_lock_token": "BROWSER-TOKEN", "edit_lock_expires_at": "2099-01-01"})
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: pytest.fail("active lease"))
    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["status"] == "EDIT_LOCKED" and not repo.writes
    assert result["required_documents"] == [] and result["actions"] == result["blockers"]
    assert not calls  # In particular, do not acquire or release the browser's lease.


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
def test_upload_marker_survives_page_refresh_before_ready(runtime, status):
    repo, _ = runtime
    repo.run.update(status=status, trigger_mode="LOCAL_UPLOAD")
    result = service.ai.get_source_ai_review_status("B1", repo.run["name"], repository=repo)
    assert result["automatic_processing"]["status"] == status
    assert result["automatic_processing"]["run_id"] == repo.run["name"]


@pytest.mark.parametrize("existing_trigger", ["MANUAL", "BULK_GAP_FILL"])
@pytest.mark.parametrize("force", [False, True])
def test_bulk_never_hydrates_or_supersedes_a_running_analysis(runtime, monkeypatch, existing_trigger, force):
    repo, _ = runtime
    repo.run.update(status="RUNNING", trigger_mode=existing_trigger)
    monkeypatch.setattr(service.ai, "start_source_ai_review", lambda *args, **kwargs: pytest.fail("must not supersede"))
    monkeypatch.setattr(service.linked_sources, "hydrate_linked_sources", lambda *args, **kwargs: pytest.fail("running inputs"))
    result = service.analyze("B1", "bulk-request-123", trigger_mode="BULK_GAP_FILL", force=force)
    assert result["status"] == "RUNNING" and len(repo.created) == 1
    expected = "RUNNING" if existing_trigger == "BULK_GAP_FILL" else "REVIEW_REQUIRED"
    assert result["automatic_processing"]["status"] == expected


def test_bulk_force_has_payment_preflight_and_keeps_a_distinct_audit_trigger(runtime, monkeypatch):
    repo, _ = runtime
    queued, preflight = [], []
    monkeypatch.setattr(service.ai, "_default_enqueue", queued.append)
    monkeypatch.setattr(service.ai, "payment_source_preflight", lambda *args: (
        preflight.append(args) or {"payment_preflight": {"status": "NOT_REQUIRED", "selected_refs": []}}))
    result = service.analyze("B1", "bulk-request-123", trigger_mode="BULK_GAP_FILL", force=True)
    assert preflight == [("B1", "V1")] and queued == [result["run_id"]]
    assert repo.run["trigger_mode"] == "BULK_GAP_FILL" and len(repo.created) == 2
    assert not repo.writes


def test_native_marker_saves_increment_revision_and_are_visible_to_incremental_poll(monkeypatch):
    values = {"name": "RUN", "batch": "B1", "version": "V1", "status": "RUNNING",
              "trigger_mode": "LOCAL_UPLOAD", "progress_revision": 10}
    commits = []

    def sql(query, params, **kwargs):
        assert "FOR UPDATE" in query
        return [dict(values)]

    def set_value(doctype, name, updates, **kwargs):
        values.update(updates)

    monkeypatch.setattr(service.ai, "frappe", SimpleNamespace(
        db=SimpleNamespace(sql=sql, set_value=set_value, commit=lambda: commits.append(1)),
        get_doc=lambda *args: SimpleNamespace(**values)))
    repo = service.ai.FrappeMaterialAIFillRepository()
    revision = values["progress_revision"]
    for status in ("PENDING", "RUNNING", "CALCULATED"):
        repo.save_run(repo.get_run("RUN"), draft_json={"automatic_processing": {
            "status": status, "run_id": "RUN", "changed_count": 0, "blockers": []}})
        result = service.ai.get_material_ai_fill_status("B1", "RUN", after_revision=revision, repository=repo)
        assert result["progress_revision"] == revision + 1 and not result["unchanged"]
        assert result["automatic_processing"]["status"] == status
        revision = result["progress_revision"]
    assert len(commits) == 3


def test_native_ai_context_rejects_independently_locked_active_batch(monkeypatch):
    from overseas_costing.services import batch_service

    def get_value(doctype, *args, **kwargs):
        if doctype == "Overseas Cost Batch":
            return {"name": "B1", "current_version": "V1", "is_locked": 1}
        return {"name": "V1", "batch": "B1", "status": "Active"}

    monkeypatch.setattr(service.ai, "frappe", SimpleNamespace(db=SimpleNamespace(get_value=get_value)))
    monkeypatch.setattr(batch_service, "_resolve_batch_name", lambda batch: "B1")
    with pytest.raises(ValueError, match="不能生成"):
        service.ai.FrappeMaterialAIFillRepository().get_context("B1", "V1")


def test_locked_native_writer_rejects_active_batch_without_changing_rows():
    from overseas_costing.tests.test_ai_selection_writer import setup_cost, selection, write_rows, load_source_bundle

    store, ledger, batch, version, *_ = setup_cost()
    name, version_name = batch["name"], version["name"]
    items = ledger.rows("item", version=version_name)
    context = load_source_bundle(name, version_name, store=store, ledger=ledger)["context"]
    prepared = selection(items, context, version_name, name, "fill_missing")
    ledger.put("batch", name, {"is_locked": 1})
    with pytest.raises(ValueError, match="冻结"):
        write_rows(store, ledger, prepared, context)
    assert ledger.rows("item", version=version_name) == items


def test_native_trial_blockers_keep_distinct_rows_fee_identity_and_actions(runtime, monkeypatch):
    repo, _ = runtime
    repo.items = [
        {"name": "I1", "stable_line_key": "LINE-1", "material_code": "SKU1", "row_no": 7},
        {"name": "I2", "stable_line_key": "LINE-2", "material_code": "SKU1", "row_no": 8},
    ]
    _prepare_automatic_snapshot(repo)
    fees = [
        {"name": "FEE-MISSING", "logical_fee_key": "bill-missing", "expense_category": "运费",
         "amount_status": "MISSING", "amount": "", "currency": "USD"},
        {"name": "FEE-FX", "logical_fee_key": "bill-fx", "expense_category": "清关费",
         "amount_status": "ACTUAL", "amount": "30", "currency": "USD"},
    ]
    native = cost_preview_service.preview_comprehensive_cost_data(repo.items, fees, {})
    native.update(ok=True, saved=True, raw_attachment="https://private.example/raw?token=secret")
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: native)

    result = service.continue_automatic_review("B1", repo.run["name"])
    groups = {group["key"]: group for group in result["required_documents"]}
    assert set(groups) == {"purchase", "packing", "fees"}
    for key in ("purchase", "packing"):
        assert {(row["item_key"], row["item_name"], row["material_code"], row["row_no"])
                for row in groups[key]["blockers"]} == {
                    ("LINE-1", "I1", "SKU1", 7), ("LINE-2", "I2", "SKU1", 8)}
    fee = groups["fees"]["blockers"]
    assert len(fee) == 1  # Native excluded_fees and incomplete_reasons describe the same fee.
    assert fee[0] == {"code": "AMOUNT_MISSING", "hint": "该费用未计入本次试算。",
                      "fee_key": "bill-missing", "rule_name": "FEE-MISSING", "expense_category": "运费",
                      "currency": "USD", "amount_status": "MISSING"}
    assert result["actions"][0]["code"] == "FX_RATE_MISSING"
    assert result["actions"][0]["rule_name"] == "FEE-FX"
    assert all(group["source_coverage"] == "not_verified" for group in groups.values())
    assert {row["field"] for row in groups["purchase"]["blockers"]} == {"goods_value"}
    assert {row["field"] for row in groups["packing"]["blockers"]} == {"actual_shipped_qty"}
    assert "private.example" not in json.dumps(result) and not repo.writes
    assert repo.run["draft_json"]["automatic_processing"] == result


def test_purchase_coverage_keeps_every_missing_row_and_separates_mismatch(runtime, monkeypatch):
    repo, _ = runtime
    repo.items = [
        {"name": "I1", "stable_line_key": "LINE-1", "material_code": "SKU1", "row_no": 2},
        {"name": "I2", "stable_line_key": "LINE-2", "material_code": "SKU2", "row_no": 3},
        {"name": "I3", "stable_line_key": "LINE-3", "material_code": "SKU3", "row_no": 4,
         "extra_json": json.dumps({"shipment_valuation": {"error": "SETTLEMENT_SHIPMENT_VALUE_CONFLICT"}})},
    ]
    _prepare_automatic_snapshot(repo)
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: (
        _ for _ in ()).throw(ValueError("当前货值缺失或失效。")))

    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["missing_value_count"] == 3 and result["status"] == "NEEDS_MATERIALS"
    purchase = result["required_documents"][0]
    assert purchase["key"] == "purchase" and purchase["source_coverage"] == "not_verified"
    assert [(row["material_code"], row["row_no"], row["item_key"]) for row in purchase["blockers"]] == [
        ("SKU1", 2, "LINE-1"), ("SKU2", 3, "LINE-2")]
    conflict = next(row for row in result["actions"] if row["code"] == "SETTLEMENT_SHIPMENT_VALUE_CONFLICT")
    assert conflict["material_code"] == "SKU3" and conflict["row_no"] == 4
    assert all(row["hint"] == "当前货值缺失或失效。" for row in result["blockers"])


def test_non_document_native_reasons_remain_actions_instead_of_missing_files(runtime, monkeypatch):
    repo, _ = runtime
    _prepare_automatic_snapshot(repo)
    codes = ["SETTLEMENT_PURCHASE_CURRENCY_OR_FX_REQUIRED", "DUPLICATE_LOGICAL_FEE",
             "SETTLEMENT_EXPENSE_PRICE_AMBIGUOUS", "PURCHASE_VALUE_UOM_MISMATCH", "UNKNOWN_NATIVE_REASON"]
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: {
        "ok": True, "saved": True, "summary": {"is_complete": False},
        "incomplete_reasons": [{"reason_code": code, "message": code} for code in codes]})

    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["status"] == "REVIEW_REQUIRED" and result["saved"]
    assert result["required_documents"] == []
    assert [row["code"] for row in result["actions"]] == codes
    assert result["actions"] == result["blockers"]


def test_linked_coverage_is_saved_before_enqueue_and_never_overwrites_worker_draft(runtime, monkeypatch):
    repo, _ = runtime
    coverage = {"status": "PARTIAL", "linked_count": 1, "hydrated_count": 0, "blockers": [{
        "code": "LINKED_APPROVAL_ARCHIVE_MISSING", "hint": "A linked approval is not available in the upstream archive."}]}
    monkeypatch.setattr(service.linked_sources, "hydrate_linked_sources", lambda *args, **kwargs: coverage)
    queued = []

    def enqueue(run_id):
        draft = service.ai._load_json(repo.run["draft_json"], {})
        assert draft["review_input"]["source_coverage"] == coverage
        draft["worker_started"] = True
        repo.run["draft_json"] = draft
        queued.append(run_id)

    monkeypatch.setattr(service.ai, "_default_enqueue", enqueue)
    result = service.analyze("B1", "coverage-request-1", trigger_mode="LOCAL_UPLOAD", force=True)
    assert queued == [result["run_id"]] and repo.run["draft_json"]["worker_started"]
    assert result["automatic_processing"]["source_coverage"] == coverage
    status = service.ai.get_material_ai_fill_status("B1", result["run_id"], repository=repo)
    assert status["automatic_processing"]["source_coverage"] == coverage


@pytest.mark.parametrize("coverage_status", ["COMPLETE", "PARTIAL"])
def test_archive_coverage_is_separate_from_user_document_gaps(runtime, monkeypatch, coverage_status):
    repo, _ = runtime
    _prepare_automatic_snapshot(repo)
    coverage = {"status": coverage_status, "linked_count": 1, "hydrated_count": 0, "blockers": []}
    if coverage_status == "PARTIAL":
        coverage["blockers"] = [{"code": "LINKED_ATTACHMENT_ARCHIVE_PENDING", "file_name": "purchase.xlsx",
                                 "hint": "A linked attachment has not been archived upstream."}]
    repo.run["draft_json"]["review_input"] = {"source_coverage": coverage}
    monkeypatch.setattr(cost_preview_service, "calculate_comprehensive_cost", lambda *args, **kwargs: (
        _ for _ in ()).throw(ValueError("当前货值缺失或失效。")))

    result = service.continue_automatic_review("B1", repo.run["name"])
    assert result["source_coverage"] == coverage
    if coverage_status == "PARTIAL":
        assert result["status"] == "REVIEW_REQUIRED" and result["required_documents"] == []
        assert result["actions"] == result["blockers"]
        assert not any(row["code"] == "LINKED_ATTACHMENT_ARCHIVE_PENDING" for row in result["actions"])
    else:
        assert result["status"] == "NEEDS_MATERIALS"
        assert result["required_documents"][0]["source_coverage"] == "COMPLETE"


@pytest.mark.parametrize("injected_repository", [False, True])
def test_upload_during_running_review_requeues_new_source_and_retains_archive_coverage(runtime, monkeypatch, injected_repository):
    repo, _ = runtime
    old = repo.run
    old.update(status="RUNNING", trigger_mode="LOCAL_UPLOAD")
    manifest = service.ai.prepare_source_manifest(repo.sources)
    old["source_manifest_json"] = manifest
    old["input_fingerprint"] = service.ai._source_review_fingerprint(
        "B1", "V1", repo.items, manifest, "", context=repo.context)
    coverage = {"status": "PARTIAL", "linked_count": 1, "hydrated_count": 0, "blockers": []}
    old["draft_json"]["review_input"] = {"source_coverage": coverage}
    repo.sources.append({"source_id": "UPLOADED-LATER", "source_kind": "manual_attachment",
                         "source_hash": "NEW-HASH", "available": True, "file_name": "new-invoice.png"})
    queued, hydrated = [], []
    refreshed = {"status": "COMPLETE", "linked_count": 1, "hydrated_count": 1, "blockers": []}

    def enqueue(run_id):
        pending = service.ai.get_source_ai_review_status("B1", old["name"], repository=repo)
        assert pending["status"] == "RUNNING"
        assert pending["automatic_processing"]["replacement_pending"]
        queued.append(run_id)

    monkeypatch.setattr(service.ai, "_default_enqueue", enqueue)
    monkeypatch.setattr(service.linked_sources, "hydrate_linked_sources", lambda *args, **kwargs: (
        hydrated.append(args) or refreshed))

    result = service.ai.execute_material_ai_fill(old["name"], **({"repository": repo} if injected_repository else {}))
    assert result["status"] == "STALE" and old["status"] == "STALE"
    replacement = repo.get_run(result["replacement_run_id"])
    assert replacement["name"] != old["name"] and queued == [replacement["name"]]
    assert replacement["trigger_mode"] == "LOCAL_UPLOAD"
    sources = service.ai._load_json(replacement["source_manifest_json"], [])
    assert next(row for row in sources if row["source_id"] == "UPLOADED-LATER")["selected"]
    saved = service.ai._load_json(replacement["draft_json"], {})["review_input"]["source_coverage"]
    assert saved == (coverage if injected_repository else refreshed)
    assert len(hydrated) == (0 if injected_repository else 1)
    status = service.ai.get_source_ai_review_status("B1", old["name"], repository=repo)
    assert status["replacement_run_id"] == replacement["name"]
    assert old["draft_json"]["replacement_run_id"] == replacement["name"]
    assert not old["draft_json"]["automatic_processing"].get("replacement_pending")
    assert not repo.writes


def _prepare_automatic_snapshot(repo):
    repo.run.update(trigger_mode="LOCAL_UPLOAD", candidates_json=[])
    repo.run["input_fingerprint"] = service.ai._source_review_fingerprint(
        "B1", "V1", repo.items, repo.sources, "", context=repo.context)
    repo.run["draft_json"]["material_input_fingerprint"] = service.selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    repo.commit = lambda: None
