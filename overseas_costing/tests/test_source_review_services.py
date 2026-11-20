"""统一资料审核的来源清单和确定性提取测试。"""

from decimal import Decimal
from copy import deepcopy
import json

import pytest

from overseas_costing.services.source_review_extract_service import (
    allocate_gross_weight,
    build_system_approval_proposals,
)
from overseas_costing.services.source_review_manifest_service import (
    prepare_source_manifest,
    source_progress_manifest,
)


def _legacy_logistics_rows(*, canonical=False, status='COMPLETED'):
    from overseas_costing.services import dingtalk_approval_service, packing_snapshot_service

    body = [{'rowNumber': row_id, 'rowValue': [
        {'label': '物料编码', 'value': 'SKU-1'}, {'label': '物料名称', 'value': '历史品名'},
        {'label': '规格型号', 'value': '历史规格'}, {'label': '数量Cantidad', 'value': quantity},
        {'label': '单位Unidad', 'value': 'PIEZA'}]} for row_id, quantity in [('ROW-A', '3'), ('ROW-B', '8')]]
    approval = dingtalk_approval_service._approval({
        'corpId': 'CORP', 'processInstanceId': 'LOG', 'status': status, 'result': 'agree',
        'formComponentValues': [{'name': '货物信息Bienes', 'componentType': 'TableField',
                                'value': json.dumps(body, ensure_ascii=False)}],
    }, [])
    source = packing_snapshot_service._list_approval_body_ai_sources('B1', detail={
        'ok': True, 'main_approval': approval, 'linked_purchase_approvals': [],
    })[0]
    items = []
    for index, row_id in enumerate(('ROW-A', 'ROW-B'), 1):
        meta = {'source': 'dingtalk_oa_logistics_form', 'instance_id': 'LOG',
                'effective_logistics_source': {'corp_id': 'CORP', 'instance_id': 'LOG'}}
        if canonical:
            meta['dingtalk_goods_row_no'] = row_id
        items.append({'name': 'I' + str(index), 'material_code': 'SKU-1', 'product_name': '主档品名',
            'spec_model': '主档规格', 'quantity': 1000 * index, 'actual_shipped_qty': 0,
            'actual_shipped_qty_mode': None, 'unit': '个', 'shipped_uom': '个',
            'unit_price': 0, 'goods_value': 0, 'extra_json': meta,
            'raw_excel_json': {} if canonical else {'_dingtalk_row_number': row_id}})
    return items, source


@pytest.mark.parametrize('canonical,status', [(False, 'RUNNING'), (True, 'COMPLETED')])
def test_legacy_logistics_detail_to_preview_uses_owned_rows_without_borrowing_purchase_quantities(monkeypatch, canonical, status):
    from types import SimpleNamespace
    from overseas_costing.services import material_ai_fill_service as ai
    from overseas_costing.services.material_ai_selection_writer import write_rows
    from overseas_costing.tests.test_source_goods_value_adoption import review, project_defaults
    from overseas_costing.tests.test_freight_lines import setup_cost

    stored_items, source = _legacy_logistics_rows(canonical=canonical, status=status)

    def get_all(doctype, *, fields, **kwargs):
        assert doctype == 'Overseas Cost Item'
        return [{field: deepcopy(row.get(field)) for field in fields} for row in stored_items]

    with monkeypatch.context() as context:
        context.setattr(ai, 'frappe', SimpleNamespace(get_all=get_all))
        items = ai.FrappeMaterialAIFillRepository().get_items('B1', 'V1')
    before = deepcopy((items, source))
    catalog = review(items, source)
    projected = project_defaults(items, catalog)
    assert [float(r['actual_shipped_qty']) for r in projected['rows']] == [3, 8]
    assert [r['quantity'] for r in projected['rows']] == [1000, 2000]
    assert all(r['spec_model'] == '主档规格' and r['product_name'] == '主档品名' for r in projected['rows'])
    assert all(r['actual_shipped_qty_mode'] == 'EXPLICIT_SOURCE' for r in projected['rows'])
    assert (items, source) == before

    store, ledger, batch, version, original, *_ = setup_cost()
    for index, row in enumerate(items):
        values = {**row, 'batch': batch['name'], 'version': version['name']}
        if index == 0:
            values['name'] = original['name']
            ledger.put('item', original['name'], values)
        else:
            ledger.create('item', values)
    saved_items = ledger.rows('item', batch=batch['name'], version=version['name'])
    projected = project_defaults(saved_items, review(saved_items, source))
    write_rows(store, ledger, {**projected, 'batch': batch['name'], 'version': version['name'],
        'id': 'P', 'revision': 'R', 'run_id': 'RUN', 'source_context': {}, 'sources': []}, {})
    saved = ledger.rows('item', batch=batch['name'], version=version['name'])
    assert sorted(float(row['actual_shipped_qty']) for row in saved) == [3, 8]
    assert all(row['product_name'] == '主档品名' and row['spec_model'] == '主档规格' for row in saved)


def test_repository_legacy_raw_stays_internal_to_ai_messages_and_public_payloads(monkeypatch):
    from types import SimpleNamespace
    from overseas_costing.services import material_ai_fill_service as ai
    from overseas_costing.services.material_ai_selection_service import public_preview
    from overseas_costing.services.material_input_service import GRID_FIELDS

    row = {'name': 'I1', 'material_code': 'SKU-1', 'product_name': '物料', 'spec_model': '型号',
        'raw_excel_json': json.dumps({'_dingtalk_row_number': 'PRIVATE-RAW-ROW',
            'image_url': 'https://example.invalid/private-image.png',
            'accessToken': 'PRIVATE-ACCESS-TOKEN', 'arbitrary_secret': 'PRIVATE-SECRET'})}
    with monkeypatch.context() as context:
        context.setattr(ai, 'frappe', SimpleNamespace(get_all=lambda doctype, *, fields, **kwargs:
            [{field: row.get(field) for field in fields}]))
        items = ai.FrappeMaterialAIFillRepository().get_items('B1', 'V1')
    assert items[0]['raw_excel_json'] == row['raw_excel_json']
    assert 'raw_excel_json' not in GRID_FIELDS
    for messages in (ai.build_ai_messages(items, []), ai.build_source_review_messages(items, [])):
        payload = json.dumps(messages, ensure_ascii=False)
        assert 'raw_excel_json' not in payload and 'PRIVATE-' not in payload
        assert 'private-image.png' not in payload and 'arbitrary_secret' not in payload
    for public in (public_preview({'rows': items}), ai._public_ai_payload({'draft': {'rows': items}})):
        payload = json.dumps(public, ensure_ascii=False)
        assert 'raw_excel_json' not in payload and 'PRIVATE-' not in payload
        assert 'private-image.png' not in payload and 'arbitrary_secret' not in payload
    assert items[0]['raw_excel_json'] == row['raw_excel_json']
    assert 'raw_excel_json' not in ai._fingerprint_item(items[0])


def test_heterogeneous_logistics_table_preserves_owned_rows_through_repository_and_preview(monkeypatch):
    from types import SimpleNamespace
    from overseas_costing.services import dingtalk_approval_service, packing_snapshot_service, material_ai_fill_service as ai
    from overseas_costing.services.source_review_extract_service import match_approval_goods_rows
    from overseas_costing.tests.test_source_goods_value_adoption import review, project_defaults

    targets = dict(zip((18, 19, 172, 173, 174, 175, 181, 182), (110000, 5000, 120, 40, 8, 8, 25, 15)))
    labels = ('物料编码 Código de material', '物料名称（中文）Nombre del material (chino)',
        '物料名称（西语）Nombre del material (español)', '规格型号Especificación / Modelo',
        '数量Cantidad', '单位Unidad', '收件人Destinatario')
    stored_items, _ = _legacy_logistics_rows()
    seed = stored_items[0]; stored_items = []; body = []
    for position in range(1, 185):
        code = 'SKU-BAG' if position in (17, 18, 19) else 'SKU-KG' if 172 <= position <= 175 else 'SKU-ROLL'
        unit = 'kg' if 172 <= position <= 175 else '卷' if position in (181, 182) else '个'
        values = (code, '历史品名', 'Nombre', '历史规格', str(targets.get(position, 125000 if position == 17 else 1)), unit, '')
        row = {'rowNumber': f'ROW-{position}', 'rowValue': [{'label': label, 'value': value}
            for label, value in zip(labels, values) if not (20 <= position <= 26 and label == labels[5])
            and not (position == 27 and label == labels[3])]}
        body.append(row)
        if position in targets:
            item = deepcopy(seed)
            item.update(name=f'I-{position}', material_code=code, quantity=position * 1000,
                unit=unit, shipped_uom=unit, raw_excel_json={'_dingtalk_row_number': row['rowNumber']})
            stored_items.append(item)
    assert sum(len(row['rowValue']) == 7 for row in body) == 176
    assert sum(labels[5] not in [c['label'] for c in row['rowValue']] for row in body) == 7
    assert sum(labels[3] not in [c['label'] for c in row['rowValue']] for row in body) == 1
    approval = dingtalk_approval_service._approval({'corpId': 'CORP', 'processInstanceId': 'LOG',
        'status': 'COMPLETED', 'result': 'agree', 'formComponentValues': [{'name': '货物信息Bienes',
            'componentType': 'TableField', 'value': json.dumps(body, ensure_ascii=False)}]}, [])
    source = packing_snapshot_service._list_approval_body_ai_sources('B1', detail={
        'ok': True, 'main_approval': approval, 'linked_purchase_approvals': []})[0]
    assert len(source['form_fields']['货物信息Bienes']) == 184
    with monkeypatch.context() as context:
        context.setattr(ai, 'frappe', SimpleNamespace(get_all=lambda doctype, *, fields, **kwargs:
            [{field: deepcopy(row.get(field)) for field in fields} for row in stored_items]))
        items = ai.FrappeMaterialAIFillRepository().get_items('B1', 'V1')
    matches = match_approval_goods_rows(items, source)
    assert matches[16][-2] is None
    assert sum(item is not None for *_, item, ambiguous in matches) == 8
    projected = project_defaults(items, review(items, source))
    assert [Decimal(str(row['actual_shipped_qty'])) for row in projected['rows']] == list(targets.values())
    assert [row['quantity'] for row in projected['rows']] == [position * 1000 for position in targets]
    assert all(row['product_name'] == '主档品名' and row['spec_model'] == '主档规格' for row in projected['rows'])
    assert all(row['actual_shipped_qty_mode'] == 'EXPLICIT_SOURCE' for row in projected['rows'])
    assert {c['item_name'] for c in projected['changes']} <= {row['name'] for row in items}


@pytest.mark.parametrize('mismatch', [
    'corp', 'missing_corp', 'target_corp', 'instance', 'target_instance_conflict', 'code',
    'source_duplicate', 'source_duplicate_other_code', 'target_duplicate',
    'target_duplicate_other_code', 'target_locator_conflict', 'purchase', 'missing_source_id',
])
def test_legacy_logistics_row_bridge_rejects_wrong_or_ambiguous_ownership(mismatch):
    from overseas_costing.services.source_review_extract_service import match_approval_goods_rows

    items, source = _legacy_logistics_rows()
    if mismatch == 'corp': source['corp_id'] = 'OTHER'
    elif mismatch == 'missing_corp': source.pop('corp_id')
    elif mismatch == 'target_corp':
        for item in items: item['extra_json']['effective_logistics_source']['corp_id'] = 'OTHER'
    elif mismatch == 'instance': source['process_instance_id'] = 'OTHER'
    elif mismatch == 'target_instance_conflict':
        for item in items: item['dingtalk_instance_id'] = 'OTHER'
    elif mismatch == 'code':
        for row in source['form_fields']['货物信息Bienes']: row['rowValue'][0]['value'] = 'OTHER'
    elif mismatch in {'source_duplicate', 'source_duplicate_other_code'}:
        source['form_fields']['货物信息Bienes'][1]['rowNumber'] = 'ROW-A'
        if mismatch == 'source_duplicate_other_code':
            source['form_fields']['货物信息Bienes'][1]['rowValue'][0]['value'] = 'OTHER'
    elif mismatch in {'target_duplicate', 'target_duplicate_other_code'}:
        items[1]['raw_excel_json']['_dingtalk_row_number'] = 'ROW-A'
        if mismatch == 'target_duplicate_other_code': items[1]['material_code'] = 'OTHER'
    elif mismatch == 'target_locator_conflict':
        for item in items: item['extra_json']['dingtalk_goods_row_no'] = 'OTHER-ROW'
    elif mismatch == 'purchase': source['approval_role'] = 'purchase'
    else:
        for row in source['form_fields']['货物信息Bienes']: row.pop('rowNumber')
    matches = match_approval_goods_rows(items, source)
    assert all(resolved is None for *_, resolved, ambiguous in matches)


def test_manual_actual_zero_is_preserved_even_when_logistics_row_identity_is_exact():
    from overseas_costing.tests.test_source_goods_value_adoption import review, project_defaults

    items, source = _legacy_logistics_rows()
    items = items[:1]
    items[0].update(actual_shipped_qty_mode='MANUAL_CONFIRMED', product_name='历史品名', spec_model='历史规格')
    source['form_fields']['货物信息Bienes'] = source['form_fields']['货物信息Bienes'][:1]
    projected = project_defaults(items, review(items, source))
    assert all(row['actual_shipped_qty'] == 0 for row in projected['rows'])
    assert not [c for c in projected['changes'] if c['fieldname'] == 'actual_shipped_qty']


def test_another_same_code_logistics_row_cannot_reuse_an_existing_imported_locator():
    from overseas_costing.services.source_review_extract_service import match_approval_goods_rows

    items, source = _legacy_logistics_rows()
    extra = deepcopy(source['form_fields']['货物信息Bienes'][0])
    extra['rowNumber'] = 'ROW-C'
    extra['rowValue'][3]['value'] = '125000'
    source['form_fields']['货物信息Bienes'].append(extra)
    matched = match_approval_goods_rows(items, source)
    assert [item['name'] if item else None for *_, item, ambiguous in matched] == ['I1', 'I2', None]


def test_existing_explicit_and_context_free_matching_keep_their_original_rules():
    from overseas_costing.services.source_review_extract_service import match_approval_goods_rows

    for explicit in (False, True):
        items, source = _legacy_logistics_rows()
        items = items[:1]
        items[0].update(product_name='历史品名', spec_model='历史规格')
        source['form_fields']['货物信息Bienes'] = source['form_fields']['货物信息Bienes'][:1]
        if explicit:
            items[0].update(actual_shipped_qty=3, actual_shipped_qty_mode='EXPLICIT_SOURCE')
            items[0]['extra_json']['effective_logistics_source'].pop('corp_id')
        else:
            source.pop('corp_id')
        matches = match_approval_goods_rows(items, source)
        assert matches[0][-2] == items[0] and matches[0][-1] is False


def test_source_manifest_uses_stable_public_ids_and_keeps_approval_bodies_locked() -> None:
    raw = [
        {
            "source_kind": "approval_form",
            "source_id": "approval:PROC-1:form",
            "logical_source_id": "approval:PROC-1:form",
            "source_label": "国际物流审批正文",
            "approval_no": "LOG-001",
            "form_fields": {"重量": "4200"},
        },
        {
            "source_kind": "approval_attachment",
            "source_id": "ATT-LOCAL-NAME",
            "logical_source_id": "oa:PROC-1:FILE-9",
            "source_label": "packing.xlsx",
            "file_name": "packing.xlsx",
            "sheet_name": "发货明细",
            "source_hash": "hash-v1",
        },
    ]

    manifest = prepare_source_manifest(raw, selected_source_ids=[])

    approval, sheet = manifest
    assert approval["source_id"] == "approval:PROC-1:form"
    assert approval["locked"] is True
    assert approval["selected"] is True
    assert sheet["parent_source_id"] == "oa:PROC-1:FILE-9"
    assert sheet["source_id"].startswith("oa:PROC-1:FILE-9:sheet:")
    assert "ATT-LOCAL-NAME" not in sheet["source_id"]
    assert sheet["resolver_source_id"] == "ATT-LOCAL-NAME"
    assert sheet["selected"] is False


def test_source_manifest_rejects_unknown_or_unavailable_selection() -> None:
    raw = [{"source_kind": "manual_attachment", "source_id": "ATT-1", "file_name": "a.xlsx"}]

    with pytest.raises(ValueError, match="不属于当前批次"):
        prepare_source_manifest(raw, selected_source_ids=["ATT-OTHER"])

    with pytest.raises(ValueError, match="不可选"):
        prepare_source_manifest(
            [{**raw[0], "excluded": True, "exclude_reason": "审计专用"}],
            selected_source_ids=["ATT-1"],
        )


def test_archived_dingtalk_attachment_is_selectable_before_local_download() -> None:
    manifest = prepare_source_manifest(
        [
            {
                "source_kind": "approval_attachment",
                "source_id": "oa:PROC-1:FILE-1",
                "source_label": "装箱单.xlsx",
                "file_name": "装箱单.xlsx",
                "available": False,
                "can_download": True,
                "download_required": True,
            }
        ]
    )

    assert manifest[0]["selected"] is True
    assert manifest[0]["selectable"] is True
    assert manifest[0]["parse_method"] == "SYSTEM_EXCEL"


def test_unavailable_selection_reports_document_identity_and_reason():
    from overseas_costing.services.material_ai_source_dependencies import SourceEligibilityError
    source = {'source_id':'opaque-id', 'source_kind':'approval_attachment', 'approval_no':'LOG-1',
              'source_label':'装箱单.xlsx', 'excluded':True, 'exclude_reason':'审计专用附件，不参与资料分析。'}
    with pytest.raises(SourceEligibilityError) as caught:
        prepare_source_manifest([source], selected_source_ids=['opaque-id'])
    assert caught.value.source_id == 'opaque-id'
    assert caught.value.approval_no == 'LOG-1'
    assert '装箱单.xlsx' in str(caught.value) and '审计专用' in str(caught.value)


def test_progress_manifest_exposes_only_safe_source_metadata() -> None:
    manifest = prepare_source_manifest(
        [
            {
                "source_kind": "approval_comment",
                "source_id": "COMMENT-1",
                "source_label": "评论",
                "actor_name": "张三",
                "occurred_at": "2026-09-01 10:00:00",
                "comment_text": "不应返回浏览器",
                "file_url": "/private/files/secret.pdf",
            }
        ]
    )

    progress = source_progress_manifest(manifest)

    assert progress[0]["parse_method"] == "AI_TEXT"
    assert progress[0]["read_status"] == "NO_RESULT"
    assert progress[0]["actor_name"] == "张三"
    assert progress[0]["occurred_at"] == "2026-09-01 10:00:00"
    assert "comment_text" not in progress[0]
    assert "secret.pdf" not in str(progress[0])


def test_progress_manifest_preserves_partial_read_status() -> None:
    manifest = prepare_source_manifest(
        [{"source_kind": "manual_attachment", "source_id": "ATT-1", "file_name": "packing.xlsx"}]
    )
    manifest[0]["read_status"] = "PARTIAL"

    progress = source_progress_manifest(manifest)

    assert progress[0]["read_status"] == "PARTIAL"


def test_progress_manifest_exposes_safe_skipped_evidence_metadata() -> None:
    manifest = prepare_source_manifest(
        [{"source_kind": "approval_attachment", "source_id": "ATT-1", "file_name": "packing.xlsx"}]
    )
    manifest[0].update(
        read_status="SKIPPED",
        skip_reason_code="FILE_NOT_FOUND",
        skip_reason_text="资料文件不存在。",
        elapsed_ms=37,
        error="<html>Internal Server Error /private/files/secret.xlsx</html>",
    )

    progress = source_progress_manifest(manifest)

    assert progress[0]["status"] == "SKIPPED"
    assert progress[0]["read_status"] == "SKIPPED"
    assert progress[0]["evidence_id"] == "ATT-1"
    assert progress[0]["evidence_kind"] == "attachment"
    assert progress[0]["skip_reason_code"] == "FILE_NOT_FOUND"
    assert progress[0]["skip_reason_text"] == "资料文件不存在。"
    assert progress[0]["elapsed_ms"] == 37
    assert progress[0]["detail"] == "资料文件不存在。已跳过，继续读取下一资料。"
    assert "html" not in str(progress[0]).lower()
    assert "/private/" not in str(progress[0])


def test_allocate_gross_weight_uses_decimal_and_last_row_absorbs_rounding() -> None:
    result = allocate_gross_weight(
        Decimal("4200"),
        [Decimal("1494"), Decimal("990"), Decimal("396"), Decimal("396"), Decimal("216")],
    )

    assert result == [
        Decimal("1796.907216"),
        Decimal("1190.721649"),
        Decimal("476.288660"),
        Decimal("476.288660"),
        Decimal("259.793815"),
    ]
    assert sum(result) == Decimal("4200.000000")


def test_logistics_approval_is_system_read_and_kg_quantity_becomes_net_weight() -> None:
    items = [
        {"name": "I1", "material_code": "SKU-1", "actual_shipped_qty": None, "net_weight_kg": None},
        {"name": "I2", "material_code": "SKU-2", "actual_shipped_qty": None, "net_weight_kg": None},
    ]
    source = {
        "source_kind": "approval_form",
        "source_id": "approval:PROC-1:form",
        "source_label": "国际物流审批正文",
        "process_instance_id": "PROC-1",
        "approval_no": "LOG-001",
        "approval_role": "international_logistics",
        "form_fields": {
            "货物信息": [
                {"物料编码": "SKU-1", "数量": "1494", "单位": "KG"},
                {"物料编码": "SKU-2", "数量": "990", "单位": "kg"},
            ],
            "重量Peso（KG）": "4,200 kg",
        },
    }

    proposals = build_system_approval_proposals(items, source, transport_mode="SEA")

    rows = [row for row in proposals if row["proposal_type"] == "item_update"]
    assert [row["payload"]["fields"]["actual_shipped_qty"] for row in rows] == ["1494", "990"]
    assert [row["payload"]["fields"]["net_weight_kg"] for row in rows] == ["1494", "990"]
    assert [row["payload"]["fields"]["gross_weight_kg"] for row in rows] == [
        "2526.086957",
        "1673.913043",
    ]
    assert all(row["result_origin"] == "SYSTEM" for row in rows)
    assert all(row["default_selected"] is True for row in rows)


def test_purchase_approval_is_readonly_matching_fact_and_creates_no_updates() -> None:
    proposals = build_system_approval_proposals(
        [{"name": "I1", "material_code": "SKU-1", "quantity": 10}],
        {
            "source_kind": "approval_form",
            "source_id": "approval:BUY-1:form",
            "approval_role": "purchase",
            "form_fields": {"采购明细": [{"物料编码": "SKU-1", "数量": 20}]},
        },
    )

    assert proposals == []


def test_non_kg_shipping_quantity_never_becomes_net_weight() -> None:
    proposals = build_system_approval_proposals(
        [{"name": "I1", "material_code": "SKU-1"}],
        {
            "source_kind": "approval_form",
            "source_id": "approval:LOG-1:form",
            "approval_role": "international_logistics",
            "form_fields": {
                "货物信息": [{"物料编码": "SKU-1", "数量": 12, "单位": "箱"}],
                "重量Peso（KG）": 4200,
            },
        },
    )

    fields = proposals[0]["payload"]["fields"]
    assert fields["actual_shipped_qty"] == "12"
    assert fields["shipped_uom"] == "箱"
    assert "net_weight_kg" not in fields
    assert "gross_weight_kg" not in fields


def test_total_gross_is_not_allocated_when_any_logistics_row_is_unmatched() -> None:
    proposals = build_system_approval_proposals(
        [{"name": "I1", "material_code": "SKU-1"}],
        {
            "source_kind": "approval_form",
            "source_id": "approval:LOG-1:form",
            "approval_role": "international_logistics",
            "form_fields": {
                "货物信息": [
                    {"物料编码": "SKU-1", "数量": 60, "单位": "KG"},
                    {"物料编码": "SKU-MISSING", "数量": 40, "单位": "KG"},
                ],
                "重量Peso（KG）": 120,
            },
        },
    )

    assert len(proposals) == 1
    fields = proposals[0]["payload"]["fields"]
    assert fields["net_weight_kg"] == "60"
    assert "gross_weight_kg" not in fields


def test_snvq1vekp5_system_approval_values_and_gross_allocation_are_exact() -> None:
    quantities = [1494, 990, 396, 396, 216]
    items = [
        {"name": f"I{index}", "material_code": f"SKU-{index}"}
        for index in range(1, 6)
    ]
    source = {
        "source_kind": "approval_form",
        "source_id": "approval:SNVQ:form",
        "approval_role": "international_logistics",
        "form_fields": {
            "货物信息": [
                {"物料编码": f"SKU-{index}", "数量": quantity, "单位": "KG"}
                for index, quantity in enumerate(quantities, start=1)
            ],
            "重量Peso（KG）": 4200,
        },
    }

    proposals = build_system_approval_proposals(items, source)
    fields = [row["payload"]["fields"] for row in proposals]

    assert sum(Decimal(row["net_weight_kg"]) for row in fields) == Decimal("3492")
    assert [row["gross_weight_kg"] for row in fields] == [
        "1796.907216",
        "1190.721649",
        "476.288660",
        "476.288660",
        "259.793815",
    ]
    assert sum(Decimal(row["gross_weight_kg"]) for row in fields) == Decimal("4200.000000")
