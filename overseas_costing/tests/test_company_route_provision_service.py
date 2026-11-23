import pytest

from overseas_costing.services import company_route_provision_service as service
from overseas_costing.services.company_route_provision_service import (
    EXISTING_COMPANY_ROUTES,
    MEXICO_COMPANIES,
    MEXICO_PROJECT_ROUTES,
    build_provision_plan,
)


def _existing_core_companies():
    return [{"name": company} for company in EXISTING_COMPANY_ROUTES.values()]


def _mexico_company(name, abbr):
    return {"name": name, "abbr": abbr, "country": "Mexico", "default_currency": "MXN", "is_group": 0, "parent_company": ""}


def test_preflight_lists_six_companies_and_ten_routes_without_writes() -> None:
    plan = build_provision_plan(_existing_core_companies(), [])

    assert plan["ok"] is True
    assert len(plan["create_companies"]) == 6
    assert len(plan["create_routes"]) == 10
    latin = next(row for row in plan["create_routes"] if row["project_collection"] == "LatinGo拉丁购")
    assert latin["ai_match_hint"] == "宠物用品"


def test_preflight_repeated_run_skips_everything() -> None:
    companies = _existing_core_companies() + [_mexico_company(name, abbr) for name, abbr in MEXICO_COMPANIES]
    routes = [
        {"project_collection": project, "subsidiary_code": company, "erp_site": "", "enabled": 1}
        for project, company in {**EXISTING_COMPANY_ROUTES, **MEXICO_PROJECT_ROUTES}.items()
    ]

    plan = build_provision_plan(companies, routes)

    assert plan["ok"] is True
    assert plan["create_companies"] == []
    assert plan["create_routes"] == []
    assert len(plan["skip_routes"]) == 10


def test_preflight_company_conflict_blocks_entire_plan() -> None:
    companies = _existing_core_companies() + [
        {"name": "OTHER", "abbr": "LEMO"},
        _mexico_company("YW MOLDES", "WRONG"),
    ]

    plan = build_provision_plan(companies, [])

    assert plan["ok"] is False
    assert {row["type"] for row in plan["conflicts"]} == {"COMPANY_FIELD_CONFLICT", "COMPANY_ABBR_CONFLICT"}


def test_preflight_preserves_source_project_label_and_targets_renamed_lemos_company() -> None:
    plan = build_provision_plan(_existing_core_companies(), [])
    route = next(
        row
        for row in plan["create_routes"]
        if row["project_collection"] == "LEMOS MX供应链开发及管理"
    )

    assert MEXICO_PROJECT_ROUTES["LEMOS MX供应链开发及管理"] == "LEMOS MX"
    assert route["subsidiary_code"] == "LEMOS MX"
    assert "深圳柠檬树" not in route["subsidiary_code"]


def test_new_provisioning_uses_latingo_mx_without_changing_china_latingo_route():
    plan = build_provision_plan(_existing_core_companies(), [])

    assert {"company_name": "LATINGO MX", "abbr": "LATMX"} in plan["create_companies"]
    assert not any(row["company_name"] == "YW GRUPO MX" for row in plan["create_companies"])
    targets = {row["project_collection"]: row["subsidiary_code"] for row in plan["create_routes"]}
    assert targets["YW Centro MX 共享中心 YUEWEI Grupo"] == "LATINGO MX"
    assert targets["LatinGo拉丁购"] == "拉丁购国际电子商务（东莞）有限公司"


def test_latingo_abbreviation_conflict_blocks_all_provisioning(monkeypatch):
    fake = _Frappe(_existing_core_companies() + [_mexico_company("LATINGO MX", "YWCM")])
    monkeypatch.setattr(service, "frappe", fake)

    result = service.provision_company_routes(dry_run=False)

    assert result["ok"] is False
    assert result["conflicts"] == [{
        "type": "COMPANY_FIELD_CONFLICT", "company": "LATINGO MX",
        "fields": {"abbr": {"expected": "LATMX", "actual": "YWCM"}},
    }]
    assert fake.writes == []
    assert fake.db.commits == 0


@pytest.mark.parametrize("old_name", ["YW GRUPO MX", "YW Centro MX 共享中心 YUEWEI Grupo"])
@pytest.mark.parametrize("target_exists", [False, True])
def test_old_company_requires_explicit_migration_before_any_provisioning(monkeypatch, old_name, target_exists):
    companies = _existing_core_companies() + [_mexico_company(old_name, "YWCM")]
    if target_exists:
        companies.append(_mexico_company("LATINGO MX", "LATMX"))
    fake = _Frappe(companies)
    monkeypatch.setattr(service, "frappe", fake)

    result = service.provision_company_routes(dry_run=False)

    assert result["ok"] is False
    assert result["applied"] is False
    assert {"type": "COMPANY_MIGRATION_REQUIRED", "company": old_name,
        "target_company": "LATINGO MX"} in result["conflicts"]
    assert fake.writes == []
    assert fake.db.commits == 0


def test_dangling_old_route_is_not_overwritten_or_duplicated_after_company_migration(monkeypatch):
    companies = _existing_core_companies() + [_mexico_company(name, abbr) for name, abbr in MEXICO_COMPANIES]
    fake = _Frappe(companies)
    fake.routes = [{"project_collection": "YW Centro MX 共享中心 YUEWEI Grupo",
        "subsidiary_code": "YW GRUPO MX", "erp_site": "", "enabled": 1}]
    monkeypatch.setattr(service, "frappe", fake)

    result = service.provision_company_routes(dry_run=False)

    assert result["ok"] is False
    assert result["conflicts"][0]["type"] == "PROJECT_ROUTE_CONFLICT"
    assert fake.routes[0]["subsidiary_code"] == "YW GRUPO MX"
    assert fake.writes == []
    assert fake.db.commits == 0


def test_route_defaults_choose_latingo_warehouse_without_retargeting_existing_routes(monkeypatch):
    fake = _Frappe([_mexico_company("LATINGO MX", "LATMX"), _mexico_company("YW GRUPO MX", "YWCM")],
        warehouses=[{"name": "Stores - LATMX", "company": "LATINGO MX"},
            {"name": "Stores - YWCM", "company": "YW GRUPO MX"}])
    fake.routes = [
        {"name": "MIGRATED", "project_collection": "YW Centro MX 共享中心 YUEWEI Grupo",
            "subsidiary_code": "LATINGO MX", "warehouse": "", "enabled": 1},
        {"name": "OLD", "project_collection": "YW Centro MX 共享中心 YUEWEI Grupo",
            "subsidiary_code": "YW GRUPO MX", "warehouse": "Stores - YWCM", "enabled": 1},
    ]
    monkeypatch.setattr(service, "frappe", fake)

    first = service.ensure_route_defaults()
    second = service.ensure_route_defaults()

    assert first["warehouse_updated"] == ["MIGRATED"]
    assert fake.routes[0]["warehouse"] == "Stores - LATMX"
    assert fake.routes[1]["subsidiary_code"] == "YW GRUPO MX"
    assert fake.routes[1]["warehouse"] == "Stores - YWCM"
    assert second["warehouse_updated"] == []


class _DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def set_value(self, doctype, name, fieldname, value, update_modified=False):
        assert doctype == "Overseas Cost Project Route"
        route = next(row for row in self.owner.routes if row.get("name") == name)
        route[fieldname] = value
        self.owner.hint_writes.append((name, fieldname, value, update_modified))


class _Doc:
    def __init__(self, fake, values):
        self.fake = fake
        self.values = values

    def insert(self, ignore_permissions=True):
        self.fake.writes.append(dict(self.values))
        if self.values["doctype"] == "Company":
            self.fake.companies.append({"name": self.values["company_name"], **self.values})
        else:
            self.fake.routes.append(dict(self.values))
        return self


class _Frappe:
    def __init__(self, companies, warehouses=None):
        self.companies = list(companies)
        self.warehouses = list(warehouses or [])
        self.routes = []
        self.writes = []
        self.hint_writes = []
        self.db = _DB()
        self.db.owner = self

    def only_for(self, role):
        assert role == "System Manager"

    def get_all(self, doctype, **kwargs):
        if doctype == "Company":
            return list(self.companies)
        if doctype == "Warehouse":
            return list(self.warehouses)
        return list(self.routes)

    def get_doc(self, values):
        return _Doc(self, values)


def test_management_command_applies_once_then_is_idempotent(monkeypatch) -> None:
    fake = _Frappe(_existing_core_companies())
    monkeypatch.setattr(service, "frappe", fake)

    first = service.provision_company_routes(dry_run=False)
    second = service.provision_company_routes(dry_run=False)

    assert first["applied"] is True
    assert len(fake.writes) == 16
    assert second["create_companies"] == []
    assert second["create_routes"] == []
    assert fake.db.commits == 2


def test_management_command_conflict_performs_zero_writes(monkeypatch) -> None:
    fake = _Frappe(_existing_core_companies() + [{"name": "OTHER", "abbr": "MOLD"}])
    monkeypatch.setattr(service, "frappe", fake)

    result = service.provision_company_routes(dry_run=False)

    assert result["ok"] is False
    assert result["applied"] is False
    assert fake.writes == []
    assert fake.db.commits == 0


def test_route_defaults_seed_hint_and_receiving_warehouse_without_overwriting_admin_values(monkeypatch) -> None:
    fake = _Frappe(
        [
            {"name": "拉丁购国际电子商务（东莞）有限公司", "abbr": "拉丁购"},
            {"name": "AmigoMart", "abbr": "AMIG"},
        ],
        warehouses=[
            {"name": "仓库 - 拉丁购", "company": "拉丁购国际电子商务（东莞）有限公司"},
            {"name": "在途物料 - 拉丁购", "company": "拉丁购国际电子商务（东莞）有限公司"},
            {"name": "Stores - AMIG", "company": "AmigoMart"},
        ],
    )
    fake.routes = [
        {"name": "ROUTE-LATIN", "project_collection": "LatinGo拉丁购", "subsidiary_code": "拉丁购国际电子商务（东莞）有限公司", "ai_match_hint": "", "warehouse": ""},
        {"name": "ROUTE-ADMIN", "project_collection": "LatinGo拉丁购", "subsidiary_code": "拉丁购国际电子商务（东莞）有限公司", "ai_match_hint": "管理员自定义", "warehouse": "仓库 - 管理员指定"},
        {"name": "ROUTE-AMIGO", "project_collection": "AmigoMart", "subsidiary_code": "AmigoMart", "ai_match_hint": "", "warehouse": ""},
        {"name": "ROUTE-NOWHERE", "project_collection": "无仓库公司", "subsidiary_code": "某公司", "ai_match_hint": "", "warehouse": ""},
    ]
    monkeypatch.setattr(service, "frappe", fake)

    first = service.ensure_route_defaults()
    second = service.ensure_route_defaults()

    assert first == {
        "updated": ["ROUTE-LATIN"],
        "warehouse_updated": ["ROUTE-LATIN", "ROUTE-AMIGO"],
        "unresolved_warehouse": [{"route": "ROUTE-NOWHERE", "subsidiary_code": "某公司"}],
    }
    assert second["updated"] == []
    assert second["warehouse_updated"] == []
    assert second["unresolved_warehouse"] == [{"route": "ROUTE-NOWHERE", "subsidiary_code": "某公司"}]
    assert fake.routes[0]["ai_match_hint"] == "宠物用品"
    assert fake.routes[0]["warehouse"] == "仓库 - 拉丁购"
    assert fake.routes[1]["ai_match_hint"] == "管理员自定义"
    assert fake.routes[1]["warehouse"] == "仓库 - 管理员指定"
    assert fake.routes[2]["warehouse"] == "Stores - AMIG"
