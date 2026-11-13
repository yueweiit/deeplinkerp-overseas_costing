"""Keep persisted item extra_json to operational facts, not analysis caches."""
from copy import deepcopy

from .application import row_meta
from .model import dumps

ITEM_FIELDS = (
    'name', 'material_code', 'product_name', 'spec_model',
    'quantity', 'unit', 'actual_shipped_qty', 'shipped_uom',
    'unit_price', 'purchase_currency', 'purchase_uom', 'unit_price_uom',
    'goods_value', 'source_doc_no', 'supplier', 'source_type',
)
NESTED_KEYS = ('ai_fill_original_values', 'settlement_original_values')
CARGO_KEYS = (
    'material_code', 'product_name', 'spec_model', 'quantity', 'unit',
    'source_snapshot', 'binding_id', 'line_key',
)
FACT_FIELDS = (
    'material_code', 'quantity', 'goods_value', 'unit_price', 'purchase_currency',
    'purchase_uom', 'unit_price_uom', 'source_doc_no', 'source_type',
)
ANALYSIS_KEYS = (
    'ai_row_fields', 'ai_row_selection', 'autofill_review',
    'packing_source_history', 'purchase_fact_history',
)


def store_missing_numbers(values, fields, missing):
    """Store absent numeric columns as zero while keeping their absence explicit."""
    for field in fields:
        if values.get(field) is None:
            values[field] = 0
            missing.add(field)


def original_value_snapshot(item, *, depth=0):
    """Keep purchase identity and prior amounts, never nested extra_json or source grids."""
    if not isinstance(item, dict) or depth > 2:
        return {}
    meta = row_meta(item)
    source = {**meta, **{key: value for key, value in item.items() if key != 'extra_json'}}
    snapshot = {}
    for key in ITEM_FIELDS:
        if source.get(key) not in (None, ''):
            snapshot[key] = deepcopy(source[key])
    if depth < 2:
        for key in NESTED_KEYS:
            nested = source.get(key)
            if isinstance(nested, dict) and nested:
                compact = original_value_snapshot(nested, depth=depth + 1)
                if compact:
                    snapshot[key] = compact
    return snapshot


def compact_cargo(cargo):
    if not isinstance(cargo, dict):
        return {}
    result = {key: deepcopy(cargo[key]) for key in CARGO_KEYS if key in cargo}
    price = cargo.get('merchandise_price')
    if isinstance(price, dict):
        result['merchandise_price'] = {key: deepcopy(price[key])
            for key in ('present', 'price', 'currency', 'price_uom', 'ambiguous') if key in price}
    return result


def persist_item_meta(meta):
    return dumps(compact_row_meta(meta if isinstance(meta, dict) else {}))


def prune_analysis_cache(meta):
    return compact_row_meta(meta if isinstance(meta, dict) else {})


def prune_version_meta(meta):
    result = dict(meta) if isinstance(meta, dict) else {}
    result.pop('ai_row_applications', None)
    return result


def persist_calculated_item(item):
    row = dict(item or {})
    row['extra_json'] = persist_item_meta(prune_analysis_cache(row_meta(row)))
    return row


def compact_row_meta(meta):
    if not isinstance(meta, dict):
        return {}
    if 'doctype' in meta and 'extra_json' in meta:
        meta = row_meta(meta)
    if not isinstance(meta, dict):
        return {}
    result = dict(meta)
    for key in NESTED_KEYS:
        if isinstance(result.get(key), dict):
            result[key] = original_value_snapshot(result[key])
    history = result.get('packing_source_history')
    if isinstance(history, list):
        compacted = []
        for entry in history:
            if not isinstance(entry, dict):
                compacted.append(entry)
                continue
            entry = dict(entry)
            if isinstance(entry.get('previous'), dict):
                entry['previous'] = original_value_snapshot(entry['previous'])
            compacted.append(entry)
        result['packing_source_history'] = compacted
    if isinstance(result.get('logistics_row'), dict):
        result['logistics_row'] = _compact_logistics_row(result['logistics_row'])
    if isinstance(result.get('ai_row_fields'), dict):
        result['ai_row_fields'] = {field: _compact_ref(ref) for field, ref in result['ai_row_fields'].items()}
    if isinstance(result.get('ai_row_selection'), dict):
        result['ai_row_selection'] = _compact_ref(result['ai_row_selection'])
    if isinstance(result.get('ai_row_packing_values'), dict):
        result['ai_row_packing_values'] = {key: result['ai_row_packing_values'].get(key)
            for key in ('package_count', 'packaging_type') if key in result['ai_row_packing_values']}
    if isinstance(result.get('effective_logistics_source'), dict):
        result['effective_logistics_source'] = _compact_source_context(result['effective_logistics_source'])
    if isinstance(result.get('settlement_physical'), dict):
        physical = dict(result['settlement_physical'])
        physical.pop('evidence', None)
        result['settlement_physical'] = physical
    for key in ANALYSIS_KEYS:
        result.pop(key, None)
    return result


def _compact_ref(value):
    if not isinstance(value, dict):
        return value
    return {key: deepcopy(value[key]) for key in ('row_id', 'origin') if key in value}


def _compact_logistics_row(row):
    keys = ('identity', 'purchase_key', 'purchase_source_id', 'purchase_row_id',
            'purchase_logical_source_id', 'purchase_table_name', 'purchase_row_stable')
    result = {key: deepcopy(row[key]) for key in keys if key in row}
    fact = row.get('purchase_fact')
    if isinstance(fact, dict):
        result['purchase_fact'] = {key: deepcopy(fact[key]) for key in FACT_FIELDS if key in fact}
    packing = row.get('packing')
    if isinstance(packing, dict):
        result['packing'] = {key: deepcopy(packing[key])
            for key in ('package_count', 'packaging_type') if key in packing}
    return result


def _compact_source_context(context):
    from overseas_costing.services.effective_logistics_source import public_context
    return public_context(context)
