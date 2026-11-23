"""Rename operating companies without breaking ERP references.

The company document name is the primary key used by ERPNext link fields.  This
module deliberately uses Frappe's rename API for those fields, then repairs the
few historical ``Data`` fields in this app that also store the company name.
External project labels are not rewritten: ``project_collection`` is source
data from OA/DingTalk and is not a Company reference.
"""

from __future__ import annotations

import json

try:
    import frappe
except ImportError:  # pragma: no cover - exercised only outside a Frappe site
    frappe = None


COMPANY_RENAMES = (
    ("YW Fabricación MX 核心制造", "YUEWEI MX"),
    ("LEMOS MX供应链开发及管理", "LEMOS MX"),
    ("UV IMPRESION MX彩印", "UV IMPRESION"),
    ("YW Centro MX 共享中心 YUEWEI Grupo", "YW GRUPO MX"),
    ("YW MOLDES MX模具", "YW MOLDES"),
    ("YUEWEI CN悦为中国", "YW 中国共享中心"),
)

# The historical patch must not perform the later, separately approved company
# consolidation.  Accept its successor only after the old Company is absent.
GRUPO_LEGACY_COMPANY = "YW Centro MX 共享中心 YUEWEI Grupo"
GRUPO_COMPANY = "YW GRUPO MX"
LATINGO_COMPANY = "LATINGO MX"
LATINGO_ABBR = "LATMX"

# These fields are intentionally Data rather than Link fields, so
# ``frappe.rename_doc`` cannot discover them from metadata.
UNTYPED_COMPANY_REFERENCE_FIELDS = (
    ("Company", "company_name"),
    ("Overseas Cost ERP Site", "subsidiary_code"),
    ("Overseas Cost Batch", "subsidiary_code"),
    ("Overseas Cost Item", "subsidiary_code"),
    ("DefaultValue", "defvalue"),
)

# Dynamic references whose target DocType is stored in a companion column.
CONDITIONAL_COMPANY_REFERENCE_FIELDS = (
    ("User Permission", "for_value", "allow", "Company"),
    ("DocShare", "share_name", "share_doctype", "Company"),
)

USER_SETTINGS_VIEWS = ("List", "Gantt", "Kanban", "Calendar", "Image", "Inbox", "Report")


class CompanyRenameError(RuntimeError):
    """Raised before commit when a six-company rename cannot be proven safe."""


def _effective_company_renames(company_names: set[str]) -> tuple[tuple[str, str], ...]:
    """Keep the historical targets until the explicit consolidation is done."""

    if GRUPO_COMPANY not in company_names and LATINGO_COMPANY in company_names:
        return tuple(
            (old, LATINGO_COMPANY if new == GRUPO_COMPANY else new)
            for old, new in COMPANY_RENAMES
        )
    return COMPANY_RENAMES


def build_company_rename_plan(existing_company_names) -> dict:
    """Preflight the whole rename set before allowing the first write."""

    existing = {str(name or "").strip() for name in existing_company_names}
    # LATINGO alone does not opt an otherwise unrelated site into this old patch.
    managed_names = {name for pair in COMPANY_RENAMES for name in pair}
    if not existing.intersection(managed_names):
        return {
            "ok": True,
            "applicable": False,
            "renames": [],
            "already_renamed": [],
            "conflicts": [],
        }

    renames = []
    already_renamed = []
    conflicts = []
    for old_name, new_name in _effective_company_renames(existing):
        old_exists = old_name in existing
        new_exists = new_name in existing
        if old_exists and new_exists:
            conflicts.append(
                {
                    "type": "TARGET_ALREADY_EXISTS",
                    "old_name": old_name,
                    "new_name": new_name,
                }
            )
        elif old_exists:
            renames.append({"old_name": old_name, "new_name": new_name})
        elif new_exists:
            already_renamed.append(new_name)
        else:
            conflicts.append(
                {
                    "type": "COMPANY_PAIR_MISSING",
                    "old_name": old_name,
                    "new_name": new_name,
                }
            )

    if conflicts:
        renames = []
    return {
        "ok": not conflicts,
        "applicable": True,
        "renames": renames,
        "already_renamed": already_renamed,
        "conflicts": conflicts,
    }


def rename_operating_companies() -> dict:
    """Apply the six renames atomically and verify every Company link."""

    plan = build_company_rename_plan(_company_names())
    if not plan["applicable"]:
        return {**plan, "applied": False, "renamed": [], "audit": {"ok": True}}
    if not plan["ok"]:
        raise CompanyRenameError(
            "Company rename preflight failed: "
            + json.dumps(plan["conflicts"], ensure_ascii=False, sort_keys=True)
        )

    try:
        for row in plan["renames"]:
            old_name = row["old_name"]
            new_name = row["new_name"]
            _rename_company(old_name, new_name)
            _replace_untyped_company_references(old_name, new_name)
            _replace_user_settings_company_references(old_name, new_name)

        audit = verify_company_renames_complete()
    except Exception:
        _rollback_safely()
        raise

    return {
        **plan,
        "applied": bool(plan["renames"]),
        "renamed": [row["new_name"] for row in plan["renames"]],
        "audit": audit,
    }


def audit_company_renames() -> dict:
    """Return residual active references to any retired Company primary key."""

    _require_frappe()
    company_names = set(_company_names())
    effective_renames = _effective_company_renames(company_names)
    old_names = [old for old, _new in COMPANY_RENAMES]
    new_names = [new for _old, new in effective_renames]
    if LATINGO_COMPANY in new_names:
        # A deleted Company can still have dangling Link, Data, Dynamic Link or
        # saved-filter references.  Do not treat target existence as completion.
        old_names.append(GRUPO_COMPANY)
    missing_targets = [name for name in new_names if name not in company_names]
    old_company_records = [name for name in old_names if name in company_names]
    old_references = []

    company_link_fields = set(_company_link_fields())
    fields = set(company_link_fields)
    fields.update(UNTYPED_COMPANY_REFERENCE_FIELDS)
    for doctype, fieldname in sorted(fields):
        if not _field_exists(doctype, fieldname):
            continue
        for old_name in old_names:
            count = _exact_reference_count(doctype, fieldname, old_name)
            if count:
                old_references.append(
                    {
                        "doctype": doctype,
                        "fieldname": fieldname,
                        "value": old_name,
                        "count": count,
                    }
                )

    conditional_fields = set(CONDITIONAL_COMPANY_REFERENCE_FIELDS)
    conditional_fields.update(_company_dynamic_link_fields())
    for doctype, fieldname, condition_field, condition_value in sorted(conditional_fields):
        if not _field_exists(doctype, fieldname) or not _field_exists(doctype, condition_field):
            continue
        for old_name in old_names:
            count = _conditional_reference_count(
                doctype,
                fieldname,
                old_name,
                condition_field,
                condition_value,
            )
            if count:
                old_references.append(
                    {
                        "doctype": doctype,
                        "fieldname": fieldname,
                        "value": old_name,
                        "count": count,
                    }
                )

    old_references.extend(
        _user_settings_old_references(old_names, company_link_fields)
    )

    return {
        "ok": not missing_targets and not old_company_records and not old_references,
        "missing_targets": missing_targets,
        "old_company_records": old_company_records,
        "old_references": old_references,
        "pending_company_migrations": [
            {
                "source_company": GRUPO_COMPANY,
                "target_company": LATINGO_COMPANY,
                "target_exists": LATINGO_COMPANY in company_names,
            }
        ] if GRUPO_COMPANY in company_names else [],
    }


def verify_company_renames_complete(*, require_latingo_migration: bool = False) -> dict:
    """Verify historical renames; optionally require the explicit consolidation.

    The default supports deploying this code before the data maintenance.  The
    stricter mode is read-only and must be used for post-maintenance acceptance.
    Neither mode performs a Company merge or repairs references.
    """

    audit = audit_company_renames()
    if not audit["ok"]:
        raise CompanyRenameError(_format_audit_error(audit))
    if require_latingo_migration and audit.get("pending_company_migrations"):
        raise CompanyRenameError(
            "LATINGO_COMPANY_MIGRATION_REQUIRED: "
            + json.dumps(audit["pending_company_migrations"], ensure_ascii=False, sort_keys=True)
        )
    return audit


def _company_names() -> list[str]:
    _require_frappe()
    return list(frappe.get_all("Company", pluck="name", limit_page_length=0))


def _rename_company(old_name: str, new_name: str) -> None:
    _require_frappe()
    from frappe.model.rename_doc import rename_doc

    rename_doc(
        "Company",
        old_name,
        new_name,
        force=False,
        merge=False,
        ignore_permissions=True,
    )


def _replace_untyped_company_references(old_name: str, new_name: str) -> None:
    for doctype, fieldname in UNTYPED_COMPANY_REFERENCE_FIELDS:
        _replace_exact_reference(doctype, fieldname, old_name, new_name)
    for doctype, fieldname, condition_field, condition_value in CONDITIONAL_COMPANY_REFERENCE_FIELDS:
        _replace_conditional_reference(
            doctype,
            fieldname,
            old_name,
            new_name,
            condition_field,
            condition_value,
        )


def _company_link_fields() -> list[tuple[str, str]]:
    # Use the same discovery path as Frappe's rename operation.  In addition
    # to DocField and Custom Field it includes links created through Property
    # Setter, which a hand-written metadata query can otherwise miss.
    try:
        from frappe.model.rename_doc import get_link_fields

        link_fields = get_link_fields("Company")
        return [
            (_row_value(row, "parent"), _row_value(row, "fieldname"))
            for row in link_fields
            if _row_value(row, "parent") and _row_value(row, "fieldname")
        ]
    except ImportError:
        # Lightweight test environments do not install Frappe.  Keep the
        # metadata fallback testable without changing production behaviour.
        pass

    standard = frappe.get_all(
        "DocField",
        filters={"fieldtype": "Link", "options": "Company"},
        fields=["parent", "fieldname"],
        limit_page_length=0,
    )
    custom = frappe.get_all(
        "Custom Field",
        filters={"fieldtype": "Link", "options": "Company"},
        fields=["dt", "fieldname"],
        limit_page_length=0,
    )
    result = []
    for row in standard:
        result.append((_row_value(row, "parent"), _row_value(row, "fieldname")))
    for row in custom:
        result.append((_row_value(row, "dt"), _row_value(row, "fieldname")))
    return [(doctype, fieldname) for doctype, fieldname in result if doctype and fieldname]


def _company_dynamic_link_fields() -> list[tuple[str, str, str, str]]:
    standard = frappe.get_all(
        "DocField",
        filters={"fieldtype": "Dynamic Link"},
        fields=["parent", "fieldname", "options"],
        limit_page_length=0,
    )
    custom = frappe.get_all(
        "Custom Field",
        filters={"fieldtype": "Dynamic Link"},
        fields=["dt", "fieldname", "options"],
        limit_page_length=0,
    )
    result = []
    for row in standard:
        result.append(
            (
                _row_value(row, "parent"),
                _row_value(row, "fieldname"),
                _row_value(row, "options"),
                "Company",
            )
        )
    for row in custom:
        result.append(
            (
                _row_value(row, "dt"),
                _row_value(row, "fieldname"),
                _row_value(row, "options"),
                "Company",
            )
        )
    return [
        (doctype, fieldname, condition_field, condition_value)
        for doctype, fieldname, condition_field, condition_value in result
        if doctype and fieldname and condition_field
    ]


def _replace_exact_reference(doctype: str, fieldname: str, old_name: str, new_name: str) -> None:
    if not _field_exists(doctype, fieldname):
        return
    table = _table_name(doctype)
    field = _identifier(fieldname)
    frappe.db.sql(
        f"update `{table}` set `{field}` = %s where `{field}` = %s",
        (new_name, old_name),
    )


def _replace_conditional_reference(
    doctype: str,
    fieldname: str,
    old_name: str,
    new_name: str,
    condition_field: str,
    condition_value: str,
) -> None:
    if not _field_exists(doctype, fieldname) or not _field_exists(doctype, condition_field):
        return
    table = _table_name(doctype)
    field = _identifier(fieldname)
    condition = _identifier(condition_field)
    frappe.db.sql(
        f"update `{table}` set `{field}` = %s where `{field}` = %s and `{condition}` = %s",
        (new_name, old_name, condition_value),
    )


def _exact_reference_count(doctype: str, fieldname: str, value: str) -> int:
    meta = frappe.get_meta(doctype)
    if getattr(meta, "issingle", False):
        return int(frappe.db.get_single_value(doctype, fieldname) == value)
    return int(frappe.db.count(doctype, {fieldname: value}) or 0)


def _conditional_reference_count(
    doctype: str,
    fieldname: str,
    value: str,
    condition_field: str,
    condition_value: str,
) -> int:
    meta = frappe.get_meta(doctype)
    if getattr(meta, "issingle", False):
        return int(
            frappe.db.get_single_value(doctype, fieldname) == value
            and frappe.db.get_single_value(doctype, condition_field) == condition_value
        )
    return int(
        frappe.db.count(
            doctype,
            {fieldname: value, condition_field: condition_value},
        )
        or 0
    )


def _user_settings_old_references(
    old_names: list[str],
    company_link_fields: set[tuple[str, str]],
) -> list[dict]:
    """Audit Frappe's internal saved-filter table using its filter schema."""

    if not old_names or not company_link_fields:
        return []
    fields_by_doctype: dict[str, set[str]] = {}
    for doctype, fieldname in company_link_fields:
        fields_by_doctype.setdefault(doctype, set()).add(fieldname)
    rows = _user_settings_rows_for_doctypes(set(fields_by_doctype))

    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        doctype = _row_value(row, "doctype")
        link_fields = fields_by_doctype.get(doctype)
        if not link_fields:
            continue
        data = json.loads(_row_value(row, "data") or "{}")
        for view in USER_SETTINGS_VIEWS:
            filters = (data.get(view) or {}).get("filters") or []
            for saved_filter in filters:
                if not isinstance(saved_filter, list) or len(saved_filter) < 4:
                    continue
                filter_doctype = str(saved_filter[0] or doctype)
                filter_fieldname = str(saved_filter[1] or "")
                if filter_doctype != doctype or filter_fieldname not in link_fields:
                    continue
                for old_name in _company_names_in_filter_value(saved_filter[3], old_names):
                    key = (f"{doctype}.{filter_fieldname}", old_name)
                    counts[key] = counts.get(key, 0) + 1

    return [
        {
            "doctype": "__UserSettings",
            "fieldname": fieldname,
            "value": value,
            "count": count,
        }
        for (fieldname, value), count in sorted(counts.items())
    ]


def _replace_user_settings_company_references(old_name: str, new_name: str) -> None:
    """Repair saved Company filters that Frappe's exact LIKE query can miss."""

    fields_by_doctype: dict[str, set[str]] = {}
    for doctype, fieldname in set(_company_link_fields()):
        fields_by_doctype.setdefault(doctype, set()).add(fieldname)

    for row in _user_settings_rows_for_doctypes(set(fields_by_doctype)):
        doctype = _row_value(row, "doctype")
        link_fields = fields_by_doctype.get(doctype)
        if not link_fields:
            continue
        data = json.loads(_row_value(row, "data") or "{}")
        changed = False
        for view in USER_SETTINGS_VIEWS:
            filters = (data.get(view) or {}).get("filters") or []
            for saved_filter in filters:
                if not isinstance(saved_filter, list) or len(saved_filter) < 4:
                    continue
                filter_doctype = str(saved_filter[0] or doctype)
                filter_fieldname = str(saved_filter[1] or "")
                if filter_doctype != doctype or filter_fieldname not in link_fields:
                    continue
                rewritten, value_changed = _replace_company_in_filter_value(
                    saved_filter[3], old_name, new_name
                )
                if value_changed:
                    saved_filter[3] = rewritten
                    changed = True

        if not changed:
            continue
        user = _row_value(row, "user")
        frappe.db.sql(
            "update `__UserSettings` set data=%s where doctype=%s and user=%s",
            (json.dumps(data, ensure_ascii=False), doctype, user),
        )
        frappe.cache.hset("_user_settings", f"{doctype}::{user}", None)


def _user_settings_rows_for_doctypes(doctypes: set[str]) -> list:
    """Load relevant rows without depending on JSON's Unicode escaping."""

    names = sorted(name for name in doctypes if name)
    if not names:
        return []
    placeholders = ", ".join("%s" for _name in names)
    return list(
        frappe.db.sql(
            f"select user, doctype, data from `__UserSettings` where doctype in ({placeholders})",
            tuple(names),
            as_dict=True,
        )
    )


def _replace_company_in_filter_value(value, old_name: str, new_name: str):
    if isinstance(value, list):
        changed = False
        rewritten = []
        for entry in value:
            new_entry, entry_changed = _replace_company_in_filter_value(
                entry, old_name, new_name
            )
            rewritten.append(new_entry)
            changed = changed or entry_changed
        return rewritten, changed
    if value == old_name:
        return new_name, True
    return value, False


def _company_names_in_filter_value(value, company_names: list[str]) -> list[str]:
    candidates = set(company_names)
    if isinstance(value, list):
        found = set()
        for entry in value:
            found.update(_company_names_in_filter_value(entry, company_names))
        return sorted(found)
    if isinstance(value, str) and value in candidates:
        return [value]
    return []


def _field_exists(doctype: str, fieldname: str) -> bool:
    if frappe.db.table_exists(doctype):
        return bool(frappe.db.has_column(doctype, fieldname))
    if not frappe.db.exists("DocType", doctype):
        return False
    meta = frappe.get_meta(doctype)
    return bool(
        getattr(meta, "issingle", False)
        and meta.has_field(fieldname)
    )


def _format_audit_error(audit: dict) -> str:
    details = []
    if audit.get("missing_targets"):
        details.append("missing=" + ", ".join(audit["missing_targets"]))
    if audit.get("old_company_records"):
        details.append("old Company=" + ", ".join(audit["old_company_records"]))
    for row in audit.get("old_references") or []:
        details.append(
            f"{row['doctype']}.{row['fieldname']}={row['value']} ({row['count']})"
        )
    return "Company rename post-audit failed: " + "; ".join(details)


def _table_name(doctype: str) -> str:
    return "tab" + _identifier(doctype)


def _identifier(value: str) -> str:
    text = str(value or "")
    if not text or "`" in text or "\x00" in text:
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return text


def _row_value(row, fieldname: str) -> str:
    if isinstance(row, dict):
        return str(row.get(fieldname) or "")
    return str(getattr(row, fieldname, "") or "")


def _rollback_safely() -> None:
    if frappe is None:
        return
    try:
        frappe.db.rollback()
    except Exception:
        pass


def _require_frappe() -> None:
    if frappe is None:
        raise RuntimeError("Frappe 运行环境不可用。")
