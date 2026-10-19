"""批次利润测算服务。

销售输入和采购成本分开保存，利润测算不会改变成本批次状态或 ERP 推送状态。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json

try:
    import frappe as _frappe
except Exception:  # pragma: no cover - local unit tests do not need Frappe
    _frappe = None


SALES_INPUT_FIELDS = (
    "sales_quantity",
    "sales_unit_price",
    "sales_currency",
    "sales_fx_rate",
    "other_sales_expense_rmb",
)
PROFIT_RESULT_FIELDS = (
    "sales_amount",
    "sales_amount_rmb",
    "sales_cost_rmb",
    "gross_profit_rmb",
    "profit_rmb",
    "profit_margin",
    "profit_status",
)
CURRENCY_ALIASES = {
    "人民币": "RMB",
    "CNY": "RMB",
    "美元": "USD",
    "美金": "USD",
    "比索": "MXN",
    "墨西哥比索": "MXN",
}


def _number(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rounded(value: float) -> float:
    return round(float(value or 0), 6)


def _currency(value) -> str:
    text = str(value or "RMB").strip()
    return CURRENCY_ALIASES.get(text, text.upper() or "RMB")


def default_fx_rate(currency: str, *, fx_usd_to_rmb: float | None = None, fx_rmb_to_mxn: float | None = None) -> float:
    """返回销售币种换算成人民币的默认汇率。"""

    code = _currency(currency)
    if code == "RMB":
        return 1.0
    if code == "USD":
        return _number(fx_usd_to_rmb)
    if code == "MXN":
        rmb_to_mxn = _number(fx_rmb_to_mxn)
        return _rounded(1 / rmb_to_mxn) if rmb_to_mxn else 0.0
    return 0.0


def calculate_profit_row(
    row: dict,
    *,
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> dict:
    """按物料行计算销售额、毛利、利润和利润率。金额统一为 RMB。"""

    quantity = _number(row.get("sales_quantity"))
    unit_price = _number(row.get("sales_unit_price"))
    currency = _currency(row.get("sales_currency"))
    fx_rate = _number(row.get("sales_fx_rate")) or default_fx_rate(
        currency,
        fx_usd_to_rmb=fx_usd_to_rmb,
        fx_rmb_to_mxn=fx_rmb_to_mxn,
    )
    other_expense = _number(row.get("other_sales_expense_rmb"))
    cost_unit = _number(row.get("total_unit_rmb"))

    missing = []
    if quantity <= 0:
        missing.append("销售数量")
    if unit_price <= 0:
        missing.append("销售单价")
    if fx_rate <= 0:
        missing.append("销售汇率")
    if cost_unit <= 0:
        missing.append("综合成本单价")

    sales_amount = quantity * unit_price
    sales_amount_rmb = sales_amount * fx_rate
    sales_cost_rmb = quantity * cost_unit
    gross_profit = sales_amount_rmb - sales_cost_rmb
    profit = gross_profit - other_expense
    margin = (profit / sales_amount_rmb * 100) if sales_amount_rmb else 0.0
    status = "CALCULATED" if not missing else "PENDING"

    return {
        "sales_quantity": _rounded(quantity),
        "sales_unit_price": _rounded(unit_price),
        "sales_currency": currency,
        "sales_fx_rate": _rounded(fx_rate),
        "sales_amount": _rounded(sales_amount),
        "sales_amount_rmb": _rounded(sales_amount_rmb),
        "sales_cost_rmb": _rounded(sales_cost_rmb),
        "other_sales_expense_rmb": _rounded(other_expense),
        "gross_profit_rmb": _rounded(gross_profit),
        "profit_rmb": _rounded(profit),
        "profit_margin": _rounded(margin),
        "profit_status": status,
        "profit_status_label": "已测算" if status == "CALCULATED" else "待补销售数据",
        "profit_missing_fields": missing,
    }


def calculate_profit_rows(
    rows: list[dict],
    *,
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> tuple[list[dict], dict]:
    calculated_rows = [
        {
            **row,
            **calculate_profit_row(row, fx_usd_to_rmb=fx_usd_to_rmb, fx_rmb_to_mxn=fx_rmb_to_mxn),
        }
        for row in rows or []
    ]
    complete = [row for row in calculated_rows if row.get("profit_status") == "CALCULATED"]
    summary = {
        "item_count": len(calculated_rows),
        "calculated_count": len(complete),
        "pending_count": len(calculated_rows) - len(complete),
        "sales_amount_rmb": _rounded(sum(_number(row.get("sales_amount_rmb")) for row in complete)),
        "sales_cost_rmb": _rounded(sum(_number(row.get("sales_cost_rmb")) for row in complete)),
        "gross_profit_rmb": _rounded(sum(_number(row.get("gross_profit_rmb")) for row in complete)),
        "other_sales_expense_rmb": _rounded(sum(_number(row.get("other_sales_expense_rmb")) for row in complete)),
        "profit_rmb": _rounded(sum(_number(row.get("profit_rmb")) for row in complete)),
    }
    summary["profit_margin"] = _rounded(
        summary["profit_rmb"] / summary["sales_amount_rmb"] * 100 if summary["sales_amount_rmb"] else 0
    )
    summary["status"] = "CALCULATED" if summary["item_count"] and not summary["pending_count"] else "PENDING"
    summary["status_label"] = "已测算" if summary["status"] == "CALCULATED" else "待补销售数据"
    return calculated_rows, summary


def _resolve_batch(batch_name: str) -> str | None:
    if _frappe is None:
        return batch_name or None
    if _frappe.db.exists("Overseas Cost Batch", batch_name):
        return batch_name
    return _frappe.db.get_value("Overseas Cost Batch", {"batch_no": batch_name}, "name")


def _version_rates(batch_name: str, version_name: str | None) -> tuple[str | None, float, float]:
    resolved_version = version_name or _frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
    if not resolved_version:
        resolved_version = _frappe.db.get_value(
            "Overseas Cost Version", {"batch": batch_name}, "name", order_by="modified desc"
        )
    version = _frappe.db.get_value(
        "Overseas Cost Version", resolved_version, ["fx_usd_to_rmb", "fx_rmb_to_mxn"], as_dict=True
    ) or {}
    return resolved_version, _number(version.get("fx_usd_to_rmb")), _number(version.get("fx_rmb_to_mxn"))


def _write_audit(batch_name: str, version_name: str | None, row: dict, old_values: dict, new_values: dict) -> None:
    _frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_name,
            "version": version_name,
            "action_type": "BATCH_EDIT",
            "field_name": "profit_inputs",
            "row_no": row.get("row_no"),
            "old_value": json.dumps(old_values, ensure_ascii=False, default=str),
            "new_value": json.dumps(new_values, ensure_ascii=False, default=str),
            "operator_name": getattr(getattr(_frappe, "session", None), "user", ""),
            "action_remark": "保存利润测算销售数据",
        }
    ).insert(ignore_permissions=True)


def save_profit_inputs(batch_name: str, version_name: str | None, rows_payload) -> dict:
    """保存销售输入并计算利润。销售数据不使成本批次失效。"""

    if isinstance(rows_payload, str):
        rows_payload = json.loads(rows_payload or "[]")
    if not isinstance(rows_payload, list):
        raise ValueError(_("rows_payload 必须是数组。"))

    if _frappe is None:
        rows, summary = calculate_profit_rows(rows_payload)
        return {"ok": True, "dry_run": True, "items": rows, "summary": summary, "message": "已返回利润测算预览。"}

    resolved_batch = _resolve_batch(batch_name)
    if not resolved_batch:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    resolved_version, fx_usd_to_rmb, fx_rmb_to_mxn = _version_rates(resolved_batch, version_name)
    allowed = _frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": resolved_batch, "version": resolved_version, "is_excluded": 0},
        fields=["name", "row_no", "batch", "version", "total_unit_rmb", *SALES_INPUT_FIELDS],
        limit_page_length=10000,
    )
    allowed_by_name = {row["name"]: row for row in allowed}
    result_rows = []
    changed_count = 0
    for payload in rows_payload:
        item_name = payload.get("item_name") or payload.get("name")
        current = allowed_by_name.get(item_name)
        if not current:
            return {"ok": False, "message": f"物料不属于当前批次版本：{item_name or '未命名'}"}
        merged = {**current, **{field: payload.get(field) for field in SALES_INPUT_FIELDS if field in payload}}
        result = calculate_profit_row(merged, fx_usd_to_rmb=fx_usd_to_rmb, fx_rmb_to_mxn=fx_rmb_to_mxn)
        old_values = {field: current.get(field) for field in SALES_INPUT_FIELDS}
        new_values = {field: result.get(field) for field in SALES_INPUT_FIELDS}
        updates = {**new_values, **{field: result.get(field) for field in PROFIT_RESULT_FIELDS}}
        if any(str(old_values.get(field) or "") != str(new_values.get(field) or "") for field in SALES_INPUT_FIELDS):
            changed_count += 1
            _write_audit(resolved_batch, resolved_version, current, old_values, new_values)
        _frappe.db.set_value("Overseas Cost Item", item_name, updates, update_modified=True)
        result_rows.append({**current, **updates, **result})

    _frappe.db.commit()
    result_rows, summary = calculate_profit_rows(
        result_rows, fx_usd_to_rmb=fx_usd_to_rmb, fx_rmb_to_mxn=fx_rmb_to_mxn
    )
    return {
        "ok": True,
        "batch_name": resolved_batch,
        "version_name": resolved_version,
        "changed_count": changed_count,
        "items": result_rows,
        "summary": summary,
        "message": f"利润测算已保存，{changed_count} 行销售数据发生变化。",
    }
