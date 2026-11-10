"""Project archived OA logistics lines for an estimated intake draft."""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache

from .. import purchase_value_evidence as value
from ..material_value_semantics import is_effectively_missing, is_placeholder_token
from ..shipment_cost_service import object_json, shipment_input
from ..source_review_extract_service import _approval_goods_locations, _approval_row_details
from .model import decoded, digest

ROLE = 'international_logistics'
SOURCE_META = 'initial_logistics_source'
REVIEW_META = 'initial_logistics_value_review'
GOODS_TABLES = tuple(value.key(label) for label in (
    '货物信息', '货物明细', '运输货物', 'Bienes', 'Lista de mercancías', 'Goods details',
))
PROTECTED_VALUATION = {
    value.META_KEY, 'manual_shipment_valuation', 'shipment_valuation', 'settlement_valuation', 'settlement_cargo',
    'purchase_value_history', 'purchase_value_retired', 'shipment_valuation_history',
}
SUPPLEMENT_FIELDS = (
    'product_name', 'spec_model', 'recipient', 'supplier', 'project_collection',
    'net_weight_kg', 'gross_weight_kg', 'volume_m3',
    'volume_weight_kg', 'chargeable_weight_kg',
)


def _source_document(source):
    """Use top-level form components so a sibling line cannot supply header currency."""
    from overseas_costing.scripts.import_oa_logistics import (
        PROJECT_OWNERSHIP_FIELD_ALIASES, _get_form_components,
        extract_form_fields,
    )

    fields, goods = {}, defaultdict(list)
    header_aliases = {value.key(label) for label in (*value.CURRENCY_FIELDS, *PROJECT_OWNERSHIP_FIELD_ALIASES)}
    for component in _get_form_components(source.get('raw') or {}):
        if not isinstance(component, dict):
            continue
        label = str(component.get('name') or component.get('label') or '')
        normalized = value.key(label)
        if any(alias in normalized for alias in GOODS_TABLES):
            goods[label].append(decoded(component.get('value')))
        elif normalized in header_aliases:
            header = {key: component[key] for key in ('name', 'label', 'value', 'extValue', 'ext_value') if key in component}
            content = extract_form_fields({'formComponentValues': [header]}).get(label)
            if content not in (None, ''):
                fields[label] = [] if label in fields and fields[label] != content else content
    return {'form_fields': fields}, goods


def _archive_lines(source, goods):
    """Only original form lines are trusted. Legacy parser rows supply a fallback skeleton."""
    indexed = defaultdict(list)
    for line in source.get('goods') or []:
        indexed[(line.get('source_table'), line.get('source_position'))].append(line)
    for table, occurrences in goods.items():
        if len(occurrences) != 1:
            continue
        content = occurrences[0]
        # The review parser recognizes Bienes, while archive templates also use 货物明细.
        parsed = _approval_goods_locations({'form_fields': {'货物信息': content}})
        for _, position, cells in parsed:
            matches = indexed.pop((table, position), [])
            entry = content[position - 1] if isinstance(content, list) else None
            native_id = str(entry.get('rowId') or entry.get('row_id') or '') if isinstance(entry, dict) else ''
            yield table, position, native_id, cells, matches[0] if len(matches) == 1 else {}
    for (table, position), matches in indexed.items():
        for line in matches:
            yield table, position, '', None, line


def _approval_ref(source, table, position, native_id):
    return {
        'source': 'approval_form', 'file': '钉钉审批正文', 'source_id': source.get('id'),
        'source_hash': source.get('snapshot') or source.get('fingerprint'),
        'approval_no': source.get('approval_no') or '', 'field': table, 'row': position,
        'source_row_id': native_id,
    }


def project_initial_logistics_items(source, *, fx_rates=None, supplier_rows=tuple):
    """Return item values and issues without touching the archive or the ledger."""
    document, goods = _source_document(source)
    lines = [(table, position, native_id, value.row_context(raw, document) if raw is not None else {}, legacy)
             for table, position, native_id, raw, legacy in _archive_lines(source, goods)]
    locator_counts = Counter((table, native_id or position) for table, position, native_id, _, _ in lines)
    legacy_counts = Counter(line.get('line_key') for _, _, _, _, line in lines)
    unidentified = Counter((table, digest(raw)) for table, _, native_id, raw, _ in lines if not native_id)
    rows, issues = [], []
    supplier_rows = lru_cache(maxsize=1)(supplier_rows)
    approved = bool(source.get('approved')) and not source.get('invalid')
    running = source.get('status') == 'RUNNING' and not source.get('invalid')
    allowed = source.get('kind') == 'logistics' and (approved or running)

    for table, position, native_id, cells, legacy in lines:
        ref = _approval_ref(source, table, position, native_id)
        mapped = value.mapped_row(cells) if cells else {}
        details, detail_refs = _approval_row_details(cells, document, ROLE, supplier_rows) if cells else ({}, {})
        purchase_qty, purchase_unit = value._purchase_row_cells(cells)
        shipping_qty, shipping_unit = value.shipment_row_cells(cells)
        quantity = value.text(purchase_qty[2]) if purchase_qty else legacy.get('quantity')
        unit = purchase_unit[2] if purchase_unit else legacy.get('unit')
        if quantity is None:
            issues.append({'row': ref, 'code': 'OA_INITIAL_QUANTITY_MISSING', 'message': '来源物料行缺少采购数量。'})
            continue
        material = {k: mapped.get(k) or legacy.get(k) for k in ('material_code', 'product_name', 'spec_model')}
        material.update({field: details[field] for field in SUPPLEMENT_FIELDS if field in details})
        legacy_line = legacy.get('line_key')
        unique = (locator_counts[table, native_id or position] == 1
                  and (native_id or (legacy_counts[legacy_line] <= 1 or not legacy_line)
                       and unidentified[table, digest(cells)] == 1))
        stable_key = digest('oa-initial-logistics-line-v1', source.get('id'), table, native_id or position)
        source_meta = {
            'approval_status': source.get('status') or '', 'provisional': running,
            'source_ref': ref, 'fields': {field: entry['column'] for field, entry in detail_refs.items()},
        }
        if 'package_count' in details:
            source_meta['package_count'] = details['package_count']
        metadata = {SOURCE_META: source_meta}
        if legacy_line and legacy_counts[legacy_line] == 1:
            metadata['initial_logistics_line'] = legacy_line
        row = {
            **{field: content for field, content in material.items() if content not in (None, '')},
            'row_no': len(rows) + 1, 'source_type': 'oa_logistics', 'quantity': quantity, 'unit': unit,
            'extra_json': metadata,
        }
        if unique:
            row['stable_line_key'] = stable_key
        if shipping_qty and shipping_qty[2] > 0 and shipping_unit:
            if shipping_qty[0] != (purchase_qty or ('',))[0] or shipping_unit[0] != (purchase_unit or ('',))[0]:
                row.update(actual_shipped_qty=value.text(shipping_qty[2]), shipped_uom=shipping_unit[2],
                           actual_shipped_qty_mode='EXPLICIT_SOURCE', actual_shipped_qty_source_revision=ref['source_hash'])

        fact, original_amount, reason = value.row_fact_status(cells, role=ROLE, refs=[ref], fx_rates=fx_rates)
        code = 'OA_INITIAL_VALUE_NEEDS_REVIEW'
        if fact and fact['currency_scope'] == 'default':
            fact, reason = None, '来源货值未注明币种，初始草稿暂不换算。'
        if not allowed:
            fact, code, reason = None, 'OA_INITIAL_APPROVAL_NOT_ELIGIBLE', '来源审批状态不允许暂估采用货值。'
        elif not unique or not cells:
            fact, code, reason = None, 'OA_INITIAL_SOURCE_LINE_UNVERIFIED', '缺少唯一可核对的原始货物明细行。'
        if fact:
            fact = value.bind_purchase_value(row, {**fact, 'approval_status': source.get('status'), 'provisional': running})
            if fact:
                valuation = value.shipment_valuation(row, fact)
                if valuation['amount_rmb'] is not None:
                    row['goods_value'] = valuation['amount_rmb']
                    metadata[value.META_KEY] = fact
                else:
                    code = valuation['error']
                    reason = value.PURCHASE_VALUE_REVIEW_MESSAGES[code]
            else:
                reason = '来源货值缺少可核对的物料编码或规格。'
        if value.META_KEY not in metadata:
            issue = {'row': ref, 'code': code, 'message': reason or '来源缺少唯一有效的逐行货值。'}
            issues.append(issue)
            metadata[REVIEW_META] = {
                'original_amount': original_amount,
                'currency_fields': {label: cell for label, cell in cells.items()
                                    if value.key(label) in {value.key(alias) for alias in value.CURRENCY_FIELDS}},
                'code': issue['code'], 'message': issue['message'],
            }
        rows.append(row)

    amounts = [value.number(row['goods_value']) for row in rows if 'goods_value' in row]
    return {
        'rows': rows, 'issues': issues,
        'goods_value_summary': {'row_count': len(rows), 'valued_count': len(amounts),
                                'amount_rmb': value.text(sum(amounts, Decimal(0))) if amounts else None},
    }


def _same_item(left, right):
    code = str(left.get('material_code') or '').strip().casefold()
    left_spec = str(left.get('spec_model') or '').strip().casefold()
    right_spec = str(right.get('spec_model') or '').strip().casefold()
    quantity = value.number(left.get('quantity'))
    return bool(code and code == str(right.get('material_code') or '').strip().casefold()
                and (is_placeholder_token(left_spec) or is_placeholder_token(right_spec) or left_spec == right_spec)
                and quantity is not None and quantity > 0 and quantity == value.number(right.get('quantity'))
                and value.same_uom(left.get('purchase_uom') or left.get('unit'), right.get('unit')))


def _item_matches(item, rows):
    meta = object_json(item.get('extra_json'))
    source_line = meta.get('initial_logistics_line')
    if source_line:
        return [row for row in rows if row['extra_json'].get('initial_logistics_line') == source_line and _same_item(item, row)]
    return [row for row in rows if _same_item(item, row)]


def plan_existing_initial_items(source, existing_items, *, fx_rates=None, supplier_rows=tuple):
    """Plan missing amounts on uniquely matched initial rows. The caller guards draft eligibility."""
    plan = project_initial_logistics_items(source, fx_rates=fx_rates, supplier_rows=supplier_rows)
    patches, item_results = [], []
    matches = [_item_matches(item, plan['rows']) for item in existing_items]
    unique_targets = Counter(row['row_no'] for candidates in matches for row in candidates)
    for item, candidates in zip(existing_items, matches):
        name = item.get('name')
        meta = object_json(item.get('extra_json'))
        result = {'name': name}
        if (not name or item.get('source_type') != 'oa_logistics' or item.get('is_active') in (0, '0')
                or item.get('is_excluded') in (1, '1') or item.get('parse_status') == 'MANUAL'
                or item.get('manual_override_flag') in (1, '1')
                or item.get('actual_shipped_qty_mode') == 'MANUAL_CONFIRMED'):
            result['code'] = 'OA_INITIAL_ITEM_NOT_ELIGIBLE'
        elif (set(meta) & PROTECTED_VALUATION or not is_effectively_missing('goods_value', item.get('goods_value'), item)
              or not is_effectively_missing('unit_price', item.get('unit_price'), item)
              or not is_placeholder_token(item.get('purchase_currency'))):
            result['code'] = 'OA_INITIAL_ITEM_HAS_VALUE'
        elif len(candidates) != 1 or unique_targets[candidates[0]['row_no']] != 1:
            result['code'] = 'OA_INITIAL_ITEM_MATCH_UNVERIFIED'
        else:
            row = candidates[0]
            fact = row['extra_json'].get(value.META_KEY)
            patch = {field: row[field] for field in SUPPLEMENT_FIELDS if field in row
                     and is_effectively_missing(field, item.get(field), item)}
            target = {**item, **patch}
            original_shipping, current_shipping = shipment_input(row), shipment_input(target)
            if (original_shipping['quantity'] != current_shipping['quantity']
                    or not value.same_uom(original_shipping['uom'], current_shipping['uom'])):
                result['code'] = 'OA_INITIAL_ITEM_SHIPPING_CHANGED'
            else:
                bound = value.bind_purchase_value(target, fact) if fact else None
                valuation = value.shipment_valuation(target, bound) if bound else {}
                if valuation.get('amount_rmb') is None:
                    result['code'] = valuation.get('error') or 'OA_INITIAL_ITEM_VALUE_UNVERIFIED'
                else:
                    patch['goods_value'] = valuation['amount_rmb']
                    patch['extra_json'] = {**deepcopy(meta), SOURCE_META: row['extra_json'][SOURCE_META], value.META_KEY: bound}
                    patch['extra_json'].pop(REVIEW_META, None)
                    patches.append({'name': name, 'values': patch})
                    result['code'] = 'OA_INITIAL_ITEM_READY'
        item_results.append(result)
    return {**plan, 'patches': patches, 'item_results': item_results}
