from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, Body, Response
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import io
import json
import logging
import threading
import time

from pydantic import BaseModel

from dependencies import PermissionChecker
from models.user import User
from config import get_settings
from services.ai_image_service import (
    submit_image_task,
    list_image_tasks,
    delete_image_task,
    cancel_image_task,
    retry_image_items,
    build_task_zip,
    get_image_task,
    prompt_belongs_to_tenant,
    get_comfy_prompt_history,
    GEN_TYPES,
    ASPECT_RATIO_MAP,
    APLUS_SPECS,
    MAIN_GEN_TYPES,
    MAIN_TYPE_DESCRIPTIONS,
    SCENE_GROUP,
    BRAND_OPTIONS,
    DEFAULT_BRAND_SETTINGS,
    MAX_REF_IMAGES,
    MAX_SHEETS_PER_TASK,
    _supported_ratio_options,
    AIImageError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-image", tags=["AI 生图"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024     # 单张 10MB，与 /ai-creation/proxy-image 一致
MAX_IMAGE_PIXELS = 40_000_000          # 40MP（约 8000x5000），防解码炸弹

# 提交幂等：同一 Idempotency-Key 在 TTL 内重复提交直接返回首次结果。
# 进程内实现（单 worker 足够；多 worker 需换 Redis/DB 唯一键，见 Phase 4 备注）。
_IDEM_TTL = 600
_idem_lock = threading.Lock()
_idem_done: Dict[str, Dict[str, Any]] = {}
_idem_inflight: set = set()


def _idem_check(key: str) -> Dict[str, Any]:
    """命中返回首次结果；进行中返回 None，重复点击由调用方抛 409。"""
    if not key:
        return {}
    now = time.time()
    with _idem_lock:
        for k in [k for k, v in _idem_done.items() if now - v["at"] > _IDEM_TTL]:
            _idem_done.pop(k, None)
        hit = _idem_done.get(key)
        if hit and now - hit["at"] <= _IDEM_TTL:
            return hit["task"]
        if key in _idem_inflight:
            return {"__inflight__": True}
        _idem_inflight.add(key)
    return {}


def _idem_settle(key: str, task: Dict[str, Any] = None) -> None:
    if not key:
        return
    with _idem_lock:
        _idem_inflight.discard(key)
        if task is not None:
            _idem_done[key] = {"task": task, "at": time.time()}


@router.get("/options")
async def get_options(current_user: User = Depends(PermissionChecker("ai_image:use"))) -> Dict[str, Any]:
    """生成类型与比例选项（供前端渲染）

    比例以 ComfyUI 实时能力为准：探测失败时回退静态表，避免因节点探测抖动禁用整页。
    """
    settings = get_settings()
    supported = _supported_ratio_options()
    ratios = {k: v for k, v in ASPECT_RATIO_MAP.items() if supported is None or v in supported}
    if not ratios:
        ratios = dict(ASPECT_RATIO_MAP)
    return {
        "generation_types": dict(GEN_TYPES),
        "aspect_ratios": ratios,
        "aspect_ratios_all": dict(ASPECT_RATIO_MAP),
        # 生成通道（本地 ComfyUI / 中转站）
        "providers": _provider_options(),
        # v2 分组选项
        # size：由 target_size 拼出的展示文案（1464×600），前端不再各自写死；
        # desc：卡片副标题，避免改后端尺寸时前端文案跟着失真。
        "aplus_types": {
            k: {
                "label": v["label"],
                "gen_ratio": v["gen_ratio"],
                "target_size": list(v["target_size"]),
                "size": f"{v['target_size'][0]}×{v['target_size'][1]}",
                "desc": v.get("desc", ""),
            }
            for k, v in APLUS_SPECS.items()
        },
        "main_types": dict(MAIN_GEN_TYPES),
        # 主副图卡片说明 + 场景图父项/子路径结构（含历史记录标签用的带人物项）
        "main_type_descriptions": dict(MAIN_TYPE_DESCRIPTIONS),
        "scene_group": SCENE_GROUP,
        "brand_options": BRAND_OPTIONS,
        # 品牌默认值：与后端 DEFAULT_BRAND_SETTINGS 同源，避免前端写死成另一套市场
        "brand_defaults": dict(DEFAULT_BRAND_SETTINGS),
        # 上限（前端据此渲染张数控件与计数文案）
        "max_ref_images": MAX_REF_IMAGES,
        "max_sheets_per_task": MAX_SHEETS_PER_TASK,
        # ComfyUI 页面基地址（无凭据，仅供前端拼 history 外链）
        "h3_base_url": (settings.H3_BASE_URL or "").rstrip("/"),
    }


def _validate_images(files: List[UploadFile]) -> List[tuple]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一张参考图")
    if len(files) > MAX_REF_IMAGES:
        raise HTTPException(status_code=400, detail=f"最多上传 {MAX_REF_IMAGES} 张参考图")

    from PIL import Image as PILImage

    result = []
    for file in files:
        name = file.filename or "image.jpg"
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {name}，请上传 JPEG/PNG/WebP/GIF/BMP 图片",
            )
        # 先量体积再读，避免把超大文件整体读进内存
        try:
            file.file.seek(0, 2)
            size = file.file.tell()
            file.file.seek(0)
        except Exception:
            size = 0
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"图片过大: {name}（{size / 1024 / 1024:.1f}MB，限 10MB）",
            )
        content = file.file.read(MAX_IMAGE_BYTES + 1)
        if not content:
            raise HTTPException(status_code=400, detail=f"图片为空: {name}")
        # 有限读取 + 读后兜底：即使 seek 不可用（size 量不到）也不会把超大文件整体读进内存
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"图片过大: {name}（限 {MAX_IMAGE_BYTES // 1024 // 1024}MB）",
            )

        # MIME 可伪造：真正解码一次，确认是可读图片且像素数合理
        try:
            PILImage.open(io.BytesIO(content)).verify()
            with PILImage.open(io.BytesIO(content)) as im:
                pixels = im.size[0] * im.size[1]
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"图片无法解析: {name}（文件可能已损坏或不是真正的图片）",
            )
        if pixels > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=400,
                detail=f"图片分辨率过大: {name}（{pixels / 1_000_000:.0f}MP，限 40MP）",
            )
        result.append((name, content))
    return result


def _provider_options() -> List[Dict[str, Any]]:
    """可选生成模型：本地 ComfyUI（Qwen Image 2.1）与中转站（Nano Banana 2）"""
    from services import relay_image_service

    relay = relay_image_service.provider_info()
    return [
        {
            "value": "local",
            "label": "Qwen Image 2.1（本地）",
            "desc": "ComfyUI 本地生成，细节保真高，串行排队",
            "configured": "1",
        },
        {
            "value": relay["value"],
            "label": f"{relay['label']}（中转站）",
            "desc": "gemini 图像模型，看图理解强，可并行生成",
            "configured": relay["configured"],
        },
    ]


@router.get("/layouts")
async def get_layouts(current_user: User = Depends(PermissionChecker("ai_image:use"))) -> Dict[str, Any]:
    """A+ 版式库：官方推荐（带收藏标记）/ 我的（上传）/ 收藏"""
    from services.ai_image_layout_service import LayoutError, list_layouts

    try:
        return list_layouts(current_user.id, current_user.tenant_id or 0)
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] 读取版式库失败")
        raise HTTPException(status_code=500, detail="读取版式库失败")


@router.post("/layouts/upload")
async def upload_layout(
    file: UploadFile = File(..., description="版式参考图，<=10MB"),
    name: str = Form("", description="版式名称"),
    current_user: User = Depends(PermissionChecker("ai_image:use")),
) -> Dict[str, Any]:
    """上传自己的版式（「我的」标签页）"""
    from services.ai_image_layout_service import LayoutError, add_upload

    name_hint, content = _validate_images([file])[0]
    try:
        return add_upload(current_user.id, current_user.tenant_id or 0,
                          current_user.username or "", content, name_hint, name)
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] 版式上传失败")
        raise HTTPException(status_code=500, detail="版式上传失败")


class LayoutFavoriteIn(BaseModel):
    key: str
    favorite: bool = True


class LayoutStructureIn(BaseModel):
    structure: str = ""


@router.put("/layouts/{layout_id}/structure")
async def save_layout_structure(
    layout_id: int,
    payload: LayoutStructureIn = Body(...),
    current_user: User = Depends(PermissionChecker("ai_image:use")),
) -> Dict[str, Any]:
    """保存版式结构描述（下一次生成即带上该文字约束；留空=只用参考图）"""
    from services.ai_image_layout_service import LayoutError, update_structure

    try:
        return update_structure(current_user.id, current_user.tenant_id or 0,
                                layout_id, payload.structure)
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] 保存版式结构描述失败")
        raise HTTPException(status_code=500, detail="保存失败")


@router.post("/layouts/{layout_id}/structure/suggest")
async def suggest_layout_structure(
    layout_id: int,
    current_user: User = Depends(PermissionChecker("ai_image:use")),
) -> Dict[str, Any]:
    """AI 读版式图产出结构描述草稿（不落库，前端确认后再保存）"""
    from services.ai_image_layout_service import LayoutError, suggest_structure

    try:
        return suggest_structure(current_user.id, current_user.tenant_id or 0, layout_id)
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] AI 生成版式结构描述失败")
        raise HTTPException(status_code=500, detail="AI 生成失败")


@router.post("/layouts/favorite")
async def favorite_layout(
    payload: LayoutFavoriteIn = Body(...),
    current_user: User = Depends(PermissionChecker("ai_image:use")),
) -> Dict[str, Any]:
    """收藏/取消收藏（内置版式 key=preset:xxx，上传版式 key=db:id）"""
    from services.ai_image_layout_service import LayoutError, set_favorite

    try:
        return set_favorite(current_user.id, current_user.tenant_id or 0,
                            payload.key, payload.favorite)
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] 版式收藏失败")
        raise HTTPException(status_code=500, detail="版式收藏失败")


@router.delete("/layouts/{layout_id}")
async def delete_layout(layout_id: int, current_user: User = Depends(PermissionChecker("ai_image:use"))) -> Dict[str, Any]:
    """删除自己上传的版式（软删，历史任务不受影响）"""
    from services.ai_image_layout_service import LayoutError, delete_layout as _delete

    try:
        _delete(current_user.id, current_user.tenant_id or 0, layout_id)
        return {"ok": True}
    except LayoutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("[AI生图] 删除版式失败")
        raise HTTPException(status_code=500, detail="删除版式失败")


@router.post("/tasks")
async def create_image_task(
    files: List[UploadFile] = File(..., description="参考图，1~8 张（第 1 张为主参考）"),
    title: str = Form("", description="任务标题（产品名）"),
    generation_types: str = Form("[]", description='v1 兼容：生成类型 JSON 数组'),
    aspect_ratio: str = Form("1:1"),
    image_count: int = Form(1, ge=1, le=4),
    # ===== v2 分组参数 =====
    aplus_type: str = Form("", description="A+ 类型（单选）：aplus_advanced/aplus_normal/aplus_mobile/aplus_banner，空=未选"),
    main_types: str = Form("[]", description='主副图类型 JSON 数组，如 ["selling_point","white_background"]'),
    main_aspect_ratio: str = Form("1:1", description="主副图生成比例"),
    brand_settings: str = Form("", description='品牌自定义设置 JSON（主色/字体/自定义文本/国家/语言/平台）'),
    product_info: str = Form("", description="商品信息（卖点/材质/场景等）"),
    product_fact_card: str = Form("", description="P4 结构化商品事实卡 JSON（提供时覆盖 product_info）"),
    prompt: str = Form("", description="附加提示词"),
    product_code: str = Form(""),
    product_name: str = Form(""),
    provider: str = Form("local", description="生成通道：local=本地 Qwen Image 2.1 / relay=中转站 Nano Banana 2"),
    layout_ids: str = Form("[]", description='A+ 版式标识 JSON 数组，如 ["preset:left_text_right_image","db:12"]；空=随机官方版式（品牌A+横幅忽略）'),
    brand_name: str = Form("", description="品牌A+横幅必填的英文品牌名（保留大小写，2–20 字符）"),
    idempotency_key: str = Header("", alias="Idempotency-Key"),
    current_user: User = Depends(PermissionChecker("ai_image:use")),
):
    """提交商品套图生成任务：落库后立即返回，生成在后台进行"""
    # 幂等：前端同一份表单只带一个 key；重复点击/重试不会产生第二单
    _raw_key = (idempotency_key or "").strip()[:80]
    idem_key = f"{current_user.id}:{_raw_key}" if _raw_key else ""
    cached = _idem_check(idem_key)
    if cached.get("__inflight__"):
        raise HTTPException(status_code=409, detail="上一次提交仍在处理中，请勿重复点击")
    if cached:
        logger.info("[AI生图] 幂等命中，返回已创建任务 %s", cached.get("id"))
        return cached

    try:
        try:
            types = json.loads(generation_types)
            if not isinstance(types, list):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="generation_types 必须是 JSON 数组")
        try:
            main_type_list = json.loads(main_types)
            if not isinstance(main_type_list, (list, dict)):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="main_types 必须是 JSON 数组或对象")
        brand: dict = {}
        if brand_settings:
            try:
                brand = json.loads(brand_settings)
                if not isinstance(brand, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="brand_settings 必须是 JSON 对象")

        fact_card: dict = {}
        if product_fact_card:
            try:
                fact_card = json.loads(product_fact_card)
                if not isinstance(fact_card, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="product_fact_card 必须是 JSON 对象")

        images = _validate_images(files)
        from services.ai_image_layout_service import LayoutError, parse_layout_keys
        try:
            layout_keys = parse_layout_keys(layout_ids)
        except LayoutError as e:
            raise HTTPException(status_code=400, detail=str(e))

        task = submit_image_task(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            creator_name=current_user.username or "",
            title=title or product_name or "商品套图",
            generation_types=types,
            aspect_ratio=aspect_ratio,
            image_count=image_count,
            product_info=product_info,
            prompt=prompt,
            images=images,
            product_code=product_code,
            product_name=product_name,
            aplus_type=aplus_type or None,
            main_types=main_type_list,
            main_aspect_ratio=main_aspect_ratio,
            brand_settings=brand or None,
            provider=provider,
            layout_keys=layout_keys,
            product_fact_card=fact_card or None,
            brand_name=brand_name,
        )
    except HTTPException:
        _idem_settle(idem_key)
        raise
    except AIImageError as e:
        _idem_settle(idem_key)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        _idem_settle(idem_key)
        logger.exception("[AI生图] 提交任务异常")
        # 不透传底层异常（含内部服务地址），排障看服务端日志
        raise HTTPException(status_code=500, detail="提交任务失败，请稍后重试")

    _idem_settle(idem_key, task)
    return task


@router.get("/tasks")
async def list_tasks(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    status: str = "",
    current_user: User = Depends(PermissionChecker("ai_image:use")),
):
    """套图生成任务列表（分页，按时间倒序；status 由服务端过滤，保证分页自洽）"""
    try:
        return list_image_tasks(
            current_user.tenant_id, page=page, page_size=page_size, keyword=keyword, status=status
        )
    except AIImageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, current_user: User = Depends(PermissionChecker("ai_image:use"))):
    """单任务详情（前端轮询用：只拉当前页仍在生成的单条，避免整页重复传输）"""
    try:
        return get_image_task(current_user.tenant_id, task_id)
    except AIImageError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/tasks/{task_id}")
async def remove_task(task_id: int, current_user: User = Depends(PermissionChecker("ai_image:use"))):
    """删除任务记录，并清理该任务在 TOS 的参考图/生成图"""
    try:
        delete_image_task(current_user.tenant_id, task_id)
    except AIImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, current_user: User = Depends(PermissionChecker("ai_image:use"))):
    """取消排队中/生成中的任务（已执行的那一张会自然结束，不再回收）"""
    try:
        return cancel_image_task(current_user.tenant_id, task_id)
    except AIImageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: int,
    payload: Optional[Dict[str, Any]] = Body(default=None),
    current_user: User = Depends(PermissionChecker("ai_image:use")),
):
    """重试失败子项：body 可带 {"indices": [0, 2]}，缺省重试全部失败项"""
    indices = (payload or {}).get("indices")
    if indices is not None and not isinstance(indices, list):
        raise HTTPException(status_code=400, detail="indices 必须是数组")
    try:
        wanted = [int(i) for i in indices] if indices else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="indices 必须是整数数组")
    try:
        return retry_image_items(current_user.tenant_id, task_id, wanted)
    except AIImageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks/{task_id}/download.zip")
async def download_zip(task_id: int, current_user: User = Depends(PermissionChecker("ai_image:use"))):
    """打包下载该任务全部已生成的图片"""
    try:
        data, filename = build_task_zip(current_user.tenant_id, task_id)
    except AIImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(data)),
        },
    )


@router.get("/comfy-history/{prompt_id}")
async def get_comfy_history(prompt_id: str, current_user: User = Depends(PermissionChecker("ai_image:use"))):
    """按 ComfyUI prompt_id 查执行历史：原始提示词 + PE 优化后提示词（生图详情弹窗用）"""
    # 归属校验：prompt_id 属于其他租户时一律 404（不泄露存在性）
    if not prompt_belongs_to_tenant(current_user.tenant_id, prompt_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return get_comfy_prompt_history(prompt_id)
    except AIImageError as e:
        raise HTTPException(status_code=502, detail=str(e))
