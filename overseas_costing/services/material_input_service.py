"""物料录入的纯规则：稳定行、有效发货数量和单位来源。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Dict, Optional
from uuid import uuid4

try:
    import frappe
except Exception:  # pragma: no cover - pure tests do not require Frappe
    frappe = None


VALID_QTY_MODES = frozenset(
    {
        "DEFAULT_PURCHASE",
        "EXPLICIT_SOURCE",
        "MANUAL_CONFIRMED",
        "LEGACY_UNVERIFIED",
    }
)

GRID_FIELDS = (
    'extra_json',
    "name",
    "modified",
    "row_no",
    "excel_row_no",
    "stable_line_key",
    "material_code",
    "product_name",
    "product_name_es",
    "spec_model",
    "unit",
    "purchase_uom",
    "unit_price",
    "unit_price_uom",
    "purchase_currency",
    "quantity",
    "actual_shipped_qty",
    "actual_shipped_qty_mode",
    "actual_shipped_qty_source_revision",
    "shipped_uom",
    "cost_output_uom",
    "goods_value",
    "net_weight_kg",
    "gross_weight_kg",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "project_collection",
    "supplier",
    "source_type",
    "source_doc_no",
    "dingtalk_instance_id",
    "source_file_name",
    "source_attachment_id",
    "parse_status",
    "is_excluded",
    "excluded_at",
    "excluded_by",
    "exclusion_reason",
)


def _positive_decimal(value: object) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def ensure_stable_line_key(
    item: dict,
    *,
    key_factory: Optional[Callable[[], str]] = None,
) -> str:
    """Return the existing business-row key, or create one for a new row."""

    existing = str(item.get("stable_line_key") or "").strip()
    if existing:
        return existing
    generated = str((key_factory or (lambda: uuid4().hex))() or "").strip()
    if not generated:
        raise ValueError("stable_line_key generator returned an empty value")
    return generated


def resolve_effective_quantity(item: dict) -> Dict[str, object]:
    """Resolve the quantity used by costing without inferring legacy provenance."""

    from overseas_costing.services.shipment_cost_service import object_json
    metadata = object_json(item.get('extra_json'))
    if 'settlement_cargo' in metadata:
        cargo = metadata['settlement_cargo'] if isinstance(metadata['settlement_cargo'], dict) else {}
        quantity = _positive_decimal(cargo.get('quantity'))
        uom = str(cargo.get('unit') or '').strip()
        blocking = []
        if quantity is None:
            blocking.append({'code':'SETTLEMENT_QTY_REQUIRED','field':'actual_shipped_qty'})
        if not uom:
            blocking.append({'code':'SETTLEMENT_UOM_REQUIRED','field':'shipped_uom'})
        return {'quantity':format(quantity,'f') if quantity is not None else '', 'uom':uom,
                'mode':'SETTLEMENT_FINAL' if cargo else 'SETTLEMENT_PENDING', 'is_default':False, 'blocking':blocking,
                'source_revision':cargo.get('source_snapshot')}

    stored_mode = str(item.get("actual_shipped_qty_mode") or "").strip()
    if not stored_mode:
        stored_mode = (
            "LEGACY_UNVERIFIED"
            if _positive_decimal(item.get("actual_shipped_qty")) is not None
            else "DEFAULT_PURCHASE"
        )
    mode = stored_mode
    if mode not in VALID_QTY_MODES:
        mode = "LEGACY_UNVERIFIED"

    raw_quantity = item.get("quantity") if mode == "DEFAULT_PURCHASE" else item.get("actual_shipped_qty")
    quantity = _positive_decimal(raw_quantity)
    uom = str(item.get("shipped_uom") or item.get("purchase_uom") or item.get("unit") or "").strip()
    blocking = []
    if quantity is None:
        blocking.append({"code": "SHIPPED_QTY_REQUIRED", "field": "actual_shipped_qty"})
    if not uom:
        blocking.append({"code": "SHIPPED_UOM_REQUIRED", "field": "shipped_uom"})

    return {
        "quantity": format(quantity, "f") if quantity is not None else "",
        "uom": uom,
        "mode": mode,
        "is_default": mode == "DEFAULT_PURCHASE",
        "blocking": blocking,
    }


def normalize_grid_page(page: object, page_length: object) -> tuple[int, int]:
    try:
        normalized_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        normalized_page = 1
    try:
        normalized_length = min(200, max(1, int(page_length or 100)))
    except (TypeError, ValueError):
        normalized_length = 100
    return normalized_page, normalized_length


def _purchase_total_unit_price(row: dict) -> dict | None:
    """Project a missing purchase price from the full RMB purchase total.

    This is display-only provenance.  It deliberately does not rewrite the
    original purchase fields because the source document may use another
    currency or purchase unit.
    """

    from overseas_costing.services.material_value_semantics import is_effectively_missing

    if not is_effectively_missing("unit_price", row.get("unit_price"), row):
        return None
    try:
        amount = Decimal(str(row.get("goods_value")))
        quantity = Decimal(str(row.get("quantity")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    uom = str(row.get("purchase_uom") or row.get("unit_price_uom") or row.get("unit") or "").strip()
    if not amount.is_finite() or amount <= 0 or not quantity.is_finite() or quantity <= 0 or not uom:
        return None
    try:
        calculation_value = amount / quantity
        value = calculation_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return {
        "value": format(value, ".2f"),
        "calculation_value": format(calculation_value, "f"),
        "currency": "RMB",
        "unit": uom,
        "error": "",
        "source_type": "purchase_total_derived",
        "source": "LEGACY_PURCHASE",
        "evidence": {
            "total_goods_value_rmb": format(amount.normalize(), "f"),
            "purchase_quantity": format(quantity.normalize(), "f"),
            "purchase_uom": uom,
        },
    }


def _explicit_purchase_unit_price(row: dict) -> dict | None:
    """Project a complete stored purchase-price tuple without changing evidence."""

    from overseas_costing.services.material_value_semantics import is_effectively_missing

    if is_effectively_missing("unit_price", row.get("unit_price"), row):
        return None
    try:
        value = Decimal(str(row.get("unit_price")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    currency = str(row.get("purchase_currency") or "").strip().upper()
    uom = str(row.get("unit_price_uom") or row.get("purchase_uom") or row.get("unit") or "").strip()
    if not value.is_finite() or value < 0 or not currency or not uom:
        return None
    return {
        "value": format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f"),
        "calculation_value": format(value, "f"),
        "currency": currency,
        "unit": uom,
        "error": "",
        "source_type": "explicit_purchase_price",
        "source": str(row.get("source_type") or "ITEM_PURCHASE"),
        "evidence": {
            "unit_price": str(row.get("unit_price")),
            "purchase_currency": currency,
            "unit_price_uom": uom,
            "source_doc_no": str(row.get("source_doc_no") or ""),
        },
    }


def _settlement_unit_price(valuation: dict) -> dict | None:
    """Project a unit-price valuation only when its complete tuple is present."""

    method = str((valuation or {}).get("method") or "")
    if method not in {"settlement_expense_unit_price", "settlement_purchase_unit_price"}:
        return None
    status = str(valuation.get("status") or "").strip().lower()
    if valuation.get("error") or status in {"missing", "conflict", "invalid", "stale", "historical_pending"}:
        return None
    evidence = valuation.get("input_evidence") or {}
    try:
        price = Decimal(str(evidence.get("price")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    currency = str(evidence.get("original_currency") or "").strip().upper()
    unit = str(evidence.get("price_uom") or "").strip()
    if not price.is_finite() or price < 0 or not currency or not unit:
        return None
    return {
        "value": format(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f"),
        "calculation_value": format(price, "f"),
        "currency": currency,
        "unit": unit,
        "source_type": "expense" if method == "settlement_expense_unit_price" else "commodity_purchase",
        "source": evidence.get("purchase_source"),
        "error": valuation.get("error"),
        "evidence": evidence.get("price_evidence") or evidence,
    }


def present_material_row(item: dict) -> dict:
    """Expose a stable row identity and lazy quantity provenance without backfilling."""

    from overseas_costing.services.effective_source_values import project_source_values
    row = project_source_values(item or {})
    stored_key = str(row.get("stable_line_key") or "").strip()
    item_name = str(row.get("name") or "").strip()
    row["stable_line_key"] = stored_key or (f"legacy:{item_name}" if item_name else "")
    quantity_state = resolve_effective_quantity(row)
    row["effective_shipping"] = quantity_state
    from overseas_costing.services.shipment_cost_service import object_json
    row['settlement_cargo'] = object_json(row.get('extra_json')).get('settlement_cargo')
    row["effective_shipping_quantity"] = quantity_state["quantity"]
    row["effective_shipping_uom"] = quantity_state["uom"]
    row["quantity_source_badge"] = {
        "SETTLEMENT_FINAL": "物流结算采购支出",
        "DEFAULT_PURCHASE": "默认采购数量",
        "EXPLICIT_SOURCE": "资料来源",
        "MANUAL_CONFIRMED": "已人工确认",
        "LEGACY_UNVERIFIED": "历史值待核对",
    }.get(str(quantity_state["mode"]), "待核对")
    row["field_sources"] = {
        "purchase": {
            "source_type": row.get("source_type") or "",
            "source_doc_no": row.get("source_doc_no") or "",
        },
        "shipping_quantity": {
            "mode": quantity_state["mode"],
            "revision": row.get("actual_shipped_qty_source_revision") or "",
        },
    }
    from overseas_costing.services.shipment_cost_service import shipment_value
    valuation = shipment_value(row)
    row['shipment_value_rmb'] = valuation['amount_rmb']
    row['shipment_valuation'] = valuation
    from .purchase_value_evidence import META_KEY, adopted_price
    fact = object_json(row.get('extra_json')).get(META_KEY)
    row.pop('adopted_price', None)
    if fact:
        row['adopted_price'] = adopted_price(fact, row) or {}
    else:
        settlement_method = valuation.get('method') in {
            'settlement_expense_unit_price', 'settlement_purchase_unit_price'
        }
        if settlement_method:
            price = _settlement_unit_price(valuation)
            if valuation.get('method') == 'settlement_purchase_unit_price' and not valuation.get('trusted_shipment_source'):
                price = None
        else:
            price = _explicit_purchase_unit_price(row) or _purchase_total_unit_price(row)
        if price is not None:
            row['adopted_price'] = price
    if (row.get("adopted_price") or {}).get("source_type") == "purchase_total_derived":
        row["purchase_price_source"] = "按货值÷采购数量计算"
    return row


def purchase_value_coverage(items: list[dict]) -> dict:
    """Return whether every active material has a current, evidenced value."""

    from overseas_costing.services.shipment_cost_service import is_explicit_shipment_zero

    active = [
        dict(row or {})
        for row in (items or [])
        if str((row or {}).get("is_excluded") or "").strip().lower()
        not in {"1", "true", "yes"}
    ]
    missing_items = []
    for raw in active:
        row = present_material_row(raw)
        amount = _positive_decimal(row.get("shipment_value_rmb"))
        valuation = row.get("shipment_valuation") or {}
        if amount is not None or is_explicit_shipment_zero(valuation):
            continue
        missing_items.append(
            {
                "name": str(row.get("name") or ""),
                "stable_line_key": str(row.get("stable_line_key") or ""),
                "row_no": row.get("row_no"),
                "material_code": str(row.get("material_code") or ""),
                "reason_code": str(valuation.get("error") or "GOODS_VALUE_MISSING"),
            }
        )
    return {
        "complete": bool(active) and not missing_items,
        "item_count": len(active),
        "missing_count": len(missing_items),
        "missing_items": missing_items,
    }


def assert_complete_purchase_values(items: list[dict]) -> dict:
    """Reject formal calculations until all active shipment values are valid."""

    coverage = purchase_value_coverage(items)
    if not coverage["item_count"]:
        raise ValueError(_("当前批次没有物料，请先补充物料后再试算。"))
    if coverage["missing_count"]:
        raise ValueError(
            _("还有 {0} 行本次发货货值缺失或失效，请先补齐后再试算。").format(coverage['missing_count'])
        )
    return coverage


def analyze_material_requirements(items: list[dict], fees: list[dict]) -> dict:
    """Derive red cells from effective system bases; project ownership is informational."""

    from overseas_costing.services import fee_allocation_service
    from overseas_costing.services.shipment_cost_service import is_explicit_shipment_zero

    presented = [present_material_row(dict(row or {})) for row in (items or [])]
    row_states = {}
    for row in presented:
        key = str(row.get("stable_line_key") or row.get("name") or "")
        reasons: dict[str, list[dict]] = {}
        for blocking in (row.get("effective_shipping") or {}).get("blocking") or []:
            reasons.setdefault(str(blocking.get("field") or "actual_shipped_qty"), []).append(blocking)
        explicit_zero = is_explicit_shipment_zero(row["shipment_valuation"])
        if _positive_decimal(row.get("shipment_value_rmb")) is None and not explicit_zero:
            value_field = 'goods_value' if row['shipment_valuation']['method'] == 'LEGACY_PURCHASE' else 'shipment_value_rmb'
            reasons.setdefault(value_field, []).append(
                {"code": row['shipment_valuation'].get('error') or "GOODS_VALUE_REQUIRED", "message": "本次发货货值缺失或已失效，请重新分析资料。"}
            )
        row_states[key] = {"missing_fields": [], "field_reasons": reasons}

    basis_fields = {
        "goods_value": "shipment_value_rmb",
        "gross_weight": "gross_weight_kg",
        "volume": "volume_m3",
        "chargeable_weight": "chargeable_weight_kg",
    }
    for fee in fees or []:
        if not fee_allocation_service.is_counted_fee(fee):
            continue
        if str(fee.get("scope_type") or "ALL_ITEMS").upper() == "DIRECT_ITEM":
            continue
        eligible = fee_allocation_service.resolve_eligible_items(fee, presented)
        basis = fee_allocation_service.select_allocation_basis(fee, eligible)["basis"]
        fieldname = basis_fields.get(basis)
        if not fieldname:
            continue
        fee_key = str(fee.get("logical_fee_key") or fee.get("rule_code") or "")
        for row in fee_allocation_service.resolve_eligible_items(fee, presented):
            if _positive_decimal(row.get(fieldname)) is not None:
                continue
            reason_field = 'goods_value' if fieldname == 'shipment_value_rmb' and row['shipment_valuation']['method'] == 'LEGACY_PURCHASE' else fieldname
            key = str(row.get("stable_line_key") or row.get("name") or "")
            state = row_states.get(key)
            if state is None:
                continue
            state["field_reasons"].setdefault(reason_field, []).append(
                {
                    "code": "ALLOCATION_BASIS_REQUIRED",
                    "fee_key": fee_key,
                    "message": f"费用 {fee_key or '--'} 需要该分摊依据。",
                }
            )

    missing_cell_count = 0
    for state in row_states.values():
        state["missing_fields"] = sorted(state["field_reasons"])
        missing_cell_count += len(state["missing_fields"])
    return {
        "rows": row_states,
        "missing_cell_count": missing_cell_count,
        "affected_row_count": sum(1 for state in row_states.values() if state["missing_fields"]),
    }


def get_material_grid(
    batch_name: str,
    version_name: str | None = None,
    page: object = 1,
    page_length: object = 100,
) -> dict:
    """Return only the material-input grid data for the current batch version."""

    normalized_page, normalized_length = normalize_grid_page(page, page_length)
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "items": [],
            "total": 0,
            "page": normalized_page,
            "page_length": normalized_length,
            "page_count": 0,
        }

    from overseas_costing.services import batch_service

    resolved_batch = batch_service._resolve_batch_name(str(batch_name or ""))
    if not resolved_batch:
        raise ValueError(_("未找到批次：{0}").format(batch_name))
    resolved_version = batch_service._resolve_version_name(resolved_batch, version_name)
    if not resolved_version:
        raise ValueError(_("当前批次没有可用成本版本。"))

    filters = {"batch": resolved_batch, "version": resolved_version, "is_excluded": 0}
    total = int(frappe.db.count("Overseas Cost Item", filters=filters) or 0)
    raw_items = frappe.get_all(
        "Overseas Cost Item",
        filters=filters,
        fields=list(GRID_FIELDS),
        order_by="row_no asc, name asc",
        limit_page_length=10000,
    )
    from overseas_costing.services.approval_link_service import attach_approval_links

    from overseas_costing.services.effective_source_values import project_batch_items, project_source_values
    raw_items, source_context = project_batch_items(raw_items,resolved_batch,resolved_version)
    all_items = [present_material_row(item) for item in raw_items]
    from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
    version_meta = frappe.db.get_value("Overseas Cost Version", resolved_version,
                                       ["extra_json", "status"], as_dict=True) or {}
    if not isinstance(version_meta, dict):
        version_meta = {}
    packing = project_packing_groups(all_items, groups_from_version(version_meta), strict=False)
    all_items = packing['items']
    start = (normalized_page - 1) * normalized_length
    page_items = all_items[start:start + normalized_length]
    page_groups = {}
    for item in page_items:
        if item.get('packing_group_id'):
            page_groups.setdefault(item['packing_group_id'], []).append(item)
    for members in page_groups.values():
        for position, item in enumerate(members):
            item['packing_group_absolute_position'] = item.get('packing_group_position')
            item['packing_group_position'] = position
            item['packing_group_size_on_page'] = len(members)
            item['packing_group'] = {**(item.get('packing_group') or {}),
                                     'rowspan':len(members) if position == 0 else 0}
    items = attach_approval_links(resolved_batch, page_items)
    from overseas_costing.services import fee_service

    batch_meta = frappe.db.get_value(
        "Overseas Cost Batch", resolved_batch,
        ["transport_mode", "current_version", "confirm_status", "writeback_status", "is_locked"],
        as_dict=True,
    ) or {}
    transport_mode = (batch_meta.get("transport_mode") if isinstance(batch_meta, dict) else batch_meta) or "SEA"
    fees = fee_service.compose_fee_worklist_rows(
        fee_service._query_rules(resolved_batch, resolved_version),
        transport_mode, **({"source_context":source_context} if source_context else {}),
    )
    requirements = analyze_material_requirements(all_items, fees)
    for item in items:
        item["requirements"] = requirements["rows"].get(item["stable_line_key"], {})
    return {
        "ok": True,
        "batch_name": resolved_batch,
        "version_name": resolved_version,
        "items": items,
        "total": total,
        "page": normalized_page,
        "page_length": normalized_length,
        "page_count": (total + normalized_length - 1) // normalized_length,
        "source_context": source_context,
        "missing_cell_count": requirements["missing_cell_count"],
        "affected_row_count": requirements["affected_row_count"],
        "packing_groups": packing['groups'],
        "packing_group_editable": bool(
            isinstance(batch_meta, dict)
            and str(version_meta.get('status') or '') == 'Active'
            and str(batch_meta.get('current_version') or '') == str(resolved_version)
            and str(batch_meta.get('confirm_status') or '') != 'Confirmed'
            and str(batch_meta.get('writeback_status') or '') != 'Success'
            and not batch_meta.get('is_locked')
        ),
        # 供应商 / 项目归属单开一档门槛，与 calculate_service._assert_current_item_version 的
        # reference 档同一口径：只要求「是本批次的当前版本、且版本未归档」。这两项是采购在成本
        # 确认后补的 ERP 基础资料、不参与金额计算，所以已确认 / 已锁定 / 已回写的当前版本仍可维护。
        "reference_editable": bool(
            isinstance(batch_meta, dict)
            and str(batch_meta.get('current_version') or '') == str(resolved_version)
            and str(version_meta.get('status') or '') not in {'', 'Archived'}
        ),
    }


def get_excluded_materials(batch_name: str, version_name: str | None = None) -> dict:
    """返回当前活动版本中可恢复的软排除物料。"""
    if frappe is None:
        return {"ok": True, "dry_run": True, "batch_name": batch_name,
                "version_name": version_name, "items": []}
    from overseas_costing.services import batch_service
    resolved_batch = batch_service._resolve_batch_name(str(batch_name or ""))
    if not resolved_batch:
        raise ValueError(_("未找到批次：{0}").format(batch_name))
    resolved_version = batch_service._resolve_version_name(resolved_batch, version_name)
    if not resolved_version:
        raise ValueError(_("当前批次没有可用成本版本。"))
    rows = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": resolved_batch, "version": resolved_version, "is_excluded": 1},
        fields=list(GRID_FIELDS), order_by="row_no asc, name asc", limit_page_length=10000,
    )
    return {"ok": True, "batch_name": resolved_batch, "version_name": resolved_version,
            "items": [present_material_row(row) for row in rows]}
def build_shipping_quantity_updates(
    item: dict,
    *,
    mode: str,
    value: object,
    uom: str,
    source_revision: str = "",
) -> dict:
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in {"DEFAULT_PURCHASE", "MANUAL_CONFIRMED"}:
        raise ValueError(_("发货数量来源状态不合法。"))
    resolved_uom = str(uom or item.get("shipped_uom") or item.get("purchase_uom") or item.get("unit") or "").strip()
    if not resolved_uom:
        raise ValueError(_("请填写发货单位。"))

    if normalized_mode == "DEFAULT_PURCHASE":
        if _positive_decimal(item.get("quantity")) is None:
            raise ValueError(_("采购数量缺失，不能设为采购数量默认。"))
        quantity = None
        revision = ""
    else:
        quantity_value = _positive_decimal(value)
        if quantity_value is None:
            raise ValueError(_("手工确认的发货数量必须大于 0。"))
        quantity = format(quantity_value, "f")
        revision = str(source_revision or "")

    return {
        "actual_shipped_qty": quantity,
        "actual_shipped_qty_mode": normalized_mode,
        "actual_shipped_qty_source_revision": revision,
        "shipped_uom": resolved_uom,
        "cost_output_uom": resolved_uom,
    }


def set_shipping_quantity(
    batch_name: str,
    item_name: str,
    mode: str,
    value: object,
    uom: str,
    edit_token: str,
    expected_modified: str,
) -> dict:
    """Atomically update quantity, provenance and output unit for one item."""

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "item_name": item_name,
            "updates": build_shipping_quantity_updates(
                {"quantity": value, "purchase_uom": uom},
                mode=mode,
                value=value,
                uom=uom,
            ),
        }

    from overseas_costing.services import batch_service, edit_session_service, import_service

    resolved_batch = batch_service._resolve_batch_name(str(batch_name or ""))
    if not resolved_batch:
        raise ValueError(_("未找到批次：{0}").format(batch_name))
    edit_session_service.assert_batch_write(
        resolved_batch,
        edit_token=edit_token,
        expected_modified=expected_modified,
    )
    item = frappe.db.get_value(
        "Overseas Cost Item",
        str(item_name or ""),
        [
            "name",
            "batch",
            "version",
            "row_no",
            "quantity",
            "unit",
            "purchase_uom",
            "shipped_uom",
            "actual_shipped_qty",
        ],
        as_dict=True,
    ) or {}
    if str(item.get("batch") or "") != resolved_batch:
        raise ValueError(_("物料行不属于当前批次。"))
    current_version = str(frappe.db.get_value("Overseas Cost Batch", resolved_batch, "current_version") or "")
    if str(item.get("version") or "") != current_version:
        raise RuntimeError("物料行不属于当前版本，请刷新后重试。")

    updates = build_shipping_quantity_updates(item, mode=mode, value=value, uom=uom)
    changed = import_service._update_item_fields(
        item_name=str(item["name"]),
        batch_doc_name=resolved_batch,
        version_name=current_version,
        row_no=item.get("row_no"),
        field_updates=updates,
        action_remark="设置采购数量默认发货" if mode == "DEFAULT_PURCHASE" else "人工确认发货数量",
    )
    if changed:
        import_service._mark_batch_dirty(resolved_batch)
        frappe.db.commit()
    return {
        "ok": True,
        "batch_name": resolved_batch,
        "version_name": current_version,
        "item_name": str(item["name"]),
        "changed_field_count": len(changed),
        "updates": updates,
        "batch_modified": frappe.db.get_value("Overseas Cost Batch", resolved_batch, "modified"),
    }
