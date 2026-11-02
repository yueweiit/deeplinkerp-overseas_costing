"""钉钉国际物流来源只允许服务端同步维护。"""

from types import SimpleNamespace

import pytest

from overseas_costing.doctype.overseas_cost_batch import overseas_cost_batch


@pytest.fixture(autouse=True)
def plain_messages(monkeypatch):
    monkeypatch.setattr(overseas_cost_batch, "_", lambda message: message)

    def reject(message):
        raise ValueError(message)

    monkeypatch.setattr(overseas_cost_batch.frappe, "throw", reject)


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


def test_oa_source_fields_cannot_be_changed_by_document_save():
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
            overseas_cost_batch.OverseasCostBatch._validate_oa_source_provenance(batch)

    batch = FakeBatch({**before, "extra_json": '{"approval_result":"agree","excluded":false,"note":"ok"}'}, before=before)
    overseas_cost_batch.OverseasCostBatch._validate_oa_source_provenance(batch)


def test_oa_source_requires_server_import_for_new_document():
    batch = FakeBatch({"source_type": "oa_logistics"}, is_new=True)
    with pytest.raises(ValueError, match="只能通过审批导入"):
        overseas_cost_batch.OverseasCostBatch._validate_oa_source_provenance(batch)

    imported = FakeBatch({"source_type": "oa_logistics"}, is_new=True, import_write=True)
    overseas_cost_batch.OverseasCostBatch._validate_oa_source_provenance(imported)
