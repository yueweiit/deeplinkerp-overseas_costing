"""小型 SQL 存储层：生产使用 Frappe/MariaDB，SQLite 用于真实事务约束测试。"""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from contextlib import contextmanager
import json
import uuid
import re

from .model import digest, dumps, norm

TABLES = {
    'freight_line': 'source_id VARCHAR(64) NOT NULL, snapshot VARCHAR(64) NOT NULL, line_key VARCHAR(64) NOT NULL, waybill VARCHAR(160) NOT NULL, approval_no VARCHAR(160) NOT NULL, charge_key VARCHAR(64) NOT NULL',
    'freight_candidate': 'logistics_id VARCHAR(64) NOT NULL, expense_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, UNIQUE(logistics_id, expense_id)',
    'freight_claim': 'batch VARCHAR(140) NOT NULL, logistics_id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, line_id VARCHAR(64) NOT NULL, charge_key VARCHAR(64) NOT NULL UNIQUE',
    'freight_application': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, revision VARCHAR(64) NOT NULL, UNIQUE(batch, version, revision)',
    'payment_preview': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, logistics_id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, revision VARCHAR(64) NOT NULL, expires_at VARCHAR(64) NOT NULL',
    'payment_claim': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, logistics_id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, source_snapshot VARCHAR(64) NOT NULL, source_line_id VARCHAR(64) NOT NULL, logical_fee_key VARCHAR(96) NOT NULL, amount VARCHAR(64) NOT NULL, currency VARCHAR(16) NOT NULL, status VARCHAR(32) NOT NULL, exclusive INTEGER NOT NULL, claim_key VARCHAR(160) NOT NULL UNIQUE, revision VARCHAR(64) NOT NULL',
    'payment_application': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, preview_id VARCHAR(96) NOT NULL, status VARCHAR(32) NOT NULL, revision VARCHAR(64) NOT NULL, UNIQUE(preview_id, revision)',
    'payment_evidence_pending': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, source_id VARCHAR(64) NOT NULL, document_id VARCHAR(64) NOT NULL, logical_fee_key VARCHAR(96) NOT NULL, status VARCHAR(32) NOT NULL, revision VARCHAR(64) NOT NULL',
    'packing_review': 'batch VARCHAR(140) NOT NULL, version VARCHAR(140) NOT NULL, source_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL',
    'source': 'corp VARCHAR(128) NOT NULL, instance VARCHAR(160) NOT NULL, kind VARCHAR(32) NOT NULL, snapshot VARCHAR(64) NOT NULL, match_hash VARCHAR(64) NOT NULL, updated_at VARCHAR(64) NOT NULL, UNIQUE(corp, instance)',
    'snapshot': 'source_id VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, UNIQUE(source_id, fingerprint)',
    'document': 'source_id VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL',
    'attachment_map': 'document_id VARCHAR(64) NOT NULL, version VARCHAR(140) NOT NULL, attachment VARCHAR(140) NOT NULL, UNIQUE(document_id, version)',
    'document_sync': 'source_id VARCHAR(64) NOT NULL UNIQUE, batch VARCHAR(140) NOT NULL, status VARCHAR(32) NOT NULL',
    'detail': 'source_id VARCHAR(64) NOT NULL, snapshot VARCHAR(64) NOT NULL, detail_type VARCHAR(32) NOT NULL, line_key VARCHAR(160) NOT NULL',
    'reference': 'source_id VARCHAR(64) NOT NULL, corp VARCHAR(128) NOT NULL, target_instance VARCHAR(160) NOT NULL, UNIQUE(source_id, target_instance)',
    'identifier': 'source_id VARCHAR(64) NOT NULL, corp VARCHAR(128) NOT NULL, token VARCHAR(160) NOT NULL, token_type VARCHAR(32) NOT NULL, UNIQUE(source_id, token, token_type)',
    'candidate': 'logistics_id VARCHAR(64) NOT NULL, expense_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, UNIQUE(logistics_id, expense_id)',
    'binding': 'logistics_id VARCHAR(64) NOT NULL UNIQUE, expense_id VARCHAR(64) NOT NULL UNIQUE',
    'application': 'binding_id VARCHAR(64) NOT NULL, snapshot VARCHAR(64) NOT NULL, version VARCHAR(140) NOT NULL, status VARCHAR(32) NOT NULL, UNIQUE(binding_id, snapshot, version)',
    'job': 'job_key VARCHAR(64) NOT NULL UNIQUE, status VARCHAR(32) NOT NULL, phase VARCHAR(32) NOT NULL',
    'job_item': 'job_id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, UNIQUE(job_id, source_id)',
    'manifest': 'job_id VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL, round_no INTEGER NOT NULL, UNIQUE(job_id, source_id, round_no)',
    'batch_map': 'source_id VARCHAR(64) NOT NULL UNIQUE, batch VARCHAR(140) NOT NULL UNIQUE',
    'audit': 'binding_id VARCHAR(64) NOT NULL, action VARCHAR(64) NOT NULL, created_at VARCHAR(64) NOT NULL',
    'state': 'updated_at VARCHAR(64) NOT NULL',
}


class Store:
    def __init__(self, db, sqlite=False):
        self.db, self.is_sqlite = db, sqlite

    @classmethod
    def sqlite(cls, connection):
        import sqlite3
        connection.row_factory = sqlite3.Row
        connection.create_function('OC_LS_NORM',1,norm,deterministic=True)
        return cls(connection, True)

    @classmethod
    def frappe(cls):
        import frappe
        return cls(frappe.db)

    def sql(self, query, params=()):
        if self.is_sqlite:
            cursor = self.db.execute(query.replace('%s', '?').replace(' FOR UPDATE', ''), params)
            return [dict(r) for r in cursor.fetchall()] if cursor.description else []
        return self.db.sql(query, tuple(params), as_dict=True)

    def install(self):
        for table, fields in TABLES.items():
            self.sql(f'CREATE TABLE IF NOT EXISTS oc_ls_{table} (id VARCHAR(64) PRIMARY KEY, data LONGTEXT NOT NULL, {fields})')
        for name, table, columns in [('token', 'identifier', 'corp, token'), ('candidate_status', 'candidate', 'status'), ('job_status', 'job_item', 'job_id, status'), ('source_kind', 'source', 'kind'), ('source_corp_kind', 'source', 'corp, kind'), ('reference_target', 'reference', 'corp, target_instance'), ('detail_snapshot', 'detail', 'snapshot, detail_type'), ('audit_binding', 'audit', 'binding_id, created_at'), ('document_sync_status', 'document_sync', 'status, source_id')]:
            if self.is_sqlite:
                self.sql(f'CREATE INDEX IF NOT EXISTS oc_ls_{name} ON oc_ls_{table} ({columns})')
            elif not self.sql(f"SHOW INDEX FROM oc_ls_{table} WHERE Key_name=%s", (f'oc_ls_{name}',)):
                self.sql(f'CREATE INDEX oc_ls_{name} ON oc_ls_{table} ({columns})')
        for column in ('waybill','approval_no','source_id','charge_key'):
            name='oc_ls_freight_'+column
            if self.is_sqlite:
                self.sql(f'CREATE INDEX IF NOT EXISTS {name} ON oc_ls_freight_line ({column})')
            elif not self.sql('SHOW INDEX FROM oc_ls_freight_line WHERE Key_name=%s',(name,)):
                self.sql(f'CREATE INDEX {name} ON oc_ls_freight_line ({column})')
        payment_indexes = (
            ('payment_preview_batch', 'payment_preview', 'batch, status, expires_at'),
            ('payment_claim_source', 'payment_claim', 'source_id, status, currency'),
            ('payment_claim_batch', 'payment_claim', 'batch, version, status'),
            ('payment_application_batch', 'payment_application', 'batch, version, status'),
            ('payment_evidence_pending_batch', 'payment_evidence_pending', 'batch, version, status'),
        )
        for name, table, columns in payment_indexes:
            index_name = 'oc_ls_' + name
            if self.is_sqlite:
                self.sql(f'CREATE INDEX IF NOT EXISTS {index_name} ON oc_ls_{table} ({columns})')
            elif not self.sql(f'SHOW INDEX FROM oc_ls_{table} WHERE Key_name=%s', (index_name,)):
                self.sql(f'CREATE INDEX {index_name} ON oc_ls_{table} ({columns})')
        for lock_id in ('job_lock', 'match_lock'):
            if not self.get('state', lock_id):
                self.insert('state', {'id': lock_id, 'updated_at': '', 'data': '{}'})

    @staticmethod
    def validate_columns(table, keys):
        if table not in TABLES:
            raise ValueError(_('未知数据表'))
        columns = set(re.findall(r'(\w+) (?:VARCHAR|INTEGER)', TABLES[table])) | {'id', 'data'}
        if set(keys) - columns:
            raise ValueError(_('未知查询字段'))

    def commit(self):
        self.db.commit()

    @contextmanager
    def atomic(self):
        name = 'ls_' + uuid.uuid4().hex
        self.sql(f'SAVEPOINT {name}')
        try:
            yield
            self.sql(f'RELEASE SAVEPOINT {name}')
        except Exception as exc:
            # InnoDB has already rolled back the whole transaction on deadlock.
            # Its savepoints no longer exist; keep the original conflict visible.
            if type(exc).__name__ == 'QueryDeadlockError' or (exc.args and exc.args[0] == 1213):
                raise
            self.sql(f'ROLLBACK TO SAVEPOINT {name}')
            self.sql(f'RELEASE SAVEPOINT {name}')
            raise

    def insert(self, table, values):
        self.validate_columns(table, values)
        keys = list(values)
        self.sql(f"INSERT INTO oc_ls_{table} ({','.join(keys)}) VALUES ({','.join(['%s']*len(keys))})", [values[k] for k in keys])

    def put(self, table, values):
        self.validate_columns(table, values)
        if self.get(table, values['id']):
            keys = [k for k in values if k != 'id']
            self.sql(f"UPDATE oc_ls_{table} SET {','.join(k+'=%s' for k in keys)} WHERE id=%s", [values[k] for k in keys] + [values['id']])
        else:
            self.insert(table, values)

    def get(self, table, id, lock=False):
        assert table in TABLES
        rows = self.sql(f'SELECT * FROM oc_ls_{table} WHERE id=%s' + (' FOR UPDATE' if lock else ''), (id,))
        return self.unpack(rows[0]) if rows else None

    @staticmethod
    def unpack(row):
        return {**json.loads(row['data']), **{k: v for k, v in row.items() if k != 'data'}}

    def find(self, table, *, limit=None, after=None, **filters):
        self.validate_columns(table, filters)
        where = ' AND '.join(k+'=%s' for k in filters) or '1=1'
        params = list(filters.values())
        if after:
            where += ' AND id>%s'
            params.append(after)
        suffix = ' ORDER BY id'
        if limit is not None:
            suffix += ' LIMIT %s'
            params.append(max(1, min(1000, int(limit))))
        return [self.unpack(r) for r in self.sql(f'SELECT * FROM oc_ls_{table} WHERE {where}' + suffix, params)]

    def count(self, table, **filters):
        self.validate_columns(table, filters)
        where = ' AND '.join(k+'=%s' for k in filters) or '1=1'
        return self.sql(f'SELECT COUNT(*) AS n FROM oc_ls_{table} WHERE {where}', list(filters.values()))[0]['n']

    def _approved_expense_where(self, corp, logistics_id=None):
        if self.is_sqlite:
            predicate="json_extract(data,'$.approved')=1 AND COALESCE(json_extract(data,'$.invalid'),0)=0"
        else:
            predicate="CAST(JSON_EXTRACT(data,'$.approved') AS CHAR) IN ('true','1') AND CAST(JSON_EXTRACT(data,'$.invalid') AS CHAR) IN ('false','0')"
        where=f"corp=%s AND kind='expense' AND {predicate}";params=[corp]
        if logistics_id:
            where+=" AND NOT EXISTS (SELECT 1 FROM oc_ls_freight_candidate c WHERE c.logistics_id=%s AND c.expense_id=oc_ls_source.id AND c.status='rejected')"
            params.append(logistics_id)
        return where,params

    def approved_expenses(self, corp, *, offset=0, limit=50, hints=None, logistics_id=None,
                          logistics_source_id=None, logistics_instance=None):
        offset=max(0,int(offset));limit=max(1,min(50,int(limit)))
        where,params=self._approved_expense_where(corp,logistics_id)
        scores=[];score_params=[]
        if logistics_source_id:
            exact=["EXISTS (SELECT 1 FROM oc_ls_freight_line fl JOIN oc_ls_identifier li "
                   "ON li.source_id=%s AND ((li.token_type='waybill' AND fl.waybill=li.token) "
                   "OR (li.token_type='approval' AND fl.approval_no=li.token)) "
                   "WHERE fl.source_id=oc_ls_source.id AND fl.snapshot=oc_ls_source.snapshot)"]
            exact_params=[logistics_source_id]
            if logistics_instance:
                exact.append("EXISTS (SELECT 1 FROM oc_ls_reference r WHERE r.source_id=oc_ls_source.id "
                             "AND r.corp=%s AND r.target_instance=%s)")
                exact_params.extend([corp,logistics_instance])
            scores.append('(CASE WHEN '+' OR '.join(exact)+' THEN 3000 ELSE 0 END)')
            score_params.extend(exact_params)
            scores.append("(1000 * (SELECT COUNT(*) FROM oc_ls_identifier si WHERE si.source_id=oc_ls_source.id "
                          "AND EXISTS (SELECT 1 FROM oc_ls_identifier li WHERE li.source_id=%s "
                          "AND li.token_type=si.token_type AND li.token=si.token)))")
            score_params.append(logistics_source_id)
        if self.is_sqlite:
            searchable='OC_LS_NORM(data)'
        else:
            searchable='LOWER(data)'
            for accented,plain in zip('áéíóúüñ','aeiouun'):
                searchable=f"REPLACE({searchable},'{accented}','{plain}')"
            searchable=f"REGEXP_REPLACE({searchable}, '[^a-z0-9一-鿿]', '')"
        weights={'waybill':400,'supplier':80,'project':120,'date':60,'description':40}
        for key,value in (hints or {}).items():
            if value:
                scores.append(f"(CASE WHEN {searchable} LIKE %s ESCAPE '!' THEN {weights.get(key,1)} ELSE 0 END)")
                normalized=norm(value)
                score_params.append('%'+normalized.replace('!','!!').replace('%','!%').replace('_','!_')+'%')
        order=(' + '.join(scores) if scores else '0')+' DESC, updated_at DESC, id'
        params.extend(score_params+[limit,offset])
        return [self.unpack(row) for row in self.sql(
            f"SELECT * FROM oc_ls_source WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",params)]

    def approved_expense_count(self, corp, *, logistics_id=None):
        where,params=self._approved_expense_where(corp,logistics_id)
        return self.sql(f'SELECT COUNT(*) AS n FROM oc_ls_source WHERE {where}',params)[0]['n']

    def audit(self, binding_id, action, actor, **details):
        from .jobs import utcnow
        self.insert('audit', {'id': uuid.uuid4().hex, 'binding_id': binding_id, 'action': action,
                             'created_at': utcnow(), 'data': dumps({'actor': actor, **details})})

    def ingest(self, parsed):
        source_id = parsed['id']
        with self.atomic():
            self.get('state', 'match_lock', lock=True)
            prior = self.get('source', source_id, lock=True)
            # A delayed worker may not move the current source backwards.
            if prior and prior['source_updated_at'] > parsed['source_updated_at']:
                return prior
            snapshot = digest(source_id, parsed['fingerprint'])
            if not self.get('snapshot', snapshot):
                self.insert('snapshot', {'id': snapshot, 'source_id': source_id, 'fingerprint': parsed['fingerprint'], 'data': dumps(parsed)})
                for detail_type, lines in [('goods', parsed['goods']), ('fee', parsed['fees']), ('billing', parsed.get('billing', []))]:
                    for index, line in enumerate(lines):
                        line_key = line.get('line_key') or digest(line)
                        self.insert('detail', {'id': digest(snapshot, detail_type, line_key, index), 'source_id': source_id, 'snapshot': snapshot,
                                               'detail_type': detail_type, 'line_key': line_key, 'data': dumps(line)})
            result = {**parsed, 'snapshot': snapshot}
            self.put('source', {'id': source_id, 'corp': parsed['corp'], 'instance': parsed['instance'], 'kind': parsed['kind'], 'snapshot': snapshot, 'match_hash': parsed['match_hash'], 'updated_at': parsed['source_updated_at'], 'data': dumps(result)})
            self.sql('DELETE FROM oc_ls_identifier WHERE source_id=%s', (source_id,))
            for token_type, token in parsed['identifiers']:
                self.insert('identifier', {'id': digest(source_id, token_type, token), 'source_id': source_id, 'corp': parsed['corp'], 'token_type': token_type, 'token': token, 'data': '{}'})
            self.sql('DELETE FROM oc_ls_reference WHERE source_id=%s', (source_id,))
            for instance in parsed['related']:
                self.insert('reference', {'id': digest(source_id, instance), 'source_id': source_id, 'corp': parsed['corp'], 'target_instance': instance, 'data': '{}'})
            if result['kind'] == 'expense':
                from .freight_matching import index_source
                index_source(self,result)
        return result
