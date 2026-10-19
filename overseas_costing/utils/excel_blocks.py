"""中文用途：解析前端 Demo 导出的 Excel block JSON。"""

from __future__ import annotations

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message


import json
from pathlib import Path


def _parse_window_assignment(text: str, variable_name: str):
    marker = f"window.{variable_name}"
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError(_i18n("未找到变量：{0}").format(marker))

    equal_index = text.find("=", marker_index)
    if equal_index < 0:
        raise ValueError(_i18n("变量缺少赋值：{0}").format(marker))

    start_index = equal_index + 1
    while start_index < len(text) and text[start_index].isspace():
        start_index += 1

    value, _ = json.JSONDecoder().raw_decode(text[start_index:])
    return value


def load_excel_blocks_js_text(text: str) -> tuple[dict, list[dict]]:
    """从 excel-imported-blocks.js 文本中读取 meta 和 blocks。"""

    meta = _parse_window_assignment(text, "EXCEL_IMPORTED_META")
    blocks = _parse_window_assignment(text, "EXCEL_IMPORTED_BLOCKS")
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(blocks, list):
        blocks = []
    return meta, [block for block in blocks if isinstance(block, dict)]


def load_excel_blocks_js_file(file_path: str | Path) -> tuple[dict, list[dict]]:
    path = Path(file_path).expanduser()
    return load_excel_blocks_js_text(path.read_text(encoding="utf-8"))


def normalize_batch_ids(batch_ids) -> set[str]:
    if not batch_ids:
        return set()
    if isinstance(batch_ids, str):
        values = batch_ids.split(",")
    else:
        values = list(batch_ids)
    return {str(value).strip() for value in values if str(value).strip()}


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def select_excel_blocks(
    blocks: list[dict],
    *,
    source_sheet: str | None = "2026年YUEWEI",
    transport_keyword: str = "海运",
    include_double_clear=False,
    batch_ids=None,
    limit: int | None = None,
) -> list[dict]:
    """按一期范围筛选已解析 Excel block。

    默认排除“海运双清”，因为当前一期文档把双清放在后续版本。
    """

    include_double_clear_flag = to_bool(include_double_clear)
    allowed_batch_ids = normalize_batch_ids(batch_ids)
    selected = []

    for block in blocks:
        block_id = str(block.get("id") or block.get("waybillNo") or "").strip()
        if allowed_batch_ids and block_id not in allowed_batch_ids:
            continue
        if source_sheet and block.get("sourceSheet") != source_sheet:
            continue

        transport_mode = str(block.get("transportMode") or "")
        if transport_keyword and transport_keyword not in transport_mode:
            continue
        if not include_double_clear_flag and "双清" in transport_mode:
            continue

        selected.append(block)
        if limit and len(selected) >= int(limit):
            break

    return selected


def summarize_excel_blocks(blocks: list[dict]) -> dict:
    return {
        "block_count": len(blocks),
        "item_count": sum(len(block.get("items") or []) for block in blocks),
        "batch_ids": [block.get("id") or block.get("waybillNo") for block in blocks],
    }
