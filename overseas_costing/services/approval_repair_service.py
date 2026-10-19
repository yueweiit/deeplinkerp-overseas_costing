"""钉钉审批同步缺口的全局核对与受控修复服务。

综合成本端只读取阿里云 PostgreSQL；需要补拉时，仅通过
SECURITY DEFINER 函数提交任务，不直接调用钉钉 API，也不直接写审批基础表。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import uuid
from collections import Counter
from typing import Any, Iterable

try:
    import frappe
except Exception:  # pragma: no cover - 本地单元测试不强制安装 Frappe
    frappe = None

from overseas_costing.integrations.dingtalk_approval_source import (
    ApprovalRepairSubmitter,
    ApprovalSourceConfig,
)
from overseas_costing.scripts.import_oa_logistics import (
    extract_linked_purchase_approvals,
    is_hidden_approval_status,
    resolve_logistics_process_code,
    resolve_purchase_process_code,
    save_sea_approvals_to_erp,
    summarize_approval,
)
from overseas_costing.services.dingtalk_approval_service import _get_approval_source


REPAIRING_STATES = {"pending", "running", "retry"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _business_id(batch: dict) -> str:
    return _text(batch.get("source_approval_no") or batch.get("batch_no"))


def _repair_state(status: dict | None) -> str:
    value = _text((status or {}).get("status")).lower()
    if value in REPAIRING_STATES:
        return "repairing"
    if value == "manual_required":
        return "manual_required"
    return ""


def classify_approval_sources(
    batches: Iterable[dict],
    coverage: dict,
    repair_statuses: dict[str, dict],
    *,
    expected_process_code: str,
) -> list[dict]:
    """对全部批次做纯函数分类，便于列表、统计和定时任务共用。"""

    by_instance = coverage.get("by_instance") or {}
    by_business = coverage.get("by_business") or {}
    expected_code = _text(expected_process_code)
    classified: list[dict] = []

    for source_batch in batches:
        batch = dict(source_batch or {})
        batch_name = _text(batch.get("name"))
        instance_id = _text(batch.get("source_instance_id"))
        approval_no = _business_id(batch)
        base = {
            "batch_name": batch_name,
            "process_instance_id": instance_id,
            "expected_business_id": approval_no,
            "expected_process_code": expected_code,
        }

        if not instance_id:
            candidates = [
                dict(row)
                for row in (by_business.get(approval_no) or [])
                if _text(row.get("process_code")) == expected_code
                and _text(row.get("business_id")) == approval_no
                and _text(row.get("process_instance_id"))
            ]
            if len(candidates) == 1:
                classified.append({
                    **base,
                    "state": "instance_id_recoverable",
                    "resolved_instance_id": _text(candidates[0].get("process_instance_id")),
                    "reason": "已通过唯一审批编号找到正确流程实例。",
                })
            else:
                classified.append({
                    **base,
                    "state": "manual_required",
                    "reason_code": "INSTANCE_ID_MISSING" if not candidates else "BUSINESS_ID_AMBIGUOUS",
                    "reason": "缺少流程实例 ID，无法通过审批编号唯一确定。",
                })
            continue

        row = by_instance.get(instance_id)
        status = repair_statuses.get(instance_id) or {}
        if not row:
            queued_state = _repair_state(status)
            if queued_state:
                classified.append({
                    **base,
                    "state": queued_state,
                    "repair_status": dict(status),
                    "reason_code": _text(status.get("error_code")),
                    "reason": _text(status.get("error_message"))
                    or ("钉钉审批正在补同步。" if queued_state == "repairing" else "钉钉审批需要人工处理。"),
                })
            else:
                classified.append({
                    **base,
                    "state": "missing_in_postgres",
                    "reason_code": "APPROVAL_NOT_SYNCED",
                    "reason": "综合成本已保存审批引用，但同步库中尚无该流程。",
                })
            continue

        actual_business_id = _text(row.get("business_id"))
        actual_process_code = _text(row.get("process_code"))
        if actual_business_id != approval_no or actual_process_code != expected_code:
            classified.append({
                **base,
                "state": "manual_required",
                "reason_code": "SOURCE_MISMATCH",
                "reason": "同步库流程与综合成本保存的审批编号或流程模板不一致。",
                "actual_business_id": actual_business_id,
                "actual_process_code": actual_process_code,
            })
        elif is_hidden_approval_status(row.get("status"), row.get("result")):
            classified.append({
                **base,
                "state": "excluded",
                "reason_code": "APPROVAL_EXCLUDED",
                "reason": "审批已拒绝、撤销或终止，仅保留查看与审计。",
                "approval_status": _text(row.get("status")),
                "approval_result": _text(row.get("result")),
            })
        else:
            classified.append({
                **base,
                "state": "available",
                "approval_status": _text(row.get("status")),
                "approval_result": _text(row.get("result")),
                "repair_status": dict(status),
            })

    return classified


def _get_repair_submitter() -> ApprovalRepairSubmitter:
    from overseas_costing.scripts.import_oa_logistics import (
        _runtime_config_int,
        _runtime_config_value,
    )

    common = {
        "host": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_HOST", "overseas_costing_oa_db_host", default="10.203.0.1"
        ),
        "port": _runtime_config_int(
            "OVERSEAS_COSTING_OA_DB_PORT", "overseas_costing_oa_db_port", default=5432
        ),
        "database": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_NAME", "overseas_costing_oa_db_name", default="dingtalk_oa"
        ),
        "sslmode": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_SSLMODE", "overseas_costing_oa_db_sslmode"
        ) or None,
    }
    config = ApprovalSourceConfig(
        **common,
        user=_runtime_config_value(
            "OVERSEAS_COSTING_APPROVAL_REPAIR_DB_USER",
            "overseas_costing_approval_repair_db_user",
            "OVERSEAS_COSTING_PACKING_REFRESH_DB_USER",
            "overseas_costing_packing_refresh_db_user",
            default="costing_job_submitter",
        ),
        password=_runtime_config_value(
            "OVERSEAS_COSTING_APPROVAL_REPAIR_DB_PASSWORD",
            "overseas_costing_approval_repair_db_password",
            "OVERSEAS_COSTING_PACKING_REFRESH_DB_PASSWORD",
            "overseas_costing_packing_refresh_db_password",
        ),
    )
    if not config.user or not config.password:
        raise RuntimeError("审批修复提交账号未配置。")
    return ApprovalRepairSubmitter(config)


def _request_key(*parts: Any) -> str:
    raw = "\x1f".join(_text(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _submit_repair(
    *,
    submitter: ApprovalRepairSubmitter,
    corp_id: str,
    item: dict,
    requested_by: str,
    trigger_source: str,
    nonce: str = "",
) -> int:
    instance_id = _text(item.get("process_instance_id"))
    approval_no = _text(item.get("expected_business_id"))
    process_code = _text(item.get("expected_process_code"))
    return submitter.request_repair(
        corp_id=corp_id,
        process_instance_id=instance_id,
        expected_business_id=approval_no,
        expected_process_code=process_code,
        expected_purpose=_text(item.get("expected_purpose")) or "international_logistics",
        request_key=_request_key(
            "approval-repair-v1", corp_id, instance_id, approval_no, process_code,
            trigger_source, nonce,
        ),
        requested_by=_text(requested_by)[:200],
        trigger_source=trigger_source,
    )


def _source_batches() -> list[dict]:
    if frappe is None:
        raise RuntimeError("Frappe 未加载。")
    return list(frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=[
            "name", "batch_no", "source_type", "source_approval_no",
            "source_instance_id", "source_approval_status", "extra_json", "current_version",
        ],
        limit_page_length=10000,
    ) or [])


def _recover_instance_ids(items: list[dict]) -> int:
    """仅在审批编号唯一命中时回填实例 ID。"""

    if frappe is None or not getattr(frappe, "db", None):
        return 0
    updated = 0
    for item in items:
        if item.get("state") != "instance_id_recoverable":
            continue
        resolved = _text(item.get("resolved_instance_id"))
        if not resolved:
            continue
        row = frappe.db.get_value(
            "Overseas Cost Batch", item["batch_name"], ["extra_json"], as_dict=True
        ) or {}
        extra = _as_dict(row.get("extra_json"))
        extra["approval_instance_id_recovery"] = {
            "resolved_instance_id": resolved,
            "expected_business_id": _text(item.get("expected_business_id")),
            "method": "unique_business_id_and_process_code",
        }
        frappe.db.set_value(
            "Overseas Cost Batch",
            item["batch_name"],
            {
                "source_instance_id": resolved,
                "extra_json": json.dumps(extra, ensure_ascii=False, default=str),
            },
            update_modified=True,
        )
        item["process_instance_id"] = resolved
        item["state"] = "available"
        item["reason"] = "已通过唯一审批编号回填流程实例 ID。"
        updated += 1
    return updated


def _mark_repair_reconciled(batch_name: str, request_id: Any) -> None:
    if frappe is None or not getattr(frappe, "db", None):
        return
    row = frappe.db.get_value(
        "Overseas Cost Batch", batch_name, ["extra_json"], as_dict=True
    ) or {}
    payload = _as_dict(row.get("extra_json"))
    payload["approval_repair_reconciled_request_id"] = _text(request_id)
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        "extra_json",
        json.dumps(payload, ensure_ascii=False, default=str),
        update_modified=True,
    )


def _reconcile_successful_repairs(
    *, batches: list[dict], items: list[dict], source, payloads: dict | None = None
) -> dict:
    """仅回收由修复队列成功补齐的主审批，并明确禁止自动试算。"""

    batches_by_name = {_text(row.get("name")): row for row in batches}
    targets: list[dict] = []
    for item in items:
        repair_status = item.get("repair_status") or {}
        if item.get("state") != "available" or _text(repair_status.get("status")) != "success":
            continue
        batch = batches_by_name.get(_text(item.get("batch_name"))) or {}
        request_id = _text(repair_status.get("id"))
        marker = _text(_as_dict(batch.get("extra_json")).get("approval_repair_reconciled_request_id"))
        if request_id and marker == request_id:
            continue
        targets.append(item)

    instance_ids = list(dict.fromkeys(
        _text(item.get("process_instance_id"))
        for item in targets if _text(item.get("process_instance_id"))
    ))
    if payloads is None:
        payloads = source.get_instances(instance_ids) if instance_ids else {}
    reconciled: list[dict] = []
    failures: list[dict] = []
    for item in targets:
        instance_id = _text(item.get("process_instance_id"))
        payload = payloads.get(instance_id)
        if not isinstance(payload, dict):
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "REPAIRED_PAYLOAD_MISSING",
                "reason": "队列标记成功，但只读视图尚未返回审批正文。",
            })
            continue
        summary = summarize_approval(payload, process_instance_id=instance_id)
        result = save_sea_approvals_to_erp(
            {"ok": True, "items": [summary]},
            recalculate_after_sync=False,
        )
        if not result.get("ok") or result.get("failed_items"):
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "COSTING_RECONCILE_FAILED",
                "reason": result.get("message") or "审批已恢复，但采购事实同步失败。",
            })
            continue
        request_id = (item.get("repair_status") or {}).get("id")
        _mark_repair_reconciled(_text(item.get("batch_name")), request_id)
        reconciled.append({
            "batch_name": item.get("batch_name"),
            "process_instance_id": instance_id,
            "repair_request_id": request_id,
        })
    return {
        "reconciled_count": len(reconciled),
        "failed_count": len(failures),
        "items": reconciled,
        "failed": failures,
    }


def _linked_purchase_repair_items(
    *,
    main_items: list[dict],
    main_payloads: dict,
    coverage: dict,
    repair_statuses: dict[str, dict],
    expected_process_code: str,
) -> list[dict]:
    """从已验证的物流审批正文发现采购审批缺口。"""

    pseudo_batches: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for main in main_items:
        if main.get("state") != "available":
            continue
        main_id = _text(main.get("process_instance_id"))
        payload = main_payloads.get(main_id)
        if not isinstance(payload, dict):
            continue
        for link in extract_linked_purchase_approvals(payload):
            instance_id = _text(link.get("source_instance_id") or link.get("proc_inst_id"))
            approval_no = _text(
                link.get("source_approval_no") or link.get("approval_no") or link.get("business_id")
            )
            key = (instance_id, approval_no)
            if not instance_id or not approval_no or key in seen:
                continue
            seen.add(key)
            pseudo_batches.append({
                "name": _text(main.get("batch_name")),
                "batch_no": approval_no,
                "source_approval_no": approval_no,
                "source_instance_id": instance_id,
            })
    classified = classify_approval_sources(
        pseudo_batches,
        coverage,
        repair_statuses,
        expected_process_code=expected_process_code,
    )
    for item in classified:
        item["expected_purpose"] = "purchase_expense"
    return classified


def audit_and_queue_dingtalk_approval_repairs() -> dict:
    """一次批量核对全部 OA 物流批次，并仅为真正缺失的实例提交任务。"""

    batches = _source_batches()
    instance_ids = list(dict.fromkeys(
        _text(row.get("source_instance_id")) for row in batches if _text(row.get("source_instance_id"))
    ))
    business_ids = list(dict.fromkeys(_business_id(row) for row in batches if _business_id(row)))
    expected_code = resolve_logistics_process_code()
    source = _get_approval_source()

    try:
        coverage = source.get_reference_coverage(instance_ids, business_ids)
        repair_statuses = source.get_repair_statuses(instance_ids)
        process_context = source.get_process_context(expected_code) or {}
    except Exception as exc:
        return {
            "ok": False,
            "scanned_count": len(batches),
            "queued_count": 0,
            "counts": {"data_source_unavailable": len(batches)},
            "items": [],
            "failed": [{"reason_code": "DATA_SOURCE_UNAVAILABLE", "reason": str(exc)}],
        }

    items = classify_approval_sources(
        batches, coverage, repair_statuses, expected_process_code=expected_code
    )
    recovered_count = _recover_instance_ids(items)
    available_main_ids = list(dict.fromkeys(
        _text(item.get("process_instance_id"))
        for item in items
        if item.get("state") == "available" and _text(item.get("process_instance_id"))
    ))
    main_payloads = source.get_instances(available_main_ids) if available_main_ids else {}
    purchase_items: list[dict] = []
    purchase_code = resolve_purchase_process_code()
    if purchase_code and main_payloads:
        linked_refs: list[dict] = []
        for payload in main_payloads.values():
            linked_refs.extend(extract_linked_purchase_approvals(payload))
        linked_instance_ids = list(dict.fromkeys(
            _text(row.get("source_instance_id") or row.get("proc_inst_id"))
            for row in linked_refs
            if _text(row.get("source_instance_id") or row.get("proc_inst_id"))
        ))
        linked_business_ids = list(dict.fromkeys(
            _text(row.get("source_approval_no") or row.get("approval_no") or row.get("business_id"))
            for row in linked_refs
            if _text(row.get("source_approval_no") or row.get("approval_no") or row.get("business_id"))
        ))
        linked_coverage = source.get_reference_coverage(linked_instance_ids, linked_business_ids)
        linked_statuses = source.get_repair_statuses(linked_instance_ids)
        purchase_items = _linked_purchase_repair_items(
            main_items=items,
            main_payloads=main_payloads,
            coverage=linked_coverage,
            repair_statuses=linked_statuses,
            expected_process_code=purchase_code,
        )
    corp_id = _text(process_context.get("corp_id"))
    submitter = None
    queued_count = 0
    failures: list[dict] = []
    for item in items:
        if item.get("state") != "missing_in_postgres":
            continue
        if not corp_id:
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "CORP_CONTEXT_MISSING",
                "reason": "无法从允许的物流流程解析企业上下文。",
            })
            continue
        try:
            submitter = submitter or _get_repair_submitter()
            request_id = _submit_repair(
                submitter=submitter,
                corp_id=corp_id,
                item=item,
                requested_by="scheduler",
                trigger_source="costing_audit",
            )
            item["repair_request_id"] = request_id
            item["queued"] = True
            queued_count += 1
        except Exception as exc:
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "REPAIR_SUBMIT_FAILED",
                "reason": str(exc),
            })

    linked_queued_count = 0
    for item in purchase_items:
        if item.get("state") != "missing_in_postgres":
            continue
        if not corp_id:
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "CORP_CONTEXT_MISSING",
                "reason": "无法确定关联采购审批的企业上下文。",
            })
            continue
        try:
            submitter = submitter or _get_repair_submitter()
            request_id = _submit_repair(
                submitter=submitter,
                corp_id=corp_id,
                item=item,
                requested_by="scheduler",
                trigger_source="linked_purchase",
            )
            item["repair_request_id"] = request_id
            item["queued"] = True
            linked_queued_count += 1
        except Exception as exc:
            failures.append({
                "batch_name": item.get("batch_name"),
                "reason_code": "LINKED_REPAIR_SUBMIT_FAILED",
                "reason": str(exc),
            })

    counts = dict(Counter(_text(item.get("state")) for item in items))
    reconcile_result = _reconcile_successful_repairs(
        batches=batches,
        items=items,
        source=source,
        payloads=main_payloads,
    )
    return {
        "ok": not failures and not reconcile_result.get("failed_count"),
        "scanned_count": len(items),
        "queued_count": queued_count,
        "linked_purchase_queued_count": linked_queued_count,
        "recovered_instance_id_count": recovered_count,
        "counts": counts,
        "items": items,
        "linked_purchase_items": purchase_items,
        "failed": [*failures, *(reconcile_result.get("failed") or [])],
        "reconcile": reconcile_result,
    }


def request_batch_repair(batch_name: str, requested_by: str) -> dict:
    """由已通过批次写权限校验的 API 提交单笔补同步。"""

    if frappe is None:
        raise RuntimeError("Frappe 未加载。")
    row = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_name,
        ["name", "batch_no", "source_type", "source_approval_no", "source_instance_id"],
        as_dict=True,
    )
    if not row or _text(row.get("source_type")) != "oa_logistics":
        raise ValueError(_("当前批次不是钉钉国际物流来源。"))
    instance_id = _text(row.get("source_instance_id"))
    approval_no = _business_id(row)
    if not instance_id or not approval_no:
        raise ValueError(_("当前批次缺少审批编号或流程实例 ID，需先人工核对。"))

    process_code = resolve_logistics_process_code()
    source = _get_approval_source()
    context = source.get_process_context(process_code) or {}
    corp_id = _text(context.get("corp_id"))
    if not corp_id:
        raise RuntimeError("无法从同步库确定企业上下文。")
    item = {
        "batch_name": _text(row.get("name")) or batch_name,
        "process_instance_id": instance_id,
        "expected_business_id": approval_no,
        "expected_process_code": process_code,
    }
    request_id = _submit_repair(
        submitter=_get_repair_submitter(),
        corp_id=corp_id,
        item=item,
        requested_by=requested_by,
        trigger_source="manual",
        nonce=uuid.uuid4().hex,
    )
    return {
        "ok": True,
        "batch_name": item["batch_name"],
        "source_state": "repairing",
        "repair_request_id": request_id,
        "message": "钉钉审批已加入补同步队列。",
    }


def scheduled_audit_and_repair_dingtalk_approvals() -> dict:
    """Frappe 每 6 小时调度入口。"""

    result = audit_and_queue_dingtalk_approval_repairs()
    if frappe is not None and hasattr(frappe, "logger"):
        log_summary = {
            "ok": result.get("ok"),
            "scanned_count": result.get("scanned_count"),
            "queued_count": result.get("queued_count"),
            "linked_purchase_queued_count": result.get("linked_purchase_queued_count"),
            "recovered_instance_id_count": result.get("recovered_instance_id_count"),
            "counts": result.get("counts"),
            "failed_count": len(result.get("failed") or []),
            "reconciled_count": (result.get("reconcile") or {}).get("reconciled_count", 0),
        }
        frappe.logger("overseas_costing").info(
            "dingtalk approval repair audit: %s",
            json.dumps(log_summary, ensure_ascii=False, default=str),
        )
    return result
