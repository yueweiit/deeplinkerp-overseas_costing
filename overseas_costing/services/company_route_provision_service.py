"""Preflight and provision international-logistics Companies and project routes."""

from __future__ import annotations

from overseas_costing.services.company_rename_service import (
    GRUPO_COMPANY,
    GRUPO_LEGACY_COMPANY,
    LATINGO_ABBR,
    LATINGO_COMPANY,
)

try:
    import frappe
except ImportError:
    frappe = None


EXISTING_COMPANY_ROUTES = {
    "悦为智能 YW Tech_AI": "悦为智能技术（东莞）有限公司",
    "Guangzhou Lingxiang 广州凌翔": "广州凌翔电子产品有限公司",
    "LatinGo拉丁购": "拉丁购国际电子商务（东莞）有限公司",
    "Dongguan Xingming东莞星铭": "东莞市星铭贸易有限公司",
}

MEXICO_COMPANIES = (
    ("YW MOLDES", "MOLD"),
    ("LEMOS MX", "LEMO"),
    ("UV IMPRESION", "UVIM"),
    ("YUEWEI MX", "YWFM"),
    ("AmigoMart", "AMIG"),
    (LATINGO_COMPANY, LATINGO_ABBR),
)

# ``project_collection`` is the external OA/DingTalk source label.  Keep those
# historical labels stable while routing them to the renamed ERP Companies.
MEXICO_PROJECT_ROUTES = {
    "YW MOLDES MX模具": "YW MOLDES",
    "LEMOS MX供应链开发及管理": "LEMOS MX",
    "UV IMPRESION MX彩印": "UV IMPRESION",
    "YW Fabricación MX 核心制造": "YUEWEI MX",
    "AmigoMart": "AmigoMart",
    GRUPO_LEGACY_COMPANY: LATINGO_COMPANY,
}

ALL_PROJECT_ROUTES = {
    **EXISTING_COMPANY_ROUTES,
    **MEXICO_PROJECT_ROUTES,
}
DEFAULT_ROUTE_AI_HINTS = {"LatinGo拉丁购": "宠物用品"}
# ErpNext 的仓库自动命名为“<仓库名> - <公司缩写>”；收货仓只认这两个仓库名。
RECEIVING_WAREHOUSE_NAMES = ("仓库", "Stores")


def build_provision_plan(existing_companies: list[dict], existing_routes: list[dict]) -> dict:
    """Return the complete create/skip/conflict set before any database write."""

    companies_by_name = {_text(row.get("name") or row.get("company_name")): row for row in existing_companies}
    companies_by_abbr = {_text(row.get("abbr")): row for row in existing_companies if _text(row.get("abbr"))}
    conflicts = []
    create_companies = []
    skipped_companies = []

    # Provisioning is not a migration command.  Even an apparently empty old
    # Company can own submitted records, permissions or shared configuration.
    for old_company in (GRUPO_LEGACY_COMPANY, GRUPO_COMPANY):
        if old_company in companies_by_name:
            conflicts.append({
                "type": "COMPANY_MIGRATION_REQUIRED",
                "company": old_company,
                "target_company": LATINGO_COMPANY,
            })

    for company_name in EXISTING_COMPANY_ROUTES.values():
        if company_name not in companies_by_name:
            conflicts.append({"type": "MISSING_EXISTING_COMPANY", "company": company_name})

    for company_name, abbr in MEXICO_COMPANIES:
        current = companies_by_name.get(company_name)
        if current:
            mismatches = {
                field: {"expected": expected, "actual": _normal_value(current.get(field))}
                for field, expected in (
                    ("abbr", abbr),
                    ("country", "Mexico"),
                    ("default_currency", "MXN"),
                    ("is_group", 0),
                    ("parent_company", ""),
                )
                if _normal_value(current.get(field)) != expected
            }
            if mismatches:
                conflicts.append({"type": "COMPANY_FIELD_CONFLICT", "company": company_name, "fields": mismatches})
            else:
                skipped_companies.append(company_name)
            continue
        abbreviation_owner = companies_by_abbr.get(abbr)
        if abbreviation_owner:
            conflicts.append(
                {"type": "COMPANY_ABBR_CONFLICT", "company": company_name, "abbr": abbr,
                 "existing_company": _text(abbreviation_owner.get("name") or abbreviation_owner.get("company_name"))}
            )
        else:
            create_companies.append({"company_name": company_name, "abbr": abbr})

    active_routes = {}
    for route in existing_routes:
        if route.get("enabled") in (0, False, "0"):
            continue
        project = _text(route.get("project_collection"))
        if project:
            active_routes.setdefault(project, set()).add(
                (_text(route.get("subsidiary_code")), _text(route.get("erp_site")))
            )

    create_routes = []
    skipped_routes = []
    for project, company in ALL_PROJECT_ROUTES.items():
        targets = active_routes.get(project, set())
        if not targets:
            create_routes.append(
                {
                    "project_collection": project,
                    "subsidiary_code": company,
                    "erp_site": "",
                    "ai_match_hint": DEFAULT_ROUTE_AI_HINTS.get(project, ""),
                }
            )
        elif targets == {(company, "")}:
            skipped_routes.append(project)
        else:
            conflicts.append({"type": "PROJECT_ROUTE_CONFLICT", "project_collection": project, "targets": sorted(targets)})

    return {
        "ok": not conflicts,
        "conflicts": conflicts,
        "create_companies": create_companies,
        "skip_companies": skipped_companies,
        "create_routes": create_routes,
        "skip_routes": skipped_routes,
    }


def ensure_route_defaults() -> dict:
    """Seed missing route business defaults without overwriting administrator content.

    每个路由补齐两类默认值：AI 匹配提示、收货仓库。仓库名由 ERP 自己的
    ``<仓库名> - <公司缩写>`` 命名推导；推不出唯一结果时留空并登记，绝不猜。
    """

    _require_frappe()
    updated = []
    warehouse_updated = []
    unresolved = []
    routes = frappe.get_all(
        "Overseas Cost Project Route",
        filters={"enabled": 1},
        fields=["name", "project_collection", "subsidiary_code", "ai_match_hint", "warehouse", "enabled"],
        limit_page_length=10000,
    )
    warehouses_by_company = _warehouse_names_by_company()
    company_abbrs = _company_abbrs()
    for route in routes:
        if route.get("enabled") in (0, False, "0"):
            continue
        name = _text(route.get("name"))
        hint = DEFAULT_ROUTE_AI_HINTS.get(_text(route.get("project_collection")))
        if hint and not _text(route.get("ai_match_hint")):
            _set_route_value(name, "ai_match_hint", hint)
            updated.append(name)
        if _text(route.get("warehouse")):
            continue
        warehouse = _receiving_warehouse(route.get("subsidiary_code"), company_abbrs, warehouses_by_company)
        if warehouse:
            _set_route_value(name, "warehouse", warehouse)
            warehouse_updated.append(name)
        else:
            unresolved.append({"route": name, "subsidiary_code": _text(route.get("subsidiary_code"))})
    return {"updated": updated, "warehouse_updated": warehouse_updated, "unresolved_warehouse": unresolved}


def _receiving_warehouse(subsidiary_code, company_abbrs: dict[str, str], warehouses_by_company: dict[str, set[str]]) -> str:
    company = _text(subsidiary_code)
    available = warehouses_by_company.get(company) or set()
    abbr = _text(company_abbrs.get(company))
    if not available or not abbr:
        return ""
    candidates = [f"{warehouse_name} - {abbr}" for warehouse_name in RECEIVING_WAREHOUSE_NAMES]
    matched = [name for name in candidates if name in available]
    return matched[0] if len(matched) == 1 else ""


def _warehouse_names_by_company() -> dict[str, set[str]]:
    rows = frappe.get_all(
        "Warehouse", filters={"is_group": 0}, fields=["name", "company"], limit_page_length=0
    )
    by_company: dict[str, set[str]] = {}
    for row in rows:
        by_company.setdefault(_text(row.get("company")), set()).add(_text(row.get("name")))
    return by_company


def _company_abbrs() -> dict[str, str]:
    rows = frappe.get_all("Company", fields=["name", "abbr"], limit_page_length=0)
    return {_text(row.get("name")): _text(row.get("abbr")) for row in rows}


def _set_route_value(name: str, fieldname: str, value) -> None:
    frappe.db.set_value("Overseas Cost Project Route", name, fieldname, value, update_modified=False)


def provision_company_routes(*, dry_run: bool = True) -> dict:
    """System Manager command; preflight everything, then insert in one transaction."""

    _require_frappe()
    frappe.only_for("System Manager")
    companies = frappe.get_all(
        "Company",
        fields=["name", "company_name", "abbr", "country", "default_currency", "is_group", "parent_company"],
        limit_page_length=10000,
    )
    routes = frappe.get_all(
        "Overseas Cost Project Route",
        fields=["name", "project_collection", "subsidiary_code", "erp_site", "enabled", "ai_match_hint"],
        limit_page_length=10000,
    )
    plan = build_provision_plan(companies, routes)
    if dry_run or not plan["ok"]:
        return {**plan, "dry_run": True, "applied": False}

    try:
        for values in plan["create_companies"]:
            frappe.get_doc(
                {
                    "doctype": "Company",
                    **values,
                    "country": "Mexico",
                    "default_currency": "MXN",
                    "is_group": 0,
                    "parent_company": "",
                    "create_chart_of_accounts_based_on": "Standard Template",
                    "chart_of_accounts": "Standard",
                    "enable_perpetual_inventory": 0,
                }
            ).insert(ignore_permissions=True)
        for values in plan["create_routes"]:
            frappe.get_doc(
                {"doctype": "Overseas Cost Project Route", **values, "enabled": 1, "revision": 1,
                 "remark": "国际物流项目归属到 ERP Company 路由"}
            ).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    return {**plan, "dry_run": False, "applied": True}


def _normal_value(value):
    if value in (None, False):
        return "" if value is None else 0
    if value in (True, "1", 1):
        return 1
    if value in ("0", 0):
        return 0
    return _text(value)


def _text(value) -> str:
    return str(value or "").strip()


def _require_frappe() -> None:
    if frappe is None:
        raise RuntimeError("Frappe 运行环境不可用。")
