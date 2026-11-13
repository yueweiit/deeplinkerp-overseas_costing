"""Aggregate real intake facts through source review and the row writer."""

from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from overseas_costing.services.logistics_cargo_value import aggregate_cargo_valuation
from overseas_costing.services.logistics_settlement.initial_intake import project_initial_logistics_items
from overseas_costing.services.purchase_value_evidence import META_KEY, purchase_target_matches
from overseas_costing.services.shipment_cost_service import object_json, shipment_value
from overseas_costing.tests.test_logistics_initial_intake import source as intake_source


def real_intake(amounts=("10", "20"), quantities=(2, 3), currencies=None, fx_rates=None):
    template = intake_source()["raw"]["formComponentValues"][1]["value"][0]
    raw_rows = []
    for index, (amount, quantity) in enumerate(zip(amounts, quantities), 1):
        row = deepcopy(template)
        row["rowId"] = "line-" + str(index)
        row["rowValue"][3]["value"] = str(quantity)
        row["rowValue"][5]["value"] = amount
        row["rowValue"][6]["value"] = currencies[index - 1] if currencies else "人民币RMB"
        raw_rows.append(row)
    archive = intake_source(rows=raw_rows)
    archive["approval_no"] = "FIXTURE-OA"
    projected = project_initial_logistics_items(archive, fx_rates=fx_rates)
    assert not projected["issues"]
    members = projected["rows"]
    for index, member in enumerate(members, 1):
        member.update(name="I" + str(index), stable_line_key=None, unit_price="0",
                      actual_shipped_qty=0, actual_shipped_qty_mode="DEFAULT_PURCHASE")
        member["extra_json"][META_KEY]["target_identity"]["line_key"] = "legacy:" + member["name"]
        member["extra_json"] = json.dumps(member["extra_json"])
    source = {"source_id": archive["id"], "approval_role": "international_logistics",
              "source_kind": "approval_form", "approval_no": archive["approval_no"],
              "workflow_stage": "international_logistics", "form_fields": archive["fields"]}
    return source, members


def aggregate_row(members):
    row = deepcopy(members[0])
    quantity = sum(Decimal(str(object_json(member["extra_json"])[META_KEY]["quantity"])) for member in members)
    row.update(quantity=str(quantity), actual_shipped_qty=str(quantity), shipped_uom="套",
               actual_shipped_qty_mode="EXPLICIT_SOURCE", stable_line_key="logistics:aggregate")
    return row


def test_real_legacy_intake_facts_sum_to_30_not_anchor_proration_25():
    _, members = real_intake()
    row = aggregate_row(members)
    before = deepcopy((members, row))
    assert shipment_value(row)["error"] == "PURCHASE_VALUE_IDENTITY_MISMATCH"
    result = aggregate_cargo_valuation(members, row)
    assert result["ok"], result
    assert Decimal(result["goods_value"]) == Decimal(30)
    assert result["fact"]["target_identity"]["line_key"] == row["stable_line_key"]
    assert [ref["source_row_id"] for ref in result["fact"]["source_refs"]] == ["line-1", "line-2"]
    assert [component["fact"]["amount_rmb"] for component in result["fact"]["components"]] == ["10", "20"]
    assert result["metadata"]["purchase_value_history"][0] == object_json(row["extra_json"])[META_KEY]
    assert purchase_target_matches(row, result["fact"])
    assert Decimal(shipment_value({**row, "extra_json": result["metadata"]})["amount_rmb"]) == 30
    assert (members, row) == before


def test_one_member_rebinds_legacy_identity_without_wrapping_or_averaging():
    _, members = real_intake(amounts=("10",), quantities=(2,))
    row = aggregate_row(members)
    result = aggregate_cargo_valuation(members, row)
    assert result["ok"], result
    assert result["fact"]["target_identity"]["line_key"] == "logistics:aggregate"
    assert "components" not in result["fact"]
    assert result["fact"]["original_amount"] == "10"
    saved = {**row, "extra_json": result["metadata"], "goods_value": result["goods_value"]}
    repeated = aggregate_cargo_valuation([saved], saved)
    assert repeated == result


def test_autoexcluded_default_purchase_uses_source_quantity_not_stored_zero():
    _, members = real_intake()
    row = aggregate_row(members)
    for member in members:
        member.update(is_excluded=1, excluded_by="system", quantity=0, actual_shipped_qty=0)
    before = deepcopy(members)
    result = aggregate_cargo_valuation(members, row)
    assert result["ok"] and Decimal(result["goods_value"]) == 30
    assert members == before


def test_fact_fx_snapshots_and_original_amounts_are_preserved():
    _, members = real_intake(currencies=("USD", "MXN"), fx_rates={"USD": "7", "MXN": "0.5"})
    result = aggregate_cargo_valuation(members, aggregate_row(members), fx_rates={"USD": "999"})
    assert result["ok"] and Decimal(result["goods_value"]) == 80
    assert [part["fact"]["currency"] for part in result["fact"]["components"]] == ["USD", "MXN"]
    assert [part["fact"]["original_amount"] for part in result["fact"]["components"]] == ["10", "20"]
    assert all(member["unit_price"] == "0" for member in members)


def test_explicit_zero_fact_is_valid_but_a_missing_fact_never_becomes_zero():
    _, members = real_intake(amounts=("0", "0"))
    row = aggregate_row(members)
    result = aggregate_cargo_valuation(members, row)
    assert result["ok"] and Decimal(result["goods_value"]) == 0
    assert result["fact"]["explicit_zero"]
    meta = object_json(members[1]["extra_json"])
    meta.pop(META_KEY)
    members[1]["extra_json"] = meta
    assert aggregate_cargo_valuation(members, row)["reason"] == "CARGO_VALUE_FACT_REQUIRED"


@pytest.mark.parametrize("change,reason", [
    ("quantity", "CARGO_VALUE_QUANTITY_MISMATCH"),
    ("unit", "PURCHASE_VALUE_UOM_MISMATCH"),
    ("identity", "PURCHASE_VALUE_IDENTITY_MISMATCH"),
    ("stale_fact", "PURCHASE_VALUE_IDENTITY_MISMATCH"),
    ("overlap", "CARGO_VALUE_SOURCE_OVERLAP"),
    ("manual", "CARGO_VALUE_MANUAL_PROTECTED"),
])
def test_unsafe_member_or_target_tuples_block_without_mutation(change, reason):
    _, members = real_intake()
    row = aggregate_row(members)
    if change == "quantity":
        row["actual_shipped_qty"] = "6"
    elif change == "unit":
        row["shipped_uom"] = "kg"
    elif change == "identity":
        row["material_code"] = "OTHER"
    elif change == "stale_fact":
        members[1]["stable_line_key"] = "changed"
    else:
        meta = object_json(members[1]["extra_json"])
        if change == "overlap":
            meta[META_KEY]["source_refs"] = deepcopy(object_json(members[0]["extra_json"])[META_KEY]["source_refs"])
        else:
            meta["manual_shipment_valuation"] = {"manual": True, "confirmed": True}
        members[1]["extra_json"] = meta
    before = deepcopy((members, row))
    result = aggregate_cargo_valuation(members, row)
    assert not result["ok"] and result["reason"] == reason
    assert (members, row) == before


@pytest.mark.parametrize("count,excluded", [(1, False), (2, False), (2, True)])
def test_real_intake_reconciliation_catalog_and_writer_bind_final_stable_identity(count, excluded):
    from overseas_costing.services.logistics_autofill_service import AUTO_SCOPE_EXCLUSION_REASON, build_logistics_reconciliation
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.material_ai_row_selection import catalog, project
    from overseas_costing.services.material_ai_selection_writer import write_rows
    from overseas_costing.tests.test_settlement_writer import Ledger

    source, members = real_intake(amounts=("10", "20")[:count], quantities=(2, 3)[:count])
    store = Store.sqlite(sqlite3.connect(":memory:"))
    store.install()
    ledger = Ledger(store)
    batch = ledger.create("batch", {"status": "Dirty", "confirm_status": "Pending"})
    version = ledger.create("version", {"batch": batch["name"], "status": "Active"})
    ledger.put("batch", batch["name"], {"current_version": version["name"]})
    for member in members:
        saved = ledger.create("item", {**member, "batch": batch["name"], "version": version["name"]})
        meta = object_json(saved["extra_json"])
        meta[META_KEY]["target_identity"]["line_key"] = "legacy:" + saved["name"]
        values = {"extra_json": json.dumps(meta)}
        if excluded:
            values.update(is_excluded=1, excluded_by="system", exclusion_reason=AUTO_SCOPE_EXCLUSION_REASON)
        ledger.put("item", saved["name"], values)
    members = ledger.rows("item", batch=batch["name"])
    proposal = build_logistics_reconciliation(members, source)
    assert not proposal.get("blocked"), proposal.get("reason")
    aggregate, = proposal["payload"]["rows"]
    expected = Decimal(10 if count == 1 else 30)
    assert Decimal(shipment_value(aggregate)["amount_rmb"]) == expected
    choices = catalog(members, [proposal], [], {}, run_id="RUN", sources=[source])
    fields = {f"{candidate['item_name']}:{candidate['fieldname']}": candidate["candidate_id"]
              for candidate in choices["field_candidates"]
              if candidate["fieldname"] in {"actual_shipped_qty", "shipped_uom"} and candidate.get("can_apply")}
    assert fields
    preview = project(members, choices, [], [], "update_selected", field_choices=fields)
    preview.update(batch=batch["name"], version=version["name"], id="PREVIEW", revision="REV",
                   run_id="RUN", source_context={})
    write_rows(store, ledger, preview, {})
    saved = ledger.rows("item", batch=batch["name"])
    active = [row for row in saved if not row.get("is_excluded")]
    assert len(active) == 1
    row = active[0]
    fact = object_json(row["extra_json"])[META_KEY]
    assert row["stable_line_key"] and purchase_target_matches(row, fact)
    assert Decimal(shipment_value(row)["amount_rmb"]) == expected
    assert Decimal(row["goods_value"]) == expected
    assert row["unit_price"] == "0"
    assert len(fact["source_refs"]) == count
    if count == 2:
        assert any(change["fieldname"] == "goods_value" for change in preview["changes"])
