"""Adopt human-reviewed current-source tables; never mutate archived source facts.

Callers supply rows reparsed from signed local material previews or a fenced AI
draft, never an arbitrary browser row list. `full_table` is a server assertion;
`complete` is the separate human confirmation. Both are required for retirement.
"""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from .model import digest, dumps
from .jobs import utcnow


def _pointer(binding_id, snapshot):
    return digest('reviewed_source_current', binding_id, snapshot)


def resolve_reviewed_source(store, source, binding, version_name=None):
    if not source or not binding or not store:
        return source
    result = deepcopy(source)
    if not version_name:
        from .bound_wiki_service import pending_wiki_issues
        if pending_wiki_issues(store,binding):
            return deepcopy(store.get('source',binding['expense_id']) or source)
    if version_name:
        apps = store.find('application', binding_id=binding['id'], version=version_name)
        app = max(apps, key=lambda x:str(x.get('applied_at') or ''), default={})
        review_id = app.get('review_id')
    else:
        pointer = store.get('state', _pointer(binding['id'], source.get('snapshot'))) or {}
        review_id = pointer.get('review_id')
    review = store.get('state', review_id) if review_id else None
    if not review or review.get('binding_id') != binding['id'] or review.get('source_snapshot') != source.get('snapshot'):
        return result
    if review.get('goods_complete'):
        result.update(goods=deepcopy(review['goods']), goods_complete=True)
    if review.get('fees') is not None:
        result['fees'] = deepcopy(review['fees'])
        incomplete_labels = {str(issue).removesuffix(suffix)+'明细不完整'
            for issue in source.get('fee_issues') or [] for suffix in ('未完整解析','未完整解析或行身份不唯一')
            if str(issue).endswith(suffix)}
        def resolved_fee_issue(issue):
            return (issue in incomplete_labels or issue in {'费用明细与总额未核对一致','正文与附件或多份附件存在费用明细，需核对重叠范围'}
                    or str(issue).startswith(('费用附件明细完整性待核对：','费用附件尚未完整识别：'))
                    or str(issue).endswith(('未完整解析','未完整解析或行身份不唯一'))
                    or (issue=='负数或冲抵费用待核对' and all(Decimal(row['amount'])>=0 for row in review['fees'])))
        result['fee_issues'] = [issue for issue in source.get('fee_issues') or [] if not resolved_fee_issue(issue)]
        result['issues'] = [issue for issue in source.get('issues') or [] if not resolved_fee_issue(issue)]
    result['review_id'] = review['id']
    result['raw_cost_hash'] = source.get('raw_cost_hash') or source['cost_hash']
    result['cost_hash'] = digest(result['raw_cost_hash'], review['id'])
    return result


def _decimal(value, label, *, positive=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or (positive and number <= 0):
            raise ValueError()
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(label + '须是明确的' + ('正数' if positive else '数字'))
    return format(number.normalize(), 'f')


def normalize_goods(rows, evidence):
    from collections import Counter
    from .model import identity
    identities = Counter((identity(r.get('material_code') or r.get('product_name')),identity(r.get('spec_model')),
                          identity(r.get('unit') or r.get('shipped_uom'))) for r in rows)
    result, positions, line_keys = [], set(), set()
    for row in rows:
        position = str(row.get('source_row') or row.get('source_position') or row.get('line_key') or '')
        if not position or position in positions:
            raise ValueError(_('完整清单的来源行身份必须唯一，不能按 SKU 合并行。'))
        positions.add(position)
        code, name = str(row.get('material_code') or '').strip(), str(row.get('product_name') or '').strip()
        unit = str(row.get('unit') or row.get('shipped_uom') or '').strip()
        if not (code or name) or not unit:
            raise ValueError(_('完整清单的每行需要 SKU 或品名，以及明确单位。'))
        row_identity = (identity(code or name),identity(row.get('spec_model')),identity(unit))
        native = row.get('native_line_id') or row.get('line_key')
        if not native and identities[row_identity] != 1:
            raise ValueError(_('同 SKU 重复行缺少唯一原生行标识，请先核对行归属；行号不能作为稳定身份。'))
        line_key = str(native or digest('reviewed_cargo', evidence['source_id'], evidence.get('sheet'),row_identity))
        if line_key in line_keys:
            raise ValueError(_('货物明细的最终稳定行标识必须唯一。'))
        line_keys.add(line_key)
        quantity = row.get('quantity') if row.get('quantity') not in (None, '') else row.get('actual_shipped_qty')
        physical = {}
        for key in ('actual_shipped_qty', 'gross_weight_kg','net_weight_kg','volume_m3','volume_weight_kg','chargeable_weight_kg'):
            if row.get(key) not in (None, ''):
                physical[key] = _decimal(row[key], key)
                if Decimal(physical[key]) < 0:
                    raise ValueError(_('装箱物理值不得为负数。'))
        if row.get('shipped_uom'):
            physical['shipped_uom'] = str(row['shipped_uom'])
        # Packing table unit-price columns are not merchandise-price evidence.
        price = deepcopy(row.get('merchandise_price') or {'present':False})
        if evidence.get('table_kind') == 'packing':
            price = {'present':False}
        if price.get('present') and not price.get('ambiguous'):
            price['price'] = _decimal(price.get('price'), '商品单价')
        result.append({'line_key':line_key,'native_line_id':str(native or ''),'source_table':str(evidence.get('sheet') or ''),
                       'source_position':position,'material_code':code,'product_name':name,
                       'spec_model':str(row.get('spec_model') or ''),'quantity':_decimal(quantity,'货物数量',positive=True),
                       'unit':unit,'merchandise_price':price,'reviewed_physical':physical})
    if not result:
        raise ValueError(_('没有可采用的货物行；缺少归档或空清单不能确认成功。'))
    return result


def inherit_expense_prices(goods, source):
    """A packing-only update cannot erase the same expense's merchandise price."""
    from .model import identity
    from overseas_costing.utils.field_mapper import normalize_unit
    def key(row):
        return (identity(row.get('material_code') or row.get('product_name')),identity(row.get('spec_model')),
                normalize_unit(row.get('unit') or ''))
    for row in goods or []:
        if (row.get('merchandise_price') or {}).get('present'):
            continue
        candidates=[g for g in source.get('goods') or [] if (
            g.get('line_key')==row['native_line_id'] if row.get('native_line_id') else key(g)==key(row))]
        if not candidates:
            # Body and attachment native IDs are separate namespaces. Only a
            # unique business identity can safely bridge those two sources.
            candidates=[g for g in source.get('goods') or [] if key(g)==key(row)]
        if len(candidates)>1 and any((candidate.get('merchandise_price') or {}).get('present') for candidate in candidates):
            row['merchandise_price']={'present':True,'price':None,'ambiguous':True,
                'evidence':{'reason':'当前采购支出同一物料有多个价格行，归属待核对',
                            'line_keys':[candidate.get('line_key') for candidate in candidates]}}
            continue
        if len(candidates)!=1 or key(candidates[0])!=key(row):
            continue
        price=deepcopy(candidates[0].get('merchandise_price') or {})
        if price.get('present'):
            if price.get('price_uom') and normalize_unit(price['price_uom'])!=normalize_unit(row['unit']):
                price.update(ambiguous=True,price=None)
            row['merchandise_price']=price
    return goods


def cargo_review_for_preview(trusted, context):
    """Browser-safe review of every parsed source row, independent of old items."""
    from overseas_costing.services.effective_logistics_source import public_context
    preview, source = trusted.get('preview') or {}, trusted.get('source') or {}
    rows = []
    for index,row in enumerate(preview.get('material_rows') or [],1):
        combined = {**(row.get('raw_fields') or {}),**row}
        rows.append({k:v for k,v in combined.items() if k in {
            'material_code','product_name','spec_model','quantity','unit','actual_shipped_qty','shipped_uom',
            'gross_weight_kg','net_weight_kg','volume_m3','volume_weight_kg','chargeable_weight_kg','native_line_id','line_key'}}
                    | {'source_row':row.get('source_row') or index})
    result = {'rows':rows,'complete':False,'reason':'尚未取得可信完整货物表，不能采用空清单。',
              'source_id':str(source.get('source_id') or ''),'source_kind':str(source.get('source_kind') or 'approval_attachment'),
              'source_hash':str(trusted.get('source_hash') or ''),'sheet':str(source.get('sheet_name') or ''),
              'row_count':len(rows),'source_context':public_context(context),'table_kind':'packing'}
    errors = (preview.get('validation') or {}).get('blocking') or []
    grid = trusted.get('grid') or {}
    header=int(preview.get('header_row') or 0)
    columns={column['field']:int(column['column']) for column in preview.get('columns') or []
             if column.get('field') and column.get('column')}
    consumed={int(row['source_row']) for row in rows}
    unread=[]
    if header and columns:
        from .model import norm
        from overseas_costing.services.packing_parse_service import _is_formula_total_row
        for position,cells in enumerate(grid.get('cells') or [],1):
            if position<=header or position in consumed:
                continue
            values={field:(cells[col-1].get('raw_value') if col<=len(cells) else None)
                    for field,col in columns.items() if field in {'material_code','product_name','quantity','unit'}}
            if not any(value not in (None,'') for value in values.values()):
                continue
            if any(norm(values.get(field)) in {'合计','总计','total','totals'} for field in ('material_code','product_name')):
                continue
            if _is_formula_total_row(cells,header,position):
                continue
            unread.append(position)
    if rows and grid.get('cells') and not grid.get('truncated') and not preview.get('truncated') and not errors:
        try:
            normalize_goods(rows,result)
            result.update(complete=True,reason='已读取整页货物表；请核对所有行并确认这是最终完整清单。')
        except ValueError as exc:
            result['reason']=str(exc)
    elif errors:
        result['reason']='资料仍有未解决的解析问题：'+'；'.join(str(e.get('message') or e.get('code') or '') for e in errors)
    if unread:
        result.update(complete=False,reason='完整网格仍有未解析的货物或数量行：'+','.join(map(str,unread[:20])))
    result['full_table']=result['complete']
    return result


def normalize_fees(rows, source, evidence, negative_confirmed):
    fees, positions = [], set()
    for index, row in enumerate(rows):
        position = str(row.get('source_row') or row.get('line_key') or index+1)
        if position in positions:
            raise ValueError(_('费用明细行身份必须唯一。'))
        positions.add(position)
        amount = _decimal(row.get('amount'), '费用金额')
        currency = str(row.get('currency') or '').upper()
        currency = 'RMB' if currency == 'CNY' else currency
        if currency != source.get('currency') or currency not in {'RMB','USD','MXN'}:
            raise ValueError(_('费用明细必须与采购支出整单采用同一明确币种。'))
        if Decimal(amount) < 0 and not negative_confirmed:
            raise ValueError(_('负数或冲抵明细需要本次明确确认。'))
        fees.append({'line_key':digest('reviewed_fee',evidence['source_id'],evidence.get('sheet'),position),
                     'label':str(row.get('label') or row.get('expense_category') or '物流费用'),
                     'amount':amount,'currency':currency,'source_position':position})
    if not fees or source.get('amount') is None:
        raise ValueError(_('需核对采购支出明确总额后才能采用费用明细。'))
    if sum((Decimal(x['amount']) for x in fees),Decimal(0)) != Decimal(str(source['amount'])):
        raise ValueError(_('所选费用明细合计与采购支出整单金额不一致，需补全拆分；尚未采用。'))
    return fees


def _response(store, ledger, binding, review_id, *, idempotent=False, goods_count=0, fee_count=0):
    from overseas_costing.services.effective_logistics_source import context_for_source
    applied = binding.get('application_status') in {'applied','applied_pending'}
    mapping=(store.find('batch_map',source_id=binding['logistics_id'],limit=1) or [{}])[0]
    batch=ledger.get('batch',mapping.get('batch')) or {}
    version=batch.get('current_version') or binding.get('version')
    return {'ok':applied, 'review_saved':True, 'review_id':review_id, 'idempotent':idempotent,
            'source_context':context_for_source(
                store.get('source',binding['expense_id']),binding,version,mapping.get('batch')),
            'application_status':binding.get('application_status'), 'version':version,
            'changed_count':goods_count+fee_count if applied else 0, 'fee_count':fee_count if applied else 0,
            'issues':binding.get('issues') or [],
            'message':'当前采购支出审核明细已采用，核算仍须通过待核对项。' if applied else '审核明细已保存；采用仍待处理，请查看原因。'}


def confirm_reviewed_cargo(store, ledger, batch_name, expected_context, rows, evidence, complete, actor,
                           *, fees=None, coverage=None, negative_confirmed=False):
    from overseas_costing.services.effective_logistics_source import load_source_bundle, require_available, public_context
    from .matching import save_binding
    from .writer import apply_binding, locked
    with store.atomic():
        bundle = load_source_bundle(batch_name,store=store,ledger=ledger,lock=True)
        context, binding, raw = bundle['context'], bundle['binding'], bundle['source']
        if context['root_kind'] != 'expense' or not binding:
            raise ValueError(_('当前批次没有已确认的采购支出关联。'))
        require_available(context)
        if locked(bundle['batch']) and bundle['batch'].get('edit_lock_owner') != actor:
            raise ValueError(_('批次由其他人编辑，不能采用审核明细。'))
        if not evidence.get('source_id') or len(str(evidence.get('source_hash') or '')) != 64:
            raise ValueError(_('审核缺少可信本地资料版本。'))
        if public_context(evidence.get('source_context')) != public_context(expected_context):
            raise ValueError(_('审核证据与当前预览来源不一致。'))
        goods = inherit_expense_prices(normalize_goods(rows, evidence),raw) if rows is not None else None
        if goods is not None and (not complete or not evidence.get('full_table') or evidence.get('row_count') != len(goods)):
            return {'ok':False,'candidate_only':True,'changed_count':0,'message':'货物表尚未确认完整；仅保留候选，不替换或删除现有行。'}
        try:
            normalized_fees = normalize_fees(fees, raw, evidence,negative_confirmed) if fees is not None else None
        except ValueError as exc:
            return {'ok':False,'candidate_only':True,'fee_count':0,'message':str(exc)}
        prior = resolve_reviewed_source(store,raw,binding)
        # Retain independently reviewed dimension when updating only cargo/fees.
        prior_record = store.get('state',prior['review_id']) if prior.get('review_id') else {}
        payload = {'binding_id':binding['id'],'source_snapshot':raw['snapshot'],
                   'goods':goods if goods is not None else (prior_record or {}).get('goods'),
                   'goods_complete':True if goods is not None else bool((prior_record or {}).get('goods_complete')),
                   'fees':normalized_fees if fees is not None else (prior_record or {}).get('fees'),
                   'evidence':{k:deepcopy(v) for k,v in evidence.items() if k != 'source_context'}}
        review_id = digest('reviewed_source',payload)
        pointer = store.get('state',_pointer(binding['id'],raw['snapshot'])) or {}
        receipt = store.get('state',digest('reviewed_source_receipt',review_id)) or {}
        if coverage is not None and (not coverage or set(coverage)-{'freight','customs','tax','mexico_inland'}):
            raise ValueError(_('请明确确认有效费用覆盖范围。'))
        if pointer.get('review_id') == review_id and receipt.get('binding_revision') == binding['revision'] and receipt.get('version') == context['cost_version']:
            confirmations_changed = False
            if coverage is not None and (binding.get('coverage_cost_hash') != prior['cost_hash'] or set(binding.get('coverage') or []) != set(coverage)):
                binding.update(coverage=sorted(set(coverage)),coverage_cost_hash=prior['cost_hash'])
                confirmations_changed = True
            if negative_confirmed and binding.get('negative_cost_hash') != prior['cost_hash']:
                binding.update(negative_confirmed=True,negative_cost_hash=prior['cost_hash'])
                confirmations_changed = True
            if confirmations_changed:
                save_binding(store,binding)
                store.audit(binding['id'],'review_source_confirmation',actor,review_id=review_id,coverage=coverage,negative_confirmed=negative_confirmed)
                binding=apply_binding(store,ledger,binding['id'],actor,trusted_review_actor=actor)
            return _response(store,ledger,binding,review_id,idempotent=True,goods_count=len(payload['goods'] or []),fee_count=len(payload['fees'] or []))
        if public_context(context) != public_context(expected_context):
            raise ValueError(_('关联、资料快照或成本版本已变化，请重新预览。'))
        from .bound_wiki_service import acknowledge_wiki_review
        acknowledge_wiki_review(store,binding,evidence,expected_context,complete=bool(goods is not None and complete),actor=actor)
        if not store.get('state',review_id):
            store.insert('state',{'id':review_id,'updated_at':utcnow(),'data':dumps({**payload,'actor':actor})})
        store.put('state',{'id':_pointer(binding['id'],raw['snapshot']),'updated_at':utcnow(),'data':dumps({'review_id':review_id})})
        binding['revision'] += 1
        effective = resolve_reviewed_source(store,raw,binding)
        if fees is None:
            # Cargo-only human review does not alter the already reviewed fee pool.
            if binding.get('coverage_cost_hash') == prior['cost_hash']:
                binding['coverage_cost_hash'] = effective['cost_hash']
            if binding.get('negative_cost_hash') == prior['cost_hash']:
                binding['negative_cost_hash'] = effective['cost_hash']
        if coverage is not None:
            binding.update(coverage=sorted(set(coverage)),coverage_cost_hash=effective['cost_hash'])
        if negative_confirmed:
            binding.update(negative_confirmed=True,negative_cost_hash=effective['cost_hash'])
        save_binding(store,binding)
        store.audit(binding['id'],'review_source',actor,review_id=review_id,source_snapshot=raw['snapshot'])
        applied = apply_binding(store,ledger,binding['id'],actor,trusted_review_actor=actor)
        result=_response(store,ledger,applied,review_id,goods_count=len(payload['goods'] or []),fee_count=len(payload['fees'] or []))
        store.put('state',{'id':digest('reviewed_source_receipt',review_id),'updated_at':utcnow(),
                          'data':dumps({'binding_revision':applied['revision'],'version':result['version']})})
        return result
