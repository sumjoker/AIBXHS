"""ComfyUI 跨业务优先级（生图 > 超分/视频）+ 队列排位提示的回归测试。

纯函数/线程状态层，不触网、不写库。

背景：生图、超分、H3 视频共用 ComfyUI 串行队列。ComfyUI 是 FIFO 且无优先级接口，
只能在**入队前**让路；本文件锁定「登记/注销 + 上限让路」这两条不变式。
运行：cd backend && python -m pytest tests/test_comfy_priority.py -q
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import comfy_priority as P


class _S:
    def __init__(self, enabled="true", wait=1):
        self.COMFY_IMAGE_PRIORITY = enabled
        self.COMFY_IMAGE_PRIORITY_WAIT_SECONDS = wait


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例从干净状态开始，避免用例间互相污染"""
    monkeypatch.setattr(P, "get_settings", lambda: _S("true", 0))
    P._active_image_tasks.clear()
    yield
    P._active_image_tasks.clear()


def test_begin_end_are_idempotent():
    P.begin_image_task(1)
    P.begin_image_task(1)
    assert P.active_image_tasks() == 1
    P.end_image_task(1)
    P.end_image_task(1)  # 未登记也不报错
    assert P.active_image_tasks() == 0


def test_wait_returns_immediately_when_no_image_active():
    t0 = time.time()
    assert P.wait_before_enqueue("超分", 9, timeout=5) == 0.0
    assert time.time() - t0 < 0.5


def test_wait_gives_way_while_image_active(monkeypatch):
    """生图在跑时必须让路；等生图注销后立刻继续"""
    monkeypatch.setattr(P, "get_settings", lambda: _S("true", 10))
    P.begin_image_task(1)
    threading.Timer(0.3, P.end_image_task, args=(1,)).start()
    waited = P.wait_before_enqueue("H3视频", 2, timeout=5)
    assert waited >= 0.2, "应当让路而不是立刻入队"
    assert P.active_image_tasks() == 0


def test_wait_is_capped_so_video_never_starves(monkeypatch):
    """生图长期占位时也必须按时放弃让路，不能让视频无限期等待"""
    monkeypatch.setattr(P, "get_settings", lambda: _S("true", 10))
    P.begin_image_task(1)
    t0 = time.time()
    waited = P.wait_before_enqueue("超分", 3, timeout=0.3)
    assert 0.2 <= waited < 2.0
    assert time.time() - t0 < 2.0


def test_disabled_switch_falls_back_to_fifo(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _S("false", 10))
    P.begin_image_task(1)
    assert P.wait_before_enqueue("超分", 4, timeout=5) == 0.0


def test_zero_timeout_never_blocks():
    P.begin_image_task(1)
    assert P.wait_before_enqueue("超分", 5, timeout=0) == 0.0


# ---------- 队列排位提示 ----------

class _Task:
    def __init__(self, status, provider="local", task_id=7, user_id=3, pids=None):
        self.status = status
        self.provider = provider
        self.id = task_id
        self.user_id = user_id
        self.comfy_prompt_ids = pids


def test_queue_info_none_for_finished_task():
    from services import ai_image_service as S

    assert S._queue_info(_Task("已完成")) is None
    assert S._queue_info(None) is None


def test_queue_info_relay_has_no_comfy_position():
    from services import ai_image_service as S

    info = S._queue_info(_Task("生成中", provider="relay"))
    assert info and info["stage"] == "relay"


def test_queue_info_reports_comfy_position(monkeypatch):
    """进行中任务要能说出「前面还有几个任务」，这是「排队中」无提示的直接修复"""
    from services import ai_image_service as S

    monkeypatch.setattr(
        S, "_comfy_queue_state",
        lambda: {"ok": True, "running": 1, "pending": 3, "positions": {"p2": 2, "p3": 3}},
    )
    info = S._queue_info(_Task("排队中", pids='["p3"]'))
    assert info["position"] == 3
    assert info["ahead"] == 2
    assert "2" in info["hint"] and "ComfyUI" in info["hint"]


def test_queue_info_reports_user_queue_ahead(monkeypatch):
    from services import ai_image_service as S

    monkeypatch.setattr(
        S, "_comfy_queue_state", lambda: {"ok": True, "running": 0, "pending": 0, "positions": {}},
    )
    with S._lock:
        S._pending_by_user[3] = [(7, {}), (8, {})]
        S._user_current[3] = 5
    try:
        info = S._queue_info(_Task("排队中", pids=[]))
        # 正在跑的那一单 + 队列里排在本单前面的单数
        assert info["user_ahead"] == 1
        assert "同账号" in info["hint"]
    finally:
        with S._lock:
            S._pending_by_user.pop(3, None)
            S._user_current.pop(3, None)
