"""中文用途：商品业务归类预览服务。

AI/规则只生成业务大类建议，用于查询、汇总和费用分摊参考。
不覆盖海关进口名称、HS 编码、税率、完税金额，也不直接回写商品明细。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import re

from overseas_costing.services import allocation_service

try:
    import frappe
except Exception:  # pragma: no cover - 本地无 Frappe 环境时保持可导入
    frappe = None


ITEM_FIELDS = [
    "name",
    "row_no",
    "material_code",
    "product_name",
    "product_name_es",
    "import_name",
    "spec_model",
    "hs_code",
    "category",
    "source_doc_no",
    "purchase_order_no",
]
MAX_AI_NORMALIZATION_ITEMS = 120
MIN_AI_CONFIDENCE = 0.72
CATEGORY_USAGE = "query_summary_allocation_reference"
CATEGORY_USAGE_LABEL = "查询 / 汇总 / 分摊参考"
CATEGORY_CALCULATION_POLICY = "仅作为查询、汇总和费用分摊参考；不覆盖海关进口名称、HS编码、税率、完税金额。"

NAME_NORMALIZATION_RULES = [
    {
        "canonical_name": "太阳眼镜",
        "aliases": ("墨镜", "墨迹", "太阳镜", "太阳眼镜", "sunglass", "sunglasses", "gafas de sol", "lentes de sol"),
    },
]


def preview_batch_categories(batch_name: str | None = None, version_name: str | None = None, rows_json: str | None = None, limit: int | str = 200) -> dict:
    """返回批次内商品的业务归类预览，不写库。"""

    items = _load_rows(rows_json)
    dry_run = frappe is None
    batch_doc_name = batch_name or ""
    resolved_version_name = version_name or ""

    if not items and frappe is not None:
        if not batch_name:
            return {"ok": False, "message": "缺少批次。", "items": [], "summary": {}}
        batch_doc_name = _resolve_batch_name(batch_name)
        if not batch_doc_name:
            return {"ok": False, "message": f"未找到批次：{batch_name}", "items": [], "summary": {}}
        resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
        if not resolved_version_name:
            return {"ok": False, "message": "当前批次没有可归类版本。", "items": [], "summary": {}}
        items = _fetch_batch_items(batch_doc_name, resolved_version_name, _normalize_limit(limit))

    suggestions = [_build_preview_row(row) for row in items[: _normalize_limit(limit)]]
    ai_result = _apply_ai_business_category(suggestions)
    summary = _summarize_suggestions(suggestions)
    summary.update(ai_result)
    summary.update(
        {
            "category_usage": CATEGORY_USAGE,
            "category_usage_label": CATEGORY_USAGE_LABEL,
            "calculation_policy": CATEGORY_CALCULATION_POLICY,
            "affects_customs_fields": False,
            "affects_tax_rate": False,
        }
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "items": suggestions,
        "summary": summary,
        "taxonomy": [row["canonical_name"] for row in NAME_NORMALIZATION_RULES],
        "message": _category_preview_message(summary),
    }


def _resolve_batch_name(batch_name: str) -> str | None:
    batch = frappe.db.get_value("Overseas Cost Batch", batch_name, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    batch = frappe.db.get_value("Overseas Cost Batch", {"batch_no": batch_name}, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    return None


def _resolve_version_name(batch_doc_name: str, version_name: str | None = None) -> str | None:
    if version_name:
        return version_name
    current_version = frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "current_version")
    if current_version:
        return current_version
    rows = frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=["name"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return rows[0]["name"] if rows else None


def _fetch_batch_items(batch_doc_name: str, version_name: str, limit: int) -> list[dict]:
    return frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": version_name, "is_excluded": 0},
        fields=ITEM_FIELDS,
        order_by="row_no asc",
        limit_page_length=limit,
    )


def _load_rows(rows_json: str | None) -> list[dict]:
    if not rows_json:
        return []
    loaded = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
    if isinstance(loaded, dict):
        loaded = [loaded]
    if not isinstance(loaded, list):
        raise ValueError(_("rows_json 必须是 JSON 对象或对象数组。"))
    return [row for row in loaded if isinstance(row, dict)]


def _build_preview_row(row: dict) -> dict:
    suggestion = _suggest_business_category(row)
    current_category = row.get("category") or ""
    preview_row = {
        "item_name": row.get("name") or "",
        "row_no": row.get("row_no") or "",
        "material_code": row.get("material_code") or "",
        "product_name": row.get("product_name") or "",
        "product_name_es": row.get("product_name_es") or "",
        "import_name": row.get("import_name") or "",
        "spec_model": row.get("spec_model") or "",
        "hs_code": row.get("hs_code") or "",
        "current_category": current_category,
        "current_business_category": current_category,
        **suggestion,
    }
    return _with_category_policy(preview_row)


def _suggest_business_category(row: dict) -> dict:
    name_fields = ("product_name", "product_name_es", "import_name", "spec_model")
    source_names = [str(row.get(field) or "").strip() for field in name_fields if str(row.get(field) or "").strip()]
    normalized_names = {_normalize_text(name) for name in source_names}

    for definition in NAME_NORMALIZATION_RULES:
        canonical_name = definition["canonical_name"]
        canonical_key = _normalize_text(canonical_name)
        alias_hits = _alias_hits(" ".join(source_names), definition.get("aliases") or ())
        if not alias_hits or canonical_key in normalized_names:
            continue
        return {
            "suggested_category": canonical_name,
            "suggested_name": canonical_name,
            "suggested_business_category": canonical_name,
            "confidence": 0.95,
            "reason": f"命中业务别名：{'、'.join(alias_hits[:3])}；建议归到“{canonical_name}”，仅用于查询、汇总和分摊参考。",
            "match_type": "explicit_business_alias",
            "needs_review": True,
            "ai_ready": False,
            "no_action": False,
        }

    return {
        "suggested_category": "",
        "suggested_name": "",
        "suggested_business_category": "",
        "confidence": 0.0,
        "reason": "未发现证据明确的业务归类建议。",
        "match_type": "no_action",
        "needs_review": False,
        "ai_ready": False,
        "no_action": True,
    }


def _with_category_policy(row: dict) -> dict:
    suggested_business_category = row.get("suggested_business_category") or row.get("suggested_category") or ""
    row["suggested_business_category"] = suggested_business_category
    row["business_category"] = suggested_business_category or row.get("current_business_category") or ""
    row["category_usage"] = CATEGORY_USAGE
    row["category_usage_label"] = CATEGORY_USAGE_LABEL
    row["calculation_policy"] = CATEGORY_CALCULATION_POLICY
    row["affects_customs_fields"] = False
    row["affects_tax_rate"] = False
    return row


def _apply_ai_business_category(items: list[dict]) -> dict:
    candidates = [
        row
        for row in items
        if row.get("no_action") and _has_ai_candidate_text(row)
    ][:MAX_AI_NORMALIZATION_ITEMS]
    if not candidates:
        return {
            "ai_enabled": False,
            "ai_status": "skipped",
            "ai_message": "当前批次没有需要 AI 判断的业务归类候选。",
            "ai_candidate_count": 0,
            "ai_business_category_count": 0,
            "ai_normalization_count": 0,
        }

    config = allocation_service._ai_config()
    if not config.get("api_key"):
        return {
            "ai_enabled": False,
            "ai_status": "not_configured",
            "ai_message": "AI 未配置，已仅使用明确业务别名规则。",
            "ai_candidate_count": len(candidates),
            "ai_business_category_count": 0,
            "ai_normalization_count": 0,
        }

    payload = _build_ai_payload(candidates)
    try:
        content = allocation_service._call_chat_completions(config, _build_ai_messages(payload))
        parsed = allocation_service._extract_json_object(content)
        applied_count = _apply_ai_rows(items, parsed)
    except Exception as exc:
        return {
            "ai_enabled": True,
            "ai_status": "failed",
            "ai_message": f"AI 业务归类判断失败，已保留规则预览：{exc}",
            "ai_candidate_count": len(candidates),
            "ai_business_category_count": 0,
            "ai_normalization_count": 0,
        }

    return {
        "ai_enabled": True,
        "ai_status": "ok",
        "ai_message": f"AI 已完成业务归类判断，命中 {applied_count} 个候选。",
        "ai_candidate_count": len(candidates),
        "ai_business_category_count": applied_count,
        "ai_normalization_count": applied_count,
    }


def _build_ai_payload(candidates: list[dict]) -> dict:
    return {
        "known_examples": NAME_NORMALIZATION_RULES,
        "rules": [
            "把不同语言、别名或相近叫法归到统一业务大类，例如墨镜/太阳镜/sunglasses归到太阳眼镜。",
            "业务大类仅用于查询、汇总和费用分摊参考，必须人工确认后才能用于规则沉淀。",
            "不得修改或推断海关进口名称、HS编码、税率、完税金额。",
            "证据不足时必须返回 no_action，不要把普通袋子、原料、配件粗暴归到包装材料或其他大类。",
            "suggested_business_category 使用中文短名称，表示建议业务大类。",
        ],
        "items": [
            {
                "item_name": row.get("item_name"),
                "row_no": row.get("row_no"),
                "material_code": row.get("material_code"),
                "product_name": row.get("product_name"),
                "product_name_es": row.get("product_name_es"),
                "import_name": row.get("import_name"),
                "spec_model": row.get("spec_model"),
                "hs_code": row.get("hs_code"),
                "current_category": row.get("current_category"),
            }
            for row in candidates
        ],
    }


def _build_ai_messages(payload: dict) -> list[dict]:
    system_prompt = (
        "你是海外采购成本核算中的商品业务归类助手。"
        "你的任务是把不同语言、别名或相近叫法归到统一业务大类，辅助财务查询、汇总和费用分摊参考。"
        "你不能修改、推断或覆盖海关进口名称、HS编码、税率、完税金额。"
        "必须谨慎：证据不足就 no_action；不得把普通袋子、原料、配件等粗暴合并。"
        "输出必须是 JSON 对象。"
    )
    user_prompt = {
        "task": "判断 items 中是否存在可建议的业务大类。只返回需要归类的候选或明确 no_action。",
        "output_schema": {
            "items": [
                {
                    "item_name": "必须来自输入 item_name",
                    "action": "classify/no_action",
                    "suggested_business_category": "建议业务大类；no_action 时为空",
                    "suggested_name": "同 suggested_business_category，兼容旧字段；no_action 时为空",
                    "suggested_category": "同 suggested_business_category，兼容旧字段；no_action 时为空",
                    "confidence": "0-1 数字",
                    "reason": "中文理由，说明命中的同义词、跨语言名称或品类依据",
                }
            ]
        },
        "data": payload,
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, default=str)},
    ]


def _apply_ai_rows(items: list[dict], parsed: dict) -> int:
    rows = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return 0

    by_item_name = {str(row.get("item_name") or ""): row for row in items if row.get("item_name")}
    applied_count = 0
    for ai_row in rows:
        if not isinstance(ai_row, dict):
            continue
        item = by_item_name.get(str(ai_row.get("item_name") or ""))
        if not item:
            continue
        action = str(ai_row.get("action") or "").strip().lower()
        suggested_name = str(
            ai_row.get("suggested_business_category")
            or ai_row.get("suggested_name")
            or ai_row.get("suggested_category")
            or ""
        ).strip()
        confidence = _to_float(ai_row.get("confidence"))
        if action not in {"classify", "normalize"} or not suggested_name or confidence < MIN_AI_CONFIDENCE:
            continue
        if _suggestion_already_present(item, suggested_name):
            continue
        if _is_too_generic_ai_suggestion(item, suggested_name):
            continue
        item.update(
            {
                "suggested_category": suggested_name,
                "suggested_name": suggested_name,
                "suggested_business_category": suggested_name,
                "business_category": suggested_name,
                "confidence": confidence,
                "reason": str(ai_row.get("reason") or "AI 判断为业务大类候选，建议人工确认后使用。").strip(),
                "match_type": "ai_business_category",
                "needs_review": True,
                "ai_ready": True,
                "no_action": False,
                "category_usage": CATEGORY_USAGE,
                "category_usage_label": CATEGORY_USAGE_LABEL,
                "calculation_policy": CATEGORY_CALCULATION_POLICY,
                "affects_customs_fields": False,
                "affects_tax_rate": False,
            }
        )
        applied_count += 1
    return applied_count


def _has_ai_candidate_text(row: dict) -> bool:
    text = " ".join(
        str(row.get(field) or "").strip()
        for field in ("product_name", "product_name_es", "import_name", "spec_model")
        if str(row.get(field) or "").strip()
    )
    if len(_normalize_text(text)) < 2:
        return False
    return True


def _suggestion_already_present(row: dict, suggested_name: str) -> bool:
    suggested_key = _normalize_text(suggested_name)
    if not suggested_key:
        return True
    return any(
        _normalize_text(row.get(field) or "") == suggested_key
        for field in ("product_name", "product_name_es", "import_name", "spec_model", "current_category")
    )


def _is_too_generic_ai_suggestion(row: dict, suggested_name: str) -> bool:
    suggestion_key = _normalize_text(suggested_name)
    banned_generic_names = {
        "原料",
        "配件",
        "杂货",
        "商品",
        "物料",
        "其他",
        "塑料制品",
        "电子配件",
    }
    if suggestion_key in {_normalize_text(name) for name in banned_generic_names}:
        return True
    source_text = _normalize_text(
        " ".join(
            str(row.get(field) or "")
            for field in ("product_name", "product_name_es", "import_name", "spec_model")
        )
    )
    return len(suggestion_key) <= 1 or (
        suggestion_key not in source_text and not _has_business_category_hint(source_text, suggestion_key)
    )


def _has_business_category_hint(source_text: str, suggestion_key: str) -> bool:
    hint_map = {
        "太阳眼镜": (
            "sunglass",
            "sunglasses",
            "gafasdesol",
            "lentesdesol",
            "anteojosdesol",
            "sunshadeeyewear",
            "墨镜",
            "墨迹",
            "太阳镜",
        ),
        "眼镜": (
            "sunglass",
            "sunglasses",
            "gafasdesol",
            "lentesdesol",
            "anteojos",
            "eyewear",
            "眼镜",
            "墨镜",
            "太阳镜",
        ),
        "服装": ("衣服", "上衣", "裤子", "长裤", "短裤", "服装", "ropa", "camisa", "pantalon", "pantalones"),
        "化妆品": ("口红", "唇膏", "化妆品", "化妆", "cosmetic", "cosmetics", "lipstick"),
        "包装材料": ("包装", "纸箱", "外箱", "carton", "box", "packing", "package", "packaging"),
    }
    for category_name, tokens in hint_map.items():
        if suggestion_key == _normalize_text(category_name):
            return any(token in source_text for token in tokens)
    return False


def _alias_hits(haystack: str, aliases: tuple[str, ...]) -> list[str]:
    normalized_haystack = _normalize_text(haystack)
    hits = []
    for alias in aliases:
        normalized_alias = _normalize_text(alias)
        if normalized_alias and normalized_alias in normalized_haystack:
            hits.append(alias)
    return hits


def _normalize_text(value: str) -> str:
    text = str(value or "").lower()
    return re.sub(r"[\s\-_()/\\,，.。;；:：]+", "", text)


def _to_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_limit(limit, default: int = 200, maximum: int = 1000) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _summarize_suggestions(items: list[dict]) -> dict:
    category_counts: dict[str, int] = {}
    needs_review_count = 0
    no_action_count = 0
    for item in items:
        category = item.get("suggested_business_category") or item.get("suggested_name") or ""
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        if item.get("needs_review"):
            needs_review_count += 1
        if item.get("no_action"):
            no_action_count += 1
    return {
        "item_count": len(items),
        "category_counts": category_counts,
        "business_category_counts": category_counts,
        "needs_review_count": needs_review_count,
        "business_category_candidate_count": needs_review_count,
        "normalization_candidate_count": needs_review_count,
        "no_action_count": no_action_count,
        "ai_ready_count": sum(1 for item in items if item.get("ai_ready")),
    }


def _category_preview_message(summary: dict) -> str:
    ai_message = summary.get("ai_message") or ""
    if ai_message:
        return f"商品业务归类预览已生成。{ai_message}"
    return "商品业务归类预览已生成。"
