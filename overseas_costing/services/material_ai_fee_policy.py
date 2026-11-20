"""Server-owned eligibility for AI fee suggestions, independent of packing source."""
from collections import Counter
from copy import deepcopy

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from .logistics_settlement.fee_policy import is_final, row_scopes
from .transport_fee_service import fee_is_active


def recommended_missing_fee_ids(catalog, current_fees, source_context=None, *, transport_mode=''):
    """Select unique native defaults for absent rows or untouched system shells.

    The catalog already owns evidence validation and source arbitration. This
    narrower policy never turns a reference quote into a default or changes an
    existing amount decision. The same selector runs under confirmation locks.
    """
    from . import fee_service

    existing = fee_service._decorate_historical_rules(
        [fee for fee in current_fees if not fee.get('virtual')], transport_mode)
    defaults = [proposal for proposal in catalog.get('fees') or []
                if proposal.get('proposal_type') == 'fee_update'
                and proposal.get('can_apply') is True and proposal.get('default_selected') is True
                and proposal.get('source_refs')]
    candidates = []
    for proposal in decorate(defaults, existing, source_context):
        payload = proposal.get('payload') or {}
        identifier = proposal.get('proposal_id')
        representative = proposal.get('presentation_representative_candidate_id')
        if not proposal['can_apply'] or not identifier or representative and representative != identifier:
            continue
        try:
            normalized = fee_service.normalize_fee_payload(payload, trusted_project_policy=True)
        except ValueError:
            continue
        if normalized['amount_status'] not in {'ESTIMATED', 'ACTUAL'} or not _amount_is_missing(normalized, existing):
            continue
        candidates.append(proposal)

    identifiers = Counter(proposal['proposal_id'] for proposal in candidates)
    keys = Counter(proposal['payload']['logical_fee_key'] for proposal in candidates)
    groups = Counter(proposal['conflict_group'] for proposal in candidates if proposal.get('conflict_group'))
    scopes = Counter(scope for proposal in candidates for scope in row_scopes(proposal['payload']))
    return sorted(proposal['proposal_id'] for proposal in candidates
                  if identifiers[proposal['proposal_id']] == 1
                  and keys[proposal['payload']['logical_fee_key']] == 1
                  and (not proposal.get('conflict_group') or groups[proposal['conflict_group']] == 1)
                  and all(scopes[scope] == 1 for scope in row_scopes(proposal['payload'])))


def assert_missing_fee_selection(catalog, selected_ids, current_fees, source_context=None, *, transport_mode=''):
    """Reject automatic receipts that can now overwrite a fee decision."""
    allowed = recommended_missing_fee_ids(catalog, current_fees, source_context, transport_mode=transport_mode)
    if set(selected_ids) - set(allowed):
        raise ValueError(_('来源、费用或物料已变化，请刷新预览；本次未保存。'))


def _amount_is_missing(payload, existing):
    key = str(payload.get('logical_fee_key') or '')
    if not key:
        return False
    scopes = row_scopes(payload)
    related = [fee for fee in existing
               if fee.get('logical_fee_key') == key or scopes & row_scopes(fee)]
    if not related:
        return True
    if len(related) != 1 or related[0].get('logical_fee_key') != key:
        return False
    fee = related[0]
    if (not fee_is_active(fee) or is_final(fee) or fee.get('source_binding_id')
            or str(fee.get('amount_status') or '').upper() != 'MISSING'
            or fee.get('status_changed_by') or fee.get('status_change_reason')):
        return False
    from . import fee_service

    if (str(fee.get('scope_type') or 'ALL_ITEMS').upper() != payload['scope_type']
            or fee_service._cost_value(fee, 'scope_value_json') != fee_service._cost_value(payload, 'scope_value_json')
            or (fee.get('allocation_basis') or fee.get('basis_field')) not in (None, '', payload['allocation_basis'])):
        return False
    revisions = [str(fee.get(field) or '') for field in ('amount_revision', 'scope_revision')]
    if (not revisions[0].startswith(('system:', 'evidence-shell:'))
            or any(value and not value.startswith(('system:', 'evidence-shell:')) for value in revisions)):
        return False
    # Frappe Currency persists a blank shell as zero. Only its explicit MISSING
    # state and server provenance allow that zero to be supplemented.
    from .purchase_value_evidence import number

    return fee.get('amount') in (None, '') or number(fee.get('amount')) == 0


def _semantic_actual_reason(proposal, fees):
    if not proposal.get('_semantic_payment_fee'):
        return ''
    payload = proposal.get('payload') or {}
    scopes = row_scopes(payload)
    key = str(payload.get('logical_fee_key') or '')
    for fee in fees or []:
        if (
            str(fee.get('amount_status') or '').strip().upper() == 'ACTUAL'
            and (is_final(fee) or fee_is_active(fee))
            and (scopes & row_scopes(fee) or key == fee.get('logical_fee_key'))
        ):
            return '已有实际运费或最终费用，本报价不参与填充；请通过费用更正调整。'
    return ''


def blocked_reason(payload, fees, context):
    scopes = row_scopes(payload)
    freight = (context or {}).get('freight') or {}
    if 'freight' in scopes and freight.get('selected'):
        return '已有实际运费，本报价不参与填充；金额调整请使用费用更正。' if freight.get('available') else '实际运费待核对，本报价不参与填充，不能恢复旧暂估。'
    key = str(payload.get('logical_fee_key') or '')
    for fee in fees or []:
        if is_final(fee) and (scopes & row_scopes(fee) or key == fee.get('logical_fee_key')):
            return '已有实际运费或最终费用，本报价不参与填充；请通过费用更正调整。'
        if key and key == fee.get('logical_fee_key') and not fee_is_active(fee):
            return '该费用已停用，仅供参考，不能通过 AI 填充恢复启用。'
    return ''


def decorate(proposals, fees, context):
    output = deepcopy(proposals)
    for proposal in output:
        if proposal.get('proposal_type') != 'fee_update':
            continue
        role = str(proposal.get('selection_role') or '')
        role_reason = (
            '该费用是已裁决运费总额的分项，仅供只读核对，不能单独应用。'
            if role == 'component'
            else '该费用是已裁决运费总额之外的备选报价，仅供只读参考。'
            if role == 'alternative'
            else blocked_reason(proposal.get('payload') or {}, fees, context)
        )
        reason = (
            str(proposal.get('source_policy_blocked') or '')
            or _semantic_actual_reason(proposal, fees)
            or role_reason
        )
        proposal.update(can_apply=not bool(reason), blocked_reason=reason)
        if reason:
            proposal['default_selected'] = False
            if proposal.get('_semantic_payment_fee'):
                proposal['recommended'] = False
    return output


def assert_allowed(proposals, fees, context):
    for proposal in proposals:
        if proposal.get('proposal_type') == 'fee_update':
            role = str(proposal.get('selection_role') or '')
            role_reason = (
                '该费用仅供只读核对，不能单独应用。'
                if role in {'component', 'alternative'}
                else blocked_reason(proposal.get('payload') or {}, fees, context)
            )
            reason = (
                str(proposal.get('source_policy_blocked') or '')
                or _semantic_actual_reason(proposal, fees)
                or role_reason
            )
            if reason:
                raise ValueError(reason)


def prompt_policy(fees, context, keys):
    return {key: {'can_apply': not bool(reason), 'reason': reason}
            for key in sorted(keys)
            for reason in [blocked_reason({'logical_fee_key': key}, fees, context)]}
