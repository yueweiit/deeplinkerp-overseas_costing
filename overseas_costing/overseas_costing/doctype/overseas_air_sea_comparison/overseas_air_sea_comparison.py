"""Enforce authoritative calculation on RPC and ordinary Frappe REST writes."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import json
import uuid
import frappe
from frappe.model.document import Document
from overseas_costing.services import air_sea_records


class OverseasAirSeaComparison(Document):
    def autoname(self):
        # Native REST creates need a name too. Avoid Frappe's hash collision retry:
        # RPC deterministic-name collisions must reach its idempotency handler.
        self.name = "ASC-" + uuid.uuid4().hex

    def validate(self):
        try:
            before = self.get_doc_before_save()
            if before:
                # REST applies request fields before permission checks. Check persisted provenance too.
                air_sea_records._access(before, "write")
            values = air_sea_records.prepare_values(self.title, self.payload_json)
            digest = air_sea_records.request_digest(values["title"], json.loads(values["payload_json"]))
            if before:
                if self.create_request_id != before.create_request_id or self.create_request_hash != before.create_request_hash:
                    raise ValueError(_("不能修改记录的创建标识。"))
                if self.last_request_id == before.last_request_id and digest != before.last_request_hash:
                    self.last_request_id = str(uuid.uuid4())
            else:
                self.create_request_id = air_sea_records.validate_request_id(self.create_request_id or str(uuid.uuid4()))
                self.create_request_hash = digest
            self.last_request_id = air_sea_records.validate_request_id(self.last_request_id or self.create_request_id)
            self.last_request_hash = digest
            self.update(values)
        except ValueError as exc:
            frappe.throw(str(exc))

    def on_trash(self):
        air_sea_records._access(self, "delete")
