from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from urllib.parse import urlparse
import asyncio
import httpx
import io
import json
import logging
import time

from fastapi.responses import Response, StreamingResponse
from dependencies import get_current_user
from config import get_settings
from models.user import User
from services.ai_creation_service import (
    analyze_product_images,
    generate_video_concepts,
    generate_video_prompts,
    generate_voiceover_plans,
    AIAnalysisError,
)
from services.h3_video_service import (
    submit_video_task,
    list_video_tasks,
    delete_video_task,
    cancel_video_task,
    H3VideoError,
)
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-creation", tags=["AI 创作中心"])


def _sse(payload: dict, event: str = "message") -> str:
    """SSE 事件帧"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class _UploadedBytes:
    """内存上传文件替身。

    流式响应场景下，FastAPI 依赖清理会在响应体发送前关闭 UploadFile，
    因此必须先把图片字节读入内存，再交给后台生成任务使用。
    file 属性提供 seek/read/close 接口，与 _build_user_content 的读取方式兼容。
    """

    def __init__(self, filename: str, content_type: str, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(content)


class ProductProfileRequest(BaseModel):
    product_profile: dict
    market: str = "US"
    duration: int = 30
    aspect_ratio: str = "9:16"
    previous_concepts: Optional[list] = None


class GeneratePromptsRequest(BaseModel):
    product_profile: dict
    concepts: list


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}


# 代理取图静态白名单后缀（后缀匹配）：TOS 历史直连地址 + 自有 CDN + 亚马逊图片 CDN。
# TOS 侧域名（自定义 CDN 域名 / 桶域名）由 _proxy_image_hosts 动态拼装，
# .env 的 PROXY_IMAGE_HOST_SUFFIXES 可追加额外域名，避免再改代码。
PROXY_IMAGE_HOST_SUFFIXES = (
    "volces.com",             # 火山引擎 TOS（历史直连地址）
    "bxhsrpa.com",            # 自有 CDN（产品图/生成图）
    "media-amazon.com",       # 亚马逊图片 CDN
    "ssl-images-amazon.com",  # 亚马逊图片 CDN（旧域名）
)
PROXY_IMAGE_MAX_BYTES = 10 * 1024 * 1024


def _proxy_image_hosts() -> set:
    """代理白名单完整主机集合：静态后缀 + .env 追加项 + 当前 TOS 配置的自定义域名 / 桶域名"""
    hosts = set(PROXY_IMAGE_HOST_SUFFIXES)
    settings = get_settings()
    extra = (settings.PROXY_IMAGE_HOST_SUFFIXES or "").strip()
    if extra:
        hosts.update(p.strip().lower().lstrip(".") for p in extra.split(",") if p.strip())
    if settings.TOS_CUSTOM_DOMAIN:
        host = urlparse(f"https://{settings.TOS_CUSTOM_DOMAIN.strip('/')}").hostname
        if host:
            hosts.add(host.lower())
    if settings.TOS_BUCKET and settings.TOS_ENDPOINT:
        hosts.add(f"{settings.TOS_BUCKET}.{settings.TOS_ENDPOINT.strip('/')}".lower())
    return hosts


@router.get("/proxy-image")
async def proxy_image(url: str, current_user: User = Depends(get_current_user)):
    """服务端代取图片字节。

    历史记录编辑回填/重试、产品库选图等场景前端仅持有图片 URL，需 fetch 下载后转 File 提交；
    TOS / CDN 未配置 CORS 响应头时浏览器直连 fetch 会被拦截（<img> 预览不受影响），
    此时前端改走本接口由后端下载并回传图片字节。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="无效的图片地址")
    host = parsed.hostname.lower()
    allowed = _proxy_image_hosts()
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise HTTPException(
            status_code=400,
            detail=f"该图片域名不在代理白名单内（{host}），可在 .env 的 PROXY_IMAGE_HOST_SUFFIXES 追加",
        )

    try:
        # trust_env=False：绕过系统/沙箱 HTTP 代理，避免隧道 502 把取图拖到超时
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail="目标地址不是图片")
            data = resp.content
    except httpx.HTTPError as e:
        logger.warning(f"[AI创作中心] 代理取图失败: {url}, err={e}")
        raise HTTPException(status_code=502, detail="图片下载失败，请重试")
    if len(data) > PROXY_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片过大，请重新上传")
    return Response(content=data, media_type=content_type)


def _validate_images(files: List[UploadFile]) -> None:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一张图片")
    if len(files) > 9:
        raise HTTPException(status_code=400, detail="最多上传 9 张图片")
    for file in files:
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {file.filename}，请上传 JPEG/PNG/WebP/GIF/BMP 图片"
            )


@router.post("/generate-voiceover-plans")
async def generate_voiceover_plans_endpoint(
    files: List[UploadFile] = File(...),
    duration: int = Form(15),
    target_market: str = Form("美国"),
    voiceover_language: str = Form("自动"),
    video_type: str = Form("口播带货"),
    aspect_ratio: str = Form("9:16"),
    product_name: str = Form(""),
    specified_types: Optional[str] = Form(None),
    style_preference: str = Form(""),
    avoid_types: Optional[str] = Form(None),
    confirmed_card: str = Form(""),
    current_user: User = Depends(get_current_user),
):
    """
    新方案：上传 1~9 张商品参考图，一次性生成产品信息卡 + 3 套视频提示词方案。
    video_type：口播带货（含口播台词）/ 产品展示（纯画面展示，无台词）。
    specified_types / avoid_types 为顿号或逗号分隔的类型名。

    生成耗时 1~5 分钟以上且期间不回字节，前置网关（nginx 默认 60s 读超时）会报 504。
    因此改为 SSE 流式响应：每 15 秒发一次心跳保活，完成后以最终事件返回结果。
    """
    _validate_images(files)

    def _split_types(raw: Optional[str]) -> Optional[List[str]]:
        if not raw:
            return None
        items = [t.strip() for t in raw.replace("，", "、").replace(",", "、").split("、")]
        return [t for t in items if t] or None

    if not 5 <= duration <= 30:
        raise HTTPException(status_code=400, detail="视频时长仅支持 5 / 10 / 15 秒")

    logger.info(
        f"[AI创作中心] 用户 {current_user.id} 请求生成方案（{video_type}）："
        f"{len(files)} 张图, 时长={duration}s, 市场={target_market}, "
        f"语言={voiceover_language}, 比例={aspect_ratio}"
    )

    # 返回 StreamingResponse 后上传文件会被依赖清理关闭，先读入内存
    file_datas = [
        _UploadedBytes(f.filename or f"image-{i}.jpg", f.content_type or "image/jpeg", await f.read())
        for i, f in enumerate(files)
    ]

    async def _do_generate() -> dict:
        try:
            result = await generate_voiceover_plans(
                file_datas,
                duration=duration,
                target_market=target_market,
                voiceover_language=voiceover_language,
                aspect_ratio=aspect_ratio,
                product_name=product_name,
                specified_types=_split_types(specified_types),
                style_preference=style_preference,
                avoid_types=_split_types(avoid_types),
                confirmed_card=confirmed_card,
                video_type=video_type,
            )
            return {"success": True, "data": result}
        except AIAnalysisError as e:
            logger.error(f"[AI创作中心] 生成口播方案失败: {e}")
            return {"detail": str(e)}
        except Exception as e:
            logger.error(f"[AI创作中心] 生成口播方案异常: {e}")
            return {"detail": f"生成失败: {e}"}

    async def _stream():
        gen_task = asyncio.ensure_future(_do_generate())
        # 心跳 15 秒一次；总上限与前端超时（960s）对齐
        deadline = time.monotonic() + 950
        while not gen_task.done():
            if time.monotonic() > deadline:
                gen_task.cancel()
                yield _sse({"detail": "AI 生成超时（约 16 分钟），请稍后重试"}, event="error")
                return
            yield ": ping\n\n"
            await asyncio.sleep(15)
        payload = gen_task.result()
        if "detail" in payload:
            yield _sse(payload, event="error")
        else:
            yield _sse(payload)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 关闭 nginx 对该响应的缓冲，让心跳实时透传
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/generate-video")
async def generate_video_endpoint(
    files: List[UploadFile] = File(...),
    prompt: str = Form(...),
    duration: int = Form(15),
    resolution: str = Form("0.5"),
    ratio: str = Form("9:16"),
    model: str = Form("minimax-h3"),
    market: str = Form("US"),
    voiceover_language: str = Form("自动"),
    video_type: str = Form("口播带货"),
    title: str = Form("视频生成任务"),
    product_code: str = Form(""),
    product_name: str = Form(""),
    current_user: User = Depends(get_current_user),
):
    """
    提交 MiniMax H3 视频生成任务：参考图 + 完整提示词 + 参数。
    生成需较长时间，接口立即返回任务记录，由后台轮询进度并回写状态。
    """
    _validate_images(files)
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="提示词不能为空")
    if not 5 <= duration <= 30:
        raise HTTPException(status_code=400, detail="视频时长仅支持 5 / 10 / 15 秒")

    images = [(f.filename or f"ref-{i}.jpg", await f.read()) for i, f in enumerate(files)]

    logger.info(
        f"[AI创作中心] 用户 {current_user.id} 提交视频生成：{len(images)} 张图, "
        f"时长={duration}s, 分辨率={resolution}, 比例={ratio}, 模型={model}, 市场={market}"
    )

    try:
        task = submit_video_task(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            creator_name=current_user.username,
            title=title,
            prompt=prompt,
            images=images,
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            model=model,
            market=market,
            voiceover_language=voiceover_language,
            video_type=video_type,
            product_code=product_code,
            product_name=product_name,
        )
    except H3VideoError as e:
        logger.error(f"[AI创作中心] 提交视频生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "data": task}


@router.get("/video-tasks")
async def list_video_tasks_endpoint(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    bind_status: str = "all",
    current_user: User = Depends(get_current_user),
):
    """视频生成任务列表（租户内共享，含状态与成片地址），用于生成历史与进度轮询；bind_status: all/bound/unbound"""
    return {
        "success": True,
        "data": list_video_tasks(
            current_user.tenant_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            bind_status=bind_status,
        ),
    }


# ============================================================
# 视频超分（Topaz 星光 2.6，与 H3 共用同一台 ComfyUI）
# ============================================================

class UpscaleTaskCreate(BaseModel):
    title: str = ""
    source_video_url: str
    source_width: int = 0
    source_height: int = 0
    scale: float = 2.0


@router.post("/upscale-tasks")
async def create_upscale_task(
    body: UpscaleTaskCreate,
    current_user: User = Depends(get_current_user),
):
    """提交视频超分任务：源视频（TOS 地址）+ 放大倍数，后台下载/上传/入队/轮询"""
    from services.upscale_service import submit_upscale_task, UpscaleError

    if not body.source_video_url.strip():
        raise HTTPException(status_code=400, detail="缺少源视频地址")
    logger.info(
        f"[AI创作中心] 用户 {current_user.id} 提交超分任务: 倍数={body.scale}, "
        f"源={body.source_width}x{body.source_height}"
    )
    try:
        task = submit_upscale_task(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            creator_name=current_user.username,
            title=body.title or "超分任务",
            source_video_url=body.source_video_url.strip(),
            source_width=body.source_width,
            source_height=body.source_height,
            scale=body.scale,
        )
    except UpscaleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": task}


@router.get("/upscale-tasks")
async def list_upscale_tasks_endpoint(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    bind_status: str = "all",
    current_user: User = Depends(get_current_user),
):
    """超分任务列表（分页 + 标题搜索 + 绑定状态筛选），用于高清处理页面与进度轮询"""
    from services.upscale_service import list_upscale_tasks

    return {
        "success": True,
        "data": list_upscale_tasks(
            current_user.tenant_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            bind_status=bind_status,
        ),
    }


@router.post("/upscale-tasks/{task_id}/cancel")
async def cancel_upscale_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """取消排队中/处理中的超分任务（同步移除/中断 ComfyUI 任务）"""
    from services.upscale_service import cancel_upscale_task, UpscaleError

    try:
        cancel_upscale_task(current_user.tenant_id, task_id)
    except UpscaleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": "任务已取消"}


@router.post("/upscale-tasks/{task_id}/retry")
async def retry_upscale_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """重试失败的超分任务：先查 ComfyUI history，有成片直接接续收片，无成片重新入队"""
    from services.upscale_service import retry_upscale_task, UpscaleError

    try:
        task, message = retry_upscale_task(current_user.tenant_id, task_id)
    except UpscaleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": message, "data": task}


@router.delete("/upscale-tasks/{task_id}")
async def delete_upscale_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """删除超分任务（软删除）"""
    from services.upscale_service import delete_upscale_task, UpscaleError

    try:
        delete_upscale_task(current_user.tenant_id, task_id)
    except UpscaleError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True}


@router.get("/upscale-tasks/{task_id}/download")
async def download_upscale_video(
    task_id: int,
    target: str = "result",
    current_user: User = Depends(get_current_user),
):
    """下载超分任务的源视频/超分结果（target: result=超分结果 / source=源视频）

    服务端代理 TOS 下载并附带 Content-Disposition 下载文件名：
    浏览器对跨域 <a download> 不生效，必须由同源接口流式返回。
    """
    import re
    from urllib.parse import quote
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        task = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.id == task_id,
            VideoUpscaleTask.tenant_id == current_user.tenant_id,
            VideoUpscaleTask.deleted_at.is_(None),
        ).first()
    finally:
        db.close()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    url = (task.video_url if target == "result" else task.source_video_url) or ""
    if not url:
        raise HTTPException(status_code=400, detail="该任务暂无可下载的视频")

    # 文件名：任务标题（清理非法字符）_超分结果/源视频.mp4
    safe_title = re.sub(r'[\\/:*?"<>|\r\n]', "_", (task.title or f"upscale-{task_id}")).strip()[:60]
    safe_title = safe_title or f"upscale-{task_id}"
    m = re.search(r"(\.[a-zA-Z0-9]{2,5})$", url.split("?")[0])
    ext = m.group(1).lower() if m else ".mp4"
    label = "超分结果" if target == "result" else "源视频"
    filename = f"{safe_title}_{label}{ext}"

    def _stream():
        import requests as _requests
        with _requests.get(url, stream=True, timeout=(10, 600)) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    yield chunk

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(_stream(), media_type="video/mp4", headers=headers)


@router.post("/video-tasks/{task_id}/cancel")
async def cancel_video_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """取消排队中/生成中的视频生成任务（同步移除/中断 ComfyUI 任务）"""
    try:
        cancel_video_task(current_user.tenant_id, task_id)
    except H3VideoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": "任务已取消"}


@router.post("/video-tasks/{task_id}/recover")
async def recover_video_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """智能重试失败的视频任务（原记录重试）。

    action="resumed"：已接续原 ComfyUI 任务（后台轮询收片回填原记录）；
    action="resubmitted"：服务端已下载历史参考图并复用原记录重新提交生成。
    含 ComfyUI 查询与参考图下载（秒级），放到线程池执行避免阻塞事件循环。
    """
    from services.h3_video_service import recover_failed_task, H3VideoError

    try:
        action, message = await asyncio.to_thread(
            recover_failed_task, current_user.tenant_id, task_id
        )
    except H3VideoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "action": action, "message": message}


@router.delete("/video-tasks/{task_id}")
async def delete_video_task_endpoint(
    task_id: int,
    current_user: User = Depends(get_current_user),
):
    """删除视频生成任务记录（租户内任意账号均可删）"""
    delete_video_task(current_user.tenant_id, task_id)
    return {"success": True}


@router.post("/analyze-product")
async def analyze_product(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    【旧方案】上传商品图片（最多 9 张），调用视觉模型分析 Amazon 产品信息。
    返回结构化的产品名称、卖点、类目、关键词等信息。
    """
    _validate_images(files)

    logger.info(f"[AI创作中心] 用户 {current_user.id} 上传 {len(files)} 张图片进行产品分析")

    try:
        result = await analyze_product_images(files)
    except AIAnalysisError as e:
        logger.error(f"[AI创作中心] 分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not result:
        raise HTTPException(status_code=500, detail="AI 分析结果为空，请稍后重试")

    # 同时返回「已格式化文本」：与 AI 生图中转站自动分析用的是同一个 _card_to_text，
    # 让前端「AI 分析」按钮与后端自动补全产出同一套字段（含组件/尺寸/使用方式/风格倾向），
    # 避免前端自己再映射一遍导致两边漂移（2026-10-28：local 曾只有 6 字段、relay 有 11 字段）。
    from services.ai_image_service import _card_to_text  # 局部导入，规避模块级循环依赖

    return {
        "success": True,
        "data": result,
        "text": _card_to_text(result or {}),
    }


@router.post("/generate-video-concepts")
async def generate_video_concepts_endpoint(
    body: ProductProfileRequest,
    current_user: User = Depends(get_current_user),
):
    """根据产品画像生成 3 个差异化带货视频创意"""
    if not body.product_profile:
        raise HTTPException(status_code=400, detail="产品画像不能为空")

    logger.info(
        f"[AI创作中心] 用户 {current_user.id} 请求生成视频创意，"
        f"market={body.market}, duration={body.duration}, aspect_ratio={body.aspect_ratio}, "
        f"previous={len(body.previous_concepts) if body.previous_concepts else 0}"
    )

    try:
        concepts = await generate_video_concepts(
            body.product_profile,
            market=body.market,
            duration=body.duration,
            aspect_ratio=body.aspect_ratio,
            previous_concepts=body.previous_concepts,
        )
    except AIAnalysisError as e:
        logger.error(f"[AI创作中心] 生成视频创意失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success": True,
        "data": concepts,
    }


@router.post("/generate-video-prompts")
async def generate_video_prompts_endpoint(
    body: GeneratePromptsRequest,
    current_user: User = Depends(get_current_user),
):
    """根据产品画像和 3 个视频创意生成 3 套最终视频提示词"""
    if not body.product_profile:
        raise HTTPException(status_code=400, detail="产品画像不能为空")
    if not body.concepts or len(body.concepts) != 3:
        raise HTTPException(status_code=400, detail="必须提供 3 个视频创意")

    logger.info(f"[AI创作中心] 用户 {current_user.id} 请求生成视频提示词")

    try:
        prompts = await generate_video_prompts(body.product_profile, body.concepts)
    except AIAnalysisError as e:
        logger.error(f"[AI创作中心] 生成视频提示词失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success": True,
        "data": prompts,
    }
