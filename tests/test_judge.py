"""Judgment semantics tests."""

from app.judge import judge
from app.version_def import parse_definition


def definition(text):
    parsed, errors = parse_definition(text)
    assert errors == [], errors
    return parsed


def answers(*values):
    return {index: value for index, value in enumerate(values) if value}


def test_unconditional_items_answered_missing():
    d = definition("甲\n乙")
    assert judge(d, answers("是", "否")) == ["answered", "answered"]
    assert judge(d, answers("是", "")) == ["answered", "missing"]
    assert judge(d, answers("", "")) == ["missing", "missing"]


def test_conditional_item_exempt_when_parent_no():
    d = definition("冷却泵已安装\n备用泵外观检查 | 1=是")
    assert judge(d, answers("否", "")) == ["answered", "exempt"]
    assert judge(d, answers("否", "是")) == ["answered", "exempt"]


def test_conditional_item_answered_when_parent_yes():
    d = definition("冷却泵已安装\n备用泵外观检查 | 1=是")
    assert judge(d, answers("是", "是")) == ["answered", "answered"]
    assert judge(d, answers("是", "")) == ["answered", "missing"]


def test_blank_parent_makes_child_pending():
    d = definition("冷却泵已安装\n备用泵外观检查 | 1=是")
    assert judge(d, answers("", "")) == ["missing", "pending"]
    assert judge(d, answers("", "否")) == ["missing", "pending"]


def test_hidden_residual_answer_ignored_evaluated_with_parent_false():
    # Child answered 是 but parent says 否 -> exempt regardless of child value.
    d = definition("甲\n乙 | 1=是")
    assert judge(d, answers("否", "是")) == ["answered", "exempt"]


def test_or_groups_any_group_satisfied():
    d = definition("甲\n乙\n丙 | 1=是 ; 2=是")
    assert judge(d, answers("是", "否", ""))[2] == "missing"
    assert judge(d, answers("否", "是", ""))[2] == "missing"
    assert judge(d, answers("否", "否", ""))[2] == "exempt"
    assert judge(d, answers("", "否", ""))[2] == "pending"


def test_and_group_requires_all():
    d = definition("甲\n乙\n丙 | 1=是 & 2=是")
    assert judge(d, answers("是", "否", ""))[2] == "exempt"
    assert judge(d, answers("是", "是", ""))[2] == "missing"
    assert judge(d, answers("是", "", ""))[2] == "pending"


def test_blank_child_only_one_value_per_completion():
    # 乙 blank, condition 甲=是 & 乙=是, 甲=是 => 乙 is pending (shown only when 乙=是)
    d = definition("甲\n乙\n丙 | 1=是 & 2=是")
    assert judge(d, answers("是", "", ""))[2] == "pending"
    # 丙 has an answer, but whether it counts as answered depends on display.
    assert judge(d, answers("是", "", "是"))[2] == "pending"


def test_blank_child_blank_and_grandchild_chain():
    # 甲 yes, 乙 blank, 丙 | 2=是, 丁 | 3=是
    d = definition("甲\n乙 | 1=是\n丙 | 2=是\n丁 | 3=是")
    result = judge(d, answers("是", "", "", ""))
    assert result == ["answered", "missing", "pending", "pending"]


def test_parent_always_hidden_makes_child_exempt():
    d = definition("甲\n乙 | 1=是\n丙 | 2=是")
    # 甲=否 => 乙 exempt => 丙 exempt even if 乙 says 是
    assert judge(d, answers("否", "是", "是")) == ["answered", "exempt", "exempt"]


def test_always_shown_answered_only_counts_original():
    d = definition("甲")
    assert judge(d, {}) == ["missing"]


def test_multiple_blank_parents_combinatorial():
    # 甲 blank, 乙 | 1=是 => 乙 pending; 丙 | 1=是 & 2=是
    d = definition("甲\n乙 | 1=是\n丙 | 1=是 & 2=是")
    assert judge(d, answers("", "", "")) == ["missing", "pending", "pending"]
