"""批量给官方模板生成「版式结构描述」草稿（AI 读图 → 写库）

用法：
    py -3.13 backend/scripts/fill_layout_structure.py [--limit N] [--ids 3,80,150] [--overwrite]

默认只补 structure 为空的模板；--overwrite 覆盖已有描述。
每张约 8~16s（视觉模型一次调用），建议先 --limit 5 看质量再放开。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

from services.ai_image_service import _ensure_models_loaded  # noqa: E402

# 独立解释器必须先注册全部 models，否则 Tenant.relationship 解析失败
_ensure_models_loaded()

from database.database import SessionLocal  # noqa: E402
from models.ai_image_layout import AIImageLayout  # noqa: E402
from services.ai_image_layout_service import suggest_structure, update_structure  # noqa: E402


def _work(item) -> tuple:
    """单张：AI 出草稿 → 写库。失败重试一次（视觉模型偶发超时/限流）。"""
    lid, name, source, tenant_id, user_id = item
    last = ""
    for attempt in (1, 2):
        try:
            r = suggest_structure(user_id or 0, tenant_id or 0, lid)
            update_structure(user_id or 0, tenant_id or 0, lid, r["structure"])
            return lid, name, r["structure"], None
        except Exception as e:
            last = str(e)
            if attempt == 1:
                time.sleep(1.5)
    return lid, name, None, last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 张")
    ap.add_argument("--ids", default="", help="指定模板 id，逗号分隔")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有描述")
    ap.add_argument("--workers", type=int, default=1, help="并发数（默认 1 串行；建议 4~8）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(AIImageLayout).filter(
            AIImageLayout.deleted_at.is_(None),
            AIImageLayout.source.in_(("official", "upload")),
        )
        if args.ids:
            ids = [int(x) for x in args.ids.split(",") if x.strip().isdigit()]
            q = q.filter(AIImageLayout.id.in_(ids))
        if not args.overwrite:
            q = q.filter((AIImageLayout.structure.is_(None)) | (AIImageLayout.structure == ""))
        rows = q.order_by(AIImageLayout.id.asc()).all()
        if args.limit:
            rows = rows[: args.limit]
        targets = [(r.id, r.name, r.source, r.tenant_id, r.user_id) for r in rows]
    finally:
        db.close()

    if not targets:
        print("没有需要处理的模板（加 --overwrite 可重写已有描述）")
        return

    print(f"待生成 {len(targets)} 张，并发 {args.workers}", flush=True)
    t0 = time.time()
    ok = 0
    failed = []
    workers = max(1, min(args.workers, 16))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_work, it): it for it in targets}
        for fut in as_completed(futs):
            lid, name, structure, err = fut.result()
            done = ok + len(failed) + 1
            if err:
                failed.append(f"#{lid} {name}: {err}")
                print(f"  [{done}/{len(targets)}] #{lid} {name} FAIL: {err}", flush=True)
            else:
                ok += 1
                eta = (time.time() - t0) / done * (len(targets) - done) / 60
                print(f"  [{done}/{len(targets)}] #{lid} {name}: {structure[:46]}… "
                      f"(剩约 {eta:.0f} 分钟)", flush=True)

    print(f"完成：成功 {ok}，失败 {len(failed)}，耗时 {time.time() - t0:.0f}s", flush=True)
    for f in failed[:10]:
        print("  FAIL", f, flush=True)


if __name__ == "__main__":
    main()
