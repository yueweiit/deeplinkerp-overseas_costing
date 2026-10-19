"""Materialize locally cached logistics evidence; never fetch or replace source bytes.

document_sync is a durable queue and idempotence ledger. Every cost/File/attachment
write shares the caller's database transaction. Frozen versions remain immutable.
"""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import PurePosixPath
import unicodedata
from urllib.parse import unquote

from overseas_costing.services.material_value_semantics import is_effectively_missing

from .application import row_meta
from .jobs import utcnow
from .model import digest, dumps, fields_of, number, pick
from .writer import DERIVED_FIELDS, locked, mutable_version

PHYSICAL_ALIASES = {
    'actual_shipped_qty': ('装箱数量', '实发数量', '实际发货数量', '发货数量', 'Cantidad embarcada', 'Cantidad enviada', '数量', 'Cantidad'),
    'gross_weight_kg': ('毛重(kg)', '毛重', 'Gross weight (kg)', 'Gross weight', 'Peso bruto kg', 'Peso bruto'),
    'net_weight_kg': ('净重(kg)', '净重', 'Net weight (kg)', 'Net weight', 'Peso neto'),
    'volume_m3': ('体积(m³)', '体积(m3)', '体积', 'CBM', 'Volume m3', 'Volumen m3'),
    'volume_weight_kg': ('体积重(kg)', '体积重', 'Volumetric weight', 'Peso volumétrico'),
    'chargeable_weight_kg': ('计费重(kg)', '计费重', 'Chargeable weight', 'Peso facturable'),
    'weight_ratio': ('重量比例', '分摊重量比例', 'Weight ratio'),
}


def identity(value):
    # Unlike free-text search, physical row identity preserves meaningful punctuation.
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def item_key(item):
    cargo = row_meta(item).get('settlement_cargo') or {}
    return (identity(item.get('material_code')), identity(item.get('spec_model')), identity(cargo.get('unit') or item.get('shipped_uom') or item.get('unit')))


def packing_state_hash(items):
    """Only inputs used by the packing planner; unrelated calculations cannot revive reviews."""
    rows = []
    for item in items:
        meta = row_meta(item)
        numeric = {}
        # Formal calculation always rewrites weight_ratio from gross weights. Its
        # source descriptor/provenance is tracked separately; the derived value
        # must not reopen an acknowledged packing discrepancy on the next replay.
        for field in ('quantity', *(field for field in PHYSICAL_ALIASES if field != 'weight_ratio')):
            parsed = number(item.get(field))
            numeric[field] = str(Decimal(parsed).normalize()) if parsed is not None else item.get(field)
        rows.append({'name': item['name'], 'identity': item_key(item), 'numeric': numeric,
                     'provenance': meta.get('settlement_packing_provenance') or {},
                     'candidates': meta.get('settlement_packing_candidates') or {},
                     'packing_review': bool(meta.get('settlement_packing_review')), 'settlement_cargo':meta.get('settlement_cargo'),
                     'packing_quantity': meta.get('packing_quantity')})
    return digest(sorted(rows, key=lambda row: row['name']))


def source_packing_state_hash(items, context):
    """Expense checks track adopted physical inputs, excluding computed columns."""
    if context.get('root_kind') != 'expense':
        return packing_state_hash(items)
    from overseas_costing.services.effective_source_values import project_source_values
    return digest(context.get('fingerprint'), packing_state_hash([
        project_source_values(item, context) for item in items]))


def frozen(batch, version):
    return version.get('status') in {'Confirmed', 'Archived'} or batch.get('confirm_status') == 'Confirmed' or batch.get('writeback_status') == 'Success'


def document_retired(document):
    return bool(document.get('retired_at') or (document.get('manifest') or {}).get('retired_at'))


def packing_rows(documents):
    rows = []
    for document in documents:
        if document_retired(document):
            continue
        for table_index, table in enumerate(document.get('tables') or []):
            if table.get('kind') != 'packing':
                continue
            for index, row in enumerate(table.get('rows') or []):
                fields = fields_of(row)
                key = (identity(pick(fields, '物料编码', '物料代码', '编码', 'SKU', 'Codigo', 'Código de material')),
                       identity(pick(fields, '规格', '规格型号', 'Especificacion', 'Modelo')),
                       identity(pick(fields, '单位', 'Unidad')))
                values, invalid = {}, []
                for field, aliases in PHYSICAL_ALIASES.items():
                    raw = pick(fields, *aliases)
                    if raw in (None, ''):
                        continue
                    parsed = number(raw)
                    if parsed is None or Decimal(parsed) < 0:
                        invalid.append(field)
                    else:
                        values[field] = parsed
                rows.append({'document_id': document['id'], 'line': row.get('position', index + 1),
                             'adopted_line_key': row.get('settlement_line_key'),
                             'line_key': digest(document['id'], table_index, row.get('position', index), row),
                             'identity': key, 'values': values, 'invalid_fields': invalid,
                             'quality': (document.get('manifest') or {}).get('archive_quality') or (document.get('manifest') or {}).get('content_quality'),
                             'archive_status': (document.get('manifest') or {}).get('archive_status'), 'raw': row})
    return rows


def plan_packing(source, items, documents):
    rows = packing_rows(documents)
    counts = Counter(row['identity'] for row in rows)
    by_identity = defaultdict(list)
    for item in items:
        by_identity[item_key(item)].append(item)
    changes, reviews, issues = {}, defaultdict(list), []
    valid = bool(source.get('approved')) and not source.get('invalid')
    for row in rows:
        key, values, reasons = row['identity'], row['values'], []
        matches = [item for item in items if row_meta(item).get('settlement_line_key') == row['adopted_line_key']] if row.get('adopted_line_key') else by_identity[key]
        if not valid:
            reasons.append('国际物流未审批通过或已失效，仅保留附件证据')
        if row['quality'] not in {'original', 'original_complete'} or row['archive_status'] != 'archived':
            reasons.append('附件原件或归档完整性待核对')
        if not key[0] or not key[2]:
            reasons.append('装箱行缺少明确物料编码或单位')
        if len(matches) != 1 or (not row.get('adopted_line_key') and counts[key] != 1):
            reasons.append('装箱行与当前物料无法唯一对应（编码、规格、单位）')
        if row['invalid_fields']:
            reasons.append('装箱物理字段不是有效非负数：' + ','.join(row['invalid_fields']))
        if not values:
            reasons.append('装箱行尚未识别出物理字段')
        if reasons:
            review = {**row, 'reason': '；'.join(reasons)}
            reviews[row['document_id']].append(review)
            issues.append(f"装箱行 {key[0] or row['line']}：{review['reason']}")
            continue
        item = matches[0]
        meta = deepcopy(row_meta(item))
        provenance = deepcopy(meta.get('settlement_packing_provenance') or {})
        updates, conflicts = {}, []
        for field, value in values.items():
            existing = number(item.get(field))
            empty = item.get(field) in (None, '') or (existing is not None and Decimal(existing) == 0 and field not in provenance)
            if field == 'actual_shipped_qty':
                empty = is_effectively_missing(field, item.get(field), item) and field not in provenance
            if empty:
                if existing is None or Decimal(existing) != Decimal(value):
                    updates[field] = value
                provenance[field] = {'document_id': row['document_id'], 'line_key': row['line_key'], 'line': row['line'],
                                     'source_id': source['id'], 'source_snapshot': source['snapshot'], 'value': value}
            elif existing is None or Decimal(existing) != Decimal(value):
                conflicts.append({'field': field, 'existing': item.get(field), 'candidate': value})
            elif field in provenance and provenance[field].get('document_id') != row['document_id']:
                provenance[field] = {'document_id': row['document_id'], 'line_key': row['line_key'], 'line': row['line'],
                                     'source_id': source['id'], 'source_snapshot': source['snapshot'], 'value': value}
        quantity = values.get('actual_shipped_qty')
        final_quantity = (meta.get('settlement_cargo') or {}).get('quantity',item.get('quantity'))
        if quantity is not None and (number(final_quantity) is None or Decimal(quantity) != Decimal(number(final_quantity))):
            conflicts.append({'field': 'quantity_applicability', 'existing': final_quantity, 'candidate': quantity})
        if 'actual_shipped_qty' in updates:
            updates.update(actual_shipped_qty_mode='EXPLICIT_SOURCE',shipped_uom=key[2],actual_shipped_qty_source_revision=source['snapshot'])
        if provenance != (meta.get('settlement_packing_provenance') or {}):
            meta['settlement_packing_provenance'] = provenance
        if quantity is not None:
            meta['packing_quantity'] = quantity
        if conflicts:
            review = {**row, 'reason': '装箱值与现有物理值／最终数量存在差异，保留原值待核对', 'conflicts': conflicts, 'item_name': item['name']}
            reviews[row['document_id']].append(review)
            meta['settlement_packing_review'] = True
            candidates = dict(meta.get('settlement_packing_candidates') or {})
            candidates[row['document_id']] = review
            meta['settlement_packing_candidates'] = candidates
            issues.append(f"装箱行 {key[0]}：{review['reason']}")
        if meta != row_meta(item):
            updates['extra_json'] = dumps(meta)
        if updates:
            changes[item['name']] = updates
    return {'changes': changes, 'reviews': dict(reviews), 'issues': sorted(set(issues)),
            'blocking': bool(issues), 'has_packing': bool(rows), 'valid': valid}


def retired_documents(store, source, documents, prior):
    active_ids = {d['id'] for d in documents if not document_retired(d)}
    active_files = {(d.get('manifest') or {}).get('file_id') for d in documents if not document_retired(d)}
    retired_files = {a.get('file_id') for a in source.get('attachments') or [] if a.get('retired_at')}
    result = {d['id']: d for d in documents if document_retired(d)}
    for document_id in (prior or {}).get('document_ids') or []:
        if document_id in active_ids:
            continue
        mapped = store.find('attachment_map', document_id=document_id, limit=1)
        document = (mapped[0].get('document') or {}) if mapped else {}
        file_id = (document.get('manifest') or {}).get('file_id')
        if document and file_id and (file_id in retired_files or file_id in active_files):
            result[document_id] = document
    return result


def flag_retired_physical_evidence(plan, items, retired):
    if not plan['valid']:
        return
    for item in items:
        values = plan['changes'].get(item['name'], {})
        meta = json.loads(values['extra_json']) if 'extra_json' in values else deepcopy(row_meta(item))
        by_document = defaultdict(list)
        for field, proof in (meta.get('settlement_packing_provenance') or {}).items():
            if proof.get('document_id') in retired:
                by_document[proof['document_id']].append(field)
        for document_id, fields in by_document.items():
            reason = '已采用的装箱依据已撤销或更新，保留物理值待重新核对'
            review = {'document_id': document_id, 'item_name': item['name'], 'line_key': digest(document_id, item['name'], 'retired'),
                      'reason': reason, 'retired': True, 'fields': fields}
            plan['reviews'].setdefault(document_id, []).append(review)
            plan['issues'].append(f"装箱行 {item.get('material_code') or item['name']}：{reason}")
            meta['settlement_packing_review'] = True
            meta.setdefault('settlement_packing_candidates', {})[document_id] = review
        if by_document:
            plan['changes'][item['name']] = {**values, 'extra_json': dumps(meta)}
            plan['has_packing'] = plan['blocking'] = True
    plan['issues'] = sorted(set(plan['issues']))


def register_private_file(ledger, attachment, document):
    """Ensure a private attachment link without reparenting an owned historical File.

    Frappe's Attach-field hook may already have adopted an unattached cache File.
    """
    url = str(document.get('file_url') or '')
    if not url:
        return
    decoded = unquote(url)
    if not decoded.startswith('/private/files/') or '..' in PurePosixPath(decoded).parts or '?' in decoded or '#' in decoded:
        raise ValueError(_('归档附件必须使用本地私有缓存文件'))
    frappe = getattr(ledger, 'frappe', None)
    if frappe is None:
        return  # The pure ledger has no File controller; tests may inject register_file.
    filters = {'file_url': url, 'is_private': 1, 'attached_to_doctype': 'Overseas Cost Attachment', 'attached_to_name': attachment['name']}
    if frappe.db.get_value('File', filters, 'name'):
        return
    if not frappe.db.get_value('File', {'file_url': url, 'is_private': 1}, 'name'):
        raise ValueError(_('本地私有缓存 File 记录不存在'))
    frappe.get_doc({'doctype': 'File', **filters, 'file_name': PurePosixPath(str(document.get('file_name') or decoded)).name}).insert(ignore_permissions=True)


def attachment_values(source, document, batch, version, reviews, *, audit_only=False, retired=False):
    manifest = document.get('manifest') or {}
    kinds = {table.get('kind') for table in document.get('tables') or []}
    attachment_type = 'Packing List' if 'packing' in kinds else 'Logistics Bill' if 'fee' in kinds else 'Other'
    transport = str(batch.get('transport_mode') or 'SEA').upper()
    packing_slot = {'SEA': 'sea_packing_list', 'AIR': 'air_packing_list', 'EXPRESS': 'express_goods_list'}.get(transport, 'sea_packing_list')
    slot = packing_slot if attachment_type == 'Packing List' else ''
    # 「被撤销/被替代」和「审批未完成」是两回事：前者只能留档，后者是业务常态。
    # 旧实现把两者都写进 audit_only，导致审批中的附件在页面上被显示成"仅审计"。
    closed = bool(audit_only) or bool(retired) or bool(source.get('invalid'))
    approved = bool(source.get('approved'))
    allowed = approved and not closed
    descriptor = {'document_id': document['id'], 'source_id': source['id'], 'fingerprint': document.get('fingerprint') or document['id'],
                  'manifest': manifest, 'tables': document.get('tables') or [], 'reviews': reviews,
                  'issues': document.get('issues') or [], 'status': document.get('status'), 'retired': retired,
                  'audit_only': closed, 'pending_approval': not approved and not closed}
    parsed = {'process_instance_id': source['instance'], 'corp_id': source['corp'], 'file_id': manifest.get('file_id'),
              'data_source': 'local_archive', 'approval_excluded': not approved, 'cost_source_allowed': allowed,
              'settlement_document': descriptor, 'manual_document': {'logistics_type': transport, 'slot_code': slot,
                  'slot_label': '装箱单' if slot else '物流归档资料', 'required': bool(slot), 'read_only': True}}
    notes = ['物流归档附件（只读）']
    if closed:
        notes.append('仅保留审计证据，不计入当前资料完成度')
    elif not approved:
        notes.append('审批尚未完成，暂不计入当前资料完成度')
    if retired: notes.append('附件已撤销或被新版本替代')
    if reviews: notes.append('装箱数据待核对')
    return {'batch': batch['name'], 'version': version, 'source_type': 'OA', 'attachment_type': attachment_type,
            'oa_attachment_origin': 'Comment' if str(manifest.get('attachment_origin') or manifest.get('source_kind') or '').lower() == 'comment' else 'Form',
            'source_doc_no': source.get('approval_no') or source['instance'], 'file_name': document.get('file_name') or '',
            'file_url': document.get('file_url') or '', 'parse_status': 'Parsed' if document.get('status') == 'parsed' else 'Queued' if document.get('status') == 'pending' else 'Draft',
            'parse_result_json': dumps(parsed), 'mapped_result_json': dumps({'settlement_document': descriptor}), 'remark': '；'.join(notes)}


def save_attachment(store, ledger, source, document, batch, version, reviews, register_file,
                    *, values_factory=None, **flags):
    values_factory = values_factory or attachment_values
    key = digest(document['id'], version)
    mapped = store.get('attachment_map', key)
    current = ledger.get('attachment', mapped['attachment']) if mapped else None
    if reviews is None:
        reviews = ((json.loads(current.get('parse_result_json') or '{}').get('settlement_document') or {}).get('reviews') or []) if current else []
    values = values_factory(source, document, batch, version, reviews, **flags)
    if current is None:
        current = ledger.create('attachment', values)
        register_file(current, document)
        store.put('attachment_map', {'id': key, 'document_id': document['id'], 'version': version,
                                    'attachment': current['name'], 'data': dumps({'source_id': source['id'], 'document': document})})
        return True
    changed = {key: value for key, value in values.items() if current.get(key) != value}
    if changed:
        ledger.put('attachment', current['name'], changed)
    return bool(changed)


def sync_logistics_documents(store, ledger, source, batch_name, actor, *, register_file=None):
    if source.get('kind') != 'logistics':
        raise ValueError(_('只有国际物流来源可同步装箱附件'))
    register_file = register_file or (lambda attachment, document: register_private_file(ledger, attachment, document))
    state_id = digest('logistics_documents', source['id'], batch_name)
    documents = [deepcopy(d) for d in source.get('documents') or [] if d.get('id')]
    documents_hash = digest(documents, source.get('attachments') or [])
    effective_hash = digest(source.get('packing_hash'), documents_hash, bool(source.get('approved')), bool(source.get('invalid')))
    with store.atomic():
        batch = ledger.get('batch', batch_name, lock=True)
        if not batch:
            raise ValueError(_('物流来源对应批次不存在'))
        mappings = store.find('batch_map', source_id=source['id'], limit=1)
        if not mappings or mappings[0]['batch'] != batch_name:
            raise ValueError(_('物流来源与成本批次不对应'))
        version = ledger.get('version', batch['current_version'], lock=True)
        prior = store.get('document_sync', state_id, lock=True)
        items = ledger.rows('item', batch=batch_name, version=version['name'])
        items_hash = packing_state_hash(items)
        if prior and prior.get('effective_hash') == effective_hash and prior.get('version') == version['name'] and prior.get('items_hash') == items_hash and prior['status'] != 'queued':
            return {**prior, 'changed': False, 'state_id': state_id}
        plan = plan_packing(source, items, documents)
        retired = retired_documents(store, source, documents, prior)
        flag_retired_physical_evidence(plan, items, retired)
        document_issues = [f"{d.get('file_name') or d['id']}：{issue}" for d in documents for issue in (d.get('issues') or ([] if d.get('status') == 'parsed' else ['附件内容待核对']))]
        state = {'id': state_id, 'source_id': source['id'], 'batch': batch_name, 'status': 'review' if plan['issues'] else 'applied',
                 'version': version['name'], 'packing_hash': source.get('packing_hash'), 'documents_hash': documents_hash, 'items_hash': items_hash,
                 'effective_hash': effective_hash, 'blocking': plan['blocking'], 'issues': plan['issues'], 'reviews': plan['reviews'], 'document_issues': sorted(set(document_issues)),
                 'document_ids': sorted(set((prior or {}).get('document_ids') or []) | {d['id'] for d in documents}), 'updated_at': utcnow()}
        if locked(batch):
            state.update(status='queued', blocking=plan['has_packing'] and (bool(plan['changes']) or plan['blocking']), issues=['批次正在编辑，物流附件等待同步'])
            if not prior or any(prior.get(k) != v for k, v in state.items() if k != 'updated_at'):
                store.put('document_sync', {k: state[k] for k in ('id', 'source_id', 'batch', 'status')} | {'data': dumps(state)})
            return {**state, 'changed': False, 'state_id': state_id}
        # A changed identified packing candidate is meaningful even when old values must be retained.
        physical_changes = any(set(values) & PHYSICAL_ALIASES.keys() for values in plan['changes'].values())
        requires_draft = plan['valid'] and (physical_changes or (plan['has_packing'] and plan['blocking']))
        if frozen(batch, version) and requires_draft:
            version = mutable_version(ledger, batch)
            batch = ledger.get('batch', batch_name)
            items = ledger.rows('item', batch=batch_name, version=version['name'])
            plan = plan_packing(source, items, documents)
            flag_retired_physical_evidence(plan, items, retired)
        current_frozen = frozen(batch, version)
        changed = False
        if not current_frozen and plan['valid']:
            for name, values in plan['changes'].items():
                ledger.put('item', name, {**values, **{field: 0 for field in DERIVED_FIELDS}})
                changed = True
            if plan['changes'] or plan['blocking']:
                ledger.put('batch', batch_name, {'status': 'Dirty'})
                ledger.put('version', version['name'], {'calculated_at': None, 'summary_snapshot_json': '{}'})
        target_version = '' if current_frozen else version['name']
        for document in documents:
            changed |= save_attachment(store, ledger, source, document, batch, target_version, plan['reviews'].get(document['id'], []),
                                       register_file, audit_only=current_frozen, retired=document_retired(document))
        active_ids = {d['id'] for d in documents}
        active_files = {(d.get('manifest') or {}).get('file_id') for d in documents if not document_retired(d)}
        retired_files = {a.get('file_id') for a in source.get('attachments') or [] if a.get('retired_at')}
        # Only explicit retirement, superseding the same file, or approval exclusion may retire a card.
        for document_id in state['document_ids']:
            if document_id in active_ids:
                continue
            mapped_rows = store.find('attachment_map', document_id=document_id, limit=1)
            if not mapped_rows:
                continue
            mapped = mapped_rows[0]
            old_document = mapped.get('document') or {}
            file_id = (old_document.get('manifest') or {}).get('file_id')
            retired = bool(file_id and (file_id in retired_files or file_id in active_files))
            if not old_document or not (retired or source.get('invalid') or not source.get('approved')):
                continue
            # Frozen mappings are retained byte-for-byte. Copy their evidence into current audit cards.
            changed |= save_attachment(store, ledger, source, old_document, batch, target_version, plan['reviews'].get(document_id), register_file, audit_only=True, retired=retired)
        issues = sorted(set(plan['issues'] + document_issues))
        state.update(version=version['name'], status='review' if issues else 'applied', blocking=plan['blocking'], issues=issues, reviews=plan['reviews'],
                     items_hash=packing_state_hash(ledger.rows('item', batch=batch_name, version=version['name'])))
        store.put('document_sync', {k: state[k] for k in ('id', 'source_id', 'batch', 'status')} | {'data': dumps(state)})
        if changed or (prior and prior.get('effective_hash') != effective_hash):
            store.audit(source['id'], 'sync_documents', actor, source_snapshot=source['snapshot'], version=version['name'], issues=plan['issues'])
        return {**state, 'changed': changed, 'state_id': state_id}


def sync_source_documents(store, ledger, source, batch_name, actor, *, source_context, register_file=None, trusted_review_actor=None):
    """Apply the expense's packing into an isolated overlay, never old raw values."""
    if source_context.get('root_kind') != 'expense':
        return sync_logistics_documents(store,ledger,source,batch_name,actor,register_file=register_file)
    from overseas_costing.services.effective_source_values import project_source_values, PHYSICAL_FIELDS
    from overseas_costing.services.effective_logistics_source import context_for_source
    register_file = register_file or (lambda attachment, document: register_private_file(ledger, attachment, document))
    with store.atomic():
        binding = store.get('binding',source_context['binding_id'],lock=True)
        actual = store.get('source',binding['expense_id'],lock=True) if binding else None
        batch = ledger.get('batch',batch_name,lock=True)
        if not binding or context_for_source(actual,binding,batch['current_version'],batch_name) != source_context:
            raise ValueError(_('采购支出来源已变化，装箱结果不能写回'))
        if locked(batch) and (not trusted_review_actor or batch.get('edit_lock_owner') != trusted_review_actor):
            raise ValueError(_('批次正在编辑，采购支出装箱资料等待应用'))
        version = batch['current_version']
        state_id = digest('expense_documents',source['id'],batch_name)
        documents = [deepcopy(d) for d in source.get('documents') or [] if d.get('id')]
        items = ledger.rows('item',batch=batch_name,version=version)
        effective_hash = digest(source_context,documents)
        prior = store.get('document_sync',state_id,lock=True)
        items_hash = source_packing_state_hash(items, source_context)
        if prior and prior.get('effective_hash') == effective_hash and prior.get('items_hash') == items_hash:
            return {**prior,'changed':False}
        projected = []
        for item in items:
            row = project_source_values(item,source_context)
            meta = row_meta(row)
            meta['settlement_packing_provenance'] = {k:v for k,v in (meta.get('settlement_packing_provenance') or {}).items()
                if v.get('source_id') == source['id'] and v.get('source_snapshot') == source['snapshot']}
            meta.pop('settlement_packing_candidates',None)
            row['extra_json'] = dumps(meta)
            projected.append(row)
        body_rows = []
        cached_rows = packing_rows(documents)
        for goods in source.get('goods') or []:
            if not goods.get('physical') and not goods.get('reviewed_physical'):
                continue
            fields = dict(goods.get('raw') or {})
            if goods.get('reviewed_physical'):
                fields = {'物料编码':goods.get('material_code'),'规格':goods.get('spec_model'),
                          '单位':goods.get('unit'), **{
                              PHYSICAL_ALIASES[key][0]:value for key,value in goods['reviewed_physical'].items()
                              if key in PHYSICAL_ALIASES}}
                # Do not count a reviewed projection of the same cached row twice.
                key = (identity(goods.get('material_code')),identity(goods.get('spec_model')),identity(goods.get('unit')))
                values = {k:v for k,v in goods['reviewed_physical'].items() if k in PHYSICAL_ALIASES}
                equivalent = [r for r in cached_rows if r['identity'] == key and not r['invalid_fields']
                              and all(number(r['values'].get(k)) is not None and Decimal(number(r['values'][k])) == Decimal(str(v)) for k,v in values.items())]
                if len(equivalent) == 1 and len([r for r in cached_rows if r['identity'] == key]) == 1:
                    continue
            body_rows.append({'rowValue':[{'name':key,'value':value} for key,value in fields.items()],
                              'position':goods.get('source_position'),'settlement_line_key':goods['line_key']})
        packing_documents = documents + ([{'id':'approval-body:'+source['snapshot'],
             'manifest':{'archive_quality':'original','archive_status':'archived'},
             'tables':[{'kind':'packing','rows':body_rows}]}] if body_rows else [])
        plan = plan_packing(source,projected,packing_documents)
        for item in projected:
            updates = plan['changes'].get(item['name']) or {}
            meta = row_meta({**item,**updates})
            values = {key:updates.get(key,item.get(key)) for key in PHYSICAL_FIELDS
                      if updates.get(key,item.get(key)) is not None}
            meta['settlement_physical'] = {'source_snapshot':source['snapshot'],
                'source_context_fingerprint':source_context['fingerprint'], 'values':values,
                'evidence':meta.get('settlement_packing_provenance') or {}}
            has_values = any(values.get(key) is not None for key in ('gross_weight_kg','volume_m3','chargeable_weight_kg'))
            row_reviews = [r for rows in plan['reviews'].values() for r in rows if r.get('item_name') == item['name']]
            if has_values and not row_reviews:
                meta.pop('settlement_packing_review',None)
            if meta != row_meta(next(i for i in items if i['name'] == item['name'])):
                ledger.put('item',item['name'],{'extra_json':dumps(meta), **{key:0 for key in DERIVED_FIELDS}})
        document_issues = sorted({issue for d in documents for issue in d.get('issues') or []})
        for document in documents:
            save_attachment(store,ledger,source,document,batch,version,plan['reviews'].get(document['id'],[]),
                            register_file, retired=document_retired(document))
        remaining = ledger.rows('item',batch=batch_name,version=version)
        state = {'id':state_id,'source_id':source['id'],'batch':batch_name,'version':version,
                 'status':'review' if plan['issues'] or document_issues else 'applied',
                 'source_context':source_context,'effective_hash':effective_hash,
                 'items_hash':source_packing_state_hash(remaining, source_context),
                 'blocking':bool(plan['blocking'] or document_issues),'issues':sorted(set(plan['issues']+document_issues)),
                 'reviews':plan['reviews'],'document_issues':document_issues,'document_ids':[d['id'] for d in documents]}
        store.put('document_sync',{k:state[k] for k in ('id','source_id','batch','status')}|{'data':dumps(state)})
        return {**state,'changed':True}
