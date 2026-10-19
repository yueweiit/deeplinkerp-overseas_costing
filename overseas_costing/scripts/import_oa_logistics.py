"""
中文用途：批量拉取钉钉国际物流审批单，并先筛出海运审批追溯数据。

当前脚本只做只读拉取和本地输出，不写入 ERP/Frappe：
1. 按流程模板 process_code 和时间范围拉审批实例 ID
2. 逐条读取审批实例详情
3. 从表单字段中筛选“物流方式=海运”的审批单
4. 输出 JSON / CSV，供人工核对后再决定是否导入系统

运行示例：
python -m overseas_costing.scripts.import_oa_logistics ^
  --process-code PROC-XXXX ^
  --start 2026-07-01 ^
  --end 2026-07-21 ^
  --output data/dingtalk_sea_approvals.json ^
  --csv data/dingtalk_sea_approvals.csv
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    import frappe
except Exception:  # pragma: no cover - 本地命令行无 Frappe 环境时保持可运行
    frappe = None

CURRENT_FILE = Path(__file__).resolve()
PACKAGE_ROOT = CURRENT_FILE.parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from overseas_costing.integrations.dingtalk_approval_source import ApprovalSourceConfig, PostgresApprovalSource
from overseas_costing.services.supplier_resolution_service import (
    load_active_suppliers,
    resolve_supplier_reference,
)
from overseas_costing.utils.dingtalk import build_dingtalk_order_payload, extract_dingtalk_instance_id
from overseas_costing.utils.field_mapper import (
    map_oa_row_to_item,
    map_purchase_expense_row_to_item,
    normalize_business_type,
    normalize_unit,
)

NEW_TOKEN_PATH = "/v1.0/oauth2/{corp_id}/token"
NEW_LIST_INSTANCE_IDS_PATH = "/v1.0/workflow/processes/instanceIds/query"
NEW_INSTANCE_DETAIL_PATH = "/v1.0/workflow/processInstances?processInstanceId={process_instance_id}"
NEW_APPROVAL_ATTACHMENT_DOWNLOAD_PATH = "/v1.0/workflow/processInstances/spaces/files/urls/download"
NEW_APPROVAL_ATTACHMENT_AUTH_DOWNLOAD_PATH = "/v1.0/workflow/processInstances/spaces/files/authDownload"

LEGACY_TOKEN_PATH = "/gettoken"
LEGACY_LIST_INSTANCE_IDS_PATH = "/topapi/processinstance/listids?access_token={access_token}"
LEGACY_INSTANCE_DETAIL_PATH = "/topapi/processinstance/get?access_token={access_token}"
LEGACY_APPROVAL_ATTACHMENT_DOWNLOAD_PATH = "/topapi/processinstance/file/url/get?access_token={access_token}"
LEGACY_APPROVAL_ATTACHMENT_SPACE_PATH = "/topapi/processinstance/cspace/info?access_token={access_token}"
LEGACY_APPROVAL_ATTACHMENT_DENTRY_AUTH_PATH = "/topapi/process/dentry/auth?access_token={access_token}"
LEGACY_USER_DETAIL_PATH = "/topapi/v2/user/get?access_token={access_token}"
NEW_STORAGE_DENTRY_DOWNLOAD_INFO_PATH = "/v1.0/storage/spaces/{space_id}/dentries/{dentry_id}/downloadInfos/query"
NEW_STORAGE_DENTRY_LIST_PATH = "/v1.0/storage/spaces/{space_id}/dentries"
NEW_DRIVE_FILE_DOWNLOAD_INFO_PATH = "/v1.0/drive/spaces/{space_id}/files/{file_id}/downloadInfos"
NEW_STORAGE_THUMBNAILS_QUERY_PATH = "/v1.0/storage/spaces/{space_id}/thumbnails/query"

DEFAULT_SEA_KEYWORDS = ("海运", "SEA", "OCEAN", "MARITIMO", "MARÍTIMO")
TRANSPORT_MODE_KEYWORDS = {
    "EXPRESS": ("快递", "EXPRESS", "COURIER", "CORREO EXPRESS", "CORREO", "DHL", "FEDEX", "UPS"),
    "AIR": ("空运", "AIR", "AIR FREIGHT", "CARGA AEREA", "CARGA AÉREA", "AEREA", "AÉREA"),
    "SEA": DEFAULT_SEA_KEYWORDS + ("OCEAN FREIGHT", "CONTENEDOR", "MARITIMO", "MARÍTIMO"),
}
TRANSPORT_MODE_LABELS = {
    "SEA": "海运",
    "AIR": "空运",
    "EXPRESS": "快递",
}
HIDDEN_APPROVAL_STATUSES = (
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "REVOKED",
    "REJECTED",
    "REFUSED",
    "DENIED",
    "ABORTED",
    "撤销",
    "已撤销",
    "拒绝",
    "已拒绝",
    "驳回",
    "已驳回",
    "不通过",
    "未通过",
    "终止",
    "已终止",
    "取消",
    "已取消",
    "作废",
    "已作废",
)
INVALID_APPROVAL_RESULTS = (
    "REFUSE",
    "REFUSED",
    "REJECT",
    "REJECTED",
    "DENY",
    "DENIED",
    "DISAGREE",
    "拒绝",
    "已拒绝",
    "驳回",
    "已驳回",
    "不通过",
    "未通过",
)
COMPLETED_APPROVAL_STATUSES = ("COMPLETED", "FINISHED", "AGREE", "APPROVED", "已完成", "审批通过", "同意")
TRANSPORT_FIELD_ALIASES = (
    "物流方式",
    "运输方式",
    "物流方式Camino Envío",
    "物流方式Camino Envio",
    "Camino Envío",
    "Camino Envio",
    "way",
    "transport",
)
BATCH_NO_FIELD_ALIASES = (
    "柜号/单号Número DE Logística",
    "柜号/单号Numero DE Logistica",
    "Número DE Logística",
    "Numero DE Logistica",
    "物流单号",
    "运单号",
    "柜号",
    "单号",
)
LOGISTICS_FEE_FIELD_ALIASES = (
    "物流费用",
    "运费金额",
    "国际运费",
    "海运费",
    "空运费",
    "logisticsfee",
    "freight",
)
LOGISTICS_QUOTE_FIELD_ALIASES = (
    "物流报价",
    "物流报价Cotización de logística",
    "物流报价Cotizacion de logistica",
    "Cotización de logística",
    "Cotizacion de logistica",
    "报价",
)
LOGISTICS_CURRENCY_FIELD_ALIASES = (
    "币种Moneda",
    "币种",
    "Moneda",
)
LOGISTICS_WEIGHT_FIELD_ALIASES = (
    "重量Peso（KG）",
    "重量Peso(KG)",
    "重量Peso",
    "重量",
    "Peso",
    "Peso KG",
)
LOGISTICS_PRE_DELIVERY_FIELD_ALIASES = (
    "预计发货日期Fecha de Pre-entrega",
    "预计发货日期",
    "Fecha de Pre-entrega",
    "Fecha de Pre entrega",
    "Pre-entrega",
)
LOGISTICS_DESTINATION_FIELD_ALIASES = (
    "目标地区Países destinatarios",
    "目标地区Paises destinatarios",
    "目标地区",
    "Países destinatarios",
    "Paises destinatarios",
    "目的地",
    "目的国",
)
GOODS_TABLE_FIELD_ALIASES = ("货物信息", "Bienes")
BUSINESS_ENTITY_FIELD_ALIASES = (
    "业务主体",
    "业务主体Empresas",
    "Entidad comercial",
    "Empresas",
    "Empresa",
    "Business Entity",
    "Company",
    "公司主体",
    "归属公司",
    "子公司",
)
PROJECT_OWNERSHIP_FIELD_ALIASES = (
    "项目归属Pertenencia del proyecto",
    "项目归属",
    "Pertenencia del proyecto",
    "项目proyecto",
)
OPTIONAL_LOGISTICS_FEE_FIELDS = (
    ("customs_clearance_fee", "清关费", "goods_value", ("清关费", "Gastos de despacho de aduana", "Customs clearance fee")),
    ("import_tax", "进口税费", "goods_value", ("进口税费", "Impuestos de importación", "Import tax")),
    ("express_surcharge", "快递附加费", "chargeable_weight", ("快递附加费", "Cargo adicional exprés", "Express surcharge")),
    ("destination_delivery", "当地配送费", "gross_weight", ("当地配送费", "Entrega local", "Local delivery fee")),
)
PURCHASE_DETAIL_TABLE_FIELD_ALIASES = (
    "需求明细",
    "Desglose de los gastos",
)
PURCHASE_CURRENCY_FIELD_ALIASES = (
    "币种Moneda",
    "币种",
    "Moneda",
)
PURCHASE_RELATE_FIELD_ALIASES = (
    "关联审批单",
    "Asociar órdenes de compra",
    "Asociar ordenes de compra",
    "órdenes de compra",
    "ordenes de compra",
)
PURCHASE_APPROVAL_KEYWORDS = (
    "采购支出",
    "采购",
    "gastosdecompra",
    "gastoscompra",
    "ordendecompra",
    "ordenesdecompra",
)
ATTACHMENT_FIELD_ALIASES = (
    "附件",
    "Adjunto",
    "物品清单",
    "运费报价",
    "Packing",
    "Packing List",
    "Factura",
    "Invoice",
)
ATTACHMENT_FILE_ID_KEYS = ("fileId", "file_id", "fileID", "mediaId", "media_id", "downloadId", "id")
ATTACHMENT_FILE_NAME_KEYS = ("fileName", "file_name", "filename", "name", "title")
ATTACHMENT_FILE_URL_KEYS = ("fileUrl", "file_url", "downloadUrl", "download_url", "url", "previewUrl", "preview_url")
ATTACHMENT_SPACE_ID_KEYS = ("spaceId", "space_id", "spaceID")
DEFAULT_LOGISTICS_PROCESS_CODE = "PROC-RIYJTXWV-CN52YRK70C5499JG0TJ03-3GSSHZQJ-5"
DEFAULT_FX_RMB_TO_MXN = 2.6
DEFAULT_FX_USD_TO_RMB = round(1 / 0.1393, 6)
MAX_AUDIT_TEXT_LENGTH = 20000
MAX_AI_LOGISTICS_TEXT_LENGTH = 6000
DINGTALK_ENV_FILE_CANDIDATES = (
    "/mnt/e/Yuewei开发/预算管理系统/dingtalk-expense-sync-main/.env",
    "/mnt/e/Yuewei开发/预算管理系统/dingtalk-budget-main/server/.env",
    "/mnt/e/Yuewei开发/dingtalk-expense-sync-main/.env",
    "/mnt/e/Yuewei开发/dingtalk-budget-main/server/.env",
    "E:/Yuewei开发/预算管理系统/dingtalk-expense-sync-main/.env",
    "E:/Yuewei开发/预算管理系统/dingtalk-budget-main/server/.env",
    "E:/Yuewei开发/dingtalk-expense-sync-main/.env",
    "E:/Yuewei开发/dingtalk-budget-main/server/.env",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _dingtalk_id_value(value: Any) -> str | int:
    text = _clean(value)
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return text
    return text


def load_env_file(env_file: str | None, *, override: bool = False) -> str:
    """加载 .env 文件，但不打印任何敏感值。"""

    path_text = _clean(env_file)
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if not path.exists():
        raise FileNotFoundError(_("未找到 .env 文件：{0}").format(path))

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value
        if key == "DINGTALK_APPKEY" and (override or "DINGTALK_APP_KEY" not in os.environ):
            os.environ["DINGTALK_APP_KEY"] = value
        if key == "DINGTALK_APPSECRET" and (override or "DINGTALK_APP_SECRET" not in os.environ):
            os.environ["DINGTALK_APP_SECRET"] = value
    return str(path)


def resolve_dingtalk_env_file(env_file: str | None = None) -> str:
    """定位钉钉配置文件，优先使用显式路径，再找预算系统里的 .env。"""

    explicit = _clean(env_file) or _clean(os.environ.get("DINGTALK_ENV_FILE"))
    if explicit:
        return explicit
    for candidate in DINGTALK_ENV_FILE_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    return ""


def _runtime_config_value(*keys: str, default: str = "") -> str:
    """读取运行时配置：环境变量优先，其次 Frappe site_config。"""

    for key in keys:
        value = os.environ.get(key)
        if _has_value(value):
            return _clean(value)

    conf = getattr(frappe, "conf", None) if frappe is not None else None
    if conf:
        for key in keys:
            candidates = (key, key.lower())
            for candidate in candidates:
                value = conf.get(candidate) if hasattr(conf, "get") else None
                if _has_value(value):
                    return _clean(value)
    return default


def _runtime_config_int(*keys: str, default: int = 0) -> int:
    value = _runtime_config_value(*keys)
    if not _has_value(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _runtime_config_bool(*keys: str, default: bool = False) -> bool:
    value = _runtime_config_value(*keys)
    if not _has_value(value):
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "开启", "启用")


def resolve_approval_source_mode() -> str:
    """返回 OA 读取源。默认保持 API，生产通过 site_config 显式切换。"""

    mode = _runtime_config_value(
        "OVERSEAS_COSTING_OA_SOURCE",
        "overseas_costing_oa_source",
        default="api",
    ).lower()
    if mode not in ("api", "postgres"):
        raise ValueError(_("不支持的 OA 数据源：{0}").format(mode))
    return mode


def _assert_emergency_api_enabled() -> None:
    """Block explicitly disabled API mode while preserving legacy unset installs."""

    configured = _runtime_config_value(
        "OVERSEAS_COSTING_OA_EMERGENCY_API_ENABLED",
        "overseas_costing_oa_emergency_api_enabled",
    )
    if configured and not _runtime_config_bool(
        "OVERSEAS_COSTING_OA_EMERGENCY_API_ENABLED",
        "overseas_costing_oa_emergency_api_enabled",
    ):
        raise PermissionError(_("钉钉 API 应急开关未开启，禁止消耗接口额度。"))


def _get_postgres_approval_source() -> PostgresApprovalSource:
    host = _runtime_config_value(
        "OVERSEAS_COSTING_OA_DB_HOST",
        "overseas_costing_oa_db_host",
        default="10.203.0.1",
    )
    database = _runtime_config_value(
        "OVERSEAS_COSTING_OA_DB_NAME",
        "overseas_costing_oa_db_name",
        default="dingtalk_oa",
    )
    user = _runtime_config_value("OVERSEAS_COSTING_OA_DB_USER", "overseas_costing_oa_db_user")
    password = _runtime_config_value("OVERSEAS_COSTING_OA_DB_PASSWORD", "overseas_costing_oa_db_password")
    if not host or not database or not user or not password:
        raise ValueError(_("缺少 PostgreSQL OA 数据源配置（host/name/user/password）。"))
    return PostgresApprovalSource(
        ApprovalSourceConfig(
            host=host,
            port=_runtime_config_int(
                "OVERSEAS_COSTING_OA_DB_PORT",
                "overseas_costing_oa_db_port",
                default=5432,
            ),
            database=database,
            user=user,
            password=password,
            sslmode=_runtime_config_value(
                "OVERSEAS_COSTING_OA_DB_SSLMODE",
                "overseas_costing_oa_db_sslmode",
            )
            or None,
        )
    )


def _serialize_source_updated_at(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    text = _clean(value)
    return text or None


def _has_dingtalk_pull_credentials() -> bool:
    if resolve_approval_source_mode() == "postgres":
        return bool(
            _runtime_config_value("OVERSEAS_COSTING_OA_DB_USER", "overseas_costing_oa_db_user")
            and _runtime_config_value("OVERSEAS_COSTING_OA_DB_PASSWORD", "overseas_costing_oa_db_password")
        )
    if _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"):
        return True
    if _runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key") and _runtime_config_value(
        "DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"
    ):
        return True
    return bool(
        _runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id")
        and _runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id")
        and _runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret")
    )


def _preload_env_file_from_argv(argv: list[str]) -> str:
    env_file = _clean(os.environ.get("DINGTALK_ENV_FILE"))
    for index, arg in enumerate(argv):
        if arg == "--env-file" and index + 1 < len(argv):
            env_file = argv[index + 1]
            break
        if arg.startswith("--env-file="):
            env_file = arg.split("=", 1)[1]
            break
    return load_env_file(env_file) if env_file else ""


def resolve_logistics_process_code(process_code: str | None = "") -> str:
    """解析国际物流流程号。

    预算系统 .env 里的 DINGTALK_PROCESS_CODE 可能是预算审批流，不能当成本模块的默认值。
    """

    return _clean(process_code) or _runtime_config_value(
        "DINGTALK_LOGISTICS_PROCESS_CODE",
        "overseas_costing_dingtalk_logistics_process_code",
        default=DEFAULT_LOGISTICS_PROCESS_CODE,
    )


def resolve_purchase_process_code(process_code: str | None = "") -> str:
    """解析采购支出流程号。

    采购支出流程没有安全默认值，必须显式传入或配置环境变量，避免误把预算/其它审批流当采购来源。
    """

    process_codes = _parse_json_text(
        _runtime_config_value("DINGTALK_PROCESS_CODES", "overseas_costing_dingtalk_process_codes")
    )
    purchase_code_from_list = ""
    if isinstance(process_codes, list) and len(process_codes) > 1:
        purchase_code_from_list = _clean(process_codes[1])

    return (
        _clean(process_code)
        or _runtime_config_value("DINGTALK_PURCHASE_PROCESS_CODE", "overseas_costing_dingtalk_purchase_process_code")
        or _runtime_config_value(
            "DINGTALK_PURCHASE_EXPENSE_PROCESS_CODE",
            "overseas_costing_dingtalk_purchase_expense_process_code",
        )
        or purchase_code_from_list
    )


def _api_url() -> str:
    return (_clean(os.environ.get("DINGTALK_API_URL")) or "https://api.dingtalk.com").rstrip("/")


def _oapi_url() -> str:
    return (_clean(os.environ.get("DINGTALK_OAPI_URL")) or "https://oapi.dingtalk.com").rstrip("/")


def _resolve_api_style(api_style: str = "auto") -> str:
    requested = (
        _clean(api_style)
        or _runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default="auto")
        or "auto"
    ).lower()
    if requested in ("legacy", "old"):
        return "legacy"
    if requested == "new":
        return "new"
    if requested != "auto":
        raise ValueError(_("不支持的钉钉接口风格：{0}").format(api_style))

    has_new = bool(
        _runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id")
        and _runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id")
    )
    has_legacy = bool(
        _runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key")
        and _runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret")
    )
    if has_legacy:
        return "legacy"
    if has_new:
        return "new"
    return "legacy"


def _normalize_key(value: Any) -> str:
    text = _clean(value).lower()
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _parse_json_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _parse_datetime_ms(value: str, *, end_of_day: bool = False) -> int:
    text = _clean(value)
    if not text:
        raise ValueError(_("缺少时间参数。"))
    if text.isdigit():
        number = int(text)
        return number if number > 10_000_000_000 else number * 1000

    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d")
    parsed: datetime | None = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        parsed = datetime.fromisoformat(text)
    if end_of_day and parsed.time() == dt_time.min:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(parsed.timestamp() * 1000)


def _normalize_transport_mode(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    upper = text.upper()
    if upper in TRANSPORT_MODE_LABELS:
        return upper
    normalized = _normalize_key(text).upper()
    for mode, keywords in TRANSPORT_MODE_KEYWORDS.items():
        if any(_normalize_key(keyword).upper() in normalized for keyword in keywords):
            return mode
    return upper if upper in TRANSPORT_MODE_LABELS else ""


def _parse_transport_modes(value: Any, *, default: tuple[str, ...] = ("SEA",)) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        raw_values = re.split(r"[,，/、\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]
    modes: list[str] = []
    for raw_value in raw_values:
        text = _clean(raw_value)
        if not text:
            continue
        if text.upper() in ("ALL", "*", "全部"):
            return tuple(TRANSPORT_MODE_LABELS)
        mode = _normalize_transport_mode(text)
        if mode and mode not in modes:
            modes.append(mode)
    return tuple(modes) if modes else default


def _iter_time_chunks(start_time_ms: int, end_time_ms: int, chunk_days: int = 30) -> list[tuple[int, int]]:
    if end_time_ms < start_time_ms:
        raise ValueError(_("结束时间不能早于开始时间。"))
    if chunk_days <= 0:
        return [(start_time_ms, end_time_ms)]

    chunks: list[tuple[int, int]] = []
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    cursor = start_time_ms
    while cursor <= end_time_ms:
        chunk_end = min(cursor + chunk_ms - 1, end_time_ms)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + 1
    return chunks


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    api_style: str = "new",
    payload: dict | None = None,
    timeout: int = 30,
    retry: int = 2,
) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if token and api_style == "new":
        headers["x-acs-dingtalk-access-token"] = token
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(retry + 1):
        try:
            request = Request(url, data=data, headers=headers, method=method)
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"钉钉接口 HTTP {error.code}：{body}")
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
        if attempt < retry:
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"钉钉接口请求失败：{last_error}") from last_error


def get_access_token(
    *,
    api_style: str = "auto",
    access_token: str = "",
    corp_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    app_key: str = "",
    app_secret: str = "",
) -> str:
    """读取或换取钉钉 access_token。

    优先级：
    1. 显式传入或环境变量中的 DINGTALK_ACCESS_TOKEN
    2. 新版 OpenAPI：corp_id + client_id + client_secret
    3. 旧版 OpenAPI：app_key + app_secret
    """

    if resolve_approval_source_mode() == "postgres":
        return ""
    _assert_emergency_api_enabled()

    token = _clean(access_token) or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token")
    if token:
        return token

    resolved_api_style = _resolve_api_style(api_style)

    if resolved_api_style == "legacy":
        resolved_app_key = _clean(app_key) or _runtime_config_value(
            "DINGTALK_APP_KEY",
            "DINGTALK_APPKEY",
            "overseas_costing_dingtalk_app_key",
        )
        resolved_app_secret = _clean(app_secret) or _runtime_config_value(
            "DINGTALK_APP_SECRET",
            "DINGTALK_APPSECRET",
            "overseas_costing_dingtalk_app_secret",
        )
        if not resolved_app_key or not resolved_app_secret:
            raise ValueError(_("旧版钉钉接口需要 DINGTALK_APP_KEY 和 DINGTALK_APP_SECRET。"))
        query = urlencode({"appkey": resolved_app_key, "appsecret": resolved_app_secret})
        result = _request_json(f"{_oapi_url()}{LEGACY_TOKEN_PATH}?{query}", api_style="legacy")
        _ensure_dingtalk_success(result, api_style="legacy")
        return _clean(result.get("access_token"))

    resolved_corp_id = _clean(corp_id) or _runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id")
    resolved_client_id = _clean(client_id) or _runtime_config_value(
        "DINGTALK_CLIENT_ID",
        "overseas_costing_dingtalk_client_id",
    ) or _runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key")
    resolved_client_secret = _clean(client_secret) or _runtime_config_value(
        "DINGTALK_CLIENT_SECRET",
        "overseas_costing_dingtalk_client_secret",
    ) or _runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret")
    if not resolved_corp_id or not resolved_client_id or not resolved_client_secret:
        raise ValueError(_("新版钉钉接口需要 DINGTALK_CORP_ID、DINGTALK_CLIENT_ID、DINGTALK_CLIENT_SECRET。"))

    result = _request_json(
        f"{_api_url()}{NEW_TOKEN_PATH.format(corp_id=quote(resolved_corp_id, safe=''))}",
        method="POST",
        payload={
            "grant_type": "client_credentials",
            "client_id": resolved_client_id,
            "client_secret": resolved_client_secret,
        },
    )
    token = _clean(result.get("accessToken") or result.get("access_token"))
    if not token:
        raise RuntimeError(f"新版钉钉 token 响应中没有 accessToken：{result}")
    return token


def _ensure_dingtalk_success(result: dict, *, api_style: str) -> None:
    if api_style == "legacy":
        errcode = result.get("errcode", 0)
        if errcode not in (0, "0", None):
            raise RuntimeError(f"钉钉旧版接口返回失败：{result}")
        return
    code = result.get("code")
    if code not in (None, "", "0", 0):
        raise RuntimeError(f"钉钉新版接口返回失败：{result}")


def list_process_instance_ids(
    *,
    token: str,
    process_code: str,
    start_time_ms: int,
    end_time_ms: int,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
) -> list[str]:
    """分页拉取审批实例 ID。"""

    process_code = _clean(process_code)
    if not process_code:
        raise ValueError(_("缺少国际物流流程模板 process_code。"))
    ids: list[str] = []

    resolved_list_api = _resolve_list_api_mode(list_api, api_style)
    for chunk_start_ms, chunk_end_ms in _iter_time_chunks(start_time_ms, end_time_ms, chunk_days=chunk_days):
        if resolved_list_api in ("old", "both"):
            ids.extend(
                _list_process_instance_ids_by_legacy_api(
                    token=token,
                    process_code=process_code,
                    start_time_ms=chunk_start_ms,
                    end_time_ms=chunk_end_ms,
                    page_size=page_size,
                    max_pages=max_pages,
                )
            )

        if resolved_list_api in ("new", "both"):
            ids.extend(
                _list_process_instance_ids_by_new_api(
                    token=token,
                    process_code=process_code,
                    start_time_ms=chunk_start_ms,
                    end_time_ms=chunk_end_ms,
                    page_size=page_size,
                    max_pages=max_pages,
                )
            )

    return list(dict.fromkeys([item for item in ids if item]))


def _resolve_list_api_mode(list_api: str = "auto", api_style: str = "auto") -> str:
    requested = (
        _clean(list_api)
        or _runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default="auto")
        or "auto"
    ).lower()
    if requested == "legacy":
        requested = "old"
    if requested in ("old", "new", "both"):
        return requested
    if requested != "auto":
        raise ValueError(_("不支持的钉钉列表接口模式：{0}").format(list_api))
    return "old" if _resolve_api_style(api_style) == "legacy" else "new"


def _list_process_instance_ids_by_legacy_api(
    *,
    token: str,
    process_code: str,
    start_time_ms: int,
    end_time_ms: int,
    page_size: int,
    max_pages: int,
) -> list[str]:
    ids: list[str] = []
    cursor = 0
    for _page_no in range(max_pages):
        result = _request_json(
            f"{_oapi_url()}{LEGACY_LIST_INSTANCE_IDS_PATH.format(access_token=quote(token, safe=''))}",
            method="POST",
            api_style="legacy",
            payload={
                "process_code": process_code,
                "start_time": start_time_ms,
                "end_time": end_time_ms,
                "size": min(page_size, 20),
                "cursor": cursor,
            },
        )
        _ensure_dingtalk_success(result, api_style="legacy")
        body = result.get("result") or {}
        ids.extend([_clean(item) for item in body.get("list") or [] if _clean(item)])
        next_cursor = body.get("next_cursor")
        if next_cursor in (None, "", cursor):
            break
        cursor = int(next_cursor)
    return ids


def _list_process_instance_ids_by_new_api(
    *,
    token: str,
    process_code: str,
    start_time_ms: int,
    end_time_ms: int,
    page_size: int,
    max_pages: int,
) -> list[str]:
    ids: list[str] = []
    next_token = ""
    for _page_no in range(max_pages):
        payload = {
            "processCode": process_code,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "maxResults": min(page_size, 20),
            "statuses": ["COMPLETED"],
        }
        if next_token:
            payload["nextToken"] = next_token
        result = _request_json(
            f"{_api_url()}{NEW_LIST_INSTANCE_IDS_PATH}",
            method="POST",
            token=token,
            api_style="new",
            payload=payload,
        )
        _ensure_dingtalk_success(result, api_style="new")
        body = _unwrap_result(result)
        ids.extend([_clean(item) for item in _first_list(body, "list", "instanceIds", "processInstanceIds") if _clean(item)])
        next_token = _clean(body.get("nextToken") or body.get("next_token"))
        if not next_token:
            break
    return ids


def get_process_instance_detail(*, token: str, process_instance_id: str, api_style: str = "auto") -> dict:
    """读取单个审批实例详情。"""

    instance_id = _clean(process_instance_id)
    if resolve_approval_source_mode() == "postgres":
        details = _get_postgres_approval_source().get_instances([instance_id])
        if instance_id not in details:
            raise LookupError(_("审批实例不在成本系统数据库白名单视图中：{0}").format(instance_id))
        return details[instance_id]
    resolved_api_style = _resolve_api_style(api_style)
    if resolved_api_style == "legacy":
        try:
            return _get_process_instance_detail_by_new_api(token=token, process_instance_id=instance_id)
        except Exception:
            return _get_process_instance_detail_by_legacy_api(token=token, process_instance_id=instance_id)

    return _get_process_instance_detail_by_new_api(token=token, process_instance_id=instance_id)


def _is_dingtalk_user_not_exist_response(result: dict) -> bool:
    text = json.dumps(result or {}, ensure_ascii=False)
    return any(marker in text for marker in ("userNotExist", "找不到该用户", "用户不存在"))


def _configured_attachment_union_id() -> str:
    for key in ("DINGTALK_ATTACHMENT_UNION_ID", "DINGTALK_DOWNLOAD_UNION_ID"):
        value = _clean(os.environ.get(key))
        if value:
            return value
    return ""


def _legacy_user_union_id(*, token: str, user_id: str) -> str:
    configured_union_id = _configured_attachment_union_id()
    if configured_union_id:
        return configured_union_id

    resolved_user_id = _clean(user_id)
    if not resolved_user_id:
        raise RuntimeError("钉钉附件备用下载链路缺少下载人 userId。")

    result = _request_json(
        f"{_oapi_url()}{LEGACY_USER_DETAIL_PATH.format(access_token=quote(token, safe=''))}",
        method="POST",
        api_style="legacy",
        payload={"userid": resolved_user_id, "language": "zh_CN"},
    )
    if result.get("errcode") in (88, "88") or "qyapi_get_member" in json.dumps(result, ensure_ascii=False):
        raise RuntimeError(
            "钉钉附件备用下载需要应用开通 qyapi_get_member 权限，或在 .env 配置 DINGTALK_ATTACHMENT_UNION_ID。"
        )
    _ensure_dingtalk_success(result, api_style="legacy")
    body = _unwrap_result(result)
    union_id = _clean(body.get("unionid") or body.get("unionId"))
    if not union_id:
        raise RuntimeError("钉钉用户详情响应中没有 unionId，无法继续下载审批附件。")
    return union_id


def _legacy_process_attachment_space_id(
    *,
    token: str,
    process_instance_id: str,
    file_id: str,
    user_id: str,
) -> str:
    resolved_user_id = _clean(user_id)
    if not resolved_user_id:
        raise RuntimeError("钉钉附件备用下载链路缺少下载人 userId。")

    result = _request_json(
        f"{_oapi_url()}{LEGACY_APPROVAL_ATTACHMENT_SPACE_PATH.format(access_token=quote(token, safe=''))}",
        method="POST",
        api_style="legacy",
        payload={
            "process_instance_id": process_instance_id,
            "file_id": file_id,
            "user_id": resolved_user_id,
        },
    )
    _ensure_dingtalk_success(result, api_style="legacy")
    body = _unwrap_result(result)
    space_id = _clean(body.get("space_id") or body.get("spaceId"))
    if not space_id:
        raise RuntimeError(f"钉钉审批附件空间响应中没有 space_id：{result}")
    return space_id


def _legacy_process_attachment_dentry_auth(
    *,
    token: str,
    file_id: str,
    space_id: str,
    user_id: str,
) -> dict:
    resolved_file_id = _clean(file_id)
    resolved_space_id = _clean(space_id)
    resolved_user_id = _clean(user_id)
    if not resolved_file_id or not resolved_space_id or not resolved_user_id:
        raise RuntimeError("钉钉旧版附件授权缺少 fileId、spaceId 或 userId。")

    result = _request_json(
        f"{_oapi_url()}{LEGACY_APPROVAL_ATTACHMENT_DENTRY_AUTH_PATH.format(access_token=quote(token, safe=''))}",
        method="POST",
        api_style="legacy",
        payload={
            "request": {
                "file_infos": [
                    {
                        "file_id": _dingtalk_id_value(resolved_file_id),
                        "space_id": _dingtalk_id_value(resolved_space_id),
                    }
                ],
                "userid": resolved_user_id,
            }
        },
    )
    _ensure_dingtalk_success(result, api_style="legacy")
    return result


def _legacy_process_attachment_file_url(
    *,
    token: str,
    process_instance_id: str,
    file_id: str,
) -> dict:
    resolved_instance_id = _clean(process_instance_id)
    resolved_file_id = _clean(file_id)
    if not resolved_instance_id or not resolved_file_id:
        raise RuntimeError("钉钉旧版附件下载缺少审批实例 ID 或 fileId。")
    return _request_json(
        f"{_oapi_url()}{LEGACY_APPROVAL_ATTACHMENT_DOWNLOAD_PATH.format(access_token=quote(token, safe=''))}",
        method="POST",
        api_style="legacy",
        payload={
            "request": {
                "process_instance_id": resolved_instance_id,
                "file_id": resolved_file_id,
            },
        },
    )


def _extract_storage_download_info(body: dict) -> tuple[str, dict]:
    download_info = body.get("downloadInfo") or body.get("download_info")
    if isinstance(download_info, dict):
        body = {**body, **download_info}

    header_info = body.get("headerSignatureInfo") or body.get("header_signature_info") or {}
    if not isinstance(header_info, dict):
        header_info = {}

    urls = (
        body.get("resourceUrls")
        or body.get("resource_urls")
        or header_info.get("resourceUrls")
        or header_info.get("resource_urls")
        or []
    )
    download_uri = _clean(
        body.get("downloadUri")
        or body.get("download_uri")
        or body.get("downloadUrl")
        or body.get("download_url")
        or body.get("resourceUrl")
        or body.get("resource_url")
        or body.get("url")
    )
    if not download_uri and isinstance(urls, list) and urls:
        download_uri = _clean(urls[0])

    headers = (
        body.get("headers")
        or body.get("downloadHeaders")
        or body.get("download_headers")
        or header_info.get("headers")
        or header_info.get("downloadHeaders")
        or header_info.get("download_headers")
        or {}
    )
    if not isinstance(headers, dict):
        headers = {}
    return download_uri, {str(key): str(value) for key, value in headers.items() if value not in (None, "")}


def _extract_attachment_download_uri(result: dict) -> str:
    body = _unwrap_result(result)
    return _clean(
        body.get("downloadUri")
        or body.get("download_uri")
        or body.get("downloadUrl")
        or body.get("download_url")
        or body.get("resourceUrl")
        or body.get("resource_url")
        or body.get("url")
    )


def _compact_dingtalk_response(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in ("errcode", "errmsg", "code", "message", "request_id", "requestId", "success"):
        if key in result:
            compact[key] = result.get(key)
    body = _unwrap_result(result)
    if body and body is not result:
        compact["result_keys"] = sorted(str(key) for key in body.keys())
        if _extract_attachment_download_uri(result):
            compact["download_uri_obtained"] = True
    return compact


def _diagnostic_step(name: str, fn) -> tuple[dict, Any]:
    try:
        value = fn()
    except Exception as exc:
        return {"step": name, "ok": False, "error": str(exc)}, None
    response = value if isinstance(value, dict) else None
    step = {"step": name, "ok": True}
    if response:
        step["response"] = _compact_dingtalk_response(response)
    return step, value


def _storage_dentry_download_info(
    *,
    token: str,
    space_id: str,
    dentry_id: str,
    union_id: str,
) -> tuple[str, dict, dict]:
    query = f"?unionId={quote(union_id, safe='')}" if union_id else ""
    result = _request_json(
        f"{_api_url()}{NEW_STORAGE_DENTRY_DOWNLOAD_INFO_PATH.format(space_id=quote(space_id, safe=''), dentry_id=quote(dentry_id, safe=''))}{query}",
        method="POST",
        token=token,
        api_style="new",
        payload={"option": {"version": 1, "preferIntranet": False}},
    )
    _ensure_dingtalk_success(result, api_style="new")
    body = _unwrap_result(result)
    download_uri, download_headers = _extract_storage_download_info(body)
    if not download_uri:
        raise RuntimeError(f"钉钉钉盘下载信息响应中没有下载地址：{result}")
    return download_uri, download_headers, result


def _extract_storage_thumbnail_info(body: dict, *, dentry_id: str) -> tuple[str, dict]:
    items = (
        body.get("resultItems")
        or body.get("result_items")
        or body.get("items")
        or body.get("list")
        or []
    )
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []

    requested_dentry_id = _clean(dentry_id)
    fallback_item: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate_id = _clean(item.get("dentryId") or item.get("dentry_id") or item.get("fileId") or item.get("file_id"))
        if requested_dentry_id and candidate_id and candidate_id != requested_dentry_id:
            fallback_item = fallback_item or item
            continue
        thumbnail = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else item
        url = _clean(
            thumbnail.get("url")
            or thumbnail.get("downloadUrl")
            or thumbnail.get("download_url")
            or thumbnail.get("resourceUrl")
            or thumbnail.get("resource_url")
        )
        if url:
            return url, item
        fallback_item = fallback_item or item

    thumbnail = fallback_item.get("thumbnail") if isinstance(fallback_item.get("thumbnail"), dict) else fallback_item
    url = _clean(
        thumbnail.get("url")
        or thumbnail.get("downloadUrl")
        or thumbnail.get("download_url")
        or thumbnail.get("resourceUrl")
        or thumbnail.get("resource_url")
    )
    if url:
        return url, fallback_item
    raise RuntimeError("钉钉缩略图响应中没有可下载的 thumbnail.url。")


def _storage_dentry_thumbnail_info(
    *,
    token: str,
    space_id: str,
    dentry_id: str,
    union_id: str,
) -> tuple[str, dict, dict]:
    resolved_dentry_id = _clean(dentry_id)
    if not resolved_dentry_id:
        raise RuntimeError("钉钉缩略图下载缺少 dentryId。")
    query = f"?unionId={quote(union_id, safe='')}" if union_id else ""
    result = _request_json(
        f"{_api_url()}{NEW_STORAGE_THUMBNAILS_QUERY_PATH.format(space_id=quote(space_id, safe=''))}{query}",
        method="POST",
        token=token,
        api_style="new",
        payload={
            "dentryIds": [resolved_dentry_id],
            "thumbnailOptions": {"size": "large"},
        },
    )
    _ensure_dingtalk_success(result, api_style="new")
    body = _unwrap_result(result)
    thumbnail_uri, thumbnail_item = _extract_storage_thumbnail_info(body, dentry_id=resolved_dentry_id)
    return thumbnail_uri, {}, {"raw_response": result, "thumbnail_item": thumbnail_item}


def _new_auth_process_attachment_download(
    *,
    token: str,
    user_id: str,
    space_id: str,
    file_id: str,
) -> dict:
    resolved_user_id = _clean(user_id)
    resolved_space_id = _clean(space_id)
    resolved_file_id = _clean(file_id)
    if not resolved_user_id or not resolved_space_id or not resolved_file_id:
        raise RuntimeError("钉钉新版附件授权下载缺少 userId、spaceId 或 fileId。")
    result = _request_json(
        f"{_api_url()}{NEW_APPROVAL_ATTACHMENT_AUTH_DOWNLOAD_PATH}",
        method="POST",
        token=token,
        api_style="new",
        payload={
            "userId": resolved_user_id,
            "fileInfos": [
                {
                    "spaceId": _dingtalk_id_value(resolved_space_id),
                    "fileId": resolved_file_id,
                }
            ],
        },
    )
    _ensure_dingtalk_success(result, api_style="new")
    return result


def _extract_storage_dentries(body: dict) -> list[dict]:
    for key in ("dentries", "list", "items", "results", "data"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _storage_dentry_identity(row: dict) -> tuple[str, str]:
    dentry_id = _clean(
        row.get("dentryId")
        or row.get("dentry_id")
        or row.get("fileId")
        or row.get("file_id")
        or row.get("id")
    )
    file_name = _clean(
        row.get("name")
        or row.get("fileName")
        or row.get("file_name")
        or row.get("title")
    )
    return dentry_id, file_name


def _list_storage_dentries(
    *,
    token: str,
    space_id: str,
    union_id: str,
) -> tuple[list[dict], dict]:
    query = urlencode({"parentId": "0", "maxResults": 100, "unionId": union_id})
    result = _request_json(
        f"{_api_url()}{NEW_STORAGE_DENTRY_LIST_PATH.format(space_id=quote(space_id, safe=''))}?{query}",
        method="GET",
        token=token,
        api_style="new",
    )
    _ensure_dingtalk_success(result, api_style="new")
    body = _unwrap_result(result)
    return _extract_storage_dentries(body), result


def _resolve_storage_dentry_id(
    *,
    token: str,
    space_id: str,
    union_id: str,
    file_id: str,
    file_name: str,
) -> tuple[str, dict]:
    dentries, result = _list_storage_dentries(token=token, space_id=space_id, union_id=union_id)
    resolved_file_id = _clean(file_id)
    resolved_file_name = _clean(file_name)
    for row in dentries:
        dentry_id, listed_name = _storage_dentry_identity(row)
        if resolved_file_id and dentry_id == resolved_file_id:
            return dentry_id, result
        if resolved_file_name and listed_name == resolved_file_name:
            return dentry_id, result
    if resolved_file_name:
        normalized_target = _normalize_key(resolved_file_name)
        for row in dentries:
            dentry_id, listed_name = _storage_dentry_identity(row)
            if normalized_target and _normalize_key(listed_name) == normalized_target:
                return dentry_id, result
    raise RuntimeError(
        "钉钉已允许读取附件空间文件列表，但没有找到与当前附件文件名匹配的文件。"
    )


def _drive_file_download_info(
    *,
    token: str,
    space_id: str,
    file_id: str,
    union_id: str,
) -> tuple[str, dict, dict]:
    query = f"?unionId={quote(union_id, safe='')}" if union_id else ""
    result = _request_json(
        f"{_api_url()}{NEW_DRIVE_FILE_DOWNLOAD_INFO_PATH.format(space_id=quote(space_id, safe=''), file_id=quote(file_id, safe=''))}{query}",
        method="GET",
        token=token,
        api_style="new",
    )
    _ensure_dingtalk_success(result, api_style="new")
    body = _unwrap_result(result)
    download_uri, download_headers = _extract_storage_download_info(body)
    if not download_uri:
        raise RuntimeError(f"钉钉钉盘文件下载信息响应中没有下载地址：{result}")
    return download_uri, download_headers, result


def _fallback_process_attachment_download_url(
    *,
    legacy_token: str,
    process_instance_id: str,
    file_id: str,
    file_name: str = "",
    user_id: str,
    corp_id: str,
) -> dict:
    space_id = _legacy_process_attachment_space_id(
        token=legacy_token,
        process_instance_id=process_instance_id,
        file_id=file_id,
        user_id=user_id,
    )
    legacy_auth_result = {}
    legacy_auth_error = ""
    try:
        legacy_auth_result = _legacy_process_attachment_dentry_auth(
            token=legacy_token,
            file_id=file_id,
            space_id=space_id,
            user_id=user_id,
        )
        legacy_download_result = _legacy_process_attachment_file_url(
            token=legacy_token,
            process_instance_id=process_instance_id,
            file_id=file_id,
        )
        _ensure_dingtalk_success(legacy_download_result, api_style="legacy")
        legacy_download_uri = _extract_attachment_download_uri(legacy_download_result)
        if legacy_download_uri:
            return {
                "space_id": space_id,
                "union_id_obtained": False,
                "download_uri": legacy_download_uri,
                "download_headers": {},
                "fallback_api": "legacy_dentry_auth_then_file_url",
                "storage_response": {
                    "legacy_auth_response": legacy_auth_result,
                    "download_response": legacy_download_result,
                },
            }
    except Exception as exc:
        legacy_auth_error = str(exc)

    union_id = _legacy_user_union_id(token=legacy_token, user_id=user_id)
    new_token = get_access_token(api_style="new", corp_id=corp_id)
    auth_result = {}
    auth_error = ""
    try:
        auth_result = _new_auth_process_attachment_download(
            token=new_token,
            user_id=user_id,
            space_id=space_id,
            file_id=file_id,
        )
        direct_result = _request_json(
            f"{_api_url()}{NEW_APPROVAL_ATTACHMENT_DOWNLOAD_PATH}",
            method="POST",
            token=new_token,
            api_style="new",
            payload={
                "processInstanceId": process_instance_id,
                "fileId": file_id,
            },
        )
        _ensure_dingtalk_success(direct_result, api_style="new")
        direct_uri = _extract_attachment_download_uri(direct_result)
        if direct_uri:
            return {
                "space_id": space_id,
                "union_id_obtained": bool(union_id),
                "download_uri": direct_uri,
                "download_headers": {},
                "fallback_api": "new_auth_then_approval_download",
                "storage_response": {
                    "legacy_auth_response": legacy_auth_result,
                    "legacy_auth_error": legacy_auth_error,
                    "auth_response": auth_result,
                    "download_response": direct_result,
                },
            }
    except Exception as exc:
        auth_error = str(exc)
    try:
        download_uri, download_headers, storage_response = _storage_dentry_download_info(
            token=new_token,
            space_id=space_id,
            dentry_id=file_id,
            union_id=union_id,
        )
        fallback_api = "storage_dentry_download_info"
    except Exception as storage_exc:
        try:
            dentry_id, list_response = _resolve_storage_dentry_id(
                token=new_token,
                space_id=space_id,
                union_id=union_id,
                file_id=file_id,
                file_name=file_name,
            )
            download_uri, download_headers, storage_response = _storage_dentry_download_info(
                token=new_token,
                space_id=space_id,
                dentry_id=dentry_id,
                union_id=union_id,
            )
            storage_response = {
                "legacy_auth_response": legacy_auth_result,
                "legacy_auth_error": legacy_auth_error,
                "auth_response": auth_result,
                "auth_error": auth_error,
                "original_storage_error": str(storage_exc),
                "dentry_list_response": list_response,
                "download_response": storage_response,
                "resolved_dentry_id": dentry_id,
            }
            fallback_api = "storage_dentry_list_then_download_info"
        except Exception as list_exc:
            download_uri, download_headers, thumbnail_response = _storage_dentry_thumbnail_info(
                token=new_token,
                space_id=space_id,
                dentry_id=file_id,
                union_id=union_id,
            )
            storage_response = {
                "legacy_auth_response": legacy_auth_result,
                "legacy_auth_error": legacy_auth_error,
                "auth_response": auth_result,
                "auth_error": auth_error,
                "original_storage_error": str(storage_exc),
                "dentry_list_error": str(list_exc),
                "thumbnail_response": thumbnail_response,
            }
            fallback_api = "storage_thumbnail_query"
    return {
        "space_id": space_id,
        "union_id_obtained": bool(union_id),
        "download_uri": download_uri,
        "download_headers": download_headers,
        "fallback_api": fallback_api,
        "storage_response": {
            "legacy_auth_response": legacy_auth_result,
            "legacy_auth_error": legacy_auth_error,
            "auth_response": auth_result,
            "auth_error": auth_error,
            "download_response": storage_response,
        },
    }


def get_process_attachment_download_url(
    *,
    token: str,
    process_instance_id: str,
    file_id: str,
    file_name: str = "",
    space_id: str = "",
    user_id: str = "",
    corp_id: str = "",
    api_style: str = "auto",
) -> dict:
    """换取钉钉审批发起表单附件的临时下载地址。"""

    instance_id = _clean(process_instance_id)
    resolved_file_id = _clean(file_id)
    resolved_space_id = _clean(space_id)
    resolved_user_id = _clean(user_id)
    resolved_corp_id = _clean(corp_id)
    if not instance_id:
        raise ValueError(_("缺少钉钉审批实例 ID。"))
    if not resolved_file_id:
        raise ValueError(_("缺少钉钉附件 file_id。"))

    resolved_api_style = _resolve_api_style(api_style)
    if resolved_api_style == "legacy":
        result = _request_json(
            f"{_oapi_url()}{LEGACY_APPROVAL_ATTACHMENT_DOWNLOAD_PATH.format(access_token=quote(token, safe=''))}",
            method="POST",
            api_style="legacy",
            payload={
                "request": {
                    "process_instance_id": instance_id,
                    "file_id": resolved_file_id,
                },
            },
        )
        if _is_dingtalk_user_not_exist_response(result):
            fallback = _fallback_process_attachment_download_url(
                legacy_token=token,
                process_instance_id=instance_id,
                file_id=resolved_file_id,
                file_name=file_name,
                user_id=resolved_user_id,
                corp_id=resolved_corp_id,
            )
            return {
                "process_instance_id": instance_id,
                "file_id": resolved_file_id,
                "user_id": resolved_user_id,
                "download_uri": fallback["download_uri"],
                "download_headers": fallback.get("download_headers") or {},
                "space_id": fallback.get("space_id") or "",
                "union_id_obtained": fallback.get("union_id_obtained") or False,
                "api_style": resolved_api_style,
                "fallback_api": fallback.get("fallback_api") or "",
                "raw_response": result,
                "storage_response": fallback.get("storage_response") or {},
            }
    else:
        if resolved_space_id and resolved_user_id:
            _new_auth_process_attachment_download(
                token=token,
                user_id=resolved_user_id,
                space_id=resolved_space_id,
                file_id=resolved_file_id,
            )
        result = _request_json(
            f"{_api_url()}{NEW_APPROVAL_ATTACHMENT_DOWNLOAD_PATH}",
            method="POST",
            token=token,
            api_style="new",
            payload={
                "processInstanceId": instance_id,
                "fileId": resolved_file_id,
            },
        )
    _ensure_dingtalk_success(result, api_style=resolved_api_style)
    body = _unwrap_result(result)
    download_uri = _clean(
        body.get("downloadUri")
        or body.get("download_uri")
        or body.get("downloadUrl")
        or body.get("download_url")
        or body.get("url")
    )
    if not download_uri:
        raise RuntimeError(f"钉钉附件下载地址响应中没有 downloadUri：{result}")
    return {
        "process_instance_id": instance_id,
        "file_id": resolved_file_id,
        "space_id": resolved_space_id,
        "user_id": resolved_user_id,
        "download_uri": download_uri,
        "download_headers": {},
        "api_style": resolved_api_style,
        "raw_response": result,
    }


def diagnose_process_attachment_download(
    *,
    token: str,
    process_instance_id: str,
    file_id: str,
    file_name: str = "",
    space_id: str = "",
    user_id: str = "",
    corp_id: str = "",
    api_style: str = "auto",
) -> dict:
    """诊断审批发起附件自动下载链路，不保存文件内容。"""

    instance_id = _clean(process_instance_id)
    resolved_file_id = _clean(file_id)
    resolved_space_id = _clean(space_id)
    resolved_user_id = _clean(user_id)
    resolved_corp_id = _clean(corp_id)
    resolved_api_style = _resolve_api_style(api_style)
    steps: list[dict] = []
    download_uri_obtained = False
    downloaded_by = ""
    storage_response: dict | None = None

    if not instance_id or not resolved_file_id:
        return {
            "ok": False,
            "api_style": resolved_api_style,
            "message": "缺少审批实例 ID 或 fileId，不能诊断附件下载。",
            "steps": steps,
        }

    if resolved_api_style == "legacy":
        legacy_step, legacy_result = _diagnostic_step(
            "legacy_file_url",
            lambda: _legacy_process_attachment_file_url(
                token=token,
                process_instance_id=instance_id,
                file_id=resolved_file_id,
            ),
        )
        steps.append(legacy_step)
        if legacy_result and _extract_attachment_download_uri(legacy_result):
            download_uri_obtained = True
            downloaded_by = "legacy_file_url"

        if not download_uri_obtained and resolved_user_id:
            space_step, space_result = _diagnostic_step(
                "legacy_cspace_info",
                lambda: _request_json(
                    f"{_oapi_url()}{LEGACY_APPROVAL_ATTACHMENT_SPACE_PATH.format(access_token=quote(token, safe=''))}",
                    method="POST",
                    api_style="legacy",
                    payload={
                        "process_instance_id": instance_id,
                        "file_id": resolved_file_id,
                        "user_id": resolved_user_id,
                    },
                ),
            )
            steps.append(space_step)
            if space_result:
                try:
                    _ensure_dingtalk_success(space_result, api_style="legacy")
                    resolved_space_id = _clean(_unwrap_result(space_result).get("space_id") or _unwrap_result(space_result).get("spaceId"))
                except Exception:
                    pass

        if not download_uri_obtained and resolved_space_id and resolved_user_id:
            auth_step, _auth_result = _diagnostic_step(
                "legacy_dentry_auth",
                lambda: _legacy_process_attachment_dentry_auth(
                    token=token,
                    file_id=resolved_file_id,
                    space_id=resolved_space_id,
                    user_id=resolved_user_id,
                ),
            )
            steps.append(auth_step)
            retry_step, retry_result = _diagnostic_step(
                "legacy_file_url_after_auth",
                lambda: _legacy_process_attachment_file_url(
                    token=token,
                    process_instance_id=instance_id,
                    file_id=resolved_file_id,
                ),
            )
            steps.append(retry_step)
            if retry_result and _extract_attachment_download_uri(retry_result):
                download_uri_obtained = True
                downloaded_by = "legacy_dentry_auth_then_file_url"

    if not download_uri_obtained and resolved_user_id:
        union_id = ""
        union_step, union_result = _diagnostic_step(
            "legacy_user_union_id",
            lambda: _request_json(
                f"{_oapi_url()}{LEGACY_USER_DETAIL_PATH.format(access_token=quote(token, safe=''))}",
                method="POST",
                api_style="legacy",
                payload={"userid": resolved_user_id, "language": "zh_CN"},
            ),
        )
        steps.append(union_step)
        if union_result:
            try:
                _ensure_dingtalk_success(union_result, api_style="legacy")
                union_id = _clean(_unwrap_result(union_result).get("unionid") or _unwrap_result(union_result).get("unionId"))
            except Exception:
                union_id = ""

        new_token = ""
        new_token_step, new_token_result = _diagnostic_step(
            "new_access_token",
            lambda: {"result": {"accessToken": get_access_token(api_style="new", corp_id=resolved_corp_id)}},
        )
        steps.append({"step": "new_access_token", "ok": new_token_step["ok"], "token_obtained": bool(new_token_result)})
        if new_token_result:
            new_token = _clean(_unwrap_result(new_token_result).get("accessToken"))

        if new_token and resolved_space_id:
            new_auth_step, _new_auth_result = _diagnostic_step(
                "new_auth_download",
                lambda: _new_auth_process_attachment_download(
                    token=new_token,
                    user_id=resolved_user_id,
                    space_id=resolved_space_id,
                    file_id=resolved_file_id,
                ),
            )
            steps.append(new_auth_step)

            new_download_step, new_download_result = _diagnostic_step(
                "new_approval_download",
                lambda: _request_json(
                    f"{_api_url()}{NEW_APPROVAL_ATTACHMENT_DOWNLOAD_PATH}",
                    method="POST",
                    token=new_token,
                    api_style="new",
                    payload={"processInstanceId": instance_id, "fileId": resolved_file_id},
                ),
            )
            steps.append(new_download_step)
            if new_download_result and _extract_attachment_download_uri(new_download_result):
                download_uri_obtained = True
                downloaded_by = "new_auth_then_approval_download"

            if not download_uri_obtained and union_id:
                storage_step, storage_result = _diagnostic_step(
                    "storage_dentry_download_info",
                    lambda: _request_json(
                        f"{_api_url()}{NEW_STORAGE_DENTRY_DOWNLOAD_INFO_PATH.format(space_id=quote(resolved_space_id, safe=''), dentry_id=quote(resolved_file_id, safe=''))}?unionId={quote(union_id, safe='')}",
                        method="POST",
                        token=new_token,
                        api_style="new",
                        payload={"option": {"version": 1, "preferIntranet": False}},
                    ),
                )
                steps.append(storage_step)
                storage_response = storage_result if isinstance(storage_result, dict) else None
                if storage_result:
                    uri, _headers = _extract_storage_download_info(_unwrap_result(storage_result))
                    if uri:
                        download_uri_obtained = True
                        downloaded_by = "storage_dentry_download_info"

    return {
        "ok": download_uri_obtained,
        "api_style": resolved_api_style,
        "process_instance_id": instance_id,
        "file_id": resolved_file_id,
        "file_name": file_name,
        "space_id": resolved_space_id,
        "user_id_configured": bool(resolved_user_id),
        "download_uri_obtained": download_uri_obtained,
        "downloaded_by": downloaded_by,
        "storage_response": _compact_dingtalk_response(storage_response),
        "steps": steps,
    }


def _get_process_instance_detail_by_legacy_api(*, token: str, process_instance_id: str) -> dict:
    result = _request_json(
        f"{_oapi_url()}{LEGACY_INSTANCE_DETAIL_PATH.format(access_token=quote(token, safe=''))}",
        method="POST",
        api_style="legacy",
        payload={"process_instance_id": process_instance_id},
    )
    _ensure_dingtalk_success(result, api_style="legacy")
    process_instance = result.get("process_instance") or result.get("result", {}).get("process_instance") or result.get("result") or result
    return _normalize_legacy_instance(process_instance, process_instance_id)


def _get_process_instance_detail_by_new_api(*, token: str, process_instance_id: str) -> dict:
    result = _request_json(
        f"{_api_url()}{NEW_INSTANCE_DETAIL_PATH.format(process_instance_id=quote(process_instance_id, safe=''))}",
        method="GET",
        token=token,
        api_style="new",
    )
    _ensure_dingtalk_success(result, api_style="new")
    return _unwrap_result(result)


def _normalize_legacy_instance(instance: dict, process_instance_id: str) -> dict:
    if not isinstance(instance, dict):
        return {"processInstanceId": process_instance_id}

    mapping = {
        "process_instance_id": "processInstanceId",
        "business_id": "businessId",
        "originator_userid": "originatorUserId",
        "originator_dept_id": "originatorDeptId",
        "form_component_values": "formComponentValues",
        "create_time": "createTime",
        "finish_time": "finishTime",
    }
    normalized = dict(instance)
    for old_key, new_key in mapping.items():
        if old_key in normalized and new_key not in normalized:
            normalized[new_key] = normalized[old_key]
    normalized.setdefault("processInstanceId", process_instance_id)
    return normalized


def _unwrap_result(result: dict) -> dict:
    for key in ("result", "data", "processInstance"):
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return result


def _first_list(source: dict, *keys: str) -> list:
    for key in keys:
        value = source.get(key)
        if isinstance(value, list):
            return value
    return []


def _get_form_components(instance: dict) -> list:
    components = (
        instance.get("form_component_values")
        or instance.get("formComponentValues")
        or instance.get("form_component_values_v2")
        or []
    )
    if isinstance(components, str):
        components = _parse_json_text(components)
    return components if isinstance(components, list) else []


def _iter_form_components(instance: dict):
    stack = list(reversed(_get_form_components(instance)))
    while stack:
        component = stack.pop()
        if not isinstance(component, dict):
            continue
        yield component
        for key in ("details", "children", "items"):
            children = component.get(key)
            if isinstance(children, str):
                children = _parse_json_text(children)
            if isinstance(children, list):
                stack.extend(reversed(children))


def extract_form_fields(instance: dict) -> dict[str, Any]:
    """把钉钉表单组件拍平成字段字典。"""

    fields: dict[str, Any] = {}

    def visit(component: Any) -> None:
        if not isinstance(component, dict):
            return
        name = _clean(
            component.get("name")
            or component.get("label")
            or component.get("bizAlias")
            or component.get("componentName")
            or component.get("id")
        )
        value = _parse_json_text(component.get("value"))
        ext_value = _parse_json_text(component.get("ext_value") or component.get("extValue"))
        if name:
            resolved_value = value if value not in (None, "") else ext_value
            if _field_matches_alias(name, BUSINESS_ENTITY_FIELD_ALIASES + PROJECT_OWNERSHIP_FIELD_ALIASES):
                ext_entity = _extract_dingtalk_entity_value(ext_value)
                value_entity = _extract_dingtalk_entity_value(value)
                if ext_entity.get("name"):
                    resolved_value = ext_value
                elif value_entity.get("name"):
                    resolved_value = value
            if name not in fields or fields[name] in (None, ""):
                fields[name] = resolved_value
        for key in ("details", "children", "items"):
            children = component.get(key)
            if isinstance(children, str):
                children = _parse_json_text(children)
            if isinstance(children, list):
                for child in children:
                    visit(child)
        for row in _iter_detail_rows(value):
            for row_key, row_value in row.items():
                if row_key and row_key not in fields:
                    fields[row_key] = row_value

    for component in _get_form_components(instance):
        visit(component)
    return fields


def _iter_detail_rows(value: Any):
    parsed = _parse_json_text(value)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                yield from _flatten_detail_row(item)
    elif isinstance(parsed, dict):
        yield from _flatten_detail_row(parsed)


def _flatten_detail_row(row: dict) -> list[dict]:
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        parsed_value = _parse_json_text(value)
        if isinstance(parsed_value, dict):
            name = _clean(parsed_value.get("name") or parsed_value.get("label") or key)
            cell_value = parsed_value.get("value")
            flattened[name] = cell_value if cell_value not in (None, "") else parsed_value.get("extValue")
        else:
            flattened[_clean(key)] = parsed_value
    return [flattened] if flattened else []


def _find_field_value(fields: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key, value in fields.items():
        if _field_matches_alias(key, aliases):
            if value not in (None, ""):
                return value
    return ""


def _find_field_entry(fields: dict[str, Any], aliases: tuple[str, ...]) -> tuple[str, Any]:
    for key, value in fields.items():
        if _field_matches_alias(key, aliases):
            if value not in (None, ""):
                return key, value
    return "", ""


def _field_matches_alias(fieldname: Any, aliases: tuple[str, ...]) -> bool:
    normalized_fieldname = _normalize_key(fieldname)
    normalized_aliases = [_normalize_key(alias) for alias in aliases]
    return any(alias and alias in normalized_fieldname for alias in normalized_aliases)


def _extract_dingtalk_entity_values(value: Any) -> list[dict[str, str]]:
    parsed = _parse_json_text(value)
    if isinstance(parsed, list):
        entities: list[dict[str, str]] = []
        for item in parsed:
            entities.extend(_extract_dingtalk_entity_values(item))
        return _unique_dingtalk_entities(entities)
    if isinstance(parsed, dict):
        for key in ("selectedOptions", "selected_options", "options", "items", "list", "data"):
            nested_value = parsed.get(key)
            if isinstance(nested_value, str):
                nested_value = _parse_json_text(nested_value)
            if isinstance(nested_value, (list, dict)):
                nested_entities = _extract_dingtalk_entity_values(nested_value)
                if nested_entities:
                    return nested_entities
        name = _clean(
            parsed.get("deptName")
            or parsed.get("name")
            or parsed.get("label")
            or parsed.get("displayName")
            or parsed.get("displayValue")
            or parsed.get("optionName")
            or parsed.get("orgName")
            or parsed.get("corpName")
            or parsed.get("companyName")
            or parsed.get("businessName")
            or parsed.get("title")
            or parsed.get("text")
            or parsed.get("value")
        )
        entity_id = _clean(
            parsed.get("itemId")
            or parsed.get("deptId")
            or parsed.get("id")
            or parsed.get("value")
        )
        return [{"name": name, "id": entity_id}] if name or entity_id else []
    text = _clean(parsed)
    return [{"name": text, "id": ""}] if text else []


def _unique_dingtalk_entities(entities: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        normalized = {"name": _clean(entity.get("name")), "id": _clean(entity.get("id"))}
        identity = (normalized["id"], normalized["name"])
        if not any(identity) or identity in seen:
            continue
        seen.add(identity)
        result.append(normalized)
    return result


def _extract_dingtalk_entity_value(value: Any) -> dict[str, str]:
    entities = _extract_dingtalk_entity_values(value)
    return entities[0] if entities else {"name": "", "id": ""}


def extract_project_candidates_from_approval(item: dict) -> dict:
    """Return every selected project department; never choose the first of many."""

    form_fields = item.get("form_fields") or {}
    source_field, raw_value = _find_field_entry(form_fields, PROJECT_OWNERSHIP_FIELD_ALIASES)
    candidates = _extract_dingtalk_entity_values(raw_value)
    if not candidates:
        stack = list(reversed(item.get("raw_form_components") or item.get("form_component_values") or item.get("formComponentValues") or []))
        while stack:
            component = stack.pop()
            if not isinstance(component, dict):
                continue
            for child_key in ("details", "children", "items"):
                children = component.get(child_key)
                if isinstance(children, str):
                    children = _parse_json_text(children)
                if isinstance(children, list):
                    stack.extend(reversed(children))
            name = _clean(component.get("name") or component.get("label") or component.get("bizAlias") or component.get("componentName") or component.get("id"))
            if not _field_matches_alias(name, PROJECT_OWNERSHIP_FIELD_ALIASES):
                continue
            source_field = name
            raw_value = component.get("ext_value") or component.get("extValue") or component.get("value")
            candidates = _extract_dingtalk_entity_values(raw_value)
            if candidates:
                break
    return {
        "candidates": candidates,
        "source_field": source_field,
        "source": "dingtalk_form_project_ownership" if source_field else "",
    }


def _extract_subsidiary_from_form_components(components: Any) -> dict[str, str]:
    if not isinstance(components, list):
        return {"subsidiary_code": "", "business_entity_name": "", "business_entity_id": "", "source_field": "", "source": ""}

    stack = list(reversed(components))
    while stack:
        component = stack.pop()
        if not isinstance(component, dict):
            continue
        for child_key in ("details", "children", "items"):
            children = component.get(child_key)
            if isinstance(children, str):
                children = _parse_json_text(children)
            if isinstance(children, list):
                stack.extend(reversed(children))
        source_field = _clean(
            component.get("name")
            or component.get("label")
            or component.get("bizAlias")
            or component.get("componentName")
            or component.get("id")
        )
        component_type = _clean(component.get("componentType") or component.get("component_type")).lower()
        value = component.get("value")
        ext_value = component.get("ext_value") or component.get("extValue")
        raw_value = ext_value if ext_value not in (None, "") else value
        entity = _extract_dingtalk_entity_value(raw_value)
        if not (entity.get("name") or entity.get("id")):
            continue

        if _field_matches_alias(source_field, BUSINESS_ENTITY_FIELD_ALIASES):
            name = entity.get("name") or entity.get("id") or ""
        elif component_type in {"departmentfield", "deptfield", "organizationfield", "companyfield"}:
            name = entity.get("name") or entity.get("id") or ""
        else:
            continue

        if not name:
            continue
        return {
            "subsidiary_code": name,
            "business_entity_name": entity.get("name") or "",
            "business_entity_id": entity.get("id") or "",
            "source_field": source_field,
            "source": "dingtalk_form_business_entity" if source_field else "",
        }

    return {"subsidiary_code": "", "business_entity_name": "", "business_entity_id": "", "source_field": "", "source": ""}


def extract_subsidiary_from_approval(item: dict) -> dict[str, str]:
    """从钉钉表单提取归属子公司/业务主体，当前直接使用中文主体名称。"""

    form_fields = item.get("form_fields") or {}
    source_field, raw_value = _find_field_entry(form_fields, BUSINESS_ENTITY_FIELD_ALIASES)
    entity = _extract_dingtalk_entity_value(raw_value)
    name = entity.get("name") or entity.get("id") or ""
    if not name:
        raw_components = item.get("raw_form_components") or item.get("form_component_values") or item.get("formComponentValues") or []
        fallback = _extract_subsidiary_from_form_components(raw_components)
        if fallback.get("subsidiary_code"):
            return fallback
    return {
        "subsidiary_code": name,
        "business_entity_name": entity.get("name") or "",
        "business_entity_id": entity.get("id") or "",
        "source_field": source_field,
        "source": "dingtalk_form_business_entity" if source_field else "",
    }


def _component_name_matches_business_entity(name: Any) -> bool:
    normalized = _normalize_key(name)
    if not normalized:
        return False
    extra_tokens = ("empresa", "entidad", "businessentity", "company", "公司", "主体")
    return _field_matches_alias(name, BUSINESS_ENTITY_FIELD_ALIASES) or any(token in normalized for token in extra_tokens)


def _collect_business_entity_debug_candidates(item: dict, *, max_count: int = 12) -> list[dict]:
    candidates: list[dict] = []
    form_fields = item.get("form_fields") or {}
    for fieldname, value in form_fields.items():
        if not _component_name_matches_business_entity(fieldname):
            continue
        entity = _extract_dingtalk_entity_value(value)
        candidates.append(
            {
                "source": "form_fields",
                "field": _clean(fieldname),
                "entity_name": entity.get("name") or "",
                "entity_id": entity.get("id") or "",
                "raw_type": type(value).__name__,
                "raw_preview": _clean(value)[:160],
            }
        )
        if len(candidates) >= max_count:
            return candidates

    stack = list(reversed(item.get("raw_form_components") or item.get("form_component_values") or item.get("formComponentValues") or []))
    while stack and len(candidates) < max_count:
        component = stack.pop()
        if not isinstance(component, dict):
            continue
        for child_key in ("details", "children", "items"):
            children = component.get(child_key)
            if isinstance(children, str):
                children = _parse_json_text(children)
            if isinstance(children, list):
                stack.extend(reversed(children))
        name = _clean(
            component.get("name")
            or component.get("label")
            or component.get("bizAlias")
            or component.get("componentName")
            or component.get("id")
        )
        component_type = _clean(component.get("componentType") or component.get("component_type"))
        if not _component_name_matches_business_entity(name) and _clean(component_type).lower() not in {
            "departmentfield",
            "deptfield",
            "organizationfield",
            "companyfield",
        }:
            continue
        raw_value = component.get("ext_value") or component.get("extValue") or component.get("value")
        entity = _extract_dingtalk_entity_value(raw_value)
        candidates.append(
            {
                "source": "raw_form_components",
                "field": name,
                "component_type": component_type,
                "entity_name": entity.get("name") or "",
                "entity_id": entity.get("id") or "",
                "raw_type": type(raw_value).__name__,
                "raw_preview": _clean(raw_value)[:160],
            }
        )
    return candidates


def _normalize_currency_code(value: Any) -> str:
    normalized = _normalize_key(value)
    if not normalized:
        return ""
    if any(token in normalized for token in ("rmb", "cny", "人民币", "renminbi")):
        return "RMB"
    if any(token in normalized for token in ("usd", "dólar", "dolar", "dolares", "dólares", "美元", "美金")):
        return "USD"
    if any(token in normalized for token in ("mxn", "peso", "pesos", "比索", "墨西哥")):
        return "MXN"
    return ""


def _normalize_allocation_basis(value: Any) -> str:
    text = _normalize_key(value)
    if text in {"goods_value", "gross_weight", "volume", "chargeable_weight"}:
        return text
    if text in {"货值", "按货值", "按货值分摊"} or "货值" in text:
        return "goods_value"
    if text in {"计费重", "计费重量", "体积重", "按计费重", "按计费重量", "按计费重分摊"} or "计费重" in text:
        return "chargeable_weight"
    if text in {"体积", "方数", "按体积", "按方数", "按体积分摊"} or "体积" in text or "方数" in text:
        return "volume"
    if text in {"毛重", "重量", "按毛重", "按重量", "按毛重分摊", "按重量分摊"} or "毛重" in text or "重量" in text:
        return "gross_weight"
    return "gross_weight"


def _parse_money_amount(value: Any, *, allow_zero: bool = False) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and (number > 0 or allow_zero and number == 0) else None

    text = _clean(value).replace("，", ",")
    if not text:
        return None

    candidates: list[tuple[int, int, float]] = []
    for match in re.finditer(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
        raw_number = match.group(0).replace(",", "")
        try:
            number = float(raw_number)
        except ValueError:
            continue
        if not math.isfinite(number) or number < 0 or number == 0 and not allow_zero:
            continue

        before = text[max(0, match.start() - 12) : match.start()].lower()
        after = text[match.end() : match.end() + 12].lower()
        if re.match(r"\s*(hq|gp|kg|kgs|cbm|m3|pcs|pc|件|个|箱|柜|天|day)", after):
            continue
        score = 1
        if _normalize_currency_code(f"{before}{after}") or any(symbol in before + after for symbol in ("$", "￥", "¥")):
            score = 3
        candidates.append((score, -match.start(), number))

    if not candidates:
        return None
    return max(candidates)[2]


def extract_logistics_fee_from_approval(item: dict) -> dict:
    """只提取明确填写的物流费用，报价说明不能直接入账。"""

    form_fields = item.get("form_fields") or {}
    currency_field, currency_raw = _find_field_entry(form_fields, LOGISTICS_CURRENCY_FIELD_ALIASES)
    explicit_currency = _normalize_currency_code(currency_raw)
    source_field, raw_value = _find_field_entry(form_fields, LOGISTICS_FEE_FIELD_ALIASES)
    amount = _parse_money_amount(raw_value)
    if amount is None:
        return {}
    currency = _normalize_currency_code(raw_value) or explicit_currency or "RMB"
    return {
        "amount": amount,
        "currency": currency,
        "source_label": "物流费用",
        "source_field": source_field,
        "source_value": raw_value,
        "currency_field": currency_field,
        "currency_value": currency_raw,
    }


def extract_logistics_fees_from_approval(item: dict) -> list[dict]:
    """Extract positive OA estimates into canonical logical fee descriptors."""

    from overseas_costing.services import fee_service

    fees: list[dict] = []
    mode = detect_approval_transport_mode(
        item.get("transport_mode") or item.get("transport_mode_raw") or (item.get("form_fields") or {})
    )
    primary = item.get("logistics_fee") if isinstance(item.get("logistics_fee"), dict) else {}
    primary = primary or extract_logistics_fee_from_approval(item)
    primary_amount = _parse_money_amount(primary.get("amount")) if primary else None
    if primary_amount is not None and mode:
        definition = fee_service.primary_freight_definition(mode)
        fees.append(
            {
                **primary,
                **definition,
                "amount": primary_amount,
                "currency": _normalize_currency_code(primary.get("currency")) or "RMB",
                "rule_code": "oa_logistics_freight",
            }
        )

    form_fields = item.get("form_fields") or {}
    templates = {row["logical_fee_key"]: row for row in fee_service.build_default_fee_templates(mode)}
    for logical_key, label, basis, aliases in OPTIONAL_LOGISTICS_FEE_FIELDS:
        source_field, raw_value = _find_field_entry(form_fields, aliases)
        amount = _parse_money_amount(raw_value)
        if amount is None:
            continue
        template = templates.get(logical_key) or {}
        fees.append(
            {
                "logical_fee_key": logical_key,
                "rule_code": f"oa_{logical_key}",
                "expense_category": template.get("expense_category") or label,
                "allocation_basis": template.get("allocation_basis") or basis,
                "basis_field": template.get("basis_field") or basis,
                "required_evidence_role": template.get("required_evidence_role") or "expense_invoice",
                "amount": amount,
                "currency": _normalize_currency_code(raw_value) or "MXN",
                "source_label": label,
                "source_field": source_field,
                "source_value": raw_value,
            }
        )
    return fees


def _parse_quote_total_amount(value: Any) -> float | None:
    """报价合计行优先取等号后经验证的总额。"""

    text = _clean(value).replace("，", ",")
    if not text:
        return None
    amount_text = text.rsplit("=", 1)[1] if "=" in text else text
    matches = _quote_money_amount_matches(amount_text)
    if not matches:
        return None
    try:
        amount = float(matches[-1][0].replace(",", ""))
    except ValueError:
        return None
    return amount if amount > 0 else None


_ENGLISH_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
)
_MONEY_CONTEXT_WINDOW = 64


def _money_match_is_date(
    text: str,
    match: re.Match,
    *,
    number_before_currency: bool,
) -> bool:
    """Return whether this money-shaped candidate is actually part of a date."""

    number_text = match.group("number").lstrip("+-").replace(",", "")
    if re.fullmatch(r"\d{8}", number_text):
        try:
            datetime.strptime(number_text, "%Y%m%d")
        except ValueError:
            pass
        else:
            return True

    number_start = match.start("number")
    number_end = match.end("number")
    after_number = text[number_end:number_end + _MONEY_CONTEXT_WINDOW]
    if re.match(r"\s*(?:年|月|日(?!期)|[./\-]\s*\d)", after_number):
        return True
    if re.match(
        rf"\s*(?:[-./]\s*)?{_ENGLISH_MONTH}\b\s*[-./,]?\s*\d{{4}}\b",
        after_number,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(r"\d{4}", number_text) and re.match(
        rf"\s*{_ENGLISH_MONTH}\b\s+\d{{1,2}}\b",
        after_number,
        re.IGNORECASE,
    ):
        return True

    before_number = text[max(0, number_start - _MONEY_CONTEXT_WINDOW):number_start]
    nearest_before_number = before_number.rstrip()[-1:]
    if number_before_currency and nearest_before_number and nearest_before_number in "./-":
        return True

    prefix = text[max(0, match.start() - _MONEY_CONTEXT_WINDOW):match.start()].rstrip()
    if re.search(r"(?:日期|日期为|date(?:d)?)\s*[:：]?\s*$", prefix, re.IGNORECASE):
        return True
    if re.search(
        rf"{_ENGLISH_MONTH}\s+\d{{1,2}}\s*,?\s*$|{_ENGLISH_MONTH}\s*$|[年月日]\s*$",
        prefix,
        re.IGNORECASE,
    ):
        return True
    return False


def _quote_money_amount_matches(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Return validated money text and span, excluding unit/date suffix matches."""

    currency_token = r"(?:元|rmb|cny|¥|￥|usd|us\$|美金|美元|mxn|peso|比索)"
    number_token = r"[-+]?\d[\d,]*(?:\.\d+)?"
    patterns = (
        (re.compile(
            rf"(?P<currency>{currency_token})\s*[:：]?\s*(?P<number>{number_token})",
            re.IGNORECASE,
        ), False),
        (re.compile(
            rf"(?P<number>{number_token})\s*(?P<currency>{currency_token})",
            re.IGNORECASE,
        ), True),
    )
    rejected_suffix = re.compile(
        r"^(?:m[3³]|cbm\b|kgs?\b|公斤|方|立方|件|pcs?\b|[%％]|"
        r"/(?:方|立方|箱|票|件|公斤|cbm|m[3³]|kg|kgs?|pcs?|packages?|shipments?)"
        r"(?![A-Za-z0-9_])|[/\-]\s*\d)",
        re.IGNORECASE,
    )
    matches = []
    seen_spans = set()
    for pattern, number_before_currency in patterns:
        for match in pattern.finditer(text):
            if number_before_currency:
                number_start = match.start("number")
                if number_start:
                    previous = text[number_start - 1]
                    if (not previous.isspace()
                            and (previous.isalnum() or previous in "._,/-/%％")):
                        continue
            if _money_match_is_date(
                text, match, number_before_currency=number_before_currency
            ):
                continue
            # Inspect after the complete greedy number match in Python rather
            # than a regex lookahead that may backtrack 2026 to 202, etc.
            number_end = match.end("number")
            money_end = match.end()
            after_number = text[
                number_end:number_end + _MONEY_CONTEXT_WINDOW
            ].lstrip()
            after_money = text[
                money_end:money_end + _MONEY_CONTEXT_WINDOW
            ].lstrip()
            if rejected_suffix.search(after_number) or rejected_suffix.search(after_money):
                continue
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            matches.append((match.group("number"), span))
    distinct = []
    for amount, span in sorted(matches, key=lambda value: value[1]):
        amount_key = amount.replace(",", "").lstrip("+")
        if any(
            amount_key == existing_amount.replace(",", "").lstrip("+")
            and span[0] < existing_span[1]
            and existing_span[0] < span[1]
            for existing_amount, existing_span in distinct
        ):
            continue
        distinct.append((amount, span))
    return distinct


def _looks_like_quote_amount_line(line: str) -> bool:
    """识别物流报价里的金额行，避免把重量、日期、型号误当成费用。"""

    text = _clean(line)
    if not text:
        return False
    if re.search(
        r"/(?:方|立方|箱|票|件|公斤|cbm|m3|kg|kgs?|pcs?|packages?|shipments?)",
        text,
        re.IGNORECASE,
    ) and "=" not in text:
        return False
    amount_text = text.rsplit("=", 1)[1] if "=" in text else text
    money_matches = _quote_money_amount_matches(amount_text)
    if re.search(
        r"(?:合计|总计|总额|总费用|总价|grand\s+total|total(?:\s+amount)?)",
        text,
        re.IGNORECASE,
    ):
        return len(money_matches) == 1
    if _fee_term_binds_money(text, money_matches):
        return len(money_matches) == 1
    if "=" not in text:
        return False
    return len(money_matches) == 1


_EXPLICIT_FEE_TERM_PATTERN = re.compile(
    r"(?:国际)?(?:空运|海运|快递|物流)?运费|"
    r"物流费|快递费|港杂(?:费)?|货代(?:附加)?费|附加费|"
    r"(?:(?:air|sea|ocean|international|express)\s+)?freight(?:\s+(?:fee|charge|cost))?|"
    r"(?:logistics|shipping|port|forwarder)\s+(?:fee|charge|cost)|"
    r"express\s+surcharge",
    re.IGNORECASE,
)
_FEE_MONEY_CONNECTORS = frozenset(
    {
        "", "金额", "为", "共", "计", "合计", "合计金额", "总计", "总额",
        "应付", "实付", "约", "预计", "预估", "amount", "total", "totalamount",
    }
)


def _fee_term_binds_money(
    text: str, money_matches: list[tuple[str, tuple[int, int]]]
) -> bool:
    """Require one explicit fee term to bind directly to the money span."""

    if len(money_matches) != 1:
        return False
    money_start = money_matches[0][1][0]
    for match in _EXPLICIT_FEE_TERM_PATTERN.finditer(text):
        if match.end() > money_start:
            continue
        connector = re.sub(
            r"[\s:：,，;；()（）\-—]", "", text[match.end():money_start]
        ).lower()
        if connector in _FEE_MONEY_CONNECTORS:
            return True
    return False


def _parse_direct_quote_line(line: str) -> dict | None:
    """识别“飞力达PIL：5850USD/40HQ+杂费”这类报价比较行。"""

    text = _clean(line)
    if not text:
        return None
    match = re.search(
        r"^(?P<carrier>.+?)[:：]\s*(?P<amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<currency>usd|美金|美元|rmb|cny|元|mxn|peso|比索)(?P<tail>.*)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    tail = _clean(match.group("tail"))
    if re.search(
        r"/(?:方|立方|箱|票|件|公斤|cbm|m3|kg|kgs?|pcs?|packages?|shipments?)",
        tail,
        re.IGNORECASE,
    ):
        # The amount immediately after the colon is a rate.  If the line also
        # contains an equals sign, the total-line parser will take its RHS.
        return None
    if _quote_money_amount_matches(tail):
        return None
    carrier = _clean(match.group("carrier")).strip("：:")
    if not carrier or len(carrier) > 40:
        return None
    if any(skip in carrier for skip in ("合计", "总计", "总费用", "总价", "单价", "每kg", "每KG", "运费", "卸货费", "装货费", "仓租", "免租期", "超期")):
        return None
    try:
        amount = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None
    if amount <= 0:
        return None
    return {
        "carrier": carrier,
        "amount": amount,
        "currency": _normalize_currency_code(match.group("currency")) or _normalize_currency_code(text) or "RMB",
        "remark": tail,
    }


def extract_logistics_quote_candidates_from_approval(item: dict) -> list[dict]:
    """从物流报价文字中提取待确认候选，不生成任何费用分摊。"""

    form_fields = item.get("form_fields") or {}
    source_field, raw_value = _find_field_entry(form_fields, LOGISTICS_QUOTE_FIELD_ALIASES)
    text = _clean(raw_value)
    if not source_field or not text:
        return []

    volume_match = re.search(r"(?:预估方数|体积)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:方|立方|cbm|m3)", text, re.IGNORECASE)
    volume_m3 = float(volume_match.group(1)) if volume_match else None
    candidates: list[dict] = []
    carrier = ""
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = _clean(raw_line)
        if not line:
            continue
        carrier_match = re.search(r"^\s*(?:\d+\s*[.、]?\s*)?(.+?)报价", line)
        if carrier_match:
            carrier = _clean(carrier_match.group(1)).strip("：:")
        # A rate and its explicitly labelled total may share a line.
        rate_total = re.search(
            r"(?P<rate>\d[\d,]*(?:\.\d+)?)\s*元\s*/\s*(?P<unit>立方|方|cbm|m3|kg|公斤)"
            r".*?(?:费用|总价|总额|合计)\s*[:：=]\s*[¥￥]?\s*(?P<total>\d[\d,]*(?:\.\d+)?)\s*(?:元|RMB|CNY)",
            line, re.IGNORECASE,
        )
        if rate_total and carrier_match:
            candidates.append({"carrier": carrier, "amount": float(rate_total["total"].replace(",", "")),
                "unit_rate": float(rate_total["rate"].replace(",", "")), "amount_kind": "total_amount",
                "currency": "RMB", "volume_m3": volume_m3,
                "pricing_basis": "weight" if rate_total["unit"].lower() in {"kg", "公斤"} else "volume",
                "source_field": source_field, "source_value": text, "evidence_line": line,
                "evidence_line_no": line_no, "status": "待确认"})
            continue
        compact_quote = re.search(
            r"^\s*(?:\d+\s*[.、]?\s*)?(?P<carrier>[^,，:：]{1,40}?)报价\s*[,，:：]?\s*"
            r"(?P<amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
            r"(?P<currency>usd|美金|美元|rmb|cny|元|mxn|peso|比索)\s*$",
            line,
            re.IGNORECASE,
        )
        if compact_quote:
            candidates.append(
                {
                    "carrier": _clean(compact_quote.group("carrier")),
                    "amount": float(compact_quote.group("amount").replace(",", "")),
                    "currency": _normalize_currency_code(compact_quote.group("currency")) or "RMB",
                    "volume_m3": volume_m3,
                    "source_field": source_field,
                    "source_value": text,
                    "evidence_line": line,
                    "evidence_line_no": line_no,
                    "status": "待确认",
                }
            )
            continue
        direct_quote = _parse_direct_quote_line(line)
        if direct_quote:
            candidates.append(
                {
                    "carrier": direct_quote["carrier"],
                    "amount": direct_quote["amount"],
                    "currency": direct_quote["currency"],
                    "volume_m3": volume_m3,
                    "source_field": source_field,
                    "source_value": text,
                    "evidence_line": line,
                    "evidence_line_no": line_no,
                    "status": "待确认",
                    "remark": direct_quote.get("remark") or "",
                }
            )
            continue
        if not _looks_like_quote_amount_line(line):
            continue
        amount = _parse_quote_total_amount(line)
        if amount is None:
            continue
        candidate = {
                "carrier": carrier,
                "amount": amount,
                "currency": _normalize_currency_code(line) or "RMB",
                "volume_m3": volume_m3,
                "source_field": source_field,
                "source_value": text,
                "evidence_line": line,
                "evidence_line_no": line_no,
                "status": "待确认",
            }
        before_total = line.rsplit("=", 1)[0] if "=" in line else line
        if re.search(r"/(?:方|立方|cbm|m3)", before_total, re.IGNORECASE):
            candidate["pricing_basis"] = "volume"
        elif re.search(r"/(?:kg|kgs?)\b", before_total, re.IGNORECASE):
            candidate["pricing_basis"] = "weight"
        candidates.append(candidate)
    return candidates


def _build_logistics_ai_source_text(form_fields: dict[str, Any]) -> str:
    """把审批正文的普通字段整理给 AI 兜底识别，避开附件/明细表等大块数据。"""

    lines: list[str] = []
    for fieldname, value in (form_fields or {}).items():
        if isinstance(value, (dict, list)):
            continue
        text = _clean(value)
        if not text:
            continue
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > 1200:
            text = f"{text[:1200]}..."
        lines.append(f"{_clean(fieldname)}: {text}")
    return "\n".join(lines)[:MAX_AI_LOGISTICS_TEXT_LENGTH]


def _should_ai_parse_logistics_text(base_summary: dict, source_text: str) -> bool:
    if not source_text or not _runtime_config_bool(
        "OVERSEAS_COST_AI_TEXT_PARSE_ENABLED",
        "overseas_cost_ai_text_parse_enabled",
        default=True,
    ):
        return False
    has_quote_marker = bool(re.search(r"(报价|运费|物流费|quote|cotizaci|dhl|fedex|ups|合计|总计)", source_text, re.IGNORECASE))
    has_logistics_marker = bool(
        re.search(r"(物流方式|camino|重量|peso|预计发货|pre[- ]?entrega|目标地区|destinat|目的地)", source_text, re.IGNORECASE)
    )
    if not (has_quote_marker or has_logistics_marker):
        return False
    missing_quote = not base_summary.get("logistics_quote_amount") and has_quote_marker
    missing_core = not any(
        base_summary.get(key)
        for key in ("transport_mode", "gross_weight_kg", "pre_delivery_date", "destination", "logistics_no")
    )
    return missing_quote or missing_core


def _call_ai_logistics_text_summary(source_text: str, base_summary: dict) -> dict:
    """调用现有 DeepSeek/OpenAI 兼容配置，兜底识别审批正文基础字段。"""

    try:
        from overseas_costing.services import allocation_service

        config = allocation_service._ai_config()
        if not config.get("api_key"):
            return {}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是钉钉国际物流审批正文的基础字段识别助手。"
                    "只能从用户提供的原文中抽取字段，不得猜测、不得改写物料明细、不得新增费用。"
                    "输出必须是 JSON 对象；没有明确值就返回空字符串或 null。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "从审批正文中识别整票物流基础信息。报价金额只取最终总额，不取每kg单价。",
                        "output_schema": {
                            "transport_mode": "SEA/AIR/EXPRESS 或空",
                            "transport_mode_raw": "原文中的物流方式",
                            "logistics_no": "柜号、运单号或单号",
                            "pre_delivery_date": "预计发货日期，保持原文日期即可",
                            "destination": "目标地区/目的地",
                            "gross_weight_kg": "整票重量KG，数字",
                            "logistics_quote_amount": "物流报价最终总金额，数字",
                            "logistics_quote_currency": "RMB/USD/MXN",
                            "logistics_quote_carrier": "承运商/货代，例如 DHL",
                            "logistics_quote_evidence": "能证明报价金额的原文短句",
                            "confidence": "0-1 数字",
                            "reason": "一句中文说明",
                        },
                        "already_parsed_by_rules": base_summary,
                        "approval_text": source_text,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
        content = allocation_service._call_chat_completions(config, messages)
        parsed = allocation_service._extract_json_object(content)
        if not isinstance(parsed, dict):
            return {}
        return _normalize_ai_logistics_text_summary(parsed, config.get("model"))
    except Exception as exc:
        return {"ai_used": False, "ai_error": str(exc)[:240]}


def _normalize_ai_logistics_text_summary(parsed: dict, model: str | None = "") -> dict:
    mode_raw = _clean(parsed.get("transport_mode_raw") or parsed.get("transport_mode"))
    amount = _parse_money_amount(parsed.get("logistics_quote_amount"))
    weight = _to_number_or_none(parsed.get("gross_weight_kg"))
    confidence = _to_number_or_none(parsed.get("confidence"))
    return {
        "transport_mode": detect_approval_transport_mode(parsed.get("transport_mode") or mode_raw),
        "transport_mode_raw": mode_raw,
        "logistics_no": _clean(parsed.get("logistics_no")),
        "pre_delivery_date": _clean(parsed.get("pre_delivery_date")),
        "destination": _clean(parsed.get("destination")),
        "gross_weight_kg": weight,
        "logistics_quote_amount": amount,
        "logistics_quote_currency": _normalize_currency_code(parsed.get("logistics_quote_currency")) if parsed.get("logistics_quote_currency") else "",
        "logistics_quote_carrier": _clean(parsed.get("logistics_quote_carrier")),
        "logistics_quote_evidence": _clean(parsed.get("logistics_quote_evidence")),
        "ai_used": True,
        "ai_model": _clean(model),
        "ai_confidence": confidence,
        "ai_reason": _clean(parsed.get("reason")),
    }


def _merge_ai_logistics_text_summary(base_summary: dict, ai_summary: dict) -> dict:
    if not ai_summary:
        return base_summary

    merged = dict(base_summary)
    changed = False
    fill_fields = (
        "transport_mode",
        "transport_mode_raw",
        "logistics_no",
        "pre_delivery_date",
        "destination",
        "gross_weight_kg",
        "logistics_quote_amount",
        "logistics_quote_currency",
        "logistics_quote_carrier",
        "logistics_quote_evidence",
    )
    for fieldname in fill_fields:
        current = merged.get(fieldname)
        candidate = ai_summary.get(fieldname)
        if current not in (None, "") or candidate in (None, ""):
            continue
        merged[fieldname] = candidate
        changed = True

    if changed:
        merged["ai_used"] = bool(ai_summary.get("ai_used"))
        merged["ai_model"] = ai_summary.get("ai_model") or ""
        merged["ai_confidence"] = ai_summary.get("ai_confidence")
        merged["ai_reason"] = ai_summary.get("ai_reason") or ""
    elif ai_summary.get("ai_error"):
        merged["ai_error"] = ai_summary.get("ai_error")
    return merged


def extract_logistics_text_summary_from_approval(item: dict, *, allow_ai: bool = True) -> dict:
    """提取钉钉国际物流审批正文里的整票基础信息。"""

    form_fields = item.get("form_fields") or {}
    quote_field, quote_text = _find_field_entry(form_fields, LOGISTICS_QUOTE_FIELD_ALIASES)
    pre_delivery_field, pre_delivery_date = _find_field_entry(form_fields, LOGISTICS_PRE_DELIVERY_FIELD_ALIASES)
    destination_field, destination = _find_field_entry(form_fields, LOGISTICS_DESTINATION_FIELD_ALIASES)
    weight_field, weight_value = _find_field_entry(form_fields, LOGISTICS_WEIGHT_FIELD_ALIASES)
    transport_mode_raw = item.get("transport_mode_raw") or _find_field_value(form_fields, TRANSPORT_FIELD_ALIASES)
    logistics_no = item.get("logistics_no") or _find_field_value(form_fields, BATCH_NO_FIELD_ALIASES)

    quote_candidates = item.get("logistics_quote_candidates")
    if not isinstance(quote_candidates, list):
        quote_candidates = extract_logistics_quote_candidates_from_approval(item)
    first_quote = quote_candidates[0] if quote_candidates else {}

    summary = {
        "transport_mode": detect_approval_transport_mode(transport_mode_raw),
        "transport_mode_raw": _clean(transport_mode_raw),
        "logistics_no": _clean(logistics_no),
        "pre_delivery_date": _clean(pre_delivery_date),
        "pre_delivery_field": pre_delivery_field,
        "destination": _clean(destination),
        "destination_field": destination_field,
        "gross_weight_kg": _to_number_or_none(weight_value),
        "gross_weight_field": weight_field,
        "logistics_quote_field": quote_field,
        "logistics_quote_text": _clean(quote_text),
        "logistics_quote_amount": first_quote.get("amount"),
        "logistics_quote_currency": first_quote.get("currency"),
        "logistics_quote_carrier": first_quote.get("carrier"),
        "logistics_quote_evidence": first_quote.get("evidence_line"),
    }
    source_text = _build_logistics_ai_source_text(form_fields)
    if allow_ai and _should_ai_parse_logistics_text(summary, source_text):
        summary = _merge_ai_logistics_text_summary(
            summary,
            _call_ai_logistics_text_summary(source_text, summary),
        )
    return summary


def _find_component_value(instance: dict, aliases: tuple[str, ...]) -> Any:
    normalized_aliases = [_normalize_key(alias) for alias in aliases]
    for component in _iter_form_components(instance):
        name = _clean(
            component.get("name")
            or component.get("label")
            or component.get("bizAlias")
            or component.get("componentName")
            or component.get("id")
        )
        normalized_name = _normalize_key(name)
        if not any(alias and alias in normalized_name for alias in normalized_aliases):
            continue
        value = _parse_json_text(component.get("value"))
        if value not in (None, ""):
            return value
        ext_value = _parse_json_text(component.get("ext_value") or component.get("extValue"))
        if ext_value not in (None, ""):
            return ext_value
    return ""


def _is_purchase_relate_component(component: dict) -> bool:
    component_type = _clean(component.get("componentType") or component.get("component_type"))
    name = _clean(
        component.get("name")
        or component.get("label")
        or component.get("bizAlias")
        or component.get("componentName")
        or component.get("id")
    )
    normalized_name = _normalize_key(name)
    if component_type == "RelateField":
        return any(_normalize_key(alias) in normalized_name for alias in PURCHASE_RELATE_FIELD_ALIASES)
    return any(_normalize_key(alias) in normalized_name for alias in PURCHASE_RELATE_FIELD_ALIASES)


def _relation_display_values(component: dict) -> list[str]:
    value = _parse_json_text(component.get("value"))
    if isinstance(value, list):
        display_values: list[str] = []
        for item in value:
            if isinstance(item, dict):
                display_values.append(
                    _clean(
                        item.get("title")
                        or item.get("processInstanceTitle")
                        or item.get("name")
                        or item.get("label")
                        or item.get("value")
                    )
                )
            else:
                display_values.append(_clean(item))
        return display_values
    text = _clean(value)
    return [text] if text else []


def _looks_like_linked_approval_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(
        _clean(value.get(key))
        for key in (
            "businessId",
            "business_id",
            "bizId",
            "approvalNo",
            "approval_no",
            "procInstId",
            "processInstanceId",
            "process_instance_id",
            "instanceId",
            "instance_id",
            "url",
            "detailUrl",
            "officialUrl",
        )
    )


def _iter_linked_approval_payloads(value: Any):
    parsed = _parse_json_text(value)
    if isinstance(parsed, dict):
        if _looks_like_linked_approval_payload(parsed):
            yield parsed
        for child in parsed.values():
            yield from _iter_linked_approval_payloads(child)
    elif isinstance(parsed, list):
        for child in parsed:
            yield from _iter_linked_approval_payloads(child)


def _relation_ext_items(component: dict) -> list:
    ext_value = _parse_json_text(component.get("ext_value") or component.get("extValue"))
    nested_items = list(_iter_linked_approval_payloads(ext_value))
    if nested_items:
        return nested_items
    if isinstance(ext_value, dict):
        for key in ("list", "items", "data", "value"):
            value = ext_value.get(key)
            if isinstance(value, str):
                value = _parse_json_text(value)
            if isinstance(value, list):
                return value
        return [ext_value]
    if isinstance(ext_value, list):
        return ext_value
    return []


def _build_linked_approval_record(component: dict, raw_item: Any, display_name: str = "") -> dict:
    if not isinstance(raw_item, dict):
        return {}

    official_url = _clean(raw_item.get("url") or raw_item.get("detailUrl") or raw_item.get("officialUrl"))
    approval_no = _clean(
        raw_item.get("businessId")
        or raw_item.get("business_id")
        or raw_item.get("bizId")
        or raw_item.get("approvalNo")
        or raw_item.get("approval_no")
    )
    instance_id = _clean(
        raw_item.get("procInstId")
        or raw_item.get("processInstanceId")
        or raw_item.get("process_instance_id")
        or raw_item.get("instanceId")
        or raw_item.get("instance_id")
    )
    if not instance_id:
        instance_id = extract_dingtalk_instance_id(official_url)
    title = _clean(raw_item.get("title") or raw_item.get("processInstanceTitle") or raw_item.get("name") or display_name)
    if not approval_no and not instance_id and not official_url:
        return {}

    payload = build_dingtalk_order_payload(
        approval_no=approval_no,
        instance_id=instance_id,
        official_url=official_url,
    )
    source_field = _clean(component.get("name") or component.get("label") or component.get("id"))
    return {
        "approval_no": approval_no,
        "source_approval_no": approval_no,
        "source_instance_id": instance_id,
        "business_id": approval_no,
        "proc_inst_id": instance_id,
        "title": title,
        "display_name": display_name or title,
        "source_field": source_field,
        "source_dingtalk_url": official_url,
        "open_url": payload.get("open_url") or "",
    }


def _has_purchase_approval_keyword(*values: Any) -> bool:
    normalized_text = _normalize_key(" ".join(_clean(value) for value in values if value not in (None, "")))
    return any(keyword in normalized_text for keyword in PURCHASE_APPROVAL_KEYWORDS)


def extract_linked_purchase_approvals(instance: dict) -> list[dict]:
    """从国际物流 OA 的关联审批控件里提取采购支出审批编号和实例 ID。"""

    approvals: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add_record(component: dict, raw_item: Any, display_name: str = "", *, require_keyword: bool = False) -> None:
        record = _build_linked_approval_record(component, raw_item, display_name=display_name)
        if not record:
            return
        if require_keyword and not _has_purchase_approval_keyword(
            record.get("title"),
            record.get("display_name"),
            record.get("source_field"),
            raw_item.get("title") if isinstance(raw_item, dict) else "",
            raw_item.get("processInstanceTitle") if isinstance(raw_item, dict) else "",
        ):
            return
        key = (
            record.get("approval_no", ""),
            record.get("source_instance_id", ""),
            "" if (record.get("approval_no") or record.get("source_instance_id")) else record.get("display_name", ""),
        )
        if key in seen:
            return
        seen.add(key)
        approvals.append(record)

    for component in _iter_form_components(instance):
        if not _is_purchase_relate_component(component):
            continue
        display_values = _relation_display_values(component)
        for index, raw_item in enumerate(_relation_ext_items(component)):
            display_name = display_values[index] if index < len(display_values) else ""
            add_record(component, raw_item, display_name=display_name)

    for raw_item in _iter_linked_approval_payloads(instance):
        add_record({}, raw_item, require_keyword=True)
    return approvals


def _first_dict_value(source: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return ""


def _looks_like_attachment_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    has_file_name = any(_clean(value.get(key)) for key in ATTACHMENT_FILE_NAME_KEYS)
    if not has_file_name:
        return False
    return any(
        _clean(value.get(key))
        for key in (
            *ATTACHMENT_FILE_ID_KEYS,
            *ATTACHMENT_FILE_URL_KEYS,
            *ATTACHMENT_SPACE_ID_KEYS,
            "fileSize",
            "file_size",
            "size",
            "extension",
            "fileType",
            "file_type",
        )
    ) or any(key in value for key in ("fileName", "file_name"))


def _file_extension(file_name: str, payload: dict | None = None) -> str:
    payload = payload or {}
    explicit = _clean(payload.get("extension") or payload.get("fileType") or payload.get("file_type"))
    if explicit:
        return explicit.lower().lstrip(".")
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix


def _guess_oa_attachment_type(file_name: str, payload: dict | None = None) -> str:
    normalized_name = _normalize_key(file_name)
    if any(keyword in normalized_name for keyword in ("完税", "税单", "pedimento", "taxcertificate")):
        return "Tax Certificate"
    if any(keyword in normalized_name for keyword in ("发票", "invoice", "factura")):
        return "Commercial Invoice"
    if any(
        keyword in normalized_name
        for keyword in ("装箱单", "装箱计划", "装柜清单", "发货清单", "快递清单", "物品清单", "packing", "packinglist")
    ):
        return "Packing List"
    if any(keyword in normalized_name for keyword in ("运费", "物流", "报价", "燃油", "dhl", "fedex", "ups", "bill")):
        return "Logistics Bill"
    return "Other"


def _build_attachment_record(
    payload: dict,
    *,
    source_field: str = "",
    component_type: str = "",
    value_source: str = "",
) -> dict:
    file_name = _clean(_first_dict_value(payload, ATTACHMENT_FILE_NAME_KEYS))
    file_id = _clean(_first_dict_value(payload, ATTACHMENT_FILE_ID_KEYS))
    file_url = _clean(_first_dict_value(payload, ATTACHMENT_FILE_URL_KEYS))
    space_id = _clean(_first_dict_value(payload, ATTACHMENT_SPACE_ID_KEYS))
    file_size = _first_dict_value(payload, ("fileSize", "file_size", "size"))
    extension = _file_extension(file_name, payload)
    attachment_type = _guess_oa_attachment_type(file_name, payload)
    return {
        "source": "oa_form_attachment",
        "source_field": source_field,
        "component_type": component_type,
        "value_source": value_source,
        "file_id": file_id,
        "space_id": space_id,
        "file_name": file_name,
        "file_url": file_url,
        "file_size": file_size,
        "file_ext": extension,
        "attachment_type": attachment_type,
        "raw": payload,
    }


def _extract_attachments_from_value(
    value: Any,
    *,
    source_field: str = "",
    component_type: str = "",
    value_source: str = "",
) -> list[dict]:
    parsed = _parse_json_text(value)
    if isinstance(parsed, dict):
        records: list[dict] = []
        if _looks_like_attachment_payload(parsed):
            records.append(
                _build_attachment_record(
                    parsed,
                    source_field=source_field,
                    component_type=component_type,
                    value_source=value_source,
                )
            )
        for child in parsed.values():
            records.extend(
                _extract_attachments_from_value(
                    child,
                    source_field=source_field,
                    component_type=component_type,
                    value_source=value_source,
                )
            )
        return records
    if isinstance(parsed, list):
        records: list[dict] = []
        for child in parsed:
            records.extend(
                _extract_attachments_from_value(
                    child,
                    source_field=source_field,
                    component_type=component_type,
                    value_source=value_source,
                )
            )
        return records
    return []


def _dedupe_attachment_records(records: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in records:
        file_name = _clean(record.get("file_name"))
        file_id = _clean(record.get("file_id"))
        file_url = _clean(record.get("file_url"))
        source_field = _clean(record.get("source_field"))
        if not file_name and not file_id and not file_url:
            continue
        key = (file_id, file_url, file_name, source_field)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def extract_form_attachments(instance: dict) -> list[dict]:
    """只从审批发起表单字段提取附件；评论附件暂不纳入。"""

    records: list[dict] = []
    for component in _iter_form_components(instance):
        source_field = _clean(
            component.get("name")
            or component.get("label")
            or component.get("bizAlias")
            or component.get("componentName")
            or component.get("id")
        )
        component_type = _clean(component.get("componentType") or component.get("component_type"))
        if component_type and "attachment" not in component_type.lower():
            continue
        records.extend(
            _extract_attachments_from_value(
                component.get("value"),
                source_field=source_field,
                component_type=component_type,
                value_source="value",
            )
        )
        records.extend(
            _extract_attachments_from_value(
                component.get("ext_value") or component.get("extValue"),
                source_field=source_field,
                component_type=component_type,
                value_source="extValue",
            )
        )
    return _dedupe_attachment_records(records)


def extract_approval_attachments(instance: dict) -> list[dict]:
    """提取审批附件，并保留来源信息。"""

    records = [
        {
            **record,
            "source": "oa_form_attachment",
            "origin": "Form",
        }
        for record in extract_form_attachments(instance)
    ]
    operation_records = instance.get("operationRecords") or instance.get("operation_records") or instance.get("comments") or []
    if isinstance(operation_records, dict):
        operation_records = [operation_records]
    for operation in operation_records if isinstance(operation_records, list) else []:
        if not isinstance(operation, dict):
            continue
        comment_user_id = _clean(
            operation.get("userId")
            or operation.get("user_id")
            or operation.get("operatorUserId")
            or operation.get("operator_user_id")
        )
        comment_time = operation.get("date") or operation.get("createTime") or operation.get("create_time") or ""
        comment_remark = _clean(operation.get("remark") or operation.get("comment") or operation.get("content"))
        comment_records = _extract_attachments_from_value(
            operation,
            source_field="operationRecords",
            component_type="CommentAttachment",
            value_source="operationRecords",
        )
        records.extend(
            {
                **record,
                "source": "oa_comment_attachment",
                "origin": "Comment",
                "comment_user_id": comment_user_id,
                "comment_time": comment_time,
                "comment_remark": comment_remark,
            }
            for record in comment_records
        )

    deduped: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for record in records:
        file_id = _clean(record.get("file_id"))
        if file_id:
            key = ("file_id", file_id)
        else:
            key = (
                "fallback",
                _clean(record.get("file_url")),
                _clean(record.get("file_name")),
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def extract_attachments_from_form_fields(form_fields: dict[str, Any]) -> list[dict]:
    """从已拍平的表单字段中兜底提取附件清单。"""

    records: list[dict] = []
    normalized_aliases = tuple(_normalize_key(alias) for alias in ATTACHMENT_FIELD_ALIASES)
    for fieldname, value in (form_fields or {}).items():
        normalized_fieldname = _normalize_key(fieldname)
        if normalized_fieldname and not any(alias in normalized_fieldname for alias in normalized_aliases):
            continue
        records.extend(
            _extract_attachments_from_value(
                value,
                source_field=_clean(fieldname),
                component_type="",
                value_source="form_fields",
            )
        )
    return _dedupe_attachment_records(records)


def is_sea_approval(fields: dict[str, Any], *, sea_keywords: tuple[str, ...] = DEFAULT_SEA_KEYWORDS) -> bool:
    transport_value = _find_field_value(fields, TRANSPORT_FIELD_ALIASES)
    detected = detect_approval_transport_mode(transport_value)
    if detected:
        return detected == "SEA"
    normalized = _normalize_key(transport_value).upper()
    return any(_normalize_key(keyword).upper() in normalized for keyword in sea_keywords)


def detect_approval_transport_mode(fields_or_value: Any) -> str:
    if isinstance(fields_or_value, dict):
        fields_or_value = _find_field_value(fields_or_value, TRANSPORT_FIELD_ALIASES)
    return _normalize_transport_mode(fields_or_value)


def is_transport_approval(fields: dict[str, Any], transport_modes: tuple[str, ...]) -> bool:
    mode = detect_approval_transport_mode(fields)
    return bool(mode and mode in transport_modes)


def _is_invalid_approval_result(result: str | None) -> bool:
    normalized = _clean(result).upper()
    if not normalized:
        return False
    return any(_clean(invalid).upper() in normalized for invalid in INVALID_APPROVAL_RESULTS)


def is_hidden_approval_status(status: str | None, result: str | None = None) -> bool:
    """钉钉已撤销/终止/拒绝审批不进入成本表格。"""

    normalized = _clean(status).upper()
    hidden_status = bool(normalized) and any(
        _clean(hidden).upper() in normalized for hidden in HIDDEN_APPROVAL_STATUSES
    )
    return hidden_status or _is_invalid_approval_result(result)


def resolve_approval_decision(status: str | None, result: str | None = None) -> dict:
    """合并钉钉流程状态和审批结果，形成唯一业务判定。"""

    process_status = _clean(status)
    approval_result = _clean(result)
    rejected = _is_invalid_approval_result(approval_result)
    status_excluded = is_hidden_approval_status(process_status)
    return {
        "process_status": process_status,
        "approval_result": approval_result,
        "effective_status": "REJECTED" if rejected else process_status or approval_result,
        "excluded": bool(rejected or status_excluded),
    }


def is_invalid_approval_status(status: str | None) -> bool:
    """判断采购审批是否已经拒绝、撤销或终止。"""

    normalized = _clean(status).upper()
    if not normalized:
        return False
    return any(_clean(invalid).upper() in normalized for invalid in HIDDEN_APPROVAL_STATUSES)


def is_completed_approval_status(
    status: str | None,
    *,
    result: str | None = None,
    allow_empty: bool = True,
) -> bool:
    """判断审批是否已完成；空状态默认放行以兼容部分旧接口响应。"""

    normalized = _clean(status).upper()
    if is_hidden_approval_status(normalized, result):
        return False
    if not normalized:
        return allow_empty
    return any(_clean(completed).upper() in normalized for completed in COMPLETED_APPROVAL_STATUSES)


def summarize_approval(instance: dict, *, process_instance_id: str = "", include_raw: bool = False) -> dict:
    """整理成系统可回溯的最小审批摘要。"""

    fields = extract_form_fields(instance)
    linked_purchase_approvals = extract_linked_purchase_approvals(instance)
    oa_attachments = extract_approval_attachments(instance)
    oa_form_attachments = [item for item in oa_attachments if item.get("origin") == "Form"]
    oa_comment_attachments = [item for item in oa_attachments if item.get("origin") == "Comment"]
    instance_id = (
        _clean(process_instance_id)
        or _clean(instance.get("process_instance_id"))
        or _clean(instance.get("processInstanceId"))
        or extract_dingtalk_instance_id(instance.get("url"))
    )
    approval_no = _clean(
        instance.get("business_id")
        or instance.get("businessId")
        or instance.get("bizId")
        or instance.get("approval_no")
    )
    official_url = _clean(instance.get("url") or instance.get("detailUrl") or instance.get("officialUrl"))
    dingtalk_payload = build_dingtalk_order_payload(
        approval_no=approval_no,
        instance_id=instance_id,
        official_url=official_url,
    )
    transport_mode_raw = _find_field_value(fields, TRANSPORT_FIELD_ALIASES)
    decision = resolve_approval_decision(
        instance.get("status") or instance.get("approvalStatus"),
        instance.get("result") or instance.get("approvalResult"),
    )
    summary = {
        "source_instance_id": instance_id,
        "source_approval_no": approval_no,
        "source_dingtalk_url": official_url,
        "open_url": dingtalk_payload.get("open_url") or "",
        "approval_title": _clean(instance.get("title") or instance.get("process_instance_title") or instance.get("processInstanceTitle")),
        "approval_status": decision["effective_status"],
        **decision,
        "originator_userid": _clean(instance.get("originator_userid") or instance.get("originatorUserId")),
        "originator_dept_id": _clean(instance.get("originator_dept_id") or instance.get("originatorDeptId")),
        "create_time": instance.get("create_time") or instance.get("createTime") or "",
        "finish_time": instance.get("finish_time") or instance.get("finishTime") or "",
        "transport_mode": detect_approval_transport_mode(transport_mode_raw),
        "transport_mode_raw": transport_mode_raw,
        "logistics_no": _find_field_value(fields, BATCH_NO_FIELD_ALIASES),
        "linked_purchase_count": len(linked_purchase_approvals),
        "linked_purchase_approvals": linked_purchase_approvals,
        "oa_attachment_count": len(oa_attachments),
        "oa_attachments": oa_attachments,
        "oa_form_attachment_count": len(oa_form_attachments),
        "oa_form_attachments": oa_form_attachments,
        "oa_comment_attachment_count": len(oa_comment_attachments),
        "oa_comment_attachments": oa_comment_attachments,
        "raw_form_components": _get_form_components(instance),
        "form_fields": fields,
    }
    summary["logistics_fee"] = extract_logistics_fee_from_approval(summary)
    summary["logistics_quote_candidates"] = extract_logistics_quote_candidates_from_approval(summary)
    summary["logistics_text_summary"] = extract_logistics_text_summary_from_approval(summary)
    if include_raw:
        summary["raw_instance"] = instance
    return summary


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _compact_extra_json_for_audit(value: Any) -> dict:
    data = _json_loads_dict(value)
    if not data:
        return {"has_value": bool(_clean(value))}

    trace = data.get("oa_logistics_trace") if isinstance(data.get("oa_logistics_trace"), dict) else data
    linked_approvals = trace.get("linked_purchase_approvals") if isinstance(trace, dict) else []
    linked_approvals = linked_approvals or []
    attachments = trace.get("oa_attachments") if isinstance(trace, dict) else []
    attachments = attachments or []
    form_attachments = trace.get("oa_form_attachments") if isinstance(trace, dict) else []
    form_attachments = form_attachments or []
    comment_attachments = trace.get("oa_comment_attachments") if isinstance(trace, dict) else []
    comment_attachments = comment_attachments or []
    return {
        "source": data.get("source"),
        "has_form_fields": bool(trace.get("form_fields")) if isinstance(trace, dict) else False,
        "oa_form_attachment_count": len(form_attachments) if isinstance(form_attachments, list) else 0,
        "oa_comment_attachment_count": len(comment_attachments) if isinstance(comment_attachments, list) else 0,
        "oa_attachment_count": len(attachments) if isinstance(attachments, list) else 0,
        "linked_purchase_count": len(linked_approvals) if isinstance(linked_approvals, list) else 0,
        "linked_purchase_approval_nos": [
            linked.get("approval_no") or linked.get("source_approval_no")
            for linked in linked_approvals
            if isinstance(linked, dict) and (linked.get("approval_no") or linked.get("source_approval_no"))
        ],
    }


def _compact_for_audit(value: Any) -> Any:
    if isinstance(value, dict):
        compacted = {}
        for key, item in value.items():
            if key == "extra_json":
                compacted[key] = _compact_extra_json_for_audit(item)
            else:
                compacted[key] = _compact_for_audit(item)
        return compacted
    if isinstance(value, list):
        return [_compact_for_audit(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 1000:
        return f"{value[:1000]}...<已截断，原长度 {len(value)}>"
    return value


def _json_dumps_for_audit(value: Any) -> str:
    text = json.dumps(_compact_for_audit(value), ensure_ascii=True, default=str)
    if len(text) > MAX_AUDIT_TEXT_LENGTH:
        return f"{text[:MAX_AUDIT_TEXT_LENGTH]}...<已截断，原长度 {len(text)}>"
    return text


def _json_loads_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    text = _clean(value)
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _merge_oa_extra_json(old_value: Any, new_value: Any) -> str:
    old_data = _json_loads_dict(old_value)
    new_data = _json_loads_dict(new_value)
    if not old_data:
        return _json_dumps(new_data) if new_data else _clean(new_value)
    if not new_data:
        return _json_dumps(old_data)

    if old_data.get("source") == "dingtalk_oa_logistics":
        merged = {**old_data, **new_data}
    else:
        merged = dict(old_data)
        merged["oa_logistics_trace"] = {
            "source": new_data.get("source"),
            "transport_mode_raw": new_data.get("transport_mode_raw"),
            "open_url": new_data.get("open_url"),
            "logistics_fee": new_data.get("logistics_fee") or {},
            "logistics_fees": new_data.get("logistics_fees") or [],
            "logistics_quote_candidates": new_data.get("logistics_quote_candidates") or [],
            "linked_purchase_approvals": new_data.get("linked_purchase_approvals") or [],
            "oa_attachments": new_data.get("oa_attachments") or [],
            "oa_form_attachments": new_data.get("oa_form_attachments") or [],
            "oa_comment_attachments": new_data.get("oa_comment_attachments") or [],
            "subsidiary": new_data.get("subsidiary") or {},
            "project_ownership": new_data.get("project_ownership") or {},
            "form_fields": new_data.get("form_fields") or {},
        }
    return _json_dumps(merged)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            if not isinstance(value, str) or value.strip():
                return value
    return ""


def _to_frappe_datetime(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text.isdigit():
        number = int(text)
        timestamp = number / 1000 if number > 10_000_000_000 else number
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _values_match(old_value: Any, new_value: Any) -> bool:
    if old_value in (None, "") and new_value in (None, ""):
        return True
    if isinstance(old_value, datetime):
        old_text = old_value.strftime("%Y-%m-%d %H:%M:%S")
    else:
        old_text = _clean(old_value)
    if isinstance(new_value, datetime):
        new_text = new_value.strftime("%Y-%m-%d %H:%M:%S")
    else:
        new_text = _clean(new_value)
    return old_text == new_text


def _looks_like_container_no(value: Any) -> bool:
    text = _clean(value).replace(" ", "").upper()
    return bool(re.match(r"^[A-Z]{3,4}\d{6,8}$", text))


def _count_dingtalk_attachments(value: Any) -> int:
    parsed = _parse_json_text(value)
    if isinstance(parsed, dict):
        count = 1 if parsed.get("fileId") and parsed.get("fileName") else 0
        return count + sum(_count_dingtalk_attachments(item) for item in parsed.values())
    if isinstance(parsed, list):
        return sum(_count_dingtalk_attachments(item) for item in parsed)
    return 0


def _is_goods_table_field(fieldname: Any) -> bool:
    normalized = _normalize_key(fieldname)
    return any(_normalize_key(alias) in normalized for alias in GOODS_TABLE_FIELD_ALIASES)


def _flatten_dingtalk_table_row(row: Any) -> dict:
    if not isinstance(row, dict):
        return {}
    row_value = _parse_json_text(row.get("rowValue") or row.get("row_value") or row.get("value") or row)
    if isinstance(row_value, dict):
        row_value = row_value.get("rowValue") or row_value.get("row_value") or row_value
    flattened: dict[str, Any] = {}
    columns: list[str] = []
    if isinstance(row_value, list):
        for cell in row_value:
            if not isinstance(cell, dict):
                continue
            label = _clean(cell.get("label") or cell.get("name") or cell.get("key"))
            value = cell.get("value")
            if label:
                columns.append(label)
                flattened[label] = value
    elif isinstance(row_value, dict):
        for key, value in row_value.items():
            if isinstance(value, dict):
                label = _clean(value.get("label") or value.get("name") or key)
                if label:
                    columns.append(label)
                flattened[label] = value.get("value")
            else:
                label = _clean(key)
                if label:
                    columns.append(label)
                    flattened[label] = value
    if row.get("rowNumber") or row.get("row_number"):
        flattened["_dingtalk_row_number"] = row.get("rowNumber") or row.get("row_number")
    result = {key: value for key, value in flattened.items() if key and value not in (None, "")}
    if columns:
        result["_oa_goods_columns"] = list(dict.fromkeys(columns))
    return result


def _is_oa_goods_placeholder_row(row: dict) -> bool:
    material_code = _clean(
        row.get("物料编码 Código de material")
        or row.get("物料编码")
        or row.get("material_code")
    )
    product_name = _clean(
        row.get("物料名称（中文）Nombre del material (chino)")
        or row.get("物料名称（中文）")
        or row.get("物料名称")
        or row.get("产品名称")
    )
    quantity = row.get("数量Cantidad") or row.get("数量") or row.get("quantity")
    placeholder_markers = {
        "物料编码",
        "品目编码",
        "物料名称",
        "物料名称中文",
        "产品名称",
        "规格型号",
        "数量",
        "单位",
        "收货人",
        "收件人",
    }
    if material_code in placeholder_markers:
        return True
    if product_name in placeholder_markers and _to_number_or_none(quantity) in (None, 0):
        return True
    values = [_clean(value) for value in row.values()]
    marker_count = sum(1 for value in values if value in placeholder_markers)
    return marker_count >= 3


def _parse_oa_goods_text_rows(value: Any) -> list[dict]:
    """解析普通文本填写的货物信息，兼容“编码-名称-数量pcs”格式。"""

    text = _clean(value)
    if not text:
        return []

    rows: list[dict] = []
    quantity_pattern = re.compile(
        r"^(?P<body>.+?)\s*(?:[-－—]+\s*)?(?P<quantity>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>pcs?|pieces?|pzas?|piezas?|件|个|箱|袋|套|卷|支|台|片|kg|kgs|公斤|千克|吨|m3|cbm|方)\s*$",
        re.IGNORECASE,
    )
    material_code_pattern = re.compile(
        r"^(?P<material_code>[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9]*)\s*[-－—]\s*(?P<product_name>.+)$"
    )

    for raw_line in re.split(r"[\r\n;；]+", text):
        line = re.sub(r"^\s*(?:\d+[.、)）]\s*)?[-*•]+\s*", "", _clean(raw_line))
        if not line:
            continue
        if re.match(r"^(?:合计|总计|共计|总数|总重量|重量|体积|总体积)", line, re.IGNORECASE):
            continue

        quantity_match = quantity_pattern.match(line)
        if not quantity_match:
            continue

        body = _clean(quantity_match.group("body")).rstrip("-－— ")
        if not body:
            continue
        code_match = material_code_pattern.match(body)
        row = {
            "product_name": _clean(code_match.group("product_name")) if code_match else body,
            "quantity": quantity_match.group("quantity"),
            "unit": normalize_unit(quantity_match.group("unit")),
            "_oa_goods_source": "text",
        }
        if code_match:
            row["material_code"] = _clean(code_match.group("material_code"))
        rows.append(row)
    return rows


def extract_oa_goods_rows(item: dict) -> list[dict]:
    """从钉钉国际物流审批摘要中提取货物信息表格或文本行。"""

    form_fields = item.get("form_fields") or {}
    goods_value = None
    for fieldname, value in form_fields.items():
        if _is_goods_table_field(fieldname):
            goods_value = value
            break
    goods_table = _parse_json_text(goods_value)
    if isinstance(goods_table, list):
        raw_rows = [_flatten_dingtalk_table_row(raw_row) for raw_row in goods_table]
    elif isinstance(goods_value, str):
        raw_rows = _parse_oa_goods_text_rows(goods_value)
    else:
        return []

    project_ownership = extract_project_candidates_from_approval(item)
    common_values = {
        "project_collection": project_ownership["candidates"][0]["name"] if len(project_ownership["candidates"]) == 1 else "",
        "项目proyecto": form_fields.get("项目proyecto"),
        "物料类别TIPO": _find_field_value(
            form_fields, ("物料类别Tipo de Material", "物料类别TIPO")
        ),
        "物流方式Camino Envío": item.get("transport_mode_raw"),
        "柜号/单号Número DE Logística": item.get("logistics_no"),
        "备注otro": form_fields.get("备注otro"),
    }
    rows: list[dict] = []
    for row in raw_rows:
        if not row:
            continue
        if _is_oa_goods_placeholder_row(row):
            continue
        for key, value in common_values.items():
            if value not in (None, "") and key not in row:
                row[key] = value
        rows.append(row)
    return rows


def _to_number_or_none(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    text = _clean(value).replace(",", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return value


def _is_purchase_detail_table_field(fieldname: Any) -> bool:
    normalized = _normalize_key(fieldname)
    return any(_normalize_key(alias) in normalized for alias in PURCHASE_DETAIL_TABLE_FIELD_ALIASES)


def _looks_like_purchase_detail_row(row: dict) -> bool:
    normalized_keys = [_normalize_key(key) for key in row]
    return any("物品编码" in key or "codigo" in key for key in normalized_keys) and any(
        "单价" in key or "precio" in key or "总金额" in key or "montototal" in key for key in normalized_keys
    )


def _purchase_text_value(text: str, aliases: tuple[str, ...]) -> str:
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    stop_pattern = (
        r"物品编码|物料编码|品目编码|C[oó]digo|Codigo|Código|SKU|"
        r"物品名称|物料名称|品名|Nombre(?:\s+del\s+art[ií]culo)?|"
        r"物品规格|规格|Especificaci[oó]n|Especificacion|"
        r"数量|Cantidad|Qty|QTY|单位|Unidad|"
        r"单价|Precio|Unit\s*Price|总金额|Monto\s*Total|金额|Total|币种|Moneda"
    )
    match = re.search(
        rf"(?:{alias_pattern})\s*[:：]?\s*(.+?)(?=\s+(?:{stop_pattern})\s*[:：]?|$)",
        text,
        flags=re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else ""


def _purchase_text_number(text: str, aliases: tuple[str, ...]):
    value = _purchase_text_value(text, aliases)
    if not value:
        return None
    match = re.search(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value)
    return match.group(0) if match else None


def _purchase_text_currency(text: str, default_currency: Any = "") -> str:
    upper = text.upper()
    if any(marker in upper for marker in ("人民币", "RMB", "CNY", "¥")):
        return "人民币RMB"
    if any(marker in upper for marker in ("美元", "USD", "US$")):
        return "美元USD"
    if any(marker in upper for marker in ("MXN", "PESO", "比索")):
        return "墨西哥比索MXN"
    return _clean(default_currency)


def _clean_purchase_product_name(value: str) -> str:
    text = _clean(value)
    text = re.sub(r"^(?:[-－—*•]|\d+[.、)）])\s*", "", text)
    return text.strip(" -－—")


def _parse_purchase_text_chunk(chunk: str, *, currency: Any = "", source_field: str = "") -> dict:
    text = _clean(chunk)
    if not text:
        return {}

    material_code = _purchase_text_value(text, ("物品编码", "物料编码", "品目编码", "Código", "Codigo", "SKU"))
    if not material_code:
        code_match = re.search(r"\b([A-Z]{1,5}\d{3,}[A-Z0-9-]*)\b", text, flags=re.IGNORECASE)
        material_code = _clean(code_match.group(1)).upper() if code_match else ""

    product_name = _purchase_text_value(text, ("物品名称", "物料名称", "品名", "Nombre del artículo", "Nombre del articulo", "Nombre"))
    spec_model = _purchase_text_value(text, ("物品规格", "规格型号", "规格", "Especificación", "Especificacion"))
    quantity = _purchase_text_number(text, ("数量", "Cantidad", "Qty", "QTY"))
    unit = normalize_unit(_purchase_text_value(text, ("单位", "Unidad")))
    unit_price = _purchase_text_number(text, ("单价", "Precio", "Unit Price"))
    goods_value = _purchase_text_number(text, ("总金额", "Monto Total", "金额", "Total"))

    if material_code and not (quantity and unit_price and goods_value):
        after_code = re.split(re.escape(material_code), text, maxsplit=1, flags=re.IGNORECASE)
        tail = after_code[1] if len(after_code) > 1 else text
        numbers = re.findall(r"(?<![A-Z0-9])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", tail, flags=re.IGNORECASE)
        if len(numbers) >= 3:
            quantity = quantity or numbers[-3]
            unit_price = unit_price or numbers[-2]
            goods_value = goods_value or numbers[-1]

    if not product_name and material_code:
        after_code = re.split(re.escape(material_code), text, maxsplit=1, flags=re.IGNORECASE)
        tail = after_code[1] if len(after_code) > 1 else ""
        product_name = re.split(r"\s+(?:数量|Cantidad|Qty|QTY|单价|Precio|总金额|Monto\s*Total|金额|Total)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0]
        if numbers := re.findall(r"(?<![A-Z0-9])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", product_name, flags=re.IGNORECASE):
            product_name = product_name.split(numbers[0], 1)[0]
        product_name = _clean_purchase_product_name(product_name)

    if not (quantity and (unit_price or goods_value) and (material_code or product_name)):
        return {}

    row = {
        "物品编码Código": material_code,
        "物品名称Nombre del artículo": product_name,
        "物品规格Especificacion": spec_model,
        "数量Cantidad": quantity,
        "单位Unidad": unit,
        "单价Precio": unit_price,
        "总金额Monto Total": goods_value,
        "币种Moneda": _purchase_text_currency(text, currency),
        "_dingtalk_table_name": source_field or "文本采购明细",
        "_purchase_text_source": "text",
    }
    return {key: value for key, value in row.items() if value not in (None, "")}


def _parse_purchase_expense_text_rows(value: Any, *, currency: Any = "", source_field: str = "") -> list[dict]:
    text = _clean(value)
    if not text:
        return []

    normalized_text = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    chunks: list[str] = []
    code_matches = list(re.finditer(r"\b[A-Z]{1,5}\d{3,}[A-Z0-9-]*\b", normalized_text, flags=re.IGNORECASE))
    if len(code_matches) > 1:
        for index, match in enumerate(code_matches):
            start = match.start()
            end = code_matches[index + 1].start() if index + 1 < len(code_matches) else len(normalized_text)
            chunks.append(normalized_text[start:end])
    else:
        chunks = [line for line in re.split(r"[\n;；]+", normalized_text) if _clean(line)]
        if len(chunks) <= 1:
            chunks = [normalized_text]

    rows: list[dict] = []
    for chunk in chunks:
        if re.match(r"^\s*(?:合计|总计|小计|total)\b", chunk, flags=re.IGNORECASE):
            continue
        row = _parse_purchase_text_chunk(chunk, currency=currency, source_field=source_field)
        if row:
            rows.append(row)
    return rows


def _is_purchase_text_candidate_field(fieldname: Any, value: Any) -> bool:
    text = _clean(value)
    if not text:
        return False
    normalized_name = _normalize_key(fieldname)
    name_hit = any(
        marker in normalized_name
        for marker in ("明细", "详情", "物品", "商品", "采购", "备注", "otro", "descripcion", "description", "desglose")
    )
    normalized_text = _normalize_key(text)
    price_hit = any(marker in normalized_text for marker in ("单价", "precio", "金额", "montototal", "total"))
    goods_hit = any(marker in normalized_text for marker in ("物品", "物料", "编码", "codigo", "sku", "名称", "nombre"))
    return name_hit and price_hit and goods_hit


def extract_purchase_expense_rows(instance: dict) -> list[dict]:
    """从采购支出 OA 详情里提取明细行，并补上表单级币种。"""

    currency = _find_component_value(instance, PURCHASE_CURRENCY_FIELD_ALIASES)
    rows: list[dict] = []
    fallback_rows: list[dict] = []
    text_rows: list[dict] = []
    for component in _iter_form_components(instance):
        name = _clean(component.get("name") or component.get("label") or component.get("id"))
        component_type = _clean(component.get("componentType") or component.get("component_type"))
        if component_type != "TableField":
            value = _parse_json_text(component.get("value"))
            if isinstance(value, str) and _is_purchase_text_candidate_field(name, value):
                for row in _parse_purchase_expense_text_rows(value, currency=currency, source_field=name):
                    if currency and "币种Moneda" not in row:
                        row["币种Moneda"] = currency
                    text_rows.append(row)
            continue

        parsed_table = _parse_json_text(component.get("value"))
        if not isinstance(parsed_table, list):
            continue

        table_rows: list[dict] = []
        for raw_row in parsed_table:
            row = _flatten_dingtalk_table_row(raw_row)
            if not row:
                continue
            if currency and "币种Moneda" not in row:
                row["币种Moneda"] = currency
            row["_dingtalk_table_name"] = name
            table_rows.append(row)

        if _is_purchase_detail_table_field(name):
            rows.extend(table_rows)
        else:
            fallback_rows.extend([row for row in table_rows if _looks_like_purchase_detail_row(row)])

    return rows or fallback_rows or text_rows



def build_purchase_expense_item_values_from_approval(instance: dict) -> list[dict]:
    """把采购支出 OA 明细行映射成可回填采购字段的预览结构。"""

    from overseas_costing.services.logistics_settlement.model import is_logistics_expense, components
    if is_logistics_expense({c.get('name', ''): c.get('value') for c in components(instance)}):
        return []
    values: list[dict] = []
    for row in extract_purchase_expense_rows(instance):
        mapped = map_purchase_expense_row_to_item(row)
        for fieldname in ("quantity", "unit_price", "goods_value"):
            mapped[fieldname] = _to_number_or_none(mapped.get(fieldname))
        mapped["raw_oa_row"] = row
        values.append(mapped)
    return values


def summarize_purchase_approval(instance: dict, *, process_instance_id: str = "") -> dict:
    """整理采购支出审批详情，供国际物流关联采购单后续补价使用。"""

    instance_id = (
        _clean(process_instance_id)
        or _clean(instance.get("process_instance_id"))
        or _clean(instance.get("processInstanceId"))
        or extract_dingtalk_instance_id(instance.get("url"))
    )
    approval_no = _clean(
        instance.get("business_id")
        or instance.get("businessId")
        or instance.get("bizId")
        or instance.get("approval_no")
    )
    official_url = _clean(instance.get("url") or instance.get("detailUrl") or instance.get("officialUrl"))
    dingtalk_payload = build_dingtalk_order_payload(
        approval_no=approval_no,
        instance_id=instance_id,
        official_url=official_url,
    )
    detail_rows = extract_purchase_expense_rows(instance)
    mapped_items = build_purchase_expense_item_values_from_approval(instance)
    decision = resolve_approval_decision(
        instance.get("status") or instance.get("approvalStatus"),
        instance.get("result") or instance.get("approvalResult"),
    )
    return {
        "source_instance_id": instance_id,
        "source_approval_no": approval_no,
        "source_dingtalk_url": official_url,
        "open_url": dingtalk_payload.get("open_url") or "",
        "approval_title": _clean(instance.get("title") or instance.get("process_instance_title") or instance.get("processInstanceTitle")),
        "approval_status": decision["effective_status"],
        **decision,
        "originator_userid": _clean(instance.get("originator_userid") or instance.get("originatorUserId")),
        "originator_dept_id": _clean(instance.get("originator_dept_id") or instance.get("originatorDeptId")),
        "create_time": instance.get("create_time") or instance.get("createTime") or "",
        "finish_time": instance.get("finish_time") or instance.get("finishTime") or "",
        "purchase_currency": _find_component_value(instance, PURCHASE_CURRENCY_FIELD_ALIASES),
        "detail_row_count": len(detail_rows),
        "detail_rows": detail_rows,
        "mapped_preview_items": mapped_items,
    }


def pull_linked_purchase_approval_details(
    *,
    token: str,
    linked_approvals: list[dict],
    api_style: str = "auto",
    include_running: bool = False,
) -> list[dict]:
    """按关联审批实例 ID 拉取采购支出详情并返回解析摘要。"""

    source_mode = resolve_approval_source_mode()
    database_details: dict[str, dict] = {}
    if source_mode == "postgres":
        instance_ids = list(
            dict.fromkeys(
                _clean(linked.get("source_instance_id") or linked.get("proc_inst_id") or linked.get("instance_id"))
                for linked in linked_approvals
                if isinstance(linked, dict)
                and _clean(linked.get("source_instance_id") or linked.get("proc_inst_id") or linked.get("instance_id"))
            )
        )
        database_details = _get_postgres_approval_source().get_instances(instance_ids)

    summaries: list[dict] = []
    for linked in linked_approvals:
        if not isinstance(linked, dict):
            continue
        instance_id = _clean(linked.get("source_instance_id") or linked.get("proc_inst_id") or linked.get("instance_id"))
        if not instance_id:
            summaries.append(
                {
                    "ok": False,
                    "source_approval_no": linked.get("approval_no") or linked.get("source_approval_no") or "",
                    "source_instance_id": "",
                    "message": "关联采购审批缺少实例 ID，无法拉取详情。",
                }
            )
            continue
        if source_mode == "postgres":
            detail = database_details.get(instance_id)
            if detail is None:
                summaries.append(
                    {
                        "ok": False,
                        "source_approval_no": linked.get("approval_no") or linked.get("source_approval_no") or "",
                        "source_instance_id": instance_id,
                        "data_source": "postgres",
                        "fallback_used": False,
                        "message": "关联采购审批不在成本系统数据库白名单视图中。",
                    }
                )
                continue
        else:
            detail = get_process_instance_detail(token=token, process_instance_id=instance_id, api_style=api_style)
        summary = summarize_purchase_approval(detail, process_instance_id=instance_id)
        if is_hidden_approval_status(summary.get("approval_status")):
            summary["ok"] = False
            summary["detail_row_count"] = 0
            summary["detail_rows"] = []
            summary["mapped_preview_items"] = []
            summary["message"] = "采购支出审批已撤销或终止，未用于采购字段同步。"
        elif not include_running and not is_completed_approval_status(summary.get("approval_status")):
            summary["ok"] = False
            summary["detail_row_count"] = 0
            summary["detail_rows"] = []
            summary["mapped_preview_items"] = []
            summary["message"] = "采购支出审批未完成，未用于采购字段同步。"
        else:
            summary["ok"] = True
        summary["data_source"] = source_mode
        summary["fallback_used"] = False
        summary["linked_from"] = linked
        summaries.append(summary)
    return summaries


def build_oa_item_values_from_approval(item: dict) -> list[dict]:
    """把审批里的货物信息表格转成可写入 Overseas Cost Item 的基础行。"""

    from overseas_costing.services.material_input_service import ensure_stable_line_key

    source_approval_no = _clean(item.get("source_approval_no"))
    source_instance_id = _clean(item.get("source_instance_id"))
    source_dingtalk_url = _clean(item.get("source_dingtalk_url"))
    rows = extract_oa_goods_rows(item)
    project_ownership = extract_project_candidates_from_approval(item)
    form_fields = item.get("form_fields") or {}
    total_gross_weight = _to_number_or_none(_find_field_value(form_fields, LOGISTICS_WEIGHT_FIELD_ALIASES))
    active_suppliers = load_active_suppliers()
    values: list[dict] = []
    for index, row in enumerate(rows, start=1):
        mapped = map_oa_row_to_item(row)
        supplier_field_present = any(
            _field_matches_alias(column, ("供应商Proveedor", "供应商", "Proveedor"))
            for column in (row.get("_oa_goods_columns") or [])
        )
        supplier_resolution = resolve_supplier_reference(mapped.get("supplier"), suppliers=active_suppliers)
        mapped["supplier"] = (
            supplier_resolution.get("canonical_supplier")
            if supplier_resolution.get("status") == "EXACT"
            else ""
        )
        mapped.update(
            {
                "row_no": index,
                # 物料行身份键：装箱单导入（import_service）与手动新增都会生成，
                # OA 骨架行此前漏了，导致工作台「全选本页」/行复选框永远禁用。
                "stable_line_key": ensure_stable_line_key(mapped),
                "quantity": _to_number_or_none(mapped.get("quantity")),
                "gross_weight_kg": _to_number_or_none(mapped.get("gross_weight_kg")),
                "actual_shipped_qty": _to_number_or_none(mapped.get("quantity")),
                "actual_shipped_qty_mode": "EXPLICIT_SOURCE",
                "shipped_uom": mapped.get("unit") or "",
                "source_type": "OA_LOGISTICS_ROW",
                "source_doc_no": source_approval_no or source_instance_id,
                "parse_status": "SUCCESS",
                "dingtalk_instance_id": source_instance_id,
                "dingtalk_official_url": source_dingtalk_url,
                "raw_excel_json": _json_dumps(row),
                "extra_json": _json_dumps(
                    {
                        "source": "dingtalk_oa_logistics_form",
                        "approval_no": source_approval_no,
                        "instance_id": source_instance_id,
                        "dingtalk_goods_row_no": row.get("_dingtalk_row_number") or index,
                        "goods_table_columns": row.get("_oa_goods_columns") or [],
                        "supplier_raw_value": supplier_resolution.get("raw_value") or "",
                        "supplier_field_present": supplier_field_present,
                        "supplier_match_status": supplier_resolution.get("status") or "UNRESOLVED",
                        "supplier_canonical_name": supplier_resolution.get("canonical_supplier") or "",
                        "supplier_candidates": supplier_resolution.get("candidates") or [],
                        "project_candidates": project_ownership.get("candidates") or [],
                        "project_source_field": project_ownership.get("source_field") or "",
                    }
                ),
            }
        )
        values.append(mapped)
    has_line_weight_column = any(
        _field_matches_alias(column, ("重量 Peso", "重量Peso（KG）", "重量Peso(KG)", "Peso KG"))
        for row in rows
        for column in (row.get("_oa_goods_columns") or [])
    )
    if total_gross_weight and values and not has_line_weight_column and not any(_to_number_or_none(row.get("gross_weight_kg")) for row in values):
        total_quantity = sum(_to_number_or_none(row.get("quantity")) or 0 for row in values)
        if total_quantity > 0:
            for mapped in values:
                quantity = _to_number_or_none(mapped.get("quantity")) or 0
                mapped["gross_weight_kg"] = round(total_gross_weight * quantity / total_quantity, 6) if quantity else 0
    return values


def build_batch_values_from_approval(item: dict) -> dict:
    """把一条国际物流审批摘要整理成批次头追溯字段。"""

    form_fields = item.get("form_fields") or {}
    logistics_no = _clean(item.get("logistics_no"))
    source_approval_no = _clean(item.get("source_approval_no"))
    source_instance_id = _clean(item.get("source_instance_id"))
    batch_no = _clean(_first_non_empty(logistics_no, source_approval_no, source_instance_id))
    source_dingtalk_url = _clean(item.get("source_dingtalk_url"))
    oa_form_attachments = item.get("oa_form_attachments") or extract_attachments_from_form_fields(form_fields)
    oa_attachments = item.get("oa_attachments") or oa_form_attachments
    attachment_count = len(oa_attachments) if oa_attachments else _count_dingtalk_attachments(form_fields)
    transport_mode = _normalize_transport_mode(item.get("transport_mode")) or detect_approval_transport_mode(item.get("transport_mode_raw")) or ""
    transport_mode_raw = item.get("transport_mode_raw") or ""
    business_type = normalize_business_type(
        item.get("business_type") or transport_mode_raw,
        transport_mode=transport_mode,
    ) or ""
    subsidiary = extract_subsidiary_from_approval(item)
    project_ownership = extract_project_candidates_from_approval(item)
    values = {
        "batch_no": batch_no,
        "waybill_no": logistics_no,
        "container_no": logistics_no if _looks_like_container_no(logistics_no) else "",
        "transport_mode": transport_mode,
        "business_type": business_type,
        "subsidiary_code": subsidiary.get("subsidiary_code") or "",
        "source_type": "oa_logistics",
        "source_data_id": source_instance_id or source_approval_no,
        "source_approval_no": source_approval_no,
        "source_instance_id": source_instance_id or extract_dingtalk_instance_id(source_dingtalk_url),
        "source_dingtalk_url": source_dingtalk_url,
        "source_approval_status": _clean(item.get("approval_status")),
        "source_title": _clean(item.get("approval_title")),
        "source_creator_name": _clean(item.get("originator_userid")),
        "source_creator_dept": _clean(item.get("originator_dept_id")),
        "source_created_at": _to_frappe_datetime(item.get("create_time")),
        "source_finished_at": _to_frappe_datetime(item.get("finish_time")),
        "source_attachment_count": attachment_count,
        "status": "Imported",
        "confirm_status": "Pending",
        "writeback_status": "Not Started",
        "version_count": 1,
        "item_count": 0,
        "import_remark": "从钉钉国际物流审批拉取，仅保存追溯字段，未写入物料和金额。",
        "source_remark": _clean(item.get("transport_mode_raw")),
        "extra_json": _json_dumps(
            {
                "source": "dingtalk_oa_logistics",
                "process_status": item.get("process_status") or item.get("approval_status") or "",
                "approval_result": item.get("approval_result") or "",
                "effective_status": item.get("effective_status") or item.get("approval_status") or "",
                "excluded": bool(item.get("excluded") or is_hidden_approval_status(
                    item.get("approval_status"), item.get("approval_result")
                )),
                "transport_mode": transport_mode,
                "business_type": business_type,
                "transport_mode_raw": item.get("transport_mode_raw"),
                "open_url": item.get("open_url"),
                "logistics_fee": item.get("logistics_fee") or extract_logistics_fee_from_approval(item),
                "logistics_fees": item.get("logistics_fees") or extract_logistics_fees_from_approval(item),
                "logistics_quote_candidates": item.get("logistics_quote_candidates") or extract_logistics_quote_candidates_from_approval(item),
                "linked_purchase_approvals": item.get("linked_purchase_approvals") or [],
                "oa_attachments": oa_attachments,
                "oa_form_attachments": oa_form_attachments,
                "oa_comment_attachments": item.get("oa_comment_attachments") or [],
                "subsidiary": subsidiary,
                "project_ownership": project_ownership,
                "form_fields": form_fields,
            }
        ),
    }
    return values


def _filter_batch_values(values: dict) -> dict:
    if frappe is None:
        return dict(values)
    meta = frappe.get_meta("Overseas Cost Batch")
    return {fieldname: value for fieldname, value in values.items() if meta.has_field(fieldname)}


def _filter_item_values(values: dict) -> dict:
    if frappe is None:
        return dict(values)
    meta = frappe.get_meta("Overseas Cost Item")
    return {fieldname: value for fieldname, value in values.items() if meta.has_field(fieldname)}


def _resolve_existing_batch_name(values: dict) -> str:
    if frappe is None:
        return ""
    lookup_specs = [
        ("source_instance_id", values.get("source_instance_id")),
        ("source_approval_no", values.get("source_approval_no")),
        ("batch_no", values.get("batch_no")),
        ("waybill_no", values.get("waybill_no")),
    ]
    for fieldname, value in lookup_specs:
        if not _has_value(value):
            continue
        existing_name = frappe.db.get_value("Overseas Cost Batch", {fieldname: value}, "name")
        if existing_name:
            return existing_name
    return ""


def _insert_batch_audit_log(*, batch_name: str, field_name: str, old_value: Any, new_value: Any, remark: str) -> None:
    if frappe is None:
        return
    operator_name = ""
    session_user = getattr(getattr(frappe, "session", None), "user", None)
    if session_user and session_user != "Guest":
        operator_name = session_user
    frappe.get_doc(
        {
            "doctype": "Overseas Cost Audit Log",
            "batch": batch_name,
            "action_type": "IMPORT",
            "field_name": field_name,
            "old_value": "" if old_value is None else _json_dumps_for_audit(old_value),
            "new_value": "" if new_value is None else _json_dumps_for_audit(new_value),
            "operator_name": operator_name,
            "action_remark": remark,
        }
    ).insert(ignore_permissions=True)


def _ensure_oa_trace_version(batch_name: str, batch_no: str) -> str:
    if frappe is None:
        return ""
    current_version = frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
    if current_version:
        return current_version
    existing_name = frappe.db.get_value(
        "Overseas Cost Version",
        {"batch": batch_name, "version_code": "OA追溯"},
        "name",
    )
    if existing_name:
        frappe.db.set_value(
            "Overseas Cost Batch",
            batch_name,
            {"current_version": existing_name, "version_count": 1},
            update_modified=False,
        )
        return existing_name
    version_doc = frappe.get_doc(
        {
            "doctype": "Overseas Cost Version",
            "batch": batch_name,
            "version_code": "OA追溯",
            "version_type": "Estimated",
            "status": "Active",
            "is_current": 1,
            "source_type": "Import",
            "fx_usd_to_rmb": DEFAULT_FX_USD_TO_RMB,
            "fx_rmb_to_mxn": DEFAULT_FX_RMB_TO_MXN,
            "remark": f"钉钉国际物流审批追溯默认版本：{batch_no}",
        }
    ).insert(ignore_permissions=True)
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        {"current_version": version_doc.name, "version_count": 1},
        update_modified=False,
    )
    return version_doc.name


def _create_oa_trace_batch(values: dict) -> dict:
    filtered_values = _filter_batch_values(values)
    filtered_values["doctype"] = "Overseas Cost Batch"
    batch_doc = frappe.get_doc(filtered_values).insert(ignore_permissions=True)
    version_name = _ensure_oa_trace_version(batch_doc.name, values.get("batch_no") or batch_doc.name)
    _insert_batch_audit_log(
        batch_name=batch_doc.name,
        field_name="oa_logistics_trace",
        old_value=None,
        new_value={
            "batch_no": values.get("batch_no"),
            "source_approval_no": values.get("source_approval_no"),
            "source_instance_id": values.get("source_instance_id"),
        },
        remark="从钉钉国际物流审批创建批次追溯记录",
    )
    return {"action": "created", "batch_name": batch_doc.name, "version_name": version_name, "changed_fields": list(filtered_values.keys())}


def _update_oa_trace_batch(batch_name: str, values: dict) -> dict:
    current = frappe.db.get_value("Overseas Cost Batch", batch_name, list(_filter_batch_values(values).keys()), as_dict=True) or {}
    updates: dict[str, Any] = {}
    always_refresh_fields = {
        "transport_mode",
        "business_type",
        "source_approval_status",
        "source_attachment_count",
        "source_finished_at",
    }
    for fieldname, new_value in _filter_batch_values(values).items():
        if fieldname in {"batch_no", "status", "confirm_status", "writeback_status", "version_count", "item_count"}:
            continue
        old_value = current.get(fieldname)
        if fieldname == "extra_json":
            merged_extra_json = _merge_oa_extra_json(old_value, new_value)
            if _has_value(merged_extra_json) and not _values_match(old_value, merged_extra_json):
                updates[fieldname] = merged_extra_json
            continue
        if fieldname in always_refresh_fields:
            if _has_value(new_value) and not _values_match(old_value, new_value):
                updates[fieldname] = new_value
            continue
        if not _has_value(old_value) and _has_value(new_value):
            updates[fieldname] = new_value

    version_name = _ensure_oa_trace_version(batch_name, values.get("batch_no") or batch_name)
    if not updates:
        return {"action": "unchanged", "batch_name": batch_name, "version_name": version_name, "changed_fields": []}

    old_snapshot = {fieldname: current.get(fieldname) for fieldname in updates}
    changed_fields = list(updates.keys())
    new_snapshot = dict(updates)
    frappe.db.set_value("Overseas Cost Batch", batch_name, updates, update_modified=True)
    _insert_batch_audit_log(
        batch_name=batch_name,
        field_name="oa_logistics_trace",
        old_value=old_snapshot,
        new_value=new_snapshot,
        remark="从钉钉国际物流审批补充批次追溯字段",
    )
    return {"action": "updated", "batch_name": batch_name, "version_name": version_name, "changed_fields": changed_fields}


def _sync_oa_goods_items(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    only_when_empty: bool = True,
) -> dict:
    from overseas_costing.services.logistics_settlement.runtime import has_final_binding
    if frappe is not None and has_final_binding(batch_name):
        return {'action': 'skipped', 'ok': True, 'created_count': 0, 'updated_count': 0, 'skipped': True, 'reason': '已关联最终物流采购支出，保留其费用与物料来源'}

    if frappe is None:
        item_values = build_oa_item_values_from_approval(approval_item)
        return {
            "action": "preview",
            "created_count": 0,
            "skipped": False,
            "item_count": len(item_values),
            "items": item_values,
        }
    if not version_name:
        return {
            "action": "skipped",
            "created_count": 0,
            "skipped": True,
            "reason": "当前批次没有版本，无法写入 OA 基础物料行。",
        }

    existing_count = frappe.db.count("Overseas Cost Item", {"batch": batch_name, "version": version_name})
    if only_when_empty and existing_count:
        return {
            "action": "skipped",
            "created_count": 0,
            "skipped": True,
            "reason": "当前批次已有 SKU 明细，未用 OA 表单覆盖。",
            "existing_count": existing_count,
        }

    item_values = build_oa_item_values_from_approval(approval_item)
    if not item_values:
        return {
            "action": "skipped",
            "created_count": 0,
            "skipped": True,
            "reason": "钉钉审批单未解析到可用的货物信息。",
        }

    created_names: list[str] = []
    from overseas_costing.services.transport_service import prepare_item_transport

    batch_transport_mode = frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode") or ""
    for values in item_values:
        values = prepare_item_transport(values, batch_transport_mode)
        doc_values = _filter_item_values(
            {
                **values,
                "doctype": "Overseas Cost Item",
                "batch": batch_name,
                "version": version_name,
            }
        )
        doc_values["doctype"] = "Overseas Cost Item"
        created_names.append(frappe.get_doc(doc_values).insert(ignore_permissions=True).name)

    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        {
            "item_count": existing_count + len(created_names),
            "status": "Imported",
        },
        update_modified=True,
    )
    _insert_batch_audit_log(
        batch_name=batch_name,
        field_name="oa_logistics_items",
        old_value={"item_count": existing_count},
        new_value={"created_count": len(created_names), "item_count": existing_count + len(created_names)},
        remark="从钉钉国际物流审批表单生成基础物料行",
    )
    return {
        "action": "created",
        "created_count": len(created_names),
        "skipped": False,
        "item_count": existing_count + len(created_names),
        "item_names": created_names,
    }


def _purchase_expense_rows_from_preview(preview_result: dict) -> list[dict]:
    invalid_approval_nos = set()
    invalid_instance_ids = set()
    for summary in preview_result.get("purchase_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if not is_invalid_approval_status(summary.get("approval_status")) and not is_invalid_approval_status(summary.get("message")):
            continue
        approval_no = _clean(summary.get("source_approval_no") or summary.get("approval_no"))
        instance_id = _clean(summary.get("source_instance_id") or summary.get("instance_id"))
        if approval_no:
            invalid_approval_nos.add(approval_no)
        if instance_id:
            invalid_instance_ids.add(instance_id)

    rows = preview_result.get("mapped_preview_items") or []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and _clean(row.get("material_code"))
        and _clean(row.get("source_approval_no")) not in invalid_approval_nos
        and _clean(row.get("source_instance_id")) not in invalid_instance_ids
    ]


def _build_purchase_expense_item_doc_values(
    *,
    row: dict,
    row_no: int,
    batch_name: str,
    version_name: str,
    approval_item: dict,
) -> dict:
    from overseas_costing.services.material_input_service import ensure_stable_line_key

    quantity = _to_number_or_none(row.get("quantity")) or 0
    goods_value = _to_number_or_none(row.get("goods_value"))
    unit_price = _to_number_or_none(row.get("unit_price"))
    if goods_value is None and unit_price is not None and quantity:
        goods_value = round(unit_price * quantity, 6)
    values = {
        "doctype": "Overseas Cost Item",
        "batch": batch_name,
        "version": version_name,
        "row_no": row_no,
        # 与 build_oa_item_values_from_approval 同口径：新行必须带身份键。
        "stable_line_key": ensure_stable_line_key({}),
        "material_code": row.get("material_code") or "",
        "product_name": row.get("product_name") or "",
        "spec_model": row.get("spec_model") or "",
        "quantity": quantity,
        "actual_shipped_qty": quantity,
        "unit": normalize_unit(row.get("unit")) or row.get("unit") or "",
        "unit_price": unit_price,
        "purchase_currency": row.get("purchase_currency") or "",
        "goods_value": goods_value,
        "source_type": "PURCHASE_EXPENSE_OA",
        "source_doc_no": row.get("source_approval_no") or row.get("source_instance_id") or "",
        "parse_status": "SUCCESS",
        "dingtalk_instance_id": row.get("source_instance_id") or "",
        "dingtalk_official_url": row.get("source_dingtalk_url") or "",
        "raw_excel_json": _json_dumps(row),
        "extra_json": _json_dumps(
            {
                "source": "linked_purchase_expense_oa",
                "display_grain": "purchase_expense_line",
                "actual_shipped_qty_source": "purchase_quantity",
                "logistics_source_approval_no": approval_item.get("source_approval_no") or "",
                "logistics_source_instance_id": approval_item.get("source_instance_id") or "",
                "purchase_source_approval_no": row.get("source_approval_no") or "",
                "purchase_source_instance_id": row.get("source_instance_id") or "",
            }
        ),
    }
    from overseas_costing.services.transport_service import prepare_item_transport

    batch_transport_mode = (
        frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode")
        if frappe is not None else approval_item.get("transport_mode") or approval_item.get("transport_mode_raw") or ""
    )
    values["transport_mode"] = row.get("transport_mode") or ""
    return _filter_item_values(prepare_item_transport(values, batch_transport_mode))


def _has_logistics_item_structure(
    *, batch_name: str, version_name: str, approval_item: dict, existing_items: list[dict] | None = None,
) -> bool:
    """Shipment rows and saved purchase facts change only through the confirmation flow."""
    for raw_row in extract_oa_goods_rows(approval_item):
        row = map_oa_row_to_item(raw_row)
        if (row.get("material_code") or row.get("product_name")) and _parse_money_amount(row.get("quantity")):
            return True
    if existing_items is None:
        if not version_name or frappe is None or not hasattr(frappe, "get_all"):
            return False
        existing_items = frappe.get_all(
            "Overseas Cost Item", filters={"batch": batch_name, "version": version_name},
            fields=["source_type"], limit_page_length=10000,
        )
    return any(_clean(row.get("source_type")).upper() == "OA_LOGISTICS_ROW" for row in existing_items)


def _logistics_purchase_preview_result(preview_result: dict) -> dict:
    return {
        "action": "preview" if preview_result.get("ok") else "failed",
        "sync_strategy": "logistics_read_only", "ok": bool(preview_result.get("ok")),
        "confirmation_required": True, "updated_count": 0, "changed_field_count": 0,
        "created_count": 0, "deleted_count": 0, "skipped_count": 0,
        "unmatched_count": 0, "ambiguous_count": 0, "purchase_preview": preview_result,
        "message": "已读取采购资料，保留当前物流明细和采购事实；请在自动填充资料中预览并确认。",
    }


def _replace_items_with_purchase_expense_rows(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    purchase_rows: list[dict],
) -> dict:
    from overseas_costing.services.logistics_settlement.runtime import has_final_binding
    if frappe is not None and has_final_binding(batch_name):
        return {'action': 'skipped', 'ok': True, 'created_count': 0, 'updated_count': 0, 'skipped': True, 'reason': '已关联最终物流采购支出，保留其费用与物料来源'}

    if frappe is None:
        return {
            "action": "preview",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "purchase_item_count": len(purchase_rows),
        }
    if not version_name:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "reason": "当前批次没有版本，无法按采购支出明细重建 SKU。",
        }
    if not purchase_rows:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "reason": "关联采购支出 OA 没有可用明细，保留当前 SKU。",
        }

    existing_items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version_name},
        fields=["name", "manual_override_flag", "source_type"],
        limit_page_length=10000,
    )
    if _has_logistics_item_structure(
        batch_name=batch_name, version_name=version_name, approval_item=approval_item, existing_items=existing_items,
    ):
        return {
            "action": "skipped", "ok": True, "created_count": 0, "updated_count": 0, "deleted_count": 0,
            "confirmation_required": True, "existing_count": len(existing_items),
            "reason": "保留物流明细和采购事实；物料调整需在自动填充资料中确认。",
        }
    manual_items = [item for item in existing_items if int(item.get("manual_override_flag") or 0)]
    if manual_items:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "reason": "当前批次存在人工修改的 SKU 明细，未自动按采购支出明细替换。",
            "manual_count": len(manual_items),
            "existing_count": len(existing_items),
        }

    if existing_items:
        frappe.db.delete("Overseas Cost Item", {"batch": batch_name, "version": version_name})

    created_names: list[str] = []
    for index, row in enumerate(purchase_rows, start=1):
        values = _build_purchase_expense_item_doc_values(
            row=row,
            row_no=index,
            batch_name=batch_name,
            version_name=version_name,
            approval_item=approval_item,
        )
        values["doctype"] = "Overseas Cost Item"
        created_names.append(frappe.get_doc(values).insert(ignore_permissions=True).name)

    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        {
            "item_count": len(created_names),
            "status": "Imported",
        },
        update_modified=True,
    )
    _insert_batch_audit_log(
        batch_name=batch_name,
        field_name="purchase_expense_items",
        old_value={"item_count": len(existing_items), "source": "oa_logistics_items"},
        new_value={"created_count": len(created_names), "display_grain": "purchase_expense_line"},
        remark="按经理口径以关联采购支出 OA 明细作为 SKU 展示和 ERP 推送粒度，国际物流 OA 仅作为费用来源。",
    )
    return {
        "action": "replaced",
        "ok": True,
        "created_count": len(created_names),
        "updated_count": len(created_names),
        "deleted_count": len(existing_items),
        "purchase_item_count": len(purchase_rows),
        "item_names": created_names,
        "message": f"已按采购支出 OA 明细重建 {len(created_names)} 条 SKU。",
    }


def _restore_main_logistics_items_after_excluded_purchases(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    excluded_purchase_summaries: list[dict],
) -> dict:
    """审核已排除采购的历史行，保留现状并要求用户确认后恢复。"""
    from overseas_costing.services.logistics_settlement.runtime import has_final_binding
    if frappe is not None and has_final_binding(batch_name):
        return {'action': 'skipped', 'ok': True, 'created_count': 0, 'updated_count': 0, 'skipped': True, 'reason': '已关联最终物流采购支出，保留其费用与物料来源'}

    excluded_purchase_decisions = [
        {
            "source_approval_no": _clean(summary.get("source_approval_no")),
            "source_instance_id": _clean(summary.get("source_instance_id") or summary.get("instance_id")),
            "process_status": _clean(summary.get("process_status")),
            "approval_result": _clean(summary.get("approval_result")),
            "effective_status": _clean(summary.get("effective_status") or summary.get("approval_status")),
            "message": _clean(summary.get("message")),
        }
        for summary in excluded_purchase_summaries
        if isinstance(summary, dict)
    ]

    def manual_required(reason: str, **details) -> dict:
        result = {
            "action": "manual_required",
            "ok": True,
            "created_count": 0,
            "deleted_count": 0,
            "reason": reason,
            **details,
        }
        try:
            _insert_batch_audit_log(
                batch_name=batch_name,
                field_name="invalid_purchase_item_repair_review",
                old_value={"version_name": version_name},
                new_value={
                    **result,
                    "excluded_purchase_decisions": excluded_purchase_decisions,
                    "affected_fields": ["source_type", "dingtalk_instance_id", "item_count"],
                },
                remark="已排除采购审批的历史 SKU 无法安全自动恢复，已保留现状并标记人工处理。",
            )
            result["audit_logged"] = True
        except Exception as exc:
            result.update(
                {
                    "ok": False,
                    "audit_logged": False,
                    "audit_error": str(exc),
                }
            )
        return result

    if frappe is None or not hasattr(frappe, "get_all"):
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "deleted_count": 0,
            "reason": "当前环境无法检查历史 SKU 来源，未执行恢复。",
        }
    if not version_name:
        return manual_required("当前批次没有版本，无法安全恢复主物流审批物料。")

    excluded_instance_ids = {
        _clean(summary.get("source_instance_id") or summary.get("instance_id"))
        for summary in excluded_purchase_summaries
        if isinstance(summary, dict)
        and _clean(summary.get("source_instance_id") or summary.get("instance_id"))
    }
    if not excluded_instance_ids:
        return manual_required("已排除采购审批缺少流程实例 ID，不能安全判断历史 SKU 来源。")

    existing_items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version_name},
        fields=["name", "manual_override_flag", "source_type", "dingtalk_instance_id"],
        limit_page_length=10000,
    )
    if not existing_items:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "deleted_count": 0,
            "reason": "当前批次没有需要修复的历史 SKU。",
        }

    protected_items = [
        item
        for item in existing_items
        if int(item.get("manual_override_flag") or 0)
        or _clean(item.get("source_type")).upper() != "PURCHASE_EXPENSE_OA"
        or _clean(item.get("dingtalk_instance_id")) not in excluded_instance_ids
    ]
    if protected_items:
        return manual_required(
            "当前 SKU 包含人工修改、其他来源或无法证明来自已排除采购审批的数据，未自动替换。",
            protected_count=len(protected_items),
            existing_count=len(existing_items),
        )

    main_item_values = build_oa_item_values_from_approval(approval_item)
    if not main_item_values:
        return manual_required(
            "主物流审批没有可恢复的物料行，保留现有数据待人工处理。",
            existing_count=len(existing_items),
        )

    return manual_required(
        "关联采购审批已排除，历史物料行已保留；请在自动填充资料中预览主物流明细并确认后恢复。",
        existing_count=len(existing_items), logistics_item_count=len(main_item_values),
        confirmation_required=True,
    )


def _oa_attachment_parse_targets(record: dict) -> list[str]:
    attachment_type = _clean(record.get("attachment_type"))
    extension = _clean(record.get("file_ext")).lower()
    if attachment_type == "Packing List" or extension in {"xls", "xlsx", "csv"}:
        return ["actual_shipped_qty", "gross_weight_kg", "volume_m3", "chargeable_weight_kg"]
    if attachment_type == "Tax Certificate":
        return ["pedimento_no", "tax_totals", "paid_total_mxn", "line_items"]
    if attachment_type == "Logistics Bill":
        return ["logistics_fee", "fuel_surcharge", "currency", "bill_total"]
    if attachment_type == "Commercial Invoice":
        return ["invoice_no", "goods_value", "currency", "line_items"]
    return []


def _oa_attachment_source_doc_no(approval_item: dict, record: dict) -> str:
    approval_no = _clean(approval_item.get("source_approval_no"))
    instance_id = _clean(approval_item.get("source_instance_id"))
    source_no = approval_no or instance_id or "OA"
    file_key = _clean(record.get("file_id") or record.get("file_url") or record.get("file_name"))
    if len(file_key) > 90:
        file_key = file_key[:90]
    return f"{source_no}::{file_key}" if file_key else source_no


def _build_oa_attachment_values(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    record: dict,
) -> dict:
    origin = _clean(record.get("origin")) or "Form"
    origin_label = "评论" if origin == "Comment" else "发起表单"
    parse_snapshot = {
        "source": "dingtalk_oa_comment_attachment" if origin == "Comment" else "dingtalk_oa_form_attachment",
        "attachment_origin": origin,
        "comment_attachments_included": True,
        "approval_no": approval_item.get("source_approval_no") or "",
        "instance_id": approval_item.get("source_instance_id") or "",
        "originator_userid": approval_item.get("originator_userid") or "",
        "source_field": record.get("source_field") or "",
        "component_type": record.get("component_type") or "",
        "file_id": record.get("file_id") or "",
        "space_id": record.get("space_id") or "",
        "file_size": record.get("file_size") or "",
        "file_ext": record.get("file_ext") or "",
        "comment_user_id": record.get("comment_user_id") or "",
        "comment_time": record.get("comment_time") or "",
        "comment_remark": record.get("comment_remark") or "",
        "raw_attachment": record.get("raw") or {},
    }
    mapped_snapshot = {
        "parse_targets": _oa_attachment_parse_targets(record),
        "next_step": "待从归档存储下载附件后解析。",
    }
    return {
        "batch": batch_name,
        "version": version_name,
        "source_type": "OA",
        "oa_attachment_origin": origin,
        "attachment_type": record.get("attachment_type") or "Other",
        "source_doc_no": _oa_attachment_source_doc_no(approval_item, record),
        "file_name": record.get("file_name") or "",
        "file_url": record.get("file_url") or "",
        "parse_status": "Queued",
        "parse_result_json": _json_dumps(parse_snapshot),
        "mapped_result_json": _json_dumps(mapped_snapshot),
        "remark": f"钉钉{origin_label}附件已登记，等待归档、下载和解析。",
    }


def _find_existing_oa_attachment(values: dict) -> str:
    if frappe is None:
        return ""
    filters = {
        "batch": values.get("batch"),
        "source_type": "OA",
        "source_doc_no": values.get("source_doc_no"),
    }
    if not filters["batch"] or not filters["source_doc_no"]:
        return ""
    return frappe.db.get_value("Overseas Cost Attachment", filters, "name") or ""


def _save_existing_oa_attachment(existing_name: str, values: dict) -> None:
    doc = frappe.get_doc("Overseas Cost Attachment", existing_name)
    if hasattr(doc, "reload"):
        doc.reload()
    for fieldname, value in values.items():
        setattr(doc, fieldname, value)
    try:
        doc.save(ignore_permissions=True)
    except Exception as exc:
        if exc.__class__.__name__ != "TimestampMismatchError":
            raise
        doc = frappe.get_doc("Overseas Cost Attachment", existing_name)
        if hasattr(doc, "reload"):
            doc.reload()
        for fieldname, value in values.items():
            setattr(doc, fieldname, value)
        doc.save(ignore_permissions=True)


def _sync_oa_form_attachments(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
) -> dict:
    attachments = (
        approval_item.get("oa_attachments")
        or approval_item.get("oa_form_attachments")
        or extract_attachments_from_form_fields(approval_item.get("form_fields") or {})
    )
    if frappe is None:
        return {
            "action": "preview",
            "attachment_count": len(attachments),
            "created_count": 0,
            "updated_count": 0,
            "items": attachments,
        }
    if not attachments:
        return {
            "action": "skipped",
            "attachment_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "reason": "钉钉审批未解析到表单或评论附件。",
        }

    created_names: list[str] = []
    updated_names: list[str] = []
    for record in attachments:
        values = _build_oa_attachment_values(
            batch_name=batch_name,
            version_name=version_name,
            approval_item=approval_item,
            record=record,
        )
        existing_name = _find_existing_oa_attachment(values)
        if existing_name:
            _save_existing_oa_attachment(existing_name, values)
            updated_names.append(existing_name)
        else:
            doc = frappe.get_doc({"doctype": "Overseas Cost Attachment", **values}).insert(ignore_permissions=True)
            created_names.append(doc.name)

    if created_names or updated_names:
        _insert_batch_audit_log(
            batch_name=batch_name,
            field_name="oa_attachments",
            old_value=None,
            new_value={
                "created_count": len(created_names),
                "updated_count": len(updated_names),
                "attachment_count": len(attachments),
                "comment_attachments_included": True,
            },
            remark="登记钉钉发起表单和评论附件",
        )

    return {
        "action": "synced",
        "attachment_count": len(attachments),
        "created_count": len(created_names),
        "updated_count": len(updated_names),
        "created_names": created_names,
        "updated_names": updated_names,
    }


def _sync_linked_purchase_fields(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
) -> dict:
    """按国际物流 OA 关联的采购支出审批，自动补采购单价、币种和货值。"""

    linked_approvals = approval_item.get("linked_purchase_approvals") or []
    if not linked_approvals:
        return {
            "action": "skipped",
            "ok": True,
            "linked_purchase_count": 0,
            "updated_count": 0,
            "reason": "当前国际物流 OA 没有关联采购支出审批。",
        }
    if frappe is None:
        return {
            "action": "preview",
            "ok": True,
            "linked_purchase_count": len(linked_approvals),
            "updated_count": 0,
            "reason": "当前未连接 Frappe，仅返回关联采购支出审批数量。",
        }

    try:
        from overseas_costing.services import import_service

        preview_result = import_service.preview_linked_purchase_expense_oa(
            batch_name=batch_name,
            version_name=version_name,
            linked_purchase_json=_json_dumps(linked_approvals),
            trusted_server_payload=True,
        )
        purchase_summaries = preview_result.get("purchase_summaries") or []
        if purchase_summaries:
            _persist_linked_purchase_approval_statuses(
                batch_name=batch_name,
                summaries=purchase_summaries,
            )
        purchase_rows = _purchase_expense_rows_from_preview(preview_result)
        excluded_purchase_count = int(preview_result.get("excluded_purchase_summary_count") or 0)
        if purchase_summaries and excluded_purchase_count >= len(purchase_summaries):
            repair_result = _restore_main_logistics_items_after_excluded_purchases(
                batch_name=batch_name,
                version_name=version_name,
                approval_item=approval_item,
                excluded_purchase_summaries=preview_result.get("excluded_purchase_summaries") or purchase_summaries,
            )
            return {
                "action": repair_result.get("action") or "skipped",
                "sync_strategy": "excluded_purchase_approvals",
                "ok": bool(repair_result.get("ok")),
                "linked_purchase_count": len(linked_approvals),
                "excluded_purchase_count": excluded_purchase_count,
                "updated_count": repair_result.get("updated_count", 0),
                "changed_field_count": repair_result.get("created_count", 0),
                "created_count": repair_result.get("created_count", 0),
                "deleted_count": repair_result.get("deleted_count", 0),
                "skipped_count": len(purchase_summaries),
                "unmatched_count": 0,
                "ambiguous_count": 0,
                "message": repair_result.get("message")
                or repair_result.get("reason")
                or "关联采购审批已拒绝、撤销或终止，已排除对应采购数据和成本写入。",
                "excluded_purchase_summaries": preview_result.get("excluded_purchase_summaries") or [],
                "repair_result": repair_result,
            }
        from overseas_costing.services.logistics_settlement.runtime import has_final_binding
        if _has_logistics_item_structure(
            batch_name=batch_name, version_name=version_name, approval_item=approval_item,
        ):
            return {**_logistics_purchase_preview_result(preview_result), "linked_purchase_count": len(linked_approvals)}
        if preview_result.get("ok") and purchase_rows and not has_final_binding(batch_name):
            rebuild_result = _replace_items_with_purchase_expense_rows(
                batch_name=batch_name,
                version_name=version_name,
                approval_item=approval_item,
                purchase_rows=purchase_rows,
            )
            return {
                "action": "synced" if rebuild_result.get("ok") else "failed",
                "sync_strategy": "purchase_expense_items",
                "ok": bool(rebuild_result.get("ok")),
                "linked_purchase_count": len(linked_approvals),
                "updated_count": rebuild_result.get("updated_count", 0),
                "changed_field_count": rebuild_result.get("created_count", 0),
                "created_count": rebuild_result.get("created_count", 0),
                "deleted_count": rebuild_result.get("deleted_count", 0),
                "skipped_count": 0 if rebuild_result.get("action") == "replaced" else 1,
                "unmatched_count": 0,
                "ambiguous_count": 0,
                "message": rebuild_result.get("message") or rebuild_result.get("reason") or "",
                "rebuild_result": rebuild_result,
            }

        result = import_service.apply_linked_purchase_expense_fillable_fields(
            batch_name=batch_name,
            version_name=version_name,
            linked_purchase_json=_json_dumps(linked_approvals),
            recalculate_after_writeback=False,
            trusted_server_payload=True,
        )
    except Exception as exc:
        return {
            "action": "failed",
            "ok": False,
            "linked_purchase_count": len(linked_approvals),
            "updated_count": 0,
            "message": f"关联采购支出 OA 同步失败：{exc}",
        }

    return {
        "action": "synced" if result.get("ok") else "failed",
        "sync_strategy": "matched_field_update",
        "ok": bool(result.get("ok")),
        "linked_purchase_count": len(linked_approvals),
        "updated_count": result.get("updated_count", 0),
        "changed_field_count": result.get("changed_field_count", 0),
        "skipped_count": result.get("skipped_count", 0),
        "unmatched_count": result.get("unmatched_count", 0),
        "ambiguous_count": result.get("ambiguous_count", 0),
        "message": result.get("message") or "",
        }


def _persist_linked_purchase_approval_statuses(*, batch_name: str, summaries: list[dict]) -> None:
    """保存关联采购审批的最新状态，便于后续解释为何不进入核算。"""

    if frappe is None or not summaries or not hasattr(frappe, "db"):
        return
    if not hasattr(frappe.db, "get_value") or not hasattr(frappe.db, "set_value"):
        return

    row = frappe.db.get_value(
        "Overseas Cost Batch",
        batch_name,
        ["extra_json"],
        as_dict=True,
    ) or {}
    root, trace, is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
    linked_lists: list[list[dict]] = []
    for candidate in (
        trace.get("linked_purchase_approvals"),
        trace.get("purchase_summaries"),
        (trace.get("purchase_sync") or {}).get("linked_purchase_approvals")
        if isinstance(trace.get("purchase_sync"), dict)
        else None,
        (trace.get("purchase_sync") or {}).get("purchase_summaries")
        if isinstance(trace.get("purchase_sync"), dict)
        else None,
        (trace.get("purchase_sync") or {}).get("items")
        if isinstance(trace.get("purchase_sync"), dict)
        else None,
    ):
        if isinstance(candidate, list) and candidate not in linked_lists:
            linked_lists.append(candidate)
    if not linked_lists:
        return

    summary_by_key = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        key = (
            _clean(summary.get("source_approval_no")),
            _clean(summary.get("source_instance_id")),
        )
        summary_by_key[key] = summary

    def update_links(linked_approvals: list[dict]) -> list[dict]:
        updated_links = []
        for linked in linked_approvals:
            if not isinstance(linked, dict):
                updated_links.append(linked)
                continue
            key = (
                _clean(linked.get("approval_no") or linked.get("source_approval_no")),
                _clean(
                    linked.get("source_instance_id")
                    or linked.get("proc_inst_id")
                    or linked.get("instance_id")
                ),
            )
            summary = summary_by_key.get(key)
            if not summary:
                updated_links.append(linked)
                continue
            updated_links.append(
                {
                    **linked,
                    "approval_status": summary.get("approval_status") or "",
                    "approval_title": summary.get("approval_title") or "",
                    "message": summary.get("message") or "",
                }
            )
        return updated_links

    updated_lists = [update_links(linked_approvals) for linked_approvals in linked_lists]
    if isinstance(trace.get("linked_purchase_approvals"), list):
        trace["linked_purchase_approvals"] = updated_lists[0]
    else:
        trace["linked_purchase_approvals"] = updated_lists[0]
    purchase_sync = trace.get("purchase_sync")
    if isinstance(purchase_sync, dict):
        for fieldname in ("linked_purchase_approvals", "purchase_summaries", "items"):
            candidate = purchase_sync.get(fieldname)
            for index, original in enumerate(linked_lists):
                if candidate is original:
                    purchase_sync[fieldname] = updated_lists[index]
                    break
        trace["purchase_sync"] = purchase_sync
    frappe.db.set_value(
        "Overseas Cost Batch",
        batch_name,
        "extra_json",
        _set_oa_trace_in_extra(root, trace, is_root_trace),
        update_modified=False,
    )


def _oa_fee_sync_context(batch_name, version_name, *, edit_token=None, expected_modified=None) -> dict:
    """Serialize fee writers with the batch lease and current version guards."""
    from overseas_costing.services import edit_session_service

    rows = frappe.db.sql(
        """select name, current_version, transport_mode, status, confirm_status, is_locked,
                  modified, edit_lock_owner, edit_lock_token, edit_lock_expires_at,
                  source_approval_status, extra_json
             from `tabOverseas Cost Batch` where name = %s for update""",
        (batch_name,), as_dict=True)
    if not rows:
        raise ValueError(_("未找到当前批次。"))
    batch = dict(rows[0])
    if edit_token is not None or expected_modified is not None:
        if not edit_token or not expected_modified:
            raise PermissionError(_("缺少编辑会话或数据版本，请刷新后重试。"))
        edit_session_service.assert_editable(batch, str(frappe.session.user), str(edit_token), expected_modified)
    elif edit_session_service.lock_is_active(batch):
        raise PermissionError(_("当前批次存在正在使用的编辑会话，OA 费用同步暂缓。"))
    if batch.get("current_version") != version_name:
        raise ValueError(_("只能更新当前成本版本的物流费用。"))
    versions = frappe.db.sql(
        "select name, batch, status from `tabOverseas Cost Version` where name = %s for update",
        (version_name,), as_dict=True)
    if not versions or versions[0].get("batch") != batch_name:
        raise ValueError(_("成本版本不属于当前批次。"))
    if (batch.get("confirm_status") in {"Confirmed", "Partially Confirmed"}
            or batch.get("status") in {"Confirmed", "Written Back"}
            or batch.get("is_locked") in (1, True, "1") or versions[0].get("status") != "Active"):
        raise PermissionError(_("已确认或归档版本不能覆盖物流费用，请先创建调整版本。"))
    return batch


def _sync_oa_fee_descriptor(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    fee_descriptor: dict | None = None,
    edit_token: str | None = None,
    expected_modified: str | None = None,
    manual_entry: bool = False,
) -> dict:
    from overseas_costing.services.logistics_settlement.runtime import has_final_binding
    if frappe is not None and has_final_binding(batch_name):
        return {'action': 'skipped', 'ok': True, 'created_count': 0, 'updated_count': 0, 'skipped': True, 'reason': '已关联最终物流采购支出，保留其费用与物料来源'}

    """用同一条保护路径把一项 OA 费用落成整票分摊规则。"""

    if manual_entry and (not edit_token or not expected_modified):
        return {"ok": False, "action": "blocked", "created_count": 0, "updated_count": 0,
                "message": "缺少编辑会话或数据版本，请刷新后重试。"}

    fee = dict(fee_descriptor or {})
    if not fee:
        fee = approval_item.get("logistics_fee") if isinstance(approval_item.get("logistics_fee"), dict) else {}
        fee = fee or extract_logistics_fee_from_approval(approval_item)
    if not fee and detect_approval_transport_mode(
        approval_item.get("transport_mode") or approval_item.get("transport_mode_raw") or (approval_item.get("form_fields") or {})
    ) == "EXPRESS":
        quote_candidates = approval_item.get("logistics_quote_candidates")
        if not isinstance(quote_candidates, list):
            quote_candidates = extract_logistics_quote_candidates_from_approval(approval_item)
        valid_quotes = [candidate for candidate in quote_candidates if _parse_money_amount(candidate.get("amount")) is not None]
        if len(valid_quotes) == 1:
            selected = valid_quotes[0]
            fee = {
                "amount": selected.get("amount"),
                "currency": selected.get("currency") or "RMB",
                "source_label": "快递物流报价",
                "source_field": selected.get("source_field") or "物流报价",
                "source_value": selected.get("evidence_line") or selected.get("source_value") or "",
            }
    parsed_amount = _parse_money_amount(fee.get("amount")) if fee else None
    if not fee or parsed_amount is None:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "reason": "当前国际物流 OA 没有解析到明确物流费用，暂不生成分摊规则。",
        }
    if not version_name:
        return {
            "action": "skipped",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "reason": "当前批次没有版本，无法生成物流费用分摊规则。",
        }

    from overseas_costing.services import batch_service, fee_service, import_service

    if frappe is not None:
        invalid_response = import_service._invalid_batch_cost_write_response(batch_name, version_name)
        if invalid_response:
            return {**invalid_response, "action": "blocked", "created_count": 0, "updated_count": 0}
        try:
            batch = _oa_fee_sync_context(batch_name, version_name, edit_token=edit_token, expected_modified=expected_modified)
            # Under REPEATABLE READ ordinary queries still see a pre-lock
            # snapshot. Validate the locked batch and current item provenance.
            items = frappe.db.sql(
                """select name, source_type, dingtalk_instance_id from `tabOverseas Cost Item`
                    where batch = %s and version = %s order by name for update""",
                (batch_name, version_name), as_dict=True)
            invalid = batch_service._build_invalid_business_state(batch, items)
            if invalid.get("invalid"):
                return {"ok": False, "invalid_business": True, "invalid_business_scope": invalid.get("scope"),
                        "message": invalid.get("message"), "action": "blocked", "created_count": 0, "updated_count": 0}
            mode = batch.get("transport_mode")
            if fee.get("logical_fee_key"):
                definition = {
                    "logical_fee_key": fee["logical_fee_key"],
                    "expense_category": fee.get("expense_category") or fee.get("source_label") or fee["logical_fee_key"],
                    "allocation_basis": fee.get("allocation_basis") or "goods_value",
                    "required_evidence_role": fee.get("required_evidence_role") or "expense_invoice",
                }
            else:
                definition = fee_service.primary_freight_definition(mode)
        except (ValueError, PermissionError, RuntimeError) as exc:
            return {"ok": False, "action": "blocked", "created_count": 0, "updated_count": 0, "message": str(exc)}
    else:
        mode = detect_approval_transport_mode(approval_item.get("transport_mode") or approval_item.get("transport_mode_raw")
                                              or (approval_item.get("form_fields") or {}))
        try:
            if fee.get("logical_fee_key"):
                definition = {
                    "logical_fee_key": fee["logical_fee_key"],
                    "expense_category": fee.get("expense_category") or fee.get("source_label") or fee["logical_fee_key"],
                    "allocation_basis": fee.get("allocation_basis") or "goods_value",
                    "required_evidence_role": fee.get("required_evidence_role") or "expense_invoice",
                }
            else:
                definition = fee_service.primary_freight_definition(mode)
        except ValueError as exc:
            return {"ok": False, "action": "blocked", "created_count": 0, "updated_count": 0, "message": str(exc)}

    amount = float(parsed_amount)
    currency = _normalize_currency_code(fee.get("currency")) or "RMB"
    allocation_basis = definition["allocation_basis"]
    manual_basis = str(fee.get("allocation_basis") or approval_item.get("allocation_basis") or "").strip()
    if manual_entry and manual_basis in fee_service.ALLOCATION_BASES:
        allocation_basis = manual_basis
    rule_code = str(fee.get("rule_code") or "oa_logistics_freight").strip()
    values = {
        "batch": batch_name,
        "version": version_name,
        "rule_code": rule_code,
        **definition,
        "amount_status": "ESTIMATED",
        "allocation_basis": allocation_basis,
        "basis_field": allocation_basis,
        "currency": currency,
        "amount": amount,
        "priority_no": 20,
        "remark": f"从钉钉国际物流 OA {fee.get('source_label') or '物流费用'}生成：{fee.get('source_field') or ''}={fee.get('source_value')}",
        "is_active": 1,
        "is_enabled": 1,
    }
    revision = ("manual:" if manual_entry else "oa:") + hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
    values.update(amount_revision=revision, scope_revision=revision)

    if frappe is None:
        return {
            "action": "preview",
            "ok": True,
            "created_count": 0,
            "updated_count": 0,
            "rule": values,
            "fee": fee,
        }

    # Read protected/manual/retired rows from the current database state, not
    # the transaction snapshot that may precede another user's committed save.
    rule_fields = ", ".join(f"`{field}`" for field in fee_service._rule_fields())
    rules = frappe.db.sql(
        f"""select {rule_fields} from `tabOverseas Cost Allocation Rule`
            where batch = %s and version = %s order by name for update""",
        (batch_name, version_name), as_dict=True)
    candidates = [dict(row) for row in rules if row.get("rule_code") == rule_code
                  or fee_service.map_historical_fee_key(row, mode) == definition["logical_fee_key"]]
    active = [row for row in candidates if fee_service.fee_is_active(row)]

    def preserve(action, reason, *, ok=True):
        names = [str(row.get("name") or "") for row in candidates]
        _insert_batch_audit_log(batch_name=batch_name, field_name="oa_logistics_fee_candidate",
                               old_value={"rule_names": names},
                               new_value={"candidate_fee": fee, "action": action, "reason": reason},
                               remark="保留新的 OA 物流报价供复核，现有费用记录未修改。")
        return {"ok": ok, "action": action, "created_count": 0, "updated_count": 0,
                "rule_name": names[0] if names else "", "fee": fee, "message": reason, "reason": reason,
                **({"duplicate_rule_names": [str(row.get("name") or "") for row in active]} if action == "conflict" else {})}

    if len(active) > 1:
        return preserve("conflict", f"{definition['expense_category']}存在重复记录，请先核对并停用重复费用。", ok=False)
    if any(not fee_service.fee_is_active(row) for row in candidates):
        return preserve("retired", "该物流费用已有停用记录，同步保留停用状态。")
    current = active[0] if active else {}
    existing_name = current.get("name")
    if existing_name:
        revisions = [str(current.get(field) or "") for field in ("amount_revision", "scope_revision")]
        if (current.get("rule_code") != rule_code
                or str(current.get("amount_status") or "").upper() in {"ACTUAL", "NOT_INCURRED", "INCLUDED"}
                or any(value and not value.startswith("oa:") for value in revisions)
                or current.get("logical_fee_key") not in (None, "", definition["logical_fee_key"])):
            return preserve("protected", "该物流费用已经人工保存或确认，新报价已保留供复核。")
        # Revision tracks only changes to its source facts; keep existing scope and
        # amount revision values on a refresh that has not changed those inputs.
        if not manual_entry and all(_values_match(current.get(field), values[field]) for field in ("amount", "currency", "amount_status")):
            values["amount_revision"] = current.get("amount_revision") or revision
        if not manual_entry and all(_values_match(current.get(field), values[field]) for field in ("allocation_basis", "basis_field")):
            values["scope_revision"] = current.get("scope_revision") or revision
        if current and all(_values_match(current.get(fieldname), value) for fieldname, value in values.items()):
            return {
                "action": "unchanged",
                "ok": True,
                "rule_name": existing_name,
                "created_count": 0,
                "updated_count": 0,
                "fee": fee,
            }
        frappe.db.set_value("Overseas Cost Allocation Rule", existing_name, values, update_modified=True)
        action = "updated"
        rule_name = existing_name
        created_count = 0
        updated_count = 1
    else:
        rule_name = frappe.get_doc({"doctype": "Overseas Cost Allocation Rule", **values}).insert(ignore_permissions=True).name
        action = "created"
        created_count = 1
        updated_count = 0

    frappe.db.set_value("Overseas Cost Batch", batch_name, {"status": "Dirty"}, update_modified=True)

    _insert_batch_audit_log(
        batch_name=batch_name,
        field_name="oa_logistics_fee_rule",
        old_value={"rule_name": existing_name} if existing_name else None,
        new_value={**values, "rule_name": rule_name},
        remark="从钉钉国际物流 OA 生成/更新物流费用分摊规则",
    )
    return {
        "action": action,
        "ok": True,
        "rule_name": rule_name,
        "created_count": created_count,
        "updated_count": updated_count,
        "fee": fee,
        "rule": values,
    }


def _sync_oa_logistics_allocation_rule(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    edit_token: str | None = None,
    expected_modified: str | None = None,
    manual_entry: bool = False,
) -> dict:
    """Compatibility adapter for the manual primary-freight quote APIs.

    Callers: ``confirm_logistics_quote_candidate``,
    ``save_manual_logistics_quote`` and the historical
    ``backfill_express_single_quote_freight_rules`` repair command. Retain
    while those entry points accept legacy quote payloads; remove after both
    public APIs submit canonical logical-fee descriptors and the backfill
    command has left the supported replay window.
    """

    return _sync_oa_fee_descriptor(
        batch_name=batch_name,
        version_name=version_name,
        approval_item=approval_item,
        edit_token=edit_token,
        expected_modified=expected_modified,
        manual_entry=manual_entry,
    )


def _sync_oa_logistics_allocation_rules(
    *,
    batch_name: str,
    version_name: str,
    approval_item: dict,
    include_new_optional_fees: bool = True,
) -> dict:
    """Synchronize every positive logistics fee through the canonical upsert."""

    from overseas_costing.services import fee_service

    descriptors = extract_logistics_fees_from_approval(approval_item)
    if not include_new_optional_fees:
        descriptors = [
            descriptor
            for descriptor in descriptors
            if descriptor.get("logical_fee_key") in {definition[0] for definition in fee_service.PRIMARY_FREIGHT.values()}
        ]
    if not descriptors:
        return {
            "ok": True,
            "action": "skipped",
            "created_count": 0,
            "updated_count": 0,
            "results": [],
            "reason": "当前国际物流 OA 没有可同步的正数费用。",
        }

    results = [
        _sync_oa_fee_descriptor(
            batch_name=batch_name,
            version_name=version_name,
            approval_item=approval_item,
            fee_descriptor=descriptor,
        )
        for descriptor in descriptors
    ]
    failed = [result for result in results if result.get("ok") is False]
    created_count = sum(int(result.get("created_count") or 0) for result in results)
    updated_count = sum(int(result.get("updated_count") or 0) for result in results)
    if failed:
        action = "blocked"
    elif created_count:
        action = "created"
    elif updated_count:
        action = "updated"
    elif all(result.get("action") == "unchanged" for result in results):
        action = "unchanged"
    else:
        action = "preserved"
    return {
        "ok": not failed,
        "action": action,
        "created_count": created_count,
        "updated_count": updated_count,
        "results": results,
        "message": "；".join(str(result.get("message") or "").strip() for result in failed if result.get("message")),
    }


def _is_database_connection_lost(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "server has gone away",
            "lost connection",
            "connection already closed",
            "mysql server has gone away",
            "(2006",
            "(2013",
        )
    )


def _reset_database_connection() -> None:
    if frappe is None:
        return
    try:
        frappe.db.rollback()
    except Exception:
        pass
    try:
        frappe.db.close()
    except Exception:
        pass
    if hasattr(frappe.db, "connect"):
        frappe.db.connect()


def _run_with_database_retry(operation):
    try:
        return operation()
    except Exception as exc:
        if not _is_database_connection_lost(exc):
            raise
        _reset_database_connection()
        return operation()


def _commit_oa_pull_progress() -> None:
    if frappe is None or not hasattr(frappe.db, "commit"):
        return
    frappe.db.commit()


def _recalculate_after_purchase_sync(
    *,
    batch_name: str,
    version_name: str,
    purchase_sync: dict,
    logistics_fee_sync: dict | None = None,
) -> dict:
    if frappe is None:
        return {"action": "skipped", "reason": "当前未连接 Frappe。"}
    if logistics_fee_sync and logistics_fee_sync.get("ok") is False:
        return {"action": "blocked", "ok": False, "message": logistics_fee_sync.get("message") or "物流费用同步未完成，暂不自动试算。"}
    purchase_changed = purchase_sync.get("ok") and int(purchase_sync.get("updated_count") or 0) > 0
    fee_changed = bool(logistics_fee_sync and logistics_fee_sync.get("ok") and logistics_fee_sync.get("action") in {"created", "updated"})
    if not purchase_changed and not fee_changed:
        return {"action": "skipped", "reason": "采购字段和物流费用规则没有新增写入，暂不自动试算。"}
    try:
        from overseas_costing.services.calculate_service import recalculate_batch

        result = recalculate_batch(batch_name=batch_name, version_name=version_name)
        return {"action": "recalculated", "ok": bool(result.get("ok", True)), "result": result}
    except Exception as exc:
        return {"action": "failed", "ok": False, "message": f"采购字段同步后自动试算失败：{exc}"}


def _get_oa_trace_from_extra(extra_json: Any) -> tuple[dict, dict, bool]:
    data = _json_loads_dict(extra_json)
    if data.get("source") == "dingtalk_oa_logistics":
        return data, data, True
    trace = data.get("oa_logistics_trace")
    if isinstance(trace, dict):
        return data, trace, False
    return data, {}, False


def _set_oa_trace_in_extra(root: dict, trace: dict, is_root_trace: bool) -> str:
    if is_root_trace:
        merged = {**root, **trace}
    else:
        merged = dict(root)
        merged["oa_logistics_trace"] = trace
    return _json_dumps(merged)


def _trace_finish_time_candidates(row: dict, trace: dict) -> list[Any]:
    candidates = [
        row.get("source_finished_at"),
        trace.get("finish_time"),
        trace.get("finishTime"),
        trace.get("source_finished_at"),
        trace.get("finished_at"),
        trace.get("completed_at"),
        trace.get("complete_time"),
        trace.get("approval_finished_at"),
    ]
    raw_instance = trace.get("raw_instance") if isinstance(trace.get("raw_instance"), dict) else {}
    if raw_instance:
        candidates.extend(
            [
                raw_instance.get("finish_time"),
                raw_instance.get("finishTime"),
                raw_instance.get("completed_at"),
                raw_instance.get("complete_time"),
            ]
        )
    return candidates


def _resolve_trace_finished_at(row: dict, trace: dict) -> tuple[str, str]:
    for candidate in _trace_finish_time_candidates(row, trace):
        normalized = _to_frappe_datetime(candidate)
        if normalized:
            return normalized, _clean(candidate)
    return "", ""


def sync_existing_oa_finished_times(limit: int | None = 200) -> dict:
    """从已保存的 OA 快照回填批次来源完成时间，仅补空值。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法回填历史批次来源完成时间。",
        }

    page_length = max(1, min(int(limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=[
            "name",
            "batch_no",
            "source_approval_no",
            "source_instance_id",
            "source_finished_at",
            "extra_json",
        ],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    updated_items: list[dict] = []
    skipped_items: list[dict] = []
    for row in rows:
        existing_finished_at = _to_frappe_datetime(row.get("source_finished_at"))
        if existing_finished_at:
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "reason": "已有来源完成时间",
                }
            )
            continue

        _root, trace, _is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        finished_at, raw_finished_at = _resolve_trace_finished_at(row, trace)
        if not finished_at:
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_approval_no": row.get("source_approval_no") or trace.get("source_approval_no") or "",
                    "source_instance_id": row.get("source_instance_id") or trace.get("source_instance_id") or "",
                    "reason": "已保存 OA 快照里没有完成时间，需要重拉钉钉详情",
                }
            )
            continue

        frappe.db.set_value(
            "Overseas Cost Batch",
            row.get("name"),
            "source_finished_at",
            finished_at,
            update_modified=False,
        )
        _insert_batch_audit_log(
            batch_name=row.get("name"),
            field_name="source_finished_at",
            old_value=row.get("source_finished_at"),
            new_value=finished_at,
            remark="从已保存钉钉 OA 快照回填来源完成时间，用于缺真实付款日时暂估汇率",
        )
        updated_items.append(
            {
                "batch_name": row.get("name"),
                "batch_no": row.get("batch_no"),
                "source_approval_no": row.get("source_approval_no") or trace.get("source_approval_no") or "",
                "source_instance_id": row.get("source_instance_id") or trace.get("source_instance_id") or "",
                "source_finished_at": finished_at,
                "raw_finished_at": raw_finished_at,
            }
        )

    if updated_items and hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "dry_run": False,
        "scanned_count": len(rows),
        "updated_count": len(updated_items),
        "skipped_count": len(skipped_items),
        "items": updated_items,
        "skipped_items": skipped_items,
        "message": (
            f"已从本地 OA 快照回填 {len(updated_items)} 条批次来源完成时间；"
            f"{len(skipped_items)} 条未处理，缺快照完成时间的需重拉钉钉详情。"
        ),
    }


def refresh_missing_oa_finished_times(
    limit: int | None = 200,
    *,
    env_file: str | None = None,
    api_style: str = "auto",
    access_token: str = "",
) -> dict:
    """回钉钉重拉详情，只补缺失的来源完成时间和审批状态。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法重拉钉钉详情补来源完成时间。",
        }

    resolved_env_file = resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    resolved_api_style = _resolve_api_style(api_style)
    token = get_access_token(
        api_style=resolved_api_style,
        access_token=access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )

    page_length = max(1, min(int(limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics", "source_finished_at": ["in", ["", None]]},
        fields=[
            "name",
            "batch_no",
            "source_approval_no",
            "source_instance_id",
            "source_dingtalk_url",
            "source_approval_status",
            "source_finished_at",
            "extra_json",
        ],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    updated_items: list[dict] = []
    skipped_items: list[dict] = []
    failed_items: list[dict] = []
    for row in rows:
        root, trace, is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        instance_id = _resolve_batch_source_instance_id(row, trace)
        if not instance_id:
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "reason": "缺少钉钉审批实例 ID，无法重拉详情",
                }
            )
            continue

        try:
            detail = get_process_instance_detail(
                token=token,
                process_instance_id=instance_id,
                api_style=resolved_api_style,
            )
            summary = summarize_approval(detail, process_instance_id=instance_id)
        except Exception as exc:
            failed_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_instance_id": instance_id,
                    "reason": str(exc),
                }
            )
            continue

        finished_at = _to_frappe_datetime(summary.get("finish_time"))
        approval_status = _clean(summary.get("approval_status"))
        if not finished_at:
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_instance_id": instance_id,
                    "approval_status": approval_status,
                    "reason": "钉钉详情未返回完成时间",
                }
            )
            continue

        updates: dict[str, Any] = {"source_finished_at": finished_at}
        if approval_status and not _values_match(row.get("source_approval_status"), approval_status):
            updates["source_approval_status"] = approval_status

        trace_updates = {
            "source_instance_id": summary.get("source_instance_id") or instance_id,
            "source_approval_no": summary.get("source_approval_no") or row.get("source_approval_no") or trace.get("source_approval_no") or "",
            "source_dingtalk_url": summary.get("source_dingtalk_url") or row.get("source_dingtalk_url") or trace.get("source_dingtalk_url") or "",
            "approval_status": approval_status,
            "finish_time": summary.get("finish_time") or "",
        }
        updates["extra_json"] = _set_oa_trace_in_extra(root, {**trace, **trace_updates}, is_root_trace)
        frappe.db.set_value("Overseas Cost Batch", row.get("name"), updates, update_modified=False)
        _insert_batch_audit_log(
            batch_name=row.get("name"),
            field_name="source_finished_at",
            old_value=row.get("source_finished_at"),
            new_value=updates.get("source_finished_at") or "",
            remark="从钉钉审批详情补充来源完成时间，用于缺真实付款日时暂估汇率",
        )
        updated_items.append(
            {
                "batch_name": row.get("name"),
                "batch_no": row.get("batch_no"),
                "source_approval_no": trace_updates["source_approval_no"],
                "source_instance_id": instance_id,
                "source_approval_status": approval_status,
                "source_finished_at": finished_at,
                "changed_fields": list(updates.keys()),
            }
        )

    if updated_items and hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": not failed_items,
        "dry_run": False,
        "env_file_loaded": bool(resolved_env_file),
        "api_style": resolved_api_style,
        "scanned_count": len(rows),
        "updated_count": len(updated_items),
        "skipped_count": len(skipped_items),
        "failed_count": len(failed_items),
        "items": updated_items,
        "skipped_items": skipped_items,
        "failed_items": failed_items,
        "message": f"已重拉钉钉详情补充 {len(updated_items)} 条来源完成时间；失败 {len(failed_items)} 条。",
    }


def supplement_empty_oa_goods_items(batch_name: str) -> dict:
    """只为没有 SKU 的指定 OA 批次补建表单货物明细，不覆盖已有明细。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法补建历史批次明细。",
        }

    batch = frappe.db.get_value(
        "Overseas Cost Batch",
        _clean(batch_name),
        ["name", "batch_no", "current_version", "source_approval_no", "source_instance_id", "source_dingtalk_url", "source_remark", "extra_json"],
        as_dict=True,
    )
    if not batch:
        return {"ok": False, "message": "未找到指定的海外成本批次。"}

    root, trace, _is_root_trace = _get_oa_trace_from_extra(batch.get("extra_json"))
    form_fields = trace.get("form_fields") or {}
    if not form_fields:
        return {
            "ok": False,
            "batch_name": batch.get("name"),
            "message": "该批次没有保存原始审批字段，需先重拉对应钉钉审批单。",
        }

    approval_item = {
        "source_approval_no": batch.get("source_approval_no") or trace.get("source_approval_no") or "",
        "source_instance_id": batch.get("source_instance_id") or trace.get("source_instance_id") or "",
        "source_dingtalk_url": batch.get("source_dingtalk_url") or trace.get("source_dingtalk_url") or "",
        "transport_mode_raw": trace.get("transport_mode_raw") or batch.get("source_remark") or "",
        "logistics_no": batch.get("batch_no") or "",
        "form_fields": form_fields,
    }
    sync_result = _sync_oa_goods_items(
        batch_name=batch.get("name"),
        version_name=batch.get("current_version") or "",
        approval_item=approval_item,
        only_when_empty=True,
    )
    return {
        "ok": not sync_result.get("skipped") or bool(sync_result.get("existing_count")),
        "batch_name": batch.get("name"),
        "batch_no": batch.get("batch_no"),
        "item_sync": sync_result,
    }


def sync_existing_oa_form_attachments(limit: int | None = 200) -> dict:
    """从已有 OA 批次快照回填表单和评论附件记录。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法回填已有批次附件记录。",
        }

    page_length = max(1, min(int(limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=[
            "name",
            "batch_no",
            "current_version",
            "source_approval_no",
            "source_instance_id",
            "extra_json",
            "source_attachment_count",
        ],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    synced_items: list[dict] = []
    skipped_items: list[dict] = []
    total_created = 0
    total_updated = 0

    for row in rows:
        root, trace, is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        form_fields = trace.get("form_fields") or {}
        form_attachments = trace.get("oa_form_attachments") or extract_attachments_from_form_fields(form_fields)
        comment_attachments = trace.get("oa_comment_attachments") or []
        attachments = trace.get("oa_attachments") or _dedupe_attachment_records(
            [*(form_attachments or []), *(comment_attachments or [])]
        )
        if not attachments:
            skipped_items.append({"batch_name": row.get("name"), "batch_no": row.get("batch_no"), "reason": "未找到表单或评论附件"})
            continue

        approval_item = {
            "source_approval_no": row.get("source_approval_no") or trace.get("source_approval_no") or "",
            "source_instance_id": row.get("source_instance_id") or trace.get("source_instance_id") or "",
            "form_fields": form_fields,
            "oa_attachments": attachments,
            "oa_form_attachments": form_attachments,
            "oa_comment_attachments": comment_attachments,
        }
        sync_result = _sync_oa_form_attachments(
            batch_name=row.get("name"),
            version_name=row.get("current_version") or "",
            approval_item=approval_item,
        )
        total_created += int(sync_result.get("created_count") or 0)
        total_updated += int(sync_result.get("updated_count") or 0)

        if trace.get("oa_attachments") != attachments or int(row.get("source_attachment_count") or 0) != len(attachments):
            trace = {
                **trace,
                "oa_attachments": attachments,
                "oa_form_attachments": form_attachments,
                "oa_comment_attachments": comment_attachments,
            }
            frappe.db.set_value(
                "Overseas Cost Batch",
                row.get("name"),
                {
                    "source_attachment_count": len(attachments),
                    "extra_json": _set_oa_trace_in_extra(root, trace, is_root_trace),
                },
                update_modified=False,
            )

        synced_items.append(
            {
                "batch_name": row.get("name"),
                "batch_no": row.get("batch_no"),
                "attachment_count": len(attachments),
                "attachment_sync": sync_result,
            }
        )

    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "dry_run": False,
        "scanned_count": len(rows),
        "synced_count": len(synced_items),
        "skipped_count": len(skipped_items),
        "created_count": total_created,
        "updated_count": total_updated,
        "items": synced_items,
        "skipped_items": skipped_items,
        "message": "已有 OA 批次的表单和评论附件已登记。",
    }


def _resolve_batch_source_instance_id(row: dict, trace: dict) -> str:
    return (
        _clean(row.get("source_instance_id"))
        or _clean(trace.get("source_instance_id"))
        or extract_dingtalk_instance_id(row.get("source_dingtalk_url"))
        or extract_dingtalk_instance_id(trace.get("source_dingtalk_url") or trace.get("open_url"))
    )


def refresh_existing_oa_logistics_details(
    limit: int | None = 200,
    *,
    target: str = "",
    batch_name: str = "",
    batch_no: str = "",
    source_approval_no: str = "",
    env_file: str | None = None,
    api_style: str = "auto",
    include_non_sea: bool = False,
    access_token: str = "",
) -> dict:
    """重拉已有国际物流 OA 批次详情，并按关联采购支出 OA 自动补采购字段。

    旧批次如果只保存了国际物流基础字段，没有保存 form_fields / linked_purchase_approvals，
    单独执行 sync_existing_linked_purchase_fields 会因为缺少关联采购支出入口而跳过。
    这个函数会先按 source_instance_id 回到钉钉重拉审批详情，再复用 save_sea_approvals_to_erp
    更新批次追溯、附件入口、基础物料行和采购单价/币种/货值。
    """

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法刷新已有 OA 批次详情。",
        }

    resolved_env_file = resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    resolved_api_style = _resolve_api_style(api_style)
    token = get_access_token(
        api_style=resolved_api_style,
        access_token=access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )

    page_length = max(1, min(int(limit or 200), 1000))
    target_value = _clean(target)
    target_batch_name = _clean(batch_name)
    target_batch_no = _clean(batch_no)
    target_source_approval_no = _clean(source_approval_no)
    filters: dict[str, Any] = {"source_type": "oa_logistics"}
    or_filters: list[dict[str, str]] = []
    if target_value:
        or_filters.extend(
            [
                {"name": target_value},
                {"batch_no": target_value},
                {"source_approval_no": target_value},
            ]
        )
    elif target_batch_name:
        filters["name"] = target_batch_name
    else:
        if target_batch_no and target_source_approval_no:
            or_filters.extend(
                [
                    {"batch_no": target_batch_no},
                    {"source_approval_no": target_source_approval_no},
                ]
            )
        elif target_batch_no:
            filters["batch_no"] = target_batch_no
        elif target_source_approval_no:
            filters["source_approval_no"] = target_source_approval_no

    query_kwargs: dict[str, Any] = {
        "filters": filters,
        "fields": [
            "name",
            "batch_no",
            "waybill_no",
            "current_version",
            "source_approval_no",
            "source_instance_id",
            "source_dingtalk_url",
            "extra_json",
        ],
        "limit_page_length": page_length,
        "order_by": "modified desc",
    }
    if or_filters and not target_batch_name:
        query_kwargs["or_filters"] = or_filters
    rows = frappe.get_all("Overseas Cost Batch", **query_kwargs)

    refreshed_items: list[dict] = []
    skipped_items: list[dict] = []
    failed_items: list[dict] = []

    for row in rows:
        _root, trace, _is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        instance_id = _resolve_batch_source_instance_id(row, trace)
        if not instance_id:
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "reason": "缺少钉钉审批实例 ID，无法回到钉钉重拉详情",
                }
            )
            continue

        try:
            detail = get_process_instance_detail(
                token=token,
                process_instance_id=instance_id,
                api_style=resolved_api_style,
            )
            summary = summarize_approval(detail, process_instance_id=instance_id)
        except Exception as exc:
            failed_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_instance_id": instance_id,
                    "reason": str(exc),
                }
            )
            continue

        summary["source_approval_no"] = summary.get("source_approval_no") or row.get("source_approval_no") or trace.get("source_approval_no") or ""
        summary["source_dingtalk_url"] = summary.get("source_dingtalk_url") or row.get("source_dingtalk_url") or trace.get("source_dingtalk_url") or ""
        summary["logistics_no"] = summary.get("logistics_no") or row.get("waybill_no") or row.get("batch_no") or ""
        if is_hidden_approval_status(summary.get("approval_status"), summary.get("approval_result")):
            invalid_update = _update_oa_trace_batch(
                row.get("name"),
                build_batch_values_from_approval(summary),
            )
            _commit_oa_pull_progress()
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_instance_id": instance_id,
                    "approval_status": summary.get("approval_status"),
                    "invalid_update_action": invalid_update.get("action"),
                    "reason": "审批单已拒绝、撤销或终止，已更新批次状态并从当前工作台排除。",
                }
            )
            continue

        if not include_non_sea and not is_sea_approval(summary.get("form_fields") or {}):
            skipped_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "source_instance_id": instance_id,
                    "transport_mode_raw": summary.get("transport_mode_raw"),
                    "reason": "审批详情不是海运，未纳入本次刷新",
                }
            )
            continue

        refreshed_items.append(summary)

    save_result = save_sea_approvals_to_erp({"ok": True, "items": refreshed_items}) if refreshed_items else {
        "ok": True,
        "created_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "items": [],
        "skipped_items": [],
    }
    saved_items = save_result.get("items") or []
    purchase_updated_count = sum(int((item.get("purchase_sync") or {}).get("updated_count") or 0) for item in saved_items)
    purchase_changed_field_count = sum(int((item.get("purchase_sync") or {}).get("changed_field_count") or 0) for item in saved_items)
    linked_purchase_count = sum(int((item.get("purchase_sync") or {}).get("linked_purchase_count") or 0) for item in saved_items)
    attachment_count = sum(int((item.get("attachment_sync") or {}).get("attachment_count") or 0) for item in saved_items)

    return {
        "ok": bool(save_result.get("ok", True)) and not failed_items,
        "dry_run": False,
        "env_file_loaded": bool(resolved_env_file),
        "api_style": resolved_api_style,
        "target": {
            "target": target_value,
            "batch_name": target_batch_name,
            "batch_no": target_batch_no,
            "source_approval_no": target_source_approval_no,
        },
        "scanned_count": len(rows),
        "detail_count": len(refreshed_items),
        "saved_count": len(saved_items),
        "created_count": save_result.get("created_count", 0),
        "updated_count": save_result.get("updated_count", 0),
        "unchanged_count": save_result.get("unchanged_count", 0),
        "purchase_updated_count": purchase_updated_count,
        "purchase_changed_field_count": purchase_changed_field_count,
        "linked_purchase_count": linked_purchase_count,
        "attachment_count": attachment_count,
        "skipped_count": len(skipped_items) + int(save_result.get("skipped_count") or 0),
        "failed_count": len(failed_items),
        "items": saved_items,
        "skipped_items": skipped_items + (save_result.get("skipped_items") or []),
        "failed_items": failed_items,
        "message": (
            f"已重拉 {len(refreshed_items)} 条国际物流 OA 详情，并按关联采购支出 OA 同步采购字段；"
            f"采购字段更新 {purchase_updated_count} 行，变更 {purchase_changed_field_count} 个字段。"
        ),
    }


def refresh_oa_logistics_detail(
    target: str,
    limit: int | None = 50,
    *,
    env_file: str | None = None,
    api_style: str = "auto",
    include_non_sea: bool = False,
    access_token: str = "",
) -> dict:
    """Refresh one OA logistics batch by internal name, batch number, or approval number."""

    return refresh_existing_oa_logistics_details(
        limit=limit,
        target=target,
        env_file=env_file,
        api_style=api_style,
        include_non_sea=include_non_sea,
        access_token=access_token,
    )


def backfill_express_single_quote_freight_rules(limit: int | None = 500) -> dict:
    """为历史快递单补充“唯一明确物流报价”的运费分摊规则并重算。"""

    if frappe is None:
        return {"ok": False, "message": "当前未连接 Frappe。", "updated_count": 0, "skipped_count": 0}

    page_length = max(1, min(int(limit or 500), 5000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"transport_mode": "EXPRESS"},
        fields=["name", "batch_no", "current_version", "extra_json"],
        limit_page_length=page_length,
        order_by="modified desc",
    )
    updated_items: list[dict] = []
    skipped_items: list[dict] = []
    for row in rows:
        version_name = row.get("current_version") or _ensure_oa_trace_version(row["name"], row.get("batch_no") or row["name"])
        payload = _json_loads_dict(row.get("extra_json"))
        trace = payload.get("oa_logistics_trace") if isinstance(payload.get("oa_logistics_trace"), dict) else payload
        if not isinstance(trace, dict):
            skipped_items.append({"batch_name": row["name"], "reason": "缺少 OA 追溯数据"})
            continue
        candidates = trace.get("logistics_quote_candidates")
        if not isinstance(candidates, list):
            candidates = extract_logistics_quote_candidates_from_approval({"form_fields": trace.get("form_fields") or {}})
        valid_candidates = [candidate for candidate in candidates if _parse_money_amount((candidate or {}).get("amount")) is not None]
        if len(valid_candidates) != 1:
            skipped_items.append({"batch_name": row["name"], "reason": f"报价候选数量不是 1：{len(valid_candidates)}"})
            continue

        result = _sync_oa_logistics_allocation_rule(
            batch_name=row["name"],
            version_name=version_name,
            approval_item={
                "transport_mode": "EXPRESS",
                "logistics_quote_candidates": valid_candidates,
            },
        )
        recalculate_result = _recalculate_after_purchase_sync(
            batch_name=row["name"],
            version_name=version_name,
            purchase_sync={"ok": True, "updated_count": 0},
            logistics_fee_sync=result,
        )
        if result.get("action") in {"created", "updated"}:
            updated_items.append(
                {
                    "batch_name": row["name"],
                    "batch_no": row.get("batch_no"),
                    "rule_result": result,
                    "recalculate_result": recalculate_result,
                }
            )
        else:
            skipped_items.append({"batch_name": row["name"], "batch_no": row.get("batch_no"), "reason": result.get("reason") or result.get("action")})

    if updated_items and hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "scanned_count": len(rows),
        "updated_count": len(updated_items),
        "skipped_count": len(skipped_items),
        "updated_items": updated_items[:50],
        "skipped_items": skipped_items[:50],
    }


def detect_purchase_process_codes_from_existing_links(
    limit: int | None = 50,
    *,
    env_file: str | None = None,
    api_style: str = "auto",
    access_token: str = "",
) -> dict:
    """从已有国际物流批次的关联采购单里反查采购支出流程模板号。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法检测采购支出流程号。",
        }

    resolved_env_file = resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    resolved_api_style = _resolve_api_style(api_style)
    token = get_access_token(
        api_style=resolved_api_style,
        access_token=access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )

    page_length = max(1, min(int(limit or 50), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=["name", "batch_no", "extra_json"],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    detected: dict[str, dict] = {}
    skipped_items: list[dict] = []
    failed_items: list[dict] = []
    for row in rows:
        _root, trace, _is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        linked_approvals = trace.get("linked_purchase_approvals") or []
        if not linked_approvals:
            skipped_items.append({"batch_name": row.get("name"), "batch_no": row.get("batch_no"), "reason": "没有关联采购支出单"})
            continue
        for linked in linked_approvals:
            instance_id = _clean(linked.get("source_instance_id") or linked.get("proc_inst_id") or linked.get("instance_id"))
            if not instance_id:
                skipped_items.append(
                    {
                        "batch_name": row.get("name"),
                        "batch_no": row.get("batch_no"),
                        "approval_no": linked.get("approval_no") or linked.get("source_approval_no"),
                        "reason": "关联采购支出单缺少实例 ID",
                    }
                )
                continue
            try:
                detail = get_process_instance_detail(token=token, process_instance_id=instance_id, api_style=resolved_api_style)
            except Exception as exc:
                failed_items.append(
                    {
                        "batch_name": row.get("name"),
                        "batch_no": row.get("batch_no"),
                        "source_instance_id": instance_id,
                        "reason": str(exc),
                    }
                )
                continue
            process_code = _clean(detail.get("processCode") or detail.get("process_code") or detail.get("processCodeValue"))
            title = _clean(detail.get("title") or detail.get("processInstanceTitle") or detail.get("process_instance_title"))
            if not process_code:
                skipped_items.append(
                    {
                        "batch_name": row.get("name"),
                        "batch_no": row.get("batch_no"),
                        "source_instance_id": instance_id,
                        "approval_title": title,
                        "reason": "采购支出详情里没有返回 processCode",
                    }
                )
                continue
            record = detected.setdefault(
                process_code,
                {
                    "process_code": process_code,
                    "approval_title": title,
                    "sample_instance_id": instance_id,
                    "sample_approval_no": linked.get("approval_no") or linked.get("source_approval_no") or "",
                    "count": 0,
                },
            )
            record["count"] += 1

    return {
        "ok": not failed_items,
        "dry_run": False,
        "env_file_loaded": bool(resolved_env_file),
        "api_style": resolved_api_style,
        "scanned_count": len(rows),
        "detected_count": len(detected),
        "items": list(detected.values()),
        "skipped_count": len(skipped_items),
        "failed_count": len(failed_items),
        "skipped_items": skipped_items,
        "failed_items": failed_items,
        "message": f"已从已有国际物流关联采购单中检测到 {len(detected)} 个采购支出流程号。",
    }


def sync_existing_linked_purchase_fields(limit: int | None = 200) -> dict:
    """给已有国际物流 OA 批次补关联采购支出 OA 的采购字段。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法同步已有批次采购字段。",
        }

    page_length = max(1, min(int(limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=[
            "name",
            "batch_no",
            "current_version",
            "source_approval_no",
            "source_instance_id",
            "extra_json",
        ],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    synced_items: list[dict] = []
    skipped_items: list[dict] = []
    total_updated = 0
    total_changed_fields = 0

    for row in rows:
        _root, trace, _is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        linked_approvals = trace.get("linked_purchase_approvals") or []
        if not linked_approvals:
            skipped_items.append({"batch_name": row.get("name"), "batch_no": row.get("batch_no"), "reason": "未找到关联采购支出审批"})
            continue

        approval_item = {
            "source_approval_no": row.get("source_approval_no") or trace.get("source_approval_no") or "",
            "source_instance_id": row.get("source_instance_id") or trace.get("source_instance_id") or "",
            "linked_purchase_approvals": linked_approvals,
            "form_fields": trace.get("form_fields") or {},
        }
        purchase_sync = _sync_linked_purchase_fields(
            batch_name=row.get("name"),
            version_name=row.get("current_version") or "",
            approval_item=approval_item,
        )
        recalculate_sync = _recalculate_after_purchase_sync(
            batch_name=row.get("name"),
            version_name=row.get("current_version") or "",
            purchase_sync=purchase_sync,
        )
        total_updated += int(purchase_sync.get("updated_count") or 0)
        total_changed_fields += int(purchase_sync.get("changed_field_count") or 0)
        synced_items.append(
            {
                "batch_name": row.get("name"),
                "batch_no": row.get("batch_no"),
                "linked_purchase_count": len(linked_approvals),
                "purchase_sync": purchase_sync,
                "recalculate_sync": recalculate_sync,
            }
        )

    if hasattr(frappe.db, "commit"):
        frappe.db.commit()

    return {
        "ok": True,
        "dry_run": False,
        "scanned_count": len(rows),
        "synced_count": len(synced_items),
        "skipped_count": len(skipped_items),
        "updated_count": total_updated,
        "changed_field_count": total_changed_fields,
        "items": synced_items,
        "skipped_items": skipped_items,
        "message": "已有国际物流 OA 批次已按关联采购支出 OA 尝试同步采购单价、币种和货值。",
    }


def save_sea_approvals_to_erp(result: dict, *, recalculate_after_sync: bool = True) -> dict:
    """保存国际物流 OA，生成批次，并自动补关联采购支出 OA 的采购字段。

    国际物流 OA 负责批次头、物料基础行、附件记录和采购支出关联。
    采购支出 OA 负责补采购单价、采购币种、总货值。
    装箱单/凭证等附件后续继续补实际发货、重量、体积和税费。
    已有 SKU 明细不会被国际物流 OA 表单覆盖；采购支出 OA 有匹配结果时会直接写入采购字段。
    """

    raw_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    skipped_items: list[dict] = [
        {
            "reason": "审批单已撤销或终止，不进入成本表格",
            "source_approval_no": item.get("source_approval_no"),
            "source_instance_id": item.get("source_instance_id"),
            "approval_status": item.get("approval_status"),
        }
        for item in raw_items
        if is_hidden_approval_status(item.get("approval_status"), item.get("approval_result"))
    ]
    invalid_items = [
        item for item in raw_items
        if is_hidden_approval_status(item.get("approval_status"), item.get("approval_result"))
    ]
    items = [
        item for item in raw_items
        if not is_hidden_approval_status(item.get("approval_status"), item.get("approval_result"))
    ]
    preview = [build_batch_values_from_approval(item) for item in items]
    preview = [item for item in preview if item.get("batch_no")]
    if frappe is None:
        return {
            "ok": True,
            "dry_run": True,
            "message": "当前未连接 Frappe，仅返回钉钉国际物流审批批次追溯和基础物料行预览。",
            "total": len(raw_items),
            "valid_count": len(preview),
            "created_count": 0,
            "updated_count": 0,
            "unchanged_count": 0,
            "skipped_count": len(raw_items) - len(preview),
            "items": preview,
            "skipped_items": skipped_items,
        }

    created_count = 0
    updated_count = 0
    unchanged_count = 0
    saved_items: list[dict] = []
    failed_items: list[dict] = []
    for invalid_item in invalid_items:
        invalid_values = build_batch_values_from_approval(invalid_item)
        existing_name = _resolve_existing_batch_name(invalid_values)
        if not existing_name:
            continue
        invalid_update = _update_oa_trace_batch(existing_name, invalid_values)
        _commit_oa_pull_progress()
        for skipped in skipped_items:
            if (
                skipped.get("source_instance_id") == invalid_item.get("source_instance_id")
                or skipped.get("source_approval_no") == invalid_item.get("source_approval_no")
            ):
                skipped["batch_name"] = existing_name
                skipped["invalid_update_action"] = invalid_update.get("action")
                skipped["reason"] = "审批单已拒绝、撤销或终止，已更新历史批次状态并从当前工作台排除。"
                break
    for item in items:
        values = build_batch_values_from_approval(item)
        if not values.get("batch_no"):
            skipped_items.append({"reason": "缺少批次号、审批编号和实例ID", "source_instance_id": item.get("source_instance_id")})
            continue
        def save_one_approval():
            existing_name = _resolve_existing_batch_name(values)
            if existing_name:
                saved_row = _update_oa_trace_batch(existing_name, values)
            else:
                saved_row = _create_oa_trace_batch(values)

            item_sync = _sync_oa_goods_items(
                batch_name=saved_row["batch_name"],
                version_name=saved_row.get("version_name") or "",
                approval_item=item,
                only_when_empty=True,
            )
            attachment_sync = _sync_oa_form_attachments(
                batch_name=saved_row["batch_name"],
                version_name=saved_row.get("version_name") or "",
                approval_item=item,
            )
            purchase_sync = _sync_linked_purchase_fields(
                batch_name=saved_row["batch_name"],
                version_name=saved_row.get("version_name") or "",
                approval_item=item,
            )
            logistics_fee_sync = _sync_oa_logistics_allocation_rules(
                batch_name=saved_row["batch_name"],
                version_name=saved_row.get("version_name") or "",
                approval_item=item,
                include_new_optional_fees=saved_row.get("action") == "created",
            )
            if recalculate_after_sync:
                recalculate_sync = _recalculate_after_purchase_sync(
                    batch_name=saved_row["batch_name"],
                    version_name=saved_row.get("version_name") or "",
                    purchase_sync=purchase_sync,
                    logistics_fee_sync=logistics_fee_sync,
                )
            else:
                purchase_changed = bool(
                    purchase_sync.get("ok")
                    and int(purchase_sync.get("updated_count") or 0) > 0
                )
                fee_changed = bool(
                    logistics_fee_sync.get("ok")
                    and logistics_fee_sync.get("action") in {"created", "updated"}
                )
                if purchase_changed or fee_changed:
                    from overseas_costing.services.import_service import _mark_batch_dirty

                    _mark_batch_dirty(saved_row["batch_name"])
                    recalculate_sync = {
                        "action": "marked_stale",
                        "ok": True,
                        "message": "补同步导致计算输入变化，已进入待重新试算，未自动计算。",
                    }
                else:
                    recalculate_sync = {
                        "action": "skipped",
                        "ok": True,
                        "reason": "补同步未改变采购事实或物流费用。",
                    }
            _commit_oa_pull_progress()
            return saved_row, item_sync, attachment_sync, purchase_sync, logistics_fee_sync, recalculate_sync

        saved, item_sync, attachment_sync, purchase_sync, logistics_fee_sync, recalculate_sync = _run_with_database_retry(save_one_approval)

        if saved["action"] == "created":
            created_count += 1
        elif saved["action"] == "updated":
            updated_count += 1
        else:
            unchanged_count += 1
        saved.update(
            {
                "batch_no": values.get("batch_no"),
                "source_approval_no": values.get("source_approval_no"),
                "source_instance_id": values.get("source_instance_id"),
                "logistics_no": values.get("waybill_no"),
                "item_sync": item_sync,
                "attachment_sync": attachment_sync,
                "purchase_sync": purchase_sync,
                "logistics_fee_sync": logistics_fee_sync,
                "recalculate_sync": recalculate_sync,
            }
        )
        saved_items.append(saved)
        failed_stages = [
            stage
            for stage, stage_result in (
                ("item_sync", item_sync),
                ("attachment_sync", attachment_sync),
                ("purchase_sync", purchase_sync),
                ("logistics_fee_sync", logistics_fee_sync),
                ("recalculate_sync", recalculate_sync),
            )
            if isinstance(stage_result, dict) and stage_result.get("ok") is False
        ]
        if failed_stages:
            failed_items.append(
                {
                    "batch_name": saved.get("batch_name"),
                    "source_instance_id": values.get("source_instance_id"),
                    "failed_stages": failed_stages,
                    "message": "；".join(
                        str(saved.get(stage, {}).get("message") or stage)
                        for stage in failed_stages
                    ),
                }
            )

    _commit_oa_pull_progress()

    return {
        "ok": not failed_items,
        "dry_run": False,
        "message": "钉钉国际物流审批已保存到批次；已生成/保留物料行、登记发起附件，并按关联采购支出 OA 同步采购单价、币种和货值，同时把明确的物流费用生成分摊规则。",
        "total": len(raw_items),
        "created_count": created_count,
        "updated_count": updated_count,
        "unchanged_count": unchanged_count,
        "skipped_count": len(skipped_items),
        "failed_count": len(failed_items),
        "items": saved_items,
        "skipped_items": skipped_items,
        "failed_items": failed_items,
    }


def save_json_file_to_erp(input_path: str | Path) -> dict:
    path = Path(input_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(_("未找到钉钉拉取结果文件：{0}").format(path))
    return save_sea_approvals_to_erp(json.loads(path.read_text(encoding="utf-8")))


def save_json_file_to_erp_from_env() -> dict:
    input_path = _clean(os.environ.get("DINGTALK_PULL_INPUT")) or _clean(os.environ.get("DINGTALK_PULL_OUTPUT"))
    if not input_path:
        raise ValueError(_("请设置 DINGTALK_PULL_INPUT 或 DINGTALK_PULL_OUTPUT。"))
    return save_json_file_to_erp(input_path)


def pull_logistics_approvals(
    *,
    process_code: str,
    start: str,
    end: str,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
    limit: int | None = None,
    include_raw: bool = False,
    include_all: bool = False,
    access_token: str = "",
    corp_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    app_key: str = "",
    app_secret: str = "",
    transport_modes: tuple[str, ...] | list[str] | str = ("SEA",),
) -> dict:
    """拉取并按运输方式筛选国际物流审批单，不写数据库。"""

    source_mode = resolve_approval_source_mode()
    resolved_api_style = _resolve_api_style(api_style)
    resolved_list_api = _resolve_list_api_mode(list_api, resolved_api_style)
    resolved_transport_modes = _parse_transport_modes(transport_modes)
    start_time_ms = _parse_datetime_ms(start)
    end_time_ms = _parse_datetime_ms(end, end_of_day=True)
    source_metadata = {
        "data_source": source_mode,
        "source_updated_at": None,
        "source_lag_seconds": None,
        "fallback_used": False,
    }
    details_by_id: dict[str, dict] = {}

    if source_mode == "postgres":
        database_result = _get_postgres_approval_source().list_instances(
            process_code=process_code,
            start=start,
            end=end,
            limit=limit or max(1, page_size * max_pages),
        )
        details = database_result.get("items") or []
        instance_ids = [
            _clean(detail.get("processInstanceId") or detail.get("process_instance_id"))
            for detail in details
        ]
        instance_ids = [value for value in instance_ids if value]
        details_by_id = dict(zip(instance_ids, details))
        source_metadata.update(
            {
                "source_updated_at": _serialize_source_updated_at(database_result.get("source_updated_at")),
                "source_lag_seconds": database_result.get("source_lag_seconds"),
            }
        )
    else:
        token = get_access_token(
            api_style=resolved_api_style,
            access_token=access_token,
            corp_id=corp_id,
            client_id=client_id,
            client_secret=client_secret,
            app_key=app_key,
            app_secret=app_secret,
        )
        instance_ids = list_process_instance_ids(
            token=token,
            process_code=process_code,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            api_style=resolved_api_style,
            list_api=resolved_list_api,
            page_size=page_size,
            max_pages=max_pages,
            chunk_days=chunk_days,
        )
        if limit:
            instance_ids = instance_ids[:limit]

    all_items: list[dict] = []
    matched_items: list[dict] = []
    for instance_id in instance_ids:
        detail = details_by_id.get(instance_id) if source_mode == "postgres" else get_process_instance_detail(
            token=token,
            process_instance_id=instance_id,
            api_style=resolved_api_style,
        )
        summary = summarize_approval(detail, process_instance_id=instance_id, include_raw=include_raw)
        all_items.append(summary)
        if is_transport_approval(summary["form_fields"], resolved_transport_modes) and not is_hidden_approval_status(summary.get("approval_status")):
            matched_items.append(summary)

    transport_counts = {
        mode: sum(
            1
            for item in all_items
            if not is_hidden_approval_status(item.get("approval_status")) and item.get("transport_mode") == mode
        )
        for mode in TRANSPORT_MODE_LABELS
    }

    result = {
        "ok": True,
        "process_code": process_code,
        "api_style": resolved_api_style,
        "list_api": resolved_list_api,
        "transport_modes": list(resolved_transport_modes),
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "chunk_days": chunk_days,
        "total_instance_count": len(instance_ids),
        "detail_count": len(all_items),
        "transport_counts": transport_counts,
        "filtered_count": len(matched_items),
        "sea_count": transport_counts.get("SEA", 0),
        "items": matched_items,
        **source_metadata,
    }
    if include_all:
        result["all_items"] = all_items
        result["non_matching_items"] = [item for item in all_items if item not in matched_items]
        result["non_sea_items"] = [item for item in all_items if item.get("transport_mode") != "SEA"]
    return result


def pull_sea_approvals(
    *,
    process_code: str,
    start: str,
    end: str,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
    limit: int | None = None,
    include_raw: bool = False,
    include_all: bool = False,
    access_token: str = "",
    corp_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    app_key: str = "",
    app_secret: str = "",
) -> dict:
    """拉取并筛选海运审批单，不写数据库。"""

    return pull_logistics_approvals(
        process_code=process_code,
        start=start,
        end=end,
        api_style=api_style,
        list_api=list_api,
        page_size=page_size,
        max_pages=max_pages,
        chunk_days=chunk_days,
        limit=limit,
        include_raw=include_raw,
        include_all=include_all,
        access_token=access_token,
        corp_id=corp_id,
        client_id=client_id,
        client_secret=client_secret,
        app_key=app_key,
        app_secret=app_secret,
        transport_modes=("SEA",),
    )


def pull_purchase_expense_approvals(
    *,
    process_code: str,
    start: str,
    end: str,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
    limit: int | None = None,
    include_raw: bool = False,
    include_running: bool = False,
    access_token: str = "",
    corp_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    app_key: str = "",
    app_secret: str = "",
) -> dict:
    """按采购支出流程批量拉取审批详情，并解析行级单价、币种、总金额。"""

    source_mode = resolve_approval_source_mode()
    resolved_process_code = resolve_purchase_process_code(process_code)
    if not resolved_process_code:
        raise ValueError(_("缺少采购支出流程模板 process_code，请配置 DINGTALK_PURCHASE_PROCESS_CODE。"))

    resolved_api_style = _resolve_api_style(api_style)
    resolved_list_api = _resolve_list_api_mode(list_api, resolved_api_style)
    start_time_ms = _parse_datetime_ms(start)
    end_time_ms = _parse_datetime_ms(end, end_of_day=True)
    source_metadata = {
        "data_source": source_mode,
        "source_updated_at": None,
        "source_lag_seconds": None,
        "fallback_used": False,
    }
    details_by_id: dict[str, dict] = {}
    if source_mode == "postgres":
        database_result = _get_postgres_approval_source().list_instances(
            process_code=resolved_process_code,
            start=start,
            end=end,
            limit=limit or max(1, page_size * max_pages),
        )
        details = database_result.get("items") or []
        instance_ids = [
            _clean(detail.get("processInstanceId") or detail.get("process_instance_id"))
            for detail in details
        ]
        instance_ids = [value for value in instance_ids if value]
        details_by_id = dict(zip(instance_ids, details))
        source_metadata.update(
            {
                "source_updated_at": _serialize_source_updated_at(database_result.get("source_updated_at")),
                "source_lag_seconds": database_result.get("source_lag_seconds"),
            }
        )
    else:
        token = get_access_token(
            api_style=resolved_api_style,
            access_token=access_token,
            corp_id=corp_id,
            client_id=client_id,
            client_secret=client_secret,
            app_key=app_key,
            app_secret=app_secret,
        )
        instance_ids = list_process_instance_ids(
            token=token,
            process_code=resolved_process_code,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            api_style=resolved_api_style,
            list_api=resolved_list_api,
            page_size=page_size,
            max_pages=max_pages,
            chunk_days=chunk_days,
        )
        if limit:
            instance_ids = instance_ids[:limit]

    items: list[dict] = []
    skipped_items: list[dict] = []
    for instance_id in instance_ids:
        detail = details_by_id.get(instance_id) if source_mode == "postgres" else get_process_instance_detail(
            token=token,
            process_instance_id=instance_id,
            api_style=resolved_api_style,
        )
        summary = summarize_purchase_approval(detail, process_instance_id=instance_id)
        if include_raw:
            summary["raw_instance"] = detail
        if is_hidden_approval_status(summary.get("approval_status")):
            skipped_items.append(
                {
                    "source_instance_id": summary.get("source_instance_id"),
                    "source_approval_no": summary.get("source_approval_no"),
                    "approval_status": summary.get("approval_status"),
                    "reason": "采购支出审批已撤销或终止",
                }
            )
            continue
        if not include_running and not is_completed_approval_status(summary.get("approval_status"), allow_empty=False):
            skipped_items.append(
                {
                    "source_instance_id": summary.get("source_instance_id"),
                    "source_approval_no": summary.get("source_approval_no"),
                    "approval_status": summary.get("approval_status"),
                    "approval_title": summary.get("approval_title"),
                    "reason": "采购支出审批未完成",
                }
            )
            continue
        if not summary.get("detail_row_count") and not _has_purchase_approval_keyword(summary.get("approval_title")):
            skipped_items.append(
                {
                    "source_instance_id": summary.get("source_instance_id"),
                    "source_approval_no": summary.get("source_approval_no"),
                    "approval_title": summary.get("approval_title"),
                    "reason": "未解析到采购支出明细行",
                }
            )
            continue
        summary["ok"] = True
        items.append(summary)

    return {
        "ok": True,
        "process_code": resolved_process_code,
        "api_style": resolved_api_style,
        "list_api": resolved_list_api,
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "chunk_days": chunk_days,
        "total_instance_count": len(instance_ids),
        "detail_count": len(items),
        "skipped_count": len(skipped_items),
        "items": items,
        "skipped_items": skipped_items,
        **source_metadata,
    }


def sync_purchase_expenses_from_process(
    *,
    process_code: str = "",
    start: str = "",
    end: str = "",
    env_file: str | None = None,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
    limit: int | None = None,
    batch_limit: int | None = 200,
    include_running: bool = False,
    access_token: str = "",
) -> dict:
    """从采购支出流程批量拉审批，并按物料编码/规格同步已有 OA 批次采购字段。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法批量同步采购支出 OA。",
        }

    resolved_env_file = resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    resolved_process_code = resolve_purchase_process_code(process_code)
    if not resolved_process_code:
        return {
            "ok": False,
            "dry_run": False,
            "env_file_loaded": bool(resolved_env_file),
            "message": "缺少采购支出流程模板 process_code，请在环境变量配置 DINGTALK_PURCHASE_PROCESS_CODE 后再自动拉取。",
        }

    pull_result = pull_purchase_expense_approvals(
        process_code=resolved_process_code,
        start=start,
        end=end,
        api_style=_runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default=api_style),
        list_api=_runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default=list_api),
        page_size=page_size,
        max_pages=max_pages,
        chunk_days=chunk_days,
        limit=limit,
        include_running=include_running,
        access_token=access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )
    purchase_summaries = pull_result.get("items") or []
    if not purchase_summaries:
        return {
            "ok": True,
            "dry_run": False,
            "env_file_loaded": bool(resolved_env_file),
            "pull": pull_result,
            "scanned_count": 0,
            "updated_count": 0,
            "changed_field_count": 0,
            "message": "本次没有拉到可同步的采购支出 OA 明细。",
        }

    page_length = max(1, min(int(batch_limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=["name", "batch_no", "current_version", "extra_json"],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    from overseas_costing.services import import_service

    purchase_summaries_json = _json_dumps(purchase_summaries)
    synced_items: list[dict] = []
    total_updated = 0
    total_changed_fields = 0
    total_unmatched = 0
    total_ambiguous = 0
    failed_items: list[dict] = []
    for row in rows:
        _root, trace, _is_root_trace = _get_oa_trace_from_extra(row.get("extra_json"))
        if _has_logistics_item_structure(
            batch_name=row.get("name"), version_name=row.get("current_version") or "", approval_item=trace,
        ):
            purchase_sync = _logistics_purchase_preview_result(import_service.preview_linked_purchase_expense_oa(
                batch_name=row.get("name"), version_name=row.get("current_version") or "",
                purchase_summaries_json=purchase_summaries_json, trusted_server_payload=True,
            ))
        else:
            purchase_sync = import_service.apply_linked_purchase_expense_fillable_fields(
                batch_name=row.get("name"),
                version_name=row.get("current_version") or "",
                purchase_summaries_json=purchase_summaries_json,
                recalculate_after_writeback=False,
                trusted_server_payload=True,
            )
        recalculate_sync = _recalculate_after_purchase_sync(
            batch_name=row.get("name"),
            version_name=row.get("current_version") or "",
            purchase_sync=purchase_sync,
        )
        total_updated += int(purchase_sync.get("updated_count") or 0)
        total_changed_fields += int(purchase_sync.get("changed_field_count") or 0)
        total_unmatched += int(purchase_sync.get("unmatched_count") or 0)
        total_ambiguous += int(purchase_sync.get("ambiguous_count") or 0)
        synced_items.append(
            {
                "batch_name": row.get("name"),
                "batch_no": row.get("batch_no"),
                "purchase_sync": purchase_sync,
                "recalculate_sync": recalculate_sync,
            }
        )
        if purchase_sync.get("ok") is False or recalculate_sync.get("ok") is False:
            failed_items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "purchase_sync": purchase_sync,
                    "recalculate_sync": recalculate_sync,
                }
            )

    return {
        "ok": not failed_items,
        "dry_run": False,
        "env_file_loaded": bool(resolved_env_file),
        "pull": {
            **pull_result,
            "items": [
                {
                    "source_approval_no": item.get("source_approval_no"),
                    "source_instance_id": item.get("source_instance_id"),
                    "approval_title": item.get("approval_title"),
                    "detail_row_count": item.get("detail_row_count"),
                    "purchase_currency": item.get("purchase_currency"),
                }
                for item in purchase_summaries[:20]
            ],
        },
        "scanned_count": len(rows),
        "updated_count": total_updated,
        "changed_field_count": total_changed_fields,
        "unmatched_count": total_unmatched,
        "ambiguous_count": total_ambiguous,
        "failed_count": len(failed_items),
        "items": synced_items,
        "failed_items": failed_items,
        "message": (
            f"已拉取 {len(purchase_summaries)} 张采购支出 OA，并尝试同步 {len(rows)} 个国际物流批次；"
            f"采购字段更新 {total_updated} 行，变更 {total_changed_fields} 个字段。"
        ),
    }


def preview_purchase_expenses_from_process(
    *,
    process_code: str = "",
    start: str = "",
    end: str = "",
    env_file: str | None = None,
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int = 20,
    max_pages: int = 20,
    chunk_days: int = 30,
    limit: int | None = None,
    batch_limit: int | None = 200,
    include_running: bool = False,
    access_token: str = "",
) -> dict:
    """从采购支出流程拉明细，并预览可匹配到哪些已有 OA 批次；不写数据库。"""

    if frappe is None:
        return {
            "ok": False,
            "dry_run": True,
            "message": "当前未连接 Frappe，无法预览采购支出 OA 批量匹配。",
        }

    resolved_env_file = resolve_dingtalk_env_file(env_file)
    if resolved_env_file:
        load_env_file(resolved_env_file)
    resolved_process_code = resolve_purchase_process_code(process_code)
    if not resolved_process_code:
        return {
            "ok": False,
            "dry_run": False,
            "env_file_loaded": bool(resolved_env_file),
            "message": "缺少采购支出流程模板 process_code，请在环境变量配置 DINGTALK_PURCHASE_PROCESS_CODE 后再预览。",
        }

    pull_result = pull_purchase_expense_approvals(
        process_code=resolved_process_code,
        start=start,
        end=end,
        api_style=_runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default=api_style),
        list_api=_runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default=list_api),
        page_size=page_size,
        max_pages=max_pages,
        chunk_days=chunk_days,
        limit=limit,
        include_running=include_running,
        access_token=access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )
    purchase_summaries = pull_result.get("items") or []
    purchase_rows_preview: list[dict] = []
    for summary in purchase_summaries:
        source_approval_no = summary.get("source_approval_no") or ""
        source_instance_id = summary.get("source_instance_id") or ""
        source_dingtalk_url = summary.get("source_dingtalk_url") or ""
        for mapped_row in summary.get("mapped_preview_items") or []:
            if not isinstance(mapped_row, dict):
                continue
            purchase_rows_preview.append(
                {
                    "material_code": mapped_row.get("material_code"),
                    "product_name": mapped_row.get("product_name"),
                    "spec_model": mapped_row.get("spec_model"),
                    "quantity": mapped_row.get("quantity"),
                    "unit_price": mapped_row.get("unit_price"),
                    "goods_value": mapped_row.get("goods_value"),
                    "purchase_currency": mapped_row.get("purchase_currency") or summary.get("purchase_currency"),
                    "source_approval_no": mapped_row.get("source_approval_no") or source_approval_no,
                    "source_instance_id": mapped_row.get("source_instance_id") or source_instance_id,
                    "source_dingtalk_url": mapped_row.get("source_dingtalk_url") or source_dingtalk_url,
                }
            )
    if not purchase_summaries:
        return {
            "ok": True,
            "dry_run": False,
            "env_file_loaded": bool(resolved_env_file),
            "pull": {**pull_result, "items": []},
            "scanned_count": 0,
            "matched_batch_count": 0,
            "writable_batch_count": 0,
            "mapped_purchase_row_count": 0,
            "mapped_purchase_rows": [],
            "message": "本次没有拉到可预览的采购支出 OA 明细。",
        }

    page_length = max(1, min(int(batch_limit or 200), 1000))
    rows = frappe.get_all(
        "Overseas Cost Batch",
        filters={"source_type": "oa_logistics"},
        fields=["name", "batch_no", "current_version"],
        limit_page_length=page_length,
        order_by="modified desc",
    )

    from overseas_costing.services import import_service

    purchase_summaries_json = _json_dumps(purchase_summaries)
    items: list[dict] = []
    matched_batch_count = 0
    writable_batch_count = 0
    total_matched_rows = 0
    total_writable_rows = 0
    total_unmatched_rows = 0
    total_ambiguous_rows = 0

    for row in rows:
        preview = import_service.preview_linked_purchase_expense_oa(
            batch_name=row.get("name"),
            version_name=row.get("current_version") or "",
            purchase_summaries_json=purchase_summaries_json,
            trusted_server_payload=True,
        )
        writeback = preview.get("writeback_preview") or {}
        matched_count = int(writeback.get("matched_count") or 0)
        writable_count = int(writeback.get("writable_row_count") or 0)
        unmatched_count = int(writeback.get("unmatched_count") or 0)
        ambiguous_count = int(writeback.get("ambiguous_count") or 0)
        if matched_count:
            matched_batch_count += 1
        if writable_count:
            writable_batch_count += 1
        total_matched_rows += matched_count
        total_writable_rows += writable_count
        total_unmatched_rows += unmatched_count
        total_ambiguous_rows += ambiguous_count
        if matched_count or writable_count:
            items.append(
                {
                    "batch_name": row.get("name"),
                    "batch_no": row.get("batch_no"),
                    "matched_count": matched_count,
                    "writable_row_count": writable_count,
                    "fillable_row_count": writeback.get("fillable_row_count", 0),
                    "conflict_row_count": writeback.get("conflict_row_count", 0),
                    "same_row_count": writeback.get("same_row_count", 0),
                    "unmatched_count": unmatched_count,
                    "ambiguous_count": ambiguous_count,
                    "matched_rows": [
                        {
                            "target_row_no": matched.get("target_row_no"),
                            "target_material_code": matched.get("target_material_code"),
                            "target_product_name": matched.get("target_product_name"),
                            "target_spec_model": matched.get("target_spec_model"),
                            "mapped_row": matched.get("mapped_row"),
                            "business_changes": matched.get("business_changes"),
                        }
                        for matched in (writeback.get("matched_rows") or [])[:10]
                    ],
                }
            )

    return {
        "ok": True,
        "dry_run": False,
        "env_file_loaded": bool(resolved_env_file),
        "pull": {
            **pull_result,
            "items": [
                _build_purchase_process_preview_row(item)
                for item in purchase_summaries[:20]
            ],
        },
        "scanned_count": len(rows),
        "matched_batch_count": matched_batch_count,
        "writable_batch_count": writable_batch_count,
        "purchase_summary_count": len(purchase_summaries),
        "mapped_purchase_row_count": len(purchase_rows_preview),
        "mapped_purchase_rows": purchase_rows_preview[:50],
        "matched_row_count": total_matched_rows,
        "writable_row_count": total_writable_rows,
        "unmatched_row_count": total_unmatched_rows,
        "ambiguous_row_count": total_ambiguous_rows,
        "items": items,
        "message": (
            f"已拉取 {len(purchase_summaries)} 张已完成采购支出 OA，预览匹配 {len(rows)} 个国际物流批次；"
            f"命中 {matched_batch_count} 个批次，{total_writable_rows} 行可写入。"
        ),
    }


def _build_purchase_process_preview_row(item: dict) -> dict:
    approval_no = item.get("source_approval_no") or ""
    instance_id = item.get("source_instance_id") or ""
    official_url = item.get("source_dingtalk_url") or ""
    dingtalk_payload = build_dingtalk_order_payload(
        approval_no=approval_no,
        instance_id=instance_id,
        official_url=official_url,
    )
    return {
        "source_approval_no": approval_no,
        "source_instance_id": instance_id,
        "source_dingtalk_url": official_url,
        "approval_title": item.get("approval_title"),
        "approval_status": item.get("approval_status"),
        "detail_row_count": item.get("detail_row_count"),
        "purchase_currency": item.get("purchase_currency"),
        "open_url": dingtalk_payload.get("open_url") or "",
        "open_mode": dingtalk_payload.get("open_mode") or "unavailable",
        "can_open": bool(dingtalk_payload.get("can_open")),
    }


def save_json(result: dict, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def save_csv(items: list[dict], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_instance_id",
        "source_approval_no",
        "source_dingtalk_url",
        "open_url",
        "approval_title",
        "approval_status",
        "originator_userid",
        "create_time",
        "finish_time",
        "transport_mode",
        "transport_mode_raw",
        "logistics_no",
        "linked_purchase_count",
        "oa_form_attachment_count",
        "linked_purchase_approval_nos",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            row = {fieldname: item.get(fieldname, "") for fieldname in fieldnames}
            linked_approvals = item.get("linked_purchase_approvals") or []
            row["linked_purchase_approval_nos"] = "；".join(
                _clean(linked.get("approval_no") or linked.get("source_approval_no"))
                for linked in linked_approvals
                if isinstance(linked, dict) and _clean(linked.get("approval_no") or linked.get("source_approval_no"))
            )
            writer.writerow(row)


def build_console_summary(result: dict, *, output: str = "", csv_output: str = "") -> dict:
    return {
        "ok": result.get("ok"),
        "process_code": result.get("process_code"),
        "api_style": result.get("api_style"),
        "list_api": result.get("list_api"),
        "chunk_days": result.get("chunk_days"),
        "total_instance_count": result.get("total_instance_count"),
        "detail_count": result.get("detail_count"),
        "transport_modes": result.get("transport_modes"),
        "transport_counts": result.get("transport_counts"),
        "filtered_count": result.get("filtered_count"),
        "sea_count": result.get("sea_count"),
        "output": output,
        "csv": csv_output,
        "preview": [
            {
                "source_approval_no": item.get("source_approval_no"),
                "source_instance_id": item.get("source_instance_id"),
                "transport_mode": item.get("transport_mode"),
                "transport_mode_raw": item.get("transport_mode_raw"),
                "logistics_no": item.get("logistics_no"),
                "approval_status": item.get("approval_status"),
                "linked_purchase_count": item.get("linked_purchase_count"),
                "oa_form_attachment_count": item.get("oa_form_attachment_count"),
            }
            for item in (result.get("items") or [])[:5]
        ],
    }


def pull_from_env() -> dict:
    """从环境变量读取参数，适合 bench execute 调试。"""

    load_env_file(os.environ.get("DINGTALK_ENV_FILE"))
    result = pull_logistics_approvals(
        process_code=resolve_logistics_process_code(),
        start=_clean(os.environ.get("DINGTALK_PULL_START")),
        end=_clean(os.environ.get("DINGTALK_PULL_END")),
        api_style=_clean(os.environ.get("DINGTALK_API_STYLE")) or "auto",
        list_api=_clean(os.environ.get("DINGTALK_LIST_API")) or "auto",
        page_size=int(os.environ.get("DINGTALK_PAGE_SIZE") or 20),
        max_pages=int(os.environ.get("DINGTALK_MAX_PAGES") or 20),
        chunk_days=int(os.environ.get("DINGTALK_CHUNK_DAYS") or 30),
        limit=int(os.environ.get("DINGTALK_LIMIT") or 0) or None,
        include_raw=os.environ.get("DINGTALK_INCLUDE_RAW") in ("1", "true", "True", "yes"),
        include_all=os.environ.get("DINGTALK_INCLUDE_ALL") in ("1", "true", "True", "yes"),
        transport_modes=os.environ.get("DINGTALK_TRANSPORT_MODES") or os.environ.get("DINGTALK_TRANSPORT_MODE") or "SEA",
    )
    output = _clean(os.environ.get("DINGTALK_PULL_OUTPUT"))
    csv_output = _clean(os.environ.get("DINGTALK_PULL_CSV"))
    if output:
        save_json(result, output)
    if csv_output:
        save_csv(result["items"], csv_output)
    return result


def diagnose_business_entity_from_env() -> dict:
    """只拉取钉钉审批详情并诊断业务主体字段，不写入数据库。"""

    resolved_env_file = resolve_dingtalk_env_file(os.environ.get("DINGTALK_ENV_FILE"))
    if resolved_env_file:
        load_env_file(resolved_env_file)
    result = pull_logistics_approvals(
        process_code=resolve_logistics_process_code(),
        start=_clean(os.environ.get("DINGTALK_PULL_START")),
        end=_clean(os.environ.get("DINGTALK_PULL_END")),
        api_style=_clean(os.environ.get("DINGTALK_API_STYLE")) or "auto",
        list_api=_clean(os.environ.get("DINGTALK_LIST_API")) or "auto",
        page_size=int(os.environ.get("DINGTALK_PAGE_SIZE") or 20),
        max_pages=int(os.environ.get("DINGTALK_MAX_PAGES") or 20),
        chunk_days=int(os.environ.get("DINGTALK_CHUNK_DAYS") or 30),
        limit=int(os.environ.get("DINGTALK_LIMIT") or 5) or 5,
        include_raw=True,
        include_all=True,
        transport_modes=os.environ.get("DINGTALK_TRANSPORT_MODES") or os.environ.get("DINGTALK_TRANSPORT_MODE") or "ALL",
    )
    items = result.get("items") or []
    diagnosed_items = []
    for item in items[:10]:
        subsidiary = extract_subsidiary_from_approval(item)
        candidates = _collect_business_entity_debug_candidates(item)
        diagnosed_items.append(
            {
                "source_instance_id": item.get("source_instance_id") or "",
                "source_approval_no": item.get("source_approval_no") or "",
                "approval_title": item.get("approval_title") or "",
                "transport_mode": item.get("transport_mode") or "",
                "subsidiary_code": subsidiary.get("subsidiary_code") or "",
                "business_entity_name": subsidiary.get("business_entity_name") or "",
                "business_entity_id": subsidiary.get("business_entity_id") or "",
                "source_field": subsidiary.get("source_field") or "",
                "business_entity_candidates": candidates,
                "form_field_keys": sorted((item.get("form_fields") or {}).keys())[:80],
                "raw_form_component_count": len(item.get("raw_form_components") or []),
            }
        )

    first = next(
        (item for item in diagnosed_items if item.get("subsidiary_code") or item.get("business_entity_candidates")),
        diagnosed_items[0] if diagnosed_items else {},
    )
    return {
        "ok": bool(result.get("ok")),
        "env_file_loaded": bool(resolved_env_file),
        "total_instance_count": result.get("total_instance_count", 0),
        "detail_count": result.get("detail_count", 0),
        "filtered_count": result.get("filtered_count", 0),
        "diagnosed_count": len(diagnosed_items),
        "diagnosed_items": diagnosed_items,
        "first_item": {
            "source_instance_id": first.get("source_instance_id") or "",
            "source_approval_no": first.get("source_approval_no") or "",
            "approval_title": first.get("approval_title") or "",
            "transport_mode": first.get("transport_mode") or "",
            "subsidiary_code": first.get("subsidiary_code") or "",
            "business_entity_name": first.get("business_entity_name") or "",
            "business_entity_id": first.get("business_entity_id") or "",
            "source_field": first.get("source_field") or "",
            "form_field_keys": first.get("form_field_keys") or [],
            "business_entity_candidates": first.get("business_entity_candidates") or [],
            "raw_form_component_count": first.get("raw_form_component_count") or 0,
        },
    }


def pull_purchase_expenses_from_env() -> dict:
    """从环境变量拉采购支出 OA 预览，不写数据库，适合 bench execute 调试。"""

    resolved_env_file = resolve_dingtalk_env_file()
    if resolved_env_file:
        load_env_file(resolved_env_file)
    start = (
        _clean(os.environ.get("DINGTALK_PURCHASE_PULL_START"))
        or _clean(os.environ.get("DINGTALK_PULL_START"))
        or "2026-01-01"
    )
    end = (
        _clean(os.environ.get("DINGTALK_PURCHASE_PULL_END"))
        or _clean(os.environ.get("DINGTALK_PULL_END"))
        or datetime.now().strftime("%Y-%m-%d")
    )
    limit = int(os.environ.get("DINGTALK_PURCHASE_PULL_LIMIT") or os.environ.get("DINGTALK_LIMIT") or 5)
    include_running = os.environ.get("DINGTALK_PURCHASE_INCLUDE_RUNNING") in ("1", "true", "True", "yes")
    result = pull_purchase_expense_approvals(
        process_code=resolve_purchase_process_code(),
        start=start,
        end=end,
        api_style=_runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default="auto"),
        list_api=_runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default="auto"),
        page_size=_runtime_config_int("DINGTALK_PAGE_SIZE", default=20),
        max_pages=_runtime_config_int("DINGTALK_MAX_PAGES", default=20),
        chunk_days=_runtime_config_int("DINGTALK_CHUNK_DAYS", default=30),
        limit=limit or None,
        include_running=include_running,
        access_token=_runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token"),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
    )
    return {
        **result,
        "env_file_loaded": bool(resolved_env_file),
        "preview_items": [
            {
                "source_approval_no": item.get("source_approval_no"),
                "source_instance_id": item.get("source_instance_id"),
                "approval_title": item.get("approval_title"),
                "approval_status": item.get("approval_status"),
                "purchase_currency": item.get("purchase_currency"),
                "detail_row_count": item.get("detail_row_count"),
                "mapped_preview_items": [
                    {
                        "material_code": row.get("material_code"),
                        "product_name": row.get("product_name"),
                        "spec_model": row.get("spec_model"),
                        "quantity": row.get("quantity"),
                        "unit_price": row.get("unit_price"),
                        "goods_value": row.get("goods_value"),
                        "purchase_currency": row.get("purchase_currency"),
                    }
                    for row in (item.get("mapped_preview_items") or [])[:5]
                ],
            }
            for item in (result.get("items") or [])[:5]
        ],
        "items": [],
    }


def pull_and_save_to_erp_from_env() -> dict:
    """从钉钉拉取国际物流审批，并保存为 ERP 批次追溯记录。"""

    result = pull_from_env()
    save_result = save_sea_approvals_to_erp(result)
    return {
        "ok": save_result.get("ok"),
        "pull": build_console_summary(
            result,
            output=_clean(os.environ.get("DINGTALK_PULL_OUTPUT")),
            csv_output=_clean(os.environ.get("DINGTALK_PULL_CSV")),
        ),
        "save": save_result,
    }


def pull_latest_logistics_approvals_to_erp(
    *,
    start: str | None = "",
    end: str | None = "",
    transport_modes: tuple[str, ...] | list[str] | str = "ALL",
    limit: int | None = 200,
    env_file: str | None = None,
    process_code: str | None = "",
    api_style: str = "auto",
    list_api: str = "auto",
    page_size: int | None = None,
    max_pages: int | None = None,
    chunk_days: int | None = None,
    access_token: str = "",
    dry_run: bool = False,
) -> dict:
    """手动拉取指定时间范围内的国际物流 OA，并保存/更新为成本批次。

    该入口给前端“钉钉拉取”使用；不清空历史批次，不删除已有明细。
    新审批单会创建批次，已有审批单只补追溯、附件、采购字段和明确费用规则。
    """

    source_mode = resolve_approval_source_mode()
    resolved_env_file = resolve_dingtalk_env_file(env_file) if source_mode == "api" else ""
    env_file_loaded = False
    if resolved_env_file:
        load_env_file(resolved_env_file)
        env_file_loaded = True

    if not _has_dingtalk_pull_credentials() and not (source_mode == "api" and _clean(access_token)):
        return {
            "ok": True,
            "skipped": True,
            "reason": (
                "未配置 PostgreSQL OA 读取账号，本次未执行拉取。"
                if source_mode == "postgres"
                else "未配置钉钉拉取凭据，本次未执行拉取。"
            ),
            "env_file_loaded": env_file_loaded,
        }

    resolved_start = _clean(start)
    resolved_end = _clean(end)
    if not resolved_start or not resolved_end:
        default_start, default_end = _build_scheduled_pull_window()
        resolved_start = resolved_start or default_start
        resolved_end = resolved_end or default_end

    pull_result = pull_logistics_approvals(
        process_code=resolve_logistics_process_code(
            process_code
            or _runtime_config_value(
                "DINGTALK_LOGISTICS_PROCESS_CODE",
                "overseas_costing_dingtalk_logistics_process_code",
            )
        ),
        start=resolved_start,
        end=resolved_end,
        api_style=api_style or _runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default="auto"),
        list_api=list_api or _runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default="auto"),
        page_size=page_size
        or _runtime_config_int("DINGTALK_SCHEDULE_PAGE_SIZE", "DINGTALK_PAGE_SIZE", default=20),
        max_pages=max_pages
        or _runtime_config_int("DINGTALK_SCHEDULE_MAX_PAGES", "DINGTALK_MAX_PAGES", default=20),
        chunk_days=chunk_days
        or _runtime_config_int("DINGTALK_SCHEDULE_CHUNK_DAYS", "DINGTALK_CHUNK_DAYS", default=30),
        limit=limit or None,
        include_raw=False,
        include_all=False,
        access_token=(
            ""
            if source_mode == "postgres"
            else access_token or _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token")
        ),
        corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
        client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
        client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
        app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
        app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
        transport_modes=transport_modes or "ALL",
    )
    if dry_run:
        previews = []
        for approval in pull_result.get("items") or []:
            item_values = build_oa_item_values_from_approval(approval)
            previews.append(
                {
                    "source_approval_no": approval.get("source_approval_no"),
                    "source_instance_id": approval.get("source_instance_id"),
                    "approval_status": approval.get("approval_status"),
                    "transport_mode": approval.get("transport_mode"),
                    "project_ownership": extract_project_candidates_from_approval(approval),
                    "logistics_fees": extract_logistics_fees_from_approval(approval),
                    "item_count": len(item_values),
                    "items": [
                        {
                            key: row.get(key)
                            for key in ("row_no", "material_code", "product_name", "quantity", "gross_weight_kg", "project_collection", "supplier")
                        }
                        for row in item_values[:20]
                    ],
                }
            )
        return {
            "ok": bool(pull_result.get("ok", True)),
            "dry_run": True,
            "data_source": pull_result.get("data_source"),
            "source_updated_at": pull_result.get("source_updated_at"),
            "source_lag_seconds": pull_result.get("source_lag_seconds"),
            "start": resolved_start,
            "end": resolved_end,
            "pull": {
                "total_instance_count": pull_result.get("total_instance_count", 0),
                "detail_count": pull_result.get("detail_count", 0),
                "filtered_count": pull_result.get("filtered_count", 0),
                "transport_counts": pull_result.get("transport_counts", {}),
            },
            "previews": previews[:20],
        }

    save_result = save_sea_approvals_to_erp(pull_result)
    summary = {
        "ok": bool(save_result.get("ok")),
        "data_source": pull_result.get("data_source"),
        "source_updated_at": pull_result.get("source_updated_at"),
        "source_lag_seconds": pull_result.get("source_lag_seconds"),
        "fallback_used": bool(pull_result.get("fallback_used", False)),
        "manual": True,
        "env_file_loaded": env_file_loaded,
        "start": resolved_start,
        "end": resolved_end,
        "transport_modes": pull_result.get("transport_modes"),
        "pull": {
            "total_instance_count": pull_result.get("total_instance_count", 0),
            "detail_count": pull_result.get("detail_count", 0),
            "transport_counts": pull_result.get("transport_counts", {}),
            "filtered_count": pull_result.get("filtered_count", 0),
        },
        "save": {
            "created_count": save_result.get("created_count", 0),
            "updated_count": save_result.get("updated_count", 0),
            "unchanged_count": save_result.get("unchanged_count", 0),
            "skipped_count": save_result.get("skipped_count", 0),
            "failed_count": save_result.get("failed_count", 0),
            "failed_items": (save_result.get("failed_items") or [])[:20],
            "message": save_result.get("message"),
        },
        "items": (save_result.get("items") or [])[:20],
        "skipped_items": (save_result.get("skipped_items") or [])[:20],
    }
    _log_scheduled_pull_summary(summary)
    return summary


def _build_scheduled_pull_window() -> tuple[str, str]:
    start = _runtime_config_value(
        "DINGTALK_SCHEDULE_PULL_START",
        "overseas_costing_dingtalk_schedule_pull_start",
    )
    end = _runtime_config_value(
        "DINGTALK_SCHEDULE_PULL_END",
        "overseas_costing_dingtalk_schedule_pull_end",
    )
    if start and end:
        return start, end

    lookback_days = max(
        _runtime_config_int(
            "DINGTALK_SCHEDULE_LOOKBACK_DAYS",
            "overseas_costing_dingtalk_schedule_lookback_days",
            default=30,
        ),
        1,
    )
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days - 1)
    return start or start_date.strftime("%Y-%m-%d"), end or end_date.strftime("%Y-%m-%d")


def _log_scheduled_pull_summary(summary: dict) -> None:
    if frappe is None:
        return
    try:
        logger = frappe.logger("overseas_costing", allow_site=True) if hasattr(frappe, "logger") else None
        if logger:
            logger.info(_json_dumps(summary))
    except Exception:
        return


def _log_scheduled_pull_error(exc: Exception) -> None:
    if frappe is None or not hasattr(frappe, "log_error"):
        return
    try:
        message = frappe.get_traceback() if hasattr(frappe, "get_traceback") else str(exc)
        frappe.log_error(title="海外成本钉钉自动拉取失败", message=message)
    except Exception:
        return


def scheduled_pull_logistics_approvals() -> dict:
    """Frappe 定时任务：每天自动拉取最近几天的国际物流 OA 并写入/更新批次。"""

    try:
        if _runtime_config_bool(
            "DINGTALK_SCHEDULE_DISABLED",
            "overseas_costing_dingtalk_schedule_disabled",
            default=False,
        ):
            return {"ok": True, "skipped": True, "reason": "钉钉国际物流自动拉取已关闭。"}

        source_mode = resolve_approval_source_mode()
        env_file = (
            resolve_dingtalk_env_file(
                _runtime_config_value(
                    "DINGTALK_SCHEDULE_ENV_FILE",
                    "DINGTALK_ENV_FILE",
                    "overseas_costing_dingtalk_env_file",
                )
            )
            if source_mode == "api"
            else ""
        )
        env_file_loaded = False
        if env_file:
            load_env_file(env_file)
            env_file_loaded = True

        if not _has_dingtalk_pull_credentials():
            return {
                "ok": True,
                "skipped": True,
                "reason": (
                    "未配置 PostgreSQL OA 读取账号，自动拉取跳过。"
                    if source_mode == "postgres"
                    else "未配置钉钉拉取凭据，自动拉取跳过。"
                ),
                "env_file_loaded": env_file_loaded,
            }

        start, end = _build_scheduled_pull_window()
        transport_modes = _runtime_config_value(
            "DINGTALK_SCHEDULE_TRANSPORT_MODES",
            "DINGTALK_TRANSPORT_MODES",
            "DINGTALK_TRANSPORT_MODE",
            "overseas_costing_dingtalk_schedule_transport_modes",
            default="ALL",
        )
        result = pull_logistics_approvals(
            process_code=resolve_logistics_process_code(
                _runtime_config_value(
                    "DINGTALK_LOGISTICS_PROCESS_CODE",
                    "overseas_costing_dingtalk_logistics_process_code",
                )
            ),
            start=start,
            end=end,
            api_style=_runtime_config_value("DINGTALK_API_STYLE", "overseas_costing_dingtalk_api_style", default="auto"),
            list_api=_runtime_config_value("DINGTALK_LIST_API", "overseas_costing_dingtalk_list_api", default="auto"),
            page_size=_runtime_config_int("DINGTALK_SCHEDULE_PAGE_SIZE", "DINGTALK_PAGE_SIZE", default=20),
            max_pages=_runtime_config_int("DINGTALK_SCHEDULE_MAX_PAGES", "DINGTALK_MAX_PAGES", default=20),
            chunk_days=_runtime_config_int("DINGTALK_SCHEDULE_CHUNK_DAYS", "DINGTALK_CHUNK_DAYS", default=30),
            limit=_runtime_config_int(
                "DINGTALK_SCHEDULE_LIMIT",
                "overseas_costing_dingtalk_schedule_limit",
                default=200,
            )
            or None,
            include_raw=False,
            include_all=False,
            access_token=(
                ""
                if source_mode == "postgres"
                else _runtime_config_value("DINGTALK_ACCESS_TOKEN", "overseas_costing_dingtalk_access_token")
            ),
            corp_id=_runtime_config_value("DINGTALK_CORP_ID", "overseas_costing_dingtalk_corp_id"),
            client_id=_runtime_config_value("DINGTALK_CLIENT_ID", "overseas_costing_dingtalk_client_id"),
            client_secret=_runtime_config_value("DINGTALK_CLIENT_SECRET", "overseas_costing_dingtalk_client_secret"),
            app_key=_runtime_config_value("DINGTALK_APP_KEY", "DINGTALK_APPKEY", "overseas_costing_dingtalk_app_key"),
            app_secret=_runtime_config_value("DINGTALK_APP_SECRET", "DINGTALK_APPSECRET", "overseas_costing_dingtalk_app_secret"),
            transport_modes=transport_modes,
        )
        save_result = save_sea_approvals_to_erp(result)
        summary = {
            "ok": bool(save_result.get("ok")),
            "data_source": result.get("data_source"),
            "source_updated_at": result.get("source_updated_at"),
            "source_lag_seconds": result.get("source_lag_seconds"),
            "fallback_used": bool(result.get("fallback_used", False)),
            "scheduled": True,
            "env_file_loaded": env_file_loaded,
            "start": start,
            "end": end,
            "transport_modes": result.get("transport_modes"),
            "pull": {
                "total_instance_count": result.get("total_instance_count", 0),
                "detail_count": result.get("detail_count", 0),
                "transport_counts": result.get("transport_counts", {}),
                "filtered_count": result.get("filtered_count", 0),
            },
            "save": {
                "created_count": save_result.get("created_count", 0),
                "updated_count": save_result.get("updated_count", 0),
                "unchanged_count": save_result.get("unchanged_count", 0),
                "skipped_count": save_result.get("skipped_count", 0),
                "failed_count": save_result.get("failed_count", 0),
                "failed_items": (save_result.get("failed_items") or [])[:20],
                "message": save_result.get("message"),
            },
        }
        _log_scheduled_pull_summary(summary)
        return summary
    except Exception as exc:
        _log_scheduled_pull_error(exc)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="拉取钉钉国际物流审批单并按运输方式筛选")
    parser.add_argument("--env-file", default=os.environ.get("DINGTALK_ENV_FILE", ""), help="加载指定 .env，例如预算管理系统 server\\.env")
    parser.add_argument("--process-code", default="", help=f"国际物流审批流程模板 process_code，默认 {DEFAULT_LOGISTICS_PROCESS_CODE}")
    parser.add_argument("--start", default=os.environ.get("DINGTALK_PULL_START", ""), help="开始时间，例如 2026-07-01")
    parser.add_argument("--end", default=os.environ.get("DINGTALK_PULL_END", ""), help="结束时间，例如 2026-07-21")
    parser.add_argument("--api-style", choices=["auto", "new", "legacy"], default=os.environ.get("DINGTALK_API_STYLE", "auto"), help="钉钉 token/详情接口风格，默认自动判断")
    parser.add_argument("--list-api", choices=["auto", "old", "new", "both"], default=os.environ.get("DINGTALK_LIST_API", "auto"), help="审批实例列表接口，预算系统默认 old，也可 both")
    parser.add_argument("--access-token", default=os.environ.get("DINGTALK_ACCESS_TOKEN", ""), help="已有 access_token，可选")
    parser.add_argument("--corp-id", default=os.environ.get("DINGTALK_CORP_ID", ""), help="新版 OpenAPI corp_id")
    parser.add_argument("--client-id", default=os.environ.get("DINGTALK_CLIENT_ID", ""), help="新版 OpenAPI client_id")
    parser.add_argument("--client-secret", default=os.environ.get("DINGTALK_CLIENT_SECRET", ""), help="新版 OpenAPI client_secret")
    parser.add_argument("--app-key", default=os.environ.get("DINGTALK_APP_KEY", ""), help="旧版或兼容 app_key")
    parser.add_argument("--app-secret", default=os.environ.get("DINGTALK_APP_SECRET", ""), help="旧版或兼容 app_secret")
    parser.add_argument("--page-size", type=int, default=int(os.environ.get("DINGTALK_PAGE_SIZE") or 20), help="每页数量，钉钉通常最大 20")
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("DINGTALK_MAX_PAGES") or 20), help="最多拉取页数，避免误拉过大范围")
    parser.add_argument("--chunk-days", type=int, default=int(os.environ.get("DINGTALK_CHUNK_DAYS") or 30), help="按多少天分段拉取，旧版钉钉接口建议 30")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("DINGTALK_LIMIT") or 0), help="最多读取多少个实例详情，0 表示不限制")
    parser.add_argument("--transport-mode", default=os.environ.get("DINGTALK_TRANSPORT_MODES") or os.environ.get("DINGTALK_TRANSPORT_MODE") or "SEA", help="运输方式：SEA、AIR、EXPRESS、ALL，可用逗号分隔")
    parser.add_argument("--include-raw", action="store_true", help="JSON 中包含完整审批详情原文")
    parser.add_argument("--include-all", action="store_true", help="JSON 中同时包含未命中当前运输方式的审批摘要，便于调试字段名")
    parser.add_argument("--output", default=os.environ.get("DINGTALK_PULL_OUTPUT", ""), help="输出 JSON 路径")
    parser.add_argument("--csv", default=os.environ.get("DINGTALK_PULL_CSV", ""), help="输出 CSV 路径")
    parser.add_argument("--save-to-erp", action="store_true", help="保存海运审批追溯到 Frappe 批次头；不写物料和金额")
    return parser


def main() -> None:
    _preload_env_file_from_argv(sys.argv[1:])
    args = build_parser().parse_args()
    if args.env_file:
        load_env_file(args.env_file)
    result = pull_logistics_approvals(
        process_code=resolve_logistics_process_code(args.process_code),
        start=args.start,
        end=args.end,
        api_style=args.api_style,
        list_api=args.list_api,
        page_size=args.page_size,
        max_pages=args.max_pages,
        chunk_days=args.chunk_days,
        limit=args.limit or None,
        include_raw=args.include_raw,
        include_all=args.include_all,
        access_token=args.access_token,
        corp_id=args.corp_id,
        client_id=args.client_id,
        client_secret=args.client_secret,
        app_key=args.app_key,
        app_secret=args.app_secret,
        transport_modes=args.transport_mode,
    )
    if args.output:
        save_json(result, args.output)
    if args.csv:
        save_csv(result["items"], args.csv)
    if args.save_to_erp:
        save_result = save_sea_approvals_to_erp(result)
        printable = {
            "ok": save_result.get("ok"),
            "pull": build_console_summary(result, output=args.output, csv_output=args.csv),
            "save": save_result,
        }
    else:
        printable = build_console_summary(result, output=args.output, csv_output=args.csv) if args.output or args.csv else result
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
