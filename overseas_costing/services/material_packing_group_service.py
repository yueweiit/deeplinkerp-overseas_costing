"""Server-owned material packing groups and count-once calculation projection."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from datetime import datetime

from .logistics_settlement.model import digest


GROUP_FIELDS = ('package_count', 'net_weight_kg', 'gross_weight_kg', 'volume_m3')
PACKING_REPOSITORY_ITEM_FIELDS = (
    'name', 'stable_line_key', 'row_no', 'is_excluded', 'net_weight_kg',
    'gross_weight_kg', 'volume_m3', 'actual_shipped_qty', 'quantity',
    'goods_value', 'extra_json',
)
MONEY_FIELDS = ('net_weight_kg', 'gross_weight_kg', 'volume_m3')
META_KEY = 'material_packing_groups'


def _decimal(value, *, allow_zero=True):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        return None
    return result


def _text(value):
    if isinstance(value, Decimal):
        rendered = format(value, 'f')
        return rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered
    return str(value or '').strip()


def groups_from_version(version):
    raw = version.get('extra_json') if isinstance(version, dict) else None
    if isinstance(raw, dict):
        meta = raw
    else:
        try:
            meta = json.loads(raw or '{}')
        except (TypeError, ValueError):
            meta = {}
    groups = meta.get(META_KEY) or []
    return deepcopy(groups) if isinstance(groups, list) else []


def version_metadata_with_groups(version, groups):
    raw = (version or {}).get('extra_json')
    if isinstance(raw, dict):
        meta = deepcopy(raw)
    else:
        try:
            meta = json.loads(raw or '{}')
        except (TypeError, ValueError):
            meta = {}
    meta[META_KEY] = deepcopy(groups)
    return meta


def _ordered_rows(items):
    active = [deepcopy(row) for row in items if not int(row.get('is_excluded') or 0)]
    return sorted(active, key=lambda row: (int(row.get('row_no') or 0), str(row.get('name') or '')))


def _key(row):
    return str(row.get('stable_line_key') or '').strip()


def _assert_members(items, groups, member_keys, *, ignore_group_id=''):
    ordered = _ordered_rows(items)
    keys = [_key(row) for row in ordered]
    members = [str(value or '').strip() for value in member_keys or []]
    if len(members) < 2 or len(members) != len(set(members)) or any(not value for value in members):
        raise ValueError(_('请至少选择两行不重复的物料。'))
    if any(value not in keys or keys.count(value) != 1 for value in members):
        raise ValueError(_('装箱组物料已变化，请刷新后重新选择。'))
    positions = sorted(keys.index(value) for value in members)
    if positions != list(range(min(positions), max(positions) + 1)):
        raise ValueError(_('只能合并当前表中连续的物料行。'))
    occupied = {key for group in groups or [] if group.get('group_id') != ignore_group_id
                and group.get('status') != 'removed' for key in group.get('member_keys') or []}
    overlap = occupied.intersection(members)
    if overlap:
        raise ValueError(_('所选物料已属于其他装箱组，请先解除原分组。'))
    return [ordered[position] for position in positions]


def build_group_preview(items, groups, member_keys, *, action, version, group_id='', values=None,
                        source_fingerprint='', reason='', creation_method='manual'):
    if action not in {'create', 'update', 'remove'}:
        raise ValueError(_('装箱组操作不合法。'))
    current = next((deepcopy(group) for group in groups or [] if group.get('group_id') == group_id), None)
    if action in {'update', 'remove'} and not current:
        raise ValueError(_('装箱组不存在或已变化。'))
    if action in {'update', 'remove'} and not str(reason or '').strip():
        raise ValueError(_('修改或解除装箱组时请填写原因。'))
    members = list((current or {}).get('member_keys') or member_keys or []) if action == 'remove' else list(member_keys or [])
    if action == 'remove':
        by_key = {_key(row):deepcopy(row) for row in items}
        member_rows = [by_key[key] for key in members if key in by_key]
    else:
        member_rows = _assert_members(items, groups, members, ignore_group_id=group_id)
        members = [_key(row) for row in member_rows]
    supplied = values or {}
    totals = {}
    for field in GROUP_FIELDS:
        raw = supplied.get(field)
        if action == 'remove' and raw in (None, ''):
            raw = (current or {}).get(field)
        if raw in (None, ''):
            raw = sum((_decimal(row.get(field)) or Decimal('0') for row in member_rows), Decimal('0'))
        parsed = _decimal(raw)
        if parsed is None:
            raise ValueError(_("装箱组 {0} 必须是非负数。").format(field))
        totals[field] = _text(parsed)
    types = list(dict.fromkeys(str(row.get('packaging_type') or '').strip() for row in member_rows
                               if str(row.get('packaging_type') or '').strip()))
    packing_type = str(supplied.get('packaging_type') or (types[0] if len(types) == 1 else '混装' if types else '')).strip()
    stable_id = group_id or digest('material-packing-group-1', version, members)
    preserved_source = str(source_fingerprint or (current or {}).get('source_fingerprint') or '')
    preserved_creation = str((current or {}).get('creation_method') or creation_method or 'manual')
    group = {**(current or {}), 'group_id':stable_id, 'member_keys':members,
             **totals, 'packaging_type':packing_type, 'source_fingerprint':preserved_source,
             'creation_method':preserved_creation, 'modification_reason':str(reason or ''),
             'status':'removed' if action == 'remove' else 'pending_confirmation'}
    revision = digest('material-packing-group-preview-1', version, action, groups, group)
    return {'ok':True, 'action':action, 'version':version, 'group':group,
            'before_group':current, 'member_rows':member_rows, 'revision':revision,
            'preview_id':digest('material-packing-group-preview-id', revision), 'can_confirm':True}


def allocate_group_values(member_rows, group):
    rows = _ordered_rows(member_rows)
    bases = []
    for field in ('shipment_value_rmb', 'actual_shipped_qty'):
        if field == 'shipment_value_rmb':
            from .shipment_cost_service import shipment_value
            values = [_decimal(row.get(field) if row.get(field) is not None
                               else shipment_value(row).get('amount_rmb'), allow_zero=False) or Decimal('0')
                      for row in rows]
        else:
            values = [_decimal(row.get(field), allow_zero=False) or Decimal('0') for row in rows]
        total = sum(values, Decimal('0'))
        if total > 0 and all(value > 0 for value in values):
            bases, basis, basis_total = values, field, total
            break
    else:
        raise ValueError(_('装箱组缺少有效货值和发货数量，无法分摊。'))
    result = [deepcopy(row) for row in rows]
    for field in MONEY_FIELDS:
        if field not in group:
            continue
        total = _decimal(group.get(field))
        if total is None:
            raise ValueError(_("装箱组 {0} 无效。").format(field))
        assigned = Decimal('0')
        precision = Decimal('0.000001')
        for index, base in enumerate(bases):
            value = total - assigned if index == len(result) - 1 else (total * base / basis_total).quantize(precision, rounding=ROUND_HALF_UP)
            assigned += value
            result[index][field] = _text(value)
    return {'basis':basis, 'rows':result}


def project_packing_groups(items, groups, *, strict=True):
    projected = _ordered_rows(items)
    by_key = {_key(row): row for row in projected}
    public_groups = []
    seen = set()
    for raw_group in groups or []:
        group = deepcopy(raw_group)
        if group.get('status') in {'removed'}:
            continue
        if group.get('status') == 'needs_reconfirmation':
            if strict:
                raise ValueError(_('装箱组成员已变化，请重新确认分组。'))
            public_groups.append({**group, 'blocking':True})
            continue
        members = [by_key.get(str(key)) for key in group.get('member_keys') or []]
        if any(row is None for row in members) or seen.intersection(group.get('member_keys') or []):
            if strict:
                raise ValueError(_('装箱组成员不完整或重复，请重新确认。'))
            public_groups.append({**group, 'blocking':True})
            continue
        try:
            allocation = allocate_group_values(members, group)
        except ValueError as error:
            if strict:
                raise
            public_groups.append({**group, 'blocking':True, 'blocking_reason':str(error)})
            continue
        for position, allocated in enumerate(allocation['rows']):
            target = by_key[_key(allocated)]
            for field in MONEY_FIELDS:
                target[field] = allocated[field]
            # A group-level carton count is counted once. Weight and volume are
            # allocated for downstream per-row cost splits, while the UI/export
            # still renders the authoritative group totals once via rowspan.
            if group.get('package_count') not in (None, ''):
                target['package_count'] = _text(_decimal(group.get('package_count')) or Decimal('0')) if position == 0 else '0'
            target['packaging_type'] = str(group.get('packaging_type') or '')
            target['packing_group_id'] = group['group_id']
            target['packing_group_position'] = position
            target['packing_group_size'] = len(members)
            target['packing_group'] = {**group, 'allocation_basis':allocation['basis'],
                                       'rowspan':len(members) if position == 0 else 0}
        seen.update(group.get('member_keys') or [])
        public_groups.append({**group, 'allocation_basis':allocation['basis']})
    return {'items':projected, 'groups':public_groups}


def _state_fingerprint(state):
    rows = [{key:row.get(key) for key in ('name','stable_line_key','row_no','is_excluded',*GROUP_FIELDS,
                                          'packaging_type','actual_shipped_qty','goods_value','extra_json')}
            for row in state.get('items') or []]
    return digest('material-packing-group-state-1', state.get('batch'), state.get('batch_modified'),
                  state.get('version'), state.get('version_modified'), state.get('groups') or [], rows)


def prepare_group_preview(batch_name, version_name, member_keys, action, *, group_id='', values=None,
                          reason='', source_fingerprint='', creation_method='manual', repository=None):
    repo = repository or FrappeMaterialPackingGroupRepository()
    state = repo.load(batch_name, version_name, lock=False)
    if not state.get('editable'):
        raise ValueError(_('历史、已确认或已回写版本不能修改装箱组。'))
    preview = build_group_preview(state['items'], state['groups'], member_keys, action=action,
                                  version=state['version'], group_id=group_id, values=values,
                                  source_fingerprint=source_fingerprint, reason=reason,
                                  creation_method=creation_method)
    preview.update(batch=state['batch'], state_fingerprint=_state_fingerprint(state),
                   batch_modified=state['batch_modified'], version_modified=state['version_modified'])
    preview['revision'] = digest(preview['revision'], preview['state_fingerprint'])
    preview['preview_id'] = digest('material-packing-group-server-preview-1', preview['revision'])
    repo.save_preview(deepcopy(preview))
    return deepcopy(preview)


def confirm_group_preview(batch_name, preview_id, revision, edit_token, expected_modified, *, repository=None,
                          actor=''):
    repo = repository or FrappeMaterialPackingGroupRepository()
    preview = repo.get_preview(preview_id)
    if not preview or preview.get('batch') != batch_name or preview.get('revision') != revision:
        raise ValueError(_('装箱组预览已失效，请重新预览。'))
    repo.assert_write(batch_name, edit_token, expected_modified)
    state = repo.load(batch_name, preview['version'], lock=True)
    if not state.get('editable') or _state_fingerprint(state) != preview.get('state_fingerprint'):
        raise ValueError(_('物料、装箱组或版本已变化，请重新预览。'))
    group = deepcopy(preview['group'])
    group.update(status='removed' if preview['action'] == 'remove' else 'confirmed',
                 confirmed_by=str(actor or ''), confirmed_at=datetime.now().isoformat(timespec='seconds'),
                 last_preview_id=preview_id)
    groups = [deepcopy(value) for value in state.get('groups') or []
              if value.get('group_id') != group['group_id']]
    groups.append(group)
    modified = repo.save_groups(state, groups, preview, actor)
    return {'ok':True, 'status':'CONFIRMED', 'action':preview['action'], 'group':group,
            'packing_groups':[value for value in groups if value.get('status') != 'removed'],
            'version_name':state['version'], 'batch_modified':modified}


def prepare_group_batch_preview(batch_name, version_name, group_ids, *, reason='', repository=None):
    """Build one server-held preview for atomically removing complete packing groups."""
    repo = repository or FrappeMaterialPackingGroupRepository()
    requested = [str(value or '').strip() for value in group_ids or []]
    if not requested or any(not value for value in requested):
        raise ValueError(_('请选择至少一个装箱组。'))
    if len(requested) != len(set(requested)):
        raise ValueError(_('装箱组选择存在重复。'))
    if len(requested) > 100:
        raise ValueError(_('一次最多解除 100 个装箱组。'))
    if not str(reason or '').strip():
        raise ValueError(_('解除装箱组时请填写原因。'))
    state = repo.load(batch_name, version_name, lock=False)
    if not state.get('editable'):
        raise ValueError(_('历史、已确认或已回写版本不能修改装箱组。'))
    active = {str(group.get('group_id') or ''):deepcopy(group)
              for group in state.get('groups') or [] if group.get('status') != 'removed'}
    if any(group_id not in active for group_id in requested):
        raise ValueError(_('装箱组已变化，请刷新后重新选择。'))
    all_keys = [_key(row) for row in state.get('items') or [] if _key(row)]
    key_counts = Counter(all_keys)
    available_keys = {key for key, count in key_counts.items() if count == 1}
    selected_groups = [active[group_id] for group_id in requested]
    members = []
    for group in selected_groups:
        group_members = [str(key or '').strip() for key in group.get('member_keys') or []]
        if len(group_members) < 2 or any(not key or key not in available_keys for key in group_members):
            raise ValueError(_('装箱组成员已变化，请刷新后重新选择。'))
        members.extend(group_members)
    if len(members) != len(set(members)):
        raise ValueError(_('装箱组成员重复，请先修复分组。'))
    fingerprint = _state_fingerprint(state)
    preview = {
        'ok':True, 'action':'batch_remove', 'batch':state['batch'], 'version':state['version'],
        'group_ids':requested, 'before_groups':selected_groups, 'affected_member_keys':members,
        'reason':str(reason or '').strip(), 'state_fingerprint':fingerprint,
        'batch_modified':state['batch_modified'], 'version_modified':state['version_modified'],
    }
    preview['revision'] = digest('material-packing-group-batch-preview-1', preview)
    preview['preview_id'] = digest('material-packing-group-batch-preview-id', preview['revision'])
    repo.save_preview(deepcopy(preview))
    return deepcopy(preview)


def confirm_group_batch_preview(batch_name, preview_id, revision, edit_token, expected_modified, *, repository=None,
                                actor=''):
    """Remove all previewed groups in one save or reject the whole request."""
    repo = repository or FrappeMaterialPackingGroupRepository()
    preview = repo.get_preview(preview_id)
    if (not preview or preview.get('action') != 'batch_remove' or preview.get('batch') != batch_name
            or preview.get('revision') != revision):
        raise ValueError(_('装箱组预览已失效，请重新预览。'))
    repo.assert_write(batch_name, edit_token, expected_modified)
    state = repo.load(batch_name, preview['version'], lock=True)
    if not state.get('editable') or _state_fingerprint(state) != preview.get('state_fingerprint'):
        raise ValueError(_('物料、装箱组或版本已变化，请重新预览。'))
    requested = set(preview.get('group_ids') or [])
    now = datetime.now().isoformat(timespec='seconds')
    removed = []
    groups = []
    for raw_group in state.get('groups') or []:
        group = deepcopy(raw_group)
        if group.get('group_id') in requested:
            group.update(status='removed', modification_reason=preview.get('reason') or '',
                         confirmed_by=str(actor or ''), confirmed_at=now, last_preview_id=preview_id)
            removed.append(group.get('group_id'))
        groups.append(group)
    if set(removed) != requested:
        raise ValueError(_('装箱组已变化，请重新预览。'))
    modified = repo.save_groups(state, groups, preview, actor)
    return {
        'ok':True, 'status':'CONFIRMED', 'action':'batch_remove',
        'removed_group_ids':[group_id for group_id in preview.get('group_ids') or []],
        'affected_member_keys':list(preview.get('affected_member_keys') or []),
        'packing_groups':[group for group in groups if group.get('status') != 'removed'],
        'version_name':state['version'], 'batch_modified':modified,
    }


class FrappeMaterialPackingGroupRepository:
    CACHE_PREFIX = 'overseas-costing:material-packing-group:'

    def __init__(self):
        import frappe
        self.frappe = frappe

    def load(self, batch_name, version_name, *, lock=False):
        from overseas_costing.services import batch_service
        frappe = self.frappe
        batch = batch_service._resolve_batch_name(str(batch_name or ''))
        if not batch:
            raise ValueError(_('未找到当前批次。'))
        current = frappe.db.get_value('Overseas Cost Batch', batch,
            ['name','current_version','modified','confirm_status','writeback_status','is_locked'], as_dict=True) or {}
        version = batch_service._resolve_version_name(batch, version_name)
        if lock:
            frappe.db.sql('SELECT name FROM `tabOverseas Cost Version` WHERE name=%s AND batch=%s FOR UPDATE',
                          (version, batch))
        version_row = frappe.db.get_value('Overseas Cost Version', version,
            ['name','batch','modified','status','extra_json'], as_dict=True) or {}
        if version_row.get('batch') != batch:
            raise ValueError(_('成本版本不属于当前批次。'))
        items = frappe.get_all('Overseas Cost Item', filters={'batch':batch,'version':version},
                               fields=list(PACKING_REPOSITORY_ITEM_FIELDS),
                               order_by='row_no asc, name asc', limit_page_length=10000)
        from overseas_costing.services.effective_source_values import project_batch_items
        items, _source_context = project_batch_items(items, batch, version)
        editable = (current.get('current_version') == version and version_row.get('status') == 'Active'
                    and current.get('confirm_status') != 'Confirmed'
                    and current.get('writeback_status') != 'Success' and not current.get('is_locked'))
        return {'batch':batch,'batch_modified':str(current.get('modified') or ''),'version':version,
                'version_modified':str(version_row.get('modified') or ''),'version_status':version_row.get('status'),
                'editable':editable,'groups':groups_from_version(version_row),'items':items,
                'version_row':version_row}

    def save_preview(self, preview):
        self.frappe.cache().set_value(self.CACHE_PREFIX + preview['preview_id'], preview, expires_in_sec=1800)

    def get_preview(self, preview_id):
        return self.frappe.cache().get_value(self.CACHE_PREFIX + str(preview_id or ''))

    def assert_write(self, batch, edit_token, expected_modified):
        from overseas_costing.services import edit_session_service
        edit_session_service.assert_batch_write(batch, edit_token=edit_token,
                                                expected_modified=expected_modified)

    def save_groups(self, state, groups, preview, actor):
        frappe = self.frappe
        meta = version_metadata_with_groups(state.get('version_row') or {}, groups)
        frappe.db.set_value('Overseas Cost Version', state['version'], {
            'extra_json':json.dumps(meta, ensure_ascii=False, default=str),
            'calculated_at':None, 'summary_snapshot_json':'{}', 'rule_snapshot_json':'[]'}, update_modified=True)
        frappe.db.set_value('Overseas Cost Batch', state['batch'], {
            'status':'Dirty','confirm_status':'Pending','writeback_status':'Not Started'}, update_modified=True)
        from overseas_costing.services.calculate_service import _insert_audit_log
        _insert_audit_log(batch_doc_name=state['batch'], version_name=state['version'],
            action_type='BATCH_EDIT', field_name='material_packing_groups', row_no=None,
            old_value=json.dumps(state.get('groups') or [], ensure_ascii=False, default=str),
            new_value=json.dumps(groups, ensure_ascii=False, default=str),
            action_remark=f'装箱组{preview["action"]}：{preview["preview_id"]}')
        frappe.db.commit()
        return str(frappe.db.get_value('Overseas Cost Batch', state['batch'], 'modified') or '')


def mark_members_changed(frappe, version_name, stable_line_keys, action):
    """Invalidate saved groups touched by excluded or restored members in one metadata write."""
    keys = {str(value or '').strip() for value in stable_line_keys or [] if str(value or '').strip()}
    if not keys:
        return []
    version = frappe.db.get_value('Overseas Cost Version', version_name,
                                  ['name','extra_json'], as_dict=True) or {}
    groups = groups_from_version(version)
    affected = []
    for group in groups:
        if (group.get('status') != 'removed'
                and keys.intersection(str(key) for key in group.get('member_keys') or [])):
            group['status'] = 'needs_reconfirmation'
            group['member_change'] = str(action or '')
            affected.append(str(group.get('group_id') or ''))
    if affected:
        metadata = version_metadata_with_groups(version, groups)
        frappe.db.set_value('Overseas Cost Version', version_name, 'extra_json',
                            json.dumps(metadata, ensure_ascii=False, default=str), update_modified=True)
    return affected


def mark_member_changed(frappe, version_name, stable_line_key, action):
    """Backward-compatible single-member invalidation helper."""
    return bool(mark_members_changed(frappe, version_name, [stable_line_key], action))


def adopt_xlsx_group_candidates(items, existing_groups, candidates, preview_id, *, actor='', confirmed_member_keys=None):
    """Adopt server-proven Excel merges or uniquely matched comment groups."""
    groups = [deepcopy(group) for group in existing_groups or []]
    # A removed group is an explicit user decision and acts as a tombstone for
    # automatic source re-adoption. Users can still create a new manual group.
    occupied = {str(key) for group in groups for key in group.get('member_keys') or []}
    confirmed = ({str(key) for key in confirmed_member_keys}
                 if confirmed_member_keys is not None else None)
    for candidate in candidates or []:
        members = [str(key) for key in candidate.get('member_keys') or []]
        evidence = candidate.get('evidence') or []
        xlsx_merge = any(str(row.get('kind') or '') == 'xlsx_merge' for row in evidence)
        trusted_comment = (
            candidate.get('creation_method') == 'trusted_comment_text'
            and candidate.get('can_apply') and candidate.get('default_selected')
            and any(str(row.get('kind') or '') == 'trusted_comment_text' for row in evidence)
        )
        if (len(members) < 2 or occupied.intersection(members)
                or (confirmed is not None and not set(members).issubset(confirmed))
                or not (xlsx_merge or trusted_comment)):
            continue
        values = {field:candidate.get(field) for field in (*GROUP_FIELDS, 'packaging_type')}
        try:
            prepared = build_group_preview(items, groups, members, action='create',
                version='', values=values, source_fingerprint=candidate.get('source_fingerprint') or '',
                creation_method='trusted_comment_text' if trusted_comment else 'xlsx_merge',
                reason='采用评论中唯一匹配的整票装箱组' if trusted_comment else '采用 Excel 真实合并范围')
        except ValueError:
            continue
        group = prepared['group']
        if trusted_comment and candidate.get('package_count') in (None, ''):
            group['package_count'] = None
        group.update(group_id=str(candidate.get('candidate_id') or group['group_id']), status='confirmed',
                     confirmed_by=str(actor or 'source-confirmation'), confirmed_at=datetime.now().isoformat(timespec='seconds'),
                     last_preview_id=preview_id, source_id=candidate.get('source_id'),
                     sheet_name=candidate.get('sheet_name'), evidence=deepcopy(evidence))
        groups.append(group)
        occupied.update(members)
    return groups
