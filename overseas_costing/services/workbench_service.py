"""海外成本工作台的轻量查询与任务分类。"""

from __future__ import annotations

import json
import re

try:
    import frappe
except Exception:  # pragma: no cover - 本地纯函数测试时保持可导入
    frappe = None

from overseas_costing.services import batch_service, cost_preview_service, cost_review_service, fee_service


ISSUE_ORDER = ("purchase", "logistics", "calculation", "erp_failed")
FIELD_GROUPS = {
    "basic": ("A", "H"),
    "purchase": ("I", "P"),
    "logistics": ("Q", "Z"),
    "tax": ("AA", "AJ"),
    "total": ("AK", "BE"),
}
FIXED_ITEM_FIELDS = ("material_code", "product_name")
TRANSPORT_IDENTIFIER_FIELDS = ("waybill_no", "sea_bill_no", "container_no")
SAFE_SORT_FIELDS = {
    "row_no",
    "material_code",
    "product_name",
    "quantity",
    "goods_value",
    "total_cost_rmb",
    "total_unit_rmb",
}
RESULT_PREVIEW_ITEM_FIELDS = [
    "name",
    "row_no",
    "material_code",
    "product_name",
    "spec_model",
    "unit",
    "purchase_uom",
    "unit_price",
    "unit_price_uom",
    "purchase_currency",
    "quantity",
    "goods_value",
    "freight_alloc_rmb",
    "mexico_customs_mxn",
    "mexico_customs_rmb",
    "mexico_customs_usd",
    "import_tax_total",
    *batch_service.TAX_COMPONENT_FIELDS,
    "total_cost_rmb",
    "total_unit_rmb",
    "extra_json",
    "derived_json",
]
RESULT_PREVIEW_OUTPUT_FIELDS = (
    "material_code",
    "product_name",
    "spec_model",
    "unit_price",
    "adopted_price",
    "purchase_currency",
    "quantity",
    "freight_alloc_rmb",
    "tax_alloc_rmb",
    "clearance_alloc_rmb",
    "total_unit_rmb",
)
RESULT_PREVIEW_FREIGHT_KEYWORDS = (
    "freight",
    "ocean",
    "shipping",
    "transport",
    "logistics",
    "运费",
    "运输",
    "物流",
    "海运",
)
RESULT_PREVIEW_TAX_KEYWORDS = ("tax", "tariff", "duty", "igi", "iva", "关税", "税费")
RESULT_PREVIEW_CLEARANCE_KEYWORDS = (
    "clearance",
    "customs",
    "broker",
    "清关",
    "报关",
)


def _item_count_fields_for_frappe() -> list:
    """Use the aggregate field syntax supported by the active Frappe major version."""

    version = str(getattr(frappe, "__version__", "") or "")
    match = re.search(r"\d+", version)
    if match and int(match.group()) < 16:
        return ["count(name) as total"]
    return [{"COUNT": "name", "as": "total"}]


def operation_error(stage: str, scope: str, reason: str, next_action: str) -> dict:
    """Return the stable error shape consumed by workbench actions."""
    return {
        "ok": False,
        "error": {
            "stage": str(stage or "操作"),
            "scope": str(scope or "当前范围"),
            "reason": str(reason or "操作失败"),
            "next_action": str(next_action or "刷新后重试或查看操作记录"),
        },
    }


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_page(page, page_length, *, default_length: int = 30) -> tuple[int, int]:
    try:
        normalized_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        normalized_page = 1
    try:
        normalized_length = min(100, max(10, int(page_length or default_length)))
    except (TypeError, ValueError):
        normalized_length = default_length
    return normalized_page, normalized_length


def classify_batch(batch: dict, stats: dict) -> dict:
    """Legacy stats-only helper; production lists use saved-result readiness."""
    approval_state = str(
        (batch.get("source_status") or {}).get("purchase_approval_sync_state") or ""
    ).lower()
    status = str(batch.get("status") or "").lower()
    writeback = str(batch.get("writeback_status") or "").lower()
    issues = []
    if (
        approval_state in {"missing", "pending", "excluded"}
        or not batch.get("subsidiary_code")
        or stats.get("missing_purchase_count", 0)
        or not stats.get("item_count", 0)
    ):
        issues.append("purchase")
    if stats.get("missing_logistics_count", 0):
        issues.append("logistics")
    if status in {"draft", "dirty", "writeback failed"} or _as_float(
        batch.get("actual_total_cost_rmb") or batch.get("estimated_total_cost_rmb")
    ) <= 0:
        issues.append("calculation")
    if "fail" in writeback:
        issues.append("erp_failed")
    primary = next((code for code in ISSUE_ORDER if code in issues), "ready")
    action = {
        "purchase": "supplement",
        "logistics": "supplement",
        "calculation": "recalculate",
        "erp_failed": "erp_retry",
        "ready": "view",
    }[primary]
    return {"issue_codes": issues, "primary_issue": primary, "primary_action": action}


def filter_batches_for_task(rows: list[dict], task: str, review_status: str = "pending") -> list[dict]:
    if task == "pending":
        return [
            row for row in rows
            if (
                str(row.get("remediation_state") or "").lower() == "returned"
                or (
                    row.get("review_state") == "processing"
                    and (
                        not row.get("cost_review_started")
                        or row.get("cost_review_eligible", True) is False
                    )
                )
            )
        ]
    if task == "cost":
        if str(review_status).lower() == "confirmed":
            return [row for row in rows if row.get("review_state") == "confirmed"]
        return [
            row for row in rows
            if (
                row.get("cost_review_started")
                or str(row.get("remediation_state") or "").lower() in {"returned", "resubmitted"}
            )
            and row.get("cost_review_eligible", True) is not False
            and row.get("review_state") != "confirmed"
        ]
    if task == "erp":
        # 队列保留待推送/失败，以及全部已确认批次（含已推送成功的 Success）——
        # 已推送成功的批次也要留在队列里，才能在 ERP 队列直接看到推送结果并回访远端单据。
        return [
            row
            for row in rows
            if str(row.get("writeback_status") or "").lower() in {"pending", "failed"}
            or str(row.get("confirm_status") or "").lower() == "confirmed"
        ]
    return list(rows)


def select_item_columns(columns: list[dict], group: str) -> list[dict]:
    if group == "all":
        return list(columns)
    start, end = FIELD_GROUPS.get(group, FIELD_GROUPS["basic"])
    start_index = next(index for index, column in enumerate(columns) if column["excel_col"] == start)
    end_index = next(index for index, column in enumerate(columns) if column["excel_col"] == end)
    return [
        column
        for index, column in enumerate(columns)
        if column["fieldname"] in FIXED_ITEM_FIELDS or start_index <= index <= end_index
    ]


def normalize_item_query(page, page_length, group, sort_by, sort_order) -> dict:
    page, page_length = normalize_page(page, page_length, default_length=50)
    group = group if group in {*FIELD_GROUPS, "all"} else "basic"
    sort_by = sort_by if sort_by in SAFE_SORT_FIELDS else "row_no"
    sort_order = "desc" if str(sort_order).lower() == "desc" else "asc"
    return {
        "page": page,
        "page_length": page_length,
        "group": group,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }


def _load_result_preview_json(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _round_result_amount(value):
    return round(_as_float(value), 6)


def _rule_bucket(rule: dict) -> str:
    text = " ".join(
        str(rule.get(fieldname) or "")
        for fieldname in ("rule_code", "expense_category")
    ).lower()
    if any(keyword in text for keyword in RESULT_PREVIEW_TAX_KEYWORDS):
        return "tax"
    if any(keyword in text for keyword in RESULT_PREVIEW_CLEARANCE_KEYWORDS):
        return "clearance"
    if any(keyword in text for keyword in RESULT_PREVIEW_FREIGHT_KEYWORDS):
        return "freight"
    return "other"


def build_batch_result_preview_item(item: dict, *, calculated: bool) -> dict:
    """将一行明细收敛为快捷结果口径，不把普通杂费冒充清关费。"""

    from overseas_costing.services.material_input_service import present_material_row

    presented = present_material_row(item)

    result = {
        "name": item.get("name") or "",
        "row_no": item.get("row_no"),
        "material_code": item.get("material_code") or "",
        "product_name": item.get("product_name") or "",
        "spec_model": item.get("spec_model") or "",
        "unit_price": item.get("unit_price"),
        "adopted_price": presented.get("adopted_price") or {},
        "purchase_currency": item.get("purchase_currency") or "",
        "quantity": item.get("quantity"),
        "freight_alloc_rmb": None,
        "tax_alloc_rmb": None,
        "clearance_alloc_rmb": None,
        "unlisted_other_cost_rmb": None,
        "total_unit_rmb": None,
    }
    if not calculated:
        return result

    saved = batch_service._saved_expense_classification(item)
    if saved is not None:
        result.update({"freight_alloc_rmb": _round_result_amount(saved["freight"]),
                       "tax_alloc_rmb": _round_result_amount(saved["tax"]),
                       "clearance_alloc_rmb": _round_result_amount(saved["clearance"]),
                       "unlisted_other_cost_rmb": _round_result_amount(saved["other"]),
                       "total_unit_rmb": _round_result_amount(item.get("total_unit_rmb"))})
        return result

    metadata = _load_result_preview_json(item.get("derived_json"))
    direct_customs = metadata.get("direct_customs")
    if not isinstance(direct_customs, dict):
        direct_customs = {}
    allocated_rules = metadata.get("allocated_rules")
    if not isinstance(allocated_rules, list):
        allocated_rules = []

    fx_rmb_to_mxn = _as_float(metadata.get("fx_rmb_to_mxn"))
    fx_usd_to_rmb = _as_float(metadata.get("fx_usd_to_rmb"))
    tax_mxn = _as_float(item.get("import_tax_total"))
    if not tax_mxn:
        tax_mxn = sum(_as_float(item.get(fieldname)) for fieldname in batch_service.TAX_COMPONENT_FIELDS)
    if not tax_mxn:
        tax_mxn = _as_float(direct_customs.get("tax_mxn"))

    customs_source_type = str(direct_customs.get("source_type") or "").strip().lower()
    direct_customs_rmb = _as_float(direct_customs.get("amount_rmb"))
    if not direct_customs_rmb:
        direct_customs_rmb = _as_float(item.get("mexico_customs_rmb"))
    if not direct_customs_rmb and _as_float(item.get("mexico_customs_mxn")) and fx_rmb_to_mxn:
        direct_customs_rmb = _as_float(item.get("mexico_customs_mxn")) / fx_rmb_to_mxn
    if not direct_customs_rmb and _as_float(item.get("mexico_customs_usd")) and fx_usd_to_rmb:
        direct_customs_rmb = _as_float(item.get("mexico_customs_usd")) * fx_usd_to_rmb

    has_component_breakdown = customs_source_type == "customs_components"
    customs_total_cannot_be_split = bool(direct_customs_rmb and not has_component_breakdown and not tax_mxn)
    tax_from_customs = (
        None
        if customs_total_cannot_be_split or (tax_mxn and not fx_rmb_to_mxn)
        else _as_float(tax_mxn) / fx_rmb_to_mxn
        if tax_mxn
        else 0.0
    )
    direct_clearance_rmb = None
    service_mxn = _as_float(direct_customs.get("service_mxn"))
    if service_mxn:
        direct_clearance_rmb = service_mxn / fx_rmb_to_mxn if fx_rmb_to_mxn else None
    elif has_component_breakdown:
        direct_clearance_rmb = 0.0
    elif direct_customs_rmb and tax_from_customs is not None:
        direct_clearance_rmb = max(direct_customs_rmb - tax_from_customs, 0.0)
    elif not direct_customs_rmb:
        direct_clearance_rmb = 0.0

    buckets = {"freight": 0.0, "tax": 0.0, "clearance": 0.0, "other": 0.0}
    rule_buckets = []
    for rule in allocated_rules:
        if not isinstance(rule, dict):
            continue
        bucket = _rule_bucket(rule)
        rule_buckets.append(bucket)
        buckets[bucket] += _as_float(rule.get("allocated_rmb"))

    freight_value_is_missing = item.get("freight_alloc_rmb") in (None, "")
    freight_rmb = (
        None
        if freight_value_is_missing and "freight" not in rule_buckets
        else buckets["freight"]
        if allocated_rules
        else _as_float(item.get("freight_alloc_rmb"))
    )
    if allocated_rules and not freight_rmb and not freight_value_is_missing:
        freight_rmb = _as_float(item.get("freight_alloc_rmb"))
    tax_rmb = None if tax_from_customs is None else tax_from_customs + buckets["tax"]
    clearance_rmb = None if direct_clearance_rmb is None else direct_clearance_rmb + buckets["clearance"]
    other_rmb = buckets["other"] + (direct_customs_rmb if customs_total_cannot_be_split else 0.0)

    if freight_rmb is not None and tax_rmb is not None and clearance_rmb is not None:
        expected_extra = max(_as_float(item.get("total_cost_rmb")) - _as_float(item.get("goods_value")), 0.0)
        known_extra = freight_rmb + tax_rmb + clearance_rmb + other_rmb
        if expected_extra - known_extra > 0.000001:
            other_rmb += expected_extra - known_extra

    result.update(
        {
            "freight_alloc_rmb": None if freight_rmb is None else _round_result_amount(freight_rmb),
            "tax_alloc_rmb": None if tax_rmb is None else _round_result_amount(tax_rmb),
            "clearance_alloc_rmb": None if clearance_rmb is None else _round_result_amount(clearance_rmb),
            "unlisted_other_cost_rmb": _round_result_amount(other_rmb),
            "total_unit_rmb": (
                None
                if item.get("total_unit_rmb") in (None, "")
                else _round_result_amount(item.get("total_unit_rmb"))
            ),
        }
    )
    return result


def build_batch_result_preview_payload(
    *,
    batch: dict,
    version: dict,
    items: list[dict],
    page=1,
    page_length=20,
) -> dict:
    page, page_length = normalize_page(page, page_length, default_length=20)
    calculated = bool(version.get("calculated_at"))
    ordered_items = sorted(items, key=lambda item: (_as_float(item.get("row_no")), str(item.get("name") or "")))
    result_items = [build_batch_result_preview_item(item, calculated=calculated) for item in ordered_items]

    purchase_totals_by_currency: dict[str, float] = {}
    for item in ordered_items:
        currency = str(item.get("purchase_currency") or "").strip().upper()
        purchase_totals_by_currency[currency] = purchase_totals_by_currency.get(currency, 0.0) + _as_float(
            item.get("goods_value")
        )
    purchase_totals = [
        {"currency": currency, "amount": _round_result_amount(amount)}
        for currency, amount in purchase_totals_by_currency.items()
    ]
    total_quantity = sum(_as_float(item.get("quantity")) for item in ordered_items)
    saved_units = [_load_result_preview_json(item.get("derived_json")) for item in ordered_items]
    if saved_units and all(meta.get("calculation_schema") == 2 for meta in saved_units):
        total_quantity = sum(_as_float(meta.get("shipping_quantity")) for meta in saved_units)
    weighted_total_unit_rmb = None
    has_all_total_costs = all(item.get("total_cost_rmb") not in (None, "") for item in ordered_items)
    if calculated and total_quantity and has_all_total_costs:
        weighted_total_unit_rmb = _round_result_amount(
            sum(_as_float(item.get("total_cost_rmb")) for item in ordered_items) / total_quantity
        )

    def result_total(fieldname: str):
        values = [item.get(fieldname) for item in result_items]
        if not calculated or any(value is None for value in values):
            return None
        return _round_result_amount(sum(_as_float(value) for value in values))

    tax_total = result_total("tax_alloc_rmb")
    clearance_total = result_total("clearance_alloc_rmb")
    result_totals = {
        "freight": result_total("freight_alloc_rmb"),
        "tax": tax_total,
        "clearance": clearance_total,
    }
    calculation_status = (
        "pending"
        if not calculated
        else "partial"
        if weighted_total_unit_rmb is None or any(value is None for value in result_totals.values())
        else "ready"
    )
    total = len(result_items)
    offset = (page - 1) * page_length
    identifiers = {
        field: str(batch.get(field) or "").strip()
        for field in (*TRANSPORT_IDENTIFIER_FIELDS, "customs_no")
    }
    return {
        "ok": True,
        "batch_name": batch.get("name") or "",
        "version_name": version.get("name") or "",
        "summary": {
            "batch_no": batch.get("batch_no") or batch.get("name") or "",
            "logistics_no": next((identifiers[field] for field in TRANSPORT_IDENTIFIER_FIELDS if identifiers[field]), ""),
            **identifiers,
            "purchase_totals": purchase_totals,
            "total_freight_rmb": result_totals["freight"],
            "total_tax_rmb": tax_total,
            "total_clearance_rmb": clearance_total,
            "total_quantity": _round_result_amount(total_quantity),
            "weighted_total_unit_rmb": weighted_total_unit_rmb,
            "unlisted_other_cost_rmb": result_total("unlisted_other_cost_rmb"),
            "calculation_status": calculation_status,
        },
        "items": [
            {fieldname: item.get(fieldname) for fieldname in RESULT_PREVIEW_OUTPUT_FIELDS}
            for item in result_items[offset : offset + page_length]
        ],
        "total": total,
        "page": page,
        "page_length": page_length,
        "page_count": (total + page_length - 1) // page_length,
    }


def get_batch_result_preview(batch_name: str, page=1, page_length=20) -> dict:
    page, page_length = normalize_page(page, page_length, default_length=20)
    if frappe is None:
        return build_batch_result_preview_payload(
            batch={"name": batch_name, "batch_no": batch_name},
            version={},
            items=[],
            page=page,
            page_length=page_length,
        ) | {"dry_run": True}

    batch_doc_name = batch_service._resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    version_name = batch_service._resolve_version_name(batch_doc_name, None)
    if not version_name:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有版本。"}

    batch_rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"name": batch_doc_name},
        fields=["name", "batch_no", *TRANSPORT_IDENTIFIER_FIELDS, "customs_no", "current_version", "status"],
        limit_page_length=1,
    )
    version_rows = frappe.get_all(
        "Overseas Cost Version",
        filters={"name": version_name, "batch": batch_doc_name},
        fields=["name", "calculated_at"],
        limit_page_length=1,
    )
    if not batch_rows or not version_rows:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次或版本已不存在。"}

    items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": version_name, "is_excluded": 0},
        fields=RESULT_PREVIEW_ITEM_FIELDS,
        order_by="row_no asc, name asc",
        limit_page_length=0,
    )
    return build_batch_result_preview_payload(
        batch=batch_rows[0],
        version=version_rows[0],
        items=items,
        page=page,
        page_length=page_length,
    )


# ERP 状态筛选项 → writeback_status 取值域（Not Started / Pending / Failed / Success）的映射。
# 「未推送」精确对应 Not Started；「待推送」按业务语义覆盖未开始与待推送两种未成功中间态，
# 否则 Not Started 的批次在任一筛选项下都筛不出来（历史上就是精确等值导致的口径空洞）。
ERP_WRITEBACK_STATUS_GROUPS = {
    "not_started": {"not started"},
    "pending": {"not started", "pending"},
    "failed": {"failed"},
    "success": {"success"},
}


def _matches_workbench_filters(row: dict, filters: dict) -> bool:
    subsidiary = str(filters.get("subsidiary_code") or "")
    if subsidiary and str(row.get("subsidiary_code") or "") != subsidiary:
        return False
    calculation_status = str(filters.get("calculation_status") or "").lower()
    if calculation_status and str(row.get("status") or "").lower() != calculation_status:
        return False
    erp_status = str(filters.get("erp_status") or "").strip().lower()
    if erp_status:
        allowed = ERP_WRITEBACK_STATUS_GROUPS.get(erp_status, {erp_status})
        if str(row.get("writeback_status") or "").strip().lower() not in allowed:
            return False
    return True


def _load_review_readiness(batches: list[dict]) -> dict[str, dict]:
    """Load authorized batch inputs in chunks and check each local source context.

    Exact (batch, current_version) grouping prevents foreign or historical rows
    from supplying readiness. Grouping columns never enter the cost input hash.
    Reads neither backfill legacy fields nor recalculate persisted results.
    """
    result = {}
    chunk_size = 200
    for offset in range(0, len(batches), chunk_size):
        chunk = batches[offset:offset + chunk_size]
        names = [row["name"] for row in chunk]
        versions = [row["current_version"] for row in chunk if row.get("current_version")]
        version_rows, item_rows, rule_rows, evidence_rows, component_rows, audit_rows = [], [], [], [], [], []
        review_round_rows, review_issue_rows = [], []
        latest_rounds = {}
        if frappe is not None and versions:
            filters = {"batch": ["in", names], "version": ["in", versions]}
            version_rows = frappe.get_all("Overseas Cost Version",
                filters={"batch": ["in", names], "name": ["in", versions]},
                fields=["name", "batch", "version_code", "status", "fx_usd_to_rmb", "fx_rmb_to_mxn",
                        "calculated_at", "extra_json", "summary_snapshot_json"], limit_page_length=0)
            item_rows = frappe.get_all("Overseas Cost Item", filters={**filters, "is_excluded": 0},
                fields=["batch", "version", *cost_preview_service.COST_INPUT_FIELDS, *cost_review_service.SAVED_ITEM_OUTPUT_FIELDS],
                order_by="row_no asc, name asc", limit_page_length=0)
            rule_rows = frappe.get_all("Overseas Cost Allocation Rule", filters=filters,
                fields=fee_service._rule_fields(), order_by="priority_no asc, modified asc", limit_page_length=0)
            evidence_rows = frappe.get_all("Overseas Cost Fee Evidence", filters=filters,
                fields=["batch", "version", "fee_rule", "evidence_role", "validation_status"], limit_page_length=0)
            component_rows = frappe.get_all(
                "Overseas Cost Fee SKU Component",
                filters={**filters, "status": "CONFIRMED", "is_active": 1},
                fields=[
                    "name", "batch", "version", "fee_rule", "logical_fee_key", "evidence", "attachment",
                    "item", "stable_line_key", "component_type", "tax_code", "hs_code", "currency",
                    "original_amount", "amount_rmb", "exchange_rate", "allocation_basis",
                    "source_evidence_json", "accounting_role", "cost_effect", "reverses_component",
                    "status", "is_active",
                ],
                limit_page_length=0,
            )
            confirmed_names = [row["name"] for row in chunk if str(row.get("confirm_status") or "").lower() == "confirmed"]
            if confirmed_names:
                audit_rows = frappe.get_all("Overseas Cost Audit Log",
                    filters={"batch": ["in", confirmed_names], "version": ["in", versions],
                             "action_type": "BATCH_EDIT", "field_name": "confirm_status"},
                    fields=["batch", "version", "creation", "new_value"],
                    order_by="creation desc", limit_page_length=0)
            review_round_rows = frappe.get_all(
                "Overseas Cost Review Round",
                filters={"batch": ["in", names]},
                fields=["name", "batch", "version", "round_no", "status", "trial_signature",
                        "returned_by", "returned_at", "resubmitted_by", "resubmitted_at",
                        "resolved_by", "resolved_at", "creation", "modified"],
                order_by="batch asc, round_no desc, creation desc",
                limit_page_length=0,
            )
            for review_round in review_round_rows:
                latest_rounds.setdefault(review_round.get("batch"), dict(review_round))
            round_names = [row.get("name") for row in latest_rounds.values() if row.get("name")]
            if round_names:
                review_issue_rows = frappe.get_all(
                    "Overseas Cost Review Issue",
                    filters={"review_round": ["in", round_names]},
                    fields=["name", "review_round", "status"],
                    order_by="review_round asc, order_no asc",
                    limit_page_length=0,
                )
        by_version = {(row["batch"], row["name"]): dict(row) for row in version_rows}
        groups = []
        for rows in (item_rows, rule_rows, evidence_rows, component_rows):
            grouped = {}
            for row in rows:
                grouped.setdefault((row.get("batch"), row.get("version")), []).append(row)
            groups.append(grouped)
        reviewed = {}
        for row in audit_rows:
            if _load_result_preview_json(row.get("new_value")).get("confirm_status") == "Confirmed":
                reviewed.setdefault((row.get("batch"), row.get("version")), row.get("creation"))
        issues_by_round = {}
        for issue in review_issue_rows:
            issues_by_round.setdefault(issue.get("review_round"), []).append(dict(issue))
        from overseas_costing.services.review_communication_service import project_review_state
        for batch in chunk:
            key = (batch["name"], batch.get("current_version"))
            version = by_version.get(key, {})
            version["reviewed_at"] = reviewed.get(key)
            from overseas_costing.services.effective_source_values import batch_source_context
            context = batch_source_context(batch['name'], batch.get('current_version'))
            readiness = cost_review_service.evaluate_review_readiness(
                batch=batch, version=version, items=groups[0].get(key, []),
                fees=groups[1].get(key, []), evidence=groups[2].get(key, []),
                fee_components=groups[3].get(key, []), source_context=context)
            review_round = latest_rounds.get(batch["name"])
            projection = project_review_state(
                review_round,
                issues_by_round.get(review_round.get("name"), []) if review_round else [],
            )
            result[batch["name"]] = cost_review_service.apply_remediation_projection(readiness, projection)
    return result


def _classified_batches(filters: dict | None = None) -> list[dict]:
    filters = dict(filters or {})
    query = dict(filters)
    # 分类和异常统计必须基于完整结果集，不能在 2000 条时静默截断。
    query["candidate_limit"] = 0
    batches = batch_service.get_batch_list(query).get("items") or []
    batches = [
        row
        for row in batches
        if not (
            (row.get("source_status") or {}).get("invalid_business")
            and (row.get("source_status") or {}).get("invalid_business_scope") == "source_approval"
        )
    ]
    readiness = _load_review_readiness(batches)
    classified = [
        {**row, **readiness[row["name"]]}
        for row in batches
    ]
    return [row for row in classified if _matches_workbench_filters(row, filters)]


def get_workbench_batches(
    filters: dict | None = None,
    task: str = "pending",
    page=1,
    page_length=30,
) -> dict:
    filters = dict(filters or {})
    classified = filter_batches_for_task(_classified_batches(filters), task, filters.get("review_status") or "pending")
    if task == "pending" and filters.get("issue"):
        classified = [row for row in classified if filters["issue"] in row["issue_codes"]]
    if task == "cost" and filters.get("review_warning") in {"estimated", "evidence_missing"}:
        warning_code = {"estimated": "ESTIMATED_AMOUNT", "evidence_missing": "EVIDENCE_MISSING"}[filters["review_warning"]]
        classified = [row for row in classified if any(warning["code"] == warning_code for warning in row["review_warnings"])]
    if task == "erp":
        classified = [{**row, "primary_action": "erp_retry"}
                      if "fail" in str(row.get("writeback_status") or "").lower() else row for row in classified]
    page, page_length = normalize_page(page, page_length)
    offset = (page - 1) * page_length
    return {
        "ok": True,
        "items": classified[offset : offset + page_length],
        "total": len(classified),
        "page": page,
        "page_length": page_length,
    }


def get_workbench_summary(filters: dict | None = None, task: str = "pending") -> dict:
    filters = dict(filters or {})
    # Cards summarize the base population, not the currently selected card.
    base_filters = {key: value for key, value in filters.items() if key not in {"issue", "review_status", "review_warning"}}
    rows = _classified_batches(base_filters)
    counts = {"purchase": 0, "logistics": 0, "calculation": 0, "erp_failed": 0}
    for row in filter_batches_for_task(rows, "pending"):
        for code in row["issue_codes"]:
            counts[code] += 1
    if task == "erp":
        counts["erp_failed"] = sum("erp_failed" in row["issue_codes"] for row in filter_batches_for_task(rows, "erp"))
    review_rows = filter_batches_for_task(rows, "cost", filters.get("review_status") or "pending")
    review_counts = {"pending": sum(row["review_state"] == "ready" for row in rows),
                     "confirmed": sum(row["review_state"] == "confirmed" for row in rows)}
    for key, warning_code in (("estimated", "ESTIMATED_AMOUNT"), ("evidence_missing", "EVIDENCE_MISSING")):
        review_counts[key] = sum(any(warning["code"] == warning_code for warning in row["review_warnings"]) for row in review_rows)
    return {"ok": True, "counts": counts, "review_counts": review_counts}


def present_saved_sku_result(row: dict, transport_mode: str = "") -> dict:
    item = dict(row)
    from overseas_costing.services.material_input_service import present_material_row
    current = present_material_row(item)
    price = current.get('adopted_price') or {}
    if price:
        item['adopted_price'] = price
    if current.get('purchase_price_source'):
        item['purchase_price_source'] = current['purchase_price_source']
    if (item.get('source_context') or {}).get('root_kind') == 'expense':
        item.update(quantity=current.get('effective_shipping_quantity'),
                    actual_shipped_qty=current.get('effective_shipping_quantity'),
                    unit=current.get('effective_shipping_uom'),
                    packing_quantity=current.get('actual_shipped_qty'),
                    unit_price=price.get('value'),purchase_currency=price.get('currency'),
                    goods_value=current.get('shipment_value_rmb'),adopted_price=price)
    elif (current.get('adopted_price') or {}).get('source_type') == 'purchase_total_derived':
        item.update(
            unit_price=price.get('value'),
            purchase_currency=price.get('currency'),
            unit_price_uom=price.get('unit'),
            adopted_price=price,
        )
    if transport_mode in {"SEA", "AIR", "EXPRESS"}:
        item["transport_mode"] = transport_mode
    derived = _load_result_preview_json(item.get("derived_json"))
    if derived.get("calculation_schema") == 2:
        shipping = derived.get("shipping_unit_cost") or {}
        pricing = derived.get("purchase_pricing_unit_cost") or {}
        item["shipping_unit_label"] = shipping.get("uom") or "单位待补"
        item["purchase_pricing_unit_display"] = f"{pricing['amount_rmb']} / {pricing['uom']}" if pricing else "单位换算未明确"
        item["calculated_customs_rmb"] = derived.get("mexico_customs_rmb")
        item["calculated_tax_rmb"] = derived.get("tax_allocated_rmb")
        item["calculated_direct_rmb"] = derived.get("direct_fees_rmb")
        item["calculated_allocated_rmb"] = derived.get("allocated_fees_rmb")
    return item


def _load_sku_batch_meta(batch_name):
    return frappe.db.get_value("Overseas Cost Batch", batch_name, ["transport_mode", "current_version", "status"], as_dict=True) or {}


def get_batch_items_page(
    batch_name: str,
    version_name: str | None = None,
    keyword: str = "",
    page=1,
    page_length=50,
    field_group: str = "basic",
    sort_by: str = "row_no",
    sort_order: str = "asc",
) -> dict:
    query = normalize_item_query(page, page_length, field_group, sort_by, sort_order)
    columns = select_item_columns(batch_service.EXCEL_COLUMNS, query["group"])
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "columns": columns,
            "items": [],
            "total": 0,
            "page": query["page"],
            "page_length": query["page_length"],
            "page_count": 0,
            "field_group": query["group"],
        }

    batch_doc_name = batch_service._resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    resolved_version = batch_service._resolve_version_name(batch_doc_name, version_name)
    if not resolved_version:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有版本。"}

    db_filters, or_filters = batch_service._build_item_query_args(
        batch_doc_name,
        resolved_version,
        keyword=keyword,
    )
    display_fieldnames = [column["fieldname"] for column in columns]
    fieldnames = list(
        dict.fromkeys(
            ["name", "row_no", "excel_row_no", "modified", "derived_json", "extra_json", "source_doc_no", "dingtalk_instance_id"]
            + batch_service._persistent_item_fieldnames(display_fieldnames)
        )
    )
    count_rows = frappe.get_all(
        "Overseas Cost Item",
        filters=db_filters,
        or_filters=or_filters,
        fields=_item_count_fields_for_frappe(),
        limit_page_length=1,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)
    items = frappe.get_all(
        "Overseas Cost Item",
        filters=db_filters,
        or_filters=or_filters,
        fields=fieldnames,
        order_by=f"{query['sort_by']} {query['sort_order']}",
        limit_start=(query["page"] - 1) * query["page_length"],
        limit_page_length=query["page_length"],
    )
    batch_meta = _load_sku_batch_meta(batch_doc_name)
    mode = batch_meta.get("transport_mode") if resolved_version == batch_meta.get("current_version") else ""
    from overseas_costing.services.approval_link_service import attach_approval_links
    from overseas_costing.services.effective_source_values import project_batch_items
    items, source_context = project_batch_items(items, batch_doc_name, resolved_version)
    items = attach_approval_links(batch_doc_name, [present_saved_sku_result(row, mode) for row in items])
    columns = [dict(column) for column in columns]
    if source_context.get('root_kind') == 'expense':
        labels = {'quantity':'采购支出采用数量','unit_price':'有效商品单价','goods_value':'当前发货货值 RMB'}
        for column in columns:
            if column['fieldname'] in labels:
                column['label'] = labels[column['fieldname']]
    if query["group"] in {"basic", "all"}:
        columns.append({"excel_col": "", "fieldname": "approval_link", "label": "采购审批来源", "read_only": 1})
    if query["group"] in {"total", "all"}:
        for column in columns:
            if column["fieldname"] == "total_unit_rmb":
                column["label"] = "每发货单位成本 RMB"
        columns.extend([
            {"excel_col": "", "fieldname": "shipping_unit_label", "label": "发货计价单位"},
            {"excel_col": "", "fieldname": "purchase_pricing_unit_display", "label": "每采购计价单位成本 RMB"},
        ])
    if query["group"] in {"logistics", "tax", "total", "all"}:
        columns.extend([
            {"excel_col": "", "fieldname": "calculated_customs_rmb", "label": "本次清关费用 RMB"},
            {"excel_col": "", "fieldname": "calculated_tax_rmb", "label": "本次进口税费 RMB"},
        ])
    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": resolved_version,
        "columns": columns,
        "items": items,
        "total": total,
        "page": query["page"],
        "page_length": query["page_length"],
        "page_count": (total + query["page_length"] - 1) // query["page_length"],
        "field_group": query["group"],
        "source_context": source_context,
        "calculation_stale": batch_meta.get("status") == "Dirty" or any(row.get('source_adoption_state') == 'historical_pending' for row in items),
    }


def locate_batch_item(
    batch_name: str,
    item_name: str,
    version_name: str | None = None,
    page_length=50,
) -> dict:
    """定位 SKU 在无筛选、按原始行号排序的详情页码。"""

    _page, page_length = normalize_page(1, page_length, default_length=50)
    if frappe is None:
        return {"ok": False, "dry_run": True, "message": "当前未连接 Frappe。"}
    batch_doc_name = batch_service._resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}
    resolved_version = batch_service._resolve_version_name(batch_doc_name, version_name)
    if not resolved_version:
        return {"ok": False, "message": "当前批次没有版本。"}

    fields = list(dict.fromkeys(
        ["name", "row_no", "excel_row_no"]
        + batch_service._persistent_item_fieldnames(batch_service.EXCEL_FIELDNAMES)
    ))
    items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": resolved_version, "is_excluded": 0},
        fields=fields,
        order_by="row_no asc, name asc",
        limit_page_length=0,
    )
    normalized_item_name = str(item_name or "").strip()
    index = next((index for index, item in enumerate(items) if item.get("name") == normalized_item_name), -1)
    if index < 0:
        return {"ok": False, "message": "当前批次中没有找到该 SKU。"}
    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": resolved_version,
        "item": items[index],
        "page": index // page_length + 1,
        "page_length": page_length,
        "total": len(items),
    }
