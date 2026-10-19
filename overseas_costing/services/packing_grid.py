"""中文用途：定义来源无关的装箱表格网格，并保留原值、显示值、公式和合并证据。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from dataclasses import asdict, dataclass
import copy
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_GRID_ROWS = 10_000
MAX_GRID_COLUMNS = 256
MAX_GRID_CELLS = 250_000


def _check_grid_size(rows: int, columns: int) -> None:
    if rows > MAX_GRID_ROWS or columns > MAX_GRID_COLUMNS or rows * columns > MAX_GRID_CELLS:
        raise ValueError(_("来源范围超出预览限制（10000 行、256 列、250000 个单元格），请缩小工作表范围。"))


class PackingSheetNotFound(ValueError):
    """用户明确选择的工作表不存在。"""


@dataclass(frozen=True)
class Cell:
    raw_value: Any
    display_value: str | None
    formula: str | None
    row: int
    column: int


@dataclass(frozen=True)
class MergeRange:
    start_row: int
    end_row: int
    start_column: int
    end_column: int
    evidence_kind: str


@dataclass(frozen=True)
class MaterialLine:
    source_row: int
    material_code: str | None
    quantity: str | None
    unit: str | None
    raw_fields: dict[str, Any]


@dataclass(frozen=True)
class PackageGroup:
    group_id: str
    row_numbers: tuple[int, ...]
    dimensions: dict[str, Any]
    net_weight: dict[str, Any]
    gross_weight: dict[str, Any]
    volume: dict[str, Any]
    package_count: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    needs_confirmation: bool


@dataclass(frozen=True)
class PackingTotals:
    value: str | None
    unit: str
    kind: str
    source_range: str | None
    precision: int | None


def dataclass_dict(value: Any) -> dict[str, Any]:
    return asdict(value)


def build_grid_from_dingtalk_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """把阿里云缓存的 schema v1 JSON 转为统一网格，不猜测不存在的合并范围。"""

    if snapshot.get("schemaVersion", snapshot.get("schema_version")) != 1:
        raise ValueError(_("仅支持 schema version 1 的钉钉表格快照。"))

    parts = [snapshot] if snapshot.get("values") or not snapshot.get("chunks") else snapshot["chunks"]
    values = _snapshot_matrix(parts, "values")
    display_values = _snapshot_matrix(parts, "displayValues", "display_values")
    formulas = _snapshot_matrix(parts, "formulas")
    row_count = max(len(values), len(display_values), len(formulas))
    column_count = max(
        [len(row) for matrix in (values, display_values, formulas) for row in matrix] or [0]
    )
    _check_grid_size(row_count, column_count)

    cells: list[list[dict[str, Any]]] = []
    for row_index in range(row_count):
        row: list[dict[str, Any]] = []
        for column_index in range(column_count):
            raw_value = _matrix_value(values, row_index, column_index)
            display_value = _matrix_value(display_values, row_index, column_index)
            formula = _matrix_value(formulas, row_index, column_index)
            row.append(
                dataclass_dict(
                    Cell(
                        raw_value=raw_value,
                        display_value=None if display_value in (None, "") else str(display_value),
                        formula=None if formula in (None, "") else str(formula),
                        row=row_index + 1,
                        column=column_index + 1,
                    )
                )
            )
        cells.append(row)

    merge_ranges = snapshot.get("mergeRanges", snapshot.get("merge_ranges")) or []
    normalized_merges = [_normalize_merge_range(item, "dingtalk_merge") for item in merge_ranges]
    for region in normalized_merges:
        if not (1 <= region['start_row'] <= region['end_row'] <= row_count
                and 1 <= region['start_column'] <= region['end_column'] <= column_count):
            raise ValueError(_("来源合并范围超出实际快照，请刷新完整来源后重新预览。"))
    merge_ranges_available = bool(
        snapshot.get("mergeRangesAvailable", snapshot.get("merge_ranges_available", False))
    )

    return {
        "schema_version": 1,
        "source_kind": "wiki_sheet",
        "sheet_name": str(snapshot.get("sheetName", snapshot.get("sheet_name")) or ""),
        "range_address": snapshot.get("rangeAddress", snapshot.get("range_address")),
        "cells": cells,
        "merge_ranges_available": merge_ranges_available,
        "merge_ranges": normalized_merges if merge_ranges_available else [],
        "source_updated_at": snapshot.get("sourceUpdatedAt", snapshot.get("source_updated_at")),
    }


def _chunk_start_row(chunk: Any) -> int:
    if not isinstance(chunk, dict):
        return 0
    address = str(chunk.get("rangeAddress", chunk.get("range_address")) or "")
    match = __import__("re").search(r"[A-Za-z]+(\d+)", address)
    return int(match.group(1)) if match else 0


def _snapshot_matrix(parts: list[dict[str, Any]], key: str, alias: str | None = None) -> list[list[Any]]:
    """Place ranges at their real worksheet coordinates, including chunk gaps."""

    result = []
    widest = 0
    for part in parts:
        matrix = part.get(key, part.get(alias) if alias else None) or []
        if not isinstance(matrix, list) or any(not isinstance(row, list) for row in matrix):
            raise ValueError(_("来源范围必须是二维单元格数组。"))
        address = str(part.get("rangeAddress", part.get("range_address")) or "A1").rsplit("!", 1)[-1]
        match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})(?::\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,6})?", address)
        if not match:
            raise ValueError(_("来源单元格范围缺少有效工作表坐标。"))
        column_number = 0
        for letter in match.group(1).upper():
            column_number = column_number * 26 + ord(letter) - ord("A") + 1
        row_offset, column_offset = int(match.group(2)) - 1, column_number - 1
        widest = max(widest, column_offset + max((len(row) for row in matrix), default=0))
        _check_grid_size(max(len(result), row_offset + max(1, len(matrix))), widest)
        for index, source_row in enumerate(matrix):
            row_index = row_offset + index
            while len(result) <= row_index:
                result.append([])
            target = result[row_index]
            while len(target) < column_offset + len(source_row):
                target.append(None)
            for column, value in enumerate(source_row, start=column_offset):
                target[column] = value
    return result


def _matrix_value(matrix: list[list[Any]], row: int, column: int) -> Any:
    if row >= len(matrix) or column >= len(matrix[row]):
        return None
    return matrix[row][column]


def _normalize_merge_range(item: Any, default_evidence: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(_("非法的合并单元格范围。"))
    return dataclass_dict(
        MergeRange(
            start_row=int(item.get("start_row", item.get("startRow"))),
            end_row=int(item.get("end_row", item.get("endRow"))),
            start_column=int(item.get("start_column", item.get("startColumn"))),
            end_column=int(item.get("end_column", item.get("endColumn"))),
            evidence_kind=str(item.get("evidence_kind", item.get("evidenceKind")) or default_evidence),
        )
    )


RANGE_KEYS = ("start_row", "end_row", "start_column", "end_column")


def range_coordinates(value: dict[str, Any]) -> dict[str, int]:
    return {key: int(value[key]) for key in RANGE_KEYS}


def ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (left["start_row"] <= right["end_row"] and right["start_row"] <= left["end_row"]
            and left["start_column"] <= right["end_column"] and right["start_column"] <= left["end_column"])


def range_contains(value: dict[str, Any], row: int, column: int) -> bool:
    return (value["start_row"] <= row <= value["end_row"]
            and value["start_column"] <= column <= value["end_column"])


def _same_anchor(left: Any, right: Any, *, numeric: bool) -> bool:
    if not numeric:
        return str(left).strip() == str(right).strip()
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def review_packing_grid(grid: dict[str, Any], source_hash: str, reviews: Any = None) -> dict[str, Any]:
    """Validate source-bound coordinate evidence without changing any original cell."""

    from overseas_costing.services.packing_parse_service import PHYSICAL_FIELDS, parse_packing_grid

    if reviews is None or reviews == "":
        reviews = {"source_hash": source_hash, "ranges": []}
    if isinstance(reviews, str):
        if len(reviews.encode("utf-8")) > 100_000:
            raise ValueError(_("合并复核内容过大。"))
        try:
            reviews = json.loads(reviews)
        except (TypeError, ValueError) as error:
            raise ValueError(_("合并复核必须是有效 JSON 对象。")) from error
    if not isinstance(reviews, dict) or set(reviews) - {"source_hash", "ranges"}:
        raise ValueError(_("合并复核必须是包含来源哈希和 ranges 的对象。"))
    if str(reviews.get("source_hash") or "") != source_hash:
        raise ValueError(_("来源已变化，合并复核的来源哈希不一致，请重新预览。"))
    ranges = reviews.get("ranges")
    if not isinstance(ranges, list) or len(ranges) > 500:
        raise ValueError(_("合并复核 ranges 必须是最多 500 项的数组。"))
    parsed = parse_packing_grid(grid)
    columns = {item["column"]: item["field"] for item in parsed.get("columns") or []}
    rows = {int(row["source_row"]) for row in parsed.get("material_rows") or []}
    cells = grid.get("cells") or []
    source_merges = grid.get("merge_ranges") or []
    normalized = []
    for item in ranges:
        if (not isinstance(item, dict) or set(item) != {*RANGE_KEYS, "action"}
                or any(type(item.get(key)) is not int for key in RANGE_KEYS)):
            raise ValueError(_("合并复核范围必须提供整数行列坐标和操作。"))
        coords = range_coordinates(item)
        start, end, left, right = (coords[key] for key in RANGE_KEYS)
        if (start < 1 or end <= start or end > len(cells) or left < 1 or right < left
                or any(column not in columns for column in range(left, right + 1))
                or any(row not in rows for row in range(start, end + 1))
                or any(right > len(cells[row - 1]) for row in range(start, end + 1))):
            raise ValueError(_("合并复核范围越界，或包含表头、空白分隔行及未识别字段。"))
        if not isinstance(item["action"], str) or item["action"] not in {"confirm", "separate"}:
            raise ValueError(_("合并复核操作只能是 confirm 或 separate。"))
        if any(ranges_overlap(coords, existing) for existing in source_merges):
            raise ValueError(_("不能覆盖来源提供的真实合并范围。"))
        if any(ranges_overlap(coords, existing) for existing in normalized):
            raise ValueError(_("合并复核范围不能重叠。"))
        # A rectangular selection is evidence for each semantic column, never a
        # horizontal merge that could make an identity value become a weight.
        for column in range(left, right + 1):
            if item["action"] == "confirm":
                anchor = cells[start - 1][column - 1]
                if anchor.get("raw_value") in (None, ""):
                    raise ValueError(_("确认合并范围的首行必须有来源原值，不能猜算空白或公式。"))
                for row in range(start, end + 1):
                    cell = cells[row - 1][column - 1]
                    raw = cell.get("raw_value")
                    if cell.get("formula") and raw is None:
                        raise ValueError(_("确认合并范围包含没有缓存结果的公式。"))
                    numeric = columns[column] in {*PHYSICAL_FIELDS, "quantity", "chargeable_weight_kg"}
                    if raw not in (None, "") and not _same_anchor(anchor["raw_value"], raw, numeric=numeric):
                        raise ValueError(_("确认合并范围包含相互冲突的非空原值。"))
            normalized.append({**coords, "start_column": column, "end_column": column, "action": item["action"]})
    if len(normalized) > 500:
        raise ValueError(_("按字段展开的合并复核范围不能超过 500 项。"))
    normalized.sort(key=lambda item: (item["start_row"], item["start_column"], item["end_row"]))
    result = copy.deepcopy(grid)
    result["merge_reviews"] = {"source_hash": source_hash, "ranges": normalized}
    result["merge_ranges"] = [*copy.deepcopy(source_merges), *[
        {**range_coordinates(item), "evidence_kind": "manual_confirmed"}
        for item in normalized if item["action"] == "confirm"
    ]]
    return result
