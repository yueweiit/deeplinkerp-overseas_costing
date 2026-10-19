"""Local RPC projections and background integration for shipment freight."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import re

from . import freight_adoption as adoption, freight_matching as matching, freight_packing as packing
from .model import digest,dumps
from .application import row_meta
from .ai_matching import save
from .jobs import utcnow


def financial_summary(source):
    from .runtime import source_summary
    # The full payment statement is server evidence, not a batch-readable payload.
    allowed={'id','corp','instance','approval_no','kind','status','approved','invalid','title','process_code',
             'amount','currency','source_updated_at','snapshot','open_url'}
    return {k:v for k,v in source_summary(source).items() if k in allowed}


def candidate_view(store,candidate,transport_mode='',ledger=None):
    from decimal import Decimal, InvalidOperation
    from .freight_lines import logical_fee_key
    from .runtime import source_summary
    source=store.get('source',candidate['expense_id'])
    payment_claims=[claim for claim in store.find('payment_claim',source_id=source['id']) if claim.get('status')=='active']
    if ledger:
        payment_claims=[claim for claim in payment_claims
            if not (ledger.get('batch',claim.get('batch')) or {}).get('current_version')
            or (ledger.get('batch',claim.get('batch')) or {}).get('current_version')==claim.get('version')]
    lines=[]
    for lid in candidate['line_ids']:
        line=store.get('freight_line',lid)
        if not line:continue
        occupied=store.find('freight_claim',charge_key=line['charge_key'])
        payment_occupied=[claim for claim in payment_claims if claim.get('source_line_id')==lid]
        lines.append({**line,'logical_fee_key':logical_fee_key(line.get('scope'),transport_mode),
            'available':source['approved'] and not source['invalid'] and not line.get('ambiguous') and line['scope']=='freight'
            and not any(c['logistics_id']!=candidate['logistics_id'] for c in occupied+payment_occupied),
            'adopted':any(c['logistics_id']==candidate['logistics_id'] and (c.get('line_id')==lid or c.get('source_line_id')==lid)
                          for c in occupied+payment_occupied)})
    all_claims=store.find('freight_claim',source_id=source['id'])
    all_claims += payment_claims
    active_claims=[claim for claim in all_claims if claim.get('source_snapshot')==source['snapshot'] and claim.get('currency')==source.get('currency')]
    stale_claims=[claim for claim in all_claims if claim not in active_claims]
    try:
        claimed=sum((abs(Decimal(str(claim['amount']))) for claim in active_claims),Decimal('0'))
        stale_totals={}
        for claim in stale_claims:
            currency=str(claim.get('currency') or '')
            stale_totals[currency]=stale_totals.get(currency,Decimal('0'))+abs(Decimal(str(claim['amount'])))
        stale_claimed=stale_totals.get(str(source.get('currency') or ''),Decimal('0')) if set(stale_totals)<={str(source.get('currency') or '')} else None
        total=Decimal(str(source['amount'])) if source.get('amount') is not None else None
        remaining=total-claimed if total is not None and not stale_claims else None
    except (InvalidOperation,ValueError,TypeError):
        claimed=stale_claimed=remaining=None;stale_totals={}
    claim_status='stale_review' if stale_claims else 'current'
    from .payment_evidence import candidate_attachment_summaries
    attachments = candidate_attachment_summaries(source, lines, transport_mode)
    return {**candidate,'source_revision':candidate.get('source_revision') or candidate.get('expense_snapshot'),
            'expense':financial_summary(source),'lines':lines,'approval_total':source.get('amount'),'approval_currency':source.get('currency'),
            'attachments':attachments,
            'claimed':str(claimed) if claimed is not None else None,'remaining':str(remaining) if remaining is not None else None,
            'active_claimed_amount':str(claimed) if claimed is not None else None,
            'stale_claimed_amount':str(stale_claimed) if stale_claimed is not None else None,
            'stale_claimed_by_currency':{currency:str(amount) for currency,amount in stale_totals.items()},
            'remaining_amount':str(remaining) if remaining is not None else None,'claim_status':claim_status,
            'packing_available':any(l.get('cargo_text') for l in lines) or bool(source.get('goods')) or any(d.get('tables') for d in source.get('documents') or [])}


def _payment_template(source):
    """Classify only the approved payment workflow families used by this product."""

    from overseas_costing.services.material_ai_payment_match import payment_workflow_template
    return payment_workflow_template(source)


def _payment_line_summary(line):
    from .freight_lines import packing_for_line
    evidence=line.get('evidence') or {}
    packing=packing_for_line(line)
    return {
        'line_id':str(line.get('id') or ''),
        'approval_no':str(line.get('approval_no') or ''),
        'waybill':str(line.get('waybill') or ''),
        'freight':{
            'amount':line.get('amount'),
            'currency':str(line.get('currency') or ''),
        } if line.get('amount') not in (None,'') else None,
        'packing':{
            key:value for key,value in packing.items()
            if key in {'material_code_hints','chargeable_weight_kg','gross_weight_kg',
                       'package_count','dimensions_cm','volume_m3'}
        },
        'evidence':{
            key:evidence.get(key) for key in ('file_name','sheet','row')
            if evidence.get(key) not in (None,'')
        },
    }


def payment_source_scope(store,candidates,version_name,items=None,requested_refs=None):
    """Small browser projection for choosing payment processes, never fee adoption."""

    from overseas_costing.services import material_ai_payment_match
    rows=[]
    for candidate in candidates:
        if (str(candidate.get('status') or '').lower() not in {'pending','confirmed'}
                or candidate.get('issues')):
            continue
        source=store.get('source',candidate.get('expense_id')) or {}
        template=_payment_template(source)
        if not template or not source.get('approved') or source.get('invalid'):
            continue
        summaries=[]
        for line_id in candidate.get('line_ids') or []:
            line=store.get('freight_line',line_id) or {}
            if line.get('source_id')==source.get('id') and line.get('snapshot')==source.get('snapshot'):
                summaries.append(_payment_line_summary(line))
        strength='STRONG' if material_ai_payment_match._strong(candidate) else 'WEAK'
        parsed_summary=(summaries[0] if len(summaries)==1 else {'lines':summaries})
        rows.append({
            'candidate_id':str(candidate.get('id') or ''),
            'revision':str(candidate.get('revision') or ''),
            'workflow_template':template,
            'title':str(source.get('title') or template),
            'approval_no':str(source.get('approval_no') or source.get('instance') or ''),
            'match_strength':strength,
            'match_reason':str(candidate.get('reason') or ('审批号或运单号精确匹配' if strength=='STRONG' else '需要人工确认范围')),
            'parsed_summary':parsed_summary,
        })
    rows.sort(key=lambda row:(0 if row['match_strength']=='STRONG' else 1,row['approval_no'],row['candidate_id']))
    strong=[row for row in rows if row['match_strength']=='STRONG']
    if requested_refs is not None:
        if not isinstance(requested_refs,list):raise ValueError(_('支付来源选择格式不正确'))
        available={(row['candidate_id'],row['revision'],str(version_name or '')):row for row in rows}
        keys=[]
        for reference in requested_refs:
            if not isinstance(reference,dict) or set(reference)!={'candidate_id','revision','version'}:
                raise ValueError(_('支付来源选择格式不正确'))
            key=tuple(str(reference.get(name) or '') for name in ('candidate_id','revision','version'))
            if key not in available:raise ValueError(_('支付来源候选已变化，请刷新'))
            keys.append(key)
        if len(keys)!=len(set(keys)):raise ValueError(_('支付来源选择重复'))
        selected=[available[key] for key in keys]
        status='SELECTED' if selected else 'NEEDS_SELECTION' if rows else 'UNAVAILABLE'
    else:
        selected=strong if len(strong)==1 else []
        status='AUTO_MATCHED' if len(strong)==1 else 'NEEDS_SELECTION' if rows else 'UNAVAILABLE'
    for row in rows:
        row['selected']=row in selected
    material_rows=[]
    selected_lines=[summary for row in selected for summary in (
        row['parsed_summary'].get('lines') or [row['parsed_summary']]
        if isinstance(row.get('parsed_summary'),dict) else [])]
    overlay_fields=('gross_weight_kg','chargeable_weight_kg','package_count','volume_m3')
    for item in items or []:
        code=str(item.get('material_code') or '').strip().upper()
        values={field:item.get(field) for field in overlay_fields if item.get(field) not in (None,'')}
        evidence=[];overlaid=set()
        for summary in selected_lines:
            packing=summary.get('packing') or {}
            codes=[str(value or '').strip().upper() for value in packing.get('material_code_hints') or []
                   if re.fullmatch(r'[A-Z]{2,8}\d{3,}',str(value or '').strip(),re.I)]
            if len(set(codes))!=1 or code not in codes:continue
            for field in overlay_fields:
                if packing.get(field) not in (None,''):
                    values[field]=packing[field];overlaid.add(field)
            if summary.get('evidence'):evidence.append(summary['evidence'])
        material_rows.append({
            'item_name':str(item.get('name') or ''),'material_code':code,
            'product_name':str(item.get('product_name') or ''),'values':values,
            'fallback_fields':[field for field in overlay_fields if field not in overlaid],
            'evidence':evidence,
        })
    return {
        'policy':'payment-source-scope-1','version':str(version_name or ''),'status':status,
        'selected_refs':[{'candidate_id':row['candidate_id'],'revision':row['revision'],
                          'version':str(version_name or '')} for row in selected],
        'candidates':rows,
        'material_rows':material_rows,
        'message':(
            '已按审批号或运单号自动匹配唯一支付流程。'
            if status=='AUTO_MATCHED' else
            '已选择支付流程范围，AI 只会解析这些流程的本票明细。'
            if status=='SELECTED' else
            '找到多个或弱匹配支付流程，请只选择需要 AI 解析的范围。'
            if status=='NEEDS_SELECTION' else
            '未找到可用支付流程，将继续使用国际物流和采购支出资料。'
        ),
    }


def payment_source_history(store, version):
    """Safe, version-scoped summary of payment relations confirmed by AI fill."""

    metadata=row_meta(version or {})
    relations=metadata.get('material_ai_payment_matches') or []
    if not relations and isinstance(metadata.get('material_ai_payment_match'),dict):
        relations=[metadata['material_ai_payment_match']]
    result=[];seen=set()
    for relation in relations:
        if not isinstance(relation,dict):continue
        identity=(str(relation.get('candidate_id') or ''),str(relation.get('expense_id') or ''))
        if not all(identity) or identity in seen:continue
        seen.add(identity)
        source=store.get('source',identity[1]) or {}
        result.append({
            'title':str(source.get('title') or '支付流程'),
            'approval_no':str(source.get('approval_no') or source.get('instance') or ''),
            'workflow_template':_payment_template(source),
            'confirmed_by':str(relation.get('confirmed_by') or ''),
            'confirmed_at':str(relation.get('confirmed_at') or ''),
        })
    return result


def batch_status(store,ledger,batch_name,version_name=None):
    from .runtime import source_summary
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    batch=ledger.get('batch',batch_name) or {};vname=version_name or batch.get('current_version')
    version=ledger.get('version',vname) or {}
    if version.get('batch')!=batch_name:raise ValueError(_('版本不属于当前批次'))
    historical=vname!=batch.get('current_version')
    maps=store.find('batch_map',batch=batch_name)
    base={'ok':True,'freight_mode':True,'mapped':bool(maps),'historical':historical,'viewed_version':vname,
          'transport_mode':batch.get('transport_mode'),
          'confirm_status':batch.get('confirm_status'),'writeback_status':batch.get('writeback_status'),
          'binding':None,'candidates':[],
          'payment_candidates':[],'payment_rejected_candidates':[],
          'matching':{'status':'not_started'},'payment_matching':{'status':'not_started'},
          'sync':store.get('state','sync') or {},'health':store.get('state','health') or {},
          'payment_source_scope':payment_source_scope(store,[],vname,[])}
    from .payment_adoption import public_payment_context
    base.update(public_payment_context(store,ledger,batch_name,vname))
    if not maps:return base
    logistics=store.get('source',maps[0]['source_id'])
    current_candidates=[
        candidate for candidate in matching.candidates(store,logistics['id'])
        if candidate.get('line_scope_policy')==matching.CANDIDATE_SCOPE_POLICY
    ] if not historical else []
    candidates=[candidate_view(store,c,batch.get('transport_mode'),ledger) for c in current_candidates if c['status']!='rejected']
    rejected=[candidate_view(store,c,batch.get('transport_mode'),ledger) for c in current_candidates if c['status']=='rejected']
    from . import payment_ai_matching
    base.update(logistics=source_summary(logistics),matching=matching.rule_status(store,logistics['id']) if not historical else {'status':'historical'},
        payment_matching=payment_ai_matching.status(store,logistics['id'],current_version=lambda _batch:batch.get('current_version')) if not historical else {'status':'historical'},
        candidates=candidates,payment_candidates=candidates,payment_rejected_candidates=rejected,
        freight=adoption.context(store,ledger,batch_name,vname),source_context=resolve_source_context(batch_name,vname,store=store,ledger=ledger))
    base['payment_source_scope']=payment_source_scope(
        store,current_candidates if not historical else [],vname,
        ledger.rows('item',batch=batch_name,version=vname),
    )
    ctx=base['source_context'];review=store.get('packing_review',base['freight'].get('packing_review_id') or '')
    base['packing']={'status':'adopted' if review else 'unverified','message':'已独立采用装箱变更；原始资料保留在操作记录' if review else '装箱沿用当前资料；尚未确认是否有变更',
        'review_id':(review or {}).get('id'),'source_context':ctx.get('packing') or ctx}
    base['packing_checks']=[{**{k:i.get(k) for k in ('name','material_code','product_name','quantity','unit','gross_weight_kg','volume_m3')},'revision':packing.item_fingerprint([i])} for i in ledger.rows('item',batch=batch_name,version=vname) if row_meta(i).get('settlement_packing_review')]
    base['blocking_reasons']=adoption.blockers(store,ledger,batch_name,vname)
    if review and not ctx.get('available'):base['blocking_reasons'].append('已采用装箱来源更新或失效，请重新核对')
    base['payment_source_history']=payment_source_history(store,version)
    audit_rows=store.find('audit',binding_id=batch_name,limit=50)
    for audit_row in audit_rows:
        if audit_row.get('action')!='material_ai_payment_match_confirmed':continue
        source=store.get('source',audit_row.get('expense_id')) or {}
        audit_row['source_approval_no']=str(source.get('approval_no') or source.get('instance') or '')
        audit_row['source_title']=str(source.get('title') or '支付流程')
    base['audit']=audit_rows
    legacy=store.find('binding',logistics_id=logistics['id'])
    if legacy and not row_meta(version).get('freight_settlement'):
        base['legacy_binding']={**legacy[0],'expense':financial_summary(store.get('source',legacy[0]['expense_id']))}
        base['message']='已有整单关联保留历史，请按本票费用明细核对后迁移；旧金额不会自动扩大到其他票。'
    return base


def manual_candidate(store,ledger,batch_name,expense_id,reason,expected_revision=None):
    with store.atomic():
        store.get('state','match_lock',lock=True)
        maps=store.find('batch_map',batch=batch_name)
        if not maps:raise ValueError(_('请先开始本票匹配'))
        candidate_id=digest(matching.POLICY,maps[0]['source_id'],expense_id)
        store.get('freight_candidate',candidate_id,lock=True)
        logistics=store.get('source',maps[0]['source_id'],lock=True);expense=store.get('source',expense_id,lock=True)
        if not expense or expense['corp']!=logistics['corp'] or expense['invalid'] or not expense.get('approved') or expense['kind']!='expense':
            raise ValueError(_('来源企业、类型或状态不符'))
        lines=matching.current_lines(store,expense)
        own=matching.shipment_candidate_lines(logistics,lines)
        candidate=matching.save_candidate(store,logistics,expense,own,'manual',reason,expected_revision=expected_revision)
    return candidate_view(store,candidate)


def reopen_candidate(store,ledger,batch_name,candidate_id,revision,reason,actor):
    if not str(reason or '').strip():raise ValueError(_('请填写重新纳入原因'))
    with store.atomic():
        store.get('state','match_lock',lock=True)
        candidate=store.get('freight_candidate',candidate_id,lock=True)
        maps=store.find('batch_map',batch=batch_name)
        if not candidate or not any(row['source_id']==candidate.get('logistics_id') for row in maps):
            raise ValueError(_('候选不属于当前批次'))
        if candidate.get('revision')!=revision:raise ValueError(_('候选已变化，请刷新后重试'))
        if candidate.get('status')!='rejected':raise ValueError(_('只能重新纳入已否决候选'))
        old_revision=candidate['revision']
        candidate.update(status='reopened',method='reopened',reason=str(reason).strip(),
                         revision=digest('payment-candidate-reopened',old_revision,str(reason).strip(),actor))
        store.put('freight_candidate',{k:candidate[k] for k in ('id','logistics_id','expense_id','status')}|{'data':dumps(candidate)})
        store.audit(batch_name,'payment_candidate_reopened',actor,candidate_id=candidate_id,old_revision=old_revision,
                    new_revision=candidate['revision'],reason=str(reason).strip())
        return candidate


def decide_payment_candidate(store,ledger,batch_name,version_name,candidate_id,revision,action,reason,actor,
                             *,lease_check=None,edit_token=None,expected_modified=None):
    """CAS a unified-payment candidate decision under the batch edit lease.

    The legacy freight endpoints intentionally keep their historical contract.  New
    payment clients must use this path so a stale browser cannot reject or reopen a
    candidate after the batch version or its trusted source relation has changed.
    """
    reason=str(reason or '').strip()
    if action not in ('reject','reopen') or not reason:
        raise ValueError(_('请选择候选操作并填写核对依据'))
    if not lease_check or not edit_token or expected_modified in (None,''):
        raise ValueError(_('候选状态修改需要有效编辑租约'))
    with store.atomic():
        store.get('state','match_lock',lock=True)
        lease_check(batch_name,edit_token=edit_token,expected_modified=expected_modified)
        batch=ledger.get('batch',batch_name,lock=True) or {}
        version=ledger.get('version',version_name,lock=True) or {}
        if (not batch or version.get('batch')!=batch_name
                or batch.get('current_version')!=version_name):
            raise ValueError(_('当前版本已变化，请刷新后重试'))
        if (version.get('status') in ('Confirmed','Archived')
                or batch.get('confirm_status')=='Confirmed'
                or batch.get('writeback_status')=='Success' or batch.get('is_locked')):
            raise ValueError(_('历史、已确认、已回写或锁定版本不可修改'))

        candidate=store.get('freight_candidate',candidate_id,lock=True)
        if not candidate or candidate.get('revision')!=revision:
            raise ValueError(_('付款候选已变化，请刷新后重试'))
        maps=store.find('batch_map',batch=batch_name,source_id=candidate.get('logistics_id'))
        if not maps:
            raise ValueError(_('付款候选不属于当前批次'))
        for mapping in maps:
            store.get('batch_map',mapping['id'],lock=True)
        logistics=store.get('source',candidate.get('logistics_id'),lock=True) or {}
        expense=store.get('source',candidate.get('expense_id'),lock=True) or {}
        if (logistics.get('kind')!='logistics' or logistics.get('invalid')
                or expense.get('kind')!='expense' or expense.get('invalid') or not expense.get('approved')
                or logistics.get('corp')!=expense.get('corp')
                or candidate.get('logistics_snapshot')!=logistics.get('snapshot')
                or candidate.get('expense_snapshot')!=expense.get('snapshot')):
            raise ValueError(_('付款候选来源关系已变化，请重新匹配'))
        for line_id in candidate.get('line_ids') or []:
            line=store.get('freight_line',line_id,lock=True) or {}
            if line.get('source_id')!=expense.get('id') or line.get('snapshot')!=expense.get('snapshot'):
                raise ValueError(_('付款候选明细已变化，请重新匹配'))

        status=candidate.get('status')
        if action=='reopen' and status!='rejected':
            raise ValueError(_('只能重新纳入已否决候选'))
        if action=='reject' and status not in ('pending','conflict','reopened'):
            raise ValueError(_('当前付款候选不可否决'))
        old_revision=candidate['revision']
        if action=='reopen':
            candidate.update(status='reopened',method='reopened',reason=reason,reopened_by=actor,
                             reopened_at=utcnow(),revision=digest('payment-candidate-reopened',old_revision,reason,actor))
        else:
            candidate.update(status='rejected',rejection_reason=reason,rejected_by=actor,
                             rejected_at=utcnow(),revision=digest('payment-candidate-rejected',old_revision,reason,actor))
        store.put('freight_candidate',{k:candidate[k] for k in ('id','logistics_id','expense_id','status')}|{'data':dumps(candidate)})
        store.audit(batch_name,f'payment_candidate_{action}ed',actor,candidate_id=candidate_id,
                    version=version_name,old_revision=old_revision,new_revision=candidate['revision'],reason=reason)
        touched=ledger.put('batch',batch_name,{'status':batch.get('status')}) or ledger.get('batch',batch_name,lock=True) or batch
        return {'candidate':candidate,'batch_modified':str(touched.get('modified') or '')}


def resume(store,ledger):
    # Durable per-request outcomes; no repeated source reads or automatic candidate confirmation.
    for record in store.find('state'):
        if record.get('status')!='queued' or record.get('kind') not in ('freight_apply','packing_apply','freight_refresh','freight_amend','packing_replace'):continue
        try:
            if record['kind']=='freight_refresh':
                result=adoption.source_updated(store,ledger,record['source_id'])
            else:
                if record['kind']=='freight_amend':call=adoption.amend
                elif record['kind']=='packing_replace':
                    from .packing_selection import confirm_selection
                    call=confirm_selection
                else:call=adoption.confirm if record['kind']=='freight_apply' else packing.confirm
                result=call(store,ledger,**record['request'])
            if result['status']=='queued':continue
            record.update(status='completed',result=result);save(store,record)
        except Exception as exc:
            record.update(status='failed',error=str(exc)[:500]);save(store,record)


def history_status(store,job_id=None):
    from .runtime import source_summary
    job=store.get('job',job_id or (store.get('state','latest_job') or {}).get('job_id',''))
    logistics=[s for s in store.find('source',kind='logistics') if not s['invalid']]
    rows=[];counts={'pending':0,'conflict':0,'confirmed':0,'rejected':0,'reopened':0,'unmatched':0}
    matched=set();adopted=set()
    for source in logistics:
        cs=matching.candidates(store,source['id']);active=[c for c in cs if c['status']!='rejected']
        for c in cs:counts[c['status']]+=1
        if active:matched.add(source['id'])
        if store.find('freight_claim',logistics_id=source['id']):adopted.add(source['id'])
        if active:
            mapping=store.find('batch_map',source_id=source['id'])
            rows.append({'logistics':source_summary(source),'batch':mapping[0]['batch'] if len(mapping)==1 else None,
                         'candidate_count':len(active),'status':'conflict' if any(c['status']=='conflict' for c in active) else 'pending'})
    counts.update(confirmed=len(adopted),unmatched=len(logistics)-len(matched))
    coverage=[]
    groups={}
    for source in store.find('source',kind='expense'):
        key=(source['corp'],source['process_code'])
        group=groups.setdefault(key,{'template_name':source.get('title') or source['process_code'],'local_sources':0,'qualified':0,'matched_shipments':set(),'missing_attachments':0})
        group['local_sources']+=1
        if source['invalid']:continue
        group['qualified']+=1
        group['matched_shipments'].update(c['logistics_id'] for c in store.find('freight_candidate',expense_id=source['id']) if c['logistics_id'] in matched and c['status']!='rejected')
        group['missing_attachments']+=sum(1 for a in source.get('attachments') or [] if a.get('archive_status')!='archived' and not a.get('retired_at'))
    for group in groups.values():coverage.append({**group,'matched_shipments':len(group['matched_shipments'])})
    return {'ok':True,'freight_mode':True,'job':job,'counts':counts,'shipments':rows,'coverage':coverage,
            'sync':store.get('state','sync') or {},'health':store.get('state','health') or {},
            'failures':store.find('job_item',job_id=job['id'],status='failed',limit=50) if job else [],
            'pending_failure_count':store.count('job_item',status='failed')}


def line_evidence(store,ledger,batch_name,version_name,line_id):
    """Only a shipment-owned archived row; no upstream or full-statement disclosure."""
    batch=ledger.get('batch',batch_name) or {};version=ledger.get('version',version_name) or {}
    if version.get('batch')!=batch_name:raise ValueError(_('版本不属于本票'))
    claims=(row_meta(version).get('freight_settlement') or {}).get('claims') or []
    allowed={c['line_id'] for c in claims}
    mappings=store.find('batch_map',batch=batch_name)
    if version_name==batch.get('current_version') and mappings:
        for candidate in matching.candidates(store,mappings[0]['source_id']):
            if candidate['status']!='rejected':allowed.update(candidate['line_ids'])
    if line_id not in allowed:raise ValueError(_('此账单行不属于本票可查看的证据'))
    line=store.get('freight_line',line_id)
    if not line:raise ValueError(_('本地证据行不存在，请核对归档状态'))
    from .runtime import source_summary
    source=store.get('snapshot',line['snapshot']) or {}
    return {**{k:line.get(k) for k in ('amount','currency','waybill','approval_no','label','billing_weight','cargo_text','evidence')},
            'source':financial_summary(source),'source_snapshot':line['snapshot']}
