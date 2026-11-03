"""AI 商品套图生成服务（Qwen-Image-2.1 图生图，对接与 H3 共用的 ComfyUI API）

流程：参考图上传 TOS + ComfyUI → 按「生成类型 × 张数」展开子任务、逐个提交工作流
（不同 seed）→ 轮询 /history → 逐张下载 → 上传 TOS → 增量回写任务记录。

接口（均需 Basic Auth，复用 H3_* 配置）：
    POST /upload/image      上传参考图
    POST /prompt            提交任务（JSON 里是工作流）
    GET  /history/{任务ID}   查状态（完成后 GET /view 下载图片）
"""
import io
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 生成类型：key（前端/模板键）→ 中文名
GEN_TYPES: Dict[str, str] = {
    "white_background": "白底图",
    "scene": "场景图",
    "scene_person": "场景图（带人物）",
    "closeup": "特写图",
    "selling_point": "卖点图",
    "aplus": "A+图",
}

# 前端比例 → ComfyUI ResolutionSelector 选项字符串
# 2026-10-19 探测 /object_info/ResolutionSelector：8 项均在服务端 options 内（含 2:3/3:2/21:9）。
# 运行时以 _supported_ratio_options() 动态校验为准：选项缺失即报错，绝不静默降级到 1:1。
ASPECT_RATIO_MAP: Dict[str, str] = {
    "1:1": "1:1 (Square)",                 # 1248x1248
    "3:4": "3:4 (Portrait Standard)",      # 1088x1440
    "4:3": "4:3 (Standard)",               # 1440x1088
    "9:16": "9:16 (Portrait Widescreen)",  # 928x1664
    "16:9": "16:9 (Widescreen)",           # 1664x928
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "21:9": "21:9 (Ultrawide)",
}

# 各比例的数值（宽/高），用于「自适应」按参考图宽高比选最近项
RATIO_VALUES: Dict[str, float] = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 1.5,
    "3:4": 0.75,
    "4:3": 4 / 3,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
}

# ===== v2（2026-10-15）：A+ 与主副图分组 =====
# A+ 类型（单选）：gen_ratio=生成用比例（ResolutionSelector 支持项）；
# target_size=目标精确尺寸（Pillow 中心裁剪+缩放后处理，A+ 比例 2.44:1/1.62:1 不在节点选项内）
# megapixels=生成时的总像素（按 target_size 反推，避免无谓算力与超采样）
# - advanced 1464x600=0.88MP、目标比 2.44:1 → 用 21:9(2.33) 生成，中心裁剪仅损失约 4%
#   （旧配置 16:9(1.79) 需裁掉 26% 高度，主体/A+ 文案会被切）
APLUS_SPECS: Dict[str, Dict[str, Any]] = {
    "aplus_advanced": {"label": "高级A+图", "gen_ratio": "21:9", "target_size": (1464, 600),
                       "megapixels": 1.0,
                       "desc": "超宽幅品牌横幅，A+ 页面顶部大图"},
    "aplus_normal":   {"label": "普通A+图", "gen_ratio": "16:9", "target_size": (970, 600),
                       "megapixels": 0.8,
                       "desc": "标准 A+ 页面横幅"},
    "aplus_mobile":   {"label": "手机A+图", "gen_ratio": "4:3",  "target_size": (600, 450),
                       "megapixels": 0.5,
                       "desc": "移动端 A+ 图，小屏浏览"},
    # 品牌A+横幅（970×300 = 3.23:1）：现有 ResolutionSelector 无此比例，
    # 由专用工作流自定义 EmptyLatent（1888×576 ≈ 3.28:1）生成后轻微裁剪到 970×300。
    "aplus_banner":   {"label": "品牌A+横幅", "gen_ratio": "21:9", "target_size": (970, 300),
                       "megapixels": 1.1,
                       "desc": "画面只有品牌名（AI 艺术字），风格跟随商品主图；不使用排版模板"},
}
# 主副图类型（多选）
# scene 与 scene_person 是「同一类型的两个变体」：scene 保留旧 key（历史任务回填依赖它），
# 语义收窄为「不带人物」；带人物另开 scene_person，因为两者的画面设计与合规约束完全不同。
MAIN_GEN_TYPES: Dict[str, str] = {
    "selling_point": "卖点图",
    "white_background": "白底图",
    "scene": "场景图（不带人物）",
    "scene_person": "场景图（带人物）",
    "closeup": "特写图",
    "closeup_white": "特写白底图",
}
# 主副图卡片的一句话说明（/options 下发，前端不再各写一份）
MAIN_TYPE_DESCRIPTIONS: Dict[str, str] = {
    "selling_point": "商品置于卖点匹配的场景，配文字排版展示卖点",
    "white_background": "商品精修后放置于纯白色背景",
    "closeup": "与卖点匹配的局部特写细节",
    "closeup_white": "白底局部特写细节",
}
# 场景图在 UI 上是一个父项 + 两条子路径：父项不直接对应生成类型，
# 勾选状态由子路径（scene/scene_person）决定；这里的 key/label/hint 一并下发，
# 避免前端再维护一份易与后端脱节的文案。
SCENE_GROUP: Dict[str, Any] = {
    "key": "scene",
    "label": "场景图",
    "hint": "商品置于真实使用场景，可展开选择带人物 / 不带人物",
    "variants": [
        {"key": "scene", "label": "场景图（不带人物）",
         "hint": "商品置于真实使用场景，画面中不出现任何人物"},
        {"key": "scene_person", "label": "场景图（带人物）",
         "hint": "成年模特按使用方式佩戴/使用商品；严禁儿童"},
    ],
}
# 品牌自定义设置选项（前端下拉用）
BRAND_OPTIONS: Dict[str, List[str]] = {
    "countries": ["US 美国", "CA 加拿大", "UK 英国", "DE 德国", "FR 法国",
                  "IT 意大利", "ES 西班牙", "JP 日本", "AU 澳大利亚"],
    "languages": ["英语", "德语", "法语", "意大利语", "西班牙语", "日语"],
    "platforms": ["亚马逊", "eBay", "独立站", "Temu", "Shein"],
    "font_styles": ["智能字体风格", "现代无衬线", "经典衬线", "圆润可爱", "硬朗工业"],
}

# 这三项缺失会让模型按自己偏好决定语境与文字语言，且详情页留空 —— 提交时统一补默认
DEFAULT_BRAND_SETTINGS: Dict[str, str] = {
    "country": "US 美国",
    "language": "英语",
    "platform": "亚马逊",
}


def _compile_brand_genes(bs: Optional[Dict[str, Any]]) -> str:
    """品牌设置 → 一行补充约束（只陈述，不展开成画面指令，避免压过 PE 的判断）"""
    if not bs:
        return ""
    parts = []
    if bs.get("main_color"):
        parts.append(f"主色调 {bs['main_color']}")
    if bs.get("font_style") and bs["font_style"] != "智能字体风格":
        parts.append(f"文字排版 {bs['font_style']}")
    if bs.get("custom_text"):
        parts.append(f"额外要求 {bs['custom_text']}")
    if bs.get("country"):
        parts.append(f"目标市场 {bs['country']}")
    if bs.get("language"):
        parts.append(f"画面文字语言 {bs['language']}")
    if bs.get("platform"):
        parts.append(f"发布平台 {bs['platform']}")
    return "；".join(parts)


def _fit_to_target(content: bytes, target: Tuple[int, int]) -> bytes:
    """A+ 图后处理：中心裁剪到目标宽高比 → 缩放到精确目标尺寸"""
    from io import BytesIO
    from PIL import Image as PILImage

    img = PILImage.open(BytesIO(content))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    tw, th = target
    tr = tw / th
    w, h = img.size
    r = w / h
    if abs(r - tr) > 0.01:
        if r > tr:      # 过宽 → 裁左右
            nw = int(h * tr)
            x = (w - nw) // 2
            img = img.crop((x, 0, x + nw, h))
        else:           # 过高 → 裁上下
            nh = int(w / tr)
            y = (h - nh) // 2
            img = img.crop((0, y, w, y + nh))
    img = img.resize((tw, th), PILImage.LANCZOS)
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# 工作流节点编号（与 workflows/qwen_image_workflow.json 对应）
NODE_PROMPT = "571"        # PrimitiveStringMultiline，输入 value
NODE_REF_IMAGE = "475"     # LoadImage，输入 image
NODE_RESOLUTION = "13"     # ResolutionSelector，输入 aspect_ratio / megapixels
NODE_SEED = "502"          # rgthree Seed，输入 seed（-1 为随机，这里显式指定）
NODE_SAVE = "487"          # SaveImage，输入 filename_prefix
NODE_ENCODE = "497"        # TextEncodeQwenImage21，多图走 images.image_1..image_N（AUTOGROW）
NODE_PE = "565"            # TE_Qwen_Image_2_1_Prompt_Enhancer，多图走 图片/图片2..图片8
NODE_SCALE = "532"         # ImageScaleToTotalPixels，参考图统一到同一像素尺度
NODE_CONTRACT = "572"      # A+ append 变体：版式契约节点（PE 之后拼接，PE 改写不到）

# A+ 提示词通道实验（2026-10-29）：空=现有链路；direct=跳过 PE；append=PE 后强制拼接版式契约
APLUS_PROMPT_MODES = ("", "direct", "append")
_APLUS_WORKFLOW_FILES = {
    "direct": "qwen_image_workflow_aplus_direct.json",
    "append": "qwen_image_workflow_aplus_append.json",
}

# zones 类型 → 分区中文名（无字/有字两套措辞）
_ZONE_TYPE_LABELS_NOTEXT = {
    "img": "商品图区", "txt": "留白区", "band": "留白横带", "gap": "背景间隙",
}
_ZONE_TYPE_LABELS_TEXT = {
    "img": "商品图区", "txt": "标题文字区", "band": "信息条（可排文案）", "gap": "背景间隙",
}

# 商品图区的场景类型槽位（2026-10-30 P3）：不写死具体场地，
# 只给「使用中 / 环境中 / 陈列细节」三类语义，具体环境与道具由 PE 按商品用途推导。
# 同一版式里多个 img 区按此顺序轮换，避免三列全是同一种构图。
_SCENE_SLOT_ORDER = ("usage", "context", "display")
_SCENE_SLOT_HINTS = {
    "usage": "商品正在被使用，人物或手部与商品自然互动",
    "context": "商品在由用途推导的真实环境中自然摆放，背景与陈设服务于主题",
    "display": "商品陈列位或细节特写，突出材质、工艺或关键细节",
}


def _resolve_aplus_workflow() -> Optional[str]:
    """按 IMAGE_APLUS_PROMPT_MODE 解析 A+ 专用工作流路径；未启用返回 None。

    模式非法或文件缺失立即报错（配置错误要在提交期暴露，不能静默回退旧链路）。
    """
    mode = (getattr(settings, "IMAGE_APLUS_PROMPT_MODE", "") or "").strip().lower()
    if not mode:
        return None
    if mode not in APLUS_PROMPT_MODES:
        raise AIImageError(
            f"IMAGE_APLUS_PROMPT_MODE 配置非法: {mode!r}（可选 {'/'.join(APLUS_PROMPT_MODES)}）"
        )
    # 与 IMAGE_WORKFLOW_PATH 同目录：无论从哪个工作目录启动都正确
    path = os.path.join(
        os.path.dirname(os.path.abspath(settings.IMAGE_WORKFLOW_PATH)),
        _APLUS_WORKFLOW_FILES[mode],
    )
    if not os.path.isfile(path):
        raise AIImageError(f"A+ 工作流文件缺失: {path}")
    return path


def _pct(v: float) -> str:
    return f"{round(float(v) * 100):g}%"


def _zones_to_clause(zones: List[Dict[str, Any]], canvas: Tuple[int, int],
                     no_text: bool) -> str:
    """zones 坐标 → 机器可读的分区约束串（官方预设带 zones 时优先用它）。

    zones 是 0–1 归一化坐标（x/y 左上角 + w/h + t 类型），与画布尺寸解耦：
    同一版式可适配 advanced/normal/mobile 三种画布。
    """
    labels = _ZONE_TYPE_LABELS_NOTEXT if no_text else _ZONE_TYPE_LABELS_TEXT
    lines = [f"目标画布 {canvas[0]}×{canvas[1]}。", "画面分区（百分比坐标，左上角为原点，逐条执行）："]
    img_index = 0
    for i, z in enumerate(zones, start=1):
        t = z.get("t") or "img"
        label = labels.get(t, "商品图区")
        line = (
            f"{i}. {label}：x {_pct(z.get('x', 0))}–{_pct(z.get('x', 0) + z.get('w', 0))}，"
            f"y {_pct(z.get('y', 0))}–{_pct(z.get('y', 0) + z.get('h', 0))}"
        )
        if t == "img":
            slot = _SCENE_SLOT_ORDER[img_index % len(_SCENE_SLOT_ORDER)]
            img_index += 1
            line += f"；场景类型 {slot}（{_SCENE_SLOT_HINTS[slot]}）"
            # P4：zones 只描述几何/场景；组件展示由 571 的 coverage_plan 决定。
            # 仅当版式数据显式给出覆盖策略时才写进契约。
            coverage = z.get("product_coverage") or z.get("coverage")
            if coverage:
                line += f"；商品覆盖 {coverage}"
        lines.append(line)
    return "\n".join(lines)


def _layout_contract(lay: Optional[Dict[str, Any]], aplus_type: str,
                     no_text: bool) -> Optional[str]:
    """A+ 子项的最终版式契约（append 模式写入 572，PE 之后拼接，PE 改写不到）。

    官方预设优先用 zones 坐标生成；没有 zones（如 DB 官方模板/上传版式）退回
    structure 文本（无字模式下先做同一套清洗）。都没有 → 返回 None，调用方
    回退旧链路，绝不静默地空转契约。

    2026-10-29 对标真实 A+ 成品补两条正向语言（此前产出是「白底陈列」，差距在）：
    1. 商品图区 = 真实使用场景摄影（不是白底棚拍/简单陈列）；
    2. 各图区呈现为圆角照片卡片（无字清洗摘掉了「圆角/卡片」，这里正向补回——
       之前的「禁边框/禁卡片」只该约束留白区，不该错杀图区的设计语言）。

    2026-10-30 P3：删除「家居/卧室/桌面」等固定场地枚举，改为场景类型槽位
    （usage/context/display），环境与道具由 PE 按商品用途推导；并声明契约优先级
    高于前述任何风格描述。卡片规则只作用于商品图区，留白区仍禁卡片/边框。
    """
    if not lay:
        return None
    spec = APLUS_SPECS.get(aplus_type) or {}
    canvas = tuple(spec.get("target_size") or (1464, 600))
    zones = lay.get("zones") or []
    if zones:
        body = _zones_to_clause(zones, canvas, no_text)
    else:
        structure = (lay.get("structure") or "").strip()
        if not structure:
            return None
        if no_text:
            structure = _sanitize_structure_no_text(structure)
        slot_cycle = " / ".join(
            f"{s}（{_SCENE_SLOT_HINTS[s]}）" for s in _SCENE_SLOT_ORDER
        )
        body = (
            f"目标画布 {canvas[0]}×{canvas[1]}。\n"
            f"画面分区（位置与占比必须遵守）：{structure}\n"
            f"各商品图区按场景类型轮换：{slot_cycle}；具体环境、陈设与道具由商品用途推导。"
        )
    if no_text:
        rule = (
            "【约束优先级（高→低）】1 商品真实性与件数；2 分区位置、数量与比例；"
            "3 无文字规则；4 各分区的场景类型与商品覆盖；"
            "5 在上述约束内，采用前述 PE 输出的视觉主题、色彩、场景与摄影方案。"
            "商品覆盖按 571 的 coverage_plan / zone_assignments 执行：每个分区只展示该区指定的组件；"
            "全局组件数量以事实卡 components 为准，不得改变；同一组件在不同分区重复出现属于展示性重复，"
            "不等于增加实际数量；不得为了满足每个分区而重复整套商品；不得遗漏 coverage_plan 要求覆盖的组件。"
            "每个商品图区都是一张独立的真实商业场景摄影，禁止退化为白底商品陈列，"
            "也不得把参考图的白底拼贴背景、原始排列或原图构图复刻进画面；"
            "各图区场景可不同，但整体主题、色彩与摄影语言必须统一。"
            "各商品图区呈现为带圆角的独立照片卡片，卡片之间是均匀的背景间隙；"
            "该卡片规则只适用于商品图区，不适用于留白区。"
            "留白区/间隙只允许背景自然延伸，不得出现商品、文字、色块、卡片、边框或阴影分界。"
            "不得新增、删除、移动或合并分区。"
        )
    else:
        rule = (
            "【约束优先级（高→低）】1 商品真实性与件数；2 分区位置、数量与比例；"
            "3 各分区的场景类型与商品覆盖；"
            "4 在上述约束内，采用前述 PE 输出的视觉主题、色彩、场景与摄影方案。"
            "商品覆盖按 571 的 coverage_plan / zone_assignments 执行；全局组件数量以事实卡 components 为准，"
            "不得改变；展示性重复不等于增加实际数量；不得为了覆盖而重复整套商品。"
            "每个商品图区都是一张独立的真实商业场景摄影，场景与环境由商品用途推导；"
            "各图区场景可不同，但整体主题、色彩与摄影语言必须统一。"
            "各商品图区呈现为带圆角的独立照片卡片，卡片之间是均匀的背景间隙；"
            "该卡片规则只适用于商品图区。"
            "不得新增、删除、移动或合并分区。"
        )
    return f"【最终版式约束 —— 优先级最高，逐条执行】\n{body}\n{rule}"

# 参考图上限：受 PE 节点可选输入约束（图片/图片2..图片8 共 8 个通道）。
# 编码器 images 通道虽支持 image_1..image_16，但第 9 张进不了 PE，故上限取 8。
MAX_REF_IMAGES = 8
# 单任务生成张数上限（A+ 张数 + 各主副图类型张数之和），防止一次性灌满 ComfyUI 队列
MAX_SHEETS_PER_TASK = 12
# 主副图默认生成像素（与工作流模板一致）
DEFAULT_MEGAPIXELS = 1.5

# ResolutionSelector 选项缓存（服务端能力探测，TTL 内复用，避免每张图都打 /object_info）
_RATIO_CACHE: Dict[str, Any] = {"at": 0.0, "options": None}
_RATIO_CACHE_TTL = 600
_ratio_lock = threading.Lock()

_running: set = set()
_lock = threading.Lock()
# 取消标记：轮询线程在每一轮开头检查，命中即停止回收未完成子项
_cancel_flags: set = set()
# 运行代号：同一任务每次执行（提交 / 重试）分配一个新代号，
# 旧线程落库前比对代号即可丢弃过期结果 —— 否则「取消后立刻重试」时，
# 旧线程跑完仍会写回「已完成」，覆盖重试的成果。
_run_tokens: Dict[int, int] = {}

# 用户级排队：同一用户同时只执行一单，后续提交进入队列等待（不再拒绝提交）。
# 队列保存在进程内（含参考图字节），服务重启后未启动的排队任务由
# resume_pending_tasks 判失败并提示重新提交（与既有重启口径一致）。
_pending_by_user: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
_task_owner: Dict[int, int] = {}
_user_current: Dict[int, int] = {}

# 用户级提交锁：「查/插 + 入队」是 check-then-act，双开页面/并发请求下会各建一单。
# 进程内锁对单 worker 足够；多 worker 需换 DB 唯一约束（同幂等实现备注）。
_user_submit_locks: Dict[int, threading.Lock] = {}
_user_locks_guard = threading.Lock()


def _user_submit_lock(user_id: int) -> threading.Lock:
    with _user_locks_guard:
        lock = _user_submit_locks.get(user_id)
        if lock is None:
            lock = _user_submit_locks[user_id] = threading.Lock()
        return lock


def _new_run_token(task_id: int) -> int:
    with _lock:
        tok = _run_tokens.get(task_id, 0) + 1
        _run_tokens[task_id] = tok
        return tok


def _is_current_token(task_id: int, token: int) -> bool:
    with _lock:
        return _run_tokens.get(task_id, 0) == token


def _is_cancelled(task_id: int) -> bool:
    with _lock:
        return task_id in _cancel_flags


def _clear_cancel(task_id: int) -> None:
    with _lock:
        _cancel_flags.discard(task_id)


def _claim_task_slot(task_id: int) -> bool:
    """抢占任务执行槽：同一任务同一时刻只允许一个后台线程。

    check-then-act 必须原子：否则「重试」与「旧线程收尾」交错时，
    两边都判断为无人执行，导致同一批图重复生成或新提交的子项无人轮询。
    """
    with _lock:
        if task_id in _running:
            return False
        _running.add(task_id)
        return True


def _release_task_slot(task_id: int) -> None:
    """释放任务执行槽，并启动该用户队列里的下一单（如果有）。

    用户级排队：同一用户同时只跑一单，其余任务保持「排队中」等待。
    """
    advance_user: Optional[int] = None
    with _lock:
        _running.discard(task_id)
        owner = _task_owner.pop(task_id, None)
        if owner is not None and _user_current.get(owner) == task_id:
            _user_current.pop(owner, None)
            advance_user = owner
    if advance_user is not None:
        _advance_user_queue(advance_user)


def _register_task_run(task_id: int, user_id: int, prep_kwargs: Dict[str, Any]) -> bool:
    """登记任务归属：返回 True=当前空闲可立即启动；False=已进入该用户队列。

    必须在插入 DB 记录之后调用；排队任务的参考图字节留在内存，等前序任务结束后启动。
    """
    with _lock:
        _task_owner[task_id] = user_id
        if _user_current.get(user_id) is None:
            _user_current[user_id] = task_id
            return True
        _pending_by_user.setdefault(user_id, []).append((task_id, prep_kwargs))
        return False


def _mark_task_resumed(task_id: int, user_id: int) -> None:
    """重启恢复的任务也要登记用户归属，避免新提交绕过队列与恢复任务并发"""
    with _lock:
        _task_owner[task_id] = user_id
        _user_current.setdefault(user_id, task_id)


def _queued_task_startable(task_id: int) -> bool:
    """排队中的任务是否仍可启动：被取消/删除的任务直接跳过"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        row = (
            db.query(AIImageTask.status, AIImageTask.deleted_at)
            .filter(AIImageTask.id == task_id)
            .first()
        )
    finally:
        db.close()
    return bool(row) and row[0] == "排队中" and row[1] is None


def _advance_user_queue(user_id: int) -> None:
    """启动该用户队列里的下一单（一次只启动一单，其余继续排队）"""
    while True:
        with _lock:
            if _user_current.get(user_id) is not None:
                return
            pending = _pending_by_user.get(user_id) or []
            if not pending:
                _pending_by_user.pop(user_id, None)
                return
            task_id, prep_kwargs = pending.pop(0)
            _user_current[user_id] = task_id
            _running.add(task_id)

        if not _queued_task_startable(task_id):
            with _lock:
                if _user_current.get(user_id) == task_id:
                    _user_current.pop(user_id, None)
                _running.discard(task_id)
                _task_owner.pop(task_id, None)
            continue

        try:
            threading.Thread(
                target=_prepare_and_run, args=(task_id,), kwargs=prep_kwargs,
                daemon=True, name=f"ai-image-task-{task_id}",
            ).start()
        except Exception:
            logger.exception(f"[AI生图] 队列任务 {task_id} 启动失败")
            with _lock:
                _running.discard(task_id)
                _user_current.pop(user_id, None)
                _task_owner.pop(task_id, None)
            continue
        logger.info(f"[AI生图] 用户 {user_id} 队列启动任务 {task_id}")
        return


# ComfyUI 串行队列：限制本服务同时轮询的任务数，与生视频互让
_slot_sem = threading.Semaphore(max(1, int(settings.IMAGE_MAX_CONCURRENT_TASKS)))


def _comfy_queue_depth() -> Optional[int]:
    """ComfyUI 当前队列深度（running + pending）；接口不可用时返回 None"""
    try:
        q = _api("GET", "/queue", timeout=10).json()
        return len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
    except Exception:
        return None


# 队列快照缓存：列表页 5s 轮询一次，10 条进行中任务不该触发 10 次 /queue 查询
_QUEUE_SNAPSHOT_TTL = 3.0
_queue_snapshot: Dict[str, Any] = {"at": 0.0, "data": None}
_queue_snapshot_lock = threading.Lock()


def _comfy_queue_state() -> Dict[str, Any]:
    """ComfyUI 队列快照：{ok, running, pending, positions{pid: 1 基排位}}"""
    with _queue_snapshot_lock:
        cached = _queue_snapshot.get("data")
        if cached is not None and time.time() - float(_queue_snapshot.get("at") or 0) < _QUEUE_SNAPSHOT_TTL:
            return cached
    data: Dict[str, Any] = {"ok": False, "running": 0, "pending": 0, "positions": {}}
    try:
        q = _api("GET", "/queue", timeout=4).json()
        pending = q.get("queue_pending") or []
        positions: Dict[str, int] = {}
        for i, entry in enumerate(pending):
            pid = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else None
            if pid:
                positions[str(pid)] = i + 1
        data = {
            "ok": True,
            "running": len(q.get("queue_running") or []),
            "pending": len(pending),
            "positions": positions,
        }
    except Exception:
        pass  # ComfyUI 忙时查不到就退化为「无排位提示」，不影响任务本身
    with _queue_snapshot_lock:
        _queue_snapshot["at"] = time.time()
        _queue_snapshot["data"] = data
    return data


def _queue_info(task) -> Optional[Dict[str, Any]]:
    """进行中任务的队列排位（前端展示「前面还有 N 个任务」）。

    relay 任务不占 ComfyUI 队列；本地任务分两段等待：①同账号用户级排队
    ②ComfyUI 队列（running + pending）。两段都可能把「排队中」拖到几十分钟，
    不给提示用户只会以为是卡死。
    """
    if task is None or task.status not in ("排队中", "生成中"):
        return None
    if (getattr(task, "provider", None) or "local") == "relay":
        return {"stage": "relay", "ahead": None, "hint": ""}

    user_ahead = 0
    with _lock:
        queued_ids = [tid for tid, _ in (_pending_by_user.get(task.user_id) or [])]
        if task.id in queued_ids:
            # 队列里排在本单之前的单数 + 正在执行的那一单（若存在且不是自己）
            user_ahead = queued_ids.index(task.id)
            current = _user_current.get(task.user_id)
            if current is not None and current != task.id:
                user_ahead += 1
        elif _user_current.get(task.user_id) not in (None, task.id):
            user_ahead = 1

    state = _comfy_queue_state()
    position: Optional[int] = None
    if state.get("ok"):
        for pid in _read_json_list(task.comfy_prompt_ids):
            pos = (state.get("positions") or {}).get(str(pid))
            if pos:
                position = pos if position is None else min(position, pos)

    ahead = position - 1 if position else 0
    if user_ahead:
        hint = f"同账号前面还有 {user_ahead} 单在跑"
        if ahead:
            hint += f"；ComfyUI 队列前面还有 {ahead} 个任务"
    elif ahead:
        hint = f"ComfyUI 队列前面还有 {ahead} 个任务"
    elif state.get("ok") and task.status == "排队中":
        hint = "已进入 ComfyUI 队列，等待开始"
    else:
        hint = ""

    return {
        "stage": "comfy",
        "ahead": ahead,
        "position": position,
        "user_ahead": user_ahead,
        "comfy_running": state.get("running"),
        "comfy_pending": state.get("pending"),
        "hint": hint,
    }


def _wait_comfy_queue_slot(task_id: int) -> None:
    """提交工作流前的背压：队列过深时先等位。

    原实现只把「轮询」纳入信号量，提交阶段在闸门外；多用户并发时 12 张 × N
    会瞬间灌满 ComfyUI 队列，挤占生视频/超分。等待有上限，超时仍提交，
    避免队列长期高位时整单无限阻塞。
    """
    limit = max(1, int(settings.IMAGE_MAX_QUEUE_DEPTH))
    deadline = time.time() + max(0, int(settings.IMAGE_QUEUE_WAIT_SECONDS))
    waited = False
    while time.time() < deadline:
        depth = _comfy_queue_depth()
        if depth is None or depth < limit:
            if waited:
                logger.info(f"[AI生图] 任务 {task_id} 队列已回落（深度 {depth}），继续提交")
            return
        waited = True
        time.sleep(3)
    logger.warning(f"[AI生图] 任务 {task_id} 等待队列回落超时，继续提交以避免整单卡死")


_MODELS_LOADED = False


def _ensure_models_loaded():
    """独立解释器（脚本/子进程）安全网：pkgutil 动态导入全部 models。

    项目已知坑：models/__init__.py 未导出全部模型类（缺 ProductSelection 等），
    Tenant 用字符串 relationship("ProductSelection")，若独立解释器只间接注册了
    少数类，首次 ORM 操作触发 mapper 全量配置即抛 InvalidRequestError。
    uvicorn 主进程经 database.init_db() 已导入全部模型，此处为幂等 no-op。
    """
    global _MODELS_LOADED
    if _MODELS_LOADED:
        return
    import pkgutil
    import models  # noqa: F401
    for m in pkgutil.iter_modules(models.__path__):
        __import__(f"models.{m.name}")
    _MODELS_LOADED = True


class AIImageError(Exception):
    """AI 生图异常"""
    pass


# 直连 ComfyUI：trust_env=False 绕过本地 HTTP 代理（实测代理隧道把 2MB 下载从 ~5s 拖到 11s+，
# 且偶发 502），同时复用 TCP 连接省掉每次 TLS 握手。每线程一个 Session（Session 非线程安全）。
_local = threading.local()


def _http_session() -> requests.Session:
    sess = getattr(_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.trust_env = False
        _local.session = sess
    return sess


def _api(method: str, path: str, timeout: int = 60, **kwargs) -> requests.Response:
    """带 Basic Auth 的接口请求（复用连接、不走系统代理）"""
    url = settings.H3_BASE_URL.rstrip("/") + path
    resp = _http_session().request(
        method,
        url,
        auth=(settings.H3_AUTH_USER, settings.H3_AUTH_PASSWORD),
        timeout=timeout,
        **kwargs,
    )
    if resp.status_code == 401:
        raise AIImageError("ComfyUI 服务认证失败，请检查 H3 账号密码")
    resp.raise_for_status()
    return resp


def _supported_ratio_options(force: bool = False) -> Optional[List[str]]:
    """探测 ComfyUI ResolutionSelector 当前支持的 aspect_ratio 选项。

    返回选项字符串列表；服务不可达/节点缺失时返回 None（调用方自行决定放行还是报错）。
    结果缓存 TTL 10 分钟：节点装/卸不需要重启服务即可逐步生效。
    """
    now = time.time()
    with _ratio_lock:
        cached = _RATIO_CACHE.get("options")
        if not force and cached and now - float(_RATIO_CACHE.get("at") or 0) < _RATIO_CACHE_TTL:
            return list(cached)

    if not (settings.H3_BASE_URL or "").strip():
        return None
    try:
        info = _api("GET", "/object_info/ResolutionSelector", timeout=15).json()
        spec = (
            ((info.get("ResolutionSelector") or {}).get("input") or {})
            .get("required", {})
            .get("aspect_ratio")
        )
        options: Optional[List[str]] = None
        if isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict):
            options = spec[1].get("options")
        if not options and isinstance(spec, list) and spec and isinstance(spec[0], list):
            options = spec[0]
        options = [str(o) for o in (options or [])]
        if not options:
            logger.warning("[AI生图] ResolutionSelector 未返回 aspect_ratio 选项")
            return None
        with _ratio_lock:
            _RATIO_CACHE["at"] = now
            _RATIO_CACHE["options"] = options
        return options
    except Exception as e:
        logger.warning(f"[AI生图] 探测比例选项失败（本次不校验）: {e}")
        return None


def resolve_ratio_label(aspect_ratio: str) -> str:
    """前端比例 key → ComfyUI 选项字符串；服务端不支持时**报错而不是降级**。"""
    label = ASPECT_RATIO_MAP.get(aspect_ratio)
    if not label:
        raise AIImageError(
            f"不支持的图片比例: {aspect_ratio}（可选：{'、'.join(ASPECT_RATIO_MAP)}）"
        )
    supported = _supported_ratio_options()
    if supported is not None and label not in supported:
        raise AIImageError(
            f"当前生图服务不支持 {aspect_ratio} 比例，请在比例卡片中改选"
            f"（服务端可用：{'、'.join(supported[:6])}…）"
        )
    return label


def _safe_text(value: Optional[str], limit: int) -> Optional[str]:
    """按数据库列宽截断，避免 MySQL 1406 Data too long 直接抛 500。

    前端已限制输入长度，但接口可被绕过；这里按列宽兜底而非报错（截断不改变语义）。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


# ComfyUI 原始报错 → 运营可读归因（按顺序命中，第一条优先）
_COMFY_ERROR_HINTS: List[Tuple[Tuple[str, ...], str]] = [
    (("out of memory", "oom", "allocation on device"), "显存不足：请减少单任务张数或降低生成像素后重试"),
    (("failed to load", "not found", "no such file", "checkpoint"), "模型缺失：生图服务端模型未加载，请联系管理员"),
    (("cannot identify image", "invalid image", "truncated", "broken data"), "参考图无法解析：请更换图片后重试"),
    (("timed out", "timeout", "connection reset", "connection refused"), "生图服务连接异常：请稍后重试"),
    (("invalid prompt", "validation", "required input"), "工作流参数校验失败：当前比例/张数组合不受支持"),
    (("cuda", "device"), "生图服务 GPU 异常：请联系管理员检查服务状态"),
]


def humanize_comfy_error(raw: Optional[str]) -> str:
    """把 ComfyUI 的英文报错归因成一句中文处置建议；无法归因返回空串。"""
    text = (raw or "").lower()
    if not text:
        return ""
    for keys, hint in _COMFY_ERROR_HINTS:
        if any(k in text for k in keys):
            return hint
    return ""


def _to_jpeg(content: bytes, filename: str, quality: int = 95) -> Tuple[bytes, str]:
    """回传前把 PNG 转 JPEG。

    外网带宽是瓶颈（实测 2.09MB PNG 下载 5.5s、回传 TOS 约 6.8s）；
    1248x1248 转 q=95 后约 0.29MB（14%），单张可省约 6s，多张任务线性放大。
    转换失败时回退原图，不阻塞任务。
    """
    try:
        from io import BytesIO
        from PIL import Image as PILImage

        img = PILImage.open(BytesIO(content))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        return buf.getvalue(), filename.rsplit(".", 1)[0] + ".jpg"
    except Exception as e:
        logger.warning(f"[AI生图] 转 JPEG 失败，回传原图: {e}")
        return content, filename


def _normalize_for_comfy(content: bytes) -> bytes:
    """上传前统一压成 JPEG（长边 ≤2048、透明底合成白底），与生视频同策略"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        img.load()
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > 2048:
            scale = 2048 / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue()
    except Exception:
        return content


# 主参考图长边下限：低于此值模型开始编造细节（实测 175px 缩略图把皇冠认成海滩套装）
_MIN_MAIN_IMAGE_SIDE = 512


def _fetch_url_bytes(url: str, limit: int = 10 * 1024 * 1024) -> bytes:
    """下载素材图（版式参考图存 TOS，只存了 URL，生成时按需取回）

    直连：本机/服务器若配了 http_proxy，走代理取自家 CDN 会被隧道抖动拖到超时
    （与 ZIP 打包同款问题，见 build_task_zip 的 trust_env=False）。
    """
    if not url:
        raise AIImageError("版式图地址为空")
    try:
        sess = requests.Session()
        sess.trust_env = False
        resp = sess.get(url, timeout=20, stream=True)
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise AIImageError("版式图超过 10MB")
        if not buf:
            raise AIImageError("版式图为空")
        return bytes(buf)
    except AIImageError:
        raise
    except Exception as e:
        raise AIImageError(f"版式图下载失败: {e}")


def _image_area(content: bytes) -> int:
    """参考图像素数；解码失败返回 0（按最不清晰处理）"""
    try:
        from io import BytesIO
        from PIL import Image as PILImage

        with PILImage.open(BytesIO(content)) as im:
            return im.size[0] * im.size[1]
    except Exception:
        return 0


def _order_images_by_clarity(images: List[Tuple[str, bytes]]) -> List[Tuple[str, bytes]]:
    """把像素最大的参考图排到第一位（同尺寸保持原顺序）。

    主参考图决定 local 的 LoadImage(475)/PE「图片」与 relay 的看图设计。
    产品库首图常是亚马逊 `_SL75_` 缩略图（175x175 / 2KB），实测会让模型把
    「金色月桂皇冠 4 件套」认成「海滩度假套装」——商品信息、件数清单、提示词全错。
    排序后主图自动取 1080x1080 那张。
    """
    if len(images) < 2:
        return list(images)
    indexed = list(enumerate(images))
    indexed.sort(key=lambda x: (-_image_area(x[1][1]), x[0]))
    return [item for _i, item in indexed]


def _upload_image(idx: int, content: bytes) -> str:
    """上传参考图到 ComfyUI，返回服务器端文件名；失败自动重试一次"""
    name = f"aiimage-{int(time.time() * 1000)}-{idx}.jpg"
    content = _normalize_for_comfy(content)

    last_err: Optional[str] = None
    for attempt in (1, 2):
        try:
            resp = _api(
                "POST",
                "/upload/image",
                files={"image": (name, content)},
                data={"overwrite": "true"},
            )
            return resp.json()["name"]
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[AI生图] 参考图上传第 {attempt} 次失败: {last_err}")
            threading.Event().wait(2)

    raise AIImageError(f"参考图上传失败（已重试）: {last_err}")


def _load_prompt_templates() -> Dict[str, Dict[str, str]]:
    """读取生成类型提示词模板（运营可直接调优 JSON，无需改代码）

    `IMAGE_LOCAL_HARDENED_PROMPT=False` 时回退 v3 轻量版
    （v3 是「画面语言完全交给 PE」的老假设，保留以便一键撤回）。
    """
    path = settings.IMAGE_PROMPT_TEMPLATES_PATH
    if not bool(getattr(settings, "IMAGE_LOCAL_HARDENED_PROMPT", True)):
        legacy = path.replace(".json", "_v3.json")
        if os.path.exists(legacy):
            path = legacy
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise AIImageError(f"读取提示词模板失败: {e}")


def _multi_shot_types(gen_plan) -> set:
    """哪些类型在同一任务里要出多张（只有它们需要“彼此不同”的提示）"""
    counts: Dict[str, int] = {}
    for row in gen_plan:
        t = row[0]
        counts[t] = counts.get(t, 0) + 1
    return {t for t, n in counts.items() if n > 1}


# 画面文字条款（A+ 模板的 {text_clause} 占位）。
# 2026-10-28 PE 探针实测：光写「禁止文字」PE 会照搬且不诱发写字；但只剩禁止、没有正向版面目标时，
# 它会退化成「纯白背景」来满足约束——所以条款必须配正向版面红线（见 aplus_advanced 模板）。
_NO_TEXT_CLAUSE = (
    "禁止：画面内出现任何可读的文字、字母、数字、价签、水印、社媒标识或 logo；"
    "商品自带的字母与图案不算文字，必须逐字保留。"
)
_TEXT_OK_CLAUSE = (
    "画面可出现排版文字：只允许品牌文案，语言按目标市场，不出现价签、水印、社媒标识或 logo。"
)
# 品牌A+横幅文字条款（{text_clause} 占位）：品牌名由 571 直接给出，这里只声明「不得自造文字」。
# （2026-11-02 A/B 实测后用户裁定：572 后置合约已去除，品牌名进 571 即可。）
_BANNER_TEXT_CLAUSE = (
    "画面中只允许出现后置指令指定的品牌名（即「品牌名：」一行给出的名称）；"
    "不要自行编写任何品牌名、字母、数字、标语，也不要绘制文字占位或字形图案；"
    "除该品牌名外，画面禁止任何文字、字母、数字、价签、水印、社媒标识或 logo。"
)

# 品牌名（英文）：字母/数字/空格与常见商标符号，2–20 字符，保留大小写。
# 只用于「品牌A+横幅」的必填校验，其他 A+ 类型不读取也不校验。
_BRAND_NAME_RE = re.compile(r"^[A-Za-z0-9&'\-., ]+$")
_BRAND_NAME_MIN = 2
_BRAND_NAME_MAX = 20


def _normalize_brand_name(value: Optional[str]) -> str:
    """规范化并校验英文品牌名；空值返回 ""，非法值抛 AIImageError。

    只做空白归一（多空格压成一个、去首尾），**不改大小写**——用户指定的大小写
    会原样写进 571 提示词，PE 与模型都不得改变文字内容（字形可艺术化设计）。
    """
    name = " ".join(str(value or "").split())
    if not name:
        return ""
    if not (_BRAND_NAME_MIN <= len(name) <= _BRAND_NAME_MAX):
        raise AIImageError(
            f"品牌名长度需为 {_BRAND_NAME_MIN}–{_BRAND_NAME_MAX} 个字符（仅英文品牌名）"
        )
    if not _BRAND_NAME_RE.match(name) or not re.search(r"[A-Za-z]", name):
        raise AIImageError("品牌名仅支持英文字母、数字与 & ' - . , 空格（区分大小写）")
    return name

# 无文字模式下，版式结构描述里的「标题/文案/文字区」等词会直接诱发模型画字形
# （2026-10-29 实测：正向「留白」指令也压不住，出乱码标题）——
# 生成时把这些词统一改写为「留白」，数据库里的原始描述不动（运营仍按真实版式阅读）。
# 2026-10-29 用户裁定：不要「留白块/留白条」这类名词 —— 它们暗示一个可见的矩形块，
# 实测顶部留白会被画成一块浅色空矩形。
# 做法：位置词统一缩成「留白」，含义由 aplus_layout 模板里的 {layout_text_rule} 定义一次
# （「与背景连续的整片留白…不留任何矩形色块」）。**不要在这里再追加一份定义**：
# 2026-10-29 实测结构段末尾 + 文字规则两处各写一遍，同一句话重复出现，只增加噪声。
# 也不要在每个位置后面拖长定语（「一句短与背景连续的整片留白的位置」这类句子已不通）。
_NO_TEXT_STRUCTURE_REPLACEMENTS: List[Tuple[str, str]] = [
    # 长复合词必须排在短词前面：`标题文字区` 先命中会把「横向主标题文字区」
    # 变成「横向主留白区」（残留「主」字），所以带修饰语的写法先吃掉。
    ("横向主标题文字区", "横向留白区"),
    ("主标题文字区", "留白区"),
    ("标题文字区", "留白区"),
    ("文字说明区", "留白区"),
    ("文字信息区", "留白区"),
    ("文字与信息条区", "留白区"),
    ("文字色块区", "留白区"),
    ("文字承载区", "留白区"),
    ("图文组合区", "图区"),
    ("文字区", "留白区"),
    ("文字说明", "留白"),
    ("文字条", "留白"),
    ("文字标签", "留白"),
    # 「叠加文字」「每格底部叠加文字」——单独的「文字」也要吃掉，
    # 否则模型会找地方写字（正向禁令压不住，见 2026-10-29 乱码实测）
    ("叠加文字", "留白"),
    ("文字", "留白"),
    ("品牌标题区", "品牌留白区"),
    ("大标题", "留白"),
    ("主标题", "留白"),
    ("标题位", "留白位置"),
    ("标题", "留白"),
    ("副文案", "留白"),
    ("卖点短句", "留白"),
    ("卖点条", "留白区"),
    ("卖点位", "留白位置"),
    ("卖点", "留白"),
    # 「一句短文案」→「一句短留白」不通：整段吃掉，避免留下残句
    ("一句短文案", "一处留白"),
    ("短文案", "留白"),
    ("文案", "留白"),
    # 结构描述里本来就有「留白块/留白条」的（模板撰写时写的），同样要消掉矩形暗示
    ("留白块", "留白"),
    ("留白条", "留白"),
]

# 替换的副作用收尾：逐条替换会拼出「预留留白与一处留白的位置」「预留留白位置」这类
# 重复短语（规则互相叠），必须在主替换之后单独收一次，否则提示词读起来是病句。
_NO_TEXT_STRUCTURE_CLEANUPS: List[Tuple[str, str]] = [
    ("预留留白与一处留白的位置", "预留一处留白的位置"),
    ("预留留白与留白的位置", "预留一处留白的位置"),
    ("预留留白位置", "预留一处留白的位置"),
    ("留白位置位置", "留白位置"),
    ("留白留白", "留白"),
    # 装饰词摘除后相邻两个留白会并成「留白区留白区」「留白区留白」——收敛成一个
    ("留白区留白区", "留白区"),
    ("留白区留白", "留白区"),
    ("留白留白区", "留白区"),
]

# 摘掉装饰词后残留的标点/连接词。必须放在整条链路最后，因为前面的替换
# 会不断制造新的「，、」「，，」「以形成」；放中间会被后续替换再破坏一次。
_STRUCTURE_PUNCT_CLEANUPS: List[Tuple[str, str]] = [
    ("、、", "、"),
    ("，、", "，"),
    ("、，", "，"),
    ("，，", "，"),
    ("；，", "；"),
    ("，；", "；"),
    ("。，", "。"),
    ("，。", "。"),
    ("；。", "。"),
    ("，以形成", "，形成"),
    ("以形成", "形成"),
    ("，以上下", "，上下"),
    ("，以弧形与上下区域", ""),
    ("，以", "，"),
    ("以弧形", ""),
]

# 版式描述里「分区怎么画」的部分必须整段摘掉——它描述的是视觉分隔手段，
# 模型会照着画出来（2026-10-29 实测 db:19「以硬边分隔形成多格拼接布局」→
# 底部 25% 被画成四列半透明矩形 + 硬边竖线，压在商品裙摆上）。
# 无文字模式只需要「哪个区占多少、放什么」；分隔线/边框/卡片由设计师后期做。
# 注意：只摘词、不整句删——整句删会把同一句里的占比一起丢掉（实测 #122 丢了商品图描述）。
_LAYOUT_DECOR_TOKENS: Tuple[str, ...] = (
    "硬边", "分隔", "分割", "分栏", "拼接", "白线", "边框", "圆角",
    "卡片", "色块", "色条", "信息条", "斜切", "边界", "竖线", "细线",
    "横线", "圆角框", "外框",
)

# 装饰性名词本身 = 一个可见块，收敛成留白（长词在前，避免摘一半留一半）
_LAYOUT_DECOR_NOUNS: List[Tuple[str, str]] = [
    ("半透明深色圆角信息条", "留白区"),
    ("半透明圆角信息条", "留白区"),
    ("半透明深色信息条", "留白区"),
    ("浅色硬边圆角信息面板", "留白区"),
    ("硬边圆角信息面板", "留白区"),
    ("半透明圆角信息色块", "留白区"),
    ("渐变色块条", "留白区"),
    ("弧形色块条", "留白区"),
    ("信息面板", "留白区"),
    ("信息色块", "留白区"),
    ("信息卡片", "留白区"),
    ("信息框", "留白区"),
    ("信息卡", "留白区"),
    ("色块条", "留白区"),
    ("色条", "留白区"),
    ("信息条", "留白区"),
    ("图片卡片", "商品图"),
    ("卡片区", "图区"),
    ("面板", "留白区"),
    ("色块区", "留白区"),
    ("色块", "留白区"),
]

# 无文字模式下「四列等宽留白区」= 四个空文字位，不出字就不需要分列，
# 留着「四列」只会让模型画四条竖线。商品图区的列数不动（那里确实要多图并排）。
_BLANK_COLS_RE = re.compile(
    r"[一二三四五六七八九十两\d]+\s*(?:列|格|栏)\s*(?:等宽|并列|等分)?\s*(?:的)?\s*留白区"
)

# 摘完装饰词后剩下的空壳分句：「三块区域均为」「右侧约20%有」这类没有实体内容，
# 留着只会让模型去猜「这里该画什么」。
_DANGLING_TAIL_RE = re.compile(r"(?:为|有|以|均|按|的|并|且|与|及|用)$")


def _drop_empty_clauses(text: str) -> str:
    """删掉摘掉装饰词后变成空壳的分句（无实体内容、只剩连接词）。"""
    parts = re.split(r"([，。；,;])", text)
    out: List[str] = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        body = seg.strip()
        if not body:
            out.append(seg + sep)
            continue
        if _DANGLING_TAIL_RE.search(body) and "%" not in body:
            continue
        # 去掉空白/数字/百分比/「约」后不足 3 字 = 没有实体名词
        if len(re.sub(r"[\s\d%约．.]", "", body)) < 3:
            continue
        out.append(seg + sep)
    return "".join(out).strip("，。；,; ")


def _sanitize_structure_no_text(structure: str) -> str:
    """无文字模式：把版式描述清洗成「只有分区位置与占比」的纯结构说明。

    四步，顺序不能换：
    1. 文字位词 → 留白（「标题区」会直接诱发模型画字形，正向禁令压不住）
    2. 装饰性名词 → 留白区（信息条/色块条/卡片 = 可见的块）
    3. 装饰性形容词/动词摘除（硬边/分隔/圆角/拼接 = 怎么画线）
    4. 删掉摘完后的空壳分句

    「留白」的定义不在这里追加 —— 定义在 aplus_layout 模板的 {layout_text_rule}
    里给一次（「与背景连续的整片留白…不留任何矩形色块」）。两处都写会重复，稀释指令。
    """
    text = structure or ""
    for old, new in _NO_TEXT_STRUCTURE_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in _LAYOUT_DECOR_NOUNS:
        text = text.replace(old, new)
    for token in _LAYOUT_DECOR_TOKENS:
        text = text.replace(token, "")
    text = _BLANK_COLS_RE.sub("留白区", text)
    text = _drop_empty_clauses(text)
    for old, new in _NO_TEXT_STRUCTURE_CLEANUPS:
        text = text.replace(old, new)
    # 摘词后的标点/连接词收尾必须放最后：前面的替换会不断制造新的「，、」「，，」
    for old, new in _STRUCTURE_PUNCT_CLEANUPS:
        text = text.replace(old, new)
    return text.strip("，、；; ")


def _build_prompt(gen_type: str, product_info: str, extra_prompt: str,
                  brand_genes: str = "",
                  multi_shot: bool = False,
                  no_text: bool = False,
                  layout_structure: str = "",
                  layout_with_image: bool = False,
                  fact_card: Optional[Dict[str, Any]] = None,
                  brand_name: str = "") -> str:
    """拼接原始提示词（brief 式）。

    PE 的「图片」输入接了参考图，它能看到商品形态与组合关系，会自行设计场景/机位/光线/姿态。
    原始提示词只负责：要什么类型的图、图片上看不出来的商品事实、必须保留的不变量。
    写死焦段/色温/构图占比会与 PE 的判断冲突（实测导致「蛋糕装饰」被拆成桌面摆件）。
    2026-10-27 用户裁定：连「构图/姿态/光线」也不要指定，只保留“多张之间必须有差异”。

    no_text=True 时（A+ 默认）：① 从品牌基因里剔除「画面文字语言 X」——2026-10-28 探针实测，
    该字段与「预留排版空间」同时出现会把 PE 整段输出切成英文（1444 字并自造 dotted frame）；
    ② 模板有 {text_clause} 则替换成无字条款，没有则维持原来的追加句。

    layout_structure 非空（A+ 绑定了带描述的版式）时改用 aplus_layout 模板：
    构图完全交给版式分区，原模板里「一侧留三分之一空域」「不得出现分栏」等自由构图条款
    会与「三格分栏 / 上图下文条 / 信息条」类版式直接矛盾，必须整体替换而不是叠加。

    2026-10-29 实测（水母横幅 × 3 版式）：把版式成稿图送进 PE，PE 会把版式里的
    商品与文字当成要画的内容——商品被换成耳环/T 恤、无文字模式下照搬英文大标题。
    因此**有结构文字的版式不再送图**（纯文字分区 + 商品图），layout_with_image 仅用于
    控制正文里「第二张参考图」句子的有无。
    """
    templates = _load_prompt_templates()
    layout_structure = (layout_structure or "").strip()
    if gen_type == "aplus_banner":
        # 品牌横幅不使用版式模板：独立模板 + 后置品牌名合约
        tpl = templates.get("aplus_banner", {}).get("template")
        if not tpl:
            raise AIImageError("缺少 aplus_banner 提示词模板（prompts/image_prompt_templates.json）")
    elif gen_type.startswith("aplus") and layout_structure:
        if no_text:
            layout_structure = _sanitize_structure_no_text(layout_structure)
        tpl = templates.get("aplus_layout", {}).get("template")
        if not tpl:
            raise AIImageError("缺少 aplus_layout 提示词模板（prompts/image_prompt_templates.json）")
        tpl = tpl.replace("{layout_structure}", layout_structure)
        tpl = tpl.replace("{layout_ref_note}", (
            "第二张参考图是版式成稿样例：只借用它的分区与留白，"
            "其中出现的商品、文案、配色与 logo 一律不得带入画面。" if layout_with_image else ""
        ))
        # 版式里的「文字区/大标题」描述会诱发模型画字，无文字的禁令压不住
        # （2026-10-29 实测：方形-32 出乱码标题、竖版-9 出整段英文卖点）——
        # 必须给正向指令：文字区 = 与背景连续的整片留白，而不是「不要写字」。
        # 「纯色色块填充」的旧措辞会被画成一块可见的浅色矩形（2026-10-29 用户裁定改掉）：
        # 留白必须等于背景本身，不能是贴在背景上的一块东西。
        tpl = tpl.replace("{layout_text_rule}", (
            "全部做成与背景连续的整片留白：该区域的背景自然延伸过去，不留任何矩形色块、"
            "边框、卡片、圆角或阴影分界，也不绘制任何字形、字母、数字、标语或图形化文字占位"
            "（文字由设计师后期排版）。"
            if no_text else
            "排布品牌文案，语言与目标市场一致，字号层级分明。"
        ))
    else:
        tpl = templates.get(gen_type, {}).get("template")
    if not tpl:
        raise AIImageError(f"生成类型 {gen_type} 缺少提示词模板")

    genes = brand_genes or ""
    if no_text and genes:
        genes = "；".join(p for p in genes.split("；") if "画面文字语言" not in p)

    info = (product_info or "").strip() or "（未提供，请以参考图可见特征为准）"
    structured_intro = "商品信息（运营提供，优先采用；与参考图核对后写入事实卡）："
    if fact_card:
        from services.aplus_fact_card import render_fact_card
        info = render_fact_card(fact_card)
        structured_intro = (
            "以下为运营确认的结构化商品事实卡（唯一事实来源，不得改写字段；"
            "quantity=待确认 的项保持待确认，不得猜测）："
        )
    extra = (extra_prompt or "").strip()
    has_clause_slot = "{text_clause}" in tpl
    out = tpl.replace("{fact_card_intro}", structured_intro)
    out = out.replace("{product_info}", info)
    out = out.replace("{extra_prompt}", f"\n补充要求：{extra}" if extra else "")
    if has_clause_slot:
        # 品牌横幅：品牌名由 572 后置合约注入，571 只负责「不要自造文字」，
        # 不能复用通用无字条款（它会把品牌名也一并禁掉）。
        clause = _BANNER_TEXT_CLAUSE if gen_type == "aplus_banner" else (
            _NO_TEXT_CLAUSE if no_text else _TEXT_OK_CLAUSE
        )
        out = out.replace("{text_clause}", clause)
    if genes:
        out += f"\n品牌与区域：{genes}"
    # 品牌横幅：真实品牌名必须同时进 571。2026-11-02 任务 #108 实测：只靠 572 后置合约时，
    # PE 会在增强输出里按参考图调性自造品牌名（"FLEUR DE ROSE"），模型跟从 PE 丢弃合约。
    # 让 PE 一开始就知道真名，它才会把该名字写进自己的输出。
    if gen_type == "aplus_banner" and brand_name:
        out += (
            f"\n品牌名：{brand_name}（画面唯一要展示的品牌名，逐字使用、保持大小写，"
            "不得设计、猜测或另起任何其他品牌名）"
        )
    if no_text and not has_clause_slot:
        out += "\n无文字模式：画面中不要出现任何文字、标语、水印或标识。"
    # 2026-10-28 取消「多张必须不同」的追加句：PE 是确定性函数（同输入两次 md5 相同），
    # 该句进不了 PE 输出，只增加噪声；多张差异必须靠输入侧锚点（如 scene_person 的 PERSON_VARIATIONS）。
    # multi_shot 形参保留以兼容调用点。
    return out


_SPEED_CHAIN_CHECKED = False


def _warn_if_speed_chain_broken(wf: Dict[str, Any]) -> None:
    """加速链完整性自检（只告警一次，不阻断生成）。

    正式调用流的模型链：476 UNETLoader → 649 TESpeedQwenImage21 → 483 QwenImage21Cache → 481 KSampler。
    若运营替换工作流后丢了 649，任务仍能出图但会明显变慢且无人察觉 —— 实测关闭该节点
    单张 1:1@1MP 从 42.2s 增至 57.3s（-26%），故此处兜底告警。
    """
    global _SPEED_CHAIN_CHECKED
    if _SPEED_CHAIN_CHECKED:
        return
    _SPEED_CHAIN_CHECKED = True
    ok = (
        isinstance(wf.get("649"), dict)
        and ((wf.get("483") or {}).get("inputs") or {}).get("model") == ["649", 0]
        and ((wf.get("481") or {}).get("inputs") or {}).get("model") == ["483", 0]
    )
    if not ok:
        logger.warning(
            "[AI生图] 工作流加速链不完整（期望 476→649 TESpeed→483 Cache→481），"
            "生图会明显变慢；请检查 workflow 是否误删加速节点"
        )


def _build_workflow(
    *,
    prompt: str,
    aspect_ratio: str,
    seed: int,
    image_names: List[str],
    filename_prefix: str,
    megapixels: float = DEFAULT_MEGAPIXELS,
    workflow_path: Optional[str] = None,
    layout_contract: Optional[str] = None,
    encode_images: bool = True,
) -> Dict[str, Any]:
    """读取工作流模板并填入本次生成参数（switch=true 走 EmptyLatentImage 生图路径）

    encode_images=False（品牌A+横幅）：参考图只进 PE（提取风格），
    从 497 图像条件中全部摘除，保证画面里不会画出商品。

    多参考图：模板只接了 1 个 LoadImage(475) → 532 → 497.images.image_1 / 565.图片。
    这里按张数动态注入 LoadImage + ImageScaleToTotalPixels 节点，并挂到
    497 的 AUTOGROW 通道 images.image_N 与 PE 的 图片N（N=2..8）。

    workflow_path：A+ 实验通道（direct/append）使用专用工作流文件；
    direct 变体没有 PE 节点（565/569 已移除），注入时自动跳过 PE 相关接线。
    layout_contract：append 变体的版式契约文本，写入 572 节点（PE 之后拼接，
    PE 改写不到）。给了契约但工作流里没有 572 → 直接报错，不静默丢弃。
    """
    if not image_names:
        raise AIImageError("参考图为空")
    if len(image_names) > MAX_REF_IMAGES:
        raise AIImageError(f"最多 {MAX_REF_IMAGES} 张参考图")

    path = workflow_path or settings.IMAGE_WORKFLOW_PATH
    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        raise AIImageError(f"读取生图工作流模板失败({os.path.basename(path)}): {e}")

    # A+ 专用工作流的必需节点校验：缺节点立即报错，绝不静默降级回旧链路
    if workflow_path:
        missing = [
            n for n in (NODE_PROMPT, NODE_REF_IMAGE, NODE_RESOLUTION,
                        NODE_SEED, NODE_SAVE, NODE_ENCODE)
            if n not in wf
        ]
        if missing:
            raise AIImageError(
                f"A+ 工作流 {os.path.basename(path)} 缺少必需节点: {missing}"
            )
        if layout_contract and NODE_CONTRACT not in wf:
            raise AIImageError(
                f"A+ 工作流 {os.path.basename(path)} 缺少版式契约节点 {NODE_CONTRACT}"
            )

    wf[NODE_PROMPT]["inputs"]["value"] = prompt
    wf[NODE_REF_IMAGE]["inputs"]["image"] = image_names[0]
    # 比例：服务端不支持时抛错（不静默降级 1:1）
    wf[NODE_RESOLUTION]["inputs"]["aspect_ratio"] = resolve_ratio_label(aspect_ratio)
    wf[NODE_RESOLUTION]["inputs"]["megapixels"] = float(megapixels)
    wf[NODE_SEED]["inputs"]["seed"] = int(seed)
    wf[NODE_SAVE]["inputs"]["filename_prefix"] = filename_prefix
    # UNET/CLIP/VAE/KSampler/增强器等固定节点均以模板为准，代码不再覆盖

    # 品牌A+横幅：497 不接任何参考图（含模板里的主图 image_1），
    # 参考图只保留 PE 的「图片」通道用于风格提取。
    if not encode_images:
        enc_inputs = (wf.get(NODE_ENCODE) or {}).get("inputs") or {}
        for key in [k for k in enc_inputs if str(k).startswith("images.")]:
            enc_inputs.pop(key, None)

    # 额外参考图：每张一个 LoadImage + 尺度对齐节点（与主图 532 同像素，避免尺度不一致）
    for i, name in enumerate(image_names[1:], start=2):
        load_id, scale_id = str(9000 + i), str(9100 + i)
        wf[load_id] = {
            "inputs": {"image": name},
            "class_type": "LoadImage",
            "_meta": {"title": f"加载参考图{i}"},
        }
        wf[scale_id] = {
            "inputs": {
                "upscale_method": "lanczos",
                "megapixels": DEFAULT_MEGAPIXELS,
                "resolution_steps": 32,
                "image": [load_id, 0],
            },
            "class_type": "ImageScaleToTotalPixels",
            "_meta": {"title": f"参考图{i}尺度对齐"},
        }
        if encode_images:
            wf[NODE_ENCODE]["inputs"][f"images.image_{i}"] = [scale_id, 0]
        if NODE_PE in wf:  # direct 变体无 PE 节点
            wf[NODE_PE]["inputs"][f"图片{i}"] = [scale_id, 0]

    # 版式契约：append 变体专用（PE 之后拼接，PE 改写不到）。
    # 常规调用只有 append 路径会带契约（上面已校验 572 必在）；其它组合不注入只告警。
    if layout_contract:
        if NODE_CONTRACT in wf:
            wf[NODE_CONTRACT]["inputs"]["value"] = layout_contract
        else:
            logger.warning("当前工作流缺少版式契约节点 572，契约未注入")

    # PE 节点参数按配置注入（workflow 文件里的值可被 .env 覆盖，便于一键回退）
    pe_node = wf.get(NODE_PE)
    if isinstance(pe_node, dict):
        pe_inputs = pe_node.setdefault("inputs", {})
        pe_lang = (getattr(settings, "IMAGE_LOCAL_PE_LANGUAGE", "") or "").strip()
        if pe_lang:
            pe_inputs["输出语言"] = pe_lang
        pe_inputs["生成后自动卸载模型"] = bool(
            getattr(settings, "IMAGE_LOCAL_PE_UNLOAD", True)
        )

    _warn_if_speed_chain_broken(wf)
    return wf


def _extract_output_file(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 ComfyUI 任务结果中取出图片文件信息"""
    finfo = None
    for node_out in (item.get("outputs") or {}).values():
        for key in ("images", "gifs"):
            for f in node_out.get(key) or []:
                finfo = f
    return finfo


def _download_image(finfo: Dict[str, Any]) -> Tuple[bytes, str]:
    """下载生成图片，返回 (字节内容, 文件名)"""
    resp = _api("GET", "/view", timeout=300, params=finfo)
    return resp.content, finfo.get("filename") or "output.png"


def _comfy_gen_seconds(item: Dict[str, Any]) -> Optional[float]:
    """ComfyUI 实际执行时长（execution_start → execution_success），**不含排队时间**。

    用于回答「这张图到底跑了多久」：端到端含排队，队列被视频任务占用时会失真；
    history 里的时间戳才是模型真实耗时（冷启动会明显大于热态基线）。
    """
    msgs = ((item.get("status") or {}).get("messages") or [])
    t_start = t_end = None
    for m in msgs:
        if not isinstance(m, (list, tuple)) or len(m) < 2:
            continue
        ev, data = m[0], m[1] if isinstance(m[1], dict) else {}
        ts = data.get("timestamp")
        if not ts:
            continue
        if ev == "execution_start" and t_start is None:
            t_start = ts
        elif ev == "execution_success":
            t_end = ts
    if t_start and t_end and t_end >= t_start:
        return round((t_end - t_start) / 1000.0, 2)
    return None


def _update_task(task_id: int, **fields) -> None:
    """独立会话更新任务记录（后台线程中使用）"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        db.query(AIImageTask).filter(AIImageTask.id == task_id).update(fields)
        db.commit()
    except Exception as e:
        logger.error(f"[AI生图] 更新任务 {task_id} 状态失败: {e}")
        db.rollback()
    finally:
        db.close()


def _read_json_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


# result_images 是 MySQL Text（65535 字节）。子项带上 final_prompt / prompt_cn /
# enhanced_prompt 后，长商品信息（product_info 允许 5000 字符 ≈ 15KB）+ 多类型任务
# 会超限，写库抛 1406 被 _update_task 吞成一行日志 —— 表现为「图已生成但任务永远
# 停在生成中」，直到 30 分钟超时才失败。所有写库点统一走 _dump_items 裁剪。
_RESULT_JSON_BUDGET = 48000     # 留 ~17KB 余量给列宽与转义膨胀
_PROMPT_FIELD_LIMIT = 3000      # 单条提示词落库上限（中文 UTF-8 三字节，再长没意义）
_PROMPT_CN_LIMIT = 800
_INVENTORY_MAX_ITEMS = 10


def _clip_text(value: Optional[str], limit: int) -> Optional[str]:
    if not value or limit <= 0:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _dump_items(items: List[Dict[str, Any]]) -> str:
    """序列化 result_images：按总字节预算逐级收缩提示词。

    status/url/error 是业务字段，永不裁剪；提示词只用于详情展示，
    收缩到极短仍超预算时才整体丢弃（12 张 × 超长中文商品信息才会走到这一步）。
    """
    sources = [dict(it) for it in (items or [])]

    def render(limit: int, cn_limit: int, keep_inventory: bool) -> str:
        rows: List[Dict[str, Any]] = []
        for src in sources:
            row = dict(src)
            for key in ("final_prompt", "enhanced_prompt"):
                if key in row:
                    row[key] = _clip_text(row.get(key), limit)
            if "prompt_cn" in row:
                row["prompt_cn"] = _clip_text(row.get("prompt_cn"), cn_limit)
            inv = row.get("inventory")
            if isinstance(inv, list):
                row["inventory"] = inv[:_INVENTORY_MAX_ITEMS] if keep_inventory else None
            rows.append(row)
        return json.dumps(rows, ensure_ascii=False)

    raw = render(_PROMPT_FIELD_LIMIT, _PROMPT_CN_LIMIT, True)
    if len(raw.encode("utf-8")) <= _RESULT_JSON_BUDGET:
        return raw

    # 超预算：逐级缩短提示词（保留部分内容远好于整段丢弃）
    for limit in (1500, 800, 400, 200):
        raw = render(limit, min(_PROMPT_CN_LIMIT, limit), True)
        if len(raw.encode("utf-8")) <= _RESULT_JSON_BUDGET:
            logger.warning(
                f"[AI生图] result_images 超预算，提示词已收缩到 {limit} 字符"
                f"（{len(sources)} 个子项，{len(raw.encode('utf-8'))} 字节）"
            )
            return raw

    # 最后一档：丢提示词与清单，保住状态与图片地址
    raw = render(0, 0, False)
    logger.warning(
        f"[AI生图] result_images 极端超预算，已丢弃提示词"
        f"（{len(sources)} 个子项，{len(raw.encode('utf-8'))} 字节）"
    )
    return raw


def _aggregate_status(items: List[Dict[str, Any]]) -> str:
    """根据子项状态聚合任务级状态"""
    statuses = [it.get("status") for it in items]
    if not statuses:
        return "生成中"
    if all(s == "已完成" for s in statuses):
        return "已完成"
    if all(s == "失败" for s in statuses):
        return "失败"
    if all(s == "已取消" for s in statuses):
        return "已取消"
    # 仍有子项在排队/生成中 → 任务整体仍在进行
    if any(s in ("排队中", "生成中") for s in statuses):
        return "生成中"
    # 全部到达终态且有成功有失败（或含取消）
    return "部分完成"


def _finalize_status(task_id: int, agg: str) -> str:
    """中转站线程收尾时保住「已取消」终态。

    relay 取消是「先落终态、线程继续跑完 HTTP」：线程收尾若把 status 写成
    「部分完成」，前端（已停止轮询）刷新后会看到终态被翻回，以为取消失败。
    已全部完成（agg="已完成"）则如实显示完成 —— 事实比取消指令更准确。
    """
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        current = db.query(AIImageTask.status).filter(AIImageTask.id == task_id).scalar()
    except Exception:
        current = None
    finally:
        db.close()
    if current == "已取消" and agg != "已完成":
        return "已取消"
    return agg


def _pick_retry_targets(
    items: List[Dict[str, Any]], indices: Optional[List[int]], retryable: Tuple[str, ...]
) -> List[int]:
    """选出可重试子项下标：去重 + 越界/非可重试过滤。

    indices 来自前端且可为任意值（含重复与越界）；同一张图被提交两次会白花一次生成费。
    """
    if indices:
        return list(dict.fromkeys(
            i for i in indices
            if isinstance(i, int) and 0 <= i < len(items)
            and items[i].get("status") in retryable
        ))
    return [i for i, it in enumerate(items) if it.get("status") in retryable]


def _run_task(task_id: int, comfy_prompt_ids: List[str],
              index_map: Optional[Dict[str, int]] = None) -> None:
    """后台线程：轮询本任务全部子项的 ComfyUI 状态，逐张完成后下载并上传 TOS

    comfy_prompt_ids 与 result_images 按下标一一对应；
    index_map 用于「只重试部分子项」：{prompt_id: result_images 下标}。
    """
    from services import tos_service
    from services import comfy_priority

    # 轮询期间仍在占 ComfyUI 队列（图未全部跑完）：登记优先级，视频/超分入队前让路。
    # 重试路径没有经过 _prepare_and_run 的登记，所以这里也要打一次（set，幂等）。
    comfy_priority.begin_image_task(task_id)

    logger.info(f"[AI生图] 任务 {task_id} 开始轮询，{len(comfy_prompt_ids)} 个子项")
    interval = max(3, int(settings.IMAGE_POLL_INTERVAL))
    max_polls = max(1, int(settings.IMAGE_MAX_WAIT_MINUTES * 60 / interval))
    token = _new_run_token(task_id)

    with _slot_sem:
        try:
            pending = dict(index_map) if index_map else {
                pid: i for i, pid in enumerate(comfy_prompt_ids) if pid
            }
            started_written = False
            cancelled = False
            abandoned = False
            t_poll_start = time.time()
            t_all_done: Optional[float] = None
            download_cost = 0.0
            poll_count = 0

            for _ in range(max_polls):
                if not pending:
                    break
                if _is_cancelled(task_id):
                    cancelled = True
                    break
                if not _is_current_token(task_id, token):
                    abandoned = True
                    logger.info(f"[AI生图] 任务 {task_id} 已被新的执行接管，旧轮询退出")
                    break
                # 首轮立即查询，不白等一个 interval（单张任务可省 2s 感知延迟）
                if poll_count:
                    threading.Event().wait(interval)
                poll_count += 1
                for pid in list(pending.keys()):
                    try:
                        history = _api("GET", f"/history/{pid}").json()
                    except Exception as e:
                        logger.warning(f"[AI生图] 任务 {task_id} 查询 {pid[:8]} 失败: {e}")
                        continue
                    item = history.get(pid)
                    if item is None:
                        continue  # 排队/执行中，保持现状

                    status_str = (item.get("status") or {}).get("status_str")
                    idx = pending[pid]

                    # 回读最新 result_images 再改写，避免覆盖其它子项的增量更新
                    db = None
                    from database.database import SessionLocal
                    from models.ai_image_task import AIImageTask
                    db = SessionLocal()
                    try:
                        task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
                        items = _read_json_list(task.result_images if task else None)
                        if idx >= len(items):
                            continue

                        def _save(sub: Dict[str, Any], extra: Dict[str, Any]) -> None:
                            nonlocal started_written
                            items[idx] = sub
                            agg = _aggregate_status(items)
                            fields = {
                                "result_images": _dump_items(items),
                                "status": agg,
                            }
                            if agg in ("已完成", "部分完成", "失败", "已取消") and all(
                                s in ("已完成", "失败", "已取消")
                                for s in [it.get("status") for it in items]
                            ):
                                fields["finished_at"] = datetime.now()
                            if agg == "生成中" and not started_written:
                                fields["started_at"] = datetime.now()
                                started_written = True
                            db.query(AIImageTask).filter(AIImageTask.id == task_id).update(fields)
                            db.commit()

                        if status_str == "success":
                            finfo = _extract_output_file(item)
                            if not finfo:
                                sub = dict(items[idx], status="失败", error="生成完成但未找到输出文件")
                                _save(sub, {})
                                logger.error(f"[AI生图] 任务 {task_id} 子项 {idx} 无输出文件")
                            else:
                                _dl_t0 = time.time()
                                content, filename = _download_image(finfo)
                                _ts = (items[idx] or {}).get("target_size")
                                if _ts and len(_ts) == 2:
                                    try:
                                        content = _fit_to_target(content, tuple(_ts))
                                        filename = filename.rsplit(".", 1)[0] + ".png"
                                    except Exception as fit_err:
                                        logger.warning(f"[AI生图] 任务 {task_id} 子项 {idx} 裁剪失败（用原图）: {fit_err}")
                                # 回传前转 JPEG：外网带宽是瓶颈，体积约为 PNG 的 14%
                                content, filename = _to_jpeg(content, filename)
                                url = tos_service.upload_file(
                                    content, filename, content_type="image/jpeg", subdir="ai-image",
                                )
                                download_cost += time.time() - _dl_t0
                                # 生成当下取一次 PE 输出并落库：ComfyUI /history 会过期，
                                # 只靠查询会让历史任务的提示词在几天后变成空白
                                enhanced = ""
                                try:
                                    info = get_comfy_prompt_history(pid)
                                    enhanced = info.get("enhanced_prompt") or ""
                                except Exception as eh:
                                    logger.warning(
                                        f"[AI生图] 任务 {task_id} 子项 {idx} 取 PE 提示词失败: {eh}"
                                    )
                                sub = dict(
                                    items[idx], status="已完成", url=url, error=None,
                                    enhanced_prompt=enhanced or None,
                                    # ComfyUI 真实执行时长（不含排队），用于排查「为什么这么久」
                                    gen_seconds=_comfy_gen_seconds(item),
                                )
                                _save(sub, {})
                                logger.info(
                                    f"[AI生图] 任务 {task_id} 子项 {idx} 完成: {url}"
                                    f" | ComfyUI 执行 {sub.get('gen_seconds')}s"
                                )
                            del pending[pid]
                        elif status_str in ("error", "failed"):
                            msgs = json.dumps(
                                (item.get("status") or {}).get("messages", []), ensure_ascii=False
                            )[:400]
                            hint = humanize_comfy_error(msgs)
                            err_text = (
                                f"{hint}（原始日志：{msgs}）" if hint else (msgs or "生成失败")
                            )
                            sub = dict(items[idx], status="失败", error=err_text)
                            _save(sub, {})
                            logger.error(f"[AI生图] 任务 {task_id} 子项 {idx} 失败: {msgs}")
                            del pending[pid]
                        else:
                            # history 里但仍在执行 → 首次改「生成中」
                            if items[idx].get("status") == "排队中":
                                items[idx] = dict(items[idx], status="生成中")
                                fields = {
                                    "result_images": _dump_items(items),
                                    "status": "生成中",
                                }
                                if not started_written:
                                    fields["started_at"] = datetime.now()
                                    started_written = True
                                db.query(AIImageTask).filter(AIImageTask.id == task_id).update(fields)
                                db.commit()
                    except Exception as e:
                        logger.error(f"[AI生图] 任务 {task_id} 子项 {idx} 处理异常: {e}")
                    finally:
                        if db is not None:
                            db.close()

            if not pending:
                t_all_done = time.time()

            # 兜底：取消或超时后，仍在排队的子项落到终态
            # abandoned：本线程已被更新的执行（重试）接管，不能落任何终态，否则会覆盖新结果
            if pending and not abandoned:
                from database.database import SessionLocal
                from models.ai_image_task import AIImageTask
                db = SessionLocal()
                try:
                    task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
                    items = _read_json_list(task.result_images if task else None)
                    changed = False
                    final_status = "已取消" if cancelled else "失败"
                    reason = "任务已取消" if cancelled else (
                        f"生成超时（超过 {settings.IMAGE_MAX_WAIT_MINUTES} 分钟）"
                    )
                    for pid, idx in pending.items():
                        if idx < len(items) and items[idx].get("status") not in (
                            "已完成", "失败", "已取消"
                        ):
                            items[idx] = dict(items[idx], status=final_status, error=reason)
                            changed = True
                    if changed:
                        db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
                            "result_images": _dump_items(items),
                            "status": _aggregate_status(items),
                            "finished_at": datetime.now(),
                        })
                        db.commit()
                finally:
                    db.close()
                if cancelled:
                    logger.info(f"[AI生图] 任务 {task_id} 已取消，{len(pending)} 个子项标记已取消")
                else:
                    logger.error(f"[AI生图] 任务 {task_id} 等待超时，{len(pending)} 个子项标记失败")
                    # 超时 != 图没出来：ComfyUI 可能稍后才跑完，交后台继续捞
                    threading.Thread(
                        target=_recover_timed_out,
                        args=(task_id, token, dict(pending)),
                        daemon=True, name=f"ai-image-recover-{task_id}",
                    ).start()
                    logger.info(
                        f"[AI生图] 任务 {task_id} 已排入超时结果回收"
                        f"（+{ '/+'.join(str(d) for d in _TIMEOUT_RECOVERY_DELAYS) } 秒各查一次）"
                    )
            else:
                if abandoned:
                    pass  # 已被新执行接管，不做任何收尾写库
                elif not pending:
                    # 分段耗时：定位端到端开销（轮询等待 vs 下载回传）
                    if t_all_done is None:
                        t_all_done = time.time()
                    logger.info(
                        f"[AI生图] 任务 {task_id} 全部子项处理完毕 | 轮询等待 "
                        f"{t_all_done - t_poll_start:.1f}s | 下载+回传TOS {download_cost:.1f}s "
                        f"| 轮询 {poll_count} 次"
                    )
        except Exception as e:
            logger.error(f"[AI生图] 任务 {task_id} 轮询异常: {e}")
            _update_task(task_id, status="失败", error_message=str(e)[:900],
                         finished_at=datetime.now())
        finally:
            # 释放执行槽并启动该用户队列里的下一单（排队任务不再被拒绝提交）
            _release_task_slot(task_id)
            _clear_cancel(task_id)
            # 任务结束：注销生图优先级，让超分/视频恢复入队
            comfy_priority.end_image_task(task_id)


# ComfyUI 不会因为我们停止轮询而停止执行：队列被视频生成/超分占用时，
# 常见「判定超时 → 几分钟后图才出来」。不回收的话图只留在 ComfyUI 的 output
# 目录里，用户看到的是假失败（2026-10-26 实测任务 #57：15:46 判超时，
# ComfyUI 随后 success，产出 AIIMG-57-scene-0_00001_.png 无人认领）。
_TIMEOUT_RECOVERY_DELAYS = (300, 900, 1800)


def _should_reclaim_item(cur: Dict[str, Any]) -> bool:
    """该子项是否值得回收：只有「超时失败」才可以。

    重试已接手（排队中）、已回收（已完成）、用户取消（已取消）都不能动，
    否则会把重试后的新结果覆盖成旧图。
    """
    return cur.get("status") == "失败" and "超时" in str(cur.get("error") or "")


def _recover_timed_out(task_id: int, token: int, pending: Dict[str, int],
                       delays: Optional[Tuple[int, ...]] = None) -> None:
    """超时判定后继续尝试回收已生成的图（最多 3 轮）。

    只在「本执行仍是当前代号」且「该子项仍是超时失败」时改写，
    避免把重试后的新结果覆盖掉。
    """
    from services import tos_service
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    left = dict(pending)
    for round_no, delay in enumerate(delays or _TIMEOUT_RECOVERY_DELAYS, start=1):
        threading.Event().wait(delay)
        if not left:
            return
        if not _is_current_token(task_id, token):
            logger.info(f"[AI生图] 任务 {task_id} 超时回收退出（已有更新的执行）")
            return

        for pid in list(left.keys()):
            idx = left[pid]
            try:
                history = _api("GET", f"/history/{pid}", timeout=30).json()
            except Exception as e:
                logger.warning(f"[AI生图] 任务 {task_id} 超时回收查询 {pid[:8]} 失败: {e}")
                continue
            item = history.get(pid)
            if not item:
                continue                      # 仍在排队/执行，下一轮再看
            if (item.get("status") or {}).get("status_str") != "success":
                left.pop(pid, None)           # 明确失败或中断，不再等
                continue
            finfo = _extract_output_file(item)
            if not finfo:
                left.pop(pid, None)
                continue

            db = SessionLocal()
            try:
                task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
                if not task:
                    return
                items = _read_json_list(task.result_images)
                if idx >= len(items):
                    left.pop(pid, None)
                    continue
                cur = items[idx]
                # 只回收「超时失败」的子项：重试已接手 / 已回收 / 已取消都不动
                if not _should_reclaim_item(cur):
                    left.pop(pid, None)
                    continue
                if _is_cancelled(task_id):
                    logger.info(f"[AI生图] 任务 {task_id} 已取消，放弃超时回收")
                    return

                content, filename = _download_image(finfo)
                ts = cur.get("target_size")
                if ts and len(ts) == 2:
                    try:
                        content = _fit_to_target(content, tuple(ts))
                        filename = filename.rsplit(".", 1)[0] + ".png"
                    except Exception as fit_err:
                        logger.warning(f"[AI生图] 任务 {task_id} 回收裁剪失败（用原图）: {fit_err}")
                content, filename = _to_jpeg(content, filename)
                url = tos_service.upload_file(
                    content, filename, content_type="image/jpeg", subdir="ai-image",
                )
                enhanced = ""
                try:
                    enhanced = (get_comfy_prompt_history(pid) or {}).get("enhanced_prompt") or ""
                except Exception:
                    pass
                items[idx] = dict(
                    cur, status="已完成", url=url, error=None,
                    enhanced_prompt=enhanced or cur.get("enhanced_prompt"),
                    gen_seconds=_comfy_gen_seconds(item),
                )
                agg = _finalize_status(task.status or "", _aggregate_status(items))
                db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
                    "result_images": _dump_items(items),
                    "status": agg,
                    "finished_at": datetime.now(),
                    "error_message": None,
                })
                db.commit()
                left.pop(pid, None)
                logger.warning(
                    f"[AI生图] 任务 {task_id} 超时回收成功：子项 {idx}（第 {round_no} 轮，"
                    f"延迟 {delay}s）→ 任务状态 {agg}"
                )
            except Exception as e:
                logger.error(f"[AI生图] 任务 {task_id} 超时回收子项 {idx} 失败: {e}")
            finally:
                db.close()


def _start_task_thread(task_id: int, comfy_prompt_ids: Optional[List[str]] = None, *,
                       prep_kwargs: Optional[Dict[str, Any]] = None,
                       index_map: Optional[Dict[str, int]] = None) -> None:
    """启动任务后台线程（调用方必须已通过 _claim_task_slot 抢占执行槽）"""
    if prep_kwargs is not None:
        threading.Thread(
            target=_prepare_and_run, args=(task_id,), kwargs=prep_kwargs,
            daemon=True, name=f"ai-image-task-{task_id}",
        ).start()
    else:
        threading.Thread(
            target=_run_task, args=(task_id, comfy_prompt_ids or []),
            kwargs={"index_map": index_map},
            daemon=True, name=f"ai-image-task-{task_id}",
        ).start()


def _ensure_worker(task_id: int, comfy_prompt_ids: Optional[List[str]] = None, *,
                   prep_kwargs: Optional[Dict[str, Any]] = None,
                   index_map: Optional[Dict[str, int]] = None) -> bool:
    """确保某任务有后台线程在跑（避免重复启动）。

    :return: 是否真正启动了线程。False 表示该任务已有执行在跑，
             调用方不得再把子项改回「排队中」（否则新 pid 无人轮询）。
    """
    if not _claim_task_slot(task_id):
        return False
    try:
        _start_task_thread(
            task_id, comfy_prompt_ids, prep_kwargs=prep_kwargs, index_map=index_map,
        )
    except Exception:
        _release_task_slot(task_id)
        raise
    return True


def submit_image_task(
    *,
    tenant_id: int,
    user_id: int,
    creator_name: str = "",
    title: str,
    generation_types: List[str] = None,
    aspect_ratio: str = "1:1",
    image_count: int = 1,
    product_info: str = "",
    prompt: str = "",
    images: List[Tuple[str, bytes]],
    product_code: str = "",
    product_name: str = "",
    # ===== v2 分组参数（优先；为空时兼容 v1 旧参数）=====
    aplus_type: Optional[str] = None,
    main_types: Optional[List[str]] = None,
    main_aspect_ratio: Optional[str] = None,
    brand_settings: Optional[Dict[str, Any]] = None,
    provider: str = "local",
    layout_keys: Optional[List[str]] = None,
    product_fact_card: Optional[Dict[str, Any]] = None,
    brand_name: str = "",
) -> Dict[str, Any]:
    """提交套图生成任务：落库后立即返回，准备与轮询全部在后台线程

    :param generation_types: 生成类型 key 列表（GEN_TYPES 的子集）
    :param images: [(文件名, 图片字节)]，1~8 张参考图
    :param product_fact_card: P4 结构化商品事实卡；提供时覆盖 product_info 作为唯一事实来源
    :param brand_name: 品牌A+横幅必填的英文品牌名（保留大小写），其他类型忽略
    """
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    if not images:
        raise AIImageError("请至少上传一张参考图")
    if len(images) > MAX_REF_IMAGES:
        raise AIImageError(f"最多上传 {MAX_REF_IMAGES} 张参考图")

    # P4 结构化商品事实卡：提供时校验并渲染，覆盖自然语言 product_info
    fact_card: Optional[Dict[str, Any]] = None
    if product_fact_card:
        from services.aplus_fact_card import (
            normalize_fact_card, render_fact_card, validate_fact_card,
        )
        errors = validate_fact_card(product_fact_card)
        if errors:
            raise AIImageError("商品事实卡校验失败：" + "；".join(errors[:5]))
        fact_card = normalize_fact_card(product_fact_card)
        product_info = render_fact_card(fact_card)

    # 品牌/区域设置兜底：这三项决定画面语境与文字语言，缺省就用默认值，
    # 保证「未填」也一定会进提示词（否则模型按自己的偏好出图，且详情页显示空白）
    brand_settings = dict(brand_settings or {})
    for _k, _v in DEFAULT_BRAND_SETTINGS.items():
        if not str(brand_settings.get(_k) or "").strip():
            brand_settings[_k] = _v

    # 生成通道：local=本地 ComfyUI（Qwen Image 2.1）/ relay=中转站（Nano Banana 2）
    provider = (provider or "local").strip().lower()
    if provider not in ("local", "relay"):
        raise AIImageError(f"不支持的生成模型: {provider}")
    if provider == "relay":
        from services import relay_image_service
        if not relay_image_service.is_configured():
            raise AIImageError("中转站模型未配置（缺少 IMAGE_RELAY_API_BASE / IMAGE_RELAY_API_KEY）")
    image_count = min(max(1, int(image_count)), 4)

    # 品牌A+横幅：品牌名逐字进画面，必须由用户显式给出（用于填默认的品牌名一律拒绝）。
    # 本地 / 中转站（Nano Banana 2）通道均支持，中转站走专属的品牌横幅设计指令。
    if aplus_type == "aplus_banner":
        brand_name = _normalize_brand_name(brand_name)
        if not brand_name:
            raise AIImageError("品牌A+横幅必须填写品牌名（英文，2–20 个字符，区分大小写）")
        # 落库到 brand_settings（不新增数据库列），历史回填与重试都从这里读
        brand_settings["brand_name"] = brand_name
    else:
        brand_name = ""
        # 历史回填可能把上一单的 brand_name 一并带回来，非横幅任务不得残留
        brand_settings.pop("brand_name", None)

    # ===== v2 分组解析（兼容 v1：未传新参数时由旧参数映射）=====
    if aplus_type is None and main_types is None:
        legacy = [t for t in (generation_types or []) if t in MAIN_GEN_TYPES]
        main_types = legacy or None
        main_aspect_ratio = aspect_ratio
        if "aplus" in (generation_types or []):
            logger.warning("[AI生图] v1 调用含旧 'aplus' 类型，已忽略（请改用 aplus_type 分组参数）")
    if aplus_type is not None and aplus_type not in APLUS_SPECS:
        raise AIImageError(f"不支持的 A+ 类型: {aplus_type}")
    # main_types v2.1：支持 dict（类型→每类型独立张数）与 list（全部用 image_count）两种形态
    main_counts: Dict[str, int] = {}
    if isinstance(main_types, dict):
        for t, c in main_types.items():
            if t in MAIN_GEN_TYPES and c:
                main_counts[t] = min(max(1, int(c)), 4)
    else:
        for t in (main_types or []):
            if t in MAIN_GEN_TYPES:
                main_counts[t] = image_count
    bad = [t for t in main_counts if t not in MAIN_GEN_TYPES]
    if bad:
        raise AIImageError(f"不支持的主副图类型: {bad}")
    main_aspect_ratio = main_aspect_ratio or "1:1"
    if main_counts and main_aspect_ratio not in ASPECT_RATIO_MAP:
        raise AIImageError(f"不支持的主副图比例: {main_aspect_ratio}")
    if not aplus_type and not main_counts:
        raise AIImageError("请至少选择 A+ 类型或主副图类型")

    # ===== A+ 版式：选 N 个版式 → 每种生成一张；未选 → 按画幅随机取官方版式 =====
    # 与参考实现（ai.linkfox.com 商品套图）一致：没有「无版式自由发挥」这条兜底路径，
    # 因为纯提示词的 A+ 出图只会是「宽幅场景照 + 一块死白」，结构上就不像 A+ 模块。
    layouts: List[Dict[str, Any]] = []
    aplus_sheets = image_count if aplus_type else 0
    layout_checks: List[Dict[str, Any]] = []
    if aplus_type == "aplus_banner":
        # 品牌横幅不使用排版模板：不解析 layout_keys、不随机取官方版式，
        # 张数完全由用户自选（1~4），每张只换 seed。
        if layout_keys:
            logger.info("[AI生图] 品牌A+横幅不使用版式，已忽略 layout_keys")
        layouts = []
        aplus_sheets = image_count
    elif aplus_type:
        from services import ai_image_layout_service as layout_svc
        keys = list(layout_keys or [])
        if keys:
            try:
                layouts = layout_svc.resolve_layouts(user_id, tenant_id, keys)
            except layout_svc.LayoutError as e:
                # 统一转成 AIImageError：路由层只把 AIImageError 映射成 400，
                # 否则「版式不存在」会被当成 500 内部错误，前端只看到「提交任务失败」
                raise AIImageError(str(e))
            aplus_sheets = len(layouts)
        else:
            tag = "vertical" if aplus_type == "aplus_mobile" else "horizontal"
            try:
                all_presets = layout_svc.load_presets()
            except layout_svc.LayoutError as e:
                raise AIImageError(str(e))
            pool = [p for p in all_presets if p["ratio_tag"] == tag] or all_presets
            if not pool:
                raise AIImageError("内置版式库为空（backend/prompts/aplus_layouts.json 缺失或格式错误）")
            picks: List[Dict[str, Any]] = []
            for i in range(image_count):
                picks.append(pool[i % len(pool)] if i < len(pool) else random.choice(pool))
            layouts, aplus_sheets = picks, image_count
        # 上传版式要额外占 1 个参考图通道（商品图 + 版式图 ≤ 8）
        if any(_layout_has_image(l) for l in layouts) and len(images) + 1 > MAX_REF_IMAGES:
            raise AIImageError(
                f"上传的版式图会额外占用 1 个参考图通道，商品图请控制在 {MAX_REF_IMAGES - 1} 张以内"
            )
        # P4.1：创建任务前校验模板可执行性与画幅兼容性，禁止静默退化/错配
        layout_checks: List[Dict[str, Any]] = []
        for lay in layouts:
            check = layout_svc.check_aplus_compatibility(lay, aplus_type)
            if not check.get("ok"):
                raise AIImageError(check.get("error") or "版式不可用")
            layout_checks.append(check)
    else:
        layout_checks = []

    # 单任务总张数护栏：防止一次提交就把 ComfyUI 串行队列灌满（与生视频互抢）
    total_sheets = aplus_sheets + sum(main_counts.values())
    if total_sheets > MAX_SHEETS_PER_TASK:
        raise AIImageError(
            f"单个任务最多生成 {MAX_SHEETS_PER_TASK} 张（当前 {total_sheets} 张），请减少类型或张数"
        )

    no_text_mode = bool((brand_settings or {}).get("no_text_mode"))
    # A+ 文字模式：默认无字（T0）。显式传 text_mode="with_text" 才允许 PE 排版文字。
    # 无字时 _build_prompt 会同步剔除品牌语言字段——那是 PE 转英文输出的触发器。
    aplus_text_mode = str((brand_settings or {}).get("text_mode") or "").strip().lower()
    # 品牌横幅固定「无字底图 + 后置品牌名合约」：文字开关对它不生效，
    # 否则 PE 会自行排版文案，与「画面只有品牌名」直接冲突。
    aplus_no_text = True if aplus_type == "aplus_banner" else (aplus_text_mode != "with_text")

    # P4.1：模板快照在创建任务时固化（含执行模式、版本、比例、契约），
    # 生成与重试都只读快照，模板后续被修改不影响历史任务。
    layout_snapshots: List[Dict[str, Any]] = []
    layout_warnings: List[str] = []
    for lay, check in zip(layouts, layout_checks):
        mode = layout_svc.execution_mode(lay)
        contract = ""
        if mode in ("zones", "text"):
            contract = _layout_contract(lay, aplus_type, aplus_no_text) or ""
        snap = {
            "layout_id": lay.get("id") or lay.get("key"),
            "key": lay.get("key"),
            "name": lay.get("name"),
            "source": lay.get("source"),
            "ratio_tag": lay.get("ratio_tag"),
            "aspect_ratio": lay.get("aspect_ratio"),
            "template_version": lay.get("template_version"),
            "structure": lay.get("structure") or "",
            "zones": lay.get("zones") or [],
            "image_url": lay.get("image_url") or "",
            "execution_mode": mode,
            "warnings": list(check.get("warnings") or []),
            "layout_contract": contract,
        }
        lay["_snapshot"] = snap
        layout_snapshots.append(snap)
        for w in snap["warnings"]:
            layout_warnings.append(f"{snap['name'] or snap['key']}：{w}")

    # 生成计划：(类型键, 生成比例, 目标裁剪尺寸|None, 展示名, seq, no_text, megapixels)
    gen_plan: List[Tuple[str, str, Optional[Tuple[int, int]], str, int, bool, float]] = []
    if aplus_type:
        spec = APLUS_SPECS[aplus_type]
        aplus_mp = float(spec.get("megapixels") or DEFAULT_MEGAPIXELS)
        for seq in range(aplus_sheets):
            gen_plan.append((aplus_type, spec["gen_ratio"], spec["target_size"],
                             spec["label"], seq, aplus_no_text, aplus_mp))
    for t, c in main_counts.items():
        for seq in range(c):
            gen_plan.append((t, main_aspect_ratio, None, MAIN_GEN_TYPES[t], seq,
                             no_text_mode, DEFAULT_MEGAPIXELS))

    # 展开子任务（target_size 供轮询完成后裁剪；no_text 供提示词追加无文字指令）
    items: List[Dict[str, Any]] = []
    for (t, _ratio, target_size, label, seq, _no_text, _mp) in gen_plan:
        item: Dict[str, Any] = {
            "type": t, "label": label, "seq": seq,
            "status": "排队中", "url": None, "error": None,
            "target_size": list(target_size) if target_size else None,
            "no_text": _no_text,
        }
        # A+ 子项绑定自己的版式（seq 即 layouts 下标）；快照在创建任务时即落库
        if aplus_type and t == aplus_type and seq < len(layouts):
            snap = dict(layouts[seq].get("_snapshot") or {})
            if snap:
                item["layout"] = snap
        # 品牌横幅：品牌名随子项落库，重试/历史详情不依赖 brand_settings 是否被改动
        if t == "aplus_banner":
            item["brand_name"] = brand_name
        items.append(item)
    display_types = [aplus_type] if aplus_type else []
    display_types += list(main_counts.keys())

    prep_kwargs = {
        "images": images,
        "gen_plan": gen_plan,
        "image_count": image_count,
        "product_info": product_info,
        "extra_prompt": prompt,
        "brand_genes": _compile_brand_genes(brand_settings),
        "provider": provider,
        "product_name": product_name,
        "layouts": layouts,
        "fact_card": fact_card,
        "brand_name": brand_name,
    }

    db = SessionLocal()
    try:
        # 用户级排队：任务一律接受并落库；同一用户已有任务在跑时，新任务保持「排队中」，
        # 由 _release_task_slot → _advance_user_queue 在上一单结束后自动启动。
        with _user_submit_lock(user_id):
            task = AIImageTask(
                tenant_id=tenant_id, user_id=user_id,
                creator_name=_safe_text(creator_name, 100) or "",
                title=_safe_text(title, 200) or "商品套图", status="排队中",
                provider=provider,
                generation_types=json.dumps(display_types, ensure_ascii=False),
                aspect_ratio=main_aspect_ratio, image_count=image_count,
                product_info=_safe_text(product_info, 5000),
                prompt=_safe_text(prompt, 2000),
                product_code=_safe_text(product_code, 50),
                product_name=_safe_text(product_name, 200),
                aplus_type=aplus_type,
                main_types=json.dumps(main_counts, ensure_ascii=False) if main_counts else None,
                main_aspect_ratio=main_aspect_ratio if main_counts else None,
                brand_settings=json.dumps(brand_settings, ensure_ascii=False) if brand_settings else None,
                result_images=_dump_items(items),
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            task_id = task.id
    finally:
        db.close()

    # 登记归属：空闲则立即启动，否则入队等待（HTTP 请求仍立即返回，前端看到「排队中」）
    if _register_task_run(task_id, user_id, prep_kwargs):
        if not _ensure_worker(task_id, prep_kwargs=prep_kwargs):
            # 理论不可达：任务刚插入 DB，不应已有执行线程
            _release_task_slot(task_id)
    else:
        logger.info(f"[AI生图] 任务 {task_id} 已加入用户 {user_id} 队列，等待前序任务完成")

    db = SessionLocal()
    try:
        task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
        out = _serialize(task)
        if layout_warnings:
            out["layout_warnings"] = layout_warnings
        return out
    finally:
        db.close()


def _merge_facts(user_text: str, auto_text: str) -> str:
    """用户填写的商品信息 与 看图分析结果 合并。

    自动分析只补用户没写的字段：两边都有「品名」时保留用户版本，
    否则提示词里会出现两行互相矛盾的品名，模型会自行挑一个。
    """
    user_text = (user_text or "").strip()
    auto_text = (auto_text or "").strip()
    if not auto_text:
        return user_text
    if not user_text:
        return auto_text
    existing = {
        ln.split("：", 1)[0].strip()
        for ln in user_text.splitlines() if "：" in ln
    }
    kept = [
        ln for ln in auto_text.splitlines()
        if not ("：" in ln and ln.split("：", 1)[0].strip() in existing)
    ]
    extra = "\n".join(kept).strip()
    return f"{user_text}\n{extra}" if extra else user_text


def _card_to_text(card: Dict[str, Any]) -> str:
    """生视频风格的商品信息卡 → 「品名：… 材质：…」文本（与前端 analysisToText 同结构）。

    含「无法确认/不确定/未知」的值直接丢弃：这类占位符进提示词后会被模型当成真实属性。
    """
    def _val(v: Any) -> str:
        if isinstance(v, (list, tuple)):
            items = [str(x).strip() for x in v if str(x).strip()]
        else:
            items = [str(v).strip()] if v else []
        keep = [
            x for x in items
            if not any(k in x for k in ("无法", "不确定", "未知", "unknown", "n/a", "N/A"))
        ]
        return "、".join(keep)

    rows = [
        ("品名", card.get("product_name_cn")),
        ("英文品名", card.get("product_name_en")),
        ("品类", card.get("product_type") or card.get("amazon_category")),
        # 风格锚点：让同一任务的多张图共享同一场景主题（人物不锁定，主题要一致）
        ("风格倾向", card.get("style_context")),
        ("材质", card.get("material")),
        ("颜色", card.get("colors")),
        ("组件", card.get("product_components")),
        ("产品尺寸", card.get("product_size")),
        ("核心卖点", card.get("selling_points")),
        ("使用方式", card.get("usage_methods")),
        ("使用场景", card.get("usage_scenarios")),
    ]
    out: List[str] = []
    for key, val in rows:
        text = _val(val)
        if text:
            out.append(f"{key}：{text}")
    return "\n".join(out)


def _plan_design_jobs(
    gen_plan: List[Tuple[str, str, Optional[Tuple[int, int]], str, int, bool, float]],
) -> List[Tuple[int, str, str, int]]:
    """设计任务编排：scene_person / A+ **每张一个任务**（构图、光位、版式分区各不相同），
    其余类型同类型多张复用一份（省调用，seed 变化即可）。

    A+ 必须逐张设计：一个任务里每张挂的是**不同版式**，复用一份设计会让所有版式出成同一张。
    返回 [(gen_plan 下标, 类型, 比例, seq)]
    """
    jobs: List[Tuple[int, str, str, int]] = []
    seen: set = set()
    for gi, (t, ratio, _ts, _label, seq, _nt, _mp) in enumerate(gen_plan):
        if t == "scene_person" or t.startswith("aplus"):
            jobs.append((gi, t, ratio, seq))
        elif t not in seen:
            seen.add(t)
            jobs.append((gi, t, ratio, 0))
    return jobs


def _build_relay_items(
    gen_plan: List[Tuple[str, str, Optional[Tuple[int, int]], str, int, bool, float]],
    prompts: List[str],
    prompt_cn_map: Dict[int, str],
    shot_designs: List[Dict[str, Any]],
    layouts: Optional[List[Dict[str, Any]]],
    relay,
) -> List[Dict[str, Any]]:
    """构建中转站子项（仅元数据与提示词，尚未有图）。

    必须在调用生成接口**之前**落库：服务重启后恢复线程只靠这份数据补跑，
    否则在途的 relay 任务只能整单判失败、远端已产生的费用白花。
    """
    items: List[Dict[str, Any]] = []
    for gi, (t, _ratio, target_size, label, seq, no_text, _mp) in enumerate(gen_plan):
        item: Dict[str, Any] = {
            "type": t, "label": label, "seq": seq,
            "status": "排队中", "url": None, "error": None,
            "target_size": list(target_size) if target_size else None,
            "no_text": no_text,
            # 中转站实际提交的完整提示词（无 PE，这就是最终提示词）
            "final_prompt": prompts[gi] if gi < len(prompts) else None,
            # 中文对照（运营阅读用，不参与出图）
            "prompt_cn": prompt_cn_map.get(gi) or None,
            # 该张是否由 AI 按产品定制（否则为模板回退）
            "prompt_designed": bool(shot_designs[gi]) if gi < len(shot_designs) else False,
            # AI 清点出的商品件数清单（用于核对一致性）
            "inventory": (shot_designs[gi] if gi < len(shot_designs) else {}).get("inventory") or None,
            # 该张的差异化维度（仅带人物场景），便于排查"为什么这几张构图不同"
            "variation": (
                relay.PERSON_VARIATIONS[seq % len(relay.PERSON_VARIATIONS)]
                if t == "scene_person" else None
            ),
        }
        if layouts and t.startswith("aplus") and seq < len(layouts):
            lay = layouts[seq]
            item["layout"] = {
                "key": lay.get("key"), "name": lay.get("name"),
                "source": lay.get("source"), "ratio_tag": lay.get("ratio_tag"),
                "image_url": lay.get("image_url") or "",
            }
        items.append(item)
    return items


def _prepare_and_run_relay(
    task_id: int,
    *,
    images: List[Tuple[str, bytes]],
    gen_plan: List[Tuple[str, str, Optional[Tuple[int, int]], str, int, bool, float]],
    product_info: str,
    extra_prompt: str,
    brand_genes: str = "",
    product_name: str = "",
    layouts: Optional[List[Dict[str, Any]]] = None,
    brand_name: str = "",
) -> None:
    """中转站通道：按图类型设计提示词 → 并行生成 → 转存 TOS。

    与旧版的关键差异：不再"先提取商品事实、再套固定模板"。
    每一类图单独调用一次多模态模型，同时完成「读商品」与「设计这张图怎么拍」，
    产出的英文提示词直接驱动出图（与生视频 VOICEOVER_PLAN 的一次性多模态同思路）。
    同类型多张复用同一条设计结果，seed 不同即可。
    设计失败时回退到轻量模板，保证仍能出图。
    """
    from services import tos_service
    from services import relay_image_service as relay

    t0 = time.time()
    # 子项在生成前先构建并落库（重启恢复依赖它），异常时也用它把未完成项落终态
    items: List[Dict[str, Any]] = []
    token = _new_run_token(task_id)
    try:
        with _lock:
            _running.add(task_id)

        # 1. 参考图存 TOS（供前端展示，失败不影响生成）
        def _to_tos(item: Tuple[str, bytes]) -> Optional[str]:
            name, content = item
            try:
                return tos_service.upload_file(content, name, subdir="ai-image")
            except Exception as e:
                logger.warning(f"[AI生图] 参考图上传 TOS 失败（不影响生成）: {e}")
                return None

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(images)))) as ex:
            urls = [u for u in ex.map(_to_tos, images) if u]
        if urls:
            _update_task(task_id, images=json.dumps(urls, ensure_ascii=False))

        main_bytes = images[0][1]
        facts_text = (product_info or "").strip()

        # A+ 版式图取回（只有上传的版式有图；内置版式走 structure 文本）
        layout_bytes_map: Dict[str, bytes] = {}
        for lay in (layouts or []):
            if _layout_has_image(lay) and not (lay.get("structure") or "").strip():
                try:
                    layout_bytes_map[lay.get("key") or ""] = _fetch_url_bytes(lay["image_url"])
                except Exception as e:
                    logger.warning(f"[AI生图] 任务 {task_id} 版式图取回失败（该张只用文字版式）: {e}")

        # 2. 商品分析：与生视频页**同一套** SYSTEM_PROMPT / 全部参考图 / temperature=0.3 /
        #    结构化输出。分析必须单独成一步 —— 夹在"写英文提示词"里时注意力被抢走，
        #    实测同一张图会在两个图类型间给出互相矛盾的商品结论（海滩套装 vs 新娘新郎）。
        main_area = _image_area(main_bytes)
        if not bool(settings.IMAGE_RELAY_ANALYZE):
            logger.info(f"[AI生图] 任务 {task_id} 已关闭自动分析，沿用现有商品信息")
        elif main_area and main_area < _MIN_MAIN_IMAGE_SIDE ** 2:
            logger.warning(
                f"[AI生图] 任务 {task_id} 主参考图过小（约 {int(main_area ** 0.5)}px），"
                f"跳过自动分析以避免写入误识别结果"
            )
        else:
            try:
                from services.ai_creation_service import analyze_product_card

                card = analyze_product_card(images, product_name=product_name)   # 全部参考图一起送
                card_text = _card_to_text(card)
                if card_text:
                    facts_text = _merge_facts(facts_text, card_text)
                    _update_task(task_id, product_info=_safe_text(facts_text, 5000))
                logger.info(
                    f"[AI生图] 任务 {task_id} 商品分析完成（{len(images)} 张图）: "
                    f"{(card.get('product_name_cn') or card.get('product_name_en') or '')[:20]}"
                )
            except Exception as e:
                logger.warning(f"[AI生图] 任务 {task_id} 商品分析失败（继续用现有信息）: {e}")

        # 3. 设计提示词。
        #    scene_person **每张单独设计**（构图/姿态/光位要不同，逐张才有差异）；
        #    其他类型同类型多张复用一份（省调用，seed 变化即可）。
        per_shot_types = {"scene_person"}
        designed_tasks = _plan_design_jobs(gen_plan)

        designs_by_shot: Dict[int, Dict[str, Any]] = {}
        designs_by_type: Dict[str, Dict[str, Any]] = {}
        if bool(settings.IMAGE_RELAY_ANALYZE):
            # 同一张参考图只压缩一次（多类型并行设计时复用，避免重复解码大图）
            prepared = relay.prepare_reference(main_bytes)

            def _design_one(
                job: Tuple[int, str, str, int]
            ) -> Tuple[Tuple[int, str, str, int], Dict[str, Any]]:
                gi, t, ratio, seq = job
                variation = (
                    relay.PERSON_VARIATIONS[seq % len(relay.PERSON_VARIATIONS)]
                    if t in per_shot_types else None
                )
                lay = (layouts[seq] if (layouts and t.startswith("aplus") and seq < len(layouts))
                       else None)
                # 版式描述喂给设计模型前必须做同一套无字清洗：
                # relay 也走 A+ 无字模式，把「标题/文案/文字区」原样给设计模型，
                # 它会把「标题区」翻成 "headline text block"，出图阶段就画出字块。
                # no_text 取该张自己的值（gen_plan[gi][5]），不能用 submit 里的局部变量。
                shot_no_text = bool(gen_plan[gi][5]) if gi < len(gen_plan) else False
                hint = ((lay.get("structure") or "") if lay else "")
                if hint and t.startswith("aplus") and shot_no_text:
                    hint = _sanitize_structure_no_text(hint)
                try:
                    d = relay.design_shot_prompt(
                        main_bytes, gen_type=t, ratio=ratio,
                        # 传「已确认的事实卡」：design 只按它设计画面，不再二次识别
                        product_info=facts_text, extra=extra_prompt,
                        brand_genes=brand_genes, prepared=prepared,
                        variation=variation,
                        layout_bytes=(layout_bytes_map.get(lay.get("key") or "") if lay else None),
                        layout_hint=hint,
                        # 品牌横幅：设计指令与商品图完全不同，需要逐字品牌名
                        brand_name=(brand_name if t == "aplus_banner" else ""),
                    )
                    return job, d
                except Exception as e:
                    logger.warning(
                        f"[AI生图] 任务 {task_id} 类型 {t}#{seq} 提示词设计失败，回退模板: {e}"
                    )
                    return job, {}

            with ThreadPoolExecutor(max_workers=min(4, max(1, len(designed_tasks)))) as ex:
                for (gi, t, _r, _s), d in ex.map(_design_one, designed_tasks):
                    if not d:
                        continue
                    if t in per_shot_types:
                        designs_by_shot[gi] = d
                    elif t not in designs_by_type:
                        designs_by_type[t] = d

            logger.info(
                f"[AI生图] 任务 {task_id} 提示词设计完成 "
                f"{len(designs_by_shot) + len(designs_by_type)}/{len(designed_tasks)} 项"
            )

        # 4. 逐项确定最终提示词（设计结果优先，缺失回退模板）
        #    按下标取设计结果：scene_person 每张一份，其他类型共用一份
        multi_types = _multi_shot_types(gen_plan)
        prompt_cn_map: Dict[int, str] = {}
        shot_designs: List[Dict[str, Any]] = []
        prompts: List[str] = []
        for gi, (t, ratio, target_size, label, seq, no_text, mp) in enumerate(gen_plan):
            d = designs_by_shot.get(gi) or designs_by_type.get(t) or {}
            shot_designs.append(d)
            designed = d.get("prompt_en")
            if designed:
                p = designed
                prompt_cn_map[gi] = d.get("prompt_cn") or ""
                neg = d.get("negative") or ""
                if neg:
                    p += f"\n\nAvoid: {neg}"
            else:
                p = _build_prompt(
                    t, facts_text, extra_prompt, brand_genes,
                    multi_shot=(t in multi_types), no_text=no_text,
                    # 品牌横幅回退模板时品牌名同样要进提示词
                    brand_name=(brand_name if t == "aplus_banner" else ""),
                )
                # 设计失败回退模板时，版式只能靠文字条款补（设计成功时已由设计模型写进 prompt_en）
                if layouts and t.startswith("aplus") and seq < len(layouts):
                    _lay = layouts[seq]
                    if _lay.get("structure"):
                        p = _build_prompt(
                            t, facts_text, extra_prompt, brand_genes,
                            multi_shot=(t in multi_types), no_text=no_text,
                            layout_structure=_lay["structure"],
                            layout_with_image=_layout_has_image(_lay),
                        )
                    else:
                        from services import ai_image_layout_service as layout_svc
                        p += layout_svc.layout_clause(_lay, no_text=no_text)
            if t == "aplus_banner":
                # 品牌横幅：唯一文字就是品牌名，禁止把「画面不得出现文字」追加进去自相矛盾；
                # 比例按 970×300 声明（21:9 只是本地链路的生成比例，与横幅无关）。
                p += "\n\n画面比例 970×300 横幅（约 3.2:1）。只输出生成后的图片，不要输出任何文字说明。"
                p += f"\n品牌名 {brand_name} 是画面中唯一允许出现的文字，逐字精确、大小写不变；除它之外不得出现任何其他文字、字母或数字。"
            else:
                p += f"\n\n画面比例 {ratio}。只输出生成后的图片，不要输出任何文字说明。"
                # 除卖点图外一律禁止画面文字：模型容易自作主张加英文标签牌
                if t != "selling_point":
                    p += "\n画面中不得出现任何文字、标签、价签、水印或标识。"
                if no_text:
                    p += "\n画面中不要出现任何文字、标语、水印或标识。"
            prompts.append(p)

        logger.info(f"[AI生图] 任务 {task_id} 走中转站生成，共 {len(prompts)} 张")

        # 4. 先落「排队中 + 最终提示词」再生成：
        #    重启恢复线程按这份数据补跑，避免在途任务整单判失败、远端已出图却丢失。
        items = _build_relay_items(
            gen_plan, prompts, prompt_cn_map, shot_designs, layouts, relay,
        )
        _update_task(
            task_id, status="生成中", started_at=datetime.now(),
            result_images=_dump_items(items),
        )
        logger.info(f"[AI生图] 任务 {task_id} 走中转站生成，共 {len(prompts)} 张")

        # 5. 并行生成（失败项独立，不影响其它张）
        results = relay.generate_batch(main_bytes, prompts)

        # 6. 填充结果 + 转存 TOS
        ok = 0
        for gi, ((t, _ratio, target_size, _label, seq, _no_text, _mp), r) in enumerate(
            zip(gen_plan, results)
        ):
            item = items[gi]
            if not r.get("ok"):
                item.update(status="失败", error=r.get("error") or "生成失败")
                continue
            try:
                content = r["data"]
                if target_size:
                    content = _fit_to_target(content, tuple(target_size))
                content, fname = _to_jpeg(content, f"relay-{task_id}-{t}-{seq}.png")
                url = tos_service.upload_file(
                    content, fname, content_type="image/jpeg", subdir="ai-image",
                )
                item.update(status="已完成", url=url)
                ok += 1
            except Exception as e:
                item.update(status="失败", error=str(e)[:300])

        # 过期执行：取消后用户已重试（新代号已接管），本次结果必须丢弃，
        # 否则会把重试的成果覆盖回旧结果
        if not _is_current_token(task_id, token):
            logger.info(f"[AI生图] 任务 {task_id} 已有更新的执行，丢弃本次中转站结果")
            return

        # 取消检查：中转站是普通 HTTP 调用，无法像 ComfyUI 那样删队列项，
        # 只能在结果落库前丢弃未完成项，否则用户点了取消仍会看到「已完成」
        if _is_cancelled(task_id):
            for i, it in enumerate(items):
                if it.get("status") not in ("已完成", "失败"):
                    items[i] = dict(it, status="已取消", error="任务已取消")
            ok = 0
        agg = _aggregate_status(items)
        _update_task(
            task_id,
            result_images=_dump_items(items),
            status=_finalize_status(task_id, agg),
            finished_at=datetime.now(),
        )
        logger.info(
            f"[AI生图] 任务 {task_id} 中转站完成 {ok}/{len(items)} 张，"
            f"耗时 {time.time() - t0:.1f}s"
        )
    except Exception as e:
        logger.exception(f"[AI生图] 中转站任务 {task_id} 异常")
        msg = str(e)[:900]
        # 已落库的子项必须一起落终态，否则重启恢复无从判断哪些是真的没跑
        for i, it in enumerate(items):
            if it.get("status") not in ("已完成", "失败", "已取消"):
                items[i] = dict(it, status="失败", error=f"生成中断：{msg}"[:300])
        _update_task(
            task_id, status="失败", error_message=msg,
            result_images=_dump_items(items) if items else None,
            finished_at=datetime.now(),
        )
    finally:
        _release_task_slot(task_id)
        _clear_cancel(task_id)


def _ensure_relay_resume_worker(task_id: int) -> bool:
    """重启后补跑中转站任务的后台线程（执行槽原子抢占，避免与用户重试并发）"""
    if not _claim_task_slot(task_id):
        return False
    try:
        threading.Thread(
            target=_resume_relay_task, args=(task_id,),
            daemon=True, name=f"ai-image-resume-relay-{task_id}",
        ).start()
    except Exception:
        _release_task_slot(task_id)
        raise
    return True


def _resume_relay_task(task_id: int) -> None:
    """服务重启后补跑中转站任务：复用已落库的提示词，只生成未完成子项。

    中转站是同步 HTTP 调用，重启时无法续接原请求；但子项在生成前已落库
    （提示词、比例、目标尺寸），因此补跑不会重复支付设计费用，也不会整单丢失。
    """
    from services import tos_service
    from services import relay_image_service as relay
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        row = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.deleted_at.is_(None),
            )
            .first()
        )
        if not row:
            _release_task_slot(task_id)
            return
        items = _read_json_list(row.result_images)
        image_urls = _read_json_list(row.images)
    finally:
        db.close()

    pending = [
        (i, it) for i, it in enumerate(items)
        if it.get("status") in ("排队中", "生成中")
    ]
    if not pending:
        if not items:
            # 连子项都没落库：设计/上传阶段就中断，无从补跑，明确判失败而不是悬在「生成中」
            _update_task(
                task_id, status="失败",
                error_message="服务重启，任务尚未进入生成阶段，请重新提交",
                finished_at=datetime.now(),
            )
        else:
            _update_task(
                task_id, result_images=_dump_items(items),
                status=_aggregate_status(items), finished_at=datetime.now(),
            )
        _release_task_slot(task_id)
        return

    token = _new_run_token(task_id)
    try:
        # 设计阶段就中断的任务没有提示词，重跑设计成本高且结果不可控 → 明确判失败
        lacking = [i for i, it in pending if not (it.get("final_prompt") or "").strip()]
        if lacking:
            for i in lacking:
                items[i] = dict(
                    items[i], status="失败",
                    error="服务重启，提示词尚未生成，请重新提交任务",
                )
            agg = _aggregate_status(items)
            _update_task(
                task_id, result_images=_dump_items(items),
                status=agg,
                finished_at=datetime.now() if agg not in ("生成中", "排队中") else None,
            )
            logger.warning(
                f"[AI生图] 任务 {task_id} 重启恢复：{len(lacking)} 个子项缺少提示词，已判失败"
            )
            return

        if not image_urls:
            for i, _it in pending:
                items[i] = dict(items[i], status="失败", error="参考图已丢失，请重新提交任务")
            agg = _aggregate_status(items)
            _update_task(
                task_id, result_images=_dump_items(items),
                status=agg,
                finished_at=datetime.now() if agg not in ("生成中", "排队中") else None,
            )
            return

        _http = requests.Session()
        _http.trust_env = False
        resp = _http.get(image_urls[0], timeout=60)
        resp.raise_for_status()
        main_bytes = resp.content

        prompts = [it["final_prompt"] for _i, it in pending]
        logger.info(f"[AI生图] 任务 {task_id} 重启恢复中转站，补跑 {len(prompts)} 张")
        results = relay.generate_batch(main_bytes, prompts)

        ok = 0
        for (i, it), r in zip(pending, results):
            if not r.get("ok"):
                items[i] = dict(
                    it, status="失败", url=None,
                    error=(r.get("error") or "生成失败")[:300],
                )
                continue
            try:
                content = r["data"]
                ts = it.get("target_size")
                if isinstance(ts, list) and len(ts) == 2:
                    content = _fit_to_target(content, tuple(ts))
                content, fname = _to_jpeg(
                    content, f"relay-{task_id}-{it.get('type')}-{it.get('seq')}-resume.png"
                )
                url = tos_service.upload_file(
                    content, fname, content_type="image/jpeg", subdir="ai-image",
                )
                items[i] = dict(it, status="已完成", url=url, error=None)
                ok += 1
            except Exception as e:
                items[i] = dict(it, status="失败", error=str(e)[:300])

        # 取消同样适用于恢复线程：结果落库前把未完成项落「已取消」，避免取消后又变已完成
        if _is_cancelled(task_id):
            for i, it in pending:
                if items[i].get("status") not in ("已完成", "失败"):
                    items[i] = dict(items[i], status="已取消", error="任务已取消")

        if not _is_current_token(task_id, token):
            return
        agg = _finalize_status(task_id, _aggregate_status(items))
        _update_task(
            task_id, result_images=_dump_items(items),
            status=agg,
            finished_at=datetime.now() if agg not in ("生成中", "排队中") else None,
        )
        logger.info(f"[AI生图] 任务 {task_id} 重启恢复完成 {ok}/{len(pending)} 张")
    except Exception as e:
        logger.exception(f"[AI生图] 任务 {task_id} 重启恢复中转站失败")
        for i, _it in pending:
            if items[i].get("status") not in ("已完成", "失败", "已取消"):
                items[i] = dict(items[i], status="失败", error=f"恢复失败：{e}"[:300])
        _update_task(
            task_id, result_images=_dump_items(items),
            status=_aggregate_status(items), finished_at=datetime.now(),
        )
    finally:
        _release_task_slot(task_id)
        _clear_cancel(task_id)


def _prepare_and_run(
    task_id: int,
    *,
    images: List[Tuple[str, bytes]],
    gen_plan: List[Tuple[str, str, Optional[Tuple[int, int]], str, int, bool, float]],
    image_count: int,
    product_info: str,
    extra_prompt: str,
    brand_genes: str = "",
    provider: str = "local",
    product_name: str = "",
    layouts: Optional[List[Dict[str, Any]]] = None,
    fact_card: Optional[Dict[str, Any]] = None,
    brand_name: str = "",
) -> None:
    """后台线程：参考图传 TOS → 传 ComfyUI → 逐项提交工作流（不同 seed）→ 轮询到完成"""
    from services import tos_service

    # 主参考图必须是清晰的那张：产品库首图常是 75px 缩略图，会让看图分析严重失真
    images = _order_images_by_clarity(images)

    # 中转站通道走独立的并行实现（无 ComfyUI 队列、无 PE 节点）
    if (provider or "local") == "relay":
        _prepare_and_run_relay(
            task_id, images=images, gen_plan=gen_plan,
            product_info=product_info, extra_prompt=extra_prompt,
            brand_genes=brand_genes, product_name=product_name,
            layouts=layouts,
            brand_name=brand_name,
        )
        return

    # 本地通道要占 ComfyUI 串行队列：登记为「生图优先」，超分/视频入队前会让路
    # （relay 通道在上面已 return，不占 ComfyUI，无需登记）
    from services import comfy_priority

    comfy_priority.begin_image_task(task_id)

    items: List[Dict[str, Any]] = []
    prompt_ids: List[str] = []
    try:
        with _lock:
            _running.add(task_id)

        # 0. ComfyUI 配置守卫
        if not (settings.H3_BASE_URL or "").strip():
            _update_task(task_id, status="失败",
                         error_message="H3_BASE_URL 未配置（.env 需配置 ComfyUI 服务地址/账号密码）",
                         finished_at=datetime.now())
            # 释放槽位：队列里的下一单继续处理（配置缺失会让它同样快速失败）
            _release_task_slot(task_id)
            return

        # 1+2. 参考图并行上传：TOS（供前端展示）与 ComfyUI（生成用）同时进行
        t_upload = time.time()

        def _to_tos(item: Tuple[str, bytes]) -> Optional[str]:
            name, content = item
            try:
                return tos_service.upload_file(content, name, subdir="ai-image")
            except Exception as e:
                logger.warning(f"[AI生图] 参考图上传 TOS 失败（不影响生成）: {e}")
                return None

        def _to_comfy(item) -> str:
            idx, (_, content) = item
            return _upload_image(idx, content)

        with ThreadPoolExecutor(max_workers=min(10, max(2, len(images) * 2))) as ex:
            tos_futs = [ex.submit(_to_tos, it) for it in images]
            comfy_futs = [ex.submit(_to_comfy, it) for it in enumerate(images)]
            urls: List[str] = []
            for f in tos_futs:
                u = f.result()
                if u:
                    urls.append(u)
            image_names = [f.result() for f in comfy_futs]
        if urls:
            _update_task(task_id, images=json.dumps(urls, ensure_ascii=False))
        t_upload_done = time.time()

        # 2.5 A+ 版式图入 ComfyUI（只有「用户上传的版式」有图；内置版式靠 structure 文本）
        #     一个任务里多个上传版式各占 1 个通道，但每张图只带自己那一个，所以总数仍受 8 限制。
        layout_names: Dict[str, str] = {}
        # 只有「无结构文字」的版式才把图送进 PE：有 structure 的版式送图实测会导致
        # PE 把版式成稿里的商品/文字当成画面内容（商品被替换 + 无文字模式失效）
        _layout_imgs = [l for l in (layouts or [])
                        if _layout_has_image(l) and not (l.get("structure") or "").strip()]
        if _layout_imgs:
            _base = len(images)

            def _push_layout(arg: Tuple[int, Dict[str, Any]]) -> Tuple[str, Optional[str]]:
                i, lay = arg
                try:
                    data = _fetch_url_bytes(lay["image_url"])
                    name = _upload_image(_base + 100 + i, data)
                    return lay.get("key") or "", name
                except Exception as e:
                    logger.warning(f"[AI生图] 任务 {task_id} 版式图上传失败（该张将只用文字版式）: {e}")
                    return lay.get("key") or "", None

            with ThreadPoolExecutor(max_workers=min(4, len(_layout_imgs))) as ex:
                for key, name in ex.map(_push_layout, list(enumerate(_layout_imgs))):
                    if key and name:
                        layout_names[key] = name

        # 3. 按「类型 × 张数」逐项提交工作流，不同 seed；先全部提交后统一轮询
        multi_types = _multi_shot_types(gen_plan)
        # A+ 提示词通道实验：仅 A+ 子任务走专用工作流，主副图保持原链路
        aplus_mode = (getattr(settings, "IMAGE_APLUS_PROMPT_MODE", "") or "").strip().lower()
        aplus_wf_path = _resolve_aplus_workflow()
        for (t, gen_ratio, target_size, label, seq, no_text, mp) in gen_plan:
            is_banner = t == "aplus_banner"
            if is_banner:
                # 品牌横幅：571 只设计「只有品牌名的底图」，品牌名同时进 571（让 PE 使用真名）
                # 和 572 后置合约（逐字渲染）；参考图只进 PE 提取风格，497 不接图像条件。
                prompt_text = _build_prompt(
                    t, product_info, extra_prompt, brand_genes,
                    multi_shot=(t in multi_types), no_text=True, fact_card=fact_card,
                    brand_name=brand_name,
                )
            else:
                prompt_text = _build_prompt(t, product_info, extra_prompt, brand_genes,
                                            multi_shot=(t in multi_types), no_text=no_text,
                                            fact_card=fact_card)
            item_images = list(image_names)
            lay = (None if is_banner else (
                layouts[seq] if (layouts and t.startswith("aplus") and seq < len(layouts))
                else None
            ))
            if lay:
                from services import ai_image_layout_service as layout_svc
                ref_name = layout_names.get(lay.get("key") or "")
                if lay.get("structure"):
                    # 版式版正文：构图完全交给版式分区。原 aplus 模板的
                    # 「一侧留三分之一空域 / 不得出现分栏」与多分区版式直接矛盾，不能叠加。
                    prompt_text = _build_prompt(
                        t, product_info, extra_prompt, brand_genes,
                        multi_shot=(t in multi_types), no_text=no_text,
                        layout_structure=lay["structure"],
                        layout_with_image=bool(ref_name),
                        fact_card=fact_card,
                    )
                else:
                    prompt_text += layout_svc.layout_clause(
                        lay, no_text=no_text,
                        ref_index=(len(item_images) + 1) if ref_name else None,
                    )
                if ref_name:
                    item_images.append(ref_name)
            wf_path = None
            contract = None
            encode_images = True
            if is_banner:
                # 专用工作流：固定 1888×576 latent（约 3.28:1），后处理裁到 970×300；
                # 参考图只进 PE，497 不接图。品牌名已进 571，不再使用 572 后置合约
                # （2026-11-02 A/B 实测后去除）。
                wf_path = settings.IMAGE_BRAND_BANNER_WORKFLOW_PATH
                encode_images = False
            elif t.startswith("aplus") and aplus_wf_path:
                if aplus_mode == "append":
                    # P4.1：只读创建任务时的模板契约快照；模板后续被修改不影响本任务
                    contract = (lay.get("_snapshot") or {}).get("layout_contract") or None
                    if contract is None:
                        contract = _layout_contract(lay, t, no_text)
                    if contract is None:
                        # 版式连结构文字都没有：契约无从生成，回退旧链路而不是空转
                        logger.warning(
                            f"[AI生图] 任务 {task_id} A+ 子项 {t}#{seq} 无版式结构，"
                            f"append 契约不可用，回退默认工作流"
                        )
                    else:
                        wf_path = aplus_wf_path
                else:
                    wf_path = aplus_wf_path
            seed = random.randint(1, 2**31 - 1)
            workflow = _build_workflow(
                prompt=prompt_text,
                aspect_ratio=gen_ratio,
                seed=seed,
                image_names=item_images,
                filename_prefix=f"AIIMG-{task_id}-{t}-{seq}",
                megapixels=mp,
                workflow_path=wf_path,
                layout_contract=contract,
                encode_images=encode_images,
            )
            _wait_comfy_queue_slot(task_id)
            resp = _api("POST", "/prompt", json={"prompt": workflow})
            pid = resp.json()["prompt_id"]
            prompt_ids.append(pid)
            item = {
                "type": t, "label": label, "seq": seq,
                "status": "排队中", "url": None, "error": None,
                "target_size": list(target_size) if target_size else None,
                "no_text": no_text,
                "prompt_id": pid,
                # 实际提交给 ComfyUI 571 节点的完整提示词（详情弹窗直接读库，不再依赖 /history）
                "final_prompt": prompt_text,
                # 本地链路暂无差异轴（只有中转站的 scene_person 走 PERSON_VARIATIONS）；
                # 这里曾引用未定义的 shot_variation，导致重启后本地生图 100% NameError。
                "variation": None,
            }
            if is_banner:
                item["brand_name"] = brand_name
            if lay:
                # P4.1：沿用创建任务时的完整快照（含 zones/contract/mode/version），
                # 不能在生成阶段用当前模板重新拼一份，否则模板更新会污染在途任务。
                snap = dict(lay.get("_snapshot") or {})
                if not snap:
                    snap = {
                        "key": lay.get("key"), "name": lay.get("name"),
                        "source": lay.get("source"), "ratio_tag": lay.get("ratio_tag"),
                        "image_url": lay.get("image_url") or "",
                        "structure": lay.get("structure") or "",
                        "zones": lay.get("zones") or [],
                    }
                item["layout"] = snap
            items.append(item)

        t_submit_done = time.time()
        _update_task(
            task_id,
            comfy_prompt_ids=json.dumps(prompt_ids, ensure_ascii=False),
            result_images=_dump_items(items),
        )
        logger.info(
            f"[AI生图] 任务 {task_id} 已提交 {len(prompt_ids)} 个子任务 | "
            f"参考图上传(并行) {t_upload_done - t_upload:.1f}s | "
            f"工作流提交 {t_submit_done - t_upload_done:.1f}s"
        )

        # 4. 同线程继续轮询直到完成（成功后 finally 里清除 _running）
        _run_task(task_id, prompt_ids)
    except Exception as e:
        logger.error(f"[AI生图] 任务 {task_id} 准备阶段异常: {e}")
        msg = str(e)[:900]

        # 已成功提交的子任务若丢弃，ComfyUI 会继续空跑且无人回收 → 回写后照常轮询
        if prompt_ids:
            for (rt, _rr, rts, rlb, rsq, _rnt, _rmp) in gen_plan[len(items):]:
                items.append({
                    "type": rt, "label": rlb, "seq": rsq,
                    "status": "失败", "url": None,
                    "error": f"提交中断：{msg}",
                    "target_size": list(rts) if rts else None,
                    "no_text": _rnt,
                })
            _update_task(
                task_id,
                comfy_prompt_ids=json.dumps(prompt_ids, ensure_ascii=False),
                result_images=_dump_items(items),
                error_message=msg,
            )
            logger.warning(
                f"[AI生图] 任务 {task_id} 提交中断，已回写 {len(prompt_ids)} 个已提交子任务并继续回收"
            )
            try:
                _run_task(task_id, prompt_ids)
            except Exception as poll_err:
                logger.error(f"[AI生图] 任务 {task_id} 中断后回收失败: {poll_err}")
        else:
            # 一个子任务都没提交成功（如参考图上传 404）：子项必须一起落终态，
            # 否则会永远停在「排队中」——前端不轮询、详情转圈、重试按钮不出现。
            pending_items = [
                i for i, it in enumerate(items)
                if it.get("status") not in ("已完成", "失败", "已取消")
            ]
            for i in pending_items:
                items[i] = dict(items[i], status="失败", error=f"提交失败：{msg}")
            _update_task(task_id, status="失败", error_message=msg,
                         result_images=_dump_items(items),
                         finished_at=datetime.now())
            if pending_items:
                logger.warning(
                    f"[AI生图] 任务 {task_id} 未提交任何子任务，"
                    f"{len(pending_items)} 个子项一并标记失败"
                )
            # 没有任何子任务可回收：直接释放槽位并启动下一单
            _release_task_slot(task_id)
    finally:
        # 无论提交成功/中断，都要注销优先级登记，否则超分/视频会一直让路
        try:
            from services import comfy_priority

            comfy_priority.end_image_task(task_id)
        except Exception:
            pass


def _serialize(task) -> Dict[str, Any]:
    """任务记录 → 前端结构"""
    if not task:
        return {}
    result_images = _read_json_list(task.result_images)
    images = _read_json_list(task.images)
    cost_seconds = None
    if task.finished_at:
        start = task.started_at or task.created_at
        if start:
            try:
                cost_seconds = int((task.finished_at - start).total_seconds())
            except Exception:
                cost_seconds = None
    return {
        "id": task.id,
        "title": task.title or "商品套图任务",
        "status": task.status,
        "provider": getattr(task, "provider", None) or "local",
        "provider_label": (
            (settings.IMAGE_RELAY_LABEL or "Nano Banana 2")
            if (getattr(task, "provider", None) or "local") == "relay"
            else "Qwen Image 2.1"
        ),
        "creator_name": task.creator_name or "",
        "generation_types": _read_json_list(task.generation_types) if task.generation_types else [],
        "aspect_ratio": task.aspect_ratio,
        "aplus_type": task.aplus_type,
        "main_types": _read_json_list(task.main_types) if task.main_types else [],
        "main_aspect_ratio": task.main_aspect_ratio,
        "brand_settings": (json.loads(task.brand_settings) if task.brand_settings else None),
        "image_count": task.image_count,
        "product_info": task.product_info or "",
        "prompt": task.prompt or "",
        "product_code": task.product_code or "",
        "product_name": task.product_name or "",
        "images": images,
        "result_images": result_images,
        "comfy_prompt_ids": _read_json_list(task.comfy_prompt_ids),
        "cost_seconds": cost_seconds,
        "error_message": task.error_message,
        "created_at": task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "",
        # 进行中任务的排队排位（已完成/失败为 None）：排队中到底在等什么、前面还有多少
        "queue": _queue_info(task),
    }


def get_comfy_prompt_history(prompt_id: str) -> Dict[str, Any]:
    """按 prompt_id 查 ComfyUI 执行历史，提取两级提示词（生图详情弹窗用）：
    - 原始提示词：提交工作流 PrimitiveStringMultiline(571) 的 value（后端拼接后的完整原文）
    - 优化后提示词：执行输出 TE_text_display(569) 的 text（本地官方 PE 增强后的英文）
    """
    pid = (prompt_id or "").strip()
    if not (settings.H3_BASE_URL or "").strip():
        raise AIImageError("H3_BASE_URL 未配置")
    if not pid:
        raise AIImageError("prompt_id 不能为空")
    try:
        data = _api("GET", f"/history/{pid}", timeout=20).json()
    except AIImageError:
        raise
    except Exception as e:
        raise AIImageError(f"查询 ComfyUI 历史失败: {e}")

    entry = data.get(pid) or {}

    original = ""
    prompt_part = entry.get("prompt")
    # ComfyUI 各版本 history.prompt 结构不一：工作流 dict 可能在 prompt[1] 或 prompt[2]，
    # 其余元素可能为 int/str —— 统一遍历取第一个包含提示词节点的 dict
    workflow = None
    if isinstance(prompt_part, dict):
        workflow = prompt_part
    elif isinstance(prompt_part, list):
        for part in prompt_part:
            if isinstance(part, dict) and NODE_PROMPT in part:
                workflow = part
                break
    if workflow:
        node = workflow.get(NODE_PROMPT) or {}
        original = (node.get("inputs") or {}).get("value") or ""

    enhanced = ""
    node_out = (entry.get("outputs") or {}).get("569") or {}
    texts = node_out.get("text") or []
    if texts:
        enhanced = texts[0] or ""

    return {
        "prompt_id": pid,
        "found": bool(entry),
        "original_prompt": original,
        "enhanced_prompt": enhanced,
    }


def list_image_tasks(
    tenant_id: int, page: int = 1, page_size: int = 10, keyword: str = "",
    status: str = "",
) -> Dict[str, Any]:
    """租户的套图生成任务列表（按时间倒序）。keyword 模糊匹配产品编码/品名/标题"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask
    from sqlalchemy import or_

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    db = SessionLocal()
    try:
        query = db.query(AIImageTask).filter(
            AIImageTask.tenant_id == tenant_id,
            AIImageTask.deleted_at.is_(None),
        )
        status = (status or "").strip()
        if status:
            # 白名单：直接拼进 WHERE 前先限定为已知状态值（须与前端下拉/STATUS_COLORS 完全对齐）
            if status not in ("排队中", "生成中", "已完成", "部分完成", "失败", "已取消"):
                raise AIImageError(f"不支持的任务状态: {status}")
            query = query.filter(AIImageTask.status == status)
        keyword = (keyword or "").strip()
        if keyword:
            # 转义 LIKE 通配符：用户输入 % 或 _ 时按字面匹配，否则 % 会命中全部记录
            escaped = (
                keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            like = f"%{escaped}%"
            query = query.filter(
                or_(
                    AIImageTask.product_code.ilike(like, escape="\\"),
                    AIImageTask.product_name.ilike(like, escape="\\"),
                    AIImageTask.title.ilike(like, escape="\\"),
                )
            )
        total = query.count()
        rows = (
            query.order_by(AIImageTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {"items": [_serialize(r) for r in rows], "total": total}
    finally:
        db.close()


def get_image_task(tenant_id: int, task_id: int) -> Dict[str, Any]:
    """单任务详情（带租户隔离）"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        row = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.tenant_id == tenant_id,
                AIImageTask.deleted_at.is_(None),
            )
            .first()
        )
        if not row:
            raise AIImageError("任务不存在或已删除")
        return _serialize(row)
    finally:
        db.close()


def prompt_belongs_to_tenant(tenant_id: int, prompt_id: str) -> bool:
    """ComfyUI prompt_id 是否属于该租户的任务（详情弹窗查历史前的归属校验）"""
    import re

    pid = (prompt_id or "").strip()
    # 白名单字符 + 长度，避免 LIKE 通配符注入（_ / % 会被当哨兵）
    if not re.fullmatch(r"[A-Za-z0-9\-]{8,64}", pid):
        return False

    _ensure_models_loaded()
    from sqlalchemy import or_
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        hit = (
            db.query(AIImageTask.id)
            .filter(
                AIImageTask.tenant_id == tenant_id,
                # 重试生成的 pid 只落在子项里（任务级列表按下标同步失败/旧数据），
                # 两处都要查，否则重试子项的历史提示词一律 404。
                or_(
                    AIImageTask.comfy_prompt_ids.like(f"%{pid}%"),
                    AIImageTask.result_images.like(f"%{pid}%"),
                ),
            )
            .first()
        )
        return hit is not None
    finally:
        db.close()


def _delete_task_tos_objects(task_id: int, urls: List[str]) -> None:
    """后台清理任务图片（TOS 删除是网络调用，不能阻塞删除接口）"""
    if not urls:
        return
    from services import tos_service

    removed = 0
    failed = 0
    for url in dict.fromkeys(urls):
        try:
            if tos_service.delete_file(url):
                removed += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[AI生图] 任务 {task_id} TOS 清理失败: {url} ({e})")
    logger.info(f"[AI生图] 任务 {task_id} TOS 清理完成：删除 {removed} 个，跳过/失败 {failed} 个")


def delete_image_task(tenant_id: int, task_id: int) -> None:
    """删除任务：软删数据库记录 + 清理该任务在 TOS 的参考图与生成图"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    urls: List[str] = []
    db = SessionLocal()
    try:
        row = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.tenant_id == tenant_id,
            )
            .first()
        )
        if row:
            # 生成中的任务仍在往 TOS 上传，先删会留下无人回收的孤儿对象
            if row.status in ("排队中", "生成中"):
                raise AIImageError("任务仍在生成中，请先取消后再删除")
            # 生成图 + 参考图都归本任务所有（上传时使用 UUID 命名，不会与其他任务共享）
            items = _read_json_list(row.result_images)
            urls = [it.get("url") for it in items if isinstance(it, dict) and it.get("url")]
            urls.extend(_read_json_list(row.images))
        db.query(AIImageTask).filter(
            AIImageTask.id == task_id, AIImageTask.tenant_id == tenant_id
        ).update({"deleted_at": datetime.now()})
        db.commit()
    finally:
        db.close()

    if urls:
        threading.Thread(
            target=_delete_task_tos_objects, args=(task_id, urls),
            daemon=True, name=f"ai-image-tos-cleanup-{task_id}",
        ).start()


def cancel_image_task(tenant_id: int, task_id: int) -> Dict[str, Any]:
    """取消排队中/生成中的任务：标记取消 + 清 ComfyUI 待执行队列。

    已在 KSampler 里执行的那一张无法精准中断（/interrupt 是全局的，会波及生视频），
    因此只放弃回收，等它自然结束。
    """
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        task = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.tenant_id == tenant_id,
                AIImageTask.deleted_at.is_(None),
            )
            .first()
        )
        if not task:
            raise AIImageError("任务不存在或已删除")
        if task.status not in ("排队中", "生成中"):
            raise AIImageError("只有排队中/生成中的任务可以取消")

        pids = _read_json_list(task.comfy_prompt_ids)
        with _lock:
            _cancel_flags.add(task_id)
            alive = task_id in _running

        for pid in pids:
            try:
                _api("POST", "/queue", json={"delete": [pid]}, timeout=10)
            except Exception as e:
                logger.warning(f"[AI生图] 任务 {task_id} 移除 ComfyUI 队列项失败: {e}")

        # 无活跃线程（服务重启过）：直接落终态，避免永远停在生成中。
        # 中转站任务没有 ComfyUI 队列项可删（pids 为空），线程也无法中断 HTTP 调用，
        # 因此同样先落终态；线程收尾时会读到取消标志并把未完成项写成「已取消」。
        if not alive or not pids:
            items = _read_json_list(task.result_images)
            for it in items:
                if it.get("status") not in ("已完成", "失败"):
                    it["status"] = "已取消"
                    it["error"] = "任务已取消"
            db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
                "result_images": _dump_items(items),
                "status": _aggregate_status(items),
                "finished_at": datetime.now(),
            })
            db.commit()
            if not alive:
                _clear_cancel(task_id)
        return {"success": True, "message": "已取消"}
    finally:
        db.close()


def _item_layout(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从子项里取回当初用的版式（重试/回收时复现同一版式，不能丢）"""
    lay = it.get("layout")
    return lay if isinstance(lay, dict) and lay.get("key") else None


def _layout_has_image(lay: Optional[Dict[str, Any]]) -> bool:
    """该版式是否带参考图：上传素材与官方模板都有，内置结构版式只有文字分区"""
    return bool(lay) and lay.get("source") in ("upload", "official") and bool(lay.get("image_url"))


def _retry_relay_items(
    task_id: int,
    snapshot: List[Tuple[int, Dict[str, Any]]],
    datas: List[Tuple[str, bytes]],
    product_info: str,
    extra_prompt: str,
    brand_genes: str,
    main_ratio: str,
) -> Dict[str, Any]:
    """中转站任务的重试：先按类型重设计提示词，再后台并行重生成（不阻塞 HTTP 请求）"""
    plan: List[Tuple[int, str, Optional[Tuple[int, int]], str, str, bool]] = []
    skipped: List[str] = []

    # 重试与首次生成保持同一套口径：scene_person 逐张设计（各自 variation），其余按类型复用
    ratio_by_pos: Dict[int, str] = {}
    for pos, (_idx, it) in enumerate(snapshot):
        _t = it.get("type") or ""
        if _t in APLUS_SPECS:
            ratio_by_pos[pos] = APLUS_SPECS[_t]["gen_ratio"]
        elif _t in MAIN_GEN_TYPES:
            ratio_by_pos[pos] = main_ratio

    design_jobs: List[Tuple[int, str, str, int]] = []   # (snapshot 位置, 类型, 比例, seq)
    seen_types: set = set()
    for pos, (_idx, it) in enumerate(snapshot):
        t = it.get("type") or ""
        ratio = ratio_by_pos.get(pos)
        if not ratio:
            continue
        seq = int(it.get("seq") or 0)
        # A+ 也逐张设计：重试常只挑其中一两张，各张版式不同，复用一份会串版式
        if t == "scene_person" or t.startswith("aplus"):
            design_jobs.append((pos, t, ratio, seq))
        elif t not in seen_types:
            seen_types.add(t)
            design_jobs.append((pos, t, ratio, 0))

    designs_by_pos: Dict[int, Dict[str, Any]] = {}
    designs_by_type: Dict[str, Dict[str, Any]] = {}
    if bool(settings.IMAGE_RELAY_ANALYZE) and design_jobs:
        from services import relay_image_service as relay

        prepared = relay.prepare_reference(datas[0][1])

        def _design(job: Tuple[int, str, str, int]) -> Tuple[Tuple[int, str, str, int], Dict[str, Any]]:
            pos, t, ratio, seq = job
            variation = (
                relay.PERSON_VARIATIONS[seq % len(relay.PERSON_VARIATIONS)]
                if t == "scene_person" else None
            )
            lay = _item_layout(snapshot[pos][1])
            lay_bytes: Optional[bytes] = None
            if _layout_has_image(lay):
                try:
                    lay_bytes = _fetch_url_bytes(lay["image_url"])
                except Exception as e:
                    logger.warning(f"[AI生图] 任务 {task_id} 重试版式图取回失败: {e}")
            # 与首次生成同一套清洗：重试也必须把「标题/文案/文字区」改写成留白
            retry_hint = ((lay.get("structure") or "") if lay else "")
            if retry_hint and t.startswith("aplus") and bool(snapshot[pos][1].get("no_text")):
                retry_hint = _sanitize_structure_no_text(retry_hint)
            try:
                return job, relay.design_shot_prompt(
                    datas[0][1], gen_type=t, ratio=ratio,
                    product_info=product_info, extra=extra_prompt,
                    brand_genes=brand_genes, prepared=prepared,
                    variation=variation,
                    layout_bytes=lay_bytes,
                    layout_hint=retry_hint,
                )
            except Exception as e:
                logger.warning(
                    f"[AI生图] 任务 {task_id} 重试时类型 {t}#{seq} 设计失败，回退模板: {e}"
                )
                return job, {}

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(design_jobs)))) as ex:
            for (pos, t, _r, _s), d in ex.map(_design, design_jobs):
                if not d:
                    continue
                if t == "scene_person" or t.startswith("aplus"):
                    designs_by_pos[pos] = d
                elif t not in designs_by_type:
                    designs_by_type[t] = d

    # 本次重试里哪些类型有多张（只有它们需要“彼此不同”的提示）
    _retry_multi_types = _multi_shot_types([(it.get("type") or "",) for _i, it in snapshot])

    for pos, (_idx, it) in enumerate(snapshot):
        idx = _idx
        t = it.get("type") or ""
        if t in APLUS_SPECS:
            ratio = APLUS_SPECS[t]["gen_ratio"]
            target: Optional[Tuple[int, int]] = tuple(APLUS_SPECS[t]["target_size"])
        elif t in MAIN_GEN_TYPES:
            ratio = main_ratio
            ts = it.get("target_size")
            target = tuple(ts) if isinstance(ts, list) and len(ts) == 2 else None
        else:
            skipped.append(f"{t} 类型已不支持")
            continue
        d = designs_by_pos.get(pos) or designs_by_type.get(t) or {}
        designed = d.get("prompt_en")
        prompt_cn = d.get("prompt_cn") or ""
        if designed:
            prompt_text = designed
            neg = d.get("negative") or ""
            if neg:
                prompt_text += f"\n\nAvoid: {neg}"
        else:
            lay = _item_layout(it)
            has_struct = bool(lay and lay.get("structure"))
            prompt_text = _build_prompt(
                t, product_info, extra_prompt, brand_genes,
                multi_shot=(t in _retry_multi_types),
                no_text=bool(it.get("no_text")),
                layout_structure=(lay.get("structure") if has_struct else ""),
                layout_with_image=bool(lay and _layout_has_image(lay)),
            )
            if lay and not has_struct:
                from services import ai_image_layout_service as layout_svc
                prompt_text += layout_svc.layout_clause(lay, no_text=bool(it.get("no_text")))
        prompt_text += f"\n\n画面比例 {ratio}。只输出生成后的图片，不要输出任何文字说明。"
        if t != "selling_point":
            prompt_text += "\n画面中不得出现任何文字、标签、价签、水印或标识。"
        if it.get("no_text"):
            prompt_text += "\n画面中不要出现任何文字、标语、水印或标识。"
        plan.append((idx, prompt_text, target, t, prompt_cn, bool(designed)))

    if not plan:
        raise AIImageError("重试提交失败：" + "；".join(skipped[:3] or ["未知原因"]))

    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    # 先落库：目标子项回到排队中、任务回到生成中，前端立刻能轮询到进度
    db = SessionLocal()
    try:
        task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
        items = _read_json_list(task.result_images if task else None)
        for idx, _p, _t, _ty, _cn, _ds in plan:
            if idx < len(items):
                items[idx] = dict(
                    items[idx], status="排队中", error=None, url=None,
                    final_prompt=_p,
                    prompt_cn=_cn or None,
                    prompt_designed=_ds,
                    retried_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                )
        db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
            "result_images": _dump_items(items),
            "status": "生成中",
            "finished_at": None,
        })
        db.commit()
    finally:
        db.close()

    _clear_cancel(task_id)
    token = _new_run_token(task_id)
    # 执行槽已由 retry_image_items 抢占（同一任务禁止并行重试）；线程在 finally 释放
    threading.Thread(
        target=_run_relay_retry, args=(task_id, plan, datas[0][1], token),
        daemon=True, name=f"ai-image-retry-relay-{task_id}",
    ).start()
    return {"retried": len(plan), "failed": skipped}


def _run_relay_retry(
    task_id: int,
    plan: List[Tuple[int, str, Optional[Tuple[int, int]], str, str, bool]],
    main_bytes: bytes,
    token: int,
) -> None:
    """后台线程：中转站重试生成 → 裁剪/转 JPEG → TOS → 回写子项"""
    from services import relay_image_service as relay
    from services import tos_service
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    try:
        results = relay.generate_batch(main_bytes, [p for _i, p, _t, _ty, _cn, _ds in plan])
        if not _is_current_token(task_id, token):
            logger.info(f"[AI生图] 任务 {task_id} 已有更新的执行，丢弃本次中转站重试结果")
            return
        db = SessionLocal()
        try:
            task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
            items = _read_json_list(task.result_images if task else None)
            for (idx, _p, target, t, _cn, _ds), r in zip(plan, results):
                if idx >= len(items):
                    continue
                if not r.get("ok"):
                    items[idx] = dict(
                        items[idx], status="失败", url=None,
                        error=(r.get("error") or "生成失败")[:300],
                    )
                    continue
                try:
                    content = r["data"]
                    if target:
                        content = _fit_to_target(content, target)
                    content, fname = _to_jpeg(content, f"relay-{task_id}-{t}-{idx}-retry.png")
                    url = tos_service.upload_file(
                        content, fname, content_type="image/jpeg", subdir="ai-image",
                    )
                    items[idx] = dict(items[idx], status="已完成", url=url, error=None)
                except Exception as e:
                    items[idx] = dict(items[idx], status="失败", error=str(e)[:300])
            db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
                "result_images": _dump_items(items),
                "status": _finalize_status(task_id, _aggregate_status(items)),
                "finished_at": datetime.now(),
            })
            db.commit()
        finally:
            db.close()
        logger.info(f"[AI生图] 任务 {task_id} 中转站重试完成 {len(plan)} 项")
    except Exception as e:
        logger.exception(f"[AI生图] 任务 {task_id} 中转站重试异常")
        _update_task(task_id, status="失败", error_message=str(e)[:900],
                     finished_at=datetime.now())
    finally:
        _release_task_slot(task_id)
        _clear_cancel(task_id)


def retry_image_items(tenant_id: int, task_id: int,
                      indices: Optional[List[int]] = None) -> Dict[str, Any]:
    """重试失败子项（复用已存参考图，不要求用户重新上传）。

    :param indices: 指定子项下标；为空则重试全部失败项
    """
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        task = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.tenant_id == tenant_id,
                AIImageTask.deleted_at.is_(None),
            )
            .first()
        )
        if not task:
            raise AIImageError("任务不存在或已删除")
        if task.status in ("排队中", "生成中"):
            raise AIImageError("任务仍在生成中，请等它结束或先取消")

        items = _read_json_list(task.result_images)
        # 可重试范围：失败 + 已取消（取消后想补齐是常见诉求，前端按钮口径与此一致）
        targets = _pick_retry_targets(items, indices, ("失败", "已取消"))
        if not targets:
            raise AIImageError("没有可重试的失败或已取消图片")

        image_urls = _read_json_list(task.images)
        if not image_urls:
            raise AIImageError("参考图已丢失，请重新提交任务")

        product_info = task.product_info or ""
        extra_prompt = task.prompt or ""
        try:
            brand = json.loads(task.brand_settings) if task.brand_settings else None
        except Exception:
            brand = None
        brand_genes = _compile_brand_genes(brand)
        stored_brand_name = str((brand or {}).get("brand_name") or "") if isinstance(brand, dict) else ""
        main_ratio = task.main_aspect_ratio or task.aspect_ratio or "1:1"
        provider = getattr(task, "provider", None) or "local"
        snapshot = [(i, dict(items[i])) for i in targets]
    finally:
        db.close()

    # 抢占执行槽必须在任何子项状态变更之前：否则与上一轮线程的收尾阶段交错时，
    # 新提交的 pid 会无人轮询，任务永远停在「生成中」。
    if not _claim_task_slot(task_id):
        raise AIImageError("任务正在执行中，请稍后再试")

    started = False
    try:
        # 参考图：TOS 下载 → 重新上传 ComfyUI（上传的临时文件不长期保留）
        datas: List[Tuple[str, bytes]] = []
        # 直连：走系统代理时隧道抖动会把下载拖到超时
        _http = requests.Session()
        _http.trust_env = False
        for u in image_urls:
            try:
                resp = _http.get(u, timeout=60)
                resp.raise_for_status()
            except Exception as e:
                raise AIImageError(f"参考图下载失败，请重新提交任务: {e}")
            datas.append((u.rsplit("/", 1)[-1] or "ref.jpg", resp.content))

        # 主参考图必须是清晰的那张（与首次生成同口径，否则重试结果会再次跑偏）
        datas = _order_images_by_clarity(datas)

        # 中转站任务必须回中转站重试：换成本地 ComfyUI 会换模型、换风格，还要抢串行队列
        if provider == "relay":
            result = _retry_relay_items(
                task_id, snapshot, datas, product_info, extra_prompt, brand_genes, main_ratio,
            )
            started = True
            return result

        # datas 元素是 (filename, bytes)，上传只需要 bytes（勿把元组当内容传进去）
        image_names = [_upload_image(i, _c) for i, (_n, _c) in enumerate(datas)]

        # A+ 提示词通道实验：重试与首次同口径（否则重试会静默退回旧链路）
        aplus_mode = (getattr(settings, "IMAGE_APLUS_PROMPT_MODE", "") or "").strip().lower()
        aplus_wf_path = _resolve_aplus_workflow()

        prompt_map: Dict[str, int] = {}
        prompt_text_map: Dict[int, str] = {}
        failed: List[str] = []
        for idx, it in snapshot:
            t = it.get("type") or ""
            if t in APLUS_SPECS:
                ratio = APLUS_SPECS[t]["gen_ratio"]
                mp = float(APLUS_SPECS[t].get("megapixels") or DEFAULT_MEGAPIXELS)
            elif t in MAIN_GEN_TYPES:
                ratio = main_ratio
                mp = DEFAULT_MEGAPIXELS
            else:
                failed.append(f"{t} 类型已不支持")
                continue
            is_banner = t == "aplus_banner"
            banner_brand = ""
            if is_banner:
                # 品牌名优先取子项快照（创建任务时固化），其次回退 brand_settings
                banner_brand = _normalize_brand_name(it.get("brand_name") or stored_brand_name)
                if not banner_brand:
                    failed.append(f"{t} 品牌名已丢失，请重新提交任务")
                    continue
                prompt_text = _build_prompt(t, product_info, extra_prompt, brand_genes,
                                            no_text=True, brand_name=banner_brand)
            else:
                prompt_text = _build_prompt(t, product_info, extra_prompt, brand_genes,
                                            no_text=bool(it.get("no_text")))
            # 版式必须复现：重试单独一张时也要用当初那个版式，否则重试结果和其余张版式不一致
            use_names = image_names
            lay = None if is_banner else _item_layout(it)
            if lay and lay.get("structure"):
                # 版式版正文：重新构建，替换掉自由构图条款
                prompt_text = _build_prompt(t, product_info, extra_prompt, brand_genes,
                                            no_text=bool(it.get("no_text")),
                                            layout_structure=lay["structure"],
                                            layout_with_image=_layout_has_image(lay))
            else:
                prompt_text = _build_prompt(t, product_info, extra_prompt, brand_genes,
                                            no_text=bool(it.get("no_text")))
            if lay:
                from services import ai_image_layout_service as layout_svc
                ref_name: Optional[str] = None
                if _layout_has_image(lay) and not (lay.get("structure") or "").strip():
                    try:
                        ref_name = _upload_image(900 + idx, _fetch_url_bytes(lay["image_url"]))
                    except Exception as e:
                        logger.warning(f"[AI生图] 任务 {task_id} 重试版式图上传失败: {e}")
                if not (lay.get("structure")):
                    prompt_text += layout_svc.layout_clause(
                        lay, no_text=bool(it.get("no_text")),
                        ref_index=(len(image_names) + 1) if ref_name else None,
                    )
                if ref_name:
                    use_names = image_names + [ref_name]
            wf_path = None
            contract = None
            encode_images = True
            if is_banner:
                wf_path = settings.IMAGE_BRAND_BANNER_WORKFLOW_PATH
                encode_images = False
            elif t.startswith("aplus") and aplus_wf_path:
                if aplus_mode == "append":
                    # P4.1：重试必须复现创建任务时的契约，不得按当前模板重新生成
                    contract = lay.get("layout_contract") or _layout_contract(lay, t, bool(it.get("no_text")))
                    if contract is not None:
                        wf_path = aplus_wf_path
                    else:
                        logger.warning(
                            f"[AI生图] 任务 {task_id} 重试子项 {t}#{it.get('seq', idx)} "
                            f"无版式结构，append 契约不可用，回退默认工作流"
                        )
                else:
                    wf_path = aplus_wf_path
            try:
                wf = _build_workflow(
                    prompt=prompt_text,
                    aspect_ratio=ratio,
                    seed=random.randint(1, 2**31 - 1),
                    image_names=use_names,
                    filename_prefix=f"AIIMG-{task_id}-{t}-{it.get('seq', idx)}-retry",
                    megapixels=mp,
                    workflow_path=wf_path,
                    layout_contract=contract,
                    encode_images=encode_images,
                )
                _wait_comfy_queue_slot(task_id)
                resp = _api("POST", "/prompt", json={"prompt": wf})
                prompt_map[resp.json()["prompt_id"]] = idx
                prompt_text_map[idx] = prompt_text
            except AIImageError:
                raise
            except Exception as e:
                failed.append(f"{t}: {e}")

        if not prompt_map:
            raise AIImageError("重试提交失败：" + "；".join(failed[:3] or ["未知原因"]))

        # 落库：目标子项回到排队中，任务回到生成中。
        # comfy_prompt_ids 必须按子项下标同步更新：重启恢复只认这份任务级列表，
        # 不同步会导致重试中的子项在重启后无人轮询；详情页的历史提示词归属校验也依赖它。
        db = SessionLocal()
        try:
            task = db.query(AIImageTask).filter(AIImageTask.id == task_id).first()
            if not task:
                raise AIImageError("任务不存在或已删除")
            items = _read_json_list(task.result_images)
            pids = _read_json_list(task.comfy_prompt_ids)
            if not pids:
                pids = [str(it.get("prompt_id") or "") for it in items]
            if len(pids) < len(items):
                pids.extend([""] * (len(items) - len(pids)))
            pids = pids[:len(items)]
            for pid, idx in prompt_map.items():
                if idx < len(items):
                    pids[idx] = pid
                    items[idx] = dict(
                        items[idx], status="排队中", error=None, url=None,
                        prompt_id=pid,
                        final_prompt=prompt_text_map.get(idx),
                        retried_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
            db.query(AIImageTask).filter(AIImageTask.id == task_id).update({
                "result_images": _dump_items(items),
                "comfy_prompt_ids": json.dumps(pids, ensure_ascii=False),
                "status": _aggregate_status(items),
                "finished_at": None,
            })
            db.commit()
        finally:
            db.close()

        _clear_cancel(task_id)
        _start_task_thread(task_id, list(prompt_map.keys()), index_map=prompt_map)
        started = True
        return {"retried": len(prompt_map), "failed": failed}
    finally:
        if not started:
            _release_task_slot(task_id)


def build_task_zip(tenant_id: int, task_id: int) -> Tuple[bytes, str]:
    """把任务已生成的图片打包成 zip（服务端拉取，规避逐张点击与跨域）"""
    import re
    import zipfile

    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        task = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.id == task_id,
                AIImageTask.tenant_id == tenant_id,
                AIImageTask.deleted_at.is_(None),
            )
            .first()
        )
        if not task:
            raise AIImageError("任务不存在或已删除")
        items = [
            it for it in _read_json_list(task.result_images)
            if it.get("status") == "已完成" and it.get("url")
        ]
        if not items:
            raise AIImageError("没有可下载的图片")
        raw_title = task.title or f"任务{task_id}"
    finally:
        db.close()

    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", raw_title).strip("_")[:60] or "ai-image"
    buf = io.BytesIO()
    used = set()
    failed: List[str] = []
    first_error = ""
    # 直连取图：走系统代理时隧道抖动会把下载拖到超时（沙箱环境尤其明显）
    http = requests.Session()
    http.trust_env = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            try:
                resp = http.get(it["url"], timeout=120)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[AI生图] 任务 {task_id} 打包下载失败: {e}")
                failed.append(it.get("label") or it.get("type") or "image")
                if not first_error:
                    first_error = str(e)[:120]
                continue
            # 扩展名按实际地址推断：中转站产出 jpg，硬编码 .png 会让文件名与内容不符
            ext = os.path.splitext(urlparse(it["url"]).path)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            name = (
                f"{safe_title}-{it.get('label') or it.get('type') or 'image'}"
                f"-{(it.get('seq') or 0) + 1}{ext}"
            )
            base, n = name, 2
            while name in used:
                name = f"{base[:-len(ext)]}-{n}{ext}"
                n += 1
            used.add(name)
            zf.writestr(name, resp.content)

        # 部分失败时写入清单，避免用户以为图片全都在
        if failed and used:
            zf.writestr(
                "_未下载.txt",
                "以下图片源地址已失效或下载失败，请在页面里重新生成：\n"
                + "\n".join(f"- {x}" for x in failed)
                + (f"\n\n首个错误：{first_error}" if first_error else ""),
            )

    if not used:
        raise AIImageError(
            f"图片下载失败（{len(items)} 张均无法取回）：{first_error or '源地址无响应'}"
        )
    return buf.getvalue(), f"{safe_title}.zip"


def resume_pending_tasks() -> None:
    """服务启动时恢复未完成的任务：重新挂上后台轮询线程"""
    _ensure_models_loaded()
    from database.database import SessionLocal
    from models.ai_image_task import AIImageTask

    db = SessionLocal()
    try:
        rows = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.status.in_(["排队中", "生成中"]),
                AIImageTask.comfy_prompt_ids.isnot(None),
                AIImageTask.deleted_at.is_(None),
            )
            .all()
        )
        pending = [(r.id, r.user_id, _read_json_list(r.comfy_prompt_ids)) for r in rows]

        # 中转站任务的子项在生成前已落库（含提示词），重启后按提示词补跑；
        # 绝不能走下面的「无 ComfyUI 任务 ID → 判失败」分支（那会丢掉远端已出的图）。
        relay_rows = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.status.in_(["排队中", "生成中"]),
                AIImageTask.provider == "relay",
                AIImageTask.deleted_at.is_(None),
            )
            .all()
        )
        relay_owners = {r.id: r.user_id for r in relay_rows}
        relay_ids = set(relay_owners)
        pending = [(tid, uid, pids) for tid, uid, pids in pending if tid not in relay_ids]

        # 未拿到 ComfyUI 任务 ID 的记录已无法追踪，直接标记失败。
        # 必须连子项一起落终态：只改任务级会让子项永远停在「排队中」，
        # 前端就不再轮询、详情弹窗一直转圈，且「重试未完成」按钮也不会出现。
        orphan_rows = (
            db.query(AIImageTask)
            .filter(
                AIImageTask.status.in_(["排队中", "生成中"]),
                AIImageTask.comfy_prompt_ids.is_(None),
                AIImageTask.deleted_at.is_(None),
            )
            .all()
        )
        orphan_rows = [r for r in orphan_rows if r.id not in relay_ids]
        for row in orphan_rows:
            items = _read_json_list(row.result_images)
            touched = False
            for i, it in enumerate(items):
                if it.get("status") not in ("已完成", "失败", "已取消"):
                    items[i] = dict(it, status="失败",
                                    error="服务重启导致任务中断，请重新提交")
                    touched = True
            row.status = "失败"
            row.error_message = "服务重启导致任务中断，请重新提交"
            row.finished_at = datetime.now()
            if touched:
                row.result_images = _dump_items(items)
        db.commit()
        if orphan_rows:
            logger.warning(f"[AI生图] {len(orphan_rows)} 条未提交成功的任务已标记为失败（含子项）")
    finally:
        db.close()

    for task_id, user_id, pids in pending:
        # 按下标建映射：重试可能把某些位置写成空串占位，过滤时不能改变其余项的位置
        index_map = {p: i for i, p in enumerate(pids) if p}
        if index_map:
            logger.info(f"[AI生图] 恢复未完成任务 {task_id}（{len(index_map)} 个子任务）")
            _mark_task_resumed(task_id, user_id)
            _ensure_worker(task_id, list(index_map.keys()), index_map=index_map)

    for task_id in sorted(relay_ids):
        logger.info(f"[AI生图] 恢复中转站任务 {task_id}（按已存提示词补跑）")
        _mark_task_resumed(task_id, relay_owners[task_id])
        if not _ensure_relay_resume_worker(task_id):
            logger.info(f"[AI生图] 任务 {task_id} 已有执行在跑，跳过重启恢复")
