"""装箱计划表的本地物化缓存。

远端 PostgreSQL/MinIO 仍是可信来源；这里只保存通过完整性校验的最新成功副本，
用于目录和预览的本地读取。
"""

from __future__ import annotations

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message


from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import sleep as _sleep
from typing import Any
import uuid

from overseas_costing.services.logistics_settlement.model import digest, dumps


CATALOG_STATE_ID = "packing_sheet_catalog_v1"
STALE_AFTER = timedelta(hours=24)
RUN_LEASE = timedelta(hours=2)


class PackingSheetCacheError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _is_stale(last_success_at: Any, now: str) -> bool:
    success = _datetime(last_success_at)
    current = _datetime(now)
    return not success or not current or current - success > STALE_AFTER


def _sheet_state_id(source_id: str) -> str:
    return digest("packing_sheet_cache_v1", str(source_id))


def _default_store():
    from overseas_costing.services.logistics_settlement.store import Store

    return Store.frappe()


def _default_clients():
    from overseas_costing.integrations.dingtalk_packing_source import get_packing_runtime_clients

    return get_packing_runtime_clients()


def _put_state(store, state_id: str, payload: dict, now: str) -> None:
    store.put("state", {"id": state_id, "updated_at": now, "data": dumps(payload)})


def _commit(store) -> None:
    commit = getattr(store, "commit", None)
    if callable(commit):
        commit()


def _begin_run(store, now: str) -> tuple[str, dict] | None:
    token = uuid.uuid4().hex
    with store.atomic():
        store.get("state", "job_lock", lock=True)
        previous = store.get("state", CATALOG_STATE_ID, lock=True) or {}
        started_at = _datetime(previous.get("started_at"))
        current = _datetime(now)
        if (
            previous.get("run_status") == "running"
            and started_at
            and current
            and current - started_at <= RUN_LEASE
        ):
            return None
        generation = int(previous.get("generation") or 0) + 1
        running = {
            **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
            "schema_version": 1,
            "generation": generation,
            "run_token": token,
            "run_status": "running",
            "started_at": now,
            "last_checked_at": now,
        }
        _put_state(store, CATALOG_STATE_ID, running, now)
    _commit(store)
    return token, previous


def _assert_run(store, token: str) -> dict:
    current = store.get("state", CATALOG_STATE_ID, lock=True) or {}
    if current.get("run_token") != token or current.get("run_status") != "running":
        raise PackingSheetCacheError(_i18n("装箱缓存同步已被更新的任务取代。"))
    return current


def _active(row: dict) -> bool:
    return str(row.get("visibility") or "").strip().lower() not in {
        "deleted",
        "disabled",
        "inactive",
    }


def _source_id(row: dict) -> str:
    workbook_id = str(row.get("workbook_id") or "").strip()
    sheet_id = str(row.get("sheet_id") or "").strip()
    if not workbook_id or not sheet_id:
        raise ValueError(_i18n("装箱计划表目录缺少工作簿或 Sheet 标识。"))
    return f"{workbook_id}:{sheet_id}"


def _metadata(row: dict, source_id: str) -> dict:
    return {
        "source_kind": "wiki_sheet",
        "source_id": source_id,
        "source_label": str(row.get("sheet_name") or row.get("sheet_id") or ""),
        "workbook_id": str(row.get("workbook_id") or ""),
        "sheet_id": str(row.get("sheet_id") or ""),
        "workbook_year": row.get("year"),
        "workbook_label": str(row.get("workbook_label") or row.get("workbook_id") or ""),
        "workbook_updated_at": row.get("workbook_updated_at"),
        "source_updated_at": row.get("source_updated_at"),
        "indexed_at": row.get("indexed_at"),
        "snapshot_id": row.get("snapshot_id") or row.get("id"),
        "snapshot_updated_at": row.get("snapshot_created_at") or row.get("created_at"),
        "active": _active(row),
    }


def _build_trusted_sheet(row: dict, payload: dict) -> tuple[dict, dict]:
    from overseas_costing.services.packing_grid import build_grid_from_dingtalk_snapshot
    from overseas_costing.services.packing_parse_service import parse_packing_grid
    from overseas_costing.services.packing_sheet_recommendation import summarize_packing_preview

    source_id = _source_id(row)
    workbook_id, _, sheet_id = source_id.partition(":")
    if str(payload.get("workbookId") or "") != workbook_id or str(payload.get("sheetId") or "") != sheet_id:
        raise PackingSheetCacheError(_i18n("归档内容不属于所选装箱计划 Sheet。"))
    source_hash = str(row.get("content_sha256") or "").strip().lower()
    if len(source_hash) != 64 or any(character not in "0123456789abcdef" for character in source_hash):
        raise PackingSheetCacheError(_i18n("装箱计划 Sheet 缺少可验证的 SHA-256。"))
    grid = build_grid_from_dingtalk_snapshot(payload)
    preview = parse_packing_grid(grid)
    trusted = {
        "source_hash": source_hash,
        "source": {
            "source_kind": "wiki_sheet",
            "source_id": source_id,
            "source_label": str(payload.get("sheetName") or row.get("sheet_name") or sheet_id),
            "workbook_id": workbook_id,
            "sheet_id": sheet_id,
            "sheet_name": str(payload.get("sheetName") or row.get("sheet_name") or ""),
            "source_updated_at": payload.get("captureFinishedAt") or row.get("capture_finished_at"),
        },
        "grid": grid,
        "preview": preview,
    }
    return trusted, summarize_packing_preview(preview)


def _download_snapshot(clients, row: dict) -> dict:
    """目录将快照状态命名为 snapshot_status；归档完整性校验器接收标准 status。"""

    return clients.archive.download(
        {**row, "status": str(row.get("snapshot_status") or row.get("status") or "pending")}
    )


def _sheet_entry(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "source_kind",
            "source_id",
            "source_label",
            "workbook_id",
            "sheet_id",
            "workbook_year",
            "workbook_label",
            "source_updated_at",
            "indexed_at",
            "snapshot_updated_at",
            "cache_status",
            "cache_updated_at",
            "last_checked_at",
            "last_success_at",
            "sync_error",
            "content_hash",
            "summary",
            "active",
        )
    }


def _save_sheet_state(store, token: str, source_id: str, state: dict, now: str) -> None:
    with store.atomic():
        _assert_run(store, token)
        _put_state(store, _sheet_state_id(source_id), state, now)
    _commit(store)


def refresh_catalog_cache(*, store=None, clients=None, now: str | None = None) -> dict:
    """增量更新全局目录。网络与解析在事务外完成，存储时使用代次校验。"""

    store = store or _default_store()
    clients = clients or _default_clients()
    now = now or _now()
    started = _begin_run(store, now)
    if started is None:
        return {"checked": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": True}
    token, previous_catalog = started
    result = {"checked": 0, "updated": 0, "unchanged": 0, "failed": 0}
    try:
        rows = clients.catalog.list_catalog_snapshot()
    except Exception as error:
        with store.atomic():
            current = _assert_run(store, token)
            failed = {
                **{key: value for key, value in current.items() if key not in {"id", "updated_at"}},
                "run_status": "failed",
                "catalog_status": "error",
                "finished_at": now,
                "sync_error": str(error)[:1000],
                "last_result": result,
            }
            _put_state(store, CATALOG_STATE_ID, failed, now)
        _commit(store)
        return {**result, "error": str(error)[:1000]}

    active_source_ids: set[str] = set()
    entries: list[dict] = []
    for row in rows:
        if not row.get("sheet_id"):
            continue
        source_id = _source_id(row)
        if not _active(row):
            continue
        active_source_ids.add(source_id)
        result["checked"] += 1
        previous = store.get("state", _sheet_state_id(source_id)) or {}
        metadata = _metadata(row, source_id)
        observed_hash = str(row.get("content_sha256") or "").strip().lower()
        try:
            if str(row.get("snapshot_status") or "") != "ready":
                raise PackingSheetCacheError(_i18n("装箱计划 Sheet 的最新归档尚未就绪。"))
            if previous.get("trusted") and previous.get("content_hash") == observed_hash:
                state = {
                    **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
                    **metadata,
                    "cache_status": "ready",
                    "last_checked_at": now,
                    "last_success_at": now,
                    "sync_error": "",
                    "observed_content_hash": observed_hash,
                }
                result["unchanged"] += 1
            else:
                payload = _download_snapshot(clients, row)
                trusted, summary = _build_trusted_sheet(row, payload)
                state = {
                    **metadata,
                    "schema_version": 1,
                    "cache_status": "ready",
                    "cache_updated_at": now,
                    "last_checked_at": now,
                    "last_success_at": now,
                    "sync_error": "",
                    "content_hash": trusted["source_hash"],
                    "observed_content_hash": trusted["source_hash"],
                    "trusted": trusted,
                    "summary": summary,
                }
                result["updated"] += 1
        except Exception as error:
            state = {
                **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
                **metadata,
                "cache_status": "stale" if previous.get("trusted") else "error",
                "last_checked_at": now,
                "sync_error": str(error)[:1000],
                "observed_content_hash": observed_hash,
            }
            result["failed"] += 1
        _save_sheet_state(store, token, source_id, state, now)
        entries.append(_sheet_entry(state))

    for workbook in previous_catalog.get("wiki_workbooks") or []:
        for sheet in workbook.get("sheets") or []:
            source_id = str(sheet.get("source_id") or "")
            if not source_id or source_id in active_source_ids:
                continue
            previous = store.get("state", _sheet_state_id(source_id)) or {}
            if previous:
                previous = {
                    **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
                    "active": False,
                    "cache_status": "unavailable",
                    "last_checked_at": now,
                }
                _save_sheet_state(store, token, source_id, previous, now)
                entries.append(_sheet_entry(previous))

    workbooks_by_id: dict[str, dict] = {}
    for entry in entries:
        workbook_id = str(entry.get("workbook_id") or "")
        workbook = workbooks_by_id.setdefault(
            workbook_id,
            {
                "workbook_id": workbook_id,
                "year": entry.get("workbook_year"),
                "label": entry.get("workbook_label") or workbook_id,
                "sheets": [],
            },
        )
        workbook["sheets"].append(entry)
    workbooks = sorted(
        workbooks_by_id.values(),
        key=lambda item: (-(int(item.get("year") or 0)), str(item.get("label") or "")),
    )
    for workbook in workbooks:
        workbook["sheets"].sort(
            key=lambda item: (
                str(item.get("source_updated_at") or ""),
                str(item.get("source_label") or ""),
            ),
            reverse=True,
        )

    with store.atomic():
        current = _assert_run(store, token)
        complete = result["failed"] == 0
        catalog = {
            **{key: value for key, value in current.items() if key not in {"id", "updated_at"}},
            "run_status": "idle",
            "catalog_status": "ready" if complete else "partial",
            "finished_at": now,
            "last_checked_at": now,
            "last_success_at": now if complete else previous_catalog.get("last_success_at"),
            "sync_error": "" if complete else f"{result['failed']} 个 Sheet 同步失败。",
            "last_result": result,
            "wiki_workbooks": workbooks,
        }
        _put_state(store, CATALOG_STATE_ID, catalog, now)
    _commit(store)
    return result


def get_cached_catalog(*, store=None, now: str | None = None) -> dict:
    store = store or _default_store()
    now = now or _now()
    saved = store.get("state", CATALOG_STATE_ID) or {}
    workbooks = deepcopy(saved.get("wiki_workbooks") or [])
    catalog_stale = _is_stale(saved.get("last_success_at"), now)
    status = str(saved.get("catalog_status") or "missing")
    if catalog_stale and status != "missing":
        status = "stale"
    for workbook in workbooks:
        for sheet in workbook.get("sheets") or []:
            if sheet.get("active") is False or sheet.get("cache_status") == "unavailable":
                sheet["cache_status"] = "unavailable"
                continue
            sheet_stale = _is_stale(sheet.get("last_success_at"), now)
            if (sheet_stale or sheet.get("sync_error")) and sheet.get("content_hash"):
                sheet["cache_status"] = "stale"
            elif not sheet.get("content_hash"):
                sheet["cache_status"] = "error" if sheet.get("sync_error") else "missing"
    return {
        "catalog_status": status,
        "last_success_at": saved.get("last_success_at"),
        "last_checked_at": saved.get("last_checked_at"),
        "sync_error": saved.get("sync_error") or "",
        "wiki_workbooks": workbooks,
    }


def get_cached_sheet(source_id: str, *, store=None) -> dict:
    store = store or _default_store()
    saved = store.get("state", _sheet_state_id(str(source_id))) or {}
    if saved.get("active") is False:
        raise PackingSheetCacheError(_i18n("该装箱计划 Sheet 已停用或移除。"))
    trusted = saved.get("trusted")
    if not isinstance(trusted, dict):
        raise PackingSheetCacheError(_i18n("该装箱计划 Sheet 尚无可用的本地缓存。"))
    if str(trusted.get("source", {}).get("source_id") or "") != str(source_id):
        raise PackingSheetCacheError(_i18n("装箱计划 Sheet 缓存标识不一致。"))
    return deepcopy(trusted)


def _merge_sheet_entry(workbooks: list[dict], state: dict) -> list[dict]:
    result = deepcopy(workbooks)
    workbook_id = str(state.get("workbook_id") or "")
    source_id = str(state.get("source_id") or "")
    workbook = next(
        (row for row in result if str(row.get("workbook_id") or "") == workbook_id),
        None,
    )
    if workbook is None:
        workbook = {
            "workbook_id": workbook_id,
            "year": state.get("workbook_year"),
            "label": state.get("workbook_label") or workbook_id,
            "sheets": [],
        }
        result.append(workbook)
    sheets = list(workbook.get("sheets") or [])
    entry = _sheet_entry(state)
    index = next(
        (
            position
            for position, sheet in enumerate(sheets)
            if str(sheet.get("source_id") or "") == source_id
        ),
        None,
    )
    if index is None:
        sheets.append(entry)
    else:
        sheets[index] = entry
    workbook["sheets"] = sheets
    result.sort(key=lambda item: (-(int(item.get("year") or 0)), str(item.get("label") or "")))
    return result


def refresh_sheet_cache(
    workbook_id: str,
    sheet_id: str,
    *,
    store=None,
    clients=None,
    now: str | None = None,
) -> dict:
    """手动刷新完成后只物化用户选中的 Sheet。"""

    store = store or _default_store()
    clients = clients or _default_clients()
    now = now or _now()
    source_id = f"{str(workbook_id).strip()}:{str(sheet_id).strip()}"
    started = _begin_run(store, now)
    if started is None:
        return {"checked": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": True}
    token, previous_catalog = started
    result = {"checked": 1, "updated": 0, "unchanged": 0, "failed": 0}
    try:
        row = next(
            (
                item
                for item in clients.catalog.list_catalog_snapshot()
                if str(item.get("workbook_id") or "") == str(workbook_id)
                and str(item.get("sheet_id") or "") == str(sheet_id)
                and _active(item)
            ),
            None,
        )
        if row is None:
            raise PackingSheetCacheError(_i18n("刷新后未找到所选装箱计划 Sheet。"))
        previous = store.get("state", _sheet_state_id(source_id)) or {}
        metadata = _metadata(row, source_id)
        observed_hash = str(row.get("content_sha256") or "").strip().lower()
        if str(row.get("snapshot_status") or "") != "ready":
            raise PackingSheetCacheError(_i18n("所选装箱计划 Sheet 的最新归档尚未就绪。"))
        if previous.get("trusted") and previous.get("content_hash") == observed_hash:
            state = {
                **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
                **metadata,
                "cache_status": "ready",
                "last_checked_at": now,
                "last_success_at": now,
                "sync_error": "",
                "observed_content_hash": observed_hash,
            }
            result["unchanged"] = 1
        else:
            payload = _download_snapshot(clients, row)
            trusted, summary = _build_trusted_sheet(row, payload)
            state = {
                **metadata,
                "schema_version": 1,
                "cache_status": "ready",
                "cache_updated_at": now,
                "last_checked_at": now,
                "last_success_at": now,
                "sync_error": "",
                "content_hash": trusted["source_hash"],
                "observed_content_hash": trusted["source_hash"],
                "trusted": trusted,
                "summary": summary,
            }
            result["updated"] = 1
    except Exception as error:
        previous = store.get("state", _sheet_state_id(source_id)) or {}
        state = {
            **{key: value for key, value in previous.items() if key not in {"id", "updated_at"}},
            "source_kind": "wiki_sheet",
            "source_id": source_id,
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "cache_status": "stale" if previous.get("trusted") else "error",
            "last_checked_at": now,
            "sync_error": str(error)[:1000],
            "active": True,
        }
        result["failed"] = 1
    _save_sheet_state(store, token, source_id, state, now)
    with store.atomic():
        current = _assert_run(store, token)
        workbooks = _merge_sheet_entry(previous_catalog.get("wiki_workbooks") or [], state)
        catalog = {
            **{key: value for key, value in current.items() if key not in {"id", "updated_at"}},
            "run_status": "idle",
            "catalog_status": (
                previous_catalog.get("catalog_status") or ("partial" if result["failed"] else "ready")
            ),
            "finished_at": now,
            "last_checked_at": now,
            "last_success_at": previous_catalog.get("last_success_at") or (
                now if not result["failed"] else None
            ),
            "sync_error": str(state.get("sync_error") or ""),
            "last_result": result,
            "wiki_workbooks": workbooks,
        }
        _put_state(store, CATALOG_STATE_ID, catalog, now)
    _commit(store)
    return result


def _manual_refresh_state_id(request_key: str) -> str:
    return digest("packing_sheet_manual_refresh_v1", str(request_key))


def register_manual_refresh(
    *,
    batch_name: str,
    workbook_id: str,
    sheet_id: str,
    request_key: str,
    store=None,
) -> None:
    store = store or _default_store()
    now = _now()
    state_id = _manual_refresh_state_id(request_key)
    payload = {
        "batch_name": str(batch_name),
        "workbook_id": str(workbook_id),
        "sheet_id": str(sheet_id),
        "request_key": str(request_key),
        "status": "pending",
        "created_at": now,
    }
    with store.atomic():
        store.get("state", "job_lock", lock=True)
        previous = store.get("state", state_id, lock=True) or {}
        if previous:
            previous_target = tuple(
                str(previous.get(key) or "")
                for key in ("batch_name", "workbook_id", "sheet_id")
            )
            requested_target = tuple(
                str(payload.get(key) or "")
                for key in ("batch_name", "workbook_id", "sheet_id")
            )
            if previous_target != requested_target:
                raise PackingSheetCacheError(_i18n("该刷新请求 ID 已绑定其他批次或 Sheet。"))
        else:
            _put_state(store, state_id, payload, now)
    _commit(store)


def complete_manual_refresh(request_key: str, *, store=None, clients=None) -> dict | None:
    store = store or _default_store()
    state_id = _manual_refresh_state_id(request_key)
    request = store.get("state", state_id) or {}
    if not request:
        return None
    if request.get("status") == "success":
        return {"ok": True, "idempotent": True}
    result = refresh_sheet_cache(
        str(request.get("workbook_id") or ""),
        str(request.get("sheet_id") or ""),
        store=store,
        clients=clients,
    )
    if result.get("skipped"):
        return {"ok": False, "pending": True}
    if result.get("failed"):
        raise PackingSheetCacheError(_i18n("所选 Sheet 的本地缓存更新失败。"))
    now = _now()
    request.update(status="success", completed_at=now, result=result)
    _put_state(store, state_id, request, now)
    _commit(store)
    return {"ok": True, "idempotent": False, "result": result}


def scheduled_refresh_catalog_cache() -> dict:
    return refresh_catalog_cache()


def prewarm_catalog_cache(
    *,
    store=None,
    clients=None,
    sleep=None,
    poll_seconds: float = 5.0,
    max_polls: int = 120,
) -> dict:
    """发布门禁：首次为无快照 Sheet 排队，随后要求所有启用 Sheet 完成物化。"""

    from overseas_costing.integrations.dingtalk_packing_source import build_refresh_request_key

    store = store or _default_store()
    clients = clients or _default_clients()
    sleeper = sleep or _sleep
    rows = clients.catalog.list_catalog_snapshot()
    missing = [
        row
        for row in rows
        if row.get("sheet_id")
        and _active(row)
        and (
            str(row.get("snapshot_status") or "") != "ready"
            or len(str(row.get("content_sha256") or "").strip()) != 64
        )
    ]
    pending: dict[str, str] = {}
    for row in missing:
        workbook_id = str(row.get("workbook_id") or "")
        sheet_id = str(row.get("sheet_id") or "")
        request_key = build_refresh_request_key(
            "sheet",
            workbook_id,
            sheet_id,
            uuid.uuid4().hex,
        )
        clients.submitter.request_sheet_refresh(
            workbook_id,
            sheet_id,
            request_key,
            "deployment-prewarm",
        )
        pending[request_key] = f"{workbook_id}:{sheet_id}"

    for poll_index in range(max(1, int(max_polls))):
        failures: list[str] = []
        for request_key, source_id in list(pending.items()):
            status = clients.catalog.get_refresh_status(request_key) or {}
            state = str(status.get("status") or "not_found")
            if state == "success":
                pending.pop(request_key, None)
            elif state == "failed":
                message = str(status.get("error_message") or "远端刷新失败。")
                failures.append(f"{source_id}: {message}")
        if failures:
            raise PackingSheetCacheError("装箱 Sheet 首次刷新失败：" + "; ".join(failures)[:1000])
        if not pending:
            break
        if poll_index + 1 < max(1, int(max_polls)):
            sleeper(max(0.0, float(poll_seconds)))
    if pending:
        raise PackingSheetCacheError(
            _i18n("仍有 {0} 个装箱 Sheet 首次刷新未完成，请稍后重试部署。").format(len(pending))
        )

    result = refresh_catalog_cache(store=store, clients=clients)
    if result.get("skipped"):
        raise PackingSheetCacheError(_i18n("装箱 Sheet 缓存正在由另一个任务同步，本次预热未完成。"))
    if result.get("error") or result.get("failed"):
        raise PackingSheetCacheError(
            _i18n("装箱 Sheet 缓存预热失败：{0}").format(result.get('error') or str(result.get('failed')) + ' 个 Sheet 未就绪。')
        )
    catalog = get_cached_catalog(store=store)
    sheets = [
        sheet
        for workbook in catalog.get("wiki_workbooks") or []
        for sheet in workbook.get("sheets") or []
        if sheet.get("active") is not False
    ]
    pending = [
        str(sheet.get("source_id") or "")
        for sheet in sheets
        if sheet.get("cache_status") != "ready" or not sheet.get("content_hash")
    ]
    if not sheets:
        raise PackingSheetCacheError(_i18n("装箱 Sheet 目录为空，不允许切换新前端。"))
    if pending:
        raise PackingSheetCacheError(
            _i18n("仍有 {0} 个启用 Sheet 未完成本地物化。").format(len(pending))
        )
    return {
        **result,
        "requested": len(missing),
        "ready": len(sheets),
        "catalog_status": catalog.get("catalog_status"),
    }
