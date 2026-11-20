"""Read original workbook images without mistaking system limits for missing evidence."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from overseas_costing.services import allocation_service, attachment_parse_service, cost_preview_service
from overseas_costing.services import dingtalk_approval_service, material_ai_fill_service as ai, packing_source_service
from overseas_costing.services import workbench_bulk_ai_service as controller
from overseas_costing.services.material_ai_vision import document_read_issues
from overseas_costing.tests.test_workbench_bulk_ai_service import runtime


@pytest.fixture
def workbook(tmp_path):
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image
    from PIL import Image as PillowImage

    picture = tmp_path / 'source.png'
    PillowImage.new('RGB', (8, 8), 'blue').save(picture)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Images'
    for index in range(35):
        sheet.add_image(Image(picture), 'D' + str(index + 1))
    path = tmp_path / 'statement.xlsx'
    book.save(path)
    return path


def read_document(path, monkeypatch):
    monkeypatch.setattr(attachment_parse_service, '_resolve_source_file_path', lambda **kwargs: path)
    monkeypatch.setattr(packing_source_service, 'resolve_trusted_packing_source', lambda **kwargs: (
        _ for _ in ()).throw(ValueError('未识别到装箱表头或明细。')))
    _, document = ai._read_source([], {'batch': 'B1', 'source_id': 'ATT', 'source_label': path.name,
        'source_kind': 'approval_attachment', 'sheet_name': 'Images'},
        prepared_attachment={'source_id': 'ATT', 'file_name': path.name, 'file_url': str(path), 'trusted_workbook': True})
    document['document_id'] = 'DOC'
    return document


def vision_provider(monkeypatch, *, partial=False):
    batches = []
    monkeypatch.setattr(ai, '_vision_config', lambda: {'api_key': 'test', 'model': 'vision'})
    monkeypatch.setattr(allocation_service, '_ai_config', lambda: {'api_key': 'test', 'model': 'text'})
    monkeypatch.setattr(ai, '_ocr_embedded_image', lambda image: '')

    def call(config, messages, **kwargs):
        content = messages[0]['content']
        if not isinstance(content, list):
            return '{"proposals":[]}'
        index = json.loads(content[0]['text'].partition('图片索引：')[2])
        batches.append(index)
        selected = index[:1] if partial else index
        return json.dumps({'observations': [{**row, 'description': 'Visible source text'} for row in selected]})

    monkeypatch.setattr(allocation_service, '_call_chat_completions', call)
    return batches


def test_image_only_workbook_without_packing_headers_reaches_all_35_vision_transcriptions(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    assert not document['semantic_rows'] and not document['structured_rows']
    assert len(document['_image_payloads']) == len(document['image_anchors']) == 35
    batches = vision_provider(monkeypatch)
    result = ai._call_source_review_ai([], [document])
    assert [len(batch) for batch in batches] == [20, 15]
    assert {row['anchor']['cell'] for batch in batches for row in batch} == {'D' + str(index + 1) for index in range(35)}
    evidence = result['evidence_documents'][0]
    assert len(evidence['vision_observations']) == 35 and not document_read_issues(evidence)
    assert evidence['image_read_status']['processed'] == 35
    assert '_image_payloads' not in evidence and not result['proposals']


def test_vision_byte_budget_splits_batches_instead_of_dropping_images(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    budget = sum(image['size_bytes'] for image in document['_image_payloads'][:2])
    monkeypatch.setattr(ai, 'MAX_VISION_IMAGE_BYTES', budget)
    batches = vision_provider(monkeypatch)
    ai._call_vision_style_descriptions([document])
    assert len(batches) == 18 and sum(map(len, batches)) == 35
    assert max(map(len, batches)) == 2 and not document_read_issues(document)


def test_missing_model_transcriptions_keep_exact_pending_anchors_visible(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    vision_provider(monkeypatch, partial=True)
    ai._call_vision_style_descriptions([document])
    issue = next(row for row in document_read_issues(document) if row['code'] == 'SOURCE_IMAGES_UNREAD')
    assert issue['code'] == 'SOURCE_IMAGES_UNREAD' and issue['pending_count'] == 33
    assert len(issue['anchors']) == 33 and {'sheet': 'Images', 'cell': 'D2'} in issue['anchors']
    assert issue['file_name'] == workbook.name and '33' in issue['hint']


def test_oversized_individual_images_are_explicit_system_issues_not_empty_content(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    monkeypatch.setattr(ai, 'MAX_VISION_IMAGE_BYTES', 1)
    batches = vision_provider(monkeypatch)
    ai._call_vision_style_descriptions([document])
    assert not batches and ai._document_has_evidence(document)
    issues = document_read_issues(document)
    assert len([issue for issue in issues if issue['code'] == 'SOURCE_IMAGE_SIZE_LIMIT']) == 35
    assert issues[-1]['pending_count'] == 35


def test_interrupted_vision_batches_keep_completed_and_remaining_images(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    calls = []

    class BudgetExpired(BaseException):
        pass

    def batch(images):
        calls.append(images)
        if len(calls) == 2:
            raise BudgetExpired()
        return {'ok': True, 'observations': [{**row, 'description': 'Visible text'} for row in images]}

    monkeypatch.setattr(ai, '_call_vision_batch', batch)
    with pytest.raises(BudgetExpired):
        ai._call_vision_style_descriptions([document])
    assert document['image_read_status']['processed'] == 20
    assert document_read_issues(document)[0]['pending_count'] == 15


@pytest.mark.parametrize('trusted,size,configured,allowed', [
    (True, 39, None, True), (False, 39, None, False), (True, 51, None, False),
    (True, 39, 35, False), (True, 39, 45, True),
])
def test_large_workbook_limit_only_extends_complete_trusted_archives(tmp_path, monkeypatch, trusted, size, configured, allowed):
    path = tmp_path / 'large.xlsx'
    with path.open('wb') as handle:
        handle.truncate(size * 1024 * 1024)
    row = {'name': 'ATT', 'batch': 'B1', 'source_type': 'OA', 'file_name': path.name, 'file_url': str(path)}
    config = {} if configured is None else {'overseas_cost_ai_trusted_workbook_max_bytes': configured * 1024 * 1024}
    monkeypatch.setattr(ai, 'frappe', SimpleNamespace(conf=config, db=SimpleNamespace(get_value=lambda *args, **kwargs: row)))
    monkeypatch.setattr(ai.effective_source, 'current_source_bundle', lambda *args: None)
    monkeypatch.setattr(dingtalk_approval_service, 'attachment_has_complete_archive_binding', lambda *args: trusted)
    monkeypatch.setattr(attachment_parse_service, '_resolve_source_file_path', lambda **kwargs: path)
    source = {'source_id': 'ATT', 'batch': 'B1'}
    if allowed:
        assert ai._ensure_local_attachment(source)['trusted_workbook']
    else:
        with pytest.raises(ai.EvidenceReadSkipped) as failure:
            ai._ensure_local_attachment(source)
        assert failure.value.code == 'SOURCE_SIZE_LIMIT'
        assert 'system reading limit' in failure.value.safe_text and '重新上传' not in failure.value.safe_text


def test_explicit_semantic_budget_reuses_the_native_deadline_wrapper(monkeypatch):
    from overseas_costing.services import logistics_autofill_service

    limits = []
    monkeypatch.setattr(logistics_autofill_service, 'run_supplement',
                        lambda callback, *, seconds: (limits.append(seconds) or callback()))
    assert ai._run_ai_semantic(lambda: {'ok': True}, fallback={})['ok']
    assert ai._run_ai_semantic(lambda: {'ok': True}, fallback={}, seconds=300)['ok']
    assert limits == [60, 300]


def test_system_unread_images_never_become_a_request_for_users_to_upload_more_documents(runtime, monkeypatch):
    repo, _ = runtime
    repo.run['trigger_mode'] = 'LOCAL_UPLOAD'
    issue = {'code': 'SOURCE_IMAGES_UNREAD', 'hint': 'Some workbook images have not been transcribed (15 remaining).',
             'file_name': 'statement.xlsx', 'pending_count': 15}
    repo.run['draft_json']['source_read_issues'] = [issue]
    monkeypatch.setattr(cost_preview_service, 'calculate_comprehensive_cost', lambda *args, **kwargs: {
        'ok': True, 'saved': True, 'summary': {'is_complete': True}})
    result = controller.continue_automatic_review('B1', repo.run['name'])
    assert result['status'] == 'REVIEW_REQUIRED' and result['saved']
    assert result['required_documents'] == [] and result['source_coverage']['blockers'] == [issue]
    assert len(repo.writes) == 1


@pytest.mark.parametrize('trusted,budget', [(False, 60), (True, 300)])
def test_worker_persists_unread_anchors_and_uses_the_trusted_workbook_budget(workbook, monkeypatch, trusted, budget):
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository, _items

    document = read_document(workbook, monkeypatch)
    document['trusted_workbook'] = trusted
    repository = _LifecycleRepository(status='QUEUED')
    repository.sources = [{'source_kind': 'approval_attachment', 'source_id': 'ATT',
        'source_hash': 'HASH', 'source_label': workbook.name, 'file_name': workbook.name}]
    manifest = ai.prepare_source_manifest(repository.sources)
    repository.run.update(proposal_version=1, source_manifest_json=manifest,
        input_fingerprint=ai._source_review_fingerprint('B1', 'V1', _items(), manifest, '',
                                                      context=repository.get_context('B1', 'V1')))
    monkeypatch.setattr(ai, '_read_source', lambda *args, **kwargs: ([], document))
    budgets = []

    def semantic(callback, *, fallback, seconds=None):
        budgets.append(seconds)
        return callback()

    monkeypatch.setattr(ai, '_run_ai_semantic', semantic)
    vision_provider(monkeypatch, partial=True)
    result = ai.execute_material_ai_fill('RUN-1', repository=repository)
    assert result['status'] == 'READY_WITH_WARNINGS'
    draft = ai._load_json(repository.run['draft_json'], {})
    unread = next(row for row in draft['source_read_issues'] if row['code'] == 'SOURCE_IMAGES_UNREAD')
    assert unread['pending_count'] == 33
    assert unread['file_name'] == workbook.name
    progress = ai._load_json(repository.run['source_progress_json'], [])
    assert progress[0]['status'] == 'PARTIAL' and progress[0].get('skip_reason_code') != 'NO_RECOGNIZABLE_CONTENT'
    assert budgets == [budget] and not repository.applied


def test_failed_vision_uses_native_ocr_for_all_images_and_removes_temporary_files(workbook, monkeypatch):
    from pathlib import Path

    document = read_document(workbook, monkeypatch)
    monkeypatch.setattr(ai, '_call_vision_batch', lambda images: {'ok': False, 'observations': []})
    paths = []

    def ocr(path):
        assert path.read_bytes().startswith(b'\x89PNG')
        paths.append(Path(path))
        return 'Original OCR text; no adopted prices'

    monkeypatch.setattr(attachment_parse_service, '_ocr_image_file', ocr)
    result = ai._run_ai_semantic(lambda: ai._call_vision_style_descriptions([document]), fallback={})

    assert result['ok'] and len(result['observations']) == 35
    assert all(row['method'] == 'ocr' and row['document_id'] == 'DOC' for row in result['observations'])
    assert document['image_read_status']['ocr_processed'] == 35
    assert document['image_read_status']['vision_processed'] == 0
    assert document['image_read_status']['pending'] == 0 and not document_read_issues(document)
    assert len(paths) == 35 and all(not path.exists() for path in paths)
    assert not result.get('proposals') and not result.get('candidates')


def test_ocr_only_reads_anchors_missing_from_the_vision_response(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    vision_provider(monkeypatch, partial=True)
    anchors = []
    monkeypatch.setattr(ai, '_ocr_embedded_image', lambda image: (
        anchors.append(image['anchor']['cell']) or 'OCR text'))

    result = ai._call_vision_style_descriptions([document])

    assert result['ok'] and len(anchors) == 33
    assert 'D1' not in anchors and 'D21' not in anchors
    assert document['image_read_status']['vision_processed'] == 2
    assert document['image_read_status']['ocr_processed'] == 33
    assert not document_read_issues(document)


def test_recoverable_ocr_failure_keeps_pending_anchors_and_cleans_files(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    monkeypatch.setattr(ai, '_call_vision_batch', lambda images: {'ok': False, 'observations': []})
    paths = []

    def unreadable(path):
        paths.append(path)
        raise RuntimeError('附件 OCR 处理失败：unreadable image')

    monkeypatch.setattr(attachment_parse_service, '_ocr_image_file', unreadable)
    result = ai._call_vision_style_descriptions([document])

    assert not result['ok'] and document['image_read_status']['pending'] == 35
    assert sum(row['code'] == 'SOURCE_IMAGE_OCR_UNREAD' for row in document_read_issues(document)) == 35
    assert len(paths) == 35 and all(not path.exists() for path in paths)


@pytest.mark.parametrize('error', [RuntimeError('当前环境缺少 tesseract'), PermissionError('native temp denied')])
def test_fatal_ocr_runtime_failure_escapes_the_native_semantic_fallback(workbook, monkeypatch, error):
    document = read_document(workbook, monkeypatch)
    monkeypatch.setattr(ai, '_call_vision_batch', lambda images: {'ok': False, 'observations': []})
    monkeypatch.setattr(attachment_parse_service, '_ocr_image_file', lambda path: (
        _ for _ in ()).throw(error))

    with pytest.raises(type(error)) as caught:
        ai._run_ai_semantic(lambda: ai._call_vision_style_descriptions([document]), fallback={})

    assert caught.value is error


def test_ocr_budget_interruption_preserves_already_read_anchors(workbook, monkeypatch):
    document = read_document(workbook, monkeypatch)
    vision_provider(monkeypatch, partial=True)
    calls = []

    class BudgetExpired(BaseException):
        pass

    def ocr(image):
        calls.append(image['anchor']['cell'])
        if len(calls) == 2:
            raise BudgetExpired()
        return 'OCR text'

    monkeypatch.setattr(ai, '_ocr_embedded_image', ocr)
    with pytest.raises(BudgetExpired):
        ai._call_vision_style_descriptions([document])

    assert document['image_read_status']['processed'] == 2
    assert document['image_read_status']['vision_processed'] == document['image_read_status']['ocr_processed'] == 1
    assert document['image_read_status']['pending'] == 33
    assert {'sheet': 'Images', 'cell': 'D3'} in document['image_read_status']['pending_anchors']


def test_scoped_packing_rows_do_not_open_workbook_images_for_ocr(monkeypatch):
    monkeypatch.setattr(ai, '_ensure_local_attachment', lambda source: pytest.fail('opened whole workbook'))
    monkeypatch.setattr(ai, '_ocr_embedded_image', lambda image: pytest.fail('OCR outside scoped rows'))
    _, document = ai._read_source([], {'source_id': 'SCOPED', 'scoped_packing': True,
                                     'scoped_goods': [], 'scoped_text': 'Authorized payment rows only'})
    document['document_id'] = 'SCOPED'

    result = ai._call_vision_style_descriptions([document])

    assert result['observations'] == [] and document['text'] == 'Authorized payment rows only'


def test_runtime_vision_connectivity_gate_cannot_succeed_via_ocr(monkeypatch):
    monkeypatch.setattr(ai.shutil, 'which', lambda command: '/bin/' + command)
    monkeypatch.setattr(ai.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='chi_sim\neng'))
    monkeypatch.setattr(allocation_service, '_ai_config', lambda: {'api_key': 'test', 'model': 'text'})
    monkeypatch.setattr(allocation_service, '_call_chat_completions', lambda *args, **kwargs: '{"health_check":true}')
    monkeypatch.setattr(ai, '_call_vision_batch', lambda images: {'ok': False, 'warning': 'Vision unavailable'})
    monkeypatch.setattr(ai, '_ocr_embedded_image', lambda image: pytest.fail('OCR hid failed vision connectivity'))

    with pytest.raises(RuntimeError, match='Vision unavailable'):
        ai.verify_material_ai_runtime(check_connection=True)
