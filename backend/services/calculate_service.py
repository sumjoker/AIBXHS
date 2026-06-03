"""
补货计算服务 - 后台异步执行
"""
import threading
import time
import uuid
from typing import Optional

# 任务状态存储（内存）
_tasks = {}


def _log(task_id: str, msg: str):
    elapsed = time.time() - _tasks[task_id]["started_at"] if _tasks[task_id]["started_at"] else 0
    print(f"[计算 {task_id[:8]} {elapsed:>6.1f}s] {msg}", flush=True)
    _tasks[task_id]["step"] = msg


def _update_status(task_id: str, **kwargs):
    if task_id in _tasks:
        _tasks[task_id].update(kwargs)


def _run_calculation(task_id: str, snapshot_date: str, snapshot_ids: list):
    """后台执行补货计算"""
    try:
        _update_status(task_id, status="running", started_at=time.time())

        # 定义进度回调
        def progress_callback(phase: str, current: int, total: int):
            pct = min(int(current / total * 100) if total > 0 else 0, 99)
            _update_status(task_id, step=f"{phase}({pct}%)", progress=pct)

        _log(task_id, "开始补货计算...")

        from database.database import SessionLocal
        from services.inventory_service import calculate_replenishment

        db = SessionLocal()
        try:
            _log(task_id, f"增量计算 {len(snapshot_ids) if snapshot_ids else '全部'} 条数据...")
            result = calculate_replenishment(db, snapshot_date=snapshot_date, snapshot_ids=snapshot_ids, progress_callback=progress_callback)
            _update_status(task_id, status="completed", finished_at=time.time(), result=result, progress=100)
            _log(task_id, f"补货计算完成: 共{result.get('total',0)}条")
        finally:
            db.close()

    except Exception as e:
        _log(task_id, f"计算失败: {e}")
        _update_status(task_id, status="failed", error=str(e), finished_at=time.time())


def start_calculation_async(snapshot_date: str = None, snapshot_ids: list = None) -> dict:
    """启动异步补货计算"""
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0,
        "step": "",
        "result": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "snapshot_date": snapshot_date,
        "snapshot_ids": snapshot_ids,
    }

    thread = threading.Thread(
        target=_run_calculation,
        args=(task_id, snapshot_date, snapshot_ids),
        daemon=True
    )
    thread.start()

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "补货计算任务已启动",
    }


def get_calculation_status(task_id: str) -> Optional[dict]:
    """获取计算任务状态"""
    task = _tasks.get(task_id)
    if not task:
        return None
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "step": task["step"],
        "result": task["result"],
        "error": task["error"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
    }


def cleanup_old_tasks(max_age: float = 3600):
    """清理超过 max_age 秒的已完成/失败任务"""
    now = time.time()
    to_delete = []
    for tid, task in _tasks.items():
        if task["status"] in ("completed", "failed") and task["finished_at"]:
            if now - task["finished_at"] > max_age:
                to_delete.append(tid)
    for tid in to_delete:
        _tasks.pop(tid, None)