"""
中文用途：导入服务骨架测试。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from overseas_costing.services import attachment_parse_service
from overseas_costing.services.attachment_parse_service import (
    _build_tax_certificate_manual_resolution,
    _build_tax_certificate_reconciliation,
    build_packing_list_parse_task,
)
from overseas_costing.services.import_service import (
    _build_attachment_price_provenance,
    _build_packing_unmatched_item_values,
    _prepare_imported_item_values,
    _protect_existing_shipping_values,
    _build_source_document_manual_review,
    _diagnose_ambiguous_source_row,
    _diagnose_unmatched_source_row,
    apply_linked_purchase_expense_fillable_fields,
    backfill_oa_item_details_from_raw,
    confirm_oa_source_attachment_type,
    confirm_logistics_quote_candidate,
    delete_manual_document_attachment,
    _coerce_item_numeric_defaults,
    _ensure_supported_excel_path,
    _get_linked_purchase_approvals_from_extra,
    _index_items,
    _match_item,
    _values_equal_for_import,
    get_tax_certificate_parse_record,
    import_main_excel,
    import_purchase_expense_oa,
    list_oa_form_attachments,
    list_tax_certificate_parse_records,
    parse_packing_list_attachment,
    parse_manual_document_attachments,
    parse_oa_packing_list_attachments,
    parse_oa_source_attachments,
    preview_packing_list_attachment,
    preview_oa_source_attachment,
    preview_linked_purchase_expense_oa,
    preview_tax_certificate_pdf,
    preview_yuewei_excel_file,
    save_tax_certificate_parse_result,
    save_manual_logistics_quote,
    apply_packing_list_fillable_fields,
    download_oa_form_attachment,
)


def test_new_packing_item_has_stable_key_and_explicit_quantity_provenance(monkeypatch) -> None:
    from overseas_costing.services import import_service as service

    monkeypatch.setattr(service, "_filter_doctype_values", lambda _doctype, values, **_kwargs: values)
    values = _build_packing_unmatched_item_values(
        batch_doc_name="B1",
        version_name="V1",
        row_no=1,
        mapped_row={
            "material_code": "M1",
            "actual_shipped_qty": 9,
            "unit": "桶",
        },
        attachment_provenance={"source_file_name": "装箱计划.xlsx"},
    )

    assert values["stable_line_key"]
    assert values["actual_shipped_qty_mode"] == "EXPLICIT_SOURCE"
    assert values["purchase_uom"] == "桶"
    assert values["shipped_uom"] == "桶"
    assert values["cost_output_uom"] == "桶"


def test_imported_purchase_row_defaults_shipping_to_purchase_quantity() -> None:
    values = _prepare_imported_item_values(
        {"material_code": "M1", "quantity": 34, "unit": "桶", "unit_price": 25},
        source_revision="OA-REV-1",
        include_stable_key=True,
    )

    assert values["stable_line_key"]
    assert values["actual_shipped_qty_mode"] == "DEFAULT_PURCHASE"
    assert values["purchase_uom"] == "桶"
    assert values["unit_price_uom"] == "桶"
    assert values["shipped_uom"] == "桶"
    assert values["actual_shipped_qty_source_revision"] == "OA-REV-1"


def test_reimport_without_explicit_shipping_never_clears_confirmed_quantity() -> None:
    values = _protect_existing_shipping_values(
        {
            "actual_shipped_qty": 0,
            "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
            "actual_shipped_qty_source_revision": "",
            "shipped_uom": "件",
            "cost_output_uom": "件",
            "goods_value": 850,
        },
        source_actual_present=False,
    )

    assert values == {"goods_value": 850}


def test_unmatched_row_diagnosis_explains_missing_code() -> None:
    result = _diagnose_unmatched_source_row(
        {"product_name": "墨镜", "spec_model": "黑色", "unit_price": 12.5},
        source_index=2,
    )

    assert result["source_row_no"] == 3
    assert "没有物料编码" in result["reason"]
    assert "优先补物料编码" in result["suggestion"]


def test_ambiguous_row_diagnosis_lists_candidate_rows() -> None:
    result = _diagnose_ambiguous_source_row(
        {"material_code": "SKU001", "product_name": "镜片"},
        matched_by="material_code",
        candidates=[{"row_no": 4}, {"row_no": 8}],
        source_index=0,
    )

    assert result["source_row_no"] == 1
    assert result["candidate_row_nos"] == [4, 8]
    assert "第 4、8 行" in result["reason"]


def test_import_purchase_expense_oa_returns_preview_and_dingtalk_payload() -> None:
    result = import_purchase_expense_oa(
        batch_name="BATCH-001",
        source_instance_id="PROC-001",
        approval_no="OA-001",
        official_url="https://oa.dingtalk.com/example",
        detail_rows_json=(
            '[{"物品编码Código":"FL004104","物品名称Nombre del artículo":"包装袋",'
            '"单价Precio":0.049,"总金额Monto Total":49,"币种Moneda":"人民币RMB"}]'
        ),
    )

    assert result["ok"] is True
    assert result["mapped_preview_count"] == 1
    assert result["mapped_preview_items"][0]["material_code"] == "FL004104"
    assert result["dingtalk_payload"]["instance_id"] == "PROC-001"
    assert "purchase_currency" in result["writeback_targets"]


def test_import_purchase_expense_oa_rejects_client_rows_in_frappe(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(
        import_service,
        "_run_item_writeback",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("untrusted client rows must not be written")),
    )

    result = import_service.import_purchase_expense_oa(
        batch_name="BATCH-001",
        source_instance_id="PROC-REJECTED",
        detail_rows_json='[{"物品编码Código":"MAT-FAKE","单价Precio":1}]',
    )

    assert result["ok"] is False
    assert result["untrusted_source"] is True


def test_resolve_attachment_user_id_prefers_configured_active_user(monkeypatch) -> None:
    from overseas_costing.services.import_service import _resolve_attachment_user_id

    class Attachment:
        batch = "BATCH-001"

    monkeypatch.setenv("DINGTALK_ATTACHMENT_USER_ID", "ACTIVE-USER-001")

    assert _resolve_attachment_user_id(Attachment(), {"originator_userid": "FORM-CREATOR-001"}) == "ACTIVE-USER-001"


def test_confirm_logistics_quote_candidate_requires_frappe_environment() -> None:
    result = confirm_logistics_quote_candidate(batch_name="BATCH-001", candidate_index=0)

    assert result["ok"] is False
    assert result["dry_run"] is True


def test_confirm_logistics_quote_candidate_blocks_invalid_batch_before_writes(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda _name: "BATCH-DOC")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *_args: "VER-001")
    monkeypatch.setattr(
        import_service,
        "_invalid_batch_cost_write_response",
        lambda *_args, **_kwargs: {
            "ok": False,
            "invalid_business": True,
            "message": "已拒绝审批不能写入成本",
        },
    )
    monkeypatch.setattr(
        import_service,
        "_get_batch_trace_row",
        lambda *_args: (_ for _ in ()).throw(AssertionError("invalid batch must stop before writes")),
    )

    result = import_service.confirm_logistics_quote_candidate(
        batch_name="BATCH-001",
        candidate_index=0,
    )

    assert result["ok"] is False
    assert result["invalid_business"] is True


def test_save_manual_logistics_quote_requires_frappe_environment() -> None:
    result = save_manual_logistics_quote(batch_name="BATCH-001", amount=6160.615461)

    assert result["ok"] is False
    assert result["dry_run"] is True


def test_preview_oa_source_attachment_requires_frappe_environment() -> None:
    result = preview_oa_source_attachment("ATTACHMENT-001")

    assert result["ok"] is False
    assert result["dry_run"] is True


def test_preview_oa_source_attachment_caches_recognition_snapshot(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeAttachmentDoc:
        file_name = "PO2026072401.docx"
        file_url = "/private/files/PO2026072401.docx"
        batch = "BATCH-001"
        version = "VER-001"
        attachment_type = "Other"
        parse_status = "Queued"
        mapped_result_json = '{"parse_targets":["goods_value"]}'
        save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    attachment_doc = FakeAttachmentDoc()
    preview_calls = {"value": 0}

    class FakeDB:
        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(*args):
            assert args == ("Overseas Cost Attachment", "ATTACH-001")
            return attachment_doc

    def fake_preview(**_kwargs):
        preview_calls["value"] += 1
        return {
            "ok": True,
            "source_name": "PO2026072401.docx",
            "file_ext": "docx",
            "extraction_method": "word_docx",
            "classification": {"code": "purchase_order", "label": "采购订单"},
            "field_candidates": {"material_codes": ["AB123"]},
            "purchase_order": {"purchase_order_no": "PO2026072401", "line_items": []},
            "text_excerpt": "PURCHASE ORDER NO: PO2026072401",
            "text_length": 30,
            "can_write_purchase_price": False,
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(attachment_parse_service, "preview_source_document", fake_preview)

    first = preview_oa_source_attachment("ATTACH-001")
    second = preview_oa_source_attachment("ATTACH-001")

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["classification"]["code"] == "purchase_order"
    assert preview_calls["value"] == 1
    assert attachment_doc.parse_status == "Parsed"
    assert attachment_doc.save_count == 1


def test_excluded_approval_attachment_cannot_be_recognized_or_manually_classified(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeAttachmentDoc:
        file_name = "PO-rejected.xlsx"
        file_url = "/private/files/PO-rejected.xlsx"
        batch = "BATCH-001"
        version = "VER-001"
        attachment_type = "Other"
        parse_status = "Queued"
        parse_result_json = json.dumps({
            "approval_excluded": True,
            "cost_source_allowed": False,
            "exclusion_reason": "采购审批已拒绝",
        })
        mapped_result_json = "{}"

        def save(self, **_kwargs):
            raise AssertionError("audit-only attachment must not be modified")

    attachment_doc = FakeAttachmentDoc()

    class FakeFrappe:
        @staticmethod
        def get_doc(*args):
            assert args == ("Overseas Cost Attachment", "ATT-REJECTED")
            return attachment_doc

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        attachment_parse_service,
        "preview_source_document",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("audit-only attachment must not be parsed")),
    )

    preview = preview_oa_source_attachment("ATT-REJECTED")
    confirmed = confirm_oa_source_attachment_type(
        attachment_name="ATT-REJECTED",
        confirmed_type="purchase_price_document",
    )

    assert preview["ok"] is False
    assert preview["audit_only"] is True
    assert confirmed["ok"] is False
    assert confirmed["audit_only"] is True


def test_legacy_excluded_attachment_without_marker_uses_batch_trace_policy(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeAttachmentDoc:
        file_name = "legacy-rejected.xlsx"
        file_url = "/private/files/legacy-rejected.xlsx"
        batch = "BATCH-001"
        version = "VER-001"
        source_type = "OA"
        attachment_type = "Packing List"
        parse_status = "Parsed"
        parse_result_json = json.dumps({
            "process_instance_id": "PROC-REJECTED",
            "file_id": "FILE-OLD",
        })
        mapped_result_json = "{}"

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Batch"
            assert name == "BATCH-001"
            assert as_dict is True
            return {
                "source_instance_id": "PROC-MAIN",
                "source_approval_status": "COMPLETED",
                "extra_json": json.dumps({"linked_purchase_approvals": [
                    {
                        "source_instance_id": "PROC-REJECTED",
                        "approval_status": "REJECTED",
                    }
                ]}),
            }

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(*_args):
            return FakeAttachmentDoc()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        attachment_parse_service,
        "preview_source_document",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy excluded attachment must not be parsed")),
    )

    result = preview_oa_source_attachment("ATT-LEGACY-REJECTED")

    assert result["ok"] is False
    assert result["audit_only"] is True


def test_confirm_oa_source_attachment_type_requires_frappe_environment() -> None:
    result = confirm_oa_source_attachment_type(
        attachment_name="ATTACHMENT-001",
        confirmed_type="purchase_price_document",
    )

    assert result["ok"] is False
    assert result["dry_run"] is True


def test_build_source_document_manual_review_keeps_automatic_result_for_traceability() -> None:
    review = _build_source_document_manual_review(
        confirmed_type="customs_declaration",
        remark="核对后确认是报关单",
        automatic_classification={"code": "unclassified", "label": "待人工识别"},
    )

    assert review["confirmed_type_label"] == "报关资料"
    assert review["attachment_type"] == "Customs Declaration"
    assert review["parse_targets"] == ["pedimento_no", "line_items"]
    assert review["automatic_classification"]["code"] == "unclassified"


def test_build_attachment_price_provenance_uses_attachment_metadata(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            assert name_or_filters == "ATT-PRICE-001"
            assert fields == [
                "name", "batch", "source_type", "file_name", "file_url", "source_doc_no", "parse_result_json"
            ]
            assert as_dict is True
            return {
                "name": "ATT-PRICE-001",
                "batch": "BATCH-001",
                "source_type": "OA",
                "file_name": "5月指环扣双清-未关联采购单.xlsx",
                "file_url": "/private/files/5月指环扣双清-未关联采购单.xlsx",
                "source_doc_no": "202605270001",
                "parse_result_json": "{}",
            }

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    provenance = _build_attachment_price_provenance(
        attachment_name="ATT-PRICE-001",
        file_url="/private/files/fallback.xlsx",
    )

    assert provenance == {
        "source_type": "ATTACHMENT_PRICE",
        "source_file_name": "5月指环扣双清-未关联采购单.xlsx",
        "source_attachment_id": "ATT-PRICE-001",
        "source_doc_no": "202605270001",
        "parse_status": "SUCCESS",
    }


def test_build_attachment_price_provenance_rejects_audit_only_attachment(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "ATT-REJECTED",
                "file_name": "PO-rejected.xlsx",
                "parse_result_json": json.dumps({
                    "approval_excluded": True,
                    "cost_source_allowed": False,
                }),
            }

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    with pytest.raises(ValueError, match="仅供审计"):
        _build_attachment_price_provenance(attachment_name="ATT-REJECTED")


def test_preview_purchase_order_match_builds_price_rows_without_writing(monkeypatch) -> None:
    from overseas_costing.services import import_service

    captured = {}

    def fake_source_preview(_attachment_name):
        return {
            "ok": True,
            "attachment_name": "ATT-PO-001",
            "batch_name": "BATCH-PO",
            "version_name": "VER-PO",
            "source_name": "PO2026050901.pdf",
            "classification": {"code": "purchase_order", "label": "采购订单"},
            "purchase_order": {
                "purchase_order_no": "PO2026050901",
                "supplier": "HUAFON",
                "buyer": "YUEWEISA",
                "currency": "USD",
                "line_items": [
                    {
                        "material_code": "S890",
                        "product_name": "TPU",
                        "quantity": 1200,
                        "unit_price": 0.619,
                        "purchase_currency": "USD",
                        "goods_value": 742.8,
                    }
                ],
            },
        }

    def fake_writeback_preview(**kwargs):
        captured.update(kwargs)
        updates = kwargs["update_builder"](kwargs["mapped_rows"][0], {})
        assert updates == {"unit_price": 0.619, "purchase_currency": "USD", "goods_value": 742.8, "supplier": "HUAFON"}
        return {"version_name": "VER-PO", "matched_count": 1, "fillable_row_count": 1, "message": "预览完成"}

    monkeypatch.setattr(import_service, "preview_oa_source_attachment", fake_source_preview)
    monkeypatch.setattr(import_service, "_preview_item_writeback", fake_writeback_preview)

    result = import_service.preview_oa_purchase_order_match("ATT-PO-001")

    assert result["ok"] is True
    assert result["purchase_order"]["purchase_order_no"] == "PO2026050901"
    assert result["purchase_order"]["recognized_line_count"] == 1
    assert result["source_rows"][0]["material_code"] == "S890"
    assert result["source_rows"][0]["supplier"] == "HUAFON"
    assert captured["batch_name"] == "BATCH-PO"
    assert captured["trust_unique_material_code"] is True


def test_resolve_packing_list_conflict_adopts_attachment_for_current_item_only(monkeypatch) -> None:
    from overseas_costing.services import import_service

    audit_payloads = []
    updates = {}

    class FakeItemDoc:
        def __init__(self):
            self.name = "ITEM-001"
            self.actual_shipped_qty = 500
            self.source_type = "OA_LOGISTICS"
            self.source_file_name = ""
            self.source_attachment_id = ""
            self.source_doc_no = ""
            self.parse_status = "PENDING"

        def save(self, **_kwargs):
            return self

    class FakeAttachmentDoc:
        def __init__(self):
            self.name = "ATT-PACKING"
            self.mapped_result_json = "{}"

        def save(self, **_kwargs):
            return self

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    item_doc = FakeItemDoc()
    attachment_doc = FakeAttachmentDoc()

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Attachment" and name_or_filters == "ATT-PACKING":
                return {
                    "name": "ATT-PACKING",
                    "file_name": "装箱单.xlsx",
                    "file_url": "/private/files/装箱单.xlsx",
                    "source_doc_no": "OA-001",
                }
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_doc(*args):
            if args == ("Overseas Cost Item", "ITEM-001"):
                return item_doc
            if args == ("Overseas Cost Attachment", "ATT-PACKING"):
                return attachment_doc
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeAuditDoc(args[0])
            raise AssertionError(args)

    def fake_preview(**_kwargs):
        return {
            "ok": True,
            "batch_doc_name": "BATCH-DOC",
            "version_name": "VER-DOC",
            "writeback_preview": {
                "batch_doc_name": "BATCH-DOC",
                "version_name": "VER-DOC",
                "matched_rows": [
                    {
                        "target_item_name": "ITEM-001",
                        "target_row_no": 1,
                        "business_changes": [
                            {
                                "field_name": "actual_shipped_qty",
                                "field_label": "实际发货数量",
                                "old_value": 500,
                                "new_value": 600,
                                "status": "conflict",
                            }
                        ],
                    }
                ],
            },
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "preview_packing_list_attachment", fake_preview)
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *_args: None)

    result = import_service.resolve_packing_list_conflict_row(
        batch_name="BATCH-001",
        attachment_name="ATT-PACKING",
        target_item_name="ITEM-001",
        resolution_action="use_attachment",
        recalculate_after_writeback=False,
        trusted_server_payload=True,
    )

    assert result["ok"] is True
    assert result["resolution"]["action_label"] == "采用附件值"
    assert item_doc.actual_shipped_qty == 600
    assert item_doc.source_type == "PACKING_LIST"
    assert item_doc.source_file_name == "装箱单.xlsx"
    assert item_doc.source_attachment_id == "ATT-PACKING"
    assert updates[("Overseas Cost Batch", "BATCH-DOC", "status")] == "Dirty"
    assert "packing_conflict_resolutions" in attachment_doc.mapped_result_json
    assert any(row["field_name"] == "actual_shipped_qty" for row in audit_payloads)


def test_get_linked_purchase_approvals_from_oa_trace_extra_json() -> None:
    linked = _get_linked_purchase_approvals_from_extra(
        (
            '{"source":"excel","oa_logistics_trace":{"linked_purchase_approvals":['
            '{"approval_no":"202604300000000596348","source_instance_id":"PROC-PURCHASE-001"}'
            "]}}"
        )
    )

    assert linked[0]["approval_no"] == "202604300000000596348"
    assert linked[0]["source_instance_id"] == "PROC-PURCHASE-001"


def test_purchase_item_match_rejects_same_code_with_different_spec() -> None:
    indexes = _index_items(
        [
            {
                "name": "ITEM-001",
                "material_code": "YL00060",
                "product_name": "TPU原料 HF-1190A-8",
                "spec_model": "HF-1190A-8",
            }
        ]
    )

    matched_by, candidates = _match_item(
        {"material_code": "YL00060", "product_name": "原料TPU", "spec_model": "HF-1190A-1"},
        indexes,
    )

    assert matched_by == "material_code"
    assert candidates == []


def test_purchase_item_match_trusts_unique_material_code_when_enabled() -> None:
    indexes = _index_items(
        [
            {
                "name": "ITEM-001",
                "material_code": "YL00060",
                "product_name": "TPU原料 HF-1190A-8",
                "spec_model": "HF-1190A-8",
            }
        ]
    )

    matched_by, candidates = _match_item(
        {"material_code": "YL00060", "product_name": "原料TPU", "spec_model": "HF-1190A-1"},
        indexes,
        trust_unique_material_code=True,
    )

    assert matched_by == "material_code"
    assert candidates[0]["name"] == "ITEM-001"


def test_preview_linked_purchase_expense_oa_matches_without_writing(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "FSCU8486789":
                return {"name": "BATCH-DOC"} if as_dict else "BATCH-DOC"
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-DOC":
                if fields == ["current_version"]:
                    return {"current_version": "VER-DOC"}
                return {
                    "name": "BATCH-DOC",
                    "batch_no": "FSCU8486789",
                    "source_approval_no": "202606101808000475588",
                    "source_instance_id": "PROC-SEA",
                    "source_dingtalk_url": "",
                    "extra_json": (
                        '{"source":"dingtalk_oa_logistics","linked_purchase_approvals":['
                        '{"approval_no":"202604150041000081318","source_instance_id":"PROC-PURCHASE-001"}'
                        "]}"
                    ),
                }
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return [
                    {
                        "name": "ITEM-001",
                        "row_no": 1,
                        "material_code": "YL000097",
                        "product_name": "TPU原料 HF-8695AU",
                        "spec_model": "HF-8695AU",
                        "unit_price": 0.0,
                        "purchase_currency": "",
                        "goods_value": 0.0,
                    },
                    {
                        "name": "ITEM-002",
                        "row_no": 2,
                        "material_code": "YL000058",
                        "product_name": "副牌PC透明 LUXI",
                        "spec_model": "LUXI",
                        "unit_price": 2.1,
                        "purchase_currency": "人民币RMB",
                        "goods_value": 100,
                    },
                ]
            return []

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = preview_linked_purchase_expense_oa(
        batch_name="FSCU8486789",
        trusted_server_payload=True,
        purchase_summaries_json=(
            '[{"source_approval_no":"202604150041000081318","source_instance_id":"PROC-PURCHASE-001",'
            '"purchase_currency":"人民币RMB","mapped_preview_items":['
            '{"material_code":"YL000097","product_name":"TPU原料 HF-8695AU","spec_model":"HF-8695AU",'
            '"unit_price":2.9,"goods_value":29000,"purchase_currency":"人民币RMB","source_type":"PURCHASE_EXPENSE_OA"},'
            '{"material_code":"YL000058","product_name":"副牌PC透明 LUXI","spec_model":"副牌/高透PC/打暗甲PC用 LUXI",'
            '"unit_price":2.35,"goods_value":11750,"purchase_currency":"人民币RMB","source_type":"PURCHASE_EXPENSE_OA"}'
            "]}]"
        ),
    )

    assert result["ok"] is True
    assert result["linked_purchase_count"] == 1
    assert result["purchase_summaries"][0]["can_open"] is True
    assert result["purchase_summaries"][0]["open_url"].startswith("dingtalk://")
    assert result["writeback_preview"]["matched_count"] == 2
    assert result["writeback_preview"]["fillable_row_count"] == 1
    assert result["writeback_preview"]["writable_row_count"] == 2
    assert result["writeback_preview"]["conflict_row_count"] == 1
    first_changes = result["writeback_preview"]["matched_rows"][0]["business_changes"]
    second_changes = result["writeback_preview"]["matched_rows"][1]["business_changes"]
    assert {change["status"] for change in first_changes} == {"fillable"}
    assert {change["field_label"] for change in first_changes} >= {"单价Precio", "总金额Monto Total"}
    assert any(change["status"] == "conflict" for change in second_changes)


def test_preview_linked_purchase_expense_oa_excludes_refused_summary(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", None)

    result = preview_linked_purchase_expense_oa(
        batch_name="BATCH-001",
        purchase_summaries_json=json.dumps(
            [
                {
                    "source_approval_no": "PUR-VALID-001",
                    "source_instance_id": "PROC-PUR-VALID",
                    "process_status": "COMPLETED",
                    "approval_result": "agree",
                    "mapped_preview_items": [{"material_code": "MAT-VALID"}],
                },
                {
                    "source_approval_no": "PUR-REFUSED-001",
                    "source_instance_id": "PROC-PUR-REFUSED",
                    "process_status": "COMPLETED",
                    "approval_result": "refuse",
                    "mapped_preview_items": [{"material_code": "MAT-REFUSED"}],
                },
            ],
            ensure_ascii=False,
        ),
    )

    assert result["purchase_summary_count"] == 2
    assert result["excluded_purchase_summary_count"] == 1
    assert result["excluded_purchase_summaries"][0]["source_instance_id"] == "PROC-PUR-REFUSED"
    assert result["excluded_purchase_summaries"][0]["effective_status"] == "REJECTED"
    assert result["mapped_purchase_row_count"] == 1
    assert result["mapped_preview_items"][0]["source_instance_id"] == "PROC-PUR-VALID"


def test_preview_packing_list_rejects_audit_only_excluded_attachment(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Attachment"
            assert name == "ATT-EXCLUDED"
            return {
                "batch": "BATCH-001",
                "parse_result_json": json.dumps({
                    "approval_excluded": True,
                    "cost_source_allowed": False,
                    "exclusion_reason": "审批结果为拒绝",
                }),
            }

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_service,
        "_build_packing_preview_items",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("audit-only file must not be parsed")),
    )

    result = preview_packing_list_attachment(
        batch_name="BATCH-001",
        attachment_name="ATT-EXCLUDED",
        file_url="/private/files/excluded.xlsx",
    )

    assert result["ok"] is False
    assert result["audit_only"] is True


def test_preview_packing_list_rejects_legacy_excluded_attachment_from_batch_trace(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Attachment"
            assert name == "ATT-LEGACY-EXCLUDED"
            return {
                "batch": "BATCH-001",
                "parse_result_json": json.dumps({"process_instance_id": "PROC-PUR-REFUSED"}),
            }

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "_approval_reference_is_excluded", lambda *_args: True)
    monkeypatch.setattr(
        import_service,
        "_build_packing_preview_items",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy audit-only file must not be parsed")),
    )

    result = import_service.preview_packing_list_attachment(
        batch_name="BATCH-001",
        attachment_name="ATT-LEGACY-EXCLUDED",
    )

    assert result["ok"] is False
    assert result["audit_only"] is True


def test_preview_linked_purchase_rejects_untrusted_client_summaries_in_frappe(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())

    result = import_service.preview_linked_purchase_expense_oa(
        batch_name="BATCH-001",
        purchase_summaries_json=json.dumps([
            {
                "source_instance_id": "PROC-REJECTED",
                "approval_status": "COMPLETED",
                "mapped_preview_items": [{"material_code": "MAT-FAKE", "unit_price": 1}],
            }
        ]),
    )

    assert result["ok"] is False
    assert result["untrusted_source"] is True


def test_preview_linked_purchase_rejects_client_env_path_in_frappe(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(
        import_service,
        "_resolve_dingtalk_env_file",
        lambda *_args: (_ for _ in ()).throw(AssertionError("client path must not be inspected")),
    )

    result = import_service.preview_linked_purchase_expense_oa(
        batch_name="BATCH-001",
        env_file="/etc/passwd",
    )

    assert result["ok"] is False
    assert result["untrusted_source"] is True


def test_preview_packing_rejects_browser_rows_even_with_attachment_id(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(import_service, "_packing_attachment_is_audit_only", lambda *_args: False)
    monkeypatch.setattr(
        import_service,
        "_build_packing_preview_items",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("browser rows must not be parsed")),
    )

    result = import_service.preview_packing_list_attachment(
        batch_name="BATCH-001",
        attachment_name="ATT-001",
        sheet_rows_json='[{"物料编码":"MAT-FAKE"}]',
    )

    assert result["ok"] is False
    assert result["untrusted_source"] is True


def test_apply_linked_purchase_blocks_invalid_batch_before_item_write(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", lambda **_kwargs: {
        "ok": True,
        "batch_doc_name": "BATCH-DOC",
        "version_name": "VER-001",
        "writeback_preview": {"matched_rows": [{"target_item_name": "ITEM-001"}]},
    })
    monkeypatch.setattr(
        import_service,
        "_invalid_batch_cost_write_response",
        lambda *_args, **_kwargs: {"ok": False, "invalid_business": True, "message": "invalid"},
    )
    monkeypatch.setattr(
        import_service,
        "_update_item_fields",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("invalid batch must not write items")),
    )

    result = import_service.apply_linked_purchase_expense_fillable_fields(batch_name="BATCH-001")

    assert result["ok"] is False
    assert result["invalid_business"] is True


def test_apply_packing_blocks_invalid_batch_before_item_write(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", object())
    monkeypatch.setattr(import_service, "preview_packing_list_attachment", lambda **_kwargs: {
        "ok": True,
        "batch_doc_name": "BATCH-DOC",
        "version_name": "VER-001",
        "writeback_preview": {"matched_rows": [{"target_item_name": "ITEM-001"}]},
    })
    monkeypatch.setattr(
        import_service,
        "_invalid_batch_cost_write_response",
        lambda *_args, **_kwargs: {"ok": False, "invalid_business": True, "message": "invalid"},
    )
    monkeypatch.setattr(
        import_service,
        "_update_item_fields",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("invalid batch must not write items")),
    )

    result = import_service.apply_packing_list_fillable_fields(
        batch_name="BATCH-001",
        attachment_name="ATT-001",
        trusted_server_payload=True,
        preview_result={
            "ok": True,
            "batch_doc_name": "BATCH-DOC",
            "version_name": "VER-001",
            "writeback_preview": {"matched_rows": [{"target_item_name": "ITEM-001"}]},
        },
    )

    assert result["ok"] is False
    assert result["invalid_business"] is True


def test_pull_linked_purchase_summaries_includes_running(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    captured = {}

    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda _env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")

    def fake_pull_linked_purchase_approval_details(**kwargs):
        captured.update(kwargs)
        return [
            {
                "ok": True,
                "source_approval_no": "202606220952000179521",
                "source_instance_id": "PROC-PURCHASE-RUNNING",
                "approval_status": "RUNNING",
                "mapped_preview_items": [
                    {
                        "material_code": "FL004106",
                        "product_name": "钢化膜",
                        "spec_model": "GALAXY A07",
                        "unit_price": 1.23,
                        "purchase_currency": "RMB",
                        "goods_value": 615,
                    }
                ],
            }
        ]

    monkeypatch.setattr(
        import_oa_logistics,
        "pull_linked_purchase_approval_details",
        fake_pull_linked_purchase_approval_details,
    )

    result = import_service._pull_purchase_summaries_from_dingtalk(
        linked_approvals=[
            {
                "approval_no": "202606220952000179521",
                "source_instance_id": "PROC-PURCHASE-RUNNING",
            }
        ]
    )

    assert captured["include_running"] is True
    assert result[0]["approval_status"] == "RUNNING"
    assert result[0]["mapped_preview_items"][0]["unit_price"] == 1.23


def test_pull_linked_purchase_summaries_postgres_does_not_request_api_token(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda _env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "resolve_approval_source_mode", lambda: "postgres")
    monkeypatch.setattr(
        import_oa_logistics,
        "get_access_token",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("postgres mode must not request an API token")),
    )
    monkeypatch.setattr(
        import_oa_logistics,
        "pull_linked_purchase_approval_details",
        lambda **kwargs: [{
            "source_instance_id": kwargs["linked_approvals"][0]["source_instance_id"],
            "mapped_preview_items": [],
        }],
    )

    result = import_service._pull_purchase_summaries_from_dingtalk(
        linked_approvals=[{"source_instance_id": "PROC-PURCHASE-001"}],
    )

    assert result[0]["source_instance_id"] == "PROC-PURCHASE-001"


def test_pull_linked_purchase_summaries_passes_runtime_credentials(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    captured_token_kwargs = {}
    captured_detail_kwargs = {}

    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda _env_file=None: "")
    monkeypatch.setattr(
        import_oa_logistics,
        "_runtime_config_value",
        lambda *keys, default="": {
            "DINGTALK_API_STYLE": "legacy",
            "DINGTALK_APP_KEY": "APP-KEY",
            "DINGTALK_APP_SECRET": "APP-SECRET",
        }.get(keys[0], default),
    )

    def fake_get_access_token(**kwargs):
        captured_token_kwargs.update(kwargs)
        return "TOKEN"

    def fake_pull_linked_purchase_approval_details(**kwargs):
        captured_detail_kwargs.update(kwargs)
        return [{"ok": True, "approval_status": "COMPLETED", "mapped_preview_items": []}]

    monkeypatch.setattr(import_oa_logistics, "get_access_token", fake_get_access_token)
    monkeypatch.setattr(import_oa_logistics, "pull_linked_purchase_approval_details", fake_pull_linked_purchase_approval_details)

    import_service._pull_purchase_summaries_from_dingtalk(
        linked_approvals=[{"approval_no": "202606220952000179521", "source_instance_id": "PROC-PURCHASE"}]
    )

    assert captured_token_kwargs["api_style"] == "legacy"
    assert captured_token_kwargs["app_key"] == "APP-KEY"
    assert captured_token_kwargs["app_secret"] == "APP-SECRET"
    assert captured_detail_kwargs["api_style"] == "legacy"


def test_preview_linked_purchase_expense_splits_aggregated_price_by_material_code(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-DOC":
                if fields == ["current_version"]:
                    return {"current_version": "VER-DOC"}
                return {
                    "name": "BATCH-DOC",
                    "batch_no": "202607031639000159867",
                    "extra_json": (
                        '{"source":"dingtalk_oa_logistics","linked_purchase_approvals":['
                        '{"approval_no":"202606220952000179521","source_instance_id":"PROC-PURCHASE-RUNNING"}'
                        "]}",
                    )[0],
                }
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return [
                    {
                        "name": "ITEM-001",
                        "row_no": 1,
                        "material_code": "FL004106",
                        "product_name": "钢化膜",
                        "spec_model": "GALAXY A07",
                        "quantity": 500,
                        "actual_shipped_qty": 0,
                        "unit_price": 0,
                        "purchase_currency": "",
                        "goods_value": 0,
                    },
                    {
                        "name": "ITEM-002",
                        "row_no": 2,
                        "material_code": "FL004106",
                        "product_name": "钢化膜",
                        "spec_model": "GALAXY A07",
                        "quantity": 300,
                        "actual_shipped_qty": 0,
                        "unit_price": 0,
                        "purchase_currency": "",
                        "goods_value": 0,
                    },
                ]
            return []

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = preview_linked_purchase_expense_oa(
        batch_name="BATCH-DOC",
        trusted_server_payload=True,
        purchase_summaries_json=(
            '[{"source_approval_no":"202606220952000179521","source_instance_id":"PROC-PURCHASE-RUNNING",'
            '"purchase_currency":"人民币RMB","mapped_preview_items":['
            '{"material_code":"FL004106","product_name":"钢化膜Película templada","spec_model":"SM A07/9H",'
            '"quantity":800,"unit_price":1.2,"goods_value":960,"purchase_currency":"人民币RMB",'
            '"source_type":"PURCHASE_EXPENSE_OA"}'
            "]}]"
        ),
    )

    matched_rows = result["writeback_preview"]["matched_rows"]
    goods_values = [
        change["new_value"]
        for row in matched_rows
        for change in row["business_changes"]
        if change["field_name"] == "goods_value"
    ]

    assert result["writeback_preview"]["matched_count"] == 2
    assert result["writeback_preview"]["unmatched_count"] == 0
    assert result["writeback_preview"]["ambiguous_count"] == 0
    assert goods_values == [600, 360]
    assert {row["disambiguation_strategy"] for row in matched_rows} == {
        "material_code_split_by_target_quantity"
    }


def test_apply_linked_purchase_expense_fillable_fields_writes_matched_conflicts(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeItemDoc:
        def __init__(self, **values):
            self.__dict__.update(values)
            self.save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload
            self.name = f"AUDIT-{len(audit_payloads) + 1}"

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    items = {
        "ITEM-001": FakeItemDoc(
            name="ITEM-001",
            row_no=1,
            material_code="YL000097",
            product_name="TPU原料 HF-8695AU",
            spec_model="HF-8695AU",
            unit_price=0.0,
            purchase_currency="",
            goods_value=0.0,
        ),
        "ITEM-002": FakeItemDoc(
            name="ITEM-002",
            row_no=2,
            material_code="YL000058",
            product_name="副牌PC透明 LUXI",
            spec_model="LUXI",
            unit_price=2.1,
            purchase_currency="人民币RMB",
            goods_value=100,
        ),
    }
    batch_updates = {}
    audit_payloads = []
    batch_locks = []
    commit_count = {"value": 0}
    recalculate_calls = []

    def fake_recalculate_batch(**kwargs):
        recalculate_calls.append(kwargs)
        return {
            "ok": True,
            "summary_snapshot": {"total_amount_rmb": 40750},
            "message": "重算完成",
        }

    class FakeDB:
        @staticmethod
        def sql(query, values):
            assert query == "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE"
            batch_locks.append(values)

        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "FSCU8486789":
                return {"name": "BATCH-DOC"} if as_dict else "BATCH-DOC"
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-DOC":
                if fields == ["current_version"]:
                    return {"current_version": "VER-DOC"}
                return {
                    "name": "BATCH-DOC",
                    "batch_no": "FSCU8486789",
                    "extra_json": (
                        '{"source":"dingtalk_oa_logistics","linked_purchase_approvals":['
                        '{"approval_no":"202604150041000081318","source_instance_id":"PROC-PURCHASE-001"}'
                        "]}"
                    ),
                }
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            batch_updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype != "Overseas Cost Item":
                return []
            return [
                {
                    "name": item.name,
                    "row_no": item.row_no,
                    "material_code": item.material_code,
                    "product_name": item.product_name,
                    "spec_model": item.spec_model,
                    "unit_price": item.unit_price,
                    "purchase_currency": item.purchase_currency,
                    "goods_value": item.goods_value,
                }
                for item in items.values()
            ]

        @staticmethod
        def get_doc(*args):
            if len(args) == 2 and args[0] == "Overseas Cost Item":
                return items[args[1]]
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeAuditDoc(args[0])
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        "overseas_costing.services.calculate_service.recalculate_batch",
        fake_recalculate_batch,
    )

    result = apply_linked_purchase_expense_fillable_fields(
        batch_name="FSCU8486789",
        trusted_server_payload=True,
        purchase_summaries_json=(
            '[{"source_approval_no":"202604150041000081318","source_instance_id":"PROC-PURCHASE-001",'
            '"purchase_currency":"人民币RMB","mapped_preview_items":['
            '{"material_code":"YL000097","product_name":"TPU原料 HF-8695AU","spec_model":"HF-8695AU",'
            '"unit_price":2.9,"goods_value":29000,"purchase_currency":"人民币RMB","source_type":"PURCHASE_EXPENSE_OA"},'
            '{"material_code":"YL000058","product_name":"副牌PC透明 LUXI","spec_model":"LUXI",'
            '"unit_price":2.35,"goods_value":11750,"purchase_currency":"人民币RMB","source_type":"PURCHASE_EXPENSE_OA"}'
            "]}]"
        ),
    )

    assert result["ok"] is True
    assert result["updated_count"] == 2
    assert result["changed_field_count"] == 5
    assert items["ITEM-001"].unit_price == 2.9
    assert items["ITEM-001"].purchase_currency == "人民币RMB"
    assert items["ITEM-001"].goods_value == 29000
    assert items["ITEM-002"].unit_price == 2.35
    assert items["ITEM-002"].goods_value == 11750
    assert batch_updates[("Overseas Cost Batch", "BATCH-DOC", "status")] == "Dirty"
    assert commit_count["value"] == 1
    assert batch_locks == [("BATCH-DOC",)]
    assert len(audit_payloads) == 5
    assert recalculate_calls == [{"batch_name": "BATCH-DOC", "version_name": "VER-DOC"}]
    assert result["recalculate_result"]["action"] == "recalculated"
    assert result["recalculate_result"]["summary_snapshot"] == {"total_amount_rmb": 40750}
    assert "已自动重算" in result["message"]


def test_apply_linked_purchase_expense_aggregates_duplicate_target_rows(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeItemDoc:
        def __init__(self, **values):
            self.__dict__.update(values)
            self.save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    item = FakeItemDoc(
        name="ITEM-001",
        row_no=1,
        material_code="YL00060",
        product_name="TPU原料 HF-1190A-8",
        spec_model="HF-1190A-8",
        quantity=15000,
        unit_price=0.0,
        purchase_currency="",
        goods_value=0.0,
    )
    batch_updates = {}
    audit_payloads = []
    batch_locks = []
    commit_count = {"value": 0}

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload
            self.name = f"AUDIT-{len(audit_payloads) + 1}"

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def sql(query, values):
            assert query == "SELECT name FROM `tabOverseas Cost Batch` WHERE name=%s FOR UPDATE"
            batch_locks.append(values)

        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "FSCU8486789":
                return {"name": "BATCH-DOC"} if as_dict else "BATCH-DOC"
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-DOC":
                if fields == ["current_version"]:
                    return {"current_version": "VER-DOC"}
                return {
                    "name": "BATCH-DOC",
                    "batch_no": "FSCU8486789",
                    "extra_json": (
                        '{"source":"dingtalk_oa_logistics","linked_purchase_approvals":['
                        '{"approval_no":"202604300000000596348","source_instance_id":"PROC-PURCHASE-001"}'
                        "]}"
                    ),
                }
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            batch_updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype != "Overseas Cost Item":
                return []
            return [
                {
                    "name": item.name,
                    "row_no": item.row_no,
                    "material_code": item.material_code,
                        "product_name": item.product_name,
                        "spec_model": item.spec_model,
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                        "purchase_currency": item.purchase_currency,
                        "goods_value": item.goods_value,
                }
            ]

        @staticmethod
        def get_doc(*args):
            if len(args) == 2 and args[0] == "Overseas Cost Item":
                return item
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeAuditDoc(args[0])
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    approved_rate = 7.2

    def version_fx(version_name, **_kwargs):
        assert version_name == "VER-DOC"
        return {"fx_usd_to_rmb": approved_rate}

    monkeypatch.setattr(import_service, "_get_version_fx_context", version_fx)

    result = apply_linked_purchase_expense_fillable_fields(
        batch_name="FSCU8486789",
        trusted_server_payload=True,
        purchase_summaries_json=(
            '[{"source_approval_no":"202604300000000596348","source_instance_id":"PROC-PURCHASE-001",'
                '"purchase_currency":"美元Dólar","mapped_preview_items":['
                '{"material_code":"YL00060","product_name":"原料TPU","spec_model":"HF-1190A-1",'
                '"quantity":5000,"unit_price":2.35,"goods_value":11750,"purchase_currency":"美元Dólar","source_type":"PURCHASE_EXPENSE_OA"},'
                '{"material_code":"YL00060","product_name":"原料TPU","spec_model":"HF-1190A-1",'
                '"quantity":10500,"unit_price":2.2,"goods_value":23100,"purchase_currency":"美元Dólar","source_type":"PURCHASE_EXPENSE_OA"}'
                "]}]"
            ),
        )

    assert result["ok"] is True
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 0
    assert item.unit_price == pytest.approx((34850 / 15500) * approved_rate)
    assert item.purchase_currency == "美元Dólar"
    assert item.goods_value == pytest.approx((34850 / 15500) * approved_rate * 15000)
    assert batch_updates[("Overseas Cost Batch", "BATCH-DOC", "status")] == "Dirty"
    assert commit_count["value"] == 1
    assert batch_locks == [("BATCH-DOC",)]
    assert len(audit_payloads) == 3


def test_list_oa_form_attachments_returns_structured_records(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fieldname=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "FSCU8486789":
                return {"name": "BATCH-DOC"} if as_dict else "BATCH-DOC"
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype != "Overseas Cost Attachment":
                return []
            return [
                {
                    "name": "ATTACH-001",
                    "batch": "BATCH-DOC",
                    "version": "VER-DOC",
                    "source_type": "OA",
                    "oa_attachment_origin": "Comment",
                    "attachment_type": "Packing List",
                    "source_doc_no": "202607220001::FILE-001",
                    "file_name": "装箱单.xlsx",
                    "file_url": "",
                    "parse_status": "Queued",
                    "parse_result_json": attachment_parse_service._json_dumps(
                        {
                            "source_field": "Adjunto物品清单/运费报价等附件信息",
                            "file_id": "FILE-001",
                            "space_id": "SPACE-001",
                            "file_ext": "xlsx",
                            "comment_attachments_included": True,
                            "comment_user_name": "张三",
                            "comment_time": "2026-09-01 10:00:00",
                            "comment_remark": "补充装箱单",
                            "archive": {
                                "status": "pending",
                                "archive_method": "workflow_download",
                                "content_quality": "original",
                                "failure_code": "userNotExist",
                                "failure_reason": "download authorization failed: userNotExist",
                                "source_updated_at": "2026-09-04T06:00:00+00:00",
                            },
                            "last_download_error": {
                                "error_type": "dingtalk_attachment_file_access",
                                "message": "当前账号无附件访问权",
                            },
                        }
                    ),
                    "mapped_result_json": attachment_parse_service._json_dumps(
                        {"parse_targets": ["actual_shipped_qty", "gross_weight_kg", "volume_m3"]}
                    ),
                    "remark": "钉钉评论附件已登记，等待归档。",
                }
            ]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = list_oa_form_attachments(batch_name="FSCU8486789")

    assert result["ok"] is True
    assert result["comment_attachments_included"] is True
    assert result["total"] == 1
    assert result["items"][0]["file_name"] == "装箱单.xlsx"
    assert result["items"][0]["file_id"] == "FILE-001"
    assert result["items"][0]["can_download"] is True
    assert result["items"][0]["parse_targets"] == ["actual_shipped_qty", "gross_weight_kg", "volume_m3"]
    assert result["items"][0]["last_download_error"]["error_type"] == "dingtalk_attachment_file_access"
    assert result["items"][0]["oa_attachment_origin"] == "Comment"
    assert result["items"][0]["comment_user_name"] == "张三"
    assert result["items"][0]["data_source"] == "minio_archive"
    assert result["items"][0]["archive_status"] == "pending"
    assert result["items"][0]["archive_method"] == "workflow_download"
    assert result["items"][0]["content_quality"] == "original"
    assert result["items"][0]["failure_code"] == "userNotExist"
    assert "尚不能判断文件是否删除" in result["items"][0]["failure_reason"]
    assert result["items"][0]["previewable"] is False
    assert result["items"][0]["downloadable"] is False
    assert result["items"][0]["source_lag_seconds"] >= 0


def test_download_oa_form_attachment_saves_file_url_and_keeps_trace(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    commit_count = {"value": 0}

    class FakeAttachmentDoc:
        name = "ATTACH-001"
        source_type = "OA"
        attachment_type = "Packing List"
        file_name = "packing.xlsx"
        file_url = ""
        parse_status = "Queued"
        remark = ""
        parse_result_json = json.dumps(
            {
                "source": "dingtalk_oa_form_attachment",
                "instance_id": "PROC-SEA-001",
                "file_id": "FILE-001",
                "space_id": "SPACE-001",
                "raw_attachment": {"fileName": "packing.xlsx", "fileId": "FILE-001"},
            },
            ensure_ascii=False,
        )

        def save(self, **_kwargs):
            return self

    attachment_doc = FakeAttachmentDoc()

    class FakeDB:
        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(*args):
            if args == ("Overseas Cost Attachment", "ATTACH-001"):
                return attachment_doc
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "load_env_file", lambda _path: _path)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN-001")
    monkeypatch.setattr(
        import_oa_logistics,
        "get_process_attachment_download_url",
        lambda **_kwargs: {"download_uri": "https://download.example.com/packing.xlsx"},
    )
    monkeypatch.setattr(
        import_service,
        "_fetch_dingtalk_attachment_content",
        lambda _download_uri: (
            b"excel-bytes",
            {
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content_length": 11,
            },
        ),
    )
    monkeypatch.setattr(
        import_service,
        "_save_content_as_frappe_file",
        lambda **_kwargs: {"file_url": "/private/files/packing.xlsx"},
    )

    result = download_oa_form_attachment("ATTACH-001")
    updated_snapshot = json.loads(attachment_doc.parse_result_json)

    assert result["ok"] is True
    assert result["downloaded"] is True
    assert result["file_url"] == "/private/files/packing.xlsx"
    assert attachment_doc.file_url == "/private/files/packing.xlsx"
    assert attachment_doc.parse_status == "Queued"
    assert updated_snapshot["download"]["file_id"] == "FILE-001"
    assert commit_count["value"] == 1


def test_download_oa_attachment_reads_verified_minio_archive_in_postgres_mode(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    commit_count = {"value": 0}

    class FakeAttachmentDoc:
        name = "ATTACH-ARCHIVED"
        source_type = "OA"
        oa_attachment_origin = "Comment"
        attachment_type = "Packing List"
        file_name = "comment-packing.xlsx"
        file_url = ""
        parse_status = "Queued"
        remark = ""
        parse_result_json = json.dumps(
            {
                "source": "dingtalk_oa_comment_attachment",
                "instance_id": "PROC-SEA-001",
                "file_id": "FILE-COMMENT-001",
                "space_id": "SPACE-001",
                "comment_user_name": "张三",
                "comment_time": "2026-09-01 10:00:00",
                "comment_remark": "补充装箱单",
            },
            ensure_ascii=False,
        )

        def save(self, **_kwargs):
            return self

    attachment_doc = FakeAttachmentDoc()

    class FakeDB:
        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(*args):
            assert args == ("Overseas Cost Attachment", "ATTACH-ARCHIVED")
            return attachment_doc

    class FakeSource:
        @staticmethod
        def get_attachment_manifest(process_instance_id, file_id):
            assert (process_instance_id, file_id) == ("PROC-SEA-001", "FILE-COMMENT-001")
            return {
                "archive_status": "archived",
                "object_key": "corp/PROC-SEA-001/FILE-COMMENT-001",
                "actual_size": 13,
                "sha256": "abc",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "archived_at": "2026-09-04T03:16:00+00:00",
                "updated_at": "2026-09-04T03:17:00+00:00",
            }

    class FakeArchiveClient:
        @staticmethod
        def download(manifest):
            assert manifest["archive_status"] == "archived"
            return b"archived-data", {
                "content_length": 13,
                "sha256": "abc",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "object_key": manifest["object_key"],
                "archive_status": "archived",
            }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_approval_source_mode", lambda: "postgres")
    monkeypatch.setattr(import_service, "_get_oa_postgres_source", lambda: FakeSource())
    monkeypatch.setattr(import_service, "_get_minio_archive_client", lambda: FakeArchiveClient())
    monkeypatch.setattr(
        import_service,
        "_save_content_as_frappe_file",
        lambda **_kwargs: {"file_url": "/private/files/comment-packing.xlsx"},
    )
    monkeypatch.setattr(
        import_oa_logistics,
        "get_access_token",
        lambda **_kwargs: pytest.fail("归档模式不应请求钉钉 token"),
    )

    result = download_oa_form_attachment("ATTACH-ARCHIVED")
    snapshot = json.loads(attachment_doc.parse_result_json)

    assert result["ok"] is True
    assert result["data_source"] == "minio_archive"
    assert result["archive_status"] == "archived"
    assert result["fallback_used"] is False
    assert result["source_updated_at"] == "2026-09-04T03:17:00+00:00"
    assert result["source_lag_seconds"] >= 0
    assert attachment_doc.file_url == "/private/files/comment-packing.xlsx"
    assert snapshot["download"]["sha256"] == "abc"
    assert snapshot["download"]["object_key"].endswith("FILE-COMMENT-001")
    assert snapshot["comment_user_name"] == "张三"
    assert commit_count["value"] == 1


@pytest.mark.parametrize(
    ("status", "expected_message", "manual"),
    [
        ("retry", "附件正在归档", False),
        ("manual_required", "历史附件已失效", True),
    ],
)
def test_download_oa_attachment_reports_archive_state(monkeypatch, status, expected_message, manual) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    class FakeAttachmentDoc:
        source_type = "OA"
        file_name = "packing.xlsx"
        file_url = ""
        parse_status = "Queued"
        remark = ""
        parse_result_json = json.dumps({"instance_id": "PROC-1", "file_id": "FILE-1"})

        def save(self, **_kwargs):
            return self

    class FakeFrappe:
        class db:
            @staticmethod
            def commit():
                return None

        @staticmethod
        def get_doc(*_args):
            return FakeAttachmentDoc()

    class FakeSource:
        @staticmethod
        def get_attachment_manifest(*_args):
            return {
                "archive_status": status,
                "last_error": "历史附件已失效" if manual else "temporary",
                "updated_at": "2026-09-04T04:00:00+00:00",
            }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_approval_source_mode", lambda: "postgres")
    monkeypatch.setattr(import_service, "_get_oa_postgres_source", lambda: FakeSource())

    result = download_oa_form_attachment("ATTACH-1")

    assert result["ok"] is False
    assert result["archive_status"] == status
    assert expected_message in result["message"]
    assert result["needs_manual_upload"] is manual
    assert result["fallback_used"] is False
    assert result["source_updated_at"] == "2026-09-04T04:00:00+00:00"
    assert result["source_lag_seconds"] >= 0


@pytest.mark.parametrize("archive_error", [
    "You do not have permission to access this file",
    "403 Forbidden",
])
def test_minio_access_denied_is_reported_as_actionable_chinese_message(monkeypatch, archive_error) -> None:
    from overseas_costing.services import import_service

    class Attachment:
        parse_status = "Queued"
        remark = ""
        parse_result_json = "{}"

        def save(self, **_kwargs):
            return self

    class Source:
        @staticmethod
        def get_attachment_manifest(*_args):
            return {
                "archive_status": "archived",
                "object_key": "corp/proc/file",
                "updated_at": "2026-09-04T04:00:00+00:00",
            }

    class Archive:
        @staticmethod
        def download(_manifest):
            raise RuntimeError(archive_error)

    monkeypatch.setattr(import_service, "_get_oa_postgres_source", lambda: Source())
    monkeypatch.setattr(import_service, "_get_minio_archive_client", lambda: Archive())

    result = import_service._download_oa_attachment_from_archive(
        attachment_doc=Attachment(), attachment_name="ATTACH-1", parse_snapshot={},
        process_instance_id="PROC-1", file_id="FILE-1", file_name="packing.xlsx",
    )

    assert result["ok"] is False
    assert result["needs_manual_upload"] is True
    assert "MinIO 归档账号无权读取" in result["message"]
    assert "人工上传" in result["message"]
    assert archive_error not in result["message"]


def test_manual_required_access_denied_manifest_hides_raw_archive_error(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class Attachment:
        parse_status = "Queued"
        remark = ""
        parse_result_json = "{}"

        def save(self, **_kwargs):
            return self

    class Source:
        @staticmethod
        def get_attachment_manifest(*_args):
            return {
                "archive_status": "manual_required",
                "last_error": "AccessDenied: You do not have permission to access this file",
                "updated_at": "2026-09-04T04:00:00+00:00",
            }

    class FakeFrappe:
        class db:
            @staticmethod
            def commit():
                return None

    monkeypatch.setattr(import_service, "_get_oa_postgres_source", lambda: Source())
    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = import_service._download_oa_attachment_from_archive(
        attachment_doc=Attachment(), attachment_name="ATTACH-1", parse_snapshot={},
        process_instance_id="PROC-1", file_id="FILE-1", file_name="packing.xlsx",
    )

    assert result["ok"] is False
    assert result["error_type"] == "minio_archive_access_denied"
    assert result["needs_manual_upload"] is True
    assert "MinIO 归档账号无权读取" in result["message"]
    assert "AccessDenied" not in result["message"]


def test_fetch_dingtalk_attachment_content_passes_signed_headers(monkeypatch) -> None:
    from overseas_costing.services import import_service

    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/vnd.ms-excel", "Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b"data"

    def fake_urlopen(request, timeout):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(import_service, "urlopen", fake_urlopen)

    content, meta = import_service._fetch_dingtalk_attachment_content(
        "https://download.example.com/file.xlsx",
        headers={"x-acs-signature": "SIG-001"},
    )

    assert content == b"data"
    assert meta["content_length"] == 4
    assert captured["timeout"] == 60
    assert captured["headers"]["x-acs-signature"] == "SIG-001"
    assert captured["headers"]["user-agent"] == "overseas-costing/1.0"


def test_download_oa_form_attachment_reports_file_access_error(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    save_count = {"value": 0}
    commit_count = {"value": 0}

    class FakeAttachmentDoc:
        name = "ATTACH-001"
        source_type = "OA"
        attachment_type = "Packing List"
        file_name = "packing.xlsx"
        file_url = ""
        parse_status = "Queued"
        remark = ""
        parse_result_json = json.dumps(
            {
                "source": "dingtalk_oa_form_attachment",
                "instance_id": "PROC-SEA-001",
                "file_id": "FILE-001",
                "raw_attachment": {"fileName": "packing.xlsx", "fileId": "FILE-001"},
            },
            ensure_ascii=False,
        )

        def save(self, **_kwargs):
            save_count["value"] += 1
            return self

    attachment_doc = FakeAttachmentDoc()

    class FakeFrappe:
        class db:
            @staticmethod
            def get_value(*_args, **_kwargs):
                return None

            @staticmethod
            def commit():
                commit_count["value"] += 1

        @staticmethod
        def get_doc(*args):
            if args == ("Overseas Cost Attachment", "ATTACH-001"):
                return attachment_doc
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "load_env_file", lambda _path: _path)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN-001")
    monkeypatch.setattr(
        import_oa_logistics,
        "get_process_attachment_download_url",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError('HTTP 403：{"code":"permissionDenied","message":"dentryId"}')),
    )

    result = download_oa_form_attachment("ATTACH-001")

    assert result["ok"] is False
    assert result["error_type"] == "dingtalk_attachment_file_access"
    assert result["needs_manual_upload"] is True
    assert "文件级访问权限" in result["message"]
    assert "能在钉钉原单里打开该附件的在职账号" in result["message"]
    updated_snapshot = json.loads(attachment_doc.parse_result_json)
    assert attachment_doc.parse_status == "Failed"
    assert attachment_doc.remark.startswith("钉钉已找到这份审批附件")
    assert updated_snapshot["last_download_error"]["error_type"] == "dingtalk_attachment_file_access"
    assert updated_snapshot["last_download_error"]["file_id"] == "FILE-001"
    assert save_count["value"] == 1
    assert commit_count["value"] == 1


def test_download_oa_form_attachment_uses_thumbnail_media_fallback(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    commit_count = {"value": 0}
    saved_payload = {}
    fetched_urls: list[str] = []

    class FakeAttachmentDoc:
        name = "ATTACH-PNG"
        source_type = "OA"
        attachment_type = "Other"
        file_name = "报价.png"
        file_url = ""
        parse_status = "Queued"
        remark = ""
        parse_result_json = json.dumps(
            {
                "source": "dingtalk_oa_form_attachment",
                "instance_id": "PROC-SEA-001",
                "file_id": "FILE-001",
                "raw_attachment": {
                    "fileName": "报价.png",
                    "fileId": "FILE-001",
                    "thumbnail": {"authMediaId": "MEDIA-001"},
                },
            },
            ensure_ascii=False,
        )

        def save(self, **_kwargs):
            return self

    attachment_doc = FakeAttachmentDoc()

    class FakeDB:
        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(*args):
            if args == ("Overseas Cost Attachment", "ATTACH-PNG"):
                return attachment_doc
            raise AssertionError(args)

    def fake_fetch(download_uri, **_kwargs):
        fetched_urls.append(download_uri)
        return b"\x89PNG\r\n", {"content_type": "image/png", "content_length": 6}

    def fake_save(**kwargs):
        saved_payload.update(kwargs)
        return {"file_url": "/private/files/报价.png"}

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "_resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "load_env_file", lambda _path: _path)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN-001")
    monkeypatch.setattr(
        import_oa_logistics,
        "get_process_attachment_download_url",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError('HTTP 403：{"code":"permissionDenied","message":"dentryId"}')),
    )
    monkeypatch.setattr(import_service, "_fetch_dingtalk_attachment_content", fake_fetch)
    monkeypatch.setattr(import_service, "_save_content_as_frappe_file", fake_save)

    result = download_oa_form_attachment("ATTACH-PNG")
    updated_snapshot = json.loads(attachment_doc.parse_result_json)

    assert result["ok"] is True
    assert result["downloaded"] is True
    assert result["file_url"] == "/private/files/报价.png"
    assert "media/downloadFile" in fetched_urls[0]
    assert "media_id=MEDIA-001" in fetched_urls[0]
    assert saved_payload["file_name"] == "报价.png"
    assert updated_snapshot["download"]["fallback_api"] == "thumbnail_media_download"
    assert updated_snapshot["download"]["saved_file_name"] == "报价.png"
    assert commit_count["value"] == 1


def test_save_large_content_as_private_file_registers_file_doc(monkeypatch) -> None:
    from overseas_costing.services import import_service

    inserted_docs: list[dict] = []
    site_root = Path("tmp_large_file_test_site").resolve()
    if site_root.exists():
        shutil.rmtree(site_root)

    class FakeFileDoc:
        def __init__(self, payload):
            self.payload = payload
            self.file_url = payload["file_url"]

        def insert(self, **_kwargs):
            inserted_docs.append(self.payload)
            return self

    class FakeFrappe:
        @staticmethod
        def get_site_path(*parts):
            return str(site_root.joinpath(*parts))

        @staticmethod
        def get_doc(payload):
            return FakeFileDoc(payload)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    try:
        result = import_service._save_large_content_as_private_file(
            file_name='CI&PL:测试?.xlsx',
            content=b"large-excel-bytes",
            attached_to_name="ATTACH-001",
        )

        assert result.file_url.startswith("/private/files/")
        assert (site_root / "private" / "files" / Path(result.file_url).name).read_bytes() == b"large-excel-bytes"
        assert inserted_docs[0]["doctype"] == "File"
        assert inserted_docs[0]["attached_to_doctype"] == "Overseas Cost Attachment"
        assert inserted_docs[0]["attached_to_name"] == "ATTACH-001"
        assert ":" not in inserted_docs[0]["file_name"]
        assert "?" not in inserted_docs[0]["file_name"]
    finally:
        if site_root.exists():
            shutil.rmtree(site_root)


def test_parse_oa_packing_list_attachments_only_handles_excel_packing_lists(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "ATTACH-XLSX",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-XLSX",
            "file_name": "CI&PL.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-XLSX", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty", "gross_weight_kg"]}, ensure_ascii=False),
        },
        {
            "name": "ATTACH-PDF",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-PDF",
            "file_name": "装箱单.pdf",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-PDF", "file_ext": "pdf"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        },
        {
            "name": "ATTACH-PARSED",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-PARSED",
            "file_name": "已解析装箱单.xlsx",
            "file_url": "/private/files/parsed.xlsx",
            "parse_status": "Parsed",
            "parse_result_json": json.dumps({"file_id": "FILE-PARSED", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        },
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    download_calls: list[str] = []
    parse_calls: list[dict] = []

    def fake_download(attachment_name, **_kwargs):
        download_calls.append(attachment_name)
        return {
            "ok": True,
            "downloaded": True,
            "attachment_name": attachment_name,
            "file_url": "/private/files/CI&PL.xlsx",
            "message": "已下载",
        }

    def fake_apply(**kwargs):
        parse_calls.append(kwargs)
        return {
            "ok": True,
            "batch_doc_name": kwargs["batch_name"],
            "version_name": kwargs["version_name"],
            "updated_count": 2,
            "created_count": 1,
            "changed_field_count": 4,
            "skipped_count": 0,
            "conflict_row_count": 0,
            "unmatched_count": 1,
            "ambiguous_count": 0,
            "attachment_marked_parsed": True,
            "message": "已解析",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)
    monkeypatch.setattr(import_service, "apply_packing_list_fillable_fields", fake_apply)

    result = parse_oa_packing_list_attachments(recalculate=False)

    assert result["ok"] is True
    assert result["scanned_count"] == 3
    assert result["downloaded_count"] == 1
    assert result["parsed_count"] == 1
    assert result["updated_count"] == 2
    assert result["created_count"] == 1
    assert result["changed_field_count"] == 4
    assert result["skipped_count"] == 2
    assert download_calls == ["ATTACH-XLSX"]
    assert parse_calls[0]["attachment_name"] == "ATTACH-XLSX"
    assert parse_calls[0]["auto_create_unmatched_items"] is True
    assert any(item["attachment_name"] == "ATTACH-PDF" and item["action"] == "skipped" for item in result["items"])


def test_parse_oa_source_attachments_downloads_excel_and_recognizes_image(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "ATTACH-XLSX",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-XLSX",
            "file_name": "CI&PL.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-XLSX", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty", "gross_weight_kg"]}, ensure_ascii=False),
        },
        {
            "name": "ATTACH-PNG",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Other",
            "source_doc_no": "OA-001::FILE-PNG",
            "file_name": "劳保鞋物流报价3.26.png",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-PNG", "file_ext": "png"}, ensure_ascii=False),
            "mapped_result_json": "{}",
        },
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    download_calls: list[str] = []
    parse_calls: list[dict] = []
    preview_calls: list[str] = []

    def fake_download(attachment_name, **_kwargs):
        download_calls.append(attachment_name)
        return {
            "ok": True,
            "downloaded": True,
            "attachment_name": attachment_name,
            "file_url": f"/private/files/{attachment_name}.{'xlsx' if attachment_name.endswith('XLSX') else 'png'}",
            "message": "已下载",
        }

    def fake_apply(**kwargs):
        parse_calls.append(kwargs)
        return {
            "ok": True,
            "batch_doc_name": kwargs["batch_name"],
            "version_name": kwargs["version_name"],
            "updated_count": 1,
            "created_count": 0,
            "changed_field_count": 2,
            "skipped_count": 0,
            "conflict_row_count": 0,
            "unmatched_count": 0,
            "ambiguous_count": 0,
            "attachment_marked_parsed": True,
            "message": "已解析",
        }

    def fake_preview(attachment_name):
        preview_calls.append(attachment_name)
        return {
            "ok": True,
            "classification": {"code": "logistics_quote", "label": "物流报价"},
            "extraction_method": "ocr_image",
            "text_length": 120,
            "purchase_order": {},
            "can_write_purchase_price": False,
            "message": "附件内容识别预览已生成，当前不会写入物料单价或货值。",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)
    monkeypatch.setattr(import_service, "apply_packing_list_fillable_fields", fake_apply)
    monkeypatch.setattr(import_service, "preview_oa_source_attachment", fake_preview)

    result = parse_oa_source_attachments(recalculate=False)

    assert result["ok"] is True
    assert result["scanned_count"] == 2
    assert result["downloaded_count"] == 2
    assert result["packing_parsed_count"] == 1
    assert result["source_recognized_count"] == 1
    assert result["parsed_count"] == 2
    assert result["updated_count"] == 1
    assert result["changed_field_count"] == 2
    assert download_calls == ["ATTACH-XLSX", "ATTACH-PNG"]
    assert parse_calls[0]["attachment_name"] == "ATTACH-XLSX"
    assert preview_calls == ["ATTACH-PNG"]
    assert result["items"][1]["recognized_type_label"] == "物流报价"


def test_parse_manual_document_attachments_uses_latest_slot_without_dingtalk_download(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "MANUAL-OLD",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "Manual",
            "attachment_type": "Packing List",
            "source_doc_no": "装箱单",
            "file_name": "old.xlsx",
            "file_url": "/private/files/old.xlsx",
            "parse_status": "Draft",
            "parse_result_json": json.dumps(
                {"manual_document": {"slot_code": "sea_packing_list", "logistics_type": "SEA"}},
                ensure_ascii=False,
            ),
            "mapped_result_json": "{}",
        },
        {
            "name": "MANUAL-NEW",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "Manual",
            "attachment_type": "Packing List",
            "source_doc_no": "装箱单",
            "file_name": "new.xlsx",
            "file_url": "/private/files/new.xlsx",
            "parse_status": "Draft",
            "parse_result_json": json.dumps(
                {"manual_document": {"slot_code": "sea_packing_list", "logistics_type": "SEA"}},
                ensure_ascii=False,
            ),
            "mapped_result_json": "{}",
        },
        {
            "name": "MANUAL-PNG",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "Manual",
            "attachment_type": "Other",
            "source_doc_no": "物流报价",
            "file_name": "quote.png",
            "file_url": "/private/files/quote.png",
            "parse_status": "Draft",
            "parse_result_json": json.dumps(
                {"manual_document": {"slot_code": "sea_other", "logistics_type": "SEA"}},
                ensure_ascii=False,
            ),
            "mapped_result_json": "{}",
        },
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    parse_calls: list[dict] = []
    preview_calls: list[str] = []

    def fake_download(*_args, **_kwargs):
        raise AssertionError("人工补传资料不应调用钉钉附件下载")

    def fake_apply(**kwargs):
        parse_calls.append(kwargs)
        return {
            "ok": True,
            "batch_doc_name": kwargs["batch_name"],
            "version_name": kwargs["version_name"],
            "updated_count": 1,
            "created_count": 0,
            "changed_field_count": 2,
            "skipped_count": 0,
            "conflict_row_count": 0,
            "unmatched_count": 0,
            "ambiguous_count": 0,
            "attachment_marked_parsed": True,
            "message": "已解析",
        }

    def fake_preview(attachment_name):
        preview_calls.append(attachment_name)
        return {
            "ok": True,
            "classification": {"code": "logistics_quote", "label": "物流报价"},
            "extraction_method": "ocr_image",
            "text_length": 80,
            "purchase_order": {},
            "can_write_purchase_price": False,
            "message": "资料内容识别预览已生成。",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)
    monkeypatch.setattr(import_service, "apply_packing_list_fillable_fields", fake_apply)
    monkeypatch.setattr(import_service, "preview_oa_source_attachment", fake_preview)

    result = parse_manual_document_attachments(logistics_type="SEA", recalculate=False)

    assert result["ok"] is True
    assert result["scanned_count"] == 2
    assert result["downloaded_count"] == 0
    assert result["packing_parsed_count"] == 1
    assert result["source_recognized_count"] == 1
    assert result["changed_field_count"] == 2
    assert parse_calls[0]["attachment_name"] == "MANUAL-NEW"
    assert preview_calls == ["MANUAL-PNG"]


def test_delete_manual_document_attachment_rolls_back_parse_snapshot(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeDoc:
        def __init__(self, doctype=None, name=None, **values):
            self.doctype = doctype
            self.name = name
            for key, value in values.items():
                setattr(self, key, value)

        def save(self, **_kwargs):
            return self

        def insert(self, **_kwargs):
            FakeFrappe.audit_logs.append(self)
            return self

    attachment = FakeDoc(
        "Overseas Cost Attachment",
        "MANUAL-PARSED",
        source_type="Manual",
        batch="BATCH-DOC",
        version="VER-DOC",
        mapped_result_json=json.dumps(
            {
                "applied_rows": [
                    {
                        "target_item_name": "ITEM-1",
                        "target_row_no": 1,
                        "changed_fields": [
                            {"field_name": "actual_shipped_qty", "old_value": 0, "new_value": 12},
                            {"field_name": "gross_weight_kg", "old_value": 0, "new_value": 5.5},
                        ],
                    }
                ],
                "created_rows": [{"item_name": "ITEM-NEW", "row_no": 2}],
            },
            ensure_ascii=False,
        ),
    )
    item_existing = FakeDoc(
        "Overseas Cost Item",
        "ITEM-1",
        batch="BATCH-DOC",
        version="VER-DOC",
        row_no=1,
        actual_shipped_qty=12,
        gross_weight_kg=5.5,
        volume_m3=0,
    )
    item_created = FakeDoc(
        "Overseas Cost Item",
        "ITEM-NEW",
        batch="BATCH-DOC",
        version="VER-DOC",
        row_no=2,
        material_code="SKU-NEW",
        product_name="新增物料",
        spec_model="",
        source_attachment_id="MANUAL-PARSED",
    )

    class FakeDB:
        @staticmethod
        def exists(doctype, name):
            return name in FakeFrappe.docs.get(doctype, {})

        @staticmethod
        def set_value(*_args, **_kwargs):
            return None

        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()
        session = type("Session", (), {"user": "tester@example.com"})()
        docs = {
            "Overseas Cost Attachment": {"MANUAL-PARSED": attachment},
            "Overseas Cost Item": {"ITEM-1": item_existing, "ITEM-NEW": item_created},
        }
        audit_logs: list[FakeDoc] = []

        @staticmethod
        def get_doc(doctype, name=None):
            if isinstance(doctype, dict):
                return FakeDoc(**doctype)
            return FakeFrappe.docs[doctype][name]

        @staticmethod
        def delete_doc(doctype, name, **_kwargs):
            FakeFrappe.docs.get(doctype, {}).pop(name, None)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_service,
        "_recalculate_after_writeback",
        lambda **_kwargs: {"ok": True, "action": "recalculated", "message": "已重算"},
    )

    result = delete_manual_document_attachment("MANUAL-PARSED")

    assert result["ok"] is True
    assert item_existing.actual_shipped_qty == 0
    assert item_existing.gross_weight_kg == 0
    assert "ITEM-NEW" not in FakeFrappe.docs["Overseas Cost Item"]
    assert "MANUAL-PARSED" not in FakeFrappe.docs["Overseas Cost Attachment"]
    assert result["rollback"]["rolled_back_field_count"] == 2
    assert result["rollback"]["deleted_item_count"] == 1


def test_parse_oa_packing_list_attachments_summarizes_dingtalk_permission_error(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "ATTACH-XLSX",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-XLSX",
            "file_name": "CI&PL.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-XLSX", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        }
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    def fake_download(_attachment_name, **_kwargs):
        return {
            "ok": False,
            "message": "钉钉接口 HTTP 403：AccessTokenPermissionDenied，应用尚未开通所需的权限：[Workflow.Instance.Write]",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)

    result = parse_oa_packing_list_attachments(recalculate=False)

    assert result["ok"] is False
    assert result["failed_count"] == 1
    assert result["permission_blocked_count"] == 1
    assert result["permission_scopes"] == ["Workflow.Instance.Write"]
    assert result["items"][0]["error_type"] == "dingtalk_permission"
    assert "缺少 Workflow.Instance.Write 权限" in result["message"]


def test_parse_oa_packing_list_attachments_summarizes_file_access_error(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "ATTACH-XLSX",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "source_doc_no": "OA-001::FILE-XLSX",
            "file_name": "CI&PL.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-XLSX", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        }
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    def fake_download(_attachment_name, **_kwargs):
        return {
            "ok": False,
            "error_type": "dingtalk_attachment_file_access",
            "message": "钉钉已找到这份审批附件，但当前配置的下载账号没有该附件的文件级访问权限。",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)

    result = parse_oa_packing_list_attachments(recalculate=False)

    assert result["ok"] is False
    assert result["failed_count"] == 1
    assert result["file_access_blocked_count"] == 1
    assert result["items"][0]["error_type"] == "dingtalk_attachment_file_access"
    assert "当前下载账号没有文件级访问权限" in result["message"]


def test_parse_oa_packing_list_attachments_stops_repeated_downloads_after_permission_error(monkeypatch) -> None:
    from overseas_costing.services import import_service

    rows = [
        {
            "name": "ATTACH-XLSX-1",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "file_name": "CI&PL-1.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-1", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        },
        {
            "name": "ATTACH-XLSX-2",
            "batch": "BATCH-DOC",
            "version": "VER-DOC",
            "source_type": "OA",
            "attachment_type": "Packing List",
            "file_name": "CI&PL-2.xlsx",
            "file_url": "",
            "parse_status": "Queued",
            "parse_result_json": json.dumps({"file_id": "FILE-2", "file_ext": "xlsx"}, ensure_ascii=False),
            "mapped_result_json": json.dumps({"parse_targets": ["actual_shipped_qty"]}, ensure_ascii=False),
        },
    ]

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return rows

    download_calls: list[str] = []

    def fake_download(attachment_name, **_kwargs):
        download_calls.append(attachment_name)
        return {
            "ok": False,
            "message": "钉钉接口 HTTP 403：AccessTokenPermissionDenied，应用尚未开通所需的权限：[Workflow.Instance.Write]",
        }

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "download_oa_form_attachment", fake_download)

    result = parse_oa_packing_list_attachments(recalculate=False)

    assert download_calls == ["ATTACH-XLSX-1"]
    assert result["failed_count"] == 1
    assert result["permission_blocked_count"] == 2
    assert result["items"][1]["action"] == "blocked"
    assert result["items"][1]["error_type"] == "dingtalk_permission"
    assert "暂不重复请求下载" in result["items"][1]["reason"]


def test_parse_packing_list_attachment_returns_parse_plan() -> None:
    result = parse_packing_list_attachment(
        batch_name="BATCH-002",
        attachment_name="packing-list.xlsx",
        template_hint="sea_container_sheet",
        sheet_rows_json=(
            '[{"物料编码":"CW000175","实际发货数量":400,"毛重KG":32.5,"体积m3":0.21}]'
        ),
    )

    assert result["ok"] is True
    assert result["mapped_preview_count"] == 1
    assert result["mapped_preview_items"][0]["actual_shipped_qty"] == 400
    assert result["parse_task"]["parser_strategy"] == "sea_container_sheet"


def test_preview_packing_list_attachment_resolves_file_url_by_keyword(monkeypatch) -> None:
    from overseas_costing.services import import_service

    calls: list[dict] = []

    def fake_resolve_excel_file_path(**kwargs):
        calls.append(kwargs)
        return Path("packing.xlsx")

    def fake_parse(_path):
        return (
            {"sourceSheet": "CI&PL", "parser": "oa_attachment_detail"},
            [
                {
                    "id": "PO-001",
                    "sourceSheet": "CI&PL",
                    "sourceTemplate": "oa_attachment_detail",
                    "items": [
                        [
                            "YL000098",
                            "TPU",
                            None,
                            5000,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            {"actualShippedQty": 5000, "grossWeightKg": 1200},
                        ]
                    ],
                }
            ],
        )

    monkeypatch.setattr(import_service, "_resolve_excel_file_path", fake_resolve_excel_file_path)
    monkeypatch.setattr(import_service, "parse_yuewei_excel_workbook", fake_parse)

    result = preview_packing_list_attachment(batch_name="BATCH-002", file_url="/private/files/packing.xlsx")

    assert result["ok"] is True
    assert calls == [{"file_url": "/private/files/packing.xlsx"}]
    assert result["mapped_preview_items"][0]["actual_shipped_qty"] == 5000


def test_apply_packing_list_fillable_fields_fills_safe_fields_and_leaves_conflicts(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeItemDoc:
        def __init__(self, **values):
            self.__dict__.update(values)
            self.save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload
            self.name = f"AUDIT-{len(audit_payloads) + 1}"

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    items = {
        "ITEM-001": FakeItemDoc(
            name="ITEM-001",
            row_no=1,
            material_code="CW000175",
            product_name="钢化膜",
            spec_model="透明",
            actual_shipped_qty=0,
            gross_weight_kg=0,
            volume_m3=0,
            volume_weight_kg=0,
            chargeable_weight_kg=0,
        ),
        "ITEM-002": FakeItemDoc(
            name="ITEM-002",
            row_no=2,
            material_code="CW000176",
            product_name="钢化膜",
            spec_model="磨砂",
            actual_shipped_qty=200,
            gross_weight_kg=10,
            volume_m3=0,
            volume_weight_kg=0,
            chargeable_weight_kg=0,
        ),
    }
    batch_updates = {}
    audit_payloads = []
    commit_count = {"value": 0}
    recalculate_calls = []

    def fake_recalculate_batch(**kwargs):
        recalculate_calls.append(kwargs)
        return {
            "ok": True,
            "summary_snapshot": {"total_gross_weight_kg": 32.5},
            "message": "重算完成",
        }

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-PACKING":
                if fields == ["current_version"]:
                    return {"current_version": "VER-PACKING"}
                return {"name": "BATCH-PACKING"} if as_dict else "BATCH-PACKING"
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            batch_updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype != "Overseas Cost Item":
                return []
            return [
                {
                    "name": item.name,
                    "row_no": item.row_no,
                    "material_code": item.material_code,
                    "product_name": item.product_name,
                    "spec_model": item.spec_model,
                    "actual_shipped_qty": item.actual_shipped_qty,
                    "gross_weight_kg": item.gross_weight_kg,
                    "volume_m3": item.volume_m3,
                    "volume_weight_kg": item.volume_weight_kg,
                    "chargeable_weight_kg": item.chargeable_weight_kg,
                }
                for item in items.values()
            ]

        @staticmethod
        def get_doc(*args):
            if len(args) == 2 and args[0] == "Overseas Cost Item":
                return items[args[1]]
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeAuditDoc(args[0])
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        "overseas_costing.services.calculate_service.recalculate_batch",
        fake_recalculate_batch,
    )
    rows_json = (
        '[{"物料编码":"CW000175","物料名称":"钢化膜","规格型号":"透明","实际发货数量":400,"毛重KG":32.5,"体积m3":0.21},'
        '{"物料编码":"CW000176","物料名称":"钢化膜","规格型号":"磨砂","实际发货数量":220,"毛重KG":12,"体积m3":0.22}]'
    )

    preview = preview_packing_list_attachment(
        batch_name="BATCH-PACKING",
        attachment_name="packing.xlsx",
        sheet_rows_json=rows_json,
        trusted_server_payload=True,
    )
    result = apply_packing_list_fillable_fields(
        batch_name="BATCH-PACKING",
        attachment_name="ATT-PACKING",
        sheet_rows_json=rows_json,
        trusted_server_payload=True,
    )

    assert preview["writeback_preview"]["fillable_row_count"] == 2
    assert preview["writeback_preview"]["conflict_row_count"] == 1
    assert result["ok"] is True
    assert result["updated_count"] == 2
    assert items["ITEM-001"].actual_shipped_qty == 400
    assert items["ITEM-001"].gross_weight_kg == 32.5
    assert items["ITEM-001"].volume_m3 == 0.21
    assert items["ITEM-002"].actual_shipped_qty == 200
    assert items["ITEM-002"].gross_weight_kg == 10
    assert items["ITEM-002"].volume_m3 == 0.22
    assert batch_updates[("Overseas Cost Batch", "BATCH-PACKING", "status")] == "Dirty"
    assert batch_updates[("Overseas Cost Attachment", "ATT-PACKING", "parse_status")] == "Parsed"
    assert result["attachment_marked_parsed"] is True
    assert commit_count["value"] == 1
    assert len(audit_payloads) == 4
    assert recalculate_calls == [{"batch_name": "BATCH-PACKING", "version_name": "VER-PACKING"}]
    assert result["recalculate_result"]["action"] == "recalculated"
    assert result["recalculate_result"]["summary_snapshot"] == {"total_gross_weight_kg": 32.5}
    assert "已自动重算" in result["message"]


def test_apply_packing_list_duplicate_rows_matches_by_quantity(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeItemDoc:
        def __init__(self, **values):
            self.__dict__.update(values)

        def save(self, **_kwargs):
            return self

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    items = {
        "ITEM-ROW-2": FakeItemDoc(
            name="ITEM-ROW-2",
            row_no=2,
            material_code="",
            product_name="超队指环扣",
            spec_model="",
            quantity=86400,
            actual_shipped_qty=0,
            gross_weight_kg=0,
            volume_m3=0,
            volume_weight_kg=0,
            chargeable_weight_kg=0,
            unit_price=0,
            purchase_currency="",
            goods_value=0,
        ),
        "ITEM-ROW-3": FakeItemDoc(
            name="ITEM-ROW-3",
            row_no=3,
            material_code="",
            product_name="超队指环扣",
            spec_model="",
            quantity=3600,
            actual_shipped_qty=0,
            gross_weight_kg=0,
            volume_m3=0,
            volume_weight_kg=0,
            chargeable_weight_kg=0,
            unit_price=0,
            purchase_currency="",
            goods_value=0,
        ),
    }
    batch_updates = {}
    audit_payloads = []
    commit_count = {"value": 0}

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-PACKING":
                if fields == ["current_version"]:
                    return {"current_version": "VER-PACKING"}
                return {"name": "BATCH-PACKING"} if as_dict else "BATCH-PACKING"
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            batch_updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Version":
                return []
            if doctype != "Overseas Cost Item":
                return []
            return [
                {
                    "name": item.name,
                    "row_no": item.row_no,
                    "material_code": item.material_code,
                    "product_name": item.product_name,
                    "spec_model": item.spec_model,
                    "quantity": item.quantity,
                    "actual_shipped_qty": item.actual_shipped_qty,
                    "gross_weight_kg": item.gross_weight_kg,
                    "volume_m3": item.volume_m3,
                    "volume_weight_kg": item.volume_weight_kg,
                    "chargeable_weight_kg": item.chargeable_weight_kg,
                    "unit_price": item.unit_price,
                    "purchase_currency": item.purchase_currency,
                    "goods_value": item.goods_value,
                }
                for item in items.values()
            ]

        @staticmethod
        def get_doc(*args):
            if len(args) == 2 and args[0] == "Overseas Cost Item":
                return items[args[1]]
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeAuditDoc(args[0])
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    rows_json = (
        '[{"物料名称":"超队指环扣","数量":86400,"毛重KG":19.4,"体积m3":0.02001,"单价":0.619,"总价（RMB)":53481.6},'
        '{"物料名称":"超队指环扣","数量":3600,"毛重KG":14.5,"体积m3":0.00864,"单价":0.619,"总价（RMB)":2228.4}]'
    )

    preview = preview_packing_list_attachment(
        batch_name="BATCH-PACKING",
        attachment_name="packing.xlsx",
        sheet_rows_json=rows_json,
        trusted_server_payload=True,
    )
    result = apply_packing_list_fillable_fields(
        batch_name="BATCH-PACKING",
        attachment_name="ATT-PACKING",
        sheet_rows_json=rows_json,
        trusted_server_payload=True,
    )

    assert preview["writeback_preview"]["matched_count"] == 2
    assert preview["writeback_preview"]["ambiguous_count"] == 0
    assert preview["writeback_preview"]["fillable_row_count"] == 2
    assert result["ok"] is True
    assert result["updated_count"] == 2
    assert items["ITEM-ROW-2"].actual_shipped_qty == 86400
    assert items["ITEM-ROW-2"].gross_weight_kg == 19.4
    assert items["ITEM-ROW-2"].unit_price == 0.619
    assert items["ITEM-ROW-2"].purchase_currency == "人民币RMB"
    assert items["ITEM-ROW-2"].goods_value == 53481.6
    assert items["ITEM-ROW-2"].source_type == "ATTACHMENT_PRICE"
    assert items["ITEM-ROW-2"].source_file_name == "ATT-PACKING"
    assert items["ITEM-ROW-2"].source_attachment_id == "ATT-PACKING"
    assert items["ITEM-ROW-2"].source_doc_no == "ATT-PACKING"
    assert items["ITEM-ROW-2"].parse_status == "SUCCESS"
    assert items["ITEM-ROW-3"].actual_shipped_qty == 3600
    assert items["ITEM-ROW-3"].gross_weight_kg == 14.5
    assert items["ITEM-ROW-3"].unit_price == 0.619
    assert items["ITEM-ROW-3"].purchase_currency == "人民币RMB"
    assert items["ITEM-ROW-3"].goods_value == 2228.4
    assert items["ITEM-ROW-3"].source_type == "ATTACHMENT_PRICE"
    assert result["price_source_row_count"] == 2
    assert batch_updates[("Overseas Cost Attachment", "ATT-PACKING", "mapped_result_json")]
    assert commit_count["value"] == 1
    assert len(audit_payloads) == 22


def test_apply_packing_list_auto_creates_unmatched_items_once(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeMeta:
        @staticmethod
        def has_field(_fieldname):
            return True

    class FakeItemDoc:
        def __init__(self, values):
            self.__dict__.update(values)

        def insert(self, **_kwargs):
            self.name = f"ITEM-AUTO-{len(items) + 1:03d}"
            items[self.name] = self
            return self

        def save(self, **_kwargs):
            return self

    class FakeAuditDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    items = {}
    audit_payloads = []
    batch_updates = {}
    commit_count = {"value": 0}

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-PACKING":
                if fields == ["current_version"]:
                    return {"current_version": "VER-PACKING"}
                return {"name": "BATCH-PACKING"} if as_dict else "BATCH-PACKING"
            if doctype == "Overseas Cost Attachment" and name_or_filters == "ATT-PACKING":
                return {
                    "name": "ATT-PACKING",
                    "file_name": "CI&PL.xlsx",
                    "source_doc_no": "ATT-PACKING",
                }
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            batch_updates[(doctype, name, fieldname)] = value

        @staticmethod
        def exists(doctype, name):
            return doctype == "Overseas Cost Attachment" and name == "ATT-PACKING"

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_meta(_doctype):
            return FakeMeta()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Version":
                return []
            if doctype != "Overseas Cost Item":
                return []
            return [
                {
                    "name": item.name,
                    "row_no": item.row_no,
                    "material_code": item.material_code,
                    "product_name": item.product_name,
                    "spec_model": item.spec_model,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "purchase_currency": item.purchase_currency,
                    "goods_value": item.goods_value,
                    "excel_row_no": item.excel_row_no,
                    "actual_shipped_qty": item.actual_shipped_qty,
                    "gross_weight_kg": item.gross_weight_kg,
                    "volume_m3": item.volume_m3,
                    "volume_weight_kg": item.volume_weight_kg,
                    "chargeable_weight_kg": item.chargeable_weight_kg,
                    "source_type": item.source_type,
                    "source_doc_no": item.source_doc_no,
                    "source_file_name": item.source_file_name,
                    "source_attachment_id": item.source_attachment_id,
                    "parse_status": item.parse_status,
                }
                for item in items.values()
            ]

        @staticmethod
        def get_doc(*args):
            if len(args) == 1 and isinstance(args[0], dict):
                if args[0].get("doctype") == "Overseas Cost Item":
                    return FakeItemDoc(args[0])
                return FakeAuditDoc(args[0])
            if len(args) == 2 and args[0] == "Overseas Cost Item":
                return items[args[1]]
            raise AssertionError(args)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    rows_json = (
        '[{"物料编码":"物料编码","物料名称":"物料名称","规格型号":"规格型号","数量":"数量"},'
        '{"sourceRow":3,"物料编码":"GJ003786","物料名称":"太阳眼镜","规格型号":"黑色","数量":10,"毛重KG":2.2,"体积m3":0.03}]'
    )

    first_result = apply_packing_list_fillable_fields(
        batch_name="BATCH-PACKING",
        attachment_name="ATT-PACKING",
        sheet_rows_json=rows_json,
        recalculate_after_writeback=False,
        auto_create_unmatched_items=True,
        trusted_server_payload=True,
    )
    second_result = apply_packing_list_fillable_fields(
        batch_name="BATCH-PACKING",
        attachment_name="ATT-PACKING",
        sheet_rows_json=rows_json,
        recalculate_after_writeback=False,
        auto_create_unmatched_items=True,
        trusted_server_payload=True,
    )

    assert first_result["created_count"] == 1
    assert first_result["skipped_count"] == 1
    assert second_result["created_count"] == 0
    assert len(items) == 1
    item = next(iter(items.values()))
    assert item.material_code == "GJ003786"
    assert item.product_name == "太阳眼镜"
    assert item.spec_model == "黑色"
    assert item.quantity == 10
    assert item.actual_shipped_qty == 10
    assert item.gross_weight_kg == 2.2
    assert item.volume_m3 == 0.03
    assert item.source_type == "PACKING_LIST"
    assert item.source_attachment_id == "ATT-PACKING"
    assert batch_updates[("Overseas Cost Batch", "BATCH-PACKING", "status")] == "Dirty"
    assert commit_count["value"] == 1
    assert len(audit_payloads) == 1


def test_preview_tax_certificate_pdf_extracts_pedimento_tax_summary_and_items() -> None:
    sample_text = """
Ped. 6000151
9 17.79570 23540.000 160
26  16  1681  6000151
A1
01/04/2026
FECHA DE PAGO: 01/04/2026
DTA 0 5532 PRV 0 330
IVA/PRV 0 53 IVA 0 113244
IGI/IGE 0 10724
IMPORTE PAGADO:
$129,883
NUM. CFDI O DOCUMENTO EQUIVALENTE FECHA INCOTERM MONEDA FACT VAL. MON. FACT
COVE2680NE2K1
LX20251231001
31/12/2025 CIF USD 38,858.55
NO. (GUIA/ORDEN EMBARQUE)/ID: SZCN60111600 M
NUMERO / TIPO HPCU5155607 3
PEDIMENTO REF: MZ260108
39079101001 00 0 1 1 5,000.000 1 5,000.00000 CHN CHN
PLASTICO TPU EN FORMAS PRIMARIAS
IGI 0.00000 1 0 0
IVA 16.00000 1 0 32719
202871 40.57420202871
39232991005 01 0 1 6 512,000.000 1 896.00000 CHN CHN
BOLSAS DE PLASTICO
IGI 7.00000 1 0 2105
IVA 16.00000 1 0 5186
30068 0.0587330068
********* FIN DE PEDIMENTO NUM. TOTAL DE PARTIDAS: CLAVE PREVALIDADOR: 01002****** ****** *********
"""

    result = preview_tax_certificate_pdf(source_name="PD_MZ260108凭证.pdf", text=sample_text)

    assert result["ok"] is True
    assert result["header"]["pedimento_no"] == "26 16 1681 6000151"
    assert result["header"]["pedimento_short_no"] == "6000151"
    assert result["header"]["pedimento_ref"] == "MZ260108"
    assert result["header"]["container_no"] == "HPCU5155607"
    assert result["header"]["payment_date"] == "01/04/2026"
    assert result["header"]["payment_date_source"] == "explicit_label"
    assert result["header"]["exchange_rate"] == 17.7957
    assert result["tax_totals"]["dta_mxn"] == 5532
    assert result["tax_totals"]["iva_mxn"] == 113244
    assert result["summary"]["tax_total_sum_mxn"] == 129883
    assert result["summary"]["tax_total_matches_paid_total"] is True
    assert result["summary"]["declared_item_count"] == 2
    assert result["summary"]["validation_status"] == "passed"
    assert result["validation"]["status_label"] == "通过"
    assert result["summary"]["item_count"] == 2
    assert result["line_items"][0]["hs_code"] == "39079101"
    assert result["line_items"][0]["import_name"] == "PLASTICO TPU EN FORMAS PRIMARIAS"
    assert result["line_items"][0]["taxes"]["iva_amount_mxn"] == 32719
    assert result["reconciliation"]["status"] == "pending"
    assert result["reconciliation"]["voucher"]["paid_total_mxn"] == 129883


def test_tax_certificate_does_not_treat_first_document_date_as_payment_date() -> None:
    parsed = attachment_parse_service.parse_tax_certificate_text(
        "Ped. 6000151\n01/04/2026\nIMPORTE PAGADO:\n$129,883",
    )
    assert parsed["header"]["entry_date"] == "01/04/2026"
    assert parsed["header"]["payment_date"] == ""
    assert parsed["header"]["payment_date_source"] == ""


def test_preview_tax_certificate_pdf_flags_failed_validation_for_amount_mismatch() -> None:
    sample_text = """
Ped. 6000151
9 17.79570 23540.000 160
26  16  1681  6000151
01/04/2026
DTA 0 5532 PRV 0 330
IVA/PRV 0 53 IVA 0 113244
IGI/IGE 0 10724
IMPORTE PAGADO:
$129,884
NUMERO / TIPO HPCU5155607 3
PEDIMENTO REF: MZ260108
39079101001 00 0 1 1 5,000.000 1 5,000.00000 CHN CHN
PLASTICO TPU EN FORMAS PRIMARIAS
IGI 0.00000 1 0 0
IVA 16.00000 1 0 32719
202871 40.57420202871
********* FIN DE PEDIMENTO NUM. TOTAL DE PARTIDAS: CLAVE PREVALIDADOR: 01001****** ****** *********
"""

    result = preview_tax_certificate_pdf(source_name="bad-tax.pdf", text=sample_text)
    amount_check = next(check for check in result["validation"]["checks"] if check["code"] == "tax_total_matches_paid")

    assert result["summary"]["tax_total_sum_mxn"] == 129883
    assert result["summary"]["paid_total_mxn"] == 129884
    assert result["validation"]["status"] == "failed"
    assert result["summary"]["needs_manual_review"] is True
    assert amount_check["status"] == "failed"


def test_save_tax_certificate_parse_result_returns_dry_run_without_frappe() -> None:
    sample_text = """
Ped. 6000151
9 17.79570 23540.000 160
26  16  1681  6000151
01/04/2026
DTA 0 5532 PRV 0 330
IVA/PRV 0 53 IVA 0 113244
IGI/IGE 0 10724
IMPORTE PAGADO:
$129,883
NUMERO / TIPO HPCU5155607 3
PEDIMENTO REF: MZ260108
39079101001 00 0 1 1 5,000.000 1 5,000.00000 CHN CHN
PLASTICO TPU EN FORMAS PRIMARIAS
IGI 0.00000 1 0 0
IVA 16.00000 1 0 32719
202871 40.57420202871
********* FIN DE PEDIMENTO NUM. TOTAL DE PARTIDAS: CLAVE PREVALIDADOR: 01001****** ****** *********
"""

    result = save_tax_certificate_parse_result(
        source_name="PD_MZ260108凭证.pdf",
        text=sample_text,
        batch_name="HPCU5155607",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["saved"] is False
    assert result["preview"]["header"]["pedimento_no"] == "26 16 1681 6000151"
    assert result["preview"]["reconciliation"]["voucher"]["paid_total_mxn"] == 129883


def test_save_tax_certificate_parse_result_updates_version_fx_by_payment_date(monkeypatch) -> None:
    version_row = {"fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 2.6, "remark": ""}
    batch_updates = {}
    attachment_payloads = []
    audit_payloads = []
    commit_count = {"value": 0}

    class FakeDoc:
        def __init__(self, payload):
            self.__dict__.update(payload)
            self.payload = payload
            self.name = payload.get("name") or f"DOC-{len(attachment_payloads) + len(audit_payloads) + 1}"

        def insert(self, **_kwargs):
            if self.payload.get("doctype") == "Overseas Cost Attachment":
                attachment_payloads.append(self.payload)
            elif self.payload.get("doctype") == "Overseas Cost Audit Log":
                audit_payloads.append(self.payload)
            return self

        def save(self, **_kwargs):
            self.payload["parse_result_json"] = getattr(self, "parse_result_json", None)
            return self

    class FakeDB:
        @staticmethod
        def sql(query, *_args, **_kwargs):
            if "tabOverseas Cost Attachment" in query:
                return [{"parse_result_json": row["parse_result_json"]} for row in attachment_payloads]
            return []

        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Version" and name_or_filters == "VER-001":
                if as_dict:
                    return dict(version_row)
                return version_row.get(fields)
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value=None, **_kwargs):
            if doctype == "Overseas Cost Version" and name == "VER-001":
                updates = fieldname if isinstance(fieldname, dict) else {fieldname: value}
                version_row.update(updates)
            else:
                batch_updates[(doctype, name, json.dumps(fieldname, ensure_ascii=False, default=str))] = value

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeFrappe:
        db = FakeDB()

        class local:
            site = "development.localhost"

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, filters=None, fields=None, **_kwargs):
            if doctype == "Overseas Cost Batch":
                values = {
                    "name": "BATCH-001",
                    "batch_no": "HPCU5155607",
                    "customs_no": "26 16 1681 6000151",
                    "waybill_no": "HPCU5155607",
                    "container_no": "HPCU5155607",
                    "current_version": "VER-001",
                    "item_count": 2,
                    "total_goods_value": 0,
                    "estimated_total_cost_rmb": 0,
                    "actual_total_cost_rmb": 0,
                }
                if filters and any(value in {"26 16 1681 6000151", "HPCU5155607", "BATCH-001"} for value in filters.values()):
                    return [values]
                return []
            if doctype == "Overseas Cost Item":
                return []
            if doctype == "Overseas Cost Attachment":
                return [{"parse_result_json": row["parse_result_json"]} for row in attachment_payloads]
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    sample_text = """
Ped. 6000151
9 17.79570 23540.000 160
26  16  1681  6000151
01/04/2026
FECHA DE PAGO: 01/04/2026
DTA 0 5532 PRV 0 330
IVA/PRV 0 53 IVA 0 113244
IGI/IGE 0 10724
IMPORTE PAGADO:
$129,883
NUMERO / TIPO HPCU5155607 3
PEDIMENTO REF: MZ260108
39079101001 00 0 1 1 5,000.000 1 5,000.00000 CHN CHN
PLASTICO TPU EN FORMAS PRIMARIAS
IGI 0.00000 1 0 0
IVA 16.00000 1 0 32719
202871 40.57420202871
********* FIN DE PEDIMENTO NUM. TOTAL DE PARTIDAS: CLAVE PREVALIDADOR: 01001****** ****** *********
"""

    from overseas_costing.services import cost_preview_service

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)
    monkeypatch.setattr(attachment_parse_service, "_", lambda message: message)
    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    monkeypatch.setattr(
        attachment_parse_service, "_refresh_costing_after_fx_sync",
        lambda **_kwargs: {"action": "refreshed", "recalculated": True},
    )

    def fake_fx_context(*, payment_date=None, approval_finished_at=None):
        assert payment_date == "01/04/2026"
        assert approval_finished_at == ""
        return {
            "ok": True,
            "action": "resolved",
            "payment_date": payment_date,
            "normalized_payment_date": "2026-04-01",
            "normalized_fx_rate_date": "2026-04-01",
            "fx_date_source": "payment_date",
            "fx_date_source_label": "真实付款日",
            "is_estimated_rate": False,
            "source": "fx-rate-api",
            "fx_usd_to_rmb": 6.8,
            "fx_mxn_to_rmb": 0.4,
            "fx_rmb_to_mxn": 2.5,
            "rate_snapshots": {
                "USD": {"ok": True, "currency": "USD", "rate_date": "2026-04-01", "cny_per_unit": 6.8},
                "MXN": {"ok": True, "currency": "MXN", "rate_date": "2026-04-01", "cny_per_unit": 0.4},
            },
        }

    monkeypatch.setattr(attachment_parse_service.fx_rate_service, "build_fx_context_for_costing", fake_fx_context)

    result = attachment_parse_service.save_tax_certificate_parse_result(
        source_name="PD_MZ260108.pdf",
        text=sample_text,
        batch_name="HPCU5155607",
    )

    assert result["ok"] is True
    assert result["saved"] is True
    assert result["fx_sync"]["action"] == "updated"
    assert result["fx_sync"]["normalized_payment_date"] == "2026-04-01"
    assert result["fx_sync"]["normalized_fx_rate_date"] == "2026-04-01"
    assert result["fx_sync"]["fx_date_source_label"] == "真实付款日"
    assert result["fx_sync"]["voucher_usd_to_mxn"] == pytest.approx(17.7957)
    assert version_row["fx_usd_to_rmb"] == pytest.approx(6.8)
    assert version_row["fx_rmb_to_mxn"] == pytest.approx(2.5)
    fx_snapshot = json.loads(version_row["extra_json"])["costing_fx_snapshot"]
    assert fx_snapshot["date_source"] == "payment_date"
    assert fx_snapshot["rates"]["MXN"]["rate_date"] == "2026-04-01"
    assert "PD_MZ260108.pdf" in version_row["remark"]
    assert "汇率日期 2026-04-01（真实付款日）" in version_row["remark"]
    assert batch_updates[("Overseas Cost Batch", "BATCH-001", '"status"')] == "Dirty"
    assert len(attachment_payloads) == 1
    assert len(audit_payloads) == 2
    assert {row["field_name"] for row in audit_payloads} == {"fx_usd_to_rmb", "fx_rmb_to_mxn"}
    assert commit_count["value"] == 1


def test_tax_certificate_conflicting_date_is_saved_without_changing_cost(monkeypatch) -> None:
    from overseas_costing.services import cost_preview_service

    attachments = [{"parse_result_json": '{"header":{"payment_date":"2026-10-29","payment_date_source":"explicit_label"}}'}]
    transaction = {"commits": 0, "rollbacks": 0}

    class FakeDoc:
        def __init__(self, payload):
            self.__dict__.update(payload)
            self.payload = payload
            self.name = "NEW-CERT"

        def insert(self, **_kwargs):
            attachments.append(self.payload)
            return self

        def save(self, **_kwargs):
            self.payload["parse_result_json"] = self.parse_result_json
            return self

    class FakeFrappe:
        class db:
            @staticmethod
            def sql(query, *_args, **_kwargs):
                if "tabOverseas Cost Attachment" in query:
                    return list(attachments)
                return []

            @staticmethod
            def commit():
                transaction["commits"] += 1

            @staticmethod
            def rollback():
                transaction["rollbacks"] += 1

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

        @staticmethod
        def get_all(_doctype, **kwargs):
            start = kwargs.get("limit_start", 0)
            return attachments[start:start + kwargs.get("limit_page_length", 200)]

    batch = {"name": "B1", "current_version": "V1"}
    parsed = {"header": {"payment_date": "2026-10-30", "payment_date_source": "explicit_label"}, "reconciliation": {"batch": batch}}
    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)
    monkeypatch.setattr(attachment_parse_service, "_", lambda message: message)
    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    monkeypatch.setattr(cost_preview_service, "_", lambda message: message)
    monkeypatch.setattr(attachment_parse_service, "_has_frappe_db_context", lambda: True)
    monkeypatch.setattr(attachment_parse_service, "preview_tax_certificate_pdf", lambda **_kwargs: parsed)
    monkeypatch.setattr(attachment_parse_service, "_sync_tax_certificate_identity", lambda **_kwargs: {})
    monkeypatch.setattr(attachment_parse_service, "_find_existing_tax_certificate_attachment", lambda _values: None)
    monkeypatch.setattr(attachment_parse_service, "_build_tax_certificate_attachment_values", lambda **_kwargs: {
        "batch": "B1", "parse_result_json": '{"header":{"payment_date":"2026-10-30","payment_date_source":"explicit_label"}}',
    })
    def unexpected_fx_sync(**_kwargs):
        raise AssertionError("conflicting dates must not update the version")

    monkeypatch.setattr(attachment_parse_service, "_sync_tax_certificate_exchange_rate_to_version", unexpected_fx_sync)

    result = attachment_parse_service.save_tax_certificate_parse_result(batch_name="B1")
    assert result["saved"] is True
    assert result["fx_sync"]["action"] == "skipped"
    assert "不同的完税凭证付款日" in result["fx_sync"]["reason"]
    assert transaction == {"commits": 1, "rollbacks": 0}

    attachments.clear()
    monkeypatch.setattr(attachment_parse_service, "_sync_tax_certificate_exchange_rate_to_version", lambda **_kwargs: {
        "action": "updated",
    })
    monkeypatch.setattr(attachment_parse_service, "_refresh_costing_after_fx_sync", lambda **_kwargs: {
        "action": "failed", "message": "重算失败",
    })
    with pytest.raises(ValueError, match="重算失败"):
        attachment_parse_service.save_tax_certificate_parse_result(batch_name="B1")
    assert transaction == {"commits": 1, "rollbacks": 1}


def test_sync_tax_certificate_fx_uses_explicit_payment_approval_finished_at(monkeypatch) -> None:
    assert attachment_parse_service._batch_approval_finished_at({
        "source_finished_at": "2026-04-01 17:16:00",
    }) == ""
    version_row = {"fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "remark": ""}
    batch_updates = {}
    audit_payloads = []

    class FakeDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Version" and name_or_filters == "VER-001":
                if as_dict:
                    return dict(version_row)
                return version_row.get(fields)
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value=None, **_kwargs):
            if doctype == "Overseas Cost Version" and name == "VER-001":
                updates = fieldname if isinstance(fieldname, dict) else {fieldname: value}
                version_row.update(updates)
            else:
                batch_updates[(doctype, name, json.dumps(fieldname, ensure_ascii=False, default=str))] = value

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    def fake_fx_context(*, payment_date=None, approval_finished_at=None):
        assert payment_date == ""
        assert approval_finished_at == "2026-04-21 17:16:00"
        return {
            "ok": True,
            "action": "resolved",
            "payment_date": "",
            "approval_finished_at": approval_finished_at,
            "normalized_payment_date": "",
            "normalized_approval_finished_at": "2026-04-21",
            "normalized_fx_rate_date": "2026-04-21",
            "fx_date_source": "approval_finished_at",
            "fx_date_source_label": "付款审批完成日（暂估）",
            "is_estimated_rate": True,
            "source": "fx-rate-api",
            "fx_usd_to_rmb": 6.9,
            "fx_mxn_to_rmb": 0.4,
            "fx_rmb_to_mxn": 2.5,
            "rate_snapshots": {
                "USD": {"ok": True, "currency": "USD", "rate_date": "2026-04-21", "cny_per_unit": 6.9},
                "MXN": {"ok": True, "currency": "MXN", "rate_date": "2026-04-21", "cny_per_unit": 0.4},
            },
        }

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)
    monkeypatch.setattr(attachment_parse_service.fx_rate_service, "build_fx_context_for_costing", fake_fx_context)

    result = attachment_parse_service._sync_tax_certificate_exchange_rate_to_version(
        parsed={"header": {"payment_date": "", "exchange_rate": "17.5", "pedimento_no": "26 16 1681 6000151"}},
        batch={"name": "BATCH-001", "current_version": "VER-001",
               "source_finished_at": "2026-04-01 17:16:00",
               "payment_approval_finished_at": "2026-04-21 17:16:00"},
        source_name="PD_MZ260108.pdf",
    )

    assert result["action"] == "updated"
    assert result["normalized_payment_date"] == ""
    assert result["normalized_approval_finished_at"] == "2026-04-21"
    assert result["normalized_fx_rate_date"] == "2026-04-21"
    assert result["fx_date_source"] == "approval_finished_at"
    assert result["fx_date_source_label"] == "付款审批完成日（暂估）"
    assert result["is_estimated_rate"] is True
    assert result["message"].startswith("已按付款审批完成日暂估汇率")
    assert version_row["fx_usd_to_rmb"] == pytest.approx(6.9)
    assert version_row["fx_rmb_to_mxn"] == pytest.approx(2.5)
    assert "汇率日期 2026-04-21（付款审批完成日（暂估））" in version_row["remark"]
    assert batch_updates[("Overseas Cost Batch", "BATCH-001", '"status"')] == "Dirty"
    assert {row["field_name"] for row in audit_payloads} == {"fx_usd_to_rmb", "fx_rmb_to_mxn"}


def test_tax_certificate_refresh_defers_commit_to_attachment_save(monkeypatch) -> None:
    from overseas_costing.services import calculate_service, import_service

    calls = {}

    def fake_purchase_sync(**kwargs):
        calls["purchase"] = kwargs
        return {"ok": True, "updated_count": 0}

    def fake_recalculate(**kwargs):
        calls["recalculate"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(import_service, "apply_linked_purchase_expense_fillable_fields", fake_purchase_sync)
    monkeypatch.setattr(calculate_service, "recalculate_batch", fake_recalculate)

    result = attachment_parse_service._refresh_costing_after_fx_sync(
        fx_sync={"action": "updated"}, batch={"name": "B1", "current_version": "V1"},
    )
    assert result["recalculated"] is True
    assert calls["purchase"]["commit_after_writeback"] is False
    assert calls["recalculate"]["commit_after_recalculate"] is False

    monkeypatch.setattr(import_service, "apply_linked_purchase_expense_fillable_fields", lambda **_kwargs: {
        "ok": False, "message": "采购同步失败",
    })
    failed = attachment_parse_service._refresh_costing_after_fx_sync(
        fx_sync={"action": "updated"}, batch={"name": "B1", "current_version": "V1"},
    )
    assert failed["action"] == "failed"
    assert failed["message"] == "采购同步失败"


def test_get_tax_certificate_parse_record_enriches_missing_fx_with_version_fallback(monkeypatch) -> None:
    parse_result_json = attachment_parse_service._json_dumps(
        {
            "header": {"payment_date": "01/04/2026"},
            "fx_sync": {
                "action": "skipped",
                "reason": "付款日汇率缺失。",
                "payment_date": "01/04/2026",
                "normalized_payment_date": "2026-04-01",
                "rate_snapshots": {
                    "USD": {"ok": False, "action": "http_error", "message": "汇率接口 HTTP 错误：404"},
                    "MXN": {"ok": False, "action": "http_error", "message": "汇率接口 HTTP 错误：404"},
                },
            },
        }
    )
    mapped_result_json = attachment_parse_service._json_dumps(
        {
            "batch": {
                "name": "BATCH-001",
                "batch_no": "HPCU5155607",
                "current_version": "VER-001",
            }
        }
    )

    class FakeDB:
        @staticmethod
        def exists(doctype, name):
            return doctype == "Overseas Cost Attachment" and name == "ATT-001"

        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Attachment" and name_or_filters == "ATT-001":
                return {
                    "name": "ATT-001",
                    "batch": "BATCH-001",
                    "version": "VER-001",
                    "source_type": "Voucher",
                    "attachment_type": "Tax Certificate",
                    "source_doc_no": "26 16 1681 6000151",
                    "file_name": "PD_MZ260108凭证.pdf",
                    "file_url": "/private/files/PD_MZ260108凭证.pdf",
                    "parse_status": "Parsed",
                    "parse_result_json": parse_result_json,
                    "mapped_result_json": mapped_result_json,
                    "modified": "2026-08-06 09:01:22",
                    "creation": "2026-08-06 09:01:22",
                }
            if doctype == "Overseas Cost Version" and name_or_filters == "VER-001":
                return {"fx_usd_to_rmb": 7.178751, "fx_rmb_to_mxn": 2.6}
            return None

    class FakeFrappe:
        db = FakeDB()

        class local:
            site = "development.localhost"

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)

    result = attachment_parse_service.get_tax_certificate_parse_record(record_name="ATT-001")

    fx_sync = result["parse_result"]["fx_sync"]
    assert fx_sync["usd_to_rmb"] == pytest.approx(7.178751)
    assert fx_sync["rmb_to_mxn"] == pytest.approx(2.6)
    assert fx_sync["fallback_rate_source_label"] == "当前版本汇率（暂用）"
    assert "汇率库缺少 2026-04-01 的 USD/MXN 汇率" in fx_sync["fallback_message"]
    assert fx_sync["fx_date_source_label"] == "真实付款日"


def test_get_tax_certificate_parse_record_resolves_version_from_batch_for_legacy_record(monkeypatch) -> None:
    parse_result_json = attachment_parse_service._json_dumps(
        {
            "header": {"payment_date": "01/04/2026"},
            "fx_sync": {
                "action": "skipped",
                "reason": "付款日汇率缺失。",
                "payment_date": "01/04/2026",
                "normalized_payment_date": "2026-04-01",
            },
        }
    )
    mapped_result_json = attachment_parse_service._json_dumps(
        {
            "batch": {
                "name": "BATCH-001",
                "batch_no": "HPCU5155607",
            }
        }
    )

    class FakeDB:
        @staticmethod
        def exists(doctype, name):
            return doctype == "Overseas Cost Attachment" and name == "ATT-001"

        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Attachment" and name_or_filters == "ATT-001":
                return {
                    "name": "ATT-001",
                    "batch": "BATCH-001",
                    "version": "",
                    "source_type": "Voucher",
                    "attachment_type": "Tax Certificate",
                    "source_doc_no": "26 16 1681 6000151",
                    "file_name": "PD_MZ260108凭证.pdf",
                    "file_url": "/private/files/PD_MZ260108凭证.pdf",
                    "parse_status": "Parsed",
                    "parse_result_json": parse_result_json,
                    "mapped_result_json": mapped_result_json,
                    "modified": "2026-08-06 09:01:22",
                    "creation": "2026-08-06 09:01:22",
                }
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-001":
                return "VER-001"
            if doctype == "Overseas Cost Version" and name_or_filters == "VER-001":
                return {"fx_usd_to_rmb": 7.178751, "fx_rmb_to_mxn": 2.6}
            return None

    class FakeFrappe:
        db = FakeDB()

        class local:
            site = "development.localhost"

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)

    result = attachment_parse_service.get_tax_certificate_parse_record(record_name="ATT-001")

    fx_sync = result["parse_result"]["fx_sync"]
    assert fx_sync["usd_to_rmb"] == pytest.approx(7.178751)
    assert fx_sync["fallback_rate_source"] == "current_version"


def test_sync_saved_tax_certificate_fx_fallback_updates_snapshot_only(monkeypatch) -> None:
    parse_result_json = attachment_parse_service._json_dumps(
        {
            "header": {"payment_date": "01/04/2026"},
            "fx_sync": {
                "action": "skipped",
                "reason": "付款日汇率缺失。",
                "payment_date": "01/04/2026",
                "normalized_payment_date": "2026-04-01",
            },
        }
    )
    mapped_result_json = attachment_parse_service._json_dumps({"batch": {"name": "BATCH-001"}})
    updates = {}
    committed = {"value": False}

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Attachment" and name_or_filters == "ATT-001":
                return {
                    "name": "ATT-001",
                    "batch": "BATCH-001",
                    "version": "",
                    "parse_result_json": parse_result_json,
                    "mapped_result_json": mapped_result_json,
                }
            if doctype == "Overseas Cost Batch" and name_or_filters == "BATCH-001":
                return "VER-001"
            if doctype == "Overseas Cost Version" and name_or_filters == "VER-001":
                return {"fx_usd_to_rmb": 7.178751, "fx_rmb_to_mxn": 2.6}
            return None

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            updates[(doctype, name, fieldname)] = value

        @staticmethod
        def commit():
            committed["value"] = True

    class FakeFrappe:
        db = FakeDB()

        class local:
            site = "development.localhost"

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)

    result = attachment_parse_service.sync_saved_tax_certificate_fx_fallback(record_name="ATT-001")

    assert result["updated_count"] == 1
    assert committed["value"] is True
    saved = attachment_parse_service._json_loads(
        updates[("Overseas Cost Attachment", "ATT-001", "parse_result_json")]
    )
    assert saved["fx_sync"]["fallback_rate_source"] == "current_version"
    assert saved["fx_sync"]["usd_to_rmb"] == pytest.approx(7.178751)


def test_list_tax_certificate_parse_records_returns_empty_without_frappe() -> None:
    result = list_tax_certificate_parse_records(batch_name="HPCU5155607")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["items"] == []


def test_get_tax_certificate_parse_record_requires_frappe_context() -> None:
    result = get_tax_certificate_parse_record(record_name="ATT-001")

    assert result["ok"] is False
    assert result["dry_run"] is True
    assert result["record_name"] == "ATT-001"


def test_tax_certificate_record_summary_extracts_business_fields() -> None:
    row = {
        "name": "ATT-001",
        "batch": "BATCH-001",
        "version": "VER-001",
        "source_doc_no": "26 16 1681 6000151",
        "file_name": "PD_MZ260108凭证.pdf",
        "file_url": "/private/files/PD_MZ260108凭证.pdf",
        "parse_status": "Parsed",
        "modified": "2026-07-20 10:00:00",
        "parse_result_json": attachment_parse_service._json_dumps(
            {
                "summary": {"item_count": 22, "declared_item_count": 22, "paid_total_mxn": 129883},
                "header": {"pedimento_no": "26 16 1681 6000151", "container_no": "HPCU5155607", "payment_date": "01/04/2026"},
                "validation": {"status": "passed", "status_label": "通过"},
            }
        ),
        "mapped_result_json": attachment_parse_service._json_dumps(
            {
                "status": "review",
                "status_label": "需复核",
                "batch": {"name": "BATCH-001", "batch_no": "HPCU5155607", "customs_no": "26 16 1681 6000151"},
                "system": {"system_import_tax_total_mxn": 130186},
                "difference": {"tax_total_diff_mxn": -303, "direction_label": "凭证金额低于系统"},
                "review_count": 1,
                "passed_count": 3,
            }
        ),
    }

    result = attachment_parse_service._build_tax_certificate_record_summary(row)

    assert result["name"] == "ATT-001"
    assert result["customs_no"] == "26 16 1681 6000151"
    assert result["container_no"] == "HPCU5155607"
    assert result["paid_total_mxn"] == 129883
    assert result["system_tax_total_mxn"] == 130186
    assert result["tax_total_diff_mxn"] == -303
    assert result["reconciliation_status_label"] == "需复核"


def test_tax_certificate_reconciliation_preview_calculates_difference_without_writeback() -> None:
    parsed = {
        "header": {
            "pedimento_no": "26 16 1681 6000151",
            "container_no": "HPCU5155607",
            "payment_date": "01/04/2026",
        },
        "summary": {
            "paid_total_mxn": 220,
            "tax_total_sum_mxn": 220,
            "item_count": 2,
            "declared_item_count": 2,
        },
        "validation": {"status": "passed", "status_label": "通过"},
    }
    result = _build_tax_certificate_reconciliation(
        parsed=parsed,
        batch={
            "name": "BATCH-001",
            "batch_no": "HPCU5155607",
            "customs_no": "26 16 1681 6000151",
            "waybill_no": "HPCU5155607",
            "item_count": 2,
        },
        items=[
            {"row_no": 1, "import_tax_total": 120, "hs_code": "39079101"},
            {"row_no": 2, "igi_amount": 30, "iva_amount": 40, "dta": 10, "hs_code": "39232991"},
        ],
    )

    assert result["status"] == "review"
    assert result["system"]["system_import_tax_total_mxn"] == 200
    assert result["difference"]["tax_total_diff_mxn"] == 20
    assert result["difference"]["direction_label"] == "凭证金额高于系统"
    assert result["message"].startswith("对比结果仅用于复核")


def test_tax_certificate_manual_resolution_can_use_voucher_total() -> None:
    result = _build_tax_certificate_manual_resolution(
        mapped_result={
            "voucher": {"paid_total_mxn": 129883},
            "system": {"system_import_tax_total_mxn": 130186},
            "difference": {"tax_total_diff_mxn": -303},
        },
        resolution_action="use_voucher",
        remark="按正式完税凭证处理",
        operator_name="tester",
    )

    assert result["ok"] is True
    resolution = result["resolution"]
    assert resolution["action_label"] == "按凭证金额为准"
    assert resolution["final_tax_total_mxn"] == 129883
    assert resolution["final_diff_vs_system_mxn"] == -303
    assert resolution["final_diff_vs_voucher_mxn"] == 0


def test_tax_certificate_manual_resolution_requires_adjusted_amount() -> None:
    result = _build_tax_certificate_manual_resolution(
        mapped_result={
            "voucher": {"paid_total_mxn": 129883},
            "system": {"system_import_tax_total_mxn": 130186},
            "difference": {"tax_total_diff_mxn": -303},
        },
        resolution_action="manual_adjust",
    )

    assert result["ok"] is False
    assert "手工调整" in result["message"]


def test_tax_certificate_manual_resolution_requires_exception_remark() -> None:
    result = _build_tax_certificate_manual_resolution(
        mapped_result={
            "voucher": {"paid_total_mxn": 129883},
            "system": {"system_import_tax_total_mxn": 130186},
            "difference": {"tax_total_diff_mxn": -303},
        },
        resolution_action="mark_exception",
    )

    assert result["ok"] is False
    assert "异常原因" in result["message"]


def test_tax_certificate_batch_lookup_prefers_voucher_header_over_requested_batch(monkeypatch) -> None:
    class FakeFrappe:
        @staticmethod
        def get_all(_doctype, filters=None, **_kwargs):
            if filters == {"customs_no": "26 16 1681 6000151"}:
                return [
                    {
                        "name": "BATCH-CUSTOMS",
                        "batch_no": "HPCU5155607",
                        "customs_no": "26 16 1681 6000151",
                        "waybill_no": "HPCU5155607",
                    }
                ]
            if filters == {"name": "CURRENT-BATCH"}:
                return [
                    {
                        "name": "CURRENT-BATCH",
                        "batch_no": "202606301549000536602",
                        "customs_no": "",
                        "waybill_no": "",
                    }
                ]
            return []

    monkeypatch.setattr(attachment_parse_service, "frappe", FakeFrappe)

    result = attachment_parse_service._find_tax_certificate_batch(
        {"pedimento_no": "26 16 1681 6000151", "container_no": "HPCU5155607"},
        batch_name="CURRENT-BATCH",
    )

    assert result["name"] == "BATCH-CUSTOMS"
    assert result["customs_no"] == "26 16 1681 6000151"


def test_build_packing_list_parse_task_defaults_to_multi_template_router() -> None:
    task = build_packing_list_parse_task(batch_name="BATCH-003")

    assert task["parser_strategy"] == "mixed_workbook_router"
    assert "volume_m3" in task["parse_targets"]
    assert "unit_price" in task["parse_targets"]
    assert "purchase_currency" in task["parse_targets"]
    assert "goods_value" in task["parse_targets"]


def test_import_main_excel_returns_yuewei_block_preview_without_frappe(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "frappe", None)
    result = import_main_excel(
        source_name="墨西哥进口物料综合成本核算.xlsx",
        source_sheet="2026年YUEWEI",
        blocks_json=(
            '[{"id":"HPCU5155607","sourceSheet":"2026年YUEWEI","sourceRange":"2026年YUEWEI!79:100",'
            '"customsNo":"26 16 1681 6000151","waybillNo":"HPCU5155607","transportMode":"海运",'
            '"items":[["YL000098","TPU-HF-8695AU",14.3575,5000,71787.5,'
            '"PLASTICO TPU EN FORMAS PRIMARIAS","39079101","00",null,null,null,{"grossWeightKg":1200}]]}]'
        ),
    )

    assert result["ok"] is True
    assert result["queued"] is False
    assert result["preview_batches"][0]["batch_no"] == "HPCU5155607"
    assert result["preview_batches"][0]["mapped_preview_items"][0]["material_code"] == "YL000098"
    assert result["preview_batches"][0]["mapped_preview_items"][0]["transport_mode"] == "SEA"


def test_imported_cost_version_has_no_implicit_exchange_rates(monkeypatch) -> None:
    from overseas_costing.services import import_service

    inserted = []

    class FakeDB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return None

    class FakeDoc:
        def __init__(self, values):
            self.values = values

        def insert(self, **_kwargs):
            inserted.append(self.values)
            return self

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return []

        @staticmethod
        def get_doc(values):
            return FakeDoc(values)

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    import_service._resolve_or_create_excel_version(
        batch_doc_name="B1", version_type="Estimated",
        fx_usd_to_rmb=None, fx_rmb_to_mxn=None,
    )
    assert inserted[0]["fx_usd_to_rmb"] is None
    assert inserted[0]["fx_rmb_to_mxn"] is None


def test_reimport_without_rates_preserves_existing_version_rates(monkeypatch) -> None:
    from overseas_costing.services import import_service

    updates = []

    class FakeDB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return "V1"

        @staticmethod
        def set_value(doctype, name, values, *_args, **_kwargs):
            updates.append((doctype, name, values))

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{"name": "V1"}]

        @staticmethod
        def get_doc(*_args, **_kwargs):
            return object()

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    import_service._resolve_or_create_excel_version(
        batch_doc_name="B1", version_type="Estimated",
        fx_usd_to_rmb=None, fx_rmb_to_mxn=None,
    )
    updated_version = next(values for doctype, _name, values in updates if doctype == "Overseas Cost Version" and isinstance(values, dict))
    assert "fx_usd_to_rmb" not in updated_version
    assert "fx_rmb_to_mxn" not in updated_version


def test_purchase_amount_conversion_uses_resolved_fx_without_defaults(monkeypatch) -> None:
    from overseas_costing.services import cost_preview_service, import_service

    class FakeFrappe:
        class db:
            @staticmethod
            def get_value(*_args, **_kwargs):
                return {"batch": "B1", "fx_usd_to_rmb": None, "fx_rmb_to_mxn": None}

    class FakeRepository:
        persisted = 0

        def prepare_fx_resolution(self, batch, version):
            assert (batch, version) == ("B1", "V1")
            return {"ok": True, "fx_usd_to_rmb": 6.6583, "fx_rmb_to_mxn": 2.837751}

        def persist_fx_resolution(self, context, _resolution):
            assert context == {"batch": "B1", "version": "V1"}
            self.persisted += 1

    repository = FakeRepository()
    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(cost_preview_service, "FrappeCostRepository", lambda: repository)
    context = import_service._get_version_fx_context("V1", persist=True)
    assert context == {"fx_usd_to_rmb": 6.6583, "fx_rmb_to_mxn": 2.837751}
    assert repository.persisted == 1
    assert import_service._amount_to_rmb(100, "MXN", context) == pytest.approx(100 / 2.837751)


def test_purchase_apply_without_changed_rows_does_not_persist_fx(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeFrappe:
        class db:
            @staticmethod
            def commit():
                raise AssertionError("no rows changed, so no commit is allowed")

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", lambda **_kwargs: {
        "ok": True, "batch_doc_name": "B1", "version_name": "V1",
        "writeback_preview": {"matched_rows": []},
    })
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *_args: None)
    def unexpected_fx_resolution(*_args, **_kwargs):
        raise AssertionError("no FX resolution is needed")

    monkeypatch.setattr(import_service, "_get_version_fx_context", unexpected_fx_resolution)

    result = import_service.apply_linked_purchase_expense_fillable_fields(batch_name="B1")
    assert result["ok"] is True
    assert result["updated_count"] == 0


def test_purchase_apply_defers_fx_commit_when_called_from_voucher(monkeypatch) -> None:
    from overseas_costing.services import cost_preview_service, import_service

    events = []

    class FakeFrappe:
        class db:
            @staticmethod
            def sql(query, values):
                assert "tabOverseas Cost Batch" in query
                assert values == ("B1",)
                events.append("batch_locked")

            @staticmethod
            def commit():
                events.append("commit")

    class FakeRepository:
        @staticmethod
        def persist_fx_resolution(context, resolution):
            assert context == {"batch": "B1", "version": "V1"}
            assert resolution["batch_name"] == "B1"
            events.append("fx_persisted")

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)
    monkeypatch.setattr(cost_preview_service, "FrappeCostRepository", FakeRepository)
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", lambda **_kwargs: {
        "ok": True, "batch_doc_name": "B1", "version_name": "V1",
        "writeback_preview": {"matched_rows": [{
            "target_item_name": "I1", "business_changes": [{
                "field_name": "unit_price", "new_value": 10, "status": "fillable",
            }],
        }]},
    })
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *_args: None)
    monkeypatch.setattr(import_service, "_get_version_fx_context", lambda *_args, **_kwargs: {
        "fx_usd_to_rmb": 6.7, "fx_rmb_to_mxn": 2.8,
        "_resolution": {"batch_name": "B1", "version_name": "V1"},
    })
    monkeypatch.setattr(import_service, "_update_item_fields", lambda **_kwargs: [{"field_name": "unit_price"}])
    monkeypatch.setattr(import_service, "_mark_batch_dirty", lambda _name: events.append("batch_dirty"))

    result = import_service.apply_linked_purchase_expense_fillable_fields(
        batch_name="B1", recalculate_after_writeback=False, commit_after_writeback=False,
    )
    assert result["updated_count"] == 1
    assert events == ["batch_locked", "fx_persisted", "batch_dirty"]


def test_coerce_item_numeric_defaults_keeps_formula_cache_blanks_importable() -> None:
    normalized = _coerce_item_numeric_defaults(
        {
            "material_code": "YL000098",
            "unit_price": None,
            "quantity": "",
            "goods_value": None,
            "gross_weight_kg": 1200,
        }
    )

    assert normalized["unit_price"] == 0
    assert normalized["quantity"] == 0
    assert normalized["goods_value"] == 0
    assert normalized["gross_weight_kg"] == 1200


def test_values_equal_for_import_treats_numeric_strings_as_same_value() -> None:
    assert _values_equal_for_import("2.60", 2.6) is True
    assert _values_equal_for_import("", None) is True
    assert _values_equal_for_import(" YL000098 ", "YL000098") is True
    assert _values_equal_for_import("2.61", 2.6) is False


def test_ensure_supported_excel_path_rejects_non_excel_suffix() -> None:
    xlsx_path = Path("成本表.xlsx")
    pdf_path = Path("成本表.pdf")

    assert _ensure_supported_excel_path(xlsx_path) == xlsx_path

    try:
        _ensure_supported_excel_path(pdf_path)
    except ValueError as exc:
        assert ".xlsx / .xlsm" in str(exc)
    else:
        raise AssertionError("PDF 文件不应通过 Excel 导入扩展名校验")


def test_preview_yuewei_excel_file_returns_selected_batches_without_import(monkeypatch) -> None:
    from overseas_costing.services import import_service

    def fake_parse(path: Path, sheet_name: str | None = None):
        assert path == Path("sample.xlsx")
        assert sheet_name is None
        return (
            {"sourceSheet": "7月份钢化膜空运", "parser": "oa_attachment_detail"},
            [
                {
                    "id": "PO-001",
                    "batchNo": "PO-001",
                    "sourceSheet": "7月份钢化膜空运",
                    "transportMode": "空运双清包税",
                    "items": [["FL004106", "钢化膜", 1.2, 500, 600]],
                }
            ],
        )

    monkeypatch.setattr(import_service, "_resolve_excel_file_path", lambda **_kwargs: Path("sample.xlsx"))
    monkeypatch.setattr(import_service, "parse_yuewei_excel_workbook", fake_parse)

    result = preview_yuewei_excel_file(file_url="/private/files/sample.xlsx")

    assert result["ok"] is True
    assert result["source_summary"]["block_count"] == 1
    assert result["selected_summary"]["block_count"] == 1
    assert result["selected_summary"]["item_count"] == 1
    assert result["preview_batches"][0]["batch_no"] == "PO-001"


def test_preview_batch_excel_supplement_rejects_foreign_batch(monkeypatch) -> None:
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_service, "_resolve_excel_file_path", lambda **_kwargs: Path("supplement.xlsx"))
    monkeypatch.setattr(
        import_service,
        "parse_yuewei_excel_workbook",
        lambda path, sheet_name=None: (
            {"sourceSheet": "成本"},
            [
                {"id": "CURRENT-001", "batchNo": "CURRENT-001", "items": [["SKU-1", "产品一", 10, 2, 20]]},
                {"id": "OTHER-002", "batchNo": "OTHER-002", "items": [["SKU-2", "产品二", 20, 1, 20]]},
            ],
        ),
    )
    monkeypatch.setattr(
        import_service,
        "_batch_identity_map",
        lambda batch_name: {
            "id": {"CURRENT-001"},
            "batchNo": {"CURRENT-001"},
            "customsNo": set(),
            "waybillNo": set(),
        },
    )

    result = import_service.preview_batch_excel_supplement(
        "CURRENT-001", file_url="/private/files/supplement.xlsx"
    )

    assert result["ok"] is False
    assert result["foreign_batches"] == ["OTHER-002"]


def test_batch_supplement_rejects_conflicting_secondary_identity() -> None:
    from overseas_costing.services.import_service import _excel_block_identity_conflicts

    conflicts = _excel_block_identity_conflicts(
        {
            "batchNo": "CURRENT-001",
            "waybillNo": "OTHER-WAYBILL",
        },
        {
            "id": {"BATCH-DOC", "CURRENT-001", "CURRENT-WAYBILL"},
            "batchNo": {"BATCH-DOC", "CURRENT-001"},
            "customsNo": {"CURRENT-CUSTOMS"},
            "waybillNo": {"CURRENT-WAYBILL"},
        },
    )

    assert conflicts == ["waybillNo=OTHER-WAYBILL"]


def test_batch_supplement_rejects_conflicting_block_id_even_when_batch_no_matches() -> None:
    from overseas_costing.services.import_service import _excel_block_identity_conflicts

    conflicts = _excel_block_identity_conflicts(
        {
            "id": "OTHER-001",
            "batchNo": "CURRENT-001",
        },
        {
            "id": {"BATCH-DOC", "CURRENT-001"},
            "batchNo": {"BATCH-DOC", "CURRENT-001"},
            "customsNo": set(),
            "waybillNo": set(),
        },
    )

    assert conflicts == ["id=OTHER-001"]


def test_batch_supplement_only_writes_nonempty_non_identity_values() -> None:
    from overseas_costing.services.import_service import _batch_supplement_update_values

    values = _batch_supplement_update_values(
        {
            "row_no": 1,
            "material_code": "SKU-1",
            "product_name": "更新名称",
            "unit_price": None,
            "quantity": "",
            "goods_value": 0,
            "supplier": "供应商A",
        }
    )

    assert values == {"product_name": "更新名称", "goods_value": 0, "supplier": "供应商A"}


def test_batch_supplement_matches_existing_item_by_material_code_not_row_number(monkeypatch) -> None:
    from overseas_costing.services import import_service

    writes = []

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [
                {"name": "ITEM-A", "row_no": 1, "material_code": "SKU-A"},
                {"name": "ITEM-B", "row_no": 2, "material_code": "SKU-B"},
            ]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe())
    monkeypatch.setattr(import_service, "_filter_doctype_values", lambda _doctype, values: values)
    monkeypatch.setattr(
        import_service,
        "_update_item_fields",
        lambda **kwargs: writes.append(kwargs) or [{"field_name": "product_name"}],
    )

    result = import_service._apply_batch_supplement_items(
        batch_doc_name="BATCH-1",
        version_name="VER-1",
        mapped_rows=[{"row_no": 1, "material_code": "SKU-B", "product_name": "B 新名称", "unit_price": None}],
        raw_rows=[["SKU-B", "B 新名称"]],
    )

    assert result["ok"] is True
    assert result["created_count"] == 0
    assert writes[0]["item_name"] == "ITEM-B"
    assert writes[0]["field_updates"] == {"product_name": "B 新名称"}


def test_batch_supplement_rejects_unknown_material_before_any_write(monkeypatch) -> None:
    from overseas_costing.services import import_service

    writes = []

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{"name": "ITEM-A", "row_no": 1, "material_code": "SKU-A"}]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe())
    monkeypatch.setattr(import_service, "_update_item_fields", lambda **kwargs: writes.append(kwargs))

    result = import_service._apply_batch_supplement_items(
        batch_doc_name="BATCH-1",
        version_name="VER-1",
        mapped_rows=[{"material_code": "SKU-OTHER", "product_name": "不应写入"}],
        raw_rows=[["SKU-OTHER", "不应写入"]],
    )

    assert result["ok"] is False
    assert "未写入任何数据" in result["message"]
    assert writes == []


def test_batch_supplement_preview_reports_unknown_material_before_apply(monkeypatch) -> None:
    from overseas_costing.services import import_service

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{"name": "ITEM-A", "row_no": 1, "material_code": "SKU-A"}]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe())
    monkeypatch.setattr(
        import_service,
        "_prepare_batch_excel_supplement",
        lambda *args, **kwargs: {
            "ok": True,
            "batch_doc_name": "BATCH-1",
            "version_name": "VER-1",
            "mapped_rows": [{"material_code": "SKU-OTHER", "product_name": "未知物料"}],
            "raw_rows": [["SKU-OTHER", "未知物料"]],
            "conflicts": [],
        },
    )

    result = import_service.preview_batch_excel_supplement("BATCH-1", file_url="/private/files/supplement.xlsx")

    assert result["ok"] is False
    assert result["conflicts"] == ["当前批次不存在物料：SKU-OTHER"]
    assert "mapped_rows" not in result


def test_apply_batch_excel_supplement_passes_explicit_batch_to_safe_updater(monkeypatch) -> None:
    from overseas_costing.services import import_service

    captured = {}
    monkeypatch.setattr(
        import_service,
        "_prepare_batch_excel_supplement",
        lambda *args, **kwargs: {
            "ok": True,
            "batch_doc_name": "BATCH-DOC",
            "version_name": "VER-1",
            "mapped_rows": [{"material_code": "SKU-1"}],
            "raw_rows": [["SKU-1"]],
        },
    )
    monkeypatch.setattr(
        import_service,
        "_apply_batch_supplement_items",
        lambda **kwargs: captured.update(kwargs)
        or {
            "ok": True,
            "total_count": 1,
            "created_count": 0,
            "updated_count": 1,
            "unchanged_count": 0,
            "changed_field_count": 2,
        },
    )

    result = import_service.apply_batch_excel_supplement(
        "BATCH-DOC", file_url="/private/files/supplement.xlsx"
    )

    assert result["ok"] is True
    assert captured["batch_doc_name"] == "BATCH-DOC"
    assert captured["version_name"] == "VER-1"


def test_backfill_oa_item_details_from_raw_reads_row_snapshot(monkeypatch) -> None:
    from overseas_costing.services import import_service

    row = {
        "name": "ITEM-001",
        "batch": "BATCH-001",
        "row_no": 1,
        "material_code": "FL004116",
        "product_name": "",
        "product_name_es": "",
        "spec_model": "",
        "recipient": "",
        "quantity": 0.0,
        "unit": "",
        "category": "",
        "purchase_currency": "",
        "gross_weight_kg": 0.0,
        "goods_value": 0.0,
        "raw_excel_json": json.dumps(
            {
                "物料编码 Código de material": "FL004116",
                "物料名称（中文）Nombre del material (chino)": "笔式万用表",
                "数量Cantidad": "50",
                "重量 Peso": "12.85",
                "单位Unidad": "个",
                "货值Valor de mercancía": "1250",
                "币种Moneda": "人民币RMB",
                "物料类别TIPO": "Material物料",
            },
            ensure_ascii=False,
        ),
    }
    written = {}

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields=None, **_kwargs):
            return None

        @staticmethod
        def set_value(doctype, name, updates, **_kwargs):
            written[name] = updates

        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, filters=None, fields=None, **_kwargs):
            assert doctype == "Overseas Cost Item"
            assert filters == {"source_type": "oa_logistics"}
            return [row]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = backfill_oa_item_details_from_raw()

    assert result["updated_count"] == 1
    assert result["batch_fallback_count"] == 0
    assert written["ITEM-001"]["gross_weight_kg"] == 12.85
    assert written["ITEM-001"]["goods_value"] == 1250.0
    assert written["ITEM-001"]["product_name"] == "笔式万用表"
    assert written["ITEM-001"]["purchase_currency"] == "人民币RMB"
    assert written["ITEM-001"]["category"] == "Material物料"


def test_backfill_oa_item_details_from_raw_falls_back_to_batch_form(monkeypatch) -> None:
    from overseas_costing.services import import_service

    row = {
        "name": "ITEM-002",
        "batch": "vno4sb614j",
        "row_no": 1,
        "material_code": "FL004116",
        "product_name": "",
        "product_name_es": "",
        "spec_model": "",
        "recipient": "",
        "quantity": 50.0,
        "unit": "个",
        "gross_weight_kg": 0.0,
        "goods_value": 0.0,
        "raw_excel_json": "",
    }
    written = {}

    batch_extra = {
        "form_fields": {
            "货物信息Bienes": [
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "FL004116"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "笔式万用表"},
                        {"label": "数量Cantidad", "value": "50"},
                        {"label": "重量 Peso", "value": "12.85"},
                        {"label": "单位Unidad", "value": "个"},
                        {"label": "货值Valor de mercancía", "value": "1250"},
                    ]
                }
            ]
        },
        "transport_mode_raw": "DDP Marítimo海运DDP",
    }

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields=None, **_kwargs):
            assert doctype == "Overseas Cost Batch"
            assert name == "vno4sb614j"
            return json.dumps(batch_extra, ensure_ascii=False)

        @staticmethod
        def set_value(doctype, name, updates, **_kwargs):
            written[name] = updates

        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, filters=None, fields=None, **_kwargs):
            return [row]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = backfill_oa_item_details_from_raw()

    assert result["updated_count"] == 1
    assert result["batch_fallback_count"] == 1
    assert written["ITEM-002"]["gross_weight_kg"] == 12.85
    assert written["ITEM-002"]["goods_value"] == 1250.0


def test_backfill_oa_item_details_from_raw_does_not_overwrite_existing(monkeypatch) -> None:
    from overseas_costing.services import import_service

    row = {
        "name": "ITEM-003",
        "batch": "BATCH-003",
        "row_no": 1,
        "material_code": "FL004116",
        "product_name": "笔式万用表",
        "product_name_es": "",
        "spec_model": "",
        "recipient": "",
        "quantity": 99.0,
        "unit": "个",
        "gross_weight_kg": 8.8,
        "goods_value": 999.0,
        "raw_excel_json": json.dumps(
            {
                "物料编码 Código de material": "FL004116",
                "物料名称（中文）Nombre del material (chino)": "笔式万用表",
                "数量Cantidad": "50",
                "重量 Peso": "12.85",
                "货值Valor de mercancía": "1250",
            },
            ensure_ascii=False,
        ),
    }
    written = {}

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields=None, **_kwargs):
            return None

        @staticmethod
        def set_value(doctype, name, updates, **_kwargs):
            written[name] = updates

        @staticmethod
        def commit():
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, filters=None, fields=None, **_kwargs):
            return [row]

    monkeypatch.setattr(import_service, "frappe", FakeFrappe)

    result = backfill_oa_item_details_from_raw()

    # 所有字段都已填，回填不应覆盖任何值
    assert result["updated_count"] == 0
    assert "ITEM-003" not in written
