"""Server-owned preview, claim, application, and correction for approved payments."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import uuid

from .freight_lines import logical_fee_key as inferred_fee_key, matching_lines
from .freight_matching import CANDIDATE_SCOPE_POLICY
from .jobs import utcnow
from .model import digest, dumps


POLICY = "payment-adoption-2"
AMOUNT_STATUSES = {"ACTUAL", "ESTIMATED", "MISSING", "NOT_INCURRED", "INCLUDED"}
CURRENCIES = {"RMB", "USD", "MXN"}
SELECTION_FIELDS = {"source_line_id", "logical_fee_key", "amount", "currency", "amount_status", "replace_claim_ids"}
KEY_DEFINITIONS = {
    "customs_clearance_fee": ("进口清关费", "customs", "purchase_value"),
    "import_tax": ("进口关税", "tax", "purchase_value"),
    "destination_delivery": ("墨西哥内陆配送费", "mexico_inland", "gross_weight"),
}


def _time(now=None):
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def _parse_time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal("1000000000000"):
            raise ValueError()
        if result != result.quantize(Decimal("0.000001")):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(_("金额须是有效数字，最多六位小数"))


def _text(value):
    value = Decimal(value)
    if not value:
        return "0"
    return format(value.normalize(), "f")


def _allowed_keys(batch):
    from overseas_costing.services.transport_fee_service import primary_freight_definition
    freight = primary_freight_definition(batch.get("transport_mode"))
    definitions = {**KEY_DEFINITIONS, freight["logical_fee_key"]: (
        freight["expense_category"], "freight", freight["allocation_basis"])}
    return definitions


def _editable(batch, version):
    if not batch or not version or version.get("name") != batch.get("current_version"):
        raise ValueError(_("成本版本已变化，请刷新"))
    if version.get("status") in {"Confirmed", "Archived"} or batch.get("confirm_status") == "Confirmed" or batch.get("writeback_status") == "Success":
        raise ValueError(_("历史、已确认或已回写版本不可修改"))


def _active_claims(store, **filters):
    return [row for row in store.find("payment_claim", **filters) if row.get("status") == "active"]


def _current_active_claims(store, ledger, **filters):
    current = []
    for row in _active_claims(store, **filters):
        owner = ledger.get("batch", row.get("batch")) if row.get("batch") else None
        if not owner or not row.get("version") or owner.get("current_version") == row.get("version"):
            current.append(row)
    return current


def _claim_signature(row):
    return {key: row.get(key) for key in (
        "id", "batch", "version", "source_id", "source_snapshot", "source_line_id",
        "logical_fee_key", "amount", "currency", "amount_status", "status", "exclusive", "revision",
    )}


def _balance_state(store, ledger, source):
    payment = store.find("payment_claim", source_id=source["id"])
    legacy = store.find("freight_claim", source_id=source["id"])
    active = _current_active_claims(store, ledger, source_id=source["id"])
    current = [row for row in active if row.get("source_snapshot") == source.get("snapshot")]
    stale = [row for row in active if row not in current]
    currencies = {str(row.get("currency") or "") for row in current + legacy if row.get("currency")}
    source_currency = str(source.get("currency") or "")
    claimed = sum((abs(_amount(row.get("amount"))) for row in current + legacy
                   if row.get("currency") == source_currency), Decimal("0"))
    total = abs(_amount(source.get("amount"))) if source.get("amount") is not None else None
    remaining = total - claimed if total is not None else None
    blocking = []
    if stale or any(row.get("source_snapshot") != source.get("snapshot") for row in legacy):
        blocking.append("已有旧快照认领，需先复核")
    if currencies - {source_currency}:
        blocking.append("已有混合币种认领，需先复核")
    fingerprint = digest("payment-balance", [_claim_signature(row) for row in payment],
                         [{key: row.get(key) for key in ("id", "source_snapshot", "amount", "currency", "charge_key")} for row in legacy])
    return {"claimed": claimed, "remaining": remaining, "active": current, "stale": stale,
            "blocking": blocking, "fingerprint": fingerprint}


def _rule_signature(ledger, batch_name, version_name, keys):
    rows = []
    for row in ledger.rows("rule", batch=batch_name, version=version_name):
        if row.get("logical_fee_key") in keys:
            rows.append({key: row.get(key) for key in ("name", "logical_fee_key", "amount", "currency", "amount_status",
                "source_binding_id", "source_snapshot", "is_final", "is_enabled", "is_active", "covered_scopes")})
    return digest("payment-rules", sorted(rows, key=lambda row: str(row.get("name"))))


def _resolve_context(store, ledger, batch_name, version_name, candidate_id, candidate_revision, *, lock=False):
    batch = ledger.get("batch", batch_name, lock=lock) or {}
    version = ledger.get("version", version_name, lock=lock) or {}
    _editable(batch, version)
    candidate = store.get("freight_candidate", candidate_id, lock=lock)
    if not candidate or candidate.get("revision") != candidate_revision or candidate.get("status") == "rejected":
        raise ValueError(_("付款候选已变化或被否决，请刷新"))
    if candidate.get("line_scope_policy") != CANDIDATE_SCOPE_POLICY:
        raise ValueError(_("付款候选范围策略已更新，请重新匹配"))
    if not store.find("batch_map", batch=batch_name, source_id=candidate["logistics_id"]):
        raise ValueError(_("付款候选不属于当前批次"))
    source = store.get("source", candidate["expense_id"], lock=lock) or {}
    logistics = store.get("source", candidate["logistics_id"], lock=lock) or {}
    if source.get("snapshot") != candidate.get("expense_snapshot") or logistics.get("snapshot") != candidate.get("logistics_snapshot"):
        raise ValueError(_("来源快照已变化，请重新核对"))
    if not source.get("approved") or source.get("invalid") or source.get("kind") != "expense" or logistics.get("invalid") or source.get("corp") != logistics.get("corp"):
        raise ValueError(_("付款来源未批准、已失效或企业不符"))
    current_lines = {}
    for line_id in candidate.get("line_ids") or []:
        line = store.get("freight_line", line_id, lock=lock)
        if line and line.get("source_id") == source.get("id") and line.get("snapshot") == source.get("snapshot"):
            current_lines[line_id] = line
    if candidate.get("line_ids") and set(current_lines) != set(candidate["line_ids"]):
        raise ValueError(_("来源明细已变化，请重新核对"))
    if not current_lines:
        if source.get("amount") is None or source.get("currency") not in CURRENCIES:
            raise ValueError(_("来源缺少可认领的审批总额或币种"))
        current_lines["approval_total"] = {
            "id": "approval_total", "source_id": source["id"], "source_snapshot": source["snapshot"],
            "line_key": "approval_total", "revision": digest(source["snapshot"], source.get("amount"), source.get("currency")),
            "waybill": "", "approval_no": "", "amount": source["amount"], "currency": source["currency"],
            "label": source.get("title") or "审批总额", "scope": "review", "charge_key": digest(source["id"], "approval_total"),
            "aggregate": True, "exclusive": False,
        }
    return batch, version, candidate, source, logistics, current_lines


def _selection_plan(store, ledger, batch, version, candidate, source, logistics, lines, selections, *, reason, negative_confirmed):
    if not isinstance(selections, list) or not selections or len(selections) > 200:
        raise ValueError(_("请选择 1 至 200 条付款金额"))
    definitions = _allowed_keys(batch)
    balance = _balance_state(store, ledger, source)
    selected = []
    replace_ids = set()
    positive = Decimal("0")
    for index, raw in enumerate(selections):
        if not isinstance(raw, dict) or set(raw) - SELECTION_FIELDS:
            raise ValueError(_("付款选择包含不允许的来源凭据字段"))
        line = lines.get(str(raw.get("source_line_id") or ""))
        if not line:
            raise ValueError(_("付款明细不属于当前候选"))
        if line.get("ambiguous"):
            raise ValueError(_("付款明细身份重复，需先复核"))
        if line.get("waybill") or line.get("approval_no"):
            verified = matching_lines(logistics, [line])
            if not verified or verified[0].get("identifier_conflict"):
                raise ValueError(_("付款明细标识与本票不符"))
        key = str(raw.get("logical_fee_key") or "")
        if not key:
            key = inferred_fee_key(line.get("scope"), batch.get("transport_mode")) or ""
        if not key:
            raise ValueError(_("该明细需人工选择费用分类"))
        if key not in definitions:
            raise ValueError(_("费用分类不在当前运输方式允许范围"))
        if line.get("scope") not in (None, "", "review"):
            expected = inferred_fee_key(line.get("scope"), batch.get("transport_mode"))
            if key != expected:
                raise ValueError(_("来源明细费用分类已明确，不能改分类"))
        value = _amount(raw.get("amount"))
        currency = str(raw.get("currency") or line.get("currency") or "").upper()
        if currency not in CURRENCIES:
            raise ValueError(_("仅支持 RMB/USD/MXN 原币"))
        aggregate_line = (line.get("id") == "approval_total" and line.get("line_key") == "approval_total"
                          and line.get("aggregate") is True)
        exclusive = not aggregate_line
        replacements = raw.get("replace_claim_ids") or []
        if not isinstance(replacements, list) or any(not isinstance(value, str) for value in replacements):
            raise ValueError(_("待替换认领参数格式错误"))
        if exclusive and (value != _amount(line.get("amount")) or currency != line.get("currency")):
            raise ValueError(_("结构化付款明细的金额和币种必须与来源明细一致"))
        if exclusive:
            legacy_occupied = store.find("freight_claim", charge_key=line.get("charge_key"))
            payment_occupied = [claim for claim in _current_active_claims(store, ledger, source_id=source["id"])
                                if claim.get("source_line_id") == line["id"] and claim["id"] not in replacements]
            if legacy_occupied or payment_occupied:
                raise ValueError(_("这条结构化付款明细已采用，不能重复采用"))
        if not exclusive and currency != source.get("currency"):
            raise ValueError(_("首次认领必须使用审批原币"))
        if value < 0 and not negative_confirmed:
            raise ValueError(_("请明确确认负数冲抵费用"))
        amount_status = str(raw.get("amount_status") or "ACTUAL").upper()
        if amount_status not in AMOUNT_STATUSES:
            raise ValueError(_("费用金额状态不受支持"))
        replace_ids.update(replacements)
        positive += abs(value)
        selected.append({"selection_index": index, "source_line_id": line["id"], "line_key": line["line_key"],
            "line_revision": line["revision"], "logical_fee_key": key, "amount": _text(value), "currency": currency,
            "amount_status": amount_status, "exclusive": exclusive, "charge_key": line.get("charge_key"),
            "evidence_document_id": str((line.get("evidence") or {}).get("document_id") or ""),
            "label": line.get("label"), "waybill": line.get("waybill") or "", "replace_claim_ids": replacements})
    if len({(row["source_line_id"], row["logical_fee_key"]) for row in selected}) != len(selected):
        raise ValueError(_("同一来源明细不能重复选择同一费用分类"))
    for key in {row["logical_fee_key"] for row in selected}:
        grouped = [row for row in selected if row["logical_fee_key"] == key]
        if len({row["currency"] for row in grouped}) > 1:
            raise ValueError(_("同一费用分类的多条来源明细币种必须一致"))
        if len({row["amount_status"] for row in grouped}) > 1:
            raise ValueError(_("同一费用分类的多条来源明细金额状态必须一致"))
    selected_currencies = {row["currency"] for row in selected}
    source_currency = str(source.get("currency") or "").upper()
    if len(selected_currencies) != 1 or source_currency and selected_currencies != {source_currency}:
        raise ValueError(_("同一审批候选的全部费用必须使用审批原币，当前不支持混合币种额度"))
    replace_claims = {row["id"]: row for row in _active_claims(
        store, batch=batch["name"], version=version["name"])}
    if replace_ids - set(replace_claims):
        raise ValueError(_("待替换认领不属于当前批次"))
    if replace_ids and not str(reason or "").strip():
        raise ValueError(_("替换已有实际费用必须填写原因"))
    selected_keys = {row["logical_fee_key"] for row in selected}
    freight_keys = {key for key, (_label, scope, _basis) in definitions.items() if scope == "freight"}
    if selected_keys & freight_keys and store.find("freight_claim", batch=batch["name"]):
        raise ValueError(_("新旧运费认领不能混用，请先在统一付款列表更正或撤销旧认领"))
    existing = [row for row in _active_claims(store, batch=batch["name"], version=version["name"])
                if row.get("logical_fee_key") in selected_keys]
    required = {row["id"] for row in existing}
    if required - replace_ids:
        raise ValueError(_("已有实际费用认领，请明确选择替换项并填写原因"))
    if any(replace_claims[cid].get("logical_fee_key") not in selected_keys for cid in replace_ids):
        raise ValueError(_("只能替换与本次选择相同费用分类的认领"))
    active_rules = [row for row in ledger.rows("rule", batch=batch["name"], version=version["name"])
                    if row.get("logical_fee_key") in selected_keys
                    and row.get("is_enabled") not in (0, False, "0") and row.get("is_active") not in (0, False, "0")]
    actual_rules = [row for row in active_rules if row.get("amount_status") == "ACTUAL" or row.get("is_final") in (1, True, "1")]
    replace_bindings = replace_ids | {row.get("rule_binding_id") for row in replace_claims.values() if row.get("rule_binding_id")}
    if any(not row.get("source_binding_id") or row.get("source_binding_id") not in replace_bindings for row in actual_rules):
        raise ValueError(_("目标分类已有实际费用规则，需先选择可替换认领并填写原因"))
    replace_rule_names = [row["name"] for row in active_rules if row.get("source_binding_id") not in replace_bindings]
    released = sum((abs(_amount(replace_claims[cid]["amount"])) for cid in replace_ids
                    if replace_claims[cid].get("source_id") == source["id"]
                    and replace_claims[cid].get("source_snapshot") == source["snapshot"]
                    and replace_claims[cid].get("currency") == source.get("currency")), Decimal("0"))
    remaining = None if balance["remaining"] is None else balance["remaining"] + released
    if remaining is not None and positive > remaining:
        raise ValueError(_("本次认领超过审批来源剩余金额"))
    return selected, replace_claims, balance, definitions, remaining, replace_rule_names


def preview_payment_adoption(store, ledger, batch_name, version_name, candidate_id, candidate_revision,
                             selections, actor, *, reason="", negative_confirmed=False, now=None,
                             attachment_selections=None):
    batch, version, candidate, source, logistics, lines = _resolve_context(
        store, ledger, batch_name, version_name, candidate_id, candidate_revision)
    selected, replace_claims, balance, definitions, remaining, replace_rule_names = _selection_plan(
        store, ledger, batch, version, candidate, source, logistics, lines, selections,
        reason=reason, negative_confirmed=bool(negative_confirmed))
    from .payment_evidence import plan_attachment_selections
    attachment_plan = plan_attachment_selections(source, lines, selected, attachment_selections)
    keys = {row["logical_fee_key"] for row in selected}
    dependencies = {
        "batch_modified": str(batch.get("modified") or ""), "current_version": batch.get("current_version"),
        "candidate_revision": candidate["revision"], "source_snapshot": source["snapshot"],
        "logistics_snapshot": logistics["snapshot"],
        "line_revisions": sorted((row["source_line_id"], row["line_revision"]) for row in selected),
        "claim_fingerprint": balance["fingerprint"],
        "rule_fingerprint": _rule_signature(ledger, batch_name, version_name, keys),
        "attachment_fingerprints": attachment_plan["dependency"],
    }
    created = _time(now); expires = created + timedelta(minutes=30)
    preview_id = uuid.uuid4().hex
    private = {"id": preview_id, "policy": POLICY, "batch": batch_name, "version": version_name, "logistics_id": logistics["id"],
        "source_id": source["id"], "candidate_id": candidate["id"], "candidate_revision": candidate["revision"],
        "source_snapshot": source["snapshot"], "logistics_snapshot": logistics["snapshot"], "selections": selected,
        "attachment_plan": attachment_plan,
        "replace_claim_ids": sorted({cid for row in selected for cid in row["replace_claim_ids"]}),
        "replace_rule_names": replace_rule_names,
        "dependencies": dependencies, "status": "pending", "created_at": created.isoformat(),
        "expires_at": expires.isoformat(), "reason": str(reason or "").strip(), "negative_confirmed": bool(negative_confirmed),
        "actor": actor, "source_total": (_text(_amount(source["amount"])) if source.get("amount") is not None else None),
        "source_currency": source.get("currency") or (selected[0]["currency"] if len({row["currency"] for row in selected}) == 1 else None)}
    private["revision"] = digest(POLICY, preview_id, private)
    store.insert("payment_preview", {key: private[key] for key in ("id", "batch", "version", "logistics_id", "source_id", "status", "revision", "expires_at")} | {"data": dumps(private)})
    impacts = [{"logical_fee_key": row["logical_fee_key"], "expense_category": definitions[row["logical_fee_key"]][0],
        "amount": row["amount"], "currency": row["currency"], "amount_status": row["amount_status"],
        "covered_scope": definitions[row["logical_fee_key"]][1]} for row in selected]
    return {"preview_id": preview_id, "revision": private["revision"], "expires_at": private["expires_at"],
        "source": {"approval_no": source.get("approval_no"), "title": source.get("title")},
        "source_total": private["source_total"], "source_currency": private["source_currency"],
        "active_claimed": _text(balance["claimed"]), "stale_claimed": bool(balance["stale"]),
        "remaining": _text(remaining) if remaining is not None else None, "impacted_fees": impacts,
        "replace_claim_ids": private["replace_claim_ids"],
        "attachments": attachment_plan["public"],
        "warnings": sorted(set(list(balance["blocking"]) + attachment_plan["blockers"])),
        "replace_rules": replace_rule_names,
        "payment_blocking_reasons": sorted(set(list(balance["blocking"]) + attachment_plan["blockers"]))}


def _validate_preview(store, ledger, preview, now):
    if preview.get("status") == "applied":
        return "applied"
    if preview.get("policy") != POLICY:
        raise ValueError(_("付款预览策略已更新，请重新预览"))
    if _time(now) > _parse_time(preview["expires_at"]):
        raise ValueError(_("付款预览已过期，请重新预览"))
    batch = ledger.get("batch", preview["batch"], lock=True) or {}
    if str(batch.get("modified") or "") != preview["dependencies"]["batch_modified"]:
        raise ValueError(_("批次数据已变化，请重新预览"))
    if batch.get("current_version") != preview["dependencies"]["current_version"]:
        raise ValueError(_("当前成本版本已变化，请重新预览"))
    version = ledger.get("version", preview["version"], lock=True) or {}
    _editable(batch, version)
    candidate = store.get("freight_candidate", preview["candidate_id"], lock=True) or {}
    if candidate.get("revision") != preview["dependencies"]["candidate_revision"] or candidate.get("status") == "rejected":
        raise ValueError(_("付款候选已变化，请重新预览"))
    if candidate.get("line_scope_policy") != CANDIDATE_SCOPE_POLICY:
        raise ValueError(_("付款候选范围策略已更新，请重新匹配"))
    source = store.get("source", preview["source_id"], lock=True) or {}
    logistics = store.get("source", preview["logistics_id"], lock=True) or {}
    if source.get("snapshot") != preview["dependencies"]["source_snapshot"] or logistics.get("snapshot") != preview["dependencies"]["logistics_snapshot"]:
        raise ValueError(_("付款来源快照已变化，请重新预览"))
    if (not source.get("approved") or source.get("invalid") or source.get("kind") != "expense"
            or logistics.get("invalid") or logistics.get("kind") != "logistics"
            or source.get("corp") != logistics.get("corp")
            or candidate.get("expense_id") != source.get("id")
            or candidate.get("logistics_id") != logistics.get("id")):
        raise ValueError(_("付款来源未批准、已失效、类型或企业已变化，请重新预览"))
    current_line_revisions = []
    for line_id, _revision in preview["dependencies"]["line_revisions"]:
        if line_id == "approval_total":
            current = digest(source["snapshot"], source.get("amount"), source.get("currency"))
        else:
            line = store.get("freight_line", line_id, lock=True) or {}
            current = line.get("revision")
        current_line_revisions.append((line_id, current))
    if sorted(current_line_revisions) != [tuple(row) for row in preview["dependencies"]["line_revisions"]]:
        raise ValueError(_("付款明细已变化，请重新预览"))
    balance = _balance_state(store, ledger, source)
    if balance["fingerprint"] != preview["dependencies"]["claim_fingerprint"]:
        raise ValueError(_("付款认领余额已变化，请重新预览"))
    if balance["blocking"]:
        raise ValueError(balance["blocking"][0])
    from .payment_evidence import validate_attachment_dependencies
    validate_attachment_dependencies(source, preview.get("attachment_plan") or {
        "dependency": preview.get("dependencies", {}).get("attachment_fingerprints") or [],
        "selected": [], "blockers": [],
    })
    keys = {row["logical_fee_key"] for row in preview["selections"]}
    if _rule_signature(ledger, preview["batch"], preview["version"], keys) != preview["dependencies"]["rule_fingerprint"]:
        raise ValueError(_("当前费用规则已变化，请重新预览"))
    return batch, version, candidate, source, logistics, balance


def _rule_for_claim(claim, definitions):
    label, scope, basis = definitions[claim["logical_fee_key"]]
    return {"rule_code": "payment_" + claim["id"][:24], "logical_fee_key": claim["logical_fee_key"],
        "expense_category": label, "amount": claim["amount"], "currency": claim["currency"],
        "amount_status": claim["amount_status"], "source_binding_id": claim["id"],
        "source_snapshot": claim["source_snapshot"], "covered_scopes": scope,
        "is_final": 1 if claim["amount_status"] == "ACTUAL" else 0, "is_enabled": 1, "is_active": 1,
        "scope_type": "ALL_ITEMS", "allocation_basis": basis, "basis_field": basis,
        "remark": "已核对审批付款来源"}


def _rule_binding_id(batch_name, version_name, key, claims):
    if len(claims) == 1:
        return claims[0]["id"]
    return digest(POLICY, "rule-group", batch_name, version_name, key)


def _sync_rule_groups(store, ledger, batch_name, version_name, keys, definitions, actor, reason):
    """Keep one active payment-owned rule per logical key while claims retain line-level audit."""
    for key in sorted(set(keys)):
        claims = [row for row in _active_claims(store, batch=batch_name, version=version_name)
                  if row.get("logical_fee_key") == key]
        currencies = {row.get("currency") for row in claims}
        statuses = {row.get("amount_status") or "ACTUAL" for row in claims}
        if len(currencies) > 1:
            raise ValueError(_("同一费用分类的有效认领币种不一致，请先复核"))
        if len(statuses) > 1:
            raise ValueError(_("同一费用分类的有效认领金额状态不一致，请先复核"))
        for rule in ledger.rows("rule", batch=batch_name, version=version_name):
            if (rule.get("logical_fee_key") == key and str(rule.get("rule_code") or "").startswith("payment_")
                    and rule.get("is_active") not in (0, False, "0")):
                ledger.put("rule", rule["name"], {"is_enabled": 0, "is_active": 0, "is_final": 0,
                    "status_change_reason": reason, "status_changed_by": actor, "status_changed_at": utcnow()})
        if not claims:
            continue
        binding_id = _rule_binding_id(batch_name, version_name, key, claims)
        for claim in claims:
            if claim.get("rule_binding_id") != binding_id:
                claim["rule_binding_id"] = binding_id
                _store_claim(store, claim)
        aggregate = {**claims[0], "id": binding_id, "rule_binding_id": binding_id,
            "amount": _text(sum((_amount(row["amount"]) for row in claims), Decimal("0"))),
            "amount_status": next(iter(statuses)), "currency": next(iter(currencies)),
            "source_snapshot": (claims[0]["source_snapshot"] if len({row["source_snapshot"] for row in claims}) == 1
                                else digest(POLICY, "source-group", sorted(row["source_snapshot"] for row in claims)))}
        rule = _rule_for_claim(aggregate, definitions)
        rule["source_binding_id"] = binding_id
        rule["rule_code"] = "payment_" + binding_id[:24]
        ledger.create("rule", {**rule, "batch": batch_name, "version": version_name,
            "status_change_reason": reason, "status_changed_by": actor, "status_changed_at": utcnow()})


def _store_claim(store, claim):
    keys = ("id", "batch", "version", "logistics_id", "source_id", "source_snapshot", "source_line_id",
        "logical_fee_key", "amount", "currency", "status", "exclusive", "claim_key", "revision")
    store.put("payment_claim", {key: claim[key] for key in keys} | {"data": dumps(claim)})


def _disable_claim_rule(ledger, batch, version, claim_id, actor, reason):
    for rule in ledger.rows("rule", batch=batch, version=version):
        if rule.get("source_binding_id") == claim_id and rule.get("is_active") not in (0, False, "0"):
            ledger.put("rule", rule["name"], {"is_enabled": 0, "is_active": 0, "is_final": 0,
                "status_change_reason": reason, "status_changed_by": actor, "status_changed_at": utcnow()})


def _payment_evidence_bindings(store, ledger, batch_name, version_name, application_claims):
    """Resolve the current claim/rule authority used by deferred evidence retries."""
    requested_keys = set()
    for expected in application_claims:
        claim = store.get("payment_claim", expected.get("id") or "", lock=True) or {}
        if (claim.get("status") != "active" or claim.get("batch") != batch_name
                or claim.get("version") != version_name):
            raise ValueError(_("付款认领已变化，不能重试附件"))
        requested_keys.add(claim.get("logical_fee_key"))
    grouped = {key: [row for row in _active_claims(store, batch=batch_name, version=version_name)
                     if row.get("logical_fee_key") == key]
               for key in requested_keys}
    result = {}
    for key, claims in grouped.items():
        rules = [row for row in ledger.rows("rule", batch=batch_name, version=version_name)
                 if row.get("logical_fee_key") == key
                 and str(row.get("rule_code") or "").startswith("payment_")
                 and row.get("is_active") not in (0, False, "0")
                 and row.get("is_enabled") not in (0, False, "0")]
        bindings = {claim.get("rule_binding_id") or claim.get("id") for claim in claims}
        if len(rules) != 1 or len(bindings) != 1 or rules[0].get("source_binding_id") not in bindings:
            raise ValueError(_("付款费用规则与认领绑定已变化，不能重试附件"))
        rule = rules[0]
        result[key] = {
            "claim_ids": sorted(claim["id"] for claim in claims),
            "claim_fingerprint": digest("payment-evidence-claims-1", sorted(
                [_claim_signature(claim) | {"rule_binding_id": claim.get("rule_binding_id")} for claim in claims],
                key=lambda row: str(row.get("id")))),
            "rule_name": rule["name"], "rule_binding_id": rule.get("source_binding_id"),
            "rule_fingerprint": digest("payment-evidence-rule-1", {field: rule.get(field) for field in (
                "name", "logical_fee_key", "amount", "currency", "amount_status", "source_binding_id",
                "source_snapshot", "is_final", "is_enabled", "is_active", "covered_scopes",
            )}),
        }
    return result


def _payment_evidence_rows(claims):
    return [{
        "selection_index": index, "source_line_id": claim.get("source_line_id"),
        "line_revision": claim.get("source_line_revision"), "logical_fee_key": claim.get("logical_fee_key"),
        "amount": claim.get("amount"), "currency": claim.get("currency"),
        "amount_status": claim.get("amount_status"), "evidence_document_id": claim.get("evidence_document_id") or "",
    } for index, claim in enumerate(sorted(claims, key=lambda row: str(row.get("id"))))]


def _refresh_archive_only_payment_source(store, ledger, batch_name, version_name, source, bindings,
                                         application, actor):
    """Adopt a verified archive-only source snapshot without relaxing normal stale checks.

    The caller must first validate the persisted pending document's business, content,
    identity and source-relation fingerprints. This migration is private to evidence retry.
    """
    affected_ids = {claim_id for binding in bindings.values()
                    for claim_id in (binding.get("claim_ids") or [])}
    current_claims = [store.get("payment_claim", claim_id, lock=True) for claim_id in sorted(affected_ids)]
    source_claims = [claim for claim in _active_claims(store, batch=batch_name, version=version_name)
                     if claim.get("source_id") == source.get("id")]
    if not source_claims or all(claim.get("source_snapshot") == source.get("snapshot")
                                for claim in source_claims):
        return bindings, _payment_evidence_rows([claim for claim in current_claims if claim])

    affected_keys = set(bindings) | {claim.get("logical_fee_key") for claim in source_claims}
    changed_claim_ids = set()
    for claim in source_claims:
        claim["source_snapshot"] = source["snapshot"]
        claim["revision"] = digest(POLICY, claim.get("revision"), "archive-source-refresh", source["snapshot"])
        _store_claim(store, claim)
        changed_claim_ids.add(claim["id"])

    # Membership is unchanged, so update rules in place and preserve any existing
    # evidence links while moving the current source authority.
    for key in sorted(affected_keys):
        claims = [row for row in _active_claims(store, batch=batch_name, version=version_name)
                  if row.get("logical_fee_key") == key]
        binding_id = _rule_binding_id(batch_name, version_name, key, claims)
        for claim in claims:
            if claim.get("rule_binding_id") != binding_id:
                claim["rule_binding_id"] = binding_id
                _store_claim(store, claim)
        rules = [row for row in ledger.rows("rule", batch=batch_name, version=version_name)
                 if row.get("logical_fee_key") == key
                 and str(row.get("rule_code") or "").startswith("payment_")
                 and row.get("is_active") not in (0, False, "0")
                 and row.get("is_enabled") not in (0, False, "0")]
        if len(rules) != 1:
            raise ValueError(_("付款费用规则与认领绑定已变化，不能重试附件"))
        snapshots = {claim.get("source_snapshot") for claim in claims}
        rule_snapshot = (next(iter(snapshots)) if len(snapshots) == 1
                         else digest(POLICY, "source-group", sorted(snapshots)))
        ledger.put("rule", rules[0]["name"], {
            "source_binding_id": binding_id, "rule_code": "payment_" + binding_id[:24],
            "source_snapshot": rule_snapshot, "status_change_reason": "付款附件归档快照刷新",
            "status_changed_by": actor, "status_changed_at": utcnow(),
        })

    refreshed_claims = [row for row in _active_claims(store, batch=batch_name, version=version_name)
                        if row.get("logical_fee_key") in affected_keys]
    refreshed_bindings = _payment_evidence_bindings(
        store, ledger, batch_name, version_name, [{"id": row["id"]} for row in refreshed_claims])
    selected_rows = _payment_evidence_rows(refreshed_claims)

    from .payment_evidence import refresh_pending_evidence_groups
    refresh_pending_evidence_groups(
        store, batch_name, version_name, refreshed_bindings, selected_rows, actor,
        "付款附件归档快照刷新", source=source)

    # Update embedded current-claim projections in every affected application.
    current_rows = {row["id"]: row for row in _active_claims(
        store, batch=batch_name, version=version_name)}
    for current in store.find("payment_application", batch=batch_name, version=version_name, status="applied"):
        result = current.get("result") or {}
        embedded = result.get("claims") or []
        if not any(row.get("id") in changed_claim_ids for row in embedded):
            continue
        result["claims"] = [current_rows.get(row.get("id"), row) for row in embedded]
        result["revision"] = digest(POLICY, batch_name, version_name,
                                    [_claim_signature(row) for row in current_rows.values()])
        current.update(result=result, source_snapshot=source["snapshot"],
                       source_snapshot_revision=digest("payment-application-source-1", source["snapshot"],
                                                       sorted(changed_claim_ids)))
        store.put("payment_application", {key: current[key] for key in (
            "id", "batch", "version", "preview_id", "status", "revision"
        )} | {"data": dumps(current)})
        if current.get("id") == application.get("id"):
            application.clear()
            application.update(current)
    return refreshed_bindings, selected_rows


def confirm_payment_adoption(store, ledger, batch_name, preview_id, revision, actor, *, now=None,
                             lease_check=None, edit_token=None, expected_modified=None, register_file=None):
    with store.atomic():
        store.get("state", "match_lock", lock=True)
        batch = ledger.get("batch", batch_name, lock=True) or {}
        preview = store.get("payment_preview", preview_id, lock=True)
        if not preview or preview.get("batch") != batch_name or preview.get("revision") != revision:
            raise ValueError(_("付款预览不属于当前批次或已变化"))
        if preview.get("actor") != actor and preview.get("status") != "applied":
            raise ValueError(_("付款预览属于其他操作人，请当前用户重新预览"))
        if preview.get("status") == "applied":
            application = store.get("payment_application", preview.get("application_id") or "")
            if application:
                pending_rows = [row for row in store.find(
                    "payment_evidence_pending", batch=batch_name, version=preview["version"], status="pending")
                    if row.get("preview_id") == preview.get("id") or row.get("application_id") == application.get("id")]
                if pending_rows:
                    if preview.get("actor") != actor and not lease_check:
                        raise ValueError(_("接续归档付款附件需要有效的编辑租约"))
                    if lease_check:
                        lease_check(batch_name, edit_token=edit_token, expected_modified=expected_modified)
                    current_batch = ledger.get("batch", batch_name, lock=True) or {}
                    current_version = ledger.get("version", preview["version"], lock=True) or {}
                    _editable(current_batch, current_version)
                    source = store.get("source", preview["source_id"], lock=True) or {}
                    logistics = store.get("source", preview["logistics_id"], lock=True) or {}
                    if (not source.get("approved") or source.get("invalid") or source.get("kind") != "expense"
                            or logistics.get("invalid") or logistics.get("kind") != "logistics"
                            or source.get("corp") != logistics.get("corp")):
                        raise ValueError(_("付款来源未批准、已失效、类型或企业已变化"))
                    from .document_writer import register_private_file
                    from .payment_evidence import materialize_payment_evidence, validate_pending_evidence_retry
                    seed_claim_ids = sorted({claim_id for row in pending_rows
                                             for claim_id in (row.get("claim_ids") or [])})
                    retry_claims = [{"id": claim_id} for claim_id in seed_claim_ids]
                    bindings = _payment_evidence_bindings(
                        store, ledger, batch_name, current_version["name"], retry_claims)
                    claim_ids = sorted({claim_id for binding in bindings.values()
                                        for claim_id in (binding.get("claim_ids") or [])})
                    selected_rows = _payment_evidence_rows([
                        store.get("payment_claim", claim_id, lock=True) for claim_id in claim_ids])
                    retry_plan = validate_pending_evidence_retry(
                        store, source, current_batch, current_version["name"], preview, application, bindings,
                        selected_rows)
                    bindings, selected_rows = _refresh_archive_only_payment_source(
                        store, ledger, batch_name, current_version["name"], source, bindings,
                        application, actor)
                    retry_register = register_file or (
                        lambda attachment, document: register_private_file(ledger, attachment, document))
                    attachments = materialize_payment_evidence(
                        store, ledger, source, current_batch, current_version["name"],
                        retry_plan, selected_rows, actor, retry_register,
                        pending_context={"preview_id": preview["id"], "application_id": application["id"],
                                         "bindings": bindings},
                    )
                    attachment_results = {row["document_id"]: row for row in application["result"].get("attachments") or []}
                    attachment_results.update({row["document_id"]: row for row in attachments})
                    application["result"] = {**application["result"],
                                             "attachments": list(attachment_results.values())}
                    store.put("payment_application", {key: application[key] for key in (
                        "id", "batch", "version", "preview_id", "status", "revision"
                    )} | {"data": dumps(application)})
                    store.audit(batch_name, "payment_evidence_retried", actor, preview_id=preview_id,
                                application_id=application["id"], document_ids=sorted(
                                    row["document_id"] for row in attachments),
                                statuses={row["document_id"]: row["status"] for row in attachments})
                return {**application["result"], "cached": True}
            raise ValueError(_("付款预览已处理，请刷新"))
        if lease_check:
            lease_check(batch_name, edit_token=edit_token, expected_modified=expected_modified)
        _batch, version, _candidate, source, _logistics, balance = _validate_preview(store, ledger, preview, now)
        definitions = _allowed_keys(batch)
        replace_ids = set(preview["replace_claim_ids"])
        replace_claims = {row["id"]: row for row in _active_claims(
            store, batch=batch_name, version=version["name"]) if row["id"] in replace_ids}
        if set(replace_claims) != replace_ids:
            raise ValueError(_("待替换付款认领已变化，请重新预览"))
        released = sum((abs(_amount(row["amount"])) for row in replace_claims.values()
                        if row.get("source_id") == source["id"] and row.get("source_snapshot") == source["snapshot"]
                        and row.get("currency") == source.get("currency")), Decimal("0"))
        positive = sum((abs(_amount(row["amount"])) for row in preview["selections"]), Decimal("0"))
        if balance["remaining"] is not None and positive > balance["remaining"] + released:
            raise ValueError(_("付款认领余额已不足，请重新预览"))
        for old in replace_claims.values():
            changed = deepcopy(old)
            changed.update(status="replaced", active=False, replaced_at=utcnow(), replaced_by=actor,
                           replacement_reason=preview["reason"], original_claim_key=old["claim_key"],
                           claim_key=digest("released-payment-claim", old["id"], preview_id),
                           revision=digest(old["revision"], "replaced", preview_id))
            _store_claim(store, changed)
            _disable_claim_rule(ledger, batch_name, version["name"], old["id"], actor, preview["reason"])
        selected_keys = {row["logical_fee_key"] for row in preview["selections"]}
        for rule_name in preview.get("replace_rule_names") or []:
            rule = ledger.get("rule", rule_name)
            if not rule or rule.get("logical_fee_key") not in selected_keys:
                raise ValueError(_("待替换费用规则已变化，请重新预览"))
            ledger.put("rule", rule_name, {"is_enabled": 0, "is_active": 0, "is_final": 0,
                "status_change_reason": "采用已核对付款金额", "status_changed_by": actor, "status_changed_at": utcnow()})
        claims = []
        for row in preview["selections"]:
            claim_id = digest(POLICY, preview_id, row["selection_index"])
            claim_key = ("exclusive:" + digest(source["id"], row["source_line_id"])
                         if row["exclusive"] else "aggregate:" + digest(source["id"], batch_name, claim_id))
            claim = {"id": claim_id, "batch": batch_name, "version": version["name"],
                "logistics_id": preview["logistics_id"], "logistics_snapshot": preview["logistics_snapshot"],
                "source_id": source["id"], "source_snapshot": source["snapshot"], "source_line_id": row["source_line_id"],
                "source_line_revision": row["line_revision"], "logical_fee_key": row["logical_fee_key"],
                "evidence_document_id": row.get("evidence_document_id") or "",
                "amount": row["amount"], "currency": row["currency"], "amount_status": row["amount_status"],
                "status": "active", "active": True, "exclusive": 1 if row["exclusive"] else 0,
                "claim_key": claim_key, "revision": digest(POLICY, claim_id, row, source["snapshot"]),
                "approval_no": source.get("approval_no") or "", "source_title": source.get("title") or "",
                "waybill": row["waybill"], "actor": actor, "adopted_at": utcnow(), "preview_id": preview_id}
            occupied = _current_active_claims(store, ledger, source_id=source["id"])
            legacy_occupied = store.find("freight_claim", charge_key=row.get("charge_key")) if row["exclusive"] else []
            if row["exclusive"] and (legacy_occupied or any(other.get("source_line_id") == row["source_line_id"] for other in occupied)):
                raise ValueError(_("这条结构化付款明细已采用于其他批次，不能重复采用"))
            _store_claim(store, claim)
            claims.append(claim)
        _sync_rule_groups(store, ledger, batch_name, version["name"], selected_keys, definitions, actor, "采用已核对付款金额")
        claims = [store.get("payment_claim", claim["id"]) for claim in claims]
        from .document_writer import register_private_file
        from .payment_evidence import materialize_payment_evidence
        register_file = register_file or (lambda attachment, document: register_private_file(ledger, attachment, document))
        application_id = digest(POLICY, "application", preview_id, revision)
        evidence_bindings = _payment_evidence_bindings(store, ledger, batch_name, version["name"], claims)
        attachments = materialize_payment_evidence(
            store, ledger, source, batch, version["name"], preview.get("attachment_plan") or {},
            preview["selections"], actor, register_file,
            pending_context={"preview_id": preview_id, "application_id": application_id,
                             "bindings": evidence_bindings},
        )
        payment_revision = digest(POLICY, batch_name, version["name"], [_claim_signature(row) for row in _active_claims(store, batch=batch_name, version=version["name"])])
        ledger.put("version", version["name"], {"calculated_at": None, "summary_snapshot_json": "{}", "rule_snapshot_json": "[]"})
        ledger.put("batch", batch_name, {"status": "Dirty", "confirm_status": "Pending", "is_locked": 0})
        result = {"status": "applied", "version": version["name"], "revision": payment_revision,
                  "application_id": application_id, "claims": claims, "attachments": attachments}
        application = {"id": application_id, "batch": batch_name, "version": version["name"], "preview_id": preview_id,
            "status": "applied", "revision": revision, "result": result, "actor": actor, "applied_at": utcnow(),
            "replaced_claim_ids": sorted(replace_ids)}
        store.insert("payment_application", {key: application[key] for key in ("id", "batch", "version", "preview_id", "status", "revision")} | {"data": dumps(application)})
        preview.update(status="applied", application_id=application_id, applied_at=utcnow(), applied_by=actor)
        store.put("payment_preview", {key: preview[key] for key in ("id", "batch", "version", "logistics_id", "source_id", "status", "revision", "expires_at")} | {"data": dumps(preview)})
        store.audit(batch_name, "payment_adoption_confirmed", actor, preview_id=preview_id, revision=payment_revision,
                    reason=preview["reason"], negative_confirmed=preview["negative_confirmed"], replace_claim_ids=sorted(replace_ids))
        return result


def _assert_claim_capacity(store, ledger, claim, source, amount, currency):
    if claim.get("exclusive"):
        line = store.get("freight_line", claim.get("source_line_id") or "", lock=True) or {}
        if (line.get("source_id") != source.get("id") or line.get("snapshot") != source.get("snapshot")
                or amount != _amount(line.get("amount")) or currency != line.get("currency")):
            raise ValueError(_("结构化明细金额和币种不可脱离审批来源"))
        return
    if currency != source.get("currency"):
        return
    used = sum((abs(_amount(row["amount"])) for row in _current_active_claims(store, ledger, source_id=source["id"])
                if row["id"] != claim["id"] and row.get("source_snapshot") == source["snapshot"]
                and row.get("currency") == currency), Decimal("0"))
    if used + abs(amount) > abs(_amount(source["amount"])):
        raise ValueError(_("更正后金额超过审批来源剩余额度"))


def amend_payment_claim(store, ledger, batch_name, version_name, claim_id, expected_revision, action,
                        reason, edits, actor, *, now=None, lease_check=None, edit_token=None, expected_modified=None):
    if action not in {"amount", "currency", "status", "reclassify", "revoke"} or not str(reason or "").strip():
        raise ValueError(_("请选择更正操作并填写原因"))
    if not isinstance(edits, dict):
        raise ValueError(_("更正参数格式错误"))
    allowed_edits = {"amount": {"amount", "negative_confirmed"}, "currency": {"currency", "amount_status"},
                     "status": {"amount_status"}, "reclassify": {"logical_fee_key"}, "revoke": set()}
    if set(edits) - allowed_edits[action]:
        raise ValueError(_("更正包含不允许的来源或审计字段"))
    with store.atomic():
        store.get("state", "match_lock", lock=True)
        if lease_check:
            lease_check(batch_name, edit_token=edit_token, expected_modified=expected_modified)
        batch = ledger.get("batch", batch_name, lock=True) or {}
        version = ledger.get("version", version_name, lock=True) or {}
        _editable(batch, version)
        claim = store.get("payment_claim", claim_id, lock=True)
        if not claim:
            from . import freight_adoption
            legacy = freight_adoption.context(store, ledger, batch_name, version_name, live=True)
            if any(row.get("id") == claim_id for row in legacy["claims"]):
                if action not in {"amount", "revoke"}:
                    raise ValueError(_("旧运费认领仅支持金额更正或撤销"))
                result = freight_adoption.amend(
                    store, ledger, batch_name, version_name, claim_id, expected_revision, action, actor,
                    reason=str(reason).strip(), amount=edits.get("amount"),
                    negative_confirmed=bool(edits.get("negative_confirmed")))
                remaining = next((row for row in result.get("claims", []) if row.get("id") == claim_id), None)
                return {**result, "legacy": True, "claim": remaining}
        if not claim or claim.get("batch") != batch_name or claim.get("version") != version_name or claim.get("status") != "active":
            raise ValueError(_("待更正付款认领不属于当前可编辑版本"))
        if claim.get("revision") != expected_revision:
            raise ValueError(_("付款认领已变化，请刷新"))
        source = store.get("source", claim["source_id"], lock=True) or {}
        logistics = store.get("source", claim["logistics_id"], lock=True) or {}
        stale = (source.get("snapshot") != claim.get("source_snapshot")
                 or logistics.get("snapshot") != claim.get("logistics_snapshot")
                 or not source.get("approved") or source.get("invalid") or source.get("kind") != "expense"
                 or logistics.get("invalid") or logistics.get("kind") != "logistics"
                 or source.get("corp") != logistics.get("corp"))
        if stale and action != "revoke":
            raise ValueError(_("付款来源或本票快照已变化，请先复核"))
        before = deepcopy(claim)
        affected_keys = {claim["logical_fee_key"]}
        if action == "amount":
            changed_amount = _amount(edits.get("amount"))
            if changed_amount < 0 and edits.get("negative_confirmed") not in (True, 1, "1", "true"):
                raise ValueError(_("请明确确认负数冲抵费用"))
            claim["amount"] = _text(changed_amount)
        elif action == "currency":
            new_currency = str(edits.get("currency") or "").upper()
            if new_currency not in CURRENCIES:
                raise ValueError(_("更正币种仅支持 RMB/USD/MXN"))
            resulting_status = str(edits.get("amount_status") or claim.get("amount_status") or "ACTUAL").upper()
            if new_currency != source.get("currency") and resulting_status == "ACTUAL":
                raise ValueError(_("与审批原币不同时只能保存为非实际金额状态"))
            if resulting_status not in AMOUNT_STATUSES:
                raise ValueError(_("费用金额状态不受支持"))
            claim.update(currency=new_currency, amount_status=resulting_status)
        elif action == "status":
            state = str(edits.get("amount_status") or "").upper()
            if state not in AMOUNT_STATUSES:
                raise ValueError(_("费用金额状态不受支持"))
            if claim.get("currency") != source.get("currency") and state == "ACTUAL":
                raise ValueError(_("非审批原币不能标记为实际金额"))
            claim["amount_status"] = state
        elif action == "reclassify":
            key = str(edits.get("logical_fee_key") or "")
            definitions = _allowed_keys(batch)
            if key not in definitions:
                raise ValueError(_("费用重分类不在当前运输方式允许范围"))
            if definitions[key][1] == "freight" and store.find("freight_claim", batch=batch_name):
                raise ValueError(_("新旧运费认领不能混用，请先在统一付款列表更正或撤销旧认领"))
            if claim.get("exclusive"):
                line = store.get("freight_line", claim.get("source_line_id") or "", lock=True) or {}
                expected_key = inferred_fee_key(line.get("scope"), batch.get("transport_mode"))
                if expected_key and key != expected_key:
                    raise ValueError(_("来源明细费用分类已明确，不能重分类"))
            if any(row["id"] != claim_id and row.get("logical_fee_key") == key and row.get("amount_status") == "ACTUAL"
                   for row in _active_claims(store, batch=batch_name, version=version_name)):
                raise ValueError(_("目标分类已有实际费用认领"))
            claim["logical_fee_key"] = key
            affected_keys.add(key)
        elif action == "revoke":
            claim.update(status="revoked", active=False, revoked_at=utcnow(), original_claim_key=claim["claim_key"],
                         claim_key=digest("released-payment-claim", claim["id"], expected_revision, "revoke"))
        if action != "revoke":
            _assert_claim_capacity(store, ledger, claim, source, _amount(claim["amount"]), claim["currency"])
        claim.update(revision=digest(POLICY, before["revision"], action, edits, str(reason).strip()),
                     correction_reason=str(reason).strip(), corrected_by=actor, corrected_at=utcnow())
        from .payment_evidence import (
            capture_current_rule_evidence,
            migrate_current_rule_evidence,
            update_pending_evidence_for_claim,
        )
        captured_evidence = capture_current_rule_evidence(ledger, batch_name, version_name, affected_keys)
        _store_claim(store, claim)
        _disable_claim_rule(ledger, batch_name, version_name, claim_id, actor, str(reason).strip())
        _sync_rule_groups(store, ledger, batch_name, version_name, affected_keys, _allowed_keys(batch), actor,
                          str(reason).strip())
        evidence_aliases = ({claim["logical_fee_key"]: before["logical_fee_key"]}
                            if action == "reclassify" else {})
        migrate_current_rule_evidence(ledger, batch_name, version_name, captured_evidence, actor,
                                      str(reason).strip(), aliases=evidence_aliases)
        claim = store.get("payment_claim", claim_id)
        active_affected_claims = [row for row in _active_claims(
            store, batch=batch_name, version=version_name) if row.get("logical_fee_key") in affected_keys]
        pending_bindings = (_payment_evidence_bindings(
            store, ledger, batch_name, version_name,
            [{"id": row["id"]} for row in active_affected_claims]) if active_affected_claims else {})
        update_pending_evidence_for_claim(
            store, batch_name, version_name, before, claim, action, pending_bindings,
            _payment_evidence_rows(active_affected_claims), actor, str(reason).strip())
        ledger.put("version", version_name, {"calculated_at": None, "summary_snapshot_json": "{}", "rule_snapshot_json": "[]"})
        ledger.put("batch", batch_name, {"status": "Dirty", "confirm_status": "Pending", "is_locked": 0})
        app_revision = digest(POLICY, "amend", claim_id, claim["revision"])
        application_id = digest(POLICY, batch_name, version_name, app_revision)
        result = {"status": "applied", "version": version_name, "revision": app_revision,
                  "application_id": application_id, "claim": claim}
        application = {"id": application_id, "batch": batch_name, "version": version_name,
            "preview_id": "amend:" + claim_id, "status": "applied", "revision": app_revision,
            "result": result, "before": before, "actor": actor, "reason": str(reason).strip(), "applied_at": utcnow()}
        store.insert("payment_application", {key: application[key] for key in ("id", "batch", "version", "preview_id", "status", "revision")} | {"data": dumps(application)})
        store.audit(batch_name, "payment_claim_" + action, actor, claim_id=claim_id, reason=str(reason).strip(),
                    before=_claim_signature(before), after=_claim_signature(claim), version=version_name,
                    negative_confirmed=bool(edits.get("negative_confirmed")))
        return result


def _claim_stale(store, claim):
    source = store.get("source", claim.get("source_id") or "") or {}
    logistics = store.get("source", claim.get("logistics_id") or "") or {}
    return (not source or not logistics or source.get("snapshot") != claim.get("source_snapshot")
            or logistics.get("snapshot") != claim.get("logistics_snapshot")
            or not source.get("approved") or source.get("invalid") or source.get("kind") != "expense"
            or logistics.get("invalid") or logistics.get("kind") != "logistics"
            or source.get("corp") != logistics.get("corp"))


def payment_claim_blockers(store, ledger, batch_name, version_name):
    issues = []
    for claim in _active_claims(store, batch=batch_name, version=version_name):
        if _claim_stale(store, claim):
            issues.append("已认领付款来源已更新、失效或不再属于本票，请复核或撤销")
    return sorted(set(issues))


def payment_freight_adoption_status(store, ledger, batch_name, version_name):
    """Validate whether current payment-owned freight can satisfy final adoption."""
    from overseas_costing.services.transport_fee_service import PRIMARY_FREIGHT, primary_freight_definition

    batch = ledger.get("batch", batch_name) or {}
    freight_keys = {definition[0] for definition in PRIMARY_FREIGHT.values()}
    all_freight = [row for row in _active_claims(store, batch=batch_name, version=version_name)
                   if row.get("logical_fee_key") in freight_keys]
    if not all_freight:
        return {"selected": False, "issues": []}
    try:
        freight_key = primary_freight_definition(batch.get("transport_mode"))["logical_fee_key"]
    except ValueError:
        return {"selected": True, "issues": ["付款运费与当前运输方式不一致，请重新核对"]}
    claims = [row for row in all_freight if row.get("logical_fee_key") == freight_key]
    if len(claims) != len(all_freight):
        return {"selected": True, "issues": ["付款运费与当前运输方式不一致，请重新核对"]}
    issues = []
    applications = store.find("payment_application", batch=batch_name, version=version_name, status="applied")
    rules = ledger.rows("rule", batch=batch_name, version=version_name)
    active_freight_rules = [row for row in rules if row.get("logical_fee_key") == freight_key
                            and row.get("is_enabled") not in (0, False, "0")
                            and row.get("is_active") not in (0, False, "0")]
    if len(active_freight_rules) != 1:
        issues.append("付款运费当前有效费用规则缺失或重复，请重新核对")
    for claim in claims:
        if _claim_stale(store, claim):
            issues.append("付款运费来源快照已变化或失效，请复核或撤销")
        source = store.get("source", claim.get("source_id") or "") or {}
        if source.get("currency") and str(source.get("currency")).upper() != claim.get("currency"):
            issues.append("付款运费认领币种与当前审批原币不一致，请重新核对")
        current_application = False
        for application in applications:
            result = application.get("result") or {}
            applied_claims = list(result.get("claims") or [])
            if result.get("claim"):
                applied_claims.append(result["claim"])
            if any(row and row.get("id") == claim["id"] and row.get("revision") == claim.get("revision")
                   for row in applied_claims):
                current_application = True
                break
        if not current_application:
            issues.append("付款运费缺少当前有效应用记录，请重新核对")
    groups = {}
    for claim in claims:
        groups.setdefault(claim.get("rule_binding_id") or claim["id"], []).append(claim)
    for binding_id, grouped in groups.items():
        bound = [row for row in rules if row.get("logical_fee_key") == freight_key
                 and row.get("source_binding_id") == binding_id]
        active = [row for row in bound if row.get("is_enabled") not in (0, False, "0")
                  and row.get("is_active") not in (0, False, "0")]
        if not bound:
            issues.append("付款运费缺少对应费用绑定，请重新核对")
            continue
        if len(active) != 1:
            issues.append("付款运费对应费用规则已失效或重复，请重新核对")
            continue
        rule = active[0]
        currencies = {row.get("currency") for row in grouped}
        snapshots = {row.get("source_snapshot") for row in grouped}
        statuses = {row.get("amount_status") or "ACTUAL" for row in grouped}
        try:
            amount_matches = (_amount(rule.get("amount")) ==
                              sum((_amount(row.get("amount")) for row in grouped), Decimal("0")))
        except ValueError:
            amount_matches = False
        if (len(currencies) != 1 or rule.get("currency") not in currencies or not amount_matches
                or statuses != {"ACTUAL"} or rule.get("amount_status") != "ACTUAL"
                or rule.get("is_final") not in (1, True, "1")
                or len(snapshots) != 1 or rule.get("source_snapshot") not in snapshots):
            issues.append("付款运费规则与当前认领金额、币种或快照不一致，请重新核对")
    return {"selected": True, "issues": sorted(set(issues))}


def public_payment_context(store, ledger, batch_name, version_name):
    from . import freight_adoption
    from overseas_costing.services.transport_fee_service import primary_freight_definition
    batch = ledger.get("batch", batch_name) or {}
    version = ledger.get("version", version_name) or {}
    historical = (version_name != batch.get("current_version") or version.get("status") in {"Confirmed", "Archived"}
                  or batch.get("confirm_status") == "Confirmed" or batch.get("writeback_status") == "Success")
    claims = store.find("payment_claim", batch=batch_name, version=version_name)
    group_sizes = {}
    for row in claims:
        if row.get("status") == "active":
            binding = row.get("rule_binding_id") or row["id"]
            group_sizes[binding] = group_sizes.get(binding, 0) + 1
    projected = []
    blocking = []
    source_ids = {claim["source_id"] for claim in claims}
    for mapping in store.find("batch_map", batch=batch_name):
        source_ids.update(row["expense_id"] for row in store.find("freight_candidate", logistics_id=mapping["source_id"])
                          if row.get("status") != "rejected")
    for source_id in source_ids:
        source = store.get("source", source_id)
        if source:
            blocking.extend(_balance_state(store, ledger, source)["blocking"])
    for claim in claims:
        source = store.get("source", claim["source_id"]) or {}
        stale = _claim_stale(store, claim)
        active = claim.get("status") == "active"
        if active and stale:
            blocking.append("已认领付款来源已更新或失效，请复核")
        normal_actions = ["amount", "currency", "status", "reclassify", "revoke"]
        if group_sizes.get(claim.get("rule_binding_id") or claim["id"], 0) > 1:
            normal_actions = ["amount", "reclassify", "revoke"]
        projected.append({"id": claim["id"], "source_approval_no": claim.get("approval_no") or source.get("approval_no") or "",
            "source_title": claim.get("source_title") or source.get("title") or "", "logical_fee_key": claim["logical_fee_key"],
            "amount": claim["amount"], "currency": claim["currency"], "amount_status": claim.get("amount_status") or "ACTUAL",
            "status": "stale_review" if stale and active else claim.get("status"), "active": active, "stale": stale,
            "revision": claim["revision"], "legacy": False,
            "available_actions": ([] if historical or not active else (["revoke"] if stale else
                normal_actions))})
    legacy_context = freight_adoption.context(store, ledger, batch_name, version_name, live=True)
    try:
        freight_key = primary_freight_definition(batch.get("transport_mode"))["logical_fee_key"]
    except ValueError:
        freight_key = "freight"
    legacy_stale = bool(legacy_context.get("issues"))
    for claim in legacy_context.get("claims") or []:
        source = store.get("source", claim.get("source_id") or "") or {}
        projected.append({"id": claim["id"], "source_approval_no": claim.get("approval_no") or source.get("approval_no") or "",
            "source_title": source.get("title") or "", "logical_fee_key": freight_key,
            "amount": claim["amount"], "currency": claim["currency"], "amount_status": "ACTUAL",
            "status": "stale_review" if legacy_stale else "active", "active": True, "stale": legacy_stale,
            "revision": legacy_context["revision"], "legacy": True,
            "available_actions": ([] if historical else (["revoke"] if legacy_stale else ["amount", "revoke"]))})
    active = [_claim_signature(row) for row in claims if row.get("status") == "active"]
    active += [{key: row.get(key) for key in ("id", "source_id", "source_snapshot", "amount", "currency")}
               for row in legacy_context.get("claims") or []]
    from .payment_evidence import public_pending_evidence
    return {"payment_claims": projected,
            "payment_evidence_pending": public_pending_evidence(store, batch_name, version_name),
            "payment_revision": digest(POLICY, batch_name, version_name, active),
            "payment_blocking_reasons": sorted(set(blocking + payment_claim_blockers(store, ledger, batch_name, version_name)))}
