"""CSV validation for bulk record import."""

from __future__ import annotations

import csv
import io

MAX_RECORDS = 50_000
MAX_BYTES = 20 * 1024 * 1024
VALID_VALUES = {"是", "否", ""}
RECORD_NO_ALIASES = ("记录号", "记录编号", "record_no", "record", "编号")


class CsvError(Exception):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []


def _normalise(value: str) -> str:
    return (value or "").strip()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvError("无法解码 CSV 文件，请使用 UTF-8 或 GB18030 编码。")


def parse_csv(data: bytes, definition: dict) -> list[tuple[str, list[str]]]:
    """Validate uploaded CSV.

    Returns rows as ``(record_no, answers)`` with answers aligned to the
    version's items.  Raises :class:`CsvError` when the file cannot be
    accepted.
    """
    if not data:
        raise CsvError("CSV 文件为空。")
    if len(data) > MAX_BYTES:
        raise CsvError(
            f"CSV 文件过大（超过 {MAX_BYTES // (1024 * 1024)} MB）。"
        )

    text = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise CsvError("CSV 文件为空。") from None
    except csv.Error as exc:
        raise CsvError(f"CSV 解析失败：{exc}") from exc

    header = [cell.strip() for cell in header]
    if not header:
        raise CsvError("CSV 缺少表头。")

    record_col = None
    for index, name in enumerate(header):
        if name in RECORD_NO_ALIASES:
            record_col = index
            break
    if record_col is None:
        raise CsvError(
            "CSV 表头缺少“记录号”列（可用列名："
            + "、".join(RECORD_NO_ALIASES)
            + "）。"
        )

    item_names = [item["name"] for item in definition["items"]]
    item_columns: list[int | None] = []
    missing_columns: list[str] = []
    for name in item_names:
        if name in header:
            item_columns.append(header.index(name))
        else:
            item_columns.append(None)
            missing_columns.append(name)

    if missing_columns:
        raise CsvError(
            "CSV 缺少检查项列：" + "、".join(missing_columns)
        )

    rows: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for line_no, raw in enumerate(reader, 2):
        if not raw or all(cell.strip() == "" for cell in raw):
            continue

        if len(errors) >= 20:
            errors.append("……错误过多，仅显示前 20 条。")
            break

        record_no = (
            raw[record_col].strip() if record_col < len(raw) else ""
        )
        if not record_no:
            errors.append(f"第 {line_no} 行：记录号为空。")
            continue
        if record_no in seen:
            errors.append(f"第 {line_no} 行：记录号重复：{record_no}。")
            continue
        seen.add(record_no)

        answers: list[str] = []
        row_valid = True
        for name, column in zip(item_names, item_columns):
            cell = raw[column].strip() if column is not None and column < len(raw) else ""
            value = _normalise(cell)
            if value not in VALID_VALUES:
                errors.append(
                    f"第 {line_no} 行“{name}”：只能填是、否或留空，"
                    f"实际为“{cell}”。"
                )
                row_valid = False
                break
            answers.append(value)
        if not row_valid:
            continue

        if len(rows) >= MAX_RECORDS:
            raise CsvError(
                f"每个表版最多导入 {MAX_RECORDS} 条记录，文件超出上限。"
            )
        rows.append((record_no, answers))

    if errors:
        raise CsvError("CSV 未通过校验，未导入任何记录。", errors)
    if not rows:
        raise CsvError("CSV 中没有可导入的记录。")
    return rows
