"""Atomic fee-line claims, with independent packing and frozen version evidence."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from decimal import Decimal, InvalidOperation
from copy import deepcopy
from .model import digest, dumps
from .freight_lines import POLICY
from .application import row_meta, round_fee_rules
from .writer import mutable_version, locked, DERIVED_FIELDS
from .fee_policy import row_scopes, validate_final
from .ai_matching import save
from .jobs import utcnow


def context(store,ledger,batch_name,version_name=None,*,live=False):
    batch=ledger.get('batch',batch_name) or {}
    version=ledger.get('version',version_name or batch.get('current_version')) or {}
    saved=row_meta(version).get('freight_settlement') or {}
    records=deepcopy(saved.get('claims') or [])
    historical=version.get('name')!=batch.get('current_version') or version.get('status') in ('Confirmed','Archived')
    issues=['实际运费待核对；不会恢复旧暂估'] if saved.get('recovery_pending') else []
    if not historical or live:
        for claim in records:
            logistics=store.get('source',claim['logistics_id']) or {}
            adopted_logistics=store.get('snapshot',claim.get('logistics_snapshot') or '') or {}
            transport=lambda source:set(tuple(t) for t in source.get('identifiers',[]) if t[0] in ('waybill','approval'))
            if not logistics or logistics.get('invalid') or not adopted_logistics or transport(logistics)!=transport(adopted_logistics):
                issues.append('本票国际物流运输标识变化或失效，请重新核对运费明细')
            source=store.get('source',claim['source_id'])
            if not source or not source.get('approved') or source.get('invalid') or source.get('kind')!='expense':
                issues.append('已采用运费来源缺失、未批准或已失效')
            elif source['snapshot']!=claim['source_snapshot']:
                issues.append('已采用运费来源已更新，请核对本票新明细')
    return {'policy':POLICY,'selected':bool(records) or bool(saved.get('recovery_pending')),'claims':records,'available':bool(records) and not issues,
            'issues':sorted(set(issues)),'revision':saved.get('revision') or digest(POLICY,batch_name,[]),'epoch':saved.get('epoch'),
            'version':version.get('name'),'historical':historical,'packing_review_id':saved.get('packing_review_id')}


def rule_for(claim):
    return {'rule_code':'settled_line_'+claim['id'][:24],'logical_fee_key':'settled_line_'+claim['id'][:24],
        'expense_category':claim.get('label') or '国际运费','amount':claim['amount'],'currency':claim['currency'],
        'source_binding_id':claim['id'],'source_snapshot':claim['source_snapshot'],'covered_scopes':'freight',
        'is_final':1,'is_enabled':1,'is_active':1,'amount_status':'ACTUAL','allocation_basis':'gross_weight',
        'scope_type':'ALL_ITEMS','remark':'已审批账单明细；付款状态需凭证核实'}


def refresh_item_contexts(store,ledger,batch_name,version_name):
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    ctx=resolve_source_context(batch_name,version_name,store=store,ledger=ledger)
    version=ledger.get('version',version_name);metadata=row_meta(version)
    metadata['effective_logistics_source']=ctx
    ledger.put('version',version_name,{'extra_json':dumps(metadata)})
    for item in ledger.rows('item',batch=batch_name,version=version_name):
        meta=row_meta(item);meta['effective_logistics_source']=ctx
        ledger.put('item',item['name'],{'extra_json':dumps(meta),**{k:0 for k in DERIVED_FIELDS}})
    return ctx


def confirm(store,ledger,batch_name,version_name,candidate_id,candidate_revision,line_ids,actor,*,
            replace_claim_ids=None,expected_revision=None,reason='',negative_confirmed=False):
    replace_claim_ids=list(replace_claim_ids or [])
    if not isinstance(line_ids,list) or not line_ids or len(line_ids)>200 or len(set(line_ids))!=len(line_ids):
        raise ValueError(_('请选择 1 至 200 条不重复的本票费用明细'))
    with store.atomic():
        store.get('state','match_lock',lock=True)
        c=store.get('freight_candidate',candidate_id,lock=True)
        if not c or c['revision']!=candidate_revision or c['status']=='rejected': raise ValueError(_('候选已变化或被否决，请刷新'))
        maps=store.find('batch_map',batch=batch_name,source_id=c['logistics_id'])
        if not maps: raise ValueError(_('候选不属于当前批次'))
        batch=ledger.get('batch',batch_name,lock=True)
        if batch['current_version']!=version_name: raise ValueError(_('成本版本已变化，请刷新'))
        payment_claims=store.find('payment_claim',batch=batch_name,version=version_name,status='active')
        if payment_claims:
            from overseas_costing.services.transport_fee_service import primary_freight_definition
            freight_key=primary_freight_definition(batch.get('transport_mode'))['logical_fee_key']
            if any(row.get('logical_fee_key')==freight_key for row in payment_claims):
                raise ValueError(_('新旧运费认领不能混用，请通过统一付款流程更正或撤销'))
        source=store.get('source',c['expense_id'],lock=True);logistics=store.get('source',c['logistics_id'],lock=True)
        if not source or source['snapshot']!=c['expense_snapshot'] or logistics['snapshot']!=c['logistics_snapshot']:
            raise ValueError(_('来源快照已变化，请重新核对'))
        if not source['approved'] or source['invalid'] or source['kind']!='expense' or logistics['invalid'] or source['corp']!=logistics['corp']:
            raise ValueError(_('费用来源未批准、失效或企业不符'))
        if set(line_ids)-set(c['line_ids']): raise ValueError(_('费用明细不属于当前候选'))
        previous=context(store,ledger,batch_name,version_name)
        if replace_claim_ids and (not reason.strip() or expected_revision!=previous['revision']):
            raise ValueError(_('更正需填写原因并核对当前采用版本'))
        old_claims={r['id']:r for r in previous['claims']}
        if set(replace_claim_ids)-set(old_claims): raise ValueError(_('待更正费用不属于本票'))
        selected=[]
        for line_id in line_ids:
            line=store.get('freight_line',line_id,lock=True)
            if not line or line['source_id']!=source['id'] or line['snapshot']!=source['snapshot']: raise ValueError(_('明细已失效'))
            from .freight_lines import matching_lines
            verified=matching_lines(logistics,[line])
            if any(r.get('identifier_conflict') for r in verified) or (line.get('waybill') or line.get('approval_no')) and not verified:
                raise ValueError(_('费用运单与本票审批标识不一致'))
            if line.get('ambiguous') or line.get('scope')!='freight': raise ValueError(_('费用性质或重复明细待核对，不能直接采用'))
            if Decimal(line['amount'])<0 and not negative_confirmed: raise ValueError(_('请确认负数冲抵费用'))
            claim_id=digest(POLICY,c['logistics_id'],source['id'],line['line_key'])
            prior_claim=old_claims.get(claim_id)
            if prior_claim and any(prior_claim.get(k)!=v for k,v in {'source_snapshot':source['snapshot'],'line_id':line_id,'logistics_snapshot':logistics['snapshot']}.items()) and (claim_id not in replace_claim_ids or not reason.strip() or expected_revision!=previous['revision']):
                raise ValueError(_('已采用费用发生变化，请选择旧采用记录并填写更正原因'))
            occupied=store.find('freight_claim',charge_key=line['charge_key'])
            payment_occupied=[o for o in store.find('payment_claim',source_id=source['id'])
                              if o.get('status')=='active' and o.get('source_line_id')==line_id]
            if payment_occupied or any(o['id']!=claim_id and o['id'] not in replace_claim_ids for o in occupied):
                raise ValueError(_('这笔费用已采用，不能重复计费'))
            claim={'id':claim_id,'batch':batch_name,'logistics_id':c['logistics_id'],'source_id':source['id'],
                'line_id':line_id,'source_snapshot':source['snapshot'],'logistics_snapshot':logistics['snapshot'],'charge_key':line['charge_key'],
                'amount':line['amount'],'currency':line['currency'],'label':line['label'],'evidence':line['evidence'],
                'waybill':line['waybill'],'approval_no':source['approval_no'],'actor':actor,'adopted_at':utcnow()}
            if prior_claim and prior_claim.get('manual_corrected') and claim_id not in replace_claim_ids:
                claim = deepcopy(prior_claim)
            validate_final(rule_for(claim))
            selected.append(claim)
        new_claims={k:v for k,v in old_claims.items() if k not in replace_claim_ids}
        for claim in selected: new_claims[claim['id']]=claim
        signature=lambda r:{k:r.get(k) for k in ('id','source_snapshot','logistics_snapshot','line_id','amount','currency','charge_key')}
        if old_claims and [signature(old_claims[k]) for k in sorted(old_claims)] == [signature(new_claims[k]) for k in sorted(new_claims)] and previous['available']:
            return {'status':'applied','version':version_name,'revision':previous['revision'],'claims':previous['claims'],'cached':True}
        revision=digest(POLICY,previous['revision'],[signature(new_claims[k]) for k in sorted(new_claims)])
        if previous.get('epoch'):revision=digest(revision,previous['epoch'])
        if previous['revision']==revision: return {'status':'applied','version':version_name,'revision':revision,'cached':True}
        if locked(batch):
            request={'batch_name':batch_name,'version_name':version_name,'candidate_id':candidate_id,'candidate_revision':candidate_revision,
                'line_ids':line_ids,'actor':actor,'replace_claim_ids':replace_claim_ids,'expected_revision':expected_revision,
                'reason':reason,'negative_confirmed':negative_confirmed}
            save(store,{'id':digest(POLICY,'queued',batch_name,revision),'kind':'freight_apply','status':'queued','request':request})
            return {'status':'queued','version':version_name,'message':'批次正在编辑，费用采用已排队'}
        return _write_claims(store,ledger,batch,previous,new_claims,revision,actor,reason,line_ids,replace_claim_ids)


def _write_claims(store,ledger,batch,previous,new_claims,revision,actor,reason,line_ids,replace_claim_ids):
    batch_name=batch['name']
    version=mutable_version(ledger,batch);vname=version['name']
    before={'rules':ledger.rows('rule',batch=batch_name,version=vname),'claims':previous['claims'],'version':deepcopy(version)}
    # Release only explicitly replaced claims. An invalid source never frees its line automatically.
    for cid in replace_claim_ids:
        store.sql('DELETE FROM oc_ls_freight_claim WHERE id=%s',(cid,))
    for claim in new_claims.values():
        store.put('freight_claim',{k:claim[k] for k in ('id','batch','logistics_id','source_id','line_id','charge_key')}|{'data':dumps(claim)})
    for rule in before['rules']:
        scopes=row_scopes(rule)
        if 'freight' not in scopes: continue
        if scopes-{'freight'}: raise ValueError(_('已有费用同时含运费与其他费用，请先核对覆盖范围'))
        # Keep historical rule evidence but remove it from current final selection.
        ledger.put('rule',rule['name'],{'is_enabled':0,'is_active':0,'is_final':0})
    rules=[rule_for(r) for r in new_claims.values()];round_fee_rules(rules)
    for rule in rules:
        existing=next((r for r in ledger.rows('rule',batch=batch_name,version=vname) if r.get('rule_code')==rule['rule_code']),None)
        values={**rule,'batch':batch_name,'version':vname}
        ledger.put('rule',existing['name'],values) if existing else ledger.create('rule',values)
    # Applied amounts use the same six-decimal, currency-conserving pool as the rules.
    for rule in rules: new_claims[rule['source_binding_id']]['applied_amount']=rule['amount']
    meta=row_meta(version);old=meta.get('freight_settlement') or {}
    meta['freight_settlement']={'policy':POLICY,'revision':revision,'epoch':previous.get('epoch'),'claims':list(new_claims.values()),'packing_review_id':old.get('packing_review_id'),'recovery_pending':not bool(new_claims)}
    ledger.put('version',vname,{'extra_json':dumps(meta),'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
    ledger.put('batch',batch_name,{'status':'Dirty','confirm_status':'Pending','is_locked':0})
    ctx=refresh_item_contexts(store,ledger,batch_name,vname)
    app={'id':digest(POLICY,batch_name,vname,revision),'batch':batch_name,'version':vname,'revision':revision,
         'claims':list(new_claims.values()),'before':before,'source_context':ctx,'actor':actor,'reason':reason,'applied_at':utcnow()}
    store.insert('freight_application',{k:app[k] for k in ('id','batch','version','revision')}|{'data':dumps(app)})
    store.audit(batch_name,'freight_lines_adopted',actor,revision=revision,reason=reason,line_ids=line_ids,version=vname)
    return {'status':'applied','version':vname,'revision':revision,'claims':list(new_claims.values())}


def amend(store, ledger, batch_name, version_name, claim_id, expected_revision, action, actor, *,
          reason='', amount=None, candidate_id=None, candidate_revision=None, line_ids=None, negative_confirmed=False):
    """Change only the human adoption; archived statements are immutable."""
    if action not in ('amount', 'replace', 'revoke') or not str(reason).strip():
        raise ValueError(_('请选择更正操作并填写核对原因'))
    request = dict(batch_name=batch_name, version_name=version_name, claim_id=claim_id,
                   expected_revision=expected_revision, action=action, actor=actor, reason=str(reason).strip(),
                   amount=amount, candidate_id=candidate_id, candidate_revision=candidate_revision,
                   line_ids=line_ids, negative_confirmed=negative_confirmed)
    operation_id = digest('freight-amend-1', request)
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        batch = ledger.get('batch', batch_name, lock=True) or {}
        previous = context(store, ledger, batch_name, batch.get('current_version'))
        done = store.get('state', operation_id) or {}
        if done.get('status') == 'completed':
            result = done['result']
            if result['version'] == batch.get('current_version') and result['revision'] == previous['revision']:
                return {**result, 'cached': True}
            raise ValueError(_('这次更正已处理，当前费用又有变化，请刷新'))
        if batch.get('current_version') != version_name or previous['revision'] != expected_revision:
            raise ValueError(_('费用或成本版本已变化，请刷新后更正'))
        claims = {c['id']:deepcopy(c) for c in previous['claims']}
        if claim_id not in claims:
            raise ValueError(_('待更正费用不属于本票当前采用记录'))
        if action == 'amount':
            claim = claims[claim_id]
            source = store.get('source', claim['source_id'], lock=True) or {}
            logistics = store.get('source', claim['logistics_id'], lock=True) or {}
            if not source.get('approved') or source.get('invalid') or source.get('snapshot') != claim['source_snapshot'] or logistics.get('invalid') or logistics.get('snapshot') != claim['logistics_snapshot']:
                raise ValueError(_('原费用来源已变化或失效，请先核对新证据'))
            try:
                value = Decimal(str(amount))
                if not value.is_finite() or abs(value) >= Decimal('1000000000000') or value != value.quantize(Decimal('.000001')):
                    raise ValueError()
            except (InvalidOperation, ValueError, TypeError):
                raise ValueError(_('更正金额须是有效数字，最多六位小数'))
            if value < 0 and not negative_confirmed:
                raise ValueError(_('请确认负数冲抵费用'))
            claim.update(original_amount=claim.get('original_amount', claim['amount']), amount=format(value.normalize(), 'f'),
                         manual_corrected=True, correction_reason=str(reason).strip(), corrected_by=actor, corrected_at=utcnow())
            validate_final(rule_for(claim))
        elif action == 'revoke':
            del claims[claim_id]
        if locked(batch):
            save(store, {'id':operation_id, 'kind':'freight_amend', 'status':'queued', 'request':request})
            return {'status':'queued','version':version_name,'message':'批次正在编辑，费用更正已排队'}
        if action == 'replace':
            result = confirm(store, ledger, batch_name, version_name, candidate_id, candidate_revision, line_ids, actor,
                             replace_claim_ids=[claim_id], expected_revision=expected_revision, reason=reason,
                             negative_confirmed=negative_confirmed)
        else:
            revision = digest(POLICY, operation_id, claims)
            result = _write_claims(store,ledger,batch,previous,claims,revision,actor,reason,[],[claim_id] if action=='revoke' else [])
        if result['status'] == 'applied':
            save(store, {'id':operation_id, 'kind':'freight_amend', 'status':'completed', 'request':request, 'result':result})
            store.audit(batch_name,'freight_'+action,actor,claim_id=claim_id,reason=str(reason),before=previous['claims'],after=result.get('claims',[]),version=result['version'])
        return result


def blockers(store,ledger,batch_name,version_name,for_calculation=False):
    ctx=context(store,ledger,batch_name,version_name,live=True)
    from .payment_adoption import payment_claim_blockers, payment_freight_adoption_status
    payment_issues=payment_claim_blockers(store,ledger,batch_name,version_name)
    payment_freight=payment_freight_adoption_status(store,ledger,batch_name,version_name)
    payment_issues+=payment_freight['issues']
    packing_issues=['物料或数量变化后，保留的装箱重量、体积待核对'] if any(row_meta(i).get('settlement_packing_review') for i in ledger.rows('item',batch=batch_name,version=version_name)) else []
    from .packing_selection import scope_blockers
    packing_issues += scope_blockers(ledger,batch_name,version_name)
    if not ctx['selected']:
        missing=[] if for_calculation or payment_freight['selected'] else ['尚未采用审批通过的本票实际运费']
        return sorted(set(payment_issues + packing_issues + missing))
    if ctx['issues']: return sorted(set(payment_issues + ctx['issues']))
    if not ctx['claims']: return sorted(set(payment_issues + ['本票实际运费待核对，不能恢复旧暂估']))
    rules=ledger.rows('rule',batch=batch_name,version=ctx['version'])
    issues=list(payment_issues + packing_issues)
    for claim in ctx['claims']:
        matches=[r for r in rules if r.get('is_final') and r.get('source_binding_id')==claim['id'] and r.get('source_snapshot')==claim['source_snapshot']]
        if len(matches)!=1 or str(matches[0].get('currency'))!=claim['currency'] or Decimal(str(matches[0].get('amount') or 0))!=Decimal(claim.get('applied_amount',claim['amount'])):
            issues.append('当前费用与采用明细不一致，请重新核对')
    return sorted(set(issues))


def source_updated(store,ledger,source_id,actor='sync'):
    queued=False
    batches={r['batch'] for r in store.find('freight_claim',source_id=source_id)}
    batches.update(r['batch'] for r in store.find('packing_review',source_id=source_id,status='applied'))
    batches.update(r['batch'] for r in store.find('batch_map',source_id=source_id))
    for batch_name in sorted(batches):
        with store.atomic():
            store.get('state','match_lock',lock=True)
            batch=ledger.get('batch',batch_name,lock=True)
            if not batch: continue
            claims=context(store,ledger,batch_name,batch['current_version'])['claims']
            source=store.get('source',source_id)
            current_version=ledger.get('version',batch['current_version']);metadata=row_meta(current_version)
            review=store.get('packing_review',(metadata.get('freight_settlement') or {}).get('packing_review_id') or '')
            saved_packing=(metadata.get('effective_logistics_source') or {}).get('packing') or {}
            changed_baseline=saved_packing.get('root_source_id')==source_id and (saved_packing.get('source_snapshot')!=source['snapshot'] or source['invalid'])
            changed_packing=changed_baseline or review and (review['source_id']==source_id and (review['source_snapshot']!=source['snapshot'] or source['invalid'])
                or source['kind']=='logistics' and review['logistics_snapshot']!=source['snapshot'])
            if not changed_packing and not any(c['source_id']==source_id and (c['source_snapshot']!=source['snapshot'] or source['invalid']) for c in claims): continue
            if (metadata.get('freight_refresh_snapshots') or {}).get(source_id)==source['snapshot']:continue
            if locked(batch):
                queued=True
                save(store,{'id':digest(POLICY,'refresh',batch_name,source['snapshot']),'kind':'freight_refresh','status':'queued','source_id':source_id})
                continue
            version=mutable_version(ledger,batch)
            metadata=row_meta(version);metadata.setdefault('freight_refresh_snapshots',{})[source_id]=source['snapshot']
            ledger.put('version',version['name'],{'extra_json':dumps(metadata),'calculated_at':None,'summary_snapshot_json':'{}'})
            ledger.put('batch',batch_name,{'status':'Dirty','confirm_status':'Pending'})
            store.audit(batch_name,'freight_source_pending',actor,source_id=source_id,source_snapshot=source['snapshot'],version=version['name'])
    return {'status':'queued' if queued else 'applied'}
