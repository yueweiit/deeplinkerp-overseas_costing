"""
中文用途：分摊规则服务。

这里负责把“可追溯费用池”转成“基础分摊口径”。
AI 只选择分摊依据和理由，不允许凭空新增金额；系统随后按该口径填入每行分摊金额。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import os
import re
import urllib.error
import urllib.request

try:
    import frappe
except Exception:  # pragma: no cover - 本地测试环境不一定有 Frappe
    frappe = None

SUPPORTED_ALLOCATION_BASES = ("goods_value", "gross_weight", "volume", "chargeable_weight")
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
MAX_AI_ITEMS = 80


def get_supported_allocation_bases() -> list[dict]:
    return [
        {"code": "goods_value", "label": "按货值分摊"},
        {"code": "gross_weight", "label": "按重量分摊"},
        {"code": "volume", "label": "按体积分摊"},
        {"code": "chargeable_weight", "label": "按计费重分摊"},
    ]


def suggest_allocation_rules_with_ai(
    *,
    items: list[dict],
    candidate_rules: list[dict],
    context: dict | None = None,
) -> dict:
    """调用 AI 选择基础分摊口径；未配置或失败时返回 ok=False，由调用方回落系统规则。"""

    normalized_candidates = _normalize_candidate_rules(candidate_rules)
    if not normalized_candidates:
        return {
            "ok": False,
            "action": "skipped",
            "reason": "当前没有可供 AI 判断的费用池。",
            "rules": [],
        }

    config = _ai_config()
    if not config.get("api_key"):
        return {
            "ok": False,
            "action": "skipped",
            "reason": "未配置 AI 接口密钥，已回落系统基础分摊规则。",
            "rules": normalized_candidates,
        }
    payload = _build_ai_prompt_payload(
        items=items,
        candidate_rules=normalized_candidates,
        context=context or {},
    )
    messages = _build_ai_messages(payload)
    try:
        content = _call_chat_completions(config, messages)
        parsed = _extract_json_object(content)
        rules = _normalize_ai_rules(parsed, normalized_candidates)
        rules = _append_basis_coverage_warnings(rules, items)
    except Exception as exc:  # pragma: no cover - 网络异常路径本地通常不走
        return {
            "ok": False,
            "action": "failed",
            "reason": f"AI 分摊口径调用失败，已回落系统基础分摊规则：{exc}",
            "rules": normalized_candidates,
            "model": config.get("model"),
        }

    if not rules:
        return {
            "ok": False,
            "action": "invalid",
            "reason": "AI 未返回有效分摊口径，已回落系统基础分摊规则。",
            "rules": normalized_candidates,
            "model": config.get("model"),
        }

    return {
        "ok": True,
        "action": "suggested",
        "source": "ai",
        "model": config.get("model"),
        "rules": rules,
        "summary": str(parsed.get("summary") or "AI 已选择基础分摊口径，系统已填入每行基础分摊金额。"),
        "message": "AI 已选择基础分摊口径，系统已填入基础分摊金额。",
    }


def _ai_config() -> dict:
    api_key = (
        _conf_value("overseas_cost_ai_api_key")
        or os.getenv("OVERSEAS_COST_AI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or ""
    )
    base_url = (
        _conf_value("overseas_cost_ai_base_url")
        or os.getenv("OVERSEAS_COST_AI_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or DEFAULT_DEEPSEEK_BASE_URL
        or ""
    )
    model = (
        _conf_value("overseas_cost_ai_model")
        or os.getenv("OVERSEAS_COST_AI_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or DEFAULT_DEEPSEEK_MODEL
        or ""
    )
    timeout = _to_float(
        _conf_value("overseas_cost_ai_timeout") or os.getenv("OVERSEAS_COST_AI_TIMEOUT"),
        default=18.0,
    )
    return {
        "api_key": str(api_key or "").strip(),
        "base_url": str(base_url or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/"),
        "model": str(model or DEFAULT_DEEPSEEK_MODEL).strip(),
        "timeout": timeout,
    }


def _conf_value(key: str):
    if frappe is None:
        return None
    value = None
    conf = getattr(frappe, "conf", None)
    try:
        if conf and hasattr(conf, "get"):
            value = conf.get(key)
        elif conf:
            value = getattr(conf, key, None)
    except Exception:
        value = None
    if value not in (None, ""):
        return value

    try:
        get_site_path = getattr(frappe, "get_site_path", None)
        site_config_path = get_site_path("site_config.json") if callable(get_site_path) else None
    except Exception:
        site_config_path = None
    if not site_config_path:
        return value

    try:
        with open(site_config_path, encoding="utf-8") as handle:
            site_config = json.load(handle)
        return site_config.get(key) or value
    except Exception:
        return value


def _build_ai_prompt_payload(*, items: list[dict], candidate_rules: list[dict], context: dict) -> dict:
    from overseas_costing.services.shipment_cost_service import shipment_value

    effective_goods_values = [
        _to_float(shipment_value(row).get("amount_rmb"))
        for row in (items or [])
    ]
    item_rows = [
        {
            "row_no": row.get("row_no"),
            "material_code": row.get("material_code"),
            "product_name": row.get("product_name"),
            "category": row.get("category"),
            "transport_mode": row.get("transport_mode"),
            "goods_value": effective_goods_values[index],
            "quantity": _to_float(row.get("quantity")),
            "gross_weight_kg": _to_float(row.get("gross_weight_kg")),
            "volume_m3": _to_float(row.get("volume_m3")),
            "volume_weight_kg": _to_float(row.get("volume_weight_kg")),
            "chargeable_weight_kg": _to_float(row.get("chargeable_weight_kg")),
        }
        for index, row in enumerate((items or [])[:MAX_AI_ITEMS])
    ]
    totals = {
        "item_count": len(items or []),
        "sample_item_count": len(item_rows),
        "total_goods_value": sum(effective_goods_values),
        "total_gross_weight_kg": sum(_to_float(row.get("gross_weight_kg")) for row in items or []),
        "total_volume_m3": sum(_to_float(row.get("volume_m3")) for row in items or []),
        "total_chargeable_weight_kg": sum(_chargeable_weight(row) for row in items or []),
        "missing_goods_value_count": sum(1 for value in effective_goods_values if not value),
        "missing_gross_weight_count": sum(1 for row in items or [] if not _to_float(row.get("gross_weight_kg"))),
        "missing_volume_count": sum(1 for row in items or [] if not _to_float(row.get("volume_m3"))),
        "missing_chargeable_weight_count": sum(1 for row in items or [] if not _chargeable_weight(row)),
    }
    return {
        "context": {
            "batch_name": context.get("batch_name") or "",
            "version_name": context.get("version_name") or "",
            "transport_mode": context.get("transport_mode") or _first_value(items, "transport_mode"),
            "project": context.get("project") or context.get("project_collection") or _first_value(items, "project_collection"),
            "supplier": context.get("supplier") or context.get("vendor") or _first_value(items, "supplier"),
            "fx_rmb_to_mxn": context.get("fx_rmb_to_mxn"),
            "fx_usd_to_rmb": context.get("fx_usd_to_rmb"),
            "retrieval_context": context.get("retrieval_context") or [],
            "retrieval_version": context.get("retrieval_version") or "",
        },
        "totals": totals,
        "candidate_rules": candidate_rules,
        "items": item_rows,
    }


def _build_ai_messages(payload: dict) -> list[dict]:
    system_prompt = (
        "你是海外采购综合成本核算的分摊顾问。"
        "你的任务是基于给定费用池和物料结构，只选择基础分摊口径；逐 SKU 金额由服务端规则引擎计算。"
        "你必须遵守：1. 不得新增费用池；2. 不得修改金额和币种；"
        "3. allocation_basis 只能是 goods_value、gross_weight、volume、chargeable_weight；"
        "4. 不得输出或推算逐 SKU 分摊金额；5. 输出必须是 JSON 对象；"
        "6. 如果证据不足，选择最保守、最容易解释的分摊依据；"
        "7. candidate_rules 提供 available_bases 时，allocation_basis 必须从该列表中选择。"
    )
    user_prompt = {
        "task": "请为 candidate_rules 中每个费用池选择基础分摊依据，并给出中文理由。不要输出逐 SKU 金额。",
        "output_schema": {
            "summary": "一句中文总结",
            "rules": [
                {
                    "rule_code": "必须来自 candidate_rules.rule_code",
                    "allocation_basis": "goods_value/gross_weight/volume/chargeable_weight",
                    "reason": "中文理由",
                    "confidence": "0-1 数字",
                }
            ],
        },
        "business_hint": (
            "所有可追溯的物流费、清关费、税费、仓储费、滞留罚款、杂费原则上都进入综合成本；"
            "关税、增值税最终以完税凭证为准，已有物料税费金额不得作为整票费用池重复分摊；"
            "运输费需根据运输方式、物料重量与体积特征，在毛重、体积或计费重中选择最可解释的口径；"
            "与价值、保险、税负或商品责任相关的费用可建议按货值。"
            "每个费用项独立判断，不得用单一全局默认覆盖所有费用。"
            "必须检查 totals 中的缺失数量，不得把存在缺失的数据描述为完整；"
            "缺少分摊依据时应在理由中明确提示补数据，不得为了得到结果而建议平均分摊。"
        ),
        "data": payload,
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, default=str)},
    ]


def _call_chat_completions(
    config: dict,
    messages: list[dict],
    *,
    response_json: bool = True,
    disable_thinking: bool = True,
    temperature: float | None = 0.1,
) -> str:
    payload = {
        "model": config["model"],
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking and "deepseek" in str(config.get("base_url") or "").lower():
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(config.get("timeout") or 18)) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"AI 接口返回失败：HTTP {exc.code} {detail}") from exc
    loaded = json.loads(response_body)
    choices = loaded.get("choices") or []
    if not choices:
        raise RuntimeError("AI 接口未返回 choices。")
    content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("AI 接口返回内容为空。")
    return content


def _normalize_candidate_rules(candidate_rules: list[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for index, rule in enumerate(candidate_rules or [], start=1):
        decision_request = bool(rule.get("_decision_request"))
        public_rule = {key: value for key, value in rule.items() if key != "_decision_request"}
        amount = _to_float(rule.get("amount"))
        rule_code = str(rule.get("rule_code") or rule.get("fee_key") or f"fee_{index}").strip()
        if not rule_code or not amount:
            continue
        key = (rule_code, amount, _normalize_currency_code(rule.get("currency")))
        if key in seen:
            continue
        seen.add(key)
        basis = str(rule.get("allocation_basis") or rule.get("basis_field") or "goods_value").strip()
        if basis not in SUPPORTED_ALLOCATION_BASES:
            basis = "goods_value"
        normalized_rule = {
            **public_rule,
            "rule_code": rule_code,
            "expense_category": str(rule.get("expense_category") or rule_code).strip(),
            "currency": _normalize_currency_code(rule.get("currency")),
            "amount": amount,
            "priority_no": int(_to_float(rule.get("priority_no"), default=float(index * 10))),
            "is_enabled": 1,
        }
        if not decision_request:
            normalized_rule.update({"allocation_basis": basis, "basis_field": basis})
        normalized.append(normalized_rule)
    return normalized


def _normalize_ai_rules(parsed: dict, candidate_rules: list[dict]) -> list[dict]:
    rules = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(rules, list):
        return []
    candidates_by_code = {rule["rule_code"]: rule for rule in candidate_rules}
    normalized = []
    used_codes = set()
    for row in rules:
        if not isinstance(row, dict):
            continue
        rule_code = str(row.get("rule_code") or "").strip()
        if rule_code not in candidates_by_code or rule_code in used_codes:
            continue
        basis = str(row.get("allocation_basis") or "").strip()
        if basis not in SUPPORTED_ALLOCATION_BASES:
            continue
        base = dict(candidates_by_code[rule_code])
        allowed = base.get("available_bases")
        if isinstance(allowed, list) and allowed and basis not in allowed:
            continue
        reason = str(row.get("reason") or "").strip()
        confidence = _to_float(row.get("confidence"), default=0.0)
        base.update(
            {
                "allocation_basis": basis,
                "basis_field": basis,
                "remark": f"AI基础分摊填入：{reason or 'AI 根据费用类型和物料结构选择分摊依据'}",
                "ai_confidence": confidence,
                "is_ai_suggestion": 1,
                "is_system_suggestion": 0,
            }
        )
        normalized.append(base)
        used_codes.add(rule_code)

    for candidate in candidate_rules:
        if candidate["rule_code"] not in used_codes:
            fallback = dict(candidate)
            fallback["remark"] = f"AI未返回该费用池，沿用系统基础分摊：{fallback.get('remark') or ''}".strip()
            fallback["is_ai_suggestion"] = 0
            fallback["is_system_suggestion"] = 1
            normalized.append(fallback)
    return normalized


def _append_basis_coverage_warnings(rules: list[dict], items: list[dict]) -> list[dict]:
    field_map = {
        "goods_value": ("goods_value", "货值"),
        "gross_weight": ("gross_weight_kg", "重量"),
        "volume": ("volume_m3", "体积"),
        "chargeable_weight": ("chargeable_weight_kg", "计费重"),
    }
    warned_rules = []
    for rule in rules or []:
        current = dict(rule)
        basis = str(current.get("allocation_basis") or current.get("basis_field") or "").strip()
        fieldname, label = field_map.get(basis, ("", ""))
        if basis == "chargeable_weight":
            missing_count = sum(1 for row in items or [] if not _chargeable_weight(row))
        else:
            missing_count = sum(1 for row in items or [] if fieldname and not _to_float(row.get(fieldname)))
        if missing_count:
            warning = f"数据校验：{missing_count} 行缺少{label}，需补齐后复核"
            remark = str(current.get("remark") or "")
            remark = re.sub(
                r"(毛重|净重|计费重|计费重量|重量|体积|货值)数据完整(?:（仅\s*\d+\s*条缺失）)?",
                rf"\1数据存在缺失（{missing_count} 行）",
                remark,
            )
            current["remark"] = f"{remark.rstrip('；。')}；{warning}。"
            current["basis_missing_count"] = missing_count
        warned_rules.append(current)
    return warned_rules


def _extract_json_object(content: str) -> dict:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        loaded = json.loads(match.group(0))
    if not isinstance(loaded, dict):
        raise ValueError(_("AI 返回结果不是 JSON 对象。"))
    return loaded


def _normalize_currency_code(value) -> str:
    compact = str(value or "").strip().replace(" ", "").lower()
    if not compact:
        return "RMB"
    if "usd" in compact or "dólar" in compact or "dolar" in compact or "美元" in compact:
        return "USD"
    if "mxn" in compact or "peso" in compact or "比索" in compact or "墨西哥" in compact:
        return "MXN"
    if "rmb" in compact or "cny" in compact or "人民币" in compact:
        return "RMB"
    return str(value).strip().upper()


def _to_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _chargeable_weight(row: dict) -> float:
    explicit = _to_float(row.get("chargeable_weight_kg"))
    if explicit:
        return explicit
    gross_weight = _to_float(row.get("gross_weight_kg"))
    volume_weight = _to_float(row.get("volume_weight_kg"))
    return max(gross_weight, volume_weight)


def _first_value(items: list[dict], fieldname: str):
    for row in items or []:
        value = row.get(fieldname)
        if value not in (None, ""):
            return value
    return ""
