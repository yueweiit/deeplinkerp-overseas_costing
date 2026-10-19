"""中文用途：通用 Excel 工作簿预览/导入脚本。

用法示例：
OVERSEAS_COST_EXCEL_FILE=/mnt/e/.../data/current_air_import.xlsx bench --site development.localhost execute overseas_costing.scripts.import_excel_workbook.preview_from_env
OVERSEAS_COST_EXCEL_FILE=/mnt/e/.../data/current_air_import.xlsx bench --site development.localhost execute overseas_costing.scripts.import_excel_workbook.import_from_env
python -m overseas_costing.scripts.import_excel_workbook --file data/current_air_import.xlsx --preview
"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import argparse
import json
import os
from pathlib import Path

from overseas_costing.services.import_service import import_yuewei_excel_file
from overseas_costing.utils.excel_blocks import select_excel_blocks, summarize_excel_blocks, to_bool
from overseas_costing.utils.excel_workbook import parse_yuewei_excel_workbook


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _to_int(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _to_float(value: str | float | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return to_bool(value)


def _resolve_file_path(file_path: str | None = None) -> Path:
    resolved = _clean(file_path) or _clean(os.environ.get("OVERSEAS_COST_EXCEL_FILE"))
    if not resolved:
        raise FileNotFoundError(_("缺少 Excel 文件路径，请传入 file_path 或设置 OVERSEAS_COST_EXCEL_FILE。"))

    path = Path(resolved).expanduser()
    if not path.exists():
        raise FileNotFoundError(_("未找到 Excel 文件：{0}").format(path))
    return path


def _env_options() -> dict:
    return {
        "source_sheet": _clean(os.environ.get("OVERSEAS_COST_SOURCE_SHEET")) or None,
        "transport_keyword": _clean(os.environ.get("OVERSEAS_COST_TRANSPORT_KEYWORD")),
        "include_double_clear": _env_bool("OVERSEAS_COST_INCLUDE_DOUBLE_CLEAR", default=True),
        "batch_ids": _clean(os.environ.get("OVERSEAS_COST_BATCH_IDS")) or None,
        "limit": _to_int(os.environ.get("OVERSEAS_COST_LIMIT")),
        "source_name": _clean(os.environ.get("OVERSEAS_COST_SOURCE_NAME")) or None,
        "fx_usd_to_rmb": _to_float(os.environ.get("OVERSEAS_COST_FX_USD_TO_RMB")),
        "fx_rmb_to_mxn": _to_float(os.environ.get("OVERSEAS_COST_FX_RMB_TO_MXN")),
    }


def preview_excel_workbook(
    file_path: str | None = None,
    *,
    source_sheet: str | None = None,
    transport_keyword: str = "",
    include_double_clear=1,
    batch_ids: str | None = None,
    limit: int | None = None,
) -> dict:
    """解析真实 xlsx 并返回可导入批次预览，不写数据库。"""

    path = _resolve_file_path(file_path)
    meta, blocks = parse_yuewei_excel_workbook(path, sheet_name=source_sheet)
    selected_blocks = select_excel_blocks(
        blocks,
        source_sheet=meta.get("sourceSheet") or source_sheet,
        transport_keyword=transport_keyword,
        include_double_clear=to_bool(include_double_clear),
        batch_ids=batch_ids,
        limit=limit,
    )
    return {
        "ok": True,
        "file_path": str(path),
        "parser_meta": meta,
        "source_summary": summarize_excel_blocks(blocks),
        "selected_summary": summarize_excel_blocks(selected_blocks),
        "selection": {
            "source_sheet": meta.get("sourceSheet") or source_sheet or "",
            "transport_keyword": transport_keyword or "",
            "include_double_clear": to_bool(include_double_clear),
            "batch_ids": batch_ids or "",
            "limit": limit,
        },
    }


def import_excel_workbook(
    file_path: str | None = None,
    *,
    source_name: str | None = None,
    source_sheet: str | None = None,
    transport_keyword: str = "",
    include_double_clear=1,
    batch_ids: str | None = None,
    limit: int | None = None,
    fx_usd_to_rmb: float | None = None,
    fx_rmb_to_mxn: float | None = None,
) -> dict:
    """导入真实 xlsx，内部复用统一 Excel 解析与落库链路。"""

    path = _resolve_file_path(file_path)
    return import_yuewei_excel_file(
        source_name=source_name or path.name,
        file_path=str(path),
        source_sheet=source_sheet,
        transport_keyword=transport_keyword,
        include_double_clear=include_double_clear,
        batch_ids=batch_ids,
        limit=limit,
        version_type="Estimated",
        fx_usd_to_rmb=fx_usd_to_rmb,
        fx_rmb_to_mxn=fx_rmb_to_mxn,
    )


def preview_from_env() -> dict:
    """从环境变量读取参数并预览，适合 bench execute 调试。"""

    options = _env_options()
    return preview_excel_workbook(
        file_path=os.environ.get("OVERSEAS_COST_EXCEL_FILE"),
        source_sheet=options["source_sheet"],
        transport_keyword=options["transport_keyword"],
        include_double_clear=options["include_double_clear"],
        batch_ids=options["batch_ids"],
        limit=options["limit"],
    )


def import_from_env() -> dict:
    """从环境变量读取参数并导入，适合 bench execute 调试。"""

    options = _env_options()
    return import_excel_workbook(
        file_path=os.environ.get("OVERSEAS_COST_EXCEL_FILE"),
        source_name=options["source_name"],
        source_sheet=options["source_sheet"],
        transport_keyword=options["transport_keyword"],
        include_double_clear=options["include_double_clear"],
        batch_ids=options["batch_ids"],
        limit=options["limit"],
        fx_usd_to_rmb=options["fx_usd_to_rmb"],
        fx_rmb_to_mxn=options["fx_rmb_to_mxn"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="预览或导入海外采购成本 Excel 工作簿")
    parser.add_argument("--file", required=True, help="xlsx/xlsm 文件路径")
    parser.add_argument("--sheet", default="", help="工作表名，可留空自动识别")
    parser.add_argument("--transport-keyword", default="", help="运输方式关键词，例如 海运/空运")
    parser.add_argument("--exclude-double-clear", action="store_true", help="排除双清批次")
    parser.add_argument("--batch-ids", default="", help="只处理指定批次，多个用逗号分隔")
    parser.add_argument("--limit", type=int, default=None, help="限制处理批次数")
    parser.add_argument("--source-name", default="", help="导入来源文件名，默认取文件名")
    parser.add_argument("--fx-usd-to-rmb", type=float, default=None, help="USD->RMB 汇率")
    parser.add_argument("--fx-rmb-to-mxn", type=float, default=None, help="RMB->MXN 汇率")
    parser.add_argument("--preview", action="store_true", help="只预览，不写库")
    parser.add_argument("--import", dest="do_import", action="store_true", help="执行导入")
    args = parser.parse_args()

    if args.do_import and not args.preview:
        result = import_excel_workbook(
            file_path=args.file,
            source_name=args.source_name or None,
            source_sheet=args.sheet or None,
            transport_keyword=args.transport_keyword,
            include_double_clear=not args.exclude_double_clear,
            batch_ids=args.batch_ids or None,
            limit=args.limit,
            fx_usd_to_rmb=args.fx_usd_to_rmb,
            fx_rmb_to_mxn=args.fx_rmb_to_mxn,
        )
    else:
        result = preview_excel_workbook(
            file_path=args.file,
            source_sheet=args.sheet or None,
            transport_keyword=args.transport_keyword,
            include_double_clear=not args.exclude_double_clear,
            batch_ids=args.batch_ids or None,
            limit=args.limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
