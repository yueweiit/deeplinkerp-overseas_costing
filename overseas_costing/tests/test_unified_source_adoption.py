import json
from copy import deepcopy
import pytest

from overseas_costing.tests.test_settlement_writer import setup
from overseas_costing.tests.test_settlement_document_writer import document
from overseas_costing.tests.test_logistics_settlement import source, ingest, table_row
from overseas_costing.services.logistics_settlement.writer import apply_binding
from overseas_costing.services.material_input_service import present_material_row


@pytest.mark.parametrize('change', [{'approved': False}, {'invalid': True}, {'currency': 'EUR'}])
def test_pending_source_switch_creates_one_draft_and_excludes_old_values(setup, change):
    s,l,b,v,i,r,binding=setup
    expense=s.get('source',binding['expense_id'])
    s.put('source',{'id':expense['id'], 'data':json.dumps({**expense, **change})})
    l.put('version',v['name'],{'status':'Confirmed'})
    l.put('batch',b['name'],{'confirm_status':'Confirmed'})
    before=deepcopy(l.get('item',i['name']))
    first=apply_binding(s,l,binding['id'],'u')
    current=l.get('batch',b['name'])['current_version']
    assert current!=v['name']
    assert l.get('item',i['name'])==before
    row=present_material_row(l.rows('item',version=current)[0])
    assert row['effective_shipping_quantity']==''
    assert row.get('gross_weight_kg') in (None,'')
    assert row['shipment_value_rmb'] is None
    assert not any(x.get('is_enabled') for x in l.rows('rule',version=current))
    apply_binding(s,l,binding['id'],'u')
    assert len(l.rows('version'))==2


def test_expense_packing_overlay_replaces_current_weights_preserves_raw_history(setup):
    s,l,b,v,i,r,binding=setup
    expense=s.get('source',binding['expense_id'])
    expense.update(documents=[document()],packing_hash='expense-doc')
    s.put('source',{'id':expense['id'],'data':json.dumps(expense)})
    apply_binding(s,l,binding['id'],'u')
    raw=l.get('item',i['name']); row=present_material_row(raw)
    assert raw['gross_weight_kg']==4 and raw['volume_m3']==3
    assert row['gross_weight_kg']=='8' and row['volume_m3']=='2'
    assert row['effective_shipping_quantity']=='4'
    meta=json.loads(raw['extra_json'])
    assert meta['settlement_physical']['source_snapshot']==expense['snapshot']
    assert meta['effective_logistics_source']['root_kind']=='expense'


def test_no_expense_packing_never_reuses_raw_weights(setup):
    s,l,b,v,i,r,binding=setup
    apply_binding(s,l,binding['id'],'u')
    row=present_material_row(l.get('item',i['name']))
    assert row.get('gross_weight_kg') in (None,'')
    assert row.get('volume_m3') in (None,'')


def test_partial_source_retains_history_without_adopting_old_sku(setup):
    s,l,b,v,i,r,binding=setup
    expense=s.get('source',binding['expense_id']); expense['goods_complete']=False
    s.put('source',{'id':expense['id'],'data':json.dumps(expense)})
    apply_binding(s,l,binding['id'],'u')
    assert l.get('item',i['name']) is not None
    row=present_material_row(l.get('item',i['name']))
    assert row['effective_shipping_quantity']=='' and row['shipment_value_rmb'] is None


@pytest.mark.parametrize('price,expected,error', [('0','0.000000',False),('12','48.000000',False),('unknown',None,True)])
def test_expense_merchandise_price_is_explicit_and_never_ambiguous_fallback(setup,price,expected,error):
    s,l,b,v,i,r,binding=setup
    data=source('E',amount='100'); data['updated_at']='2026-09-10T01:00:00+00:00'
    goods=table_row('a',quantity='4')
    goods['rowValue'] += [{'name':'商品单价','value':price},{'name':'商品币种','value':'RMB'},{'name':'商品计价单位','value':'件'}]
    data['raw_payload']['formComponentValues'].append({'name':'货物明细','componentType':'TableField','value':[goods]})
    parsed=ingest(s,data)
    assert parsed['goods'][0]['merchandise_price']['present'] is True
    apply_binding(s,l,binding['id'],'u')
    row=present_material_row(l.get('item',i['name']))
    assert row['shipment_value_rmb']==expected
    assert bool(row['shipment_valuation']['error']) is error


def test_expense_freight_rate_not_merchandise_price():
    from overseas_costing.services.logistics_settlement.model import parse_source
    data=source('E'); goods=table_row('a',quantity='4')
    goods['rowValue'] += [{'name':'物流计费单价','value':'90'},{'name':'计费单位','value':'kg'}]
    data['raw_payload']['formComponentValues'].append({'name':'货物明细','componentType':'TableField','value':[goods]})
    assert not parse_source(data,logistics_codes={'L'})['goods'][0].get('merchandise_price',{}).get('present')


def test_real_bilingual_expense_total_and_cargo_unit_price_aliases():
    from overseas_costing.services.logistics_settlement.model import parse_source
    data=source('E'); fields=data['raw_payload']['formComponentValues']
    fields[:]=[f for f in fields if f['name'] != '总金额Monto Total']
    fields.append({'name':'金额importe','componentType':'MoneyField','value':'13068'})
    row=table_row('a',quantity='4')
    row['rowValue'] += [{'name':'单价Precio unitario','value':'0'}, {'name':'币种Moneda','value':'RMB'}]
    fields.append({'name':'货物明细','componentType':'TableField','value':[row]})
    parsed=parse_source(data,logistics_codes={'L'})
    assert parsed['amount']=='13068'
    assert parsed['goods'][0]['merchandise_price']['price']=='0'


def test_expense_document_ack_survives_calculation_and_exact_replay(setup):
    from overseas_costing.services.logistics_settlement.writer import resolve_item_checks,item_review
    from overseas_costing.services.logistics_settlement.document_writer import sync_source_documents
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    s,l,b,v,i,r,binding=setup
    expense=s.get('source',binding['expense_id']); doc=document()
    doc['tables'][0]['rows'][0]['rowValue'] += [{'name':'装箱数量','value':'2'}]
    expense.update(documents=[doc],packing_hash='expense-doc')
    s.put('source',{'id':expense['id'],'data':json.dumps(expense)})
    apply_binding(s,l,binding['id'],'u')
    current=s.get('binding',binding['id'])
    row=l.get('item',i['name'])
    resolve_item_checks(s,l,binding['id'],current['revision'],[{'item_name':i['name'],
        'expected_item_hash':item_review(row)['revision'],'packing_confirmed':True}], '核对采购与装箱数量差异','u')
    l.put('item',i['name'],{'weight_ratio':'1','total_cost_rmb':'40','modified':'2026-09-10 14:00:00'})
    context=resolve_source_context(b['name'],v['name'],store=s,ledger=l)
    repeated=sync_source_documents(s,l,expense,b['name'],'sync',source_context=context)
    assert not repeated['changed'] and not repeated['blocking']


def test_missing_current_physical_cannot_be_acknowledged_as_complete(setup):
    from overseas_costing.services.logistics_settlement.writer import resolve_item_checks,item_review
    s,l,b,v,i,r,binding=setup
    applied=apply_binding(s,l,binding['id'],'u')
    with pytest.raises(ValueError,match='尚无可采用'):
        resolve_item_checks(s,l,binding['id'],applied['revision'],[{'item_name':i['name'],
            'expected_item_hash':item_review(l.get('item',i['name']))['revision'],'packing_confirmed':True}], '核对','u')


def test_current_expense_zero_suppresses_all_old_fee_scopes_and_item_pools(setup):
    from decimal import Decimal
    from overseas_costing.services.cost_preview_service import build_saved_cost_data
    from overseas_costing.services.fee_service import compose_fee_worklist_rows
    from overseas_costing.services.effective_source_values import item_source_context
    s,l,b,v,i,r,binding=setup
    row=source('E',amount='0');row['updated_at']='2026-09-10T01:00:00+00:00'
    row['raw_payload']['formComponentValues'].append({'name':'货物明细','componentType':'TableField','value':[table_row('a',quantity='4')]})
    ingest(s,row)
    l.create('rule',{'batch':b['name'],'version':v['name'],'rule_code':'customs_clearance_fee','amount':20,'currency':'RMB','is_enabled':1,'is_active':1})
    l.create('rule',{'batch':b['name'],'version':v['name'],'rule_code':'destination_delivery','amount':7,'currency':'RMB','is_enabled':1,'is_active':1})
    l.put('item',i['name'],{'china_misc_rmb':80})
    apply_binding(s,l,binding['id'],'u')
    items=l.rows('item',version=v['name']);ctx=item_source_context(items[0])
    fees=compose_fee_worklist_rows(l.rows('rule',version=v['name']),'SEA',source_context=ctx)
    assert len(fees)==1 and fees[0]['amount']=='0'
    result=build_saved_cost_data(items,fees,{'fx_rmb_to_mxn':2.5},'SEA')
    assert Decimal(result['summary']['total_cost_rmb'])==40
    assert Decimal(result['summary_snapshot']['total_gross_weight_kg'])==0
    assert Decimal(result['summary_snapshot']['total_volume_m3'])==0


def test_local_policy_migration_preserves_binding_and_has_no_history_initialization(setup):
    from overseas_costing.services.logistics_settlement.policy_migration import register,run_step
    s,l,b,v,i,r,binding=setup
    before=s.count('source')
    job=register(s,l)
    assert job['binding_ids']==[binding['id']]
    done=run_step(s,l,limit=1)
    assert done['status']=='completed' and done['cursor']==1 and not done['failed']
    assert s.count('source')==before and s.count('job')==0
    assert s.get('binding',binding['id'])['expense_id']==binding['expense_id']
    assert present_material_row(l.get('item',i['name']))['gross_weight_kg'] is None
    assert register(s,l)['cursor']==1
    assert run_step(s,l)==s.get('state',job['id'])


def test_queued_binding_query_projection_cannot_show_old_physical_or_quantity(setup):
    from overseas_costing.services.effective_source_values import project_source_values
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    s,l,b,v,i,r,binding=setup
    l.put('batch',b['name'],{'edit_lock_expires_at':'2099-01-01 00:00:00','edit_lock_owner':'u'})
    assert apply_binding(s,l,binding['id'],'sync')['application_status']=='queued'
    ctx=resolve_source_context(b['name'],v['name'],store=s,ledger=l)
    projected=present_material_row(project_source_values(l.get('item',i['name']),ctx))
    assert projected['effective_shipping_quantity']=='' and projected['gross_weight_kg'] is None
    assert l.get('item',i['name'])['gross_weight_kg']==4


def test_pending_projection_excludes_every_legacy_tax_pool_and_saved_output():
    from overseas_costing.services.effective_source_values import project_source_values
    from overseas_costing.services.cost_preview_service import LEGACY_CUSTOMS_SERVICE_FIELDS,LEGACY_TAX_COMPONENT_FIELDS
    fields=(*LEGACY_CUSTOMS_SERVICE_FIELDS,*LEGACY_TAX_COMPONENT_FIELDS,'mexico_customs_rmb','mexico_customs_mxn',
            'mexico_customs_usd','import_tax_total','total_cost_rmb')
    raw={**dict.fromkeys(fields,40),'derived_json':'{"total_cost_rmb":999}'}
    result=project_source_values(raw,{'root_kind':'expense','available':False,'fingerprint':'NEW'})
    assert all(result[key] is None for key in fields)
    assert result['derived_json']=='{}' and raw['total_cost_rmb']==40


def test_real_workbench_readiness_checks_new_source_even_before_background_adoption(monkeypatch):
    from overseas_costing.tests.test_workbench_review_readiness import batch_context,install_rows
    from overseas_costing.services import workbench_service,effective_source_values
    context=batch_context('SOURCE-CHANGED')
    install_rows(monkeypatch,[context])
    monkeypatch.setattr(effective_source_values,'batch_source_context',lambda *a,**k:{
        'root_kind':'expense','available':True,'approved':False,'fingerprint':'NEW'})
    result=workbench_service._load_review_readiness([context['batch']])['SOURCE-CHANGED']
    assert result['review_state']=='processing' and not result['result_is_current']
    assert any(row['code']=='SOURCE_ADOPTION_PENDING' for row in result['review_blockers'])


def test_bound_generic_fee_save_fails_before_silently_ignored_write(monkeypatch):
    from overseas_costing.services import fee_service,effective_source_values
    monkeypatch.setattr(fee_service,'frappe',object())
    monkeypatch.setattr(effective_source_values,'batch_source_context',lambda *a,**k:{'root_kind':'expense'})
    with pytest.raises(ValueError,match='完整费用明细'):
        fee_service.save_fee('B','V',{'logical_fee_key':'misc_expense','amount':'10','currency':'RMB','amount_status':'ACTUAL'})


@pytest.mark.parametrize('historical_status', ['拒绝', 'TERMINATED', 'COMPLETED'])
def test_valid_expense_is_not_hidden_or_blocked_by_historical_logistics_status(monkeypatch, historical_status):
    from overseas_costing.services import batch_service,effective_source_values
    context={'root_kind':'expense','approved':True,'available':True,'invalid':False}
    monkeypatch.setattr(effective_source_values,'batch_source_context',lambda *a,**k:context)
    batch={'name':'B','current_version':'V','source_approval_status':historical_status}
    assert not batch_service._build_invalid_business_state(batch,[])['invalid']
    context['approved']=False
    assert batch_service._build_invalid_business_state(batch,[])['scope']=='logistics_expense'


@pytest.mark.parametrize('status,result,invalid', [
    ('COMPLETED', 'refuse', True),
    ('TERMINATED', '', True),
    ('COMPLETED', 'agree', False),
])
def test_logistics_source_status_uses_the_current_approval_result(monkeypatch, status, result, invalid):
    from overseas_costing.services import batch_service, effective_source_values
    from overseas_costing.services.effective_logistics_source import context_for_source
    from overseas_costing.services.logistics_settlement.model import parse_source

    raw = source('L', 'logistics')
    raw.update(status=status, result=result)
    current = context_for_source(parse_source(raw, logistics_codes={'logistics'}))
    monkeypatch.setattr(effective_source_values, 'batch_source_context', lambda *args, **kwargs: current)
    summary = batch_service._build_batch_source_status({
        'name': 'B', 'current_version': 'V', 'source_type': 'oa_logistics',
        'source_approval_status': 'COMPLETED',
    })
    assert summary['invalid_business'] is invalid
    assert summary['invalid_business_scope'] == ('source_approval' if invalid else '')
    if invalid:
        assert '业务已拒绝/撤销/终止' in summary['invalid_business_reason']


def test_reviewed_packing_does_not_duplicate_the_same_cached_document_row(setup):
    from overseas_costing.services.logistics_settlement.reviewed_cargo import confirm_reviewed_cargo
    from overseas_costing.services.effective_logistics_source import load_source_bundle
    s,l,b,v,i,r,binding=setup
    expense=s.get('source',binding['expense_id']);expense['documents']=[document()]
    s.put('source',{'id':expense['id'],'data':json.dumps(expense)})
    apply_binding(s,l,binding['id'],'u')
    bundle=load_source_bundle(b['name'],store=s,ledger=l)
    result=confirm_reviewed_cargo(s,l,b['name'],bundle['context'],[{
        'source_row':2,'material_code':'A','quantity':'4','unit':'件','actual_shipped_qty':'4',
        'gross_weight_kg':'8.0','volume_m3':'2'}],{'source_context':bundle['context'],'source_id':'doc1',
        'source_hash':'a'*64,'sheet':'装箱单','full_table':True,'row_count':1,'table_kind':'packing'},True,'u')
    assert result['ok']
    assert not s.find('document_sync',source_id=expense['id'])[0]['blocking']
