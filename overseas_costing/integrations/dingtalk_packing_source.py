"""中文用途：经 WireGuard 只读钉钉装箱缓存，并通过专用账号提交受限刷新任务。"""

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
from typing import Any, Callable, Mapping

from overseas_costing.integrations.dingtalk_approval_source import (
    ApprovalSourceConfig,
    ArchiveIntegrityError,
    postgres_connection_kwargs,
)


ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
REQUEST_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PackingSnapshotNotReady(RuntimeError):
    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        super().__init__(message or status)


def _validate_id(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not ID_PATTERN.fullmatch(normalized):
        raise ValueError(_("{0} 不合法。").format(label))
    return normalized


def _validate_request_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not REQUEST_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(_("刷新请求 ID 必须是 64 位小写 SHA-256。"))
    return normalized


def build_refresh_request_key(
    request_kind: str,
    workbook_id: str,
    sheet_id: str | None,
    nonce: str,
) -> str:
    """生成不泄露用户输入且可重试幂等的固定长度请求键。"""

    payload = json.dumps(
        {
            "kind": str(request_kind),
            "workbook_id": _validate_id(workbook_id, "工作簿 ID"),
            "sheet_id": _validate_id(sheet_id, "Sheet ID") if sheet_id else None,
            "nonce": str(nonce or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PackingSheetCatalog:
    """只查询 costing_read 的四个公开视图。"""

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
                application_name="overseas_costing_packing_reader",
                read_only=True,
            )
        )

    def _read(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def list_workbooks(self) -> list[dict[str, Any]]:
        return self._read(
            """
            SELECT corp_id, workbook_id, year, label, updated_at
              FROM costing_read.packing_workbooks_v1
             ORDER BY year DESC, label, workbook_id
            """
        )

    def list_catalog_snapshot(self) -> list[dict[str, Any]]:
        """一次取得工作簿、Sheet 和最新快照 manifest，供本地增量缓存使用。"""

        return self._read(
            """
            WITH latest AS (
                SELECT DISTINCT ON (corp_id, workbook_id, sheet_id) *
                  FROM costing_read.packing_sheet_snapshots_v1
                 WHERE is_latest = TRUE
                 ORDER BY corp_id, workbook_id, sheet_id, created_at DESC, id DESC
            )
            SELECT w.corp_id, w.workbook_id, w.year,
                   w.label AS workbook_label, w.updated_at AS workbook_updated_at,
                   s.sheet_id, s.sheet_name, s.visibility,
                   s.source_updated_at, s.indexed_at,
                   p.id AS snapshot_id, p.status AS snapshot_status,
                   p.created_at AS snapshot_created_at, p.capture_finished_at,
                   p.content_sha256, p.bucket, p.object_key, p.actual_size,
                   p.error_code, p.error_message
              FROM costing_read.packing_workbooks_v1 w
              LEFT JOIN costing_read.packing_sheet_index_v1 s
                ON s.corp_id = w.corp_id AND s.workbook_id = w.workbook_id
              LEFT JOIN latest p
                ON p.corp_id = s.corp_id
               AND p.workbook_id = s.workbook_id
               AND p.sheet_id = s.sheet_id
             ORDER BY w.year DESC, w.label, w.workbook_id,
                      s.source_updated_at DESC NULLS LAST, s.sheet_name, s.sheet_id
            """
        )

    def list_sheets(self, workbook_id: str, *, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
        workbook_id = _validate_id(workbook_id, "工作簿 ID")
        search = str(search or "").strip()[:100]
        limit = max(1, min(int(limit), 500))
        return self._read(
            """
            SELECT corp_id, workbook_id, year, workbook_label, sheet_id,
                   sheet_name, visibility, source_updated_at, indexed_at
              FROM costing_read.packing_sheet_index_v1
             WHERE workbook_id = %s
               AND (%s = '' OR sheet_name ILIKE '%%' || %s || '%%')
             ORDER BY source_updated_at DESC NULLS LAST, sheet_name, sheet_id
             LIMIT %s
            """,
            (workbook_id, search, search, limit),
        )

    def get_latest_snapshot(self, workbook_id: str, sheet_id: str) -> dict[str, Any] | None:
        rows = self._read(
            """
            SELECT *
              FROM costing_read.packing_sheet_snapshots_v1
             WHERE workbook_id = %s AND sheet_id = %s AND is_latest = TRUE
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (_validate_id(workbook_id, "工作簿 ID"), _validate_id(sheet_id, "Sheet ID")),
        )
        return rows[0] if rows else None

    def list_latest_snapshots(self, workbook_id: str) -> list[dict[str, Any]]:
        """一次取得工作簿内所有已缓存 Sheet 的最新只读清单。"""

        return self._read(
            """
            SELECT *
              FROM costing_read.packing_sheet_snapshots_v1
             WHERE workbook_id = %s AND is_latest = TRUE
             ORDER BY created_at DESC, id DESC
            """,
            (_validate_id(workbook_id, "工作簿 ID"),),
        )

    def get_refresh_status(self, request_key: str) -> dict[str, Any] | None:
        rows = self._read(
            """
            SELECT *
              FROM costing_read.packing_refresh_status_v1
             WHERE request_key = %s
             LIMIT 1
            """,
            (_validate_request_key(request_key),),
        )
        return rows[0] if rows else None


class PackingRefreshSubmitter:
    """仅暴露两个 SECURITY DEFINER 函数，不提供通用 SQL 执行入口。"""

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
                application_name="overseas_costing_packing_submitter",
                read_only=False,
            )
        )

    def _call(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
        if not row:
            raise RuntimeError("刷新任务未返回请求 ID。")
        if isinstance(row, Mapping):
            value = next(iter(row.values()))
        else:
            value = row[0]
        return int(value)

    def request_workbook_index_refresh(
        self, workbook_id: str, request_key: str, requested_by: str
    ) -> int:
        return self._call(
            "SELECT costing_read.request_packing_workbook_index_refresh(%s, %s, %s) AS request_id",
            (
                _validate_id(workbook_id, "工作簿 ID"),
                _validate_request_key(request_key),
                str(requested_by or "")[:140],
            ),
        )

    def request_sheet_refresh(
        self,
        workbook_id: str,
        sheet_id: str,
        request_key: str,
        requested_by: str,
    ) -> int:
        return self._call(
            "SELECT costing_read.request_packing_sheet_refresh(%s, %s, %s, %s) AS request_id",
            (
                _validate_id(workbook_id, "工作簿 ID"),
                _validate_id(sheet_id, "Sheet ID"),
                _validate_request_key(request_key),
                str(requested_by or "")[:140],
            ),
        )


class PackingSnapshotArchive:
    def __init__(self, *, bucket: str, client: Any) -> None:
        self.bucket = str(bucket or "").strip()
        self.client = client

    def download(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        status = str(manifest.get("status") or "pending")
        if status != "ready":
            raise PackingSnapshotNotReady(
                status,
                str(manifest.get("error_message") or manifest.get("error_code") or status),
            )
        manifest_bucket = str(manifest.get("bucket") or self.bucket)
        if manifest_bucket != self.bucket:
            raise ArchiveIntegrityError(_("装箱快照 Bucket 与受控配置不一致。"))
        object_key = str(manifest.get("object_key") or "").strip()
        if not object_key:
            raise ArchiveIntegrityError(_("装箱快照缺少 object_key。"))

        response = self.client.get_object(self.bucket, object_key)
        try:
            content = response.read()
        finally:
            response.close()
            response.release_conn()
        expected_size = manifest.get("actual_size")
        if expected_size is not None and len(content) != int(expected_size):
            raise ArchiveIntegrityError(_("装箱快照大小校验失败。"))
        actual_hash = hashlib.sha256(content).hexdigest()
        expected_hash = str(manifest.get("content_sha256") or "").strip().lower()
        if expected_hash and actual_hash != expected_hash:
            raise ArchiveIntegrityError(_("装箱快照 SHA-256 校验失败。"))
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArchiveIntegrityError(_("装箱快照不是有效的 UTF-8 JSON。")) from error
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise ValueError(_("仅支持 schema version 1 的钉钉装箱快照。"))
        return payload


@dataclass(frozen=True)
class PackingRuntimeClients:
    catalog: PackingSheetCatalog
    submitter: PackingRefreshSubmitter
    archive: PackingSnapshotArchive


def get_packing_runtime_clients() -> PackingRuntimeClients:
    """从服务器环境/site_config 组装客户端；任何密钥都不会进入 API 返回值。"""

    from minio import Minio
    from overseas_costing.scripts.import_oa_logistics import (
        _runtime_config_bool,
        _runtime_config_int,
        _runtime_config_value,
    )

    common = {
        "host": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_HOST", "overseas_costing_oa_db_host", default="10.203.0.1"
        ),
        "port": _runtime_config_int(
            "OVERSEAS_COSTING_OA_DB_PORT", "overseas_costing_oa_db_port", default=5432
        ),
        "database": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_NAME", "overseas_costing_oa_db_name", default="dingtalk_oa"
        ),
        "sslmode": _runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_SSLMODE", "overseas_costing_oa_db_sslmode"
        )
        or None,
    }
    reader_config = ApprovalSourceConfig(
        **common,
        user=_runtime_config_value("OVERSEAS_COSTING_OA_DB_USER", "overseas_costing_oa_db_user"),
        password=_runtime_config_value(
            "OVERSEAS_COSTING_OA_DB_PASSWORD", "overseas_costing_oa_db_password"
        ),
    )
    submitter_config = ApprovalSourceConfig(
        **common,
        user=_runtime_config_value(
            "OVERSEAS_COSTING_PACKING_REFRESH_DB_USER",
            "overseas_costing_packing_refresh_db_user",
            default="costing_job_submitter",
        ),
        password=_runtime_config_value(
            "OVERSEAS_COSTING_PACKING_REFRESH_DB_PASSWORD",
            "overseas_costing_packing_refresh_db_password",
        ),
    )
    endpoint = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_ENDPOINT",
        "overseas_costing_oa_minio_endpoint",
        default="10.203.0.1:9000",
    )
    access_key = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_ACCESS_KEY", "overseas_costing_oa_minio_access_key"
    )
    secret_key = _runtime_config_value(
        "OVERSEAS_COSTING_OA_MINIO_SECRET_KEY", "overseas_costing_oa_minio_secret_key"
    )
    bucket = _runtime_config_value(
        "OVERSEAS_COSTING_PACKING_SNAPSHOT_BUCKET",
        "overseas_costing_packing_snapshot_bucket",
        default="dingtalk-packing-snapshots",
    )
    required = (
        reader_config.user,
        reader_config.password,
        submitter_config.user,
        submitter_config.password,
        access_key,
        secret_key,
        bucket,
    )
    if not all(required):
        raise ValueError(_("缺少钉钉装箱表缓存的 PostgreSQL 或 MinIO 服务器配置。"))
    minio = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=_runtime_config_bool(
            "OVERSEAS_COSTING_OA_MINIO_SECURE",
            "overseas_costing_oa_minio_secure",
            default=False,
        ),
    )
    return PackingRuntimeClients(
        catalog=PackingSheetCatalog(reader_config),
        submitter=PackingRefreshSubmitter(submitter_config),
        archive=PackingSnapshotArchive(bucket=bucket, client=minio),
    )
