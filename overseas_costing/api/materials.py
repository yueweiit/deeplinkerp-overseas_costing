"""物料表格和发货数量来源的最小权限 API。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json

import frappe

from overseas_costing.services import (
    material_ai_fill_service,
    material_import_service,
    material_input_service,
    supplier_resolution_service,
)
from overseas_costing.services.access_control import require_batch_permission
from overseas_costing.services.material_ai_errors import source_review_endpoint


USER_QUANTITY_MODES = {"DEFAULT_PURCHASE", "MANUAL_CONFIRMED"}
MAX_CHOICES_BYTES = 100_000
MAX_SOURCE_ID_LENGTH = 500
MAX_PREVIEW_REVISION_LENGTH = 200_000
MAX_AI_UPDATES_BYTES = 1_000_000


@frappe.whitelist()
def list_project_route_options(batch_name: str) -> dict:
    """Return valid project-to-Company choices for exact selected item rows."""

    from overseas_costing.services.erp_sync_plan_service import list_project_route_options as list_options

    batch_name = require_batch_permission(batch_name, "read")
    return list_options(batch_name)


@frappe.whitelist()
def resolve_supplier_options(batch_name: str, raw_value: str = "") -> dict:
    """Return active ERP Supplier matches without writing or creating a supplier."""

    require_batch_permission(batch_name, "read")
    return supplier_resolution_service.resolve_supplier_reference(raw_value)


@frappe.whitelist(methods=["POST"])
def create_supplier_from_workbench(batch_name: str, supplier_name: str, confirm_similar=False) -> dict:
    """创建基础 Supplier；物料写入仍由既有批量更新入口负责。"""

    require_batch_permission(batch_name, "write")
    confirmed = confirm_similar in (True, 1, "1", "true", "True")
    try:
        return supplier_resolution_service.create_supplier(supplier_name, confirm_similar=confirmed)
    except supplier_resolution_service.SupplierCreationError as error:
        return {
            "ok": False,
            "code": error.code,
            "message": str(error),
            "candidates": error.candidates,
        }


def _choices_payload(value) -> dict:
    if isinstance(value, dict):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value or "{}")
        if len(encoded.encode("utf-8")) > MAX_CHOICES_BYTES:
            raise ValueError(_("物料导入选择过大。"))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError(_("物料导入选择不是有效 JSON。")) from error
    if len(encoded.encode("utf-8")) > MAX_CHOICES_BYTES:
        raise ValueError(_("物料导入选择过大。"))
    if not isinstance(payload, dict):
        raise ValueError(_("物料导入选择必须是对象。"))
    return payload


def _trusted_source_id(value) -> str:
    source_id = str(value or "").strip()
    if not source_id or len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(_("物料来源 ID 不合法。"))
    if "://" in source_id or source_id.startswith(("/", "~")) or "\\" in source_id:
        raise ValueError(_("物料来源只能使用系统内受控 ID。"))
    return source_id


def _ai_updates_payload(value) -> list:
    if isinstance(value, list):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value or "[]")
        if len(encoded.encode("utf-8")) > MAX_AI_UPDATES_BYTES:
            raise ValueError(_("AI 草稿更新内容过大。"))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError(_("AI 草稿更新不是有效 JSON。")) from error
    if len(encoded.encode("utf-8")) > MAX_AI_UPDATES_BYTES:
        raise ValueError(_("AI 草稿更新内容过大。"))
    if not isinstance(payload, list):
        raise ValueError(_("AI 草稿更新必须是数组。"))
    return payload


def _ai_review_payload(value, expected_type, label):
    if isinstance(value, expected_type):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value or ("[]" if expected_type is list else "{}"))
        if len(encoded.encode("utf-8")) > MAX_AI_UPDATES_BYTES:
            raise ValueError(_("{0}内容过大。").format(label))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError(_("{0}不是有效 JSON。").format(label)) from error
    if len(encoded.encode("utf-8")) > MAX_AI_UPDATES_BYTES:
        raise ValueError(_("{0}内容过大。").format(label))
    if not isinstance(payload, expected_type):
        raise ValueError(_("{0}格式不正确。").format(label))
    return payload


@frappe.whitelist()
def get_material_grid(batch_name, version_name=None, page=1, page_length=100):
    batch_name = require_batch_permission(batch_name, "read")
    return material_input_service.get_material_grid(
        batch_name=batch_name,
        version_name=version_name,
        page=page,
        page_length=page_length,
    )


@frappe.whitelist()
def get_excluded_materials(batch_name, version_name=None):
    batch_name = require_batch_permission(batch_name, "read")
    return material_input_service.get_excluded_materials(batch_name, version_name=version_name)


@frappe.whitelist(methods=['POST'])
def preview_material_packing_group(batch_name, version_name, member_keys_json, action,
                                   group_id=None, values_json=None, reason=None):
    batch_name = require_batch_permission(batch_name, 'write')
    from overseas_costing.services.material_packing_group_service import prepare_group_preview
    try:
        return prepare_group_preview(batch_name, str(version_name or ''),
            _ai_review_payload(member_keys_json, list, '装箱组物料'), str(action or ''),
            group_id=str(group_id or '')[:200],
            values=_ai_review_payload(values_json, dict, '装箱组数值') if values_json is not None else None,
            reason=str(reason or '')[:1000])
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'PACKING_GROUP_REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def confirm_material_packing_group(batch_name, preview_id, revision, edit_token, expected_modified):
    batch_name = require_batch_permission(batch_name, 'write')
    from overseas_costing.services.material_packing_group_service import confirm_group_preview
    try:
        return confirm_group_preview(batch_name, str(preview_id or ''), str(revision or ''),
            str(edit_token or '')[:200], str(expected_modified or '')[:200], actor=frappe.session.user)
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'PACKING_GROUP_REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def preview_material_packing_group_batch(batch_name, version_name, group_ids_json, reason=None):
    batch_name = require_batch_permission(batch_name, 'write')
    from overseas_costing.services.material_packing_group_service import prepare_group_batch_preview
    try:
        return prepare_group_batch_preview(
            batch_name, str(version_name or ''),
            _ai_review_payload(group_ids_json, list, '装箱组'), reason=str(reason or '')[:1000],
        )
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'PACKING_GROUP_REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def confirm_material_packing_group_batch(batch_name, preview_id, revision, edit_token, expected_modified):
    batch_name = require_batch_permission(batch_name, 'write')
    from overseas_costing.services.material_packing_group_service import confirm_group_batch_preview
    try:
        return confirm_group_batch_preview(
            batch_name, str(preview_id or ''), str(revision or ''),
            str(edit_token or '')[:200], str(expected_modified or '')[:200], actor=frappe.session.user,
        )
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'PACKING_GROUP_REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def exclude_material_items(batch_name, version_name, stable_line_keys_json, reason,
                           edit_token, expected_modified):
    batch_name = require_batch_permission(batch_name, 'write')
    from overseas_costing.services.material_bulk_edit_service import bulk_exclude_materials
    try:
        return bulk_exclude_materials(
            batch_name, str(version_name or ''),
            _ai_review_payload(stable_line_keys_json, list, '物料行'),
            str(edit_token or '')[:200], str(expected_modified or '')[:200],
            reason=str(reason or '')[:1000], actor=frappe.session.user,
        )
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'MATERIAL_SELECTION_STALE','message':str(error)}


@frappe.whitelist()
def preview_material_import(batch_name, source_kind, source_id, sheet_name=None, merge_reviews_json=None):
    batch_name = require_batch_permission(batch_name, "read")
    return material_import_service.preview_material_import(
        batch_name,
        str(source_kind or "")[:40],
        _trusted_source_id(source_id),
        sheet_name=str(sheet_name or "")[:200] or None,
        **({"merge_reviews_json": _choices_payload(merge_reviews_json)} if merge_reviews_json is not None else {}),
    )


@frappe.whitelist()
def apply_material_import(
    batch_name,
    preview_revision,
    choices_json,
    edit_token,
    expected_modified,
):
    batch_name = require_batch_permission(batch_name, "write")
    return material_import_service.apply_material_import(
        batch_name,
        str(preview_revision or "")[:MAX_PREVIEW_REVISION_LENGTH],
        _choices_payload(choices_json),
        str(edit_token or "")[:200],
        str(expected_modified or "")[:200],
    )


@frappe.whitelist()
def set_shipping_quantity(
    batch_name,
    item_name,
    mode,
    value,
    uom,
    edit_token,
    expected_modified,
):
    batch_name = require_batch_permission(batch_name, "write")
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in USER_QUANTITY_MODES:
        raise ValueError(_("发货数量来源状态不合法。"))
    return material_input_service.set_shipping_quantity(
        batch_name=batch_name,
        item_name=str(item_name or "")[:200],
        mode=normalized_mode,
        value=value,
        uom=str(uom or "")[:100],
        edit_token=str(edit_token or "")[:200],
        expected_modified=str(expected_modified or "")[:200],
    )


@frappe.whitelist()
def start_material_ai_fill(batch_name, version_name, edit_token, expected_modified):
    require_batch_permission(batch_name, "write")
    raise ValueError(material_ai_fill_service.LEGACY_AI_FLOW_DISABLED_MESSAGE)


@frappe.whitelist()
def get_material_ai_fill_status(batch_name, run_id, after_revision=None):
    batch_name = require_batch_permission(batch_name, "read")
    return material_ai_fill_service.get_material_ai_fill_status(
        batch_name,
        str(run_id or "")[:200],
        after_revision=(int(after_revision) if str(after_revision or "").strip() else None),
    )


@frappe.whitelist()
def apply_material_ai_fill(batch_name, run_id, updates_json, edit_token, expected_modified):
    require_batch_permission(batch_name, "write")
    raise ValueError(material_ai_fill_service.LEGACY_AI_FLOW_DISABLED_MESSAGE)


@frappe.whitelist()
def discard_material_ai_fill(batch_name, run_id):
    batch_name = require_batch_permission(batch_name, "write")
    return material_ai_fill_service.discard_material_ai_fill(
        batch_name,
        str(run_id or "")[:200],
    )


@frappe.whitelist()
def get_source_ai_clarification(batch_name):
    batch_name = require_batch_permission(batch_name, "read")
    return material_ai_fill_service.get_source_ai_clarification(batch_name)


@frappe.whitelist()
def save_source_ai_clarification(batch_name, clarification_text, expected_revision):
    batch_name = require_batch_permission(batch_name, "write")
    return material_ai_fill_service.save_source_ai_clarification(
        batch_name, str(clarification_text or "")[:4000], int(expected_revision),
    )


@frappe.whitelist()
@source_review_endpoint("启动分析")
def start_source_ai_review(
    batch_name,
    version_name,
    clarification_text=None,
    force=False,
    selected_source_ids_json=None,
    payment_candidate_refs_json=None,
    expected_clarification_revision=None,
    request_id=None,
    reanalyze_original_sources=False,
):
    batch_name = require_batch_permission(batch_name, "write")
    return material_ai_fill_service.start_source_ai_review(
        batch_name,
        str(version_name or "")[:200],
        None if clarification_text is None else str(clarification_text or "")[:4000],
        **({"expected_clarification_revision": int(expected_clarification_revision)}
           if expected_clarification_revision is not None else {}),
        force=str(force).strip().lower() in {"1", "true", "yes"},
        request_id=request_id,
        selected_source_ids=(
            _ai_review_payload(selected_source_ids_json, list, "资料来源选择")
            if selected_source_ids_json is not None
            else None
        ),
        payment_candidate_refs=(
            _ai_review_payload(payment_candidate_refs_json, list, "支付来源选择")
            if payment_candidate_refs_json is not None
            else None
        ),
        **({"reanalyze_original_sources": True}
           if str(reanalyze_original_sources).strip().lower() in {"1", "true", "yes"} else {}),
    )


@frappe.whitelist()
@source_review_endpoint("读取分析状态")
def get_source_ai_review_status(batch_name, run_id=None, version_name=None, after_revision=None):
    batch_name = require_batch_permission(batch_name, "read")
    return material_ai_fill_service.get_source_ai_review_status(
        batch_name,
        str(run_id or "")[:200],
        version_name=str(version_name or "")[:200],
        after_revision=(int(after_revision) if str(after_revision or "").strip() else None),
    )


@frappe.whitelist()
def get_source_ai_process_open_target(batch_name, run_id, source_open_ref):
    batch_name = require_batch_permission(batch_name, "read")
    return material_ai_fill_service.get_source_ai_process_open_target(
        batch_name,
        str(run_id or "")[:200],
        str(source_open_ref or "")[:80],
    )


@frappe.whitelist()
def apply_source_ai_review(
    batch_name,
    run_id,
    selections_json,
    edits_json,
    edit_token,
    expected_modified,
    manual_updates_json=None,
):
    require_batch_permission(batch_name, "write")
    raise ValueError(material_ai_fill_service.LEGACY_AI_FLOW_DISABLED_MESSAGE)


@frappe.whitelist(methods=['POST'])
def preview_source_ai_selection(batch_name,run_id,row_ids_json,fee_ids_json,mode,expected_version,
                                field_choices_json=None,packing_group_ids_json=None,
                                packing_assignments_json=None):
    batch_name=require_batch_permission(batch_name,'write')
    from overseas_costing.services.material_ai_selection_service import prepare
    try:
        args=(batch_name,str(run_id),_ai_review_payload(row_ids_json,list,'物料选择'),
            _ai_review_payload(fee_ids_json,list,'费用选择'),str(mode),str(expected_version))
        if (field_choices_json is None and packing_group_ids_json is None
                and packing_assignments_json is None):
            return prepare(*args)
        kwargs={}
        if field_choices_json is not None:
            kwargs['field_choices']=_ai_review_payload(field_choices_json,dict,'逐字段选择')
        if packing_group_ids_json is not None:
            kwargs['packing_group_ids']=_ai_review_payload(packing_group_ids_json,list,'装箱组选择')
        if packing_assignments_json is not None:
            kwargs['packing_assignments']=_ai_review_payload(
                packing_assignments_json,dict,'装箱归属选择')
        return prepare(*args,**kwargs)
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def confirm_source_ai_selection(batch_name,run_id,preview_id,preview_revision,edit_token,expected_modified):
    batch_name=require_batch_permission(batch_name,'write')
    from overseas_costing.services.material_ai_selection_service import confirm
    try:
        return confirm(batch_name,str(run_id),str(preview_id),str(preview_revision),str(edit_token),str(expected_modified))
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist(methods=['POST'])
def preview_material_row_recovery(batch_name,version_name):
    batch_name=require_batch_permission(batch_name,'write')
    from overseas_costing.services.material_ai_row_recovery import preview_recovery
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.logistics_settlement.ledger import FrappeLedger
    try:
        return preview_recovery(Store.frappe(),FrappeLedger(),batch_name,str(version_name))
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'RECOVERY_UNAVAILABLE','message':str(error)}


@frappe.whitelist(methods=['POST'])
def confirm_material_row_recovery(batch_name,version_name,preview_id,revision,edit_token,expected_modified):
    batch_name=require_batch_permission(batch_name,'write')
    from overseas_costing.services import edit_session_service
    from overseas_costing.services.material_ai_row_recovery import confirm_recovery
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.logistics_settlement.ledger import FrappeLedger
    try:
        edit_session_service.assert_batch_write(batch_name,edit_token=str(edit_token),expected_modified=str(expected_modified))
        result=confirm_recovery(Store.frappe(),FrappeLedger(),batch_name,str(version_name),str(preview_id),str(revision),frappe.session.user)
        result['batch_modified']=str(frappe.db.get_value('Overseas Cost Batch',batch_name,'modified') or '')
        return result
    except ValueError as error:
        frappe.db.rollback()
        return {'ok':False,'code':'REVIEW_REQUIRED','message':str(error)}


@frappe.whitelist()
def discard_source_ai_review(batch_name, run_id):
    batch_name = require_batch_permission(batch_name, "write")
    return material_ai_fill_service.discard_source_ai_review(batch_name, str(run_id or "")[:200])
