# -*- coding: utf-8 -*-
"""P4.1 模板执行模式 / 画幅兼容 / 快照字段单元测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import ai_image_layout_service as L  # noqa: E402


def _layout(**kw):
    base = {
        "key": "preset:test", "id": "test", "name": "测试版式",
        "ratio_tag": "horizontal", "aspect_ratio": 16 / 9,
        "structure": "左文右图，左右各占一半",
        "zones": [{"x": 0, "y": 0, "w": 0.5, "h": 1, "t": "txt"},
                  {"x": 0.5, "y": 0, "w": 0.5, "h": 1, "t": "img"}],
        "image_url": "",
        "template_version": "2",
    }
    base.update(kw)
    return base


def test_execution_modes():
    assert L.execution_mode(_layout()) == "zones"
    assert L.execution_mode(_layout(zones=[])) == "text"
    assert L.execution_mode(_layout(structure="", zones=[],
                                    image_url="https://x/y.jpg")) == "reference_image"
    assert L.execution_mode(_layout(structure="", zones=[], image_url="")) == "invalid"


def test_invalid_layout_rejected_before_task():
    r = L.check_aplus_compatibility(_layout(structure="", zones=[], image_url=""),
                                    "aplus_advanced")
    assert r["ok"] is False
    assert "缺少结构描述" in r["error"]


def test_vertical_and_square_rejected_for_aplus():
    for tag in ("vertical", "square"):
        r = L.check_aplus_compatibility(_layout(ratio_tag=tag), "aplus_advanced")
        assert r["ok"] is False
        assert "不能用于" in r["error"]


def test_horizontal_allowed_for_all_aplus():
    for t in ("aplus_advanced", "aplus_normal", "aplus_mobile"):
        assert L.check_aplus_compatibility(_layout(), t)["ok"] is True


def test_aspect_mismatch_warns_but_allows_conversion():
    r = L.check_aplus_compatibility(
        _layout(aspect_ratio=16 / 9, aspect_source="image_size"), "aplus_advanced"
    )
    assert r["ok"] is True
    assert any("中心裁剪" in w for w in r["warnings"])


def test_matching_aspect_has_no_crop_warning():
    r = L.check_aplus_compatibility(
        _layout(aspect_ratio=2048 / 869, aspect_source="image_size"), "aplus_advanced"
    )
    assert r["ok"] is True
    assert not any("中心裁剪" in w for w in r["warnings"])


def test_reference_image_mode_has_warning():
    r = L.check_aplus_compatibility(
        _layout(structure="", zones=[], image_url="https://x/y.jpg"), "aplus_normal"
    )
    assert r["ok"] is True
    assert any("只有参考图" in w for w in r["warnings"])
