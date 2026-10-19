"""Repair shipment values for batch 202609032107000062462. Default: preview only.

Apply requires a preflight manifest plus a verified local database backup.
This is an administrative bench script, not a whitelisted HTTP endpoint.
"""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import gzip
import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from overseas_costing.services import batch_service, edit_session_service
from overseas_costing.services.logistics_settlement.application import row_meta
from overseas_costing.services.logistics_settlement.model import digest
from overseas_costing.services.logistics_settlement.valuation import reconcile_replacement_value, value_final_cargo
from overseas_costing.services.logistics_settlement.item_metadata import persist_item_meta, prune_analysis_cache
from overseas_costing.services.material_input_service import present_material_row
from overseas_costing.services.shipment_cost_service import number, object_json, shipment_value
from overseas_costing.utils.field_mapper import normalize_unit

REPAIR_KEY = 'shipment_valuation_unify_20260911'
BATCH = 'vunn2lvq4k'
BATCH_NO = '202609032107000062462'
EXPECTED_ROWS = (
    dict(material_code='FL000429', qty='96000', uom='个', action='restore', amount='14496'),
    dict(material_code='FL000429', qty='4000', uom='个', action='restore', amount='604'),
    dict(material_code='FL000427', qty='4000', uom='个', action='restore', amount='604'),
    dict(material_code='FL000427', qty='96000', uom='个', action='restore', amount='14496'),
    dict(material_code='FL000428', qty='100000', uom='个', action='restore', amount='15100'),
    dict(material_code='FL000430', qty='100000', uom='个', action='restore', amount='15100'),
    dict(material_code='FL003377', qty='22000', uom='个', action='conflict', amount='2860', prior='26000'),
    dict(material_code='CW000191', qty='2400', uom='个', action='missing'),
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def snapshot_hash(snapshot):
    return hashlib.sha256(_json(snapshot).encode()).hexdigest()


def _same(left, right):
    return number(left) is not None and number(right) is not None and Decimal(number(left)) == Decimal(number(right))


def _fields_equal(left, right):
    if _same(left, right):
        return True
    if isinstance(left, (dict, str)) or isinstance(right, (dict, str)):
        parsed_left, parsed_right = object_json(left), object_json(right)
        if parsed_left or parsed_right:
            return parsed_left == parsed_right
    return _json(left) == _json(right)


def _audit_item(row):
    meta = object_json(row.get('extra_json'))
    valuation = meta.get('settlement_valuation') or meta.get('shipment_valuation') or {}
    payload = (row.get('extra_json') or '')
    if not isinstance(payload, str):
        payload = _json(payload)
    return {
        'name': row.get('name'),
        'material_code': row.get('material_code'),
        'goods_value': row.get('goods_value'),
        'valuation_status': valuation.get('status'),
        'amount_rmb': valuation.get('amount_rmb'),
        'prior_amount_rmb': valuation.get('prior_amount_rmb'),
        'calculated_amount_rmb': valuation.get('calculated_amount_rmb'),
        'extra_json_sha256': hashlib.sha256(payload.encode()).hexdigest(),
    }


def _qty_key(value):
    amount = number(value)
    return '' if amount is None else format(amount.normalize(), 'f')


def _identity(item):
    return (str(item.get('material_code') or '').strip(),
            _qty_key(item.get('actual_shipped_qty')),
            normalize_unit(item.get('shipped_uom') or item.get('unit') or ''))


def _adoption(version):
    meta = object_json(version.get('extra_json'))
    packing = meta.get('packing_source_selection') or (meta.get('freight_settlement') or {}).get('packing_review_id')
    return meta.get('ai_row_adoption') or packing


def _fx(version):
    return {key: value for key, value in (version or {}).items() if str(key).startswith('fx_')}


def _cargo(item):
    cargo = row_meta(item).get('settlement_cargo')
    if isinstance(cargo, dict) and cargo.get('quantity') is not None:
        return cargo
    return {'quantity': str(item.get('actual_shipped_qty') or ''),
            'unit': item.get('shipped_uom') or item.get('unit'),
            'material_code': item.get('material_code')}


def _manual(item):
    manual = row_meta(item).get('manual_shipment_valuation')
    return isinstance(manual, dict) and bool(manual.get('confirmed') and manual.get('manual'))


def _match_rows(items):
    remaining = list(items)
    matched = []
    for spec in EXPECTED_ROWS:
        key = (spec['material_code'], _qty_key(spec['qty']), normalize_unit(spec['uom']))
        found = [item for item in remaining if _identity(item) == key]
        if len(found) != 1:
            return None
        remaining.remove(found[0])
        matched.append((spec, found[0]))
    return matched if not remaining else None


def _conflict_prior(item, spec):
    meta = row_meta(item)
    originals = meta.get('ai_fill_original_values') or meta.get('settlement_original_values') or {}
    fact = (meta.get('logistics_row') or {}).get('purchase_fact') or {}
    for candidate in (item.get('goods_value'), originals.get('goods_value'), fact.get('goods_value')):
        if _same(candidate, spec['prior']):
            return {'goods_value': spec['prior'], 'extra_json': '{}'}
    return None


def _desired(item, spec, version):
    cargo = _cargo(item)
    fx = _fx(version)
    if spec['action'] == 'restore':
        calculated = value_final_cargo(item, cargo, fx)
        if calculated.get('error') or not _same(calculated.get('amount_rmb'), spec['amount']):
            return None
        return calculated
    if spec['action'] == 'conflict':
        prior = _conflict_prior(item, spec)
        if prior is None:
            return None
        result = reconcile_replacement_value(item, cargo, fx, prior)
        if result.get('status') != 'conflict' or not _same(result.get('prior_amount_rmb'), spec['prior']):
            return None
        if not _same(result.get('calculated_amount_rmb'), spec['amount']):
            return None
        return result
    calculated = value_final_cargo(item, cargo, fx)
    if calculated.get('amount_rmb') is not None:
        return None
    return {**calculated, 'status': 'missing', 'amount_rmb': None}


def _already(item, spec, version):
    current = shipment_value(present_material_row(dict(item)))
    if spec['action'] == 'restore':
        return current.get('status') == 'automatic' and _same(current.get('amount_rmb'), spec['amount'])
    if spec['action'] == 'conflict':
        return (current.get('status') == 'conflict' and _same(current.get('prior_amount_rmb'), spec['prior'])
                and _same(current.get('calculated_amount_rmb'), spec['amount']) and current.get('amount_rmb') is None)
    return current.get('status') == 'missing' and current.get('amount_rmb') is None


def _persist(item, valuation):
    meta = prune_analysis_cache(deepcopy(row_meta(item)))
    key = 'settlement_valuation' if isinstance(meta.get('settlement_cargo'), dict) else 'shipment_valuation'
    meta[key] = valuation
    meta['shipment_value_repair'] = {'key': REPAIR_KEY, 'status': valuation.get('status')}
    amount = valuation.get('amount_rmb')
    return {'extra_json': persist_item_meta(meta),
            'goods_value': 0 if amount is None else amount}


def plan_batch_repair(snapshot):
    batch, version = snapshot['batch'], snapshot['version']
    result = dict(batch=batch['name'], batch_no=batch.get('batch_no'), version=version.get('name'),
                  snapshot_hash=snapshot_hash(snapshot), status='unchanged', changes=[],
                  adoption=digest(_adoption(version)))

    def skip(code, message):
        return {**result, 'status': 'skipped', 'reason_code': code, 'message': message, 'changes': []}

    if batch.get('name') != BATCH or batch.get('batch_no') != BATCH_NO:
        return skip('BATCH_CHANGED', '批次编号与批准的修复目标不一致。')
    if batch.get('current_version') != version.get('name') or version.get('batch') != batch['name']:
        return skip('VERSION_CHANGED', '当前版本或所属批次已变化。')
    if (batch.get('is_locked') or batch.get('confirm_status') in {'Confirmed', 'Partially Confirmed'}
            or batch.get('status') in {'Confirmed', 'Written Back'} or version.get('status') != 'Active'):
        return skip('FROZEN', '已锁定、确认、回写或归档的批次不自动修改。')
    if edit_session_service.lock_is_active(batch):
        return skip('EDIT_IN_PROGRESS', '批次存在有效编辑会话，请结束编辑后重新预检。')
    invalid = batch_service._build_invalid_business_state(batch, snapshot['items'])
    if invalid.get('invalid'):
        return skip('INVALID_APPROVAL', invalid.get('message') or '审批已失效。')
    if not _adoption(version):
        return skip('ADOPTION_CHANGED', '当前版本缺少已采用的物料分析记录，停止修复。')
    matched = _match_rows(snapshot['items'])
    if matched is None:
        return skip('ROWS_CHANGED', '物料行身份、数量或单位已变化，停止修复。')

    for spec, item in matched:
        if _manual(item) or _already(item, spec, version):
            continue
        desired = _desired(item, spec, version)
        if desired is None:
            return skip('EVIDENCE_CHANGED', f'{spec["material_code"]} 现有证据无法按批准口径恢复货值。')
        change = dict(item_name=item['name'], material_code=spec['material_code'], kind=spec['action'],
                      values=_persist(item, desired))
        if spec['action'] == 'conflict':
            change.update(prior_amount_rmb=desired.get('prior_amount_rmb'),
                          calculated_amount_rmb=desired.get('calculated_amount_rmb'))
        result['changes'].append(change)
    if result['changes']:
        result['status'] = 'ready'
    return result


def run(*, apply=False, manifest=None, backup_path=None, repository=None):
    repo = repository or FrappeRepairRepository()
    repo.assert_operator()
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    if apply and (not isinstance(manifest, dict) or not manifest or not backup_path):
        raise ValueError(_('执行前必须提供只读预检清单 manifest 和数据库备份路径 backup_path。'))
    backup = repo.validate_backup(backup_path) if apply else None
    results, output_manifest = [], {}
    names = sorted(manifest) if apply else repo.candidates()
    for name in names:
        try:
            before = repo.load(name, lock=bool(apply))
            plan = plan_batch_repair(before)
            if apply and manifest.get(name) != plan['snapshot_hash']:
                plan = {**plan, 'status': 'skipped', 'changes': [], 'reason_code': 'SOURCE_CHANGED',
                        'message': '预检后批次、物料或采用记录已变化，请重新预检。'}
            if plan['status'] == 'ready':
                output_manifest[name] = plan['snapshot_hash']
                if apply:
                    audit = repo.save(before, plan)
                    repo.commit()
                    plan = {**plan, 'status': 'applied', 'audit_name': audit}
            if not apply or plan['status'] != 'applied':
                repo.rollback()
            results.append(plan)
        except Exception as exc:
            repo.rollback()
            results.append(dict(batch=name, status='skipped', changes=[], reason_code='TRANSACTION_FAILED',
                                message=f'{type(exc).__name__}: {exc}'))
    return dict(ok=not any(r['status'] == 'skipped' for r in results), repair=REPAIR_KEY, dry_run=not apply,
                candidate_count=len(names), ready_count=sum(r['status'] == 'ready' for r in results),
                applied_count=sum(r['status'] == 'applied' for r in results),
                unchanged_count=sum(r['status'] == 'unchanged' for r in results),
                skipped_count=sum(r['status'] == 'skipped' for r in results),
                manifest=output_manifest, backup=backup, results=results)


class FrappeRepairRepository:
    def __init__(self):
        import frappe
        self.frappe = frappe

    def assert_operator(self):
        if self.frappe.session.user == 'Guest' or 'System Manager' not in self.frappe.get_roles():
            raise PermissionError(_('此修复仅允许 System Manager 在管理终端执行。'))

    def candidates(self):
        return [BATCH]

    def load(self, name, *, lock=False):
        f = self.frappe
        fields = ['name', 'batch_no', 'current_version', 'transport_mode', 'status', 'confirm_status', 'is_locked',
                  'modified', 'writeback_status', 'source_approval_status', 'extra_json',
                  'edit_lock_owner', 'edit_lock_expires_at']
        if lock:
            columns = ','.join(f'`{field}`' for field in fields)
            batch_rows = f.db.sql(f'SELECT {columns} FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE',
                                  (name,), as_dict=True)
            batch = batch_rows[0] if batch_rows else None
        else:
            batch = f.db.get_value('Overseas Cost Batch', name, fields, as_dict=True)
        if not batch or not batch.get('current_version'):
            raise ValueError(_('批次不存在或没有当前版本。'))
        version = batch['current_version']
        if lock:
            versions = f.db.sql('SELECT * FROM `tabOverseas Cost Version` WHERE name=%s AND batch=%s FOR UPDATE',
                                (version, name), as_dict=True)
            version_row = versions[0] if versions else {}
        else:
            version_row = f.db.get_value('Overseas Cost Version', version, ['*'], as_dict=True) or {}

        def rows(doctype):
            if lock:
                return f.db.sql(f'SELECT * FROM `tab{doctype}` WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE',
                                (name, version), as_dict=True)
            return f.get_all(doctype, filters={'batch': name, 'version': version}, fields=['*'],
                             order_by='name asc', limit_page_length=0)

        return {'batch': dict(batch), 'version': dict(version_row),
                'items': [dict(row) for row in rows('Overseas Cost Item')],
                'rules': [dict(row) for row in rows('Overseas Cost Allocation Rule')]}

    def validate_backup(self, path):
        resolved = Path(path).resolve()
        backup_dir = Path(self.frappe.get_site_path('private', 'backups')).resolve()
        if resolved.parent != backup_dir or not resolved.name.endswith('-database.sql.gz') or not resolved.is_file():
            raise ValueError(_('必须使用当前站点 private/backups 内的数据库备份。'))
        if resolved.stat().st_size < 100:
            raise ValueError(_('数据库备份为空。'))
        with gzip.open(resolved, 'rb') as stream:
            total = 0
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                total += len(block)
            if not total:
                raise ValueError(_('数据库备份不可读。'))
        digest_value = hashlib.sha256()
        with resolved.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest_value.update(block)
        return {'path': str(resolved), 'sha256': digest_value.hexdigest()}

    def save(self, before, plan):
        f = self.frappe
        name, version = before['batch']['name'], before['version']['name']
        originals = {row['name']: dict(row) for row in before['items']}
        expected = {key: dict(value) for key, value in originals.items()}
        changed = {change['item_name'] for change in plan['changes']}
        for change in plan['changes']:
            expected[change['item_name']].update(change['values'])
            f.db.set_value('Overseas Cost Item', change['item_name'], change['values'], update_modified=True)
        f.db.set_value('Overseas Cost Batch', name, 'status', 'Dirty', update_modified=True)
        after = self.load(name, lock=True)
        if after['rules'] != before['rules'] or after['version'] != before['version']:
            raise RuntimeError('修复不得修改费用或成本版本。')
        if {row['name'] for row in after['items']} != set(originals):
            raise RuntimeError('修复不得新增或删除物料。')
        after_items = {row['name']: row for row in after['items']}
        mutable = {'goods_value', 'extra_json', 'modified', 'modified_by'}
        for item_name, original in originals.items():
            current = after_items[item_name]
            want = expected[item_name]
            ignored = mutable if item_name in changed else {'modified', 'modified_by'}
            for field in set(original) | set(current):
                if field in ignored:
                    continue
                if not _fields_equal(original.get(field), current.get(field)):
                    raise RuntimeError('物料更新超出了已批准的字段范围。')
            if item_name in changed and not _fields_equal(current.get('goods_value'), want.get('goods_value')):
                raise RuntimeError('物料货值未按批准口径写入。')
            if item_name in changed:
                stored = object_json(current.get('extra_json'))
                intended = object_json(want.get('extra_json'))
                stored_val = stored.get('settlement_valuation') or stored.get('shipment_valuation') or {}
                intended_val = intended.get('settlement_valuation') or intended.get('shipment_valuation') or {}
                if stored_val.get('status') != intended_val.get('status'):
                    raise RuntimeError('物料估值状态未按批准口径写入。')
        for field, value in before['batch'].items():
            if field not in {'status', 'modified'} and after['batch'].get(field) != value:
                raise RuntimeError('批次其他属性发生变化，已停止修复。')
        audit_fields = ['name', 'batch_no', 'current_version', 'status', 'modified', 'confirm_status', 'writeback_status']

        def audit_snapshot(data):
            return dict(batch={key: data['batch'].get(key) for key in audit_fields},
                        items=[_audit_item(row) for row in data['items'] if row['name'] in changed],
                        rules_sha256=snapshot_hash(data['rules']), version_sha256=snapshot_hash(data['version']))

        audit = f.get_doc(dict(
            doctype='Overseas Cost Audit Log', batch=name, version=version, action_type='BATCH_EDIT',
            field_name=REPAIR_KEY, old_value=_json(audit_snapshot(before)), new_value=_json(audit_snapshot(after)),
            operator_name=f.session.user,
            action_remark='按批准口径恢复本次发货货值：前六行自动估值，第七行保留旧值/计算值冲突，第八行待人工确认。',
        )).insert(ignore_permissions=True)
        return audit.name

    def commit(self):
        self.frappe.db.commit()

    def rollback(self):
        self.frappe.db.rollback()
