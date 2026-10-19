"""
中文用途：批次查询与回写检查服务。

当前先返回稳定的数据结构，占住后续前端对接形态。
后面接数据库时，优先从这里补查询实现。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import base64
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json

from overseas_costing.services import source_priority_service

try:
    import frappe
except Exception:  # pragma: no cover - 本地无 Frappe 环境时保持可导入
    frappe = None

from overseas_costing.utils.dingtalk import build_dingtalk_order_payload, extract_dingtalk_instance_id


DEFAULT_BATCH_RECENT_DAYS = 30
CLASSIC_HISTORY_BATCH_KEYS = (
    "HPCU5155607",
    "FSCU8486789",
    "MXT959831",
)
TAX_COMPONENT_FIELDS = ("igi_amount", "iva_amount", "dta", "prv_duty", "prv_iva")


def _get_batch_source_meta(batch_name: str) -> dict:
    """读取批次上的钉钉原单跳转元信息。"""

    meta = {
        "name": batch_name,
        "batch_no": "",
        "source_approval_no": "",
        "source_instance_id": "",
        "source_dingtalk_url": "",
        "source_title": "",
        "source_creator_name": "",
        "source_approval_status": "",
    }
    if frappe is None:
        return meta

    fields = list(meta.keys())
    try:
        rows = frappe.get_all(
            "Overseas Cost Batch",
            filters={"name": batch_name},
            fields=fields,
            limit_page_length=1,
        )
        if not rows:
            rows = frappe.get_all(
                "Overseas Cost Batch",
                filters={"batch_no": batch_name},
                fields=fields,
                limit_page_length=1,
            )
    except Exception:
        return meta

    if rows:
        meta.update(rows[0])
        if not meta.get("source_instance_id") and not meta.get("source_dingtalk_url"):
            try:
                item_rows = frappe.get_all(
                    "Overseas Cost Item",
                    filters={"batch": meta.get("name"), "is_excluded": 0},
                    fields=["source_doc_no", "dingtalk_instance_id", "dingtalk_official_url"],
                    limit_page_length=50,
                )
            except Exception:
                item_rows = []
            for item in item_rows:
                if item.get("dingtalk_instance_id") or item.get("dingtalk_official_url"):
                    meta["source_approval_no"] = meta.get("source_approval_no") or item.get("source_doc_no") or ""
                    meta["source_instance_id"] = item.get("dingtalk_instance_id") or extract_dingtalk_instance_id(item.get("dingtalk_official_url")) or ""
                    meta["source_dingtalk_url"] = item.get("dingtalk_official_url") or ""
                    break
    if not meta.get("source_instance_id"):
        meta["source_instance_id"] = extract_dingtalk_instance_id(meta.get("source_dingtalk_url"))
    return meta


def get_dingtalk_order_link(batch_name: str) -> dict:
    """返回钉钉原单跳转信息。"""

    source_meta = _get_batch_source_meta(batch_name)
    payload = build_dingtalk_order_payload(
        batch_name=source_meta.get("batch_no") or batch_name,
        approval_no=source_meta.get("source_approval_no"),
        instance_id=source_meta.get("source_instance_id"),
        official_url=source_meta.get("source_dingtalk_url"),
    )
    return {
        "ok": True,
        "batch_name": batch_name,
        "message": "钉钉原单跳转信息已生成。" if payload["can_open"] else "当前批次缺少钉钉实例ID或官方链接。",
        "dingtalk_order": payload,
    }


# --- Database-backed query implementation for the Excel flat-table MVP. ---

import json as _json

from overseas_costing.utils.field_mapper import normalize_business_type, normalize_transport_mode

EXCEL_COLUMNS = [
    {"excel_col": "A", "fieldname": "material_code", "label": "物料编码"},
    {"excel_col": "B", "fieldname": "product_name", "label": "产品名称"},
    {"excel_col": "B1", "fieldname": "spec_model", "label": "规格型号 Especificación / Modelo"},
    {"excel_col": "C", "fieldname": "unit_price", "label": "采购单价"},
    {"excel_col": "C1", "fieldname": "purchase_currency", "label": "采购币种"},
    {"excel_col": "D", "fieldname": "quantity", "label": "采购数量"},
    {"excel_col": "D2", "fieldname": "actual_shipped_qty", "label": "出库数量（实际发货）"},
    {"excel_col": "D1", "fieldname": "unit", "label": "单位"},
    {"excel_col": "E", "fieldname": "goods_value", "label": "总货值"},
    {"excel_col": "F", "fieldname": "import_name", "label": "海关进口名称"},
    {"excel_col": "G", "fieldname": "hs_code", "label": "海关分类编码"},
    {"excel_col": "H", "fieldname": "category", "label": "大类分类"},
    {"excel_col": "I", "fieldname": "customs_no", "label": "报关单号"},
    {"excel_col": "J", "fieldname": "waybill_no", "label": "中国到墨西哥运单号"},
    {"excel_col": "K", "fieldname": "china_misc_rmb", "label": "中国运输及相关杂费 RMB"},
    {"excel_col": "L", "fieldname": "china_misc_mxn", "label": "中国运输及相关杂费 MXN"},
    {"excel_col": "M", "fieldname": "china_ocean_usd", "label": "中国海运 USD"},
    {"excel_col": "N", "fieldname": "cc_rate", "label": "C.C税率"},
    {"excel_col": "O", "fieldname": "cc_anti_dumping", "label": "C.C反倾销附加税"},
    {"excel_col": "P", "fieldname": "igi_rate", "label": "IGI关税税率"},
    {"excel_col": "Q", "fieldname": "igi_amount", "label": "IGI关税税额"},
    {"excel_col": "R", "fieldname": "iva_rate", "label": "IVA增值税税率"},
    {"excel_col": "S", "fieldname": "iva_amount", "label": "IVA增值税税额"},
    {"excel_col": "T", "fieldname": "goods_value_ratio", "label": "分摊比例（货值比）"},
    {"excel_col": "U", "fieldname": "dta", "label": "DTA海关递延税款"},
    {"excel_col": "V", "fieldname": "prv_duty", "label": "PRV从价税/关税"},
    {"excel_col": "W", "fieldname": "prv_iva", "label": "PRV的IVA"},
    {"excel_col": "X", "fieldname": "import_tax_total", "label": "IMPUESTOS合计清关税费"},
    {"excel_col": "Y", "fieldname": "revalidacion", "label": "REVALIDACION文件验证费"},
    {"excel_col": "Z", "fieldname": "maniobras", "label": "MANIOBRAS码头操作费"},
    {"excel_col": "AA", "fieldname": "muellaje", "label": "MUELLAJE码头作业费"},
    {"excel_col": "AB", "fieldname": "entrega_mercancia", "label": "ENTREGA DE MERCANCIA配送费用"},
    {"excel_col": "AC", "fieldname": "previo", "label": "PREVIO预检费用"},
    {"excel_col": "AD", "fieldname": "service_aa", "label": "Servicios A.A.货代服务费"},
    {"excel_col": "AE", "fieldname": "almacenajes", "label": "ALMACENAJES仓储费"},
    {"excel_col": "AF", "fieldname": "reconocimiento_aduanero", "label": "RECONOCIMIENTO ADUANERO"},
    {"excel_col": "AG", "fieldname": "honorarios", "label": "HONORARIOS"},
    {"excel_col": "AH", "fieldname": "complemento_maniobras", "label": "COMPLEMENTO DE MANIOBRAS"},
    {"excel_col": "AI", "fieldname": "desconsolidacion", "label": "DESCONSOLIDACION"},
    {"excel_col": "AJ", "fieldname": "maniobra_falso", "label": "MANIOBRA EN FALSO"},
    {"excel_col": "AK", "fieldname": "arrastre", "label": "ARRASTRE拖运费"},
    {"excel_col": "AL", "fieldname": "patio_regulador", "label": "PATIO REGULADOR"},
    {"excel_col": "AM", "fieldname": "entrega_vacio", "label": "ENTREGA DE VACIO还箱费"},
    {"excel_col": "AN", "fieldname": "limpieza_contenedor", "label": "LIMPIEZA DE CONTENEDOR"},
    {"excel_col": "AO", "fieldname": "mexico_customs_mxn", "label": "墨西哥清关费用 MXN"},
    {"excel_col": "AP", "fieldname": "mexico_customs_rmb", "label": "墨西哥清关费用 RMB"},
    {"excel_col": "AQ", "fieldname": "mexico_customs_usd", "label": "墨西哥清关费用 USD"},
    {"excel_col": "AR", "fieldname": "mexico_inland_mxn", "label": "墨西哥内陆运输费用 MXN"},
    {"excel_col": "AS", "fieldname": "mexico_misc_mxn", "label": "墨西哥杂费 MXN"},
    {"excel_col": "AT", "fieldname": "mexico_inland_misc_rmb", "label": "墨西哥内陆运输+杂费 RMB"},
    {"excel_col": "AU", "fieldname": "china_to_mexico_freight_rmb", "label": "中国到墨西哥运费 RMB"},
    {"excel_col": "AU1", "fieldname": "package_count", "label": "箱数"},
    {"excel_col": "AU2", "fieldname": "packaging_type", "label": "包装类型"},
    {"excel_col": "AU3", "fieldname": "net_weight_kg", "label": "货重净重 KG"},
    {"excel_col": "AV", "fieldname": "gross_weight_kg", "label": "货重毛重 KG"},
    {"excel_col": "AV1", "fieldname": "volume_m3", "label": "体积 m³"},
    {"excel_col": "AW", "fieldname": "weight_ratio", "label": "分摊比例（重量比）"},
    {"excel_col": "AX", "fieldname": "freight_alloc_rmb", "label": "运输费用分摊 RMB"},
    {"excel_col": "AY", "fieldname": "freight_alloc_mxn", "label": "运输费用分摊 MXN"},
    {"excel_col": "AZ", "fieldname": "total_logistics_mxn", "label": "运输+清关+杂费 MXN"},
    {"excel_col": "BA", "fieldname": "alloc_price_mxn", "label": "分摊物流价格 MXN"},
    {"excel_col": "BB", "fieldname": "total_cost_rmb", "label": "综合成本 RMB"},
    {"excel_col": "BC", "fieldname": "total_unit_rmb", "label": "综合单价 RMB"},
    {"excel_col": "BD", "fieldname": "project_collection", "label": "项目归集"},
    {"excel_col": "BE", "fieldname": "transport_mode", "label": "运输方式"},
]
EXCEL_FIELDNAMES = [column["fieldname"] for column in EXCEL_COLUMNS]
EXPORT_COLUMNS = [
    *EXCEL_COLUMNS[:4],
    {"excel_col": "C0", "fieldname": "purchase_price_source", "label": "采购单价来源"},
    *EXCEL_COLUMNS[4:],
]
VIRTUAL_ITEM_FIELDNAMES = frozenset({"package_count", "packaging_type"})
EXTRA_ITEM_FIELDS = [
    "name",
    "row_no",
    "excel_row_no",
    "product_name_es",
    "spec_model",
    "unit",
    "recipient",
    "purchase_currency",
    "actual_shipped_qty",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "source_type",
    "source_doc_no",
    "source_file_name",
    "parse_status",
    "manual_override_flag",
    "dingtalk_instance_id",
    "dingtalk_official_url",
    "derived_json",
    "sales_quantity",
    "sales_unit_price",
    "sales_currency",
    "sales_fx_rate",
    "sales_amount",
    "sales_amount_rmb",
    "sales_cost_rmb",
    "other_sales_expense_rmb",
    "gross_profit_rmb",
    "profit_rmb",
    "profit_margin",
    "profit_status",
]


def _persistent_item_fieldnames(fieldnames) -> list[str]:
    """Return fields physically stored on Overseas Cost Item."""
    return [
        fieldname
        for fieldname in fieldnames or []
        if fieldname not in VIRTUAL_ITEM_FIELDNAMES
    ]


ITEM_FILTER_FIELDS = (
    "customs_no",
    "waybill_no",
    "material_code",
    "product_name",
    "import_name",
    "hs_code",
    "category",
)
ITEM_KEYWORD_FIELDS = ITEM_FILTER_FIELDS + ("project_collection", "transport_mode")
DEFAULT_FX_RMB_TO_MXN = 2.6
DEFAULT_FX_USD_TO_RMB = round(1 / 0.1393, 6)
HIDDEN_APPROVAL_STATUSES = ("TERMINATED", "CANCELED", "CANCELLED", "REVOKED", "撤销", "已撤销")
INVALID_APPROVAL_STATUSES = HIDDEN_APPROVAL_STATUSES + (
    "REJECTED",
    "REFUSED",
    "DENIED",
    "ABORTED",
    "拒绝",
    "已拒绝",
    "驳回",
    "已驳回",
    "不通过",
    "未通过",
    "终止",
    "已终止",
    "取消",
    "已取消",
    "作废",
    "已作废",
)
APPROVAL_STATUS_FIELDNAMES = (
    "approval_status",
    "approvalStatus",
    "source_approval_status",
    "status",
    "process_status",
    "processStatus",
)
APPROVAL_MESSAGE_FIELDNAMES = (
    "message",
    "reason",
    "remark",
    "comment",
    "refuse_reason",
    "reject_reason",
    "approval_result",
    "approvalResult",
    "result",
)


def is_hidden_approval_status(status: str | None) -> bool:
    """判断钉钉审批状态是否属于无效审批状态。"""

    normalized = str(status or "").strip().upper()
    if not normalized:
        return False
    return any(str(hidden).upper() in normalized for hidden in HIDDEN_APPROVAL_STATUSES)


def is_invalid_approval_status(status: str | None) -> bool:
    """判断审批是否代表业务已拒绝、撤销或终止。"""

    normalized = str(status or "").strip().upper()
    if not normalized:
        return False
    return any(str(invalid).upper() in normalized for invalid in INVALID_APPROVAL_STATUSES)


def _load_batch_payload(batch_payload: str | dict | None) -> dict:
    if not batch_payload:
        return {}
    if isinstance(batch_payload, dict):
        return dict(batch_payload)
    loaded = _json.loads(batch_payload)
    if not isinstance(loaded, dict):
        raise ValueError(_("新增报关运单参数必须是对象。"))
    return loaded


def _clean_payload_text(payload: dict, fieldname: str) -> str:
    return str(payload.get(fieldname) or "").strip()


def _build_manual_batch_values(payload: dict) -> dict:
    batch_no = _clean_payload_text(payload, "batch_no")
    source_dingtalk_url = _clean_payload_text(payload, "source_dingtalk_url")
    source_instance_id = _clean_payload_text(payload, "source_instance_id") or extract_dingtalk_instance_id(source_dingtalk_url)
    values = {
        "batch_no": batch_no,
        "customs_no": _clean_payload_text(payload, "customs_no"),
        "waybill_no": _clean_payload_text(payload, "waybill_no"),
        "container_no": _clean_payload_text(payload, "container_no"),
        "sea_bill_no": _clean_payload_text(payload, "sea_bill_no"),
        "commercial_invoice_no": _clean_payload_text(payload, "commercial_invoice_no"),
        "transport_mode": normalize_transport_mode(payload.get("transport_mode")) or "SEA",
        "business_type": normalize_business_type(
            payload.get("business_type"),
            transport_mode=payload.get("transport_mode"),
        ) or "SEA_STANDARD",
        "project_collection": _clean_payload_text(payload, "project_collection"),
        "source_type": "manual",
        "source_approval_no": _clean_payload_text(payload, "source_approval_no"),
        "source_instance_id": source_instance_id,
        "source_dingtalk_url": source_dingtalk_url,
        "status": "Draft",
        "confirm_status": "Pending",
        "writeback_status": "Not Started",
        "version_count": 1,
        "item_count": 0,
        "import_remark": _clean_payload_text(payload, "import_remark") or "前端手工新增报关运单",
        "source_remark": _clean_payload_text(payload, "source_remark"),
    }
    return values


def create_batch(batch_payload: str | dict | None = None) -> dict:
    """新增一个空批次和默认当前版本，供后续手工加物料或文件补数。"""

    try:
        payload = _load_batch_payload(batch_payload)
    except Exception as exc:
        return {
            "ok": False,
            "dry_run": frappe is None,
            "message": f"新增报关运单参数解析失败：{exc}",
        }

    values = _build_manual_batch_values(payload)
    if not values["batch_no"]:
        return {
            "ok": False,
            "dry_run": frappe is None,
            "message": "请填写批次号/来源单号。",
        }

    version_values = {
        "version_code": f"手工-{values['batch_no']}",
        "version_type": "Estimated",
        "status": "Active",
        "is_current": 1,
        "source_type": "Manual",
        "fx_usd_to_rmb": DEFAULT_FX_USD_TO_RMB,
        "fx_rmb_to_mxn": DEFAULT_FX_RMB_TO_MXN,
        "remark": "前端手工新增批次默认版本",
    }

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch": values,
            "version": version_values,
            "message": "当前未连接 Frappe，已返回新增报关运单预览。",
        }

    existing_name = frappe.db.get_value("Overseas Cost Batch", {"batch_no": values["batch_no"]}, "name")
    if existing_name:
        return {
            "ok": False,
            "batch_name": existing_name,
            "message": f"批次号已存在：{values['batch_no']}。请直接查询或换一个批次号。",
        }

    batch_doc = frappe.get_doc({"doctype": "Overseas Cost Batch", **values}).insert(ignore_permissions=True)
    version_doc = frappe.get_doc(
        {
            "doctype": "Overseas Cost Version",
            "batch": batch_doc.name,
            **version_values,
        }
    ).insert(ignore_permissions=True)
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_doc.name,
        {
            "current_version": version_doc.name,
            "version_count": 1,
            "item_count": 0,
        },
        update_modified=False,
    )
    frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_doc.name,
            "version": version_doc.name,
            "action_type": "BATCH_EDIT",
            "field_name": "batch",
            "new_value": _json.dumps(values, ensure_ascii=False, default=str),
            "operator_name": getattr(frappe.session, "user", "") if getattr(frappe, "session", None) else "",
            "action_remark": "前端手工新增报关运单",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "ok": True,
        "batch_name": batch_doc.name,
        "version_name": version_doc.name,
        "batch_no": values["batch_no"],
        "message": "报关运单已新增，可继续添加物料或导入附件补数。",
    }


def _resolve_batch_name(batch_name: str) -> str | None:
    if frappe is None:
        return None
    batch = frappe.db.get_value("Overseas Cost Batch", batch_name, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    batch = frappe.db.get_value("Overseas Cost Batch", {"batch_no": batch_name}, ["name"], as_dict=True)
    if batch:
        return batch["name"]
    return None


def _resolve_version_name(batch_doc_name: str, version_name: str | None = None) -> str | None:
    if frappe is None:
        return version_name
    if version_name:
        return version_name
    current_version = frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "current_version")
    if current_version:
        return current_version
    rows = frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=["name"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return rows[0]["name"] if rows else None


def _load_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        return _json.loads(text)
    except Exception:
        return {}


def _append_subsidiary_candidates(candidates: list, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    candidates.extend(
        [
            payload.get("subsidiary_code"),
            payload.get("business_entity_name"),
            payload.get("business_entity"),
            payload.get("entity_name"),
            payload.get("company"),
            payload.get("company_name"),
        ]
    )
    subsidiary = payload.get("subsidiary")
    if isinstance(subsidiary, dict):
        candidates.extend(
            [
                subsidiary.get("subsidiary_code"),
                subsidiary.get("business_entity_name"),
                subsidiary.get("name"),
                subsidiary.get("deptName"),
            ]
        )
    elif subsidiary is not None:
        candidates.append(subsidiary)

    form_fields = payload.get("form_fields")
    if not isinstance(form_fields, dict):
        return
    for fieldname, value in form_fields.items():
        normalized = str(fieldname or "").replace(" ", "").lower()
        if (
            "entidadcomercial" in normalized
            or "businessentity" in normalized
            or "empresa" in normalized
            or "company" in normalized
            or "业务主体" in normalized
            or "公司" in normalized
            or "主体" in normalized
        ):
            candidates.append(value)


def _subsidiary_candidate_text(candidate) -> str:
    if isinstance(candidate, dict):
        for fieldname in (
            "subsidiary_code",
            "business_entity_name",
            "business_entity",
            "entity_name",
            "name",
            "deptName",
            "label",
            "text",
            "displayName",
            "displayValue",
            "optionName",
            "orgName",
            "companyName",
            "value",
        ):
            text = _subsidiary_candidate_text(candidate.get(fieldname))
            if text:
                return text
        for fieldname in ("selectedOptions", "selected_options", "options", "items", "list", "data", "children"):
            text = _subsidiary_candidate_text(candidate.get(fieldname))
            if text:
                return text
        return ""
    if isinstance(candidate, list):
        for item in candidate:
            text = _subsidiary_candidate_text(item)
            if text:
                return text
        return ""
    return str(candidate or "").strip()


def _resolve_batch_subsidiary_code(batch: dict) -> str:
    direct_value = batch.get("subsidiary_code")
    if direct_value is not None and str(direct_value).strip():
        return str(direct_value).strip()

    source_data = _load_json(batch.get("extra_json"))
    trace = source_data.get("oa_logistics_trace") if isinstance(source_data.get("oa_logistics_trace"), dict) else {}
    candidates = []
    _append_subsidiary_candidates(candidates, source_data)
    _append_subsidiary_candidates(candidates, trace)

    for candidate in candidates:
        text = _subsidiary_candidate_text(candidate)
        if text:
            return text
    return ""


def _resolve_batch_business_type(batch: dict) -> str:
    direct_value = batch.get("business_type")
    transport_mode = batch.get("transport_mode")
    normalized = normalize_business_type(direct_value)
    if normalized:
        return normalized

    source_data = _load_json(batch.get("extra_json"))
    source_transport_mode = transport_mode or source_data.get("transport_mode")
    normalized = normalize_business_type(
        source_data.get("business_type") or source_data.get("businessType")
    )
    if normalized:
        return normalized
    return normalize_business_type(
        source_transport_mode,
        transport_mode=source_transport_mode,
    ) or ""


def _clean_query_value(value) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _normalize_item_query_filters(filters: dict | None = None, **overrides) -> dict:
    query_filters = {}
    if isinstance(filters, dict):
        query_filters.update(filters)
    for key, value in overrides.items():
        if value not in (None, ""):
            query_filters[key] = value
    return {
        key: _clean_query_value(value)
        for key, value in query_filters.items()
        if _clean_query_value(value)
    }


def _build_item_query_args(
    batch_doc_name: str,
    version_name: str,
    filters: dict | None = None,
    *,
    keyword: str | None = None,
) -> tuple[list[list[str]], list[list[str]]]:
    query_filters = _normalize_item_query_filters(filters)
    keyword_value = _clean_query_value(keyword or query_filters.pop("keyword", ""))
    db_filters = [["batch", "=", batch_doc_name], ["version", "=", version_name], ["is_excluded", "=", 0]]

    for fieldname in ITEM_FILTER_FIELDS:
        value = query_filters.get(fieldname)
        if value:
            db_filters.append([fieldname, "like", f"%{value}%"])

    or_filters = []
    if keyword_value:
        or_filters = [[fieldname, "like", f"%{keyword_value}%"] for fieldname in ITEM_KEYWORD_FIELDS]

    return db_filters, or_filters


def _has_source_value(value) -> bool:
    return str(value or "").strip() != ""


def _source_status_key(value) -> str:
    return str(value or "").strip().lower()


def _is_parsed_attachment(row: dict) -> bool:
    return _source_status_key(row.get("parse_status")) == "parsed"


def _json_loads_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_tax_certificate_reconciliation(attachment_rows: list[dict]) -> dict:
    tax_rows = [
        row
        for row in attachment_rows
        if row.get("attachment_type") == "Tax Certificate" or row.get("source_type") == "Voucher"
    ]
    if not tax_rows:
        return {}

    sorted_rows = sorted(
        tax_rows,
        key=lambda row: str(row.get("modified") or row.get("creation") or ""),
        reverse=True,
    )
    for row in sorted_rows:
        mapped_result = _json_loads_dict(row.get("mapped_result_json"))
        if not mapped_result:
            continue
        parse_result = _json_loads_dict(row.get("parse_result_json"))
        summary = parse_result.get("summary") if isinstance(parse_result.get("summary"), dict) else {}
        voucher = mapped_result.get("voucher") if isinstance(mapped_result.get("voucher"), dict) else {}
        system = mapped_result.get("system") if isinstance(mapped_result.get("system"), dict) else {}
        difference = mapped_result.get("difference") if isinstance(mapped_result.get("difference"), dict) else {}
        manual_resolution = (
            mapped_result.get("manual_resolution")
            if isinstance(mapped_result.get("manual_resolution"), dict)
            else {}
        )
        return {
            "name": row.get("name") or "",
            "file_name": row.get("file_name") or "",
            "modified": row.get("modified") or "",
            "status": mapped_result.get("status") or "",
            "status_label": mapped_result.get("status_label") or "",
            "manual_resolution_status_label": manual_resolution.get("status_label") or "",
            "paid_total_mxn": (
                voucher.get("paid_total_mxn")
                if voucher.get("paid_total_mxn") is not None
                else summary.get("paid_total_mxn")
            ),
            "system_tax_total_mxn": system.get("system_import_tax_total_mxn"),
            "tax_total_diff_mxn": difference.get("tax_total_diff_mxn"),
            "direction_label": difference.get("direction_label") or "",
        }
    return {}


def _get_oa_logistics_trace(extra_json) -> dict:
    if isinstance(extra_json, dict):
        payload = dict(extra_json)
    else:
        try:
            payload = json.loads(extra_json or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
    if not isinstance(payload, dict):
        return {}
    trace = payload.get("oa_logistics_trace")
    return trace if isinstance(trace, dict) else payload


def _source_trace_is_readable(batch: dict) -> bool:
    """Distinguish unavailable/corrupt internal data from a genuinely empty trace."""
    if "extra_json" not in batch:
        return False
    raw = batch.get("extra_json")
    try:
        payload = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return "oa_logistics_trace" not in payload or isinstance(payload["oa_logistics_trace"], dict)


def _invalid_approval_text(row: dict) -> str:
    for fieldname in APPROVAL_STATUS_FIELDNAMES + APPROVAL_MESSAGE_FIELDNAMES:
        value = row.get(fieldname)
        if value is not None and is_invalid_approval_status(str(value)):
            return str(value).strip()
    return ""


def _linked_purchase_approval_lists(trace: dict) -> list[list[dict]]:
    candidates = [
        trace.get("linked_purchase_approvals"),
        trace.get("purchase_summaries"),
    ]
    purchase_sync = trace.get("purchase_sync")
    if isinstance(purchase_sync, dict):
        candidates.extend(
            [
                purchase_sync.get("linked_purchase_approvals"),
                purchase_sync.get("purchase_summaries"),
                purchase_sync.get("items"),
            ]
        )
    return [rows for rows in candidates if isinstance(rows, list)]


def _build_purchase_approval_status_summary(trace: dict) -> dict:
    """汇总关联采购审批状态，供列表、详情和回写检查共用。"""

    approvals: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rows in _linked_purchase_approval_lists(trace):
        for row in rows:
            if not isinstance(row, dict):
                continue
            approval_no = str(
                row.get("approval_no")
                or row.get("source_approval_no")
                or row.get("business_id")
                or row.get("businessId")
                or row.get("source_instance_id")
                or row.get("proc_inst_id")
                or row.get("instance_id")
                or ""
            ).strip()
            instance_id = str(
                row.get("source_instance_id")
                or row.get("proc_inst_id")
                or row.get("instance_id")
                or ""
            ).strip()
            key = (approval_no, instance_id)
            if key in seen:
                continue
            seen.add(key)
            approvals.append(
                {
                    "approval_no": approval_no,
                    "source_instance_id": instance_id,
                    "approval_status": str(
                        row.get("approval_status")
                        or row.get("source_approval_status")
                        or row.get("status")
                        or ""
                    ).strip(),
                    "approval_title": str(
                        row.get("approval_title") or row.get("title") or row.get("display_name") or ""
                    ).strip(),
                    "message": str(row.get("message") or row.get("reason") or "").strip(),
                }
            )

    invalid = [row for row in approvals if is_invalid_approval_status(row["approval_status"]) or is_invalid_approval_status(row["message"])]
    missing_status = [row for row in approvals if not row["approval_status"]]
    valid = [row for row in approvals if row not in invalid and row not in missing_status]
    if not approvals:
        state = "missing"
        message = "当前国际物流审批未关联采购审批，采购单价来源待确认。"
    elif invalid and valid:
        state = "partial"
        message = f"已关联 {len(approvals)} 条采购审批，其中 {len(valid)} 条有效，{len(invalid)} 条已排除。"
    elif invalid:
        state = "excluded"
        message = f"已关联 {len(approvals)} 条采购审批，均已拒绝/撤销/终止并排除。"
    elif missing_status:
        state = "pending"
        message = f"已关联 {len(approvals)} 条采购审批，但有 {len(missing_status)} 条状态尚未同步。"
    else:
        state = "valid"
        message = f"已关联 {len(approvals)} 条采购审批，状态已同步。"

    return {
        "state": state,
        "message": message,
        "linked_purchase_count": len(approvals),
        "linked_purchase_approval_nos": [row["approval_no"] for row in approvals if row["approval_no"]],
        "linked_purchase_approval_statuses": [row["approval_status"] for row in approvals if row["approval_status"]],
        "invalid_purchase_approval_count": len(invalid),
        "linked_purchase_approvals": approvals,
    }


def _build_invalid_business_state(batch: dict, items: list[dict] | None = None) -> dict:
    from overseas_costing.services.effective_source_values import batch_source_context
    context = batch_source_context(batch['name'],batch.get('current_version')) if batch.get('name') else {}
    if context.get('root_kind') == 'expense':
        if not context.get('available') or not context.get('approved') or context.get('invalid'):
            return {'invalid':True,'scope':'logistics_expense','status':'pending',
                    'message':'当前采购支出尚未有效采用或审批已失效，资料待处理。'}
        # International logistics no longer determines the selected expense's validity.
        batch = {**batch,'source_approval_status':None}
        if items is None:
            return {'invalid':False}
        from overseas_costing.services.logistics_settlement.application import row_meta
        items = [row for row in items if not ((row_meta(row).get('settlement_cargo') or {}).get('merchandise_price') or {}).get('present')]
        if not items:
            return {'invalid':False}
    source_status = batch.get("source_approval_status")
    if is_invalid_approval_status(source_status):
        return {
            "invalid": True,
            "scope": "source_approval",
            "status": str(source_status or "").strip(),
            "message": f"当前 OA 审批状态为 {str(source_status or '').strip()}，业务已拒绝/撤销/终止，不进入综合成本确认或 ERP 推送。",
        }

    trace = _get_oa_logistics_trace(batch.get("extra_json"))
    purchase_status = _build_purchase_approval_status_summary(trace)
    if purchase_status["state"] == "excluded":
        return {
            "invalid": True,
            "scope": "linked_purchase_approval",
            "status": "excluded",
            "message": "当前批次的关联采购审批均已拒绝/撤销/终止，不进入综合成本确认或 ERP 推送。",
        }

    if purchase_status["state"] == "partial" and items is not None:
        invalid_instance_ids = {
            str(row.get("source_instance_id") or "").strip()
            for row in purchase_status["linked_purchase_approvals"]
            if (
                is_invalid_approval_status(row.get("approval_status"))
                or is_invalid_approval_status(row.get("message"))
            )
            and str(row.get("source_instance_id") or "").strip()
        }
        valid_instance_ids = {
            str(row.get("source_instance_id") or "").strip()
            for row in purchase_status["linked_purchase_approvals"]
            if not (
                is_invalid_approval_status(row.get("approval_status"))
                or is_invalid_approval_status(row.get("message"))
            )
            and str(row.get("approval_status") or "").strip()
            and str(row.get("source_instance_id") or "").strip()
        }
        contaminated_rows = []
        for item in items:
            source_type = str(item.get("source_type") or "").strip().upper()
            instance_id = str(item.get("dingtalk_instance_id") or "").strip()
            if instance_id in invalid_instance_ids or (
                source_type == "PURCHASE_EXPENSE_OA" and instance_id not in valid_instance_ids
            ):
                contaminated_rows.append(item)
        if contaminated_rows:
            return {
                "invalid": True,
                "scope": "linked_purchase_approval",
                "status": "manual_required",
                "contaminated_item_count": len(contaminated_rows),
                "message": "当前批次仍包含已拒绝/撤销/终止的关联采购审批数据，需完成人工清理后才能确认成本或推送 ERP。",
            }

    return {"invalid": False}


def _quote_candidate_summary(candidate: dict) -> dict:
    return {
        "carrier": str(candidate.get("carrier") or "").strip(),
        "amount": candidate.get("amount"),
        "currency": str(candidate.get("currency") or "").strip(),
        "volume_m3": candidate.get("volume_m3"),
        "gross_weight_kg": candidate.get("gross_weight_kg"),
        "chargeable_weight_kg": candidate.get("chargeable_weight_kg"),
        "unit_freight_per_kg": candidate.get("unit_freight_per_kg"),
        "billing_method": str(candidate.get("billing_method") or "").strip(),
        "allocation_basis": str(candidate.get("allocation_basis") or "").strip(),
        "pre_delivery_date": str(candidate.get("pre_delivery_date") or "").strip(),
        "destination": str(candidate.get("destination") or "").strip(),
        "source": str(candidate.get("source") or "").strip(),
        "evidence_line": str(candidate.get("evidence_line") or "").strip(),
        "source_field": str(candidate.get("source_field") or "").strip(),
        "status": str(candidate.get("status") or "待确认").strip(),
    }


def _public_logistics_text_summary(summary: dict) -> dict:
    if not isinstance(summary, dict):
        return {}
    allowed_fields = (
        "transport_mode",
        "transport_mode_raw",
        "logistics_no",
        "pre_delivery_date",
        "destination",
        "gross_weight_kg",
        "logistics_quote_amount",
        "logistics_quote_currency",
        "logistics_quote_carrier",
        "ai_used",
        "ai_model",
        "ai_confidence",
        "ai_reason",
    )
    public_summary = {
        key: summary.get(key)
        for key in allowed_fields
        if summary.get(key) not in (None, "")
    }
    evidence = str(summary.get("logistics_quote_evidence") or "").strip()
    if evidence:
        public_summary["logistics_quote_evidence"] = evidence[:180]
    return public_summary


def _logistics_text_summary(trace: dict) -> dict:
    summary = trace.get("logistics_text_summary")
    if isinstance(summary, dict):
        return _public_logistics_text_summary(summary)
    if isinstance(trace.get("form_fields"), dict):
        try:
            from overseas_costing.scripts.import_oa_logistics import extract_logistics_text_summary_from_approval

            return _public_logistics_text_summary(extract_logistics_text_summary_from_approval(trace, allow_ai=False))
        except Exception:
            return {}
    return {}


def _build_batch_source_status(batch: dict, attachments: list[dict] | None = None) -> dict:
    """按业务资料链路汇总批次来源状态，供前端数据检查展示。"""

    attachment_rows = attachments or []
    source_no = (
        batch.get("source_approval_no")
        or batch.get("source_instance_id")
        or batch.get("batch_no")
        or batch.get("name")
        or ""
    )
    has_oa_logistics = (
        batch.get("source_type") == "oa_logistics"
        or _has_source_value(batch.get("source_approval_no"))
        or _has_source_value(batch.get("source_instance_id"))
        or _has_source_value(batch.get("source_dingtalk_url"))
    )

    oa_attachment_rows = [row for row in attachment_rows if row.get("source_type") == "OA"]
    packing_list_rows = [row for row in attachment_rows if row.get("attachment_type") == "Packing List"]
    tax_certificate_rows = [
        row
        for row in attachment_rows
        if row.get("attachment_type") == "Tax Certificate" or row.get("source_type") == "Voucher"
    ]
    batch_source_attachment_count = int(batch.get("source_attachment_count") or 0)
    oa_attachment_count = max(batch_source_attachment_count, len(oa_attachment_rows))
    trace = _get_oa_logistics_trace(batch.get("extra_json"))
    quote_candidates = trace.get("logistics_quote_candidates") or []
    if not isinstance(quote_candidates, list):
        quote_candidates = []
    if not quote_candidates and isinstance(trace.get("form_fields"), dict):
        try:
            from overseas_costing.scripts.import_oa_logistics import extract_logistics_quote_candidates_from_approval

            quote_candidates = extract_logistics_quote_candidates_from_approval({"form_fields": trace["form_fields"]})
        except Exception:
            quote_candidates = []
    quote_candidates = [_quote_candidate_summary(row) for row in quote_candidates if isinstance(row, dict)]
    confirmed_quote = trace.get("confirmed_logistics_quote")
    confirmed_quote = _quote_candidate_summary(confirmed_quote) if isinstance(confirmed_quote, dict) else {}
    logistics_text_summary = _logistics_text_summary(trace)
    purchase_status = _build_purchase_approval_status_summary(trace)
    if has_oa_logistics and not _source_trace_is_readable(batch):
        purchase_status.update(state="unreadable", message="采购来源信息无法读取，请刷新或联系管理员检查同步数据。")
    invalid_business_state = _build_invalid_business_state(batch)

    return {
        "source_no": source_no,
        "has_oa_logistics": bool(has_oa_logistics),
        "source_approval_status": batch.get("source_approval_status") or "",
        "invalid_business": bool(invalid_business_state.get("invalid")),
        "invalid_business_reason": invalid_business_state.get("message") or "",
        "invalid_business_scope": invalid_business_state.get("scope") or "",
        "purchase_approval_sync_state": purchase_status["state"],
        "purchase_approval_sync_message": purchase_status["message"],
        "linked_purchase_count": purchase_status["linked_purchase_count"],
        "linked_purchase_approval_nos": purchase_status["linked_purchase_approval_nos"],
        "linked_purchase_approval_statuses": purchase_status["linked_purchase_approval_statuses"],
        "invalid_purchase_approval_count": purchase_status["invalid_purchase_approval_count"],
        "linked_purchase_approvals": purchase_status["linked_purchase_approvals"],
        "oa_attachment_count": oa_attachment_count,
        "registered_attachment_count": len(attachment_rows),
        "packing_list_count": len(packing_list_rows),
        "parsed_packing_list_count": sum(1 for row in packing_list_rows if _is_parsed_attachment(row)),
        "tax_certificate_count": len(tax_certificate_rows),
        "parsed_tax_certificate_count": sum(1 for row in tax_certificate_rows if _is_parsed_attachment(row)),
        "has_form_attachments": oa_attachment_count > 0,
        "has_packing_list": bool(packing_list_rows),
        "has_tax_certificate": bool(tax_certificate_rows),
        "logistics_quote_candidate_count": len(quote_candidates),
        "logistics_quote_candidates": quote_candidates,
        "confirmed_logistics_quote": confirmed_quote,
        "logistics_text_summary": logistics_text_summary,
        "has_confirmed_logistics_quote": bool(confirmed_quote.get("amount")),
        "latest_tax_certificate_reconciliation": _latest_tax_certificate_reconciliation(attachment_rows),
        "source_priority_policy": source_priority_service.get_source_priority_policy(),
        "source_priority_summary": source_priority_service.get_source_priority_summary(),
    }


def _attach_batch_source_status(items: list[dict]) -> list[dict]:
    if not items:
        return items

    batch_names = [item.get("name") for item in items if item.get("name")]
    attachment_rows: list[dict] = []
    if batch_names:
        try:
            attachment_rows = frappe.get_all(
                "Overseas Cost Attachment",
                filters={"batch": ["in", batch_names]},
                fields=[
                    "name",
                    "batch",
                    "source_type",
                    "attachment_type",
                    "parse_status",
                    "file_name",
                    "parse_result_json",
                    "mapped_result_json",
                    "modified",
                ],
                limit_page_length=10000,
            )
        except Exception:
            attachment_rows = []

    attachments_by_batch: dict[str, list[dict]] = {}
    for row in attachment_rows:
        batch_name = row.get("batch")
        if not batch_name:
            continue
        attachments_by_batch.setdefault(batch_name, []).append(row)

    for item in items:
        item["source_status"] = _build_batch_source_status(
            item,
            attachments_by_batch.get(item.get("name"), []),
        )
    return items


def _attach_batch_calculation_snapshot(items: list[dict]) -> list[dict]:
    """把当前版本的 AI/分摊快照附加到批次列表，供右下角复核面板展示。"""

    if not items:
        return items

    version_names = [item.get("current_version") for item in items if item.get("current_version")]
    if not version_names:
        return items

    versions = frappe.get_all(
        "Overseas Cost Version",
        filters={"name": ["in", version_names], "batch": ["in", [item["name"] for item in items]]},
        fields=["name", "batch", "summary_snapshot_json", "rule_snapshot_json", "calculated_at"],
        limit_page_length=len(version_names),
    )
    versions_by_pair = {(version["batch"], version["name"]): version for version in versions}
    for item in items:
        version = versions_by_pair.get((item.get("name"), item.get("current_version"))) or {}
        summary = _load_json(version.get("summary_snapshot_json"))
        rules = _load_json(version.get("rule_snapshot_json"))
        item["summary_snapshot"] = summary
        item["ai_allocation"] = summary.get("ai_allocation") or {}
        item["allocation_rule_snapshot"] = rules if isinstance(rules, list) else []
        item["calculated_at"] = version.get("calculated_at")
    return items


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _classic_history_or_filters() -> list[list[str]]:
    or_filters: list[list[str]] = []
    for key in CLASSIC_HISTORY_BATCH_KEYS:
        or_filters.extend(
            [
                ["name", "=", key],
                ["batch_no", "=", key],
                ["source_approval_no", "=", key],
            ]
        )
    return or_filters


def _recent_start(recent_days: int) -> str:
    start = datetime.now() - timedelta(days=max(recent_days, 1))
    return start.strftime("%Y-%m-%d %H:%M:%S")


def _date_filter_boundary(value: object, *, end_of_day: bool = False) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    try:
        date_value = datetime.strptime(raw_value[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return date_value.strftime("%Y-%m-%d 23:59:59" if end_of_day else "%Y-%m-%d 00:00:00")


def _build_default_batch_time_filters(filters: dict) -> tuple[list, list, int, bool]:
    recent_days = max(_to_int(filters.get("recent_days"), DEFAULT_BATCH_RECENT_DAYS), 1)
    include_history = _to_bool(filters.get("include_history"))
    has_keyword = bool(str(filters.get("keyword") or "").strip())
    start_date = _date_filter_boundary(filters.get("start_date"))
    end_date = _date_filter_boundary(filters.get("end_date"), end_of_day=True)
    if start_date or end_date:
        date_filters = []
        if start_date:
            date_filters.append(["source_created_at", ">=", start_date])
        if end_date:
            date_filters.append(["source_created_at", "<=", end_date])
        return date_filters, [], recent_days, False
    if include_history or has_keyword:
        return [], [], recent_days, False

    return [["source_created_at", ">=", _recent_start(recent_days)]], _classic_history_or_filters(), recent_days, True


def _dedupe_batches(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    items: list[dict] = []
    for row in rows:
        name = row.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(row)
    return items


def _keyword_item_batch_names(keyword: str, authorized_names: list[str]) -> list[str]:
    if not keyword or not authorized_names:
        return []
    like_keyword = f"%{keyword}%"
    rows = frappe.get_all(
        "Overseas Cost Item",
        filters=[["batch", "in", authorized_names], ["is_excluded", "=", 0]],
        or_filters=[
            ["material_code", "like", like_keyword],
            ["product_name", "like", like_keyword],
            ["product_name_es", "like", like_keyword],
            ["import_name", "like", like_keyword],
            ["hs_code", "like", like_keyword],
            ["category", "like", like_keyword],
            ["customs_no", "like", like_keyword],
            ["waybill_no", "like", like_keyword],
            ["source_doc_no", "like", like_keyword],
        ],
        fields=["batch"],
        limit_page_length=0,
    )
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = row.get("batch")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _normalize_transport_filter(value) -> str:
    raw_value = str(value or "").strip()
    if not raw_value or raw_value in {"ALL", "*", "全部"}:
        return ""
    return normalize_transport_mode(raw_value) or raw_value


def get_batch_filter_options() -> dict:
    """返回钉钉业务主体字段的完整选项，不随当前列表查询条件收缩。"""
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "items": [],
        }

    fields = ["extra_json"]
    if _db_has_column("Overseas Cost Batch", "subsidiary_code"):
        fields.insert(0, "subsidiary_code")
    rows = frappe.get_list(
        "Overseas Cost Batch",
        filters={},
        fields=fields,
        limit_page_length=0,
    )
    values = {
        _resolve_batch_subsidiary_code(row)
        for row in rows
        if _resolve_batch_subsidiary_code(row)
    }
    return {
        "ok": True,
        "items": sorted(values, key=lambda value: value.casefold()),
        "business_types": [
            {"value": "SEA_STANDARD", "label": "海运正报正清"},
            {"value": "SEA_DDP", "label": "海运 DDP（双清包税）"},
            {"value": "AIR_DDP", "label": "空运 DDP（双清包税）"},
            {"value": "AIR_STANDARD", "label": "正常空运"},
            {"value": "EXPRESS", "label": "快递"},
        ],
    }


def get_batch_list(filters: dict) -> dict:
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，返回空批次列表。",
            "filters": filters,
            "items": [],
            "total": 0,
        }

    # First apply Frappe Role/User Permission/share rules. Internal source fields
    # have higher permlevels and must not be interpreted as missing when get_list
    # omits them. Every subsequent query is restricted to these readable IDs.
    authorized_names = [row["name"] for row in frappe.get_list(
        "Overseas Cost Batch", fields=["name"], filters=[], limit_page_length=0
    )]
    if not authorized_names:
        return {"ok": True, "items": [], "total": 0, "filters": filters}
    transport_mode = _normalize_transport_filter(filters.get("transport_mode"))
    db_filters = [["name", "in", authorized_names]]
    if transport_mode:
        db_filters.append(["transport_mode", "=", transport_mode])
    has_business_type_column = _db_has_column("Overseas Cost Batch", "business_type")
    if filters.get("business_type") and has_business_type_column:
        db_filters.append(["business_type", "=", filters["business_type"]])
    if filters.get("status"):
        db_filters.append(["status", "=", filters["status"]])
    recent_filters, default_or_filters, recent_days, recent_only = _build_default_batch_time_filters(filters)
    db_filters.extend(recent_filters)

    keyword = filters.get("keyword")
    fields = [
        "name",
        "batch_no",
        "customs_no",
        "waybill_no",
        "transport_mode",
        "project_collection",
        "source_type",
        "source_file_name",
        "source_sheet",
        "source_range",
        "source_approval_no",
        "source_instance_id",
        "source_dingtalk_url",
        "source_approval_status",
        "source_attachment_count",
        "source_created_at",
        "status",
        "current_version",
        "confirm_status",
        "writeback_status",
        "writeback_time",
        "writeback_message",
        "item_count",
        "total_goods_value",
        "total_gross_weight_kg",
        "estimated_total_cost_rmb",
        "actual_total_cost_rmb",
        "extra_json",
        "modified",
    ]
    if has_business_type_column:
        fields.insert(5, "business_type")
    if _db_has_column("Overseas Cost Batch", "subsidiary_code"):
        fields.insert(7 if has_business_type_column else 6, "subsidiary_code")
    raw_candidate_limit = filters.get("candidate_limit")
    try:
        candidate_limit = 200 if raw_candidate_limit is None else int(raw_candidate_limit)
    except (TypeError, ValueError):
        candidate_limit = 200
    candidate_limit = 0 if candidate_limit <= 0 else min(10000, candidate_limit)
    query_kwargs = {
        "filters": db_filters,
        "fields": fields,
        "order_by": "source_created_at desc, modified desc",
        "limit_page_length": candidate_limit,
    }
    if keyword:
        like_keyword = f"%{keyword}%"
        query_kwargs["or_filters"] = [
            ["batch_no", "like", like_keyword],
            ["customs_no", "like", like_keyword],
            ["waybill_no", "like", like_keyword],
            ["source_approval_no", "like", like_keyword],
            ["source_instance_id", "like", like_keyword],
            ["project_collection", "like", like_keyword],
        ]

    items = frappe.get_all("Overseas Cost Batch", **query_kwargs)
    if keyword:
        item_batch_names = _keyword_item_batch_names(str(keyword).strip(), authorized_names)
        if item_batch_names:
            item_filters = list(db_filters)
            item_filters.append(["name", "in", item_batch_names])
            item_batches = frappe.get_all(
                "Overseas Cost Batch",
                filters=item_filters,
                fields=fields,
                order_by="source_created_at desc, modified desc",
                limit_page_length=candidate_limit,
            )
            items = _dedupe_batches(items + item_batches)
    if default_or_filters:
        classic_filters = [["name", "in", authorized_names]]
        if transport_mode:
            classic_filters.append(["transport_mode", "=", transport_mode])
        if filters.get("business_type") and has_business_type_column:
            classic_filters.append(["business_type", "=", filters["business_type"]])
        if filters.get("status"):
            classic_filters.append(["status", "=", filters["status"]])
        classic_items = frappe.get_all(
            "Overseas Cost Batch",
            filters=classic_filters,
            or_filters=default_or_filters,
            fields=fields,
            order_by="source_created_at desc, modified desc",
            limit_page_length=50,
        )
        for item in classic_items:
            item["is_classic_sample"] = 1
            item["sample_note"] = "历史样本"
        items = _dedupe_batches(items + classic_items)
        items.sort(key=lambda row: (str(row.get("source_created_at") or ""), str(row.get("modified") or "")), reverse=True)

    # Keyword OR-branches are combined before paging and use one deterministic order.
    items.sort(key=lambda row: (str(row.get("source_created_at") or ""), str(row.get("modified") or ""), str(row.get("name") or "")), reverse=True)
    classic_keys = set(CLASSIC_HISTORY_BATCH_KEYS)
    for item in items:
        item["business_type"] = _resolve_batch_business_type(item)
        item["subsidiary_code"] = _resolve_batch_subsidiary_code(item)
        if item.get("batch_no") in classic_keys or item.get("source_approval_no") in classic_keys or item.get("name") in classic_keys:
            item["is_classic_sample"] = 1
            item["sample_note"] = "历史样本"

    if filters.get("business_type") and not has_business_type_column:
        normalized_business_type = normalize_business_type(filters["business_type"])
        items = [
            item
            for item in items
            if _resolve_batch_business_type(item) == normalized_business_type
        ]
    items = _attach_batch_source_status(items)
    items = _attach_batch_calculation_snapshot(items)
    for item in items:
        item.pop("extra_json", None)
    return {
        "ok": True,
        "message": "批次列表已返回。",
        "filters": filters,
        "items": items,
        "total": len(items),
    }


def get_batch_detail(batch_name: str, version_name: str | None = None) -> dict:
    if frappe is None:
        source_meta = _get_batch_source_meta(batch_name)
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，返回批次详情预览。",
            "batch_name": batch_name,
            "version_name": version_name,
            "header": {"batch_no": source_meta.get("batch_no") or batch_name},
            "summary": {},
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    header_fields = [
        "name",
        "modified",
        "batch_no",
        "customs_no",
        "waybill_no",
        "container_no",
        "sea_bill_no",
        "commercial_invoice_no",
        "transport_mode",
        "project_collection",
        "source_type",
        "source_file_name",
        "source_sheet",
        "source_range",
        "source_approval_no",
        "source_instance_id",
        "source_dingtalk_url",
        "source_approval_status", "source_attachment_count", "extra_json",
        "status",
        "current_version",
        "confirm_status",
        "writeback_status",
        "writeback_time",
        "writeback_message",
        "item_count",
        "total_goods_value",
        "total_gross_weight_kg",
        "estimated_total_cost_rmb",
        "actual_total_cost_rmb",
    ]
    if _db_has_column("Overseas Cost Batch", "business_type"):
        header_fields.insert(8, "business_type")
    if _db_has_column("Overseas Cost Batch", "subsidiary_code"):
        header_fields.insert(10 if _db_has_column("Overseas Cost Batch", "business_type") else 9, "subsidiary_code")
    header = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        header_fields,
        as_dict=True,
    ) or {}
    header["business_type"] = _resolve_batch_business_type(header)
    header["subsidiary_code"] = _resolve_batch_subsidiary_code(header)
    _attach_batch_source_status([header])
    header.pop("extra_json", None)
    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    version = {}
    summary = {}
    if resolved_version_name:
        version = frappe.db.get_value(
            "Overseas Cost Version",
            resolved_version_name,
            [
                "name",
                "version_code",
                "version_type",
                "status",
                "is_current",
                "fx_usd_to_rmb",
                "fx_rmb_to_mxn",
                "calculated_at",
                "summary_snapshot_json",
            ],
            as_dict=True,
        ) or {}
        summary = _load_json(version.get("summary_snapshot_json"))
        comprehensive = summary.get("comprehensive_cost")
        comprehensive_items = (comprehensive.get("items") or []) if isinstance(comprehensive, dict) else []
        if any(isinstance(item, dict) and not item.get("shipping_unit_price") for item in comprehensive_items):
            from overseas_costing.services import cost_preview_service

            saved_item_rows = frappe.get_all(
                "Overseas Cost Item",
                filters={"batch": batch_doc_name, "version": resolved_version_name},
                fields=["name", "derived_json"],
                limit_page_length=10000,
            )
            summary = cost_preview_service.enrich_saved_unit_prices(summary, saved_item_rows)

    rules = []
    if resolved_version_name:
        rules = frappe.get_all(
            "Overseas Cost Allocation Rule",
            filters={"batch": batch_doc_name, "version": resolved_version_name},
            fields=["name", "rule_code", "expense_category", "allocation_basis", "currency", "amount", "remark", "is_enabled"],
            order_by="priority_no asc, modified asc",
            limit_page_length=1000,
        )

    from .effective_logistics_source import current_source_bundle, json_dict
    bundle = current_source_bundle(batch_doc_name, resolved_version_name)
    if bundle and bundle['context']['root_kind'] == 'expense':
        header['source_context'] = bundle['context']
        adopted = json_dict((bundle.get('version') or {}).get('extra_json')).get('effective_logistics_source') or {}
        header['calculation_stale'] = adopted.get('fingerprint') != bundle['context'].get('fingerprint')
        if header['calculation_stale'] and resolved_version_name == header.get('current_version'):
            # Read projection only; frozen records remain byte-for-byte intact.
            header['status'] = 'Dirty'

    # Detail refreshes must carry the same authoritative remediation projection
    # as queue rows; otherwise a just-returned batch would keep stale header
    # actions until the user leaves the detail page.
    header.update(_build_review_remediation_gate(batch_doc_name, for_update=False))
    header["erp_work"] = _batch_erp_work(batch_doc_name, resolved_version_name)

    return {
        "ok": True,
        "message": "批次详情已返回。",
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "header": header,
        "version": version,
        "summary": summary,
        "allocation_rules": rules,
    }


def _batch_erp_work(batch_doc_name: str, version_name: str) -> dict:
    """详情页要的站点级 ERP 待办；只读账本，不重算分站点计划。

    ``build_site_sync_plan`` 要跑路由与供应商校验，详情页每次打开都跑不起，因此这里只按
    账本能回答的问题作答（哪个站点推过/失败/待核对/需人工），``overall`` 里也不会出现
    需要当前结果哈希才成立的 ``UPDATE_REQUIRED``。要那份完整视图请看 ``preview_site_sync_plan``。
    """

    from overseas_costing.services import fee_status_service
    from overseas_costing.services.erp_sync_ledger_service import list_sync_requests

    try:
        items = list_sync_requests(batch_doc_name, version=version_name or "", limit=200).get("items") or []
    except Exception:  # noqa: BLE001
        # 账本读失败不该让整个详情页打不开：退回「这一块没有信息」，不假装同步过。
        items = []
    return fee_status_service.build_erp_work_state(sites=items)


def get_batch_items(
    batch_name: str,
    version_name: str | None = None,
    filters: dict | None = None,
    keyword: str | None = None,
    customs_no: str | None = None,
    waybill_no: str | None = None,
    material_code: str | None = None,
    product_name: str | None = None,
    import_name: str | None = None,
    hs_code: str | None = None,
    category: str | None = None,
) -> dict:
    query_filters = _normalize_item_query_filters(
        filters,
        keyword=keyword,
        customs_no=customs_no,
        waybill_no=waybill_no,
        material_code=material_code,
        product_name=product_name,
        import_name=import_name,
        hs_code=hs_code,
        category=category,
    )
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，返回空明细。",
            "batch_name": batch_name,
            "version_name": version_name,
            "filters": query_filters,
            "columns": EXCEL_COLUMNS,
            "items": [],
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "batch_name": batch_doc_name, "message": "当前批次没有版本。"}

    fields = list(dict.fromkeys(
        EXTRA_ITEM_FIELDS + _persistent_item_fieldnames(EXCEL_FIELDNAMES) + ["extra_json"]
    ))
    db_filters, or_filters = _build_item_query_args(
        batch_doc_name,
        resolved_version_name,
        query_filters,
    )
    items = frappe.get_all(
        "Overseas Cost Item",
        filters=db_filters,
        or_filters=or_filters,
        fields=fields,
        order_by="row_no asc",
        limit_page_length=0,
    )
    batch_fields = ["transport_mode", "extra_json"]
    if _db_has_column("Overseas Cost Batch", "business_type"):
        batch_fields.insert(0, "business_type")
    batch_header = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        batch_fields,
        as_dict=True,
    ) or {}
    from overseas_costing.services.material_input_service import present_material_row
    items = [present_material_row(item) for item in items]
    from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
    version_row = frappe.db.get_value("Overseas Cost Version", resolved_version_name,
                                      ["extra_json"], as_dict=True) or {}
    packing = project_packing_groups(items, groups_from_version(version_row), strict=False)
    items = packing['items']
    for item in items: item.pop("extra_json", None)
    batch_business_type = _resolve_batch_business_type(batch_header)
    for item in items:
        item["business_type"] = batch_business_type
    return {
        "ok": True,
        "message": "批次明细已返回。",
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "filters": query_filters,
        "columns": EXCEL_COLUMNS,
        "items": items,
        "packing_groups": packing['groups'],
        "total": len(items),
    }


def get_version_list(batch_name: str) -> dict:
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，返回空版本列表。",
            "batch_name": batch_name,
            "items": [],
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    items = frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=[
            "name",
            "version_code",
            "version_type",
            "status",
            "is_current",
            "source_type",
            "fx_usd_to_rmb",
            "fx_rmb_to_mxn",
            "calculated_at",
            "modified",
        ],
        order_by="creation asc",
        limit_page_length=1000,
    )
    return {
        "ok": True,
        "message": "版本列表已返回。",
        "batch_name": batch_doc_name,
        "items": items,
    }


def _normalize_limit(limit, default: int = 80, maximum: int = 300) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


EXPORT_XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT_TEXT_FIELDS = {
    "material_code",
    "product_name",
    "product_name_es",
    "spec_model",
    "unit",
    "recipient",
    "purchase_currency",
    "import_name",
    "hs_code",
    "category",
    "customs_no",
    "waybill_no",
    "container_no",
    "sea_bill_no",
    "commercial_invoice_no",
    "purchase_order_no",
    "project_collection",
    "transport_mode",
    "source_type",
    "source_doc_no",
    "source_file_name",
}
EXPORT_BATCH_FALLBACK_FIELDS = [
    "name",
    "batch_no",
    "customs_no",
    "waybill_no",
    "container_no",
    "sea_bill_no",
    "commercial_invoice_no",
    "transport_mode",
    "business_type",
    "project_collection",
    "source_approval_no",
    "source_instance_id",
    "source_dingtalk_url",
    "source_file_name",
    "current_version",
]
TRANSPORT_MODE_LABELS = {"SEA": "海运", "AIR": "空运", "EXPRESS": "快递"}
EXPORT_MERGE_FIELDNAMES = {
    "customs_no",
    "waybill_no",
    "china_misc_rmb",
    "china_misc_mxn",
    "china_ocean_usd",
    "china_to_mexico_freight_rmb",
    "project_collection",
    "transport_mode",
    "business_type",
}
EXPORT_ZERO_AS_EMPTY_FIELDNAMES = {
    "limpieza_contenedor",
}
EXPORT_MIN_COLUMN_WIDTHS = {
    "material_code": 14,
    "product_name": 28,
    "spec_model": 28,
    "unit": 12,
    "import_name": 34,
    "hs_code": 16,
    "category": 20,
    "customs_no": 24,
    "waybill_no": 24,
    "project_collection": 36,
    "transport_mode": 14,
    "business_type": 24,
}
EXPORT_MAX_COLUMN_WIDTHS = {
    "product_name": 42,
    "spec_model": 42,
    "unit": 16,
    "import_name": 48,
    "category": 34,
    "customs_no": 30,
    "waybill_no": 30,
    "project_collection": 52,
    "transport_mode": 18,
    "business_type": 32,
}
EXPORT_WRAP_FIELDNAMES = {
    "product_name",
    "spec_model",
    "import_name",
    "category",
    "customs_no",
    "waybill_no",
    "project_collection",
    "transport_mode",
    "business_type",
}
EXPORT_HEADER_LABELS = {
    "spec_model": "规格型号\nEspecificación / Modelo",
    "unit_price": "采购单价",
    "quantity": "采购数量\n按采购单位",
    "unit": "单位\nUnidad",
    "waybill_no": "中国到墨西哥\n运单号",
    "china_misc_rmb": "中国运输及\n相关杂费\nRMB",
    "china_misc_mxn": "中国运输及\n相关杂费\n折合MXN PESOS",
    "china_ocean_usd": "中国海运\n(USD)",
    "goods_value_ratio": "分摊比例\n(货值比）",
    "import_tax_total": "IMPUESTOS\n合计清关税费",
    "revalidacion": "REVALIDACION\n文件验证费",
    "maniobras": "MANIOBRAS\n码头操作费",
    "entrega_mercancia": "ENTREGA DE \nMERCANCIA\n配送费用",
    "previo": "PREVIO\n预检费用",
    "service_aa": "Servicios A.A.\n货代服务费",
    "complemento_maniobras": "COMPLEMENTO DE MANIOBRAS\n（操作辅助装置）",
    "desconsolidacion": "DESCONSOLIDACION\n拆箱分货费用",
    "maniobra_falso": "MANIOBRA EN FALSO\n虚假操作费",
    "limpieza_contenedor": "LIMPIEZA DE CONTENEDOR\n集装箱清洁",
    "mexico_customs_mxn": "墨西哥\n清关费用\n(MXN)",
    "mexico_customs_rmb": "墨西哥\n清关费用\n(RMB )",
    "mexico_customs_usd": "墨西哥\n清关费用\n（USD）",
    "mexico_inland_mxn": "墨西哥\n内陆运输\n费用\n（MXN）",
    "mexico_misc_mxn": "墨西哥杂费\n（MXN）",
    "mexico_inland_misc_rmb": "墨西哥\n内陆运输+杂费\n费用\n（RMB）",
    "china_to_mexico_freight_rmb": "中国到\n墨西哥运费\n（RMB）",
    "gross_weight_kg": "货重毛重\n（kg）",
    "weight_ratio": "分摊比例\n（重量比）",
    "freight_alloc_rmb": "中国到墨西哥\n运输费用分摊\nRMB",
    "freight_alloc_mxn": "中国到墨西哥\n运输费用分摊\nMXN PESOS",
    "total_logistics_mxn": "中国到墨西哥\n运输+清关+杂费\nMXN PESOS",
    "alloc_price_mxn": "分摊运输+物流+杂费\n到产品的价格\nMXN PESOS",
    "total_cost_rmb": "综合成本\nRMB",
    "total_unit_rmb": "综合单价\nRMB\n按采购单位",
}
EXPORT_HEADER_COLOR_GROUPS = {
    "tax": {
        "cc_rate",
        "cc_anti_dumping",
        "igi_rate",
        "igi_amount",
        "iva_rate",
        "iva_amount",
        "goods_value_ratio",
        "dta",
        "prv_duty",
        "prv_iva",
    },
    "customs_fee": {
        "import_tax_total",
        "revalidacion",
        "maniobras",
        "muellaje",
        "entrega_mercancia",
        "previo",
        "service_aa",
        "almacenajes",
        "reconocimiento_aduanero",
        "honorarios",
        "complemento_maniobras",
        "desconsolidacion",
        "maniobra_falso",
        "arrastre",
        "patio_regulador",
        "entrega_vacio",
        "limpieza_contenedor",
    },
    "mexico_customs": {"mexico_customs_mxn", "mexico_customs_rmb"},
    "allocation": {"gross_weight_kg", "weight_ratio", "freight_alloc_rmb", "freight_alloc_mxn"},
    "result": {"total_logistics_mxn", "alloc_price_mxn", "total_cost_rmb", "total_unit_rmb"},
}


def _normalize_export_batch_names(batch_names_json) -> list[str]:
    if isinstance(batch_names_json, str):
        text = batch_names_json.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except Exception:
            value = [part.strip() for part in text.split(",")]
    else:
        value = batch_names_json
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        name = str(item or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def _transport_label(value) -> str:
    code = normalize_transport_mode(value)
    return TRANSPORT_MODE_LABELS.get(code, str(value or ""))


BUSINESS_TYPE_LABELS = {
    "SEA_STANDARD": "海运正报正清",
    "SEA_DDP": "海运 DDP（双清包税）",
    "AIR_DDP": "空运 DDP（双清包税）",
    "AIR_STANDARD": "正常空运",
    "EXPRESS": "快递",
}


def _business_type_label(value) -> str:
    return BUSINESS_TYPE_LABELS.get(str(value or "").strip(), str(value or ""))


def _clean_export_filename_part(value: str) -> str:
    text = str(value or "").strip() or "全部"
    for char in '\\/:*?"<>|':
        text = text.replace(char, "_")
    return text


def _export_cell_value(item: dict, batch: dict, column: dict):
    fieldname = column.get("fieldname")
    value = item.get(fieldname)
    if value in (None, ""):
        value = batch.get(fieldname)
    if fieldname == "transport_mode":
        value = _transport_label(value)
    if fieldname == "business_type":
        value = _business_type_label(value)
    if value in (None, ""):
        return ""
    if fieldname in EXPORT_ZERO_AS_EMPTY_FIELDNAMES:
        try:
            if float(value) == 0:
                return ""
        except (TypeError, ValueError):
            pass
    if fieldname in EXPORT_TEXT_FIELDS:
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return int(number) if number.is_integer() else number


def _display_width(value) -> int:
    text = str(value or "")
    return sum(2 if ord(char) > 127 else 1 for char in text)


def _export_column_width(fieldname: str, header: str, values: list) -> int:
    max_width = _display_width(header) + 2
    for value in values[:500]:
        max_width = max(max_width, _display_width(value) + 2)
    min_width = EXPORT_MIN_COLUMN_WIDTHS.get(fieldname, 12)
    max_limit = EXPORT_MAX_COLUMN_WIDTHS.get(fieldname, 40)
    return min(max(max_width, min_width), max_limit)


def _export_header_label(column: dict) -> str:
    fieldname = column.get("fieldname") or ""
    return EXPORT_HEADER_LABELS.get(fieldname) or str(column.get("label") or fieldname or "").strip()


def _export_header_fill_color(fieldname: str) -> str:
    if fieldname in EXPORT_HEADER_COLOR_GROUPS["tax"]:
        return "FF0DF2DF"
    if fieldname in EXPORT_HEADER_COLOR_GROUPS["customs_fee"]:
        return "FFD04FCA"
    if fieldname in EXPORT_HEADER_COLOR_GROUPS["mexico_customs"]:
        return "FFFCC102"
    if fieldname in EXPORT_HEADER_COLOR_GROUPS["allocation"]:
        return "FFFFF3CE"
    if fieldname in EXPORT_HEADER_COLOR_GROUPS["result"]:
        return "FF04B0F1"
    if fieldname in {"project_collection", "transport_mode"}:
        return "FFFFFFFF"
    return "FFFFD966"


def _export_header_font(fieldname: str):
    from openpyxl.styles import Font

    if fieldname in EXPORT_HEADER_COLOR_GROUPS["result"]:
        return Font(name="宋体", size=8, bold=True, color="FFFFFFFF")
    if fieldname in {"project_collection", "transport_mode"}:
        return Font(name="宋体", size=8, bold=True, color="FF000000")
    return Font(name="宋体", size=8, bold=False, color="FF000000")


def _merge_repeated_export_cells(sheet, columns: list[dict], rows: list[list], merge_alignment, cell_border) -> None:
    """把连续相同的整票字段纵向合并，保留 SKU 行级字段逐行展示。"""

    if len(rows) < 2:
        return

    merge_column_indexes = [
        index
        for index, column in enumerate(columns, start=1)
        if column.get("fieldname") in EXPORT_MERGE_FIELDNAMES
    ]
    if not merge_column_indexes:
        return

    for column_index in merge_column_indexes:
        start_row = 2
        previous_value = sheet.cell(row=start_row, column=column_index).value
        for row_index in range(3, len(rows) + 3):
            current_value = sheet.cell(row=row_index, column=column_index).value if row_index <= len(rows) + 1 else None
            if current_value == previous_value:
                continue
            if previous_value not in (None, "") and row_index - start_row > 1:
                sheet.merge_cells(
                    start_row=start_row,
                    start_column=column_index,
                    end_row=row_index - 1,
                    end_column=column_index,
                )
                merged_cell = sheet.cell(row=start_row, column=column_index)
                merged_cell.alignment = merge_alignment
                merged_cell.border = cell_border
            start_row = row_index
            previous_value = current_value


def _build_export_xlsx_content(columns: list[dict], rows: list[list], packing_merge_ranges: list[dict] | None = None) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except Exception as exc:  # pragma: no cover - 真实导出时才需要 openpyxl
        raise RuntimeError("导出 .xlsx 需要安装 openpyxl。") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "综合成本核算"
    sheet.freeze_panes = "C2"

    headers = [_export_header_label(column) for column in columns]
    sheet.append(headers)
    for row in rows:
        sheet.append(row)

    last_column_letter = get_column_letter(len(headers))
    sheet.auto_filter.ref = f"A1:{last_column_letter}{max(sheet.max_row, 1)}"

    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    body_font = Font(name="等线", size=10, bold=False, color="FF000000")
    small_body_font = Font(name="等线", size=8, bold=False, color="FF000000")
    body_alignment = Alignment(horizontal="center", vertical="center")
    center_wrap_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_wrap_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    merged_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_side = Side(style="thin", color="FF000000")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    sheet.row_dimensions[1].height = 53.25
    for column_index, cell in enumerate(sheet[1], start=1):
        fieldname = columns[column_index - 1].get("fieldname") if column_index <= len(columns) else ""
        cell.fill = PatternFill(fill_type="solid", fgColor=_export_header_fill_color(fieldname))
        cell.font = _export_header_font(fieldname)
        cell.alignment = header_alignment
        cell.border = cell_border

    sheet.sheet_view.showGridLines = True
    for row_index, row in enumerate(sheet.iter_rows(min_row=2), start=2):
        sheet.row_dimensions[row_index].height = 20
        for column_index, cell in enumerate(row, start=1):
            fieldname = columns[column_index - 1].get("fieldname") if column_index <= len(columns) else ""
            cell.font = small_body_font if fieldname == "import_name" else body_font
            if fieldname in {"product_name", "project_collection"}:
                cell.alignment = left_wrap_alignment
            elif fieldname in EXPORT_WRAP_FIELDNAMES:
                cell.alignment = center_wrap_alignment
            else:
                cell.alignment = body_alignment
            cell.border = cell_border
            if isinstance(cell.value, (int, float)):
                cell.number_format = '#,##0.######'

    _merge_repeated_export_cells(sheet, columns, rows, merged_alignment, cell_border)
    packing_fields = {"package_count", "packaging_type", "net_weight_kg", "gross_weight_kg", "volume_m3"}
    packing_columns = [index for index, column in enumerate(columns, start=1)
                       if column.get("fieldname") in packing_fields]
    for region in packing_merge_ranges or []:
        start, end = int(region.get("start") or 0) + 2, int(region.get("end") or 0) + 2
        if end <= start:
            continue
        for column_index in packing_columns:
            sheet.merge_cells(start_row=start, start_column=column_index,
                              end_row=end, end_column=column_index)
            cell = sheet.cell(row=start, column=column_index)
            cell.alignment = merged_alignment
            cell.border = cell_border

    for index, header in enumerate(headers, start=1):
        fieldname = columns[index - 1].get("fieldname") if index - 1 < len(columns) else ""
        values = [row[index - 1] if index - 1 < len(row) else "" for row in rows]
        sheet.column_dimensions[get_column_letter(index)].width = _export_column_width(fieldname, header, values)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_current_result_xlsx(batch_names_json=None, transport_label: str | None = None) -> dict:
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能导出真实结果。",
        }

    batch_names = _normalize_export_batch_names(batch_names_json)
    if not batch_names:
        return {"ok": False, "message": "当前没有可导出的批次。"}

    export_rows = []
    packing_merge_ranges = []
    for batch_name in batch_names:
        batch_doc_name = _resolve_batch_name(batch_name)
        if not batch_doc_name:
            continue
        batch_fields = [
            fieldname
            for fieldname in EXPORT_BATCH_FALLBACK_FIELDS
            if fieldname not in {"business_type", "subsidiary_code"}
            or _db_has_column("Overseas Cost Batch", fieldname)
        ]
        if "extra_json" not in batch_fields:
            batch_fields.append("extra_json")
        batch = frappe.db.get_value(
            "Overseas Cost Batch",
            batch_doc_name,
            batch_fields,
            as_dict=True,
        ) or {}
        batch["business_type"] = _resolve_batch_business_type(batch)
        batch["subsidiary_code"] = _resolve_batch_subsidiary_code(batch)
        detail = get_batch_items(
            batch_name=batch_doc_name,
            version_name=batch.get("current_version"),
        )
        if not detail.get("ok"):
            continue
        detail_items = detail.get("items") or []
        group_starts = {}
        for item in detail_items:
            export_item = _effective_purchase_item(item)
            adopted = export_item.get("adopted_price") or {}
            if adopted:
                export_item["unit_price"] = adopted.get("value")
                export_item["purchase_currency"] = adopted.get("currency") or export_item.get("purchase_currency")
                export_item["purchase_price_source"] = (
                    "按货值÷采购数量计算"
                    if adopted.get("source_type") == "purchase_total_derived"
                    else ""
                )
            group = item.get('packing_group') or {}
            if item.get('packing_group_id'):
                if int(item.get('packing_group_position') or 0) == 0:
                    for field in ('package_count','packaging_type','net_weight_kg','gross_weight_kg','volume_m3'):
                        export_item[field] = group.get(field)
                    group_starts[item['packing_group_id']] = len(export_rows)
                else:
                    for field in ('package_count','packaging_type','net_weight_kg','gross_weight_kg','volume_m3'):
                        export_item[field] = None
                    if int(item.get('packing_group_position') or 0) == int(item.get('packing_group_size') or 1) - 1:
                        packing_merge_ranges.append({'start':group_starts.get(item['packing_group_id'], len(export_rows)),
                                                     'end':len(export_rows)})
            export_rows.append([_export_cell_value(export_item, batch, column) for column in EXPORT_COLUMNS])

    if not export_rows:
        return {"ok": False, "message": "当前批次没有可导出的 SKU 明细。"}

    content = _build_export_xlsx_content(EXPORT_COLUMNS, export_rows, packing_merge_ranges)
    label = _clean_export_filename_part(transport_label or "全部")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"海外采购综合成本核算_{label}_{stamp}.xlsx"
    return {
        "ok": True,
        "file_name": file_name,
        "mime_type": EXPORT_XLSX_MIME_TYPE,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "total": len(export_rows),
        "message": f"已生成 {len(export_rows)} 行 SKU 明细。",
    }


WRITEBACK_REQUIRED_ITEM_FIELDS = (
    ("material_code", "物料编码", "text", "钉钉物流 OA 货物信息 / 装箱单解析 / 手工明细补录"),
    ("product_name", "物料名称", "text", "钉钉物流 OA 货物信息 / 装箱单解析 / 手工明细补录"),
    ("quantity", "采购数量", "positive_number", "钉钉物流 OA 货物信息 / 采购支出 OA / 装箱单解析"),
    ("actual_shipped_qty", "出库数量", "positive_number", "装箱单解析 / 手工明细补录"),
    ("unit_price", "采购单价", "positive_number", "采购支出 OA / 商业发票 / 手工明细补录"),
    ("purchase_currency", "采购币种", "text", "采购支出 OA / 商业发票 / 手工明细补录"),
    ("goods_value", "总货值", "positive_number", "采购支出 OA / 商业发票 / 重新试算"),
    ("total_unit_rmb", "综合物品单价RMB", "positive_number", "重新试算生成"),
)

ERP_PAYLOAD_ITEM_FIELDS = list(
    dict.fromkeys(
        [
            "name",
            "row_no",
            "excel_row_no",
            "material_code",
            "product_name",
            "supplier",
            "extra_json",
            "quantity",
            "actual_shipped_qty",
            "actual_shipped_qty_mode", "actual_shipped_qty_source_revision",
            "unit", "purchase_uom", "shipped_uom", "derived_json",
            "unit_price",
            "purchase_currency",
            "goods_value",
            "goods_value_ratio",
            "weight_ratio",
            "gross_weight_kg",
            "volume_m3",
            "chargeable_weight_kg",
            "china_to_mexico_freight_rmb",
            "freight_alloc_rmb",
            "freight_alloc_mxn",
            "total_logistics_mxn",
            "mexico_customs_mxn",
            "mexico_customs_rmb",
            "mexico_customs_usd",
            "import_tax_total",
            "igi_amount",
            "iva_amount",
            "total_cost_rmb",
            "total_unit_rmb",
            "project_collection",
            "subsidiary_code",
            "erp_site_code",
            "route_status",
            "route_revision",
        ]
    )
)


def _as_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


ZERO_FEE_CONFIRMATION_MARKER = "OCW_ZERO_CONFIRMED"


def _has_positive_rule(rules: list[dict], keywords: tuple[str, ...]) -> bool:
    for rule in rules:
        if rule.get("is_enabled") in (0, False, "0") or rule.get("amount_status") == "MISSING":
            continue
        text = " ".join(
            str(rule.get(fieldname) or "")
            for fieldname in ("rule_code", "expense_category", "remark")
        ).lower()
        if _as_float(rule.get("amount")) > 0 and any(keyword.lower() in text for keyword in keywords):
            return True
    return False


def _has_zero_confirmed_rule(rules: list[dict], keywords: tuple[str, ...]) -> bool:
    for rule in rules:
        if rule.get("is_enabled") in (0, False, "0"):
            continue
        text = " ".join(
            str(rule.get(fieldname) or "")
            for fieldname in ("rule_code", "expense_category", "remark")
        ).lower()
        marker_hit = ZERO_FEE_CONFIRMATION_MARKER.lower() in text or rule.get("amount_status") in {"ACTUAL", "NOT_INCURRED", "INCLUDED"}
        keyword_hit = any(keyword.lower() in text for keyword in keywords)
        if marker_hit and keyword_hit and _as_float(rule.get("amount")) == 0:
            return True
    return False


def _has_fee_pool_or_zero_confirmation(rules: list[dict], keywords: tuple[str, ...]) -> bool:
    return _has_positive_rule(rules, keywords) or _has_zero_confirmed_rule(rules, keywords)


def _sum_item_fields(items: list[dict], fieldnames: tuple[str, ...]) -> float:
    return sum(_as_float(item.get(fieldname)) for item in items for fieldname in fieldnames)


def _has_positive_item_value(items: list[dict], fieldnames: tuple[str, ...]) -> bool:
    return _sum_item_fields(items, fieldnames) > 0


def _load_json_value(text):
    if not text:
        return {}
    try:
        return _json.loads(text)
    except Exception:
        return {}


def _round_payload_amount(value, digits: int = 6):
    number = _as_float(value)
    return round(number, digits) if number else 0


def _finite_decimal(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _positive_decimal(value) -> Decimal | None:
    number = _finite_decimal(value)
    return number if number is not None and number > 0 else None


def _effective_purchase_item(item: dict) -> dict:
    """Consume the canonical material projection without changing source evidence."""

    projected = _effective_calculated_item(item)
    from overseas_costing.services.material_input_service import present_material_row

    presented = present_material_row(projected)
    adopted = presented.get("adopted_price") or {}
    adopted_value = _finite_decimal(adopted.get("calculation_value") or adopted.get("value"))
    if adopted_value is None or adopted_value < 0:
        return projected
    return {
        **projected,
        "unit_price": adopted_value,
        "purchase_currency": adopted.get("currency") or projected.get("purchase_currency") or "",
        "unit_price_uom": adopted.get("unit") or projected.get("unit_price_uom") or "",
        "adopted_price": adopted,
    }


def _build_cost_formula(item: dict) -> dict:
    item = _effective_purchase_item(item)
    from overseas_costing.services.material_input_service import present_material_row

    presented = present_material_row(item)
    quantity = _positive_decimal(presented.get("effective_shipping_quantity")) or _positive_decimal(item.get("quantity"))
    unit_price = _positive_decimal(item.get("unit_price")) or Decimal("0")
    shipment_value = _finite_decimal(presented.get("shipment_value_rmb"))
    goods_value = shipment_value if shipment_value is not None and shipment_value >= 0 else unit_price * (quantity or Decimal("0"))
    total_cost = _positive_decimal(item.get("total_cost_rmb")) or Decimal("0")
    allocated_cost = max(total_cost - goods_value, Decimal("0")) if total_cost else Decimal("0")
    logistics_cost = _positive_decimal(item.get("freight_alloc_rmb")) or Decimal("0")
    clearance_tax_cost = max(allocated_cost - logistics_cost, Decimal("0"))
    return {
        "original_unit_price": _round_payload_amount(unit_price),
        "quantity": _round_payload_amount(quantity),
        "goods_value": _round_payload_amount(goods_value),
        "allocated_logistics_cost": _round_payload_amount(logistics_cost),
        "allocated_clearance_tax_cost": _round_payload_amount(clearance_tax_cost),
        "allocated_total_cost": _round_payload_amount(allocated_cost),
        "total_cost": _round_payload_amount(total_cost),
        "comprehensive_unit_price": _round_payload_amount(item.get("total_unit_rmb")),
        "formula": "原始单价 + 单件分摊物流成本 + 单件分摊清关/关税成本 = 综合物品单价",
    }


def _item_expense_detail(item: dict, formula: dict | None = None) -> dict:
    formula = formula or _build_cost_formula(item)
    clearance_tax_total_rmb = _as_float(formula.get("allocated_clearance_tax_cost"))
    import_tax_mxn = _as_float(item.get("import_tax_total"))
    if not import_tax_mxn:
        import_tax_mxn = sum(_as_float(item.get(fieldname)) for fieldname in TAX_COMPONENT_FIELDS)

    calculation_meta = _load_json_value(item.get("derived_json"))
    fx_rmb_to_mxn = _as_float(calculation_meta.get("fx_rmb_to_mxn"))
    tax_alloc_rmb = import_tax_mxn / fx_rmb_to_mxn if import_tax_mxn and fx_rmb_to_mxn else 0.0
    # The total is already used by the cost formula. Split it only for ERP display.
    tax_alloc_rmb = min(tax_alloc_rmb, clearance_tax_total_rmb)
    clearance_alloc_rmb = max(clearance_tax_total_rmb - tax_alloc_rmb, 0.0)

    saved = _saved_expense_classification(item)
    if saved is not None:
        clearance_alloc_rmb = saved["clearance"]
        tax_alloc_rmb = saved["tax"]
        import_tax_mxn = tax_alloc_rmb * fx_rmb_to_mxn
        item = {**item, "import_tax_total": import_tax_mxn, "igi_amount": None, "iva_amount": None,
                "mexico_customs_rmb": clearance_alloc_rmb,
                "mexico_customs_mxn": clearance_alloc_rmb * fx_rmb_to_mxn if fx_rmb_to_mxn else None,
                "mexico_customs_usd": None}

    return {
        "logistics": {
            "freight_alloc_rmb": _round_payload_amount(item.get("freight_alloc_rmb")),
            "freight_alloc_mxn": _round_payload_amount(item.get("freight_alloc_mxn")),
            "total_logistics_mxn": _round_payload_amount(item.get("total_logistics_mxn")),
        },
        "clearance_and_tax": {
            "clearance_alloc_rmb": _round_payload_amount(clearance_alloc_rmb),
            "tax_alloc_rmb": _round_payload_amount(tax_alloc_rmb),
            "import_tax_total_mxn": _round_payload_amount(import_tax_mxn),
            "igi_amount": _round_payload_amount(item.get("igi_amount")),
            "iva_amount": _round_payload_amount(item.get("iva_amount")),
            "mexico_customs_mxn": _round_payload_amount(item.get("mexico_customs_mxn")),
            "mexico_customs_rmb": _round_payload_amount(item.get("mexico_customs_rmb")),
            "mexico_customs_usd": _round_payload_amount(item.get("mexico_customs_usd")),
        },
        "allocation_basis": {
            "goods_value_ratio": _round_payload_amount(item.get("goods_value_ratio")),
            "weight_ratio": _round_payload_amount(item.get("weight_ratio")),
            "gross_weight_kg": _round_payload_amount(item.get("gross_weight_kg")),
            "volume_m3": _round_payload_amount(item.get("volume_m3")),
            "chargeable_weight_kg": _round_payload_amount(item.get("chargeable_weight_kg")),
        },
    }


def _saved_expense_classification(item: dict):
    meta = _load_json_value(item.get("derived_json"))
    if meta.get("calculation_schema") != 2:
        return None
    freight = _as_float(item.get("freight_alloc_rmb"))
    clearance = _as_float(meta.get("mexico_customs_rmb"))
    tax = _as_float(meta.get("tax_allocated_rmb"))
    extras = _as_float(meta.get("direct_fees_rmb")) + _as_float(meta.get("allocated_fees_rmb"))
    return {"freight": freight, "clearance": clearance, "tax": tax,
            "other": round(extras - freight - clearance - tax, 6)}


def _effective_calculated_item(item):
    if _load_json_value(item.get("derived_json")).get("calculation_schema") != 2:
        return item
    from overseas_costing.services.material_input_service import present_material_row
    effective = present_material_row(item).get("effective_shipping") or {}
    return {**item, "actual_shipped_qty": effective.get("quantity")}


def _build_expense_pool_summary(rules: list[dict], items: list[dict]) -> dict:
    normalized_items = []
    for item in items:
        if _saved_expense_classification(item) is not None:
            detail = _item_expense_detail(item)["clearance_and_tax"]
            item = {**item, **{field: detail.get(field) for field in (
                "mexico_customs_rmb", "mexico_customs_mxn", "mexico_customs_usd", "igi_amount", "iva_amount")},
                    "import_tax_total": detail["import_tax_total_mxn"]}
        normalized_items.append(item)
    items = normalized_items
    rule_pools = [
        {
            "rule_code": rule.get("rule_code") or "",
            "expense_category": rule.get("expense_category") or "",
            "amount": _round_payload_amount(rule.get("amount")),
            "currency": rule.get("currency") or "",
            "allocation_basis": rule.get("allocation_basis") or rule.get("basis_field") or "",
            "source": "allocation_rule",
            "remark": rule.get("remark") or "",
        }
        for rule in rules
        if _as_float(rule.get("amount")) > 0 and rule.get("is_enabled") not in (0, False, "0")
    ]
    item_allocations = {
        "logistics_allocated_rmb": _round_payload_amount(_sum_item_fields(items, ("freight_alloc_rmb",))),
        "logistics_allocated_mxn": _round_payload_amount(_sum_item_fields(items, ("freight_alloc_mxn",))),
        "clearance_fee_mxn": _round_payload_amount(_sum_item_fields(items, ("mexico_customs_mxn",))),
        "clearance_fee_rmb": _round_payload_amount(_sum_item_fields(items, ("mexico_customs_rmb",))),
        "clearance_fee_usd": _round_payload_amount(_sum_item_fields(items, ("mexico_customs_usd",))),
        "tariff_tax_total": _round_payload_amount(_sum_item_fields(items, ("import_tax_total", "igi_amount", "iva_amount"))),
    }
    return {
        "rules": rule_pools,
        "item_allocations": item_allocations,
        "allocation_basis_fields": sorted(
            {
                rule.get("allocation_basis") or rule.get("basis_field")
                for rule in rules
                if rule.get("allocation_basis") or rule.get("basis_field")
            }
        ),
    }


def _resolve_payload_supplier(items: list[dict]) -> str:
    suppliers = []
    for item in items:
        supplier = str(item.get("supplier") or "").strip()
        if supplier and supplier not in suppliers:
            suppliers.append(supplier)
    return suppliers[0] if len(suppliers) == 1 else ""


def _build_writeback_item_quality(items: list[dict]) -> dict:
    issue_counts = {fieldname: 0 for fieldname, _label, _rule, _source in WRITEBACK_REQUIRED_ITEM_FIELDS}
    issue_examples = []

    for index, item in enumerate(items, start=1):
        item = _effective_purchase_item(item)
        item_missing_labels = []
        item_missing_fieldnames = []
        for fieldname, label, rule, _source in WRITEBACK_REQUIRED_ITEM_FIELDS:
            value = item.get(fieldname)
            has_issue = _is_blank(value) if rule == "text" else _as_float(value) <= 0
            if has_issue:
                issue_counts[fieldname] += 1
                item_missing_labels.append(label)
                item_missing_fieldnames.append(fieldname)

        if item_missing_labels and len(issue_examples) < 5:
            issue_examples.append(
                {
                    "item_name": item.get("name") or "",
                    "row_no": item.get("row_no") or item.get("excel_row_no") or index,
                    "material_code": item.get("material_code") or "",
                    "product_name": item.get("product_name") or "",
                    "missing_fields": item_missing_labels,
                    "missing_fieldnames": item_missing_fieldnames,
                }
            )

    checks = {
        f"items_have_{fieldname}": count == 0
        for fieldname, count in issue_counts.items()
    }
    blocking_reasons = [
        f"有 {count} 条 SKU 缺少或未填有效的{label}。"
        for fieldname, label, _rule, _source in WRITEBACK_REQUIRED_ITEM_FIELDS
        if (count := issue_counts[fieldname]) > 0
    ]

    return {
        "checks": checks,
        "issue_counts": issue_counts,
        "issue_examples": issue_examples,
        "blocking_reasons": blocking_reasons,
    }


def _build_writeback_field_gaps(batch: dict, items: list[dict], rules: list[dict], resolved_version_name: str | None) -> dict:
    item_quality = _build_writeback_item_quality(items)
    actual_total_cost = _as_float(batch.get("actual_total_cost_rmb"))
    estimated_total_cost = _as_float(batch.get("estimated_total_cost_rmb"))
    total_cost = _current_cost_total(batch)

    batch_gaps = []
    if not _resolve_batch_subsidiary_code(batch):
        batch_gaps.append(
            {
                "scope": "batch",
                "fieldname": "subsidiary_code",
                "label": "业务主体",
                "missing_count": 1,
                "source_hint": "钉钉国际物流 OA 业务主体字段",
                "suggestion": "从钉钉表单补取业务主体后再确认。",
            }
        )
    if not (resolved_version_name or batch.get("current_version")):
        batch_gaps.append(
            {
                "scope": "batch",
                "fieldname": "current_version",
                "label": "当前版本",
                "missing_count": 1,
                "source_hint": "综合单价重算结果",
                "suggestion": "先完成重新试算，再执行人工校验。",
            }
        )
    if total_cost <= 0:
        batch_gaps.append(
            {
                "scope": "batch",
                "fieldname": "total_cost_rmb",
                "label": "综合成本",
                "missing_count": 1,
                "source_hint": "费用池 + 分摊结果",
                "suggestion": "先补齐费用池或重新试算。",
            }
        )

    rule_gaps = []
    pool_needles = [
        (("freight", "logistics", "运输", "运费", "海运", "物流"), "国际运费", "钉钉物流 OA / 货代账单 / 费用清单"),
        (("clearance", "customs", "清关", "报关", "货代"), "清关费", "清关资料 / 费用清单"),
        (("tariff", "duty", "tax", "关税", "税费", "igi", "iva"), "关税", "完税凭证 / 税费资料"),
    ]
    for keywords, label, source_hint in pool_needles:
        if (batch.get("summary_snapshot") or {}).get("calculation_schema") == 2:
            break
        has_pool = _has_fee_pool_or_zero_confirmation(rules, keywords)
        if not has_pool:
            rule_gaps.append(
                {
                    "scope": "rule",
                    "fieldname": label,
                    "label": label,
                    "missing_count": 1,
                    "source_hint": source_hint,
                    "suggestion": f"补充{label}费用池或分摊规则后再确认。",
                }
            )

    item_gaps = []
    for fieldname, label, rule, source in WRITEBACK_REQUIRED_ITEM_FIELDS:
        count = item_quality["issue_counts"].get(fieldname, 0)
        if count <= 0:
            continue
        examples = [
            example
            for example in item_quality["issue_examples"]
            if fieldname in (example.get("missing_fieldnames") or [])
        ]
        item_gaps.append(
            {
                "scope": "item",
                "fieldname": fieldname,
                "label": label,
                "missing_count": count,
                "source_hint": source,
                "missing_fieldnames": [fieldname],
                "suggestion": f"{label} 缺失 {count} 行，建议从 {source} 补齐后重新试算。",
            }
        )

    return {
        "batch": batch_gaps,
        "rules": rule_gaps,
        "items": item_gaps,
        "missing_total": len(batch_gaps) + len(rule_gaps) + len(item_gaps),
    }


def _current_cost_total(batch: dict) -> float:
    snapshot = batch.get("summary_snapshot") or {}
    if snapshot.get("calculation_schema") == 2:
        return _as_float(snapshot.get("total_cost_rmb"))
    return _as_float(batch.get("actual_total_cost_rmb") or batch.get("estimated_total_cost_rmb"))


def _non_final_fee_reasons(rules: list[dict]) -> list[str]:
    """返回尚不能进入 ERP 的启用费用项；暂估可核算但不可推送。"""

    reasons = []
    for rule in rules or []:
        if rule.get("is_enabled") in (0, False, "0") or rule.get("is_active") in (0, False, "0"):
            continue
        status = str(rule.get("amount_status") or "MISSING").strip().upper()
        if status not in {"ACTUAL", "NOT_INCURRED", "INCLUDED"}:
            label = str(rule.get("expense_category") or rule.get("rule_code") or "未命名费用").strip()
            reasons.append(f"{label}尚未取得实际费用或确认结果（当前：{status}）。")
    return reasons


def _comprehensive_readiness(batch, items, rules, version_name, *, for_writeback=False, require_batch_subsidiary=True):
    snapshot = batch["summary_snapshot"]
    result = snapshot.get("comprehensive_cost") or {}
    gaps = _build_writeback_field_gaps(batch, items, rules, version_name)
    quality = _build_writeback_item_quality(items)
    invalid = _build_invalid_business_state(batch, items)
    missing = []
    if invalid.get("invalid"):
        missing.append(invalid.get("message") or "当前批次审批已被排除。")
    if require_batch_subsidiary and not _resolve_batch_subsidiary_code(batch):
        missing.append("当前批次缺少归属业务主体。")
    dirty = batch.get("status") == "Dirty"
    if dirty:
        missing.append("当前批次结果待更新，请重新试算。")
    if not items:
        missing.append("当前批次没有 SKU 明细。")
    if _current_cost_total(batch) <= 0:
        missing.append("当前批次没有有效综合成本结果。")
    formal_confirmation_blocked = bool(snapshot.get("formal_confirmation_blocked"))
    if formal_confirmation_blocked:
        missing.append("当前结果使用暂行分摊口径，可预览和导出，但不能确认正式成本或推送 ERP。")
    for reason in result.get("incomplete_reasons") or []:
        label = next((r.get("expense_category") for r in result.get("excluded_fees", []) if r.get("fee_key") == reason.get("fee_key")), "")
        message = (label + "：" if label else "") + (reason.get("message") or "计算资料不完整。")
        if message not in missing:
            missing.append(message)
        if label:
            gaps["rules"].append({"scope": "rule", "fieldname": reason.get("fee_key"), "label": label,
                                  "missing_count": 1, "suggestion": message, "source_hint": "资料与费用"})
    missing.extend(quality["blocking_reasons"])
    if for_writeback and batch.get("confirm_status") != "Confirmed":
        missing.append("当前批次还没有确认。")
    if for_writeback:
        missing.extend(_non_final_fee_reasons(rules))
    known = {fee.get("fee_key") for key in ("included_fees", "ignored_fees") for fee in result.get(key, [])}
    checks = {"batch_exists": True, "has_current_version": bool(version_name),
              "has_subsidiary_code": bool(_resolve_batch_subsidiary_code(batch)), "has_dirty_data": dirty,
              "has_invalid_business_approval": bool(invalid.get("invalid")), "has_items": bool(items),
              "has_total_cost": _current_cost_total(batch) > 0,
              "has_formal_confirmation_block": formal_confirmation_blocked,
              "all_cost_fees_final": not _non_final_fee_reasons(rules),
              "has_international_freight": any(str(k).startswith("international_") for k in known),
              "has_clearance_fee": "customs_clearance_fee" in known, "has_tariff": "import_tax" in known,
              "is_confirmed": batch.get("confirm_status") == "Confirmed", **quality["checks"]}
    gaps["missing_total"] = sum(len(gaps[key]) for key in ("batch", "rules", "items"))
    return {"ready": not missing, "checks": checks, "blocking_reasons": missing, "warning_reasons": [],
            "field_gaps": gaps, "item_issue_counts": quality["issue_counts"], "item_issue_examples": quality["issue_examples"],
            "invalid_business": invalid, "item_count": len(items), "total_cost_rmb": _current_cost_total(batch),
            "expense_pools": _build_expense_pool_summary([r for r in rules if r.get("is_enabled") not in (0, False, "0")], items),
            "message": "校验通过。" if not missing else "；".join(missing)}


def _build_calculation_confirmation_readiness(
    batch: dict,
    items: list[dict],
    rules: list[dict],
    resolved_version_name: str | None,
) -> dict:
    if (batch.get("summary_snapshot") or {}).get("calculation_schema") == 2:
        return _comprehensive_readiness(
            batch,
            items,
            rules,
            resolved_version_name,
            require_batch_subsidiary=False,
        )
    item_quality = _build_writeback_item_quality(items)
    field_gaps = _build_writeback_field_gaps(batch, items, rules, resolved_version_name)
    actual_total_cost = _as_float(batch.get("actual_total_cost_rmb"))
    estimated_total_cost = _as_float(batch.get("estimated_total_cost_rmb"))
    total_cost = actual_total_cost or estimated_total_cost
    recorded_item_count = int(_as_float(batch.get("item_count")))
    actual_item_count = len(items)
    invalid_business_state = _build_invalid_business_state(batch, items)

    final_scopes = {scope.strip() for rule in rules if rule.get("is_final") and rule.get("is_enabled", 1) and rule.get("is_active", 1) for scope in str(rule.get("covered_scopes") or "").split(",")}
    has_international_freight = ("freight" in final_scopes) or _has_positive_rule(
        rules,
        ("freight", "logistics", "运输", "运费", "海运", "物流"),
    ) or _has_positive_item_value(
        items,
        ("china_to_mexico_freight_rmb", "freight_alloc_rmb", "freight_alloc_mxn"),
    )
    has_clearance_fee = ("customs" in final_scopes) or _has_positive_rule(
        rules,
        ("clearance", "customs", "清关", "报关", "货代"),
    ) or _has_zero_confirmed_rule(
        rules,
        ("clearance", "customs", "清关", "报关", "货代"),
    ) or _has_positive_item_value(
        items,
        ("mexico_customs_mxn", "mexico_customs_rmb", "mexico_customs_usd"),
    )
    has_tariff = ("tax" in final_scopes) or _has_positive_rule(
        rules,
        ("tariff", "duty", "tax", "关税", "税费", "igi", "iva"),
    ) or _has_zero_confirmed_rule(
        rules,
        ("tariff", "duty", "tax", "关税", "税费", "igi", "iva"),
    ) or _has_positive_item_value(
        items,
        ("import_tax_total", "igi_amount", "iva_amount"),
    )

    checks = {
        "batch_exists": True,
        "has_current_version": bool(resolved_version_name or batch.get("current_version")),
        "has_subsidiary_code": bool(_resolve_batch_subsidiary_code(batch)),
        "has_invalid_business_approval": bool(invalid_business_state.get("invalid")),
        "has_dirty_data": batch.get("status") == "Dirty",
        "has_items": actual_item_count > 0,
        "has_total_cost": total_cost > 0,
        "has_international_freight": has_international_freight,
        "has_clearance_fee": has_clearance_fee,
        "has_tariff": has_tariff,
        **item_quality["checks"],
    }

    from overseas_costing.services.logistics_settlement.runtime import calculation_blockers
    blocking_reasons = calculation_blockers(batch.get("name") or "", resolved_version_name)
    if checks["has_invalid_business_approval"]:
        blocking_reasons.append(invalid_business_state.get("message") or "当前批次存在已拒绝/撤销/终止审批，不进入综合成本确认或 ERP 推送。")
    if not checks["has_current_version"]:
        blocking_reasons.append("当前批次没有当前版本。")
    if checks["has_dirty_data"]:
        blocking_reasons.append("当前批次存在未重新计算的数据。")
    if not checks["has_items"]:
        blocking_reasons.append("当前批次没有 SKU 明细。")
    if not checks["has_total_cost"]:
        blocking_reasons.append("当前批次没有可确认的综合成本结果。")
    if not checks["has_international_freight"]:
        blocking_reasons.append("当前批次缺少国际运费费用池或分摊结果。")
    if not checks["has_clearance_fee"]:
        blocking_reasons.append("当前批次缺少清关费费用池或分摊结果。")
    if not checks["has_tariff"]:
        blocking_reasons.append("当前批次缺少关税费用池或分摊结果。")
    blocking_reasons.extend(item_quality["blocking_reasons"])

    warning_reasons = []
    if recorded_item_count and recorded_item_count != actual_item_count:
        warning_reasons.append(f"批次记录明细数为 {recorded_item_count}，实际查询到 {actual_item_count} 条。")
    if estimated_total_cost > 0 and actual_total_cost <= 0:
        warning_reasons.append("当前只有系统计算成本，尚无凭证后的实际总成本。")

    ready = not blocking_reasons
    return {
        "ready": ready,
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "item_issue_counts": item_quality["issue_counts"],
        "item_issue_examples": item_quality["issue_examples"],
        "field_gaps": field_gaps,
        "invalid_business": invalid_business_state,
        "item_count": actual_item_count,
        "total_cost_rmb": total_cost,
        "expense_pools": _build_expense_pool_summary(rules, items),
        "message": "允许确认计算结果。" if ready else "当前批次暂不满足计算结果确认条件：" + "；".join(blocking_reasons),
    }


def _build_writeback_readiness(
    batch: dict,
    items: list[dict],
    resolved_version_name: str | None,
    rules: list[dict] | None = None,
    *,
    require_batch_subsidiary: bool = True,
) -> dict:
    if (batch.get("summary_snapshot") or {}).get("calculation_schema") == 2:
        return _comprehensive_readiness(
            batch,
            items,
            rules or [],
            resolved_version_name,
            for_writeback=True,
            require_batch_subsidiary=require_batch_subsidiary,
        )
    rules = rules or []
    item_quality = _build_writeback_item_quality(items)
    field_gaps = _build_writeback_field_gaps(batch, items, rules, resolved_version_name)
    actual_total_cost = _as_float(batch.get("actual_total_cost_rmb"))
    estimated_total_cost = _as_float(batch.get("estimated_total_cost_rmb"))
    total_cost = actual_total_cost or estimated_total_cost
    recorded_item_count = int(_as_float(batch.get("item_count")))
    actual_item_count = len(items)
    invalid_business_state = _build_invalid_business_state(batch, items)

    checks = {
        "batch_exists": True,
        "has_current_version": bool(resolved_version_name or batch.get("current_version")),
        "has_subsidiary_code": bool(_resolve_batch_subsidiary_code(batch)),
        "has_invalid_business_approval": bool(invalid_business_state.get("invalid")),
        "is_confirmed": batch.get("confirm_status") == "Confirmed",
        "has_dirty_data": batch.get("status") == "Dirty",
        "has_items": actual_item_count > 0,
        "has_total_cost": total_cost > 0,
        "all_cost_fees_final": not _non_final_fee_reasons(rules),
        **item_quality["checks"],
    }

    from overseas_costing.services.logistics_settlement.runtime import calculation_blockers
    blocking_reasons = calculation_blockers(batch.get("name") or "", resolved_version_name)
    if checks["has_invalid_business_approval"]:
        blocking_reasons.append(invalid_business_state.get("message") or "当前批次存在已拒绝/撤销/终止审批，不进入综合成本确认或 ERP 推送。")
    if not checks["has_current_version"]:
        blocking_reasons.append("当前批次没有当前版本。")
    if require_batch_subsidiary and not checks["has_subsidiary_code"]:
        blocking_reasons.append("当前批次缺少归属业务主体。")
    if not checks["is_confirmed"]:
        blocking_reasons.append("当前批次还没有确认。")
    if checks["has_dirty_data"]:
        blocking_reasons.append("当前批次存在未重新计算的数据。")
    if not checks["has_items"]:
        blocking_reasons.append("当前批次没有 SKU 明细。")
    if not checks["has_total_cost"]:
        blocking_reasons.append("当前批次没有可回写的综合成本结果。")
    blocking_reasons.extend(_non_final_fee_reasons(rules))
    blocking_reasons.extend(item_quality["blocking_reasons"])

    warning_reasons = []
    if recorded_item_count and recorded_item_count != actual_item_count:
        warning_reasons.append(f"批次记录明细数为 {recorded_item_count}，实际查询到 {actual_item_count} 条。")
    if estimated_total_cost > 0 and actual_total_cost <= 0:
        warning_reasons.append("当前只有系统计算成本，尚无凭证后的实际总成本。")

    ready = not blocking_reasons
    return {
        "ready": ready,
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "item_issue_counts": item_quality["issue_counts"],
        "item_issue_examples": item_quality["issue_examples"],
        "field_gaps": field_gaps,
        "invalid_business": invalid_business_state,
        "item_count": actual_item_count,
        "total_cost_rmb": total_cost,
        "message": "允许回写。" if ready else "当前批次暂不满足回写条件：" + "；".join(blocking_reasons),
    }


def build_erp_payload_item(
    item: dict,
    *,
    subsidiary_code: str = "",
    business_type: str = "",
) -> dict:
    """构造单条 ERP 报文行。

    行级报文字段只有这一处真源：批量推送与分站点分组推送共用，
    否则分组链路会发出缺 ``cost_formula`` 的行，导致采购单数量/单价归零。
    """

    row = _effective_purchase_item(item)
    formula = _build_cost_formula(row)
    return {
        "subsidiary_code": subsidiary_code or row.get("subsidiary_code") or "",
        "business_type": business_type or row.get("business_type") or "",
        "material_code": row.get("material_code") or "",
        "material_name": row.get("product_name") or "",
        "supplier": row.get("supplier") or "",
        "original_unit_price": formula["original_unit_price"],
        "purchase_currency": row.get("purchase_currency") or "",
        "adopted_price": row.get("adopted_price") or {},
        "comprehensive_unit_price": formula["comprehensive_unit_price"],
        "outbound_quantity": _round_payload_amount(row.get("actual_shipped_qty")),
        "source_quantity": _round_payload_amount(row.get("quantity")),
        "cost_formula": formula,
        "expense_detail": _item_expense_detail(row, formula),
    }


def _build_erp_push_payload(
    batch: dict,
    version: dict,
    items: list[dict],
    rules: list[dict],
    readiness: dict,
) -> dict:
    subsidiary_code = _resolve_batch_subsidiary_code(batch)
    supplier = _resolve_payload_supplier(items)
    payload_items = [
        build_erp_payload_item(
            item,
            subsidiary_code=subsidiary_code,
            business_type=batch.get("business_type") or "",
        )
        for item in items
    ]

    return {
        "target_system": "DeepLinkERP",
        "payload_version": "overseas_cost.v1",
        "batch_name": batch.get("name") or "",
        "batch_no": batch.get("batch_no") or batch.get("name") or "",
        "version_name": version.get("name") or "",
        "version_code": version.get("version_code") or "",
        "subsidiary_code": subsidiary_code,
        "business_type": batch.get("business_type") or "",
        "supplier": supplier,
        "item_count": len(payload_items),
        "total_cost_rmb": _round_payload_amount(readiness.get("total_cost_rmb")),
        "expense_pools": _build_expense_pool_summary(rules, items),
        "items": payload_items,
    }


def _insert_batch_audit_log(
    batch_doc_name: str,
    version_name: str | None,
    action_type: str,
    field_name: str,
    old_value: str | None = None,
    new_value: str | None = None,
    action_remark: str | None = None,
) -> None:
    frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_doc_name,
            "version": version_name,
            "action_type": action_type,
            "field_name": field_name,
            "old_value": old_value or "",
            "new_value": new_value or "",
            "operator_name": getattr(frappe.session, "user", "") if getattr(frappe, "session", None) else "",
            "action_remark": action_remark or "",
        }
    ).insert(ignore_permissions=True)


def _db_has_column(doctype: str, fieldname: str) -> bool:
    if frappe is None:
        return True
    try:
        if hasattr(frappe.db, "has_column"):
            return bool(frappe.db.has_column(doctype, fieldname))
    except Exception:
        pass
    try:
        return fieldname in set(frappe.db.get_table_columns(doctype))
    except Exception:
        return True


def _load_erp_push_context(batch_name: str, version_name: str | None = None) -> dict:
    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}"}

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    batch_fields = [
        "name",
        "batch_no",
        "status",
        "confirm_status",
        "current_version",
        "transport_mode",
        "item_count",
        "source_type",
        "source_file_name",
        "source_sheet",
        "source_range",
        "source_approval_no",
        "source_instance_id",
        "source_dingtalk_url",
        "source_approval_status",
        "source_attachment_count",
        "source_created_at",
        "estimated_total_cost_rmb",
        "actual_total_cost_rmb",
        "extra_json",
    ]
    if _db_has_column("Overseas Cost Batch", "subsidiary_code"):
        batch_fields.insert(3, "subsidiary_code")
    if _db_has_column("Overseas Cost Batch", "business_type"):
        batch_fields.insert(4, "business_type")
    batch = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        batch_fields,
        as_dict=True,
    ) or {"name": batch_doc_name}
    batch["business_type"] = _resolve_batch_business_type(batch)
    batch["subsidiary_code"] = _resolve_batch_subsidiary_code(batch)
    version = {}
    rules = []
    if resolved_version_name:
        version = frappe.db.get_value(
            "Overseas Cost Version",
            resolved_version_name,
            [
                "name",
                "batch",
                "version_code",
                "version_type",
                "status",
                "fx_usd_to_rmb",
                "fx_rmb_to_mxn",
                "calculated_at",
                "extra_json",
                "rule_snapshot_json",
                "summary_snapshot_json",
            ],
            as_dict=True,
        ) or {"name": resolved_version_name}
        from overseas_costing.services import fee_service
        rules = frappe.get_all(
            "Overseas Cost Allocation Rule",
            filters={"batch": batch_doc_name, "version": resolved_version_name},
            fields=fee_service._rule_fields(),
            order_by="priority_no asc, modified asc",
            limit_page_length=1000,
        )
        if not rules:
            rules = _load_json_value(version.get("rule_snapshot_json"))
            if not isinstance(rules, list):
                rules = []

    batch["summary_snapshot"] = _load_json(version.get("summary_snapshot_json"))

    item_filters = {"batch": batch_doc_name, "is_excluded": 0}
    if resolved_version_name:
        item_filters["version"] = resolved_version_name
    from overseas_costing.services import cost_preview_service, cost_review_service
    item_fields = list(dict.fromkeys([
        *ERP_PAYLOAD_ITEM_FIELDS,
        *cost_preview_service.COST_INPUT_FIELDS,
        *cost_review_service.SAVED_ITEM_OUTPUT_FIELDS,
    ]))
    items = frappe.get_all(
        "Overseas Cost Item",
        filters=item_filters,
        fields=item_fields,
        order_by="row_no asc",
        limit_page_length=10000,
    )
    evidence = []
    fee_components = []
    if resolved_version_name:
        filters = {"batch": batch_doc_name, "version": resolved_version_name}
        evidence = frappe.get_all(
            "Overseas Cost Fee Evidence",
            filters=filters,
            fields=["batch", "version", "fee_rule", "evidence_role", "validation_status"],
            limit_page_length=0,
        )
        fee_components = frappe.get_all(
            "Overseas Cost Fee SKU Component",
            filters={**filters, "status": "CONFIRMED", "is_active": 1},
            fields=[
                "name", "batch", "version", "fee_rule", "logical_fee_key", "evidence", "attachment",
                "item", "stable_line_key", "component_type", "tax_code", "hs_code", "currency",
                "original_amount", "amount_rmb", "exchange_rate", "allocation_basis",
                "source_evidence_json", "accounting_role", "cost_effect", "reverses_component",
                "status", "is_active",
            ],
            limit_page_length=0,
        )
    _attach_batch_source_status([batch])
    from overseas_costing.services.effective_source_values import batch_source_context
    source_context = batch_source_context(batch_doc_name, resolved_version_name)
    return {
        "ok": True,
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "batch": batch,
        "version": version,
        "rules": rules,
        "items": items,
        "evidence": evidence,
        "fee_components": fee_components,
        "source_context": source_context,
    }


def _build_authoritative_review_readiness(context: dict) -> dict:
    """Use the same canonical evaluator that classifies the cost-review queue."""

    from overseas_costing.services import cost_review_service

    return cost_review_service.evaluate_review_readiness(
        batch=context["batch"],
        version=context["version"],
        items=context["items"],
        fees=context["rules"],
        evidence=context.get("evidence") or [],
        fee_components=context.get("fee_components") or [],
        source_context=context.get("source_context") or {},
    )


def _build_review_remediation_gate(batch_name: str, *, for_update=False) -> dict:
    """Read the active remediation gate after Batch/Version locks are acquired."""

    sql = getattr(getattr(frappe, "db", None), "sql", None) if frappe is not None else None
    if not callable(sql):
        return {
            "remediation_state": "none", "round_name": "", "round_no": 0,
            "issue_count": 0, "unresolved_count": 0, "addressed_count": 0,
            "erp_blocked": False, "blocking_reasons": [],
        }
    from overseas_costing.services.review_communication_service import FrappeReviewRepository, get_review_gate
    return get_review_gate(
        batch_name,
        for_update=for_update,
        repository=FrappeReviewRepository(frappe),
    )


def _lock_confirmation_records(batch_doc_name: str, version_name: str | None) -> str | None:
    """Serialize the readiness check and confirmation writes in one transaction."""

    sql = getattr(getattr(frappe, "db", None), "sql", None)
    if callable(sql):
        sql("select name from `tabOverseas Cost Batch` where name=%s for update", (batch_doc_name,))
    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if resolved_version_name and callable(sql):
        sql("select name from `tabOverseas Cost Version` where name=%s for update", (resolved_version_name,))
    return resolved_version_name


def get_audit_logs(batch_name: str, version_name: str | None = None, limit: int | str = 80) -> dict:
    normalized_limit = _normalize_limit(limit)
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，返回空修改记录。",
            "batch_name": batch_name,
            "version_name": version_name,
            "items": [],
            "total": 0,
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到批次：{batch_name}", "items": [], "total": 0}

    filters = {"batch": batch_doc_name}
    if version_name:
        filters["version"] = version_name

    items = frappe.get_all(
        "Overseas Cost Audit Log",
        filters=filters,
        fields=[
            "name",
            "batch",
            "version",
            "action_type",
            "field_name",
            "row_no",
            "old_value",
            "new_value",
            "operator_name",
            "action_remark",
            "creation",
        ],
        order_by="creation desc",
        limit_page_length=normalized_limit,
    )
    return {
        "ok": True,
        "message": "修改记录已返回。",
        "batch_name": batch_doc_name,
        "version_name": version_name,
        "items": items,
        "total": len(items),
    }


def check_writeback_ready(
    batch_name: str,
    version_name: str | None = None,
    *,
    require_batch_subsidiary: bool = True,
) -> dict:
    if frappe is None:
        blocking_reasons = ["当前未连接 Frappe，不能执行真实回写检查。"]
        return {
            "ok": True,
            "dry_run": True,
            "ready": False,
            "batch_name": batch_name,
            "version_name": version_name,
            "checks": {
                "batch_exists": False,
                "has_current_version": False,
                "is_confirmed": False,
                "has_dirty_data": False,
                "has_items": False,
                "has_total_cost": False,
            },
            "blocking_reasons": blocking_reasons,
            "warning_reasons": [],
            "item_issue_counts": {},
            "item_issue_examples": [],
            "item_count": 0,
            "total_cost_rmb": 0,
            "message": blocking_reasons[0],
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {
            "ok": False,
            "ready": False,
            "batch_name": batch_name,
            "version_name": version_name,
            "checks": {"batch_exists": False},
            "blocking_reasons": [f"未找到批次：{batch_name}"],
            "warning_reasons": [],
            "message": f"未找到批次：{batch_name}",
        }

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    context = _load_erp_push_context(batch_doc_name, resolved_version_name)
    if not context.get("ok"):
        return {**context, "ready": False}
    readiness = _build_writeback_readiness(
        batch=context["batch"],
        items=context["items"],
        resolved_version_name=context["version_name"],
        rules=context["rules"],
        require_batch_subsidiary=require_batch_subsidiary,
    )
    if readiness.get("field_gaps"):
        readiness["field_gaps"]["summary"] = {
            "missing_total": readiness["field_gaps"].get("missing_total", 0),
            "batch_count": len(readiness["field_gaps"].get("batch") or []),
            "rule_count": len(readiness["field_gaps"].get("rules") or []),
            "item_count": len(readiness["field_gaps"].get("items") or []),
        }
    return {
        "ok": True,
        "batch_name": context["batch_doc_name"],
        "version_name": context["version_name"],
        **readiness,
    }


def confirm_calculation_result(batch_name: str, version_name: str | None = None, remark: str | None = None) -> dict:
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "confirmed": False,
            "batch_name": batch_name,
            "version_name": version_name,
            "message": "当前未连接 Frappe，不能真实确认计算结果。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "confirmed": False, "message": f"未找到批次：{batch_name}"}

    from overseas_costing.services.logistics_settlement.runtime import lock_for_final_action, installed
    settlement_issues = lock_for_final_action(batch_doc_name, version_name) if installed() else []
    if settlement_issues:
        return {'ok': False, 'confirmed': False, 'blocking_reasons': settlement_issues, 'message': '；'.join(settlement_issues)}

    locked_version_name = _lock_confirmation_records(batch_doc_name, version_name)
    context = _load_erp_push_context(batch_doc_name, locked_version_name)
    if not context.get("ok"):
        return {**context, "confirmed": False}

    readiness = _build_authoritative_review_readiness(context)
    if readiness.get("review_state") != "ready":
        blocking_reasons = [
            str(row.get("message") or row.get("code") or "").strip()
            for row in readiness.get("review_blockers") or []
            if str(row.get("message") or row.get("code") or "").strip()
        ]
        if readiness.get("review_state") == "confirmed" and not blocking_reasons:
            blocking_reasons.append("当前计算结果已确认，无需重复确认。")
        return {
            "ok": False,
            "confirmed": False,
            "ready": False,
            "batch_name": context["batch_doc_name"],
            "version_name": context["version_name"],
            "blocking_reasons": blocking_reasons,
            "message": "；".join(blocking_reasons) or "当前计算结果尚未进入成本核对。",
            **readiness,
        }

    remediation_gate = _build_review_remediation_gate(batch_doc_name, for_update=True)
    if remediation_gate.get("erp_blocked"):
        blocking_reasons = list(remediation_gate.get("blocking_reasons") or [])
        return {
            "ok": False,
            "confirmed": False,
            "ready": False,
            "batch_name": context["batch_doc_name"],
            "version_name": context["version_name"],
            **readiness,
            **remediation_gate,
            "blocking_reasons": blocking_reasons,
            "message": "；".join(blocking_reasons),
        }

    confirmation_readiness = _build_calculation_confirmation_readiness(
        batch=context["batch"],
        items=context["items"],
        rules=context["rules"],
        resolved_version_name=context["version_name"],
    )
    if not confirmation_readiness.get("ready"):
        review_blocking_reasons = [
            str(row.get("message") or row.get("code") or "").strip()
            for row in readiness.get("review_blockers") or []
            if str(row.get("message") or row.get("code") or "").strip()
        ]
        blocking_reasons = list(dict.fromkeys([
            *review_blocking_reasons,
            *[
                str(reason).strip()
                for reason in confirmation_readiness.get("blocking_reasons") or []
                if str(reason).strip()
            ],
        ]))
        return {
            "ok": False,
            "confirmed": False,
            "batch_name": context["batch_doc_name"],
            "version_name": context["version_name"],
            **readiness,
            **confirmation_readiness,
            "review_state": readiness.get("review_state"),
            "review_blockers": readiness.get("review_blockers") or [],
            "review_warnings": readiness.get("review_warnings") or [],
            "ready": False,
            "blocking_reasons": blocking_reasons,
            "confirmation_readiness": confirmation_readiness,
            "message": "；".join(blocking_reasons) or "当前计算结果尚不满足最终确认条件。",
        }

    old_status = {
        "status": context["batch"].get("status"),
        "confirm_status": context["batch"].get("confirm_status"),
        "version_status": context["version"].get("status"),
    }
    frappe.db.set_value(
        "Overseas Cost Batch",
        context["batch_doc_name"],
        {
            "status": "Confirmed",
            "confirm_status": "Confirmed",
            "is_locked": 1,
            "writeback_status": context["batch"].get("writeback_status") or "Not Started",
        },
        update_modified=True,
    )
    if context["version_name"]:
        frappe.db.set_value(
            "Overseas Cost Version",
            context["version_name"],
            "status",
            "Confirmed",
            update_modified=True,
        )
    _insert_batch_audit_log(
        batch_doc_name=context["batch_doc_name"],
        version_name=context["version_name"],
        action_type="BATCH_EDIT",
        field_name="confirm_status",
        old_value=_json.dumps(old_status, ensure_ascii=False, default=str),
        new_value=_json.dumps({"confirm_status": "Confirmed", "remark": remark or ""}, ensure_ascii=False, default=str),
        action_remark="人工校验通过，确认综合单价计算结果。",
    )
    frappe.db.commit()

    return {
        "ok": True,
        "confirmed": True,
        "ready": True,
        "batch_name": context["batch_doc_name"],
        "version_name": context["version_name"],
        **readiness,
        "confirmation_readiness": confirmation_readiness,
        "review_state": "confirmed",
        "message": "计算结果已确认，可预览并组织 DeepLinkERP 推送报文。",
    }


def preview_erp_payload(batch_name: str, version_name: str | None = None) -> dict:
    """Compatibility API delegating to the canonical per-Company sync planner."""

    from overseas_costing.services.erp_sync_plan_service import preview_site_sync_plan

    plan = preview_site_sync_plan(batch_name, version_name)
    preview = ((plan.get("push_state") or {}).get("preview") or {})
    sites = preview.get("sites") or []
    items = [item for site in sites for group in site.get("groups") or [] for item in group.get("items") or []]
    payload = {
        "target_system": "DeepLinkERP",
        "batch_name": plan.get("batch_name") or batch_name,
        "version_name": plan.get("version_name") or version_name,
        "sites": sites,
        "items": items,
        "item_count": len(items),
        "company_count": len({group.get("subsidiary_code") for site in sites for group in site.get("groups") or [] if group.get("subsidiary_code")}),
        "group_count": sum(len(site.get("groups") or []) for site in sites),
        "total_cost_rmb": preview.get("preview_total_cost_rmb") or 0,
        "allocated_fee_rmb": preview.get("preview_allocated_fee_rmb") or 0,
    }
    return {
        **plan,
        "payload": payload,
        "blocking_reasons": _erp_plan_blocking_messages(plan),
        "message": "已按 ERP 公司生成推送预览。" if plan.get("ready") else "ERP 推送预览存在阻断项。",
    }


def writeback_to_erp(batch_name: str, version_name: str | None = None) -> dict:
    """Compatibility API delegating to the canonical ledger-backed executor."""

    from overseas_costing.services.erp_sync_plan_service import execute_site_sync_plan

    result = execute_site_sync_plan(batch_name, version_name)
    return {**result, "blocking_reasons": _erp_plan_blocking_messages(result)}


def _erp_plan_blocking_messages(result: dict) -> list[str]:
    messages = []
    for reason in result.get("blocking") or result.get("blocking_reasons") or []:
        if isinstance(reason, dict):
            message = str(reason.get("message") or reason.get("code") or "").strip()
        else:
            message = str(reason or "").strip()
        if message and message not in messages:
            messages.append(message)
    return messages
