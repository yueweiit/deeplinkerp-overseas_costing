"""Stage native reviews and safely continue local evidence uploads.

Only explicitly automatic runs adopt recommended gaps and save a trial.
Manual preview/apply callers own transactions; analysis commits its queued run.
"""

from collections import defaultdict

from . import edit_session_service as leases
from . import material_ai_fill_service as ai
from . import material_ai_fee_policy as fee_policy
from . import material_ai_linked_sources as linked_sources
from . import material_ai_selection_service as selections
from .access_control import require_batch_permission
from .material_ai_errors import retry_on_deadlock


def analyze(batch_name, request_id, version_name="", *, trigger_mode="MANUAL", force=False):
    """Queue one eligible batch, preserving native payment-source preflight."""
    batch = require_batch_permission(batch_name, "write")
    repo = ai.FrappeMaterialAIFillRepository()
    context = repo.get_context(batch, version_name or None)
    automatic = trigger_mode in ai.AUTOMATIC_GAP_FILL_TRIGGER_MODES
    running = repo.find_running_run(batch, context["version"]) if force or automatic else None
    coverage = None
    if running:
        started = {"ok": True, "status": ai._record_value(running, "status"),
                   "run_id": ai._record_value(running, "name")}
    else:
        if automatic:
            coverage = linked_sources.hydrate_linked_sources(
                batch, context["version"], source_context=context.get("effective_source") or {})
            repo.commit()
        # Passing a repository here would bypass the production payment preflight.
        started = ai.start_source_ai_review(batch, context["version"], request_id=request_id,
                                           trigger_mode=trigger_mode, force=force,
                                           **({"source_coverage": coverage} if coverage else {}))
    summary = _summary(started, batch, context["version"])
    if automatic:
        processing = {
            "status": summary["status"], "run_id": summary["run_id"], "changed_count": 0,
            "blockers": ([{"code": "PAYMENT_SOURCE_SELECTION_REQUIRED",
                           "hint": "Confirm the matching payment source before continuing."}]
                         if summary["payment_selection_required"] else []),
        }
        if summary["run_id"]:
            selected_run = repo.get_run(summary["run_id"])
            draft = ai._load_json(ai._record_value(selected_run, "draft_json"), {})
            coverage = coverage or (draft.get("review_input") or {}).get("source_coverage")
            existing = draft.get("automatic_processing")
            if ai._record_value(selected_run, "trigger_mode") not in ai.AUTOMATIC_GAP_FILL_TRIGGER_MODES:
                processing.update(status="REVIEW_REQUIRED", blockers=[{
                    "code": "MANUAL_ANALYSIS_PENDING", "hint": "An existing manual analysis needs review before automatic processing."}])
            elif existing:
                processing = existing
            elif summary["status"] not in ai.RUNNING_STATES:
                processing.update(status="REVIEW_REQUIRED", blockers=[{
                    "code": "AUTOMATIC_CONTINUATION_REQUIRED", "hint": "Reanalyze this older draft to continue automatic processing."}])
        summary["automatic_processing"] = _with_guidance({**processing, **({"source_coverage": coverage} if coverage else {})})
    return summary


def preview(batch_name, run_id):
    """Persist a native receipt, without writing material or fee values."""
    batch = require_batch_permission(batch_name, "write")
    return _preview(ai.FrappeMaterialAIFillRepository(), batch, run_id)


def apply(batch_name, run_id, preview_id, preview_revision, *, respect_active_lease=False):
    """Explicitly confirm exactly the currently recommended fill-only receipt."""
    batch = require_batch_permission(batch_name, "write")
    repo = ai.FrappeMaterialAIFillRepository()
    run = repo.get_run(run_id)
    ai._assert_run_batch(run, batch)
    if ai._record_value(run, "status") == "APPLIED":
        result = selections.confirm(batch, run_id, preview_id, preview_revision, "", "", repository=repo)
        return _applied_summary(result, batch, run_id)
    prepared = _preview(repo, batch, run_id)
    if not prepared.get("can_apply"):
        return prepared
    if (prepared["preview_id"], prepared["preview_revision"]) != (preview_id, preview_revision):
        return {**prepared, "ok": False, "status": "PREVIEW_CHANGED", "can_apply": False}
    if respect_active_lease and leases.lock_is_active(leases._lock_row(batch)):
        return {"ok": False, "status": "EDIT_LOCKED", "batch_name": batch, "run_id": run_id}
    lease = leases.acquire_edit_session(batch)
    if not lease.get("ok"):
        return {"ok": False, "status": "EDIT_LOCKED", "batch_name": batch, "run_id": run_id}
    token = lease["edit_token"]
    try:
        result = selections.confirm(batch, run_id, preview_id, preview_revision, token,
                                    prepared["batch_modified"], repository=repo)
        return _applied_summary(result, batch, run_id)
    except Exception:
        repo.rollback()
        raise
    finally:
        leases.release_edit_session(batch, token)


def continue_automatic_review(batch_name, run_id):
    """Resume only automatic runs, retaining progress on the native run."""
    repo = ai.FrappeMaterialAIFillRepository()
    run = repo.get_run(run_id)
    ai._assert_run_batch(run, batch_name)
    if ai._record_value(run, "trigger_mode") not in ai.AUTOMATIC_GAP_FILL_TRIGGER_MODES:
        return {"ok": False, "status": "MANUAL_REVIEW_REQUIRED"}
    previous = ai._load_json(ai._record_value(run, "draft_json"), {}).get("automatic_processing")
    if previous and previous.get("status") not in {"PENDING", "RUNNING"}:
        return previous
    draft = ai._load_json(ai._record_value(run, "draft_json"), {})
    coverage = (draft.get("review_input") or {}).get("source_coverage")
    if draft.get('source_read_issues'):
        coverage = {**(coverage or {}), 'status': 'PARTIAL',
                    'blockers': [*(coverage or {}).get('blockers', []), *draft['source_read_issues']]}
    repo.save_run(run, draft_json={**draft, "automatic_processing": {
        "status": "RUNNING", "run_id": run_id, "changed_count": 0, "blockers": [],
        **({"source_coverage": coverage} if coverage else {}),
    }})
    repo.commit()
    changed_count = 0
    try:
        staged = preview(batch_name, run_id)
        if staged.get("can_apply"):
            staged = apply(batch_name, run_id, staged["preview_id"], staged["preview_revision"], respect_active_lease=True)
            changed_count = staged.get("changed_count", 0) if staged.get("status") == "APPLIED" else 0
            repo.commit()
        if staged["status"] not in {"APPLIED", "NO_RECOMMENDED_GAPS", "NO_APPLICABLE_GAPS"}:
            result = {**staged, "blockers": [{"code": staged["status"],
                      "hint": "Automatic filling requires a current, unambiguous preview."}]}
        else:
            result = retry_on_deadlock(lambda: _calculate_after_upload(batch_name, staged.get("version_name") or ""))
            result["changed_count"] = changed_count
    except Exception as error:
        repo.rollback()
        result = _automatic_failure(error)
    result.update(run_id=run_id)
    result["changed_count"] = changed_count
    result.setdefault("blockers", [])
    if coverage:
        result["source_coverage"] = coverage
    result = _with_guidance(result)
    run = repo.get_run(run_id)
    draft = ai._load_json(ai._record_value(run, "draft_json"), {})
    repo.save_run(run, draft_json={**draft, "automatic_processing": result})
    repo.commit()
    return result


def _calculate_after_upload(batch, version):
    from . import cost_preview_service, material_input_service

    repo = ai.FrappeMaterialAIFillRepository()
    context = repo.get_context(batch, version or None)
    if leases.lock_is_active(leases._lock_row(batch)):
        return {"ok": False, "status": "EDIT_LOCKED", "blockers": [{
            "code": "EDIT_LOCKED", "hint": "The batch is being edited."}]}
    lease = leases.acquire_edit_session(batch)
    if not lease.get("ok"):
        return {"ok": False, "status": "EDIT_LOCKED", "reason": "The batch is being edited."}
    token = lease["edit_token"]
    try:
        result = cost_preview_service.calculate_comprehensive_cost(
            batch, version or None, edit_token=token, expected_modified=lease.get("modified"), trusted=False)
        summary = result.get("summary") or {}
        items = repo.get_items(batch, context["version"])
        blockers = _calculation_blockers(result, items)
        return {"ok": result.get("ok") is True,
                "status": "CALCULATED" if summary.get("is_complete") is True else "NEEDS_MATERIALS",
                "saved": bool(result.get("saved")),
                "is_complete": summary.get("is_complete") is True,
                "missing_fee_count": len(result.get("excluded_fees") or []),
                "blockers": blockers}
    except Exception as error:
        if type(error).__name__ == "QueryDeadlockError":
            raise
        # Native calculation rolls back its own transaction; completed gap fills
        # were committed before entering it and must remain available for review.
        failure = _automatic_failure(error)
        coverage = material_input_service.purchase_value_coverage(repo.get_items(batch, context["version"]))
        failure["missing_value_count"] = coverage["missing_count"]
        missing = [{**row, "item_key": cost_preview_service._item_key(row),
                    "field": "goods_value", "message": failure["reason"]}
                   for row in coverage["missing_items"]]
        if not coverage["item_count"]:
            missing.append({"reason_code": "MATERIAL_ITEMS_REQUIRED", "message": failure["reason"]})
        failure["blockers"] = [_public_blocker(row) for row in missing] + failure["blockers"]
        return failure
    finally:
        leases.release_edit_session(batch, token)


def _automatic_failure(error):
    reason = ai._safe_public_text(str(error)) if isinstance(error, (ValueError, PermissionError)) else "Automatic processing needs review."
    return {"ok": False, "status": "NEEDS_MATERIALS" if isinstance(error, ValueError) else "REVIEW_REQUIRED",
            "reason": reason[:1000], "error_type": type(error).__name__,
            "blockers": [{"code": "REVIEW_REQUIRED", "hint": reason[:1000]}]}


def _calculation_blockers(result, items):
    from .cost_preview_service import _item_key

    by_item = {_item_key(row): row for row in items}
    reasons = result.get("incomplete_reasons") or []
    fee_messages = {(row.get("reason_code"), row.get("fee_key")): row for row in reasons if row.get("fee_key")}
    excluded = result.get("excluded_fees") or []
    excluded_keys = {(row.get("reason_code"), row.get("fee_key")) for row in excluded}
    # Native incomplete reasons carry the explanation; excluded fees carry identity.
    rows = [{**row, **fee_messages.get((row.get("reason_code"), row.get("fee_key")), {})} for row in excluded]
    rows.extend(row for row in reasons if (row.get("reason_code"), row.get("fee_key")) not in excluded_keys)
    blockers = []
    for row in rows:
        item = by_item.get(str(row.get("item_key") or ""), {})
        blocker = _public_blocker({**{key: item.get(key) for key in ("name", "stable_line_key", "material_code", "row_no")}, **row})
        if blocker not in blockers:
            blockers.append(blocker)
    return blockers


def _public_blocker(row):
    blocker = {"code": row.get("reason_code") or row.get("code") or "REVIEW_REQUIRED",
               "hint": ai._safe_public_text(str(row.get("message") or row.get("reason") or row.get("hint") or ""))[:1000]}
    for key in ("item_key", "stable_line_key", "material_code", "row_no", "field",
                "fee_key", "rule_name", "expense_category", "currency", "amount_status"):
        value = row.get(key)
        if value is not None and value != "" and isinstance(value, (str, int, float)):
            blocker[key] = ai._safe_public_text(value)[:300] if isinstance(value, str) else value
    if row.get("name"):
        blocker["item_name"] = ai._safe_public_text(str(row["name"]))[:300]
    return blocker


def _with_guidance(result):
    coverage = result.get("source_coverage") or {}
    if coverage.get("status") == "PARTIAL":
        status = result.get("status") if result.get("status") in {"PENDING", "QUEUED", "RUNNING", "PAYMENT_SELECTION"} else "REVIEW_REQUIRED"
        return {**result, "status": status, "required_documents": [], "actions": result.get("blockers") or []}
    document_reasons = {
        "GOODS_VALUE_MISSING": "purchase", "SETTLEMENT_PURCHASE_PRICE_EVIDENCE_REQUIRED": "purchase",
        "SETTLEMENT_SHIPMENT_VALUE_REQUIRED": "purchase",
        "MATERIAL_ITEMS_REQUIRED": "packing", "SHIPPING_UNIT_REQUIRED": "packing",
        "PROJECT_WEIGHT_REQUIRED": "packing", "PURCHASE_VALUE_QUANTITY_REQUIRED": "packing",
        "AMOUNT_MISSING": "fees", "ESTIMATED_AMOUNT": "fees",
    }
    groups = {
        "purchase": ("Purchase value and evidence", "goods_value",
                     "Provide the purchase amount, currency and matching invoice or payment evidence."),
        "packing": ("Shipping and packing documents", "actual_shipped_qty",
                    "Provide the shipping quantities, units and packing details for the listed rows."),
        "fees": ("Fee evidence", "freight", "Provide the bill or fee evidence for the listed fees."),
    }
    documents, actions = {}, []
    for blocker in result.get("blockers") or []:
        key = document_reasons.get(blocker["code"])
        if not key:
            actions.append(blocker)
            continue
        label, fieldname, hint = groups[key]
        group = documents.setdefault(key, {"key": key, "label": label, "fieldname": fieldname,
            "hint": hint, "source_coverage": coverage.get("status") or "not_verified", "blockers": []})
        group["blockers"].append(blocker)
    status = "REVIEW_REQUIRED" if result.get("status") == "NEEDS_MATERIALS" and not documents else result.get("status")
    return {**result, "status": status, "required_documents": list(documents.values()), "actions": actions}


def _preview(repo, batch, run_id):
    status = ai.get_source_ai_review_status(batch, run_id, repository=repo)
    summary = _summary(status, batch, status.get("version_name", ""))
    if not ai.is_material_ai_preview_ready(status.get("status")):
        return summary
    catalog = status.get("row_review") or {}
    choices = _recommended_fields(catalog)
    context = repo.get_context(batch, status["version_name"])
    fee_ids = fee_policy.recommended_missing_fee_ids(
        catalog, repo.get_fees(batch, context["version"]), context.get("effective_source") or {},
        transport_mode=context.get("transport_mode") or "")
    if not choices and not fee_ids:
        return {**summary, "status": "NO_RECOMMENDED_GAPS", "can_apply": False}
    result = selections.prepare(batch, run_id, [], fee_ids, "fill_missing", status["version_name"],
                                field_choices=choices, packing_assignments={}, repository=repo)
    prepared = result["preview"]
    changes = prepared.get("changes") or []
    selected_fees = prepared.get("fees") or []
    structural = bool(prepared.get("added_count") or prepared.get("removed_count")
                      or prepared.get("scope_excluded_item_names"))
    can_apply = prepared.get("can_apply") is True and bool(changes or selected_fees) and not structural
    return {**summary, "preview_id": prepared["id"], "preview_revision": prepared["revision"],
            "batch_modified": prepared.get("batch_modified") or "", "can_apply": can_apply,
            "status": "PREVIEW_READY" if can_apply else "REVIEW_REQUIRED" if structural else "NO_APPLICABLE_GAPS",
            "changed_count": len(changes) + len(selected_fees), "fee_count": len(selected_fees),
            "field_names": sorted({change["fieldname"] for change in changes}),
            "review_count": sum(row.get("status") == "review" for row in prepared.get("field_results") or [])}


def _recommended_fields(catalog):
    by_field = defaultdict(list)
    for candidate in catalog.get("field_candidates") or []:
        if (candidate.get("can_apply") is True and candidate.get("default_selected") is True
                and not candidate.get("process_conflict")):
            key = f"{candidate['item_name']}:{candidate['fieldname']}"
            by_field[key].append(candidate["candidate_id"])
    return {key: ids[0] for key, ids in by_field.items() if len(ids) == 1}


def _summary(result, batch, version):
    return {"ok": result.get("ok", True), "batch_name": batch,
            "version_name": result.get("version_name") or version,
            "run_id": result.get("run_id") or "", "status": result.get("status") or "UNKNOWN",
            "progress_percent": result.get("progress_percent", 0), "stalled": bool(result.get("stalled")),
            "payment_selection_required": result.get("status") == "PAYMENT_SELECTION"}


def _applied_summary(result, batch, run_id):
    return {"ok": result.get("ok") is True,
            "status": "APPLIED" if result.get("ok") is True else "REVIEW_REQUIRED", "batch_name": batch,
            "run_id": run_id, "version_name": result.get("version_name") or "",
            "preview_id": result.get("preview_id") or "", "changed_count": result.get("changed_count", 0),
            "idempotent": bool(result.get("idempotent"))}
