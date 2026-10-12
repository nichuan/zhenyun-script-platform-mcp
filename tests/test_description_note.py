import pytest

from zhenyun_script_platform_mcp.description_note import create_description, update_description


def test_create_description_uses_one_canonical_title_and_rejects_two_sources():
    note = {
        "work_item": "CDP-185966",
        "change_type": "requirement",
        "customer_label": "绿联成本项目",
        "summary": "物料查询视图转询价",
    }
    assert create_description(note) == "cdp-185966【绿联成本项目】物料查询视图转询价"

    with pytest.raises(ValueError, match="customer_label"):
        create_description({"work_item": "cdp-1", "summary": "test"})
    with pytest.raises(ValueError, match="summary"):
        create_description({**note, "summary": "x" * 61})


def test_update_description_preserves_legacy_text_and_is_idempotent():
    note = {"work_item": "cdp-185966", "change_type": "fix", "summary": "修复校验"}
    current = "cdp-100 [需求] 旧需求 | 原始描述"
    updated = update_description(current, note)
    assert updated == "cdp-185966 [修复] 修复校验 | " + current
    assert update_description(updated, note) == updated
    changed = update_description(
        updated + " | cdp-185966 [修复] 重复的历史标记",
        {**note, "summary": "修复校验2"},
    )
    assert changed.startswith("cdp-185966 [修复] 修复校验2")
    assert "原始描述" in changed


def test_update_rejects_unmanaged_same_work_item_and_overlong_result():
    note = {"work_item": "cdp-1", "summary": "说明"}
    with pytest.raises(ValueError, match="unmanaged"):
        update_description("cdp-1【客户】旧标题", note)
    with pytest.raises(ValueError, match="limit"):
        update_description("x" * 250, note)
