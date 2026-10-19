"""付款日及成本计算日汇率解析、缓存与可追溯快照。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import frappe
except Exception:  # pragma: no cover - pure tests do not require Frappe
    frappe = None

DEFAULT_FX_RATE_API_URL = "http://8.135.70.130:3003/api/fx-rate"
DEFAULT_TIMEOUT_SECONDS = 8

FX_DATE_SOURCE_PAYMENT = "payment_date"
FX_DATE_SOURCE_APPROVAL_FINISHED = "approval_finished_at"
FX_DATE_SOURCE_MISSING = "missing"

FX_DATE_SOURCE_LABELS = {
    FX_DATE_SOURCE_PAYMENT: "真实付款日",
    FX_DATE_SOURCE_APPROVAL_FINISHED: "付款审批完成日（暂估）",
    FX_DATE_SOURCE_MISSING: "未取得汇率日期",
}

FX_RATE_SOURCES = {
    "version_snapshot": "当前版本汇率",
    "currency_exchange": "当日汇率库",
    "fx_api": "当日汇率 API",
    "historical_currency_exchange": "历史汇率暂估",
}


def _positive_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _rate_payload(currency: str, rate: Decimal, *, source: str, rate_date: str = "",
                  record_name: str = "", cache_pending: bool = False, api_snapshot: dict | None = None) -> dict:
    return {
        "currency": currency,
        "cny_per_unit": float(rate),
        "source": source,
        "source_label": FX_RATE_SOURCES[source],
        "rate_date": normalize_payment_date(rate_date),
        "record_name": str(record_name or ""),
        "is_estimated": source == "historical_currency_exchange",
        "cache_pending": bool(cache_pending),
        "source_url": str((api_snapshot or {}).get("source_url") or ""),
        "fetched_at": (api_snapshot or {}).get("fetched_at"),
    }


def normalize_currency_code(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "CNY"
    compact = text.replace(" ", "").upper()
    lower = text.replace(" ", "").lower()
    if any(token in compact for token in ("RMB", "CNY")) or "人民币" in text:
        return "CNY"
    if "USD" in compact or "DÓLAR" in compact or "DOLAR" in compact or "美元" in text or "美金" in text:
        return "USD"
    if "MXN" in compact or "PESO" in compact or "比索" in text or "墨西哥" in text or "pesos" in lower:
        return "MXN"
    return compact


def normalize_payment_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[年月.]", "-", text).replace("日", "")
    text = re.sub(r"\s+", " ", text).strip()
    if " " in text:
        text = text.split(" ", 1)[0]

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def resolve_fx_rate_date(*, payment_date=None, approval_finished_at=None) -> dict:
    """确定本次汇率查询应该使用的日期，并保留来源说明。"""

    raw_payment_date = str(payment_date or "").strip()
    normalized_payment_date = normalize_payment_date(payment_date)
    raw_approval_finished_at = str(approval_finished_at or "").strip()
    normalized_approval_finished_at = normalize_payment_date(approval_finished_at)

    if normalized_payment_date:
        return {
            "ok": True,
            "date": raw_payment_date,
            "normalized_date": normalized_payment_date,
            "date_source": FX_DATE_SOURCE_PAYMENT,
            "date_source_label": FX_DATE_SOURCE_LABELS[FX_DATE_SOURCE_PAYMENT],
            "is_estimated_rate": False,
            "payment_date": raw_payment_date,
            "normalized_payment_date": normalized_payment_date,
            "approval_finished_at": raw_approval_finished_at,
            "normalized_approval_finished_at": normalized_approval_finished_at,
            "message": "已按真实付款日查询汇率。",
        }

    if normalized_approval_finished_at:
        return {
            "ok": True,
            "date": raw_approval_finished_at,
            "normalized_date": normalized_approval_finished_at,
            "date_source": FX_DATE_SOURCE_APPROVAL_FINISHED,
            "date_source_label": FX_DATE_SOURCE_LABELS[FX_DATE_SOURCE_APPROVAL_FINISHED],
            "is_estimated_rate": True,
            "payment_date": raw_payment_date,
            "normalized_payment_date": "",
            "approval_finished_at": raw_approval_finished_at,
            "normalized_approval_finished_at": normalized_approval_finished_at,
            "message": "未识别到真实付款日，已按付款审批完成日暂估汇率。",
        }

    return {
        "ok": False,
        "date": "",
        "normalized_date": "",
        "date_source": FX_DATE_SOURCE_MISSING,
        "date_source_label": FX_DATE_SOURCE_LABELS[FX_DATE_SOURCE_MISSING],
        "is_estimated_rate": False,
        "payment_date": raw_payment_date,
        "normalized_payment_date": "",
        "approval_finished_at": raw_approval_finished_at,
        "normalized_approval_finished_at": "",
        "message": "缺少真实付款日和付款审批完成日，未自动查询汇率。",
    }


def _api_endpoint(endpoint: str | None = None) -> str:
    return str(endpoint or os.environ.get("OVERSEAS_COST_FX_RATE_API") or DEFAULT_FX_RATE_API_URL).strip()


def _read_json(url: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS, opener=None) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    open_fn = opener or urlopen
    response = None
    try:
        response = open_fn(request, timeout=timeout)
        raw = response.read()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw or "{}")


def fetch_cny_rate(
    *,
    currency,
    payment_date,
    endpoint: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
) -> dict:
    """查询单个币种在付款日的人民币汇率。"""

    currency_code = normalize_currency_code(currency)
    requested_date = normalize_payment_date(payment_date)

    if currency_code == "CNY":
        return {
            "ok": True,
            "action": "resolved",
            "currency": "CNY",
            "requested_date": requested_date,
            "rate_date": requested_date,
            "cny_per_unit": 1.0,
            "source": "builtin:CNY",
            "source_url": "builtin:CNY",
            "fetched_at": None,
            "message": "人民币固定按 1 折算。",
        }
    if currency_code not in {"USD", "MXN"}:
        return {
            "ok": False,
            "action": "unsupported_currency",
            "currency": currency_code,
            "requested_date": requested_date,
            "message": f"暂不支持币种：{currency_code}",
        }
    if not requested_date:
        return {
            "ok": False,
            "action": "missing_payment_date",
            "currency": currency_code,
            "requested_date": "",
            "message": "缺少付款日，未自动查询汇率。",
        }

    query = urlencode({"currency": currency_code, "date": requested_date})
    base_url = _api_endpoint(endpoint)
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}{query}"

    try:
        payload = _read_json(url, timeout=timeout, opener=opener)
    except HTTPError as exc:
        return {
            "ok": False,
            "action": "http_error",
            "currency": currency_code,
            "requested_date": requested_date,
            "message": f"汇率接口 HTTP 错误：{exc.code}",
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "action": "request_failed",
            "currency": currency_code,
            "requested_date": requested_date,
            "message": f"汇率接口请求失败：{exc}",
        }

    if payload.get("error"):
        return {
            "ok": False,
            "action": str(payload.get("error") or "rate_not_found"),
            "currency": currency_code,
            "requested_date": requested_date,
            "rate_date": payload.get("rateDate") or payload.get("date") or "",
            "source": "fx-rate-api",
            "source_url": "",
            "fetched_at": None,
            "raw": payload,
            "message": payload.get("message") or f"付款日 {requested_date} 没有 {currency_code} 汇率。",
        }

    cny_per_unit = payload.get("cnyPerUnit")
    if cny_per_unit is None:
        cny_per_unit = payload.get("rateToCny")
    try:
        cny_per_unit = float(cny_per_unit)
    except (TypeError, ValueError):
        cny_per_unit = 0.0
    if cny_per_unit <= 0:
        return {
            "ok": False,
            "action": "invalid_rate",
            "currency": currency_code,
            "requested_date": requested_date,
            "raw": payload,
            "message": f"汇率接口返回的 {currency_code} 汇率无效。",
        }

    return {
        "ok": True,
        "action": "resolved",
        "currency": currency_code,
        "requested_date": requested_date,
        "rate_date": payload.get("rateDate") or requested_date,
        "cny_per_unit": cny_per_unit,
        "usd_per_unit": payload.get("usdPerUnit"),
        "usd_cny": payload.get("usdCny"),
        "source": "fx-rate-api",
        "source_url": payload.get("sourceUrl") or "",
        "fetched_at": payload.get("fetchedAt"),
        "rate_text": payload.get("rateText") or "",
        "raw": payload,
        "message": payload.get("rateText") or f"已取得 {currency_code} 对人民币汇率。",
    }


def resolve_costing_fx(
    *,
    version_fx: dict | None,
    calculation_date,
    repository=None,
    endpoint: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
) -> dict:
    """按版本快照、当日汇率库、API、历史汇率的顺序解析试算汇率。"""

    normalized_date = normalize_payment_date(calculation_date)
    if not normalized_date:
        raise ValueError(_("缺少计算日期，无法自动取得汇率。"))
    version_fx = dict(version_fx or {})
    repository = repository or FrappeCurrencyExchangeRepository()
    rates: dict[str, dict] = {}
    blocking_errors: list[dict] = []

    usd_to_rmb = _positive_decimal(version_fx.get("fx_usd_to_rmb"))
    rmb_to_mxn = _positive_decimal(version_fx.get("fx_rmb_to_mxn"))
    if usd_to_rmb is not None:
        rates["USD"] = _rate_payload("USD", usd_to_rmb, source="version_snapshot")
    if rmb_to_mxn is not None:
        rates["MXN"] = _rate_payload("MXN", Decimal("1") / rmb_to_mxn, source="version_snapshot")

    for currency_code in ("USD", "MXN"):
        if currency_code in rates:
            continue
        exact = repository.find_exact_buying_rate(currency_code, normalized_date) or {}
        exact_rate = _positive_decimal(exact.get("exchange_rate"))
        if exact_rate is not None:
            rates[currency_code] = _rate_payload(
                currency_code,
                exact_rate,
                source="currency_exchange",
                rate_date=exact.get("date") or normalized_date,
                record_name=exact.get("name") or "",
            )
            continue

        fetch_kwargs = {
            "currency": currency_code,
            "payment_date": normalized_date,
            "timeout": timeout,
        }
        if endpoint is not None:
            fetch_kwargs["endpoint"] = endpoint
        if opener is not None:
            fetch_kwargs["opener"] = opener
        api_snapshot = fetch_cny_rate(**fetch_kwargs)
        api_rate = _positive_decimal(api_snapshot.get("cny_per_unit")) if api_snapshot.get("ok") else None
        api_rate_date = normalize_payment_date(api_snapshot.get("rate_date") or normalized_date)
        if api_rate is not None and api_rate_date == normalized_date:
            rates[currency_code] = _rate_payload(
                currency_code,
                api_rate,
                source="fx_api",
                rate_date=api_rate_date,
                cache_pending=True,
                api_snapshot=api_snapshot,
            )
            continue

        latest = repository.find_latest_buying_rate(currency_code, normalized_date) or {}
        latest_rate = _positive_decimal(latest.get("exchange_rate"))
        if latest_rate is not None:
            rates[currency_code] = _rate_payload(
                currency_code,
                latest_rate,
                source="historical_currency_exchange",
                rate_date=latest.get("date") or "",
                record_name=latest.get("name") or "",
            )
            continue
        blocking_errors.append(
            {
                "currency": currency_code,
                "code": "FX_RATE_UNAVAILABLE",
                "message": f"未取得 {normalized_date} 的 {currency_code} 汇率，汇率库也没有可用历史值。",
            }
        )

    usd_rate = _positive_decimal((rates.get("USD") or {}).get("cny_per_unit"))
    mxn_rate = _positive_decimal((rates.get("MXN") or {}).get("cny_per_unit"))
    resolved_rmb_to_mxn = (
        (Decimal("1") / mxn_rate).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        if mxn_rate is not None
        else None
    )
    return {
        "ok": not blocking_errors,
        "calculation_date": normalized_date,
        "fx_usd_to_rmb": float(usd_rate) if usd_rate is not None else None,
        "fx_rmb_to_mxn": float(resolved_rmb_to_mxn) if resolved_rmb_to_mxn is not None else None,
        "rates": rates,
        "is_estimated": any(bool(row.get("is_estimated")) for row in rates.values()),
        "blocking_errors": blocking_errors,
    }


def _numbers_close(left, right, tolerance: Decimal = Decimal("0.000001")) -> bool:
    left_number = _positive_decimal(left)
    right_number = _positive_decimal(right)
    if left_number is None or right_number is None:
        return left_number is None and right_number is None
    return abs(left_number - right_number) <= tolerance


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _resolution_error_message(resolution: dict) -> str:
    messages = [str(row.get("message") or "").strip() for row in resolution.get("blocking_errors") or []]
    messages = [message for message in messages if message]
    return "；".join(messages) or "未取得可用汇率，请稍后重试或人工补录。"


def persist_costing_fx_resolution(
    *,
    resolution: dict,
    batch_name: str,
    version_name: str,
    current_date,
    repository,
    operator: str = "",
) -> dict:
    """校验并保存已签名的试算汇率；由调用方控制事务提交与回滚。"""

    if not resolution.get("ok"):
        raise ValueError(_resolution_error_message(resolution))
    calculation_date = normalize_payment_date(resolution.get("calculation_date"))
    normalized_current_date = normalize_payment_date(current_date)
    if not calculation_date or calculation_date != normalized_current_date:
        raise ValueError(_("试算汇率已跨日，请重新生成预览。"))

    version = repository.get_version_for_update(version_name) or {}
    if str(version.get("batch") or "") != str(batch_name or ""):
        raise ValueError(_("成本版本不属于当前批次。"))
    if str(version.get("status") or "") in {"Confirmed", "Archived"}:
        raise PermissionError(_("已确认或归档版本不能自动补汇率。"))

    stored_resolution = json.loads(json.dumps(resolution, ensure_ascii=False, default=str))
    rates = stored_resolution.get("rates") if isinstance(stored_resolution.get("rates"), dict) else {}
    for currency_code in ("USD", "MXN"):
        rate = rates.get(currency_code) or {}
        expected = _positive_decimal(rate.get("cny_per_unit"))
        if expected is None:
            continue
        source = str(rate.get("source") or "")
        record = None
        if source == "fx_api":
            record = repository.find_exact_buying_rate(currency_code, calculation_date) or None
            if record:
                if not _numbers_close(record.get("exchange_rate"), expected):
                    raise ValueError(_("{0} 当日汇率已变化，请重新预览。").format(currency_code))
            else:
                record = repository.insert_api_buying_rate(currency_code, calculation_date, float(expected))
                if not _numbers_close((record or {}).get("exchange_rate"), expected):
                    raise ValueError(_("{0} 当日汇率被并发写入不同数值，请重新预览。").format(currency_code))
        elif source in {"currency_exchange", "historical_currency_exchange"}:
            record_name = str(rate.get("record_name") or "")
            record = repository.get_buying_rate(record_name) if record_name else None
            if not record or not _numbers_close(record.get("exchange_rate"), expected):
                raise ValueError(_("{0} 汇率记录已变化，请重新预览。").format(currency_code))
        if record:
            rate["record_name"] = str(record.get("name") or "")
            rate["rate_date"] = normalize_payment_date(record.get("date") or rate.get("rate_date"))
            rate["cache_pending"] = False

    target_values = {
        "fx_usd_to_rmb": _positive_decimal(stored_resolution.get("fx_usd_to_rmb")),
        "fx_rmb_to_mxn": _positive_decimal(stored_resolution.get("fx_rmb_to_mxn")),
    }
    if target_values["fx_rmb_to_mxn"] is None:
        raise ValueError(_("未取得可用的人民币兑比索汇率，本次试算未保存。"))

    updates = {}
    changed_fields = []
    audit_changes = []
    for fieldname, target in target_values.items():
        if target is None:
            continue
        current = _positive_decimal(version.get(fieldname))
        if current is not None:
            if not _numbers_close(current, target):
                raise ValueError(_("{0} 已被人工修改，请重新预览。").format(fieldname))
            continue
        value = float(target)
        updates[fieldname] = value
        changed_fields.append(fieldname)
        audit_changes.append((fieldname, version.get(fieldname), value))

    extra = _json_object(version.get("extra_json"))
    extra["costing_fx_snapshot"] = {
        "schema": 1,
        "calculation_date": calculation_date,
        "fx_usd_to_rmb": float(target_values["fx_usd_to_rmb"]) if target_values["fx_usd_to_rmb"] is not None else None,
        "fx_rmb_to_mxn": float(target_values["fx_rmb_to_mxn"]),
        "rates": rates,
        "is_estimated": bool(stored_resolution.get("is_estimated")),
        "operator": str(operator or ""),
    }
    updates["extra_json"] = json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    repository.update_version(version_name, updates)

    for fieldname, old_value, new_value in audit_changes:
        repository.insert_audit_log(
            batch_name=batch_name,
            version_name=version_name,
            field_name=fieldname,
            old_value=old_value,
            new_value=new_value,
            operator=operator,
            remark=f"试算汇率自动补全；计算日期 {calculation_date}",
        )

    return {
        "fx_context": {
            "fx_usd_to_rmb": float(target_values["fx_usd_to_rmb"]) if target_values["fx_usd_to_rmb"] is not None else None,
            "fx_rmb_to_mxn": float(target_values["fx_rmb_to_mxn"]),
        },
        "fx_resolution": stored_resolution,
        "changed_fields": changed_fields,
    }


class FrappeCurrencyExchangeRepository:
    """复用 ERPNext Currency Exchange 并把版本汇率与审计留在同一事务。"""

    RATE_FIELDS = ["name", "date", "from_currency", "to_currency", "exchange_rate", "for_buying", "for_selling"]

    def __init__(self):
        if frappe is None:
            raise RuntimeError("当前未连接 Frappe 数据库。")

    def find_exact_buying_rate(self, currency, rate_date):
        rows = frappe.get_all(
            "Currency Exchange",
            filters={
                "from_currency": normalize_currency_code(currency),
                "to_currency": "CNY",
                "date": normalize_payment_date(rate_date),
                "for_buying": 1,
            },
            fields=self.RATE_FIELDS,
            order_by="modified desc",
            limit_page_length=1,
        )
        return dict(rows[0]) if rows else None

    def find_latest_buying_rate(self, currency, on_or_before):
        rows = frappe.get_all(
            "Currency Exchange",
            filters={
                "from_currency": normalize_currency_code(currency),
                "to_currency": "CNY",
                "date": ["<=", normalize_payment_date(on_or_before)],
                "for_buying": 1,
            },
            fields=self.RATE_FIELDS,
            order_by="date desc, modified desc",
            limit_page_length=1,
        )
        return dict(rows[0]) if rows else None

    def get_buying_rate(self, record_name):
        if not record_name:
            return None
        row = frappe.db.get_value("Currency Exchange", record_name, self.RATE_FIELDS, as_dict=True)
        return dict(row) if row else None

    def insert_api_buying_rate(self, currency, rate_date, exchange_rate):
        payload = {
            "doctype": "Currency Exchange",
            "date": normalize_payment_date(rate_date),
            "from_currency": normalize_currency_code(currency),
            "to_currency": "CNY",
            "exchange_rate": float(exchange_rate),
            "for_buying": 1,
            "for_selling": 0,
        }
        try:
            frappe.get_doc(payload).insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            pass
        row = self.find_exact_buying_rate(currency, rate_date)
        if not row:
            raise RuntimeError(f"{currency} 当日汇率写入后未能重新读取。")
        return row

    def get_version_for_update(self, version_name):
        rows = frappe.db.sql(
            "SELECT name,batch,status,fx_usd_to_rmb,fx_rmb_to_mxn,extra_json "
            "FROM `tabOverseas Cost Version` WHERE name=%s FOR UPDATE",
            (version_name,),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def update_version(self, version_name, updates):
        frappe.db.set_value("Overseas Cost Version", version_name, updates, update_modified=True)

    def insert_audit_log(self, *, batch_name, version_name, field_name, old_value, new_value,
                         operator="", remark=""):
        operator_name = str(operator or "")
        if not operator_name:
            session_user = getattr(getattr(frappe, "session", None), "user", None)
            if session_user and session_user != "Guest":
                operator_name = str(session_user)
        frappe.get_doc(
            {
                "doctype": "Overseas Cost Audit Log",
                "batch": batch_name,
                "version": version_name,
                "action_type": "EDIT",
                "field_name": field_name,
                "old_value": "" if old_value is None else str(old_value),
                "new_value": "" if new_value is None else str(new_value),
                "operator_name": operator_name,
                "action_remark": remark,
            }
        ).insert(ignore_permissions=True)


def build_fx_context_from_payment_date(
    payment_date,
    *,
    endpoint: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
) -> dict:
    """按付款日查询 USD、MXN 汇率，并兼容现有版本汇率字段。"""

    normalized_date = normalize_payment_date(payment_date)
    snapshots: dict[str, dict] = {}
    errors: list[dict] = []
    context: dict = {
        "ok": True,
        "action": "resolved",
        "payment_date": str(payment_date or "").strip(),
        "normalized_payment_date": normalized_date,
        "source": "fx-rate-api",
        "rate_snapshots": snapshots,
        "errors": errors,
    }

    for currency_code in ("USD", "MXN"):
        snapshot = fetch_cny_rate(
            currency=currency_code,
            payment_date=normalized_date,
            endpoint=endpoint,
            timeout=timeout,
            opener=opener,
        )
        snapshots[currency_code] = snapshot
        if not snapshot.get("ok"):
            errors.append(
                {
                    "currency": currency_code,
                    "action": snapshot.get("action"),
                    "message": snapshot.get("message"),
                }
            )
            continue
        rate = float(snapshot.get("cny_per_unit") or 0)
        if currency_code == "USD":
            context["fx_usd_to_rmb"] = round(rate, 6)
        elif currency_code == "MXN" and rate:
            context["fx_mxn_to_rmb"] = round(rate, 6)
            context["fx_rmb_to_mxn"] = round(1 / rate, 6)

    if errors:
        context["ok"] = False
        context["action"] = "partial" if len(errors) < 2 else "failed"
        context["message"] = "部分付款日汇率缺失。" if len(errors) < 2 else "付款日汇率缺失。"
    else:
        context["message"] = "付款日汇率已取得。"
    return context


def build_fx_context_for_costing(
    *,
    payment_date=None,
    approval_finished_at=None,
    endpoint: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
) -> dict:
    """按成本核算口径查询汇率：真实付款日优先，审批完成日仅作暂估。"""

    resolved_date = resolve_fx_rate_date(
        payment_date=payment_date,
        approval_finished_at=approval_finished_at,
    )
    metadata = {
        "payment_date": str(payment_date or "").strip(),
        "approval_finished_at": str(approval_finished_at or "").strip(),
        "normalized_payment_date": resolved_date.get("normalized_payment_date") or "",
        "normalized_approval_finished_at": resolved_date.get("normalized_approval_finished_at") or "",
        "fx_rate_date": resolved_date.get("date") or "",
        "normalized_fx_rate_date": resolved_date.get("normalized_date") or "",
        "fx_date_source": resolved_date.get("date_source") or FX_DATE_SOURCE_MISSING,
        "fx_date_source_label": resolved_date.get("date_source_label") or FX_DATE_SOURCE_LABELS[FX_DATE_SOURCE_MISSING],
        "is_estimated_rate": bool(resolved_date.get("is_estimated_rate")),
        "rate_date_message": resolved_date.get("message") or "",
    }

    if not resolved_date.get("ok"):
        return {
            "ok": False,
            "action": "missing_fx_rate_date",
            "source": "fx-rate-api",
            "rate_snapshots": {},
            "errors": [],
            "message": resolved_date.get("message") or "缺少汇率日期，未自动查询汇率。",
            **metadata,
        }

    query_kwargs = {}
    if endpoint is not None:
        query_kwargs["endpoint"] = endpoint
    if timeout != DEFAULT_TIMEOUT_SECONDS:
        query_kwargs["timeout"] = timeout
    if opener is not None:
        query_kwargs["opener"] = opener
    context = build_fx_context_from_payment_date(resolved_date["normalized_date"], **query_kwargs)
    message = context.get("message") or ""
    if resolved_date.get("is_estimated_rate"):
        message = f"{resolved_date['message']} {message}".strip()
    else:
        message = f"{resolved_date['message']} {message}".strip()
    context.update(metadata)
    context["message"] = message
    return context
