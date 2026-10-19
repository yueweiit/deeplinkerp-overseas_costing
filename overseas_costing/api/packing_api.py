"""中文用途：装箱来源、缓存刷新、不可变确认和独立运费试算的最小权限 API。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import re

import frappe

from overseas_costing.integrations.dingtalk_packing_source import get_packing_runtime_clients
from overseas_costing.services import (
    effective_logistics_source as effective_source,
    freight_comparison_service,
    packing_sheet_cache_service,
    packing_snapshot_service,
)
from overseas_costing.services.access_control import require_packing_workflow_permission


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _json_object(value, *, label: str, max_length: int) -> dict:
    if isinstance(value, dict):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value or "{}")
        if len(encoded.encode("utf-8")) > max_length:
            raise ValueError(_("{0}过大。").format(label))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError(_("{0}不是有效 JSON。").format(label)) from error
    if len(encoded.encode("utf-8")) > max_length:
        raise ValueError(_("{0}过大。").format(label))
    if not isinstance(payload, dict):
        raise ValueError(_("{0}必须是对象。").format(label))
    return payload


def _request_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not HASH_PATTERN.fullmatch(normalized):
        raise ValueError(_("请求 ID 必须是 64 位小写 SHA-256。"))
    return normalized


@frappe.whitelist()
def list_packing_sources(batch_name):
    batch_name = require_packing_workflow_permission(batch_name, "read")
    return packing_snapshot_service.list_packing_sources(batch_name)


@frappe.whitelist()
def list_packing_sheet_catalog(batch_name):
    """只读取本地物化缓存；打开弹窗不触发远端目录或归档读取。"""

    batch_name = require_packing_workflow_permission(batch_name, "read")
    return packing_snapshot_service.list_cached_packing_sheet_catalog(batch_name)


@frappe.whitelist()
def list_packing_attachment_sources(batch_name):
    """懒加载附件来源，且不查询或解析远程装箱 Sheet。"""

    batch_name = require_packing_workflow_permission(batch_name, "read")
    return packing_snapshot_service.list_packing_sources(batch_name, include_wiki=False)


@frappe.whitelist()
def list_packing_attachment_sheets(batch_name, source_kind, source_id):
    batch_name = require_packing_workflow_permission(batch_name, 'read')
    from overseas_costing.services.packing_attachment_service import list_attachment_sheets
    return list_attachment_sheets(batch_name, str(source_kind), str(source_id))


@frappe.whitelist()
def request_packing_workbook_refresh(batch_name, workbook_id, request_id):
    batch_name = require_packing_workflow_permission(batch_name, "refresh")
    request_key = _request_key(request_id)
    current = _refresh_selected_wiki(batch_name, str(workbook_id), None, request_key)
    if current is not None:
        return current
    request_number = get_packing_runtime_clients().submitter.request_workbook_index_refresh(
        str(workbook_id), request_key, str(frappe.session.user)
    )
    return {"ok": True, "request_id": request_number, "request_key": request_key}


@frappe.whitelist()
def request_packing_sheet_refresh(batch_name, workbook_id, sheet_id, request_id):
    batch_name = require_packing_workflow_permission(batch_name, "refresh")
    request_key = _request_key(request_id)
    current = _refresh_selected_wiki(batch_name, str(workbook_id), str(sheet_id), request_key)
    if current is not None:
        return current
    packing_sheet_cache_service.register_manual_refresh(
        batch_name=batch_name,
        workbook_id=str(workbook_id),
        sheet_id=str(sheet_id),
        request_key=request_key,
    )
    request_number = get_packing_runtime_clients().submitter.request_sheet_refresh(
        str(workbook_id), str(sheet_id), request_key, str(frappe.session.user)
    )
    return {"ok": True, "request_id": request_number, "request_key": request_key}


@frappe.whitelist()
def get_packing_refresh_status(batch_name, request_id):
    batch_name = require_packing_workflow_permission(batch_name, "read")
    bundle = effective_source.current_source_bundle(batch_name)
    if bundle and bundle['context']['root_kind'] == 'expense':
        from overseas_costing.services.logistics_settlement.store import Store
        from overseas_costing.services.logistics_settlement.model import digest
        state = Store.frappe().get('state',digest('selected-wiki-refresh',batch_name,_request_key(request_id))) or {}
        if state and state.get('source_context') != bundle['context']:
            return {'status':'failed','error_message':'刷新期间采购支出来源已变化，请重新获取资料。'}
        return {key:state.get(key) for key in ('status','error_message','source_ids','source_context')} if state else {'status':'not_found'}
    request_key = _request_key(request_id)
    status = get_packing_runtime_clients().catalog.get_refresh_status(request_key) or {
        "status": "not_found"
    }
    if str(status.get("status") or "") == "success":
        try:
            completed = packing_sheet_cache_service.complete_manual_refresh(request_key)
        except Exception as error:
            return {"status": "failed", "error_message": str(error)[:1000]}
        if completed and completed.get("pending") is True:
            return {"status": "running"}
        if not completed or completed.get("ok") is not True:
            return {
                "status": "failed",
                "error_message": "远端刷新已完成，但缺少可验证的本地物化请求。请重新刷新所选 Sheet。",
            }
    return status


def _refresh_selected_wiki(batch_name, workbook_id, sheet_id, request_key):
    """Explicitly copy only referenced archived sheets; ordinary polling stays local."""
    from overseas_costing.services.effective_logistics_source import current_source_bundle, explicit_wiki_sources, require_readable
    bundle = current_source_bundle(batch_name)
    if not bundle or bundle['context']['root_kind'] != 'expense':
        return None
    require_readable(bundle['context'])
    ids = sorted(source_id for source_id in explicit_wiki_sources(bundle['source'])
                 if source_id.partition(':')[0] == workbook_id
                 and (sheet_id is None or source_id.partition(':')[2] == sheet_id))
    if not ids:
        raise ValueError(_('该装箱表未被当前采购支出明确关联，不能刷新或采用。'))
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.logistics_settlement.model import digest,dumps
    from overseas_costing.services.logistics_settlement.jobs import utcnow
    from overseas_costing.services.packing_source_service import refresh_bound_wiki_snapshot
    store = Store.frappe()
    identity = digest('selected-wiki-refresh',batch_name,request_key)
    previous = store.get('state',identity)
    if previous:
        if previous.get('source_ids') != ids or previous.get('source_context') != bundle['context']:
            raise ValueError(_('刷新请求的来源已变化，请重新刷新。'))
        return {'ok':previous.get('status') == 'success','request_id':request_key,'request_key':request_key}
    for source_id in ids:
        refresh_bound_wiki_snapshot(batch_name,source_id)
    current_context = current_source_bundle(batch_name)['context']
    store.put('state',{'id':identity,'updated_at':utcnow(),'data':dumps({
        'status':'success','source_ids':ids,'source_context':current_context})})
    return {'ok':True,'request_id':request_key,'request_key':request_key,'status':'success'}


@frappe.whitelist()
def preview_packing_source_v2(batch_name, source_kind, source_id, sheet_name=None):
    batch_name = require_packing_workflow_permission(batch_name, "preview")
    return packing_snapshot_service.preview_packing_source_v2(
        batch_name,
        str(source_kind)[:40],
        str(source_id)[:500],
        sheet_name=str(sheet_name)[:200] if sheet_name else None,
    )


@frappe.whitelist()
def confirm_packing_snapshot(batch_name, source_revision, resolutions_json=None):
    batch_name = require_packing_workflow_permission(batch_name, "confirm")
    resolutions = _json_object(resolutions_json, label="装箱确认内容", max_length=50000)
    return packing_snapshot_service.confirm_packing_snapshot(
        batch_name, str(source_revision)[:10000], resolutions
    )


@frappe.whitelist()
def get_current_packing_snapshot(batch_name):
    batch_name = require_packing_workflow_permission(batch_name, "read")
    return packing_snapshot_service.get_current_packing_snapshot(batch_name)


@frappe.whitelist()
def preview_freight_comparison(batch_name, snapshot_revision, quote_json):
    batch_name = require_packing_workflow_permission(batch_name, "preview")
    quote = _json_object(quote_json, label="报价内容", max_length=20000)
    return freight_comparison_service.preview_freight_comparison(
        batch_name, str(snapshot_revision)[:200], quote
    )


@frappe.whitelist()
def save_freight_comparison(batch_name, snapshot_revision, request_id, quote_json):
    batch_name = require_packing_workflow_permission(batch_name, "compare")
    request_key = _request_key(request_id)
    quote = _json_object(quote_json, label="报价内容", max_length=20000)
    return freight_comparison_service.save_freight_comparison(
        batch_name, str(snapshot_revision)[:200], request_key, quote
    )


@frappe.whitelist()
def list_freight_comparisons(batch_name):
    batch_name = require_packing_workflow_permission(batch_name, "read")
    return freight_comparison_service.list_freight_comparisons(batch_name)
