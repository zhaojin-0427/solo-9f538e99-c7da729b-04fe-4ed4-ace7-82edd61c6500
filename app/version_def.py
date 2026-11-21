"""Parse and describe immutable inspection-form version definitions.

A definition is a small text format, one item per line::

    冷却泵已安装
    备用泵外观检查 | 1=是
    备用泵压力检查 | 1=是 & 2=否 ; 3=是

* Lines starting with ``#`` and empty lines are ignored.
* ``name`` alone (or ``name | -``) means an unconditional item, always shown.
* ``1=是`` refers to the 1st item (1 based).  A condition may only refer to
  items that appear above it.
* ``&`` joins requirements that must all hold, ``;`` separates alternative
  groups (OR).  Any satisfied group makes the item shown.

The parsed structure is JSON serialisable::

    {"items": [
        {"name": "...", "unconditional": true, "groups": []},
        {"name": "...", "unconditional": false,
         "groups": [[[0, "是"], [1, "否"]], [[2, "是"]]]}
    ]}
"""

from __future__ import annotations

import json
import re

ITEM_LIMIT = 12
NAME_LIMIT = 100

_REQ_RE = re.compile(r"^(\d+)\s*=\s*(是|否)$")
_NORMALISE = str.maketrans({"，": ",", "；": ";", "＆": "&", "＝": "="})


def parse_definition(text: str):
    """Return ``(definition, errors)``; definition is None when invalid."""
    errors: list[str] = []
    items: list[dict] = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "|" in line:
            name_raw, condition_raw = line.split("|", 1)
        else:
            name_raw, condition_raw = line, ""
        name = name_raw.strip()
        condition = condition_raw.strip()

        if not name:
            errors.append(f"第 {lineno} 行：检查项名称不能为空。")
            items.append({"name": "", "unconditional": True, "groups": []})
            continue
        if len(name) > NAME_LIMIT:
            errors.append(f"第 {lineno} 行：检查项名称不能超过 {NAME_LIMIT} 个字符。")

        condition = condition.translate(_NORMALISE)
        if condition in ("", "-"):
            items.append({"name": name, "unconditional": True, "groups": []})
            continue

        groups: list[list[list]] = []
        valid = True
        for group_raw in condition.split(";"):
            group_raw = group_raw.strip()
            if not group_raw:
                errors.append(f"第 {lineno} 行：条件组不能为空。")
                valid = False
                break

            requirements: list[list] = []
            for requirement_raw in group_raw.split("&"):
                requirement_raw = requirement_raw.strip()
                match = _REQ_RE.fullmatch(requirement_raw)
                if not match:
                    errors.append(
                        f"第 {lineno} 行：无法识别条件“{requirement_raw}”，"
                        "应形如 1=是。"
                    )
                    valid = False
                    break
                reference = int(match.group(1))
                if reference < 1 or reference > len(items):
                    errors.append(
                        f"第 {lineno} 行：条件只能引用前面的检查项，"
                        f"第 {reference} 项不是此项之前的检查项。"
                    )
                    valid = False
                    break
                requirements.append([reference - 1, match.group(2)])
            if not valid:
                break
            groups.append(requirements)

        # Keep the item in the list even when its condition is invalid so that
        # later reference numbers and diagnostics stay aligned.
        items.append(
            {
                "name": name,
                "unconditional": False,
                "groups": groups if valid else [],
            }
        )

    if not items:
        errors.append("请至少填写一个检查项。")
    if len(items) > ITEM_LIMIT:
        errors.append(
            f"每个表版最多 {ITEM_LIMIT} 个检查项，当前有 {len(items)} 个。"
        )

    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        if not item["name"]:
            continue
        if item["name"] in seen:
            errors.append(f"第 {index} 个检查项名称重复：{item['name']}。")
        seen.add(item["name"])

    if errors:
        return None, errors
    return {"items": items}, []


def dumps(definition: dict) -> str:
    return json.dumps(definition, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str) -> dict:
    return json.loads(raw)


def describe_item(definition: dict, index: int) -> str:
    """Human readable condition description for the version page."""
    item = definition["items"][index]
    if item["unconditional"]:
        return "始终显示"
    groups_desc = []
    for group in item["groups"]:
        parts = [
            f"第{ref + 1}项“{definition['items'][ref]['name']}”={value}"
            for ref, value in group
        ]
        groups_desc.append(" 且 ".join(parts))
    return " 或 ".join(f"({part})" for part in groups_desc)
