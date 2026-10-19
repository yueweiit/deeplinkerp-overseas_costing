"""Server-owned payment-match selection for material AI previews.

The production freight-line matcher remains the only authority that decides
whether a payment process belongs to a shipment.  This module merely admits a
single strong, current candidate into the AI preview and confirms that exact
candidate inside the caller's transaction.
"""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

from .logistics_settlement.model import digest, dumps, norm


POLICY = "material-ai-payment-match-2"
STRONG_METHODS = frozenset({"manual", "explicit", "identifier"})
DISPLAY_METHODS = frozenset({"manual", "explicit", "identifier", "reopened"})
AI_CONFIDENCE_MINIMUM = Decimal("0.90")
REFERENCE_KEYS = frozenset({"candidate_id", "revision", "version"})
RELATION_KEYS = frozenset({
    "policy", "candidate_id", "candidate_revision", "version",
    "logistics_id", "expense_id", "logistics_snapshot", "expense_snapshot",
    "confirmed_by", "confirmed_at",
})


def _evidence(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    value = row.get("evidence")
    return value if isinstance(value, dict) else {}


def _has_malformed_evidence(row: dict) -> bool:
    return bool(
        isinstance(row, dict)
        and "evidence" in row
        and row.get("evidence") is not None
        and not isinstance(row.get("evidence"), dict)
    )


def _line_scoped_goods(line: dict, evidence: dict) -> list[dict]:
    """Project row-scoped packing facts only when one material code is explicit."""

    # Older archived monthly-statement rows predate the persisted ``packing``
    # projection.  Use the same normalizer as the payment-source summary so a
    # successfully matched legacy row cannot expose its freight amount while
    # silently losing its weight, carton count and dimensions in AI fill.
    from .logistics_settlement.freight_lines import packing_for_line

    packing = packing_for_line(line)
    material_codes = list(dict.fromkeys(
        str(value or "").strip().upper()
        for value in packing.get("material_code_hints") or []
        if re.fullmatch(r"[A-Z]{2,8}\d{3,}", str(value or "").strip(), re.I)
    ))
    if len(material_codes) != 1:
        return []
    physical = {
        field: str(packing[field])
        for field in ("gross_weight_kg", "chargeable_weight_kg", "package_count", "volume_m3")
        if packing.get(field) not in (None, "")
    }
    if not physical:
        return []
    return [{
        "material_code": material_codes[0],
        "product_name": "",
        "spec_model": "",
        "quantity": None,
        "unit": "",
        **physical,
        "dimensions_cm": deepcopy(packing.get("dimensions_cm") or []),
        "physical": deepcopy(physical),
        "evidence": deepcopy(evidence),
    }]


def _confidence(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _strong(candidate: dict) -> bool:
    if str(candidate.get("status") or "").lower() not in {"pending", "confirmed"}:
        return False
    if candidate.get("issues"):
        return False
    method = str(candidate.get("method") or "").lower()
    if method in STRONG_METHODS:
        return True
    return method == "deepseek" and (_confidence(candidate.get("confidence")) or Decimal("-1")) >= AI_CONFIDENCE_MINIMUM


def _current_logistics(store, ledger, batch_name: str, version_name: str, *, lock=False):
    batch = ledger.get("batch", batch_name, lock=lock) or {}
    version = ledger.get("version", version_name, lock=lock) or {}
    if batch.get("current_version") != version_name or version.get("batch") != batch_name:
        raise ValueError(_("当前成本版本已变化，请刷新"))
    mappings = store.find("batch_map", batch=batch_name)
    if len(mappings) != 1:
        return None
    logistics_id = str(mappings[0].get("source_id") or "")
    # A logistics source is allowed to own one and only one batch.  This also
    # prevents a browser-provided candidate from being replayed across tickets.
    owned = store.find("batch_map", source_id=logistics_id)
    if len(owned) != 1 or owned[0].get("batch") != batch_name:
        return None
    logistics = store.get("source", logistics_id, lock=lock) or {}
    if logistics.get("kind") != "logistics" or logistics.get("invalid"):
        return None
    return logistics


def _current_candidates(store, ledger, batch_name: str, version_name: str, *, lock=False):
    logistics = _current_logistics(store, ledger, batch_name, version_name, lock=lock)
    if not logistics:
        return []
    from .logistics_settlement.freight_matching import CANDIDATE_SCOPE_POLICY, candidates

    rows = []
    for raw in candidates(store, logistics["id"]):
        candidate = store.get("freight_candidate", raw["id"], lock=lock) if lock else raw
        if (candidate
                and candidate.get("line_scope_policy") == CANDIDATE_SCOPE_POLICY
                and _strong(candidate)):
            rows.append(candidate)
    return rows


def select_preview_candidate(store, ledger, batch_name: str, version_name: str, *, freight_mode: bool):
    """Return the only browser-safe match reference, never source values."""

    if not freight_mode:
        # The legacy whole-source matcher has its own binding confirmation
        # workflow.  Pending freight candidate IDs must never cross into it.
        return None
    rows = _current_candidates(store, ledger, batch_name, version_name)
    if len(rows) != 1:
        return None
    candidate = rows[0]
    return {
        "candidate_id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
        "version": str(version_name),
    }


def _validate_reference(reference: dict, version_name: str) -> dict:
    if not isinstance(reference, dict) or set(reference) != REFERENCE_KEYS:
        raise ValueError(_("付款匹配凭证格式不正确"))
    clean = {key: str(reference.get(key) or "") for key in REFERENCE_KEYS}
    if not all(clean.values()) or clean["version"] != str(version_name):
        raise ValueError(_("付款匹配凭证已变化，请刷新"))
    return clean


def payment_workflow_template(source: dict) -> str:
    """Classify only registered payment workflows, not ordinary product purchase."""

    if source.get("kind") != "expense":
        return ""
    identities = [norm(source.get(key)) for key in ("title", "process_code", "template_name")
                  if norm(source.get(key))]
    title = " ".join(identities)
    fields = source.get("fields") if isinstance(source.get("fields"), dict) else {}
    typed = [title]
    for key, value in fields.items():
        label = norm(key)
        if any(marker in label for marker in ("类型", "类别", "科目", "支出分类")):
            typed.append(norm(value))
    business_type = " ".join(typed)
    transport_markers = (
        "运输", "物流", "运费", "海运", "空运", "快递", "dhl", "fedex", "ups", "清关", "关税", "完税",
    )
    product_markers = ("采购商品", "商品采购", "物料采购", "product purchase", "product-purchase")
    # An explicit product-purchase identity is authoritative.  Words such as
    # DHL or 物流 may occur in a free-form title and must not promote an
    # ordinary merchandise purchase into a payment workflow.
    if any(marker in business_type for marker in product_markers):
        return ""
    if "月结付款" in title:
        return "月结付款"
    if "运营支出" in title:
        return "运营支出"
    if "费用支出" in title:
        return "费用支出"
    if "采购支出" not in title:
        return ""
    if any(marker in business_type for marker in transport_markers):
        return "运输类采购支出"
    return ""


def _payment_source_allowed(source: dict) -> bool:
    return bool(payment_workflow_template(source) and source.get("approved") and not source.get("invalid"))


def validate_preview_references(store, ledger, batch_name: str, version_name: str,
                                references: list[dict], *, lock: bool = False) -> list[dict]:
    """Validate ID-only user choices against the current shipment matcher."""

    if not isinstance(references, list):
        raise ValueError(_("支付来源选择格式不正确"))
    logistics = _current_logistics(store, ledger, batch_name, version_name, lock=lock)
    if not logistics:
        raise ValueError(_("本票国际物流来源已变化，请刷新"))
    from .logistics_settlement.freight_matching import CANDIDATE_SCOPE_POLICY, candidates
    available = {}
    for row in candidates(store, logistics["id"]):
        candidate = store.get("freight_candidate", row.get("id"), lock=lock) if lock else row
        source = store.get("source", (candidate or {}).get("expense_id"), lock=lock) if candidate else None
        if (not candidate
                or candidate.get("line_scope_policy") != CANDIDATE_SCOPE_POLICY
                or str(candidate.get("status") or "").lower() not in {"pending", "confirmed"}
                or candidate.get("issues")
                or not source or not _payment_source_allowed(source)):
            continue
        available[str(candidate.get("id") or "")] = candidate
    clean = []
    seen = set()
    for reference in references:
        current = _validate_reference(reference, version_name)
        candidate = available.get(current["candidate_id"])
        if not candidate or str(candidate.get("revision") or "") != current["revision"]:
            raise ValueError(_("支付来源候选已变化，请刷新"))
        key = tuple(current[name] for name in ("candidate_id", "revision", "version"))
        if key in seen:
            raise ValueError(_("支付来源选择重复"))
        seen.add(key)
        clean.append(current)
    return clean


def _metadata_only_process_source(source: dict, *, reason: str, reference: dict | None = None) -> dict:
    """Expose payment-process provenance without exposing unscoped business values."""

    identity = {
        "source_id": str(source.get("id") or ""),
        "source_snapshot": str(source.get("snapshot") or ""),
        "process_instance_id": str(source.get("instance") or ""),
        "approval_no": str(source.get("approval_no") or ""),
        "title": str(source.get("title") or ""),
    }
    evidence_id = digest(POLICY, identity, reference or {}, "matched_process_without_safe_line")
    result = {
        "source_id": evidence_id,
        "logical_source_id": evidence_id,
        "source_kind": "approval_form",
        "source_label": str(source.get("title") or "实际付款流程"),
        "file_name": "",
        "sheet_name": "",
        "approval_no": str(source.get("approval_no") or ""),
        "process_instance_id": str(source.get("instance") or ""),
        "approval_role": "payment",
        "approval_title": str(source.get("title") or "实际付款流程"),
        "source_updated_at": str(source.get("source_updated_at") or ""),
        "available": True,
        "excluded": False,
        "selected": True,
        "workflow_stage": "payment",
        "workflow_rank": 0,
        "selected_source": {
            "id": evidence_id,
            "source_id": str(source.get("id") or ""),
            "source_kind": "approval_form",
            "source_label": str(source.get("title") or "实际付款流程"),
            "approval_no": str(source.get("approval_no") or ""),
            "source_snapshot": str(source.get("snapshot") or ""),
            "process_instance_id": str(source.get("instance") or ""),
            "occurred_at": str(source.get("source_updated_at") or ""),
            "revision": evidence_id,
        },
        "scoped_packing": True,
        "scoped_goods": [],
        "scoped_text": "",
        "form_fields": {},
        "approval_decisions": [],
        "can_download": False,
        "read_status": "PARTIAL",
        "analysis_reason": reason,
        "error": reason,
        "ai_eligible": False,
        "metadata_only_process": True,
        "source_hash": evidence_id,
        "content_hash": evidence_id,
    }
    if reference:
        result.update(
            payment_match_candidate=True,
            payment_match_candidate_id=reference["candidate_id"],
            payment_match_candidate_revision=reference["revision"],
            payment_match_version=reference["version"],
        )
    return result


def preview_process_sources(store, ledger, batch_name: str, version_name: str, *, freight_mode: bool):
    """Keep locally matched payment processes visible when none is safe to adopt.

    Conflict and multi-match candidates are provenance only.  They intentionally
    carry neither a confirmation reference nor any amount/document contents.
    """

    if not freight_mode:
        return []
    logistics = _current_logistics(store, ledger, batch_name, version_name)
    if not logistics:
        return []
    from .logistics_settlement.freight_matching import CANDIDATE_SCOPE_POLICY, candidates

    rows = []
    for candidate in candidates(store, logistics["id"]):
        if candidate.get("line_scope_policy") != CANDIDATE_SCOPE_POLICY:
            continue
        status = str(candidate.get("status") or "").lower()
        method = str(candidate.get("method") or "").lower()
        confidence = _confidence(candidate.get("confidence"))
        displayable = method in DISPLAY_METHODS or (
            method == "deepseek" and (confidence or Decimal("-1")) >= AI_CONFIDENCE_MINIMUM
        )
        if status not in {"pending", "confirmed", "conflict"} or not displayable:
            continue
        source = store.get("source", candidate.get("expense_id")) or {}
        if not source:
            continue
        issues = [str(value) for value in candidate.get("issues") or [] if str(value).strip()]
        reason = (
            "已匹配支付流程，但候选存在冲突，未自动采用；已继续使用下一优先级阶段。"
            if status == "conflict" or issues
            else "已匹配支付流程，但存在多个同级候选，未自动采用；已继续使用下一优先级阶段。"
        )
        rows.append(_metadata_only_process_source(source, reason=reason))
    return rows


def coalesce_fact_sources(sources: list[dict]) -> list[dict]:
    """Prefer scoped facts over the same process/document/Sheet raw path."""

    fact_locations = []
    for source in sources or []:
        process_id = str(source.get("process_instance_id") or "")
        for fact in source.get("semantic_facts") or []:
            provenance = fact.get("provenance") if isinstance(fact, dict) else {}
            document_id = str((provenance or {}).get("document_id") or "")
            file_name = str((provenance or {}).get("file_name") or "").casefold()
            sheet = str((provenance or {}).get("sheet") or source.get("sheet_name") or "")
            if process_id and (document_id or file_name):
                fact_locations.append(
                    {
                        "process_id": process_id,
                        "document_id": document_id,
                        "file_name": file_name,
                        "sheet": sheet.casefold(),
                    }
                )

    result = []
    for source in sources or []:
        if source.get("semantic_facts"):
            result.append(source)
            continue
        process_id = str(source.get("process_instance_id") or "")
        selected = source.get("selected_source")
        selected = selected if isinstance(selected, dict) else {}
        evidence = _evidence(selected)
        stable_document_id = str(
            source.get("document_id")
            or selected.get("document_id")
            or evidence.get("document_id")
            or ""
        )
        sheet = str(source.get("sheet_name") or selected.get("sheet") or evidence.get("sheet") or "")
        file_name = str(source.get("file_name") or source.get("source_label") or "").casefold()

        def same_location(location):
            if (
                location["process_id"] != process_id
                or location["sheet"] != sheet.casefold()
            ):
                return False
            if stable_document_id and location["document_id"]:
                return stable_document_id == location["document_id"]
            return bool(file_name and location["file_name"] == file_name)

        if process_id and any(same_location(location) for location in fact_locations):
            continue
        result.append(source)
    return result


def confirm_preview_candidate(
    store,
    ledger,
    batch_name: str,
    reference: dict,
    actor: str,
    *,
    freight_mode: bool,
    user_selected: bool = False,
):
    """Revalidate an exact candidate and return a server-owned relation."""

    if not freight_mode:
        raise ValueError(_("当前模式不支持付款明细匹配"))
    version_name = str((reference or {}).get("version") or "")
    clean = _validate_reference(reference, version_name)
    # Serialize candidate arbitration with freight_matching.save_candidate.
    # The caller owns the surrounding transaction, so this row lock remains
    # held through the unique-candidate check and the downstream AI writes.
    store.get("state", "match_lock", lock=True)
    from .logistics_settlement.payment_adoption import _resolve_context

    _batch, _version, candidate, _source, _logistics, _lines = _resolve_context(
        store,
        ledger,
        batch_name,
        version_name,
        clean["candidate_id"],
        clean["revision"],
        lock=True,
    )
    # Re-run the unique/strong arbitration under locks.  The user cannot turn
    # a candidate ID into authority, and a newly appeared peer blocks adoption.
    if user_selected:
        validate_preview_references(
            store, ledger, batch_name, version_name, [clean], lock=True
        )
    else:
        current = _current_candidates(
            store, ledger, batch_name, version_name, lock=True
        )
        if len(current) != 1 or current[0].get("id") != clean["candidate_id"]:
            raise ValueError(_("付款匹配候选已变化或存在冲突，请刷新"))
    from .logistics_settlement.jobs import utcnow
    return {
        "policy": POLICY,
        "candidate_id": str(candidate["id"]),
        "candidate_revision": str(candidate["revision"]),
        "version": version_name,
        "logistics_id": str(candidate["logistics_id"]),
        "expense_id": str(candidate["expense_id"]),
        "logistics_snapshot": str(candidate["logistics_snapshot"]),
        "expense_snapshot": str(candidate["expense_snapshot"]),
        "confirmed_by": str(actor or ""),
        "confirmed_at": utcnow(),
    }


def _validate_relation(relation: dict, version_name: str, actor: str) -> dict:
    if not isinstance(relation, dict) or set(relation) != RELATION_KEYS:
        raise ValueError(_("付款匹配关系格式不正确"))
    clean = {key: str(relation.get(key) or "") for key in RELATION_KEYS}
    if (not all(clean.values()) or clean["policy"] != POLICY
            or clean["version"] != str(version_name)
            or clean["confirmed_by"] != str(actor or "")):
        raise ValueError(_("付款匹配关系已变化，请刷新"))
    return clean


def persist_relation(store, ledger, batch_name: str, version_name: str,
                     relation: dict, actor: str, *, user_selected: bool = False) -> dict:
    """Persist a version-scoped relation without freezing matcher state."""

    clean = _validate_relation(relation, version_name, actor)
    store.get("state", "match_lock", lock=True)
    from .logistics_settlement.payment_adoption import _resolve_context
    _batch, version, candidate, _source, _logistics, _lines = _resolve_context(
        store, ledger, batch_name, version_name,
        clean["candidate_id"], clean["candidate_revision"], lock=True,
    )
    reference = {
        "candidate_id": clean["candidate_id"],
        "revision": clean["candidate_revision"],
        "version": clean["version"],
    }
    if user_selected:
        validate_preview_references(
            store, ledger, batch_name, version_name, [reference], lock=True
        )
    else:
        current = _current_candidates(store, ledger, batch_name, version_name, lock=True)
        if len(current) != 1 or current[0].get("id") != clean["candidate_id"]:
            raise ValueError(_("付款匹配候选已变化或存在冲突，请刷新"))
    expected = {
        "logistics_id": candidate.get("logistics_id"),
        "expense_id": candidate.get("expense_id"),
        "logistics_snapshot": candidate.get("logistics_snapshot"),
        "expense_snapshot": candidate.get("expense_snapshot"),
    }
    if any(clean[key] != str(value or "") for key, value in expected.items()):
        raise ValueError(_("付款匹配来源快照已变化，请刷新"))

    from .logistics_settlement.application import row_meta
    metadata = row_meta(version)
    prior = metadata.get("material_ai_payment_match") or {}
    prior_many = metadata.get("material_ai_payment_matches") or []
    identity_keys = RELATION_KEYS - {"confirmed_by", "confirmed_at"}
    existing = next((row for row in prior_many if isinstance(row, dict)
        and set(row) == RELATION_KEYS
        and all(str(row.get(key) or "") == clean[key] for key in identity_keys)), None)
    if existing:
        return {key: str(existing.get(key) or "") for key in RELATION_KEYS}
    if (isinstance(prior, dict) and set(prior) == RELATION_KEYS
            and all(str(prior.get(key) or "") == clean[key] for key in identity_keys)):
        existing = {key: str(prior.get(key) or "") for key in RELATION_KEYS}
        if not prior_many:
            metadata["material_ai_payment_matches"] = [existing]
            ledger.put("version", version_name, {"extra_json": dumps(metadata)})
        return existing
    metadata.setdefault("material_ai_payment_matches", []).append(clean)
    metadata.setdefault("material_ai_payment_match", clean)
    ledger.put("version", version_name, {"extra_json": dumps(metadata)})
    store.audit(
        batch_name,
        "material_ai_payment_match_confirmed",
        actor,
        candidate_id=clean["candidate_id"],
        candidate_revision=clean["candidate_revision"],
        version=version_name,
        logistics_id=clean["logistics_id"],
        expense_id=clean["expense_id"],
        user_selected=bool(user_selected),
    )
    return deepcopy(clean)


def preview_sources(
    store,
    ledger,
    batch_name: str,
    version_name: str,
    reference: dict | None,
    *,
    freight_mode: bool,
    user_selected: bool = False,
):
    """Build shipment-scoped payment evidence without copying the matcher."""

    if not reference or not freight_mode:
        return []
    clean = _validate_reference(reference, version_name)
    if user_selected:
        validate_preview_references(store, ledger, batch_name, version_name, [clean])
    else:
        selected = select_preview_candidate(
            store, ledger, batch_name, version_name, freight_mode=freight_mode
        )
        if selected != clean:
            return []
    candidate = store.get("freight_candidate", clean["candidate_id"]) or {}
    source = store.get("source", candidate.get("expense_id")) or {}
    if not source:
        return []
    from .material_ai_semantic_facts import build_payment_facts, eligible_evidence_facts

    baseline_items = ledger.rows("item", batch=batch_name, version=version_name)
    authorized_line_ids = {
        str(line_id) for line_id in candidate.get("line_ids") or [] if str(line_id)
    }
    payment_lines = [
        line
        for line in store.find("freight_line", source_id=source.get("id"))
        if line.get("snapshot") == source.get("snapshot")
        and str(line.get("id") or "") in authorized_line_ids
    ]
    from .logistics_settlement.packing_selection import _catalog
    from .logistics_settlement.freight_packing import text_goods

    _logistics, catalog = _catalog(store, ledger, batch_name, version_name)
    from .shipment_material_scope import shipment_identifiers
    semantic_facts = build_payment_facts(
        baseline_items,
        source,
        payment_lines,
        shipment_identifiers=shipment_identifiers(_logistics or {}),
    )
    line_evidence = []
    for line_id in candidate.get("line_ids") or []:
        line = store.get("freight_line", line_id) or {}
        if line.get("source_id") == source.get("id") and line.get("snapshot") == source.get("snapshot"):
            line_evidence.append(_evidence(line))

    def row_matches_line(row):
        if _has_malformed_evidence(row):
            return False
        evidence = _evidence(row)
        document_id = str(row.get("document_id") or evidence.get("document_id") or "")
        sheet = str(row.get("sheet") or evidence.get("sheet") or "")
        row_number = row.get("row") if row.get("row") is not None else evidence.get("row")
        for allowed in line_evidence:
            if (document_id and document_id == str(allowed.get("document_id") or "")
                    and sheet == str(allowed.get("sheet") or "")
                    and allowed.get("row") not in (None, "")
                    and row_number == allowed.get("row")):
                return True
        return False

    rows = [row for row in catalog if row.get("source_id") == source.get("id") and row_matches_line(row)]
    result = []
    covered_documents = {}
    for row in rows:
        if not (row.get("goods") or str(row.get("text") or "").strip()):
            continue
        row_evidence = _evidence(row)
        document_id = str(row.get("document_id") or row_evidence.get("document_id") or "")
        document_key = (document_id, str(row.get("sheet") or row_evidence.get("sheet") or ""))
        evidence_id = digest(POLICY, clean, row.get("id"), row.get("revision"))
        selected_source = {
            key: deepcopy(row.get(key))
            for key in (
                "id",
                "source_id",
                "source_kind",
                "source_label",
                "approval_no",
                "source_snapshot",
                "process_instance_id",
                "occurred_at",
                "actor_name",
                "evidence",
                "document_id",
                "sheet",
                "revision",
            )
            if row.get(key) not in (None, "")
        }
        if "evidence" in selected_source:
            selected_source["evidence"] = deepcopy(row_evidence)
        result.append(
            {
                "source_id": evidence_id,
                "logical_source_id": evidence_id,
                "source_kind": str(row.get("source_kind") or "approval_form"),
                "source_label": str(row.get("source_label") or source.get("title") or "实际付款流程"),
                "file_name": str(row_evidence.get("file_name") or ""),
                "sheet_name": str(row.get("sheet") or ""),
                "approval_no": str(source.get("approval_no") or ""),
                "process_instance_id": str(source.get("instance") or ""),
                "approval_role": "payment",
                "approval_title": str(source.get("title") or "实际付款流程"),
                "source_updated_at": str(row.get("occurred_at") or source.get("source_updated_at") or ""),
                "actor_name": str(row.get("actor_name") or ""),
                "available": True,
                "excluded": False,
                "selected": True,
                "workflow_stage": "payment",
                "workflow_rank": 0,
                "payment_match_candidate": True,
                "payment_match_candidate_id": clean["candidate_id"],
                "payment_match_candidate_revision": clean["revision"],
                "payment_match_version": clean["version"],
                "payment_match_user_selected": bool(user_selected),
                "selected_source": selected_source,
                "scoped_packing": True,
                "scoped_goods": deepcopy(row.get("goods") or []),
                "scoped_text": str(row.get("text") or ""),
                "form_fields": {},
                "approval_decisions": [],
                "can_download": False,
                "source_hash": evidence_id,
                "content_hash": evidence_id,
            }
        )
        if document_id:
            covered_documents[document_key] = len(result) - 1
    # A monthly payment attachment may have a valid, shipment-scoped freight
    # line even when its packing catalog row is not readable (for example an
    # older archive marked ``review`` instead of ``original``).  Reuse only the
    # exact line IDs authenticated by the matcher.  Never expose the payment
    # approval total or the other shipments in the monthly source.
    for line_id in candidate.get("line_ids") or []:
        line = store.get("freight_line", line_id) or {}
        if (line.get("source_id") != source.get("id")
                or line.get("snapshot") != source.get("snapshot")):
            continue
        if _has_malformed_evidence(line):
            continue
        evidence = deepcopy(_evidence(line))
        document_id = str(evidence.get("document_id") or "")
        sheet = str(evidence.get("sheet") or "")
        cargo_text = str(line.get("cargo_text") or "").strip()
        amount = line.get("amount")
        billing_weight = line.get("billing_weight")
        line_goods = _line_scoped_goods(line, evidence)
        text_lines = []
        if line.get("label"):
            text_lines.append(f"费用项目: {line['label']}")
        if amount is not None and str(amount).strip():
            amount_label = "运费金额" if str(line.get("scope") or "") == "freight" else "费用金额"
            text_lines.append(
                f"{amount_label}: {amount} {str(line.get('currency') or source.get('currency') or '').strip()}".rstrip()
            )
        if line.get("waybill"):
            text_lines.append(f"运单号: {line['waybill']}")
        if line.get("approval_no"):
            text_lines.append(f"关联审批号: {line['approval_no']}")
        if billing_weight is not None and str(billing_weight).strip():
            text_lines.append(f"计费重量: {billing_weight} kg")
        if cargo_text:
            text_lines.append(f"发货明细: {cargo_text}")
        if not text_lines:
            continue
        if document_id and (document_id, sheet) in covered_documents:
            existing = result[covered_documents[(document_id, sheet)]]
            existing_text = str(existing.get("scoped_text") or "").strip()
            matched_text = "\n".join(text_lines)
            existing["scoped_text"] = "\n".join(filter(None, (existing_text, matched_text)))
            existing["content_hash"] = digest(
                existing.get("content_hash"), line_id, line.get("line_key"), matched_text
            )
            existing_codes = {str(row.get("material_code") or "").strip().casefold()
                              for row in existing.get("scoped_goods") or []}
            existing.setdefault("scoped_goods", []).extend(
                row for row in line_goods
                if str(row.get("material_code") or "").strip().casefold() not in existing_codes
            )
            continue
        source_kind = (
            "approval_comment_attachment" if document_id and evidence.get("comment_id")
            else "approval_attachment" if document_id
            else "approval_comment" if evidence.get("comment_id")
            else "approval_form"
        )
        file_name = str(evidence.get("file_name") or "")
        source_label = file_name or str(source.get("title") or "实际付款流程")
        if sheet:
            source_label += f" · {sheet}"
        evidence_id = digest(POLICY, clean, "freight_line", line_id, line.get("line_key"), evidence)
        selected_source = {
            "id": evidence_id,
            "source_id": str(source.get("id") or ""),
            "source_kind": source_kind,
            "source_label": source_label,
            "approval_no": str(source.get("approval_no") or ""),
            "source_snapshot": str(source.get("snapshot") or ""),
            "process_instance_id": str(source.get("instance") or ""),
            "occurred_at": str(source.get("source_updated_at") or ""),
            "evidence": evidence,
            "revision": evidence_id,
        }
        if document_id:
            selected_source["document_id"] = document_id
        if sheet:
            selected_source["sheet"] = sheet
        scoped_text = "\n".join(text_lines)
        scoped_goods = line_goods or (text_goods(cargo_text, evidence) if cargo_text else [])
        result.append(
            {
                "source_id": evidence_id,
                "logical_source_id": evidence_id,
                "source_kind": source_kind,
                "source_label": source_label,
                "file_name": file_name,
                "sheet_name": sheet,
                "approval_no": str(source.get("approval_no") or ""),
                "process_instance_id": str(source.get("instance") or ""),
                "approval_role": "payment",
                "approval_title": str(source.get("title") or "实际付款流程"),
                "source_updated_at": str(source.get("source_updated_at") or ""),
                "available": True,
                "excluded": False,
                "selected": True,
                "workflow_stage": "payment",
                "workflow_rank": 0,
                "payment_match_candidate": True,
                "payment_match_candidate_id": clean["candidate_id"],
                "payment_match_candidate_revision": clean["revision"],
                "payment_match_version": clean["version"],
                "payment_match_user_selected": bool(user_selected),
                "selected_source": selected_source,
                "scoped_packing": True,
                "scoped_goods": scoped_goods,
                "scoped_text": scoped_text,
                "form_fields": {"物流报价": scoped_text} if source_kind == "approval_form" else {},
                "approval_decisions": [],
                "can_download": False,
                "source_hash": evidence_id,
                "content_hash": evidence_id,
            }
        )
    # Semantic facts are derived only from the exact matcher-authorized line
    # IDs above.  This attachment step must never scan or widen to sibling rows
    # in the same monthly workbook.
    def fact_location(fact):
        provenance = fact.get("provenance") or {}
        return (
            str(provenance.get("document_id") or ""),
            str(provenance.get("sheet") or ""),
            provenance.get("row"),
            str(fact.get("waybill") or ""),
        )

    def source_location(preview):
        selected = preview.get("selected_source")
        selected = selected if isinstance(selected, dict) else {}
        evidence = _evidence(selected)
        text = str(preview.get("scoped_text") or "")
        waybill_match = re.search(r"(?:运单号|DHL\s*单号)\s*[:：]?\s*([^\s]+)", text, re.I)
        return (
            str(selected.get("document_id") or evidence.get("document_id") or ""),
            str(selected.get("sheet") or evidence.get("sheet") or preview.get("sheet_name") or ""),
            evidence.get("row"),
            waybill_match.group(1) if waybill_match else "",
        )

    eligible_facts = eligible_evidence_facts(semantic_facts)
    facts_by_location = {}
    for semantic_fact in semantic_facts:
        if semantic_fact.get("fact_kind") == "payment_freight_total":
            continue
        facts_by_location.setdefault(fact_location(semantic_fact), []).append(semantic_fact)
    total_facts = [
        fact for fact in semantic_facts if fact.get("fact_kind") == "payment_freight_total"
    ]
    total_attached = False
    existing_by_location = {source_location(preview): preview for preview in result}
    for fact in eligible_facts:
        target = fact["material_targets"][0]
        physical = fact.get("physical") or {}
        provenance = fact.get("provenance") or {}
        evidence = {
            key: deepcopy(provenance.get(key))
            for key in ("document_id", "file_name", "sheet", "row")
            if provenance.get(key) not in (None, "")
        }
        scoped_good = {
            "material_code": target.get("material_code") or "",
            "product_name": target.get("product_name") or "",
            "spec_model": "",
            "quantity": None,
            "unit": "",
            **{key: value for key, value in physical.items() if key != "dimensions_cm"},
            "dimensions_cm": deepcopy(physical.get("dimensions_cm") or []),
            "physical": {
                key: value for key, value in physical.items() if key != "dimensions_cm"
            },
            "evidence": evidence,
            "waybill": fact.get("waybill") or "",
            "package_identity": fact.get("package_identity") or "",
            "_fact_id": fact["fact_id"],
            "_material_key": target.get("material_key") or "",
            "_proposed_new_item": bool(fact.get("proposed_new_item")),
            "_scope_status": str(fact.get("scope_status") or ""),
        }
        location = fact_location(fact)
        preview = existing_by_location.get(location)
        if preview is None:
            evidence_id = digest(POLICY, clean, "semantic_fact", fact["fact_id"])
            source_kind = "approval_attachment" if evidence.get("document_id") else "approval_form"
            source_label = str(evidence.get("file_name") or source.get("title") or "实际付款流程")
            if evidence.get("sheet"):
                source_label += f" · {evidence['sheet']}"
            selected_source = {
                "id": evidence_id,
                "source_id": str(source.get("id") or ""),
                "source_kind": source_kind,
                "source_label": source_label,
                "approval_no": str(source.get("approval_no") or ""),
                "source_snapshot": str(source.get("snapshot") or ""),
                "process_instance_id": str(source.get("instance") or ""),
                "occurred_at": str(source.get("source_updated_at") or ""),
                "evidence": evidence,
                "revision": evidence_id,
            }
            if evidence.get("document_id"):
                selected_source["document_id"] = evidence["document_id"]
            if evidence.get("sheet"):
                selected_source["sheet"] = evidence["sheet"]
            preview = {
                "source_id": evidence_id,
                "logical_source_id": evidence_id,
                "source_kind": source_kind,
                "source_label": source_label,
                "file_name": str(evidence.get("file_name") or ""),
                "sheet_name": str(evidence.get("sheet") or ""),
                "approval_no": str(source.get("approval_no") or ""),
                "process_instance_id": str(source.get("instance") or ""),
                "approval_role": "payment",
                "approval_title": str(source.get("title") or "实际付款流程"),
                "source_updated_at": str(source.get("source_updated_at") or ""),
                "available": True,
                "excluded": False,
                "selected": True,
                "workflow_stage": "payment",
                "workflow_rank": 0,
                "payment_match_candidate": True,
                "payment_match_candidate_id": clean["candidate_id"],
                "payment_match_candidate_revision": clean["revision"],
                "payment_match_version": clean["version"],
                "payment_match_user_selected": bool(user_selected),
                "selected_source": selected_source,
                "scoped_packing": True,
                "scoped_goods": [scoped_good],
                "scoped_text": "\n".join(
                    filter(
                        None,
                        (
                            f"运单号: {fact.get('waybill')}" if fact.get("waybill") else "",
                            f"物料编码: {target.get('material_code')}" if target.get("material_code") else "",
                        ),
                    )
                ),
                "form_fields": {},
                "approval_decisions": [],
                "can_download": False,
                "source_hash": evidence_id,
                "content_hash": evidence_id,
                "packing_group_candidates": [],
            }
            result.append(preview)
            existing_by_location[location] = preview
        else:
            preview["scoped_goods"] = [scoped_good]
        fact_bundle = list(facts_by_location.get(location) or [fact])
        if not total_attached and total_facts:
            fact_bundle.extend(total_facts)
            total_attached = True
        preview["semantic_facts"] = fact_bundle
        preview["semantic_fact_ids"] = [row["fact_id"] for row in fact_bundle]
    if not result:
        # A strong process-level relation is still useful provenance even when
        # a monthly statement cannot be narrowed to one shipment line.  Keep
        # the matched payment process visible, but publish no amount, document
        # contents, or AI input; downstream stage arbitration will safely fall
        # through to the logistics stage for every missing field.
        reason = "已匹配支付流程，但未识别出属于本票的可采用明细；已继续使用下一优先级阶段。"
        fallback = _metadata_only_process_source(source, reason=reason, reference=clean)
        fallback["payment_match_user_selected"] = bool(user_selected)
        result.append(fallback)
    return result
