"""Final-source ownership and scope selection shared by production fee consumers."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from decimal import Decimal, InvalidOperation


METADATA_FIELDS = ('is_final', 'source_binding_id', 'source_snapshot', 'covered_scopes')
SCOPES = frozenset({'freight', 'customs', 'tax', 'mexico_inland'})
LEGACY_POOL_CURRENCIES = {'china_misc_rmb': 'RMB', 'china_misc_mxn': 'MXN',
    'china_ocean_usd': 'USD', 'china_to_mexico_freight_rmb': 'RMB',
    'mexico_inland_mxn': 'MXN', 'mexico_misc_mxn': 'MXN', 'mexico_inland_misc_rmb': 'RMB'}


def is_final(rule):
    return str(rule.get('is_final') or '').strip().lower() not in {'', '0', 'false', 'no', 'none'}


def row_scopes(rule):
    explicit = str(rule.get('covered_scopes') or '').strip()
    if explicit:
        return {value.strip() for value in explicit.split(',') if value.strip()}
    keys = ' '.join(str(rule.get(field) or '').lower() for field in ('logical_fee_key', 'rule_code'))
    if 'mexico_inland' in keys or 'destination_delivery' in keys:
        return {'mexico_inland'}
    if 'customs' in keys or 'clearance' in keys:
        return {'customs'}
    if 'tax' in keys:
        return {'tax'}
    if any(token in keys for token in (
            'freight', 'ocean', 'international_express_fee', 'express_surcharge',
            'forwarder_surcharge', 'port_and_forwarder_charges')):
        return {'freight'}
    return set()


def covered_scopes(fees):
    return set().union(*(row_scopes(fee) for fee in fees if is_final(fee)))


def _number(value, label):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(_("最终费用{0}缺失或无效").format(label)) from None
    if not parsed.is_finite():
        raise ValueError(_("最终费用{0}缺失或无效").format(label))
    return parsed


def validate_final(fee, fx_context=None):
    if any(str(fee.get(field, 1)).lower() in {'0', 'false', 'none', ''} for field in ('is_enabled', 'is_active')):
        raise ValueError(_('最终费用已停用，需核对来源，不能恢复旧暂估'))
    if not all(str(fee.get(field) or '').strip() for field in ('source_binding_id', 'source_snapshot')):
        raise ValueError(_('最终费用缺少明确来源'))
    scopes = {value.strip() for value in str(fee.get('covered_scopes') or '').split(',') if value.strip()}
    if not scopes or scopes - SCOPES:
        raise ValueError(_('最终费用覆盖范围缺失或无效'))
    if str(fee.get('amount_status') or '').upper() != 'ACTUAL':
        raise ValueError(_('最终费用必须具有明确实际金额状态'))
    _number(fee.get('amount'), '金额')
    currency = str(fee.get('currency') or '').strip().upper()
    if currency not in {'RMB', 'CNY', 'USD', 'MXN'}:
        raise ValueError(_('最终费用币种缺失或不支持'))
    if fx_context is not None:
        required = ['fx_rmb_to_mxn'] + (['fx_usd_to_rmb'] if currency == 'USD' else [])
        for field in required:
            if _number(fx_context.get(field), '汇率') <= 0:
                raise ValueError(_('最终费用汇率必须大于零'))


def select_fees(fees, fx_context=None, *, source_context=None):
    """Select once without reviving estimates, including when final zero is disabled.

    ``None`` performs structural validation for fee lists; a supplied FX mapping
    also requires all rates needed by the final RMB/MXN calculation.
    """
    freight = (source_context or {}).get('freight') or {}
    if freight.get('selected'):
        if not freight.get('available'):
            raise ValueError(_('已采用实际运费来源待核对，不能恢复旧暂估'))
        owned={(c['id'],c['source_snapshot']) for c in freight['claims']}
        selected=[]; seen=set()
        for fee in fees:
            if 'freight' not in row_scopes(fee):
                selected.append(fee); continue
            pair=(fee.get('source_binding_id'),fee.get('source_snapshot'))
            if is_final(fee) and pair in owned:
                validate_final(fee,fx_context)
                if pair in seen: raise ValueError(_('本票运费明细重复计费'))
                seen.add(pair);selected.append(fee)
            elif is_final(fee):
                raise ValueError(_('当前运费与采用来源不一致'))
            elif row_scopes(fee)-{'freight'}:
                raise ValueError(_('旧费用覆盖范围交叉，请核对'))
        if seen!=owned: raise ValueError(_('本票已采用费用缺失，不能恢复旧暂估'))
        return selected
    if source_context and source_context.get('root_kind') == 'expense' and not source_context.get('separate_adoption'):
        if not source_context.get('available') or not source_context.get('approved') or source_context.get('invalid'):
            return []
        finals = [fee for fee in fees if is_final(fee)
                  and fee.get('source_binding_id') == source_context.get('binding_id')
                  and fee.get('source_snapshot') == source_context.get('source_snapshot')]
        for fee in finals:
            validate_final(fee,fx_context)
        return finals
    finals = [fee for fee in fees if is_final(fee)]
    if not finals:
        return list(fees)
    for fee in finals:
        validate_final(fee, fx_context)
    logical_keys = [str(fee.get('logical_fee_key') or '').strip() for fee in finals]
    explicit_keys = [key for key in logical_keys if key]
    if len(set(explicit_keys)) != len(explicit_keys):
        raise ValueError(_('最终来源费用分类重复，请先更正认领或费用凭证'))
    scope_owners = {}
    component_credit = lambda row: 'credit' in ' '.join(
        str(row.get(field) or '').lower() for field in ('logical_fee_key', 'rule_code'))
    for fee in finals:
        scopes = row_scopes(fee)
        owner = (fee.get('source_binding_id'), fee.get('source_snapshot'))
        for scope in scopes:
            previous = scope_owners.get(scope)
            same_owned_credit_component = (previous and previous[0] == owner
                                           and (component_credit(previous[1]) or component_credit(fee)))
            if previous and not same_owned_credit_component:
                raise ValueError(_('最终费用覆盖范围重叠，请先更正来源费用'))
        for scope in scopes:
            scope_owners.setdefault(scope, (owner, fee))
    covered = covered_scopes(finals)
    selected = []
    for fee in fees:
        scopes = row_scopes(fee)
        if not is_final(fee) and scopes & covered:
            active = all(str(fee.get(flag, 1)).lower() not in {'0', 'false'} for flag in ('is_active', 'is_enabled'))
            if active and scopes - covered:
                raise ValueError(_('旧费用覆盖范围交叉，不能部分停用整笔费用'))
            continue
        selected.append(fee)
    return selected


def assert_fee_edit_allowed(existing_fees, payload):
    """Source fees and their retired scopes can only change through settlement review."""
    keys = {str(payload.get(field) or '') for field in ('name', 'rule_code', 'logical_fee_key')} - {''}
    owned = [fee for fee in existing_fees if is_final(fee) or fee.get('source_binding_id')]
    if is_final(payload) or any(payload.get(field) for field in METADATA_FIELDS if field != 'is_final') or any(
        keys & {str(fee.get(field) or '') for field in ('name', 'rule_code', 'logical_fee_key')} for fee in owned
    ):
        raise ValueError(_('最终采购支出费用为来源事实，请通过结算关联更正'))
    if row_scopes(payload) & set().union(*(row_scopes(fee) for fee in owned)):
        raise ValueError(_('该费用范围已由最终采购支出覆盖，不能恢复旧费用或增加重复费用'))


def supplement_legacy_fees(items, fees):
    """Preserve identifiable old item pools only for a settlement-owned trial."""
    from overseas_costing.services.effective_source_values import source_context_from_items
    context=source_context_from_items(items)
    if context.get('root_kind') == 'expense' and not context.get('separate_adoption'):
        return list(fees)
    if not any(is_final(fee) for fee in fees):
        return list(fees)
    explicit = [fee for fee in fees if not fee.get('virtual')]
    codes = {str(fee.get(field) or '') for fee in explicit for field in ('logical_fee_key', 'rule_code')}
    scopes = set().union(*(row_scopes(fee) for fee in explicit))
    added = []
    for field, currency in LEGACY_POOL_CURRENCIES.items():
        if field in codes:
            continue
        values = {_number(item.get(field), '旧费用金额') for item in items if item.get(field) not in (None, '', 0, '0')}
        values.discard(Decimal(0))
        if not values:
            continue
        if field == 'mexico_inland_misc_rmb':
            raise ValueError(_('旧内陆／杂费覆盖范围不明确，请先核对'))
        if row_scopes({'rule_code': field}) & scopes:
            continue
        if len(values) != 1:
            raise ValueError(_('旧费用字段存在不同金额，请先核对费用池'))
        added.append({'name': 'legacy-item:' + field, 'rule_code': field, 'logical_fee_key': field,
            'amount': str(next(iter(values))), 'currency': currency, 'amount_status': 'ESTIMATED',
            'allocation_basis': 'gross_weight', 'scope_type': 'ALL_ITEMS', 'is_active': 1, 'is_enabled': 1})
    added_scopes = set().union(*(row_scopes(fee) for fee in added))
    return [fee for fee in fees if not (fee.get('virtual') and row_scopes(fee) & added_scopes)] + added
