from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from overseas_costing.services.logistics_settlement.model import digest, dumps
from overseas_costing.services.logistics_settlement.freight_matching import CANDIDATE_SCOPE_POLICY
from overseas_costing.services.logistics_settlement.store import Store
from overseas_costing.tests.test_settlement_writer import Ledger


NOW = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)


def payment_setup(*, structured=False, scope="review", amount="100", currency="RMB", mode="SEA"):
    store = Store.sqlite(sqlite3.connect(":memory:"))
    store.install()
    ledger = Ledger(store)
    batch = ledger.create("batch", {
        "batch_no": "B1", "status": "Dirty", "confirm_status": "Pending",
        "transport_mode": mode, "modified": "2026-09-14T02:00:00+00:00",
    })
    version = ledger.create("version", {"batch": batch["name"], "status": "Active", "is_current": 1})
    ledger.put("batch", batch["name"], {"current_version": version["name"]})
    logistics = {
        "id": "logistics-1", "corp": "corp", "instance": "LOG-1", "kind": "logistics",
        "snapshot": "logistics-snapshot", "match_hash": "lm", "updated_at": "2026-09-14T01:00:00+00:00",
        "invalid": False, "identifiers": [("waybill", "WB-1")],
    }
    source = {
        "id": "payment-1", "corp": "corp", "instance": "PAY-1", "kind": "expense",
        "snapshot": "payment-snapshot", "match_hash": "pm", "updated_at": "2026-09-14T01:00:00+00:00",
        "invalid": False, "approved": True, "approval_no": "APP-1", "amount": amount,
        "currency": currency, "title": "approved payment", "identifiers": [],
    }
    for row in (logistics, source):
        store.insert("source", {"id": row["id"], "corp": row["corp"], "instance": row["instance"],
            "kind": row["kind"], "snapshot": row["snapshot"], "match_hash": row["match_hash"],
            "updated_at": row["updated_at"], "data": dumps(row)})
        store.insert("snapshot", {"id": row["snapshot"], "source_id": row["id"], "fingerprint": row["snapshot"], "data": dumps(row)})
    store.insert("batch_map", {"id": logistics["id"], "source_id": logistics["id"], "batch": batch["name"], "data": "{}"})
    line_ids = []
    if structured:
        line = {
            "id": "payment-line-1", "source_id": source["id"], "source_snapshot": source["snapshot"],
            "line_key": "line-key-1", "revision": "line-revision-1", "waybill": "WB-1",
            "approval_no": "", "amount": amount, "currency": currency, "label": "freight",
            "scope": scope, "charge_key": "economic-charge-1", "ambiguous": False, "evidence": {"row": 1},
        }
        store.insert("freight_line", {"id": line["id"], "source_id": source["id"], "snapshot": source["snapshot"],
            "line_key": line["line_key"], "waybill": line["waybill"], "approval_no": "",
            "charge_key": line["charge_key"], "data": dumps(line)})
        line_ids = [line["id"]]
    candidate = {
        "id": "candidate-1", "logistics_id": logistics["id"], "expense_id": source["id"], "status": "pending",
        "revision": "candidate-revision", "expense_snapshot": source["snapshot"],
        "logistics_snapshot": logistics["snapshot"], "line_ids": line_ids,
        "line_scope_policy": CANDIDATE_SCOPE_POLICY,
    }
    store.insert("freight_candidate", {"id": candidate["id"], "logistics_id": logistics["id"],
        "expense_id": source["id"], "status": "pending", "data": dumps(candidate)})
    return store, ledger, batch, version, logistics, source, candidate


def selection(amount="100", key="customs_clearance_fee", **values):
    return {"source_line_id": "approval_total", "logical_fee_key": key, "amount": amount,
            "currency": "RMB", "amount_status": "ACTUAL", "replace_claim_ids": [], **values}


def add_batch(ctx, suffix="2"):
    store, ledger, _batch, _version, logistics, source, candidate = ctx
    batch = ledger.create("batch", {"batch_no": "B" + suffix, "status": "Dirty", "confirm_status": "Pending",
        "transport_mode": "SEA", "modified": "2026-09-14T02:00:00+00:00"})
    version = ledger.create("version", {"batch": batch["name"], "status": "Active", "is_current": 1})
    ledger.put("batch", batch["name"], {"current_version": version["name"]})
    logistics2 = dict(logistics, id="logistics-" + suffix, instance="LOG-" + suffix,
                      snapshot="logistics-snapshot-" + suffix, match_hash="lm" + suffix)
    store.insert("source", {"id": logistics2["id"], "corp": "corp", "instance": logistics2["instance"],
        "kind": "logistics", "snapshot": logistics2["snapshot"], "match_hash": logistics2["match_hash"],
        "updated_at": logistics2["updated_at"], "data": dumps(logistics2)})
    store.insert("snapshot", {"id": logistics2["snapshot"], "source_id": logistics2["id"],
        "fingerprint": logistics2["snapshot"], "data": dumps(logistics2)})
    store.insert("batch_map", {"id": logistics2["id"], "source_id": logistics2["id"], "batch": batch["name"], "data": "{}"})
    candidate2 = dict(candidate, id="candidate-" + suffix, logistics_id=logistics2["id"],
                      logistics_snapshot=logistics2["snapshot"], revision="candidate-revision-" + suffix)
    store.insert("freight_candidate", {"id": candidate2["id"], "logistics_id": logistics2["id"],
        "expense_id": source["id"], "status": "pending", "data": dumps(candidate2)})
    return store, ledger, batch, version, logistics2, source, candidate2


def make_preview(ctx, selections, **kwargs):
    from overseas_costing.services.logistics_settlement.payment_adoption import preview_payment_adoption
    store, ledger, batch, version, _logistics, _source, candidate = ctx
    return preview_payment_adoption(store, ledger, batch["name"], version["name"], candidate["id"],
                                    candidate["revision"], selections, "user", now=NOW, **kwargs)


def confirm_preview(ctx, preview):
    from overseas_costing.services.logistics_settlement.payment_adoption import confirm_payment_adoption
    store, ledger, batch, *_ = ctx
    return confirm_payment_adoption(store, ledger, batch["name"], preview["preview_id"], preview["revision"], "user", now=NOW)


def test_store_installs_payment_tables_and_indexes_idempotently():
    store = Store.sqlite(sqlite3.connect(":memory:"))
    store.install(); store.install()
    tables = {r["name"] for r in store.sql("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r["name"] for r in store.sql("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"oc_ls_payment_preview", "oc_ls_payment_claim", "oc_ls_payment_application"} <= tables
    assert {"oc_ls_payment_claim_source", "oc_ls_payment_claim_batch", "oc_ls_payment_preview_batch"} <= indexes


def test_preview_is_private_read_only_and_confirm_writes_only_selected_fee():
    ctx = payment_setup()
    store, ledger, batch, version, *_ = ctx
    unrelated = ledger.create("rule", {"batch": batch["name"], "version": version["name"],
        "logical_fee_key": "insurance", "amount": 7, "currency": "RMB", "is_enabled": 1, "is_active": 1})
    before_rules = ledger.rows("rule")
    preview = make_preview(ctx, [selection("40")])
    assert ledger.rows("rule") == before_rules and not store.find("payment_claim") and store.count("payment_preview") == 1
    assert "source_snapshot" not in dumps(preview) and "logistics_id" not in dumps(preview)
    result = confirm_preview(ctx, preview)
    rule = next(r for r in ledger.rows("rule") if r.get("source_binding_id"))
    assert result["status"] == "applied" and store.count("payment_claim") == 1
    assert rule["logical_fee_key"] == "customs_clearance_fee" and rule["covered_scopes"] == "customs"
    assert rule["allocation_basis"] == "purchase_value" and rule["is_final"] == 1
    assert ledger.get("rule", unrelated["name"])["is_enabled"] == 1


def test_legacy_scope_candidate_with_unrelated_identifierless_line_is_hidden_and_not_previewable():
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status
    from overseas_costing.services.logistics_settlement.payment_adoption import preview_payment_adoption

    ctx = payment_setup(structured=True, scope="freight", amount="3414.19", mode="EXPRESS")
    store, ledger, batch, version, _logistics, _source, candidate = ctx
    line = store.get("freight_line", "payment-line-1")
    line.update(
        waybill="",
        approval_no="",
        cargo_text="ABC999 其他票物料",
        packing={"material_code_hints": ["ABC999"]},
    )
    store.put("freight_line", {
        "id": line["id"], "waybill": "", "approval_no": "", "data": dumps(line),
    })
    candidate.pop("line_scope_policy")
    store.put("freight_candidate", {
        "id": candidate["id"], "data": dumps(candidate),
    })

    public = batch_status(store, ledger, batch["name"], version["name"])
    assert public["payment_candidates"] == []
    with pytest.raises(ValueError, match="策略已更新"):
        preview_payment_adoption(
            store, ledger, batch["name"], version["name"],
            candidate["id"], candidate["revision"],
            [selection("3414.19", key="international_express_fee",
                       source_line_id="payment-line-1")], "user",
            now=NOW,
        )
    assert not store.find("payment_preview")


def test_confirmation_rejects_preview_when_candidate_scope_policy_becomes_legacy():
    ctx = payment_setup(structured=True, scope="freight", amount="3414.19", mode="EXPRESS")
    store, _ledger, _batch, _version, _logistics, _source, candidate = ctx
    line = store.get("freight_line", "payment-line-1")
    line.update(
        waybill="",
        approval_no="",
        cargo_text="ABC999 其他票物料",
        packing={"material_code_hints": ["ABC999"]},
    )
    store.put("freight_line", {
        "id": line["id"], "waybill": "", "approval_no": "", "data": dumps(line),
    })
    preview = make_preview(
        ctx,
        [selection("3414.19", key="international_express_fee",
                   source_line_id="payment-line-1")],
    )
    candidate.pop("line_scope_policy")
    store.put("freight_candidate", {
        "id": candidate["id"], "data": dumps(candidate),
    })

    with pytest.raises(ValueError, match="策略已更新"):
        confirm_preview(ctx, preview)
    assert not store.find("payment_claim")
    assert store.get("payment_preview", preview["preview_id"])["status"] == "pending"


@pytest.mark.parametrize("mode,key,scope,basis", [
    ("SEA", "international_sea_freight", "freight", "gross_weight"),
    ("AIR", "international_air_freight", "freight", "gross_weight"),
    ("EXPRESS", "international_express_fee", "freight", "gross_weight"),
    ("SEA", "customs_clearance_fee", "customs", "purchase_value"),
    ("SEA", "import_tax", "tax", "purchase_value"),
    ("SEA", "destination_delivery", "mexico_inland", "gross_weight"),
])
def test_fee_keys_create_exact_scope_and_basis(mode, key, scope, basis):
    ctx = payment_setup(mode=mode)
    result = confirm_preview(ctx, make_preview(ctx, [selection(key=key)]))
    claim = result["claims"][0]
    rule = next(r for r in ctx[1].rows("rule") if r.get("source_binding_id") == claim["id"])
    assert (rule["logical_fee_key"], rule["covered_scopes"], rule["allocation_basis"]) == (key, scope, basis)


def test_review_requires_manual_key_and_structured_line_is_exact_and_exclusive():
    ctx = payment_setup(structured=True, scope="review")
    with pytest.raises(ValueError, match="分类"):
        make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}])
    with pytest.raises(ValueError, match="金额"):
        make_preview(ctx, [{"source_line_id": "payment-line-1", "logical_fee_key": "import_tax", "amount": "99", "currency": "RMB"}])
    preview = make_preview(ctx, [{"source_line_id": "payment-line-1", "logical_fee_key": "import_tax", "amount": "100", "currency": "RMB"}])
    confirm_preview(ctx, preview)
    store, ledger, batch, version, logistics, source, candidate = payment_setup(structured=True, scope="review")
    store.sql("DELETE FROM oc_ls_source WHERE id=%s", (source["id"],))
    store.sql("DELETE FROM oc_ls_snapshot WHERE id=%s", (source["snapshot"],))
    store.sql("DELETE FROM oc_ls_freight_line WHERE id=%s", ("payment-line-1",))
    for table in ("source", "snapshot", "freight_line", "payment_claim"):
        rows = ctx[0].sql(f"SELECT * FROM oc_ls_{table}")
        for row in rows:
            cols = list(row); store.sql(f"INSERT OR REPLACE INTO oc_ls_{table} ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})", [row[c] for c in cols])
    source = store.get("source", "payment-1")
    candidate.update(logistics_id=logistics["id"], logistics_snapshot=logistics["snapshot"])
    store.put("freight_candidate", {"id": candidate["id"], "logistics_id": logistics["id"], "expense_id": source["id"], "status": "pending", "data": dumps(candidate)})
    with pytest.raises(ValueError, match="已采用"):
        confirm_preview((store, ledger, batch, version, logistics, source, candidate),
                        make_preview((store, ledger, batch, version, logistics, source, candidate),
                                     [{"source_line_id": "payment-line-1", "logical_fee_key": "import_tax", "amount": "100", "currency": "RMB"}]))


def _remove_structured_line_identifiers(ctx):
    line = ctx[0].get("freight_line", "payment-line-1")
    line.update(waybill="", approval_no="")
    ctx[0].put("freight_line", {"id": line["id"], "waybill": "", "approval_no": "", "data": dumps(line)})


def test_real_line_without_identifiers_is_still_exclusive_across_batches():
    ctx = payment_setup(structured=True, scope="freight", amount="100")
    _remove_structured_line_identifiers(ctx)
    source = {**ctx[5], "amount": "200"}
    ctx[0].put("source", {"id": source["id"], "data": dumps(source)})
    confirm_preview(ctx, make_preview(ctx, [
        {"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]))
    ctx2 = add_batch(ctx)
    with pytest.raises(ValueError, match="已采用|重复"):
        make_preview(ctx2, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}])


def test_real_line_without_identifiers_cannot_be_partially_amended():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim

    ctx = payment_setup(structured=True, scope="freight", amount="100")
    _remove_structured_line_identifiers(ctx)
    claim = confirm_preview(ctx, make_preview(ctx, [
        {"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]))["claims"][0]
    with pytest.raises(ValueError, match="结构化"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "amount", "真实明细金额不可脱离来源", {"amount": "99"}, "user", now=NOW)


def test_aggregate_can_split_and_claim_across_batches_without_exceeding_total():
    ctx = payment_setup(amount="100.000000")
    first = confirm_preview(ctx, make_preview(ctx, [selection("20.000001"), selection("29.999999", key="import_tax")]))
    assert sum(Decimal(c["amount"]) for c in first["claims"]) == Decimal("50.000000")
    store, ledger, _batch, _version, _logistics, source, _candidate = ctx
    ctx2 = add_batch(ctx)
    confirm_preview(ctx2, make_preview(ctx2, [selection("50")]))
    assert sum(Decimal(c["amount"]) for c in store.find("payment_claim", source_id=source["id"]) if c["status"] == "active") == Decimal("100.000000")
    with pytest.raises(ValueError, match="剩余"):
        make_preview(ctx2, [selection("0.000001", key="import_tax")])


def test_two_batch_concurrent_previews_cannot_overclaim_aggregate():
    ctx1 = payment_setup(amount="100")
    ctx2 = add_batch(ctx1)
    first = make_preview(ctx1, [selection("60")])
    second = make_preview(ctx2, [selection("60")])
    confirm_preview(ctx1, first)
    with pytest.raises(ValueError, match="认领余额"):
        confirm_preview(ctx2, second)
    active = [row for row in ctx1[0].find("payment_claim", source_id=ctx1[5]["id"]) if row["status"] == "active"]
    assert sum(abs(Decimal(row["amount"])) for row in active) == Decimal("60")


def test_negative_needs_explicit_confirmation_and_is_audited():
    ctx = payment_setup(amount="100")
    with pytest.raises(ValueError, match="负数"):
        make_preview(ctx, [selection("-1")])
    preview = make_preview(ctx, [selection("-1")], negative_confirmed=True, reason="退款冲抵已核对")
    confirm_preview(ctx, preview)
    audit = ctx[0].find("audit", binding_id=ctx[2]["name"])[-1]
    assert audit["negative_confirmed"] is True and audit["reason"] == "退款冲抵已核对"


@pytest.mark.parametrize("changed,expected", [
    ("batch", "批次"), ("version", "版本"), ("candidate", "候选"), ("source", "来源"),
    ("line", "明细"), ("claim", "认领"), ("rule", "费用"), ("expired", "过期"),
])
def test_preview_fails_closed_when_any_dependency_changes(changed, expected):
    ctx = payment_setup(structured=(changed == "line"), scope="freight")
    selections = ([{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]
                  if changed == "line" else [selection()])
    preview = make_preview(ctx, selections)
    store, ledger, batch, version, logistics, source, candidate = ctx
    now = NOW
    if changed == "batch": ledger.put("batch", batch["name"], {"modified": "2026-09-14T02:01:00+00:00"})
    elif changed == "version": ledger.put("batch", batch["name"], {"current_version": "different"})
    elif changed == "candidate": store.put("freight_candidate", {"id": candidate["id"], "status": "pending", "data": dumps({**candidate, "revision": "different"})})
    elif changed == "source": store.put("source", {"id": source["id"], "snapshot": "different", "data": dumps({**source, "snapshot": "different"})})
    elif changed == "line": store.put("freight_line", {"id": "payment-line-1", "data": dumps({**store.get("freight_line", "payment-line-1"), "revision": "different"})})
    elif changed == "claim":
        claim = {"id": "foreign", "batch": "other", "version": "v", "logistics_id": "other", "source_id": source["id"], "source_snapshot": source["snapshot"], "source_line_id": "approval_total", "logical_fee_key": "import_tax", "amount": "1", "currency": "RMB", "status": "active", "exclusive": 0, "claim_key": "foreign", "revision": "r"}
        store.insert("payment_claim", {**{k: claim[k] for k in ("id", "batch", "version", "logistics_id", "source_id", "source_snapshot", "source_line_id", "logical_fee_key", "amount", "currency", "status", "exclusive", "claim_key", "revision")}, "data": dumps(claim)})
    elif changed == "rule": ledger.create("rule", {"batch": batch["name"], "version": version["name"], "logical_fee_key": "customs_clearance_fee", "amount": 9, "currency": "RMB", "is_enabled": 1, "is_active": 1})
    elif changed == "expired": now = NOW + timedelta(minutes=31)
    from overseas_costing.services.logistics_settlement.payment_adoption import confirm_payment_adoption
    with pytest.raises(ValueError, match=expected):
        confirm_payment_adoption(store, ledger, batch["name"], preview["preview_id"], preview["revision"], "user", now=now)
    assert not store.find("payment_claim") or changed == "claim"


def test_confirm_is_idempotent_and_existing_actual_requires_explicit_replacement_reason():
    ctx = payment_setup()
    first_preview = make_preview(ctx, [selection("40")])
    first = confirm_preview(ctx, first_preview)
    again = confirm_preview(ctx, first_preview)
    assert again["cached"] is True and again["application_id"] == first["application_id"]
    with pytest.raises(ValueError, match="替换"):
        make_preview(ctx, [selection("50")])
    old = first["claims"][0]["id"]
    second_preview = make_preview(ctx, [selection("50", replace_claim_ids=[old])], reason="付款金额更正")
    second = confirm_preview(ctx, second_preview)
    assert len([c for c in ctx[0].find("payment_claim", batch=ctx[2]["name"]) if c["status"] == "active"]) == 1
    assert second["claims"][0]["amount"] == "50"


@pytest.mark.parametrize("action,edits,expected", [
    ("amount", {"amount": "35.123456"}, ("amount", "35.123456")),
    ("status", {"amount_status": "ESTIMATED"}, ("amount_status", "ESTIMATED")),
    ("currency", {"currency": "USD", "amount_status": "ESTIMATED"}, ("currency", "USD")),
    ("reclassify", {"logical_fee_key": "import_tax"}, ("logical_fee_key", "import_tax")),
])
def test_amend_updates_claim_and_rule_with_revision_and_audit(action, edits, expected):
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup()
    applied = confirm_preview(ctx, make_preview(ctx, [selection("30")]))
    claim = applied["claims"][0]
    result = amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"],
                                 claim["revision"], action, "修正已复核", edits, "user", now=NOW)
    updated = result["claim"]
    assert updated[expected[0]] == expected[1] and updated["revision"] != claim["revision"]
    rule = next(r for r in ctx[1].rows("rule") if r.get("source_binding_id") == claim["id"] and r.get("is_active"))
    assert str(rule[expected[0]]) == expected[1]
    assert any(not r.get("is_active") for r in ctx[1].rows("rule") if r.get("source_binding_id") == claim["id"])
    if action == "status": assert rule["is_final"] == 0
    assert any(row["action"] == "payment_claim_" + action for row in ctx[0].find("audit", binding_id=ctx[2]["name"]))


def test_amend_revoke_releases_balance_but_retains_history_and_application():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup()
    applied = confirm_preview(ctx, make_preview(ctx, [selection("30")]))
    claim = applied["claims"][0]
    result = amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                                 "revoke", "不属于本票", {}, "user", now=NOW)
    assert result["claim"]["status"] == "revoked" and ctx[0].get("payment_claim", claim["id"])
    assert ctx[0].count("payment_application") == 2
    replacement = make_preview(ctx, [selection("100")])
    assert replacement["remaining"] == "100"


def test_amend_rejects_actual_currency_change_and_immutable_or_stale_claims():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup()
    claim = confirm_preview(ctx, make_preview(ctx, [selection("30")]))["claims"][0]
    with pytest.raises(ValueError, match="非实际"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "currency", "币种核对", {"currency": "USD"}, "user", now=NOW)
    ctx[1].put("version", ctx[3]["name"], {"status": "Confirmed"})
    with pytest.raises(ValueError, match="历史|确认"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "revoke", "撤销", {}, "user", now=NOW)


def test_public_projection_is_safe_additive_and_marks_stale_claims():
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status
    ctx = payment_setup()
    claim = confirm_preview(ctx, make_preview(ctx, [selection("30")]))["claims"][0]
    public = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    assert public["confirm_status"] == "Pending" and public["writeback_status"] is None
    assert public["payment_revision"] and public["payment_claims"][0]["source_approval_no"] == "APP-1"
    assert "raw" not in dumps(public["payment_claims"]) and "source_snapshot" not in dumps(public["payment_claims"])
    assert "freight" in public
    assert public["payment_candidates"][0]["active_claimed_amount"] == "30"
    assert public["payment_candidates"][0]["remaining_amount"] == "70"
    source = ctx[5]
    ctx[0].put("source", {"id": source["id"], "snapshot": "new-snapshot", "data": dumps({**source, "snapshot": "new-snapshot"})})
    stale = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    assert stale["payment_claims"][0]["stale"] is True and stale["payment_claims"][0]["available_actions"] == ["revoke"]
    assert stale["payment_blocking_reasons"]


def test_payment_source_scope_auto_selects_unique_monthly_match_and_exposes_only_exact_line_summary():
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status

    ctx = payment_setup(structured=True, scope="freight", amount="3414.19", mode="EXPRESS")
    store, ledger, batch, version, _logistics, source, candidate = ctx
    source.update(title="月结付款", approval_no="202608260009000284583")
    store.put("source", {"id": source["id"], "data": dumps(source)})
    line = store.get("freight_line", "payment-line-1")
    line.update(
        waybill="1841361513", approval_no="202607211417000078258",
        billing_weight="46", cargo_text="MWV101144 IP17PRO -TPU",
        packing={
            "material_code_hints": ["MWV101144", "IP17PRO"],
            "chargeable_weight_kg": "46", "gross_weight_kg": "42.05",
            "package_count": "1", "dimensions_cm": ["33", "20", "23"],
            "volume_m3": "0.01518",
        },
        evidence={"file_name": "DHL(6.29-7.24)快递明细.xlsx", "sheet": "DHL快递", "row": 14},
    )
    store.put("freight_line", {"id": line["id"], "waybill": line["waybill"],
        "approval_no": line["approval_no"], "data": dumps(line)})
    candidate.update(method="identifier", issues=[])
    store.put("freight_candidate", _candidate_values_for_runtime(candidate))

    scope = batch_status(store, ledger, batch["name"], version["name"])["payment_source_scope"]

    assert scope["status"] == "AUTO_MATCHED"
    assert scope["version"] == version["name"]
    assert scope["selected_refs"] == [{"candidate_id": candidate["id"],
                                        "revision": candidate["revision"],
                                        "version": version["name"]}]
    assert len(scope["candidates"]) == 1
    summary = scope["candidates"][0]["parsed_summary"]
    assert summary["approval_no"] == "202607211417000078258"
    assert summary["waybill"] == "1841361513"
    assert summary["freight"] == {"amount": "3414.19", "currency": "RMB"}
    assert summary["packing"]["gross_weight_kg"] == "42.05"
    assert summary["evidence"]["row"] == 14
    assert "source_snapshot" not in dumps(scope)


def test_payment_source_scope_derives_packing_from_legacy_monthly_line_payload():
    """Previously archived statement rows may predate the packing projection."""

    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status

    ctx = payment_setup(structured=True, scope="freight", amount="3414.19", mode="EXPRESS")
    store, ledger, batch, version, _logistics, source, candidate = ctx
    source.update(title="月结付款", approval_no="202608260009000284583")
    store.put("source", {"id": source["id"], "data": dumps(source)})
    ledger.create("item", {
        "batch": batch["name"], "version": version["name"],
        "material_code": "MWV101144", "product_name": "薇武士IP17 PRO",
        "gross_weight_kg": 0, "chargeable_weight_kg": 0, "volume_m3": 0,
    })
    line = store.get("freight_line", "payment-line-1")
    line.update(
        waybill="1841361513", approval_no="202607211417000078258",
        billing_weight="46",
        cargo_text='"\u8587\u6b66\u58eb   MWV101144    IP17PRO  -TPU\n\u89c4\u683c33*20*23,\u91cd\u91cf\uff1a42.05kg\n1\u5957\u6a21\u5177+3\u4e2a\u624b\u673a\u58f3"',
        fields={"件数": 1, "重量": 46},
        evidence={"file_name": "DHL(6.29-7.24)快递明细.xlsx", "sheet": "DHL快递", "row": 14},
    )
    line.pop("packing", None)
    store.put("freight_line", {"id": line["id"], "waybill": line["waybill"],
        "approval_no": line["approval_no"], "data": dumps(line)})
    candidate.update(method="identifier", issues=[])
    store.put("freight_candidate", _candidate_values_for_runtime(candidate))

    scope = batch_status(store, ledger, batch["name"], version["name"])["payment_source_scope"]

    packing = scope["candidates"][0]["parsed_summary"]["packing"]
    assert packing == {
        "material_code_hints": ["MWV101144", "IP17PRO"],
        "chargeable_weight_kg": "46", "gross_weight_kg": "42.05",
        "package_count": "1", "dimensions_cm": ["33", "20", "23"],
        "volume_m3": "0.01518",
    }
    material = next(row for row in scope["material_rows"] if row["material_code"] == "MWV101144")
    assert material["values"] == {
        "gross_weight_kg": "42.05", "chargeable_weight_kg": "46",
        "package_count": "1", "volume_m3": "0.01518",
    }
    assert material["fallback_fields"] == []


def _candidate_values_for_runtime(candidate):
    return {
        "id": candidate["id"], "logistics_id": candidate["logistics_id"],
        "expense_id": candidate["expense_id"], "status": candidate["status"],
        "data": dumps(candidate),
    }


def test_payment_source_template_excludes_product_purchase_but_accepts_transport_typed_purchase():
    from overseas_costing.services.logistics_settlement.freight_runtime import _payment_template

    product = {"title": "采购支出", "kind": "expense", "fields": {"支出类型": "采购商品"}}
    product_monthly = {"title": "商品采购月结", "kind": "expense", "fields": {"支出类型": "采购商品"}}
    product_monthly_payment = {
        "title": "DHL 商品采购月结付款", "process_code": "PRODUCT-PURCHASE", "kind": "expense",
        "fields": {"支出类型": "采购商品"},
    }
    transport = {"title": "采购支出", "kind": "expense", "fields": {"支出类型": "运输及物流服务"}}

    assert _payment_template(product) == ""
    assert _payment_template(product_monthly) == ""
    assert _payment_template(product_monthly_payment) == ""
    assert _payment_template(transport) == "运输类采购支出"


def test_payment_source_scope_hides_conflicted_or_issue_candidates():
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status

    for changes in (
        {"status": "conflict", "method": "explicit", "issues": []},
        {"status": "pending", "method": "explicit", "issues": ["本笔费用已用于其他票"]},
    ):
        ctx = payment_setup(structured=True, scope="freight")
        store, ledger, batch, version, _logistics, source, candidate = ctx
        source.update(title="月结付款")
        store.put("source", {"id": source["id"], "data": dumps(source)})
        candidate.update(**changes)
        store.put("freight_candidate", _candidate_values_for_runtime(candidate))

        scope = batch_status(store, ledger, batch["name"], version["name"])["payment_source_scope"]

        assert scope["status"] == "UNAVAILABLE"
        assert scope["candidates"] == []


@pytest.mark.parametrize("batch_updates", [
    {"confirm_status": "Confirmed"},
    {"writeback_status": "Success"},
])
def test_public_projection_is_readonly_after_confirmation_or_writeback(batch_updates):
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status
    ctx = payment_setup()
    confirm_preview(ctx, make_preview(ctx, [selection("30")]))
    ctx[1].put("batch", ctx[2]["name"], batch_updates)
    public = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    assert public["payment_claims"][0]["available_actions"] == []
    assert public["confirm_status"] == batch_updates.get("confirm_status", "Pending")
    assert public["writeback_status"] == batch_updates.get("writeback_status")


def test_existing_actual_rule_without_replaceable_claim_blocks_preview_but_estimate_is_previewed_for_disable():
    ctx = payment_setup()
    store, ledger, batch, version, *_ = ctx
    ledger.create("rule", {"batch": batch["name"], "version": version["name"],
        "logical_fee_key": "customs_clearance_fee", "amount": 8, "currency": "RMB",
        "amount_status": "ACTUAL", "is_final": 1, "is_enabled": 1, "is_active": 1})
    with pytest.raises(ValueError, match="实际费用规则"):
        make_preview(ctx, [selection("20")])

    ctx = payment_setup()
    store, ledger, batch, version, *_ = ctx
    estimate = ledger.create("rule", {"batch": batch["name"], "version": version["name"],
        "logical_fee_key": "customs_clearance_fee", "amount": 8, "currency": "RMB",
        "amount_status": "ESTIMATED", "is_final": 0, "is_enabled": 1, "is_active": 1})
    preview = make_preview(ctx, [selection("20")])
    assert preview["replace_rules"] == [estimate["name"]]
    confirm_preview(ctx, preview)
    assert ledger.get("rule", estimate["name"])["is_active"] == 0


def test_negative_aggregate_uses_absolute_capacity_and_mixed_currency_blocks_confirm():
    ctx = payment_setup(amount="100")
    confirm_preview(ctx, make_preview(ctx, [selection("-60")], negative_confirmed=True, reason="refund"))
    with pytest.raises(ValueError, match="剩余"):
        make_preview(ctx, [selection("-40.000001", key="import_tax")], negative_confirmed=True, reason="refund")

    ctx = payment_setup(amount="100")
    preview = make_preview(ctx, [selection("20")])
    source = ctx[5]
    claim = {"id": "foreign", "batch": "other", "version": "v", "logistics_id": "other",
        "source_id": source["id"], "source_snapshot": source["snapshot"], "source_line_id": "approval_total",
        "logical_fee_key": "import_tax", "amount": "1", "currency": "USD", "amount_status": "ESTIMATED",
        "status": "active", "exclusive": 0, "claim_key": "foreign", "revision": "r"}
    ctx[0].insert("payment_claim", {**{k: claim[k] for k in ("id", "batch", "version", "logistics_id", "source_id", "source_snapshot", "source_line_id", "logical_fee_key", "amount", "currency", "status", "exclusive", "claim_key", "revision")}, "data": dumps(claim)})
    from overseas_costing.services.logistics_settlement.payment_adoption import public_payment_context
    assert any("混合币种" in reason for reason in public_payment_context(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])["payment_blocking_reasons"])
    with pytest.raises(ValueError, match="认领余额|混合币种"):
        confirm_preview(ctx, preview)


def test_confirm_lock_order_is_match_then_lease_batch_then_preview():
    from overseas_costing.services.logistics_settlement.payment_adoption import confirm_payment_adoption
    ctx = payment_setup()
    preview = make_preview(ctx, [selection("20")])
    events = []
    original_get = ctx[0].get
    def observed_get(table, identity, lock=False):
        if lock: events.append((table, identity))
        return original_get(table, identity, lock=lock)
    ctx[0].get = observed_get
    def lease(batch_name, *, edit_token, expected_modified):
        events.append(("lease_batch", batch_name, edit_token, expected_modified))
    confirm_payment_adoption(ctx[0], ctx[1], ctx[2]["name"], preview["preview_id"], preview["revision"], "user",
                             now=NOW, lease_check=lease, edit_token="token", expected_modified="modified")
    assert events[:3] == [("state", "match_lock"), ("payment_preview", preview["preview_id"]),
                          ("lease_batch", ctx[2]["name"], "token", "modified")]


def test_multiple_actual_payment_rules_are_valid_fee_inputs_and_nonactual_is_not_final():
    from overseas_costing.services.logistics_settlement.fee_policy import select_fees
    ctx = payment_setup()
    confirm_preview(ctx, make_preview(ctx, [selection("40"), selection("20", key="import_tax")]))
    active = [row for row in ctx[1].rows("rule") if row.get("is_active")]
    assert len(select_fees(active)) == 2

    ctx = payment_setup()
    pending = selection("10", amount_status="MISSING")
    confirm_preview(ctx, make_preview(ctx, [pending]))
    rule = next(row for row in ctx[1].rows("rule") if row.get("source_binding_id"))
    assert rule["is_final"] == 0 and rule["amount_status"] == "MISSING"


def test_distinct_nonoverlapping_final_fee_sources_can_coexist_but_collisions_still_fail():
    from overseas_costing.services.logistics_settlement.fee_policy import select_fees

    manual_tax = {"name": "manual-tax", "rule_code": "manual_tax", "logical_fee_key": "import_tax",
        "amount": "10", "currency": "RMB", "amount_status": "ACTUAL", "is_final": 1,
        "is_enabled": 1, "is_active": 1, "source_binding_id": "tax-evidence",
        "source_snapshot": "tax-snapshot", "covered_scopes": "tax"}
    payment_customs = {"name": "payment-customs", "rule_code": "payment_customs",
        "logical_fee_key": "customs_clearance_fee", "amount": "20", "currency": "RMB",
        "amount_status": "ACTUAL", "is_final": 1, "is_enabled": 1, "is_active": 1,
        "source_binding_id": "payment-claim", "source_snapshot": "payment-snapshot",
        "covered_scopes": "customs"}
    assert select_fees([manual_tax, payment_customs]) == [manual_tax, payment_customs]
    with pytest.raises(ValueError, match="重复|重叠"):
        select_fees([manual_tax, {**payment_customs, "logical_fee_key": "import_tax"}])
    with pytest.raises(ValueError, match="重复|重叠"):
        select_fees([manual_tax, {**payment_customs, "covered_scopes": "tax"}])
    with pytest.raises(ValueError, match="重复|重叠"):
        select_fees([manual_tax, {**payment_customs, "covered_scopes": "tax",
            "source_binding_id": "tax-evidence", "source_snapshot": "tax-snapshot"}])


def test_amend_rejects_private_edit_fields_and_cannot_change_exclusive_source_amount():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup(structured=True, scope="freight")
    claim = confirm_preview(ctx, make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]))["claims"][0]
    with pytest.raises(ValueError, match="字段"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "amount", "核对", {"amount": "100", "source_snapshot": "forged"}, "user")
    with pytest.raises(ValueError, match="结构化"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "amount", "核对", {"amount": "99"}, "user")


def test_amend_negative_amount_requires_fresh_explicit_confirmation():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup()
    claim = confirm_preview(ctx, make_preview(ctx, [selection("20")]))["claims"][0]
    with pytest.raises(ValueError, match="负数"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "amount", "退款核对", {"amount": "-20"}, "user")
    result = amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                                 "amount", "退款核对", {"amount": "-20", "negative_confirmed": True}, "user")
    assert result["claim"]["amount"] == "-20"
    audit = next(row for row in ctx[0].find("audit", binding_id=ctx[2]["name"])
                 if row["action"] == "payment_claim_amount")
    assert audit["after"]["amount"] == "-20"


def test_legacy_and_payment_exclusive_claims_are_mutually_exclusive():
    from overseas_costing.services.logistics_settlement import freight_adoption
    ctx = payment_setup(structured=True, scope="freight")
    legacy = {"id": "legacy", "batch": "other", "logistics_id": "other", "source_id": ctx[5]["id"],
        "line_id": "payment-line-1", "charge_key": "economic-charge-1", "source_snapshot": ctx[5]["snapshot"],
        "amount": "100", "currency": "RMB"}
    ctx[0].insert("freight_claim", {**{key: legacy[key] for key in ("id", "batch", "logistics_id", "source_id", "line_id", "charge_key")},
        "data": dumps(legacy)})
    with pytest.raises(ValueError, match="已采用"):
        make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}])

    ctx = payment_setup(structured=True, scope="freight")
    confirm_preview(ctx, make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]))
    ctx2 = add_batch(ctx)
    with pytest.raises(ValueError, match="已采用"):
        freight_adoption.confirm(ctx2[0], ctx2[1], ctx2[2]["name"], ctx2[3]["name"], ctx2[6]["id"],
                                 ctx2[6]["revision"], ["payment-line-1"], "user")


def test_nonactual_claim_must_be_explicitly_replaced_before_actual_for_same_key():
    ctx = payment_setup()
    old = confirm_preview(ctx, make_preview(ctx, [selection("20", amount_status="ESTIMATED")]))["claims"][0]
    with pytest.raises(ValueError, match="替换"):
        make_preview(ctx, [selection("30")])
    result = confirm_preview(ctx, make_preview(ctx, [selection("30", replace_claim_ids=[old["id"]])], reason="改为实际金额"))
    assert result["claims"][0]["amount_status"] == "ACTUAL"
    assert ctx[0].get("payment_claim", old["id"])["status"] == "replaced"


def test_current_version_ignores_old_version_claim_and_rejects_hidden_old_replacement_id():
    from overseas_costing.services.logistics_settlement.payment_adoption import public_payment_context
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status

    ctx = payment_setup(amount="100")
    old_version = ctx[1].create("version", {"batch": ctx[2]["name"], "status": "Archived", "is_current": 0})
    old = {"id": "old-version-claim", "batch": ctx[2]["name"], "version": old_version["name"],
        "logistics_id": ctx[4]["id"], "logistics_snapshot": ctx[4]["snapshot"], "source_id": ctx[5]["id"],
        "source_snapshot": ctx[5]["snapshot"], "source_line_id": "approval_total",
        "source_line_revision": "old-line", "logical_fee_key": "customs_clearance_fee", "amount": "100",
        "currency": "RMB", "amount_status": "ACTUAL", "status": "active", "active": True,
        "exclusive": 0, "claim_key": "old-version-key", "revision": "old-version-revision",
        "approval_no": "APP-1", "preview_id": "old-preview"}
    ctx[0].insert("payment_claim", {**{key: old[key] for key in (
        "id", "batch", "version", "logistics_id", "source_id", "source_snapshot", "source_line_id",
        "logical_fee_key", "amount", "currency", "status", "exclusive", "claim_key", "revision")},
        "data": dumps(old)})
    preview = make_preview(ctx, [selection("20")])
    with pytest.raises(ValueError, match="当前.*版本|待替换"):
        make_preview(ctx, [selection("20", replace_claim_ids=[old["id"]])], reason="不得改历史")
    confirm_preview(ctx, preview)
    current_public = public_payment_context(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    old_public = public_payment_context(ctx[0], ctx[1], ctx[2]["name"], old_version["name"])
    assert old["id"] not in {row["id"] for row in current_public["payment_claims"]}
    candidate = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])["payment_candidates"][0]
    assert candidate["active_claimed_amount"] == "20" and candidate["remaining_amount"] == "80"
    historical = next(row for row in old_public["payment_claims"] if row["id"] == old["id"])
    assert historical["available_actions"] == []
    assert ctx[0].get("payment_claim", old["id"])["status"] == "active"


def test_structured_line_can_confirm_without_source_level_total():
    ctx = payment_setup(structured=True, scope="freight")
    source = ctx[5]
    source.pop("amount"); source.pop("currency")
    ctx[0].put("source", {"id": source["id"], "data": dumps(source)})
    preview = make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}])
    assert preview["source_total"] is None
    assert confirm_preview(ctx, preview)["status"] == "applied"


def test_idempotent_confirm_retry_returns_cached_before_stale_lease_check():
    from overseas_costing.services.logistics_settlement.payment_adoption import confirm_payment_adoption
    ctx = payment_setup()
    preview = make_preview(ctx, [selection("20")])
    calls = []
    def lease(batch_name, *, edit_token, expected_modified): calls.append(expected_modified)
    confirm_payment_adoption(ctx[0], ctx[1], ctx[2]["name"], preview["preview_id"], preview["revision"], "user",
                             now=NOW, lease_check=lease, edit_token="token", expected_modified="old")
    ctx[1].put("batch", ctx[2]["name"], {"modified": "new"})
    def stale(*args, **kwargs): raise RuntimeError("stale lease")
    retried = confirm_payment_adoption(ctx[0], ctx[1], ctx[2]["name"], preview["preview_id"], preview["revision"], "user",
                                       now=NOW, lease_check=stale, edit_token="token", expected_modified="old")
    assert retried["cached"] is True and calls == ["old"]


def test_exclusive_definitive_scope_cannot_be_reclassified():
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    ctx = payment_setup(structured=True, scope="freight")
    claim = confirm_preview(ctx, make_preview(ctx, [{"source_line_id": "payment-line-1", "amount": "100", "currency": "RMB"}]))["claims"][0]
    with pytest.raises(ValueError, match="分类已明确"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                            "reclassify", "核对", {"logical_fee_key": "import_tax"}, "user")


@pytest.mark.parametrize("updates", [
    {"approved": False},
    {"invalid": True},
    {"kind": "logistics"},
    {"corp": "another-corp"},
])
def test_confirm_revalidates_current_source_state_even_when_snapshot_is_unchanged(updates):
    ctx = payment_setup()
    preview = make_preview(ctx, [selection("20")])
    source = {**ctx[5], **updates}
    values = {"id": source["id"], "data": dumps(source)}
    for column in ("kind", "corp"):
        if column in updates:
            values[column] = source[column]
    ctx[0].put("source", values)
    with pytest.raises(ValueError, match="未批准|失效|企业|类型|状态"):
        confirm_preview(ctx, preview)


def test_stale_payment_claim_blocks_calculation_but_can_be_safely_revoked():
    from overseas_costing.services.logistics_settlement import freight_adoption
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status

    ctx = payment_setup()
    claim = confirm_preview(ctx, make_preview(ctx, [selection("30")]))["claims"][0]
    source = {**ctx[5], "invalid": True}
    ctx[0].put("source", {"id": source["id"], "data": dumps(source)})
    blockers = freight_adoption.blockers(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], for_calculation=True)
    assert any("付款" in reason and ("失效" in reason or "复核" in reason) for reason in blockers)
    public = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    assert public["payment_claims"][0]["available_actions"] == ["revoke"]
    result = amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], claim["id"], claim["revision"],
                                 "revoke", "来源已失效，释放认领", {}, "user", now=NOW)
    assert result["claim"]["status"] == "revoked"
    assert not any("付款" in reason for reason in freight_adoption.blockers(
        ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], for_calculation=True))
    assert any(row["action"] == "payment_claim_revoke" for row in ctx[0].find("audit", binding_id=ctx[2]["name"]))


def test_current_actual_payment_freight_satisfies_final_freight_adoption_requirement():
    from overseas_costing.services.logistics_settlement import freight_adoption

    ctx = payment_setup()
    confirm_preview(ctx, make_preview(ctx, [selection("30", key="international_sea_freight")]))
    assert freight_adoption.blockers(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"]) == []


@pytest.mark.parametrize("changed,expected", [
    ("application", "应用"),
    ("rule_disabled", "规则"),
    ("binding", "绑定"),
    ("rule_amount", "规则"),
    ("rule_status", "规则"),
    ("extra_rule", "规则"),
    ("source_currency", "原币"),
])
def test_payment_freight_only_counts_as_adopted_with_current_application_and_rule(changed, expected):
    from overseas_costing.services.logistics_settlement import freight_adoption

    ctx = payment_setup()
    result = confirm_preview(ctx, make_preview(ctx, [selection("30", key="international_sea_freight")]))
    claim = result["claims"][0]
    if changed == "application":
        application = ctx[0].get("payment_application", result["application_id"])
        application["status"] = "revoked"
        ctx[0].put("payment_application", {"id": application["id"], "status": application["status"],
                                            "data": dumps(application)})
    elif changed == "source_currency":
        source = {**ctx[5], "currency": "USD"}
        ctx[0].put("source", {"id": source["id"], "data": dumps(source)})
    elif changed == "extra_rule":
        ctx[1].create("rule", {"batch": ctx[2]["name"], "version": ctx[3]["name"],
            "rule_code": "payment_extra", "logical_fee_key": "international_sea_freight",
            "amount": "1", "currency": "RMB", "amount_status": "ACTUAL", "is_final": 1,
            "is_enabled": 1, "is_active": 1, "source_binding_id": "extra-binding"})
    else:
        rule = next(row for row in ctx[1].rows("rule") if row.get("is_active"))
        updates = ({"is_active": 0} if changed == "rule_disabled" else
                   {"source_binding_id": "wrong-binding"} if changed == "binding" else
                   {"amount_status": "ESTIMATED"} if changed == "rule_status" else {"amount": "invalid"})
        ctx[1].put("rule", rule["name"], updates)
    blockers = freight_adoption.blockers(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    assert any("付款运费" in reason and expected in reason for reason in blockers)


def test_runtime_uses_payment_blockers_even_when_a_legacy_binding_exists(monkeypatch):
    from overseas_costing.services.logistics_settlement import runtime

    ctx = payment_setup()
    confirm_preview(ctx, make_preview(ctx, [selection("30")]))
    source = {**ctx[5], "invalid": True}
    ctx[0].put("source", {"id": source["id"], "data": dumps(source)})
    monkeypatch.setattr(runtime, "freight_enabled", lambda: True)
    monkeypatch.setattr(runtime, "store", lambda: ctx[0])
    monkeypatch.setattr(runtime, "FrappeLedger", lambda: ctx[1])
    monkeypatch.setattr(runtime, "for_batch", lambda *_args, **_kwargs: {"id": "legacy-binding"})
    blockers = runtime.calculation_blockers(ctx[2]["name"], ctx[3]["name"], for_calculation=True)
    assert any("付款" in reason for reason in blockers)


def _add_second_structured_line(ctx, *, amount="60", currency="RMB"):
    store, _ledger, _batch, _version, _logistics, source, candidate = ctx
    line = {"id": "payment-line-2", "source_id": source["id"], "source_snapshot": source["snapshot"],
            "line_key": "line-key-2", "revision": "line-revision-2", "waybill": "WB-1",
            "approval_no": "", "amount": amount, "currency": currency, "label": "freight 2",
            "scope": "freight", "charge_key": "economic-charge-2", "ambiguous": False, "evidence": {"row": 2}}
    store.insert("freight_line", {"id": line["id"], "source_id": source["id"], "snapshot": source["snapshot"],
        "line_key": line["line_key"], "waybill": line["waybill"], "approval_no": "",
        "charge_key": line["charge_key"], "data": dumps(line)})
    candidate.update(line_ids=[*candidate["line_ids"], line["id"]])
    store.put("freight_candidate", {"id": candidate["id"], "data": dumps(candidate)})
    source["amount"] = "100"
    store.put("source", {"id": source["id"], "data": dumps(source)})
    return line


def test_same_logical_key_source_lines_create_distinct_claims_but_one_aggregated_rule():
    from overseas_costing.services.logistics_settlement.payment_adoption import public_payment_context
    ctx = payment_setup(structured=True, scope="freight", amount="40")
    _add_second_structured_line(ctx)
    selections = [
        {"source_line_id": "payment-line-1", "amount": "40", "currency": "RMB"},
        {"source_line_id": "payment-line-2", "amount": "60", "currency": "RMB"},
    ]
    result = confirm_preview(ctx, make_preview(ctx, selections))
    rules = [row for row in ctx[1].rows("rule") if row.get("is_active")]
    assert len(result["claims"]) == 2 and len(rules) == 1
    assert Decimal(str(rules[0]["amount"])) == Decimal("100")
    assert {claim["rule_binding_id"] for claim in result["claims"]} == {rules[0]["source_binding_id"]}
    projected = public_payment_context(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])["payment_claims"]
    assert all("currency" not in row["available_actions"] and "status" not in row["available_actions"]
               for row in projected)


def test_same_logical_key_source_lines_with_mixed_currency_are_blocked_in_preview():
    ctx = payment_setup(structured=True, scope="freight", amount="40")
    _add_second_structured_line(ctx, currency="USD")
    with pytest.raises(ValueError, match="同一费用分类.*币种"):
        make_preview(ctx, [
            {"source_line_id": "payment-line-1", "amount": "40", "currency": "RMB"},
            {"source_line_id": "payment-line-2", "amount": "60", "currency": "USD"},
        ])


def test_all_candidate_selections_must_use_one_source_approval_currency_across_fee_keys():
    ctx = payment_setup(structured=True, scope="review", amount="40", currency="RMB")
    _add_second_structured_line(ctx, currency="USD")
    with pytest.raises(ValueError, match="审批原币|全部.*币种"):
        make_preview(ctx, [
            {"source_line_id": "payment-line-1", "logical_fee_key": "customs_clearance_fee",
             "amount": "40", "currency": "RMB"},
            {"source_line_id": "payment-line-2", "amount": "60", "currency": "USD"},
        ])


def test_preview_is_owned_by_actor_and_cannot_be_confirmed_by_another_actor():
    from overseas_costing.services.logistics_settlement.payment_adoption import confirm_payment_adoption
    ctx = payment_setup()
    preview = make_preview(ctx, [selection("20")])
    with pytest.raises(ValueError, match="操作人|当前用户"):
        confirm_payment_adoption(ctx[0], ctx[1], ctx[2]["name"], preview["preview_id"], preview["revision"],
                                 "another-user", now=NOW)


def test_legacy_freight_claim_is_in_unified_projection_and_new_amend_can_revoke_it():
    from overseas_costing.services.logistics_settlement import freight_adoption
    from overseas_costing.services.logistics_settlement.freight_runtime import batch_status
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim

    ctx = payment_setup(structured=True, scope="freight")
    legacy = freight_adoption.confirm(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], ctx[6]["id"],
                                      ctx[6]["revision"], ["payment-line-1"], "user")
    public = batch_status(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"])
    projected = next(row for row in public["payment_claims"] if row["id"] == legacy["claims"][0]["id"])
    assert projected["legacy"] is True and projected["available_actions"] == ["amount", "revoke"]
    result = amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], projected["id"],
                                 projected["revision"], "revoke", "历史运费认领错票", {}, "user", now=NOW)
    assert result["status"] == "applied" and not ctx[0].find("freight_claim", batch=ctx[2]["name"])
    assert not ctx[0].find("payment_claim", batch=ctx[2]["name"])


def test_legacy_and_payment_freight_claims_cannot_be_mixed_across_distinct_lines():
    from overseas_costing.services.logistics_settlement import freight_adoption

    ctx = payment_setup(structured=True, scope="freight", amount="40")
    _add_second_structured_line(ctx)
    freight_adoption.confirm(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], ctx[6]["id"],
                             ctx[6]["revision"], ["payment-line-1"], "user")
    with pytest.raises(ValueError, match="新旧运费|统一付款"):
        make_preview(ctx, [{"source_line_id": "payment-line-2", "amount": "60", "currency": "RMB"}])

    ctx = payment_setup(structured=True, scope="freight", amount="40")
    _add_second_structured_line(ctx)
    confirm_preview(ctx, make_preview(ctx, [
        {"source_line_id": "payment-line-1", "amount": "40", "currency": "RMB"}]))
    with pytest.raises(ValueError, match="新旧运费|统一付款"):
        freight_adoption.confirm(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], ctx[6]["id"],
                                 ctx[6]["revision"], ["payment-line-2"], "user")

    ctx = payment_setup(structured=True, scope="review", amount="40")
    _add_second_structured_line(ctx)
    freight_adoption.confirm(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], ctx[6]["id"],
                             ctx[6]["revision"], ["payment-line-2"], "user")
    payment_claim = confirm_preview(ctx, make_preview(ctx, [
        {"source_line_id": "payment-line-1", "logical_fee_key": "customs_clearance_fee",
         "amount": "40", "currency": "RMB"}]))["claims"][0]
    from overseas_costing.services.logistics_settlement.payment_adoption import amend_payment_claim
    with pytest.raises(ValueError, match="新旧运费|统一付款"):
        amend_payment_claim(ctx[0], ctx[1], ctx[2]["name"], ctx[3]["name"], payment_claim["id"],
                            payment_claim["revision"], "reclassify", "重分类为运费",
                            {"logical_fee_key": "international_sea_freight"}, "user", now=NOW)
