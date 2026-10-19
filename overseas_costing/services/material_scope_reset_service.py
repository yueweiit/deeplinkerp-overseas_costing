"""Audited, resumable initialization of logistics-authoritative material scopes."""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import uuid4

try:
    import frappe as _frappe
except Exception:  # pragma: no cover - unit tests inject the runtime
    _frappe = None

from .logistics_autofill_service import (
    PHYSICAL_FIELDS,
    PURCHASE_FIELDS,
    apply_reconciliation,
    build_material_identity_hints,
    build_logistics_reconciliation,
    plan_authoritative_scope_membership,
)


class MaterialScopeSourceError(ValueError):
    def __init__(self, message: str, *, scope_status: str):
        super().__init__(message)
        self.scope_status = scope_status


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode()).hexdigest()


def _material_fingerprint(items: list[dict]) -> str:
    return _hash([
        {
            key: row.get(key)
            for key in sorted(row)
            if key != "modified"
        }
        for row in sorted(items or [], key=lambda value: str(value.get("name") or ""))
    ])


def _packing_member_signature(row: dict) -> tuple[str, str, str, str] | None:
    material_code = str(row.get("material_code") or "").strip()
    spec_model = str(row.get("spec_model") or "").strip()
    unit = str(row.get("shipped_uom") or row.get("unit") or "").strip()
    try:
        quantity = Decimal(str(row.get("actual_shipped_qty")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not material_code or not spec_model or not unit or not quantity.is_finite() or quantity <= 0:
        return None
    return material_code, spec_model, format(quantity.normalize(), "f"), unit


def plan_packing_group_changes(
    items: list[dict], projected_rows: list[dict], groups: list[dict],
) -> list[dict]:
    """Plan conservative group repairs; ambiguous identities always require reconfirmation."""

    old_rows_by_key: dict[str, list[dict]] = {}
    for row in items or []:
        key = str(row.get("stable_line_key") or "").strip()
        if key:
            old_rows_by_key.setdefault(key, []).append(row)
    projected_by_key: dict[str, list[dict]] = {}
    projected_by_name: dict[str, list[dict]] = {}
    projected_by_signature: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in projected_rows or []:
        key = str(row.get("stable_line_key") or "").strip()
        if not key:
            continue
        projected_by_key.setdefault(key, []).append(row)
        name = str(row.get("_existing_name") or row.get("name") or "").strip()
        if name:
            projected_by_name.setdefault(name, []).append(row)
        signature = _packing_member_signature(row)
        if signature:
            projected_by_signature.setdefault(signature, []).append(row)

    member_owners: dict[str, set[str]] = {}
    candidates_to_plan = []
    for raw_group in groups or []:
        if raw_group.get("status") == "removed":
            continue
        group_id = str(raw_group.get("group_id") or "").strip()
        before = [str(value or "").strip() for value in raw_group.get("member_keys") or []]
        for member_key in before:
            if group_id and member_key:
                member_owners.setdefault(member_key, set()).add(group_id)
        if raw_group.get("status") == "needs_reconfirmation":
            continue
        if not group_id or len(before) < 2 or any(not value for value in before):
            continue
        after = []
        unresolved = False
        for member_key in before:
            direct = projected_by_key.get(member_key) or []
            if len(direct) == 1:
                after.append(member_key)
                continue
            old_rows = old_rows_by_key.get(member_key) or []
            if len(old_rows) != 1:
                unresolved = True
                after.append(member_key)
                continue
            old_row = old_rows[0]
            candidates = projected_by_name.get(str(old_row.get("name") or "").strip()) or []
            if len(candidates) != 1:
                signature = _packing_member_signature(old_row)
                candidates = projected_by_signature.get(signature) or [] if signature else []
            if len(candidates) != 1:
                unresolved = True
                after.append(member_key)
                continue
            after.append(str(candidates[0].get("stable_line_key") or "").strip())
        if len(after) != len(set(after)):
            unresolved = True
        candidates_to_plan.append({
            "group_id": group_id,
            "before": before,
            "after": after,
            "status": str(raw_group.get("status") or "confirmed"),
            "unresolved": unresolved,
        })

    proposed_owners: dict[str, set[str]] = {}
    for candidate in candidates_to_plan:
        candidate["unresolved"] = candidate["unresolved"] or any(
            member_owners.get(member_key, set()) - {candidate["group_id"]}
            for member_key in candidate["after"]
        )
        if candidate["unresolved"]:
            continue
        for member_key in candidate["after"]:
            proposed_owners.setdefault(member_key, set()).add(candidate["group_id"])

    planned = []
    for candidate in candidates_to_plan:
        group_id = candidate["group_id"]
        before = candidate["before"]
        after = candidate["after"]
        unresolved = candidate["unresolved"] or any(
            proposed_owners.get(member_key, set()) - {group_id}
            for member_key in after
        )
        if unresolved:
            planned.append({
                "group_id": group_id,
                "action": "reconfirm",
                "before_member_keys": before,
                "after_member_keys": before,
                "after_status": "needs_reconfirmation",
            })
            continue
        if after != before:
            planned.append({
                "group_id": group_id,
                "action": "remap",
                "before_member_keys": before,
                "after_member_keys": after,
                "after_status": candidate["status"],
            })
    return planned


_NUMERIC_ITEM_FIELDS = {
    "row_no", "quantity", "goods_value", "unit_price",
    "actual_shipped_qty", "net_weight_kg", "gross_weight_kg",
    "volume_m3", "chargeable_weight_kg",
}


def _projected_value_matches(field: str, current: object, projected: object) -> bool:
    """Compare persisted values using the storage semantics of each field."""

    if field in _NUMERIC_ITEM_FIELDS:
        if current in (None, "") and projected in (None, ""):
            return True
        try:
            return Decimal(str(current)) == Decimal(str(projected))
        except (InvalidOperation, TypeError, ValueError):
            return False
    if field == "extra_json":
        try:
            current_json = json.loads(current or "{}")
            projected_json = json.loads(projected or "{}")
        except (TypeError, ValueError):
            pass
        else:
            return current_json == projected_json
    return str(current or "") == str(projected or "")


def _json_difference_paths(current: object, projected: object, path: str = "$") -> list[str]:
    """Return value-free JSON paths for audit diagnostics."""

    if type(current) is not type(projected):
        return [path]
    if isinstance(current, dict):
        differences = []
        for key in sorted(set(current) | set(projected)):
            child = f"{path}.{key}"
            if key not in current or key not in projected:
                differences.append(child)
            else:
                differences.extend(_json_difference_paths(current[key], projected[key], child))
        return differences
    if isinstance(current, list):
        differences = []
        if len(current) != len(projected):
            differences.append(f"{path}.length")
        for index, (left, right) in enumerate(zip(current, projected)):
            differences.extend(_json_difference_paths(left, right, f"{path}[{index}]"))
        return differences
    return [] if current == projected else [path]


def list_reset_targets(
    frappe, *, batch_names=None, include_all_versions: bool = True, limit: int = 100000
) -> list[dict]:
    if batch_names is None:
        batch_filters = {}
        batch_limit = max(1, int(limit or 100000))
    else:
        if isinstance(batch_names, str):
            batch_names = [batch_names]
        requested = [str(value or "").strip() for value in batch_names
                     if str(value or "").strip()]
        batch_filters = {"name": ["in", requested]}
        batch_limit = max(1, len(requested))
    batches = frappe.get_all(
        "Overseas Cost Batch", filters=batch_filters,
        fields=["name", "current_version"], order_by="name asc",
        limit_page_length=batch_limit,
    )
    by_name = {str(row.get("name") or ""): row for row in batches}
    if not by_name:
        return []
    if include_all_versions:
        versions = frappe.get_all(
            "Overseas Cost Version",
            filters={"batch": ["in", sorted(by_name)]},
            fields=["name", "batch", "status"],
            order_by="batch asc, creation asc, name asc",
            limit_page_length=0,
        )
    else:
        current_names = [str(row.get("current_version") or "") for row in batches
                         if str(row.get("current_version") or "")]
        versions = frappe.get_all(
            "Overseas Cost Version", filters={"name": ["in", current_names]},
            fields=["name", "batch", "status"], order_by="batch asc, name asc",
            limit_page_length=0,
        ) if current_names else []
    result = []
    for version in versions:
        batch_name = str(version.get("batch") or "")
        version_name = str(version.get("name") or "")
        batch = by_name.get(batch_name)
        if not batch or not version_name:
            continue
        current_version = str(batch.get("current_version") or "")
        result.append({
            "batch": batch_name,
            "version": version_name,
            "current_version": current_version,
            "is_current": version_name == current_version,
            "version_status": str(version.get("status") or ""),
        })
    return result


def _authoritative_logistics_source(sources: list[dict]) -> tuple[dict | None, str]:
    candidates = [source for source in sources or []
                  if source.get("approval_role") == "international_logistics"
                  and source.get("source_kind") == "approval_form"
                  and source.get("available", True) and not source.get("excluded")]
    groups: dict[str, list[dict]] = {}
    for source in candidates:
        identity = str(source.get("process_instance_id") or source.get("approval_no")
                       or source.get("source_id") or "")
        if identity:
            groups.setdefault(identity, []).append(source)
    if not groups:
        return None, "UNAVAILABLE"
    if len(groups) != 1:
        return None, "PARTIAL"
    group = next(iter(groups.values()))
    group.sort(key=lambda row: (
        not bool(row.get("source_hash") or row.get("content_hash")),
        str(row.get("source_id") or ""),
    ))
    return group[0], "AUTHORITATIVE"


def build_reset_entry(
    target: dict, items: list[dict], source: dict, *,
    identity_hints: list[dict] | None = None,
    packing_groups: list[dict] | None = None,
) -> dict:
    proposal = build_logistics_reconciliation(
        items, source, reset_manual_scope=True,
        identity_hints=identity_hints,
    )
    if not proposal or proposal.get("blocked"):
        raise ValueError(_("国际物流物料表无法唯一确定。"))
    payload = proposal.get("payload") or {}
    if payload.get("scope_status") != "AUTHORITATIVE" or not payload.get("rows"):
        raise ValueError(_("国际物流物料范围不完整。"))
    plan = plan_authoritative_scope_membership(
        items, proposal, reset_all_existing=True)
    before_codes = sorted(
        str(item.get("material_code") or "") for item in items
        if not int(item.get("is_excluded") or 0)
    )
    after_codes = [str(row.get("material_code") or "")
                   for row in payload.get("rows") or []]
    retained = {str(row.get("_existing_name") or ""): row
                for row in payload.get("rows") or []
                if str(row.get("_existing_name") or "")}
    existing = {str(item.get("name") or ""): item for item in items}
    update_fields = {
        "stable_line_key", "row_no", "material_code", "product_name",
        "spec_model", "unit", "actual_shipped_qty",
        "actual_shipped_qty_mode", "shipped_uom", "project_collection",
        "extra_json", "manual_override_flag", "manual_override_reason",
        *PURCHASE_FIELDS, *PHYSICAL_FIELDS,
    }
    updated_item_fields = {
        name: sorted(
            field for field in update_fields
            if not _projected_value_matches(
                field, existing[name].get(field), row.get(field),
            )
        )
        for name, row in retained.items()
        if name in existing
    }
    updated_item_fields = {
        name: fields for name, fields in updated_item_fields.items() if fields
    }
    updated = sorted(updated_item_fields)
    packing_group_changes = plan_packing_group_changes(
        items, payload.get("rows") or [], packing_groups or [],
    )
    updated_json_paths = {}
    for name, fields in updated_item_fields.items():
        if "extra_json" not in fields:
            continue
        try:
            current_json = json.loads(existing[name].get("extra_json") or "{}")
            projected_json = json.loads(retained[name].get("extra_json") or "{}")
        except (TypeError, ValueError):
            updated_json_paths[name] = ["$"]
        else:
            updated_json_paths[name] = _json_difference_paths(
                current_json, projected_json,
            )
    entry = {
        **{key: deepcopy(target.get(key)) for key in (
            "batch", "version", "current_version", "is_current", "version_status")},
        **plan,
        "before_material_codes": before_codes,
        "after_material_codes": after_codes,
        "material_fingerprint": _material_fingerprint(items),
        "updated_item_names": updated,
        "updated_item_fields": updated_item_fields,
        "updated_json_paths": updated_json_paths,
        "packing_group_changes": packing_group_changes,
        "proposal": proposal,
    }
    entry["material_changed"] = bool(
        entry["exclude"] or entry["restore"] or entry["create_rows"] or updated
    )
    entry["changed"] = bool(entry["material_changed"] or packing_group_changes)
    entry["entry_hash"] = _hash({
        key: entry.get(key) for key in (
            "batch", "version", "current_version", "is_current",
            "scope_status", "scope_origin", "source_fingerprint",
            "material_fingerprint",
            "before_material_codes", "after_material_codes", "exclude", "restore",
            "create_rows", "updated_item_names", "name_mismatches",
            "packing_group_changes",
        )
    })
    return entry


def build_reset_manifest(entries: list[dict], skipped: list[dict]) -> dict:
    for entry in entries:
        if "changed" not in entry:
            entry["changed"] = bool(
                entry.get("exclude") or entry.get("restore")
                or entry.get("create_rows") or entry.get("updated_item_names")
                or entry.get("packing_group_changes"))
    compact_entries = []
    for entry in entries:
        compact = {
            key: deepcopy(entry.get(key)) for key in (
            "batch", "version", "current_version", "is_current", "version_status",
            "scope_status", "scope_origin", "before_material_codes",
            "after_material_codes",
            "source_fingerprint", "material_fingerprint", "entry_hash", "changed",
            )
        }
        compact.update(
            excluded_item_count=len(entry.get("exclude") or []),
            restored_item_count=len(entry.get("restore") or []),
            created_item_count=len(entry.get("create_rows") or []),
            updated_item_count=len(entry.get("updated_item_names") or []),
            packing_group_change_count=len(entry.get("packing_group_changes") or []),
        )
        compact["packing_group_changes"] = [{
            key: deepcopy(change.get(key)) for key in (
                "group_id", "action", "before_member_keys",
                "after_member_keys", "after_status",
            )
        } for change in entry.get("packing_group_changes") or []]
        field_counts: dict[str, int] = {}
        for fields in (entry.get("updated_item_fields") or {}).values():
            for field in fields:
                field_counts[field] = field_counts.get(field, 0) + 1
        compact["updated_field_counts"] = {
            field: field_counts[field] for field in sorted(field_counts)
        }
        json_path_counts: dict[str, int] = {}
        for paths in (entry.get("updated_json_paths") or {}).values():
            for path in paths:
                json_path_counts[path] = json_path_counts.get(path, 0) + 1
        compact["updated_json_path_counts"] = {
            path: json_path_counts[path] for path in sorted(json_path_counts)
        }
        compact["name_mismatches"] = [{
            key: deepcopy(row.get(key)) for key in (
                "material_code", "source_name", "canonical_name",
            )
        } for row in entry.get("name_mismatches") or []]
        compact["create_rows"] = [{
            key: deepcopy(row.get(key)) for key in (
                "material_code", "product_name",
            )
        } for row in entry.get("create_rows") or []]
        compact_entries.append(compact)
    plan_hash = _hash({
        "policy": "material-scope-reset-v1",
        "entries": [(row.get("batch"), row.get("version"), row.get("entry_hash"))
                    for row in compact_entries],
        "skipped": skipped,
    })
    return {
        "policy": "material-scope-reset-v1",
        "plan_hash": plan_hash,
        "checked_versions": len(entries) + len(skipped),
        "changed_versions": sum(bool(row.get("changed")) for row in entries),
        "excluded": sum(len(row.get("exclude") or []) for row in entries),
        "restored": sum(len(row.get("restore") or []) for row in entries),
        "created": sum(len(row.get("create_rows") or []) for row in entries),
        "packing_group_changes": sum(
            len(row.get("packing_group_changes") or []) for row in entries),
        "name_mismatch_count": sum(len(row.get("name_mismatches") or []) for row in entries),
        "entries": compact_entries,
        "skipped": deepcopy(skipped),
    }


def _reload_reset_entry(runtime, target: dict) -> dict:
    from .material_input_service import GRID_FIELDS
    sources = _list_original_logistics_sources(target["batch"], target["version"])
    source, status = _authoritative_logistics_source(sources)
    if source is None:
        if status == "PARTIAL":
            raise MaterialScopeSourceError(
                _("找到多个国际物流正文，无法唯一定位。"), scope_status="PARTIAL")
        raise MaterialScopeSourceError(
            _("未找到可用的国际物流正文。"), scope_status="UNAVAILABLE")
    fields = list(dict.fromkeys([
        *GRID_FIELDS, "manual_override_flag", "manual_override_reason",
        "is_excluded", "exclusion_reason",
    ]))
    items = runtime.get_all(
        "Overseas Cost Item",
        filters={"batch": target["batch"], "version": target["version"]},
        fields=fields, order_by="row_no asc, name asc", limit_page_length=0,
    )
    identity_sources = [
        *sources,
        *_list_material_identity_sources(target["batch"], target["version"]),
    ]
    from .material_packing_group_service import groups_from_version
    version_meta = runtime.db.get_value(
        "Overseas Cost Version", target["version"], ["extra_json"], as_dict=True,
    ) or {}
    entry = build_reset_entry(
        target, items, source,
        identity_hints=build_material_identity_hints(identity_sources),
        packing_groups=groups_from_version(version_meta),
    )
    entry["_items"] = items
    return entry


def _list_original_logistics_sources(batch_name: str, version_name: str) -> list[dict]:
    from .packing_snapshot_service import _list_material_ai_sources

    return _list_material_ai_sources(
        batch_name, version_name,
        original_scope=True, _ignore_effective_context=True)


def _list_material_identity_sources(batch_name: str, version_name: str) -> list[dict]:
    from .packing_snapshot_service import list_material_ai_sources

    try:
        return list_material_ai_sources(
            batch_name, version_name, original_scope=False,
        )
    except Exception:
        # Lower-stage identities are optional. Failure to enumerate a payment
        # source must not hide a valid logistics/purchase-based scope.
        return []


def _build_material_scope_reset_audit(
    *, batch_names=None, include_all_versions: bool = True, limit: int = 100000,
    frappe=None,
) -> dict:
    runtime = frappe or _frappe
    if runtime is None:
        raise RuntimeError("Frappe 运行时不可用。")
    entries, skipped = [], []
    targets = list_reset_targets(
        runtime, batch_names=batch_names,
        include_all_versions=include_all_versions, limit=limit)
    for target in targets:
        try:
            entries.append(_reload_reset_entry(runtime, target))
        except Exception as error:
            skipped.append({
                **target,
                "scope_status": str(getattr(error, "scope_status", "PARTIAL")),
                "reason": str(error)[:500],
            })
    manifest = build_reset_manifest(entries, skipped)
    manifest["_entries_with_proposals"] = entries
    return manifest


def build_material_scope_reset_audit(
    *, batch_names=None, include_all_versions: bool = True, limit: int = 100000,
    frappe=None,
) -> dict:
    """Return the compact dry-run report without server-owned projections."""

    audit = _build_material_scope_reset_audit(
        batch_names=batch_names, include_all_versions=include_all_versions,
        limit=limit, frappe=frappe)
    return {key: deepcopy(value) for key, value in audit.items()
            if not key.startswith("_")}


def _insert_reset_audit(entry: dict, run_id: str) -> None:
    from .calculate_service import _insert_audit_log
    _insert_audit_log(
        batch_doc_name=entry["batch"], version_name=entry["version"],
        action_type="BATCH_EDIT", field_name="material_scope_reset",
        old_value=json.dumps(entry.get("before_material_codes") or [], ensure_ascii=False),
        new_value=json.dumps(entry.get("after_material_codes") or [], ensure_ascii=False),
        action_remark=(f"国际物流主物料范围初始化 {run_id}；"
                       f"packing_group_changes={len(entry.get('packing_group_changes') or [])}；"
                       f"plan={entry.get('entry_hash') or ''}"),
    )


def _apply_packing_group_changes(metadata: dict, changes: list[dict], run_id: str) -> None:
    groups = metadata.get("material_packing_groups") or []
    by_id = {str(group.get("group_id") or ""): group for group in groups}
    now = datetime.now().isoformat(timespec="seconds")
    for change in changes or []:
        group = by_id.get(str(change.get("group_id") or ""))
        before = list(change.get("before_member_keys") or [])
        if not group or list(group.get("member_keys") or []) != before:
            raise ValueError(_("装箱组已变化，请重新生成只读审计。"))
        after = list(change.get("after_member_keys") or [])
        group["member_keys"] = after
        group["status"] = str(change.get("after_status") or "needs_reconfirmation")
        event = {
            "run_id": run_id,
            "action": str(change.get("action") or ""),
            "before_member_keys": before,
            "after_member_keys": after,
            "repaired_at": now,
        }
        history = deepcopy(group.get("scope_reset_repair_history") or [])
        previous = group.get("scope_reset_repair")
        if isinstance(previous, dict) and previous not in history:
            history.append(deepcopy(previous))
        history.append(deepcopy(event))
        group["scope_reset_repair_history"] = history
        group["scope_reset_repair"] = event


def _mark_version_for_recalculation(runtime, entry: dict, run_id: str) -> None:
    metadata_raw = runtime.db.get_value(
        "Overseas Cost Version", entry["version"], "extra_json") or "{}"
    try:
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw)
    except (TypeError, ValueError):
        metadata = {}
    _apply_packing_group_changes(
        metadata, entry.get("packing_group_changes") or [], run_id,
    )
    metadata["material_scope_reset"] = {
        "run_id": run_id,
        "entry_hash": entry.get("entry_hash"),
        "reset_at": datetime.now().isoformat(timespec="seconds"),
        "scope_origin": entry.get("scope_origin"),
        "source_fingerprint": entry.get("source_fingerprint"),
        "material_fingerprint": entry.get("material_fingerprint"),
        "source_fact_ids": deepcopy(entry.get("source_fact_ids") or []),
        "name_mismatches": deepcopy(entry.get("name_mismatches") or []),
    }
    runtime.db.set_value("Overseas Cost Version", entry["version"], {
        "status": "Active" if entry.get("is_current") else entry.get("version_status"),
        "calculated_at": None,
        "summary_snapshot_json": None,
        "rule_snapshot_json": "[]",
        "extra_json": json.dumps(metadata, ensure_ascii=False),
    }, update_modified=True)
    if entry.get("is_current"):
        runtime.db.set_value("Overseas Cost Batch", entry["batch"], {
            "status": "Dirty", "confirm_status": "Pending",
            "writeback_status": "Not Started",
            "item_count": len(entry.get("after_material_codes") or []),
        }, update_modified=True)


def _lock_reset_target(runtime, entry: dict) -> None:
    runtime.db.sql(
        "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE",
        (entry["batch"],),
    )
    runtime.db.sql(
        "SELECT name FROM `tabOverseas Cost Version` "
        "WHERE name=%s AND batch=%s FOR UPDATE",
        (entry["version"], entry["batch"]),
    )
    runtime.db.sql(
        "SELECT name FROM `tabOverseas Cost Item` "
        "WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE",
        (entry["batch"], entry["version"]),
    )


def _refresh_reset_target(runtime, target: dict) -> dict:
    batch = runtime.db.get_value(
        "Overseas Cost Batch", target["batch"], ["current_version"], as_dict=True) or {}
    version = runtime.db.get_value(
        "Overseas Cost Version", target["version"], ["batch", "status"], as_dict=True) or {}
    if str(version.get("batch") or "") != str(target["batch"]):
        raise ValueError(_("成本版本已不属于当前批次。"))
    current_version = str(batch.get("current_version") or "")
    return {
        **target,
        "current_version": current_version,
        "is_current": str(target["version"]) == current_version,
        "version_status": str(version.get("status") or ""),
    }


def reset_all_material_scopes(
    batch_names=None, *, dry_run: bool = True, expected_plan_hash: str = "",
    run_id: str = "", include_all_versions: bool = True, limit: int = 100000,
    raise_on_incomplete: bool = False,
) -> dict:
    audit = _build_material_scope_reset_audit(
        batch_names=batch_names, include_all_versions=include_all_versions, limit=limit)
    public = {key: deepcopy(value) for key, value in audit.items()
              if not key.startswith("_")}
    public["dry_run"] = bool(dry_run)
    if dry_run:
        return public
    if not expected_plan_hash or expected_plan_hash != audit.get("plan_hash"):
        raise ValueError(_("只读审计结果已变化，请重新生成全量计划后再执行。"))
    runtime = _frappe
    if runtime is None:
        raise RuntimeError("Frappe 运行时不可用。")
    # The dry-run audit uses ordinary consistent reads. End that read
    # transaction before acquiring per-version locks so MariaDB does not reuse
    # a REPEATABLE READ snapshot while we perform the locked recheck.
    runtime.db.rollback()
    run_id = str(run_id or f"material-scope-reset-{uuid4().hex[:12]}")
    applied, failed, stale = [], [], []
    for entry in audit.get("_entries_with_proposals") or []:
        try:
            _lock_reset_target(runtime, entry)
            current_target = _refresh_reset_target(runtime, entry)
            current_entry = _reload_reset_entry(runtime, current_target)
            if current_entry.get("entry_hash") != entry.get("entry_hash"):
                stale.append({
                    "batch": entry["batch"], "version": entry["version"],
                    "reason": "来源或物料已变化，请重新生成只读审计。",
                })
                runtime.db.rollback()
                continue
            if not current_entry.get("changed"):
                runtime.db.rollback()
                continue
            created = apply_reconciliation(
                runtime, current_entry["proposal"], batch=current_entry["batch"],
                version=current_entry["version"], current=current_entry["_items"], run_id=run_id,
                update_batch_count=bool(current_entry.get("is_current")),
                initialization_reset=True,
            ) if current_entry.get("material_changed") else []
            _mark_version_for_recalculation(runtime, current_entry, run_id)
            _insert_reset_audit(current_entry, run_id)
            runtime.db.commit()
            applied.append({
                "batch": current_entry["batch"], "version": current_entry["version"],
                "excluded": len(current_entry.get("exclude") or []),
                "restored": len(current_entry.get("restore") or []),
                "created": len(created), "entry_hash": current_entry.get("entry_hash"),
                "packing_group_changes": len(current_entry.get("packing_group_changes") or []),
            })
        except Exception as error:
            runtime.db.rollback()
            failed.append({
                "batch": entry.get("batch"), "version": entry.get("version"),
                "reason": str(error)[:500],
            })
    result = {
        **public, "dry_run": False, "run_id": run_id,
        "applied_versions": len(applied), "applied": applied,
        "stale": stale, "failed": failed,
    }
    if raise_on_incomplete and (stale or failed):
        raise RuntimeError(
            "全量物料范围重置未完整完成：" + json.dumps({
                "run_id": run_id,
                "applied_versions": len(applied),
                "stale": stale,
                "failed": failed,
            }, ensure_ascii=False, default=str)
        )
    return result
