"""UI 探针：验证「媒体创作中心」菜单按 ai_image:use / ai_video:use 正确拆分。

断言矩阵（只读，不触发生图）：
  美工(meigong, ai_image + ai_video)  → 看到 AI生图 / AI视频 / 高清处理
  demo_yunying(仅 ai_image)          → 只看到 AI生图，看不到 AI视频 / 高清处理
  采购(purchaser, 都没有)             → 整个「媒体创作中心」不出现

在 backend 目录执行：python scripts/probe_media_menu_permission.py
"""
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

import pkgutil  # noqa: E402
import importlib  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import models as _models_pkg  # noqa: E402

for _m in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"models.{_m.name}")

from config import get_settings  # noqa: E402
from services.auth_service import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8002"
OUT = os.path.join(os.path.dirname(BACKEND), "_probe_media_menu.png")

# (说明, 用户id, {菜单文案: 期望是否可见})
CASES = [
    ("美工 黄家淇(ai_image+ai_video)", 47, {"AI生图": True, "AI视频": True, "高清处理": True}),
    ("运营 demo_yunying(仅 ai_image)", 38, {"AI生图": True, "AI视频": False, "高清处理": False}),
    ("采购 邓诗婷(都没有)", 39, {"媒体创作中心": False}),
]


def main() -> None:
    eng = create_engine(get_settings().DATABASE_URL)
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, uid, expects in CASES:
            with eng.connect() as c:
                u = c.execute(
                    text("SELECT id, username, tenant_id FROM users WHERE id = :i"),
                    {"i": uid},
                ).mappings().first()
            if not u:
                print(f"[SKIP] {label}: 用户 {uid} 不存在")
                continue
            token = create_access_token(
                data={"sub": u["username"], "uid": u["id"], "tid": u["tenant_id"]}
            )
            ctx = browser.new_context(viewport={"width": 1440, "height": 950})
            ctx.add_init_script(f"window.localStorage.setItem('token', {token!r});")
            page = ctx.new_page()
            page.goto(f"{BASE}/", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(2500)

            # 首页可能有晨报弹层（.benv-overlay）挡住菜单，点空白关掉它
            overlay = page.locator(".benv-overlay")
            if overlay.count() > 0:
                try:
                    overlay.first.click(position={"x": 8, "y": 8}, timeout=5000)
                    page.wait_for_timeout(900)
                except Exception:
                    page.evaluate("document.querySelectorAll('.benv-overlay').forEach(e=>e.remove())")
                    page.wait_for_timeout(400)

            # 展开「媒体创作中心」（antd 子菜单：点父项展开）
            parent = page.get_by_text("媒体创作中心", exact=True)
            parent_visible = parent.count() > 0 and parent.first.is_visible()
            results = {"媒体创作中心": parent_visible}
            if parent_visible:
                parent.first.click()
                page.wait_for_timeout(900)
                for name in ("AI生图", "AI视频", "高清处理"):
                    loc = page.get_by_text(name, exact=True)
                    results[name] = loc.count() > 0 and loc.first.is_visible()

            ok = True
            lines = []
            for name, want in expects.items():
                got = results.get(name, False)
                if got != want:
                    ok = False
                lines.append(f"{name}={'可见' if got else '不可见'}（期望{'可见' if want else '不可见'}）")
            if not ok:
                failed += 1
            print(f"{'[PASS]' if ok else '[FAIL]'} {label:32s} " + "；".join(lines))
            if uid == 38:
                page.screenshot(path=OUT, full_page=False)
                print(f"        截图: {OUT}")
            ctx.close()
        browser.close()

    print()
    print("全部通过" if failed == 0 else f"{failed} 项失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
