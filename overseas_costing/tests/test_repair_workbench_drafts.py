"""Exercise native intake planning against committed SQLite source/ledger rows."""

from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from overseas_costing.scripts import repair_workbench_drafts as repair
from overseas_costing.services.logistics_settlement.model import digest, dumps
from overseas_costing.services.logistics_settlement.store import Store
from overseas_costing.tests.test_logistics_initial_intake import source
from overseas_costing.tests.test_settlement_writer import Ledger

DATES = {"start_date": "2026-01-01", "end_date": "2026-11-20"}


class SQLiteRepository(repair.FrappeRepairRepository):
    """Replace Frappe boundaries; keep real source resolution and intake writes."""

    def __init__(self, db, ledger):
        self.db, self.ledger = db, ledger
        self.erp_batches, self.failed_fees = set(), set()
        self.fee_calls, self.commits = [], 0

    def assert_operator(self):
        pass

    def candidates(self, start_date, end_date):
        return [row["name"] for row in self.ledger.rows("batch")]

    def has_erp_activity(self, batch):
        return batch in self.erp_batches

    def fee_rows(self, batch, version):
        return sorted(self.ledger.rows("rule", batch=batch, version=version), key=lambda row: row["name"])

    def backfill_fees(self, batch, version):
        self.fee_calls.append(batch)
        if self.fee_rows(batch, version):
            return {"ok": True, "action": "unchanged", "created_count": 0, "updated_count": 0}
        self.ledger.create("rule", {"batch": batch, "version": version, "amount": "42.50"})
        return {"ok": batch not in self.failed_fees, "action": "created", "created_count": 1,
                "updated_count": 0, "source_value": "must-not-be-exported"}

    def audit(self, result):
        self.db.audit(result["source_id"], repair.REPAIR_KEY, "test-operator", **result)

    def commit(self):
        self.commits += 1
        self.db.commit()

    def rollback(self):
        self.db.db.rollback()


@pytest.fixture
def draft(monkeypatch):
    from overseas_costing.services.logistics_settlement import initial_draft_refresh

    monkeypatch.setattr(initial_draft_refresh, "load_active_suppliers", lambda: [])
    db = Store.sqlite(sqlite3.connect(":memory:"))
    db.install()
    ledger = Ledger(db)
    approval = db.ingest(source())
    batch = ledger.create("batch", {
        "source_type": "oa_logistics", "source_corp_id": approval["corp"],
        "source_instance_id": approval["instance"], "source_created_at": "2026-04-01 00:00:00",
        "status": "Draft", "confirm_status": "Pending", "writeback_status": "Not Started",
        "modified": "2026-04-02 00:00:00",
    })
    version = ledger.create("version", {"batch": batch["name"], "status": "Active", "is_current": 1,
                                         "version_code": "初始录入"})
    ledger.put("batch", batch["name"], {"current_version": version["name"]})
    item = ledger.create("item", {
        "batch": batch["name"], "version": version["name"], "source_type": "oa_logistics",
        "material_code": "FIXTURE1", "quantity": 2, "unit": "套", "goods_value": 0,
        "extra_json": dumps({"initial_logistics_line": approval["goods"][0]["line_key"], "keep": "original"}),
    })
    db.insert("batch_map", {"id": approval["id"], "source_id": approval["id"], "batch": batch["name"], "data": "{}"})
    db.commit()
    return SQLiteRepository(db, ledger), batch, version, item


def test_preview_rolls_back_native_writes_then_apply_fills_once_without_confirmation(draft):
    repo, batch, version, item = draft
    preview = repair.run(**DATES, repository=repo)
    assert preview["ok"] and preview["dry_run"]
    assert preview["item_update_count"] == preview["fee_create_count"] == 1
    assert repo.ledger.get("item", item["name"])["goods_value"] == 0
    assert not repo.ledger.rows("rule") and repo.db.count("audit") == 0 and repo.commits == 0
    assert "must-not-be-exported" not in json.dumps(preview)
    assert "2437.50" not in json.dumps(preview) and "测试夹具" not in json.dumps(preview)

    applied = repair.run(**DATES, apply=True, manifest=preview["manifest"], repository=repo)
    assert applied["status_counts"] == {"applied": 1} and repo.commits == 1
    saved = repo.ledger.get("item", item["name"])
    assert saved["goods_value"] == "2437.50" and saved["quantity"] == 2 and saved["unit"] == "套"
    assert json.loads(saved["extra_json"])["keep"] == "original"
    assert len(repo.ledger.rows("rule")) == 1
    assert repo.ledger.get("batch", batch["name"])["confirm_status"] == "Pending"
    assert repo.ledger.get("version", version["name"]) == version
    assert repo.db.count("audit", action="initial_logistics_draft_synced") == 1
    assert repo.db.count("audit", action=repair.REPAIR_KEY) == 1
    repeated = repair.run(**DATES, repository=repo)
    assert repeated["status_counts"] == {"unchanged": 1} and not repeated["manifest"]
    assert repo.commits == 1 and repo.db.count("audit") == 2


@pytest.mark.parametrize("target,change,code", [
    ("batch", {"confirm_status": "Confirmed"}, "PROTECTED_REVIEW_STATE"),
    ("batch", {"confirm_status": "PartiallyConfirmed"}, "PROTECTED_REVIEW_STATE"),
    ("batch", {"is_locked": 1}, "PROTECTED_REVIEW_STATE"),
    ("batch", {"docstatus": 1}, "PROTECTED_REVIEW_STATE"),
    ("batch", {"writeback_status": "Success"}, "PROTECTED_WRITEBACK_STATE"),
    ("batch", {"writeback_status": "Failed"}, "PROTECTED_WRITEBACK_STATE"),
    ("batch", {"edit_lock_expires_at": (datetime.now() + timedelta(hours=1)).isoformat()}, "EDITING"),
    ("version", {"status": "Archived"}, "PROTECTED_REVIEW_STATE"),
    ("version", {"is_current": 0}, "PROTECTED_REVIEW_STATE"),
])
def test_protected_batches_never_invoke_fee_writer(draft, target, change, code):
    repo, batch, version, item = draft
    repo.ledger.put(target, batch["name"] if target == "batch" else version["name"], change)
    repo.db.commit()
    result = repair.run(**DATES, repository=repo)
    assert result["results"][0]["issue_codes"] == [code]
    assert not repo.fee_calls and not result["manifest"]
    assert repo.ledger.get("item", item["name"])["goods_value"] == 0


def test_prior_erp_attempt_protects_even_inconsistent_batch_flags(draft):
    repo, batch, _, _ = draft
    repo.erp_batches.add(batch["name"])
    result = repair.run(**DATES, repository=repo)
    assert result["results"][0]["issue_codes"] == ["ERP_ACTIVITY"] and not repo.fee_calls


@pytest.mark.parametrize("change", ["source", "item", "fee"])
def test_manifest_rejects_changes_after_preview_without_any_writes(draft, change):
    repo, batch, version, item = draft
    preview = repair.run(**DATES, repository=repo)
    if change == "source":
        original = repo.db.find("source")[0]
        repo.db.put("source", {"id": original["id"], "snapshot": "changed-source-snapshot"})
    elif change == "item":
        repo.ledger.put("item", item["name"], {"quantity": 3})
    else:
        repo.ledger.create("rule", {"batch": batch["name"], "version": version["name"], "amount": "0"})
    repo.db.commit()
    calls = len(repo.fee_calls)
    result = repair.run(**DATES, apply=True, manifest=preview["manifest"], repository=repo)
    assert "SOURCE_CHANGED" in result["results"][0]["issue_codes"]
    assert len(repo.fee_calls) == calls and repo.commits == 0 and repo.db.count("audit") == 0
    assert repo.ledger.get("item", item["name"])["goods_value"] == 0


def test_failed_fees_roll_back_native_items_and_partial_fee_rows(draft):
    repo, batch, _, item = draft
    preview = repair.run(**DATES, repository=repo)
    repo.failed_fees.add(batch["name"])
    result = repair.run(**DATES, apply=True, manifest=preview["manifest"], repository=repo)
    assert not result["ok"] and result["status_counts"] == {"failed": 1}
    assert repo.ledger.get("item", item["name"])["goods_value"] == 0
    assert not repo.ledger.rows("rule") and repo.db.count("audit") == 0 and repo.commits == 0


def test_one_failed_batch_does_not_prevent_another_batch_from_committing(draft):
    repo, original, version, item = draft
    approval = source()
    approval.update(id=digest("second-source"), instance="SECOND-APPROVAL", fingerprint=digest("second-snapshot"))
    approval = repo.db.ingest(approval)
    other = repo.ledger.create("batch", {key: value for key, value in original.items() if key != "name"})
    repo.ledger.put("batch", other["name"], {"source_instance_id": approval["instance"]})
    other_version = repo.ledger.create("version", {**version, "batch": other["name"]})
    repo.ledger.put("batch", other["name"], {"current_version": other_version["name"]})
    repo.ledger.create("item", {**item, "batch": other["name"], "version": other_version["name"]})
    repo.db.insert("batch_map", {"id": approval["id"], "source_id": approval["id"],
                                "batch": other["name"], "data": "{}"})
    repo.db.commit()
    preview = repair.run(**DATES, repository=repo)
    first, second = sorted(preview["manifest"])
    repo.failed_fees.add(first)
    result = repair.run(**DATES, apply=True, manifest=preview["manifest"], repository=repo)
    assert [(row["batch"], row["status"]) for row in result["results"]] == [(first, "failed"), (second, "applied")]
    assert repo.ledger.rows("item", batch=first)[0]["goods_value"] == 0
    assert repo.ledger.rows("item", batch=second)[0]["goods_value"] == "2437.50"
    assert not repo.ledger.rows("rule", batch=first) and len(repo.ledger.rows("rule", batch=second)) == 1
    assert repo.commits == 1 and repo.db.count("audit", action=repair.REPAIR_KEY) == 1


def test_imported_batches_can_fill_absent_fees_but_keep_native_item_protection(draft):
    repo, batch, _, item = draft
    repo.ledger.put("batch", batch["name"], {"status": "Imported"})
    repo.db.commit()
    preview = repair.run(**DATES, repository=repo)
    assert preview["item_update_count"] == 0 and preview["fee_create_count"] == 1
    assert "ITEM_PROTECTED_DRAFT" in preview["results"][0]["issue_codes"]
    result = repair.run(**DATES, apply=True, manifest=preview["manifest"], repository=repo)
    assert result["status_counts"] == {"applied": 1}
    assert repo.ledger.get("item", item["name"])["goods_value"] == 0


def test_apply_scope_cannot_add_a_batch_absent_from_current_pending_population(draft):
    repo, batch, _, _ = draft
    preview = repair.run(**DATES, repository=repo)
    result = repair.run(**DATES, apply=True, batch_names=["not-a-pending-batch"],
                        manifest=preview["manifest"], repository=repo)
    assert result["candidate_count"] == 0 and not repo.fee_calls[1:] and repo.commits == 0


def test_repository_uses_complete_native_pending_classifier(monkeypatch):
    from overseas_costing.services import workbench_service

    calls = []

    def classified(filters):
        calls.append(filters)
        return [{"name": f"B{index}", "review_state": "processing"} for index in range(2501)] + [
            {"name": "confirmed", "review_state": "confirmed"}]

    monkeypatch.setattr(workbench_service, "_classified_batches", classified)
    repo = object.__new__(repair.FrappeRepairRepository)
    assert len(repo.candidates(**DATES)) == 2501
    assert calls == [DATES]


@pytest.mark.parametrize("arguments", [
    {"apply": "false"}, {"apply": True}, {"start_date": "20261120"},
    {"start_date": "2026-11-21"}, {"batch_names": "not-json"}, {"batch_names": [1]},
])
def test_invalid_command_arguments_fail_before_opening_repository(arguments):
    with pytest.raises(ValueError):
        repair.run(**{**DATES, **arguments})
