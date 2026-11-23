from __future__ import annotations

import pytest
import sys
from types import SimpleNamespace

from overseas_costing.services import company_rename_service as service


EXPECTED_RENAMES = (
    ("YW Fabricación MX 核心制造", "YUEWEI MX"),
    ("LEMOS MX供应链开发及管理", "LEMOS MX"),
    ("UV IMPRESION MX彩印", "UV IMPRESION"),
    ("YW Centro MX 共享中心 YUEWEI Grupo", "YW GRUPO MX"),
    ("YW MOLDES MX模具", "YW MOLDES"),
    ("YUEWEI CN悦为中国", "YW 中国共享中心"),
)


def test_company_rename_plan_contains_all_six_requested_names() -> None:
    plan = service.build_company_rename_plan([old for old, _new in EXPECTED_RENAMES])

    assert service.COMPANY_RENAMES == EXPECTED_RENAMES
    assert plan == {
        "ok": True,
        "applicable": True,
        "renames": [
            {"old_name": old, "new_name": new}
            for old, new in EXPECTED_RENAMES
        ],
        "already_renamed": [],
        "conflicts": [],
    }


def test_company_rename_plan_is_idempotent_after_all_targets_exist() -> None:
    plan = service.build_company_rename_plan([new for _old, new in EXPECTED_RENAMES])

    assert plan["ok"] is True
    assert plan["applicable"] is True
    assert plan["renames"] == []
    assert plan["already_renamed"] == [new for _old, new in EXPECTED_RENAMES]


def test_company_rename_plan_blocks_the_entire_batch_on_collision_or_missing_company() -> None:
    existing = [old for old, _new in EXPECTED_RENAMES]
    existing.append(EXPECTED_RENAMES[0][1])
    existing.remove(EXPECTED_RENAMES[-1][0])

    plan = service.build_company_rename_plan(existing)

    assert plan["ok"] is False
    assert plan["renames"] == []
    assert plan["conflicts"] == [
        {
            "type": "TARGET_ALREADY_EXISTS",
            "old_name": EXPECTED_RENAMES[0][0],
            "new_name": EXPECTED_RENAMES[0][1],
        },
        {
            "type": "COMPANY_PAIR_MISSING",
            "old_name": EXPECTED_RENAMES[-1][0],
            "new_name": EXPECTED_RENAMES[-1][1],
        },
    ]


@pytest.mark.parametrize("companies", [[], ["Unrelated Company"], ["LATINGO MX"]])
def test_empty_unrelated_site_is_a_safe_noop(companies) -> None:
    plan = service.build_company_rename_plan(companies)

    assert plan == {
        "ok": True,
        "applicable": False,
        "renames": [],
        "already_renamed": [],
        "conflicts": [],
    }


def _current_company_names(*, migrated=False):
    return [
        "LATINGO MX" if migrated and new == "YW GRUPO MX" else new
        for _old, new in EXPECTED_RENAMES
    ]


def _stub_company_audit(monkeypatch, company_names):
    monkeypatch.setattr(service, "frappe", SimpleNamespace())
    monkeypatch.setattr(service, "_company_names", lambda: company_names)
    monkeypatch.setattr(service, "_company_link_fields", lambda: [
        ("Warehouse", "company"),
        ("Process Deferred Accounting", "company"),
        ("Overseas Cost Project Route", "subsidiary_code"),
    ])
    monkeypatch.setattr(service, "_company_dynamic_link_fields", lambda: [
        ("Comment", "reference_name", "reference_doctype", "Company")
    ])
    monkeypatch.setattr(service, "_field_exists", lambda *_args: True)
    monkeypatch.setattr(service, "_exact_reference_count", lambda *_args: 0)
    monkeypatch.setattr(service, "_conditional_reference_count", lambda *_args: 0)
    monkeypatch.setattr(service, "_user_settings_old_references", lambda *_args: [])


def test_migrated_site_does_not_recreate_grupo_or_rename_latingo(monkeypatch):
    names = _current_company_names(migrated=True)
    _stub_company_audit(monkeypatch, names)
    monkeypatch.setattr(service, "_rename_company", lambda *_args: pytest.fail("must not write"))
    plan = service.build_company_rename_plan(names)

    assert plan["ok"] is True
    assert plan["renames"] == []
    assert plan["already_renamed"] == names
    assert service.build_company_rename_plan(names) == plan
    assert service.rename_operating_companies()["applied"] is False


def test_missing_source_and_successor_cannot_pass_migration_acceptance(monkeypatch):
    names = [name for name in _current_company_names() if name != "YW GRUPO MX"]
    _stub_company_audit(monkeypatch, names)

    assert service.build_company_rename_plan(names)["ok"] is False
    with pytest.raises(service.CompanyRenameError, match="missing=YW GRUPO MX"):
        service.verify_company_renames_complete(require_latingo_migration=True)


def test_legacy_long_name_cannot_be_automatically_merged_into_existing_latingo(monkeypatch):
    names = _current_company_names(migrated=True) + ["YW Centro MX 共享中心 YUEWEI Grupo"]
    monkeypatch.setattr(service, "_company_names", lambda: names)
    monkeypatch.setattr(service, "_rename_company", lambda *_args: pytest.fail("must not write"))

    plan = service.build_company_rename_plan(names)
    assert plan["ok"] is False
    assert plan["renames"] == []
    assert plan["conflicts"] == [{
        "type": "TARGET_ALREADY_EXISTS",
        "old_name": "YW Centro MX 共享中心 YUEWEI Grupo",
        "new_name": "LATINGO MX",
    }]
    with pytest.raises(service.CompanyRenameError, match="TARGET_ALREADY_EXISTS"):
        service.rename_operating_companies()


@pytest.mark.parametrize("target_exists", [False, True])
def test_pre_migration_deployment_passes_but_explicit_migration_acceptance_fails(monkeypatch, target_exists):
    names = _current_company_names() + (["LATINGO MX"] if target_exists else [])
    _stub_company_audit(monkeypatch, names)
    monkeypatch.setattr(service, "_rename_company", lambda *_args: pytest.fail("must not write"))

    result = service.rename_operating_companies()
    assert result["applied"] is False
    assert result["renamed"] == []
    audit = service.verify_company_renames_complete()
    assert audit["ok"] is True
    assert audit["pending_company_migrations"] == [{
        "source_company": "YW GRUPO MX", "target_company": "LATINGO MX",
        "target_exists": target_exists,
    }]
    with pytest.raises(service.CompanyRenameError, match="LATINGO_COMPANY_MIGRATION_REQUIRED"):
        service.verify_company_renames_complete(require_latingo_migration=True)


def test_post_migration_audit_accepts_latingo_without_weakening_other_company_checks(monkeypatch):
    names = _current_company_names(migrated=True)
    _stub_company_audit(monkeypatch, names)
    audit = service.verify_company_renames_complete(require_latingo_migration=True)
    assert audit["ok"] is True
    assert audit["pending_company_migrations"] == []

    names.remove("LEMOS MX")
    with pytest.raises(service.CompanyRenameError, match="missing=LEMOS MX"):
        service.verify_company_renames_complete()


@pytest.mark.parametrize("doctype, field", [
    ("Warehouse", "company"),
    ("Process Deferred Accounting", "company"),
    ("Overseas Cost Project Route", "subsidiary_code"),
    ("Overseas Cost Batch", "subsidiary_code"),
    ("Overseas Cost Item", "subsidiary_code"),
    ("Overseas Cost ERP Site", "subsidiary_code"),
    ("DefaultValue", "defvalue"),
])
def test_post_migration_rejects_dangling_grupo_link_and_data_references(monkeypatch, doctype, field):
    _stub_company_audit(monkeypatch, _current_company_names(migrated=True))
    monkeypatch.setattr(service, "_exact_reference_count", lambda dt, key, value:
        1 if (dt, key, value) == (doctype, field, "YW GRUPO MX") else 0)

    with pytest.raises(service.CompanyRenameError, match=doctype):
        service.verify_company_renames_complete()


@pytest.mark.parametrize("doctype, field", [
    ("User Permission", "for_value"), ("DocShare", "share_name"), ("Comment", "reference_name"),
])
def test_post_migration_rejects_dangling_grupo_dynamic_references(monkeypatch, doctype, field):
    _stub_company_audit(monkeypatch, _current_company_names(migrated=True))
    monkeypatch.setattr(service, "_conditional_reference_count", lambda dt, key, value, *_args:
        1 if (dt, key, value) == (doctype, field, "YW GRUPO MX") else 0)

    with pytest.raises(service.CompanyRenameError, match=doctype):
        service.verify_company_renames_complete()


def test_post_migration_rejects_grupo_in_saved_filters(monkeypatch):
    _stub_company_audit(monkeypatch, _current_company_names(migrated=True))

    def saved_filters(old_names, company_links):
        assert "YW GRUPO MX" in old_names
        assert ("Warehouse", "company") in company_links
        return [{
            "doctype": "__UserSettings", "fieldname": "Warehouse.company",
            "value": "YW GRUPO MX", "count": 1,
        }]

    monkeypatch.setattr(service, "_user_settings_old_references", saved_filters)

    with pytest.raises(service.CompanyRenameError, match="__UserSettings"):
        service.verify_company_renames_complete()


def test_apply_renames_all_companies_then_rewrites_untyped_references_and_audits(monkeypatch) -> None:
    calls: list[tuple] = []
    old_names = [old for old, _new in EXPECTED_RENAMES]
    monkeypatch.setattr(service, "_company_names", lambda: old_names)
    monkeypatch.setattr(
        service,
        "_rename_company",
        lambda old, new: calls.append(("rename", old, new)),
    )
    monkeypatch.setattr(
        service,
        "_replace_untyped_company_references",
        lambda old, new: calls.append(("replace", old, new)),
    )
    monkeypatch.setattr(
        service,
        "_replace_user_settings_company_references",
        lambda old, new: calls.append(("user-settings", old, new)),
    )
    monkeypatch.setattr(
        service,
        "audit_company_renames",
        lambda: {"ok": True, "missing_targets": [], "old_company_records": [], "old_references": []},
    )

    result = service.rename_operating_companies()

    assert result["applied"] is True
    assert result["renamed"] == [new for _old, new in EXPECTED_RENAMES]
    assert calls == [
        call
        for old, new in EXPECTED_RENAMES
        for call in (
            ("rename", old, new),
            ("replace", old, new),
            ("user-settings", old, new),
        )
    ]


def test_apply_performs_zero_writes_when_preflight_finds_a_conflict(monkeypatch) -> None:
    old_names = [old for old, _new in EXPECTED_RENAMES]
    old_names.append(EXPECTED_RENAMES[0][1])
    monkeypatch.setattr(service, "_company_names", lambda: old_names)
    monkeypatch.setattr(
        service,
        "_rename_company",
        lambda *_args: pytest.fail("rename must not run after failed preflight"),
    )

    with pytest.raises(service.CompanyRenameError, match="TARGET_ALREADY_EXISTS"):
        service.rename_operating_companies()


def test_apply_raises_when_post_migration_audit_finds_an_old_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "_company_names",
        lambda: [new for _old, new in EXPECTED_RENAMES],
    )
    monkeypatch.setattr(
        service,
        "audit_company_renames",
        lambda: {
            "ok": False,
            "missing_targets": [],
            "old_company_records": [],
            "old_references": [
                {
                    "doctype": "Warehouse",
                    "fieldname": "company",
                    "value": EXPECTED_RENAMES[0][0],
                    "count": 1,
                }
            ],
        },
    )

    with pytest.raises(service.CompanyRenameError, match="Warehouse.company"):
        service.rename_operating_companies()


def test_non_link_reference_fields_cover_saved_targets_and_defaults() -> None:
    assert service.UNTYPED_COMPANY_REFERENCE_FIELDS == (
        ("Company", "company_name"),
        ("Overseas Cost ERP Site", "subsidiary_code"),
        ("Overseas Cost Batch", "subsidiary_code"),
        ("Overseas Cost Item", "subsidiary_code"),
        ("DefaultValue", "defvalue"),
    )


def test_audit_includes_company_links_stored_in_single_doctypes(monkeypatch) -> None:
    old_name = EXPECTED_RENAMES[0][0]

    class FakeDB:
        @staticmethod
        def table_exists(doctype: str) -> bool:
            return doctype == "Company"

        @staticmethod
        def has_column(doctype: str, fieldname: str) -> bool:
            return (doctype, fieldname) == ("Company", "company_name")

        @staticmethod
        def exists(doctype: str, name: str) -> bool:
            return (doctype, name) == ("DocType", "Stock Settings")

        @staticmethod
        def get_single_value(doctype: str, fieldname: str):
            assert (doctype, fieldname) == ("Stock Settings", "default_company")
            return old_name

        @staticmethod
        def count(_doctype: str, _filters: dict) -> int:
            return 0

        @staticmethod
        def sql(_query: str, _values, as_dict: bool):
            assert as_dict is True
            return []

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype: str, **_kwargs):
            if doctype == "Company":
                return [new for _old, new in EXPECTED_RENAMES]
            if doctype == "DocField":
                return [{"parent": "Stock Settings", "fieldname": "default_company"}]
            if doctype == "Custom Field":
                return []
            raise AssertionError(doctype)

        @staticmethod
        def get_meta(doctype: str):
            if doctype == "Stock Settings":
                return SimpleNamespace(
                    issingle=True,
                    has_field=lambda fieldname: fieldname == "default_company",
                )
            if doctype == "Company":
                return SimpleNamespace(issingle=False)
            raise AssertionError(doctype)

    monkeypatch.setattr(service, "frappe", FakeFrappe())

    audit = service.audit_company_renames()

    assert audit["ok"] is False
    assert audit["old_references"] == [
        {
            "doctype": "Stock Settings",
            "fieldname": "default_company",
            "value": old_name,
            "count": 1,
        }
    ]


def test_rename_uses_the_model_api_that_accepts_ignore_permissions(monkeypatch) -> None:
    calls: list[tuple] = []

    def rename_doc(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(service, "frappe", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "frappe.model", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "frappe.model.rename_doc",
        SimpleNamespace(rename_doc=rename_doc),
    )

    service._rename_company("Old Company", "New Company")

    assert calls == [
        (
            ("Company", "Old Company", "New Company"),
            {
                "force": False,
                "merge": False,
                "ignore_permissions": True,
            },
        )
    ]


def test_verification_command_fails_when_any_old_reference_remains(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "audit_company_renames",
        lambda: {
            "ok": False,
            "missing_targets": [],
            "old_company_records": [],
            "old_references": [
                {
                    "doctype": "Stock Ledger Entry",
                    "fieldname": "company",
                    "value": EXPECTED_RENAMES[0][0],
                    "count": 2,
                }
            ],
        },
    )

    with pytest.raises(service.CompanyRenameError, match="Stock Ledger Entry.company"):
        service.verify_company_renames_complete()


def test_audit_includes_dynamic_links_that_point_to_company(monkeypatch) -> None:
    old_name = EXPECTED_RENAMES[1][0]

    class FakeDB:
        @staticmethod
        def table_exists(doctype: str) -> bool:
            return doctype == "ToDo"

        @staticmethod
        def has_column(doctype: str, fieldname: str) -> bool:
            return doctype == "ToDo" and fieldname in {"reference_type", "reference_name"}

        @staticmethod
        def exists(_doctype: str, _name: str) -> bool:
            return False

        @staticmethod
        def count(doctype: str, filters: dict) -> int:
            if doctype == "ToDo" and filters == {
                "reference_name": old_name,
                "reference_type": "Company",
            }:
                return 3
            return 0

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype: str, **kwargs):
            if doctype == "Company":
                return [new for _old, new in EXPECTED_RENAMES]
            if doctype == "DocField":
                if kwargs["filters"]["fieldtype"] == "Dynamic Link":
                    return [
                        {
                            "parent": "ToDo",
                            "fieldname": "reference_name",
                            "options": "reference_type",
                        }
                    ]
                return []
            if doctype == "Custom Field":
                return []
            raise AssertionError(doctype)

        @staticmethod
        def get_meta(doctype: str):
            assert doctype == "ToDo"
            return SimpleNamespace(issingle=False)

    monkeypatch.setattr(service, "frappe", FakeFrappe())

    audit = service.audit_company_renames()

    assert audit["old_references"] == [
        {
            "doctype": "ToDo",
            "fieldname": "reference_name",
            "value": old_name,
            "count": 3,
        }
    ]


def test_audit_includes_dynamic_links_stored_in_single_doctypes(monkeypatch) -> None:
    old_name = EXPECTED_RENAMES[1][0]

    class FakeDB:
        @staticmethod
        def table_exists(_doctype: str) -> bool:
            return False

        @staticmethod
        def exists(doctype: str, name: str) -> bool:
            return (doctype, name) == ("DocType", "Integration Settings")

        @staticmethod
        def get_single_value(doctype: str, fieldname: str):
            assert doctype == "Integration Settings"
            return {
                "reference_type": "Company",
                "reference_name": old_name,
            }[fieldname]

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_meta(doctype: str):
            assert doctype == "Integration Settings"
            return SimpleNamespace(
                issingle=True,
                has_field=lambda fieldname: fieldname in {"reference_type", "reference_name"},
            )

    monkeypatch.setattr(service, "frappe", FakeFrappe())

    assert service._conditional_reference_count(
        "Integration Settings",
        "reference_name",
        old_name,
        "reference_type",
        "Company",
    ) == 1


def test_audit_checks_frappe_internal_user_settings_filters(monkeypatch) -> None:
    old_name = EXPECTED_RENAMES[2][0]
    user_settings = {
        "List": {
            "filters": [
                ["Warehouse", "company", "=", old_name, False],
                ["Warehouse", "warehouse_name", "like", "%MX%", False],
            ]
        }
    }

    class FakeDB:
        @staticmethod
        def sql(query: str, values, as_dict: bool):
            assert "`__UserSettings`" in query
            assert "doctype in" in query
            assert as_dict is True
            assert values == ("Warehouse",)
            return [
                {
                    "user": "stock@example.com",
                    "doctype": "Warehouse",
                    "data": __import__("json").dumps(user_settings),
                }
            ]

    monkeypatch.setattr(service, "frappe", SimpleNamespace(db=FakeDB()))
    monkeypatch.setattr(
        service,
        "_company_names",
        lambda: [new for _old, new in EXPECTED_RENAMES],
    )
    monkeypatch.setattr(
        service,
        "_company_link_fields",
        lambda: [("Warehouse", "company")],
    )
    monkeypatch.setattr(service, "_company_dynamic_link_fields", lambda: [])
    monkeypatch.setattr(service, "_field_exists", lambda *_args: False)

    audit = service.audit_company_renames()

    assert audit["old_references"] == [
        {
            "doctype": "__UserSettings",
            "fieldname": "Warehouse.company",
            "value": old_name,
            "count": 1,
        }
    ]


def test_user_settings_migration_updates_scalar_and_multi_value_company_filters(monkeypatch) -> None:
    old_name, new_name = EXPECTED_RENAMES[0]
    user_settings = {
        "List": {
            "filters": [
                ["Warehouse", "company", "=", old_name, False],
                ["Warehouse", "company", "in", ["Other Company", old_name], False],
                ["Warehouse", "warehouse_name", "like", old_name, False],
            ]
        }
    }
    updates: list[tuple] = []
    cache_calls: list[tuple] = []

    class FakeDB:
        @staticmethod
        def sql(query: str, values, as_dict: bool = False):
            if query.startswith("select user"):
                assert values == ("Warehouse",)
                assert as_dict is True
                return [
                    {
                        "user": "stock@example.com",
                        "doctype": "Warehouse",
                        "data": __import__("json").dumps(user_settings),
                    }
                ]
            updates.append((query, values))
            return []

    class FakeCache:
        @staticmethod
        def hset(*args):
            cache_calls.append(args)

    monkeypatch.setattr(
        service,
        "frappe",
        SimpleNamespace(db=FakeDB(), cache=FakeCache()),
    )
    monkeypatch.setattr(
        service,
        "_company_link_fields",
        lambda: [("Warehouse", "company")],
    )

    service._replace_user_settings_company_references(old_name, new_name)

    assert len(updates) == 1
    rewritten = __import__("json").loads(updates[0][1][0])
    assert rewritten["List"]["filters"] == [
        ["Warehouse", "company", "=", new_name, False],
        ["Warehouse", "company", "in", ["Other Company", new_name], False],
        ["Warehouse", "warehouse_name", "like", old_name, False],
    ]
    assert cache_calls == [
        ("_user_settings", "Warehouse::stock@example.com", None)
    ]


def test_user_settings_audit_detects_old_company_in_multi_value_filter(monkeypatch) -> None:
    old_name = EXPECTED_RENAMES[3][0]
    data = {
        "Report": {
            "filters": [
                ["Stock Ledger Entry", "company", "not in", ["Other", old_name], False]
            ]
        }
    }

    class FakeDB:
        @staticmethod
        def sql(_query: str, _values, as_dict: bool):
            assert as_dict is True
            return [
                {
                    "user": "audit@example.com",
                    "doctype": "Stock Ledger Entry",
                    "data": __import__("json").dumps(data),
                }
            ]

    monkeypatch.setattr(service, "frappe", SimpleNamespace(db=FakeDB()))

    residuals = service._user_settings_old_references(
        [old_name],
        {("Stock Ledger Entry", "company")},
    )

    assert residuals == [
        {
            "doctype": "__UserSettings",
            "fieldname": "Stock Ledger Entry.company",
            "value": old_name,
            "count": 1,
        }
    ]
