from __future__ import annotations

from copy import deepcopy

import pytest

from overseas_costing.services import material_ai_fee_policy
from overseas_costing.services.material_ai_fill_service import (
    build_payment_material_extension_proposals,
    build_semantic_payment_fee_proposals,
    normalize_source_review_proposals,
)
from overseas_costing.services.material_ai_semantic_facts import build_payment_facts


def _items() -> list[dict]:
    return [
        {
            "name": "ITEM-144",
            "stable_line_key": "LINE-144",
            "material_code": "MWV101144",
            "product_name": "薇武士 IP17 PRO",
        },
        {
            "name": "ITEM-145",
            "stable_line_key": "LINE-145",
            "material_code": "MWV101145",
            "product_name": "薇武士 IP17 PRO MAX",
        },
    ]


def _line(row: int, waybill: str, code: str) -> dict:
    return {
        "id": f"LINE-{row}",
        "line_key": f"BUSINESS-{waybill}",
        "waybill": waybill,
        "approval_no": f"SHIP-{row}",
        "scope": "freight",
        "amount": "3414.19",
        "currency": "RMB",
        "cargo_text": code,
        "packing": {"material_code_hints": [code]},
        "evidence": {
            "document_id": "DHL-MONTHLY",
            "file_name": "DHL-monthly.xlsx",
            "sheet": "DHL快递",
            "row": row,
            "cell": f"K{row}",
        },
    }


def _document() -> dict:
    facts = build_payment_facts(
        _items(),
        {
            "id": "PAYMENT",
            "instance": "PAY-1",
            "approval_no": "PAY-1",
            "title": "月结付款",
        },
        [
            _line(14, "1841361513", "MWV101144"),
            _line(13, "1841364722", "MWV101145"),
        ],
    )
    return {
        "document_id": "DOC-1",
        "source_ref": {
            "source": "approval_attachment",
            "file": "DHL-monthly.xlsx",
            "source_id": "PAYMENT",
            "process_instance_id": "PAY-1",
            "workflow_stage": "payment",
            "workflow_rank": 0,
            "evidence_kind": "attachment",
            "evidence_rank": 2,
            "priority_reason": "支付申请阶段",
        },
        "structured_rows": [
            {"source_row": 13, "amount": "3414.19", "currency": "RMB"},
            {"source_row": 14, "amount": "3414.19", "currency": "RMB"},
        ],
        "semantic_facts": facts,
        "ai_eligible": False,
    }


def test_same_shipment_payment_fact_becomes_server_owned_material_extension():
    facts = build_payment_facts(
        [_items()[0]],
        {'id':'PAYMENT','instance':'PAY-1','approval_no':'PAY-1','title':'月结付款'},
        [{
            **_line(13, '1841364722', 'MWV101145'),
            'material_code':'MWV101145',
            'packing': {
                'material_code_hints':['MWV101145'],
                'package_count':'1','gross_weight_kg':'42.05',
                'volume_m3':'0.01518','chargeable_weight_kg':'46',
            },
            'product_name':'薇武士 IP17 PRO MAX',
        }],
        shipment_identifiers={'1841364722'},
    )
    document={
        'document_id':'DOC-EXT',
        'source_ref':{'source_id':'PAYMENT'},
        'semantic_facts':facts,
    }

    proposals=build_payment_material_extension_proposals([document])

    assert len(proposals)==1
    proposal=proposals[0]
    assert proposal['proposal_type']=='payment_material_extension'
    assert proposal['result_origin']=='SYSTEM'
    assert proposal['default_selected'] is False
    assert proposal['fact_ids']==[next(
        fact['fact_id'] for fact in facts if fact['fact_kind']=='payment_physical'
    )]
    assert proposal['payload']['rows']==[{
        'material_code':'MWV101145',
        'product_name':'薇武士 IP17 PRO MAX',
        'spec_model':'',
        'stable_line_key':proposal['payload']['rows'][0]['stable_line_key'],
        'source_doc_no':'1841364722',
        'package_identity':'waybill:1841364722',
        'package_count':'1',
        'gross_weight_kg':'42.05',
        'volume_m3':'0.01518',
        'chargeable_weight_kg':'46',
    }]
    normalized=normalize_source_review_proposals(
        proposals,[_items()[0]],[document],
        transport_mode='AIR',
        trusted_system_proposal_ids={proposal['proposal_id']},
    )
    assert len(normalized)==1
    assert normalized[0]['proposal_type']=='payment_material_extension'
    assert normalized[0]['payload']['rows'][0]['material_code']=='MWV101145'


def test_same_shipment_payment_material_can_extend_scope_without_physical_fields():
    line={
        **_line(13,'1841364722','MWV101145'),
        'material_code':'MWV101145','product_name':'薇武士 IP17 PRO MAX',
        'packing':{'material_code_hints':['MWV101145']},
    }
    facts=build_payment_facts(
        [_items()[0]],{'id':'PAYMENT','instance':'PAY-1','approval_no':'PAY-1'},
        [line],shipment_identifiers={'1841364722'},
    )
    document={'document_id':'DOC-EXT','source_ref':{'source_id':'PAYMENT'},
              'semantic_facts':facts}

    proposals=build_payment_material_extension_proposals([document])

    assert len(proposals)==1
    assert proposals[0]['payload']['rows'][0]['material_code']=='MWV101145'
    assert not any(
        field in proposals[0]['payload']['rows'][0]
        for field in ('gross_weight_kg','volume_m3','package_count')
    )


def _normalized(mode: str = "AIR", *, existing_fees: list[dict] | None = None):
    document = _document()
    raw = build_semantic_payment_fee_proposals(document, transport_mode=mode)
    trusted = {row["proposal_id"] for row in raw}
    normalized = normalize_source_review_proposals(
        raw,
        _items(),
        [document],
        existing_fees=existing_fees,
        transport_mode=mode,
        trusted_system_proposal_ids=trusted,
    )
    return document, raw, normalized


def test_payment_total_is_the_only_applicable_fee_and_components_are_read_only() -> None:
    document, raw, normalized = _normalized("AIR")
    raw_by_role = {row["selection_role"]: row for row in raw if row["selection_role"] != "component"}
    primary = raw_by_role["primary_total"]
    raw_components = [row for row in raw if row["selection_role"] == "component"]

    assert primary["payload"] == {
        "logical_fee_key": "international_air_freight",
        "expense_category": "国际空运费",
        "amount_status": "ACTUAL",
        "amount": "6828.38",
        "currency": "RMB",
        "scope_type": "ALL_ITEMS",
        "allocation_basis": "gross_weight",
        "remark": "同一已选付款工作簿内、同币种的本批次运费行只汇总一次。",
    }
    assert primary["fact_ids"] == [
        next(
            fact["fact_id"]
            for fact in document["semantic_facts"]
            if fact["fact_kind"] == "payment_freight_total"
        )
    ]
    assert {ref["row"] for ref in primary["source_refs"]} == {13, 14}
    assert len(raw_components) == 2
    assert [row["payload"]["amount"] for row in raw_components] == ["3414.19", "3414.19"]
    assert all(row["parent_proposal_id"] == primary["proposal_id"] for row in raw_components)
    assert all(row["default_selected"] is False for row in raw_components)

    total_fact = next(
        fact for fact in document["semantic_facts"]
        if fact["fact_kind"] == "payment_freight_total"
    )
    component_facts = [
        fact for fact in document["semantic_facts"]
        if fact["fact_kind"] == "payment_freight_component"
    ]
    assert total_fact["allowed_actions"] == [{
        "action": "fee_update",
        "logical_fee_key": "international_air_freight",
        "amount": "6828.38",
        "currency": "RMB",
    }]
    assert all(fact["allowed_actions"] == [] for fact in component_facts)

    by_role = {
        "primary": next(row for row in normalized if row["selection_role"] == "primary_total"),
        "components": [row for row in normalized if row["selection_role"] == "component"],
    }
    assert by_role["primary"]["default_selected"] is True
    assert by_role["primary"]["recommended"] is True
    assert len(by_role["components"]) == 2
    decorated = material_ai_fee_policy.decorate(normalized, [], {})
    assert sum(row["can_apply"] for row in decorated) == 1
    assert next(row for row in decorated if row["can_apply"])["payload"]["amount"] == "6828.38"


@pytest.mark.parametrize("mode", ["AIR", "EXPRESS", "SEA", "AIR_DDP", "SEA_STANDARD"])
def test_payment_total_uses_the_transport_service_primary_definition(mode: str) -> None:
    from overseas_costing.services.transport_fee_service import primary_freight_definition

    _document_value, raw, _normalized_value = _normalized(mode)
    primary = next(row for row in raw if row["selection_role"] == "primary_total")

    assert {
        key: primary["payload"][key]
        for key in ("logical_fee_key", "expense_category", "allocation_basis")
    } == primary_freight_definition(mode)


def test_existing_editable_fee_conflicts_and_final_or_disabled_fee_blocks_application() -> None:
    existing = [{
        "logical_fee_key": "international_air_freight",
        "amount": "6000",
        "currency": "RMB",
        "amount_status": "ESTIMATED",
    }]
    _document_value, _raw, normalized = _normalized("AIR", existing_fees=existing)
    primary = next(row for row in normalized if row["selection_role"] == "primary_total")
    assert primary["conflict"] is True
    assert primary["default_selected"] is False
    assert primary["recommended"] is False

    _clean_document, _clean_raw, unblocked = _normalized("AIR")
    for blocking in (
        [{**existing[0], "amount_status": "ACTUAL"}],
        [{**existing[0], "is_enabled": 0, "is_active": 0}],
    ):
        decorated = material_ai_fee_policy.decorate(unblocked, blocking, {})
        blocked_primary = next(row for row in decorated if row["selection_role"] == "primary_total")
        assert blocked_primary["can_apply"] is False
        assert blocked_primary["default_selected"] is False
        assert blocked_primary["recommended"] is False


def test_actual_scope_blocking_ignores_only_inactive_different_key_nonfinal_fees() -> None:
    _document_value, _raw, normalized = _normalized("AIR")

    inactive_other = {
        "logical_fee_key": "international_express_fee",
        "amount": "6000",
        "currency": "RMB",
        "amount_status": "ACTUAL",
        "is_enabled": 0,
        "is_active": 0,
    }
    allowed = material_ai_fee_policy.decorate(normalized, [inactive_other], {})
    allowed_primary = next(row for row in allowed if row["selection_role"] == "primary_total")
    assert allowed_primary["can_apply"] is True
    assert allowed_primary["default_selected"] is True

    for blocking in (
        {**inactive_other, "is_enabled": 1, "is_active": 1},
        {**inactive_other, "is_final": 1},
        {**inactive_other, "logical_fee_key": "international_air_freight"},
    ):
        decorated = material_ai_fee_policy.decorate(normalized, [blocking], {})
        primary = next(row for row in decorated if row["selection_role"] == "primary_total")
        assert primary["can_apply"] is False
        assert primary["default_selected"] is False


def test_authoritative_payment_total_outranks_lower_logistics_quote() -> None:
    from overseas_costing.tests.test_material_ai_fill_service import _fee_document, _review_fee

    payment = _document()
    raw = build_semantic_payment_fee_proposals(payment, transport_mode="AIR")
    logistics = _fee_document("DOC-LOG", "国际空运费 RMB 6000")
    logistics["source_ref"].update(
        source_id="LOG-1",
        process_instance_id="LOG-1",
        workflow_stage="international_logistics",
        workflow_rank=1,
        evidence_kind="approval_form",
        evidence_rank=1,
    )
    logistics_quote = _review_fee(
        "LOGISTICS", "6000", "international_air_freight", "DOC-LOG", 1
    )

    normalized = normalize_source_review_proposals(
        [*raw, logistics_quote],
        _items(),
        [payment, logistics],
        transport_mode="AIR",
        trusted_system_proposal_ids={row["proposal_id"] for row in raw},
    )
    primary = next(row for row in normalized if row["selection_role"] == "primary_total")
    lower = next(row for row in normalized if row["proposal_id"] == "LOGISTICS")

    assert primary["workflow_stage"] == "payment"
    assert primary["default_selected"] is True
    assert lower["default_selected"] is False


def test_forged_component_selection_is_rejected_at_fee_policy_boundary() -> None:
    _document_value, _raw, normalized = _normalized("AIR")
    component = next(row for row in normalized if row["selection_role"] == "component")

    with pytest.raises(ValueError, match="只读"):
        material_ai_fee_policy.assert_allowed([component], [], {})


def test_component_fact_binding_rejects_a_changed_parent_relation() -> None:
    document = _document()
    raw = build_semantic_payment_fee_proposals(document, transport_mode="AIR")
    component = next(row for row in raw if row["selection_role"] == "component")
    component["parent_proposal_id"] = "forged-parent"

    normalized = normalize_source_review_proposals(
        [component],
        _items(),
        [document],
        transport_mode="AIR",
        trusted_system_proposal_ids={component["proposal_id"]},
    )

    assert normalized == []


def test_duplicate_semantic_facts_do_not_create_a_second_total_or_component() -> None:
    document = _document()
    document["semantic_facts"].extend(deepcopy(document["semantic_facts"]))

    raw = build_semantic_payment_fee_proposals(document, transport_mode="AIR")

    assert sum(row["selection_role"] == "primary_total" for row in raw) == 1
    assert sum(row["selection_role"] == "component" for row in raw) == 2
    assert sum(
        row["payload"]["amount"] == "6828.38" and row["payload"]["amount_status"] == "ACTUAL"
        for row in raw
    ) == 1


def test_unsupported_transport_mode_keeps_total_visible_but_read_only() -> None:
    document = _document()

    raw = build_semantic_payment_fee_proposals(document, transport_mode="RAIL")
    total = next(row for row in raw if row["selection_role"] == "alternative")
    total_fact = next(
        fact for fact in document["semantic_facts"]
        if fact["fact_kind"] == "payment_freight_total"
    )

    assert total["payload"]["amount"] == "6828.38"
    assert total["can_apply"] is False
    assert total["default_selected"] is False
    assert "运输方式" in total["blocked_reason"]
    assert total_fact["allowed_actions"] == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda row: row["payload"].update(amount="6828.39"),
        lambda row: row["payload"].update(logical_fee_key="international_express_fee"),
        lambda row: row["source_refs"].__setitem__(0, {**row["source_refs"][0], "row": 99}),
    ],
)
def test_total_fact_binding_rejects_changed_value_key_or_provenance(tamper) -> None:
    document = _document()
    raw = build_semantic_payment_fee_proposals(document, transport_mode="AIR")
    primary = next(row for row in raw if row["selection_role"] == "primary_total")
    tampered = deepcopy(primary)
    tamper(tampered)

    normalized = normalize_source_review_proposals(
        [tampered],
        _items(),
        [document],
        transport_mode="AIR",
        trusted_system_proposal_ids={tampered["proposal_id"]},
    )

    assert normalized == []


def test_deepseek_duplicate_must_repeat_the_exact_action_and_all_component_refs() -> None:
    document = _document()
    document["semantic_rows"] = [
        {
            "sheet": "DHL快递",
            "source_row": row,
            "cells": [{"cell": f"K{row}", "value": value}],
        }
        for row, value in (
            (13, "RMB 3414.19"),
            (14, "RMB 3414.19"),
            (15, "总计 RMB 6828.38"),
        )
    ]
    raw = build_semantic_payment_fee_proposals(document, transport_mode="AIR")
    primary = next(row for row in raw if row["selection_role"] == "primary_total")

    altered_refs = deepcopy(primary)
    altered_refs.update(proposal_id="MODEL-ALTERED-REF")
    altered_refs.pop("selection_role")
    altered_refs["source_refs"] = [{
        "document_id": "DOC-1", "sheet": "DHL快递", "row": 15, "cell": "K15"
    }]
    forged_system_id = deepcopy(altered_refs)
    forged_system_id["proposal_id"] = primary["proposal_id"]
    omitted_semantic_claim = deepcopy(primary)
    omitted_semantic_claim.pop("selection_role")
    omitted_semantic_claim.pop("fact_ids")
    omitted_semantic_claim["payload"]["amount"] = "9999"
    omitted_semantic_claim["source_refs"] = [deepcopy(primary["source_refs"][0])]
    wrong_key = deepcopy(primary)
    wrong_key.update(proposal_id="MODEL-WRONG-KEY")
    wrong_key.pop("selection_role")
    wrong_key["payload"]["logical_fee_key"] = "international_express_fee"

    for forged, trusted_ids in (
        (altered_refs, set()),
        (wrong_key, set()),
        (forged_system_id, {primary["proposal_id"]}),
        (omitted_semantic_claim, {primary["proposal_id"]}),
    ):
        assert normalize_source_review_proposals(
            [forged],
            _items(),
            [document],
            transport_mode="AIR",
            trusted_system_proposal_ids=trusted_ids,
        ) == []

    exact_duplicate = deepcopy(primary)
    exact_duplicate.update(proposal_id="MODEL-EXACT")
    exact_duplicate.pop("selection_role")
    normalized = normalize_source_review_proposals(
        [primary, exact_duplicate],
        _items(),
        [document],
        transport_mode="AIR",
        trusted_system_proposal_ids={primary["proposal_id"]},
    )
    assert [row["proposal_id"] for row in normalized] == [primary["proposal_id"]]


def test_unified_worker_injects_semantic_payment_fees_without_deepseek(monkeypatch) -> None:
    from overseas_costing.services import material_ai_fill_service as service
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository

    repository = _LifecycleRepository(status="QUEUED")
    repository.sources = [{
        "source_kind": "manual_attachment",
        "source_id": "PAYMENT",
        "logical_source_id": "PAYMENT",
        "source_hash": "payment-hash",
        "source_label": "DHL-monthly.xlsx",
    }]
    repository.get_context = lambda _batch, _version: {
        "batch": "B1",
        "version": "V1",
        "batch_modified": "M1",
        "transport_mode": "AIR",
        "effective_source": {},
    }
    manifest = prepare_source_manifest(repository.sources)
    context = repository.get_context("B1", "V1")
    repository.run.update(
        proposal_version=1,
        source_manifest_json=manifest,
        input_fingerprint=service._source_review_fingerprint(
            "B1", "V1", repository.get_items("B1", "V1"), manifest, "", context=context
        ),
    )
    document = _document()
    document.pop("document_id")
    deepseek_calls = []
    monkeypatch.setattr(service, "_read_source", lambda *_args, **_kwargs: ([], deepcopy(document)))
    monkeypatch.setattr(
        service,
        "_call_source_review_ai",
        lambda *_args, **_kwargs: deepseek_calls.append(True) or {
            "ok": True,
            "proposals": [],
            "evidence_documents": [],
            "warning": "",
        },
    )

    result = service.execute_material_ai_fill("RUN-1", repository=repository)

    assert result["status"] in {"READY", "READY_WITH_WARNINGS"}, repository.run.get("error_message")
    fees = [
        row for row in repository.run["candidates_json"]
        if row.get("proposal_type") == "fee_update"
    ]
    assert deepseek_calls == [True]
    assert len(fees) == 3
    assert sum(row["selection_role"] == "primary_total" for row in fees) == 1
    assert sum(row["selection_role"] == "component" for row in fees) == 2
    assert next(row for row in fees if row["selection_role"] == "primary_total")["payload"]["amount"] == "6828.38"


def test_unified_worker_keeps_payment_total_when_an_approved_logistics_quote_has_same_key(
    monkeypatch,
) -> None:
    from overseas_costing.services import material_ai_fill_service as service
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository

    repository = _LifecycleRepository(status="QUEUED")
    repository.sources = [
        {
            "source_kind": "manual_attachment",
            "source_id": "PAYMENT",
            "logical_source_id": "PAYMENT",
            "source_hash": "payment-hash",
            "source_label": "DHL-monthly.xlsx",
        },
        {
            "source_kind": "approval_form",
            "source_id": "approval:LOG-1:form",
            "source_hash": "logistics-hash",
            "source_label": "国际物流审批正文",
            "process_instance_id": "LOG-1",
            "approval_role": "international_logistics",
            "form_fields": {"物流报价Cotización de logística": "DHL报价，6000元"},
            "approval_decisions": [
                {
                    "operation_result": "AGREE",
                    "remark": "走DHL",
                    "operation_time": "2026-09-15",
                }
            ],
        },
    ]
    repository.get_context = lambda _batch, _version: {
        "batch": "B1",
        "version": "V1",
        "batch_modified": "M1",
        "transport_mode": "AIR",
        "effective_source": {},
    }
    manifest = prepare_source_manifest(repository.sources)
    context = repository.get_context("B1", "V1")
    repository.run.update(
        proposal_version=1,
        source_manifest_json=manifest,
        input_fingerprint=service._source_review_fingerprint(
            "B1", "V1", repository.get_items("B1", "V1"), manifest, "", context=context
        ),
    )
    payment_document = _document()
    payment_document.pop("document_id")

    def read_source(_items_value, source, **_kwargs):
        if source.get("source_kind") != "approval_form":
            return [], deepcopy(payment_document)
        return [], {
            "source_ref": {
                "source": "approval_form",
                "source_id": source["source_id"],
                "process_instance_id": source["process_instance_id"],
                "workflow_stage": "international_logistics",
                "evidence_kind": "approval_form",
            },
            "form_fields": deepcopy(source["form_fields"]),
            "text": "DHL报价，6000元",
            "ai_eligible": False,
        }

    monkeypatch.setattr(service, "_read_source", read_source)
    monkeypatch.setattr(
        service,
        "_call_source_review_ai",
        lambda *_args, **_kwargs: {
            "ok": True,
            "proposals": [],
            "evidence_documents": [],
            "warning": "",
        },
    )

    result = service.execute_material_ai_fill("RUN-1", repository=repository)

    assert result["status"] in {"READY", "READY_WITH_WARNINGS"}, repository.run.get("error_message")
    fees = [
        row for row in repository.run["candidates_json"]
        if row.get("proposal_type") == "fee_update"
    ]
    primary = next(row for row in fees if row.get("selection_role") == "primary_total")
    approved = next(row for row in fees if row.get("approved_carrier"))
    assert primary["payload"]["amount"] == "6828.38"
    assert primary["default_selected"] is True
    assert approved["payload"]["amount"] == "6000"
    assert approved["default_selected"] is False


def test_selection_confirmation_contains_the_total_exactly_once() -> None:
    from overseas_costing.services import material_ai_fill_service as service
    from overseas_costing.services import material_ai_selection_service as selection
    from overseas_costing.tests.test_ai_selection_service import Repo, confirm, prepare

    repo = Repo()
    repo.context["transport_mode"] = "AIR"
    _document_value, _raw, normalized = _normalized("AIR")
    candidates = material_ai_fee_policy.decorate(normalized, [], repo.context["effective_source"])
    repo.run["candidates_json"] = candidates
    repo.run["input_fingerprint"] = service._source_review_fingerprint(
        "B1", "V1", repo.items, repo.sources, "", context=repo.context
    )
    repo.run["draft_json"]["material_input_fingerprint"] = selection.material_fingerprint(
        repo.items, repo.sources, repo.context
    )
    primary_id = next(
        row["proposal_id"] for row in candidates
        if row["selection_role"] == "primary_total"
    )

    with pytest.raises(ValueError, match="重复"):
        prepare(repo, ids=[], fees=[primary_id, primary_id])

    preview = prepare(repo, ids=[], fees=[primary_id])
    assert [row["payload"]["amount"] for row in preview["fees"]] == ["6828.38"]
    assert confirm(repo, preview)["ok"] is True
    assert confirm(repo, preview)["ok"] is True
    assert len(repo.writes) == 1
    assert [row["payload"]["amount"] for row in repo.writes[0]["fees"]] == ["6828.38"]
