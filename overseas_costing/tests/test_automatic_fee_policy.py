"""Automatic fee filling reuses native catalogs, receipts and amount semantics."""

from copy import deepcopy

import pytest

from overseas_costing.services import fee_service, material_ai_fee_policy as policy
from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services import material_ai_selection_service as selections
from overseas_costing.tests.test_ai_selection_service import Repo, confirm


def proposal(identifier='F1', key='international_sea_freight', amount='4288.37', **values):
    return {'proposal_id': identifier, 'proposal_type': 'fee_update', 'default_selected': True,
            'source_refs': [{'source_id': 'DOC', 'field': '运费', 'value': amount}],
            'payload': {'logical_fee_key': key, 'amount': amount, 'amount_status': 'ESTIMATED',
                        'currency': 'RMB', 'allocation_basis': 'gross_weight', 'scope_type': 'ALL_ITEMS'},
            **values}


def shell(**values):
    return {'name': 'SHELL', 'logical_fee_key': 'international_sea_freight',
            'amount_status': 'MISSING', 'amount': 0, 'is_enabled': 1, 'is_active': 1,
            'amount_revision': 'evidence-shell:created-by-native-evidence-link',
            'scope_revision': 'system:created-by-native-evidence-link', **values}


def recommended(proposals, fees=None, context=None, *, public=False):
    fees = fees or []
    catalog = rows.catalog([], proposals, fees, context or {}, run_id='R', sources=[])
    if public:
        catalog = selections.public_catalog(catalog)
    return policy.recommended_missing_fee_ids(catalog, fees, context, transport_mode='SEA')


@pytest.mark.parametrize('public', [False, True])
@pytest.mark.parametrize('fees', [[], [shell()], [shell(amount=None)], [shell(amount='')],
                                 [shell(amount='0.000000', amount_revision='system:default-shell')]])
def test_absent_fee_and_proven_system_missing_shells_accept_only_native_default(fees, public):
    before = deepcopy(fees)
    assert recommended([proposal()], fees, public=public) == ['F1']
    assert fees == before


@pytest.mark.parametrize('values', [
    {'amount': '12.30'}, {'amount': '-1'}, {'amount': 'not-an-amount'},
    {'amount_status': 'ESTIMATED'}, {'amount_status': 'ACTUAL'}, {'amount_status': 'CONFIRMED'},
    {'amount_status': 'NOT_INCURRED'}, {'amount_status': 'INCLUDED'},
    {'amount_revision': 'manual:explicit-zero'}, {'scope_revision': 'manual:chosen-items'},
    {'amount_revision': ''}, {'amount_revision': 'oa:legacy'}, {'scope_revision': 'unknown-decision'},
    {'is_enabled': 0}, {'is_active': 0}, {'is_final': 1}, {'source_binding_id': 'ACTUAL-SOURCE'},
    {'status_changed_by': 'employee'}, {'status_change_reason': 'deliberately cleared'},
])
def test_existing_money_zero_decisions_manual_state_and_owned_fees_are_preserved(values):
    assert recommended([proposal()], [shell(**values)]) == []


def test_virtual_zero_default_is_not_a_persisted_zero_decision():
    template = next(row for row in fee_service.build_default_fee_templates('SEA')
                    if row['logical_fee_key'] == 'destination_delivery')
    assert template['amount_status'] == 'ESTIMATED' and template['amount'] == '0'
    assert recommended([proposal(key='destination_delivery')], [template]) == ['F1']


def test_native_historical_mapping_keeps_old_oa_or_named_fee_amounts_protected():
    for fee in [{'name': 'OLD', 'rule_code': 'oa_logistics_freight', 'amount': 0, 'amount_status': 'ESTIMATED'},
                {'name': 'OLD', 'expense_category': '国际海运费', 'amount': 20}]:
        assert recommended([proposal()], [fee]) == []


@pytest.mark.parametrize('change', [
    {'default_selected': False}, {'selection_role': 'alternative'}, {'selection_role': 'component'},
    {'source_refs': []}, {'source_policy_blocked': 'Wrong shipment'},
])
def test_reference_quotes_and_unverified_evidence_never_become_automatic_defaults(change):
    assert recommended([proposal(**change)]) == []


def test_multiple_default_quotes_or_overlapping_fee_scopes_are_not_guessed():
    assert recommended([proposal(), proposal('F2', amount='4000')]) == []
    assert recommended([proposal(), proposal('F2', key='sea_port_forwarder_surcharge')]) == []
    candidates = [proposal(), proposal('F2', key='customs_clearance_fee', conflict_group='one-bill'),
                  proposal('F3', key='import_tax', conflict_group='one-bill')]
    assert recommended(candidates) == ['F1']


def test_native_equivalent_evidence_group_uses_its_existing_representative():
    proposals = [proposal('F2'), proposal('F1')]
    assert recommended(proposals) == ['F1']
    proposals[1]['default_selected'] = False
    assert recommended(proposals) == ['F2']
    assert recommended([proposal(default_selected=False)]) == []


def test_other_existing_fee_in_the_same_scope_blocks_automatic_addition():
    related = shell(logical_fee_key='sea_port_forwarder_surcharge')
    assert recommended([proposal()], [related]) == []
    assert recommended([proposal()], context={'freight': {'selected': True, 'available': False}}) == []
    unrelated = shell(logical_fee_key='customs_clearance_fee', amount='10', amount_status='ACTUAL')
    assert recommended([proposal()], [unrelated]) == ['F1']


@pytest.mark.parametrize('values', [
    {'scope_type': 'ITEMS', 'scope_value_json': '["I1"]'},
    {'scope_type': 'DIRECT_ITEM', 'scope_value_json': '["I1"]'},
    {'allocation_basis': 'goods_value'},
])
def test_missing_amount_does_not_authorize_changing_the_existing_scope_or_basis(values):
    assert recommended([proposal()], [shell(**values)]) == []


def test_unchanged_direct_item_scope_can_receive_its_explicit_missing_amount():
    candidate = proposal()
    candidate['payload'].update(scope_type='DIRECT_ITEM', scope_value_json='["I1"]')
    assert recommended([candidate], [shell(scope_type='DIRECT_ITEM', scope_value_json='["I1"]')]) == ['F1']
    assert recommended([candidate], [shell(scope_type='DIRECT_ITEM', scope_value_json='["I2"]')]) == []


def fee_run(*, automatic=True, fees=None):
    repo = Repo()
    repo.run['trigger_mode'] = 'LOCAL_UPLOAD' if automatic else 'MANUAL'
    repo.run['candidates_json'] = [proposal()]
    repo.fees = deepcopy(fees or [])
    repo.get_locked_fees = lambda *args: deepcopy(repo.fees)
    return repo


def prepare_fee(repo, ids=None):
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    selected = ids if ids is not None else policy.recommended_missing_fee_ids(
        catalog, repo.fees, repo.context['effective_source'], transport_mode='SEA')
    return selections.prepare('B1', repo.run['name'], [], selected, 'fill_missing', 'V1',
                              field_choices={}, packing_assignments={}, repository=repo)['preview']


@pytest.mark.parametrize('fees', [[], [shell()]])
def test_native_fee_only_receipt_confirms_without_material_changes_and_is_idempotent(fees):
    repo = fee_run(fees=fees)
    preview = prepare_fee(repo)
    assert preview['can_apply'] and not preview['changes']
    assert preview['selected_row_ids'] == [] and preview['selected_fee_ids'] == ['F1']
    assert confirm(repo, preview)['ok']
    assert len(repo.writes) == 1 and repo.writes[0]['fees'][0]['payload']['amount'] == '4288.37'
    assert confirm(repo, preview)['idempotent'] and len(repo.writes) == 1


@pytest.mark.parametrize('automatic', [False, True])
def test_only_automatic_confirmation_restricts_an_explicit_selected_fee_to_missing_amounts(automatic):
    repo = fee_run(automatic=automatic, fees=[shell(amount='95', amount_status='ESTIMATED')])
    preview = prepare_fee(repo, ['F1'])
    if automatic:
        with pytest.raises(ValueError, match='费用或物料已变化'):
            confirm(repo, preview)
        assert not repo.writes
    else:
        assert confirm(repo, preview)['ok'] and len(repo.writes) == 1


def test_locking_read_rejects_new_money_hidden_by_the_older_transaction_snapshot():
    repo = fee_run(fees=[shell()])
    preview = prepare_fee(repo)
    before = deepcopy(repo.fees)
    latest = [shell(amount=600, amount_status='ESTIMATED', amount_revision='manual:new-amount')]
    reads = []

    def get_locked_fees(*_args):
        reads.append('current')
        return deepcopy(latest)

    repo.get_fees = lambda *_args: deepcopy(before)
    repo.get_locked_fees = get_locked_fees
    with pytest.raises(ValueError, match='费用或物料已变化'):
        confirm(repo, preview)
    assert reads == ['current'] and not repo.writes


def test_new_missing_shell_hidden_by_snapshot_cannot_create_a_duplicate_fee():
    repo = fee_run()
    preview = prepare_fee(repo)
    repo.get_locked_fees = lambda *_args: [shell()]
    with pytest.raises(ValueError, match='费用或物料已变化'):
        confirm(repo, preview)
    assert not repo.writes


def test_equal_current_fee_rows_in_a_different_query_order_keep_the_native_receipt_valid():
    unrelated = shell(name='OTHER', logical_fee_key='import_tax', amount='10', amount_status='ACTUAL')
    repo = fee_run(fees=[shell(), unrelated])
    preview = prepare_fee(repo)
    repo.get_locked_fees = lambda *_args: deepcopy(list(reversed(repo.fees)))
    assert confirm(repo, preview)['ok'] and len(repo.writes) == 1


@pytest.mark.parametrize('change', [
    {'amount_revision': 'manual:now-cleared'}, {'is_active': 0}, {'amount_status': 'ACTUAL'},
    {'logical_fee_key': 'sea_port_forwarder_surcharge'},
])
def test_locked_current_manual_zero_retirement_and_scope_changes_block_old_fee_receipts(change):
    repo = fee_run(fees=[shell()])
    preview = prepare_fee(repo)
    snapshot = deepcopy(repo.fees)
    repo.get_fees = lambda *_args: deepcopy(snapshot)
    repo.get_locked_fees = lambda *_args: [shell(**change)]
    with pytest.raises(ValueError, match='费用或物料已变化'):
        confirm(repo, preview)
    assert not repo.writes
