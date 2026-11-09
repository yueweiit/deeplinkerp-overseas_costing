"""Same-row purchase totals must survive source review, including historical rows."""
from copy import deepcopy
from decimal import Decimal

import pytest

from overseas_costing.services import material_ai_fill_service as ai
from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services.material_input_service import present_material_row
from overseas_costing.services.shipment_cost_service import shipment_value
from overseas_costing.services.source_review_extract_service import build_system_approval_proposals


def evidence(amounts=(800, 450, 440, 360, 1100, 1120), quantities=(4, 3, 2, 2, 5, 4)):
    goods = [{"物料编码 Código de material": f"SKU-{i}", "物料名称": "色粉",
              "数量Cantidad": str(q), "单位Unidad": "kg", "货值Valor de mercancía": str(v)}
             for i, (q, v) in enumerate(zip(quantities, amounts), 1)]
    source = {"source_id": "LOG", "source_kind": "approval_form", "available": True,
              "approval_role": "international_logistics", "form_fields": {"货物信息Bienes": goods}}
    items = [{"name": f"I{i}", "material_code": f"SKU-{i}", "product_name": "色粉",
              "quantity": str(q), "unit": "kg", "actual_shipped_qty": str(q), "shipped_uom": "kg",
              "goods_value": 0, "unit_price": 0} for i, q in enumerate(quantities, 1)]
    return items, source


def review(items, source, *, proposals=None):
    document = {"document_id": "DOC", "form_fields": source["form_fields"],
                "source_ref": {"source_id": source["source_id"], "approval_role": source["approval_role"]}}
    proposals = build_system_approval_proposals(items, source) if proposals is None else deepcopy(proposals)
    for proposal in proposals:
        for ref in proposal["source_refs"]:
            ref["document_id"] = "DOC"
    normalized = ai.normalize_source_review_proposals(proposals, items, [document],
        trusted_system_proposal_ids={p["proposal_id"] for p in proposals if p.get("result_origin") == "SYSTEM"})
    return rows.catalog(items, normalized, [], {}, run_id="RUN", sources=[source])


def project_defaults(items, catalog):
    choices = {f"{c['item_name']}:{c['fieldname']}": c['candidate_id']
               for c in catalog['field_candidates'] if c.get('default_selected')}
    return rows.project(items, catalog, [], [], 'fill_missing', field_choices=choices)


def test_six_logistics_purchase_totals_reach_candidates_and_readonly_projection():
    items, source = evidence()
    before = deepcopy((items, source))
    catalog = review(items, source)
    values = [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']
    assert len(values) == 6
    assert all(c['default_selected'] for c in values)
    projected = project_defaults(items, catalog)
    assert sum(Decimal(str(shipment_value(r)['amount_rmb'])) for r in projected['rows']) == 4270
    assert [present_material_row(r)['adopted_price']['value'] for r in projected['rows']] == [
        '200.00', '150.00', '220.00', '180.00', '220.00', '280.00']
    assert (items, source) == before


def test_model_goods_value_has_same_evidence_path_as_deterministic_extraction():
    items, source = evidence((800,), (4,))
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': .99, 'source_refs': [{'field': '货物信息Bienes', 'row': 1}],
                'payload': {'fields': {'goods_value': '800'}}}
    catalog = review(items, source, proposals=[proposal])
    projected = project_defaults(items, catalog)
    assert shipment_value(projected['rows'][0])['amount_rmb'] == '800'
    assert present_material_row(projected['rows'][0])['adopted_price']['value'] == '200.00'


def test_purchase_total_never_divides_by_another_sources_shipping_quantity():
    items, source = evidence((2067,), (1700,))
    items[0].update(quantity=9999, actual_shipped_qty=1500, actual_shipped_qty_mode='MANUAL_CONFIRMED')
    catalog = review(items, source)
    projected = project_defaults(items, catalog)['rows'][0]
    assert projected['actual_shipped_qty'] == 1500
    assert Decimal(shipment_value(projected)['amount_rmb']) == Decimal(2067) * 1500 / 1700
    assert present_material_row(projected)['adopted_price']['value'] == '1.22'


def test_historical_placeholder_is_fillable_but_manual_value_is_preserved():
    items, source = evidence((800,), (4,))
    items[0]['extra_json'] = {'manual_shipment_valuation': {
        'amount_rmb': '900', 'quantity': '4', 'uom': 'kg', 'currency': 'RMB',
        'manual': True, 'confirmed': True,
    }}
    from overseas_costing.services.shipment_cost_service import shipment_input_fingerprint
    items[0]['extra_json']['manual_shipment_valuation']['input_fingerprint'] = shipment_input_fingerprint(items[0])
    projected = project_defaults(items, review(items, source))['rows'][0]
    assert shipment_value(projected)['amount_rmb'] == '900'
    assert not projected['extra_json'].get('adopted_purchase_value')


@pytest.mark.parametrize('amount', ['-1', 'NaN', 'Infinity', '--'])
def test_invalid_goods_values_do_not_become_zero_candidates(amount):
    items, source = evidence((amount,), (4,))
    catalog = review(items, source)
    assert not [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']


def test_dedicated_explicit_zero_is_not_a_database_placeholder():
    items, source = evidence((0,), (4,))
    projected = project_defaults(items, review(items, source))['rows'][0]
    assert shipment_value(projected)['amount_rmb'] == '0'
    assert present_material_row(projected)['adopted_price']['value'] == '0.00'


def test_source_unit_mismatch_never_invents_conversion():
    items, source = evidence((800,), (4,))
    items[0].update(unit='箱', shipped_uom='箱', actual_shipped_qty_mode='MANUAL_CONFIRMED')
    catalog = review(items, source)
    candidate = next(candidate for candidate in catalog['field_candidates'] if candidate['fieldname'] == 'goods_value')
    assert not candidate['default_selected']
    assert project_defaults(items, catalog)['can_apply']
    preview = rows.project(items, catalog, [], [], 'update_selected',
                           field_choices={'I1:goods_value': candidate['candidate_id']})
    projected = preview['rows'][0]
    assert shipment_value(projected)['amount_rmb'] is None
    assert not preview['can_apply']
    assert projected['goods_value'] == 0
    assert any(outcome['code'] == 'PURCHASE_VALUE_UOM_MISMATCH' for outcome in preview['field_results'])


def test_worker_keeps_monetary_proposals_when_merging_logistics_reconciliation(monkeypatch):
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest
    items, source = evidence()
    source.update(approval_no='LOG-OLD', source_label='历史国际物流审批')
    repo = _LifecycleRepository(status='QUEUED')
    repo.sources = [source]
    repo.get_items = lambda *args: deepcopy(items)
    repo.get_context = lambda *args: {'batch': 'B1', 'version': 'V1', 'transport_mode': 'SEA'}
    manifest = prepare_source_manifest(repo.sources)
    repo.run.update(proposal_version=1, source_manifest_json=manifest,
                    input_fingerprint=ai._source_review_fingerprint('B1', 'V1', items, manifest, '', context=repo.get_context()))
    monkeypatch.setattr(ai, '_call_source_review_ai', lambda *a, **kw: {'ok': True, 'proposals': []})
    result = ai.execute_material_ai_fill('RUN-1', repository=repo)
    assert result['status'] in {'READY', 'READY_WITH_WARNINGS'}, repo.run.get('error_message')
    catalog = rows.catalog(items, repo.run['candidates_json'], [], {}, run_id='RUN-1', sources=repo.sources)
    values = [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']
    assert len(values) == 6
    assert sum(Decimal(str(shipment_value(r)['amount_rmb']))
               for r in project_defaults(items, catalog)['rows']) == 4270
    assert repo.applied == []


def test_confirm_writer_and_reread_keep_purchase_basis_and_audit():
    from overseas_costing.tests.test_freight_lines import setup_cost
    from overseas_costing.services.material_ai_selection_writer import write_rows
    store, ledger, batch, version, current, *_ = setup_cost()
    items, source = evidence((2067,), (1700,))
    items[0].update(name=current['name'], actual_shipped_qty='1500', actual_shipped_qty_mode='MANUAL_CONFIRMED')
    ledger.put('item', current['name'], items[0])
    projection = project_defaults(items, review(items, source))
    preview = {**projection, 'batch': batch['name'], 'version': version['name'], 'id': 'P',
               'revision': 'R', 'run_id': 'RUN', 'source_context': {}, 'sources': []}
    write_rows(store, ledger, preview, {})
    reread = ledger.get('item', current['name'])
    assert Decimal(shipment_value(reread)['amount_rmb']) == Decimal(2067) * 1500 / 1700
    assert present_material_row(reread)['adopted_price']['value'] == '1.22'
    from overseas_costing.services.shipment_cost_service import object_json
    assert object_json(reread['extra_json'])['adopted_purchase_value']['source_refs'][0]['row'] == 1
    assert ledger.get('batch', batch['name'])['confirm_status'] == 'Pending'
    assert ledger.get('batch', batch['name'])['writeback_status'] == 'Not Started'


def test_currency_and_quantity_are_owned_by_the_selected_value_source():
    items, source = evidence((800,), (4,))
    source['form_fields']['货物信息Bienes'][0]['币种Moneda'] = 'USD'
    document = {'document_id': 'D', 'approval_role': 'international_logistics',
                'form_fields': source['form_fields'], 'source_ref': {'source_id': 'LOG'}}
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': 1, 'source_refs': [{'document_id': 'D', 'field': '货物信息Bienes', 'row': 1}],
                'payload': {'fields': {'goods_value': 800}}}
    normalized = ai.normalize_source_review_proposals([proposal], items, [document], fx_rates={'USD': '7'})
    catalog = rows.catalog(items, normalized, [], {}, run_id='R', sources=[source])
    projected = project_defaults(items, catalog)['rows'][0]
    assert shipment_value(projected)['amount_rmb'] == '5600'
    assert present_material_row(projected)['adopted_price']['value'] == '1400.00'
    assert projected.get('purchase_currency') in (None, '')  # raw columns are not fabricated


def test_unpaired_ai_amount_remains_visible_but_cannot_be_applied():
    items, source = evidence((800,), (4,))
    source['form_fields']['货物信息Bienes'][0].pop('数量Cantidad')
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': 1, 'source_refs': [{'field': '货物信息Bienes', 'row': 1}],
                'payload': {'fields': {'goods_value': 800}}}
    catalog = review(items, source, proposals=[proposal])
    candidate = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value')
    assert not candidate['can_apply']
    assert not candidate['default_selected']
    assert '数量' in candidate['resolution_reason']


def test_labelled_comment_uses_same_row_value_path():
    items, source = evidence((800,), (4,))
    items[0]['quantity'] = 9999
    source.update(source_kind='approval_comment', source_id='COMMENT')
    document = {'document_id': 'D', 'approval_role': 'international_logistics',
                'source_ref': {'source_id': 'COMMENT'},
                'text': '物料编码: SKU-1；数量: 4；单位: kg；货值: 800'}
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': 1, 'source_refs': [{'document_id': 'D'}],
                'payload': {'fields': {'goods_value': 800}}}
    normalized = ai.normalize_source_review_proposals([proposal], items, [document])
    catalog = rows.catalog(items, normalized, [], {}, run_id='R', sources=[source])
    assert shipment_value(project_defaults(items, catalog)['rows'][0])['amount_rmb'] == '800'


def test_structured_attachment_value_can_fill_lower_priority_when_payment_missing():
    items, source = evidence((800,), (4,))
    items[0]['quantity'] = 9999
    raw = source['form_fields']['货物信息Bienes'][0]
    raw['币种Moneda'] = 'RMB'
    document = {'document_id': 'D', 'approval_role': 'purchase', 'source_ref': {'source_id': 'PUR'},
                'structured_rows': [{'source_row': 7, 'raw_fields': raw}]}
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': 1, 'source_refs': [{'document_id': 'D', 'row': 7}],
                'payload': {'fields': {'goods_value': 800}}}
    normalized = ai.normalize_source_review_proposals([proposal], items, [document])
    sources = [{'source_id': 'PAY', 'approval_role': 'payment', 'source_kind': 'approval_form', 'read_status': 'FAILED'},
               {'source_id': 'PUR', 'approval_role': 'purchase', 'source_kind': 'approval_attachment'}]
    catalog = rows.catalog(items, normalized, [], {}, run_id='R', sources=sources)
    assert shipment_value(project_defaults(items, catalog)['rows'][0])['amount_rmb'] == '800'


def test_logistics_form_is_given_to_deepseek_for_semantic_review():
    items, source = evidence()
    _, document = ai._read_source(items, source)
    assert document['ai_eligible'] is True


def test_valid_total_only_workbook_can_derive_unit_price_without_inventing_amount():
    from overseas_costing.services.shipment_valuation_service import build_shipment_valuations
    item = {'name': 'I', 'stable_line_key': 'K', 'material_code': 'SKU',
            'actual_shipped_qty': 4, 'shipped_uom': 'kg', 'unit': 'kg'}
    result = build_shipment_valuations([item], {'material_rows': [{
        '_target_stable_line_key': 'K', 'source_row': 2, 'quantity': 4, 'unit': 'kg',
        'total_amount': 800, 'currency': 'RMB', 'currency_evidence': {'kind': 'explicit'},
    }]}, {'source_id': 'XLS'})
    assert result['valuations']['I']['unit_price'] == '200'
    assert result['valuations']['I']['amount_rmb'] == '800'
    assert not result['merged_amount_groups']


def test_existing_positive_purchase_price_is_not_replaced_by_default_total():
    items, source = evidence((800,), (4,))
    items[0].update(unit_price=250, purchase_currency='RMB', purchase_uom='kg')
    catalog = review(items, source)
    value = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value')
    assert value['can_apply']
    assert not value['default_selected']


def test_two_valid_sources_keep_lower_value_selectable_and_higher_default():
    items, logistics = evidence((800,), (4,))
    payment = deepcopy(logistics)
    payment.update(source_id='PAY', approval_role='payment')
    payment['form_fields']['货物信息Bienes'][0].update({'货值Valor de mercancía': '900', '币种': 'RMB'})
    documents, proposals = [], []
    for source in (payment, logistics):
        document = {'document_id': source['source_id'], 'approval_role': source['approval_role'],
                    'source_ref': {'source_id': source['source_id']}, 'form_fields': source['form_fields']}
        documents.append(document)
        proposed = build_system_approval_proposals(items, source)
        for proposal in proposed:
            for ref in proposal['source_refs']:
                ref['document_id'] = document['document_id']
        proposals.extend(proposed)
    normalized = ai.normalize_source_review_proposals(proposals, items, documents,
        trusted_system_proposal_ids={p['proposal_id'] for p in proposals})
    catalog = rows.catalog(items, normalized, [], {}, run_id='R', sources=[payment, logistics])
    candidates = [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']
    assert len(candidates) == 2 and all(c['can_apply'] for c in candidates)
    assert [c['suggested_value'] for c in candidates if c['default_selected']] == ['900']
    lower = next(c for c in candidates if c['suggested_value'] == '800')
    selected = rows.project(items, catalog, [], [], 'update_selected', field_choices={'I1:goods_value': lower['candidate_id']})
    assert shipment_value(selected['rows'][0])['amount_rmb'] == '800'


def test_worker_retains_ai_lower_priority_amount_beside_system_amount(monkeypatch):
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest
    items, logistics = evidence((800,), (4,))
    payment = deepcopy(logistics)
    payment.update(source_id='PAY', approval_role='payment')
    payment['form_fields']['货物信息Bienes'][0].update({'货值Valor de mercancía': '900', '币种': 'RMB'})
    repo = _LifecycleRepository(status='QUEUED')
    repo.sources = [payment, logistics]
    repo.get_items = lambda *args: deepcopy(items)
    repo.get_context = lambda *args: {'batch': 'B1', 'version': 'V1', 'transport_mode': 'SEA'}
    manifest = prepare_source_manifest(repo.sources)
    repo.run.update(proposal_version=1, source_manifest_json=manifest,
        input_fingerprint=ai._source_review_fingerprint('B1', 'V1', items, manifest, '', context=repo.get_context()))
    def semantic(_items, documents, **kwargs):
        document = next(d for d in documents if d['source_ref']['source_id'] == 'LOG')
        return {'proposals': [{'proposal_id': 'AI-LOWER', 'proposal_type': 'item_update',
            'target_item_name': 'I1', 'confidence': 1,
            'source_refs': [{'document_id': document['document_id'], 'field': '货物信息Bienes', 'row': 1}],
            'payload': {'fields': {'goods_value': 800}}}]}
    from overseas_costing.services import source_review_extract_service as extractor
    real_extract = extractor.build_system_approval_proposals
    monkeypatch.setattr(extractor, 'build_system_approval_proposals', lambda i, s, **kw:
        real_extract(i, s, **kw) if s.get('approval_role') == 'payment' else [])
    monkeypatch.setattr(ai, '_call_source_review_ai', semantic)
    result = ai.execute_material_ai_fill('RUN-1', repository=repo)
    assert result['status'] in {'READY', 'READY_WITH_WARNINGS'}, repo.run.get('error_message')
    catalog = rows.catalog(items, repo.run['candidates_json'], [], {}, run_id='RUN-1', sources=repo.sources)
    values = [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']
    assert {c['suggested_value'] for c in values} == {'800', '900'}


def test_purchase_fact_private_payload_is_not_exposed():
    assert '_purchase_value_fact' not in ai._public_ai_payload({'_purchase_value_fact': {'quantity': '4'}, 'ok': True})


def test_real_parsed_workbook_cells_preserve_total_and_reject_shared_total():
    from overseas_costing.services.packing_parse_service import parse_packing_grid
    from overseas_costing.services.purchase_value_evidence import from_documents
    grid = {'cells': [[{'raw_value': v} for v in row] for row in [
        ['物料编码', '数量', '单位', '货值', '币种'], ['SKU-1', 4, 'kg', 800, 'RMB']]],
        'sheet_name': 'Sheet1', 'merged_ranges': []}
    parsed = parse_packing_grid(grid)
    document = {'structured_rows': parsed['material_rows'], 'approval_role': 'international_logistics'}
    target = {'material_code': 'SKU-1'}
    ref = {'document_id': 'D', 'row': 2, 'sheet': 'Sheet1'}
    fact = from_documents({'goods_value': '800'}, target, [ref], {'D': document})
    assert fact and fact['quantity'] == '4'
    document['structured_rows'][0].setdefault('field_ranges', {})['total_amount'] = {'start_row': 2, 'end_row': 3}
    assert from_documents({'goods_value': '800'}, target, [ref], {'D': document}) is None


def test_approval_currency_at_form_level_overrides_rmb_default():
    items, source = evidence((800,), (4,))
    source['form_fields']['币种Moneda'] = 'USD'
    proposed = build_system_approval_proposals(items, source, fx_rates={'USD': 7})
    value = next(p for p in proposed if 'goods_value' in p['payload']['fields'])
    assert value['payload']['fields']['goods_value'] == '5600'


def test_duplicate_sku_with_different_units_is_not_mismatched_by_quantity():
    items, source = evidence((800,), (4,))
    items.append({**deepcopy(items[0]), 'name': 'I2', 'quantity': 10, 'actual_shipped_qty': 10,
                  'unit': '箱', 'shipped_uom': '箱'})
    source['form_fields']['货物信息Bienes'][0]['单位Unidad'] = '箱'
    proposed = build_system_approval_proposals(items, source)
    assert not any(p['target_item_name'] == 'I1' for p in proposed)


def test_explicit_total_choice_is_displayed_even_when_raw_price_is_preserved():
    items, source = evidence((800,), (4,))
    items[0].update(unit_price=250, purchase_currency='RMB', purchase_uom='kg')
    catalog = review(items, source)
    selected = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value')
    projected = rows.project(items, catalog, [], [], 'update_selected',
        field_choices={'I1:goods_value': selected['candidate_id']})['rows'][0]
    assert projected['unit_price'] == 250
    assert present_material_row(projected)['adopted_price']['value'] == '200.00'


def test_new_shipment_value_retires_prior_purchase_fact_in_preview_and_save():
    from overseas_costing.tests.test_freight_lines import setup_cost
    from overseas_costing.services.material_ai_selection_writer import write_rows
    from overseas_costing.services.shipment_cost_service import object_json
    store, ledger, batch, version, current, *_ = setup_cost()
    items, source = evidence((800,), (4,))
    items[0]['name'] = current['name']
    existing = project_defaults(items, review(items, source))['rows'][0]
    ledger.put('item', current['name'], existing)
    valuation = {'amount_rmb': '900', 'quantity': '4', 'uom': 'kg', 'currency': 'RMB',
                 'method': 'packing_row_total', 'source_refs': [{'source_id': 'NEW', 'row': 2}]}
    proposal = {'proposal_id': 'P', 'proposal_type': 'logistics_reconcile',
        'confidence': 1, 'default_selected': True, 'source_refs': valuation['source_refs'],
        'payload': {'rows': [{**items[0], '_review_origin': 'source',
                              'extra_json': {'shipment_valuation': valuation}}]}}
    catalog = rows.catalog([existing], [proposal], [], {}, run_id='R')
    selected = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'shipment_value_rmb')
    preview = rows.project([existing], catalog, [], [], 'update_selected',
        field_choices={current['name'] + ':shipment_value_rmb': selected['candidate_id']})
    assert shipment_value(preview['rows'][0])['amount_rmb'] == '900'
    preview.update(batch=batch['name'], version=version['name'], id='P', revision='R', run_id='R', source_context={}, sources=[])
    write_rows(store, ledger, preview, {})
    saved = ledger.get('item', current['name'])
    assert shipment_value(saved)['amount_rmb'] == '900'
    assert object_json(saved['extra_json'])['purchase_value_history'][-1]['amount_rmb'] == '800'


def test_clear_vision_transcript_uses_same_row_evidence_path():
    items, _ = evidence((800,), (4,))
    document = {'document_id': 'D', 'approval_role': 'international_logistics',
        'source_ref': {'source_id': 'IMAGE'}, 'vision_observations': [
            {'anchor': {'page': 1}, 'description': '物料编码: SKU-1；数量: 4；单位: kg；货值: 800'}]}
    proposal = {'proposal_id': 'AI', 'proposal_type': 'item_update', 'target_item_name': 'I1',
                'confidence': 1, 'source_refs': [{'document_id': 'D', 'page': 1}],
                'payload': {'fields': {'goods_value': 800}}}
    normalized = ai.normalize_source_review_proposals([proposal], items, [document])
    catalog = rows.catalog(items, normalized, [], {}, run_id='R')
    value = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value')
    assert value['can_apply']


def test_missing_primary_archive_does_not_erase_current_historical_targets():
    from overseas_costing.services.effective_logistics_source import project_ai_items
    items, _ = evidence((800,), (4,))
    assert project_ai_items(items, {'context': {'root_kind': 'expense', 'available': False}}) == items


def test_real_excel_reader_reaches_semantic_review_and_amount_confirmation(tmp_path, monkeypatch):
    import json
    from openpyxl import Workbook
    from overseas_costing.services import packing_source_service, attachment_parse_service, allocation_service
    from overseas_costing.services.packing_parse_service import parse_packing_grid
    from overseas_costing.utils.excel_workbook import read_packing_grid
    items, _ = evidence((800,), (4,))
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = '明细'
    worksheet.append(['物料编码', '数量', '单位', '货值', '币种'])
    worksheet.append(['SKU-1', 4, 'kg', 800, 'RMB'])
    path = tmp_path / 'goods.xlsx'
    workbook.save(path)
    workbook.close()
    grid = read_packing_grid(path, sheet_name='明细', require_exact_sheet=True)
    preview = parse_packing_grid(grid)
    monkeypatch.setattr(ai, '_ensure_local_attachment', lambda *_: {'file_name': 'goods.xlsx', 'file_url': str(path)})
    monkeypatch.setattr(attachment_parse_service, '_resolve_source_file_path', lambda **_: path)
    monkeypatch.setattr(packing_source_service, 'resolve_trusted_packing_source', lambda **_: {'preview': preview})
    source = {'source_id': 'X', 'source_kind': 'manual_attachment', 'sheet_name': '明细',
              'batch': 'B', 'approval_role': 'international_logistics'}
    _, document = ai._read_source(items, source)
    assert document['ai_eligible']
    document.update(document_id='D', approval_role='international_logistics')
    called = []
    monkeypatch.setattr(allocation_service, '_ai_config', lambda: {'api_key': 'test-only'})
    def completion(_config, messages, **_kwargs):
        called.append(messages)
        return json.dumps({'proposals': [{'proposal_id': 'AI', 'proposal_type': 'item_update',
            'target_item_name': 'I1', 'confidence': 1, 'payload': {'fields': {'goods_value': '800'}},
            'source_refs': [{'document_id': 'D', 'sheet': '明细', 'row': 2, 'cell': 'D2'}]}]})
    monkeypatch.setattr(allocation_service, '_call_chat_completions', completion)
    result = ai._call_source_review_ai(items, [document])
    assert called and result['ok']
    normalized = ai.normalize_source_review_proposals(result['proposals'], items, result['evidence_documents'])
    catalog = rows.catalog(items, normalized, [], {}, run_id='R', sources=[source])
    selected = next(c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value')
    projected = rows.project(items, catalog, [], [], 'update_selected', field_choices={'I1:goods_value': selected['candidate_id']})
    assert shipment_value(projected['rows'][0])['amount_rmb'] == '800'
