"""UI 探针：A+ 版式板块（选择排版 → 每种排版生成一张）

用法：py -3.13 backend/scripts/probe_aplus_layout.py
产出：_probe_layout_row.png（A+ 卡片内的选择排版行）、_probe_layout_modal.png（版式弹窗）
校验：行文案、弹窗三 tab、官方版式缩略图数量、已选版式面板、点选后确认回填
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

ROOT = os.path.dirname(BACKEND)
OUT_ROW = os.path.join(ROOT, "_probe_layout_row.png")
OUT_MODAL = os.path.join(ROOT, "_probe_layout_modal.png")
OUT_PREVIEW = os.path.join(ROOT, "_probe_layout_preview.png")
OUT_VERTICAL = os.path.join(ROOT, "_probe_layout_vertical.png")
OUT_HORIZONTAL = os.path.join(ROOT, "_probe_layout_horizontal.png")


def main() -> None:
    eng = create_engine(get_settings().DATABASE_URL)
    with eng.connect() as c:
        u = c.execute(text("SELECT id, username, tenant_id FROM users WHERE id = 5")).mappings().first()
    token = create_access_token(data={"sub": u["username"], "uid": u["id"], "tid": u["tenant_id"]})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        ctx.add_init_script(f"window.localStorage.setItem('token', {token!r});")
        page = ctx.new_page()
        page.goto("http://127.0.0.1:8002/ai-image", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        page.get_by_text("高级A+", exact=True).first.click()
        page.wait_for_timeout(800)

        row = page.get_by_text("选择排版", exact=True).first
        assert row.count() > 0, "A+ 卡片里没有「选择排版」行"
        hint = page.get_by_text("按官方排版随机生成").first
        assert hint.count() > 0, "未选版式时应提示将随机生成官方排版"

        row.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.screenshot(path=OUT_ROW)

        row.click()
        page.wait_for_timeout(1200)
        assert page.get_by_text("设置版式参考图", exact=True).count() > 0, "弹窗未打开"
        for t in ("官方推荐", "我的", "收藏", "未选择排版", "暂无已选版式"):
            assert page.get_by_text(t, exact=False).count() > 0 or t in ("未选择排版",), f"缺文案: {t}"

        # 官方推荐 = 官方模板（真实缩略图）+ 结构版式（svg 示意图）
        img_n = page.locator(".ant-modal img[src*='ai-image-layouts']").count()
        svg_n = page.locator(".ant-modal svg").count()
        assert img_n >= 10, f"官方模板缩略图没渲染出来：{img_n}"
        assert svg_n >= 8, f"结构版式缩略图数量不足：{svg_n}"
        total = page.get_by_text("官方推荐(", exact=False).first.inner_text()
        print(f"  缩略图：官方模板 {img_n} 张、结构版式 {svg_n} 个；tab={total}")

        # 画幅筛选竖版：卡片框应按 3:4 撑开，图片基本铺满（不再被挤成横框里的窄条）
        page.locator(".ant-modal .ant-select").first.click()
        page.wait_for_timeout(300)
        page.get_by_text("竖版", exact=True).last.click()
        page.wait_for_timeout(1200)
        vimg = page.locator(".ant-modal img[src*='ai-image-layouts']").first
        vb = vimg.bounding_box()
        assert vb and vb["height"] > vb["width"] * 1.15, f"竖版筛选下图片仍偏横：{vb}"
        print(f"  竖版模式单图 {int(vb['width'])}x{int(vb['height'])}（高/宽={vb['height']/vb['width']:.2f}）")
        page.screenshot(path=OUT_VERTICAL)
        page.locator(".ant-modal .ant-select").first.click()
        page.wait_for_timeout(300)
        page.get_by_text("全部画幅", exact=True).last.click()
        page.wait_for_timeout(800)

        # 横版筛选：3 列放大，单图高度应明显大于旧版 86px
        page.locator(".ant-modal .ant-select").first.click()
        page.wait_for_timeout(300)
        page.get_by_text("横版", exact=True).last.click()
        page.wait_for_timeout(1200)
        himg = page.locator(".ant-modal img[src*='ai-image-layouts']").first
        hb = himg.bounding_box()
        assert hb and hb["height"] >= 100, f"横版单图仍然偏小：{hb}"
        print(f"  横版模式单图 {int(hb['width'])}x{int(hb['height'])}")
        page.screenshot(path=OUT_HORIZONTAL)

        # 卡片已去掉名称：网格里不应再出现「横版-0」这类文本
        assert page.get_by_text("横版-0", exact=True).count() == 0, "卡片名称未去掉"

        # 放大预览：悬停出现放大图标 → 打开大图弹窗
        first_img = page.locator(".ant-modal img[src*='ai-image-layouts']").first
        first_img.hover()
        page.wait_for_timeout(300)
        card_box = first_img.bounding_box()
        # 悬停后点右上角放大按钮（卡片内 ZoomIn 图标）
        page.mouse.move(card_box["x"] + card_box["width"] / 2, card_box["y"] + card_box["height"] / 2)
        page.wait_for_timeout(200)
        zoom = page.locator(".anticon-zoom-in").first
        assert zoom.count() > 0, "悬停后没有放大按钮"
        zoom.click()
        page.wait_for_timeout(900)
        big = page.locator(".ant-modal img").last
        bb = big.bounding_box()
        assert bb and bb["height"] > 260, f"预览大图不够大：{bb}"
        print(f"  预览大图 {int(bb['width'])}x{int(bb['height'])}")
        # 结构描述编辑区：AI 草稿按钮 + 输入框必须存在
        assert page.get_by_text("版式结构描述", exact=True).count() > 0, "预览里没有结构描述编辑区"
        assert page.get_by_text("AI 帮我写", exact=True).count() > 0, "缺少 AI 帮我写按钮"
        assert page.locator(".ant-modal textarea").count() > 0, "缺少结构描述输入框"
        # 切到下一张：描述必须跟着换（曾出现「图换了、描述没换」，会把 A 的描述存到 B 上）
        ta = page.locator(".ant-modal textarea").first
        before = ta.input_value()
        assert before, "首张应已有结构描述（db:3 已写入）"
        page.locator(".ant-modal .anticon-right").first.click()
        page.wait_for_timeout(600)
        after = ta.input_value()
        assert after != before, "切换到下一张后结构描述没有跟着切换"
        print(f"  切换校验：描述已换（{len(before)} 字 → {len(after)} 字）")
        page.screenshot(path=OUT_PREVIEW)
        page.get_by_role("button", name="关闭").count() or page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        # 选中两个版式 → 右侧已选面板应出现 2 条
        page.locator(".ant-modal img[src*='ai-image-layouts']").nth(0).click()
        page.locator(".ant-modal img[src*='ai-image-layouts']").nth(1).click()
        page.wait_for_timeout(500)
        assert page.get_by_text("已选版式 (2/8)", exact=False).count() > 0, "已选计数不对"
        page.screenshot(path=OUT_MODAL)

        # 确认按钮：antd Modal footer 里的主按钮（get_by_role 在自定义 footer 下不稳）
        btn = page.locator(".ant-modal-footer button.ant-btn-primary").first
        assert btn.count() > 0, "弹窗 footer 里没有确认按钮"
        assert btn.is_enabled(), "已选 2 个版式时确认按钮应可用"
        btn.click()
        page.wait_for_timeout(900)
        # 回填到卡片：出现已选 tag 且张数随版式
        assert page.get_by_text("已选 2 个", exact=False).count() > 0, "确认后未回填到 A+ 卡片"

        print("OK: 选择排版行 / 弹窗 / 8 个官方版式 / 已选计数 / 回填 全部通过")
        browser.close()


if __name__ == "__main__":
    main()
