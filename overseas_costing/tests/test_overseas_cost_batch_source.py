"""钉钉国际物流来源只允许服务端同步维护。"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

@pytest.fixture(params=["canonical", "frappe_module"])
def batch_controller(monkeypatch, request):
    # Execute the actual controller with an isolated import shim. These tests
    # pass FakeBatch documents and do not need a bench or a database.
    def reject(message):
        raise ValueError(message)

    frappe = ModuleType("frappe")
    frappe._ = lambda message: message
    frappe.throw = reject
    model = ModuleType("frappe.model")
    document = ModuleType("frappe.model.document")
    document.Document = object
    root = Path(__file__).resolve().parents[1]
    path = Path("doctype/overseas_cost_batch/overseas_cost_batch.py")
    name = "overseas_costing.doctype.overseas_cost_batch.overseas_cost_batch"
    spec = importlib.util.spec_from_file_location(name, root / path)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as scope:
        scope.setitem(sys.modules, "frappe", frappe)
        scope.setitem(sys.modules, "frappe.model", model)
        scope.setitem(sys.modules, "frappe.model.document", document)
        scope.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        if request.param == "frappe_module":
            nested = importlib.util.spec_from_file_location("test_cost_batch_nested", root / "overseas_costing" / path)
            module = importlib.util.module_from_spec(nested)
            nested.loader.exec_module(module)
    return module.OverseasCostBatch


class FakeBatch(dict):
    def __init__(self, values, *, before=None, is_new=False, import_write=False):
        super().__init__(values)
        self.before = FakeBatch(before) if isinstance(before, dict) else before
        self.new = is_new
        self.flags = SimpleNamespace(ignore_permissions=import_write)

    def __getattr__(self, key):
        return self.get(key)

    def is_new(self):
        return self.new

    def get_doc_before_save(self):
        return self.before


def test_oa_source_fields_cannot_be_changed_by_document_save(batch_controller):
    before = {
        "source_type": "oa_logistics", "source_instance_id": "OA-1",
        "source_approval_status": "COMPLETED", "source_created_at": "2026-01-30 00:30:00",
        "extra_json": '{"approval_result":"agree","excluded":false}',
    }
    for changed in (
        {"source_created_at": "2026-01-29 00:30:00"},
        {"source_instance_id": "OA-2"},
        {"source_approval_no": "APPROVAL-2"},
        {"source_approval_status": "REJECTED"},
        {"source_type": "manual"},
        {"extra_json": '{"approval_result":"agree","excluded":true}'},
    ):
        batch = FakeBatch({**before, **changed}, before=before)
        with pytest.raises(ValueError, match="不可手工修改"):
            batch_controller._validate_oa_source_provenance(batch)

    batch = FakeBatch({**before, "extra_json": '{"approval_result":"agree","excluded":false,"note":"ok"}'}, before=before)
    batch_controller._validate_oa_source_provenance(batch)


def test_oa_source_requires_server_import_for_new_document(batch_controller):
    batch = FakeBatch({"source_type": "oa_logistics"}, is_new=True)
    with pytest.raises(ValueError, match="只能通过审批导入"):
        batch_controller._validate_oa_source_provenance(batch)

    imported = FakeBatch({"source_type": "oa_logistics"}, is_new=True, import_write=True)
    batch_controller._validate_oa_source_provenance(imported)
