"""Choose one archived shipment source and replace the current packing dataset."""

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message

from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from .model import digest, dumps, parse_source, fields_of, pick, number, identity, decoded
from .freight_lines import matching_lines, identifiers_in, WAYBILL_LABELS, APPROVAL_LABELS, label_value
from .freight_matching import current_lines
from .freight_packing import evidence_goods, text_goods, item_fingerprint
from .freight_adoption import context, refresh_item_contexts
from .application import row_meta
from .item_metadata import compact_cargo, original_value_snapshot, persist_item_meta
from .writer import locked, clean_copy, clone_version_children, DERIVED_FIELDS
from .jobs import utcnow
from .ai_matching import save
from .document_writer import PHYSICAL_ALIASES
from .valuation import reconcile_replacement_value
from overseas_costing.utils.field_mapper import normalize_unit

POLICY = 'packing-selection-1'
KINDS = {'approval_form':'正文','approval_attachment':'附件','approval_comment_attachment':'评论附件','approval_comment':'评论'}
PHYSICAL = tuple(PHYSICAL_ALIASES)


def _scope(logistics, source, value=''):
    tokens=identifiers_in(value)
    own={tuple(t) for t in logistics.get('identifiers',[]) if t[0]=='waybill'}
    mentioned={tuple(t) for t in tokens if t[0]=='waybill'}
    if mentioned and not mentioned.issubset(own):return False
    if mentioned:return True
    if source['id']==logistics['id']:return True
    targets={r.get('waybill') for r in current_lines_for_source(source) if r.get('waybill')}
    return source.get('related')==[logistics['instance']] and targets.issubset({x[1] for x in own})


def current_lines_for_source(source):
    from .freight_lines import lines_for_source
    return lines_for_source(source)


def _body(source):
    raw={k:v for k,v in source['raw'].items() if k not in ('comments','operationRecords','operation_records')}
    return parse_source({'corp_id':source['corp'],'process_instance_id':source['instance'],
        'process_code':source['process_code'],'status':source['status'],'result':source['approval_result'],
        'business_id':source['approval_no'],'raw_payload':raw,'title':source.get('title','')},
        logistics_codes={source['process_code']} if source['kind']=='logistics' else set())


def _stable(goods, source_key):
    result=[];occurrences=Counter();native_counts=Counter(r.get('line_key') for r in goods if r.get('native_id'))
    for incoming in goods:
        if not (incoming.get('material_code') or incoming.get('product_name')):continue
        row=deepcopy(incoming);row['quantity']=number(incoming.get('quantity'))
        base=row.get('line_key') or digest(row.get('material_code'),row.get('product_name'),row.get('spec_model'),row.get('unit'))
        # Identical repeated rows have interchangeable positions, but retain their multiplicity.
        content=digest(base,{k:row.get(k) for k in ('material_code','product_name','spec_model','unit','quantity')},row.get('physical'))
        occurrences[content]+=1
        row['line_key']=digest(POLICY,source_key,base,occurrences[content]) if row.get('native_id') and native_counts[base]==1 else digest(POLICY,source_key,content,occurrences[content])
        row['physical']={k:v for k,v in (row.get('physical') or {}).items() if k in PHYSICAL and k!='chargeable_weight_kg'}
        result.append(row)
    return result


def _catalog(store,ledger,batch_name,version_name):
    batch=ledger.get('batch',batch_name) or {};version=ledger.get('version',version_name) or {}
    if version.get('batch')!=batch_name:raise ValueError(_i18n('成本版本不属于本票'))
    maps=store.find('batch_map',batch=batch_name)
    if len(maps)!=1:raise ValueError(_i18n('本票国际物流来源待初始化'))
    logistics=store.get('source',maps[0]['source_id'])
    body_ids=_body(logistics)['identifiers']
    body_waybills=[t for t in body_ids if t[0]=='waybill']
    if body_waybills:
        logistics={**logistics,'identifiers':[t for t in logistics['identifiers'] if t[0]!='waybill']+body_waybills}
    candidates=[c for c in store.find('freight_candidate',logistics_id=logistics['id']) if c['status']!='rejected']
    source_ids={logistics['id'],*(c['expense_id'] for c in candidates)}
    output=[]
    def add(source,kind,key,label,goods,complete=False,text='',proof=None,allowed=True,**extras):
        proof=proof or {};sid=digest(POLICY,source['id'],kind,key)
        goods=_stable(goods,sid) if allowed else []
        issues=[]
        if not allowed:issues.append('尚不能确定资料属于本票')
        if not source['approved'] or source['invalid'] or logistics['invalid']:issues.append('来源未批准或已失效')
        if not goods:issues.append('未识别出本票物料；仅供查看')
        if any(g.get('quantity') is not None and Decimal(g['quantity'])<0 for g in goods):issues.append('装箱数量不能为负数')
        can_adopt=bool(goods) and not issues
        if any(not g.get('unit') or g.get('quantity') is None or Decimal(g['quantity'])==0 for g in goods):
            issues.append('数量或单位待核对');complete=False
        descriptor={'id':sid,'source_id':source['id'],'source_kind':kind,'type_label':KINDS[kind],
            'source_label':label,'approval_no':source['approval_no'],'source_snapshot':source['snapshot'],
            'process_instance_id':source['instance'],'occurred_at':source.get('source_updated_at'),
            'actor_name':source.get('raw',{}).get('originatorUserName') or '', 'evidence':proof,**extras}
        revision=digest(POLICY,source['snapshot'],logistics['snapshot'],descriptor,goods,complete,text,issues)
        output.append({**descriptor,'revision':revision,'row_count':len(goods),'complete':complete,'can_adopt':can_adopt,
            'status':'可采用' if goods and not issues else '；'.join(issues),'issues':issues,'goods':goods,
            'text':text if allowed else '', 'logistics_snapshot':logistics['snapshot']})
    for source_id in sorted(source_ids):
        source=store.get('source',source_id)
        if not source or source['corp']!=logistics['corp']:continue
        native=_body(source);body_text='\n'.join(f'{k}: {v}' for k,v in native['fields'].items() if not isinstance(v,(dict,list)))
        body_allowed=_scope(logistics,source,body_text)
        body_goods=deepcopy(native['goods'])
        if not body_goods and body_allowed:
            for key,val in native['fields'].items():
                if isinstance(val,str):body_goods.extend(text_goods(val,{'source_table':key}))
        add(source,'approval_form','body','审批正文',body_goods,native['goods_complete'],body_text,allowed=body_allowed)
        comments=decoded(source.get('raw',{}).get('comments')) or []
        comments += [r for r in decoded(source.get('raw',{}).get('operationRecords')) or [] if r.get('remark') or r.get('text')]
        seen=set()
        for c in comments:
            if not isinstance(c,dict):continue
            text=str(c.get('text') or c.get('remark') or c.get('content') or '')
            if not text.strip():continue
            cid=str(c.get('commentId') or c.get('id') or digest(c));key=(cid,text)
            if key in seen:continue
            seen.add(key);proof={'comment_id':cid}
            add(source,'approval_comment',cid,'评论 · '+str(c.get('userName') or c.get('user_name') or c.get('userId') or ''),
                text_goods(text,proof),text=text,proof=proof,allowed=_scope(logistics,source,text),comment_id=cid,
                actor_name=c.get('userName') or c.get('user_name') or '',occurred_at=c.get('createTime') or c.get('create_time') or source.get('source_updated_at'))
        lines=matching_lines(logistics,current_lines(store,source))
        for doc in source.get('documents') or []:
            manifest=doc.get('manifest') or {}
            if manifest.get('retired_at') or doc.get('retired_at'):continue
            origin=str(manifest.get('source_type') or manifest.get('source_kind') or manifest.get('origin') or manifest.get('attachment_origin') or '').lower()
            kind='approval_comment_attachment' if 'comment' in origin or manifest.get('comment_id') or manifest.get('commentId') else 'approval_attachment'
            sheets={t['title'] for t in (doc.get('tables') or [])+(doc.get('freight_tables') or [])} or {''}
            for sheet in sorted(sheets):
                local_lines=[r for r in lines if r['evidence'].get('document_id')==doc['id'] and r['evidence'].get('sheet','')==sheet and not r.get('identifier_conflict')]
                narrowed={**source,'goods':[],'documents':[{**doc,'tables':[t for t in doc.get('tables') or [] if t['title']==sheet]}]}
                goods,complete,texts=evidence_goods(store,logistics,narrowed,local_lines)
                if not goods and _scope(logistics,source,sheet):
                    for table in narrowed['documents'][0]['tables']:
                        if table.get('kind') not in ('goods','packing'):continue
                        for row in table['rows']:
                            f=fields_of(row);ident={'waybill':str(label_value(f,WAYBILL_LABELS)[1] or ''),'approval_no':str(label_value(f,APPROVAL_LABELS)[1] or '')}
                            matches=matching_lines(logistics,[ident]) if any(ident.values()) else []
                            if any(ident.values()) and (not matches or any(m.get('identifier_conflict') for m in matches)):continue
                            sku=pick(f,'物料编码','编码','SKU','Codigo') or '';name=pick(f,'物品名称','货物名称','名称','Nombre') or ''
                            proof={'document_id':doc['id'],'file_name':doc.get('file_name'),'sheet':sheet,'row':row.get('position')}
                            goods.append({'line_key':row.get('rowId'),'native_id':bool(row.get('rowId')),'material_code':sku,'product_name':name,
                                'spec_model':pick(f,'规格','Especificacion') or '', 'quantity':number(pick(f,'数量','Cantidad')),
                                'unit':pick(f,'单位','Unidad') or '', 'physical':{k:number(pick(f,*aliases)) for k,aliases in PHYSICAL_ALIASES.items() if pick(f,*aliases) is not None},
                                'packaging':pick(f,'包装','包装方式','箱数','Packing') or '', 'merchandise_price':{'present':False},'evidence':proof})
                        complete|=bool(table.get('complete'))
                proof={'document_id':doc['id'],'file_name':doc.get('file_name'),'sheet':sheet,'rows':[r['evidence'].get('row') for r in local_lines]}
                quality=manifest.get('archive_quality') or manifest.get('content_quality')
                valid_document=quality in ('original','original_complete') and doc.get('status') not in ('pending','failed','error')
                add(source,kind,doc['id']+':'+sheet,(doc.get('file_name') or '附件')+(' · '+sheet if sheet else ''),goods,complete,
                    '\n'.join(t['text'] for t in texts),proof,allowed=valid_document and (bool(goods) or _scope(logistics,source,sheet)),
                    document_id=doc['id'],sheet=sheet,parse_status=doc.get('status',''),parse_issues=doc.get('issues') or [])
    return logistics,output


def list_sources(store,ledger,batch_name,version_name):
    _,rows=_catalog(store,ledger,batch_name,version_name)
    return {'sources':[{k:v for k,v in r.items() if k not in ('goods','text')} for r in rows], 'version':version_name}


def _key(item):
    return (identity(item.get('material_code') or item.get('product_name')),identity(item.get('spec_model')),normalize_unit(item.get('unit') or ''))


def _mapping(goods,items):
    result={};used=set()
    for g in goods:
        matches=[i for i in items if i['name'] not in used and _key(i)==_key(g)]
        if len(matches)>1:
            stable=[i for i in matches if i.get('stable_line_key')==g['line_key']]
            matches=stable or matches
        if len(matches)==1:
            result[g['line_key']]=matches[0]['name'];used.add(matches[0]['name'])
    return result


def preview_selection(store,ledger,batch_name,version_name,source_id,source_revision):
    logistics,rows=_catalog(store,ledger,batch_name,version_name)
    chosen=next((r for r in rows if r['id']==source_id and r['revision']==source_revision),None)
    if not chosen:raise ValueError(_i18n('资料来源已变化，请刷新来源列表'))
    items=ledger.rows('item',batch=batch_name,version=version_name)
    mapping=_mapping(chosen['goods'],items);freight=context(store,ledger,batch_name,version_name)
    revision=digest(POLICY,chosen['revision'],item_fingerprint(items),freight['revision'])
    pr={'id':digest(POLICY,batch_name,version_name,revision),'batch':batch_name,'version':version_name,'source_id':chosen['source_id'],
        'source_snapshot':chosen['source_snapshot'],'logistics_snapshot':logistics['snapshot'],'status':'pending','revision':revision,
        'selection':chosen,'goods':chosen['goods'],'complete':chosen['complete'],'mapping':mapping,'item_fingerprint':item_fingerprint(items),
        'freight_revision':freight['revision'],'removed_count':len(items)-len(mapping),'new_count':len(chosen['goods'])-len(mapping),
        'missing_fields':sorted({label for g in chosen['goods'] for field,label in [('gross_weight_kg','毛重'),('volume_m3','体积'),('quantity','数量'),('unit','单位'),('material_code','SKU')]
                                 if (g.get('physical',{}).get(field) if field in PHYSICAL else g.get(field)) in (None,'')})}
    existing=store.get('packing_review',pr['id'])
    if existing:return existing
    store.insert('packing_review',{k:pr[k] for k in ('id','batch','version','source_id','status')}|{'data':dumps(pr)})
    return pr


def confirm_selection(store,ledger,batch_name,version_name,preview_id,revision,actor,*,replace_all=False):
    if replace_all is not True:raise ValueError(_i18n('请确认采用此来源并整份替换本票装箱资料'))
    with store.atomic():
        store.get('state','match_lock',lock=True)
        review=store.get('packing_review',preview_id,lock=True) or {};batch=ledger.get('batch',batch_name,lock=True) or {}
        if review.get('batch')!=batch_name or review.get('revision')!=revision or not review.get('selection'):raise ValueError(_i18n('装箱预览已变化或不属于本票'))
        if review.get('status')=='applied' and review.get('applied_version')==batch.get('current_version'):
            return {'status':'applied','version':batch['current_version'],'cached':True}
        if batch.get('current_version')!=version_name or review['version']!=version_name:raise ValueError(_i18n('成本版本已变化，请重新预览'))
        olditems=ledger.rows('item',batch=batch_name,version=version_name)
        if item_fingerprint(olditems)!=review['item_fingerprint'] or context(store,ledger,batch_name,version_name)['revision']!=review['freight_revision']:raise ValueError(_i18n('当前物料或费用已变化，请重新预览'))
        store.get('source',review['source_id'],lock=True)
        _,rows=_catalog(store,ledger,batch_name,version_name)
        selected=next((r for r in rows if r['id']==review['selection']['id'] and r['revision']==review['selection']['revision']),None)
        if not selected or not selected['can_adopt']:raise ValueError(_i18n('来源已变化、未批准或没有可采用物料'))
        if locked(batch):
            save(store,{'id':digest(POLICY,'queue',preview_id),'kind':'packing_replace','status':'queued','request':dict(batch_name=batch_name,version_name=version_name,preview_id=preview_id,revision=revision,actor=actor,replace_all=True)})
            return {'status':'queued','version':version_name,'message':'批次正在编辑，装箱替换已排队'}
        oldversion=ledger.get('version',version_name);values=clean_copy(oldversion)
        values.update(version_code='PACK-'+datetime.now().strftime('%Y%m%d%H%M%S%f'),version_type='Adjustment',status='Active',is_current=1,
                      source_type='Clone',calculated_at=None,summary_snapshot_json='{}',rule_snapshot_json='[]',remark='整份采用装箱来源；旧版本保留')
        version=ledger.create('version',values);vname=version['name']
        copies=clone_version_children({kind:ledger.rows(kind,batch=batch_name,version=version_name) for kind in ('item','rule','evidence','component')},vname,ledger.create,ledger.put)
        kept=set();scope_issues=[]
        for index,incoming in enumerate(review['goods'],1):
            oldid=review['mapping'].get(incoming['line_key']);target=ledger.get('item',copies['item'][oldid]) if oldid else None
            # Start from empty packing facts; only the independent price evidence can survive.
            meta={};values={field:None for field in PHYSICAL};values.update(chargeable_weight_kg=None,weight_ratio=0)
            prior_item=None
            if target:
                prior_item=next(i for i in olditems if i['name']==oldid)
                oldmeta=row_meta(target)
                meta={k:deepcopy(value) for k,value in oldmeta.items()
                      if k not in {'settlement_cargo','settlement_valuation','settlement_physical','effective_logistics_source'}}
                meta['settlement_original_values']=original_value_snapshot(prior_item)
            cargo=compact_cargo({**deepcopy(incoming),'source_snapshot':review['source_snapshot'],'binding_id':review['id']})
            values.update({k:incoming.get(k) for k in ('material_code','product_name','spec_model','quantity','unit')})
            values.update({k:v for k,v in incoming.get('physical',{}).items() if k in PHYSICAL and k!='chargeable_weight_kg'})
            values.update(actual_shipped_qty=incoming['quantity'],actual_shipped_qty_mode='EXPLICIT_SOURCE',actual_shipped_qty_source_revision=review['revision'],shipped_uom=incoming.get('unit'),row_no=index,
                          manual_override_flag=0,manual_override_reason='',stable_line_key=(target or {}).get('stable_line_key') or incoming['line_key'])
            meta.update(settlement_cargo=cargo,packing_source_selection=selected['id'],packing_quantity=incoming['quantity'])
            meta['settlement_valuation']=reconcile_replacement_value(
                {**(target or {}),**values,'extra_json':persist_item_meta(meta)}, cargo,
                {k:v for k,v in version.items() if k.startswith('fx_')}, prior_item,
            )
            from overseas_costing.services.shipment_cost_service import shipment_value
            valuation=shipment_value({**(target or {}),**values,'extra_json':persist_item_meta(meta)})
            values['goods_value']=valuation.get('amount_rmb') if valuation.get('amount_rmb') is not None else 0
            meta['settlement_packing_missing']=[k for k in (*PHYSICAL,'quantity') if values.get(k) is None]
            # Frappe numeric columns are NOT NULL; the explicit mask preserves absence.
            values.update({k:0 for k in meta['settlement_packing_missing']})
            values.update(batch=batch_name,version=vname,extra_json=persist_item_meta(meta),**{k:0 for k in DERIVED_FIELDS})
            saved=ledger.put('item',target['name'],values) if target else ledger.create('item',values);kept.add(saved['name'])
        removed={i['name'] for i in ledger.rows('item',batch=batch_name,version=vname)}-kept
        scope_issues=preserve_selected_scopes(ledger,batch_name,vname,olditems,copies,kept)
        for name in removed:ledger.delete('item',name)
        metadata=row_meta(version);freight=metadata.get('freight_settlement') or {'policy':'shipment-freight-1','revision':review['freight_revision'],'claims':[]}
        freight['packing_review_id']=review['id'];metadata.update(freight_settlement=freight,packing_scope_issues=scope_issues)
        ledger.put('version',vname,{'extra_json':dumps(metadata)})
        ledger.put('version',version_name,{'is_current':0})
        ledger.put('batch',batch_name,{'current_version':vname,'status':'Dirty','confirm_status':'Pending','writeback_status':'Not Started','is_locked':0,'version_count':len(ledger.rows('version',batch=batch_name)),'item_count':len(kept)})
        review.update(status='applied',applied_version=vname,applied_at=utcnow(),actor=actor,adopted_items=ledger.rows('item',batch=batch_name,version=vname))
        store.put('packing_review',{'id':review['id'],'status':'applied','data':dumps(review)})
        ctx=refresh_item_contexts(store,ledger,batch_name,vname)
        for item in ledger.rows('item',batch=batch_name,version=vname):
            meta=row_meta(item);meta['settlement_physical']={'source_context_fingerprint':ctx['fingerprint'],'source_snapshot':ctx['source_snapshot'],
                'values':{k:None if k in meta.get('settlement_packing_missing',[]) else item.get(k) for k in (*PHYSICAL,'shipped_uom')}}
            ledger.put('item',item['name'],{'extra_json':persist_item_meta(meta)})
        store.audit(batch_name,'packing_source_replaced',actor,preview_id=review['id'],source=selected['source_label'],old_version=version_name,version=vname)
        return {'status':'applied','version':vname,'preview_id':review['id']}


def scope_blockers(ledger,batch_name,version_name):
    from overseas_costing.services.fee_allocation_service import _scope_keys,_item_key
    version=ledger.get('version',version_name) or {};items=ledger.rows('item',batch=batch_name,version=version_name)
    ids={i['name'] for i in items};keys={_item_key(i) for i in items};issues=[]
    for pending in row_meta(version).get('packing_scope_issues') or []:
        row=ledger.get(pending['kind'],pending['name'])
        if not row:continue
        if pending['kind']=='rule':
            unresolved=not row.get('is_enabled') or not row.get('is_active') or (row.get('scope_type')!='ALL_ITEMS' and (not _scope_keys(row) or not set(_scope_keys(row)).issubset(keys)))
        else:unresolved=row.get('item') not in ids
        if unresolved:issues.append('装箱替换后，物料定向费用／凭证需在资料与费用中重新指定：'+row.get('rule_code',row['name']))
    return issues


def preserve_selected_scopes(ledger,batch_name,vname,olditems,copies,kept):
    removed={i['name'] for i in ledger.rows('item',batch=batch_name,version=vname)}-kept
    scope_issues=[]
    for kind in ('evidence','component'):
        for row in ledger.rows(kind,batch=batch_name,version=vname):
            if row.get('item') in removed:
                scope_issues.append({'kind':kind,'name':row['name']})
                ledger.put(kind,row['name'],{'item':None,**({'is_active':0} if kind=='component' else {})})
    for rule in ledger.rows('rule',batch=batch_name,version=vname):
        if rule.get('scope_type') not in (None,'','ALL_ITEMS'):
            from overseas_costing.services.fee_allocation_service import _scope_keys, _item_key
            current_items=[i for i in ledger.rows('item',batch=batch_name,version=vname) if i['name'] in kept]
            new_keys={_item_key(i) for i in current_items}
            key_map={old['name']:_item_key(ledger.get('item',copies['item'][old['name']])) for old in olditems if copies['item'][old['name']] in kept}
            keys=[key_map.get(k,k) for k in _scope_keys(rule)]
            if keys and set(keys).issubset(new_keys):ledger.put('rule',rule['name'],{'scope_value_json':dumps(keys)})
            else:
                scope_issues.append({'kind':'rule','name':rule['name']})
                ledger.put('rule',rule['name'],{'is_enabled':0,'is_active':0})
    return scope_issues
