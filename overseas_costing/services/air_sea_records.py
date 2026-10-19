"""Team-owned air/sea drafts with authoritative calculation and optimistic saves."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import uuid

from overseas_costing.services import air_sea_batch
from overseas_costing.services.air_sea_calculation import calculate, normalize_payload, FORMULA_VERSION
from overseas_costing.services.access_control import require_doctype_permission

try:
    import frappe
except ImportError:
    frappe = None

DOCTYPE = "Overseas Air Sea Comparison"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def prepare_values(title, payload):
    title = str(title or "").strip()
    if not title or len(title) > 80:
        raise ValueError(_("记录名称须为 1 至 80 个字符。"))
    payload = normalize_payload(payload)
    source = air_sea_batch.validate_source(payload.get("source"), for_update=True)
    result = calculate(payload)
    return {"title": title, "payload_json": dumps(payload), "result_json": dumps(result),
        "status": result["status"], "formula_version": FORMULA_VERSION,
        "sea_total": result["oceanTotal"], "air_total": result["airTotal"],
        "source_batch": (source or {}).get("batch"), "source_version": (source or {}).get("version"),
        "source_packing_snapshot": (source or {}).get("packing_snapshot")}


def check_revision(current, expected):
    if not expected or str(current) != str(expected):
        raise ValueError(_("记录已被其他成员修改或当前版本缺失，请重新读取后再保存；当前输入未丢失。"))


def validate_request_id(value):
    value = str(value or "")
    try:
        if len(value) != 36 or str(uuid.UUID(value)) != value.lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(_("保存请求标识无效，请重新保存。")) from None
    return value.lower()


def request_digest(title, payload):
    return hashlib.sha256(dumps({"title": str(title).strip(), "payload": normalize_payload(payload)}).encode()).hexdigest()


def _access(doc, ptype="read"):
    require_doctype_permission(DOCTYPE, ptype, doc=doc)
    source = json.loads(doc.payload_json or "{}").get("source")
    air_sea_batch.validate_source(source)


def _response(doc, **extra):
    payload = json.loads(doc.payload_json or "{}")
    return {"ok": True, "record": {
        "name": doc.name, "title": doc.title, "modified": str(doc.modified),
        "owner": doc.owner, "modified_by": doc.modified_by, "payload": payload,
        "result": json.loads(doc.result_json or "{}")},
        "source_changed": air_sea_batch.source_changed(payload.get("source")), **extra}


def get_record(name):
    require_doctype_permission(DOCTYPE, "read")
    doc = frappe.get_doc(DOCTYPE, name)
    _access(doc)
    return _response(doc)


def list_records(keyword="", page=1, page_length=20):
    require_doctype_permission(DOCTYPE, "read")
    from overseas_costing.services.workbench_service import normalize_page, _item_count_fields_for_frappe
    page, page_length = normalize_page(page, page_length, default_length=20)
    filters = {"title": ["like", "%" + str(keyword or "")[:200] + "%"]} if keyword else {}
    # get_list applies the same permission_query_conditions as Desk/REST lists.
    counts = frappe.get_list(DOCTYPE, filters=filters, fields=_item_count_fields_for_frappe())
    items = frappe.get_list(DOCTYPE, filters=filters,
        fields=["name", "title", "modified", "modified_by", "owner", "status", "sea_total", "air_total"],
        order_by="modified desc, name desc", limit_start=(page - 1) * page_length, limit_page_length=page_length)
    for row in items:
        if row.get("status") != "Ready":
            row["sea_total"] = row["air_total"] = None
    return {"ok": True, "items": items, "total": int((counts[0] if counts else {}).get("total") or 0), "page": page, "page_length": page_length}


def _locked_doc(name):
    # Read the entire current row under the lock; a plain read after locking only
    # its name can still return an earlier REPEATABLE READ snapshot.
    return frappe.get_doc(DOCTYPE, name, for_update=True)


def save_record(title, payload_json, name=None, modified=None, request_id=None):
    require_doctype_permission(DOCTYPE, "write" if name else "create")
    request_id = validate_request_id(request_id)
    values = prepare_values(title, payload_json)
    digest = request_digest(title, json.loads(values["payload_json"]))
    if name:
        doc = _locked_doc(name)
        _access(doc, "write")
        if doc.last_request_id == request_id:
            if doc.last_request_hash != digest:
                raise ValueError(_("同一保存请求的内容发生变化，请使用新的保存请求。"))
            return _response(doc, idempotent=True)
        check_revision(doc.modified, modified)
        doc.update(values)
        doc.last_request_id, doc.last_request_hash = request_id, digest
        doc.save()
    else:
        # Deterministic document name provides DB-enforced idempotency even under concurrent creates.
        record_name = "ASC-" + hashlib.sha256((frappe.session.user + ":" + request_id).encode()).hexdigest()[:32]
        existing = frappe.db.exists(DOCTYPE, record_name)
        if existing:
            doc = _locked_doc(record_name)
            _access(doc)
            if doc.create_request_hash != digest:
                raise ValueError(_("同一保存请求的内容发生变化，请使用新的保存请求。"))
            if doc.last_request_id != request_id:
                raise ValueError(_("记录已被其他成员修改，请重新读取；本次重试未覆盖任何修改。"))
            return _response(doc, idempotent=True)
        doc = frappe.get_doc({"doctype": DOCTYPE, **values, "create_request_id": request_id,
            "create_request_hash": digest, "last_request_id": request_id, "last_request_hash": digest})
        frappe.db.savepoint("air_sea_create")
        try:
            doc.insert(set_name=record_name)
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point="air_sea_create")
            doc = _locked_doc(record_name)
            _access(doc)
            if doc.create_request_hash != digest:
                raise ValueError(_("同一保存请求的内容发生变化，请重新保存。"))
            if doc.last_request_id != request_id:
                raise ValueError(_("记录已被其他成员修改，请重新读取；本次重试未覆盖任何修改。"))
            return _response(doc, idempotent=True)
    return _response(doc)


def delete_record(name, modified=None):
    require_doctype_permission(DOCTYPE, "delete")
    doc = _locked_doc(name)
    _access(doc, "delete")
    check_revision(doc.modified, modified)
    frappe.delete_doc(DOCTYPE, name)
    return {"ok": True}


def has_permission(doc, user=None, permission_type=None, ptype=None, **kwargs):
    user = user or frappe.session.user
    if not set(frappe.get_roles(user)).intersection({"System Manager", "海外成本核算用户"}):
        return False
    batch = doc.get("source_batch")
    if batch and not frappe.has_permission("Overseas Cost Batch", ptype="read", doc=batch, user=user):
        return False
    return True  # Frappe controller hooks may only deny; native roles still determine the operation.


def permission_query_conditions(user=None):
    user = user or frappe.session.user
    if not set(frappe.get_roles(user)).intersection({"System Manager", "海外成本核算用户"}):
        return "1=0"
    # Delegate batch visibility to Frappe rather than replicating User Permission rules.
    if frappe.has_permission("Overseas Cost Batch", "read", user=user):
        batches = frappe.get_list("Overseas Cost Batch", fields=["name"], limit_page_length=0, user=user)
    else:
        batches = []
    condition = "coalesce(`tabOverseas Air Sea Comparison`.`source_batch`, '') = ''"
    if batches:
        names = ",".join(frappe.db.escape(row["name"]) for row in batches)
        condition += f" OR `tabOverseas Air Sea Comparison`.`source_batch` IN ({names})"
    return "(" + condition + ")"
