"""Server-owned source catalog for unified material and fee review runs.

The browser is only allowed to send the opaque ``source_id`` values produced
here.  Resolver ids, URLs, attachment contents and comment bodies stay in the
run manifest and are revalidated by the server before application.
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
from copy import deepcopy
from pathlib import PurePath
from typing import Any, Iterable, Optional


READ_STATUSES = frozenset(
    {"READ", "PARTIAL", "FAILED", "SKIPPED", "UNREADABLE", "NO_RESULT", "EXCLUDED", "NEEDS_SELECTION"}
)
PARSE_METHODS = frozenset(
    {"SYSTEM_APPROVAL", "SYSTEM_EXCEL", "AI_TEXT", "AI_VISION", "NONE"}
)


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _stable_sheet_id(parent_source_id: str, sheet_name: str) -> str:
    digest = hashlib.sha256(sheet_name.encode("utf-8")).hexdigest()[:20]
    return f"{parent_source_id}:sheet:{digest}"


def stable_source_identity(source: dict) -> tuple[str, str]:
    """Return the public source id and optional public parent id."""

    base = _text(source.get("logical_source_id") or source.get("source_id"), 500)
    if not base:
        raise ValueError(_("资料来源缺少服务器标识。"))
    sheet_name = _text(source.get("sheet_name"), 200)
    if not sheet_name:
        return base, ""
    return _stable_sheet_id(base, sheet_name), base


def source_parse_method(source: dict) -> str:
    readable = bool(source.get("available", True)) or bool(source.get("can_download"))
    if source.get("excluded") or not readable:
        return "NONE"
    kind = _text(source.get("source_kind"), 60)
    if kind == "approval_form":
        return "SYSTEM_APPROVAL"
    file_name = _text(source.get("file_name") or source.get("source_label")).lower()
    suffix = PurePath(file_name).suffix
    context = source.get('source_context') or {}
    if kind == "wiki_sheet" or suffix in {".xlsx", ".xlsm"} or (suffix == '.xls' and (context.get('root_kind') == 'expense' or context.get('source_lineage'))):
        return "SYSTEM_EXCEL"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return "AI_VISION"
    if kind == "approval_comment" or suffix in {".pdf", ".doc", ".docx", ".txt"}:
        return "AI_TEXT"
    return "NONE"


def prepare_source_manifest(
    raw_sources: Iterable[dict], selected_source_ids: Optional[Iterable[str]] = None
) -> list[dict]:
    """Create and validate the immutable source selection for a review run.

    ``None`` selects every selectable source.  An empty collection selects only
    locked approval bodies.  Unknown and excluded ids are rejected before a run
    is created.
    """

    raw_sources = list(raw_sources or [])
    contexts = [row.get('source_context') or {} for row in raw_sources if (row.get('source_context') or {}).get('root_kind') == 'expense']
    contexts = contexts or [row['source_context'] for row in raw_sources
                            if (row.get('source_context') or {}).get('source_lineage')]
    if contexts:
        context = contexts[0]
        for row in raw_sources:
            own = row.get('source_context') or {}
            if (own.get('fingerprint') == context.get('fingerprint')
                    and row.get('process_instance_id') == context.get('instance_id')
                    and row.get('source_kind') != 'manual_attachment'):
                continue
            lineage = own.get('source_lineage') or {}
            related = bool(context.get('batch') and context.get('cost_version')
                and own.get('batch') == context['batch'] == lineage.get('batch')
                and own.get('cost_version') == context['cost_version'] == lineage.get('cost_version')
                and lineage.get('logistics_source_id') and lineage.get('logistics_snapshot')
                and str(row.get('process_instance_id') or '') == str(own.get('instance_id') or '') == str(lineage.get('instance_id') or '')
                and (not row.get('process_instance_id') or (
                    own.get('root_source_id') and own.get('source_snapshot')
                    and str(own.get('corp_id') or '') == str(context.get('corp_id') or ''))))
            if not related:
                raise ValueError(_('资料来源缺少同批次当前有效关联，请刷新来源。'))
    prepared: list[dict] = []
    by_id: dict[str, dict] = {}
    if not any(row.get('analysis_allowed') is not False and not row.get('excluded')
               and (row.get('available', True) or row.get('can_download')) for row in raw_sources):
        required = next((row for row in raw_sources if row.get('analysis_required')
                         and row.get('analysis_allowed') is False), None)
        if required:
            from .material_ai_source_dependencies import SourceEligibilityError
            raise SourceEligibilityError(required.get('analysis_reason') or '资料不可读取。', source=required,
                code=required.get('analysis_code') or 'SOURCE_UNAVAILABLE')
    for raw in raw_sources or []:
        source = deepcopy(raw or {})
        if source.get('analysis_allowed') is False:
            source.update(excluded=True, exclude_reason=source.get('analysis_reason') or '来源不可读取。')
        public_id, parent_id = stable_source_identity(source)
        if public_id in by_id:
            raise ValueError(_("资料来源标识重复：{0}。").format(public_id))
        locked = _text(source.get("source_kind"), 60) == "approval_form" and not source.get("excluded") and not source.get('scoped_packing')
        selectable = (
            bool(source.get("available", True)) or bool(source.get("can_download"))
        ) and not bool(source.get("excluded"))
        source.update(
            {
                "resolver_source_id": _text(source.get("source_id"), 500),
                "source_id": public_id,
                "parent_source_id": parent_id,
                "locked": locked,
                "selectable": selectable,
                "parse_method": source_parse_method(source),
                "approval_no": _text(
                    source.get("approval_no") or source.get("business_id"), 200
                ),
                "actor_name": _text(
                    source.get("actor_name")
                    or source.get("comment_user_name")
                    or source.get("comment_user"),
                    200,
                ),
                "occurred_at": _text(
                    source.get("occurred_at") or source.get("source_updated_at"), 100
                ),
            }
        )
        prepared.append(source)
        by_id[public_id] = source

    explicit = None if selected_source_ids is None else {
        _text(source_id, 500) for source_id in selected_source_ids if _text(source_id, 500)
    }
    if explicit is not None:
        unknown = sorted(explicit - set(by_id))
        if unknown:
            raise ValueError(_("资料来源 {0} 不属于当前批次或已失效。").format(unknown[0]))
        unavailable = sorted(source_id for source_id in explicit if not by_id[source_id]["selectable"])
        if unavailable:
            from .material_ai_source_dependencies import SourceEligibilityError
            source = by_id[unavailable[0]]
            reason = source.get('analysis_reason') or source.get('exclude_reason') or '资料不可读取，请核对来源。'
            raise SourceEligibilityError(_("{0} 当前不可选。").format(reason), source=source,
                code=source.get('analysis_code') or 'SOURCE_UNAVAILABLE')

    for source in prepared:
        selected = bool(source["locked"])
        if source["selectable"] and not source["locked"]:
            selected = explicit is None or source["source_id"] in explicit
        source["selected"] = selected
        if not source["selectable"] or (not selected and not source["locked"]):
            source["read_status"] = "EXCLUDED"
        elif source.get("needs_selection"):
            source["read_status"] = "NEEDS_SELECTION"
        else:
            source["read_status"] = "NO_RESULT"
    return prepared


def source_progress_manifest(manifest: Iterable[dict]) -> list[dict]:
    """Return browser-safe source status rows without contents or locations."""

    from overseas_costing.services.effective_logistics_source import public_context
    rows = []
    for source in manifest or []:
        read_status = _text(source.get("read_status"), 40) or "NO_RESULT"
        if read_status not in READ_STATUSES:
            read_status = "NO_RESULT"
        parse_method = _text(source.get("parse_method"), 40) or "NONE"
        if parse_method not in PARSE_METHODS:
            parse_method = "NONE"
        excluded_reason = _text(source.get("exclude_reason"), 1000)
        evidence_kind = _text(source.get("evidence_kind"), 60)
        if not evidence_kind:
            evidence_kind = {
                "approval_form": "approval_form",
                "approval_comment": "approval_comment",
                "wiki_sheet": "sheet",
                "approval_attachment": "attachment",
                "approval_comment_attachment": "attachment",
                "manual_attachment": "attachment",
            }.get(_text(source.get("source_kind"), 60), "other")
        skip_reason_text = _text(source.get("skip_reason_text"), 300)
        skipped = read_status in {"SKIPPED", "UNREADABLE"}
        rows.append(
            {
                "source_id": _text(source.get("source_id"), 500),
                "evidence_id": _text(source.get("source_id"), 500),
                "parent_source_id": _text(source.get("parent_source_id"), 500),
                "source_kind": _text(source.get("source_kind"), 60),
                "source_context": public_context(source.get('source_context') or {}),
                "label": _text(
                    source.get("source_label")
                    or source.get("file_name")
                    or source.get("source_id"),
                    500,
                ),
                "approval_no": _text(source.get("approval_no"), 200),
                "actor_name": _text(source.get("actor_name"), 200),
                "occurred_at": _text(source.get("occurred_at"), 100),
                "sheet_name": _text(source.get("sheet_name"), 200),
                "sheet": _text(source.get("sheet_name"), 200),
                "priority": int(source.get("priority") or 0),
                "priority_reason": _text(source.get("priority_reason"), 500),
                "workflow_stage": _text(source.get("workflow_stage"), 60),
                "workflow_rank": int(source.get("workflow_rank") if source.get("workflow_rank") is not None else 3),
                "evidence_kind": evidence_kind,
                "evidence_rank": int(source.get("evidence_rank") if source.get("evidence_rank") is not None else 4),
                "actual_packing_match_status": _text(source.get("actual_packing_match_status"), 40),
                "actual_packing_match_id": _text(source.get("actual_packing_match_id"), 500),
                "actual_packing_match_revision": _text(source.get("actual_packing_match_revision"), 500),
                "source_field": _text(source.get("source_field"), 500),
                "workflow_field_id": _text(source.get("workflow_field_id"), 500),
                "dedicated_packing_attachment": bool(source.get("dedicated_packing_attachment")),
                "selected": bool(source.get("selected")),
                "locked": bool(source.get("locked")),
                "selectable": bool(source.get("selectable")),
                "analysis_allowed": bool(source.get("analysis_allowed", source.get("selectable"))),
                "analysis_reason": _text(source.get("analysis_reason"), 1000),
                "adoption_allowed": bool(source.get("adoption_allowed", True)),
                "final_fee_allowed": bool(source.get("final_fee_allowed", False)),
                "adoption_restriction": _text(source.get("adoption_restriction"), 1000),
                "read_status": read_status,
                "parse_method": parse_method,
                "result_count": int(source.get("result_count") or 0),
                "error": "" if skipped else _text(source.get("error") or excluded_reason, 1000),
                "skip_reason_code": _text(source.get("skip_reason_code"), 80),
                "skip_reason_text": skip_reason_text,
                "elapsed_ms": max(0, int(source.get("elapsed_ms") or 0)),
                "sheet_options": deepcopy(source.get("sheet_options") or []),
                # Kept during rollout for older progress renderers.
                "status": "WAITING" if read_status == "NO_RESULT" and source.get("selected") else read_status,
                "detail": (
                    f"{skip_reason_text or '资料无法读取。'}已跳过，继续读取下一资料。"
                    if skipped
                    else excluded_reason or ("等待读取" if source.get("selected") else "已排除")
                ),
                "field_count": len(source.get("form_fields") or {})
                if isinstance(source.get("form_fields"), dict)
                else 0,
                "page_count": 0,
                "candidate_count": int(source.get("result_count") or 0),
            }
        )
    return rows


def selected_manifest_sources(manifest: Iterable[dict]) -> list[dict]:
    return [deepcopy(source) for source in manifest or [] if source.get("selected")]
