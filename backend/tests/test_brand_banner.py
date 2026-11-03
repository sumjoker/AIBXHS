"""品牌A+横幅（970×300）回归测试（纯函数层，不触网、不写库）。

覆盖：
- 英文品牌名校验（保留大小写、长度、非法字符）
- 571 提示词：品牌名直出、逐字母字形设计交给 PE、专用文字条款
- 专用工作流：固定 1888×576 latent、497 不接参考图、参考图只进 PE
- APLUS_SPECS 的 970×300 目标尺寸

2026-11-02 A/B 实测后用户裁定：572 后置品牌名合约已整体去除（品牌名进 571 即可）。

运行：cd backend && python -m pytest tests/test_brand_banner.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import ai_image_service as S


_BANNER_WF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "workflows", "qwen_image_workflow_banner.json",
)


# ---------- 品牌名校验 ----------

def test_brand_name_preserves_case():
    assert S._normalize_brand_name("LaVenty") == "LaVenty"
    assert S._normalize_brand_name("  LaVenty  ") == "LaVenty"


def test_brand_name_collapses_whitespace_but_not_case():
    assert S._normalize_brand_name("La  Venty") == "La Venty"


def test_brand_name_empty_returns_empty():
    assert S._normalize_brand_name("") == ""
    assert S._normalize_brand_name(None) == ""


@pytest.mark.parametrize("bad", ["A", "品牌名", "LaVenty!", "La@Venty", "x" * 21])
def test_brand_name_rejects_invalid(bad):
    with pytest.raises(S.AIImageError):
        S._normalize_brand_name(bad)


def test_brand_name_allows_common_brand_punctuation():
    assert S._normalize_brand_name("O'Brien & Co.") == "O'Brien & Co."
    assert S._normalize_brand_name("L.V. 2") == "L.V. 2"


# ---------- 571 提示词 ----------

def test_banner_prompt_embeds_brand_name_for_pe():
    """品牌名必须进 571：否则 PE 会按参考图调性自造品牌名（任务 #108 实测）。"""
    p = S._build_prompt("aplus_banner", "品名：测试", "", "", no_text=True,
                        brand_name="LaVenty")
    assert "品牌名：LaVenty" in p
    assert "不得设计、猜测或另起任何其他品牌名" in p
    # 未传品牌名时不追加该行（校验已在 submit 层拦截空值）；
    # 「品牌名：」一词仍会出现在 {text_clause} 的条款里，这里只检查逐字行不存在
    p2 = S._build_prompt("aplus_banner", "品名：测试", "", "", no_text=True)
    assert "\n品牌名：LaVenty" not in p2
    assert not any(
        ln.startswith("品牌名：") and "LaVenty" in ln for ln in p2.splitlines()
    )


def test_banner_prompt_delegates_lettering_to_pe():
    """字形设计交给 PE：逐字母艺术化设计、避免方正印刷体，但文字内容不得变。"""
    p = S._build_prompt("aplus_banner", "品名：测试", "", "", no_text=True,
                        brand_name="LaVenty")
    assert "每一个字母做艺术化字形设计" in p
    assert "lettering" in p
    assert "方正呆板" in p
    # 设计自由但有底线：文字内容与大小写不可变
    assert "不得增减或改变任何字母" in p
    # PE 需在输出中逐一描述字形设计
    assert "字形设计特征" in p


def test_banner_prompt_uses_dynamic_size_and_design_freedom():
    """①无事实卡 ②宽度 65~75% 动态 ③装饰设计交给 PE。"""
    p = S._build_prompt("aplus_banner", "品名：测试", "", "", no_text=True,
                        brand_name="LaVenty")
    # ① 商品事实卡不再出现在品牌横幅提示词
    assert "商品事实卡" not in p
    # ② 宽度动态 65~75%，不得再出现写死的 80%
    assert "65~75%" in p
    assert "80%" not in p
    # ③ 装饰与品牌名关系交给 PE 设计，只保留可读性 + 装饰无文字两条硬约束
    assert "由你设计" in p
    assert "不得遮挡或削弱品牌名的可读性" in p
    assert "装饰本身不得包含任何文字、字母或数字" in p
    # ④ 「不要自行编写任何品牌名」只保留在 {text_clause} 一处
    assert p.count("不要自行编写任何品牌名") == 1


def test_banner_prompt_uses_banner_clause_not_no_text_clause():
    p = S._build_prompt("aplus_banner", "品名：测试", "", "", no_text=True)
    assert S._BANNER_TEXT_CLAUSE in p
    assert S._NO_TEXT_CLAUSE not in p
    assert "不出现任何商品" in p


def test_banner_template_registered():
    tpl = S._load_prompt_templates()
    assert "aplus_banner" in tpl
    assert "970×300" in tpl["aplus_banner"]["template"]


def test_banner_spec_target_size():
    spec = S.APLUS_SPECS["aplus_banner"]
    assert tuple(spec["target_size"]) == (970, 300)
    assert spec["label"] == "品牌A+横幅"


# ---------- 572 合约已去除 ----------

def test_banner_contract_removed():
    """A/B 实测后裁定去除 572 合约：函数与开关都不应再存在。"""
    assert not hasattr(S, "_brand_banner_contract")
    assert not hasattr(S, "_BANNER_USE_CONTRACT")


def test_banner_workflow_has_empty_contract_node():
    """横幅工作流保留 572 节点但值为空（不注入任何合约文本）。"""
    with open(_BANNER_WF, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["572"]["inputs"]["value"] == ""


# ---------- 专用工作流 ----------

def test_banner_workflow_uses_fixed_latent_and_no_encode_image():
    """专用工作流：latent 固定 1888×576，497 无任何参考图输入。"""
    with open(_BANNER_WF, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["479"]["inputs"]["width"] == 1888
    assert raw["479"]["inputs"]["height"] == 576
    assert not any(k.startswith("images.") for k in raw["497"]["inputs"])


def test_build_workflow_banner_keeps_images_out_of_encode():
    """多参考图时只给 PE 通道，497 不注入任何图像条件；572 不注入合约。"""
    wf = S._build_workflow(
        prompt="P", aspect_ratio="21:9", seed=7,
        image_names=["a.jpg", "b.jpg", "c.jpg"], filename_prefix="T",
        workflow_path=_BANNER_WF,
        encode_images=False,
    )
    enc = wf[S.NODE_ENCODE]["inputs"]
    assert not any(k.startswith("images.") for k in enc)
    assert wf["572"]["inputs"]["value"] == ""
    # 参考图仍进 PE（风格提取），首图走模板 532，额外图走 9102/9103
    assert wf[S.NODE_PE]["inputs"]["图片"] == ["532", 0]
    assert wf["565"]["inputs"]["图片2"] == ["9102", 0]
    assert wf["565"]["inputs"]["图片3"] == ["9103", 0]
