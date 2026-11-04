# -*- coding: utf-8 -*-
"""生图中转站适配层（Nano Banana 2 / gemini-3.1-flash-image）。

与本地 ComfyUI 链路的三个差异，决定了这里的实现方式：

1. **协议**：OpenAI 兼容 `POST /chat/completions`，多模态输入（text + image_url data URI），
   返回的图片藏在 `choices[0].message.content` 的 markdown 里（`![](data:image/jpeg;base64,...)`）。
2. **没有 PE 节点**：本地链路由 Qwen PE 负责「看图 + 优化提示词」，中转站没有，
   所以生成前先做一次多模态「提示词设计」（`design_shot_prompt`）：同时完成
   读商品（含件数清单 inventory）与写这一张图该怎么拍，产出英文提示词直接驱动出图。
   **看图/设计统一走生视频页那套视觉通道**（`OPENAI_API_BASE` / `OPENAI_API_KEY` /
   `OPENAI_VISION_MODEL`），保证两个页面的事实口径一致；出图仍用 `IMAGE_RELAY_MODEL`。
3. **无串行队列**：本地 ComfyUI 一次只能跑一张，中转站是普通 HTTP 接口，可按张并行。

只负责「设计」与「生成」两件事，提示词落库/裁剪由 ai_image_service 负责（避免循环依赖）。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests

from config import get_settings

logger = logging.getLogger(__name__)


class RelayError(Exception):
    """中转站调用失败（对上层暴露可读原因）"""


_session_local = threading.local()


def _session() -> requests.Session:
    sess = getattr(_session_local, "sess", None)
    if sess is None:
        sess = requests.Session()
        sess.trust_env = False      # 直连，绕过本地代理
        _session_local.sess = sess
    return sess


# 图片可能以 markdown 内联 base64 或外链 URL 返回
_MD_BASE64 = re.compile(r"!\[[^\]]*\]\(\s*(data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)\s*\)")
_MD_URL = re.compile(r"!\[[^\]]*\]\(\s*(https?://[^\s)]+)\s*\)")
_BARE_BASE64 = re.compile(r"(data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]{200,})")

# ============================================================
# 按图类型「设计」提示词（不是套模板）
# 与生视频 VOICEOVER_PLAN_SYSTEM_PROMPT 同思路：一次多模态调用
# 同时完成「读商品」与「写这一张图该怎么拍」，输出可直接驱动出图的提示词。
# ============================================================

# 每类图的电商硬规范。全部写成「对参考图做哪一种编辑」，
# 因为 Nano Banana 2 是图像编辑模型：说清"保持什么"比"重画一张"保真得多。
SHOT_SPECS: Dict[str, str] = {
    "white_background": (
        "EDIT: lift the decorations off the cake and stand them on a bare seamless white #FFFFFF "
        "surface. Remove the cake, cake board, table, water, pebbles and every background element. "
        "EVERY inventory piece must still be present, same count, same colors, same shapes, upright "
        "and individually readable. Product fills 80-85% of the frame, front three-quarter view at "
        "natural eye level, even soft box light from both sides, only a soft contact shadow."
    ),
    "scene": (
        "EDIT: keep the product exactly as it is — same pieces, same count, same arrangement — "
        "and replace only the surrounding environment with a believable lifestyle setting where "
        "this product is used. NO people at all: the product is the only subject, with no model, "
        "no hands, no arms and no human silhouette in or out of focus. Props may support the story "
        "but must not hide or upstage any inventory piece. Natural window light, moderate depth of "
        "field, product reads as hero at 40-55% of the frame."
    ),
    "scene_person": (
        "EDIT: keep the product exactly as it is — same pieces, same count, same arrangement — and "
        "place it in a believable lifestyle setting where this product is used, WITH ONE ADULT MODEL "
        "WEARING AND USING IT.\n"
        "PEOPLE RULES (hard, compliance): the person must read clearly as an adult, 25-40 years old, "
        "with mature facial features and adult body proportions. ABSOLUTELY NO children, toddlers, "
        "teenagers or minors of any age, not as the model and not in the background. If the reference "
        "photo contains a child, replace that person with an adult. Never choose a framing that "
        "implies a children's party (no kids' birthday setting, no small chairs/plates sized for "
        "children).\n"
        "USAGE RULES (hard): the model must actually WEAR or USE every inventory piece exactly as the "
        "CONFIRMED PRODUCT CARD's usage line describes (e.g. crown worn on the head, waist chain "
        "fastened around the waist, bracelets on the wrists). The pieces must not merely be held in "
        "the hands and must not be laid out on a surface. Every piece must remain visible and "
        "identifiable.\n"
        "SETTING RULES (avoid the generic interior default): the environment must belong to the "
        "product's own style context and must contain at least TWO recognisable elements (columns, "
        "stone steps, draped fabric, foliage, an arch, a styled table…). A plain bedroom, changing "
        "room, white wall or empty studio is NOT acceptable. Wardrobe must belong to the same style "
        "family and colour range as the product — never modern casual wear next to a period piece.\n"
        "LIGHT RULES: use directional warm light with a clear direction (low sun from the side or "
        "back, rim light on hair and on metal), not flat ambient indoor light. Keep the product's "
        "metal highlights consistent with that light.\n"
        "Framing: follow THIS SHOT when it is supplied; otherwise three-quarter or waist-up so the "
        "worn pieces read clearly. The product stays the hero, every piece stays visible."
    ),
    "closeup": (
        "EDIT: move closer on the single most persuasive craft detail of the product (texture, "
        "stitching, print, finish, material grain). Everything else in the inventory stays present "
        "in frame, just less prominent. Raking light to reveal surface texture, background simple."
    ),
    "closeup_white": (
        "EDIT: same closer macro framing, but isolate it on pure white #FFFFFF — no cake, no board, "
        "no props. All inventory pieces remain present, none may be dropped just because the framing "
        "is tighter. Clean studio light, sharp surface detail."
    ),
    "selling_point": (
        "EDIT: keep the full product on a clean neutral background and leave generous EMPTY negative "
        "space reserved for copy blocks. Render NO text at all; leave blank layout areas for copy."
    ),
    "aplus_advanced": (
        "EDIT: re-frame the same product set into an ultra-wide cinematic 21:9 banner, product "
        "centered with negative space on both sides for copy, subject kept away from the edges "
        "because the frame is center-cropped to 1464x600. All inventory pieces present."
    ),
    "aplus_normal": (
        "EDIT: re-frame the same product set into a 970x600 banner, product complete and centered, "
        "comfortable margins so nothing is lost when cropped, no text."
    ),
    "aplus_mobile": (
        "EDIT: re-frame the same product set into 600x450, tighter composition, product large in "
        "frame, details still readable on a small screen. All inventory pieces present."
    ),
}

# 品牌A+横幅（970×300）：与商品图完全不同的设计目标——画面只有品牌名艺术字，
# 参考图仅用于提取风格。走独立的指令模板，不与商品图的 inventory 流程混用。
BANNER_DESIGN_INSTRUCTION = """You are a senior brand designer and prompt engineer for the image-edit model Nano Banana 2 (Gemini image), working for {platform} in {market}.

You will receive ONE reference photo of a product. This task is NOT a product shot: design a 970x300 (about 3.2:1) brand banner whose ONLY content is the brand name rendered as artistic lettering.

STEP 1 — Extract style from the reference photo ONLY: dominant colours, accent colours, material texture, pattern motifs, decorative elements, overall mood. Do NOT copy the product, its packaging, people, text or logos from the photo, and do not keep the photo's composition.

STEP 2 — Design the banner around the brand name "{brand_name}":
- The brand name is the ONLY text allowed in the frame. Render EVERY letter as hand-crafted artistic lettering: per-letter shapes, stroke weight and curves, joins and breaks, serifs or cuts, baseline play, decorative flourishes drawn from the product's style. It must look like custom lettering designed by a human, NOT a plain system font and NOT a rigid geometric block.
- The word must be spelled exactly "{brand_name}", case-sensitive, no letters added, dropped or redesigned beyond recognition.
- Letterforms may intertwine with decorative background elements, but the word stays perfectly readable.
- Brand name width about 65-75% of the frame width; you decide the exact size and position for beauty. Decorations support it from the edges or weave around it and must not reduce readability.
- No product, no packaging, no people, and no other text, letters or numbers anywhere.

STEP 3 — Reply with a single JSON object, no markdown fence, no explanation:
{{
  "prompt_en": "60-110 words describing: background palette and gradient, decorative motifs and where they sit, the lettering style of each letter (stroke, joins, flourishes), light and mood. Must state that the exact brand name '{brand_name}' is the only text.",
  "prompt_cn": "a concise simplified-Chinese translation of prompt_en",
  "negative": "product, packaging, person, hands, extra text, letters, numbers, watermark, logo, price tag"
}}
Output nothing outside the JSON.
"""

DESIGN_INSTRUCTION = """You are a senior e-commerce product photographer and prompt engineer for the image-edit model Nano Banana 2 (Gemini image), working for {platform} in {market}.

You will receive ONE reference photo of a product, the required shot type, and (usually) a CONFIRMED PRODUCT CARD.

STEP 0 — PRODUCT IDENTITY IS ALREADY DECIDED. DO NOT RE-IDENTIFY IT.
The operator has already analysed this product with a separate vision pass over ALL reference photos.
The CONFIRMED PRODUCT CARD below is authoritative for product name, material, colours and piece count.
Do not rename it, do not change its material or colours, and do not add or drop pieces.
Your only task in this step is to turn that card into an explicit English inventory ("<piece> x<count>")
so the image model cannot silently drop small items.
- Only when the CONFIRMED PRODUCT CARD is empty: read the image yourself and list EVERY sellable piece,
  including SMALL ones (loose plants/grass, float balls, paddles/oars, the life ring, extra fish, hooks,
  picks). The cake itself, the table, the water, the stones/pebbles and the background are NOT the product,
  but any ornament placed on or around the cake that looks like a separate decoration IS part of it.
Never invent certifications, dimensions or claims.

STEP 1 — Design the requested shot:
{shot_spec}

STEP 2 — Write the prompt as an EDIT of the reference photo, not as a redesign.
Nano Banana 2 is an image-edit model: it preserves far more when told what to KEEP than when told
to create something new. So prompt_en must follow this order:
  (a) "Keep the product from the reference photo exactly:" then the full inventory, piece by piece
      with counts, plus its colors and materials.
  (b) Then the SINGLE change this shot requires (background / surface / camera), stated as a change
      applied to that same product.
  (c) Then one sentence of camera + lighting.
Use verbs like keep / retain / the same / unchanged. Never use arrange, restyle, redesign,
compose a new set, or "three-piece set" when the inventory has more pieces.

STEP 3 — Reply with a single JSON object, no markdown fence, no explanation:
{{
  "product": {{}},
  "inventory": ["<piece in English> x<count>", "..."],
  "prompt_en": "70-120 words, one paragraph, written per STEP 2 order (keep-list first, then the single change, then camera and light). Name at least three specific visual features of THIS product. State the exact piece count.",
  "prompt_cn": "a concise simplified-Chinese translation of prompt_en (may be shorter than the English)",
  "negative": "comma-separated list of things to avoid, in ENGLISH (it is appended to prompt_en)"
}}
IMPORTANT: "product" must be an EMPTY object {{}} whenever the CONFIRMED PRODUCT CARD is filled — do
not copy the card back into it (it only wastes output tokens and truncates the JSON). Fill it only when
the card is empty, in which case use the 7 keys name/category/material/colors/qty/selling_points/usage.

Global rules:
- Keep the JSON compact: inventory at most 10 entries. If the product has more small pieces,
  group the least important ones as "<group> x<total>". Output nothing outside the JSON.
- Product consistency is the number one requirement: every piece in the inventory must appear in the
  output, with the same count, color and shape. Missing a small piece is a failure.
- If the shot type asks for a clean/white background while the reference shows a staged scene
  (cake, plate, table), describe the change POSITIVELY: "the decorations stand directly on a bare
  white surface, nothing underneath them" — image models ignore pure negatives, so never rely on
  "no cake" alone; say what IS there instead.
- Never introduce text, logos, price tags or watermarks unless the shot type reserves blank copy areas.
- Do not describe the cake, table or background as part of the product.
- WHEN A PERSON APPEARS IN THE FRAME: describe that person POSITIVELY as an adult ("an adult woman in
  her late twenties, mature facial features"), because image models ignore pure negatives, AND always
  append "child, kid, teenager, minor, toddler" to the negative list. This is a hard compliance rule —
  a single minor in the frame makes the asset unusable, so never write a children's-party framing.
- When a person appears, the product must be WORN or USED exactly as the usage line of the CONFIRMED
  PRODUCT CARD describes (crown on the head, waist chain around the waist, bracelets on the wrists),
  never merely held in the hands and never laid out on a surface.
- When the shot type forbids people (the plain scene shot), state that the product is the only subject
  and put "person, people, hands, model, silhouette" into the negative list.
- If a THIS SHOT block is supplied, follow its framing / pose / light / setting exactly, and make the
  result visibly different from the other shots of the same task (different camera distance, body
  orientation and light direction). Same product, same style context, different picture — never repeat
  the same composition twice.
"""


# 带人物场景的「差异化矩阵」：同一任务出多张时按 seq 轮换，保证四张构图/姿态/光位不同。
# 人物身份不锁定（用户 2026-10-26 明确"同一模特不需要一定"），只锁场景主题（由商品信息卡的
# 「风格倾向」锚定），所以这里只管画面差异，不描述人物长相。
PERSON_VARIATIONS: List[Dict[str, str]] = [
    {
        "framing": "waist-up, three-quarter front view",
        "pose": "one hand lightly touching the crown at the temple, chin slightly lowered",
        "light": "soft low morning sun from the left with a gentle rim light on the hair",
        "setting": "beside a row of columns rendered softly out of focus",
    },
    {
        "framing": "full-length walking shot",
        "pose": "mid-stride, one hand lifting the hem while the waist chain swings naturally",
        "light": "warm golden-hour backlight, hair rim-lit, a slight atmospheric haze",
        "setting": "broad stone steps with foliage far behind",
    },
    {
        "framing": "head-and-shoulders close crop",
        "pose": "profile view, head lowered, fingers combing through the hair",
        "light": "diffused overcast light for maximum material and texture detail",
        "setting": "clean defocused backdrop in the product's own palette",
    },
    {
        "framing": "three-quarter view from behind, looking back over the shoulder",
        "pose": "turning back toward the camera, one hand resting on the waist chain",
        "light": "warm directional rim light outlining the silhouette",
        "setting": "an arch or doorway framing the subject",
    },
]


def _design_user_text(gen_type: str, ratio: str, product_info: str,
                      extra: str, brand_genes: str,
                      variation: Optional[Dict[str, str]] = None) -> str:
    lines = [f"Shot type key: {gen_type}", f"Aspect ratio: {ratio}"]
    if brand_genes:
        lines.append(f"Market / language / platform: {brand_genes}")
    if (product_info or "").strip():
        lines.append(
            "CONFIRMED PRODUCT CARD (authoritative — do not re-identify, do not rename, "
            "do not change material/colours/piece count):\n" + product_info.strip()
        )
    else:
        lines.append("CONFIRMED PRODUCT CARD: (empty — identify the product from the image yourself)")
    if variation:
        lines.append(
            "THIS SHOT (must differ from the other shots of the same task; these override the "
            "default framing and light of the shot spec):\n"
            f"- framing: {variation['framing']}\n"
            f"- pose: {variation['pose']}\n"
            f"- light: {variation['light']}\n"
            f"- setting: {variation['setting']}"
        )
    if (extra or "").strip():
        lines.append("Operator extra requirements:\n" + extra.strip())
    lines.append("Now output the JSON object.")
    return "\n\n".join(lines)


def design_shot_prompt(
    image_bytes: bytes,
    *,
    gen_type: str,
    ratio: str,
    product_info: str = "",
    extra: str = "",
    brand_genes: str = "",
    prepared: Optional[bytes] = None,
    variation: Optional[Dict[str, str]] = None,
    layout_bytes: Optional[bytes] = None,
    layout_hint: str = "",
    brand_name: str = "",
) -> Dict[str, Any]:
    """一次多模态调用：读商品 + 为这一张图设计专业级提示词。

    返回 {"product", "inventory", "prompt_en", "prompt_cn", "negative"}
    任一环节失败抛 RelayError，由调用方回退到模板。
    prepared: 已预处理的参考图（`prepare_reference`），多类型并行设计时复用。
    variation: 本张的差异化维度（`PERSON_VARIATIONS` 之一），仅带人物场景传。
    layout_bytes: A+ 版式参考图（用户上传的版式）。设计模型能看到它，把分区结构翻成英文提示词，
        出图阶段仍然是单图编辑（generate_batch 不变）。
    layout_hint: 内置版式的分区描述（无图可给时用它）。
    brand_name: 品牌A+横幅必填（aplus_banner 专属分支），其他类型忽略。
    """
    if gen_type == "aplus_banner":
        # 品牌横幅：跳过商品 inventory 流程，走专属指令（画面只有品牌名艺术字）
        if not (brand_name or "").strip():
            raise RelayError("品牌A+横幅缺少品牌名")
        small = prepared or _shrink_for_analysis(image_bytes)
        instruction = BANNER_DESIGN_INSTRUCTION.format(
            platform=_pick_platform(brand_genes),
            market=_pick_market(brand_genes),
            brand_name=brand_name.strip(),
        )
        content = [
            {"type": "text", "text": instruction},
            {"type": "text", "text": (
                f"Shot type key: aplus_banner\nAspect ratio: 970x300 (about 3.2:1)\n"
                f"Brand name (the ONLY text, exact spelling & case): {brand_name.strip()}\n"
                "Now output the JSON object."
            )},
            {"type": "image_url", "image_url": {"url": _image_data_uri(small)}},
        ]
        chan = _analyze_channel()
        cfg = get_settings()
        timeout = int(getattr(cfg, "IMAGE_RELAY_DESIGN_TIMEOUT", 180) or 180)
        text = _post_chat(chan["base"], chan["key"], chan["model"], content,
                          timeout, label="视觉模型",
                          max_tokens=int(getattr(cfg, "IMAGE_RELAY_DESIGN_MAX_TOKENS", 2600) or 2600))
        data = _parse_design_json(text)
        prompt_en = (data.get("prompt_en") or "").strip()
        if not prompt_en:
            raise RelayError("品牌横幅提示词设计结果为空")
        return {
            "product": {},
            "inventory": [],
            "prompt_en": prompt_en,
            "prompt_cn": (data.get("prompt_cn") or "").strip(),
            "negative": (data.get("negative") or "").strip(),
        }

    spec = SHOT_SPECS.get(gen_type)
    if not spec:
        raise RelayError(f"未定义的图类型: {gen_type}")

    small = prepared or _shrink_for_analysis(image_bytes)
    instruction = DESIGN_INSTRUCTION.format(
        platform=_pick_platform(brand_genes),
        market=_pick_market(brand_genes),
        shot_spec=spec,
    )
    content = [
        {"type": "text", "text": instruction},
        {"type": "text", "text": _design_user_text(
            gen_type, ratio, product_info, extra, brand_genes, variation=variation
        )},
        {"type": "image_url", "image_url": {"url": _image_data_uri(small)}},
    ]
    # A+ 版式：把版式图作为第 2 张图给设计模型看，让它在 prompt_en 里把分区结构写清楚。
    # 必须显式禁止搬运版式图里的商品/文案——否则设计模型会把别人的商品也写进清单。
    if layout_bytes or layout_hint:
        note = (
            "LAYOUT REFERENCE (hard requirement for this shot): organise the frame strictly "
            "according to the A+ layout below — same zone positions, same split ratios, same "
            "text-free gaps. Borrow ONLY the zone structure and whitespace; never carry over "
            "the product, copy, logos or colour scheme shown in the layout reference.\n"
        )
        if layout_hint:
            note += f"LAYOUT STRUCTURE: {layout_hint}\n"
        if layout_bytes:
            note += "The SECOND image attached is the layout reference.\n"
        note += "Write prompt_en so the described layout is explicit (which side holds the product, where the clean text gap sits)."
        content.append({"type": "text", "text": note})
        if layout_bytes:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_data_uri(_shrink_for_analysis(layout_bytes))},
            })
    chan = _analyze_channel()
    # 设计单次输出较长，偶发被模型拖到 240s；用更短的专用超时 + 一次重试，
    # 失败就快速降级到模板，避免整单被一类拖住 5 分钟。
    cfg = get_settings()
    timeout = int(getattr(cfg, "IMAGE_RELAY_DESIGN_TIMEOUT", 180) or 180)
    base_tokens = int(getattr(cfg, "IMAGE_RELAY_DESIGN_MAX_TOKENS", 2600) or 2600)
    text = ""
    # 第一次用配置值；被 token 上限截断时第二次放大 1.5 倍
    # （多组件商品的 inventory + prompt 更长，固定 1600 实测会把 JSON 截断成半截）
    for attempt, budget in ((1, base_tokens), (2, int(base_tokens * 1.5))):
        try:
            text = _post_chat(chan["base"], chan["key"], chan["model"], content,
                              timeout, label="视觉模型", max_tokens=budget,
                              raise_on_length=True)
            break
        except RelayError as e:
            if attempt == 2:
                raise
            logger.warning(
                f"[生图中转站] {gen_type} 提示词设计失败（max_tokens={budget}），"
                f"放大后重试一次: {e}"
            )
    data = _parse_design_json(text)
    if not (data.get("prompt_en") or "").strip():
        # JSON 被截断：从半截文本里捞已完整写好的字段（prompt_en 通常已写完）
        salvaged = _salvage_design(text)
        if salvaged.get("prompt_en"):
            data = {**salvaged, **data}
    prompt_en = (data.get("prompt_en") or "").strip()
    if not prompt_en:
        raise RelayError(f"提示词设计结果为空（{gen_type}）")
    product = data.get("product") or {}
    inv = data.get("inventory")
    inventory = [str(x).strip() for x in inv if str(x).strip()] if isinstance(inv, list) else []
    return {
        "product": product if isinstance(product, dict) else {},
        "inventory": inventory,
        "prompt_en": prompt_en,
        "prompt_cn": (data.get("prompt_cn") or "").strip(),
        "negative": (data.get("negative") or "").strip(),
    }


def _pick_platform(brand_genes: str) -> str:
    for key in ("发布平台", "platform"):
        if key in (brand_genes or ""):
            part = brand_genes.split(key, 1)[1]
            for ch in ("；", ";", "，", ","):
                part = part.split(ch, 1)[0]
            val = part.strip(" ：:")
            if val:
                return val
    return "Amazon"


def _pick_market(brand_genes: str) -> str:
    for key in ("目标市场", "销售国家", "country"):
        if key in (brand_genes or ""):
            part = brand_genes.split(key, 1)[1]
            for ch in ("；", ";", "，", ","):
                part = part.split(ch, 1)[0]
            val = part.strip(" ：:")
            if val:
                return val
    return "US"


def _parse_design_json(text: str) -> Dict[str, Any]:
    """从模型输出里抠 JSON（容忍 ```json 包裹与前后废话）"""
    if not text:
        return {}
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning(f"[生图中转站] 提示词设计结果非 JSON: {text[:160]}")
        return {}
    return data if isinstance(data, dict) else {}


def _salvage_design(text: str) -> Dict[str, Any]:
    """JSON 不完整时的兜底：用正则捞出已经写完整的字段。

    实测多组件商品（气球套装等）会因 token 上限把 JSON 截成半截：
    此时 prompt_en 往往已经写完，只是后面的 prompt_cn/negative 被切掉。
    与其整类回退模板（质量骤降、清单丢失），不如把已完整的部分捞出来用。
    """
    raw = text or ""
    out: Dict[str, Any] = {}
    for key in ("prompt_en", "prompt_cn", "negative"):
        m = re.search(rf'"{key}"\s*:\s*"(.+?)"\s*[,}}\n]', raw, re.S)
        if not m:
            continue
        val = m.group(1)
        # 还原常见转义，避免把 \n / \" 直接展示给运营
        val = val.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        if val.strip():
            out[key] = val.strip()
    inv = re.search(r'"inventory"\s*:\s*(\[.*?\])', raw, re.S)
    if inv:
        try:
            arr = json.loads(inv.group(1))
            if isinstance(arr, list):
                out["inventory"] = [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    if out.get("prompt_en"):
        logger.warning(
            f"[生图中转站] 设计 JSON 不完整，已从截断文本中捞回 {sorted(out)}"
        )
    return out


def is_configured() -> bool:
    s = get_settings()
    return bool((s.IMAGE_RELAY_API_BASE or "").strip() and (s.IMAGE_RELAY_API_KEY or "").strip())


def provider_info() -> Dict[str, str]:
    s = get_settings()
    return {
        "value": "relay",
        "label": s.IMAGE_RELAY_LABEL or "Nano Banana 2",
        "model": s.IMAGE_RELAY_MODEL,
        "configured": "1" if is_configured() else "0",
    }


def _endpoint(base: str) -> str:
    return (base or "").rstrip("/") + "/chat/completions"


def _analyze_channel() -> Dict[str, str]:
    """看图分析走哪条通道。

    默认与生视频（`services/ai_creation_service.py`）**完全一致**：
    `OPENAI_API_BASE` + `OPENAI_API_KEY` + `OPENAI_VISION_MODEL`。
    这样两个页面对同一张图给出的事实口径相同，运营不需要在两处看到不同的品名/材质。

    仅当显式配置了 `IMAGE_RELAY_ANALYZE_MODEL`（指定别的模型）或 OPENAI 未配置时，
    才退回中转站自己的 base/key。
    """
    s = get_settings()
    openai_base = (s.OPENAI_API_BASE or "").strip()
    openai_key = (s.OPENAI_API_KEY or "").strip()
    if openai_base and openai_key:
        return {
            "base": openai_base,
            "key": openai_key,
            "model": (s.IMAGE_RELAY_ANALYZE_MODEL or s.OPENAI_VISION_MODEL or s.OPENAI_MODEL or "").strip(),
        }
    if not is_configured():
        raise RelayError("看图分析未配置：需要 OPENAI_API_BASE/OPENAI_API_KEY 或 IMAGE_RELAY_API_BASE/KEY")
    return {
        "base": s.IMAGE_RELAY_API_BASE,
        "key": s.IMAGE_RELAY_API_KEY,
        "model": (s.IMAGE_RELAY_ANALYZE_MODEL or s.IMAGE_RELAY_MODEL).strip(),
    }


# 429/5xx 重试次数（含首次）。超时不重试：出图单次已 240s，再重试只会双倍计费。
_RELAY_RETRY_ATTEMPTS = 3


def _retry_delay(attempt: int, retry_after: Optional[str] = None) -> float:
    """指数退避 + 尊重 Retry-After，单次最长 10s"""
    hinted = 0.0
    try:
        if retry_after:
            hinted = float(str(retry_after).strip())
    except Exception:
        hinted = 0.0
    return min(10.0, max(hinted, 1.5 * (2 ** attempt)))


def _post_chat(base: str, key: str, model: str, content: List[Dict[str, Any]],
               timeout: int, *, label: str = "中转站",
               max_tokens: Optional[int] = None,
               raise_on_length: bool = False) -> str:
    """发一次多模态对话，返回 message.content 文本（生成与分析共用）

    max_tokens 只用于「提示词设计」这类纯文本输出：不设上限时，模型偶尔会
    长篇发挥把单次调用拖到 240s 超时（实测白底图类型出现过）。
    raise_on_length=True 时，若响应因 token 上限被截断则抛错，让调用方放大预算重试
    —— 截断的 JSON 解析必然失败，不抛错就只会被当成「模型返回异常」静默降级模板。

    429/5xx 会按指数退避重试（含 Retry-After）：并发高峰期偶发限流不应直接
    把整张图判失败；超时不重试（成本与耗时都太高）。
    """
    if not (base and key and model):
        raise RelayError(f"{label}通道配置不完整（base/key/model）")
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    resp: Optional[requests.Response] = None
    for attempt in range(_RELAY_RETRY_ATTEMPTS):
        try:
            resp = _session().post(
                _endpoint(base),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout:
            raise RelayError(f"{label}请求超时（>{timeout}s，模型 {model}）")
        except Exception as e:
            if attempt + 1 < _RELAY_RETRY_ATTEMPTS:
                delay = _retry_delay(attempt)
                logger.warning(f"{label}连接失败，{delay:.1f}s 后重试：{e}")
                time.sleep(delay)
                continue
            raise RelayError(f"{label}连接失败：{e}")

        if (resp.status_code == 429 or resp.status_code >= 500) \
                and attempt + 1 < _RELAY_RETRY_ATTEMPTS:
            delay = _retry_delay(attempt, resp.headers.get("Retry-After"))
            logger.warning(
                f"{label}返回 {resp.status_code}，{delay:.1f}s 后重试"
                f"（第 {attempt + 1}/{_RELAY_RETRY_ATTEMPTS - 1} 次）"
            )
            time.sleep(delay)
            continue
        break

    assert resp is not None
    if resp.status_code == 401:
        raise RelayError(f"{label}令牌无效或已过期")
    if resp.status_code == 429:
        raise RelayError(f"{label}限流（429），请降低并行数或稍后重试")
    if resp.status_code >= 400:
        raise RelayError(f"{label}返回 {resp.status_code}：{(resp.text or '')[:300]}")

    try:
        data = resp.json()
    except Exception:
        raise RelayError(f"{label}返回非 JSON")
    choices = data.get("choices") or []
    if not choices:
        raise RelayError(f"{label}返回无 choices")
    if raise_on_length and choices[0].get("finish_reason") == "length":
        raise RelayError(f"{label}输出被 max_tokens={max_tokens} 截断（finish_reason=length）")
    msg = choices[0].get("message") or {}
    content_val = msg.get("content")
    if isinstance(content_val, list):
        # 少数实现返回分段
        texts = []
        for part in content_val:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("image_url"):
                    url = (part.get("image_url") or {}).get("url") or ""
                    texts.append(f"![image]({url})")
        return "\n".join(texts)
    return content_val or ""


def _call(content: List[Dict[str, Any]], timeout: Optional[int] = None,
          model: Optional[str] = None) -> str:
    """出图调用（走 IMAGE_RELAY_* 通道）"""
    s = get_settings()
    if not is_configured():
        raise RelayError("中转站未配置（IMAGE_RELAY_API_BASE / IMAGE_RELAY_API_KEY）")
    return _post_chat(
        s.IMAGE_RELAY_API_BASE, s.IMAGE_RELAY_API_KEY,
        model or s.IMAGE_RELAY_MODEL,
        content, timeout or s.IMAGE_RELAY_TIMEOUT,
    )


def _image_data_uri(image_bytes: bytes) -> str:
    from PIL import Image
    import io as _io

    try:
        im = Image.open(_io.BytesIO(image_bytes))
        fmt = (im.format or "JPEG").lower()
        mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png",
                "webp": "image/webp"}.get(fmt, "image/jpeg")
    except Exception:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def extract_image_bytes(content: str, timeout: int = 120) -> bytes:
    """从返回文本里取出第一张图片的字节"""
    m = _MD_BASE64.search(content) or _BARE_BASE64.search(content)
    if m:
        data = m.group(1)
        _, b64 = data.split(",", 1)
        try:
            return base64.b64decode(re.sub(r"\s+", "", b64))
        except Exception as e:
            raise RelayError(f"返回图片解码失败：{e}")

    m = _MD_URL.search(content)
    if m:
        url = m.group(1)
        try:
            r = _session().get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as e:
            raise RelayError(f"返回图片下载失败：{e}")

    raise RelayError(f"返回内容中没有图片（前 120 字：{(content or '')[:120]}）")


def _shrink_for_analysis(image_bytes: bytes, max_side: int = 1024, quality: int = 85) -> bytes:
    """分析前压缩：长边缩到 1024、转 JPEG。

    参数与生视频页的 `ai_creation_service._compress_image` 保持一致（1024/q85 + 透明底合成白底），
    使两个页面对同一张图输入相同的视觉信息。
    """
    from io import BytesIO

    from PIL import Image

    try:
        im = Image.open(BytesIO(image_bytes))
        im.load()
        if im.mode in ("RGBA", "P", "LA"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        if max(w, h) > max_side:
            r = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        # 压缩反而变大（小图重编码）时用原图，避免无谓劣化
        return out if len(out) < len(image_bytes) else image_bytes
    except Exception as e:
        logger.warning(f"[生图中转站] 分析前压缩失败，用原图: {e}")
        return image_bytes


def prepare_reference(image_bytes: bytes) -> bytes:
    """设计提示词前的参考图预处理（长边 1024 / q85）。

    一次任务可能设计多类图，各自压缩同一张大图会白耗 CPU，
    由调用方算一次后通过 `design_shot_prompt(prepared=...)` 复用。
    """
    return _shrink_for_analysis(image_bytes)


def generate_image(image_bytes: bytes, prompt: str, *, timeout: Optional[int] = None) -> bytes:
    """按提示词生成一张图，返回图片字节"""
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_data_uri(image_bytes)}},
    ]
    text = _call(content, timeout=timeout)
    return extract_image_bytes(text, timeout=timeout or 120)


def generate_batch(image_bytes: bytes, prompts: List[str], *,
                   concurrency: Optional[int] = None) -> List[Dict[str, Any]]:
    """并行生成多张（每张独立重试上下文），返回与 prompts 等长的结果列表。

    单项失败不影响其它项：失败项带 error 字段，由调用方落库为该子项失败。
    """
    s = get_settings()
    workers = max(1, min(concurrency or s.IMAGE_RELAY_CONCURRENCY, len(prompts) or 1))
    results: List[Dict[str, Any]] = [{} for _ in prompts]

    def _one(idx: int) -> None:
        try:
            data = generate_image(image_bytes, prompts[idx])
            results[idx] = {"ok": True, "data": data}
        except Exception as e:
            results[idx] = {"ok": False, "error": str(e)[:300]}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="relay-img") as ex:
        list(ex.map(_one, range(len(prompts))))
    return results
