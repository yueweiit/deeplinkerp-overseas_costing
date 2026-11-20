"""Cache explicit linked evidence before automatic review captures its inputs."""

from .dingtalk_approval_service import _trusted_linked_instance_ids
from .logistics_settlement.model import digest, norm, parse_source
from .logistics_settlement.freight_lines import financial_candidate
from .material_ai_source_dependencies import approval_eligibility


def hydrate_linked_sources(batch_name, version_name, *, source_context=None,
                          store=None, ledger=None, archive=None, prepare=None, logistics_codes=None):
    from . import packing_source_service
    from .logistics_settlement import runtime
    from .logistics_settlement.ledger import FrappeLedger
    from .logistics_settlement.store import Store

    store = store if store is not None else Store.frappe()
    ledger = ledger if ledger is not None else FrappeLedger()
    batch = ledger.get('batch', batch_name) or {}
    detail = packing_source_service.related_approval_detail(
        batch_name, version_name, bundle={'batch': batch, 'context': source_context or {}},
        store=store, ledger=ledger)
    lineage = detail.get('source_lineage') or {}
    coverage = {'status': 'COMPLETE', 'linked_count': 0, 'hydrated_count': 0, 'blockers': []}
    if not detail.get('ok'):
        if batch.get('source_type') == 'oa_logistics':
            coverage['blockers'].append({'code': 'MAIN_APPROVAL_ARCHIVE_MISSING',
                'hint': 'The current main approval archive is unavailable.'})
        else:
            coverage['status'] = 'NOT_APPLICABLE'
        return _finish(coverage)
    main = store.get('source', lineage['logistics_source_id'])
    corp = main['corp']
    instances = [value for value in _trusted_linked_instance_ids(main.get('raw') or {})
                 if value != main['instance']]
    coverage['linked_count'] = len(instances)
    local = {instance: store.find('source', corp=corp, instance=instance) for instance in instances}
    requested = [(corp, instance) for instance in instances]
    if requested:
        archive = archive if archive is not None else runtime.archive()
        try:
            upstream = {(str(row.get('corp_id') or ''), str(row.get('process_instance_id') or '')): row
                        for row in archive.get_sources(requested)}
        except Exception:
            upstream = None
        missing = [pair for pair in requested if upstream is not None and pair not in upstream]
        metadata = {}
        if missing:
            try:
                metadata = {(str(row.get('corp_id') or ''), str(row.get('process_instance_id') or '')): row
                            for row in archive.get_reference_metadata(missing)}
            except Exception:
                metadata = None
        mappings = store.find('batch_map', batch=batch_name)
        codes = logistics_codes if logistics_codes is not None else runtime.logistics_codes()
        for pair in requested:
            source_id = digest(*pair)
            if upstream is None:
                coverage['blockers'].append(_issue('LINKED_APPROVAL_ARCHIVE_UNAVAILABLE',
                    'Linked approval archive lookup could not be completed.', source_id, pair[1]))
                continue
            raw = upstream.get(pair)
            if raw is None:
                reference = metadata.get(pair) if metadata is not None else None
                if reference and _non_cost_reference(reference, codes):
                    coverage.setdefault('non_cost_references', []).append({
                        key: reference.get(key) for key in ('process_instance_id', 'process_code',
                                                           'template_name', 'title', 'status', 'result')})
                    coverage['non_cost_reference_count'] = len(coverage['non_cost_references'])
                    local.pop(pair[1], None)
                elif metadata is None:
                    coverage['blockers'].append(_issue('LINKED_APPROVAL_ARCHIVE_UNAVAILABLE',
                        'Linked approval archive lookup could not be completed.', source_id, pair[1]))
                elif reference:
                    coverage['blockers'].append(_issue('LINKED_APPROVAL_OUTSIDE_COST_SCOPE',
                        'A linked approval is outside the cost archive scope.', source_id, pair[1]))
                else:
                    coverage['blockers'].append(_issue('LINKED_APPROVAL_ARCHIVE_MISSING',
                        'A linked approval is not available in the upstream archive.', source_id, pair[1]))
                continue
            prior = (local[pair[1]] or [None])[0]
            if (prior and not _needs_documents(prior) and raw.get('archive_revision')
                    and prior.get('archive_revision') == raw['archive_revision']):
                continue
            try:
                enriched = (prepare or runtime.prepare_source)(raw, include_unclassified=True)
            except Exception:
                # The approval body remains readable when its attachment cannot be cached.
                enriched = raw
                coverage['blockers'].append(_issue('LINKED_ATTACHMENT_PREPARATION_FAILED',
                    'A linked attachment could not be prepared from the archive.', source_id, pair[1]))
            parsed = parse_source(enriched, logistics_codes=codes)
            with store.atomic():
                _assert_current(store, ledger, batch_name, version_name, mappings[0], lineage, main)
                stored = store.ingest(parsed)
            local[pair[1]] = [stored]
            coverage['hydrated_count'] += 1
    for instance, rows in local.items():
        if not rows:
            continue
        source = rows[0]
        if not approval_eligibility(source)['analysis_allowed']:
            coverage['blockers'].append(_issue('LINKED_APPROVAL_UNAVAILABLE',
                'A linked approval is withdrawn, rejected or unavailable.', source['id'], instance))
            continue
        documents = {str(row.get('file_id') or ''): row for row in source.get('documents') or []}
        for manifest in source.get('attachments') or []:
            if manifest.get('retired_at'):
                continue
            document = documents.get(str(manifest.get('file_id') or '')) or {}
            if manifest.get('archive_status') != 'archived':
                code, hint = 'LINKED_ATTACHMENT_ARCHIVE_PENDING', 'A linked attachment has not been archived upstream.'
            elif not document.get('file_url'):
                code, hint = 'LINKED_ATTACHMENT_CACHE_PENDING', 'A linked attachment is not yet available in the local cache.'
            else:
                continue
            issue = _issue(code, hint, source['id'], instance)
            issue.update(file_id=str(manifest.get('file_id') or ''), file_name=str(manifest.get('file_name') or ''))
            coverage['blockers'].append(issue)
    return _finish(coverage)


def _non_cost_reference(metadata, logistics_codes):
    if metadata.get('process_code') in logistics_codes or financial_candidate(metadata, {}):
        return False
    if financial_candidate({'title': metadata.get('title')}, {}):
        return False
    name = norm(metadata.get('template_name') or metadata.get('title'))
    return name in {norm('开模申请'), norm('LEMOS-开模申请 Fabricación de moldes')}


def _needs_documents(source):
    documents = {str(row.get('file_id') or ''): row for row in source.get('documents') or []}
    return any(not manifest.get('retired_at') and (
        manifest.get('archive_status') != 'archived'
        or not (documents.get(str(manifest.get('file_id') or '')) or {}).get('file_url'))
        for manifest in source.get('attachments') or [])


def _assert_current(store, ledger, batch_name, version_name, mapping, lineage, main):
    store.get('state', 'match_lock', lock=True)
    source = store.get('source', lineage['logistics_source_id'], lock=True) or {}
    current_map = store.get('batch_map', mapping['id'], lock=True) or {}
    batch = ledger.get('batch', batch_name, lock=True) or {}
    if (batch.get('current_version') != version_name
            or batch.get('source_instance_id') and batch['source_instance_id'] != main['instance']
            or batch.get('source_corp_id') and batch['source_corp_id'] != main['corp']
            or current_map.get('source_id') != main['id']
            or (source.get('corp'), source.get('instance'), source.get('snapshot'))
               != (main['corp'], main['instance'], lineage['logistics_snapshot'])):
        raise ValueError('The current approval source changed before linked evidence was cached.')


def _issue(code, hint, source_id, instance):
    return {'code': code, 'hint': hint, 'source_id': source_id, 'process_instance_id': instance}


def _finish(coverage):
    if coverage['blockers']:
        coverage['status'] = 'PARTIAL'
    return coverage
