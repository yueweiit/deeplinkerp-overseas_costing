from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from overseas_costing.services import review_communication_service as service


ROOT = Path(__file__).resolve().parents[1]
MIRROR_ROOT = ROOT / "overseas_costing"


@pytest.fixture(params=[ROOT, MIRROR_ROOT], ids=["canonical", "frappe_module"])
def audit_controller(monkeypatch, request):
    # Replace the unavailable Frappe base class, and execute the real controller.
    frappe = ModuleType("frappe")
    model = ModuleType("frappe.model")
    document = ModuleType("frappe.model.document")
    document.Document = object
    path = request.param / "doctype/overseas_cost_audit_log/overseas_cost_audit_log.py"
    spec = importlib.util.spec_from_file_location("review_audit_controller_test", path)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as scope:
        for name, value in (("frappe", frappe), ("frappe.model", model),
                            ("frappe.model.document", document)):
            scope.setitem(sys.modules, name, value)
        spec.loader.exec_module(module)
    return module.OverseasCostAuditLog


@pytest.mark.parametrize("action", ["REVIEW_RETURN", "REVIEW_REPLY", "REVIEW_RESUBMIT", "REVIEW_RESOLVE"])
def test_real_audit_controller_accepts_review_actions(audit_controller, action):
    audit_controller.validate(SimpleNamespace(batch="B-1", action_type=action))


def test_real_audit_controller_still_rejects_unknown_actions(audit_controller):
    with pytest.raises(ValueError, match="UNKNOWN_ACTION"):
        audit_controller.validate(SimpleNamespace(batch="B-1", action_type="UNKNOWN_ACTION"))


@pytest.mark.parametrize("root", [ROOT, MIRROR_ROOT], ids=["canonical", "frappe_module"])
def test_audit_select_metadata_allows_all_review_actions(root):
    meta = json.loads((root / "doctype/overseas_cost_audit_log/overseas_cost_audit_log.json").read_text())
    options = next(field["options"] for field in meta["fields"] if field["fieldname"] == "action_type").splitlines()
    assert {"REVIEW_RETURN", "REVIEW_REPLY", "REVIEW_RESUBMIT", "REVIEW_RESOLVE"} <= set(options)


def _doctype(name: str) -> dict:
    path = ROOT / "doctype" / name / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    ("overseas_cost_review_round", "overseas_cost_review_issue"),
)
def test_review_communication_doctypes_are_mirrored(name: str) -> None:
    source = _doctype(name)
    mirror = json.loads(
        (MIRROR_ROOT / "doctype" / name / f"{name}.json").read_text(encoding="utf-8")
    )

    assert source["doctype"] == "DocType"
    assert mirror == source


def test_review_round_schema_captures_versioned_workflow_without_assignment() -> None:
    fields = {row["fieldname"]: row for row in _doctype("overseas_cost_review_round")["fields"]}

    assert fields["batch"]["options"] == "Overseas Cost Batch"
    assert fields["version"]["options"] == "Overseas Cost Version"
    assert fields["status"]["options"] == "Returned\nResubmitted\nResolved"
    assert fields["trial_signature"]["read_only"] == 1
    assert {"returned_by", "returned_at", "resubmitted_by", "resubmitted_at", "resolved_by", "resolved_at"} <= fields.keys()
    assert "responsible_party" not in fields
    assert "assignee" not in fields


def test_review_issue_schema_keeps_optional_anchor_reply_and_audit_fields() -> None:
    fields = {row["fieldname"]: row for row in _doctype("overseas_cost_review_issue")["fields"]}

    assert fields["review_round"]["options"] == "Overseas Cost Review Round"
    assert fields["status"]["options"] == "Open\nAddressed\nResolved"
    assert fields["description"]["reqd"] == 1
    assert fields["target_tab"]["reqd"] == 0
    assert fields["attachment_manifest_json"]["fieldtype"] == "Long Text"
    assert {"reply", "addressed_by", "addressed_at", "resolved_by", "resolved_at"} <= fields.keys()
    assert "issue_type" not in fields
    assert "responsible_party" not in fields


def test_project_review_state_handles_none_returned_resubmitted_and_resolved() -> None:
    from overseas_costing.services.review_communication_service import project_review_state

    assert project_review_state(None) == {
        "remediation_state": "none",
        "round_name": "",
        "round_no": 0,
        "issue_count": 0,
        "unresolved_count": 0,
        "addressed_count": 0,
        "erp_blocked": False,
    }
    returned = project_review_state(
        {"name": "ROUND-1", "round_no": 1, "status": "Returned"},
        [
            {"name": "I-1", "status": "Open"},
            {"name": "I-2", "status": "Addressed"},
        ],
    )
    assert returned == {
        "remediation_state": "returned",
        "round_name": "ROUND-1",
        "round_no": 1,
        "issue_count": 2,
        "unresolved_count": 1,
        "addressed_count": 1,
        "erp_blocked": True,
    }
    assert project_review_state(
        {"name": "ROUND-1", "round_no": 1, "status": "Resubmitted"},
        [{"status": "Addressed"}],
    )["remediation_state"] == "resubmitted"
    resolved = project_review_state(
        {"name": "ROUND-1", "round_no": 1, "status": "Resolved"},
        [{"status": "Resolved"}],
    )
    assert resolved["remediation_state"] == "resolved"
    assert resolved["erp_blocked"] is False


def test_normalize_issue_draft_requires_text_and_preserves_only_supported_anchor() -> None:
    from overseas_costing.services.review_communication_service import normalize_issue_draft

    with pytest.raises(ValueError, match="整改问题不能为空"):
        normalize_issue_draft({"description": "  "}, order_no=1)

    assert normalize_issue_draft(
        {
            "description": "  清关费金额与凭证不一致  ",
            "target_tab": "documents",
            "target_field": "clearance_fee",
            "target_row": "RULE-1",
            "target_item": "",
            "attachments": ["FILE-1", "FILE-1", "FILE-2"],
            "issue_type": "must-drop",
            "responsible_party": "must-drop",
        },
        order_no=2,
    ) == {
        "order_no": 2,
        "description": "清关费金额与凭证不一致",
        "target_tab": "documents",
        "target_field": "clearance_fee",
        "target_row": "RULE-1",
        "target_item": "",
        "attachments": ["FILE-1", "FILE-2"],
    }


class FakeReviewRepository:
    def __init__(self) -> None:
        self.context = {
            "batch_name": "B-1",
            "version_name": "V-1",
            "trial_signature": "HASH-1",
            "result_is_current": True,
            "cost_review_eligible": True,
            "confirmed": False,
        }
        self.rounds: list[dict] = []
        self.issues: list[dict] = []
        self.audits: list[dict] = []
        self.attached: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0

    def lock_cost_context(self, batch_name, version_name=None):
        assert batch_name == "B-1"
        if version_name and version_name != self.context["version_name"]:
            raise ValueError("版本已变化，请刷新后重试。")
        return dict(self.context)

    def load_active_round(self, batch_name, for_update=False):
        del batch_name, for_update
        return next((row for row in reversed(self.rounds) if row["status"] != "Resolved"), None)

    def next_round_no(self, batch_name):
        del batch_name
        return len(self.rounds) + 1

    def validate_anchor(self, batch_name, version_name, draft):
        del batch_name, version_name
        if draft.get("target_item") == "FOREIGN":
            raise ValueError("整改问题关联的物料不属于当前批次。")

    def file_manifest(self, names):
        if "FOREIGN-FILE" in names:
            raise ValueError("整改附件不存在或不可用。")
        return [{"name": name, "file_name": f"{name}.png", "file_url": f"/private/files/{name}.png"} for name in names]

    def insert_round(self, values):
        row = {**values, "name": f"ROUND-{len(self.rounds) + 1}", "modified": "2026-09-21 10:00:00"}
        self.rounds.append(row)
        return row

    def insert_issue(self, values):
        row = {**values, "name": f"ISSUE-{len(self.issues) + 1}", "modified": "2026-09-21 10:00:00"}
        self.issues.append(row)
        return row

    def attach_file(self, file_name, issue_name):
        self.attached.append((file_name, issue_name))

    def insert_audit(self, **values):
        self.audits.append(values)

    def get_issue_for_update(self, batch_name, issue_name):
        return next((row for row in self.issues if row["batch"] == batch_name and row["name"] == issue_name), None)

    def get_round_for_update(self, batch_name, round_name):
        return next((row for row in self.rounds if row["batch"] == batch_name and row["name"] == round_name), None)

    def list_round_issues(self, round_name, for_update=False):
        del for_update
        return [row for row in self.issues if row["review_round"] == round_name]

    def update_issue(self, issue_name, values):
        row = next(row for row in self.issues if row["name"] == issue_name)
        row.update(values, modified="2026-09-21 11:00:00")
        return row

    def update_round(self, round_name, values):
        row = next(row for row in self.rounds if row["name"] == round_name)
        row.update(values, modified="2026-09-21 11:00:00")
        return row

    def list_rounds(self, batch_name):
        return [row for row in reversed(self.rounds) if row["batch"] == batch_name]

    def list_issues(self, batch_name):
        return [row for row in self.issues if row["batch"] == batch_name]

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_return_for_remediation_writes_one_round_ordered_issues_and_audit(monkeypatch) -> None:
    repo = FakeReviewRepository()
    monkeypatch.setattr(service, "_repository", lambda: repo)
    monkeypatch.setattr(service, "_current_user", lambda: "finance@example.com")
    monkeypatch.setattr(service, "_now", lambda: "2026-09-21 10:00:00")

    result = service.return_for_remediation(
        "B-1",
        "V-1",
        "HASH-1",
        [
            {"description": "清关费金额不一致", "target_tab": "documents", "target_row": "RULE-1", "attachments": ["FILE-1"]},
            {"description": "请说明快递费分摊口径"},
        ],
    )

    assert result["ok"] is True
    assert result["round"]["status"] == "Returned"
    assert [row["order_no"] for row in repo.issues] == [1, 2]
    assert [row["status"] for row in repo.issues] == ["Open", "Open"]
    assert repo.attached == [("FILE-1", "ISSUE-1")]
    assert repo.audits[-1]["action_type"] == "REVIEW_RETURN"
    assert repo.commits == 1
    assert repo.rollbacks == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda repo: repo.context.update(trial_signature="CHANGED"), "试算结果已变化"),
        (lambda repo: repo.rounds.append({"name": "ACTIVE", "batch": "B-1", "version": "V-1", "status": "Returned"}), "已有未完成的整改轮次"),
    ],
)
def test_return_rejects_stale_trial_or_duplicate_active_round(monkeypatch, mutate, message) -> None:
    repo = FakeReviewRepository()
    mutate(repo)
    monkeypatch.setattr(service, "_repository", lambda: repo)

    with pytest.raises(ValueError, match=message):
        service.return_for_remediation("B-1", "V-1", "HASH-1", [{"description": "问题"}])

    assert repo.issues == []
    assert repo.commits == 0
    assert repo.rollbacks == 1


def test_address_resubmit_and_finance_resolution_follow_round_state(monkeypatch, audit_controller) -> None:
    repo = FakeReviewRepository()
    insert_audit = repo.insert_audit

    def validated_audit(**values):
        audit_controller.validate(SimpleNamespace(**values))
        insert_audit(**values)

    monkeypatch.setattr(repo, "insert_audit", validated_audit)
    monkeypatch.setattr(service, "_repository", lambda: repo)
    monkeypatch.setattr(service, "_current_user", lambda: "buyer@example.com")
    monkeypatch.setattr(service, "_now", lambda: "2026-09-21 11:00:00")
    created = service.return_for_remediation("B-1", "V-1", "HASH-1", [{"description": "请修正清关费"}])
    issue = created["issues"][0]

    addressed = service.address_review_issue("B-1", issue["name"], "已修正并重新试算", issue["modified"])
    assert addressed["issue"]["status"] == "Addressed"
    assert addressed["issue"]["addressed_by"] == "buyer@example.com"

    repo.context["trial_signature"] = "HASH-2"
    resubmitted = service.resubmit_review_round("B-1", created["round"]["name"], created["round"]["modified"])
    assert resubmitted["round"]["status"] == "Resubmitted"
    assert resubmitted["round"]["trial_signature"] == "HASH-2"

    monkeypatch.setattr(service, "_current_user", lambda: "finance@example.com")
    resolved = service.resolve_review_issue("B-1", issue["name"], addressed["issue"]["modified"])
    assert resolved["issue"]["status"] == "Resolved"
    assert resolved["round"]["status"] == "Resolved"
    assert [row["action_type"] for row in repo.audits] == [
        "REVIEW_RETURN",
        "REVIEW_REPLY",
        "REVIEW_RESUBMIT",
        "REVIEW_RESOLVE",
    ]


def test_resubmit_requires_all_issues_addressed_and_current_trial(monkeypatch) -> None:
    repo = FakeReviewRepository()
    monkeypatch.setattr(service, "_repository", lambda: repo)
    created = service.return_for_remediation("B-1", "V-1", "HASH-1", [{"description": "问题"}])

    with pytest.raises(ValueError, match="全部回复并标记已处理"):
        service.resubmit_review_round("B-1", created["round"]["name"], created["round"]["modified"])

    repo.issues[0]["status"] = "Addressed"
    repo.context["result_is_current"] = False
    with pytest.raises(ValueError, match="重新试算"):
        service.resubmit_review_round("B-1", created["round"]["name"], created["round"]["modified"])


def test_get_review_communication_returns_current_round_and_history(monkeypatch) -> None:
    repo = FakeReviewRepository()
    monkeypatch.setattr(service, "_repository", lambda: repo)
    first = service.return_for_remediation("B-1", "V-1", "HASH-1", [{"description": "第一轮"}])
    repo.rounds[0]["status"] = "Resolved"
    repo.issues[0]["status"] = "Resolved"
    second = service.return_for_remediation("B-1", "V-1", "HASH-1", [{"description": "第二轮"}])

    result = service.get_review_communication("B-1")

    assert result["current_round"]["name"] == second["round"]["name"]
    assert result["history"][0]["name"] == first["round"]["name"]
    assert result["projection"]["unresolved_count"] == 1


def test_snapshot_eligibility_accepts_explicitly_confirmed_zero_purchase_value() -> None:
    snapshot = {
        "purchase_goods_value_rmb": "0",
        "comprehensive_cost": {
            "items": [{
                "goods_value_rmb": "0",
                "valuation_source": {"amount_rmb": "0", "status": "manual", "error": ""},
            }],
        },
    }

    assert service.snapshot_cost_review_eligible(snapshot) is True


def test_snapshot_eligibility_rejects_placeholder_zero_purchase_value() -> None:
    snapshot = {
        "purchase_goods_value_rmb": "0",
        "comprehensive_cost": {
            "items": [{"goods_value_rmb": "0", "valuation_source": None}],
        },
    }

    assert service.snapshot_cost_review_eligible(snapshot) is False
