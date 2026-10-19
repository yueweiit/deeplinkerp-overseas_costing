"""Prepare and confirm immutable, server-held AI row selections."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from copy import deepcopy
import re

from . import material_ai_row_selection as rows, material_ai_fee_policy as fees
from .logistics_settlement.model import digest


RECEIPT_POLICY = 'ai-field-preview-receipt-7'

_SKIPPED_PROGRESS_STATUSES = frozenset({
    'FAILED', 'SKIPPED', 'UNREADABLE', 'TIMEOUT', 'FORBIDDEN', 'MISSING',
    'UNSUPPORTED', 'CORRUPT',
})
_SAFE_SKIP_REASON_TEXT = {
    'FILE_NOT_FOUND': '资料文件不存在。',
    'SOURCE_PERMISSION_DENIED': '资料文件无读取权限。',
    'SOURCE_URL_EXPIRED': '资料链接已失效。',
    'UNSUPPORTED_FORMAT': '资料格式暂不支持。',
    'CORRUPT_DOCUMENT': '资料文件已损坏。',
    'OCR_FAILED': '资料图像无法识别。',
    'NO_RECOGNIZABLE_CONTENT': '资料中未发现可识别内容。',
    'DOWNLOAD_FAILED': '资料文件下载或归档失败。',
    'PARSE_FAILED': '资料文件无法解析。',
    'DOWNLOAD_TIMEOUT': '资料文件获取超时。',
    'PARSE_TIMEOUT': '资料文件解析超时。',
    'DUPLICATE_EVIDENCE': '该资料本次已读取。',
}
_PUBLIC_PROGRESS_STATUSES = frozenset({
    'WAITING', 'DOWNLOADING', 'READING', 'READ', 'PARSED', 'ANALYZING',
    'COMPLETED', 'PARTIAL', 'FAILED', 'SKIPPED', 'UNREADABLE', 'EXCLUDED',
    'NO_RESULT', 'NEEDS_SELECTION',
})
_PUBLIC_READ_STATUSES = frozenset({
    'READ', 'PARTIAL', 'FAILED', 'SKIPPED', 'UNREADABLE', 'NO_RESULT',
    'EXCLUDED', 'NEEDS_SELECTION',
})
_PUBLIC_PROGRESS_DETAIL = {
    'WAITING':'等待读取','DOWNLOADING':'正在获取资料','READING':'正在读取资料',
    'READ':'资料已读取','PARSED':'资料已解析','ANALYZING':'正在分析资料',
    'COMPLETED':'读取完成','PARTIAL':'部分资料已读取','EXCLUDED':'已排除',
    'NO_RESULT':'未产生候选','NEEDS_SELECTION':'多个工作表待选择',
}
_PUBLIC_PROGRESS_STRING_FIELDS = {
    'source_id':500,'evidence_id':500,'parent_source_id':500,
    'source_kind':60,'evidence_kind':60,'label':500,'approval_no':200,
    'actor_name':200,'occurred_at':100,'sheet_name':200,'sheet':200,
    'priority_reason':500,'workflow_stage':60,
    'actual_packing_match_status':40,'actual_packing_match_id':500,
    'actual_packing_match_revision':500,'source_field':500,
    'workflow_field_id':500,'analysis_reason':1000,'adoption_restriction':1000,
}
_PUBLIC_PROGRESS_SECRET_PATTERN = re.compile(
    r'(?:\bbearer\s+\S+|https?://\S*(?:[?&](?:access_?token|token|signature|sig|credential|auth)=)\S*)',
    re.IGNORECASE,
)


def _bounded_nonnegative_int(value, maximum):
    try:
        number=int(value or 0)
    except (TypeError,ValueError,OverflowError):
        return 0
    return min(max(number,0),maximum)


def _safe_skip_metadata(status_row):
    """Rebuild public skip metadata from server-owned progress only."""

    from . import material_ai_fill_service as ai

    statuses={
        str(status_row.get(key) or '').strip().upper()
        for key in ('read_status','status')
    }
    if not (statuses & _SKIPPED_PROGRESS_STATUSES):
        return {}
    raw_code = str(status_row.get('skip_reason_code') or '').strip().upper()
    code = raw_code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', raw_code) else ''
    safe_reason = _SAFE_SKIP_REASON_TEXT.get(code, '')
    # Always pass the server-owned reason through the public text boundary.
    # Unknown/legacy reasons deliberately fall back to the generic UI copy;
    # arbitrary document text must never become an error explanation.
    safe_reason = ai._safe_public_text(safe_reason) if safe_reason else ''
    elapsed_ms=_bounded_nonnegative_int(status_row.get('elapsed_ms'),3_600_000)
    return {
        'skip_reason_code': code if safe_reason else '',
        'skip_reason_text': safe_reason,
        'elapsed_ms': elapsed_ms,
    }


def _public_progress_text(value, limit, fallback=''):
    from . import material_ai_fill_service as ai

    text=str(value or '').strip()[:limit]
    if not text or _PUBLIC_PROGRESS_SECRET_PATTERN.search(text):
        return fallback
    safe=ai._safe_public_text(text)
    return fallback if safe==ai.SERVER_PREVIEW_FAILURE_MESSAGE else safe


def _public_source_progress(progress):
    """Project persisted worker progress into a polling-safe public schema."""

    rows=[]
    for source in progress or []:
        if not isinstance(source,dict):
            continue
        status=str(source.get('status') or 'WAITING').strip().upper()
        if status not in _PUBLIC_PROGRESS_STATUSES:
            status='WAITING'
        read_status=str(source.get('read_status') or '').strip().upper()
        if read_status not in _PUBLIC_READ_STATUSES:
            read_status=(status if status in _PUBLIC_READ_STATUSES else 'NO_RESULT')
        row={
            key:_public_progress_text(source.get(key),limit)
            for key,limit in _PUBLIC_PROGRESS_STRING_FIELDS.items()
            if source.get(key) not in (None,'')
        }
        if not row.get('evidence_id') and row.get('source_id'):
            row['evidence_id']=row['source_id']
        row.update({
            'status':status,'read_status':read_status,
            'parse_method':(
                str(source.get('parse_method') or 'NONE').strip().upper()
                if str(source.get('parse_method') or 'NONE').strip().upper()
                in {'SYSTEM_APPROVAL','SYSTEM_EXCEL','AI_TEXT','AI_VISION','NONE'}
                else 'NONE'
            ),
            'field_count':_bounded_nonnegative_int(source.get('field_count'),1_000_000),
            'page_count':_bounded_nonnegative_int(source.get('page_count'),1_000_000),
            'candidate_count':_bounded_nonnegative_int(source.get('candidate_count'),1_000_000),
            'result_count':_bounded_nonnegative_int(source.get('result_count'),1_000_000),
            'error':'',
        })
        for key in (
            'selected','locked','selectable','analysis_allowed','adoption_allowed',
            'final_fee_allowed','dedicated_packing_attachment',
        ):
            if key in source:
                row[key]=bool(source.get(key))
        for key,maximum in (
            ('priority',1_000_000),('workflow_rank',100),('evidence_rank',100),
        ):
            if key in source:
                row[key]=_bounded_nonnegative_int(source.get(key),maximum)
        sheet_options=[]
        for option in source.get('sheet_options') or []:
            if not isinstance(option,dict):
                continue
            sheet_options.append({
                'source_id':_public_progress_text(option.get('source_id'),500),
                'sheet_name':_public_progress_text(option.get('sheet_name'),200),
            })
        if sheet_options:
            row['sheet_options']=sheet_options
        skip_metadata=_safe_skip_metadata(source)
        if skip_metadata:
            row.update(skip_metadata)
            reason=skip_metadata.get('skip_reason_text') or '资料无法读取。'
            row['detail']=f'{reason}已跳过，继续读取下一资料。'
        else:
            row['detail']=_PUBLIC_PROGRESS_DETAIL.get(status,'等待读取')
        rows.append(row)
    return rows


def _sources_with_progress(sources, progress):
    """Reattach browser-safe run outcomes to freshly locked source metadata."""

    by_id={}
    for row in progress or []:
        if not isinstance(row,dict):
            continue
        for key in ('source_id','parent_source_id'):
            source_id=str(row.get(key) or '')
            if source_id:
                by_id.setdefault(source_id,row)
    result=[]
    for source in sources or []:
        current=deepcopy(source)
        for transient in (
            'skip_reason_code', 'skip_reason_text', 'elapsed_ms', 'error',
            'result_count',
        ):
            current.pop(transient,None)
        status_row=next((by_id.get(str(source.get(key) or '')) for key in (
            'source_id','logical_source_id','parent_source_id','resolver_source_id',
        ) if by_id.get(str(source.get(key) or ''))),None)
        if status_row:
            current['read_status']=str(status_row.get('read_status') or status_row.get('status') or 'NO_RESULT')
            skip_metadata=_safe_skip_metadata(status_row)
            current['error']=str(skip_metadata.get('skip_reason_text') or '')
            current['result_count']=_bounded_nonnegative_int(
                status_row.get('result_count') or status_row.get('candidate_count'),
                1_000_000,
            )
            if status_row.get('evidence_kind'):
                current['evidence_kind']=str(status_row['evidence_kind'])[:60]
            current.update(skip_metadata)
        result.append(current)
    return result


def material_fingerprint(items,sources,context):
    """Fee-only amendments do not consume reusable material recognition results."""
    from . import material_ai_fill_service as ai
    from .effective_logistics_source import json_dict
    def packing(ctx):
        ctx=ctx or {};part=deepcopy(ctx.get('packing') or ctx)
        for key in ('fingerprint','freight','cost_version'):part.pop(key,None)
        return part
    clean_items=[]
    for item in items:
        row=ai._fingerprint_item(deepcopy(item));meta=json_dict(row.get('extra_json'))
        meta.pop('effective_logistics_source',None)
        if meta.get('settlement_physical'):meta['settlement_physical'].pop('source_context_fingerprint',None)
        row['extra_json']=meta;clean_items.append(row)
    clean_sources=[]
    for source in sources:
        if not bool(source.get('selected',True)):
            continue
        row=ai._fingerprint_source(source);row['source_context']=packing(row.get('source_context'));clean_sources.append(row)
    return digest('ai-material-input-1',clean_items,clean_sources,packing(context.get('effective_source')),
        context.get('fx_rates'),context.get('clarification_revision'),
        _normalized_project_routing(context.get('project_routing')))


def _normalized_project_routing(value):
    """Keep only server-authoritative project policy fields in a stable order."""

    routing = value if isinstance(value, dict) else {}
    options = []
    for option in routing.get('options') or []:
        if not isinstance(option, dict):
            continue
        try:
            revision = int(option.get('revision') or 0)
        except (TypeError, ValueError):
            revision = 0
        options.append({
            'project_collection': str(option.get('project_collection') or ''),
            'subsidiary_code': str(option.get('subsidiary_code') or ''),
            'site_code': str(option.get('site_code') or ''),
            'revision': revision,
            'ai_match_hint': str(option.get('ai_match_hint') or ''),
            'is_approval_candidate': option.get('is_approval_candidate') in (1, True, '1', 'true', 'True'),
        })
    options.sort(key=lambda option: (
        option['project_collection'], option['subsidiary_code'], option['site_code'],
        option['revision'], option['ai_match_hint'], option['is_approval_candidate'],
    ))
    return {
        'route_revision': str(routing.get('route_revision') or ''),
        'options': options,
        'conflicts': sorted(str(value or '') for value in routing.get('conflicts') or []),
    }


def _inputs(repo, batch, run, *, locked=False):
    from . import material_ai_fill_service as ai
    ai._assert_run_batch(run,batch)
    draft=ai._load_json(ai._record_value(run,'draft_json'),{})
    if draft.get('processing_version') != ai.SOURCE_REVIEW_PROCESSING_VERSION:
        raise ValueError(_('AI 资料处理规则已升级，请重新分析资料。'))
    if int(ai._record_value(run,'proposal_version',0) or 0) > 0 and draft.get('row_review_policy') != rows.POLICY:
        raise ValueError(_('AI 预览规则已升级，请重新分析资料。'))
    context=ai._review_context(repo,batch,str(ai._record_value(run,'version')),
        original_sources=ai._run_uses_original_sources(run))
    # Validate selected evidence below; one unavailable primary source must
    # not veto a valid lower-priority source in the same batch.
    if ai._clarification_changed(repo,batch,run,locked=locked):raise ValueError(_('说明已变化，请按新说明重新分析。'))
    items=repo.get_items(batch,context['version'])
    sources=ai._reload_review_manifest(repo,batch,context['version'],run)
    sources=_sources_with_progress(
        sources,ai._load_json(ai._record_value(run,'source_progress_json'),[]))
    fingerprint=ai._source_review_fingerprint(batch,context['version'],items,sources,str(ai._record_value(run,'clarification_text') or ''),context=context)
    saved_material=draft.get('material_input_fingerprint')
    if fingerprint!=ai._record_value(run,'input_fingerprint') and (not saved_material or saved_material!=material_fingerprint(items,sources,context)):
        raise ValueError(_('来源、物料或版本已变化，请重新分析资料。'))
    baseline=(draft.get('review_input') or {}).get('source_dependencies')
    if baseline is not None and callable(getattr(repo,'assert_row_dependencies',None)):
        repo.assert_row_dependencies(batch,baseline,lock=locked)
    current_fees=repo.get_fees(batch,context['version'])
    proposals=ai._load_json(ai._record_value(run,'candidates_json'),[])
    result=rows.catalog(items,proposals,current_fees,context.get('effective_source') or {},
                        run_id=ai._record_value(run,'name'),sources=sources,fx_rates=context.get('fx_rates'))
    return context,items,sources,current_fees,result


def review_catalog(repo,batch,run):
    return public_catalog(_inputs(repo,batch,run)[-1])


def _source_ref_sheet(ref):
    return str(
        ref.get('sheet_name') or ref.get('sheet') or ref.get('worksheet') or ''
    ).strip().casefold()


def _merged_amount_group_matches_change(group, change):
    """Return whether an unverified amount control governs an adopted value."""
    if change.get('fieldname') != 'shipment_value_rmb':
        return False
    item_name = str(change.get('item_name') or '').strip()
    members = {
        str(value or '').strip()
        for value in group.get('member_item_names') or []
        if str(value or '').strip()
    }
    if members and item_name not in members:
        return False

    group_source = str(group.get('source_id') or '').strip()
    group_sheet = str(group.get('sheet_name') or group.get('sheet') or '').strip().casefold()
    refs = [ref for ref in change.get('source_refs') or [] if isinstance(ref, dict)]
    if group_source or group_sheet:
        for ref in refs:
            if group_source and str(ref.get('source_id') or '').strip() != group_source:
                continue
            if group_sheet and _source_ref_sheet(ref) != group_sheet:
                continue
            return True
        # Older proposals may lack source refs. Preserve the safety fence only
        # when the group can still be tied to the adopted material explicitly.
        return not refs and bool(members and item_name in members)
    return bool(members and item_name in members)


def _blocking_merged_amount_groups(groups, changes):
    return [
        deepcopy(group)
        for group in groups or []
        if group.get('status') != 'verified'
        and any(_merged_amount_group_matches_change(group, change) for change in changes or [])
    ]


def _attach_control_metadata(projection, merged_amount_groups, packing_group_candidates):
    merged_amount_groups=deepcopy(merged_amount_groups or [])
    packing_group_candidates=deepcopy(packing_group_candidates or [])
    blocking=_blocking_merged_amount_groups(
        merged_amount_groups,projection.get('changes') or [])
    projection['merged_amount_groups']=merged_amount_groups
    projection['blocking_merged_amount_groups']=blocking
    projection['packing_group_candidates']=packing_group_candidates
    projection['merged_amount_blocking']=bool(blocking)
    if blocking:
        projection['can_apply']=False
        projection.setdefault('unresolved',[]).append({
            'code':'MERGED_AMOUNT_ALLOCATION_REQUIRED',
            'message':'合并金额组缺少独立单价或合计不一致，请完成人工分摊后重新预览。',
        })
    if not blocking and any(candidate.get('default_selected') and candidate.get('can_apply')
                            for candidate in packing_group_candidates):
        projection['can_apply']=True
    return projection


def _preview_revision(context,items,sources,current_fees,catalog,selection,dependencies):
    return digest(
        rows.POLICY,context,items,sources,current_fees,catalog['fingerprint'],
        selection['selected_row_ids'],selection['selected_fee_ids'],selection.get('selected_field_choices'),selection['mode'],dependencies,
        selection.get('merged_amount_groups') or [],selection.get('selected_packing_group_ids') or [],
        selection.get('selected_packing_assignments') or {},
        selection.get('payment_match_candidates') or selection.get('payment_match_candidate'),
    )


def _payment_match_references(sources, version):
    references = {}
    for source in sources or []:
        if not source.get('payment_match_candidate'):
            continue
        key = (
            str(source.get('payment_match_candidate_id') or ''),
            str(source.get('payment_match_candidate_revision') or ''),
            str(source.get('payment_match_version') or ''),
        )
        if not all(key) or key[2] != str(version or ''):
            continue
        references[key] = references.get(key, False) or bool(source.get('payment_match_user_selected'))
    return [
        {'candidate_id': candidate_id, 'revision': revision, 'version': candidate_version,
         'user_selected': bool(references[(candidate_id, revision, candidate_version)])}
        for candidate_id, revision, candidate_version in sorted(references)
    ]


def _payment_match_reference(sources, version):
    references = _payment_match_references(sources, version)
    if len(references) != 1:
        return None
    return {key: references[0][key] for key in ('candidate_id', 'revision', 'version')}


def _preview_receipt(preview):
    """Persist only the inputs needed to authenticate and reconstruct a preview."""
    keys=(
        'id','revision','run_id','batch','version','mode','selected_row_ids','selected_fee_ids',
        'selected_field_choices',
        'dependencies','input_fingerprint','fee_fingerprint','catalog_fingerprint',
        'selected_packing_group_ids','selected_packing_assignments','payment_match_candidate',
        'payment_match_candidates',
    )
    return {'receipt_policy':RECEIPT_POLICY,
            **{key:deepcopy(preview.get(key)) for key in keys}}


def _clean_preview_draft(draft):
    cleaned=dict(draft)
    cleaned.pop('row_previews',None)
    cleaned.pop('current_row_preview',None)
    return cleaned


def _selected_packing_groups(items, candidates, selected_ids):
    candidates=deepcopy(candidates or [])
    by_id={str(candidate.get('candidate_id') or ''):candidate for candidate in candidates}
    if any(not candidate_id for candidate_id in by_id) or len(by_id)!=len(candidates):
        raise ValueError(_('装箱组候选标识无效，请重新分析。'))
    if selected_ids is None:
        selected_ids=[candidate_id for candidate_id,candidate in by_id.items()
                      if candidate.get('default_selected') and candidate.get('can_apply')]
    if (not isinstance(selected_ids,list) or any(not isinstance(value,str) for value in selected_ids)
            or len(selected_ids)!=len(set(selected_ids))):
        raise ValueError(_('装箱组选择格式不正确。'))
    if set(selected_ids)-by_id.keys():
        raise ValueError(_('所选装箱组不属于当前草稿，请刷新预览。'))
    ordered_items=sorted(
        [item for item in items or [] if not int(item.get('is_excluded') or 0)],
        key=lambda item:(int(item.get('row_no') or 0),str(item.get('name') or '')),
    )
    # Legacy material rows predate the persisted stable_line_key column.  The
    # rest of the material import/review pipeline exposes their immutable
    # document name as ``legacy:<name>``; use the same identity here so one
    # unrelated legacy row cannot invalidate an otherwise verified XLSX group.
    stable_keys=[str(item.get('stable_line_key') or (
        f"legacy:{item.get('name')}" if item.get('name') else '')) for item in ordered_items]
    if selected_ids and (any(not key for key in stable_keys) or len(stable_keys)!=len(set(stable_keys))):
        raise ValueError(_('装箱组物料身份不稳定，请刷新后重新分析。'))
    occupied=set();selected=[]
    for candidate_id in selected_ids:
        candidate=by_id[candidate_id]
        if not candidate.get('can_apply'):
            raise ValueError(candidate.get('resolution_reason') or '该装箱组成员尚未确认，不能采用。')
        members=[str(value or '') for value in candidate.get('member_keys') or []]
        if len(members)<2 or len(members)!=len(set(members)) or any(member not in stable_keys for member in members):
            raise ValueError(_('装箱组物料已变化，请刷新后重新选择。'))
        positions=sorted(stable_keys.index(member) for member in members)
        if positions!=list(range(min(positions),max(positions)+1)) or occupied.intersection(members):
            raise ValueError(_('装箱组只能覆盖连续且互不重复的物料行。'))
        occupied.update(members);selected.append(candidate)
    return selected,sorted(selected_ids)


def _stable_item_index(items):
    index={}
    for item in sorted(
            [value for value in items or [] if not int(value.get('is_excluded') or 0)],
            key=lambda value:(int(value.get('row_no') or 0),str(value.get('name') or ''))):
        key=str(item.get('stable_line_key') or (
            f"legacy:{item.get('name')}" if item.get('name') else '')).strip()
        if not key or key in index:
            raise ValueError(_('装箱候选的物料身份不稳定，请刷新后重新分析。'))
        index[key]=item
    return index


def _packing_key_aliases(items,catalog,projection,candidates=None):
    projected_by_name={str(row.get('name') or ''):row for row in projection.get('rows') or []}
    aliases={}
    for item in items or []:
        target=projected_by_name.get(str(item.get('name') or ''))
        if target:
            old=str(item.get('stable_line_key') or '').strip()
            current=str(target.get('stable_line_key') or '').strip()
            if old and current:aliases[old]=current
    for candidate in (catalog or {}).get('rows') or []:
        target=projected_by_name.get(str(candidate.get('target_item_name') or ''))
        source_key=str((candidate.get('values') or {}).get('stable_line_key') or '').strip()
        current=str((target or {}).get('stable_line_key') or '').strip()
        if source_key and current:aliases[source_key]=current
    # Packing candidates are generated before the authoritative logistics
    # scope is projected.  A source row can therefore carry a different line
    # identity from the persisted material that survives the projection.  The
    # candidate's server-generated material label is a safe final bridge when
    # it uniquely matches a projected material code/name; never guess on an
    # ambiguous label.
    projected_by_label={}
    for row in projection.get('rows') or []:
        current=str(row.get('stable_line_key') or (
            f"legacy:{row.get('name')}" if row.get('name') else '')).strip()
        if not current:
            continue
        for value in (row.get('material_code'),row.get('product_name')):
            label=str(value or '').strip().casefold()
            if label:
                projected_by_label.setdefault(label,[]).append(current)
    for candidate in candidates or []:
        member_keys=list(candidate.get('member_keys') or [])
        member_labels=list(candidate.get('member_labels') or [])
        if len(member_keys)!=len(member_labels):
            continue
        for source_key,label_value in zip(member_keys,member_labels):
            source_key=str(source_key or '').strip()
            matches=list(dict.fromkeys(
                projected_by_label.get(str(label_value or '').strip().casefold(),[])))
            if source_key and len(matches)==1:
                aliases[source_key]=matches[0]
    return aliases


def _selected_packing_assignments(items,candidates,selected_assignments,*,key_aliases=None):
    candidates=deepcopy(candidates or [])
    by_id={str(candidate.get('candidate_id') or ''):candidate for candidate in candidates}
    if any(not candidate_id for candidate_id in by_id) or len(by_id)!=len(candidates):
        raise ValueError(_('装箱候选标识无效，请重新分析。'))
    if selected_assignments is None:
        selected_assignments={}
        for candidate_id,candidate in by_id.items():
            defaults=[option for option in candidate.get('assignment_options') or []
                      if option.get('default_selected') and option.get('can_apply')]
            if len(defaults)==1:
                selected_assignments[candidate_id]=str(defaults[0].get('assignment_id') or '')
    if (not isinstance(selected_assignments,dict)
            or any(not isinstance(key,str) or not isinstance(value,str)
                   for key,value in selected_assignments.items())):
        raise ValueError(_('装箱归属选择格式不正确。'))
    if set(selected_assignments)-by_id.keys():
        raise ValueError(_('所选装箱候选不属于当前草稿，请刷新预览。'))
    item_index=_stable_item_index(items)
    occupied=set();groups=[];singles=[];validated={}
    for candidate_id,assignment_id in sorted(selected_assignments.items()):
        candidate=by_id[candidate_id]
        options={str(option.get('assignment_id') or ''):option
                 for option in candidate.get('assignment_options') or []}
        option=options.get(assignment_id)
        if not option:
            raise ValueError(_('所选装箱归属已变化，请刷新预览。'))
        if not option.get('can_apply'):
            raise ValueError(option.get('resolution_reason') or '该装箱归属不可采用。')
        aliases=key_aliases or {}
        members=[aliases.get(str(value or ''),str(value or ''))
                 for value in option.get('member_keys') or []]
        mode=str(option.get('mode') or '')
        expected_count=1 if mode=='single_item' else 2
        if len(members)<expected_count or len(members)!=len(set(members)) or any(member not in item_index for member in members):
            raise ValueError(_('装箱候选的物料已变化，请刷新后重新选择。'))
        if occupied.intersection(members):
            raise ValueError(_('同一物料不能同时采用两组装箱事实。'))
        occupied.update(members);validated[candidate_id]=assignment_id
        selected={**candidate,'member_keys':members,'member_labels':[
            str(item_index[key].get('material_code') or item_index[key].get('product_name') or key)
            for key in members],
            'selected_assignment_id':assignment_id,'assignment_mode':mode,
            'default_selected':True,'can_apply':True}
        package_count_override=option.get('package_count_override')
        if package_count_override not in (None,''):
            if mode!='one_box_group' or str(package_count_override).strip()!='1':
                raise ValueError(_('装箱归属的箱数确认值无效，请刷新预览。'))
            selected['package_count']='1'
        if mode=='single_item':
            singles.append(selected)
        elif mode=='one_box_group':
            positions=sorted(list(item_index).index(member) for member in members)
            if positions!=list(range(min(positions),max(positions)+1)):
                raise ValueError(_('共同装箱只能覆盖连续物料行。'))
            groups.append(selected)
        else:
            raise ValueError(_('装箱归属类型无效，请重新分析。'))
    return groups,singles,validated


def _apply_single_packing_assignments(projection,singles):
    rows_by_key={str(row.get('stable_line_key') or (
        f"legacy:{row.get('name')}" if row.get('name') else '')):row
        for row in projection.get('rows') or []}
    changes=projection.setdefault('changes',[])
    for candidate in singles or []:
        key=candidate['member_keys'][0];row=rows_by_key.get(key)
        if row is None:
            raise ValueError(_('装箱归属的目标物料已变化，请刷新。'))
        for field in ('package_count','net_weight_kg','gross_weight_kg','volume_m3'):
            value=candidate.get(field)
            if value is None:
                continue
            before=row.get(field);row[field]=deepcopy(value);row['_row_action']='source'
            changes.append({'candidate_id':candidate.get('candidate_id'),
                'assignment_id':candidate.get('selected_assignment_id'),
                'item_name':row.get('name'),'fieldname':field,
                'previous_value':before,'value':deepcopy(value),
                'source_refs':[{'source_id':candidate.get('source_id'),
                                'sheet':candidate.get('sheet_name')}],
                'workflow_stage':'payment' if candidate.get('workflow_stage')=='payment' else candidate.get('workflow_stage')})
        meta=rows.json_dict(row.get('extra_json'))
        mask=set(meta.get('settlement_packing_missing') or [])
        for field in ('package_count','net_weight_kg','gross_weight_kg','volume_m3'):
            if candidate.get(field) is not None:
                mask.discard(field)
        meta['settlement_packing_missing']=sorted(mask);row['extra_json']=meta
    if singles:
        projection['updated_count']=len({candidate['member_keys'][0] for candidate in singles})
        projection['can_apply']=True
    return projection


def _internal_fee_ids(catalog, fee_ids):
    """Resolve public fee proposal ids back to the locked catalog ids."""

    # Preserve the established validation errors for malformed request shapes.
    if (not isinstance(fee_ids, list)
            or any(not isinstance(value, str) for value in fee_ids)
            or len(fee_ids) != len(set(fee_ids))):
        return fee_ids

    public_fees = public_catalog(catalog).get('fees') or []
    internal_fees = catalog.get('fees') or []
    if len(public_fees) != len(internal_fees):
        raise ValueError(_('当前草稿费用标识无效，请刷新预览。'))

    mapping = {}
    for public_fee, internal_fee in zip(public_fees, internal_fees):
        public_id = str(public_fee.get('proposal_id') or '')
        internal_id = str(internal_fee.get('proposal_id') or '')
        if not public_id or not internal_id:
            raise ValueError(_('当前草稿费用标识无效，请刷新预览。'))
        for accepted_id in (public_id, internal_id):
            if accepted_id in mapping and mapping[accepted_id] != internal_id:
                raise ValueError(_('当前草稿费用标识无效，请刷新预览。'))
            mapping[accepted_id] = internal_id

    if set(fee_ids) - mapping.keys():
        raise ValueError(_('所选内容不属于当前草稿，请刷新预览。'))
    return [mapping[value] for value in fee_ids]


def prepare(batch_name,run_id,row_ids,fee_ids,mode,expected_version,*,field_choices=None,
            packing_group_ids=None,packing_assignments=None,repository=None):
    from . import material_ai_fill_service as ai
    if mode == 'replace_all':
        raise ValueError(_('整表替换仅能在独立的整源采纳流程中执行。'))
    repo=repository or ai.FrappeMaterialAIFillRepository()
    initial=repo.get_run(run_id);ai._assert_run_batch(initial,batch_name)
    repo.lock_review_scope(batch_name)
    run=repo.lock_run(run_id)
    if not ai.is_material_ai_preview_ready(ai._record_value(run,'status')):
        raise ValueError(_('此分析已经处理或过期，请重新读取草稿。'))
    context,items,sources,current_fees,catalog=_inputs(repo,batch_name,run,locked=True)
    if expected_version!=context['version']:raise ValueError(_('当前成本版本已变化，请刷新。'))
    draft=ai._load_json(ai._record_value(run,'draft_json'),{})
    saved_dependencies=(draft.get('review_input') or {}).get('source_dependencies')
    if saved_dependencies is not None:
        dependencies=deepcopy(saved_dependencies)
    else:
        dependencies=repo.capture_row_dependencies(sources,context) if callable(getattr(repo,'capture_row_dependencies',None)) else []
    # Re-read under the newly held evidence locks before saving any preview.
    if dependencies:
        context,items,sources,current_fees,catalog=_inputs(repo,batch_name,run,locked=True)
    candidates=draft.get('packing_group_candidates') or []
    internal_fee_ids = _internal_fee_ids(catalog, fee_ids)
    projection=rows.project(items,catalog,row_ids,internal_fee_ids,mode,field_choices=field_choices)
    projected_items=projection.get('rows') or []
    if packing_assignments is not None or any(candidate.get('assignment_options') for candidate in candidates):
        selected_packing_groups,single_assignments,validated_assignments=(
            _selected_packing_assignments(
                projected_items,candidates,packing_assignments,
                key_aliases=_packing_key_aliases(items,catalog,projection,candidates),
            ))
        selected_packing_group_ids=[str(candidate.get('candidate_id')) for candidate in selected_packing_groups]
    else:
        selected_packing_groups,selected_packing_group_ids=_selected_packing_groups(
            projected_items,candidates,packing_group_ids)
        single_assignments=[];validated_assignments={}
    _apply_single_packing_assignments(projection,single_assignments)
    projection=_attach_control_metadata(
        projection,draft.get('merged_amount_groups') or [],selected_packing_groups)
    projection['selected_packing_group_ids']=selected_packing_group_ids
    projection['selected_packing_assignments']=validated_assignments
    projection['payment_match_candidates']=_payment_match_references(sources,context['version'])
    projection['payment_match_candidate']=_payment_match_reference(sources,context['version'])
    revision=_preview_revision(context,items,sources,current_fees,catalog,projection,dependencies)
    preview={**projection,'id':digest(run_id,revision),'revision':revision,'run_id':run_id,'batch':batch_name,
             'version':context['version'],'source_context':context.get('effective_source') or {},
             'original_source_reanalysis':ai._run_uses_original_sources(run),
             'input_fingerprint':ai._record_value(run,'input_fingerprint'),
             'fee_fingerprint':digest(current_fees),'sources':deepcopy(sources),'dependencies':dependencies}
    # A receipt authenticates the selection while the material projection is
    # reconstructed under confirmation locks. Superseded previews are useless.
    draft['row_previews']={preview['id']:_preview_receipt(preview)}
    draft['current_row_preview']=preview['id']
    repo.save_row_review_draft(run,draft)
    return {'ok':True,'preview':public_preview(preview),'row_review':public_catalog(catalog)}


def public_catalog(catalog):
    from .material_ai_fill_service import _public_ai_payload
    return _public_ai_payload(catalog)


def public_preview(preview):
    from .material_ai_fill_service import _public_ai_payload
    return _public_ai_payload({k:v for k,v in preview.items()
                               if k not in ('sources','input_fingerprint','source_context','fee_fingerprint','dependencies')})


def _reconstruct_locked_preview(repo,batch_name,run,receipt,draft):
    """Rebuild an authenticated selection from locked server state."""

    from . import material_ai_fill_service as ai
    dependencies=deepcopy(receipt.get('dependencies') or [])
    if callable(getattr(repo,'assert_row_dependencies',None)):
        repo.assert_row_dependencies(batch_name,dependencies,lock=True)
    context,items,sources,current_fees,catalog=_inputs(repo,batch_name,run,locked=True)
    candidates=draft.get('packing_group_candidates') or []
    current=rows.project(items,catalog,receipt['selected_row_ids'],receipt['selected_fee_ids'],receipt['mode'],
                         field_choices=receipt.get('selected_field_choices'))
    projected_items=current.get('rows') or []
    if any(candidate.get('assignment_options') for candidate in candidates):
        selected_packing_groups,single_assignments,validated_assignments=(
            _selected_packing_assignments(
                projected_items,candidates,receipt.get('selected_packing_assignments') or {},
                key_aliases=_packing_key_aliases(items,catalog,current,candidates),
            ))
        validated_group_ids=[str(candidate.get('candidate_id')) for candidate in selected_packing_groups]
    else:
        selected_group_ids=set(receipt.get('selected_packing_group_ids') or [])
        if not selected_group_ids and receipt.get('packing_group_candidates'):
            selected_group_ids={str(candidate.get('candidate_id') or '') for candidate in receipt.get('packing_group_candidates') or []}
        selected_packing_groups,validated_group_ids=_selected_packing_groups(
            projected_items,candidates,sorted(selected_group_ids))
        single_assignments=[];validated_assignments={}
    _apply_single_packing_assignments(current,single_assignments)
    current=_attach_control_metadata(
        current,draft.get('merged_amount_groups') or receipt.get('merged_amount_groups') or [],selected_packing_groups)
    current['selected_packing_group_ids']=validated_group_ids
    current['selected_packing_assignments']=validated_assignments
    current['payment_match_candidates']=_payment_match_references(sources,context['version'])
    current['payment_match_candidate']=_payment_match_reference(sources,context['version'])
    if current.get('payment_match_candidates') != receipt.get('payment_match_candidates'):
        raise ValueError(_('实际付款流程匹配已变化，请刷新预览；本次未保存。'))
    if current.get('payment_match_candidate') != receipt.get('payment_match_candidate'):
        raise ValueError(_('实际付款流程匹配已变化，请刷新预览；本次未保存。'))
    revision=_preview_revision(context,items,sources,current_fees,catalog,current,dependencies)
    if revision!=receipt['revision']:
        raise ValueError(_('来源、费用或物料已变化，请刷新预览；本次未保存。'))
    if current.get('merged_amount_blocking'):
        raise ValueError(_('合并金额组尚未完成人工分摊或合计校验，本次未保存。'))
    selected_fee_ids=current.get('selected_fee_ids') or []
    selected_fees=current.get('fees') or []
    estimate_only=bool(selected_fee_ids) and len(selected_fees)==len(selected_fee_ids) and all(
        str((fee.get('payload') or {}).get('amount_status') or '').upper()=='ESTIMATED'
        for fee in selected_fees)
    if selected_fee_ids and not estimate_only and callable(getattr(repo,'assert_adoption_dependencies',None)):
        repo.assert_adoption_dependencies(batch_name,dependencies,lock=True)
    elif callable(getattr(repo,'assert_row_dependencies',None)):
        repo.assert_row_dependencies(batch_name,dependencies,lock=True,purpose='estimate')
    if not current['can_apply']:
        raise ValueError(_('请选择需要填充的物料或费用。'))
    fees.assert_allowed(current['fees'],current_fees,context.get('effective_source') or {})
    verified={**current,'id':receipt['id'],'revision':revision,'run_id':receipt['run_id'],'batch':batch_name,
              'version':context['version'],'source_context':context.get('effective_source') or {},
              'original_source_reanalysis':ai._run_uses_original_sources(run),
              'input_fingerprint':ai._record_value(run,'input_fingerprint'),
              'fee_fingerprint':digest(current_fees),'sources':deepcopy(sources),
              'dependencies':dependencies}
    return verified,context


def reconstruct_after_payment_match(repository,run,preview,draft):
    """Recompute after an atomic pending->confirmed match transition."""

    return _reconstruct_locked_preview(
        repository,preview['batch'],run,_preview_receipt(preview),draft)


def _confirm_locked(batch_name,run_id,preview_id,preview_revision,edit_token,expected_modified,
                    *,repo,payment_match_lock_held=False,rollback_on_error=True):
    from . import material_ai_fill_service as ai
    repo.lock_review_scope(batch_name)
    run=repo.lock_run(run_id)
    ai._assert_run_batch(run,batch_name)
    draft=ai._load_json(ai._record_value(run,'draft_json'),{})
    applied=draft.get('row_application') or {}
    if ai._record_value(run,'status')=='APPLIED':
        if applied.get('preview_id')!=preview_id:raise ValueError(_('此分析已按其他选择采用，不能重复确认。'))
        return {**applied,'ok':True,'idempotent':True}
    receipt=(draft.get('row_previews') or {}).get(preview_id)
    if not receipt or receipt.get('revision')!=preview_revision:
        raise ValueError(_('所选预览已变化，请刷新预览并使用最新预览后确认。'))
    if receipt.get('receipt_policy')!=RECEIPT_POLICY:
        raise ValueError(_('AI 预览规则已升级，请重新分析资料。'))
    if (receipt.get('payment_match_candidates') or receipt.get('payment_match_candidate')) and not payment_match_lock_held:
        raise ValueError(_('实际付款流程匹配已变化，请刷新预览；本次未保存。'))
    if receipt.get('mode') == 'replace_all':
        raise ValueError(_('整表替换仅能在独立的整源采纳流程中执行。'))
    if (not ai.is_material_ai_preview_ready(ai._record_value(run,'status'))
            or draft.get('current_row_preview')!=preview_id):
        raise ValueError(_('草稿或选择已变化，请使用最新预览。'))
    if (receipt.get('id')!=preview_id or receipt.get('run_id')!=run_id
            or receipt.get('batch')!=batch_name):
        raise ValueError(_('所选预览与当前分析不一致，请刷新预览后确认。'))
    repo.assert_write(batch_name,edit_token,expected_modified)
    repo.lock_review_inputs(batch_name,receipt['version'])
    verified,context=_reconstruct_locked_preview(repo,batch_name,run,receipt,draft)
    cleaned_draft=_clean_preview_draft(draft)
    try:
        return repo.apply_row_selection(run,verified,cleaned_draft,context)
    except Exception:
        if rollback_on_error:
            repo.rollback()
        raise


def _payment_match_receipt_hint(run,preview_id):
    """Route locking only; authority is re-read from the locked run below."""

    from . import material_ai_fill_service as ai
    draft=ai._load_json(ai._record_value(run,'draft_json'),{})
    receipt=(draft.get('row_previews') or {}).get(preview_id)
    return bool(isinstance(receipt,dict) and (
        receipt.get('payment_match_candidates') or receipt.get('payment_match_candidate')))


def confirm(batch_name,run_id,preview_id,preview_revision,edit_token,expected_modified,*,repository=None):
    from . import material_ai_fill_service as ai
    repo=repository or ai.FrappeMaterialAIFillRepository()
    initial=repo.get_run(run_id);ai._assert_run_batch(initial,batch_name)
    if _payment_match_receipt_hint(initial,preview_id):
        scope=getattr(repo,'payment_match_confirmation_scope',None)
        if not callable(scope):
            raise RuntimeError('当前存储不支持付款匹配原子确认')
        # The scope starts a savepoint and acquires match_lock before any
        # batch/version/review lock.  The locked run is then authoritative.
        with scope():
            return _confirm_locked(
                batch_name,run_id,preview_id,preview_revision,edit_token,expected_modified,
                repo=repo,payment_match_lock_held=True,rollback_on_error=False)
    return _confirm_locked(
        batch_name,run_id,preview_id,preview_revision,edit_token,expected_modified,
        repo=repo)
