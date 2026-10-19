"""Local evidence dependencies for adopted AI rows; never resolves or fetches sources."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from copy import deepcopy

from .logistics_settlement.application import row_meta
from .logistics_settlement.document_writer import document_retired
from .logistics_settlement.model import digest


INVALID_APPROVAL_STATUSES = {
    'TERMINATED', 'CANCELED', 'CANCELLED', 'DELETED', 'REJECTED', 'REFUSED',
    'DENIED', 'ABORTED', 'WITHDRAWN', 'WITHDRAW', 'REVOKED',
    '撤销', '已撤销', '拒绝', '已拒绝', '驳回', '已驳回', '不通过', '未通过',
    '终止', '已终止', '取消', '已取消', '作废', '已作废',
}
INVALID_APPROVAL_RESULTS = {
    'refuse', 'refused', 'reject', 'rejected', 'deny', 'denied', 'disagree',
    '拒绝', '已拒绝', '驳回', '已驳回', '不通过', '未通过',
}


class SourceEligibilityError(ValueError):
    """Safe source identity and a business reason, never archived document contents."""
    def __init__(self, reason, *, source=None, code='SOURCE_UNAVAILABLE'):
        source = source or {}
        self.code = code
        self.source_id = str(source.get('source_id') or source.get('id') or '')[:500]
        self.approval_no = str(source.get('approval_no') or '')[:200]
        self.source_label = str(source.get('source_label') or source.get('title') or '')[:300]
        identity = ' · '.join(filter(None, (self.approval_no, self.source_label)))
        super().__init__(f'{identity}：{reason}' if identity and not str(reason).startswith(f'{identity}：') else reason)


def approval_eligibility(source):
    """Archive readability is independent of settlement classification/approval."""
    status = str(source.get('status') or '').upper()
    result = str(source.get('approval_result') or '').lower()
    invalid = status in INVALID_APPROVAL_STATUSES or result in INVALID_APPROVAL_RESULTS
    readable = _enabled(source) and not invalid
    reason = '' if readable else ('审批已拒绝、撤销或停用，不能用于当前分析。' if source else '本地审批归档缺失，请核对资料。')
    return {'analysis_allowed': bool(readable), 'analysis_reason': reason,
            'analysis_code': '' if readable else ('SOURCE_INVALID' if source else 'SOURCE_ARCHIVE_MISSING'),
            'adoption_allowed': bool(readable),
            'final_fee_allowed': bool(readable), 'adoption_restriction': ''}


def annotate_source_eligibility(sources, *, store, ledger, batch_name):
    """Use the same read checks for the displayed catalog and task execution."""
    annotated = []
    for raw in sources:
        source = deepcopy(raw)
        source.update(analysis_allowed=True, analysis_reason='', analysis_code='',
                      adoption_allowed=True, final_fee_allowed=False, adoption_restriction='')
        source['analysis_required'] = bool(raw.get('source_kind') == 'approval_form'
            and raw.get('approval_role') in {'international_logistics', 'logistics_expense'})
        try:
            if raw.get('excluded') or raw.get('available') is False and not raw.get('can_download'):
                raise SourceEligibilityError(raw.get('exclude_reason') or '资料不可读取，请核对来源。', source=raw)
            context = raw.get('source_context') or {}
            context = context.get('packing') or context
            instance = raw.get('process_instance_id') or context.get('instance_id')
            if instance:
                matches = store.find('source', instance=instance, **({'corp': context['corp_id']} if context.get('corp_id') else {}))
                if len(matches) != 1:
                    raise SourceEligibilityError(_('缺少唯一的本地审批归档，请核对资料。'), source=raw, code='SOURCE_ARCHIVE_MISSING')
                source.update(approval_eligibility(matches[0]))
            # Explicit selection ensures excluded rows cannot silently pass this check.
            capture_dependencies([{**raw, 'selected': True}], store=store, ledger=ledger,
                batch_name=batch_name, source_context=context, allow_pending=True, purpose='analysis', lock=False)
            if raw.get('analysis_only'):
                source.update(
                    adoption_allowed=False,
                    final_fee_allowed=False,
                    adoption_restriction=(
                        raw.get('adoption_restriction')
                        or '该附件仅用于 AI 读取和预览，不能单独作为最终采用依据。'
                    ),
                )
        except (ValueError, OSError) as error:
            source.update(analysis_allowed=False, adoption_allowed=False, final_fee_allowed=False,
                analysis_reason=str(error), analysis_code=getattr(error, 'code', 'SOURCE_ARCHIVE_UNAVAILABLE'))
        annotated.append(source)
    return annotated


def _enabled(record):
    return bool(record) and not (record.get('invalid') or record.get('disabled') or record.get('excluded')
        or record.get('retired_at') or record.get('available') is False or record.get('is_active') in (0, '0'))


def _current_attachment_approval(dependency, metadata, store):
    source_id = str(dependency.get('approval_source_id') or '')
    if not source_id:
        return None
    instance = str(metadata.get('process_instance_id') or metadata.get('instance_id') or '')
    if not instance:
        return None
    source = store.get('source', source_id) or {}
    if str(source.get('instance') or '') != instance:
        raise ValueError(_('附件的当前审批身份不一致。'))
    if metadata.get('corp_id') and str(source.get('corp') or '') != str(metadata['corp_id']):
        raise ValueError(_('附件的当前审批企业身份不一致。'))
    return source


def _assert_archived_approval_attachment(attachment, metadata, descriptor, document, approval):
    """Bind a relaxed analysis read to the server-owned approval archive."""
    if str(attachment.get('source_type') or '').upper() != 'OA':
        raise ValueError(_('附件不是审批归档资料。'))
    source_id = str(approval.get('id') or '')
    document_id = str(descriptor.get('document_id') or '')
    if not source_id or not document_id or not document:
        raise ValueError(_('附件审批归档身份不完整。'))
    archived = [row for row in approval.get('documents') or []
                if str(row.get('id') or '') == document_id]
    if len(archived) != 1:
        raise ValueError(_('附件不在当前审批归档中。'))
    archived = archived[0]
    if (not _enabled(archived) or document_retired(archived)
            or archived.get('status') in {'pending', 'failed', 'error', 'invalid'}):
        raise ValueError(_('附件在当前审批归档中已停用或读取失败。'))
    if any(str(row.get('source_id') or '') != source_id
           for row in (descriptor, document, archived)):
        raise ValueError(_('附件的审批归档来源不一致。'))

    manifests = [row.get('manifest') or {} for row in (descriptor, document, archived)]
    file_ids = [str(metadata.get('file_id') or ''),
                *(str(manifest.get('file_id') or '') for manifest in manifests)]
    if not file_ids[0] or len(set(file_ids)) != 1:
        raise ValueError(_('附件的审批文件身份不一致。'))

    instance = str(approval.get('instance') or '')
    instances = [str(metadata.get('process_instance_id') or metadata.get('instance_id') or ''),
                 *(str(manifest.get('process_instance_id') or '') for manifest in manifests)]
    if not instance or any(value != instance for value in instances):
        raise ValueError(_('附件的审批实例身份不一致。'))

    corp = str(approval.get('corp') or '')
    corps = [str(metadata.get('corp_id') or ''),
             *(str(manifest.get('corp_id') or '') for manifest in manifests)]
    if corp and any(value != corp for value in corps):
        raise ValueError(_('附件的审批企业身份不一致。'))

    file_urls = [str(attachment.get('file_url') or ''), str(document.get('file_url') or ''),
                 str(archived.get('file_url') or '')]
    file_names = [str(attachment.get('file_name') or ''), str(document.get('file_name') or ''),
                  str(archived.get('file_name') or '')]
    fingerprints = [str(descriptor.get('fingerprint') or ''), str(document.get('fingerprint') or ''),
                    str(archived.get('fingerprint') or '')]
    hashes = [str(manifest.get('sha256') or '').lower() for manifest in manifests]
    if (not file_urls[0] or len(set(file_urls)) != 1
            or not file_names[0] or len(set(file_names)) != 1
            or not fingerprints[0] or len(set(fingerprints)) != 1
            or not hashes[0] or len(set(hashes)) != 1):
        raise ValueError(_('附件与服务器审批归档不一致。'))


def _read_dependency(dependency, store, ledger, batch_name, *, lock=False, purpose="adoption"):
    if purpose not in {"analysis", "estimate", "adoption"}:
        raise ValueError(_("来源校验阶段不合法。"))
    kind = dependency['kind']
    if kind == 'pending_attachment':
        # A remote file has no local row yet. Its immutable resolver identity is
        # fenced by the archived approval until the worker seals the local file.
        approval = _read_dependency({'kind': 'approval', 'source_id': dependency['source_id']},
                                    store, ledger, batch_name, lock=lock, purpose=purpose)
        if approval.get('instance') != dependency.get('process_instance_id'):
            raise ValueError(_('待下载附件的审批身份已变化。'))
        return {'identity': {k:v for k,v in dependency.items() if k != 'fingerprint'},
                'approval': approval}
    if kind == 'approval':
        source = store.get('source', dependency['source_id'], lock=lock) or {}
        eligibility = approval_eligibility(source)
        if not eligibility['analysis_allowed']:
            raise SourceEligibilityError(eligibility['analysis_reason'], source=source, code=eligibility['analysis_code'])
        if purpose == 'adoption' and not eligibility['final_fee_allowed']:
            raise SourceEligibilityError(eligibility['adoption_restriction'], source=source, code='SOURCE_NOT_APPROVED')
        batch = ledger.get('batch', batch_name, lock=lock) or {}
        from .import_service import _get_linked_purchase_approvals_from_extra
        reference = next((row for row in _get_linked_purchase_approvals_from_extra(batch.get('extra_json'))
                          if row.get('source_instance_id') == source.get('instance')), None)
        if batch.get('source_instance_id') == source.get('instance'):
            reference = {'source_instance_id': batch['source_instance_id'], 'status': batch.get('source_approval_status')}
        documents = store.find('document', source_id=source['id'])
        if lock:
            documents = [store.get('document', document['id'], lock=True) for document in documents]
        # Document records and comments can change independently of the root snapshot.
        return {key: source.get(key) for key in ('id', 'corp', 'instance', 'snapshot', 'kind', 'approved',
            'invalid', 'status', 'approval_result', 'raw', 'documents')} | {
                'archived_documents': documents, 'approval_reference': reference}
    if kind == 'attachment':
        attachment = ledger.get('attachment', dependency['attachment_id'], lock=lock) or {}
        if not _enabled(attachment) or attachment.get('batch') != batch_name or not attachment.get('file_url'):
            raise ValueError(_('附件来源缺失或已停用。'))
        metadata = row_meta({'extra_json': attachment.get('parse_result_json')})
        if not _enabled(metadata or {'present': True}):
            raise ValueError(_('附件来源已被排除。'))
        descriptor = metadata.get('settlement_document') or {}
        restricted = (
            metadata.get('approval_excluded')
            or metadata.get('cost_source_allowed') is False
            or descriptor.get('audit_only')
        )
        current_approval = (_current_attachment_approval(dependency, metadata, store)
                            if dependency.get('approval_source_id') else None)
        if restricted:
            if purpose not in {'analysis', 'estimate'} or not current_approval:
                raise ValueError(_('附件来源已被排除。'))
            eligibility = approval_eligibility(current_approval)
            if not eligibility['analysis_allowed']:
                raise SourceEligibilityError(
                    eligibility['analysis_reason'], source=current_approval,
                    code=eligibility['analysis_code'],
                )
        document_id = descriptor.get('document_id')
        document = store.get('document', document_id, lock=lock) if document_id else None
        if document_id and (not document or document_retired(document) or document.get('status') in {'pending', 'failed', 'error', 'invalid'}):
            raise ValueError(_('附件归档缺失、已停用或读取失败。'))
        if document_retired(descriptor) or descriptor.get('status') in {'pending', 'failed', 'error', 'invalid'}:
            raise ValueError(_('附件归档已停用或读取失败。'))
        if current_approval:
            _assert_archived_approval_attachment(
                attachment, metadata, descriptor, document, current_approval,
            )
        # The archive owns content hashing. Ordinary scope reads only compare
        # persisted hashes/metadata and never reopen or download large files.
        return {'attachment': {key: attachment.get(key) for key in ('name', 'batch', 'version', 'file_url',
                    'file_name', 'source_type', 'modified', 'parse_result_json')}, 'document': document}
    if kind == 'wiki':
        from .packing_source_service import _bound_wiki_cache_key, load_bound_wiki_snapshot
        context = dependency['source_context']
        cached = store.get('state', _bound_wiki_cache_key(context, dependency['source_id']), lock=lock) or {}
        trusted = load_bound_wiki_snapshot(context, dependency['source_id'], store=store)
        if not _enabled(cached) or not _enabled(trusted or {}) or not (trusted or {}).get('source_hash'):
            raise ValueError(_('工作表本地归档缺失或已停用。'))
        # Refresh timestamps/generations are operational; content is the dependency.
        return {'source_context': cached.get('source_context'),
                'source_hash': trusted['source_hash'], 'grid': trusted.get('grid'), 'preview': trusted.get('preview')}
    if kind == 'wiki_snapshot':
        frappe = getattr(ledger, 'frappe', None)
        if frappe is None:
            raise ValueError(_('工作表缺少本地已确认快照。'))
        snapshots = frappe.get_all('Overseas Packing Snapshot',
            filters={'batch': batch_name, 'source_kind': 'wiki_sheet', 'source_id': dependency['source_id'],
                     'status': 'Confirmed', 'is_current': 1},
            fields=['name', 'batch', 'source_kind', 'source_id', 'source_hash', 'status', 'is_current',
                    'material_rows_json', 'package_groups_json', 'validation_json'], limit_page_length=2)
        if len(snapshots) != 1 or not snapshots[0].get('source_hash') or snapshots[0].get('status') != 'Confirmed' or not snapshots[0].get('is_current'):
            raise ValueError(_('工作表本地快照缺失或已停用。'))
        return dict(snapshots[0])
    if kind == 'document':
        document = store.get('document', dependency['document_id'], lock=lock) or {}
        if not _enabled(document) or document_retired(document) or document.get('status') in {'pending', 'failed', 'error', 'invalid'}:
            raise ValueError(_('附件归档缺失、已停用或读取失败。'))
        return document
    if kind == 'source_document':
        source = store.get('source', dependency['source_id'], lock=lock) or {}
        documents = [document for document in source.get('documents') or []
                     if document.get('id') == dependency['document_id']]
        if not _enabled(source) or len(documents) != 1:
            raise ValueError(_('审批内的附件归档缺失或身份不唯一。'))
        document = documents[0]
        if not _enabled(document) or document_retired(document) or document.get('status') in {'pending', 'failed', 'error', 'invalid'}:
            raise ValueError(_('审批内的附件归档已停用或读取失败。'))
        return document
    raise ValueError(_('来源依赖类型无法核对。'))


def dependency_issues(adoption, *, store=None, ledger=None, batch_name='', lock=False, purpose='adoption'):
    dependencies = adoption.get('dependencies')
    if dependencies is None:
        return ['所选来源缺少依赖记录，请重新分析采用。'] if adoption.get('sources') else []
    if not isinstance(dependencies, list):
        return ['所选来源依赖记录无效，请重新分析采用。']
    if dependencies and (store is None or ledger is None):
        return ['所选来源暂时无法核对，请重新读取本地资料。']
    issues = []
    for dependency in dependencies:
        try:
            current = _read_dependency(dependency, store, ledger, batch_name, lock=lock, purpose=purpose)
            if digest(current) != dependency.get('fingerprint'):
                issues.append('所选来源内容已更新，请重新分析采用。')
        except SourceEligibilityError as error:
            issues.append(str(error))
        except (ValueError, KeyError, TypeError, OSError):
            issues.append('所选来源缺失、已更新或停用，请重新核对资料。')
    return sorted(set(issues))


def capture_dependencies(sources, *, store, ledger, batch_name, source_context, inherited=None, allow_pending=False, purpose="adoption", lock=True):
    """Save descriptors plus current local fingerprints after the run was revalidated."""
    dependencies = {}

    def add(descriptor):
        key = digest(descriptor)
        if key not in dependencies:
            try:
                fingerprint = digest(_read_dependency(descriptor, store, ledger, batch_name, lock=lock, purpose=purpose))
            except SourceEligibilityError:
                raise
            except (ValueError, KeyError, TypeError, OSError) as exc:
                raise SourceEligibilityError(str(exc) if isinstance(exc, ValueError) else '本地归档无法读取，请核对资料。',
                    source=active_source, code='SOURCE_ARCHIVE_UNAVAILABLE') from exc
            dependencies[key] = {**descriptor, 'fingerprint': fingerprint}

    active_source = {}
    for source in sources or []:
        active_source = source
        if source.get('selected') is False and not source.get('locked'):
            continue
        source_id = str(source.get('resolver_source_id') or source.get('attachment_name') or source.get('source_id') or '')
        if inherited and (source_id == inherited.get('id') or source.get('scoped_packing')):
            if dependency_issues(inherited, store=store, ledger=ledger, batch_name=batch_name, lock=lock, purpose=purpose):
                raise ValueError(_('所选来源已变化，请重新分析采用。'))
            for saved in inherited.get('dependencies') or []:
                descriptor = {key: value for key, value in saved.items() if key != 'fingerprint'}
                dependencies[digest(descriptor)] = deepcopy(saved)
            continue
        kind = source.get('source_kind')
        context = source.get('source_context') or source_context
        context = context.get('packing') or context
        instance = source.get('process_instance_id') or context.get('instance_id')
        approval_source = {}
        if instance:
            candidates = store.find('source', instance=instance, **({'corp': context['corp_id']} if context.get('corp_id') else {}))
            if len(candidates) != 1:
                raise SourceEligibilityError(_('所选审批来源缺少唯一的本地归档。'), source=source, code='SOURCE_ARCHIVE_MISSING')
            approval_source = candidates[0]
            add({'kind': 'approval', 'source_id': candidates[0]['id']})
        if kind in ('approval_attachment', 'approval_comment_attachment', 'manual_attachment'):
            if source.get('download_required'):
                if not (allow_pending and kind != 'manual_attachment' and source.get('can_download')
                        and context.get('root_kind') != 'expense' and approval_source
                        and source.get('process_instance_id') and source.get('file_id')):
                    raise ValueError(_('所选附件尚未完成本地归档，请先完成下载读取。'))
                add({'kind': 'pending_attachment', 'source_id': approval_source['id'],
                     **{key: source.get(key) for key in ('process_instance_id', 'file_id',
                         'logical_source_id', 'content_hash')}, 'resolver_source_id': source_id})
                continue
            selected = source.get('selected_source') or context.get('selected_source') or {}
            document_id = selected.get('document_id') or (selected.get('evidence') or {}).get('document_id')
            if source.get('scoped_packing') and document_id:
                # Packing selection reads source.documents directly; older archives
                # need not have a duplicate oc_ls_document row or attachment map.
                # Persist this exact locator so a later removal cannot fall back.
                if any(document.get('id') == document_id for document in approval_source.get('documents') or []):
                    add({'kind': 'source_document', 'source_id': approval_source['id'], 'document_id': document_id})
                else:
                    add({'kind': 'document', 'document_id': document_id})
                mappings = store.find('attachment_map', document_id=document_id, version=context.get('cost_version') or '')
                for mapping in mappings:
                    descriptor = {'kind': 'attachment', 'attachment_id': mapping['attachment']}
                    if kind in {'approval_attachment', 'approval_comment_attachment'} and approval_source:
                        descriptor['approval_source_id'] = approval_source['id']
                    add(descriptor)
            else:
                descriptor = {'kind': 'attachment', 'attachment_id': source_id}
                if kind in {'approval_attachment', 'approval_comment_attachment'} and approval_source:
                    descriptor['approval_source_id'] = approval_source['id']
                add(descriptor)
        elif kind == 'wiki_sheet':
            from .packing_source_service import _bound_wiki_cache_key
            if context.get('root_kind') == 'expense' or store.get('state', _bound_wiki_cache_key(context, source_id)):
                add({'kind': 'wiki', 'source_id': source_id, 'source_context': deepcopy(context)})
            else:
                add({'kind': 'wiki_snapshot', 'source_id': source_id})
        elif kind in ('approval_form', 'approval_comment'):
            if not instance:
                raise ValueError(_('所选审批来源缺少本地身份。'))
        else:
            raise ValueError(_('所选来源类型无法在本地核对。'))
    return list(dependencies.values())
