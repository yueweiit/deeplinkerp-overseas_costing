"""Group one archived goods table without losing its original row identities."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import unicodedata

from .purchase_value_evidence import number, row_context, shipment_row_cells, text
from .source_review_extract_service import _approval_goods_locations, _approval_row_details
from overseas_costing.utils.field_mapper import map_oa_row_to_item, normalize_unit

META_KEY = 'logistics_cargo_group'
AUTO_SCOPE_EXCLUSION_REASON = '系统按国际物流物料范围自动排除'
PHYSICAL = ('net_weight_kg', 'gross_weight_kg', 'volume_m3',
            'volume_weight_kg', 'chargeable_weight_kg', 'package_count')
BOUNDARIES = ('material_code', 'spec_model', 'unit', 'supplier', 'recipient',
              'project_collection', 'purchase_currency')
TEXT_FIELDS = ('purchase_currency', 'recipient', 'supplier', 'purchase_uom',
               'unit_price_uom', 'unit', 'shipped_uom', 'project_collection', 'product_name')


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def metadata(row):
    value = row.get('extra_json') or {}
    return json.loads(value) if isinstance(value, str) else deepcopy(value)


def group_goods(source):
    """Sum only additive fields. Missing measurements stay missing, not zero."""
    groups = {}
    for table, position, cells in _approval_goods_locations(source):
        cells = row_context(cells, source)
        row = map_oa_row_to_item(cells)
        quantity, unit = shipment_row_cells(cells)
        details, _ = _approval_row_details(cells, source, 'international_logistics', tuple)
        row.update({field: value for field, value in details.items() if field != 'supplier'})
        row['quantity'] = text(quantity[2]) if quantity else None
        row['unit'] = normalize_unit(unit[1]) if unit else None
        key = (table, *(normalized(row.get(field)) for field in BOUNDARIES))
        # An absent specification does not prove that two physical lines match.
        if not normalized(row.get('spec_model')):
            key += (position,)
        group = groups.setdefault(key, {**row, '_source_table': table, '_source_rows': []})
        content = source.get('form_fields', {}).get(table)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                content = None
        native = content[position - 1] if isinstance(content, list) else {}
        native = native if isinstance(native, dict) else {}
        group['_source_rows'].append({
            'table': table, 'row': position,
            'source_row_id': native.get('rowId') or native.get('row_id') or native.get('rowNumber') or '',
            'values': deepcopy(row), 'cells': deepcopy(cells),
        })
    for key, group in groups.items():
        for field in ('quantity', *PHYSICAL):
            values = [number(line['values'].get(field)) for line in group['_source_rows']]
            group[field] = text(sum(values, Decimal(0))) if all(v is not None for v in values) else None
        identity = json.dumps([source['source_id'], *key], ensure_ascii=False)
        group['_group_key'] = 'logistics:' + hashlib.sha256(identity.encode()).hexdigest()[:32]
    return list(groups.values())


def initial_members(items, group, source):
    """Only same-approval source-anchored rows can supply aggregated purchase facts."""
    positions = {line['row'] for line in group['_source_rows']}
    result = []
    for item in items:
        meta = metadata(item)
        prior = meta.get(META_KEY) or {}
        if prior.get('key') == group['_group_key'] and prior.get('item_names'):
            return [item]
        ref = (meta.get('initial_logistics_source') or {}).get('source_ref') or {}
        if (ref.get('approval_no') == source.get('approval_no')
                and ref.get('field') == group['_source_table'] and ref.get('row') in positions
                and normalized(item.get('material_code')) == normalized(group.get('material_code'))
                and normalized(item.get('spec_model')) == normalized(group.get('spec_model'))):
            result.append(item)
    return sorted(result, key=lambda item: (
        (metadata(item).get('initial_logistics_source') or {}).get('source_ref', {}).get('row', 0),
        item['name']))


def protected_members(members, group):
    """Compare source-owned fields with their last source snapshot before merging."""
    lines = {line['row']: line['values'] for line in group['_source_rows']}
    for item in members:
        meta = metadata(item)
        if (item.get('manual_override_flag') or item.get('parse_status') == 'MANUAL'
                or item.get('actual_shipped_qty_mode') == 'MANUAL_CONFIRMED'
                or meta.get('manual_shipment_valuation')
                or item.get('is_excluded') and (
                    item.get('excluded_by') != 'system'
                    or item.get('exclusion_reason') != AUTO_SCOPE_EXCLUSION_REASON)):
            return True
        prior = meta.get(META_KEY) or {}
        if not prior and number(item.get('unit_price')) not in (None, Decimal(0)):
            return True
        position = (meta.get('initial_logistics_source') or {}).get('source_ref', {}).get('row')
        expected = prior.get('values') or lines.get(position) or {}
        expected_text = prior.get('text_values') or {
            **expected, 'purchase_uom': expected.get('unit'),
            'unit_price_uom': expected.get('unit'), 'shipped_uom': expected.get('unit'),
        }
        for field in TEXT_FIELDS:
            current = item.get(field)
            expected_value = expected_text.get(field)
            if field in {'purchase_uom', 'unit_price_uom', 'unit', 'shipped_uom'}:
                current, expected_value = normalize_unit(current), normalize_unit(expected_value)
            if current and normalized(current) != normalized(expected_value):
                return True
        if (not prior and item.get('actual_shipped_qty_mode') == 'EXPLICIT_SOURCE'
                and not item.get('is_excluded')
                and number(item.get('actual_shipped_qty')) != number(expected.get('quantity'))):
            return True
        if (not prior and normalize_unit(item.get('purchase_uom') or item.get('unit'))
                != normalize_unit(expected.get('unit'))):
            return True
        for field in ('quantity', 'goods_value', 'unit_price', 'actual_shipped_qty', *PHYSICAL):
            if field in expected and number(item.get(field)) != number(expected[field]):
                # Frappe persists absent Float fields as zero.
                if number(item.get(field)) not in (None, Decimal(0)) or number(expected[field]) not in (None, Decimal(0)):
                    return True
    return False


def group_metadata(group, source, members, values):
    return {
        'key': group['_group_key'], 'source_id': source['source_id'],
        'approval_no': source.get('approval_no'), 'table': group['_source_table'],
        'source_rows': deepcopy(group['_source_rows']),
        'item_names': sorted({item['name'] for item in members}
                             | {name for item in members for name in
                                (metadata(item).get(META_KEY) or {}).get('item_names', [])}),
        'values': {field: text(number(values.get(field))) if number(values.get(field)) is not None else None for field in
                   ('quantity', 'goods_value', 'unit_price', 'actual_shipped_qty', *PHYSICAL)},
        'text_values': {field: values.get(field) for field in TEXT_FIELDS},
    }
