"""Server preview fences, idempotence and fee-only reuse without an AI call."""
from contextlib import contextmanager
from copy import deepcopy
import json
import re
import pytest
from overseas_costing.services import material_ai_selection_service as service, material_ai_fill_service as ai
from overseas_costing.tests.test_material_ai_context_fingerprint import ContextRepository


class Repo(ContextRepository):
    def __init__(self):
        super().__init__()
        self.context['effective_source']={'root_kind':'logistics','fingerprint':'SOURCE','packing':{'root_kind':'logistics','source_snapshot':'S1','available':True},'freight':{'revision':'F1'}}
        self.items[0].update(gross_weight_kg=None,quantity=1,actual_shipped_qty=1,unit='件',shipped_uom='件')
        self.fees=[];self.sources=[{
            'source_id':'DOC','source_kind':'approval_form','source_hash':'HASH',
            'approval_role':'international_logistics','approval_title':'国际物流审批',
        }]
        self.rolled_back=False
        self.create_run({'batch':'B1','version':'V1','status':'READY','proposal_version':1,'clarification_text':'','source_manifest_json':self.sources,
            'input_fingerprint':ai._source_review_fingerprint('B1','V1',self.items,self.sources,'',context=self.context),
            'draft_json':{'material_input_fingerprint':service.material_fingerprint(self.items,self.sources,self.context),
                          'row_review_policy':service.rows.POLICY,
                          'processing_version':ai.SOURCE_REVIEW_PROCESSING_VERSION},
            'candidates_json':[{
                'proposal_id':'P1','proposal_type':'item_update','target_item_name':'I1',
                'default_selected':True,'source_refs':[{'source_id':'DOC'}],
                'payload':{'fields':{'gross_weight_kg':2}},
            }]})
    def list_sources(self,*args):return deepcopy(self.sources)
    def get_fees(self,*args):return deepcopy(self.fees)
    def lock_review_scope(self,*args):pass
    def lock_review_inputs(self,*args):pass
    def save_row_review_draft(self,run,draft):run['draft_json']=deepcopy(draft)
    def rollback(self):self.rolled_back=True;self.writes=[]
    def apply_row_selection(self,run,preview,draft,context):
        self.writes.append(deepcopy(preview))
        result={'ok':True,'preview_id':preview['id'],'version_name':'V1'}
        run.update(status='APPLIED',draft_json={**draft,'row_application':result})
        return result


def prepare(repo,ids=None,fees=None,mode='fill_missing',packing_group_ids=None,packing_assignments=None):
    catalog=service.review_catalog(repo,'B1',repo.run)
    ids=ids if ids is not None else [r['row_id'] for r in catalog['rows'] if r['default_selected']]
    return service.prepare('B1',repo.run['name'],ids,fees or [],mode,'V1',
                           packing_group_ids=packing_group_ids,
                           packing_assignments=packing_assignments,repository=repo)['preview']


def confirm(repo,preview):
    return service.confirm('B1',repo.run['name'],preview['id'],preview['revision'],'TOKEN','M1',repository=repo)


def payment_match_preview(repo):
    repo.sources[0].update(
        payment_match_candidate=True,
        payment_match_candidate_id='FC-1',
        payment_match_candidate_revision='FR-1',
        payment_match_version='V1',
    )
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    return prepare(repo)


def pending_adopted_scope(repo):
    repo.context['effective_source']={
        'root_kind':'expense','available':True,'approved':False,'invalid':False,
        'separate_adoption':True,'fingerprint':'PENDING-SCOPE',
        'packing':{'selected_source':{'id':'ADOPTED'},'available':True,'approved':False},
    }
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)


def test_unselected_source_materialization_does_not_invalidate_review_fingerprints():
    context = {'batch': 'B1', 'version': 'V1', 'transport_mode': 'AIR'}
    items = [{'name': 'I1', 'material_code': 'SKU1'}]
    before = [
        {'source_id': 'SELECTED', 'source_kind': 'approval_form', 'source_hash': 'S1', 'selected': True},
        {'source_id': 'IGNORED', 'source_kind': 'approval_attachment', 'source_hash': 'OLD', 'selected': False},
    ]
    after = deepcopy(before)
    after[1]['source_hash'] = 'MATERIALIZED'

    assert ai._source_review_fingerprint('B1', 'V1', items, before, '', context=context) == (
        ai._source_review_fingerprint('B1', 'V1', items, after, '', context=context)
    )
    assert service.material_fingerprint(items, before, context) == (
        service.material_fingerprint(items, after, context)
    )


def test_material_fingerprint_normalizes_project_routing_and_detects_policy_changes():
    items = [{'name': 'I1', 'material_code': 'SKU1'}]
    routing = {
        'route_revision': 'ROUTES-1',
        'options': [{
            'project_collection': 'LatinGo拉丁购',
            'subsidiary_code': 'LATIN',
            'site_code': 'DEEPLINKERP',
            'revision': 3,
            'ai_match_hint': '宠物用品',
            'is_approval_candidate': True,
            'ignored_ui_metadata': 'one',
        }],
    }
    baseline = service.material_fingerprint(items, [], {'project_routing': routing})
    reordered = {
        'options': [{
            **routing['options'][0],
            'ignored_ui_metadata': 'two',
        }],
        'route_revision': 'ROUTES-1',
        'ignored_response_metadata': True,
    }
    assert baseline == service.material_fingerprint(items, [], {'project_routing': reordered})

    for fieldname, value in (
        ('subsidiary_code', 'OTHER'),
        ('site_code', 'MEXICO'),
        ('revision', 4),
        ('ai_match_hint', '宠物用品、户外用品'),
        ('is_approval_candidate', False),
    ):
        changed = deepcopy(routing)
        changed['options'][0][fieldname] = value
        assert baseline != service.material_fingerprint(
            items, [], {'project_routing': changed}
        )
    changed_revision = deepcopy(routing)
    changed_revision['route_revision'] = 'ROUTES-2'
    assert baseline != service.material_fingerprint(
        items, [], {'project_routing': changed_revision}
    )


def test_ready_project_draft_becomes_stale_for_catalog_and_confirm_when_route_changes():
    repo = Repo()
    repo.context['project_routing'] = {
        'route_revision': 'ROUTES-1',
        'options': [{
            'project_collection': 'LatinGo拉丁购',
            'subsidiary_code': 'LATIN',
            'site_code': 'DEEPLINKERP',
            'revision': 3,
            'ai_match_hint': '宠物用品',
            'is_approval_candidate': True,
        }],
    }
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context
    )
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context
    )
    preview = prepare(repo)

    repo.context['project_routing']['route_revision'] = 'ROUTES-2'
    repo.context['project_routing']['options'][0]['revision'] = 4
    repo.context['project_routing']['options'][0]['ai_match_hint'] = '宠物用品、户外用品'

    with pytest.raises(ValueError, match='重新分析'):
        service.review_catalog(repo, 'B1', repo.run)
    with pytest.raises(ValueError, match='重新分析'):
        confirm(repo, preview)
    assert repo.writes == []


def test_pending_adopted_material_scope_can_reenter_review_prepare_and_confirm():
    repo=Repo();pending_adopted_scope(repo)

    catalog=service.review_catalog(repo,'B1',repo.run)
    row_ids=[row['row_id'] for row in catalog['rows'] if row['default_selected']]
    preview=service.prepare('B1',repo.run['name'],row_ids,[],'fill_missing','V1',repository=repo)['preview']

    assert confirm(repo,preview)['ok']
    assert len(repo.writes)==1


def test_ready_with_warnings_can_prepare_and_confirm_when_server_projection_is_applicable():
    repo=Repo();repo.run['status']='READY_WITH_WARNINGS';repo.run['source_completeness']='PARTIAL'

    preview=prepare(repo)

    assert preview['can_apply'] is True
    assert confirm(repo,preview)['ok'] is True


def test_ready_with_warnings_without_candidates_is_viewable_but_not_confirmable():
    repo=Repo();repo.run['status']='READY_WITH_WARNINGS';repo.run['source_completeness']='UNAVAILABLE'
    repo.run['candidates_json']=[]

    response=service.prepare(
        'B1',repo.run['name'],[],[],'fill_missing','V1',repository=repo,
    )

    assert response['preview']['can_apply'] is False
    with pytest.raises(ValueError,match='请选择需要填充'):
        confirm(repo,response['preview'])


def test_ready_with_warnings_does_not_bypass_outdated_preview_policy():
    repo=Repo();repo.run['status']='READY_WITH_WARNINGS'
    repo.run['draft_json']['processing_version']='outdated-policy'

    with pytest.raises(ValueError,match='规则已升级'):
        prepare(repo)


def test_confirmation_recomputes_preview_cleans_receipt_and_reuses_result():
    repo=Repo();preview=prepare(repo)
    public=deepcopy(preview);public['rows'][0]['gross_weight_kg']=999
    confirm(repo,public);confirm(repo,public)
    assert len(repo.writes)==1 and repo.writes[0]['rows'][0]['gross_weight_kg']==2
    assert 'row_previews' not in repo.run['draft_json']
    assert 'current_row_preview' not in repo.run['draft_json']


def test_public_catalog_and_compact_receipt_deeply_hide_purchase_evidence():
    repo=Repo()
    repo.items[0]['extra_json']=json.dumps({
        'logistics_row':{'identity':'LINE','purchase_fact':{'unit_price':12},
                         'purchase_fact_history':[{'purchase_fact':{'unit_price':10}}]},
        'settlement_original_values':{'name':'OLD-ITEM','goods_value':20},
        'ai_fill_original_values':{'name':'OLDER-ITEM','goods_value':10},
    })
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)

    catalog=service.review_catalog(repo,'B1',repo.run)
    source_row=next(row for row in catalog['rows'] if row['origin']=='source')
    assert catalog['policy']==service.rows.POLICY
    assert source_row['meaningful_field_count']==1
    assert '已默认选择' in source_row['default_selection_reason']
    selected=[row['row_id'] for row in catalog['rows'] if row['default_selected']]
    response=service.prepare('B1',repo.run['name'],selected,[],'update_selected','V1',repository=repo)

    for public_payload in (catalog,response['row_review'],response['preview']):
        serialized=json.dumps(public_payload,ensure_ascii=False)
        assert '_review_' not in serialized
        assert '_price_metadata' not in serialized
        assert '_verified_prior_item' not in serialized
        assert 'purchase_fact' not in serialized
        assert 'settlement_original_values' not in serialized
        assert 'ai_fill_original_values' not in serialized
        assert 'OLD-ITEM' not in serialized and 'OLDER-ITEM' not in serialized
    receipt=repo.run['draft_json']['row_previews'][response['preview']['id']]
    internal=json.dumps(receipt,ensure_ascii=False)
    assert 'purchase_fact' not in internal
    assert not ({'rows','changes','actual_sources','fees','sources'} & receipt.keys())
    assert 'packing_group_candidates' not in receipt
    assert 'merged_amount_groups' not in receipt
    assert receipt['selected_row_ids']==selected
    assert receipt['revision']==response['preview']['revision']


def test_payment_match_receipt_contains_only_server_candidate_reference():
    repo=Repo()
    repo.sources[0].update(
        payment_match_candidate=True,
        payment_match_candidate_id='FC-1',
        payment_match_candidate_revision='FR-1',
        payment_match_version='V1',
        scoped_text='应付运费 RMB 120',
        scoped_goods=[{'material_code':'SECRET-SKU','quantity':2}],
    )
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)

    response=service.prepare('B1',repo.run['name'],[],[],'fill_missing','V1',repository=repo)
    public=response['preview']['payment_match_candidate']
    receipt=repo.run['draft_json']['row_previews'][response['preview']['id']]

    assert public=={'candidate_id':'FC-1','revision':'FR-1','version':'V1'}
    assert receipt['payment_match_candidate']==public
    serialized=json.dumps(receipt,ensure_ascii=False)
    assert '应付运费 RMB 120' not in serialized and 'SECRET-SKU' not in serialized


def test_multiple_user_selected_payment_matches_are_kept_as_id_only_receipt():
    repo = Repo()
    repo.sources = [
        {
            **repo.sources[0],
            'source_id': f'DOC-{index}',
            'source_hash': f'HASH-{index}',
            'payment_match_candidate': True,
            'payment_match_candidate_id': f'FC-{index}',
            'payment_match_candidate_revision': f'FR-{index}',
            'payment_match_version': 'V1',
            'payment_match_user_selected': True,
            'scoped_text': f'private-{index}',
        }
        for index in (1, 2)
    ]
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context)

    response = service.prepare('B1', repo.run['name'], [], [], 'fill_missing', 'V1', repository=repo)
    receipt = repo.run['draft_json']['row_previews'][response['preview']['id']]

    assert response['preview']['payment_match_candidate'] is None
    assert response['preview']['payment_match_candidates'] == [
        {'candidate_id': 'FC-1', 'revision': 'FR-1', 'version': 'V1', 'user_selected': True},
        {'candidate_id': 'FC-2', 'revision': 'FR-2', 'version': 'V1', 'user_selected': True},
    ]
    assert receipt['payment_match_candidates'] == response['preview']['payment_match_candidates']
    assert 'private-' not in json.dumps(receipt, ensure_ascii=False)


def test_payment_match_confirmation_locks_arbitration_before_review_scope():
    repo=Repo();preview=payment_match_preview(repo);events=[]
    original_get_run=repo.get_run
    original_apply=repo.apply_row_selection

    repo.get_run=lambda run_id:(events.append('initial_read') or original_get_run(run_id))
    repo.lock_run=lambda run_id:(events.append('run_lock') or original_get_run(run_id))
    repo.lock_review_scope=lambda *_args:events.append('review_scope')
    repo.lock_review_inputs=lambda *_args:events.append('review_inputs')
    repo.apply_row_selection=lambda *args:(events.append('apply') or original_apply(*args))

    @contextmanager
    def payment_scope():
        events.extend(('transaction_begin','match_lock'))
        try:
            yield
        except Exception:
            events.append('transaction_rollback')
            raise
        else:
            events.append('transaction_commit')

    repo.payment_match_confirmation_scope=payment_scope

    assert confirm(repo,preview)['ok']
    assert events == [
        'initial_read','transaction_begin','match_lock','review_scope','run_lock',
        'review_inputs','apply','transaction_commit',
    ]


def test_payment_match_confirmation_revalidates_receipt_after_arbitration_lock():
    repo=Repo();preview=payment_match_preview(repo);events=[]

    @contextmanager
    def payment_scope():
        events.append('match_lock')
        receipt=repo.run['draft_json']['row_previews'][preview['id']]
        receipt['revision']='CHANGED-WHILE-WAITING'
        yield

    repo.payment_match_confirmation_scope=payment_scope
    repo.lock_review_scope=lambda *_args:events.append('review_scope')

    with pytest.raises(ValueError,match='预览已变化'):
        confirm(repo,preview)
    assert events[:2] == ['match_lock','review_scope']
    assert not repo.writes


def test_payment_match_confirmation_failure_rolls_back_outer_transaction():
    repo=Repo();preview=payment_match_preview(repo)
    repo.payment_state='pending'

    @contextmanager
    def payment_scope():
        before_state=repo.payment_state
        before_writes=deepcopy(repo.writes)
        try:
            yield
        except Exception:
            repo.payment_state=before_state
            repo.writes=before_writes
            raise

    def fail_after_payment_confirmation(*_args):
        repo.payment_state='confirmed'
        repo.writes.append('partial')
        raise RuntimeError('downstream failed')

    repo.payment_match_confirmation_scope=payment_scope
    repo.apply_row_selection=fail_after_payment_confirmation

    with pytest.raises(RuntimeError,match='downstream failed'):
        confirm(repo,preview)
    assert repo.payment_state == 'pending'
    assert not repo.writes


def test_conflicting_payment_candidate_references_are_not_authenticated():
    repo=Repo()
    first={
        **repo.sources[0],
        'source_id':'PAY-1',
        'payment_match_candidate':True,
        'payment_match_candidate_id':'FC-1',
        'payment_match_candidate_revision':'FR-1',
        'payment_match_version':'V1',
    }
    second={
        **first,
        'source_id':'PAY-2',
        'payment_match_candidate_id':'FC-2',
        'payment_match_candidate_revision':'FR-2',
    }
    repo.sources=[first,second]
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)

    preview=service.prepare('B1',repo.run['name'],[],[],'fill_missing','V1',repository=repo)['preview']

    assert preview.get('payment_match_candidate') is None


def test_old_lightweight_receipt_policy_must_be_repreviewed():
    repo=Repo();preview=prepare(repo)
    receipt=repo.run['draft_json']['row_previews'][preview['id']]
    receipt['receipt_policy']='ai-field-preview-receipt-2'

    with pytest.raises(ValueError,match='规则已升级'):
        confirm(repo,preview)


def test_previous_processing_version_is_rejected_by_review_prepare_and_confirm():
    review_repo=Repo()
    review_repo.run['draft_json']['processing_version']='procurement-source-9'
    with pytest.raises(ValueError,match='重新分析'):
        service.review_catalog(review_repo,'B1',review_repo.run)

    prepare_repo=Repo()
    prepare_repo.run['draft_json']['processing_version']='procurement-source-9'
    with pytest.raises(ValueError,match='重新分析'):
        service.prepare(
            'B1',prepare_repo.run['name'],[],[],'fill_missing','V1',repository=prepare_repo)

    confirm_repo=Repo();preview=prepare(confirm_repo)
    confirm_repo.run['draft_json']['processing_version']='procurement-source-9'
    with pytest.raises(ValueError,match='重新分析'):
        confirm(confirm_repo,preview)
    assert not confirm_repo.writes


def test_public_catalog_and_preview_replace_process_instance_ids_with_stable_opaque_ids():
    repo = Repo()
    raw_process_id = 'SECRET-PROCESS-INSTANCE-123'
    repo.sources[0].update(
        process_instance_id=raw_process_id,
        parent_source_id=raw_process_id,
        approval_title='国际物流审批',
    )
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    repo.run['candidates_json'][0]['source_refs'][0]['process_instance_id'] = raw_process_id
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context)

    first_catalog = service.review_catalog(repo, 'B1', repo.run)
    second_catalog = service.review_catalog(repo, 'B1', repo.run)
    preview = prepare(repo)

    for payload in (first_catalog, second_catalog, preview):
        assert raw_process_id not in json.dumps(payload, ensure_ascii=False)
    first_process_id = first_catalog['stage_snapshots'][1]['processes'][0]['process_instance_id']
    second_process_id = second_catalog['stage_snapshots'][1]['processes'][0]['process_instance_id']
    candidate_process_id = first_catalog['field_candidates'][0]['process_instance_id']
    assert first_process_id.startswith('proc_')
    assert first_process_id == second_process_id == candidate_process_id


def test_public_catalog_exposes_one_safe_open_reference_for_every_real_stage_process():
    repo = Repo()
    raw_process_ids = {
        'payment': 'PAYMENT-PROCESS-001',
        'logistics': 'LOGISTICS-PROCESS-001',
        'purchase_1': 'PURCHASE-PROCESS-001',
        'purchase_2': 'PURCHASE-PROCESS-002',
    }
    repo.sources = [
        {
            'source_id': 'PAY-FORM', 'source_kind': 'approval_form',
            'source_hash': 'PAY-FORM-HASH', 'approval_role': 'payment',
            'approval_title': '月结付款', 'approval_no': 'PAY-001',
            'process_instance_id': raw_process_ids['payment'],
        },
        {
            'source_id': 'PAY-ATTACHMENT', 'source_kind': 'approval_attachment',
            'source_hash': 'PAY-ATTACHMENT-HASH', 'approval_role': 'payment',
            'approval_title': '月结付款', 'approval_no': 'PAY-001',
            'process_instance_id': raw_process_ids['payment'],
        },
        {
            'source_id': 'LOG-FORM', 'source_kind': 'approval_form',
            'source_hash': 'LOG-HASH', 'approval_role': 'international_logistics',
            'approval_title': '国际物流审批', 'approval_no': 'LOG-001',
            'process_instance_id': raw_process_ids['logistics'],
        },
        {
            'source_id': 'PUR-1', 'source_kind': 'approval_form',
            'source_hash': 'PUR-1-HASH', 'approval_role': 'purchase',
            'approval_title': '采购支出 1', 'approval_no': 'PUR-001',
            'process_instance_id': raw_process_ids['purchase_1'],
        },
        {
            'source_id': 'PUR-2', 'source_kind': 'approval_form',
            'source_hash': 'PUR-2-HASH', 'approval_role': 'purchase',
            'approval_title': '采购支出 2', 'approval_no': 'PUR-002',
            'process_instance_id': raw_process_ids['purchase_2'],
        },
    ]
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context)

    catalog = service.review_catalog(repo, 'B1', repo.run)
    processes = [
        process
        for stage in catalog['stage_snapshots']
        for process in stage['processes']
    ]

    assert len(processes) == 4
    assert all(process['can_open'] is True for process in processes)
    assert all(process['source_open_ref'] == process['process_instance_id'] for process in processes)
    assert all(re.fullmatch(r'proc_[0-9a-f]{64}', process['source_open_ref']) for process in processes)
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert all(raw_process_id not in serialized for raw_process_id in raw_process_ids.values())


def test_public_catalog_does_not_offer_internal_attachment_parent_as_dingtalk_process():
    repo = Repo()
    repo.sources = [{
        'source_id': 'oa:PROC-1:FILE-9:sheet:abc',
        'parent_source_id': 'oa:PROC-1:FILE-9',
        'logical_source_id': 'oa:PROC-1:FILE-9',
        'official_url': 'https://example.com/not-a-dingtalk-order',
        'source_kind': 'approval_attachment',
        'source_hash': 'ATTACHMENT-HASH',
        'approval_role': 'payment',
        'approval_title': '月结付款附件',
        'approval_no': 'PAY-001',
    }]
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context)

    catalog = service.review_catalog(repo, 'B1', repo.run)
    process = catalog['stage_snapshots'][0]['processes'][0]

    assert process['can_open'] is False
    assert process['source_open_ref'] == ''


def test_source_open_target_resolves_monthly_payment_and_rejects_forged_or_cross_batch_refs():
    repo = Repo()
    raw_process_id = 'PAYMENT-MONTHLY-PROCESS-001'
    repo.sources = [{
        'source_id': 'PAYMENT-FORM', 'source_kind': 'approval_form',
        'source_hash': 'PAYMENT-HASH', 'approval_role': 'payment',
        'approval_title': '李仲华提交的月结付款', 'approval_no': '202607211417000078258',
        'process_instance_id': raw_process_id,
    }]
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    source_open_ref = ai._opaque_public_process_id(raw_process_id)

    target = ai.get_source_ai_process_open_target(
        'B1', repo.run['name'], source_open_ref, repository=repo)

    assert target['ok'] is True
    assert target['source_open_ref'] == source_open_ref
    assert target['label'] == '李仲华提交的月结付款'
    assert target['approval_no'] == '202607211417000078258'
    assert target['open_mode'] == 'desktop_protocol'
    assert target['open_url'].startswith('dingtalk://dingtalkclient/')
    assert raw_process_id in target['open_url']

    with pytest.raises(ValueError, match='原单链接已失效'):
        ai.get_source_ai_process_open_target(
            'B1', repo.run['name'], 'proc_' + ('f' * 64), repository=repo)
    with pytest.raises(ValueError, match='不属于当前批次'):
        ai.get_source_ai_process_open_target(
            'B2', repo.run['name'], source_open_ref, repository=repo)


def test_public_process_id_prefix_cannot_bypass_opaque_digest_validation():
    repo = Repo()
    raw_process_id = 'proc_external-dingtalk-instance-secret'
    repo.sources[0].update(
        process_instance_id=raw_process_id,
        parent_source_id=raw_process_id,
        approval_title='国际物流审批',
    )
    repo.run['source_manifest_json'] = deepcopy(repo.sources)
    repo.run['candidates_json'][0]['source_refs'][0]['process_instance_id'] = raw_process_id
    repo.run['input_fingerprint'] = ai._source_review_fingerprint(
        'B1', 'V1', repo.items, repo.sources, '', context=repo.context)
    repo.run['draft_json']['material_input_fingerprint'] = service.material_fingerprint(
        repo.items, repo.sources, repo.context)

    first_catalog = service.review_catalog(repo, 'B1', repo.run)
    second_catalog = service.review_catalog(repo, 'B1', repo.run)
    preview = prepare(repo)

    for payload in (first_catalog, second_catalog, preview):
        assert raw_process_id not in json.dumps(payload, ensure_ascii=False)
    first_process_id = first_catalog['stage_snapshots'][1]['processes'][0]['process_instance_id']
    second_process_id = second_catalog['stage_snapshots'][1]['processes'][0]['process_instance_id']
    assert re.fullmatch(r'proc_[0-9a-f]{64}', first_process_id)
    assert first_process_id == second_process_id


def test_valid_public_process_digest_is_not_digested_again():
    opaque_process_id = 'proc_' + ('a' * 64)

    public = ai._public_ai_payload({'process_instance_id': opaque_process_id})

    assert public['process_instance_id'] == opaque_process_id


def test_field_choices_are_authenticated_without_persisting_business_values():
    repo = Repo()
    catalog = service.review_catalog(repo, 'B1', repo.run)
    candidate = next(row for row in catalog['field_candidates'] if row['default_selected'])
    choices = {f"{candidate['item_name']}:{candidate['fieldname']}": candidate['candidate_id']}

    response = service.prepare(
        'B1', repo.run['name'], [], [], 'update_selected', 'V1',
        field_choices=choices, repository=repo,
    )
    preview = response['preview']
    receipt = repo.run['draft_json']['row_previews'][preview['id']]

    assert preview['rows'][0]['gross_weight_kg'] == 2
    assert receipt['selected_field_choices'] == choices
    serialized = json.dumps(receipt, ensure_ascii=False)
    assert 'suggested_value' not in serialized
    assert len(serialized) < 32_000
    assert confirm(repo, preview)['ok']
    assert repo.writes[0]['rows'][0]['gross_weight_kg'] == 2


def test_new_selection_supersedes_older_preview():
    repo=Repo();old=prepare(repo);prepare(repo,ids=[])
    assert old['id'] not in repo.run['draft_json']['row_previews']
    assert len(repo.run['draft_json']['row_previews'])==1
    with pytest.raises(ValueError,match='最新预览'):confirm(repo,old)
    assert not repo.writes


def test_legacy_full_preview_receipt_is_accepted_but_its_rows_are_not_trusted():
    repo=Repo();preview=prepare(repo)
    receipt=repo.run['draft_json']['row_previews'][preview['id']]
    receipt['rows']=deepcopy(preview['rows'])
    receipt['rows'][0]['gross_weight_kg']=999
    receipt['changes']=deepcopy(preview['changes'])
    receipt['actual_sources']=deepcopy(preview['actual_sources'])

    assert confirm(repo,preview)['ok']
    assert repo.writes[0]['rows'][0]['gross_weight_kg']==2


def test_large_private_projection_adds_only_a_bounded_receipt_and_can_confirm():
    repo=Repo()
    large_private='x'*(11*1024*1024)
    repo.run['draft_json']['large_analysis']=large_private
    repo.run['candidates_json']=[{
        'proposal_id':'P-LARGE','proposal_type':'logistics_reconcile','default_selected':True,
        'source_refs':[{'source_id':'DOC'}],
        'payload':{'rows':[{
            'name':'draft-large','material_code':'SKU1','product_name':'Large',
            'gross_weight_kg':2,'actual_shipped_qty':1,'unit':'件','_review_origin':'source',
            '_review_price_metadata':{'logistics_row':{'purchase_fact':large_private}},
        }]},
    }]
    before_size=len(json.dumps(repo.run['draft_json'],ensure_ascii=False))

    preview=prepare(repo,mode='update_selected')

    after_size=len(json.dumps(repo.run['draft_json'],ensure_ascii=False))
    receipt=repo.run['draft_json']['row_previews'][preview['id']]
    assert after_size-before_size < 128*1024
    assert not ({'rows','changes','actual_sources','fees','sources'} & receipt.keys())
    assert large_private not in json.dumps(receipt,ensure_ascii=False)
    assert confirm(repo,preview)['ok']


def test_packing_group_default_can_be_deselected_before_confirmation():
    repo=Repo()
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'GROUP-1','member_keys':['LINE-1','LINE-2'],
        'gross_weight_kg':'42.05','package_count':None,
        'default_selected':True,'can_apply':True,
        'evidence':[{'kind':'trusted_comment_text'}],
    }]

    selected=prepare(repo,ids=[],mode='update_selected')
    assert selected['selected_packing_group_ids']==['GROUP-1']
    assert selected['can_apply'] is True

    deselected=prepare(repo,ids=[],mode='update_selected',packing_group_ids=[])
    assert deselected['selected_packing_group_ids']==[]
    assert deselected['packing_group_candidates']==[]
    assert deselected['can_apply'] is False


def test_packing_assignment_can_apply_group_facts_to_exactly_one_material():
    repo=Repo()
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'PACK-1','member_keys':['LINE-1'],
        'gross_weight_kg':'42.05','volume_m3':'0.01518','package_count':None,
        'can_apply':True,'default_selected':True,
        'assignment_options':[
            {'assignment_id':'PACK-1:LINE-1','mode':'single_item','member_keys':['LINE-1'],
             'default_selected':False,'can_apply':True},
            {'assignment_id':'PACK-1:LINE-2','mode':'single_item','member_keys':['LINE-2'],
             'default_selected':True,'can_apply':True},
            {'assignment_id':'PACK-1:GROUP','mode':'one_box_group','member_keys':['LINE-1','LINE-2'],
             'default_selected':False,'can_apply':True},
        ],
    }]

    preview=prepare(repo,ids=[],mode='update_selected',packing_assignments={'PACK-1':'PACK-1:LINE-2'})

    rows={row['name']:row for row in preview['rows']}
    assert rows['I1'].get('gross_weight_kg') is None
    assert rows['I2']['gross_weight_kg']=='42.05'
    assert rows['I2']['volume_m3']=='0.01518'
    assert preview['selected_packing_assignments']=={'PACK-1':'PACK-1:LINE-2'}
    assert preview['packing_group_candidates']==[]


def test_packing_assignment_is_validated_against_authoritative_projected_rows():
    repo=Repo()
    repo.items=[{**repo.items[0],'stable_line_key':'OLD-LINE','row_no':1}]
    repo.run['candidates_json']=[{
        'proposal_id':'LOGISTICS-SCOPE','proposal_type':'logistics_reconcile',
        'default_selected':True,'result_origin':'SYSTEM','source_refs':[{'source_id':'DOC'}],
        'payload':{'scope_status':'AUTHORITATIVE','original_item_names':['I1'],
                   'excluded_item_names':[],'unresolved':[],'rows':[
                       {**repo.items[0],'name':'I1','_existing_name':'I1','_review_origin':'source',
                        'stable_line_key':'NEW-LINE','row_no':1,
                        '_review_source_values':{'material_code':'SKU1','product_name':'SKU1',
                                                 'actual_shipped_qty':1,'stable_line_key':'NEW-LINE'}}]},
    }]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'PACK-NEW','member_keys':['NEW-LINE'],
        'gross_weight_kg':'42.05','can_apply':True,'default_selected':True,
        'assignment_options':[
            {'assignment_id':'PACK-NEW:NEW-LINE','mode':'single_item','member_keys':['NEW-LINE'],
             'default_selected':True,'can_apply':True},
        ],
    }]

    preview=prepare(repo,mode='update_selected',packing_assignments={
        'PACK-NEW':'PACK-NEW:NEW-LINE',
    })

    assert preview['rows'][0]['stable_line_key']=='OLD-LINE'
    assert preview['rows'][0]['gross_weight_kg']=='42.05'


def test_packing_assignment_maps_legacy_item_identity_into_authoritative_scope():
    repo=Repo()
    repo.items=[{**repo.items[0],'stable_line_key':'','row_no':1}]
    repo.run['candidates_json']=[{
        'proposal_id':'LOGISTICS-SCOPE','proposal_type':'logistics_reconcile',
        'default_selected':True,'result_origin':'SYSTEM','source_refs':[{'source_id':'DOC'}],
        'payload':{'scope_status':'AUTHORITATIVE','original_item_names':['I1'],
                   'excluded_item_names':[],'unresolved':[],'rows':[
                       {**repo.items[0],'name':'I1','_existing_name':'I1','_review_origin':'source',
                        'stable_line_key':'NEW-LINE','row_no':1,
                        '_review_source_values':{'material_code':'SKU1','product_name':'SKU1',
                                                 'actual_shipped_qty':1,'stable_line_key':'NEW-LINE'}}]},
    }]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'PACK-LEGACY','member_keys':['legacy:I1'],
        'gross_weight_kg':'42.05','can_apply':True,'default_selected':True,
        'assignment_options':[
            {'assignment_id':'PACK-LEGACY:legacy:I1','mode':'single_item',
             'member_keys':['legacy:I1'],'default_selected':True,'can_apply':True},
        ],
    }]

    preview=prepare(repo,mode='update_selected')

    assert preview['rows'][0]['gross_weight_kg']=='42.05'


def test_packing_assignment_maps_server_candidate_by_unique_material_label():
    repo=Repo()
    repo.items=[{**repo.items[0],'stable_line_key':'OLD-LINE','row_no':1}]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'PACK-LABEL','member_keys':['SOURCE-LINE'],
        'member_labels':['SKU1'],'gross_weight_kg':'42.05',
        'can_apply':True,'default_selected':True,
        'assignment_options':[
            {'assignment_id':'PACK-LABEL:SOURCE-LINE','mode':'single_item',
             'member_keys':['SOURCE-LINE'],'default_selected':True,'can_apply':True},
        ],
    }]

    preview=prepare(repo,mode='update_selected')

    assert preview['rows'][0]['gross_weight_kg']=='42.05'


def test_packing_assignment_group_is_the_only_adopted_relation():
    repo=Repo()
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'PACK-1','member_keys':['LINE-1'],
        'gross_weight_kg':'42.05','volume_m3':'0.01518','package_count':None,
        'can_apply':True,'default_selected':True,
        'assignment_options':[
            {'assignment_id':'PACK-1:LINE-1','mode':'single_item','member_keys':['LINE-1'],
             'default_selected':False,'can_apply':True},
            {'assignment_id':'PACK-1:GROUP','mode':'one_box_group','member_keys':['LINE-1','LINE-2'],
             'package_count_override':'1','default_selected':True,'can_apply':True},
        ],
    }]

    preview=prepare(repo,ids=[],mode='update_selected',packing_assignments={'PACK-1':'PACK-1:GROUP'})

    assert preview['selected_packing_assignments']=={'PACK-1':'PACK-1:GROUP'}
    assert [candidate['member_keys'] for candidate in preview['packing_group_candidates']]==[['LINE-1','LINE-2']]
    assert preview['packing_group_candidates'][0]['package_count']=='1'
    assert not any(change['fieldname'] in {'gross_weight_kg','volume_m3','package_count'}
                   for change in preview['changes'])


def test_packing_group_selection_tolerates_unrelated_legacy_rows_without_stored_keys():
    repo=Repo()
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
        {'name':'LEGACY-3','material_code':'SKU3','stable_line_key':'','row_no':3,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'GROUP-1','member_keys':['LINE-1','LINE-2'],
        'gross_weight_kg':'42.05','package_count':None,
        'default_selected':True,'can_apply':True,
        'evidence':[{'kind':'xlsx_merge'}],
    }]

    preview=prepare(repo,ids=[],mode='update_selected')

    assert preview['selected_packing_group_ids']==['GROUP-1']
    assert preview['packing_group_candidates'][0]['member_keys']==['LINE-1','LINE-2']


def test_unmatched_xlsx_group_is_not_implicitly_selected():
    repo=Repo()
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['draft_json']['packing_group_candidates']=[{
        'candidate_id':'GROUP-NEW','member_keys':['LINE-1','logistics:new'],
        'gross_weight_kg':'42.05','package_count':None,
        'default_selected':False,'can_apply':False,
        'needs_member_confirmation':True,
        'resolution_reason':'装箱组包含尚未确认新增的物料，成员未全部匹配现有物料。',
        'evidence':[{'kind':'xlsx_merge'}],
    }]

    preview=prepare(repo,ids=[],mode='update_selected')

    assert preview['selected_packing_group_ids']==[]
    assert preview['packing_group_candidates']==[]


def test_preview_cleanup_shallow_copies_top_level_without_copying_large_nested_draft():
    payload='x'*1024
    large_analysis=[
        {'documents':[{'rows':[{'evidence':payload} for _ in range(32)]}]}
        for _ in range(352)
    ]
    assert len(json.dumps(large_analysis)) > 11*1024*1024
    draft={
        'large_analysis':large_analysis,
        'row_previews':{'P1':{'id':'P1'}},
        'current_row_preview':'P1',
    }

    cleaned=service._clean_preview_draft(draft)

    assert cleaned is not draft
    assert cleaned['large_analysis'] is large_analysis
    assert cleaned['large_analysis'][0]['documents'] is large_analysis[0]['documents']
    assert 'row_previews' not in cleaned and 'current_row_preview' not in cleaned
    cleaned['row_application']={'preview_id':'P1'}
    assert 'row_application' not in draft


def test_policy_upgrade_rejects_preview_created_by_previous_row_policy(monkeypatch):
    repo=Repo()
    preview=prepare(repo)
    monkeypatch.setattr(service.rows,'POLICY','ai-row-review-4')

    with pytest.raises(ValueError,match='(?:不属于|刷新预览|规则已升级)'):
        confirm(repo,preview)

    assert not repo.writes


def test_ready_draft_from_previous_review_policy_requires_reanalysis():
    repo=Repo()
    repo.run['draft_json']['row_review_policy']='ai-row-review-previous'

    with pytest.raises(ValueError,match='规则已升级'):
        service.review_catalog(repo,'B1',repo.run)


def test_review_catalog_merges_run_progress_into_stage_availability_and_warnings():
    repo=Repo()
    repo.sources=[
        {'source_id':'PAY','source_kind':'approval_attachment','approval_role':'logistics_expense',
         'approval_title':'费用支出','available':True},
        {'source_id':'LOG','source_kind':'approval_form','approval_role':'international_logistics',
         'approval_title':'国际物流审批','available':True},
    ]
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['source_progress_json']=[
        {'source_id':'PAY','read_status':'FAILED','status':'FAILED','error':'附件已失效'},
        {'source_id':'LOG','read_status':'COMPLETED','status':'COMPLETED','error':''},
    ]
    repo.run['candidates_json']=[{
        'proposal_id':'LOG','proposal_type':'item_update','target_item_name':'I1',
        'confidence':.99,'source_refs':[{'source_id':'LOG'}],
        'payload':{'fields':{'gross_weight_kg':2}},
    }]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)

    catalog=service.review_catalog(repo,'B1',repo.run)
    payment,logistics,_purchase=catalog['stage_snapshots']

    assert payment['status']=='UNAVAILABLE'
    assert payment['evidence_summary']['unreadable']==1
    assert payment['warnings']==['PAY：未能读取，已跳过。']
    assert logistics['status']=='AVAILABLE'
    assert logistics['evidence_summary']['readable']==1


def test_review_catalog_rebuilds_safe_skip_metadata_from_server_progress_only():
    repo=Repo()
    repo.sources=[
        {'source_id':'FORM','process_instance_id':'LOG-1','source_kind':'approval_form',
         'approval_role':'international_logistics','approval_title':'国际物流审批',
         'skip_reason_code':'FORGED_SOURCE_VALUE','skip_reason_text':'FILE BODY SECRET',
         'elapsed_ms':999},
        {'source_id':'CORRUPT','process_instance_id':'LOG-1','source_kind':'approval_attachment',
         'evidence_kind':'attachment','source_label':'packing.xlsx',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
        {'source_id':'TIMEOUT','process_instance_id':'LOG-1','source_kind':'approval_comment_attachment',
         'evidence_kind':'comment_attachment','source_label':'quote.pdf',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
    ]
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['source_progress_json']=[
        {'source_id':'FORM','read_status':'COMPLETED','status':'COMPLETED',
         'skip_reason_code':'SHOULD_NOT_LEAK','skip_reason_text':'FILE BODY SECRET',
         'elapsed_ms':1},
        {'source_id':'CORRUPT','evidence_id':'CORRUPT','evidence_kind':'attachment',
         'read_status':'SKIPPED','status':'SKIPPED','skip_reason_code':'CORRUPT_DOCUMENT',
         'skip_reason_text':'<html>500 /private/files/secret.xlsx</html>','elapsed_ms':15321,
         'detail':'<html>raw failure</html>','error':'Traceback secret'},
        {'source_id':'TIMEOUT','evidence_id':'TIMEOUT','evidence_kind':'comment_attachment',
         'read_status':'SKIPPED','status':'SKIPPED','skip_reason_code':'PARSE_TIMEOUT',
         'skip_reason_text':'https://files.example.invalid/a?token=SECRET','elapsed_ms':15000},
    ]
    repo.run['candidates_json']=[{
        'proposal_id':'FORM','proposal_type':'item_update','target_item_name':'I1',
        'confidence':.99,'source_refs':[{'source_id':'FORM'}],
        'payload':{'fields':{'gross_weight_kg':2}},
    }]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)

    catalog=service.review_catalog(repo,'B1',repo.run)
    logistics=catalog['stage_snapshots'][1]
    evidence={record['evidence_id']:record
              for process in logistics['processes'] for record in process['evidence']}

    assert evidence['CORRUPT']['skip_reason_code']=='CORRUPT_DOCUMENT'
    assert evidence['CORRUPT']['skip_reason_text']=='资料文件已损坏。'
    assert evidence['CORRUPT']['elapsed_ms']==15321
    assert evidence['TIMEOUT']['skip_reason_code']=='PARSE_TIMEOUT'
    assert evidence['TIMEOUT']['skip_reason_text']=='资料文件解析超时。'
    assert evidence['TIMEOUT']['elapsed_ms']==15000
    assert not ({'skip_reason_code','skip_reason_text','elapsed_ms'} & evidence['FORM'].keys())
    assert logistics['evidence_summary']['records']==list(evidence.values())
    assert catalog['fee_stage_snapshots'][1]['evidence_summary']['records']==list(evidence.values())
    public=json.dumps(catalog,ensure_ascii=False)
    for secret in ('html','Traceback','/private/','token=','FILE BODY SECRET'):
        assert secret.casefold() not in public.casefold()

    response=service.prepare(
        'B1',repo.run['name'],[],[],'fill_missing','V1',repository=repo)
    receipt=repo.run['draft_json']['row_previews'][response['preview']['id']]
    assert 'skip_reason_code' not in json.dumps(receipt,ensure_ascii=False)


@pytest.mark.parametrize(
    ('elapsed_ms','result_count'),
    [
        (float('inf'), float('nan')),
        (float('nan'), 'Infinity'),
        ('Infinity', 'not-a-number'),
    ],
)
def test_progress_merge_ignores_non_dict_rows_and_invalid_numbers(
    elapsed_ms,result_count,
):
    sources=[{'source_id':'DOC','source_kind':'approval_attachment'}]
    progress=[
        None,
        'not-a-progress-row',
        42,
        {
            'source_id':'DOC','status':'SKIPPED','read_status':'SKIPPED',
            'skip_reason_code':'PARSE_TIMEOUT','skip_reason_text':'raw',
            'elapsed_ms':elapsed_ms,'result_count':result_count,
        },
    ]

    merged=service._sources_with_progress(sources,progress)

    assert merged[0]['read_status']=='SKIPPED'
    assert merged[0]['skip_reason_text']=='资料文件解析超时。'
    assert merged[0]['elapsed_ms']==0
    assert merged[0]['result_count']==0


@pytest.mark.parametrize('change',['fee','item','source','note'])
def test_changes_after_preview_block_all_writes(change):
    repo=Repo();preview=prepare(repo)
    if change=='fee':repo.fees=[{'name':'F','logical_fee_key':'international_express_fee','amount':0,'is_active':1}]
    elif change=='item':repo.items[0]['gross_weight_kg']=0
    elif change=='source':repo.sources[0]['source_hash']='new'
    else:repo.context['clarification_revision']=1
    with pytest.raises(ValueError):confirm(repo,preview)
    assert not repo.writes


def test_fee_amendment_keeps_material_recognition_but_requires_new_selection_preview():
    repo=Repo();old=prepare(repo)
    repo.context['effective_source']['freight']['revision']='F2';repo.context['effective_source']['fingerprint']='SOURCE2'
    repo.context['version_modified']='VM2'
    catalog=service.review_catalog(repo,'B1',repo.run)
    assert catalog['rows'][0]['can_fill']
    with pytest.raises(ValueError,match='刷新预览'):confirm(repo,old)
    new=prepare(repo);assert new['id']!=old['id']
    assert confirm(repo,new)['ok']


def test_unknown_row_and_fee_ids_are_rejected():
    repo=Repo()
    for kwargs in ({'ids':['forged']},{'fees':['forged']}):
        with pytest.raises(ValueError,match='不属于'):prepare(repo,**kwargs)
    assert not repo.writes


def test_public_approval_fee_id_round_trips_into_locked_preview_without_leaking_process_id():
    repo = Repo()
    raw_process_id = 'P7BgLg53TRSLsYElgdX4Dg04891788440826'
    raw_fee_id = f'approval-fee:{raw_process_id}:1'
    repo.run['candidates_json'].append({
        'proposal_id': raw_fee_id,
        'proposal_type': 'fee_update',
        'process_instance_id': raw_process_id,
        'default_selected': True,
        'payload': {
            'logical_fee_key': 'international_sea_freight',
            'amount': '7756.2',
            'currency': 'RMB',
            'amount_status': 'ESTIMATED',
        },
    })

    catalog = service.review_catalog(repo, 'B1', repo.run)
    public_fee_id = catalog['fees'][0]['proposal_id']

    assert public_fee_id != raw_fee_id
    assert raw_process_id not in json.dumps(catalog, ensure_ascii=False)
    response = service.prepare(
        'B1', repo.run['name'], [], [public_fee_id],
        'update_selected', 'V1', repository=repo,
    )
    receipt = repo.run['draft_json']['row_previews'][response['preview']['id']]

    assert response['preview']['fees'][0]['proposal_id'] == public_fee_id
    assert receipt['selected_fee_ids'] == [raw_fee_id]
    assert raw_process_id not in json.dumps(response, ensure_ascii=False)


def test_existing_ready_draft_gets_fee_presentation_groups_without_reanalysis():
    repo = Repo()
    repo.run['candidates_json'].extend([
        {
            'proposal_id': 'approval-fee:LOG-1:1',
            'proposal_type': 'fee_update',
            'result_origin': 'SYSTEM',
            'default_selected': True,
            'confidence': .99,
            'source_refs': [{'source_id': 'DOC', 'process_instance_id': 'LOG-1'}],
            'payload': {
                'logical_fee_key': 'international_express_fee',
                'expense_category': '国际快递费',
                'amount': '2385.37467',
                'currency': 'RMB',
                'amount_status': 'ESTIMATED',
                'allocation_basis': 'chargeable_weight',
                'scope_type': 'ALL_ITEMS',
            },
        },
        {
            'proposal_id': 'PROP-002',
            'proposal_type': 'fee_update',
            'result_origin': 'AI',
            'confidence': .99,
            'source_refs': [{'source_id': 'DOC', 'process_instance_id': 'LOG-1'}],
            'payload': {
                'logical_fee_key': 'international_express_fee',
                'expense_category': '国际快递费',
                'amount': '2385.374670',
                'currency': 'rmb',
                'amount_status': 'estimated',
                'allocation_basis': 'CHARGEABLE_WEIGHT',
                'scope_type': 'all_items',
            },
        },
    ])

    public_catalog = service.review_catalog(repo, 'B1', repo.run)

    assert repo.run['status'] == 'READY'
    assert len(public_catalog['fees']) == 2
    assert len({fee['presentation_group_id'] for fee in public_catalog['fees']}) == 1
    assert sum(bool(fee.get('presentation_equivalent_candidate_ids'))
               for fee in public_catalog['fees']) == 1


def test_ordinary_ai_selection_rejects_whole_table_replacement():
    repo = Repo()
    catalog = service.review_catalog(repo, 'B1', repo.run)
    selected = [row['row_id'] for row in catalog['rows'] if row['origin'] == 'source']

    with pytest.raises(ValueError, match='独立的整源采纳'):
        service.prepare('B1', repo.run['name'], selected, [], 'replace_all', 'V1', repository=repo)


def test_unrelated_unverified_merged_amount_group_does_not_block_physical_fill():
    repo = Repo()
    repo.run['draft_json']['merged_amount_groups'] = [{
        'source_id': 'OLD-WORKBOOK-SHEET',
        'sheet_name': '历史装箱单',
        'member_item_names': ['OTHER-ITEM'],
        'unit_price_range': 'Z2:Z7',
        'total_amount_range': 'AA2:AA7',
        'control_total': 60400,
        'computed_total': 59800,
        'status': 'needs_allocation',
    }]

    preview = prepare(repo)

    assert preview['merged_amount_blocking'] is False
    assert preview['can_apply'] is True
    assert not any(
        isinstance(reason, dict) and reason.get('code') == 'MERGED_AMOUNT_ALLOCATION_REQUIRED'
        for reason in preview['unresolved']
    )
    assert confirm(repo, preview)['ok'] is True
    assert len(repo.writes) == 1


def _trusted_shipment_value_proposal(item, source_id, sheet):
    refs = [{'source_id': source_id, 'sheet_name': sheet, 'row': 2}]
    return {'proposal_id': 'VALUE', 'proposal_type': 'logistics_reconcile',
            'default_selected': True, 'source_refs': refs,
            'payload': {'rows': [{**item, '_review_origin': 'source',
                'extra_json': {'shipment_valuation': {
                    'amount_rmb': '100', 'quantity': item['actual_shipped_qty'], 'uom': item['shipped_uom'],
                    'currency': 'RMB', 'source_refs': refs}}}]}}


def test_unverified_group_for_same_item_but_other_source_does_not_block_value_fill():
    repo = Repo()
    repo.sources.append({
        'source_id':'CURRENT-SHEET','source_kind':'approval_attachment',
        'approval_role':'international_logistics','source_label':'本次清单',
    })
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    repo.run['candidates_json'] = [_trusted_shipment_value_proposal(repo.items[0], 'CURRENT-SHEET', '本次清单')]
    repo.run['draft_json']['merged_amount_groups'] = [{
        'source_id': 'OLD-SHEET',
        'sheet_name': '历史清单',
        'member_item_names': ['I1'],
        'status': 'needs_allocation',
    }]

    preview = prepare(repo)

    assert preview['merged_amount_blocking'] is False
    assert preview['can_apply'] is True


def test_relevant_unverified_merged_amount_group_blocks_confirmation_without_writing():
    repo = Repo()
    repo.sources.append({
        'source_id':'PACKING-SHEET','source_kind':'approval_attachment',
        'approval_role':'international_logistics','source_label':'装箱单',
    })
    repo.items=[
        {**repo.items[0],'stable_line_key':'LINE-1','row_no':1},
        {'name':'I2','material_code':'SKU2','stable_line_key':'LINE-2','row_no':2,
         'quantity':1,'actual_shipped_qty':1,'unit':'件','shipped_uom':'件'},
    ]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=service.material_fingerprint(
        repo.items,repo.sources,repo.context)
    value_proposal = _trusted_shipment_value_proposal(repo.items[0], 'PACKING-SHEET', '装箱单')
    value_proposal['payload']['rows'].append({**repo.items[1], '_review_origin': 'source'})
    repo.run['candidates_json'] = [value_proposal]
    repo.run['draft_json']['merged_amount_groups'] = [{
        'source_id': 'PACKING-SHEET',
        'sheet_name': '装箱单',
        'member_item_names': ['I1'],
        'unit_price_range': 'Z2:Z7',
        'total_amount_range': 'AA2:AA7',
        'control_total': 60400,
        'computed_total': 59800,
        'status': 'needs_allocation',
    }]
    repo.run['draft_json']['packing_group_candidates'] = [{
        'candidate_id': 'PACKING-GROUP',
        'member_keys': ['LINE-1', 'LINE-2'],
        'default_selected': True,
        'can_apply': True,
        'evidence': [{'kind': 'trusted_comment_text'}],
    }]

    preview = prepare(repo)

    assert preview['merged_amount_blocking'] is True
    assert preview['can_apply'] is False
    assert preview['blocking_merged_amount_groups'][0]['source_id'] == 'PACKING-SHEET'
    assert preview['unresolved'][0]['code'] == 'MERGED_AMOUNT_ALLOCATION_REQUIRED'
    with pytest.raises(ValueError, match='人工分摊'):
        confirm(repo, preview)
    assert not repo.writes


def test_material_apply_failure_rolls_back_entire_selection():
    repo=Repo();preview=prepare(repo)
    def fail(*args):repo.writes.append('partial');raise RuntimeError('persist failure')
    repo.apply_row_selection=fail
    with pytest.raises(RuntimeError):confirm(repo,preview)
    assert repo.rolled_back and not repo.writes


def test_archived_dependency_change_blocks_preview_confirmation_even_with_same_manifest_hash():
    repo=Repo();archive={'hash':'first'}
    repo.capture_row_dependencies=lambda *args:[deepcopy(archive)]
    def validate(batch,snapshot,**kwargs):
        if snapshot!=[archive]:raise ValueError('来源内容已更新')
    repo.assert_row_dependencies=validate
    preview=prepare(repo);archive['hash']='second'
    with pytest.raises(ValueError,match='来源内容已更新'):confirm(repo,preview)
    assert not repo.writes


def test_prepare_reuses_successful_analysis_dependencies_without_recapturing_failed_optional_sources():
    repo = Repo()
    successful = [{"kind": "attachment", "attachment_id": "READY", "fingerprint": "SEALED"}]
    repo.run["draft_json"]["review_input"] = {"source_dependencies": deepcopy(successful)}
    checks = []
    repo.assert_row_dependencies = lambda batch, dependencies, **kwargs: checks.append(
        (deepcopy(dependencies), kwargs.get("lock", False))
    )
    repo.capture_row_dependencies = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("failed optional sources must not be recaptured")
    )

    preview = prepare(repo)

    assert checks
    assert all(dependencies == successful for dependencies, _locked in checks)
    assert any(locked for _dependencies, locked in checks)
    internal = repo.run["draft_json"]["row_previews"][preview["id"]]
    assert internal["dependencies"] == successful


def test_pending_source_confirmation_uses_estimate_dependency_check_only():
    repo=Repo();pending_adopted_scope(repo);checks=[]
    repo.capture_row_dependencies=lambda *args,**kwargs:[{'kind':'approval','source_id':'PENDING','fingerprint':'LOCKED'}]
    repo.assert_row_dependencies=lambda batch,dependencies,**kwargs:checks.append(kwargs.get('purpose','analysis'))
    repo.assert_adoption_dependencies=lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('审批中，仅供分析'))
    preview=prepare(repo)

    assert confirm(repo,preview)['ok']
    assert checks==['analysis','estimate']
    assert len(repo.writes)==1


def test_pending_source_estimated_fee_confirmation_uses_estimate_dependency_check():
    repo=Repo();pending_adopted_scope(repo)
    repo.run['candidates_json'].append({'proposal_id':'F1','proposal_type':'fee_update',
        'default_selected':True,'payload':{'logical_fee_key':'international_express_fee',
            'amount':'100','currency':'RMB','amount_status':'ESTIMATED'}})
    repo.capture_row_dependencies=lambda *args,**kwargs:[{'kind':'approval','source_id':'PENDING','fingerprint':'LOCKED'}]
    checks=[]
    repo.assert_row_dependencies=lambda batch,dependencies,**kwargs:checks.append(kwargs.get('purpose','analysis'))
    repo.assert_adoption_dependencies=lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('审批中，费用不能采用'))
    preview=prepare(repo,fees=['F1'])

    assert confirm(repo,preview)['ok']
    assert checks==['analysis','estimate']
    assert len(repo.writes)==1


def test_active_source_actual_fee_confirmation_rechecks_final_adoption_and_writes():
    repo=Repo();pending_adopted_scope(repo)
    repo.run['candidates_json'].append({'proposal_id':'F1','proposal_type':'fee_update',
        'default_selected':True,'payload':{'logical_fee_key':'international_express_fee',
            'amount':'100','currency':'RMB','amount_status':'ACTUAL'}})
    repo.capture_row_dependencies=lambda *args,**kwargs:[{'kind':'approval','source_id':'PENDING','fingerprint':'LOCKED'}]
    checks=[]
    repo.assert_row_dependencies=lambda *args,**kwargs:None
    repo.assert_adoption_dependencies=lambda *args,**kwargs:checks.append('adoption')
    preview=prepare(repo,fees=['F1'])

    assert confirm(repo,preview)['ok']
    assert checks == ['adoption']
    assert len(repo.writes) == 1


def test_source_becoming_invalid_before_actual_fee_confirmation_blocks_without_write():
    repo=Repo();pending_adopted_scope(repo)
    repo.run['candidates_json'].append({'proposal_id':'F1','proposal_type':'fee_update',
        'default_selected':True,'payload':{'logical_fee_key':'international_express_fee',
            'amount':'100','currency':'RMB','amount_status':'ACTUAL'}})
    repo.capture_row_dependencies=lambda *args,**kwargs:[{'kind':'approval','source_id':'PENDING','fingerprint':'LOCKED'}]
    invalid={'value':False}
    repo.assert_row_dependencies=lambda *args,**kwargs:None
    def recheck(*args,**kwargs):
        if invalid['value']:
            raise ValueError('审批已拒绝或撤销')
    repo.assert_adoption_dependencies=recheck
    preview=prepare(repo,fees=['F1'])
    invalid['value']=True

    with pytest.raises(ValueError,match='审批已拒绝或撤销'):
        confirm(repo,preview)
    assert not repo.writes


def test_run_dependency_baseline_blocks_reusing_analysis_after_document_changes():
    repo=Repo();repo.run['draft_json']['review_input']={'source_dependencies':[{'hash':'first'}]}
    def validate(*args,**kwargs):raise ValueError('来源内容已更新')
    repo.assert_row_dependencies=validate
    with pytest.raises(ValueError,match='来源内容已更新'):prepare(repo)


def test_legacy_fee_only_proposal_cannot_bypass_controlled_selection_preview():
    repo=ContextRepository();repo.supports_row_selection=True
    ai.start_source_ai_review('B1','V1',repository=repo,enqueue=lambda _:None)
    repo.run.update(status='READY',candidates_json=[{'proposal_id':'F1','proposal_type':'fee_update',
        'payload':{'logical_fee_key':'international_express_fee','amount':100,'currency':'RMB'}}])
    with pytest.raises(ValueError,match='预览'):
        ai.apply_source_ai_review('B1',repo.run['name'],['F1'],{},'TOKEN','M1',repository=repo)
    assert not repo.writes
