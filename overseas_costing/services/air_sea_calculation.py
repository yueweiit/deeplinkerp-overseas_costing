"""Independent transport/import comparison. No batch or accounting writes."""
from __future__ import annotations

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message


import json
import re
from decimal import Decimal, InvalidOperation, localcontext

FORMULA_VERSION = "air-sea-html-1"
DEFAULTS = {
    "oceanRate": 7.01, "airRateFx": 6.9236, "tariffRate": 0, "vatRate": 16,
    "airKgRate": 40, "airFixedFee": 2000, "clearanceMxn": 19500,
    "mxnToCny": 2.6, "deliveryFee": 600, "oceanPortFee": 3482.575,
    "oceanContainerFee": "", "oceanContainerWeight": "", "oceanContainerVolume": "",
}
CURRENCY_DEFAULTS = {
    "declaredCurrency": "USD", "oceanPortFeeCurrency": "CNY", "airKgRateCurrency": "CNY",
    "airFixedFeeCurrency": "CNY", "clearanceCurrency": "MXN", "deliveryFeeCurrency": "CNY",
    "oceanContainerFeeCurrency": "CNY",
}
LABELS = {
    "oceanRate": "海运美元汇率", "airRateFx": "空运美元汇率", "mxnToCny": "比索汇率",
    "tariffRate": "关税率", "vatRate": "增值税率", "airKgRate": "空运公斤单价",
    "airFixedFee": "空运固定费", "clearanceMxn": "清关杂费", "deliveryFee": "拖车费",
    "oceanPortFee": "海运本地费用",
}
RESULT_FIELDS = (
    "oceanPortFee", "airKgRate", "airFixedFee", "oceanDeclared", "airDeclared", "oceanCif",
    "airTransport", "airCif", "oceanTariff", "airTariff", "oceanVat", "airVat",
    "oceanClearance", "airClearance", "oceanDelivery", "airDelivery", "oceanDta", "airDta",
    "oceanImport", "airImport", "oceanTotal", "airTotal", "oceanAvg", "airAvg",
)
NUMBER = re.compile(r"^(?:(?:USD|CNY|RMB|MXN|US\$|CN¥|MX\$|[¥￥$])\s*)?([+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*(?:USD|CNY|RMB|MXN|kg|kgs|m3|m³|CBM|件|个|PZ|PCS|%))?$", re.IGNORECASE)


def number(raw):
    if raw is None or isinstance(raw, bool):
        return None
    value = str(raw).strip().replace("，", ",")
    if len(value) > 128:
        return None
    match = NUMBER.fullmatch(value)
    if not match:
        return None
    try:
        numeric = match.group(1).replace(",", "")
        coefficient, _, exponent = numeric.lower().partition("e")
        if sum(char.isdigit() for char in coefficient) > 30 or (exponent and abs(int(exponent)) > 30):
            return None
        parsed = Decimal(numeric)
        return parsed if parsed.is_finite() and abs(parsed) <= Decimal("1e15") else None
    except InvalidOperation:
        return None


def normalize_payload(raw) -> dict:
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > 4_000_000:
            raise ValueError(_i18n("测算数据过大，请控制在 4 MB 以内。"))
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(_i18n("测算数据必须是对象。"))
    rows = raw.get("rows", [])
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError(_i18n("装箱明细最多支持 5000 行。"))
    normalized = []
    for row in rows:
        if not isinstance(row, list) or len(row) > 30:
            raise ValueError(_i18n("装箱明细必须是最多 30 列的行数组。"))
        if any(isinstance(cell, (dict, list, bool)) or len(str(cell or "")) > 10000 for cell in row):
            raise ValueError(_i18n("装箱单元格格式或长度无效。"))
        cells = [str(cell) if cell is not None else "" for cell in row] + [""] * (30 - len(row))
        if any(cell.strip() for cell in cells):
            normalized.append(cells)
    result = {"rows": normalized}
    for key, defaults in (("parameters", DEFAULTS), ("currencies", CURRENCY_DEFAULTS)):
        values = raw.get(key) or {}
        if not isinstance(values, dict):
            raise ValueError(_i18n("{0} 格式无效。").format(key))
        if any(isinstance(v, (dict, list, bool)) or len(str(v)) > 100 for v in values.values()):
            raise ValueError(_i18n("{0} 的值无效。").format(key))
        result[key] = {name: values.get(name, default) for name, default in defaults.items()}
    overrides = raw.get("totals_override") or {}
    if not isinstance(overrides, dict):
        raise ValueError(_i18n("整票汇总格式无效。"))
    result["totals_override"] = {key: overrides.get(key) for key in ("grossWeight", "volume")}
    if any(isinstance(v, (dict, list, bool)) or len(str(v or "")) > 100 for v in result["totals_override"].values()):
        raise ValueError(_i18n("整票汇总值无效。"))
    result["source"] = raw.get("source") or None
    if result["source"] is not None and not isinstance(result["source"], dict):
        raise ValueError(_i18n("来源信息格式无效。"))
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 4_000_000:
        raise ValueError(_i18n("测算数据过大，请控制在 4 MB 以内。"))
    return result


def text(value):
    return format(value, "f") if isinstance(value, Decimal) else value


def quantity_unit(raw):
    unit = str(raw or "").strip()
    return "件" if not unit or unit.lower() in {"pz", "pcs", "pc", "pieces", "piece", "个", "件"} else unit


def calculate(raw) -> dict:
    with localcontext() as context:
        context.prec = 40
        return _calculate(normalize_payload(raw))


def _calculate(payload):
    issues = []
    totals = {key: Decimal(0) for key in ("quantity", "grossWeight", "volume", "declaredValue")}
    units = set()
    complete = {"quantity": True, "grossWeight": True, "volume": True, "declaredValue": True}
    overrides = payload["totals_override"]
    parsed_overrides = {}
    for key, value in overrides.items():
        if value is None or str(value).strip() == "":
            continue
        parsed = number(value)
        if parsed is None or parsed < 0 or (key == "grossWeight" and parsed == 0):
            issues.append(f"整票{'总毛重' if key == 'grossWeight' else '总体积'}无效")
        else:
            parsed_overrides[key] = parsed
    for index, row in enumerate(payload["rows"], 1):
        def cell(column, label, required=False):
            raw = row[column].strip()
            parsed = number(raw)
            if raw and (parsed is None or parsed < 0) or required and not raw:
                issues.append(f"第 {index} 行{label}无效或缺失")
            return parsed if parsed is not None and parsed >= 0 else None

        quantity = cell(20, "数量", True)
        for column in (12, 13, 14, 15, 18, 19, 22):
            cell(column, f"数值字段 {column + 1}")
        gross = cell(23, "总毛重")
        gross_per = cell(16, "单件毛重")
        volume = cell(24, "总体积")
        volume_per = cell(17, "单件体积")
        price = cell(25, "申报单价")
        total_price = cell(26, "申报货值")
        if quantity is None:
            complete["quantity"] = False
        if quantity == 0:
            continue
        if quantity is not None:
            units.add(quantity_unit(row[4]))
            totals["quantity"] += quantity
        if not row[23].strip() and gross_per is not None and quantity is not None:
            gross = gross_per * quantity
        if gross is None:
            complete["grossWeight"] = False
            if "grossWeight" not in parsed_overrides:
                issues.append(f"第 {index} 行缺少毛重，请补填或提供整票总毛重")
        totals["grossWeight"] += gross or 0
        if not row[24].strip() and volume_per is not None and quantity is not None:
            volume = volume_per * quantity
        if volume is None:
            complete["volume"] = False
        totals["volume"] += volume or 0
        declared = price * quantity if row[25].strip() and price is not None and quantity is not None else None
        if not row[25].strip():
            declared = total_price
        if declared is None:
            complete["declaredValue"] = False
            issues.append(f"第 {index} 行缺少申报货值")
        else:
            totals["declaredValue"] += declared
    totals.update(parsed_overrides)
    quantity = totals["quantity"]
    if quantity <= 0:
        issues.append("请填写大于零的核算数量")
    if totals["grossWeight"] <= 0:
        issues.append("请填写大于零的总毛重")
    for key, valid in complete.items():
        if not valid and key not in parsed_overrides:
            totals[key] = None
    if not payload["rows"]:
        totals["volume"] = parsed_overrides.get("volume")
        totals["declaredValue"] = None
    totals["declaredUnitPrice"] = totals["declaredValue"] / quantity if quantity and totals["declaredValue"] is not None and complete["quantity"] else None
    currencies = payload["currencies"]
    for key, value in currencies.items():
        if key != "oceanContainerFeeCurrency" and value not in ("CNY", "USD", "MXN"):
            issues.append("币种必须是 CNY、USD 或 MXN")
    v = {**totals, **currencies}
    for key in LABELS:
        v[key] = number(payload["parameters"][key])
    sea_currencies = [currencies[k] for k in ("declaredCurrency", "oceanPortFeeCurrency", "clearanceCurrency", "deliveryFeeCurrency")]
    air_currencies = [currencies[k] for k in ("declaredCurrency", "airKgRateCurrency", "airFixedFeeCurrency", "clearanceCurrency", "deliveryFeeCurrency")]
    used_fx = {"oceanRate": "USD" in sea_currencies, "airRateFx": "USD" in air_currencies, "mxnToCny": "MXN" in sea_currencies + air_currencies}
    for key, label in LABELS.items():
        value = v[key]
        if key in used_fx:
            if used_fx[key] and (value is None or value <= 0):
                issues.append(f"{label}须大于零")
        elif value is None or value < 0:
            issues.append(f"{label}无效或缺失")
    result = {key: None for key in RESULT_FIELDS}
    result.update({"v": {key: text(value) for key, value in v.items()}, "status": "Draft" if issues else "Ready", "issues": list(dict.fromkeys(issues)), "unit": next(iter(units)) if len(units) == 1 else "件", "mixedUnits": len(units) > 1, "formula_version": FORMULA_VERSION})
    if issues:
        return result

    def cny(value, currency, fx):
        if currency == "USD":
            return value * fx
        if currency == "MXN":
            return value / v["mxnToCny"]
        return value

    r = {}
    r["oceanPortFee"] = cny(v["oceanPortFee"], v["oceanPortFeeCurrency"], v["oceanRate"])
    r["airKgRate"] = cny(v["airKgRate"], v["airKgRateCurrency"], v["airRateFx"])
    r["airFixedFee"] = cny(v["airFixedFee"], v["airFixedFeeCurrency"], v["airRateFx"])
    r["airTransport"] = r["airKgRate"] * v["grossWeight"] + r["airFixedFee"]
    for prefix, fx, transport in (("ocean", v["oceanRate"], r["oceanPortFee"]), ("air", v["airRateFx"], r["airTransport"])):
        r[prefix + "Declared"] = cny(v["declaredValue"], v["declaredCurrency"], fx)
        cif = r[prefix + "Cif"] = transport + r[prefix + "Declared"]
        tariff = r[prefix + "Tariff"] = cif * v["tariffRate"] / 100
        r[prefix + "Vat"] = (cif + tariff) * v["vatRate"] / 100
        r[prefix + "Clearance"] = cny(v["clearanceMxn"], v["clearanceCurrency"], fx)
        r[prefix + "Delivery"] = cny(v["deliveryFee"], v["deliveryFeeCurrency"], fx)
        r[prefix + "Dta"] = cif * Decimal("0.0008")
        r[prefix + "Import"] = sum(r[prefix + suffix] for suffix in ("Tariff", "Vat", "Clearance", "Dta", "Delivery"))
        r[prefix + "Total"] = transport + r[prefix + "Import"]
        r[prefix + "Avg"] = None if result["mixedUnits"] else r[prefix + "Total"] / quantity
    result.update({key: text(value) for key, value in r.items()})
    return result
