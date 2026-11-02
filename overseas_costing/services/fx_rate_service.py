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
MAX_HISTORICAL_FX_AGE_DAYS = 7
# 仅识别历史预填值；不得用这两个数值折算新成本。
LEGACY_DEFAULT_FX_USD_TO_RMB = Decimal("7.178751")
LEGACY_DEFAULT_FX_RMB_TO_MXN = Decimal("2.6")

FX_DATE_SOURCE_PAYMENT = "payment_date"
FX_DATE_SOURCE_APPROVAL_FINISHED = "approval_finished_at"
FX_DATE_SOURCE_OA_LOGISTICS_STARTED = "oa_logistics_started_at"
FX_DATE_SOURCE_MISSING = "missing"

FX_DATE_SOURCE_LABELS = {
    FX_DATE_SOURCE_PAYMENT: "真实付款日",
    FX_DATE_SOURCE_APPROVAL_FINISHED: "付款审批完成日（暂估）",
    FX_DATE_SOURCE_OA_LOGISTICS_STARTED: "国际物流审批发起日（暂估）",
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
    return ""


def resolve_fx_rate_date(*, payment_date=None, approval_finished_at=None, oa_logistics_started_at=None) -> dict:
    """确定本次汇率查询应该使用的日期，并保留来源说明。"""

    raw_payment_date = str(payment_date or "").strip()
    normalized_payment_date = normalize_payment_date(payment_date)
    raw_approval_finished_at = str(approval_finished_at or "").strip()
    normalized_approval_finished_at = normalize_payment_date(approval_finished_at)
    raw_oa_logistics_started_at = str(oa_logistics_started_at or "").strip()
    normalized_oa_logistics_started_at = normalize_payment_date(oa_logistics_started_at)

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

    if normalized_oa_logistics_started_at:
        return {
            "ok": True,
            "date": raw_oa_logistics_started_at,
            "normalized_date": normalized_oa_logistics_started_at,
            "date_source": FX_DATE_SOURCE_OA_LOGISTICS_STARTED,
            "date_source_label": FX_DATE_SOURCE_LABELS[FX_DATE_SOURCE_OA_LOGISTICS_STARTED],
            "is_estimated_rate": True,
            "payment_date": raw_payment_date,
            "normalized_payment_date": "",
            "oa_logistics_started_at": raw_oa_logistics_started_at,
            "message": _("未取得可核实的付款日，已按国际物流审批发起日暂估汇率。"),
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
    saved_snapshot = _json_object(version_fx.get("extra_json")).get("costing_fx_snapshot")
    if not isinstance(saved_snapshot, dict):
        saved_snapshot = {}
    saved_rates = saved_snapshot.get("rates") if isinstance(saved_snapshot.get("rates"), dict) else {}
    saved_calculation_date = normalize_payment_date(saved_snapshot.get("calculation_date"))
    stale_snapshot_rates = {}

    def version_rate(currency_code: str, value: Decimal) -> dict:
        saved = saved_rates.get(currency_code) if isinstance(saved_rates.get(currency_code), dict) else {}
        saved_value = _positive_decimal(saved.get("cny_per_unit"))
        original_source = str(saved.get("original_source") or saved.get("source") or "")
        saved_matches = (
            saved_value is not None
            and abs(saved_value - value) <= Decimal("0.000001")
            and original_source in {"currency_exchange", "fx_api", "historical_currency_exchange", "manual"}
        )
        resolved = _rate_payload(
            currency_code, value, source="version_snapshot",
            rate_date=saved.get("rate_date") if saved_matches else "",
            record_name=saved.get("record_name") if saved_matches else "",
            api_snapshot=saved if saved_matches else None,
        )
        resolved["original_source"] = original_source if saved_matches else ""
        resolved["is_estimated"] = bool(saved_matches and (saved.get("is_estimated") or original_source == "historical_currency_exchange"))
        return resolved

    legacy_defaults = []
    if usd_to_rmb is not None:
        usd_snapshot = version_rate("USD", usd_to_rmb)
        if (usd_snapshot["original_source"] in {"currency_exchange", "fx_api", "historical_currency_exchange"}
                and ((saved_calculation_date and saved_calculation_date != normalized_date)
                     or (usd_snapshot["original_source"] != "historical_currency_exchange"
                         and usd_snapshot["rate_date"] != normalized_date))):
            stale_snapshot_rates["USD"] = float(usd_to_rmb)
        elif abs(usd_to_rmb - LEGACY_DEFAULT_FX_USD_TO_RMB) <= Decimal("0.000001") and not usd_snapshot["original_source"]:
            legacy_defaults.append("USD")
        else:
            rates["USD"] = usd_snapshot
    if rmb_to_mxn is not None:
        mxn_to_rmb = Decimal("1") / rmb_to_mxn
        mxn_snapshot = version_rate("MXN", mxn_to_rmb)
        if (mxn_snapshot["original_source"] in {"currency_exchange", "fx_api", "historical_currency_exchange"}
                and ((saved_calculation_date and saved_calculation_date != normalized_date)
                     or (mxn_snapshot["original_source"] != "historical_currency_exchange"
                         and mxn_snapshot["rate_date"] != normalized_date))):
            stale_snapshot_rates["MXN"] = float(rmb_to_mxn)
        elif abs(rmb_to_mxn - LEGACY_DEFAULT_FX_RMB_TO_MXN) <= Decimal("0.000001") and not mxn_snapshot["original_source"]:
            legacy_defaults.append("MXN")
        else:
            rates["MXN"] = mxn_snapshot

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
            latest_date = normalize_payment_date(latest.get("date"))
            age_days = (
                (date.fromisoformat(normalized_date) - date.fromisoformat(latest_date)).days
                if latest_date else None
            )
            if age_days is None or not 0 <= age_days <= MAX_HISTORICAL_FX_AGE_DAYS:
                blocking_errors.append({
                    "currency": currency_code,
                    "code": "FX_HISTORICAL_RATE_TOO_OLD",
                    "message": _(
                        "{0} 最近的历史汇率日期为 {1}，不在计算日期 {2} 之前 {3} 天内，请补充近期汇率。"
                    ).format(currency_code, latest_date or "--", normalized_date, MAX_HISTORICAL_FX_AGE_DAYS),
                })
                continue
            rates[currency_code] = _rate_payload(
                currency_code,
                latest_rate,
                source="historical_currency_exchange",
                rate_date=latest_date,
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
        "legacy_default_currencies": legacy_defaults,
        "stale_snapshot_rates": stale_snapshot_rates,
        "prior_snapshot_date": saved_calculation_date if stale_snapshot_rates else "",
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
        raise ValueError(_("汇率日期已变化，请重新生成试算预览。"))

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
    legacy_defaults = set(stored_resolution.get("legacy_default_currencies") or [])
    stale_snapshot_rates = stored_resolution.get("stale_snapshot_rates") or {}
    prior_snapshot = _json_object(version.get("extra_json")).get("costing_fx_snapshot")
    if not isinstance(prior_snapshot, dict):
        prior_snapshot = {}
    prior_rates = prior_snapshot.get("rates") if isinstance(prior_snapshot.get("rates"), dict) else {}
    if stale_snapshot_rates and normalize_payment_date(prior_snapshot.get("calculation_date")) != stored_resolution.get("prior_snapshot_date"):
        raise ValueError(_("版本汇率来源日期已变化，请重新生成试算预览。"))
    for fieldname, target in target_values.items():
        if target is None:
            continue
        current = _positive_decimal(version.get(fieldname))
        currency_code = "USD" if fieldname == "fx_usd_to_rmb" else "MXN"
        if currency_code in stale_snapshot_rates:
            prior_rate = prior_rates.get(currency_code) if isinstance(prior_rates.get(currency_code), dict) else {}
            prior_source = str(prior_rate.get("original_source") or prior_rate.get("source") or "")
            prior_cny = _positive_decimal(prior_rate.get("cny_per_unit"))
            expected_prior_cny = current if currency_code == "USD" else (Decimal("1") / current if current else None)
            if (not _numbers_close(current, stale_snapshot_rates[currency_code])
                    or not _numbers_close(prior_cny, expected_prior_cny)
                    or prior_source not in {"currency_exchange", "fx_api", "historical_currency_exchange"}):
                raise ValueError(_("版本汇率已变化，请重新生成试算预览。"))
        if current is not None:
            if not _numbers_close(current, target):
                legacy_value = LEGACY_DEFAULT_FX_USD_TO_RMB if currency_code == "USD" else LEGACY_DEFAULT_FX_RMB_TO_MXN
                prior_rate = prior_rates.get(currency_code) if isinstance(prior_rates.get(currency_code), dict) else {}
                prior_source = str(prior_rate.get("original_source") or prior_rate.get("source") or "")
                from_stale_snapshot = currency_code in stale_snapshot_rates
                from_legacy_default = (currency_code in legacy_defaults and _numbers_close(current, legacy_value)
                                       and prior_source not in {"currency_exchange", "fx_api", "historical_currency_exchange", "manual"})
                if not from_stale_snapshot and not from_legacy_default:
                    raise ValueError(_("{0} 已被人工修改，请重新预览。").format(fieldname))
            else:
                continue
        value = float(target)
        updates[fieldname] = value
        changed_fields.append(fieldname)
        audit_changes.append((fieldname, version.get(fieldname), value))

    extra = _json_object(version.get("extra_json"))
    extra["costing_fx_snapshot"] = {
        "schema": 1,
        "calculation_date": calculation_date,
        "date_source": str(stored_resolution.get("date_source") or ""),
        "date_source_ref": str(stored_resolution.get("date_source_ref") or ""),
        "fx_usd_to_rmb": float(target_values["fx_usd_to_rmb"]) if target_values["fx_usd_to_rmb"] is not None else None,
        "fx_rmb_to_mxn": float(target_values["fx_rmb_to_mxn"]),
        "rates": rates,
        "is_estimated": bool(stored_resolution.get("is_estimated")),
        "operator": str(operator or ""),
    }
    updates["extra_json"] = json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    repository.update_version(version_name, updates)

    prior_date_basis = (
        normalize_payment_date(prior_snapshot.get("calculation_date")),
        str(prior_snapshot.get("date_source") or ""),
        str(prior_snapshot.get("date_source_ref") or ""),
    )
    new_date_basis = (
        calculation_date,
        str(stored_resolution.get("date_source") or ""),
        str(stored_resolution.get("date_source_ref") or ""),
    )
    if stored_resolution.get("date_source") and prior_date_basis != new_date_basis:
        repository.insert_audit_log(
            batch_name=batch_name,
            version_name=version_name,
            field_name="costing_fx_date_source",
            old_value=" | ".join(prior_date_basis),
            new_value=" | ".join(new_date_basis),
            operator=operator,
            remark=f"试算汇率日期来源：{stored_resolution.get('date_source_label') or ''}",
        )

    for fieldname, old_value, new_value in audit_changes:
        repository.insert_audit_log(
            batch_name=batch_name,
            version_name=version_name,
            field_name=fieldname,
            old_value=old_value,
            new_value=new_value,
            operator=operator,
            remark=f"试算汇率更新；汇率日期 {calculation_date}",
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
        if snapshot.get("ok") and normalize_payment_date(snapshot.get("rate_date")) != normalized_date:
            snapshot = {
                **snapshot, "ok": False, "action": "rate_date_mismatch",
                "message": _("汇率接口返回的日期与付款日不一致。"),
            }
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
