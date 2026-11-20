from __future__ import annotations

import json

import pytest


def _oa_attachment_row(name: str, *, version: str = "VERSION-1", archived: bool) -> dict:
    snapshot = {
        "process_instance_id": "PROC-MAIN",
        "file_id": "FILE-1",
    }
    if archived:
        snapshot["settlement_document"] = {
            "document_id": "DOC-1",
            "source_id": "SOURCE-1",
            "fingerprint": "FINGERPRINT-1",
            "manifest": {
                "process_instance_id": "PROC-MAIN",
                "file_id": "FILE-1",
                "sha256": "a" * 64,
            },
        }
    return {
        "name": name,
        "version": version,
        "file_name": "fuel.png",
        "file_url": "/private/files/fuel.png",
        "modified": "2026-09-21 12:53:11",
        "parse_result_json": json.dumps(snapshot),
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_local_attachment_map_prefers_current_fully_archived_row_independent_of_db_order(
    monkeypatch, reverse
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    rows = [
        _oa_attachment_row("ATT-TEMP", archived=False),
        _oa_attachment_row("ATT-CANONICAL", archived=True),
    ]
    if reverse:
        rows.reverse()

    class FakeFrappe:
        @staticmethod
        def get_list(*_args, **_kwargs):
            return rows

    monkeypatch.setattr(service, "frappe", FakeFrappe)

    result = service._local_attachment_map("BATCH-1", current_version="VERSION-1")

    assert result[("PROC-MAIN", "FILE-1")]["name"] == "ATT-CANONICAL"


@pytest.mark.parametrize("reverse", [False, True])
def test_local_attachment_map_groups_legacy_instance_id_with_canonical_archive(
    monkeypatch, reverse
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    temporary = _oa_attachment_row("ATT-TEMP", archived=False)
    temporary_snapshot = json.loads(temporary["parse_result_json"])
    temporary_snapshot["instance_id"] = temporary_snapshot.pop("process_instance_id")
    temporary["parse_result_json"] = json.dumps(temporary_snapshot)
    rows = [temporary, _oa_attachment_row("ATT-CANONICAL", archived=True)]
    if reverse:
        rows.reverse()

    class FakeFrappe:
        @staticmethod
        def get_list(*_args, **_kwargs):
            return rows

    monkeypatch.setattr(service, "frappe", FakeFrappe)

    result = service._local_attachment_map("BATCH-1", current_version="VERSION-1")

    assert set(result) == {("PROC-MAIN", "FILE-1")}
    assert result[("PROC-MAIN", "FILE-1")]["name"] == "ATT-CANONICAL"


def test_batch_detail_normalizes_main_linked_comments_and_archives(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Batch"
            assert name == "BATCH-1"
            return {
                "name": "BATCH-1",
                "batch_no": "OA-001",
                "source_type": "oa_logistics",
                "source_approval_no": "OA-001",
                "source_instance_id": "PROC-MAIN",
                "extra_json": json.dumps(
                    {"linked_purchase_approvals": [{"source_instance_id": "PROC-BUY"}]},
                    ensure_ascii=False,
                ),
            }

    class FakeFrappe:
        db = FakeDB()

    class FakeSource:
        calls = []

        def get_instance_bundle(self, instance_ids):
            self.calls.append(list(instance_ids))
            return {
                "instances": {
                    "PROC-MAIN": {
                        "processInstanceId": "PROC-MAIN",
                        "businessId": "OA-001",
                        "title": "国际物流",
                        "status": "COMPLETED",
                        "originatorUserId": "USER-1",
                        "originatorUserName": "张三",
                        "formComponentValues": [{"name": "物流方式", "value": "海运"}],
                        "operationRecords": [{
                            "userId": "USER-2",
                            "userName": "李四",
                            "date": "2026-09-01T10:00:00+08:00",
                            "remark": "规格33*20*23，重量42.05kg，1套模具",
                        }],
                    },
                    "PROC-BUY": {
                        "processInstanceId": "PROC-BUY",
                        "businessId": "OA-002",
                        "title": "采购支出",
                        "formComponentValues": [],
                    },
                },
                "attachments": [{
                    "process_instance_id": "PROC-MAIN",
                    "file_id": "FILE-1",
                    "file_name": "装箱计划.xlsx",
                    "attachment_origin": "form",
                    "archive_status": "archived",
                    "archive_method": "legacy_file_url",
                    "content_quality": "original",
                }],
                "health": {"source_lag_seconds": 9, "source_updated_at": "2026-09-04T06:00:00Z"},
            }

    source = FakeSource()
    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "_get_approval_source", lambda: source)
    monkeypatch.setattr(service, "_trusted_linked_instance_ids", lambda _payload: ["PROC-BUY"])

    result = service.get_batch_dingtalk_approval_detail("BATCH-1")

    assert source.calls == [["PROC-MAIN", "PROC-BUY"]]
    assert result["main_approval"]["form_fields"] == [{
        "label": "物流方式",
        "value": "海运",
        "component_type": "",
        "display_kind": "scalar",
        "is_empty": False,
        "table": {"columns": [], "rows": []},
    }]
    assert result["main_approval"]["attachments"][0]["archive_method"] == "legacy_file_url"
    assert result["main_approval"]["attachments"][0]["comment_user_name"] == ""
    assert result["main_approval"]["timeline"][0]["packing_candidate"] is True
    assert len(result["main_approval"]["timeline"][0]["source_id"]) == 64
    assert result["linked_purchase_approvals"][0]["instance_id"] == "PROC-BUY"
    assert result["source_lag_seconds"] == 9
    assert "raw_payload" not in result["main_approval"]


def test_missing_main_returns_structured_repair_state_not_false_unlinked_message(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "B1", "batch_no": "OA-1", "source_type": "oa_logistics",
                "source_approval_no": "OA-1", "source_instance_id": "PROC-MISSING", "extra_json": "{}",
            }

    class Source:
        @staticmethod
        def get_instance_bundle(_ids):
            return {"instances": {}, "attachments": [], "health": {}}

        @staticmethod
        def get_repair_statuses(_ids):
            return {"PROC-MISSING": {"status": "retry", "attempts": 2, "error_code": "HTTP_500", "error_message": "temporary"}}

    monkeypatch.setattr(service, "frappe", type("F", (), {"db": DB()})())
    monkeypatch.setattr(service, "_get_approval_source", lambda: Source())

    result = service.get_batch_dingtalk_approval_detail("B1")

    assert result["ok"] is False
    assert result["source_state"]["code"] == "repairing"
    assert result["source_state"]["purchase_link_state"] == "unknown"
    assert result["repair_status"]["attempts"] == 2
    assert "正在补同步" in result["message"]


def test_missing_postgres_config_returns_structured_unavailable_state(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "B1",
                "batch_no": "OA-1",
                "source_type": "oa_logistics",
                "source_approval_no": "OA-1",
                "source_instance_id": "PROC-1",
                "extra_json": "{}",
            }

    def fail_source():
        raise ValueError("missing PostgreSQL OA source config")

    monkeypatch.setattr(service, "frappe", type("F", (), {"db": DB()})())
    monkeypatch.setattr(service, "_get_approval_source", fail_source)

    result = service.get_batch_dingtalk_approval_detail("B1")

    assert result["ok"] is False
    assert result["data_source"] == "postgres"
    assert result["source_state"]["code"] == "data_source_unavailable"
    assert result["source_state"]["failure_code"] == "missing_postgres_config"
    assert result["source_state"]["repairable"] is False
    assert "PostgreSQL OA" in result["message"]


def test_main_payload_links_are_trusted_even_when_old_batch_trace_is_empty(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "B1", "batch_no": "OA-1", "source_type": "oa_logistics",
                "source_approval_no": "OA-1", "source_instance_id": "MAIN", "extra_json": "{}",
            }

    class Source:
        calls = []

        def get_instance_bundle(self, ids):
            self.calls.append(list(ids))
            if len(self.calls) == 1:
                return {"instances": {"MAIN": {"processInstanceId": "MAIN", "businessId": "OA-1", "status": "RUNNING"}}, "attachments": [], "health": {}}
            return {"instances": {"BUY": {"processInstanceId": "BUY", "businessId": "PUR-1", "status": "COMPLETED", "result": "agree"}}, "attachments": [], "health": {}}

    source = Source()
    monkeypatch.setattr(service, "frappe", type("F", (), {"db": DB()})())
    monkeypatch.setattr(service, "_get_approval_source", lambda: source)
    monkeypatch.setattr(service, "_trusted_linked_instance_ids", lambda _payload: ["BUY"])

    result = service.get_batch_dingtalk_approval_detail("B1")

    assert source.calls == [["MAIN"], ["BUY"]]
    assert result["source_state"]["purchase_link_state"] == "available"
    assert result["linked_purchase_approvals"][0]["instance_id"] == "BUY"


def test_form_fields_render_structured_values_without_download_credentials() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    fields = service._form_fields({
        "formComponentValues": [
            {
                "name": "装箱单附件",
                "value": json.dumps([{
                    "fileName": "装箱计划.xlsx",
                    "fileId": "FILE-SECRET",
                    "spaceId": "SPACE-SECRET",
                    "thumbnail": {"authMediaId": "AUTH-SECRET"},
                }], ensure_ascii=False),
            },
            {
                "name": "货物信息",
                "value": json.dumps([{
                    "rowNumber": "ROW-SECRET",
                    "rowValue": [
                        {"label": "物料编码", "value": "FL000429", "key": "FIELD-SECRET"},
                        {"label": "数量", "value": "100000", "key": "FIELD-SECRET-2"},
                    ],
                }], ensure_ascii=False),
            },
        ],
    })

    assert fields == [
        {
            "label": "装箱单附件",
            "value": "装箱计划.xlsx",
            "component_type": "",
            "display_kind": "scalar",
            "is_empty": False,
            "table": {"columns": [], "rows": []},
        },
        {
            "label": "货物信息",
            "value": "物料编码：FL000429；数量：100000",
            "component_type": "",
            "display_kind": "scalar",
            "is_empty": False,
            "table": {"columns": [], "rows": []},
        },
    ]
    rendered = json.dumps(fields, ensure_ascii=False)
    assert "SECRET" not in rendered


def test_safe_form_value_uses_importer_attachment_aliases_for_nested_payloads() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    value = [
        {
            "fileName": "报价单.pdf",
            "fileUrl": "https://files.example/a?X-Amz-Signature=SECRET-URL-1",
        },
        {
            "file_name": "packing.xlsx",
            "file_url": "https://files.example/b?sig=SECRET-URL-2",
        },
        {
            "fileName": {"downloadUrl": "SECRET-NESTED-NAME"},
            "fileId": "SECRET-NESTED-FILE-ID",
        },
        {
            "fileName": "",
            "fileUrl": "https://files.example/d?signature=SECRET-EMPTY-NAME-URL",
        },
        {
            "fileName": "https://files.example/e?token=SECRET-IN-FILE-NAME",
            "fileId": "FILE-5",
        },
    ]

    sanitized = service._safe_form_value(value)

    assert sanitized == [
        "报价单.pdf",
        "packing.xlsx",
        "审批附件",
        "审批附件",
        "审批附件（敏感内容已隐藏）",
    ]
    assert "SECRET" not in json.dumps(sanitized, ensure_ascii=False)


def test_safe_form_value_preserves_ordinary_name_and_id_business_object() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    project = {
        "name": "项目 A",
        "id": "P-1",
        "amount": 100,
        "currency": "MXN",
    }

    assert service._safe_form_value(project) == project


def test_form_fields_use_attachment_context_for_generic_name_and_id_aliases() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    field = service._form_fields({
        "formComponentValues": [{
            "name": "附件",
            "componentType": "DDAttachment",
            "value": json.dumps([
                {"name": "invoice.pdf", "downloadId": "SECRET-DOWNLOAD-ID"},
                {"title": "photo.jpg", "id": "SECRET-ID"},
            ], ensure_ascii=False),
        }],
    })[0]

    assert field["value"] == "invoice.pdf；photo.jpg"
    assert "SECRET" not in json.dumps(field, ensure_ascii=False)


@pytest.mark.parametrize(
    "malformed_value",
    [
        '{"fileUrl":"https://files.example/a?token=SECRET"',
        '{"fileName":"quote.pdf","downloadId":"SECRET"',
        'https://files.example/a?X-Amz-Signature=SECRET',
    ],
)
def test_display_value_hides_sensitive_attachment_text_when_json_is_malformed(
    malformed_value,
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    displayed = service._display_value(malformed_value)

    assert displayed == "审批附件（敏感内容已隐藏）"
    assert "SECRET" not in displayed


def test_display_value_keeps_non_sensitive_malformed_legacy_text() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    malformed_value = "legacy malformed ["

    assert service._display_value(malformed_value) == malformed_value


def test_display_value_uses_attachment_context_for_malformed_generic_attachment_aliases() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    malformed_attachment = '{"name":"invoice.pdf","id":"SECRET-ID"'
    malformed_business = '{"token":"board-vote"'

    assert service._display_value(
        malformed_attachment,
        attachment_context=True,
    ) == "审批附件（敏感内容已隐藏）"
    assert service._display_value(malformed_attachment) == malformed_attachment
    assert service._display_value(malformed_business) == malformed_business
    assert service._display_value(
        malformed_business,
        attachment_context=True,
    ) == "审批附件（敏感内容已隐藏）"


@pytest.mark.parametrize(
    "malformed_attachment",
    [
        '{"id":"SECRET-ID"',
        '{"url":"https://private.example/file"',
    ],
)
def test_display_value_hides_lone_attachment_identity_in_attachment_context(
    malformed_attachment,
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    assert service._display_value(
        malformed_attachment,
        attachment_context=True,
    ) == "审批附件（敏感内容已隐藏）"


@pytest.mark.parametrize(
    "malformed_credential",
    [
        '{"auth_code":"SECRET"',
        '{"auth_media_id":"SECRET"',
        '{"access-token":"SECRET"',
        '{"auth-code":"SECRET"',
        ' auth_code=SECRET',
        '\n\tauth_code=SECRET',
        '  "auth-code":"SECRET"',
    ],
)
def test_display_value_normalizes_truncated_technical_credential_keys(
    malformed_credential,
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    assert service._display_value(
        malformed_credential,
    ) == "审批附件（敏感内容已隐藏）"


def test_safe_form_value_normalizes_structured_hyphenated_technical_keys() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    sanitized = service._safe_form_value({
        "name": "普通业务",
        "access-token": "SECRET-ACCESS",
        "auth-code": "SECRET-AUTH",
    })

    assert sanitized == {"name": "普通业务"}


def test_unparsed_key_extraction_is_linear_without_key_value_separator() -> None:
    import time

    from overseas_costing.services import dingtalk_approval_service as service

    malformed_business = "a-" * 16_000

    started_at = time.perf_counter()
    displayed = service._display_value(malformed_business)
    elapsed = time.perf_counter() - started_at

    assert displayed == malformed_business
    assert elapsed < 0.5


def test_display_value_keeps_truncated_business_text_without_sensitive_keys() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    malformed_business = '{"name":"项目 A","status":"approved"'

    assert service._display_value(malformed_business) == malformed_business


def test_safe_form_value_only_hides_global_technical_credentials_and_signed_urls() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    sanitized = service._safe_form_value({
        "name": "普通业务",
        "accessToken": "SECRET-ACCESS-TOKEN",
        "authCode": "SECRET-AUTH-CODE",
        "authMediaId": "SECRET-AUTH-MEDIA",
        "url": "https://files.example/a?X-Amz-Signature=SECRET-SIGNATURE",
    })

    assert sanitized == {
        "name": "普通业务",
        "url": "审批附件（敏感内容已隐藏）",
    }
    assert "SECRET" not in json.dumps(sanitized, ensure_ascii=False)


def test_safe_form_value_preserves_business_signature_and_authorization_fields() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    contract = {
        "name": "合同",
        "signature": "张三签字",
        "authorization": "董事会批准",
        "auth": "法务授权",
        "token": "业务标识",
        "sig": "手写签名",
        "status": "approved",
    }

    assert service._safe_form_value(contract) == contract


def test_safe_form_value_hides_contextual_credentials_in_attachment_context() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    sanitized = service._safe_form_value({
        "label": "附件元数据",
        "signature": "SECRET-SIGNATURE",
        "authorization": "SECRET-AUTHORIZATION",
        "auth": "SECRET-AUTH",
        "token": "SECRET-TOKEN",
        "sig": "SECRET-SIG",
        "credential": "SECRET-CREDENTIAL",
    }, attachment_context=True)

    assert sanitized == {"label": "附件元数据"}


@pytest.mark.parametrize(
    "ordinary_url",
    [
        "https://example.com/?design=modern",
        "https://example.com/?signal=green",
        "https://example.com/?author=smk",
    ],
)
def test_display_value_keeps_non_credential_url_parameters(ordinary_url) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    assert service._display_value(ordinary_url) == ordinary_url


def test_form_fields_expose_six_row_table_with_bilingual_columns_in_source_order() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    columns = [
        "物料编码 Código de material",
        "物料名称 Nombre del material",
        "数量 Cantidad",
    ]
    rows = []
    for index in range(1, 7):
        rows.append({
            "rowNumber": f"PRIVATE-ROW-{index}",
            "rowValue": [
                {"label": columns[0], "value": f"MAT-{index}", "key": f"PRIVATE-CODE-{index}"},
                {"label": columns[1], "value": f"物料 {index}", "authMediaId": f"PRIVATE-AUTH-{index}"},
                {"label": columns[2], "value": str(index * 10)},
            ],
        })

    fields = service._form_fields({
        "formComponentValues": [{
            "name": "货物信息 Bienes",
            "componentType": "TableField",
            "value": json.dumps(rows, ensure_ascii=False),
        }],
    })

    assert fields[0]["component_type"] == "TableField"
    assert fields[0]["display_kind"] == "table"
    assert fields[0]["is_empty"] is False
    assert fields[0]["table"] == {
        "columns": columns,
        "row_ids": [f"PRIVATE-ROW-{index}" for index in range(1, 7)],
        "rows": [
            ["MAT-1", "物料 1", "10"],
            ["MAT-2", "物料 2", "20"],
            ["MAT-3", "物料 3", "30"],
            ["MAT-4", "物料 4", "40"],
            ["MAT-5", "物料 5", "50"],
            ["MAT-6", "物料 6", "60"],
        ],
    }
    rendered = json.dumps(fields, ensure_ascii=False)
    assert "PRIVATE-CODE" not in rendered and "PRIVATE-AUTH" not in rendered


def test_form_fields_skip_historical_table_header_placeholder_row() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    columns = [
        "物料编码 Código de material",
        "物料名称（中文）Nombre del material (chino)",
        "数量Cantidad",
    ]
    rows = [
        {
            "rowNumber": "TableField_HEADER",
            "rowValue": [
                {"label": columns[0], "value": "物料编码"},
                {"label": columns[1], "value": "物料名称中文"},
                {"label": columns[2], "value": "数量"},
            ],
        },
        {
            "rowNumber": "TableField_1",
            "rowValue": [
                {"label": columns[0], "value": "YL000097"},
                {"label": columns[1], "value": "连接器"},
                {"label": columns[2], "value": "6"},
            ],
        },
    ]

    fields = service._form_fields({
        "formComponentValues": [{
            "name": "货物信息 Bienes",
            "componentType": "TableField",
            "value": json.dumps(rows, ensure_ascii=False),
        }],
    })

    assert fields[0]["table"] == {
        "columns": columns,
        "rows": [["YL000097", "连接器", "6"]],
        "row_ids": ["TableField_1"],
    }


def test_table_row_ids_remain_aligned_when_a_source_id_is_missing_or_repeated():
    from overseas_costing.services import dingtalk_approval_service as service

    rows = [{'rowNumber': row_id, 'rowValue': [{'label': '物料编码', 'value': 'SKU-1'},
            {'label': '数量', 'value': index}]} for index, row_id in enumerate(('ROW-A', '', 'ROW-A'), 1)]
    table = service._table_value(rows)
    assert table['row_ids'] == ['ROW-A', '', 'ROW-A']
    assert table['rows'] == [['SKU-1', '1'], ['SKU-1', '2'], ['SKU-1', '3']]


def test_form_fields_mark_empty_scalar_values_without_changing_display_text() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    fields = service._form_fields({
        "formComponentValues": [
            {"name": "备注", "componentType": "TextareaField", "value": None},
            {"name": "金额", "component_type": "MoneyField", "value": 0},
        ],
    })

    assert fields == [
        {
            "label": "备注",
            "value": "",
            "component_type": "TextareaField",
            "display_kind": "scalar",
            "is_empty": True,
            "table": {"columns": [], "rows": []},
        },
        {
            "label": "金额",
            "value": "0",
            "component_type": "MoneyField",
            "display_kind": "scalar",
            "is_empty": False,
            "table": {"columns": [], "rows": []},
        },
    ]


def test_form_fields_treat_json_null_string_as_empty() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    field = service._form_fields({
        "formComponentValues": [{
            "name": "备注",
            "componentType": "TextareaField",
            "value": "null",
        }],
    })[0]

    assert field["value"] == ""
    assert field["is_empty"] is True
    assert field["display_kind"] == "scalar"


@pytest.mark.parametrize(
    "malformed_value",
    [
        "not-json table source",
        json.dumps([
            {
                "rowNumber": "PRIVATE-ROW",
                "rowValue": [
                    {"label": "物料编码", "value": "MAT-1", "key": "PRIVATE-KEY"},
                ],
            },
            {"unexpected": "保留这段原文"},
        ], ensure_ascii=False),
        json.dumps([{
            "rowValue": [
                {"label": "物料编码", "value": "MAT-1"},
                "必须保留的原文",
            ],
        }], ensure_ascii=False),
    ],
)
def test_malformed_table_falls_back_to_existing_scalar_text_without_private_keys(
    malformed_value,
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    field = service._form_fields({
        "formComponentValues": [{
            "name": "货物信息",
            "componentType": "TableField",
            "value": malformed_value,
        }],
    })[0]

    assert field["component_type"] == "TableField"
    assert field["display_kind"] == "scalar"
    assert field["table"] == {"columns": [], "rows": []}
    assert field["value"] == service._display_value(malformed_value)
    assert field["is_empty"] is False
    assert "PRIVATE" not in json.dumps(field, ensure_ascii=False)
    if "保留这段原文" in malformed_value:
        assert "保留这段原文" in field["value"]
    if "必须保留的原文" in malformed_value:
        assert "必须保留的原文" in field["value"]


def test_timeline_classifies_comment_decisions_system_and_unknown_in_source_order() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    timeline = service._timeline({
        "operationRecords": [
            {"type": "ADD_REMARK", "remark": "请补充资料", "userId": "U-1", "date": "1"},
            {"type": "EXECUTE_TASK_NORMAL", "result": "AGREE", "remark": "同意", "userId": "U-2", "date": "2"},
            {"type": "EXECUTE_TASK_NORMAL", "result": "REFUSE", "remark": "信息不全", "userId": "U-3", "date": "3"},
            {"type": "START_PROCESS_INSTANCE", "userId": "U-4", "date": "4"},
            {"type": "PROCESS_CC", "userId": "U-5", "date": "5"},
            {"type": "SYNC", "userId": "bpms_system", "date": "6"},
            {"type": "FUTURE_OPERATION", "remark": "新流程事件", "userId": "U-6", "date": "7"},
        ],
    }, "PROC-1")

    assert [item["event_kind"] for item in timeline] == [
        "comment", "decision", "decision", "system", "system", "system", "system",
    ]
    assert [item["display_label"] for item in timeline] == [
        "评论", "同意", "拒绝", "发起审批", "抄送", "同步", "其他流程记录",
    ]
    assert [item["operation_type"] for item in timeline] == [
        "ADD_REMARK",
        "EXECUTE_TASK_NORMAL",
        "EXECUTE_TASK_NORMAL",
        "START_PROCESS_INSTANCE",
        "PROCESS_CC",
        "SYNC",
        "FUTURE_OPERATION",
    ]
    assert timeline[0]["result"] == ""
    assert timeline[0]["remark"] == "请补充资料"
    assert timeline[0]["packing_candidate"] is False
    assert len(timeline[0]["source_id"]) == 64
    assert "source_id" not in timeline[3]


def test_timeline_treats_legacy_comment_without_type_as_comment() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    item = service._timeline({
        "comments": [{
            "comment": "历史评论",
            "userId": "U-1",
            "createTime": "2026-09-01",
        }],
    }, "PROC-1")[0]

    assert item["operation_type"] == "comment"
    assert item["event_kind"] == "comment"
    assert item["display_label"] == "评论"
    assert item["remark"] == "历史评论"


@pytest.mark.parametrize(
    "operation_type",
    ["RESTART_PROCESS_INSTANCE", "ASYNC", "EXECUTE_TASK_UNKNOWN"],
)
def test_timeline_does_not_classify_unknown_substring_operations_as_known_events(
    operation_type,
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    item = service._timeline({
        "operationRecords": [{
            "type": operation_type,
            "result": "AGREE",
            "remark": "未知事件",
            "userId": "U-1",
        }],
    }, "PROC-1")[0]

    assert item["event_kind"] == "system"
    assert item["display_label"] == "其他流程记录"
    assert item["operation_type"] == operation_type
    assert item["result"] == "AGREE"


def test_timeline_parses_only_internal_mentions_and_keeps_markup_as_plain_text() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    remark = (
        "<script>alert(1)</script> 请 [张三](USER_123456) [李四](123-456) "
        "[王五](123456789012345678) 处理 "
        "[外部](https://evil.example) [相对](/approvals/123) "
        "[文档](README.md) [普通文档](README2) [版本](v2) [短号](123) "
        "[数字不足](USER_123) [无数字](USER_NAME) "
        "[张[三](USER_123456) [文件](ftp://host/file) **加粗**"
    )
    item = service._timeline({
        "operationRecords": [{
            "type": "ADD_REMARK",
            "remark": remark,
            "userId": "U-1",
            "date": "2026-09-22",
        }],
    }, "PROC-1")[0]

    assert item["remark_segments"] == [
        {"kind": "text", "text": "<script>alert(1)</script> 请 "},
        {"kind": "mention", "text": "张三"},
        {"kind": "text", "text": " "},
        {"kind": "mention", "text": "李四"},
        {"kind": "text", "text": " "},
        {"kind": "mention", "text": "王五"},
        {
            "kind": "text",
            "text": (
                " 处理 [外部](https://evil.example) [相对](/approvals/123) "
                "[文档](README.md) [普通文档](README2) [版本](v2) [短号](123) "
                "[数字不足](USER_123) [无数字](USER_NAME) "
                "[张[三](USER_123456) [文件](ftp://host/file) **加粗**"
            ),
        },
    ]
    assert {segment["kind"] for segment in item["remark_segments"]} <= {"text", "mention"}
    assert "href" not in repr(item["remark_segments"])


def test_timeline_keeps_large_unclosed_mention_markup_as_one_text_segment() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    remark = "[" * 200_000

    segments = service._remark_segments(remark)

    assert segments == [{"kind": "text", "text": remark}]


def test_timeline_mention_parser_handles_large_unclosed_numeric_target_linearly() -> None:
    import time

    from overseas_costing.services import dingtalk_approval_service as service

    remark = "[张三](" + ("1" * 20_000)

    started_at = time.perf_counter()
    segments = service._remark_segments(remark)
    elapsed = time.perf_counter() - started_at

    assert segments == [{"kind": "text", "text": remark}]
    assert elapsed < 0.5


def test_timeline_does_not_parse_mentions_inside_unclosed_outer_label() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    remark = "[[坏](USER_123456) [好](USER_234567)"

    segments = service._remark_segments(remark)

    assert segments == [{"kind": "text", "text": remark}]


def test_attachment_item_keeps_workflow_field_identity_without_exposing_credentials() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    item = service._attachment_item({
        "process_instance_id": "PROC-1",
        "file_id": "FILE-1",
        "file_name": "任意名称.xlsx",
        "source_field": "装箱单附件（Excel）",
        "component_id": "COMPONENT-1",
        "auth_media_id": "SECRET",
        "archive_status": "archived",
    }, None)

    assert item["source_field"] == "装箱单附件（Excel）"
    assert item["workflow_field_id"] == "COMPONENT-1"
    assert "SECRET" not in repr(item)


def test_attachment_field_identity_is_resolved_from_trusted_approval_component() -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    payload = {
        "formComponentValues": [{
            "name": "装箱单附件（Excel）",
            "componentId": "COMPONENT-PACKING-EXCEL",
            "value": json.dumps([{
                "fileName": "指环扣+亮甲包装袋2.0+宠物用品发货清单-packing list2026.9.5.xlsx",
                "fileId": "FILE-PACKING-1",
                "spaceId": "SPACE-SECRET",
                "authMediaId": "AUTH-SECRET",
            }], ensure_ascii=False),
        }],
    }

    identities = service._attachment_field_identities(payload)

    assert identities == {
        "FILE-PACKING-1": {
            "source_field": "装箱单附件（Excel）",
            "workflow_field_id": "COMPONENT-PACKING-EXCEL",
        },
    }
    assert "SECRET" not in repr(identities)


def test_batch_detail_enriches_archive_attachment_from_approval_component(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "B1", "batch_no": "OA-1", "source_type": "oa_logistics",
                "source_approval_no": "OA-1", "source_instance_id": "MAIN", "extra_json": "{}",
            }

    class Source:
        @staticmethod
        def get_instance_bundle(_ids):
            return {
                "instances": {
                    "MAIN": {
                        "processInstanceId": "MAIN",
                        "businessId": "OA-1",
                        "status": "COMPLETED",
                        "formComponentValues": [{
                            "name": "装箱单附件（Excel）",
                            "id": "PACKING-FIELD",
                            "componentType": "DDAttachment",
                            "extValue": json.dumps([{
                                "fileName": "任意文件名.xlsx",
                                "fileId": "FILE-1",
                                "spaceId": "SPACE-SECRET",
                            }], ensure_ascii=False),
                        }],
                    },
                },
                "attachments": [{
                    "process_instance_id": "MAIN",
                    "file_id": "FILE-1",
                    "file_name": "任意文件名.xlsx",
                    "source_field": "其他附件",
                    "component_id": "WRONG-FIELD",
                    "attachment_origin": "form",
                    "archive_status": "archived",
                }],
                "health": {},
            }

    monkeypatch.setattr(service, "frappe", type("F", (), {"db": DB()})())
    monkeypatch.setattr(service, "_get_approval_source", lambda: Source())

    result = service.get_batch_dingtalk_approval_detail("B1")

    attachment = result["main_approval"]["attachments"][0]
    assert attachment["source_field"] == "装箱单附件（Excel）"
    assert attachment["workflow_field_id"] == "PACKING-FIELD"
    assert attachment["packing_candidate"] is True
    assert "SECRET" not in repr(attachment)


def test_batch_detail_discovers_unindexed_workflow_attachment_from_approval_component(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    file_name = "指环扣+亮甲包装袋2.0+宠物用品发货清单-packing list2026.9.5.xlsx"

    class DB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "B1", "batch_no": "OA-1", "source_type": "oa_logistics",
                "source_approval_no": "OA-1", "source_instance_id": "MAIN", "extra_json": "{}",
            }

    class Source:
        archive_ready = False

        @staticmethod
        def get_instance_bundle(_ids):
            return {
                "instances": {
                    "MAIN": {
                        "processInstanceId": "MAIN",
                        "businessId": "OA-1",
                        "status": "COMPLETED",
                        "formComponentValues": [{
                            "name": "装箱单附件（Excel）",
                            "componentId": "PACKING-FIELD",
                            "componentType": "DDAttachment",
                            "extValue": json.dumps([{
                                "fileName": file_name,
                                "fileId": "FILE-PACKING",
                                "spaceId": "SPACE-PACKING",
                                "fileSize": 9876,
                                "authMediaId": "AUTH-SECRET",
                            }], ensure_ascii=False),
                        }],
                    },
                },
                # The authoritative form already exposes the file, but the archive
                # index has not produced its row yet.
                "attachments": [],
                "health": {},
            }

        @staticmethod
        def get_attachment_manifest(process_instance_id, file_id):
            assert (process_instance_id, file_id) == ("MAIN", "FILE-PACKING")
            if not Source.archive_ready:
                return None
            return {
                "process_instance_id": process_instance_id,
                "file_id": file_id,
                "archive_status": "archived",
                "archive_method": "dingtalk_original",
                "content_quality": "original",
            }

    monkeypatch.setattr(service, "frappe", type("F", (), {"db": DB()})())
    monkeypatch.setattr(service, "_get_approval_source", lambda: Source())

    result = service.get_batch_dingtalk_approval_detail("B1")

    assert result["ok"] is True
    assert len(result["main_approval"]["attachments"]) == 1
    attachment = result["main_approval"]["attachments"][0]
    assert attachment["file_name"] == file_name
    assert attachment["file_id"] == "FILE-PACKING"
    assert attachment["space_id"] == "SPACE-PACKING"
    assert attachment["source_field"] == "装箱单附件（Excel）"
    assert attachment["workflow_field_id"] == "PACKING-FIELD"
    assert attachment["archive_status"] == "pending"
    assert attachment["packing_candidate"] is True
    assert attachment["downloadable"] is False
    assert attachment["declared_size"] == 9876
    assert "AUTH-SECRET" not in repr(attachment)

    Source.archive_ready = True
    refreshed = service.get_batch_dingtalk_approval_detail("B1")
    archived = refreshed["main_approval"]["attachments"][0]
    assert archived["archive_status"] == "archived"
    assert archived["archive_method"] == "dingtalk_original"
    assert archived["content_quality"] == "original"
    assert archived["downloadable"] is True


def test_batch_detail_resolves_actor_names_and_audits_excluded_linked_approval(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class FakeDB:
        @staticmethod
        def get_value(_doctype, _name, _fields, as_dict=False):
            return {
                "name": "BATCH-1",
                "batch_no": "OA-001",
                "source_type": "oa_logistics",
                "source_approval_no": "OA-001",
                "source_instance_id": "PROC-MAIN",
                "extra_json": json.dumps({
                    "linked_purchase_approvals": [
                        {"source_instance_id": "PROC-BUY-VALID"},
                        {"source_instance_id": "PROC-BUY-REFUSED"},
                    ]
                }),
            }

    class FakeFrappe:
        db = FakeDB()

    class FakeSource:
        @staticmethod
        def get_instance_bundle(instance_ids):
            assert instance_ids == ["PROC-MAIN", "PROC-BUY-VALID", "PROC-BUY-REFUSED"]
            return {
                "instances": {
                    "PROC-MAIN": {
                        "corpId": "CORP-1",
                        "processInstanceId": "PROC-MAIN",
                        "businessId": "OA-001",
                        "status": "COMPLETED",
                        "result": "agree",
                        "originatorUserId": "0217304551217188371",
                        "operationRecords": [
                            {"userId": "16693147192083157833", "type": "EXECUTE_TASK_NORMAL", "date": "2026-09-04"},
                            {"userId": "UNKNOWN", "type": "PROCESS_CC", "date": "2026-09-04"},
                            {"userId": "bpms_system", "type": "SYSTEM", "date": "2026-09-04"},
                        ],
                    },
                    "PROC-BUY-VALID": {
                        "corpId": "CORP-1",
                        "processInstanceId": "PROC-BUY-VALID",
                        "businessId": "PUR-VALID",
                        "status": "COMPLETED",
                        "result": "agree",
                        "originatorUserId": "USER-3",
                    },
                    "PROC-BUY-REFUSED": {
                        "corpId": "CORP-1",
                        "processInstanceId": "PROC-BUY-REFUSED",
                        "businessId": "PUR-REFUSED",
                        "status": "COMPLETED",
                        "result": "refuse",
                        "originatorUserId": "USER-4",
                    },
                },
                "attachments": [{
                    "corp_id": "CORP-1",
                    "process_instance_id": "PROC-MAIN",
                    "file_id": "FILE-1",
                    "file_name": "评论附件.pdf",
                    "attachment_origin": "comment",
                    "archive_status": "archived",
                    "comment_user_id": "USER-3",
                }],
                "actors": {"CORP-1": {
                    "0217304551217188371": {"name": "李仲华"},
                    "16693147192083157833": {"name": "周汉琴"},
                    "USER-3": {"name": "陈一"},
                    "USER-4": {"name": "王二"},
                }},
                "health": {},
            }

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "_get_approval_source", lambda: FakeSource())
    monkeypatch.setattr(
        service,
        "_trusted_linked_instance_ids",
        lambda _payload: ["PROC-BUY-VALID", "PROC-BUY-REFUSED"],
    )

    result = service.get_batch_dingtalk_approval_detail("BATCH-1")

    main = result["main_approval"]
    assert main["originator_user_name"] == "李仲华"
    assert main["originator_name_source"] == "directory"
    assert main["timeline"][0]["user_name"] == "周汉琴"
    assert main["timeline"][0]["user_name_source"] == "directory"
    assert main["timeline"][1]["user_name_unresolved"] is True
    assert main["timeline"][2]["user_name"] == "系统"
    assert main["attachments"][0]["comment_user_name"] == "陈一"
    assert main["raw_status"] == "COMPLETED"
    assert main["raw_result"] == "agree"
    assert main["process_status"] == "COMPLETED"
    assert main["approval_result"] == "agree"
    assert main["effective_status"] == "COMPLETED"
    assert main["excluded"] is False

    assert [row["instance_id"] for row in result["linked_purchase_approvals"]] == ["PROC-BUY-VALID"]
    excluded = result["excluded_linked_purchase_approvals"][0]
    assert excluded["instance_id"] == "PROC-BUY-REFUSED"
    assert excluded["originator_user_name"] == "王二"
    assert excluded["effective_status"] == "REJECTED"
    assert excluded["excluded"] is True
    assert excluded["exclusion_reason"] == "审批结果为拒绝"


def test_materialize_rechecks_existing_attachment_while_batch_is_locked(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    sql_calls = []

    class FakeDB:
        @staticmethod
        def sql(query, params):
            sql_calls.append((query, params))

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{
                "name": "ATT-EXISTING",
                "parse_result_json": json.dumps({
                    "process_instance_id": "PROC-MAIN",
                    "file_id": "FILE-1",
                }),
            }]

        @staticmethod
        def get_doc(*_args, **_kwargs):
            raise AssertionError("duplicate attachment must not be inserted")

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {
            "instance_id": "PROC-MAIN",
            "attachments": [{
                "file_id": "FILE-1",
                "attachment_name": "",
                "archive_status": "archived",
            }],
        },
        "linked_purchase_approvals": [],
    })

    result = service.materialize_batch_dingtalk_attachment("BATCH-1", "PROC-MAIN", "FILE-1")

    assert result == {"ok": True, "attachment_name": "ATT-EXISTING", "created": False}
    assert "FOR UPDATE" in sql_calls[0][0]


@pytest.mark.parametrize("reverse", [False, True])
def test_materialize_reuses_canonical_archive_when_temporary_duplicate_exists(
    monkeypatch, reverse
) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    rows = [
        _oa_attachment_row("ATT-TEMP", archived=False),
        _oa_attachment_row("ATT-CANONICAL", archived=True),
    ]
    if reverse:
        rows.reverse()

    class FakeDB:
        @staticmethod
        def sql(*_args, **_kwargs):
            return []

        @staticmethod
        def get_value(*_args, **_kwargs):
            return {"name": "BATCH-1", "current_version": "VERSION-1"}

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return rows

        @staticmethod
        def get_doc(*_args, **_kwargs):
            raise AssertionError("existing canonical attachment must be reused")

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {
            "instance_id": "PROC-MAIN",
            "attachments": [{
                "file_id": "FILE-1",
                "attachment_name": "",
                "archive_status": "archived",
            }],
        },
        "linked_purchase_approvals": [],
    })

    result = service.materialize_batch_dingtalk_attachment(
        "BATCH-1", "PROC-MAIN", "FILE-1"
    )

    assert result == {
        "ok": True,
        "attachment_name": "ATT-CANONICAL",
        "created": False,
    }


def test_materialize_allows_download_from_excluded_approval_audit(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{
                "name": "ATT-EXCLUDED",
                "parse_result_json": json.dumps({
                    "process_instance_id": "PROC-REFUSED",
                    "file_id": "FILE-REFUSED",
                }),
            }]

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {"instance_id": "PROC-MAIN", "attachments": []},
        "linked_purchase_approvals": [],
        "excluded_linked_purchase_approvals": [{
            "instance_id": "PROC-REFUSED",
            "excluded": True,
            "attachments": [{
                "file_id": "FILE-REFUSED",
                "attachment_name": "ATT-EXCLUDED",
                "archive_status": "archived",
            }],
        }],
    })

    result = service.materialize_batch_dingtalk_attachment(
        "BATCH-1", "PROC-REFUSED", "FILE-REFUSED"
    )

    assert result == {"ok": True, "attachment_name": "ATT-EXCLUDED", "created": False}


def test_materialize_backfills_audit_policy_on_existing_excluded_attachment(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    class FakeAttachmentDoc:
        parse_result_json = json.dumps({
            "process_instance_id": "PROC-REFUSED",
            "file_id": "FILE-REFUSED",
        })
        attachment_type = "Packing List"
        save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    attachment_doc = FakeAttachmentDoc()

    class FakeFrappe:
        @staticmethod
        def get_doc(doctype, name):
            assert (doctype, name) == ("Overseas Cost Attachment", "ATT-EXCLUDED")
            return attachment_doc

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {"instance_id": "PROC-MAIN", "attachments": []},
        "linked_purchase_approvals": [],
        "excluded_linked_purchase_approvals": [{
            "instance_id": "PROC-REFUSED",
            "excluded": True,
            "exclusion_reason": "审批结果为拒绝",
            "attachments": [{
                "file_id": "FILE-REFUSED",
                "attachment_name": "ATT-EXCLUDED",
                "archive_status": "archived",
            }],
        }],
    })

    result = service.materialize_batch_dingtalk_attachment(
        "BATCH-1", "PROC-REFUSED", "FILE-REFUSED"
    )

    assert result == {"ok": True, "attachment_name": "ATT-EXCLUDED", "created": False}
    snapshot = json.loads(attachment_doc.parse_result_json)
    assert snapshot["approval_excluded"] is True
    assert snapshot["cost_source_allowed"] is False
    assert snapshot["exclusion_reason"] == "审批结果为拒绝"
    assert attachment_doc.attachment_type == "Other"
    assert attachment_doc.save_count == 1


def test_materialize_allows_attachment_from_excluded_linked_approval(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    monkeypatch.setattr(service, "frappe", object())
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {"instance_id": "PROC-MAIN", "attachments": []},
        "linked_purchase_approvals": [],
        "excluded_linked_purchase_approvals": [{
            "instance_id": "PROC-REFUSED",
            "excluded": True,
            "attachments": [{
                "file_id": "FILE-EXCLUDED",
                "attachment_name": "ATT-EXCLUDED",
                "archive_status": "archived",
            }],
        }],
    })

    result = service.materialize_batch_dingtalk_attachment(
        "BATCH-1",
        "PROC-REFUSED",
        "FILE-EXCLUDED",
    )

    assert result == {"ok": True, "attachment_name": "ATT-EXCLUDED", "created": False}


def test_materialized_excluded_attachment_is_persisted_as_audit_only(monkeypatch) -> None:
    from overseas_costing.services import dingtalk_approval_service as service

    inserted = []

    class FakeDoc:
        name = "ATT-NEW"

        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            inserted.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def sql(*_args, **_kwargs):
            return []

        @staticmethod
        def get_value(_doctype, _name, _fields, as_dict=False):
            return {"name": "BATCH-1", "current_version": "VERSION-1"}

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    monkeypatch.setattr(service, "frappe", FakeFrappe)
    monkeypatch.setattr(service, "get_batch_dingtalk_approval_detail", lambda _batch: {
        "ok": True,
        "main_approval": {"instance_id": "PROC-MAIN", "attachments": []},
        "linked_purchase_approvals": [],
        "excluded_linked_purchase_approvals": [{
            "instance_id": "PROC-REFUSED",
            "business_id": "PUR-REFUSED",
            "excluded": True,
            "exclusion_reason": "审批结果为拒绝",
            "attachments": [{
                "file_id": "FILE-REFUSED",
                "file_name": "装箱单.xlsx",
                "process_instance_id": "PROC-REFUSED",
                "archive_status": "archived",
                "packing_candidate": True,
            }],
        }],
    })

    result = service.materialize_batch_dingtalk_attachment(
        "BATCH-1", "PROC-REFUSED", "FILE-REFUSED"
    )

    assert result["created"] is True
    assert inserted[0]["attachment_type"] == "Other"
    snapshot = json.loads(inserted[0]["parse_result_json"])
    assert snapshot["approval_excluded"] is True
    assert snapshot["cost_source_allowed"] is False
    assert snapshot["exclusion_reason"] == "审批结果为拒绝"
