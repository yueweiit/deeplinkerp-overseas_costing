"""归档只读适配器；v2 包含删除记录，附件变化可唤醒历史审批。"""
from overseas_costing.services.logistics_settlement.model import dumps


class SettlementArchive:
    def __init__(self, source, *, logistics_codes, tracked_pairs=(), financial=False):
        self.source = source
        self.logistics_codes = sorted(logistics_codes)
        self.tracked_pairs = tracked_pairs
        self.financial=financial

    @staticmethod
    def _expense_category_sql():
        return "costing_read.is_logistics_purchase(COALESCE(form_component_values, raw_payload->'formComponentValues', raw_payload->'form_component_values'))"

    @staticmethod
    def _active_sql():
        return "deleted_at IS NULL AND UPPER(COALESCE(status,'')) NOT IN ('TERMINATED','CANCELED','CANCELLED','DELETED','REJECTED','WITHDRAWN','WITHDRAW','REVOKED') AND LOWER(COALESCE(result,'')) NOT IN ('refuse','reject','disagree')"

    def health(self):
        with self.source._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM costing_read.sync_health_v1 LIMIT 1')
                rows = cursor.fetchall()
                health=dict(rows[0]) if rows else {}
                if self.financial:
                    cursor.execute("SELECT corp_id,process_code,template_name,COUNT(window_start)::int AS windows,COUNT(*) FILTER(WHERE status='completed')::int AS completed_windows,COUNT(*) FILTER(WHERE status='failed')::int AS failed_windows,SUM(discovered_count)::int AS discovered_count,SUM(processed_count)::int AS processed_count,MAX(source_count)::int AS source_count,MAX(eligible_source_count)::int AS eligible_source_count,MAX(attachment_count)::int AS attachment_count,MAX(attachment_available_count)::int AS attachment_available_count,MAX(last_error) AS last_error,MAX(updated_at) AS updated_at FROM costing_read.financial_template_coverage_v1 GROUP BY corp_id,process_code,template_name ORDER BY template_name")
                    health['financial_coverage']=[dict(r) for r in cursor.fetchall()]
                return health

    def inventory_page(self, **kwargs):
        return self.page(**kwargs, lightweight=True)

    def page(self, *, cursor=None, limit=200, lower='', upper, start='', end='', lightweight=False, pairs=None):
        # Full finance payloads can be megabytes; retain larger lightweight pages.
        limit = max(1, min(20 if self.financial and not lightweight else 200, int(limit)))
        where = ['changed_at < %s']
        args = [upper]
        if lower:
            where.append('changed_at >= %s'); args.append(lower)
        if start:
            where.append('create_time >= %s'); args.append(start)
        if end:
            where.append('create_time < %s'); args.append(end)
        if cursor:
            where.append('(changed_at, corp_id, process_instance_id) > (%s, %s, %s)')
            args.extend(cursor)
        if pairs:
            where.append('(' + ' OR '.join('(corp_id=%s AND process_instance_id=%s)' for _ in pairs) + ')')
            args.extend(v for pair in pairs for v in pair)
        else:
            # Filter before pagination and payload/attachment hydration. Previously
            # adopted sources remain readable after their category is revoked.
            scope = '(process_code=ANY(%s) OR ' + ('financial_scope' if self.financial else self._expense_category_sql()) + ') AND (' + self._active_sql() + ')'
            args.append(self.logistics_codes)
            tracked_pairs = self.tracked_pairs() if callable(self.tracked_pairs) else self.tracked_pairs
            if tracked_pairs:
                scope += " OR (corp_id,process_instance_id) IN (SELECT value->>0,value->>1 FROM jsonb_array_elements(%s::jsonb))"
                args.append(dumps(tracked_pairs))
            where.append('(' + scope + ')')
        projection = 'corp_id, process_instance_id, changed_at, archive_revision' if lightweight else '*'
        sql = '''WITH changed AS (
            SELECT a.*, GREATEST(a.updated_at, COALESCE(f.attachment_updated_at, a.updated_at)) AS changed_at,
                   md5(jsonb_build_array(a.raw_payload, a.form_component_values, a.status, a.result, a.deleted_at, f.manifest_hash)::text) AS archive_revision
            FROM costing_read.approval_instances_v2 a
            LEFT JOIN (SELECT corp_id, process_instance_id, MAX(updated_at) AS attachment_updated_at,
                       string_agg(md5(jsonb_build_array(file_id, object_key, bucket, sha256, actual_size, archive_status, content_quality, retired_at)::text), ',' ORDER BY file_id) AS manifest_hash
                       FROM costing_read.attachment_archives_v2 GROUP BY corp_id, process_instance_id) f
              USING (corp_id, process_instance_id)
        ) SELECT ''' + projection + ' FROM changed WHERE ' + ' AND '.join(where) + ' ORDER BY changed_at, corp_id, process_instance_id LIMIT %s'
        if self.financial:
            sql=sql.replace('SELECT a.*, GREATEST(a.updated_at, COALESCE(f.attachment_updated_at, a.updated_at))',
                'SELECT a.*, fin.template_name, COALESCE(fin.has_transport_evidence,false) AS financial_scope, fin.transport_evidence, GREATEST(a.updated_at, COALESCE(fin.evidence_updated_at,a.updated_at), COALESCE(fin.scope_registered_at,a.updated_at), COALESCE(f.attachment_updated_at, a.updated_at))')
            sql=sql.replace('FROM costing_read.approval_instances_v2 a','FROM costing_read.approval_instances_v2 a LEFT JOIN costing_read.financial_sources_v1 fin USING (corp_id,process_instance_id)')
        args.append(limit + 1)
        with self.source._connection() as connection:
            with connection.cursor() as cur:
                cur.execute(sql, tuple(args))
                rows = [dict(r) for r in cur.fetchall()]
                has_more = len(rows) > limit
                rows = rows[:limit]
                if rows and not lightweight:
                    # Batch lookup includes tenant keys; never resolve by instance ID alone.
                    pairs = [(r['corp_id'], r['process_instance_id']) for r in rows]
                    predicates = ' OR '.join('(corp_id=%s AND process_instance_id=%s)' for _ in pairs)
                    cur.execute('SELECT * FROM costing_read.attachment_archives_v2 WHERE ' + predicates, tuple(v for p in pairs for v in p))
                    attachments = [dict(r) for r in cur.fetchall()]
                    for row in rows:
                        row['attachments'] = [a for a in attachments if (a['corp_id'], a['process_instance_id']) == (row['corp_id'], row['process_instance_id'])]
                        row['updated_at'] = row['changed_at']
                        row['raw_payload'] = self.source._raw_payload(row)
        next_cursor = [str(rows[-1]['changed_at']), rows[-1]['corp_id'], rows[-1]['process_instance_id']] if rows else cursor
        return {'items': rows, 'has_more': has_more, 'next_cursor': next_cursor}

    def get_sources(self, pairs):
        if not pairs:
            return []
        from datetime import datetime, timezone, timedelta
        # Refresh selected changed rows only; a transient absence is never a tombstone.
        upper = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rows, cursor = [], None
        while True:
            page = self.page(pairs=pairs, upper=upper, cursor=cursor, limit=200)
            rows.extend(page['items'])
            if not page['has_more']:
                return rows
            cursor = page['next_cursor']

    def get_reference_metadata(self, pairs):
        if not pairs:
            return []
        predicates = ' OR '.join('(i.corp_id=%s AND i.process_instance_id=%s)' for _ in pairs)
        query = '''SELECT i.corp_id,i.process_instance_id,i.process_code,i.title,i.status,i.result,
                   i.deleted_at,p.name AS template_name,
                   EXISTS(SELECT 1 FROM costing_read.financial_sources_v1 fin
                       WHERE fin.corp_id=i.corp_id AND fin.process_instance_id=i.process_instance_id)
                       AS registered_financial_flow
              FROM public.ding_approval_instance i
              LEFT JOIN public.ding_process_template p USING(corp_id,process_code)
             WHERE ''' + predicates
        with self.source._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(value for pair in pairs for value in pair))
                return [dict(row) for row in cursor.fetchall()]

    def preflight(self):
        with self.source._connection() as connection:
            with connection.cursor() as cursor:
                query='''SELECT CASE WHEN process_code=ANY(%s) THEN 'logistics'
                    WHEN ''' + self._expense_category_sql() + ''' THEN 'expense' ELSE 'excluded' END AS source_kind,
                    process_code, EXTRACT(YEAR FROM create_time)::int AS year,
                    status, result, deleted_at IS NOT NULL AS deleted, COUNT(*)::int AS count, MIN(create_time) AS first_created,
                    MAX(create_time) AS last_created FROM costing_read.approval_instances_v2
                    GROUP BY source_kind, process_code, EXTRACT(YEAR FROM create_time), status, result, deleted_at IS NOT NULL
                    ORDER BY year, process_code'''
                if self.financial:
                    query=query.replace(self._expense_category_sql(),"EXISTS(SELECT 1 FROM costing_read.financial_sources_v1 fin WHERE fin.corp_id=approval_instances_v2.corp_id AND fin.process_instance_id=approval_instances_v2.process_instance_id AND fin.has_transport_evidence)")
                cursor.execute(query,(self.logistics_codes,))
                inventory = [dict(r) for r in cursor.fetchall()]
        counts = {'logistics': 0, 'expense': 0, 'approved_expense': 0, 'excluded': 0, 'invalid': 0}
        active_inventory = []
        for row in inventory:
            invalid = row['deleted'] or str(row['status']).upper() in {'TERMINATED','CANCELED','CANCELLED','DELETED','REJECTED','WITHDRAWN','WITHDRAW','REVOKED'} or str(row['result']).lower() in {'refuse','reject','disagree'}
            if row['source_kind'] != 'excluded' and invalid:
                counts['invalid'] += row['count']
                continue
            counts[row['source_kind']] += row['count']
            if row['source_kind'] != 'excluded':
                active_inventory.append(row)
            if row['source_kind'] == 'expense' and not row['deleted'] and row['status'] == 'COMPLETED' and str(row['result']).lower() in {'agree', 'approved', 'pass'}:
                counts['approved_expense'] += row['count']
        return {'inventory': active_inventory,
                'scope_counts': counts, 'health': self.health(), 'data_source': 'postgres'}
