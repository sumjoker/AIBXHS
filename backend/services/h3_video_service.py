"""MiniMax H3 视频生成服务（对接本地部署的 ComfyUI API）

流程：上传参考图 → 提交工作流 → 每 15 秒轮询进度 → 生成完自动下载 → 上传 TOS → 回写任务状态。

接口（均需 Basic Auth）：
    POST /upload/image      上传参考图
    POST /prompt            提交任务（JSON 里是工作流）
    GET  /history/{任务ID}   查状态（完成后 GET /view 下载成片）
"""
import io
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import get_settings
from services.h3_prompt_optimizer import optimize_h3_prompt

logger = logging.getLogger(__name__)
settings = get_settings()

# 分辨率档位 → megapixels（0.4 草稿 ~ 0.98 满画幅）
RESOLUTION_MP: Dict[str, float] = {
    "0.3": 0.3,
    "0.5": 0.5,
    "768P": 0.98,   # 0.98 即官方 768p（1344x768）
}

# 视频比例 → ComfyUI ResolutionSelector 的选项字符串
ASPECT_RATIO_MAP: Dict[str, str] = {
    "auto": "9:16 (Portrait Widescreen)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "1:1": "1:1 (Square)",
    "4:3": "4:3 (Standard)",
    "3:4": "3:4 (Portrait Standard)",
    "21:9": "21:9 (Ultrawide)",
}

# 工作流中的固定节点编号（与 workflows/h3_workflow_template.json 对应，2026-09-27 三重加速版：
# UNET 26 → SAGE Attention 27 → Turbo LoRA 29 → TE-Speed 45 → BasicGuider 31）
NODE_DURATION = "48"          # PrimitiveFloat，时长（秒），输入 value
NODE_RESOLUTION = "46"        # ResolutionSelector，输入 aspect_ratio / megapixels
NODE_REF_IMAGE = "42"         # 首张参考图 LoadImage
NODE_H3_CONDITIONING = "43"   # MiniMaxH3ReferenceToVideo：prompt 直接写在 inputs.prompt，ref_images.ref_image_i 也挂在这里
NEW_IMAGE_NODE_BASE = 200     # 追加参考图时新增 LoadImage 节点的起始编号（模板最大节点号 51，不会冲突）

_running: set = set()
_lock = threading.Lock()

# 中文数字 → 序号（参考图最多 9 张）
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _detect_person_picture(prompt: str, image_count: int) -> int:
    """从方案文本提取人物参考图序号（1-based，0 表示无）。

    方案生成时若参考图含人物，模特字段会带“（人物参考图：第 N 张）”标注。
    """
    m = re.search(r"人物参考图\s*[：:]\s*第\s*([0-9一二三四五六七八九]+)\s*张", prompt or "")
    if not m:
        return 0
    token = m.group(1)
    n = _CN_NUM.get(token) or (int(token) if token.isdigit() else 0)
    return n if 1 <= n <= image_count else 0


class H3VideoError(Exception):
    """H3 视频生成异常"""
    pass


def _api(method: str, path: str, timeout: int = 60, **kwargs) -> requests.Response:
    """带 Basic Auth 的接口请求"""
    url = settings.H3_BASE_URL.rstrip("/") + path
    resp = requests.request(
        method,
        url,
        auth=(settings.H3_AUTH_USER, settings.H3_AUTH_PASSWORD),
        timeout=timeout,
        **kwargs,
    )
    if resp.status_code == 401:
        raise H3VideoError("H3 服务认证失败，请检查账号密码")
    resp.raise_for_status()
    return resp


def _normalize_for_comfy(content: bytes) -> bytes:
    """上传前统一压成 JPEG（长边 ≤2048、透明底合成白底）。

    超大原图或特殊格式会导致上传/解析偶发失败，且 H3 参考图管线本身就会缩到 2048。
    压缩失败时回退原始字节。
    """
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


def _upload_image(idx: int, content: bytes) -> str:
    """上传参考图到 ComfyUI，返回服务器端文件名。

    文件名统一为纯 ASCII，避免特殊字符问题；失败自动重试一次。
    """
    name = f"aivideo-{int(time.time() * 1000)}-{idx}.jpg"
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
        except requests.HTTPError as e:
            body = ""
            if e.response is not None:
                body = (e.response.text or "")[:200]
            last_err = f"{e}; {body}"
            logger.warning(f"[H3视频] 参考图上传第 {attempt} 次失败: {last_err}")
            threading.Event().wait(2)
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[H3视频] 参考图上传第 {attempt} 次异常: {e}")
            threading.Event().wait(2)

    raise H3VideoError(f"H3 参考图上传失败（已重试）: {last_err}")


def _build_workflow(
    *,
    prompt: str,
    duration: int,
    resolution: str,
    ratio: str,
    image_names: List[str],
    with_ref_note: bool = True,
    person_picture: int = 0,
) -> Dict[str, Any]:
    """读取工作流模板并填入本次生成参数

    :param with_ref_note: 是否附加中文参考图说明。二次优化后的提示词已按官方格式
        定义 <Picture N>，不再需要该说明（混入中文会破坏结构化格式）。
    :param person_picture: 人物参考图序号（1-based，0 表示无），用于回退说明文案。
    """
    try:
        with open(settings.H3_WORKFLOW_PATH, encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        raise H3VideoError(f"读取 H3 工作流模板失败: {e}")

    # 提示词中通过 <Picture i> 引用参考图（i 从 1 开始）
    if with_ref_note and image_names:
        if 1 <= person_picture <= len(image_names):
            ref_note = (
                f"参考图说明：<Picture {person_picture}> 为人物参考图，模特形象以其为准；"
                f"其余 <Picture> 为同一产品的多角度参考图，"
                "请严格保持人物形象与商品外观、颜色、材质、图案、包装的一致性。\n\n"
            )
        else:
            ref_note = (
                f"参考图说明：<Picture 1> 至 <Picture {len(image_names)}> 为同一产品的多角度参考图，"
                "请严格保持商品的外观、颜色、材质、图案与包装一致性。\n\n"
            )
    else:
        ref_note = ""

    # 提示词直接写在 H3 节点的 prompt 输入上（三重加速版模板没有独立的提示词节点）
    wf[NODE_H3_CONDITIONING]["inputs"]["prompt"] = ref_note + prompt
    wf[NODE_DURATION]["inputs"]["value"] = float(duration)
    wf[NODE_RESOLUTION]["inputs"]["megapixels"] = RESOLUTION_MP.get(resolution, 0.5)
    wf[NODE_RESOLUTION]["inputs"]["aspect_ratio"] = ASPECT_RATIO_MAP.get(
        ratio, ASPECT_RATIO_MAP["9:16"]
    )
    wf[NODE_REF_IMAGE]["inputs"]["image"] = image_names[0]

    # 追加参考图：新增 LoadImage 节点，挂到 ref_images.ref_image_1 ... 上
    for idx, name in enumerate(image_names[1:], start=1):
        node_id = str(NEW_IMAGE_NODE_BASE + idx)
        wf[node_id] = {
            "inputs": {"image": name},
            "class_type": "LoadImage",
            "_meta": {"title": "加载图像"},
        }
        wf[NODE_H3_CONDITIONING]["inputs"][f"ref_images.ref_image_{idx}"] = [node_id, 0]

    # 步数/LoRA/TE-Speed 参数均以模板为准（BasicScheduler 39 已写死 15 步），代码不再覆盖
    # 随机种子：模板用 rgthree Seed 节点且值为 -1，服务端执行时会自动生成随机种子

    return wf


def _extract_output_file(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 ComfyUI 任务结果中取出成片文件信息"""
    finfo = None
    for node_out in (item.get("outputs") or {}).values():
        for key in ("gifs", "videos", "images"):
            for f in node_out.get(key) or []:
                finfo = f
    return finfo


def _download_video(finfo: Dict[str, Any]) -> Tuple[bytes, str]:
    """下载成片，返回 (字节内容, 文件名)；网络中断（IncompleteRead 等）自动重试 3 次"""
    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = _api("GET", "/view", timeout=1800, params=finfo, stream=True)
            buf = io.BytesIO()
            for chunk in resp.iter_content(1 << 20):
                buf.write(chunk)
            return buf.getvalue(), finfo.get("filename") or "output.mp4"
        except Exception as e:
            last_err = e
            if attempt < 3:
                logger.warning(f"[H3视频] 成片下载中断（第 {attempt} 次），5 秒后重试: {e}")
                threading.Event().wait(5)
    raise last_err


def _update_task(task_id: int, only_active: bool = False, **fields) -> None:
    """独立会话更新任务记录（后台线程中使用）

    only_active=True 时仅在任务仍处于「排队中/生成中」才写入，
    防止双后端同库场景下，本进程的迟到结果覆盖另一进程已写入的「已完成」。
    """
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        q = db.query(AIVideoTask).filter(AIVideoTask.id == task_id)
        if only_active:
            q = q.filter(AIVideoTask.status.in_(["排队中", "生成中"]))
        updated = q.update(fields, synchronize_session=False)
        db.commit()
        if only_active and not updated:
            logger.info(f"[H3视频] 任务 {task_id} 状态已被其他进程更新，跳过写入 {fields.get('status')}")
    except Exception as e:
        logger.error(f"[H3视频] 更新任务 {task_id} 状态失败: {e}")
        db.rollback()
    finally:
        db.close()


def _run_task(task_id: int, h3_prompt_id: str) -> None:
    """后台线程：轮询任务状态，完成后下载并上传 TOS

    ComfyUI 串行执行：任务先在 queue_pending 排队，开始执行进入 queue_running，
    执行完移入 history。据此区分「排队中」与「生成中」。
    重复启动防护由 _ensure_worker / _prepare_and_run 负责（_running 集合）。
    """
    from services import tos_service

    logger.info(f"[H3视频] 任务 {task_id}（ComfyUI {h3_prompt_id}）开始轮询")
    interval = max(5, int(settings.H3_POLL_INTERVAL))
    max_polls = max(1, int(settings.H3_MAX_WAIT_MINUTES * 60 / interval))
    last_status = ""  # 仅状态变化时写库，避免每轮空写
    started_written = False  # 首次进入「生成中」记录开始时间，耗时不含排队

    def set_status(status: str) -> None:
        nonlocal last_status, started_written
        if status != last_status:
            if status == "生成中" and not started_written:
                _update_task(task_id, status=status, started_at=datetime.now())
                started_written = True
            else:
                _update_task(task_id, status=status)
            last_status = status

    # 配置缺失守卫：H3_BASE_URL 为空时 _api() 会拼出无 scheme 的 URL，
    # 每次查询必然失败 → 轮询线程最多空转 240 分钟刷屏。直接终止并落失败态。
    if not (settings.H3_BASE_URL or "").strip():
        _update_task(task_id, status="失败",
                     error_message="H3_BASE_URL 未配置（.env 需配置 ComfyUI 服务地址/账号密码）")
        logger.error(f"[H3视频] 任务 {task_id} 终止: H3_BASE_URL 未配置，请在 .env 配置 H3_BASE_URL / H3_AUTH_USER / H3_AUTH_PASSWORD")
        return

    try:
        consecutive_failures = 0  # 连续查询失败：配置缺失/服务不可达等确定性错误，快速失败
        timeout_failures = 0      # 连续查询超时：ComfyUI 串行执行重任务时响应慢属正常现象，容忍更久
        last_query_error = ""     # 最近一次查询异常（写入失败原因，便于直接定位配置/网络问题）
        for _ in range(max_polls):
            threading.Event().wait(interval)
            # 取消/状态变更检查：任务已不在进行中（如被用户取消）时退出轮询
            if _get_h3_task_status(task_id) not in ("排队中", "生成中"):
                logger.info(f"[H3视频] 任务 {task_id} 状态已变更，轮询退出")
                return
            try:
                history = _api("GET", f"/history/{h3_prompt_id}").json()
            except requests.exceptions.Timeout as e:
                # 超时不算服务故障：ComfyUI 忙时 HTTP 响应可能超过请求超时，任务本身仍在正常运行
                timeout_failures += 1
                if timeout_failures % 40 == 1:
                    logger.warning(
                        f"[H3视频] 任务 {task_id} 查询超时（连续 {timeout_failures} 次，ComfyUI 可能正忙）: {e}"
                    )
                if timeout_failures >= 240:
                    _update_task(
                        task_id, only_active=True, status="失败",
                        error_message="ComfyUI 长时间无响应（连续 1 小时查询超时），请检查 ComfyUI 服务后重新提交",
                        finished_at=datetime.now(),
                    )
                    logger.error(f"[H3视频] 任务 {task_id} 连续查询超时，终止轮询")
                    return
                continue
            except Exception as e:
                consecutive_failures += 1
                last_query_error = str(e)
                logger.warning(
                    f"[H3视频] 任务 {task_id} 查询失败（连续 {consecutive_failures} 次）: {e}"
                )
                if consecutive_failures >= 20:
                    _update_task(
                        task_id, only_active=True, status="失败",
                        error_message=f"ComfyUI 服务连接失败（连续 {consecutive_failures} 次查询异常，轮询间隔 {interval} 秒）：{last_query_error[:200]}；请检查服务器 H3_BASE_URL 等配置与网络后重试",
                        finished_at=datetime.now(),
                    )
                    logger.error(f"[H3视频] 任务 {task_id} 连续查询失败，终止轮询")
                    return
                continue
            consecutive_failures = 0
            timeout_failures = 0

            item = history.get(h3_prompt_id)
            if item is None:
                # 不在 history：查队列区分排队/执行中
                try:
                    queue = _api("GET", "/queue").json()
                except Exception as e:
                    logger.warning(f"[H3视频] 任务 {task_id} 查询队列失败: {e}")
                    continue
                running_ids = {e[1] for e in (queue.get("queue_running") or []) if len(e) > 1}
                pending_ids = {e[1] for e in (queue.get("queue_pending") or []) if len(e) > 1}
                if h3_prompt_id in running_ids:
                    set_status("生成中")
                elif h3_prompt_id in pending_ids:
                    set_status("排队中")
                # 两者都不在：瞬态（刚提交待入队/刚完成待写入 history），保持现状
                continue

            status_str = (item.get("status") or {}).get("status_str")
            if status_str == "success":
                finfo = _extract_output_file(item)
                if not finfo:
                    _update_task(
                        task_id, only_active=True, status="失败", error_message="生成完成但未找到成片文件",
                        finished_at=datetime.now(),
                    )
                    logger.error(f"[H3视频] 任务 {task_id} 找不到输出文件: {item.get('outputs')}")
                    return

                # 防重复收片：双进程同库场景下其他进程可能已完成本任务（下载+上传耗时长，竞态窗口大）
                if _get_h3_task_status(task_id) == "已完成":
                    logger.info(f"[H3视频] 任务 {task_id} 已被其他进程完成，跳过重复收片")
                    return

                content, filename = _download_video(finfo)
                logger.info(f"[H3视频] 任务 {task_id} 成片已下载，{len(content) // 1024}KB")
                video_url = tos_service.upload_file(
                    content, filename, content_type="video/mp4", subdir="ai-video",
                )
                _update_task(
                    task_id, only_active=True, status="已完成", video_url=video_url,
                    finished_at=datetime.now(), error_message=None,
                )
                logger.info(f"[H3视频] 任务 {task_id} 完成: {video_url}")
                return

            if status_str in ("error", "failed"):
                msgs = json.dumps(
                    (item.get("status") or {}).get("messages", []), ensure_ascii=False
                )[:800]
                _update_task(
                    task_id, only_active=True, status="失败", error_message=msgs or "生成失败",
                    finished_at=datetime.now(),
                )
                logger.error(f"[H3视频] 任务 {task_id} 生成失败: {msgs}")
                return

            # history 里但状态异常，兜底视为执行中
            set_status("生成中")

        _update_task(
            task_id, only_active=True, status="失败",
            error_message=f"生成超时（超过 {settings.H3_MAX_WAIT_MINUTES} 分钟）",
            finished_at=datetime.now(),
        )
        logger.error(f"[H3视频] 任务 {task_id} 等待超时")
    except Exception as e:
        # only_active：本异常多发生在收片阶段（下载/上传），此时其他进程可能已把任务置为已完成，不得覆盖
        logger.error(f"[H3视频] 任务 {task_id} 处理异常: {e}")
        _update_task(task_id, only_active=True, status="失败",
                     error_message=f"收片阶段异常（ComfyUI 生成结果不受影响，可点重试收片）：{str(e)[:600]}",
                     finished_at=datetime.now())
    finally:
        with _lock:
            _running.discard(task_id)


def _ensure_worker(task_id: int, h3_prompt_id: Optional[str] = None, *,
                   prep_kwargs: Optional[Dict[str, Any]] = None) -> None:
    """确保某任务有后台线程在跑（避免重复启动）

    - prep_kwargs 提供时：先执行准备（上传参考图/提示词优化/提交工作流）再轮询
    - 否则认为工作流已提交（服务重启恢复场景），直接轮询
    """
    with _lock:
        if task_id in _running:
            return
        _running.add(task_id)
    if prep_kwargs is not None:
        threading.Thread(
            target=_prepare_and_run, args=(task_id,), kwargs=prep_kwargs,
            daemon=True, name=f"h3-video-task-{task_id}",
        ).start()
    else:
        threading.Thread(
            target=_run_task, args=(task_id, h3_prompt_id), daemon=True,
            name=f"h3-video-task-{task_id}",
        ).start()


def submit_video_task(
    *,
    tenant_id: int,
    user_id: int,
    creator_name: str = "",
    title: str,
    prompt: str,
    images: List[Tuple[str, bytes]],
    duration: int,
    resolution: str,
    ratio: str,
    model: str,
    market: str,
    voiceover_language: str = "自动",
    video_type: str = "口播带货",
    product_code: str = "",
    product_name: str = "",
) -> Dict[str, Any]:
    """提交视频生成任务：落库后立即返回，准备（上传/优化/提交工作流）与轮询全部在后台线程

    :param images: [(文件名, 图片字节)]，1~9 张
    """
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    if not images:
        raise H3VideoError("请至少上传一张参考图")
    if len(images) > 9:
        raise H3VideoError("最多上传 9 张参考图")

    # 1. 先落库，拿到任务 ID（后续进度都回写到这条记录）
    db = SessionLocal()
    try:
        task = AIVideoTask(
            tenant_id=tenant_id, user_id=user_id, creator_name=creator_name,
            title=title, status="排队中", prompt=prompt,
            product_code=product_code or None, product_name=product_name or None,
            market=market, voiceover_language=voiceover_language, video_type=video_type,
            model=model, resolution=resolution, duration=duration, ratio=ratio,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id
    finally:
        db.close()

    # 2. 立即返回任务记录；参考图上传/提示词优化/工作流提交全部移入后台线程，
    #    避免 9 张图时前端转圈数分钟（多张 TOS 上传 + LLM 优化 + 多张 ComfyUI 上传）
    _ensure_worker(
        task_id,
        prep_kwargs={
            "title": title,
            "prompt": prompt,
            "images": images,
            "duration": duration,
            "resolution": resolution,
            "ratio": ratio,
            "voiceover_language": voiceover_language,
            "video_type": video_type,
        },
    )

    db = SessionLocal()
    try:
        return _serialize(db.query(AIVideoTask).filter(AIVideoTask.id == task_id).first())
    finally:
        db.close()


def _prepare_and_run(
    task_id: int,
    *,
    title: str,
    prompt: str,
    images: List[Tuple[str, bytes]],
    duration: int,
    resolution: str,
    ratio: str,
    voiceover_language: str,
    video_type: str = "口播带货",
) -> None:
    """后台线程：参考图传 TOS → 提示词二次优化 → 提交工作流 → 轮询直到完成

    全程在后台执行，接口层只负责落库后立即返回。
    """
    from services import tos_service

    try:
        with _lock:
            _running.add(task_id)

        # 1. 参考图存 TOS（并行上传），供前端「参考图片」展示
        def _to_tos(item: Tuple[str, bytes]) -> Optional[str]:
            name, content = item
            try:
                return tos_service.upload_file(content, name, subdir="ai-video")
            except Exception as e:
                logger.warning(f"[H3视频] 参考图上传 TOS 失败（不影响生成）: {e}")
                return None

        with ThreadPoolExecutor(max_workers=5) as ex:
            urls = [u for u in ex.map(_to_tos, images) if u]
        if urls:
            _update_task(task_id, images=json.dumps(urls, ensure_ascii=False))

        # 2. 提示词二次优化：按 H3 官方指南改写为 Ref2VA 六段式英文提示词；失败回退原始方案文本
        person_picture = _detect_person_picture(prompt, len(images))
        optimized_prompt = ""
        if settings.H3_PROMPT_OPTIMIZE:
            try:
                optimized_prompt = optimize_h3_prompt(
                    plan_prompt=prompt,
                    duration=duration,
                    ratio=ratio,
                    image_count=len(images),
                    voiceover_language=voiceover_language,
                    product_name=title,
                    person_picture=person_picture,
                    video_type=video_type,
                )
                _update_task(task_id, h3_prompt=optimized_prompt)
                logger.info(
                    f"[H3视频] 任务 {task_id} 提示词已优化，{len(optimized_prompt)} 字符"
                    + (f"，人物参考 <Picture {person_picture}>" if person_picture else "")
                )
            except Exception as e:
                logger.warning(f"[H3视频] 任务 {task_id} 提示词优化失败，回退原始提示词: {e}")

        # 3. 参考图并行上传 ComfyUI 后提交工作流
        try:
            def _to_comfy(item) -> str:
                idx, (_, content) = item
                return _upload_image(idx, content)

            with ThreadPoolExecutor(max_workers=5) as ex:
                image_names = list(ex.map(_to_comfy, enumerate(images)))

            workflow = _build_workflow(
                prompt=optimized_prompt or prompt,
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                image_names=image_names,
                with_ref_note=not optimized_prompt,
                person_picture=person_picture,
            )
            # 生图优先：有生图任务在等 ComfyUI 时先让路（上限 COMFY_IMAGE_PRIORITY_WAIT_SECONDS）
            from services import comfy_priority

            comfy_priority.wait_before_enqueue("H3视频", task_id)
            resp = _api("POST", "/prompt", json={"prompt": workflow})
            h3_prompt_id = resp.json()["prompt_id"]
        except Exception as e:
            logger.error(f"[H3视频] 任务 {task_id} 提交失败: {e}")
            _update_task(task_id, status="失败", error_message=str(e)[:900],
                         finished_at=datetime.now())
            with _lock:
                _running.discard(task_id)
            return

        # 提交成功仅代表进入 ComfyUI 队列，保持「排队中」；开始执行后由轮询线程按 /queue 判定改「生成中」
        _update_task(task_id, h3_prompt_id=h3_prompt_id)
        logger.info(f"[H3视频] 任务 {task_id} 已提交，ComfyUI ID={h3_prompt_id}")

        # 4. 同线程继续轮询进度直到完成（成功后 finally 里清除 _running）
        _run_task(task_id, h3_prompt_id)
    except Exception as e:
        logger.error(f"[H3视频] 任务 {task_id} 准备阶段异常: {e}")
        _update_task(task_id, only_active=True, status="失败", error_message=str(e)[:900],
                     finished_at=datetime.now())
        with _lock:
            _running.discard(task_id)


def _serialize(task) -> Dict[str, Any]:
    """任务记录 → 前端结构"""
    if not task:
        return {}
    try:
        images = json.loads(task.images) if task.images else []
    except Exception:
        images = []
    # 生成耗时（秒）：完成时间 - 开始生成时间（无开始时间回退提交时间，兼容历史数据）
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
        "title": task.title or "视频生成任务",
        "status": task.status,
        "creator_name": task.creator_name or "",
        "prompt": task.prompt or "",
        "h3_prompt": task.h3_prompt or "",
        "product_code": task.product_code or "",
        "product_name": task.product_name or "",
        "market": task.market,
        "voiceover_language": task.voiceover_language or "自动",
        "video_type": task.video_type or "口播带货",
        "model": task.model,
        "resolution": task.resolution,
        "duration": task.duration,
        "ratio": task.ratio,
        "images": images,
        "video_url": task.video_url,
        "h3_prompt_id": task.h3_prompt_id or "",
        "cost_seconds": cost_seconds,
        "error_message": task.error_message,
        "created_at": task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "",
    }


def list_video_tasks(
    tenant_id: int, page: int = 1, page_size: int = 10, keyword: str = "", bind_status: str = "all"
) -> Dict[str, Any]:
    """租户的视频生成任务列表（按时间倒序，不做账号隔离）。含失败任务及其失败原因。

    keyword 模糊匹配产品编码 / 产品品名 / 任务标题。
    bind_status: all=全部 / bound=已绑定 / unbound=未绑定（按审核通过的绑定申请判定成片地址）。
    返回 {"items": [...], "total": 总条数}，供前端分页，items 内含 bound 绑定状态。
    """
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask
    from models.video_binding_request import VideoBindingRequest
    from sqlalchemy import or_, text

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    db = SessionLocal()
    try:
        query = db.query(AIVideoTask).filter(
            AIVideoTask.tenant_id == tenant_id,
            AIVideoTask.deleted_at.is_(None),
        )
        keyword = (keyword or "").strip()
        if keyword:
            like = f"%{keyword}%"
            query = query.filter(
                or_(
                    AIVideoTask.product_code.ilike(like),
                    AIVideoTask.product_name.ilike(like),
                    AIVideoTask.title.ilike(like),
                )
            )
        # 绑定状态筛选：预计算"已绑定 URL 集合"，避免逐行相关子查询 + 全表 LIKE（切换标签慢的根因）
        # 绑定判定口径：审核通过的绑定申请，或产品视频列表已包含该地址（旧直绑数据无申请记录）。
        # products 里有视频的只有几百行，单列拉取远快于在 SQL 里对每行任务做相关子查询。
        approved_urls = {
            u
            for (u,) in db.query(VideoBindingRequest.video_url)
            .filter(
                VideoBindingRequest.tenant_id == tenant_id,
                VideoBindingRequest.status == "approved",
                VideoBindingRequest.deleted_at.is_(None),
            )
            .all()
            if u
        }
        bound_urls: set = set(approved_urls)
        prod_single = [
            u
            for (u,) in db.execute(
                text("SELECT video_url FROM products WHERE tenant_id = :tid AND deleted_at IS NULL "
                     "AND video_url IS NOT NULL AND video_url != ''"),
                {"tid": tenant_id},
            ).fetchall()
        ]
        bound_urls.update(prod_single)
        for (vids,) in db.execute(
            text("SELECT videos FROM products WHERE tenant_id = :tid AND deleted_at IS NULL "
                 "AND videos IS NOT NULL AND videos != ''"),
            {"tid": tenant_id},
        ).fetchall():
            try:
                parsed = json.loads(vids)
                if isinstance(parsed, list):
                    bound_urls.update(u for u in parsed if u)
                else:
                    bound_urls.add(parsed)
            except Exception:
                bound_urls.add(vids)
        # 该租户全部任务的 (id, video_url)，在 Python 里算出已绑定任务 id 集合
        task_url_pairs = (
            db.query(AIVideoTask.id, AIVideoTask.video_url)
            .filter(AIVideoTask.tenant_id == tenant_id, AIVideoTask.deleted_at.is_(None))
            .all()
        )
        bound_task_ids = {tid for tid, u in task_url_pairs if u and u in bound_urls}
        if bind_status == "bound":
            query = query.filter(AIVideoTask.id.in_(bound_task_ids)) if bound_task_ids else query.filter(False)
        elif bind_status == "unbound":
            query = query.filter(~AIVideoTask.id.in_(bound_task_ids)) if bound_task_ids else query
        total = query.count()
        rows = (
            query.order_by(AIVideoTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [_serialize(r) for r in rows]
        # 附加绑定状态：直接用预计算的 bound_urls 集合判断，无需再查库
        for r, item in zip(rows, items):
            item["bound"] = bool(r.video_url) and r.video_url in bound_urls
        # 附加已完成的超分结果（按源视频地址匹配，取最新一条），供前端并排对比展示
        try:
            from models.video_upscale_task import VideoUpscaleTask

            video_urls = [r.video_url for r in rows if r.video_url]
            if video_urls:
                up_rows = (
                    db.query(VideoUpscaleTask)
                    .filter(
                        VideoUpscaleTask.tenant_id == tenant_id,
                        VideoUpscaleTask.status == "已完成",
                        VideoUpscaleTask.deleted_at.is_(None),
                        VideoUpscaleTask.source_video_url.in_(video_urls),
                    )
                    .order_by(VideoUpscaleTask.id.desc())
                    .all()
                )
                mapping: Dict[str, str] = {}
                for up in up_rows:
                    if up.source_video_url and up.video_url:
                        mapping.setdefault(up.source_video_url, up.video_url)
                for r, item in zip(rows, items):
                    if r.video_url and r.video_url in mapping:
                        item["upscale_video_url"] = mapping[r.video_url]
        except Exception as e:
            logger.warning(f"生成历史附加超分结果失败: {e}")
        return {"items": items, "total": total}
    finally:
        db.close()


def _get_h3_task_status(task_id: int) -> Optional[str]:
    """读取任务当前状态（轮询循环内用于感知取消等状态变更）"""
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        r = db.query(AIVideoTask.status).filter(AIVideoTask.id == task_id).first()
        return r[0] if r else None
    finally:
        db.close()


def cancel_video_task(tenant_id: int, task_id: int) -> None:
    """取消排队中/生成中的任务：移出 ComfyUI 队列或中断执行，并标记为失败"""
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        task = db.query(AIVideoTask).filter(
            AIVideoTask.id == task_id,
            AIVideoTask.tenant_id == tenant_id,
            AIVideoTask.deleted_at.is_(None),
        ).first()
        if not task:
            raise H3VideoError("任务不存在或已删除")
        if task.status not in ("排队中", "生成中"):
            raise H3VideoError("任务已结束，无需取消")
        prompt_id = task.h3_prompt_id
    finally:
        db.close()

    # 同步 ComfyUI：执行中的中断，排队中的移出队列
    cancelled_via = "任务不在 ComfyUI 队列中（直接标记取消）"
    if prompt_id:
        try:
            queue = _api("GET", "/queue", timeout=30).json()
            running_ids = {e[1] for e in (queue.get("queue_running") or []) if len(e) > 1}
            pending_ids = {e[1] for e in (queue.get("queue_pending") or []) if len(e) > 1}
            if prompt_id in running_ids:
                _api("POST", "/interrupt", timeout=30)
                cancelled_via = "已中断 ComfyUI 执行中的任务"
            elif prompt_id in pending_ids:
                _api("POST", "/queue", timeout=30, json={"delete": [prompt_id]})
                cancelled_via = "已从 ComfyUI 队列移除"
        except Exception as e:
            logger.warning(f"[H3视频] 取消任务 {task_id} 时操作 ComfyUI 失败: {e}")
            cancelled_via = "ComfyUI 操作失败（任务仍将标记为取消）"

    db = SessionLocal()
    try:
        db.query(AIVideoTask).filter(
            AIVideoTask.id == task_id,
            AIVideoTask.status.in_(["排队中", "生成中"]),
        ).update({
            "status": "失败", "error_message": "任务已手动取消",
            "finished_at": datetime.now(),
        }, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    logger.info(f"[H3视频] 任务 {task_id} 已取消: {cancelled_via}")


def _download_reference_images(urls: List[str]) -> List[Tuple[str, bytes]]:
    """服务端并行下载历史参考图字节（同区域 TOS/CDN，不走浏览器，避开 CORS 与前端代理）。

    任一张失败即抛错：静默少图会改变生成内容，宁可明确失败。
    """
    import requests as _requests

    def _fetch(idx_url: Tuple[int, str]) -> Tuple[str, bytes]:
        idx, url = idx_url
        resp = _requests.get(url, timeout=60)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise H3VideoError(f"参考图 {idx + 1} 地址不是图片")
        return (f"ref-{idx + 1}.jpg", resp.content)

    with ThreadPoolExecutor(max_workers=5) as ex:
        return list(ex.map(_fetch, enumerate(urls)))


def _resubmit_failed_task(task) -> Tuple[str, str]:
    """失败任务原位重试：服务端下载历史参考图，重置原记录并后台重跑准备+轮询。

    复用原任务记录（id 不变），提示词与全部生成参数沿用原任务；
    重置后由 _prepare_and_run 重新上传参考图/优化提示词/提交工作流。
    """
    import json as _json

    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    try:
        urls = _json.loads(task.images) if task.images else []
    except Exception:
        urls = []
    urls = [u for u in urls if u]
    if not urls:
        raise H3VideoError("任务没有参考图地址，无法重试，请使用「编辑」重新上传")

    try:
        images = _download_reference_images(urls)
    except H3VideoError:
        raise
    except Exception as e:
        raise H3VideoError(f"参考图下载失败，请稍后重试: {str(e)[:200]}")

    db = SessionLocal()
    try:
        row = db.query(AIVideoTask).filter(AIVideoTask.id == task.id).first()
        if not row:
            raise H3VideoError("任务不存在或已删除")
        row.status = "排队中"
        row.error_message = None
        row.video_url = None
        row.h3_prompt_id = None
        row.started_at = None
        row.finished_at = None
        db.commit()
    finally:
        db.close()

    _ensure_worker(
        task.id,
        prep_kwargs={
            "title": task.title or "视频生成任务",
            "prompt": task.prompt or "",
            "images": images,
            "duration": task.duration,
            "resolution": task.resolution,
            "ratio": task.ratio,
            "voiceover_language": task.voiceover_language or "自动",
            "video_type": task.video_type or "口播带货",
        },
    )
    logger.info(f"[H3视频] 任务 {task.id} 原位重试：{len(images)} 张参考图已重新提交")
    return "resubmitted", "已重新提交生成，排队中"


def recover_failed_task(tenant_id: int, task_id: int) -> Tuple[Optional[str], str]:
    """智能重试失败的视频任务：先查 ComfyUI history/queue，能接续就接续原任务；
    无法接续（无提交记录/已无踪迹）时服务端直接取历史参考图原位重新提交。

    返回 (action, message)：action="resumed" 表示已接续原 ComfyUI 任务（后台轮询收片），
    action="resubmitted" 表示已复用原记录重新提交生成。
    """
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        task = db.query(AIVideoTask).filter(
            AIVideoTask.id == task_id,
            AIVideoTask.tenant_id == tenant_id,
            AIVideoTask.deleted_at.is_(None),
        ).first()
        if not task:
            raise H3VideoError("任务不存在或已删除")
        if task.status != "失败":
            raise H3VideoError("仅失败的任务可以重试")
        prompt_id = task.h3_prompt_id
    finally:
        db.close()

    if not prompt_id:
        # 从未成功提交到 ComfyUI（上传/优化阶段失败）：服务端取历史参考图原位重试
        return _resubmit_failed_task(task)

    # 查 ComfyUI history/queue 判断原提交的执行情况
    resume = False   # 保留 prompt_id 接续（已有成片或仍在生成）
    running = False  # 原任务仍在 ComfyUI 执行中/排队
    try:
        item = _api("GET", f"/history/{prompt_id}", timeout=30).json().get(prompt_id)
        if item and (item.get("status") or {}).get("status_str") == "success" \
                and _extract_output_file(item):
            resume = True
        elif not item:
            queue = _api("GET", "/queue", timeout=30).json()
            running_ids = {e[1] for e in (queue.get("queue_running") or []) if len(e) > 1}
            pending_ids = {e[1] for e in (queue.get("queue_pending") or []) if len(e) > 1}
            if prompt_id in running_ids or prompt_id in pending_ids:
                resume = True
                running = True
    except Exception as e:
        # 查询失败（网络抖动等）：保守接续，由后台轮询线程继续判断
        logger.warning(f"[H3视频] 重试任务 {task_id} 查询 ComfyUI 失败，保守接续轮询: {e}")
        resume = True

    if not resume:
        return _resubmit_failed_task(task)

    db = SessionLocal()
    try:
        task = db.query(AIVideoTask).filter(AIVideoTask.id == task_id).first()
        task.status = "生成中"
        task.error_message = None
        task.finished_at = None
        db.commit()
    finally:
        db.close()

    message = "检测到 ComfyUI 仍在生成中，继续等待原任务完成" if running \
        else "检测到 ComfyUI 已有成片，正在收片回填"
    logger.info(f"[H3视频] 重试任务 {task_id}：{message}（prompt_id={prompt_id}）")
    _ensure_worker(task_id, prompt_id)
    return "resumed", message


def delete_video_task(tenant_id: int, task_id: int) -> None:
    """删除任务记录（软删除，不影响已生成的成片）"""
    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        db.query(AIVideoTask).filter(
            AIVideoTask.id == task_id, AIVideoTask.tenant_id == tenant_id
        ).update({"deleted_at": datetime.now()})
        db.commit()
    finally:
        db.close()


def resume_pending_tasks() -> Optional[int]:
    """服务启动时恢复未完成的任务：重新挂上后台轮询线程

    返回值：恢复的任务数；H3_BASE_URL 未配置时返回 None（跳过恢复）。
    配置为空的进程接手任务会因无法查询 ComfyUI 而把任务误标失败，
    因此守卫必须读取本进程启动时冻结的 settings，与轮询逻辑口径一致。
    """
    if not settings.H3_BASE_URL:
        logger.error(
            "[H3视频] H3_BASE_URL 未配置，跳过未完成任务恢复："
            "本进程无法查询 ComfyUI，接手任务会误标失败。"
            "请检查 backend/.env 的 H3_BASE_URL/H3_AUTH_USER/H3_AUTH_PASSWORD 后重启"
        )
        return None

    from database.database import SessionLocal
    from models.ai_video_task import AIVideoTask

    db = SessionLocal()
    try:
        rows = (
            db.query(AIVideoTask)
            .filter(
                AIVideoTask.status.in_(["排队中", "生成中"]),
                AIVideoTask.h3_prompt_id.isnot(None),
                AIVideoTask.deleted_at.is_(None),
            )
            .all()
        )
        pending = [(r.id, r.h3_prompt_id) for r in rows]

        # 未拿到 ComfyUI 任务 ID 的记录已无法追踪，直接标记失败，避免一直停在“排队中”
        orphan = db.query(AIVideoTask).filter(
            AIVideoTask.status.in_(["排队中", "生成中"]),
            AIVideoTask.h3_prompt_id.is_(None),
            AIVideoTask.deleted_at.is_(None),
        )
        orphan_count = orphan.update(
            {"status": "失败", "error_message": "服务重启导致任务中断，请重新提交",
             "finished_at": datetime.now()},
            synchronize_session=False,
        )
        db.commit()
        if orphan_count:
            logger.warning(f"[H3视频] {orphan_count} 条未提交成功的任务已标记为失败")
    finally:
        db.close()

    for task_id, h3_prompt_id in pending:
        logger.info(f"[H3视频] 恢复未完成任务 {task_id}（ComfyUI {h3_prompt_id}）")
        _ensure_worker(task_id, h3_prompt_id)

    return len(pending)