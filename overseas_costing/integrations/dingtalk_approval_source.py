"""钉钉审批数据库与附件归档适配器。

这个模块故意不依赖 Frappe，便于同步脚本、后台 worker 和测试共用。
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=10000"
REPAIR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")
REPAIR_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArchiveIntegrityError(RuntimeError):
    pass


class ArchiveNotReady(RuntimeError):
    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        super().__init__(message or status)


@dataclass(frozen=True)
class ApprovalSourceConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout: int = 5
    sslmode: str | None = None


def postgres_connection_kwargs(
    config: ApprovalSourceConfig,
    *,
    application_name: str,
    read_only: bool,
) -> dict[str, Any]:
    """集中构造 PostgreSQL 连接参数，避免刷新账号误继承只读或查询账号失去只读。"""

    options = "-c statement_timeout=10000"
    if read_only:
        options = READ_ONLY_OPTIONS
    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "dbname": config.database,
        "user": config.user,
        "password": config.password,
        "connect_timeout": config.connect_timeout,
        "application_name": application_name,
        "options": options,
    }
    if config.sslmode:
        kwargs["sslmode"] = config.sslmode
    return kwargs


def _parse_boundary(value: str | date | datetime, *, end: bool) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
        if end:
            parsed += timedelta(days=1)
    else:
        text = str(value).strip()
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = datetime.combine(parsed_date, time.min)
            if end:
                parsed += timedelta(days=1)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc)


def parse_shanghai_window(
    start: str | date | datetime,
    end: str | date | datetime,
) -> tuple[datetime, datetime]:
    """返回上海时区下的左闭右开 UTC 查询窗口。

    日期形式的 ``end`` 表示包含该日，因此查询上界是次日 00:00。
    带时间的值则按其精确时刻作为边界。
    """

    start_utc = _parse_boundary(start, end=False)
    end_utc = _parse_boundary(end, end=True)
    if end_utc <= start_utc:
        raise ValueError(_("结束时间必须晚于开始时间"))
    return start_utc, end_utc


class PostgresApprovalSource:
    def __init__(
        self,
        config: ApprovalSourceConfig,
        *,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.connect = connect or self._default_connect

    @staticmethod
    def _default_connect(**kwargs):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(row_factory=dict_row, **kwargs)

    def _connection(self):
        return self.connect(
            **postgres_connection_kwargs(
                self.config,
                application_name="overseas_costing",
                read_only=True,
            )
        )

    @staticmethod
    def _raw_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        raw_payload = row.get("raw_payload") or {}
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        if not isinstance(raw_payload, dict):
            raw_payload = dict(raw_payload)
        payload = dict(raw_payload)
        field_map = {
            "corp_id": "corpId",
            "process_instance_id": "processInstanceId",
            "business_id": "businessId",
            "process_code": "processCode",
            "status": "status",
            "result": "result",
            "title": "title",
            "originator_user_id": "originatorUserId",
            "originator_user_name": "originatorUserName",
            "originator_dept_id": "originatorDeptId",
            "originator_dept_name": "originatorDeptName",
            "create_time": "createTime",
            "finish_time": "finishTime",
            "form_component_values": "formComponentValues",
        }
        for database_name, api_name in field_map.items():
            value = row.get(database_name)
            if value is not None and api_name not in payload:
                payload[api_name] = value
        return payload

    @classmethod
    def _actor_pairs(
        cls,
        approval_rows: Iterable[Mapping[str, Any]],
        attachment_rows: Iterable[Mapping[str, Any]],
    ) -> list[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for row in approval_rows:
            corp_id = str(row.get("corp_id") or "").strip()
            if not corp_id:
                continue
            payload = cls._raw_payload(row)
            originator_id = str(
                payload.get("originatorUserId") or payload.get("originator_user_id") or ""
            ).strip()
            if originator_id:
                pairs.add((corp_id, originator_id))
            operations = (
                payload.get("operationRecords")
                or payload.get("operation_records")
                or payload.get("comments")
                or []
            )
            if isinstance(operations, str):
                try:
                    operations = json.loads(operations)
                except (TypeError, ValueError):
                    operations = []
            if not isinstance(operations, list):
                operations = []
            for operation in operations:
                if not isinstance(operation, Mapping):
                    continue
                user_id = str(
                    operation.get("userId")
                    or operation.get("user_id")
                    or operation.get("operatorUserId")
                    or ""
                ).strip()
                if user_id:
                    pairs.add((corp_id, user_id))

        for row in attachment_rows:
            corp_id = str(row.get("corp_id") or "").strip()
            user_id = str(row.get("comment_user_id") or "").strip()
            if corp_id and user_id:
                pairs.add((corp_id, user_id))
        return sorted(pairs)

    def list_instances(
        self,
        *,
        process_code: str,
        start: str | date | datetime,
        end: str | date | datetime,
        limit: int = 1000,
    ) -> dict[str, Any]:
        start_utc, end_utc = parse_shanghai_window(start, end)
        safe_limit = max(1, min(int(limit), 10000))
        sql = """
            SELECT *
              FROM costing_read.approval_instances_v1
             WHERE process_code = %s
               AND create_time >= %s
               AND create_time < %s
             ORDER BY create_time ASC, process_instance_id ASC
             LIMIT %s
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (process_code, start_utc, end_utc, safe_limit))
                rows = cursor.fetchall()

        items = [self._raw_payload(row) for row in rows]
        updated_values = [row.get("updated_at") for row in rows if row.get("updated_at")]
        source_updated_at = max(updated_values) if updated_values else None
        source_lag_seconds = None
        if isinstance(source_updated_at, datetime):
            updated = source_updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            source_lag_seconds = max(0, int((datetime.now(timezone.utc) - updated).total_seconds()))
        return {
            "items": items,
            "source_updated_at": source_updated_at,
            "source_lag_seconds": source_lag_seconds,
            "data_source": "postgres",
            "fallback_used": False,
        }

    def get_instances(self, instance_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(value).strip() for value in instance_ids if str(value).strip()))
        if not unique_ids:
            return {}
        sql = """
            SELECT *
              FROM costing_read.approval_instances_v1
             WHERE process_instance_id = ANY(%s)
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (unique_ids,))
                rows = cursor.fetchall()

        by_id = {
            str(row["process_instance_id"]): self._raw_payload(row)
            for row in rows
            if row.get("process_instance_id")
        }
        return {instance_id: by_id[instance_id] for instance_id in unique_ids if instance_id in by_id}

    def get_reference_coverage(
        self,
        instance_ids: Iterable[str],
        business_ids: Iterable[str],
    ) -> dict[str, Any]:
        """一次批量按实例 ID/审批编号核对来源，避免逐批查询。"""

        unique_instances = list(dict.fromkeys(
            str(value).strip() for value in instance_ids if str(value).strip()
        ))
        unique_business = list(dict.fromkeys(
            str(value).strip() for value in business_ids if str(value).strip()
        ))
        if not unique_instances and not unique_business:
            return {"rows": [], "by_instance": {}, "by_business": {}}
        sql = """
            SELECT corp_id, process_instance_id, business_id, process_code,
                   status, result, create_time, finish_time, updated_at
              FROM costing_read.approval_instances_v1
             WHERE process_instance_id = ANY(%s)
                OR business_id = ANY(%s)
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (unique_instances, unique_business))
                rows = [dict(row) for row in cursor.fetchall()]
        by_instance = {
            str(row.get("process_instance_id") or ""): row
            for row in rows if row.get("process_instance_id")
        }
        by_business: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            business_id = str(row.get("business_id") or "").strip()
            if business_id:
                by_business.setdefault(business_id, []).append(row)
        return {"rows": rows, "by_instance": by_instance, "by_business": by_business}

    def get_repair_statuses(self, instance_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(
            str(value).strip() for value in instance_ids if str(value).strip()
        ))
        if not unique_ids:
            return {}
        sql = """
            SELECT DISTINCT ON (process_instance_id) *
              FROM costing_read.approval_repair_status_v1
             WHERE process_instance_id = ANY(%s)
             ORDER BY process_instance_id, requested_at DESC, id DESC
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (unique_ids,))
                rows = cursor.fetchall()
        return {
            str(row.get("process_instance_id") or ""): dict(row)
            for row in rows if row.get("process_instance_id")
        }

    def get_process_context(self, process_code: str) -> dict[str, Any] | None:
        """从已有白名单审批中解析企业上下文，不向浏览器暴露。"""

        sql = """
            SELECT corp_id, process_code
              FROM costing_read.approval_instances_v1
             WHERE process_code = %s
             ORDER BY updated_at DESC
             LIMIT 1
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (str(process_code or "").strip(),))
                rows = cursor.fetchall()
        return dict(rows[0]) if rows else None

    def get_attachment_manifest(self, process_instance_id: str, file_id: str) -> dict[str, Any] | None:
        sql = """
            SELECT *
              FROM costing_read.attachment_archives_v1
             WHERE process_instance_id = %s
               AND file_id = %s
             LIMIT 1
        """
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (process_instance_id, file_id))
                rows = cursor.fetchall()
        return dict(rows[0]) if rows else None

    def get_instance_bundle(self, instance_ids: Iterable[str]) -> dict[str, Any]:
        """批量读取审批、附件清单和同步健康状态，共用一个只读连接。"""

        unique_ids = list(dict.fromkeys(str(value).strip() for value in instance_ids if str(value).strip()))
        if not unique_ids:
            return {"instances": {}, "attachments": [], "health": {}}
        approval_sql = """
            SELECT *
              FROM costing_read.approval_instances_v1
             WHERE process_instance_id = ANY(%s)
        """
        attachment_sql = """
            SELECT *
              FROM costing_read.attachment_archives_v1
             WHERE process_instance_id = ANY(%s)
             ORDER BY process_instance_id, attachment_origin, file_name, file_id
        """
        actor_sql = """
            WITH requested(corp_id, user_id) AS (
                SELECT * FROM unnest(%s::text[], %s::text[])
            )
            SELECT actor.*
              FROM costing_read.approval_actor_names_v1 actor
              JOIN requested USING (corp_id, user_id)
             ORDER BY actor.corp_id, actor.user_id
        """
        health_sql = "SELECT * FROM costing_read.sync_health_v1 LIMIT 1"
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(approval_sql, (unique_ids,))
                approval_rows = cursor.fetchall()
                cursor.execute(attachment_sql, (unique_ids,))
                attachment_rows = cursor.fetchall()
                actor_pairs = self._actor_pairs(approval_rows, attachment_rows)
                if actor_pairs:
                    corp_ids = [pair[0] for pair in actor_pairs]
                    user_ids = [pair[1] for pair in actor_pairs]
                    try:
                        cursor.execute(actor_sql, (corp_ids, user_ids))
                        actor_rows = cursor.fetchall()
                    except Exception:
                        # 人员姓名是可选增强字段；视图尚未部署时仍需返回审批和附件。
                        connection.rollback()
                        actor_rows = []
                else:
                    actor_rows = []
                cursor.execute(health_sql, ())
                health_rows = cursor.fetchall()

        by_id = {
            str(row["process_instance_id"]): self._raw_payload(row)
            for row in approval_rows
            if row.get("process_instance_id")
        }
        actors: dict[str, dict[str, dict[str, Any]]] = {}
        for row in actor_rows:
            corp_id = str(row.get("corp_id") or "")
            user_id = str(row.get("user_id") or "")
            if corp_id and user_id:
                actors.setdefault(corp_id, {})[user_id] = dict(row)
        return {
            "instances": {
                instance_id: by_id[instance_id]
                for instance_id in unique_ids
                if instance_id in by_id
            },
            "attachments": [dict(row) for row in attachment_rows],
            "actors": actors,
            "health": dict(health_rows[0]) if health_rows else {},
            "data_source": "postgres",
            "fallback_used": False,
        }


def _repair_identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not REPAIR_ID_PATTERN.fullmatch(normalized):
        raise ValueError(_("{0}不合法。").format(label))
    return normalized


class ApprovalRepairSubmitter:
    """只允许调用审批修复 SECURITY DEFINER 函数。"""

    def __init__(
        self,
        config: ApprovalSourceConfig,
        *,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.connect = connect or self._default_connect

    @staticmethod
    def _default_connect(**kwargs):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(row_factory=dict_row, **kwargs)

    def _connection(self):
        return self.connect(**postgres_connection_kwargs(
            self.config,
            application_name="overseas_costing_approval_repair_submitter",
            read_only=False,
        ))

    def request_repair(
        self,
        *,
        corp_id: str,
        process_instance_id: str,
        expected_business_id: str,
        expected_process_code: str,
        expected_purpose: str,
        request_key: str,
        requested_by: str,
        trigger_source: str,
    ) -> int:
        purpose = str(expected_purpose or "").strip()
        if purpose not in {"international_logistics", "purchase_expense"}:
            raise ValueError(_("审批修复用途不合法。"))
        trigger = str(trigger_source or "").strip()
        if trigger not in {"costing_audit", "manual", "linked_purchase"}:
            raise ValueError(_("审批修复触发来源不合法。"))
        key = str(request_key or "").strip().lower()
        if not REPAIR_HASH_PATTERN.fullmatch(key):
            raise ValueError(_("审批修复请求 ID 必须是 64 位小写 SHA-256。"))
        params = (
            _repair_identifier(corp_id, "企业 ID"),
            _repair_identifier(process_instance_id, "流程实例 ID"),
            _repair_identifier(expected_business_id, "审批编号"),
            _repair_identifier(expected_process_code, "流程模板"),
            purpose,
            key,
            str(requested_by or "")[:200],
            trigger,
        )
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT costing_read.request_approval_repair(%s,%s,%s,%s,%s,%s,%s,%s) AS request_id",
                    params,
                )
                row = cursor.fetchone()
        if not row:
            raise RuntimeError("审批修复任务未返回请求 ID。")
        value = next(iter(row.values())) if isinstance(row, Mapping) else row[0]
        return int(value)


class MinioArchiveClient:
    def __init__(self, *, bucket: str, client) -> None:
        self.bucket = bucket
        self.client = client

    def download(self, manifest: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
        status = str(manifest.get("archive_status") or "pending")
        if status != "archived":
            raise ArchiveNotReady(status, str(manifest.get("last_error") or status))

        object_key = str(manifest.get("object_key") or "").strip()
        if not object_key:
            raise ArchiveIntegrityError(_("已归档附件缺少 object_key"))

        response = self.client.get_object(self.bucket, object_key)
        try:
            content = response.read()
        finally:
            response.close()
            response.release_conn()

        expected_size = manifest.get("actual_size")
        if expected_size is not None and len(content) != int(expected_size):
            raise ArchiveIntegrityError(
                _("附件大小校验失败：期望 {0}，实际 {1}").format(expected_size, len(content))
            )

        actual_sha256 = hashlib.sha256(content).hexdigest()
        expected_sha256 = str(manifest.get("sha256") or "").strip().lower()
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise ArchiveIntegrityError(_("附件 SHA-256 校验失败"))

        return content, {
            "content_length": len(content),
            "sha256": actual_sha256,
            "content_type": manifest.get("content_type") or "application/octet-stream",
            "object_key": object_key,
            "archive_status": status,
        }
