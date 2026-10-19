"""成本复核整改沟通的纯规则和事务入口。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from overseas_costing.services.shipment_cost_service import is_explicit_shipment_zero

try:
    import frappe
except Exception:  # pragma: no cover - pure rule tests do not require Frappe
    frappe = None


ALLOWED_TARGET_TABS = {"overview", "dingtalk", "documents", "items", "vouchers"}


def _clean(value) -> str:
    return str(value or "").strip()


def normalize_issue_draft(value: dict, *, order_no: int) -> dict:
    """只接受用户已确认的最小问题结构，丢弃分类和责任方等扩展字段。"""

    draft = dict(value or {})
    description = _clean(draft.get("description"))
    if not description:
        raise ValueError(_("整改问题不能为空。"))
    target_tab = _clean(draft.get("target_tab"))
    if target_tab and target_tab not in ALLOWED_TARGET_TABS:
        raise ValueError(_("整改问题关联页签无效。"))
    attachments = []
    for name in draft.get("attachments") or []:
        normalized = _clean(name)
        if normalized and normalized not in attachments:
            attachments.append(normalized)
    return {
        "order_no": int(order_no),
        "description": description,
        "target_tab": target_tab,
        "target_field": _clean(draft.get("target_field")),
        "target_row": _clean(draft.get("target_row")),
        "target_item": _clean(draft.get("target_item")),
        "attachments": attachments,
    }


def project_review_state(review_round: dict | None, issues: list[dict] | None = None) -> dict:
    """把活动轮次投影为列表与 ERP 门禁共同使用的稳定字段。"""

    if not review_round:
        return {
            "remediation_state": "none",
            "round_name": "",
            "round_no": 0,
            "issue_count": 0,
            "unresolved_count": 0,
            "addressed_count": 0,
            "erp_blocked": False,
        }
    rows = list(issues or [])
    unresolved = sum(_clean(row.get("status")).lower() == "open" for row in rows)
    addressed = sum(_clean(row.get("status")).lower() == "addressed" for row in rows)
    state = _clean(review_round.get("status")).lower() or "returned"
    return {
        "remediation_state": state,
        "round_name": _clean(review_round.get("name")),
        "round_no": int(review_round.get("round_no") or 0),
        "issue_count": len(rows),
        "unresolved_count": unresolved,
        "addressed_count": addressed,
        "erp_blocked": state != "resolved" or unresolved > 0 or addressed > 0,
    }


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def snapshot_cost_review_eligible(snapshot: dict) -> bool:
    """Return whether the saved trial contains a complete purchase valuation.

    Explicitly confirmed zero-value samples are valid review inputs. Plain zero
    placeholders remain ineligible, matching the canonical readiness rules.
    """

    comprehensive = _json_object(snapshot.get("comprehensive_cost"))
    items = comprehensive.get("items")
    if isinstance(items, list) and items:
        for row in items:
            try:
                amount = Decimal(str(row.get("goods_value_rmb")))
            except (InvalidOperation, TypeError, ValueError):
                return False
            if amount < 0:
                return False
            if amount == 0 and not is_explicit_shipment_zero(row.get("valuation_source")):
                return False
        return True
    try:
        return Decimal(str(snapshot.get("purchase_goods_value_rmb"))) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _current_user() -> str:
    return _clean(getattr(getattr(frappe, "session", None), "user", None)) or "Guest"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _same_modified(expected, actual) -> bool:
    return not _clean(expected) or _clean(expected) == _clean(actual)


class FrappeReviewRepository:
    """Small persistence adapter so workflow rules stay independently testable."""

    ROUND_FIELDS = [
        "name", "batch", "version", "round_no", "status", "trial_signature",
        "returned_by", "returned_at", "resubmitted_by", "resubmitted_at",
        "resolved_by", "resolved_at", "creation", "modified",
    ]
    ISSUE_FIELDS = [
        "name", "batch", "version", "review_round", "order_no", "status",
        "description", "target_tab", "target_field", "target_row", "target_item",
        "attachment_manifest_json", "reply", "addressed_by", "addressed_at",
        "resolved_by", "resolved_at", "creation", "modified",
    ]

    def __init__(self, runtime) -> None:
        if runtime is None:
            raise RuntimeError("当前未连接 Frappe，不能处理成本复核整改。")
        self.frappe = runtime

    def lock_cost_context(self, batch_name, version_name=None) -> dict:
        rows = self.frappe.db.sql(
            """
            select b.name as batch_name, b.current_version, b.status as batch_status,
                   b.confirm_status, b.writeback_status, v.name as version_name,
                   v.calculated_at, v.summary_snapshot_json
            from `tabOverseas Cost Batch` b
            join `tabOverseas Cost Version` v on v.name = b.current_version and v.batch = b.name
            where b.name = %s
            for update
            """,
            (batch_name,), as_dict=True,
        )
        if not rows:
            raise ValueError(_("未找到当前批次或活动成本版本：{0}").format(batch_name))
        row = dict(rows[0])
        if version_name and _clean(version_name) != _clean(row.get("version_name")):
            raise ValueError(_("成本版本已变化，请刷新后重试。"))
        snapshot = _json_object(row.get("summary_snapshot_json"))
        signature = _clean(snapshot.get("input_hash"))
        result_current = bool(
            snapshot.get("calculation_schema") == 2 and signature and row.get("calculated_at")
            and _clean(snapshot.get("calculated_at")) == _clean(row.get("calculated_at"))
            and _clean(row.get("batch_status")).lower() not in {"draft", "dirty"}
        )
        eligible = snapshot_cost_review_eligible(snapshot)
        return {
            "batch_name": row["batch_name"], "version_name": row["version_name"],
            "trial_signature": signature, "result_is_current": result_current,
            "cost_review_eligible": eligible,
            "confirmed": _clean(row.get("confirm_status")).lower() == "confirmed",
        }

    def load_active_round(self, batch_name, for_update=False):
        suffix = " for update" if for_update else ""
        rows = self.frappe.db.sql(
            """
            select name, batch, version, round_no, status, trial_signature,
                   returned_by, returned_at, resubmitted_by, resubmitted_at,
                   resolved_by, resolved_at, creation, modified
            from `tabOverseas Cost Review Round`
            where batch = %s and status in ('Returned', 'Resubmitted')
            order by round_no desc, creation desc limit 1
            """ + suffix,
            (batch_name,), as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def next_round_no(self, batch_name):
        value = self.frappe.db.sql(
            "select coalesce(max(round_no), 0) from `tabOverseas Cost Review Round` where batch = %s",
            (batch_name,),
        )
        return int(value[0][0] or 0) + 1

    def validate_anchor(self, batch_name, version_name, draft):
        target_item = _clean(draft.get("target_item"))
        if target_item and not self.frappe.db.exists(
            "Overseas Cost Item", {"name": target_item, "batch": batch_name, "version": version_name}
        ):
            raise ValueError(_("整改问题关联的物料不属于当前批次。"))
        target_row = _clean(draft.get("target_row"))
        if target_row and not (
            self.frappe.db.exists("Overseas Cost Item", {"name": target_row, "batch": batch_name, "version": version_name})
            or self.frappe.db.exists("Overseas Cost Allocation Rule", {"name": target_row, "batch": batch_name, "version": version_name})
            or self.frappe.db.exists("Overseas Cost Fee Evidence", {"name": target_row, "batch": batch_name, "version": version_name})
        ):
            raise ValueError(_("整改问题原关联位置已变化，请刷新后重新选择。"))

    def file_manifest(self, names):
        manifest = []
        for name in names:
            row = self.frappe.db.get_value("File", name, ["name", "file_name", "file_url", "is_private"], as_dict=True)
            if not row:
                raise ValueError(_("整改附件不存在或不可用。"))
            manifest.append(dict(row))
        return manifest

    @staticmethod
    def _document_dict(doc, fields):
        return {field: getattr(doc, field, None) for field in fields}

    def insert_round(self, values):
        doc = self.frappe.get_doc({"doctype": "Overseas Cost Review Round", **values})
        doc.insert(ignore_permissions=True)
        return self._document_dict(doc, self.ROUND_FIELDS)

    def insert_issue(self, values):
        doc = self.frappe.get_doc({"doctype": "Overseas Cost Review Issue", **values})
        doc.insert(ignore_permissions=True)
        return self._document_dict(doc, self.ISSUE_FIELDS)

    def attach_file(self, file_name, issue_name):
        self.frappe.db.set_value(
            "File", file_name,
            {"attached_to_doctype": "Overseas Cost Review Issue", "attached_to_name": issue_name},
            update_modified=True,
        )

    def insert_audit(self, **values):
        self.frappe.get_doc({"doctype": "Overseas Cost Audit Log", **values}).insert(ignore_permissions=True)

    def get_issue_for_update(self, batch_name, issue_name):
        rows = self.frappe.db.sql(
            "select * from `tabOverseas Cost Review Issue` where name = %s and batch = %s for update",
            (issue_name, batch_name), as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def get_round_for_update(self, batch_name, round_name):
        rows = self.frappe.db.sql(
            "select * from `tabOverseas Cost Review Round` where name = %s and batch = %s for update",
            (round_name, batch_name), as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def list_round_issues(self, round_name, for_update=False):
        suffix = " for update" if for_update else ""
        rows = self.frappe.db.sql(
            "select * from `tabOverseas Cost Review Issue` where review_round = %s order by order_no asc" + suffix,
            (round_name,), as_dict=True,
        )
        return [dict(row) for row in rows]

    def update_issue(self, issue_name, values):
        self.frappe.db.set_value("Overseas Cost Review Issue", issue_name, values, update_modified=True)
        row = self.frappe.db.get_value("Overseas Cost Review Issue", issue_name, self.ISSUE_FIELDS, as_dict=True)
        return dict(row)

    def update_round(self, round_name, values):
        self.frappe.db.set_value("Overseas Cost Review Round", round_name, values, update_modified=True)
        row = self.frappe.db.get_value("Overseas Cost Review Round", round_name, self.ROUND_FIELDS, as_dict=True)
        return dict(row)

    def list_rounds(self, batch_name):
        return [dict(row) for row in self.frappe.get_all(
            "Overseas Cost Review Round", filters={"batch": batch_name}, fields=self.ROUND_FIELDS,
            order_by="round_no desc, creation desc", limit_page_length=0,
        )]

    def list_issues(self, batch_name):
        return [dict(row) for row in self.frappe.get_all(
            "Overseas Cost Review Issue", filters={"batch": batch_name}, fields=self.ISSUE_FIELDS,
            order_by="review_round desc, order_no asc", limit_page_length=0,
        )]

    def commit(self):
        self.frappe.db.commit()

    def rollback(self):
        self.frappe.db.rollback()


def _repository():
    return FrappeReviewRepository(frappe)


def _audit(repo, *, batch, version, action_type, field_name, old_value="", new_value="", remark=""):
    repo.insert_audit(
        batch=batch, version=version, action_type=action_type, field_name=field_name,
        old_value=_clean(old_value), new_value=_clean(new_value), operator_name=_current_user(),
        action_remark=remark,
    )


def _require_current_trial(context: dict) -> None:
    if not context.get("result_is_current"):
        raise ValueError(_("当前试算已失效，请重新试算后再操作。"))
    if not context.get("cost_review_eligible"):
        raise ValueError(_("当前批次缺少采购货值，不能进入成本核对。"))
    if context.get("confirmed"):
        raise ValueError(_("当前成本结果已确认，不能再发起整改。"))


def return_for_remediation(batch_name, version_name, trial_signature, issues) -> dict:
    repo = _repository()
    try:
        normalized = [normalize_issue_draft(row, order_no=index) for index, row in enumerate(issues or [], 1)]
        if not normalized:
            raise ValueError(_("请至少填写一项整改问题。"))
        context = repo.lock_cost_context(_clean(batch_name), _clean(version_name) or None)
        _require_current_trial(context)
        if _clean(trial_signature) != _clean(context.get("trial_signature")):
            raise ValueError(_("试算结果已变化，请刷新后重新提出整改。"))
        if repo.load_active_round(context["batch_name"], for_update=True):
            raise ValueError(_("已有未完成的整改轮次，请在复核沟通中继续处理。"))
        prepared = []
        for draft in normalized:
            repo.validate_anchor(context["batch_name"], context["version_name"], draft)
            prepared.append((draft, repo.file_manifest(draft["attachments"])))
        actor, now = _current_user(), _now()
        review_round = repo.insert_round({
            "batch": context["batch_name"], "version": context["version_name"],
            "round_no": repo.next_round_no(context["batch_name"]), "status": "Returned",
            "trial_signature": context["trial_signature"], "returned_by": actor, "returned_at": now,
        })
        created = []
        for draft, manifest in prepared:
            issue = repo.insert_issue({
                "batch": context["batch_name"], "version": context["version_name"],
                "review_round": review_round["name"], "order_no": draft["order_no"],
                "status": "Open", "description": draft["description"],
                "target_tab": draft["target_tab"], "target_field": draft["target_field"],
                "target_row": draft["target_row"], "target_item": draft["target_item"],
                "attachment_manifest_json": json.dumps(manifest, ensure_ascii=False),
            })
            for attachment in manifest:
                repo.attach_file(attachment["name"], issue["name"])
            created.append(issue)
        _audit(
            repo, batch=context["batch_name"], version=context["version_name"],
            action_type="REVIEW_RETURN", field_name="review_round",
            new_value=json.dumps({"round": review_round["name"], "issue_count": len(created)}, ensure_ascii=False),
            remark=f"财务退回整改，共 {len(created)} 项。",
        )
        repo.commit()
        return {"ok": True, "round": review_round, "issues": created, "projection": project_review_state(review_round, created)}
    except Exception:
        repo.rollback()
        raise


def address_review_issue(batch_name, issue_name, reply, expected_modified="") -> dict:
    repo = _repository()
    try:
        context = repo.lock_cost_context(_clean(batch_name))
        issue = repo.get_issue_for_update(context["batch_name"], _clean(issue_name))
        if not issue:
            raise ValueError(_("整改问题不存在或不属于当前批次。"))
        review_round = repo.get_round_for_update(context["batch_name"], issue["review_round"])
        if not review_round or review_round.get("status") != "Returned":
            raise ValueError(_("当前整改轮次不能继续回复。"))
        if not _same_modified(expected_modified, issue.get("modified")):
            raise ValueError(_("整改问题已被其他人更新，请刷新后重试。"))
        answer = _clean(reply)
        if not answer:
            raise ValueError(_("请填写处理回复。"))
        updated = repo.update_issue(issue["name"], {
            "status": "Addressed", "reply": answer, "addressed_by": _current_user(),
            "addressed_at": _now(), "resolved_by": None, "resolved_at": None,
        })
        _audit(
            repo, batch=context["batch_name"], version=context["version_name"],
            action_type="REVIEW_REPLY", field_name=issue["name"], old_value=issue.get("status"),
            new_value="Addressed", remark=answer,
        )
        repo.commit()
        return {"ok": True, "issue": updated, "round": review_round}
    except Exception:
        repo.rollback()
        raise


def resubmit_review_round(batch_name, round_name, expected_modified="") -> dict:
    repo = _repository()
    try:
        context = repo.lock_cost_context(_clean(batch_name))
        _require_current_trial(context)
        review_round = repo.get_round_for_update(context["batch_name"], _clean(round_name))
        if not review_round or review_round.get("status") != "Returned":
            raise ValueError(_("当前整改轮次不能提交财务复核。"))
        if not _same_modified(expected_modified, review_round.get("modified")):
            raise ValueError(_("整改轮次已被其他人更新，请刷新后重试。"))
        issues = repo.list_round_issues(review_round["name"], for_update=True)
        if not issues or any(row.get("status") != "Addressed" for row in issues):
            raise ValueError(_("请先全部回复并标记已处理。"))
        updated = repo.update_round(review_round["name"], {
            "status": "Resubmitted", "trial_signature": context["trial_signature"],
            "resubmitted_by": _current_user(), "resubmitted_at": _now(),
        })
        _audit(
            repo, batch=context["batch_name"], version=context["version_name"],
            action_type="REVIEW_RESUBMIT", field_name="review_round",
            old_value=_clean(review_round.get("trial_signature")), new_value=context["trial_signature"],
            remark=f"整改第 {review_round.get('round_no') or 0} 轮已提交财务复核。",
        )
        repo.commit()
        return {"ok": True, "round": updated, "issues": issues, "projection": project_review_state(updated, issues)}
    except Exception:
        repo.rollback()
        raise


def resolve_review_issue(batch_name, issue_name, expected_modified="") -> dict:
    repo = _repository()
    try:
        context = repo.lock_cost_context(_clean(batch_name))
        _require_current_trial(context)
        issue = repo.get_issue_for_update(context["batch_name"], _clean(issue_name))
        if not issue:
            raise ValueError(_("整改问题不存在或不属于当前批次。"))
        review_round = repo.get_round_for_update(context["batch_name"], issue["review_round"])
        if not review_round or review_round.get("status") != "Resubmitted" or issue.get("status") != "Addressed":
            raise ValueError(_("只有采购已处理并再次提交的问题才能由财务确认。"))
        if not _same_modified(expected_modified, issue.get("modified")):
            raise ValueError(_("整改问题已被其他人更新，请刷新后重试。"))
        actor, now = _current_user(), _now()
        updated_issue = repo.update_issue(issue["name"], {"status": "Resolved", "resolved_by": actor, "resolved_at": now})
        issues = repo.list_round_issues(review_round["name"], for_update=True)
        issues = [updated_issue if row.get("name") == updated_issue.get("name") else row for row in issues]
        updated_round = review_round
        if issues and all(row.get("status") == "Resolved" for row in issues):
            updated_round = repo.update_round(review_round["name"], {"status": "Resolved", "resolved_by": actor, "resolved_at": now})
        _audit(
            repo, batch=context["batch_name"], version=context["version_name"],
            action_type="REVIEW_RESOLVE", field_name=issue["name"], old_value="Addressed",
            new_value="Resolved", remark="财务确认整改完成。",
        )
        repo.commit()
        return {"ok": True, "issue": updated_issue, "round": updated_round, "projection": project_review_state(updated_round, issues)}
    except Exception:
        repo.rollback()
        raise


def get_review_communication(batch_name) -> dict:
    repo = _repository()
    rounds = repo.list_rounds(_clean(batch_name))
    issues = repo.list_issues(_clean(batch_name))
    grouped = {}
    for issue in issues:
        row = dict(issue)
        try:
            parsed = json.loads(row.get("attachment_manifest_json") or "[]")
            row["attachments"] = parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            row["attachments"] = []
        grouped.setdefault(row.get("review_round"), []).append(row)
    enriched = [{**row, "issues": grouped.get(row.get("name"), [])} for row in rounds]
    current = next((row for row in enriched if row.get("status") != "Resolved"), None)
    history = [row for row in enriched if not current or row.get("name") != current.get("name")]
    return {
        "ok": True, "batch_name": _clean(batch_name), "current_round": current,
        "history": history,
        "projection": project_review_state(current, current.get("issues") if current else []),
    }


def get_review_gate(batch_name, *, for_update=False, repository=None) -> dict:
    """Return the authoritative final-action gate for an active remediation round."""

    repo = repository or _repository()
    review_round = repo.load_active_round(_clean(batch_name), for_update=for_update)
    if not review_round:
        return {**project_review_state(None), "blocking_reasons": []}
    issues = repo.list_round_issues(review_round["name"], for_update=for_update)
    projection = project_review_state(review_round, issues)
    if not projection["erp_blocked"]:
        return {**projection, "blocking_reasons": []}
    if projection["remediation_state"] == "resubmitted":
        message = "财务尚未确认全部整改问题，不能确认计算结果或推送 ERP。"
    else:
        message = "当前仍有整改问题未完成，不能确认计算结果或推送 ERP。"
    return {**projection, "blocking_reasons": [message]}
