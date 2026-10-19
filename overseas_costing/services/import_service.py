"""
中文用途：导入服务。

一期这里逐步承接：
1. 主 Excel 导入
2. OA 国际物流单导入
3. 采购支出 OA 回填采购价格
4. 装箱单解析结果回填实际发货物理属性
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import frappe
except Exception:  # pragma: no cover - 本地无 Frappe 环境时保持可导入
    frappe = None

from overseas_costing.services import attachment_parse_service, material_input_service
from overseas_costing.services.transport_service import prepare_item_transport
from overseas_costing.integrations.dingtalk_approval_source import (
    ArchiveIntegrityError,
    ArchiveNotReady,
    MinioArchiveClient,
)
from overseas_costing.utils.dingtalk import build_dingtalk_order_payload, extract_dingtalk_instance_id
from overseas_costing.utils.excel_blocks import (
    select_excel_blocks,
    summarize_excel_blocks,
    to_bool,
)
from overseas_costing.utils.excel_workbook import parse_yuewei_excel_workbook
from overseas_costing.utils.field_mapper import (
    map_oa_row_to_item,
    map_packing_list_row_to_item,
    map_purchase_expense_row_to_item,
    map_yuewei_excel_block_item_to_item,
    normalize_business_type,
    normalize_transport_mode,
    normalize_unit,
)

ITEM_KEY_FIELDS = ("material_code", "product_name", "spec_model")
DEFAULT_FX_RMB_TO_MXN = 2.6
DEFAULT_FX_USD_TO_RMB = round(1 / 0.1393, 6)
DEFAULT_DINGTALK_CORP_ID = "ding144583309b2fb01c35c2f4657eb6378f"
PURCHASE_WRITEBACK_FIELDS = (
    "unit_price",
    "purchase_currency",
    "goods_value",
    "supplier",
)
PURCHASE_ORDER_FIELD_LABELS = {
    "unit_price": "采购单价",
    "purchase_currency": "采购币种",
    "goods_value": "采购货值",
    "supplier": "供应商",
    "source_type": "价格来源",
    "source_file_name": "来源文件",
    "source_attachment_id": "来源附件 ID",
    "source_doc_no": "采购订单号",
    "parse_status": "解析状态",
}
ATTACHMENT_PRICE_PROVENANCE_FIELDS = (
    "source_type",
    "source_file_name",
    "source_attachment_id",
    "source_doc_no",
    "parse_status",
)
PURCHASE_FIELD_LABELS = {
    "unit_price": "单价Precio",
    "purchase_currency": "币种Moneda",
    "goods_value": "总金额Monto Total",
    "supplier": "供应商",
    "source_type": "来源类型",
    "source_doc_no": "来源审批编号",
    "dingtalk_instance_id": "钉钉实例ID",
    "dingtalk_official_url": "钉钉原单链接",
    "parse_status": "解析状态",
}
PACKING_WRITEBACK_FIELDS = (
    "actual_shipped_qty",
    "gross_weight_kg",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "unit_price",
    "purchase_currency",
    "goods_value",
)
PACKING_NUMERIC_ZERO_FILLABLE_FIELDS = {
    "actual_shipped_qty",
    "gross_weight_kg",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "unit_price",
    "goods_value",
}
ATTACHMENT_ROLLBACK_FIELDS = set(PACKING_WRITEBACK_FIELDS).union(ATTACHMENT_PRICE_PROVENANCE_FIELDS)
PACKING_FIELD_LABELS = {
    "actual_shipped_qty": "实际发货数量",
    "gross_weight_kg": "毛重 KG",
    "volume_m3": "体积 m3",
    "volume_weight_kg": "体积重 KG",
    "chargeable_weight_kg": "计费重 KG",
    "unit_price": "单价",
    "purchase_currency": "采购币种",
    "goods_value": "总价 RMB",
    "hs_code": "海关编码",
    "source_type": "来源类型",
    "source_file_name": "来源文件",
    "source_attachment_id": "来源附件 ID",
    "source_doc_no": "来源单号",
    "parse_status": "解析状态",
}
SOURCE_DOCUMENT_MANUAL_TYPES = {
    "purchase_order": {
        "label": "采购订单",
        "attachment_type": "Purchase Order",
        "parse_targets": ["unit_price", "purchase_currency", "goods_value"],
        "next_step": "已登记为采购订单；可查看物料匹配预览，确认后仅补入系统空值。",
    },
    "purchase_price_document": {
        "label": "采购价格资料",
        "attachment_type": "Commercial Invoice",
        "parse_targets": ["unit_price", "purchase_currency", "goods_value"],
        "next_step": "已登记为价格资料来源；后续仅在物料编码、规格和价格明细可匹配时生成补价预览。",
    },
    "customs_declaration": {
        "label": "报关资料",
        "attachment_type": "Customs Declaration",
        "parse_targets": ["pedimento_no", "line_items"],
        "next_step": "用于核对报关单号、海关编码和申报信息，不会写入采购单价。",
    },
    "tax_certificate": {
        "label": "完税凭证",
        "attachment_type": "Tax Certificate",
        "parse_targets": ["pedimento_no", "tax_totals", "paid_total_mxn", "line_items"],
        "next_step": "用于核对最终税费；确认解析结果后，再与当前批次的预估税费进行对比。",
    },
    "logistics_quote": {
        "label": "物流报价",
        "attachment_type": "Logistics Bill",
        "parse_targets": ["logistics_fee", "currency", "bill_total"],
        "next_step": "作为物流费用候选，仍需确认最终报价后才参与成本分摊。",
    },
    "other": {
        "label": "其他资料",
        "attachment_type": "Other",
        "parse_targets": [],
        "next_step": "仅保留原附件与确认记录，当前不参与成本计算。",
    },
}
SOURCE_DOCUMENT_PREVIEW_CACHE_KEY = "source_document_preview"
DINGTALK_ENV_FILE_CANDIDATES = (
    "/mnt/e/Yuewei开发/预算管理系统/dingtalk-expense-sync-main/.env",
    "/mnt/e/Yuewei开发/预算管理系统/dingtalk-budget-main/server/.env",
    "/mnt/e/Yuewei开发/dingtalk-expense-sync-main/.env",
    "/mnt/e/Yuewei开发/dingtalk-budget-main/server/.env",
    "E:/Yuewei开发/预算管理系统/dingtalk-expense-sync-main/.env",
    "E:/Yuewei开发/预算管理系统/dingtalk-budget-main/server/.env",
    "E:/Yuewei开发/dingtalk-expense-sync-main/.env",
    "E:/Yuewei开发/dingtalk-budget-main/server/.env",
)
NUMERIC_ITEM_FIELDS = {
    "unit_price",
    "quantity",
    "actual_shipped_qty",
    "goods_value",
    "china_misc_rmb",
    "china_misc_mxn",
    "china_ocean_usd",
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
    "mexico_customs_mxn",
    "mexico_customs_rmb",
    "mexico_customs_usd",
    "mexico_inland_mxn",
    "mexico_misc_mxn",
    "mexico_inland_misc_rmb",
    "china_to_mexico_freight_rmb",
    "net_weight_kg",
    "gross_weight_kg",
    "volume_m3",
    "volume_weight_kg",
    "chargeable_weight_kg",
    "weight_ratio",
    "freight_alloc_rmb",
    "freight_alloc_mxn",
    "total_logistics_mxn",
    "alloc_price_mxn",
    "total_cost_rmb",
    "total_unit_rmb",
}
# OA 原始行快照可补回的数值字段；0 视为未填，避免覆盖财务手工录入。
NUMERIC_BACKFILL_FIELDS = (
    "gross_weight_kg",
    "goods_value",
)


def import_main_excel(
    source_name: str,
    source_type: str = "excel",
    transport_mode: str = "",
    source_sheet: str | None = None,
    project_collection: str | None = None,
    version_type: str = "Estimated",
    blocks_json: str | None = None,
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> dict:
    """Import parsed Yuewei Excel blocks into Batch / Version / Item.

    The real Excel parser can feed `blocks_json` later. Until then this keeps a
    stable JSON entry point for the frontend-demo block structure.
    """

    blocks = _load_blocks(blocks_json)
    preview_batches = [_build_excel_block_preview(block) for block in blocks]

    if frappe is None:
        return {
            "ok": True,
            "queued": False,
            "dry_run": True,
            "source_name": source_name,
            "source_type": source_type,
            "transport_mode": transport_mode,
            "source_sheet": source_sheet,
            "project_collection": project_collection,
            "message": "当前未连接 Frappe，已返回 Excel 导入预览结果。",
            "created_batches": [],
            "preview_batches": preview_batches,
        }

    created_batches = []
    resolved_fx_usd_to_rmb = fx_usd_to_rmb or DEFAULT_FX_USD_TO_RMB
    resolved_fx_rmb_to_mxn = fx_rmb_to_mxn or DEFAULT_FX_RMB_TO_MXN

    for block in blocks:
        batch_doc, batch_action = _resolve_or_create_excel_batch(
            block=block,
            source_name=source_name,
            transport_mode=transport_mode,
            source_sheet=source_sheet,
            project_collection=project_collection,
        )
        version_doc, version_action = _resolve_or_create_excel_version(
            batch_doc_name=batch_doc.name,
            version_type=version_type,
            fx_usd_to_rmb=resolved_fx_usd_to_rmb,
            fx_rmb_to_mxn=resolved_fx_rmb_to_mxn,
        )
        frappe.db.set_value(
            "Overseas Cost Batch",
            batch_doc.name,
            "current_version",
            version_doc.name,
            update_modified=False,
        )

        raw_rows = block.get("items") or []
        mapped_rows = [
            map_yuewei_excel_block_item_to_item(block, row, row_index=index + 1)
            for index, row in enumerate(raw_rows)
        ]
        item_result = _upsert_excel_items(
            batch_doc_name=batch_doc.name,
            version_name=version_doc.name,
            mapped_rows=mapped_rows,
            raw_rows=raw_rows,
        )
        rules = _upsert_default_allocation_rules(
            batch_doc_name=batch_doc.name,
            version_name=version_doc.name,
            block=block,
            mapped_rows=mapped_rows,
        )

        created_batch = {
            "batch_name": batch_doc.name,
            "batch_no": batch_doc.batch_no,
            "batch_action": batch_action,
            "version_name": version_doc.name,
            "version_type": version_doc.version_type,
            "version_action": version_action,
            "item_count": item_result["total_count"],
            "created_item_count": item_result["created_count"],
            "updated_item_count": item_result["updated_count"],
            "unchanged_item_count": item_result["unchanged_count"],
            "rule_count": len(rules),
        }

        try:
            from overseas_costing.services.calculate_service import recalculate_batch

            created_batch["recalculate_result"] = recalculate_batch(
                batch_name=batch_doc.name,
                version_name=version_doc.name,
            )
        except Exception as exc:
            created_batch["recalculate_error"] = str(exc)

        created_batches.append(created_batch)

    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "queued": False,
        "source_name": source_name,
        "source_type": source_type,
        "transport_mode": transport_mode,
        "source_sheet": source_sheet,
        "project_collection": project_collection,
        "message": "Excel 主表已生成批次、版本、明细和默认分摊规则。",
        "created_batches": created_batches,
        "preview_batches": preview_batches,
    }


def import_parsed_excel_blocks(
    source_name: str,
    blocks_json: str,
    source_sheet: str | None = None,
    transport_keyword: str = "",
    include_double_clear=False,
    batch_ids: str | None = None,
    limit: int | None = None,
    project_collection: str | None = None,
    version_type: str = "Estimated",
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> dict:
    """导入已经从 Excel 解析出的 block JSON。

    这是从前端 Demo 的 excel-imported-blocks.js 过渡到正式后端导入的桥。
    默认只导入一期普通海运，不导入“海运双清”。
    """

    blocks = _load_blocks(blocks_json)
    selected_blocks = select_excel_blocks(
        blocks,
        source_sheet=source_sheet,
        transport_keyword=transport_keyword,
        include_double_clear=to_bool(include_double_clear),
        batch_ids=batch_ids,
        limit=limit,
    )
    selected_summary = summarize_excel_blocks(selected_blocks)

    result = import_main_excel(
        source_name=source_name,
        source_type="excel",
        transport_mode="",
        source_sheet=source_sheet,
        project_collection=project_collection,
        version_type=version_type,
        blocks_json=json.dumps(selected_blocks, ensure_ascii=False),
        fx_usd_to_rmb=fx_usd_to_rmb,
        fx_rmb_to_mxn=fx_rmb_to_mxn,
    )
    result.update(
        {
            "source_summary": summarize_excel_blocks(blocks),
            "selected_summary": selected_summary,
            "selection": {
                "source_sheet": source_sheet,
                "transport_keyword": transport_keyword,
                "include_double_clear": to_bool(include_double_clear),
                "batch_ids": batch_ids or "",
                "limit": limit,
            },
        }
    )
    return result


def import_yuewei_excel_file(
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    source_sheet: str | None = None,
    transport_keyword: str = "",
    include_double_clear=False,
    batch_ids: str | None = None,
    limit: int | None = None,
    project_collection: str | None = None,
    version_type: str = "Estimated",
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> dict:
    """导入真实 Yuewei 成本总表 xlsx。

    解析层输出与 `excel-imported-blocks.js` 相同的 block JSON，再复用现有落库链路。
    """

    resolved_path = _resolve_excel_file_path(file_path=file_path, file_url=file_url)
    source_sheet = (source_sheet or "").strip() or None
    meta, blocks = parse_yuewei_excel_workbook(resolved_path, sheet_name=source_sheet)
    resolved_source_name = source_name or Path(resolved_path).name
    resolved_source_sheet = meta.get("sourceSheet") or source_sheet

    result = import_parsed_excel_blocks(
        source_name=resolved_source_name,
        blocks_json=json.dumps(blocks, ensure_ascii=False),
        source_sheet=resolved_source_sheet,
        transport_keyword=transport_keyword,
        include_double_clear=include_double_clear,
        batch_ids=batch_ids,
        limit=limit,
        project_collection=project_collection,
        version_type=version_type,
        fx_usd_to_rmb=fx_usd_to_rmb,
        fx_rmb_to_mxn=fx_rmb_to_mxn,
    )
    result.update(
        {
            "parser_meta": meta,
            "file_path": str(resolved_path),
            "file_url": file_url or "",
        }
    )
    return result


def preview_yuewei_excel_file(
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    source_sheet: str | None = None,
    transport_keyword: str = "",
    include_double_clear=True,
    batch_ids: str | None = None,
    limit: int | None = None,
) -> dict:
    """预览真实 xlsx 解析结果，不写入数据库。"""

    resolved_path = _resolve_excel_file_path(file_path=file_path, file_url=file_url)
    source_sheet = (source_sheet or "").strip() or None
    meta, blocks = parse_yuewei_excel_workbook(resolved_path, sheet_name=source_sheet)
    resolved_source_sheet = meta.get("sourceSheet") or source_sheet
    selected_blocks = select_excel_blocks(
        blocks,
        source_sheet=resolved_source_sheet,
        transport_keyword=transport_keyword,
        include_double_clear=include_double_clear,
        batch_ids=batch_ids,
        limit=limit,
    )
    return {
        "ok": True,
        "source_name": source_name or Path(resolved_path).name,
        "file_path": str(resolved_path),
        "file_url": file_url or "",
        "parser_meta": meta,
        "source_summary": summarize_excel_blocks(blocks),
        "selected_summary": summarize_excel_blocks(selected_blocks),
        "preview_batches": [_build_excel_block_preview(block) for block in selected_blocks[:20]],
        "selection": {
            "source_sheet": resolved_source_sheet or "",
            "transport_keyword": transport_keyword or "",
            "include_double_clear": to_bool(include_double_clear),
            "batch_ids": batch_ids or "",
            "limit": limit,
        },
    }


def _batch_identity_map(batch_name: str) -> dict[str, set[str]]:
    """按单号类型返回单批次 Excel 补充允许的标识。"""

    normalized_name = str(batch_name or "").strip()
    if not normalized_name:
        return {}
    if frappe is None:
        return {
            "id": {normalized_name},
            "batchNo": {normalized_name},
            "customsNo": set(),
            "waybillNo": set(),
        }

    from overseas_costing.services import batch_service

    batch_doc_name = batch_service._resolve_batch_name(normalized_name)
    if not batch_doc_name:
        return {}
    row = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        ["name", "batch_no", "customs_no", "waybill_no"],
        as_dict=True,
    ) or {}
    name_values = {
        str(row.get(field) or "").strip()
        for field in ("name", "batch_no")
        if str(row.get(field) or "").strip()
    }
    customs_values = {str(row.get("customs_no") or "").strip()} - {""}
    waybill_values = {str(row.get("waybill_no") or "").strip()} - {""}
    return {
        "id": name_values | customs_values | waybill_values,
        "batchNo": name_values,
        "customsNo": customs_values,
        "waybillNo": waybill_values,
    }


def _batch_identity_values(batch_name: str) -> set[str]:
    """兼容旧调用：返回所有允许的单号。"""

    identity_map = _batch_identity_map(batch_name)
    return set().union(*identity_map.values()) if identity_map else set()


def _excel_block_identity_values(block: dict) -> set[str]:
    return {
        str(block.get(field) or "").strip()
        for field in ("id", "batchNo", "customsNo", "waybillNo")
        if str(block.get(field) or "").strip()
    }


def _excel_block_identity_conflicts(block: dict, identity_map: dict[str, set[str]]) -> list[str]:
    """检查同一 block 中每类非空标识，防止“批次号匹配但运单号属于别的批次”。"""

    conflicts = []
    for fieldname in ("id", "batchNo", "customsNo", "waybillNo"):
        value = str(block.get(fieldname) or "").strip()
        expected = identity_map.get(fieldname) or set()
        if value and value not in expected:
            conflicts.append(f"{fieldname}={value}")
    return conflicts


def _excel_block_identity_label(block: dict) -> str:
    return _first_non_empty(
        block.get("batchNo"),
        block.get("id"),
        block.get("customsNo"),
        block.get("waybillNo"),
        "无法识别批次",
    )


def _prepare_batch_excel_supplement(
    batch_name: str,
    *,
    file_path: str | None = None,
    file_url: str | None = None,
    source_sheet: str | None = None,
) -> dict:
    """解析文件并确认其中所有有数据的 block 都属于显式目标批次。"""

    resolved_path = _resolve_excel_file_path(file_path=file_path, file_url=file_url)
    normalized_sheet = (source_sheet or "").strip() or None
    parser_meta, blocks = parse_yuewei_excel_workbook(resolved_path, sheet_name=normalized_sheet)
    identity_map = _batch_identity_map(batch_name)
    identities = set().union(*identity_map.values()) if identity_map else set()
    if not identities:
        return {"ok": False, "message": f"未找到目标批次：{batch_name}", "foreign_batches": []}

    relevant_blocks = []
    foreign_batches = []
    identity_conflicts = []
    for block in blocks:
        if not (block.get("items") or []):
            continue
        block_identities = _excel_block_identity_values(block)
        block_conflicts = _excel_block_identity_conflicts(block, identity_map)
        if block_conflicts:
            identity_conflicts.extend(block_conflicts)
        if block_conflicts or not block_identities or identities.isdisjoint(block_identities):
            foreign_batches.append(str(_excel_block_identity_label(block)))
            continue
        relevant_blocks.append(block)

    if foreign_batches:
        return {
            "ok": False,
            "message": "文件包含其他批次，未写入任何数据。",
            "batch_name": batch_name,
            "foreign_batches": sorted(set(foreign_batches)),
            "identity_conflicts": sorted(set(identity_conflicts)),
            "matched_block_count": len(relevant_blocks),
        }
    if not relevant_blocks:
        return {
            "ok": False,
            "message": "文件中没有与当前批次匹配的 SKU 数据。",
            "batch_name": batch_name,
            "foreign_batches": [],
        }

    if frappe is None:
        batch_doc_name = str(batch_name)
        version_name = None
    else:
        from overseas_costing.services import batch_service

        batch_doc_name = batch_service._resolve_batch_name(batch_name)
        version_name = batch_service._resolve_version_name(batch_doc_name) if batch_doc_name else None
    if not batch_doc_name:
        return {"ok": False, "message": f"未找到目标批次：{batch_name}", "foreign_batches": []}
    if frappe is not None and not version_name:
        return {"ok": False, "message": "当前批次没有可补充的版本。", "foreign_batches": []}

    raw_rows = []
    mapped_rows = []
    for block in relevant_blocks:
        block_rows = block.get("items") or []
        start_index = len(raw_rows)
        raw_rows.extend(block_rows)
        mapped_rows.extend(
            map_yuewei_excel_block_item_to_item(block, row, row_index=start_index + index + 1)
            for index, row in enumerate(block_rows)
        )

    ignored_fields = {"doctype", "name", "batch", "version", "raw_excel_json"}
    changed_fields = sorted(
        {
            fieldname
            for row in mapped_rows
            for fieldname, value in row.items()
            if fieldname not in ignored_fields and value not in (None, "")
        }
    )
    return {
        "ok": True,
        "message": "已确认文件只包含当前批次，可以安全补充。",
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "version_name": version_name,
        "file_name": Path(resolved_path).name,
        "file_url": file_url or "",
        "source_sheet": parser_meta.get("sourceSheet") or normalized_sheet or "",
        "parser_meta": parser_meta,
        "matched_block_count": len(relevant_blocks),
        "sku_count": len(mapped_rows),
        "changed_fields": changed_fields,
        "conflicts": [],
        "skipped_items": [],
        "foreign_batches": [],
        "mapped_rows": mapped_rows,
        "raw_rows": raw_rows,
    }


def preview_batch_excel_supplement(
    batch_name: str,
    *,
    file_path: str | None = None,
    file_url: str | None = None,
    source_sheet: str | None = None,
) -> dict:
    result = _prepare_batch_excel_supplement(
        batch_name,
        file_path=file_path,
        file_url=file_url,
        source_sheet=source_sheet,
    )
    if result.get("ok") and frappe is not None:
        validation = _validate_batch_supplement_items(
            batch_doc_name=result["batch_doc_name"],
            version_name=result["version_name"],
            mapped_rows=result["mapped_rows"],
        )
        if not validation.get("ok"):
            result.update(validation)
        else:
            result["matched_sku_count"] = len(validation.get("matches") or [])
    preview = dict(result)
    preview.pop("mapped_rows", None)
    preview.pop("raw_rows", None)
    preview.pop("matches", None)
    return preview


_BATCH_SUPPLEMENT_IDENTITY_FIELDS = {
    "doctype",
    "name",
    "batch",
    "version",
    "row_no",
    "excel_row_no",
    "material_code",
    "raw_excel_json",
    "source_type",
    "transport_mode",
}


def _batch_supplement_update_values(mapped_row: dict) -> dict:
    """补充文件只写入显式提供的非标识字段，空值不会清空现有数据。"""

    return {
        fieldname: value
        for fieldname, value in (mapped_row or {}).items()
        if fieldname not in _BATCH_SUPPLEMENT_IDENTITY_FIELDS and value not in (None, "")
    }


def _validate_batch_supplement_items(
    *,
    batch_doc_name: str,
    version_name: str,
    mapped_rows: list[dict],
) -> dict:
    """在预览和应用阶段执行同一套 SKU 唯一匹配校验。"""

    if frappe is None:
        return {"ok": True, "matches": []}
    existing_rows = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": version_name, "is_excluded": 0},
        fields=["name", "row_no", "material_code"],
        limit_page_length=0,
    )
    existing_by_code: dict[str, list[dict]] = defaultdict(list)
    for row in existing_rows:
        code = str(row.get("material_code") or "").strip().casefold()
        if code:
            existing_by_code[code].append(row)

    source_codes: set[str] = set()
    conflicts = []
    matches = []
    for mapped_row in mapped_rows:
        display_code = str(mapped_row.get("material_code") or "").strip()
        code = display_code.casefold()
        if not code:
            conflicts.append("补充行缺少物料编码")
            continue
        if code in source_codes:
            conflicts.append(f"文件中物料编码重复：{display_code}")
            continue
        source_codes.add(code)
        candidates = existing_by_code.get(code) or []
        if not candidates:
            conflicts.append(f"当前批次不存在物料：{display_code}")
            continue
        if len(candidates) > 1:
            conflicts.append(f"当前批次物料编码不唯一：{display_code}")
            continue
        matches.append((mapped_row, candidates[0]))

    if conflicts:
        return {
            "ok": False,
            "message": "物料匹配不唯一或不存在，未写入任何数据。",
            "conflicts": conflicts,
            "matches": [],
        }
    return {"ok": True, "conflicts": [], "matches": matches}


def _apply_batch_supplement_items(
    *,
    batch_doc_name: str,
    version_name: str,
    mapped_rows: list[dict],
    raw_rows: list,
) -> dict:
    """按唯一物料编码定位现有 SKU；不按行号覆盖，也不在补充流程中新建 SKU。"""

    if frappe is None:
        return {
            "ok": True,
            "total_count": len(mapped_rows),
            "created_count": 0,
            "updated_count": 0,
            "unchanged_count": len(mapped_rows),
            "changed_field_count": 0,
        }

    validation = _validate_batch_supplement_items(
        batch_doc_name=batch_doc_name,
        version_name=version_name,
        mapped_rows=mapped_rows,
    )
    if not validation.get("ok"):
        return {
            "ok": False,
            "message": validation.get("message"),
            "conflicts": validation.get("conflicts") or [],
            "total_count": len(mapped_rows),
            "created_count": 0,
            "updated_count": 0,
            "unchanged_count": 0,
            "changed_field_count": 0,
        }
    matches = validation.get("matches") or []

    updated_count = 0
    unchanged_count = 0
    changed_field_count = 0
    for mapped_row, existing in matches:
        values = _filter_doctype_values("Overseas Cost Item", _batch_supplement_update_values(mapped_row))
        changed = _update_item_fields(
            item_name=existing["name"],
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            row_no=existing.get("row_no"),
            field_updates=values,
            action_remark="单批次 Excel 补充",
        )
        if changed:
            updated_count += 1
            changed_field_count += len(changed)
        else:
            unchanged_count += 1

    return {
        "ok": True,
        "total_count": len(matches),
        "created_count": 0,
        "updated_count": updated_count,
        "unchanged_count": unchanged_count,
        "changed_field_count": changed_field_count,
        "conflicts": [],
    }


def apply_batch_excel_supplement(
    batch_name: str,
    *,
    file_path: str | None = None,
    file_url: str | None = None,
    source_sheet: str | None = None,
    expected_modified: str | None = None,
    edit_token: str | None = None,
) -> dict:
    prepared = _prepare_batch_excel_supplement(
        batch_name,
        file_path=file_path,
        file_url=file_url,
        source_sheet=source_sheet,
    )
    if not prepared.get("ok"):
        return prepared

    if frappe is not None:
        from overseas_costing.services import edit_session_service

        edit_session_service.assert_batch_write(
            prepared["batch_doc_name"],
            edit_token=edit_token,
            expected_modified=expected_modified,
        )
        invalid_response = _invalid_batch_cost_write_response(
            prepared["batch_doc_name"],
            prepared["version_name"],
        )
        if invalid_response:
            return invalid_response

    item_result = _apply_batch_supplement_items(
        batch_doc_name=prepared["batch_doc_name"],
        version_name=prepared["version_name"],
        mapped_rows=prepared["mapped_rows"],
        raw_rows=prepared["raw_rows"],
    )
    if not item_result.get("ok"):
        return item_result
    if frappe is not None:
        _mark_batch_dirty(prepared["batch_doc_name"])
        _create_audit_log(
            batch_doc_name=prepared["batch_doc_name"],
            version_name=prepared["version_name"],
            row_no=None,
            field_name="batch_excel_supplement",
            old_value="",
            new_value=f"{item_result.get('updated_count', 0)} SKU / {item_result.get('changed_field_count', 0)} 字段",
            action_remark=f"单批次 Excel 补充：{prepared.get('file_name') or ''}",
        )
        if hasattr(frappe.db, "commit"):
            frappe.db.commit()
    batch_modified = (
        frappe.db.get_value("Overseas Cost Batch", prepared["batch_doc_name"], "modified")
        if frappe is not None
        else None
    )
    return {
        "ok": True,
        "message": f"已补充当前批次 {item_result.get('total_count', 0)} 行 SKU。",
        "batch_name": prepared["batch_doc_name"],
        "version_name": prepared["version_name"],
        "sku_count": item_result.get("total_count", 0),
        "created_count": item_result.get("created_count", 0),
        "updated_count": item_result.get("updated_count", 0),
        "unchanged_count": item_result.get("unchanged_count", 0),
        "failed_count": 0,
        "changed_field_count": item_result.get("changed_field_count", 0),
        "batch_modified": batch_modified,
    }


def upload_attachment(batch_name: str, version_name: str | None = None, file_url: str | None = None) -> dict:
    return {
        "ok": True,
        "queued": True,
        "batch_name": batch_name,
        "version_name": version_name,
        "file_url": file_url,
        "message": "附件登记骨架已创建，后续接 OCR / AI 解析。",
    }


def list_manual_document_attachments(
    batch_name: str,
    logistics_type: str | None = None,
    limit: int | None = 200,
    version_name: str | None = None,
) -> dict:
    """返回人工上传的资料记录，用于资料补齐清单展示。"""

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "items": [],
            "total": 0,
            "message": "当前未连接 Frappe，返回空资料记录。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {
            "ok": False,
            "batch_name": batch_name,
            "items": [],
            "total": 0,
            "message": f"未找到批次：{batch_name}",
        }

    requested_limit = max(1, min(int(limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters={"batch": batch_doc_name, "source_type": "Manual"},
        fields=[
            "name",
            "batch",
            "version",
            "source_type",
            "attachment_type",
            "source_doc_no",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
            "remark",
            "creation",
            "modified",
        ],
        order_by="creation asc",
        limit_page_length=requested_limit,
    )

    resolved_version = _resolve_version_name(batch_doc_name, version_name)
    oa_rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters={"batch": batch_doc_name, "source_type": "OA", "parse_result_json": ["like", '%"settlement_document"%']},
        fields=["name","batch","version","source_type","attachment_type","source_doc_no","file_name","file_url","parse_status","parse_result_json","mapped_result_json","remark","creation","modified"],
        order_by="creation asc", limit_page_length=requested_limit,
    )
    rows.extend(row for row in oa_rows if not row.get('version') or row['version'] == resolved_version)

    from overseas_costing.services.effective_logistics_source import current_source_bundle, attachment_allowed
    source_bundle = current_source_bundle(batch_doc_name,resolved_version)
    logistics_filter = str(logistics_type or "").strip().upper()
    items = []
    for row in rows:
        parse_result = _json_loads_dict(row.get("parse_result_json"))
        manual_meta = parse_result.get("manual_document") if isinstance(parse_result.get("manual_document"), dict) else {}
        row_logistics_type = str(manual_meta.get("logistics_type") or "").strip().upper()
        if logistics_filter and row_logistics_type and row_logistics_type != logistics_filter:
            continue
        items.append(
            {
                "name": row.get("name"),
                "batch": row.get("batch"),
                "version": row.get("version"),
                "source_type": row.get("source_type"),
                "attachment_type": row.get("attachment_type") or "Other",
                "source_doc_no": row.get("source_doc_no") or "",
                "file_name": row.get("file_name") or "",
                "file_url": row.get("file_url") or "",
                "parse_status": row.get("parse_status") or "",
                "slot_code": manual_meta.get("slot_code") or "",
                "slot_label": manual_meta.get("slot_label") or row.get("source_doc_no") or "",
                "logistics_type": row_logistics_type,
                "required": bool(manual_meta.get("required")),
                "audit_only": bool(
                    (
                        source_bundle
                        and source_bundle["context"]["root_kind"] == "expense"
                        and not attachment_allowed(row, source_bundle)
                    )
                    or _settlement_audit_state(parse_result)["audit_only"]
                ),
                # 只有「审批被拒绝/撤销/终止」或「附件被撤销、替代」才叫仅审计；
                # 审批进行中只提示待审批，避免把业务常态说成审计留档。
                "pending_approval": bool(
                    parse_result.get("approval_excluded")
                    or parse_result.get("cost_source_allowed") is False
                    or (row.get("source_type") == "OA" and not row.get("version"))
                ),
                "audit_state": _settlement_audit_state(parse_result).get("mode"),
                "manual_note": manual_meta.get("manual_note") or row.get("remark") or "",
                "remark": row.get("remark") or "",
                "creation": row.get("creation"),
                "modified": row.get("modified"),
            }
        )

    return {
        "ok": True,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "logistics_type": logistics_filter,
        "items": items,
        "total": len(items),
        "message": "人工上传资料记录已返回。",
    }


def register_manual_document_attachment(
    batch_name: str,
    logistics_type: str,
    slot_code: str,
    slot_label: str,
    attachment_type: str | None = None,
    file_url: str | None = None,
    file_name: str | None = None,
    version_name: str | None = None,
    remark: str | None = None,
    manual_note: str | None = None,
    required=0,
) -> dict:
    """登记人工上传资料，只保留来源，不参与自动解析写入。"""

    resolved_file_url = str(file_url or "").strip()
    resolved_slot_code = str(slot_code or "").strip()
    resolved_slot_label = str(slot_label or "").strip() or resolved_slot_code
    resolved_logistics_type = str(logistics_type or "").strip().upper()
    if not resolved_slot_code:
        return {"ok": False, "message": "缺少资料类型，请重新选择上传位置。"}
    if not resolved_logistics_type:
        return {"ok": False, "message": "缺少物流类型，请选择海运、空运或快递。"}

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "file_url": resolved_file_url,
            "message": "当前未连接 Frappe，仅返回资料登记预览。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "batch_name": batch_name, "message": f"未找到批次：{batch_name}"}

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    resolved_file_name = str(file_name or "").strip()
    if not resolved_file_name:
        if resolved_file_url:
            resolved_file_name = Path(resolved_file_url.split("?")[0]).name or resolved_slot_label
        else:
            resolved_file_name = resolved_slot_label
    safe_attachment_type = str(attachment_type or "").strip() or "Other"
    is_required = to_bool(required)
    manual_meta = {
        "manual_document": {
            "slot_code": resolved_slot_code,
            "slot_label": resolved_slot_label,
            "logistics_type": resolved_logistics_type,
            "required": is_required,
            "manual_note": str(manual_note or "").strip(),
            "registered_at": datetime.now().isoformat(timespec="seconds"),
        }
    }
    doc = frappe.get_doc(
        {
            "doctype": "Overseas Cost Attachment",
            "batch": batch_doc_name,
            "version": resolved_version_name,
            "source_type": "Manual",
            "attachment_type": safe_attachment_type,
            "source_doc_no": resolved_slot_label,
            "file_name": resolved_file_name,
            "file_url": resolved_file_url,
            "parse_status": "Draft",
            "parse_result_json": _json_dumps(manual_meta),
            "remark": str(remark or "").strip(),
        }
    ).insert(ignore_permissions=True)
    if resolved_file_url:
        _attach_existing_file_to_attachment(resolved_file_url, doc.name)
    frappe.db.commit()
    try:
        from overseas_costing.services.material_ai_fill_service import schedule_source_ai_review

        schedule_source_ai_review(
            batch_doc_name,
            resolved_version_name,
            trigger_mode="LOCAL_UPLOAD",
        )
    except Exception:
        pass
    return {
        "ok": True,
        "attachment": {
            "name": doc.name,
            "batch": batch_doc_name,
            "version": resolved_version_name,
            "attachment_type": safe_attachment_type,
            "slot_code": resolved_slot_code,
            "slot_label": resolved_slot_label,
            "logistics_type": resolved_logistics_type,
            "file_name": resolved_file_name,
            "file_url": resolved_file_url,
            "required": is_required,
        },
        "message": "资料已登记。",
    }


def delete_manual_document_attachment(attachment_name: str) -> dict:
    resolved_attachment_name = str(attachment_name or "").strip()
    if not resolved_attachment_name:
        return {"ok": False, "message": "缺少资料记录名称。"}

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "attachment_name": resolved_attachment_name,
            "message": "当前未连接 Frappe，仅模拟删除资料记录。",
        }

    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception as exc:
        return {"ok": False, "attachment_name": resolved_attachment_name, "message": f"未找到资料记录：{exc}"}

    if getattr(attachment_doc, "source_type", "") != "Manual":
        return {"ok": False, "attachment_name": resolved_attachment_name, "message": "只能删除人工上传资料记录。"}

    rollback_result = _rollback_manual_document_attachment_parse_effects(attachment_doc)
    from overseas_costing.services import fee_service

    fee_service.unlink_fee_evidence_for_attachment(
        resolved_attachment_name,
        reason="费用凭证附件已删除，保留原关联历史。",
    )
    frappe.delete_doc("Overseas Cost Attachment", resolved_attachment_name, ignore_permissions=True)
    frappe.db.commit()
    rollback_message = ""
    if rollback_result.get("rolled_back_field_count") or rollback_result.get("deleted_item_count"):
        rollback_message = (
            f" 已撤销 {rollback_result.get('rolled_back_field_count', 0)} 个解析字段，"
            f"删除 {rollback_result.get('deleted_item_count', 0)} 条由该附件新增的物料。"
        )
    elif rollback_result.get("skipped_count"):
        rollback_message = " 该附件已有解析记录，但相关数据后续被改动过，未自动覆盖回退。"
    return {
        "ok": True,
        "attachment_name": resolved_attachment_name,
        "rollback": rollback_result,
        "message": f"资料记录已删除。{rollback_message}".strip(),
    }


def _rollback_manual_document_attachment_parse_effects(attachment_doc) -> dict:
    """删除人工补传附件前，撤销该附件解析写入的数据。"""

    attachment_name = str(getattr(attachment_doc, "name", "") or "").strip()
    batch_doc_name = str(getattr(attachment_doc, "batch", "") or "").strip()
    version_name = str(getattr(attachment_doc, "version", "") or "").strip() or None
    mapped_result = _json_loads_dict(getattr(attachment_doc, "mapped_result_json", ""))
    applied_rows = mapped_result.get("applied_rows") if isinstance(mapped_result.get("applied_rows"), list) else []
    created_rows = mapped_result.get("created_rows") if isinstance(mapped_result.get("created_rows"), list) else []

    result = {
        "attachment_name": attachment_name,
        "rolled_back_row_count": 0,
        "rolled_back_field_count": 0,
        "deleted_item_count": 0,
        "skipped_count": 0,
        "skipped_rows": [],
    }
    if frappe is None or not attachment_name:
        return result

    for row in applied_rows:
        if not isinstance(row, dict):
            continue
        item_name = str(row.get("target_item_name") or "").strip()
        if not item_name:
            result["skipped_count"] += 1
            result["skipped_rows"].append({"reason": "缺少目标物料行"})
            continue
        exists = getattr(getattr(frappe, "db", None), "exists", None)
        if callable(exists) and not exists("Overseas Cost Item", item_name):
            result["skipped_count"] += 1
            result["skipped_rows"].append({"item_name": item_name, "reason": "物料行已不存在"})
            continue

        try:
            item_doc = frappe.get_doc("Overseas Cost Item", item_name)
        except Exception as exc:
            result["skipped_count"] += 1
            result["skipped_rows"].append({"item_name": item_name, "reason": f"物料行读取失败：{exc}"})
            continue

        rollback_updates = {}
        skipped_fields = []
        for change in row.get("changed_fields") or []:
            if not isinstance(change, dict):
                continue
            field_name = str(change.get("field_name") or "").strip()
            if field_name not in ATTACHMENT_ROLLBACK_FIELDS:
                continue
            current_value = getattr(item_doc, field_name, None)
            written_value = change.get("new_value")
            if not _values_equal_for_import(current_value, written_value):
                skipped_fields.append(field_name)
                continue
            rollback_updates[field_name] = change.get("old_value")

        if not rollback_updates:
            result["skipped_count"] += 1
            result["skipped_rows"].append(
                {
                    "item_name": item_name,
                    "row_no": row.get("target_row_no"),
                    "reason": "没有可安全撤销的字段" if not skipped_fields else f"字段已被后续修改：{', '.join(skipped_fields)}",
                }
            )
            continue

        changed_fields = _update_item_fields(
            item_name=item_name,
            batch_doc_name=batch_doc_name or str(getattr(item_doc, "batch", "") or ""),
            version_name=version_name or str(getattr(item_doc, "version", "") or "") or None,
            row_no=row.get("target_row_no") or getattr(item_doc, "row_no", None),
            field_updates=rollback_updates,
            action_remark=f"删除补传资料后撤销附件解析写入；附件：{attachment_name}",
        )
        if changed_fields:
            result["rolled_back_row_count"] += 1
            result["rolled_back_field_count"] += len(changed_fields)

    for row in created_rows:
        if not isinstance(row, dict):
            continue
        item_name = str(row.get("item_name") or "").strip()
        if not item_name:
            result["skipped_count"] += 1
            result["skipped_rows"].append({"reason": "缺少自动新增物料名称"})
            continue
        exists = getattr(getattr(frappe, "db", None), "exists", None)
        if callable(exists) and not exists("Overseas Cost Item", item_name):
            continue
        try:
            item_doc = frappe.get_doc("Overseas Cost Item", item_name)
        except Exception as exc:
            result["skipped_count"] += 1
            result["skipped_rows"].append({"item_name": item_name, "reason": f"自动新增物料读取失败：{exc}"})
            continue

        item_source_attachment = str(getattr(item_doc, "source_attachment_id", "") or "").strip()
        if item_source_attachment and item_source_attachment != attachment_name:
            result["skipped_count"] += 1
            result["skipped_rows"].append({"item_name": item_name, "reason": "物料来源附件已变化，未删除"})
            continue

        row_no = getattr(item_doc, "row_no", None)
        old_snapshot = {
            "item_name": item_name,
            "row_no": row_no,
            "material_code": getattr(item_doc, "material_code", ""),
            "product_name": getattr(item_doc, "product_name", ""),
            "spec_model": getattr(item_doc, "spec_model", ""),
        }
        frappe.delete_doc("Overseas Cost Item", item_name, ignore_permissions=True)
        _create_audit_log(
            batch_doc_name=batch_doc_name or str(getattr(item_doc, "batch", "") or ""),
            version_name=version_name or str(getattr(item_doc, "version", "") or "") or None,
            row_no=row_no,
            field_name="item",
            old_value=_json_dumps(old_snapshot),
            new_value=None,
            action_remark=f"删除补传资料后移除该附件自动新增物料；附件：{attachment_name}",
        )
        result["deleted_item_count"] += 1

    if result["rolled_back_field_count"] or result["deleted_item_count"]:
        if batch_doc_name:
            _mark_batch_dirty(batch_doc_name)
        result["recalculate_result"] = _recalculate_after_writeback(
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            enabled=True,
        )
    else:
        result["recalculate_result"] = {"action": "skipped", "reason": "没有需要撤销的解析写入。"}

    return result


def _attach_existing_file_to_attachment(file_url: str, attachment_name: str) -> None:
    if frappe is None or not file_url or not attachment_name:
        return
    try:
        file_doc_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
        if not file_doc_name:
            return
        file_doc = frappe.get_doc("File", file_doc_name)
        file_doc.attached_to_doctype = "Overseas Cost Attachment"
        file_doc.attached_to_name = attachment_name
        file_doc.save(ignore_permissions=True)
    except Exception:
        return


def _get_oa_postgres_source():
    from overseas_costing.scripts.import_oa_logistics import _get_postgres_approval_source

    return _get_postgres_approval_source()


def _get_minio_archive_client() -> MinioArchiveClient:
    from minio import Minio

    from overseas_costing.scripts.import_oa_logistics import _runtime_config_bool, _runtime_config_value

    endpoint = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_ENDPOINT",
        "overseas_costing_oa_minio_endpoint",
        default="10.203.0.1:9000",
    )
    bucket = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_BUCKET",
        "overseas_costing_oa_minio_bucket",
        default="dingtalk-approval-archive",
    )
    access_key = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_ACCESS_KEY",
        "overseas_costing_oa_minio_access_key",
    )
    secret_key = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_SECRET_KEY",
        "overseas_costing_oa_minio_secret_key",
    )
    if not endpoint or not bucket or not access_key or not secret_key:
        raise ValueError(_("缺少 MinIO OA 归档读取配置（endpoint/bucket/access key/secret key）。"))
    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=_runtime_config_bool(
            "OVERSEAS_COSTING_OA_MINIO_SECURE",
            "overseas_costing_oa_minio_secure",
            default=False,
        ),
    )
    return MinioArchiveClient(bucket=bucket, client=client)


def _source_lag_seconds(value) -> int | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return max(0, int((now - parsed).total_seconds()))


def _is_minio_access_denied(error) -> bool:
    values = [str(error or ""), str(getattr(error, "code", "") or "")]
    text = " ".join(values).casefold()
    return any(marker in text for marker in (
        "accessdenied", "access denied", "permission to access", "not authorized",
        "403 forbidden", "status code: 403", "statuscode: 403",
    ))


def _minio_access_denied_message() -> str:
    return (
        "MinIO 归档账号无权读取该附件，请联系管理员核对对象读取权限，"
        "或从钉钉原单下载后人工上传。"
    )


def _download_oa_attachment_from_archive(
    *,
    attachment_doc,
    attachment_name: str,
    parse_snapshot: dict,
    process_instance_id: str,
    file_id: str,
    file_name: str,
) -> dict:
    manifest = _get_oa_postgres_source().get_attachment_manifest(process_instance_id, file_id)
    if not manifest:
        manifest = {"archive_status": "pending", "last_error": "归档清单尚未生成"}

    archive_status = str(manifest.get("archive_status") or "pending")
    source_updated_at = manifest.get("updated_at") or manifest.get("archived_at") or None
    source_lag_seconds = _source_lag_seconds(source_updated_at)
    parse_snapshot["archive"] = {
        "status": archive_status,
        "object_key": manifest.get("object_key") or "",
        "source_updated_at": source_updated_at or "",
        "last_error": manifest.get("last_error") or "",
        "archive_method": manifest.get("archive_method") or "",
        "content_quality": manifest.get("content_quality") or "",
        "failure_code": manifest.get("failure_code") or "",
        "failure_reason": manifest.get("last_error") or "",
        "last_attempt_strategy": manifest.get("last_attempt_strategy") or "",
        "diagnostic_json": manifest.get("diagnostic_json") or [],
    }
    if archive_status != "archived":
        manual_required = archive_status == "manual_required"
        access_denied = manual_required and _is_minio_access_denied(manifest.get("last_error"))
        if manual_required:
            if access_denied:
                message = _minio_access_denied_message()
            else:
                reason = str(manifest.get("last_error") or "归档服务已标记为需要人工补传")
                message = f"附件无法自动归档：{reason}。请从钉钉原单手动下载后上传。"
            attachment_doc.parse_status = "Failed"
        else:
            message = "附件正在归档，请稍后重试。"
        attachment_doc.parse_result_json = _json_dumps(parse_snapshot)
        attachment_doc.remark = message[:500]
        attachment_doc.save(ignore_permissions=True)
        if hasattr(frappe.db, "commit"):
            frappe.db.commit()
        return {
            "ok": False,
            "downloaded": False,
            "attachment_name": attachment_name,
            "file_name": file_name,
            "data_source": "minio_archive",
            "source_updated_at": source_updated_at,
            "source_lag_seconds": source_lag_seconds,
            "archive_status": archive_status,
            "fallback_used": False,
            "needs_manual_upload": manual_required,
            **({"error_type": "minio_archive_access_denied"} if access_denied else {}),
            "message": message,
        }

    try:
        content, response_meta = _get_minio_archive_client().download(manifest)
        file_doc = _save_content_as_frappe_file(
            file_name=file_name,
            content=content,
            attached_to_name=attachment_name,
        )
    except (ArchiveIntegrityError, ArchiveNotReady) as exc:
        return {
            "ok": False,
            "downloaded": False,
            "attachment_name": attachment_name,
            "file_name": file_name,
            "data_source": "minio_archive",
            "source_updated_at": source_updated_at,
            "source_lag_seconds": source_lag_seconds,
            "archive_status": archive_status,
            "fallback_used": False,
            "message": f"归档附件校验失败：{exc}",
        }
    except Exception as exc:
        message = str(exc)
        access_denied = _is_minio_access_denied(exc)
        return {
            "ok": False,
            "downloaded": False,
            "attachment_name": attachment_name,
            "file_name": file_name,
            "data_source": "minio_archive",
            "source_updated_at": source_updated_at,
            "source_lag_seconds": source_lag_seconds,
            "archive_status": archive_status,
            "fallback_used": False,
            **({"error_type": "minio_archive_access_denied", "needs_manual_upload": True}
               if access_denied else {}),
            "message": (
                _minio_access_denied_message()
                if access_denied else f"MinIO 归档读取失败：{message}"
            ),
        }

    file_url = _get_doc_value(file_doc, "file_url") or ""
    parse_snapshot.pop("last_download_error", None)
    parse_snapshot["download"] = {
        "source": "minio_archive",
        "process_instance_id": process_instance_id,
        "file_id": file_id,
        "object_key": response_meta.get("object_key") or manifest.get("object_key") or "",
        "sha256": response_meta.get("sha256") or manifest.get("sha256") or "",
        "content_type": response_meta.get("content_type") or "",
        "content_length": response_meta.get("content_length") or len(content),
        "archived_at": manifest.get("archived_at") or "",
    }
    attachment_doc.file_url = file_url
    attachment_doc.parse_status = getattr(attachment_doc, "parse_status", "") or "Queued"
    attachment_doc.parse_result_json = _json_dumps(parse_snapshot)
    origin = getattr(attachment_doc, "oa_attachment_origin", "") or parse_snapshot.get("attachment_origin") or "Form"
    attachment_doc.remark = f"钉钉{'评论' if origin == 'Comment' else '表单'}附件已从 MinIO 归档保存，可继续解析。"
    attachment_doc.save(ignore_permissions=True)
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()
    return {
        "ok": True,
        "downloaded": True,
        "attachment_name": attachment_name,
        "file_name": file_name,
        "source_file_name": file_name,
        "file_url": file_url,
        "content_type": response_meta.get("content_type") or "",
        "content_length": response_meta.get("content_length") or len(content),
        "data_source": "minio_archive",
        "source_updated_at": source_updated_at,
        "source_lag_seconds": source_lag_seconds,
        "archive_status": "archived",
        "fallback_used": False,
        "message": "归档附件已保存，可下载或继续解析。",
    }


def list_oa_form_attachments(batch_name: str, limit: int | None = 50) -> dict:
    """返回钉钉表单和评论附件记录。"""

    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "items": [],
            "total": 0,
            "message": "当前未连接 Frappe，返回空附件记录。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {
            "ok": False,
            "batch_name": batch_name,
            "items": [],
            "total": 0,
            "message": f"未找到批次：{batch_name}",
        }

    requested_limit = max(1, min(int(limit or 50), 200))
    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters={"batch": batch_doc_name, "source_type": "OA"},
        fields=[
            "name",
            "batch",
            "version",
            "source_type",
            "oa_attachment_origin",
            "attachment_type",
            "source_doc_no",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
            "remark",
            "creation",
            "modified",
        ],
        order_by="creation asc",
        limit_page_length=requested_limit,
    )

    items = []
    for row in rows:
        parse_result = _json_loads_dict(row.get("parse_result_json"))
        mapped_result = _json_loads_dict(row.get("mapped_result_json"))
        manual_review = _source_document_manual_review(mapped_result)
        source_preview = _source_document_preview_cache(mapped_result, row.get("file_url") or "")
        source_classification = (
            source_preview.get("classification") if isinstance(source_preview.get("classification"), dict) else {}
        )
        last_download_error = parse_result.get("last_download_error") if isinstance(parse_result.get("last_download_error"), dict) else {}
        archive = parse_result.get("archive") if isinstance(parse_result.get("archive"), dict) else {}
        source_updated_at = archive.get("source_updated_at") or row.get("modified")
        failure_code = str(archive.get("failure_code") or "")
        failure_reason = str(archive.get("failure_reason") or archive.get("last_error") or "")
        if failure_code == "userNotExist" or "userNotExist" in failure_reason:
            failure_reason = "钉钉下载授权返回 userNotExist，尚不能判断文件是否删除。"
        archive_status = archive.get("status") or ("archived" if row.get("file_url") else "pending")
        has_local_file = bool(row.get("file_url"))
        is_archived = archive_status == "archived"
        items.append(
            {
                "name": row.get("name"),
                "batch": row.get("batch"),
                "version": row.get("version"),
                "attachment_type": row.get("attachment_type") or "Other",
                "oa_attachment_origin": row.get("oa_attachment_origin") or parse_result.get("attachment_origin") or "Form",
                "source_doc_no": row.get("source_doc_no") or "",
                "file_name": row.get("file_name") or "",
                "file_url": row.get("file_url") or "",
                "parse_status": row.get("parse_status") or "",
                "source_field": parse_result.get("source_field") or "",
                "file_id": parse_result.get("file_id") or "",
                "space_id": parse_result.get("space_id") or "",
                "file_ext": parse_result.get("file_ext") or "",
                "parse_targets": mapped_result.get("parse_targets") or [],
                "manual_review": manual_review,
                "confirmed_type_label": manual_review.get("confirmed_type_label") or "",
                "recognized_type": source_classification.get("code") or "",
                "recognized_type_label": source_classification.get("label") or "",
                "extraction_method": source_preview.get("extraction_method") or "",
                "text_length": source_preview.get("text_length") or 0,
                "last_download_error": last_download_error,
                "comment_user_name": parse_result.get("comment_user_name") or "",
                "comment_time": parse_result.get("comment_time") or "",
                "comment_remark": parse_result.get("comment_remark") or "",
                "archive_status": archive_status,
                "archive_method": archive.get("archive_method") or "",
                "content_quality": archive.get("content_quality") or "",
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "last_attempt_strategy": archive.get("last_attempt_strategy") or "",
                "previewable": has_local_file or is_archived,
                "downloadable": has_local_file or is_archived,
                "data_source": "minio_archive" if archive else "dingtalk_api",
                "source_updated_at": source_updated_at,
                "source_lag_seconds": _source_lag_seconds(source_updated_at),
                "fallback_used": False,
                "can_download": bool(parse_result.get("file_id")) and not bool(row.get("file_url")),
                "remark": row.get("remark") or "",
                "creation": row.get("creation"),
                "modified": row.get("modified"),
            }
        )

    return {
        "ok": True,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "items": items,
        "total": len(items),
        "comment_attachments_included": True,
        "message": "钉钉表单和评论附件记录已返回。",
    }


def download_oa_form_attachment(
    attachment_name: str,
    env_file: str | None = None,
    access_token: str | None = None,
) -> dict:
    """从 MinIO 归档（或人工应急 API）下载 OA 表单/评论附件。"""

    resolved_attachment_name = str(attachment_name or "").strip()
    if not resolved_attachment_name:
        return {"ok": False, "message": "缺少附件记录名称。"}

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "attachment_name": resolved_attachment_name,
            "message": "当前未连接 Frappe，不能下载并保存钉钉附件。",
        }

    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception as exc:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "message": f"未找到附件记录：{exc}",
        }

    if getattr(attachment_doc, "source_type", "") != "OA":
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "message": "该记录不是钉钉审批附件，不能用归档附件下载接口处理。",
        }

    existing_file_url = str(getattr(attachment_doc, "file_url", "") or "").strip()
    if existing_file_url:
        return {
            "ok": True,
            "downloaded": False,
            "attachment_name": resolved_attachment_name,
            "file_url": existing_file_url,
            "message": "附件已经有系统文件地址，无需重复下载。",
        }

    parse_snapshot = _json_loads_dict(getattr(attachment_doc, "parse_result_json", ""))
    raw_attachment = parse_snapshot.get("raw_attachment") if isinstance(parse_snapshot.get("raw_attachment"), dict) else {}
    process_instance_id = str(
        parse_snapshot.get("instance_id")
        or parse_snapshot.get("process_instance_id")
        or parse_snapshot.get("proc_inst_id")
        or ""
    ).strip()
    file_id = str(
        parse_snapshot.get("file_id")
        or raw_attachment.get("fileId")
        or raw_attachment.get("file_id")
        or raw_attachment.get("mediaId")
        or raw_attachment.get("id")
        or ""
    ).strip()
    space_id = str(
        parse_snapshot.get("space_id")
        or raw_attachment.get("spaceId")
        or raw_attachment.get("space_id")
        or ""
    ).strip()

    if not process_instance_id or not file_id:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "message": "附件记录缺少钉钉审批实例 ID 或 file_id，请先重新拉取国际物流 OA。",
            "missing": {
                "process_instance_id": not bool(process_instance_id),
                "file_id": not bool(file_id),
            },
        }

    file_name = str(getattr(attachment_doc, "file_name", "") or raw_attachment.get("fileName") or file_id).strip()
    from overseas_costing.scripts.import_oa_logistics import resolve_approval_source_mode

    if resolve_approval_source_mode() == "postgres":
        return _download_oa_attachment_from_archive(
            attachment_doc=attachment_doc,
            attachment_name=resolved_attachment_name,
            parse_snapshot=parse_snapshot,
            process_instance_id=process_instance_id,
            file_id=file_id,
            file_name=file_name,
        )

    try:
        from overseas_costing.scripts.import_oa_logistics import (
            get_access_token,
            get_process_attachment_download_url,
            load_env_file,
        )

        resolved_env_file = _resolve_dingtalk_env_file(env_file)
        if resolved_env_file:
            load_env_file(resolved_env_file)
        user_id = _resolve_attachment_user_id(attachment_doc, parse_snapshot)
        corp_id = _resolve_attachment_corp_id(attachment_doc, parse_snapshot)
        token = get_access_token(
            api_style="auto",
            access_token=str(access_token or "").strip(),
            corp_id=corp_id,
        )
        try:
            download_info = get_process_attachment_download_url(
                token=token,
                process_instance_id=process_instance_id,
                file_id=file_id,
                file_name=file_name,
                space_id=space_id,
                user_id=user_id,
                corp_id=corp_id,
                api_style="auto",
            )
            fetch_kwargs = {}
            if download_info.get("download_headers"):
                fetch_kwargs["headers"] = download_info.get("download_headers") or {}
            content, response_meta = _fetch_dingtalk_attachment_content(download_info["download_uri"], **fetch_kwargs)
            saved_file_name = _thumbnail_media_file_name(file_name, file_id) if download_info.get("fallback_api") == "storage_thumbnail_query" else file_name
            file_doc = _save_content_as_frappe_file(
                file_name=saved_file_name,
                content=content,
                attached_to_name=resolved_attachment_name,
            )
        except Exception as primary_exc:
            try:
                file_doc, response_meta, saved_file_name, thumbnail_media_id = _download_dingtalk_thumbnail_media(
                    token=token,
                    parse_snapshot=parse_snapshot,
                    file_name=file_name,
                    file_id=file_id,
                    attached_to_name=resolved_attachment_name,
                )
            except Exception as thumbnail_exc:
                raise RuntimeError(f"{primary_exc}；缩略图媒体兜底失败：{thumbnail_exc}") from thumbnail_exc
            download_info = {
                "space_id": space_id,
                "fallback_api": "thumbnail_media_download",
                "download_headers": {},
                "thumbnail_media_id": thumbnail_media_id,
                "primary_error": str(primary_exc),
            }
    except Exception as exc:
        error_message = str(exc)
        if _is_dingtalk_attachment_file_access_error(error_message):
            message = (
                "钉钉已找到这份审批附件，但当前配置的下载账号没有该附件的文件级访问权限。"
                "系统已尝试旧版下载、旧版授权、新版授权和钉盘下载信息接口，仍被钉钉拒绝。"
                "请改用一个能在钉钉原单里打开该附件的在职账号 userId，或从钉钉原单手动下载后拖放上传，系统仍会继续解析并回填数据。"
            )
            _record_oa_attachment_download_failure(
                attachment_doc,
                parse_snapshot,
                error_type="dingtalk_attachment_file_access",
                message=message,
                file_name=file_name,
                process_instance_id=process_instance_id,
                file_id=file_id,
                space_id=space_id,
                user_id=user_id if "user_id" in locals() else "",
            )
            return {
                "ok": False,
                "attachment_name": resolved_attachment_name,
                "file_name": file_name,
                "error_type": "dingtalk_attachment_file_access",
                "needs_manual_upload": True,
                "message": message,
            }
        if _is_dingtalk_attachment_permission_error(error_message):
            message = (
                "钉钉已找到该附件，但当前应用还缺少附件下载所需权限。"
                "请在钉钉开放平台为当前应用开通 qyapi_get_member、Storage.DownloadInfo.Read 和 Storage.File.Read 后重试；"
                "如果暂时不开权限，也可以先在钉钉原单下载附件后拖放上传。"
            )
            _record_oa_attachment_download_failure(
                attachment_doc,
                parse_snapshot,
                error_type="dingtalk_attachment_permission",
                message=message,
                file_name=file_name,
                process_instance_id=process_instance_id,
                file_id=file_id,
                space_id=space_id,
                user_id=user_id if "user_id" in locals() else "",
            )
            return {
                "ok": False,
                "attachment_name": resolved_attachment_name,
                "file_name": file_name,
                "error_type": "dingtalk_attachment_permission",
                "needs_dingtalk_permission": True,
                "message": message,
            }
        if _is_dingtalk_attachment_user_error(error_message):
            message = (
                "钉钉下载授权返回 userNotExist，尚不能判断文件是否删除，也不能直接归因为员工离职。"
                "请由阿里云归档服务使用管理员身份继续多策略诊断；若仍失败，再从钉钉原单人工补传。"
            )
            _record_oa_attachment_download_failure(
                attachment_doc,
                parse_snapshot,
                error_type="dingtalk_attachment_user",
                message=message,
                file_name=file_name,
                process_instance_id=process_instance_id,
                file_id=file_id,
                space_id=space_id,
                user_id=user_id if "user_id" in locals() else "",
            )
            return {
                "ok": False,
                "attachment_name": resolved_attachment_name,
                "file_name": file_name,
                "error_type": "dingtalk_attachment_user",
                "needs_attachment_user_id": True,
                "message": message,
            }
        fallback_message = f"钉钉附件下载失败：{error_message}"
        _record_oa_attachment_download_failure(
            attachment_doc,
            parse_snapshot,
            error_type="dingtalk_attachment_download_failed",
            message=fallback_message,
            file_name=file_name,
            process_instance_id=process_instance_id,
            file_id=file_id,
            space_id=space_id,
            user_id=user_id if "user_id" in locals() else "",
        )
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "file_name": file_name,
            "message": fallback_message,
        }

    file_url = _get_doc_value(file_doc, "file_url") or ""
    parse_snapshot.pop("last_download_error", None)
    parse_snapshot["download"] = {
        "source": "dingtalk_oa_form_attachment",
        "process_instance_id": process_instance_id,
        "file_id": file_id,
        "user_id": user_id,
        "space_id": download_info.get("space_id") or "",
        "fallback_api": download_info.get("fallback_api") or "",
        "saved_file_name": saved_file_name,
        "download_uri_obtained": True,
        "download_headers_obtained": bool(download_info.get("download_headers")),
        "content_type": response_meta.get("content_type") or "",
        "content_length": response_meta.get("content_length") or len(content),
    }
    attachment_doc.file_url = file_url
    attachment_doc.parse_status = getattr(attachment_doc, "parse_status", "") or "Queued"
    attachment_doc.parse_result_json = _json_dumps(parse_snapshot)
    attachment_doc.remark = "钉钉 OA 附件已通过人工应急 API 保存，可继续解析。"
    attachment_doc.save(ignore_permissions=True)
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "downloaded": True,
        "attachment_name": resolved_attachment_name,
        "file_name": saved_file_name,
        "source_file_name": file_name,
        "file_url": file_url,
        "content_type": response_meta.get("content_type") or "",
        "content_length": response_meta.get("content_length") or len(content),
        "data_source": "dingtalk_api",
        "source_updated_at": None,
        "archive_status": "api_emergency",
        "fallback_used": True,
        "message": "钉钉附件已通过人工应急 API 保存，可继续解析。",
    }


def _record_oa_attachment_download_failure(
    attachment_doc,
    parse_snapshot: dict,
    *,
    error_type: str,
    message: str,
    file_name: str,
    process_instance_id: str,
    file_id: str,
    space_id: str,
    user_id: str,
) -> None:
    parse_snapshot["last_download_error"] = {
        "error_type": error_type,
        "message": message,
        "file_name": file_name,
        "process_instance_id": process_instance_id,
        "file_id": file_id,
        "space_id": space_id,
        "user_id_configured": bool(user_id),
    }
    try:
        attachment_doc.parse_status = "Failed"
        attachment_doc.parse_result_json = _json_dumps(parse_snapshot)
        attachment_doc.remark = message[:500]
        if hasattr(attachment_doc, "save"):
            attachment_doc.save(ignore_permissions=True)
        if frappe is not None and hasattr(frappe.db, "commit"):
            frappe.db.commit()
    except Exception:
        return


def diagnose_oa_form_attachment_download(
    attachment_name: str,
    env_file: str | None = None,
    access_token: str | None = None,
) -> dict:
    """诊断钉钉审批发起附件自动下载链路，不保存文件。"""

    resolved_attachment_name = str(attachment_name or "").strip()
    if not resolved_attachment_name:
        return {"ok": False, "message": "缺少附件记录名称。"}
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "attachment_name": resolved_attachment_name,
            "message": "当前未连接 Frappe，不能诊断钉钉附件下载。",
        }

    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception as exc:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "message": f"未找到附件记录：{exc}",
        }

    parse_snapshot = _json_loads_dict(getattr(attachment_doc, "parse_result_json", ""))
    raw_attachment = parse_snapshot.get("raw_attachment") if isinstance(parse_snapshot.get("raw_attachment"), dict) else {}
    process_instance_id = str(
        parse_snapshot.get("instance_id")
        or parse_snapshot.get("process_instance_id")
        or parse_snapshot.get("proc_inst_id")
        or ""
    ).strip()
    file_id = str(
        parse_snapshot.get("file_id")
        or raw_attachment.get("fileId")
        or raw_attachment.get("file_id")
        or raw_attachment.get("mediaId")
        or raw_attachment.get("id")
        or ""
    ).strip()
    space_id = str(
        parse_snapshot.get("space_id")
        or raw_attachment.get("spaceId")
        or raw_attachment.get("space_id")
        or ""
    ).strip()
    file_name = str(getattr(attachment_doc, "file_name", "") or raw_attachment.get("fileName") or file_id).strip()
    if not process_instance_id or not file_id:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "file_name": file_name,
            "message": "附件记录缺少钉钉审批实例 ID 或 file_id，请先重新拉取国际物流 OA。",
        }

    try:
        from overseas_costing.scripts.import_oa_logistics import (
            diagnose_process_attachment_download,
            get_access_token,
            load_env_file,
        )

        resolved_env_file = _resolve_dingtalk_env_file(env_file)
        if resolved_env_file:
            load_env_file(resolved_env_file)
        user_id = _resolve_attachment_user_id(attachment_doc, parse_snapshot)
        corp_id = _resolve_attachment_corp_id(attachment_doc, parse_snapshot)
        token = get_access_token(
            api_style="auto",
            access_token=str(access_token or "").strip(),
            corp_id=corp_id,
        )
        result = diagnose_process_attachment_download(
            token=token,
            process_instance_id=process_instance_id,
            file_id=file_id,
            file_name=file_name,
            space_id=space_id,
            user_id=user_id,
            corp_id=corp_id,
            api_style="auto",
        )
    except Exception as exc:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "file_name": file_name,
            "message": f"钉钉附件下载诊断失败：{exc}",
        }

    result["attachment_name"] = resolved_attachment_name
    result["file_name"] = file_name
    return result


def _approval_reference_is_excluded(batch_name: str, snapshot: dict) -> bool:
    instance_id = str(
        snapshot.get("process_instance_id")
        or snapshot.get("instance_id")
        or snapshot.get("proc_inst_id")
        or ""
    ).strip()
    if frappe is None or not batch_name or not instance_id:
        return False
    try:
        batch = frappe.db.get_value(
            "Overseas Cost Batch",
            batch_name,
            ["source_instance_id", "source_approval_status", "extra_json"],
            as_dict=True,
        ) or {}
    except Exception:
        return False

    from overseas_costing.scripts.import_oa_logistics import is_hidden_approval_status

    if instance_id == str(batch.get("source_instance_id") or "").strip():
        return is_hidden_approval_status(batch.get("source_approval_status"))
    for linked in _get_linked_purchase_approvals_from_extra(batch.get("extra_json")):
        if instance_id != str(linked.get("source_instance_id") or "").strip():
            continue
        return is_hidden_approval_status(
            linked.get("process_status")
            or linked.get("approval_status")
            or linked.get("status"),
            linked.get("approval_result") or linked.get("result"),
        )
    # 找不到引用只能说明"查不到"，不能据此断定审批已被排除。
    # 旧实现返回 True，于是任何挂不到批次的附件都被当成审计件，
    # 表现为用户在审批批完前拉不到装箱单。真排除必须由 exclusion_reason
    # 或审批状态本身证明（见 settlement_audit_policy）。
    return False


def _settlement_audit_state(parsed: dict) -> dict:
    """复用统一审计口径；审批进行中不再被当成「仅审计」。"""

    from overseas_costing.services.settlement_audit_policy import settlement_audit_state

    return settlement_audit_state(parsed)


def _settlement_blocks_write(parsed: dict) -> bool:
    """写入闸门：真排除与审批未完成都拦。

    「仅审计」只是展示口径（见 settlement_audit_policy）；但把附件内容写进
    成本字段时，审批尚未完成的资料同样不能落库，否则未审批数据会污染核算。
    """

    from overseas_costing.services.settlement_audit_policy import is_blocked_from_settlement

    return is_blocked_from_settlement(parsed)


def _attachment_doc_is_audit_only(attachment_doc) -> bool:
    """附件是否属于真排除（只拦截 A 类）。

    这是**读口径**：审批进行中的附件仍可识别、分类、预览。写入闸门见
    ``_settlement_blocks_write``，由写入方显式追加判断。
    """

    snapshot = _json_loads_dict(getattr(attachment_doc, "parse_result_json", ""))
    if _settlement_audit_state(snapshot)["audit_only"]:
        return True
    return _approval_reference_is_excluded(
        str(getattr(attachment_doc, "batch", "") or "").strip(),
        snapshot,
    )


def _audit_only_attachment_response(attachment_name: str) -> dict:
    return {
        "ok": False,
        "audit_only": True,
        "attachment_name": attachment_name,
        "message": "该附件来自已排除审批，仅供审计查看和下载，不能识别、分类或写入任何成本数据。",
    }


def preview_oa_source_attachment(attachment_name: str) -> dict:
    """识别已下载 OA 附件的内容类型和字段候选，不写回物料字段。"""

    resolved_attachment_name = str(attachment_name or "").strip()
    if not resolved_attachment_name:
        return {"ok": False, "message": "缺少附件记录名称。"}
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "attachment_name": resolved_attachment_name,
            "message": "当前未连接 Frappe，不能读取 OA 附件。",
        }
    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception as exc:
        return {"ok": False, "attachment_name": resolved_attachment_name, "message": f"未找到附件记录：{exc}"}
    if _attachment_doc_is_audit_only(attachment_doc):
        return _audit_only_attachment_response(resolved_attachment_name)

    file_url = str(getattr(attachment_doc, "file_url", "") or "").strip()
    if not file_url:
        return {
            "ok": False,
            "attachment_name": resolved_attachment_name,
            "download_required": True,
            "message": "附件尚未下载到系统，请先下载后再识别内容。",
        }
    mapped_result = _json_loads_dict(getattr(attachment_doc, "mapped_result_json", ""))
    cached_preview = _source_document_preview_cache(mapped_result, file_url)
    if cached_preview:
        preview = {**cached_preview, "cache_hit": True}
    else:
        try:
            preview = attachment_parse_service.preview_source_document(
                source_name=str(getattr(attachment_doc, "file_name", "") or ""),
                file_url=file_url,
            )
        except Exception as exc:
            message = f"附件内容识别失败：{exc}"
            try:
                mapped_result["source_document_preview_error"] = {
                    "message": message,
                    "file_url": file_url,
                    "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                attachment_doc.parse_status = "Failed"
                attachment_doc.mapped_result_json = _json_dumps(mapped_result)
                attachment_doc.remark = message[:500]
                attachment_doc.save(ignore_permissions=True)
                if hasattr(frappe.db, "commit"):
                    frappe.db.commit()
            except Exception:
                pass
            return {
                "ok": False,
                "attachment_name": resolved_attachment_name,
                "file_url": file_url,
                "message": message,
            }
        preview = {**preview, "cache_hit": False}
        try:
            _save_source_document_preview(attachment_doc, mapped_result, preview, file_url)
        except Exception:
            # 缓存写入失败不影响本次财务核对。
            pass
    return {
        **preview,
        "attachment_name": resolved_attachment_name,
        "batch_name": str(getattr(attachment_doc, "batch", "") or ""),
        "version_name": str(getattr(attachment_doc, "version", "") or ""),
        "attachment_type": str(getattr(attachment_doc, "attachment_type", "") or "Other"),
        "manual_review": _source_document_manual_review(mapped_result),
    }


def confirm_oa_source_attachment_type(
    attachment_name: str,
    confirmed_type: str,
    remark: str | None = None,
) -> dict:
    """人工确认 OA 发起附件类型，只保存分类与追溯信息，不写入成本明细。"""

    resolved_attachment_name = str(attachment_name or "").strip()
    if not resolved_attachment_name:
        return {"ok": False, "message": "缺少附件记录名称。"}
    review_config = SOURCE_DOCUMENT_MANUAL_TYPES.get(str(confirmed_type or "").strip())
    if not review_config:
        return {"ok": False, "message": "请选择有效的附件资料类型。"}
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "attachment_name": resolved_attachment_name,
            "message": "当前未连接 Frappe，不能保存附件人工确认结果。",
        }

    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception as exc:
        return {"ok": False, "attachment_name": resolved_attachment_name, "message": f"未找到附件记录：{exc}"}
    # 保存人工确认结果属于写入：审批未完成的附件同样不能定稿类型，
    # 否则未审批资料会被当成已确认事实流入成本数据。
    if _attachment_doc_is_audit_only(attachment_doc) or _settlement_blocks_write(
        _json_loads_dict(getattr(attachment_doc, "parse_result_json", ""))
    ):
        return _audit_only_attachment_response(resolved_attachment_name)

    mapped_result = _json_loads_dict(getattr(attachment_doc, "mapped_result_json", ""))
    previous_review = _source_document_manual_review(mapped_result)
    previous_attachment_type = str(getattr(attachment_doc, "attachment_type", "") or "Other")
    automatic_classification, automatic_error = _preview_attachment_classification_for_review(attachment_doc, mapped_result)
    review = _build_source_document_manual_review(
        confirmed_type=confirmed_type,
        remark=remark,
        automatic_classification=automatic_classification,
        automatic_error=automatic_error,
    )
    history = mapped_result.get("source_document_review_history")
    if not isinstance(history, list):
        history = []
    if previous_review:
        history.append(previous_review)
    mapped_result["source_document_review"] = review
    mapped_result["source_document_review_history"] = history[-20:]
    mapped_result["parse_targets"] = _merge_parse_targets(
        mapped_result.get("parse_targets"),
        review.get("parse_targets"),
    )

    attachment_doc.attachment_type = review["attachment_type"]
    attachment_doc.parse_status = "Parsed"
    attachment_doc.mapped_result_json = _json_dumps(mapped_result)
    attachment_doc.remark = _append_source_document_review_remark(
        getattr(attachment_doc, "remark", ""),
        review,
    )
    attachment_doc.save(ignore_permissions=True)
    _create_audit_log(
        batch_doc_name=str(getattr(attachment_doc, "batch", "") or ""),
        version_name=str(getattr(attachment_doc, "version", "") or "") or None,
        row_no=None,
        field_name="attachment_type",
        old_value=previous_review.get("confirmed_type_label") or previous_attachment_type,
        new_value=review["confirmed_type_label"],
        action_remark=_source_document_review_audit_remark(attachment_doc, review),
    )
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()
    return {
        "ok": True,
        "attachment_name": resolved_attachment_name,
        "batch_name": str(getattr(attachment_doc, "batch", "") or ""),
        "attachment_type": review["attachment_type"],
        "manual_review": review,
        "message": f"已确认该附件为“{review['confirmed_type_label']}”。{review['next_step']}",
    }


def preview_oa_purchase_order_match(
    attachment_name: str,
    version_name: str | None = None,
) -> dict:
    """预览采购订单附件可补入哪些采购价格字段，不写入成本明细。"""

    source_preview = preview_oa_source_attachment(attachment_name)
    if not source_preview.get("ok"):
        return {
            "ok": False,
            "attachment_name": attachment_name,
            "source_preview": source_preview,
            "message": source_preview.get("message") or "采购订单附件识别失败。",
        }

    classification = source_preview.get("classification") or {}
    purchase_order = source_preview.get("purchase_order") or {}
    if classification.get("code") != "purchase_order":
        return {
            "ok": False,
            "attachment_name": attachment_name,
            "source_preview": source_preview,
            "message": "该附件未识别为采购订单，暂不能生成采购价格匹配预览。",
        }

    order_no = str(purchase_order.get("purchase_order_no") or "").strip()
    source_rows = []
    for row in purchase_order.get("line_items") or []:
        if not isinstance(row, dict):
            continue
        source_rows.append(
            {
                **row,
                "supplier": row.get("supplier") or purchase_order.get("supplier") or "",
                "source_type": "PURCHASE_ORDER_ATTACHMENT",
                "source_attachment_id": attachment_name,
                "source_file_name": source_preview.get("source_name") or "",
                "source_doc_no": order_no or attachment_name,
            }
        )

    def build_purchase_order_updates(mapped_row: dict, _target: dict) -> dict:
        return {
            field_name: mapped_row.get(field_name)
            for field_name in PURCHASE_WRITEBACK_FIELDS
        }

    writeback_preview = _preview_item_writeback(
        batch_name=source_preview.get("batch_name") or "",
        version_name=version_name or source_preview.get("version_name") or None,
        mapped_rows=source_rows,
        update_builder=build_purchase_order_updates,
        field_labels=PURCHASE_ORDER_FIELD_LABELS,
        business_fields=PURCHASE_WRITEBACK_FIELDS,
        numeric_zero_fillable_fields={"unit_price", "goods_value"},
        preview_message_prefix="采购订单附件",
        fillable_message="可补入匹配物料行中为空的采购单价、币种和货值。",
        trust_unique_material_code=True,
    )
    return {
        "ok": True,
        "attachment_name": attachment_name,
        "batch_name": source_preview.get("batch_name") or "",
        "version_name": writeback_preview.get("version_name") or source_preview.get("version_name") or version_name,
        "purchase_order": {
            "purchase_order_no": order_no,
            "supplier": purchase_order.get("supplier") or "",
            "buyer": purchase_order.get("buyer") or "",
            "currency": purchase_order.get("currency") or "",
            "recognized_line_count": len(source_rows),
        },
        "source_rows": [_compact_purchase_order_row(row) for row in source_rows[:100]],
        "writeback_preview": writeback_preview,
        "message": writeback_preview.get("message") or purchase_order.get("message") or "采购订单匹配预览已生成。",
    }


def apply_oa_purchase_order_fillable_fields(
    attachment_name: str,
    version_name: str | None = None,
    recalculate_after_writeback: bool = True,
) -> dict:
    """确认将采购订单附件匹配到的空采购字段写入物料行。"""

    preview_result = preview_oa_purchase_order_match(
        attachment_name=attachment_name,
        version_name=version_name,
    )
    if not preview_result.get("ok"):
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": preview_result.get("message") or "采购订单匹配预览失败，未写入数据。",
        }
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "preview_result": preview_result,
            "message": "当前未连接 Frappe，不能保存采购订单价格字段。",
        }

    writeback_preview = preview_result.get("writeback_preview") or {}
    batch_doc_name = writeback_preview.get("batch_doc_name")
    resolved_version_name = writeback_preview.get("version_name") or version_name
    if not batch_doc_name or not resolved_version_name:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "当前批次或版本未匹配成功，未写入数据。",
        }
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        invalid_response["preview_result"] = preview_result
        return invalid_response

    purchase_order = preview_result.get("purchase_order") or {}
    provenance = _build_attachment_price_provenance(attachment_name=attachment_name)
    provenance["source_doc_no"] = purchase_order.get("purchase_order_no") or provenance.get("source_doc_no")
    applied_rows: list[dict] = []
    skipped_rows: list[dict] = []
    changed_field_count = 0

    for row in writeback_preview.get("matched_rows") or []:
        target_item_name = row.get("target_item_name")
        business_changes = row.get("business_changes") or []
        if not target_item_name:
            skipped_rows.append({"row": row, "reason": "缺少目标物料行"})
            continue
        if row.get("has_conflict"):
            skipped_rows.append({"row": row, "reason": "系统已有不同采购值，未自动覆盖"})
            continue
        field_updates = {
            change.get("field_name"): change.get("new_value")
            for change in business_changes
            if change.get("field_name") in PURCHASE_WRITEBACK_FIELDS and change.get("status") == "fillable"
        }
        field_updates = {field_name: value for field_name, value in field_updates.items() if field_name}
        if not field_updates:
            skipped_rows.append({"row": row, "reason": "没有可补的采购字段"})
            continue

        field_updates.update(provenance)
        if purchase_order.get("supplier") and not field_updates.get("supplier"):
            field_updates["supplier"] = purchase_order.get("supplier")
        changed_fields = _update_item_fields(
            item_name=target_item_name,
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=row.get("target_row_no"),
            field_updates=field_updates,
            action_remark=(
                "采购订单附件确认补入空采购字段并登记附件来源；"
                f"采购订单：{provenance.get('source_doc_no') or '--'}"
            ),
        )
        if changed_fields:
            applied_rows.append(
                {
                    "target_item_name": target_item_name,
                    "target_row_no": row.get("target_row_no"),
                    "changed_fields": changed_fields,
                }
            )
            changed_field_count += len(changed_fields)

    if applied_rows:
        _mark_batch_dirty(batch_doc_name)
        _mark_attachment_parsed(
            attachment_name,
            {
                "purchase_order_no": purchase_order.get("purchase_order_no") or "",
                "recognized_line_count": purchase_order.get("recognized_line_count") or 0,
                "updated_count": len(applied_rows),
                "changed_field_count": changed_field_count,
                "skipped_count": len(skipped_rows),
                "parse_targets": list(PURCHASE_WRITEBACK_FIELDS),
            },
        )
        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit):
            commit()
        recalculate_result = _recalculate_after_writeback(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            enabled=recalculate_after_writeback,
        )
    else:
        recalculate_result = {"action": "skipped", "reason": "没有可安全补入的采购订单字段。"}

    base_message = (
        f"已从采购订单补入 {len(applied_rows)} 行、{changed_field_count} 个字段；已有不同采购值的行未覆盖。"
        if applied_rows
        else "没有可安全补入的采购订单字段，未写入数据。"
    )
    return {
        "ok": True,
        "attachment_name": attachment_name,
        "batch_name": preview_result.get("batch_name") or "",
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "purchase_order": purchase_order,
        "updated_count": len(applied_rows),
        "changed_field_count": changed_field_count,
        "skipped_count": len(skipped_rows),
        "conflict_row_count": writeback_preview.get("conflict_row_count", 0),
        "unmatched_count": writeback_preview.get("unmatched_count", 0),
        "ambiguous_count": writeback_preview.get("ambiguous_count", 0),
        "applied_rows": applied_rows,
        "skipped_rows": skipped_rows,
        "recalculate_result": recalculate_result,
        "preview_result": preview_result,
        "message": _message_with_recalculate_result(base_message, recalculate_result),
    }


def _compact_purchase_order_row(row: dict) -> dict:
    return {
        "material_code": row.get("material_code") or "",
        "product_name": row.get("product_name") or "",
        "spec_model": row.get("spec_model") or "",
        "supplier": row.get("supplier") or "",
        "quantity": row.get("quantity"),
        "unit_price": row.get("unit_price"),
        "purchase_currency": row.get("purchase_currency") or "",
        "goods_value": row.get("goods_value"),
    }


def preview_tax_certificate_pdf(
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    text: str | None = None,
    batch_name: str | None = None,
) -> dict:
    """预览解析进口完税凭证 PDF，不写库。"""

    return attachment_parse_service.preview_tax_certificate_pdf(
        source_name=source_name,
        file_path=file_path,
        file_url=file_url,
        text=text,
        batch_name=batch_name,
    )


def save_tax_certificate_parse_result(
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    text: str | None = None,
    batch_name: str | None = None,
) -> dict:
    """保存完税凭证解析结果到附件记录，不写成本字段。"""

    return attachment_parse_service.save_tax_certificate_parse_result(
        source_name=source_name,
        file_path=file_path,
        file_url=file_url,
        text=text,
        batch_name=batch_name,
    )


def list_tax_certificate_parse_records(batch_name: str | None = None, limit: int | None = 20) -> dict:
    """查询完税凭证解析快照摘要。"""

    return attachment_parse_service.list_tax_certificate_parse_records(
        batch_name=batch_name,
        limit=limit,
    )


def get_tax_certificate_parse_record(record_name: str | None = None) -> dict:
    """查询单条完税凭证解析快照详情。"""

    return attachment_parse_service.get_tax_certificate_parse_record(record_name=record_name)


def delete_tax_certificate_parse_records(
    batch_name: str | None = None,
    record_name: str | None = None,
    record_names_json: str | None = None,
) -> dict:
    """删除完税凭证解析快照记录。"""

    return attachment_parse_service.delete_tax_certificate_parse_records(
        batch_name=batch_name,
        record_name=record_name,
        record_names_json=record_names_json,
    )


def resolve_tax_certificate_reconciliation(
    record_name: str | None = None,
    resolution_action: str | None = None,
    adjusted_tax_total_mxn: float | str | None = None,
    remark: str | None = None,
) -> dict:
    """保存完税凭证差异人工处理结果，不写成本字段。"""

    return attachment_parse_service.resolve_tax_certificate_reconciliation(
        record_name=record_name,
        resolution_action=resolution_action,
        adjusted_tax_total_mxn=adjusted_tax_total_mxn,
        remark=remark,
    )


def _load_rows(rows_json: str | None) -> list[dict]:
    if not rows_json:
        return []

    loaded = json.loads(rows_json)
    if isinstance(loaded, list):
        return [row for row in loaded if isinstance(row, dict)]
    return []


def _load_json_list(rows_json: str | None) -> list[dict]:
    if not rows_json:
        return []

    loaded = json.loads(rows_json)
    if isinstance(loaded, dict):
        loaded = [loaded]
    if isinstance(loaded, list):
        return [row for row in loaded if isinstance(row, dict)]
    return []


def _load_blocks(blocks_json: str | None) -> list[dict]:
    if not blocks_json:
        return []

    loaded = json.loads(blocks_json)
    if isinstance(loaded, dict):
        loaded = [loaded]
    if isinstance(loaded, list):
        return [block for block in loaded if isinstance(block, dict)]
    return []


def _resolve_excel_file_path(*, file_path: str | None = None, file_url: str | None = None) -> Path:
    if file_path:
        path = Path(file_path).expanduser()
        if path.exists():
            return _ensure_supported_excel_path(path)
        raise FileNotFoundError(_("未找到 Excel 文件：{0}").format(file_path))

    if not file_url:
        raise ValueError(_("请传入 file_path 或 file_url。"))

    if frappe is None:
        path = Path(file_url).expanduser()
        if path.exists():
            return _ensure_supported_excel_path(path)
        raise FileNotFoundError(_("未连接 Frappe，且本地路径不存在：{0}").format(file_url))

    resolved_file_url = file_url
    if not resolved_file_url.startswith("/"):
        file_row = frappe.db.get_value("File", resolved_file_url, ["file_url"], as_dict=True)
        if file_row and file_row.get("file_url"):
            resolved_file_url = file_row["file_url"]

    if resolved_file_url.startswith("/private/files/"):
        relative_name = resolved_file_url.split("/private/files/", 1)[1]
        return _ensure_supported_excel_path(Path(frappe.get_site_path("private", "files", relative_name)))
    if resolved_file_url.startswith("/files/"):
        relative_name = resolved_file_url.split("/files/", 1)[1]
        return _ensure_supported_excel_path(Path(frappe.get_site_path("public", "files", relative_name)))

    path = Path(resolved_file_url).expanduser()
    if path.exists():
        return _ensure_supported_excel_path(path)
    raise FileNotFoundError(_("无法解析 Excel 文件路径：{0}").format(file_url))


def _ensure_supported_excel_path(path: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError(_("当前仅支持 .xlsx / .xlsm 格式的 Excel 文件。"))
    return path


def _fetch_dingtalk_attachment_content(download_uri: str, headers: dict | None = None) -> tuple[bytes, dict]:
    url = str(download_uri or "").strip()
    if not url:
        raise ValueError(_("缺少钉钉附件下载地址。"))
    request_headers = {"User-Agent": "overseas-costing/1.0"}
    if isinstance(headers, dict):
        request_headers.update({str(key): str(value) for key, value in headers.items() if value not in (None, "")})
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read()
            return content, {
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": int(response.headers.get("Content-Length") or len(content)),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"附件下载 HTTP {exc.code}: {body[:300]}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"附件下载网络失败：{exc}") from exc


def _dingtalk_thumbnail_media_id(parse_snapshot: dict) -> str:
    raw_attachment = parse_snapshot.get("raw_attachment") if isinstance(parse_snapshot.get("raw_attachment"), dict) else {}
    thumbnail = raw_attachment.get("thumbnail") if isinstance(raw_attachment.get("thumbnail"), dict) else {}
    return str(
        thumbnail.get("authMediaId")
        or thumbnail.get("mediaId")
        or parse_snapshot.get("authMediaId")
        or parse_snapshot.get("auth_media_id")
        or ""
    ).strip()


def _thumbnail_media_file_name(file_name: str, file_id: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return file_name
    return f"{Path(file_name).stem or file_id}_缩略图.png"


def _download_dingtalk_thumbnail_media(
    *,
    token: str,
    parse_snapshot: dict,
    file_name: str,
    file_id: str,
    attached_to_name: str,
):
    media_id = _dingtalk_thumbnail_media_id(parse_snapshot)
    if not media_id:
        raise RuntimeError("附件记录没有 thumbnail.authMediaId，无法走图片缩略图兜底下载。")
    media_url = (
        "https://oapi.dingtalk.com/media/downloadFile"
        f"?access_token={quote(str(token or ''), safe='')}&media_id={quote(media_id, safe='')}"
    )
    content, response_meta = _fetch_dingtalk_attachment_content(media_url)
    content_type = str(response_meta.get("content_type") or "").lower()
    if "json" in content_type or content.lstrip()[:1] in (b"{", b"["):
        detail = content.decode("utf-8", errors="replace")
        raise RuntimeError(f"钉钉缩略图媒体下载失败：{detail[:300]}")
    saved_file_name = _thumbnail_media_file_name(file_name, file_id)
    file_doc = _save_content_as_frappe_file(
        file_name=saved_file_name,
        content=content,
        attached_to_name=attached_to_name,
    )
    return file_doc, response_meta, saved_file_name, media_id


def _save_content_as_frappe_file(*, file_name: str, content: bytes, attached_to_name: str):
    if frappe is None:
        raise RuntimeError("当前未连接 Frappe。")
    from frappe.utils.file_manager import save_file

    try:
        return save_file(
            fname=file_name or "dingtalk-attachment",
            content=content,
            dt="Overseas Cost Attachment",
            dn=attached_to_name,
            is_private=1,
            decode=False,
        )
    except Exception as exc:
        if not _is_frappe_file_size_error(exc):
            raise
        return _save_large_content_as_private_file(
            file_name=file_name,
            content=content,
            attached_to_name=attached_to_name,
        )


def _is_frappe_file_size_error(exc: Exception) -> bool:
    text = str(exc)
    return "File size exceeded" in text or "maximum allowed size" in text


def _save_large_content_as_private_file(*, file_name: str, content: bytes, attached_to_name: str):
    safe_name = _safe_private_file_name(file_name or "dingtalk-attachment")
    private_dir = Path(frappe.get_site_path("private", "files"))
    private_dir.mkdir(parents=True, exist_ok=True)
    target_path = _deduplicate_private_file_path(private_dir / safe_name)
    target_path.write_bytes(content)
    file_url = f"/private/files/{target_path.name}"
    return frappe.get_doc(
        {
            "doctype": "File",
            "file_name": target_path.name,
            "file_url": file_url,
            "is_private": 1,
            "attached_to_doctype": "Overseas Cost Attachment",
            "attached_to_name": attached_to_name,
            "folder": "Home/Attachments",
        }
    ).insert(ignore_permissions=True)


def _safe_private_file_name(file_name: str) -> str:
    name = Path(str(file_name or "dingtalk-attachment")).name.strip() or "dingtalk-attachment"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)


def _deduplicate_private_file_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(_("无法生成不重名的附件文件名：{0}").format(path.name))


def _get_doc_value(doc, fieldname: str, default: str = ""):
    if isinstance(doc, dict):
        return doc.get(fieldname, default)
    return getattr(doc, fieldname, default)


def _source_document_manual_review(mapped_result: dict | None) -> dict:
    if not isinstance(mapped_result, dict):
        return {}
    review = mapped_result.get("source_document_review")
    return dict(review) if isinstance(review, dict) else {}


def _source_document_preview_cache(mapped_result: dict | None, file_url: str) -> dict:
    if not isinstance(mapped_result, dict):
        return {}
    preview = mapped_result.get(SOURCE_DOCUMENT_PREVIEW_CACHE_KEY)
    if not isinstance(preview, dict) or str(preview.get("file_url") or "") != str(file_url or ""):
        return {}
    if not isinstance(preview.get("classification"), dict):
        return {}
    return dict(preview)


def _save_source_document_preview(
    attachment_doc,
    mapped_result: dict,
    preview: dict,
    file_url: str,
) -> None:
    """保存附件识别快照，供财务复看；不修改附件类型或核算字段。"""

    snapshot = {
        "ok": True,
        "file_url": str(file_url or ""),
        "source_name": str(preview.get("source_name") or getattr(attachment_doc, "file_name", "") or ""),
        "file_ext": str(preview.get("file_ext") or ""),
        "extraction_method": str(preview.get("extraction_method") or ""),
        "classification": dict(preview.get("classification") or {}),
        "field_candidates": dict(preview.get("field_candidates") or {}),
        "purchase_order": dict(preview.get("purchase_order") or {}),
        "text_excerpt": str(preview.get("text_excerpt") or ""),
        "text_length": int(preview.get("text_length") or 0),
        "can_write_purchase_price": bool(preview.get("can_write_purchase_price")),
        "parsed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    mapped_result[SOURCE_DOCUMENT_PREVIEW_CACHE_KEY] = snapshot
    attachment_doc.parse_status = "Parsed"
    attachment_doc.mapped_result_json = _json_dumps(mapped_result)
    attachment_doc.save(ignore_permissions=True)
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()


def _preview_attachment_classification_for_review(
    attachment_doc,
    mapped_result: dict | None = None,
) -> tuple[dict, str]:
    file_url = str(getattr(attachment_doc, "file_url", "") or "").strip()
    if not file_url:
        return {}, "附件尚未下载到系统，已仅保存人工确认类型。"
    cached_preview = _source_document_preview_cache(mapped_result, file_url)
    if cached_preview:
        classification = cached_preview.get("classification")
        return (dict(classification) if isinstance(classification, dict) else {}), ""
    try:
        preview = attachment_parse_service.preview_source_document(
            source_name=str(getattr(attachment_doc, "file_name", "") or ""),
            file_url=file_url,
        )
    except Exception as exc:
        return {}, f"本次未能重新取得 OCR 结果：{exc}"
    classification = preview.get("classification")
    return (dict(classification) if isinstance(classification, dict) else {}), ""


def _build_source_document_manual_review(
    *,
    confirmed_type: str,
    remark: str | None = None,
    automatic_classification: dict | None = None,
    automatic_error: str = "",
) -> dict:
    config = SOURCE_DOCUMENT_MANUAL_TYPES[str(confirmed_type).strip()]
    confirmed_by = ""
    confirmed_at = ""
    if frappe is not None:
        session_user = getattr(getattr(frappe, "session", None), "user", None)
        if session_user and session_user != "Guest":
            confirmed_by = str(session_user)
        try:
            confirmed_at = str(frappe.utils.now())
        except Exception:
            confirmed_at = ""
    return {
        "status": "confirmed",
        "confirmed_type": str(confirmed_type).strip(),
        "confirmed_type_label": config["label"],
        "attachment_type": config["attachment_type"],
        "parse_targets": list(config["parse_targets"]),
        "next_step": config["next_step"],
        "remark": str(remark or "").strip(),
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "automatic_classification": dict(automatic_classification or {}),
        "automatic_error": automatic_error,
    }


def _merge_parse_targets(existing, additional) -> list[str]:
    values = []
    for source in (existing, additional):
        for value in source if isinstance(source, list) else []:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _append_source_document_review_remark(existing_remark: str | None, review: dict) -> str:
    line = f"人工确认附件类型：{review.get('confirmed_type_label') or ''}"
    if review.get("remark"):
        line += f"；备注：{review['remark']}"
    base = str(existing_remark or "").strip()
    return f"{base}\n{line}".strip() if base else line


def _source_document_review_audit_remark(attachment_doc, review: dict) -> str:
    file_name = str(getattr(attachment_doc, "file_name", "") or "未命名附件")
    detail = f"人工确认 OA 发起附件资料类型：{file_name} -> {review.get('confirmed_type_label') or ''}"
    if review.get("remark"):
        detail += f"；备注：{review['remark']}"
    return detail


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _extract_dingtalk_corp_id(value: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:[?&]|^)corpid=([^&#]+)", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _resolve_attachment_corp_id(attachment_doc, parse_snapshot: dict) -> str:
    env_value = (
        os.environ.get("DINGTALK_CORP_ID")
        or os.environ.get("DINGTALK_CORPID")
        or os.environ.get("DINGTALK_CORP")
        or ""
    )
    corp_id = _extract_dingtalk_corp_id(env_value) or str(env_value or "").strip()
    if corp_id:
        return corp_id

    for key in ("source_dingtalk_url", "official_url", "url", "open_url"):
        corp_id = _extract_dingtalk_corp_id(parse_snapshot.get(key))
        if corp_id:
            return corp_id

    batch_name = str(getattr(attachment_doc, "batch", "") or "").strip()
    if frappe is not None and batch_name:
        try:
            row = frappe.db.get_value(
                "Overseas Cost Batch",
                batch_name,
                ["source_dingtalk_url"],
                as_dict=True,
            )
        except Exception:
            row = None
        if row:
            corp_id = _extract_dingtalk_corp_id(row.get("source_dingtalk_url"))
            if corp_id:
                return corp_id
    return DEFAULT_DINGTALK_CORP_ID


def _resolve_attachment_user_id(attachment_doc, parse_snapshot: dict) -> str:
    """解析旧版钉钉附件下载必填的审批发起人 ID。"""

    for key in ("DINGTALK_ATTACHMENT_USER_ID", "DINGTALK_DOWNLOAD_USER_ID"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value

    for key in ("originator_userid", "originatorUserId", "user_id", "userId", "creator_id"):
        value = str(parse_snapshot.get(key) or "").strip()
        if value:
            return value

    batch_name = str(getattr(attachment_doc, "batch", "") or "").strip()
    if frappe is not None and batch_name:
        try:
            value = frappe.db.get_value("Overseas Cost Batch", batch_name, "source_creator_name")
        except Exception:
            value = ""
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _to_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_non_empty(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _has_source_value(value) -> bool:
    return str(value or "").strip() != ""


def _first_mapped_value(mapped_rows: list[dict], fieldname: str, default: float = 0.0) -> float:
    for row in mapped_rows:
        value = row.get(fieldname)
        if value not in (None, ""):
            return _to_float(value, default=default)
    return default


def _filter_doctype_values(doctype: str, values: dict, *, keep_doctype: bool = False) -> dict:
    if frappe is None:
        return dict(values)

    meta = frappe.get_meta(doctype)
    filtered = {
        fieldname: value
        for fieldname, value in values.items()
        if meta.has_field(fieldname) or (keep_doctype and fieldname == "doctype")
    }
    return filtered


def _coerce_item_numeric_defaults(values: dict) -> dict:
    normalized = dict(values)
    if normalized.get("unit"):
        normalized["unit"] = normalize_unit(normalized.get("unit"))
    for fieldname in NUMERIC_ITEM_FIELDS:
        if normalized.get(fieldname) in (None, ""):
            normalized[fieldname] = 0
    return normalized


def normalize_existing_item_units() -> dict:
    """把历史明细里的外语单位清洗成系统统一中文单位，可重复执行。"""

    if frappe is None:
        return {"updated_count": 0, "scanned_count": 0, "changed_units": []}

    rows = frappe.get_all(
        "Overseas Cost Item",
        filters=[["unit", "!=", ""]],
        fields=["name", "unit"],
        limit_page_length=0,
    )
    changed_units: list[dict] = []
    for row in rows:
        old_unit = row.get("unit")
        new_unit = normalize_unit(old_unit)
        if not new_unit or new_unit == old_unit:
            continue
        frappe.db.set_value(
            "Overseas Cost Item",
            row["name"],
            "unit",
            new_unit,
            update_modified=True,
        )
        changed_units.append({"name": row["name"], "old_unit": old_unit, "new_unit": new_unit})

    if changed_units:
        frappe.db.commit()

    return {
        "updated_count": len(changed_units),
        "scanned_count": len(rows),
        "changed_units": changed_units[:50],
    }


def backfill_oa_item_details_from_raw(batch_name: str | None = None, *, dry_run: bool = False) -> dict:
    """从钉钉 OA 快照补回历史明细漏掉的物料、重量与货值字段。

    数据源两级，按行优先：
    1. 行级 `raw_excel_json`（精确到行，优先）；
    2. 批次级 `extra_json.form_fields` 的货物信息表（老行快照为空时的回退，
       复用 `import_oa_logistics.extract_oa_goods_rows` 的解析口径）。

    只填空值：文本按空串判断，数量与物理属性按「非零才算已填」判断，
    避免覆盖财务手工录入的数据。
    """

    if frappe is None:
        return {"updated_count": 0, "scanned_count": 0, "changed_rows": []}

    filters: dict = {"source_type": "oa_logistics"}
    if batch_name:
        filters["batch"] = batch_name
    rows = frappe.get_all(
        "Overseas Cost Item",
        filters=filters,
        fields=[
            "name",
            "batch",
            "row_no",
            "material_code",
            "product_name",
            "product_name_es",
            "spec_model",
            "recipient",
            "quantity",
            "unit",
            "category",
            "purchase_currency",
            "gross_weight_kg",
            "goods_value",
            "raw_excel_json",
        ],
        limit_page_length=0,
    )

    batch_goods_rows: dict[str, dict[str, dict]] = {}

    def _batch_goods_index(target_batch: str) -> dict[str, dict]:
        """按物料编码索引批次级货物行，供行级快照缺失时回退。"""

        if target_batch in batch_goods_rows:
            return batch_goods_rows[target_batch]
        index: dict[str, dict] = {}
        batch_goods_rows[target_batch] = index
        if not target_batch:
            return index
        try:
            from overseas_costing.scripts.import_oa_logistics import extract_oa_goods_rows

            extra = _json_loads_dict(
                frappe.db.get_value("Overseas Cost Batch", target_batch, "extra_json")
            )
            if not isinstance(extra, dict):
                return index
            for goods_row in extract_oa_goods_rows(extra):
                code = str(goods_row.get("物料编码 Código de material") or "").strip()
                if code and code not in index:
                    index[code] = goods_row
        except Exception:
            # 回退源不可用时保持行级结果，不影响主流程。
            pass
        return index

    changed_rows: list[dict] = []
    fallback_count = 0
    for row in rows:
        raw_payload = _json_loads_dict(row.get("raw_excel_json"))
        used_fallback = False
        if not isinstance(raw_payload, dict) or not raw_payload:
            code = str(row.get("material_code") or "").strip()
            raw_payload = _batch_goods_index(str(row.get("batch") or "")).get(code) or {}
            used_fallback = bool(raw_payload)
        if not raw_payload:
            continue

        mapped = map_oa_row_to_item(raw_payload)
        updates: dict = {}
        source_quantity = _to_float(mapped.get("quantity"), default=0.0)
        current_quantity = _to_float(row.get("quantity"), default=0.0)
        if source_quantity and not current_quantity:
            updates["quantity"] = source_quantity
            updates["actual_shipped_qty"] = source_quantity

        source_unit = normalize_unit(mapped.get("unit"))
        current_unit = normalize_unit(row.get("unit"))
        if source_unit and source_unit != current_unit:
            updates["unit"] = source_unit
        for fieldname in ("product_name", "product_name_es", "spec_model", "recipient", "category", "purchase_currency"):
            source_value = mapped.get(fieldname)
            current_value = row.get(fieldname)
            if source_value not in (None, "") and str(source_value or "").strip() != str(current_value or "").strip():
                updates[fieldname] = source_value
        for fieldname in NUMERIC_BACKFILL_FIELDS:
            source_value = _to_float(mapped.get(fieldname), default=0.0)
            current_value = _to_float(row.get(fieldname), default=0.0)
            if source_value and not current_value:
                updates[fieldname] = source_value

        if not updates:
            continue
        if used_fallback:
            fallback_count += 1
        if not dry_run:
            frappe.db.set_value("Overseas Cost Item", row["name"], updates, update_modified=True)
        changed_rows.append(
            {
                "name": row["name"],
                "batch": row.get("batch"),
                "row_no": row.get("row_no"),
                "material_code": row.get("material_code"),
                "used_batch_fallback": used_fallback,
                "updates": updates,
            }
        )

    if changed_rows and not dry_run:
        frappe.db.commit()

    return {
        "updated_count": len(changed_rows),
        "scanned_count": len(rows),
        "batch_fallback_count": fallback_count,
        "dry_run": dry_run,
        "changed_rows": changed_rows[:50],
    }


def _values_equal_for_import(old_value, new_value) -> bool:
    if old_value in (None, "") and new_value in (None, ""):
        return True
    try:
        return abs(float(old_value) - float(new_value)) <= 0.000001
    except (TypeError, ValueError):
        return str(old_value or "").strip() == str(new_value or "").strip()


def _item_values_changed(item_name: str, values: dict) -> bool:
    if frappe is None:
        return True
    fieldnames = [fieldname for fieldname in values if fieldname not in {"doctype", "name"}]
    if not fieldnames:
        return False
    current = frappe.db.get_value("Overseas Cost Item", item_name, fieldnames, as_dict=True) or {}
    return any(not _values_equal_for_import(current.get(fieldname), value) for fieldname, value in values.items())


def _build_excel_block_preview(block: dict) -> dict:
    items = block.get("items") or []
    mapped_preview_items = [
        map_yuewei_excel_block_item_to_item(block, row, row_index=index + 1)
        for index, row in enumerate(items[:20])
    ]
    return {
        "batch_no": block.get("id") or block.get("waybillNo"),
        "source_sheet": block.get("sourceSheet"),
        "source_range": block.get("sourceRange"),
        "customs_no": block.get("customsNo"),
        "waybill_no": block.get("waybillNo"),
        "transport_mode": normalize_transport_mode(block.get("transportMode")),
        "business_type": normalize_business_type(
            block.get("businessType") or block.get("business_type") or block.get("transportMode"),
            transport_mode=block.get("transportMode"),
        ),
        "item_count": len(items),
        "mapped_preview_items": mapped_preview_items,
    }


def _resolve_or_create_excel_batch(
    *,
    block: dict,
    source_name: str,
    transport_mode: str,
    source_sheet: str | None,
    project_collection: str | None,
):
    is_attachment_detail = block.get("sourceTemplate") == "oa_attachment_detail"
    batch_no = _first_non_empty(block.get("batchNo"), block.get("id"), block.get("waybillNo"), source_name)
    existing_name = frappe.db.get_value("Overseas Cost Batch", {"batch_no": batch_no}, "name")
    source_meta = {key: value for key, value in block.items() if key != "items" and value not in (None, "")}
    waybill_no = block.get("waybillNo") or ("" if is_attachment_detail else batch_no)
    source_dingtalk_url = block.get("sourceDingtalkUrl") or block.get("dingtalkUrl") or block.get("officialUrl") or ""
    source_instance_id = block.get("sourceInstanceId") or extract_dingtalk_instance_id(source_dingtalk_url) or ""
    values = {
        "batch_no": batch_no,
        "customs_no": block.get("customsNo") or "",
        "waybill_no": waybill_no,
        "transport_mode": normalize_transport_mode(block.get("transportMode") or transport_mode) or "",
        "business_type": normalize_business_type(
            block.get("businessType") or block.get("business_type") or block.get("transportMode"),
            transport_mode=block.get("transportMode") or transport_mode,
        ) or normalize_business_type(transport_mode, transport_mode=transport_mode) or "",
        "project_collection": project_collection or block.get("projectCollection") or "",
        "source_type": "excel",
        "source_file_name": source_name,
        "source_sheet": source_sheet or block.get("sourceSheet") or "",
        "source_range": block.get("sourceRange") or "",
        "source_approval_no": block.get("sourceApprovalNo") or "",
        "source_instance_id": source_instance_id,
        "source_dingtalk_url": source_dingtalk_url,
        "status": "Imported",
        "import_remark": "Imported from Excel parser",
        "source_remark": block.get("remark") or "",
        "extra_json": _json_dumps(source_meta),
    }

    if existing_name:
        for fieldname in ("transport_mode", "business_type"):
            if not values[fieldname]:
                values.pop(fieldname)
        frappe.db.set_value("Overseas Cost Batch", existing_name, values, update_modified=True)
        return frappe.get_doc("Overseas Cost Batch", existing_name), "updated"

    values["doctype"] = "Overseas Cost Batch"
    return frappe.get_doc(values).insert(ignore_permissions=True), "created"


def _resolve_or_create_excel_version(
    *,
    batch_doc_name: str,
    version_type: str,
    fx_usd_to_rmb: float | None,
    fx_rmb_to_mxn: float,
):
    existing_name = frappe.db.get_value(
        "Overseas Cost Version",
        {"batch": batch_doc_name, "version_code": version_type},
        "name",
    )
    values = {
        "batch": batch_doc_name,
        "version_code": version_type,
        "version_type": version_type,
        "is_current": 1,
        "source_type": "Import",
        "fx_usd_to_rmb": fx_usd_to_rmb or 0,
        "fx_rmb_to_mxn": fx_rmb_to_mxn or DEFAULT_FX_RMB_TO_MXN,
    }

    existing_versions = frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=["name"],
        limit_page_length=500,
    )
    for row in existing_versions:
        frappe.db.set_value("Overseas Cost Version", row["name"], "is_current", 0, update_modified=False)

    if existing_name:
        frappe.db.set_value("Overseas Cost Version", existing_name, values, update_modified=True)
        return frappe.get_doc("Overseas Cost Version", existing_name), "updated"

    values["doctype"] = "Overseas Cost Version"
    return frappe.get_doc(values).insert(ignore_permissions=True), "created"


def _prepare_imported_item_values(
    mapped_row: dict,
    *,
    source_revision: str = "",
    include_stable_key: bool = False,
    batch_transport_mode: str = "",
) -> dict:
    """Attach explicit quantity/unit provenance to a normalized imported row."""

    values = prepare_item_transport(mapped_row, batch_transport_mode)
    unit = normalize_unit(values.get("purchase_uom") or values.get("unit")) or ""
    shipped_uom = normalize_unit(values.get("shipped_uom") or unit) or ""
    values["purchase_uom"] = unit
    values["shipped_uom"] = shipped_uom
    values["cost_output_uom"] = shipped_uom
    if values.get("unit_price") not in (None, "") and not values.get("unit_price_uom"):
        values["unit_price_uom"] = unit
    values["actual_shipped_qty_mode"] = (
        "EXPLICIT_SOURCE" if _to_float(values.get("actual_shipped_qty")) > 0 else "DEFAULT_PURCHASE"
    )
    values["actual_shipped_qty_source_revision"] = str(source_revision or "")
    if include_stable_key:
        values["stable_line_key"] = material_input_service.ensure_stable_line_key(values)
    return values


def _protect_existing_shipping_values(values: dict, *, source_actual_present: bool) -> dict:
    """Keep confirmed shipping facts when a normal source refresh omits them."""

    protected = dict(values or {})
    if not source_actual_present:
        for fieldname in (
            "actual_shipped_qty",
            "actual_shipped_qty_mode",
            "actual_shipped_qty_source_revision",
            "shipped_uom",
            "cost_output_uom",
        ):
            protected.pop(fieldname, None)
    return protected


def _upsert_excel_items(
    *,
    batch_doc_name: str,
    version_name: str,
    mapped_rows: list[dict],
    raw_rows: list,
) -> dict:
    upserted_items: list[str] = []
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    batch_transport_mode = frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "transport_mode") or ""
    for index, mapped_row in enumerate(mapped_rows):
        source_actual_present = _to_float(mapped_row.get("actual_shipped_qty")) > 0
        mapped_row = _coerce_item_numeric_defaults(mapped_row)
        row_no = mapped_row.get("row_no") or index + 1
        quantity = _to_float(mapped_row.get("quantity"))
        unit_price = _to_float(mapped_row.get("unit_price"))
        if mapped_row.get("goods_value") in (None, "") and unit_price and quantity:
            mapped_row["goods_value"] = unit_price * quantity

        values = {
            **_prepare_imported_item_values(
                mapped_row,
                batch_transport_mode=batch_transport_mode,
                source_revision=str(
                    mapped_row.get("dingtalk_instance_id")
                    or mapped_row.get("source_doc_no")
                    or mapped_row.get("source_file_name")
                    or ""
                ),
            ),
            "batch": batch_doc_name,
            "version": version_name,
            "row_no": row_no,
            "raw_excel_json": _json_dumps(raw_rows[index] if index < len(raw_rows) else mapped_row),
        }
        existing_name = frappe.db.get_value(
            "Overseas Cost Item",
            {"batch": batch_doc_name, "version": version_name, "row_no": row_no},
            "name",
        )
        if existing_name:
            values = _protect_existing_shipping_values(
                values,
                source_actual_present=source_actual_present,
            )
            filtered_values = _filter_doctype_values("Overseas Cost Item", values)
            if _item_values_changed(existing_name, filtered_values):
                frappe.db.set_value(
                    "Overseas Cost Item",
                    existing_name,
                    filtered_values,
                    update_modified=True,
                )
                updated_count += 1
            else:
                unchanged_count += 1
            upserted_items.append(existing_name)
            continue

        values["doctype"] = "Overseas Cost Item"
        values["stable_line_key"] = material_input_service.ensure_stable_line_key(values)
        values = _filter_doctype_values("Overseas Cost Item", values, keep_doctype=True)
        upserted_items.append(frappe.get_doc(values).insert(ignore_permissions=True).name)
        created_count += 1

    return {
        "item_names": upserted_items,
        "created_count": created_count,
        "updated_count": updated_count,
        "unchanged_count": unchanged_count,
        "total_count": len(upserted_items),
    }


def _upsert_default_allocation_rules(
    *,
    batch_doc_name: str,
    version_name: str,
    block: dict,
    mapped_rows: list[dict],
) -> list[str]:
    rule_specs = [
        {
            "rule_code": "china_misc_rmb",
            "expense_category": "China misc RMB",
            "allocation_basis": "goods_value",
            "currency": "RMB",
            "amount": _to_float(block.get("chinaMiscRmb")),
            "remark": "来自 Excel/OA 明细字段：中国运输及相关杂费 RMB",
            "priority_no": 10,
        },
        {
            "rule_code": "china_to_mexico_freight_rmb",
            "expense_category": "China to Mexico freight RMB",
            "allocation_basis": "gross_weight",
            "currency": "RMB",
            "amount": _to_float(
                _first_non_empty(
                    block.get("chinaToMexicoFreightRmb"),
                    _first_mapped_value(mapped_rows, "china_to_mexico_freight_rmb", default=0),
                )
            ),
            "remark": "来自国际物流 OA、货代账单或明细字段：中国到墨西哥运费 RMB",
            "priority_no": 20,
        },
        {
            "rule_code": "mexico_inland_misc_rmb",
            "expense_category": "Mexico inland and misc RMB",
            "allocation_basis": "gross_weight",
            "currency": "RMB",
            "amount": _to_float(
                _first_non_empty(
                    block.get("mexicoInlandMiscRmb"),
                    _first_mapped_value(mapped_rows, "mexico_inland_misc_rmb", default=0),
                )
            ),
            "remark": "来自清关资料、墨西哥本地费用资料或明细字段：墨西哥内陆运输+杂费 RMB",
            "priority_no": 30,
        },
    ]

    upserted_rules: list[str] = []
    for spec in rule_specs:
        existing_name = frappe.db.get_value(
            "Overseas Cost Allocation Rule",
            {"batch": batch_doc_name, "version": version_name, "rule_code": spec["rule_code"]},
            "name",
        )
        values = {
            **spec,
            "batch": batch_doc_name,
            "version": version_name,
            "basis_field": spec["allocation_basis"],
            "is_active": 1,
            "is_enabled": 1,
        }
        if existing_name:
            enabled = frappe.db.get_value("Overseas Cost Allocation Rule", existing_name, "is_enabled")
            if enabled in (0, "0", False):
                upserted_rules.append(existing_name)
                continue
            frappe.db.set_value("Overseas Cost Allocation Rule", existing_name, values, update_modified=True)
            upserted_rules.append(existing_name)
            continue

        values["doctype"] = "Overseas Cost Allocation Rule"
        upserted_rules.append(frappe.get_doc(values).insert(ignore_permissions=True).name)

    return upserted_rules


def _normalize_key(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _normalize_currency_code(value) -> str:
    normalized = _normalize_key(value).replace(" ", "")
    if not normalized:
        return "RMB"
    if any(token in normalized for token in ("rmb", "cny", "人民币")):
        return "RMB"
    if any(token in normalized for token in ("usd", "dólar", "dolar", "美元", "美金")):
        return "USD"
    if any(token in normalized for token in ("mxn", "peso", "pesos", "墨西哥")):
        return "MXN"
    return normalized.upper()


def _get_version_fx_context(version_name: str | None) -> dict:
    context = {
        "fx_usd_to_rmb": DEFAULT_FX_USD_TO_RMB,
        "fx_rmb_to_mxn": DEFAULT_FX_RMB_TO_MXN,
    }
    if frappe is None or not version_name:
        return context

    row = frappe.db.get_value(
        "Overseas Cost Version",
        version_name,
        ["fx_usd_to_rmb", "fx_rmb_to_mxn"],
        as_dict=True,
    )
    if row:
        fx_usd_to_rmb = _to_float(row.get("fx_usd_to_rmb")) or DEFAULT_FX_USD_TO_RMB
        fx_rmb_to_mxn = _to_float(row.get("fx_rmb_to_mxn")) or DEFAULT_FX_RMB_TO_MXN
        context["fx_usd_to_rmb"] = fx_usd_to_rmb
        context["fx_rmb_to_mxn"] = fx_rmb_to_mxn
        updates = {}
        if not _to_float(row.get("fx_usd_to_rmb")):
            updates["fx_usd_to_rmb"] = fx_usd_to_rmb
        if not _to_float(row.get("fx_rmb_to_mxn")):
            updates["fx_rmb_to_mxn"] = fx_rmb_to_mxn
        if updates:
            frappe.db.set_value("Overseas Cost Version", version_name, updates, update_modified=False)
    return context


def _amount_to_rmb(amount, currency, fx_context: dict) -> float:
    value = _to_float(amount, default=0.0)
    currency_code = _normalize_currency_code(currency)
    if currency_code == "USD":
        return value * (_to_float(fx_context.get("fx_usd_to_rmb")) or DEFAULT_FX_USD_TO_RMB)
    if currency_code == "MXN":
        return value / (_to_float(fx_context.get("fx_rmb_to_mxn")) or DEFAULT_FX_RMB_TO_MXN)
    return value


def _convert_purchase_updates_to_rmb(field_updates: dict, fx_context: dict) -> dict:
    converted = dict(field_updates or {})
    currency = converted.get("purchase_currency")
    if "unit_price" in converted:
        converted["unit_price"] = _amount_to_rmb(converted.get("unit_price"), currency, fx_context)
    if "goods_value" in converted:
        converted["goods_value"] = _amount_to_rmb(converted.get("goods_value"), currency, fx_context)
    return converted


def _build_preview_rows(
    raw_rows: list[dict],
    mapper: Callable[[dict], dict],
) -> list[dict]:
    rows = [mapper(row) for row in raw_rows]
    for row in rows:
        if row.get("unit"):
            row["unit"] = normalize_unit(row.get("unit"))
    return rows


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

    batch_row = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        ["current_version"],
        as_dict=True,
    )
    current_version = (batch_row or {}).get("current_version")
    if current_version:
        return current_version

    latest_rows = frappe.get_all(
        "Overseas Cost Version",
        filters={"batch": batch_doc_name},
        fields=["name"],
        order_by="modified desc",
        limit_page_length=1,
    )
    if latest_rows:
        return latest_rows[0]["name"]
    return None


def _get_batch_trace_row(batch_doc_name: str) -> dict:
    if frappe is None:
        return {}
    return frappe.db.get_value(
        "Overseas Cost Batch",
        batch_doc_name,
        [
            "name",
            "batch_no",
            "source_approval_no",
            "source_instance_id",
            "source_dingtalk_url",
            "extra_json",
        ],
        as_dict=True,
    ) or {}


def _invalid_batch_cost_write_response(
    batch_doc_name: str,
    version_name: str | None = None,
) -> dict | None:
    """Fail closed before any cost write when approval provenance is invalid."""

    if frappe is None or not batch_doc_name:
        return None
    try:
        batch = frappe.db.get_value(
            "Overseas Cost Batch",
            batch_doc_name,
            ["name", "source_approval_status", "extra_json"],
            as_dict=True,
        ) or {}
        items = _get_batch_items(batch_doc_name, version_name) if version_name else []
        from overseas_costing.services.batch_service import _build_invalid_business_state

        invalid_business = _build_invalid_business_state(batch, items)
    except Exception as exc:
        return {
            "ok": False,
            "invalid_business": True,
            "source_check_failed": True,
            "batch_doc_name": batch_doc_name,
            "version_name": version_name,
            "message": f"审批来源校验失败，已阻止成本写入：{exc}",
        }
    if not invalid_business.get("invalid"):
        return None
    return {
        "ok": False,
        "invalid_business": True,
        "invalid_business_scope": invalid_business.get("scope") or "",
        "batch_doc_name": batch_doc_name,
        "version_name": version_name,
        "message": invalid_business.get("message") or "当前批次存在已拒绝、撤销或终止的审批，不能写入成本数据。",
    }


def _get_linked_purchase_approvals_from_extra(extra_json: str | dict | None) -> list[dict]:
    payload = _json_loads_dict(extra_json)
    trace = payload.get("oa_logistics_trace") if isinstance(payload.get("oa_logistics_trace"), dict) else {}
    candidates = [
        payload.get("linked_purchase_approvals"),
        trace.get("linked_purchase_approvals"),
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
    linked: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for value in candidates:
        if not isinstance(value, list):
            continue
        for row in value:
            if not isinstance(row, dict):
                continue
            approval_no = str(row.get("approval_no") or row.get("source_approval_no") or row.get("business_id") or "").strip()
            instance_id = str(
                row.get("source_instance_id")
                or row.get("proc_inst_id")
                or row.get("procInstId")
                or row.get("instance_id")
                or ""
            ).strip()
            key = (approval_no, instance_id)
            if key in seen:
                continue
            seen.add(key)
            linked.append({**row, "approval_no": approval_no, "source_instance_id": instance_id})
    return linked


def _get_oa_trace_storage(extra_json: str | dict | None) -> tuple[dict, dict, bool]:
    payload = _json_loads_dict(extra_json)
    trace = payload.get("oa_logistics_trace")
    if isinstance(trace, dict):
        return payload, dict(trace), False
    return payload, dict(payload), True


def _save_oa_trace_storage(payload: dict, trace: dict, is_root_trace: bool) -> str:
    if is_root_trace:
        return _json_dumps({**payload, **trace})
    updated = dict(payload)
    updated["oa_logistics_trace"] = trace
    return _json_dumps(updated)


def _logistics_quote_snapshot(candidate: dict) -> dict:
    return {
        "carrier": str(candidate.get("carrier") or "").strip(),
        "amount": _to_float(candidate.get("amount")),
        "currency": str(candidate.get("currency") or "RMB").strip() or "RMB",
        "volume_m3": candidate.get("volume_m3"),
        "gross_weight_kg": candidate.get("gross_weight_kg"),
        "chargeable_weight_kg": candidate.get("chargeable_weight_kg"),
        "unit_freight_per_kg": candidate.get("unit_freight_per_kg"),
        "billing_method": str(candidate.get("billing_method") or "").strip(),
        "allocation_basis": str(candidate.get("allocation_basis") or "").strip(),
        "pre_delivery_date": str(candidate.get("pre_delivery_date") or "").strip(),
        "destination": str(candidate.get("destination") or "").strip(),
        "evidence_line": str(candidate.get("evidence_line") or "").strip(),
        "source_field": str(candidate.get("source_field") or "").strip(),
    }


def confirm_logistics_quote_candidate(
    *,
    batch_name: str,
    candidate_index: int | str,
    version_name: str | None = None,
    confirmation_note: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """人工确认 OA 物流报价候选后才写入整票物流费用分摊规则。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能确认物流报价。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": "未找到当前批次，无法确认物流报价。"}
    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "message": "当前批次没有可用成本版本，无法确认物流报价。"}
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        return invalid_response
    try:
        index = int(candidate_index)
    except (TypeError, ValueError):
        return {"ok": False, "message": "报价候选序号无效。"}

    batch_row = _get_batch_trace_row(batch_doc_name)
    payload, trace, is_root_trace = _get_oa_trace_storage(batch_row.get("extra_json"))
    explicit_fee = trace.get("logistics_fee") if isinstance(trace.get("logistics_fee"), dict) else {}
    from overseas_costing.scripts import import_oa_logistics

    if import_oa_logistics._parse_money_amount(explicit_fee.get("amount"), allow_zero=True) is not None:
        return {"ok": False, "message": "该审批单已填写明确物流费用，不能再用报价候选覆盖。"}

    candidates = trace.get("logistics_quote_candidates")
    if not isinstance(candidates, list) or not candidates:
        from overseas_costing.scripts.import_oa_logistics import extract_logistics_quote_candidates_from_approval

        candidates = extract_logistics_quote_candidates_from_approval({"form_fields": trace.get("form_fields") or {}})
    if index < 0 or index >= len(candidates) or not isinstance(candidates[index], dict):
        return {"ok": False, "message": "未找到所选物流报价候选。"}

    selected = _logistics_quote_snapshot(candidates[index])
    if selected["amount"] <= 0:
        return {"ok": False, "message": "所选报价未包含有效金额，不能生成分摊规则。"}

    carrier_label = selected["carrier"] or "未标注供应商"
    fee = {
        **selected,
        "source_label": "物流报价人工确认",
        "source_value": selected["evidence_line"] or f"{carrier_label} {selected['amount']} {selected['currency']}",
    }
    rule_result = import_oa_logistics._sync_oa_logistics_allocation_rule(
        batch_name=batch_doc_name,
        version_name=resolved_version_name,
        approval_item={"logistics_fee": fee},
        edit_token=edit_token,
        expected_modified=expected_modified,
        manual_entry=True,
    )
    if not rule_result.get("ok"):
        return {"ok": False, "message": rule_result.get("message") or "物流费用分摊规则保存失败。"}
    if rule_result.get("action") in {"protected", "retired"}:
        frappe.db.commit()
        return {"ok": True, "batch_name": batch_doc_name, "version_name": resolved_version_name,
                "rule_result": rule_result, "message": rule_result["message"]}

    old_confirmed = trace.get("confirmed_logistics_quote") if isinstance(trace.get("confirmed_logistics_quote"), dict) else {}
    operator = str(getattr(getattr(frappe, "session", None), "user", "") or "").strip()
    confirmed = {
        **selected,
        "candidate_index": index,
        "confirmation_note": str(confirmation_note or "").strip(),
        "confirmed_by": operator,
        "confirmed_at": str(frappe.utils.now_datetime()),
    }
    trace["logistics_quote_candidates"] = candidates
    trace["confirmed_logistics_quote"] = confirmed
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_doc_name,
        "extra_json",
        _save_oa_trace_storage(payload, trace, is_root_trace),
        update_modified=True,
    )
    _create_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        row_no=None,
        field_name="confirmed_logistics_quote",
        old_value=old_confirmed,
        new_value=confirmed,
        action_remark="人工确认物流报价候选并生成国际物流费用分摊规则",
    )
    _mark_batch_dirty(batch_doc_name)
    recalculate_result = _recalculate_after_writeback(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        enabled=True,
    )
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    message = f"已确认使用 {carrier_label} 报价 {selected['amount']:g} {selected['currency']}，并生成物流费用分摊规则。"
    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "batch_modified": str(frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified")),
        "version_name": resolved_version_name,
        "confirmed_quote": confirmed,
        "rule_result": rule_result,
        "recalculate_result": recalculate_result,
        "message": _message_with_recalculate_result(message, recalculate_result),
    }


def _normalize_manual_allocation_basis(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"goods_value", "gross_weight", "volume", "chargeable_weight"}:
        return text
    if "计费" in text or "charge" in text:
        return "chargeable_weight"
    if "体积" in text or "volume" in text or "cbm" in text:
        return "volume"
    if "货值" in text or "金额" in text or "goods" in text or "value" in text:
        return "goods_value"
    return "gross_weight"


def save_manual_logistics_quote(
    *,
    batch_name: str,
    amount,
    version_name: str | None = None,
    carrier: str | None = None,
    currency: str | None = "RMB",
    allocation_basis: str | None = "gross_weight",
    gross_weight_kg=None,
    chargeable_weight_kg=None,
    unit_freight_per_kg=None,
    billing_method: str | None = None,
    evidence_text: str | None = None,
    pre_delivery_date: str | None = None,
    destination: str | None = None,
    note: str | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
) -> dict:
    """手工补录物流报价，写入整票物流费用分摊规则并保留来源痕迹。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能保存物流报价补录。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {"ok": False, "message": "未找到当前批次，无法保存物流报价补录。"}
    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {"ok": False, "message": "当前批次没有可用成本版本，无法保存物流报价补录。"}
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        return invalid_response

    quote_amount = _to_float(amount)
    if quote_amount <= 0:
        return {"ok": False, "message": "请填写大于 0 的物流费用金额。"}

    from overseas_costing.scripts import import_oa_logistics

    normalized_currency = _normalize_currency_code(currency)
    normalized_basis = _normalize_manual_allocation_basis(allocation_basis)
    carrier_label = str(carrier or "").strip() or "未标注承运商/货代"
    evidence_line = str(evidence_text or note or "").strip()
    source_value = evidence_line or f"{carrier_label} {quote_amount:g} {normalized_currency}"
    fee = {
        "carrier": carrier_label,
        "amount": quote_amount,
        "currency": normalized_currency,
        "allocation_basis": normalized_basis,
        "gross_weight_kg": _to_float(gross_weight_kg) if gross_weight_kg not in (None, "") else None,
        "chargeable_weight_kg": _to_float(chargeable_weight_kg) if chargeable_weight_kg not in (None, "") else None,
        "unit_freight_per_kg": _to_float(unit_freight_per_kg) if unit_freight_per_kg not in (None, "") else None,
        "billing_method": str(billing_method or "").strip(),
        "pre_delivery_date": str(pre_delivery_date or "").strip(),
        "destination": str(destination or "").strip(),
        "evidence_line": evidence_line,
        "source_label": "物流报价人工补录",
        "source_field": "人工补录",
        "source_value": source_value,
    }
    rule_result = import_oa_logistics._sync_oa_logistics_allocation_rule(
        batch_name=batch_doc_name,
        version_name=resolved_version_name,
        approval_item={"logistics_fee": fee, "allocation_basis": normalized_basis},
        edit_token=edit_token,
        expected_modified=expected_modified,
        manual_entry=True,
    )
    if not rule_result.get("ok"):
        return {"ok": False, "message": rule_result.get("message") or "物流费用分摊规则保存失败。"}
    if rule_result.get("action") in {"protected", "retired"}:
        frappe.db.commit()
        return {"ok": True, "batch_name": batch_doc_name, "version_name": resolved_version_name,
                "rule_result": rule_result, "message": rule_result["message"]}

    batch_row = _get_batch_trace_row(batch_doc_name)
    payload, trace, is_root_trace = _get_oa_trace_storage(batch_row.get("extra_json"))
    old_confirmed = trace.get("confirmed_logistics_quote") if isinstance(trace.get("confirmed_logistics_quote"), dict) else {}
    operator = str(getattr(getattr(frappe, "session", None), "user", "") or "").strip()
    confirmed = {
        **_logistics_quote_snapshot(fee),
        "source": "manual_logistics_quote",
        "confirmation_note": str(note or "").strip(),
        "confirmed_by": operator,
        "confirmed_at": str(frappe.utils.now_datetime()),
    }
    trace["confirmed_logistics_quote"] = confirmed
    trace["manual_logistics_quote"] = confirmed
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_doc_name,
        "extra_json",
        _save_oa_trace_storage(payload, trace, is_root_trace),
        update_modified=True,
    )
    _create_audit_log(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        row_no=None,
        field_name="manual_logistics_quote",
        old_value=old_confirmed,
        new_value=confirmed,
        action_remark="人工补录物流报价并生成国际物流费用分摊规则",
    )
    _mark_batch_dirty(batch_doc_name)
    recalculate_result = _recalculate_after_writeback(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        enabled=True,
    )
    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    message = f"已保存 {carrier_label} 物流报价 {quote_amount:g} {normalized_currency}，并生成物流费用分摊规则。"
    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "batch_modified": str(frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "modified")),
        "version_name": resolved_version_name,
        "confirmed_quote": confirmed,
        "rule_result": rule_result,
        "recalculate_result": recalculate_result,
        "message": _message_with_recalculate_result(message, recalculate_result),
    }


def _normalize_purchase_summary(summary: dict) -> dict:
    from overseas_costing.scripts.import_oa_logistics import resolve_approval_decision

    mapped_items = summary.get("mapped_preview_items")
    if not isinstance(mapped_items, list):
        mapped_items = []
    process_status = summary.get("process_status") or summary.get("approval_status") or summary.get("status") or ""
    approval_result = summary.get("approval_result") or summary.get("result") or ""
    decision = resolve_approval_decision(process_status, approval_result)
    excluded = bool(summary.get("excluded") or decision.get("excluded"))
    effective_status = summary.get("effective_status") or decision.get("effective_status") or ""
    return {
        "ok": summary.get("ok", True),
        "source_approval_no": summary.get("source_approval_no") or summary.get("approval_no") or "",
        "source_instance_id": summary.get("source_instance_id") or "",
        "source_dingtalk_url": summary.get("source_dingtalk_url") or summary.get("official_url") or "",
        "approval_title": summary.get("approval_title") or "",
        "approval_status": effective_status,
        "process_status": decision.get("process_status") or "",
        "approval_result": decision.get("approval_result") or "",
        "effective_status": effective_status,
        "excluded": excluded,
        "purchase_currency": summary.get("purchase_currency") or "",
        "detail_row_count": summary.get("detail_row_count") or len(mapped_items),
        "mapped_preview_items": mapped_items,
        "message": summary.get("message") or "",
    }


def _load_purchase_summaries_from_json(purchase_summaries_json: str | None) -> list[dict]:
    return [_normalize_purchase_summary(summary) for summary in _load_json_list(purchase_summaries_json)]


def _build_purchase_summary_preview_row(summary: dict) -> dict:
    approval_no = summary.get("source_approval_no") or ""
    instance_id = summary.get("source_instance_id") or ""
    official_url = summary.get("source_dingtalk_url") or ""
    dingtalk_payload = build_dingtalk_order_payload(
        approval_no=approval_no,
        instance_id=instance_id,
        official_url=official_url,
    )
    return {
        "source_approval_no": approval_no,
        "source_instance_id": instance_id,
        "source_dingtalk_url": official_url,
        "approval_title": summary.get("approval_title"),
        "approval_status": summary.get("approval_status"),
        "process_status": summary.get("process_status") or "",
        "approval_result": summary.get("approval_result") or "",
        "effective_status": summary.get("effective_status") or summary.get("approval_status") or "",
        "excluded": bool(summary.get("excluded")),
        "message": summary.get("message") or "",
        "purchase_currency": summary.get("purchase_currency"),
        "detail_row_count": summary.get("detail_row_count"),
        "open_url": dingtalk_payload.get("open_url") or "",
        "open_mode": dingtalk_payload.get("open_mode") or "unavailable",
        "can_open": bool(dingtalk_payload.get("can_open")),
    }


def _resolve_dingtalk_env_file(env_file: str | None = None) -> str:
    explicit = str(env_file or os.environ.get("DINGTALK_ENV_FILE") or "").strip()
    if explicit:
        return explicit
    for candidate in DINGTALK_ENV_FILE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


def _pull_purchase_summaries_from_dingtalk(*, linked_approvals: list[dict], env_file: str | None = None) -> list[dict]:
    from overseas_costing.scripts.import_oa_logistics import (
        get_access_token,
        load_env_file,
        pull_linked_purchase_approval_details,
        resolve_approval_source_mode,
        _runtime_config_value,
    )

    resolved_env_file = _resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    api_style = _runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default="auto")
    token = ""
    if resolve_approval_source_mode() != "postgres":
        token = get_access_token(
            api_style=api_style,
            access_token=_runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
            corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
            client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
            client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
            app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
            app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
        )
    return [
        _normalize_purchase_summary(summary)
        for summary in pull_linked_purchase_approval_details(
            token=token,
            linked_approvals=linked_approvals,
            api_style=api_style,
            include_running=True,
        )
    ]


def _get_batch_items(batch_doc_name: str, version_name: str | None) -> list[dict]:
    if frappe is None or not version_name:
        return []

    return frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_doc_name, "version": version_name, "is_excluded": 0},
        fields=[
            "name",
            "row_no",
            "material_code",
            "product_name",
            "spec_model",
            "quantity",
            "unit_price",
            "purchase_currency",
            "goods_value",
            "excel_row_no",
            "actual_shipped_qty",
            "gross_weight_kg",
            "volume_m3",
            "volume_weight_kg",
            "chargeable_weight_kg",
            "source_type",
            "source_doc_no",
            "source_file_name",
            "source_attachment_id",
            "dingtalk_instance_id",
            "dingtalk_official_url",
            "parse_status",
        ],
        order_by="row_no asc",
        limit_page_length=5000,
    )


def _index_items(items: list[dict]) -> dict[str, dict[str, list[dict]]]:
    indexes = {
        "material_code": defaultdict(list),
        "product_name": defaultdict(list),
        "spec_model": defaultdict(list),
    }
    for item in items:
        for field in ITEM_KEY_FIELDS:
            key = _normalize_key(item.get(field))
            if key:
                indexes[field][key].append(item)
    return indexes


def _match_item(
    mapped_row: dict,
    indexes: dict[str, dict[str, list[dict]]],
    *,
    trust_unique_material_code: bool = False,
    keep_material_code_candidates_on_narrow_miss: bool = False,
) -> tuple[str, list[dict]]:
    for field in ITEM_KEY_FIELDS:
        key = _normalize_key(mapped_row.get(field))
        if not key:
            continue
        candidates = indexes[field].get(key, [])
        if candidates:
            if field == "material_code" and trust_unique_material_code and len(candidates) == 1:
                return field, candidates
            narrowed = _narrow_item_candidates(mapped_row, candidates)
            if (
                field == "material_code"
                and keep_material_code_candidates_on_narrow_miss
                and candidates
                and not narrowed
            ):
                return field, candidates
            return field, narrowed
    return "", []


def _source_quantity_for_sequence_match(mapped_row: dict) -> float | None:
    for fieldname in ("actual_shipped_qty", "quantity"):
        value = mapped_row.get(fieldname)
        if value not in (None, ""):
            return _to_float(value, default=0.0)
    return None


def _candidate_sort_key(candidate: dict) -> tuple[int, str]:
    try:
        row_no = int(candidate.get("row_no") or 0)
    except (TypeError, ValueError):
        row_no = 0
    return row_no, str(candidate.get("name") or "")


def _assign_sequence_candidate(match: dict, candidate: dict, strategy: str) -> None:
    match["assigned_candidate"] = candidate
    match["disambiguation_strategy"] = strategy


def _resolve_ambiguous_matches_by_sequence(raw_matches: list[dict]) -> None:
    """把来源多行和系统重复物料多行按数量/顺序拆开。

    只在“同一批来源行数量 = 同一组候选行数量”时生效，避免把不确定的单行强行写入。
    """

    groups: dict[tuple[str, tuple[str, ...]], list[dict]] = defaultdict(list)
    for match in raw_matches:
        candidates = match.get("candidates") or []
        if len(candidates) <= 1:
            continue
        candidate_names = tuple(sorted(str(candidate.get("name") or "") for candidate in candidates if candidate.get("name")))
        if len(candidate_names) != len(candidates):
            continue
        groups[(match.get("matched_by") or "", candidate_names)].append(match)

    for group in groups.values():
        first_candidates = group[0].get("candidates") or []
        candidates_by_name = {str(candidate.get("name")): candidate for candidate in first_candidates if candidate.get("name")}
        if len(group) != len(candidates_by_name):
            continue

        available = dict(candidates_by_name)
        for match in sorted(group, key=lambda row: row.get("source_index") or 0):
            source_quantity = _source_quantity_for_sequence_match(match.get("mapped_row") or {})
            if source_quantity is None:
                continue
            exact_candidates = [
                candidate
                for candidate in available.values()
                if _values_equal_for_import(candidate.get("quantity"), source_quantity)
            ]
            if len(exact_candidates) == 1:
                candidate = exact_candidates[0]
                _assign_sequence_candidate(match, candidate, "duplicate_quantity")
                available.pop(str(candidate.get("name")), None)

        remaining_matches = [match for match in group if not match.get("assigned_candidate")]
        remaining_candidates = sorted(available.values(), key=_candidate_sort_key)
        if len(remaining_matches) != len(remaining_candidates):
            continue
        for match, candidate in zip(sorted(remaining_matches, key=lambda row: row.get("source_index") or 0), remaining_candidates):
            _assign_sequence_candidate(match, candidate, "duplicate_row_order")


def _narrow_item_candidates(mapped_row: dict, candidates: list[dict]) -> list[dict]:
    spec_key = _normalize_key(mapped_row.get("spec_model"))
    if spec_key:
        spec_candidates = [candidate for candidate in candidates if _normalize_key(candidate.get("spec_model")) == spec_key]
        if spec_candidates:
            return spec_candidates
        if any(_normalize_key(candidate.get("spec_model")) for candidate in candidates):
            return []

    product_key = _normalize_key(mapped_row.get("product_name"))
    if product_key:
        product_candidates = [
            candidate for candidate in candidates if _normalize_key(candidate.get("product_name")) == product_key
        ]
        if product_candidates:
            return product_candidates
    return candidates


def _create_audit_log(
    *,
    batch_doc_name: str,
    version_name: str | None,
    row_no: int | None,
    field_name: str,
    old_value,
    new_value,
    action_remark: str,
) -> None:
    if frappe is None:
        return

    operator_name = ""
    session_user = getattr(getattr(frappe, "session", None), "user", None)
    if session_user and session_user != "Guest":
        operator_name = session_user

    frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_doc_name,
            "version": version_name,
            "action_type": "IMPORT",
            "field_name": field_name,
            "row_no": row_no,
            "old_value": "" if old_value is None else str(old_value),
            "new_value": "" if new_value is None else str(new_value),
            "operator_name": operator_name,
            "action_remark": action_remark,
        }
    ).insert(ignore_permissions=True)


def _update_item_fields(
    *,
    item_name: str,
    batch_doc_name: str,
    version_name: str | None,
    row_no: int | None,
    field_updates: dict,
    action_remark: str,
) -> list[dict]:
    if frappe is None:
        return []

    from overseas_costing.services.logistics_settlement.runtime import guard_legacy_item_write
    guard_legacy_item_write(batch_doc_name, version_name)

    item_doc = frappe.get_doc("Overseas Cost Item", item_name)
    changed_fields: list[dict] = []

    for field_name, new_value in field_updates.items():
        old_value = getattr(item_doc, field_name, None)
        if _values_equal_for_import(old_value, new_value):
            continue
        setattr(item_doc, field_name, new_value)
        changed_fields.append(
            {
                "field_name": field_name,
                "old_value": old_value,
                "new_value": new_value,
            }
        )

    if not changed_fields:
        return []

    item_doc.save(ignore_permissions=True)

    for changed in changed_fields:
        _create_audit_log(
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            row_no=row_no,
            field_name=changed["field_name"],
            old_value=changed["old_value"],
            new_value=changed["new_value"],
            action_remark=action_remark,
        )

    return changed_fields


def _mark_batch_dirty(batch_doc_name: str) -> None:
    if frappe is None:
        return
    frappe.db.set_value("Overseas Cost Batch", batch_doc_name, "status", "Dirty", update_modified=True)


def _recalculate_after_writeback(
    *,
    batch_doc_name: str | None,
    version_name: str | None,
    enabled: bool = True,
    commit_after_recalculate: bool = True,
) -> dict:
    if not enabled:
        return {"action": "skipped", "reason": "调用方关闭自动重算。"}
    if not batch_doc_name or not version_name:
        return {"action": "skipped", "reason": "当前批次或版本为空。"}
    try:
        from overseas_costing.services.calculate_service import recalculate_batch

        recalculate_kwargs = {"batch_name": batch_doc_name, "version_name": version_name}
        if not commit_after_recalculate:
            recalculate_kwargs["commit_after_recalculate"] = False
        result = recalculate_batch(**recalculate_kwargs)
        return {
            "action": "recalculated" if result.get("ok", True) else "failed",
            "ok": bool(result.get("ok", True)),
            "batch_name": batch_doc_name,
            "version_name": version_name,
            "summary_snapshot": result.get("summary_snapshot"),
            "message": result.get("message") or "",
        }
    except Exception as exc:
        return {
            "action": "failed",
            "ok": False,
            "batch_name": batch_doc_name,
            "version_name": version_name,
            "message": f"自动重算失败：{exc}",
        }


def _message_with_recalculate_result(message: str, recalculate_result: dict | None = None) -> str:
    recalculate_result = recalculate_result or {}
    if recalculate_result.get("action") == "recalculated" and recalculate_result.get("ok", True):
        return f"{message} 已自动重算。"
    if recalculate_result.get("action") == "failed":
        return f"{message} {recalculate_result.get('message') or '自动重算失败。'}"
    return message


def _mark_attachment_parsed(attachment_name: str | None, summary: dict) -> bool:
    if frappe is None or not attachment_name:
        return False

    exists = getattr(getattr(frappe, "db", None), "exists", None)
    if callable(exists):
        try:
            if not exists("Overseas Cost Attachment", attachment_name):
                return False
        except Exception:
            return False

    snapshot = {
        "source": "packing_list_writeback",
        "created_count": summary.get("created_count", 0),
        "updated_count": summary.get("updated_count", 0),
        "changed_field_count": summary.get("changed_field_count", 0),
        "skipped_count": summary.get("skipped_count", 0),
        "conflict_row_count": summary.get("conflict_row_count", 0),
        "unmatched_count": summary.get("unmatched_count", 0),
        "ambiguous_count": summary.get("ambiguous_count", 0),
    }
    parse_targets = summary.get("parse_targets")
    if parse_targets:
        snapshot["parse_targets"] = parse_targets
    for key in ("applied_rows", "created_rows"):
        if isinstance(summary.get(key), list):
            snapshot[key] = summary[key]
    try:
        frappe.db.set_value("Overseas Cost Attachment", attachment_name, "parse_status", "Parsed", update_modified=True)
        frappe.db.set_value(
            "Overseas Cost Attachment",
            attachment_name,
            "mapped_result_json",
            _json_dumps(snapshot),
            update_modified=True,
        )
    except Exception:
        return False
    return True


def _run_item_writeback(
    *,
    batch_name: str,
    version_name: str | None,
    mapped_rows: list[dict],
    update_builder: Callable[[dict, dict], dict],
    action_remark: str,
    trust_unique_material_code: bool = False,
    resolve_ambiguous_by_sequence: bool = False,
) -> dict:
    if frappe is None:
        return {
            "matched_count": 0,
            "updated_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": mapped_rows,
            "message": "当前未连接 Frappe，返回预览结果。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        return {
            "matched_count": 0,
            "updated_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": mapped_rows,
            "message": f"未找到批次：{batch_name}",
        }

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        return {
            "matched_count": 0,
            "updated_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": mapped_rows,
            "message": f"批次 {batch_name} 暂无可用版本，无法回填。",
        }

    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        return {
            **invalid_response,
            "matched_count": 0,
            "updated_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": mapped_rows,
        }

    items = _get_batch_items(batch_doc_name, resolved_version_name)
    indexes = _index_items(items)
    matched_rows: list[dict] = []
    ambiguous_rows: list[dict] = []
    unmatched_rows: list[dict] = []
    updated_count = 0

    raw_matches: list[dict] = []
    for source_index, mapped_row in enumerate(mapped_rows):
        matched_by, candidates = _match_item(
            mapped_row,
            indexes,
            trust_unique_material_code=trust_unique_material_code,
        )
        raw_matches.append(
            {
                "source_index": source_index,
                "mapped_row": mapped_row,
                "matched_by": matched_by,
                "candidates": candidates,
            }
        )

    if resolve_ambiguous_by_sequence:
        _resolve_ambiguous_matches_by_sequence(raw_matches)

    for match in raw_matches:
        mapped_row = match["mapped_row"]
        matched_by = match.get("matched_by") or ""
        candidates = match.get("candidates") or []
        if not candidates:
            unmatched_rows.append(mapped_row)
            continue
        if match.get("assigned_candidate"):
            candidates = [match["assigned_candidate"]]
        if len(candidates) > 1:
            ambiguous_rows.append(
                {
                    "matched_by": matched_by,
                    "mapped_row": mapped_row,
                    "candidate_row_nos": [candidate.get("row_no") for candidate in candidates],
                }
            )
            continue

        target = candidates[0]
        field_updates = update_builder(mapped_row, target)
        changed_fields = _update_item_fields(
            item_name=target["name"],
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=target.get("row_no"),
            field_updates=field_updates,
            action_remark=action_remark,
        )
        matched_rows.append(
            {
                "matched_by": matched_by,
                "target_item_name": target["name"],
                "target_row_no": target.get("row_no"),
                "mapped_row": mapped_row,
                "changed_fields": changed_fields,
            }
        )
        if changed_fields:
            updated_count += 1

    if updated_count:
        _mark_batch_dirty(batch_doc_name)

    return {
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "matched_count": len(matched_rows),
        "updated_count": updated_count,
        "ambiguous_count": len(ambiguous_rows),
        "unmatched_count": len(unmatched_rows),
        "matched_rows": matched_rows,
        "ambiguous_rows": ambiguous_rows,
        "unmatched_rows": unmatched_rows,
        "message": "已完成批次内明细匹配并执行可回填字段更新。" if updated_count else "已完成匹配，但当前没有字段变化需要写入。",
    }


def _compact_purchase_row(mapped_row: dict) -> dict:
    return {
        "material_code": mapped_row.get("material_code"),
        "product_name": mapped_row.get("product_name"),
        "spec_model": mapped_row.get("spec_model"),
        "quantity": mapped_row.get("quantity"),
        "unit": mapped_row.get("unit"),
        "unit_price": mapped_row.get("unit_price"),
        "goods_value": mapped_row.get("goods_value"),
        "purchase_currency": mapped_row.get("purchase_currency"),
        "source_approval_no": mapped_row.get("source_approval_no"),
        "source_instance_id": mapped_row.get("source_instance_id"),
    }


def _compact_packing_row(mapped_row: dict) -> dict:
    return {
        "excel_row_no": mapped_row.get("excel_row_no"),
        "material_code": mapped_row.get("material_code"),
        "product_name": mapped_row.get("product_name"),
        "spec_model": mapped_row.get("spec_model"),
        "quantity": mapped_row.get("quantity"),
        "actual_shipped_qty": mapped_row.get("actual_shipped_qty"),
        "gross_weight_kg": mapped_row.get("gross_weight_kg"),
        "volume_m3": mapped_row.get("volume_m3"),
        "volume_weight_kg": mapped_row.get("volume_weight_kg"),
        "chargeable_weight_kg": mapped_row.get("chargeable_weight_kg"),
        "unit_price": mapped_row.get("unit_price"),
        "purchase_currency": mapped_row.get("purchase_currency"),
        "goods_value": mapped_row.get("goods_value"),
        "hs_code": mapped_row.get("hs_code"),
        "source_remark": mapped_row.get("source_remark"),
        "source_doc_no": mapped_row.get("source_doc_no"),
        "source_file_name": mapped_row.get("source_file_name"),
        "source_attachment_id": mapped_row.get("source_attachment_id"),
    }


def _source_row_no(mapped_row: dict, source_index: int | None = None):
    row_no = mapped_row.get("excel_row_no") or mapped_row.get("row_no")
    if row_no not in (None, ""):
        return row_no
    if source_index is not None:
        return source_index + 1
    return ""


def _source_identity_text(mapped_row: dict) -> str:
    parts = [
        mapped_row.get("material_code"),
        mapped_row.get("product_name"),
        mapped_row.get("spec_model"),
    ]
    return " / ".join(str(part).strip() for part in parts if str(part or "").strip()) or "未识别物料"


def _diagnose_unmatched_source_row(
    mapped_row: dict,
    *,
    matched_by: str = "",
    source_index: int | None = None,
    compact_row: Callable[[dict], dict] | None = None,
) -> dict:
    compact = dict(compact_row(mapped_row) if compact_row else mapped_row)
    has_code = _has_source_value(mapped_row.get("material_code"))
    has_name = _has_source_value(mapped_row.get("product_name"))
    has_spec = _has_source_value(mapped_row.get("spec_model"))

    if not has_code and not has_name and not has_spec:
        reason = "来源行缺少物料编码、名称和规格，系统无法判断对应物料。"
        suggestion = "检查附件表头是否识别正确，或在源文件补齐物料编码/名称后重新解析。"
    elif matched_by == "material_code":
        reason = "物料编码在系统中有候选，但规格或名称不一致，未自动写入。"
        suggestion = "核对规格型号；如果确实是新物料，先新增物料行后重新解析。"
    elif has_code:
        reason = "当前批次没有相同物料编码。"
        suggestion = "确认该物料是否属于当前批次；属于的话先新增物料或修正系统编码。"
    else:
        reason = "来源行没有物料编码，仅凭名称或规格未匹配到系统物料。"
        suggestion = "优先补物料编码；名称相近但叫法不同的，后续走人工归并后再匹配。"

    compact.update(
        {
            "source_row_no": _source_row_no(mapped_row, source_index),
            "source_identity": _source_identity_text(mapped_row),
            "reason": reason,
            "suggestion": suggestion,
        }
    )
    return compact


def _diagnose_ambiguous_source_row(
    mapped_row: dict,
    *,
    matched_by: str,
    candidates: list[dict],
    source_index: int | None = None,
    compact_row: Callable[[dict], dict] | None = None,
) -> dict:
    candidate_row_nos = [candidate.get("row_no") for candidate in candidates if candidate.get("row_no") not in (None, "")]
    compact = dict(compact_row(mapped_row) if compact_row else mapped_row)
    compact.update(
        {
            "source_row_no": _source_row_no(mapped_row, source_index),
            "source_identity": _source_identity_text(mapped_row),
            "matched_by": matched_by,
            "candidate_row_nos": candidate_row_nos,
            "reason": f"同一来源行匹配到多条系统物料：第 {'、'.join(str(row_no) for row_no in candidate_row_nos) or '--'} 行。",
            "suggestion": "补齐规格、数量或物料编码后重新解析；无法区分时由人工在表格中修正。",
        }
    )
    return compact


def _build_purchase_updates_for_preview(mapped_row: dict, _target: dict) -> dict:
    return {
        "unit_price": mapped_row.get("unit_price"),
        "purchase_currency": mapped_row.get("purchase_currency"),
        "goods_value": mapped_row.get("goods_value"),
        "source_type": mapped_row.get("source_type"),
        "source_doc_no": mapped_row.get("source_approval_no") or mapped_row.get("source_instance_id") or "",
        "dingtalk_instance_id": mapped_row.get("source_instance_id") or "",
        "dingtalk_official_url": mapped_row.get("source_dingtalk_url") or "",
        "parse_status": "SUCCESS",
    }


def _target_purchase_quantity(target: dict) -> float:
    actual_quantity = _to_float(target.get("actual_shipped_qty"), default=0.0)
    if actual_quantity:
        return actual_quantity
    return _to_float(target.get("quantity"), default=0.0)


def _fanout_purchase_row_by_material_code(
    mapped_row: dict,
    *,
    matched_by: str,
    candidates: list[dict],
) -> list[dict]:
    if matched_by != "material_code" or len(candidates) <= 1:
        return []

    material_code_key = _normalize_key(mapped_row.get("material_code"))
    if not material_code_key:
        return []
    if any(_normalize_key(candidate.get("material_code")) != material_code_key for candidate in candidates):
        return []

    unit_price = _to_float(mapped_row.get("unit_price"), default=0.0)
    if not unit_price:
        return []

    fanout_rows: list[dict] = []
    for target in sorted(candidates, key=_candidate_sort_key):
        target_quantity = _target_purchase_quantity(target)
        row_for_target = {
            **mapped_row,
            "quantity": target_quantity or mapped_row.get("quantity"),
            "_fanout_source_quantity": mapped_row.get("quantity"),
            "_fanout_strategy": "material_code_split_by_target_quantity",
        }
        if target_quantity:
            row_for_target["goods_value"] = round(unit_price * target_quantity, 6)
        fanout_rows.append(
            {
                "target": target,
                "mapped_row": row_for_target,
                "disambiguation_strategy": "material_code_split_by_target_quantity",
            }
        )
    return fanout_rows


def _currency_group_key(value) -> str:
    return _normalize_key(value) or "unknown"


def _unique_join(values: list) -> str:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return "、".join(seen)


def _sum_purchase_rows(rows: list[dict], fieldname: str) -> float:
    return sum(_to_float((row.get("mapped_row") or {}).get(fieldname), default=0.0) for row in rows)


def _select_purchase_rows_for_target(rows: list[dict]) -> tuple[list[dict], list[dict], str]:
    if len(rows) <= 1:
        return rows, [], "single"

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        mapped_row = row.get("mapped_row") or {}
        groups[_currency_group_key(mapped_row.get("purchase_currency"))].append(row)

    if len(groups) <= 1:
        return rows, [], "same_currency"

    target_quantity = _to_float(rows[0].get("target_quantity"), default=0.0)

    def score(group_rows: list[dict]) -> tuple[float, float]:
        group_quantity = _sum_purchase_rows(group_rows, "quantity")
        group_goods_value = _sum_purchase_rows(group_rows, "goods_value")
        quantity_delta = abs(group_quantity - target_quantity) if target_quantity else 0
        return (quantity_delta, -group_goods_value)

    selected = min(groups.values(), key=score)
    selected_ids = {id(row) for row in selected}
    ignored = [row for row in rows if id(row) not in selected_ids]
    return selected, ignored, "closest_quantity_currency_group"


def _build_aggregated_purchase_updates(rows: list[dict]) -> tuple[dict, dict]:
    selected_rows, ignored_rows, strategy = _select_purchase_rows_for_target(rows)
    if not selected_rows:
        return {}, {"strategy": "empty", "selected_count": 0, "ignored_count": len(ignored_rows)}

    mapped_rows = [row.get("mapped_row") or {} for row in selected_rows]
    total_source_quantity = sum(_to_float(row.get("quantity"), default=0.0) for row in mapped_rows)
    total_source_goods_value = sum(_to_float(row.get("goods_value"), default=0.0) for row in mapped_rows)
    target_quantity = _to_float(selected_rows[0].get("target_quantity"), default=0.0)
    unit_price = total_source_goods_value / total_source_quantity if total_source_goods_value and total_source_quantity else 0
    if not unit_price:
        unit_price = _to_float(mapped_rows[0].get("unit_price"), default=0.0)
    goods_value = unit_price * target_quantity if unit_price and target_quantity else total_source_goods_value
    purchase_currency = mapped_rows[0].get("purchase_currency") or ""

    updates = {
        "unit_price": unit_price,
        "purchase_currency": purchase_currency,
        "goods_value": goods_value,
    }
    meta = {
        "strategy": strategy,
        "selected_count": len(selected_rows),
        "ignored_count": len(ignored_rows),
        "selected_source_approval_no": _unique_join([row.get("source_approval_no") for row in mapped_rows]),
        "ignored_source_approval_no": _unique_join([(row.get("mapped_row") or {}).get("source_approval_no") for row in ignored_rows]),
        "source_quantity": total_source_quantity,
        "target_quantity": target_quantity,
    }
    return updates, meta


def _field_change_status(field_name: str, old_value, new_value, numeric_zero_fillable_fields: set[str] | None = None) -> str:
    if new_value in (None, ""):
        return "empty_source"
    if old_value in (None, ""):
        return "fillable"
    zero_fields = numeric_zero_fillable_fields or {"unit_price", "goods_value"}
    if field_name in zero_fields and _to_float(old_value, default=0) == 0:
        return "fillable"
    if _values_equal_for_import(old_value, new_value):
        return "same"
    return "conflict"


def _build_proposed_changes(
    target: dict,
    field_updates: dict,
    *,
    field_labels: dict[str, str] | None = None,
    business_fields: tuple[str, ...] | set[str] | None = None,
    numeric_zero_fillable_fields: set[str] | None = None,
) -> list[dict]:
    resolved_labels = field_labels or PURCHASE_FIELD_LABELS
    resolved_business_fields = set(business_fields or PURCHASE_WRITEBACK_FIELDS)
    changes: list[dict] = []
    for field_name, new_value in field_updates.items():
        old_value = target.get(field_name)
        status = _field_change_status(
            field_name,
            old_value,
            new_value,
            numeric_zero_fillable_fields=numeric_zero_fillable_fields,
        )
        changes.append(
            {
                "field_name": field_name,
                "field_label": resolved_labels.get(field_name, field_name),
                "old_value": old_value,
                "new_value": new_value,
                "status": status,
                "is_business_field": field_name in resolved_business_fields,
            }
        )
    return changes


def _preview_item_writeback(
    *,
    batch_name: str,
    version_name: str | None,
    mapped_rows: list[dict],
    update_builder: Callable[[dict, dict], dict],
    field_labels: dict[str, str] | None = None,
    business_fields: tuple[str, ...] | set[str] | None = None,
    compact_row: Callable[[dict], dict] | None = None,
    numeric_zero_fillable_fields: set[str] | None = None,
    preview_message_prefix: str = "采购支出 OA",
    fillable_message: str = "可写入匹配行的业务字段。",
    trust_unique_material_code: bool = False,
    resolve_ambiguous_by_sequence: bool = False,
    fanout_material_code_purchase_rows: bool = False,
) -> dict:
    compact = compact_row or _compact_purchase_row
    if frappe is None:
        unmatched = [
            _diagnose_unmatched_source_row(row, source_index=index, compact_row=compact)
            for index, row in enumerate(mapped_rows)
        ]
        return {
            "dry_run": True,
            "matched_count": 0,
            "fillable_row_count": 0,
            "writable_row_count": 0,
            "conflict_row_count": 0,
            "same_row_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": unmatched,
            "match_diagnostics": unmatched,
            "message": "当前未连接 Frappe，仅完成来源行解析，无法匹配批次 SKU。",
        }

    batch_doc_name = _resolve_batch_name(batch_name)
    if not batch_doc_name:
        unmatched = [
            _diagnose_unmatched_source_row(row, source_index=index, compact_row=compact)
            for index, row in enumerate(mapped_rows)
        ]
        return {
            "matched_count": 0,
            "fillable_row_count": 0,
            "writable_row_count": 0,
            "conflict_row_count": 0,
            "same_row_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": unmatched,
            "match_diagnostics": unmatched,
            "message": f"未找到批次：{batch_name}",
        }

    resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not resolved_version_name:
        unmatched = [
            _diagnose_unmatched_source_row(row, source_index=index, compact_row=compact)
            for index, row in enumerate(mapped_rows)
        ]
        return {
            "batch_doc_name": batch_doc_name,
            "matched_count": 0,
            "fillable_row_count": 0,
            "writable_row_count": 0,
            "conflict_row_count": 0,
            "same_row_count": 0,
            "ambiguous_count": 0,
            "unmatched_count": len(mapped_rows),
            "matched_rows": [],
            "ambiguous_rows": [],
            "unmatched_rows": unmatched,
            "match_diagnostics": unmatched,
            "message": f"批次 {batch_name} 暂无可用版本，无法匹配。",
        }

    items = _get_batch_items(batch_doc_name, resolved_version_name)
    indexes = _index_items(items)
    matched_rows: list[dict] = []
    ambiguous_rows: list[dict] = []
    unmatched_rows: list[dict] = []

    raw_matches: list[dict] = []
    for source_index, mapped_row in enumerate(mapped_rows):
        matched_by, candidates = _match_item(
            mapped_row,
            indexes,
            trust_unique_material_code=trust_unique_material_code,
            keep_material_code_candidates_on_narrow_miss=fanout_material_code_purchase_rows,
        )
        raw_matches.append(
            {
                "source_index": source_index,
                "mapped_row": mapped_row,
                "matched_by": matched_by,
                "candidates": candidates,
            }
        )

    if resolve_ambiguous_by_sequence:
        _resolve_ambiguous_matches_by_sequence(raw_matches)

    def append_matched_row(match: dict, target: dict, row_for_target: dict, strategy: str = "") -> None:
        proposed_changes = _build_proposed_changes(
            target,
            update_builder(row_for_target, target),
            field_labels=field_labels,
            business_fields=business_fields,
            numeric_zero_fillable_fields=numeric_zero_fillable_fields,
        )
        business_changes = [change for change in proposed_changes if change["is_business_field"]]
        matched_rows.append(
            {
                "matched_by": match.get("matched_by") or "",
                "target_item_name": target.get("name"),
                "target_row_no": target.get("row_no"),
                "target_material_code": target.get("material_code"),
                "target_product_name": target.get("product_name"),
                "target_spec_model": target.get("spec_model"),
                "target_quantity": target.get("quantity"),
                "mapped_row": compact(row_for_target),
                "disambiguation_strategy": strategy or match.get("disambiguation_strategy") or "",
                "proposed_changes": proposed_changes,
                "business_changes": business_changes,
                "has_fillable": any(change["status"] == "fillable" for change in business_changes),
                "has_conflict": any(change["status"] == "conflict" for change in business_changes),
                "all_business_same": bool(business_changes)
                and all(change["status"] == "same" for change in business_changes),
            }
        )

    for match in raw_matches:
        mapped_row = match["mapped_row"]
        matched_by = match.get("matched_by") or ""
        candidates = match.get("candidates") or []
        if not candidates:
            unmatched_rows.append(
                _diagnose_unmatched_source_row(
                    mapped_row,
                    matched_by=matched_by,
                    source_index=match.get("source_index"),
                    compact_row=compact,
                )
            )
            continue
        if match.get("assigned_candidate"):
            candidates = [match["assigned_candidate"]]
        if len(candidates) > 1:
            fanout_rows = (
                _fanout_purchase_row_by_material_code(
                    mapped_row,
                    matched_by=matched_by,
                    candidates=candidates,
                )
                if fanout_material_code_purchase_rows
                else []
            )
            if fanout_rows:
                for fanout_row in fanout_rows:
                    append_matched_row(
                        match,
                        fanout_row["target"],
                        fanout_row["mapped_row"],
                        fanout_row.get("disambiguation_strategy") or "",
                    )
                continue
            diagnosis = _diagnose_ambiguous_source_row(
                mapped_row,
                matched_by=matched_by,
                candidates=candidates,
                source_index=match.get("source_index"),
                compact_row=compact,
            )
            ambiguous_rows.append(
                {
                    "matched_by": matched_by,
                    "mapped_row": compact(mapped_row),
                    "candidate_row_nos": [candidate.get("row_no") for candidate in candidates],
                    "source_row_no": diagnosis.get("source_row_no"),
                    "source_identity": diagnosis.get("source_identity"),
                    "reason": diagnosis.get("reason"),
                    "suggestion": diagnosis.get("suggestion"),
                    "candidate_items": [
                        {
                            "name": candidate.get("name"),
                            "row_no": candidate.get("row_no"),
                            "material_code": candidate.get("material_code"),
                            "product_name": candidate.get("product_name"),
                            "spec_model": candidate.get("spec_model"),
                        }
                        for candidate in candidates[:10]
                    ],
                }
            )
            continue

        append_matched_row(match, candidates[0], mapped_row)

    fillable_row_count = sum(1 for row in matched_rows if row["has_fillable"])
    conflict_row_count = sum(1 for row in matched_rows if row["has_conflict"])
    same_row_count = sum(1 for row in matched_rows if row["all_business_same"])
    writable_row_count = sum(
        1
        for row in matched_rows
        if any(change["status"] in {"fillable", "conflict"} for change in row.get("business_changes") or [])
    )
    message = f"{preview_message_prefix} 预览已完成，当前未写入任何成本字段。"
    if conflict_row_count:
        message += " 发现系统已有值与来源数据不一致，写入时将按来源字段更新并保留修改记录。"
    elif writable_row_count:
        message += f" {fillable_message}"

    return {
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "matched_count": len(matched_rows),
        "fillable_row_count": fillable_row_count,
        "writable_row_count": writable_row_count,
        "conflict_row_count": conflict_row_count,
        "same_row_count": same_row_count,
        "ambiguous_count": len(ambiguous_rows),
        "unmatched_count": len(unmatched_rows),
        "matched_rows": matched_rows,
        "ambiguous_rows": ambiguous_rows,
        "unmatched_rows": unmatched_rows,
        "match_diagnostics": [
            *[
                {
                    "type": "ambiguous",
                    "source_row_no": row.get("source_row_no"),
                    "source_identity": row.get("source_identity"),
                    "reason": row.get("reason"),
                    "suggestion": row.get("suggestion"),
                }
                for row in ambiguous_rows
            ],
            *[
                {
                    "type": "unmatched",
                    "source_row_no": row.get("source_row_no"),
                    "source_identity": row.get("source_identity"),
                    "reason": row.get("reason"),
                    "suggestion": row.get("suggestion"),
                }
                for row in unmatched_rows
            ],
        ],
        "message": message,
    }


def preview_linked_purchase_expense_oa(
    *,
    batch_name: str,
    version_name: str | None = None,
    env_file: str | None = None,
    linked_purchase_json: str | None = None,
    purchase_summaries_json: str | None = None,
    trusted_server_payload: bool = False,
) -> dict:
    """预览当前批次关联采购支出 OA 能补哪些采购价格字段，不写入数据。"""

    if frappe is not None and not trusted_server_payload and (
        env_file or linked_purchase_json or purchase_summaries_json
    ):
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "message": "审批来源数据必须由服务器从 PostgreSQL 读取，不能使用浏览器提交的审批明细。",
        }

    batch_doc_name = _resolve_batch_name(batch_name) if frappe is not None else None
    batch_trace = _get_batch_trace_row(batch_doc_name) if batch_doc_name else {"batch_no": batch_name}
    linked_approvals = _load_json_list(linked_purchase_json) or _get_linked_purchase_approvals_from_extra(
        batch_trace.get("extra_json")
    )
    purchase_summaries = _load_purchase_summaries_from_json(purchase_summaries_json)

    if not linked_approvals and not purchase_summaries:
        return {
            "ok": True,
            "dry_run": frappe is None,
            "batch_name": batch_name,
            "batch_doc_name": batch_doc_name,
            "batch_no": batch_trace.get("batch_no") or batch_name,
            "linked_purchase_count": 0,
            "purchase_summary_count": 0,
            "excluded_purchase_summary_count": 0,
            "excluded_purchase_summaries": [],
            "mapped_purchase_row_count": 0,
            "writeback_preview": {
                "matched_count": 0,
                "fillable_row_count": 0,
                "writable_row_count": 0,
                "conflict_row_count": 0,
                "same_row_count": 0,
                "ambiguous_count": 0,
                "unmatched_count": 0,
                "matched_rows": [],
                "ambiguous_rows": [],
                "unmatched_rows": [],
                "message": "当前批次没有关联采购支出审批单。",
            },
            "message": "当前批次没有关联采购支出审批单。",
        }

    if not purchase_summaries:
        purchase_summaries = _pull_purchase_summaries_from_dingtalk(
            linked_approvals=linked_approvals,
            env_file=env_file,
        )

    excluded_purchase_summaries = [summary for summary in purchase_summaries if summary.get("excluded")]
    active_purchase_summaries = [summary for summary in purchase_summaries if not summary.get("excluded")]
    mapped_rows: list[dict] = []
    for summary in active_purchase_summaries:
        source_approval_no = summary.get("source_approval_no") or ""
        source_instance_id = summary.get("source_instance_id") or ""
        source_dingtalk_url = summary.get("source_dingtalk_url") or ""
        for row in summary.get("mapped_preview_items") or []:
            if not isinstance(row, dict):
                continue
            mapped_rows.append(
                {
                    **row,
                    "source_approval_no": row.get("source_approval_no") or source_approval_no,
                    "source_instance_id": row.get("source_instance_id") or source_instance_id,
                    "source_dingtalk_url": row.get("source_dingtalk_url") or source_dingtalk_url,
                    "source_type": row.get("source_type") or "PURCHASE_EXPENSE_OA",
                }
            )

    preview_version_name = _resolve_version_name(batch_doc_name, version_name) if batch_doc_name else version_name
    fx_context = _get_version_fx_context(preview_version_name)

    def build_purchase_updates(mapped_row: dict, target: dict) -> dict:
        return _convert_purchase_updates_to_rmb(
            _build_purchase_updates_for_preview(mapped_row, target),
            fx_context,
        )

    writeback_preview = _preview_item_writeback(
        batch_name=batch_doc_name or batch_name,
        version_name=version_name,
        mapped_rows=mapped_rows,
        update_builder=build_purchase_updates,
        fillable_message="可写入匹配行的单价Precio、币种Moneda、总金额Monto Total。",
        trust_unique_material_code=True,
        fanout_material_code_purchase_rows=True,
    )

    return {
        "ok": True,
        "dry_run": frappe is None,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name or writeback_preview.get("batch_doc_name"),
        "batch_no": batch_trace.get("batch_no") or batch_name,
        "version_name": writeback_preview.get("version_name") or version_name,
        "linked_purchase_count": len(linked_approvals),
        "linked_purchase_approvals": linked_approvals,
        "purchase_summary_count": len(purchase_summaries),
        "purchase_summaries": [_build_purchase_summary_preview_row(summary) for summary in purchase_summaries],
        "excluded_purchase_summary_count": len(excluded_purchase_summaries),
        "excluded_purchase_summaries": [
            _build_purchase_summary_preview_row(summary) for summary in excluded_purchase_summaries
        ],
        "mapped_purchase_row_count": len(mapped_rows),
        "mapped_preview_items": [_compact_purchase_row(row) for row in mapped_rows[:20]],
        "writeback_targets": [PURCHASE_FIELD_LABELS[field] for field in PURCHASE_WRITEBACK_FIELDS],
        "writeback_preview": writeback_preview,
        "message": writeback_preview.get("message") or "采购支出 OA 回填预览已生成，当前未写入任何字段。",
    }


def apply_linked_purchase_expense_fillable_fields(
    *,
    batch_name: str,
    version_name: str | None = None,
    env_file: str | None = None,
    linked_purchase_json: str | None = None,
    purchase_summaries_json: str | None = None,
    recalculate_after_writeback: bool = True,
    trusted_server_payload: bool = False,
) -> dict:
    """写入关联采购支出 OA 中已匹配物料行的采购字段。

    当前口径：
    1. 只处理预览已匹配到唯一物料行的数据。
    2. 按钉钉采购审批字段写入单价、币种、总金额。
    3. 未匹配、多匹配行不写入；已写入字段保留修改记录，后续可人工双击修正。
    """

    preview_result = preview_linked_purchase_expense_oa(
        batch_name=batch_name,
        version_name=version_name,
        env_file=env_file,
        linked_purchase_json=linked_purchase_json,
        purchase_summaries_json=purchase_summaries_json,
        trusted_server_payload=trusted_server_payload,
    )
    if not preview_result.get("ok"):
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": preview_result.get("message") or "采购支出 OA 预览失败，未写入数据。",
        }
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "preview_result": preview_result,
            "message": "当前未连接 Frappe，不能保存采购字段。",
        }

    writeback_preview = preview_result.get("writeback_preview") or {}
    batch_doc_name = preview_result.get("batch_doc_name") or writeback_preview.get("batch_doc_name")
    resolved_version_name = preview_result.get("version_name") or writeback_preview.get("version_name") or version_name
    if not batch_doc_name or not resolved_version_name:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "当前批次或版本未匹配成功，未写入数据。",
        }
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        invalid_response["preview_result"] = preview_result
        return invalid_response

    applied_rows: list[dict] = []
    skipped_rows: list[dict] = []
    changed_field_count = 0
    matched_rows = writeback_preview.get("matched_rows") or []
    fx_context = _get_version_fx_context(resolved_version_name)
    matched_rows_by_target: dict[str, list[dict]] = defaultdict(list)
    for row in matched_rows:
        target_item_name = row.get("target_item_name")
        if target_item_name:
            matched_rows_by_target[target_item_name].append(row)

    processed_duplicate_targets: set[str] = set()
    for row in matched_rows:
        target_item_name = row.get("target_item_name")
        business_changes = row.get("business_changes") or []
        if not target_item_name:
            skipped_rows.append({"row": row, "reason": "缺少目标物料行"})
            continue
        duplicate_group = matched_rows_by_target.get(target_item_name) or []
        if len(duplicate_group) > 1:
            if target_item_name in processed_duplicate_targets:
                continue
            processed_duplicate_targets.add(target_item_name)
            field_updates, aggregate_meta = _build_aggregated_purchase_updates(duplicate_group)
            field_updates = _convert_purchase_updates_to_rmb(field_updates, fx_context)
            if not field_updates:
                skipped_rows.append({"row": row, "reason": "同一物料多条采购明细聚合失败", "aggregate_meta": aggregate_meta})
                continue
            source_no = aggregate_meta.get("selected_source_approval_no") or "关联采购支出 OA"
            changed_fields = _update_item_fields(
                item_name=target_item_name,
                batch_doc_name=batch_doc_name,
                version_name=resolved_version_name,
                row_no=row.get("target_row_no"),
                field_updates=field_updates,
                action_remark=f"采购支出 OA 多行聚合写入；来源审批：{source_no}；策略：{aggregate_meta.get('strategy')}",
            )
            if changed_fields:
                applied_rows.append(
                    {
                        "target_item_name": target_item_name,
                        "target_row_no": row.get("target_row_no"),
                        "source_approval_no": source_no,
                        "changed_fields": changed_fields,
                        "aggregate_meta": aggregate_meta,
                    }
                )
                changed_field_count += len(changed_fields)
            if int(aggregate_meta.get("ignored_count") or 0) > 0:
                skipped_rows.append({"row": row, "reason": "同一物料存在多币种采购来源，已按数量最接近的一组写入，其余组未写入", "aggregate_meta": aggregate_meta})
            continue
        field_updates = {
            change.get("field_name"): change.get("new_value")
            for change in business_changes
            if change.get("field_name") in PURCHASE_WRITEBACK_FIELDS
            and change.get("status") in {"fillable", "conflict", "same"}
        }
        field_updates = {field_name: value for field_name, value in field_updates.items() if field_name}
        if not field_updates:
            skipped_rows.append({"row": row, "reason": "没有可写入字段"})
            continue

        mapped_row = row.get("mapped_row") or {}
        source_no = mapped_row.get("source_approval_no") or mapped_row.get("source_instance_id") or "关联采购支出 OA"
        changed_fields = _update_item_fields(
            item_name=target_item_name,
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=row.get("target_row_no"),
            field_updates=field_updates,
            action_remark=f"采购支出 OA 写入匹配字段；来源审批：{source_no}",
        )
        if changed_fields:
            applied_rows.append(
                {
                    "target_item_name": target_item_name,
                    "target_row_no": row.get("target_row_no"),
                    "source_approval_no": mapped_row.get("source_approval_no"),
                    "changed_fields": changed_fields,
                }
            )
            changed_field_count += len(changed_fields)

    if applied_rows:
        _mark_batch_dirty(batch_doc_name)
        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit):
            commit()
        recalculate_result = _recalculate_after_writeback(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            enabled=recalculate_after_writeback,
        )
    else:
        recalculate_result = {"action": "skipped", "reason": "没有采购字段变化。"}

    updated_count = len(applied_rows)
    base_message = (
        f"已同步 {updated_count} 行采购字段，共 {changed_field_count} 个字段；未匹配、多匹配或多币种保留的行未写入。"
        if updated_count
        else "没有可同步的采购字段，未写入数据。"
    )
    return {
        "ok": True,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "updated_count": updated_count,
        "changed_field_count": changed_field_count,
        "skipped_count": len(skipped_rows),
        "conflict_row_count": writeback_preview.get("conflict_row_count", 0),
        "unmatched_count": writeback_preview.get("unmatched_count", 0),
        "ambiguous_count": writeback_preview.get("ambiguous_count", 0),
        "applied_rows": applied_rows,
        "skipped_rows": skipped_rows,
        "recalculate_result": recalculate_result,
        "preview_result": preview_result,
        "message": _message_with_recalculate_result(base_message, recalculate_result),
    }


def _build_packing_updates_for_preview(mapped_row: dict, _target: dict) -> dict:
    return {
        "actual_shipped_qty": mapped_row.get("actual_shipped_qty"),
        "gross_weight_kg": mapped_row.get("gross_weight_kg"),
        "volume_m3": mapped_row.get("volume_m3"),
        "volume_weight_kg": mapped_row.get("volume_weight_kg"),
        "chargeable_weight_kg": mapped_row.get("chargeable_weight_kg"),
        "unit_price": mapped_row.get("unit_price"),
        "purchase_currency": mapped_row.get("purchase_currency"),
        "supplier": mapped_row.get("supplier"),
        "goods_value": mapped_row.get("goods_value"),
        "hs_code": mapped_row.get("hs_code"),
        "source_type": mapped_row.get("source_type") or "PACKING_LIST",
        "source_file_name": mapped_row.get("source_file_name") or "",
        "source_doc_no": mapped_row.get("source_doc_no") or mapped_row.get("purchase_order_no") or "",
        "parse_status": "SUCCESS",
    }


def _build_attachment_price_provenance(
    *,
    attachment_name: str | None = None,
    file_url: str | None = None,
) -> dict:
    """构造附件补价的行级来源字段，附件记录不存在时保留可追溯的入参。"""

    resolved_attachment_name = str(attachment_name or "").strip()
    attachment_row = {}
    if frappe is not None and resolved_attachment_name:
        try:
            attachment_row = frappe.db.get_value(
                "Overseas Cost Attachment",
                resolved_attachment_name,
                ["name", "batch", "source_type", "file_name", "file_url", "source_doc_no", "parse_result_json"],
                as_dict=True,
            ) or {}
        except Exception:
            attachment_row = {}
    attachment_policy = _json_loads_dict(attachment_row.get("parse_result_json"))
    if (
        _settlement_blocks_write(attachment_policy)
        or _approval_reference_is_excluded(str(attachment_row.get("batch") or ""), attachment_policy)
    ):
        raise ValueError(_("该附件来自已排除审批，仅供审计查看和下载，不能写入成本数据。"))

    resolved_file_url = str(attachment_row.get("file_url") or file_url or "").strip()
    source_doc_no = str(attachment_row.get("source_doc_no") or resolved_file_url or resolved_attachment_name).strip()
    file_name = str(attachment_row.get("file_name") or "").strip()
    if not file_name and resolved_file_url:
        file_name = Path(resolved_file_url.split("?", 1)[0]).name
    if not file_name:
        file_name = resolved_attachment_name

    return {
        "source_type": "ATTACHMENT_PRICE",
        "source_file_name": file_name,
        "source_attachment_id": str(attachment_row.get("name") or resolved_attachment_name),
        "source_doc_no": source_doc_no,
        "parse_status": "SUCCESS",
    }


def _build_packing_preview_items(
    *,
    attachment_name: str | None = None,
    file_url: str | None = None,
    sheet_rows_json: str | None = None,
) -> tuple[list[dict], dict]:
    if sheet_rows_json:
        raw_rows = _load_rows(sheet_rows_json)
        rows = []
        for raw_row in raw_rows:
            row = map_packing_list_row_to_item(raw_row)
            row["excel_row_no"] = _first_non_empty(
                raw_row.get("sourceRow"),
                raw_row.get("excel_row_no"),
                raw_row.get("Excel行号"),
                raw_row.get("行号"),
            )
            row["source_remark"] = _first_non_empty(raw_row.get("备注"), raw_row.get("source_remark"))
            row["source_file_name"] = attachment_name or raw_row.get("source_file_name") or row.get("source_file_name") or ""
            row["source_doc_no"] = file_url or raw_row.get("source_doc_no") or row.get("source_doc_no") or attachment_name or ""
            row["source_attachment_id"] = attachment_name or raw_row.get("source_attachment_id") or row.get("source_attachment_id") or ""
            rows.append(row)
        return rows, {"parser": "json_rows", "source": "sheet_rows_json"}

    if not file_url:
        return [], {"parser": "empty", "source": "none"}

    path = _resolve_excel_file_path(file_url=file_url)
    parser_meta, blocks = parse_yuewei_excel_workbook(path)
    rows: list[dict] = []
    for block in blocks:
        for index, item_row in enumerate(block.get("items") or [], start=1):
            mapped = map_yuewei_excel_block_item_to_item(block, item_row, row_index=index)
            mapped["source_type"] = "PACKING_LIST"
            mapped["source_file_name"] = attachment_name or path.name
            mapped["source_attachment_id"] = attachment_name or ""
            mapped["source_doc_no"] = mapped.get("source_doc_no") or block.get("sourceDocNo") or block.get("id") or ""
            rows.append(mapped)

    return rows, {
        **(parser_meta or {}),
        "block_count": len(blocks),
        "item_count": len(rows),
        "source": "file_url",
    }


def _packing_attachment_is_audit_only(batch_name: str, attachment_name: str | None) -> bool:
    if frappe is None or not attachment_name:
        return False
    try:
        row = frappe.db.get_value(
            "Overseas Cost Attachment",
            attachment_name,
            ["batch", "parse_result_json"],
            as_dict=True,
        ) or {}
    except Exception:
        return False
    if row.get("batch") and str(row.get("batch")) != str(batch_name):
        return True
    snapshot = _json_loads_dict(row.get("parse_result_json"))
    # 预览装箱单是读路径：审批进行中（approval_excluded / cost_source_allowed=False）
    # 是业务常态，此时必须放行，否则用户在审批批完前根本拉不到装箱单。
    return _settlement_audit_state(snapshot)["audit_only"] or _approval_reference_is_excluded(
        str(row.get("batch") or batch_name), snapshot
    )


def preview_packing_list_attachment(
    *,
    batch_name: str,
    attachment_name: str | None = None,
    file_url: str | None = None,
    version_name: str | None = None,
    template_hint: str | None = None,
    sheet_rows_json: str | None = None,
    trusted_server_payload: bool = False,
) -> dict:
    """预览装箱单/物流附件可补哪些实际发货、重量、体积字段，不写入数据。"""

    if _packing_attachment_is_audit_only(batch_name, attachment_name):
        return {
            "ok": False,
            "audit_only": True,
            "batch_name": batch_name,
            "attachment_name": attachment_name,
            "message": "该附件来自已排除审批，仅供审计查看和下载，不能作为装箱或成本数据源。",
        }
    if frappe is not None and not trusted_server_payload:
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "attachment_name": attachment_name,
            "message": "装箱内容必须由服务器按附件记录读取，不能使用浏览器提交的文件路径或明细数据。",
        }

    preview_items, parser_meta = _build_packing_preview_items(
        attachment_name=attachment_name,
        file_url=file_url,
        sheet_rows_json=sheet_rows_json,
    )
    parse_task = attachment_parse_service.build_packing_list_parse_task(
        batch_name=batch_name,
        version_name=version_name,
        attachment_name=attachment_name,
        file_url=file_url,
        template_hint=template_hint,
    )
    writeback_preview = _preview_item_writeback(
        batch_name=batch_name,
        version_name=version_name,
        mapped_rows=preview_items,
        update_builder=_build_packing_updates_for_preview,
        field_labels=PACKING_FIELD_LABELS,
        business_fields=PACKING_WRITEBACK_FIELDS,
        compact_row=_compact_packing_row,
        numeric_zero_fillable_fields=PACKING_NUMERIC_ZERO_FILLABLE_FIELDS,
        preview_message_prefix="装箱单/物流附件",
        fillable_message="可用于补齐空的实际发货数量、毛重、体积、单价或总价。",
        resolve_ambiguous_by_sequence=True,
    )
    conflict_resolutions = _get_packing_conflict_resolutions(attachment_name)
    for matched_row in writeback_preview.get("matched_rows") or []:
        resolution = _latest_packing_conflict_resolution(
            conflict_resolutions,
            target_item_name=matched_row.get("target_item_name"),
        )
        if resolution:
            matched_row["conflict_resolution"] = resolution

    return {
        "ok": True,
        "dry_run": frappe is None,
        "batch_name": batch_name,
        "batch_doc_name": writeback_preview.get("batch_doc_name"),
        "version_name": writeback_preview.get("version_name") or version_name,
        "attachment_name": attachment_name,
        "file_url": file_url,
        "source_type": "PACKING_LIST",
        "parser_meta": parser_meta,
        "parse_task": parse_task,
        "mapped_preview_count": len(preview_items),
        "mapped_preview_items": [_compact_packing_row(row) for row in preview_items[:20]],
        "writeback_targets": [PACKING_FIELD_LABELS[field] for field in PACKING_WRITEBACK_FIELDS],
        "writeback_preview": writeback_preview,
        "conflict_resolutions": conflict_resolutions,
        "message": writeback_preview.get("message") or "装箱单/物流附件预览已生成，当前未写入任何字段。",
    }


def apply_packing_list_fillable_fields(
    *,
    batch_name: str,
    attachment_name: str | None = None,
    file_url: str | None = None,
    version_name: str | None = None,
    template_hint: str | None = None,
    sheet_rows_json: str | None = None,
    recalculate_after_writeback: bool = True,
    auto_create_unmatched_items: bool = False,
    preview_result: dict | None = None,
    commit_after_writeback: bool = True,
    trusted_server_payload: bool = False,
) -> dict:
    """确认补入装箱单/物流附件中可安全写入的物理属性与价格字段。"""

    from overseas_costing.services.effective_logistics_source import current_source_bundle
    bundle = current_source_bundle(batch_name, version_name, lock=True)
    if bundle and bundle['context']['root_kind'] == 'expense':
        return {'ok': False, 'code': 'EFFECTIVE_SOURCE_REQUIRED',
                'message': '当前已采用采购支出来源，请在物料区使用“装箱填充”重新预览并确认，旧装箱预览不能写入。'}

    if frappe is not None and not trusted_server_payload:
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "attachment_name": attachment_name,
            "message": "装箱写入必须使用服务器核验过的附件或评论来源。",
        }

    preview_result = preview_result or preview_packing_list_attachment(
        batch_name=batch_name,
        attachment_name=attachment_name,
        file_url=file_url,
        version_name=version_name,
        template_hint=template_hint,
        sheet_rows_json=sheet_rows_json,
        trusted_server_payload=trusted_server_payload,
    )
    if not preview_result.get("ok"):
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": preview_result.get("message") or "装箱单/物流附件预览失败，未写入数据。",
        }
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "preview_result": preview_result,
            "message": "当前未连接 Frappe，不能保存装箱单字段。",
        }

    writeback_preview = preview_result.get("writeback_preview") or {}
    batch_doc_name = preview_result.get("batch_doc_name") or writeback_preview.get("batch_doc_name")
    resolved_version_name = preview_result.get("version_name") or writeback_preview.get("version_name") or version_name
    if not batch_doc_name or not resolved_version_name:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "当前批次或版本未匹配成功，未写入数据。",
        }
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        invalid_response["preview_result"] = preview_result
        return invalid_response

    applied_rows: list[dict] = []
    skipped_rows: list[dict] = []
    created_rows: list[dict] = []
    create_skipped_rows: list[dict] = []
    changed_field_count = 0
    price_source_row_count = 0
    attachment_price_provenance = _build_attachment_price_provenance(
        attachment_name=attachment_name,
        file_url=file_url,
    )

    for row in writeback_preview.get("matched_rows") or []:
        target_item_name = row.get("target_item_name")
        business_changes = row.get("business_changes") or []
        if not target_item_name:
            skipped_rows.append({"row": row, "reason": "缺少目标物料行"})
            continue
        field_updates = {
            change.get("field_name"): change.get("new_value")
            for change in business_changes
            if change.get("field_name") in PACKING_WRITEBACK_FIELDS and change.get("status") == "fillable"
        }
        field_updates = {field_name: value for field_name, value in field_updates.items() if field_name}
        if not field_updates:
            skipped_rows.append({"row": row, "reason": "没有可补字段"})
            continue

        mapped_row = row.get("mapped_row") or {}
        source_no = mapped_row.get("source_doc_no") or mapped_row.get("source_file_name") or attachment_name or "装箱单/物流附件"
        price_fields = sorted(set(field_updates).intersection(PURCHASE_WRITEBACK_FIELDS))
        if price_fields:
            field_updates.update(attachment_price_provenance)
            source_no = attachment_price_provenance.get("source_doc_no") or source_no
            action_remark = f"装箱单/物流附件确认补入采购价格字段并登记附件来源；来源：{source_no}"
        else:
            action_remark = f"装箱单/物流附件确认补入可补字段；来源：{source_no}"
        changed_fields = _update_item_fields(
            item_name=target_item_name,
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=row.get("target_row_no"),
            field_updates=field_updates,
            action_remark=action_remark,
        )
        if changed_fields:
            if price_fields:
                price_source_row_count += 1
            applied_rows.append(
                {
                    "target_item_name": target_item_name,
                    "target_row_no": row.get("target_row_no"),
                    "source_doc_no": mapped_row.get("source_doc_no"),
                    "price_fields": price_fields,
                    "source_type": attachment_price_provenance["source_type"] if price_fields else "PACKING_LIST",
                    "changed_fields": changed_fields,
                }
            )
            changed_field_count += len(changed_fields)

    if auto_create_unmatched_items:
        create_result = _create_packing_items_from_unmatched_preview(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            writeback_preview=writeback_preview,
            attachment_name=attachment_name,
            file_url=file_url,
        )
        created_rows = create_result.get("created_rows") or []
        create_skipped_rows = create_result.get("skipped_rows") or []

    total_skipped_count = len(skipped_rows) + len(create_skipped_rows)
    if applied_rows or created_rows:
        _mark_batch_dirty(batch_doc_name)
        attachment_marked = _mark_attachment_parsed(
            attachment_name,
            {
                "created_count": len(created_rows),
                "updated_count": len(applied_rows),
                "changed_field_count": changed_field_count,
                "skipped_count": total_skipped_count,
                "conflict_row_count": writeback_preview.get("conflict_row_count", 0),
                "unmatched_count": writeback_preview.get("unmatched_count", 0),
                "ambiguous_count": writeback_preview.get("ambiguous_count", 0),
                "parse_targets": list(PACKING_WRITEBACK_FIELDS),
                "applied_rows": applied_rows,
                "created_rows": created_rows,
            },
        )
        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit) and commit_after_writeback:
            commit()
        recalculate_result = _recalculate_after_writeback(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            enabled=recalculate_after_writeback,
        )
    else:
        attachment_marked = False
        recalculate_result = {"action": "skipped", "reason": "没有装箱单字段变化。"}

    updated_count = len(applied_rows)
    created_count = len(created_rows)
    if updated_count or created_count:
        base_message = f"已更新 {updated_count} 行装箱单字段，共 {changed_field_count} 个字段；自动新增 {created_count} 条物料。"
        if writeback_preview.get("conflict_row_count") or writeback_preview.get("ambiguous_count"):
            base_message += " 冲突和多匹配行已保留给数据检查/人工核对。"
    else:
        base_message = "没有可安全补入的装箱单字段，未写入数据。"
    return {
        "ok": True,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "updated_count": updated_count,
        "created_count": created_count,
        "changed_field_count": changed_field_count,
        "price_source_row_count": price_source_row_count,
        "skipped_count": total_skipped_count,
        "conflict_row_count": writeback_preview.get("conflict_row_count", 0),
        "unmatched_count": writeback_preview.get("unmatched_count", 0),
        "ambiguous_count": writeback_preview.get("ambiguous_count", 0),
        "applied_rows": applied_rows,
        "created_rows": created_rows,
        "skipped_rows": skipped_rows,
        "create_skipped_rows": create_skipped_rows,
        "attachment_marked_parsed": attachment_marked,
        "recalculate_result": recalculate_result,
        "preview_result": preview_result,
        "message": _message_with_recalculate_result(base_message, recalculate_result),
    }


def create_items_from_packing_unmatched_rows(
    *,
    batch_name: str,
    attachment_name: str | None = None,
    file_url: str | None = None,
    version_name: str | None = None,
    template_hint: str | None = None,
    sheet_rows_json: str | None = None,
    recalculate_after_writeback: bool = True,
    trusted_server_payload: bool = False,
) -> dict:
    """将装箱单预览中的未匹配行，经用户确认后新增为当前批次物料行。"""

    preview_result = preview_packing_list_attachment(
        batch_name=batch_name,
        attachment_name=attachment_name,
        file_url=file_url,
        version_name=version_name,
        template_hint=template_hint,
        sheet_rows_json=sheet_rows_json,
        trusted_server_payload=trusted_server_payload,
    )
    if not preview_result.get("ok"):
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": preview_result.get("message") or "装箱单预览失败，未新增物料。",
        }

    writeback_preview = preview_result.get("writeback_preview") or {}
    unmatched_rows = writeback_preview.get("unmatched_rows") or []
    ambiguous_count = int(writeback_preview.get("ambiguous_count") or 0)
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name,
            "version_name": version_name,
            "created_count": len(unmatched_rows),
            "skipped_count": 0,
            "ambiguous_count": ambiguous_count,
            "created_rows": unmatched_rows,
            "message": f"当前未连接 Frappe，预计可从未匹配装箱单行新增 {len(unmatched_rows)} 条物料。",
        }

    batch_doc_name = preview_result.get("batch_doc_name") or _resolve_batch_name(batch_name)
    resolved_version_name = preview_result.get("version_name")
    if batch_doc_name and not resolved_version_name:
        resolved_version_name = _resolve_version_name(batch_doc_name, version_name)
    if not batch_doc_name or not resolved_version_name:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "当前批次或版本未匹配成功，未新增物料。",
        }
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        invalid_response["preview_result"] = preview_result
        return invalid_response
    if not unmatched_rows:
        return {
            "ok": True,
            "batch_name": batch_doc_name,
            "version_name": resolved_version_name,
            "created_count": 0,
            "skipped_count": 0,
            "ambiguous_count": ambiguous_count,
            "created_rows": [],
            "skipped_rows": [],
            "preview_result": preview_result,
            "message": "当前没有未匹配装箱单行需要新增。",
        }

    create_result = _create_packing_items_from_unmatched_preview(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        writeback_preview=writeback_preview,
        attachment_name=attachment_name,
        file_url=file_url,
    )
    created_rows = create_result.get("created_rows") or []
    skipped_rows = create_result.get("skipped_rows") or []

    if created_rows:
        _mark_batch_dirty(batch_doc_name)
        attachment_marked = _mark_attachment_parsed(
            attachment_name,
            {
                "created_count": len(created_rows),
                "updated_count": 0,
                "changed_field_count": 0,
                "skipped_count": len(skipped_rows),
                "conflict_row_count": writeback_preview.get("conflict_row_count", 0),
                "unmatched_count": writeback_preview.get("unmatched_count", 0),
                "ambiguous_count": ambiguous_count,
                "parse_targets": ["material_code", "product_name", "spec_model", *PACKING_WRITEBACK_FIELDS],
            },
        )
        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit):
            commit()
        recalculate_result = _recalculate_after_writeback(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            enabled=recalculate_after_writeback,
        )
    else:
        attachment_marked = False
        recalculate_result = {"action": "skipped", "reason": "没有新增物料。"}

    base_message = (
        f"已从装箱单未匹配行新增 {len(created_rows)} 条物料；跳过 {len(skipped_rows)} 条。"
        if created_rows
        else f"没有新增物料；跳过 {len(skipped_rows)} 条。"
    )
    if ambiguous_count:
        base_message += f" 另有 {ambiguous_count} 条多匹配行未新增，请先人工核对。"
    return {
        "ok": True,
        "batch_name": batch_doc_name,
        "version_name": resolved_version_name,
        "created_count": len(created_rows),
        "skipped_count": len(skipped_rows),
        "ambiguous_count": ambiguous_count,
        "created_rows": created_rows,
        "skipped_rows": skipped_rows,
        "attachment_marked_parsed": attachment_marked,
        "recalculate_result": recalculate_result,
        "preview_result": preview_result,
        "message": _message_with_recalculate_result(base_message, recalculate_result),
    }


def _create_packing_items_from_unmatched_preview(
    *,
    batch_doc_name: str,
    version_name: str,
    writeback_preview: dict,
    attachment_name: str | None = None,
    file_url: str | None = None,
) -> dict:
    unmatched_rows = writeback_preview.get("unmatched_rows") or []
    if frappe is None or not unmatched_rows:
        return {"created_rows": [], "skipped_rows": []}

    from overseas_costing.services.logistics_settlement.runtime import has_final_binding
    if has_final_binding(batch_doc_name):
        return {'created_rows': [], 'skipped_rows': [{'row': row, 'reason': '最终货物以采购支出为准；装箱中未匹配的行保留待核对'} for row in unmatched_rows]}

    attachment_provenance = _build_packing_attachment_provenance(
        attachment_name=attachment_name,
        file_url=file_url,
    )
    existing_items = _get_batch_items(batch_doc_name, version_name)
    next_row_no = _next_item_row_no(existing_items)
    created_rows: list[dict] = []
    skipped_rows: list[dict] = []

    for mapped_row in unmatched_rows:
        if _is_probable_packing_header_row(mapped_row):
            skipped_rows.append({"row": mapped_row, "reason": "疑似表头行，已跳过"})
            continue
        if not _packing_row_has_material_identity(mapped_row):
            skipped_rows.append({"row": mapped_row, "reason": "缺少物料名称或规格信息"})
            continue
        duplicate_item = _find_duplicate_packing_item(mapped_row, existing_items)
        if duplicate_item:
            skipped_rows.append(
                {
                    "row": mapped_row,
                    "reason": f"同一附件来源已存在第 {duplicate_item.get('row_no') or '--'} 行",
                }
            )
            continue

        values = _build_packing_unmatched_item_values(
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            row_no=next_row_no,
            mapped_row=mapped_row,
            attachment_provenance=attachment_provenance,
        )
        item_doc = frappe.get_doc(values).insert(ignore_permissions=True)
        created_row = {
            "item_name": item_doc.name,
            "row_no": next_row_no,
            "material_code": values.get("material_code"),
            "product_name": values.get("product_name"),
            "spec_model": values.get("spec_model"),
            "actual_shipped_qty": values.get("actual_shipped_qty"),
            "unit_price": values.get("unit_price"),
            "purchase_currency": values.get("purchase_currency"),
            "goods_value": values.get("goods_value"),
            "gross_weight_kg": values.get("gross_weight_kg"),
            "volume_m3": values.get("volume_m3"),
        }
        created_rows.append(created_row)
        existing_items.append(
            {
                "name": item_doc.name,
                "row_no": next_row_no,
                "material_code": values.get("material_code"),
                "product_name": values.get("product_name"),
                "spec_model": values.get("spec_model"),
                "quantity": values.get("quantity"),
                "actual_shipped_qty": values.get("actual_shipped_qty"),
                "unit_price": values.get("unit_price"),
                "purchase_currency": values.get("purchase_currency"),
                "goods_value": values.get("goods_value"),
                "gross_weight_kg": values.get("gross_weight_kg"),
                "volume_m3": values.get("volume_m3"),
                "excel_row_no": values.get("excel_row_no"),
                "source_doc_no": values.get("source_doc_no"),
                "source_file_name": values.get("source_file_name"),
                "source_attachment_id": values.get("source_attachment_id"),
            }
        )
        _create_audit_log(
            batch_doc_name=batch_doc_name,
            version_name=version_name,
            row_no=next_row_no,
            field_name="item",
            old_value=None,
            new_value=_json_dumps({key: value for key, value in values.items() if key != "doctype"}),
            action_remark="从装箱单未匹配行自动新增物料",
        )
        next_row_no += 1

    return {"created_rows": created_rows, "skipped_rows": skipped_rows}


def _packing_row_has_material_identity(mapped_row: dict) -> bool:
    return any(str(mapped_row.get(field) or "").strip() for field in ("material_code", "product_name", "spec_model"))


def _is_probable_packing_header_row(mapped_row: dict) -> bool:
    header_tokens = {
        "material_code": {"物料编码", "物品编码", "sku", "code", "material_code"},
        "product_name": {"物料名称", "物品名称", "品名", "product_name", "material", "nombre del artículo"},
        "spec_model": {"规格", "规格型号", "型号/规格", "spec_model", "model", "especificacion"},
    }
    hit_count = 0
    for fieldname, tokens in header_tokens.items():
        value = str(mapped_row.get(fieldname) or "").strip().lower()
        if value and value in tokens:
            hit_count += 1
    return hit_count >= 2


def _next_item_row_no(existing_items: list[dict]) -> int:
    row_numbers = []
    for item in existing_items:
        try:
            row_numbers.append(int(item.get("row_no") or 0))
        except (TypeError, ValueError):
            continue
    return (max(row_numbers) if row_numbers else 0) + 1


def _build_packing_attachment_provenance(
    *,
    attachment_name: str | None = None,
    file_url: str | None = None,
) -> dict:
    provenance = _build_attachment_price_provenance(attachment_name=attachment_name, file_url=file_url)
    provenance["source_type"] = "PACKING_LIST"
    return provenance


def _build_packing_unmatched_item_values(
    *,
    batch_doc_name: str,
    version_name: str,
    row_no: int,
    mapped_row: dict,
    attachment_provenance: dict,
) -> dict:
    actual_qty = _first_non_empty(mapped_row.get("actual_shipped_qty"), mapped_row.get("quantity"))
    source_file_name = _first_non_empty(mapped_row.get("source_file_name"), attachment_provenance.get("source_file_name"))
    source_attachment_id = _first_non_empty(
        mapped_row.get("source_attachment_id"),
        attachment_provenance.get("source_attachment_id"),
    )
    source_doc_no = _first_non_empty(mapped_row.get("source_doc_no"), attachment_provenance.get("source_doc_no"), source_file_name)
    unit = normalize_unit(mapped_row.get("unit")) or ""
    values = {
        "doctype": "Overseas Cost Item",
        "batch": batch_doc_name,
        "version": version_name,
        "row_no": row_no,
        "stable_line_key": material_input_service.ensure_stable_line_key({}),
        "excel_row_no": mapped_row.get("excel_row_no"),
        "material_code": mapped_row.get("material_code") or "",
        "product_name": mapped_row.get("product_name") or "",
        "product_name_es": mapped_row.get("product_name_es") or "",
        "spec_model": mapped_row.get("spec_model") or "",
        "unit": unit,
        "purchase_uom": unit,
        "unit_price_uom": unit if mapped_row.get("unit_price") not in (None, "") else "",
        "quantity": _to_float(actual_qty),
        "actual_shipped_qty": _to_float(actual_qty),
        "actual_shipped_qty_mode": "EXPLICIT_SOURCE" if _to_float(actual_qty) > 0 else "DEFAULT_PURCHASE",
        "shipped_uom": unit,
        "cost_output_uom": unit,
        "unit_price": _to_float(mapped_row.get("unit_price")),
        "purchase_currency": mapped_row.get("purchase_currency") or "",
        "goods_value": _to_float(mapped_row.get("goods_value")),
        "import_name": mapped_row.get("import_name") or "",
        "hs_code": mapped_row.get("hs_code") or "",
        "purchase_order_no": mapped_row.get("purchase_order_no") or "",
        "gross_weight_kg": _to_float(mapped_row.get("gross_weight_kg")),
        "volume_m3": _to_float(mapped_row.get("volume_m3")),
        "volume_weight_kg": _to_float(mapped_row.get("volume_weight_kg")),
        "chargeable_weight_kg": _to_float(mapped_row.get("chargeable_weight_kg")),
        "project_collection": mapped_row.get("project_collection") or "",
        "transport_mode": mapped_row.get("transport_mode") or "",
        "source_type": "PACKING_LIST",
        "source_doc_no": source_doc_no or "",
        "source_file_name": source_file_name or "",
        "source_attachment_id": source_attachment_id or "",
        "source_remark": mapped_row.get("source_remark") or "",
        "parse_status": "SUCCESS",
        "raw_excel_json": _json_dumps(mapped_row),
    }
    batch_transport_mode = (
        frappe.db.get_value("Overseas Cost Batch", batch_doc_name, "transport_mode")
        if frappe is not None else ""
    )
    values = prepare_item_transport(values, batch_transport_mode)
    return _filter_doctype_values("Overseas Cost Item", values, keep_doctype=True)


def _find_duplicate_packing_item(mapped_row: dict, existing_items: list[dict]) -> dict | None:
    for item in existing_items:
        if _same_packing_source(mapped_row, item) and _same_packing_row_identity(mapped_row, item):
            return item
    return None


def _same_packing_source(mapped_row: dict, item: dict) -> bool:
    for fieldname in ("source_attachment_id", "source_file_name", "source_doc_no"):
        source_value = _normalize_key(mapped_row.get(fieldname))
        if source_value and source_value == _normalize_key(item.get(fieldname)):
            return True
    return False


def _same_packing_row_identity(mapped_row: dict, item: dict) -> bool:
    row_excel_no = str(mapped_row.get("excel_row_no") or "").strip()
    item_excel_no = str(item.get("excel_row_no") or "").strip()
    if row_excel_no and item_excel_no and row_excel_no == item_excel_no:
        return True

    if not any(_normalize_key(mapped_row.get(field)) for field in ITEM_KEY_FIELDS):
        return False
    for fieldname in ITEM_KEY_FIELDS:
        row_key = _normalize_key(mapped_row.get(fieldname))
        item_key = _normalize_key(item.get(fieldname))
        if row_key != item_key:
            return False
    return all(
        _values_equal_for_import(mapped_row.get(fieldname), item.get(fieldname))
        for fieldname in ("actual_shipped_qty", "gross_weight_kg", "volume_m3")
    )


def resolve_packing_list_conflict_row(
    *,
    batch_name: str,
    attachment_name: str | None,
    target_item_name: str,
    resolution_action: str,
    file_url: str | None = None,
    version_name: str | None = None,
    template_hint: str | None = None,
    sheet_rows_json: str | None = None,
    recalculate_after_writeback: bool = True,
    preview_result: dict | None = None,
    commit_after_writeback: bool = True,
    trusted_server_payload: bool = False,
) -> dict:
    """按单条物料行处理装箱单差异：采用附件、保留系统或待核对。"""

    from overseas_costing.services.effective_logistics_source import current_source_bundle
    bundle = current_source_bundle(batch_name, version_name, lock=True)
    if bundle and bundle['context']['root_kind'] == 'expense':
        return {'ok': False, 'code': 'EFFECTIVE_SOURCE_REQUIRED',
                'message': '当前已采用采购支出来源，请在物料区使用“装箱填充”重新预览并确认差异。'}

    actions = {
        "use_attachment": "采用附件值",
        "keep_system": "保留系统值",
        "pending_review": "待核对",
    }
    action = str(resolution_action or "").strip()
    if action not in actions:
        return {"ok": False, "message": "请选择有效的差异处理方式。"}
    if not str(target_item_name or "").strip():
        return {"ok": False, "message": "缺少要处理的物料行。"}

    if frappe is not None and not trusted_server_payload:
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "attachment_name": attachment_name,
            "message": "装箱差异处理必须使用服务器核验过的附件或评论来源。",
        }

    preview_result = preview_result or preview_packing_list_attachment(
        batch_name=batch_name,
        attachment_name=attachment_name,
        file_url=file_url,
        version_name=version_name,
        template_hint=template_hint,
        sheet_rows_json=sheet_rows_json,
        trusted_server_payload=trusted_server_payload,
    )
    if not preview_result.get("ok"):
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": preview_result.get("message") or "装箱单预览失败，未处理差异。",
        }
    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "preview_result": preview_result,
            "message": "当前未连接 Frappe，不能保存装箱单差异处理结果。",
        }

    writeback_preview = preview_result.get("writeback_preview") or {}
    batch_doc_name = preview_result.get("batch_doc_name") or writeback_preview.get("batch_doc_name")
    resolved_version_name = preview_result.get("version_name") or writeback_preview.get("version_name") or version_name
    matched_row = next(
        (
            row
            for row in writeback_preview.get("matched_rows") or []
            if str(row.get("target_item_name") or "") == str(target_item_name)
        ),
        None,
    )
    if not batch_doc_name or not resolved_version_name or not matched_row:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "未找到当前装箱单对应的物料差异行。",
        }
    invalid_response = _invalid_batch_cost_write_response(batch_doc_name, resolved_version_name)
    if invalid_response:
        invalid_response["preview_result"] = preview_result
        return invalid_response

    conflict_changes = [
        change
        for change in matched_row.get("business_changes") or []
        if change.get("status") == "conflict" and change.get("field_name") in PACKING_WRITEBACK_FIELDS
    ]
    if not conflict_changes:
        return {
            "ok": False,
            "preview_result": preview_result,
            "message": "该物料行当前没有需要处理的装箱单差异。",
        }

    resolution = _build_packing_conflict_resolution(
        matched_row=matched_row,
        action=action,
        action_label=actions[action],
        attachment_name=attachment_name,
    )
    changed_fields: list[dict] = []
    if action == "use_attachment":
        field_updates = {change["field_name"]: change.get("new_value") for change in conflict_changes}
        provenance = _build_packing_conflict_provenance(
            attachment_name=attachment_name,
            file_url=file_url,
            changed_field_names=field_updates.keys(),
        )
        field_updates.update(provenance)
        changed_fields = _update_item_fields(
            item_name=target_item_name,
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=matched_row.get("target_row_no"),
            field_updates=field_updates,
            action_remark=f"装箱单差异处理：采用附件值；来源：{provenance.get('source_doc_no') or attachment_name or '--'}",
        )
        if changed_fields:
            _mark_batch_dirty(batch_doc_name)
    else:
        _create_audit_log(
            batch_doc_name=batch_doc_name,
            version_name=resolved_version_name,
            row_no=matched_row.get("target_row_no"),
            field_name="packing_conflict_resolution",
            old_value={change.get("field_name"): change.get("old_value") for change in conflict_changes},
            new_value=actions[action],
            action_remark=f"装箱单差异处理：{actions[action]}",
        )

    resolution_saved = _record_packing_conflict_resolution(attachment_name, resolution)
    commit = getattr(getattr(frappe, "db", None), "commit", None)
    if callable(commit) and commit_after_writeback:
        commit()
    recalculate_result = _recalculate_after_writeback(
        batch_doc_name=batch_doc_name,
        version_name=resolved_version_name,
        enabled=bool(changed_fields) and recalculate_after_writeback,
    )
    return {
        "ok": True,
        "batch_name": batch_name,
        "batch_doc_name": batch_doc_name,
        "version_name": resolved_version_name,
        "target_item_name": target_item_name,
        "target_row_no": matched_row.get("target_row_no"),
        "resolution": resolution,
        "changed_field_count": len(changed_fields),
        "resolution_saved": resolution_saved,
        "recalculate_result": recalculate_result,
        "message": _message_with_recalculate_result(
            f"已处理第 {matched_row.get('target_row_no') or '--'} 行差异：{actions[action]}。",
            recalculate_result,
        ),
    }


def _build_packing_conflict_provenance(
    *,
    attachment_name: str | None,
    file_url: str | None,
    changed_field_names,
) -> dict:
    provenance = _build_attachment_price_provenance(
        attachment_name=attachment_name,
        file_url=file_url,
    )
    price_fields = set(changed_field_names).intersection(PURCHASE_WRITEBACK_FIELDS)
    if not price_fields:
        provenance["source_type"] = "PACKING_LIST"
    return provenance


def _build_packing_conflict_resolution(
    *,
    matched_row: dict,
    action: str,
    action_label: str,
    attachment_name: str | None,
) -> dict:
    operator_name = ""
    resolved_at = ""
    if frappe is not None:
        session_user = getattr(getattr(frappe, "session", None), "user", None)
        if session_user and session_user != "Guest":
            operator_name = str(session_user)
        try:
            resolved_at = str(frappe.utils.now())
        except Exception:
            resolved_at = ""
    conflicts = [
        {
            "field_name": change.get("field_name"),
            "field_label": change.get("field_label"),
            "system_value": change.get("old_value"),
            "attachment_value": change.get("new_value"),
        }
        for change in matched_row.get("business_changes") or []
        if change.get("status") == "conflict"
    ]
    return {
        "target_item_name": matched_row.get("target_item_name") or "",
        "target_row_no": matched_row.get("target_row_no"),
        "attachment_name": attachment_name or "",
        "action": action,
        "action_label": action_label,
        "conflicts": conflicts,
        "resolved_by": operator_name,
        "resolved_at": resolved_at,
    }


def _record_packing_conflict_resolution(attachment_name: str | None, resolution: dict) -> bool:
    resolved_attachment_name = str(attachment_name or "").strip()
    if frappe is None or not resolved_attachment_name:
        return False
    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception:
        return False
    mapped_result = _json_loads_dict(getattr(attachment_doc, "mapped_result_json", ""))
    history = mapped_result.get("packing_conflict_resolutions")
    if not isinstance(history, list):
        history = []
    history.append(resolution)
    mapped_result["packing_conflict_resolutions"] = history[-50:]
    attachment_doc.mapped_result_json = _json_dumps(mapped_result)
    attachment_doc.save(ignore_permissions=True)
    return True


def _get_packing_conflict_resolutions(attachment_name: str | None) -> list[dict]:
    resolved_attachment_name = str(attachment_name or "").strip()
    if frappe is None or not resolved_attachment_name:
        return []
    try:
        attachment_doc = frappe.get_doc("Overseas Cost Attachment", resolved_attachment_name)
    except Exception:
        return []
    mapped_result = _json_loads_dict(getattr(attachment_doc, "mapped_result_json", ""))
    history = mapped_result.get("packing_conflict_resolutions")
    return [item for item in history if isinstance(item, dict)] if isinstance(history, list) else []


def _latest_packing_conflict_resolution(history: list[dict], *, target_item_name: str | None) -> dict:
    target = str(target_item_name or "")
    for resolution in reversed(history):
        if str(resolution.get("target_item_name") or "") == target:
            return resolution
    return {}


def _attachment_ext_from_row(row: dict) -> str:
    parse_result = _json_loads_dict(row.get("parse_result_json"))
    explicit = str(parse_result.get("file_ext") or "").strip().lower().lstrip(".")
    if explicit:
        return explicit
    for fieldname in ("file_url", "file_name"):
        value = str(row.get(fieldname) or "").strip()
        if value:
            suffix = Path(value.split("?", 1)[0]).suffix.lower().lstrip(".")
            if suffix:
                return suffix
    return ""


def _attachment_parse_targets_from_row(row: dict) -> list[str]:
    mapped_result = _json_loads_dict(row.get("mapped_result_json"))
    targets = mapped_result.get("parse_targets") or []
    return [str(target) for target in targets if target]


def _is_excel_packing_attachment(row: dict) -> bool:
    extension = _attachment_ext_from_row(row)
    if extension not in {"xlsx", "xlsm"}:
        return False
    targets = _attachment_parse_targets_from_row(row)
    return row.get("attachment_type") == "Packing List" or "actual_shipped_qty" in targets


def _is_source_document_attachment(row: dict) -> bool:
    return _attachment_ext_from_row(row) in {
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "bmp",
        "tif",
        "tiff",
        "doc",
        "docx",
        "txt",
    }


def _query_oa_attachment_rows(batch_name: str | None = None, limit: int | None = 200) -> tuple[str, list[dict]]:
    resolved_batch_name = _resolve_batch_name(batch_name) if batch_name else ""
    filters = {"source_type": "OA"}
    if batch_name:
        if not resolved_batch_name:
            return "", []
        filters["batch"] = resolved_batch_name

    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters=filters,
        fields=[
            "name",
            "batch",
            "version",
            "source_type",
            "attachment_type",
            "source_doc_no",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
        ],
        order_by="creation asc",
        limit_page_length=max(1, min(int(limit or 200), 1000)),
    )
    return resolved_batch_name, rows


def _query_manual_document_attachment_rows(batch_name: str | None = None, limit: int | None = 200) -> tuple[str, list[dict]]:
    resolved_batch_name = _resolve_batch_name(batch_name) if batch_name else ""
    filters = {"source_type": "Manual"}
    if batch_name:
        if not resolved_batch_name:
            return "", []
        filters["batch"] = resolved_batch_name

    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters=filters,
        fields=[
            "name",
            "batch",
            "version",
            "source_type",
            "attachment_type",
            "source_doc_no",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
            "creation",
        ],
        order_by="creation asc",
        limit_page_length=max(1, min(int(limit or 200), 1000)),
    )
    return resolved_batch_name, rows


def _filter_latest_manual_document_rows(rows: list[dict], logistics_type: str | None = None) -> list[dict]:
    logistics_filter = str(logistics_type or "").strip().upper()
    latest_by_slot: dict[str, dict] = {}
    passthrough_rows: list[dict] = []

    for row in rows:
        parse_result = _json_loads_dict(row.get("parse_result_json"))
        manual_meta = parse_result.get("manual_document") if isinstance(parse_result.get("manual_document"), dict) else {}
        row_logistics_type = str(manual_meta.get("logistics_type") or "").strip().upper()
        if logistics_filter and row_logistics_type and row_logistics_type != logistics_filter:
            continue

        slot_code = str(manual_meta.get("slot_code") or "").strip()
        if slot_code:
            latest_by_slot[slot_code] = row
        else:
            passthrough_rows.append(row)

    return [*latest_by_slot.values(), *passthrough_rows]


def _compact_download_result(result: dict) -> dict:
    return {
        "ok": result.get("ok"),
        "downloaded": result.get("downloaded"),
        "file_url": result.get("file_url") or "",
        "message": result.get("message") or "",
    }


def _compact_packing_apply_result(result: dict) -> dict:
    return {
        "ok": result.get("ok"),
        "updated_count": result.get("updated_count", 0),
        "created_count": result.get("created_count", 0),
        "changed_field_count": result.get("changed_field_count", 0),
        "skipped_count": result.get("skipped_count", 0),
        "conflict_row_count": result.get("conflict_row_count", 0),
        "unmatched_count": result.get("unmatched_count", 0),
        "ambiguous_count": result.get("ambiguous_count", 0),
        "attachment_marked_parsed": result.get("attachment_marked_parsed", False),
        "message": result.get("message") or "",
    }


def _compact_source_document_preview_result(result: dict) -> dict:
    classification = result.get("classification") if isinstance(result.get("classification"), dict) else {}
    purchase_order = result.get("purchase_order") if isinstance(result.get("purchase_order"), dict) else {}
    return {
        "ok": result.get("ok"),
        "classification_code": classification.get("code") or "",
        "classification_label": classification.get("label") or "",
        "extraction_method": result.get("extraction_method") or "",
        "text_length": result.get("text_length", 0),
        "can_write_purchase_price": bool(result.get("can_write_purchase_price")),
        "purchase_order_line_count": purchase_order.get("recognized_line_count", 0),
        "cache_hit": bool(result.get("cache_hit")),
        "message": result.get("message") or "",
    }


def _extract_dingtalk_permission_scopes(message: str | None) -> list[str]:
    text = str(message or "")
    scopes: list[str] = []
    for match in re.finditer(r"Workflow\.[A-Za-z.]+", text):
        scope = match.group(0)
        if scope not in scopes:
            scopes.append(scope)
    return scopes


def _is_dingtalk_permission_error(message: str | None) -> bool:
    text = str(message or "")
    return (
        "AccessTokenPermissionDenied" in text
        or "requiredScopes" in text
        or "未开通所需的权限" in text
    )


def _is_dingtalk_attachment_user_error(message: str | None) -> bool:
    text = str(message or "")
    return any(marker in text for marker in ("Missing required arguments:user_id", "找不到该用户", "userNotExist", "用户不存在"))


def _is_dingtalk_attachment_permission_error(message: str | None) -> bool:
    text = str(message or "")
    return any(
        marker in text
        for marker in (
            "qyapi_get_member",
            "Storage.DownloadInfo.Read",
            "Storage.File.Read",
            "Drive.DownloadInfo.Read",
            "应用尚未开通所需的权限",
            "AccessTokenPermissionDenied",
        )
    )


def _is_dingtalk_attachment_file_access_error(message: str | None) -> bool:
    text = str(message or "")
    return any(
        marker in text
        for marker in (
            "permissionDenied",
            "noPermission",
            "无访问权限",
            "dentryId",
            "Unknown Error",
        )
    )


def _build_oa_packing_parse_message(
    *,
    scanned_count: int,
    downloaded_count: int,
    parsed_count: int,
    updated_count: int,
    created_count: int,
    changed_field_count: int,
    skipped_count: int,
    failed_count: int,
    permission_blocked_count: int,
    file_access_blocked_count: int,
    permission_scopes: list[str],
) -> str:
    base = (
        f"已扫描 {scanned_count} 个 OA 发起附件，下载 {downloaded_count} 个 Excel 装箱单，"
        f"解析 {parsed_count} 个，更新 {updated_count} 行、自动新增 {created_count} 条物料、写入 {changed_field_count} 个字段；"
        f"跳过 {skipped_count} 个，失败 {failed_count} 个。"
    )
    if permission_blocked_count:
        scope_text = "、".join(permission_scopes) or "钉钉审批附件下载"
        return f"{base} 其中 {permission_blocked_count} 个 Excel 装箱单因钉钉应用缺少 {scope_text} 权限，暂时无法下载解析。"
    if file_access_blocked_count:
        return (
            f"{base} 其中 {file_access_blocked_count} 个 Excel 装箱单已找到钉钉附件记录，"
            "但当前下载账号没有文件级访问权限；请换成能打开该附件的账号，或拖放上传附件后解析。"
        )
    return base


def _build_oa_source_attachment_parse_message(
    *,
    source_label: str = "OA 发起附件",
    scanned_count: int,
    downloaded_count: int,
    packing_parsed_count: int,
    source_recognized_count: int,
    updated_count: int,
    created_count: int,
    changed_field_count: int,
    skipped_count: int,
    failed_count: int,
    permission_blocked_count: int,
    file_access_blocked_count: int,
    permission_scopes: list[str],
) -> str:
    base = (
        f"已扫描 {scanned_count} 个{source_label}，自动下载 {downloaded_count} 个；"
        f"解析 Excel 装箱单 {packing_parsed_count} 个，识别图片/PDF/Word/TXT {source_recognized_count} 个；"
        f"更新 {updated_count} 行、自动新增 {created_count} 条物料、写入 {changed_field_count} 个字段；"
        f"跳过 {skipped_count} 个，失败 {failed_count} 个。"
    )
    if permission_blocked_count:
        scope_text = "、".join(permission_scopes) or "钉钉审批附件下载"
        return f"{base} 其中 {permission_blocked_count} 个附件因钉钉应用缺少 {scope_text} 权限，暂时无法下载解析。"
    if file_access_blocked_count:
        return (
            f"{base} 其中 {file_access_blocked_count} 个附件已找到钉钉记录，"
            "但当前下载账号没有文件级访问权限；请换成能打开该附件的在职账号后重试。"
        )
    return base


def _recalculate_batches_after_attachment_parse(batch_versions: dict[str, str]) -> list[dict]:
    if not batch_versions:
        return []

    results: list[dict] = []
    try:
        from overseas_costing.services.calculate_service import recalculate_batch
    except Exception as exc:
        return [{"ok": False, "message": f"成本重算服务不可用：{exc}"}]

    for batch_doc_name, version_name in batch_versions.items():
        try:
            result = recalculate_batch(batch_name=batch_doc_name, version_name=version_name or None)
            results.append(
                {
                    "ok": result.get("ok", True) if isinstance(result, dict) else True,
                    "batch_name": batch_doc_name,
                    "version_name": version_name,
                    "message": result.get("message") if isinstance(result, dict) else "",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "batch_name": batch_doc_name,
                    "version_name": version_name,
                    "message": str(exc),
                }
            )
    return results


def parse_oa_packing_list_attachments(
    *,
    batch_name: str | None = None,
    limit: int | None = 200,
    env_file: str | None = None,
    access_token: str | None = None,
    skip_parsed: bool = True,
    recalculate: bool = True,
) -> dict:
    """批量下载并解析钉钉发起附件里的 Excel 装箱单，写入可补字段。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能批量解析钉钉附件。",
        }

    resolved_batch_name, rows = _query_oa_attachment_rows(batch_name=batch_name, limit=limit)
    if batch_name and not resolved_batch_name:
        return {
            "ok": False,
            "batch_name": batch_name,
            "message": f"未找到批次：{batch_name}",
            "items": [],
        }

    processed_items: list[dict] = []
    parsed_batch_versions: dict[str, str] = {}
    downloaded_count = 0
    parsed_count = 0
    updated_count = 0
    created_count = 0
    changed_field_count = 0
    skipped_count = 0
    failed_count = 0
    permission_blocked_count = 0
    file_access_blocked_count = 0
    permission_scopes: list[str] = []
    permission_blocked = False

    for row in rows:
        item = {
            "attachment_name": row.get("name"),
            "batch_name": row.get("batch"),
            "version_name": row.get("version"),
            "file_name": row.get("file_name") or "",
            "attachment_type": row.get("attachment_type") or "Other",
            "parse_status": row.get("parse_status") or "",
            "file_ext": _attachment_ext_from_row(row),
        }
        if skip_parsed and str(row.get("parse_status") or "").strip().lower() == "parsed":
            item["action"] = "skipped"
            item["reason"] = "附件已解析"
            skipped_count += 1
            processed_items.append(item)
            continue

        if not _is_excel_packing_attachment(row):
            item["action"] = "skipped"
            item["reason"] = "当前批处理只解析 Excel 装箱单；PDF、PO、合同和其他资料等待对应解析器。"
            skipped_count += 1
            processed_items.append(item)
            continue

        file_url = str(row.get("file_url") or "").strip()
        if not file_url and permission_blocked:
            item["action"] = "blocked"
            item["error_type"] = "dingtalk_permission"
            item["permission_scopes"] = permission_scopes
            item["reason"] = (
                f"本次批处理已确认钉钉应用缺少 {'、'.join(permission_scopes) or '审批附件下载'} 权限，"
                "该 Excel 装箱单暂不重复请求下载。"
            )
            permission_blocked_count += 1
            skipped_count += 1
            processed_items.append(item)
            continue

        if not file_url:
            download_result = download_oa_form_attachment(
                row.get("name"),
                env_file=env_file,
                access_token=access_token,
            )
            item["download"] = _compact_download_result(download_result)
            if not download_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = download_result.get("message") or "附件下载失败"
                if download_result.get("error_type"):
                    item["error_type"] = download_result.get("error_type")
                if _is_dingtalk_permission_error(item["reason"]):
                    item["error_type"] = "dingtalk_permission"
                    scopes = _extract_dingtalk_permission_scopes(item["reason"])
                    item["permission_scopes"] = scopes
                    permission_blocked = True
                    permission_blocked_count += 1
                    for scope in scopes:
                        if scope not in permission_scopes:
                            permission_scopes.append(scope)
                elif download_result.get("error_type") == "dingtalk_attachment_file_access":
                    file_access_blocked_count += 1
                failed_count += 1
                processed_items.append(item)
                continue
            if download_result.get("downloaded"):
                downloaded_count += 1
            file_url = download_result.get("file_url") or file_url

        if not file_url:
            item["action"] = "failed"
            item["reason"] = "附件没有系统文件地址，无法解析。"
            failed_count += 1
            processed_items.append(item)
            continue

        try:
            parse_result = apply_packing_list_fillable_fields(
                batch_name=row.get("batch"),
                version_name=row.get("version"),
                attachment_name=row.get("name"),
                file_url=file_url,
                recalculate_after_writeback=False,
                auto_create_unmatched_items=True,
                trusted_server_payload=True,
            )
        except Exception as exc:
            item["action"] = "failed"
            item["reason"] = f"装箱单解析失败：{exc}"
            failed_count += 1
            processed_items.append(item)
            continue

        item["parse"] = _compact_packing_apply_result(parse_result)
        if not parse_result.get("ok"):
            item["action"] = "failed"
            item["reason"] = parse_result.get("message") or "装箱单解析失败"
            failed_count += 1
            processed_items.append(item)
            continue

        item["action"] = "parsed"
        parsed_count += 1
        row_updated_count = int(parse_result.get("updated_count") or 0)
        row_created_count = int(parse_result.get("created_count") or 0)
        row_changed_field_count = int(parse_result.get("changed_field_count") or 0)
        updated_count += row_updated_count
        created_count += row_created_count
        changed_field_count += row_changed_field_count
        if row_updated_count or row_created_count:
            parsed_batch_versions[parse_result.get("batch_doc_name") or row.get("batch")] = (
                parse_result.get("version_name") or row.get("version") or ""
            )
        processed_items.append(item)

    recalculate_results = _recalculate_batches_after_attachment_parse(parsed_batch_versions) if recalculate else []

    return {
        "ok": failed_count == 0,
        "batch_name": batch_name or "",
        "resolved_batch_name": resolved_batch_name,
        "scanned_count": len(rows),
        "downloaded_count": downloaded_count,
        "parsed_count": parsed_count,
        "updated_count": updated_count,
        "created_count": created_count,
        "changed_field_count": changed_field_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "permission_blocked_count": permission_blocked_count,
        "file_access_blocked_count": file_access_blocked_count,
        "permission_scopes": permission_scopes,
        "recalculate_results": recalculate_results,
        "items": processed_items,
        "message": _build_oa_packing_parse_message(
            scanned_count=len(rows),
            downloaded_count=downloaded_count,
            parsed_count=parsed_count,
            updated_count=updated_count,
            created_count=created_count,
            changed_field_count=changed_field_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            permission_blocked_count=permission_blocked_count,
            file_access_blocked_count=file_access_blocked_count,
            permission_scopes=permission_scopes,
        ),
    }


def parse_oa_source_attachments(
    *,
    batch_name: str | None = None,
    limit: int | None = 200,
    env_file: str | None = None,
    access_token: str | None = None,
    skip_parsed: bool = True,
    recalculate: bool = True,
) -> dict:
    """批量下载并解析钉钉发起附件。

    Excel 装箱单会回填可补字段；图片、PDF、Word、TXT 只保存识别快照，
    供财务查看资料类型和字段候选，不直接改写金额字段。
    """

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能批量解析钉钉附件。",
        }

    resolved_batch_name, rows = _query_oa_attachment_rows(batch_name=batch_name, limit=limit)
    if batch_name and not resolved_batch_name:
        return {
            "ok": False,
            "batch_name": batch_name,
            "message": f"未找到批次：{batch_name}",
            "items": [],
        }

    processed_items: list[dict] = []
    parsed_batch_versions: dict[str, str] = {}
    downloaded_count = 0
    packing_parsed_count = 0
    source_recognized_count = 0
    updated_count = 0
    created_count = 0
    changed_field_count = 0
    skipped_count = 0
    failed_count = 0
    permission_blocked_count = 0
    file_access_blocked_count = 0
    permission_scopes: list[str] = []
    permission_blocked = False

    for row in rows:
        item = {
            "attachment_name": row.get("name"),
            "batch_name": row.get("batch"),
            "version_name": row.get("version"),
            "file_name": row.get("file_name") or "",
            "attachment_type": row.get("attachment_type") or "Other",
            "parse_status": row.get("parse_status") or "",
            "file_ext": _attachment_ext_from_row(row),
        }
        if skip_parsed and str(row.get("parse_status") or "").strip().lower() == "parsed":
            item["action"] = "skipped"
            item["reason"] = "附件已解析"
            skipped_count += 1
            processed_items.append(item)
            continue

        file_url = str(row.get("file_url") or "").strip()
        if not file_url and permission_blocked:
            item["action"] = "blocked"
            item["error_type"] = "dingtalk_permission"
            item["permission_scopes"] = permission_scopes
            item["reason"] = (
                f"本次批处理已确认钉钉应用缺少 {'、'.join(permission_scopes) or '审批附件下载'} 权限，"
                "该附件暂不重复请求下载。"
            )
            permission_blocked_count += 1
            skipped_count += 1
            processed_items.append(item)
            continue

        if not file_url:
            download_result = download_oa_form_attachment(
                row.get("name"),
                env_file=env_file,
                access_token=access_token,
            )
            item["download"] = _compact_download_result(download_result)
            if not download_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = download_result.get("message") or "附件下载失败"
                if download_result.get("error_type"):
                    item["error_type"] = download_result.get("error_type")
                if _is_dingtalk_permission_error(item["reason"]):
                    item["error_type"] = "dingtalk_permission"
                    scopes = _extract_dingtalk_permission_scopes(item["reason"])
                    item["permission_scopes"] = scopes
                    permission_blocked = True
                    permission_blocked_count += 1
                    for scope in scopes:
                        if scope not in permission_scopes:
                            permission_scopes.append(scope)
                elif download_result.get("error_type") == "dingtalk_attachment_file_access":
                    file_access_blocked_count += 1
                failed_count += 1
                processed_items.append(item)
                continue
            if download_result.get("downloaded"):
                downloaded_count += 1
            file_url = download_result.get("file_url") or file_url

        if not file_url:
            item["action"] = "failed"
            item["reason"] = "附件没有系统文件地址，无法解析。"
            failed_count += 1
            processed_items.append(item)
            continue

        row_for_parse = {**row, "file_url": file_url}
        item["file_url"] = file_url
        item["file_ext"] = _attachment_ext_from_row(row_for_parse)

        if _is_excel_packing_attachment(row_for_parse):
            try:
                parse_result = apply_packing_list_fillable_fields(
                    batch_name=row.get("batch"),
                    version_name=row.get("version"),
                    attachment_name=row.get("name"),
                    file_url=file_url,
                    recalculate_after_writeback=False,
                    auto_create_unmatched_items=True,
                    trusted_server_payload=True,
                )
            except Exception as exc:
                item["action"] = "failed"
                item["reason"] = f"装箱单解析失败：{exc}"
                failed_count += 1
                processed_items.append(item)
                continue

            item["parse_mode"] = "packing_list"
            item["parse"] = _compact_packing_apply_result(parse_result)
            if not parse_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = parse_result.get("message") or "装箱单解析失败"
                failed_count += 1
                processed_items.append(item)
                continue

            item["action"] = "parsed"
            packing_parsed_count += 1
            row_updated_count = int(parse_result.get("updated_count") or 0)
            row_created_count = int(parse_result.get("created_count") or 0)
            row_changed_field_count = int(parse_result.get("changed_field_count") or 0)
            updated_count += row_updated_count
            created_count += row_created_count
            changed_field_count += row_changed_field_count
            if row_updated_count or row_created_count:
                parsed_batch_versions[parse_result.get("batch_doc_name") or row.get("batch")] = (
                    parse_result.get("version_name") or row.get("version") or ""
                )
            processed_items.append(item)
            continue

        if _is_source_document_attachment(row_for_parse):
            preview_result = preview_oa_source_attachment(str(row.get("name") or ""))
            item["parse_mode"] = "source_document"
            item["preview"] = _compact_source_document_preview_result(preview_result)
            if not preview_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = preview_result.get("message") or "附件内容识别失败"
                failed_count += 1
                processed_items.append(item)
                continue

            classification = preview_result.get("classification") if isinstance(preview_result.get("classification"), dict) else {}
            item["action"] = "parsed"
            item["recognized_type"] = classification.get("code") or ""
            item["recognized_type_label"] = classification.get("label") or ""
            item["reason"] = preview_result.get("message") or "附件内容识别完成"
            source_recognized_count += 1
            processed_items.append(item)
            continue

        item["action"] = "skipped"
        item["reason"] = f"暂不支持自动识别 {item['file_ext'] or '未知'} 格式附件。"
        skipped_count += 1
        processed_items.append(item)

    recalculate_results = _recalculate_batches_after_attachment_parse(parsed_batch_versions) if recalculate else []
    parsed_count = packing_parsed_count + source_recognized_count

    return {
        "ok": failed_count == 0,
        "batch_name": batch_name or "",
        "resolved_batch_name": resolved_batch_name,
        "scanned_count": len(rows),
        "downloaded_count": downloaded_count,
        "parsed_count": parsed_count,
        "packing_parsed_count": packing_parsed_count,
        "source_recognized_count": source_recognized_count,
        "updated_count": updated_count,
        "created_count": created_count,
        "changed_field_count": changed_field_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "permission_blocked_count": permission_blocked_count,
        "file_access_blocked_count": file_access_blocked_count,
        "permission_scopes": permission_scopes,
        "recalculate_results": recalculate_results,
        "items": processed_items,
        "message": _build_oa_source_attachment_parse_message(
            scanned_count=len(rows),
            downloaded_count=downloaded_count,
            packing_parsed_count=packing_parsed_count,
            source_recognized_count=source_recognized_count,
            updated_count=updated_count,
            created_count=created_count,
            changed_field_count=changed_field_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            permission_blocked_count=permission_blocked_count,
            file_access_blocked_count=file_access_blocked_count,
            permission_scopes=permission_scopes,
        ),
    }


def parse_manual_document_attachments(
    *,
    batch_name: str | None = None,
    logistics_type: str | None = None,
    limit: int | None = 200,
    skip_parsed: bool = True,
    recalculate: bool = True,
) -> dict:
    """批量解析人工补传资料。

    只处理资料补齐弹窗里每个资料格子的最新附件。Excel 装箱单可回填可补字段；
    图片、PDF、Word、TXT 仅保存识别快照，供人工复核，不直接改写金额字段。
    """

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，不能批量解析人工补传资料。",
        }

    resolved_batch_name, all_rows = _query_manual_document_attachment_rows(batch_name=batch_name, limit=limit)
    if batch_name and not resolved_batch_name:
        return {
            "ok": False,
            "batch_name": batch_name,
            "message": f"未找到批次：{batch_name}",
            "items": [],
        }
    rows = _filter_latest_manual_document_rows(all_rows, logistics_type=logistics_type)

    processed_items: list[dict] = []
    parsed_batch_versions: dict[str, str] = {}
    packing_parsed_count = 0
    source_recognized_count = 0
    updated_count = 0
    created_count = 0
    changed_field_count = 0
    skipped_count = 0
    failed_count = 0

    for row in rows:
        item = {
            "attachment_name": row.get("name"),
            "batch_name": row.get("batch"),
            "version_name": row.get("version"),
            "file_name": row.get("file_name") or "",
            "attachment_type": row.get("attachment_type") or "Other",
            "parse_status": row.get("parse_status") or "",
            "file_ext": _attachment_ext_from_row(row),
        }
        if skip_parsed and str(row.get("parse_status") or "").strip().lower() == "parsed":
            item["action"] = "skipped"
            item["reason"] = "资料已解析"
            skipped_count += 1
            processed_items.append(item)
            continue

        file_url = str(row.get("file_url") or "").strip()
        if not file_url:
            item["action"] = "failed"
            item["reason"] = "资料没有系统文件地址，请重新上传后再批量解析。"
            failed_count += 1
            processed_items.append(item)
            continue

        row_for_parse = {**row, "file_url": file_url}
        item["file_url"] = file_url
        item["file_ext"] = _attachment_ext_from_row(row_for_parse)

        if _is_excel_packing_attachment(row_for_parse):
            try:
                parse_result = apply_packing_list_fillable_fields(
                    batch_name=row.get("batch"),
                    version_name=row.get("version"),
                    attachment_name=row.get("name"),
                    file_url=file_url,
                    recalculate_after_writeback=False,
                    auto_create_unmatched_items=True,
                    trusted_server_payload=True,
                )
            except Exception as exc:
                item["action"] = "failed"
                item["reason"] = f"装箱单解析失败：{exc}"
                failed_count += 1
                processed_items.append(item)
                continue

            item["parse_mode"] = "packing_list"
            item["parse"] = _compact_packing_apply_result(parse_result)
            if not parse_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = parse_result.get("message") or "装箱单解析失败"
                failed_count += 1
                processed_items.append(item)
                continue

            item["action"] = "parsed"
            packing_parsed_count += 1
            row_updated_count = int(parse_result.get("updated_count") or 0)
            row_created_count = int(parse_result.get("created_count") or 0)
            row_changed_field_count = int(parse_result.get("changed_field_count") or 0)
            updated_count += row_updated_count
            created_count += row_created_count
            changed_field_count += row_changed_field_count
            if row_updated_count or row_created_count:
                parsed_batch_versions[parse_result.get("batch_doc_name") or row.get("batch")] = (
                    parse_result.get("version_name") or row.get("version") or ""
                )
            processed_items.append(item)
            continue

        if _is_source_document_attachment(row_for_parse):
            preview_result = preview_oa_source_attachment(str(row.get("name") or ""))
            item["parse_mode"] = "source_document"
            item["preview"] = _compact_source_document_preview_result(preview_result)
            if not preview_result.get("ok"):
                item["action"] = "failed"
                item["reason"] = preview_result.get("message") or "资料内容识别失败"
                failed_count += 1
                processed_items.append(item)
                continue

            classification = preview_result.get("classification") if isinstance(preview_result.get("classification"), dict) else {}
            item["action"] = "parsed"
            item["recognized_type"] = classification.get("code") or ""
            item["recognized_type_label"] = classification.get("label") or ""
            item["reason"] = preview_result.get("message") or "资料内容识别完成"
            source_recognized_count += 1
            processed_items.append(item)
            continue

        item["action"] = "skipped"
        item["reason"] = f"暂不支持自动识别 {item['file_ext'] or '未知'} 格式资料。"
        skipped_count += 1
        processed_items.append(item)

    recalculate_results = _recalculate_batches_after_attachment_parse(parsed_batch_versions) if recalculate else []
    parsed_count = packing_parsed_count + source_recognized_count

    return {
        "ok": failed_count == 0,
        "batch_name": batch_name or "",
        "resolved_batch_name": resolved_batch_name,
        "logistics_type": str(logistics_type or "").strip().upper(),
        "scanned_count": len(rows),
        "downloaded_count": 0,
        "parsed_count": parsed_count,
        "packing_parsed_count": packing_parsed_count,
        "source_recognized_count": source_recognized_count,
        "updated_count": updated_count,
        "created_count": created_count,
        "changed_field_count": changed_field_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "permission_blocked_count": 0,
        "file_access_blocked_count": 0,
        "permission_scopes": [],
        "recalculate_results": recalculate_results,
        "items": processed_items,
        "message": _build_oa_source_attachment_parse_message(
            source_label="人工补传资料",
            scanned_count=len(rows),
            downloaded_count=0,
            packing_parsed_count=packing_parsed_count,
            source_recognized_count=source_recognized_count,
            updated_count=updated_count,
            created_count=created_count,
            changed_field_count=changed_field_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            permission_blocked_count=0,
            file_access_blocked_count=0,
            permission_scopes=[],
        ),
    }


def preview_linked_purchase_expense_oa_from_env() -> dict:
    """从环境变量预览关联采购支出 OA，适合 bench execute 调试。"""

    batch_name = str(os.environ.get("OVERSEAS_COST_BATCH") or os.environ.get("OVERSEAS_COST_BATCH_NAME") or "").strip()
    if not batch_name:
        raise ValueError(_("请设置 OVERSEAS_COST_BATCH。"))
    return preview_linked_purchase_expense_oa(
        batch_name=batch_name,
        version_name=str(os.environ.get("OVERSEAS_COST_VERSION") or "").strip() or None,
        env_file=str(os.environ.get("DINGTALK_ENV_FILE") or "").strip() or None,
    )


def import_purchase_expense_oa(
    *,
    batch_name: str,
    source_instance_id: str | None = None,
    approval_no: str | None = None,
    official_url: str | None = None,
    version_name: str | None = None,
    detail_rows_json: str | None = None,
) -> dict:
    """第一版采购支出 OA 导入。

    当前职责：
    1. 收口采购支出 OA 的来源标识
    2. 预览明细行会映射到哪些采购字段
    3. 有 Frappe 环境时，按批次内 SKU 自动匹配并回填采购价格字段
    """

    if frappe is not None:
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "source_instance_id": source_instance_id,
            "message": "该旧接口不再接受客户端提交的采购审批明细；请使用由服务器读取 PostgreSQL 的关联采购审批同步。",
        }

    raw_rows = _load_rows(detail_rows_json)
    preview_items = _build_preview_rows(raw_rows, map_purchase_expense_row_to_item)
    dingtalk_payload = build_dingtalk_order_payload(
        batch_name=batch_name,
        approval_no=approval_no,
        instance_id=source_instance_id,
        official_url=official_url,
    )

    def build_purchase_updates(mapped_row: dict, _target: dict) -> dict:
        return {
            "unit_price": mapped_row.get("unit_price"),
            "purchase_currency": mapped_row.get("purchase_currency"),
            "goods_value": mapped_row.get("goods_value"),
            "source_type": mapped_row.get("source_type"),
            "source_doc_no": approval_no or source_instance_id or "",
            "dingtalk_instance_id": source_instance_id or "",
            "dingtalk_official_url": official_url or "",
            "parse_status": "SUCCESS",
        }

    writeback_result = _run_item_writeback(
        batch_name=batch_name,
        version_name=version_name,
        mapped_rows=preview_items,
        update_builder=build_purchase_updates,
        action_remark="采购支出 OA 导入回填采购价格字段",
        trust_unique_material_code=True,
    )

    return {
        "ok": True,
        "queued": False,
        "batch_name": batch_name,
        "version_name": writeback_result.get("version_name") or version_name,
        "source_type": "PURCHASE_EXPENSE_OA",
        "approval_no": approval_no,
        "source_instance_id": source_instance_id,
        "official_url": official_url,
        "mapped_preview_count": len(preview_items),
        "mapped_preview_items": preview_items[:20],
        "dingtalk_payload": dingtalk_payload,
        "writeback_targets": [
            "unit_price",
            "purchase_currency",
            "goods_value",
            "source_type",
            "source_doc_no",
            "dingtalk_instance_id",
            "dingtalk_official_url",
            "parse_status",
        ],
        "writeback_result": writeback_result,
        "message": writeback_result["message"],
    }


def parse_packing_list_attachment(
    *,
    batch_name: str,
    attachment_name: str | None = None,
    file_url: str | None = None,
    version_name: str | None = None,
    template_hint: str | None = None,
    sheet_rows_json: str | None = None,
) -> dict:
    """第一版装箱单解析回填。

    当前职责：
    1. 描述解析任务与模板策略
    2. 预览装箱单明细会映射到哪些物理属性字段
    3. 有 Frappe 环境时，把已解析出的字段回填到批次明细
    """

    if frappe is not None:
        return {
            "ok": False,
            "untrusted_source": True,
            "batch_name": batch_name,
            "attachment_name": attachment_name,
            "message": "该旧接口不再接受浏览器提交的装箱明细；请使用装箱来源预览与确认接口。",
        }

    raw_rows = _load_rows(sheet_rows_json)
    preview_items = _build_preview_rows(raw_rows, map_packing_list_row_to_item)
    parse_task = attachment_parse_service.build_packing_list_parse_task(
        batch_name=batch_name,
        version_name=version_name,
        attachment_name=attachment_name,
        file_url=file_url,
        template_hint=template_hint,
    )

    def build_packing_updates(mapped_row: dict, _target: dict) -> dict:
        return {
            "actual_shipped_qty": mapped_row.get("actual_shipped_qty"),
            "gross_weight_kg": mapped_row.get("gross_weight_kg"),
            "volume_m3": mapped_row.get("volume_m3"),
            "volume_weight_kg": mapped_row.get("volume_weight_kg"),
            "chargeable_weight_kg": mapped_row.get("chargeable_weight_kg"),
            "hs_code": mapped_row.get("hs_code"),
            "source_type": mapped_row.get("source_type"),
            "source_file_name": attachment_name or "",
            "source_doc_no": file_url or attachment_name or "",
            "parse_status": "SUCCESS",
        }

    writeback_result = _run_item_writeback(
        batch_name=batch_name,
        version_name=version_name,
        mapped_rows=preview_items,
        update_builder=build_packing_updates,
        action_remark="装箱单附件解析回填实际发货与物理属性字段",
        resolve_ambiguous_by_sequence=True,
    )

    return {
        "ok": True,
        "queued": False,
        "batch_name": batch_name,
        "version_name": writeback_result.get("version_name") or version_name,
        "attachment_name": attachment_name,
        "file_url": file_url,
        "source_type": "PACKING_LIST",
        "mapped_preview_count": len(preview_items),
        "mapped_preview_items": preview_items[:20],
        "parse_task": parse_task,
        "writeback_targets": parse_task["parse_targets"],
        "writeback_result": writeback_result,
        "message": writeback_result["message"],
    }
