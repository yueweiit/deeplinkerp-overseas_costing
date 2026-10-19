"""中文用途：从真实 Yuewei xlsx 成本总表导入一期海运批次。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from pathlib import Path

from overseas_costing.services.import_service import import_yuewei_excel_file
from overseas_costing.utils.excel_blocks import select_excel_blocks, summarize_excel_blocks, to_bool
from overseas_costing.utils.excel_workbook import parse_yuewei_excel_workbook


DEFAULT_CANDIDATE_PATHS = [
    Path("/mnt/c/Users/lin/OneDrive/Desktop/墨西哥进口物料综合成本核算.xlsx"),
    Path("/mnt/e/Yuewei开发/海外采购综合成本核算项目/overseas-cost/data/墨西哥进口物料综合成本核算.xlsx"),
    Path.cwd() / "data" / "墨西哥进口物料综合成本核算.xlsx",
]


def _resolve_workbook_file(file_path: str | None = None) -> Path:
    if file_path:
        path = Path(file_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(_("未找到 Yuewei Excel 文件：{0}").format(file_path))

    for path in DEFAULT_CANDIDATE_PATHS:
        if path.exists():
            return path

    raise FileNotFoundError(_("未找到墨西哥进口物料综合成本核算.xlsx，请通过 file_path 参数传入。"))


def preview_2026_yuewei_sea(
    file_path: str | None = None,
    include_double_clear=0,
    batch_ids: str | None = None,
    limit: int | None = None,
) -> dict:
    path = _resolve_workbook_file(file_path)
    meta, blocks = parse_yuewei_excel_workbook(path, sheet_name="2026年YUEWEI")
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
        "parser_meta": meta,
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
    path = _resolve_workbook_file(file_path)
    return import_yuewei_excel_file(
        source_name=path.name,
        file_path=str(path),
        source_sheet="2026年YUEWEI",
        transport_keyword="海运",
        include_double_clear=include_double_clear,
        batch_ids=batch_ids,
        limit=limit,
        version_type="Estimated",
        fx_rmb_to_mxn=2.6,
    )
