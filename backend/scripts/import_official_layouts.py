"""把本地版式模板目录导入「官方模板」库（source=official，全租户可见）

用法：
    py -3.13 backend/scripts/import_official_layouts.py \
        --dirs "C:/.../方形" "C:/.../横版" "C:/.../竖版" [--limit N]

每张模板产出两份派生图并上传 TOS：
- ref  长边 ≤1536 / q88：生成时作为版式参考图喂给 ComfyUI / 设计模型
- thumb 长边 ≤480 / q75：列表缩略图，240 张原图（367MB）直接进列表会把首屏拖死

幂等：以 preset_id='official:<目录>/<文件名>' 判重，重复执行只会跳过不会产生副本。
"""
import argparse
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

from database.database import SessionLocal  # noqa: E402
from models.ai_image_layout import AIImageLayout  # noqa: E402
from services import tos_service  # noqa: E402
from services.ai_image_service import _ensure_models_loaded  # noqa: E402

# 独立解释器必须先把全部 models 注册进 mapper，否则 Tenant 的 relationship 解析失败
_ensure_models_loaded()

RATIO_BY_DIR = {"横版": "horizontal", "竖版": "vertical", "方形": "square"}
REF_EDGE = 1536
THUMB_EDGE = 480


def _derive(src: str) -> Tuple[bytes, bytes, int, int]:
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size

        def _fit(edge: int, quality: int) -> bytes:
            scale = edge / float(max(w, h))
            out = im if scale >= 1 else im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
            )
            buf = io.BytesIO()
            out.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()

        return _fit(REF_EDGE, 88), _fit(THUMB_EDGE, 75), w, h


def _upload_one(task: Tuple[str, str, str]) -> Dict[str, object]:
    """(目录名, 完整路径, 文件名) → 上传两份派生图"""
    folder, path, fname = task
    ref_bytes, thumb_bytes, w, h = _derive(path)
    stem = os.path.splitext(fname)[0]
    tag = f"{folder}.{stem}"
    ref_url = tos_service.upload_file(
        ref_bytes, f"ref-{tag}.jpg", content_type="image/jpeg", subdir="ai-image-layouts"
    )
    thumb_url = tos_service.upload_file(
        thumb_bytes, f"thumb-{tag}.jpg", content_type="image/jpeg", subdir="ai-image-layouts"
    )
    return {
        "preset_id": f"official:{folder}/{fname}",
        "name": f"{folder}-{stem}",
        "ratio_tag": RATIO_BY_DIR.get(folder, "horizontal"),
        "image_url": ref_url,
        "thumb_url": thumb_url,
        "width": w,
        "height": h,
    }


def _sort_key(path: str) -> Tuple[int, str]:
    stem = os.path.splitext(os.path.basename(path))[0]
    return (int(stem), "") if stem.isdigit() else (10 ** 9, stem)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=0, help="每目录只导前 N 张（调试用）")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    tasks: List[Tuple[str, str, str]] = []
    for d in args.dirs:
        folder = os.path.basename(os.path.normpath(d))
        if folder not in RATIO_BY_DIR:
            print(f"!! 跳过未知目录（需为 横版/竖版/方形）: {folder}")
            continue
        files = [f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        files.sort(key=_sort_key)
        if args.limit:
            files = files[: args.limit]
        tasks += [(folder, os.path.join(d, f), f) for f in files]

    if not tasks:
        print("没有可导入的文件")
        return

    db = SessionLocal()
    try:
        existing = {
            r[0] for r in db.query(AIImageLayout.preset_id)
            .filter(AIImageLayout.source == "official").all()
        }
    finally:
        db.close()
    todo = [t for t in tasks if f"official:{t[0]}/{t[2]}" not in existing]
    print(f"待导入 {len(todo)} 张（已存在 {len(tasks) - len(todo)} 张跳过）")

    t0 = time.time()
    done = 0
    failed: List[str] = []
    db = SessionLocal()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_upload_one, t): t for t in todo}
            for fut, t in futures.items():
                try:
                    row = fut.result()
                except Exception as e:
                    failed.append(f"{t[0]}/{t[2]}: {e}")
                    continue
                db.add(AIImageLayout(
                    tenant_id=0, user_id=0, source="official",
                    preset_id=row["preset_id"], name=row["name"],
                    ratio_tag=row["ratio_tag"], image_url=row["image_url"],
                    thumb_url=row["thumb_url"], width=row["width"], height=row["height"],
                ))
                done += 1
                if done % 20 == 0:
                    db.commit()
                    print(f"  {done}/{len(todo)}  {time.time() - t0:.0f}s")
        db.commit()
    finally:
        db.close()

    print(f"完成：导入 {done} 张，失败 {len(failed)}，耗时 {time.time() - t0:.0f}s")
    for f in failed[:10]:
        print("  FAIL", f)


if __name__ == "__main__":
    main()
