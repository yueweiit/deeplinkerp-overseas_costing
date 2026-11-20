"""Enrich copies of shipment rows with trusted, structured purchase facts only.

New goods totals use the supplied version FX snapshot; original amounts and
currencies remain in metadata. Unit prices stay in their original currency.
Full purchase quantities/totals live in metadata and must be apportioned once per
purchase_key by the logistics reconciliation before any confirmed database write.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re

from overseas_costing.scripts.import_oa_logistics import (
    PURCHASE_CURRENCY_FIELD_ALIASES,
    _find_field_value,
    _flatten_dingtalk_table_row,
    _normalize_currency_code,
    _parse_json_text,
)
from overseas_costing.utils.field_mapper import map_oa_row_to_item, map_purchase_expense_row_to_item


FACT_FIELDS = ("quantity", "goods_value", "unit_price", "purchase_currency", "purchase_uom",
               "unit_price_uom", "source_doc_no", "source_type")


def _text(value) -> str:
    return str(value or "").strip()


def _missing(value) -> bool:
    return value is None or value == ""


def _number(value):
    if _missing(value) or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return int(number) if number == number.to_integral_value() else format(number, "f")


def _metadata(item: dict) -> dict:
    value = item.get("extra_json")
    if isinstance(value, dict):
        return deepcopy(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def parse_labelled_approval_rows(value) -> list[dict]:
    """Parse archived label:value;label:value rows, never infer rows from prose."""
    if not isinstance(value, str):
        return []
    rows = []
    for line in value.splitlines():
        fields = {}
        for cell in re.split(r"[;；]", line):
            pair = re.split(r"[:：]", cell, maxsplit=1)
            if len(pair) == 2 and pair[0].strip() and pair[1].strip():
                fields[pair[0].strip()] = pair[1].strip()
        mapped = map_purchase_expense_row_to_item(fields)
        if not mapped.get("material_code"):
            mapped = map_oa_row_to_item(fields)
        quantity = _number(mapped.get("quantity"))
        if _text(mapped.get("material_code")) and quantity is not None and Decimal(str(quantity)) > 0:
            rows.append(fields)
    return rows


def _purchase_candidates(sources: list[dict], fx_rates: dict | None = None) -> list[dict]:
    candidates = []
    seen = set()
    for source in sources or []:
        if (source.get("source_kind") != "approval_form" or source.get("approval_role") != "purchase"
                or not source.get("source_id") or source.get("excluded") or source.get("read_status") == "EXCLUDED"
                or source.get("available") is False
                or (source.get("selected") is False and not source.get("locked"))):
            continue
        fields = source.get("form_fields") or {}
        if not isinstance(fields, dict):
            continue
        mapped_rows = []
        currency = _find_field_value(fields, PURCHASE_CURRENCY_FIELD_ALIASES)
        for name, value in fields.items():
            parsed = _parse_json_text(value)
            if isinstance(parsed, str):
                parsed = parse_labelled_approval_rows(parsed)
            if not isinstance(parsed, list):
                continue
            for table_index, raw_row in enumerate(parsed, 1):
                raw = _flatten_dingtalk_table_row(raw_row)
                mapped = map_purchase_expense_row_to_item(raw)
                mapped["purchase_currency"] = mapped.get("purchase_currency") or currency
                mapped_rows.append((name, table_index, raw, mapped))
        for table_name, table_index, raw, mapped in mapped_rows:
            code = _text(mapped.get("material_code"))
            quantity = _number(mapped.get("quantity"))
            if not code or quantity is None or Decimal(str(quantity)) <= 0:
                continue
            stable_row_id = _text(raw.get("_dingtalk_row_number"))
            row_id = stable_row_id or str(table_index)
            identity = json.dumps([source["source_id"], table_name, row_id], ensure_ascii=False)
            key = "purchase:" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            if key in seen:
                continue
            seen.add(key)
            amount = _number(mapped.get("goods_value"))
            currency_code = _normalize_currency_code(mapped.get("purchase_currency")) or _text(mapped.get("purchase_currency")).upper() or None
            rate = 1 if currency_code == "RMB" else _number((fx_rates or {}).get(currency_code))
            usable_rate = rate is not None and Decimal(str(rate)) > 0
            converted = (_number((Decimal(str(amount)) * Decimal(str(rate))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
                         if amount is not None and usable_rate else None)
            fact = {"material_code": code, "spec_model": _text(mapped.get("spec_model")),
                    "quantity": quantity, "goods_value": converted,
                    "unit_price": _number(mapped.get("unit_price")),
                    "purchase_currency": currency_code,
                    "original_goods_value": amount, "original_currency": currency_code,
                    "goods_value_fx_rate": rate if usable_rate else None,
                    "purchase_uom": mapped.get("purchase_uom") or mapped.get("unit") or None,
                    "unit_price_uom": mapped.get("unit_price_uom") or mapped.get("unit") or None,
                    "source_doc_no": source.get("approval_no") or source["source_id"],
                    "source_type": "PURCHASE_EXPENSE_OA",
                    "source_refs": [{"source_id": source["source_id"], "field": table_name,
                                     "row": table_index, "source_row_id": row_id}]}
            candidates.append({"purchase_key": key, "material_code": code,
                               "spec_model": _text(mapped.get("spec_model")), "purchase_fact": fact,
                               "purchase_source_id": source["source_id"], "purchase_row_id": row_id,
                               "purchase_logical_source_id": _text(source.get("logical_source_id") or source.get("process_instance_id")),
                               "purchase_table_name": table_name, "purchase_row_stable": bool(stable_row_id),
                               "fx_unresolved": (f"{code} 缺少有效的 {currency_code or '采购币种'} 人民币汇率，人民币货值待补。"
                                                 if amount is not None and not usable_rate else "")})
    return candidates


def _matching_candidates(item: dict, prior: dict, candidates: list[dict]) -> list[dict]:
    from .purchase_value_evidence import spec_identity

    matches = [candidate for candidate in candidates
               if candidate["material_code"].casefold() == _text(item.get("material_code")).casefold()]
    exact = [row for row in matches if row["purchase_key"] == prior.get("purchase_key")]
    if exact:
        return exact
    refresh = [row for row in matches if _same_logical_purchase_row(item, prior, row)]
    if refresh:
        return refresh
    fact = prior.get("purchase_fact") if isinstance(prior.get("purchase_fact"), dict) else {}
    document = _text(fact.get("source_doc_no") or item.get("source_doc_no")).casefold()
    scoped = [row for row in matches if _text(row["purchase_fact"].get("source_doc_no")).casefold() == document]
    if scoped:
        matches = scoped
    elif _text(fact.get("source_doc_no")):
        return []
    spec = _text(item.get("spec_model")).casefold()
    if spec and len(matches) > 1:
        matches = [row for row in matches if not row["spec_model"]
                   or spec_identity(row["spec_model"], item, material_code=row.get("material_code"))
                   == spec_identity(spec, item, material_code=item.get("material_code"))]
    return matches


def _same_logical_purchase_row(item: dict, prior: dict, candidate: dict) -> bool:
    """Permit source refresh only with a stable lineage and source row identity."""
    from .purchase_value_evidence import spec_identity

    lineage = _text(prior.get("purchase_logical_source_id"))
    if not lineage or lineage != _text(candidate.get("purchase_logical_source_id")):
        return False
    if not _same_stable_purchase_row(prior, candidate):
        return False
    item_spec = _text(item.get("spec_model")).casefold()
    candidate_spec = _text(candidate.get("spec_model")).casefold()
    return (not item_spec or not candidate_spec
            or spec_identity(item_spec, item, material_code=item.get("material_code"))
               == spec_identity(candidate_spec, item, material_code=candidate.get("material_code")))


def _same_stable_purchase_row(prior: dict, candidate: dict) -> bool:
    return bool(prior.get("purchase_row_stable") and candidate.get("purchase_row_stable")
                and _text(prior.get("purchase_row_id")) == _text(candidate.get("purchase_row_id"))
                and _text(prior.get("purchase_table_name")) == _text(candidate.get("purchase_table_name")))


def enrich_logistics_purchase_facts(items: list[dict], sources: list[dict], *, fx_rates: dict | None = None) -> dict:
    """Return {items, unresolved}; never mutate inputs, fetch sources, or write data.

    Previously imported PURCHASE_EXPENSE_OA rows remain unchanged. For new
    OA_LOGISTICS_ROW records, quantity is a shipment placeholder rather than a
    purchase fact unless explicitly protected by a saved manual override.
    """
    enriched = deepcopy(items or [])
    candidates = _purchase_candidates(sources, fx_rates)
    unresolved = []
    reported = set()
    for item in enriched:
        if _text(item.get("source_type")).upper() != "OA_LOGISTICS_ROW":
            continue
        metadata = _metadata(item)
        prior = metadata.get("logistics_row")
        prior = deepcopy(prior) if isinstance(prior, dict) else {}
        manual = str(item.get("manual_override_flag") or "0") not in {"0", "False", "false"}
        fact = deepcopy(prior.get("purchase_fact")) if isinstance(prior.get("purchase_fact"), dict) else {}
        if not prior:
            for field in FACT_FIELDS:
                if field not in {"source_doc_no", "source_type"}:
                    fact[field] = item.get(field)
            if not manual:
                item["quantity"] = fact["quantity"] = None
                for field in ("goods_value", "unit_price"):
                    if _number(item.get(field)) == 0:
                        item[field] = fact[field] = None
            if _missing(item.get("goods_value")):
                item["goods_value"] = None
        matches = _matching_candidates(item, prior, candidates)
        selected = matches[0] if len(matches) == 1 else None
        refreshed = bool(selected and prior.get("purchase_key") and (
            selected["purchase_key"] == prior.get("purchase_key")
            and _same_stable_purchase_row(prior, selected)
            or _same_logical_purchase_row(item, prior, selected)
        ) and (fact != selected["purchase_fact"]
               or prior.get("purchase_source_id") != selected["purchase_source_id"]
               or prior.get("purchase_row_id") != selected["purchase_row_id"]))
        if selected:
            if _missing(fact.get("goods_value")) and selected.get("fx_unresolved") and selected["fx_unresolved"] not in reported:
                unresolved.append({"item_name": item.get("name"), "material_code": item.get("material_code"),
                                   "message": selected["fx_unresolved"], "purchase_source_ids": [selected["purchase_source_id"]]})
                reported.add(selected["fx_unresolved"])
            if refreshed:
                history = list(prior.get("purchase_fact_history") or [])
                history.append({"purchase_key": prior.get("purchase_key"),
                                "purchase_source_id": prior.get("purchase_source_id"),
                                "purchase_row_id": prior.get("purchase_row_id"),
                                "purchase_logical_source_id": prior.get("purchase_logical_source_id"),
                                "purchase_table_name": prior.get("purchase_table_name"),
                                "purchase_fact": deepcopy(fact),
                                "replaced_by_source_id": selected["purchase_source_id"],
                                "replaced_by_row_id": selected["purchase_row_id"]})
                prior["purchase_fact_history"] = history[-20:]
                fact = deepcopy(selected["purchase_fact"])
                prior["purchase_key"] = selected["purchase_key"]
            else:
                for field, value in selected["purchase_fact"].items():
                    if _missing(fact.get(field)):
                        fact[field] = value
            if not prior.get("purchase_key"):
                prior["purchase_key"] = (_text(item.get("name")) if manual else "") or selected["purchase_key"]
            for field in ("purchase_source_id", "purchase_row_id", "purchase_logical_source_id",
                          "purchase_table_name", "purchase_row_stable"):
                if refreshed:
                    prior[field] = selected[field]
                else:
                    prior.setdefault(field, selected[field])
        elif not prior.get("purchase_key"):
            code = _text(item.get("material_code")) or _text(item.get("name"))
            documents = sorted({_text(row["purchase_fact"]["source_doc_no"]) for row in matches})
            message = (f"{code} 对应多个采购明细（{'、'.join(documents)}），采购关联待核对。"
                       if matches else f"{code} 未匹配到采购明细，采购数量和货值待补。")
            if message not in reported:
                unresolved.append({"item_name": item.get("name"), "material_code": item.get("material_code"),
                                   "message": message, "purchase_source_ids": sorted({row["purchase_source_id"] for row in matches})})
                reported.add(message)
        for field in FACT_FIELDS:
            fact.setdefault(field, None)
        # Never copy an entire purchase total onto each shipment row. Existing
        # apportioned totals and manual corrections remain available to reconcile.
        for field in ("unit_price", "purchase_currency", "purchase_uom", "unit_price_uom"):
            if (refreshed and not manual or _missing(item.get(field))) and not _missing(fact.get(field)):
                item[field] = fact[field]
        prior.setdefault("purchase_key", "")
        prior["purchase_fact"] = fact
        metadata["logistics_row"] = prior
        item["extra_json"] = json.dumps(metadata, ensure_ascii=False, default=str)
        if selected:
            item["_purchase_fact_enriched"] = True
    return {"items": enriched, "unresolved": unresolved}
