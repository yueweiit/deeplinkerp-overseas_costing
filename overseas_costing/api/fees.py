"""费用与凭证工作台的最小权限 API。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json

import frappe

from overseas_costing.services import fee_evidence_review_service, fee_service
from overseas_costing.services.access_control import require_batch_permission


MAX_FEE_PAYLOAD_BYTES = 100_000
MAX_IDENTIFIER_LENGTH = 300
MAX_REMARK_LENGTH = 2_000
MAX_REVIEW_PAYLOAD_BYTES = 1_000_000
MAX_COMPONENT_MATRIX_PAYLOAD_BYTES = 24 * 1024 * 1024
MAX_COMPONENT_MATRIX_CELLS = 60_000


def _bounded_json_object(value) -> dict:
    if isinstance(value, dict):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        payload = value
    else:
        encoded = str(value or "{}")
        if len(encoded.encode("utf-8")) > MAX_FEE_PAYLOAD_BYTES:
            raise ValueError(_("费用参数过大。"))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(_("费用参数不是有效 JSON。")) from error
    if len(encoded.encode("utf-8")) > MAX_FEE_PAYLOAD_BYTES:
        raise ValueError(_("费用参数过大。"))
    if not isinstance(payload, dict):
        raise ValueError(_("费用参数必须是 JSON 对象。"))
    return payload


def _identifier(value) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(_("记录标识不合法。"))
    return normalized


def _bounded_json(value, expected_type, label):
    if isinstance(value, expected_type):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value or ("[]" if expected_type is list else "{}"))
        if len(encoded.encode("utf-8")) > MAX_REVIEW_PAYLOAD_BYTES:
            raise ValueError(_("{0}内容过大。").format(label))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(_("{0}不是有效 JSON。").format(label)) from error
    if len(encoded.encode("utf-8")) > MAX_REVIEW_PAYLOAD_BYTES:
        raise ValueError(_("{0}内容过大。").format(label))
    if not isinstance(payload, expected_type):
        raise ValueError(_("{0}格式不正确。").format(label))
    return payload


def _bounded_component_matrix_json(value) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        payload = value
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        encoded = str(value)
        if len(encoded.encode("utf-8")) > MAX_COMPONENT_MATRIX_PAYLOAD_BYTES:
            raise ValueError(_("物料税费矩阵内容过大。"))
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(_("物料税费矩阵不是有效 JSON。")) from error
    if len(encoded.encode("utf-8")) > MAX_COMPONENT_MATRIX_PAYLOAD_BYTES:
        raise ValueError(_("物料税费矩阵内容过大。"))
    if not isinstance(payload, dict):
        raise ValueError(_("物料税费矩阵必须是 JSON 对象。"))
    cells = payload.get("cells")
    if isinstance(cells, list) and len(cells) > MAX_COMPONENT_MATRIX_CELLS:
        raise ValueError(
            _("物料税费矩阵单元格数量不能超过 {0}。").format(MAX_COMPONENT_MATRIX_CELLS)
        )
    return payload


@frappe.whitelist()
def get_fee_worklist(batch_name, version_name=None):
    batch_name = require_batch_permission(batch_name, "read")
    return fee_service.get_fee_worklist(batch_name, str(version_name or "") or None)


@frappe.whitelist()
def save_fee(batch_name, version_name, fee_payload, edit_token, expected_modified):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_service.save_fee(
        batch_name=batch_name,
        version_name=_identifier(version_name),
        fee_payload=_bounded_json_object(fee_payload),
        edit_token=str(edit_token or "")[:MAX_IDENTIFIER_LENGTH],
        expected_modified=str(expected_modified or "")[:MAX_IDENTIFIER_LENGTH],
    )


@frappe.whitelist()
def link_fee_evidence(
    batch_name,
    fee_rule,
    attachment,
    evidence_role,
    edit_token,
    expected_modified,
):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_service.link_fee_evidence(
        batch_name=batch_name,
        fee_rule=_identifier(fee_rule),
        attachment=_identifier(attachment),
        evidence_role=str(evidence_role or "")[:MAX_IDENTIFIER_LENGTH],
        edit_token=str(edit_token or "")[:MAX_IDENTIFIER_LENGTH],
        expected_modified=str(expected_modified or "")[:MAX_IDENTIFIER_LENGTH],
    )


@frappe.whitelist()
def set_fee_evidence_status(
    batch_name,
    evidence_name,
    status,
    remark=None,
    edit_token=None,
    expected_modified=None,
):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_service.set_fee_evidence_status(
        batch_name=batch_name,
        evidence_name=_identifier(evidence_name),
        status=str(status or "")[:40],
        remark=str(remark or "")[:MAX_REMARK_LENGTH],
        edit_token=str(edit_token or "")[:MAX_IDENTIFIER_LENGTH],
        expected_modified=str(expected_modified or "")[:MAX_IDENTIFIER_LENGTH],
    )


@frappe.whitelist()
def start_fee_evidence_review(
    batch_name,
    version_name,
    logical_fee_key,
    attachment,
    evidence_role="expense_invoice",
    force=False,
    edit_token=None,
    expected_modified=None,
):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_evidence_review_service.start_fee_evidence_review(
        batch_name=batch_name,
        version_name=_identifier(version_name),
        logical_fee_key=_identifier(logical_fee_key),
        attachment=_identifier(attachment),
        evidence_role=str(evidence_role or "expense_invoice")[:MAX_IDENTIFIER_LENGTH],
        force=str(force).strip().lower() in {"1", "true", "yes"},
        edit_token=str(edit_token or "")[:MAX_IDENTIFIER_LENGTH],
        expected_modified=str(expected_modified or "")[:MAX_IDENTIFIER_LENGTH],
    )


@frappe.whitelist()
def get_fee_evidence_review_status(batch_name, run_id, after_revision=None):
    batch_name = require_batch_permission(batch_name, "read")
    return fee_evidence_review_service.get_fee_evidence_review_status(
        batch_name=batch_name,
        run_id=_identifier(run_id),
        after_revision=int(after_revision) if str(after_revision or "").strip() else None,
    )


@frappe.whitelist()
def apply_fee_evidence_review(
    batch_name,
    run_id,
    selections_json,
    edits_json,
    edit_token,
    expected_modified,
    component_matrix_json=None,
):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_evidence_review_service.apply_fee_evidence_review(
        batch_name=batch_name,
        run_id=_identifier(run_id),
        selections=_bounded_json(selections_json, list, "草稿选择"),
        edits=_bounded_json(edits_json, dict, "草稿修改"),
        component_matrix=_bounded_component_matrix_json(component_matrix_json),
        edit_token=str(edit_token or "")[:MAX_IDENTIFIER_LENGTH],
        expected_modified=str(expected_modified or "")[:MAX_IDENTIFIER_LENGTH],
    )


@frappe.whitelist()
def discard_fee_evidence_review(batch_name, run_id):
    batch_name = require_batch_permission(batch_name, "write")
    return fee_evidence_review_service.discard_fee_evidence_review(
        batch_name=batch_name,
        run_id=_identifier(run_id),
    )
