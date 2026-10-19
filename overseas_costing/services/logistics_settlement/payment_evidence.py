"""Safe payment-document suggestions and transactional fee-evidence materialization."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from decimal import Decimal
import json
from pathlib import PurePosixPath
from urllib.parse import unquote

from .jobs import utcnow
from .model import digest, dumps, norm


ATTACHMENT_TYPES = {
    "Excel Main Table", "Logistics Bill", "Commercial Invoice", "Purchase Order",
    "Packing List", "Tax Certificate", "Customs Declaration", "Other",
}
CHOICE_FIELDS = {"document_id", "selected", "logical_fee_keys"}


def _origin(document):
    manifest = document.get("manifest") or {}
    value = str(manifest.get("attachment_origin") or manifest.get("source_kind")
                or manifest.get("source_type") or manifest.get("origin") or "").lower()
    return "Comment" if "comment" in value else "Form"


def _document_type(document):
    manifest = document.get("manifest") or {}
    explicit = str(document.get("attachment_type") or manifest.get("attachment_type") or "").strip()
    if explicit in ATTACHMENT_TYPES:
        return explicit
    text = norm(str(document.get("file_name") or manifest.get("file_name") or ""))
    if any(token in text for token in ("tax", "impuesto", "税", "关税")):
        return "Tax Certificate"
    if any(token in text for token in ("customs", "aduana", "清关", "报关")):
        return "Customs Declaration"
    if any(token in text for token in ("invoice", "factura", "发票", "账单", "bill")):
        return "Commercial Invoice"
    if any(token in text for token in ("payment", "receipt", "comprobante", "付款", "支付", "回单")):
        return "Other"
    return "Other"


def _local_private_url(document):
    url = str(document.get("file_url") or "")
    decoded = unquote(url)
    return bool(decoded.startswith("/private/files/") and ".." not in PurePosixPath(decoded).parts
                and "?" not in decoded and "#" not in decoded)


def _availability(document):
    manifest = document.get("manifest") or {}
    if document.get("retired_at") or manifest.get("retired_at"):
        return "retired", "来源附件已撤销或被替代"
    archive_status = str(manifest.get("archive_status") or "").lower()
    if archive_status != "archived":
        return "pending", "来源附件尚未完成归档"
    if not _local_private_url(document):
        return "unavailable", "来源附件尚无可用的本地私有归档"
    return "available", ""


def document_fingerprint(document):
    """Hash document authority without publishing archive URLs or credentials."""
    manifest = document.get("manifest") or {}
    status, _blocker = _availability(document)
    return digest(
        "payment-evidence-document-1",
        document.get("id"),
        document.get("fingerprint"),
        manifest.get("sha256") or manifest.get("content_hash"),
        manifest.get("file_id"),
        manifest.get("archive_status"),
        manifest.get("archive_quality") or manifest.get("content_quality"),
        manifest.get("retired_at") or document.get("retired_at"),
        document.get("source_field_id") or manifest.get("source_field_id") or manifest.get("field_id"),
        document.get("source_field_name") or manifest.get("source_field_name") or manifest.get("field_name"),
        document.get("source_comment_id") or manifest.get("source_comment_id") or manifest.get("comment_id"),
        document.get("process_instance_id") or manifest.get("process_instance_id") or manifest.get("approval_instance"),
        document.get("status"),
        _origin(document),
        _document_type(document),
        str(document.get("file_name") or manifest.get("file_name") or ""),
        status,
        digest("private-path", str(document.get("file_url") or "")),
    )


def content_fingerprint(document):
    manifest = document.get("manifest") or {}
    return str(manifest.get("sha256") or manifest.get("content_hash")
               or document.get("fingerprint") or document_fingerprint(document))


def _retry_content_fingerprint(document):
    """Return only an archive-independent, verifiable content identity."""
    manifest = document.get("manifest") or {}
    return str(manifest.get("sha256") or manifest.get("content_hash") or document.get("fingerprint") or "")


def _retry_document_identity(document):
    manifest = document.get("manifest") or {}
    return digest(
        "payment-evidence-document-identity-1", document.get("id"),
        document.get("file_id") or manifest.get("file_id"),
        str(document.get("file_name") or manifest.get("file_name") or ""),
        _origin(document), _document_type(document),
    )


def _retry_source_relation(document):
    manifest = document.get("manifest") or {}
    return digest(
        "payment-evidence-source-relation-1",
        document.get("source_field_id") or manifest.get("source_field_id") or manifest.get("field_id"),
        document.get("source_field_name") or manifest.get("source_field_name") or manifest.get("field_name"),
        document.get("source_comment_id") or manifest.get("source_comment_id") or manifest.get("comment_id"),
        document.get("process_instance_id") or manifest.get("process_instance_id") or manifest.get("approval_instance"),
    )


def _source_business_fingerprint(source):
    fields = (
        "id", "corp", "instance", "kind", "approved", "invalid", "status", "approval_result",
        "approval_no", "process_code", "amount", "currency", "title", "identifiers", "related",
        "fees", "goods", "billing", "coverage",
    )
    return digest("payment-evidence-source-business-1", {field: source.get(field) for field in fields})


def _selection_fingerprint(selected_rows, logical_key):
    fields = ("source_line_id", "line_revision", "logical_fee_key", "amount",
              "currency", "amount_status", "evidence_document_id")
    rows = [{field: row.get(field) for field in fields} for row in selected_rows
            if row.get("logical_fee_key") == logical_key]
    return digest("payment-evidence-selection-2", sorted(rows, key=lambda row: str(row.get("source_line_id"))))


def _selection_summary(selected_rows, logical_key):
    rows = [row for row in selected_rows if row.get("logical_fee_key") == logical_key]
    amount = sum((Decimal(str(row.get("amount") or "0")) for row in rows), Decimal("0"))
    currencies = {row.get("currency") for row in rows if row.get("currency")}
    return ("0" if not amount else format(amount.normalize(), "f"),
            next(iter(currencies)) if len(currencies) == 1 else "")


def _evidence_role(document):
    kind = _document_type(document)
    name = norm(str(document.get("file_name") or ""))
    if kind == "Tax Certificate":
        return "tax", "TAX_CERTIFICATE", "FINAL_BILL"
    if kind == "Commercial Invoice":
        return "invoice", "FINAL_INVOICE", "FINAL_BILL"
    if any(token in name for token in ("payment", "receipt", "comprobante", "付款", "支付", "回单")):
        return "payment", "PAYMENT", "SETTLEMENT"
    return "other", "OTHER", "REFERENCE"


def _suggested_keys(document, line_document_keys, allowed_keys):
    keys = set(line_document_keys.get(str(document.get("id") or ""), ()))
    kind = _document_type(document)
    if kind == "Tax Certificate" and "import_tax" in allowed_keys:
        keys.add("import_tax")
    elif kind == "Customs Declaration" and "customs_clearance_fee" in allowed_keys:
        keys.add("customs_clearance_fee")
    if not keys and len(allowed_keys) == 1:
        keys.update(allowed_keys)
    return sorted(keys & set(allowed_keys))


def _safe_summary(document, *, suggested_keys=(), required=False, selected=False):
    manifest = document.get("manifest") or {}
    availability, blocker = _availability(document)
    role, _evidence_type, _accounting_role = _evidence_role(document)
    raw_status = str(document.get("status") or manifest.get("archive_status") or "pending").lower()
    status = raw_status if raw_status in {"parsed", "review", "pending", "failed", "archived"} else "pending"
    file_name = str(document.get("file_name") or manifest.get("file_name") or "附件").replace("\\", "/")
    return {
        "document_id": str(document.get("id") or ""),
        "file_name": PurePosixPath(file_name).name,
        "origin": _origin(document),
        "attachment_type": _document_type(document),
        "status": status,
        "fingerprint": document_fingerprint(document),
        "suggested_logical_fee_keys": list(suggested_keys),
        "suggested_role": role,
        "default_selected": availability == "available" and bool(suggested_keys),
        "selected": bool(selected),
        "availability": availability,
        "required": bool(required),
        "blocker": blocker or None,
    }


def _documents(source):
    result = {}
    represented_files = set()
    for document in source.get("documents") or []:
        document_id = str(document.get("id") or "") if isinstance(document, dict) else ""
        if not document_id:
            continue
        if document_id in result:
            raise ValueError(_("付款来源附件标识重复，请刷新来源归档"))
        result[document_id] = document
        manifest = document.get("manifest") or {}
        if document.get("file_id") or manifest.get("file_id"):
            represented_files.add(str(document.get("file_id") or manifest.get("file_id")))
    # Older/local inventory rows can have an attachment index before parsed
    # settlement_documents exists. Surface it as pending evidence, never as
    # materializable bytes until a trusted local private document is registered.
    for manifest in source.get("attachments") or []:
        if not isinstance(manifest, dict) or manifest.get("retired_at"):
            continue
        file_id = str(manifest.get("file_id") or "")
        if file_id and file_id in represented_files:
            continue
        document_id = str(manifest.get("document_id") or digest(
            "payment-attachment-index", source.get("id"), file_id,
            manifest.get("sha256") or manifest.get("content_hash"),
        ))
        if document_id in result:
            continue
        result[document_id] = {
            "id": document_id, "file_name": manifest.get("file_name") or "附件",
            "status": manifest.get("archive_status") or "pending",
            "fingerprint": manifest.get("sha256") or manifest.get("content_hash") or document_id,
            "manifest": manifest, "tables": [],
        }
    return result


def _source_evidence_fingerprint(source):
    documents = _documents(source)
    return digest("payment-evidence-source-inventory-1", sorted((
        document_id, _retry_content_fingerprint(document), _retry_document_identity(document),
        _retry_source_relation(document),
    ) for document_id, document in documents.items()))


def candidate_attachment_summaries(source, lines=(), transport_mode=""):
    """Read-only public candidate projection. It deliberately has no archive URL."""
    from .freight_lines import logical_fee_key

    line_document_keys = {}
    allowed_keys = set()
    for line in lines or []:
        key = line.get("logical_fee_key") or logical_fee_key(line.get("scope"), transport_mode)
        if key:
            allowed_keys.add(key)
        document_id = str((line.get("evidence") or {}).get("document_id") or "")
        if document_id and key:
            line_document_keys.setdefault(document_id, set()).add(key)
    summaries = []
    for document in _documents(source).values():
        keys = _suggested_keys(document, line_document_keys, allowed_keys)
        summary = _safe_summary(document, suggested_keys=keys)
        summary["selected"] = summary["default_selected"]
        summaries.append(summary)
    return summaries


def plan_attachment_selections(source, lines, selected_rows, attachment_selections):
    documents = _documents(source)
    selected_keys = {row["logical_fee_key"] for row in selected_rows}
    line_document_keys = {}
    required_ids = set()
    for row in selected_rows:
        line = lines.get(row["source_line_id"]) or {}
        evidence = line.get("evidence") or {}
        document_id = str(evidence.get("document_id") or "")
        if document_id:
            required_ids.add(document_id)
            line_document_keys.setdefault(document_id, set()).add(row["logical_fee_key"])
            document = documents.get(document_id)
            expected_hash = str(evidence.get("document_hash") or "")
            if document and expected_hash and expected_hash != content_fingerprint(document):
                raise ValueError(_("付款明细与来源附件指纹不一致，请刷新来源归档"))

    summaries = {}
    for document_id, document in documents.items():
        keys = _suggested_keys(document, line_document_keys, selected_keys)
        summaries[document_id] = _safe_summary(
            document, suggested_keys=keys, required=document_id in required_ids
        )

    if attachment_selections is None:
        choices = [{"document_id": row["document_id"], "selected": row["default_selected"],
                    "logical_fee_keys": row["suggested_logical_fee_keys"]}
                   for row in summaries.values()]
    else:
        if not isinstance(attachment_selections, list) or len(attachment_selections) > 100:
            raise ValueError(_("附件选择格式错误"))
        choices = attachment_selections

    selected = {}
    seen = set()
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) - CHOICE_FIELDS:
            raise ValueError(_("附件选择包含不允许的来源凭据字段"))
        document_id = str(choice.get("document_id") or "")
        if document_id not in summaries:
            raise ValueError(_("附件不属于当前付款来源"))
        if document_id in seen:
            raise ValueError(_("同一附件不能重复选择"))
        seen.add(document_id)
        if choice.get("selected") not in (None, True, False, 0, 1, "0", "1"):
            raise ValueError(_("附件选择状态格式错误"))
        enabled = choice.get("selected", True) in (True, 1, "1")
        keys = choice.get("logical_fee_keys")
        if keys is None:
            keys = summaries[document_id]["suggested_logical_fee_keys"] or sorted(selected_keys)
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError(_("附件费用分类格式错误"))
        keys = sorted(set(keys))
        if set(keys) - selected_keys:
            raise ValueError(_("附件费用分类不属于本次付款选择"))
        if enabled and not keys:
            raise ValueError(_("已选附件至少关联一个本次费用分类"))
        if enabled:
            selected[document_id] = keys

    blockers = []
    missing_required = required_ids - set(documents)
    if missing_required:
        blockers.append("付款金额依赖的来源附件已缺失，请刷新归档后重新预览")
    for document_id in sorted(required_ids & set(documents)):
        summary = summaries[document_id]
        if document_id not in selected:
            blockers.append("付款金额依赖的来源附件必须保留为费用凭证")
        if summary["availability"] != "available":
            blockers.append(summary["blocker"] or "付款金额依赖的来源附件不可读取")

    public = []
    private = []
    for document_id, summary in summaries.items():
        row = {**summary, "selected": document_id in selected}
        public.append(row)
        if document_id in selected:
            private.append({
                "document_id": document_id,
                "document_fingerprint": summary["fingerprint"],
                "logical_fee_keys": selected[document_id],
                "availability": summary["availability"],
                "required": summary["required"],
                "role": summary["suggested_role"],
            })
    dependency = sorted((document_id, document_fingerprint(document))
                        for document_id, document in documents.items())
    return {"public": public, "selected": private, "dependency": dependency,
            "blockers": sorted(set(blockers))}


def validate_attachment_dependencies(source, plan):
    current = sorted((document_id, document_fingerprint(document))
                     for document_id, document in _documents(source).items())
    if current != [tuple(row) for row in plan.get("dependency") or []]:
        raise ValueError(_("付款来源附件或归档状态已变化，请重新预览"))
    if plan.get("blockers"):
        raise ValueError(plan["blockers"][0])


def payment_attachment_values(source, document, batch, version, _reviews, **_flags):
    manifest = document.get("manifest") or {}
    role, evidence_type, accounting_role = _evidence_role(document)
    descriptor = {
        "document_id": document["id"],
        "fingerprint": document_fingerprint(document),
        "file_id": manifest.get("file_id"),
        "origin": _origin(document),
        "source_field_id": document.get("source_field_id") or manifest.get("source_field_id") or manifest.get("field_id"),
        "source_field_name": document.get("source_field_name") or manifest.get("source_field_name") or manifest.get("field_name"),
        "source_comment_id": document.get("source_comment_id") or manifest.get("source_comment_id") or manifest.get("comment_id"),
        "approval_instance": source.get("instance"),
        "evidence_role": role,
        "evidence_type": evidence_type,
        "accounting_role": accounting_role,
    }
    file_name = str(document.get("file_name") or manifest.get("file_name") or "附件").replace("\\", "/")
    return {
        "batch": batch["name"], "version": version, "source_type": "OA",
        "oa_attachment_origin": _origin(document), "attachment_type": _document_type(document),
        "source_doc_no": source.get("approval_no") or source.get("instance") or "",
        "file_name": PurePosixPath(file_name).name,
        "file_url": document.get("file_url") or "", "parse_status": "Parsed" if document.get("status") == "parsed" else "Draft",
        "parse_result_json": dumps({"payment_evidence": descriptor, "data_source": "local_archive"}),
        "mapped_result_json": dumps({"payment_evidence": descriptor}),
        "remark": "实际支付流程归档凭证（只读）",
    }


def _active_payment_rule(ledger, batch_name, version_name, logical_key):
    rows = [row for row in ledger.rows("rule", batch=batch_name, version=version_name)
            if row.get("logical_fee_key") == logical_key
            and str(row.get("rule_code") or "").startswith("payment_")
            and row.get("is_active") not in (0, False, "0")
            and row.get("is_enabled") not in (0, False, "0")]
    if len(rows) != 1:
        raise ValueError(_("付款费用规则与凭证关联状态异常，请重新预览"))
    return rows[0]


def _pending_id(batch_name, version_name, source_id, document_id, logical_key):
    return digest("payment-evidence-pending-1", batch_name, version_name, source_id, document_id, logical_key)


def _record_pending(store, batch, version_name, source, document, choice, selected_rows, pending_context):
    summary = _safe_summary(document, suggested_keys=choice["logical_fee_keys"], selected=True)
    for key in choice["logical_fee_keys"]:
        binding = (pending_context.get("bindings") or {}).get(key) or {}
        if not binding:
            raise ValueError(_("付款附件缺少可验证的费用规则绑定"))
        pending_id = _pending_id(batch["name"], version_name, source["id"], document["id"], key)
        selection_amount, selection_currency = _selection_summary(selected_rows, key)
        row = {
            "id": pending_id, "batch": batch["name"], "version": version_name,
            "source_id": source["id"], "document_id": document["id"], "logical_fee_key": key,
            "status": "pending", "preview_id": pending_context.get("preview_id"),
            "application_id": pending_context.get("application_id"),
            "source_snapshot": source.get("snapshot"), "source_corp": source.get("corp"),
            "source_instance": source.get("instance"),
            "source_business_fingerprint": _source_business_fingerprint(source),
            "source_evidence_fingerprint": _source_evidence_fingerprint(source),
            "document_content_fingerprint": _retry_content_fingerprint(document),
            "document_identity_fingerprint": _retry_document_identity(document),
            "source_relation_fingerprint": _retry_source_relation(document),
            "selection_fingerprint": _selection_fingerprint(selected_rows, key),
            "selection_amount": selection_amount, "selection_currency": selection_currency,
            "claim_fingerprint": binding.get("claim_fingerprint"),
            "rule_fingerprint": binding.get("rule_fingerprint"),
            "rule_name": binding.get("rule_name"), "rule_binding_id": binding.get("rule_binding_id"),
            "claim_ids": binding.get("claim_ids") or [], "required": bool(choice.get("required")),
            "role": choice.get("role") or summary.get("suggested_role") or "other",
            "revision": digest(pending_id, _retry_content_fingerprint(document), _retry_document_identity(document),
                               _retry_source_relation(document), binding, _selection_fingerprint(selected_rows, key)),
            "file_name": summary["file_name"], "origin": summary["origin"],
            "attachment_type": summary["attachment_type"], "availability": summary["availability"],
            "reason": "付款附件私有归档或费用凭证关联待重试", "updated_at": utcnow(),
        }
        store.put("payment_evidence_pending", {key: row[key] for key in (
            "id", "batch", "version", "source_id", "document_id", "logical_fee_key", "status", "revision"
        )} | {"data": dumps(row)})


def _resolve_pending(store, batch, version_name, source, document, choice):
    for key in choice["logical_fee_keys"]:
        pending_id = _pending_id(batch["name"], version_name, source["id"], document["id"], key)
        current = store.get("payment_evidence_pending", pending_id)
        if current and current.get("status") == "pending":
            current.update(status="resolved", resolved_at=utcnow(),
                           revision=digest(current["revision"], "resolved"))
            store.put("payment_evidence_pending", {key: current[key] for key in (
                "id", "batch", "version", "source_id", "document_id", "logical_fee_key", "status", "revision"
            )} | {"data": dumps(current)})


def public_pending_evidence(store, batch_name, version_name):
    rows = store.find("payment_evidence_pending", batch=batch_name, version=version_name, status="pending")
    return [{
        "id": row["id"], "document_id": row["document_id"],
        "logical_fee_key": row["logical_fee_key"], "status": "pending",
        "revision": row["revision"], "file_name": row.get("file_name") or "附件",
        "origin": row.get("origin") if row.get("origin") in {"Form", "Comment"} else "Form",
        "attachment_type": row.get("attachment_type") if row.get("attachment_type") in ATTACHMENT_TYPES else "Other",
        "availability": row.get("availability") if row.get("availability") in {"pending", "unavailable", "available"} else "pending",
        "selection_amount": row.get("selection_amount"), "currency": row.get("selection_currency") or "",
        "reason": "付款附件归档或凭证关联待重试", "retry_available": True,
    } for row in rows]


def validate_pending_evidence_retry(store, source, batch, version_name, preview, application, bindings,
                                    selected_rows):
    """Authorize a retry without trusting the stale preview archive status/URL."""
    pending = store.find("payment_evidence_pending", batch=batch["name"], version=version_name, status="pending")
    preview_selected = {row["document_id"]: row for row in (preview.get("attachment_plan") or {}).get("selected") or []}
    rows = [row for row in pending
            if row.get("preview_id") == preview.get("id") or row.get("application_id") == application.get("id")]
    if not rows:
        return {"selected": [], "blockers": []}
    if (application.get("preview_id") != preview.get("id") or application.get("batch") != batch.get("name")
            or application.get("version") != version_name or application.get("status") != "applied"):
        raise ValueError(_("付款附件待重试记录与已采用结果不一致"))
    documents = _documents(source)
    retry_choices = {}
    for row in rows:
        key = row["logical_fee_key"]
        choice = preview_selected.get(row["document_id"]) or {}
        document = documents.get(row["document_id"])
        binding = bindings.get(key) or {}
        if (row.get("preview_id") != preview.get("id") or row.get("application_id") != application.get("id")
                or not choice):
            raise ValueError(_("付款附件待重试选择已变化"))
        if (not document or row.get("source_id") != source.get("id")
                or row.get("source_corp") != source.get("corp")
                or row.get("source_instance") != source.get("instance")
                or row.get("source_business_fingerprint") != _source_business_fingerprint(source)):
            raise ValueError(_("付款附件待重试来源已变化"))
        expected_content = row.get("document_content_fingerprint") or ""
        if not expected_content or _retry_content_fingerprint(document) != expected_content:
            raise ValueError(_("付款附件内容指纹已变化，不能重试归档"))
        if (_retry_document_identity(document) != row.get("document_identity_fingerprint")
                or _retry_source_relation(document) != row.get("source_relation_fingerprint")):
            raise ValueError(_("付款附件身份或来源关系已变化，不能重试归档"))
        if row.get("source_evidence_fingerprint") != _source_evidence_fingerprint(source):
            raise ValueError(_("付款附件待重试来源资料已变化"))
        if _selection_fingerprint(selected_rows, key) != row.get("selection_fingerprint"):
            raise ValueError(_("付款金额或费用选择已变化，不能重试附件"))
        if (binding.get("claim_fingerprint") != row.get("claim_fingerprint")
                or binding.get("rule_fingerprint") != row.get("rule_fingerprint")
                or binding.get("rule_name") != row.get("rule_name")
                or binding.get("rule_binding_id") != row.get("rule_binding_id")):
            raise ValueError(_("付款认领或费用规则已变化，不能重试附件"))
        retry = retry_choices.setdefault(row["document_id"], {
            "document_id": row["document_id"], "logical_fee_keys": [],
            "required": False, "role": row.get("role") or "other",
        })
        retry["logical_fee_keys"].append(key)
        retry["required"] = retry["required"] or bool(row.get("required"))
    for retry in retry_choices.values():
        retry["logical_fee_keys"] = sorted(set(retry["logical_fee_keys"]))
    return {"selected": [retry_choices[key] for key in sorted(retry_choices)], "blockers": []}


def _put_pending(store, row):
    store.put("payment_evidence_pending", {key: row[key] for key in (
        "id", "batch", "version", "source_id", "document_id", "logical_fee_key", "status", "revision"
    )} | {"data": dumps(row)})


def _refresh_pending_binding(row, key, binding, selected_rows, actor, reason):
    selection_amount, selection_currency = _selection_summary(selected_rows, key)
    row.update(
        logical_fee_key=key, claim_ids=binding.get("claim_ids") or [],
        claim_fingerprint=binding.get("claim_fingerprint"), rule_fingerprint=binding.get("rule_fingerprint"),
        rule_name=binding.get("rule_name"), rule_binding_id=binding.get("rule_binding_id"),
        selection_fingerprint=_selection_fingerprint(selected_rows, key),
        selection_amount=selection_amount, selection_currency=selection_currency,
        amended_by=actor, amended_at=utcnow(), amendment_reason=str(reason or ""),
    )
    row["revision"] = digest(row.get("revision"), "binding-refreshed", key, binding,
                             row["selection_fingerprint"], reason)
    return row


def refresh_pending_evidence_groups(store, batch_name, version_name, bindings, selected_rows, actor,
                                    reason, *, source=None):
    """Refresh all pending rows that depend on current aggregate claim/rule groups."""
    for row in store.find("payment_evidence_pending", batch=batch_name, version=version_name, status="pending"):
        key = row.get("logical_fee_key")
        binding = bindings.get(key)
        if not binding:
            continue
        _refresh_pending_binding(row, key, binding, selected_rows, actor, reason)
        if source and row.get("source_id") == source.get("id"):
            row.update(source_snapshot=source.get("snapshot"), source_corp=source.get("corp"),
                       source_instance=source.get("instance"),
                       source_business_fingerprint=_source_business_fingerprint(source),
                       source_evidence_fingerprint=_source_evidence_fingerprint(source))
        _put_pending(store, row)


def update_pending_evidence_for_claim(store, batch_name, version_name, before, after, action,
                                      bindings, selected_rows, actor, reason):
    """Recalculate every affected deferred-evidence aggregate after a claim amendment."""
    rows = store.find("payment_evidence_pending", batch=batch_name, version=version_name, status="pending")
    originals = [row for row in rows if before["id"] in (row.get("claim_ids") or [])]
    if not originals:
        return
    affected_keys = {before["logical_fee_key"]}
    if action != "revoke":
        affected_keys.add(after["logical_fee_key"])
    for base in originals:
        old_key = base["logical_fee_key"]
        for key in sorted(affected_keys):
            target_id = _pending_id(batch_name, version_name, base["source_id"], base["document_id"], key)
            binding = bindings.get(key)
            existing = store.get("payment_evidence_pending", target_id)
            if not binding:
                target = existing or (base if base["id"] == target_id else None)
                if target and target.get("status") == "pending":
                    target.update(status="cancelled", cancelled_at=utcnow(), cancelled_by=actor,
                                  reason="当前费用组已无有效付款认领，附件待归档任务已取消",
                                  revision=digest(target.get("revision"), "cancelled-empty-group", reason))
                    _put_pending(store, target)
                continue
            target = dict(existing if existing and existing.get("status") == "pending" else base)
            target.update(id=target_id, status="pending", logical_fee_key=key,
                          amended_from_logical_fee_key=old_key)
            _refresh_pending_binding(target, key, binding, selected_rows, actor, reason)
            _put_pending(store, target)


def capture_current_rule_evidence(ledger, batch_name, version_name, logical_keys):
    captured = {}
    for rule in ledger.rows("rule", batch=batch_name, version=version_name):
        key = rule.get("logical_fee_key")
        if (key not in logical_keys or not str(rule.get("rule_code") or "").startswith("payment_")
                or rule.get("is_active") in (0, False, "0") or rule.get("is_enabled") in (0, False, "0")):
            continue
        captured.setdefault(key, []).extend(
            ledger.rows("evidence", batch=batch_name, version=version_name, fee_rule=rule["name"])
        )
    return captured


def migrate_current_rule_evidence(ledger, batch_name, version_name, captured, actor, reason, *, aliases=None):
    """Copy immutable old-rule evidence onto regenerated current payment rules."""
    aliases = aliases or {}
    for target_key in sorted(set(captured) | set(aliases)):
        source_key = aliases.get(target_key, target_key)
        rows = captured.get(source_key) or []
        if not rows:
            continue
        try:
            rule = _active_payment_rule(ledger, batch_name, version_name, target_key)
        except ValueError:
            # Revocation or reclassification may intentionally leave no current
            # rule for the old key; historical evidence remains on old rules.
            continue
        for old in rows:
            duplicates = [row for row in ledger.rows("evidence", batch=batch_name, version=version_name)
                          if row.get("fee_rule") == rule["name"]
                          and row.get("attachment") == old.get("attachment")
                          and row.get("evidence_role") == old.get("evidence_role")]
            if duplicates:
                continue
            try:
                snapshot = json.loads(old.get("parse_snapshot_json") or "{}")
            except (TypeError, ValueError):
                snapshot = {}
            snapshot.update(migrated_from_evidence=old.get("name"), amendment_reason=str(reason or ""))
            values = {key: value for key, value in old.items() if key not in {"name", "fee_rule"}}
            values.update(
                batch=batch_name, version=version_name, fee_rule=rule["name"],
                validation_status="PENDING", parse_snapshot_json=dumps(snapshot),
                is_final=1 if rule.get("amount_status") == "ACTUAL" else 0,
                confirmed_by=actor, confirmed_at=utcnow(),
            )
            ledger.create("evidence", values)


def materialize_payment_evidence(store, ledger, source, batch, version_name, attachment_plan,
                                 selected_rows, actor, register_file, *, pending_context=None):
    """Called inside payment confirmation transaction after allocation rules exist."""
    from .document_writer import save_attachment

    documents = _documents(source)
    pending_context = pending_context or {}
    materialized = []
    for choice in attachment_plan.get("selected") or []:
        document = documents.get(choice["document_id"])
        if not document:
            raise ValueError(_("付款来源附件已缺失，请重新预览"))
        availability, _blocker = _availability(document)
        if availability != "available":
            if choice.get("required"):
                raise ValueError(_("付款金额依赖的来源附件尚不可归档"))
            _record_pending(store, batch, version_name, source, document, choice, selected_rows, pending_context)
            materialized.append({"document_id": choice["document_id"], "status": availability,
                                 "logical_fee_keys": choice["logical_fee_keys"]})
            continue
        try:
            with store.atomic():
                mapping = store.get("attachment_map", digest(document["id"], version_name))
                attachment = ledger.get("attachment", mapping["attachment"]) if mapping else None
                if attachment:
                    if (mapping.get("document_id") != document["id"] or mapping.get("version") != version_name
                            or attachment.get("batch") != batch["name"] or attachment.get("version") != version_name):
                        raise ValueError(_("付款附件已被其他批次或版本占用"))
                else:
                    save_attachment(store, ledger, source, document, batch, version_name, [], register_file,
                                    values_factory=payment_attachment_values)
                    mapping = store.get("attachment_map", digest(document["id"], version_name))
                    attachment = ledger.get("attachment", mapping["attachment"]) if mapping else None
                if not attachment:
                    raise ValueError(_("付款附件未能归档到当前批次版本"))
                role, evidence_type, accounting_role = _evidence_role(document)
                evidence_names = []
                for key in choice["logical_fee_keys"]:
                    rule = _active_payment_rule(ledger, batch["name"], version_name, key)
                    existing = [row for row in ledger.rows("evidence", batch=batch["name"], version=version_name)
                                if row.get("fee_rule") == rule["name"] and row.get("attachment") == attachment["name"]
                                and row.get("evidence_role") == role]
                    if existing:
                        evidence_names.append(existing[0]["name"])
                        continue
                    category_rows = [row for row in selected_rows if row["logical_fee_key"] == key]
                    document_rows = [row for row in category_rows
                                     if row.get("evidence_document_id") == document["id"]]
                    related = document_rows or category_rows
                    amount = sum((Decimal(str(row["amount"])) for row in related), Decimal("0"))
                    currencies = {row["currency"] for row in related}
                    parse_snapshot = {
                        "document_id": document["id"], "document_fingerprint": document_fingerprint(document),
                        "source_id": source["id"], "source_snapshot": source["snapshot"],
                        "logical_fee_key": key, "selection_indexes": [row["selection_index"] for row in related],
                        "source_line_ids": [row["source_line_id"] for row in related],
                    }
                    evidence = ledger.create("evidence", {
                        "batch": batch["name"], "version": version_name, "fee_rule": rule["name"],
                        "attachment": attachment["name"], "evidence_role": role, "validation_status": "PENDING",
                        "source_revision": source["snapshot"], "evidence_type": evidence_type,
                        "accounting_role": accounting_role, "currency": next(iter(currencies)) if len(currencies) == 1 else "",
                        "original_amount": str(amount), "direction": "CREDIT" if amount < 0 else "DEBIT",
                        "is_final": 1 if all(row.get("amount_status") == "ACTUAL" for row in related) else 0,
                        "attachment_fingerprint": content_fingerprint(document),
                        "parse_snapshot_json": dumps(parse_snapshot), "confirmed_by": actor,
                        "confirmed_at": utcnow(),
                    })
                    evidence_names.append(evidence["name"])
        except Exception as exc:
            if type(exc).__name__ == "QueryDeadlockError" or (exc.args and exc.args[0] == 1213):
                raise
            if choice.get("required"):
                raise
            _record_pending(store, batch, version_name, source, document, choice, selected_rows, pending_context)
            materialized.append({"document_id": choice["document_id"], "status": "pending",
                                 "logical_fee_keys": choice["logical_fee_keys"],
                                 "blocker": "付款附件私有归档待重试"})
            continue
        _resolve_pending(store, batch, version_name, source, document, choice)
        materialized.append({"document_id": document["id"], "status": "archived",
                             "attachment_name": attachment["name"], "evidence_names": evidence_names,
                             "logical_fee_keys": choice["logical_fee_keys"]})
    return materialized
