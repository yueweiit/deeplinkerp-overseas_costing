"""Frappe document adapter; all writes share the Store transaction and never commit here."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from .writer import clean_copy

DOCTYPES = {'batch': 'Overseas Cost Batch', 'version': 'Overseas Cost Version',
            'item': 'Overseas Cost Item', 'rule': 'Overseas Cost Allocation Rule', 'attachment': 'Overseas Cost Attachment',
            'evidence':'Overseas Cost Fee Evidence', 'component':'Overseas Cost Fee SKU Component'}


class FrappeLedger:
    def __init__(self):
        import frappe
        self.frappe = frappe

    def get(self, kind, name, lock=False):
        doctype = DOCTYPES[kind]
        rows = self.frappe.db.sql(f'SELECT * FROM `tab{doctype}` WHERE name=%s' + (' FOR UPDATE' if lock else ''), (name,), as_dict=True)
        return dict(rows[0]) if rows else None

    def rows(self, kind, **filters):
        return [dict(r) for r in self.frappe.get_all(DOCTYPES[kind], filters=filters, fields=['*'], limit_page_length=0)]

    def fields(self, kind, values):
        meta = self.frappe.get_meta(DOCTYPES[kind])
        return {key: value for key, value in clean_copy(values).items() if meta.has_field(key)}

    def create(self, kind, values):
        return self.frappe.get_doc({'doctype': DOCTYPES[kind], **self.fields(kind, values)}).insert(ignore_permissions=True).as_dict()

    def put(self, kind, name, values):
        self.frappe.db.set_value(DOCTYPES[kind], name, self.fields(kind, values), update_modified=True)
        return self.get(kind, name)

    def patch_batch_metadata(self, name, values):
        """Patch source metadata without invalidating active batch edit tokens."""
        allowed = {'waybill_no', 'extra_json'}
        if set(values) - allowed:
            raise ValueError(_('批次后台元数据包含不允许的字段'))
        filtered = self.fields('batch', values)
        if set(filtered) != set(values):
            raise ValueError(_('批次后台元数据字段不存在'))
        self.frappe.db.set_value(
            DOCTYPES['batch'], name, filtered, update_modified=False,
        )
        return self.get('batch', name)

    def delete(self, kind, name):
        self.frappe.delete_doc(DOCTYPES[kind], name, ignore_permissions=True)
