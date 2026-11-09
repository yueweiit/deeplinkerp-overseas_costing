"""Real-source and persistence regressions for per-row AI adoption."""
import json

from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services.material_ai_fill_service import _projection_candidates
from overseas_costing.services.effective_logistics_source import load_source_bundle, project_ai_items
from overseas_costing.services.effective_source_values import project_source_values
from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
from overseas_costing.services.material_ai_selection_writer import write_rows
from overseas_costing.services.shipment_cost_service import shipment_value
from overseas_costing.tests.test_logistics_autofill import approval, existing_items
from overseas_costing.tests.test_settlement_writer import setup as settlement_fixture


def preview(items, proposals, batch, version, *, mode, source_context=None):
    catalog = rows.catalog(items, proposals, [], {}, run_id="RUN")
    chosen = [row["row_id"] for row in catalog["rows"]
              if row["origin"] == ("source" if proposals else "current")]
    result = rows.project(items, catalog, chosen, [], mode)
    result.update(batch=batch["name"], version=version["name"], id="PREVIEW",
                  revision="REVISION", run_id="RUN", source_context=source_context or {})
    return result


def test_reconciliation_source_rows_do_not_claim_old_physical_values():
    items = existing_items()
    for item in items:
        item.update(gross_weight_kg=90, volume_m3=12)
    proposal = build_logistics_reconciliation(items, approval())
    catalog = rows.catalog(items, [proposal], [], {}, run_id="RUN")
    recognized = [row for row in catalog["rows"] if row["origin"] == "source"]

    # The approval has no per-row packing measurements. Inherited/apportioned
    # old values must never become new source evidence in the row catalog.
    assert recognized
    assert all(row["values"].get("gross_weight_kg") is None for row in recognized)
    assert all(row["values"].get("volume_m3") is None for row in recognized)


def test_fill_one_field_keeps_untouched_absent_numbers_missing():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "gross_weight_kg": None, "volume_m3": None,
        "actual_shipped_qty": 2, "shipped_uom": "件", "extra_json": "{}",
    })
    proposal = {"proposal_id": "P", "proposal_type": "item_update",
                "target_item_name": item["name"], "payload": {"fields": {"volume_m3": 2}}}
    selected = preview([item], [proposal], batch, version, mode="fill_missing")

    write_rows(store, ledger, selected, {})
    saved = ledger.get("item", item["name"])

    assert saved["volume_m3"] == 2
    assert rows.missing(saved, "gross_weight_kg"), (
        "Filling volume must not turn the absent weight into an unmarked real zero"
    )


def test_replacement_of_legacy_bound_version_keeps_future_source_row_scope():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    original = load_source_bundle(batch["name"], version["name"], store=store, ledger=ledger)
    assert original["binding"]
    selected = preview([item], [], batch, version, mode="replace_all",
                       source_context=original["context"])

    new_version = write_rows(store, ledger, selected, {})
    bundle = load_source_bundle(batch["name"], new_version, store=store, ledger=ledger)

    assert json.loads(bundle["version"]["extra_json"])["ai_row_adoption"]["id"] == "PREVIEW"
    scope = (bundle["context"].get("packing") or bundle["context"]).get("selected_source")
    assert scope and scope["id"] == "PREVIEW"
    assert len(bundle["source"]["goods"]) == 1
    assert bundle["source"]["goods"][0]["material_code"] == item["material_code"]
    original = load_source_bundle(batch["name"], new_version, store=store, ledger=ledger,
                                  apply_ai_row_adoption=False)
    assert not (original["context"].get("packing") or original["context"]).get("selected_source")


def test_filled_shipment_quantity_refreshes_the_adopted_cargo_value():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    metadata = {
        "settlement_packing_missing": ["actual_shipped_qty", "quantity"],
        "settlement_cargo": {"material_code": "A", "quantity": None, "unit": "件",
                             "source_snapshot": "OLD", "binding_id": "OLD", "line_key": "L"},
        "settlement_valuation": {"error": "SETTLEMENT_QUANTITY_OR_PRICE_UNIT_INVALID",
                                 "fx_context": {}, "amount_rmb": None},
    }
    item = ledger.put("item", item["name"], {
        "actual_shipped_qty": 0, "shipped_uom": "件", "extra_json": json.dumps(metadata),
    })
    proposal = {"proposal_id": "P", "proposal_type": "item_update",
                "target_item_name": item["name"], "payload": {"fields": {"actual_shipped_qty": 2}}}
    selected = preview([item], [proposal], batch, version, mode="fill_missing")

    write_rows(store, ledger, selected, {})
    saved = ledger.get("item", item["name"])
    valuation = shipment_value(saved)

    assert saved["actual_shipped_qty"] == 2
    assert not valuation["error"]
    assert valuation["amount_rmb"] == "20.000000"


def test_fill_missing_persists_goods_value_for_automatic_manual_and_error_results():
    from overseas_costing.services.logistics_settlement.valuation import value_final_cargo
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    def apply_case(*, item_values, cargo_row, fieldname, value, manual_amount=None, missing_fields=()):
        store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
        base = {**item, **item_values}
        metadata = {"settlement_cargo": cargo_row, "settlement_packing_missing": list(missing_fields),
                    "settlement_valuation": value_final_cargo(base, cargo_row, {})}
        if manual_amount is not None:
            base["extra_json"] = json.dumps(metadata)
            metadata["manual_shipment_valuation"] = build_manual_shipment_valuation(
                base, manual_amount, actor="finance", confirmed_at="now")
        item = ledger.put("item", item["name"], {**item_values, "extra_json": json.dumps(metadata)})
        proposal = {"proposal_id": "P", "proposal_type": "item_update",
                    "target_item_name": item["name"], "payload": {"fields": {fieldname: value}}}
        selected = preview([item], [proposal], batch, version, mode="fill_missing")
        if selected["can_apply"]:
            write_rows(store, ledger, selected, {})
        return ledger.get("item", item["name"])

    automatic = apply_case(
        item_values={"actual_shipped_qty": 0, "shipped_uom": "件", "goods_value": 10},
        cargo_row={"material_code": "A", "quantity": 1, "unit": "件", "source_snapshot": "S"},
        fieldname="actual_shipped_qty", value=2, missing_fields=("actual_shipped_qty",),
    )
    manual = apply_case(
        item_values={"actual_shipped_qty": 2, "shipped_uom": "件", "unit_price": None,
                     "goods_value": 25},
        cargo_row={"material_code": "A", "quantity": 2, "unit": "件", "source_snapshot": "S"},
        fieldname="unit_price", value=10, manual_amount=25,
    )
    invalid = apply_case(
        item_values={"actual_shipped_qty": 2, "shipped_uom": "件", "unit_price": None,
                     "purchase_currency": None, "goods_value": 99},
        cargo_row={"material_code": "A", "quantity": 2, "unit": "件", "source_snapshot": "S"},
        fieldname="unit_price", value=10,
    )

    assert str(automatic["goods_value"]) == "20.000000"
    assert shipment_value(automatic)["status"] == "automatic"
    assert str(manual["goods_value"]) == "25"
    assert shipment_value(manual)["status"] == "manual"
    assert invalid["goods_value"] == 99  # an unverified unit price may not replace any existing value
    assert invalid.get("unit_price") is None
    assert shipment_value(invalid)["status"] == "missing"


def test_stable_row_match_does_not_verify_price_for_a_different_specification():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "product_name": "Widget", "spec_model": "old-spec", "stable_line_key": "LINE",
        "actual_shipped_qty": 2, "shipped_uom": "件",
    })
    source = {"material_code": "A", "product_name": "Widget", "spec_model": "new-spec",
              "stable_line_key": "LINE", "quantity": 2, "actual_shipped_qty": 2,
              "unit": "件", "shipped_uom": "件", "_review_origin": "source"}
    proposal = {"proposal_id": "P", "proposal_type": "logistics_reconcile",
                "payload": {"rows": [source]}}
    selected = preview([item], [proposal], batch, version, mode="replace_all")

    new_version = write_rows(store, ledger, selected, {})
    saved = ledger.rows("item", version=new_version)[0]
    valuation = shipment_value(saved)

    assert saved["spec_model"] == "new-spec"
    assert valuation["amount_rmb"] is None
    assert valuation["error"]


def test_replace_exact_match_preserves_purchase_evidence_when_value_agrees():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "spec_model": "S1", "stable_line_key": "LINE", "actual_shipped_qty": 2,
        "shipped_uom": "件", "extra_json": json.dumps({"purchase_evidence": {"document": "PUR-1"}}),
    })
    source = {"material_code": "A", "spec_model": "S1", "stable_line_key": "LINE",
              "quantity": 2, "actual_shipped_qty": 2, "unit": "件", "shipped_uom": "件",
              "_review_origin": "source"}
    proposal = {"proposal_id": "P", "proposal_type": "logistics_reconcile", "payload": {"rows": [source]}}
    selected = preview([item], [proposal], batch, version, mode="replace_all")

    new_version = write_rows(store, ledger, selected, {})
    saved = ledger.rows("item", version=new_version)[0]
    valuation = shipment_value(saved)
    metadata = json.loads(saved["extra_json"])

    assert valuation["status"] == "automatic"
    assert valuation["amount_rmb"] == "20.000000"
    assert str(saved["goods_value"]) == "20.000000"
    assert metadata["settlement_original_values"]["source_doc_no"] == "GOODS-PURCHASE"
    assert metadata["purchase_evidence"] == {"document": "PUR-1"}


def test_replace_exact_match_quarantines_disagreeing_old_and_calculated_values():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "spec_model": "S1", "stable_line_key": "LINE", "actual_shipped_qty": 2, "shipped_uom": "件",
    })
    source = {"material_code": "A", "spec_model": "S1", "stable_line_key": "LINE",
              "quantity": 3, "actual_shipped_qty": 3, "unit": "件", "shipped_uom": "件",
              "_review_origin": "source"}
    proposal = {"proposal_id": "P", "proposal_type": "logistics_reconcile", "payload": {"rows": [source]}}
    selected = preview([item], [proposal], batch, version, mode="replace_all")

    new_version = write_rows(store, ledger, selected, {})
    saved = ledger.rows("item", version=new_version)[0]
    valuation = shipment_value(saved)

    assert valuation["status"] == "conflict"
    assert valuation["amount_rmb"] is None
    assert valuation["prior_amount_rmb"] == "20"
    assert valuation["calculated_amount_rmb"] == "30.000000"
    assert valuation["prior_evidence"]["source_doc_no"] == "GOODS-PURCHASE"
    assert valuation["calculated_evidence"]["input_evidence"]["purchase_source"] == "GOODS-PURCHASE"
    assert saved["goods_value"] == 0


def test_explicit_zero_automatic_value_conflicts_with_new_positive_calculation():
    from overseas_costing.services.logistics_settlement.valuation import reconcile_replacement_value, value_final_cargo

    cargo_row = {"material_code": "A", "quantity": 2, "unit": "件", "source_snapshot": "S"}
    prior = {"material_code": "A", "unit_price": 0, "purchase_currency": "RMB",
             "purchase_uom": "件", "unit_price_uom": "件", "source_doc_no": "PUR-1"}
    prior["extra_json"] = json.dumps({"settlement_cargo": cargo_row,
                                      "settlement_valuation": value_final_cargo(prior, cargo_row, {})})
    current = {**prior, "unit_price": 10, "extra_json": "{}"}

    result = reconcile_replacement_value(current, cargo_row, {}, prior)

    assert result["status"] == "conflict"
    assert result["amount_rmb"] is None
    assert result["prior_amount_rmb"] == "0.000000"
    assert result["calculated_amount_rmb"] == "20.000000"


def test_replace_new_row_does_not_inherit_old_value_or_purchase_evidence():
    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    source = {"material_code": "NEW", "spec_model": "S2", "stable_line_key": "NEW-LINE",
              "quantity": 3, "actual_shipped_qty": 3, "unit": "件", "shipped_uom": "件",
              "_review_origin": "source"}
    proposal = {"proposal_id": "P", "proposal_type": "logistics_reconcile", "payload": {"rows": [source]}}
    selected = preview([item], [proposal], batch, version, mode="replace_all")

    new_version = write_rows(store, ledger, selected, {})
    saved = ledger.rows("item", version=new_version)[0]
    valuation = shipment_value(saved)

    assert saved["material_code"] == "NEW"
    assert saved["goods_value"] == 0
    assert valuation["status"] == "missing"
    assert valuation["amount_rmb"] is None
    assert not saved.get("source_doc_no")
    assert "settlement_original_values" not in json.loads(saved["extra_json"])


def test_replace_exact_match_keeps_valid_manual_value_above_automatic_value():
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "spec_model": "S1", "stable_line_key": "LINE", "actual_shipped_qty": 2, "shipped_uom": "件",
    })
    metadata = {"manual_shipment_valuation": build_manual_shipment_valuation(
        item, 25, actor="finance", reason="confirmed", confirmed_at="now")}
    item = ledger.put("item", item["name"], {"extra_json": json.dumps(metadata)})
    source = {"material_code": "A", "spec_model": "S1", "stable_line_key": "LINE",
              "quantity": 2, "actual_shipped_qty": 2, "unit": "件", "shipped_uom": "件",
              "_review_origin": "source"}
    proposal = {"proposal_id": "P", "proposal_type": "logistics_reconcile", "payload": {"rows": [source]}}
    selected = preview([item], [proposal], batch, version, mode="replace_all")

    new_version = write_rows(store, ledger, selected, {})
    saved = ledger.rows("item", version=new_version)[0]

    assert shipment_value(saved)["status"] == "manual"
    assert shipment_value(saved)["amount_rmb"] == "25"
    assert saved["goods_value"] == "25"


def test_two_consecutive_replace_all_runs_keep_current_values_conflicts_and_version_history():
    from copy import deepcopy
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    store, ledger, batch, version, first, *_ = settlement_fixture.__wrapped__()
    common = {"spec_model": "S1", "unit": "件", "actual_shipped_qty": 2, "shipped_uom": "件",
              "quantity": 2, "unit_price": 10, "goods_value": 20, "purchase_currency": "RMB",
              "purchase_uom": "件", "unit_price_uom": "件", "source_doc_no": "PUR"}
    first = ledger.put("item", first["name"], {**common, "material_code": "A", "stable_line_key": "LINE-A"})
    manual_row = {**common, "material_code": "B", "stable_line_key": "LINE-B"}
    manual_row["extra_json"] = json.dumps({"manual_shipment_valuation": build_manual_shipment_valuation(
        manual_row, 25, actor="finance", reason="confirmed", confirmed_at="now")})
    second = ledger.create("item", {**manual_row, "batch": batch["name"], "version": version["name"]})
    third = ledger.create("item", {**common, "material_code": "C", "stable_line_key": "LINE-C",
                                   "batch": batch["name"], "version": version["name"]})

    def replace(current, current_version, run_id):
        source_rows = [
            {"material_code": row["material_code"], "spec_model": "S1", "stable_line_key": row["stable_line_key"],
             "quantity": 3 if row["material_code"] == "C" else 2,
             "actual_shipped_qty": 3 if row["material_code"] == "C" else 2,
             "unit": "件", "shipped_uom": "件", "_review_origin": "source"}
            for row in current
        ]
        proposal = {"proposal_id": "P-" + run_id, "proposal_type": "logistics_reconcile",
                    "default_selected": True, "payload": {"rows": source_rows}}
        catalog = rows.catalog(current, [proposal], [], {}, run_id=run_id)
        selected_ids = [row["row_id"] for row in catalog["rows"] if row["origin"] == "source"]
        projected = rows.project(current, catalog, selected_ids, [], "replace_all")
        assert projected["mode"] == "replace_all"
        projected.update(batch=batch["name"], version=current_version, id="PREVIEW-" + run_id,
                         revision="REV-" + run_id, run_id=run_id, source_context={}, sources=[])
        return write_rows(store, ledger, projected, {})

    original_items = deepcopy(ledger.rows("item", version=version["name"]))
    first_version = replace([first, second, third], version["name"], "ONE")
    first_saved = deepcopy(ledger.rows("item", version=first_version))
    second_version = replace(first_saved, first_version, "TWO")
    final = {row["material_code"]: row for row in ledger.rows("item", version=second_version)}

    assert first_version != version["name"] and second_version != first_version
    assert ledger.get("batch", batch["name"])["current_version"] == second_version
    assert ledger.rows("item", version=version["name"]) == original_items
    assert ledger.rows("item", version=first_version) == first_saved
    assert shipment_value(final["A"])["status"] == "automatic"
    assert shipment_value(final["A"])["amount_rmb"] == "20.000000"
    assert shipment_value(final["B"])["status"] == "manual"
    assert shipment_value(final["B"])["amount_rmb"] == "25"
    assert final["B"]["goods_value"] == "25"
    manual_cleared = deepcopy(final["B"])
    cleared_metadata = json.loads(manual_cleared["extra_json"])
    cleared_metadata.pop("manual_shipment_valuation")
    manual_cleared["extra_json"] = json.dumps(cleared_metadata)
    assert shipment_value(manual_cleared)["status"] == "automatic"
    assert shipment_value(manual_cleared)["amount_rmb"] == "20.000000"
    conflict = shipment_value(final["C"])
    assert conflict["status"] == "conflict"
    assert conflict["amount_rmb"] is None and final["C"]["goods_value"] == 0
    assert conflict["prior_amount_rmb"] == "20"
    assert conflict["calculated_amount_rmb"] == "30.000000"


def test_forced_second_source_review_uses_latest_version_and_refreshed_purchase_value(monkeypatch):
    from copy import deepcopy

    from overseas_costing.services import material_ai_fill_service as ai
    from overseas_costing.services import material_ai_selection_service as selection
    from overseas_costing.services.logistics_purchase_facts_service import enrich_logistics_purchase_facts

    store, ledger, batch, version, item, *_ = settlement_fixture.__wrapped__()
    item = ledger.put("item", item["name"], {
        "spec_model": "S1", "stable_line_key": "LINE-A", "actual_shipped_qty": 2,
        "shipped_uom": "件", "unit": "件", "purchase_uom": "件", "unit_price_uom": "件",
        "source_type": "OA_LOGISTICS_ROW",
    })
    source = {
        "source_kind": "approval_form", "source_id": "approval:REFRESH:form",
        "source_hash": "SOURCE-1", "approval_role": "international_logistics",
        "approval_no": "LOG-1", "source_label": "国际物流审批正文",
        "form_fields": {"货物信息Bienes": [{"物料编码": "A", "物料名称": "A", "规格型号": "S1",
                                            "数量": 2, "单位": "件"}]},
    }
    purchase_source = {
        "source_kind": "approval_form", "source_id": "SOURCE-1", "source_hash": "PURCHASE-HASH-1",
        "logical_source_id": "PURCHASE-LOGICAL-1", "approval_role": "purchase",
        "approval_no": "PURCHASE-1", "selected": True,
        "form_fields": {"币种Moneda": "RMB", "采购明细": [{"rowNumber": "stable-line-1", "rowValue": [
            {"label": "物品编码Código", "value": "A"}, {"label": "数量Cantidad", "value": 2},
            {"label": "总金额Monto Total", "value": 20}, {"label": "单价Precio", "value": 10},
            {"label": "单位Unidad", "value": "件"},
            {"label": "物品规格Especificacion", "value": "S1"},
        ]}]},
    }

    class Repository:
        def __init__(self):
            self.sources = [source, purchase_source]
            self.runs = []

        def get_context(self, batch_name, version_name=None):
            current = ledger.get("batch", batch_name)["current_version"]
            selected = str(version_name or current)
            if selected != current:
                raise ValueError("只能在当前版本生成 AI 草稿。")
            return {"batch": batch_name, "version": selected, "batch_modified": "M",
                    "version_modified": selected, "transport_mode": "SEA",
                    "effective_source": {}, "fx_rates": {"RMB": "1"}}

        def get_items(self, batch_name, version_name):
            return deepcopy(ledger.rows("item", batch=batch_name, version=version_name))

        def list_sources(self, *_args):
            return deepcopy(self.sources)

        def get_fees(self, *_args):
            return []

        def lock_review_scope(self, *_args):
            return None

        def lock_review_inputs(self, *_args):
            return None

        def assert_write(self, *_args):
            return None

        def find_running_run(self, *_args):
            return None

        def find_reusable_run(self, _batch, version_name, fingerprint):
            return next((run for run in reversed(self.runs)
                         if run["version"] == version_name and run["input_fingerprint"] == fingerprint
                         and run["status"] in {"QUEUED", "RUNNING", "READY"}), None)

        def supersede_active_runs(self, *_args):
            return None

        def create_run(self, payload):
            run = {**deepcopy(payload), "name": f"RUN-{len(self.runs) + 1}"}
            self.runs.append(run)
            return run

        def get_run(self, run_id):
            return next(run for run in self.runs if run["name"] == run_id)

        lock_run = get_run

        def save_row_review_draft(self, run, draft):
            run["draft_json"] = deepcopy(draft)

        def apply_row_selection(self, run, selected, draft, context):
            persisted = deepcopy(selected)
            # The source-refresh contract is exercised through the run manifest;
            # this in-memory ledger has no archived OA document table to seal here.
            persisted["sources"] = []
            new_version = write_rows(store, ledger, persisted, context)
            result = {"ok": True, "status": "APPLIED", "run_id": run["name"],
                      "preview_id": selected["id"], "version_name": new_version,
                      "changed_count": len(selected["changes"]), "batch_modified": "M"}
            run.update(status="APPLIED", draft_json={**draft, "row_application": result})
            return result

        def rollback(self):
            raise AssertionError("selection write unexpectedly rolled back")

    repo = Repository()

    def prepare_and_apply(run_id):
        run = repo.get_run(run_id)
        current_items = repo.get_items(batch["name"], run["version"])
        enriched = enrich_logistics_purchase_facts(current_items, repo.sources,
                                                   fx_rates={"RMB": "1"})
        proposal = build_logistics_reconciliation(enriched["items"], repo.sources[0])
        if run_id == "RUN-2":
            enriched_fact = json.loads(enriched["items"][0]["extra_json"])["logistics_row"]["purchase_fact"]
            assert float(enriched_fact["unit_price"]) == 12
            assert float(proposal["payload"]["rows"][0]["_review_purchase_values"]["unit_price"]) == 12
        ready_draft=ai._load_json(run.get("draft_json"),{})
        ready_draft["row_review_policy"]=selection.rows.POLICY
        run.update(status="READY", candidates_json=[proposal], draft_json=ready_draft)
        catalog = selection.review_catalog(repo, batch["name"], run)
        selected_ids = [row["row_id"] for row in catalog["rows"] if row["origin"] == "source"]
        prepared = selection.prepare(batch["name"], run_id, selected_ids, [], "update_selected",
                                     run["version"], repository=repo)["preview"]
        assert prepared["mode"] == "update_selected"
        assert "_price_metadata" not in str(prepared)
        assert "_verified_prior_item" not in str(prepared)
        if run_id == "RUN-2":
            receipt = run["draft_json"]["row_previews"][prepared["id"]]
            assert "rows" not in receipt
            assert "purchase_fact" not in json.dumps(receipt, ensure_ascii=False)
            assert any(candidate["fieldname"] == "unit_price" and float(candidate["suggested_value"]) == 12
                       for candidate in catalog["field_candidates"])
            assert any(result["code"] == "MANUAL_VALUE_RETAINED" for result in prepared["field_results"])
            assert not prepared["can_apply"]
            return {"version_name": run["version"], "status": "REVIEW_REQUIRED"}
        return selection.confirm(batch["name"], run_id, prepared["id"], prepared["revision"],
                                 "TOKEN", "M", repository=repo)

    first_run = ai.start_source_ai_review(batch["name"], version["name"], force=True,
                                          repository=repo, enqueue=lambda _run: None)
    first_result = prepare_and_apply(first_run["run_id"])
    first_version = first_result["version_name"]
    first_saved = ledger.rows("item", version=first_version)[0]
    assert shipment_value(first_saved)["status"] == "automatic"
    assert float(shipment_value(first_saved)["amount_rmb"]) == 20

    from types import SimpleNamespace
    from overseas_costing.services import calculate_service, effective_source_values

    class ItemDocument:
        def __init__(self, name):
            self.__dict__.update(ledger.get("item", name))

        def as_dict(self):
            return dict(self.__dict__)

        def save(self, **_kwargs):
            ledger.put("item", self.name, self.as_dict())

    class EditDB:
        def sql(self, *_args, **_kwargs):
            return [{"current_version": ledger.get("batch", batch["name"])["current_version"],
                     "confirm_status": "Pending", "writeback_status": "Not Started",
                     "version_status": "Active"}]

        def set_value(self, *_args, **_kwargs):
            return None

        def get_value(self, doctype, _name, fieldname=None, **_kwargs):
            return "M" if doctype == "Overseas Cost Batch" and fieldname == "modified" else None

        def commit(self):
            return None

    fake_frappe = SimpleNamespace(
        db=EditDB(), session=SimpleNamespace(user="finance@example.com"),
        utils=SimpleNamespace(now=lambda: "2026-09-11 10:00:00"),
        get_doc=lambda _doctype, name: ItemDocument(name),
    )
    monkeypatch.setattr(calculate_service, "_frappe", fake_frappe)
    monkeypatch.setattr(calculate_service, "_insert_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(effective_source_values, "batch_source_context", lambda *_args, **_kwargs: {})

    manual_result = calculate_service.update_item_field(
        first_saved["name"], "shipment_value_rmb", "25", version_name=first_version,
        remark="财务确认", _skip_edit_check=True,
    )
    assert manual_result["valuation"]["status"] == "manual"
    first_saved = ledger.get("item", first_saved["name"])
    assert first_saved["manual_override_flag"] == 1
    assert shipment_value(first_saved)["amount_rmb"] == "25"
    refreshed_purchase = deepcopy(purchase_source)
    refreshed_purchase.update(source_id="SOURCE-2", source_hash="PURCHASE-HASH-2",
                              approval_no="PURCHASE-2")
    refreshed_purchase["form_fields"]["采购明细"][0]["rowValue"][2]["value"] = 24
    refreshed_purchase["form_fields"]["采购明细"][0]["rowValue"][3]["value"] = 12
    repo.sources[1] = refreshed_purchase
    first_before_second = deepcopy(ledger.rows("item", version=first_version))

    second_run = ai.start_source_ai_review(batch["name"], version["name"], force=True,
                                           repository=repo, enqueue=lambda _run: None)
    assert repo.get_run(second_run["run_id"])["version"] == first_version
    assert "SOURCE-2" in str(repo.get_run(second_run["run_id"])["source_manifest_json"])
    second_result = prepare_and_apply(second_run["run_id"])
    second_version = second_result["version_name"]
    final = ledger.rows("item", version=second_version)[0]

    assert ledger.get("batch", batch["name"])["current_version"] == second_version
    assert second_version == first_version
    assert second_result["status"] == "REVIEW_REQUIRED"
    assert ledger.rows("item", version=first_version) == first_before_second
    assert shipment_value(final)["status"] == "manual"
    assert shipment_value(final)["amount_rmb"] == "25"
    assert float(final["goods_value"]) == 25
    cleared = deepcopy(final)
    cleared_meta = json.loads(cleared["extra_json"])
    cleared_meta.pop("manual_shipment_valuation")
    cleared["extra_json"] = json.dumps(cleared_meta)
    fallback = shipment_value(cleared)
    assert fallback["status"] == "automatic", fallback
    assert float(fallback["amount_rmb"]) == 20


def test_next_ai_input_preserves_both_selected_duplicate_sku_rows():
    store, ledger, batch, version, first, *_ = settlement_fixture.__wrapped__()
    first = ledger.put("item", first["name"], {
        "stable_line_key": "LINE-1", "actual_shipped_qty": 2, "shipped_uom": "件",
    })
    second = ledger.create("item", {
        **{key: value for key, value in first.items() if key != "name"},
        "stable_line_key": "LINE-2", "actual_shipped_qty": 3,
    })
    original = load_source_bundle(batch["name"], version["name"], store=store, ledger=ledger)
    selected = preview([first, second], [], batch, version, mode="replace_all",
                       source_context=original["context"])

    new_version = write_rows(store, ledger, selected, {})
    bundle = load_source_bundle(batch["name"], new_version, store=store, ledger=ledger)
    saved = ledger.rows("item", version=new_version)
    projected = project_ai_items(saved, bundle)

    assert len(saved) == 2
    assert {item["name"] for item in projected} == {item["name"] for item in saved}


def test_fillability_uses_current_source_blank_instead_of_historical_raw_weight():
    context = {"root_kind": "expense", "available": True, "approved": True,
               "invalid": False, "source_snapshot": "CURRENT", "fingerprint": "CTX"}
    item = {"name": "ITEM", "material_code": "A", "unit": "件", "shipped_uom": "件",
            "gross_weight_kg": 9, "actual_shipped_qty": 2, "extra_json": json.dumps({
                "effective_logistics_source": context,
                "settlement_physical": {"source_context_fingerprint": "CTX", "values": {}},
            })}
    assert project_source_values(item, context)["gross_weight_kg"] is None
    proposal = {"proposal_id": "P", "proposal_type": "item_update",
                "target_item_name": "ITEM", "payload": {"fields": {"gross_weight_kg": 10}}}

    catalog = rows.catalog([item], [proposal], [], context, run_id="RUN")
    recognized = next(row for row in catalog["rows"] if row["origin"] == "source")

    assert recognized["can_fill"], "A raw historical value is not an effective current-source value"


def test_fillability_treats_current_source_weight_zero_as_placeholder():
    context = {"root_kind": "expense", "available": True, "approved": True,
               "invalid": False, "source_snapshot": "CURRENT", "fingerprint": "CTX"}
    item = {"name": "ITEM", "material_code": "A", "unit": "件", "shipped_uom": "件",
            "gross_weight_kg": None, "actual_shipped_qty": 2, "extra_json": json.dumps({
                "effective_logistics_source": context,
                "settlement_physical": {"source_context_fingerprint": "CTX",
                                        "values": {"gross_weight_kg": 0}},
            })}
    assert project_source_values(item, context)["gross_weight_kg"] == 0
    proposal = {"proposal_id": "P", "proposal_type": "item_update",
                "target_item_name": "ITEM", "payload": {"fields": {"gross_weight_kg": 10}}}

    catalog = rows.catalog([item], [proposal], [], context, run_id="RUN")
    recognized = next(row for row in catalog["rows"] if row["origin"] == "source")

    assert recognized["can_fill"], "A zero weight is not meaningful packing evidence"


def test_retained_rows_publish_effective_packing_values_to_next_ai_scope():
    store,ledger,batch,version,item,*_=settlement_fixture.__wrapped__()
    original=load_source_bundle(batch['name'],version['name'],store=store,ledger=ledger)
    context=original['context']
    item=ledger.put('item',item['name'],{'gross_weight_kg':99,'extra_json':json.dumps({
        'effective_logistics_source':context,'settlement_physical':{'source_context_fingerprint':context['fingerprint'],'values':{'gross_weight_kg':5}}})})
    selected=preview([item],[],batch,version,mode='replace_all',source_context=context)
    new=write_rows(store,ledger,selected,{})
    bundle=load_source_bundle(batch['name'],new,store=store,ledger=ledger)
    assert bundle['source']['goods'][0]['physical']['gross_weight_kg']==5


def test_replace_clears_old_dimensional_weight_and_weight_ratio():
    store,ledger,batch,version,item,*_=settlement_fixture.__wrapped__()
    item=ledger.put('item',item['name'],{'volume_weight_kg':900,'weight_ratio':0.9,'shipped_uom':'件'})
    proposal={'proposal_id':'P','proposal_type':'logistics_reconcile','payload':{'rows':[
        {'material_code':'A','product_name':item.get('product_name'),'quantity':2,'unit':'件','_review_origin':'source'}]}}
    selected=preview([item],[proposal],batch,version,mode='replace_all')
    new=write_rows(store,ledger,selected,{})
    saved=ledger.rows('item',version=new)[0]
    assert rows.missing(saved,'volume_weight_kg') and rows.missing(saved,'weight_ratio')
    assert saved['volume_weight_kg']==0


def test_bound_source_fill_updates_the_effective_overlay_at_write_time():
    store,ledger,batch,version,item,*_=settlement_fixture.__wrapped__()
    context=load_source_bundle(batch['name'],version['name'],store=store,ledger=ledger)['context']
    item=ledger.put('item',item['name'],{'gross_weight_kg':99,'extra_json':json.dumps({
        'effective_logistics_source':context,'settlement_physical':{'source_context_fingerprint':context['fingerprint'],'values':{}}})})
    proposal={'proposal_id':'P','proposal_type':'item_update','target_item_name':item['name'],'payload':{'fields':{'gross_weight_kg':10}}}
    cat=rows.catalog([item],[proposal],[],context,run_id='RUN')
    selected=rows.project([item],cat,[cat['rows'][0]['row_id']],[],'fill_missing')
    selected.update(batch=batch['name'],version=version['name'],id='PREVIEW',revision='REV',run_id='RUN',source_context=context)
    write_rows(store,ledger,selected,{})
    assert project_source_values(ledger.get('item',item['name']))['gross_weight_kg']==10


def test_real_xlsx_physical_merge_becomes_a_stable_packing_group_candidate():
    items = [
        {'name':'I1','stable_line_key':'L1','row_no':1,'material_code':'A','actual_shipped_qty':2,
         'unit':'件','shipped_uom':'件','extra_json':json.dumps({'logistics_row':{'identity':{'material_code':'A'}}})},
        {'name':'I2','stable_line_key':'L2','row_no':2,'material_code':'B','actual_shipped_qty':3,
         'unit':'件','shipped_uom':'件','extra_json':json.dumps({'logistics_row':{'identity':{'material_code':'B'}}})},
    ]
    preview = {
        'ok':True,'source':{'sheet_name':'Packing','merge_ranges_available':True},
        'validation':{'blocking':[]},
        'material_rows':[
            {'source_row':2,'material_code':'A','quantity':'2','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
            {'source_row':3,'material_code':'B','quantity':'3','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
        ],
        'groups':[{'group_id':'package-1','row_numbers':[2,3],
                   'package_count':{'value':'1'},'net_weight_kg':{'value':'4'},
                   'gross_weight_kg':{'value':'5'},'volume_m3':{'value':'0.1'},
                   'needs_confirmation':False,
                   'evidence':[{'kind':'xlsx_merge','field':'gross_weight_kg','start_row':2,'end_row':3}]}],
    }

    _projection_candidates(items, {'source_id':'ATTACHMENT','source_hash':'HASH'}, preview)

    candidates = preview['shipment_fill']['packing_group_candidates']
    assert len(candidates) == 1
    assert candidates[0]['member_keys'] == ['L1','L2']
    assert candidates[0]['sheet_name'] == 'Packing'
    assert candidates[0]['can_apply'] is True
    assert candidates[0]['default_selected'] is True


def test_xlsx_group_uses_persisted_legacy_keys_for_reconciled_existing_rows():
    items = [
        {'name':'I1','stable_line_key':'logistics:one','_existing_name':'I1',
         '_existing_stable_line_key':'','row_no':1,'material_code':'A','actual_shipped_qty':2,
         'unit':'件','shipped_uom':'件','extra_json':json.dumps({'logistics_row':{'identity':'logistics:one'}})},
        {'name':'I2','stable_line_key':'logistics:two','_existing_name':'I2',
         '_existing_stable_line_key':'','row_no':2,'material_code':'B','actual_shipped_qty':3,
         'unit':'件','shipped_uom':'件','extra_json':json.dumps({'logistics_row':{'identity':'logistics:two'}})},
    ]
    preview = {
        'ok':True,'source':{'sheet_name':'Packing','merge_ranges_available':True},
        'validation':{'blocking':[]},
        'material_rows':[
            {'source_row':2,'material_code':'A','quantity':'2','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
            {'source_row':3,'material_code':'B','quantity':'3','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
        ],
        'groups':[{'group_id':'package-legacy','row_numbers':[2,3],
                   'package_count':{'value':'1'},'net_weight_kg':{'value':'4'},
                   'gross_weight_kg':{'value':'5'},'volume_m3':{'value':'0.1'},
                   'needs_confirmation':False,
                   'evidence':[{'kind':'xlsx_merge','field':'gross_weight_kg','start_row':2,'end_row':3}]}],
    }

    _projection_candidates(items, {'source_id':'ATTACHMENT','source_hash':'HASH'}, preview)

    candidate = preview['shipment_fill']['packing_group_candidates'][0]
    assert candidate['member_keys'] == ['legacy:I1','legacy:I2']


def test_xlsx_group_waits_for_confirmation_when_a_member_is_an_unmatched_new_row():
    items = [
        {'name':'I1','stable_line_key':'logistics:one','_existing_name':'I1',
         '_existing_stable_line_key':'','row_no':1,'material_code':'A','actual_shipped_qty':2,
         'unit':'件','shipped_uom':'件','extra_json':'{}'},
        {'name':'draft-new','stable_line_key':'logistics:new','_existing_name':'',
         '_existing_stable_line_key':None,'row_no':2,'material_code':'B','actual_shipped_qty':3,
         'unit':'件','shipped_uom':'件','extra_json':'{}'},
    ]
    preview = {
        'ok':True,'source':{'sheet_name':'Packing','merge_ranges_available':True},
        'validation':{'blocking':[]},
        'material_rows':[
            {'source_row':2,'material_code':'A','quantity':'2','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
            {'source_row':3,'material_code':'B','quantity':'3','unit':'件','gross_weight_kg':'5',
             'field_ranges':{'gross_weight_kg':{'start_row':2,'end_row':3,'start_column':8,'end_column':8}}},
        ],
        'groups':[{'group_id':'package-new-member','row_numbers':[2,3],
                   'package_count':{'value':'1'},'net_weight_kg':{'value':'4'},
                   'gross_weight_kg':{'value':'5'},'volume_m3':{'value':'0.1'},
                   'needs_confirmation':False,
                   'evidence':[{'kind':'xlsx_merge','field':'gross_weight_kg','start_row':2,'end_row':3}]}],
    }

    _projection_candidates(items, {'source_id':'ATTACHMENT','source_hash':'HASH'}, preview)

    candidate = preview['shipment_fill']['packing_group_candidates'][0]
    assert candidate['member_keys'] == ['legacy:I1','logistics:new']
    assert candidate['can_apply'] is False
    assert candidate['default_selected'] is False
    assert candidate['needs_member_confirmation'] is True
    assert '未全部匹配' in candidate['resolution_reason']
