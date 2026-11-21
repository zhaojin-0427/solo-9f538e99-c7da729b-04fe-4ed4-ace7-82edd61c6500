"""Cross-check the bit-parallel engine against exhaustive enumeration."""

import itertools
import json
import random

from app.judge import judge
from app.version_def import parse_definition


def reference(definition, answers):
    """Enumerate every completion, one value per blank item."""
    items = definition["items"]
    blank = [i for i in range(len(items)) if i not in answers]
    shown_sets = []
    for combo in itertools.product(("是", "否"), repeat=len(blank)):
        completion = dict(answers)
        completion.update(dict(zip(blank, combo)))
        shown = []
        for item in items:
            if item["unconditional"]:
                shown.append(True)
                continue
            visible = False
            for group in item["groups"]:
                ok = True
                for reference_index, required in group:
                    if not shown[reference_index]:
                        ok = False
                        break
                    if completion.get(reference_index) != required:
                        ok = False
                        break
                if ok:
                    visible = True
                    break
            shown.append(visible)
        shown_sets.append(shown)

    categories = []
    for index in range(len(items)):
        values = {shown[index] for shown in shown_sets}
        if values == {False}:
            categories.append("exempt")
        elif values == {True}:
            categories.append("answered" if index in answers else "missing")
        else:
            categories.append("pending")
    return categories


def random_definition(rng):
    count = rng.randint(1, 7)
    items = []
    for index in range(count):
        name = f"项{index}"
        if index == 0 or rng.random() < 0.4:
            items.append({"name": name, "unconditional": True, "groups": []})
            continue
        groups = []
        for _ in range(rng.randint(1, 2)):
            group = []
            for _ in range(rng.randint(1, 2)):
                ref = rng.randrange(index)
                group.append([ref, rng.choice(["是", "否"])])
            groups.append(group)
        items.append({"name": name, "unconditional": False, "groups": groups})
    return {"items": items}


def test_matches_reference_randomised():
    rng = random.Random(20261121)
    for _ in range(300):
        definition = random_definition(rng)
        count = len(definition["items"])
        answers = {}
        for index in range(count):
            roll = rng.random()
            if roll < 0.45:
                answers[index] = "是"
            elif roll < 0.75:
                answers[index] = "否"
        expected = reference(definition, answers)
        actual = judge(definition, answers)
        assert actual == expected, json.dumps(
            {"definition": definition, "answers": answers,
             "expected": expected, "actual": actual},
            ensure_ascii=False,
        )


def test_roundtrip_through_parser_randomised():
    """Definitions built by the parser behave the same as constructed ones."""
    rng = random.Random(7)
    for _ in range(100):
        definition = random_definition(rng)
        lines = []
        for index, item in enumerate(definition["items"]):
            if item["unconditional"]:
                lines.append(item["name"])
            else:
                groups = [
                    " & ".join(f"{ref + 1}={value}" for ref, value in group)
                    for group in item["groups"]
                ]
                lines.append(f"{item['name']} | " + " ; ".join(groups))
        parsed, errors = parse_definition("\n".join(lines))
        assert errors == []
        assert parsed == definition


def test_answer_on_sometimes_hidden_item_does_not_leak():
    from app.version_def import parse_definition

    # 乙 is hidden when 甲=否.  乙's stored 是 must not satisfy 丙 when 甲=否,
    # even though 甲 itself is blank (so 乙 is sometimes shown).
    definition, errors = parse_definition("甲\n乙 | 1=是\n丙 | 2=是")
    assert errors == []
    answers = {1: "是", 2: "是"}  # 甲 blank, 乙=是, 丙=是
    assert judge(definition, answers) == ["missing", "pending", "pending"]
    assert reference(definition, answers) == ["missing", "pending", "pending"]
