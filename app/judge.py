"""Inspection judgment engine.

Given an immutable version definition and the answers stored on one record,
classify every item into one of four categories:

``answered``  已答   always displayed, original answer given
``missing``   漏填   always displayed, originally blank
``exempt``    免检   never displayed, whatever the blank answers become
``pending``   待核   sometimes displayed, sometimes not -> needs verification

Blank displayed answers are considered under both possible supplements (是/否),
but each item takes exactly one value in a single completion.  Answers on
items that are hidden in a completion never influence display decisions.

The engine evaluates every completion in parallel using Python integers as
bit masks: with at most 12 items there are at most 4096 completions, i.e. a
4096-bit mask.  This keeps per-record judgment cheap enough for 50,000 rows.
"""

from __future__ import annotations

from .version_def import loads

ANSWER_YES = "是"
ANSWER_NO = "否"

CATEGORIES = ("answered", "missing", "exempt", "pending")
CATEGORY_LABELS = {
    "answered": "已答",
    "missing": "漏填",
    "exempt": "免检",
    "pending": "待核",
}

_MASK_CACHE: dict[int, tuple[int, list[int]]] = {}


def _masks(variable_count: int) -> tuple[int, list[int]]:
    """Return (all_ones, true_masks) for ``variable_count`` free variables."""
    cached = _MASK_CACHE.get(variable_count)
    if cached is not None:
        return cached

    total = 1 << variable_count
    all_ones = (1 << total) - 1
    true_masks: list[int] = []
    for variable in range(variable_count):
        block = 1 << variable
        chunk = (1 << block) - 1
        mask = 0
        position = 0
        while position < total:
            mask |= chunk << (position + block)
            position += 2 * block
        true_masks.append(mask)

    result = (all_ones, true_masks)
    _MASK_CACHE[variable_count] = result
    return result


def judge(definition: dict, answers: dict[int, str]) -> list[str]:
    """Classify each item; ``answers`` maps item index to 是/否."""
    items = definition["items"]
    item_count = len(items)
    blank = [index for index in range(item_count) if index not in answers]
    all_ones, true_masks = _masks(len(blank))
    variable_of = {item_index: position for position, item_index in enumerate(blank)}

    displayed: list[int] = [0] * item_count

    for index, item in enumerate(items):
        if item["unconditional"]:
            displayed[index] = all_ones
            continue

        shown_mask = 0
        for group in item["groups"]:
            group_mask = all_ones
            for reference, required in group:
                reference_mask = displayed[reference]
                if reference_mask == 0:
                    group_mask = 0
                    break

                if reference in answers:
                    value_mask = (
                        all_ones if answers[reference] == required else 0
                    )
                else:
                    yes_mask = true_masks[variable_of[reference]]
                    value_mask = yes_mask if required == ANSWER_YES else ~yes_mask

                group_mask &= reference_mask & value_mask
                if group_mask == 0:
                    break
            shown_mask |= group_mask

        displayed[index] = shown_mask & all_ones

    categories: list[str] = []
    for index in range(item_count):
        mask = displayed[index]
        if mask == 0:
            categories.append("exempt")
        elif mask == all_ones:
            categories.append("answered" if index in answers else "missing")
        else:
            categories.append("pending")
    return categories


def display_under_answers(definition: dict, answers: dict[int, str]) -> list[bool]:
    """Deterministic display using only the original answers (blanks absent)."""
    items = definition["items"]
    shown: list[bool] = []
    for item in items:
        if item["unconditional"]:
            shown.append(True)
            continue
        visible = False
        for group in item["groups"]:
            if all(
                shown[reference] and answers.get(reference) == required
                for reference, required in group
            ):
                visible = True
                break
        shown.append(visible)
    return shown


def count_categories(category_lists: list[list[str]]) -> dict[int, dict[str, int]]:
    """Aggregate categories into per-item counts."""
    counts: dict[int, dict[str, int]] = {}
    for categories in category_lists:
        for index, category in enumerate(categories):
            item_counts = counts.setdefault(
                index, {name: 0 for name in CATEGORIES}
            )
            item_counts[category] += 1
    return counts


__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "judge",
    "display_under_answers",
    "count_categories",
    "loads",
]
