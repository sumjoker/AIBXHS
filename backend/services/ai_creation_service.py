import json
import base64
import asyncio
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from openai import OpenAI
from fastapi import UploadFile

from config import get_settings
from services.ai_concurrency import ai_call_slot

settings = get_settings()
logger = logging.getLogger(__name__)


# Amazon 产品图片分析 JSON Schema
AMAZON_PRODUCT_ANALYSIS_SCHEMA = {
    "type": "json_schema",
    "name": "amazon_product_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "product_name_cn": {"type": "string"},
            "product_name_en": {"type": "string"},
            "product_type": {"type": "string"},
            "product_size": {"type": "string"},
            "target_audience": {
                "type": "array",
                "items": {"type": "string"},
            },
            "selling_points": {
                "type": "array",
                "items": {"type": "string"},
            },
            "material": {
                "type": "array",
                "items": {"type": "string"},
            },
            "usage_scenarios": {
                "type": "array",
                "items": {"type": "string"},
            },
            "usage_methods": {
                "type": "array",
                "items": {"type": "string"},
            },
            "product_components": {
                "type": "array",
                "items": {"type": "string"},
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
            },
            "amazon_category": {"type": "string"},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
            },
            "visible_text": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {"type": "number"},
            "uncertain_information": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "product_name_cn",
            "product_name_en",
            "product_type",
            "product_size",
            "target_audience",
            "selling_points",
            "material",
            "usage_scenarios",
            "usage_methods",
            "product_components",
            "colors",
            "amazon_category",
            "keywords",
            "visible_text",
            "confidence",
            "uncertain_information",
        ],
        "additionalProperties": False,
    },
}


# ============================================================
# 【旧方案提示词 · 已停用，保留备查，可随时切回】
# 旧流程：① 视觉模型识别商品 → ② 文本模型生成 3 套创意（JSON 结构）
# 注意：以下 SYSTEM_PROMPT / VIDEO_CONCEPT_SYSTEM_PROMPT /
#       VIDEO_PROMPT_SYSTEM_PROMPT 及对应接口仍保留可用，
#       但前端已切换到下方新方案（一次性多模态生成口播方案）。
# ============================================================

SYSTEM_PROMPT = """你是一名 Amazon 跨境电商产品分析专家。

你的任务是：
根据用户提供的商品图片，识别图片中的实际商品，并生成 Amazon 产品信息。

重要规则：

1. 只能根据图片中可以观察到的信息进行判断。
2. 不要把无法从图片确认的信息当成事实。
3. 对材质、尺寸、数量、食品级、安全认证、是否可重复使用等信息，
   如果图片无法确认，必须明确标记为“无法从图片确认”。
4. 可以根据商品外观和使用场景推测目标消费者，但必须保持合理。
5. Amazon 类目需要根据商品实际用途进行判断。
6. 产品名称应该是 Amazon 风格的英文商品名称，同时提供中文名称。
7. 卖点必须围绕图片中真实可见的产品特征。
8. 不要描述蛋糕本身是商品，如果图片展示的是蛋糕装饰品，
   应识别真正销售的商品为蛋糕装饰品。
9. 不要把背景中的道具误认为商品组成部分。
10. 如果图片中存在文字，可以进行 OCR，并结合文字判断产品。

请特别区分：

- 图片中明确可见的信息
- 根据视觉特征合理推测的信息
- 无法确认的信息

不要编造产品参数。
如果上传图片中包含尺寸图则可得出产品尺寸，否则产品尺寸留空，不得猜测。

必须按以下 JSON 格式返回，不要包含任何其他说明文字：

{
    "product_name_cn": "中文名称",
    "product_name_en": "Amazon 英文名称",
    "product_type": "产品类型",
    "product_size": "产品尺寸，如 30.5'D x 27'W x 30'H；图片无法确认时留空字符串",
    "target_audience": ["受众1", "受众2"],
    "selling_points": ["卖点1", "卖点2"],
    "material": ["材质1"],
    "usage_scenarios": ["场景1"],
    "usage_methods": ["使用方式1", "使用方式2"],
    "product_components": ["组件1"],
    "colors": ["颜色1"],
    "amazon_category": "Amazon 类目",
    "keywords": ["关键词1"],
    "visible_text": ["图片中可见文字1"],
    "confidence": 0.85,
    "uncertain_information": ["无法确认的信息1"]
}
"""


def _compress_image(image_bytes: bytes, mime_type: str, max_side: int = 1024, quality: int = 85) -> tuple[bytes, str]:
    """压缩参考图：长边缩到 max_side 内、转 JPEG 重编码。

    多张手机原图（3~8MB/张）直接 base64 上传会导致请求体过大、视觉模型处理缓慢甚至超时。
    识别商品信息 1024px 足够；压缩失败时回退原图。
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        if img.mode in ("RGBA", "P", "LA"):
            # 透明通道合成到白底（商品图白底为主），再转 JPEG
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        compressed = buf.getvalue()
        if len(compressed) < len(image_bytes):
            logger.info(
                f"[AI创作中心] 图片压缩: {len(image_bytes)//1024}KB -> {len(compressed)//1024}KB"
            )
            return compressed, "image/jpeg"
        return image_bytes, mime_type
    except Exception as e:
        logger.warning(f"[AI创作中心] 图片压缩失败，使用原图: {e}")
        return image_bytes, mime_type


def _build_user_content(files: List[UploadFile]) -> List[Dict[str, Any]]:
    """构建包含图片的用户消息内容"""
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": "请根据以下商品图片进行 Amazon 产品信息分析。"}
    ]

    for file in files:
        try:
            file.file.seek(0)
            image_bytes = file.file.read()
            mime_type = file.content_type or "image/jpeg"
            image_bytes, mime_type = _compress_image(image_bytes, mime_type)
            encoded = base64.b64encode(image_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                },
            })
        except Exception as e:
            logger.error(f"读取图片失败 {file.filename}: {e}")
        finally:
            try:
                file.file.close()
            except Exception:
                pass

    return content


class AIAnalysisError(Exception):
    """AI 分析异常"""
    pass


def _create_chat_completion(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    response_format: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """单次调用 OpenAI 聊天补全，返回文本内容"""
    # OpenAI 要求 json_object 模式下消息中必须出现 "json" 字样
    if response_format and response_format.get("type") == "json_object":
        all_text = " ".join(str(m.get("content", "")) for m in messages).lower()
        if "json" not in all_text:
            messages = [dict(m) for m in messages]
            if messages:
                last = messages[-1]
                last_content = last.get("content", "")
                if isinstance(last_content, str):
                    last["content"] = last_content + "\n\nPlease return the result as valid JSON."
                else:
                    # content 为数组时追加 text 片段
                    last["content"] = list(last_content) + [
                        {"type": "text", "text": "\n\nPlease return the result as valid JSON."}
                    ]

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "timeout": 180,
    }
    if response_format:
        kwargs["response_format"] = response_format

    logger.info(f"[AI创作中心] 调用模型 {model}, response_format={response_format.get('type') if response_format else 'none'}")

    try:
        with ai_call_slot():
            response = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.error(f"OpenAI 调用失败: {e}")
        raise AIAnalysisError(f"AI 模型调用失败: {str(e)}")

    if not response.choices:
        logger.warning("OpenAI 响应 choices 为空")
        return None

    message = response.choices[0].message
    logger.info(f"[AI创作中心] 模型响应 finish_reason={response.choices[0].finish_reason}, content 长度={len(message.content or '')}")
    logger.debug(f"[AI创作中心] 模型响应内容: {message.content[:500] if message.content else '空'}")

    # 部分渠道会把 JSON 放在 tool_calls / function_call 中
    if message.tool_calls:
        logger.info(f"[AI创作中心] 响应包含 tool_calls: {len(message.tool_calls)}")
        for tool_call in message.tool_calls:
            if tool_call.function and tool_call.function.arguments:
                return tool_call.function.arguments

    return (message.content or "").strip()


def _call_openai_vision_sync(files: List[UploadFile]) -> Optional[str]:
    """同步调用 OpenAI 视觉模型进行产品分析，支持多种 response_format 回退"""
    if not settings.OPENAI_API_KEY:
        raise AIAnalysisError("OpenAI API Key 未配置")

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
    )

    content = _build_user_content(files)
    if len(content) <= 1:
        raise AIAnalysisError("没有可用的图片内容")

    model = settings.OPENAI_VISION_MODEL or "gpt-4o"
    logger.info(f"[AI创作中心] 使用模型: {model}, 图片数量: {len(content) - 1}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    response_format_mode = (settings.OPENAI_VISION_RESPONSE_FORMAT or "json_object").lower()
    logger.info(f"[AI创作中心] 响应格式配置: {response_format_mode}")

    response_format: Optional[Dict[str, Any]] = None
    if response_format_mode == "json_schema":
        response_format = AMAZON_PRODUCT_ANALYSIS_SCHEMA
    elif response_format_mode == "json_object":
        response_format = {"type": "json_object"}

    # 按配置调用模型，避免多次重试导致重复扣费
    answer_text = _create_chat_completion(client, model, messages, response_format=response_format)

    if not answer_text:
        raise AIAnalysisError("AI 模型返回内容为空，请尝试切换 OPENAI_VISION_RESPONSE_FORMAT 配置（如 json_object / text）")

    logger.debug(f"OpenAI 视觉模型响应: {answer_text[:300]}...")
    return answer_text


async def analyze_product_images(files: List[UploadFile]) -> Optional[Dict[str, Any]]:
    """异步分析商品图片，返回结构化 Amazon 产品信息"""
    try:
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(_call_openai_vision_sync, files),
            timeout=240,
        )
    except asyncio.TimeoutError:
        raise AIAnalysisError("AI 图片分析超时（240秒）")

    if not raw_text:
        raise AIAnalysisError("AI 模型返回内容为空")

    result = parse_analysis_result(raw_text)
    if not result:
        raise AIAnalysisError(f"无法解析 AI 返回内容为结构化数据: {raw_text[:200]}")

    return result


def _build_user_content_from_bytes(items: List[Tuple[str, bytes]],
                                   hint: str = "请根据以下商品图片进行 Amazon 产品信息分析。"
                                   ) -> List[Dict[str, Any]]:
    """与 `_build_user_content` 同口径，但入参是内存 bytes。

    生图/中转站链路拿不到 UploadFile（参考图在后台线程里已是 bytes），
    又不能为此复制一套 SYSTEM_PROMPT —— 两页必须共用同一套分析纪律，
    否则同一张图会在两个页面得出不同商品结论。
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": hint}]
    for name, raw in items:
        try:
            image_bytes, mime_type = _compress_image(raw, "image/jpeg")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}",
                },
            })
        except Exception as e:
            logger.error(f"[AI创作中心] 读取图片失败 {name}: {e}")
    return content


def analyze_product_card(items: List[Tuple[str, bytes]], product_name: str = "") -> Dict[str, Any]:
    """商品信息卡分析（生图通道复用生视频的完整口径）。

    与 `analyze_product_images` 用**同一份 SYSTEM_PROMPT、同一温度、同一结构化格式**，
    区别只有两点：
    1. 入参形态（内存 bytes，后台线程拿不到 UploadFile）；
    2. 追加两条生图场景的补充约束 —— **全部参考图一次全传**（单图会让模型只看一个
       角度就编造：实测 175px 缩略图把「金色月桂皇冠 4 件套」认成「海滩度假套装」），
       以及**不得推断民族/地域风格**（实测把月桂叶头冠+腰链+手镯认成「印度舞饰品」，
       图上根本没有 India 字样，只是组合联想）。

    :param items: [(文件名, 图片字节)]，按清晰度排好序
    :param product_name: 系统里的产品名（用户选的品），作为命名锚点，避免模型另起风格
    """
    if not settings.OPENAI_API_KEY:
        raise AIAnalysisError("OpenAI API Key 未配置")

    hint_lines = ["请根据以下商品图片进行 Amazon 产品信息分析。本批共 {} 张图片，是同一商品的不同角度/细节图。".format(len(items))]
    if (product_name or "").strip():
        hint_lines.append(
            f"该商品在系统中的名称是「{product_name.strip()}」，命名请以此为准；"
            f"不要改成其他民族/地域风格（如印度风、波西米亚风）的命名。"
            f"若图片与这个名称存在冲突，请把冲突写进 uncertain_information。"
        )
    hint_lines.append(
        "额外约束：只写图片上能直接看到的信息。不要根据「头饰+腰链+手镯」这类组合去推断"
        "民族文化或舞蹈风格；品类只写功能性描述（例如「头饰腰链手镯四件套」）。"
    )
    hint_lines.append(
        "再补充一个字段 style_context：用 2-4 个英文词描述该商品所属的风格/时代/节庆氛围"
        "（例如 greek classical、vintage christmas、modern everyday、outdoor garden wedding）。"
        "必须能从商品本身或商品名看出来；看不出明确风格就写 modern everyday，不要硬套。"
    )
    content = _build_user_content_from_bytes(items, hint="\n".join(hint_lines))
    if len(content) <= 1:
        raise AIAnalysisError("没有可用的图片内容")

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
        max_retries=0,      # 与生视频一致：超时不自动重试，避免重复扣费
        timeout=240,
    )
    model = settings.OPENAI_VISION_MODEL or "gpt-4o"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    mode = (settings.OPENAI_VISION_RESPONSE_FORMAT or "json_object").lower()
    response_format: Optional[Dict[str, Any]] = None
    if mode == "json_schema":
        response_format = AMAZON_PRODUCT_ANALYSIS_SCHEMA
    elif mode == "json_object":
        response_format = {"type": "json_object"}

    logger.info(
        f"[AI创作中心] 生图复用商品分析：模型 {model}，参考图 {len(content) - 1} 张，"
        f"名称锚点={product_name or '无'}"
    )
    text = _create_chat_completion(client, model, messages, response_format=response_format)
    if not text:
        raise AIAnalysisError("AI 模型返回内容为空")
    return parse_analysis_result(text) or {}


def parse_analysis_result(raw_text: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析 AI 返回的 JSON 文本"""
    if not raw_text:
        return None

    text = raw_text.strip()

    # 去掉 markdown 代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().endswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试正则提取 JSON 对象
    import re
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"无法解析 AI 分析结果为 JSON: {text[:300]}")
    return None


# ============ 带货口播 AI 视频提示词生成 ============

SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "timestamp": {"type": "string"},
        "shot_purpose": {"type": "string"},
        "shot_type": {"type": "string"},
        "camera_movement": {"type": "string"},
        "character_action": {"type": "string"},
        "character_expression": {"type": "string"},
        "product_action": {"type": "string"},
        "product_position": {"type": "string"},
        "composition": {"type": "string"},
        "environment": {"type": "string"},
        "voiceover": {"type": "string"},
        "voiceover_cn": {"type": "string"},
        "sound": {"type": "string"},
    },
    "required": [
        "timestamp",
        "shot_purpose",
        "shot_type",
        "camera_movement",
        "character_action",
        "character_expression",
        "product_action",
        "product_position",
        "composition",
        "environment",
        "voiceover",
        "voiceover_cn",
        "sound",
    ],
    "additionalProperties": False,
}


VIDEO_CONCEPT_SCHEMA = {
    "type": "json_schema",
    "name": "video_concepts",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "concept_title": {"type": "string"},
                        "marketing_goal": {"type": "string"},
                        "creative_strategy": {
                            "type": "object",
                            "properties": {
                                "persona_identity": {"type": "string"},
                                "persona_role": {"type": "string"},
                                "age": {"type": "string"},
                                "relationship_to_product": {"type": "string"},
                                "story_background": {"type": "string"},
                                "consumption_scene": {"type": "string"},
                                "core_pain_point": {"type": "string"},
                                "core_selling_point": {"type": "string"},
                                "emotion": {"type": "string"},
                                "video_style": {"type": "string"},
                                "camera_language": {"type": "string"},
                            },
                            "required": [
                                "persona_identity",
                                "persona_role",
                                "age",
                                "relationship_to_product",
                                "story_background",
                                "consumption_scene",
                                "core_pain_point",
                                "core_selling_point",
                                "emotion",
                                "video_style",
                                "camera_language",
                            ],
                            "additionalProperties": False,
                        },
                        "story": {"type": "string"},
                        "character": {"type": "string"},
                        "environment": {"type": "string"},
                        "music": {"type": "string"},
                        "storyboard": {
                            "type": "array",
                            "items": SHOT_SCHEMA,
                        },
                    },
                    "required": [
                        "concept_title",
                        "marketing_goal",
                        "creative_strategy",
                        "story",
                        "character",
                        "environment",
                        "music",
                        "storyboard",
                    ],
                    "additionalProperties": False,
                },
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "required": ["concepts"],
        "additionalProperties": False,
    },
}


VIDEO_PROMPT_SCHEMA = {
    "type": "json_schema",
    "name": "video_prompts",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "concept_title": {"type": "string"},
                        "final_prompt": {"type": "string"},
                    },
                    "required": ["concept_title", "final_prompt"],
                    "additionalProperties": False,
                },
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "required": ["prompts"],
        "additionalProperties": False,
    },
}


VIDEO_CONCEPT_SYSTEM_PROMPT = """You are an elite short-form ecommerce video creative director specialized in TikTok/Instagram Reels style UGC ads.

Your task: create 3 highly differentiated, dynamically generated video advertising concepts for the product described in the Product Profile.

=== DYNAMIC GENERATION RULES ===

DO NOT use fixed templates like "方案一 = 妈妈UGC, 方案二 = 专业达人, 方案三 = 居家分享".

Every concept must be dynamically generated from scratch based on:
- Product Profile
- Target Market (e.g. US)
- Video Duration

Each concept must define its own Creative Strategy with these fields:
1. persona_identity — who is the character
2. persona_role — their job/role/status
3. age — approximate age range
4. relationship_to_product — how they relate to the product
5. story_background — the setup/context
6. consumption_scene — where they use/buy the product
7. core_pain_point — the problem they face
8. core_selling_point — the key benefit for them
9. emotion — the dominant emotional tone
10. video_style — the presentation style
11. camera_language — camera/workflow approach

Marketing goals must be dynamically assigned. Choose from:
- 推荐款
- 场景种草
- 促销转化
- 情绪共鸣
- 专业评测
- DIY教程
- 问题解决
- 前后对比
- 真实体验
- 开箱体验
- 生活方式种草
- 礼物推荐

The 3 concepts must have DIFFERENT marketing goals when possible.

DIFFERENTIATION REQUIREMENTS:
The 3 concepts must differ significantly in at least 4 of these dimensions:
- character/persona
- story
- hook
- core pain point
- marketing angle
- usage scene
- emotion
- filming style
- product presentation
- purchase reason

Do NOT just swap names. Each concept must feel like a genuinely different creative direction.

=== DURATION RULES ===

Duration is a core parameter. Design the story structure from the beginning for the given duration:

- 15 seconds: 4-5 shots, minimal setup, immediate product focus
- 30 seconds: 6-8 shots, short hook, product demo, CTA
- 45 seconds: 8-10 shots, add background/pain point/details
- 60 seconds: 10-14 shots, full arc: background, pain point, unboxing, details, usage, transformation, emotion, CTA

Do not generate a 30s script and trim it. Start from the duration.

=== ASPECT RATIO ===

The target video aspect ratio is provided in the user input (e.g. 9:16). Design shot composition and framing to fit that ratio:
- auto: follow the reference product image's aspect ratio
- 9:16 (vertical, TikTok/Reels): subjects roughly centered vertically, keep key content within the middle strip to avoid crop loss, hook face near the upper third
- 3:4 (portrait): slightly wider vertical framing
- 1:1 (square): balanced centered composition
- 4:3 (landscape): moderate horizontal framing
- 16:9 (landscape): wide cinematic framing, allow wide backgrounds
- 21:9 (ultra-wide landscape): cinematic widescreen framing, emphasize horizontal space

Mention the framing/composition choices in shot_type and composition accordingly.

=== MARKET ADAPTATION ===

Target market language mapping (the voiceover must use the market's language):
- US / UK: English
- CA: English (Canadian)
- AU: English (Australian)
- DE: German
- FR: French
- JP: Japanese
- CN: Chinese

All voiceover/dialogue must be natural spoken dialogue in the target market's language — NOT translated Chinese and NOT English for non-English markets.

Characters, clothing, home decor, furniture, props, and lifestyle must match the target market's culture.
Do NOT use Chinese-style homes, furniture, clothing, or behavior (unless market = CN).

Characters can be: local consumers, young local mom, new local dad, party planner, baking enthusiast, professional baker, parent throwing first birthday party, etc. Choose dynamically based on product and market.

=== PRODUCT FACT CONSTRAINTS ===

Stories, characters, and emotions can be fictional.
BUT product facts must NOT be invented:
- material
- size
- quantity
- certifications
- food-grade claims
- safety claims
- functions
- packaging contents
- product components

If uncertain from the Product Profile, do NOT state it as fact in the voiceover.

[product_anchor] = the exact product shown in the reference image, including all visible components.
Throughout the video, keep the product unchanged in:
- color
- shape
- structure
- components
- proportions
- design

The product is the sales subject. The cake is only the usage scene, not the product being sold.

=== STORYBOARD FORMAT (Linkfox style) ===

For each shot, provide in Chinese for visual fields and market-appropriate language for voiceover:

- timestamp: e.g. "0-3秒"
- shot_purpose: e.g. "视觉钩子"
- shot_type: e.g. "手机前置自拍，中近景" (Chinese)
- camera_movement: e.g. "手持自然晃动，快速甩镜" (Chinese)
- character_action: Chinese
- character_expression: Chinese
- product_action: Chinese
- product_position: Chinese
- composition: Chinese
- environment: Chinese
- voiceover: the EXACT spoken line for this shot — complete, natural, speakable sentences in the target market's language. Never a description such as "介绍产品外观", never a placeholder, never empty; every shot must contain real dialogue/voiceover text
- voiceover_cn: Chinese translation of the voiceover (also never empty)
- sound: music / sound effects

=== STRUCTURE ===

Each video should feel like authentic UGC, not a traditional commercial.

- First 3 seconds: strong visual hook
- Middle: demonstrate product appearance, use case, and benefits
- Final section: create purchase intent + clear CTA

Generate exactly 3 concepts as valid JSON using this structure:
{
  "concepts": [
    {
      "concept_title": "...",
      "marketing_goal": "...",
      "creative_strategy": {
        "persona_identity": "...",
        "persona_role": "...",
        "age": "...",
        "relationship_to_product": "...",
        "story_background": "...",
        "consumption_scene": "...",
        "core_pain_point": "...",
        "core_selling_point": "...",
        "emotion": "...",
        "video_style": "...",
        "camera_language": "..."
      },
      "story": "...",
      "character": "...",
      "environment": "...",
      "music": "...",
      "storyboard": [
        {
          "timestamp": "0-3秒",
          "shot_purpose": "视觉钩子",
          "shot_type": "手机前置自拍，中近景",
          "camera_movement": "手持自然晃动",
          "character_action": "...",
          "character_expression": "...",
          "product_action": "...",
          "product_position": "...",
          "composition": "...",
          "environment": "...",
          "voiceover": "Natural spoken dialogue in the target market's language",
          "voiceover_cn": "中文翻译",
          "sound": "..."
        }
      ]
    }
  ]
}
"""


VIDEO_PROMPT_SYSTEM_PROMPT = """You are an expert prompt engineer for AI video generation models (Sora, Runway, Pika, Kling, etc.).

You will receive a product profile and three video advertising concepts with detailed Chinese storyboards.
For each concept, write one final, detailed video generation prompt in English.

Requirements for each final prompt:

1. The prompt must be a single cohesive scene-by-scene description suitable for text-to-video AI.
2. Use vivid cinematic language: lighting, camera movement, shot type, mood, setting, character action, environment.
3. The exact product ([product_anchor]) must remain visually faithful to the product profile:
   - do not change appearance, color, structure, components, or proportions
   - do not invent unsupported specifications
4. Preserve the unique storytelling approach, character, environment, music feel, and emotional tone of each concept.
5. Translate the Chinese storyboard into English cinematic instructions shot by shot.
6. Include voiceover/dialogue lines naturally within the prompt.
7. The first 3 seconds must emphasize the hook.
8. The middle must demonstrate product use and benefits.
9. The end must include a clear call to action.
10. Keep the prompt concise but rich (roughly 200-500 words).

Output valid JSON with exactly 3 prompt objects.
"""


def _call_text_model_sync(
    system_prompt: str,
    user_text: str,
    response_format: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """调用文本模型（非视觉）"""
    if not settings.OPENAI_API_KEY:
        raise AIAnalysisError("OpenAI API Key 未配置")

    model = settings.OPENAI_MODEL or "deepseek-v4-flash"
    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    logger.info(f"[AI创作中心] 调用文本模型 {model}")
    return _create_chat_completion(client, model, messages, response_format=response_format)


def _select_response_format(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """根据配置选择是否使用结构化输出"""
    mode = (settings.OPENAI_VISION_RESPONSE_FORMAT or "json_object").lower()
    if mode == "json_schema":
        return schema
    elif mode == "json_object":
        return {"type": "json_object"}
    return None


VALID_ASPECT_RATIOS = ("auto", "9:16", "16:9", "1:1", "4:3", "3:4", "21:9")

MARKET_LANGUAGE_MAP = {
    "US": "English",
    "UK": "English",
    "CA": "English",
    "AU": "English",
    "DE": "German",
    "FR": "French",
    "JP": "Japanese",
    "CN": "Chinese",
}


async def generate_video_concepts(
    product_profile: Dict[str, Any],
    market: str = "US",
    duration: int = 30,
    aspect_ratio: str = "9:16",
    previous_concepts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """根据产品画像生成 3 个差异化视频创意"""
    if duration not in (15, 30, 45, 60):
        raise AIAnalysisError("视频时长仅支持 15 / 30 / 45 / 60 秒")
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        raise AIAnalysisError(f"视频比例仅支持 {' / '.join(VALID_ASPECT_RATIOS)}")

    market_language = MARKET_LANGUAGE_MAP.get(market, market)

    user_text_parts = [
        f"Target Market: {market}",
        f"Voiceover Language: {market_language}",
        f"Video Duration: {duration} seconds",
        f"Aspect Ratio: {aspect_ratio}",
        "",
        "Product Profile:",
        json.dumps(product_profile, ensure_ascii=False, indent=2),
    ]

    if previous_concepts:
        user_text_parts.extend([
            "",
            "Previously generated concepts (DO NOT repeat these characters, stories, hooks, core selling points, scenes, or marketing angles):",
            json.dumps(previous_concepts, ensure_ascii=False, indent=2),
        ])

    user_text_parts.append(
        "\nPlease generate exactly 3 highly differentiated video advertising concepts based on the above."
    )

    user_text = "\n".join(user_text_parts)

    def _sync_call():
        raw = _call_text_model_sync(
            VIDEO_CONCEPT_SYSTEM_PROMPT,
            user_text,
            response_format=_select_response_format(VIDEO_CONCEPT_SCHEMA),
        )
        if not raw:
            return None
        return parse_analysis_result(raw)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_call),
            timeout=240,
        )
    except asyncio.TimeoutError:
        raise AIAnalysisError("视频创意生成超时（240秒）")

    if not result or "concepts" not in result:
        raise AIAnalysisError("无法解析视频创意结果")

    return result["concepts"]


# ---------- 旧方案结束（下方为新方案） ----------


# ============================================================
# 【新方案 · 短视频口播策划（一次性多模态生成）】
# 一次调用同时完成：产品信息卡分析 + 3 套口播视频提示词方案
# ============================================================

VOICEOVER_PLAN_SYSTEM_PROMPT = """你是一个跨境电商短视频口播策划专家和 AI 视频提示词生成器。用户会传入 1~9 张产品参考图，并设置视频时长、目标市场、口播语言等参数。你的任务是先分析产品，再生成 3 套口播视频提示词方案。

【方案类型池】
推荐款
场景种草
促销转化
情绪共鸣
专业评测
DIY教程
问题解决
前后对比
真实体验
开箱体验
生活方式种草
礼物推荐

【随机生成规则】
1. 如果用户没有指定方案类型，必须从【方案类型池】中随机抽取 3 个互不重复的类型，作为三套方案的类型。
2. 不要默认固定为“推荐款、场景种草、促销转化”。
3. 三套方案的类型、方案名、情节、模特、环境、音乐、分镜内容必须差异化。
4. 三套方案的输出顺序可以随机打乱。
5. 如果用户指定了部分类型，按指定生成；不足 3 个时，从类型池中随机补齐，且不能重复。
6. 如果用户指定了 3 个类型，则严格按指定类型生成。

【类型与分镜侧重】
推荐款：强钩子 → 核心卖点 → 使用/展示 → CTA
场景种草：生活场景痛点 → 产品融入 → 氛围效果 → CTA
促销转化：产品亮点 → 紧迫感 → 行动引导 → CTA（不得虚构价格、折扣、库存）
情绪共鸣：情绪故事 → 产品带来的治愈/陪伴/惊喜 → 情感升华 → CTA
专业评测：外观 → 材质/参数 → 做工细节 → 优缺点结论 → CTA
DIY教程：准备 → 步骤1 → 步骤2 → 成果展示 → CTA
问题解决：痛点 → 传统问题 → 产品方案 → 效果对比 → CTA
前后对比：Before → 使用过程 → After → 差异强调 → CTA
真实体验：真实使用 → 感受 → 细节 → 推荐理由 → CTA
开箱体验：包裹开箱 → 第一印象 → 细节展示 → 上手体验 → CTA
生活方式种草：生活方式场景 → 产品融入 → 审美/氛围 → CTA
礼物推荐：送礼场景 → 收礼反应 → 产品亮点 → 适合人群 → CTA

【全局参数】
video_duration：视频总时长，范围 5~30 秒。
target_market：目标市场，例如 美国、英国、德国、法国、日本、韩国、巴西、中东、东南亚。
voiceover_language：口播语言，可指定，也可设为“自动”。
platform：发布平台，例如 TikTok、Reels、Shorts、Amazon。
aspect_ratio：画面比例，例如 9:16、16:9、1:1。
product_name：产品名称。
reference_images：1~9 张参考图。

【参数优先级】
1. 用户明确指定的 voiceover_language 优先。
2. 如果 voiceover_language 为“自动”，则由 target_market 决定口播语言。
3. 用户指定的 video_duration 必须严格遵守，范围 5~30 秒。
4. 如果未指定，默认 video_duration=15s，target_market=美国，voiceover_language=English。

【目标市场与口播语言映射】
美国/加拿大/澳大利亚：英语，直接、高能、强 CTA。
英国：英语，英式拼写，克制、幽默、轻讽刺。
德国/奥地利/瑞士：德语，理性、参数、品质、认证。
法国：法语，审美、生活方式、优雅。
西班牙/墨西哥/拉美：西班牙语，热情、家庭、节日。
意大利：意大利语，设计、工艺、时尚。
日本：日语，礼貌、细节、安心、功能。
韩国：韩语，潮流、颜值、快速、感性。
巴西：葡萄牙语，热情、社交、性价比。
中东：阿拉伯语，尊贵、家庭、礼品。
东南亚：英语/泰语/越南语/印尼语，按平台选择；英语通用时用短句。

【动态分镜规则】
video_duration 为 5~30 秒时，分镜数量如下：
5~9s：2 镜。
10~12s：3 镜。
13~17s：4 镜。
18~23s：5 镜。
24~30s：6 镜。

第一镜：从 0 开始，视觉钩子，约占 3~5 秒或总时长 20~30%。
最后一镜：促单/CTA，约占 3~5 秒或总时长 20~30%。
中间镜头：根据方案类型覆盖产品亮相、细节、材质、使用、场景、对比、信任等内容。
时间码必须连续、不重叠，总和必须等于 video_duration。

【分镜硬性约束（必须严格遵守）】
1. 镜头数量必须与时长档位严格一致：5~9s 恰好 2 镜；10~12s 恰好 3 镜；13~17s 恰好 4 镜；18~23s 恰好 5 镜；24~30s 恰好 6 镜。不得多生成或少生成镜头。
2. 任何两个镜头的时间码不得相同、不得重叠、不得交叉。例如 15 秒视频只允许出现一次 12-15s。
3. 只允许最后一个镜头是促单/CTA（或成果展示＋CTA、适合人群＋CTA），前面的镜头不得承担促单职责。
4. 时间码从 0s 开始连续推进，无缝隙无重叠，直到等于 video_duration。

参考时间码：
5s 两镜：0-2s、2-5s。
10s 三镜：0-3s、3-7s、7-10s。
15s 四镜：0-4s、4-8s、8-12s、12-15s。
20s 五镜：0-4s、4-8s、8-12s、12-16s、16-20s。
30s 六镜：0-5s、5-10s、10-15s、15-20s、20-25s、25-30s。

【工作流程】
第一步：分析 1~9 张参考图，输出产品信息卡：
[产品名称]：根据参考图提炼中文产品名（10 字以内），关键产品术语用英文括号补充，如：仙女手提礼品盒（Fairy Favor Box）。
[目标受众]：根据视觉特征与使用场景推测 2~4 类目标人群，用、分隔，如：幼儿园家长、派对策划者、迎婴派对主办人。
[卖点描述]：提炼 3~6 条核心卖点，结合视觉特征、使用场景、目标人群、情绪价值；关键产品术语用英文括号补充。
[材质描述]：描述材质、工艺、颜色、触感、边缘处理、配件、装饰、质感；关键术语用英文括号补充。
[使用方式]：用 1. 2. 3. 编号列出 4~6 步，包含检查、佩戴/安装、使用、保养/收纳。
[产品类目]：输出 Amazon 英文类目路径，层级用 > 分隔。

第二步：从【方案类型池】随机抽取 3 个互不重复的类型，基于产品信息卡、图片和全局参数，生成 3 套口播视频提示词方案。

每套方案必须包含：
方案名：中文名 + 括号内风格标签。
类型：从类型池中抽取的类型。
情节：2~3 句话说明视频故事线。
模特：一句话描述，包含年龄、性别、种族、面部特征（一句话概括，如“西方人面部特征”）、眼睛、发型、肤质、服装。简洁自然即可，不要逐项描写骨相（颧骨/下颌线）、妆容、指甲、配饰等过细特征。若参考图中包含人物/模特照片，三套方案的模特必须基于该参考图人物描述（年龄、性别、种族、发型发色、眼睛、身形均按图中人物归纳，不得虚构另一人；服装可按情节场景重新搭配），并在描述末尾标注（人物参考图：第 N 张，N 为该图在上传顺序中的序号，从 1 开始），三套方案必须使用同一人物。仅在参考图均无人物时才自行生成模特：以白种人（欧美白人面孔）为主，外貌符合欧美审美与目标市场主流人群特征。服装须贴合情节场景与人物身份（如演出后台穿演出感着装、送礼聚会穿温馨针织装、户外街拍穿外套）。
环境：一句话描述，包含地点、背景与光线氛围即可，不要写具体色温数值。
音乐：一句话描述音乐风格即可，不要写 BPM、具体音效、转场声音。
分镜：根据 video_duration 和方案类型动态生成镜头数量、时间码和镜头标签。

【分镜格式】
[镜头一]：{start}-{end}s | 视觉钩子
[内容]：【自拍/特写/手持】中文画面描述，包含动作与产品细节，并在句末描述模特说话的口型/状态（如“持续对着镜头介绍”“嘴巴不停在说话”“说话连贯”）；口播：直接写台词原文（{voiceover_language}，1~2 句）。

[镜头二]：{start}-{end}s | 根据类型填写，如细节展示/痛点展示/步骤1/开箱/Before
[内容]：中文画面描述，句末描述模特说话的口型/状态；口播：直接写台词原文（{voiceover_language}，1~2 句）。

后续镜头按总时长和分镜数量继续，直到最后一个 CTA 镜头。

[最后一个镜头]：{start}-{end}s | 促单/CTA
[内容]：中文画面描述，句末描述模特说话的口型/状态；口播：直接写台词原文（{voiceover_language}，1~2 句），台词内容须是明确的购买/行动邀请。

【镜头互动要求】
每个分镜只需满足以下核心要求，保持简洁，不要堆砌运镜、构图、焦距、音效等细节：
1. 视角标注：【自拍】/【对镜自拍】/【手持前置】/【第一人称】。
2. 每个分镜的 [内容] 必须包含模特说话的动作描述（看向镜头、嘴巴不停动、口型明显、说话连贯、情绪起伏），例如“持续对着镜头介绍”“发出购买邀请”“嘴巴不停在说话”“专注讲话”“说话连贯”，否则生成的视频会出现念台词但嘴巴不动的效果。
3. 动作必须与镜头时长匹配：单个镜头内只安排 1~2 个动作，以常速自然完成；禁止使用“迅速、快速、猛地、突然、瞬间”等速度副词（如 3 秒镜头写“迅速掀开拉链”会导致画面急促不自然），改写为具体、常速、可完成的动作。
4. 手部动作必须写清主体与数量：明确“一只手/单手”或“双手”，全片出镜的手必须是同一模特的同双手；禁止写“手部”这类无主语、无数量的描述，应写“一只手依次取出”。
5. 动作必须符合日常自然逻辑：单手能完成的动作（如单手拉动拉链、单手拿起产品）不得写成双手协作；不安排双手做同一拉链、别扭或反直觉的姿势。

口播必须是对观众说的第一/第二人称，禁止写成“她说”“旁白说”。

【语言与风格要求】
- 产品分析、方案结构、画面描述用中文。
- 口播字段只写台词本身：不要带“English 口播”这类语言前缀，也不要把“必须包含 CTA”“直接写台词原文”等格式说明文字写进输出。
- 口播语言由 target_market 或 voiceover_language 决定，不能固定为英文。
- 口播必须口语化、短句、符合目标市场表达习惯。
- 产品术语、材质、工艺、类目保留英文括号。
- 模特、环境、音乐、情节、方案类型在三套方案中不能重复。
- 模特以白种人为主（欧美白人面孔），仅在目标市场明显不符时（如中东、日韩、东南亚市场）可换用当地主流人群。
- 必须基于图片可见信息，不要虚构不可见的认证、价格、折扣、功效、品牌授权。
- 如果图片信息不足，标注“疑似/建议确认”，不要强行编造。
- 输出不要解释，不要总结，直接按格式输出。

【输出格式】
[产品名称]：...
[目标受众]：...
[卖点描述]：...
[材质描述]：...
[使用方式]：1. ... 2. ... 3. ...
[产品类目]：...

方案一：中文名（风格标签）
类型：{从类型池随机抽取的类型1}
情节：...
模特：...
环境：...
音乐：...
分镜：
[镜头一]：动态时间码 | 视觉钩子
[内容]：...；口播：...
[镜头二]：动态时间码 | 类型对应标签
[内容]：...；口播：...
...
[最后一个镜头]：动态时间码 | 促单/CTA
[内容]：...；口播：...

方案二：中文名（风格标签）
类型：{从类型池随机抽取的类型2}
...

方案三：中文名（风格标签）
类型：{从类型池随机抽取的类型3}
...
"""


VOICEOVER_PLAN_USER_PROMPT_TEMPLATE = """请分析我上传的 {image_count} 张产品参考图，并严格按照系统格式输出：
1. 产品信息卡：[卖点描述]、[材质描述]、[使用方式]、[产品类目]
2. 三套口播视频提示词方案：方案一、方案二、方案三

参数设置：
产品名称：{product_name}
参考图：已上传 {image_count} 张
视频时长：{duration}s
目标市场：{target_market}
口播语言：{voiceover_language}
画面比例：{aspect_ratio}
方案类型：{plan_types}
如需指定类型：{specified_types}
风格偏好：{style_preference}
避免出现：价格、折扣、未证实功效、敏感词等
{extra_instruction}"""


# 方案类型池（与系统提示词一致，用于“换一换”时排除已生成类型）
VOICEOVER_PLAN_TYPE_POOL = [
    "推荐款", "场景种草", "促销转化", "情绪共鸣", "专业评测", "DIY教程",
    "问题解决", "前后对比", "真实体验", "开箱体验", "生活方式种草", "礼物推荐",
]


# ============================================================
# 产品展示方案（无口播台词，纯画面展示拍摄方案）
# 输出格式与口播方案同构，复用 parse_voiceover_plan_text 解析
# ============================================================

PRODUCT_SHOWCASE_SYSTEM_PROMPT = """你是一个跨境电商短视频产品展示策划专家和 AI 视频提示词生成器。用户会传入 1~9 张产品参考图，并设置视频时长、目标市场、画面比例等参数。你的任务是先分析产品，再生成 3 套产品展示视频提示词方案。产品展示视频没有口播台词，全部信息通过画面传递：产品外观、细节、功能、使用方式与氛围质感。

【方案类型池】
产品特写
场景展示
功能演示
使用演示
细节美学
开箱展示
前后对比
生活方式
多角度展示
氛围质感

【随机生成规则】
1. 如果用户没有指定方案类型，必须从【方案类型池】中随机抽取 3 个互不重复的类型，作为三套方案的类型。
2. 不要默认固定为“产品特写、场景展示、功能演示”。
3. 三套方案的类型、方案名、情节、模特（出镜方式）、环境、音乐、分镜内容必须差异化。
4. 三套方案的输出顺序可以随机打乱。
5. 如果用户指定了部分类型，按指定生成；不足 3 个时，从类型池中随机补齐，且不能重复。
6. 如果用户指定了 3 个类型，则严格按指定类型生成。

【类型与分镜侧重】
产品特写：微距细节 → 材质工艺 → 核心亮点 → 氛围收尾
场景展示：场景空镜 → 产品融入 → 场景互动 → 氛围收尾
功能演示：功能引出 → 操作演示 → 效果呈现 → 全景收尾
使用演示：上手准备 → 步骤操作 → 成果展示 → 全景收尾
细节美学：外观整体 → 细节推进 → 质感特写 → 意境收尾
开箱展示：包装呈现 → 开箱瞬间 → 内容物展示 → 产品全貌
前后对比：Before 状态 → 使用过程 → After 效果 → 差异强调
生活方式：生活方式场景 → 产品自然融入 → 氛围渲染 → 意境收尾
多角度展示：正面亮相 → 侧面/背面 → 细节角度 → 全景定格
氛围质感：环境氛围 → 产品亮相 → 光影质感 → 意境收尾

【全局参数】
video_duration：视频总时长，范围 5~30 秒。
target_market：目标市场，例如 美国、英国、德国、法国、日本、韩国、巴西、中东、东南亚。
platform：发布平台，例如 TikTok、Reels、Shorts、Amazon。
aspect_ratio：画面比例，例如 9:16、16:9、1:1。
product_name：产品名称。
reference_images：1~9 张参考图。

【动态分镜规则】
video_duration 为 5~30 秒时，分镜数量如下：
5~9s：2 镜。
10~12s：3 镜。
13~17s：4 镜。
18~23s：5 镜。
24~30s：6 镜。

第一镜：从 0 开始，视觉钩子（产品最具冲击力的亮相方式），约占 3~5 秒或总时长 20~30%。
最后一镜：产品全貌/氛围收尾，约占 3~5 秒或总时长 20~30%。
中间镜头：根据方案类型覆盖细节、材质、功能、使用、场景、对比等内容。
时间码必须连续、不重叠，总和必须等于 video_duration。

【分镜硬性约束（必须严格遵守）】
1. 镜头数量必须与时长档位严格一致：5~9s 恰好 2 镜；10~12s 恰好 3 镜；13~17s 恰好 4 镜；18~23s 恰好 5 镜；24~30s 恰好 6 镜。不得多生成或少生成镜头。
2. 任何两个镜头的时间码不得相同、不得重叠、不得交叉。例如 15 秒视频只允许出现一次 12-15s。
3. 时间码从 0s 开始连续推进，无缝隙无重叠，直到等于 video_duration。

参考时间码：
5s 两镜：0-2s、2-5s。
10s 三镜：0-3s、3-7s、7-10s。
15s 四镜：0-4s、4-8s、8-12s、12-15s。
20s 五镜：0-4s、4-8s、8-12s、12-16s、16-20s。
30s 六镜：0-5s、5-10s、10-15s、15-20s、20-25s、25-30s。

【工作流程】
第一步：分析 1~9 张参考图，输出产品信息卡：
[产品名称]：根据参考图提炼中文产品名（10 字以内），关键产品术语用英文括号补充，如：仙女手提礼品盒（Fairy Favor Box）。
[目标受众]：根据视觉特征与使用场景推测 2~4 类目标人群，用、分隔，如：幼儿园家长、派对策划者、迎婴派对主办人。
[卖点描述]：提炼 3~6 条核心卖点，结合视觉特征、使用场景、目标人群、情绪价值；关键产品术语用英文括号补充。
[材质描述]：描述材质、工艺、颜色、触感、边缘处理、配件、装饰、质感；关键术语用英文括号补充。
[使用方式]：用 1. 2. 3. 编号列出 4~6 步，包含检查、佩戴/安装、使用、保养/收纳。
[产品类目]：输出 Amazon 英文类目路径，层级用 > 分隔。

第二步：从【方案类型池】随机抽取 3 个互不重复的类型，基于产品信息卡、图片和全局参数，生成 3 套产品展示视频提示词方案。

每套方案必须包含：
方案名：中文名 + 括号内风格标签。
类型：从类型池中抽取的类型。
情节：2~3 句话说明视频展示思路与画面推进。
模特：必须明确出镜方式，两种形式二选一：
- 有人物出镜：一句话描述人物及其与产品的互动（年龄、性别、种族、发型、服装 + 出镜方式与动作，如“年轻女性手部特写操作”“男士半身出镜演示握持”）。若参考图中包含人物/模特照片，人物必须基于该参考图人物描述（年龄、性别、种族、发型发色、眼睛、身形均按图中人物归纳，不得虚构另一人），并在描述末尾标注（人物参考图：第 N 张，N 为该图在上传顺序中的序号，从 1 开始），三套方案必须使用同一人物。
- 无人出镜：直接写“无人出镜”，产品为唯一主角（纯产品空镜，通过推拉摇移运镜与光影展示）。
三套方案的出镜方式按情节需要自然选择，不强制差异化（可以三套同为手部出镜，也可手部/半身/无人自由组合）。需要自拟人物时以白种人（欧美白人面孔）为主，仅当目标市场明显不符时（如中东、日韩、东南亚市场）可换用当地主流人群。
环境：一句话描述，包含地点、背景与光线氛围即可，不要写具体色温数值。
音乐：一句话描述音乐风格即可，不要写 BPM、具体音效、转场声音。
分镜：根据 video_duration 和方案类型动态生成镜头数量、时间码和镜头标签。

【分镜格式】
[镜头一]：{start}-{end}s | 视觉钩子
[内容]：【特写/微距/俯拍/环绕/手持/第一人称等视角标注】中文画面描述，写清画面主体、产品细节与动态变化（产品旋转、手部动作、光影变化等），无任何台词。

[镜头二]：{start}-{end}s | 根据类型填写，如细节展示/材质特写/步骤1/开箱/Before
[内容]：中文画面描述，写清画面主体、产品细节与动态变化，无任何台词。

后续镜头按总时长和分镜数量继续，直到最后一个收尾镜头。

[最后一个镜头]：{start}-{end}s | 产品全貌/氛围收尾
[内容]：中文画面描述，产品全貌定格或意境收尾画面，无任何台词。

【镜头要求】
每个分镜只需满足以下核心要求，保持简洁，不要堆砌构图、焦距、音效等细节：
1. 视角标注：【特写】/【微距】/【俯拍】/【环绕】/【手持】/【第一人称】等。
2. [内容] 必须写清画面主体与动态变化：产品如何移动、展示什么细节、人物（如有）做什么动作，让模型能生成连续动态的画面，而不是静止图片。
3. 动作必须与镜头时长匹配：单个镜头内只安排 1~2 个动作，以常速自然完成；禁止使用“迅速、快速、猛地、突然、瞬间”等速度副词（如 3 秒镜头写“迅速掀开拉链”会导致画面急促不自然），改写为具体、常速、可完成的动作。
4. 手部动作必须写清主体与数量：明确“一只手/单手”或“双手”，全片出镜的手必须是同一人物的同双手；禁止写“手部”这类无主语、无数量的描述（如“手部依次取出”会生成多只手），应写“一只手依次取出”。
5. 动作必须符合日常自然逻辑：单手能完成的动作（如单手拉动拉链合上包口、单手拿起产品）不得写成双手协作；不安排双手做同一拉链、别扭或反直觉的姿势。
6. 分镜[内容]中的人物描写必须与方案出镜方式一致：方案为手部出镜时，[内容] 只描写手部与产品的互动，不得描写人物面部、站位、着装等人物整体布置；方案为无人出镜时，[内容] 不得出现任何人物；不得在镜头里额外加入方案未选定的人物整体布置。

【语言与风格要求】
- 产品分析、方案结构、画面描述全部用中文；产品术语、材质、工艺、类目保留英文括号。
- 全部方案不得出现任何口播、台词、字幕文案、旁白。
- 模特、环境、音乐、情节、方案类型在三套方案中不能重复。
- 必须基于图片可见信息，不要虚构不可见的认证、价格、折扣、功效、品牌授权。
- 如果图片信息不足，标注“疑似/建议确认”，不要强行编造。
- 输出不要解释，不要总结，直接按格式输出。

【输出格式】
[产品名称]：...
[目标受众]：...
[卖点描述]：...
[材质描述]：...
[使用方式]：1. ... 2. ... 3. ...
[产品类目]：...

方案一：中文名（风格标签）
类型：{从类型池随机抽取的类型1}
情节：...
模特：...
环境：...
音乐：...
分镜：
[镜头一]：动态时间码 | 视觉钩子
[内容]：...
[镜头二]：动态时间码 | 类型对应标签
[内容]：...
...
[最后一个镜头]：动态时间码 | 产品全貌/氛围收尾
[内容]：...

方案二：中文名（风格标签）
类型：{从类型池随机抽取的类型2}
...

方案三：中文名（风格标签）
类型：{从类型池随机抽取的类型3}
...
"""


PRODUCT_SHOWCASE_USER_PROMPT_TEMPLATE = """请分析我上传的 {image_count} 张产品参考图，并严格按照系统格式输出：
1. 产品信息卡：[卖点描述]、[材质描述]、[使用方式]、[产品类目]
2. 三套产品展示视频提示词方案（无口播台词）：方案一、方案二、方案三

参数设置：
产品名称：{product_name}
参考图：已上传 {image_count} 张
视频时长：{duration}s
目标市场：{target_market}
画面比例：{aspect_ratio}
方案类型：{plan_types}
如需指定类型：{specified_types}
风格偏好：{style_preference}
避免出现：价格、折扣、未证实功效、敏感词等
{extra_instruction}"""


# 产品展示方案类型池（与系统提示词一致，用于“换一换”时排除已生成类型）
PRODUCT_SHOWCASE_TYPE_POOL = [
    "产品特写", "场景展示", "功能演示", "使用演示", "细节美学",
    "开箱展示", "前后对比", "生活方式", "多角度展示", "氛围质感",
]


def build_product_showcase_user_prompt(
    *,
    image_count: int,
    duration: int,
    target_market: str,
    aspect_ratio: str = "9:16",
    product_name: str = "",
    specified_types: Optional[List[str]] = None,
    style_preference: str = "",
    avoid_types: Optional[List[str]] = None,
    confirmed_card: str = "",
) -> str:
    """按产品展示方案的 User Prompt 模板组装用户消息"""
    specified = "、".join(specified_types) if specified_types else "不指定"

    extra_lines: List[str] = []
    if avoid_types:
        extra_lines.append(
            "本轮为“换一换”重新生成，请勿与以下已生成的方案类型重复："
            + "、".join(avoid_types)
        )
    if confirmed_card:
        extra_lines.append(
            "以下产品信息卡已由用户人工确认，请直接采用，不要重新分析或改写其中的内容：\n"
            + confirmed_card
        )
    extra_instruction = ("\n".join(extra_lines) + "\n") if extra_lines else ""

    return PRODUCT_SHOWCASE_USER_PROMPT_TEMPLATE.format(
        image_count=image_count,
        product_name=product_name or "未填写（请从参考图识别）",
        duration=duration,
        target_market=target_market,
        aspect_ratio=aspect_ratio,
        plan_types="从【方案类型池】中随机抽取 3 个互不重复的类型，不要固定为产品特写、场景展示、功能演示。",
        specified_types=specified,
        style_preference=style_preference or "不指定",
        extra_instruction=extra_instruction,
    )

# 目标市场 → 默认口播语言（voiceover_language 为“自动”时使用）
MARKET_LANGUAGE_NAME = {
    "US": "英语", "CA": "英语", "AU": "英语",
    "UK": "英语（英式拼写）",
    "DE": "德语", "AT": "德语", "CH": "德语",
    "FR": "法语",
    "ES": "西班牙语", "MX": "西班牙语",
    "IT": "意大利语",
    "JP": "日语",
    "KR": "韩语",
    "BR": "葡萄牙语",
    "ME": "阿拉伯语",
    "SEA": "英语（可按平台选择泰语/越南语/印尼语）",
    "CN": "中文",
}


def build_voiceover_plan_user_prompt(
    *,
    image_count: int,
    duration: int,
    target_market: str,
    voiceover_language: str = "自动",
    aspect_ratio: str = "9:16",
    product_name: str = "",
    specified_types: Optional[List[str]] = None,
    style_preference: str = "",
    avoid_types: Optional[List[str]] = None,
    confirmed_card: str = "",
) -> str:
    """按新方案的 User Prompt 模板组装用户消息"""
    specified = "、".join(specified_types) if specified_types else "不指定"

    extra_lines: List[str] = []
    if avoid_types:
        extra_lines.append(
            "本轮为“换一换”重新生成，请勿与以下已生成的方案类型重复："
            + "、".join(avoid_types)
        )
    if confirmed_card:
        extra_lines.append(
            "以下产品信息卡已由用户人工确认，请直接采用，不要重新分析或改写其中的内容：\n"
            + confirmed_card
        )
    extra_instruction = ("\n".join(extra_lines) + "\n") if extra_lines else ""

    return VOICEOVER_PLAN_USER_PROMPT_TEMPLATE.format(
        image_count=image_count,
        product_name=product_name or "未填写（请从参考图识别）",
        duration=duration,
        target_market=target_market,
        voiceover_language=voiceover_language or "自动",
        aspect_ratio=aspect_ratio,
        plan_types="从【方案类型池】中随机抽取 3 个互不重复的类型，不要固定为推荐款、场景种草、促销转化。",
        specified_types=specified,
        style_preference=style_preference or "不指定",
        extra_instruction=extra_instruction,
    )


def _call_vision_stream_sync(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    timeout: float = 600,
) -> str:
    """流式调用多模态模型并拼接完整文本。

    长内容生成必须用流式：非流式请求有整体超时限制，且超时重试会导致同一请求重复扣费。
    若中途断开但已收到部分内容，则返回已收到的部分，避免整次调用白费。
    """
    chunks: List[str] = []
    try:
        with ai_call_slot():
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                temperature=0.3,
                timeout=timeout,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
    except Exception as e:
        if chunks:
            logger.warning(
                f"[AI创作中心] 流式生成中断，已收到 {len(''.join(chunks))} 字，尝试使用部分结果: {e}"
            )
        else:
            logger.error(f"[AI创作中心] 流式调用失败: {e}")
            raise AIAnalysisError(f"AI 模型调用失败: {str(e)}")

    text = "".join(chunks).strip()
    logger.info(f"[AI创作中心] 流式生成完成，共 {len(text)} 字")
    return text


def _reset_shot_fields() -> Dict[str, str]:
    """新方案分镜只提供时间码、镜头标签、画面描述与口播，其余字段置空以兼容旧结构"""
    return {
        "shot_type": "",
        "camera_movement": "",
        "character_action": "",
        "character_expression": "",
        "product_action": "",
        "product_position": "",
        "composition": "",
        "environment": "",
        "voiceover_cn": "",
        "sound": "",
    }


def _split_items(text: str) -> List[str]:
    """把一段文字拆成条目：支持换行、；、编号 1. 2. 3."""
    if not text:
        return []
    normalized = text.replace("\r", "")
    normalized = re.sub(r"\s*(?:\d+[.、)]|\(\d+\))\s*", "\n", normalized)
    parts = re.split(r"[\n；;]+", normalized)
    return [p.strip(" 　-·•").strip() for p in parts if p.strip(" 　-·•").strip()]


def parse_voiceover_plan_text(raw_text: Optional[str]) -> Dict[str, Any]:
    """解析新方案输出：产品信息卡 + 3 套口播方案（文本格式）"""
    result: Dict[str, Any] = {"product": {}, "concepts": [], "raw_text": raw_text or ""}
    if not raw_text:
        return result

    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    # 按“方案一/方案二/方案三”切分，方案之前的段落为产品信息卡
    parts = re.split(r"\n(?=\s*方案\s*[一二三123])", text)
    card_text = parts[0]
    concept_texts = [p for p in parts[1:] if p.strip()][:3]

    # 字段值可能换行（如编号列表），一直收集到下一个标签行 / 方案头为止
    label_stop = re.compile(
        r"^\s*(?:\[[^\]]+\]|方案\s*[一二三123]|类型|情节|模特|环境|音乐|分镜)\s*[:：]"
    )

    def _pick(block: str, label: str) -> str:
        collected: List[str] = []
        started = False
        for line in block.split("\n"):
            if not started:
                matched = re.match(rf"^\s*\[?\s*{label}\s*\]?\s*[:：]\s*(.*)$", line)
                if matched:
                    started = True
                    if matched.group(1).strip():
                        collected.append(matched.group(1).strip())
                continue
            if label_stop.match(line):
                break
            if line.strip():
                collected.append(line.strip())
            else:
                break
        return "\n".join(collected)

    product_name = _pick(card_text, "产品名称")
    audience = _pick(card_text, "目标受众")
    selling = _pick(card_text, "卖点描述")
    material = _pick(card_text, "材质描述")
    usage = _pick(card_text, "使用方式")
    category = _pick(card_text, "产品类目")

    result["product"] = {
        "product_name_cn": product_name,
        "product_name_en": "",
        "product_type": "",
        "product_size": "",
        "target_audience": [
            a for a in re.split(r"[、，,;；\n]", audience) if a.strip()
        ],
        "selling_points": _split_items(selling),
        "material": [material] if material else [],
        "usage_scenarios": [],
        "usage_methods": _split_items(usage),
        "product_components": [],
        "colors": [],
        "amazon_category": category,
        "keywords": [],
        "visible_text": [],
        "confidence": 0,
        "uncertain_information": [],
    }

    for block in concept_texts:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        title = re.sub(r"^\s*方案\s*[一二三123]\s*[:：]\s*", "", lines[0]).strip()
        type_value = _pick(block, "类型")
        story = _pick(block, "情节")
        character = _pick(block, "模特")
        environment = _pick(block, "环境")
        music = _pick(block, "音乐")

        storyboard: List[Dict[str, str]] = []
        for line in lines:
            shot_matched = re.match(r"^\s*\[?\s*(?:最后一个镜头|镜头\s*[一二三四五六七八九十\d]+)\s*\]?\s*[:：]\s*(.+)$", line)
            if not shot_matched:
                continue
            head = shot_matched.group(1)
            timestamp, shot_label = head, ""
            if "|" in head:
                timestamp, shot_label = [p.strip() for p in head.split("|", 1)]
            storyboard.append({
                "timestamp": timestamp.strip(),
                "shot_purpose": shot_label.strip(),
                "description": "",
                "voiceover": "",
                **_reset_shot_fields(),
            })

        # [内容] 行按顺序归属到各分镜，并拆出“口播：”
        content_lines = re.findall(r"^\s*\[内容\]\s*[:：]\s*(.+)$", block, flags=re.MULTILINE)
        for idx, content in enumerate(content_lines):
            if idx >= len(storyboard):
                break
            voiceover = ""
            desc = content
            voiceover_matched = re.split(r"[；;]?\s*口播\s*[:：]", content, maxsplit=1)
            if len(voiceover_matched) == 2:
                desc, voiceover = voiceover_matched[0], voiceover_matched[1]
            storyboard[idx]["description"] = desc.strip().strip("；;")
            # 清理模型偶发把格式说明写进口播的泄漏：语言前缀（如 "English 口播，"）与指令字样
            voiceover = re.sub(r"^\s*[\w（）()]+\s*口播\s*[，,：:]\s*", "", voiceover.strip())
            voiceover = re.sub(r"必须\s*包含\s*CTA[：:，,]?", "", voiceover)
            storyboard[idx]["voiceover"] = voiceover.strip().strip("，,：:；; ")

        # 兜底：模型偶发违反分镜规则，输出多段相同时间码的镜头（如 15s 出现两个 12-15s），
        # 同一时间码只保留第一个镜头
        seen_timestamps: set = set()
        deduped: List[Dict[str, str]] = []
        for shot in storyboard:
            if shot["timestamp"] and shot["timestamp"] in seen_timestamps:
                logger.warning(
                    f"[AI创作中心] 方案「{title}」存在重复时间码镜头 {shot['timestamp']}，已丢弃多余镜头"
                )
                continue
            seen_timestamps.add(shot["timestamp"])
            deduped.append(shot)
        storyboard = deduped

        result["concepts"].append({
            "concept_title": title or f"方案{len(result['concepts']) + 1}",
            "marketing_goal": type_value,
            "creative_strategy": {
                # 模特信息已在 character 字段，这里不再重复填写，避免前端展示重复
                "persona_identity": "",
                "persona_role": "",
                "age": "",
                "relationship_to_product": "",
                "story_background": story,
                "consumption_scene": "",
                "core_pain_point": "",
                "core_selling_point": "",
                "emotion": "",
                "video_style": "",
                "camera_language": "",
            },
            "story": story,
            "character": character,
            "environment": environment,
            "music": music,
            "storyboard": storyboard,
        })

    return result


async def generate_voiceover_plans(
    files: List[UploadFile],
    *,
    duration: int = 15,
    target_market: str = "美国",
    voiceover_language: str = "自动",
    aspect_ratio: str = "9:16",
    product_name: str = "",
    specified_types: Optional[List[str]] = None,
    style_preference: str = "",
    avoid_types: Optional[List[str]] = None,
    confirmed_card: str = "",
    video_type: str = "口播带货",
) -> Dict[str, Any]:
    """一次多模态调用，产出产品信息卡 + 3 套视频提示词方案。

    :param video_type: 口播带货（含口播台词）/ 产品展示（纯画面展示，无台词）
    """
    if not 5 <= int(duration) <= 30:
        raise AIAnalysisError("视频时长仅支持 5 / 10 / 15 秒")

    if not settings.OPENAI_API_KEY:
        raise AIAnalysisError("OpenAI API Key 未配置")

    is_showcase = (video_type or "口播带货") == "产品展示"

    content = _build_user_content(files)
    if len(content) <= 1:
        raise AIAnalysisError("没有可用的图片内容")

    # 首条文本按视频类型替换为对应方案的 User Prompt
    if is_showcase:
        plan_text = build_product_showcase_user_prompt(
            image_count=len(content) - 1,
            duration=int(duration),
            target_market=target_market,
            aspect_ratio=aspect_ratio,
            product_name=product_name,
            specified_types=specified_types,
            style_preference=style_preference,
            avoid_types=avoid_types,
            confirmed_card=confirmed_card,
        )
        system_prompt = PRODUCT_SHOWCASE_SYSTEM_PROMPT
    else:
        plan_text = build_voiceover_plan_user_prompt(
            image_count=len(content) - 1,
            duration=int(duration),
            target_market=target_market,
            voiceover_language=voiceover_language,
            aspect_ratio=aspect_ratio,
            product_name=product_name,
            specified_types=specified_types,
            style_preference=style_preference,
            avoid_types=avoid_types,
            confirmed_card=confirmed_card,
        )
        system_prompt = VOICEOVER_PLAN_SYSTEM_PROMPT
    content[0] = {"type": "text", "text": plan_text}

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
        max_retries=0,  # 超时不自动重试，避免同一请求重复扣费
        timeout=900,
    )
    model = settings.OPENAI_VISION_MODEL or "gpt-4o"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    logger.info(
        f"[AI创作中心] 方案生成（{video_type}）：model={model}, 图片={len(content) - 1} 张, "
        f"时长={duration}s, 市场={target_market}, 语言={voiceover_language}, 比例={aspect_ratio}"
    )

    try:
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(_call_vision_stream_sync, client, model, messages, 900),
            timeout=960,
        )
    except asyncio.TimeoutError:
        raise AIAnalysisError("AI 生成超时（16分钟），请稍后重试或减少参考图数量")

    if not raw_text:
        raise AIAnalysisError("AI 模型返回内容为空")

    parsed = parse_voiceover_plan_text(raw_text)
    if not parsed["concepts"]:
        raise AIAnalysisError(f"无法解析 AI 返回的方案内容: {raw_text[:200]}")

    return parsed


# ---------- 新方案结束 ----------


# ============================================================
# 【旧方案】最终视频提示词生成（保留可用）
# ============================================================

async def generate_video_prompts(
    product_profile: Dict[str, Any],
    concepts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """根据产品画像和 3 个创意生成 3 套最终视频提示词"""
    user_text = (
        "Product Profile:\n"
        f"{json.dumps(product_profile, ensure_ascii=False, indent=2)}\n\n"
        "Video Concepts:\n"
        f"{json.dumps(concepts, ensure_ascii=False, indent=2)}\n\n"
        "Please generate exactly 3 final video generation prompts, one for each concept."
    )

    def _sync_call():
        raw = _call_text_model_sync(
            VIDEO_PROMPT_SYSTEM_PROMPT,
            user_text,
            response_format=_select_response_format(VIDEO_PROMPT_SCHEMA),
        )
        if not raw:
            return None
        return parse_analysis_result(raw)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_call),
            timeout=240,
        )
    except asyncio.TimeoutError:
        raise AIAnalysisError("视频提示词生成超时（240秒）")

    if not result or "prompts" not in result:
        raise AIAnalysisError("无法解析视频提示词结果")

    return result["prompts"]
