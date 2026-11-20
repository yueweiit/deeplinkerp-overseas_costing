"""综合成本工作台的钉钉审批只读详情服务。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

try:
    import frappe
except Exception:  # pragma: no cover
    frappe = None

from overseas_costing.services.packing_comment_service import (
    build_comment_source_id,
    parse_packing_comment,
)


PACKING_NAME_KEYWORDS = ("装箱单", "装箱计划", "packing list", "packinglist", "发货清单", "装柜清单", "物品清单")


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        result = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []


def _get_approval_source():
    from overseas_costing.services.import_service import _get_oa_postgres_source

    return _get_oa_postgres_source()


def _linked_instance_ids(extra_json) -> list[str]:
    from overseas_costing.services.import_service import _get_linked_purchase_approvals_from_extra

    return list(dict.fromkeys(
        str(row.get("source_instance_id") or "").strip()
        for row in _get_linked_purchase_approvals_from_extra(extra_json)
        if str(row.get("source_instance_id") or "").strip()
    ))


def _trusted_linked_instance_ids(payload: dict) -> list[str]:
    from overseas_costing.scripts.import_oa_logistics import extract_linked_purchase_approvals

    return list(dict.fromkeys(
        str(row.get("source_instance_id") or "").strip()
        for row in extract_linked_purchase_approvals(payload)
        if str(row.get("source_instance_id") or "").strip()
    ))


def _approval_matches_batch(batch: dict, payload: dict) -> bool:
    if str(batch.get("source_type") or "") != "oa_logistics":
        return False
    payload_instance = str(payload.get("processInstanceId") or payload.get("process_instance_id") or "").strip()
    payload_approval = str(payload.get("businessId") or payload.get("business_id") or "").strip()
    expected_instance = str(batch.get("source_instance_id") or "").strip()
    expected_approval = str(batch.get("source_approval_no") or "").strip()
    batch_no = str(batch.get("batch_no") or "").strip()
    return (
        bool(payload_instance and expected_instance and payload_instance == expected_instance)
        and bool(payload_approval and payload_approval in {expected_approval, batch_no})
    )


CREDENTIAL_KEY_RULES = (
    {"aliases": ("accessToken", "access_token"), "global": True, "url_suffix": True},
    {"aliases": ("authCode",), "global": True, "url_suffix": False},
    {"aliases": ("authMediaId",), "global": True, "url_suffix": False},
    {"aliases": ("token",), "global": False, "url_suffix": True},
    {"aliases": ("signature",), "global": False, "url_suffix": True},
    {"aliases": ("sig",), "global": False, "url_suffix": True},
    {"aliases": ("credential",), "global": False, "url_suffix": True},
    {"aliases": ("auth",), "global": False, "url_suffix": True},
    {"aliases": ("authorization",), "global": False, "url_suffix": True},
)


def _normalized_sensitive_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


GLOBAL_CREDENTIAL_KEYS = {
    _normalized_sensitive_key(alias)
    for rule in CREDENTIAL_KEY_RULES
    if rule["global"]
    for alias in rule["aliases"]
}
CONTEXTUAL_CREDENTIAL_KEYS = {
    _normalized_sensitive_key(alias)
    for rule in CREDENTIAL_KEY_RULES
    if not rule["global"]
    for alias in rule["aliases"]
}
URL_CREDENTIAL_KEYS = {
    _normalized_sensitive_key(alias)
    for rule in CREDENTIAL_KEY_RULES
    for alias in rule["aliases"]
}
URL_CREDENTIAL_SUFFIXES = {
    _normalized_sensitive_key(alias)
    for rule in CREDENTIAL_KEY_RULES
    if rule["url_suffix"]
    for alias in rule["aliases"]
}
FORM_VALUE_PRIVATE_KEYS = {
    "key",
    "rownumber",
} | GLOBAL_CREDENTIAL_KEYS
SENSITIVE_FORM_TEXT_PLACEHOLDER = "审批附件（敏感内容已隐藏）"


def _attachment_alias_contract():
    from overseas_costing.scripts.import_oa_logistics import (
        ATTACHMENT_FILE_ID_KEYS,
        ATTACHMENT_FILE_NAME_KEYS,
        ATTACHMENT_FILE_URL_KEYS,
        ATTACHMENT_SPACE_ID_KEYS,
        _looks_like_attachment_payload,
    )

    return {
        "identity_keys": (
            *ATTACHMENT_FILE_ID_KEYS,
            *ATTACHMENT_FILE_URL_KEYS,
            *ATTACHMENT_SPACE_ID_KEYS,
        ),
        "name_keys": ATTACHMENT_FILE_NAME_KEYS,
        "specific_name_keys": tuple(
            key for key in ATTACHMENT_FILE_NAME_KEYS if key not in {"name", "title"}
        ),
        "looks_like_attachment": _looks_like_attachment_payload,
    }


def _text_has_sensitive_auth_url(value: str) -> bool:
    lowered = value.lower()
    if "http://" not in lowered and "https://" not in lowered:
        return False
    for query_part in re.split(r"[?&]", lowered)[1:]:
        parameter_name, separator, _parameter_value = query_part.partition("=")
        normalized_name = _normalized_sensitive_key(parameter_name)
        if separator and (
            normalized_name in URL_CREDENTIAL_KEYS
            or any(normalized_name.endswith(suffix) for suffix in URL_CREDENTIAL_SUFFIXES)
        ):
            return True
    return False


def _safe_form_value(value, *, attachment_context: bool = False):
    if isinstance(value, list):
        return [
            _safe_form_value(item, attachment_context=attachment_context)
            for item in value
        ]
    if isinstance(value, str):
        return SENSITIVE_FORM_TEXT_PLACEHOLDER if _text_has_sensitive_auth_url(value) else value
    if not isinstance(value, dict):
        return value

    aliases = _attachment_alias_contract()
    identity_keys = aliases["identity_keys"]
    specific_name_keys = aliases["specific_name_keys"]
    has_specific_name = any(key in value for key in specific_name_keys)
    has_attachment_identity = any(
        value.get(key) not in (None, "") for key in identity_keys
    )
    is_attachment = has_specific_name and has_attachment_identity
    if attachment_context and aliases["looks_like_attachment"](value):
        is_attachment = True
    if is_attachment:
        name_keys = aliases["name_keys"] if attachment_context else specific_name_keys
        file_name = next(
            (value.get(key) for key in name_keys if value.get(key) not in (None, "")),
            "",
        )
        if not isinstance(file_name, str) or not file_name.strip():
            return "审批附件"
        return (
            SENSITIVE_FORM_TEXT_PLACEHOLDER
            if _text_has_sensitive_auth_url(file_name)
            else file_name
        )

    generic_identity_keys = {"id", "url"}
    specific_identity_keys = tuple(key for key in identity_keys if key not in generic_identity_keys)
    has_attachment_marker = attachment_context or has_specific_name or any(
        key in value for key in specific_identity_keys
    )
    private_keys = set(FORM_VALUE_PRIVATE_KEYS)
    if has_attachment_marker:
        private_keys.update(
            _normalized_sensitive_key(key) for key in identity_keys
        )
        private_keys.update(CONTEXTUAL_CREDENTIAL_KEYS)
    return {
        str(key): _safe_form_value(item, attachment_context=attachment_context)
        for key, item in value.items()
        if _normalized_sensitive_key(key) not in private_keys
    }


_UNPARSED_FORM_KEY_PATTERN = re.compile(
    r"(?i)(?:^\s*|[,{]\s*)[\"']?([A-Za-z][A-Za-z0-9_-]*)[\"']?\s*[:=]"
)


def _unparsed_form_text_keys(value: str) -> set[str]:
    return {
        _normalized_sensitive_key(match.group(1))
        for match in _UNPARSED_FORM_KEY_PATTERN.finditer(value)
    }


def _unparsed_form_text_is_sensitive(
    value: str,
    *,
    attachment_context: bool = False,
) -> bool:
    aliases = _attachment_alias_contract()
    extracted_keys = _unparsed_form_text_keys(value)
    identity_keys = {
        _normalized_sensitive_key(key) for key in aliases["identity_keys"]
    }
    generic_identity_keys = {
        _normalized_sensitive_key(key) for key in ("id", "url")
    }
    specific_identity_keys = identity_keys - generic_identity_keys
    credential_keys = {
        _normalized_sensitive_key(alias)
        for rule in CREDENTIAL_KEY_RULES
        if rule["global"] or attachment_context
        for alias in rule["aliases"]
    }
    if extracted_keys & (specific_identity_keys | credential_keys):
        return True
    if attachment_context and extracted_keys & identity_keys:
        return True
    name_keys = aliases["name_keys"] if attachment_context else aliases["specific_name_keys"]
    normalized_name_keys = {
        _normalized_sensitive_key(key) for key in name_keys
    }
    if extracted_keys & normalized_name_keys and extracted_keys & identity_keys:
        return True
    return _text_has_sensitive_auth_url(value)


def _display_value(value, *, attachment_context: bool = False) -> str:
    if value is None:
        return ""
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return (
                SENSITIVE_FORM_TEXT_PLACEHOLDER
                if _unparsed_form_text_is_sensitive(
                    value,
                    attachment_context=attachment_context,
                )
                else value
            )
    sanitized = _safe_form_value(decoded, attachment_context=attachment_context)
    if sanitized is None:
        return ""
    if isinstance(sanitized, list):
        if all(isinstance(item, str) for item in sanitized):
            return "；".join(sanitized)
        table_rows = []
        for item in sanitized:
            cells = item.get("rowValue") if isinstance(item, dict) else None
            if not isinstance(cells, list):
                table_rows = []
                break
            pairs = []
            for cell in cells:
                if not isinstance(cell, dict) or not str(cell.get("label") or "").strip():
                    table_rows = []
                    pairs = []
                    break
                label = str(cell.get("label"))
                cell_value = _display_value(cell.get("value"))
                pairs.append(f"{label}：{cell_value or '--'}")
            if not pairs:
                table_rows = []
                break
            table_rows.append("；".join(pairs))
        if table_rows:
            return "\n".join(table_rows)
    if isinstance(sanitized, (dict, list)):
        return json.dumps(sanitized, ensure_ascii=False)
    return str(sanitized)


def _table_value(value) -> dict | None:
    from overseas_costing.scripts.import_oa_logistics import _is_oa_goods_placeholder_row

    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
    sanitized = _safe_form_value(decoded)
    if not isinstance(sanitized, list) or not sanitized:
        return None

    columns: list[str] = []
    rows: list[list[str]] = []
    row_ids: list[str] = []
    for row_index, row in enumerate(sanitized):
        cells = row.get("rowValue") if isinstance(row, dict) else None
        if not isinstance(cells, list) or not cells:
            return None
        row_columns = []
        row_values = []
        for cell in cells:
            if not isinstance(cell, dict):
                return None
            label = str(cell.get("label") or "").strip()
            if not label:
                return None
            row_columns.append(label)
            row_values.append(_display_value(cell.get("value")))
        if row_index == 0:
            columns = row_columns
        elif row_columns != columns:
            return None
        raw_row = decoded[row_index] if isinstance(decoded[row_index], dict) else {}
        row_number = str(raw_row.get("rowNumber") or raw_row.get("row_number") or "").strip()
        placeholder_row = dict(zip(row_columns, row_values))
        if (
            row_number.upper() == "TABLEFIELD_HEADER"
            or _is_oa_goods_placeholder_row(placeholder_row)
        ):
            continue
        rows.append(row_values)
        row_ids.append(row_number)
    table = {"columns": columns, "rows": rows}
    if any(row_ids):
        table["row_ids"] = row_ids
    return table


def _form_fields(payload: dict) -> list[dict]:
    fields = _json_list(payload.get("formComponentValues") or payload.get("form_component_values"))
    result = []
    for row in fields:
        if not isinstance(row, dict):
            continue
        component_type = str(row.get("componentType") or row.get("component_type") or "")
        value = _display_value(
            row.get("value"),
            attachment_context=component_type.strip().lower() == "ddattachment",
        )
        table = _table_value(row.get("value")) if component_type == "TableField" else None
        result.append({
            "label": str(row.get("name") or row.get("label") or row.get("componentName") or "未命名字段"),
            "value": value,
            "component_type": component_type,
            "display_kind": "table" if table else "scalar",
            "is_empty": not value.strip(),
            "table": table or {"columns": [], "rows": []},
        })
    return result


def _attachment_field_records(payload: dict, process_instance_id: str = "") -> list[dict]:
    """Extract a safe attachment manifest from trusted approval components.

    The approval payload can expose a file before the archive index has created
    its row.  Keep enough identity to show and later materialize that pending
    file, without returning download credentials or private media tokens.
    """

    from overseas_costing.scripts.import_oa_logistics import extract_form_attachments

    records: dict[str, dict] = {}
    fields = _json_list(payload.get("formComponentValues") or payload.get("form_component_values"))
    for row in fields:
        if not isinstance(row, dict):
            continue
        source_field = str(row.get("name") or row.get("label") or row.get("componentName") or "").strip()
        workflow_field_id = str(
            row.get("componentId")
            or row.get("component_id")
            or row.get("componentKey")
            or row.get("component_key")
            or row.get("fieldId")
            or row.get("field_id")
            or row.get("id")
            or ""
        ).strip()
        # Reuse the importer parser so value/extValue, DingTalk aliases and
        # component types stay consistent with the archive producer.
        candidates = extract_form_attachments({"formComponentValues": [row]})
        for candidate in candidates:
            file_id = str(candidate.get("file_id") or "").strip()
            if file_id:
                records[file_id] = {
                    "process_instance_id": str(process_instance_id or ""),
                    "file_id": file_id,
                    "space_id": str(candidate.get("space_id") or "").strip(),
                    "file_name": str(candidate.get("file_name") or file_id).strip(),
                    "declared_size": candidate.get("file_size"),
                    "attachment_origin": "form",
                    "archive_status": "pending",
                    "source_field": source_field,
                    "workflow_field_id": workflow_field_id,
                }
    return list(records.values())


def _attachment_field_identities(payload: dict) -> dict[str, dict[str, str]]:
    """Map archived file IDs back to their trusted approval form components."""

    return {
        str(record["file_id"]): {
            "source_field": str(record.get("source_field") or ""),
            "workflow_field_id": str(record.get("workflow_field_id") or ""),
        }
        for record in _attachment_field_records(payload)
    }


def _actor_identity(
    *,
    corp_id: str,
    user_id: str,
    provided_name: str = "",
    actors: dict | None = None,
) -> dict:
    user_id = str(user_id or "").strip()
    provided_name = str(provided_name or "").strip()
    if provided_name and provided_name != user_id:
        return {
            "user_name": provided_name,
            "user_name_source": "payload",
            "user_name_unresolved": False,
        }
    if not user_id or user_id.lower() in {"bpms_system", "system", "dingtalk_system"}:
        return {
            "user_name": "系统",
            "user_name_source": "system",
            "user_name_unresolved": False,
        }
    actor = ((actors or {}).get(str(corp_id or ""), {}) or {}).get(user_id) or {}
    if isinstance(actor, str):
        actor = {"name": actor}
    actor_name = str(actor.get("name") or "").strip() if isinstance(actor, dict) else ""
    if actor_name and actor_name.lower() != "unknown" and actor_name != user_id:
        return {
            "user_name": actor_name,
            "user_name_source": "directory",
            "user_name_unresolved": False,
        }
    return {
        "user_name": "",
        "user_name_source": "unresolved",
        "user_name_unresolved": True,
    }


_MENTION_PATTERN = re.compile(
    r"\[([^\[\]\r\n]+)\]\(([A-Za-z0-9_-]+)\)",
)


def _remark_segments(remark: str) -> list[dict[str, str]]:
    if not remark:
        return []
    segments = []
    cursor = 0
    text_start = 0
    bracket_depth = 0
    for match in _MENTION_PATTERN.finditer(remark):
        for character in remark[cursor:match.start()]:
            if character == "[":
                bracket_depth += 1
            elif character == "]" and bracket_depth:
                bracket_depth -= 1
        if bracket_depth:
            cursor = match.end()
            continue
        if sum(character.isdigit() for character in match.group(2)) < 6:
            cursor = match.end()
            continue
        if match.start() > text_start:
            segments.append({"kind": "text", "text": remark[text_start:match.start()]})
        segments.append({"kind": "mention", "text": match.group(1)})
        cursor = match.end()
        text_start = match.end()
    if text_start < len(remark):
        segments.append({"kind": "text", "text": remark[text_start:]})
    return segments


COMMENT_OPERATION_TYPES = {"ADD_REMARK", "COMMENT"}
TASK_OPERATION_TYPES = {
    "EXECUTE_TASK",
    "EXECUTE_TASK_AGENT",
    "EXECUTE_TASK_APPEND",
    "EXECUTE_TASK_NORMAL",
    "EXECUTE_TASK_TRANSFER",
}
START_OPERATION_TYPES = {"PROCESS_START", "START_PROCESS", "START_PROCESS_INSTANCE"}
CC_OPERATION_TYPES = {"PROCESS_CC"}
SYNC_OPERATION_TYPES = {"SYNC", "SYNC_PROCESS"}


def _timeline_presentation(operation_type: str, operation_result: str, remark: str) -> tuple[str, str]:
    normalized_type = operation_type.strip().upper()
    normalized_result = operation_result.strip().upper()
    agreed = normalized_result in {"AGREE", "AGREED", "APPROVE", "APPROVED", "PASS", "PASSED", "同意", "通过"}
    refused = normalized_result in {
        "REFUSE", "REFUSED", "REJECT", "REJECTED", "DENY", "DENIED", "DISAGREE",
        "拒绝", "驳回", "不同意", "不通过",
    }
    if normalized_type in COMMENT_OPERATION_TYPES and remark:
        return "comment", "评论"
    if normalized_type in TASK_OPERATION_TYPES:
        if agreed:
            return "decision", "同意"
        if refused:
            return "decision", "拒绝"
        return "decision", "审批处理"
    if normalized_type in START_OPERATION_TYPES:
        return "system", "发起审批"
    if normalized_type in CC_OPERATION_TYPES:
        return "system", "抄送"
    if normalized_type in SYNC_OPERATION_TYPES:
        return "system", "同步"
    return "system", "其他流程记录"


def _timeline(payload: dict, instance_id: str, actors: dict | None = None) -> list[dict]:
    operations = _json_list(payload.get("operationRecords") or payload.get("operation_records") or payload.get("comments"))
    corp_id = str(payload.get("corpId") or payload.get("corp_id") or "")
    result = []
    for row in operations:
        if not isinstance(row, dict):
            continue
        user_id = str(row.get("userId") or row.get("user_id") or row.get("operatorUserId") or "")
        operation_time = str(row.get("date") or row.get("createTime") or row.get("operationTime") or "")
        remark = str(row.get("remark") or row.get("comment") or row.get("content") or "").strip()
        operation_type = str(row.get("type") or row.get("operationType") or "comment")
        operation_result = str(row.get("result") or "")
        event_kind, display_label = _timeline_presentation(operation_type, operation_result, remark)
        packing = parse_packing_comment(remark)
        identity = _actor_identity(
            corp_id=corp_id,
            user_id=user_id,
            provided_name=str(row.get("userName") or row.get("user_name") or row.get("operatorName") or ""),
            actors=actors,
        )
        item = {
            "operation_type": operation_type,
            "result": operation_result,
            "user_id": user_id,
            **identity,
            "operation_time": operation_time,
            "remark": remark,
            "packing_candidate": bool(packing.get("is_candidate")),
            "event_kind": event_kind,
            "display_label": display_label,
            "remark_segments": _remark_segments(remark),
        }
        if remark:
            item["source_id"] = build_comment_source_id(instance_id, operation_time, user_id, remark)
            if packing.get("is_candidate"):
                item["packing_preview"] = {key: value for key, value in packing.items() if key != "source_text"}
        result.append(item)
    return result


_LOCAL_ATTACHMENT_FIELDS = [
    "name",
    "version",
    "file_name",
    "file_url",
    "modified",
    "parse_result_json",
]


def attachment_has_complete_archive_binding(row: dict | None) -> bool:
    """Return whether an OA row is bound to one immutable approval archive file."""

    snapshot = _json_dict((row or {}).get("parse_result_json"))
    descriptor = snapshot.get("settlement_document") or {}
    manifest = descriptor.get("manifest") or {}
    if not isinstance(descriptor, dict) or not isinstance(manifest, dict):
        return False
    if descriptor.get("retired") or descriptor.get("disabled"):
        return False
    process_id = str(
        snapshot.get("process_instance_id") or snapshot.get("instance_id") or ""
    ).strip()
    file_id = str(snapshot.get("file_id") or "").strip()
    manifest_process_id = str(manifest.get("process_instance_id") or "").strip()
    manifest_file_id = str(manifest.get("file_id") or "").strip()
    return bool(
        descriptor.get("document_id")
        and descriptor.get("source_id")
        and descriptor.get("fingerprint")
        and process_id
        and file_id
        and manifest_process_id == process_id
        and manifest_file_id == file_id
    )


def _attachment_content_hash(row: dict | None) -> str:
    snapshot = _json_dict((row or {}).get("parse_result_json"))
    descriptor = snapshot.get("settlement_document") or {}
    manifest = descriptor.get("manifest") or {}
    return str(
        manifest.get("sha256")
        or snapshot.get("content_sha256")
        or snapshot.get("sha256")
        or ""
    ).strip().lower()


def _canonical_attachment_row(rows: list[dict], current_version: str = "") -> dict:
    """Choose one OA row by stable archive quality, never database row order."""

    current_version = str(current_version or "").strip()

    def rank(row: dict) -> tuple:
        row_version = str(row.get("version") or "").strip()
        is_current = not current_version or row_version == current_version
        has_binding = attachment_has_complete_archive_binding(row)
        has_file = bool(str(row.get("file_url") or "").strip())
        has_hash = bool(_attachment_content_hash(row))
        return (
            -int(is_current),
            -int(has_binding),
            -int(has_file and has_hash),
            -int(has_file),
            str(row.get("name") or ""),
        )

    return sorted((dict(row) for row in rows), key=rank)[0] if rows else {}


def _local_attachment_map(
    batch_name: str, current_version: str = ""
) -> dict[tuple[str, str], dict]:
    if frappe is None or not hasattr(frappe, "get_list"):
        return {}
    rows = frappe.get_list(
        "Overseas Cost Attachment",
        filters={"batch": batch_name, "source_type": "OA"},
        fields=_LOCAL_ATTACHMENT_FIELDS,
        limit_page_length=1000,
    )
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        snapshot = _json_dict(row.get("parse_result_json"))
        file_id = str(snapshot.get("file_id") or "").strip()
        instance_id = str(
            snapshot.get("process_instance_id") or snapshot.get("instance_id") or ""
        ).strip()
        if file_id:
            grouped[(instance_id, file_id)].append(row)
    return {
        identity: _canonical_attachment_row(candidates, current_version)
        for identity, candidates in grouped.items()
    }


def resolve_canonical_batch_attachment(
    batch_name: str,
    process_instance_id: str,
    file_id: str,
    *,
    current_version: str = "",
) -> dict:
    """Resolve an OA attachment after download without creating another row."""

    if frappe is None or not hasattr(frappe, "get_all"):
        return {}
    if not current_version:
        get_value = getattr(getattr(frappe, "db", None), "get_value", None)
        if callable(get_value):
            resolved_version = get_value(
                "Overseas Cost Batch", batch_name, "current_version"
            )
            if isinstance(resolved_version, dict):
                resolved_version = resolved_version.get("current_version")
            current_version = str(resolved_version or "")
    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters={"batch": batch_name, "source_type": "OA"},
        fields=_LOCAL_ATTACHMENT_FIELDS,
        limit_page_length=1000,
    )
    matches = []
    for row in rows:
        snapshot = _json_dict(row.get("parse_result_json"))
        if (
            str(snapshot.get("process_instance_id") or snapshot.get("instance_id") or "")
            == str(process_instance_id or "")
            and str(snapshot.get("file_id") or "") == str(file_id or "")
        ):
            matches.append(row)
    return _canonical_attachment_row(matches, current_version)


def _attachment_item(row: dict, local: dict | None, actors: dict | None = None) -> dict:
    file_name = str(row.get("file_name") or row.get("file_id") or "")
    archive_status = str(row.get("archive_status") or "pending")
    file_url = str((local or {}).get("file_url") or "")
    local_snapshot = _json_dict((local or {}).get("parse_result_json"))
    source_field = str(
        row.get("source_field")
        or row.get("field_name")
        or local_snapshot.get("source_field")
        or ""
    )
    workflow_field_id = str(
        row.get("workflow_field_id")
        or row.get("component_id")
        or row.get("component_key")
        or row.get("field_id")
        or local_snapshot.get("workflow_field_id")
        or ""
    )
    normalized_name = file_name.lower()
    suffix = Path(file_name).suffix.lower()
    packing_candidate = any(keyword in normalized_name for keyword in PACKING_NAME_KEYWORDS)
    if suffix in {".xlsx", ".xlsm"}:
        packing_candidate = packing_candidate or "packing" in normalized_name or "清单" in normalized_name
    normalized_field = source_field.replace("（", "(").replace("）", ")").replace(" ", "").lower()
    packing_candidate = packing_candidate or "装箱单附件" in normalized_field
    failure_code = str(row.get("failure_code") or "")
    failure_reason = str(row.get("last_error") or "")
    if failure_code == "userNotExist" or "userNotExist" in failure_reason:
        failure_reason = "钉钉下载授权返回 userNotExist，尚不能判断文件是否删除。"
    comment_user_id = str(row.get("comment_user_id") or "")
    comment_user_name = str(row.get("comment_user_name") or "")
    if comment_user_id or comment_user_name:
        comment_identity = _actor_identity(
            corp_id=str(row.get("corp_id") or ""),
            user_id=comment_user_id,
            provided_name=comment_user_name,
            actors=actors,
        )
    else:
        comment_identity = {
            "user_name": "",
            "user_name_source": "",
            "user_name_unresolved": False,
        }
    return {
        "attachment_name": (local or {}).get("name") or "",
        "file_id": str(row.get("file_id") or ""),
        "process_instance_id": str(row.get("process_instance_id") or ""),
        "space_id": str(row.get("space_id") or ""),
        "file_name": file_name,
        "source_field": source_field,
        "workflow_field_id": workflow_field_id,
        "declared_size": row.get("declared_size"),
        "actual_size": row.get("actual_size"),
        "origin": "Comment" if str(row.get("attachment_origin")) == "comment" else "Form",
        "comment_user_id": comment_user_id,
        "comment_user_name": comment_identity["user_name"],
        "comment_user_name_source": comment_identity["user_name_source"],
        "comment_user_name_unresolved": comment_identity["user_name_unresolved"],
        "comment_time": str(row.get("comment_time") or ""),
        "comment_remark": str(row.get("comment_remark") or ""),
        "archive_status": archive_status,
        "archive_method": str(row.get("archive_method") or ""),
        "content_quality": str(row.get("content_quality") or ""),
        "failure_code": failure_code,
        "failure_reason": failure_reason,
        "file_url": file_url,
        "previewable": bool(file_url) or archive_status == "archived",
        "downloadable": bool(file_url) or archive_status == "archived",
        "packing_candidate": packing_candidate,
    }


def _approval(payload: dict, attachments: list[dict], actors: dict | None = None) -> dict:
    from overseas_costing.scripts.import_oa_logistics import resolve_approval_decision

    instance_id = str(payload.get("processInstanceId") or payload.get("process_instance_id") or "")
    corp_id = str(payload.get("corpId") or payload.get("corp_id") or "")
    raw_status = str(payload.get("status") or "")
    raw_result = str(payload.get("result") or "")
    decision = resolve_approval_decision(raw_status, raw_result)
    status_upper = raw_status.strip().upper()
    rejection_tokens = ("REJECT", "REFUS", "DENY", "DISAGREE", "拒绝", "驳回", "不通过", "未通过")
    if decision.get("excluded"):
        if raw_result.strip() and decision.get("effective_status") == "REJECTED":
            exclusion_reason = "审批结果为拒绝"
        elif any(token in status_upper for token in rejection_tokens):
            exclusion_reason = "审批状态为拒绝"
        else:
            exclusion_reason = "审批已撤销、终止或作废"
    else:
        exclusion_reason = ""
    originator_user_id = str(payload.get("originatorUserId") or payload.get("originator_user_id") or "")
    originator_identity = _actor_identity(
        corp_id=corp_id,
        user_id=originator_user_id,
        provided_name=str(payload.get("originatorUserName") or payload.get("originator_user_name") or ""),
        actors=actors,
    )
    return {
        "corp_id": corp_id,
        "instance_id": instance_id,
        "business_id": str(payload.get("businessId") or payload.get("business_id") or ""),
        "process_code": str(payload.get("processCode") or payload.get("process_code") or ""),
        "title": str(payload.get("title") or ""),
        "status": raw_status,
        "result": raw_result,
        "raw_status": raw_status,
        "raw_result": raw_result,
        "process_status": decision.get("process_status") or "",
        "approval_result": decision.get("approval_result") or "",
        "effective_status": decision.get("effective_status") or "",
        "excluded": bool(decision.get("excluded")),
        "exclusion_reason": exclusion_reason,
        "originator_user_id": originator_user_id,
        "originator_user_name": originator_identity["user_name"],
        "originator_name_source": originator_identity["user_name_source"],
        "originator_name_unresolved": originator_identity["user_name_unresolved"],
        "originator_dept_name": str(payload.get("originatorDeptName") or payload.get("originator_dept_name") or ""),
        "create_time": str(payload.get("createTime") or payload.get("create_time") or ""),
        "finish_time": str(payload.get("finishTime") or payload.get("finish_time") or ""),
        "form_fields": _form_fields(payload),
        "timeline": _timeline(payload, instance_id, actors),
        "attachments": attachments,
    }


def get_batch_dingtalk_approval_detail(batch_name: str) -> dict:
    if frappe is None:
        return {"ok": False, "message": "当前未连接 Frappe。"}
    batch = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_name,
        [
            "name",
            "batch_no",
            "source_type",
            "source_approval_no",
            "source_instance_id",
            "current_version",
            "extra_json",
        ],
        as_dict=True,
    ) or {}
    main_id = str(batch.get("source_instance_id") or "").strip()
    if not main_id:
        return {
            "ok": False,
            "message": "当前批次缺少钉钉审批实例 ID，暂无法判断关联采购审批。",
            "source_state": {
                "code": "missing_instance_id",
                "repairable": False,
                "purchase_link_state": "unknown",
            },
        }
    candidate_linked_ids = [value for value in _linked_instance_ids(batch.get("extra_json")) if value != main_id]
    instance_ids = [main_id, *candidate_linked_ids]
    try:
        source = _get_approval_source()
    except ValueError as error:
        message = str(error)
        if "PostgreSQL OA" not in message:
            raise
        return {
            "ok": False,
            "batch_name": batch.get("name") or batch_name,
            "message": message,
            "data_source": "postgres",
            "fallback_used": False,
            "source_state": {
                "code": "data_source_unavailable",
                "repairable": False,
                "purchase_link_state": "unknown",
                "failure_code": "missing_postgres_config",
                "failure_reason": message,
            },
        }
    bundle = source.get_instance_bundle(instance_ids)
    instances = bundle.get("instances") or {}
    main_payload = instances.get(main_id)
    if not isinstance(main_payload, dict):
        repair_statuses = source.get_repair_statuses([main_id]) if hasattr(source, "get_repair_statuses") else {}
        repair_status = repair_statuses.get(main_id) or {}
        repair_state = str(repair_status.get("status") or "")
        repairing = repair_state in {"pending", "running", "retry"}
        return {
            "ok": False,
            "batch_name": batch.get("name") or batch_name,
            "message": "钉钉审批正在补同步，请稍后重试。" if repairing else "钉钉同步库缺少该物流审批，暂无法判断关联采购审批。",
            "data_source": "postgres",
            "fallback_used": False,
            "repair_status": repair_status,
            "source_state": {
                "code": "repairing" if repairing else ("manual_required" if repair_state == "manual_required" else "missing_in_postgres"),
                "repairable": True,
                "purchase_link_state": "unknown",
                "failure_code": repair_status.get("error_code") or "",
                "failure_reason": repair_status.get("error_message") or "",
            },
        }
    if not _approval_matches_batch(batch, main_payload):
        return {
            "ok": False,
            "message": "批次来源与钉钉物流审批不一致，已拒绝显示审批内容。",
            "source_state": {
                "code": "source_mismatch",
                "repairable": False,
                "purchase_link_state": "unknown",
            },
        }

    trusted_linked_ids = [value for value in _trusted_linked_instance_ids(main_payload) if value != main_id]
    missing_from_first_bundle = [value for value in trusted_linked_ids if value not in instances]
    if missing_from_first_bundle:
        linked_bundle = source.get_instance_bundle(missing_from_first_bundle)
        instances = {**instances, **(linked_bundle.get("instances") or {})}
        bundle["attachments"] = [*(bundle.get("attachments") or []), *(linked_bundle.get("attachments") or [])]
        actors = dict(bundle.get("actors") or {})
        for corp_id, values in (linked_bundle.get("actors") or {}).items():
            actors.setdefault(corp_id, {}).update(values or {})
        bundle["actors"] = actors
        if not bundle.get("health") and linked_bundle.get("health"):
            bundle["health"] = linked_bundle.get("health")

    actors = bundle.get("actors") or {}
    manifests_by_instance: dict[str, list[dict]] = defaultdict(list)
    local_by_file = _local_attachment_map(
        batch.get("name") or batch_name,
        current_version=str(batch.get("current_version") or ""),
    )
    field_records_by_instance = {
        instance_id: _attachment_field_records(payload, instance_id)
        for instance_id, payload in instances.items()
        if isinstance(payload, dict)
    }
    field_identities_by_instance = {
        instance_id: {
            str(record.get("file_id") or ""): {
                "source_field": str(record.get("source_field") or ""),
                "workflow_field_id": str(record.get("workflow_field_id") or ""),
            }
            for record in records
            if str(record.get("file_id") or "")
        }
        for instance_id, records in field_records_by_instance.items()
    }
    indexed_attachment_ids: set[tuple[str, str]] = set()
    for row in bundle.get("attachments") or []:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("process_instance_id") or "")
        file_id = str(row.get("file_id") or "")
        if instance_id and file_id:
            indexed_attachment_ids.add((instance_id, file_id))
        trusted_identity = field_identities_by_instance.get(instance_id, {}).get(file_id) or {}
        enriched_row = {
            **row,
            **trusted_identity,
        }
        manifests_by_instance[instance_id].append(
            _attachment_item(enriched_row, local_by_file.get((instance_id, file_id)), actors)
        )

    # An attachment declared in a trusted workflow component must remain visible
    # even while its asynchronous archive row is missing.  If the source can
    # already resolve the archive manifest, merge it immediately so the same
    # preview can download the file; otherwise expose an explicit pending row.
    manifest_reader = getattr(source, "get_attachment_manifest", None)
    scoped_instance_ids = {main_id, *trusted_linked_ids}
    for instance_id in scoped_instance_ids:
        for field_record in field_records_by_instance.get(instance_id, []):
            file_id = str(field_record.get("file_id") or "")
            identity = (instance_id, file_id)
            if not file_id or identity in indexed_attachment_ids:
                continue
            archive_manifest = None
            if callable(manifest_reader):
                try:
                    archive_manifest = manifest_reader(instance_id, file_id)
                except Exception:
                    archive_manifest = None
            resolved_row = {
                **field_record,
                **(archive_manifest if isinstance(archive_manifest, dict) else {}),
                "process_instance_id": instance_id,
                "file_id": file_id,
                "source_field": field_record.get("source_field") or "",
                "workflow_field_id": field_record.get("workflow_field_id") or "",
            }
            manifests_by_instance[instance_id].append(
                _attachment_item(resolved_row, local_by_file.get(identity), actors)
            )
            indexed_attachment_ids.add(identity)
    linked_ids = trusted_linked_ids
    health = bundle.get("health") or {}
    archive_rows = [row for rows in manifests_by_instance.values() for row in rows]
    archive_health = {
        "total": len(archive_rows),
        "archived": sum(1 for row in archive_rows if row.get("archive_status") == "archived"),
        "pending": sum(1 for row in archive_rows if row.get("archive_status") in {"pending", "archiving", "retry"}),
        "manual_required": sum(1 for row in archive_rows if row.get("archive_status") == "manual_required"),
        "preview_only": sum(1 for row in archive_rows if row.get("content_quality") == "preview"),
    }
    main_approval = _approval(main_payload, manifests_by_instance.get(main_id, []), actors)
    linked_approvals = [
        _approval(instances[instance_id], manifests_by_instance.get(instance_id, []), actors)
        for instance_id in linked_ids
        if isinstance(instances.get(instance_id), dict)
    ]
    missing_linked_ids = [instance_id for instance_id in linked_ids if instance_id not in instances]
    source_state = {
        "code": "excluded" if main_approval.get("excluded") else "available",
        "repairable": False,
        "purchase_link_state": (
            "repairing" if missing_linked_ids else ("available" if linked_ids else "none")
        ),
    }
    return {
        "ok": True,
        "batch_name": batch.get("name") or batch_name,
        "data_source": "postgres",
        "fallback_used": False,
        "source_updated_at": health.get("source_updated_at"),
        "source_lag_seconds": health.get("source_lag_seconds"),
        "archive_health": archive_health,
        "source_state": source_state,
        "main_approval": main_approval,
        "linked_purchase_approvals": [row for row in linked_approvals if not row.get("excluded")],
        "excluded_linked_purchase_approvals": [row for row in linked_approvals if row.get("excluded")],
        "missing_linked_instance_ids": missing_linked_ids,
    }


def _backfill_attachment_audit_policy(attachment_name: str | None, source_approval: dict) -> None:
    """Fail closed when an existing local attachment belongs to an excluded approval."""

    if not attachment_name or not source_approval.get("excluded"):
        return
    get_doc = getattr(frappe, "get_doc", None)
    if not callable(get_doc):
        return
    attachment_doc = get_doc("Overseas Cost Attachment", attachment_name)
    snapshot = _json_dict(getattr(attachment_doc, "parse_result_json", ""))
    expected = {
        "approval_excluded": True,
        "cost_source_allowed": False,
        "exclusion_reason": source_approval.get("exclusion_reason") or "",
    }
    changed = any(snapshot.get(key) != value for key, value in expected.items())
    snapshot.update(expected)
    if changed:
        attachment_doc.parse_result_json = json.dumps(snapshot, ensure_ascii=False, default=str)
    if str(getattr(attachment_doc, "attachment_type", "") or "") != "Other":
        attachment_doc.attachment_type = "Other"
        changed = True
    if changed:
        attachment_doc.save(ignore_permissions=True)


def materialize_batch_dingtalk_attachment(batch_name: str, process_instance_id: str, file_id: str) -> dict:
    """Create the local attachment record only after an authorized user requests the archived file."""

    detail = get_batch_dingtalk_approval_detail(batch_name)
    if not detail.get("ok"):
        return detail
    approvals = [
        detail.get("main_approval"),
        *(detail.get("linked_purchase_approvals") or []),
        *(detail.get("excluded_linked_purchase_approvals") or []),
    ]
    source_approval = next(
        (
            approval for approval in approvals
            if isinstance(approval, dict) and str(approval.get("instance_id") or "") == str(process_instance_id or "")
        ),
        None,
    )
    if not source_approval:
        return {"ok": False, "message": "该审批不属于当前批次。"}
    source = next(
        (
            item for item in source_approval.get("attachments") or []
            if str(item.get("file_id") or "") == str(file_id or "")
        ),
        None,
    )
    if not source:
        return {"ok": False, "message": "该附件不属于当前批次审批。"}
    if source.get("attachment_name"):
        _backfill_attachment_audit_policy(source["attachment_name"], source_approval)
        return {"ok": True, "attachment_name": source["attachment_name"], "created": False}
    if str(source.get("archive_status") or "") != "archived":
        return {"ok": False, "message": source.get("failure_reason") or "附件尚未归档，暂不能下载。"}

    sql = getattr(getattr(frappe, "db", None), "sql", None)
    if callable(sql):
        sql(
            "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE",
            (batch_name,),
        )
    get_value = getattr(getattr(frappe, "db", None), "get_value", None)
    batch = (
        get_value(
            "Overseas Cost Batch",
            batch_name,
            ["name", "current_version"],
            as_dict=True,
        )
        if callable(get_value)
        else {}
    ) or {}
    existing = resolve_canonical_batch_attachment(
        batch_name,
        process_instance_id,
        file_id,
        current_version=str(batch.get("current_version") or ""),
    )
    if existing:
        _backfill_attachment_audit_policy(existing.get("name"), source_approval)
        return {
            "ok": True,
            "attachment_name": existing.get("name"),
            "created": False,
        }

    parse_snapshot = {
        "source": "dingtalk_postgres_archive",
        "approval_excluded": bool(source_approval.get("excluded")),
        "cost_source_allowed": not bool(source_approval.get("excluded")),
        "exclusion_reason": source_approval.get("exclusion_reason") or "",
        "process_instance_id": process_instance_id,
        "file_id": file_id,
        "space_id": source.get("space_id") or "",
        "source_field": source.get("source_field") or "",
        "workflow_field_id": source.get("workflow_field_id") or "",
        "attachment_origin": source.get("origin") or "Form",
        "comment_user_name": source.get("comment_user_name") or "",
        "comment_time": source.get("comment_time") or "",
        "comment_remark": source.get("comment_remark") or "",
        "archive": {
            "status": source.get("archive_status") or "archived",
            "archive_method": source.get("archive_method") or "",
            "content_quality": source.get("content_quality") or "",
        },
    }
    doc = frappe.get_doc({
        "doctype": "Overseas Cost Attachment",
        "batch": batch.get("name") or batch_name,
        "version": batch.get("current_version") or None,
        "source_type": "OA",
        "oa_attachment_origin": source.get("origin") or "Form",
        "attachment_type": (
            "Packing List"
            if source.get("packing_candidate") and not source_approval.get("excluded")
            else "Other"
        ),
        "source_doc_no": source_approval.get("business_id") or process_instance_id,
        "file_name": source.get("file_name") or file_id,
        "parse_status": "Queued",
        "parse_result_json": json.dumps(parse_snapshot, ensure_ascii=False),
        "remark": (
            "由已排除钉钉审批归档按需创建，仅供审计查看和下载，不可作为成本或装箱数据源。"
            if source_approval.get("excluded")
            else "由钉钉审批归档清单按需创建，文件内容将从 MinIO 下载。"
        ),
    }).insert(ignore_permissions=True)
    return {"ok": True, "attachment_name": doc.name, "created": True}
