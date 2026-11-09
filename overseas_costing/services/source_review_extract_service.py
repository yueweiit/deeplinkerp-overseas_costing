"""Deterministic extraction rules used by unified source review runs."""

from __future__ import annotations

from copy import deepcopy

import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional

from overseas_costing.services.material_value_semantics import is_effectively_missing


SIX_PLACES = Decimal("0.000001")


def _proposal_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "|".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).replace(",", "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        matches = re.findall(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text)
        if len(matches) != 1:
            return None
        try:
            number = Decimal(matches[0])
        except (InvalidOperation, TypeError, ValueError):
            return None
    return number if number.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def normalize_weight_uom(value: Any) -> str:
    text = str(value or "").strip().casefold().replace(" ", "")
    if text in {"kg", "kgs", "kilogram", "kilograms", "千克", "公斤"}:
        return "kg"
    return str(value or "").strip()


def allocate_gross_weight(total_gross: Decimal, net_weights: Iterable[Decimal]) -> list[Decimal]:
    """Allocate a shipment gross total by net weight with exact conservation."""

    gross = _decimal(total_gross)
    nets = [_decimal(value) for value in net_weights]
    if gross is None or gross <= 0 or not nets or any(value is None or value <= 0 for value in nets):
        return []
    typed_nets = [value for value in nets if value is not None]
    total_net = sum(typed_nets, Decimal("0"))
    if total_net <= 0:
        return []
    allocated: list[Decimal] = []
    for net in typed_nets[:-1]:
        allocated.append((gross * net / total_net).quantize(SIX_PLACES, rounding=ROUND_HALF_UP))
    allocated.append(gross.quantize(SIX_PLACES) - sum(allocated, Decimal("0")))
    return allocated


def _source_ref(source: dict, *, row: int = 0, field: str = "") -> dict:
    return {
        "source": "approval_form",
        "file": str(source.get("source_label") or "钉钉审批正文"),
        "sheet": "",
        "page": None,
        "row": row or None,
        "cell": "",
        "field": field,
        "source_id": str(source.get("source_id") or ""),
        "approval_no": str(source.get("approval_no") or ""),
        "actor_name": str(source.get("actor_name") or ""),
        "occurred_at": str(source.get("occurred_at") or ""),
    }


# Only business goods tables can supply cost-item facts. An arbitrary list in a
# payment document may instead be a freight quote or an unrelated attachment.
APPROVAL_GOODS_TABLES = (
    "货物信息", "Bienes", "采购明细", "需求明细", "物料明细", "商品明细",
    "发货明细", "装箱明细", "Desglose de los gastos",
)
VERIFIED_APPROVAL_FIELDS = frozenset({
    "product_name", "spec_model", "recipient", "supplier", "project_collection",
    "volume_weight_kg",
})
APPROVAL_TEXT_FIELDS = {
    "product_name": ("物料名称（中文）Nombre del material (chino)", "物料名称（中文）",
                     "物料名称", "产品名称", "Nombre del material (chino)",
                     "物品名称Nombre del artículo", "物品名称", "Nombre del artículo", "product_name"),
    "spec_model": ("规格型号Especificación / Modelo", "规格型号", "Especificación / Modelo",
                   "物品规格Especificacion", "物品规格", "spec_model"),
    "recipient": ("收件人Destinatario", "收件人", "收货人", "Destinatario", "recipient"),
    "supplier": ("供应商Proveedor", "供应商", "Proveedor", "供应商supplier", "supplier"),
}
APPROVAL_MEASURE_FIELDS = {
    "net_weight_kg": ("净重", "净重kg", "净重Peso Neto", "Peso Neto", "Peso Neto kg", "net_weight_kg"),
    "gross_weight_kg": ("毛重", "毛重kg", "毛重Peso Bruto", "Peso Bruto", "Peso Bruto kg", "gross_weight_kg"),
    "volume_m3": ("体积", "体积m3", "体积Volumen", "体积Volumen m3", "Volumen m3", "CBM", "volume_m3"),
    "volume_weight_kg": ("体积重", "体积重kg", "Peso Volumétrico", "体积重Peso Volumétrico", "volume_weight_kg"),
    "chargeable_weight_kg": ("计费重", "计费重量", "计费重kg", "Peso facturable", "计费重Peso facturable", "chargeable_weight_kg"),
    "package_count": ("箱数", "包装箱数", "箱数Número de cajas", "Número de cajas", "package_count"),
}
AMBIGUOUS_APPROVAL_ROW = "同一审批中有多条货物明细指向同一物料行，未确定唯一对应关系，仅供核对。"


def _approval_goods_locations(source: dict) -> list[tuple[str, int, dict]]:
    from overseas_costing.scripts.import_oa_logistics import (
        _flatten_dingtalk_table_row, _is_oa_goods_placeholder_row,
        _parse_json_text, _parse_oa_goods_text_rows,
    )
    from .logistics_purchase_facts_service import parse_labelled_approval_rows
    from .purchase_value_evidence import key

    fields = source.get("form_fields") if isinstance(source.get("form_fields"), dict) else {}
    aliases = tuple(key(alias) for alias in APPROVAL_GOODS_TABLES)
    result = []
    for label, value in fields.items():
        if not any(alias in key(label) for alias in aliases):
            continue
        parsed = _parse_json_text(value)
        if isinstance(parsed, list):
            rows = [_flatten_dingtalk_table_row(
                {"rowValue": entry} if isinstance(entry, list) else entry) for entry in parsed]
        elif isinstance(parsed, str):
            rows = parse_labelled_approval_rows(parsed) or _parse_oa_goods_text_rows(parsed)
        else:
            continue
        result.extend((str(label), index, row) for index, row in enumerate(rows, 1)
                      if row and not _is_oa_goods_placeholder_row(row))
    return result


def _approval_goods_rows(source: dict) -> list[dict]:
    return [row for _, _, row in _approval_goods_locations(source)]


def _match_item(items: list[dict], material_code: Any, source_doc_no: Any = "", *, mapped=None,
                source_id="", row_no=0) -> Optional[dict]:
    from .purchase_value_evidence import uom

    code = str(material_code or "").strip().casefold()
    if not code:
        return None
    spec = str((mapped or {}).get("spec_model") or "").strip().casefold()
    candidates = [
        item for item in items if str(item.get("material_code") or "").strip().casefold() == code
        and (not spec or not item.get("spec_model") or str(item["spec_model"]).strip().casefold() == spec)
    ]
    if source_id and row_no:
        from .shipment_cost_service import object_json
        located = []
        for item in candidates:
            meta = object_json(item.get("extra_json"))
            logistics = meta.get("logistics_row") or {}
            if (str(logistics.get("source_id") or "") == str(source_id)
                    and str(logistics.get("row_no") or "") == str(row_no)):
                located.append(item)
        if located:
            candidates = located
    source_no = str(source_doc_no or "").strip().casefold()
    if source_no:
        scoped = [item for item in candidates
                  if str(item.get("source_doc_no") or "").strip().casefold() == source_no]
        if scoped:
            candidates = scoped
    if len(candidates) > 1 and mapped:
        if uom(mapped.get("unit")):
            candidates = [item for item in candidates
                          if uom(item.get("shipped_uom") or item.get("unit")) == uom(mapped["unit"])]
        if spec:
            exact = [item for item in candidates if str(item.get("spec_model") or "").strip().casefold() == spec]
            if exact:
                candidates = exact
        if len(candidates) > 1:
            exact = []
            for item in candidates:
                qty = item.get("actual_shipped_qty")
                if qty in (None, ""):
                    qty = item.get("quantity")
                if _decimal(qty) == _decimal(mapped.get("quantity")):
                    exact.append(item)
            candidates = exact
    return candidates[0] if len(candidates) == 1 else None


def match_approval_goods_rows(items, source):
    """Resolve exact table locators, keeping unresolved repeated rows distinct."""
    from collections import Counter, defaultdict
    from .purchase_value_evidence import mapped_row, row_context, shipment_row_cells

    by_sku = defaultdict(list)
    for item in items:
        code = str(item.get("material_code") or "").strip().casefold()
        if code:
            by_sku[code].append(item)
    source_id = source.get("source_id") or (source.get("source_ref") or {}).get("source_id")
    result = []
    for table, row_no, raw in _approval_goods_locations(source):
        raw = row_context(raw, source)
        mapped = mapped_row(raw)
        if source.get("approval_role") == "international_logistics":
            quantity, unit = shipment_row_cells(raw)
            mapped.update(quantity=quantity[2] if quantity else None, unit=unit[2] if unit else None)
        code = str(mapped.get("material_code") or "").strip().casefold()
        item = _match_item(by_sku.get(code, []), code, raw.get("source_doc_no"),
                           mapped=mapped, source_id=source_id, row_no=row_no)
        result.append((table, row_no, raw, mapped, item))
    counts = Counter(str(item.get("name") or "") for _, _, _, _, item in result if item)
    return [(*entry, bool(entry[-1] and counts[str(entry[-1].get("name") or "")] > 1))
            for entry in result]


def _field_values(raw, aliases):
    from .purchase_value_evidence import key

    keys = {key(label) for label in aliases}
    return [(label, value) for label, value in raw.items()
            if key(label) in keys and value not in (None, "")]


def _unique_value(entries, normalize):
    if not entries:
        return None
    values = [(label, raw, normalize(raw)) for label, raw in entries]
    if any(value is None for _, _, value in values) or len({value for _, _, value in values}) != 1:
        return None
    return values[0]


def _review_text(raw):
    from .material_value_semantics import is_placeholder_token

    if not isinstance(raw, str) or is_placeholder_token(raw):
        return None
    return raw.strip()[:500]


def _project_text(raw):
    from overseas_costing.scripts.import_oa_logistics import _extract_dingtalk_entity_values, _parse_json_text

    parsed = _parse_json_text(raw)
    # A delimited plain string may represent multiple selected departments.
    if isinstance(parsed, str) and re.search(r"[,，、;；\n]", parsed):
        return None
    entities = _extract_dingtalk_entity_values(parsed)
    return _review_text(entities[0].get("name")) if len(entities) == 1 else None


def _physical_number(raw, field):
    from .purchase_value_evidence import number

    if field == "package_count":
        suffix = r"(?:箱|个|cajas?)"
    elif field == "volume_m3":
        suffix = r"(?:m(?:³|\^?3)|cbm|立方米)"
    else:
        suffix = r"(?:kg|kgs|千克|公斤)"
    raw = re.sub(rf"\s*{suffix}\s*$", "", str(raw).strip(), flags=re.I)
    value = number(raw)
    if value is None or value < 0 or (field == "package_count" and value != value.to_integral_value()):
        return None
    return value


def _approval_row_details(raw, source, role, supplier_rows):
    """Return deterministic fields and their actual source columns or header."""
    from overseas_costing.scripts.import_oa_logistics import (
        LOGISTICS_WEIGHT_FIELD_ALIASES, PROJECT_OWNERSHIP_FIELD_ALIASES,
    )
    from .supplier_resolution_service import resolve_supplier_reference

    values, evidence = {}, {}

    def keep(field, choice, *, scope="row", canonical=None):
        if choice is None:
            return
        label, original, value = choice
        values[field] = canonical if canonical is not None else value
        evidence[field] = {"column": str(label), "raw_value": deepcopy(original), "scope": scope}
        if field == "supplier":
            evidence[field]["canonical_supplier"] = canonical

    for field, aliases in APPROVAL_TEXT_FIELDS.items():
        choice = _unique_value(_field_values(raw, aliases), _review_text)
        if field == "supplier" and choice:
            supplier = resolve_supplier_reference(choice[2], suppliers=supplier_rows())
            if supplier.get("status") == "EXACT":
                keep(field, choice, canonical=supplier["canonical_supplier"])
        elif field != "supplier":
            keep(field, choice)
    project_aliases = (*PROJECT_OWNERSHIP_FIELD_ALIASES, "项目", "proyecto", "project_collection")
    row_project = _field_values(raw, project_aliases)
    if row_project:
        keep("project_collection", _unique_value(row_project, _project_text))
    else:
        header_project = _field_values(source.get("form_fields") or {}, PROJECT_OWNERSHIP_FIELD_ALIASES)
        keep("project_collection", _unique_value(header_project, _project_text), scope="header")
    if role in {"international_logistics", "payment"}:
        for field, aliases in APPROVAL_MEASURE_FIELDS.items():
            matches = _field_values(raw, aliases)
            if not matches and field == "gross_weight_kg" and role == "international_logistics":
                matches = _field_values(raw, LOGISTICS_WEIGHT_FIELD_ALIASES)
            choice = _unique_value(matches, lambda value: _physical_number(value, field))
            if choice:
                label, original, value = choice
                keep(field, (label, original, _decimal_text(value)))
    return values, evidence


def build_system_approval_proposals(
    items: list[dict],
    source: dict,
    *,
    transport_mode: str = "",
    fx_rates: dict | None = None,
    existing_items: list[dict] | None = None,
) -> list[dict]:
    """Read goods fields from uniquely matched, server-owned approval rows."""
    from overseas_costing.scripts.import_oa_logistics import LOGISTICS_WEIGHT_FIELD_ALIASES
    from .purchase_value_evidence import row_fact_status, shipment_row_cells
    from .source_priority_service import classify_workflow_stage
    from .supplier_resolution_service import load_active_suppliers

    role = str(source.get("approval_role") or "")
    if role not in {"international_logistics", "payment", "purchase", "logistics_expense"}:
        return []
    if role == "purchase" and classify_workflow_stage(source) == "payment":
        role = "logistics_expense"
    catalog = None

    def supplier_rows():
        nonlocal catalog
        if catalog is None:
            catalog = load_active_suppliers()
        return catalog

    locations = match_approval_goods_rows(items, source)
    original_by_name = {item.get("name"): item for item in (existing_items if existing_items is not None else items)}
    mapped_rows = []
    blocked_values = []
    complete_net_set = bool(locations)
    for table, source_row, raw, _, item, ambiguous in locations:
        if not item:
            complete_net_set = False
            continue
        fields, evidence = _approval_row_details(raw, source, role, supplier_rows)
        original = original_by_name.get(item.get("name"), {})
        for field in (*APPROVAL_TEXT_FIELDS, "project_collection"):
            value = str(fields.get(field) or "").strip().casefold()
            placeholder_name = (field == "product_name" and value == str(item.get("material_code") or "").strip().casefold())
            if value and (placeholder_name or value == str(original.get(field) or "").strip().casefold()):
                fields.pop(field, None)
                evidence.pop(field, None)
        quantity_cell, unit_cell = shipment_row_cells(raw)
        quantity = quantity_cell[2] if quantity_cell else None
        unit = unit_cell[2] if unit_cell else None
        if role == "international_logistics" and quantity is not None and quantity >= 0 and unit:
            fields["actual_shipped_qty"] = _decimal_text(quantity)
            fields["shipped_uom"] = unit
            if unit == "kg" and not _field_values(raw, APPROVAL_MEASURE_FIELDS["net_weight_kg"]):
                fields["net_weight_kg"] = _decimal_text(quantity)
        if (unit != "kg" or ambiguous
                or _field_values(raw, (*APPROVAL_MEASURE_FIELDS["gross_weight_kg"], *LOGISTICS_WEIGHT_FIELD_ALIASES))):
            complete_net_set = False
        fact, readable_amount, issue = row_fact_status(raw, role=role, fx_rates=fx_rates)
        if fact:
            fields["goods_value"] = fact["amount_rmb"]
        elif readable_amount is not None and issue:
            blocked_values.append((table, source_row, item, readable_amount, AMBIGUOUS_APPROVAL_ROW if ambiguous else issue))
        if fields:
            mapped_rows.append((table, source_row, item, fields, evidence, ambiguous))

    form_fields = source.get("form_fields") or {}
    total = _unique_value(
        _field_values(form_fields, (*APPROVAL_MEASURE_FIELDS["gross_weight_kg"], *LOGISTICS_WEIGHT_FIELD_ALIASES)),
        lambda raw: _physical_number(raw, "gross_weight_kg"),
    )
    if (role == "international_logistics" and total and complete_net_set
            and len(mapped_rows) == len(locations)
            and not any("gross_weight_kg" in fields for _, _, _, fields, _, _ in mapped_rows)):
        nets = [_decimal(fields.get("net_weight_kg")) for _, _, _, fields, _, _ in mapped_rows]
        allocation = allocate_gross_weight(total[2], nets)
        for (_, _, _, fields, _, _), gross in zip(mapped_rows, allocation):
            fields["gross_weight_kg"] = _decimal_text(gross)

    proposals = []
    for table, source_row, item, fields, evidence, ambiguous in mapped_rows:
        conflict = any(
            not is_effectively_missing(field, item.get(field), item)
            and str(item.get(field) or "").strip().casefold() != str(value or "").strip().casefold()
            for field, value in fields.items()
        )
        proposals.append({
            "proposal_id": _proposal_id("system-approval", source.get("source_id"), table, source_row, item.get("name")),
            "proposal_type": "item_update",
            "target_item_name": str(item.get("name") or ""),
            "confidence": 1.0,
            "conflict": conflict or ambiguous,
            "default_selected": not (conflict or ambiguous),
            "result_origin": "SYSTEM",
            "reason": AMBIGUOUS_APPROVAL_ROW if ambiguous else "钉钉审批结构化明细由系统直接读取。",
            "source_refs": [_source_ref(source, row=source_row, field=table)],
            "_approval_field_evidence": evidence,
            "_review_unresolved_reason": AMBIGUOUS_APPROVAL_ROW if ambiguous else "",
            "payload": {"item_name": str(item.get("name") or ""), "fields": fields},
        })
    for table, source_row, item, readable_amount, issue in blocked_values:
        proposals.append({
            "proposal_id": _proposal_id("system-approval-value-review", source.get("source_id"), table, source_row, item.get("name")),
            "proposal_type": "item_update", "target_item_name": str(item.get("name") or ""),
            "confidence": 1.0, "conflict": True, "default_selected": False, "result_origin": "SYSTEM",
            "reason": issue, "source_refs": [_source_ref(source, row=source_row, field=table)],
            "_review_unresolved_reason": issue,
            "payload": {"item_name": str(item.get("name") or ""), "fields": {"goods_value": readable_amount}},
        })
    return proposals


def projection_candidates_to_review_proposals(candidates: list[dict], source: dict) -> list[dict]:
    """Convert deterministic packing projections to unified review proposals."""

    result = []
    for index, candidate in enumerate(candidates or [], start=1):
        item_name = str(candidate.get("item_name") or "")
        fieldname = str(candidate.get("fieldname") or "")
        if not item_name or not fieldname:
            continue
        result.append(
            {
                "proposal_id": _proposal_id(
                    "system-excel", source.get("source_id"), item_name, fieldname, index
                ),
                "proposal_type": "item_update",
                "target_item_name": item_name,
                "confidence": float(candidate.get("confidence") or 0.99),
                "conflict": float(candidate.get("confidence") or 0) < 0.9,
                "default_selected": float(candidate.get("confidence") or 0) >= 0.9,
                "result_origin": "SYSTEM",
                "reason": str(candidate.get("reason") or "Excel 确定性解析。"),
                "source_refs": list(candidate.get("source_refs") or []),
                **({"fact_ids": list(candidate.get("fact_ids") or [])} if candidate.get("fact_ids") else {}),
                "payload": {"item_name": item_name, "fields": {fieldname: candidate.get("suggested_value")}},
            }
        )
    return result
