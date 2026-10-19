"""Local-only reads and background archive synchronization for logistics settlement."""

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message

from decimal import Decimal
import json
import re
from time import monotonic

try:
    import frappe
except ImportError:
    frappe = None

from .store import Store
from .model import digest, dumps, norm, identity
from .matching import save_binding
from .writer import apply_binding
from .ledger import FrappeLedger
from .jobs import start_job, run_step, retry_job, utcnow
from .application import row_meta


RUN_JOB_BUDGET_SECONDS = 60
RUN_JOB_MAX_STEPS = 100


def installed():
    return bool(frappe is not None and getattr(frappe, 'db', None) and frappe.db.sql("SHOW TABLES LIKE 'oc_ls_binding'"))


def store():
    if not installed():
        raise ValueError(_i18n('物流结算数据库尚未迁移'))
    return Store.frappe()


def enabled():
    return bool(installed() and (Store.frappe().get('state', 'control') or {}).get('enabled'))


def freight_enabled():
    return bool(frappe is not None and getattr(frappe,'conf',None) and frappe.conf.get('overseas_costing_freight_lines_enabled'))


def archive():
    from overseas_costing.scripts.import_oa_logistics import _get_postgres_approval_source
    from overseas_costing.integrations.logistics_settlement_source import SettlementArchive
    def tracked_pairs():
        tracked = store().sql("SELECT s.corp,s.instance FROM oc_ls_source s WHERE (s.kind IN ('logistics','expense') AND CAST(JSON_EXTRACT(s.data,'$.invalid') AS CHAR) IN ('false','0')) OR EXISTS (SELECT 1 FROM oc_ls_binding b WHERE b.expense_id=s.id OR b.logistics_id=s.id) OR EXISTS(SELECT 1 FROM oc_ls_freight_claim f WHERE f.source_id=s.id OR f.logistics_id=s.id) OR EXISTS(SELECT 1 FROM oc_ls_packing_review p WHERE p.source_id=s.id AND p.status='applied')")
        return [(r['corp'], r['instance']) for r in tracked]
    return SettlementArchive(_get_postgres_approval_source(), logistics_codes=logistics_codes(), financial=freight_enabled(),
                             tracked_pairs=tracked_pairs)


def logistics_codes():
    from overseas_costing.scripts.import_oa_logistics import resolve_logistics_process_code
    configured = frappe.conf.get('overseas_costing_settlement_logistics_codes') or []
    if isinstance(configured, str):
        configured = [code.strip() for code in configured.split(',') if code.strip()]
    return set(configured or [resolve_logistics_process_code()])


def for_batch(batch_name, *, lock=False):
    if not installed():
        return None
    db = Store.frappe()
    mappings = db.find('batch_map', batch=batch_name, limit=1)
    if not mappings:
        return None
    bindings = db.find('binding', logistics_id=mappings[0]['source_id'], limit=1)
    return db.get('binding', bindings[0]['id'], lock=lock) if bindings else None


def calculation_blockers(batch_name, version_name=None, *, for_calculation=False, lock=False):
    """No upstream requests. Invalid/stale final sources cannot silently revive estimates."""
    if freight_enabled():
        from .freight_adoption import blockers
        db=store();ledger=FrappeLedger()
        version=ledger.get('version',version_name or (ledger.get('batch',batch_name) or {}).get('current_version')) or {}
        active_payment_claims=db.find('payment_claim',batch=batch_name,version=version.get('name'),status='active')
        if row_meta(version).get('freight_settlement') or active_payment_claims or not for_batch(batch_name):
            issues=blockers(db,ledger,batch_name,version.get('name'),for_calculation)
            from overseas_costing.services.effective_logistics_source import resolve_source_context
            ctx=resolve_source_context(batch_name,version.get('name'),store=db,ledger=ledger,lock=lock)
            mapping=db.find('batch_map',batch=batch_name)
            original=db.get('source',mapping[0]['source_id'],lock=lock) if mapping else None
            if original and original.get('invalid'):issues.append('本票国际物流已撤销或失效')
            if (ctx.get('root_kind')=='expense' or (ctx.get('packing') or {}).get('selected_source')) and (ctx.get('invalid') or not ctx.get('available')):issues.append('已采用装箱来源已变化，请重新核对')
            return issues
    binding = for_batch(batch_name, lock=lock)
    if not installed():
        return []
    db = Store.frappe()
    mappings = db.find('batch_map', batch=batch_name, limit=1)
    logistics_source = db.get('source', mappings[0]['source_id'], lock=lock) if mappings else None
    if not binding and logistics_source and logistics_source.get('invalid'):
        return ['国际物流来源已失效，当前结果不能确认或推送']
    effective_id = binding['expense_id'] if binding else (logistics_source or {}).get('id')
    document_states = db.find('document_sync', batch=batch_name, source_id=effective_id, limit=1) if effective_id else []
    if document_states and document_states[0].get('blocking'):
        return list(document_states[0].get('issues') or ['新装箱资料待核对'])
    if not binding:
        return ['尚未关联审批通过的物流结算采购支出，当前为初始录入或暂估'] if mappings and not for_calculation else []
    from .bound_wiki_service import pending_wiki_issues
    wiki_issues = pending_wiki_issues(db, binding)
    if wiki_issues:
        return wiki_issues
    expense = db.get('source', binding['expense_id'], lock=lock)
    logistics = db.get('source', binding['logistics_id'], lock=lock)
    from .reviewed_cargo import resolve_reviewed_source
    expense = resolve_reviewed_source(db, expense, binding)
    if for_calculation:
        version = FrappeLedger().get('version', version_name or binding.get('version'), lock=lock)
        if version and version.get('status') == 'Confirmed':
            return ['已确认版本保留历史，请在调整草稿中重新核算']
    if not expense or expense['invalid'] or expense['kind'] != 'expense' or not expense['approved']:
        return ['关联采购支出尚未通过、已失效或不再属于物流结算，不能确认或推送']
    if binding.get('applied_cost_hash') != expense['cost_hash'] or binding.get('application_status') not in {'applied', 'applied_pending'}:
        return list(binding.get('issues') or ['最终采购支出尚未完整应用，请先核对资料'])
    if version_name and version_name != binding.get('version'):
        return ['当前最终采购支出已生成新版本；历史版本仅供追溯']
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    context = resolve_source_context(batch_name,version_name or binding.get('version'),store=db,ledger=FrappeLedger(),lock=lock)
    issues = []
    application = db.get('application', binding.get('last_application', '')) or {}
    if (application.get('source_context') or {}).get('fingerprint') != context.get('fingerprint'):
        issues.append('当前来源策略或资料快照已变化，请重新采用采购支出资料')
    issues.extend((application.get('plan') or {}).get('goods_pending') or [])
    planned_rules = (application.get('plan') or {}).get('rules') or []
    actual_rules = [r for r in FrappeLedger().rows('rule', batch=batch_name, version=binding.get('version')) if r.get('is_final')]
    def rule_value(rule):
        try:
            amount = Decimal(str(rule.get('amount')))
        except Exception:
            amount = str(rule.get('amount'))
        return (rule.get('rule_code'), amount, rule.get('currency'), rule.get('source_binding_id'), rule.get('source_snapshot'), rule.get('covered_scopes'), bool(rule.get('is_enabled')), bool(rule.get('is_active')))
    if sorted(map(rule_value, actual_rules), key=str) != sorted(map(rule_value, planned_rules), key=str):
        issues.append('当前费用与已采用的采购支出来源不一致，请重新核对应用记录')
    source_goods = {g['line_key']: g for g in expense['goods']}
    seen_goods = []
    for item in FrappeLedger().rows('item', batch=batch_name, version=binding.get('version')):
        meta = row_meta(item)
        source_row = source_goods.get(meta.get('settlement_line_key'))
        if source_row:
            seen_goods.append(source_row['line_key'])
        cargo = meta.get('settlement_cargo') or {}
        if source_row and (source_row.get('quantity') is None or Decimal(str(cargo.get('quantity') or 0)) != Decimal(str(source_row['quantity'])) or
                           identity(item.get('material_code')) != identity(source_row.get('material_code')) or identity(cargo.get('unit')) != identity(source_row.get('unit'))):
            issues.append('当前货物或数量与最终采购支出不一致，请核对来源')
        if meta.get('settlement_packing_review'):
            issues.append('采购数量或物料已变化，请核对保留的装箱重量、体积')
        from overseas_costing.services.effective_source_values import project_source_values
        physical = project_source_values(item,context)
        if not any(physical.get(key) is not None for key in ('gross_weight_kg','volume_m3','chargeable_weight_kg')):
            issues.append('当前采购支出装箱资料待补，不能采用旧物流重量和体积')
        if meta.get('settlement_purchase_value_review'):
            issues.append('结算数量变化，请核对独立来源商品单价与发货货值')
        if cargo:
            from .valuation import value_final_cargo
            current_version = FrappeLedger().get('version',binding.get('version')) or {}
            valuation = value_final_cargo(item,cargo,{k:v for k,v in current_version.items() if k.startswith('fx_')})
            if valuation.get('error') or valuation.get('input_fingerprint') != (meta.get('settlement_valuation') or {}).get('input_fingerprint'):
                issues.append('最终发货货值缺少依据或已失效，请核对商品单价、单位和汇率后重新采用')
    if expense.get('goods_complete') and (set(seen_goods) != set(source_goods) or len(seen_goods) != len(set(seen_goods))):
        issues.append('当前货物清单与最终采购支出不一致，存在缺行或重复行')
    return sorted(set(issues))


def has_final_binding(batch_name):
    """Legacy import must not replace settlement-owned item rows or restore initial fees."""
    if for_batch(batch_name):return True
    if not installed():return False
    batch=FrappeLedger().get('batch',batch_name) or {}
    version=FrappeLedger().get('version',batch.get('current_version')) or {}
    return bool(row_meta(version).get('freight_settlement'))


def source_summary(source):
    from overseas_costing.utils.dingtalk import build_desktop_approval_url
    source = source or {}
    return {'open_url': build_desktop_approval_url(source.get('instance') or ''), **{key: source.get(key) for key in ('id', 'corp', 'instance', 'approval_no', 'kind', 'status', 'approved', 'invalid',
             'title', 'process_code', 'amount', 'currency', 'fees', 'goods', 'goods_complete', 'issues', 'coverage', 'source_updated_at', 'snapshot', 'documents')}}


def batch_status(batch_name, version_name=None):
    if freight_enabled():
        from .freight_runtime import batch_status as freight_status
        return freight_status(store(),FrappeLedger(),batch_name,version_name)
    from . import batch_matching
    db = store()
    ledger = FrappeLedger()
    batch = ledger.get('batch', batch_name) or {}
    viewed_version = version_name or batch.get('current_version')
    if version_name and (ledger.get('version', version_name) or {}).get('batch') != batch_name:
        raise ValueError(_i18n('版本不属于当前批次'))
    historical = bool(version_name and version_name != batch.get('current_version'))
    mappings = db.find('batch_map', batch=batch_name, limit=1)
    if not mappings:
        return {'ok': True, 'mapped': False, 'binding': None, 'historical': historical, 'viewed_version': viewed_version, 'candidates': [],
                'matching': {'status': 'not_started', 'cached': False}, 'message': '尚未整理此批次的国际物流来源；匹配时仅补读本票审批'}
    source = db.get('source', mappings[0]['source_id'])
    bindings = db.find('binding', logistics_id=source['id'], limit=1)
    binding = bindings[0] if bindings else None
    candidates = [candidate_view(db, c) for c in batch_matching.candidates(db, source)]
    result = {'ok': True, 'mapped': True, 'historical': historical, 'viewed_version': viewed_version, 'logistics': source_summary(source), 'candidates': candidates,
              'binding': {k: binding.get(k) for k in ('id', 'revision', 'application_status', 'issues', 'version', 'source_snapshot', 'coverage')} if binding else None,
              'sync': db.get('state', 'sync') or {}, 'health': db.get('state', 'health') or {},
              'document_sync': next(iter(db.find('document_sync', batch=batch_name, limit=1)), None)}
    result['matching'] = batch_matching.status(db, source['id']) if not historical else {'status': 'historical', 'cached': True}
    if binding:
        from .reviewed_cargo import resolve_reviewed_source
        result['expense'] = source_summary(resolve_reviewed_source(db, db.get('source', binding['expense_id']), binding))
        result['blocking_reasons'] = calculation_blockers(batch_name, binding.get('version'))
        app = db.get('application', binding.get('last_application', '')) or {}
        result['application'] = {k: app.get(k) for k in ('source_snapshot', 'applied_at', 'version', 'status', 'plan', 'source_context', 'review_id')}
        from .writer import item_review
        result['item_reviews'] = [item_review(i) for i in FrappeLedger().rows('item', batch=batch_name, version=viewed_version)]
        result['audit'] = db.find('audit', binding_id=binding['id'], limit=30)
    if historical:
        apps = db.find('application', binding_id=binding['id'], version=viewed_version) if binding else []
        app = max(apps, key=lambda a: a.get('applied_at', ''), default=None)
        result.update(candidates=[], item_reviews=[], blocking_reasons=[], application=app)
        if app:
            snapshot = db.get('snapshot', app['source_snapshot'])
            result['expense'] = source_summary(resolve_reviewed_source(db, {**snapshot, 'id': snapshot['source_id'], 'snapshot': app['source_snapshot']}, binding, version_name=viewed_version))
            result['binding'] = {**result['binding'], 'version': viewed_version, 'application_status': 'historical', 'issues': [], 'coverage': (app.get('plan') or {}).get('coverage'), 'source_snapshot': app['source_snapshot']}
        else:
            result.update(binding=None, expense=None, message='此历史版本尚未采用物流结算采购支出')
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    result['source_context'] = resolve_source_context(batch_name,viewed_version,store=db,ledger=ledger)
    result['document_sync'] = next(iter(db.find('document_sync',batch=batch_name,source_id=result['source_context']['root_source_id'],limit=1)),None)
    return result


def candidate_view(db, candidate):
    return {**{k: candidate.get(k) for k in ('id', 'revision', 'status', 'method', 'reason', 'evidence')},
            'logistics': source_summary(db.get('source', candidate['logistics_id'])),
            'expense': source_summary(db.get('source', candidate['expense_id']))}


def ensure_batch_source(db, batch_name):
    """Explicit match action only: hydrate one exact approval, never create a batch."""
    from .model import parse_source
    ledger = FrappeLedger()
    batch = ledger.get('batch', batch_name) or {}
    mapping = db.find('batch_map', batch=batch_name, limit=1)
    if mapping:
        return db.get('source', mapping[0]['source_id'])
    instance = str(batch.get('source_instance_id') or '').strip()
    if not instance:
        raise ValueError(_i18n('本票缺少国际物流审批实例，请先补齐原单身份'))
    corp = str(batch.get('source_corp_id') or '').strip()
    if not corp:
        corp = str(frappe.conf.get('overseas_costing_settlement_legacy_corp_id') or frappe.conf.get('overseas_costing_dingtalk_corp_id') or '')
    if not corp:
        raise ValueError(_i18n('本票缺少企业信息，无法安全确定国际物流原单'))
    source = db.get('source', digest(corp, instance))
    if not source:
        rows = archive().get_sources([(corp, instance)])
        exact = [r for r in rows if r.get('corp_id') == corp and r.get('process_instance_id') == instance]
        if len(exact) != 1:
            raise ValueError(_i18n('上游归档尚未提供本票国际物流审批，请等待归档补齐后重试'))
        raw = exact[0]
        if parse_source(raw, logistics_codes=logistics_codes())['kind'] != 'logistics':
            raise ValueError(_i18n('本票原单不是国际物流流程，需核对批次来源'))
        try:
            raw = prepare_source(raw)
        except Exception as exc:
            # A document problem must not erase the approval identity or trigger
            # a global initialization. Keep the source explicitly pending review.
            raw = {**raw, 'settlement_fee_issues': ['本票附件待处理：' + str(exc)[:300]]}
        source = db.ingest(parse_source(raw, logistics_codes=logistics_codes()))
    if source['kind'] != 'logistics' or source['invalid']:
        raise ValueError(_i18n('本票国际物流来源已失效或类型不符'))
    with db.atomic():
        db.get('state', 'match_lock', lock=True)
        current_batch = ledger.get('batch', batch_name, lock=True) or {}
        if current_batch.get('source_instance_id') != instance or current_batch.get('source_corp_id') not in (None, '', corp):
            raise ValueError(_i18n('批次原单已变化，请刷新后重试'))
        mappings = db.find('batch_map', source_id=source['id'])
        if mappings and mappings[0]['batch'] != batch_name:
            raise ValueError(_i18n('该国际物流已对应其他批次，请先处理重复批次'))
        existing = db.find('batch_map', batch=batch_name)
        if existing and existing[0]['source_id'] != source['id']:
            raise ValueError(_i18n('本票来源映射已变化，请刷新'))
        if not existing:
            db.insert('batch_map', {'id': source['id'], 'source_id': source['id'], 'batch': batch_name, 'data': '{}'})
        ledger.put('batch', batch_name, {'source_corp_id': corp})
    return source


def start_batch_matching(batch_name, version_name=None):
    if freight_enabled():
        return run_payment_rule_matching(batch_name,version_name)
    from . import batch_matching
    db = store()
    batch = FrappeLedger().get('batch', batch_name) or {}
    if version_name and version_name != batch.get('current_version'):
        raise ValueError(_i18n('历史版本只读，请返回当前版本匹配'))
    source = ensure_batch_source(db, batch_name)
    with db.atomic():
        db.get('state', 'match_lock', lock=True)
        batch = FrappeLedger().get('batch', batch_name, lock=True) or {}
        if version_name and version_name != batch.get('current_version'):
            raise ValueError(_i18n('成本版本已变化，请刷新当前版本后匹配'))
        job = batch_matching.start(db, source['id'], frappe.session.user)
    if job.get('status') == 'queued':
        frappe.enqueue('overseas_costing.services.logistics_settlement.runtime.run_batch_matching', queue='long',
                       timeout=600, batch_matching_job_id=job['id'], enqueue_after_commit=True)
    return {'ok': True, 'matching': job}


def run_payment_rule_matching(batch_name,version_name=None):
    """Refresh one batch's deterministic local candidates without a model call."""
    if not freight_enabled():raise ValueError(_i18n('当前模式不支持付款明细匹配'))
    from . import freight_matching
    db=store();ledger=FrappeLedger();batch=ledger.get('batch',batch_name) or {}
    if version_name and version_name!=batch.get('current_version'):raise ValueError(_i18n('历史版本只读，请返回当前版本匹配'))
    source=ensure_batch_source(db,batch_name)
    with db.atomic():
        db.get('state','match_lock',lock=True)
        current=ledger.get('batch',batch_name,lock=True) or {}
        if version_name and version_name!=current.get('current_version'):raise ValueError(_i18n('成本版本已变化，请刷新当前版本后匹配'))
        job=freight_matching.record_rule_pass(db,source['id'],frappe.session.user)
    db.commit()
    from .freight_runtime import candidate_view
    rows=[candidate_view(db,c,batch.get('transport_mode')) for c in freight_matching.candidates(db,source['id']) if c['status']!='rejected']
    return {'ok':True,'matching':job,'payment_candidates':rows,'candidates':rows}


def start_payment_ai_matching(batch_name,version_name=None,hints=None,offset=0,limit=30):
    if not freight_enabled():raise ValueError(_i18n('当前模式不支持付款明细 AI 匹配'))
    from . import payment_ai_matching
    db=store();ledger=FrappeLedger();batch=ledger.get('batch',batch_name) or {};current=batch.get('current_version')
    version_name=version_name or current
    if version_name!=current:raise ValueError(_i18n('历史版本只读，请返回当前版本匹配'))
    source=ensure_batch_source(db,batch_name)
    with db.atomic():
        if (ledger.get('batch',batch_name,lock=True) or {}).get('current_version')!=version_name:
            raise ValueError(_i18n('成本版本已变化，请刷新当前版本后匹配'))
        job=payment_ai_matching.start(db,source['id'],batch_name,version_name,frappe.session.user,hints=hints,offset=offset,limit=limit)
    if job.get('status')=='queued' and job.get('enqueue_required'):
        frappe.enqueue('overseas_costing.services.logistics_settlement.runtime.run_payment_ai_matching',queue='long',timeout=600,
            payment_ai_job_id=job['id'],enqueue_after_commit=True)
    return {'ok':True,'payment_matching':job,'message':job.get('message')}


def run_batch_matching(batch_matching_job_id):
    from . import batch_matching
    if freight_enabled():
        # Compatibility for jobs queued before payment matching was split. Never
        # pass those jobs to a model: they are completed as deterministic rules.
        from . import freight_matching
        db=store()
        with db.atomic():
            db.get('state','match_lock',lock=True)
            job=db.get('state',batch_matching_job_id,lock=True) or {}
            if job.get('kind') not in (None,'') or job.get('status')!='queued' or not job.get('logistics_id'):return job
            try:fingerprint=freight_matching.input_state(db,job['logistics_id'])[2]
            except ValueError:return job
            if job.get('fingerprint')!=fingerprint:return job
            result=freight_matching.record_rule_pass(db,job['logistics_id'],job.get('actor',''))
            job.update(status='completed',stage='saved',finished_at=utcnow())
            db.put('state',{'id':job['id'],'updated_at':utcnow(),'data':dumps(job)})
        db.commit();return result
    from overseas_costing.services import allocation_service
    config = allocation_service._ai_config()
    config['timeout'] = min(120, max(60, float(config.get('timeout') or 60)))
    def call_model(messages):
        if not config.get('api_key'):
            raise ValueError(_i18n('未配置 DeepSeek API 密钥，规则候选已保存，可人工搜索关联'))
        return allocation_service._extract_json_object(allocation_service._call_chat_completions(config, messages))
    return batch_matching.run(store(), batch_matching_job_id, call_model, config.get('model', ''))


def run_payment_ai_matching(payment_ai_job_id):
    from . import payment_ai_matching
    from overseas_costing.services import allocation_service
    config=allocation_service._ai_config();config['timeout']=min(120,max(60,float(config.get('timeout') or 60)))
    def call_model(messages):
        if not config.get('api_key'):raise ValueError(_i18n('未配置 DeepSeek API 密钥'))
        return allocation_service._extract_json_object(allocation_service._call_chat_completions(config,messages))
    return payment_ai_matching.run(store(),payment_ai_job_id,call_model,config.get('model',''),
        current_version=lambda batch,lock=False:(FrappeLedger().get('batch',batch,lock=lock) or {}).get('current_version'))


def _batch_metadata(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        result = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


_COMMENT_WAYBILL_LABEL = (
    r'(?:(?:快递)?(?:单号|运单号?)|tracking(?:\s+(?:number|code))?)?'
    r'[ \t:：#-]*'
)
_COMMENT_WAYBILL_TAIL = r'(?=$|[ \t,，;；。:：、()（）\[\]【】])'
_COMMENT_WAYBILL_LINES = (
    re.compile(
        r'^[ \t]*DHL\s*' + _COMMENT_WAYBILL_LABEL
        + r'(?P<token>(?:\d[ ]*){10})' + _COMMENT_WAYBILL_TAIL,
        re.IGNORECASE,
    ),
    re.compile(
        r'^[ \t]*FEDEX\s*' + _COMMENT_WAYBILL_LABEL
        + r'(?P<token>(?:(?:\d[ ]*){15}|(?:\d[ ]*){12}))'
        + _COMMENT_WAYBILL_TAIL,
        re.IGNORECASE,
    ),
    re.compile(
        r'^[ \t]*UPS\s*' + _COMMENT_WAYBILL_LABEL
        + r'(?P<token>1Z[A-Z0-9]{16})' + _COMMENT_WAYBILL_TAIL,
        re.IGNORECASE,
    ),
)
_COMMENT_WAYBILL_FORBIDDEN = re.compile(
    r'RMB|CNY|MXN|USD|PESOS?|比索|金额|AMOUNT|审批|APPROVAL|'
    r'(?<![A-Z0-9])ID(?![A-Z0-9])',
    re.IGNORECASE,
)
_COMMENT_TEXT_FIELDS = {
    'remark', 'text', 'comment', 'content', 'commenttext', 'commentcontent',
    'remarktext', 'operationremark', '评论', '评论内容', '备注', '内容',
}


def _comment_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r'[^a-z]', '', str(key).lower())
            field = normalized if normalized else str(key).strip()
            if field not in _COMMENT_TEXT_FIELDS:
                continue
            if isinstance(child, str):
                yield child
            elif isinstance(child, (dict, list, tuple)):
                yield from _comment_texts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _comment_texts(child)


def _comment_waybills(source):
    """Strict auto-adoption subset; broad identifiers remain search-only."""
    raw = source.get('raw') if isinstance(source.get('raw'), dict) else {}
    values = (raw.get('comments') or [], raw.get('operationRecords') or [])
    tokens = set()
    for text in _comment_texts(values):
        for line in text.splitlines():
            if _COMMENT_WAYBILL_FORBIDDEN.search(line):
                continue
            for pattern in _COMMENT_WAYBILL_LINES:
                match = pattern.search(line)
                if match:
                    tokens.add(re.sub(r'\s+', '', match.group('token')).upper())
                    break
    return sorted(tokens)


def _comment_waybill_source_eligible(source):
    """Allow active logistics comments without weakening terminal-state checks."""
    if source.get('kind') != 'logistics' or bool(source.get('invalid')):
        return False
    status = str(source.get('status') or '').strip().upper()
    if status == 'RUNNING':
        return True
    return status == 'COMPLETED' and source.get('approved') is True


def _sync_comment_waybill(db, ledger, batch_name, source, actor='archive-sync'):
    """Adopt one comment-derived waybill without claiming or replacing manual data."""
    if not _comment_waybill_source_eligible(source):
        return {'status': 'ineligible', 'candidates': []}
    candidates = _comment_waybills(source)
    if len(candidates) != 1:
        return {'status': 'ambiguous' if candidates else 'missing', 'candidates': candidates}

    candidate = candidates[0]
    batch = ledger.get('batch', batch_name, lock=True) or {}
    current = str(batch.get('waybill_no') or '').strip()
    metadata = _batch_metadata(batch.get('extra_json'))
    provenance = metadata.get('waybill_source') if isinstance(metadata.get('waybill_source'), dict) else {}
    source_owned = bool(
        current
        and provenance.get('kind') == 'comment-derived'
        and provenance.get('source_id') == source.get('id')
        and str(provenance.get('value') or '').strip() == current
    )

    if current == candidate:
        return {'status': 'unchanged', 'value': candidate}
    if current and not source_owned:
        conflict_fingerprint = digest(
            'batch-waybill-conflict-1', batch_name, current, candidates,
            source.get('id'), source.get('snapshot'),
        )
        prior_conflict = metadata.get('waybill_conflict')
        if (
            isinstance(prior_conflict, dict)
            and prior_conflict.get('fingerprint') == conflict_fingerprint
        ):
            return {'status': 'conflict', 'value': current, 'candidates': candidates}
        metadata['waybill_conflict'] = {
            'fingerprint': conflict_fingerprint,
            'source_id': source.get('id'),
            'source_snapshot': source.get('snapshot'),
            'current_value': current,
            'candidate_values': candidates,
            'kind': 'comment-derived',
            'observed_at': utcnow(),
            'actor': actor,
        }
        ledger.patch_batch_metadata(batch_name, {'extra_json': dumps(metadata)})
        db.audit(
            source['id'], 'batch_waybill_conflict', actor,
            batch=batch_name,
            current_value=current,
            candidate_values=candidates,
            source_snapshot=source.get('snapshot'),
            kind='comment-derived',
        )
        return {'status': 'conflict', 'value': current, 'candidates': candidates}

    synced_at = utcnow()
    metadata.pop('waybill_conflict', None)
    metadata['waybill_source'] = {
        'source_id': source['id'],
        'source_snapshot': source.get('snapshot'),
        'value': candidate,
        'kind': 'comment-derived',
        'synced_at': synced_at,
        'actor': actor,
    }
    ledger.patch_batch_metadata(batch_name, {
        'waybill_no': candidate,
        'extra_json': dumps(metadata),
    })
    db.audit(
        source['id'], 'batch_waybill_synced', actor,
        batch=batch_name,
        old_value=current,
        new_value=candidate,
        source_snapshot=source.get('snapshot'),
        kind='comment-derived',
    )
    return {'status': 'updated', 'value': candidate}


def _comment_waybill_backfill_row(db, ledger, mapping):
    batch_name = str(mapping.get('batch') or '')
    source_id = str(mapping.get('source_id') or '')
    batch = ledger.get('batch', batch_name) or {}
    source = db.get('source', source_id) or {}
    current = str(batch.get('waybill_no') or '').strip()
    candidates = _comment_waybills(source) if source else []
    row = {
        'batch_name': batch_name,
        'source_id': source_id,
        'source_snapshot': source.get('snapshot'),
        'status': 'ready',
        'current_value': current,
        'candidates': candidates,
    }
    if not batch:
        row['status'] = 'batch_missing'
    elif current:
        row['status'] = 'already_set'
    elif str(batch.get('confirm_status') or '').strip().lower() == 'confirmed':
        row['status'] = 'confirmed'
    elif str(batch.get('writeback_status') or '').strip().lower() == 'success':
        row['status'] = 'erp_success'
    elif not source or not _comment_waybill_source_eligible(source):
        row['status'] = 'ineligible'
    elif len(candidates) > 1:
        row['status'] = 'ambiguous'
    elif not candidates:
        row['status'] = 'missing'
    return row


def _comment_waybill_backfill_preview(db, ledger, batch_names=None, limit=1000):
    requested = {str(value) for value in (batch_names or []) if str(value)}
    mappings = db.find('batch_map', limit=max(1, min(1000, int(limit or 1000))))
    if requested:
        mappings = [row for row in mappings if str(row.get('batch') or '') in requested]
    rows = [_comment_waybill_backfill_row(db, ledger, row) for row in mappings]
    ready = [
        {
            'batch_name': row['batch_name'],
            'source_id': row['source_id'],
            'source_snapshot': row['source_snapshot'],
            'candidate': row['candidates'][0],
        }
        for row in rows if row['status'] == 'ready'
    ]
    return {
        'rows': rows,
        'plan_hash': digest('comment-waybill-backfill-1', ready),
    }


def backfill_comment_waybills(
    batch_names=None, *, dry_run=True, expected_plan_hash='', limit=1000,
    actor='comment-waybill-backfill',
):
    """Preview by default; apply only one-candidate blank waybills per transaction."""
    if not installed():
        return {
            'ok': False,
            'dry_run': True,
            'plan_hash': '',
            'rows': [],
            'summary': {},
            'message': '物流结算数据库尚未就绪。',
        }
    if isinstance(batch_names, str):
        try:
            decoded = json.loads(batch_names)
        except (TypeError, ValueError):
            decoded = [part.strip() for part in batch_names.split(',') if part.strip()]
        batch_names = decoded if isinstance(decoded, list) else [decoded]

    db = store()
    ledger = FrappeLedger()
    preview = _comment_waybill_backfill_preview(db, ledger, batch_names, limit)
    rows = preview['rows']
    ready = [row for row in rows if row['status'] == 'ready']
    if dry_run:
        summary = {}
        for row in rows:
            summary[row['status']] = summary.get(row['status'], 0) + 1
        return {
            'ok': True,
            'dry_run': True,
            'plan_hash': preview['plan_hash'],
            'rows': rows,
            'summary': summary,
        }
    if ready and not expected_plan_hash:
        raise ValueError(_i18n('请先生成运单号回填清单并传入 plan_hash。'))
    if expected_plan_hash and expected_plan_hash != preview['plan_hash']:
        raise ValueError(_i18n('运单号回填清单已变化，请重新预览。'))

    applied = []
    for planned in rows:
        if planned['status'] != 'ready':
            applied.append(planned)
            continue
        try:
            with db.atomic():
                mapping = {'batch': planned['batch_name'], 'source_id': planned['source_id']}
                current = _comment_waybill_backfill_row(db, ledger, mapping)
                if (
                    current['status'] != 'ready'
                    or current['source_snapshot'] != planned['source_snapshot']
                    or current['candidates'] != planned['candidates']
                ):
                    current['status'] = 'changed'
                    applied.append(current)
                    continue
                source = db.get('source', planned['source_id'], lock=True)
                result = _sync_comment_waybill(
                    db, ledger, planned['batch_name'], source, actor=actor,
                )
                applied.append({**planned, **result})
            db.commit()
        except Exception as exc:
            applied.append({**planned, 'status': 'error', 'message': str(exc)})

    summary = {}
    for row in applied:
        summary[row['status']] = summary.get(row['status'], 0) + 1
    return {
        'ok': not summary.get('error'),
        'dry_run': False,
        'plan_hash': preview['plan_hash'],
        'rows': applied,
        'summary': summary,
    }


def ensure_batch(db, source):
    ledger = FrappeLedger()
    mapping = db.find('batch_map', source_id=source['id'], limit=1)
    if mapping:
        batch_name = mapping[0]['batch']
        _sync_comment_waybill(db, ledger, batch_name, source)
        return batch_name
    matches = ledger.rows('batch', source_instance_id=source['instance'])
    exact = [b for b in matches if b.get('source_corp_id') == source['corp']]
    legacy_corp = str(frappe.conf.get('overseas_costing_settlement_legacy_corp_id') or frappe.conf.get('overseas_costing_dingtalk_corp_id') or '')
    eligible = exact or [b for b in matches if not b.get('source_corp_id') and legacy_corp == source['corp']]
    if len(eligible) > 1 or (matches and len(eligible) != 1):
        raise ValueError(_i18n('国际物流存在重复批次或旧批次缺企业信息；请核对批次映射及历史企业配置'))
    if eligible:
        batch = eligible[0]
        ledger.put('batch', batch['name'], {'source_corp_id': source['corp']})
    else:
        from overseas_costing.scripts.import_oa_logistics import summarize_approval, build_batch_values_from_approval
        payload = dict(source['raw'], processInstanceId=source['instance'], corpId=source['corp'], status=source['status'])
        summary = summarize_approval(payload)
        values = build_batch_values_from_approval(summary)
        values.update(source_corp_id=source['corp'], source_instance_id=source['instance'], source_type='oa_logistics', status='Draft', confirm_status='Pending')
        values['batch_no'] = values.get('batch_no') or source['approval_no'] or source['instance']
        batch = ledger.create('batch', values)
        version = ledger.create('version', {'batch': batch['name'], 'version_code': '初始录入', 'version_type': 'Estimated',
                               'status': 'Active', 'is_current': 1, 'source_type': 'Import', 'fx_usd_to_rmb': None, 'fx_rmb_to_mxn': None})
        ledger.put('batch', batch['name'], {'current_version': version['name'], 'version_count': 1})
        for index, goods in enumerate(source['goods']):
            if goods.get('quantity') is None:
                continue
            ledger.create('item', {**{k: goods.get(k) for k in ('material_code', 'product_name', 'spec_model', 'unit', 'quantity')},
                                  'batch': batch['name'], 'version': version['name'], 'row_no': index+1, 'source_type': 'oa_logistics',
                                  'extra_json': dumps({'initial_logistics_line': goods['line_key']})})
        if source['amount'] is not None and source['currency']:
            ledger.create('rule', {'batch': batch['name'], 'version': version['name'], 'rule_code': 'oa_logistics_freight',
                                  'expense_category': '初始物流暂估', 'amount': source['amount'], 'currency': source['currency'],
                                  'allocation_basis': 'goods_value', 'is_enabled': 1, 'is_active': 1})
    db.insert('batch_map', {'id': source['id'], 'source_id': source['id'], 'batch': batch['name'], 'data': '{}'})
    _sync_comment_waybill(db, ledger, batch['name'], source)
    return batch['name']


def apply_source(source_id):
    db = Store.frappe()
    with db.atomic():
        db.get('state', 'match_lock', lock=True)
        source = db.get('source', source_id)
        lookup = {'logistics_id':source_id} if source['kind']=='logistics' else {'expense_id':source_id}
        for existing in db.find('binding', **lookup):
            binding = db.get('binding', existing['id'], lock=True)
            db.get('source', binding['expense_id'], lock=True)
            db.get('source', binding['logistics_id'], lock=True)
        return _apply_source_locked(db, source_id)


def _apply_source_locked(db, source_id):
    source = db.get('source', source_id)
    if freight_enabled():
        from .freight_adoption import source_updated
        source_updated(db,FrappeLedger(),source_id)
        # Never send broader monthly bills through the old whole-source writer.
        if source['kind']!='logistics':return
    if source['kind'] == 'logistics':
        batch_name = ensure_batch(db, source)
        if freight_enabled():
            from overseas_costing.services.effective_logistics_source import resolve_source_context
            ctx=resolve_source_context(batch_name,store=db,ledger=FrappeLedger())
            if ctx.get('root_source_id')!=source_id:return
        from .document_writer import sync_logistics_documents
        bindings = db.find('binding', logistics_id=source_id, limit=1)
        if bindings:
            # Continue archiving Intl; its changes cannot adopt data into a bound batch.
            return
        sync_logistics_documents(db, FrappeLedger(), source, batch_name, 'archive-sync')
    else:
        bindings = db.find('binding', expense_id=source_id, limit=1)
    for binding in bindings:
        logistics = db.get('source', binding['logistics_id'])
        ensure_batch(db, logistics)
        apply_binding(db, FrappeLedger(), binding['id'], 'archive-sync')


def begin(mode='initialize', start='', end='', request_key=None):
    if freight_enabled() and mode=='initialize':
        start=start or '2026-01-01'
        request_key=request_key or 'shipment-freight-1'
    db = store()
    upstream = archive()
    preflight = upstream.preflight() if mode == 'initialize' else {'health': upstream.health(), 'data_source': 'postgres'}
    with db.atomic():
        db.get('state', 'job_lock', lock=True)
        control = db.get('state', 'control') or {}
        if mode != 'initialize' and not control.get('enabled'):
            return {'ok': True, 'skipped': True, 'message': '物流结算同步已暂停'}
        if mode != 'initialize':
            previous_health = db.get('state', 'health') or {}
            if 'scope_counts' in previous_health:
                preflight['scope_counts'] = previous_health['scope_counts']
        db.put('state', {'id': 'health', 'updated_at': utcnow(), 'data': dumps(preflight)})
        job = start_job(db, mode=mode, actor=frappe.session.user, start=start, end=end, request_key=request_key)
        if mode == 'initialize':
            db.put('state', {'id': 'control', 'updated_at': utcnow(), 'data': dumps({'enabled': True})})
        enqueue(job['id'])
        return {'ok': True, 'job': job, 'preflight': preflight}


def enqueue(job_id):
    frappe.enqueue('overseas_costing.services.logistics_settlement.runtime.run_job', queue='long',
                   timeout=900, settlement_job_id=job_id, enqueue_after_commit=True)


def run_ai_matching(matching_ai_job_id):
    from . import ai_matching
    from overseas_costing.services import allocation_service
    config = allocation_service._ai_config()
    config['timeout'] = min(120, max(60, float(config.get('timeout') or 60)))
    def call_model(messages):
        if not config.get('api_key'):
            raise ValueError(_i18n('未配置 DeepSeek API 密钥'))
        return allocation_service._extract_json_object(allocation_service._call_chat_completions(config, messages))
    return ai_matching.run(store(), matching_ai_job_id, call_model, config.get('model', ''))


def run_job(settlement_job_id):
    job_id = settlement_job_id
    db = store()
    started = monotonic()
    try:
        upstream = None if db.get('job', job_id)['mode'] == 'reparse' else archive()
        for _ in range(RUN_JOB_MAX_STEPS):
            job = run_step(db, upstream, job_id, logistics_codes=logistics_codes(), apply_source=apply_source, prepare_source=prepare_source, freight_mode=freight_enabled())
            db.commit()
            if job['status'] not in {'queued', 'running'}:
                return job
            # The budget is cooperative: finish and commit this approval's complete
            # attachment set, then yield rather than risking another slow download.
            if monotonic() - started >= RUN_JOB_BUDGET_SECONDS:
                break
        enqueue(job_id)
        db.commit()
    except Exception as exc:
        frappe.db.rollback()
        from .jobs import save_job
        job = db.get('job', job_id)
        if job:
            job.update(status='failed', error=str(exc), last_step_at=utcnow())
            save_job(db, job); db.commit()
        frappe.log_error(title='物流采购支出同步失败', message=str(exc))
        raise


def scheduled_sync():
    if not enabled():
        if installed() and Store.frappe().get('state', 'control'):
            return {'skipped': True, 'message': '物流结算同步已暂停'}
        from overseas_costing.scripts.import_oa_logistics import scheduled_pull_logistics_approvals
        return scheduled_pull_logistics_approvals()
    return begin('incremental')


def resume_pending():
    if not enabled():
        return
    db = store()
    if freight_enabled():
        from .freight_runtime import resume
        resume(db,FrappeLedger())
    document_cursor = (db.get('state', 'document_cursor') or {}).get('cursor')
    document_rows = db.find('document_sync', status='queued', limit=50, after=document_cursor)
    for state in document_rows:
        try:
            with db.atomic():
                db.get('state', 'job_lock', lock=True)
                if not (db.get('state', 'control', lock=True) or {}).get('enabled'):
                    return
                db.get('state', 'match_lock', lock=True)
                current = db.get('document_sync', state['id'])
                if current and current['status'] == 'queued':
                    from overseas_costing.services.effective_logistics_source import resolve_source_context
                    context = resolve_source_context(current['batch'],store=db,ledger=FrappeLedger())
                    if context.get('root_source_id') != current['source_id']:
                        db.put('document_sync',{'id':current['id'],'status':'historical','data':dumps({**current,'blocking':False})})
                    else:
                        apply_source(current['source_id'])
        except Exception as exc:
            db.audit(state['source_id'], 'document_sync_failed', 'pending-sync', reason=str(exc))
    db.put('state', {'id':'document_cursor', 'updated_at':utcnow(), 'data':dumps({'cursor':document_rows[-1]['id'] if len(document_rows)==50 else None})})
    cursor = (db.get('state', 'application_cursor') or {}).get('cursor')
    rows = db.find('binding', limit=50, after=cursor)
    for binding in rows:
        try:
            with db.atomic():
                # Inventory is only a hint: an operator may have rolled back or
                # stopped sync since it was read. Serialize with that operation.
                db.get('state', 'job_lock', lock=True)
                if not (db.get('state', 'control', lock=True) or {}).get('enabled'):
                    return
                db.get('state', 'match_lock', lock=True)
                current = db.get('binding', binding['id'], lock=True)
                if current and not freight_enabled() and current.get('application_status') in {'pending', 'queued', 'applied_pending'}:
                    apply_binding(db, FrappeLedger(), current['id'], 'pending-sync')
        except Exception as exc:
            db.audit(binding['id'], 'application_failed', 'pending-sync', reason=str(exc))
    db.put('state', {'id': 'application_cursor', 'updated_at': utcnow(), 'data': dumps({'cursor': rows[-1]['id'] if len(rows) == 50 else None})})


def weekly_reconcile():
    if enabled():
        return begin('reconcile')


def lock_for_final_action(batch_name, version_name=None):
    if installed():
        db=Store.frappe();ledger=FrappeLedger()
        db.get('state','match_lock',lock=True)
        batch=ledger.get('batch',batch_name,lock=True) or {}
        version=ledger.get('version',version_name or batch.get('current_version'),lock=True) or {}
        managed=row_meta(version).get('freight_settlement')
        if managed:
            from .freight_adoption import blockers
            issues=blockers(db,ledger,batch_name,version.get('name'))
            mapping=db.find('batch_map',batch=batch_name)
            original=db.get('source',mapping[0]['source_id'],lock=True) if mapping else None
            if not original or original.get('invalid'):issues.append('本票国际物流缺失、撤销或失效')
            review=db.get('packing_review',managed.get('packing_review_id') or '')
            refs=[(c['source_id'],c['source_snapshot']) for c in managed.get('claims',[])]
            if review:
                refs.append((review['source_id'],review['source_snapshot']))
                if not original or original.get('snapshot')!=review['logistics_snapshot']:issues.append('本票物流与装箱核对时的版本不同，请重新比对')
            for sid,snapshot in refs:
                source=db.get('source',sid,lock=True) or {}
                if not source.get('approved') or source.get('invalid') or source.get('snapshot')!=snapshot:issues.append('采用的费用或装箱来源更新／失效，请先核对调整草稿')
            return sorted(set(issues))
    binding = for_batch(batch_name, lock=True)
    if not binding:
        return []
    db = Store.frappe()
    db.get('source', binding['expense_id'], lock=True)
    db.get('source', binding['logistics_id'], lock=True)
    batch = FrappeLedger().get('batch', batch_name, lock=True)
    return calculation_blockers(batch_name, version_name or batch.get('current_version'), lock=True)


def guard_legacy_item_write(batch_name, version_name):
    binding = for_batch(batch_name, lock=True)
    if not binding and not has_final_binding(batch_name):
        return
    batch = FrappeLedger().get('batch', batch_name, lock=True)
    version = FrappeLedger().get('version', version_name, lock=True)
    if not version or version.get('status') == 'Confirmed' or batch.get('writeback_status') == 'Success' or version_name != batch.get('current_version'):
        raise ValueError(_i18n('采购支出关联批次的已确认版本保留历史，请在当前调整草稿补充资料'))


def prepare_source(raw):
    from .model import parse_source
    from .documents import enrich_raw
    from overseas_costing.services.import_service import _get_minio_archive_client
    from overseas_costing.integrations.dingtalk_approval_source import MinioArchiveClient
    from frappe.utils.file_manager import save_file
    from pathlib import Path
    if parse_source(raw, logistics_codes=logistics_codes())['kind'] == 'unclassified':
        return raw
    client = None
    def read(manifest):
        nonlocal client
        if not manifest.get('bucket') or not manifest.get('object_key'):
            raise ValueError(_i18n('归档缺少明确 bucket/object_key，不能推测附件地址'))
        if client is None:
            client = _get_minio_archive_client().client
        content, _ = MinioArchiveClient(bucket=manifest['bucket'], client=client).download(manifest)
        return content
    def cache_file(content, file_name):
        return save_file(Path(file_name).name, content, None, None, is_private=1).file_url
    return enrich_raw(Store.frappe(), raw, reader=read, cache_file=cache_file)
