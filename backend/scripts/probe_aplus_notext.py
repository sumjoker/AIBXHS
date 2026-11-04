"""UI 探针：确认 A+ 卡片里的「无文字模式」开关已上线且默认开启。

用法：py -3.13 backend/scripts/probe_aplus_notext.py
产出：_probe_aplus_notext.png（A+ 设置卡片截图）
"""
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from config import get_settings  # noqa: E402
from services.auth_service import create_access_token  # noqa: E402

OUT = os.path.join(os.path.dirname(BACKEND), "_probe_aplus_notext.png")


def main() -> None:
    eng = create_engine(get_settings().DATABASE_URL)
    with eng.connect() as c:
        u = c.execute(text("SELECT id, username, tenant_id FROM users WHERE id = 5")).mappings().first()
    token = create_access_token(data={"sub": u["username"], "uid": u["id"], "tid": u["tenant_id"]})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"window.localStorage.setItem('token', {token!r});")
        page = ctx.new_page()
        page.goto("http://127.0.0.1:8002/ai-image", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        # 选中「高级A+」后才会出现生成张数与无文字模式
        page.get_by_text("高级A+", exact=True).first.click()
        page.wait_for_timeout(800)

        has_label = page.get_by_text("无文字模式", exact=True).count()
        # 注意：必须在点击之后再定位卡片，否则拿到的是点击前的旧句柄（截图里会缺生成张数/开关）
        card = page.locator(".ant-card", has_text="A+图设置").last
        switches = card.locator(".ant-switch").count()
        checked = card.locator(".ant-switch-checked").count()

        card.screenshot(path=OUT)
        print(f"无文字模式标签数：{has_label} | 卡片内开关数：{switches} | 其中选中：{checked}")
        print(f"截图：{OUT}")
        browser.close()


if __name__ == "__main__":
    main()
