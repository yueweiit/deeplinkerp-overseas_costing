"""Atomic stable-key material bulk edits for the material workspace."""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from datetime import datetime
import json


MAX_BULK_MATERIALS = 1000


def _stable_keys(values):
    keys = [str(value or '').strip() for value in values or []]
    if not keys or any(not key for key in keys):
        raise ValueError(_('请选择至少一行可删除物料。'))
    if len(keys) != len(set(keys)):
        raise ValueError(_('物料选择存在重复，请清除后重试。'))
    if len(keys) > MAX_BULK_MATERIALS:
        raise ValueError(_("一次最多删除 {0} 行物料。").format(MAX_BULK_MATERIALS))
    return keys


def bulk_exclude_materials(batch_name, version_name, stable_line_keys, edit_token, expected_modified, *,
                           reason='', repository=None, actor=''):
    """Soft-exclude all requested rows after validating the complete request."""
    keys = _stable_keys(stable_line_keys)
    repo = repository or FrappeMaterialBulkEditRepository()
    repo.assert_write(batch_name, edit_token, expected_modified)
    state = repo.load(batch_name, version_name, lock=True)
    if str(state.get('batch_modified') or '') != str(expected_modified or ''):
        raise ValueError(_('批次已变化，请刷新后重新选择。'))
    if not state.get('editable'):
        raise ValueError(_('历史、已确认、已回写或锁定版本不能删除物料。'))
    by_key = {}
    for item in state.get('items') or []:
        key = str(item.get('stable_line_key') or '').strip()
        if key:
            by_key.setdefault(key, []).append(item)
    if any(len(by_key.get(key) or []) != 1 or int((by_key[key][0]).get('is_excluded') or 0) for key in keys):
        raise ValueError(_('所选物料已变化，请刷新后重新选择。'))
    selected_keys = set(keys)
    for group in state.get('groups') or []:
        if group.get('status') == 'removed':
            continue
        members = {str(key or '').strip() for key in group.get('member_keys') or [] if str(key or '').strip()}
        if selected_keys.intersection(members) and not members.issubset(selected_keys):
            raise ValueError(_('删除组内物料时必须选择完整装箱组；如只删除单个组员，请先解除合并。'))
    selected = [deepcopy(by_key[key][0]) for key in keys]
    saved = repo.save_exclusions(
        state, selected, keys, str(reason or '').strip() or '批量软排除物料', str(actor or ''),
    )
    return {
        'ok':True, 'soft_excluded':True, 'excluded_count':len(keys),
        'stable_line_keys':keys, 'item_names':[str(item.get('name') or '') for item in selected],
        'affected_group_ids':list(saved.get('affected_group_ids') or []),
        'batch_name':state['batch'], 'version_name':state['version'],
        'batch_modified':str(saved.get('batch_modified') or ''),
        'message':f'已软排除 {len(keys)} 行物料，可在“已排除物料”中恢复。',
    }


class FrappeMaterialBulkEditRepository:
    def __init__(self):
        import frappe
        self.frappe = frappe

    def assert_write(self, batch, edit_token, expected_modified):
        from overseas_costing.services import edit_session_service
        edit_session_service.assert_batch_write(
            batch, edit_token=edit_token, expected_modified=expected_modified,
        )

    def load(self, batch_name, version_name, *, lock=False):
        from overseas_costing.services.material_packing_group_service import FrappeMaterialPackingGroupRepository
        repo = FrappeMaterialPackingGroupRepository()
        state = repo.load(batch_name, version_name, lock=lock)
        if lock:
            self.frappe.db.sql(
                'SELECT name FROM `tabOverseas Cost Item` WHERE batch=%s AND version=%s FOR UPDATE',
                (state['batch'], state['version']),
            )
            state = repo.load(state['batch'], state['version'], lock=False)
        return state

    def save_exclusions(self, state, items, stable_line_keys, reason, actor):
        frappe = self.frappe
        selected = set(stable_line_keys)
        affected = []
        try:
            from overseas_costing.services.calculate_service import _insert_audit_log
            from overseas_costing.services.material_packing_group_service import mark_members_changed
            excluded_at = datetime.now().isoformat(timespec='seconds')
            for row in items:
                doc = frappe.get_doc('Overseas Cost Item', row['name'])
                key = str(getattr(doc, 'stable_line_key', '') or '').strip()
                if (key not in selected or doc.batch != state['batch'] or doc.version != state['version']
                        or int(getattr(doc, 'is_excluded', 0) or 0)):
                    raise ValueError(_('所选物料已变化，本次未删除任何物料。'))
                old_value = json.dumps({
                    'name':doc.name, 'stable_line_key':key, 'row_no':getattr(doc, 'row_no', None),
                    'material_code':getattr(doc, 'material_code', ''),
                    'product_name':getattr(doc, 'product_name', ''),
                    'is_excluded':0,
                }, ensure_ascii=False, default=str)
                doc.is_excluded = 1
                doc.excluded_at = excluded_at
                doc.excluded_by = actor
                doc.exclusion_reason = reason
                doc.save(ignore_permissions=True)
                _insert_audit_log(
                    batch_doc_name=state['batch'], version_name=state['version'],
                    action_type='BATCH_EDIT', field_name='item', row_no=getattr(doc, 'row_no', None),
                    old_value=old_value,
                    new_value=json.dumps({
                        'is_excluded':1, 'excluded_at':excluded_at,
                        'excluded_by':actor, 'exclusion_reason':reason,
                    }, ensure_ascii=False, default=str),
                    action_remark=reason,
                )
            affected = mark_members_changed(frappe, state['version'], stable_line_keys, 'excluded')
            frappe.db.set_value('Overseas Cost Batch', state['batch'], {
                'status':'Dirty', 'confirm_status':'Pending', 'writeback_status':'Not Started',
            }, update_modified=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            raise
        return {
            'batch_modified':str(frappe.db.get_value(
                'Overseas Cost Batch', state['batch'], 'modified') or ''),
            'affected_group_ids':affected,
        }
