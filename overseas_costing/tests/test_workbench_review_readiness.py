"""The real workbench service classifies trusted, permission-filtered bulk rows."""

from copy import deepcopy
import json
from math import ceil

import pytest

from overseas_costing.services import batch_service, workbench_service
from overseas_costing.tests.test_cost_review_service import saved_context, save_result


def batch_context(name, *, dirty=False, confirmed=False, estimated=False):
    context = saved_context()
    context["batch"].update(name=name, current_version=f"V-{name}")
    context["version"].update(name=f"V-{name}", batch=name)
    for row in context["items"]:
        row.update(name=f"I-{name}", batch=name, version=f"V-{name}", is_excluded=0)
    for index, row in enumerate(context["fees"]):
        row.update(name=f"F-{name}-{index}", batch=name, version=f"V-{name}")
    if estimated:
        context["fees"][0]["amount_status"] = "ESTIMATED"
    save_result(context)
    if dirty:
        context["batch"]["status"] = "Dirty"
    if confirmed:
        context["batch"].update(confirm_status="Confirmed", status="Confirmed")
        context["version"]["status"] = "Confirmed"
    return context


class ReadOnlyRows:
    def __init__(self, contexts):
        self.calls = []
        self.tables = {
            "Overseas Cost Version": [context["version"] for context in contexts],
            "Overseas Cost Item": [row for context in contexts for row in context["items"]],
            "Overseas Cost Allocation Rule": [row for context in contexts for row in context["fees"]],
            "Overseas Cost Fee Evidence": [],
            "Overseas Cost Fee SKU Component": [
                row for context in contexts for row in context.get("fee_components", [])
            ],
            "Overseas Cost Review Round": [],
            "Overseas Cost Review Issue": [],
            "Overseas Cost Audit Log": [
                {"batch": context["batch"]["name"], "version": context["version"]["name"],
                 "action_type": "BATCH_EDIT", "field_name": "confirm_status", "creation": "2026-09-08 11:00:00",
                 "new_value": json.dumps({"confirm_status": "Confirmed"})}
                for context in contexts if context["batch"]["confirm_status"] == "Confirmed"
            ],
        }

    def get_all(self, doctype, **kwargs):
        self.calls.append((doctype, deepcopy(kwargs)))
        rows = self.tables[doctype]
        for key, value in kwargs.get("filters", {}).items():
            if isinstance(value, list) and value[0] == "in":
                rows = [row for row in rows if row.get(key) in value[1]]
            else:
                rows = [row for row in rows if row.get(key) == value]
        return [{field: row.get(field) for field in kwargs["fields"]} for row in rows]


def install_rows(monkeypatch, contexts, *, allowed=None):
    database = ReadOnlyRows(contexts)
    monkeypatch.setattr(workbench_service, "frappe", database)
    selected = [context for context in contexts if allowed is None or context["batch"]["name"] in allowed]
    monkeypatch.setattr(batch_service, "get_batch_list", lambda filters: {"items": [deepcopy(context["batch"]) for context in selected]})
    return database


def test_pending_processing_and_default_cost_ready_are_mutually_exclusive(monkeypatch):
    contexts = [batch_context("READY", estimated=True), batch_context("DIRTY", dirty=True),
                batch_context("CONFIRMED", confirmed=True), batch_context("LEGACY")]
    contexts[0]["batch"]["confirm_status"] = "Partially Confirmed"
    contexts[-1]["version"]["summary_snapshot_json"] = "{}"
    install_rows(monkeypatch, contexts)
    pending = workbench_service.get_workbench_batches(task="pending")
    review = workbench_service.get_workbench_batches(task="cost")
    history = workbench_service.get_workbench_batches({"review_status": "confirmed"}, task="cost")
    assert [row["name"] for row in pending["items"]] == ["LEGACY"]
    assert [row["name"] for row in review["items"]] == ["READY", "DIRTY"]
    assert [row["name"] for row in history["items"]] == ["CONFIRMED"]
    assert history["items"][0]["reviewed_at"] == "2026-09-08 11:00:00"
    assert history["items"][0]["reviewed_version"] == "V-CONFIRMED"


def test_cost_filters_ignore_pending_issue_and_use_review_warning(monkeypatch):
    contexts = [batch_context("ESTIMATED", estimated=True), batch_context("ACTUAL")]
    install_rows(monkeypatch, contexts)
    result = workbench_service.get_workbench_batches({"issue": "purchase", "review_warning": "estimated"}, task="cost")
    assert [row["name"] for row in result["items"]] == ["ESTIMATED"]


def test_summary_counts_base_population_and_scopes_warnings_to_selected_review_status(monkeypatch):
    contexts = [batch_context("READY", estimated=True), batch_context("ACTUAL"),
                batch_context("DIRTY", dirty=True), batch_context("CONFIRMED", confirmed=True)]
    install_rows(monkeypatch, contexts)
    pending = workbench_service.get_workbench_summary({"issue": "purchase"})
    assert pending["counts"] == {"purchase": 0, "logistics": 0, "calculation": 0, "erp_failed": 0}
    assert pending["review_counts"] == {"pending": 2, "confirmed": 1, "estimated": 1, "evidence_missing": 3}
    history = workbench_service.get_workbench_summary({"review_status": "confirmed", "review_warning": "estimated"}, task="cost")
    assert history["review_counts"] == {"pending": 2, "confirmed": 1, "estimated": 0, "evidence_missing": 1}


@pytest.mark.parametrize("batch_count", [1, 30, 401])
def test_readiness_queries_are_bounded_by_chunks_and_only_authorized_batch_ids(monkeypatch, batch_count):
    allowed = [f"B-{index}" for index in range(batch_count)]
    contexts = [batch_context(name) for name in allowed] + [batch_context("SECRET")]
    database = install_rows(monkeypatch, contexts, allowed=allowed)
    result = workbench_service.get_workbench_batches(task="cost", page=1, page_length=10)
    assert result["total"] == batch_count
    assert len(result["items"]) == min(batch_count, 10)
    assert "SECRET" not in json.dumps(result)
    assert len(database.calls) <= 7 * ceil(batch_count / 200)
    queried = {doctype for doctype, _args in database.calls}
    assert {"Overseas Cost Version", "Overseas Cost Item", "Overseas Cost Allocation Rule",
            "Overseas Cost Fee Evidence", "Overseas Cost Fee SKU Component",
            "Overseas Cost Review Round"} <= queried
    for _doctype, kwargs in database.calls:
        selected_ids = kwargs["filters"]["batch"][1]
        assert set(selected_ids) <= set(allowed)
        assert len(selected_ids) <= 200
        if _doctype == "Overseas Cost Item":
            assert kwargs["filters"]["is_excluded"] == 0
        assert kwargs["limit_page_length"] == 0


def test_wrong_batch_version_pair_cannot_supply_readiness(monkeypatch):
    context = batch_context("VISIBLE")
    context["version"]["batch"] = "SECRET"
    database = install_rows(monkeypatch, [context])
    assert workbench_service.get_workbench_batches(task="cost")["total"] == 0
    pending = workbench_service.get_workbench_batches(task="pending")
    assert pending["items"][0]["result_is_current"] is False
    assert pending["items"][0]["shipment_goods_value_rmb"] is None
    assert all("SECRET" not in str(kwargs["filters"]) for _doctype, kwargs in database.calls)


def test_batch_list_uses_current_shipment_values_before_trial(monkeypatch):
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    applied, missing, free, invalid = [batch_context(name) for name in ("APPLIED", "MISSING", "FREE", "INVALID")]
    for context in (applied, missing, free, invalid):
        context["batch"].update(status="Dirty", total_goods_value=0, estimated_total_cost_rmb=0)
        context["version"].update(calculated_at=None, summary_snapshot_json="{}")
    missing["items"][0].update(goods_value=0, extra_json="{}")
    missing["items"].append({
        **deepcopy(applied["items"][0]),
        "batch": "MISSING", "version": "V-MISSING", "name": "OTHER-MATERIAL",
        "stable_line_key": "line-2", "row_no": 2,
    })
    free_item = free["items"][0]
    free_item["goods_value"] = 0
    free_item["extra_json"] = json.dumps({
        "manual_shipment_valuation": build_manual_shipment_valuation(
            free_item, "0", actor="tester@example.com", reason="确认免费样品",
        )
    })
    invalid["batch"]["source_status"] = {"purchase_approval_sync_state": "invalid"}
    install_rows(monkeypatch, [applied, missing, free, invalid])

    rows = {row["name"]: row for row in workbench_service.get_workbench_batches(task="pending")["items"]}

    assert {name: row["shipment_goods_value_rmb"] for name, row in rows.items()} == {
        "APPLIED": "100.00", "MISSING": None, "FREE": "0.00", "INVALID": None,
    }
    assert all(row["result_is_current"] is False for row in rows.values())


def test_empty_permission_population_does_not_query_cost_tables(monkeypatch):
    database = install_rows(monkeypatch, [], allowed=[])
    assert workbench_service.get_workbench_batches(task="cost")["items"] == []
    assert database.calls == []


def test_invalid_main_approval_is_hidden_before_trusted_bulk_loading(monkeypatch):
    context = batch_context("INVALID")
    context["batch"]["source_status"] = {"invalid_business": True, "invalid_business_scope": "source_approval"}
    database = install_rows(monkeypatch, [context])
    assert workbench_service.get_workbench_batches(task="pending")["items"] == []
    assert database.calls == []


def test_erp_failed_retry_action_is_preserved_without_changing_cost_history_action(monkeypatch):
    context = batch_context("FAILED", confirmed=True)
    context["batch"]["writeback_status"] = "Failed"
    install_rows(monkeypatch, [context])
    assert workbench_service.get_workbench_batches(task="erp")["items"][0]["primary_action"] == "erp_retry"
    assert workbench_service.get_workbench_batches({"review_status": "confirmed"}, task="cost")["items"][0]["primary_action"] == "view"


def test_erp_summary_failure_count_uses_erp_queue_even_for_confirmed_results(monkeypatch):
    context = batch_context("FAILED", confirmed=True)
    context["batch"]["writeback_status"] = "Failed"
    install_rows(monkeypatch, [context])
    assert workbench_service.get_workbench_summary(task="erp")["counts"]["erp_failed"] == 1
    assert workbench_service.get_workbench_summary(task="pending")["counts"]["erp_failed"] == 0
