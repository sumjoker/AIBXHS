# -*- coding: utf-8 -*-
"""AI 生图链路回归测试（纯函数层，不触网、不写库）。

覆盖本次改造的关键契约：
- 多参考图工作流注入（8 张上限、AUTOGROW/PE 通道挂载）
- 比例白名单（缺失即报错，不静默降级）
- 状态聚合（含已取消）
- 入参校验（伪造图片/超像素/超张数）
- 错误归因与文本截断
- 提交幂等

运行：cd backend && python -m pytest tests/test_ai_image_pipeline.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest
from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers import ai_image as R
from services import ai_image_service as S
from services import relay_image_service as RS


# ---------- 多参考图工作流 ----------

def test_workflow_injects_extra_reference_images():
    wf = S._build_workflow(
        prompt="p", aspect_ratio="2:3", seed=7,
        image_names=["a.jpg", "b.jpg", "c.jpg"], filename_prefix="T",
    )
    assert wf["475"]["inputs"]["image"] == "a.jpg"
    assert wf["9002"]["inputs"]["image"] == "b.jpg"
    assert wf["9003"]["inputs"]["image"] == "c.jpg"
    assert wf["497"]["inputs"]["images.image_1"] == ["532", 0]
    assert wf["497"]["inputs"]["images.image_2"] == ["9102", 0]
    assert wf["497"]["inputs"]["images.image_3"] == ["9103", 0]
    assert wf["565"]["inputs"]["图片2"] == ["9102", 0]
    assert wf["565"]["inputs"]["图片3"] == ["9103", 0]


def test_workflow_all_node_refs_exist():
    wf = S._build_workflow(
        prompt="p", aspect_ratio="1:1", seed=1,
        image_names=[f"x{i}.jpg" for i in range(1, 9)], filename_prefix="T",
    )
    ids = set(wf)
    for nid, node in wf.items():
        for key, val in (node.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0].isdigit():
                assert val[0] in ids, f"节点 {nid}.{key} 引用不存在的 {val[0]}"


def test_workflow_rejects_ninth_reference_image():
    with pytest.raises(S.AIImageError):
        S._build_workflow(
            prompt="p", aspect_ratio="1:1", seed=1,
            image_names=[f"x{i}.jpg" for i in range(1, 10)], filename_prefix="T",
        )


def test_workflow_applies_megapixels_and_ratio():
    wf = S._build_workflow(
        prompt="p", aspect_ratio="21:9", seed=1,
        image_names=["a.jpg"], filename_prefix="T", megapixels=1.0,
    )
    assert wf["13"]["inputs"]["aspect_ratio"] == "21:9 (Ultrawide)"
    assert wf["13"]["inputs"]["megapixels"] == 1.0


# ---------- 比例白名单 ----------

def test_ratio_unknown_key_raises():
    with pytest.raises(S.AIImageError):
        S.resolve_ratio_label("7:5")


def test_ratio_missing_on_server_raises_not_fallback(monkeypatch):
    """服务端选项表不含该比例时必须报错，不能悄悄用 1:1。"""
    monkeypatch.setattr(S, "_supported_ratio_options", lambda force=False: ["1:1 (Square)"])
    with pytest.raises(S.AIImageError) as ei:
        S.resolve_ratio_label("16:9")
    assert "不支持" in str(ei.value)


def test_ratio_probe_unavailable_allows_static_mapping(monkeypatch):
    """探测不可达时不阻断静态映射（服务抖动不应让整页不可用）。"""
    monkeypatch.setattr(S, "_supported_ratio_options", lambda force=False: None)
    assert S.resolve_ratio_label("16:9") == "16:9 (Widescreen)"


# ---------- 状态聚合 ----------

@pytest.mark.parametrize("statuses,expected", [
    (["已完成", "已完成"], "已完成"),
    (["失败", "失败"], "失败"),
    (["已取消", "已取消"], "已取消"),
    (["已完成", "失败"], "部分完成"),
    (["已完成", "已取消"], "部分完成"),
    (["排队中", "已完成"], "生成中"),
    (["生成中", "失败"], "生成中"),
    ([], "生成中"),
])
def test_aggregate_status(statuses, expected):
    items = [{"status": s} for s in statuses]
    assert S._aggregate_status(items) == expected


# ---------- 入参校验 ----------

class _StubUpload:
    def __init__(self, data: bytes, filename: str, content_type: str):
        self.file = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type


def _png(size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_validate_images_accepts_real_png():
    out = R._validate_images([_StubUpload(_png(), "a.png", "image/png")])
    assert len(out) == 1 and out[0][0] == "a.png" and out[0][1]


def test_validate_images_rejects_fake_mime():
    with pytest.raises(HTTPException) as ei:
        R._validate_images([_StubUpload(b"not an image", "a.jpg", "image/jpeg")])
    assert ei.value.status_code == 400
    assert "无法解析" in ei.value.detail


def test_validate_images_rejects_unsupported_type():
    with pytest.raises(HTTPException) as ei:
        R._validate_images([_StubUpload(b"x", "a.txt", "text/plain")])
    assert ei.value.status_code == 400


def test_validate_images_rejects_too_many():
    files = [_StubUpload(_png(), f"{i}.png", "image/png") for i in range(S.MAX_REF_IMAGES + 1)]
    with pytest.raises(HTTPException) as ei:
        R._validate_images(files)
    assert str(S.MAX_REF_IMAGES) in ei.value.detail


def test_validate_images_rejects_oversized_pixels(monkeypatch):
    monkeypatch.setattr(R, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(HTTPException) as ei:
        R._validate_images([_StubUpload(_png((64, 64)), "a.png", "image/png")])
    assert "分辨率" in ei.value.detail


# ---------- 文本与错误 ----------

def test_safe_text_truncates_to_column_width():
    assert S._safe_text("x" * 500, 200) == "x" * 200
    assert S._safe_text("  hi  ", 10) == "hi"
    assert S._safe_text("", 10) is None
    assert S._safe_text(None, 10) is None


@pytest.mark.parametrize("raw,hint_kw", [
    ("CUDA out of memory", "显存"),
    ("Failed to load checkpoint", "模型"),
    ("cannot identify image file", "参考图"),
    ("Connection refused", "连接"),
])
def test_humanize_comfy_error(raw, hint_kw):
    assert hint_kw in S.humanize_comfy_error(raw)


def test_humanize_comfy_error_unknown_returns_empty():
    assert S.humanize_comfy_error("something weird happened") == ""
    assert S.humanize_comfy_error(None) == ""


def test_compile_brand_genes_skips_default_font():
    text = S._compile_brand_genes({
        "main_color": "墨绿", "font_style": "智能字体风格",
        "country": "US 美国", "language": "英语",
    })
    assert "主色调" in text and "墨绿" in text
    assert "文字排版风格" not in text          # 智能字体风格是默认值，不应写入提示词
    assert "US 美国" in text


def test_build_prompt_hardened_keeps_constraints_not_camera_params():
    """brief 式：业务约束写死，场景/机位/光线交给 PE；不写死摄影参数"""
    p = S._build_prompt("scene_person", "品名：测试杯", "", "")
    assert "品名：测试杯" in p
    assert "不得改动商品" in p and "一件不少" in p, "件数不得增减必须写死"
    assert "儿童" in p
    for banned in ("f/2.8", "色温", "等效", "构图 半身"):
        assert banned not in p, f"不应写死摄影参数或构图：{banned}"

    p3 = S._build_prompt("selling_point", "核心卖点：加厚杯壁", "杯口朝上", "")
    assert "杯口朝上" in p3 and "补充要求" in p3


def test_build_prompt_falls_back_to_v3_when_hardening_disabled(monkeypatch):
    """一键撤回：IMAGE_LOCAL_HARDENED_PROMPT=False 回到 v3 轻量版"""
    monkeypatch.setattr(S.settings, "IMAGE_LOCAL_HARDENED_PROMPT", False)
    p = S._build_prompt("scene", "品名：测试杯", "", "")
    assert "外观以参考图为准" in p
    assert "【" not in p, "v3 轻量版不使用重段落标记"
    assert "必须保留" not in p


# ---------- 幂等 ----------

def test_idempotency_hit_and_inflight():
    key = "test-key-1"
    assert R._idem_check(key) == {}
    # 占位中：第二次调用返回 inflight 标记
    assert R._idem_check(key).get("__inflight__") is True
    R._idem_settle(key, {"id": 99})
    assert R._idem_check(key) == {"id": 99}
    R._idem_settle(key)


def test_idempotency_empty_key_is_noop():
    assert R._idem_check("") == {}
    R._idem_settle("", {"id": 1})   # 不应抛异常


# ---------- 运行代号（取消后立刻重试的竞态隔离）----------

def test_run_token_superseded():
    """新执行接管后，旧线程的代号必须判为过期，否则会覆盖重试结果"""
    tid = 990001
    t1 = S._new_run_token(tid)
    assert S._is_current_token(tid, t1)
    t2 = S._new_run_token(tid)
    assert t2 == t1 + 1
    assert not S._is_current_token(tid, t1)
    assert S._is_current_token(tid, t2)


def test_run_token_unknown_task_not_current():
    assert not S._is_current_token(990002, 1)


# ---------- 商品事实合并 ----------

def test_merge_facts_prefers_user_fields():
    """用户已填字段优先，自动分析只补缺失项（避免提示词出现两行品名）"""
    user = "品名：海钓蛋糕装饰\n材质：亚克力"
    auto = "品名：钓鱼摆件\n颜色：蓝色\n核心卖点：立体造型"
    out = S._merge_facts(user, auto)
    assert out.count("品名：") == 1
    assert "品名：海钓蛋糕装饰" in out
    assert "材质：亚克力" in out
    assert "颜色：蓝色" in out and "核心卖点：立体造型" in out


def test_merge_facts_handles_empty_sides():
    assert S._merge_facts("品名：A", "") == "品名：A"
    assert S._merge_facts("", "品名：B") == "品名：B"
    assert S._merge_facts("", "") == ""


# ---------- result_images 落库预算（MySQL Text 64KB） ----------

def test_dump_items_clips_long_prompt_fields():
    """单条超长提示词必须裁剪，否则 12 张叠起来会超 Text 上限"""
    items = [{
        "type": "scene", "label": "场景图", "seq": 0, "status": "已完成", "url": "u",
        "error": None, "final_prompt": "提" * 9000, "prompt_cn": "中" * 3000,
        "inventory": [f"piece {i}" for i in range(20)],
    }]
    d = json.loads(S._dump_items(items))
    assert len(d[0]["final_prompt"]) == S._PROMPT_FIELD_LIMIT + 1   # 截断 + 省略号
    assert len(d[0]["prompt_cn"]) == S._PROMPT_CN_LIMIT + 1
    assert len(d[0]["inventory"]) == S._INVENTORY_MAX_ITEMS
    assert d[0]["status"] == "已完成" and d[0]["url"] == "u"


def test_dump_items_stays_within_text_column_budget():
    """12 张 × 超长中文商品信息：必须收敛到预算内，否则 1406 会让任务永远卡在生成中"""
    items = [{
        "type": "scene", "label": "场景图", "seq": i, "status": "已完成",
        "url": f"u{i}", "error": None,
        "final_prompt": "品名：" + "超长商品信息" * 1200,
        "enhanced_prompt": "enh " * 2000,
        "prompt_cn": "中文对照" * 600,
        "inventory": [f"piece{i}-{j}" for j in range(15)],
    } for i in range(12)]
    raw = S._dump_items(items)
    assert len(raw.encode("utf-8")) <= S._RESULT_JSON_BUDGET
    rows = json.loads(raw)
    assert len(rows) == 12
    assert all(r["status"] == "已完成" and r["url"] for r in rows), "状态与图片地址永不裁剪"


def test_dump_items_edge_cases():
    assert S._dump_items([]) == "[]"
    assert S._dump_items(None) == "[]"
    tricky = 'a"\nb\\c'          # 引号/换行/反斜杠必须原样往返
    assert json.loads(S._dump_items([{"final_prompt": tricky}]))[0]["final_prompt"] == tricky


# ---------- 重试下标 ----------

def test_pick_retry_targets_dedupes_and_filters():
    items = [{"status": "失败"}, {"status": "已完成"}, {"status": "已取消"}, {"status": "生成中"}]
    retryable = ("失败", "已取消")
    assert S._pick_retry_targets(items, [0, 0, 2, 2, 1], retryable) == [0, 2]
    assert S._pick_retry_targets(items, None, retryable) == [0, 2]
    assert S._pick_retry_targets(items, [99, -1, 3], retryable) == []


# ---------- 主参考图清晰度排序 ----------

def test_order_images_by_clarity_puts_largest_first():
    """产品库首图常是 75px 缩略图：主参考图必须换成清晰的那张（否则看图全错）"""
    tiny = ("tiny.jpg", _png((75, 75)))
    big = ("big.jpg", _png((600, 600)))
    mid = ("mid.jpg", _png((200, 200)))
    out = S._order_images_by_clarity([tiny, big, mid])
    assert [n for n, _ in out] == ["big.jpg", "mid.jpg", "tiny.jpg"]


def test_order_images_keeps_original_order_on_tie():
    a = ("a.jpg", _png((100, 100)))
    b = ("b.jpg", _png((100, 100)))
    assert [n for n, _ in S._order_images_by_clarity([a, b])] == ["a.jpg", "b.jpg"]


def test_order_images_single_and_bad_bytes():
    one = [("a.jpg", _png((10, 10)))]
    assert S._order_images_by_clarity(one)[0][0] == "a.jpg"
    # 解不开的字节按「最不清晰」处理，不能抛错
    assert S._image_area(b"not an image") == 0
    out = S._order_images_by_clarity([("bad.jpg", b"xx"), ("ok.jpg", _png((300, 300)))])
    assert out[0][0] == "ok.jpg"


# ---------- 商品信息卡（复用生视频分析）----------

def test_card_to_text_maps_video_fields_and_drops_uncertain():
    card = {
        "product_name_cn": "希腊月桂皇冠套装",
        "product_name_en": "Gold Laurel Leaf Tiara Set",
        "product_type": "蛋糕装饰",
        "material": ["金属合金", "仿水晶"],
        "colors": ["金色", "透明"],
        "product_components": ["头冠 x1", "链条 x1", "手镯 x2"],
        "product_size": "无法从图片确认",          # 必须被丢弃，不能当属性写进提示词
        "selling_points": ["叶片造型", "泪滴水晶"],
        "usage_methods": ["婚礼", "生日派对"],
        "usage_scenarios": ["不确定"],
    }
    text = S._card_to_text(card)
    assert "品名：希腊月桂皇冠套装" in text
    assert "材质：金属合金、仿水晶" in text
    assert "组件：头冠 x1、链条 x1、手镯 x2" in text
    assert "产品尺寸" not in text, "「无法从图片确认」不能进提示词"
    assert "不确定" not in text


def test_card_to_text_empty_card():
    assert S._card_to_text({}) == ""
    assert S._card_to_text({"product_size": ""}) == ""


def test_design_user_text_marks_card_authoritative():
    """design 阶段必须看到「已确认事实卡」，且明确不要重新识别"""
    from services import relay_image_service as RL
    txt = RL._design_user_text("scene", "1:1", "品名：皇冠\n组件：头冠 x1", "", "")
    assert "CONFIRMED PRODUCT CARD" in txt
    assert "do not re-identify" in txt
    assert "品名：皇冠" in txt
    # 没有卡时要说明「自己看图识别」，不能留空白让模型猜
    txt2 = RL._design_user_text("scene", "1:1", "", "", "")
    assert "empty" in txt2 and "identify the product from the image" in txt2


# ---------- 场景图：带人物 / 不带人物 ----------

def test_scene_variants_registered():
    """场景图区分两个变体；旧 key scene 必须保留（历史任务回填依赖它）"""
    assert "scene" in S.MAIN_GEN_TYPES and "scene_person" in S.MAIN_GEN_TYPES
    assert "不带人物" in S.MAIN_GEN_TYPES["scene"]
    assert "带人物" in S.MAIN_GEN_TYPES["scene_person"]
    from services import relay_image_service as RL
    assert "scene_person" in RL.SHOT_SPECS
    assert "NO people" in RL.SHOT_SPECS["scene"]


def test_options_metadata_is_complete():
    """/options 是前端唯一数据源：A+ 尺寸/说明、主副图说明、场景图父子结构都要齐。

    前端已改为直接消费这些字段（不再各自硬编码），任何一项缺失都会让卡片失去说明或尺寸。
    """
    assert S.APLUS_SPECS, "A+ 类型为空"
    for key, spec in S.APLUS_SPECS.items():
        assert spec.get("desc"), f"{key} 缺 desc"
        assert len(spec.get("target_size") or ()) == 2, f"{key} target_size 异常"
        assert spec.get("label") and spec.get("gen_ratio")
    assert S.MAIN_TYPE_DESCRIPTIONS, "主副图说明为空"
    assert set(S.MAIN_TYPE_DESCRIPTIONS) <= set(S.MAIN_GEN_TYPES), "说明里出现了未注册的类型"
    # 场景图父项的两条子路径必须都在 MAIN_GEN_TYPES 内，否则前端勾选会提交非法 key
    variant_keys = [v["key"] for v in S.SCENE_GROUP["variants"]]
    assert variant_keys == ["scene", "scene_person"]
    assert set(variant_keys) <= set(S.MAIN_GEN_TYPES)
    assert S.SCENE_GROUP["key"] in variant_keys


def test_scene_person_has_minor_ban_and_usage_rule():
    """带人物场景：成年模特 + 按使用方式佩戴 + 禁儿童"""
    from services import relay_image_service as RL
    spec = RL.SHOT_SPECS["scene_person"].lower()
    for kw in ("no children", "adult", "25-40", "wear or use", "waist chain"):
        assert kw in spec, f"缺少约束: {kw}"


def test_design_instruction_has_minor_guard():
    from services import relay_image_service as RL
    tpl = RL.DESIGN_INSTRUCTION.lower()
    assert "child, kid, teenager, minor" in tpl
    assert "adult" in tpl


def test_local_template_covers_both_scene_variants():
    """local 与 relay 共用 UI，缺模板会让提交直接 400"""
    for t in ("scene", "scene_person"):
        p = S._build_prompt(t, "品名：测试", "", "")
        assert "不得改动商品" in p, f"{t} 缺件数约束"
    assert "儿童" in S._build_prompt("scene_person", "", "", "")
    assert "不出现任何人物" in S._build_prompt("scene", "", "", "")


# ---------- 设计输出截断治理 ----------

def test_design_max_tokens_configurable_and_retry_scales(monkeypatch):
    """截断时必须放大预算重试，而不是原地重试（原样重试必然再截断）"""
    from services import relay_image_service as RL
    seen = []

    def fake_post(base, key, model, content, timeout, **kw):
        seen.append(kw.get("max_tokens"))
        if len(seen) == 1:
            raise RL.RelayError("输出被 max_tokens=2600 截断")
        return '{"prompt_en":"keep the product; adult model","inventory":["tiara x1"],"prompt_cn":"保留商品","negative":"child"}'

    monkeypatch.setattr(RL, "_analyze_channel", lambda: {"base": "b", "key": "k", "model": "m"})
    monkeypatch.setattr(RL, "_post_chat", fake_post)
    monkeypatch.setattr(RL, "_shrink_for_analysis", lambda b: b)
    d = RL.design_shot_prompt(b"img", gen_type="scene_person", ratio="1:1", product_info="卡")
    assert seen[0] == RL.get_settings().IMAGE_RELAY_DESIGN_MAX_TOKENS
    assert seen[1] > seen[0], "重试必须放大 max_tokens"
    assert d["prompt_en"].startswith("keep the product")


def test_salvage_design_recovers_fields_from_truncated_json():
    """半截 JSON 也要能捞出已写完的 prompt_en / inventory"""
    from services import relay_image_service as RL
    truncated = (
        '{"product":{},"inventory":["letter balloon x1","witch hat balloon x1"],'
        '"prompt_en":"Keep the product exactly: two balloons, adult model wearing them.",'
        '"prompt_cn":"保留商品'
    )
    out = RL._salvage_design(truncated)
    assert out["prompt_en"].startswith("Keep the product exactly")
    assert out["inventory"] == ["letter balloon x1", "witch hat balloon x1"]
    assert "prompt_cn" not in out, "被截断的字段不能半截返回"


def test_salvage_design_empty_when_no_prompt():
    from services import relay_image_service as RL
    assert RL._salvage_design("") == {}
    assert RL._salvage_design('{"foo":"bar"') == {}


# ---------- 带人物场景：逐张差异化 ----------

def test_plan_design_jobs_expands_person_shots_only():
    """scene_person 每张一个设计任务；其他类型同类型只设计一次"""
    plan = [
        ("white_background", "1:1", None, "白底图", 0, False, 1.5),
        ("white_background", "1:1", None, "白底图", 1, False, 1.5),
        ("scene_person", "1:1", None, "场景图（带人物）", 0, False, 1.5),
        ("scene_person", "1:1", None, "场景图（带人物）", 1, False, 1.5),
        ("scene_person", "1:1", None, "场景图（带人物）", 2, False, 1.5),
        ("scene", "1:1", None, "场景图（不带人物）", 0, False, 1.5),
    ]
    jobs = S._plan_design_jobs(plan)
    assert [j[1] for j in jobs] == [
        "white_background", "scene_person", "scene_person", "scene_person", "scene",
    ]
    assert [j[0] for j in jobs] == [0, 2, 3, 4, 5]
    assert [j[3] for j in jobs if j[1] == "scene_person"] == [0, 1, 2]


def test_person_variations_are_pairwise_distinct():
    """四档变化必须在构图/姿态/光位/场景四个维度都不同，否则多张会撞图"""
    from services import relay_image_service as RL
    assert len(RL.PERSON_VARIATIONS) >= 4
    for key in ("framing", "pose", "light", "setting"):
        vals = [v[key] for v in RL.PERSON_VARIATIONS]
        assert len(set(vals)) == len(vals), f"{key} 存在重复"


def test_design_user_text_injects_variation():
    from services import relay_image_service as RL
    v = RL.PERSON_VARIATIONS[1]
    txt = RL._design_user_text("scene_person", "1:1", "卡", "", "", variation=v)
    assert "THIS SHOT" in txt
    assert v["framing"] in txt and v["light"] in txt and v["setting"] in txt
    assert "override" in txt
    # 不带人物时不应出现 THIS SHOT 段
    assert "THIS SHOT" not in RL._design_user_text("scene", "1:1", "卡", "", "")


def test_card_to_text_carries_style_context():
    """风格锚点进卡，保证同一任务多张图的场景主题一致"""
    text = S._card_to_text({"product_name_cn": "皇冠", "style_context": "greek classical"})
    assert "风格倾向：greek classical" in text


# ---------- 无人场景图：陈列载体 + 去人保商品 ----------

def test_scene_template_requires_display_and_keeps_product_when_removing_person():
    """参考图是模特穿戴照时，模型曾把商品连同人物一起删掉（实测空廊），
    必须写明陈列方式与「去人连商品一起去」。"""
    p = S._build_prompt("scene", "品名：维京腰包套装", "", "")
    assert "逐件清点" in p, "必须要求逐件清点（PE 是唯一看图环节）"
    assert "商品完整、独立呈现" in p, "无人场景必须写清商品怎么摆"
    assert "不得连同商品一起去掉" in p
    assert "不出现任何人物" in p
    assert "柱子、石阶" not in p and "拱门" not in p, "示例词不得再指向具体建筑风格"
    assert "本张要求" not in p, "不得再指定构图/姿态/光线"


def test_single_shot_has_no_composition_prescription():
    """2026-10-27 裁定：单张不再指定构图/姿态/光线，全部交给 PE。
    2026-10-28 进一步取消「多张必须不同」的追加句：PE 是确定性函数（同输入两次 md5 相同），
    该句进不了 PE 输出，多张差异只能靠输入侧锚点，所以 multi_shot 现在是无副作用的兼容形参。"""
    p1 = S._build_prompt("scene_person", "品名：X", "", "")
    for banned in ("构图 半身", "姿态 站姿自然", "光线 左侧低角度", "发丝带柔和轮廓光", "3/4 角度"):
        assert banned not in p1, f"单张不应指定具体构图/姿态/光线：{banned}"

    p2 = S._build_prompt("scene_person", "品名：X", "", "", multi_shot=True)
    assert p2 == p1, "multi_shot 不再改写提示词（差异靠输入侧锚点）"
    assert "半身" not in p2 and "发丝" not in p2


# ---------- 超时后结果回收 ----------

def test_should_reclaim_only_timeout_failures():
    """只回收「超时失败」：重试接手/已回收/已取消都不能被旧图覆盖"""
    assert S._should_reclaim_item({"status": "失败", "error": "生成超时（超过 30 分钟）"})
    assert not S._should_reclaim_item({"status": "已完成", "url": "u"})
    assert not S._should_reclaim_item({"status": "排队中", "error": None})
    assert not S._should_reclaim_item({"status": "已取消", "error": "任务已取消"})
    assert not S._should_reclaim_item({"status": "失败", "error": "生成完成但未找到输出文件"})
    assert not S._should_reclaim_item({})


def test_timeout_recovery_delays_are_spread_out():
    """回收不能立即重试（ComfyUI 大概率还在排队），要有间隔且轮次有限"""
    d = S._TIMEOUT_RECOVERY_DELAYS
    assert 1 <= len(d) <= 4
    assert list(d) == sorted(d) and len(set(d)) == len(d)
    assert d[0] >= 60, "首轮至少等 60s，否则等于原地空转"


# ---------- 参考图模特：只借佩戴位置，不借人 ----------

def test_scene_person_does_not_copy_reference_model():
    """参考图里的模特曾被整体搬进成图（实测：墨绿泡泡裙被原样复制），
    必须写明只沿用佩戴位置，长相/服装/机位/姿态重新设计。"""
    p = S._build_prompt("scene_person", "品名：维京腰包\n使用方式：腰带系于腰部", "", "")
    for kw in ("只作佩戴位置参考", "一律重新设计", "目标人群"):
        assert kw in p, f"缺少约束: {kw}"
    # 例外通道：运营在补充要求里显式要求时才沿用
    assert "仅当补充要求明确要求沿用时才沿用" in p
    # 年龄不做硬限制（面向中老年的商品要用中老年模特），只禁儿童
    assert "25~40" not in p and "25~35" not in p
    assert "严禁儿童" in p


def test_scene_forbids_reference_model_residue():
    p = S._build_prompt("scene", "品名：维京腰包", "", "")
    assert "不得连同商品一起去掉" in p
    assert "服装与背景陈设也不得残留" in p


# ---------- 模板体系统一（brief 式）----------

def test_all_brief_templates_share_structure():
    """场景两模板用精简 brief（分析+委托+少量硬约束）；其余类型仍是任务式 brief。
    两者都必须：含占位符替换结果、无悬空补充要求、含件数约束。"""
    for t in ("scene", "scene_person"):
        p = S._build_prompt(t, "品名：X", "", "")
        assert p.startswith("根据参考图分析商品"), f"{t} 未以分析指令开头"
        assert "逐件清点" in p, f"{t} 缺逐件清点"
        assert "不得改动商品" in p, f"{t} 缺件数约束"
        assert "由你决定" in p, f"{t} 未把画面设计权交给 PE"

    for t in ("closeup", "closeup_white", "selling_point",
              "aplus", "aplus_normal", "aplus_mobile"):
        p = S._build_prompt(t, "品名：X", "", "")
        assert p.startswith("任务："), f"{t} 未以任务描述开头"
        assert "商品不变量" in p, f"{t} 缺件数约束"

    # aplus_advanced 2026-10-28 改成分析式 brief + {text_clause} 占位（版式接入后仍沿用）
    p = S._build_prompt("aplus_advanced", "品名：X", "", "")
    assert p.startswith("根据参考图分析商品"), "aplus_advanced 未以分析指令开头"
    assert "不得改动商品" in p, "aplus_advanced 缺件数约束"
    assert "{text_clause}" not in p, "aplus_advanced 的 {text_clause} 未被替换"

    for t in ("scene", "scene_person", "closeup", "closeup_white", "selling_point",
              "aplus", "aplus_advanced", "aplus_normal", "aplus_mobile"):
        p = S._build_prompt(t, "品名：X", "", "")
        assert "{product_info}" not in p and "{extra_prompt}" not in p
        assert not p.rstrip().endswith("补充要求："), f"{t} 有空悬的补充要求"


def test_white_background_keeps_platform_hard_spec():
    """白底图保留固定硬规范（纯白 + 无道具），不走 brief 式"""
    p = S._build_prompt("white_background", "品名：X", "", "")
    assert "#FFFFFF" in p and "纯白" in p
    assert "无道具" in p


# ---------- A+ 版式（对齐官方「选择排版 → 每种排版生成一张」）----------

def _layout_svc():
    from services import ai_image_layout_service as L
    return L


def test_layout_presets_are_well_formed():
    """预设库是运营可改的 JSON：结构/分区/画幅必须自洽，否则前端缩略图会画歪"""
    L = _layout_svc()
    presets = L.load_presets()
    assert len(presets) >= 8, "内置版式至少 8 个"
    ids = [p["id"] for p in presets]
    assert len(ids) == len(set(ids)), "版式 id 不能重复"
    for p in presets:
        assert p["key"] == f"preset:{p['id']}"
        assert p["ratio_tag"] in L.RATIO_TAGS
        assert len(p["structure"]) >= 40, f"{p['id']} 的分区描述太短，撑不起版式约束"
        assert p["zones"], f"{p['id']} 缺分区数据"
        for z in p["zones"]:
            assert 0 <= z["x"] <= 1 and 0 <= z["y"] <= 1
            assert z["x"] + z["w"] <= 1.001 and z["y"] + z["h"] <= 1.001
            assert z["t"] in ("img", "txt", "band", "gap")


def test_parse_layout_keys_validates_and_dedupes():
    L = _layout_svc()
    assert L.parse_layout_keys("") == []
    assert L.parse_layout_keys('["preset:a","preset:a","db:3"]') == ["preset:a", "db:3"]
    for bad in ('{"a":1}', '["oops:1"]', '["preset"]'):
        try:
            L.parse_layout_keys(bad)
        except L.LayoutError:
            continue
        raise AssertionError(f"应拒绝非法入参: {bad}")
    too_many = "[" + ",".join(f'"preset:p{i}"' for i in range(9)) + "]"
    try:
        L.parse_layout_keys(too_many)
    except L.LayoutError:
        pass
    else:
        raise AssertionError("超过上限应报错")


def test_layout_clause_carries_zone_structure_and_text_rule():
    L = _layout_svc()
    lay = L.get_preset("left_text_right_image")
    no_text = L.layout_clause(lay, no_text=True)
    assert "左侧约 40%" in no_text and "版式（画面分区必须按此执行" in no_text
    assert "不得渲染任何文字" in no_text
    # 2026-10-29 用户裁定：留白必须是背景的自然延伸，不能是「一块矩形色块」
    assert "与背景连续的整片留白" in no_text and "矩形色块" in no_text
    with_text = L.layout_clause(lay, no_text=False)
    assert "可排布简短文案" in with_text


def test_uploaded_layout_clause_points_at_reference_index():
    """上传版式没有文字结构，只能靠「第 N 张参考图」指位，
    且必须禁止搬运其中的商品/文案——否则模型会把别人的商品画进来。"""
    L = _layout_svc()
    lay = {"key": "db:1", "source": "upload", "structure": "", "image_url": "http://x/y.jpg"}
    clause = L.layout_clause(lay, no_text=True, ref_index=3)
    assert "第 3 张参考图" in clause
    assert "不得带入画面" in clause and "不得渲染任何文字" in clause


def test_random_preset_matches_ratio_tag():
    L = _layout_svc()
    for tag in L.RATIO_TAGS:
        got = L.random_preset(tag)
        assert got and got["ratio_tag"] == tag


def test_uploaded_layout_clause_is_skipped_when_image_missing():
    """版式图没送到时不能写「照第 N 张参考图」——指向不存在的图等于没约束，
    属于静默降级；宁可不注入，让日志能看出没生效。"""
    L = _layout_svc()
    lay = {"key": "db:9", "source": "official", "structure": "", "image_url": "http://x/y.jpg"}
    assert L.layout_clause(lay, no_text=True) == ""
    assert L.layout_clause(lay, no_text=True, ref_index=0) == ""
    # 有分区文字的版式不受影响
    assert L.layout_clause(L.get_preset("left_text_right_image"), no_text=True) != ""


# ---------- A+ 版式版正文（选定版式时替换自由构图条款）----------

def test_aplus_with_layout_uses_layout_body_not_freeform():
    """选了版式还写「一侧留三分之一空域 / 不得出现分栏」，会和三格分栏类版式直接矛盾；
    带版式时必须整体切换到 aplus_layout 正文，构图交给版式。"""
    L = _layout_svc()
    struct = L.get_preset("three_column")["structure"]
    p = S._build_prompt("aplus_advanced", "品名：X", "", "", layout_structure=struct)
    assert "按下面给定的版式组织" in p, "未走版式版正文"
    assert struct[:40] in p, "版式分区文字没有嵌入"
    for banned in ("一侧留出约三分之一空域", "不得出现白边、边框、卡片或分栏", "场景、陈设、道具、色彩、光线与构图由你决定"):
        assert banned not in p, f"版式版仍残留自由构图条款: {banned}"
    # 「版式成稿样例」句子只在真正把版式图送进 PE 时出现（2026-10-29：有结构的版式默认不送图）
    assert "版式成稿样例" not in p, "未送图时不应出现第二张参考图声明"
    p_img = S._build_prompt("aplus_advanced", "品名：X", "", "", layout_structure=struct,
                            layout_with_image=True)
    assert "第二张参考图是版式成稿样例" in p_img, "送图时缺少「只借分区不搬内容」声明"
    assert "不得改动商品" in p and "{layout_structure}" not in p and "{text_clause}" not in p
    # 无文字模式：文字区必须给正向留白指令（负向禁令压不住，实测出乱码标题）
    # 2026-10-29 用户裁定：「纯色色块填充」会被画成一块可见的浅色矩形，改成「与背景连续的整片留白」
    p2 = S._build_prompt("aplus_advanced", "品名：X", "", "", layout_structure=struct, no_text=True)
    assert "与背景连续的整片留白" in p2 and "不留任何矩形色块" in p2
    assert "纯色色块填充" not in p2, "旧措辞会画出可见矩形块"
    # 「留白」的定义只给一次（结构段 + 文字规则各写一遍会稀释指令）
    assert p2.count("与背景连续的整片留白") == 1, "留白定义重复出现"
    assert "禁止：画面内出现任何可读的文字" in p2
    # 结构文字里的「标题/文案/文字区」必须被改写成留白（这些词本身会诱发模型画字形）
    assert "横向三等分" in p2 and "标题" not in p2 and "文案" not in p2.replace("品牌文案","")
    p4 = S._build_prompt("aplus_advanced", "品名：X", "", "", no_text=True,
                         layout_structure="上部约40%为标题文字区，两行大标题；中部为商品大图区")
    assert "标题" not in p4 and "留白区" in p4 and "与背景连续的整片留白" in p4
    # 「留白块/留白条」会暗示一块可见矩形，必须消失（用户裁定）
    assert "留白块" not in p4 and "留白条" not in p4
    p3 = S._build_prompt("aplus_advanced", "品名：X", "", "", layout_structure=struct, no_text=False)
    assert "画面可出现排版文字" in p3


def test_no_text_sanitizer_strips_layout_decor():
    """版式描述里「分区怎么画」的措辞必须整段摘掉，只留位置与占比。

    2026-10-29 实测 db:19（官方图片模板）：原文「以硬边分隔形成多格拼接布局」
    被模型照字面执行 —— 底部 25% 画成四列半透明矩形 + 硬边竖线压在商品上。
    无文字模式只需要「哪个区占多少、放什么」，分隔线/边框/卡片交给设计师后期做。
    """
    raw = ("整幅为森林场景背景图，上方约20%为横向主标题文字区，中部约55%为商品大图区域，"
           "底部约25%为四列等宽文字区，以硬边分隔形成多格拼接布局。")
    out = S._sanitize_structure_no_text(raw)
    # 占比必须保住（这是版式唯一的有效信息）
    assert "20%" in out and "55%" in out and "25%" in out
    # 装饰手段必须消失
    for token in ("硬边", "分隔", "拼接", "圆角", "边框", "色块", "卡片", "四列"):
        assert token not in out, f"装饰词未摘除: {token}"
    # 文字位词必须消失（会诱发画字）
    for token in ("标题", "文案", "文字"):
        assert token not in out, f"文字位词未改写: {token}"
    assert "留白区" in out

    # 装饰性名词要收敛成留白区，而不是留一个「信息条/色块」让模型去画块
    assert S._sanitize_structure_no_text(
        "下方约25%为半透明圆角信息条，上方为商品图") == "下方约25%为留白区，上方为商品图"
    # 摘词后不能留下病句（相邻留白、重复标点、悬挂连接词）
    for case in ("画面为留白区留白区，以形成多格布局", "左侧约40%为留白、、副留白"):
        t = S._sanitize_structure_no_text(case)
        for bad in ("留白区留白区", "留白留白", "、、", "，、", "，以"):
            assert bad not in t, f"摘词后残留病句 {bad}: {t}"


def test_aplus_without_layout_keeps_freeform_body():
    """无版式时保持原自由构图正文（不受 aplus_layout 模板影响）"""
    p = S._build_prompt("aplus_advanced", "品名：X", "", "")
    assert "一侧留出约三分之一空域给文案" in p
    assert "按下面给定的版式组织" not in p
    # 主副图类型不受 layout_structure 影响（传了也只对 aplus 生效）
    p2 = S._build_prompt("scene", "品名：X", "", "", layout_structure="左侧40%文案区")
    assert "按下面给定的版式组织" not in p2


# ---------- 执行互斥 / 重启恢复契约 ----------

def test_claim_task_slot_is_mutually_exclusive():
    """同一任务不允许并行执行：重试与旧线程收尾交错时不能双跑"""
    tid = 990001
    try:
        assert S._claim_task_slot(tid) is True
        assert S._claim_task_slot(tid) is False
        # 已有执行在跑时 _ensure_worker 必须明确返回 False，调用方据此拒绝重试
        assert S._ensure_worker(tid) is False
    finally:
        S._release_task_slot(tid)
    assert S._claim_task_slot(tid) is True
    S._release_task_slot(tid)


def test_user_queue_accepts_second_task(monkeypatch):
    """同一用户已有任务在跑时，第二单进入队列而不是被拒绝提交"""
    uid = 990002
    advanced = []
    monkeypatch.setattr(S, "_advance_user_queue", lambda u: advanced.append(u))
    try:
        assert S._register_task_run(990011, uid, {"x": 1}) is True
        assert S._user_current[uid] == 990011
        # 第二单不再报错，而是排队等待
        assert S._register_task_run(990012, uid, {"x": 2}) is False
        assert S._pending_by_user[uid][0][0] == 990012
        # 前一单释放后触发该用户队列推进
        S._release_task_slot(990011)
        assert advanced == [uid]
        assert S._user_current.get(uid) is None
        assert S._task_owner.get(990012) == uid
    finally:
        S._pending_by_user.pop(uid, None)
        S._task_owner.pop(990011, None)
        S._task_owner.pop(990012, None)
        S._user_current.pop(uid, None)
        S._running.discard(990011)
        S._running.discard(990012)


def test_relay_items_persist_prompt_before_generation():
    """中转站子项必须在生成前就落库（含最终提示词），否则重启后无法补跑"""
    class _Relay:
        PERSON_VARIATIONS = ["正面平视", "侧面45度"]

    plan = [
        ("white_background", "1:1", None, "白底图", 0, True, 1.5),
        ("scene_person", "3:4", (900, 1200), "场景图", 1, True, 1.5),
    ]
    items = S._build_relay_items(
        plan, ["prompt-0", "prompt-1"], {0: "白底中文描述"}, [{}, {}], None, _Relay(),
    )
    assert [it["status"] for it in items] == ["排队中", "排队中"]
    assert items[0]["final_prompt"] == "prompt-0"
    assert items[0]["prompt_cn"] == "白底中文描述"
    assert items[1]["final_prompt"] == "prompt-1"
    assert items[1]["target_size"] == [900, 1200]
    assert items[1]["variation"] in _Relay.PERSON_VARIATIONS


# ---------- 中转站退避重试 ----------

def test_relay_retry_delay_respects_header_and_cap():
    assert RS._retry_delay(0) == 1.5
    assert RS._retry_delay(1) == 3.0
    assert RS._retry_delay(0, "7") == 7.0
    assert RS._retry_delay(3, "99") == 10.0


class _FakeResp:
    def __init__(self, status, body="", headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self._body = body

    def json(self):
        return json.loads(self._body)


def test_relay_post_chat_retries_on_5xx(monkeypatch):
    """5xx 应退避重试，不能把整张图直接判失败"""
    calls = {"n": 0}
    ok_body = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    )

    class _Session:
        def post(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(500, "boom")
            return _FakeResp(200, ok_body)

    monkeypatch.setattr(RS, "_session", lambda: _Session())
    monkeypatch.setattr(RS.time, "sleep", lambda _s: None)
    out = RS._post_chat("https://example.com", "k", "m", [], 30)
    assert out == "ok"
    assert calls["n"] == 2


def test_relay_post_chat_raises_after_429_exhausted(monkeypatch):
    calls = {"n": 0}

    class _Session:
        def post(self, *a, **kw):
            calls["n"] += 1
            return _FakeResp(429, "limited", {"Retry-After": "0"})

    monkeypatch.setattr(RS, "_session", lambda: _Session())
    monkeypatch.setattr(RS.time, "sleep", lambda _s: None)
    with pytest.raises(RS.RelayError) as err:
        RS._post_chat("https://example.com", "k", "m", [], 30)
    assert "限流" in str(err.value)
    assert calls["n"] == RS._RELAY_RETRY_ATTEMPTS


# ---------- 删除任务时的 TOS 清理 ----------

def test_delete_task_tos_objects_dedupes(monkeypatch):
    """同一 URL 只删一次，失败不应影响其余对象"""
    from services import tos_service

    calls = []

    def fake_delete(url):
        calls.append(url)
        return url != "bad"

    monkeypatch.setattr(tos_service, "delete_file", fake_delete)
    S._delete_task_tos_objects(1, ["a", "a", "b", "bad"])
    assert calls == ["a", "b", "bad"]


# ---------- A+ 提示词通道实验（direct / append）----------

_WF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workflows")


def _build_aplus(workflow_file, **kw):
    return S._build_workflow(
        prompt="P", aspect_ratio="21:9", seed=7,
        image_names=["a.jpg", "b.jpg"], filename_prefix="T",
        workflow_path=os.path.join(_WF_DIR, workflow_file), **kw,
    )


def test_aplus_direct_workflow_bypasses_pe():
    """direct 变体：497 直连 571，无 PE 节点；多参考图仍正常注入 497"""
    wf = _build_aplus("qwen_image_workflow_aplus_direct.json")
    assert wf[S.NODE_ENCODE]["inputs"]["prompt"] == [S.NODE_PROMPT, 0]
    assert S.NODE_PE not in wf and "569" not in wf
    assert wf[S.NODE_ENCODE]["inputs"]["images.image_2"] == ["9102", 0]
    # PE 注入被跳过而不是 KeyError


def test_aplus_append_workflow_joins_contract():
    """append 变体：PE → JoinStringMulti(573) → 497；契约写入 572"""
    wf = _build_aplus(
        "qwen_image_workflow_aplus_append.json", layout_contract="【契约】三列",
    )
    assert wf["572"]["inputs"]["value"] == "【契约】三列"
    join = wf["573"]
    assert join["class_type"] == "JoinStringMulti"
    assert join["inputs"]["string_1"] == [S.NODE_PE, 0]
    assert join["inputs"]["string_2"] == [S.NODE_CONTRACT, 0]
    assert wf[S.NODE_ENCODE]["inputs"]["prompt"] == ["573", 0]
    assert wf[S.NODE_ENCODE]["inputs"]["images.image_2"] == ["9102", 0]


def test_build_workflow_rejects_missing_required_nodes(tmp_path):
    """A+ 专用工作流缺必需节点 → 报错而不是静默降级"""
    import json as _json
    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"571": {"inputs": {"value": ""}, "class_type": "PrimitiveStringMultiline"}}), encoding="utf-8")
    with pytest.raises(S.AIImageError, match="缺少必需节点"):
        S._build_workflow(
            prompt="P", aspect_ratio="1:1", seed=1, image_names=["a.jpg"],
            filename_prefix="T", workflow_path=str(bad),
        )


def test_aplus_contract_from_zones_and_structure():
    """zones 优先生成坐标契约；无 zones 用 structure（无字清洗）；无版式返回 None"""
    lay_zones = {
        "zones": [
            {"x": 0, "y": 0, "w": 0.4, "h": 1, "t": "txt"},
            {"x": 0.4, "y": 0, "w": 0.6, "h": 1, "t": "img"},
        ],
    }
    c = S._layout_contract(lay_zones, "aplus_advanced", no_text=True)
    assert "目标画布 1464×600" in c
    assert "留白区：x 0%–40%" in c
    assert "商品图区：x 40%–100%" in c
    assert "不得新增、删除、移动或合并分区" in c
    # P4：约束优先级分层 + 场景类型槽位 + coverage_plan 覆盖规则；不得再出现固定场地枚举
    assert "【约束优先级（高→低）】" in c
    assert "商品真实性与件数" in c
    assert "场景类型 usage" in c
    assert "商品覆盖按 571 的 coverage_plan" in c
    assert "展示性重复" in c and "不等于增加实际数量" in c
    assert "家居" not in c and "卧室" not in c and "桌面" not in c
    # 卡片规则只作用于商品图区，留白区仍禁卡片
    assert "该卡片规则只适用于商品图区" in c
    assert "留白区/间隙只允许背景自然延伸" in c

    lay_struct = {"structure": "顶部为横贯全宽的文字区，约占上部25%；下方为三列等宽商品图区"}
    c2 = S._layout_contract(lay_struct, "aplus_advanced", no_text=True)
    assert "目标画布 1464×600" in c2
    # 无字清洗：文字区 → 留白区
    assert "留白区" in c2 and "文字区" not in c2
    # 无 zones 的回退也必须是槽位轮换，而不是固定场地
    assert "按场景类型轮换" in c2 and "usage" in c2
    assert "家居" not in c2

    assert S._layout_contract(None, "aplus_advanced", True) is None
    assert S._layout_contract({"structure": ""}, "aplus_advanced", True) is None


def test_resolve_aplus_workflow_disabled_and_enabled(monkeypatch):
    """默认关闭返回 None；配置 direct/append 返回对应文件；非法值报错"""
    monkeypatch.setattr(S.settings, "IMAGE_APLUS_PROMPT_MODE", "", raising=False)
    assert S._resolve_aplus_workflow() is None

    monkeypatch.setattr(S.settings, "IMAGE_APLUS_PROMPT_MODE", "direct", raising=False)
    p = S._resolve_aplus_workflow()
    assert p and p.endswith("qwen_image_workflow_aplus_direct.json")

    monkeypatch.setattr(S.settings, "IMAGE_APLUS_PROMPT_MODE", "bogus", raising=False)
    with pytest.raises(S.AIImageError, match="配置非法"):
        S._resolve_aplus_workflow()



