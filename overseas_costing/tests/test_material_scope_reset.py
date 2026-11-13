from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest


def test_reset_target_listing_includes_every_version_not_only_current():
    from overseas_costing.services.material_scope_reset_service import list_reset_targets

    class Frappe:
        @staticmethod
        def get_all(doctype, **kwargs):
            if doctype == "Overseas Cost Batch":
                return [{"name": "B-1", "current_version": "V-2"}]
            if doctype == "Overseas Cost Version":
                return [
                    {"name": "V-1", "batch": "B-1", "status": "Confirmed"},
                    {"name": "V-2", "batch": "B-1", "status": "Active"},
                ]
            raise AssertionError(doctype)

    targets = list_reset_targets(Frappe(), include_all_versions=True)

    assert targets == [
        {"batch": "B-1", "version": "V-1", "current_version": "V-2",
         "is_current": False, "version_status": "Confirmed"},
        {"batch": "B-1", "version": "V-2", "current_version": "V-2",
         "is_current": True, "version_status": "Active"},
    ]


def test_reset_entry_reports_codes_creates_and_name_mismatch():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    items = [
        {"name": "I-144", "material_code": "MWV101144",
         "product_name": "薇武士 IP17 PRO", "quantity": 1,
         "actual_shipped_qty": 1, "is_excluded": 0, "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145",
         "product_name": "薇武士 IP17 PRO MAX", "quantity": 1,
         "actual_shipped_qty": 1, "manual_override_flag": 1,
         "is_excluded": 0, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:78258:form",
        "source_hash": "HASH", "approval_role": "international_logistics",
        "approval_no": "202607211417000078258",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "MHA超队模具",
            "数量": 1, "单位": "套",
        }, {
            "物料编码": "SKU-NEW", "物料名称": "新物料",
            "数量": 2, "单位": "个",
        }]},
    }

    entry = build_reset_entry(
        {"batch": "B-1", "version": "V-1", "current_version": "V-1",
         "is_current": True, "version_status": "Active"},
        items, source,
    )

    assert entry["scope_status"] == "AUTHORITATIVE"
    assert entry["before_material_codes"] == ["MWV101144", "MWV101145"]
    assert entry["after_material_codes"] == ["MWV101144", "SKU-NEW"]
    assert entry["exclude"] == ["I-145"]
    assert [row["material_code"] for row in entry["create_rows"]] == ["SKU-NEW"]
    assert entry["name_mismatches"][0]["source_name"] == "MHA超队模具"
    assert entry["entry_hash"]
    assert entry["proposal"]["payload"]["rows"][0]["product_name"] == "薇武士 IP17 PRO"


def test_reset_entry_keeps_logistics_row_count_when_purchase_resolves_placeholder_code():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-PLACEHOLDER:form",
        "source_hash": "LOGISTICS", "approval_role": "international_logistics",
        "approval_no": "LOG-PLACEHOLDER", "form_fields": {"货物信息": [
            {"物料编码": "/", "物料名称": "新模具", "数量": 1, "单位": "套"},
        ]},
    }
    identity_hints = [{
        "material_code": "MOLD-001", "product_name": "新模具",
        "source_id": "purchase:P-1:1", "approval_role": "purchase",
    }]

    entry = build_reset_entry(
        {"batch": "B-1", "version": "V-1", "current_version": "V-1",
         "is_current": True, "version_status": "Active"},
        [], source, identity_hints=identity_hints,
    )

    assert entry["scope_status"] == "AUTHORITATIVE"
    assert entry["after_material_codes"] == ["MOLD-001"]
    assert len(entry["create_rows"]) == 1
    assert entry["create_rows"][0]["material_code"] == "MOLD-001"


def test_entry_hash_changes_for_concurrent_business_field_change():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "物料", "数量": 1, "单位": "个",
        }]},
    }
    item = {
        "name": "I-1", "material_code": "SKU-1", "product_name": "物料",
        "quantity": 1, "actual_shipped_qty": 1, "goods_value": 100,
        "is_excluded": 0, "extra_json": "{}",
    }

    first = build_reset_entry(target, [item], source)
    second = build_reset_entry(target, [{**item, "goods_value": 200}], source)

    assert first["updated_item_names"] == second["updated_item_names"]
    assert first["material_fingerprint"] != second["material_fingerprint"]
    assert first["entry_hash"] != second["entry_hash"]


def test_equal_manual_quantity_still_changes_to_explicit_logistics_source():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "物料", "数量": 1, "单位": "个",
        }]},
    }
    item = {
        "name": "I-1", "material_code": "SKU-1", "product_name": "物料",
        "quantity": 1, "actual_shipped_qty": "1",
        "actual_shipped_qty_mode": "MANUAL_CONFIRMED", "shipped_uom": "个",
        "unit": "个", "is_excluded": 0, "extra_json": "{}",
    }

    entry = build_reset_entry(target, [item], source)

    assert entry["changed"] is True
    assert entry["updated_item_names"] == ["I-1"]
    assert entry["proposal"]["payload"]["rows"][0]["actual_shipped_qty_mode"] == "EXPLICIT_SOURCE"


def test_fresh_audit_after_projected_apply_is_idempotent():
    import json
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "物料", "数量": 1, "单位": "个",
        }]},
    }
    original = {
        "name": "I-1", "material_code": "SKU-1", "product_name": "物料",
        "quantity": 1, "actual_shipped_qty": 9,
        "actual_shipped_qty_mode": "MANUAL_CONFIRMED", "shipped_uom": "箱",
        "unit": "个", "is_excluded": 0, "extra_json": "{}",
    }
    first = build_reset_entry(target, [original], source)
    projected = {
        key: value for key, value in first["proposal"]["payload"]["rows"][0].items()
        if not key.startswith("_")
    }
    projected.update(name="I-1", is_excluded=0,
                     actual_shipped_qty_source_revision="RESET-1", cost_output_uom="个")
    metadata = json.loads(projected["extra_json"])
    metadata["autofill_review"] = {
        "run_id": "RESET-1", "proposal_id": first["proposal"]["proposal_id"],
        "source_refs": first["proposal"]["source_refs"],
    }
    projected["extra_json"] = json.dumps(metadata, ensure_ascii=False)

    second = build_reset_entry(target, [projected], source)

    assert second["exclude"] == []
    assert second["restore"] == []
    assert second["create_rows"] == []
    assert second["updated_item_names"] == []
    assert second["changed"] is False


def test_fresh_audit_treats_database_decimal_scale_as_the_same_projected_value():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "物料", "数量": 1, "单位": "个",
        }]},
    }
    item = {
        "name": "I-1", "stable_line_key": "logistics:stable",
        "material_code": "SKU-1", "product_name": "物料",
        "quantity": Decimal("1.000000"),
        "actual_shipped_qty": Decimal("1.000000"),
        "actual_shipped_qty_mode": "EXPLICIT_SOURCE", "shipped_uom": "个",
        "unit": "个", "is_excluded": 0, "extra_json": "{}",
    }

    first = build_reset_entry(target, [item], source)
    projected = {
        key: value for key, value in first["proposal"]["payload"]["rows"][0].items()
        if not key.startswith("_")
    }
    projected.update(
        name="I-1", is_excluded=0,
        actual_shipped_qty=Decimal("1.000000"),
    )

    second = build_reset_entry(target, [projected], source)

    assert second["updated_item_names"] == []
    assert second["changed"] is False


def test_reset_entry_reports_only_changed_field_names_for_diagnostics():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "物料", "数量": 1, "单位": "个",
        }]},
    }
    item = {
        "name": "I-1", "material_code": "SKU-1", "product_name": "物料",
        "quantity": 1, "actual_shipped_qty": 1,
        "actual_shipped_qty_mode": "LEGACY_UNVERIFIED", "shipped_uom": "个",
        "unit": "个", "is_excluded": 0, "extra_json": "{}",
    }

    entry = build_reset_entry(target, [item], source)

    assert entry["updated_item_names"] == ["I-1"]
    assert entry["updated_item_fields"] == {"I-1": [
        "actual_shipped_qty_mode", "extra_json", "row_no", "source_doc_no",
        "source_type", "stable_line_key",
    ]}
    assert entry["updated_json_paths"] == {"I-1": ["$.logistics_cargo_group", "$.logistics_row"]}


def test_fresh_audit_keeps_explicit_empty_purchase_link_for_created_logistics_row():
    from overseas_costing.services.material_scope_reset_service import build_reset_entry

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "source_hash": "SOURCE", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "form_fields": {"货物信息": [{
            "物料编码": "SKU-NEW", "物料名称": "新物料", "数量": 1, "单位": "个",
        }]},
    }
    first = build_reset_entry(target, [], source)
    created = {
        key: value for key, value in first["proposal"]["payload"]["rows"][0].items()
        if not key.startswith("_")
    }
    created.update(name="I-NEW", is_excluded=0)

    second = build_reset_entry(target, [created], source)

    assert second["updated_item_names"] == []
    assert second["changed"] is False


def test_manifest_hash_changes_when_material_membership_changes():
    from overseas_costing.services.material_scope_reset_service import build_reset_manifest

    entry = {
        "batch": "B-1", "version": "V-1", "entry_hash": "ENTRY-1",
        "scope_status": "AUTHORITATIVE", "exclude": ["I-2"], "restore": [],
        "create_rows": [], "before_material_codes": ["A", "B"],
        "after_material_codes": ["A"], "name_mismatches": [],
    }
    first = build_reset_manifest([entry], [])
    changed = deepcopy(entry)
    changed["entry_hash"] = "ENTRY-2"
    second = build_reset_manifest([changed], [])

    assert first["plan_hash"] != second["plan_hash"]
    assert first["checked_versions"] == 1
    assert first["changed_versions"] == 1
    assert first["excluded"] == 1


def test_public_manifest_keeps_created_rows_compact_and_omits_business_projection():
    from overseas_costing.services.material_scope_reset_service import build_reset_manifest

    entry = {
        "batch": "B-1", "version": "V-1", "entry_hash": "ENTRY-1",
        "scope_status": "AUTHORITATIVE", "exclude": [], "restore": [],
        "before_material_codes": [], "after_material_codes": ["SKU-NEW"],
        "name_mismatches": [], "updated_item_names": [], "source_fact_ids": [],
        "source_fingerprint": "FP", "changed": True,
        "create_rows": [{
            "material_code": "SKU-NEW", "product_name": "新物料",
            "actual_shipped_qty": "2", "shipped_uom": "个",
            "stable_line_key": "line-1", "goods_value": "99999",
            "extra_json": "private-evidence",
        }],
    }

    manifest = build_reset_manifest([entry], [])
    public_row = manifest["entries"][0]["create_rows"][0]

    assert public_row == {"material_code": "SKU-NEW", "product_name": "新物料"}
    assert "private-evidence" not in str(manifest)


def test_marking_historical_version_preserves_its_lifecycle_status():
    from overseas_costing.services.material_scope_reset_service import _mark_version_for_recalculation

    writes = []

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return "{}"

        @staticmethod
        def set_value(doctype, name, values, **_kwargs):
            writes.append((doctype, name, values))

    entry = {
        "batch": "B-1", "version": "V-OLD", "is_current": False,
        "version_status": "Confirmed", "entry_hash": "ENTRY-1",
        "scope_origin": "international_logistics", "source_fingerprint": "FP",
        "source_fact_ids": ["FACT-1"], "name_mismatches": [{"material_code": "SKU-1"}],
    }

    _mark_version_for_recalculation(SimpleNamespace(db=DB()), entry, "RESET-1")

    version_values = next(values for doctype, name, values in writes
                          if doctype == "Overseas Cost Version" and name == "V-OLD")
    assert version_values["status"] == "Confirmed"
    assert version_values["rule_snapshot_json"] == "[]"
    metadata = __import__("json").loads(version_values["extra_json"])["material_scope_reset"]
    assert metadata["source_fingerprint"] == "FP"
    assert metadata["source_fact_ids"] == ["FACT-1"]
    assert metadata["name_mismatches"] == [{"material_code": "SKU-1"}]
    assert not any(doctype == "Overseas Cost Batch" for doctype, _name, _values in writes)


def test_packing_group_repair_uniquely_remaps_excluded_members() -> None:
    from overseas_costing.services.material_scope_reset_service import plan_packing_group_changes

    items = [
        {"name": "OLD-1", "stable_line_key": "OLD-L1", "is_excluded": 1,
         "material_code": "FL000429", "spec_model": "超队指环扣",
         "actual_shipped_qty": "4000", "unit": "个"},
        {"name": "OLD-2", "stable_line_key": "OLD-L2", "is_excluded": 1,
         "material_code": "FL000427", "spec_model": "超队CD纹",
         "actual_shipped_qty": "4000", "unit": "个"},
    ]
    projected = [
        {"_existing_name": "NEW-1", "stable_line_key": "NEW-L1",
         "material_code": "FL000429", "spec_model": "超队指环扣",
         "actual_shipped_qty": "4000", "unit": "个"},
        {"_existing_name": "NEW-2", "stable_line_key": "NEW-L2",
         "material_code": "FL000427", "spec_model": "超队CD纹",
         "actual_shipped_qty": "4000", "unit": "个"},
    ]
    groups = [{
        "group_id": "GROUP-1", "status": "confirmed",
        "member_keys": ["OLD-L1", "OLD-L2"],
        "package_count": "1", "gross_weight_kg": "9.7",
    }]

    changes = plan_packing_group_changes(items, projected, groups)

    assert changes == [{
        "group_id": "GROUP-1",
        "action": "remap",
        "before_member_keys": ["OLD-L1", "OLD-L2"],
        "after_member_keys": ["NEW-L1", "NEW-L2"],
        "after_status": "confirmed",
    }]


def test_packing_group_repair_never_guesses_ambiguous_member() -> None:
    from overseas_costing.services.material_scope_reset_service import plan_packing_group_changes

    old = {"name": "OLD-1", "stable_line_key": "OLD-L1", "is_excluded": 1,
           "material_code": "SKU-1", "spec_model": "S", "actual_shipped_qty": "4", "unit": "个"}
    projected = [
        {"stable_line_key": "NEW-L1", "material_code": "SKU-1", "spec_model": "S",
         "actual_shipped_qty": "4", "unit": "个"},
        {"stable_line_key": "NEW-L2", "material_code": "SKU-1", "spec_model": "S",
         "actual_shipped_qty": "4", "unit": "个"},
    ]

    changes = plan_packing_group_changes(
        [old], projected,
        [{"group_id": "GROUP-1", "status": "confirmed", "member_keys": ["OLD-L1", "LIVE-L2"]}],
    )

    assert changes == [{
        "group_id": "GROUP-1",
        "action": "reconfirm",
        "before_member_keys": ["OLD-L1", "LIVE-L2"],
        "after_member_keys": ["OLD-L1", "LIVE-L2"],
        "after_status": "needs_reconfirmation",
    }]


def test_packing_group_repair_does_not_take_key_owned_by_later_group() -> None:
    from overseas_costing.services.material_scope_reset_service import plan_packing_group_changes

    items = [
        {"name": "OLD-1", "stable_line_key": "OLD-L1", "is_excluded": 1,
         "material_code": "SKU-1", "spec_model": "S",
         "actual_shipped_qty": "4", "unit": "个"},
        {"name": "OLD-3", "stable_line_key": "OLD-L3", "is_excluded": 1,
         "material_code": "SKU-3", "spec_model": "U",
         "actual_shipped_qty": "6", "unit": "个"},
    ]
    projected = [
        {"stable_line_key": "LIVE-L1", "material_code": "SKU-1", "spec_model": "S",
         "actual_shipped_qty": "4", "unit": "个"},
        {"stable_line_key": "LIVE-L2", "material_code": "SKU-2", "spec_model": "T",
         "actual_shipped_qty": "5", "unit": "个"},
        {"stable_line_key": "LIVE-L3", "material_code": "SKU-3", "spec_model": "U",
         "actual_shipped_qty": "6", "unit": "个"},
    ]
    groups = [
        {"group_id": "STALE-FIRST", "status": "confirmed",
         "member_keys": ["OLD-L1", "OLD-L3"]},
        {"group_id": "RIGHTFUL-LATER", "status": "confirmed",
         "member_keys": ["LIVE-L1", "LIVE-L2"]},
    ]

    changes = plan_packing_group_changes(items, projected, groups)

    assert changes == [{
        "group_id": "STALE-FIRST", "action": "reconfirm",
        "before_member_keys": ["OLD-L1", "OLD-L3"],
        "after_member_keys": ["OLD-L1", "OLD-L3"],
        "after_status": "needs_reconfirmation",
    }]


def test_mark_version_applies_audited_packing_group_remap() -> None:
    from overseas_costing.services.material_scope_reset_service import _mark_version_for_recalculation

    writes = []
    existing = {
        "material_packing_groups": [{
            "group_id": "GROUP-1", "status": "confirmed",
            "member_keys": ["OLD-L1", "OLD-L2"], "package_count": "1",
            "scope_reset_repair": {
                "run_id": "RESET-OLD", "action": "remap",
                "before_member_keys": ["OLDER-1", "OLDER-2"],
                "after_member_keys": ["OLD-L1", "OLD-L2"],
                "repaired_at": "2026-09-01T00:00:00",
            },
        }]
    }

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return __import__("json").dumps(existing)

        @staticmethod
        def set_value(doctype, name, values, **_kwargs):
            writes.append((doctype, name, values))

    entry = {
        "batch": "B-1", "version": "V-1", "is_current": True,
        "version_status": "Active", "entry_hash": "ENTRY-1",
        "after_material_codes": ["A", "B"],
        "packing_group_changes": [{
            "group_id": "GROUP-1", "action": "remap",
            "before_member_keys": ["OLD-L1", "OLD-L2"],
            "after_member_keys": ["NEW-L1", "NEW-L2"],
            "after_status": "confirmed",
        }],
    }

    _mark_version_for_recalculation(SimpleNamespace(db=DB()), entry, "RESET-1")

    version_values = next(values for doctype, _name, values in writes
                          if doctype == "Overseas Cost Version")
    metadata = __import__("json").loads(version_values["extra_json"])
    group = metadata["material_packing_groups"][0]
    assert group["member_keys"] == ["NEW-L1", "NEW-L2"]
    assert group["status"] == "confirmed"
    assert group["scope_reset_repair"]["run_id"] == "RESET-1"
    assert group["scope_reset_repair"]["before_member_keys"] == ["OLD-L1", "OLD-L2"]
    assert [event["run_id"] for event in group["scope_reset_repair_history"]] == [
        "RESET-OLD", "RESET-1",
    ]


def test_apply_requires_matching_dry_run_plan_hash(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "CURRENT", "entries": [], "skipped": [],
    })

    with pytest.raises(ValueError, match="只读审计"):
        service.reset_all_material_scopes(dry_run=False, expected_plan_hash="OLD")


def test_apply_is_idempotent_when_manifest_has_no_changes(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "SAME", "entries": [{
            "batch": "B-1", "version": "V-1", "is_current": True,
            "scope_status": "AUTHORITATIVE", "exclude": [], "restore": [],
            "create_rows": [], "changed": False,
        }], "skipped": [], "checked_versions": 1, "changed_versions": 0,
        "excluded": 0, "restored": 0, "created": 0,
    })
    fake = SimpleNamespace(db=SimpleNamespace(commit=lambda: None, rollback=lambda: None))
    monkeypatch.setattr(service, "_frappe", fake)

    result = service.reset_all_material_scopes(
        dry_run=False, expected_plan_hash="SAME", run_id="RESET-1")

    assert result["changed_versions"] == 0
    assert result["applied_versions"] == 0
    assert result["failed"] == []


def test_apply_group_only_repair_does_not_rewrite_material_rows(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    planned = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
        "entry_hash": "ENTRY-1", "changed": True, "material_changed": False,
        "exclude": [], "restore": [], "create_rows": [], "_items": [],
        "proposal": {"payload": {}}, "packing_group_changes": [{
            "group_id": "G-1", "action": "remap",
            "before_member_keys": ["OLD-1", "OLD-2"],
            "after_member_keys": ["NEW-1", "NEW-2"],
            "after_status": "confirmed",
        }],
    }
    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "PLAN", "entries": [], "skipped": [],
        "checked_versions": 1, "changed_versions": 1,
        "excluded": 0, "restored": 0, "created": 0,
        "_entries_with_proposals": [planned],
    })
    monkeypatch.setattr(service, "_refresh_reset_target", lambda _runtime, target: target)
    monkeypatch.setattr(service, "_reload_reset_entry", lambda *_args, **_kwargs: planned)
    monkeypatch.setattr(
        service, "apply_reconciliation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not rewrite items")),
    )
    marked = []
    monkeypatch.setattr(service, "_mark_version_for_recalculation", lambda *_args: marked.append(True))
    monkeypatch.setattr(service, "_insert_reset_audit", lambda *_args: None)
    commits = []
    fake = SimpleNamespace(db=SimpleNamespace(
        commit=lambda: commits.append(True), rollback=lambda: None,
        sql=lambda *_args, **_kwargs: [],
    ))
    monkeypatch.setattr(service, "_frappe", fake)

    result = service.reset_all_material_scopes(
        dry_run=False, expected_plan_hash="PLAN", run_id="RESET-1",
    )

    assert marked == [True]
    assert commits == [True]
    assert result["applied"] == [{
        "batch": "B-1", "version": "V-1",
        "excluded": 0, "restored": 0, "created": 0,
        "entry_hash": "ENTRY-1", "packing_group_changes": 1,
    }]


def test_apply_skips_version_when_source_or_items_change_after_preflight(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    planned = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
        "scope_status": "AUTHORITATIVE", "scope_origin": "international_logistics",
        "source_fingerprint": "SOURCE-OLD", "entry_hash": "ENTRY-OLD",
        "exclude": ["I-2"], "restore": [], "create_rows": [],
        "before_material_codes": ["A", "B"], "after_material_codes": ["A"],
        "updated_item_names": [], "name_mismatches": [], "changed": True,
        "proposal": {"payload": {}},
    }
    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "PLAN", "entries": [], "skipped": [],
        "checked_versions": 1, "changed_versions": 1,
        "excluded": 1, "restored": 0, "created": 0,
        "_entries_with_proposals": [planned],
    })
    monkeypatch.setattr(service, "_reload_reset_entry", lambda *_args, **_kwargs: {
        **planned, "entry_hash": "ENTRY-NEW", "source_fingerprint": "SOURCE-NEW",
    })
    rollbacks = []
    def get_value(doctype, _name, _fields, **_kwargs):
        if doctype == "Overseas Cost Batch":
            return {"current_version": "V-1"}
        if doctype == "Overseas Cost Version":
            return {"batch": "B-1", "status": "Active"}
        raise AssertionError(doctype)
    fake = SimpleNamespace(db=SimpleNamespace(
        commit=lambda: None, rollback=lambda: rollbacks.append(True),
        sql=lambda *_args, **_kwargs: [], get_value=get_value))
    monkeypatch.setattr(service, "_frappe", fake)

    result = service.reset_all_material_scopes(
        dry_run=False, expected_plan_hash="PLAN", run_id="RESET-1")

    assert result["applied_versions"] == 0
    assert result["failed"] == []
    assert result["stale"] == [{
        "batch": "B-1", "version": "V-1",
        "reason": "来源或物料已变化，请重新生成只读审计。",
    }]
    assert rollbacks == [True, True]


def test_apply_rechecks_even_an_unchanged_preflight_entry(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    planned = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
        "scope_status": "AUTHORITATIVE", "scope_origin": "international_logistics",
        "source_fingerprint": "SOURCE", "material_fingerprint": "ITEMS-OLD",
        "entry_hash": "ENTRY-OLD", "exclude": [], "restore": [], "create_rows": [],
        "before_material_codes": ["A"], "after_material_codes": ["A"],
        "updated_item_names": [], "name_mismatches": [], "changed": False,
        "proposal": {"payload": {}},
    }
    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "PLAN", "entries": [], "skipped": [],
        "checked_versions": 1, "changed_versions": 0,
        "excluded": 0, "restored": 0, "created": 0,
        "_entries_with_proposals": [planned],
    })
    monkeypatch.setattr(service, "_refresh_reset_target", lambda _runtime, target: target)
    monkeypatch.setattr(service, "_reload_reset_entry", lambda *_args, **_kwargs: {
        **planned, "changed": True, "entry_hash": "ENTRY-NEW",
        "material_fingerprint": "ITEMS-NEW",
    })
    rollbacks = []
    fake = SimpleNamespace(db=SimpleNamespace(
        commit=lambda: None, rollback=lambda: rollbacks.append(True),
        sql=lambda *_args, **_kwargs: []))
    monkeypatch.setattr(service, "_frappe", fake)

    result = service.reset_all_material_scopes(
        dry_run=False, expected_plan_hash="PLAN", run_id="RESET-1")

    assert result["applied_versions"] == 0
    assert result["stale"][0]["version"] == "V-1"
    assert rollbacks == [True, True]


def test_strict_apply_raises_after_recording_partial_failure(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    planned = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active", "changed": True,
        "entry_hash": "ENTRY", "proposal": {"payload": {}},
    }
    monkeypatch.setattr(service, "_build_material_scope_reset_audit", lambda **_kwargs: {
        "plan_hash": "PLAN", "entries": [], "skipped": [],
        "checked_versions": 1, "changed_versions": 1,
        "excluded": 0, "restored": 0, "created": 0,
        "_entries_with_proposals": [planned],
    })
    monkeypatch.setattr(service, "_refresh_reset_target", lambda _runtime, target: target)
    monkeypatch.setattr(
        service, "_reload_reset_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("坏版本")),
    )
    fake = SimpleNamespace(db=SimpleNamespace(
        commit=lambda: None, rollback=lambda: None,
        sql=lambda *_args, **_kwargs: []))
    monkeypatch.setattr(service, "_frappe", fake)

    with pytest.raises(RuntimeError, match="坏版本"):
        service.reset_all_material_scopes(
            dry_run=False, expected_plan_hash="PLAN", run_id="RESET-1",
            raise_on_incomplete=True)


def test_audit_distinguishes_unavailable_logistics_source(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service

    target = {
        "batch": "B-1", "version": "V-1", "current_version": "V-1",
        "is_current": True, "version_status": "Active",
    }
    monkeypatch.setattr(service, "list_reset_targets", lambda *_args, **_kwargs: [target])
    monkeypatch.setattr(
        service, "_reload_reset_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            service.MaterialScopeSourceError(
                "未找到可用的国际物流正文。", scope_status="UNAVAILABLE")),
    )

    result = service.build_material_scope_reset_audit(frappe=SimpleNamespace())

    assert "_entries_with_proposals" not in result
    assert result["skipped"] == [{
        **target, "scope_status": "UNAVAILABLE", "reason": "未找到可用的国际物流正文。",
    }]


def test_refresh_target_rechecks_current_version_and_historical_status():
    from overseas_costing.services.material_scope_reset_service import _refresh_reset_target

    class DB:
        @staticmethod
        def get_value(doctype, _name, _fields, **_kwargs):
            if doctype == "Overseas Cost Batch":
                return {"current_version": "V-2"}
            if doctype == "Overseas Cost Version":
                return {"batch": "B-1", "status": "Archived"}
            raise AssertionError(doctype)

    refreshed = _refresh_reset_target(
        SimpleNamespace(db=DB()),
        {"batch": "B-1", "version": "V-1", "current_version": "V-1",
         "is_current": True, "version_status": "Active"},
    )

    assert refreshed["current_version"] == "V-2"
    assert refreshed["is_current"] is False
    assert refreshed["version_status"] == "Archived"


def test_reset_source_listing_bypasses_bound_payment_root(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service
    from overseas_costing.services import packing_snapshot_service

    calls = []
    monkeypatch.setattr(
        packing_snapshot_service, "_list_material_ai_sources",
        lambda batch, version, **kwargs: calls.append((batch, version, kwargs)) or [
            {"approval_role": "international_logistics", "source_kind": "approval_form"}
        ],
    )

    sources = service._list_original_logistics_sources("B-1", "V-1")

    assert sources[0]["approval_role"] == "international_logistics"
    assert calls == [("B-1", "V-1", {
        "original_scope": True, "_ignore_effective_context": True,
    })]


def test_reset_identity_source_listing_includes_server_scoped_payment(monkeypatch):
    from overseas_costing.services import material_scope_reset_service as service
    from overseas_costing.services import packing_snapshot_service

    calls = []
    monkeypatch.setattr(
        packing_snapshot_service, "list_material_ai_sources",
        lambda batch, version, **kwargs: calls.append((batch, version, kwargs)) or [
            {"approval_role": "payment", "scoped_packing": True,
             "scoped_goods": [{"material_code": "SKU-PAY"}]}
        ],
    )

    sources = service._list_material_identity_sources("B-1", "V-1")

    assert sources[0]["approval_role"] == "payment"
    assert calls == [("B-1", "V-1", {"original_scope": False})]
