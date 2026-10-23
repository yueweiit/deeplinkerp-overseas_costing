"""费用修改、凭证关联和最终确认事务。"""

from __future__ import annotations

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message


import json
import uuid
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    import frappe
except Exception:  # pragma: no cover - 纯函数测试时保持可导入
    frappe = None

from overseas_costing.services import (
    edit_session_service,
    fee_allocation_service,
    fee_status_service,
)
from overseas_costing.services.transport_fee_service import (
    HISTORICAL_SURCHARGES,
    OTHER_FEE_TYPES,
    PRIMARY_FREIGHT,
    assert_no_duplicate_fees,
    fee_is_active,
    is_other_fee_key,
    legacy_oa_freight_updates,
    mark_duplicate_fees,
    primary_freight_definition,
    resolve_transport_mode,
)


AMOUNT_STATUSES = frozenset({"MISSING", "ESTIMATED", "ACTUAL", "NOT_INCURRED", "INCLUDED"})
SCOPE_TYPES = frozenset({"ALL_ITEMS", "ITEMS", "DIRECT_ITEM"})
ALLOCATION_BASES = frozenset({"goods_value", "gross_weight", "volume", "chargeable_weight"})
EVIDENCE_STATUSES = frozenset({"PENDING", "VALID", "INVALID", "UNLINKED"})
FEE_FIELDS = (
    "is_final", "source_binding_id", "source_snapshot", "covered_scopes",
    "logical_fee_key",
    "rule_code",
    "expense_category",
    "amount_status",
    "amount",
    "currency",
    "allocation_basis",
    "basis_field",
    "scope_type",
    "scope_value_json",
    "required_evidence_role",
    "included_in_fee_key",
    "priority_no",
    "remark",
    "status_change_reason",
    "status_changed_by",
    "status_changed_at",
    "is_active",
    "is_enabled",
)
COST_INPUT_FIELDS = (
    "amount_status",
    "amount",
    "currency",
    "allocation_basis",
    "scope_type",
    "scope_value_json",
    "included_in_fee_key",
    "is_enabled",
)

COMMON_FEE_TEMPLATES = (
    ("customs_clearance_fee", "清关费", "goods_value", "customs_declaration"),
    ("import_tax", "进口税费", "goods_value", "tax_certificate"),
    ("destination_delivery", "目的地配送费", "gross_weight", "expense_invoice"),
)


def _normalized_fee_name(value: object) -> str:
    return "".join(str(value or "").strip().casefold().replace("／", "/").split())


def build_default_fee_templates(transport_mode: str) -> list[dict]:
    mode = resolve_transport_mode(transport_mode)
    rows = [(*PRIMARY_FREIGHT[mode], "freight_invoice")] if mode else []
    if mode == "EXPRESS":
        rows.append(HISTORICAL_SURCHARGES[2])
    rows.extend(COMMON_FEE_TEMPLATES)
    templates = [
        {
            "name": "",
            "logical_fee_key": key,
            "rule_code": key,
            "expense_category": label,
            "amount_status": "MISSING",
            "amount": "",
            "currency": "RMB",
            "allocation_basis": basis,
            "basis_field": basis,
            "scope_type": "ALL_ITEMS",
            "scope_value_json": "[]",
            "required_evidence_role": evidence_role,
            "included_in_fee_key": "",
            "priority_no": index,
            "remark": "",
            "is_active": 1,
            "is_enabled": 1,
            "virtual": True,
        }
        for index, (key, label, basis, evidence_role) in enumerate(rows, start=1)
    ]
    for row in templates:
        key = row["logical_fee_key"]
        if key in {"customs_clearance_fee", "import_tax", "destination_delivery", "express_surcharge"}:
            row["entry_responsibility"] = "MEXICO"
        if key in {"customs_clearance_fee", "import_tax", "destination_delivery"}:
            row["currency"] = "MXN"
        if key in {"express_surcharge", "destination_delivery"}:
            # A display/preview default, not a persisted actual or no-charge declaration.
            row.update(amount="0", amount_status="ESTIMATED", is_default_zero=True)
        if key == "destination_delivery":
            row["expense_category"] = "当地快递费" if mode == "EXPRESS" else "当地配送费"
    return templates


def build_other_fee_templates() -> list[dict]:
    """Optional menu entries share local-delivery defaults in every transport mode."""

    delivery = next(row for row in build_default_fee_templates("")
                    if row["logical_fee_key"] == "destination_delivery")
    return [
        {**delivery, "logical_fee_key": key, "rule_code": key,
         "expense_category": label, "priority_no": 100 + index}
        for index, (key, label) in enumerate(OTHER_FEE_TYPES)
    ]


def map_historical_fee_key(fee: dict, transport_mode: str) -> str:
    explicit = str(fee.get("logical_fee_key") or "").strip()
    if explicit:
        return explicit
    aliases = {}
    for row in build_default_fee_templates(transport_mode):
        key = row["logical_fee_key"]
        aliases[_normalized_fee_name(row["expense_category"])] = key
        aliases[_normalized_fee_name(key)] = key
    for key, label, *_ in [*PRIMARY_FREIGHT.values(), *HISTORICAL_SURCHARGES]:
        aliases[_normalized_fee_name(label)] = key
        aliases[_normalized_fee_name(key)] = key
    aliases.update(
        {
            "oceanfreight": "international_sea_freight",
            "seafreight": "international_sea_freight",
            "airfreight": "international_air_freight",
            "expressfreight": "international_express_fee",
            "customsclearance": "customs_clearance_fee",
            "importduty": "import_tax",
            "delivery": "destination_delivery",
            "目的地配送费": "destination_delivery",
            "当地配送费": "destination_delivery",
        }
    )
    mode = resolve_transport_mode(transport_mode)
    if mode:
        primary_key = primary_freight_definition(mode)["logical_fee_key"]
        aliases["国际物流费用"] = primary_key
        aliases["oa_logistics_freight"] = primary_key
    if mode == "EXPRESS":
        aliases["当地快递费"] = "destination_delivery"
    for candidate in (fee.get("expense_category"), fee.get("rule_code")):
        matched = aliases.get(_normalized_fee_name(candidate))
        if matched:
            return matched
    return ""


def _decorate_historical_rules(rules: list[dict], transport_mode: str) -> list[dict]:
    """Add read-time logical identities without rewriting historical rows."""

    decorated = []
    for raw in rules or []:
        row = dict(raw or {})
        from overseas_costing.services.logistics_settlement.fee_policy import is_final
        if is_final(row):
            row['logical_fee_key'] = row.get('logical_fee_key') or row.get('rule_code') or row.get('name')
            row.update(virtual=False, historical_name_mapped=False, legacy_unmapped=False)
            decorated.append(row)
            continue
        row.update(legacy_oa_freight_updates(row, transport_mode))
        original_key = str(row.get("logical_fee_key") or "").strip()
        mapped_key = map_historical_fee_key(row, transport_mode)
        if mapped_key:
            row["logical_fee_key"] = mapped_key
            row["historical_name_mapped"] = not bool(original_key)
            row["legacy_unmapped"] = False
        else:
            identity = str(row.get("name") or row.get("rule_code") or "unidentified").strip()
            row["logical_fee_key"] = original_key or f"legacy:{identity}"
            row["historical_name_mapped"] = False
            row["legacy_unmapped"] = True
        row["virtual"] = False
        decorated.append(row)
    return decorated


def compose_fee_worklist_rows(existing_fees: list[dict], transport_mode: str, *, source_context=None) -> list[dict]:
    """Overlay persisted fees; preserve all active conflicts and retired intent."""

    from overseas_costing.services.logistics_settlement.fee_policy import covered_scopes, row_scopes, select_fees, is_final
    freight=(source_context or {}).get('freight') or {}
    pending_freight=freight.get('selected') and not freight.get('available')
    decorated=_decorate_historical_rules(existing_fees, transport_mode)
    decorated=[r for r in decorated if 'freight' not in row_scopes(r)] if pending_freight else select_fees(decorated,source_context=source_context)
    if source_context and source_context.get('root_kind') == 'expense' and not source_context.get('separate_adoption'):
        return mark_duplicate_fees(decorated)
    covered = covered_scopes(decorated)
    if pending_freight: covered.add('freight')
    templates = [row for row in build_default_fee_templates(transport_mode) if not row_scopes(row) & covered]
    template_by_key = {row["logical_fee_key"]: dict(row) for row in templates}
    extras = []
    retired_keys = {row["logical_fee_key"] for row in decorated if not fee_is_active(row)}
    for row in decorated:
        if not fee_is_active(row):
            if any(is_final(fee) for fee in decorated):
                extras.append(row)  # Preserve explicit retired intent for legacy item-pool eligibility.
            continue
        key = str(row.get("logical_fee_key") or "")
        if key in template_by_key:
            if not template_by_key[key].get("virtual"):
                extras.append(row)
                continue
            template = template_by_key[key]
            merged = {**template, **row, "virtual": False, "is_default_zero": False}
            if key == "destination_delivery" or key in {value[0] for value in PRIMARY_FREIGHT.values()}:
                merged["expense_category"] = template["expense_category"]
            for fieldname in (
                "rule_code",
                "currency",
                "allocation_basis",
                "basis_field",
                "scope_type",
                "scope_value_json",
                "required_evidence_role",
            ):
                if row.get(fieldname) in (None, ""):
                    # Preserve the historical RMB interpretation of saved blank currency.
                    # MXN defaults apply only to new, virtual fee rows.
                    merged[fieldname] = "RMB" if fieldname == "currency" else template.get(fieldname)
            if row.get("amount_status") in (None, ""):
                merged["amount_status"] = fee_allocation_service.amount_status(row)
            template_by_key[key] = merged
        else:
            extras.append(row)
    rows = [template_by_key[row["logical_fee_key"]] for row in templates
            if row["logical_fee_key"] not in retired_keys or not template_by_key[row["logical_fee_key"]].get("virtual")]
    rows.extend(extras)
    claims={c['id']:c for c in freight.get('claims') or []}
    for row in rows:
        claim=claims.get(row.get('source_binding_id'))
        if claim:
            row.update(freight_claim_id=claim['id'],original_amount=claim.get('original_amount',claim.get('amount')),
                applied_amount=claim.get('applied_amount',claim.get('amount')),source_label=claim.get('title') or claim.get('approval_no') or '支付账单',
                manual_corrected=claim.get('manual_corrected',False))
    return mark_duplicate_fees(rows)


def _safe_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _candidate_currency(payload: dict) -> str:
    for key, value in payload.items():
        if str(key).strip().casefold() in {"currency", "currency_code", "币种"}:
            currency = str(value or "").strip().upper()
            if currency:
                return "RMB" if currency == "CNY" else currency
    for value in payload.values():
        if isinstance(value, dict):
            nested = _candidate_currency(value)
            if nested:
                return nested
    return ""


def _extract_amount_candidates(payload: dict) -> list[dict]:
    currency = _candidate_currency(payload)
    result = []
    seen = set()

    def walk(value, path: str = "", depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                normalized_key = str(key).strip().casefold()
                is_amount_key = any(
                    marker in normalized_key
                    for marker in ("amount", "total", "fee", "tax", "金额", "费用", "税费")
                )
                if is_amount_key and not isinstance(child, (dict, list, tuple, bool)):
                    try:
                        amount = Decimal(str(child))
                    except (InvalidOperation, TypeError, ValueError):
                        amount = None
                    if amount is not None and amount.is_finite() and amount >= 0:
                        candidate = (
                            format(amount.normalize(), "f"),
                            currency,
                            child_path,
                        )
                        if candidate not in seen:
                            seen.add(candidate)
                            result.append(
                                {"amount": candidate[0], "currency": currency, "path": child_path}
                            )
                walk(child, child_path, depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", depth + 1)

    walk(payload)
    return result[:30]


#: 文件名命中该词时前端高亮，提示这是用户要优先关联的凭证件。
#: 放在服务端是为了让判定与展示同源，避免前端各自维护一份关键词。
VOUCHER_FILE_NAME_MARKER = "凭证"


def is_voucher_file_name(file_name: object) -> bool:
    """Return True when the attachment file name advertises itself as a voucher.

    ``Overseas Cost Attachment`` 没有凭证标记字段，用户上传时是否把文件命名成
    “XX凭证”是他唯一的显式表达。这里只做一次子串判定，不猜测、不推断语义。
    """

    return VOUCHER_FILE_NAME_MARKER in str(file_name or "")


def _candidate_audit_only(
    parsed: dict,
    descriptor: dict | None,
    attachment: dict,
    version_name: str | None,
) -> tuple[bool, str]:
    """Reuse the unified settlement audit policy for this candidate's flag and reason.

    判定口径统一在 ``services/settlement_audit_policy.py``：只有**真排除**
    （审批被拒绝/撤销/终止，或附件被撤销、替代）才叫“仅审计”。审批还在走流程
    的属于业务常态，返回“审批未完成”而不是“仅审计”，避免误导使用者。

    刻意**不含** ``import_service`` 里“OA 且无版本”那一款：月结付款这类不在
    国际物流关联链里的资料本来就可能没有版本，照搬会把它们误判。
    """

    from overseas_costing.services.settlement_audit_policy import settlement_audit_state

    state = settlement_audit_state({**(parsed or {}), "settlement_document": descriptor or {}})
    if state["audit_only"]:
        return True, state["reason"]
    if state["pending_approval"]:
        return False, ""
    version = str(attachment.get("version") or "").strip()
    if version and version_name and version != str(version_name).strip():
        return True, "不属于当前版本，仅审计。"
    return False, ""


def build_evidence_candidates(
    attachments: list[dict],
    *,
    version_name: str | None = None,
    approval_detail: dict | None = None,
) -> list[dict]:
    """Expose parser values as candidates only; never synthesize fee records.

    每个候选都带上服务端判定的 ``workflow_stage``，前端据此按采购、费用申请、
    国际物流三流程分组展示，避免浏览器私造流程真相。

    附件本地行里并不保存流程身份：``Overseas Cost Attachment`` 没有
    ``approval_role`` 字段，缓存下来的 ``parse_result_json`` 也常常只有
    ``process_instance_id``。因此这里复用资料 AI 填充链路已经在用的那份
    审批清单（``_list_approval_body_ai_sources``），用 ``process_instance_id``
    把本地附件挂回它真正所属的审批，再交给共享分类器判定流程。
    """

    from overseas_costing.services.source_priority_service import (
        classify_evidence_kind,
        classify_workflow_stage,
        WORKFLOW_LABELS,
    )

    approval_by_instance = _approval_identity_index(approval_detail)
    result = []
    for attachment in attachments or []:
        parsed = _safe_json_dict(attachment.get("parse_result_json"))
        mapped = _safe_json_dict(attachment.get("mapped_result_json"))
        descriptor = parsed.get("settlement_document") or mapped.get("settlement_document")
        descriptor = descriptor if isinstance(descriptor, dict) else None
        if descriptor and version_name and attachment.get("version") not in (None, "", version_name):
            continue
        classification = parsed.get("classification") if isinstance(parsed.get("classification"), dict) else {}
        instance_id = str(parsed.get("process_instance_id") or parsed.get("instance_id") or "").strip()
        evidence_source = _evidence_candidate_source(attachment, parsed, mapped)
        owning_approval = approval_by_instance.get(instance_id) or {}
        if owning_approval:
            # The approval inventory is the authority on process identity; the
            # local row is only a cache of one file inside that process.
            #
            # ``source_label`` 必须一起换成审批自己的标签。共享分类器把
            # ``source_label`` 读进流程身份文本（``_workflow_identity_text``），
            # 而它在比对“月结／报销”这类支付词之前并不先看 ``approval_role``。
            # 若这里继续塞文件名，一份挂在国际物流审批下、只是名字叫
            # “月结运费凭证.pdf”的附件，就会被名字抢进“费用申请”。
            # 审批清单给得出归属时，流程只能由归属说了算，文件名不参与判定。
            evidence_source = {
                **evidence_source,
                "source_kind": evidence_source.get("source_kind") or "approval_attachment",
                "source_label": owning_approval.get("source_label") or evidence_source.get("source_label"),
                "approval_role": owning_approval.get("approval_role") or evidence_source.get("approval_role"),
                "approval_title": owning_approval.get("approval_title") or evidence_source.get("approval_title"),
                "process_title": owning_approval.get("process_title") or evidence_source.get("process_title"),
                "process_name": owning_approval.get("process_name") or evidence_source.get("process_name"),
                "form_fields": owning_approval.get("form_fields") or evidence_source.get("form_fields") or {},
            }
        workflow_stage = classify_workflow_stage(evidence_source)
        file_name = str(attachment.get("file_name") or "")
        attachment_type = str(attachment.get("attachment_type") or "")
        audit_only, audit_reason = _candidate_audit_only(parsed, descriptor, attachment, version_name)
        result.append(
            {
                "attachment": str(attachment.get("name") or ""),
                "file_name": file_name,
                "file_url": str(attachment.get("file_url") or ""),
                "source_type": str(attachment.get("source_type") or ""),
                "attachment_type": attachment_type,
                "parse_status": str(attachment.get("parse_status") or "Draft"),
                "classification": classification,
                "version": attachment.get("version"),
                "settlement_document": descriptor,
                "workflow_stage": workflow_stage,
                "workflow_label": WORKFLOW_LABELS.get(workflow_stage, WORKFLOW_LABELS["other"]),
                "workflow_source": "approval_inventory" if owning_approval else "attachment_cache",
                "process_instance_id": instance_id,
                "evidence_kind": classify_evidence_kind(evidence_source),
                # 文件名含“凭证”的资料在弹窗里高亮，用户第一眼就能找到目标件。
                "is_voucher_name": is_voucher_file_name(file_name),
                "audit_only": audit_only,
                "audit_only_reason": audit_reason,
                "amount_candidates": [] if descriptor else _extract_amount_candidates({**parsed, **mapped}),
                "summary": _evidence_candidate_summary(
                    parsed=parsed,
                    mapped=mapped,
                    classification=classification,
                    attachment=attachment,
                    owning_approval=owning_approval,
                ),
            }
        )
    return _merge_same_file_candidates(result)


# 解析进度排序：解析完成的副本优先于还在排队的，排队优先于未开始的草稿。
_PARSE_STATUS_RANK = {"parsed": 3, "queued": 2, "draft": 1}


def _candidate_preference(candidate: dict) -> tuple:
    """同版本重复副本里挑一条保留时的取舍顺序。

    先要“这份能拿去核算”（非仅审计），再要“这份能打开、能解析”（有 file_url），
    然后是解析进度更靠前的，最后才看摘要完整度。
    """

    return (
        0 if candidate.get("audit_only") else 1,
        1 if str(candidate.get("file_url") or "").strip() else 0,
        _PARSE_STATUS_RANK.get(str(candidate.get("parse_status") or "").strip().lower(), 0),
        len(candidate.get("summary") or {}),
    )


def _merge_same_file_candidates(candidates: list[dict]) -> list[dict]:
    """同一版本内的同一份文件只留一条候选。

    OA 同步链路会把同一份文件（同一 ``file_url``／``file_id``）重复登记，
    同一批次同一版本下因此可能并存多条同名副本：一条带 ``file_url`` 可用于解析，
    其余是 ``file_url`` 为空的空壳。它们指向同一个物理文件，界面上表现为
    “同一份报价单出现两次、状态还不一样”，用户无从选择。

    归并只发生在**同一版本内**：跨版本的副本各自保留，因为“不属于当前版本”
    正是要讲给用户的审计信息。

    与 ``deduplicate_evidence_candidates`` 分工不同：那个处理的是**已入库凭证行**
    （按附件＋角色＋修订号规范成三元组），这里处理的是**候选池**，要保留候选的
    全部字段供选择界面使用，两者不是同一层面的去重，不能互相替代。

    身份取文件名而不是 ``file_url``：线上重复登记的实况正是一条带 ``file_url``、
    其余为空，用 ``file_url`` 做键反而会把它们判成不同文件。文件名缺失时才退到
    ``file_url``。

    这里不改写任何原始附件行，只是让候选池不再把同一个文件重复摆出来。
    """

    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for index, candidate in enumerate(candidates or []):
        file_key = str(candidate.get("file_name") or "").strip().lower() or str(
            candidate.get("file_url") or ""
        ).strip()
        # 既没有文件名也没有 file_url 时无从判断是否同一个文件，原样保留。
        key = (str(candidate.get("version") or ""), file_key) if file_key else ("", f"__row_{index}")
        current = merged.get(key)
        if current is None:
            merged[key] = candidate
            order.append(key)
            continue
        winner, loser = (
            (candidate, current)
            if _candidate_preference(candidate) > _candidate_preference(current)
            else (current, candidate)
        )
        merged[key] = _adopt_missing_file_url(winner, loser)
    return [merged[key] for key in order]


def _adopt_missing_file_url(winner: dict, loser: dict) -> dict:
    """保留的副本缺文件地址时，从同组的其它副本借一个。

    线上重复登记的实况是：“能进核算”的那条 ``file_url`` 为空，被标仅审计的
    副本反而有地址。只按口径留一条的话，界面上留下的候选既打不开也解析不了，
    所以同一版本、同一文件名的副本之间把文件地址补齐 —— 它们本来就是同一个
    物理文件。只借 ``file_url`` 这一个字段，不搬运任何判定结果。
    """

    if str(winner.get("file_url") or "").strip():
        return winner
    file_url = str(loser.get("file_url") or "").strip()
    if not file_url:
        return winner
    return {**winner, "file_url": file_url}


def _evidence_candidate_summary(
    *,
    parsed: dict,
    mapped: dict,
    classification: dict,
    attachment: dict,
    owning_approval: dict,
) -> dict:
    """Project already-parsed evidence into a pickable summary.

    选择阶段只展示服务端**已经解析出来的**字段，避免在弹窗里现调 AI。
    这里不生成任何新数值，只把既有解析结果整理成可展示的键值，供前端在流程
    标签下优先展示，帮助用户判断该选哪一份凭证。
    """

    source = classification if isinstance(classification, dict) else {}
    summary: dict = {}
    for key, label in (
        ("code", "类型"),
        ("category", "类别"),
        ("name", "名称"),
        ("label", "分类"),
    ):
        value = str(source.get(key) or "").strip()
        if value:
            summary[label] = value
            break

    evidence_type = str(
        parsed.get("evidence_type") or mapped.get("evidence_type") or source.get("evidence_type") or ""
    ).strip()
    if evidence_type:
        summary["凭证种类"] = evidence_type

    # 金额与币种取自既有解析结果，服务端不重新计算。
    amounts = _extract_amount_candidates({**parsed, **mapped})
    if amounts:
        first = amounts[0]
        amount_text = str(first.get("amount") or "").strip()
        currency = str(first.get("currency") or "").strip()
        if amount_text:
            summary["金额"] = f"{currency} {amount_text}".strip()

    for key, label in (
        ("expense_type", "费用类别"),
        # ``occurred_at`` 在全仓的语义是**来源／审批时间**（``approval.finish_time``、
        # ``comment.create_time``、``source_updated_at``），不是单据自身的开票日期。
        # 早先误标成“发生日期”，会让用户把别的时段的时间当成凭证日期。
        ("occurred_at", "来源时间"),
        ("invoice_no", "单据号"),
        ("source_doc_no", "单据号"),
    ):
        value = str(parsed.get(key) or mapped.get(key) or "").strip()
        if value and label not in summary:
            summary[label] = value

    # 归属审批的名称是判断“这份资料属于哪个流程”的最直接线索。
    approval_title = str(owning_approval.get("approval_title") or "").strip()
    if approval_title:
        summary["所属审批"] = approval_title

    form_fields = owning_approval.get("form_fields") or {}
    if isinstance(form_fields, dict):
        for field_name, field_value in form_fields.items():
            label = str(field_name or "").strip()
            text = str(field_value or "").strip()
            if not text or not label:
                continue
            if any(marker in label for marker in ("金额", "合计", "总额", "币种", "供应商", "费用类型")):
                summary.setdefault(label[:20], text[:60])
            if len(summary) >= 8:
                break

    # 解析状态不在这里再给一遍：候选顶层本来就有 ``parse_status`` 字段，选项的
    # 元信息行已经在展示它，摘要只补用户在选择时看不到的信息。
    # 键值对上限用于保护前端渲染，摘要只是辅助信息，不承载业务真相。
    return dict(list(summary.items())[:8])


def _fee_approval_detail(batch_name: str, version_name: str | None) -> dict:
    """Resolve the same approval inventory the material AI-fill chain consumes.

    The fee evidence picker needs process identity for every cached attachment,
    and that identity only exists on the live approval inventory.  This reuses
    ``related_approval_detail`` rather than opening a second resolution path.
    """

    from overseas_costing.services.packing_source_service import related_approval_detail

    try:
        return related_approval_detail(batch_name, version_name) or {}
    except Exception:
        return {}


def _approval_identity_index(approval_detail: dict | None) -> dict[str, dict]:
    """Index the shared approval inventory by instance id for attachment joins."""

    if not approval_detail:
        return {}
    from overseas_costing.services.packing_snapshot_service import _list_approval_body_ai_sources

    index: dict[str, dict] = {}
    try:
        sources = _list_approval_body_ai_sources("", detail=approval_detail)
    except Exception:
        return {}
    for source in sources:
        instance_id = str(source.get("process_instance_id") or "").strip()
        if instance_id:
            index.setdefault(instance_id, source)
    return index


def _evidence_candidate_source(attachment: dict, parsed: dict, mapped: dict) -> dict:
    """Project one attachment onto the shared workflow classifier's input contract.

    这里的 ``source_label`` 只是**拿不到审批归属时**的兜底：用户上传时常靠命名
    表达“这是哪一类付款”，清单里查不到 instance 时只能认这个名字。一旦
    ``build_evidence_candidates`` 用 ``process_instance_id`` 挂上了审批，
    调用方会立刻把 ``source_label`` 换成审批标签，文件名不再参与流程判定。
    """

    descriptor = parsed.get("settlement_document") or mapped.get("settlement_document")
    descriptor = descriptor if isinstance(descriptor, dict) else {}
    form_fields = parsed.get("form_fields") or mapped.get("form_fields")
    return {
        "source_kind": "approval_attachment" if attachment.get("source_type") else "",
        "source_label": attachment.get("file_name") or attachment.get("name"),
        "file_name": attachment.get("file_name"),
        "attachment_type": attachment.get("attachment_type"),
        "approval_role": descriptor.get("approval_role") or parsed.get("approval_role") or "",
        "approval_title": descriptor.get("approval_title") or parsed.get("approval_title") or "",
        "process_title": descriptor.get("process_title") or parsed.get("process_title") or "",
        "process_name": descriptor.get("process_name") or parsed.get("process_name") or "",
        "expense_type": descriptor.get("expense_type") or parsed.get("expense_type") or "",
        "form_fields": form_fields if isinstance(form_fields, dict) else {},
    }


def _load_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    loaded = json.loads(value or "{}")
    if not isinstance(loaded, dict):
        raise ValueError(_i18n("费用参数必须是 JSON 对象。"))
    return loaded


def _decimal_text(value) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(_i18n("费用金额格式不正确。")) from exc
    if not number.is_finite():
        raise ValueError(_i18n("费用金额必须是有限数值。"))
    if number < 0:
        raise ValueError(_i18n("费用金额不能小于 0。"))
    return format(number.normalize(), "f")


def _scope_keys(payload: dict) -> list[str]:
    return sorted(set(fee_allocation_service._scope_keys(payload)))


def default_status_for_amount_entry(current_status: object, amount: object) -> str:
    """The first entered amount is provisional until final evidence proves otherwise."""

    status = str(current_status or "MISSING").strip().upper()
    if status == "MISSING" and amount not in (None, ""):
        return "ESTIMATED"
    return status


def validate_fee_status_transition(
    previous_status: object,
    next_status: object,
    *,
    reason: object = "",
    has_valid_final_evidence: bool = False,
) -> dict:
    previous = str(previous_status or "MISSING").strip().upper()
    current = str(next_status or "MISSING").strip().upper()
    normalized_reason = str(reason or "").strip()
    changed = previous != current
    requires_reason = changed and (
        current in {"NOT_INCURRED", "INCLUDED"}
        or (previous == "ACTUAL" and current == "ESTIMATED")
        or (current == "ACTUAL" and not has_valid_final_evidence)
    )
    if requires_reason and not normalized_reason:
        if current == "ACTUAL":
            raise ValueError(_i18n("没有已核对的最终凭证，改为实际费用时必须填写原因。"))
        raise ValueError(_i18n("该费用状态变更必须填写原因。"))
    return {
        "previous_status": previous,
        "next_status": current,
        "reason": normalized_reason,
        "requires_reason": requires_reason,
    }


def normalize_fee_payload(payload, *, trusted_project_policy=False) -> dict:
    raw = _load_dict(payload)
    fee_key = str(raw.get("logical_fee_key") or "").strip()
    if not fee_key:
        raise ValueError(_i18n("逻辑费用标识不能为空。"))
    if fee_key.startswith("other_custom:") and not is_other_fee_key(fee_key):
        raise ValueError(_i18n("其他费用实例标识不合法。"))
    if is_other_fee_key(fee_key):
        template_key = "other_custom" if fee_key.startswith("other_custom:") else fee_key
        template = next(row for row in build_other_fee_templates() if row["logical_fee_key"] == template_key)
        template.update(logical_fee_key=fee_key, rule_code=fee_key)
        if "allocation_basis" not in raw and raw.get("basis_field"):
            template["allocation_basis"] = raw["basis_field"]
        raw = {**template, **raw}
        label = str(raw.get("expense_category") or "").strip()
        if not label or len(label) > 140:
            raise ValueError(_i18n("其他费用名称不能为空，且不能超过 140 个字符。"))
        raw["expense_category"] = label

    status = default_status_for_amount_entry(raw.get("amount_status"), raw.get("amount"))
    if status not in AMOUNT_STATUSES:
        raise ValueError(_i18n("金额状态不合法。"))
    scope_type = str(raw.get("scope_type") or "ALL_ITEMS").strip().upper()
    if scope_type not in SCOPE_TYPES:
        raise ValueError(_i18n("费用适用范围不合法。"))
    basis = str(raw.get("allocation_basis") or raw.get("basis_field") or "goods_value").strip()
    if basis not in ALLOCATION_BASES:
        raise ValueError(_i18n("费用分摊依据不合法。"))

    remark = str(raw.get("remark") or "").strip()
    included_in = str(raw.get("included_in_fee_key") or "").strip()
    if status in {"NOT_INCURRED", "INCLUDED"} and not remark:
        raise ValueError(_i18n("标记未发生或已包含时必须填写原因。"))
    if status == "INCLUDED" and not included_in:
        raise ValueError(_i18n("标记已包含时必须选择“已包含于费用”或本批采购金额。"))

    keys = _scope_keys(raw)
    if scope_type in {"ITEMS", "DIRECT_ITEM"} and not keys:
        raise ValueError(_i18n("请选择费用适用的物料。"))
    if scope_type == "DIRECT_ITEM" and len(keys) != 1:
        raise ValueError(_i18n("直接费用必须且只能选择一条物料。"))

    amount = ""
    if status in fee_allocation_service.COUNTED_AMOUNT_STATUSES:
        if raw.get("amount") in (None, ""):
            raise ValueError(_i18n("暂估或实际费用必须填写金额。"))
        amount = _decimal_text(raw.get("amount"))
    elif raw.get("amount") not in (None, ""):
        amount = _decimal_text(raw.get("amount"))

    currency = (
        "RMB"
        if "currency" not in raw
        else str(raw.get("currency") if raw.get("currency") is not None else "").strip().upper()
    )
    if currency == "CNY":
        currency = "RMB"
    if currency not in {"RMB", "MXN", "USD"}:
        raise ValueError(_i18n("费用币种只能选择人民币（RMB）、墨西哥比索（MXN）或美金（USD）。"))

    normalized = {key: raw[key] for key in FEE_FIELDS if key in raw}
    normalized.update(
        {
            "logical_fee_key": fee_key,
            "rule_code": str(raw.get("rule_code") or fee_key).strip(),
            "amount_status": status,
            "amount": amount,
            "currency": currency,
            "allocation_basis": basis,
            "basis_field": basis,
            "scope_type": scope_type,
            "scope_value_json": json.dumps(keys, ensure_ascii=False, separators=(",", ":")) if scope_type != "ALL_ITEMS" else "[]",
            "included_in_fee_key": included_in,
            "remark": remark,
            "is_active": 1 if raw.get("is_active", 1) else 0,
            "is_enabled": 1 if raw.get("is_enabled", 1) else 0,
        }
    )
    from overseas_costing.services.project_freight_service import policy_from_fee, validate_policy
    policy = policy_from_fee(raw)
    if policy:
        if not trusted_project_policy:
            raise ValueError(_i18n('项目分摊规则只能由可信资料审核生成。'))
        validate_policy(policy)
        if scope_type != 'ALL_ITEMS' or basis != 'gross_weight':
            raise ValueError(_i18n('项目毛重规则不能变更为其他范围或依据。'))
        normalized['scope_value_json'] = json.dumps({'item_keys': [], 'project_allocation': policy}, ensure_ascii=False, sort_keys=True)
    return normalized


def _cost_value(record: dict, fieldname: str):
    value = record.get(fieldname)
    if fieldname == "amount" and value not in (None, ""):
        return _decimal_text(value)
    if fieldname == "scope_value_json":
        from overseas_costing.services.project_freight_service import policy_from_fee
        return (tuple(_scope_keys(record)), json.dumps(policy_from_fee(record), sort_keys=True))
    return value


def merge_logical_fee(existing_fees: list[dict], payload: dict, *, revision: str | None = None) -> dict:
    assert_no_duplicate_fees(existing_fees)
    fees = deepcopy(existing_fees or [])
    fee_key = payload["logical_fee_key"]
    matches = [index for index, row in enumerate(fees) if str(row.get("logical_fee_key") or "") == fee_key]
    existing_index = next((index for index in matches if fee_is_active(fees[index])), matches[0] if matches else None)
    previous = fees[existing_index] if existing_index is not None else {}
    merged = {**previous, **payload}
    from overseas_costing.services.project_freight_service import policy_from_fee
    # Ordinary amount/status edits cannot erase a server-confirmed policy.
    if policy_from_fee(previous) and not policy_from_fee(payload):
        if merged.get('scope_type') != 'ALL_ITEMS' or merged.get('allocation_basis') != 'gross_weight':
            raise ValueError(_i18n('已有项目毛重分摊规则，请重新分析资料后调整。'))
        merged['scope_value_json'] = previous['scope_value_json']
    if previous and not fee_is_active(previous) and fee_is_active(merged):
        raise ValueError(_i18n("该费用已停用，请先核对历史记录，不能通过保存金额恢复启用。"))
    if previous.get("rule_code") == "oa_logistics_freight":
        merged["rule_code"] = previous["rule_code"]
    cost_inputs_changed = existing_index is None or any(
        _cost_value(previous, fieldname) != _cost_value(merged, fieldname)
        for fieldname in COST_INPUT_FIELDS
    )
    revision_value = revision or uuid.uuid4().hex
    if existing_index is None or any(
        _cost_value(previous, fieldname) != _cost_value(merged, fieldname)
        for fieldname in ("amount", "amount_status", "currency")
    ):
        merged["amount_revision"] = revision_value
    if existing_index is None or any(
        _cost_value(previous, fieldname) != _cost_value(merged, fieldname)
        for fieldname in ("scope_type", "scope_value_json", "allocation_basis")
    ):
        merged["scope_revision"] = revision_value

    if existing_index is None:
        fees.append(merged)
        action = "created"
    else:
        fees[existing_index] = merged
        action = "updated"
    return {
        "fees": fees,
        "fee": merged,
        "action": action,
        "cost_inputs_changed": cost_inputs_changed,
    }


def deduplicate_evidence_candidates(candidates: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for row in candidates or []:
        normalized = {
            "attachment": str(row.get("attachment") or "").strip(),
            "evidence_role": str(row.get("evidence_role") or "").strip(),
            "source_revision": str(row.get("source_revision") or "").strip(),
        }
        key = tuple(normalized.values())
        if not normalized["attachment"] or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def attach_evidence_file_urls(evidence_rows: list[dict]) -> list[dict]:
    """给已关联凭证行补上附件的文件地址，供前端直接预览原件。

    凭证行的 ``attachment`` 指向 ``Overseas Cost Attachment``，文件地址的真源在那里；
    这里只把这一个字段搬过来，不复制任何判定结果（来源是否可用、终核状态等仍以
    各自服务端的判定位为准）。
    """
    links = sorted({str(row.get("attachment") or "").strip() for row in evidence_rows or []} - {""})
    file_urls: dict[str, str] = {}
    if links:
        file_urls = {
            str(row.get("name") or ""): str(row.get("file_url") or "")
            for row in frappe.get_all(
                "Overseas Cost Attachment",
                filters={"name": ["in", links]},
                fields=["name", "file_url"],
                limit_page_length=len(links),
            )
        }
    for row in evidence_rows or []:
        row["file_url"] = file_urls.get(str(row.get("attachment") or "").strip(), "")
    return evidence_rows


def _now():
    if frappe is not None:
        try:
            return frappe.utils.now()
        except Exception:
            pass
    return datetime.now().isoformat(timespec="seconds")


def _session_user() -> str:
    return str(getattr(getattr(frappe, "session", None), "user", "") or "")


def _revision() -> str:
    if frappe is not None and hasattr(frappe, "generate_hash"):
        return str(frappe.generate_hash(length=20))
    return uuid.uuid4().hex[:20]


def _assert_write_context(batch_name: str, version_name: str | None, edit_token, expected_modified) -> None:
    edit_session_service.assert_batch_write(
        batch_name,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )
    if version_name:
        version_batch = frappe.db.get_value("Overseas Cost Version", version_name, "batch")
        if version_batch != batch_name:
            raise ValueError(_i18n("成本版本不属于当前批次。"))
    from overseas_costing.services.batch_service import _build_invalid_business_state

    batch = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_name,
        ["name", "source_approval_status", "extra_json"],
        as_dict=True,
    ) or {}
    invalid = _build_invalid_business_state(batch)
    if invalid.get("invalid"):
        raise ValueError(invalid.get("message") or "当前审批已失效，不能修改费用。")


def _rule_fields() -> list[str]:
    return [
        "name",
        "batch",
        "version",
        *FEE_FIELDS,
        "amount_revision",
        "scope_revision",
    ]


def _query_rules(batch_name: str, version_name: str) -> list[dict]:
    return frappe.get_all(
        "Overseas Cost Allocation Rule",
        filters={"batch": batch_name, "version": version_name},
        fields=_rule_fields(),
        order_by="priority_no asc, modified asc",
        limit_page_length=1000,
    )


def _has_valid_final_evidence(rule_name: str) -> bool:
    if frappe is None or not rule_name:
        return False
    return bool(
        frappe.db.get_value(
            "Overseas Cost Fee Evidence",
            {
                "fee_rule": rule_name,
                "validation_status": "VALID",
                "accounting_role": "FINAL_BILL",
                "is_final": 1,
            },
            "name",
        )
    )


def materialize_fee_rule(batch_name: str, version_name: str, logical_fee_key: str) -> dict:
    """Create a non-counting fee shell so evidence may be linked before an amount exists."""

    if frappe is None:
        return {"name": "DRY-FEE", "logical_fee_key": logical_fee_key, "amount_status": "MISSING"}
    mode = frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode") or ""
    existing = _decorate_historical_rules(_query_rules(batch_name, version_name), mode)
    active = next(
        (row for row in existing if row.get("logical_fee_key") == logical_fee_key and fee_is_active(row)),
        None,
    )
    if active:
        return active
    if any(row.get("logical_fee_key") == logical_fee_key for row in existing):
        raise ValueError(_i18n("该费用已停用，请先核对历史记录，不能通过关联凭证恢复启用。"))
    template = next(
        (row for row in [*build_default_fee_templates(mode), *build_other_fee_templates()]
         if row["logical_fee_key"] == logical_fee_key),
        None,
    )
    if not template:
        raise ValueError(_i18n("当前批次没有该逻辑费用项。"))
    values = {
        key: template.get(key)
        for key in FEE_FIELDS
        if key not in {"status_changed_by", "status_changed_at", "status_change_reason"}
    }
    values.update(
        {
            "doctype": "Overseas Cost Allocation Rule",
            "batch": batch_name,
            "version": version_name,
            "amount_status": "MISSING",
            "amount": None,
            "amount_revision": f"evidence-shell:{_revision()}",
            "scope_revision": f"system:{_revision()}",
        }
    )
    doc = frappe.get_doc(values).insert(ignore_permissions=True)
    return {**values, "name": doc.name, "virtual": False}


def save_fee(
    batch_name: str,
    version_name: str,
    fee_payload,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    if frappe is None:
        payload = normalize_fee_payload(fee_payload)
        merged = merge_logical_fee([], payload, revision="DRY-RUN")
        return {"ok": True, "dry_run": True, **merged}

    from overseas_costing.services.effective_source_values import batch_source_context
    context = batch_source_context(batch_name, version_name, lock=True)
    if context.get('root_kind') == 'expense' and not context.get('separate_adoption'):
        raise ValueError(_i18n('当前费用统一来自已匹配采购支出，请通过采购支出资料审核采用完整费用明细。'))
    _assert_write_context(batch_name, version_name, edit_token, expected_modified)
    transport_mode = frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode") or ""
    existing = _decorate_historical_rules(_query_rules(batch_name, version_name), transport_mode)
    raw_payload = _load_dict(fee_payload)
    from overseas_costing.services.logistics_settlement.fee_policy import assert_fee_edit_allowed
    assert_fee_edit_allowed(existing, raw_payload)
    previous = next(
        (
            row for row in existing
            if row.get("logical_fee_key") == raw_payload.get("logical_fee_key") and fee_is_active(row)
        ),
        {},
    )
    from overseas_costing.services.project_freight_service import policy_from_fee
    submitted_policy = policy_from_fee(raw_payload)
    if submitted_policy and submitted_policy != policy_from_fee(previous):
        raise ValueError(_i18n('不能通过浏览器修改服务器签发的项目分摊规则。'))
    payload = normalize_fee_payload(raw_payload, trusted_project_policy=bool(submitted_policy))
    transition = validate_fee_status_transition(
        previous.get("amount_status") or "MISSING",
        payload.get("amount_status"),
        reason=payload.get("status_change_reason") or payload.get("remark"),
        has_valid_final_evidence=_has_valid_final_evidence(str(previous.get("name") or "")),
    )
    if transition["previous_status"] != transition["next_status"]:
        payload.update(
            {
                "status_change_reason": transition["reason"],
                "status_changed_by": _session_user(),
                "status_changed_at": _now(),
            }
        )
    merged = merge_logical_fee(existing, payload, revision=_revision())
    fee = merged["fee"]
    if not str(fee.get("amount_revision") or "").startswith("manual:"):
        fee["amount_revision"] = f"manual:{_revision()}"
        merged["cost_inputs_changed"] = True
    values = {key: fee.get(key) for key in (*FEE_FIELDS, "amount_revision", "scope_revision")}
    values.update({"batch": batch_name, "version": version_name})
    if merged["action"] == "updated":
        rule_name = fee["name"]
        frappe.db.set_value("Overseas Cost Allocation Rule", rule_name, values, update_modified=True)
    else:
        rule_name = frappe.get_doc({"doctype": "Overseas Cost Allocation Rule", **values}).insert(
            ignore_permissions=True
        ).name
    if merged["cost_inputs_changed"]:
        frappe.db.set_value("Overseas Cost Batch", batch_name, "status", "Dirty", update_modified=True)
    if transition["previous_status"] != transition["next_status"]:
        from overseas_costing.services.calculate_service import _insert_audit_log

        _insert_audit_log(
            batch_doc_name=batch_name,
            version_name=version_name,
            action_type="EDIT",
            field_name=f"fee:{payload['logical_fee_key']}:amount_status",
            old_value=transition["previous_status"],
            new_value=transition["next_status"],
            action_remark=transition["reason"] or "费用状态人工调整",
        )
    frappe.db.commit()
    batch_modified = frappe.db.get_value("Overseas Cost Batch", batch_name, "modified")
    return {
        "ok": True,
        "action": merged["action"],
        "rule_name": rule_name,
        "fee": fee,
        "cost_inputs_changed": merged["cost_inputs_changed"],
        "batch_modified": batch_modified,
        "message": "费用已保存，原逻辑费用记录已替换更新。",
    }


def link_fee_evidence(
    batch_name: str,
    fee_rule: str,
    attachment: str,
    evidence_role: str,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "evidence": {
                "batch": batch_name,
                "fee_rule": fee_rule,
                "attachment": attachment,
                "evidence_role": evidence_role,
                "validation_status": "PENDING",
            },
        }
    rule = frappe.db.get_value(
        "Overseas Cost Allocation Rule",
        fee_rule,
        ["name", "batch", "version"],
        as_dict=True,
    ) or {}
    if rule.get("batch") != batch_name:
        raise ValueError(_i18n("费用规则不属于当前批次。"))
    _assert_write_context(batch_name, rule.get("version"), edit_token, expected_modified)
    attachment_row = frappe.db.get_value(
        "Overseas Cost Attachment",
        attachment,
        ["name", "batch", "version", "modified"],
        as_dict=True,
    ) or {}
    if attachment_row.get("batch") != batch_name:
        raise ValueError(_i18n("凭证附件不属于当前批次。"))
    role = str(evidence_role or "").strip()
    if not role:
        raise ValueError(_i18n("凭证角色不能为空。"))
    existing = frappe.db.get_value(
        "Overseas Cost Fee Evidence",
        {"fee_rule": fee_rule, "attachment": attachment, "evidence_role": role},
        "name",
    )
    if existing:
        return {
            "ok": True,
            "action": "existing",
            "evidence_name": existing,
            "batch_modified": frappe.db.get_value("Overseas Cost Batch", batch_name, "modified"),
            "message": "该凭证已关联，未重复新增。",
        }
    doc = frappe.get_doc(
        {
            "doctype": "Overseas Cost Fee Evidence",
            "batch": batch_name,
            "version": rule.get("version"),
            "fee_rule": fee_rule,
            "attachment": attachment,
            "evidence_role": role,
            "validation_status": "PENDING",
            "source_revision": str(attachment_row.get("modified") or ""),
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return {
        "ok": True,
        "action": "created",
        "evidence_name": doc.name,
        "batch_modified": frappe.db.get_value("Overseas Cost Batch", batch_name, "modified"),
        "message": "凭证已关联，等待校验。",
    }


def set_fee_evidence_status(
    batch_name: str,
    evidence_name: str,
    status: str,
    remark: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    normalized = str(status or "").strip().upper()
    if normalized not in EVIDENCE_STATUSES:
        raise ValueError(_i18n("费用凭证校验状态不合法。"))
    if frappe is None:
        return {"ok": True, "dry_run": True, "evidence_name": evidence_name, "status": normalized}
    row = frappe.db.get_value(
        "Overseas Cost Fee Evidence",
        evidence_name,
        ["name", "batch", "version", "attachment"],
        as_dict=True,
    ) or {}
    if row.get("batch") != batch_name:
        raise ValueError(_i18n("费用凭证不属于当前批次。"))
    _assert_write_context(batch_name, row.get("version"), edit_token, expected_modified)
    values = {
        "validation_status": normalized,
        "remark": str(remark or "").strip(),
        "validated_by": _session_user(),
        "validated_at": _now(),
    }
    frappe.db.set_value("Overseas Cost Fee Evidence", evidence_name, values, update_modified=True)
    frappe.db.commit()
    return {
        "ok": True,
        "evidence_name": evidence_name,
        "status": normalized,
        "batch_modified": frappe.db.get_value("Overseas Cost Batch", batch_name, "modified"),
        "message": "凭证状态已更新。",
    }


def unlink_fee_evidence_for_attachment(attachment: str, *, reason: str = "附件已删除。") -> dict:
    """Keep evidence history after its file record is removed."""

    if frappe is None:
        return {"ok": True, "dry_run": True, "affected_count": 0}
    rows = frappe.get_all(
        "Overseas Cost Fee Evidence",
        filters={"attachment": str(attachment or "")},
        fields=["name"],
        limit_page_length=5000,
    )
    for row in rows:
        frappe.db.set_value(
            "Overseas Cost Fee Evidence",
            row["name"],
            {
                "attachment": "",
                "validation_status": "UNLINKED",
                "source_revision": str(attachment or ""),
                "validated_by": _session_user(),
                "validated_at": _now(),
                "remark": str(reason or "").strip(),
            },
            update_modified=True,
        )
    return {"ok": True, "affected_count": len(rows)}


def get_fee_worklist(batch_name: str, version_name: str | None = None) -> dict:
    if frappe is None:
        rows = compose_fee_worklist_rows([], "SEA")
        statuses = []
        for row in rows:
            allocation = fee_allocation_service.allocate_fee(row, [])
            statuses.append(
                {
                    **row,
                    **fee_status_service.build_fee_status(
                        fee=row,
                        allocation=allocation,
                        evidence=[],
                    ),
                    "allocation": allocation,
                    "evidence": [],
                }
            )
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "transport_mode": "SEA",
            "other_fee_options": build_other_fee_templates(),
            "fees": statuses,
            "items": statuses,
            "summary": fee_status_service.summarize_fee_statuses(statuses),
            "evidence_candidates": [],
        }
    version = version_name or frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
    if not version:
        raise ValueError(_i18n("当前批次没有可用成本版本。"))
    if frappe.db.get_value("Overseas Cost Version", version, "batch") != batch_name:
        raise ValueError(_i18n("成本版本不属于当前批次。"))
    transport_mode = frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode") or ""
    from overseas_costing.services.effective_source_values import batch_source_context, project_source_values
    source_context = batch_source_context(batch_name,version)
    rules = compose_fee_worklist_rows(_query_rules(batch_name, version), transport_mode,source_context=source_context)
    raw_items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version, "is_excluded": 0},
        fields=[
            "name",
            "stable_line_key",
            "row_no",
            "material_code",
            "product_name",
            "unit",
            "purchase_uom",
            "unit_price_uom",
            "unit_price",
            "purchase_currency",
            "source_doc_no",
            "quantity",
            "actual_shipped_qty",
            "actual_shipped_qty_mode",
            "actual_shipped_qty_source_revision",
            "shipped_uom",
            "goods_value",
            "gross_weight_kg",
            "volume_m3",
            "volume_weight_kg",
            "chargeable_weight_kg",
            "project_collection",
            "extra_json",
        ],
        order_by="row_no asc, name asc",
        limit_page_length=10000,
    )
    from overseas_costing.services.material_input_service import present_material_row

    items = [present_material_row(project_source_values(row,source_context)) for row in raw_items]
    version_row = frappe.db.get_value(
        "Overseas Cost Version",
        version,
        ["fx_usd_to_rmb", "fx_rmb_to_mxn"],
        as_dict=True,
    ) or {}
    evidence_rows = frappe.get_all(
        "Overseas Cost Fee Evidence",
        filters={"batch": batch_name, "version": version},
        fields=[
            "name",
            "fee_rule",
            "attachment",
            "evidence_role",
            "validation_status",
            "source_revision",
            "remark",
            "evidence_type",
            "accounting_role",
            "currency",
            "original_amount",
            "direction",
            "related_evidence",
            "is_final",
            "attachment_fingerprint",
            "confirmed_by",
            "confirmed_at",
            "review_run",
        ],
        order_by="confirmed_at asc, creation asc",
        limit_page_length=5000,
    )
    evidence_by_rule: dict[str, list[dict]] = {}
    for row in attach_evidence_file_urls(evidence_rows):
        evidence_by_rule.setdefault(str(row.get("fee_rule") or ""), []).append(row)

    fx_context = {
        "fx_usd_to_rmb": version_row.get("fx_usd_to_rmb"),
        "fx_rmb_to_mxn": version_row.get("fx_rmb_to_mxn"),
    }
    statuses = []
    from overseas_costing.services.cost_preview_service import allocate_fee_in_rmb

    for rule in rules:
        if not fee_is_active(rule):
            continue
        allocation = allocate_fee_in_rmb(rule, items, fx_context)
        current_hash = fee_status_service.build_fee_input_hash(rule, items=items, fx_context=fx_context)
        evidence = evidence_by_rule.get(str(rule.get("name") or ""), [])
        status = fee_status_service.build_fee_status(
                fee=rule,
                allocation=allocation,
                evidence=evidence,
                calculation={"input_hash": current_hash},
        )
        from overseas_costing.services.fee_evidence_review_service import summarize_evidence_ledger

        statuses.append(
            {
                **rule,
                **status,
                "allocation": allocation,
                "evidence": evidence,
                "evidence_financials": summarize_evidence_ledger(
                    evidence,
                    current_amount=rule.get("amount"),
                    current_status=rule.get("amount_status") or "MISSING",
                ),
            }
        )

    attachments = frappe.get_all(
        "Overseas Cost Attachment",
        filters={"batch": batch_name},
        fields=[
            "name",
            "version",
            "source_type",
            "attachment_type",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
        ],
        order_by="modified desc",
        limit_page_length=1000,
    )
    summary = fee_status_service.summarize_fee_statuses(statuses)
    candidates = build_evidence_candidates(
        attachments, version_name=version, approval_detail=_fee_approval_detail(batch_name, version)
    )
    if source_context.get('root_kind') == 'expense' and not source_context.get('separate_adoption'):
        from overseas_costing.services.effective_logistics_source import current_source_bundle, attachment_allowed
        from overseas_costing.services.source_priority_service import is_selectable_source_stage
        bundle = current_source_bundle(batch_name, version)
        current_names = {row['name'] for row in attachments if bundle and attachment_allowed(row, bundle)}
        for candidate in candidates:
            # 采购、费用申请、国际物流三流程的资料都可被关联解析，且都保留金额
            # 候选（来源优先级只决定逐字段默认值）。这里只标注该候选是否落在当前
            # 采购支出范围内，供前端提示，不再整体降级为审计资料。
            candidate['in_current_source'] = candidate['attachment'] in current_names
            candidate['stage_selectable'] = is_selectable_source_stage(candidate.get('workflow_stage'))
        if not statuses or not source_context.get('approved') or source_context.get('invalid') or not source_context.get('available'):
            summary.update(all_requirements_satisfied=False, source_pending=True,
                           source_message='当前采购支出费用尚未有效采用，请先核对采购支出资料。')
    return {
        "ok": True,
        "batch_name": batch_name,
        "version_name": version,
        "transport_mode": transport_mode,
        "other_fee_options": build_other_fee_templates(),
        "fees": statuses,
        "items": statuses,
        "summary": summary,
        "evidence_candidates": candidates,
        "source_context": source_context,
    }
