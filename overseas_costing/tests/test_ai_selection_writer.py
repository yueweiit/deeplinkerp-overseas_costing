from copy import deepcopy
from decimal import Decimal
import json
from overseas_costing.tests.test_freight_lines import setup_cost
from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services.material_ai_selection_writer import write_rows, _confirm_and_reconstruct_payment_match
from overseas_costing.services.effective_logistics_source import load_source_bundle
from overseas_costing.services.logistics_settlement.model import dumps
from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups


def valuation_selection(items, ctx, version, batch):
    target = items[0]
    row = {
        'name': 'draft-valued',
        'material_code': target['material_code'],
        'product_name': target['product_name'],
        'actual_shipped_qty': '2400',
        'unit': '个',
        '_review_origin': 'source',
        'extra_json': dumps({'shipment_valuation': {
            'amount_rmb': '10560',
            'unit_price': '4.40',
            'currency': 'RMB',
            'quantity': '2400',
            'uom': '个',
            'status': 'confirmed_source',
            'source': {'kind': 'packing_attachment', 'file_id': 'FILE-PACKING-1'},
        }}),
    }
    proposal = {'proposal_id': 'VALUED', 'proposal_type': 'logistics_reconcile',
        'default_selected': True, 'payload': {'rows': [row]}}
    catalog = rows.catalog(items, [proposal], [], ctx, run_id='R-VALUED')
    candidate = next(value for value in catalog['rows'] if value['origin'] == 'source')
    plan = rows.project(items, catalog, [candidate['row_id']], [], 'update_selected')
    return catalog, {**plan, 'id': 'valued-preview', 'revision': 'valued-revision',
        'batch': batch, 'version': version, 'run_id': 'R-VALUED',
        'source_context': ctx, 'sources': []}


def selection(items,ctx,version,batch,mode='replace_all'):
    row={'name':'draft-A','material_code':'AA100','product_name':'Oppo','actual_shipped_qty':'2','unit':'件','_review_origin':'source'}
    proposal={'proposal_id':'P','proposal_type':'logistics_reconcile','default_selected':True,'payload':{'rows':[row]}}
    cat=rows.catalog(items,[proposal],[],ctx,run_id='R')
    plan=rows.project(items,cat,[cat['rows'][0]['row_id']],[],mode)
    return {**plan,'id':'preview','revision':'revision','batch':batch,'version':version,'run_id':'R','source_context':ctx,'sources':[]}


def test_replace_keeps_old_table_and_invalidates_directed_costs_without_reassigning():
    store,ledger,b,v,i,ls,e=setup_cost()
    removed=ledger.create('item',{'batch':b['name'],'version':v['name'],'material_code':'BB100','product_name':'other','unit':'件','quantity':1})
    ledger.create('component',{'batch':b['name'],'version':v['name'],'item':removed['name'],'amount':20,'is_active':1})
    old=ledger.rows('item',batch=b['name'],version=v['name']);ctx=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger)['context']
    p=selection(old,ctx,v['name'],b['name']);new=write_rows(store,ledger,p,ctx)
    assert new!=v['name'] and ledger.rows('item',batch=b['name'],version=v['name'])==old
    current=ledger.rows('item',batch=b['name'],version=new)
    assert len(current)==1 and current[0]['gross_weight_kg']==0
    assert 'gross_weight_kg' in current[0]['extra_json']
    component=ledger.rows('component',version=new)[0]
    assert not component['is_active'] and not component['item']
    assert len(ledger.rows('rule',version=new))==2
    bundle=load_source_bundle(b['name'],new,store=store,ledger=ledger)
    assert len(bundle['source']['goods'])==1 and 'other' not in dumps(bundle['source']['goods'])
    assert bundle['context']['packing']['selected_source']['row_ids']==p['selected_row_ids']


def test_fill_retains_unselected_existing_rows_byte_for_byte():
    store,ledger,b,v,i,ls,e=setup_cost()
    before=deepcopy(ledger.get('item',i['name']))
    rows0=ledger.rows('item',version=v['name']);ctx=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger)['context']
    p=selection(rows0,ctx,v['name'],b['name'],'fill_missing')
    # Existing row only lacks actual quantity; its real weight and volume must survive.
    new=write_rows(store,ledger,p,ctx)
    assert new==v['name']
    current=ledger.get('item',i['name'])
    assert current['gross_weight_kg']==before['gross_weight_kg'] and current['volume_m3']==before['volume_m3']


def test_update_selected_persists_one_row_and_keeps_all_other_rows():
    store,ledger,b,v,i,ls,e=setup_cost()
    for index in range(2,9):
        ledger.create('item',{'batch':b['name'],'version':v['name'],'material_code':f'SKU-{index}',
            'product_name':f'Product {index}','unit':'件','quantity':index,'gross_weight_kg':index})
    before={row['name']:deepcopy(row) for row in ledger.rows('item',version=v['name'])}
    ctx=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger)['context']
    p=selection(list(before.values()),ctx,v['name'],b['name'],'update_selected')

    new=write_rows(store,ledger,p,ctx)

    after={row['name']:row for row in ledger.rows('item',version=v['name'])}
    assert new==v['name'] and len(after)==8
    assert after[i['name']]['actual_shipped_qty']=='2'
    for name,row in before.items():
        if name!=i['name']:
            assert after[name]==row


def test_confirmed_ai_shared_box_is_merged_in_material_packing_data_and_counted_once():
    store,ledger,batch,version,first,*_=setup_cost()
    ledger.put('item',first['name'],{
        'stable_line_key':'L1','row_no':1,'actual_shipped_qty':1,
        'goods_value':12000,'package_count':0,'gross_weight_kg':0,'volume_m3':0,
    })
    ledger.create('item',{
        'batch':batch['name'],'version':version['name'],'row_no':2,
        'stable_line_key':'L2','material_code':'MWV101145','product_name':'薇武士IP17 PRO MAX',
        'unit':'套','quantity':1,'actual_shipped_qty':1,'goods_value':12000,
        'package_count':0,'gross_weight_kg':0,'volume_m3':0,
    })
    current=ledger.rows('item',version=version['name'])
    context=load_source_bundle(batch['name'],version['name'],store=store,ledger=ledger)['context']
    preview={
        'id':'AI-SHARED-BOX','revision':'AI-SHARED-BOX-R1','run_id':'RUN',
        'batch':batch['name'],'version':version['name'],'mode':'update_selected',
        'rows':deepcopy(current),'changes':[],'selected_row_ids':[],
        'selected_field_choices':{},'sources':[],'source_context':context,
        'packing_group_candidates':[{
            'candidate_id':'COMMENT-ONE-BOX','member_keys':['L1','L2'],
            'gross_weight_kg':'42.05','volume_m3':'0.01518','package_count':'1',
            'source_fingerprint':'COMMENT-HASH','creation_method':'trusted_comment_text',
            'default_selected':True,'can_apply':True,
            'evidence':[{'kind':'trusted_comment_text','confidence':1}],
        }],
    }

    written_version=write_rows(store,ledger,preview,context)
    saved_version=ledger.get('version',written_version)
    saved_items=ledger.rows('item',version=written_version)
    groups=groups_from_version(saved_version)
    material_packing=project_packing_groups(saved_items,groups)

    assert written_version==version['name']
    assert len(groups)==1
    assert groups[0]['member_keys']==['L1','L2']
    assert groups[0]['package_count']=='1'
    assert [row['packing_group_id'] for row in material_packing['items']]==[
        'COMMENT-ONE-BOX','COMMENT-ONE-BOX']
    assert [row['package_count'] for row in material_packing['items']]==['1','0']
    assert sum(Decimal(str(row['gross_weight_kg'])) for row in material_packing['items'])==Decimal('42.05')
    assert sum(Decimal(str(row['volume_m3'])) for row in material_packing['items'])==Decimal('0.01518')


def test_original_source_reanalysis_clears_legacy_selected_row_scope_after_update():
    store,ledger,b,v,i,ls,e=setup_cost()
    ledger.put('version',v['name'],{'extra_json':dumps({'ai_row_adoption':{
        'id':'OLD','revision':'OLD-R','selected_row_ids':['ONE'],'goods':[]}})})
    ctx=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger,
                           apply_ai_row_adoption=False)['context']
    p=selection(ledger.rows('item',version=v['name']),ctx,v['name'],b['name'],'update_selected')
    p['original_source_reanalysis']=True

    write_rows(store,ledger,p,ctx)

    assert 'ai_row_adoption' not in dumps(ledger.get('version',v['name']))


def test_separate_add_selected_mode_is_the_only_nonreplace_path_that_adds_a_row():
    store,ledger,b,v,i,ls,e=setup_cost()
    existing=ledger.rows('item',version=v['name'])
    proposal={'proposal_id':'NEW','proposal_type':'logistics_reconcile','default_selected':True,
        'payload':{'rows':[{'name':'draft-new','material_code':'SKU-NEW','product_name':'New',
            'actual_shipped_qty':3,'unit':'件','_review_origin':'source'}]}}
    catalog=rows.catalog(existing,[proposal],[],{},run_id='R')
    candidate=next(row for row in catalog['rows'] if row['origin']=='source')
    projected=rows.project(existing,catalog,[candidate['row_id']],[],'add_selected')
    projected.update(id='ADD',revision='ADD-R',batch=b['name'],version=v['name'],run_id='R',
        source_context={},sources=[])

    written=write_rows(store,ledger,projected,{})

    assert written==v['name']
    saved=ledger.rows('item',version=v['name'])
    assert len(saved)==2
    assert {row['material_code'] for row in saved}=={i['material_code'],'SKU-NEW'}


def test_update_soft_excludes_rows_outside_authoritative_logistics_scope():
    store,ledger,b,v,i,*_=setup_cost()
    outside=ledger.create('item',{
        'batch':b['name'],'version':v['name'],'row_no':2,
        'material_code':'PURCHASE-ONLY','product_name':'Purchase only',
        'unit':'件','quantity':1,'actual_shipped_qty':1,'is_excluded':0,
    })
    context=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger)['context']
    preview={
        'id':'SCOPE','revision':'SCOPE-R','run_id':'RUN',
        'batch':b['name'],'version':v['name'],'mode':'update_selected',
        'rows':[{**deepcopy(ledger.get('item',i['name'])),'_row_action':'retain'}],
        'changes':[],'selected_row_ids':['LOGISTICS-BASELINE'],
        'selected_field_choices':{},'sources':[],'source_context':context,
        'scope_excluded_item_names':[outside['name']],
    }

    written=write_rows(store,ledger,preview,context)

    assert written==v['name']
    excluded=ledger.get('item',outside['name'])
    assert excluded['is_excluded']==1
    assert excluded['excluded_by']=='system'
    assert '国际物流' in excluded['exclusion_reason']
    assert ledger.get('batch',b['name'])['item_count']==1


def test_frozen_version_write_is_rejected():
    import pytest
    store,ledger,b,v,i,ls,e=setup_cost();ctx=load_source_bundle(b['name'],v['name'],store=store,ledger=ledger)['context']
    p=selection(ledger.rows('item',version=v['name']),ctx,v['name'],b['name'])
    ledger.put('version',v['name'],{'status':'Confirmed'})
    with pytest.raises(ValueError,match='冻结'):write_rows(store,ledger,p,ctx)


def test_pending_payment_match_is_confirmed_before_locked_preview_is_recomputed(monkeypatch):
    from overseas_costing.services import material_ai_payment_match, material_ai_selection_service

    events=[]
    preview={'batch':'B1','payment_match_candidate':{
        'candidate_id':'FC-1','revision':'FR-1','version':'V1'}}
    refreshed={**preview,'recomputed':True}
    relation={'policy':'material-ai-payment-match-1','candidate_id':'FC-1'}
    monkeypatch.setattr(material_ai_payment_match,'confirm_preview_candidate',
        lambda store,ledger,batch,reference,actor,freight_mode: (
            events.append(('confirm',batch,reference,actor,freight_mode)) or relation))
    def reconstruct(repository,run,current,draft):
        assert events and events[0][0]=='confirm'
        events.append(('recompute',repository))
        return refreshed,{'locked':True}
    monkeypatch.setattr(material_ai_selection_service,'reconstruct_after_payment_match',reconstruct)

    result,context=_confirm_and_reconstruct_payment_match(
        object(),object(),{'name':'RUN'},preview,{}, {},'user',repository='repo',freight_mode=True)

    assert result is not refreshed and result['_payment_match_relation'] is relation
    assert {key:value for key,value in result.items()
            if key not in {'_payment_match_relation','_payment_match_relations'}}==refreshed
    assert context=={'locked':True}
    assert events==[
        ('confirm','B1',preview['payment_match_candidate'],'user',True),
        ('recompute','repo'),
    ]


def test_payment_relation_is_saved_only_after_row_metadata(monkeypatch):
    from overseas_costing.services import material_ai_payment_match
    from overseas_costing.services import material_ai_selection_writer as writer

    relation={'policy':'material-ai-payment-match-1','candidate_id':'FC-1'}
    preview={
        'batch':'B1','version':'V1','selected_row_ids':['ROW-1'],
        'selected_field_choices':{},'packing_group_candidates':[],
        '_payment_match_relation':relation,
    }
    events=[]
    monkeypatch.setattr(writer,'write_rows',lambda *_args:(events.append('write_rows') or 'V2'))
    monkeypatch.setattr(material_ai_payment_match,'persist_relation',
        lambda store,ledger,batch,version,current,actor: events.append(
            ('persist_relation',batch,version,current,actor)))

    version=writer._write_rows_and_payment_relation(
        object(),object(),preview,{},'user')

    assert version=='V2'
    assert events==[
        'write_rows',
        ('persist_relation','B1','V2',relation,'user'),
    ]


def test_multiple_selected_payment_relations_are_confirmed_and_persisted_atomically(monkeypatch):
    from overseas_costing.services import material_ai_payment_match, material_ai_selection_service
    from overseas_costing.services import material_ai_selection_writer as writer

    references = [
        {'candidate_id': 'FC-1', 'revision': 'FR-1', 'version': 'V1', 'user_selected': True},
        {'candidate_id': 'FC-2', 'revision': 'FR-2', 'version': 'V1', 'user_selected': True},
    ]
    preview = {'batch': 'B1', 'version': 'V1', 'payment_match_candidate': None,
               'payment_match_candidates': references}
    events = []
    monkeypatch.setattr(material_ai_payment_match, 'confirm_preview_candidate',
        lambda store, ledger, batch, reference, actor, freight_mode, user_selected=False: (
            events.append(('confirm', reference['candidate_id'], user_selected))
            or {'candidate_id': reference['candidate_id']}))
    monkeypatch.setattr(material_ai_selection_service, 'reconstruct_after_payment_match',
        lambda repository, run, current, draft: ({**current, 'recomputed': True}, {'locked': True}))

    rebuilt, _context = writer._confirm_and_reconstruct_payment_match(
        object(), object(), {}, preview, {}, {}, 'user', repository='repo', freight_mode=True)

    assert events == [('confirm', 'FC-1', True), ('confirm', 'FC-2', True)]
    assert [row['relation']['candidate_id'] for row in rebuilt['_payment_match_relations']] == ['FC-1', 'FC-2']


def test_structured_attachment_valuation_survives_row_review_and_is_persisted_without_purchase_link():
    store, ledger, batch, version, item, *_ = setup_cost()
    item['material_code'] = 'CW000191'
    item['product_name'] = '宠物项圈'
    ledger.put('item', item['name'], {
        'material_code': 'CW000191', 'product_name': '宠物项圈',
        'actual_shipped_qty': 2400, 'quantity': 2400, 'unit': '个',
        'shipped_uom': '个', 'unit_price': 0, 'goods_value': 0,
    })
    current = ledger.rows('item', version=version['name'])
    ctx = load_source_bundle(batch['name'], version['name'], store=store, ledger=ledger)['context']

    catalog, preview = valuation_selection(current, ctx, version['name'], batch['name'])
    candidate = next(row for row in catalog['rows'] if row['origin'] == 'source')

    assert candidate['values']['shipment_value_rmb'] == '10560'
    assert candidate['values']['shipment_valuation_status'] == 'confirmed_source'
    assert candidate['values']['shipment_valuation_ref']
    assert candidate['values']['shipment_value_source']['kind'] == 'packing_attachment'
    assert '_shipment_valuation' in candidate

    write_rows(store, ledger, preview, ctx)

    saved = ledger.get('item', item['name'])
    assert saved['goods_value'] == '10560'
    metadata = json.loads(saved['extra_json'])
    assert metadata['shipment_valuation']['amount_rmb'] == '10560'
    assert metadata['settlement_valuation']['amount_rmb'] == '10560'
    assert metadata['shipment_valuation']['source']['file_id'] == 'FILE-PACKING-1'
