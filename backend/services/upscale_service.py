"""视频超分（Topaz 星光 2.6）服务

与 H3 视频生成共用同一台 ComfyUI（串行排队）。流程：
下载源视频 → 上传 ComfyUI → 构建超分工作流入队 → 后台轮询 → 下载成片 → 上传 TOS。
"""
import io
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_SCALE = 4.0  # 与工作流模板 TopazStarlight 放大倍数上限一致


class UpscaleError(Exception):
    """超分任务异常"""
    pass


def _api(method: str, path: str, timeout: int = 60, **kwargs) -> requests.Response:
    """带 Basic Auth 的 ComfyUI 接口请求"""
    url = settings.H3_BASE_URL.rstrip("/") + path
    resp = requests.request(
        method,
        url,
        auth=(settings.H3_AUTH_USER, settings.H3_AUTH_PASSWORD),
        timeout=timeout,
        **kwargs,
    )
    if resp.status_code == 401:
        raise UpscaleError("H3 服务认证失败，请检查账号密码")
    resp.raise_for_status()
    return resp


def _upload_video(content: bytes) -> str:
    """源视频上传 ComfyUI input 目录，返回服务器端文件名（失败重试一次）"""
    name = f"upscale-{int(time.time() * 1000)}.mp4"
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
            body = (e.response.text or "")[:200] if e.response is not None else ""
            last_err = f"{e}; {body}"
            logger.warning(f"[超分] 源视频上传第 {attempt} 次失败: {last_err}")
            threading.Event().wait(2)
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[超分] 源视频上传第 {attempt} 次异常: {e}")
            threading.Event().wait(2)
    raise UpscaleError(f"源视频上传 ComfyUI 失败（已重试）: {last_err}")


def _build_workflow(video_name: str, scale: float) -> Dict[str, Any]:
    """读取超分工作流模板并填入本次参数"""
    try:
        with open(settings.UPSCALE_WORKFLOW_PATH, encoding="utf-8") as f:
            wf = json.load(f)
    except Exception as e:
        raise UpscaleError(f"读取超分工作流模板失败: {e}")

    wf["27"]["inputs"]["video"] = video_name
    wf["24"]["inputs"]["放大倍数"] = float(scale)
    return wf


def _extract_output_file(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 ComfyUI 结果中取出超分成片文件信息（VHS_VideoCombine 的 gifs/videos）"""
    finfo = None
    for node_out in (item.get("outputs") or {}).values():
        for key in ("gifs", "videos", "images"):
            for f in node_out.get(key) or []:
                finfo = f
    return finfo


def _download(url: str, timeout: int = 1800) -> bytes:
    """流式下载文件到内存；网络中断（IncompleteRead 等）自动重试 3 次"""
    last_err: Exception = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_content(1 << 20):
                buf.write(chunk)
            return buf.getvalue()
        except Exception as e:
            last_err = e
            if attempt < 3:
                logger.warning(f"[超分] 文件下载中断（第 {attempt} 次），5 秒后重试: {e}")
                threading.Event().wait(5)
    raise last_err


def _download_comfy_file(finfo: Dict[str, Any]) -> Tuple[bytes, str]:
    """按 ComfyUI 文件信息下载结果文件；网络中断自动重试 3 次"""
    last_err: Exception = None
    for attempt in range(1, 4):
        try:
            resp = _api("GET", "/view", timeout=1800, params=finfo, stream=True)
            buf = io.BytesIO()
            for chunk in resp.iter_content(1 << 20):
                buf.write(chunk)
            return buf.getvalue(), finfo.get("filename") or "upscaled.mp4"
        except Exception as e:
            last_err = e
            if attempt < 3:
                logger.warning(f"[超分] 成片下载中断（第 {attempt} 次），5 秒后重试: {e}")
                threading.Event().wait(5)
    raise last_err


def _update_task(task_id: int, only_active: bool = False, **fields) -> None:
    """独立会话更新任务记录（后台线程中使用）

    only_active=True 时仅在任务仍处于「排队中/处理中」才写入，
    防止双后端同库场景下，本进程的迟到结果覆盖另一进程已写入的「已完成」。
    """
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        q = db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id)
        if only_active:
            q = q.filter(VideoUpscaleTask.status.in_(["排队中", "处理中"]))
        updated = q.update(fields, synchronize_session=False)
        db.commit()
        if only_active and not updated:
            logger.info(f"[超分] 任务 {task_id} 状态已被其他进程更新，跳过写入 {fields.get('status')}")
    except Exception as e:
        logger.error(f"[超分] 更新任务 {task_id} 状态失败: {e}")
        db.rollback()
    finally:
        db.close()


_lock = threading.Lock()
_running: set = set()


def _run_task(task_id: int) -> None:
    """后台线程：下载源视频 → 上传入队 → 轮询 → 成片上传 TOS

    说明：源视频下载/上传在拿到 ComfyUI 队列位后才开始（线程内串行），
    期间任务状态保持「排队中」，入队成功后改「处理中」。
    """
    from services import tos_service

    with _lock:
        if task_id in _running:
            return
        _running.add(task_id)

    interval = max(5, int(settings.H3_POLL_INTERVAL))
    max_polls = max(1, int(settings.H3_MAX_WAIT_MINUTES * 60 / interval))
    last_status = ""
    started_written = False

    def set_status(status: str) -> None:
        nonlocal last_status, started_written
        if status != last_status:
            if status == "处理中" and not started_written:
                _update_task(task_id, status=status, started_at=datetime.now())
                started_written = True
            else:
                _update_task(task_id, status=status)
            last_status = status

    try:
        # 1. 取任务信息并下载源视频
        from database.database import SessionLocal
        from models.video_upscale_task import VideoUpscaleTask

        db = SessionLocal()
        try:
            task = db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id).first()
            source_url = task.source_video_url if task else None
            existing_prompt_id = task.comfy_prompt_id if task else None
        finally:
            db.close()
        if not source_url:
            raise UpscaleError("找不到源视频地址")

        if existing_prompt_id:
            # 服务重启恢复：工作流已提交 ComfyUI，直接接续轮询，不重复上传/入队（与 H3 恢复逻辑一致）
            prompt_id = existing_prompt_id
            logger.info(f"[超分] 任务 {task_id} 接续轮询 ComfyUI {prompt_id}")
        else:
            logger.info(f"[超分] 任务 {task_id} 开始下载源视频: {source_url}")
            content = _download(source_url)
            logger.info(f"[超分] 任务 {task_id} 源视频已下载，{len(content) // 1024}KB")

            # 2. 上传 ComfyUI 并提交工作流
            video_name = _upload_video(content)
            workflow = _build_workflow(video_name, _get_scale(task_id))
            # 生图优先：有生图任务在等 ComfyUI 时先让路（上限 COMFY_IMAGE_PRIORITY_WAIT_SECONDS）
            from services import comfy_priority

            comfy_priority.wait_before_enqueue("超分", task_id)
            resp = _api("POST", "/prompt", json={"prompt": workflow})
            prompt_id = resp.json()["prompt_id"]
            _update_task(task_id, comfy_prompt_id=prompt_id)
            logger.info(f"[超分] 任务 {task_id} 已提交，ComfyUI ID={prompt_id}")

        # 3. 轮询直到完成
        consecutive_failures = 0  # 连续查询失败：配置缺失/服务不可达等确定性错误，快速失败
        timeout_failures = 0      # 连续查询超时：ComfyUI 串行执行重任务时响应慢属正常现象，容忍更久
        last_query_error = ""     # 最近一次查询异常（写入失败原因，便于直接定位配置/网络问题）
        for _ in range(max_polls):
            threading.Event().wait(interval)
            # 取消/状态变更检查：任务已不在进行中（如被用户取消）时退出轮询
            if _get_upscale_task_status(task_id) not in ("排队中", "处理中"):
                logger.info(f"[超分] 任务 {task_id} 状态已变更，轮询退出")
                return
            try:
                history = _api("GET", f"/history/{prompt_id}").json()
            except requests.exceptions.Timeout as e:
                # 超时不算服务故障：ComfyUI 忙时 HTTP 响应可能超过请求超时，任务本身仍在正常运行
                timeout_failures += 1
                if timeout_failures % 40 == 1:
                    logger.warning(
                        f"[超分] 任务 {task_id} 查询超时（连续 {timeout_failures} 次，ComfyUI 可能正忙）: {e}"
                    )
                if timeout_failures >= 240:
                    _update_task(task_id, only_active=True, status="失败",
                                 error_message="ComfyUI 长时间无响应（连续 1 小时查询超时），请检查 ComfyUI 服务后重新提交",
                                 finished_at=datetime.now())
                    logger.error(f"[超分] 任务 {task_id} 连续查询超时，终止轮询")
                    return
                continue
            except Exception as e:
                consecutive_failures += 1
                last_query_error = str(e)
                logger.warning(
                    f"[超分] 任务 {task_id} 查询失败（连续 {consecutive_failures} 次）: {e}"
                )
                if consecutive_failures >= 20:
                    _update_task(task_id, only_active=True, status="失败",
                                 error_message=f"ComfyUI 服务连接失败（连续 {consecutive_failures} 次查询异常，轮询间隔 {interval} 秒）：{last_query_error[:200]}；请检查服务器 H3_BASE_URL 等配置与网络后重试",
                                 finished_at=datetime.now())
                    logger.error(f"[超分] 任务 {task_id} 连续查询失败，终止轮询")
                    return
                continue
            consecutive_failures = 0
            timeout_failures = 0

            item = history.get(prompt_id)
            if item is None:
                try:
                    queue = _api("GET", "/queue").json()
                except Exception as e:
                    logger.warning(f"[超分] 任务 {task_id} 查询队列失败: {e}")
                    continue
                running_ids = {e[1] for e in (queue.get("queue_running") or []) if len(e) > 1}
                pending_ids = {e[1] for e in (queue.get("queue_pending") or []) if len(e) > 1}
                if prompt_id in running_ids:
                    set_status("处理中")
                elif prompt_id in pending_ids:
                    set_status("排队中")
                continue

            status_str = (item.get("status") or {}).get("status_str")
            if status_str == "success":
                finfo = _extract_output_file(item)
                if not finfo:
                    _update_task(task_id, only_active=True, status="失败", error_message="处理完成但未找到输出文件",
                                 finished_at=datetime.now())
                    logger.error(f"[超分] 任务 {task_id} 找不到输出文件: {item.get('outputs')}")
                    return

                # 防重复收片：双进程同库场景下其他进程可能已完成本任务（下载+上传耗时长，竞态窗口大）
                if _get_upscale_task_status(task_id) == "已完成":
                    logger.info(f"[超分] 任务 {task_id} 已被其他进程完成，跳过重复收片")
                    return

                result, filename = _download_comfy_file(finfo)
                logger.info(f"[超分] 任务 {task_id} 成片已下载，{len(result) // 1024}KB")
                video_url = tos_service.upload_file(
                    result, filename, content_type="video/mp4", subdir="upscale-video",
                )
                _update_task(task_id, only_active=True, status="已完成", video_url=video_url,
                             finished_at=datetime.now(), error_message=None)
                logger.info(f"[超分] 任务 {task_id} 完成: {video_url}")
                return

            if status_str in ("error", "failed"):
                msgs = json.dumps((item.get("status") or {}).get("messages", []),
                                  ensure_ascii=False)[:800]
                _update_task(task_id, only_active=True, status="失败", error_message=msgs or "处理失败",
                             finished_at=datetime.now())
                logger.error(f"[超分] 任务 {task_id} 处理失败: {msgs}")
                return

            set_status("处理中")

        _update_task(task_id, only_active=True, status="失败",
                     error_message=f"处理超时（超过 {settings.H3_MAX_WAIT_MINUTES} 分钟）",
                     finished_at=datetime.now())
        logger.error(f"[超分] 任务 {task_id} 等待超时")
    except Exception as e:
        # only_active：本异常多发生在收片阶段（下载/上传），此时其他进程可能已把任务置为已完成，不得覆盖
        logger.error(f"[超分] 任务 {task_id} 处理异常: {e}")
        _update_task(task_id, only_active=True, status="失败",
                     error_message=f"收片阶段异常（ComfyUI 生成结果不受影响，可点重试收片）：{str(e)[:600]}",
                     finished_at=datetime.now())
    finally:
        with _lock:
            _running.discard(task_id)


def _get_scale(task_id: int) -> float:
    """读取任务的放大倍数"""
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        task = db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id).first()
        return float(task.scale) if task and task.scale else 2.0
    finally:
        db.close()


def _ensure_worker(task_id: int) -> None:
    """确保某任务有后台线程在跑（避免重复启动）"""
    with _lock:
        if task_id in _running:
            return
    threading.Thread(
        target=_run_task, args=(task_id,), daemon=True,
        name=f"upscale-task-{task_id}",
    ).start()


def submit_upscale_task(
    *,
    tenant_id: int,
    user_id: int,
    creator_name: str = "",
    title: str,
    source_video_url: str,
    source_width: int = 0,
    source_height: int = 0,
    scale: float = 2.0,
) -> Dict[str, Any]:
    """提交超分任务：落库 + 启动后台线程（下载/上传/入队/轮询均在后台）"""
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    if not source_video_url:
        raise UpscaleError("缺少源视频地址")
    if not 1.0 <= float(scale) <= MAX_SCALE:
        raise UpscaleError(f"放大倍数需在 1.0 ~ {MAX_SCALE} 之间")

    db = SessionLocal()
    try:
        task = VideoUpscaleTask(
            tenant_id=tenant_id, user_id=user_id, creator_name=creator_name,
            title=title, status="排队中",
            source_video_url=source_video_url,
            source_width=source_width or None, source_height=source_height or None,
            scale=float(scale),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id
    finally:
        db.close()

    logger.info(
        f"[超分] 用户 {user_id} 提交任务 {task_id}: 倍数={scale}, "
        f"源={source_width}x{source_height}, {source_video_url}"
    )
    _ensure_worker(task_id)

    db = SessionLocal()
    try:
        return _serialize(db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id).first())
    finally:
        db.close()


def _serialize(task) -> Dict[str, Any]:
    """任务记录 → 前端结构"""
    if not task:
        return {}
    cost_seconds = None
    if task.started_at and task.finished_at:
        cost_seconds = int((task.finished_at - task.started_at).total_seconds())
    return {
        "id": task.id,
        "title": task.title or "",
        "creator_name": task.creator_name or "",
        "source_video_url": task.source_video_url or "",
        "source_width": task.source_width,
        "source_height": task.source_height,
        "scale": task.scale,
        "status": task.status,
        "error_message": task.error_message or "",
        "video_url": task.video_url or "",
        "created_at": task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "",
        "cost_seconds": cost_seconds,
    }


def list_upscale_tasks(
    tenant_id: int, page: int = 1, page_size: int = 20, keyword: str = "", bind_status: str = "all"
) -> Dict[str, Any]:
    """租户的超分任务列表（按时间倒序），keyword 模糊匹配任务标题。

    bind_status: all=全部 / bound=已绑定 / unbound=未绑定（按审核通过的绑定申请判定，成片或源视频地址一致均可）。
    """
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask
    from models.video_binding_request import VideoBindingRequest
    from sqlalchemy import or_, text, bindparam

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    db = SessionLocal()
    try:
        query = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.tenant_id == tenant_id,
            VideoUpscaleTask.deleted_at.is_(None),
        )
        keyword = (keyword or "").strip()
        if keyword:
            like = f"%{keyword}%"
            query = query.filter(or_(VideoUpscaleTask.title.ilike(like)))
        # 绑定状态筛选：审核通过的绑定申请命中，或产品视频列表已包含成片/源视频地址（旧直绑数据无申请记录）
        # products 表无 ORM 模型映射，产品包含判定用 text SQL 相关子查询
        bind_exists = or_(
            db.query(VideoBindingRequest.id)
            .filter(
                VideoBindingRequest.tenant_id == tenant_id,
                VideoBindingRequest.status == "approved",
                VideoBindingRequest.deleted_at.is_(None),
                or_(
                    VideoBindingRequest.video_url == VideoUpscaleTask.video_url,
                    VideoBindingRequest.video_url == VideoUpscaleTask.source_video_url,
                ),
            )
            .exists(),
            text(
                "EXISTS (SELECT 1 FROM products p "
                "WHERE p.tenant_id = :bs_tid AND p.deleted_at IS NULL "
                "AND (p.video_url = video_upscale_tasks.video_url "
                "OR p.videos LIKE CONCAT('%', video_upscale_tasks.video_url, '%') "
                "OR p.video_url = video_upscale_tasks.source_video_url "
                "OR p.videos LIKE CONCAT('%', video_upscale_tasks.source_video_url, '%')))"
            ).bindparams(bs_tid=tenant_id),
        )
        if bind_status == "bound":
            query = query.filter(bind_exists)
        elif bind_status == "unbound":
            query = query.filter(~bind_exists)
        total = query.count()
        rows = (
            query.order_by(VideoUpscaleTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [_serialize(r) for r in rows]
        # 附加绑定状态：approved 申请命中，或产品视频列表已包含成片/源视频地址（旧直绑数据）
        try:
            check_urls = []
            for r in rows:
                if r.video_url:
                    check_urls.append(r.video_url)
                if r.source_video_url:
                    check_urls.append(r.source_video_url)
            bound_urls = set()
            if check_urls:
                bound_urls = {
                    u
                    for (u,) in db.query(VideoBindingRequest.video_url)
                    .filter(
                        VideoBindingRequest.tenant_id == tenant_id,
                        VideoBindingRequest.status == "approved",
                        VideoBindingRequest.deleted_at.is_(None),
                        VideoBindingRequest.video_url.in_(check_urls),
                    )
                    .all()
                }
                remaining = [u for u in check_urls if u not in bound_urls]
                if remaining:
                    like_parts = " OR ".join(
                        [f"videos LIKE CONCAT('%', :u{i}, '%')" for i in range(len(remaining))]
                    )
                    prods = db.execute(
                        text(
                            "SELECT video_url, videos FROM products "
                            "WHERE tenant_id = :tid AND deleted_at IS NULL AND "
                            "(video_url IN :urls OR " + like_parts + ")"
                        ).bindparams(bindparam("urls", expanding=True)),
                        {
                            "tid": tenant_id,
                            "urls": remaining,
                            **{f"u{i}": u for i, u in enumerate(remaining)},
                        },
                    ).fetchall()
                    for pv, pv_ids in prods:
                        blob = (pv or "") + (pv_ids or "")
                        for u in remaining:
                            if u in blob:
                                bound_urls.add(u)
            for r, item in zip(rows, items):
                item["bound"] = (
                    bool(r.video_url) and r.video_url in bound_urls
                ) or (
                    bool(r.source_video_url) and r.source_video_url in bound_urls
                )
        except Exception as e:
            logger.warning(f"超分列表附加绑定状态失败: {e}")
        return {"items": items, "total": total}
    finally:
        db.close()


def _get_upscale_task_status(task_id: int):
    """读取任务当前状态（轮询循环内用于感知取消等状态变更）"""
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        r = db.query(VideoUpscaleTask.status).filter(VideoUpscaleTask.id == task_id).first()
        return r[0] if r else None
    finally:
        db.close()


def cancel_upscale_task(tenant_id: int, task_id: int) -> None:
    """取消排队中/处理中的超分任务：移出 ComfyUI 队列或中断执行，并标记为失败"""
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        task = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.id == task_id,
            VideoUpscaleTask.tenant_id == tenant_id,
            VideoUpscaleTask.deleted_at.is_(None),
        ).first()
        if not task:
            raise UpscaleError("任务不存在或已删除")
        if task.status not in ("排队中", "处理中"):
            raise UpscaleError("任务已结束，无需取消")
        prompt_id = task.comfy_prompt_id
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
            logger.warning(f"[超分] 取消任务 {task_id} 时操作 ComfyUI 失败: {e}")
            cancelled_via = "ComfyUI 操作失败（任务仍将标记为取消）"

    db = SessionLocal()
    try:
        db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.id == task_id,
            VideoUpscaleTask.status.in_(["排队中", "处理中"]),
        ).update({
            "status": "失败", "error_message": "任务已手动取消",
            "finished_at": datetime.now(),
        }, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    logger.info(f"[超分] 任务 {task_id} 已取消: {cancelled_via}")


def retry_upscale_task(tenant_id: int, task_id: int) -> Tuple[Dict[str, Any], str]:
    """重试失败的超分任务。

    先查 ComfyUI history：原提交已有完整成片时保留 comfy_prompt_id 接续收片
    （后台线程接续轮询第一轮即收片回填，ComfyUI 不重跑）；无成片才清空重置、
    重新走完整流程（下载/上传/入队/轮询）。返回 (任务序列化, 提示文案)。
    """
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        task = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.tenant_id == tenant_id,
            VideoUpscaleTask.id == task_id,
            VideoUpscaleTask.deleted_at.is_(None),
        ).first()
        if not task:
            raise UpscaleError("任务不存在或已删除")
        if task.status != "失败":
            raise UpscaleError("仅失败的任务可以重试")
        if not task.source_video_url:
            raise UpscaleError("找不到源视频地址，无法重试")
        prompt_id = task.comfy_prompt_id
    finally:
        db.close()

    # 查 ComfyUI history/queue 判断原提交的执行情况
    resume_prompt = False   # 保留 prompt_id 接续（已有成片或仍在生成）
    resume_running = False  # 原任务仍在 ComfyUI 执行中/排队
    if prompt_id:
        try:
            item = _api("GET", f"/history/{prompt_id}", timeout=30).json().get(prompt_id)
            if item and (item.get("status") or {}).get("status_str") == "success" \
                    and _extract_output_file(item):
                resume_prompt = True
            elif not item:
                # 不在 history：查队列，仍在生成/排队则接续等待原任务完成，不重复提交
                queue = _api("GET", "/queue", timeout=30).json()
                running_ids = {e[1] for e in (queue.get("queue_running") or []) if len(e) > 1}
                pending_ids = {e[1] for e in (queue.get("queue_pending") or []) if len(e) > 1}
                if prompt_id in running_ids or prompt_id in pending_ids:
                    resume_prompt = True
                    resume_running = True
        except Exception as e:
            # 查询失败（网络抖动等）：保守保留 prompt_id 接续轮询，由后台线程继续判断
            logger.warning(f"[超分] 重试任务 {task_id} 查询 ComfyUI history 失败，保守接续轮询: {e}")
            resume_prompt = True

    db = SessionLocal()
    try:
        task = db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id).first()
        # 接续场景直接置「处理中」（不经过排队观感，ComfyUI 无需重跑，仅等待收片回填）；
        # 原任务仍在 ComfyUI 排队时置「排队中」如实反映
        if resume_prompt:
            task.status = "排队中" if resume_running else "处理中"
        else:
            task.status = "排队中"
        task.error_message = None
        task.started_at = None
        task.finished_at = None
        task.video_url = None
        if not resume_prompt:
            # 无成片且不在生成：清空原 ComfyUI 任务 ID，重新走完整流程
            task.comfy_prompt_id = None
        db.commit()
    finally:
        db.close()

    if resume_prompt:
        message = "检测到 ComfyUI 仍在生成中，继续等待原任务完成" if resume_running \
            else "检测到 ComfyUI 已有成片，正在收片回填"
    else:
        message = "已重新提交超分任务，排队中"
    logger.info(f"[超分] 重试任务 {task_id}：{message}（prompt_id={prompt_id}）")
    _ensure_worker(task_id)

    db = SessionLocal()
    try:
        return _serialize(db.query(VideoUpscaleTask).filter(VideoUpscaleTask.id == task_id).first()), message
    finally:
        db.close()


def delete_upscale_task(tenant_id: int, task_id: int) -> None:
    """软删除超分任务"""
    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        now = datetime.now()
        updated = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.id == task_id,
            VideoUpscaleTask.tenant_id == tenant_id,
            VideoUpscaleTask.deleted_at.is_(None),
        ).update({"deleted_at": now}, synchronize_session=False)
        db.commit()
        if not updated:
            raise UpscaleError("任务不存在或已删除")
    finally:
        db.close()


def resume_pending_tasks() -> Optional[int]:
    """服务启动时恢复未完成的超分任务：已有 ComfyUI 任务 ID 的接续轮询，未提交成功的标失败（与 H3 恢复逻辑一致）

    返回值：恢复的任务数；H3_BASE_URL 未配置时返回 None（跳过恢复）。
    配置为空的进程接手任务会因无法查询 ComfyUI 而把任务误标失败。
    """
    if not settings.H3_BASE_URL:
        logger.error(
            "[超分] H3_BASE_URL 未配置，跳过未完成任务恢复："
            "本进程无法查询 ComfyUI，接手任务会误标失败。"
            "请检查 backend/.env 的 H3_BASE_URL/H3_AUTH_USER/H3_AUTH_PASSWORD 后重启"
        )
        return None

    from database.database import SessionLocal
    from models.video_upscale_task import VideoUpscaleTask

    db = SessionLocal()
    try:
        rows = (
            db.query(VideoUpscaleTask)
            .filter(
                VideoUpscaleTask.status.in_(["排队中", "处理中"]),
                VideoUpscaleTask.comfy_prompt_id.isnot(None),
                VideoUpscaleTask.deleted_at.is_(None),
            )
            .all()
        )
        pending = [(r.id, r.comfy_prompt_id) for r in rows]

        # 未拿到 ComfyUI 任务 ID 的记录已无法追踪，直接标记失败，避免一直停在“排队中”
        orphan = db.query(VideoUpscaleTask).filter(
            VideoUpscaleTask.status.in_(["排队中", "处理中"]),
            VideoUpscaleTask.comfy_prompt_id.is_(None),
            VideoUpscaleTask.deleted_at.is_(None),
        )
        orphan_count = orphan.update(
            {"status": "失败", "error_message": "服务重启导致任务中断，请重新提交",
             "finished_at": datetime.now()},
            synchronize_session=False,
        )
        db.commit()
        if orphan_count:
            logger.warning(f"[超分] {orphan_count} 条未提交成功的任务已标记为失败")
    finally:
        db.close()

    for task_id, prompt_id in pending:
        logger.info(f"[超分] 恢复未完成任务 {task_id}（ComfyUI {prompt_id}）")
        _ensure_worker(task_id)

    return len(pending)
