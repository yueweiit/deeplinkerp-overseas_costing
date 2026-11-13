"""
中文用途：钉钉审批跳转工具测试。
"""

import json
import os
from pathlib import Path

from overseas_costing.scripts import import_oa_logistics
from overseas_costing.utils.dingtalk import (
    build_desktop_approval_url,
    build_dingtalk_order_payload,
    build_mobile_approval_url,
    extract_dingtalk_instance_id,
)
from overseas_costing.scripts.import_oa_logistics import (
    DEFAULT_LOGISTICS_PROCESS_CODE,
    _merge_oa_extra_json,
    _guess_oa_attachment_type,
    _normalize_allocation_basis,
    _purchase_expense_rows_from_preview,
    _recalculate_after_purchase_sync,
    _restore_main_logistics_items_after_excluded_purchases,
    _sync_oa_form_attachments,
    _sync_oa_logistics_allocation_rule,
    _sync_linked_purchase_fields,
    _normalize_legacy_instance,
    build_oa_item_values_from_approval,
    build_batch_values_from_approval,
    build_purchase_expense_item_values_from_approval,
    extract_logistics_fee_from_approval,
    extract_logistics_quote_candidates_from_approval,
    extract_logistics_text_summary_from_approval,
    extract_approval_attachments,
    extract_form_attachments,
    extract_oa_goods_rows,
    extract_form_fields,
    extract_linked_purchase_approvals,
    extract_subsidiary_from_approval,
    extract_purchase_expense_rows,
    get_access_token,
    get_process_attachment_download_url,
    is_completed_approval_status,
    is_hidden_approval_status,
    is_sea_approval,
    load_env_file,
    pull_latest_logistics_approvals_to_erp,
    refresh_missing_oa_finished_times,
    refresh_oa_logistics_detail,
    resolve_logistics_process_code,
    resolve_purchase_process_code,
    refresh_existing_oa_logistics_details,
    resolve_approval_decision,
    pull_purchase_expense_approvals,
    preview_purchase_expenses_from_process,
    save_sea_approvals_to_erp,
    sync_existing_oa_finished_times,
    sync_purchase_expenses_from_process,
    summarize_approval,
    summarize_purchase_approval,
)


def test_excel_attachment_requires_packing_evidence_instead_of_extension_only() -> None:
    assert _guess_oa_attachment_type("装箱计划.xlsx") == "Packing List"
    assert _guess_oa_attachment_type("packing list.xlsm") == "Packing List"
    assert _guess_oa_attachment_type("商业发票.xlsx") == "Commercial Invoice"
    assert _guess_oa_attachment_type("历史数据.xlsx") == "Other"


def test_build_mobile_and_desktop_approval_url() -> None:
    mobile_url = build_mobile_approval_url("PROC-001")
    desktop_url = build_desktop_approval_url("PROC-001")

    assert "PROC-001" in mobile_url
    assert mobile_url.startswith("https://aflow.dingtalk.com/")
    assert desktop_url.startswith("dingtalk://dingtalkclient/page/link?url=")


def test_build_dingtalk_order_payload_prefers_desktop_protocol() -> None:
    payload = build_dingtalk_order_payload(
        batch_name="BATCH-001",
        approval_no="OA-20260709-001",
        instance_id="PROC-001",
        official_url="https://oa.dingtalk.com/approval/detail",
    )

    assert payload["approval_no"] == "OA-20260709-001"
    assert payload["instance_id"] == "PROC-001"
    assert payload["desktop_url"].startswith("dingtalk://")
    assert payload["open_url"] == payload["desktop_url"]
    assert payload["can_open"] is True


def test_normalize_allocation_basis_accepts_business_words() -> None:
    assert _normalize_allocation_basis("按计费重") == "chargeable_weight"
    assert _normalize_allocation_basis("体积分摊") == "volume"
    assert _normalize_allocation_basis("货值比例") == "goods_value"
    assert _normalize_allocation_basis("") == "gross_weight"


def test_build_dingtalk_order_payload_fallback_to_official_url() -> None:
    payload = build_dingtalk_order_payload(
        batch_name="BATCH-002",
        official_url="https://oa.dingtalk.com/approval/detail",
    )

    assert payload["desktop_url"] == ""
    assert payload["open_mode"] == "web_url"
    assert payload["open_url"] == "https://oa.dingtalk.com/approval/detail"


def test_extract_dingtalk_instance_id_from_pc_approval_url() -> None:
    url = (
        "https://aflow.dingtalk.com/dingtalk/web/query/pchomepage.htm?from=oflow&op=true"
        "#/plainapproval?procInstId=aR2wGNueQB-FVuGOgSAZdA04891770039043"
    )

    assert extract_dingtalk_instance_id(url) == "aR2wGNueQB-FVuGOgSAZdA04891770039043"


def test_build_dingtalk_order_payload_uses_proc_inst_id_from_official_url() -> None:
    official_url = (
        "https://aflow.dingtalk.com/dingtalk/web/query/pchomepage.htm"
        "#/plainapproval?procInstId=PROC-URL-001"
    )

    payload = build_dingtalk_order_payload(batch_name="BATCH-003", official_url=official_url)

    assert payload["instance_id"] == "PROC-URL-001"
    assert payload["open_mode"] == "desktop_protocol"
    assert payload["open_url"] == payload["desktop_url"]


def test_get_process_attachment_download_url_uses_process_instance_and_file_id(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"result": {"downloadUri": "https://download.example.com/packing.xlsx"}}

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)

    result = get_process_attachment_download_url(
        token="TOKEN-001",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        user_id="USER-001",
        api_style="new",
    )

    assert result["download_uri"] == "https://download.example.com/packing.xlsx"
    assert calls[0]["method"] == "POST"
    assert calls[0]["token"] == "TOKEN-001"
    assert calls[0]["payload"] == {"processInstanceId": "PROC-SEA-001", "fileId": "FILE-001"}


def test_get_process_attachment_download_url_uses_legacy_api_for_legacy_credentials(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"errcode": 0, "result": {"download_uri": "https://download.example.com/packing.xlsx"}}

    monkeypatch.delenv("DINGTALK_CORP_ID", raising=False)
    monkeypatch.delenv("DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.setenv("DINGTALK_APP_KEY", "legacy-app-key")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "legacy-app-secret")
    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)

    result = get_process_attachment_download_url(
        token="TOKEN-001",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        user_id="USER-001",
    )

    assert result["api_style"] == "legacy"
    assert result["download_uri"] == "https://download.example.com/packing.xlsx"
    assert "/topapi/processinstance/file/url/get?access_token=TOKEN-001" in calls[0]["url"]
    assert calls[0]["api_style"] == "legacy"
    assert calls[0]["payload"] == {
        "request": {
            "process_instance_id": "PROC-SEA-001",
            "file_id": "FILE-001",
        },
    }


def test_get_process_attachment_download_url_legacy_does_not_require_user_id(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"errcode": 0, "result": {"downloadUri": "https://download.example.com/no-user.xlsx"}}

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)

    result = get_process_attachment_download_url(
        token="TOKEN-001",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/no-user.xlsx"
    assert "user_id" not in calls[0]["payload"]["request"]


def test_get_process_attachment_download_url_uses_legacy_dentry_auth_before_new_fallback(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url and len([call for call in calls if "/topapi/processinstance/file/url/get" in call["url"]]) == 1:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "764607503"}}
        if "/topapi/process/dentry/auth" in url:
            return {"errcode": 0, "result": True}
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 0, "result": {"downloadUri": "https://download.example.com/legacy-auth.xlsx"}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="207632357484",
        user_id="03435534375526711913",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/legacy-auth.xlsx"
    assert result["fallback_api"] == "legacy_dentry_auth_then_file_url"
    auth_call = next(call for call in calls if "/topapi/process/dentry/auth" in call["url"])
    assert auth_call["payload"] == {
        "request": {
            "file_infos": [
                {
                    "file_id": 207632357484,
                    "space_id": 764607503,
                }
            ],
            "userid": "03435534375526711913",
        }
    }


def test_get_process_attachment_download_url_falls_back_to_storage_download(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "SPACE-001"}}
        if "/topapi/process/dentry/auth" in url:
            raise RuntimeError("legacy auth failed")
        if "/topapi/v2/user/get" in url:
            return {"errcode": 0, "result": {"unionid": "UNION-001"}}
        if "/v1.0/workflow/processInstances/spaces/files/authDownload" in url:
            raise RuntimeError("noPermission")
        if "/v1.0/storage/spaces/SPACE-001/dentries/FILE-001/downloadInfos/query" in url:
            return {
                "headerSignatureInfo": {
                    "resourceUrls": ["https://download.example.com/storage.xlsx"],
                    "headers": {"x-acs-signature": "SIG-001"},
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "NEW-TOKEN")

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        user_id="USER-001",
        corp_id="ding-corp-001",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/storage.xlsx"
    assert result["download_headers"] == {"x-acs-signature": "SIG-001"}
    assert result["space_id"] == "SPACE-001"
    assert result["fallback_api"] == "storage_dentry_download_info"
    assert [call["payload"] for call in calls[:4]] == [
        {"request": {"process_instance_id": "PROC-SEA-001", "file_id": "FILE-001"}},
        {"process_instance_id": "PROC-SEA-001", "file_id": "FILE-001", "user_id": "USER-001"},
        {"request": {"file_infos": [{"file_id": "FILE-001", "space_id": "SPACE-001"}], "userid": "USER-001"}},
        {"userid": "USER-001", "language": "zh_CN"},
    ]
    assert any("/v1.0/workflow/processInstances/spaces/files/authDownload" in call["url"] for call in calls)
    assert any("unionId=UNION-001" in call["url"] for call in calls)


def test_get_process_attachment_download_url_uses_new_auth_before_storage(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "SPACE-001"}}
        if "/topapi/v2/user/get" in url:
            return {"errcode": 0, "result": {"unionid": "UNION-001"}}
        if "/v1.0/workflow/processInstances/spaces/files/authDownload" in url:
            return {"result": {"success": True}}
        if "/v1.0/workflow/processInstances/spaces/files/urls/download" in url:
            return {"result": {"downloadUri": "https://download.example.com/auth.xlsx"}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "NEW-TOKEN")

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        user_id="USER-001",
        corp_id="ding-corp-001",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/auth.xlsx"
    assert result["fallback_api"] == "new_auth_then_approval_download"
    auth_call = next(call for call in calls if "/v1.0/workflow/processInstances/spaces/files/authDownload" in call["url"])
    assert auth_call["payload"]["fileInfos"] == [{"spaceId": "SPACE-001", "fileId": "FILE-001"}]


def test_get_process_attachment_download_url_keeps_new_auth_file_id_as_string(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "764607503"}}
        if "/topapi/process/dentry/auth" in url:
            raise RuntimeError("legacy auth failed")
        if "/topapi/v2/user/get" in url:
            return {"errcode": 0, "result": {"unionid": "UNION-001"}}
        if "/v1.0/workflow/processInstances/spaces/files/authDownload" in url:
            return {"result": {"success": True}}
        if "/v1.0/workflow/processInstances/spaces/files/urls/download" in url:
            return {"result": {"downloadUri": "https://download.example.com/auth.xlsx"}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "NEW-TOKEN")

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="207632357484",
        user_id="03435534375526711913",
        corp_id="ding-corp-001",
        api_style="legacy",
    )

    auth_call = next(call for call in calls if "/v1.0/workflow/processInstances/spaces/files/authDownload" in call["url"])
    assert result["download_uri"] == "https://download.example.com/auth.xlsx"
    assert auth_call["payload"]["fileInfos"] == [{"spaceId": 764607503, "fileId": "207632357484"}]


def test_get_process_attachment_download_url_resolves_storage_dentry_by_file_name(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "SPACE-001"}}
        if "/topapi/v2/user/get" in url:
            return {"errcode": 0, "result": {"unionid": "UNION-001"}}
        if "/v1.0/workflow/processInstances/spaces/files/authDownload" in url:
            raise RuntimeError("noPermission")
        if "/v1.0/storage/spaces/SPACE-001/dentries/FILE-001/downloadInfos/query" in url:
            raise RuntimeError("permissionDenied")
        if "/v1.0/storage/spaces/SPACE-001/dentries?" in url:
            return {"dentries": [{"id": "DENTRY-002", "name": "packing.xlsx"}]}
        if "/v1.0/storage/spaces/SPACE-001/dentries/DENTRY-002/downloadInfos/query" in url:
            return {
                "headerSignatureInfo": {
                    "resourceUrls": ["https://download.example.com/resolved.xlsx"],
                    "headers": {"x-acs-signature": "RESOLVED-SIG"},
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "NEW-TOKEN")

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        file_name="packing.xlsx",
        user_id="USER-001",
        corp_id="ding-corp-001",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/resolved.xlsx"
    assert result["download_headers"] == {"x-acs-signature": "RESOLVED-SIG"}
    assert result["fallback_api"] == "storage_dentry_list_then_download_info"
    assert any("/v1.0/storage/spaces/SPACE-001/dentries/FILE-001/downloadInfos/query" in call["url"] for call in calls)
    assert any("/v1.0/storage/spaces/SPACE-001/dentries?" in call["url"] for call in calls)
    assert any("/v1.0/storage/spaces/SPACE-001/dentries/DENTRY-002/downloadInfos/query" in call["url"] for call in calls)


def test_get_process_attachment_download_url_falls_back_to_thumbnail(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = []

    def fake_request_json(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "/topapi/processinstance/file/url/get" in url:
            return {"errcode": 60121, "errmsg": "找不到该用户", "success": False}
        if "/topapi/processinstance/cspace/info" in url:
            return {"errcode": 0, "result": {"space_id": "SPACE-001"}}
        if "/topapi/process/dentry/auth" in url:
            raise RuntimeError("legacy auth failed")
        if "/topapi/v2/user/get" in url:
            return {"errcode": 0, "result": {"unionid": "UNION-001"}}
        if "/v1.0/workflow/processInstances/spaces/files/authDownload" in url:
            raise RuntimeError("noPermission")
        if "/v1.0/storage/spaces/SPACE-001/dentries/FILE-001/downloadInfos/query" in url:
            raise RuntimeError("permissionDenied")
        if "/v1.0/storage/spaces/SPACE-001/dentries?" in url:
            raise RuntimeError("list noPermission")
        if "/v1.0/storage/spaces/SPACE-001/thumbnails/query" in url:
            return {
                "resultItems": [
                    {
                        "dentryId": "FILE-001",
                        "thumbnail": {"url": "https://download.example.com/thumb.png"},
                    }
                ]
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "NEW-TOKEN")

    result = get_process_attachment_download_url(
        token="LEGACY-TOKEN",
        process_instance_id="PROC-SEA-001",
        file_id="FILE-001",
        file_name="报价.png",
        user_id="USER-001",
        corp_id="ding-corp-001",
        api_style="legacy",
    )

    assert result["download_uri"] == "https://download.example.com/thumb.png"
    assert result["download_headers"] == {}
    assert result["fallback_api"] == "storage_thumbnail_query"
    thumbnail_call = next(call for call in calls if "/v1.0/storage/spaces/SPACE-001/thumbnails/query" in call["url"])
    assert "unionId=UNION-001" in thumbnail_call["url"]
    assert thumbnail_call["payload"]["dentryIds"] == ["FILE-001"]


def test_logistics_approval_summary_extracts_sea_trace_fields() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-001",
        "businessId": "202607210001",
        "title": "国际物流 Logística Internacional",
        "status": "COMPLETED",
        "url": "https://aflow.dingtalk.com/dingtalk/web/query/pchomepage.htm#/plainapproval?procInstId=PROC-SEA-001",
        "formComponentValues": [
            {"name": "物流方式Camino Envío", "value": "海运"},
            {"name": "柜号/单号Número DE Logística", "value": "HPCU5155607"},
        ],
    }

    fields = extract_form_fields(instance)
    summary = summarize_approval(instance)

    assert is_sea_approval(fields) is True
    assert summary["source_instance_id"] == "PROC-SEA-001"
    assert summary["source_approval_no"] == "202607210001"
    assert summary["transport_mode_raw"] == "海运"
    assert summary["logistics_no"] == "HPCU5155607"
    assert summary["open_url"].startswith("dingtalk://")


def test_logistics_approval_reads_supplier_per_goods_row_and_ignores_top_level_field(monkeypatch) -> None:
    monkeypatch.setattr(
        import_oa_logistics,
        "resolve_supplier_reference",
        lambda raw, suppliers=None: {
            "raw_value": str(raw or "").strip(),
            "status": "EXACT" if raw else "EMPTY",
            "canonical_supplier": str(raw or "").strip(),
            "candidates": [],
        },
    )
    instance = {
        "processInstanceId": "PROC-SEA-SUPPLIER",
        "businessId": "202609230001",
        "status": "COMPLETED",
        "formComponentValues": [
            {"name": "物流方式Camino Envío", "value": "海运"},
            {
                "name": "供应商Proveedor",
                "value": "顶层旧值不得广播",
            },
            {
                "name": "货物信息Bienes",
                "value": json.dumps(
                    [
                        {
                            "rowValue": [
                                {"label": "物料编码 Código de material", "value": "YL000097"},
                                {"label": "物料名称（中文）Nombre del material (chino)", "value": "TPU原料"},
                                {"label": "供应商Proveedor", "value": "SUPPLIER-A"},
                                {"label": "数量Cantidad", "value": "100"},
                                {"label": "单位Unidad", "value": "KG"},
                            ]
                        },
                        {
                            "rowValue": [
                                {"label": "物料编码 Código de material", "value": "YL000098"},
                                {"label": "物料名称（中文）Nombre del material (chino)", "value": "塑料粒子"},
                                {"label": "Proveedor", "value": "SUPPLIER-B"},
                                {"label": "数量Cantidad", "value": "20"},
                                {"label": "单位Unidad", "value": "KG"},
                            ]
                        },
                        {
                            "rowValue": [
                                {"label": "物料编码 Código de material", "value": "YL000099"},
                                {"label": "物料名称（中文）Nombre del material (chino)", "value": "色母"},
                                {"label": "供应商Proveedor", "value": ""},
                                {"label": "数量Cantidad", "value": "10"},
                                {"label": "单位Unidad", "value": "KG"},
                            ]
                        },
                    ],
                    ensure_ascii=False,
                ),
            },
        ],
    }

    summary = summarize_approval(instance)
    items = build_oa_item_values_from_approval(summary)

    assert "supplier" not in summary
    assert [item["supplier"] for item in items] == ["SUPPLIER-A", "SUPPLIER-B", ""]
    metadata = [json.loads(item["extra_json"]) for item in items]
    assert [(item["supplier_raw_value"], item["supplier_field_present"]) for item in metadata] == [
        ("SUPPLIER-A", True),
        ("SUPPLIER-B", True),
        ("", True),
    ]
    assert [item["dingtalk_goods_row_no"] for item in metadata] == [1, 2, 3]


def test_logistics_approval_keeps_unmatched_supplier_only_in_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        import_oa_logistics,
        "resolve_supplier_reference",
        lambda raw, suppliers=None: {
            "raw_value": str(raw or "").strip(),
            "status": "SUGGESTED",
            "canonical_supplier": "",
            "candidates": [{"name": "SUP-ALPHA", "score": 0.94, "high_confidence": True}],
        },
    )
    approval = {
        "source_approval_no": "OA-SUPPLIER-UNMATCHED",
        "source_instance_id": "PROC-SUPPLIER-UNMATCHED",
        "form_fields": {
            "货物信息Bienes": [{
                "rowNumber": "TableField_9",
                "rowValue": [
                    {"label": "物料编码 Código de material", "value": "M-1"},
                    {"label": "物料名称（中文）Nombre del material (chino)", "value": "原料"},
                    {"label": "供应商Proveedor", "value": "Alpha Tradng Mexico"},
                    {"label": "数量Cantidad", "value": "1"},
                ],
            }],
        },
    }

    item = build_oa_item_values_from_approval(approval)[0]
    metadata = json.loads(item["extra_json"])

    assert item["supplier"] == ""
    assert metadata["supplier_raw_value"] == "Alpha Tradng Mexico"
    assert metadata["supplier_field_present"] is True
    assert metadata["supplier_match_status"] == "SUGGESTED"
    assert metadata["supplier_candidates"][0]["name"] == "SUP-ALPHA"
    assert metadata["dingtalk_goods_row_no"] == "TableField_9"


def test_oa_goods_rows_carry_a_stable_line_key(monkeypatch) -> None:
    """OA 骨架行此前不写 stable_line_key，工作台行复选框/全选本页因此永远禁用。

    与装箱单导入（import_service）同口径：每行都必须有唯一的身份键。
    """

    monkeypatch.setattr(
        import_oa_logistics,
        "resolve_supplier_reference",
        lambda raw, suppliers=None: {
            "raw_value": str(raw or "").strip(),
            "status": "UNRESOLVED",
            "canonical_supplier": "",
            "candidates": [],
        },
    )
    approval = {
        "source_approval_no": "OA-KEY-1",
        "source_instance_id": "PROC-KEY-1",
        "form_fields": {
            "货物信息Bienes": [
                {
                    "rowNumber": "TableField_1",
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "M-1"},
                        {"label": "数量Cantidad", "value": "1"},
                    ],
                },
                {
                    "rowNumber": "TableField_2",
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "M-2"},
                        {"label": "数量Cantidad", "value": "2"},
                    ],
                },
            ],
        },
    }

    items = build_oa_item_values_from_approval(approval)

    keys = [item["stable_line_key"] for item in items]
    assert all(keys)
    assert len(set(keys)) == len(keys)


def test_purchase_expense_item_values_carry_a_stable_line_key() -> None:
    values = import_oa_logistics._build_purchase_expense_item_doc_values(
        row={"material_code": "M-1", "product_name": "原料", "quantity": "2", "unit_price": "10"},
        row_no=1,
        batch_name="B",
        version_name="V",
        approval_item={},
    )

    assert values["stable_line_key"]


def test_build_oa_items_loads_active_supplier_catalog_once_per_approval(monkeypatch) -> None:
    supplier_catalog = [
        {"name": "SUP-A", "supplier_name": "Supplier A", "disabled": 0},
        {"name": "SUP-B", "supplier_name": "Supplier B", "disabled": 0},
    ]
    load_calls = []
    resolution_catalogs = []
    monkeypatch.setattr(
        import_oa_logistics,
        "load_active_suppliers",
        lambda: load_calls.append("load") or supplier_catalog,
    )
    real_resolver = import_oa_logistics.resolve_supplier_reference

    def tracked_resolver(raw, *, suppliers=None):
        resolution_catalogs.append(suppliers)
        return real_resolver(raw, suppliers=suppliers)

    monkeypatch.setattr(import_oa_logistics, "resolve_supplier_reference", tracked_resolver)
    approval = {
        "form_fields": {
            "货物信息Bienes": [
                {"rowValue": [
                    {"label": "物料编码 Código de material", "value": "M-1"},
                    {"label": "物料名称（中文）Nombre del material (chino)", "value": "A"},
                    {"label": "供应商Proveedor", "value": "Supplier A"},
                ]},
                {"rowValue": [
                    {"label": "物料编码 Código de material", "value": "M-2"},
                    {"label": "物料名称（中文）Nombre del material (chino)", "value": "B"},
                    {"label": "供应商Proveedor", "value": "Supplier B"},
                ]},
            ],
        },
    }

    items = build_oa_item_values_from_approval(approval)

    assert load_calls == ["load"]
    assert resolution_catalogs == [supplier_catalog, supplier_catalog]
    assert [item["supplier"] for item in items] == ["SUP-A", "SUP-B"]


def test_extract_logistics_fee_from_approval_only_reads_explicit_amount() -> None:
    fee = extract_logistics_fee_from_approval(
        {
            "form_fields": {
                "物流费用": "900",
                "币种Moneda": "美元Dólar",
                "物流报价Cotización de logística": "40HQ / 900美金",
            }
        }
    )

    assert fee["amount"] == 900
    assert fee["currency"] == "USD"
    assert fee["source_field"] == "物流费用"

    quote_fee = extract_logistics_fee_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": "货物预估方数：1.5方\n1.SISA报价：3720/立方\n合计价格：5730元\n2.大墨仓报价：2900/立方\n合计价格：4350元"
            }
        }
    )

    assert quote_fee == {}


def test_extract_logistics_fees_maps_optional_mexico_fees_without_shared_currency() -> None:
    fees = import_oa_logistics.extract_logistics_fees_from_approval(
        {
            "transport_mode_raw": "Express快递",
            "form_fields": {
                "物流费用": "125 USD",
                "币种Moneda": "美元Dólar",
                "清关费": "800",
                "进口税费": "1200 MXN",
                "快递附加费": "0",
                "当地配送费": "450",
            },
        }
    )

    assert [(row["logical_fee_key"], row["amount"], row["currency"], row["allocation_basis"]) for row in fees] == [
        ("international_express_fee", 125.0, "USD", "gross_weight"),
        ("customs_clearance_fee", 800.0, "MXN", "goods_value"),
        ("import_tax", 1200.0, "MXN", "goods_value"),
        ("destination_delivery", 450.0, "MXN", "gross_weight"),
    ]


def test_project_ownership_keeps_all_dingtalk_entities_and_assigns_only_single_choice() -> None:
    single = {
        "form_fields": {
            "项目归属Pertenencia del proyecto": [
                {"deptName": "YW MOLDES MX模具", "deptId": "D-MOLD"},
            ],
            "货物信息Bienes": [{"rowValue": [
                {"label": "物料编码 Código de material", "value": "M1"},
                {"label": "物料名称（中文）Nombre del material (chino)", "value": "模具"},
                {"label": "数量Cantidad", "value": "1"},
            ]}],
        }
    }
    multiple = {
        **single,
        "form_fields": {
            **single["form_fields"],
            "项目归属Pertenencia del proyecto": {
                "selectedOptions": [
                    {"deptName": "YW MOLDES MX模具", "itemId": "D-MOLD"},
                    {"deptName": "AmigoMart", "itemId": "D-AMIGO"},
                ]
            },
        },
    }

    assert import_oa_logistics.extract_project_candidates_from_approval(single)["candidates"] == [
        {"name": "YW MOLDES MX模具", "id": "D-MOLD"}
    ]
    assert build_oa_item_values_from_approval(single)[0]["project_collection"] == "YW MOLDES MX模具"
    multiple_item = build_oa_item_values_from_approval(multiple)[0]
    assert multiple_item.get("project_collection") in (None, "")
    assert json.loads(multiple_item["extra_json"])["project_candidates"] == [
        {"name": "YW MOLDES MX模具", "id": "D-MOLD"},
        {"name": "AmigoMart", "id": "D-AMIGO"},
    ]

    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": "货物预估方数：1.5方\n1.SISA报价：3720/立方\n合计价格：5730元\n2.大墨仓报价：2900/立方\n合计价格：4350元"
            }
        }
    )

    assert candidates == [
        {
            "carrier": "SISA",
            "amount": 5730,
            "currency": "RMB",
            "volume_m3": 1.5,
            "source_field": "物流报价Cotización de logística",
            "source_value": "货物预估方数：1.5方\n1.SISA报价：3720/立方\n合计价格：5730元\n2.大墨仓报价：2900/立方\n合计价格：4350元",
            "evidence_line": "合计价格：5730元",
            "evidence_line_no": 3,
            "status": "待确认",
        },
        {
            "carrier": "大墨仓",
            "amount": 4350,
            "currency": "RMB",
            "volume_m3": 1.5,
            "source_field": "物流报价Cotización de logística",
            "source_value": "货物预估方数：1.5方\n1.SISA报价：3720/立方\n合计价格：5730元\n2.大墨仓报价：2900/立方\n合计价格：4350元",
            "evidence_line": "合计价格：4350元",
            "evidence_line_no": 5,
            "status": "待确认",
        },
    ]


def test_extract_logistics_quote_candidates_reads_formula_amount_line() -> None:
    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "DHL报价：\n"
                    "运费=4075*（1+燃油附加费）+重量+155*（1+燃油附加费）+超过25kg的箱数+附加费\n"
                    "50.22*1.3825*33.2+155*1.3825*1+155*1=2674.33528元\n"
                    "每kg单价：80.5522674元"
                )
            }
        }
    )

    assert candidates == [
        {
            "carrier": "DHL",
            "amount": 2674.33528,
            "currency": "RMB",
            "volume_m3": None,
            "source_field": "物流报价Cotización de logística",
            "source_value": (
                "DHL报价：\n"
                "运费=4075*（1+燃油附加费）+重量+155*（1+燃油附加费）+超过25kg的箱数+附加费\n"
                "50.22*1.3825*33.2+155*1.3825*1+155*1=2674.33528元\n"
                "每kg单价：80.5522674元"
            ),
            "evidence_line": "50.22*1.3825*33.2+155*1.3825*1+155*1=2674.33528元",
            "evidence_line_no": 3,
            "status": "待确认",
        }
    ]


def test_quote_candidates_treat_per_unit_values_as_rates_not_total_amounts() -> None:
    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "预估方数：11.67方\n"
                    "体积方案：5000元/方 * 11.67 = 58,350元\n"
                    "重量方案：25元/kg * 4200 = 105,000元"
                )
            }
        }
    )

    assert [(row["amount"], row.get("pricing_basis")) for row in candidates] == [
        (58350.0, "volume"),
        (105000.0, "weight"),
    ]
    assert all(row["amount"] not in {5000, 25} for row in candidates)


def test_quote_candidates_ignore_rate_only_lines_without_a_total() -> None:
    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "体积方案：5000元/方\n重量方案：25元/kg"
                )
            }
        }
    )

    assert candidates == []


def test_extract_logistics_quote_candidates_reads_compact_dhl_quote_with_comma() -> None:
    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": "DHL报价，251元",
            }
        }
    )

    assert candidates == [
        {
            "carrier": "DHL",
            "amount": 251.0,
            "currency": "RMB",
            "volume_m3": None,
            "source_field": "物流报价Cotización de logística",
            "source_value": "DHL报价，251元",
            "evidence_line": "DHL报价，251元",
            "evidence_line_no": 1,
            "status": "待确认",
        }
    ]


def test_extract_logistics_text_summary_reads_dhl_express_text_block() -> None:
    summary = extract_logistics_text_summary_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "DHL报价：\n"
                    "运费=4075*（1+燃油附加费）*重量+155*（1+燃油附加费）*超过25kg的箱数+附加费*重量*附加费25折+超过25kg搬运费*超重箱数\n"
                    "50.22*1.3825*43.2+155*1.3825*1+155*1=3368.62678元\n"
                    "每kg单价：77.9774717592593元（含超重费用1箱）"
                ),
                "物流方式Camino Envío": "Express快递",
                "预计发货日期Fecha de Pre-entrega": "2026/8/12",
                "目标地区Países destinatarios": "MANZANILLO Mexico",
                "重量Peso（KG）": "43.2",
            }
        }
    )

    assert summary["transport_mode"] == "EXPRESS"
    assert summary["transport_mode_raw"] == "Express快递"
    assert summary["pre_delivery_date"] == "2026/8/12"
    assert summary["destination"] == "MANZANILLO Mexico"
    assert summary["gross_weight_kg"] == 43.2
    assert summary["logistics_quote_carrier"] == "DHL"
    assert summary["logistics_quote_amount"] == 3368.62678
    assert summary["logistics_quote_currency"] == "RMB"
    assert "50.22*1.3825*43.2" in summary["logistics_quote_evidence"]
    assert not summary.get("ai_used")


def test_extract_logistics_text_summary_uses_ai_fallback_without_overwriting_rule_values(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.setattr(import_oa_logistics, "_runtime_config_bool", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        import_oa_logistics,
        "_call_ai_logistics_text_summary",
        lambda source_text, base_summary: {
            "transport_mode": "EXPRESS",
            "transport_mode_raw": "Express快递",
            "destination": "MANZANILLO Mexico",
            "gross_weight_kg": 43.2,
            "logistics_quote_amount": 9999,
            "logistics_quote_currency": "RMB",
            "logistics_quote_carrier": "DHL",
            "logistics_quote_evidence": "AI返回的金额不应覆盖规则金额",
            "ai_used": True,
            "ai_model": "deepseek-test",
            "ai_confidence": 0.76,
            "ai_reason": "字段名不规范，使用 AI 兜底识别。",
        },
    )

    summary = extract_logistics_text_summary_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "DHL报价：\n"
                    "50.22*1.3825*43.2+155*1.3825*1+155*1=3368.62678元\n"
                    "每kg单价：77.9774717592593元"
                ),
                "业务备注": "目的地 MANZANILLO Mexico，重量 43.2KG，物流方式 Express快递",
            }
        }
    )

    assert summary["logistics_quote_amount"] == 3368.62678
    assert summary["logistics_quote_evidence"].startswith("50.22*1.3825*43.2")
    assert summary["transport_mode"] == "EXPRESS"
    assert summary["destination"] == "MANZANILLO Mexico"
    assert summary["gross_weight_kg"] == 43.2
    assert summary["ai_used"] is True
    assert summary["ai_model"] == "deepseek-test"


def test_extract_logistics_quote_candidates_reads_direct_40hq_quote_lines() -> None:
    candidates = extract_logistics_quote_candidates_from_approval(
        {
            "form_fields": {
                "物流报价Cotización de logística": (
                    "本周报价：\n"
                    "华运PIL：6050USD/40HQ+杂费\n"
                    "登泰PIL：5850USD/40HQ+杂费\n"
                    "彩虹捷运PIL：5850USD/40HQ+杂费\n"
                    "飞力达PIL：5850USD/40HQ+杂费\n"
                    "建议选择飞力达，可以借用货代仓库装柜"
                )
            }
        }
    )

    assert [(item["carrier"], item["amount"], item["currency"], item["remark"]) for item in candidates] == [
        ("华运PIL", 6050.0, "USD", "/40HQ+杂费"),
        ("登泰PIL", 5850.0, "USD", "/40HQ+杂费"),
        ("彩虹捷运PIL", 5850.0, "USD", "/40HQ+杂费"),
        ("飞力达PIL", 5850.0, "USD", "/40HQ+杂费"),
    ]
    assert all(item["status"] == "待确认" for item in candidates)


def test_extract_linked_purchase_approvals_from_relate_field_ext_value() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-001",
        "businessId": "202606101808000475588",
        "formComponentValues": [
            {
                "componentType": "RelateField",
                "name": "关联审批单Asociar órdenes de compra.",
                "value": (
                    '["采购支出 Gastos de compra enviado por Yadira Pérez Reyes",'
                    '"采购支出 Gastos de compra enviado por Yadira Pérez Reyes"]'
                ),
                "extValue": (
                    '{"list":['
                    '{"businessId":"202604300000000596348","procInstId":"5Qmu4-WKReWhGss44I3fyQ04891777478459"},'
                    '{"businessId":"202604150041000081318","procInstId":"xi3Aw3-rQDmc0H89KGjJmw04891776184868"}'
                    "]}"
                ),
            }
        ],
    }

    linked = extract_linked_purchase_approvals(instance)
    summary = summarize_approval(instance)

    assert len(linked) == 2
    assert linked[0]["approval_no"] == "202604300000000596348"
    assert linked[0]["source_instance_id"] == "5Qmu4-WKReWhGss44I3fyQ04891777478459"
    assert linked[0]["open_url"].startswith("dingtalk://")
    assert summary["linked_purchase_count"] == 2
    assert summary["linked_purchase_approvals"][1]["approval_no"] == "202604150041000081318"


def test_extract_linked_purchase_approvals_from_nested_relate_payload() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-NESTED",
        "formComponentValues": [
            {
                "componentType": "RelateField",
                "name": "Asocar órdenes de compra",
                "value": json.dumps(["采购支出 Gastos de compra enviado por Yadira"], ensure_ascii=False),
                "extValue": json.dumps(
                    {
                        "data": {
                            "list": [
                                {
                                    "businessId": "202604300000000596348",
                                    "url": "https://aflow.dingtalk.com/#/approval?procInstId=PROC-PURCHASE-NESTED",
                                    "title": "采购支出 Gastos de compra",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    }

    linked = extract_linked_purchase_approvals(instance)

    assert len(linked) == 1
    assert linked[0]["approval_no"] == "202604300000000596348"
    assert linked[0]["source_instance_id"] == "PROC-PURCHASE-NESTED"


def test_extract_linked_purchase_approvals_from_top_level_related_payload() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-RELATED",
        "processInstanceTitle": "国际物流 Logística Internacional",
        "formComponentValues": [],
        "relatedProcessInstances": [
            {
                "businessId": "202604150041000081318",
                "processInstanceId": "PROC-PURCHASE-TOP",
                "processInstanceTitle": "采购支出 Gastos de Compra",
                "url": "https://aflow.dingtalk.com/#/approval?procInstId=PROC-PURCHASE-TOP",
            },
            {
                "businessId": "202604150041000000000",
                "processInstanceId": "PROC-LOGISTICS-TOP",
                "processInstanceTitle": "国际物流 Logística Internacional",
            },
        ],
    }

    linked = extract_linked_purchase_approvals(instance)

    assert len(linked) == 1
    assert linked[0]["approval_no"] == "202604150041000081318"
    assert linked[0]["source_instance_id"] == "PROC-PURCHASE-TOP"


def test_extract_form_attachments_remains_form_only_while_summary_includes_comments() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-ATTACH",
        "businessId": "202607220001",
        "formComponentValues": [
            {
                "componentType": "DepartmentField",
                "name": "业务主体Entidad comercial",
                "extValue": json.dumps(
                    {"deptName": "YW MOLDES MX模具", "itemId": "1089528309", "name": "YW MOLDES MX模具"},
                    ensure_ascii=False,
                ),
            },
            {
                "componentType": "DDAttachment",
                "name": "Adjunto物品清单/运费报价等附件信息",
                "value": json.dumps(
                    [
                        {
                            "fileName": "2026.7.3DHL快递清单.xlsx",
                            "fileId": "FILE-001",
                            "spaceId": "SPACE-001",
                            "fileSize": 2048,
                        },
                        {
                            "fileName": "7月份燃油附加费.png",
                            "fileId": "FILE-002",
                            "spaceId": "SPACE-001",
                        },
                    ],
                    ensure_ascii=False,
                ),
            }
        ],
        "comments": [
            {
                "attachments": [
                    {"fileName": "评论里的凭证.pdf", "fileId": "COMMENT-FILE-001"},
                ]
            }
        ],
    }

    attachments = extract_form_attachments(instance)
    summary = summarize_approval(instance)
    values = build_batch_values_from_approval(
        {
            **summary,
            "transport_mode_raw": "contenedor marítimo海运整柜",
            "logistics_no": "TCLU1234567",
        }
    )
    extra = json.loads(values["extra_json"])

    assert len(attachments) == 2
    assert attachments[0]["file_name"] == "2026.7.3DHL快递清单.xlsx"
    assert attachments[0]["attachment_type"] == "Packing List"
    assert attachments[1]["attachment_type"] == "Logistics Bill"
    assert "评论里的凭证.pdf" not in [row["file_name"] for row in attachments]
    assert summary["oa_form_attachment_count"] == 2
    assert summary["oa_comment_attachment_count"] == 1
    assert values["source_attachment_count"] == 3
    assert values["subsidiary_code"] == "YW MOLDES MX模具"
    assert extra["oa_form_attachments"][0]["file_id"] == "FILE-001"
    assert extra["oa_comment_attachments"][0]["file_id"] == "COMMENT-FILE-001"
    assert extra["subsidiary"]["subsidiary_code"] == "YW MOLDES MX模具"


def test_extract_approval_attachments_includes_form_and_operation_record_files() -> None:
    shared = {
        "fileName": "重复凭证.pdf",
        "fileId": "FILE-SHARED",
        "spaceId": "SPACE-001",
        "fileSize": 128,
    }
    instance = {
        "processInstanceId": "PROC-SEA-ATTACH",
        "formComponentValues": [
            {
                "componentType": "DDAttachment",
                "name": "物品清单",
                "value": json.dumps(
                    [
                        {
                            "fileName": "packing.xlsx",
                            "fileId": "FILE-FORM",
                            "spaceId": "SPACE-001",
                        },
                        shared,
                    ],
                    ensure_ascii=False,
                ),
            }
        ],
        "operationRecords": [
            {
                "userId": "COMMENTER-001",
                "date": "2026-09-03 10:00:00",
                "remark": "补充凭证",
                "attachments": [shared, {"fileName": "评论附件.png", "fileId": "FILE-COMMENT", "spaceId": "SPACE-002"}],
            }
        ],
    }

    attachments = extract_approval_attachments(instance)

    assert [item["file_id"] for item in attachments] == ["FILE-FORM", "FILE-SHARED", "FILE-COMMENT"]
    assert [item["origin"] for item in attachments] == ["Form", "Form", "Comment"]
    assert attachments[2]["comment_user_id"] == "COMMENTER-001"
    assert attachments[2]["comment_time"] == "2026-09-03 10:00:00"
    assert attachments[2]["comment_remark"] == "补充凭证"


def test_summarize_approval_counts_form_and_comment_attachments() -> None:
    instance = {
        "processInstanceId": "PROC-SEA-ATTACH",
        "businessId": "OA-001",
        "formComponentValues": [
            {
                "componentType": "DDAttachment",
                "name": "物品清单",
                "value": json.dumps([{"fileName": "packing.xlsx", "fileId": "FILE-FORM"}]),
            }
        ],
        "operationRecords": [
            {
                "userId": "COMMENTER-001",
                "attachments": [{"fileName": "评论附件.pdf", "fileId": "FILE-COMMENT"}],
            }
        ],
    }

    summary = summarize_approval(instance)
    values = build_batch_values_from_approval(summary)
    extra = json.loads(values["extra_json"])

    assert summary["oa_attachment_count"] == 2
    assert summary["oa_form_attachment_count"] == 1
    assert summary["oa_comment_attachment_count"] == 1
    assert values["source_attachment_count"] == 2
    assert [item["origin"] for item in extra["oa_attachments"]] == ["Form", "Comment"]


def test_extract_subsidiary_from_approval_uses_business_entity_name() -> None:
    subsidiary = extract_subsidiary_from_approval(
        {
            "form_fields": {
                "业务主体Entidad comercial": {
                    "deptName": "YW MOLDES MX模具",
                    "itemId": "1089528309",
                    "name": "YW MOLDES MX模具",
                }
            }
        }
    )

    assert subsidiary == {
        "subsidiary_code": "YW MOLDES MX模具",
        "business_entity_name": "YW MOLDES MX模具",
        "business_entity_id": "1089528309",
        "source_field": "业务主体Entidad comercial",
        "source": "dingtalk_form_business_entity",
    }


def test_extract_subsidiary_from_empresas_prefers_display_value() -> None:
    instance = {
        "formComponentValues": [
            {
                "componentType": "DepartmentField",
                "name": "业务主体Empresas",
                "value": "1089528309",
                "extValue": json.dumps(
                    {"deptName": "YW MOLDES MX MOLDE", "itemId": "1089528309"},
                    ensure_ascii=False,
                ),
            }
        ]
    }

    fields = extract_form_fields(instance)
    subsidiary = extract_subsidiary_from_approval({"form_fields": fields})

    assert subsidiary["subsidiary_code"] == "YW MOLDES MX MOLDE"
    assert subsidiary["business_entity_id"] == "1089528309"
    assert subsidiary["source_field"] == "业务主体Empresas"


def test_extract_subsidiary_from_empresas_plain_text_value() -> None:
    fields = extract_form_fields(
        {
            "formComponentValues": [
                {
                    "componentType": "TextField",
                    "name": "业务主体Empresas",
                    "value": "YW MOLDES/UV",
                }
            ]
        }
    )

    subsidiary = extract_subsidiary_from_approval({"form_fields": fields})

    assert subsidiary["subsidiary_code"] == "YW MOLDES/UV"
    assert subsidiary["business_entity_name"] == "YW MOLDES/UV"
    assert subsidiary["source_field"] == "业务主体Empresas"


def test_extract_subsidiary_falls_back_to_raw_form_components() -> None:
    subsidiary = extract_subsidiary_from_approval(
        {
            "form_fields": {},
            "raw_form_components": [
                {
                    "componentType": "DepartmentField",
                    "name": "业务主体Empresas",
                    "value": "1089528309",
                    "extValue": json.dumps(
                        {"deptName": "YW MOLDES MX", "itemId": "1089528309"},
                        ensure_ascii=False,
                    ),
                }
            ],
        }
    )

    assert subsidiary["subsidiary_code"] == "YW MOLDES MX"
    assert subsidiary["business_entity_id"] == "1089528309"
    assert subsidiary["source_field"] == "业务主体Empresas"


def test_extract_purchase_expense_rows_keeps_first_non_empty_currency() -> None:
    instance = {
        "processInstanceId": "PROC-PURCHASE-001",
        "businessId": "202604150041000081318",
        "title": "采购支出 Gastos de compra enviado por Yadira Pérez Reyes",
        "status": "RUNNING",
        "formComponentValues": [
            {"componentType": "DDSelectField", "name": "币种Moneda", "value": "人民币RMB"},
            {
                "componentType": "TableField",
                "name": "需求明细Desglose de los gastos",
                "value": json.dumps(
                    [
                        {
                            "rowValue": [
                                {"label": "物品名称Nombre del artículo", "value": "TPU原料 HF-8695AU"},
                                {"label": "物品编码Código", "value": "YL000097"},
                                {"label": "物品规格Especificacion", "value": "HF-8695AU"},
                                {"label": "数量Cantidad", "value": "10000"},
                                {"label": "单位Unidad", "value": "KG"},
                                {"label": "单价Precio", "value": "2.2"},
                                {"label": "总金额Monto Total", "value": "22000"},
                            ],
                            "rowNumber": "TableField_119WFD19L8R40_1",
                        }
                    ],
                    ensure_ascii=False,
                ),
            },
            {"componentType": "DDSelectField", "name": "币种Moneda", "value": None},
        ],
    }

    fields = extract_form_fields(instance)
    rows = extract_purchase_expense_rows(instance)
    mapped_items = build_purchase_expense_item_values_from_approval(instance)
    summary = summarize_purchase_approval(instance)

    assert fields["币种Moneda"] == "人民币RMB"
    assert rows[0]["币种Moneda"] == "人民币RMB"
    assert mapped_items[0]["material_code"] == "YL000097"
    assert mapped_items[0]["unit_price"] == 2.2
    assert mapped_items[0]["goods_value"] == 22000
    assert mapped_items[0]["purchase_currency"] == "人民币RMB"
    assert summary["detail_row_count"] == 1
    assert summary["mapped_preview_items"][0]["product_name"] == "TPU原料 HF-8695AU"


def test_legacy_dingtalk_instance_is_normalized_for_summary() -> None:
    legacy = _normalize_legacy_instance(
        {
            "business_id": "202607210002",
            "status": "COMPLETED",
            "form_component_values": [{"name": "物流方式Camino Envío", "value": "SEA"}],
        },
        "PROC-OLD-001",
    )
    summary = summarize_approval(legacy)

    assert legacy["processInstanceId"] == "PROC-OLD-001"
    assert summary["source_instance_id"] == "PROC-OLD-001"
    assert summary["source_approval_no"] == "202607210002"
    assert is_sea_approval(summary["form_fields"]) is True


def test_load_env_file_keeps_existing_values_by_default(monkeypatch) -> None:
    env_file = Path.cwd() / ".tmp_dingtalk_env_test"
    env_file.write_text(
        "DINGTALK_PROCESS_CODE=FROM_FILE\nDINGTALK_LIST_API=old\nDINGTALK_APPKEY=APPKEY\nDINGTALK_APPSECRET=APPSECRET\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DINGTALK_PROCESS_CODE", "EXISTING")
    monkeypatch.delenv("DINGTALK_APP_KEY", raising=False)
    monkeypatch.delenv("DINGTALK_APP_SECRET", raising=False)

    try:
        load_env_file(str(env_file))
    finally:
        env_file.unlink(missing_ok=True)

    assert os.environ["DINGTALK_PROCESS_CODE"] == "EXISTING"
    assert os.environ["DINGTALK_LIST_API"] == "old"
    assert os.environ["DINGTALK_APP_KEY"] == "APPKEY"
    assert os.environ["DINGTALK_APP_SECRET"] == "APPSECRET"


def test_logistics_process_code_does_not_use_budget_process_env(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_PROCESS_CODE", "PROC-BUDGET-001")
    monkeypatch.delenv("DINGTALK_LOGISTICS_PROCESS_CODE", raising=False)

    assert resolve_logistics_process_code() == DEFAULT_LOGISTICS_PROCESS_CODE

    monkeypatch.setenv("DINGTALK_LOGISTICS_PROCESS_CODE", "PROC-LOGISTICS-ENV")

    assert resolve_logistics_process_code() == "PROC-LOGISTICS-ENV"
    assert resolve_logistics_process_code("PROC-CLI") == "PROC-CLI"


def test_purchase_process_code_requires_purchase_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_PROCESS_CODE", "PROC-BUDGET-001")
    monkeypatch.delenv("DINGTALK_PURCHASE_PROCESS_CODE", raising=False)
    monkeypatch.delenv("DINGTALK_PURCHASE_EXPENSE_PROCESS_CODE", raising=False)
    monkeypatch.delenv("DINGTALK_PROCESS_CODES", raising=False)

    assert resolve_purchase_process_code() == ""

    monkeypatch.setenv("DINGTALK_PURCHASE_PROCESS_CODE", "PROC-PURCHASE-ENV")

    assert resolve_purchase_process_code() == "PROC-PURCHASE-ENV"
    assert resolve_purchase_process_code("PROC-PURCHASE-CLI") == "PROC-PURCHASE-CLI"

    monkeypatch.delenv("DINGTALK_PURCHASE_PROCESS_CODE", raising=False)
    monkeypatch.setenv("DINGTALK_PROCESS_CODES", '["PROC-OPERATION","PROC-PURCHASE-LIST"]')

    assert resolve_purchase_process_code() == "PROC-PURCHASE-LIST"


def test_purchase_process_code_reads_frappe_site_config(monkeypatch) -> None:
    from types import SimpleNamespace

    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.delenv("DINGTALK_PURCHASE_PROCESS_CODE", raising=False)
    monkeypatch.delenv("DINGTALK_PURCHASE_EXPENSE_PROCESS_CODE", raising=False)
    monkeypatch.delenv("DINGTALK_PROCESS_CODES", raising=False)
    monkeypatch.setattr(
        import_oa_logistics,
        "frappe",
        SimpleNamespace(conf={"overseas_costing_dingtalk_purchase_process_code": "PROC-PURCHASE-SITE"}),
    )

    assert resolve_purchase_process_code() == "PROC-PURCHASE-SITE"


def test_get_access_token_reads_legacy_credentials_from_frappe_site_config(monkeypatch) -> None:
    from types import SimpleNamespace

    from overseas_costing.scripts import import_oa_logistics

    captured = {}

    def fake_request_json(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {"errcode": 0, "access_token": "SITE-TOKEN"}

    monkeypatch.delenv("DINGTALK_APP_KEY", raising=False)
    monkeypatch.delenv("DINGTALK_APP_SECRET", raising=False)
    monkeypatch.delenv("DINGTALK_APPKEY", raising=False)
    monkeypatch.delenv("DINGTALK_APPSECRET", raising=False)
    monkeypatch.delenv("DINGTALK_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(import_oa_logistics, "_request_json", fake_request_json)
    monkeypatch.setattr(
        import_oa_logistics,
        "frappe",
        SimpleNamespace(
            conf={
                "overseas_costing_dingtalk_app_key": "SITE-APP-KEY",
                "overseas_costing_dingtalk_app_secret": "SITE-APP-SECRET",
            }
        ),
    )

    assert get_access_token(api_style="legacy") == "SITE-TOKEN"
    assert "appkey=SITE-APP-KEY" in captured["url"]
    assert "appsecret=SITE-APP-SECRET" in captured["url"]


def test_completed_approval_status_filters_running() -> None:
    assert is_completed_approval_status("COMPLETED") is True
    assert is_completed_approval_status("审批通过") is True
    assert is_completed_approval_status("RUNNING") is False
    assert is_completed_approval_status("TERMINATED") is False


def test_approval_decision_combines_process_status_and_result() -> None:
    assert resolve_approval_decision("COMPLETED", "refuse") == {
        "process_status": "COMPLETED",
        "approval_result": "refuse",
        "effective_status": "REJECTED",
        "excluded": True,
    }
    assert resolve_approval_decision("TERMINATED", "agree")["excluded"] is True
    assert resolve_approval_decision("COMPLETED", "agree")["effective_status"] == "COMPLETED"
    assert resolve_approval_decision("RUNNING", "agree")["effective_status"] == "RUNNING"
    assert is_completed_approval_status("COMPLETED", result="refuse") is False
    assert is_completed_approval_status("", result="refuse") is False


def test_pull_purchase_expense_approvals_reads_process_details(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.setenv("DINGTALK_PURCHASE_PROCESS_CODE", "PROC-PURCHASE")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")
    monkeypatch.setattr(import_oa_logistics, "list_process_instance_ids", lambda **_kwargs: ["PROC-PURCHASE-001"])

    def fake_get_process_instance_detail(**_kwargs):
        return {
            "processInstanceId": "PROC-PURCHASE-001",
            "businessId": "202604150041000081318",
            "title": "采购支出 Gastos de Compra",
            "status": "COMPLETED",
            "formComponentValues": [
                {"componentType": "TextField", "name": "币种Moneda", "value": "人民币RMB"},
                {
                    "componentType": "TableField",
                    "name": "需求明细",
                    "value": json.dumps(
                        [
                            {
                                "rowValue": [
                                    {"label": "物品编码Código", "value": "YL000097"},
                                    {"label": "物品名称Nombre del artículo", "value": "TPU原料"},
                                    {"label": "数量Cantidad", "value": "10000"},
                                    {"label": "单价Precio", "value": "2.9"},
                                    {"label": "总金额Monto Total", "value": "29000"},
                                ]
                            }
                        ],
                        ensure_ascii=False,
                    ),
                },
            ],
        }

    monkeypatch.setattr(import_oa_logistics, "get_process_instance_detail", fake_get_process_instance_detail)

    result = pull_purchase_expense_approvals(process_code="", start="2026-04-01", end="2026-04-30")

    assert result["ok"] is True
    assert result["process_code"] == "PROC-PURCHASE"
    assert result["detail_count"] == 1
    assert result["items"][0]["detail_row_count"] == 1
    assert result["items"][0]["mapped_preview_items"][0]["material_code"] == "YL000097"
    assert result["items"][0]["mapped_preview_items"][0]["unit_price"] == 2.9


def test_extract_purchase_expense_rows_from_plain_text_detail() -> None:
    instance = {
        "formComponentValues": [
            {"componentType": "TextField", "name": "币种Moneda", "value": "人民币RMB"},
            {
                "componentType": "TextareaField",
                "name": "采购明细",
                "value": (
                    "物品编码Código: YL000097 物品名称Nombre del artículo: TPU原料 "
                    "物品规格Especificacion: 25KG 数量Cantidad: 10000 单价Precio: 2.9 总金额Monto Total: 29000\n"
                    "FL004104 包装袋 1000 0.049 49"
                ),
            },
        ]
    }

    rows = extract_purchase_expense_rows(instance)
    mapped = build_purchase_expense_item_values_from_approval(instance)

    assert len(rows) == 2
    assert rows[0]["物品编码Código"] == "YL000097"
    assert rows[0]["币种Moneda"] == "人民币RMB"
    assert mapped[0]["unit_price"] == 2.9
    assert mapped[1]["material_code"] == "FL004104"
    assert mapped[1]["product_name"] == "包装袋"
    assert mapped[1]["goods_value"] == 49


def test_pull_purchase_expense_approvals_skips_running_by_default(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.setenv("DINGTALK_PURCHASE_PROCESS_CODE", "PROC-PURCHASE")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")
    monkeypatch.setattr(import_oa_logistics, "list_process_instance_ids", lambda **_kwargs: ["PROC-PURCHASE-RUNNING"])
    monkeypatch.setattr(
        import_oa_logistics,
        "get_process_instance_detail",
        lambda **_kwargs: {
            "processInstanceId": "PROC-PURCHASE-RUNNING",
            "businessId": "202604150041000081318",
            "title": "采购支出 Gastos de Compra",
            "status": "RUNNING",
            "formComponentValues": [],
        },
    )

    result = pull_purchase_expense_approvals(process_code="", start="2026-04-01", end="2026-04-30")

    assert result["detail_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped_items"][0]["reason"] == "采购支出审批未完成"


def test_sync_purchase_expenses_from_process_requires_process_code(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.delenv("DINGTALK_PURCHASE_PROCESS_CODE", raising=False)
    monkeypatch.delenv("DINGTALK_PURCHASE_EXPENSE_PROCESS_CODE", raising=False)
    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")

    result = sync_purchase_expenses_from_process(start="2026-04-01", end="2026-04-30")

    assert result["ok"] is False
    assert "DINGTALK_PURCHASE_PROCESS_CODE" in result["message"]


def test_sync_purchase_expenses_from_process_reports_batch_write_failure(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{"name": "BATCH-001", "batch_no": "B-001", "current_version": "VER-001"}]

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda _env=None: "")
    monkeypatch.setattr(import_oa_logistics, "resolve_purchase_process_code", lambda _code="": "PROC-PURCHASE")
    monkeypatch.setattr(import_oa_logistics, "pull_purchase_expense_approvals", lambda **_kwargs: {
        "ok": True,
        "items": [{"source_instance_id": "PROC-PUR-001", "mapped_preview_items": []}],
    })
    monkeypatch.setattr(import_service, "apply_linked_purchase_expense_fillable_fields", lambda **_kwargs: {
        "ok": False,
        "audit_logged": False,
        "message": "审计写入失败",
    })
    monkeypatch.setattr(import_oa_logistics, "_recalculate_after_purchase_sync", lambda **_kwargs: {"action": "skipped"})

    result = sync_purchase_expenses_from_process(start="2026-01-01", end="2026-09-04")

    assert result["ok"] is False
    assert result["failed_count"] == 1


def test_save_sea_approvals_to_erp_reports_purchase_sync_failure(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_oa_logistics, "_resolve_existing_batch_name", lambda _values: "BATCH-001")
    monkeypatch.setattr(import_oa_logistics, "_update_oa_trace_batch", lambda *_args: {
        "action": "updated", "batch_name": "BATCH-001", "version_name": "VER-001"
    })
    monkeypatch.setattr(import_oa_logistics, "_sync_oa_goods_items", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(import_oa_logistics, "_sync_oa_form_attachments", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(import_oa_logistics, "_sync_linked_purchase_fields", lambda **_kwargs: {
        "ok": False, "audit_logged": False, "message": "审计写入失败"
    })
    monkeypatch.setattr(import_oa_logistics, "_sync_oa_logistics_allocation_rule", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(import_oa_logistics, "_recalculate_after_purchase_sync", lambda **_kwargs: {"action": "skipped"})
    monkeypatch.setattr(import_oa_logistics, "_commit_oa_pull_progress", lambda: None)
    monkeypatch.setattr(import_oa_logistics, "_run_with_database_retry", lambda callback: callback())

    result = save_sea_approvals_to_erp({"items": [{
        "source_approval_no": "OA-001",
        "source_instance_id": "PROC-LOG-001",
        "approval_status": "COMPLETED",
        "transport_mode": "SEA",
        "form_fields": {},
    }]})

    assert result["ok"] is False
    assert result["failed_count"] == 1


def test_preview_purchase_expenses_from_process_matches_existing_batches(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            return [
                {"name": "BATCH-FSCU", "batch_no": "FSCU8486789", "current_version": "VER-FSCU"},
                {"name": "BATCH-HPCU", "batch_no": "HPCU5155607", "current_version": "VER-HPCU"},
            ]

    purchase_summary = {
        "source_approval_no": "202604150041000081318",
        "source_instance_id": "PROC-PURCHASE-001",
        "approval_title": "采购支出 Gastos de Compra",
        "detail_row_count": 1,
        "purchase_currency": "人民币RMB",
        "mapped_preview_items": [{"material_code": "YL000097", "unit_price": 2.9}],
    }
    preview_calls: list[dict] = []

    def fake_pull_purchase_expense_approvals(**_kwargs):
        return {"ok": True, "detail_count": 1, "items": [purchase_summary], "skipped_items": []}

    def fake_preview_linked_purchase_expense_oa(**kwargs):
        preview_calls.append(kwargs)
        if kwargs["batch_name"] == "BATCH-FSCU":
            return {
                "writeback_preview": {
                    "matched_count": 1,
                    "writable_row_count": 1,
                    "fillable_row_count": 1,
                    "conflict_row_count": 0,
                    "same_row_count": 0,
                    "unmatched_count": 0,
                    "ambiguous_count": 0,
                    "matched_rows": [
                        {
                            "target_row_no": 1,
                            "target_material_code": "YL000097",
                            "target_product_name": "TPU原料",
                            "target_spec_model": "",
                            "mapped_row": {"material_code": "YL000097", "unit_price": 2.9},
                            "business_changes": [
                                {"fieldname": "purchase_unit_price", "new_value": 2.9, "status": "fillable"}
                            ],
                        }
                    ],
                }
            }
        return {
            "writeback_preview": {
                "matched_count": 0,
                "writable_row_count": 0,
                "fillable_row_count": 0,
                "conflict_row_count": 0,
                "same_row_count": 0,
                "unmatched_count": 1,
                "ambiguous_count": 0,
                "matched_rows": [],
            }
        }

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe())
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setenv("DINGTALK_PURCHASE_PROCESS_CODE", "PROC-PURCHASE")
    monkeypatch.setattr(import_oa_logistics, "pull_purchase_expense_approvals", fake_pull_purchase_expense_approvals)
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", fake_preview_linked_purchase_expense_oa)

    result = preview_purchase_expenses_from_process(start="2026-04-01", end="2026-04-30")

    assert result["ok"] is True
    assert result["matched_batch_count"] == 1
    assert result["writable_batch_count"] == 1
    assert result["writable_row_count"] == 1
    assert result["purchase_summary_count"] == 1
    assert result["mapped_purchase_row_count"] == 1
    assert result["pull"]["items"][0]["can_open"] is True
    assert result["mapped_purchase_rows"][0]["source_approval_no"] == "202604150041000081318"
    assert result["items"][0]["batch_no"] == "FSCU8486789"
    assert len(preview_calls) == 2
    assert preview_calls[0]["purchase_summaries_json"]


def test_build_batch_values_from_oa_logistics_approval() -> None:
    values = build_batch_values_from_approval(
        {
            "source_approval_no": "202601291020000337788",
            "source_instance_id": "PROC-SEA-TRACE",
            "approval_title": "国际物流 Logística Internacional",
            "approval_status": "COMPLETED",
            "originator_userid": "USER-001",
            "originator_dept_id": "DEPT-001",
            "create_time": "2026-01-29T10:20Z",
            "finish_time": "2026-04-21T17:16Z",
            "transport_mode_raw": "contenedor marítimo海运整柜",
            "logistics_no": "HPCU5155607",
            "form_fields": {
                "Adjunto物品清单/运费报价等附件信息": [
                    {"fileName": "2026.1.29装箱单.xlsx", "fileId": "209810480976"}
                ]
            },
        }
    )

    assert values["batch_no"] == "HPCU5155607"
    assert values["waybill_no"] == "HPCU5155607"
    assert values["container_no"] == "HPCU5155607"
    assert values["transport_mode"] == "SEA"
    assert values["business_type"] == "SEA_STANDARD"
    assert values["source_type"] == "oa_logistics"
    assert values["source_approval_no"] == "202601291020000337788"
    assert values["source_instance_id"] == "PROC-SEA-TRACE"
    assert values["source_attachment_count"] == 1
    assert values["source_created_at"] == "2026-01-29 18:20:00"


def test_oa_datetime_uses_shanghai_day_for_utc_and_epoch_values() -> None:
    from overseas_costing.scripts.import_oa_logistics import _to_frappe_datetime

    assert _to_frappe_datetime("2026-01-29T16:30:00Z") == "2026-01-30 00:30:00"
    assert _to_frappe_datetime("1769704200000") == "2026-01-30 00:30:00"


def test_build_batch_values_keeps_linked_purchase_approvals_in_extra_json() -> None:
    values = build_batch_values_from_approval(
        {
            "source_approval_no": "202606101808000475588",
            "source_instance_id": "PROC-SEA-TRACE",
            "approval_status": "COMPLETED",
            "transport_mode_raw": "contenedor marítimo海运整柜",
            "logistics_no": "FSCU8486789",
            "linked_purchase_approvals": [
                {
                    "approval_no": "202604300000000596348",
                    "source_instance_id": "5Qmu4-WKReWhGss44I3fyQ04891777478459",
                }
            ],
            "form_fields": {},
        }
    )

    extra = json.loads(values["extra_json"])

    assert extra["linked_purchase_approvals"][0]["approval_no"] == "202604300000000596348"


def test_merge_oa_extra_json_preserves_existing_excel_payload() -> None:
    merged = _merge_oa_extra_json(
        json.dumps({"source": "excel", "sourceSheet": "2026年YUEWEI"}, ensure_ascii=False),
        json.dumps(
            {
                "source": "dingtalk_oa_logistics",
                "transport_mode_raw": "海运",
                "linked_purchase_approvals": [{"approval_no": "202604300000000596348"}],
            },
            ensure_ascii=False,
        ),
    )

    payload = json.loads(merged)

    assert payload["source"] == "excel"
    assert payload["sourceSheet"] == "2026年YUEWEI"
    assert payload["oa_logistics_trace"]["linked_purchase_approvals"][0]["approval_no"] == "202604300000000596348"


def test_extract_oa_goods_rows_and_build_item_values() -> None:
    approval = {
        "source_approval_no": "202606101808000475588",
        "source_instance_id": "PROC-SEA-FORM",
        "transport_mode_raw": "contenedor marítimo海运整柜",
        "logistics_no": "FSCU8486789",
        "form_fields": {
            "项目proyecto": "YW ODM",
            "物料类别TIPO": "material物料",
            "货物信息Bienes": [
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "物料编码"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "物料名称中文"},
                        {"label": "物料名称（西语）Nombre del material (español)", "value": "物料名称西语"},
                        {"label": "规格型号Especificación / Modelo", "value": "规格型号"},
                        {"label": "数量Cantidad", "value": "数量"},
                        {"label": "单位Unidad", "value": "单位"},
                        {"label": "收件人Destinatario", "value": "收货人"},
                    ],
                    "rowNumber": "TableField_HEADER",
                },
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "YL000097"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "TPU原料 HF-8695AU"},
                        {"label": "物料名称（西语）Nombre del material (español)", "value": "Elastómero de poliuretano termoplástico"},
                        {"label": "规格型号Especificación / Modelo", "value": "HF-8695AU"},
                        {"label": "数量Cantidad", "value": "10000"},
                        {"label": "单位Unidad", "value": "KG"},
                        {"label": "收件人Destinatario", "value": "Alfredo Garcia Cardenas"},
                    ],
                    "rowNumber": "TableField_1",
                }
            ],
        },
    }

    rows = extract_oa_goods_rows(approval)
    items = build_oa_item_values_from_approval(approval)

    assert len(rows) == 1
    assert len(items) == 1
    assert rows[0]["项目proyecto"] == "YW ODM"
    assert items[0]["material_code"] == "YL000097"
    assert items[0]["product_name"] == "TPU原料 HF-8695AU"
    assert items[0]["product_name_es"] == "Elastómero de poliuretano termoplástico"
    assert items[0]["spec_model"] == "HF-8695AU"
    assert items[0]["quantity"] == 10000
    assert items[0]["unit"] == "KG"
    assert items[0]["recipient"] == "Alfredo Garcia Cardenas"
    assert items[0]["waybill_no"] == "FSCU8486789"
    assert items[0]["source_doc_no"] == "202606101808000475588"
    assert items[0]["parse_status"] == "SUCCESS"


def test_extract_oa_goods_rows_reads_new_material_category_field() -> None:
    """新表单单头字段名是「物料类别Tipo de Material」，必须能注入到货物行 category。"""

    approval = {
        "source_approval_no": "202609221831000580722",
        "source_instance_id": "PROC-SEA-NEW",
        "transport_mode_raw": "DDP Marítimo海运DDP",
        "logistics_no": None,
        "form_fields": {
            "物料类别Tipo de Material": "Material物料",
            "货物信息Bienes": [
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "FL004116"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "笔式万用表"},
                        {"label": "数量Cantidad", "value": "50"},
                        {"label": "重量 Peso", "value": "12.85"},
                        {"label": "单位Unidad", "value": "个"},
                        {"label": "货值Valor de mercancía", "value": "1250"},
                        {"label": "币种Moneda", "value": "人民币RMB"},
                    ],
                    "rowNumber": "TableField_1",
                }
            ],
        },
    }

    items = build_oa_item_values_from_approval(approval)

    assert items[0]["category"] == "Material物料"
    assert items[0]["goods_value"] == "1250"
    assert items[0]["purchase_currency"] == "人民币RMB"


def test_extract_oa_goods_text_rows_and_skip_summary() -> None:
    approval = {
        "source_approval_no": "202601121522000486665",
        "source_instance_id": "PROC-SEA-TEXT",
        "transport_mode_raw": "海运",
        "logistics_no": "202601121522000486665",
        "form_fields": {
            "货物信息Bienes": """
                GJ003786-灯管-8pcs
                FL002598-灯管+连接线-30pcs
                热熔胶--100pcs
                合计47件，重量470.71kg，体积：1.53方
            """,
        },
    }

    rows = extract_oa_goods_rows(approval)
    items = build_oa_item_values_from_approval(approval)

    assert len(rows) == 3
    assert rows[0]["material_code"] == "GJ003786"
    assert rows[0]["product_name"] == "灯管"
    assert rows[0]["quantity"] == "8"
    assert rows[0]["unit"] == "个"
    assert rows[1]["material_code"] == "FL002598"
    assert rows[1]["product_name"] == "灯管+连接线"
    assert "material_code" not in rows[2]
    assert rows[2]["product_name"] == "热熔胶"
    assert rows[2]["quantity"] == "100"
    assert [item["quantity"] for item in items] == [8.0, 30.0, 100.0]
    assert items[2]["material_code"] is None


def test_build_oa_item_values_allocates_header_weight_by_quantity() -> None:
    approval = {
        "source_approval_no": "202608111418000558262",
        "source_instance_id": "PROC-EXPRESS-WEIGHT",
        "transport_mode_raw": "Express快递",
        "form_fields": {
            "重量Peso（KG）": "33.2",
            "货物信息Bienes": [
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "MHA101290"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "超队TPU模具"},
                        {"label": "规格型号Especificación / Modelo", "value": "Honor X8D 4G"},
                        {"label": "数量Cantidad", "value": "1"},
                    ],
                    "rowNumber": "ROW-1",
                },
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "MHA201290"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "超队PC模具"},
                        {"label": "规格型号Especificación / Modelo", "value": "Honor X8D 4G"},
                        {"label": "数量Cantidad", "value": "1"},
                    ],
                    "rowNumber": "ROW-2",
                },
            ],
        },
    }

    items = build_oa_item_values_from_approval(approval)

    assert [item["material_code"] for item in items] == ["MHA101290", "MHA201290"]
    assert [item["gross_weight_kg"] for item in items] == [16.6, 16.6]


def test_line_weight_column_is_authoritative_and_missing_rows_are_not_inferred() -> None:
    approval = {
        "form_fields": {
            "重量Peso（KG）": "33.2",
            "货物信息Bienes": [
                {"rowValue": [
                    {"label": "物料编码 Código de material", "value": "M1"},
                    {"label": "物料名称（中文）Nombre del material (chino)", "value": "物料一"},
                    {"label": "数量Cantidad", "value": "1"},
                    {"label": "重量 Peso", "value": "12.5"},
                ]},
                {"rowValue": [
                    {"label": "物料编码 Código de material", "value": "M2"},
                    {"label": "物料名称（中文）Nombre del material (chino)", "value": "物料二"},
                    {"label": "数量Cantidad", "value": "1"},
                    {"label": "重量 Peso", "value": ""},
                ]},
            ],
        }
    }

    items = build_oa_item_values_from_approval(approval)

    assert items[0]["gross_weight_kg"] == 12.5
    assert items[1].get("gross_weight_kg") in (None, "")
    assert json.loads(items[1]["extra_json"])["goods_table_columns"][-1] == "重量 Peso"


def test_build_oa_item_values_keeps_spec_model_for_air_approval() -> None:
    approval = {
        "source_approval_no": "202608140001",
        "source_instance_id": "PROC-AIR-SPEC",
        "transport_mode_raw": "空运",
        "logistics_no": "AIR-001",
        "form_fields": {
            "货物信息Bienes": [
                {
                    "rowValue": [
                        {"label": "物料编码 Código de material", "value": "AIR001"},
                        {"label": "物料名称（中文）Nombre del material (chino)", "value": "空运测试物料"},
                        {"label": "物料名称（西语）Nombre del material (español)", "value": "Material de prueba"},
                        {"label": "规格型号Especificación / Modelo", "value": "AIR-SPEC-01"},
                        {"label": "数量Cantidad", "value": "3"},
                        {"label": "单位Unidad", "value": "pieza"},
                    ],
                    "rowNumber": "ROW-1",
                }
            ],
        },
    }

    items = build_oa_item_values_from_approval(approval)

    assert len(items) == 1
    assert items[0]["transport_mode"] == "AIR"
    assert items[0]["spec_model"] == "AIR-SPEC-01"
    assert items[0]["unit"] == "个"
    assert items[0]["waybill_no"] == "AIR-001"


def test_save_sea_approvals_to_erp_dry_run_returns_trace_preview() -> None:
    result = save_sea_approvals_to_erp(
        {
            "items": [
                {
                    "source_approval_no": "202601291020000337788",
                    "source_instance_id": "PROC-SEA-TRACE",
                    "approval_status": "COMPLETED",
                    "transport_mode_raw": "contenedor marítimo海运整柜",
                    "logistics_no": "HPCU5155607",
                    "form_fields": {},
                }
            ]
        }
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["valid_count"] == 1
    assert result["items"][0]["batch_no"] == "HPCU5155607"


def test_transport_mode_follows_camino_envio_value() -> None:
    sea_double_clear = build_batch_values_from_approval(
        {
            "source_approval_no": "202601301527000335149",
            "source_instance_id": "PROC-SEA-DOUBLE-CLEAR",
            "approval_status": "RUNNING",
            "transport_mode_raw": "doble despacho en aduana para transporte marítimo海运双清",
            "logistics_no": "",
            "form_fields": {},
        }
    )
    express = build_batch_values_from_approval(
        {
            "source_approval_no": "202601300932000271071",
            "source_instance_id": "PROC-EXPRESS",
            "approval_status": "COMPLETED",
            "transport_mode_raw": "correo express快递",
            "logistics_no": "",
            "form_fields": {},
        }
    )

    assert sea_double_clear["transport_mode"] == "SEA"
    assert express["transport_mode"] == "EXPRESS"


def test_existing_oa_batch_refreshes_transport_mode_and_corrected_source_time(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    set_values = []
    audits = []

    class FakeMeta:
        @staticmethod
        def has_field(_fieldname):
            return True

    class FakeDB:
        @staticmethod
        def get_value(doctype, filters, fieldname=None, as_dict=False):
            if doctype == "Overseas Cost Batch" and filters == "BATCH-001" and fieldname == "current_version":
                return "VER-001"
            if doctype == "Overseas Cost Batch" and filters == "BATCH-001" and isinstance(fieldname, list):
                current = {name: "" for name in fieldname}
                current["transport_mode"] = "SEA"
                current["source_created_at"] = "2026-01-29 16:30:00"
                return current if as_dict else current
            return None

        @staticmethod
        def set_value(doctype, name, values, update_modified=False):
            set_values.append(
                {
                    "doctype": doctype,
                    "name": name,
                    "values": dict(values),
                    "update_modified": update_modified,
                }
            )

    class FakeDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audits.append(self.payload)
            return self

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_meta(_doctype):
            return FakeMeta()

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)

    result = import_oa_logistics._update_oa_trace_batch("BATCH-001", {
        "transport_mode": "EXPRESS", "source_created_at": "2026-01-30 00:30:00",
    })

    assert result["action"] == "updated"
    assert result["changed_fields"] == ["transport_mode", "source_created_at"]
    assert set_values[0]["values"] == {
        "transport_mode": "EXPRESS", "source_created_at": "2026-01-30 00:30:00",
    }
    assert audits[0]["field_name"] == "oa_logistics_trace"


def test_sync_oa_form_attachments_creates_form_and_comment_records(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    inserted_attachments = []
    inserted_audits = []

    class FakeDoc:
        def __init__(self, payload):
            self.payload = dict(payload)
            self.__dict__.update(payload)
            self.name = payload.get("name") or f"DOC-{len(inserted_attachments) + len(inserted_audits) + 1}"

        def insert(self, **_kwargs):
            if self.payload.get("doctype") == "Overseas Cost Attachment":
                self.name = f"ATTACH-{len(inserted_attachments) + 1}"
                self.payload["name"] = self.name
                inserted_attachments.append(self.payload)
            elif self.payload.get("doctype") == "Overseas Cost Audit Log":
                self.name = f"AUDIT-{len(inserted_audits) + 1}"
                self.payload["name"] = self.name
                inserted_audits.append(self.payload)
            return self

        def save(self, **_kwargs):
            return self

    class FakeDB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return None

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_doc(*args):
            if len(args) == 1 and isinstance(args[0], dict):
                return FakeDoc(args[0])
            raise AssertionError(args)

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)

    result = _sync_oa_form_attachments(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "source_approval_no": "202607220001",
            "source_instance_id": "PROC-SEA-ATTACH",
            "oa_attachments": [
                {
                    "origin": "Form",
                    "source_field": "Adjunto物品清单/运费报价等附件信息",
                    "component_type": "DDAttachment",
                    "file_id": "FILE-001",
                    "space_id": "SPACE-001",
                    "file_name": "2026.7.3DHL快递清单.xlsx",
                    "file_ext": "xlsx",
                    "file_url": "",
                    "attachment_type": "Packing List",
                    "raw": {"fileName": "2026.7.3DHL快递清单.xlsx", "fileId": "FILE-001"},
                },
                {
                    "origin": "Comment",
                    "source_field": "operationRecords",
                    "component_type": "CommentAttachment",
                    "file_id": "FILE-002",
                    "space_id": "SPACE-002",
                    "file_name": "评论凭证.pdf",
                    "file_ext": "pdf",
                    "file_url": "",
                    "attachment_type": "Other",
                    "comment_user_id": "COMMENTER-001",
                    "comment_time": "2026-09-03 10:00:00",
                    "comment_remark": "补充凭证",
                    "raw": {"fileName": "评论凭证.pdf", "fileId": "FILE-002"},
                }
            ],
        },
    )

    assert result["created_count"] == 2
    assert inserted_attachments[0]["batch"] == "BATCH-001"
    assert inserted_attachments[0]["version"] == "VER-001"
    assert inserted_attachments[0]["source_type"] == "OA"
    assert inserted_attachments[0]["attachment_type"] == "Packing List"
    assert inserted_attachments[0]["file_name"] == "2026.7.3DHL快递清单.xlsx"
    assert inserted_attachments[0]["parse_status"] == "Queued"
    assert "FILE-001" in inserted_attachments[0]["source_doc_no"]
    assert inserted_attachments[0]["oa_attachment_origin"] == "Form"
    assert inserted_attachments[1]["oa_attachment_origin"] == "Comment"
    comment_snapshot = json.loads(inserted_attachments[1]["parse_result_json"])
    assert comment_snapshot["comment_user_id"] == "COMMENTER-001"
    assert comment_snapshot["comment_attachments_included"] is True
    assert inserted_audits[0]["field_name"] == "oa_attachments"


def test_sync_linked_purchase_fields_applies_existing_import_service(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    calls = []

    def fake_apply_linked_purchase_expense_fillable_fields(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "updated_count": 2,
            "changed_field_count": 6,
            "skipped_count": 0,
            "unmatched_count": 0,
            "ambiguous_count": 0,
            "message": "采购字段已同步",
        }

    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", lambda **_kwargs: {"ok": True, "mapped_preview_items": []})
    monkeypatch.setattr(import_service, "apply_linked_purchase_expense_fillable_fields", fake_apply_linked_purchase_expense_fillable_fields)

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "linked_purchase_approvals": [
                {"approval_no": "202604300000000596348", "source_instance_id": "PROC-PURCHASE-001"}
            ]
        },
    )

    assert result["ok"] is True
    assert result["action"] == "synced"
    assert result["updated_count"] == 2
    assert result["changed_field_count"] == 6
    assert calls[0]["batch_name"] == "BATCH-001"
    assert calls[0]["version_name"] == "VER-001"
    assert "202604300000000596348" in calls[0]["linked_purchase_json"]


def test_sync_linked_purchase_fields_persists_rejected_purchase_status(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    saved_values = []

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Batch"
            assert name == "BATCH-001"
            return {
                "extra_json": json.dumps(
                    {
                        "oa_logistics_trace": {
                            "linked_purchase_approvals": [
                                {
                                    "approval_no": "PUR-REJECTED-001",
                                    "source_instance_id": "PROC-PUR-001",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                )
            }

        @staticmethod
        def set_value(doctype, name, fieldname, value, **_kwargs):
            saved_values.append((doctype, name, fieldname, value))

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_service,
        "preview_linked_purchase_expense_oa",
        lambda **_kwargs: {
            "ok": True,
            "mapped_preview_items": [],
            "purchase_summaries": [
                {
                    "source_approval_no": "PUR-REJECTED-001",
                    "source_instance_id": "PROC-PUR-001",
                    "approval_status": "REJECTED",
                    "message": "总经理拒绝，本次不采购。",
                }
            ],
        },
    )
    monkeypatch.setattr(
        import_service,
        "apply_linked_purchase_expense_fillable_fields",
        lambda **_kwargs: {"ok": True, "updated_count": 0},
    )

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "linked_purchase_approvals": [
                {"approval_no": "PUR-REJECTED-001", "source_instance_id": "PROC-PUR-001"}
            ]
        },
    )

    assert result["ok"] is True
    assert saved_values
    saved_extra = json.loads(saved_values[0][3])
    saved_link = saved_extra["oa_logistics_trace"]["linked_purchase_approvals"][0]
    assert saved_link["approval_status"] == "REJECTED"
    assert saved_link["message"] == "总经理拒绝，本次不采购。"


def test_sync_linked_purchase_fields_does_not_apply_when_all_purchases_are_excluded(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    apply_calls = []
    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_oa_logistics, "_persist_linked_purchase_approval_statuses", lambda **_kwargs: None)
    monkeypatch.setattr(
        import_service,
        "preview_linked_purchase_expense_oa",
        lambda **_kwargs: {
            "ok": True,
            "mapped_preview_items": [],
            "purchase_summaries": [
                {
                    "source_approval_no": "PUR-REFUSED-001",
                    "source_instance_id": "PROC-PUR-REFUSED",
                    "process_status": "COMPLETED",
                    "approval_result": "refuse",
                    "effective_status": "REJECTED",
                    "approval_status": "REJECTED",
                    "excluded": True,
                }
            ],
            "excluded_purchase_summary_count": 1,
            "excluded_purchase_summaries": [
                {
                    "source_approval_no": "PUR-REFUSED-001",
                    "source_instance_id": "PROC-PUR-REFUSED",
                    "effective_status": "REJECTED",
                    "excluded": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        import_service,
        "apply_linked_purchase_expense_fillable_fields",
        lambda **kwargs: apply_calls.append(kwargs) or {"ok": True},
    )

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "linked_purchase_approvals": [
                {"approval_no": "PUR-REFUSED-001", "source_instance_id": "PROC-PUR-REFUSED"}
            ]
        },
    )

    assert result["ok"] is True
    assert result["action"] == "skipped"
    assert result["excluded_purchase_count"] == 1
    assert apply_calls == []


def test_sync_linked_purchase_fields_propagates_manual_review_audit_failure(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_oa_logistics, "_persist_linked_purchase_approval_statuses", lambda **_kwargs: None)
    monkeypatch.setattr(
        import_service,
        "preview_linked_purchase_expense_oa",
        lambda **_kwargs: {
            "ok": True,
            "purchase_summaries": [{"source_instance_id": "PROC-REFUSED", "excluded": True}],
            "excluded_purchase_summary_count": 1,
            "excluded_purchase_summaries": [{"source_instance_id": "PROC-REFUSED", "excluded": True}],
            "mapped_preview_items": [],
        },
    )
    monkeypatch.setattr(
        import_oa_logistics,
        "_restore_main_logistics_items_after_excluded_purchases",
        lambda **_kwargs: {
            "action": "manual_required",
            "ok": False,
            "audit_logged": False,
            "audit_error": "audit unavailable",
            "reason": "审计写入失败",
        },
    )

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"linked_purchase_approvals": [{"source_instance_id": "PROC-REFUSED"}]},
    )

    assert result["ok"] is False
    assert result["repair_result"]["audit_logged"] is False


def test_invalid_purchase_item_repair_requires_confirmation_before_restoring_rows(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    deleted_filters = []
    inserted_items = []
    inserted_audits = []
    batch_updates = []

    class FakeMeta:
        @staticmethod
        def has_field(_fieldname):
            return True

    class FakeDoc:
        def __init__(self, payload):
            self.payload = dict(payload)
            self.name = f"DOC-{len(inserted_items) + len(inserted_audits) + 1}"

        def insert(self, **_kwargs):
            if self.payload.get("doctype") == "Overseas Cost Item":
                inserted_items.append(self.payload)
            else:
                inserted_audits.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, field):
            return "EXPRESS"

        @staticmethod
        def savepoint(name):
            assert name == "before_invalid_purchase_item_repair"

        @staticmethod
        def rollback(*, save_point):
            raise AssertionError(f"successful repair must not roll back {save_point}")

        @staticmethod
        def delete(doctype, filters):
            deleted_filters.append((doctype, filters))

        @staticmethod
        def set_value(doctype, name, values, **_kwargs):
            batch_updates.append((doctype, name, values))

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return [
                    {
                        "name": "PURCHASE-1",
                        "manual_override_flag": 0,
                        "source_type": "PURCHASE_EXPENSE_OA",
                        "dingtalk_instance_id": "PROC-PUR-REFUSED",
                    }
                ]
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

        @staticmethod
        def get_meta(_doctype):
            return FakeMeta()

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_oa_logistics,
        "build_oa_item_values_from_approval",
        lambda _approval: [
            {
                "row_no": 1,
                "material_code": "MAT-MAIN",
                "product_name": "主物流审批物料",
                "source_type": "oa_logistics",
                "dingtalk_instance_id": "PROC-LOG-001",
            }
        ],
    )

    repaired = _restore_main_logistics_items_after_excluded_purchases(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"source_instance_id": "PROC-LOG-001"},
        excluded_purchase_summaries=[{
            "source_instance_id": "PROC-PUR-REFUSED",
            "source_approval_no": "PUR-REFUSED",
            "process_status": "COMPLETED",
            "approval_result": "REFUSE",
            "effective_status": "REJECTED",
        }],
    )

    assert repaired["action"] == "manual_required"
    assert repaired["deleted_count"] == 0
    assert repaired["created_count"] == 0
    assert deleted_filters == []
    assert inserted_items == []
    assert inserted_audits[0]["field_name"] == "invalid_purchase_item_repair_review"
    audit_new_value = json.loads(inserted_audits[0]["new_value"])
    assert audit_new_value["excluded_purchase_decisions"] == [{
        "source_approval_no": "PUR-REFUSED",
        "source_instance_id": "PROC-PUR-REFUSED",
        "process_status": "COMPLETED",
        "approval_result": "REFUSE",
        "effective_status": "REJECTED",
        "message": "",
    }]
    assert "dingtalk_instance_id" in audit_new_value["affected_fields"]
    assert batch_updates == []
    assert "确认" in repaired["reason"]


def test_invalid_purchase_item_repair_protects_mixed_or_manual_rows(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    class FakeDB:
        @staticmethod
        def delete(*_args, **_kwargs):
            raise AssertionError("protected rows must not be deleted")

        @staticmethod
        def set_value(*_args, **_kwargs):
            raise AssertionError("protected rows must not be changed")

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Item"
            return [
                {
                    "name": "PURCHASE-1",
                    "manual_override_flag": 0,
                    "source_type": "PURCHASE_EXPENSE_OA",
                    "dingtalk_instance_id": "PROC-PUR-REFUSED",
                },
                {
                    "name": "MANUAL-1",
                    "manual_override_flag": 1,
                    "source_type": "manual",
                    "dingtalk_instance_id": "",
                },
            ]

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)

    protected = _restore_main_logistics_items_after_excluded_purchases(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"source_instance_id": "PROC-LOG-001"},
        excluded_purchase_summaries=[{"source_instance_id": "PROC-PUR-REFUSED"}],
    )

    assert protected["action"] == "manual_required"
    assert protected["deleted_count"] == 0
    assert protected["protected_count"] == 1


def test_invalid_purchase_item_repair_never_enters_destructive_transaction(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    events = []
    audit_payloads = []

    class FakeMeta:
        @staticmethod
        def has_field(_fieldname):
            return True

    class FailingDoc:
        def insert(self, **_kwargs):
            events.append("insert_failed")
            raise RuntimeError("insert failed")

    class AuditDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, field):
            return "AIR"

        @staticmethod
        def savepoint(name):
            events.append(("savepoint", name))

        @staticmethod
        def rollback(*, save_point):
            events.append(("rollback", save_point))

        @staticmethod
        def delete(_doctype, _filters):
            events.append("delete")

        @staticmethod
        def set_value(*_args, **_kwargs):
            events.append("batch_update")

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(_doctype, **_kwargs):
            return [{
                "name": "PURCHASE-1",
                "manual_override_flag": 0,
                "source_type": "PURCHASE_EXPENSE_OA",
                "dingtalk_instance_id": "PROC-PUR-REFUSED",
            }]

        @staticmethod
        def get_doc(payload):
            if payload.get("doctype") == "Overseas Cost Audit Log":
                return AuditDoc(payload)
            return FailingDoc()

        @staticmethod
        def get_meta(_doctype):
            return FakeMeta()

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "build_oa_item_values_from_approval", lambda _approval: [{
        "row_no": 1,
        "material_code": "MAT-MAIN",
        "source_type": "oa_logistics",
    }])

    result = _restore_main_logistics_items_after_excluded_purchases(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"source_instance_id": "PROC-LOG-001"},
        excluded_purchase_summaries=[{"source_instance_id": "PROC-PUR-REFUSED"}],
    )

    assert result["action"] == "manual_required"
    assert result["deleted_count"] == 0
    assert events == []
    assert audit_payloads[0]["field_name"] == "invalid_purchase_item_repair_review"
    audit_result = json.loads(audit_payloads[0]["new_value"])
    assert audit_result["action"] == "manual_required"
    assert "确认" in audit_result["reason"]


def test_invalid_purchase_item_manual_review_reports_audit_failure(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    class FakeFrappe:
        @staticmethod
        def get_all(_doctype, **_kwargs):
            return [{
                "name": "MANUAL-1",
                "manual_override_flag": 1,
                "source_type": "manual",
                "dingtalk_instance_id": "",
            }]

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_oa_logistics,
        "_insert_batch_audit_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    result = _restore_main_logistics_items_after_excluded_purchases(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"source_instance_id": "PROC-LOG-001"},
        excluded_purchase_summaries=[{"source_instance_id": "PROC-PUR-REFUSED"}],
    )

    assert result["action"] == "manual_required"
    assert result["ok"] is False
    assert result["audit_logged"] is False
    assert "audit unavailable" in result["audit_error"]


def test_purchase_expense_rows_from_preview_excludes_rejected_approvals() -> None:
    rows = _purchase_expense_rows_from_preview(
        {
            "purchase_summaries": [
                {
                    "source_approval_no": "PUR-REJECTED-001",
                    "source_instance_id": "PROC-PUR-001",
                    "approval_status": "REJECTED",
                }
            ],
            "mapped_preview_items": [
                {
                    "material_code": "MAT-INVALID",
                    "source_approval_no": "PUR-REJECTED-001",
                    "source_instance_id": "PROC-PUR-001",
                },
                {
                    "material_code": "MAT-VALID",
                    "source_approval_no": "PUR-OK-001",
                    "source_instance_id": "PROC-PUR-OK",
                },
            ],
        }
    )

    assert [row["material_code"] for row in rows] == ["MAT-VALID"]


def test_sync_linked_purchase_fields_rebuilds_items_from_purchase_expense_rows(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    inserted_items = []
    deleted_filters = []
    batch_updates = []
    inserted_audits = []

    class FakeMeta:
        @staticmethod
        def has_field(_fieldname):
            return True

    class FakeDoc:
        def __init__(self, payload):
            self.payload = dict(payload)
            self.name = f"DOC-{len(inserted_items) + len(inserted_audits) + 1}"

        def insert(self, **_kwargs):
            if self.payload.get("doctype") == "Overseas Cost Item":
                self.name = f"ITEM-{len(inserted_items) + 1}"
                self.payload["name"] = self.name
                inserted_items.append(self.payload)
            else:
                inserted_audits.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, field):
            assert (doctype, name, field) == ("Overseas Cost Batch", "BATCH-001", "transport_mode")
            return "AIR"

        @staticmethod
        def delete(doctype, filters):
            deleted_filters.append((doctype, filters))

        @staticmethod
        def set_value(doctype, name, values, **_kwargs):
            batch_updates.append((doctype, name, values))

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return [
                    {"name": "OLD-1", "manual_override_flag": 0, "source_type": "OA_LOGISTICS"},
                    {"name": "OLD-2", "manual_override_flag": 0, "source_type": "OA_LOGISTICS"},
                ]
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

        @staticmethod
        def get_meta(_doctype):
            return FakeMeta()

    def fake_preview_linked_purchase_expense_oa(**_kwargs):
        return {
            "ok": True,
            "mapped_preview_items": [
                {
                    "material_code": "MBA101291",
                    "product_name": "壳甲TPU模具",
                    "spec_model": "HR Redmi A7 4G",
                    "quantity": 1,
                    "unit": "套",
                    "unit_price": 7250,
                    "goods_value": 7250,
                    "purchase_currency": "RMB",
                    "source_approval_no": "PUR-001",
                    "source_instance_id": "PROC-PUR-001",
                },
                {
                    "material_code": "MBA201291",
                    "product_name": "壳甲PC模具",
                    "spec_model": "HR Redmi A7 4G",
                    "quantity": 1,
                    "unit": "套",
                    "unit_price": 7250,
                    "goods_value": 7250,
                    "purchase_currency": "RMB",
                    "source_approval_no": "PUR-001",
                    "source_instance_id": "PROC-PUR-001",
                },
            ],
        }

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_service, "preview_linked_purchase_expense_oa", fake_preview_linked_purchase_expense_oa)

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "source_approval_no": "LOG-001",
            "source_instance_id": "PROC-LOG-001",
            "linked_purchase_approvals": [{"approval_no": "PUR-001", "source_instance_id": "PROC-PUR-001"}],
        },
    )

    assert result["ok"] is True
    assert result["sync_strategy"] == "purchase_expense_items"
    assert result["created_count"] == 2
    assert deleted_filters == [("Overseas Cost Item", {"batch": "BATCH-001", "version": "VER-001"})]
    assert [item["material_code"] for item in inserted_items] == ["MBA101291", "MBA201291"]
    assert inserted_items[0]["actual_shipped_qty"] == 1
    assert inserted_items[0]["source_type"] == "PURCHASE_EXPENSE_OA"
    assert batch_updates[0][2]["item_count"] == 2


def test_sync_linked_purchase_fields_does_not_rebuild_manual_items(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    class FakeDB:
        @staticmethod
        def delete(*_args, **_kwargs):
            raise AssertionError("manual items must not be deleted")

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return [{"name": "MANUAL-1", "manual_override_flag": 1, "source_type": "manual"}]
            return []

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(
        import_service,
        "preview_linked_purchase_expense_oa",
        lambda **_kwargs: {"ok": True, "mapped_preview_items": [{"material_code": "MBA101291", "quantity": 1}]},
    )

    result = _sync_linked_purchase_fields(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={"linked_purchase_approvals": [{"approval_no": "PUR-001", "source_instance_id": "PROC-PUR-001"}]},
    )

    assert result["ok"] is True
    assert result["sync_strategy"] == "purchase_expense_items"
    assert result["rebuild_result"]["action"] == "skipped"
    assert result["rebuild_result"]["manual_count"] == 1


def test_sync_oa_logistics_allocation_rule_creates_rule_and_recalculates(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    inserted_rules = []
    recalculate_calls = []

    class FakeDoc:
        def __init__(self, payload):
            self.payload = dict(payload)
            self.name = payload.get("name") or f"RULE-{len(inserted_rules) + 1}"

        def insert(self, **_kwargs):
            self.name = f"RULE-{len(inserted_rules) + 1}"
            self.payload["name"] = self.name
            inserted_rules.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Allocation Rule":
                return None
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    def fake_recalculate_batch(**kwargs):
        recalculate_calls.append(kwargs)
        return {"ok": True, "summary_snapshot": {"total_cost_rmb": 1234}}

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "_oa_fee_sync_context", lambda *args, **kwargs: {"transport_mode": "SEA"})
    monkeypatch.setattr(FakeFrappe.db, "sql", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(FakeFrappe.db, "set_value", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr("overseas_costing.services.calculate_service.recalculate_batch", fake_recalculate_batch)

    rule_result = _sync_oa_logistics_allocation_rule(
        batch_name="BATCH-001",
        version_name="VER-001",
        approval_item={
            "logistics_fee": {
                "amount": "900",
                "currency": "美元Dólar",
                "source_label": "物流费用",
                "source_field": "物流费用",
                "source_value": "900",
            }
        },
    )
    recalc_result = _recalculate_after_purchase_sync(
        batch_name="BATCH-001",
        version_name="VER-001",
        purchase_sync={"ok": True, "updated_count": 0},
        logistics_fee_sync=rule_result,
    )

    assert rule_result["action"] == "created"
    assert rule_result["rule"]["rule_code"] == "oa_logistics_freight"
    assert rule_result["rule"]["amount"] == 900
    assert inserted_rules[0]["currency"] == "USD"
    assert recalc_result["action"] == "recalculated"
    assert recalculate_calls == [{"batch_name": "BATCH-001", "version_name": "VER-001"}]


def test_sync_express_single_quote_creates_freight_rule(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    inserted_rules = []

    class FakeDoc:
        def __init__(self, payload):
            self.payload = dict(payload)
            self.name = payload.get("name") or f"RULE-{len(inserted_rules) + 1}"

        def insert(self, **_kwargs):
            self.name = f"RULE-{len(inserted_rules) + 1}"
            self.payload["name"] = self.name
            inserted_rules.append(self.payload)
            return self

    class FakeDB:
        @staticmethod
        def get_value(doctype, name_or_filters, fields=None, as_dict=False, **_kwargs):
            if doctype == "Overseas Cost Allocation Rule":
                return None
            return None

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "_oa_fee_sync_context", lambda *args, **kwargs: {"transport_mode": "EXPRESS"})
    monkeypatch.setattr(FakeFrappe.db, "sql", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(FakeFrappe.db, "set_value", lambda *args, **kwargs: None, raising=False)

    rule_result = _sync_oa_logistics_allocation_rule(
        batch_name="202608131523000315085",
        version_name="VER-EXPRESS",
        approval_item={
            "transport_mode": "EXPRESS",
            "transport_mode_raw": "Express快递",
            "logistics_quote_candidates": extract_logistics_quote_candidates_from_approval(
                {
                    "form_fields": {
                        "物流报价Cotización de logística": (
                            "DHL报价：\n"
                            "运费=4075*（1+燃油附加费）+重量+155*（1+燃油附加费）+超过25kg的箱数+附加费\n"
                            "50.22*1.3975*43.2KG+155*1.3975*1+155*1=3403.49434元\n"
                            "每kg单价：78.7845912元"
                        )
                    }
                }
            ),
        },
    )

    assert rule_result["action"] == "created"
    assert rule_result["rule"]["amount"] == 3403.49434
    assert rule_result["rule"]["currency"] == "RMB"
    assert rule_result["rule"]["rule_code"] == "oa_logistics_freight"
    assert inserted_rules[0]["expense_category"] == "国际快递费"


def test_sync_oa_logistics_allocation_rule_blocks_invalid_batch_before_db_write(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics
    from overseas_costing.services import import_service

    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(
        import_service,
        "_invalid_batch_cost_write_response",
        lambda _batch_name, _version_name="": {
            "ok": False,
            "invalid_business": True,
            "message": "审批已撤销，禁止写入费用。",
        },
    )

    result = import_oa_logistics._sync_oa_logistics_allocation_rule(
        batch_name="BATCH-REVOKED",
        version_name="VER-001",
        approval_item={"logistics_fee": {"amount": "900", "currency": "RMB"}},
    )

    assert result["ok"] is False
    assert result["action"] == "blocked"
    assert result["created_count"] == 0
    assert result["updated_count"] == 0


def test_refresh_existing_oa_logistics_details_repulls_missing_trace(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    detail_calls = []
    save_calls = []
    invalid_updates = []

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype != "Overseas Cost Batch":
                return []
            return [
                {
                    "name": "BATCH-OLD",
                    "batch_no": "HPCU5155607",
                    "waybill_no": "HPCU5155607",
                    "current_version": "VER-OLD",
                    "source_approval_no": "202607010001",
                    "source_instance_id": "PROC-SEA-OLD",
                    "source_dingtalk_url": "",
                    "extra_json": "{}",
                },
                {
                    "name": "BATCH-MISSING-ID",
                    "batch_no": "NO-ID",
                    "waybill_no": "",
                    "current_version": "VER-MISSING",
                    "source_approval_no": "",
                    "source_instance_id": "",
                    "source_dingtalk_url": "",
                    "extra_json": "{}",
                },
                {
                    "name": "BATCH-REVOKED",
                    "batch_no": "REVOKED",
                    "waybill_no": "REVOKED",
                    "current_version": "VER-REVOKED",
                    "source_approval_no": "202607010002",
                    "source_instance_id": "PROC-REVOKED",
                    "source_dingtalk_url": "",
                    "extra_json": "{}",
                },
            ]

    def fake_get_process_instance_detail(**kwargs):
        detail_calls.append(kwargs)
        return {"processInstanceId": kwargs["process_instance_id"]}

    def fake_summarize_approval(detail, **_kwargs):
        instance_id = detail["processInstanceId"]
        if instance_id == "PROC-REVOKED":
            return {
                "source_instance_id": instance_id,
                "source_approval_no": "202607010002",
                "approval_status": "TERMINATED",
                "form_fields": {"物流方式": "海运"},
                "transport_mode_raw": "海运",
                "logistics_no": "REVOKED",
            }
        return {
            "source_instance_id": instance_id,
            "source_approval_no": "202607010001",
            "approval_status": "COMPLETED",
            "form_fields": {"物流方式": "海运"},
            "transport_mode_raw": "海运",
            "logistics_no": "HPCU5155607",
            "linked_purchase_approvals": [
                {"approval_no": "202604300000000596348", "source_instance_id": "PROC-PURCHASE-001"}
            ],
            "oa_form_attachments": [],
        }

    def fake_save_sea_approvals_to_erp(result):
        save_calls.append(result)
        return {
            "ok": True,
            "created_count": 0,
            "updated_count": 1,
            "unchanged_count": 0,
            "skipped_count": 0,
            "items": [
                {
                    "batch_name": "BATCH-OLD",
                    "purchase_sync": {
                        "ok": True,
                        "linked_purchase_count": 1,
                        "updated_count": 2,
                        "changed_field_count": 6,
                    },
                    "attachment_sync": {"attachment_count": 0},
                }
            ],
            "skipped_items": [],
        }

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")
    monkeypatch.setattr(import_oa_logistics, "get_process_instance_detail", fake_get_process_instance_detail)
    monkeypatch.setattr(import_oa_logistics, "summarize_approval", fake_summarize_approval)
    monkeypatch.setattr(import_oa_logistics, "save_sea_approvals_to_erp", fake_save_sea_approvals_to_erp)
    monkeypatch.setattr(
        import_oa_logistics,
        "_update_oa_trace_batch",
        lambda batch_name, values: invalid_updates.append((batch_name, values)) or {"action": "updated"},
    )
    monkeypatch.setattr(import_oa_logistics, "_commit_oa_pull_progress", lambda: None)

    result = refresh_existing_oa_logistics_details(limit=50)

    assert result["ok"] is True
    assert result["scanned_count"] == 3
    assert result["detail_count"] == 1
    assert result["saved_count"] == 1
    assert result["purchase_updated_count"] == 2
    assert result["purchase_changed_field_count"] == 6
    assert {call["process_instance_id"] for call in detail_calls} == {"PROC-SEA-OLD", "PROC-REVOKED"}
    assert len(save_calls) == 1
    assert save_calls[0]["items"][0]["source_instance_id"] == "PROC-SEA-OLD"
    assert save_calls[0]["items"][0]["linked_purchase_approvals"][0]["source_instance_id"] == "PROC-PURCHASE-001"
    assert result["skipped_count"] == 2
    assert {item["batch_name"] for item in result["skipped_items"]} == {"BATCH-MISSING-ID", "BATCH-REVOKED"}
    assert invalid_updates[0][0] == "BATCH-REVOKED"
    assert invalid_updates[0][1]["source_approval_status"] == "TERMINATED"


def test_refresh_existing_oa_logistics_details_can_target_one_batch(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    query_calls = []
    detail_calls = []

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **kwargs):
            query_calls.append((doctype, kwargs))
            return [
                {
                    "name": "BATCH-TARGET",
                    "batch_no": "2k3o4tgh3e",
                    "waybill_no": "2k3o4tgh3e",
                    "current_version": "VER-TARGET",
                    "source_approval_no": "202608051608000144099",
                    "source_instance_id": "PROC-TARGET",
                    "source_dingtalk_url": "",
                    "extra_json": "{}",
                }
            ]

    def fake_get_process_instance_detail(**kwargs):
        detail_calls.append(kwargs)
        return {"processInstanceId": kwargs["process_instance_id"]}

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")
    monkeypatch.setattr(import_oa_logistics, "get_process_instance_detail", fake_get_process_instance_detail)
    monkeypatch.setattr(
        import_oa_logistics,
        "summarize_approval",
        lambda detail, **_kwargs: {
            "source_instance_id": detail["processInstanceId"],
            "source_approval_no": "202608051608000144099",
            "approval_status": "COMPLETED",
            "form_fields": {"物流方式": "海运"},
            "transport_mode_raw": "海运",
            "logistics_no": "2k3o4tgh3e",
        },
    )
    monkeypatch.setattr(
        import_oa_logistics,
        "save_sea_approvals_to_erp",
        lambda result: {
            "ok": True,
            "created_count": 0,
            "updated_count": 1,
            "unchanged_count": 0,
            "skipped_count": 0,
            "items": [],
            "skipped_items": [],
        },
    )

    result = import_oa_logistics.refresh_existing_oa_logistics_details(
        batch_no="2k3o4tgh3e",
        source_approval_no="202608051608000144099",
    )

    assert result["ok"] is True
    assert result["target"] == {
        "target": "",
        "batch_name": "",
        "batch_no": "2k3o4tgh3e",
        "source_approval_no": "202608051608000144099",
    }
    assert query_calls[0][0] == "Overseas Cost Batch"
    assert query_calls[0][1]["filters"]["source_type"] == "oa_logistics"
    assert query_calls[0][1]["or_filters"] == [
        {"batch_no": "2k3o4tgh3e"},
        {"source_approval_no": "202608051608000144099"},
    ]
    assert [call["process_instance_id"] for call in detail_calls] == ["PROC-TARGET"]


def test_refresh_oa_logistics_detail_matches_target_across_identifiers(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    query_calls = []

    class FakeFrappe:
        @staticmethod
        def get_all(doctype, **kwargs):
            query_calls.append((doctype, kwargs))
            return []

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")

    result = refresh_oa_logistics_detail("202608051608000144099", limit=10)

    assert result["ok"] is True
    assert result["target"]["target"] == "202608051608000144099"
    assert query_calls[0][1]["filters"] == {"source_type": "oa_logistics"}
    assert query_calls[0][1]["or_filters"] == [
        {"name": "202608051608000144099"},
        {"batch_no": "202608051608000144099"},
        {"source_approval_no": "202608051608000144099"},
    ]


def test_sync_existing_oa_finished_times_backfills_only_empty(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    rows = [
        {
            "name": "BATCH-MISSING-FINISH",
            "batch_no": "HPCU5155607",
            "source_approval_no": "202601291020000337788",
            "source_instance_id": "PROC-SEA-TRACE",
            "source_finished_at": "",
            "extra_json": json.dumps(
                {
                    "source": "dingtalk_oa_logistics",
                    "finish_time": "2026-04-21T17:16Z",
                    "source_instance_id": "PROC-SEA-TRACE",
                },
                ensure_ascii=False,
            ),
        },
        {
            "name": "BATCH-HAS-FINISH",
            "batch_no": "FSCU8486789",
            "source_approval_no": "202607010001",
            "source_instance_id": "PROC-HAS-FINISH",
            "source_finished_at": "2026-07-01 09:30:00",
            "extra_json": json.dumps(
                {
                    "source": "dingtalk_oa_logistics",
                    "finish_time": "2026-07-01T09:30Z",
                    "source_instance_id": "PROC-HAS-FINISH",
                },
                ensure_ascii=False,
            ),
        },
        {
            "name": "BATCH-NO-SNAPSHOT-FINISH",
            "batch_no": "NO-FINISH",
            "source_approval_no": "202607010002",
            "source_instance_id": "PROC-NO-FINISH",
            "source_finished_at": "",
            "extra_json": json.dumps({"source": "dingtalk_oa_logistics"}, ensure_ascii=False),
        },
    ]
    set_values = []
    audit_payloads = []
    commit_count = {"value": 0}

    class FakeDB:
        @staticmethod
        def set_value(doctype, name, fieldname, value=None, **kwargs):
            set_values.append(
                {
                    "doctype": doctype,
                    "name": name,
                    "fieldname": fieldname,
                    "value": value,
                    "update_modified": kwargs.get("update_modified"),
                }
            )

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Batch":
                return rows
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)

    result = sync_existing_oa_finished_times(limit=50)

    assert result["ok"] is True
    assert result["scanned_count"] == 3
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 2
    assert set_values == [
        {
            "doctype": "Overseas Cost Batch",
            "name": "BATCH-MISSING-FINISH",
            "fieldname": "source_finished_at",
            "value": "2026-04-22 01:16:00",
            "update_modified": False,
        }
    ]
    assert audit_payloads[0]["field_name"] == "source_finished_at"
    assert "不作为付款日汇率依据" in audit_payloads[0]["action_remark"]
    assert commit_count["value"] == 1
    assert result["items"][0]["source_finished_at"] == "2026-04-22 01:16:00"
    assert {item["batch_name"] for item in result["skipped_items"]} == {"BATCH-HAS-FINISH", "BATCH-NO-SNAPSHOT-FINISH"}


def test_refresh_missing_oa_finished_times_only_updates_finish_fields(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    rows = [
        {
            "name": "BATCH-MISSING-FINISH",
            "batch_no": "HPCU5155607",
            "source_approval_no": "202601291020000337788",
            "source_instance_id": "PROC-SEA-TRACE",
            "source_dingtalk_url": "",
            "source_approval_status": "",
            "source_finished_at": "",
            "extra_json": json.dumps({"source": "dingtalk_oa_logistics"}, ensure_ascii=False),
        },
        {
            "name": "BATCH-NO-FINISH",
            "batch_no": "NO-FINISH",
            "source_approval_no": "202607010001",
            "source_instance_id": "PROC-NO-FINISH",
            "source_dingtalk_url": "",
            "source_approval_status": "",
            "source_finished_at": "",
            "extra_json": json.dumps({"source": "dingtalk_oa_logistics"}, ensure_ascii=False),
        },
    ]
    detail_calls = []
    set_values = []
    audit_payloads = []
    commit_count = {"value": 0}

    class FakeDB:
        @staticmethod
        def set_value(doctype, name, fieldname, value=None, **kwargs):
            set_values.append(
                {
                    "doctype": doctype,
                    "name": name,
                    "fieldname": fieldname,
                    "value": value,
                    "update_modified": kwargs.get("update_modified"),
                }
            )

        @staticmethod
        def commit():
            commit_count["value"] += 1

    class FakeDoc:
        def __init__(self, payload):
            self.payload = payload

        def insert(self, **_kwargs):
            audit_payloads.append(self.payload)
            return self

    class FakeFrappe:
        db = FakeDB()

        class session:
            user = "tester@example.com"

        @staticmethod
        def get_all(doctype, **kwargs):
            assert kwargs["filters"] == {"source_type": "oa_logistics", "source_finished_at": ["in", ["", None]]}
            if doctype == "Overseas Cost Batch":
                return rows
            return []

        @staticmethod
        def get_doc(payload):
            return FakeDoc(payload)

    def fake_get_process_instance_detail(**kwargs):
        detail_calls.append(kwargs)
        return {"processInstanceId": kwargs["process_instance_id"]}

    def fake_summarize_approval(detail, **_kwargs):
        if detail["processInstanceId"] == "PROC-NO-FINISH":
            return {
                "source_instance_id": "PROC-NO-FINISH",
                "source_approval_no": "202607010001",
                "source_dingtalk_url": "",
                "approval_status": "RUNNING",
                "finish_time": "",
            }
        return {
            "source_instance_id": "PROC-SEA-TRACE",
            "source_approval_no": "202601291020000337788",
            "source_dingtalk_url": "https://aflow.dingtalk.com/example",
            "approval_status": "COMPLETED",
            "finish_time": "2026-04-21T17:16Z",
        }

    monkeypatch.setattr(import_oa_logistics, "frappe", FakeFrappe)
    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "get_access_token", lambda **_kwargs: "TOKEN")
    monkeypatch.setattr(import_oa_logistics, "get_process_instance_detail", fake_get_process_instance_detail)
    monkeypatch.setattr(import_oa_logistics, "summarize_approval", fake_summarize_approval)

    result = refresh_missing_oa_finished_times(limit=50)

    assert result["ok"] is True
    assert result["scanned_count"] == 2
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 1
    assert {call["process_instance_id"] for call in detail_calls} == {"PROC-SEA-TRACE", "PROC-NO-FINISH"}
    assert set_values[0]["doctype"] == "Overseas Cost Batch"
    assert set_values[0]["name"] == "BATCH-MISSING-FINISH"
    assert set_values[0]["fieldname"]["source_finished_at"] == "2026-04-22 01:16:00"
    assert set_values[0]["fieldname"]["source_approval_status"] == "COMPLETED"
    assert "extra_json" in set_values[0]["fieldname"]
    assert set_values[0]["update_modified"] is False
    assert audit_payloads[0]["field_name"] == "source_finished_at"
    assert "不作为付款日汇率依据" in audit_payloads[0]["action_remark"]
    assert commit_count["value"] == 1


def test_revoked_approval_is_skipped_when_saving_oa_trace() -> None:
    assert is_hidden_approval_status("TERMINATED") is True

    result = save_sea_approvals_to_erp(
        {
            "items": [
                {
                    "source_approval_no": "202601121441000259291",
                    "source_instance_id": "PROC-REVOKED",
                    "approval_status": "TERMINATED",
                    "transport_mode_raw": "doble despacho en aduana para transporte marítimo海运双清",
                    "logistics_no": "",
                    "form_fields": {},
                }
            ]
        }
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["valid_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped_items"][0]["reason"] == "审批单已撤销或终止，不进入成本表格"


def test_revoked_existing_approval_updates_stored_main_status(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    updates = []
    monkeypatch.setattr(import_oa_logistics, "frappe", object())
    monkeypatch.setattr(import_oa_logistics, "_resolve_existing_batch_name", lambda _values: "BATCH-REVOKED")
    monkeypatch.setattr(
        import_oa_logistics,
        "_update_oa_trace_batch",
        lambda batch_name, values: updates.append((batch_name, values)) or {"action": "updated"},
    )
    monkeypatch.setattr(import_oa_logistics, "_commit_oa_pull_progress", lambda: None)

    result = save_sea_approvals_to_erp({"items": [{
        "source_approval_no": "OA-REVOKED",
        "source_instance_id": "PROC-REVOKED",
        "approval_status": "TERMINATED",
        "transport_mode": "SEA",
        "form_fields": {},
    }]})

    assert result["skipped_count"] == 1
    assert updates[0][0] == "BATCH-REVOKED"
    assert updates[0][1]["source_approval_status"] == "TERMINATED"


def test_pull_latest_logistics_approvals_to_erp_reuses_pull_and_save(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    calls = {}

    def fake_pull_logistics_approvals(**kwargs):
        calls["pull"] = kwargs
        return {
            "ok": True,
            "data_source": "postgres",
            "source_updated_at": "2026-08-17T03:00:00+00:00",
            "source_lag_seconds": 9,
            "fallback_used": False,
            "transport_modes": ["SEA", "AIR"],
            "total_instance_count": 3,
            "detail_count": 3,
            "filtered_count": 2,
            "transport_counts": {"SEA": 1, "AIR": 1, "EXPRESS": 0},
            "items": [{"source_approval_no": "OA-1"}, {"source_approval_no": "OA-2"}],
        }

    def fake_save_sea_approvals_to_erp(result):
        calls["save"] = result
        return {
            "ok": True,
            "created_count": 1,
            "updated_count": 1,
            "unchanged_count": 0,
            "skipped_count": 0,
            "items": [{"batch_no": "NEW-SEA"}, {"batch_no": "NEW-AIR"}],
            "skipped_items": [],
            "message": "saved",
        }

    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "_has_dingtalk_pull_credentials", lambda: True)
    monkeypatch.setattr(import_oa_logistics, "pull_logistics_approvals", fake_pull_logistics_approvals)
    monkeypatch.setattr(import_oa_logistics, "save_sea_approvals_to_erp", fake_save_sea_approvals_to_erp)

    result = pull_latest_logistics_approvals_to_erp(
        start="2026-08-01",
        end="2026-08-17",
        transport_modes="SEA,AIR",
        limit=50,
    )

    assert result["ok"] is True
    assert result["start"] == "2026-08-01"
    assert result["end"] == "2026-08-17"
    assert result["pull"]["filtered_count"] == 2
    assert result["save"]["created_count"] == 1
    assert result["save"]["updated_count"] == 1
    assert result["data_source"] == "postgres"
    assert result["source_updated_at"] == "2026-08-17T03:00:00+00:00"
    assert result["source_lag_seconds"] == 9
    assert result["fallback_used"] is False
    assert calls["pull"]["transport_modes"] == "SEA,AIR"
    assert calls["pull"]["limit"] == 50
    assert calls["save"]["items"][0]["source_approval_no"] == "OA-1"


def test_pull_latest_dry_run_previews_new_mapping_without_database_save(monkeypatch) -> None:
    from overseas_costing.scripts import import_oa_logistics

    monkeypatch.setattr(import_oa_logistics, "resolve_dingtalk_env_file", lambda env_file=None: "")
    monkeypatch.setattr(import_oa_logistics, "_has_dingtalk_pull_credentials", lambda: True)
    monkeypatch.setattr(
        import_oa_logistics,
        "pull_logistics_approvals",
        lambda **kwargs: {
            "ok": True,
            "data_source": "postgres",
            "filtered_count": 1,
            "items": [{
                "source_approval_no": "OA-NEW",
                "transport_mode": "EXPRESS",
                "form_fields": {
                    "项目归属Pertenencia del proyecto": [{"deptName": "AmigoMart", "deptId": "D1"}],
                    "快递附加费": "25",
                },
            }],
        },
    )
    monkeypatch.setattr(
        import_oa_logistics,
        "save_sea_approvals_to_erp",
        lambda result: (_ for _ in ()).throw(AssertionError("dry run must not save")),
    )

    result = pull_latest_logistics_approvals_to_erp(
        start="2026-09-22",
        end="2026-09-22",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["previews"][0]["project_ownership"]["candidates"] == [{"name": "AmigoMart", "id": "D1"}]
    assert result["previews"][0]["logistics_fees"][0]["logical_fee_key"] == "express_surcharge"
