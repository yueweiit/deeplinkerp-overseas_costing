"""Effective shipment values, without rewriting read-only purchase facts."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from overseas_costing.utils.field_mapper import normalize_unit
from overseas_costing.services.logistics_settlement.model import currency, digest


def object_json(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _decimal_text(value):
    amount = number(value)
    if amount is None:
        return None
    return format(amount.normalize(), 'f')


def shipment_input(row):
    shipping = row.get('effective_shipping')
    if not isinstance(shipping, dict) or not shipping:
        from overseas_costing.services.material_input_service import resolve_effective_quantity
        shipping = resolve_effective_quantity(row)
    return {'quantity': _decimal_text(shipping.get('quantity')),
            'uom': normalize_unit(shipping.get('uom'))}


def shipment_input_fingerprint(row):
    return digest(shipment_input(row))


def build_manual_shipment_valuation(row, amount_rmb, *, actor, reason='', confirmed_at=''):
    amount = number(amount_rmb)
    if amount is None or amount < 0:
        raise ValueError(_('本次发货货值必须是有限非负数字。'))
    inputs = shipment_input(row)
    return {
        'amount_rmb': _decimal_text(amount),
        'currency': 'RMB',
        'quantity': inputs['quantity'],
        'uom': inputs['uom'],
        'input_fingerprint': digest(inputs),
        'confirmed': True,
        'manual': True,
        'actor': str(actor or ''),
        'confirmed_at': str(confirmed_at or ''),
        'reason': str(reason or ''),
        'method': 'manual_shipment_valuation',
        'status': 'manual',
        'error': '',
    }


def activate_shipment_valuation(metadata, valuation):
    """Activate one trusted valuation while preserving superseded evidence."""

    from .purchase_value_evidence import retire_purchase_value

    retire_purchase_value(metadata)
    value = {**deepcopy(valuation), 'trusted_shipment_source': True}
    metadata['shipment_valuation'] = value
    if metadata.get('settlement_cargo'):
        metadata['settlement_valuation'] = deepcopy(value)
    return value


def build_legacy_shipment_valuation(row):
    """Freeze a positive legacy mirror before a server-managed manual override."""
    amount = number(row.get('goods_value'))
    if amount is None or amount <= 0:
        return None
    inputs = shipment_input(row)
    return {
        'amount_rmb': _decimal_text(amount),
        'currency': 'RMB',
        'quantity': inputs['quantity'],
        'uom': inputs['uom'],
        'input_fingerprint': digest(inputs),
        'input_evidence': {
            'legacy_goods_value': _decimal_text(amount),
            'source_doc_no': str(row.get('source_doc_no') or ''),
        },
        'method': 'LEGACY_PURCHASE',
        'status': 'automatic',
        'error': '',
    }


def legacy_manual_shipment_valuation(metadata):
    """Return the pre-unification manual value stored in shipment_valuation."""
    if not isinstance(metadata, dict):
        return None
    valuation = metadata.get('shipment_valuation')
    if not isinstance(valuation, dict):
        return None
    return valuation if any(valuation.get(flag) for flag in (
        'manual', 'manual_override', 'manual_override_flag',
    )) else None


def restore_legacy_manual_prior(row, valuation):
    """Promote only independently evidenced prior RMB candidates to automatic state."""
    if not isinstance(valuation, dict):
        return None
    evidence = valuation.get('prior_evidence')
    if not isinstance(evidence, dict) or not evidence:
        return None
    if (evidence.get('error')
            or any(evidence.get(flag) for flag in ('manual', 'manual_override', 'manual_override_flag'))
            or str(evidence.get('status') or 'automatic') != 'automatic'
            or not any(key in evidence for key in (
                'amount_rmb', 'input_fingerprint', 'input_evidence', 'source_doc_no', 'goods_value',
            ))):
        return None
    amount = number(valuation.get('prior_amount_rmb'))
    if amount is None:
        amount = number(evidence.get('amount_rmb'))
    if amount is None or amount < 0:
        return None
    inputs = shipment_input(row)
    quantity = _decimal_text(evidence.get('quantity')) or _decimal_text(valuation.get('quantity')) or inputs['quantity']
    uom = normalize_unit(evidence.get('uom') or valuation.get('uom')) or inputs['uom']
    method = str(evidence.get('method') or 'LEGACY_PURCHASE')
    if method.lower() == 'manual':
        method = 'LEGACY_PURCHASE'
    return {
        'amount_rmb': _decimal_text(amount),
        'currency': 'RMB',
        'quantity': quantity,
        'uom': uom,
        'input_fingerprint': evidence.get('input_fingerprint') or digest(
            {'quantity': quantity, 'uom': uom}
        ),
        'input_evidence': deepcopy(evidence),
        'method': method,
        'status': 'automatic',
        'error': '',
    }


def _manual_value(row, manual):
    if not isinstance(manual, dict) or not manual.get('confirmed') or not manual.get('manual'):
        return None
    amount = number(manual.get('amount_rmb'))
    valid = amount is not None and amount >= 0 and currency(manual.get('currency')) == 'RMB'
    stale = manual.get('input_fingerprint') != shipment_input_fingerprint(row)
    if stale:
        return {**manual, 'amount_rmb': None, 'prior_amount_rmb': _decimal_text(manual.get('amount_rmb')),
                'status': 'stale', 'error': 'MANUAL_SHIPMENT_VALUATION_STALE'}
    if not valid:
        return {**manual, 'amount_rmb': None, 'status': 'missing', 'error': 'MANUAL_SHIPMENT_VALUATION_INVALID'}
    return {**manual, 'amount_rmb': _decimal_text(amount), 'status': 'manual', 'error': ''}


def _status(error, value, default='automatic'):
    if 'STALE' in str(error or ''):
        return 'stale'
    if 'CONFLICT' in str(error or ''):
        return 'conflict'
    if error:
        return 'missing'
    if value.get('status') in {'automatic', 'manual', 'conflict', 'missing', 'stale'}:
        return value['status']
    return default


def is_explicit_shipment_zero(valuation):
    """Only structured, valid automatic/manual evidence can declare a zero value."""
    return (isinstance(valuation, dict)
            and number(valuation.get('amount_rmb')) == 0
            and not valuation.get('error')
            and valuation.get('status') in {'automatic', 'manual'})


def _legacy_purchase_total_valuation(row):
    """Value the current shipment from a full-purchase RMB total."""

    total = number(row.get('goods_value'))
    purchase_quantity = number(row.get('quantity'))
    purchase_uom = normalize_unit(
        row.get('purchase_uom') or row.get('unit_price_uom') or row.get('unit')
    )
    shipment = shipment_input(row)
    shipped_quantity = number(shipment.get('quantity'))
    shipped_uom = normalize_unit(shipment.get('uom'))
    base = {
        'amount_rmb': None,
        'currency': 'RMB',
        'quantity': shipment.get('quantity'),
        'uom': shipped_uom,
        'method': 'LEGACY_PURCHASE',
        'status': 'missing',
    }
    if total is None or total <= 0:
        return {**base, 'error': 'GOODS_VALUE_MISSING'}
    if (
        purchase_quantity is None
        or purchase_quantity <= 0
        or not purchase_uom
        or shipped_quantity is None
        or shipped_quantity <= 0
        or not shipped_uom
    ):
        # Old rows may only contain a frozen RMB total.  Preserve that proven
        # amount; proportional valuation is permitted only with both complete
        # quantity/unit contexts.
        return {
            **base,
            'amount_rmb': row.get('goods_value'),
            'method': 'LEGACY_PURCHASE',
            'status': 'automatic',
            'error': '',
        }
    if purchase_uom != shipped_uom:
        return {
            **base,
            'method': 'purchase_total_proration',
            'error': 'PURCHASE_TOTAL_PRORATION_UOM_MISMATCH',
        }

    amount = total * shipped_quantity / purchase_quantity
    amount_text = format(amount.normalize(), 'f')
    method = 'LEGACY_PURCHASE' if shipped_quantity == purchase_quantity else 'purchase_total_proration'
    return {
        **base,
        'amount_rmb': row.get('goods_value') if method == 'LEGACY_PURCHASE' else amount_text,
        'method': method,
        'status': 'automatic',
        'error': '',
        'input_evidence': {
            'total_goods_value_rmb': _decimal_text(total),
            'purchase_quantity': _decimal_text(purchase_quantity),
            'purchase_uom': purchase_uom,
            'actual_shipped_qty': _decimal_text(shipped_quantity),
            'shipped_uom': shipped_uom,
        },
    }


def shipment_value(row):
    metadata = object_json(row.get('extra_json'))
    manual = _manual_value(row, metadata.get('manual_shipment_valuation'))
    if manual is not None:
        return manual
    from .purchase_value_evidence import META_KEY, shipment_valuation as purchase_valuation
    if isinstance(metadata.get(META_KEY), dict) and metadata[META_KEY].get('source_refs'):
        return purchase_valuation(row, metadata[META_KEY])
    if 'settlement_cargo' in metadata:
        cargo = metadata.get('settlement_cargo')
        valuation = metadata.get('settlement_valuation')
        if not isinstance(cargo, dict) or not isinstance(valuation, dict):
            return {'amount_rmb':None, 'method':'SETTLEMENT_FINAL', 'status':'missing',
                    'error':'SETTLEMENT_SHIPMENT_VALUE_REQUIRED'}
        if valuation.get('trusted_shipment_source') is True:
            qty_matches = number(valuation.get('quantity')) == number(row.get('actual_shipped_qty'))
            uom_matches = normalize_unit(valuation.get('uom')) == normalize_unit(
                row.get('shipped_uom') or row.get('unit'))
            error = valuation.get('error') or ('' if qty_matches and uom_matches
                                                else 'SETTLEMENT_SHIPMENT_VALUE_STALE')
            return {**valuation, 'amount_rmb': None if error else valuation.get('amount_rmb'),
                    'status': _status(error, valuation), 'error': error}
        from overseas_costing.services.logistics_settlement.valuation import value_final_cargo
        expected = value_final_cargo(row, cargo, valuation.get('fx_context') or {})
        if expected['input_fingerprint'] != valuation.get('input_fingerprint'):
            error = 'SETTLEMENT_SHIPMENT_VALUE_STALE'
        elif valuation.get('status') == 'conflict':
            error = valuation.get('error') or 'SETTLEMENT_SHIPMENT_VALUE_CONFLICT'
        else:
            error = expected['error']
        status = _status(error, valuation)
        return {**valuation, 'amount_rmb':None if error else valuation.get('amount_rmb'),
                'status':status, 'error':error}
    valuation = metadata.get('shipment_valuation')
    if valuation is None:
        return _legacy_purchase_total_valuation(row)
    if not isinstance(valuation, dict):
        return {'amount_rmb': None, 'method': 'INVALID', 'status':'missing', 'error': 'SHIPMENT_VALUATION_INVALID'}
    legacy_manual = _manual_value(row, {
        **valuation,
        'manual': valuation.get('manual') or valuation.get('manual_override') or valuation.get('manual_override_flag'),
        'input_fingerprint': valuation.get('input_fingerprint') or shipment_input_fingerprint({
            **row, 'actual_shipped_qty': valuation.get('quantity'), 'shipped_uom': valuation.get('uom')}),
    })
    if legacy_manual is not None:
        return legacy_manual
    inputs = shipment_input(row)
    qty = inputs['quantity']
    uom = inputs['uom']
    error = valuation.get('error') or ''
    if not error and (number(qty) != number(valuation.get('quantity')) or normalize_unit(uom) != normalize_unit(valuation.get('uom'))):
        error = 'SHIPMENT_VALUATION_STALE'
    if not error and (number(valuation.get('amount_rmb')) is None or number(valuation.get('amount_rmb')) < 0
                      or currency(valuation.get('currency')) != 'RMB'):
        error = 'SHIPMENT_VALUATION_INVALID'
    return {**valuation, 'amount_rmb': None if error else valuation.get('amount_rmb'),
            'status': _status(error, valuation), 'error': error}


def project_summaries(rows, precision=2):
    groups = {}
    for row in rows:
        project = str(row.get('project_collection') or '').strip() or '未归属项目'
        group = groups.setdefault(project, {'project_collection': project, 'item_keys': [],
            **{f: Decimal('0') for f in ('goods_value_rmb','gross_weight_kg','allocated_fees_rmb','direct_fees_rmb','total_cost_rmb')}})
        group['item_keys'].append(row.get('stable_line_key') or row.get('name'))
        for field in ('goods_value_rmb','gross_weight_kg','allocated_fees_rmb','direct_fees_rmb','total_cost_rmb'):
            group[field] += number(row.get(field)) or Decimal('0')
    return [{k: (format(v, 'f') if k == 'gross_weight_kg' else format(v, f'.{precision}f')) if isinstance(v, Decimal) else v
             for k,v in group.items()} for group in groups.values()]
