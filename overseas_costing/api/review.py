"""成本复核整改沟通 API。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json

import frappe

from overseas_costing.services import review_communication_service as service
from overseas_costing.services.access_control import require_batch_permission


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    parsed = json.loads(value or "[]")
    if not isinstance(parsed, list):
        raise ValueError(_("整改问题格式无效。"))
    return parsed


@frappe.whitelist()
def get_review_communication(batch_name: str) -> dict:
    batch_name = require_batch_permission(batch_name, "read")
    return service.get_review_communication(batch_name)


@frappe.whitelist()
def return_for_remediation(batch_name: str, version_name: str, trial_signature: str, issues_json=None) -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return service.return_for_remediation(batch_name, version_name, trial_signature, _json_list(issues_json))


@frappe.whitelist()
def address_review_issue(batch_name: str, issue_name: str, reply: str, expected_modified: str = "") -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return service.address_review_issue(batch_name, issue_name, reply, expected_modified)


@frappe.whitelist()
def resubmit_review_round(batch_name: str, round_name: str, expected_modified: str = "") -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return service.resubmit_review_round(batch_name, round_name, expected_modified)


@frappe.whitelist()
def resolve_review_issue(batch_name: str, issue_name: str, expected_modified: str = "") -> dict:
    batch_name = require_batch_permission(batch_name, "write")
    return service.resolve_review_issue(batch_name, issue_name, expected_modified)
