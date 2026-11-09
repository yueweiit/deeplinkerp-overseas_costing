"""Coherent row purchase totals, independent of source priority and shipping quantity.

Only server-resolved evidence may create these facts. Amount, currency and the
quantity/unit they price are one bundle; changing shipment quantity cannot change
the purchase denominator. See docs/source-field-priority.md.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from overseas_costing.utils.field_mapper import map_oa_row_to_item, normalize_unit

META_KEY = 'adopted_purchase_value'
CURRENCY_FIELDS = ('币种', '币种Moneda', 'Moneda', '采购币种', 'purchase_currency', 'currency')


def number(value):
    try:
        from .logistics_settlement.model import number as parse_number
        parsed = parse_number(value)
        result = Decimal(parsed) if parsed is not None else None
        if result is None:
            return None
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def text(value):
    return format(value, 'f')


def key(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', ''.join(
        c for c in unicodedata.normalize('NFKD', str(value)).casefold()
        if not unicodedata.combining(c)))


def uom(value):
    from .source_review_extract_service import normalize_weight_uom
    return normalize_unit(normalize_weight_uom(value))


_DEDICATED_VALUE_ALIASES = ('货值', '采购货值', '总货值', '货值Valor de mercancía',
                            'Valor de mercancía', 'goods_value')
_PURCHASE_TOTAL_ALIASES = ('总金额Monto Total', '总金额', 'Monto Total', '总价')
_CURRENCY_TOKEN = re.compile(r'(?i)(?<![a-z])(?:RMB|CNY|USD|MXN)(?![a-z])|人民币|美元|比索|[¥￥]')


def row_context(raw, document):
    """Unwrap cells, inheriting only an explicitly stated document currency."""
    result = {k: (v.get('raw_value') if isinstance(v, dict) and 'raw_value' in v else v)
              for k, v in raw.items()}
    aliases = {key(v) for v in CURRENCY_FIELDS}
    if not any(key(k) in aliases and v not in (None, '') for k, v in result.items()):
        inherited = {k: v for k, v in (document.get('form_fields') or {}).items()
                     if key(k) in aliases and v not in (None, '')}
        if inherited:
            result.update(inherited)
            result['_review_currency_scope'] = 'header'
    return result


def _purchase_row_cells(raw):
    quantity = _choose_cell(raw, ("采购数量", "采购数量Cantidad comprada", "Cantidad comprada", "purchase_quantity"),
                            ("数量Cantidad", "数量", "Cantidad", "Qty", "quantity"), number)
    unit = _choose_cell(raw, ("采购单位", "采购单位Unidad de compra", "Unidad de compra", "purchase_uom"),
                        ("单位Unidad", "单位", "Unidad", "unit"), uom)
    return quantity, unit


def _choose_cell(raw, specific, generic, normalize):
    """Return a single agreed value and source cell; invalid specifics never fall back."""
    candidates = []
    for aliases in (specific, generic):
        keys = {key(alias) for alias in aliases}
        candidates = [(k, v, normalize(v)) for k, v in raw.items()
                      if key(k) in keys and v not in (None, "")]
        if candidates:
            break
    if not candidates or any(value is None for _, _, value in candidates):
        return None
    return candidates[0] if len({value for _, _, value in candidates}) == 1 else None


def shipment_row_cells(raw):
    """An explicit shipped quantity wins over the goods table's generic quantity."""
    quantity = _choose_cell(
        raw, ("实发数量", "实际发货数量", "发货数量", "实发数量Cantidad enviada",
              "Cantidad enviada", "actual_shipped_qty"),
        ("数量Cantidad", "数量", "Cantidad", "Qty", "quantity"), number,
    )
    unit = _choose_cell(
        raw, ("发货单位", "发货单位Unidad de envío", "Unidad de envío", "shipped_uom"),
        ("单位Unidad", "单位", "Unidad", "unit"), uom,
    )
    return quantity, unit


def row_fact_status(raw, *, role, refs=(), fx_rates=None):
    """Return a fact, or a readable row amount with its actual blocking reason."""
    dedicated_aliases = {key(v) for v in _DEDICATED_VALUE_ALIASES}
    allowed_aliases = (dedicated_aliases | {key(v) for v in _PURCHASE_TOTAL_ALIASES}
                       if role == 'purchase' else dedicated_aliases)
    fields = [(k, v) for k, v in raw.items() if key(k) in allowed_aliases and v not in (None, '')]
    if len(fields) != 1:
        return None, None, ""
    label, raw_amount = fields[0]
    amount = number(_CURRENCY_TOKEN.sub('', str(raw_amount)))
    if amount is None or amount < 0:
        return None, None, ""
    amount_text = text(amount)
    currency_aliases = {key(v) for v in CURRENCY_FIELDS}
    currency_cells = {k: v for k, v in raw.items()
                      if key(k) in currency_aliases and v not in (None, '')}
    from .logistics_settlement.model import currency

    explicit = {currency(v) for v in currency_cells.values()}
    embedded = {currency(v) for v in _CURRENCY_TOKEN.findall(str(raw_amount))}
    currencies = explicit | embedded
    if currencies - {'RMB', 'USD', 'MXN'}:
        return None, amount_text, "来源货值币种无法识别或不受支持，仅供核对。"
    if len(currencies) > 1:
        return None, amount_text, "同一来源行的货值币种相互冲突，仅供核对。"
    cur = next(iter(currencies), '')
    reason = 'explicit'
    dedicated = key(label) in dedicated_aliases and key(label) != key('goods_value')
    if not cur and role == 'international_logistics' and dedicated:
        cur, reason = 'RMB', '国际物流专用货值列未标币种，按人民币采购货值采用。'
    if not cur:
        return None, amount_text, "来源货值未注明币种，仅供核对。"
    mapped = mapped_row(raw)
    quantity, unit = _purchase_row_cells(raw)
    if not quantity or quantity[2] <= 0:
        return None, amount_text, "来源货值缺少同一行有效采购数量或数量冲突，仅供核对。"
    if not unit:
        return None, amount_text, "来源货值缺少同一行采购单位或单位冲突，仅供核对。"
    rate = Decimal(1) if cur == 'RMB' else number((fx_rates or {}).get(cur))
    if rate is None or rate <= 0:
        return None, amount_text, f"来源货值为 {cur}，当前缺少有效人民币换算汇率，仅供核对。"
    return {'original_amount': amount_text, 'currency': cur, 'amount_rmb': text(amount * rate),
            'quantity': text(quantity[2]), 'uom': unit[2], 'rate_to_rmb': text(rate),
            'currency_basis': reason, 'source_type': role, 'source_refs': deepcopy(list(refs)),
            'currency_scope': raw.get('_review_currency_scope') or ('row' if currency_cells else 'amount' if embedded else 'default'),
            'currency_fields': deepcopy(currency_cells), 'amount_field': str(label),
            'quantity_field': str(quantity[0]), 'unit_field': str(unit[0]), 'explicit_zero': amount == 0,
            'material_code': str(mapped.get('material_code') or ''),
            'spec_model': str(mapped.get('spec_model') or ''),
            'default_reason': '按逐字段来源优先级采用；货值、数量和单位来自同一资料行，币种依据随行保存。'}, amount_text, ""


def extract_row_fact(raw, *, role, refs=(), fx_rates=None):
    """Read a row total only when amount, currency, and denominator agree."""
    return row_fact_status(raw, role=role, refs=refs, fx_rates=fx_rates)[0]


def mapped_row(raw):
    from overseas_costing.utils.field_mapper import map_purchase_expense_row_to_item
    result = map_oa_row_to_item(raw)
    purchase = map_purchase_expense_row_to_item(raw)
    return {k: (v if v not in (None, '') else purchase.get(k)) for k, v in result.items()}


def document_rows(document, ref):
    from .logistics_purchase_facts_service import parse_labelled_approval_rows
    if document.get('structured_rows'):
        result = []
        for row in document['structured_rows']:
            if not isinstance(row, dict):
                continue
            if row.get('missing_formula_cache') or row.get('formula_errors'):
                continue
            if any((e or {}).get('cache_status') == 'missing'
                   for f, e in (row.get('field_evidence') or {}).items()
                   if f in {'total_amount', 'quantity', 'unit', 'currency'}):
                continue
            region = (row.get('field_ranges') or {}).get('total_amount') or {}
            if region.get('start_row') != region.get('end_row'):
                continue  # a merged total is not a per-row purchase amount
            raw = row.get('raw_fields') or row
            result.append((row.get('source_row') or (row.get('evidence') or {}).get('row'), row_context(raw, document)))
        return result
    if document.get('semantic_rows'):
        result = []
        for row in document['semantic_rows']:
            if str(row.get('sheet') or '') != str(ref.get('sheet') or ''):
                continue
            raw = {str(cell['header']): cell.get('value') for cell in row.get('cells') or []
                   if isinstance(cell, dict) and cell.get('header')}
            if raw:
                result.append((row.get('source_row'), raw))
        return result
    labelled = parse_labelled_approval_rows(document.get('text'))
    for observation in document.get('vision_observations') or []:
        anchor = observation.get('anchor') or {}
        if any(ref.get(k) and anchor.get(k) and str(ref[k]) != str(anchor[k]) for k in ('page', 'sheet')):
            continue
        labelled.extend(parse_labelled_approval_rows(observation.get('description')))
    return list(enumerate(labelled, 1))


def from_documents(fields, target, refs, documents, *, fx_rates=None, items=None, approval_row_index=None):
    """Bind a total to one server-owned document row, including the table."""
    if 'goods_value' not in fields:
        return None
    from .source_priority_service import classify_workflow_stage
    from .source_review_extract_service import match_approval_goods_rows

    if approval_row_index is None:
        approval_row_index = {}
    facts = []
    for ref in refs:
        document_id = str(ref.get('document_id') or '')
        document = documents.get(document_id, {})
        source = {**(document.get('source_ref') or {}), **document}
        role = source.get('approval_role') or ''
        if role == 'purchase' and ('payment' == (document.get('_review_source_identity') or {}).get('workflow_stage')
                                   or classify_workflow_stage(source) == 'payment'):
            role = 'logistics_expense'
        if document.get('form_fields'):
            if document_id not in approval_row_index:
                from collections import defaultdict
                by_target_table = defaultdict(list)
                for table, index, raw, _, item, ambiguous in match_approval_goods_rows(
                        items if items is not None else [target], source):
                    if not ambiguous and item:
                        by_target_table[(item.get('name'), table)].append((table, index, raw))
                approval_row_index[document_id] = by_target_table
            rows = approval_row_index[document_id].get((target.get('name'), str(ref.get('field') or '')), ())
        else:
            rows = [(ref.get('field') or '', index, raw) for index, raw in document_rows(document, ref)]
        for table, index, raw in rows:
            if ref.get('row') and int(ref['row']) != index:
                continue
            mapped = mapped_row(raw)
            if str(mapped.get('material_code') or '').strip().casefold() != str(target.get('material_code') or '').strip().casefold():
                continue
            if (mapped.get('spec_model') and target.get('spec_model')
                    and str(mapped['spec_model']).strip().casefold() != str(target['spec_model']).strip().casefold()):
                continue
            locator = {**ref, 'row': index, 'field': table}
            if raw.get('_dingtalk_row_number'):
                locator['source_row_id'] = str(raw['_dingtalk_row_number'])
            fact = extract_row_fact(raw, role=role, refs=[locator], fx_rates=fx_rates)
            if fact and number(fields['goods_value']) in {number(fact['original_amount']), number(fact['amount_rmb'])}:
                if document.get('_review_source_identity'):
                    fact['_review_source_identity'] = deepcopy(document['_review_source_identity'])
                facts.append(fact)
    # Multiple locators for the same source row are not multiple purchase rows.
    import json
    distinct = {json.dumps({k: v for k, v in fact.items() if k != 'source_refs'}, sort_keys=True): fact for fact in facts}
    identities = {(ref.get('document_id'), ref.get('sheet'), ref.get('field'), ref.get('row'), ref.get('source_row_id'))
                  for fact in facts for ref in fact['source_refs']}
    return next(iter(distinct.values())) if len(distinct) == 1 and len(identities) == 1 else None


def identity_matches(row, fact, *, check_uom=True):
    """Stable row keys are not evidence that the material itself is unchanged."""
    identity = lambda value: str(value or '').strip().casefold()
    if (not identity(fact.get('material_code'))
            or any(identity(row.get(field)) != identity(fact.get(field))
                   for field in ('material_code', 'spec_model'))):
        return False
    return not check_uom or same_uom(row.get('shipped_uom') or row.get('unit') or row.get('purchase_uom'), fact.get('uom'))


def same_uom(left, right):
    """Compare known ERP aliases without changing source evidence or converting quantities."""
    from .erp_routing_service import normalize_erp_uom

    left, right = uom(left), uom(right)
    if not left or not right:
        return False
    if left == right:
        return True
    canonical = normalize_erp_uom(left)
    return bool(canonical and canonical == normalize_erp_uom(right))


def _target_identity(row):
    from .material_value_semantics import is_placeholder_token

    def normalized(value):
        return '' if is_placeholder_token(value) else str(value).strip().casefold()

    name = str(row.get('name') or '').strip()
    return {
        'line_key': str(row.get('stable_line_key') or (f'legacy:{name}' if name else '')).strip(),
        'material_code': normalized(row.get('material_code')),
        'spec_model': normalized(row.get('spec_model')),
    }


def bind_purchase_value(row, fact):
    """Pin a new, server-proven source row to the final confirmed material."""
    target = _target_identity(row)
    source = _target_identity(fact)
    refs = fact.get('source_refs') or []
    anchored = any(ref.get('row') and (ref.get('source_id') or ref.get('document_id')) for ref in refs)
    if (not anchored or not target['line_key'] or not source['material_code']
            or source['material_code'] != target['material_code']
            or (source['spec_model'] and target['spec_model'] and source['spec_model'] != target['spec_model'])):
        return None
    return {**deepcopy(fact), 'target_identity': target}


def purchase_target_matches(row, fact):
    """Old persisted facts keep their original, strict identity behavior."""
    bound = fact.get('target_identity')
    if bound is None:
        return identity_matches(row, fact, check_uom=False)
    target = _target_identity(row)
    return bool(target['line_key'] and target['material_code'] and bound == target)


def retire_purchase_value(meta):
    previous = meta.pop(META_KEY, None)
    if previous:
        meta.setdefault('purchase_value_history', []).append(deepcopy(previous))


def shipment_valuation(row, fact):
    from .shipment_cost_service import shipment_input
    inputs = shipment_input(row)
    quantity, shipped = number(fact.get('quantity')), number(inputs.get('quantity'))
    total = number(fact.get('amount_rmb'))
    error = ''
    if not purchase_target_matches(row, fact):
        error = 'PURCHASE_VALUE_IDENTITY_MISMATCH'
    elif total is None or total < 0 or quantity is None or quantity <= 0 or shipped is None or shipped < 0:
        error = 'PURCHASE_VALUE_QUANTITY_REQUIRED'
    elif not same_uom(inputs.get('uom'), fact.get('uom')):
        error = 'PURCHASE_VALUE_UOM_MISMATCH'
    return {'amount_rmb': None if error else text(total * shipped / quantity),
            'currency': 'RMB', 'quantity': inputs['quantity'], 'uom': inputs['uom'],
            'method': 'source_purchase_total_proration', 'status': 'missing' if error else 'automatic',
            'error': error, 'source_refs': deepcopy(fact.get('source_refs') or []),
            'input_evidence': deepcopy(fact)}


PURCHASE_VALUE_REVIEW_MESSAGES = {
    'PURCHASE_VALUE_IDENTITY_MISMATCH': '采购货值有来源记录，规格或物料身份尚未核对。请重新读取原单后确认。',
    'PURCHASE_VALUE_QUANTITY_REQUIRED': '发货货值缺少有效的实发数量或采购数量，请补充后重新预览。',
    'PURCHASE_VALUE_UOM_MISMATCH': '发货单位与货值依据的采购单位不一致，请核对单位。',
}


def purchase_value_summary(row, fact, valuation):
    """Public amount evidence is not itself a valid shipment valuation."""
    try:
        from frappe import _
    except ImportError:
        _ = lambda message: message
    basis_value = valuation if valuation.get('method') == 'source_purchase_total_proration' else shipment_valuation(row, fact)
    code = basis_value.get('error') or ''
    return {**{key: fact.get(key) for key in ('original_amount', 'currency', 'amount_rmb', 'quantity', 'uom')},
            'status': 'review' if code else 'retain', 'code': code,
            'message': _(PURCHASE_VALUE_REVIEW_MESSAGES.get(code) or '现有货值依据待重新核对。') if code else ''}


def adopted_price(fact, row=None):
    if row is not None and (not purchase_target_matches(row, fact)
                            or not same_uom(row.get('shipped_uom') or row.get('unit') or row.get('purchase_uom'), fact.get('uom'))):
        return None
    total, quantity = number(fact.get('amount_rmb')), number(fact.get('quantity'))
    if total is None or total < 0 or quantity is None or quantity <= 0:
        return None
    from decimal import ROUND_HALF_UP
    calculation_value = total / quantity
    return {'value': format(calculation_value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f'),
            'calculation_value': format(calculation_value, 'f'),
            'currency': 'RMB', 'unit': fact.get('uom'), 'source_type': 'purchase_total_derived',
            'source': fact.get('source_type'), 'error': '', 'evidence': deepcopy(fact)}


def price_valuation(row, metadata, refs, *, fx_rates=None):
    """Value a complete trusted purchase-price tuple using its own denomination."""
    from .shipment_cost_service import shipment_input
    from .logistics_settlement.valuation import value_final_cargo
    fact = (metadata.get('logistics_row') or {}).get('purchase_fact') or {}
    if not isinstance(fact, dict):
        return None
    quantity = number(fact.get('quantity'))
    if (quantity is None or quantity <= 0 or not fact.get('purchase_currency')
            or not fact.get('unit_price_uom') and not fact.get('purchase_uom')
            or not fact.get('source_doc_no')):
        return None
    identity_fact = {**fact, 'uom': fact.get('unit_price_uom') or fact.get('purchase_uom')}
    if not identity_matches(row, identity_fact):
        return None
    inputs = shipment_input(row)
    cargo = {**{field: row.get(field) for field in ('material_code', 'spec_model')},
             'quantity': inputs['quantity'], 'unit': inputs['uom']}
    rates = dict((metadata.get('settlement_valuation') or {}).get('fx_context') or {})
    for currency, rate in (fx_rates or {}).items():
        if currency == 'USD':
            rates['fx_usd_to_rmb'] = rate
        elif currency == 'MXN' and number(rate) is not None and number(rate) > 0:
            rates['fx_rmb_to_mxn'] = text(Decimal(1) / number(rate))
        elif currency.startswith('fx_'):
            rates[currency] = rate
    valuation = value_final_cargo({**row, 'extra_json': metadata}, cargo, rates)
    valuation['source_refs'] = deepcopy(refs)
    return valuation if not valuation.get('error') else None
