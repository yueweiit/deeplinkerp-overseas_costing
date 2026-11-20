"""An unadopted shared carton total is not an automatic per-SKU measurement."""

from copy import deepcopy

import pytest

from overseas_costing.services import material_ai_fill_service as ai
from overseas_costing.services import material_ai_selection_service as selections
from overseas_costing.services import workbench_bulk_ai_service as controller
from overseas_costing.tests.test_workbench_bulk_ai_service import runtime


@pytest.fixture
def shared_runtime(runtime):
    repo, calls = runtime
    repo.sources[0]['process_instance_id'] = 'OA'
    repo.sources.append({'source_id': 'PACKING', 'source_kind': 'approval_attachment',
                         'file_name': 'packing.xlsx', 'source_hash': 'PACKING-HASH',
                         'approval_role': 'international_logistics', 'process_instance_id': 'OA'})
    repo.run['source_manifest_json'] = list(repo.sources)
    repo.items = [{**repo.items[0], 'name': 'I' + str(index),
                   'material_code': 'SKU' + str(index), 'stable_line_key': 'LINE-' + str(index),
                   'row_no': index, 'actual_shipped_qty': None, 'quantity': 1}
                  for index in (1, 2)]
    repo.run['candidates_json'] = [{
        'proposal_id': 'P' + row['name'], 'proposal_type': 'item_update',
        'target_item_name': row['name'], 'default_selected': True,
        'source_refs': [{'source_id': 'DOC'}], 'payload': {'fields': {
            'gross_weight_kg': 15.05, 'net_weight_kg': 14.55,
            'volume_m3': 91960, 'package_count': 1, 'actual_shipped_qty': 50}},
    } for row in repo.items]
    repo.run['draft_json']['packing_group_candidates'] = [{
        'candidate_id': 'SHARED', 'member_keys': ['LINE-1', 'LINE-2'],
        'member_labels': ['SKU1', 'SKU2'], 'source_id': 'PACKING', 'sheet_name': 'Sheet1',
        'creation_method': 'xlsx_merge', 'gross_weight_kg': '15.05',
        'net_weight_kg': '14.55', 'volume_m3': '91960', 'package_count': '1',
        'default_selected': True, 'can_apply': True,
        'evidence': [{'kind': 'xlsx_merge', 'field': field, 'rows': [3, 4]}
                     for field in ('gross_weight_kg', 'net_weight_kg', 'volume_m3', 'package_count')],
        'assignment_options': [{'assignment_id': 'ONE-BOX', 'mode': 'one_box_group',
                                'member_keys': ['LINE-1', 'LINE-2'],
                                'default_selected': True, 'can_apply': True}],
    }]
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    repo.get_packing_groups = lambda batch, version: []
    return repo, calls


def test_automatic_native_receipt_keeps_shipping_but_never_duplicates_shared_totals(shared_runtime):
    repo, calls = shared_runtime
    staged = controller.preview('B1', repo.run['name'])
    assert staged['can_apply'] and staged['field_names'] == ['actual_shipped_qty']
    receipt = repo.run['draft_json']['row_previews'][staged['preview_id']]
    assert not receipt['selected_packing_assignments']
    assert not receipt.get('packing_group_candidates')
    result = controller.apply('B1', repo.run['name'], staged['preview_id'], staged['preview_revision'])
    assert result['status'] == 'APPLIED' and len(repo.writes) == 1
    written = repo.writes[0]
    assert {change['fieldname'] for change in written['changes']} == {'actual_shipped_qty'}
    assert all(row.get('gross_weight_kg') is None for row in written['rows'])
    assert all(row['actual_shipped_qty'] == 50 for row in written['rows'])


@pytest.mark.parametrize('status', [None, 'removed', 'pending_confirmation', 'confirmed'])
def test_confirmed_group_owns_totals_without_recommending_them_again_per_sku(shared_runtime, status):
    repo, _ = shared_runtime
    repo.get_packing_groups = lambda batch, version: [{'group_id': 'SHARED', 'status': status,
        'member_keys': ['LINE-1', 'LINE-2'], 'gross_weight_kg': '15.05',
        'net_weight_kg': '14.55', 'volume_m3': '91960', 'package_count': '1'}]
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    shared = [candidate for candidate in catalog['field_candidates']
              if candidate['fieldname'] != 'actual_shipped_qty']
    assert len(shared) == 8 and all(candidate['can_apply'] for candidate in shared)
    assert not any(candidate['default_selected'] for candidate in shared)
    assert all(candidate['packing_group_confirmed'] is (status == 'confirmed') for candidate in shared)


def test_member_alias_bridge_uses_native_unique_labels_without_guessing_repeated_values(shared_runtime):
    repo, _ = shared_runtime
    group = repo.run['draft_json']['packing_group_candidates'][0]
    group['member_keys'] = ['OLD-1', 'OLD-2']
    repo.run['candidates_json'][1]['payload']['fields']['gross_weight_kg'] = 4
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    weights = {candidate['item_name']: candidate for candidate in catalog['field_candidates']
               if candidate['fieldname'] == 'gross_weight_kg'}
    assert not weights['I1']['default_selected']
    assert weights['I1']['packing_group_candidate_ids'] == ['SHARED']
    assert weights['I2']['default_selected'] and weights['I2']['suggested_value'] == 4


def test_unconfirmed_merge_members_still_cannot_default_to_per_sku_totals(shared_runtime):
    repo, _ = shared_runtime
    repo.run['draft_json']['packing_group_candidates'][0].update(can_apply=False, default_selected=False)
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    assert not any(candidate['default_selected'] for candidate in catalog['field_candidates']
                   if candidate['fieldname'] == 'gross_weight_kg')


def test_pre_fix_receipt_is_rejected_before_native_write(shared_runtime, monkeypatch):
    repo, _ = shared_runtime
    with monkeypatch.context() as old_code:
        old_code.setattr(selections, '_guard_shared_packing_defaults', lambda *args: None)
        staged = controller.preview('B1', repo.run['name'])
    with pytest.raises(ValueError, match='刷新预览'):
        selections.confirm('B1', repo.run['name'], staged['preview_id'], staged['preview_revision'],
                           'TOKEN', 'M1', repository=repo)
    assert not repo.writes


def test_manual_explicit_field_selection_remains_a_reviewable_native_choice(shared_runtime):
    repo, _ = shared_runtime
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    candidate = next(candidate for candidate in catalog['field_candidates']
                     if candidate['item_name'] == 'I1' and candidate['fieldname'] == 'gross_weight_kg')
    preview = selections.prepare('B1', repo.run['name'], [], [], 'fill_missing', 'V1',
        field_choices={'I1:gross_weight_kg': candidate['candidate_id']},
        packing_assignments={}, repository=repo)['preview']
    assert preview['can_apply']
    assert [change['fieldname'] for change in preview['changes']] == ['gross_weight_kg']


def test_equal_numeric_values_without_a_shared_source_group_keep_existing_recommendations(shared_runtime):
    repo, _ = shared_runtime
    repo.run['draft_json']['packing_group_candidates'] = []
    staged = controller.preview('B1', repo.run['name'])
    assert set(staged['field_names']) == {
        'gross_weight_kg', 'net_weight_kg', 'volume_m3', 'package_count', 'actual_shipped_qty'}


@pytest.mark.parametrize('evidence_kind,instance', [
    ('approval_attachment', 'OA'), ('approval_attachment', 'OTHER-OA'), ('approval_form', 'OTHER-OA'),
])
def test_independent_equal_weight_still_wins_the_native_arbitration(shared_runtime, evidence_kind, instance):
    repo, _ = shared_runtime
    source = {'source_id': 'INDEPENDENT', 'source_kind': evidence_kind,
              'file_name': 'individual.xlsx', 'source_hash': 'INDEPENDENT-HASH',
              'approval_role': 'international_logistics', 'process_instance_id': instance}
    repo.sources.append(source)
    proposal = deepcopy(repo.run['candidates_json'][0])
    proposal.update(proposal_id='INDEPENDENT', source_refs=[{'source_id': 'INDEPENDENT'}])
    proposal['payload']['fields'] = {'gross_weight_kg': 15.05}
    repo.run['candidates_json'].append(proposal)
    repo.run['source_manifest_json'] = list(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    weights = [candidate for candidate in catalog['field_candidates']
               if candidate['fieldname'] == 'gross_weight_kg' and candidate['item_name'] == 'I1']
    selected = [candidate for candidate in weights if candidate['default_selected']]
    assert len(selected) == 1
    assert any(candidate.get('packing_group_candidate_ids') for candidate in weights)
    assert not selected[0].get('packing_group_candidate_ids')


@pytest.mark.parametrize('sheet_name,shared,parent_ref', [
    ('Sheet1', True, False), ('Sheet2', False, False),
    ('Sheet1', True, True), ('Sheet2', False, True), ('', True, True),
])
def test_exact_sheet_source_is_bound_but_another_sheet_is_independent(shared_runtime, sheet_name, shared, parent_ref):
    from overseas_costing.services.source_review_manifest_service import stable_source_identity

    repo, _ = shared_runtime
    source_id = stable_source_identity({'source_id': 'PACKING', 'sheet_name': sheet_name})[0]
    source = {**repo.sources[-1], 'source_id': source_id, 'parent_source_id': 'PACKING'}
    repo.sources.append(source)
    repo.run['candidates_json'][0]['source_refs'] = [
        {'source_id': 'PACKING', 'sheet': sheet_name} if parent_ref else {'source_id': source_id}]
    repo.run['source_manifest_json'] = list(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    candidate = next(candidate for candidate in catalog['field_candidates']
                     if candidate['fieldname'] == 'gross_weight_kg' and candidate['item_name'] == 'I1')
    assert candidate['default_selected'] is not shared
    assert bool(candidate.get('packing_group_candidate_ids')) is shared


def test_confirmed_weight_group_does_not_approve_an_unverified_volume(shared_runtime):
    repo, _ = shared_runtime
    repo.run['draft_json']['packing_group_candidates'][0]['source_fingerprint'] = 'PACKING-HASH'
    repo.get_packing_groups = lambda batch, version: [{
        'group_id': 'MANUAL-NATIVE-GROUP', 'status': 'confirmed',
        'source_fingerprint': 'PACKING-HASH', 'member_keys': ['LINE-1', 'LINE-2'],
        'gross_weight_kg': '15.05', 'net_weight_kg': '14.55', 'package_count': '1', 'volume_m3': 0}]
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    fields = {candidate['fieldname']: candidate for candidate in catalog['field_candidates']
              if candidate['item_name'] == 'I1'}
    assert fields['gross_weight_kg']['packing_group_confirmed']
    assert not fields['volume_m3']['packing_group_confirmed']
    assert not fields['gross_weight_kg']['default_selected']
    assert not fields['volume_m3']['default_selected']


def test_native_purchase_value_evidence_remains_automatically_fillable(shared_runtime):
    from overseas_costing.services.source_review_extract_service import build_system_approval_proposals
    from overseas_costing.tests.test_purchase_spec_identity import purchase

    repo, _ = shared_runtime
    for row in repo.items:
        row.update(unit='个', shipped_uom='个')
    source = purchase('Old', 'SKU1')
    source['source_hash'] = 'PURCHASE-HASH'
    document = {'document_id': 'PURCHASE-DOC', 'form_fields': source['form_fields'],
                'source_ref': {'source_id': source['source_id'], 'approval_role': 'purchase'}}
    proposals = build_system_approval_proposals(repo.items, source)
    for proposal in proposals:
        for ref in proposal['source_refs']:
            ref['document_id'] = 'PURCHASE-DOC'
    normalized = ai.normalize_source_review_proposals(proposals, repo.items, [document],
        trusted_system_proposal_ids={proposal['proposal_id'] for proposal in proposals})
    repo.sources.append(source)
    repo.run['candidates_json'].extend(normalized)
    repo.run['source_manifest_json'] = list(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = selections.material_fingerprint(
        repo.items, repo.sources, repo.context)
    staged = controller.preview('B1', repo.run['name'])
    assert staged['can_apply'] and 'goods_value' in staged['field_names']
    assert not set(staged['field_names']) & {'gross_weight_kg', 'net_weight_kg', 'volume_m3', 'package_count'}
    result = controller.apply('B1', repo.run['name'], staged['preview_id'], staged['preview_revision'])
    assert result['status'] == 'APPLIED'
    assert any(change['fieldname'] == 'goods_value' and change['value'] == '100'
               for change in repo.writes[0]['changes'])


def test_verified_individual_approval_cell_is_not_mistaken_for_the_attachment_total(shared_runtime):
    from overseas_costing.services.source_review_extract_service import build_system_approval_proposals

    repo, _ = shared_runtime
    source = {**repo.sources[0], 'form_fields': {'货物信息Bienes': [{
        '物料编码': 'SKU1', '规格型号': 'Old', '数量': 1, '单位': '件', '毛重': 15.05}]}}
    document = {'document_id': 'DOC', 'form_fields': source['form_fields'],
                'source_ref': {'source_id': 'DOC', 'approval_role': 'international_logistics'}}
    proposals = build_system_approval_proposals(repo.items, source)
    for proposal in proposals:
        for ref in proposal['source_refs']:
            ref['document_id'] = 'DOC'
    normalized = ai.normalize_source_review_proposals(proposals, repo.items, [document],
        trusted_system_proposal_ids={proposal['proposal_id'] for proposal in proposals})
    repo.run['candidates_json'].extend(normalized)
    catalog = selections.review_catalog(repo, 'B1', repo.run)
    selected = [candidate for candidate in catalog['field_candidates']
                if candidate['item_name'] == 'I1' and candidate['fieldname'] == 'gross_weight_kg'
                and candidate['default_selected']]
    assert len(selected) == 1 and str(selected[0]['suggested_value']) == '15.05'
