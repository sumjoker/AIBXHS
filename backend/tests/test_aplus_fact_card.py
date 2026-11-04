# -*- coding: utf-8 -*-
"""P4 商品事实卡：Schema/校验/渲染/提示词注入测试（纯函数，不触网不写库）"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import aplus_fact_card as F  # noqa: E402
from services import ai_image_service as S  # noqa: E402


def _card():
    return {
        "product_identity": {
            "name": "足球生日气球套装",
            "category": "Party Supplies",
            "usage": "7 岁男孩足球主题生日派对",
            "target_audience": "儿童生日派对",
            "theme": "足球冠军生日派对",
        },
        "components": [
            {
                "id": "ball", "name": "足球气球", "quantity": 2,
                "color": ["黑白"], "material": "铝膜",
                "printed_details": ["足球图案"], "is_sold_item": True,
                "must_preserve": True,
            },
            {
                "id": "trophy", "name": "奖杯气球", "quantity": 1,
                "color": ["金色"], "material": "铝膜",
                "printed_details": ["CHAMPION", "1"], "is_sold_item": True,
                "must_preserve": True,
            },
            {
                "id": "silhouette", "name": "球员剪影贴纸", "quantity": F.UNKNOWN,
                "color": ["黑色"], "material": "纸质",
                "printed_details": [], "is_sold_item": True,
                "must_preserve": True,
            },
        ],
        "excluded_objects": ["蛋糕本体", "餐桌", "餐具", "尺寸标注文字"],
        "visual_identity": {
            "theme": "足球冠军生日派对",
            "color_direction": "黑白金为主、绿色点缀",
            "photography_style": "明亮柔和的家庭派对商业摄影",
        },
        "global_requirements": [
            "不得改变组件名称、数量、颜色、材质与印刷内容",
            "不得增加未列入 components 的销售商品",
        ],
        "coverage_plan": {
            "strategy": "global_coverage_across_zones",
            "global_quantity_rule": "展示性重复不改变实际数量",
            "zones": [
                {"zone_id": "col1", "scene_type": "usage", "component_ids": ["ball", "silhouette"]},
                {"zone_id": "col2", "scene_type": "context", "component_ids": ["trophy", "ball"]},
                {"zone_id": "col3", "scene_type": "display", "component_ids": ["trophy", "ball"]},
            ],
        },
    }


def test_schema_exposes_required_sections():
    props = F.FACT_CARD_SCHEMA["properties"]
    for key in ("product_identity", "components", "excluded_objects",
                "visual_identity", "global_requirements", "coverage_plan"):
        assert key in props


def test_valid_card_passes_and_renders():
    card = _card()
    assert F.validate_fact_card(card) == []
    text = F.render_fact_card(card)
    assert "结构化商品事实卡" in text
    assert "足球气球 ×2" in text
    assert "蛋糕本体" in text  # 排除对象进入提示词
    assert "分区 col1（usage）" in text
    assert "队形" not in text
    assert "待确认" in text  # 剪影数量未知必须显式标记


def test_duplicate_component_id_rejected():
    card = _card()
    card["components"].append(copy.deepcopy(card["components"][0]))
    errors = F.validate_fact_card(card)
    assert any("id 重复" in e for e in errors)


def test_uncovered_sold_component_rejected():
    card = _card()
    card["coverage_plan"]["zones"] = [
        {"zone_id": "col1", "scene_type": "usage", "component_ids": ["ball"]},
    ]
    errors = F.validate_fact_card(card)
    assert any("未覆盖以下销售组件" in e for e in errors)


def test_unknown_component_in_zone_rejected():
    card = _card()
    card["coverage_plan"]["zones"][0]["component_ids"].append("ghost")
    errors = F.validate_fact_card(card)
    assert any("不存在的组件: ghost" in e for e in errors)


def test_invalid_quantity_rejected():
    card = _card()
    card["components"][0]["quantity"] = 1.5
    errors = F.validate_fact_card(card)
    assert any("quantity" in e for e in errors)


def test_invalid_scene_type_rejected():
    card = _card()
    card["coverage_plan"]["zones"][0]["scene_type"] = "lifestyle"
    errors = F.validate_fact_card(card)
    assert any("scene_type" in e for e in errors)


def test_build_prompt_injects_structured_card():
    """571 必须出现结构化事实卡与覆盖计划，而不是普通自然语言商品信息"""
    card = _card()
    p = S._build_prompt(
        "aplus_advanced", "", "", no_text=True,
        layout_structure="顶部25%留白；下方三列等宽商品图区",
        fact_card=card,
    )
    assert "结构化商品事实卡" in p
    assert "足球气球 ×2" in p
    assert "coverage_plan" in p or "覆盖计划" in p
    assert "分区 col1（usage）" in p
    assert "蛋糕本体" in p
    # 不得再要求每个分区重复完整套装
    assert "每个商品图区默认" not in p
    assert "full_set" not in p
