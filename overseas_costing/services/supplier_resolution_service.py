"""ERP 供应商解析和受控创建的唯一入口。

只有启用中的 Supplier 精确匹配才会返回可写入的规范名称；模糊结果
始终只是人工选择候选。Supplier 仅能由用户在工作台明确确认后创建。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable

try:
    import frappe
except Exception:  # pragma: no cover - 纯单测可注入供应商列表
    frappe = None


MIN_CANDIDATE_SCORE = 0.72
HIGH_CONFIDENCE_SCORE = 0.90
HIGH_CONFIDENCE_MARGIN = 0.08
MAX_CANDIDATES = 5
MAX_SUPPLIER_NAME_LENGTH = 140
PLACEHOLDER_NAMES = {"-", "—", "－"}
# 「显式无供应商」标记：写入物料 supplier 字段的规范值。``-``/``—``/``－`` 是等价输入，
# 统一归一为 ``/`` 存储；与空串（＝未设置）语义不同。占位符供应商仍不能被创建成 Supplier。
NO_SUPPLIER_MARKER = "/"
NO_SUPPLIER_MARKERS = {"/", "-", "—", "－"}


class SupplierCreationError(ValueError):
    """可安全返回到工作台的供应商创建业务错误。"""

    def __init__(self, code: str, message: str, *, candidates: list[dict] | None = None):
        super().__init__(message)
        self.code = str(code or "SUPPLIER_CREATION_FAILED")
        self.candidates = list(candidates or [])


def _value(record: Any, fieldname: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(fieldname, default)
    return getattr(record, fieldname, default)


def normalize_supplier_text(value: Any) -> str:
    """用于名称匹配的稳定规范化，不改写 ERP 中的原始名称。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return "".join(character for character in text if character.isalnum())


def _supplier_rows(suppliers: Iterable[Any], *, include_disabled: bool = False) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for supplier in suppliers or []:
        disabled = bool(int(_value(supplier, "disabled", 0) or 0))
        if disabled and not include_disabled:
            continue
        name = str(_value(supplier, "name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            {
                "name": name,
                "supplier_name": str(_value(supplier, "supplier_name") or "").strip(),
                "disabled": int(disabled),
            }
        )
    return rows


def _active_supplier_rows(suppliers: Iterable[Any]) -> list[dict]:
    return _supplier_rows(suppliers)


def load_active_suppliers() -> list[dict]:
    """从当前 ERP 读取启用 Supplier；无 Frappe 环境时返回空集。"""

    if frappe is None:
        return []
    return _active_supplier_rows(
        frappe.get_all(
            "Supplier",
            filters={"disabled": 0},
            fields=["name", "supplier_name", "disabled"],
            order_by="supplier_name asc, name asc",
            limit_page_length=0,
        )
    )


def load_suppliers() -> list[dict]:
    """读取启用和停用 Supplier，用于创建前的完整查重。"""

    if frappe is None:
        return []
    return _supplier_rows(
        frappe.get_all(
            "Supplier",
            fields=["name", "supplier_name", "disabled"],
            order_by="supplier_name asc, name asc",
            limit_page_length=0,
        ),
        include_disabled=True,
    )


def _similarity(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    sequence_score = SequenceMatcher(None, query, candidate).ratio()
    if query in candidate or candidate in query:
        shorter, longer = min(len(query), len(candidate)), max(len(query), len(candidate))
        partial_score = MIN_CANDIDATE_SCORE + (1 - MIN_CANDIDATE_SCORE) * shorter / longer
        return max(sequence_score, partial_score)
    return sequence_score


def _exact_supplier_rows(raw_value: Any, suppliers: Iterable[Any]) -> list[dict]:
    normalized_raw = normalize_supplier_text(raw_value)
    if not normalized_raw:
        return []
    return [
        supplier
        for supplier in _supplier_rows(suppliers, include_disabled=True)
        if normalized_raw in {
            normalize_supplier_text(supplier["name"]),
            normalize_supplier_text(supplier["supplier_name"]),
        }
    ]


def resolve_supplier_reference(raw_value: Any, *, suppliers: Iterable[Any] | None = None) -> dict:
    """解析原始供应商文本；只有 EXACT 会给出 ``canonical_supplier``。"""

    raw = str(raw_value or "").strip()
    result = {
        "raw_value": raw,
        "status": "EMPTY" if not raw else "UNRESOLVED",
        "canonical_supplier": "",
        "candidates": [],
    }
    if not raw:
        return result

    rows = _active_supplier_rows(load_active_suppliers() if suppliers is None else suppliers)
    normalized_raw = normalize_supplier_text(raw)
    exact_matches = _exact_supplier_rows(raw, rows)
    if len(exact_matches) == 1:
        return {
            "raw_value": raw,
            "status": "EXACT",
            "canonical_supplier": exact_matches[0]["name"],
            "candidates": [],
        }
    if len(exact_matches) > 1:
        return {
            "raw_value": raw,
            "status": "AMBIGUOUS",
            "canonical_supplier": "",
            "candidates": [
                {
                    "name": supplier["name"],
                    "supplier_name": supplier["supplier_name"],
                    "score": 1.0,
                    "high_confidence": False,
                }
                for supplier in sorted(exact_matches, key=lambda item: item["name"])
            ][:MAX_CANDIDATES],
        }

    scored: list[dict] = []
    for supplier in rows:
        score = max(
            (_similarity(normalized_raw, normalize_supplier_text(alias)) for alias in (
                supplier["name"], supplier["supplier_name"]
            ) if alias),
            default=0.0,
        )
        if score < MIN_CANDIDATE_SCORE:
            continue
        scored.append(
            {
                "name": supplier["name"],
                "supplier_name": supplier["supplier_name"],
                "score": round(score, 4),
                "high_confidence": False,
            }
        )
    scored.sort(key=lambda candidate: (-candidate["score"], candidate["supplier_name"], candidate["name"]))
    candidates = scored[:MAX_CANDIDATES]
    if candidates:
        runner_up_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
        candidates[0]["high_confidence"] = bool(
            candidates[0]["score"] >= HIGH_CONFIDENCE_SCORE
            and candidates[0]["score"] - runner_up_score >= HIGH_CONFIDENCE_MARGIN
        )
        result["status"] = "SUGGESTED"
        result["candidates"] = candidates
    return result


def _default_supplier_group() -> str:
    if frappe is None:
        return ""
    configured = ""
    try:
        field = frappe.get_meta("Supplier").get_field("supplier_group")
        configured = str(getattr(field, "default", "") or "").strip()
    except Exception:
        configured = ""
    if configured and frappe.db.exists("Supplier Group", configured):
        return configured
    root_group = "All Supplier Groups"
    return root_group if frappe.db.exists("Supplier Group", root_group) else ""


def _existing_supplier_result(row: dict) -> dict:
    return {
        "ok": True,
        "created": False,
        "supplier": row["name"],
        "supplier_name": row.get("supplier_name") or row["name"],
        "supplier_group": "",
    }


def _validate_new_supplier_name(value: Any) -> str:
    supplier_name = str(value or "").strip()
    if (
        not supplier_name
        or supplier_name in PLACEHOLDER_NAMES
        or not normalize_supplier_text(supplier_name)
        or len(supplier_name) > MAX_SUPPLIER_NAME_LENGTH
    ):
        raise SupplierCreationError(
            "INVALID_SUPPLIER_NAME",
            f"供应商名称不能为空、不能使用占位符，且不能超过 {MAX_SUPPLIER_NAME_LENGTH} 个字符。",
        )
    return supplier_name


def _check_exact_supplier(supplier_name: str, suppliers: Iterable[Any]) -> dict | None:
    exact = _exact_supplier_rows(supplier_name, suppliers)
    if len(exact) > 1:
        raise SupplierCreationError("AMBIGUOUS_SUPPLIER", "同一名称对应多个 ERP Supplier，请先处理主数据冲突。")
    if not exact:
        return None
    if exact[0].get("disabled"):
        raise SupplierCreationError("DISABLED_SUPPLIER_EXISTS", "同名 ERP Supplier 已停用，请管理员启用或处理旧记录。")
    return _existing_supplier_result(exact[0])


def create_supplier(
    supplier_name: Any,
    *,
    confirm_similar: bool = False,
    suppliers: Iterable[Any] | None = None,
    supplier_loader=None,
    supplier_group: str | None = None,
    document_factory=None,
) -> dict:
    """在完整查重后创建最小 Supplier；重复请求返回既有规范 Supplier。"""

    normalized_name = _validate_new_supplier_name(supplier_name)
    loader = supplier_loader or load_suppliers
    supplier_rows = list(suppliers) if suppliers is not None else list(loader())
    existing = _check_exact_supplier(normalized_name, supplier_rows)
    if existing:
        return existing

    resolution = resolve_supplier_reference(normalized_name, suppliers=supplier_rows)
    high_confidence = [candidate for candidate in resolution["candidates"] if candidate.get("high_confidence")]
    if high_confidence and not confirm_similar:
        raise SupplierCreationError(
            "SIMILAR_SUPPLIER_CONFIRMATION_REQUIRED",
            "存在高置信近似供应商，请确认这是不同供应商后再创建。",
            candidates=high_confidence,
        )

    group = _default_supplier_group() if supplier_group is None else str(supplier_group or "").strip()
    if not group:
        raise SupplierCreationError("SUPPLIER_GROUP_REQUIRED", "未配置默认供应商组，无法快速创建 Supplier。")
    if frappe is None and document_factory is None:
        raise RuntimeError("当前未连接 Frappe。")
    factory = document_factory or frappe.get_doc
    document = factory({
        "doctype": "Supplier",
        "supplier_name": normalized_name,
        "supplier_group": group,
        "supplier_type": "Company",
        "disabled": 0,
    })
    try:
        document.insert(ignore_permissions=True)
    except Exception:
        # 两个请求并发通过预检时，唯一性约束可能只允许其中一个写入。
        # 重新读取并复用胜出的记录；没有同名记录则保留原始异常。
        refreshed = list(loader())
        concurrent = _check_exact_supplier(normalized_name, refreshed)
        if concurrent:
            return concurrent
        raise
    return {
        "ok": True,
        "created": True,
        "supplier": str(document.name or "").strip(),
        "supplier_name": str(getattr(document, "supplier_name", "") or normalized_name).strip(),
        "supplier_group": str(getattr(document, "supplier_group", "") or group).strip(),
    }


def validate_canonical_supplier(value: Any, *, suppliers: Iterable[Any] | None = None) -> str:
    """校验人工选中的规范 Supplier ID，不使用 supplier_name 别名解析。

    ``/`` 是业务确认的「显式无供应商」标记（``-``/``—``/``－`` 是等价输入，统一归一为
    ``/``），与「未设置」（空串）区分：标记过的行明确表达过"这行就是没有供应商"。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if text in NO_SUPPLIER_MARKERS:
        return NO_SUPPLIER_MARKER
    normalized_value = normalize_supplier_text(text)
    rows = _active_supplier_rows(load_active_suppliers() if suppliers is None else suppliers)
    matches = [
        supplier["name"]
        for supplier in rows
        if normalize_supplier_text(supplier["name"]) == normalized_value
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(_("供应商必须从启用中的 ERP 供应商列表选择。"))


def effective_push_supplier(value: Any) -> str:
    """ERP 推送边界的供应商归一：显式无供应商标记等价于空。

    ``/`` 是「这行确认没有供应商」的终态（见 :func:`validate_canonical_supplier`）：
    推送时既不拦截标记行，也绝不把标记当真实供应商名发给 ERP；采购订单的
    供应商由站点默认供应商配置兜底（见 ``erp_client._resolve_supplier``）。
    """

    text = str(value or "").strip()
    if not text or text in NO_SUPPLIER_MARKERS:
        return ""
    return text


def _metadata(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def supplier_provenance_state(item: dict) -> dict:
    """区分新模板缺供应商与旧模板默认供应商兼容场景。"""

    metadata = _metadata(item.get("extra_json"))
    supplier = str(item.get("supplier") or "").strip()
    field_present = metadata.get("supplier_field_present") is True
    legacy_without_supplier_column = not field_present
    missing_new_supplier = field_present and not supplier
    return {
        "supplier": supplier,
        "raw_value": str(metadata.get("supplier_raw_value") or "").strip(),
        "match_status": str(metadata.get("supplier_match_status") or "").strip(),
        "supplier_field_present": field_present,
        "requires_explicit_supplier": missing_new_supplier,
        "legacy_default_allowed": legacy_without_supplier_column and not supplier,
        "warning": "历史兼容默认供应商" if legacy_without_supplier_column and not supplier else "",
    }
