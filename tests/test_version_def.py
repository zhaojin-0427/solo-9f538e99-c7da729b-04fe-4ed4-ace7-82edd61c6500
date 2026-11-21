"""Version definition parser tests."""

from app.version_def import ITEM_LIMIT, parse_definition


def test_unconditional_and_conditional_parse():
    definition, errors = parse_definition(
        "冷却泵已安装\n备用泵外观检查 | 1=是\n备用泵压力检查 | 1=是 & 2=否 ; 2=是"
    )
    assert errors == []
    items = definition["items"]
    assert items[0] == {"name": "冷却泵已安装", "unconditional": True, "groups": []}
    assert items[1]["groups"] == [[[0, "是"]]]
    assert items[2]["groups"] == [[[0, "是"], [1, "否"]], [[1, "是"]]]


def test_blank_definition_rejected():
    definition, errors = parse_definition("   \n# comment\n")
    assert definition is None
    assert "请至少填写一个检查项。" in errors


def test_self_reference_rejected():
    definition, errors = parse_definition("甲 | 1=是")
    assert definition is None
    assert any("只能引用前面" in error for error in errors)


def test_forward_reference_rejected():
    definition, errors = parse_definition("甲\n乙 | 3=是\n丙")
    assert definition is None
    assert any("只能引用前面" in error for error in errors)


def test_bad_requirement_rejected():
    definition, errors = parse_definition("甲\n乙 | 1=好")
    assert definition is None
    assert any("无法识别条件" in error for error in errors)


def test_too_many_items_rejected():
    text = "\n".join(f"项{i}" for i in range(ITEM_LIMIT + 1))
    definition, errors = parse_definition(text)
    assert definition is None
    assert any("最多 12" in error for error in errors)


def test_twelve_items_ok():
    text = "\n".join(f"项{i}" for i in range(ITEM_LIMIT))
    definition, errors = parse_definition(text)
    assert errors == []
    assert len(definition["items"]) == ITEM_LIMIT


def test_duplicate_names_rejected():
    definition, errors = parse_definition("甲\n甲")
    assert definition is None
    assert any("重复" in error for error in errors)


def test_fullwidth_punctuation_accepted():
    definition, errors = parse_definition("甲\n乙\n丙 | 1＝是＆2＝是；2＝否")
    assert errors == []
    assert definition["items"][2]["groups"] == [[[0, "是"], [1, "是"]], [[1, "否"]]]


def test_hyphen_means_unconditional():
    definition, errors = parse_definition("甲 | -")
    assert errors == []
    assert definition["items"][0]["unconditional"] is True


def test_name_whitespace_trimmed():
    definition, errors = parse_definition("  甲  ")
    assert definition["items"][0]["name"] == "甲"
