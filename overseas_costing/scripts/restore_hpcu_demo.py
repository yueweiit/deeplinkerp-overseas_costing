"""Restore the HPCU5155607 local demo batch.

Usage:
bench --site development.localhost execute overseas_costing.scripts.restore_hpcu_demo.restore
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import shutil
from pathlib import Path

import frappe

from overseas_costing.scripts.compare_manual_excel_baseline import load_manual_baseline
from overseas_costing.services import batch_service, calculate_service, import_service
from overseas_costing.utils.dingtalk import build_desktop_approval_url


BATCH_NO = "HPCU5155607"
CUSTOMS_NO = "26 16 1681 6000151"
PACKING_SOURCE = Path("/mnt/c/Users/lin/OneDrive/Desktop/2026.1.29装箱单.xlsx")
TAX_SOURCE = Path("/mnt/c/Users/lin/OneDrive/Desktop/PD_MZ260108凭证.pdf")
MANUAL_BASELINE_SOURCE = Path("/mnt/c/Users/lin/OneDrive/Desktop/墨西哥进口物料综合成本核算.xlsx")
PACKING_FILE_NAME = "2026.1.29装箱单.xlsx"
TAX_FILE_NAME = "PD_MZ260108凭证.pdf"
MANUAL_BASELINE_FILE_NAME = "墨西哥进口物料综合成本核算.xlsx"
SOURCE_APPROVAL_NO = "202601291020000337788"
SOURCE_INSTANCE_ID = "jvPKK8z8QFinWAZ3UgMJmw04891769653233"
OA_SEA_FREIGHT_RMB = 13976.3
DEMO_ESTIMATED_TAX_TOTAL_MXN = 130186.0


def restore() -> dict:
    """Reset and rebuild the local demo batch from the two demo attachments."""

    _ensure_source_files()
    reset_result = _reset_existing_batch()
    create_result = _create_batch()
    if not create_result.get("ok"):
        return {"ok": False, "stage": "create_batch", "reset": reset_result, "create": create_result}

    batch_name = create_result["batch_name"]
    version_name = create_result["version_name"]
    _mark_batch_as_oa_demo(batch_name)

    packing_file_url = _copy_to_private_files(PACKING_SOURCE, PACKING_FILE_NAME)
    tax_file_url = _copy_to_private_files(TAX_SOURCE, TAX_FILE_NAME)

    packing_attachment = import_service.register_manual_document_attachment(
        batch_name=batch_name,
        version_name=version_name,
        logistics_type="SEA",
        slot_code="sea_packing_list",
        slot_label="装箱单",
        attachment_type="Packing List",
        file_url=packing_file_url,
        file_name=PACKING_FILE_NAME,
        required=1,
        remark="HPCU5155607 演示资料：装箱单",
    )
    tax_attachment = import_service.register_manual_document_attachment(
        batch_name=batch_name,
        version_name=version_name,
        logistics_type="SEA",
        slot_code="sea_tax_certificate",
        slot_label="完税凭证（最终核对）",
        attachment_type="Tax Certificate",
        file_url=tax_file_url,
        file_name=TAX_FILE_NAME,
        required=0,
        remark="HPCU5155607 演示资料：完税凭证",
    )

    parse_result = import_service.parse_manual_document_attachments(
        batch_name=batch_name,
        logistics_type="SEA",
        skip_parsed=False,
        recalculate=False,
    )

    allocation_rule = _create_freight_allocation_rule(batch_name, version_name)
    tax_estimate = _seed_demo_estimated_tax(batch_name, version_name)
    recalculate_result = calculate_service.recalculate_batch(batch_name=batch_name, version_name=version_name)
    tax_certificate_result = import_service.save_tax_certificate_parse_result(
        source_name=TAX_FILE_NAME,
        file_url=tax_file_url,
        batch_name=batch_name,
    )
    tax_records = import_service.list_tax_certificate_parse_records(batch_name=batch_name, limit=5)
    summary = _build_summary(batch_name, version_name, tax_certificate_result)

    return {
        "ok": True,
        "batch_name": batch_name,
        "version_name": version_name,
        "reset": reset_result,
        "attachments": {
            "packing": packing_attachment,
            "tax_certificate_manual": tax_attachment,
            "packing_file_url": packing_file_url,
            "tax_file_url": tax_file_url,
        },
        "parse": _compact_parse_result(parse_result),
        "allocation_rule": allocation_rule,
        "tax_estimate": tax_estimate,
        "recalculate": _compact_recalculate_result(recalculate_result),
        "tax_certificate": _compact_tax_certificate_result(tax_certificate_result),
        "tax_records": {
            "total": tax_records.get("total"),
            "items": tax_records.get("items", [])[:3],
        },
        "summary": summary,
    }


def restore_manual_baseline() -> dict:
    """Reset the demo batch to the historical manual Excel baseline.

    这个入口用于经理演示：明细口径采用人工核算表的 22 行，附件仍保留
    装箱单和完税凭证做追溯。系统会按费用池重新试算，避免只是硬塞最终结果。
    """

    _ensure_source_files(include_manual_baseline=True)
    manual = load_manual_baseline(
        file_path=MANUAL_BASELINE_SOURCE,
        source_sheet="2026年YUEWEI",
        customs_no=CUSTOMS_NO,
        waybill_no=BATCH_NO,
    )
    reset_result = _reset_existing_batch()
    create_result = _create_batch()
    if not create_result.get("ok"):
        return {"ok": False, "stage": "create_batch", "reset": reset_result, "create": create_result}

    batch_name = create_result["batch_name"]
    version_name = create_result["version_name"]
    _mark_batch_as_oa_demo(batch_name)
    _mark_manual_baseline_version(version_name, manual)

    packing_file_url = _copy_to_private_files(PACKING_SOURCE, PACKING_FILE_NAME)
    tax_file_url = _copy_to_private_files(TAX_SOURCE, TAX_FILE_NAME)
    manual_file_url = _copy_to_private_files(MANUAL_BASELINE_SOURCE, MANUAL_BASELINE_FILE_NAME)

    packing_attachment = import_service.register_manual_document_attachment(
        batch_name=batch_name,
        version_name=version_name,
        logistics_type="SEA",
        slot_code="sea_packing_list",
        slot_label="装箱单",
        attachment_type="Packing List",
        file_url=packing_file_url,
        file_name=PACKING_FILE_NAME,
        required=1,
        remark="HPCU5155607 演示资料：装箱单，作为原始追溯资料保留。",
    )
    tax_attachment = import_service.register_manual_document_attachment(
        batch_name=batch_name,
        version_name=version_name,
        logistics_type="SEA",
        slot_code="sea_tax_certificate",
        slot_label="完税凭证（最终核对）",
        attachment_type="Tax Certificate",
        file_url=tax_file_url,
        file_name=TAX_FILE_NAME,
        required=0,
        remark="HPCU5155607 演示资料：完税凭证，作为最终核对资料保留。",
    )
    manual_attachment = import_service.register_manual_document_attachment(
        batch_name=batch_name,
        version_name=version_name,
        logistics_type="SEA",
        slot_code="sea_cost_baseline",
        slot_label="人工核算基准表",
        attachment_type="Excel Main Table",
        file_url=manual_file_url,
        file_name=MANUAL_BASELINE_FILE_NAME,
        required=0,
        remark=f"演示基准：{manual.get('source_range')}，用于和系统试算口径对齐。",
    )

    inserted_items = _insert_manual_baseline_items(batch_name, version_name, manual)
    allocation_rules = _create_manual_baseline_allocation_rules(batch_name, version_name, manual["items"])
    recalculate_result = calculate_service.recalculate_batch(batch_name=batch_name, version_name=version_name)
    tax_certificate_result = import_service.save_tax_certificate_parse_result(
        source_name=TAX_FILE_NAME,
        file_url=tax_file_url,
        batch_name=batch_name,
    )
    tax_records = import_service.list_tax_certificate_parse_records(batch_name=batch_name, limit=5)
    summary = _build_summary(batch_name, version_name, tax_certificate_result)

    return {
        "ok": True,
        "mode": "manual_baseline",
        "batch_name": batch_name,
        "version_name": version_name,
        "reset": reset_result,
        "manual_baseline": {
            "source_file": manual.get("source_file"),
            "source_range": manual.get("source_range"),
            "item_count": len(manual["items"]),
            "inserted_count": inserted_items["inserted_count"],
        },
        "attachments": {
            "packing": packing_attachment,
            "tax_certificate_manual": tax_attachment,
            "manual_baseline": manual_attachment,
            "packing_file_url": packing_file_url,
            "tax_file_url": tax_file_url,
            "manual_file_url": manual_file_url,
        },
        "allocation_rules": allocation_rules,
        "recalculate": _compact_recalculate_result(recalculate_result),
        "tax_certificate": _compact_tax_certificate_result(tax_certificate_result),
        "tax_records": {
            "total": tax_records.get("total"),
            "items": tax_records.get("items", [])[:3],
        },
        "summary": summary,
    }


def current_summary() -> dict:
    """Return the current restored demo batch summary without changing data."""

    batch_name = frappe.db.get_value("Overseas Cost Batch", {"batch_no": BATCH_NO}, "name")
    if not batch_name:
        return {"ok": False, "message": f"未找到演示批次：{BATCH_NO}"}
    version_name = frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
    tax_records = import_service.list_tax_certificate_parse_records(batch_name=batch_name, limit=5)
    return {
        "ok": True,
        "batch_name": batch_name,
        "version_name": version_name,
        "summary": _build_summary(batch_name, version_name, {}),
        "attachments": frappe.get_all(
            "Overseas Cost Attachment",
            filters={"batch": batch_name},
            fields=["source_type", "attachment_type", "file_name", "parse_status"],
            order_by="creation asc",
            limit_page_length=20,
        ),
        "tax_records": {
            "total": tax_records.get("total"),
            "items": tax_records.get("items", [])[:3],
        },
    }


def _ensure_source_files(*, include_manual_baseline: bool = False) -> None:
    sources = [PACKING_SOURCE, TAX_SOURCE]
    if include_manual_baseline:
        sources.append(MANUAL_BASELINE_SOURCE)
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise FileNotFoundError(_("演示资料不存在：{0}").format(', '.join(missing)))


def _reset_existing_batch() -> dict:
    existing_name = frappe.db.get_value("Overseas Cost Batch", {"batch_no": BATCH_NO}, "name")
    if not existing_name:
        return {"action": "skipped", "reason": "当前站点没有旧演示批次"}
    return calculate_service.delete_batch(batch_name=existing_name, remark="恢复 HPCU5155607 演示批次前清理旧数据")


def _create_batch() -> dict:
    payload = {
        "batch_no": BATCH_NO,
        "customs_no": CUSTOMS_NO,
        "waybill_no": BATCH_NO,
        "container_no": BATCH_NO,
        "transport_mode": "SEA",
        "project_collection": "演示批次",
        "source_approval_no": SOURCE_APPROVAL_NO,
        "source_instance_id": SOURCE_INSTANCE_ID,
        "source_dingtalk_url": build_desktop_approval_url(SOURCE_INSTANCE_ID),
        "import_remark": "HPCU5155607 海运全流程演示批次",
        "source_remark": "来源：钉钉国际物流 OA + 本地装箱单 + 完税凭证",
    }
    return batch_service.create_batch(json.dumps(payload, ensure_ascii=False))


def _mark_batch_as_oa_demo(batch_name: str) -> None:
    extra = {
        "oa_logistics_trace": {
            "logistics_fee": {
                "amount": OA_SEA_FREIGHT_RMB,
                "currency": "RMB",
                "source_label": "物流费用",
                "source_field": "物流费用",
                "source_value": str(OA_SEA_FREIGHT_RMB),
            },
            "demo_notice": "本批次用于本地演示闭环；附件来自桌面演示资料。",
        }
    }
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        {
            "source_type": "oa_logistics",
            "source_approval_status": "COMPLETED",
            "source_title": "李仲华提交的国际物流Logística Internacional",
            "source_creator_name": "李仲华",
            "source_created_at": "2026-01-29 10:20:00",
            "source_finished_at": "2026-04-21 17:16:00",
            "source_attachment_count": 1,
            "extra_json": json.dumps(extra, ensure_ascii=False),
        },
        update_modified=False,
    )
    frappe.db.commit()


def _copy_to_private_files(source: Path, file_name: str) -> str:
    target_dir = Path(frappe.get_site_path("private", "files"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_name
    shutil.copy2(source, target)
    file_url = f"/private/files/{file_name}"
    file_doc_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    values = {
        "file_name": file_name,
        "file_url": file_url,
        "is_private": 1,
        "folder": "Home/Attachments",
        "file_size": target.stat().st_size,
    }
    if file_doc_name:
        file_doc = frappe.get_doc("File", file_doc_name)
        for fieldname, value in values.items():
            setattr(file_doc, fieldname, value)
        file_doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "File", **values}).insert(ignore_permissions=True)
    frappe.db.commit()
    return file_url


def _create_freight_allocation_rule(batch_name: str, version_name: str) -> dict:
    doc = frappe.get_doc(
        {
            "doctype": "Overseas Cost Allocation Rule",
            "batch": batch_name,
            "version": version_name,
            "rule_code": "oa_sea_freight_rmb",
            "expense_category": "海运运费（OA）",
            "allocation_basis": "gross_weight",
            "basis_field": "gross_weight",
            "currency": "RMB",
            "amount": OA_SEA_FREIGHT_RMB,
            "priority_no": 10,
            "is_enabled": 1,
            "is_active": 1,
            "remark": "来自国际物流 OA 的物流费用字段，用于演示按毛重分摊。",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "amount": OA_SEA_FREIGHT_RMB, "basis": "gross_weight"}


def _mark_manual_baseline_version(version_name: str, manual: dict) -> None:
    frappe.db.set_value(
        "Overseas Cost Version",
        version_name,
        {
            "version_code": "演示-人工核算基准",
            "version_type": "Actual",
            "source_type": "Manual Excel Baseline",
            "remark": f"经理演示用：明细来自 {manual.get('source_file')} / {manual.get('source_range')}，附件用于追溯。",
        },
        update_modified=False,
    )
    frappe.db.commit()


def _insert_manual_baseline_items(batch_name: str, version_name: str, manual: dict) -> dict:
    fieldnames = _doctype_fieldnames("Overseas Cost Item")
    inserted = []
    for index, row in enumerate(manual["items"], start=1):
        values = {
            "doctype": "Overseas Cost Item",
            "batch": batch_name,
            "version": version_name,
            "row_no": index,
            "excel_row_no": row.get("manual_excel_row"),
            "source_type": "MANUAL_EXCEL_BASELINE",
            "source_doc_no": manual.get("source_range"),
            "source_file_name": manual.get("source_file"),
            "parse_status": "SUCCESS",
            "manual_override_flag": 1,
            "manual_override_reason": "演示基准：来自人工核算 Excel，后续正式数据仍应从 OA/附件逐步补齐。",
            "purchase_currency": "人民币RMB",
            "transport_mode": "SEA",
            "chargeable_weight_kg": row.get("gross_weight_kg"),
        }
        for fieldname in (
            "material_code",
            "product_name",
            "unit_price",
            "quantity",
            "goods_value",
            "import_name",
            "hs_code",
            "category",
            "customs_no",
            "waybill_no",
            "china_misc_rmb",
            "china_misc_mxn",
            "china_ocean_usd",
            "igi_amount",
            "iva_amount",
            "import_tax_total",
            "mexico_customs_mxn",
            "mexico_customs_rmb",
            "mexico_customs_usd",
            "mexico_inland_mxn",
            "mexico_misc_mxn",
            "mexico_inland_misc_rmb",
            "china_to_mexico_freight_rmb",
            "gross_weight_kg",
            "weight_ratio",
            "freight_alloc_rmb",
            "freight_alloc_mxn",
            "total_logistics_mxn",
            "alloc_price_mxn",
            "total_cost_rmb",
            "total_unit_rmb",
            "project_collection",
        ):
            if row.get(fieldname) is not None:
                values[fieldname] = row.get(fieldname)
        doc = frappe.get_doc({key: value for key, value in values.items() if key == "doctype" or key in fieldnames})
        inserted.append(doc.insert(ignore_permissions=True).name)
    frappe.db.commit()
    return {"inserted_count": len(inserted), "items": inserted[:5]}


def _create_manual_baseline_allocation_rules(batch_name: str, version_name: str, rows: list[dict]) -> list[dict]:
    specs = []
    rule_inputs = [
        {
            "fieldname": "china_misc_rmb",
            "rule_code": "manual_china_misc_rmb",
            "expense_category": "中国段杂费",
            "allocation_basis": "goods_value",
            "currency": "RMB",
            "remark": "演示基准：来自人工核算表中国杂费合计，按货值分摊进系统试算。",
            "priority_no": 10,
        },
        {
            "fieldname": "china_to_mexico_freight_rmb",
            "rule_code": "manual_freight_rmb",
            "expense_category": "中国到墨西哥运费",
            "allocation_basis": "gross_weight",
            "currency": "RMB",
            "remark": "演示基准：来自人工核算表整票运费，按人工表重量比例分摊。",
            "priority_no": 20,
        },
        {
            "fieldname": "mexico_inland_misc_rmb",
            "rule_code": "manual_mexico_inland_misc_rmb",
            "expense_category": "墨西哥内陆/杂费",
            "allocation_basis": "gross_weight",
            "currency": "RMB",
            "remark": "演示基准：来自人工核算表墨西哥内陆运输及杂费合计，按重量分摊进系统试算。",
            "priority_no": 30,
        },
    ]
    for item in rule_inputs:
        amount = _sum_manual_field(rows, item["fieldname"])
        if not amount:
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Overseas Cost Allocation Rule",
                "batch": batch_name,
                "version": version_name,
                "rule_code": item["rule_code"],
                "expense_category": item["expense_category"],
                "allocation_basis": item["allocation_basis"],
                "basis_field": item["allocation_basis"],
                "currency": item["currency"],
                "amount": amount,
                "priority_no": item["priority_no"],
                "is_enabled": 1,
                "is_active": 1,
                "remark": item["remark"],
            }
        ).insert(ignore_permissions=True)
        specs.append(
            {
                "name": doc.name,
                "rule_code": item["rule_code"],
                "amount": amount,
                "basis": item["allocation_basis"],
            }
        )
    frappe.db.commit()
    return specs


def _seed_demo_estimated_tax(batch_name: str, version_name: str) -> dict:
    rows = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version_name},
        fields=["name", "goods_value"],
        limit_page_length=10000,
    )
    total_goods_value = sum(_to_float(row.get("goods_value")) for row in rows)
    if not rows or not total_goods_value:
        return {"action": "skipped", "reason": "没有可分摊暂估税费的物料货值"}

    allocated_total = 0.0
    for index, row in enumerate(rows, start=1):
        if index == len(rows):
            amount_mxn = round(DEMO_ESTIMATED_TAX_TOTAL_MXN - allocated_total, 6)
        else:
            amount_mxn = round(DEMO_ESTIMATED_TAX_TOTAL_MXN * _to_float(row.get("goods_value")) / total_goods_value, 6)
            allocated_total += amount_mxn
        frappe.db.set_value(
            "Overseas Cost Item",
            row["name"],
            {
                "import_tax_total": amount_mxn,
                "mexico_customs_mxn": amount_mxn,
                "mexico_customs_rmb": round(amount_mxn / 2.6, 6),
                "source_remark": "演示暂估税费：凭证到达前按货值比例分摊，用于展示凭证差异对比。",
            },
            update_modified=False,
        )
    frappe.db.commit()
    return {
        "action": "seeded",
        "amount_mxn": DEMO_ESTIMATED_TAX_TOTAL_MXN,
        "basis": "goods_value",
        "item_count": len(rows),
        "remark": "演示暂估税费，不代表正式财务来源。",
    }


def _build_summary(batch_name: str, version_name: str, tax_certificate_result: dict) -> dict:
    batch = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_name,
        [
            "batch_no",
            "customs_no",
            "waybill_no",
            "container_no",
            "status",
            "item_count",
            "total_goods_value",
            "total_gross_weight_kg",
            "estimated_total_cost_rmb",
        ],
        as_dict=True,
    ) or {}
    item_totals = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version_name},
        fields=[
            "sum(goods_value) as goods_value",
            "sum(gross_weight_kg) as gross_weight_kg",
            "sum(volume_m3) as volume_m3",
            "sum(freight_alloc_rmb) as freight_alloc_rmb",
            "sum(import_tax_total) as import_tax_total_mxn",
            "sum(total_cost_rmb) as total_cost_rmb",
        ],
    )
    reconciliation = ((tax_certificate_result or {}).get("preview") or {}).get("reconciliation") or {}
    return {
        "batch": dict(batch),
        "item_totals": dict(item_totals[0] if item_totals else {}),
        "voucher_reconciliation": {
            "status": reconciliation.get("status"),
            "status_label": reconciliation.get("status_label"),
            "voucher": reconciliation.get("voucher"),
            "system": reconciliation.get("system"),
            "difference": reconciliation.get("difference"),
            "passed_count": reconciliation.get("passed_count"),
            "review_count": reconciliation.get("review_count"),
            "failed_count": reconciliation.get("failed_count"),
        },
    }


def _compact_parse_result(result: dict) -> dict:
    return {
        "ok": result.get("ok"),
        "scanned_count": result.get("scanned_count"),
        "parsed_count": result.get("parsed_count"),
        "packing_parsed_count": result.get("packing_parsed_count"),
        "source_recognized_count": result.get("source_recognized_count"),
        "created_count": result.get("created_count"),
        "updated_count": result.get("updated_count"),
        "changed_field_count": result.get("changed_field_count"),
        "skipped_count": result.get("skipped_count"),
        "failed_count": result.get("failed_count"),
        "message": result.get("message"),
    }


def _compact_recalculate_result(result: dict) -> dict:
    return {
        "ok": result.get("ok"),
        "batch_name": result.get("batch_name"),
        "version_name": result.get("version_name"),
        "summary_snapshot": result.get("summary_snapshot"),
        "message": result.get("message"),
    }


def _compact_tax_certificate_result(result: dict) -> dict:
    preview = result.get("preview") or {}
    reconciliation = preview.get("reconciliation") or {}
    return {
        "ok": result.get("ok"),
        "saved": result.get("saved"),
        "action": result.get("action"),
        "attachment_name": result.get("attachment_name"),
        "source_doc_no": result.get("source_doc_no"),
        "fx_sync": result.get("fx_sync"),
        "cost_refresh": result.get("cost_refresh"),
        "voucher": reconciliation.get("voucher"),
        "system": reconciliation.get("system"),
        "difference": reconciliation.get("difference"),
        "status": reconciliation.get("status"),
        "status_label": reconciliation.get("status_label"),
        "message": result.get("message"),
    }


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_manual_field(rows: list[dict], fieldname: str) -> float:
    return sum(_to_float(row.get(fieldname)) for row in rows)


def _doctype_fieldnames(doctype: str) -> set[str]:
    meta = frappe.get_meta(doctype)
    return {field.fieldname for field in meta.fields} | {"name", "doctype"}
