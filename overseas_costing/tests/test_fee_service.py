"""费用修改、默认清单和凭证关联事务测试。"""

import json

import pytest

from overseas_costing.services import fee_service
from overseas_costing.services.fee_service import (
    build_default_fee_templates,
    build_evidence_candidates,
    compose_fee_worklist_rows,
    deduplicate_evidence_candidates,
    map_historical_fee_key,
    merge_logical_fee,
    normalize_fee_payload,
)


def test_express_defaults_are_unconfirmed_zeros_and_mexican_currencies():
    rows = {row['logical_fee_key']: row for row in build_default_fee_templates('EXPRESS')}
    for key, currency in [('express_surcharge', 'RMB'), ('destination_delivery', 'MXN')]:
        assert rows[key]['amount'] == '0'
        assert rows[key]['amount_status'] == 'ESTIMATED'
        assert rows[key]['currency'] == currency
        assert rows[key]['is_default_zero'] is True
    assert rows['destination_delivery']['expense_category'] == '当地快递费'
    for key in ('customs_clearance_fee', 'import_tax'):
        assert rows[key]['amount'] == ''
        assert rows[key]['amount_status'] == 'MISSING'
        assert rows[key]['currency'] == 'MXN'
    for key in ('express_surcharge', 'destination_delivery', 'customs_clearance_fee', 'import_tax'):
        assert rows[key]['entry_responsibility'] == 'MEXICO'
        assert rows[key]['virtual'] is True
    assert rows['international_express_fee']['currency'] == 'RMB'
    assert not rows['international_express_fee'].get('entry_responsibility')


@pytest.mark.parametrize('mode', ['SEA', 'AIR'])
def test_non_express_defaults_apply_mexico_local_currency_and_delivery_estimate(mode):
    rows = build_default_fee_templates(mode)
    assert rows[0]['currency'] == 'RMB' and rows[0]['amount'] == ''
    assert all(row['currency'] == 'MXN' and row['entry_responsibility'] == 'MEXICO' for row in rows[1:])
    assert all(row['amount_status'] == 'MISSING' for row in rows[:-1])
    assert rows[-1]['expense_category'] == '当地配送费' and rows[-1]['amount'] == '0'


@pytest.mark.parametrize('amount,status,currency', [('0', 'ACTUAL', 'USD'), ('50.25', 'ESTIMATED', 'RMB'), ('', 'MISSING', 'USD'), ('0', 'NOT_INCURRED', 'MXN')])
def test_express_saved_fees_keep_user_values_and_do_not_inherit_default_zero(amount, status, currency):
    from copy import deepcopy
    saved = {'name': 'R-1', 'expense_category': '目的地配送费', 'amount': amount,
             'amount_status': status, 'currency': currency, 'remark': '原始核对记录'}
    before = deepcopy(saved)
    rows = compose_fee_worklist_rows([saved], 'EXPRESS')
    assert len(rows) == 5
    row = next(row for row in rows if row['logical_fee_key'] == 'destination_delivery')
    assert row['name'] == 'R-1' and row['expense_category'] == '当地快递费'
    assert (row['amount'], row['amount_status'], row['currency'], row['remark']) == (amount, status, currency, saved['remark'])
    assert row.get('is_default_zero') is False
    assert row['entry_responsibility'] == 'MEXICO'
    assert saved == before


def test_both_delivery_names_map_to_one_express_fee_identity():
    assert map_historical_fee_key({'expense_category': '目的地配送费'}, 'EXPRESS') == 'destination_delivery'
    assert map_historical_fee_key({'expense_category': '当地快递费'}, 'EXPRESS') == 'destination_delivery'


@pytest.mark.parametrize('currency', ['', None])
def test_saved_missing_currency_keeps_legacy_rmb_fallback(currency):
    saved = {'name': 'R-1', 'logical_fee_key': 'customs_clearance_fee',
             'expense_category': '清关费', 'amount': '100', 'amount_status': 'ACTUAL', 'currency': currency}
    rows = compose_fee_worklist_rows([saved], 'EXPRESS')
    row = next(row for row in rows if row['name'] == 'R-1')
    assert row['currency'] == 'RMB'
    assert saved['currency'] == currency


@pytest.mark.parametrize('mode', ['SEA', 'AIR'])
def test_non_express_local_fee_alias_does_not_merge_existing_distinct_fees(mode):
    saved = [{'name': 'DELIVERY', 'expense_category': '目的地配送费', 'amount': '100', 'amount_status': 'ACTUAL'},
             {'name': 'LOCAL', 'expense_category': '当地快递费', 'amount': '50', 'amount_status': 'ACTUAL'}]
    rows = compose_fee_worklist_rows(saved, mode)
    assert len(rows) == 5
    assert {row['name'] for row in rows if not row['virtual']} == {'DELIVERY', 'LOCAL'}
    assert not any(row.get('duplicate_rule_names') for row in rows)


def test_default_fee_templates_follow_transport_specific_primary_and_shared_local_fees() -> None:
    sea = build_default_fee_templates("SEA")
    air = build_default_fee_templates("AIR")
    express = build_default_fee_templates("EXPRESS")

    assert [row["expense_category"] for row in sea] == [
        "国际海运费",
        "清关费",
        "进口税费",
        "当地配送费",
    ]
    assert [row["allocation_basis"] for row in sea] == [
        "volume",
        "goods_value",
        "goods_value",
        "gross_weight",
    ]
    assert air[0]["expense_category"] == "国际空运费"
    assert air[0]["allocation_basis"] == "chargeable_weight"
    assert express[0]["expense_category"] == "国际快递费"
    assert all(row["virtual"] for row in sea + air + express)


def test_historical_fee_names_map_in_memory_without_guessing_unknown_names() -> None:
    assert map_historical_fee_key({"expense_category": "国际海运费"}, "SEA") == "international_sea_freight"
    assert map_historical_fee_key({"rule_code": "清关费"}, "SEA") == "customs_clearance_fee"
    assert map_historical_fee_key({"expense_category": "历史特殊费用"}, "SEA") == ""


def test_saved_historical_fee_replaces_virtual_template_without_writing_defaults() -> None:
    rows = compose_fee_worklist_rows(
        [
            {
                "name": "RULE-1",
                "expense_category": "国际海运费",
                "amount": "123.45",
                "currency": "USD",
            },
            {
                "name": "RULE-LEGACY",
                "expense_category": "历史特殊费用",
                "amount": "20",
                "currency": "RMB",
            },
        ],
        "SEA",
    )

    assert len([row for row in rows if row["logical_fee_key"] == "international_sea_freight"]) == 1
    freight = next(row for row in rows if row["logical_fee_key"] == "international_sea_freight")
    assert freight["name"] == "RULE-1"
    assert freight["amount"] == "123.45"
    assert freight["amount_status"] == "ESTIMATED"
    assert freight["required_evidence_role"] == "freight_invoice"
    assert freight["virtual"] is False
    legacy = next(row for row in rows if row["name"] == "RULE-LEGACY")
    assert legacy["legacy_unmapped"] is True
    assert legacy["logical_fee_key"] == "legacy:RULE-LEGACY"


def test_parsed_amounts_are_candidates_only_and_never_become_fee_rows() -> None:
    attachments = [
        {
            "name": "ATT-1",
            "file_name": "freight.pdf",
            "source_type": "Voucher",
            "parse_status": "Parsed",
            "parse_result_json": '{"classification":{"code":"logistics_quote"},"field_candidates":{"amount_candidate":321.50,"currency":"USD"}}',
            "mapped_result_json": "{}",
        }
    ]

    candidates = build_evidence_candidates(attachments)

    assert candidates[0]["attachment"] == "ATT-1"
    assert candidates[0]["amount_candidates"] == [
        {"amount": "321.5", "currency": "USD", "path": "field_candidates.amount_candidate"}
    ]
    assert "logical_fee_key" not in candidates[0]


def test_estimate_to_actual_updates_one_logical_fee_instead_of_adding() -> None:
    existing = [
        {
            "name": "RULE-1",
            "logical_fee_key": "FREIGHT",
            "amount": "100",
            "amount_status": "ESTIMATED",            "amount_revision": "A1",
            "scope_type": "ALL_ITEMS",
            "allocation_basis": "gross_weight",
        }
    ]

    result = merge_logical_fee(
        existing,
        normalize_fee_payload(
            {
                "logical_fee_key": "FREIGHT",
                "amount": "120",
                "amount_status": "ACTUAL",
                "currency": "RMB",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "gross_weight",
            }
        ),
        revision="A2",
    )

    assert len(result["fees"]) == 1
    assert result["action"] == "updated"
    assert result["fees"][0]["name"] == "RULE-1"
    assert result["fees"][0]["amount"] == "120"
    assert result["fees"][0]["amount_status"] == "ACTUAL"
    assert result["fees"][0]["amount_revision"] == "A2"
    assert result["cost_inputs_changed"] is True


def test_same_amount_nature_change_still_reopens_cost_result() -> None:
    result = merge_logical_fee(
        [
            {
                "logical_fee_key": "FREIGHT",
                "amount": "100",
                "amount_status": "ESTIMATED",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "gross_weight",
            }
        ],
        normalize_fee_payload(
            {
                "logical_fee_key": "FREIGHT",
                "amount": "100",
                "amount_status": "ACTUAL",
                "currency": "RMB",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "gross_weight",
            }
        ),
        revision="A2",
    )

    assert result["cost_inputs_changed"] is True


@pytest.mark.parametrize("currency", [None, "", "US", "USDT", "U1D"])
def test_normalize_fee_payload_rejects_invalid_currency(currency) -> None:
    with pytest.raises(ValueError, match="币种"):
        normalize_fee_payload(
            {
                "logical_fee_key": "FREIGHT",
                "amount": "100",
                "amount_status": "ACTUAL",
                "currency": currency,
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "gross_weight",
            }
        )


def test_normalize_fee_payload_defaults_missing_currency_to_rmb() -> None:
    result = normalize_fee_payload(
        {
            "logical_fee_key": "FREIGHT",
            "amount": "100",
            "amount_status": "ACTUAL",
            "scope_type": "ALL_ITEMS",
            "allocation_basis": "gross_weight",
        }
    )

    assert result["currency"] == "RMB"


@pytest.mark.parametrize(("currency", "expected"), [("rmb", "RMB"), ("cny", "RMB"), ("usd", "USD"), ("mxn", "MXN")])
def test_normalize_fee_payload_normalizes_supported_currencies(currency, expected) -> None:
    result = normalize_fee_payload(
        {
            "logical_fee_key": "FREIGHT",
            "amount": "100",
            "amount_status": "ACTUAL",
            "currency": currency,
            "scope_type": "ALL_ITEMS",
            "allocation_basis": "gross_weight",
        }
    )

    assert result["currency"] == expected


@pytest.mark.parametrize("currency", ["EUR", "GBP", "JPY"])
def test_normalize_fee_payload_rejects_unsupported_currencies(currency):
    with pytest.raises(ValueError, match="币种"):
        normalize_fee_payload({"logical_fee_key": "freight", "amount_status": "ACTUAL", "amount": "100", "currency": currency})


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_normalize_fee_payload_rejects_non_finite_amount(amount: str) -> None:
    with pytest.raises(ValueError, match="金额"):
        normalize_fee_payload(
            {
                "logical_fee_key": "FREIGHT",
                "amount": amount,
                "amount_status": "ACTUAL",
                "currency": "RMB",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "gross_weight",
            }
        )


def test_not_incurred_and_included_require_auditable_reason() -> None:
    with pytest.raises(ValueError, match="原因"):
        normalize_fee_payload(
            {
                "logical_fee_key": "STORAGE",
                "amount_status": "NOT_INCURRED",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "goods_value",
            }
        )
    with pytest.raises(ValueError, match="已包含于"):
        normalize_fee_payload(
            {
                "logical_fee_key": "SURCHARGE",
                "amount_status": "INCLUDED",
                "remark": "已并入主费用",
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "goods_value",
            }
        )


def test_attachment_candidates_are_deduplicated_but_not_turned_into_fees() -> None:
    result = deduplicate_evidence_candidates(
        [
            {"attachment": "ATT-1", "evidence_role": "invoice", "source_revision": "R1"},
            {"attachment": "ATT-1", "evidence_role": "invoice", "source_revision": "R1"},
            {"attachment": "ATT-2", "evidence_role": "payment", "source_revision": "R1"},
        ]
    )

    assert result == [
        {"attachment": "ATT-1", "evidence_role": "invoice", "source_revision": "R1"},
        {"attachment": "ATT-2", "evidence_role": "payment", "source_revision": "R1"},
    ]
    assert all("logical_fee_key" not in row for row in result)


def test_deleted_attachment_unlinks_evidence_without_creating_completion_state(monkeypatch) -> None:
    writes = []

    class FakeDb:
        @staticmethod
        def set_value(doctype, name, values, update_modified=False):
            writes.append((doctype, name, values, update_modified))

    class FakeFrappe:
        db = FakeDb()

        @staticmethod
        def get_all(*_args, **_kwargs):
            return [{"name": "EVID-1"}, {"name": "EVID-2"}]

    monkeypatch.setattr(fee_service, "frappe", FakeFrappe())

    result = fee_service.unlink_fee_evidence_for_attachment("ATT-1", reason="附件已删除")

    assert result["affected_count"] == 2
    assert all(row[2]["validation_status"] == "UNLINKED" for row in writes)
    assert all(row[2]["attachment"] == "" for row in writes)


def test_fee_worklist_rejects_a_version_from_another_batch(monkeypatch) -> None:
    class FakeDb:
        @staticmethod
        def get_value(doctype, name, fieldname, **_kwargs):
            if doctype == "Overseas Cost Version" and fieldname == "batch":
                return "OTHER-BATCH"
            return None

    class FakeFrappe:
        db = FakeDb()

    monkeypatch.setattr(fee_service, "frappe", FakeFrappe())

    with pytest.raises(ValueError, match="不属于当前批次"):
        fee_service.get_fee_worklist("BATCH-1", "VERSION-OTHER")


@pytest.mark.parametrize(("amount", "expected"), [("10", "BLOCKED"), ("0", "ALLOCATED")])
def test_worklist_and_preview_agree_when_foreign_currency_has_no_fx(monkeypatch, amount, expected):
    from overseas_costing.services import cost_preview_service

    items = [{"name": "A", "stable_line_key": "A", "goods_value": "100", "quantity": "1", "purchase_uom": "件"}]
    rules = build_default_fee_templates("SEA")
    for rule in rules:
        rule.update(amount_status="NOT_INCURRED", remark="未发生", name=rule["logical_fee_key"])
    rules[0].update(amount_status="ACTUAL", amount=amount, currency="USD")

    class FakeDb:
        @staticmethod
        def get_value(doctype, name, fieldname, **kwargs):
            if isinstance(fieldname, list):
                return {}
            return {"batch": "B", "current_version": "V", "transport_mode": "SEA"}.get(fieldname)

    class FakeFrappe:
        db = FakeDb()

        @staticmethod
        def get_all(doctype, **kwargs):
            return {
                "Overseas Cost Item": items,
                "Overseas Cost Fee Evidence": [{"name": "E", "fee_rule": rules[0]["name"], "evidence_role": "freight_invoice", "validation_status": "VALID"}],
            }.get(doctype, [])

    monkeypatch.setattr(fee_service, "frappe", FakeFrappe())
    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe())
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: rules)
    # The preview now resolves its valuation date separately. Keep the
    # fixture focused on fee allocation when there is no usable USD rate.
    def resolve_without_usd(self, batch_name, version_name):
        assert (batch_name, version_name) == ("B", "V")
        return {"ok": True, "fx_usd_to_rmb": None, "fx_rmb_to_mxn": 2.5}

    monkeypatch.setattr(cost_preview_service.FrappeCostRepository, "prepare_fx_resolution", resolve_without_usd)
    worklist = fee_service.get_fee_worklist("B", "V")
    preview = cost_preview_service.preview_comprehensive_cost("B", "V")
    assert worklist["fees"][0]["allocation_state"] == expected
    assert worklist["summary"]["all_requirements_satisfied"] is (amount == "0")
    assert preview["summary"]["included_fee_count"] == (1 if amount == "0" else 0)
    if amount != "0":
        assert worklist["fees"][0]["todos"][0]["code"] == preview["excluded_fees"][0]["reason_code"] == "FX_RATE_MISSING"


@pytest.mark.parametrize(
    "descriptor,expected_stage",
    [
        ({"approval_role": "purchase"}, "purchase"),
        ({"approval_role": "international_logistics"}, "international_logistics"),
        ({"approval_role": "payment"}, "payment"),
    ],
)
def test_evidence_candidates_expose_the_three_pullable_workflow_stages(descriptor, expected_stage) -> None:
    """采购、国际物流、费用申请三类凭证都必须带上服务端判定的流程阶段。"""

    attachments = [
        {
            "name": "ATT-STAGE",
            "file_name": "evidence.pdf",
            "source_type": "OA",
            "parse_status": "Parsed",
            "parse_result_json": json.dumps({"classification": {"code": "expense"}, **descriptor}),
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments)[0]

    assert candidate["workflow_stage"] == expected_stage
    assert candidate["workflow_label"]
    # 金额候选不再因为流程不同而被清空，来源优先级只决定默认值。
    assert "audit_only" in candidate


def test_evidence_candidates_without_stage_identity_stay_out_of_the_three_processes() -> None:
    """无法判定流程的资料归入其他来源，不能被冒名为三类流程之一。"""

    attachments = [
        {
            "name": "ATT-UNKNOWN",
            "file_name": "misc.pdf",
            "source_type": "",
            "parse_status": "Parsed",
            "parse_result_json": "{}",
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments)[0]

    assert candidate["workflow_stage"] == "other"
    assert candidate["workflow_label"] == "其他来源"


@pytest.mark.parametrize(
    "inventory_slot,expected_stage",
    [
        ("main_expense", "payment"),
        ("main_logistics", "international_logistics"),
        ("linked_purchase", "purchase"),
    ],
)
def test_evidence_candidates_resolve_process_identity_from_the_approval_inventory(
    inventory_slot, expected_stage
) -> None:
    """真实附件本地行只带 process_instance_id，流程身份必须从审批清单回挂。

    ``Overseas Cost Attachment`` 没有 approval_role 字段，缓存下来的
    parse_result_json 也不含审批标题。若只读本地行，所有凭证都会退化成
    “其他来源”，三流程分组在前端永远不出现。
    """

    attachment = {
        "name": "ATT-INVENTORY",
        "file_name": "evidence.pdf",
        "source_type": "OA",
        "parse_status": "Parsed",
        # 注意：这里刻意不写 approval_role / approval_title。
        "parse_result_json": "{}",
        "mapped_result_json": "{}",
    }
    purchase = {
        "instance_id": "INST-PURCHASE",
        "title": "采购审批",
        "form_fields": [{"label": "供应商", "value": "ACME"}],
    }
    if inventory_slot == "linked_purchase":
        attachment["parse_result_json"] = json.dumps({"process_instance_id": "INST-PURCHASE"})
        main = {"instance_id": "INST-LOGISTICS", "title": "国际物流审批",
                "form_fields": [{"label": "费用类型", "value": "海运运费"}]}
        linked = [purchase]
    elif inventory_slot == "main_expense":
        attachment["parse_result_json"] = json.dumps({"process_instance_id": "INST-EXPENSE"})
        main = {"instance_id": "INST-EXPENSE", "title": "运营支出审批",
                "form_fields": [{"label": "费用类型", "value": "月结付款"}]}
        linked = []
    else:
        attachment["parse_result_json"] = json.dumps({"process_instance_id": "INST-LOGISTICS"})
        main = {"instance_id": "INST-LOGISTICS", "title": "国际物流审批",
                "form_fields": [{"label": "费用类型", "value": "海运运费"}]}
        linked = []
    approval_detail = {
        "ok": True,
        "main_approval": main,
        "linked_purchase_approvals": linked,
    }
    if inventory_slot == "main_expense":
        approval_detail["source_context"] = {"root_kind": "expense"}

    candidate = build_evidence_candidates([attachment], approval_detail=approval_detail)[0]

    assert candidate["workflow_stage"] == expected_stage
    assert candidate["workflow_source"] == "approval_inventory"
    assert candidate["process_instance_id"]
    assert candidate["amount_candidates"] == []


@pytest.mark.parametrize(
    "file_name,instance_id,expected_stage",
    [
        ("月结运费凭证.pdf", "INST-LOGISTICS", "international_logistics"),
        ("报销运费.pdf", "INST-LOGISTICS", "international_logistics"),
        ("月结付款-货款.pdf", "INST-PURCHASE", "purchase"),
    ],
)
def test_file_name_payment_words_never_override_the_owning_approval_stage(
    file_name, instance_id, expected_stage
) -> None:
    """审批归属定了流程，文件名里的“月结／报销”不能把它抢走。

    共享分类器会把 ``source_label`` 读进流程身份文本，而“月结／报销”这类支付词
    的判定排在 ``approval_role`` 之前。早先费用链路把 ``file_name`` 直接当
    ``source_label``，于是一份挂在国际物流审批下、只是名字带“月结”的附件，
    会被抢进“费用申请”分组。清单查得到归属时，身份只认归属。
    """

    attachment = {
        "name": "ATT-MISROUTE",
        "file_name": file_name,
        "source_type": "OA",
        "parse_status": "Parsed",
        "parse_result_json": json.dumps({"process_instance_id": instance_id}),
        "mapped_result_json": "{}",
    }
    approval_detail = {
        "ok": True,
        "main_approval": {
            "instance_id": "INST-LOGISTICS",
            "title": "国际物流审批",
            "form_fields": [{"label": "费用类型", "value": "海运运费"}],
        },
        "linked_purchase_approvals": [
            {
                "instance_id": "INST-PURCHASE",
                "title": "采购审批",
                "form_fields": [{"label": "供应商", "value": "ACME"}],
            }
        ],
        "source_context": {"root_kind": "logistics"},
    }

    candidate = build_evidence_candidates([attachment], approval_detail=approval_detail)[0]

    assert candidate["workflow_stage"] == expected_stage
    assert candidate["workflow_source"] == "approval_inventory"
    # 高亮仍按文件名走：判定归判定、展示归展示，改判定不该连带压掉高亮。
    assert candidate["is_voucher_name"] is ("凭证" in file_name)


def test_without_inventory_the_file_name_still_carries_the_payment_hint() -> None:
    """拿不到审批清单时保留文件名兜底，否则月结凭证会整片退化成“其他来源”。"""

    attachment = {
        "name": "ATT-FALLBACK",
        "file_name": "月结付款凭证.pdf",
        "source_type": "OA",
        "parse_status": "Parsed",
        "parse_result_json": json.dumps({"process_instance_id": "INST-UNKNOWN"}),
        "mapped_result_json": "{}",
    }

    candidate = build_evidence_candidates([attachment], approval_detail={"ok": False})[0]

    assert candidate["workflow_stage"] == "payment"
    assert candidate["workflow_source"] == "attachment_cache"


def test_evidence_candidates_without_inventory_fall_back_to_the_attachment_cache() -> None:
    """拿不到审批清单时保留本地缓存判定，不把候选整体丢掉。"""

    attachments = [
        {
            "name": "ATT-CACHE",
            "file_name": "evidence.pdf",
            "source_type": "OA",
            "parse_status": "Parsed",
            "parse_result_json": json.dumps(
                {"process_instance_id": "INST-X", "approval_role": "purchase"}
            ),
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments, approval_detail={"ok": False})[0]

    assert candidate["workflow_stage"] == "purchase"
    assert candidate["workflow_source"] == "attachment_cache"


@pytest.mark.parametrize(
    "file_name,expected",
    [
        ("国际物流凭证.pdf", True),
        ("运费凭证-202609.pdf", True),
        ("凭证.jpg", True),
        ("commercial_invoice.pdf", False),
        ("装箱单.xlsx", False),
        ("", False),
        (None, False),
    ],
)
def test_evidence_candidates_flag_voucher_file_names(file_name, expected) -> None:
    """文件名含“凭证”的资料要被标出来，其余资料不能被误标。"""

    attachments = [
        {
            "name": "ATT-VOUCHER",
            "file_name": file_name,
            "source_type": "OA",
            "parse_status": "Parsed",
            "parse_result_json": "{}",
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments)[0]

    assert candidate["is_voucher_name"] is expected


def test_voucher_marker_is_the_single_shared_token() -> None:
    """高亮判定只认一个服务端常量，前端不再维护第二份关键词。"""

    assert fee_service.VOUCHER_FILE_NAME_MARKER == "凭证"
    assert fee_service.is_voucher_file_name("XX凭证YY") is True
    assert fee_service.is_voucher_file_name("voucher.pdf") is False


def test_evidence_candidates_carry_no_material_category() -> None:
    """候选不带资料分类，分组只按审批归属。

    曾经用 ``attachment_type`` 把装箱单一类判成“批次其他资料”并单独成组，结果三个
    渠道分组看不见自己抓到的资料。分类已撤掉，这里锁住它不被顺手加回来：
    要看清“这条渠道抓到了什么”，就得让资料落回它所属的渠道。
    """

    attachments = [
        {
            "name": "ATT-PACKING",
            "file_name": "装箱单2026.9.5.xlsx",
            "attachment_type": "Packing List",
            "source_type": "OA",
            "parse_status": "Queued",
            "parse_result_json": "{}",
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments)[0]

    assert "attachment_category" not in candidate
    assert "attachment_category_label" not in candidate
    assert not hasattr(fee_service, "attachment_category")


@pytest.mark.parametrize(
    "parse_result,version,version_name,expected_reason",
    [
        (
            {"settlement_document": {"audit_only": True, "retired": True}},
            "V1",
            "V1",
            "资料已撤销或被替代，仅留档备查。",
        ),
        (
            {"approval_excluded": True, "exclusion_reason": "审批结果为拒绝"},
            "V1",
            "V1",
            "审批结果为拒绝",
        ),
        (
            {"cost_source_allowed": False, "exclusion_reason": "审批已撤销、终止或作废"},
            "V1",
            "V1",
            "审批已撤销、终止或作废",
        ),
        ({}, "V0", "V1", "不属于当前版本，仅审计。"),
    ],
)
def test_evidence_candidates_mark_audit_only_sources_with_a_reason(
    parse_result, version, version_name, expected_reason
) -> None:
    """真被排除的候选只加注原因，仍然留在列表里供人工确认。"""

    attachments = [
        {
            "name": "ATT-AUDIT",
            "file_name": "商业发票.pdf",
            "attachment_type": "Commercial Invoice",
            "source_type": "OA",
            "version": version,
            "parse_status": "Parsed",
            "parse_result_json": json.dumps(parse_result, ensure_ascii=False),
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments, version_name=version_name)[0]

    assert candidate["audit_only"] is True
    assert candidate["audit_only_reason"] == expected_reason


@pytest.mark.parametrize(
    "parse_result",
    [
        pytest.param({"approval_excluded": True}, id="approval_excluded"),
        pytest.param({"cost_source_allowed": False}, id="cost_source_allowed_false"),
        pytest.param({"settlement_document": {"audit_only": True, "pending_approval": True}},
                     id="descriptor_pending"),
    ],
)
def test_pending_approval_is_not_reported_as_audit_only(parse_result) -> None:
    """审批进行中是业务常态，不能标成「仅审计」误导使用者。

    这些标记在钉钉审批还在走流程时同样成立。旧实现把它们一律写成“仅审计”，
    用户看到的状态与实际不符。真排除必须另有 ``exclusion_reason`` 佐证。
    """

    attachments = [
        {
            "name": "ATT-PENDING",
            "file_name": "装箱单.xlsx",
            "attachment_type": "Packing List",
            "source_type": "OA",
            "version": "V1",
            "parse_status": "Parsed",
            "parse_result_json": json.dumps(parse_result, ensure_ascii=False),
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments, version_name="V1")[0]

    assert candidate["audit_only"] is False
    assert candidate["audit_only_reason"] == ""


def test_evidence_candidates_without_source_flags_are_not_audit_only() -> None:
    """回归护栏：没有任何失效标记的候选不得被误标为仅审计。"""

    attachments = [
        {
            "name": "ATT-OK",
            "file_name": "运费账单9月.pdf",
            "attachment_type": "Logistics Bill",
            "source_type": "OA",
            "version": "V1",
            "parse_status": "Parsed",
            "parse_result_json": "{}",
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments, version_name="V1")[0]

    assert candidate["audit_only"] is False
    assert candidate["audit_only_reason"] == ""


def test_versionless_attachments_are_not_judged_as_audit_only() -> None:
    """无版本的附件不判仅审计。

    月结付款这类不在国际物流关联链里的资料本来就可能没有版本，
    照搬 import_service 里“OA 且无版本即 audit_only”的判据会把它们误判。
    """

    attachments = [
        {
            "name": "ATT-NO-VERSION",
            "file_name": "月结运费付款申请.pdf",
            "attachment_type": "Commercial Invoice",
            "source_type": "OA",
            "version": None,
            "parse_status": "Queued",
            "parse_result_json": "{}",
            "mapped_result_json": "{}",
        }
    ]

    candidate = build_evidence_candidates(attachments, version_name="V1")[0]

    assert candidate["audit_only"] is False


def _attachment_row(name, file_name, *, version, file_url="", parse_result=None, attachment_type="Logistics Bill", parse_status="Parsed"):
    return {
        "name": name,
        "file_name": file_name,
        "file_url": file_url,
        "attachment_type": attachment_type,
        "source_type": "OA",
        "version": version,
        "parse_status": parse_status,
        "parse_result_json": json.dumps(parse_result or {}, ensure_ascii=False),
        "mapped_result_json": "{}",
    }


def test_same_file_registered_twice_in_one_version_merges_into_one_candidate() -> None:
    """同一版本里同一份文件的重复登记归并为一条候选。

    线上实况：OA 同步把同一份报价单在同一版本下重复登记成多条，其中一条带
    ``file_url`` 可用于解析，其余 ``file_url`` 为空。界面上表现为同一份文件
    出现两次、类型和状态还不一样，用户无从选择，所以候选池只摆一条出来。
    """

    attachments = [
        _attachment_row(
            "ATT-FULL",
            "sisa报价3420元-立方.png",
            version="V1",
            file_url="/private/files/sisa报价3420元-立方2345f0.png",
        ),
        _attachment_row(
            "ATT-SHELL",
            "sisa报价3420元-立方.png",
            version="V1",
            attachment_type="Other",
            parse_status="Draft",
        ),
    ]

    candidates = build_evidence_candidates(attachments, version_name="V1")

    assert len(candidates) == 1
    # 留下的是能打开、能解析的那一份，而不是空壳。
    assert candidates[0]["attachment"] == "ATT-FULL"
    assert candidates[0]["file_url"] == "/private/files/sisa报价3420元-立方2345f0.png"


def test_same_file_across_versions_keeps_one_candidate_per_version() -> None:
    """跨版本的同名副本各自保留。

    “哪些副本不属于当前版本”本身就是要讲给用户的审计信息，归并不能连它一起吃掉。
    """

    attachments = [
        _attachment_row("ATT-V1", "报价单.png", version="V1"),
        _attachment_row("ATT-V0", "报价单.png", version="V0"),
    ]

    candidates = build_evidence_candidates(attachments, version_name="V1")

    assert [row["attachment"] for row in candidates] == ["ATT-V1", "ATT-V0"]
    assert [row["audit_only"] for row in candidates] == [False, True]


def test_merging_duplicates_prefers_the_copy_that_can_enter_costing() -> None:
    """重复副本里优先留下能进核算的那一份，而不是已失效的副本。"""

    attachments = [
        _attachment_row(
            "ATT-RETIRED",
            "运费账单.pdf",
            version="V1",
            file_url="/private/files/运费账单-retired.pdf",
            parse_result={"settlement_document": {"audit_only": True, "retired": True}},
        ),
        _attachment_row("ATT-LIVE", "运费账单.pdf", version="V1"),
    ]

    candidates = build_evidence_candidates(attachments, version_name="V1")

    assert len(candidates) == 1
    assert candidates[0]["attachment"] == "ATT-LIVE"
    assert candidates[0]["audit_only"] is False


def test_merged_candidate_borrows_the_file_url_from_the_sibling_copy() -> None:
    """留下的副本缺文件地址时，从同版本同文件的其它副本借一个。

    线上实况：能进核算的那条 ``file_url`` 为空，已被替代的副本反而有地址。
    不借的话，界面上留下的候选既打不开也解析不了。
    """

    attachments = [
        _attachment_row("ATT-LIVE", "运费账单.pdf", version="V1"),
        _attachment_row(
            "ATT-RETIRED",
            "运费账单.pdf",
            version="V1",
            file_url="/private/files/运费账单-retired.pdf",
            parse_result={"settlement_document": {"audit_only": True, "retired": True}},
        ),
    ]

    candidate = build_evidence_candidates(attachments, version_name="V1")[0]

    assert candidate["attachment"] == "ATT-LIVE"
    assert candidate["audit_only"] is False
    assert candidate["file_url"] == "/private/files/运费账单-retired.pdf"


def test_worklist_evidence_rows_expose_the_attachment_file_url(monkeypatch) -> None:
    """工作清单里的已关联凭证要带上文件地址。

    ``Overseas Cost Fee Evidence.attachment`` 只存链接，文件地址的真源是
    ``Overseas Cost Attachment``；不补这一步，界面上的「预览」拿到空地址，只能变灰。
    """

    rules = build_default_fee_templates("SEA")
    for rule in rules:
        rule.update(amount_status="NOT_INCURRED", remark="未发生", name=rule["logical_fee_key"])
    rules[0].update(amount_status="ACTUAL", amount="10", currency="RMB")
    items = [{"name": "A", "stable_line_key": "A", "goods_value": "100", "quantity": "1", "purchase_uom": "件"}]

    class FakeDb:
        @staticmethod
        def get_value(doctype, name, fieldname, **kwargs):
            if isinstance(fieldname, list):
                return {}
            return {"batch": "B", "current_version": "V", "transport_mode": "SEA"}.get(fieldname)

    class FakeFrappe:
        db = FakeDb()

        @staticmethod
        def get_all(doctype, **kwargs):
            return {
                "Overseas Cost Item": items,
                "Overseas Cost Fee Evidence": [
                    {
                        "name": "E",
                        "fee_rule": rules[0]["name"],
                        "attachment": "ATT-1",
                        "evidence_role": "freight_invoice",
                        "validation_status": "VALID",
                    }
                ],
                "Overseas Cost Attachment": [{"name": "ATT-1", "file_url": "/private/files/bill.png"}],
            }.get(doctype, [])

    monkeypatch.setattr(fee_service, "frappe", FakeFrappe())
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: rules)

    worklist = fee_service.get_fee_worklist("B", "V")
    urls = [row["file_url"] for fee in worklist["fees"] for row in (fee.get("evidence") or [])]

    assert urls == ["/private/files/bill.png"]


def test_attach_evidence_file_urls_skips_the_query_when_nothing_is_linked(monkeypatch) -> None:
    """没有已关联凭证时不去查附件表，也不给凭证行留一个假的地址。"""

    class FakeFrappe:
        @staticmethod
        def get_all(*_args, **_kwargs):
            raise AssertionError("没有已关联凭证时不应查询附件表")

    monkeypatch.setattr(fee_service, "frappe", FakeFrappe())

    assert fee_service.attach_evidence_file_urls([]) == []
    orphan = fee_service.attach_evidence_file_urls([{"name": "E", "attachment": ""}])
    assert orphan[0]["file_url"] == ""
