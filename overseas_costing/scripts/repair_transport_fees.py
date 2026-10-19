"""Approved current-version fee repair. Default: read-only preflight.

Apply requires a preflight manifest plus a verified local database backup.
This is an administrative bench script, not a whitelisted HTTP endpoint.
Each batch owns one transaction; no item, cost result, confirmation or ERP writes.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path

from overseas_costing.services import batch_service, edit_session_service, fee_service

REPAIR_KEY = 'unified_transport_fees_20260908'
RETIREMENTS = {
    'kr2rgs4kp1': dict(rule='kr2rgs4kp1', batch='sjgde0rgc3', batch_no='202606151547000071699',
        version='sjg4j03827', transport_mode='AIR', logical_fee_key='air_forwarder_surcharge',
        amount='5000', primary_rule='kncvrfasoh', primary_amount='7000'),
    'urp34dkllu': dict(rule='urp34dkllu', batch='vunn2lvq4k', batch_no='202609032107000062462',
        version='vunna0uia0', transport_mode='SEA', logical_fee_key='sea_port_forwarder_surcharge',
        amount='3000', primary_rule='uvgpcv5kqo', primary_amount='2004'),
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def snapshot_hash(snapshot):
    return hashlib.sha256(_json(snapshot).encode()).hexdigest()


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _enabled(rule):
    return all(rule.get(field) not in (0, '0', False) for field in ('is_enabled', 'is_active'))


def plan_batch_repair(snapshot):
    """Compute exact allowed updates from trusted snapshots without mutating them."""
    batch, version = snapshot['batch'], snapshot['version']
    rules = snapshot['rules']
    result = dict(batch=batch['name'], batch_no=batch.get('batch_no'), version=version.get('name'),
                  transport_mode=batch.get('transport_mode'), snapshot_hash=snapshot_hash(snapshot),
                  status='unchanged', changes=[])

    def skip(code, message):
        return {**result, 'status': 'skipped', 'reason_code': code, 'message': message, 'changes': []}

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
    try:
        primary = fee_service.primary_freight_definition(batch.get('transport_mode'))
    except ValueError:
        return skip('UNKNOWN_TRANSPORT', '运输方式不明确，不能自动归类主运费。')
    if not primary:
        return skip('UNKNOWN_TRANSPORT', '运输方式不明确，不能自动归类主运费。')
    effective_rules = [{**r, **fee_service.legacy_oa_freight_updates(r, batch['transport_mode'])} for r in rules]
    primaries = [r for r in effective_rules if _enabled(r)
                 and fee_service.map_historical_fee_key(r, batch['transport_mode']) == primary['logical_fee_key']]
    if len(primaries) > 1:
        return skip('PRIMARY_FREIGHT_CONFLICT', '存在多条有效主运费，需先核对，不能自动合并计费。')

    for spec in RETIREMENTS.values():
        if spec['batch'] != batch['name']:
            continue
        target = next((r for r in rules if r['name'] == spec['rule']), None)
        main = next((r for r in rules if r['name'] == spec['primary_rule']), None)
        if (version['name'] != spec['version'] or batch.get('batch_no') != spec['batch_no']
            or batch.get('transport_mode') != spec['transport_mode'] or not target or not main
            or target.get('logical_fee_key') != spec['logical_fee_key']
            or _number(target.get('amount')) != _number(spec['amount']) or target.get('currency') != 'RMB'
            or target.get('amount_status') != 'ACTUAL'
            or main.get('logical_fee_key') != primary['logical_fee_key'] or not _enabled(main)
            or _number(main.get('amount')) != _number(spec['primary_amount']) or main.get('currency') != 'RMB'):
            return skip('RETIREMENT_TARGET_CHANGED', '指定停用记录或要保留的主运费与已批准值不一致。')
        if _enabled(target):
            values = {'is_enabled': 0, 'is_active': 0,
                      'remark': (target.get('remark') or '') + f'\n[{REPAIR_KEY}] 按用户确认停用，不再计费，保留原始记录。'}
            result['changes'].append(dict(rule_name=target['name'], kind='retire_authorized_surcharge', values=values))

    for rule in rules:
        if not _enabled(rule):
            continue
        proposed = fee_service.legacy_oa_freight_updates(rule, batch['transport_mode'])
        values = {field: value for field, value in (proposed or {}).items()
                  if field in {'logical_fee_key', 'expense_category', 'amount_status'} and rule.get(field) != value}
        if values:
            result['changes'].append(dict(rule_name=rule['name'], kind='normalize_oa_freight', values=values))
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
                        'message': '预检后批次或费用已变化，请重新预检。'}
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
        rows = self.frappe.db.sql('''SELECT DISTINCT b.name
            FROM `tabOverseas Cost Batch` b JOIN `tabOverseas Cost Allocation Rule` r
            ON r.batch=b.name AND r.version=b.current_version
            WHERE r.rule_code=%s ORDER BY b.name''', ('oa_logistics_freight',), as_dict=True)
        # Missing/changed retirement targets must be reported, never silently omitted.
        return sorted({r['name'] for r in rows} | {spec['batch'] for spec in RETIREMENTS.values()})

    def load(self, name, *, lock=False):
        f = self.frappe
        fields = ['name','batch_no','current_version','transport_mode','status','confirm_status','is_locked',
                  'modified','writeback_status','source_approval_status','extra_json',
                  'edit_lock_owner','edit_lock_expires_at']
        if lock:
            # Locking SELECTs are current reads under production REPEATABLE-READ.
            # A separate lock followed by get_value/get_all may reuse an older snapshot.
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
                                (version,name), as_dict=True)
            version_row = versions[0] if versions else {}
        else:
            version_row = f.db.get_value('Overseas Cost Version', version, ['*'], as_dict=True) or {}
        def rows(doctype):
            if lock:
                return f.db.sql(f'SELECT * FROM `tab{doctype}` WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE',
                                (name,version), as_dict=True)
            return f.get_all(doctype, filters={'batch':name,'version':version}, fields=['*'], order_by='name asc', limit_page_length=0)
        return {'batch':dict(batch), 'version':dict(version_row),
                'items':[dict(r) for r in rows('Overseas Cost Item')],
                'rules':[dict(r) for r in rows('Overseas Cost Allocation Rule')]}

    def validate_backup(self, path):
        resolved = Path(path).resolve()
        backup_dir = Path(self.frappe.get_site_path('private','backups')).resolve()
        if resolved.parent != backup_dir or not resolved.name.endswith('-database.sql.gz') or not resolved.is_file():
            raise ValueError(_('必须使用当前站点 private/backups 内的数据库备份。'))
        if resolved.stat().st_size < 100:
            raise ValueError(_('数据库备份为空。'))
        with gzip.open(resolved, 'rb') as stream:
            total = 0
            for block in iter(lambda: stream.read(1024*1024), b''):
                total += len(block)
            if not total:
                raise ValueError(_('数据库备份不可读。'))
        digest = hashlib.sha256()
        with resolved.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        return {'path':str(resolved),'sha256':digest.hexdigest()}

    def save(self, before, plan):
        f = self.frappe
        name, version = before['batch']['name'], before['version']['name']
        originals = {r['name']:r for r in before['rules']}
        changed_names = {change['rule_name'] for change in plan['changes']}
        expected = {key:dict(value) for key,value in originals.items()}
        for change in plan['changes']:
            values = dict(change['values'])
            original = originals[change['rule_name']]
            if 'amount_status' in values:
                revision = original.get('amount_revision') or ''
                values['amount_revision'] = 'oa:repair:' + plan['snapshot_hash'][:20] if not revision or revision.startswith('oa:') else revision
            expected[change['rule_name']].update(values)
            f.db.set_value('Overseas Cost Allocation Rule', change['rule_name'], values, update_modified=True)
        f.db.set_value('Overseas Cost Batch', name, 'status', 'Dirty', update_modified=True)
        after = self.load(name, lock=True)
        if after['items'] != before['items'] or after['version'] != before['version']:
            raise RuntimeError('修复不得修改物料或成本结果。')
        if {r['name'] for r in after['rules']} != set(originals):
            raise RuntimeError('修复不得新增或删除费用。')
        for rule in after['rules']:
            want = expected[rule['name']]
            fields = set(want) - ({'modified','modified_by'} if rule['name'] in changed_names else set())
            if any(_json(rule.get(k)) != _json(want.get(k)) for k in fields):
                raise RuntimeError('费用更新超出了已批准的字段范围。')
        for field,value in before['batch'].items():
            if field not in {'status','modified'} and after['batch'].get(field) != value:
                raise RuntimeError('批次其他属性发生变化，已停止修复。')
        audit_fields = ['name','batch_no','current_version','status','modified','confirm_status','writeback_status']
        def audit_snapshot(data):
            return dict(batch={k:data['batch'].get(k) for k in audit_fields},
                        rules=[r for r in data['rules'] if r['name'] in changed_names],
                        items_sha256=snapshot_hash(data['items']), version_sha256=snapshot_hash(data['version']))
        audit = f.get_doc(dict(doctype='Overseas Cost Audit Log',batch=name,version=version,action_type='BATCH_EDIT',
            field_name=REPAIR_KEY,old_value=_json(audit_snapshot(before)),new_value=_json(audit_snapshot(after)),
            operator_name=f.session.user,action_remark='按批准的全运输方式费用规则修复；金额币种保持，指定附加费停用留档；批次待重算。')).insert(ignore_permissions=True)
        return audit.name

    def commit(self):
        self.frappe.db.commit()

    def rollback(self):
        self.frappe.db.rollback()
