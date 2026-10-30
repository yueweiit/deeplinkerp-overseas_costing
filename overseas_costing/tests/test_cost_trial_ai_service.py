"""AI 试算口径审核服务。"""

from decimal import Decimal
import hashlib
import json

import pytest

from overseas_costing.services import cost_trial_ai_service


ITEMS = [
    {
        "name": "ITEM-1",
        "stable_line_key": "line-1",
        "goods_value": 100,
        "shipment_value_rmb": 100,
        "gross_weight_kg": 10,
        "volume_m3": 0,
        "chargeable_weight_kg": 12,
    },
    {
        "name": "ITEM-2",
        "stable_line_key": "line-2",
        "goods_value": 300,
        "shipment_value_rmb": 300,
        "gross_weight_kg": 30,
        "volume_m3": 0,
        "chargeable_weight_kg": 36,
    },
]


def _fee(key="international_air_freight", amount=100):
    return {
        "name": f"RULE-{key}",
        "logical_fee_key": key,
        "rule_code": key,
        "expense_category": key,
        "amount": amount,
        "currency": "RMB",
        "amount_status": "ACTUAL",
        "scope_type": "ALL_ITEMS",
        "allocation_basis": "goods_value",
    }


@pytest.mark.parametrize("amount", [0, "0", "0.00", "-0"])
def test_zero_amount_fee_does_not_require_ai_allocation(amount):
    assert cost_trial_ai_service.requires_ai_allocation(_fee("destination_delivery", amount)) is False


@pytest.mark.parametrize("amount", [1, "0.01", "-1"])
def test_nonzero_counted_fee_requires_ai_allocation(amount):
    assert cost_trial_ai_service.requires_ai_allocation(_fee(amount=amount)) is True


@pytest.mark.parametrize(
    "fee",
    [
        {**_fee(amount=""), "amount_status": "ACTUAL"},
        {**_fee(amount=None), "amount_status": "ACTUAL"},
        {**_fee(amount="100"), "amount_status": "MISSING"},
    ],
)
def test_missing_or_non_counted_fee_does_not_require_ai_allocation(fee):
    assert cost_trial_ai_service.requires_ai_allocation(fee) is False


def test_review_omits_zero_fee_but_preserves_nonzero_fee_suggestion():
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee("destination_delivery", "0.00"), _fee("international_air_freight", "100")],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={
            "ok": True,
            "model": "deepseek-test",
            "rules": [
                {**_fee("destination_delivery", "0.00"), "allocation_basis": "gross_weight"},
                {**_fee("international_air_freight", "100"), "allocation_basis": "goods_value"},
            ],
        },
    )

    assert [row["fee_key"] for row in result["fee_suggestions"]] == ["international_air_freight"]


def test_zero_fee_needs_no_choice_and_remains_in_cost_snapshot():
    fee = _fee("destination_delivery", "0")
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[fee],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={"ok": True, "model": "deepseek-test", "rules": []},
    )

    result = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS,
        fees=[fee],
        fx_context={},
        fee_components=[],
        draft=draft,
        selections=[],
    )

    assert draft["fee_suggestions"] == []
    assert result["trial_review"]["fee_choices"] == []
    assert result["included_fees"][0]["fee_key"] == "destination_delivery"
    assert result["included_fees"][0]["amount_rmb"] == "0.00"


def test_build_review_falls_back_to_a_complete_basis_when_ai_basis_is_missing():
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={
            "ok": True,
            "model": "deepseek-test",
            "rules": [{
                **_fee(),
                "allocation_basis": "volume",
                "ai_confidence": 0.88,
                "remark": "AI 认为应按体积分摊",
            }],
        },
    )

    proposal = result["fee_suggestions"][0]
    assert proposal["ai_recommended_basis"] == "volume"
    assert proposal["default_basis"] == "chargeable_weight"
    assert proposal["recommended_basis"] == "chargeable_weight"
    assert proposal["recommended_basis_available"] is True
    assert proposal["requires_temporary_basis"] is False
    assert proposal["decision_source"] == "SYSTEM_FALLBACK"
    assert [row["basis"] for row in proposal["available_alternatives"]] == [
        "goods_value",
        "gross_weight",
        "chargeable_weight",
    ]
    assert proposal["missing_fields"] == []
    assert result["trial_context"] == {
        "transport_mode": "AIR",
        "project": "",
        "supplier": "",
    }
    assert result["retrieval_context"] == []
    assert result["retrieval_version"] == ""


def test_ai_normalizer_system_placeholder_is_recorded_as_system_fallback_not_ai():
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={
            "ok": True,
            "model": "deepseek-test",
            "rules": [{
                **_fee(),
                "allocation_basis": "goods_value",
                "is_ai_suggestion": 0,
                "is_system_suggestion": 1,
                "remark": "AI未返回该费用池，沿用系统基础分摊",
            }],
        },
    )

    proposal = result["fee_suggestions"][0]
    assert proposal["ai_recommended_basis"] == ""
    assert proposal["default_basis"] == "chargeable_weight"
    assert proposal["decision_source"] == "SYSTEM_FALLBACK"
    assert result["decision_summary"] == {
        "reused": 0,
        "ai": 0,
        "system_fallback": 1,
        "evidence": 0,
    }


def test_review_exposes_scope_and_deterministic_alternative_differences():
    items = [
        {**ITEMS[0], "gross_weight_kg": 30},
        {**ITEMS[1], "gross_weight_kg": 10},
    ]
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=items,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={"ok": True, "rules": [{**_fee(), "allocation_basis": "volume"}]},
    )

    proposal = result["fee_suggestions"][0]
    alternatives = {row["basis"]: row for row in proposal["available_alternatives"]}
    assert proposal["scope_type"] == "ALL_ITEMS"
    assert alternatives["goods_value"]["allocation_preview"] == [
        {"item_key": "line-1", "amount_rmb": "25.00"},
        {"item_key": "line-2", "amount_rmb": "75.00"},
    ]
    assert alternatives["gross_weight"]["allocation_preview"] == [
        {"item_key": "line-1", "amount_rmb": "75.00"},
        {"item_key": "line-2", "amount_rmb": "25.00"},
    ]


def test_preview_uses_selected_complete_basis_without_marking_result_temporary():
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
        ai_result={
            "ok": True,
            "model": "deepseek-test",
            "rules": [{**_fee(), "allocation_basis": "volume", "ai_confidence": 0.8}],
        },
    )
    proposal = draft["fee_suggestions"][0]

    result = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        fee_components=[],
        draft=draft,
        selections=[{
            "suggestion_id": proposal["suggestion_id"],
            "basis": "gross_weight",
            "reason": "体积尚未补齐，本次暂按重量",
        }],
    )

    fee = result["included_fees"][0]
    assert fee["allocation_basis"] == "gross_weight"
    assert fee["allocations"] == {"line-1": "25.00", "line-2": "75.00"}
    assert result["trial_review"]["is_temporary"] is False
    assert not any(row["reason_code"] == "TEMPORARY_ALLOCATION_BASIS" for row in result["incomplete_reasons"])


def test_preview_rejects_client_basis_that_server_did_not_offer():
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1"},
        ai_result={"ok": True, "rules": [{**_fee(), "allocation_basis": "volume"}]},
    )
    proposal = draft["fee_suggestions"][0]

    with pytest.raises(ValueError, match="不可用"):
        cost_trial_ai_service.preview_selected_cost_trial(
            items=ITEMS,
            fees=[_fee()],
            fx_context={},
            fee_components=[],
            draft=draft,
            selections=[{"suggestion_id": proposal["suggestion_id"], "basis": "not-a-basis"}],
        )


def test_complete_manual_override_is_formal_without_requiring_a_reason():
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1"},
        ai_result={"ok": True, "rules": [{**_fee(), "allocation_basis": "goods_value"}]},
    )
    proposal = draft["fee_suggestions"][0]

    result = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        fee_components=[],
        draft=draft,
        selections=[{
            "suggestion_id": proposal["suggestion_id"],
            "basis": "gross_weight",
            "reason": "",
        }],
    )

    assert result["trial_review"]["is_temporary"] is False
    choice = result["trial_review"]["fee_choices"][0]
    assert choice["modified_ai_suggestion"] is True
    assert choice["decision_source"] == "USER_OVERRIDE"
    assert choice["reason"] == ""


def test_saved_projection_hashes_formal_basis_but_keeps_temporary_basis_private():
    fee = _fee()
    formal = cost_trial_ai_service.project_fees_for_trial(
        [fee],
        {"international_air_freight": {"basis": "gross_weight", "temporary": False}},
        for_save=True,
    )[0]
    temporary = cost_trial_ai_service.project_fees_for_trial(
        [fee],
        {"international_air_freight": {"basis": "volume", "temporary": True}},
        for_save=True,
    )[0]

    assert formal["allocation_basis"] == "gross_weight"
    assert "trial_allocation_basis" not in formal
    assert temporary["allocation_basis"] == fee["allocation_basis"]
    assert temporary["trial_allocation_basis"] == "volume"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ({"basis": "gross_weight", "decision_source": "REUSED"}, "试算沿用上次：gross_weight"),
        ({"basis": "goods_value", "decision_source": "AI"}, "AI试算自动：goods_value"),
        ({"basis": "goods_value", "decision_source": "SYSTEM_FALLBACK"}, "试算系统兜底：goods_value"),
        ({"basis": "volume", "decision_source": "USER_OVERRIDE", "previous_basis": "goods_value"},
         "试算人工调整：goods_value→volume"),
    ],
)
def test_choice_audit_remark_records_decision_source(choice, expected):
    assert cost_trial_ai_service._choice_audit_remark(choice) == expected


def test_temporary_saved_trial_is_marked_incomplete_and_blocks_formal_use():
    saved = cost_trial_ai_service.cost_preview_service.build_saved_cost_data(
        ITEMS,
        [{**_fee(), "trial_allocation_basis": "gross_weight"}],
        {},
        "AIR",
    )

    result = cost_trial_ai_service.annotate_saved_trial_result(
        saved,
        {"run_id": "RUN-1", "is_temporary": True, "fee_choices": []},
    )

    assert result["summary"]["is_complete"] is False
    assert result["summary"]["is_temporary"] is True
    assert result["summary_snapshot"]["formal_confirmation_blocked"] is True
    assert result["summary_snapshot"]["comprehensive_cost"]["summary"]["is_complete"] is False
    assert any(row["reason_code"] == "TEMPORARY_ALLOCATION_BASIS" for row in result["incomplete_reasons"])


def test_confirmed_customs_components_are_evidence_locked_and_conserve_amount():
    components = [
        {
            "name": "COMP-1",
            "fee_rule": "RULE-import_tax",
            "logical_fee_key": "import_tax",
            "evidence": "EVIDENCE-1",
            "attachment": "ATTACHMENT-1",
            "stable_line_key": "line-1",
            "amount_rmb": "20",
            "status": "CONFIRMED",
            "is_active": 1,
            "cost_effect": "COST",
            "tax_code": "IGI",
            "hs_code": "3926.90",
            "source_evidence_json": '{"page":2,"line":18}',
        },
        {
            "name": "COMP-2",
            "fee_rule": "RULE-import_tax",
            "logical_fee_key": "import_tax",
            "evidence": "EVIDENCE-1",
            "attachment": "ATTACHMENT-1",
            "stable_line_key": "line-2",
            "amount_rmb": "80",
            "status": "CONFIRMED",
            "is_active": 1,
            "cost_effect": "COST",
            "tax_code": "IGI",
            "hs_code": "3926.90",
            "source_evidence_json": '{"page":2,"line":19}',
        },
    ]
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee("import_tax")],
        fx_context={},
        fee_components=components,
        context={"batch_name": "B1", "version_name": "V1"},
        ai_result={"ok": True, "rules": [{**_fee("import_tax"), "allocation_basis": "volume"}]},
    )

    proposal = result["fee_suggestions"][0]
    assert proposal["evidence_locked"] is True
    assert proposal["evidence_component_count"] == 2
    assert Decimal(proposal["evidence_amount_rmb"]) == Decimal("100.00")
    assert proposal["requires_user_choice"] is False
    assert {row["tax_code"] for row in proposal["evidence_summary"]} == {"IGI"}

    preview = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS,
        fees=[_fee("import_tax")],
        fx_context={},
        fee_components=components,
        draft=result,
        selections=[],
    )
    evidence_lock = preview["trial_review"]["evidence_locks"][0]
    assert evidence_lock["fee_key"] == "import_tax"
    assert evidence_lock["component_count"] == 2
    assert evidence_lock["components"][0]["evidence"] == "EVIDENCE-1"
    assert evidence_lock["components"][0]["attachment"] == "ATTACHMENT-1"
    assert evidence_lock["components"][0]["source_evidence"]["page"] == 2


def test_component_with_unmatched_sku_cannot_be_evidence_locked():
    result = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee("import_tax")],
        fx_context={},
        fee_components=[{
            "fee_rule": "RULE-import_tax",
            "logical_fee_key": "import_tax",
            "stable_line_key": "missing-line",
            "amount_rmb": "100",
            "status": "CONFIRMED",
            "is_active": 1,
            "cost_effect": "COST",
        }],
        context={"batch_name": "B1", "version_name": "V1"},
        ai_result={"ok": True, "rules": [_fee("import_tax")]},
    )

    proposal = result["fee_suggestions"][0]
    assert proposal["evidence_locked"] is False
    assert proposal["evidence_issue"] == "SKU_MATCH_INVALID"
    assert proposal["blocked"] is True
    with pytest.raises(ValueError, match="凭证.*SKU"):
        cost_trial_ai_service.preview_selected_cost_trial(
            items=ITEMS,
            fees=[_fee("import_tax")],
            fx_context={},
            fee_components=[],
            draft=result,
            selections=[],
        )


class TrialRepository:
    def __init__(self):
        self.context = {
            "batch_name": "B1",
            "version_name": "V1",
            "batch": "B1",
            "version": "V1",
            "current_version": "V1",
            "version_status": "Draft",
            "confirm_status": "Unconfirmed",
            "is_locked": 0,
            "transport_mode": "AIR",
            "batch_modified": "m1",
        }
        self.items = ITEMS
        self.fees = [_fee()]
        self.fx = {}
        self.components = []
        self.runs = {}
        self.created = 0
        self.saved_calculation = None
        self.save_calculation_hook = None
        self.save_calculation_error = None
        self.previous_trial_review = {}
        self.commits = 0
        self.rollbacks = 0

    def load_trial_inputs(self, batch_name, version_name, *, edit_token="", expected_modified="", write=False, trusted=False):
        assert batch_name == "B1"
        if write and not trusted:
            assert edit_token == "T" and expected_modified == "m1"
        return {
            "context": dict(self.context),
            "items": [dict(row) for row in self.items],
            "fees": [dict(row) for row in self.fees],
            "fx_context": dict(self.fx),
            "fee_components": [dict(row) for row in self.components],
        }

    def find_active(self, batch_name, version_name, fingerprint):
        return next((row for row in self.runs.values() if row["input_fingerprint"] == fingerprint and row["status"] in {"QUEUED", "RUNNING"}), None)

    def find_reusable(self, batch_name, version_name, fingerprint):
        return next((row for row in self.runs.values() if row["input_fingerprint"] == fingerprint and row["status"] == "READY"), None)

    def load_previous_trial_review(self, batch_name, version_name):
        assert batch_name == "B1" and version_name == "V1"
        return dict(self.previous_trial_review)

    def create_run(self, values):
        self.created += 1
        row = {"name": f"RUN-{self.created}", **values}
        self.runs[row["name"]] = row
        return row

    def get_run(self, run_id):
        return self.runs[run_id]

    def save_run(self, run_id, **values):
        self.runs[run_id].update(values)
        self.runs[run_id]["progress_revision"] = int(self.runs[run_id].get("progress_revision") or 0) + 1
        return self.runs[run_id]

    def claim_run(self, run_id):
        if self.runs[run_id]["status"] != "QUEUED":
            return False
        self.save_run(
            run_id,
            status="RUNNING",
            progress_step="DeepSeek 正在分析费用口径",
            progress_percent=30,
        )
        return True

    def save_run_if_status(self, run_id, expected_status, **values):
        if self.runs[run_id]["status"] != expected_status:
            return False
        self.save_run(run_id, **values)
        return True

    def save_calculation(self, inputs, preview, trial_review):
        if callable(self.save_calculation_hook):
            self.save_calculation_hook()
        if self.save_calculation_error:
            raise self.save_calculation_error
        self.saved_calculation = {"preview": preview, "trial_review": trial_review}
        return {**preview, "ok": True, "saved": True, "batch_modified": "m2"}

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FxTrialRepository(TrialRepository):
    def __init__(self):
        super().__init__()
        self.fx_resolution = {
            "ok": True,
            "calculation_date": "2026-09-20",
            "fx_usd_to_rmb": 6.71,
            "fx_rmb_to_mxn": 2.564103,
            "is_estimated": False,
            "blocking_errors": [],
            "rates": {
                "USD": {"currency": "USD", "source": "fx_api", "rate_date": "2026-09-20", "cny_per_unit": 6.71},
                "MXN": {"currency": "MXN", "source": "fx_api", "rate_date": "2026-09-20", "cny_per_unit": 0.39},
            },
        }
        self.prepared = 0
        self.persisted = 0

    def prepare_fx_resolution(self, batch_name, version_name):
        assert batch_name == "B1" and version_name == "V1"
        self.prepared += 1
        return dict(self.fx_resolution)

    def persist_fx_resolution(self, inputs, resolution):
        assert inputs["context"]["batch"] == "B1"
        assert resolution["calculation_date"] == "2026-09-20"
        self.persisted += 1
        return {
            "fx_context": {
                "fx_usd_to_rmb": resolution["fx_usd_to_rmb"],
                "fx_rmb_to_mxn": resolution["fx_rmb_to_mxn"],
            },
            "fx_resolution": dict(resolution),
            "changed_fields": ["fx_usd_to_rmb", "fx_rmb_to_mxn"],
        }


def test_trial_stops_before_ai_when_fx_date_is_missing():
    repo = FxTrialRepository()
    repo.fx_resolution = {
        "ok": False,
        "blocking_errors": [{"code": "FX_DATE_REQUIRED", "message": "缺少真实付款日和付款审批完成日"}],
    }
    with pytest.raises(ValueError, match="缺少真实付款日"):
        cost_trial_ai_service.start_cost_trial_ai_review(
            "B1", "V1", repository=repo, enqueue=lambda *_args, **_kwargs: pytest.fail("AI must not start"),
        )
    assert repo.prepared == 1
    assert repo.persisted == 0
    assert repo.rollbacks == 1


def _ai_volume(**_kwargs):
    return {
        "ok": True,
        "action": "suggested",
        "model": "deepseek-test",
        "rules": [{**_fee(), "allocation_basis": "volume", "ai_confidence": 0.91}],
    }


def test_lifecycle_reuses_same_ready_input_unless_force_is_requested():
    repo = TrialRepository()
    queued = []
    first = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=queued.append
    )
    assert first == {
        "ok": True,
        "run_id": "RUN-1",
        "status": "QUEUED",
        "reused": False,
        "progress_revision": 0,
        "ai_invoked": False,
        "ai_required": True,
        "ai_candidate_count": 1,
    }
    assert queued == ["RUN-1"]

    cost_trial_ai_service.execute_cost_trial_ai_review("RUN-1", repository=repo, ai_suggester=_ai_volume)
    reused = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=queued.append
    )
    forced = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", force=True, repository=repo, enqueue=queued.append
    )

    assert reused["run_id"] == "RUN-1" and reused["reused"] is True
    assert forced["run_id"] == "RUN-2" and forced["reused"] is False
    assert queued == ["RUN-1", "RUN-2"]


def test_previous_valid_choice_makes_run_ready_without_calling_ai():
    repo = TrialRepository()
    repo.previous_trial_review = {
        "fee_choices": [{
            "fee_key": "international_air_freight",
            "basis": "gross_weight",
            "decision_source": "AI",
        }],
    }
    queued = []

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=queued.append
    )

    assert started["status"] == "READY"
    assert started["ai_invoked"] is False
    assert queued == []
    proposal = repo.runs[started["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["default_basis"] == "gross_weight"
    assert proposal["decision_source"] == "REUSED"
    assert proposal["available_bases"] == ["goods_value", "gross_weight", "chargeable_weight"]


def test_adjustment_reuse_only_never_queues_ai_and_uses_complete_local_default():
    repo = TrialRepository()
    queued = []

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", reuse_only=True,
        repository=repo, enqueue=queued.append,
    )

    assert started["status"] == "READY"
    assert started["ai_invoked"] is False
    assert started["ai_required"] is False
    assert started["reuse_reason"] == "ADJUSTMENT_DEFAULTS"
    assert queued == []
    proposal = repo.runs[started["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["default_basis"] == "chargeable_weight"
    assert proposal["decision_source"] == "SYSTEM_FALLBACK"


def test_adjustment_reuse_only_does_not_attach_to_an_active_ai_run():
    repo = TrialRepository()
    queued = []
    active = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=queued.append,
    )

    adjustment = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", reuse_only=True,
        repository=repo, enqueue=queued.append,
    )

    assert active["status"] == "QUEUED"
    assert adjustment["status"] == "READY"
    assert adjustment["run_id"] != active["run_id"]
    assert queued == [active["run_id"]]


def test_only_unresolved_fees_are_sent_in_one_ai_request():
    repo = TrialRepository()
    repo.fees = [_fee("international_air_freight", 100), _fee("destination_delivery", 50)]
    repo.previous_trial_review = {
        "fee_choices": [{"fee_key": "international_air_freight", "basis": "gross_weight"}],
    }
    received = []

    def capture_suggester(*, items, candidate_rules, context):
        received.append([dict(row) for row in candidate_rules])
        return {
            "ok": True,
            "action": "suggested",
            "model": "deepseek-test",
            "rules": [{**candidate_rules[0], "allocation_basis": "chargeable_weight", "ai_confidence": 0.9}],
        }

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=capture_suggester
    )

    assert len(received) == 1
    assert [row["logical_fee_key"] for row in received[0]] == ["destination_delivery"]
    assert received[0][0]["available_bases"] == ["goods_value", "gross_weight", "chargeable_weight"]
    proposals = {row["fee_key"]: row for row in repo.runs[started["run_id"]]["draft_json"]["fee_suggestions"]}
    assert proposals["international_air_freight"]["decision_source"] == "REUSED"
    assert proposals["destination_delivery"]["decision_source"] == "AI"
    assert repo.runs[started["run_id"]]["draft_json"]["decision_summary"] == {
        "reused": 1,
        "ai": 1,
        "system_fallback": 0,
        "evidence": 0,
    }


def test_confirmed_review_takes_precedence_over_an_older_ready_run():
    repo = TrialRepository()
    first = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        first["run_id"], repository=repo,
        ai_suggester=lambda **_kwargs: {
            "ok": True,
            "model": "deepseek-test",
            "rules": [{**_fee(), "allocation_basis": "goods_value"}],
        },
    )
    repo.previous_trial_review = {
        "fee_choices": [{"fee_key": "international_air_freight", "basis": "gross_weight"}],
    }

    second = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )

    assert second["run_id"] != first["run_id"]
    assert second["status"] == "READY"
    proposal = repo.runs[second["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["default_basis"] == "gross_weight"
    assert proposal["decision_source"] == "REUSED"


def test_confirmed_review_source_takes_precedence_even_when_old_ready_basis_matches():
    repo = TrialRepository()
    first = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        first["run_id"], repository=repo,
        ai_suggester=lambda **_kwargs: {
            "ok": True,
            "model": "deepseek-test",
            "rules": [{**_fee(), "allocation_basis": "gross_weight"}],
        },
    )
    repo.previous_trial_review = {
        "fee_choices": [{
            "fee_key": "international_air_freight",
            "basis": "gross_weight",
            "decision_source": "USER_OVERRIDE",
        }],
    }

    second = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )

    assert second["run_id"] != first["run_id"]
    proposal = repo.runs[second["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["decision_source"] == "REUSED"
    assert repo.runs[second["run_id"]]["draft_json"]["decision_summary"]["reused"] == 1


def test_ai_failure_is_not_retried_and_uses_a_complete_system_fallback():
    repo = TrialRepository()
    calls = 0

    def failing_suggester(**_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("upstream timeout")

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    result = cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=failing_suggester,
    )

    assert calls == 1
    assert result["status"] == "READY"
    draft = repo.runs[started["run_id"]]["draft_json"]
    assert draft["ai_invoked"] is True
    assert draft["fee_suggestions"][0]["decision_source"] == "SYSTEM_FALLBACK"
    assert draft["fee_suggestions"][0]["default_basis"] == "chargeable_weight"


def test_discard_during_ai_call_cannot_be_revived_to_ready():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )

    def discard_then_reply(**_kwargs):
        cost_trial_ai_service.discard_cost_trial_ai_review(
            "B1", started["run_id"], repository=repo,
        )
        return {"ok": True, "model": "deepseek-test", "rules": []}

    result = cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=discard_then_reply,
    )

    assert result["status"] == "DISCARDED"
    assert repo.runs[started["run_id"]]["status"] == "DISCARDED"


def test_second_worker_does_not_call_ai_for_an_already_running_run():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    repo.save_run(started["run_id"], status="RUNNING")
    calls = 0

    def count_calls(**_kwargs):
        nonlocal calls
        calls += 1
        return {"ok": True, "rules": []}

    result = cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=count_calls,
    )

    assert result["status"] == "RUNNING"
    assert calls == 0


def test_force_ai_ignores_reusable_choice_but_keeps_available_basis_limits():
    repo = TrialRepository()
    repo.previous_trial_review = {
        "fee_choices": [{"fee_key": "international_air_freight", "basis": "gross_weight"}],
    }
    received = []

    def capture_suggester(*, candidate_rules, **_kwargs):
        received.extend(candidate_rules)
        return {"ok": False, "action": "failed", "rules": [], "reason": "unavailable"}

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", force=True,
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=capture_suggester,
    )

    assert len(received) == 1
    assert received[0]["available_bases"] == ["goods_value", "gross_weight", "chargeable_weight"]
    proposal = repo.runs[started["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["decision_source"] == "SYSTEM_FALLBACK"


def test_force_ai_ignores_legacy_confirmed_rule_and_active_run():
    repo = TrialRepository()
    queued = []
    normal = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=queued.append,
    )
    repo.fees[0]["remark"] = "AI试算自动：按计费重分摊"

    forced = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", force=True,
        repository=repo, enqueue=queued.append,
    )

    assert normal["status"] == "QUEUED"
    assert forced["status"] == "QUEUED"
    assert forced["run_id"] != normal["run_id"]
    assert queued == [normal["run_id"], forced["run_id"]]


def test_force_ai_candidate_does_not_carry_old_decision_outputs():
    repo = TrialRepository()
    repo.fees[0].update({
        "allocation_basis": "gross_weight",
        "basis_field": "gross_weight",
        "remark": "AI试算自动：旧口径",
        "scope_revision": "manual:old",
        "priority_no": 999,
    })
    received = []
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", force=True,
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo,
        ai_suggester=lambda **kwargs: received.extend(kwargs["candidate_rules"]) or {
            "ok": False, "rules": [],
        },
    )

    assert len(received) == 1
    for field in ("allocation_basis", "basis_field", "remark", "scope_revision", "priority_no"):
        assert field not in received[0]
    assert received[0]["available_bases"] == ["goods_value", "gross_weight", "chargeable_weight"]


def test_no_complete_basis_blocks_without_calling_ai():
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    repo = TrialRepository()
    repo.items = [
        {**ITEMS[0], "goods_value": 0, "shipment_value_rmb": 0, "gross_weight_kg": 0,
         "volume_m3": 0, "chargeable_weight_kg": 0},
        {**ITEMS[1], "goods_value": 0, "shipment_value_rmb": 0, "gross_weight_kg": 0,
         "volume_m3": 0, "chargeable_weight_kg": 0},
    ]
    for row in repo.items:
        row["extra_json"] = json.dumps({
            "manual_shipment_valuation": build_manual_shipment_valuation(
                row, 0, actor="buyer@example.com", reason="免费样品"
            )
        })
    queued = []

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=queued.append
    )

    assert started["status"] == "READY"
    assert started["ai_invoked"] is False
    assert queued == []
    proposal = repo.runs[started["run_id"]]["draft_json"]["fee_suggestions"][0]
    assert proposal["blocked"] is True
    assert proposal["available_bases"] == []
    assert proposal["missing_fields"] == [
        "goods_value", "gross_weight_kg", "volume_m3", "chargeable_weight_kg",
    ]


def test_decision_fingerprint_ignores_output_metadata_but_tracks_cost_inputs():
    base = cost_trial_ai_service.build_input_fingerprint(
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR", "batch_modified": "m1"},
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        fee_components=[],
    )
    output_changed = cost_trial_ai_service.build_input_fingerprint(
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR", "batch_modified": "m2"},
        items=ITEMS,
        fees=[{**_fee(), "allocation_basis": "gross_weight", "remark": "AI 试算确认"}],
        fx_context={},
        fee_components=[],
    )
    input_changed = cost_trial_ai_service.build_input_fingerprint(
        context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR", "batch_modified": "m2"},
        items=ITEMS,
        fees=[_fee(amount=101)],
        fx_context={},
        fee_components=[],
    )

    assert output_changed == base
    assert input_changed != base


def test_decision_fingerprint_uses_effective_shipment_value_not_extra_json_timestamps():
    item = {
        **ITEMS[0],
        "actual_shipped_qty": 1,
        "shipped_uom": "PCS",
        "extra_json": '{"shipment_valuation":{"amount_rmb":100,"currency":"RMB","quantity":1,"uom":"PCS","calculated_at":"t1"}}',
    }
    timestamp_changed = {
        **item,
        "extra_json": '{"shipment_valuation":{"amount_rmb":100,"currency":"RMB","quantity":1,"uom":"PCS","calculated_at":"t2"}}',
    }
    value_changed = {
        **item,
        "extra_json": '{"shipment_valuation":{"amount_rmb":110,"currency":"RMB","quantity":1,"uom":"PCS","calculated_at":"t2"}}',
    }

    def fingerprint(row):
        return cost_trial_ai_service.build_input_fingerprint(
            context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
            items=[row], fees=[_fee()], fx_context={}, fee_components=[],
        )

    assert fingerprint(timestamp_changed) == fingerprint(item)
    assert fingerprint(value_changed) != fingerprint(item)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("material_code", "MAT-CHANGED"),
        ("product_name", "新品名"),
        ("category", "新分类"),
        ("quantity", 99),
        ("volume_weight_kg", 88),
        ("project_collection", "PROJECT-2"),
        ("supplier", "SUPPLIER-2"),
    ],
)
def test_decision_fingerprint_tracks_all_ai_decision_item_inputs(field, value):
    item = {
        **ITEMS[0],
        "material_code": "MAT-1",
        "product_name": "品名",
        "category": "分类",
        "quantity": 1,
        "volume_weight_kg": 2,
        "project_collection": "PROJECT-1",
        "supplier": "SUPPLIER-1",
    }

    def fingerprint(row):
        return cost_trial_ai_service.build_input_fingerprint(
            context={"batch_name": "B1", "version_name": "V1", "transport_mode": "AIR"},
            items=[row], fees=[_fee()], fx_context={}, fee_components=[],
        )

    assert fingerprint({**item, field: value}) != fingerprint(item)


def test_execute_does_not_send_zero_amount_fee_to_deepseek():
    repo = TrialRepository()
    repo.fees = [_fee("destination_delivery", "0"), _fee("international_air_freight", "100")]
    received = []

    def capture_suggester(*, items, candidate_rules, context):
        received.extend(candidate_rules)
        return {
            "ok": True,
            "action": "suggested",
            "model": "deepseek-test",
            "rules": [{**candidate_rules[0], "allocation_basis": "goods_value"}],
        }

    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=capture_suggester
    )

    assert [_fee_row["logical_fee_key"] for _fee_row in received] == ["international_air_freight"]


def test_v1_ready_run_is_not_reused_after_zero_fee_policy_change():
    repo = TrialRepository()
    legacy_payload = {
        "schema": 1,
        "context": repo.context,
        "items": repo.items,
        "fees": repo.fees,
        "fx_context": repo.fx,
        "fee_components": repo.components,
    }
    legacy_fingerprint = hashlib.sha256(
        cost_trial_ai_service._json(legacy_payload).encode("utf-8")
    ).hexdigest()
    repo.runs["RUN-V1"] = {
        "name": "RUN-V1",
        "batch": "B1",
        "version": "V1",
        "status": "READY",
        "input_fingerprint": legacy_fingerprint,
        "progress_revision": 3,
    }

    result = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )

    assert result["run_id"] != "RUN-V1"
    assert result["reused"] is False


def test_zero_to_nonzero_change_invalidates_ready_preview():
    repo = TrialRepository()
    repo.fees = [_fee("destination_delivery", "0")]
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=lambda **_kwargs: {"ok": True, "rules": []}
    )
    repo.fees[0]["amount"] = "25"

    with pytest.raises(ValueError, match="输入已变化"):
        cost_trial_ai_service.preview_cost_trial("B1", started["run_id"], [], repository=repo)


def test_feature_switch_can_disable_new_ai_trial_entry(monkeypatch):
    repo = TrialRepository()
    monkeypatch.setattr(cost_trial_ai_service, "cost_trial_ai_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="未启用"):
        cost_trial_ai_service.start_cost_trial_ai_review(
            "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
        )

    assert repo.created == 0


def test_start_rejects_missing_purchase_values_before_creating_or_enqueuing_ai_run():
    repo = TrialRepository()
    repo.items = [dict(ITEMS[0]), {**ITEMS[1], "goods_value": "", "shipment_value_rmb": None}]
    queued = []

    with pytest.raises(
        ValueError,
        match="还有 1 行本次发货货值缺失或失效，请先补齐后再试算",
    ):
        cost_trial_ai_service.start_cost_trial_ai_review(
            "B1", "V1", edit_token="T", expected_modified="m1",
            repository=repo, enqueue=queued.append,
        )

    assert repo.created == 0
    assert queued == []
    assert repo.saved_calculation is None
    assert repo.commits == 0
    assert repo.rollbacks == 1


def test_legacy_queued_run_with_missing_purchase_values_fails_before_ai_invocation():
    repo = TrialRepository()
    repo.items = [dict(ITEMS[0]), {**ITEMS[1], "goods_value": "", "shipment_value_rmb": None}]
    inputs = repo.load_trial_inputs("B1", "V1", write=False, trusted=True)
    run = repo.create_run({
        "batch": "B1",
        "version": "V1",
        "status": "QUEUED",
        "input_fingerprint": cost_trial_ai_service._input_fingerprint(inputs),
        "progress_revision": 0,
        "draft_json": {},
    })
    invoked = []

    result = cost_trial_ai_service.execute_cost_trial_ai_review(
        run["name"], repository=repo,
        ai_suggester=lambda **kwargs: invoked.append(kwargs) or {"ok": True, "rules": []},
    )

    assert result["status"] == "FAILED"
    assert "还有 1 行" in result["message"]
    assert invoked == []
    assert repo.runs[run["name"]]["status"] == "FAILED"


def test_preview_marks_run_stale_when_any_trial_input_changes():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(started["run_id"], repository=repo, ai_suggester=_ai_volume)
    repo.items[0]["gross_weight_kg"] = 11

    with pytest.raises(ValueError, match="输入已变化"):
        cost_trial_ai_service.preview_cost_trial(
            "B1", started["run_id"], [], repository=repo
        )
    assert repo.runs["RUN-1"]["status"] == "STALE"


def test_confirm_validates_server_preview_token_and_saves_trial_audit():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(started["run_id"], repository=repo, ai_suggester=_ai_volume)
    draft = repo.runs["RUN-1"]["draft_json"]
    selection = [{
        "suggestion_id": draft["fee_suggestions"][0]["suggestion_id"],
        "basis": "gross_weight",
        "reason": "暂按毛重",
    }]
    preview = cost_trial_ai_service.preview_cost_trial("B1", "RUN-1", selection, repository=repo)

    with pytest.raises(ValueError, match="预览已失效"):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", "RUN-1", "wrong", selection, edit_token="T", expected_modified="m1", repository=repo
        )
    saved = cost_trial_ai_service.confirm_cost_trial(
        "B1", "RUN-1", preview["preview_token"], selection,
        edit_token="T", expected_modified="m1", repository=repo,
    )

    assert saved["saved"] is True
    assert saved["trial_review"]["run_id"] == "RUN-1"
    assert saved["trial_review"]["is_temporary"] is False
    assert repo.runs["RUN-1"]["status"] == "CONFIRMED"
    assert repo.saved_calculation["trial_review"]["fee_choices"][0]["basis"] == "gross_weight"


def test_confirm_rejects_a_matching_legacy_ready_run_with_missing_purchase_values():
    repo = TrialRepository()
    repo.items = [dict(ITEMS[0]), {**ITEMS[1], "goods_value": "", "shipment_value_rmb": None}]
    repo.fees = [_fee(amount=0)]
    inputs = repo.load_trial_inputs("B1", "V1", write=False, trusted=True)
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=inputs["items"],
        fees=inputs["fees"],
        fx_context=inputs["fx_context"],
        context=inputs["context"],
        fee_components=[],
        ai_result={"ok": True, "action": "not_needed", "model": "", "rules": []},
    )
    run = repo.create_run({
        "batch": "B1",
        "version": "V1",
        "status": "READY",
        "input_fingerprint": cost_trial_ai_service._input_fingerprint(inputs),
        "progress_revision": 0,
        "draft_json": draft,
    })
    preview = cost_trial_ai_service.preview_cost_trial("B1", run["name"], [], repository=repo)

    with pytest.raises(
        ValueError,
        match="还有 1 行本次发货货值缺失或失效，请先补齐后再试算",
    ):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", run["name"], preview["preview_token"], [],
            edit_token="T", expected_modified="m1", repository=repo,
        )

    assert repo.saved_calculation is None
    assert repo.runs[run["name"]]["status"] == "READY"
    assert "还有 1 行" in repo.runs[run["name"]]["error_message"]


def test_trial_carries_signed_fx_resolution_and_persists_it_only_on_valid_confirm():
    repo = FxTrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    assert repo.prepared == 1
    assert repo.runs[started["run_id"]]["draft_json"]["fx_resolution"]["fx_rmb_to_mxn"] == pytest.approx(2.564103)

    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )

    assert preview["fx_resolution"]["calculation_date"] == "2026-09-20"
    assert preview["summary"]["total_cost_rmb"] == "500.00"
    assert repo.persisted == 0

    with pytest.raises(ValueError, match="预览已失效"):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", started["run_id"], "wrong", [], edit_token="T", expected_modified="m1", repository=repo,
        )
    assert repo.persisted == 0

    saved = cost_trial_ai_service.confirm_cost_trial(
        "B1", started["run_id"], preview["preview_token"], [],
        edit_token="T", expected_modified="m1", repository=repo,
    )

    assert saved["saved"] is True
    assert saved["fx_resolution"]["fx_usd_to_rmb"] == pytest.approx(6.71)
    assert repo.persisted == 1
    assert repo.saved_calculation["preview"]["fx_resolution"]["fx_rmb_to_mxn"] == pytest.approx(2.564103)


def test_frappe_trial_save_rejects_null_mxn_before_repository_write(monkeypatch):
    saved = []

    class FakeCostRepository:
        def assert_unchanged(self, _context):
            return None

        def save(self, _context, result):
            saved.append(result)
            return "m2"

    monkeypatch.setattr(cost_trial_ai_service, "frappe", object())
    monkeypatch.setattr(cost_trial_ai_service.cost_preview_service, "FrappeCostRepository", FakeCostRepository)
    repository = cost_trial_ai_service.FrappeCostTrialAIRepository()
    monkeypatch.setattr(repository, "get_run", lambda _run_id: {"draft_json": {"fee_suggestions": []}})

    with pytest.raises(ValueError, match="人民币兑比索汇率"):
        repository.save_calculation(
            {
                "context": {"batch": "B1", "version": "V1", "transport_mode": "AIR"},
                "items": [dict(row) for row in ITEMS],
                "fees": [],
                "fx_context": {},
                "fee_components": [],
            },
            {"summary": {}, "trial_review": {}},
            {"run_id": "RUN-1", "fee_choices": []},
        )

    assert saved == []


def test_confirm_claim_prevents_discard_from_overwriting_a_saved_trial():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )
    discard_errors = []

    def try_discard_while_saving():
        try:
            cost_trial_ai_service.discard_cost_trial_ai_review(
                "B1", started["run_id"], repository=repo,
            )
        except ValueError as exc:
            discard_errors.append(str(exc))

    repo.save_calculation_hook = try_discard_while_saving
    saved = cost_trial_ai_service.confirm_cost_trial(
        "B1", started["run_id"], preview["preview_token"], [],
        edit_token="T", expected_modified="m1", repository=repo,
    )

    assert saved["saved"] is True
    assert repo.runs[started["run_id"]]["status"] == "CONFIRMED"
    assert discard_errors and "正在保存" in discard_errors[0]


def test_confirm_does_not_save_if_ready_claim_loses_to_discard():
    class LoseReadyClaimRepository(TrialRepository):
        def save_run_if_status(self, run_id, expected_status, **values):
            if expected_status == "READY":
                self.runs[run_id]["status"] = "DISCARDED"
                return False
            return super().save_run_if_status(run_id, expected_status, **values)

    repo = LoseReadyClaimRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )

    with pytest.raises(ValueError, match="已不可确认"):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", started["run_id"], preview["preview_token"], [],
            edit_token="T", expected_modified="m1", repository=repo,
        )
    assert repo.saved_calculation is None
    assert repo.runs[started["run_id"]]["status"] == "DISCARDED"


def test_failed_save_releases_confirm_claim_and_preserves_ready_choices():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )
    repo.save_calculation_error = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", started["run_id"], preview["preview_token"], [],
            edit_token="T", expected_modified="m1", repository=repo,
        )

    assert repo.runs[started["run_id"]]["status"] == "READY"
    assert repo.runs[started["run_id"]]["draft_json"]
    assert repo.runs[started["run_id"]]["error_message"] == "database unavailable"

    repo.save_calculation_error = None
    saved = cost_trial_ai_service.confirm_cost_trial(
        "B1", started["run_id"], preview["preview_token"], [],
        edit_token="T", expected_modified="m1", repository=repo,
    )

    assert saved["saved"] is True
    assert repo.runs[started["run_id"]]["status"] == "CONFIRMED"
    assert repo.runs[started["run_id"]]["error_message"] == ""


@pytest.mark.parametrize(
    ("context_change", "message"),
    [
        ({"current_version": "V2"}, "只能试算当前版本"),
        ({"version_status": "Confirmed"}, "已确认或归档版本不能覆盖"),
        ({"confirm_status": "Confirmed"}, "已确认或归档版本不能覆盖"),
        ({"is_locked": 1}, "已确认或归档版本不能覆盖"),
    ],
)
def test_confirm_rechecks_current_version_and_confirmation_boundaries(context_change, message):
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )
    repo.context.update(context_change)

    with pytest.raises((ValueError, PermissionError), match=message):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", started["run_id"], preview["preview_token"], [],
            edit_token="T", expected_modified="m1", repository=repo,
        )
    assert repo.saved_calculation is None


def test_start_rejects_a_non_current_or_locked_version():
    repo = TrialRepository()
    repo.context["current_version"] = "V2"

    with pytest.raises(ValueError, match="只能试算当前版本"):
        cost_trial_ai_service.start_cost_trial_ai_review(
            "B1", "V1", edit_token="T", expected_modified="m1",
            repository=repo, enqueue=lambda _run: None,
        )


def test_start_fails_closed_when_current_version_is_missing():
    repo = TrialRepository()
    repo.context["current_version"] = ""

    with pytest.raises(ValueError, match="只能试算当前版本"):
        cost_trial_ai_service.start_cost_trial_ai_review(
            "B1", "V1", edit_token="T", expected_modified="m1",
            repository=repo, enqueue=lambda _run: None,
        )


def test_preview_token_is_bound_to_the_ready_run_and_draft():
    repo = TrialRepository()
    started = cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1",
        repository=repo, enqueue=lambda _run: None,
    )
    cost_trial_ai_service.execute_cost_trial_ai_review(
        started["run_id"], repository=repo, ai_suggester=_ai_volume,
    )
    preview = cost_trial_ai_service.preview_cost_trial(
        "B1", started["run_id"], [], repository=repo,
    )
    repo.runs["RUN-2"] = {
        **repo.runs[started["run_id"]],
        "name": "RUN-2",
        "status": "READY",
    }

    with pytest.raises(ValueError, match="预览已失效"):
        cost_trial_ai_service.confirm_cost_trial(
            "B1", "RUN-2", preview["preview_token"], [],
            edit_token="T", expected_modified="m1", repository=repo,
        )


def test_preview_token_is_server_signed(monkeypatch):
    draft = cost_trial_ai_service.build_cost_trial_review_draft(
        items=ITEMS,
        fees=[_fee()],
        fx_context={},
        context={"batch_name": "B1", "version_name": "V1"},
        ai_result={"ok": True, "rules": [_fee()]},
    )
    proposal = draft["fee_suggestions"][0]
    selections = [{"suggestion_id": proposal["suggestion_id"], "basis": "goods_value"}]

    monkeypatch.setattr(cost_trial_ai_service, "_preview_token_secret", lambda: b"first-secret")
    first = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS, fees=[_fee()], fx_context={}, fee_components=[], draft=draft, selections=selections,
    )
    monkeypatch.setattr(cost_trial_ai_service, "_preview_token_secret", lambda: b"second-secret")
    second = cost_trial_ai_service.preview_selected_cost_trial(
        items=ITEMS, fees=[_fee()], fx_context={}, fee_components=[], draft=draft, selections=selections,
    )

    assert first["preview_token"] != second["preview_token"]


def test_discard_does_not_change_a_confirmed_run():
    repo = TrialRepository()
    repo.runs["RUN-1"] = {
        "name": "RUN-1",
        "batch": "B1",
        "version": "V1",
        "status": "CONFIRMED",
        "progress_revision": 4,
    }

    result = cost_trial_ai_service.discard_cost_trial_ai_review("B1", "RUN-1", repository=repo)

    assert result["status"] == "CONFIRMED"
    assert repo.runs["RUN-1"]["status"] == "CONFIRMED"
    assert repo.commits == 0


def test_status_does_not_expose_private_input_snapshots():
    repo = TrialRepository()
    cost_trial_ai_service.start_cost_trial_ai_review(
        "B1", "V1", edit_token="T", expected_modified="m1", repository=repo, enqueue=lambda _run: None
    )
    cost_trial_ai_service.execute_cost_trial_ai_review("RUN-1", repository=repo, ai_suggester=_ai_volume)

    result = cost_trial_ai_service.get_cost_trial_ai_review_status("B1", "RUN-1", repository=repo)

    assert result["status"] == "READY"
    assert result["draft"]["model"] == "deepseek-test"
    assert "items_json" not in result and "fees_json" not in result
