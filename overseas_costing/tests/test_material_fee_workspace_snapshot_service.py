from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from overseas_costing.services.logistics_settlement.store import Store


def _store() -> Store:
    store = Store.sqlite(sqlite3.connect(":memory:"))
    store.install()
    return store


def _builder(calls: list[str], *, total: str = "100.00"):
    def build(batch_name: str, version_name: str, page: int, page_length: int) -> dict:
        calls.append(f"{batch_name}:{version_name}:{page}:{page_length}")
        return {
            "detail": {"header": {"name": batch_name}, "edit_token": "must-not-cache"},
            "materials": {
                "items": [{"name": "ITEM-1", "content": "已落库的 OA 正文"}],
                "missing_cell_count": 0,
            },
            "fees": {"fees": [], "summary": {}},
            "preview": {"summary": {"total_cost_rmb": total}, "preview_token": "must-not-cache"},
            "settlement": {
                "logistics": {"approval_no": "OA-1", "image_url": "data:image/png;base64,AAAA"},
                "raw_attachment": b"not-cacheable",
            },
        }

    return build


def test_first_read_builds_persistent_snapshot_and_second_read_hits_cache() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    calls: list[str] = []
    fingerprint_calls: list[str] = []

    def fingerprint(batch_name: str, version_name: str) -> str:
        fingerprint_calls.append(f"{batch_name}:{version_name}")
        return "fingerprint-1"

    first = service.get_snapshot(
        "B1",
        "V1",
        page=1,
        page_length=200,
        store=store,
        fingerprint_loader=fingerprint,
        snapshot_builder=_builder(calls),
        now="2026-09-17T01:00:00+00:00",
    )
    second = service.get_snapshot(
        "B1",
        "V1",
        page=1,
        page_length=200,
        store=store,
        fingerprint_loader=fingerprint,
        snapshot_builder=_builder(calls, total="999.00"),
        now="2026-09-17T01:01:00+00:00",
    )

    assert calls == ["B1:V1:1:200"]
    assert fingerprint_calls == ["B1:V1"]
    assert first["data"] == second["data"]
    assert first["cache"]["served_from_cache"] is False
    assert second["cache"]["served_from_cache"] is True
    assert second["cache"]["status"] == "ready"
    assert second["cache"]["input_fingerprint"] == "fingerprint-1"
    assert second["cache"]["generated_at"] == "2026-09-17T01:00:00+00:00"
    assert "raw_attachment" not in second["data"]["settlement"]
    assert "image_url" not in second["data"]["settlement"]["logistics"]
    assert second["data"]["materials"]["items"][0]["content"] == "已落库的 OA 正文"
    assert "edit_token" not in second["data"]["detail"]
    assert "preview_token" not in second["data"]["preview"]


def test_freshness_check_marks_changed_snapshot_stale_without_rebuilding() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    calls: list[str] = []
    service.get_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder(calls),
        now="2026-09-17T01:00:00+00:00",
    )

    unchanged = service.check_freshness(
        "B1",
        "V1",
        snapshot_fingerprint="fingerprint-1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-1",
        now="2026-09-17T01:02:00+00:00",
    )
    changed = service.check_freshness(
        "B1",
        "V1",
        snapshot_fingerprint="fingerprint-1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-2",
        now="2026-09-17T01:03:00+00:00",
    )
    cached = service.get_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-2",
        snapshot_builder=_builder(calls, total="999.00"),
        now="2026-09-17T01:04:00+00:00",
    )

    assert unchanged == {
        "ok": True,
        "batch_name": "B1",
        "version_name": "V1",
        "unchanged": True,
        "snapshot_fingerprint": "fingerprint-1",
        "current_fingerprint": "fingerprint-1",
        "checked_at": "2026-09-17T01:02:00+00:00",
    }
    assert changed["unchanged"] is False
    assert changed["current_fingerprint"] == "fingerprint-2"
    assert cached["cache"]["status"] == "stale"
    assert cached["cache"]["served_from_cache"] is True
    assert calls == ["B1:V1:1:200"]


def test_refresh_failure_keeps_last_known_good_snapshot() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    service.get_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder([]),
        now="2026-09-17T01:00:00+00:00",
    )

    def fail(*_args):
        raise RuntimeError("database busy")

    stale = service.refresh_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-2",
        snapshot_builder=fail,
        now="2026-09-17T01:05:00+00:00",
    )

    assert stale["ok"] is True
    assert stale["data"]["preview"]["summary"]["total_cost_rmb"] == "100.00"
    assert stale["cache"]["status"] == "stale"
    assert stale["cache"]["served_from_cache"] is True
    assert stale["cache"]["refresh_error"] == "database busy"
    assert stale["cache"]["last_success_at"] == "2026-09-17T01:00:00+00:00"


def test_first_build_failure_has_no_stale_payload_to_serve() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    with pytest.raises(RuntimeError, match="database busy"):
        service.get_snapshot(
            "B1",
            "V1",
            store=_store(),
            fingerprint_loader=lambda *_: "fingerprint-1",
            snapshot_builder=lambda *_: (_ for _ in ()).throw(RuntimeError("database busy")),
            now="2026-09-17T01:00:00+00:00",
        )


def test_concurrent_refresh_reuses_last_good_snapshot_without_duplicate_build() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    calls: list[str] = []
    service.get_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder(calls),
        now="2026-09-17T01:00:00+00:00",
    )
    state_id = service._state_id("B1", "V1", 1, 200)
    started = service._begin_refresh(
        store,
        state_id,
        {"batch_name": "B1", "version_name": "V1", "page": 1, "page_length": 200},
        "2026-09-17T01:01:00+00:00",
    )
    assert started is not None

    concurrent = service.refresh_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-2",
        snapshot_builder=_builder(calls, total="999.00"),
        now="2026-09-17T01:02:00+00:00",
    )

    assert calls == ["B1:V1:1:200"]
    assert concurrent["data"]["preview"]["summary"]["total_cost_rmb"] == "100.00"
    assert concurrent["cache"]["served_from_cache"] is True
    assert concurrent["cache"]["status"] == "refreshing"


def test_begin_refresh_retries_record_changed_conflict_after_rollback() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    class QueryDeadlockError(Exception):
        pass

    class ConflictStore:
        def __init__(self) -> None:
            self.state = {}
            self.lock_attempts = 0
            self.rollbacks = 0
            self.commits = 0

        @contextmanager
        def atomic(self):
            yield

        def get(self, table, state_id, lock=False):
            assert table == "state"
            if state_id == "snapshot-1" and lock:
                self.lock_attempts += 1
                if self.lock_attempts == 1:
                    raise QueryDeadlockError(1020, "Record has changed since last read")
            return self.state.get(state_id)

        def put(self, table, value):
            assert table == "state"
            self.state[value["id"]] = dict(value)

        def rollback(self):
            self.rollbacks += 1

        def commit(self):
            self.commits += 1

    store = ConflictStore()
    started = service._begin_refresh(
        store,
        "snapshot-1",
        {"batch_name": "B1", "version_name": "V1", "page": 1, "page_length": 200},
        "2026-09-21T02:32:04+00:00",
    )

    assert started is not None
    assert store.lock_attempts == 2
    assert store.rollbacks == 1
    assert store.commits == 1
    assert json.loads(store.state["snapshot-1"]["data"])["run_status"] == "running"


def test_begin_refresh_rolls_back_final_lock_conflict_before_raising() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    class QueryDeadlockError(Exception):
        pass

    class AlwaysConflictStore:
        def __init__(self) -> None:
            self.lock_attempts = 0
            self.rollbacks = 0

        @contextmanager
        def atomic(self):
            yield

        def get(self, table, state_id, lock=False):
            assert table == "state"
            if state_id == "snapshot-1" and lock:
                self.lock_attempts += 1
                raise QueryDeadlockError(1020, "Record has changed since last read")
            return None

        def rollback(self):
            self.rollbacks += 1

    store = AlwaysConflictStore()
    with pytest.raises(QueryDeadlockError, match="Record has changed"):
        service._begin_refresh(
            store,
            "snapshot-1",
            {"batch_name": "B1", "version_name": "V1", "page": 1, "page_length": 200},
            "2026-09-21T02:32:04+00:00",
        )

    assert store.lock_attempts == service.REFRESH_LOCK_RETRIES + 1
    assert store.rollbacks == service.REFRESH_LOCK_RETRIES + 1


def test_context_rejects_a_version_owned_by_another_batch(monkeypatch) -> None:
    from overseas_costing.services import batch_service
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    fake_db = SimpleNamespace(
        get_value=lambda doctype, name, field: "OTHER-BATCH"
        if doctype == "Overseas Cost Version" and name == "V-SECRET" and field == "batch"
        else None
    )
    monkeypatch.setattr(batch_service, "frappe", SimpleNamespace(db=fake_db))
    monkeypatch.setattr(batch_service, "_resolve_batch_name", lambda value: "B-ALLOWED")

    with pytest.raises(ValueError, match="版本不属于当前批次"):
        service._context("B-ALLOWED", "V-SECRET")


def test_incomplete_rebuild_never_replaces_last_good_snapshot() -> None:
    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    service.get_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder([]),
        now="2026-09-17T01:00:00+00:00",
    )

    stale = service.refresh_snapshot(
        "B1",
        "V1",
        store=store,
        fingerprint_loader=lambda *_: "fingerprint-2",
        snapshot_builder=lambda *_: {"detail": {}, "materials": {}, "fees": {}},
        now="2026-09-17T01:05:00+00:00",
    )

    assert stale["cache"]["status"] == "stale"
    assert "preview" in stale["cache"]["refresh_error"]
    assert stale["data"]["preview"]["summary"]["total_cost_rmb"] == "100.00"


def test_workspace_snapshot_stays_available_when_packing_group_is_blocked(monkeypatch) -> None:
    from overseas_costing.services import (
        batch_service,
        cost_preview_service,
        fee_service,
        material_fee_workspace_snapshot_service as service,
        material_input_service,
    )

    materials = {
        "items": [{"name": "I-1", "stable_line_key": "LINE-1"}],
        "packing_groups": [{
            "group_id": "GROUP-OLD",
            "member_keys": ["LINE-OLD-1", "LINE-OLD-2"],
            "status": "confirmed",
            "blocking": True,
        }],
    }
    monkeypatch.setattr(batch_service, "get_batch_detail", lambda *_args: {"header": {"name": "B-1"}})
    monkeypatch.setattr(material_input_service, "get_material_grid", lambda *_args, **_kwargs: materials)
    monkeypatch.setattr(fee_service, "get_fee_worklist", lambda *_args: {"fees": [], "summary": {}})
    monkeypatch.setattr(
        cost_preview_service,
        "preview_comprehensive_cost",
        lambda *_args: (_ for _ in ()).throw(AssertionError("blocked packing must skip strict preview")),
    )

    snapshot = service.build_workspace_snapshot("B-1", "V-1", 1, 200)

    assert snapshot["materials"] == materials
    assert snapshot["preview"]["summary"]["is_complete"] is False
    assert snapshot["preview"]["items"] == []
    assert snapshot["preview"]["incomplete_reasons"] == [{
        "reason_code": "PACKING_GROUP_RECONFIRMATION_REQUIRED",
        "message": "装箱组成员已变化，请重新确认分组后再试算。",
    }]


def _rewrite_cached_schema(store, service, batch: str, version: str, schema_version: int) -> None:
    """把已缓存快照的项目口径版本改写成指定值，用来模拟旧代码写下的快照。"""

    from overseas_costing.services.logistics_settlement.model import dumps

    state_id = service._state_id(batch, version, 1, service.DEFAULT_PAGE_LENGTH)
    stored = store.get("state", state_id)
    store.put(
        "state",
        {
            "id": state_id,
            "updated_at": "2026-09-23T01:00:00+00:00",
            "data": dumps({**stored, "schema_version": schema_version}),
        },
    )
    store.commit()


def test_pre_other_fee_menu_cache_is_rebuilt_with_optional_fee_choices():
    from overseas_costing.services import fee_service, material_fee_workspace_snapshot_service as service

    store = _store()
    kwargs = dict(store=store, fingerprint_loader=lambda *_: "same-inputs", now="2026-10-23T02:00:00+00:00")
    service.get_snapshot("B1", "V1", snapshot_builder=_builder([]), **kwargs)
    _rewrite_cached_schema(store, service, "B1", "V1", 3)

    def current_builder(*args):
        data = _builder([])(*args)
        data["fees"]["other_fee_options"] = fee_service.build_other_fee_templates()
        return data

    current = service.get_snapshot("B1", "V1", snapshot_builder=current_builder, **kwargs)
    assert len(current["data"]["fees"]["other_fee_options"]) == 5


def test_snapshot_written_by_an_older_projection_is_rebuilt_not_served() -> None:
    """改过投影口径后旧快照必须重建。

    2026-09-23 线上就是吃到了这一口：候选列表的字段与分组早已换代，快照却还在按
    旧口径回放（没有 workflow_stage，于是全部落进“其他资料”），页面看起来像是新功能
    没生效。读侧只认当前口径版本的缓存。
    """

    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    calls: list[str] = []
    service.get_snapshot(
        "B1", "V1", store=store, fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder(calls), now="2026-09-23T01:00:00+00:00",
    )
    _rewrite_cached_schema(store, service, "B1", "V1", service.SCHEMA_VERSION - 1)

    rebuilt = service.get_snapshot(
        "B1", "V1", store=store, fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder(calls, total="999.00"), now="2026-09-23T01:01:00+00:00",
    )

    assert calls == ["B1:V1:1:200", "B1:V1:1:200"]
    assert rebuilt["cache"]["served_from_cache"] is False
    assert rebuilt["cache"]["schema_version"] == service.SCHEMA_VERSION
    assert rebuilt["data"]["preview"]["summary"]["total_cost_rmb"] == "999.00"


def test_failed_rebuild_keeps_the_surviving_payload_marked_as_old() -> None:
    """重建失败时不能把上一份旧口径数据改标成新口径。

    标成新口径，读侧的版本校验就会放行它，之后即便代码已经是对的，页面也永远
    拿到那份旧数据 —— 失效机制被绕过去了。
    """

    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    store = _store()
    service.get_snapshot(
        "B1", "V1", store=store, fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=_builder([]), now="2026-09-23T01:00:00+00:00",
    )
    previous_version = service.SCHEMA_VERSION - 1
    _rewrite_cached_schema(store, service, "B1", "V1", previous_version)

    def boom(*_args, **_kwargs):
        raise RuntimeError("builder down")

    stale = service.refresh_snapshot(
        "B1", "V1", store=store, fingerprint_loader=lambda *_: "fingerprint-1",
        snapshot_builder=boom, now="2026-09-23T01:02:00+00:00",
    )

    assert stale["cache"]["served_from_cache"] is True
    assert stale["cache"]["status"] == "stale"
    assert stale["cache"]["schema_version"] == previous_version
    assert stale["data"]["preview"]["summary"]["total_cost_rmb"] == "100.00"


def test_projection_revision_follows_the_deploy_marker(monkeypatch) -> None:
    """指纹必须带上发布号。

    指纹里原先只有数据修订，投影改了、数据没动时它不变，快照就会被一直沿用。
    发布号随部署变化，正好补上这一维；站点上没有它时退回空串，不误判。
    """

    from overseas_costing.services import material_fee_workspace_snapshot_service as service

    fake = SimpleNamespace(conf={"overseas_costing_release_id": "rev-a"})
    monkeypatch.setitem(sys.modules, "frappe", fake)
    assert service.projection_revision() == "rev-a"

    fake.conf["overseas_costing_release_id"] = "rev-b"
    assert service.projection_revision() == "rev-b"

    fake.conf.pop("overseas_costing_release_id")
    assert service.projection_revision() == ""
