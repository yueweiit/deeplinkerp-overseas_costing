"""Transactional row adoption, version preservation and scoped evidence."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from copy import deepcopy
from datetime import datetime

from .material_ai_row_selection import PHYSICAL, IDENTITY, FILL_FIELDS, missing
from .logistics_settlement.model import digest, dumps
from .logistics_settlement.application import row_meta
from .logistics_settlement.item_metadata import compact_cargo, original_value_snapshot, persist_item_meta
from .logistics_settlement.writer import clean_copy, clone_version_children, DERIVED_FIELDS
from .logistics_settlement.valuation import reconcile_replacement_value, value_final_cargo


def write_rows(store,ledger,preview,context):
    batch_name=preview['batch'];old_version=ledger.get('version',preview['version'],lock=True)
    batch=ledger.get('batch',batch_name,lock=True)
    if batch.get('current_version')!=old_version['name'] or old_version.get('status')!='Active' or batch.get('confirm_status')=='Confirmed' or batch.get('writeback_status')=='Success':
        raise ValueError(_('当前版本已变化或被冻结，不能覆盖；请刷新后重新预览。'))
    old_items=ledger.rows('item',batch=batch_name,version=old_version['name'])
    originals={i['name']:i for i in old_items};version=old_version
    copies={'item':{i['name']:i['name'] for i in old_items}}
    if preview['mode']=='replace_all':
        values=clean_copy(old_version)
        values.update(version_code='AI-'+datetime.now().strftime('%Y%m%d%H%M%S%f'),version_type='Adjustment',status='Active',is_current=1,
                      source_type='Clone',calculated_at=None,summary_snapshot_json='{}',rule_snapshot_json='[]',remark='按勾选行替换装箱资料，旧版本保留')
        version=ledger.create('version',values)
        copies=clone_version_children({kind:ledger.rows(kind,batch=batch_name,version=old_version['name']) for kind in ('item','rule','evidence','component')},version['name'],ledger.create,ledger.put)
    kept=set();adopted=[]
    for index,incoming in enumerate(preview['rows'],1):
        target=incoming.get('_target') or incoming.get('name');original=originals.get(target)
        source_meta=row_meta(incoming);is_source=incoming.get('_row_action')=='source'
        name=copies['item'].get(target)
        if not is_source and original:
            kept.add(name);adopted.append(ledger.get('item',name));continue
        values={k:deepcopy(v) for k,v in incoming.items() if k in (*IDENTITY,*FILL_FIELDS,'quantity','unit','goods_value','source_doc_no','supplier','stable_line_key')}
        meta=deepcopy(source_meta)
        if preview['mode']=='update_selected' and original and incoming.get('_price_metadata'):
            # The catalog derives this only from trusted purchase evidence and
            # public payload sanitization removes it before returning previews.
            for key,value in incoming['_price_metadata'].items():
                if key not in {'adopted_purchase_value','purchase_value_history','shipment_valuation',
                               'settlement_valuation','manual_shipment_valuation'}:
                    meta[key]=deepcopy(value)
        if original:
            meta.setdefault('ai_fill_original_values', original_value_snapshot(original))
        if preview['mode']=='replace_all' or not original:
            for key in ('settlement_cargo','settlement_valuation','settlement_physical','effective_logistics_source'):
                meta.pop(key,None)
            if original:
                meta['settlement_original_values']=original_value_snapshot(original)
                price_meta=incoming.get('_price_metadata') or {}
                for key, value in price_meta.items():
                    if key not in {'settlement_cargo','settlement_valuation','settlement_physical','effective_logistics_source'}:
                        meta.setdefault(key, deepcopy(value))
            cargo=compact_cargo({**{k:values.get(k) for k in IDENTITY},
                'quantity':values.get('actual_shipped_qty'),'unit':values.get('shipped_uom'),
                'source_snapshot':preview['id'],'binding_id':preview['id'],
                'line_key':incoming.get('stable_line_key')})
            meta['settlement_cargo']=cargo
            meta['settlement_valuation']=reconcile_replacement_value(
                {**values,'extra_json':persist_item_meta(meta)}, cargo,
                {k:v for k,v in version.items() if k.startswith('fx_')},
                incoming.get('_verified_prior_item'),
            )
            from .shipment_cost_service import shipment_value
            effective_valuation=shipment_value({**values,'extra_json':persist_item_meta(meta)})
            values['goods_value']=effective_valuation.get('amount_rmb') if effective_valuation.get('amount_rmb') is not None else 0
            values['actual_shipped_qty_mode']='EXPLICIT_SOURCE';values['actual_shipped_qty_source_revision']=preview['revision']
            # All omitted packing facts are missing, never copied from a previous row.
            mask={k for k in (*PHYSICAL,'quantity','actual_shipped_qty') if values.get(k) is None}
            meta['settlement_packing_missing']=sorted(mask)
        else:
            mask=set(meta.get('settlement_packing_missing') or [])
            changed_fields={c['fieldname'] for c in preview['changes'] if c.get('item_name')==target}
            if 'actual_shipped_qty' in changed_fields:
                values['actual_shipped_qty_mode']='EXPLICIT_SOURCE';values['actual_shipped_qty_source_revision']=preview['revision']
            valuation_fields={'actual_shipped_qty','shipped_uom','unit_price','purchase_currency','unit_price_uom','purchase_uom'}
            trusted_price_refresh=bool(incoming.get('_price_metadata') and changed_fields & valuation_fields)
            if (meta.get('settlement_cargo') or trusted_price_refresh) and changed_fields & valuation_fields:
                cargo=compact_cargo({**(meta.get('settlement_cargo') or {}),
                    **{k:values.get(k) or original.get(k) for k in IDENTITY},
                    'quantity':values.get('actual_shipped_qty'),'unit':values.get('shipped_uom'),
                    'source_snapshot':(meta.get('settlement_cargo') or {}).get('source_snapshot') or preview['id'],
                    'binding_id':(meta.get('settlement_cargo') or {}).get('binding_id') or preview['id'],
                    'line_key':incoming.get('stable_line_key') or original.get('stable_line_key')})
                meta['settlement_cargo']=cargo
                item_for_valuation={**original,**values,'extra_json':persist_item_meta(meta)}
                meta['settlement_valuation']=(
                    reconcile_replacement_value(item_for_valuation,cargo,
                        {k:v for k,v in version.items() if k.startswith('fx_')},original)
                    if trusted_price_refresh
                    else value_final_cargo(item_for_valuation,cargo,
                        {k:v for k,v in version.items() if k.startswith('fx_')})
                )
                from .shipment_cost_service import shipment_value
                effective_valuation=shipment_value({**original,**values,'extra_json':persist_item_meta(meta)})
                values['goods_value']=(effective_valuation.get('amount_rmb')
                                       if effective_valuation.get('amount_rmb') is not None else 0)
        trusted_valuation=incoming.get('_shipment_valuation')
        if trusted_valuation is not None:
            amount=trusted_valuation.get('amount_rmb') if isinstance(trusted_valuation,dict) else None
            if (amount is None or trusted_valuation.get('error')
                    or str(amount) != str(incoming.get('shipment_value_rmb'))):
                raise ValueError(_('发货货值凭据已变化，请重新预览。'))
            valuation=deepcopy(trusted_valuation)
            valuation.update(status='automatic',trusted_shipment_source=True)
            meta['shipment_valuation']=deepcopy(valuation)
            meta['settlement_valuation']=deepcopy(valuation)
            values['goods_value']=str(amount)
        if incoming.get('unverified_material_code'):meta['unverified_material_code']=incoming['unverified_material_code']
        # Only numeric columns need zero storage; absence remains explicit in the mask.
        for key in (*PHYSICAL,'quantity','actual_shipped_qty'):
            if key not in ('packaging_type',) and values.get(key) is None:
                values[key]=0
                mask.add(key)
        meta['settlement_packing_missing']=sorted(mask)
        meta['ai_row_packing_values']={k:None if k in mask else incoming.get(k) for k in ('package_count','packaging_type')}
        from .effective_source_values import PHYSICAL_FIELDS as projected_fields
        physical_context=meta.get('effective_logistics_source') or preview['source_context']
        if physical_context:
            meta['effective_logistics_source']=deepcopy(physical_context)
            meta['settlement_physical']={'source_context_fingerprint':physical_context.get('fingerprint'),
                'source_snapshot':physical_context.get('source_snapshot'),
                'values':{k:None if k in mask else values.get(k,incoming.get(k)) for k in projected_fields}}
        values.update(batch=batch_name,version=version['name'],row_no=index,extra_json=persist_item_meta(meta),**{k:0 for k in DERIVED_FIELDS})
        saved=ledger.put('item',name,values) if name else ledger.create('item',values)
        kept.add(saved['name']);adopted.append(saved)
    scope_excluded = {
        str(name or '') for name in preview.get('scope_excluded_item_names') or []
        if str(name or '')
    }
    if scope_excluded - originals.keys() or scope_excluded & kept:
        raise ValueError(_('国际物流物料范围已变化，请重新预览。'))
    if scope_excluded:
        from .logistics_autofill_service import AUTO_SCOPE_EXCLUSION_REASON
        excluded_at = datetime.now().isoformat(timespec='seconds')
        for name in sorted(scope_excluded):
            ledger.put('item',name,{
                'is_excluded':1,
                'excluded_at':excluded_at,
                'excluded_by':'system',
                'exclusion_reason':AUTO_SCOPE_EXCLUSION_REASON,
            })
    removed={i['name'] for i in ledger.rows('item',batch=batch_name,version=version['name'])}-kept
    issues=[]
    if preview['mode']=='replace_all':
        from .logistics_settlement.packing_selection import preserve_selected_scopes
        issues=preserve_selected_scopes(ledger,batch_name,version['name'],old_items,copies,kept)
        for name in removed:ledger.delete('item',name)
        ledger.put('version',old_version['name'],{'is_current':0})
    metadata=row_meta(version)
    if preview.get('packing_group_candidates'):
        from .material_packing_group_service import adopt_xlsx_group_candidates, groups_from_version, META_KEY
        confirmed_member_keys = {
            str(row.get('stable_line_key') or '') for row in preview.get('rows') or []
            if row.get('_row_action') == 'source' and row.get('stable_line_key')
        }
        confirmed_member_keys.update(
            str(key) for candidate in preview.get('packing_group_candidates') or []
            if candidate.get('default_selected') and candidate.get('can_apply')
            for key in candidate.get('member_keys') or []
        )
        metadata[META_KEY]=adopt_xlsx_group_candidates(
            adopted, groups_from_version({'extra_json':metadata}), preview.get('packing_group_candidates') or [],
            preview['id'], confirmed_member_keys=confirmed_member_keys)
    metadata['packing_scope_issues']=(metadata.get('packing_scope_issues') or [])+issues
    if preview['mode']=='replace_all':
        from .material_ai_selected_scope import capture_dependencies
        dependencies=capture_dependencies(preview.get('sources') or [],store=store,ledger=ledger,
            batch_name=batch_name,source_context=preview['source_context'],inherited=metadata.get('ai_row_adoption'),
            purpose='estimate')
        metadata['ai_row_adoption']={'id':preview['id'],'revision':preview['revision'],
            'source_context':preview['source_context'],'selected_row_ids':preview['selected_row_ids'],
            'sources':[{k:s.get(k) for k in ('source_id','source_kind','source_hash','content_hash','source_context',
                'resolver_source_id','logical_source_id','parent_source_id','attachment_name','document_id','sheet_name',
                'process_instance_id','approval_no','selected','locked','scoped_packing','selected_source')} for s in preview.get('sources') or []],
            'dependencies':dependencies,'goods':scoped_goods(adopted),'complete':False}
    elif preview.get('original_source_reanalysis'):
        metadata.pop('ai_row_adoption',None)
    elif metadata.get('ai_row_adoption'):
        metadata['ai_row_adoption'].update(revision=preview['revision'],goods=scoped_goods(adopted))
    metadata.setdefault('ai_row_applications',[]).append({'preview_id':preview['id'],'run_id':preview['run_id'],'mode':preview['mode'],
        'selected_row_ids':preview['selected_row_ids'],'selected_field_choices':preview.get('selected_field_choices') or {},'changes':preview['changes'],
        'actual_sources':preview.get('actual_sources') or []})
    ledger.put('version',version['name'],{'extra_json':dumps(metadata),'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
    ledger.put('batch',batch_name,{'current_version':version['name'],'status':'Dirty','confirm_status':'Pending','writeback_status':'Not Started',
        'version_count':len(ledger.rows('version',batch=batch_name)),'item_count':len(kept)})
    return version['name']


def scoped_goods(items):
    goods=[]
    from .effective_source_values import project_source_values
    for raw_item in items:
        item=project_source_values(raw_item)
        meta=row_meta(item);mask=meta.get('settlement_packing_missing') or []
        goods.append({**{k:item.get(k) for k in IDENTITY},'quantity':None if 'actual_shipped_qty' in mask or 'quantity' in mask else item.get('actual_shipped_qty'),
            'unit':item.get('shipped_uom') or item.get('unit'),'line_key':item.get('stable_line_key') or item['name'],
            'physical':{k:None if k in mask else item.get(k) for k in PHYSICAL},'evidence':meta.get('ai_row_selection') or {'current_item':item['name']}})
    return goods


def _confirm_and_reconstruct_payment_match(store,ledger,run,preview,draft,context,actor,*,repository=None,freight_mode=None):
    references=preview.get('payment_match_candidates') or ([preview['payment_match_candidate']]
        if preview.get('payment_match_candidate') else [])
    if not references:
        return preview,context
    from . import material_ai_payment_match, material_ai_selection_service
    from .logistics_settlement import runtime
    enabled=runtime.freight_enabled() if freight_mode is None else bool(freight_mode)
    relations=[]
    for reference in references:
        clean={key:reference.get(key) for key in ('candidate_id','revision','version')}
        kwargs={'freight_mode':enabled}
        if reference.get('user_selected'):
            kwargs['user_selected']=True
        relation=material_ai_payment_match.confirm_preview_candidate(
            store,ledger,preview['batch'],clean,actor,**kwargs)
        relations.append({'relation':relation,'user_selected':bool(reference.get('user_selected'))})
    # Re-read all sources and rebuild rows/fees after the locked relation
    # validation, before any business record is written in the same savepoint.
    if repository is None:
        from .material_ai_fill_service import FrappeMaterialAIFillRepository
        repository=FrappeMaterialAIFillRepository()
    refreshed,locked_context=material_ai_selection_service.reconstruct_after_payment_match(
        repository,run,preview,draft)
    result={**refreshed,'_payment_match_relations':relations}
    if len(relations)==1:
        result['_payment_match_relation']=relations[0]['relation']
    return result,locked_context


def _write_rows_and_payment_relation(store,ledger,preview,context,actor):
    version_name=write_rows(store,ledger,preview,context) if (
        preview['selected_row_ids'] or preview.get('selected_field_choices')
        or preview.get('scope_excluded_item_names')
        or preview.get('selected_packing_assignments')
        or any(candidate.get('default_selected') and candidate.get('can_apply')
               for candidate in preview.get('packing_group_candidates') or [])
    ) else preview['version']
    relations=preview.get('_payment_match_relations') or ([{
        'relation':preview['_payment_match_relation'],'user_selected':False
    }] if preview.get('_payment_match_relation') else [])
    for entry in relations:
        from .material_ai_payment_match import persist_relation
        kwargs={'user_selected':True} if entry.get('user_selected') else {}
        persist_relation(store,ledger,preview['batch'],version_name,entry['relation'],actor,**kwargs)
    return version_name


def apply_selection(run,preview,draft,context):
    import frappe
    from . import material_ai_fill_service as ai, fee_service
    from .material_ai_fee_policy import assert_allowed
    from .logistics_settlement.store import Store
    from .logistics_settlement.ledger import FrappeLedger
    from .logistics_settlement.freight_adoption import refresh_item_contexts
    store=Store.frappe();ledger=FrappeLedger()
    with store.atomic():
        preview,context=_confirm_and_reconstruct_payment_match(
            store,ledger,run,preview,draft,context,frappe.session.user)
        version_name=_write_rows_and_payment_relation(
            store,ledger,preview,context,frappe.session.user)
        for proposal in preview['fees']:
            existing=fee_service._decorate_historical_rules(fee_service._query_rules(preview['batch'],version_name),context.get('transport_mode'))
            assert_allowed([proposal],existing,context.get('effective_source') or {})
            normalized=fee_service.normalize_fee_payload({**proposal['payload'],'is_enabled':1,'is_active':1,'required_evidence_role':'freight_invoice'},trusted_project_policy=True)
            merged=fee_service.merge_logical_fee(existing,normalized,revision='ai-row:'+preview['id'])
            values={k:merged['fee'].get(k) for k in (*fee_service.FEE_FIELDS,'amount_revision','scope_revision')}
            values.update(batch=preview['batch'],version=version_name)
            ledger.put('rule',merged['fee']['name'],values) if merged['action']=='updated' else ledger.create('rule',values)
        if preview['fees']:
            ledger.put('version',version_name,{'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
            ledger.put('batch',preview['batch'],{'status':'Dirty','confirm_status':'Pending','writeback_status':'Not Started'})
        from .effective_source_values import PHYSICAL_FIELDS, project_source_values
        effective_items={i['name']:project_source_values(i) for i in ledger.rows('item',batch=preview['batch'],version=version_name)}
        ctx=refresh_item_contexts(store,ledger,preview['batch'],version_name)
        for item in ledger.rows('item',batch=preview['batch'],version=version_name):
            meta=row_meta(item)
            if meta.get('ai_row_selection') or (ctx.get('packing') or {}).get('selected_source'):
                meta['settlement_physical']={'source_context_fingerprint':ctx['fingerprint'],'source_snapshot':ctx['source_snapshot'],
                    'values':{k:None if k in meta.get('settlement_packing_missing',[]) else effective_items[item['name']].get(k) for k in PHYSICAL_FIELDS}}
                ledger.put('item',item['name'],{'extra_json':persist_item_meta(meta)})
        result={'ok':True,'status':'APPLIED','run_id':preview['run_id'],'preview_id':preview['id'],'version_name':version_name,
                'changed_count':len(preview['changes'])+preview['added_count']+preview['removed_count']+len(preview['fees']),
                'batch_modified':str(ledger.get('batch',preview['batch']).get('modified') or ''),'message':'所选内容已保存，试算结果待更新。'}
        draft['row_application']=result
        frappe.db.set_value('Overseas Cost Material AI Run',ai._record_value(run,'name'),{'status':'APPLIED','draft_json':ai._json(draft),
            'progress_step':'所选行已采用','applied_at':ai._now(),'completed_at':ai._now()},update_modified=True)
        store.audit(preview['batch'],'ai_rows_adopted',frappe.session.user,run_id=preview['run_id'],preview_id=preview['id'],
            mode=preview['mode'],old_version=preview['version'],version=version_name,selected_row_ids=preview['selected_row_ids'],
            selected_fee_ids=preview['selected_fee_ids'],actual_sources=preview.get('actual_sources') or [])
        return result
