"""ComfyUI 串行队列的跨业务优先级（生图 > 超分 / 视频）。

背景：生图、超分、H3 视频共用同一个 ComfyUI 串行队列。ComfyUI 是 FIFO 且没有
优先级接口 —— 任务一旦入队就无法插到前面，所以「一条 10 分钟的超分把整单生图
堵在后面」（2026-10-26 实测 4 条超分占满队列）只能在**入队前**解决。

做法：
- 生图任务在「提交/轮询」期间登记为 active（`begin_image_task` / `end_image_task`）；
- 超分与视频在 `POST /prompt` 之前调用 `wait_before_enqueue()`，若生图正在等处则让路；
- 让路有上限（默认 180s）：生图长期占位时视频任务仍会入队，不能把它饿死。

中转站（relay）生图不占 ComfyUI 队列，不登记，避免无谓地拖慢视频任务。
"""

import logging
import threading
import time
from typing import Optional, Set

from config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_image_tasks: Set[int] = set()

_POLL_SECONDS = 2.0


def _enabled() -> bool:
    """优先级开关（配置缺失/为假时完全退化回原来的 FIFO 行为）"""
    raw = str(getattr(get_settings(), "COMFY_IMAGE_PRIORITY", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _wait_seconds() -> float:
    try:
        return max(0.0, float(getattr(get_settings(), "COMFY_IMAGE_PRIORITY_WAIT_SECONDS", 180)))
    except Exception:
        return 180.0


def begin_image_task(task_id: int) -> None:
    """登记一个正在占用 ComfyUI 的生图任务（幂等）"""
    if not _enabled():
        return
    with _lock:
        _active_image_tasks.add(int(task_id))


def end_image_task(task_id: int) -> None:
    """生图任务结束/中断时注销（幂等；任务未登记过也不报错）"""
    with _lock:
        _active_image_tasks.discard(int(task_id))


def active_image_tasks() -> int:
    with _lock:
        return len(_active_image_tasks)


def image_priority_state() -> dict:
    """供日志/测试观察当前状态"""
    return {
        "enabled": _enabled(),
        "active": active_image_tasks(),
        "wait_seconds": _wait_seconds(),
    }


def wait_before_enqueue(
    kind: str, task_id: Optional[int] = None, *, timeout: Optional[float] = None,
) -> float:
    """超分/视频入队前让路给生图，返回实际等待秒数。

    只等「生图正在提交或轮询」的窗口，且最多等 `timeout`（默认取配置 180s）；
    超时后照常入队 —— 宁可让视频稍慢，也不能因为一次生图卡死而永久阻塞视频。
    """
    if not _enabled():
        return 0.0
    limit = _wait_seconds() if timeout is None else max(0.0, float(timeout))
    if limit <= 0:
        return 0.0

    started = time.time()
    deadline = started + limit
    logged = False
    while time.time() < deadline:
        n = active_image_tasks()
        if n <= 0:
            if not logged:
                return 0.0  # 一开始就没人占队列：不算让路，返回 0 便于调用方区分
            logger.info(
                f"[队列优先] {kind} 任务 {task_id} 等待 {time.time() - started:.1f}s 后入队"
                f"（生图已提交完毕）"
            )
            return time.time() - started
        if not logged:
            logger.info(
                f"[队列优先] {kind} 任务 {task_id} 让路给 {n} 个进行中的生图任务，"
                f"最多等待 {limit:.0f}s"
            )
            logged = True
        # 末段只睡剩余时间：让路上限必须准时生效，不能因为一个轮询周期就超时很久
        time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.time())))

    if logged:
        logger.warning(
            f"[队列优先] {kind} 任务 {task_id} 等待生图 {limit:.0f}s 仍未结束，直接入队"
            f"（避免视频被无限期饿死）"
        )
    return time.time() - started
