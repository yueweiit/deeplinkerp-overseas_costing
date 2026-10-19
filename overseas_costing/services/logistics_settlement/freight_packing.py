"""Independent, server-owned packing comparison and explicit row adoption."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import re
from copy import deepcopy
from .model import digest,dumps,identity,number,fields_of,pick,merchandise_price
from .application import row_meta
from .item_metadata import compact_cargo, original_value_snapshot, persist_item_meta
from .writer import mutable_version,locked,DERIVED_FIELDS
from .freight_lines import matching_lines,POLICY,WAYBILL_LABELS,APPROVAL_LABELS,label_value
from .freight_adoption import context,refresh_item_contexts
from .jobs import utcnow
from .ai_matching import save


def text_goods(text,evidence):
    # Free text is always partial, even when every recognizable row parses.
    result=[]
    clean=str(text or '').strip(' "\n')
    pattern=r'(?P<description>.*?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>pcs?|pieces?|pieza[s]?|件|套|个)(?=\s|$)'
    for match in re.finditer(pattern,clean,re.I|re.S):
        description=match.group('description').strip(' \n\r\t\"')
        identified=re.match(r'^([A-Z]{2,8}\d{3,})(.*)$',description,re.S)
        sku=identified.group(1) if identified else ''
        name=(identified.group(2) if identified else description).strip()
        if not name:continue
        unit=match.group('unit');unit='件' if unit.lower() in ('pc','pcs','piece','pieces','pieza','piezas') else unit
        result.append({'line_key':digest(evidence.get('document_id'),sku,name,unit),'material_code':sku,'product_name':name,
            'spec_model':'','quantity':number(match.group('qty')),'unit':unit,'physical':{},'merchandise_price':{'present':False},'evidence':evidence})
    return result


def evidence_goods(store,logistics,source,lines):
    if any(r.get('identifier_conflict') for r in matching_lines(logistics,lines)):
        raise ValueError(_('装箱证据的运单与审批号指向不同票，请先核对'))
    goods=[];complete=False;texts=[];seen_text=set()
    for line in lines:
        if line.get('cargo_text') and line['cargo_text'] not in seen_text:
            seen_text.add(line['cargo_text'])
            texts.append({'text':line['cargo_text'],'evidence':line['evidence']})
            goods.extend(text_goods(line['cargo_text'],line['evidence']))
    tokens={t[1] for t in logistics['identifiers'] if t[0] in ('waybill','approval')}
    from .document_writer import PHYSICAL_ALIASES
    for doc in source.get('documents') or []:
        if (doc.get('manifest') or {}).get('retired_at'):continue
        for table in doc.get('tables') or []:
            if table.get('kind') not in ('goods','packing'):continue
            for row in table['rows']:
                fields=fields_of(row)
                identifiers=[label_value(fields,WAYBILL_LABELS)[1],label_value(fields,APPROVAL_LABELS)[1],table['title']]
                identified={'waybill':str(identifiers[0] or ''),'approval_no':str(identifiers[1] or '')}
                matched=matching_lines(logistics,[identified])
                if any(r.get('identifier_conflict') for r in matched):continue
                if any(identified.values()) and not matched:continue
                if not matched and table['title'] not in tokens:continue
                sku=pick(fields,'物料编码','编码','品目编码Item code','SKU') or ''
                quantity=number(pick(fields,'数量','总个数','总个数 The total number of','Cantidad'))
                if quantity is None or not sku:continue
                proof={'document_id':doc['id'],'file_name':doc.get('file_name'),'sheet':table['title'],'row':row.get('position')}
                item={'line_key':digest(doc.get('file_id'),table['title'],row.get('rowId') or [sku,pick(fields,'规格'),pick(fields,'单位')]),
                    'material_code':str(sku),'product_name':pick(fields,'物品名称','货物名称','名称','中文品名 Chinese Name') or '',
                    'quantity':quantity,'unit':pick(fields,'单位','申报单位') or '', 'spec_model':pick(fields,'规格') or '',
                    'physical':{field:number(pick(fields,*aliases)) for field,aliases in PHYSICAL_ALIASES.items()
                                if field!='actual_shipped_qty' and pick(fields,*aliases) is not None},
                    'merchandise_price':merchandise_price(fields,source_table=table['title'],source_position=row.get('position')),'evidence':proof}
                if item['unit']:goods.append(item)
                complete|=bool(table.get('complete') and table['title'] in tokens)
    # Native full cargo table is safe only for a demonstrably single-shipment source.
    from .freight_matching import current_lines
    all_lines=current_lines(store,source)
    targets={r.get('waybill') or r.get('approval_no') for r in all_lines}-{''}
    if source.get('goods') and len(source['related'])==1 and logistics['instance'] in source['related'] and len(targets)<=1:
        goods.extend(deepcopy(source['goods']));complete|=bool(source['goods_complete'])
    # Repeated cargo text across split project rows is one packing evidence set.
    dedup={digest(g):g for g in goods}
    return list(dedup.values()),complete,texts


def item_fingerprint(items):
    return digest(sorted((dict(row) for row in items),key=lambda row:row['name']))


def preview(store,ledger,batch_name,version_name,candidate_id,candidate_revision):
    c=store.get('freight_candidate',candidate_id)
    if not c or c['revision']!=candidate_revision or c['status']=='rejected':raise ValueError(_('候选已变化'))
    if not store.find('batch_map',batch=batch_name,source_id=c['logistics_id']):raise ValueError(_('候选不属于本票'))
    batch=ledger.get('batch',batch_name)
    if batch['current_version']!=version_name:raise ValueError(_('请在当前成本版本核对装箱'))
    source=store.get('source',c['expense_id']);logistics=store.get('source',c['logistics_id'])
    if source['snapshot']!=c['expense_snapshot'] or logistics['snapshot']!=c['logistics_snapshot']:raise ValueError(_('来源已变化'))
    lines=[store.get('freight_line',i) for i in c['line_ids']]
    goods,complete,texts=evidence_goods(store,logistics,source,lines)
    items=ledger.rows('item',batch=batch_name,version=version_name)
    fingerprint=item_fingerprint(items)
    revision=digest(POLICY,c['revision'],fingerprint,goods,context(store,ledger,batch_name,version_name)['revision'])
    value={'id':digest(POLICY,'packing',batch_name,version_name,revision),'batch':batch_name,'version':version_name,
           'source_id':source['id'],'source_snapshot':source['snapshot'],'logistics_snapshot':logistics['snapshot'],
           'candidate_id':c['id'],'candidate_revision':c['revision'],'revision':revision,'item_fingerprint':fingerprint,
           'freight_revision':context(store,ledger,batch_name,version_name)['revision'],
           'goods':goods,'complete':complete,'texts':texts,'current_items':items,'original_goods':logistics.get('goods') or [],
           'status':'pending' if goods or texts else 'no_evidence','approved':source['approved'],'invalid':source['invalid'],
           'message':'请单独核对本票物料差异；缺失字段保留原资料，计费重量不覆盖毛重' if goods or texts else '未发现可核对的装箱变更资料'}
    previous=store.get('packing_review',value['id'])
    if previous:return previous
    store.insert('packing_review',{k:value[k] for k in ('id','batch','version','source_id','status')}|{'data':dumps(value)})
    return value


def confirm(store,ledger,batch_name,version_name,preview_id,revision,selections,actor,*,complete_confirmed=False):
    with store.atomic():
        store.get('state','match_lock',lock=True)
        review=store.get('packing_review',preview_id,lock=True)
        if not review or review['batch']!=batch_name or review['revision']!=revision:raise ValueError(_('装箱预览不属于本票或已变化'))
        batch=ledger.get('batch',batch_name,lock=True)
        if review['status']=='applied' and review.get('applied_version')==batch['current_version']:return {'status':'applied','version':batch['current_version'],'cached':True}
        if batch['current_version']!=version_name or review['version']!=version_name:raise ValueError(_('成本版本已变化'))
        candidate=store.get('freight_candidate',review['candidate_id'],lock=True)
        if not candidate or candidate['status']=='rejected' or candidate['revision']!=review['candidate_revision']:
            raise ValueError(_('匹配候选已变化或被否决，请重新核对'))
        items=ledger.rows('item',batch=batch_name,version=version_name)
        if review['item_fingerprint']!=item_fingerprint(items) or review['freight_revision']!=context(store,ledger,batch_name,version_name)['revision']:
            raise ValueError(_('当前资料已变化，请重新预览装箱差异'))
        source=store.get('source',review['source_id'],lock=True)
        mapping=store.find('batch_map',batch=batch_name)
        logistics=store.get('source',mapping[0]['source_id'],lock=True) if mapping else None
        if not logistics or logistics['invalid'] or logistics['snapshot']!=review['logistics_snapshot']:raise ValueError(_('本票国际物流已变化，请重新预览'))
        if source['snapshot']!=review['source_snapshot'] or not source['approved'] or source['invalid']:raise ValueError(_('装箱来源已更新、未批准或失效'))
        if any(r.get('identifier_conflict') for r in matching_lines(logistics,[store.get('freight_line',lid) for lid in candidate['line_ids']])):
            raise ValueError(_('装箱证据的运单与审批号冲突，不能采用'))
        if not isinstance(selections,list) or not selections:raise ValueError(_('请选择需要采用的物料行'))
        goods={g['line_key']:g for g in review['goods']}
        if len(goods)!=len(review['goods']):raise ValueError(_('来源行身份重复，请先核对'))
        selected_keys=[s.get('line_key') for s in selections];target_ids=[s.get('item_name') for s in selections if s.get('item_name')]
        if len(set(selected_keys))!=len(selected_keys) or set(selected_keys)-set(goods) or len(set(target_ids))!=len(target_ids):raise ValueError(_('装箱行选择或映射重复'))
        current={i['name']:i for i in items}
        if set(target_ids)-set(current):raise ValueError(_('映射物料不属于当前版本'))
        if complete_confirmed and (not review['complete'] or set(selected_keys)!=set(goods)):raise ValueError(_('部分资料不能作为完整清单删除物料'))
        if locked(batch):
            save(store,{'id':digest(POLICY,'packing-queue',preview_id),'kind':'packing_apply','status':'queued',
                'request':{'batch_name':batch_name,'version_name':version_name,'preview_id':preview_id,'revision':revision,'selections':selections,'actor':actor,'complete_confirmed':complete_confirmed}})
            return {'status':'queued','message':'批次正在编辑，装箱采用已排队'}
        version=mutable_version(ledger,batch);vname=version['name'];new_items=ledger.rows('item',batch=batch_name,version=vname)
        translated={row_meta(i).get('settlement_origin_item') or i['name']:i for i in new_items}
        used=set()
        for selection in selections:
            incoming=goods[selection['line_key']];old=current.get(selection.get('item_name'))
            target=translated.get((row_meta(old).get('settlement_origin_item') or old['name']) if old else '')
            metadata=row_meta(target or {})
            metadata.setdefault('packing_source_history',[]).append({'source_snapshot':review['source_snapshot'],'previous':original_value_snapshot(target or {}),'evidence':incoming.get('evidence')})
            values={k:incoming.get(k) for k in ('material_code','product_name','spec_model','unit','quantity')}
            values.update({k:v for k,v in (incoming.get('physical') or {}).items() if v is not None})
            changed_identity=target and (identity(target.get('material_code'))!=identity(incoming.get('material_code')) or identity(target.get('unit'))!=identity(incoming.get('unit')))
            if changed_identity:
                metadata.setdefault('settlement_original_values',original_value_snapshot(target))
                # Preserve original price/packing in audit; never transfer another SKU's facts.
                values.update(unit_price=0,goods_value=0,purchase_currency='',unit_price_uom='',gross_weight_kg=0,net_weight_kg=0,volume_m3=0)
                values.update(incoming.get('physical') or {})
                metadata['settlement_packing_review']=True
            elif target and str(target.get('quantity'))!=str(incoming.get('quantity')):
                metadata['settlement_packing_review']=True
            metadata.update(settlement_line_key=incoming['line_key'],settlement_cargo=compact_cargo({**incoming,'source_snapshot':review['source_snapshot'],'binding_id':review['id']}))
            from .valuation import value_final_cargo
            valuation=value_final_cargo({**(target or {}),**values,'extra_json':persist_item_meta(metadata)},metadata['settlement_cargo'],{k:v for k,v in version.items() if k.startswith('fx_')})
            metadata['settlement_valuation']=valuation
            values.update(batch=batch_name,version=vname,extra_json=persist_item_meta(metadata),stable_line_key=(target or {}).get('stable_line_key') or incoming['line_key'],**{k:0 for k in DERIVED_FIELDS})
            saved=ledger.put('item',target['name'],values) if target else ledger.create('item',values);used.add(saved['name'])
        if complete_confirmed:
            for item in new_items:
                if item['name'] not in used:
                    if item.get('manual_override_flag'):raise ValueError(_('完整清单与人工补充行冲突，请先核对'))
                    ledger.delete('item',item['name'])
        meta=row_meta(version);freight=meta.get('freight_settlement') or {'policy':POLICY,'revision':review['freight_revision'],'claims':[]}
        freight['packing_review_id']=review['id'];meta['freight_settlement']=freight
        ledger.put('version',vname,{'extra_json':dumps(meta),'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
        review.update(status='applied',applied_version=vname,applied_at=utcnow(),actor=actor,selections=selections,
                      adopted_items=ledger.rows('item',batch=batch_name,version=vname))
        store.put('packing_review',{'id':review['id'],'status':'applied','data':dumps(review)})
        ctx=refresh_item_contexts(store,ledger,batch_name,vname)
        for item in ledger.rows('item',batch=batch_name,version=vname):
            im=row_meta(item)
            im['settlement_physical']={'source_context_fingerprint':ctx['fingerprint'],'source_snapshot':ctx['source_snapshot'],
                'values':{k:item.get(k) for k in ('actual_shipped_qty','shipped_uom','gross_weight_kg','net_weight_kg','volume_m3','chargeable_weight_kg')}}
            ledger.put('item',item['name'],{'extra_json':persist_item_meta(im)})
        ledger.put('batch',batch_name,{'status':'Dirty','confirm_status':'Pending','is_locked':0})
        store.audit(batch_name,'packing_changes_adopted',actor,preview_id=review['id'],version=vname)
        return {'status':'applied','version':vname,'preview_id':review['id']}


def resolve_checks(store,ledger,batch_name,version_name,selections,reason,actor):
    if not isinstance(selections,list) or not selections or not str(reason).strip():raise ValueError(_('请选择装箱核对项并填写依据'))
    with store.atomic():
        store.get('state','match_lock',lock=True)
        batch=ledger.get('batch',batch_name,lock=True) or {};version=ledger.get('version',version_name,lock=True) or {}
        if batch.get('current_version')!=version_name or version.get('batch')!=batch_name or version.get('status') in ('Confirmed','Archived') or batch.get('writeback_status')=='Success' or locked(batch):
            raise ValueError(_('请在未锁定的当前调整草稿核对装箱'))
        from overseas_costing.services.effective_logistics_source import resolve_source_context
        current_context=resolve_source_context(batch_name,version_name,store=store,ledger=ledger,lock=True)
        if not current_context.get('available') or current_context.get('invalid'):raise ValueError(_('当前装箱来源已变化或不可用，请先重新核对来源'))
        for choice in selections:
            item=ledger.get('item',choice.get('item_name'),lock=True) or {}
            if item.get('batch')!=batch_name or item.get('version')!=version_name or item_fingerprint([item])!=choice.get('expected_item_hash'):
                raise ValueError(_('物料或装箱值已变化，请刷新后核对'))
            meta=row_meta(item)
            if not meta.get('settlement_packing_review'):continue
            if (meta.get('effective_logistics_source') or {}).get('fingerprint')!=current_context.get('fingerprint'):raise ValueError(_('装箱来源或采用版本已变化，请重新比对'))
            if not any(item.get(k) is not None for k in ('gross_weight_kg','volume_m3')):raise ValueError(_('请先补充本票重量或体积'))
            meta.pop('settlement_packing_review',None)
            meta['freight_packing_verification']={'actor':actor,'reason':str(reason),'at':utcnow(),'item_fingerprint':choice['expected_item_hash']}
            ledger.put('item',item['name'],{'extra_json':dumps(meta),**{k:0 for k in DERIVED_FIELDS}})
        ledger.put('version',version_name,{'calculated_at':None,'summary_snapshot_json':'{}'})
        ledger.put('batch',batch_name,{'status':'Dirty'})
        store.audit(batch_name,'freight_packing_verified',actor,items=[c['item_name'] for c in selections],reason=str(reason),version=version_name)
    return {'status':'applied','version':version_name}
