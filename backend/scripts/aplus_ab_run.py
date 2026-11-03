"""A+ 出图对照：用新模板（无字）跑 2 张 aplus_advanced，落库后测量画框/主体占比。

不重启后端：直接调用 services.ai_image_service.submit_image_task（与路由同一入口），
在本进程内起后台线程完成 ComfyUI 提交与轮询，脚本存活到任务终态。

用法：py -3.13 backend/scripts/aplus_ab_run.py [--with-text]
"""
import io
import json
import os
import pkgutil
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

import importlib  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

# 项目约定：独立脚本必须动态导入全部 models，否则 mapper 解析不全
import models as _models_pkg  # noqa: E402

for _m in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"models.{_m.name}")

from config import get_settings  # noqa: E402
from services.ai_image_service import submit_image_task  # noqa: E402

TENANT_ID, USER_ID, CREATOR = 1, 5, "k"
REF_LOCAL = os.path.join(os.path.dirname(BACKEND), "_ap65_ref.jpg")
POLL_SEC, TIMEOUT_MIN = 3, 12


def measure(path: str) -> dict:
    """量白边宽度与商品占宽（金色像素 bbox）"""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(int)
    h, w, _ = a.shape
    bl = (a[:, :, 2] - a[:, :, 0]) > 8           # 蓝色底板判据（与上次同一口径）
    cols = np.where(bl.mean(axis=0) > 0.5)[0]
    rows = np.where(bl.mean(axis=1) > 0.5)[0]
    gold = ((a[:, :, 0] > 140) & (a[:, :, 1] > 110) & (a[:, :, 2] < 130)
            & ((a[:, :, 0] - a[:, :, 2]) > 50))
    ys, xs = np.where(gold)
    out = {"size": [w, h], "blue_panel_cols": [int(cols.min()), int(cols.max())] if len(cols) else None}
    if len(cols):
        out["white_left"] = int(cols.min())
        out["white_right"] = int(w - 1 - cols.max())
    else:
        out["white_left"] = out["white_right"] = None
    out["blue_rows"] = [int(rows.min()), int(rows.max())] if len(rows) else None
    if len(xs):
        out["product_width_pct"] = round(100 * (xs.max() - xs.min()) / w, 1)
        out["product_height_pct"] = round(100 * (ys.max() - ys.min()) / h, 1)
        out["product_center_x_pct"] = round(100 * (xs.min() + xs.max()) / 2 / w, 1)
    return out


def main() -> None:
    with_text = "--with-text" in sys.argv
    ref_path = os.environ.get("APLUS_REF_IMAGE", "").strip() or REF_LOCAL
    images = [(os.path.basename(ref_path), open(ref_path, "rb").read())]
    print(f"参考图：{ref_path}")

    eng = create_engine(get_settings().DATABASE_URL)
    pi_file = os.environ.get("APLUS_PI_FILE", "").strip()
    if pi_file and os.path.exists(pi_file):
        pi = open(pi_file, encoding="utf-8").read().strip()
        print(f"商品信息取自 {pi_file}（{len(pi)} 字）")
    else:
        with eng.connect() as c:
            pi = c.execute(text("SELECT product_info FROM ai_image_tasks WHERE id = 65")).scalar()

    brand = {"country": "US 美国", "language": "英语", "platform": "亚马逊"}
    if with_text:
        brand["text_mode"] = "with_text"

    task = submit_image_task(
        tenant_id=TENANT_ID, user_id=USER_ID, creator_name=CREATOR,
        title=f"AB-新模板-{'有字' if with_text else '无字'}",
        generation_types=["aplus_advanced"], aspect_ratio="1:1", image_count=2,
        product_info=pi, prompt="", images=images,
        product_code="BXHS-CP-00002", product_name="E-水母1岁横幅",
        aplus_type="aplus_advanced", main_types=[], main_aspect_ratio="1:1",
        brand_settings=brand, provider="local",
    )
    tid = task["id"]
    print(f"已提交任务 #{tid}（{'有字' if with_text else '无字'}模式）")

    deadline = time.time() + TIMEOUT_MIN * 60
    last = ""
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        with eng.connect() as c:
            row = c.execute(
                text("SELECT status,error_message,result_images FROM ai_image_tasks WHERE id = :i"),
                {"i": tid}).mappings().first()
        if row["status"] != last:
            print(f"  {int(time.time() % 10000)}s 状态：{row['status']}  {row['error_message'] or ''}")
            last = row["status"]
        if row["status"] in ("已完成", "部分完成", "失败", "已取消"):
            break

    items = json.loads(row["result_images"] or "[]")
    print(f"\n=== 任务 #{tid} 终态 {row['status']} ===")
    for it in items:
        print(f"[seq{it.get('seq')}] {it.get('status')} {it.get('gen_seconds')}s")
        print(f"  原始提示词长度 {len(it.get('final_prompt') or '')} / 含语言字段 "
              f"{'画面文字语言' in (it.get('final_prompt') or '')}")
        print(f"  PE 输出长度 {len(it.get('enhanced_prompt') or '')} / 前 90 字："
              f"{(it.get('enhanced_prompt') or '')[:90]}")
        url = it.get("url")
        if not url:
            print(f"  无图：{it.get('error')}")
            continue
        import requests
        path = os.path.join(os.path.dirname(BACKEND), f"_ap{tid}_{it.get('seq')}.jpg")
        open(path, "wb").write(requests.get(url, timeout=60).content)
        print(f"  {path}  {measure(path)}")


if __name__ == "__main__":
    main()
