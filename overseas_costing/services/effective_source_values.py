"""Project adopted values without destroying original purchase/packing evidence."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from copy import deepcopy
from .shipment_cost_service import object_json

PHYSICAL_FIELDS = ('actual_shipped_qty', 'actual_shipped_qty_mode', 'actual_shipped_qty_source_revision',
                   'shipped_uom', 'gross_weight_kg', 'net_weight_kg', 'volume_m3', 'volume_weight_kg',
                   'chargeable_weight_kg', 'weight_ratio', 'package_count', 'packaging_type')


def item_source_context(item):
    return object_json(item.get('extra_json')).get('effective_logistics_source') or {}


def expense_context(context):
    return context.get('root_kind') == 'expense'


def batch_source_context(batch, version=None, *, lock=False):
    from .effective_logistics_source import current_source_bundle
    bundle = current_source_bundle(batch,version,lock=lock)
    return bundle['context'] if bundle else {}


def project_batch_items(items, batch, version=None):
    context = batch_source_context(batch,version)
    return [project_source_values(item,context) for item in items], context


def source_context_from_items(items):
    contexts = [item_source_context(item) for item in items if expense_context(item_source_context(item)) or item_source_context(item).get('separate_adoption')]
    if len({context.get('fingerprint') for context in contexts}) > 1:
        raise ValueError(_('物料采用来源不一致，请重新应用当前采购支出'))
    return contexts[0] if contexts else {}


def physical_overlay_update(item, context, values, *, evidence, adopt_current_context=False):
    """Called only after a trusted source/lease check; returns metadata to persist."""
    from .effective_logistics_source import require_available
    require_available(context)
    meta = deepcopy(object_json(item.get('extra_json')))
    adopted_context = meta.get('effective_logistics_source') or {}
    if adopted_context.get('fingerprint') != context.get('fingerprint') and adopt_current_context:
        # A user may fill a missing physical value before the source has written any
        # row-level overlay. The locked, server-resolved context becomes the audit
        # parent for that manual value; callers cannot supply this context directly.
        meta['effective_logistics_source'] = deepcopy(context)
        adopted_context = meta['effective_logistics_source']
    if adopted_context.get('fingerprint') != context.get('fingerprint'):
        raise ValueError(_('当前采购支出尚未应用或资料已变化，请重新读取资料'))
    previous = meta.get('settlement_physical') or {}
    current = previous if previous.get('source_context_fingerprint') == context.get('fingerprint') else {}
    adopted = dict(current.get('values') or {})
    proof = deepcopy(current.get('evidence') or {})
    for field, value in values.items():
        if field in PHYSICAL_FIELDS:
            adopted[field] = value
            proof[field] = evidence
    if 'settlement_packing_missing' in meta:
        missing=set(meta['settlement_packing_missing'])
        for field,value in values.items():
            if field in PHYSICAL_FIELDS:
                if value is None: missing.add(field)
                else: missing.discard(field)
        meta['settlement_packing_missing']=sorted(missing)
    meta['settlement_physical'] = {'source_snapshot':context['source_snapshot'],
        'source_context_fingerprint':context['fingerprint'],'values':adopted,'evidence':proof}
    return meta


def project_source_values(item, context=None):
    row = dict(item)
    meta = deepcopy(object_json(row.get('extra_json')))
    row.update({k:v for k,v in (meta.get('ai_row_packing_values') or {}).items() if k in ('package_count','packaging_type')})
    adopted_context = meta.get('effective_logistics_source') or {}
    context = context if context is not None else adopted_context
    if context.get('separate_adoption'):
        row['source_context']=context
        meta['effective_logistics_source']=context
        row['extra_json']=meta
        if (context.get('packing') or {}).get('selected_source'):
            physical=meta.get('settlement_physical') or {}
            if physical.get('source_snapshot') == context.get('source_snapshot'):
                row.update({k:v for k,v in (physical.get('values') or {}).items() if k in PHYSICAL_FIELDS})
        for field in meta.get('settlement_packing_missing') or []:
            if field in (*PHYSICAL_FIELDS,'quantity'): row[field]=None
        # These are independently adopted rows; changing freight must not blank packing or customs.
        return row
    if not expense_context(context):
        return row
    valid = (context.get('available') and context.get('approved') and not context.get('invalid')
             and context.get('fingerprint') == adopted_context.get('fingerprint'))
    physical = meta.get('settlement_physical') or {}
    current_physical = valid and physical.get('source_context_fingerprint') == context.get('fingerprint')
    values = physical.get('values') or {} if current_physical else {}
    for field in PHYSICAL_FIELDS:
        row[field] = values.get(field)
    if not valid or not meta.get('settlement_cargo'):
        meta['settlement_cargo'] = {}
        meta.pop('settlement_valuation', None)
    row['source_adoption_state'] = 'current' if valid and meta.get('settlement_cargo') else 'historical_pending'
    # Legacy fields are raw source facts; they cannot become supplemental pools.
    from .logistics_settlement.fee_policy import LEGACY_POOL_CURRENCIES
    from .cost_preview_service import LEGACY_CUSTOMS_SERVICE_FIELDS, LEGACY_TAX_COMPONENT_FIELDS
    for field in (*LEGACY_POOL_CURRENCIES, *LEGACY_CUSTOMS_SERVICE_FIELDS, *LEGACY_TAX_COMPONENT_FIELDS,
                  'customs_rmb', 'customs_mxn', 'tax_rmb', 'tax_mxn', 'mexico_customs_rmb',
                  'mexico_customs_mxn', 'mexico_customs_usd', 'import_tax_total'):
        if field in row:
            row[field] = None
    if row['source_adoption_state'] != 'current':
        from .logistics_settlement.writer import DERIVED_FIELDS
        for field in DERIVED_FIELDS:
            if field in row:
                row[field] = None
        row['derived_json'] = '{}'
    row['source_context'] = context
    meta['effective_logistics_source'] = context
    row['extra_json'] = meta
    return row
