"""
中文用途：从前端 Demo 解析出的 excel-imported-blocks.js 导入一期海运批次。

用法示例：
bench --site development.localhost execute overseas_costing.scripts.import_parsed_excel_blocks.import_2026_yuewei_sea
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import json
from pathlib import Path

from overseas_costing.services.import_service import import_parsed_excel_blocks
from overseas_costing.utils.excel_blocks import (
    load_excel_blocks_js_file,
    select_excel_blocks,
    summarize_excel_blocks,
    to_bool,
)


DEFAULT_CANDIDATE_PATHS = [
    Path("/mnt/e/Yuewei开发/海外采购综合成本核算项目/overseas-cost/frontend-demo/excel-imported-blocks.js"),
    Path.cwd() / "frontend-demo" / "excel-imported-blocks.js",
]


def _resolve_blocks_file(file_path: str | None = None) -> Path:
    if file_path:
        path = Path(file_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(_("未找到 Excel blocks 文件：{0}").format(file_path))

    for path in DEFAULT_CANDIDATE_PATHS:
        if path.exists():
            return path

    raise FileNotFoundError(_("未找到 excel-imported-blocks.js，请通过 file_path 参数传入。"))


def preview_2026_yuewei_sea(
    file_path: str | None = None,
    include_double_clear=0,
    batch_ids: str | None = None,
    limit: int | None = None,
) -> dict:
    path = _resolve_blocks_file(file_path)
    meta, blocks = load_excel_blocks_js_file(path)
    selected = select_excel_blocks(
        blocks,
        source_sheet="2026年YUEWEI",
        transport_keyword="海运",
        include_double_clear=to_bool(include_double_clear),
        batch_ids=batch_ids,
        limit=limit,
    )
    return {
        "ok": True,
        "file_path": str(path),
        "meta": meta,
        "source_summary": summarize_excel_blocks(blocks),
        "selected_summary": summarize_excel_blocks(selected),
        "include_double_clear": to_bool(include_double_clear),
    }


def import_2026_yuewei_sea(
    file_path: str | None = None,
    include_double_clear=0,
    batch_ids: str | None = None,
    limit: int | None = None,
) -> dict:
    path = _resolve_blocks_file(file_path)
    _meta, blocks = load_excel_blocks_js_file(path)
    return import_parsed_excel_blocks(
        source_name="墨西哥进口物料综合成本核算.xlsx",
        blocks_json=json.dumps(blocks, ensure_ascii=False),
        source_sheet="2026年YUEWEI",
        transport_keyword="海运",
        include_double_clear=include_double_clear,
        batch_ids=batch_ids,
        limit=limit,
        project_collection=None,
        version_type="Estimated",
        fx_rmb_to_mxn=2.6,
    )
