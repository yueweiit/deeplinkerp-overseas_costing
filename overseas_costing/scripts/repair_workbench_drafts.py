"""Fill deterministic gaps in pending workbench drafts, without costing or ERP.

Run in a fresh administrator command: this function owns each batch transaction.
Dry-run invokes the native fee writer and rolls its transaction back, because
that writer has no separate preview API. Apply requires the returned manifest.
Only identifiers, counts and issue codes leave this module; no source export or
backup is created.
"""

from collections import Counter
from datetime import date
import json

from overseas_costing.services.edit_session_service import lock_is_active
from overseas_costing.services.effective_logistics_source import load_source_bundle
from overseas_costing.services.logistics_settlement.initial_draft_refresh import sync_initial_draft_items
from overseas_costing.services.logistics_settlement.model import digest, dumps

REPAIR_KEY = "workbench-deterministic-gaps-1"


def run(*, start_date, end_date, apply=False, batch_names=None, manifest=None, repository=None):
    """Preview the complete date range; apply only matching, still-pending IDs.

    Dates filter source_created_at through the existing workbench classifier.
    batch_names optionally limits a retry/chunk to IDs within that same scope.
    A failed batch rolls back independently and does not undo earlier commits.
    """
    batch_names, manifest = _arguments(start_date, end_date, apply, batch_names, manifest)
    repo = repository or FrappeRepairRepository()
    repo.assert_operator()
    names = set(repo.candidates(start_date, end_date))
    if batch_names is not None:
        names &= set(batch_names)
    if apply:
        names &= set(manifest)
    # End the discovery read snapshot before acquiring each batch's write locks.
    repo.rollback()
    results, output_manifest = [], {}
    for name in sorted(names):
        try:
            result = _repair_batch(repo, name, apply, manifest.get(name))
            if result["status"] == "ready":
                output_manifest[name] = result["input_hash"]
            if result["status"] == "applied":
                repo.commit()
            else:
                repo.rollback()
        except Exception as exc:
            repo.rollback()
            result = {"batch": name, "status": "failed", "issue_codes": ["TRANSACTION_FAILED"],
                      "error_type": type(exc).__name__, "item_update_count": 0, "fee_create_count": 0}
        results.append(result)
    counts = Counter(row["status"] for row in results)
    return {"ok": not counts["failed"], "repair": REPAIR_KEY, "dry_run": not apply,
            "candidate_count": len(names), "status_counts": dict(counts),
            "item_update_count": sum(row["item_update_count"] for row in results),
            "fee_create_count": sum(row["fee_create_count"] for row in results),
            "manifest": output_manifest, "results": results}


def _repair_batch(repo, name, apply, expected_hash):
    bundle = repo.load(name)
    batch, version = bundle.get("batch") or {}, bundle.get("version") or {}
    source, context = bundle.get("source") or {}, bundle.get("context") or {}
    source_id = context.get("root_source_id") or ""
    result = {"batch": name, "version": version.get("name") or "", "source_id": source_id,
              "status": "skipped", "issue_codes": [], "item_update_count": 0, "fee_create_count": 0}
    issue = _protected_reason(batch, version)
    if not issue and repo.has_erp_activity(name):
        issue = "ERP_ACTIVITY"
    if issue:
        return {**result, "issue_codes": [issue]}

    plan = sync_initial_draft_items(repo.db, repo.ledger, name, source_id, dry_run=True)
    issues = {row["code"] for row in plan.get("issues") or [] if row.get("code")}
    issues.update(row["code"] for row in plan.get("item_results") or []
                  if row.get("code") and row["code"] != "OA_INITIAL_ITEM_READY")
    if plan["status"] not in {"ready", "unchanged"}:
        issues.add("ITEM_" + plan["status"].upper())
    input_hash = digest(REPAIR_KEY, batch.get("modified"), version, source_id, source.get("snapshot"),
                        context.get("fingerprint"), plan.get("plan_hash"), repo.fee_rows(name, version["name"]))
    result.update(input_hash=input_hash, issue_codes=sorted(issues))
    if apply and expected_hash != input_hash:
        return {**result, "issue_codes": sorted(issues | {"SOURCE_CHANGED"})}

    if apply and plan.get("update_count"):
        sync_initial_draft_items(repo.db, repo.ledger, name, source_id,
                                 expected_plan_hash=plan["plan_hash"])
    fees = repo.backfill_fees(name, version["name"])
    if not fees.get("ok") or fees.get("updated_count", 0):
        raise RuntimeError("Native missing-fee repair failed its fill-only contract")
    if fees.get("action") == "skipped":
        issues.add("FEES_SKIPPED")
    result.update(item_update_count=plan.get("update_count", 0),
                  fee_create_count=fees.get("created_count", 0), issue_codes=sorted(issues))
    changed = bool(result["item_update_count"] or result["fee_create_count"])
    result["status"] = ("applied" if apply else "ready") if changed else "unchanged"
    if apply and changed:
        repo.audit(result)
    return result


def _protected_reason(batch, version):
    if (not batch.get("name") or not version.get("name")
            or batch.get("current_version") != version["name"] or version.get("batch") != batch["name"]):
        return "CURRENT_VERSION_MISSING"
    if (batch.get("docstatus") or batch.get("is_locked") or batch.get("confirm_status") != "Pending"
            or batch.get("status") in {"Confirmed", "Written Back"}
            or version.get("status") != "Active" or not version.get("is_current")):
        return "PROTECTED_REVIEW_STATE"
    if batch.get("writeback_status") != "Not Started":
        return "PROTECTED_WRITEBACK_STATE"
    if lock_is_active(batch):
        return "EDITING"
    return ""


def _arguments(start_date, end_date, apply, batch_names, manifest):
    if (not isinstance(start_date, str) or not isinstance(end_date, str)
            or date.fromisoformat(start_date).isoformat() != start_date
            or date.fromisoformat(end_date).isoformat() != end_date or start_date > end_date):
        raise ValueError("Expected an ordered ISO date range")
    if not isinstance(apply, bool):
        raise ValueError("apply must be a boolean")
    if isinstance(batch_names, str):
        batch_names = json.loads(batch_names)
    if batch_names is not None and (not isinstance(batch_names, list)
                                   or any(not isinstance(name, str) or not name for name in batch_names)):
        raise ValueError("batch_names must be a list of IDs")
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    if manifest is None:
        manifest = {}
    if not isinstance(manifest, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                             for key, value in manifest.items()):
        raise ValueError("manifest must map batch IDs to input hashes")
    if apply and not manifest:
        raise ValueError("Apply requires the dry-run manifest")
    return batch_names, manifest


class FrappeRepairRepository:
    def __init__(self):
        import frappe
        from overseas_costing.services.logistics_settlement.runtime import store
        from overseas_costing.services.logistics_settlement.ledger import FrappeLedger

        self.frappe, self.db, self.ledger = frappe, store(), FrappeLedger()

    def assert_operator(self):
        if self.frappe.session.user == "Guest" or "System Manager" not in self.frappe.get_roles():
            raise PermissionError("Only a System Manager may run this administrator repair")

    def candidates(self, start_date, end_date):
        from overseas_costing.services.workbench_service import _classified_batches, filter_batches_for_task

        rows = _classified_batches({"start_date": start_date, "end_date": end_date})
        return [row["name"] for row in filter_batches_for_task(rows, "pending")]

    def load(self, name):
        return load_source_bundle(name, store=self.db, ledger=self.ledger, lock=True)

    def fee_rows(self, batch, version):
        return self.frappe.db.sql(
            "SELECT * FROM `tabOverseas Cost Allocation Rule` "
            "WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE", (batch, version), as_dict=True)

    def has_erp_activity(self, batch):
        from overseas_costing.services.erp_sync_ledger_service import DOCTYPE, LINK_DOCTYPE

        requests = self.frappe.db.sql(
            f"SELECT status, attempt_count FROM `tab{DOCTYPE}` WHERE batch=%s FOR UPDATE", (batch,), as_dict=True)
        links = self.frappe.db.sql(f"SELECT name FROM `tab{LINK_DOCTYPE}` WHERE batch=%s FOR UPDATE", (batch,))
        return bool(links or any(row.get("attempt_count") or row.get("status") in {
            "RUNNING", "SUCCESS", "UNCERTAIN", "MANUAL_REQUIRED"} for row in requests))

    def backfill_fees(self, batch, version):
        from overseas_costing.services.oa_fee_backfill_service import backfill_missing_oa_fees

        return backfill_missing_oa_fees(batch, version)

    def audit(self, result):
        from overseas_costing.services.calculate_service import _insert_audit_log

        _insert_audit_log(batch_doc_name=result["batch"], version_name=result["version"], action_type="BATCH_EDIT",
                          field_name=REPAIR_KEY, new_value=dumps(result),
                          action_remark="Fill verified missing intake values and absent OA fee rows; no cost confirmation.")

    def commit(self):
        self.frappe.db.commit()

    def rollback(self):
        self.frappe.db.rollback()
