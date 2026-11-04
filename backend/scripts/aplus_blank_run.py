"""A+ 横版单张实测：验证「与背景连续的整片留白」措辞 + 换产品复测。

与 aplus_ab_run.py 的区别：
- 强制只出 1 张（用户指令：后续测试每次只生成一张）
- 强制横版结构版式（用户指令：A+ 优先走横版布局）
- 参考图从产品库取（换产品测试），不是本地固定文件
- 额外度量「顶部/侧边留白是否被画成可见矩形块」：对留白区做边缘强度检测

用法：
  py -3.13 backend/scripts/aplus_blank_run.py --product 33572 --layout three_column
  py -3.13 backend/scripts/aplus_blank_run.py --product 33574 --layout db:19

--layout 支持两种写法：
  preset:<key>  内置结构版式（aplus_layouts.json）
  db:<id>       官方图片模板（ai_image_layouts 表，240 张导入的那批）
两者都是**纯文字注入**：带 structure 的版式不送图给 PE，走正常按钮生图通道。
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
import requests  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

# 项目约定：独立脚本必须动态导入全部 models，否则 mapper 解析不全
import models as _models_pkg  # noqa: E402

for _m in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"models.{_m.name}")

from config import get_settings  # noqa: E402
from services.ai_image_service import submit_image_task  # noqa: E402

TENANT_ID, USER_ID, CREATOR = 1, 5, "k"
POLL_SEC, TIMEOUT_MIN = 4, 14
ROOT = os.path.dirname(BACKEND)


def _pick_product(eng, pid: int):
    with eng.connect() as c:
        r = c.execute(
            text("SELECT id, product_code, name, main_image, images FROM products WHERE id = :i"),
            {"i": pid},
        ).mappings().first()
    if not r:
        raise SystemExit(f"产品 {pid} 不存在")
    imgs = json.loads(r["images"] or "[]")
    urls = [u for u in imgs if u]
    if r["main_image"] and r["main_image"] not in urls:
        urls.insert(0, r["main_image"])
    return r, urls


def _fetch(url: str) -> bytes:
    # 走 CDN 直连：项目里 _fetch_url_bytes 也是 trust_env=False（代理会让整包 400）
    s = requests.Session()
    s.trust_env = False
    resp = s.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def _order_by_clarity(items):
    """主参考图必须清晰：产品库首图常是缩略图，会让分析彻底失真"""
    scored = []
    for name, data in items:
        try:
            im = Image.open(io.BytesIO(data))
            scored.append((im.width * im.height, name, data))
        except Exception:
            scored.append((0, name, data))
    scored.sort(key=lambda x: -x[0])
    return [(n, d) for _, n, d in scored]


def measure_blank(path: str) -> dict:
    """量「留白区是不是一块可见矩形」：看留白区内部有没有色块边界。

    判据：把画面按 3x3 分块，逐块算色度方差 + 与相邻块的平均色差。
    真·背景延伸 → 相邻块色差极小；画了一块浅色矩形 → 交界处出现明显色阶跳变。
    """
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(float)
    h, w, _ = a.shape
    out = {"size": [w, h]}
    # 顶部 20% 区域（横版版式里最常被画成矩形留白的地方）
    top = a[: int(h * 0.20)]
    out["top_mean_rgb"] = [round(float(x), 1) for x in top.reshape(-1, 3).mean(axis=0)]
    out["top_std"] = round(float(top.reshape(-1, 3).std()), 1)
    # 顶部区与下方区的色差（矩形块会明显不同于下方场景）
    if h > int(h * 0.35):
        mid = a[int(h * 0.25): int(h * 0.60)]
        diff = float(np.abs(top.reshape(-1, 3).mean(axis=0) - mid.reshape(-1, 3).mean(axis=0)).mean())
        out["top_vs_mid_diff"] = round(diff, 1)
    # 水平边界检测：逐行算「与上一行的平均绝对差」，出现尖峰 = 一条横向硬边
    rowdiff = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    if len(rowdiff):
        k = int(np.argmax(rowdiff))
        out["strongest_h_edge_row_pct"] = round(100 * k / h, 1)
        out["strongest_h_edge_strength"] = round(float(rowdiff[k]), 2)
    # 有没有可读文字（留白区里出现高频笔画 → 乱码/文字）
    gray = a.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1))
    out["high_freq_px_pct"] = round(100 * float((gx > 60).mean()), 2)
    return out


def main() -> None:
    pid = 33572
    layout_key = "preset:three_column"
    for i, arg in enumerate(sys.argv):
        if arg == "--product" and i + 1 < len(sys.argv):
            pid = int(sys.argv[i + 1])
        if arg == "--layout" and i + 1 < len(sys.argv):
            layout_key = sys.argv[i + 1]
    # 兼容旧写法：--layout three_column → preset:three_column
    if not layout_key.startswith(("preset:", "db:")):
        layout_key = f"preset:{layout_key}"

    eng = create_engine(get_settings().DATABASE_URL)
    prod, urls = _pick_product(eng, pid)
    print(f"产品：{prod['id']} {prod['product_code']} {prod['name']}（图 {len(urls)} 张）")

    picked = _order_by_clarity([(os.path.basename(u), _fetch(u)) for u in urls[:3]])
    print("参考图（按清晰度排序）：" + "、".join(n for n, _ in picked))

    product_info = (
        f"品名：{prod['name']}\n"
        f"英文品名：{prod['name']}\n"
        f"编码：{prod['product_code']}\n"
        "说明：以参考图可见特征为准，逐件清点，不得改动外观、颜色、材质、图案与件数。"
    )
    brand = {"country": "US 美国", "language": "英语", "platform": "亚马逊"}

    task = submit_image_task(
        tenant_id=TENANT_ID, user_id=USER_ID, creator_name=CREATOR,
        title=f"留白措辞复测-横版-{layout_key}",
        generation_types=["aplus_advanced"], aspect_ratio="21:9", image_count=1,
        product_info=product_info, prompt="", images=picked,
        product_code=prod["product_code"], product_name=prod["name"],
        aplus_type="aplus_advanced", main_types=[], main_aspect_ratio="1:1",
        brand_settings=brand, provider="local",
        layout_keys=[layout_key],
    )
    tid = task["id"]
    print(f"已提交任务 #{tid}（1 张 · 横版 {layout_key} · 无字）")

    deadline = time.time() + TIMEOUT_MIN * 60
    row = None
    last = ""
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        with eng.connect() as c:
            row = c.execute(
                text("SELECT status,error_message,result_images FROM ai_image_tasks WHERE id = :i"),
                {"i": tid},
            ).mappings().first()
        if row["status"] != last:
            print(f"  状态：{row['status']}  {row['error_message'] or ''}")
            last = row["status"]
        if row["status"] in ("已完成", "部分完成", "失败", "已取消"):
            break

    items = json.loads(row["result_images"] or "[]")
    print(f"\n=== 任务 #{tid} 终态 {row['status']} ===")
    for it in items:
        print(f"[seq{it.get('seq')}] {it.get('status')} {it.get('gen_seconds')}s 版式={it.get('layout', {}).get('key')}")
        lay = it.get("layout") or {}
        print(f"  版式描述已注入: {'留白' in (it.get('final_prompt') or '')} / 含矩形禁令: "
              f"{'不留任何矩形色块' in (it.get('final_prompt') or '')}")
        fp = it.get("final_prompt") or ""
        if "版式（画面分区" in fp or "按下面给定的版式" in fp:
            print(f"  提示词版式段：{fp[fp.find('按下面给定的版式'):][:260]}")
        url = it.get("url")
        if not url:
            print(f"  无图：{it.get('error')}")
            continue
        path = os.path.join(ROOT, f"_ap{tid}_{it.get('seq')}.jpg")
        open(path, "wb").write(_fetch(url))
        print(f"  {path}")
        print(f"  度量：{measure_blank(path)}")


if __name__ == "__main__":
    main()
