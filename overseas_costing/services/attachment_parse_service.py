"""
中文用途：附件解析服务。

后续这里会接：
1. 凭证文件上传后的解析任务
2. OCR 识别
3. AI 字段抽取
4. 识别结果回填
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

try:
    import frappe
except Exception:  # pragma: no cover - 本地测试环境不一定有 Frappe
    frappe = None

from overseas_costing.services import fx_rate_service


PACKING_LIST_TEMPLATE_STRATEGIES = {
    "mixed_workbook_router": "多 sheet 混合工作簿，先识别 sheet 类型再分别路由解析。",
    "sea_container_sheet": "海运装柜 / 装箱单模板，重点取物料编码、数量、毛重、体积。",
    "carton_packing_list": "彩盒 / 纸箱装箱单模板，重点取箱数、NW/GW、尺寸。",
    "express_item_list": "快递清单模板，重点取 SKU、数量、收件人、总重。",
}

TAX_CERTIFICATE_PARSE_TARGETS = [
    "pedimento_no",
    "pedimento_ref",
    "payment_date",
    "exchange_rate",
    "gross_weight_kg",
    "container_no",
    "paid_total_mxn",
    "tax_totals",
    "line_items",
]
OCR_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
WORD_DOCUMENT_SUFFIXES = {".doc", ".docx"}
TEXT_DOCUMENT_SUFFIXES = {".txt"}
DOCX_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_AI_EXTRACTED_TEXT_CHARS = 80_000


def preview_source_document(
    *,
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    include_text: bool = False,
) -> dict:
    """预览识别 OA 附件内容，只判断资料类型和字段候选，不写入成本字段。"""

    path = _resolve_source_file_path(file_path=file_path, file_url=file_url)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        native_text, native_method = extract_pdf_text_with_method(file_path=str(path))
        if _has_meaningful_document_text(native_text):
            text = native_text
            extraction_method = native_method
        else:
            text = _ocr_pdf_file(path)
            extraction_method = "ocr_pdf"
    elif suffix in OCR_IMAGE_SUFFIXES:
        text = _ocr_image_file(path)
        extraction_method = "ocr_image"
    elif suffix == ".docx":
        text = _extract_docx_text(path)
        extraction_method = "word_docx"
    elif suffix == ".doc":
        text = _extract_legacy_word_text(path)
        extraction_method = "word_doc"
    elif suffix in TEXT_DOCUMENT_SUFFIXES:
        text = _extract_plain_text_file(path)
        extraction_method = "txt_text"
    else:
        raise ValueError(_("暂不支持识别 {0} 格式附件。").format(suffix or '未知'))

    classification = classify_source_document_text(text, source_name=source_name or path.name)
    fields = extract_source_document_field_candidates(text, classification_code=classification["code"])
    purchase_order = (
        parse_purchase_order_text(text, source_name=source_name or path.name)
        if classification["code"] == "purchase_order"
        else {}
    )
    result = {
        "ok": True,
        "source_name": source_name or path.name,
        "file_path": str(path),
        "file_url": file_url or "",
        "file_ext": suffix.lstrip("."),
        "extraction_method": extraction_method,
        "classification": classification,
        "field_candidates": fields,
        "purchase_order": purchase_order,
        "text_excerpt": _document_text_excerpt(text),
        "text_length": len(text),
        "can_write_purchase_price": classification["code"] == "purchase_order" and bool(purchase_order.get("line_items")),
        "message": "附件内容识别预览已生成，当前不会写入物料单价或货值。",
    }
    if include_text:
        result["text_content"] = _document_text_excerpt(text, limit=MAX_AI_EXTRACTED_TEXT_CHARS)
    return result


def classify_source_document_text(text: str | None, *, source_name: str | None = None) -> dict:
    """按内容而非文件名区分采购价格、报关和物流报价资料。"""

    normalized = _normalize_text(text).upper()
    if (
        "PEDIMENTO" in normalized
        and any(marker in normalized for marker in ("NUM. PEDIMENTO", "IMPORTE PAGADO", "ADUANA E/S"))
    ):
        return {
            "code": "tax_certificate",
            "label": "完税凭证",
            "reason": "识别到墨西哥 PEDIMENTO 完税凭证结构，将提取报关单号和税费候选用于最终核对。",
        }
    if any(marker in normalized for marker in ("海关出口货物报关单", "海关进口货物报关单", "CUSTOMS DECLARATION")):
        return {
            "code": "customs_declaration",
            "label": "报关资料",
            "reason": "识别到海关报关单标题，申报价格仅作为关务资料，不自动写入采购单价。",
        }
    if any(marker in normalized for marker in ("物流报价", "运费报价", "FREIGHT QUOTE", "COTIZACI")):
        return {
            "code": "logistics_quote",
            "label": "物流报价",
            "reason": "识别到物流/运费报价，作为费用候选，需人工确认后才能参与分摊。",
        }
    if any(marker in normalized for marker in ("PURCHASE ORDER", "采购订单")):
        return {
            "code": "purchase_order",
            "label": "采购订单",
            "reason": "识别到采购订单标题，将提取订单明细并按物料编码、规格生成匹配预览。",
        }
    has_price = any(marker in normalized for marker in ("单价", "UNIT PRICE", "PRECIO", "金额", "AMOUNT", "TOTAL"))
    has_goods = any(marker in normalized for marker in ("物料编码", "物品编码", "货号", "SKU", "MATERIAL CODE", "PRODUCT CODE"))
    if has_price and has_goods:
        return {
            "code": "purchase_price_document",
            "label": "采购价格资料",
            "reason": "同时识别到物料标识和价格字段，可进入物料编码匹配预览。",
        }
    return {
        "code": "unclassified",
        "label": "待人工识别",
        "reason": f"未识别出稳定的采购价格、报关或物流报价结构（文件：{source_name or '--'}）。",
    }


def extract_source_document_field_candidates(text: str | None, *, classification_code: str = "") -> dict:
    """提取仅供预览核对的附件字段候选，不承担自动写入职责。"""

    normalized = _normalize_text(text)
    compact_numbers = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", normalized)
    material_codes = list(dict.fromkeys(re.findall(r"\b[A-Z]{1,5}\d{3,}\b", compact_numbers.upper())))[:30]
    hs_codes = list(dict.fromkeys(re.findall(r"\b\d{8,10}\b", compact_numbers)))[:30]
    currencies = []
    for code, markers in (("USD", ("美元", "USD", "US$")), ("RMB", ("人民币", "RMB", "CNY", "¥")), ("MXN", ("比索", "MXN", "PESO"))):
        if any(marker in compact_numbers.upper() for marker in markers):
            currencies.append(code)

    result = {
        "material_codes": material_codes,
        "hs_codes": hs_codes,
        "currencies": currencies,
        "purchase_order_no": "",
        "unit_price_candidate": None,
        "goods_value_candidate": None,
        "pedimento_no_candidate": "",
        "paid_total_mxn_candidate": None,
        "tax_total_mxn_candidate": None,
    }
    if classification_code == "purchase_order":
        result["purchase_order_no"] = parse_purchase_order_text(normalized).get("purchase_order_no") or ""
    if classification_code == "customs_declaration":
        unit_price = re.search(r"\b(\d+\.\d{2,4})\s+(?:中国|CHIN)", compact_numbers, flags=re.IGNORECASE)
        goods_value = re.search(r"\b(\d+\.\d{2})\s*(?:美元|USD)\b", compact_numbers, flags=re.IGNORECASE)
        result["unit_price_candidate"] = _to_number(unit_price.group(1)) if unit_price else None
        result["goods_value_candidate"] = _to_number(goods_value.group(1)) if goods_value else None
    if classification_code == "tax_certificate":
        certificate = parse_tax_certificate_text(normalized)
        header = certificate.get("header") or {}
        summary = certificate.get("summary") or {}
        result["pedimento_no_candidate"] = header.get("pedimento_no") or ""
        result["paid_total_mxn_candidate"] = header.get("paid_total_mxn")
        result["tax_total_mxn_candidate"] = summary.get("tax_total_sum_mxn")
    return result


def parse_purchase_order_text(text: str | None, *, source_name: str | None = None) -> dict:
    """从采购订单文本中提取财务核对所需的订单头和完整价格行。"""

    normalized = _normalize_text(text)
    compact = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", normalized)
    purchase_order_no = _find_purchase_order_no(compact, source_name=source_name)
    currency = _find_purchase_order_currency(compact)
    line_items = _parse_purchase_order_line_items(compact, currency=currency)
    return {
        "purchase_order_no": purchase_order_no,
        "supplier": _find_purchase_order_party(compact, "SUPPLIER|供应商"),
        "buyer": _find_purchase_order_party(compact, "BUYER|采购方|买方"),
        "currency": currency,
        "line_items": line_items,
        "recognized_line_count": len(line_items),
        "message": (
            f"已识别采购订单 {purchase_order_no or '--'}，可匹配 {len(line_items)} 条完整价格明细。"
            if line_items
            else f"已识别采购订单 {purchase_order_no or '--'}，但未识别出可安全写入的完整价格明细。"
        ),
    }


def _find_purchase_order_no(text: str, *, source_name: str | None = None) -> str:
    patterns = (
        r"(?:PURCHASE\s+ORDER|P\.?O\.?|采购订单)\s*(?:NO\.?|NUMBER|#|编号)?\s*[:：#-]?\s*([A-Z]{1,5}\d{6,}(?:[-_/][A-Z0-9]+)*)",
        r"\b(PO\d{6,}(?:[-_/][A-Z0-9]+)*)\b",
    )
    for candidate in (text, str(source_name or "")):
        upper = candidate.upper()
        for pattern in patterns:
            match = re.search(pattern, upper, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return ""


def _find_purchase_order_currency(text: str) -> str:
    upper = text.upper()
    for code, markers in (("USD", ("USD", "US$", "美元")), ("RMB", ("RMB", "CNY", "人民币", "¥")), ("MXN", ("MXN", "PESO", "比索"))):
        if any(marker in upper for marker in markers):
            return code
    return ""


def _find_purchase_order_party(text: str, label_pattern: str) -> str:
    match = re.search(
        rf"(?:{label_pattern})(?:\s*\([^)]*\))?\s*[:：]\s*(?:\n\s*)?(?:COMPANY\s+FULL\s+NAME\s*\+?\s*)?([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(match.group(1)) if match else ""


def _parse_purchase_order_line_items(text: str, *, currency: str) -> list[dict]:
    items: list[dict] = []
    seen_codes: set[str] = set()
    code_pattern = re.compile(r"\b([A-Z]{1,5}\d{3,}[A-Z0-9-]*)\b", flags=re.IGNORECASE)
    number_pattern = re.compile(r"(?<![A-Z0-9])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

    for raw_line in text.splitlines():
        line = _clean_spaces(raw_line)
        code_match = code_pattern.search(line)
        if not code_match:
            continue
        material_code = code_match.group(1).upper()
        if material_code in seen_codes:
            continue

        remainder = f"{line[:code_match.start()]} {line[code_match.end():]}".strip()
        number_matches = list(number_pattern.finditer(remainder))
        if len(number_matches) < 3:
            continue
        numeric_values = [_to_number(match.group(0)) for match in number_matches]
        if any(value is None for value in numeric_values):
            continue
        quantity, unit_price, goods_value = numeric_values[-3:]
        if not quantity or unit_price is None or goods_value is None or quantity <= 0 or unit_price <= 0 or goods_value <= 0:
            continue

        expected_total = float(quantity) * float(unit_price)
        tolerance = max(1, abs(float(goods_value)) * 0.03)
        if abs(expected_total - float(goods_value)) > tolerance:
            continue

        first_number_start = number_matches[0].start()
        product_name = _clean_spaces(remainder[:first_number_start].strip(" |-"))
        items.append(
            {
                "material_code": material_code,
                "product_name": product_name,
                "spec_model": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "purchase_currency": currency,
                "goods_value": goods_value,
                "source_type": "PURCHASE_ORDER_ATTACHMENT",
            }
        )
        seen_codes.add(material_code)
    return items


def _has_meaningful_document_text(text: str | None) -> bool:
    without_page_markers = re.sub(r"---\s*Page\s*\d+\s*---", "", str(text or ""), flags=re.IGNORECASE)
    return len(re.sub(r"\s+", "", without_page_markers)) >= 40


def _ocr_pdf_file(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="ocw-ocr-pdf-") as temp_dir:
        output_prefix = Path(temp_dir) / "page"
        _run_document_command(["pdftoppm", "-r", "250", "-png", str(path), str(output_prefix)])
        page_paths = sorted(Path(temp_dir).glob("page-*.png"))
        if not page_paths:
            raise RuntimeError("PDF 转图片失败，未生成可供 OCR 的页面。")
        return "\n".join(
            f"--- Page {index} ---\n{_ocr_image_file(page_path)}"
            for index, page_path in enumerate(page_paths[:20], start=1)
        ).strip()


def _ocr_image_file(path: Path) -> str:
    return _run_document_command(
        ["tesseract", str(path), "stdout", "-l", "chi_sim+eng", "--psm", "3"],
        timeout=120,
    ).strip()


def _extract_docx_text(path: Path) -> str:
    """抽取 DOCX 的段落和表格文字，统一交给现有资料分类与字段候选逻辑。"""

    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Word 文件格式不正确，无法读取 DOCX 内容。") from exc
    except KeyError as exc:
        raise RuntimeError("该 Word 文件缺少正文内容，无法读取 DOCX 内容。") from exc

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise RuntimeError("Word 文件正文格式不正确，无法读取 DOCX 内容。") from exc

    paragraphs = []
    for paragraph in root.iter(f"{DOCX_WORD_NAMESPACE}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{DOCX_WORD_NAMESPACE}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_legacy_word_text(path: Path) -> str:
    """读取旧版 DOC；部署环境未安装 antiword 时给出可执行的处理建议。"""

    try:
        return _run_document_command(["antiword", "-w", "0", str(path)], timeout=120).strip()
    except RuntimeError as exc:
        if "缺少 antiword" in str(exc):
            raise RuntimeError("当前环境无法读取旧版 .doc 文件，请先转换为 .docx 后再识别。") from exc
        raise


def _extract_plain_text_file(path: Path) -> str:
    """兼容 UTF-8 和常见中文编码的 TXT 附件。"""

    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")


def _run_document_command(command: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        tool = command[0] if command else "解析工具"
        raise RuntimeError(f"当前环境缺少 {tool}，无法识别扫描附件。") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"附件 OCR 处理失败：{detail or command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("附件 OCR 超时，请拆分文件或人工核对。") from exc
    return result.stdout or ""


def _document_text_excerpt(text: str | None, limit: int = 1800) -> str:
    normalized = _normalize_text(text).strip()
    return normalized if len(normalized) <= limit else f"{normalized[:limit]}\n...（识别文本已截断）"


def enqueue_parse_task(attachment_name: str) -> dict:
    return {
        "ok": True,
        "attachment_name": attachment_name,
        "queued": True,
        "message": "附件解析任务骨架已创建。",
    }


def build_packing_list_parse_task(
    *,
    batch_name: str,
    version_name: str | None = None,
    attachment_name: str | None = None,
    file_url: str | None = None,
    template_hint: str | None = None,
) -> dict:
    """构造装箱单解析任务的第一版描述。"""

    strategy = template_hint or "mixed_workbook_router"
    return {
        "batch_name": batch_name,
        "version_name": version_name,
        "attachment_name": attachment_name,
        "file_url": file_url,
        "template_hint": strategy,
        "parse_targets": [
            "actual_shipped_qty",
            "gross_weight_kg",
            "volume_m3",
            "volume_weight_kg",
            "chargeable_weight_kg",
            "unit_price",
            "purchase_currency",
            "goods_value",
        ],
        "parser_strategy": strategy,
        "parser_strategy_desc": PACKING_LIST_TEMPLATE_STRATEGIES.get(strategy, "待补充的模板策略。"),
        "needs_manual_review": True,
        "message": "一期先按多模板路由方式解析装箱单，识别后仍允许人工修正并留痕。",
    }


def preview_tax_certificate_pdf(
    *,
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    text: str | None = None,
    batch_name: str | None = None,
) -> dict:
    """预览解析进口完税凭证 PDF，不写入成本明细。"""

    extracted_text = text if text is not None else extract_pdf_text(file_path=file_path, file_url=file_url)
    parsed = parse_tax_certificate_text(extracted_text, source_name=source_name or file_path or file_url or "")
    parsed["reconciliation"] = build_tax_certificate_reconciliation_preview(parsed, batch_name=batch_name)
    parsed["ok"] = True
    parsed["file_name"] = source_name or _file_name_from_ref(file_path or file_url or "")
    parsed["file_path"] = file_path or ""
    parsed["file_url"] = file_url or ""
    parsed["message"] = "完税凭证解析预览已生成，当前不会写入成本表。"
    return parsed


def save_tax_certificate_parse_result(
    *,
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
    text: str | None = None,
    batch_name: str | None = None,
) -> dict:
    """保存完税凭证解析快照到附件记录，不写入成本字段。"""

    parsed = preview_tax_certificate_pdf(
        source_name=source_name,
        file_path=file_path,
        file_url=file_url,
        text=text,
        batch_name=batch_name,
    )
    reconciliation = parsed.get("reconciliation") or {}
    matched_batch = reconciliation.get("batch") or {}
    if not _has_frappe_db_context():
        return {
            "ok": True,
            "dry_run": True,
            "saved": False,
            "preview": parsed,
            "message": "当前未连接 Frappe，仅返回保存预览，不写入附件记录。",
        }
    if not matched_batch.get("name"):
        return {
            "ok": False,
            "saved": False,
            "preview": parsed,
            "message": "未匹配到系统批次，暂不保存解析结果。请先确认报关单号或柜号。",
        }

    identity_sync = _sync_tax_certificate_identity(
        parsed=parsed,
        batch=matched_batch,
        source_name=source_name or parsed.get("file_name") or "",
    )
    fx_sync = _sync_tax_certificate_exchange_rate_to_version(
        parsed=parsed,
        batch=matched_batch,
        source_name=source_name or parsed.get("file_name") or "",
    )
    cost_refresh = _refresh_costing_after_fx_sync(fx_sync=fx_sync, batch=matched_batch)
    parsed["identity_sync"] = identity_sync
    parsed["fx_sync"] = fx_sync
    parsed["cost_refresh"] = cost_refresh
    values = _build_tax_certificate_attachment_values(
        parsed=parsed,
        batch=matched_batch,
        source_name=source_name,
        file_path=file_path,
        file_url=file_url,
    )
    existing_name = _find_existing_tax_certificate_attachment(values)
    if existing_name:
        doc = frappe.get_doc("Overseas Cost Attachment", existing_name)
        for fieldname, value in values.items():
            setattr(doc, fieldname, value)
        doc.save(ignore_permissions=True)
        action = "updated"
    else:
        doc = frappe.get_doc({"doctype": "Overseas Cost Attachment", **values}).insert(ignore_permissions=True)
        action = "created"

    frappe.db.commit()
    parsed["saved_attachment_name"] = doc.name
    return {
        "ok": True,
        "saved": True,
        "action": action,
        "attachment_name": doc.name,
        "batch_name": matched_batch.get("name") or "",
        "source_doc_no": values.get("source_doc_no") or "",
        "identity_sync": identity_sync,
        "fx_sync": fx_sync,
        "cost_refresh": cost_refresh,
        "preview": parsed,
        "message": _tax_certificate_save_message(fx_sync, cost_refresh),
    }


def list_tax_certificate_parse_records(batch_name: str | None = None, limit: int | None = 20) -> dict:
    """返回已保存完税凭证解析记录摘要，方便前端展示。"""

    if not _has_frappe_db_context():
        return {
            "ok": True,
            "dry_run": True,
            "batch_name": batch_name or "",
            "items": [],
            "total": 0,
            "message": "当前未连接 Frappe，返回空解析记录。",
        }

    resolved_batch = _find_tax_certificate_batch({}, batch_name=batch_name) if batch_name else None
    requested_limit = max(1, min(int(limit or 20), 100))
    records = _query_tax_certificate_attachment_records(
        batch_name=resolved_batch.get("name") if resolved_batch else None,
        limit=requested_limit,
    )
    fallback_recent = False
    if batch_name and resolved_batch and not records:
        records = _query_tax_certificate_attachment_records(limit=requested_limit)
        fallback_recent = True

    items = [_build_tax_certificate_record_summary(row) for row in records]
    return {
        "ok": True,
        "batch_name": batch_name or "",
        "resolved_batch": _public_batch_snapshot(resolved_batch),
        "fallback_recent": fallback_recent,
        "items": items,
        "total": len(items),
        "message": "完税凭证解析记录已返回。",
    }


def sync_saved_tax_certificate_identity(limit: int | None = 200) -> dict:
    """回填历史已解析完税凭证的报关单号，仅补空值且不覆盖冲突。"""

    if not _has_frappe_db_context():
        return {"ok": False, "dry_run": True, "message": "当前未连接 Frappe，无法回填历史完税凭证。"}

    rows = _query_tax_certificate_attachment_records(limit=max(1, min(int(limit or 200), 1000)))
    synced_items = []
    total_updated = 0
    total_conflict = 0
    total_skipped = 0
    for row in rows:
        batch_name = str(row.get("batch") or "").strip()
        snapshot = _json_loads(row.get("parse_result_json"))
        header = snapshot.get("header") if isinstance(snapshot.get("header"), dict) else {}
        if not batch_name or not header.get("pedimento_no"):
            total_skipped += 1
            continue
        batch = frappe.db.get_value(
            "Overseas Cost Batch",
            batch_name,
            ["name", "current_version", "customs_no"],
            as_dict=True,
        ) or {}
        if not batch.get("name"):
            total_skipped += 1
            continue
        sync_result = _sync_tax_certificate_identity(
            parsed={"header": header},
            batch=batch,
            source_name=str(row.get("file_name") or ""),
        )
        action = sync_result.get("action")
        if action == "updated":
            total_updated += 1
        elif action == "conflict":
            total_conflict += 1
        else:
            total_skipped += 1
        synced_items.append({"attachment_name": row.get("name"), "batch_name": batch_name, "result": sync_result})

    if total_updated:
        frappe.db.commit()
    return {
        "ok": True,
        "scanned_count": len(rows),
        "updated_count": total_updated,
        "conflict_count": total_conflict,
        "skipped_count": total_skipped,
        "items": synced_items,
        "message": "已回填历史完税凭证的报关单号；已有不同值的批次未覆盖。",
    }


def sync_saved_tax_certificate_fx_fallback(record_name: str | None = None, limit: int | None = 200) -> dict:
    """给历史完税凭证解析快照补充当前版本汇率兜底说明，不改成本字段。"""

    if not _has_frappe_db_context():
        return {"ok": False, "dry_run": True, "message": "当前未连接 Frappe，无法回填历史完税凭证汇率说明。"}

    if record_name:
        fields = [
            "name",
            "batch",
            "version",
            "parse_result_json",
            "mapped_result_json",
        ]
        row = frappe.db.get_value("Overseas Cost Attachment", record_name, fields, as_dict=True)
        rows = [row] if row else []
    else:
        rows = _query_tax_certificate_attachment_records(limit=max(1, min(int(limit or 200), 1000)))

    updated = []
    skipped = 0
    for row in rows:
        if not row:
            skipped += 1
            continue
        parse_result = _json_loads(row.get("parse_result_json"))
        mapped_result = _json_loads(row.get("mapped_result_json"))
        enriched = _enrich_tax_certificate_fx_sync_with_version_fallback(
            parse_result=parse_result,
            mapped_result=mapped_result,
            row=row,
        )
        if enriched == parse_result:
            skipped += 1
            continue
        frappe.db.set_value(
            "Overseas Cost Attachment",
            row.get("name"),
            "parse_result_json",
            _json_dumps(enriched),
            update_modified=False,
        )
        updated.append(row.get("name"))

    if updated:
        frappe.db.commit()
    return {
        "ok": True,
        "updated_count": len(updated),
        "skipped_count": skipped,
        "record_names": updated,
        "message": f"已回填 {len(updated)} 条完税凭证解析记录的汇率兜底说明。",
    }


def get_tax_certificate_parse_record(record_name: str | None = None) -> dict:
    """返回单条完税凭证解析快照详情，不暴露原始 DocType 表单给业务页面。"""

    if not record_name:
        return {
            "ok": False,
            "message": "请传入要查看的解析记录。",
        }

    if not _has_frappe_db_context():
        return {
            "ok": False,
            "dry_run": True,
            "record_name": record_name,
            "message": "当前未连接 Frappe，无法读取已保存的解析记录。",
        }

    if not frappe.db.exists("Overseas Cost Attachment", record_name):
        return {
            "ok": False,
            "record_name": record_name,
            "message": "未找到对应的完税凭证解析记录。",
        }

    fields = [
        "name",
        "batch",
        "version",
        "source_type",
        "attachment_type",
        "source_doc_no",
        "file_name",
        "file_url",
        "parse_status",
        "parse_result_json",
        "mapped_result_json",
        "modified",
        "creation",
    ]
    row = frappe.db.get_value("Overseas Cost Attachment", record_name, fields, as_dict=True) or {}
    row["name"] = row.get("name") or record_name
    if row.get("source_type") != "Voucher" or row.get("attachment_type") != "Tax Certificate":
        return {
            "ok": False,
            "record_name": record_name,
            "message": "该附件记录不是完税凭证解析记录。",
        }

    parse_result = _json_loads(row.get("parse_result_json"))
    mapped_result = _json_loads(row.get("mapped_result_json"))
    parse_result = _enrich_tax_certificate_fx_sync_with_version_fallback(
        parse_result=parse_result,
        mapped_result=mapped_result,
        row=row,
    )
    return {
        "ok": True,
        "record_name": record_name,
        "record_summary": _build_tax_certificate_record_summary(row),
        "parse_result": parse_result,
        "mapped_result": mapped_result,
        "message": "完税凭证解析记录详情已返回。",
    }


def delete_tax_certificate_parse_records(
    *,
    batch_name: str | None = None,
    record_name: str | None = None,
    record_names_json: str | None = None,
) -> dict:
    """删除已保存的完税凭证解析记录，仅限 Voucher / Tax Certificate 附件记录。"""

    if not _has_frappe_db_context():
        return {
            "ok": False,
            "dry_run": True,
            "batch_name": batch_name or "",
            "record_name": record_name or "",
            "record_names": [],
            "deleted_count": 0,
            "message": "当前未连接 Frappe，无法删除解析记录。",
        }

    target_names: list[str] = []
    skipped_items: list[dict] = []
    if record_names_json is not None:
        try:
            loaded_names = json.loads(record_names_json or "[]")
        except Exception:
            return {"ok": False, "deleted_count": 0, "message": "要删除的解析记录列表格式不正确。"}
        if not isinstance(loaded_names, list):
            return {"ok": False, "deleted_count": 0, "message": "要删除的解析记录列表格式不正确。"}

        seen_names: set[str] = set()
        requested_names: list[str] = []
        for value in loaded_names:
            name = str(value or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                requested_names.append(name)

        if not requested_names:
            return {"ok": False, "deleted_count": 0, "message": "当前列表没有可删除的解析记录。"}

        for name in requested_names:
            if not frappe.db.exists("Overseas Cost Attachment", name):
                skipped_items.append({"name": name, "reason": "记录不存在"})
                continue
            row = frappe.db.get_value(
                "Overseas Cost Attachment",
                name,
                ["name", "source_type", "attachment_type"],
                as_dict=True,
            ) or {}
            if row.get("source_type") != "Voucher" or row.get("attachment_type") != "Tax Certificate":
                skipped_items.append({"name": name, "reason": "不是完税凭证解析记录"})
                continue
            target_names.append(row.get("name") or name)
    elif record_name:
        if not frappe.db.exists("Overseas Cost Attachment", record_name):
            return {"ok": False, "record_name": record_name, "deleted_count": 0, "message": "未找到对应的完税凭证解析记录。"}
        row = frappe.db.get_value(
            "Overseas Cost Attachment",
            record_name,
            ["name", "source_type", "attachment_type"],
            as_dict=True,
        ) or {}
        if row.get("source_type") != "Voucher" or row.get("attachment_type") != "Tax Certificate":
            return {"ok": False, "record_name": record_name, "deleted_count": 0, "message": "该记录不是完税凭证解析记录，未删除。"}
        target_names = [record_name]
    else:
        if not batch_name:
            return {"ok": False, "deleted_count": 0, "message": "请先选择要删除记录的批次。"}
        resolved_batch = _find_tax_certificate_batch({}, batch_name=batch_name)
        if not resolved_batch or not resolved_batch.get("name"):
            return {"ok": False, "batch_name": batch_name, "deleted_count": 0, "message": "未找到对应批次，未删除解析记录。"}
        rows = _query_tax_certificate_attachment_records(batch_name=resolved_batch.get("name"), limit=1000)
        target_names = [row.get("name") for row in rows if row.get("name")]

    if not target_names:
        return {
            "ok": True,
            "batch_name": batch_name or "",
            "record_name": record_name or "",
            "record_names": [],
            "deleted_count": 0,
            "skipped_count": len(skipped_items),
            "skipped_items": skipped_items,
            "message": "当前没有可删除的完税凭证解析记录。",
        }

    for name in target_names:
        frappe.delete_doc("Overseas Cost Attachment", name, ignore_permissions=True)
    frappe.db.commit()
    skipped_count = len(skipped_items)
    message = f"已删除 {len(target_names)} 条完税凭证解析记录。"
    if skipped_count:
        message += f"另有 {skipped_count} 条已不存在或不是完税凭证记录，已跳过。"
    return {
        "ok": True,
        "batch_name": batch_name or "",
        "record_name": record_name or "",
        "record_names": target_names,
        "deleted_count": len(target_names),
        "skipped_count": skipped_count,
        "skipped_items": skipped_items,
        "deleted_names": target_names,
        "message": message,
    }


def resolve_tax_certificate_reconciliation(
    *,
    record_name: str | None = None,
    resolution_action: str | None = None,
    adjusted_tax_total_mxn: float | str | None = None,
    remark: str | None = None,
) -> dict:
    """保存完税凭证差异的人工处理结果，不写入成本字段。"""

    if not record_name:
        return {"ok": False, "message": "请传入要处理的完税凭证解析记录。"}
    if not _has_frappe_db_context():
        return {
            "ok": False,
            "dry_run": True,
            "record_name": record_name,
            "message": "当前未连接 Frappe，无法保存人工处理结果。",
        }
    if not frappe.db.exists("Overseas Cost Attachment", record_name):
        return {"ok": False, "record_name": record_name, "message": "未找到对应的完税凭证解析记录。"}

    fields = [
        "name",
        "source_type",
        "attachment_type",
        "parse_result_json",
        "mapped_result_json",
        "remark",
    ]
    row = frappe.db.get_value("Overseas Cost Attachment", record_name, fields, as_dict=True) or {}
    if row.get("source_type") != "Voucher" or row.get("attachment_type") != "Tax Certificate":
        return {"ok": False, "record_name": record_name, "message": "该附件记录不是完税凭证解析记录。"}

    mapped_result = _json_loads(row.get("mapped_result_json")) or {}
    resolution = _build_tax_certificate_manual_resolution(
        mapped_result=mapped_result,
        resolution_action=resolution_action,
        adjusted_tax_total_mxn=adjusted_tax_total_mxn,
        remark=remark,
        operator_name=getattr(frappe.session, "user", "") if getattr(frappe, "session", None) else "",
    )
    if not resolution["ok"]:
        return {
            "ok": False,
            "record_name": record_name,
            "message": resolution["message"],
        }

    history = mapped_result.get("manual_resolution_history")
    if not isinstance(history, list):
        history = []
    existing_resolution = mapped_result.get("manual_resolution")
    if existing_resolution and (not history or history[-1] != existing_resolution):
        history.append(existing_resolution)
    history.append(resolution["resolution"])

    mapped_result["manual_resolution"] = resolution["resolution"]
    mapped_result["manual_resolution_history"] = history[-20:]
    mapped_result["status"] = resolution["resolution"]["status"]
    mapped_result["status_label"] = resolution["resolution"]["status_label"]
    mapped_result["message"] = resolution["resolution"]["message"]

    remark_text = _manual_resolution_remark(row.get("remark"), resolution["resolution"])
    frappe.db.set_value(
        "Overseas Cost Attachment",
        record_name,
        {
            "mapped_result_json": _json_dumps(mapped_result),
            "remark": remark_text,
        },
        update_modified=True,
    )
    frappe.db.commit()
    return get_tax_certificate_parse_record(record_name=record_name)


def extract_pdf_text(*, file_path: str | None = None, file_url: str | None = None) -> str:
    """从 PDF 中抽取文本，优先保留版面布局。"""

    text, _method = extract_pdf_text_with_method(file_path=file_path, file_url=file_url)
    return text


def extract_pdf_text_with_method(*, file_path: str | None = None, file_url: str | None = None) -> tuple[str, str]:
    """轻量版 PDF 文本抽取：优先 Poppler 版面文本，缺失时回退 pypdf。"""

    path = _resolve_pdf_file_path(file_path=file_path, file_url=file_url)
    layout_text = _extract_pdf_layout_text(path)
    if _has_meaningful_document_text(layout_text):
        return layout_text, "pdf_layout_text"
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - 取决于部署环境依赖
        raise RuntimeError("当前环境缺少 pypdf，无法解析 PDF。请先在 bench 环境安装 pypdf。") from exc

    reader = PdfReader(str(path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(f"\n--- Page {index} ---\n{text}")
    return "\n".join(pages).strip(), "pdf_text"


def _extract_pdf_layout_text(path: Path) -> str:
    """使用已安装的 Poppler 保留空格和列结构，适合后续表格行匹配。"""

    try:
        raw_text = _run_document_command(["pdftotext", "-layout", str(path), "-"], timeout=120)
    except RuntimeError:
        return ""
    pages = [page.strip() for page in raw_text.split("\f") if page.strip()]
    return "\n".join(f"--- Page {index} ---\n{page}" for index, page in enumerate(pages, start=1)).strip()


def parse_tax_certificate_text(text: str, source_name: str | None = None) -> dict:
    """从墨西哥 Pedimento / 完税凭证文本中抽取第一版核算字段。"""

    normalized = _normalize_text(text)
    line_items = _parse_pedimento_items(normalized)
    tax_totals = _parse_tax_totals(normalized)
    paid_total = _first_number(
        _search(r"IMPORTE PAGADO:[\s\S]{0,260}?\$\s*([\d,]+(?:\.\d+)?)", normalized),
        _search(r"\bTOTAL\s*\n\s*([\d,]+(?:\.\d+)?)", normalized),
    )
    tax_total_sum = round(sum(value for value in tax_totals.values() if isinstance(value, (int, float))), 2)

    dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", normalized)
    payment_date = _search(r"FECHA DE PAGO:[\s\S]{0,80}?(\d{2}/\d{2}/\d{4})", normalized) or (dates[0] if dates else "")
    entry_date = dates[0] if dates else ""
    exchange_match = re.search(
        r"TIPO\s+CAMBIO:\s*(\d+\.\d{4,6})[\s\S]{0,120}?PESO\s+BRUTO:\s*([\d,]+\.\d{3})",
        normalized,
        flags=re.IGNORECASE,
    ) or re.search(r"\n\s*\d+\s+(\d+\.\d{4,6})\s+([\d,]+\.\d{3})\s+\d+\s*\n", normalized)
    document_match = re.search(r"\n(COVE[0-9A-Z]+)\s*\n([A-Z0-9\-]+)\s*\n(\d{2}/\d{2}/\d{4})\s+([A-Z]{3})\s+([A-Z]{3})\s+([\d,]+\.\d+)", normalized)
    header = {
        "pedimento_no": _search(r"(\d{2}\s+\d{2}\s+\d{4}\s+\d{7})", normalized),
        "pedimento_short_no": _search(r"Ped\.\s*(\d+)", normalized),
        "pedimento_ref": _search(r"PEDIMENTO\s+REF:\s*([A-Z0-9]+)", normalized)
        or _search(r"\bREF:\s*([A-Z0-9]+)", normalized),
        "customs_section": _search(r"PEDIMENTO:\s*ADUANA:[\s\S]{0,60}?(\d{4})\s+(\d{3})", normalized),
        "importer_rfc": _search(r"\b([A-Z&Ñ]{3}\d{6}[A-Z0-9]{3})\b", normalized),
        "importer_name": _search(r"\b(YUEWEI SA DE CV)\b", normalized),
        "entry_date": entry_date,
        "payment_date": payment_date,
        "exchange_rate": _to_number(exchange_match.group(1)) if exchange_match else None,
        "gross_weight_kg": _to_number(exchange_match.group(2)) if exchange_match else None,
        "paid_total_mxn": paid_total,
        "line_capture": _search(r"L[ÍI]NEA DE CAPTURA:[\s\S]{0,180}?([0-9]{4}\s+[0-9A-Z]{4}\s+[0-9A-Z]{4}\s+[0-9A-Z]{4}\s+[0-9A-Z]{4})", normalized),
        "bank_operation_no": _search(r"\b(\d{14})\b", normalized),
        "sat_transaction_no": _search(r"\b(\d{20,})\b", normalized),
        "cove_no": document_match.group(1) if document_match else "",
        "invoice_no": document_match.group(2) if document_match else "",
        "invoice_date": document_match.group(3) if document_match else "",
        "incoterm": document_match.group(4) if document_match else "",
        "invoice_currency": document_match.group(5) if document_match else "",
        "invoice_value": _to_number(document_match.group(6)) if document_match else None,
        "guide_no": _search(r"NO\.\s*\(GUIA/ORDEN EMBARQUE\)/ID:\s*([A-Z0-9]+)", normalized),
        "container_no": _first_match(r"\b[A-Z]{4}\d{7}\b", normalized),
    }
    summary = {
        "item_count": len(line_items),
        "declared_item_count": _parse_declared_item_count(normalized),
        "tax_total_sum_mxn": tax_total_sum,
        "paid_total_mxn": paid_total,
        "tax_total_matches_paid_total": bool(paid_total is not None and abs(tax_total_sum - paid_total) < 0.01),
        "needs_manual_review": not line_items or paid_total is None,
    }
    validation = _build_tax_certificate_validation(
        header=header,
        summary=summary,
        tax_totals=tax_totals,
        line_items=line_items,
    )
    summary["validation_status"] = validation["status"]
    summary["validation_status_label"] = validation["status_label"]
    summary["needs_manual_review"] = validation["status"] != "passed"

    source_evidence = {}
    paid_total_evidence = _source_locator(
        normalized, r"IMPORTE\s+PAGADO|^\s*TOTAL\s*$"
    )
    if paid_total is not None and paid_total_evidence:
        source_evidence["header.paid_total_mxn"] = paid_total_evidence
    tax_patterns = {
        "dta_mxn": r"\bDTA\s+0\s+[\d,]+(?:\.\d+)?",
        "prv_mxn": r"\bPRV\s+0\s+[\d,]+(?:\.\d+)?",
        "prv_iva_mxn": r"\bIVA/PRV\s+0\s+[\d,]+(?:\.\d+)?",
        "iva_mxn": r"\bIVA\s+0\s+[\d,]+(?:\.\d+)?",
        "igi_mxn": r"\bIGI/IGE\s+0\s+[\d,]+(?:\.\d+)?",
    }
    for fieldname, pattern in tax_patterns.items():
        if tax_totals.get(fieldname) in (None, 0, 0.0):
            continue
        locator = _source_locator(normalized, pattern)
        if locator:
            source_evidence[f"tax_totals.{fieldname}"] = locator

    return {
        "source_name": source_name or "",
        "parser": "mexico_tax_certificate_pedimento",
        "parse_targets": TAX_CERTIFICATE_PARSE_TARGETS,
        "summary": summary,
        "header": header,
        "tax_totals": tax_totals,
        "line_items": line_items,
        "source_evidence": source_evidence,
        "validation": validation,
        "raw_text_sample": normalized[:1200],
    }


def _build_tax_certificate_validation(*, header: dict, summary: dict, tax_totals: dict, line_items: list[dict]) -> dict:
    checks: list[dict] = []

    def add_check(code: str, label: str, status: str, message: str, expected=None, actual=None) -> None:
        labels = {"passed": "通过", "review": "需复核", "failed": "失败"}
        checks.append(
            {
                "code": code,
                "label": label,
                "status": status,
                "status_label": labels.get(status, status),
                "message": message,
                "expected": expected,
                "actual": actual,
            }
        )

    required_fields = [
        ("pedimento_no", "报关单号"),
        ("container_no", "柜号"),
        ("payment_date", "支付日期"),
        ("exchange_rate", "汇率"),
        ("paid_total_mxn", "支付总额"),
    ]
    missing = [label for fieldname, label in required_fields if header.get(fieldname) in (None, "")]
    add_check(
        "required_header_fields",
        "凭证基础字段",
        "passed" if not missing else "failed",
        "基础字段完整" if not missing else f"缺少：{'、'.join(missing)}",
        expected="报关单号、柜号、支付日期、汇率、支付总额",
        actual="完整" if not missing else "、".join(missing),
    )

    paid_total = summary.get("paid_total_mxn")
    tax_total = summary.get("tax_total_sum_mxn")
    if paid_total is None:
        add_check("tax_total_matches_paid", "税费合计", "failed", "未识别到支付总额，无法校验税费合计", actual=tax_total)
    elif abs(float(tax_total or 0) - float(paid_total)) < 0.01:
        add_check("tax_total_matches_paid", "税费合计", "passed", "DTA/PRV/IVA/IGI 合计与支付总额一致", expected=paid_total, actual=tax_total)
    else:
        add_check("tax_total_matches_paid", "税费合计", "failed", "DTA/PRV/IVA/IGI 合计与支付总额不一致", expected=paid_total, actual=tax_total)

    declared_count = summary.get("declared_item_count")
    item_count = len(line_items)
    if declared_count is None:
        add_check("line_item_count", "商品分项数量", "review", "未识别到凭证声明的总分项数，需人工确认", actual=item_count)
    elif int(declared_count) == item_count:
        add_check("line_item_count", "商品分项数量", "passed", "识别分项数与凭证声明一致", expected=declared_count, actual=item_count)
    else:
        add_check("line_item_count", "商品分项数量", "failed", "识别分项数与凭证声明不一致", expected=declared_count, actual=item_count)

    invalid_hs_count = sum(1 for item in line_items if not re.fullmatch(r"\d{8}", str(item.get("hs_code") or "")))
    missing_name_count = sum(1 for item in line_items if not item.get("import_name"))
    if not line_items:
        add_check("line_item_fields", "商品分项字段", "failed", "未识别到商品分项")
    elif invalid_hs_count or missing_name_count:
        add_check(
            "line_item_fields",
            "商品分项字段",
            "failed",
            f"HS 编码异常 {invalid_hs_count} 条，海关进口名称缺失 {missing_name_count} 条",
            expected="每条分项有 8 位 HS 编码和海关进口名称",
            actual=f"{item_count} 条",
        )
    else:
        add_check("line_item_fields", "商品分项字段", "passed", "每条分项均识别到 HS 编码和海关进口名称", actual=f"{item_count} 条")

    missing_tax_count = sum(1 for item in line_items if not item.get("taxes"))
    add_check(
        "line_item_tax_fields",
        "分项税率税额",
        "passed" if line_items and not missing_tax_count else "review",
        "每条分项均识别到税率/税额" if line_items and not missing_tax_count else f"有 {missing_tax_count} 条分项缺少税率/税额，需人工复核",
        actual=f"{missing_tax_count} 条缺失",
    )

    status = "passed"
    if any(check["status"] == "failed" for check in checks):
        status = "failed"
    elif any(check["status"] == "review" for check in checks):
        status = "review"

    status_label = {"passed": "通过", "review": "需复核", "failed": "失败"}[status]
    return {
        "status": status,
        "status_label": status_label,
        "checks": checks,
        "failed_count": sum(1 for check in checks if check["status"] == "failed"),
        "review_count": sum(1 for check in checks if check["status"] == "review"),
        "passed_count": sum(1 for check in checks if check["status"] == "passed"),
    }


def build_tax_certificate_reconciliation_preview(parsed: dict, batch_name: str | None = None) -> dict:
    """生成完税凭证与系统批次的对比预览，不写库。"""

    if not _has_frappe_db_context():
        return _build_tax_certificate_reconciliation(
            parsed=parsed,
            batch=None,
            items=[],
            requested_batch_name=batch_name,
            dry_run=True,
        )

    batch = _find_tax_certificate_batch(parsed.get("header") or {}, batch_name=batch_name)
    if not batch:
        return _build_tax_certificate_reconciliation(
            parsed=parsed,
            batch=None,
            items=[],
            requested_batch_name=batch_name,
        )

    filters = {"batch": batch["name"], "is_excluded": 0}
    if batch.get("current_version"):
        filters["version"] = batch["current_version"]

    items = frappe.get_all(
        "Overseas Cost Item",
        filters=filters,
        fields=[
            "name",
            "row_no",
            "material_code",
            "product_name",
            "import_name",
            "hs_code",
            "quantity",
            "goods_value",
            "cc_anti_dumping",
            "igi_amount",
            "iva_amount",
            "dta",
            "prv_duty",
            "prv_iva",
            "import_tax_total",
            "mexico_customs_mxn",
            "mexico_customs_rmb",
        ],
        order_by="row_no asc",
        limit_page_length=10000,
    )
    return _build_tax_certificate_reconciliation(
        parsed=parsed,
        batch=batch,
        items=items,
        requested_batch_name=batch_name,
    )


def _build_tax_certificate_reconciliation(
    *,
    parsed: dict,
    batch: dict | None,
    items: list[dict],
    requested_batch_name: str | None = None,
    dry_run: bool = False,
) -> dict:
    header = parsed.get("header") or {}
    summary = parsed.get("summary") or {}
    validation = parsed.get("validation") or {}
    voucher_total = _first_number(summary.get("paid_total_mxn"), summary.get("tax_total_sum_mxn"))
    system_summary = _summarize_system_tax_items(items)
    system_total = system_summary["system_import_tax_total_mxn"]
    diff = None if voucher_total is None or system_total is None else _round_money(float(voucher_total) - float(system_total), 6)
    checks: list[dict] = []

    def add_check(code: str, label: str, status: str, message: str, expected=None, actual=None) -> None:
        labels = {"passed": "通过", "review": "需复核", "failed": "失败"}
        checks.append(
            {
                "code": code,
                "label": label,
                "status": status,
                "status_label": labels.get(status, status),
                "message": message,
                "expected": expected,
                "actual": actual,
            }
        )

    if dry_run:
        add_check("frappe_connection", "系统批次连接", "review", "当前未连接 Frappe，只能查看凭证解析结果")
    elif batch:
        expected_match = _batch_match_target(batch)
        actual_match = _voucher_match_target(header)
        match_ok = _batch_matches_certificate(batch, header)
        add_check(
            "batch_match",
            "批次匹配",
            "passed" if match_ok else "review",
            "凭证柜号/报关单号已匹配系统批次" if match_ok else "当前批次与凭证柜号/报关单号不完全一致，请确认是否选错批次",
            expected=expected_match,
            actual=actual_match,
        )
    else:
        add_check(
            "batch_match",
            "批次匹配",
            "failed",
            "未根据当前批次、柜号或报关单号匹配到系统批次",
            expected=requested_batch_name or "系统中存在对应批次",
            actual=_voucher_match_target(header),
        )

    validation_status = validation.get("status")
    add_check(
        "voucher_validation",
        "凭证解析校验",
        "passed" if validation_status == "passed" else "failed" if validation_status == "failed" else "review",
        f"完税凭证解析校验{validation.get('status_label') or '未完成'}",
        expected="通过",
        actual=validation.get("status_label") or "--",
    )

    if not batch:
        add_check("system_tax_total", "系统税费金额", "review", "未匹配批次，无法读取系统税费金额")
    elif not items:
        add_check("system_tax_total", "系统税费金额", "review", "当前批次没有可对比的物料明细")
    elif system_total is None:
        add_check("system_tax_total", "系统税费金额", "review", "系统当前物料税费为空，后续需由凭证或人工补齐", expected=voucher_total, actual="空")
    elif voucher_total is None:
        add_check("system_tax_total", "系统税费金额", "failed", "凭证支付总额为空，无法比较系统税费", actual=system_total)
    elif abs(diff or 0) < 0.01:
        add_check("system_tax_total", "系统税费金额", "passed", "凭证税费与系统当前税费一致", expected=voucher_total, actual=system_total)
    else:
        add_check("system_tax_total", "系统税费金额", "review", "凭证税费与系统当前税费存在差额，后续用于多退少补复核", expected=voucher_total, actual=system_total)

    declared_count = summary.get("declared_item_count")
    item_count = len(items)
    if not batch:
        add_check("system_item_count", "系统物料行数", "review", "未匹配批次，无法比较系统物料行数")
    elif declared_count is None:
        add_check("system_item_count", "系统物料行数", "review", "凭证未识别声明分项数，无法比较行数", actual=item_count)
    elif item_count == int(declared_count):
        add_check("system_item_count", "系统物料行数", "passed", "系统物料行数与凭证分项数一致", expected=declared_count, actual=item_count)
    else:
        add_check("system_item_count", "系统物料行数", "review", "系统 SKU 行数与凭证分项数不一致，可能是合并/拆分口径差异", expected=declared_count, actual=item_count)

    status = "matched"
    if any(check["status"] == "failed" for check in checks):
        status = "unmatched" if not batch else "failed"
    elif system_total is None:
        status = "pending"
    elif any(check["status"] == "review" for check in checks):
        status = "review"

    status_labels = {
        "matched": "可对比",
        "review": "需复核",
        "pending": "待补系统税费",
        "unmatched": "未匹配批次",
        "failed": "不可用",
    }
    return {
        "status": status,
        "status_label": status_labels.get(status, status),
        "requested_batch_name": requested_batch_name or "",
        "batch": _public_batch_snapshot(batch),
        "voucher": {
            "customs_no": header.get("pedimento_no") or "",
            "container_no": header.get("container_no") or "",
            "pedimento_ref": header.get("pedimento_ref") or header.get("pedimento_short_no") or "",
            "payment_date": header.get("payment_date") or "",
            "paid_total_mxn": voucher_total,
            "item_count": summary.get("item_count") or 0,
            "declared_item_count": declared_count,
        },
        "system": system_summary,
        "difference": {
            "tax_total_diff_mxn": diff,
            "abs_tax_total_diff_mxn": None if diff is None else _round_money(abs(diff), 6),
            "direction_label": _tax_difference_direction(diff),
        },
        "checks": checks,
        "failed_count": sum(1 for check in checks if check["status"] == "failed"),
        "review_count": sum(1 for check in checks if check["status"] == "review"),
        "passed_count": sum(1 for check in checks if check["status"] == "passed"),
        "message": "对比结果仅用于复核，不会自动写入成本或生成补差。",
    }


def _summarize_system_tax_items(items: list[dict]) -> dict:
    total = 0.0
    has_tax_value = False
    rows_with_tax = 0
    rows_missing_tax = 0
    source_counts = {"import_tax_total": 0, "component_fields": 0}
    hs_codes = set()

    for item in items:
        if item.get("hs_code"):
            hs_codes.add(str(item.get("hs_code")))
        import_tax_total = _to_number(item.get("import_tax_total")) or 0
        component_total = sum(
            _to_number(item.get(fieldname)) or 0
            for fieldname in ("cc_anti_dumping", "igi_amount", "iva_amount", "dta", "prv_duty", "prv_iva")
        )
        if import_tax_total:
            row_tax = import_tax_total
            source_counts["import_tax_total"] += 1
        else:
            row_tax = component_total
            if component_total:
                source_counts["component_fields"] += 1

        if row_tax:
            has_tax_value = True
            rows_with_tax += 1
            total += row_tax
        else:
            rows_missing_tax += 1

    return {
        "item_count": len(items),
        "rows_with_tax_count": rows_with_tax,
        "rows_missing_tax_count": rows_missing_tax,
        "system_import_tax_total_mxn": _round_money(total, 6) if has_tax_value else None,
        "tax_source": _system_tax_source_label(source_counts),
        "hs_code_count": len(hs_codes),
    }


def _build_tax_certificate_attachment_values(
    *,
    parsed: dict,
    batch: dict,
    source_name: str | None = None,
    file_path: str | None = None,
    file_url: str | None = None,
) -> dict:
    header = parsed.get("header") or {}
    parse_snapshot = {
        "source_name": parsed.get("source_name") or source_name or "",
        "parser": parsed.get("parser") or "",
        "parse_targets": parsed.get("parse_targets") or [],
        "summary": parsed.get("summary") or {},
        "header": header,
        "tax_totals": parsed.get("tax_totals") or {},
        "line_items": parsed.get("line_items") or [],
        "validation": parsed.get("validation") or {},
        "fx_sync": parsed.get("fx_sync") or {},
        "cost_refresh": parsed.get("cost_refresh") or {},
    }
    reconciliation_snapshot = parsed.get("reconciliation") or {}
    file_ref = file_url or file_path or parsed.get("file_url") or parsed.get("file_path") or ""
    file_name = source_name or parsed.get("file_name") or _file_name_from_ref(file_ref)
    return {
        "batch": batch.get("name") or "",
        "version": batch.get("current_version") or "",
        "source_type": "Voucher",
        "attachment_type": "Tax Certificate",
        "source_doc_no": header.get("pedimento_no") or header.get("pedimento_ref") or header.get("pedimento_short_no") or "",
        "file_name": file_name,
        "file_url": file_ref,
        "parse_status": "Parsed",
        "parse_result_json": _json_dumps(parse_snapshot),
        "mapped_result_json": _json_dumps(reconciliation_snapshot),
        "remark": "完税凭证解析快照，仅用于复核，未写入成本字段。",
    }


def _sync_tax_certificate_identity(*, parsed: dict, batch: dict, source_name: str = "") -> dict:
    """把已匹配凭证的报关单号补入批次与物料行，供主查询追溯。"""

    header = parsed.get("header") or {}
    customs_no = str(header.get("pedimento_no") or "").strip()
    batch_name = str(batch.get("name") or "").strip()
    version_name = str(batch.get("current_version") or "").strip()
    current_customs_no = str(batch.get("customs_no") or "").strip()
    if not customs_no or not batch_name:
        return {"action": "skipped", "reason": "完税凭证或匹配批次缺少报关单号。"}
    if current_customs_no and _compact_match_value(current_customs_no) != _compact_match_value(customs_no):
        return {
            "action": "conflict",
            "reason": "当前批次已有不同报关单号，未用凭证覆盖。",
            "existing_customs_no": current_customs_no,
            "voucher_customs_no": customs_no,
        }

    batch_updated = False
    if not current_customs_no:
        frappe.db.set_value("Overseas Cost Batch", batch_name, "customs_no", customs_no, update_modified=True)
        batch_updated = True

    item_updated_count = 0
    if version_name:
        items = frappe.get_all(
            "Overseas Cost Item",
            filters={"batch": batch_name, "version": version_name, "is_excluded": 0},
            fields=["name", "customs_no"],
            limit_page_length=10000,
        )
        for item in items:
            if str(item.get("customs_no") or "").strip():
                continue
            frappe.db.set_value("Overseas Cost Item", item.get("name"), "customs_no", customs_no, update_modified=False)
            item_updated_count += 1

    if batch_updated or item_updated_count:
        source_label = source_name or customs_no
        _insert_fx_sync_audit_log(
            batch_name=batch_name,
            version_name=version_name,
            field_name="customs_no",
            old_value=current_customs_no,
            new_value={"customs_no": customs_no, "item_updated_count": item_updated_count},
            remark=f"完税凭证追溯字段同步：来源 {source_label}，仅补空的报关单号。",
        )
        return {
            "action": "updated",
            "customs_no": customs_no,
            "batch_updated": batch_updated,
            "item_updated_count": item_updated_count,
        }
    return {"action": "unchanged", "customs_no": customs_no, "item_updated_count": 0}


def _numbers_close(left, right, tolerance: float = 0.000001) -> bool:
    left_number = _to_number(left)
    right_number = _to_number(right)
    if left_number is None and right_number is None:
        return True
    if left_number is None or right_number is None:
        return False
    return abs(float(left_number) - float(right_number)) <= tolerance


def _append_version_remark(existing_remark: str | None, line: str) -> str:
    base = str(existing_remark or "").strip()
    if not base:
        return line
    if line in base:
        return base
    return f"{base}\n{line}".strip()


def _insert_fx_sync_audit_log(
    *,
    batch_name: str,
    version_name: str,
    field_name: str,
    old_value,
    new_value,
    remark: str,
) -> None:
    operator_name = ""
    session_user = getattr(getattr(frappe, "session", None), "user", None)
    if session_user and session_user != "Guest":
        operator_name = session_user
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


def _batch_approval_finished_at(batch: dict) -> str:
    for fieldname in ("source_finished_at", "source_finish_time", "finish_time", "complete_time", "completed_at"):
        value = batch.get(fieldname)
        if value:
            return str(value).strip()
    return ""


def _fx_sync_date_fields(fx_context: dict, *, payment_date: str, approval_finished_at: str) -> dict:
    normalized_payment_date = fx_context.get("normalized_payment_date") or fx_rate_service.normalize_payment_date(payment_date)
    normalized_approval_finished_at = (
        fx_context.get("normalized_approval_finished_at") or fx_rate_service.normalize_payment_date(approval_finished_at)
    )
    normalized_fx_rate_date = fx_context.get("normalized_fx_rate_date") or fx_context.get("normalized_date") or ""
    fx_date_source = fx_context.get("fx_date_source") or fx_context.get("date_source") or ""
    if not normalized_fx_rate_date:
        normalized_fx_rate_date = normalized_payment_date or normalized_approval_finished_at
    if not fx_date_source:
        if normalized_payment_date and normalized_fx_rate_date == normalized_payment_date:
            fx_date_source = fx_rate_service.FX_DATE_SOURCE_PAYMENT
        elif normalized_approval_finished_at and normalized_fx_rate_date == normalized_approval_finished_at:
            fx_date_source = fx_rate_service.FX_DATE_SOURCE_APPROVAL_FINISHED
        else:
            fx_date_source = fx_rate_service.FX_DATE_SOURCE_MISSING

    return {
        "payment_date": payment_date,
        "normalized_payment_date": normalized_payment_date,
        "approval_finished_at": approval_finished_at,
        "normalized_approval_finished_at": normalized_approval_finished_at,
        "fx_rate_date": fx_context.get("fx_rate_date") or fx_context.get("date") or normalized_fx_rate_date,
        "normalized_fx_rate_date": normalized_fx_rate_date,
        "fx_date_source": fx_date_source,
        "fx_date_source_label": fx_context.get("fx_date_source_label")
        or fx_context.get("date_source_label")
        or fx_rate_service.FX_DATE_SOURCE_LABELS.get(fx_date_source, "汇率日期"),
        "is_estimated_rate": bool(fx_context.get("is_estimated_rate")),
        "rate_date_message": fx_context.get("rate_date_message") or "",
    }


def _version_fx_fallback_payload(version_name: str | None, *, reason: str = "") -> dict:
    if not version_name or not _has_frappe_db_context():
        return {}
    version = frappe.db.get_value(
        "Overseas Cost Version",
        version_name,
        ["fx_usd_to_rmb", "fx_rmb_to_mxn"],
        as_dict=True,
    ) or {}
    usd_to_rmb = _to_number(version.get("fx_usd_to_rmb"))
    rmb_to_mxn = _to_number(version.get("fx_rmb_to_mxn"))
    if not usd_to_rmb and not rmb_to_mxn:
        return {}
    return {
        "usd_to_rmb": usd_to_rmb,
        "rmb_to_mxn": rmb_to_mxn,
        "fallback_usd_to_rmb": usd_to_rmb,
        "fallback_rmb_to_mxn": rmb_to_mxn,
        "fallback_rate_source": "current_version",
        "fallback_rate_source_label": "当前版本汇率（暂用）",
        "fallback_rate_reason": reason,
        "fallback_message": "汇率库缺少付款日汇率，当前成本暂用版本汇率；后续补齐汇率库后需重新确认。",
    }


def _resolve_tax_certificate_version_name(*, row: dict, mapped_result: dict) -> str:
    batch = mapped_result.get("batch") if isinstance(mapped_result.get("batch"), dict) else {}
    version_name = str(row.get("version") or batch.get("current_version") or "").strip()
    if version_name or not _has_frappe_db_context():
        return version_name

    batch_name = str(row.get("batch") or batch.get("name") or "").strip()
    if not batch_name:
        return ""
    return str(
        frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
        or ""
    ).strip()


def _enrich_tax_certificate_fx_sync_with_version_fallback(*, parse_result: dict, mapped_result: dict, row: dict) -> dict:
    if not isinstance(parse_result, dict):
        return {}
    fx_sync = parse_result.get("fx_sync")
    if not isinstance(fx_sync, dict):
        return parse_result
    if fx_sync.get("usd_to_rmb") or fx_sync.get("rmb_to_mxn"):
        return parse_result

    version_name = _resolve_tax_certificate_version_name(row=row, mapped_result=mapped_result)
    reason = fx_sync.get("reason") or fx_sync.get("message") or "汇率库缺少付款日汇率。"
    fallback = _version_fx_fallback_payload(version_name, reason=reason)
    if not fallback:
        return parse_result

    normalized_payment_date = fx_sync.get("normalized_payment_date") or fx_rate_service.normalize_payment_date(fx_sync.get("payment_date"))
    fallback_message = str(fallback.get("fallback_message") or "")
    if normalized_payment_date:
        fallback_message = f"汇率库缺少 {normalized_payment_date} 的 USD/MXN 汇率，当前成本暂用版本汇率；后续补齐汇率库后需重新确认。"
    enriched = dict(parse_result)
    enriched_fx_sync = {
        **fx_sync,
        **fallback,
        "fallback_message": fallback_message,
        "normalized_fx_rate_date": fx_sync.get("normalized_fx_rate_date") or normalized_payment_date,
        "fx_date_source": fx_sync.get("fx_date_source") or fx_rate_service.FX_DATE_SOURCE_PAYMENT,
        "fx_date_source_label": fx_sync.get("fx_date_source_label") or "真实付款日",
        "message": fallback_message,
    }
    enriched["fx_sync"] = enriched_fx_sync
    return enriched


def _sync_tax_certificate_exchange_rate_to_version(*, parsed: dict, batch: dict, source_name: str | None = None) -> dict:
    header = parsed.get("header") or {}
    payment_date = header.get("payment_date") or ""
    approval_finished_at = _batch_approval_finished_at(batch)
    resolved_fx_date = fx_rate_service.resolve_fx_rate_date(
        payment_date=payment_date,
        approval_finished_at=approval_finished_at,
    )
    fx_date_fields = _fx_sync_date_fields(
        resolved_fx_date,
        payment_date=payment_date,
        approval_finished_at=approval_finished_at,
    )
    voucher_usd_to_mxn = _to_number(header.get("exchange_rate"))
    if not fx_date_fields["normalized_fx_rate_date"]:
        return {
            "action": "skipped",
            "voucher_usd_to_mxn": voucher_usd_to_mxn,
            "reason": resolved_fx_date.get("message") or "缺少汇率日期，未自动查询汇率。",
            **fx_date_fields,
        }

    version_name = batch.get("current_version") or ""
    batch_name = batch.get("name") or ""
    if not version_name or not batch_name:
        return {
            "action": "skipped",
            "reason": "当前批次没有可更新的版本。",
            "voucher_usd_to_mxn": voucher_usd_to_mxn,
            **fx_date_fields,
        }

    fx_context = fx_rate_service.build_fx_context_for_costing(
        payment_date=payment_date,
        approval_finished_at=approval_finished_at,
    )
    fx_date_fields = _fx_sync_date_fields(
        fx_context,
        payment_date=payment_date,
        approval_finished_at=approval_finished_at,
    )
    if not fx_context.get("fx_usd_to_rmb") and not fx_context.get("fx_rmb_to_mxn"):
        reason = fx_context.get("message") or "付款日汇率缺失，未更新版本汇率。"
        fallback = _version_fx_fallback_payload(version_name, reason=reason)
        normalized_rate_date = fx_date_fields.get("normalized_fx_rate_date") or ""
        if fallback and normalized_rate_date:
            fallback["fallback_message"] = (
                f"汇率库缺少 {normalized_rate_date} 的 USD/MXN 汇率，当前成本暂用版本汇率；后续补齐汇率库后需重新确认。"
            )
        return {
            "action": "skipped",
            "reason": reason,
            "voucher_usd_to_mxn": voucher_usd_to_mxn,
            "rate_snapshots": fx_context.get("rate_snapshots") or {},
            "errors": fx_context.get("errors") or [],
            **fx_date_fields,
            **fallback,
        }

    version = frappe.db.get_value(
        "Overseas Cost Version",
        version_name,
        ["fx_usd_to_rmb", "fx_rmb_to_mxn", "remark"],
        as_dict=True,
    ) or {}
    old_usd_to_rmb = _to_number(version.get("fx_usd_to_rmb"))
    old_rmb_to_mxn = _to_number(version.get("fx_rmb_to_mxn"))
    usd_to_rmb = _to_number(fx_context.get("fx_usd_to_rmb"))
    rmb_to_mxn = _to_number(fx_context.get("fx_rmb_to_mxn"))

    updates = {}
    changed_fields = []
    if usd_to_rmb is not None and not _numbers_close(old_usd_to_rmb, usd_to_rmb):
        updates["fx_usd_to_rmb"] = usd_to_rmb
        changed_fields.append({"field_name": "fx_usd_to_rmb", "old_value": old_usd_to_rmb, "new_value": usd_to_rmb})
    if rmb_to_mxn is not None and not _numbers_close(old_rmb_to_mxn, rmb_to_mxn):
        updates["fx_rmb_to_mxn"] = rmb_to_mxn
        changed_fields.append({"field_name": "fx_rmb_to_mxn", "old_value": old_rmb_to_mxn, "new_value": rmb_to_mxn})

    source_label = source_name or header.get("pedimento_ref") or header.get("pedimento_no") or "完税凭证"
    date_label = fx_date_fields["fx_date_source_label"]
    normalized_fx_rate_date = fx_date_fields["normalized_fx_rate_date"]
    remark = (
        f"完税凭证汇率同步：来源 {source_label}，汇率日期 {normalized_fx_rate_date}（{date_label}），"
        f"USD→RMB {usd_to_rmb or '缺失'}，RMB→MXN {rmb_to_mxn or '缺失'}"
    )

    if not updates:
        return {
            "action": "unchanged",
            "version_name": version_name,
            "voucher_usd_to_mxn": voucher_usd_to_mxn,
            "rate_snapshots": fx_context.get("rate_snapshots") or {},
            "rmb_to_mxn": rmb_to_mxn,
            "usd_to_rmb": usd_to_rmb,
            "message": f"{date_label}汇率与当前版本汇率一致，未更新版本。",
            **fx_date_fields,
        }

    updates["remark"] = _append_version_remark(version.get("remark"), remark)
    frappe.db.set_value("Overseas Cost Version", version_name, updates, update_modified=True)
    frappe.db.set_value("Overseas Cost Batch", batch_name, "status", "Dirty", update_modified=True)
    for changed in changed_fields:
        _insert_fx_sync_audit_log(
            batch_name=batch_name,
            version_name=version_name,
            field_name=changed["field_name"],
            old_value=changed["old_value"],
            new_value=changed["new_value"],
            remark=remark,
        )

    return {
        "action": "updated",
        "version_name": version_name,
        "voucher_usd_to_mxn": voucher_usd_to_mxn,
        "rate_snapshots": fx_context.get("rate_snapshots") or {},
        "rmb_to_mxn": rmb_to_mxn,
        "usd_to_rmb": usd_to_rmb,
        "changed_fields": changed_fields,
        "message": (
            "已按付款审批完成日暂估汇率更新当前版本，批次已标记为待重算；后续拿到真实付款日后需重算确认。"
            if fx_date_fields.get("is_estimated_rate")
            else "已按真实付款日汇率更新当前版本，批次已标记为待重算。"
        ),
        **fx_date_fields,
    }


def _refresh_costing_after_fx_sync(*, fx_sync: dict | None, batch: dict) -> dict:
    if (fx_sync or {}).get("action") != "updated":
        return {"action": "skipped", "reason": "版本汇率未变化，不需要自动刷新核算。"}
    batch_name = batch.get("name") or ""
    version_name = batch.get("current_version") or ""
    if not batch_name or not version_name:
        return {"action": "skipped", "reason": "当前批次或版本为空。"}

    purchase_sync = {}
    recalculate_sync = {}
    try:
        from overseas_costing.services import import_service

        purchase_sync = import_service.apply_linked_purchase_expense_fillable_fields(
            batch_name=batch_name,
            version_name=version_name,
            recalculate_after_writeback=False,
        )
    except Exception as exc:
        return {
            "action": "failed",
            "stage": "purchase_sync",
            "message": f"汇率更新后自动同步采购字段失败：{exc}",
        }

    try:
        from overseas_costing.services.calculate_service import recalculate_batch

        recalculate_sync = recalculate_batch(batch_name=batch_name, version_name=version_name)
    except Exception as exc:
        recalculate_sync = {
            "ok": False,
            "message": f"汇率更新后自动重算失败：{exc}",
        }

    return {
        "action": "refreshed",
        "purchase_sync": purchase_sync,
        "recalculate_sync": recalculate_sync,
        "updated_purchase_rows": purchase_sync.get("updated_count") or 0,
        "changed_purchase_fields": purchase_sync.get("changed_field_count") or 0,
        "recalculated": bool(recalculate_sync.get("ok")),
        "message": "汇率更新后已尝试重新同步采购字段并重算。",
    }


def _tax_certificate_save_message(fx_sync: dict | None = None, cost_refresh: dict | None = None) -> str:
    fx_sync = fx_sync or {}
    cost_refresh = cost_refresh or {}
    date_label = fx_sync.get("fx_date_source_label") or "汇率日期"
    if fx_sync.get("action") == "updated":
        date_note = (
            "付款审批完成日暂估汇率"
            if fx_sync.get("is_estimated_rate")
            else f"{date_label}汇率"
        )
        if cost_refresh.get("action") == "refreshed" and cost_refresh.get("recalculated"):
            return f"完税凭证解析结果已保存；{date_note}已同步到当前版本，并已尝试重新同步采购字段和重算。"
        return f"完税凭证解析结果已保存；{date_note}已同步到当前版本，批次已标记为待重算。"
    if fx_sync.get("action") == "unchanged":
        return f"完税凭证解析结果已保存；{date_label}汇率与当前版本一致。"
    return "完税凭证解析结果已保存到附件记录，未写入成本字段。"


def _find_existing_tax_certificate_attachment(values: dict) -> str | None:
    filters = {
        "batch": values.get("batch"),
        "attachment_type": "Tax Certificate",
    }
    if values.get("source_doc_no"):
        filters["source_doc_no"] = values["source_doc_no"]
    elif values.get("file_url"):
        filters["file_url"] = values["file_url"]
    else:
        return None

    rows = frappe.get_all(
        "Overseas Cost Attachment",
        filters=filters,
        fields=["name"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return rows[0]["name"] if rows else None


def _query_tax_certificate_attachment_records(*, batch_name: str | None = None, limit: int = 20) -> list[dict]:
    filters = {
        "source_type": "Voucher",
        "attachment_type": "Tax Certificate",
    }
    if batch_name:
        filters["batch"] = batch_name
    return frappe.get_all(
        "Overseas Cost Attachment",
        filters=filters,
        fields=[
            "name",
            "batch",
            "version",
            "source_doc_no",
            "file_name",
            "file_url",
            "parse_status",
            "parse_result_json",
            "mapped_result_json",
            "modified",
            "creation",
        ],
        order_by="modified desc",
        limit_page_length=limit,
    )


def _build_tax_certificate_record_summary(row: dict) -> dict:
    parse_result = _json_loads(row.get("parse_result_json")) or {}
    mapped_result = _json_loads(row.get("mapped_result_json")) or {}
    header = parse_result.get("header") or {}
    summary = parse_result.get("summary") or {}
    validation = parse_result.get("validation") or {}
    voucher = mapped_result.get("voucher") or {}
    system = mapped_result.get("system") or {}
    difference = mapped_result.get("difference") or {}
    batch = mapped_result.get("batch") or {}
    manual_resolution = mapped_result.get("manual_resolution") or {}
    return {
        "name": row.get("name") or "",
        "batch": batch or {"name": row.get("batch") or ""},
        "version": row.get("version") or "",
        "source_doc_no": row.get("source_doc_no") or header.get("pedimento_no") or "",
        "file_name": row.get("file_name") or parse_result.get("source_name") or "",
        "file_url": row.get("file_url") or "",
        "parse_status": row.get("parse_status") or "",
        "modified": row.get("modified") or "",
        "creation": row.get("creation") or "",
        "customs_no": header.get("pedimento_no") or voucher.get("customs_no") or row.get("source_doc_no") or "",
        "container_no": header.get("container_no") or voucher.get("container_no") or "",
        "pedimento_ref": header.get("pedimento_ref") or voucher.get("pedimento_ref") or "",
        "payment_date": header.get("payment_date") or voucher.get("payment_date") or "",
        "paid_total_mxn": summary.get("paid_total_mxn") if summary.get("paid_total_mxn") is not None else voucher.get("paid_total_mxn"),
        "tax_total_sum_mxn": summary.get("tax_total_sum_mxn"),
        "system_tax_total_mxn": system.get("system_import_tax_total_mxn"),
        "tax_total_diff_mxn": difference.get("tax_total_diff_mxn"),
        "direction_label": difference.get("direction_label") or "",
        "item_count": summary.get("item_count") or voucher.get("item_count") or 0,
        "declared_item_count": summary.get("declared_item_count") or voucher.get("declared_item_count"),
        "validation_status": validation.get("status") or summary.get("validation_status") or "",
        "validation_status_label": validation.get("status_label") or summary.get("validation_status_label") or "",
        "reconciliation_status": mapped_result.get("status") or "",
        "reconciliation_status_label": mapped_result.get("status_label") or "",
        "manual_resolution": manual_resolution,
        "manual_resolution_action": manual_resolution.get("action") or "",
        "manual_resolution_status_label": manual_resolution.get("status_label") or "",
        "resolved_tax_total_mxn": manual_resolution.get("final_tax_total_mxn"),
        "review_count": mapped_result.get("review_count") or 0,
        "failed_count": mapped_result.get("failed_count") or 0,
        "passed_count": mapped_result.get("passed_count") or 0,
    }


def _build_tax_certificate_manual_resolution(
    *,
    mapped_result: dict,
    resolution_action: str | None,
    adjusted_tax_total_mxn=None,
    remark: str | None = None,
    operator_name: str | None = None,
) -> dict:
    action = str(resolution_action or "").strip()
    actions = {
        "accept_difference": {
            "label": "确认差异可接受",
            "status": "accepted",
            "status_label": "差异已确认",
            "final_source": "system_current",
            "final_source_label": "保留系统金额，差异作为可接受尾差",
        },
        "mark_exception": {
            "label": "备注异常",
            "status": "exception",
            "status_label": "已标记异常",
            "final_source": "pending_review",
            "final_source_label": "暂不确认金额，待继续核对",
        },
        "use_voucher": {
            "label": "按凭证金额为准",
            "status": "resolved",
            "status_label": "已按凭证处理",
            "final_source": "voucher",
            "final_source_label": "完税凭证金额",
        },
        "keep_system": {
            "label": "保留系统金额",
            "status": "resolved",
            "status_label": "已保留系统金额",
            "final_source": "system_current",
            "final_source_label": "系统当前金额",
        },
        "manual_adjust": {
            "label": "手工调整金额",
            "status": "adjusted",
            "status_label": "已手工调整",
            "final_source": "manual_adjust",
            "final_source_label": "人工调整金额",
        },
    }
    if action not in actions:
        return {"ok": False, "message": "请选择人工处理方式。"}

    voucher = mapped_result.get("voucher") or {}
    system = mapped_result.get("system") or {}
    difference = mapped_result.get("difference") or {}
    voucher_total = _first_number(voucher.get("paid_total_mxn"))
    system_total = _first_number(system.get("system_import_tax_total_mxn"))
    adjusted_total = _to_number(adjusted_tax_total_mxn)
    config = actions[action]

    if action == "use_voucher" and voucher_total is None:
        return {"ok": False, "message": "凭证金额为空，不能选择按凭证金额为准。"}
    if action in {"keep_system", "accept_difference"} and system_total is None:
        return {"ok": False, "message": "系统金额为空，不能选择保留系统金额或确认差异可接受。"}
    if action == "manual_adjust" and adjusted_total is None:
        return {"ok": False, "message": "请填写手工调整后的税费金额。"}
    if action == "mark_exception" and not str(remark or "").strip():
        return {"ok": False, "message": "备注异常时请填写异常原因。"}

    if action == "use_voucher":
        final_total = voucher_total
    elif action in {"keep_system", "accept_difference"}:
        final_total = system_total
    elif action == "manual_adjust":
        final_total = adjusted_total
    else:
        final_total = None

    final_vs_system = None if final_total is None or system_total is None else _round_money(float(final_total) - float(system_total), 6)
    final_vs_voucher = None if final_total is None or voucher_total is None else _round_money(float(final_total) - float(voucher_total), 6)
    resolved_at = ""
    try:
        resolved_at = frappe.utils.now()
    except Exception:
        resolved_at = ""

    resolution = {
        "action": action,
        "action_label": config["label"],
        "status": config["status"],
        "status_label": config["status_label"],
        "final_source": config["final_source"],
        "final_source_label": config["final_source_label"],
        "voucher_tax_total_mxn": voucher_total,
        "system_tax_total_mxn": system_total,
        "original_diff_mxn": difference.get("tax_total_diff_mxn"),
        "final_tax_total_mxn": None if final_total is None else _round_money(float(final_total), 6),
        "final_diff_vs_system_mxn": final_vs_system,
        "final_diff_vs_voucher_mxn": final_vs_voucher,
        "remark": str(remark or "").strip(),
        "resolved_by": operator_name or "",
        "resolved_at": resolved_at,
        "message": _manual_resolution_message(config["label"], final_total, remark),
    }
    return {"ok": True, "resolution": resolution}


def _manual_resolution_message(action_label: str, final_total, remark: str | None = None) -> str:
    suffix = f"；备注：{str(remark).strip()}" if str(remark or "").strip() else ""
    if final_total is None:
        return f"人工处理：{action_label}，暂不确认采用金额{suffix}。"
    amount = _round_money(float(final_total), 6)
    return f"人工处理：{action_label}，采用金额 MXN {amount}{suffix}。"


def _manual_resolution_remark(existing_remark: str | None, resolution: dict) -> str:
    base = str(existing_remark or "").strip()
    line = f"人工处理：{resolution.get('action_label') or ''}"
    if resolution.get("final_tax_total_mxn") is not None:
        line += f"，采用金额 MXN {resolution.get('final_tax_total_mxn')}"
    if resolution.get("remark"):
        line += f"，备注：{resolution.get('remark')}"
    return f"{base}\n{line}".strip() if base else line


def _find_tax_certificate_batch(header: dict, batch_name: str | None = None) -> dict | None:
    fields = [
        "name",
        "batch_no",
        "customs_no",
        "waybill_no",
        "container_no",
        "current_version",
        "source_finished_at",
        "item_count",
        "total_goods_value",
        "estimated_total_cost_rmb",
        "actual_total_cost_rmb",
    ]

    def first_by(fieldname: str, value: str | None) -> dict | None:
        if not value:
            return None
        rows = frappe.get_all(
            "Overseas Cost Batch",
            filters={fieldname: value},
            fields=fields,
            order_by="modified desc",
            limit_page_length=1,
        )
        return rows[0] if rows else None

    # 完税凭证本身识别出的报关单号/柜号最可信；页面当前批次只作为兜底。
    for fieldname, value in (
        ("customs_no", header.get("pedimento_no")),
        ("waybill_no", header.get("container_no")),
        ("container_no", header.get("container_no")),
        ("batch_no", header.get("container_no")),
    ):
        batch = first_by(fieldname, value)
        if batch:
            return batch

    for value in [batch_name]:
        for fieldname in ("name", "batch_no", "customs_no", "waybill_no", "container_no"):
            batch = first_by(fieldname, value)
            if batch:
                return batch
    return None


def _has_frappe_db_context() -> bool:
    if frappe is None:
        return False
    try:
        return bool(getattr(frappe.local, "site", None)) and getattr(frappe, "db", None) is not None
    except Exception:
        return False


def _public_batch_snapshot(batch: dict | None) -> dict:
    if not batch:
        return {}
    return {
        "name": batch.get("name") or "",
        "batch_no": batch.get("batch_no") or "",
        "customs_no": batch.get("customs_no") or "",
        "waybill_no": batch.get("waybill_no") or "",
        "container_no": batch.get("container_no") or "",
        "current_version": batch.get("current_version") or "",
        "source_finished_at": batch.get("source_finished_at") or "",
        "item_count": batch.get("item_count") or 0,
        "total_goods_value": batch.get("total_goods_value"),
        "estimated_total_cost_rmb": batch.get("estimated_total_cost_rmb"),
        "actual_total_cost_rmb": batch.get("actual_total_cost_rmb"),
    }


def _batch_matches_certificate(batch: dict, header: dict) -> bool:
    customs_no = _compact_match_value(header.get("pedimento_no"))
    container_no = _compact_match_value(header.get("container_no"))
    batch_values = {
        _compact_match_value(batch.get("customs_no")),
        _compact_match_value(batch.get("waybill_no")),
        _compact_match_value(batch.get("container_no")),
        _compact_match_value(batch.get("batch_no")),
    }
    return bool((customs_no and customs_no in batch_values) or (container_no and container_no in batch_values))


def _batch_match_target(batch: dict | None) -> str:
    if not batch:
        return ""
    values = [batch.get("customs_no"), batch.get("waybill_no"), batch.get("container_no"), batch.get("batch_no")]
    return " / ".join(str(value) for value in values if value) or batch.get("name") or ""


def _voucher_match_target(header: dict) -> str:
    values = [header.get("pedimento_no"), header.get("container_no")]
    return " / ".join(str(value) for value in values if value) or "--"


def _compact_match_value(value) -> str:
    return re.sub(r"[\s\-_/]+", "", str(value or "")).upper()


def _system_tax_source_label(source_counts: dict) -> str:
    if source_counts.get("import_tax_total"):
        return "IMPUESTOS合计清关税费"
    if source_counts.get("component_fields"):
        return "IGI/IVA/DTA/PRV分项合计"
    return "未填系统税费"


def _tax_difference_direction(diff) -> str:
    if diff is None:
        return "暂无差额"
    if abs(float(diff)) < 0.01:
        return "一致"
    if diff > 0:
        return "凭证金额高于系统"
    return "凭证金额低于系统"


def _parse_tax_totals(text: str) -> dict:
    aliases = {
        "DTA": "dta_mxn",
        "PRV": "prv_mxn",
        "IVA/PRV": "prv_iva_mxn",
        "IVA": "iva_mxn",
        "IGI/IGE": "igi_mxn",
    }
    totals = {}
    for label, fieldname in aliases.items():
        match = re.search(rf"\b{re.escape(label)}\s+0\s+([\d,]+(?:\.\d+)?)\b", text)
        totals[fieldname] = _to_number(match.group(1)) if match else 0
    return totals


def _parse_declared_item_count(text: str) -> int | None:
    match = re.search(r"TOTAL DE PARTIDAS[\s\S]{0,140}?(\d{3,6})\*+", text, flags=re.IGNORECASE)
    if not match:
        return None
    token = re.sub(r"\D", "", match.group(1))
    if not token:
        return None
    if len(token) >= 3:
        value = int(token[-3:])
        if value:
            return value
    return int(token)


def _parse_pedimento_items(text: str) -> list[dict]:
    lines = [line.strip() for line in text.splitlines()]
    page_by_index: list[int] = []
    current_page = 1
    for line in lines:
        marker = re.fullmatch(r"---\s*Page\s*(\d+)\s*---", line, flags=re.IGNORECASE)
        if marker:
            current_page = int(marker.group(1))
        page_by_index.append(current_page)
    items: list[dict] = []
    index = 0
    while index < len(lines):
        item_match = _match_pedimento_item_line(lines[index])
        if not item_match:
            index += 1
            continue

        description = _next_description(lines, index + 1)
        tax_lines = _collect_item_tax_lines(lines, index + 1)
        tax_lines.update(_parse_item_tax_fields(lines[index]))
        value_line = _next_value_line(lines, index + 1)
        item_evidence = {
            "hs_code": _line_locator(lines, page_by_index, index),
            "quantity_umc": _line_locator(lines, page_by_index, index),
            "quantity_umt": _line_locator(lines, page_by_index, index),
        }
        for offset, tax_line in enumerate(lines[index + 1 : index + 9], start=index + 1):
            parsed_taxes = _parse_item_tax_fields(tax_line)
            for fieldname in parsed_taxes:
                if fieldname.endswith("_amount_mxn"):
                    item_evidence[fieldname] = _line_locator(lines, page_by_index, offset)
        items.append(
            {
                "row_no": len(items) + 1,
                "fraction_raw": item_match["fraction_raw"],
                "hs_code": item_match["hs_code"],
                "item_seq": item_match["item_seq"],
                "nico": item_match["nico"],
                "quantity_umc": item_match["quantity_umc"],
                "quantity_umt": item_match["quantity_umt"],
                "origin_country": item_match["origin_country"],
                "seller_country": item_match["seller_country"],
                "import_name": description,
                "taxes": tax_lines,
                "source_evidence": item_evidence,
                "value_line_raw": value_line,
                "needs_manual_review": not description,
            }
        )
        index += 1
    return items


def _line_locator(lines: list[str], page_by_index: list[int], index: int) -> dict:
    if index < 0 or index >= len(lines):
        return {}
    return {
        "page": page_by_index[index],
        "text_line": index + 1,
        "text_excerpt": lines[index][:500],
    }


def _match_pedimento_item_line(line: str) -> dict | None:
    patterns = (
        re.compile(
            r"^(?P<seq>\d{3})\s+(?P<hs>\d{8})\s+(?P<nico>\d{2})\s+\d+\s+\d+\s+\d+\s+"
            r"(?P<quantity_umc>[\d,]+\.\d{3})\s+\d+\s+(?P<quantity_umt>[\d,]+\.\d{5})\s+"
            r"(?P<origin>[A-Z]{3})\s+(?P<seller>[A-Z]{3})(?:\s+.*)?$"
        ),
        re.compile(
            r"^(?P<raw_fraction>\d{11})\s+(?P<nico>\d{2})\s+\d+\s+\d+\s+\d+\s+"
            r"(?P<quantity_umc>[\d,]+\.\d{3})\s+\d+\s+(?P<quantity_umt>[\d,]+\.\d{5})\s+"
            r"(?P<origin>[A-Z]{3})\s+(?P<seller>[A-Z]{3})(?:\s+.*)?$"
        ),
    )
    for pattern in patterns:
        match = pattern.match(line)
        if not match:
            continue
        groups = match.groupdict()
        raw_fraction = groups.get("raw_fraction") or f"{groups.get('hs')}{groups.get('seq') or ''}"
        hs_code = groups.get("hs") or raw_fraction[:8]
        return {
            "fraction_raw": raw_fraction,
            "hs_code": hs_code,
            "item_seq": groups.get("seq") or raw_fraction[8:],
            "nico": groups.get("nico"),
            "quantity_umc": _to_number(groups.get("quantity_umc")),
            "quantity_umt": _to_number(groups.get("quantity_umt")),
            "origin_country": groups.get("origin"),
            "seller_country": groups.get("seller"),
        }
    return None


def _next_description(lines: list[str], start: int) -> str:
    for line in lines[start : start + 5]:
        if not line or _is_noise_line(line):
            continue
        if re.match(r"^(IGI|IVA)\s+", line):
            continue
        if re.match(r"^\d", line):
            continue
        cleaned = _clean_item_description(line)
        if cleaned:
            return cleaned
    return ""


def _collect_item_tax_lines(lines: list[str], start: int) -> dict:
    taxes = {}
    for line in lines[start : start + 8]:
        if _is_item_start_line(line):
            break
        taxes.update(_parse_item_tax_fields(line))
    return taxes


def _is_item_start_line(line: str) -> bool:
    return _match_pedimento_item_line(line) is not None


def _parse_item_tax_fields(line: str) -> dict:
    match = re.search(r"\b(IGI|IVA)\s+([\d.]+)\s+\d+\s+\d+\s+([\d,]+(?:\.\d+)?)\b", line)
    if not match:
        return {}
    key = match.group(1).lower()
    return {
        f"{key}_rate": _to_number(match.group(2)),
        f"{key}_amount_mxn": _to_number(match.group(3)),
    }


def _clean_item_description(line: str) -> str:
    text = re.split(r"\s{2,}(?:IGI|IVA)\s+[\d.]+\s+\d+\s+\d+\s+[\d,]+(?:\.\d+)?", line, maxsplit=1)[0]
    return _clean_spaces(text)


def _next_value_line(lines: list[str], start: int) -> str:
    for line in lines[start : start + 8]:
        if re.match(r"^[\d,]+\s+[\d.]+$", line):
            return line
    return ""


def _is_noise_line(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "IDENTIF.",
            "COMPLEMENTO",
            "PARTIDAS",
            "AGENTE ADUANAL",
            "DECLARO BAJO PROTESTA",
        )
    )


def _resolve_pdf_file_path(*, file_path: str | None = None, file_url: str | None = None) -> Path:
    return _ensure_pdf_path(_resolve_source_file_path(file_path=file_path, file_url=file_url))


def _resolve_source_file_path(*, file_path: str | None = None, file_url: str | None = None) -> Path:
    if file_path:
        path = Path(file_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(_("未找到附件文件：{0}").format(file_path))

    if not file_url:
        raise ValueError(_("请传入 file_path 或 file_url。"))

    if frappe is not None:
        resolved_file_url = file_url
        if not resolved_file_url.startswith("/"):
            file_row = frappe.db.get_value("File", resolved_file_url, ["file_url"], as_dict=True)
            if file_row and file_row.get("file_url"):
                resolved_file_url = file_row["file_url"]
        if resolved_file_url.startswith("/private/files/"):
            relative_name = resolved_file_url.split("/private/files/", 1)[1]
            path = Path(frappe.get_site_path("private", "files", relative_name))
            if path.exists():
                return path
        if resolved_file_url.startswith("/files/"):
            relative_name = resolved_file_url.split("/files/", 1)[1]
            path = Path(frappe.get_site_path("public", "files", relative_name))
            if path.exists():
                return path

    path = Path(file_url).expanduser()
    if path.exists():
        return path
    raise FileNotFoundError(_("无法解析附件文件路径：{0}").format(file_url))


def _ensure_pdf_path(path: Path) -> Path:
    if path.suffix.lower() != ".pdf":
        raise ValueError(_("当前仅支持 .pdf 格式的完税凭证。"))
    return path


def _normalize_text(text: str | None) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _source_locator(text: str, pattern: str) -> dict:
    page = 1
    for line_no, line in enumerate(str(text or "").splitlines(), start=1):
        marker = re.fullmatch(r"---\s*Page\s*(\d+)\s*---", line.strip(), flags=re.IGNORECASE)
        if marker:
            page = int(marker.group(1))
        if re.search(pattern, line, flags=re.IGNORECASE):
            return {"page": page, "text_line": line_no, "text_excerpt": line.strip()[:500]}
    return {}


def _search(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    if len(match.groups()) == 1:
        return _clean_spaces(match.group(1))
    return " ".join(_clean_spaces(group) for group in match.groups() if group is not None)


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(0) if match else ""


def _first_number(*values) -> float | None:
    for value in values:
        number = _to_number(value)
        if number is not None:
            return number
    return None


def _to_number(value) -> float | None:
    if value in (None, ""):
        return None
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _round_money(value, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_loads(value) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _file_name_from_ref(value: str) -> str:
    text = str(value or "").split("?", 1)[0].replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""
